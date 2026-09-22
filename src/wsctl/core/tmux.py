"""Optional tmux backend.

When a session uses ``backend="tmux"`` its command runs inside a detached tmux
session instead of being a direct child of wsctl. The PTY wsctl holds belongs
to a short-lived ``tmux attach`` client, so when the wsctl server exits the
shell keeps running inside the tmux server. On the next start the session is
reattached with the same id, which is what makes sessions survive a restart.

tmux is optional: nothing here is used unless a session requests this backend.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path

PREFIX = "wsctl-"

# Optional per-data-directory namespace so that two wsctl deployments on the
# same host (sharing one tmux server) never see or reap each other's sessions.
_namespace = ""


class TmuxError(RuntimeError):
    """Raised when tmux is required but unavailable or misbehaving."""


def set_namespace(value: str | Path | None) -> None:
    """Bind tmux session names to a data directory (call once at startup)."""
    global _namespace
    if not value:
        _namespace = ""
        return
    digest = hashlib.sha1(str(Path(value).resolve()).encode("utf-8")).hexdigest()
    _namespace = digest[:8]


def namespace() -> str:
    return _namespace


def _prefix() -> str:
    return f"{PREFIX}{_namespace}-" if _namespace else PREFIX


def tmux_path() -> str | None:
    return shutil.which("tmux")


def is_available() -> bool:
    return tmux_path() is not None


def session_name(sid: str) -> str:
    return f"{_prefix()}{sid}"


def owns(name: str) -> bool:
    """Whether ``name`` belongs to this deployment's namespace."""
    return name.startswith(_prefix())


def sid_from_name(name: str) -> str:
    prefix = _prefix()
    return name[len(prefix):] if name.startswith(prefix) else ""


def wrap_argv(name: str, argv: list[str]) -> list[str]:
    """Build the argv that attaches to (or creates) the named tmux session."""
    tmux = tmux_path()
    if tmux is None:
        raise TmuxError("tmux backend requested but tmux is not installed")
    return [tmux, "new-session", "-A", "-s", name, *argv]


def has_session(name: str) -> bool:
    tmux = tmux_path()
    if tmux is None:
        return False
    result = subprocess.run(
        [tmux, "has-session", "-t", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def kill_session(name: str) -> None:
    tmux = tmux_path()
    if tmux is None:
        return
    subprocess.run(
        [tmux, "kill-session", "-t", name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def list_sessions() -> list[str]:
    """Return the names of all tmux sessions (empty if tmux is unavailable)."""
    tmux = tmux_path()
    if tmux is None:
        return []
    result = subprocess.run(
        [tmux, "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# Async wrappers: the subprocess calls above block, so async callers must run
# them off the event loop.
async def has_session_async(name: str) -> bool:
    return await asyncio.to_thread(has_session, name)


async def kill_session_async(name: str) -> None:
    await asyncio.to_thread(kill_session, name)


async def list_sessions_async() -> list[str]:
    return await asyncio.to_thread(list_sessions)
