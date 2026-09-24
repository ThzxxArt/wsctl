"""SSH backend helpers.

An SSH session is just a session whose command is ``ssh``; this module builds a
safe argv (no shell interpolation -- every value is a separate argument) from a
structured target description.

That is only half of "safe". ``ssh -o`` accepts options that execute programs
on the **local** machine (``ProxyCommand``, ``LocalCommand`` with
``PermitLocalCommand``, ``KnownHostsCommand``...), so passing a user-supplied
options list straight through is a local command-execution surface for anyone
who can create an SSH session. Options are therefore checked against a
whitelist of configuration knobs that cannot run anything here.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field


class SshError(ValueError):
    """Raised for an invalid SSH target."""


#: ``-o`` keys that only *configure* the connection. Anything that can spawn a
#: process locally (``ProxyCommand``, ``LocalCommand``, ``KnownHostsCommand``,
#: ``PermitLocalCommand``, ``HostName`` rewrites, ``IdentityFile`` elsewhere)
#: is refused rather than forwarded.
SAFE_OPTIONS = frozenset(
    {
        "BatchMode",
        "ConnectTimeout",
        "ConnectionAttempts",
        "ServerAliveInterval",
        "ServerAliveCountMax",
        "StrictHostKeyChecking",
        "UserKnownHostsFile",
        "LogLevel",
        "Compression",
        "IPQoS",
        "AddressFamily",
        "TCPKeepAlive",
        "VisualHostKey",
        "IdentitiesOnly",
    }
)


def ssh_available() -> bool:
    return shutil.which("ssh") is not None


def _check_option(option: str) -> str:
    key, sep, _value = option.partition("=")
    name = key.strip()
    if not sep or not name:
        raise SshError(f"invalid ssh option: {option}")
    if name not in SAFE_OPTIONS:
        raise SshError(f"ssh 选项不允许：{name}")
    return option


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
        if self.port is not None and not (1 <= int(self.port) <= 65535):
            raise SshError("invalid ssh port")
        self.options = [_check_option(option) for option in self.options]

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
