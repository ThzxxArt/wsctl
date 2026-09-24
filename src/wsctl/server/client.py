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
from typing import Any

from fastapi import WebSocket

from wsctl.core.ansi import AnsiTracker
from wsctl.core.session import ClientGone

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
    ) -> None:
        self._ws = websocket
        #: ``(seq, payload, ends_inside_escape)`` -- the flag is the streaming
        #: ANSI state *after* that frame. A drop may only land where the state
        #: is outside a sequence, otherwise the surviving stream resumes
        #: mid-escape and a full-screen program renders as garbage.
        self._binary: deque[tuple[int, bytes, bool]] = deque()
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
        return b"".join(payload for _seq, payload, _inside in self._binary)

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
        while self._binary:
            _seq, item, ends_inside = self._binary.popleft()
            self._pending_bytes = max(0, self._pending_bytes - len(item))
            self.dropped_bytes += len(item)
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
            self._announce_shed()

    def _announce_shed(self) -> None:
        """Tell the viewer, once per burst, that its screen is now incomplete.

        Losing frames is the lesser evil; losing them *silently* is not, which
        is the same rule the input path already follows for dropped keystrokes.

        ``desync`` rather than a plain ``notice`` because the consequence is
        specific and actionable: a full-screen program is now drawing from a
        gapped stream, and the remedy is a resync (or the program's own
        repaint) -- not an acknowledgement.
        """
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
        self._seq += 1
        self._control.append((self._seq, message))
        self._wakeup.set()
        return True

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
                self._announce_shed()
                return
            self._shed_for(len(item))
            self._pending_bytes += len(item)
            self._seq += 1
            self._binary.append((self._seq, item, ends_inside))
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
                    _seq, frame, _inside = self._binary.popleft()
                    self._pending_bytes = max(0, self._pending_bytes - len(frame))
                    await self._ws.send_bytes(frame)
                else:
                    _seq, message = self._control.popleft()
                    await self._ws.send_json(message)
                if not self._binary and not self._control and self._shed_notice_open:
                    self._shed_notice_open = False
                if self._closed and not self._binary and not self._control:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._closed = True


__all__ = ["MAX_CONTROL_PENDING", "MAX_PENDING", "WsClient"]
