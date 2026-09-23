from __future__ import annotations

import json
import os
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


def test_session_list_json_is_machine_readable(
    monkeypatch: object, tmp_path: Path
) -> None:
    # Hermetic: a real ~/.config/wsctl/credentials.json on the developer's
    # machine must not be able to make this pass (it did, and the test then
    # failed on a clean CI runner).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    monkeypatch.delenv("WSCTL_TOKEN", raising=False)  # type: ignore[attr-defined]
    rows = [{"id": "abc", "name": "build", "pid": 1, "clients": 0,
             "owner": "alice", "backend": "tmux", "alive": True}]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        "wsctl.cli.client.ApiClient", lambda *a, **k: _fake_api_client(rows)
    )
    result = runner.invoke(app, ["session", "list", "--json", "--url", "http://127.0.0.1:1"])
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
    import os

    for key in [k for k in os.environ if k.startswith("WSCTL_")]:
        monkeypatch.delenv(key, raising=False)  # type: ignore[attr-defined]
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


# -- 0.1.5: lifecycle follows the running instance, --admin-password is loud --


def test_resolve_target_follows_the_running_instance(tmp_path, monkeypatch) -> None:
    """`wsctl start --port 7682` then `wsctl status` must find it.

    Regression for "你把 7681 端口写死了": without an explicit --port the
    commands used to act on the default port and report 未在运行 / 没有日志文件.
    """
    from wsctl.cli import daemon

    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    settings = load_settings()
    inst = daemon.Instance(
        pid=os.getpid(), host="0.0.0.0", port=18111,
        started_at=0.0, version="0", identity="",
    )
    aimed = settings.model_copy(update={"port": 18111})
    path = daemon.pidfile_path(aimed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(daemon.asdict(inst)), encoding="utf-8")
    log_path = daemon.logfile_path(aimed)
    log_path.write_text("booted on 18111\n", encoding="utf-8")
    try:
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0, result.output
        assert "18111" in result.output
        assert "已自动跟随正在运行的实例" in result.output

        # `logs` must open the *instance's* log, not wsctl-7681.log.
        logs = runner.invoke(app, ["logs", "-n", "1"])
        assert logs.exit_code == 0, logs.output
        assert "booted on 18111" in logs.output
    finally:
        path.unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)


def test_admin_password_is_never_silently_ignored(tmp_path, monkeypatch, capsys) -> None:
    """A --admin-password that cannot take effect must say so on the terminal.

    The bootstrap runs in the detached child, so its warning only reached the
    log file -- which reads exactly like "your password is wrong" when the user
    then cannot log in.
    """
    from wsctl.cli.main import _warn_ignored_admin_password

    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    store = Store(load_settings().db_path)
    store.user_create("admin", "password123", role="admin")
    store.user_create("bob", "password123")
    store.close()

    _warn_ignored_admin_password(load_settings(), "somethingElse1")
    out = capsys.readouterr().err
    assert "--admin-password 将被忽略" in out
    assert "wsctl user passwd admin" in out


def test_admin_password_recovers_a_lockout(tmp_path, monkeypatch) -> None:
    """With no enabled admin left, the value becomes the recovery password."""
    from wsctl.cli.main import _ensure_admin

    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    store = Store(load_settings().db_path)
    store.user_create("bob", "password123")  # a user, but no admin at all
    assert store.admin_count() == 0

    _ensure_admin(store, load_settings(), "recoveredPass1")
    assert store.admin_count() == 1
    assert store.user_authenticate("admin", "recoveredPass1") is not None
    store.close()


def test_user_passwd_accepts_a_non_interactive_password(tmp_path, monkeypatch) -> None:
    """The one-command fix when someone is locked out of the web UI."""
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    store = Store(load_settings().db_path)
    store.user_create("admin", "oldpassword1", role="admin")
    store.close()

    result = runner.invoke(app, ["user", "passwd", "admin", "-p", "brandNewPass1"])
    assert result.exit_code == 0, result.output

    store = Store(load_settings().db_path)
    try:
        assert store.user_authenticate("admin", "brandNewPass1") is not None
        assert store.user_authenticate("admin", "oldpassword1") is None
    finally:
        store.close()


# -- 0.1.6 review: doctor probes the instance, not a stale login URL --------


def _doctor_rows(result) -> dict[str, str]:
    payload = json.loads(result.stdout)
    return {row["check"]: row["result"] for row in payload}


def test_doctor_probes_the_running_instance_not_a_stale_credential(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    """A URL cached by an old `wsctl login` must not make a healthy box look dead.

    This is the exact report: doctor said "cannot reach http://127.0.0.1:7720"
    while an instance was serving on 7682 in the same data directory.
    """
    import json as _json

    from wsctl.cli import daemon

    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    creds = tmp_path / "wsctl" / "credentials.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text(_json.dumps({"url": "http://127.0.0.1:7720", "token": "stale"}))

    inst = daemon.Instance(
        pid=os.getpid(), host="0.0.0.0", port=18111,
        started_at=0.0, version="0", identity="",
    )
    aimed = load_settings().model_copy(update={"host": "0.0.0.0", "port": 18111})
    path = daemon.pidfile_path(aimed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json.dumps(daemon.asdict(inst)), encoding="utf-8")
    try:
        rows = _doctor_rows(runner.invoke(app, ["doctor", "--json"]))
        assert "0.0.0.0:18111" in rows["运行状态"]
        assert "18111" in rows["服务器"], rows["服务器"]
        assert "7720" not in rows["服务器"], "the stale login URL must be ignored"
    finally:
        path.unlink(missing_ok=True)


def test_doctor_falls_back_to_the_credential_and_says_so(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    """With nothing running, the cached URL is used -- and identified as such."""
    import json as _json

    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    creds = tmp_path / "wsctl" / "credentials.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text(_json.dumps({"url": "http://127.0.0.1:1", "token": "stale"}))

    rows = _doctor_rows(runner.invoke(app, ["doctor", "--json"]))
    assert rows["运行状态"].startswith("未运行")
    assert "7720" not in rows["服务器"]
    assert "wsctl login" in rows["服务器"], "the origin of the address must be named"
    assert "wsctl logout" in rows["服务器"], "the remedy must be named"


def test_doctor_explicit_url_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    creds = tmp_path / "wsctl" / "credentials.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text('{"url": "http://127.0.0.1:1", "token": "stale"}')

    rows = _doctor_rows(
        runner.invoke(app, ["doctor", "--json", "--url", "http://127.0.0.1:2"])
    )
    assert "127.0.0.1:2" in rows["服务器"]


def test_doctor_accepts_host_and_port(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))  # type: ignore[attr-defined]
    """`wsctl doctor --port N` used to die with "No such option: --port"."""
    monkeypatch.setenv("WSCTL_DATA_DIR", str(tmp_path))  # type: ignore[attr-defined]
    _missing_config(tmp_path, monkeypatch)
    result = runner.invoke(app, ["doctor", "--port", "18111"])
    assert result.exit_code != 2, result.output  # 2 = usage error


def test_settings_from_does_not_leak_wsctl_config(tmp_path) -> None:
    """`--config X` must not become ambient state for the next call.

    `_settings_from` used to assign `os.environ["WSCTL_CONFIG"]` permanently,
    so a later call without `--config` in the same process silently kept
    reading the previous file. That is process-wide hidden state -- the same
    shape as the environment leaks this suite keeps getting bitten by.
    """
    import os

    from wsctl.cli.main import _settings_from

    first = tmp_path / "first.toml"
    second = tmp_path / "second.toml"
    first.write_text("max_sessions = 5\n", encoding="utf-8")
    second.write_text("max_sessions = 9\n", encoding="utf-8")

    assert os.environ.get("WSCTL_CONFIG") != str(first)
    assert _settings_from(first).max_sessions == 5
    assert "WSCTL_CONFIG" not in os.environ or os.environ["WSCTL_CONFIG"] != str(first), (
        "--config leaked into the process environment"
    )

    # A later call with no --config must not keep reading `first`.
    assert _settings_from(second).max_sessions == 9
    assert _settings_from(None).max_sessions != 5


def test_doctor_does_not_create_the_database(tmp_path, monkeypatch) -> None:
    """A diagnostic command must not mutate the data directory it inspects.

    Building a ``Store`` to count users created an empty database, so
    ``wsctl doctor`` run to find out why the directory was broken made it
    "initialised" and muddied every later diagnosis.
    """

    from typer.testing import CliRunner

    from wsctl.cli.main import app

    data = tmp_path / "brand-new"
    conf = tmp_path / "config"
    conf.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(conf))
    monkeypatch.setenv("WSCTL_DATA_DIR", str(data))

    # Assert through ``--json``: Rich truncates long table cells to "…" and a
    # long ``tmp_path`` ate the very sentence under test. JSON is exact.
    result = CliRunner().invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output
    assert not (data / "wsctl.db").exists(), "doctor created the database"
    rows = json.loads(result.output)
    database = next(r for r in rows if r["check"] == "数据库")
    assert "尚未初始化" in database["result"], database
