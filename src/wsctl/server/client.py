"""Adapts a Starlette WebSocket to the :class:`~wsctl.core.session.Client` protocol.

The outbound side is a bounded FIFO of frames. What happens at the bound is
the whole point of this module: the earlier version **killed the connection**
the moment a client fell behind. On a burst of output that turned a merely
slow viewer into a dropped one, and the browser then looped reconnect -> clear
-> replay -> dropped again. To the person watching, their terminal froze and
"jumped".

A terminal is a *current view*, not an archive: when the far end cannot keep
up, the right answer is to shed the oldest frames and say so, never to cut the
cord. ``bytes`` items are terminal data and are evictable; ``dict`` items are
control messages and are bounded separately -- the one message explaining what
was lost must still get through, but it must not become the leak.
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
    """Ordered, bounded outbound queue for one WebSocket connection.

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
        self._items: deque[bytes | dict[str, Any]] = deque()
        # Streaming ANSI state at each *frame* boundary, so a drop can land
        # where a terminal would consider the stream fresh. A frame that
        # begins mid-escape (a PTY read cut a sequence) is not a safe cut
        # point, and no stateless scan can tell that.
        self._tracker = AnsiTracker()
        self._item_inside: deque[bool] = deque()
        self._wakeup = asyncio.Event()
        self._max_pending = max_pending
        self._max_bytes = max_bytes
        self._pending_bytes = 0
        self._closed = False
        self._control_count = 0
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

    @property
    def pending_bytes(self) -> int:
        return self._pending_bytes

    @property
    def closed(self) -> bool:
        return self._closed

    def _drop_oldest_binary(self) -> bool:
        """Evict the oldest terminal frame(s), landing on an ANSI boundary.

        A PTY read can cut a colour or cursor sequence in half. Dropping the
        first half leaves the surviving stream resuming mid-escape, and a
        full-screen program -- ``vi``, ``htop`` -- then renders as garbage
        until its next repaint. Keep dropping until the last frame removed
        *ends outside* a sequence, which is what the streaming tracker (not a
        stateless scan) can tell us: a frame may well begin mid-escape.

        Returns ``False`` when nothing terminal-shaped is left to give up.
        """
        while True:
            victim: tuple[int, bytes] | None = None
            for index, item in enumerate(self._items):
                if isinstance(item, bytes):
                    victim = (index, item)
                    break
            if victim is None:
                return False
            index, item = victim
            # Read the state *before* deleting: the two deques are parallel and
            # the index stops meaning anything the moment either is shortened.
            ends_inside = self._item_inside[index]
            # Delete outside the scan: mutating the deque while enumerating it
            # raises RuntimeError the moment a second pass is needed.
            del self._items[index]
            del self._item_inside[index]
            self._pending_bytes = max(0, self._pending_bytes - len(item))
            self.dropped_bytes += len(item)
            # ``ends_inside`` is the state *after* that frame. A frame that
            # leaves us outside a sequence is a safe cut point.
            if not ends_inside:
                return True


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
        """Make room for one control message by dropping the oldest one.

        Both deques are parallel -- dropping from one and not the other leaves
        their indices meaningless and the next binary eviction reads the wrong
        state (or falls off the end).
        """
        for index, item in enumerate(self._items):
            if isinstance(item, dict):
                del self._items[index]
                del self._item_inside[index]
                self._control_count -= 1
                return

    def _shed_for(self, size: int) -> None:
        """Make room for ``size`` bytes by evicting the oldest terminal data."""
        if self._max_bytes <= 0 and len(self._items) < self._max_pending:
            return
        shed = False
        while (self._max_bytes > 0 and self._pending_bytes + size > self._max_bytes) or (
            len(self._items) >= self._max_pending
        ):
            if not self._drop_oldest_binary():
                break
            shed = True
        if shed:
            self.dropped_events += 1
            self._announce_shed()

    def _announce_shed(self) -> None:
        """Tell the viewer, once per burst, that history was shortened.

        Losing frames is the lesser evil; losing them *silently* is not, which
        is the same rule the input path already follows for dropped keystrokes.
        """
        if self._shed_notice_open:
            return
        self._shed_notice_open = True
        with contextlib.suppress(Exception):
            self._put_control(
                {
                    "type": "notice",
                    "level": "warn",
                    "msg": "输出过快，已省略部分较早内容（会话仍在，连接未断）",
                }
            )

    def _put_control(self, message: dict[str, Any]) -> bool:
        """Queue a control frame, shedding the *oldest control frame* if needed.

        Control frames are exempt from the byte budget -- the one message
        explaining a loss must get through -- but they are not exempt from
        being bounded. An earlier rewrite of this class let them accumulate
        without limit, which is its own way to take a connection down.
        """
        while self._control_count >= MAX_CONTROL_PENDING:
            before = self._control_count
            self._drop_oldest_control()
            if self._control_count == before:
                break
        self._items.append(message)
        self._item_inside.append(False)
        self._control_count += 1
        self._wakeup.set()
        return True

    def put(self, item: bytes | dict[str, Any]) -> None:
        """Queue ``item``; terminal data is shed under pressure, never the link."""
        if self._closed:
            raise ClientGone("连接已关闭")
        if isinstance(item, bytes):
            self._shed_for(len(item))
            if self._max_bytes > 0 and self._pending_bytes + len(item) > self._max_bytes:
                # Nothing left to evict and the frame still does not fit: lose
                # *this* frame rather than the connection.
                self.dropped_bytes += len(item)
                self.dropped_events += 1
                return
            self._pending_bytes += len(item)
            self._items.append(item)
            self._item_inside.append(self._tracker.feed(item))
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
                if not self._items:
                    self._wakeup.clear()
                    if self._closed and not self._items:
                        return
                    await self._wakeup.wait()
                    continue
                item = self._items.popleft()
                self._item_inside.popleft()
                if not self._items and self._shed_notice_open:
                    self._shed_notice_open = False
                if isinstance(item, bytes):
                    self._pending_bytes = max(0, self._pending_bytes - len(item))
                    await self._ws.send_bytes(item)
                else:
                    self._control_count -= 1
                    await self._ws.send_json(item)
                if self._closed and not self._items:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._closed = True


__all__ = ["MAX_CONTROL_PENDING", "MAX_PENDING", "WsClient"]
