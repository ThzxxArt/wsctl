from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from wsctl.cli.main import app

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "wsctl" in result.output


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
