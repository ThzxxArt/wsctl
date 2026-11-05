"""Adapts a Starlette WebSocket to the :class:`~wsctl.core.session.Client` protocol.

The outbound side is a bounded pair of FIFOs. What happens at the bound is the
whole point of this module: the earlier version **killed the connection** the
moment a client fell behind. On a burst of output that turned a merely slow
viewer into a dropped one, and the browser then looped reconnect -> clear ->
replay -> dropped again. To the person watching, their terminal froze and
"jumped".

A terminal is a *current view*, not an archive: when the far end cannot keep
up, the right answer is to shed the oldest frames and say so, never to cut the
cord. ``bytes`` items are terminal data and are evictable; ``dict`` items are
control messages and are bounded separately -- the one message explaining what
was lost must still get through, but it must not become the leak.

Two queues, one sequence
------------------------

Terminal frames and control frames live in separate deques, every item tagged
with a monotonic sequence number, and ``run()`` emits whichever queue holds the
lower number. Splitting them must not change the order a client observes, and
the tests assert that.

The split is what makes eviction O(1). The single-queue version had to *scan*
for the first ``bytes`` item on every drop (the head might be a control
message) -- an O(n) pass per drop, O(n^2) for a burst -- and that scan ran
inside :meth:`~wsctl.core.session.TermSession._broadcast`, i.e. on the same
event loop that is reading the PTY. Under a flood the loop stalled, the kernel
buffer filled, and the shell itself stopped writing. That is a *server-side*
freeze, a different failure from the client-side one shedding exists to
prevent. Now the oldest terminal frame is simply ``popleft()``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket

from wsctl.core.ansi import AnsiTracker
from wsctl.core.protocol import (
    FLAG_DISPLAY,
    FLAG_LAST,
    FLAG_SNAPSHOT,
    MAX_DATA_FRAME,
    encode_data,
)
from wsctl.core.session import ClientGone, note_shed_frames

MAX_PENDING = 512
#: Control frames are small and are never shed for *byte* pressure, so they
#: get their own bound. Without one a peer that never drains could accumulate
#: them forever -- the previous implementation bounded every frame together
#: and this rewrite briefly lost that.
MAX_CONTROL_PENDING = 128


class WsClient:
    """Ordered, bounded outbound queues for one WebSocket connection.

    ``bytes`` items become binary frames (terminal data); ``dict`` items become
    JSON text frames (control messages).
    """

    def __init__(
        self,
        websocket: WebSocket,
        *,
        max_pending: int = MAX_PENDING,
        max_bytes: int = 0,
        on_shed: Callable[[], None] | None = None,
        on_gap: Callable[[int, int], None] | None = None,
        protocol_version: int = 1,
    ) -> None:
        self._ws = websocket
        self._protocol_version = protocol_version
        self.on_gap = on_gap
        #: Human label shown in the online-viewer list. Set by the WebSocket
        #: endpoint after authentication; never trusted for authorization.
        self.viewer_label = ""
        #: Called once per shedding burst, right after the viewer is told its
        #: screen is incomplete. The notice is not the repair: a full-screen
        #: application draws on top of a base the dropped frames no longer
        #: carry, so without a repaint the regions it last drew are never
        #: painted again. Whoever wires this is expected to ask the session to
        #: redraw (``TermSession.request_repaint``) rather than wait for a
        #: human to click 重新同步.
        self.on_shed = on_shed
        #: ``(order, payload, ends_inside_escape, data_seq, flags)`` -- the
        #: first flag is the streaming ANSI state *after* that frame. A drop
        #: may only land where the state is outside a sequence, otherwise the
        #: surviving stream resumes mid-escape and a full-screen program
        #: renders as garbage. ``data_seq`` is ``None`` for the legacy
        #: endpoint; protocol v2 stamps every terminal frame with its global
        #: byte offset so a reconnect can resume exactly.
        self._binary: deque[tuple[int, bytes, bool, int | None, int]] = deque()
        self._control: deque[tuple[int, dict[str, Any]]] = deque()
        # Streaming ANSI state fed once per terminal frame. A stateless scan of
        # a single frame cannot answer "does this end inside a sequence",
        # because a PTY read can begin one.
        self._tracker = AnsiTracker()
        self._seq = 0
        self._wakeup = asyncio.Event()
        self._max_pending = max_pending
        self._max_bytes = max_bytes
        self._pending_bytes = 0
        self._closed = False
        #: Why this client ended, when the sink decided for itself. ``None``
        #: means nothing unusual; the transport reports this instead of a plain
        #: 1000 ("normal closure"), which would be a lie for "we threw you out".
        self.close_code: int | None = None
        #: Bytes discarded to keep the connection alive, and how many times.
        #: Surfaced to the user: silently losing history is not acceptable,
        #: but losing the connection over it is worse.
        self.dropped_bytes = 0
        self.dropped_events = 0
        self._shed_notice_open = False
        self._gap_open = False
        #: Exact range evicted since the last reported gap. ``None`` means no
        #: sequenced frame was lost (legacy endpoint or a control-only queue).
        self._lost_from: int | None = None
        self._lost_to: int | None = None
        #: Highest sequence the client has acknowledged writing to xterm.
        #: Monotonic: a stale ACK (arriving out of order) must not move it
        #: backwards and make the server re-send bytes.
        self.last_acked_seq = 0

    # -- introspection (also the tests' view of the queue) ---------------

    @property
    def pending_bytes(self) -> int:
        return self._pending_bytes

    @property
    def max_bytes(self) -> int:
        """The per-client byte budget (0 = unbounded by bytes).

        Replay framing must respect this: a frame larger than the budget can
        never be queued -- ``put`` sheds the *whole* frame -- so coalescing to
        64 KiB against a 16 KiB client threw away the entire replay where the
        raw (small) chunks used to fit.
        """
        return self._max_bytes

    @property
    def closed(self) -> bool:
        return self._closed

    def queued_binary(self) -> bytes:
        """The terminal frames still queued, oldest first."""
        return b"".join(payload for _o, payload, _i, _s, _f in self._binary)

    def queued_controls(self) -> list[dict[str, Any]]:
        """The control frames still queued, oldest first."""
        return [payload for _seq, payload in self._control]

    @property
    def queue_len(self) -> int:
        """Total frames waiting to go out, of either kind."""
        return len(self._binary) + len(self._control)

    # -- shedding --------------------------------------------------------

    def _drop_oldest_binary(self) -> bool:
        """Evict the oldest terminal frame(s), landing on an ANSI boundary.

        A PTY read can cut a colour or cursor sequence in half. Dropping the
        first half leaves the surviving stream resuming mid-escape, and a
        full-screen program -- ``vi``, ``htop`` -- then renders as garbage
        until its next repaint. Keep dropping until the last frame removed
        *ends outside* a sequence, which is what the streaming tracker (not a
        stateless scan) can tell us: a frame may well begin mid-escape.

        O(1) per drop: the oldest terminal frame is the head of its own deque,
        so there is nothing to scan for.

        Returns ``False`` when nothing terminal-shaped is left to give up.
        """
        if not self._binary:
            return False

        # A snapshot is one atomic repaint split into 1 MiB chunks. Dropping
        # only a prefix leaves the client with an incomplete screen and -- if
        # the tail still carries FLAG_LAST -- a resume cursor that claims it is
        # whole. Give up the *entire remaining group* instead; the client then
        # has no LAST, stays in "resyncing", and our gap control asks it to
        # fetch a fresh snapshot.
        _order, _item, _inside, anchor, flags = self._binary[0]
        if anchor is not None and flags & FLAG_SNAPSHOT:
            while self._binary:
                _o, item, _ends, data_seq, frame_flags = self._binary[0]
                if data_seq != anchor or not frame_flags & FLAG_SNAPSHOT:
                    break
                self._binary.popleft()
                self._pending_bytes = max(0, self._pending_bytes - len(item))
                self.dropped_bytes += len(item)
                if self._lost_from is None:
                    self._lost_from = anchor
                self._lost_to = max(self._lost_to or 0, anchor + len(item))
            return True

        while self._binary:
            _order, item, ends_inside, data_seq, frame_flags = self._binary.popleft()
            self._pending_bytes = max(0, self._pending_bytes - len(item))
            self.dropped_bytes += len(item)
            if data_seq is not None and not frame_flags & FLAG_DISPLAY:
                if self._lost_from is None:
                    self._lost_from = data_seq
                self._lost_to = data_seq + len(item)
            # ``ends_inside`` is the state *after* that frame. A frame that
            # leaves us outside a sequence is a safe cut point.
            if not ends_inside:
                return True
        return False

    def shed_oldest(self, target_bytes: int) -> int:
        """Give up at least ``target_bytes`` of queued terminal data.

        Used when a *session* is over its own memory cap: shedding one slow
        viewer's backlog is the lesser evil, and strictly better than cutting
        that viewer off. Returns how many bytes went.
        """
        before = self.dropped_bytes
        while self._pending_bytes > 0 and (self.dropped_bytes - before) < target_bytes:
            if not self._drop_oldest_binary():
                break
        if self.dropped_bytes > before:
            self.dropped_events += 1
            note_shed_frames()
            self._announce_shed()
        return self.dropped_bytes - before

    def _drop_oldest_control(self) -> None:
        """Make room for one control message by dropping the oldest one."""
        if self._control:
            self._control.popleft()

    def _shed_for(self, size: int) -> None:
        """Make room for ``size`` bytes by evicting the oldest terminal data."""
        if self._max_bytes <= 0 and self.queue_len < self._max_pending:
            return
        shed = False
        while (self._max_bytes > 0 and self._pending_bytes + size > self._max_bytes) or (
            self.queue_len >= self._max_pending
        ):
            if not self._drop_oldest_binary():
                break
            shed = True
        if shed:
            self.dropped_events += 1
            note_shed_frames()
            self._announce_shed()

    def _announce_shed(self) -> None:
        """Tell the viewer, once per burst, that its screen is now incomplete.

        Losing frames is the lesser evil; losing them *silently* is not, which
        is the same rule the input path already follows for dropped keystrokes.

        Protocol v2 clients get the **exact** lost range as a ``gap`` message
        before the generic notice: "you lost bytes 123000..123456" is
        actionable (take a snapshot, know what is missing), while "some output
        was omitted" is not. The range is only sent once per burst, mirroring
        the notice.
        """
        # One gap per burst. This used to run before the notice gate below,
        # so a shed storm produced one gap per dropped frame; every gap made
        # the browser request a snapshot and the rate limiter rejected most of
        # them, turning backpressure into a request storm.
        if self._protocol_version >= 2 and self._lost_from is not None and not self._gap_open:
            self._gap_open = True
            lost_from = self._lost_from
            lost_to = self._lost_to if self._lost_to is not None else lost_from
            self._lost_from = None
            self._lost_to = None
            with contextlib.suppress(Exception):
                self._put_control(
                    {
                        "type": "gap",
                        "from": lost_from,
                        "to": lost_to,
                        "reason": "shed",
                    }
                )
                if self.on_gap is not None:
                    self.on_gap(lost_from, lost_to)
        if self._shed_notice_open:
            return
        self._shed_notice_open = True
        with contextlib.suppress(Exception):
            self._put_control(
                {
                    "type": "desync",
                    "level": "warn",
                    "msg": "输出过快，部分内容已省略（连接未断）",
                }
            )
        # The notice above is only the *report*. Left there, the viewer's
        # screen stays gapped until a human clicks 重新同步 -- and for a
        # full-screen application "stays gapped" means the regions it last
        # drew (sidebars, status bars, input boxes) are never painted again,
        # because it redraws only what it thinks changed. Ask for the repair
        # now, on the same once-per-burst cadence as the notice.
        if self.on_shed is not None:
            with contextlib.suppress(Exception):
                self.on_shed()

    # -- queueing --------------------------------------------------------

    def _put_control(self, message: dict[str, Any]) -> bool:
        """Queue a control frame, shedding the *oldest control frame* if needed.

        Control frames are exempt from the byte budget -- the one message
        explaining a loss must get through -- but they are not exempt from
        being bounded. An earlier rewrite of this class let them accumulate
        without limit, which is its own way to take a connection down.
        """
        while len(self._control) >= MAX_CONTROL_PENDING:
            before = len(self._control)
            self._drop_oldest_control()
            if len(self._control) == before:
                break
        if self._protocol_version >= 2 and "v" not in message:
            message = {**message, "v": 2}
        self._seq += 1
        self._control.append((self._seq, message))
        self._wakeup.set()
        return True

    def ack(self, seq: int) -> None:
        """Record an ACK from the client. Stale ACKs are ignored."""
        if seq > self.last_acked_seq:
            self.last_acked_seq = int(seq)

    def _record_drop(self, seq: int, size: int, *, announce: bool = True) -> None:
        """Count a dropped frame; optionally tell the viewer what it lost.

        A snapshot rejected *before* anything was queued is different from a
        queued group later evicted under pressure: the caller falls back to a
        raw replay, so a client-side "request another snapshot" here would
        only create a repair loop.
        """
        self.dropped_bytes += size
        self.dropped_events += 1
        note_shed_frames()
        if announce:
            if self._lost_from is None:
                self._lost_from = seq
            self._lost_to = seq + size
            self._announce_shed()

    def _queue_snapshot(self, seq: int, payload: bytes, flags: int) -> None:
        """Queue a snapshot as one atomic unit: all chunks or none.

        A snapshot is a full-screen repaint. The client atomically adopts the
        anchor only on ``FLAG_LAST``; a prefix followed by LAST (the old
        per-chunk queueing let backpressure drop just the middle) painted a
        half-screen and advanced the resume cursor as if it were whole. That
        is the redraw corruption this method exists to prevent.
        """
        chunks = [
            payload[offset : offset + MAX_DATA_FRAME]
            for offset in range(0, len(payload), MAX_DATA_FRAME)
        ]
        total = len(payload)

        # Make room before touching the queue. Shedding can drop a previous
        # (now obsolete) snapshot group and old live frames.
        self._shed_for(total)
        fits_bytes = self._max_bytes <= 0 or self._pending_bytes + total <= self._max_bytes
        fits_frames = self.queue_len + len(chunks) <= self._max_pending
        if not fits_bytes or not fits_frames:
            # Never enqueue a partial snapshot. The caller (attach/resync)
            # sees dropped_events and falls back to a raw replay. Do not emit
            # a gap: no snapshot bytes were sent, and asking the client to
            # request another snapshot would fight the fallback.
            self._record_drop(seq, total, announce=False)
            return

        self._pending_bytes += total
        for index, chunk in enumerate(chunks):
            chunk_flags = flags
            if index == len(chunks) - 1:
                chunk_flags |= FLAG_LAST
            self._seq += 1
            # Snapshots are display-only, not source-stream bytes: feeding
            # them to the ANSI tracker would make a later live-frame cut
            # depend on a repaint the stream never contained. The group is
            # dropped atomically, so every chunk is a safe cut point.
            self._binary.append((self._seq, chunk, False, int(seq), int(chunk_flags)))
        self._wakeup.set()

    def put_data(self, seq: int, payload: bytes, flags: int = 0) -> None:
        """Queue one sequenced terminal frame (protocol v2)."""
        if self._closed:
            raise ClientGone("连接已关闭")
        if self._protocol_version < 2:
            # The legacy endpoint speaks raw bytes. ``_put_data`` calls this
            # method whenever the sink has it, and every ``WsClient`` does --
            # so without this branch v1 viewers received a 10-byte v2 header
            # before every PTY read and rendered it as garbage.
            self.put(payload)
            return
        if flags & FLAG_SNAPSHOT:
            self._queue_snapshot(seq, payload, flags)
            return
        ends_inside = self._tracker.feed(payload)
        if self._max_bytes > 0 and len(payload) > self._max_bytes:
            # Display-only bytes are not resumable stream content; dropping
            # them must not manufacture a "lost range" in the stream.
            if flags & FLAG_DISPLAY:
                self.dropped_bytes += len(payload)
                self.dropped_events += 1
                note_shed_frames()
                self._announce_shed()
            else:
                self._record_drop(seq, len(payload))
            return
        self._shed_for(len(payload))
        self._pending_bytes += len(payload)
        self._seq += 1
        # v2 frames always carry a header, even when they are display-only.
        # ``data_seq`` stays the wire anchor; ``_drop_oldest_binary`` decides
        # whether a drop is a resumable-stream loss from the flags.
        data_seq = int(seq)
        self._binary.append((self._seq, payload, ends_inside, data_seq, int(flags)))
        self._wakeup.set()

    def put(self, item: bytes | dict[str, Any]) -> None:
        """Queue ``item``; terminal data is shed under pressure, never the link."""
        if self._closed:
            raise ClientGone("连接已关闭")
        if isinstance(item, bytes):
            # Feed the tracker *unconditionally*, before any eviction decision.
            # It models the source stream, and a frame dropped under pressure
            # still moves the parser along: the next kept frame begins wherever
            # this one would have left off. Skipping it (as the "does not fit"
            # path used to) desynchronised the recorded state, and the next cut
            # could land inside an escape sequence -- the exact corruption this
            # whole mechanism exists to prevent.
            ends_inside = self._tracker.feed(item)
            if self._max_bytes > 0 and len(item) > self._max_bytes:
                # A frame bigger than the whole budget can never enter the
                # queue. Shedding older frames to "make room" for it first
                # destroys whatever was queued -- an attach replay, typically
                # -- and the frame is then dropped anyway: the worst of both
                # worlds. A doomed frame is lost alone. (This also settles
                # every "cannot fit" case: while it stands, the shed below
                # always finds room for a frame of at most ``max_bytes``, so
                # no second loss branch is reachable.)
                self.dropped_bytes += len(item)
                self.dropped_events += 1
                # Counted like every other loss: this branch is *how* every
                # frame is lost when ``client_max_bytes`` is configured below
                # one PTY read (64 KiB), and it used to be the one shedding
                # path that never reached the process-wide counter -- so the
                # metrics page showed a healthy zero while the viewer's screen
                # went blind.
                note_shed_frames()
                self._announce_shed()
                return
            self._shed_for(len(item))
            self._pending_bytes += len(item)
            self._seq += 1
            self._binary.append((self._seq, item, ends_inside, None, 0))
            self._wakeup.set()
        else:
            self._put_control(item)

    def final_notice(self, message: dict[str, Any]) -> bool:
        """Enqueue one last control message even though the client is going.

        Call this **before** :meth:`close`; after it the writer is on its way
        out and anything queued may never be sent.
        """
        if self._closed:
            return False
        return self._put_control(message)

    def close(self) -> None:
        """Stop the writer.

        Must terminate the writer even when the client already marked itself
        closed. Returning early left ``run()`` blocked forever, so every
        dropped client cost a two-second teardown stall -- and anything queued
        after that point was never sent.
        """
        self._closed = True
        self._wakeup.set()

    async def run(self) -> None:
        try:
            while True:
                if not self._binary and not self._control:
                    self._wakeup.clear()
                    if self._closed:
                        return
                    await self._wakeup.wait()
                    continue
                # Arrival order survives the split: whichever queue holds the
                # lower sequence number goes first. A client must not be able to
                # tell the two deques apart from the single queue this replaced.
                use_binary = bool(self._binary) and (
                    not self._control or self._binary[0][0] < self._control[0][0]
                )
                if use_binary:
                    _seq, frame, _inside, data_seq, flags = self._binary.popleft()
                    self._pending_bytes = max(0, self._pending_bytes - len(frame))
                    if data_seq is None:
                        await self._ws.send_bytes(frame)
                    else:
                        await self._ws.send_bytes(encode_data(data_seq, frame, flags))
                else:
                    _seq, message = self._control.popleft()
                    await self._ws.send_json(message)
                if not self._binary and not self._control:
                    self._shed_notice_open = False
                    self._gap_open = False
                if self._closed and not self._binary and not self._control:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._closed = True


__all__ = ["MAX_CONTROL_PENDING", "MAX_PENDING", "WsClient"]
