from __future__ import annotations

from pathlib import Path

import pytest

from wsctl.core import config as config_mod
from wsctl.core import config_v2
from wsctl.core.config_edit import apply_key


def test_every_setting_has_a_section_and_an_apply_tier() -> None:
    fields = set(config_mod.Settings.model_fields)
    assert fields == set(config_v2.SECTION_MAP)
    for key in fields:
        assert config_v2.apply_tier(key) == config_mod.kind_of(key), key


def test_human_units_are_normalised() -> None:
    assert config_v2.coerce_value("scrollback_bytes", "8MiB") == 8 * 1024 * 1024
    assert config_v2.coerce_value("session_ttl", "12h") == 12 * 3600
    assert config_v2.coerce_value("session_ttl", "30m") == 1800
    assert config_v2.coerce_value("session_ttl", 3600) == 3600
    assert config_v2.coerce_value("session_ttl", "not-a-unit") == "not-a-unit"


def test_flat_and_sectioned_documents_flatten_identically() -> None:
    flat = {"host": "127.0.0.1", "port": 7681, "scrollback_bytes": "8MiB"}
    sectioned = {
        "server": {"host": "127.0.0.1", "port": 7681},
        "terminal": {"scrollback_bytes": "8MiB"},
    }
    assert config_v2.flatten_document(flat) == config_v2.flatten_document(sectioned)


def test_duplicate_key_across_shapes_is_refused() -> None:
    with pytest.raises(config_v2.ConfigV2Error):
        config_v2.flatten_document({"port": 1, "server": {"port": 2}})


def test_sectioned_file_loads_through_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[server]\nhost = "0.0.0.0"\nport = 9000\n\n[terminal]\nscrollback_bytes = "8MiB"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("WSCTL_CONFIG", str(path))
    settings = config_mod.load_settings()
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000
    assert settings.scrollback_bytes == 8 * 1024 * 1024


def test_section_environment_variable_overrides_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[server]\nport = 9000\n", encoding="utf-8")
    monkeypatch.setenv("WSCTL_CONFIG", str(path))
    monkeypatch.setenv("WSCTL_SERVER__PORT", "9100")
    assert config_mod.load_settings().port == 9100


def test_migrate_preserves_values_and_trailing_comments() -> None:
    original = (
        "# wsctl 配置\n"
        'host = "127.0.0.1"  # the bind address\n'
        "port = 7681\n"
        "\n"
        "# session defaults\n"
        "session_ttl = 43200\n"
    )
    migrated = config_v2.migrate_text(original)
    assert "[server]" in migrated
    assert "[auth]" in migrated
    assert "# the bind address" in migrated
    assert "session_ttl = 43200" in migrated
    assert config_v2.migrate_text(migrated) == migrated


def test_migrate_file_creates_a_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("port = 7681\n", encoding="utf-8")
    changed, backup = config_v2.migrate_file(path)
    assert changed is True
    assert backup is not None and backup.is_file()
    assert "[server]" in path.read_text(encoding="utf-8")


def test_render_toml_round_trips() -> None:
    values = {"host": "127.0.0.1", "port": 7681, "session_ttl": 3600}
    rendered = config_v2.render_toml(values)
    parsed = config_v2.flatten_document(__import__("tomllib").loads(rendered))
    assert parsed == values


def test_apply_key_inserts_into_the_right_existing_section() -> None:
    original = "[server]\nhost = \"127.0.0.1\"\n\n[auth]\nauth_required = true\n"
    updated = apply_key(original, "port", 9000)
    assert "[server]\nhost = \"127.0.0.1\"\nport = 9000" in updated
    assert updated.index("port = 9000") < updated.index("[auth]")


def test_postgres_url_is_refused_in_020() -> None:
    with pytest.raises(Exception) as caught:
        config_mod.Settings(database_url="postgresql://user@host/wsctl")
    assert "0.3" in str(caught.value)


def test_dotted_section_keys_are_accepted_and_canonicalised(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("", encoding="utf-8")
    apply_key(path.read_text(encoding="utf-8"), "server.port", 9000)
    from wsctl.core.config_edit import apply_key as apply

    text = apply("", "auth.required", False)
    assert "auth_required" in text
    parsed = config_v2.flatten_document(__import__("tomllib").loads(text))
    assert parsed["auth_required"] is False
    assert config_v2.normalise_key("files.max_upload") == "file_max_upload"
    assert config_v2.normalise_key("server.port") == "port"


def test_section_aliases_flatten_to_the_model_name() -> None:
    flat = config_v2.flatten_document(
        {
            "auth": {"required": False},
            "files": {"root": "/tmp", "max_upload": "8MiB"},
        }
    )
    assert flat["auth_required"] is False
    assert flat["file_root"] == "/tmp"
    assert flat["file_max_upload"] == 8 * 1024 * 1024


def test_editing_a_section_alias_updates_it_instead_of_duplicating() -> None:
    """``[auth] required`` and ``auth_required`` are one setting.

    The editor used to search for the canonical spelling only; on a
    hand-written ``required = true`` it appended ``auth_required``, and the
    whole edit was rejected as a duplicate key.
    """
    original = '[auth]\nrequired = true\n# keep this comment\n'
    edited = apply_key(original, "auth.required", False)
    parsed = __import__("tomllib").loads(edited)
    assert parsed["auth"]["auth_required"] is False
    assert "required = true" not in edited
    assert "# keep this comment" in edited
    assert config_v2.flatten_document(parsed)["auth_required"] is False


def test_clearing_a_section_alias_removes_that_line() -> None:
    original = '[files]\nroot = "/tmp"\n'
    edited = apply_key(original, "files.root", None)
    assert "root" not in edited



def test_retention_days_are_converted_as_days_not_seconds() -> None:
    """``30d`` means thirty days; the sweep multiplies by 86400 itself."""
    assert config_v2.coerce_value("audit_retention_days", "30d") == 30
    assert config_v2.coerce_value("term_session_retention_days", "2w") == 14
    assert config_v2.coerce_value("recordings_retention_days", "48h") == 2
