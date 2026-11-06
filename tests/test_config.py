from __future__ import annotations

from pathlib import Path

from wsctl.core.config import Settings, load_settings, reload_settings_file


def test_defaults(tmp_path: Path, monkeypatch: object) -> None:
    # Hermetic: point at a non-existent config file so a developer's real
    # ~/.config/wsctl/config.toml cannot change the result.
    monkeypatch.setenv("WSCTL_CONFIG", str(tmp_path / "missing.toml"))  # type: ignore[attr-defined]
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.host == "127.0.0.1"
    assert settings.port == 7681
    assert settings.auth_required is True
    # An active terminal must not be logged out at a fixed 12h boundary.
    assert settings.session_sliding_ttl is True


def test_overrides_win() -> None:
    settings = load_settings(host="0.0.0.0", port=9000)
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


def test_new_resource_defaults(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("WSCTL_CONFIG", str(tmp_path / "missing.toml"))  # type: ignore[attr-defined]
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.max_sessions_per_user == 0
    assert settings.instance_ttl == 30
    assert settings.audit_retention_days == 30
    assert settings.term_session_retention_days == 30
    assert settings.recordings_retention_days == 0
    assert settings.recordings_max_bytes == 0
    assert settings.metrics_require_auth is False


def test_none_overrides_ignored(monkeypatch: object) -> None:
    settings = load_settings(host=None, port=None)
    assert settings.host == "127.0.0.1"
    assert settings.port == 7681


def test_db_path_within_data_dir(tmp_path: Path, monkeypatch: object) -> None:
    settings = load_settings(data_dir=tmp_path)
    assert settings.db_path == tmp_path / "wsctl.db"


def test_shell_fallback(monkeypatch: object) -> None:
    settings = load_settings(default_shell="/bin/zsh")
    assert settings.shell == "/bin/zsh"


def test_reload_settings_file(tmp_path: Path, monkeypatch: object) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    monkeypatch.setenv("WSCTL_CONFIG", str(config))  # type: ignore[attr-defined]

    settings = load_settings()
    assert settings.max_sessions == 64

    config.parent.mkdir(parents=True)
    config.write_text('max_sessions = 5\nhost = "0.0.0.0"\n', encoding="utf-8")
    changed, errors = reload_settings_file(settings)
    assert errors == []
    assert "max_sessions" in changed
    assert settings.max_sessions == 5
    # non-hot fields are ignored
    assert "host" not in changed
    assert settings.host == "127.0.0.1"


def test_reload_missing_file_is_noop(tmp_path: Path, monkeypatch: object) -> None:
    settings = load_settings(config_path=tmp_path / "nope.toml")
    assert reload_settings_file(settings) == ([], [])


def test_reload_invalid_value_reports_an_error(
    tmp_path: Path, monkeypatch: object
) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    settings = load_settings(config_path=config)
    config.write_text('max_sessions = "not-an-int"\n', encoding="utf-8")
    changed, errors = reload_settings_file(settings)
    assert changed == []
    assert settings.max_sessions == 64  # the running server keeps its value
    assert errors and ("not-an-int" in errors[0] or "无效" in errors[0])


def test_reload_broken_toml_reports_an_error(
    tmp_path: Path, monkeypatch: object
) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    settings = load_settings(config_path=config)
    config.write_text("max_sessions = [unclosed\n", encoding="utf-8")
    changed, errors = reload_settings_file(settings)
    assert changed == []
    assert errors and "TOML" in errors[0]


def test_reload_respects_env_precedence(tmp_path: Path, monkeypatch: object) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("max_sessions = 5\n", encoding="utf-8")
    monkeypatch.setenv("WSCTL_MAX_SESSIONS", "99")  # type: ignore[attr-defined]

    settings = load_settings(config_path=config)
    assert settings.max_sessions == 99  # env wins at load time

    config.write_text("max_sessions = 5\n", encoding="utf-8")
    changed, errors = reload_settings_file(settings)
    assert errors == []
    assert "max_sessions" not in changed
    assert settings.max_sessions == 99  # env still wins after reload


def test_reload_skips_explicitly_empty_env(tmp_path: Path, monkeypatch: object) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('default_shell = "/bin/zsh"\n', encoding="utf-8")
    monkeypatch.setenv("WSCTL_DEFAULT_SHELL", "")  # type: ignore[attr-defined]

    settings = load_settings(config_path=config)
    assert settings.default_shell == ""  # env (empty) wins at load
    changed, errors = reload_settings_file(settings)
    assert errors == []
    assert "default_shell" not in changed
    assert settings.default_shell == ""


def test_every_setting_is_classified_exactly_once() -> None:
    """A new setting must not be able to sneak in unlabelled.

    "热覆盖" used to swallow eight settings that only affect sessions created
    *after* the edit. The three tiers are the fix; this test is what keeps a
    later addition from quietly landing in the wrong one.
    """
    from wsctl.core.config import (
        HOT_IMMEDIATE,
        HOT_NEW_OBJECTS,
        RESTART_FIELDS,
        Settings,
        kind_of,
    )

    declared = HOT_IMMEDIATE | HOT_NEW_OBJECTS | RESTART_FIELDS
    fields = set(Settings.model_fields)
    assert declared == fields, (
        f"unclassified: {sorted(fields - declared)}; "
        f"stale: {sorted(declared - fields)}"
    )
    # No overlaps: a setting cannot be both "immediate" and "new objects".
    assert not (HOT_IMMEDIATE & HOT_NEW_OBJECTS)
    assert not (HOT_IMMEDIATE & RESTART_FIELDS)
    assert not (HOT_NEW_OBJECTS & RESTART_FIELDS)
    for key in HOT_IMMEDIATE:
        assert kind_of(key) == "hot", key
    for key in HOT_NEW_OBJECTS:
        assert kind_of(key) == "new", key
    for key in RESTART_FIELDS:
        assert kind_of(key) == "restart", key


def test_new_objects_tier_is_not_advertised_as_immediate() -> None:
    """The settings that only reach new sessions must say so.

    These eight were the substance of the defect: raising
    ``session_memory_limit`` was labelled 热更新 while existing sessions kept
    their old cap, so the user believed the change had landed.
    """
    from wsctl.core.config import HOT_IMMEDIATE, HOT_NEW_OBJECTS, kind_of

    for key in (
        "scrollback_bytes",
        "session_memory_limit",
        "session_max_clients",
        "idle_timeout",
        "max_life",
        "input_rate_limit",
        "input_rate_burst",
        "audit_input",
    ):
        assert key in HOT_NEW_OBJECTS, key
        assert key not in HOT_IMMEDIATE, key
        assert kind_of(key) == "new", key


def test_reload_removing_a_key_restores_its_default(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Deleting a line must mean "default", not "keep the running value".

    The old code skipped every key absent from the file. Removing
    ``trust_proxy = true`` therefore left X-Forwarded-For trusted while the
    reload reported no changes -- a fail-open silence on a security switch.
    """
    config = tmp_path / "config.toml"
    config.write_text("trust_proxy = true\n", encoding="utf-8")
    monkeypatch.setenv("WSCTL_CONFIG", str(config))  # type: ignore[attr-defined]
    settings = load_settings()
    assert settings.trust_proxy is True

    config.write_text("", encoding="utf-8")
    changed, errors = reload_settings_file(settings)
    assert errors == []
    assert "trust_proxy" in changed
    assert settings.trust_proxy is False


def test_a_malformed_trusted_proxy_is_refused_at_load() -> None:
    """A typo must not silently stop trusting the proxy.

    ``10.0.0.0/33`` used to be dropped by the header parser: the proxy's own
    address became every client's rate-limit key, and ``config validate`` said
    the file was fine.
    """
    import pytest

    with pytest.raises(ValueError, match="trusted_proxies"):
        Settings(trusted_proxies=["10.0.0.0/33"])


def test_negative_connection_limits_are_refused() -> None:
    """A negative cap is a typo; treating it as "unlimited" hid the mistake."""
    import pytest

    with pytest.raises(ValueError):
        Settings(ws_max_connections=-1)
    with pytest.raises(ValueError):
        Settings(ws_max_connections_per_ip=-1)
    with pytest.raises(ValueError):
        Settings(login_rate_limit=-1)
    # 0 still means "unlimited/off"; that is a documented choice.
    assert Settings(ws_max_connections=0).ws_max_connections == 0
    assert Settings(login_rate_limit=0).login_rate_limit == 0


def test_reload_preserves_explicit_cli_overrides(tmp_path: Path, monkeypatch: object) -> None:
    """``--no-auth`` must survive a config reload from a file that omits it."""
    config = tmp_path / "config.toml"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("WSCTL_CONFIG", str(config))  # type: ignore[attr-defined]
    settings = load_settings(auth_required=False)
    assert settings.auth_required is False
    changed, errors = reload_settings_file(settings)
    assert errors == []
    assert "auth_required" not in changed
    assert settings.auth_required is False
