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

**Restart-safe sessions**
- Optional `tmux` backend (`default_backend`, `--backend tmux`): the shell runs
  inside a tmux session and survives a full wsctl restart, then is reattached
  automatically with the same id and a redrawn screen.
- Graceful shutdown preserves tmux sessions (`tmux_preserve_on_shutdown`); an
  explicit kill still tears them down.

**Read-only sharing**
- Per-session share tokens (`POST/DELETE /api/sessions/{id}/share`) that are
  unguessable, optionally time-limited and revocable.
- Anonymous read-only viewers can attach with a share link (`?session=&share=`)
  without an account; their input is refused and the UI shows a read-only badge.
- QR code for a share link (`GET /api/sessions/{id}/qr.svg`) plus a share dialog
  in the web UI.

**SSH sessions**
- `backend="ssh"` builds a safe `ssh` argv from a structured target
  (host/user/port/identity/options/remote command); `wsctl session new --ssh`.
- Hosts and users are validated and passed as separate arguments (no shell
  interpolation).

**Session recording**
- asciinema cast v2 recording (`core/recording.py`), optionally including input.
- Per-session start/stop/download endpoints and `auto_record` / `record_input`
  settings; CLI `session record`, `record-stop` and `recording`.

**Zero-downtime restarts**
- `reuse_port` / `--reuse-port` binds with `SO_REUSEPORT` so a new instance can
  take over the port before the old one exits (no connection-refused window).

**Miscellaneous**
- `settings` table and full `term_sessions` columns (`argv`, `env`,
  `idle_timeout`, `max_life`) with an idempotent schema migration.
- Optional webhooks (`webhook_url`): every audit event is POSTed as JSON by a
  background dispatcher.
- `wsctl config set KEY VALUE` (validated TOML) for editing the config file.
- Web UI preferences: dark/light theme and font size, persisted locally, plus
  responsive/mobile layout adjustments.

### Testing

- Browser-level end-to-end tests with Playwright (`pytest -m browser`, extra
  `wsctl[e2e]`) covering login, terminal I/O, multi-tab, hotkeys, file panel,
  sharing with QR, recording replay, theme switching and anonymous read-only
  share links.
- Fixed the share QR endpoint to emit a standalone SVG (with `xmlns`) so it
  renders inside an `<img>`; the previous inline SVG was blank in browsers.

### Added after M13

- **Read-write share links**: share tokens carry a writable flag; the share
  dialog has an "allow typing" option.
- **Recording replay UI**: record toggle + in-browser asciinema player; vendored
  `asciinema-player`, `@xterm/addon-image` and `zmodem.js`.
- **Configurable keyboard shortcuts** (Alt+N/W/←/→/F/S/H by default), editable
  and persisted locally.
- **Runtime config hot-reload**: file-mtime watcher, `POST /api/config/reload`
  and `wsctl config reload`; restart-only fields are ignored.
- **Per-session memory hard limit** and per-client byte cap
  (`session_memory_limit`, `client_max_bytes`); metric `wsctl_session_bytes`.
- **Sixel image rendering** via `@xterm/addon-image`.
- **Opt-in ZMODEM** (`sz`/`rz`) file transfer in the browser via `zmodem.js`.
- **Terminal theme marketplace**: 10 built-in themes plus custom JSON themes.
- CSP updated to allow `'wasm-unsafe-eval'` and `worker-src blob:` for the
  recording player.

**Packaging & CI**
- Hatchling packaging (PyPI: `wsctl`), MIT license, `py.typed`.
- GitHub Actions CI (ruff, mypy, pytest on Python 3.11–3.13) and a release
  workflow using PyPI Trusted Publishing.

### Fixed

- WebSocket teardown now drains queued control messages before closing.
- WebSocket auto-created sessions record `owner_id` and persist metadata.
- `safe_resolve` rejects absolute paths instead of silently re-rooting them.

[0.1.0]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.0
