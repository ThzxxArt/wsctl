"""Are we running inside the very session we are about to kill?

Stopping a wsctl instance tears down every session it hosts, and each session
stops its process group. A user who runs ``wsctl stop`` *inside* one of those
sessions is sawing off the branch they are sitting on: the shell dies before
the ``&&`` chain reaches whatever came next (typically ``wsctl start``), so the
service stops and nothing brings it back. That is not a theoretical hazard --
it is what a remote user did to lock themselves out.

The check is "is this process a descendant of the instance pid". Walking
``/proc`` on Linux, ``ps`` elsewhere. Windows returns ``None`` (unknown) rather
than ``False``: there is no managed background lifecycle there to protect, so
guessing "safe" would be a lie and guessing "unsafe" would be noise.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator

#: Guard against a corrupted ppid chain looping forever.
MAX_DEPTH = 64


def _parent_of(pid: int) -> int | None:
    """Return ``pid``'s parent, or ``None`` if it cannot be determined."""
    if pid <= 1:
        return None
    if sys.platform == "win32":  # pragma: no cover - platform specific
        return _parent_of_ps(pid)
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return _parent_of_ps(pid)
    try:
        # Field 2 is "(comm)" and may itself contain spaces and parens, so the
        # reliable cut is the last ")" -- the same trick ``daemon`` uses.
        rest = text[text.rindex(")") + 2 :].split()
        return int(rest[1])  # ppid is field 4 overall == index 1 here
    except (ValueError, IndexError):
        return _parent_of_ps(pid)


def _parent_of_ps(pid: int) -> int | None:
    try:
        result = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def ancestors(pid: int | None = None) -> Iterator[int]:
    """Yield ``pid``'s ancestors, nearest first, excluding ``pid`` itself."""
    current = os.getpid() if pid is None else pid
    seen: set[int] = set()
    for _ in range(MAX_DEPTH):
        parent = _parent_of(current)
        if parent is None or parent <= 1 or parent in seen:
            return
        seen.add(parent)
        yield parent
        current = parent


def is_descendant_of(ancestor_pid: int | None, pid: int | None = None) -> bool | None:
    """Is ``pid`` running underneath ``ancestor_pid``?

    ``None`` means "cannot tell" (Windows). Callers must treat that as a
    warning rather than as permission -- when the answer is unknown, the safe
    behaviour is to warn, not to proceed silently.
    """
    if not ancestor_pid or ancestor_pid <= 1:
        return False
    if sys.platform == "win32":  # pragma: no cover - platform specific
        return None
    if pid is not None and pid == ancestor_pid:
        return True
    return ancestor_pid in set(ancestors(pid))


def inside_instance(instance_pid: int | None) -> bool | None:
    """Convenience wrapper: is *this* process hosted by that instance?"""
    return is_descendant_of(instance_pid)
