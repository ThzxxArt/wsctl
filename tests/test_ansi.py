"""Where ANSI sequences begin and end.

Dropping terminal bytes keeps a slow viewer connected; dropping them across a
sequence boundary is what turned `vi`/`htop` into garbage. These are the
judgements that make a drop safe, so they are tested like safety-critical
code.
"""

from __future__ import annotations

import pytest

from wsctl.core.ansi import (
    ends_inside_escape,
    is_boundary_aligned,
    sequence_end,
    skip_partial_prefix,
)

CSI_RED = b"\x1b[31m"
CSI_RESET = b"\x1b[0m"
CSI_MOVE = b"\x1b[10;20H"
OSC_TITLE = b"\x1b]0;title\x07"
OSC_ST = b"\x1b]8;;http://x\x1b\\"
CHARSET = b"\x1b(B"
CLEAR = b"\x1b[2J"


@pytest.mark.parametrize(
    "buf,expected",
    [
        (b"", False),
        (b"plain text", False),
        (b"hello\x1b[31mred", False),
        (b"hello\x1b[31m", False),
        (b"hello\x1b", True),
        (b"hello\x1b[", True),
        (b"hello\x1b[31", True),
        (b"hello\x1b[", True),
        (b"pre\x1b]0;title", True),
        (b"pre\x1b]0;title\x07", False),
        # `ESC ( B` is a complete three-byte sequence; truncating *before* the
        # `B` is what makes it incomplete.
        (b"pre\x1b(B", False),
        (b"pre\x1b(", True),
        (b"pre\x1b(Bx", False),
    ],
)
def test_ends_inside_escape(buf: bytes, expected: bool) -> None:
    assert ends_inside_escape(buf) is expected


def test_sequence_end_finds_the_real_end() -> None:
    assert sequence_end(CSI_RED + b"x", 0) == len(CSI_RED)
    assert sequence_end(CSI_MOVE + b"x", 0) == len(CSI_MOVE)
    assert sequence_end(OSC_TITLE + b"x", 0) == len(OSC_TITLE)
    assert sequence_end(OSC_ST + b"x", 0) == len(OSC_ST)
    assert sequence_end(CHARSET + b"x", 0) == len(CHARSET)
    assert sequence_end(CLEAR + b"x", 0) == len(CLEAR)
    # incomplete -> None
    assert sequence_end(b"\x1b[31", 0) is None
    assert sequence_end(b"\x1b", 0) is None


def test_skip_partial_prefix_only_dangles_the_head() -> None:
    # Plain text is untouched.
    assert skip_partial_prefix(b"plain") == b"plain"
    # A *complete* leading sequence is kept -- there is nothing partial here.
    assert skip_partial_prefix(CSI_RED + b"rest") == CSI_RED + b"rest"
    # A sequence that never completes here is all there is: drop it whole.
    assert skip_partial_prefix(b"\x1b[31") == b""
    assert skip_partial_prefix(b"\x1b") == b""
    assert skip_partial_prefix(b"") == b""


def test_boundary_aligned_is_what_shedding_must_preserve() -> None:
    assert is_boundary_aligned(b"")
    assert is_boundary_aligned(b"plain")
    assert is_boundary_aligned(CSI_RESET + b"plain")
    assert not is_boundary_aligned(b"\x1b[31")


def test_a_split_colour_sequence_is_not_a_safe_cut_point() -> None:
    """The exact hazard: a PTY read cuts ``ESC[31m`` across two frames."""
    first, second = CSI_RED[:3], CSI_RED[3:] + b"red text"
    assert ends_inside_escape(first) is True, "ESC[31 is cut mid-sequence"
    assert ends_inside_escape(first + second) is False, "together they complete"
    # The invariant shedding must preserve is about the *cut point*: whatever
    # we drop has to end outside a sequence, so what survives is read fresh.
    # ``second`` on its own starts with plain text and is therefore a valid
    # starting point -- the damage would be the torn escape we dropped, not
    # something in what is left.
    assert is_boundary_aligned(second) is True
    assert is_boundary_aligned(first) is False


def test_tracker_sees_a_sequence_split_across_frames() -> None:
    """The hazard a stateless scan cannot see: a frame starting mid-escape.

    ``ESC[31m`` cut as ``ESC[3`` + ``1m`` leaves the second frame looking like
    plain text to a scan that starts at its own byte 0. A streaming tracker
    knows the truth, and that is what makes "drop here is safe" decidable.
    """
    from wsctl.core.ansi import AnsiTracker

    tracker = AnsiTracker()
    assert tracker.feed(b"\x1b[3") is True, "ESC[3 leaves us inside CSI"
    assert tracker.feed(b"1m") is False, "1m completes the colour"
    assert tracker.inside is False

    tracker = AnsiTracker()
    assert tracker.feed(b"plain") is False
    assert tracker.feed(b"\x1b[31m") is False
    assert tracker.feed(b"text") is False

    tracker = AnsiTracker()
    assert tracker.feed(b"\x1b]0;t") is True, "OSC unterminated"
    assert tracker.feed(b"\x07") is False, "BEL closes OSC"

    tracker = AnsiTracker()
    assert tracker.feed(b"\x1b") is True, "bare ESC"
    assert tracker.feed(b"c") is False, "ESC c is a complete reset"


def test_skip_partial_prefix_keeps_a_complete_leading_sequence() -> None:
    """Only *partial* prefixes are dropped; a real colour is not garbage."""
    assert skip_partial_prefix(b"\x1b[31mX") == b"\x1b[31mX"
    assert skip_partial_prefix(b"\x1b[31") == b""
    assert skip_partial_prefix(b"plain") == b"plain"


def test_tracker_is_chunking_invariant() -> None:
    """The same bytes, split however you like, must reach the same state.

    This is the property the whole "shed on an ANSI boundary" claim rests on:
    frames arrive at whatever boundaries a PTY read happens to produce. A state
    machine whose answer depends on where the cuts fell cannot decide whether
    a cut is safe -- and the first version of this one did exactly that, by
    treating ``ESC c`` as three bytes instead of two and ``ESC ( B`` as four
    instead of three.
    """
    import random

    from wsctl.core.ansi import AnsiTracker, ends_inside_escape

    rng = random.Random(20260923)
    for _ in range(20000):
        buf = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
        whole = AnsiTracker()
        expected = whole.feed(buf)
        split = AnsiTracker()
        i = 0
        while i < len(buf):
            cut = rng.randrange(1, 9)
            split.feed(buf[i : i + cut])
            i += cut
        assert split.inside == expected, f"split changed the answer for {buf!r}"

    # And the streaming and stateless models agree on boundary-aligned input.
    for _ in range(5000):
        buf = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 12)))
        tracker = AnsiTracker()
        assert tracker.feed(buf) == ends_inside_escape(buf), buf


def test_two_byte_and_three_byte_sequences_finish_where_they_should() -> None:
    """The exact off-by-ones that broke chunking invariance."""
    from wsctl.core.ansi import AnsiTracker

    t = AnsiTracker()
    assert t.feed(b"\x1bc") is False, "ESC c is two bytes and complete"
    t = AnsiTracker()
    assert t.feed(b"\x1b(B") is False, "ESC ( B is three bytes and complete"
    t = AnsiTracker()
    assert t.feed(b"\x1b(") is True, "ESC ( still needs its final byte"
    assert t.feed(b"B") is False
    t = AnsiTracker()
    assert t.feed(b"\x1b") is True
    assert t.feed(b"c") is False


def test_an_opener_split_across_frames_is_still_seen() -> None:
    """``ESC ]`` cut as ``ESC`` + ``]`` must not turn into plain text.

    The ``esc`` state (a bare ESC at the end of a buffer) has to dispatch on
    the *next* frame's first byte as the sequence kind. Resetting instead ate
    the ``]``, so an OSC opener split across frames was never recognised.
    """
    from wsctl.core.ansi import AnsiTracker

    t = AnsiTracker()
    assert t.feed(b"\x1b") is True
    assert t.feed(b"]0;title") is True, "ESC ] must open an OSC, not plain text"
    assert t.feed(b"\x07") is False

    t = AnsiTracker()
    assert t.feed(b"\x1b") is True
    assert t.feed(b"[31m") is False, "ESC [ must open a CSI"

    t = AnsiTracker()
    assert t.feed(b"\x1b") is True
    assert t.feed(b"(B") is False, "ESC ( B is a complete charset designation"


def test_resume_and_skip_to_boundary_re_anchor_a_kept_head() -> None:
    """A head that resumes mid-escape must skip the *rest of that sequence*.

    This is the re-anchor half of scrollback eviction: the bytes before the
    kept head are gone, and only the recorded state says what kind of sequence
    the head is still inside. Scanning the head alone reads its remainder
    (``1m...``) as text -- the exact 花屏 this exists to prevent.
    """
    from wsctl.core.ansi import AnsiTracker

    # The kept head is `1mRED` -- the tail of `ESC[31m` plus real text.
    t = AnsiTracker()
    t.resume("csi")
    assert t.skip_to_boundary(b"1mRED") == b"RED"
    assert not t.inside

    t = AnsiTracker()
    t.resume("osc")
    assert t.skip_to_boundary(b"title\x07after") == b"after"

    t = AnsiTracker()
    t.resume("short")  # ESC ( B: one final byte outstanding
    assert t.skip_to_boundary(b"Btext") == b"text"

    # Not inside: nothing to skip.
    t = AnsiTracker()
    assert t.skip_to_boundary(b"plain") == b"plain"

    # The sequence never completes here: everything is remainder.
    t = AnsiTracker()
    t.resume("csi")
    assert t.skip_to_boundary(b"31") == b""

    # `state` round-trips through `resume`.
    t = AnsiTracker()
    t.feed(b"\x1b[31")
    saved = t.state
    u = AnsiTracker()
    u.resume(saved)
    assert u.inside
    assert u.skip_to_boundary(b"mOK") == b"OK"
