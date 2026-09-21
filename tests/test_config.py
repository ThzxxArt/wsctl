from __future__ import annotations

from pathlib import Path

from wsctl.core.config import Settings, load_settings


def test_defaults() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.host == "127.0.0.1"
    assert settings.port == 7681
    assert settings.auth_required is True


def test_overrides_win() -> None:
    settings = load_settings(host="0.0.0.0", port=9000)
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


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
