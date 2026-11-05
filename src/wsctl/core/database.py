"""SQLAlchemy models and repository for 0.2.0 features.

0.1.x persisted everything through a hand-written ``sqlite3`` wrapper. That
worked, but it made schema evolution a rewrite rather than a versioned chain.
0.2.0 introduces a real SQLAlchemy layer for the *new* tables -- shares, API
keys, layouts, command blocks, push subscriptions, recordings -- and Alembic
owns the schema. The legacy tables remain on the proven ``Store``
implementation until their repositories move one by one; both share the same
database. PostgreSQL is a 0.3 milestone; ``Settings`` rejects a non-SQLite URL
so the two kernels can never silently split across databases.

Everything here is synchronous. Callers already run database work on a worker
thread (``asyncio.to_thread``) for exactly the same reason as the legacy store:
SQLite in WAL mode is fast, but it is still blocking I/O and must not stall the
PTY read loop.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Share(Base):
    __tablename__ = "shares"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_shares_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term_session_id: Mapped[str] = mapped_column(String(64), index=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    token_hint: Mapped[str] = mapped_column(String(16), default="")
    scope: Mapped[str] = mapped_column(String(16), default="read")
    label: Mapped[str] = mapped_column(String(80), default="")
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_used: Mapped[float | None] = mapped_column(Float, nullable=True)


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_api_keys_token"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    token_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_used: Mapped[float | None] = mapped_column(Float, nullable=True)


class Layout(Base):
    __tablename__ = "layouts"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_layouts_user_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(80), default="default")
    layout_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[float] = mapped_column(Float, default=0.0)


class CommandBlockRow(Base):
    __tablename__ = "command_blocks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    term_session_id: Mapped[str] = mapped_column(String(64), index=True)
    command: Mapped[str] = mapped_column(Text, default="")
    cwd: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    ended_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_push_endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    keys_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(Float, default=0.0)


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term_session_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    ended_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="recording")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(Text)
    request_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[int] = mapped_column(Integer, default=200)
    body_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[float] = mapped_column(Float, default=0.0)


@dataclass(frozen=True)
class ShareRecord:
    id: int
    term_session_id: str
    scope: str
    label: str
    created_at: float
    expires_at: float | None
    last_used: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session": self.term_session_id,
            "scope": self.scope,
            "label": self.label,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "last_used": self.last_used,
        }


class Database:
    """Thread-safe SQLAlchemy repository for the 0.2.0 tables.

    SQLite is the only supported database in 0.2.0. It gets WAL plus a bounded
    busy timeout, matching the legacy store. ``Settings`` rejects any other
    URL at startup, so this layer never pretends to support a backend the
    auth/session kernel cannot follow.
    """

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = _normalise_url(url)
        kwargs: dict[str, Any] = {"echo": echo, "future": True}
        if self.url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        self.engine: Engine = create_engine(self.url, **kwargs)
        if self.url.startswith("sqlite"):
            with self.engine.begin() as conn:
                from sqlalchemy import text

                conn.execute(text("PRAGMA journal_mode=WAL"))
                conn.execute(text("PRAGMA busy_timeout=5000"))
                conn.execute(text("PRAGMA foreign_keys=ON"))
        deadline = time.monotonic() + 20.0
        while True:
            try:
                Base.metadata.create_all(self.engine)
                break
            except OperationalError as exc:
                message = str(exc).lower()
                if not (
                    "already exists" in message
                    or "locked" in message
                    or "busy" in message
                ):
                    raise
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        self._session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def close(self) -> None:
        self.engine.dispose()

    # -- shares --------------------------------------------------------

    def share_create(
        self,
        term_session_id: str,
        token_hash: str,
        *,
        scope: str = "read",
        label: str = "",
        created_by: int | None = None,
        expires_at: float | None = None,
        token_hint: str = "",
    ) -> ShareRecord:
        now = time.time()
        with self.session() as s:
            row = Share(
                term_session_id=term_session_id,
                token_hash=token_hash,
                token_hint=token_hint,
                scope=scope,
                label=label,
                created_by=created_by,
                created_at=now,
                expires_at=expires_at,
            )
            s.add(row)
            s.flush()
            return _share_record(row)

    def share_list(self, term_session_id: str) -> list[ShareRecord]:
        with self.session() as s:
            rows = s.scalars(
                select(Share)
                .where(Share.term_session_id == term_session_id)
                .order_by(Share.id)
            ).all()
            return [_share_record(row) for row in rows]

    def share_resolve(self, token_hash: str, *, now: float | None = None) -> ShareRecord | None:
        now = time.time() if now is None else now
        with self.session() as s:
            row = s.scalar(select(Share).where(Share.token_hash == token_hash))
            if row is None:
                return None
            if row.expires_at is not None and now >= row.expires_at:
                s.delete(row)
                return None
            # One row-write per request is unnecessary: ``last_used`` is an
            # operator-facing "is this key still in use" signal, and a minute
            # of resolution is plenty. SQLite still has to commit every write,
            # which showed up as a lock contention source under API polling.
            if row.last_used is None or now - row.last_used >= 60:
                row.last_used = now
            return _share_record(row)

    def share_get(self, share_id: int) -> ShareRecord | None:
        with self.session() as s:
            row = s.get(Share, share_id)
            return _share_record(row) if row is not None else None

    def share_revoke(self, share_id: int, *, term_session_id: str | None = None) -> bool:
        with self.session() as s:
            row = s.get(Share, share_id)
            if row is None or (term_session_id and row.term_session_id != term_session_id):
                return False
            s.delete(row)
            return True

    def share_revoke_session(self, term_session_id: str) -> int:
        with self.session() as s:
            result = s.execute(delete(Share).where(Share.term_session_id == term_session_id))
            return int(getattr(result, "rowcount", 0) or 0)

    def share_purge_expired(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self.session() as s:
            result = s.execute(
                delete(Share).where(Share.expires_at.is_not(None), Share.expires_at <= now)
            )
            return int(getattr(result, "rowcount", 0) or 0)

    # -- API keys ------------------------------------------------------

    def api_key_create(
        self,
        user_id: int,
        name: str,
        token_hash: str,
        *,
        scopes: list[str] | None = None,
        expires_at: float | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with self.session() as s:
            row = ApiKey(
                user_id=user_id,
                name=name,
                token_hash=token_hash,
                scopes=" ".join(scopes or []),
                created_at=now,
                expires_at=expires_at,
            )
            s.add(row)
            s.flush()
            return {
                "id": row.id,
                "user_id": row.user_id,
                "name": row.name,
                "scopes": (row.scopes or "").split(),
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "last_used": row.last_used,
            }

    def api_key_resolve(
        self, token_hash: str, *, now: float | None = None
    ) -> dict[str, Any] | None:
        now = time.time() if now is None else now
        with self.session() as s:
            row = s.scalar(select(ApiKey).where(ApiKey.token_hash == token_hash))
            if row is None:
                return None
            if row.expires_at is not None and now >= row.expires_at:
                s.delete(row)
                return None
            if row.last_used is None or now - row.last_used >= 60:
                row.last_used = now
            return {
                "id": row.id,
                "user_id": row.user_id,
                "name": row.name,
                "scopes": (row.scopes or "").split(),
                "created_at": row.created_at,
                "expires_at": row.expires_at,
                "last_used": row.last_used,
            }

    def api_key_list(self, user_id: int | None = None) -> list[dict[str, Any]]:
        stmt = select(ApiKey).order_by(ApiKey.id)
        if user_id is not None:
            stmt = stmt.where(ApiKey.user_id == user_id)
        with self.session() as s:
            return [
                {
                    "id": row.id,
                    "user_id": row.user_id,
                    "name": row.name,
                    "scopes": (row.scopes or "").split(),
                    "created_at": row.created_at,
                    "expires_at": row.expires_at,
                    "last_used": row.last_used,
                }
                for row in s.scalars(stmt).all()
            ]

    def api_key_delete(self, key_id: int, *, user_id: int | None = None) -> bool:
        with self.session() as s:
            row = s.get(ApiKey, key_id)
            if row is None or (user_id is not None and row.user_id != user_id):
                return False
            s.delete(row)
            return True

    # -- layouts -------------------------------------------------------

    def layout_put(self, user_id: int, name: str, layout: dict[str, Any]) -> None:
        now = time.time()
        with self.session() as s:
            row = s.scalar(
                select(Layout).where(Layout.user_id == user_id, Layout.name == name)
            )
            payload = json.dumps(layout, ensure_ascii=False)
            if row is None:
                s.add(
                    Layout(
                        user_id=user_id,
                        name=name,
                        layout_json=payload,
                        updated_at=now,
                    )
                )
            else:
                row.layout_json = payload
                row.updated_at = now

    def layout_get(self, user_id: int, name: str) -> dict[str, Any] | None:
        with self.session() as s:
            row = s.scalar(
                select(Layout).where(Layout.user_id == user_id, Layout.name == name)
            )
            if row is None:
                return None
            try:
                data = json.loads(row.layout_json)
            except json.JSONDecodeError:
                data = {}
            return {"name": row.name, "layout": data, "updated_at": row.updated_at}

    def layout_list(self, user_id: int) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.scalars(
                select(Layout).where(Layout.user_id == user_id).order_by(Layout.name)
            ).all()
            out = []
            for row in rows:
                try:
                    data = json.loads(row.layout_json)
                except json.JSONDecodeError:
                    data = {}
                out.append({"name": row.name, "layout": data, "updated_at": row.updated_at})
            return out

    def layout_delete(self, user_id: int, name: str) -> bool:
        with self.session() as s:
            row = s.scalar(
                select(Layout).where(Layout.user_id == user_id, Layout.name == name)
            )
            if row is None:
                return False
            s.delete(row)
            return True

    # -- command blocks -----------------------------------------------

    def command_block_add(self, block: dict[str, Any]) -> None:
        with self.session() as s:
            s.merge(
                CommandBlockRow(
                    id=str(block["id"]),
                    term_session_id=str(block.get("session") or block.get("term_session_id")),
                    command=str(block.get("command") or ""),
                    cwd=block.get("cwd"),
                    started_at=block.get("started_at"),
                    ended_at=block.get("ended_at"),
                    duration_ms=block.get("duration_ms"),
                    exit_code=block.get("exit_code"),
                    status=str(block.get("status") or "running"),
                )
            )

    def command_blocks_purge(self, before: float) -> int:
        """Drop command history older than the session retention cutoff."""
        from sqlalchemy import delete, func

        with self.session() as s:
            result = s.execute(
                delete(CommandBlockRow).where(
                    func.coalesce(
                        CommandBlockRow.ended_at,
                        CommandBlockRow.started_at,
                        0.0,
                    )
                    < before
                )
            )
            return int(getattr(result, "rowcount", 0) or 0)

    def command_blocks(self, sid: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.scalars(
                select(CommandBlockRow)
                .where(CommandBlockRow.term_session_id == sid)
                .order_by(CommandBlockRow.started_at.desc())
                .limit(max(1, min(int(limit), 1000)))
            ).all()
            return [
                {
                    "id": row.id,
                    "session": row.term_session_id,
                    "command": row.command,
                    "cwd": row.cwd,
                    "started_at": row.started_at,
                    "ended_at": row.ended_at,
                    "duration_ms": row.duration_ms,
                    "exit_code": row.exit_code,
                    "status": row.status,
                }
                for row in rows
            ][::-1]

    # -- push ----------------------------------------------------------

    def push_find(self, endpoint: str) -> dict[str, Any] | None:
        with self.session() as s:
            row = s.scalar(
                select(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
            if row is None:
                return None
            return {"id": row.id, "user_id": row.user_id, "endpoint": row.endpoint}

    def push_subscribe(
        self, user_id: int, endpoint: str, keys: dict[str, Any]
    ) -> dict[str, Any]:
        now = time.time()
        with self.session() as s:
            row = s.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
            payload = json.dumps(keys, ensure_ascii=False)
            if row is None:
                row = PushSubscription(
                    user_id=user_id,
                    endpoint=endpoint,
                    keys_json=payload,
                    created_at=now,
                )
                s.add(row)
            else:
                row.user_id = user_id
                row.keys_json = payload
            s.flush()
            return {"id": row.id, "user_id": row.user_id, "endpoint": row.endpoint}

    def push_unsubscribe(self, endpoint: str, *, user_id: int | None = None) -> bool:
        with self.session() as s:
            row = s.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
            if row is None or (user_id is not None and row.user_id != user_id):
                return False
            s.delete(row)
            return True

    def push_list(self, user_id: int) -> list[dict[str, Any]]:
        with self.session() as s:
            rows = s.scalars(
                select(PushSubscription)
                .where(PushSubscription.user_id == user_id)
                .order_by(PushSubscription.id)
            ).all()
            out = []
            for row in rows:
                try:
                    keys = json.loads(row.keys_json or "{}")
                except json.JSONDecodeError:
                    keys = {}
                if not isinstance(keys, dict):
                    keys = {}
                out.append(
                    {
                        "id": row.id,
                        "user_id": row.user_id,
                        "endpoint": row.endpoint,
                        "keys": keys,
                        "created_at": row.created_at,
                    }
                )
            return out


    # -- recordings -----------------------------------------------------

    def recording_start(
        self,
        term_session_id: str,
        path: str,
        *,
        owner_id: int | None = None,
        started_at: float | None = None,
    ) -> dict[str, Any]:
        now = time.time()
        with self.session() as s:
            row = s.scalar(select(Recording).where(Recording.path == path))
            if row is None:
                row = Recording(
                    term_session_id=term_session_id,
                    owner_id=owner_id,
                    path=path,
                    size=0,
                    started_at=started_at or now,
                    status="recording",
                )
                s.add(row)
            else:
                row.status = "recording"
                row.ended_at = None
            s.flush()
            return _recording_record(row)

    def recording_finish(
        self,
        path: str,
        *,
        size: int = 0,
        status: str = "finished",
        ended_at: float | None = None,
    ) -> bool:
        with self.session() as s:
            row = s.scalar(select(Recording).where(Recording.path == path))
            if row is None:
                return False
            row.size = max(0, int(size))
            row.status = status
            row.ended_at = ended_at or time.time()
            return True

    def recording_reconcile(
        self, directory: Path, *, live_paths: set[str] | None = None
    ) -> list[dict[str, Any]]:
        """Make the table match ``*.cast`` files on disk.

        Recordings were previously only files: there was no way to answer
        "which session produced this cast" or "did it finish cleanly". The
        table is now the index, and a startup/list reconciliation backfills
        casts created by 0.1.x or by an interrupted process.
        """
        live_paths = live_paths or set()
        now = time.time()
        if not directory.is_dir():
            return []
        with self.session() as s:
            existing = {row.path: row for row in s.scalars(select(Recording)).all()}
            for entry in sorted(directory.glob("*.cast")):
                try:
                    stat = entry.stat()
                except OSError:
                    continue
                path = str(entry)
                row = existing.get(path)
                if row is None:
                    row = Recording(
                        term_session_id=entry.stem.split("-")[0],
                        path=path,
                        size=int(stat.st_size),
                        started_at=stat.st_mtime,
                        ended_at=stat.st_mtime,
                        status="recording" if path in live_paths else "finished",
                    )
                    s.add(row)
                    existing[path] = row
                else:
                    row.size = int(stat.st_size)
                    if path not in live_paths and row.status == "recording":
                        row.status = "finished"
                        row.ended_at = row.ended_at or now
            # Retention and manual deletes remove the file first. A row that
            # outlives its cast made the admin library list recordings that
            # could not be downloaded.
            for path, row in list(existing.items()):
                if path in live_paths:
                    continue
                if not Path(path).is_file():
                    s.delete(row)
                    existing.pop(path, None)
            s.flush()
            return [_recording_record(row) for row in existing.values()]

    def recording_delete(self, path: str, *, owner_id: int | None = None) -> bool:
        with self.session() as s:
            row = s.scalar(select(Recording).where(Recording.path == path))
            if row is None:
                return False
            if owner_id is not None and row.owner_id not in (None, owner_id):
                return False
            s.delete(row)
            return True

    # -- idempotency ----------------------------------------------------

    def idempotency_get(
        self,
        key: str,
        *,
        method: str,
        path: str,
        request_hash: str = "",
    ) -> dict[str, Any] | None:
        with self.session() as s:
            row = s.get(IdempotencyKey, key)
            if row is None:
                return None
            if row.method != method or row.path != path:
                return {"conflict": True, "reason": "同一个 Idempotency-Key 被用于不同请求"}
            if request_hash and row.request_hash and row.request_hash != request_hash:
                return {"conflict": True, "reason": "同一个 Idempotency-Key 的请求内容不同"}
            if row.status == 102:
                return {"pending": True}
            try:
                body = json.loads(row.body_json)
            except json.JSONDecodeError:
                body = {}
            return {"status": row.status, "body": body, "created_at": row.created_at}

    def idempotency_claim(
        self,
        key: str,
        *,
        method: str,
        path: str,
        request_hash: str = "",
        now: float | None = None,
        stale_after: float = 120.0,
    ) -> dict[str, Any]:
        """Atomically claim a key before performing the side effect.

        ``replay`` tells the caller whether a key is already finished; this is
        what stops two concurrent requests that both saw "not found" from both
        creating a session. A claim is row ``status=102`` and a second request
        gets ``pending``. A process that dies mid-request leaves a stale claim;
        after ``stale_after`` seconds it may be taken over.
        """
        now = time.time() if now is None else now
        with self.session() as s:
            row = s.get(IdempotencyKey, key)
            if row is None:
                s.add(
                    IdempotencyKey(
                        key=key,
                        method=method,
                        path=path,
                        request_hash=request_hash,
                        status=102,
                        body_json="",
                        created_at=now,
                    )
                )
                try:
                    # Flush inside the transaction instead of letting the
                    # context manager commit at exit. Two requests can both
                    # SELECT "absent" and both INSERT; the loser must become a
                    # clean ``pending`` (409), not a 500 from a primary-key
                    # violation. Correctness of the claim is the whole point of
                    # this method.
                    s.flush()
                except IntegrityError:
                    s.rollback()
                    row = s.get(IdempotencyKey, key)
                    if row is None:  # pragma: no cover - the winner rolled back
                        return {"state": "claimed"}
                else:
                    return {"state": "claimed"}
            if row.method != method or row.path != path:
                return {
                    "state": "conflict",
                    "reason": "同一个 Idempotency-Key 被用于不同请求",
                }
            if request_hash and row.request_hash and row.request_hash != request_hash:
                return {
                    "state": "conflict",
                    "reason": "同一个 Idempotency-Key 的请求内容不同",
                }
            if row.status == 102:
                if now - row.created_at < stale_after:
                    return {"state": "pending"}
                row.created_at = now
                row.request_hash = request_hash
                return {"state": "claimed"}
            try:
                body = json.loads(row.body_json)
            except json.JSONDecodeError:
                body = {}
            return {"state": "done", "status": row.status, "body": body}

    def idempotency_abort(self, key: str) -> None:
        with self.session() as s:
            row = s.get(IdempotencyKey, key)
            if row is not None and row.status == 102:
                s.delete(row)

    def idempotency_put(
        self,
        key: str,
        *,
        method: str,
        path: str,
        status: int,
        body: Any,
        request_hash: str = "",
    ) -> None:
        with self.session() as s:
            row = s.get(IdempotencyKey, key)
            payload = json.dumps(body, ensure_ascii=False, default=str)
            if row is None:
                s.add(
                    IdempotencyKey(
                        key=key,
                        method=method,
                        path=path,
                        request_hash=request_hash,
                        status=status,
                        body_json=payload,
                        created_at=time.time(),
                    )
                )
            else:
                row.status = status
                row.body_json = payload
                row.request_hash = request_hash or row.request_hash

    def idempotency_purge(self, *, older_than: float = 86400) -> int:
        cutoff = time.time() - older_than
        with self.session() as s:
            result = s.execute(
                delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff)
            )
            return int(getattr(result, "rowcount", 0) or 0)


def _recording_record(row: Recording) -> dict[str, Any]:
    return {
        "id": row.id,
        "session": row.term_session_id,
        "owner_id": row.owner_id,
        "path": row.path,
        "name": Path(row.path).name,
        "size": row.size,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "status": row.status,
    }


def _share_record(row: Share) -> ShareRecord:
    return ShareRecord(
        id=row.id,
        term_session_id=row.term_session_id,
        scope=row.scope,
        label=row.label,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used=row.last_used,
    )


def _normalise_url(url: str) -> str:
    if url.startswith("sqlite:///") and not url.startswith("sqlite:////"):
        # Make the path absolute so a server started from another directory
        # cannot silently create a second database.
        path = Path(url[len("sqlite:///") :])
        if not path.is_absolute():
            return "sqlite:///" + str(path.resolve())
    return url


__all__ = [
    "ApiKey",
    "Base",
    "CommandBlockRow",
    "Database",
    "Layout",
    "PushSubscription",
    "Share",
    "ShareRecord",
]
