"""Declarative validation for mutually exclusive / dependent CLI options.

Commands collect the options that were *explicitly* provided into an
:class:`Options` view and run it through a small rule table, so a contradictory
invocation fails fast with an actionable message instead of silently ignoring
one of the flags. This is the single place where "these two flags cannot be
combined" or "this flag needs that one" is expressed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


class CliUsageError(Exception):
    """A command line that is self-contradictory or missing a dependency."""

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class Options:
    """The subset of CLI options that were explicitly provided.

    ``None`` (unset) and ``False`` (unset boolean flag) are treated as absent,
    so a rule only ever sees flags the user actually typed. Values that are
    meaningfully ``0`` or ``""`` are kept.
    """

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = {
            key: value
            for key, value in values.items()
            if value is not None and value is not False
        }

    def has(self, name: str) -> bool:
        return name in self._values

    def get(self, name: str, default: Any = None) -> Any:
        return self._values.get(name, default)

    def value(self, name: str) -> Any:
        return self._values.get(name)

    def set(self, name: str, value: Any) -> None:
        """Record a value the user did not type (e.g. a resolved port).

        Used when a command has to reproduce a running instance's listening
        address in a child argv: leaving it out would boot the replacement on
        the default port instead of the one in use.
        """
        if value is None or value is False:
            self._values.pop(name, None)
        else:
            self._values[name] = value

    def names(self) -> list[str]:
        return sorted(self._values)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)


@dataclass(frozen=True)
class Rule:
    """A single validation rule; ``check`` returns an error message or ``None``."""

    check: Callable[[Options], str | None]

    def __call__(self, options: Options) -> str | None:
        return self.check(options)


def label(name: str) -> str:
    """Render an internal option name as the flag the user typed."""
    return "--" + name.replace("_", "-")


def exclusive(*names: str, message: str | None = None) -> Rule:
    """At most one of ``names`` may be provided."""

    def check(options: Options) -> str | None:
        present = [name for name in names if options.has(name)]
        if len(present) > 1:
            if message:
                return message
            return " 与 ".join(label(name) for name in present) + " 不能同时使用"
        return None

    return Rule(check)


def requires(option: str, *needs: str, message: str | None = None) -> Rule:
    """If ``option`` is set, every option in ``needs`` must also be set."""

    def check(options: Options) -> str | None:
        if not options.has(option):
            return None
        missing = [name for name in needs if not options.has(name)]
        if missing:
            if message:
                return message
            needed = "、".join(label(name) for name in missing)
            return f"{label(option)} 需要同时提供 {needed}"
        return None

    return Rule(check)


def requires_if(option: str, value: Any, *needs: str, message: str | None = None) -> Rule:
    """If ``option == value``, every option in ``needs`` must be set."""

    def check(options: Options) -> str | None:
        if options.get(option) != value:
            return None
        missing = [name for name in needs if not options.has(name)]
        if missing:
            if message:
                return message
            needed = "、".join(label(name) for name in missing)
            return f"{label(option)} {value!r} 需要同时提供 {needed}"
        return None

    return Rule(check)


def choices(option: str, allowed: Iterable[str], *, message: str | None = None) -> Rule:
    """``option``, when provided, must be one of ``allowed``."""
    allowed_set = frozenset(allowed)

    def check(options: Options) -> str | None:
        if not options.has(option):
            return None
        value = options.get(option)
        if value not in allowed_set:
            if message:
                return message
            options_text = "、".join(sorted(allowed_set))
            return f"{label(option)} 只能是 {options_text}，当前为 {value!r}"
        return None

    return Rule(check)


def custom(check: Callable[[Options], str | None]) -> Rule:
    """Escape hatch for rules the helpers above cannot express."""
    return Rule(check)


def validate(options: Options, rules: Iterable[Rule]) -> None:
    """Run ``rules`` in order; raise on the first violation."""
    for rule in rules:
        error = rule(options)
        if error:
            raise CliUsageError(error)
