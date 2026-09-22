from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wsctl.cli.main import app
from wsctl.core.config import load_settings
from wsctl.core.store import Store

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "wsctl" in result.output


def test_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "wsctl" in result.output


def test_doctor_runs(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    monkeypatch.setenv("WSCTL_CONFIG", str(tmp_path / "missing.toml"))  # type: ignore[attr-defined]
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "Python" in result.output
    assert "数据目录" in result.output


def test_user_disable_enable(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    monkeypatch.setenv("WSCTL_CONFIG", str(tmp_path / "missing.toml"))  # type: ignore[attr-defined]
    store = Store(load_settings().db_path)
    store.user_create("u", "pw")
    store.close()

    assert runner.invoke(app, ["user", "disable", "u"]).exit_code == 0
    store = Store(load_settings().db_path)
    assert store.user_get("u") is not None
    assert store.user_get("u").disabled is True  # type: ignore[union-attr]
    store.close()

    assert runner.invoke(app, ["user", "enable", "u"]).exit_code == 0
    store = Store(load_settings().db_path)
    assert store.user_get("u").disabled is False  # type: ignore[union-attr]
    store.close()

    assert runner.invoke(app, ["user", "disable", "nope"]).exit_code == 1


def test_config_set_creates_and_updates(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    config = tmp_path / "wsctl" / "config.toml"

    first = runner.invoke(app, ["config", "set", "port", "9000"])
    assert first.exit_code == 0, first.output
    assert "port = 9000" in config.read_text()

    second = runner.invoke(app, ["config", "set", "port", "9100"])
    assert second.exit_code == 0, second.output
    text = config.read_text()
    assert text.count("port =") == 1
    assert "port = 9100" in text


def test_config_set_rejects_invalid_toml(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    result = runner.invoke(app, ["config", "set", "host", "not toml !!"])
    assert result.exit_code == 1


def test_config_set_rejects_unknown_key(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    result = runner.invoke(app, ["config", "set", "prot", "8080"])
    assert result.exit_code == 1
    assert "未知的配置项" in result.output
    assert "port" in result.output  # suggests the closest known key


def test_config_set_accepts_typed_values(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    config = tmp_path / "wsctl" / "config.toml"
    assert runner.invoke(app, ["config", "set", "auth_required", "false"]).exit_code == 0
    assert runner.invoke(
        app, ["config", "set", "allowed_ips", '["10.0.0.0/8"]']
    ).exit_code == 0
    text = config.read_text()
    assert "auth_required = false" in text
    assert 'allowed_ips = ["10.0.0.0/8"]' in text
