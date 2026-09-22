from __future__ import annotations

import json
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
    store.user_create("u", "password123")
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
    store.user_create("root", "password123", role="admin")
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
    result = runner.invoke(
        app, ["user", "add", "x", "-p", "password123", "--role", "root"]
    )
    assert result.exit_code == 2


def test_user_passwd_cli_revokes_existing_logins(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    store = Store(load_settings().db_path)
    user = store.user_create("bob", "oldpw123")
    token = store.create_auth_session(user.id, ttl=3600)
    store.close()

    monkeypatch.setattr("typer.prompt", lambda *a, **k: "newpw1234")  # type: ignore[attr-defined]
    assert runner.invoke(app, ["user", "passwd", "bob"]).exit_code == 0

    store = Store(load_settings().db_path)
    try:
        assert store.resolve_auth_session(token) is None  # old token revoked
        assert store.user_authenticate("bob", "newpw1234") is not None
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
    assert "磁盘剩余空间" in result.output
    assert "密码策略" in result.output
    assert "系统时间" in result.output


# -- 0.1.4: TOTP login, --json, comment-preserving config set -------------


def _fake_api_client(payload: object) -> object:
    class _Stub:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def request(self, method: str, path: str, *args: object, **kwargs: object) -> object:
            self.calls.append((method, path))
            return payload

        def download(self, path: str) -> bytes:  # pragma: no cover - unused
            return b""

    return _Stub()


def test_login_passes_the_totp_code_through(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    monkeypatch.setenv("WSCTL_TOTP", "")  # type: ignore[attr-defined]
    seen: dict[str, object] = {}

    def fake_login(url: str, username: str, password: str, *, totp: str | None = None) -> str:
        seen.update(url=url, username=username, password=password, totp=totp)
        return "tok-123"

    monkeypatch.setattr("wsctl.cli.client.login", fake_login)  # type: ignore[attr-defined]
    result = runner.invoke(
        app,
        ["login", "http://127.0.0.1:9", "-u", "alice", "-p", "password123", "--totp", "123456"],
    )
    assert result.exit_code == 0, result.output
    assert seen["totp"] == "123456"
    assert seen["username"] == "alice"


def test_login_prompts_for_totp_when_the_server_asks(
    tmp_path: Path, monkeypatch: object
) -> None:
    """A 2FA account rejects a missing code; the CLI must recover, not fail."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    monkeypatch.delenv("WSCTL_TOTP", raising=False)  # type: ignore[attr-defined]
    attempts: list[str | None] = []

    def fake_login(url: str, username: str, password: str, *, totp: str | None = None) -> str:
        attempts.append(totp)
        if totp is None:
            from wsctl.cli.client import ApiError

            raise ApiError(401, "一次性验证码错误")
        return "tok-456"

    monkeypatch.setattr("wsctl.cli.client.login", fake_login)  # type: ignore[attr-defined]
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "654321")  # type: ignore[attr-defined]
    result = runner.invoke(
        app, ["login", "http://127.0.0.1:9", "-u", "alice", "-p", "password123"]
    )
    assert result.exit_code == 0, result.output
    assert attempts == [None, "654321"]


def test_login_env_var_supplies_the_totp(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    monkeypatch.setenv("WSCTL_TOTP", "111222")  # type: ignore[attr-defined]
    seen: dict[str, object] = {}

    def fake_login(url: str, username: str, password: str, *, totp: str | None = None) -> str:
        seen["totp"] = totp
        return "tok"

    monkeypatch.setattr("wsctl.cli.client.login", fake_login)  # type: ignore[attr-defined]
    result = runner.invoke(app, ["login", "http://127.0.0.1:9", "-p", "password123"])
    assert result.exit_code == 0, result.output
    assert seen["totp"] == "111222"


def test_session_list_json_is_machine_readable(monkeypatch: object) -> None:
    rows = [{"id": "abc", "name": "build", "pid": 1, "clients": 0,
             "owner": "alice", "backend": "tmux", "alive": True}]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "wsctl.cli.client.ApiClient", lambda *a, **k: _fake_api_client(rows)
    )
    result = runner.invoke(app, ["session", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload[0]["owner"] == "alice"
    assert payload[0]["backend"] == "tmux"


def test_user_list_json(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    store = Store(load_settings().db_path)
    store.user_create("zoe", "password123")
    store.close()
    result = runner.invoke(app, ["user", "list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert {u["username"] for u in payload} == {"zoe"}


def test_config_show_json(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    result = runner.invoke(app, ["config", "show", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["values"]["port"] == 7681


def test_config_set_preserves_the_inline_comment(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '# wsctl 配置\nport = 7681  # 监听端口，反向代理请保持不变\nhost = "127.0.0.1"\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["config", "set", "port", "9000"])
    assert result.exit_code == 0, result.output
    text = config.read_text(encoding="utf-8")
    assert "port = 9000  # 监听端口，反向代理请保持不变" in text
    assert "# wsctl 配置" in text
    assert 'host = "127.0.0.1"' in text


def test_config_set_ignores_commented_out_keys(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    config = tmp_path / "wsctl" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("# port = 1234\n", encoding="utf-8")
    result = runner.invoke(app, ["config", "set", "port", "9000"])
    assert result.exit_code == 0, result.output
    text = config.read_text(encoding="utf-8")
    assert "# port = 1234" in text  # the comment stays commented
    assert "port = 9000" in text


def test_completion_show_emits_a_script_for_each_shell() -> None:
    for shell in ("bash", "zsh", "fish"):
        result = runner.invoke(app, ["completion", "show", shell])
        assert result.exit_code == 0, f"{shell}: {result.output}"
        assert result.stdout.strip(), f"{shell}: empty script"
        assert "wsctl" in result.stdout.lower() or "WSCTL" in result.stdout


def test_completion_rejects_an_unknown_shell() -> None:
    result = runner.invoke(app, ["completion", "show", "csh"])
    assert result.exit_code == 1
    assert "不支持的 shell" in result.output
