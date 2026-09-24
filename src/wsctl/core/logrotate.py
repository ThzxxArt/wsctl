"""Size-based log rotation that keeps existing descriptors valid.

``wsctl start`` hands its child an already-open *append-mode* descriptor for
the log file. Classic rename-based rotation would leave that descriptor
pointing at the renamed inode, so the daemon would keep writing into the
rotated file forever. Copy-and-truncate avoids that: older backups are shifted
out of the way, the live file is copied to ``.1``, and then the live file is
truncated **in place**. Because the descriptor is ``O_APPEND``, the next write
starts at offset 0 again.

The cost is a small window around the truncate itself. Copying a large log
takes real time, and lines written *during* the copy used to be destroyed
outright -- the copy had not seen them and the truncate threw them away, so
they were in neither file (the module used to claim the opposite, "may appear
in both files"). The tail written after the copy snapshot is now carried over
into the backup before the truncate; only the sub-millisecond gap between the
final carry-over read and the truncate is still losable, which is the honest
limit of copy-and-truncate without a writer-side lock.

Rotating is serialised across processes with an ``O_EXCL`` lock file, because
two instances sharing a data directory (the SO_REUSEPORT handover) would
otherwise shift each other's backups and both truncate.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from pathlib import Path


def _shift_backups(path: Path, backup_count: int) -> None:
    """Move ``.N`` upwards so ``.1`` is free for the new copy."""
    if backup_count <= 1:
        return
    with contextlib.suppress(OSError):
        path.with_name(f"{path.name}.{backup_count}").unlink()
    for index in range(backup_count - 1, 0, -1):
        src = path.with_name(f"{path.name}.{index}")
        dst = path.with_name(f"{path.name}.{index + 1}")
        if src.exists():
            with contextlib.suppress(OSError):
                src.replace(dst)


def _truncate(path: Path) -> None:
    """Empty ``path`` in place (the append-mode descriptor stays valid)."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND)
    try:
        os.ftruncate(fd, 0)
    finally:
        os.close(fd)


def _carry_over_tail(live: Path, backup: Path) -> None:
    """Append whatever arrived after the copy snapshot into the backup.

    Without this, bytes written between ``copy2`` and the truncate vanished
    from both files -- including, on a bad day, the lines describing the
    incident the log is for.
    """
    for _ in range(2):
        try:
            copied = backup.stat().st_size
            with live.open("rb") as source:
                source.seek(copied)
                tail = source.read()
        except OSError:
            return
        if not tail:
            return
        with backup.open("ab") as sink:
            sink.write(tail)
            sink.flush()
            os.fsync(sink.fileno())


def _bump_generation(path: Path) -> None:
    """Leave a durable "this inode was emptied" marker for readers.

    Copy-and-truncate keeps the *same* inode, so a follower cannot tell "the
    file continued" from "the file was emptied and rewritten" by size alone:
    if the rotation finishes between two of its polls and the new content is at
    least as long as the old offset, the prefix is silently skipped. A sidecar
    generation counter survives the window and makes the truncation observable
    long after it happened.
    """
    gen = path.with_name(f"{path.name}.gen")
    with contextlib.suppress(OSError):
        gen.write_text(str(time.time_ns()), encoding="utf-8")


#: A rotation completes in milliseconds. A holder that is still alive and
#: younger than this is genuinely busy; anything else is a leftover from a
#: crash (``SIGKILL`` gets no ``finally``, so the lock never reached its
#: ``unlink``) and must not disable rotation for the rest of the process's
#: life -- one crash would otherwise mean logs grow without bound forever.
STALE_LOCK_SECONDS = 60.0


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness; ``True`` when it cannot be determined."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # Permission denied means it exists. Windows turns this into things
        # that are not "absent", so assume alive rather than break a live lock.
        return True
    return True


def _lock_is_stale(lock: Path) -> bool:
    """Is the holder gone, or has it held the lock far too long?"""
    try:
        raw = lock.read_text(encoding="utf-8", errors="replace")
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return True
    try:
        pid = int(raw.strip() or "0")
    except ValueError:
        pid = 0
    if age >= STALE_LOCK_SECONDS:
        return True
    return pid > 0 and not _pid_alive(pid)


def _acquire_lock(lock: Path) -> bool:
    """Take the rotation lock, breaking one left behind by a dead holder.

    ``O_EXCL`` is the atomic half. The stale-break is the second half: a lock
    file outlives its holder when that holder never got a ``finally``, and
    treating it as live forever is how a single crash turned into "logs grow
    without bound until someone notices".
    """
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if not _lock_is_stale(lock):
                return False
            with contextlib.suppress(OSError):
                lock.unlink()
            continue
        except OSError:
            return False
        with contextlib.suppress(OSError):
            os.write(fd, str(os.getpid()).encode("ascii"))
        with contextlib.suppress(OSError):
            os.close(fd)
        return True
    return False


def rotate_if_needed(path: Path, max_bytes: int, backup_count: int = 3) -> bool:
    """Rotate ``path`` once it reaches ``max_bytes``; return True if rotated.

    ``max_bytes <= 0`` disables rotation, and a missing file is a no-op.
    ``backup_count <= 0`` means "keep no history": the live file is simply
    emptied and no ``.1`` is written.
    """
    if max_bytes <= 0:
        return False
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < max_bytes:
        return False

    lock = path.with_name(f"{path.name}.rotate.lock")
    if not _acquire_lock(lock):
        # A peer sharing this log is mid-rotation. Both of us truncating would
        # double-shift the backup chain and empty the file twice.
        return False
    try:
        try:
            if backup_count <= 0:
                _truncate(path)
                _bump_generation(path)
                return True
            _shift_backups(path, backup_count)
            backup = path.with_name(f"{path.name}.1")
            # Copy first: if anything fails mid-way the live log is still intact.
            shutil.copy2(path, backup)
            _carry_over_tail(path, backup)
            _truncate(path)
            _bump_generation(path)
        except OSError:
            return False
        return True
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()
