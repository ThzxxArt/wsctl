"""Pydantic request bodies for the HTTP API.

Kept out of the route modules so importing one router never drags in another
(and so ``create_app`` can stay a thin assembly step).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str
    totp: str | None = None


class SshConfig(BaseModel):
    host: str
    user: str | None = None
    port: int | None = None
    identity: str | None = None
    options: list[str] = Field(default_factory=list)
    command: str | None = None


class SessionCreate(BaseModel):
    name: str | None = None
    command: str | None = None
    cwd: str | None = None
    backend: str | None = None
    ssh: SshConfig | None = None
    cols: int = Field(default=80, ge=1, le=1000)
    rows: int = Field(default=24, ge=1, le=1000)


class SessionReopen(BaseModel):
    """Recreate a finished session the way it originally ran.

    The client sends back the ``argv`` the session was created with, not a
    ``command`` string plus a backend name. Reconstructing an SSH session from
    its remote command alone would either 400 (no ``ssh`` block) or -- if the
    backend were dropped -- run the *remote* command on the *local* shell.
    """

    argv: list[str]
    name: str | None = None
    cwd: str | None = None
    backend: str = "local"


class SessionRename(BaseModel):
    name: str


class SessionShare(BaseModel):
    ttl: int | None = None
    writable: bool = False


class RecordingStart(BaseModel):
    record_input: bool = False


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"


class UserUpdate(BaseModel):
    role: str | None = None
    disabled: bool | None = None
    password: str | None = None
