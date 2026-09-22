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


def test_none_overrides_ignored() -> None:
    settings = load_settings(host=None, port=None)
    assert settings.host == "127.0.0.1"
    assert settings.port == 7681


def test_db_path_within_data_dir(tmp_path: Path) -> None:
    settings = load_settings(data_dir=tmp_path)
    assert settings.db_path == tmp_path / "wsctl.db"


def test_shell_fallback() -> None:
    settings = load_settings(default_shell="/bin/zsh")
    assert settings.shell == "/bin/zsh"


def test_reload_settings_file(tmp_path: Path, monkeypatch: object) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    monkeypatch.setenv("WSCTL_CONFIG", str(config))  # type: ignore[attr-defined]

    settings = load_settings()
    assert settings.max_sessions == 64

    config.parent.mkdir(parents=True)
    config.write_text('max_sessions = 5\nhost = "0.0.0.0"\n', encoding="utf-8")
    changed = reload_settings_file(settings)
    assert "max_sessions" in changed
    assert settings.max_sessions == 5
    # non-hot fields are ignored
    assert "host" not in changed
    assert settings.host == "127.0.0.1"


def test_reload_missing_file_is_noop(tmp_path: Path) -> None:
    settings = load_settings(config_path=tmp_path / "nope.toml")
    assert reload_settings_file(settings) == []


def test_reload_invalid_value_is_noop(tmp_path: Path) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    settings = load_settings(config_path=config)
    config.write_text('max_sessions = "not-an-int"\n', encoding="utf-8")
    assert reload_settings_file(settings) == []
    assert settings.max_sessions == 64


def test_reload_respects_env_precedence(tmp_path: Path, monkeypatch: object) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("max_sessions = 5\n", encoding="utf-8")
    monkeypatch.setenv("WSCTL_MAX_SESSIONS", "99")  # type: ignore[attr-defined]

    settings = load_settings(config_path=config)
    assert settings.max_sessions == 99  # env wins at load time

    config.write_text("max_sessions = 5\n", encoding="utf-8")
    changed = reload_settings_file(settings)
    assert "max_sessions" not in changed
    assert settings.max_sessions == 99  # env still wins after reload


def test_reload_skips_explicitly_empty_env(tmp_path: Path, monkeypatch: object) -> None:
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text('default_shell = "/bin/zsh"\n', encoding="utf-8")
    monkeypatch.setenv("WSCTL_DEFAULT_SHELL", "")  # type: ignore[attr-defined]

    settings = load_settings(config_path=config)
    assert settings.default_shell == ""  # env (empty) wins at load
    changed = reload_settings_file(settings)
    assert "default_shell" not in changed
    assert settings.default_shell == ""
