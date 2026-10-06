"""Pydantic request bodies for the HTTP API.

Kept out of the route modules so importing one router never drags in another
(and so ``create_app`` can stay a thin assembly step).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    # Bounded *before* it reaches Argon2: an unbounded password field is a
    # free CPU knob for anyone who can reach /api/login.
    password: str = Field(min_length=1, max_length=256)
    totp: str | None = Field(default=None, max_length=16)


class TotpConfirm(BaseModel):
    """The code that turns a *pending* TOTP enrolment into an active one."""

    code: str = Field(min_length=6, max_length=16)


class SshConfig(BaseModel):
    host: str
    user: str | None = None
    port: int | None = None
    identity: str | None = None
    options: list[str] = Field(default_factory=list)
    command: str | None = None


class SessionCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    command: str | None = Field(default=None, max_length=4000)
    cwd: str | None = Field(default=None, max_length=4096)
    backend: str | None = None
    ssh: SshConfig | None = None
    cols: int = Field(default=80, ge=1, le=1000)
    rows: int = Field(default=24, ge=1, le=1000)


class SessionReopen(BaseModel):
    """Optional display overrides for a history "reopen".

    The ``argv`` and the backend come from the *record* (see
    ``routes.sessions.reopen_session``): the endpoint replays what was
    recorded, and the caller does not get to dictate what is spawned. These
    fields only rename the replay or move its working directory.
    """

    name: str | None = Field(default=None, max_length=200)
    cwd: str | None = Field(default=None, max_length=4096)


class SessionRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SessionShare(BaseModel):
    #: ``None`` means "no expiry". 0 and negatives used to be accepted and
    # produced a share that was already dead on arrival -- a 200 with a token
    # that could never work.
    ttl: int | None = Field(default=None, ge=1)
    writable: bool = False


class RecordingStart(BaseModel):
    record_input: bool = False


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)
    role: str = "user"


class UserUpdate(BaseModel):
    role: str | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, max_length=256)


#: How big one configuration value may be. Untyped must not mean unbounded:
#: a megabyte of ``value`` is a megabyte of config file, and the request body
#: is the only place that can be stopped -- the same principle
#: ``test_request_bodies_are_bounded_before_they_reach_the_work`` pins for
#: passwords and upload bodies.
CONFIG_VALUE_MAX_CHARS = 64 * 1024


class ConfigSetItem(BaseModel):
    """One configuration assignment from the admin panel.

    ``value`` is deliberately untyped: the settings model decides what is
    acceptable, and ``core.config_edit.check_value`` refuses the rest against
    that model rather than against a second, drifting schema here. It is
    bounded, though -- see :data:`CONFIG_VALUE_MAX_CHARS`.
    """

    key: str = Field(min_length=1, max_length=128)
    value: Any

    @model_validator(mode="after")
    def _value_is_bounded(self) -> ConfigSetItem:
        if _value_size(self.value) > CONFIG_VALUE_MAX_CHARS:
            raise ValueError(
                f"配置项 {self.key} 的值过大（上限 {CONFIG_VALUE_MAX_CHARS} 字符）"
            )
        return self


def _value_size(value: Any) -> int:
    """Rough size of a value, without serialising it first."""
    if value is None or isinstance(value, (bool, int, float)):
        return 1
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (list, tuple)):
        return sum(_value_size(item) for item in value) + 2 * len(value)
    if isinstance(value, dict):
        return sum(_value_size(k) + _value_size(v) for k, v in value.items())
    return len(repr(value))


class ConfigSetRequest(BaseModel):
    #: Bounded so one request cannot rewrite the file a hundred times over.
    items: list[ConfigSetItem] = Field(min_length=1, max_length=64)
    #: Apply what a reload can reach after publishing. Off means "write the
    #: file only", which is what an operator editing ahead of a restart wants.
    reload: bool = True
