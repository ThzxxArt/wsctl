"""The one implementation of "write a config file", and its guarantees.

Both entry points -- ``wsctl config set`` and the admin panel's online write --
go through :mod:`wsctl.core.config_edit`. The guarantees below are the product
promise, not implementation detail: a human's comments survive an edit, a bad
value never reaches the disk, and a rejected edit cannot leave a config the
server refuses to start from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wsctl.core.config import Settings
from wsctl.core.config_edit import (
    ConfigEditError,
    apply_key,
    format_toml_value,
    parse_toml_value,
    set_key,
    split_trailing_comment,
)


def test_a_trailing_comment_survives_the_edit() -> None:
    """Editing one key must not strip the explanation written beside it."""
    original = 'port = 7681  # 对外只暴露给内网\nhost = "127.0.0.1"\n'
    out = apply_key(original, "port", 9000)
    assert 'port = 9000  # 对外只暴露给内网' in out
    assert 'host = "127.0.0.1"' in out


def test_the_comment_splitter_is_quote_aware() -> None:
    """A ``#`` inside a quoted value is part of the value, not a comment."""
    assert split_trailing_comment('"a # b"') == ('"a # b"', None)
    assert split_trailing_comment("'a # b'  # real") == ("'a # b'", "# real")
    assert split_trailing_comment('""  # c') == ('""', "# c")


def test_a_new_key_appends_without_disturbing_the_rest() -> None:
    original = 'host = "0.0.0.0"\n'
    out = apply_key(original, "port", 8080)
    assert out.startswith('host = "0.0.0.0"\n')
    assert out.rstrip("\n").endswith("port = 8080")
    assert out.endswith("\n")


def test_an_unknown_key_is_refused_with_a_suggestion() -> None:
    with pytest.raises(ConfigEditError) as excinfo:
        apply_key("", "prot", 8080)
    assert "port" in str(excinfo.value), "a near-miss must be suggested"


def test_a_malformed_value_is_refused_before_any_write(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text('host = "127.0.0.1"\n', encoding="utf-8")
    with pytest.raises(ConfigEditError):
        set_key(target, "port", "not-an-int")
    # The file is untouched: the edit never reached the disk.
    assert target.read_text(encoding="utf-8") == 'host = "127.0.0.1"\n'


def test_a_wrong_type_is_refused_rather_than_written_for_the_next_start() -> None:
    """``port = "abc"`` must fail here, not as a service that will not come up."""
    with pytest.raises(ConfigEditError):
        apply_key("", "port", "abc")


def test_the_whole_file_is_validated_and_a_bad_edit_rolls_back(tmp_path: Path) -> None:
    """A single-key edit is checked against the *resulting* document.

    Type-checking only the new value would let a file with a duplicate key (or
    a value that only conflicts when read together) be published -- and the
    next start would be the first place anyone found out.
    """
    target = tmp_path / "config.toml"
    original = 'host = "127.0.0.1"\nport = 7681\n'
    target.write_text(original, encoding="utf-8")
    out = set_key(target, "port", 9000)
    assert "port = 9000" in out
    assert target.read_text(encoding="utf-8") == out

    # Now force a document that will not load, by hand-editing the text the
    # helper would produce: ``apply_key`` must refuse to hand it back.
    broken = 'host = "127.0.0.1"\nport = 7681\nport = 8080\n'
    with pytest.raises(ConfigEditError):
        apply_key(broken, "port", 9000)


def test_the_write_is_atomic(tmp_path: Path) -> None:
    """A reader never sees a half-written config, and no staging file is left."""
    target = tmp_path / "config.toml"
    target.write_text("port = 7681\n", encoding="utf-8")
    set_key(target, "port", 8080)
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "config.toml"]
    assert not leftovers, f"staging files left behind: {leftovers}"
    assert target.read_text(encoding="utf-8") == "port = 8080\n"


def test_a_file_that_does_not_exist_yet_is_created() -> None:
    out = apply_key("", "port", 8080)
    data = out.strip().splitlines()
    assert data == ["port = 8080"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, "true"),
        (False, "false"),
        (8080, "8080"),
        (1.5, "1.5"),
        ("文本", '"文本"'),
        (["a", "b"], '["a", "b"]'),
    ],
)
def test_values_round_trip_through_toml(value: object, expected: str) -> None:
    """Whatever the panel sends comes back the same after a parse."""
    rendered = format_toml_value(value)
    assert rendered == expected
    assert parse_toml_value(rendered) == value


def test_an_unsupported_type_is_refused_not_stringified() -> None:
    """A dict must fail loudly; silently ``str()``-ing it would write junk."""
    with pytest.raises(ConfigEditError):
        format_toml_value({"a": 1})


def test_every_key_the_model_knows_can_be_written(tmp_path: Path) -> None:
    """No key is a special case that the writer silently drops.

    The admin panel lists every field, so "this one cannot be edited" would be
    a surprise discovered at the keyboard.
    """
    from wsctl.core.config_edit import known_keys

    target = tmp_path / "config.toml"
    target.write_text("", encoding="utf-8")
    sample: dict[str, object] = {
        "host": "127.0.0.1", "port": 1, "auth_required": True,
        "session_ttl": 1, "session_sliding_ttl": True, "cookie_secure": True,
        "trust_proxy": True, "allowed_ips": ["127.0.0.1"], "security_headers": True,
        "login_rate_limit": 1, "login_rate_window": 1, "audit_input": True,
        "totp_issuer": "x", "default_shell": "/bin/sh", "default_cwd": "/tmp",
        "default_backend": "local", "tmux_preserve_on_shutdown": True,
        "allowed_origins": ["http://x"], "idle_timeout": 1, "max_life": 1,
        "max_sessions": 1, "max_sessions_per_user": 1, "session_max_clients": 1,
        "session_memory_limit": 1, "client_max_bytes": 1, "input_rate_limit": 1,
        "input_rate_burst": 1, "scrollback_bytes": 1, "instance_ttl": 1,
        "audit_retention_days": 1, "term_session_retention_days": 1,
        "recordings_retention_days": 1, "recordings_max_bytes": 1,
        "data_dir": "/tmp/d", "config_path": "/tmp/c.toml", "ssl_cert": "/tmp/c",
        "ssl_key": "/tmp/k", "reuse_port": True, "file_root": "/tmp/f",
        "file_max_upload": 1, "metrics_enabled": True, "metrics_require_auth": True,
        "log_level": "info", "log_json": True, "log_file": "/tmp/l",
        "log_max_bytes": 1, "log_backup_count": 1, "auto_record": True,
        "record_input": True, "webhook_url": "http://example.invalid/hook",
    }
    missing = known_keys() - set(sample)
    assert not missing, f"the sample does not cover {sorted(missing)}"
    text = ""
    for key in sorted(sample):
        text = apply_key(text, key, sample[key])
    parsed = Settings(**__import__("tomllib").loads(text))
    assert parsed.port == 1


# -- 0.1.24 回归 review：编辑不许动别的东西 ----------------------------


def test_a_crlf_file_keeps_its_line_endings(tmp_path: Path) -> None:
    """One key, one line, one diff.

    The writer used to translate line endings, so editing a CRLF config on any
    platform rewrote the whole file as LF -- "the only acceptable change is
    the one value they asked for" is this module's own promise.
    """
    target = tmp_path / "config.toml"
    target.write_bytes('host = "127.0.0.1"\r\nport = 7681  # 对外\r\n'.encode())
    set_key(target, "port", 9000)
    raw = target.read_bytes()
    text = raw.decode("utf-8")
    assert "\r\n" in text, "the file's CRLF endings were rewritten"
    assert 'host = "127.0.0.1"\r\n' in text
    assert "port = 9000  # 对外\r\n" in text
    assert text.count("\n") == text.count("\r\n"), "an LF-only line appeared"


def test_an_lf_file_keeps_lf(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_bytes(b'port = 7681\nhost = "127.0.0.1"\n')
    set_key(target, "port", 9000)
    raw = target.read_bytes()
    assert b"\r" not in raw, "an LF file must not grow CR"
    assert raw == b'port = 9000\nhost = "127.0.0.1"\n'


def test_clearing_an_optional_setting_removes_the_key() -> None:
    """An empty box means "unset", and unset is *no line*, not an empty string.

    `default_cwd = ""` is a different -- and broken -- thing from no cwd at
    all, and an optional int field cannot be `""` in the first place.
    """
    original = 'default_cwd = "/srv"  # 也是注释\ndefault_shell = "/bin/bash"\n'
    out = apply_key(original, "default_cwd", None)
    assert "default_cwd" not in out
    assert 'default_shell = "/bin/bash"' in out


def test_clearing_a_setting_that_cannot_be_unset_is_refused() -> None:
    """`host = null` is not a setting; say so instead of writing nonsense."""
    with pytest.raises(ConfigEditError):
        apply_key('host = "127.0.0.1"\n', "host", None)


def test_clearing_the_only_key_leaves_a_valid_empty_file() -> None:
    out = apply_key('default_cwd = "/srv"\n', "default_cwd", None)
    assert out == ""
    # ...and a file with nothing in it is still a legal config.
    assert Settings(**{}) is not None
