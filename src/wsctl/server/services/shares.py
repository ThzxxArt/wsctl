"""Multi-link sharing with scopes, persisted across restarts.

0.1.x had one share token per session, stored in a column, and the only scopes
were "read" and "write". 0.2.0 moves links into their own table so a session
can have several, each with a label, expiry and scope (``read`` / ``write`` /
``control``).

Only a hash of the token is stored. The plaintext is returned exactly once, at
creation time -- which is what a bearer credential should look like on disk.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from wsctl.core.database import Database, ShareRecord

SCOPES = ("read", "write", "control")
#: How long a resolved token is trusted without a database hit. The token is
#: validated on every broadcast (in a synchronous filter), so this cache is
#: what keeps a share viewer from turning each PTY read into a SQLite query.
#: Revocation invalidates locally *immediately*; a revocation performed by a
#: different process is seen by the 5s re-validation loop.
CACHE_TTL = 5.0
#: How long a revoked token is remembered as *definitely* invalid. The
#: broadcast filter uses this to drop viewers immediately on a local revoke,
#: while a cache miss (never seen, evicted, or cold after startup) stays
#: "unknown" and is resolved by the connection's periodic re-check instead.
REVOKED_TTL = 60.0


@dataclass
class _CacheEntry:
    record: ShareRecord
    expires: float


class ShareService:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: dict[str, _CacheEntry] = {}
        #: Plaintext token -> wall-clock deadline. Revocation removes the cache
        #: entry; without a tombstone that is indistinguishable from "never
        #: resolved", and the broadcast filter must not guess.
        self._revoked: dict[str, float] = {}

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()

    def create(
        self,
        term_session_id: str,
        *,
        scope: str = "read",
        label: str = "",
        ttl: float | None = None,
        created_by: int | None = None,
    ) -> tuple[str, ShareRecord]:
        if scope not in SCOPES:
            raise ValueError(f"invalid share scope: {scope}")
        token = "wss_" + secrets.token_urlsafe(32)
        digest = self.hash_token(token)
        record = self._db.share_create(
            term_session_id,
            digest,
            scope=scope,
            label=label[:80],
            created_by=created_by,
            expires_at=time.time() + ttl if ttl else None,
            token_hint=token[:8],
        )
        self._cache[token] = _CacheEntry(record, time.time() + CACHE_TTL)
        return token, record

    def resolve(self, token: str) -> ShareRecord | None:
        """Resolve a token, using the cache when it is warm."""
        revoked_until = self._revoked.get(token)
        if revoked_until is not None:
            if time.time() < revoked_until:
                return None
            self._revoked.pop(token, None)
        entry = self._cache.get(token)
        now = time.time()
        if entry is not None and entry.expires > now:
            cached = entry.record
            if cached.expires_at is None or now < cached.expires_at:
                return cached
            self._cache.pop(token, None)
            return None
        record = self._db.share_resolve(self.hash_token(token))
        if record is None:
            self._cache.pop(token, None)
            return None
        if len(self._cache) >= 4096:
            # The cache is an optimisation, never the source of truth. A
            # process that has resolved thousands of share tokens (each one
            # unique) must not grow this dict forever; clearing it only costs
            # one database read per still-active viewer.
            self._cache.clear()
        self._cache[token] = _CacheEntry(record, now + CACHE_TTL)
        return record

    def scope(self, token: str, *, session_id: str | None = None) -> str | None:
        record = self.resolve(token)
        if record is None:
            return None
        if session_id is not None and record.term_session_id != session_id:
            return None
        return record.scope

    def cached_scope(self, token: str, *, session_id: str) -> str | None:
        """Scope lookup safe for the synchronous broadcast filter.

        Never touches the database. A miss returns ``None``; the connection's
        periodic re-check resolves from the database on the event loop and
        refreshes the cache. The cache TTL is deliberately **not** applied
        here: an expired entry still carries the last known answer, and
        treating that as "revoked" detached every share viewer the moment the
        entry aged out.
        """
        if token in self._revoked:
            return None
        entry = self._cache.get(token)
        if entry is None:
            return None
        now = time.time()
        record = entry.record
        if record.term_session_id != session_id:
            return None
        if record.expires_at is not None and now >= record.expires_at:
            return None
        return record.scope

    def known(self, session_id: str) -> Callable[[str], bool]:
        """Whether a token is present in (or explicitly revoked from) the cache.

        The broadcast filter needs this to distinguish "we have never seen this
        token" (keep the viewer; the pump will resolve it) from "we know it is
        gone" (drop it now).
        """

        def is_known(token: str) -> bool:
            if token in self._revoked:
                return True
            entry = self._cache.get(token)
            return entry is not None and entry.record.term_session_id == session_id

        return is_known

    def resolver(self, session_id: str) -> Callable[[str], str | None]:
        def resolve(token: str) -> str | None:
            return self.cached_scope(token, session_id=session_id)

        return resolve

    def list(self, term_session_id: str) -> list[dict[str, Any]]:
        now = time.time()
        return [
            record.to_dict()
            for record in self._db.share_list(term_session_id)
            if record.expires_at is None or record.expires_at > now
        ]

    def revoke(self, share_id: int, *, term_session_id: str | None = None) -> bool:
        # Drop every cached token belonging to this record. The cache is keyed
        # by plaintext, so find by record id. A tombstone makes the revocation
        # visible to the synchronous broadcast filter on the very next chunk.
        deadline = time.time() + REVOKED_TTL
        for token, entry in list(self._cache.items()):
            if entry.record.id == share_id:
                self._cache.pop(token, None)
                self._revoked[token] = deadline
        return self._db.share_revoke(share_id, term_session_id=term_session_id)

    def revoke_session(self, term_session_id: str) -> int:
        deadline = time.time() + REVOKED_TTL
        for token, entry in list(self._cache.items()):
            if entry.record.term_session_id == term_session_id:
                self._cache.pop(token, None)
                self._revoked[token] = deadline
        return self._db.share_revoke_session(term_session_id)

    def purge_expired(self) -> int:
        now = time.time()
        for token, entry in list(self._cache.items()):
            if entry.record.expires_at is not None and now >= entry.record.expires_at:
                self._cache.pop(token, None)
        for token, deadline in list(self._revoked.items()):
            if now >= deadline:
                self._revoked.pop(token, None)
        return self._db.share_purge_expired(now)


__all__ = ["SCOPES", "ShareService"]
