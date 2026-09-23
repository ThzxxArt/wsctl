"""Route modules for the HTTP API.

Each module owns one concern and exposes a single ``router``. ``create_app``
mounts them in a fixed order; keep that order stable, because FastAPI matches
paths in registration order and a later, broader pattern can shadow an earlier
narrower one.
"""

from __future__ import annotations

from fastapi import APIRouter

from . import audit, auth, config, files, health, recordings, sessions, share, status, users

ROUTERS: list[tuple[APIRouter, str]] = [
    (health.router, ""),
    (auth.router, ""),
    (sessions.router, ""),
    (status.router, ""),
    (share.router, ""),
    (recordings.router, ""),
    (users.router, ""),
    (audit.router, ""),
    (config.router, ""),
    (files.router, ""),
]

__all__ = ["ROUTERS"]
