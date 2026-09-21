# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-21

First release. Single-server web terminal with persistent sessions, multi-user
RBAC, auditing, a file panel and observability.

### Added

**Sessions & server**
- `SessionManager` with connection-independent PTY sessions, attach/detach,
  scrollback replay on reconnect and bounded output backpressure.
- POSIX PTY backend; optional Windows support via `pywinpty` (`wsctl[win]`).
- Binary WebSocket protocol (raw terminal bytes) with a JSON control channel.
- Multi-tab web UI (vendored xterm.js, zero build step) with auto-reconnect.
- Session idle/max-lifetime enforcement and metadata reconciliation.

**Users & security**
- Multi-user accounts with Argon2 hashing and server-side session tokens.
- Role-based access control (admin / user) on the REST API and WebSocket attach.
- TOTP two-factor authentication (`wsctl user totp`).
- Login rate limiting (per IP + username) and a CIDR IP allowlist.
- Origin allowlist on WebSocket handshakes (anti-CSWSH).
- Security response headers (nosniff, frame DENY, referrer policy, CSP).
- Optional submitted-input auditing (`audit_input`).

**Auditing**
- Full audit trail with an admin `GET /api/audit` endpoint and `wsctl audit`.

**Files**
- Web file panel API (list / download / upload) rooted at a configurable
  `file_root`, with traversal-proof resolution and upload size limits.

**Observability**
- `/healthz` and Prometheus metrics at `/metrics`.
- Structured JSON logging (`--log-json`).

**CLI**
- `serve` (`--new`), `connect` (terminal thin client), `login`, `logout`,
  `session list|new|kill|attach`, `user add|list|del|passwd|role|totp`, `audit`,
  `config show|path|edit`, `version`.

**Limits & robustness**
- Per-session client limit (`session_max_clients`) and per-connection input rate
  limiting via a token bucket (`input_rate_limit` / `input_rate_burst`).
- Session renaming (`PATCH /api/sessions/{id}`, double-click a tab in the UI).
- Concurrency and large-output load tests (`pytest -m slow` for the heavy one).

**Packaging & CI**
- Hatchling packaging (PyPI: `wsctl`), MIT license, `py.typed`.
- GitHub Actions CI (ruff, mypy, pytest on Python 3.11–3.13) and a release
  workflow using PyPI Trusted Publishing.

### Fixed

- WebSocket teardown now drains queued control messages before closing.
- WebSocket auto-created sessions record `owner_id` and persist metadata.
- `safe_resolve` rejects absolute paths instead of silently re-rooting them.

[0.1.0]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.0
