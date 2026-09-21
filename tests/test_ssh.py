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
