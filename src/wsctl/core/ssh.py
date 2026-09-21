"""SSH backend helpers.

An SSH session is just a session whose command is ``ssh``; this module builds a
safe argv (no shell interpolation — every value is a separate argument) from a
structured target description.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field


class SshError(ValueError):
    """Raised for an invalid SSH target."""


def ssh_available() -> bool:
    return shutil.which("ssh") is not None


@dataclass
class SshTarget:
    host: str
    user: str | None = None
    port: int | None = None
    identity: str | None = None
    options: list[str] = field(default_factory=list)
    remote_command: str | None = None

    def __post_init__(self) -> None:
        if not self.host or self.host.startswith("-"):
            raise SshError("invalid ssh host")
        if any(ch.isspace() for ch in self.host):
            raise SshError("ssh host must not contain whitespace")
        if self.user is not None and (not self.user or self.user.startswith("-")):
            raise SshError("invalid ssh user")

    def destination(self) -> str:
        return f"{self.user}@{self.host}" if self.user else self.host

    def argv(self) -> list[str]:
        args = ["ssh", "-tt"]
        if self.port:
            args += ["-p", str(self.port)]
        if self.identity:
            args += ["-i", self.identity]
        for option in self.options:
            args += ["-o", option]
        args.append(self.destination())
        if self.remote_command:
            args.append(self.remote_command)
        return args
