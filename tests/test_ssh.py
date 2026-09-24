from __future__ import annotations

import pytest

from wsctl.core.ssh import SshError, SshTarget


def test_argv_basic() -> None:
    assert SshTarget("example.com").argv() == ["ssh", "-tt", "example.com"]


def test_argv_full() -> None:
    target = SshTarget(
        "example.com",
        user="alice",
        port=2222,
        identity="/home/alice/.ssh/id_ed25519",
        options=["StrictHostKeyChecking=accept-new"],
        remote_command="uptime",
    )
    assert target.argv() == [
        "ssh",
        "-tt",
        "-p",
        "2222",
        "-i",
        "/home/alice/.ssh/id_ed25519",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "alice@example.com",
        "uptime",
    ]


def test_destination() -> None:
    assert SshTarget("h").destination() == "h"
    assert SshTarget("h", user="u").destination() == "u@h"


@pytest.mark.parametrize("host", ["", "-evil", "a b", "host\nx"])
def test_invalid_host(host: str) -> None:
    with pytest.raises(SshError):
        SshTarget(host)


def test_invalid_user() -> None:
    with pytest.raises(SshError):
        SshTarget("h", user="-x")


def test_options_that_execute_locally_are_refused() -> None:
    """``-o ProxyCommand=...`` runs a program **on the wsctl host**.

    The module promises "a safe argv"; passing a user-supplied options list
    straight through kept a local command-execution surface open for anyone
    who can create an SSH session. Only configuration knobs that cannot spawn
    anything here are forwarded.
    """
    for option in (
        "ProxyCommand=nc -X connect -x 127.0.0.1:8080 %h %p",
        "LocalCommand=touch /tmp/pwned",
        "PermitLocalCommand=yes",
        "KnownHostsCommand=/bin/sh -c id",
        "HostName=evil.example",
        "IdentityFile=/etc/shadow",
    ):
        with pytest.raises(SshError, match=r"不允许|invalid"):
            SshTarget("example.com", options=[option]), option


def test_port_is_bounded() -> None:
    for port in (0, -1, 70000):
        with pytest.raises(SshError):
            SshTarget("example.com", port=port)


def test_safe_options_are_still_accepted() -> None:
    target = SshTarget(
        "example.com",
        options=["ConnectTimeout=5", "ServerAliveInterval=30", "BatchMode=yes"],
    )
    argv = target.argv()
    assert argv.count("-o") == 3
