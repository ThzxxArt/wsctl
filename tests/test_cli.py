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


# -- M29: mutual-exclusion / dependency validation --------------------


def _missing_config(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("WSCTL_CONFIG", str(tmp_path / "missing.toml"))  # type: ignore[attr-defined]


def test_serve_rejects_daemon_with_reuse_port(tmp_path: Path, monkeypatch: object) -> None:
    _missing_config(tmp_path, monkeypatch)
    result = runner.invoke(app, ["serve", "--daemon", "--reuse-port"])
    assert result.exit_code == 2
    assert "--daemon" in result.output


def test_serve_requires_ssl_pair(tmp_path: Path, monkeypatch: object) -> None:
    _missing_config(tmp_path, monkeypatch)
    result = runner.invoke(app, ["serve", "--ssl-cert", str(tmp_path / "c.pem")])
    assert result.exit_code == 2
    assert "ssl-key" in result.output


def test_serve_rejects_ssh_backend(tmp_path: Path, monkeypatch: object) -> None:
    _missing_config(tmp_path, monkeypatch)
    result = runner.invoke(app, ["serve", "--backend", "ssh"])
    assert result.exit_code == 2
    assert "backend" in result.output


def test_serve_rejects_no_auth_with_admin_password(
    tmp_path: Path, monkeypatch: object
) -> None:
    _missing_config(tmp_path, monkeypatch)
    result = runner.invoke(app, ["serve", "--no-auth", "--admin-password", "x"])
    assert result.exit_code == 2


def test_session_new_rejects_ssh_backend_conflict() -> None:
    result = runner.invoke(app, ["session", "new", "--ssh", "h", "--backend", "tmux"])
    assert result.exit_code == 2


def test_session_new_requires_ssh_for_ssh_options() -> None:
    result = runner.invoke(app, ["session", "new", "--ssh-port", "22"])
    assert result.exit_code == 2


def test_config_set_rejects_wrong_type(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    result = runner.invoke(app, ["config", "set", "port", '"abc"'])
    assert result.exit_code == 1
    assert "无效" in result.output
    # A rejected value must not be written.
    assert not (tmp_path / "wsctl" / "config.toml").exists()


def test_user_cli_cannot_lock_out_the_last_admin(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    store = Store(load_settings().db_path)
    store.user_create("root", "pw", role="admin")
    store.close()

    assert runner.invoke(app, ["user", "disable", "root"]).exit_code == 1
    assert runner.invoke(app, ["user", "role", "root", "user"]).exit_code == 1
    assert runner.invoke(app, ["user", "del", "root"]).exit_code == 1

    store = Store(load_settings().db_path)
    try:
        user = store.user_get("root")
        assert user is not None and user.role == "admin" and not user.disabled
    finally:
        store.close()


def test_user_cli_rejects_bad_role(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    assert runner.invoke(app, ["user", "add", "x", "-p", "pw", "--role", "root"]).exit_code == 2


def test_user_passwd_cli_revokes_existing_logins(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    store = Store(load_settings().db_path)
    user = store.user_create("bob", "oldpw")
    token = store.create_auth_session(user.id, ttl=3600)
    store.close()

    monkeypatch.setattr("typer.prompt", lambda *a, **k: "newpw")  # type: ignore[attr-defined]
    assert runner.invoke(app, ["user", "passwd", "bob"]).exit_code == 0

    store = Store(load_settings().db_path)
    try:
        assert store.resolve_auth_session(token) is None  # old token revoked
        assert store.user_authenticate("bob", "newpw") is not None
    finally:
        store.close()


def test_status_reports_not_running(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "未在运行" in result.output


def test_status_json_when_not_running(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 1
    assert '"running": false' in result.output


def test_doctor_reports_new_checks(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "后台启动" in result.output
    assert "SO_REUSEPORT" in result.output
