"""WebSocket close codes: one definition for server, browser and CLI.

Three consumers used to keep their own "do not reconnect" sets, which is how
``4401`` ended up fatal in the CLI and non-fatal in the browser. The browser
cannot import Python, so ``static/app.js`` mirrors the *fatal* set literally and
``tests/test_closecodes.py`` asserts the two agree -- a mirror that is not
checked is just a second place to forget.
"""

from __future__ import annotations

#: The client sent something malformed (first frame was not an ``attach``).
CLOSE_BAD_REQUEST = 4400
#: No valid login (or it was revoked/expired mid-session).
CLOSE_UNAUTHORIZED = 4401
#: Authenticated, but not allowed to touch this session (or the share is gone).
CLOSE_FORBIDDEN = 4403
#: The session does not exist (or is already gone).
CLOSE_NOT_FOUND = 4404
#: Half-open connection: the peer vanished without closing.
CLOSE_TIMEOUT = 4408
#: A quota is exhausted (session count, per-user quota, client count).
CLOSE_LIMIT = 4409
#: Input kept arriving faster than the configured rate limit.
CLOSE_RATE_LIMITED = 4429
#: Output arrived faster than this client could drain it. The *session* is
#: fine -- reconnecting replays the screen -- so this is recoverable, but it
#: must be distinguishable from a clean shutdown: closing with 1000 made the
#: browser think everything was normal, auto-reconnect, clear the terminal and
#: then race a replay it could lose again.
CLOSE_SLOW_CONSUMER = 4410
#: The server failed while serving this connection.
CLOSE_SERVER_ERROR = 4500

ALL_CLOSE_CODES: tuple[int, ...] = (
    CLOSE_BAD_REQUEST,
    CLOSE_UNAUTHORIZED,
    CLOSE_FORBIDDEN,
    CLOSE_NOT_FOUND,
    CLOSE_TIMEOUT,
    CLOSE_LIMIT,
    CLOSE_RATE_LIMITED,
    CLOSE_SERVER_ERROR,
    CLOSE_SLOW_CONSUMER,
)

#: Codes after which retrying cannot help: the failure is permanent and the
#: reason was already delivered as a control message. ``CLOSE_TIMEOUT`` is
#: deliberately absent -- a half-open link is exactly the case where
#: reconnecting and replaying the screen is the right response.
FATAL_CLOSE_CODES = frozenset(
    {
        CLOSE_BAD_REQUEST,
        CLOSE_UNAUTHORIZED,
        CLOSE_FORBIDDEN,
        CLOSE_NOT_FOUND,
        CLOSE_LIMIT,
        CLOSE_RATE_LIMITED,
        CLOSE_SERVER_ERROR,
    }
)
