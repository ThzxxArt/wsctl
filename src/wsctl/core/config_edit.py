"""Edit one key of the TOML config file without destroying the author's work.

The CLI's ``config set`` grew this logic first: keep the trailing comment the
author wrote next to the value, type-check the single value before touching
the file, validate the *whole* file afterwards and roll back rather than leave
a config the server cannot start from. The web admin wants exactly the same
guarantees, and a second implementation of "write a config file" is a second
place for them to diverge -- so this is the single source, used by both.

Nothing here is allowed to be clever. A config file is a document a human
maintains; the only acceptable change is the one value they asked for.
"""

from __future__ import annotations

import os
import re
import secrets
import tomllib
from pathlib import Path
from typing import Any

from . import config as config_mod


class ConfigEditError(Exception):
    """A rejected edit. The message is written for a human and is safe to show."""


_KEY_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*=")


def known_keys() -> set[str]:
    """Every key the settings model accepts."""
    return set(config_mod.Settings.model_fields)


def split_trailing_comment(text: str) -> tuple[str, str | None]:
    """Split ``value  # comment`` into ``(value, comment)``, quote-aware.

    The comment is kept verbatim: editing one key must not strip the
    explanation the author wrote beside it, which is what a naive
    "rewrite the line" does.
    """
    in_single = in_double = False
    for index, ch in enumerate(text):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif (
            ch == "#"
            and not in_single
            and not in_double
            and (index == 0 or text[index - 1] in " \t")
        ):
            return text[:index].rstrip(), text[index:].strip()
    return text.rstrip(), None


def format_toml_value(value: Any) -> str:
    """Render a Python value as a TOML literal on one line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return repr(value)
    if isinstance(value, str):
        # ``json.dumps`` is a superset of TOML's basic string escapes and
        # always quotes, so the result is a legal TOML basic string.
        import json

        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        inner = ", ".join(format_toml_value(item) for item in value)
        return f"[{inner}]"
    raise ConfigEditError(f"不支持的值类型：{type(value).__name__}")


def parse_toml_value(text: str) -> Any:
    """Parse a TOML literal. Rejects anything that is not a single value."""
    try:
        return tomllib.loads(f"__value__ = {text}")["__value__"]
    except tomllib.TOMLDecodeError as exc:
        raise ConfigEditError(
            '值必须是合法 TOML，例如 8080、"文本"、true、["a", "b"]'
        ) from exc


def check_key(key: str) -> None:
    if key not in known_keys():
        import difflib

        suggestion = difflib.get_close_matches(key, known_keys(), n=1)
        hint = f"，是否想用 “{suggestion[0]}”？" if suggestion else ""
        raise ConfigEditError(f"未知的配置项：{key}{hint}")


def check_value(key: str, value: Any) -> None:
    """Type-check one value against the settings model before any write.

    ``port = "abc"`` is refused here rather than at the next start, where the
    failure would be a service that does not come up and a log nobody reads.
    """
    try:
        config_mod.Settings(**{key: value})
    except Exception as exc:  # pydantic ValidationError and friends
        raise ConfigEditError(f"配置项 {key} 的值无效：{exc}") from exc


def apply_key(original: str, key: str, value: Any) -> str:
    """Return ``original`` with ``key`` set to ``value``, comments intact.

    Pure: does not touch the filesystem, so the caller can validate the result
    before deciding to keep it. Raises :class:`ConfigEditError` rather than
    return a document that would not load.
    """
    check_key(key)
    check_value(key, value)

    # The line ending is the file's, not the writer's. ``splitlines()`` drops
    # whichever ending each line used, so it has to be measured and put back:
    # rewriting a CRLF config as LF turns "changed one key" into "changed every
    # line", which is the exact thing this module promises not to do.
    ending = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines() if original else []

    if value is None:
        # "Clear this setting" is *removing the key*, not writing an empty
        # string: an optional field with nothing in it means "unset", and
        # `default_cwd = ""` is a different (and broken) thing from no cwd.
        # ``check_value`` above has already confirmed the model accepts None.
        kept = []
        removed = False
        for line in lines:
            match = _KEY_LINE.match(line)
            if match is not None and match.group(2) == key and not removed:
                removed = True
                continue
            kept.append(line)
        new_text = ending.join(kept)
        if new_text:
            new_text = new_text.rstrip("\r\n") + ending
    else:
        rendered = format_toml_value(value)
        replaced = False
        for index, line in enumerate(lines):
            match = _KEY_LINE.match(line)
            if match is None or match.group(2) != key:
                continue
            _, comment = split_trailing_comment(line[match.end():])
            suffix = f"  {comment}" if comment else ""
            lines[index] = f"{key} = {rendered}{suffix}"
            replaced = True
            break
        if not replaced:
            # Keep the file's trailing newline convention: a config that grew a
            # key should not also grow a diff on every unrelated line.
            if lines and not lines[-1].strip():
                lines.insert(len(lines) - 1, f"{key} = {rendered}")
            else:
                lines.append(f"{key} = {rendered}")
        new_text = ending.join(lines).rstrip("\r\n") + ending

    # Validate the *whole* file (duplicate keys, values that only conflict when
    # read together) and roll back on failure so a bad edit never leaves the
    # server unable to start.
    try:
        data = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigEditError(f"写入后配置无效，已放弃修改：{exc}") from exc
    try:
        config_mod.Settings(**data)
    except Exception as exc:
        raise ConfigEditError(f"写入后配置无效，已放弃修改：{exc}") from exc
    return new_text


def write_text_atomic(path: Path, text: str) -> None:
    """Publish ``text`` at ``path`` in one step (staging file + ``os.replace``).

    The staging name is unique and created with ``O_EXCL``: a predictable name
    lets two concurrent writers interleave into one file, and lets a hostile
    neighbour pre-create it to catch someone else's draft.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.parent / f".{path.name}.wsctl-{secrets.token_hex(8)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(staging, flags, 0o600)
    try:
        # ``newline=""``: the text already carries whatever line endings the
        # file had, and translating them here is what silently rewrote a CRLF
        # config as LF on every edit.
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise


def set_key(path: Path, key: str, value: Any) -> str:
    """Set ``key`` in the config file at ``path``. Returns the new text.

    The file is read, edited in memory, validated whole, and only then written
    atomically. A rejected edit never reaches the disk.
    """
    # ``newline=""`` keeps the file's own line endings intact; universal
    # newline translation would hand back ``\n`` and the writer could not
    # tell a CRLF file from an LF one.
    if path.is_file():
        with path.open("r", encoding="utf-8", newline="") as handle:
            original = handle.read()
    else:
        original = ""
    new_text = apply_key(original, key, value)
    write_text_atomic(path, new_text)
    return new_text
