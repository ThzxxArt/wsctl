from __future__ import annotations

import re
from pathlib import Path

from py_vapid import Vapid
from typer.testing import CliRunner

from wsctl.cli.main import app
from wsctl.core.migrations import current_revision

runner = CliRunner()


def _invoke(args: list[str], tmp_path: Path):
    return runner.invoke(
        app,
        args,
        env={
            "WSCTL_DATA_DIR": str(tmp_path / "data"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
        },
    )


def test_config_migrate_converts_flat_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("port = 9000\n# keep me\nsession_ttl = 60\n", encoding="utf-8")
    result = _invoke(["config", "migrate", "--config", str(path)], tmp_path)
    assert result.exit_code == 0, result.output
    migrated = path.read_text(encoding="utf-8")
    assert "[server]" in migrated
    assert "[auth]" in migrated
    assert "# keep me" in migrated
    assert (tmp_path / "config.toml.v1.bak").is_file()


def test_config_validate_accepts_sectioned_and_rejects_broken(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[server]\nport = 9000\n', encoding="utf-8")
    ok = _invoke(["config", "validate", "--config", str(path)], tmp_path)
    assert ok.exit_code == 0, ok.output
    path.write_text('[server]\nport = "not-a-port"\n', encoding="utf-8")
    bad = _invoke(["config", "validate", "--config", str(path)], tmp_path)
    assert bad.exit_code == 1


def test_migrate_check_and_apply(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('[server]\nport = 0\n', encoding="utf-8")
    check = _invoke(["migrate", "--check", "--config", str(config)], tmp_path)
    assert check.exit_code == 0, check.output
    assert "0004_session_exit_code" in check.output
    applied = _invoke(["migrate", "--apply", "--config", str(config)], tmp_path)
    assert applied.exit_code == 0, applied.output
    url = f"sqlite:///{tmp_path / 'data' / 'wsctl.db'}"
    assert current_revision(url) == "0004_session_exit_code"


def test_shell_integration_show_and_install(tmp_path: Path) -> None:
    shown = _invoke(["shell-integration", "show", "bash"], tmp_path)
    assert shown.exit_code == 0
    assert "133" in shown.output
    installed = _invoke(["shell-integration", "install", "bash"], tmp_path)
    assert installed.exit_code == 0
    target = tmp_path / "config" / "wsctl" / "shell-integration" / "wsctl.sh"
    assert target.is_file()
    assert "133" in target.read_text(encoding="utf-8")


def test_shell_integration_rejects_unknown_shell(tmp_path: Path) -> None:
    result = _invoke(["shell-integration", "show", "tcsh"], tmp_path)
    assert result.exit_code == 1
    assert "不支持" in result.output


def test_push_keys_generates_a_usable_pair(tmp_path: Path) -> None:
    result = _invoke(["push", "keys"], tmp_path)
    assert result.exit_code == 0, result.output
    assert "web_push_enabled = true" in result.output
    assert "web_push_vapid_public" in result.output
    assert "web_push_vapid_private" in result.output
    private = re.search(r'web_push_vapid_private = "([^"]+)"', result.output)
    assert private is not None
    Vapid.from_string(private.group(1))
