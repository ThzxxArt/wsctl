"""The declarative CLI validation layer."""

from __future__ import annotations

import pytest

from wsctl.cli import validate as v


def test_options_treat_none_and_false_as_absent() -> None:
    options = v.Options({"a": None, "b": False, "c": 0, "d": ""})
    assert not options.has("a")
    assert not options.has("b")
    # Meaningful falsy values are still "set".
    assert options.has("c")
    assert options.has("d")


def test_exclusive_names_both_flags() -> None:
    options = v.Options({"daemon": True, "foreground": True})
    with pytest.raises(v.CliUsageError) as err:
        v.validate(options, [v.exclusive("daemon", "foreground")])
    assert "--daemon" in err.value.message
    assert "--foreground" in err.value.message


def test_exclusive_allows_one() -> None:
    v.validate(v.Options({"daemon": True}), [v.exclusive("daemon", "foreground")])
    v.validate(v.Options({}), [v.exclusive("daemon", "foreground")])


def test_requires() -> None:
    with pytest.raises(v.CliUsageError) as err:
        v.validate(v.Options({"ssl_cert": "x"}), [v.requires("ssl_cert", "ssl_key")])
    assert "--ssl-key" in err.value.message
    v.validate(
        v.Options({"ssl_cert": "x", "ssl_key": "y"}),
        [v.requires("ssl_cert", "ssl_key")],
    )


def test_requires_if_only_when_value_matches() -> None:
    rule = v.requires_if("backend", "ssh", "ssh")
    with pytest.raises(v.CliUsageError):
        v.validate(v.Options({"backend": "ssh"}), [rule])
    v.validate(v.Options({"backend": "local"}), [rule])
    v.validate(v.Options({}), [rule])


def test_choices() -> None:
    rule = v.choices("backend", ("local", "tmux"))
    with pytest.raises(v.CliUsageError) as err:
        v.validate(v.Options({"backend": "nope"}), [rule])
    assert "local" in err.value.message and "tmux" in err.value.message
    v.validate(v.Options({"backend": "tmux"}), [rule])
    v.validate(v.Options({}), [rule])


def test_custom_rule_and_ordering() -> None:
    seen: list[str] = []

    def first(_: v.Options) -> str | None:
        seen.append("first")
        return None

    def second(_: v.Options) -> str | None:
        seen.append("second")
        return "boom"

    def third(_: v.Options) -> str | None:
        seen.append("third")
        return None

    with pytest.raises(v.CliUsageError) as err:
        v.validate(v.Options({}), [v.custom(first), v.custom(second), v.custom(third)])
    assert err.value.message == "boom"
    # Validation stops at the first violation.
    assert seen == ["first", "second"]


def test_error_carries_optional_hint() -> None:
    error = v.CliUsageError("bad", hint="try this")
    assert error.hint == "try this"
