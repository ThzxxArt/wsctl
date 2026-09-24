from __future__ import annotations

from pathlib import Path

import pytest

from wsctl.core.store import Store


def make_store(tmp_path: Path) -> Store:
    return Store(tmp_path / "test.db")


def test_user_lifecycle(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        assert store.user_count() == 0
        user = store.user_create("alice", "s3cret12", role="admin")
        assert user.username == "alice"
        assert store.user_count() == 1
        assert store.user_get("alice") is not None
        assert store.user_set_password("alice", "newpass123")
        assert store.user_set_role("alice", "user")
        assert store.user_get("alice") is not None
        assert store.user_get("alice").role == "user"  # type: ignore[union-attr]
        assert store.user_delete("alice")
        assert store.user_count() == 0
    finally:
        store.close()


def test_authenticate(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("bob", "hunter22")
        assert store.user_authenticate("bob", "hunter22") is not None
        assert store.user_authenticate("bob", "wrong") is None
        assert store.user_authenticate("nobody", "hunter22") is None
    finally:
        store.close()


def test_duplicate_username(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("carol", "password123")
        import sqlite3

        import pytest

        with pytest.raises(sqlite3.IntegrityError):
            store.user_create("carol", "password123")
    finally:
        store.close()


def test_auth_session_lifecycle(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        user = store.user_create("dave", "password123")
        token = store.create_auth_session(user.id, ttl=3600)
        resolved = store.resolve_auth_session(token)
        assert resolved is not None
        assert resolved.id == user.id
        store.delete_auth_session(token)
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_expired_session(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        user = store.user_create("erin", "password123")
        token = store.create_auth_session(user.id, ttl=-1)
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_disabling_a_user_invalidates_sessions(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        user = store.user_create("gina", "password123")
        token = store.create_auth_session(user.id, ttl=3600)
        assert store.resolve_auth_session(token) is not None
        assert store.user_set_disabled("gina", True)
        assert store.resolve_auth_session(token) is None
        assert store.user_authenticate("gina", "password123") is None
    finally:
        store.close()


def test_audit_roundtrip(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.log_event("login", payload="ok")
        rows = store.recent_audit()
        assert rows and rows[0]["event"] == "login"
    finally:
        store.close()


def test_totp_secret_lifecycle(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("frank", "password123")
        assert store.user_totp_secret("frank") is None
        assert store.user_set_totp("frank", "ABCDEF")
        assert store.user_totp_secret("frank") == "ABCDEF"
        assert store.user_clear_totp("frank")
        assert store.user_totp_secret("frank") is None
        assert not store.user_set_totp("nobody", "X")
    finally:
        store.close()


def test_settings_table(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        assert store.setting_get("k") is None
        assert store.setting_get("k", "default") == "default"
        store.setting_set("k", "v1")
        store.setting_set("k", "v2")
        assert store.setting_get("k") == "v2"
        assert store.setting_all() == {"k": "v2"}
    finally:
        store.close()


def test_term_session_full_fields(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.term_session_upsert(
            "s1",
            name="shell",
            owner_id=None,
            backend="local",
            command="bash",
            argv=["bash", "-l"],
            env={"TERM": "xterm"},
            cwd="/tmp",
            idle_timeout=60.0,
            max_life=3600.0,
        )
        rows = store.term_session_list()
        assert len(rows) == 1
        row = rows[0]
        assert row["argv"] == '["bash", "-l"]'
        assert row["env"] == '{"TERM": "xterm"}'
        assert row["idle_timeout"] == 60.0
        assert row["max_life"] == 3600.0
    finally:
        store.close()


def test_last_seen_write_is_throttled(tmp_path: Path) -> None:
    import sqlite3

    store = make_store(tmp_path)
    try:
        user = store.user_create("throttle", "password123")
        token = store.create_auth_session(user.id, ttl=3600)

        def last_seen() -> float:
            conn = sqlite3.connect(tmp_path / "test.db")
            try:
                return float(conn.execute("SELECT last_seen FROM auth_sessions").fetchone()[0])
            finally:
                conn.close()

        store.resolve_auth_session(token)  # fresh: within throttle, no write
        first = last_seen()
        store.resolve_auth_session(token)
        assert last_seen() == first
        # Age the row past the throttle; the next resolve must refresh it.
        conn = sqlite3.connect(tmp_path / "test.db")
        conn.execute("UPDATE auth_sessions SET last_seen = 0")
        conn.commit()
        conn.close()
        store.resolve_auth_session(token)
        assert last_seen() > 0
    finally:
        store.close()


def test_sliding_ttl_extends_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A renewal slides the deadline -- without ever widening the window.

    The window is ``expires_at - created_at``. Pushing only ``expires_at``
    forward made every renewal widen the window itself, so ``session_ttl``
    silently stopped meaning "at most N seconds". The compounding only shows
    when *time passes* between renewals -- three renewals inside the same
    millisecond cannot tell the difference, which is exactly why the first
    version of this test went green against the buggy code. The clock is
    therefore stepped 80s per round against a 100s ttl: the buggy formula
    yields 100 -> 180 -> 340, the correct one stays at 100.
    """
    import sqlite3

    import wsctl.core.store as store_mod

    real_time = store_mod.time
    clock = {"now": 1_000_000.0}

    class TimeShim:
        def time(self) -> float:
            return clock["now"]

        def __getattr__(self, name: str) -> object:
            return getattr(real_time, name)

    monkeypatch.setattr(store_mod, "time", TimeShim())

    store = make_store(tmp_path)
    try:
        store.sliding_ttl = True
        user = store.user_create("slider", "password123")
        token = store.create_auth_session(user.id, ttl=100)
        window = None
        for round_no in range(3):
            clock["now"] += 80.0
            assert store.resolve_auth_session(token) is not None, f"round {round_no}"
            conn = sqlite3.connect(tmp_path / "test.db")
            try:
                created, expires = conn.execute(
                    "SELECT created_at, expires_at FROM auth_sessions"
                ).fetchone()
            finally:
                conn.close()
            now_window = float(expires) - float(created)
            assert float(expires) > clock["now"] + 90, f"round {round_no} did not extend"
            if window is None:
                window = now_window
            else:
                assert abs(now_window - window) < 1.0, (
                    f"round {round_no}: the window itself grew "
                    f"{window:.1f}s -> {now_window:.1f}s -- session_ttl is being "
                    "compounded away"
                )
    finally:
        store.close()


def test_delete_user_sessions_and_admin_count(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.user_create("root", "password123", role="admin")
        user = store.user_create("u", "password123")
        assert store.admin_count() == 1
        store.user_set_disabled("root", True)
        assert store.admin_count() == 0
        t1 = store.create_auth_session(user.id, ttl=3600)
        t2 = store.create_auth_session(user.id, ttl=3600)
        assert store.delete_user_sessions(user.id) == 2
        assert store.resolve_auth_session(t1) is None
        assert store.resolve_auth_session(t2) is None
    finally:
        store.close()


# -- password policy at the storage boundary -------------------------------


def test_store_enforces_the_password_policy(tmp_path: Path) -> None:
    import pytest

    from wsctl.core.passwords import PasswordPolicyError

    store = make_store(tmp_path)
    try:
        with pytest.raises(PasswordPolicyError):
            store.user_create("a", "")
        with pytest.raises(PasswordPolicyError):
            store.user_create("a", "short")
        with pytest.raises(PasswordPolicyError):
            store.user_set_password("a", "short")
    finally:
        store.close()


# -- share persistence ----------------------------------------------------


def test_share_round_trips_through_the_database(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.term_session_upsert("s1", name="n", owner_id=None, backend="tmux")
        store.term_session_set_share(
            "s1", token="tok", expires=4102444800.0, writable=True
        )
        row = store.term_session_list()[0]
        assert row["share_token"] == "tok"
        assert row["share_writable"] == 1
        assert row["share_expires"] == 4102444800.0

        store.term_session_set_share("s1", token=None)
        row = store.term_session_list()[0]
        assert row["share_token"] is None
        assert row["share_writable"] == 0
    finally:
        store.close()


def test_clear_expired_shares(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        store.term_session_upsert("old", name="a", owner_id=None)
        store.term_session_upsert("new", name="b", owner_id=None)
        store.term_session_set_share("old", token="t1", expires=1.0, writable=False)
        store.term_session_set_share("new", token="t2", expires=4102444800.0, writable=True)
        assert store.term_session_clear_expired_shares(now=1000.0) == 1
        rows = {str(r["id"]): r for r in store.term_session_list()}
        assert rows["old"]["share_token"] is None
        assert rows["new"]["share_token"] == "t2"
    finally:
        store.close()


def test_upsert_does_not_clobber_an_existing_share(tmp_path: Path) -> None:
    """Renaming a session must not silently invalidate its share link."""
    store = make_store(tmp_path)
    try:
        store.term_session_upsert("s1", name="n", owner_id=None)
        store.term_session_set_share("s1", token="keepme", expires=None, writable=False)
        store.term_session_upsert("s1", name="renamed", owner_id=None, instance_id="A")
        row = store.term_session_list()[0]
        assert row["name"] == "renamed"
        assert row["share_token"] == "keepme"
    finally:
        store.close()


# -- auth cache integration ------------------------------------------------


def test_auth_cache_hides_the_database_but_revokes_immediately(tmp_path: Path) -> None:
    from wsctl.core.authcache import AuthCache

    store = make_store(tmp_path)
    store.set_auth_cache(AuthCache(ttl=30.0))
    try:
        user = store.user_create("cached", "password123")
        token = store.create_auth_session(user.id, ttl=3600)

        assert store.resolve_auth_session(token) is not None
        # A second lookup must come from the cache (same object identity is not
        # guaranteed, so assert on the observable outcome).
        assert store.resolve_auth_session(token).username == "cached"  # type: ignore[union-attr]

        # Disabling the user drops the cache entry at once: the very next
        # resolve sees the change even though the TTL has not elapsed.
        store.user_set_disabled("cached", True)
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_auth_cache_drops_on_password_and_role_change(tmp_path: Path) -> None:
    from wsctl.core.authcache import AuthCache

    store = make_store(tmp_path)
    store.set_auth_cache(AuthCache(ttl=30.0))
    try:
        user = store.user_create("u", "password123")
        token = store.create_auth_session(user.id, ttl=3600)
        assert store.resolve_auth_session(token).role == "user"  # type: ignore[union-attr]

        store.user_set_role("u", "admin")
        assert store.resolve_auth_session(token).role == "admin"  # type: ignore[union-attr]

        store.user_set_password("u", "password456")
        store.delete_user_sessions(user.id)  # what user_admin does on a reset
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_delete_auth_session_drops_the_cache_entry(tmp_path: Path) -> None:
    from wsctl.core.authcache import AuthCache

    store = make_store(tmp_path)
    store.set_auth_cache(AuthCache(ttl=30.0))
    try:
        user = store.user_create("u", "password123")
        token = store.create_auth_session(user.id, ttl=3600)
        assert store.resolve_auth_session(token) is not None
        store.delete_auth_session(token)
        assert store.resolve_auth_session(token) is None
    finally:
        store.close()


def test_schema_migration_is_safe_under_two_concurrent_instances(tmp_path) -> None:
    """Two stores on one data directory must both open.

    This is the documented multi-instance mode (SO_REUSEPORT handover shares a
    data directory), and the migration used `PRAGMA table_info` then `ALTER
    TABLE`. Two processes starting together both saw "the column is absent"
    and both tried to add it -- the loser failed the entire open with
    `duplicate column name`. Adding `end_reason` is what finally made the race
    reproducible.
    """
    import threading

    from wsctl.core.store import Store

    db = tmp_path / "wsctl.db"
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def open_one() -> None:
        try:
            barrier.wait(timeout=20)
            store = Store(db)
            store.close()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=open_one) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=40)
    assert not errors, f"concurrent open failed: {errors}"

    # And the column is there exactly once.
    import sqlite3

    conn = sqlite3.connect(db)
    try:
        names = [r[1] for r in conn.execute("PRAGMA table_info(term_sessions)").fetchall()]
    finally:
        conn.close()
    assert names.count("end_reason") == 1
