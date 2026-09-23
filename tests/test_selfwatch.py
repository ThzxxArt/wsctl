"""The "don't saw off the branch you are sitting on" detector."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from wsctl.core import selfwatch


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process tree only")
def test_a_child_is_descended_from_the_process_that_spawned_it() -> None:
    """The relationship the guard relies on, checked in both directions.

    Deliberately not built with ``sh -c``: that shell *exec*s its last command,
    so the grandchild's parent becomes us and the intermediate shell vanishes
    from the chain -- which made an earlier version of this test assert
    backwards.
    """
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert selfwatch.is_descendant_of(os.getpid(), pid=proc.pid) is True
        assert selfwatch.is_descendant_of(proc.pid, pid=os.getpid()) is False
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process tree only")
def test_ancestors_climbs_the_chain_without_loops() -> None:
    chain = list(selfwatch.ancestors(os.getpid()))
    assert chain, "every process except init has an ancestor"
    assert chain[0] == os.getppid()
    assert len(chain) == len(set(chain)), "the chain must not repeat"
    assert all(pid > 1 for pid in chain)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process tree only")
def test_an_unrelated_pid_is_not_an_ancestor() -> None:
    # init/systemd is everybody's ancestor eventually; a *sibling* is not.
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert selfwatch.is_descendant_of(proc.pid, pid=os.getpid()) is False
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_nonsense_pids_are_answered_safely() -> None:
    assert selfwatch.is_descendant_of(None) is False
    assert selfwatch.is_descendant_of(0) is False
    assert selfwatch.is_descendant_of(-1) is False
    # A pid that does not exist cannot be an ancestor of anything.
    assert selfwatch.is_descendant_of(2 ** 22, pid=os.getpid()) in (False, None)


def test_windows_says_unknown_rather_than_safe() -> None:
    """On Windows we must not claim "you are safe" when we cannot tell."""
    if sys.platform != "win32":
        assert selfwatch.is_descendant_of(1, pid=os.getpid()) is not None
        pytest.skip("behaviour verified on POSIX; Windows returns None by design")
    assert selfwatch.is_descendant_of(1234) is None
