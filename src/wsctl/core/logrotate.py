"""Size-based log rotation that keeps existing descriptors valid.

``wsctl start`` hands its child an already-open *append-mode* descriptor for
the log file. Classic rename-based rotation would leave that descriptor
pointing at the renamed inode, so the daemon would keep writing into the
rotated file forever. Copy-and-truncate avoids that: older backups are shifted
out of the way, the live file is copied to ``.1``, and then the live file is
truncated **in place**. Because the descriptor is ``O_APPEND``, the next write
starts at offset 0 again.

The cost is a small window where lines written during the copy may appear in
both files; for an operations log that is the right trade against silently
growing without bound.
"""

from __future__ import annotations

import contextlib
import os
import shutil
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


def rotate_if_needed(path: Path, max_bytes: int, backup_count: int = 3) -> bool:
    """Rotate ``path`` once it reaches ``max_bytes``; return True if rotated.

    ``max_bytes <= 0`` disables rotation, and a missing file is a no-op.
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

    try:
        _shift_backups(path, backup_count)
        # Copy first: if anything fails mid-way the live log is still intact.
        shutil.copy2(path, path.with_name(f"{path.name}.1"))
        # Truncate in place so the child's append-mode descriptor stays valid.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND)
        try:
            os.ftruncate(fd, 0)
        finally:
            os.close(fd)
    except OSError:
        return False
    return True
