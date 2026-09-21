# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-09-21

First release. Single-server web terminal with persistent sessions, multi-user
RBAC, auditing, sharing, a file panel and observability.

### Added

**Sessions & server**
- `SessionManager` with connection-independent PTY sessions, attach/detach,
  scrollback replay on reconnect and bounded output backpressure.
- POSIX PTY backend; optional Windows support via `pywinpty` (`wsctl[win]`).
- Binary WebSocket protocol (raw terminal bytes) with a JSON control channel.
- Multi-tab web UI (vendored xterm.js, zero build step) with auto-reconnect.
- Session renaming, idle/max-lifetime enforcement and metadata reconciliation.
- Per-session client limit (`session_max_clients`), per-session memory hard limit
  (`session_memory_limit`) and per-client byte cap (`client_max_bytes`).
- Per-connection input rate limiting via a token bucket
  (`input_rate_limit` / `input_rate_burst`).

**Backends**
- Optional `tmux` backend (`default_backend`, `--backend tmux`): the shell runs
  inside a tmux session and survives a full wsctl restart, then is reattached
  automatically with the same id and a redrawn screen; graceful shutdown
  preserves tmux sessions (`tmux_preserve_on_shutdown`).
- SSH backend: `backend="ssh"` builds a safe `ssh` argv from a structured target
  (host/user/port/identity/options/remote command) with no shell interpolation.

**Users & security**
- Multi-user accounts with Argon2 hashing and server-side session tokens.
- Role-based access control (admin / user) on the REST API and WebSocket attach.
- TOTP two-factor authentication (`wsctl user totp`).
- Login rate limiting (per IP + username) and a CIDR IP allowlist.
- Origin allowlist on WebSocket handshakes (anti-CSWSH).
- Security response headers (nosniff, frame DENY, referrer policy, CSP).
- Optional submitted-input auditing (`audit_input`).

**Auditing & events**
- Full audit trail with an admin `GET /api/audit` endpoint and `wsctl audit`.
- Optional webhooks (`webhook_url`): every audit event is POSTed as JSON by a
  background dispatcher.

**Sharing**
- Read-only and read-write share tokens (`POST/DELETE /api/sessions/{id}/share`),
  unguessable, optionally time-limited and revocable.
- Anonymous viewers attach with a share link (`?session=&share=`) without an
  account; input is refused for read-only links.
- QR code for a share link (`GET /api/sessions/{id}/qr.svg`) and a share dialog
  in the web UI.

**Recording**
- asciinema cast v2 recording (`core/recording.py`), optionally including input,
  with `auto_record` / `record_input` settings and per-session start/stop.
- In-browser replay via a vendored asciinema player, plus `wsctl session
  record|record-stop|recording`.

**Files & terminal features**
- Web file panel API (list / download / upload) rooted at a configurable
  `file_root`, with traversal-proof resolution and upload size limits.
- Sixel image rendering via `@xterm/addon-image`.
- Opt-in ZMODEM (`sz`/`rz`) file transfer in the browser via `zmodem.js`.

**Observability & operations**
- `/healthz` and Prometheus metrics at `/metrics` (incl. `wsctl_session_bytes`).
- Structured JSON logging (`--log-json`).
- Zero-downtime restarts: `reuse_port` / `--reuse-port` binds with `SO_REUSEPORT`
  so a new instance takes over the port before the old one exits.
- Runtime config hot-reload: file-mtime watcher, `POST /api/config/reload` and
  `wsctl config reload` (restart-only fields are ignored).

**Web UI**
- Dark/light page theme, font size, a terminal theme gallery (10 built-in themes
  plus custom JSON themes) and configurable keyboard shortcuts, persisted locally.
- Responsive/mobile layout adjustments.

**Persistence**
- SQLite storage for users, auth sessions, terminal sessions, audit logs and
  settings; full `term_sessions` columns (`argv`, `env`, `idle_timeout`,
  `max_life`) with an idempotent schema migration.

**CLI**
- `serve` (`--new`, `--backend`, `--reuse-port`), `connect`, `login`, `logout`,
  `session list|new|kill|attach|record|record-stop|recording`,
  `user add|list|del|passwd|role|totp`, `audit`, `config show|path|edit|set|reload`,
  `version`.

**Packaging & CI**
- Hatchling packaging (PyPI: `wsctl`), MIT license, `py.typed`.
- GitHub Actions CI (ruff, mypy, pytest on Python 3.11–3.13) and a release
  workflow using PyPI Trusted Publishing.

### Fixed

- PTY no longer leaks the master fd or the child process when a spawn fails,
  and out-of-range terminal dimensions are clamped instead of raising.
- Read-only share clients can no longer resize the shared terminal.
- Revoking or expiring a share now detaches clients that already connected
  with that token.
- The PTY write buffer is bounded, so a child that stops reading stdin cannot
  grow server memory without limit.
- Sessions are stopped if post-create setup fails (REST and WebSocket paths),
  instead of leaking a live process with no metadata.
- File upload refuses to overwrite a symlink at the destination.
- Config hot-reload respects environment-variable precedence.
- Disabled users' existing sessions are invalidated.
- Empty commands (400), out-of-range dimensions (422) and duplicate users (409)
  are rejected cleanly instead of erroring out.
- WebSocket auth/origin failures now deliver close codes (4401/4403) to the
  browser instead of an opaque HTTP 403 that caused endless reconnects.
- CSP allows `blob:` so the recording player actually loads its cast, and
  `connect-src` was tightened to `'self'`.
- Web UI: guarded `localStorage` parsing, restored recording state on reload,
  reset the screen on reconnect (no duplicated scrollback), share "revoke"
  targets the session the dialog was opened for, read-only viewers can no
  longer trigger the login modal, and the replay player/blob URL is disposed.
- WebSocket teardown now drains queued control messages before closing.
- WebSocket auto-created sessions record `owner_id` and persist metadata.
- `safe_resolve` rejects absolute paths instead of silently re-rooting them.
- The share QR endpoint emits a standalone SVG (with `xmlns`) so it renders
  inside an `<img>`; the previous inline SVG was blank in browsers.
- CSP allows `'wasm-unsafe-eval'` and `worker-src blob:` so the recording player
  can run.

### Testing

- 147 unit/integration tests plus concurrency and large-output load tests
  (`pytest -m slow`).
- Browser-level end-to-end tests with Playwright (`pytest -m browser`, extra
  `wsctl[e2e]`) covering login, terminal I/O, multi-tab, hotkeys, file panel,
  sharing with QR, recording replay, theme switching, read-only input blocking
  and anonymous share links; run in CI in a dedicated job.
- Five backend end-to-end scripts (server, CLI, `connect`, tmux restart
  recovery, SO_REUSEPORT graceful restart).

[0.1.0]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.0
