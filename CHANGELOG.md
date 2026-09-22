# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-09-22

Hardening release: safe multi-instance operation, bounded resource usage, and a
full Chinese user interface. No new terminal features.

### Added

- **Multi-instance safety.** A per-process lease (`instances` table plus
  `term_sessions.instance_id`) means two servers sharing one data directory
  (e.g. during an `SO_REUSEPORT` handover) never reconcile or reap each other's
  sessions. `--reuse-port` restarts are now correct even with live sessions.
- tmux sessions are namespaced per data directory, so two deployments on the
  same host sharing one tmux server cannot see or kill each other's sessions.
- **Bounded retention.** `audit_retention_days` (default 30) and
  `term_session_retention_days` (default 30) prune the audit log and finished
  session rows; `recordings_retention_days` and `recordings_max_bytes` bound
  recordings on disk.
- `max_sessions_per_user` for per-user session quotas.
- `metrics_require_auth` to require authentication for `GET /metrics`.
- `instance_ttl` controls how long another instance's lease may go unheard.
- `GET /api/sessions/{id}/share` returns the active share token so the UI can
  reuse an existing link instead of silently invalidating it.
- CLI: `wsctl --version`, `wsctl doctor` (environment/preflight checks),
  `wsctl user disable|enable`, `wsctl session rename`. `wsctl config set` now
  rejects unknown keys and suggests the closest match.
- Web UI: a session list (`会话`) to reopen detached sessions or kill them, a
  tab context menu (right-click, or long-press on touch devices), share expiry
  (TTL) selection and link regeneration, and an optional TOTP field on the
  login form.
- `contrib/systemd/wsctl.service` and `contrib/nginx/wsctl.conf` deployment
  examples.

### Changed

- **Closing a tab now detaches (keeps the session running) instead of killing
  it.** This matches the project's core promise that a session is independent
  of its connections. Use the tab context menu, the session list, or
  `Alt+Shift+W` to actually terminate a session; `Alt+W` detaches.
- The entire web UI and CLI help/output are now in Chinese (`lang="zh-CN"`),
  including server API/WebSocket error messages surfaced to the user.
- Live WebSocket terminals now re-validate the login session every few seconds
  and close (`4401`) when the token is revoked/expired or the user is disabled,
  instead of staying connected until the tab is closed.

### Fixed

- **Sessions could leak when a client could not accept the reconnect replay:**
  a session created during the WebSocket handshake is now removed if attaching
  fails, and a client that rejects the replay is no longer left registered in
  the session's client set.
- **Startup race between instances:** `_ensure_admin` no longer crashes with a
  `UNIQUE constraint` error when two instances bootstrap the same database
  concurrently.
- **Schema migration ordering:** the `instance_id` index is created after the
  `ALTER TABLE`, so upgrading an existing 0.1.0 database no longer fails with
  `no such column: instance_id`.
- `RateLimiter` no longer accumulates keys with no recent failures (a slow
  memory leak under credential-stuffing).
- Expired sessions are reaped concurrently instead of one at a time.
- Sessions abandoned by a **crashed** instance are reclaimed/adopted
  immediately (same-host pid check) rather than waiting for the lease TTL, and
  reconciliation now runs on every maintenance tick, so a peer's sessions are
  taken over as soon as it exits instead of only on the next restart.
- Regenerating a share link now asks for confirmation first, so an already
  distributed link is not invalidated by a stray click.
- Multi-instance liveness probing is POSIX-only: `os.kill(pid, 0)` is not a
  liveness check on Windows (CPython maps it to `TerminateProcess`), so the
  lease falls back to the heartbeat TTL there.
- A paused instance whose lease was pruned by a peer re-registers itself on the
  next maintenance tick (idempotent upsert) instead of staying unregistered.
- A command that cannot be started (bad `--command`/`--new`, missing binary) now
  returns a clean `400 无法启动命令` instead of a `500`, and a bad `--new`
  command no longer prevents the server from starting.

### Testing

- New `test_instances.py` (leases, per-instance reconciliation, retention
  purging), plus tests for rate-limiter eviction, recording capacity, per-user
  quotas, share reuse, `metrics_require_auth`, `within_user_quota`, attach
  rollback, and the new CLI commands.
- New backend e2e scenario `multiplex`: two instances share one data directory
  and must not mark each other's sessions stopped.
- Browser test now covers detach-on-close and share-link reuse.

[0.1.1]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.1

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
- Third-party front-end assets ship with their license texts
  (`src/wsctl/static/vendor/THIRD_PARTY_NOTICES.txt`, `THIRD_PARTY_NOTICES.md`).
- GitHub Actions CI (ruff, mypy, pytest on Python 3.11–3.13), a dedicated
  browser-test job and a backend end-to-end job, plus a release workflow using
  PyPI Trusted Publishing that also creates a GitHub Release.

### Fixed

- tmux-backed sessions now default `TERM` (xterm-256color), so they work in
  headless environments where `TERM` is unset (previously the tmux client
  exited immediately and restart recovery failed).
- A tmux client is never signalled as a process group on shutdown, so the
  freshly forked tmux server (and the preserved session) cannot be killed.
- Revoked/expired share tokens are now enforced promptly: input is rejected
  immediately and the connection is closed, and a periodic re-check detaches
  viewers even on sessions that produce no output.
- Read-only clients can no longer resize the shared terminal at attach time.
- Authorization failures during the WebSocket handshake are delivered as close
  codes (4401), so clients stop reconnecting instead of looping forever.
- File upload opens the destination with `O_NOFOLLOW` (race-free symlink guard).
- Config hot-reload treats an explicitly-empty environment variable as set.
- Web UI: `localStorage.setItem` is guarded, and the record button reflects the
  actual recording state on load and when switching tabs.
- PTY no longer leaks the master fd or the child process when a spawn fails,
  and out-of-range terminal dimensions are clamped instead of raising.
- The PTY write buffer is bounded, so a child that stops reading stdin cannot
  grow server memory without limit.
- Sessions are stopped if post-create setup fails (REST and WebSocket paths),
  instead of leaking a live process with no metadata.
- Disabled users' existing sessions are invalidated.
- Empty commands (400), out-of-range dimensions (422) and duplicate users (409)
  are rejected cleanly instead of erroring out.
- CSP allows `blob:` so the recording player actually loads its cast, and
  `connect-src` was tightened to `'self'`.
- Web UI: guarded `localStorage` parsing, reset the screen on reconnect (no
  duplicated scrollback), share "revoke" targets the session the dialog was
  opened for, read-only viewers can no longer trigger the login modal, and the
  replay player/blob URL is disposed.
- WebSocket teardown now drains queued control messages before closing.
- WebSocket auto-created sessions record `owner_id` and persist metadata.
- `safe_resolve` rejects absolute paths instead of silently re-rooting them.
- The share QR endpoint emits a standalone SVG (with `xmlns`) so it renders
  inside an `<img>`; the previous inline SVG was blank in browsers.
- CSP allows `'wasm-unsafe-eval'` and `worker-src blob:` so the recording player
  can run.

### Testing

- 149 unit/integration tests; concurrency and large-output load tests are opt-in
  (`pytest -m slow`), browser tests are opt-in (`pytest -m browser`), and a
  120-second per-test timeout guards against hangs.
- Browser-level end-to-end tests with Playwright (`wsctl[e2e]`) covering login,
  terminal I/O, multi-tab, hotkeys, file panel, sharing with QR, recording
  replay, theme switching, read-only input blocking and anonymous share links;
  run in CI in a dedicated job.
- `scripts/e2e/run_all.py` runs five backend end-to-end scenarios (server, CLI,
  `connect`, tmux restart recovery, SO_REUSEPORT graceful restart); it runs in
  CI in a dedicated job.

[0.1.0]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.0
