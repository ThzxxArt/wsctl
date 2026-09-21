"""Optional tmux backend.

When a session uses ``backend="tmux"`` its command runs inside a detached tmux
session instead of being a direct child of wsctl. The PTY wsctl holds belongs
to a short-lived ``tmux attach`` client, so when the wsctl server exits the
shell keeps running inside the tmux server. On the next start the session is
reattached with the same id, which is what makes sessions survive a restart.

tmux is optional: nothing here is used unless a session requests this backend.
"""

from __future__ import annotations

import shutil
import subprocess

PREFIX = "wsctl-"


class TmuxError(RuntimeError):
    """Raised when tmux is required but unavailable or misbehaving."""


def tmux_path() -> str | None:
    return shutil.which("tmux")


def is_available() -> bool:
    return tmux_path() is not None


def session_name(sid: str) -> str:
    return f"{PREFIX}{sid}"


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
