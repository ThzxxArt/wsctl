# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.5] - 2026-09-22

Windows portability and test-independence release. Two of the three fixes below
are real product bugs that made the CLI unusable on Windows; the rest make the
suite mean what it says on every platform. No new features, no schema change.

### Fixed

- **`import wsctl.core.pty` no longer crashes on Windows.** `Pty.terminate`
  declared `sig: int = signal.SIGHUP` as an *annotation default*, which Python
  evaluates at definition time -- and `signal.SIGHUP` does not exist on
  Windows, so merely importing the module raised `AttributeError`. The signal is
  now resolved once into `DEFAULT_TERM_SIGNAL` (`SIGHUP` where it exists,
  `SIGTERM` otherwise).
- **Chinese output no longer crashes a default Windows console.** Such a
  console is cp1252, so writing the help text or `wsctl doctor` through it
  raised `UnicodeEncodeError` before anything appeared. The CLI now
  reconfigures stdout/stderr to UTF-8 with a replacement fallback at startup: a
  terminal that truly cannot render a glyph degrades to `?` instead of dying.
- `fs.relative_to` returns POSIX separators on every platform. Those strings
  land in audit payloads and share links, so the same upload used to look like
  `a\b.txt` on one host and `a/b.txt` on another.

### Testing

- `test_session_list_json_is_machine_readable` is hermetic: it passed locally
  only because the developer's `~/.config/wsctl/credentials.json` gave the CLI
  a URL to fall back on, and failed on a clean runner. It now isolates
  `XDG_CONFIG_HOME` and passes `--url` explicitly.
- The PTY input-drop tests stub the write-buffer drain instead of filling a
  real PTY after `SIGSTOP`. How many bytes the kernel soaks up first is
  platform-specific (macOS takes far more than Linux), so the old version was
  measuring the kernel rather than wsctl.
- The browser rename step asserts the outcome (tab label and server-side name)
  rather than "a PATCH must be in flight while this context manager is open",
  which was timing-dependent, and guards a `querySelector` that can legitimately
  be null while the admin table renders.

[0.1.5]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.5

## [0.1.4] - 2026-09-22

Reliability and usability release. Every promise the product makes now holds
across a restart, a broken disk and a broken config edit; the CLI can log in
with 2FA and script against JSON. No new terminal protocols.

### Added

- **CLI can log in to a two-factor account.** `wsctl login --totp 123456`, the
  `WSCTL_TOTP` environment variable, or a prompt when the server asks for a
  code. Previously a 2FA-enabled account made every CLI command unusable.
- **Share links survive a server restart.** The token, its expiry and its
  writable flag are persisted with the session (`term_sessions.share_*`,
  schema version 4) and rehydrated verbatim on adoption, so a QR code that was
  already handed out keeps working — the same promise the session itself makes
  on the tmux backend. Renaming a session no longer invalidates its link.
- **Minimum password policy** (non-empty, at least 8 characters), enforced in
  one place (`core/passwords.validate_password`) at the storage boundary, in
  `user_admin`, and therefore by both the HTTP API and the CLI. Existing
  passwords are *not* force-changed; `wsctl doctor` warns instead of locking
  anyone out.
- **Log rotation** for the background lifecycle. `log_file` / `log_max_bytes` /
  `log_backup_count` rotate by copy-and-truncate, so the append-mode descriptor
  `wsctl start` hands its child stays valid across a rotation (rename-based
  rotation would leave the daemon writing into the rotated file forever).
- **Web new-session dialog** with name, command, working directory, backend and
  a structured SSH target form (host / user / port / identity / `ssh -o`
  options). `Alt+N` still opens a default shell with one keystroke.
- **Web admin "config" tab**: the effective configuration, each key labelled
  `热更新` or `需重启`, plus a reload button that reports exactly what changed
  *and* what failed to parse.
- **`wsctl completion install|show [bash|zsh|fish|powershell]`**. Typer's
  `--install-completion` only ever installs bash; this covers the rest and
  picks the shell from `$SHELL` instead of guessing bash.
- `GET /api/config` (admin) — the read-only effective-configuration view behind
  the new tab.
- CLI `--json` on `session list|new|rename`, `user add|list`, `audit` and
  `config show|get`; `wsctl audit` gained `--event` / `--user-id` / `--ip` filters;
  `wsctl session list` now shows the owner's **username** (and the backend and
  liveness) instead of a bare numeric id.
- `wsctl logs` reads across rotated backups, so a rotation never hides history.
- `wsctl doctor` additionally reports free disk space on the data partition,
  the password policy in force, the log file and its rotation threshold, and
  the system clock (which TOTP depends on).
- Metrics `wsctl_pty_input_dropped_total` and `wsctl_recording_failures_total`.
- Uploads can opt in to replacing an existing file (`overwrite=1`); the web UI
  asks first.
- File listings report `truncated` and `limit` when a directory exceeds 2000
  entries instead of silently returning everything.

### Changed

- **A broken config edit is no longer silent.** `reload_settings_file` returns
  `(changed, errors)`; `wsctl reload` and `POST /api/config/reload` surface the
  exact TOML/validation error and exit non-zero, and the server logs it.
  Previously an invalid value was discarded and the reload reported "no
  changes", which was indistinguishable from the reload not running.
- **`config set` preserves trailing comments** on the line it edits
  (`port = 7681  # 监听端口` stays annotated) and no longer mistakes a
  commented-out `# port = 1234` for the real key.
- **Web terminal connections are bounded.** At most `8` sessions hold a live
  socket (the most recently used ones); the rest appear in the tab bar as
  `未连接` and attach when opened. Previously every session opened a WebSocket
  and an xterm instance at page load, which fell over past a handful.
- Uploading over an existing file is refused with `409` unless the client
  explicitly opts in. Files were previously truncated without warning.
- The image in `contrib/docker` builds from the **repository root** and installs
  the local source; `docker compose` refuses to start without
  `WSCTL_ADMIN_PASSWORD` instead of defaulting to `change-me`.
- CI adds a macOS leg and a Windows import/CLI smoke job, runs the `-m slow`
  load suite, installs `lrzsz` so the ZMODEM round trip is really exercised,
  and builds the Docker image.

### Fixed

- **A session whose child never exits can no longer hang the server.**
  `TermSession._finalize` waited unboundedly on `Pty.wait()`; a daemonized
  grandchild holding the terminal open would wedge the read loop's cleanup
  forever and leak the session object. The wait is now bounded and escalates to
  `kill`.
- **A failed recording no longer claims to be recording.** When the writer hit a
  disk error it silently stopped while `is_recording` stayed true, so the UI
  showed a red dot for a file that was not being written. The recorder now
  closes itself, records the error, and `close()` can no longer race the writer
  thread into a half-closed file.
- **`import wsctl.core.store` no longer computes an Argon2 hash**, which made
  every CLI invocation — including `wsctl --version` — pay ~120ms before doing
  anything. The timing-equalising dummy hash is computed lazily; combined with
  lazy imports of the server/client dependencies, `wsctl version` went from
  ~1.05s to ~0.36s.
- **Authentication lookups no longer run on the event loop.** The WebSocket
  handshake and its periodic re-check did synchronous SQLite work per
  connection every 5s, as did the maintenance loop's leases/reaping/retention.
  They now run on worker threads, and a short-lived auth cache
  (`core/authcache.py`) turns the re-check into a dict lookup. Revocation stays
  immediate: disabling a user, changing a password or changing a role drops that
  user's cache entries at once.
- **A dropped keystroke is now reported.** When a session's child stops
  draining its terminal the write path drops input rather than grow without
  bound — but it did so *silently*, which looked like a dead keyboard. The
  client is told once (`终端输入过快，部分按键已丢弃`) and the drops are counted.
- **A stale `attached` message can no longer revert a rename.** The tab label
  is protected by a rename generation counter, so a reconnect that echoes the
  pre-rename name does not silently undo what the user just typed.
- **`promptDialog`/`confirmDialog` are singletons.** A second invocation while
  one was open overwrote the input the user was typing into; that is how a
  rename could send the *old* name back to the server and look lost.
- **A finished ZMODEM transfer always releases the keyboard.** The input lock
  was only cleared by the transfer's end event, so an aborted transfer left the
  terminal accepting no input at all. An inactivity watchdog now force-releases
  it (and `on_retract` does too).
- `wsctl connect` survives non-JSON text frames (a proxy or middlebox injecting
  one used to tear the connection down with a traceback).
- **Maintenance reconciliation can no longer mis-kill a live session.**
  `term_session_stop_missing` takes the in-memory session set as its liveness
  list; running its `UPDATE` on a worker thread after taking that snapshot
  widened the window to thread-scheduling latency, so a session created in
  between would be written as `stopped` while running (and never adopted again
  after a restart). The snapshot and that one statement stay in a single
  synchronous stretch on the loop; only snapshot-independent writes are
  threaded.
- **Argon2 hashing no longer runs under the store lock.** `user_create` and
  `user_set_password` hashed while holding the single store mutex, serialising
  every other database operation — including authentication — behind one
  ~100ms CPU-bound hash. The hash is now computed before the lock is taken.
- **Audit input records only what actually reached the shell.** Keystrokes
  dropped by write backpressure (or a session that had already exited) used to
  be audited as if the command had run. Dropped input is now reported to the
  user and simply not recorded.
- The terminal connection indicator follows the *visible* tab: a background
  tab reconnecting or being suspended no longer rewrites it to
  "连接中"/"空闲".
- Metrics named `_total` (`wsctl_audit_dropped_total`,
  `wsctl_audit_write_errors_total`, `wsctl_pty_input_dropped_total`,
  `wsctl_recording_failures_total`) are now exposed with `# TYPE … counter`.
  They were declared `gauge`, which makes `rate()` reject them. The two new
  ones are also genuinely monotonic — a per-session sum would have gone *down*
  whenever a session ended, showing up as counter resets.
- Metric label values are escaped for the Prometheus text format (a `"` or
  newline in a label produced invalid exposition output).
- Scrollback eviction no longer leaves an orphaned UTF-8 continuation byte at
  the head of a replayed screen (rendered as a replacement glyph).
- `wsctl doctor` no longer imports the hashing stack just to print a table.

### Testing

- New suites for the password policy, the auth cache (including revocation
  guarantees), copy-and-truncate log rotation, recording-failure honesty,
  scrollback UTF-8 alignment and metric label escaping; extended suites for
  share persistence (including "a rename must not invalidate the link"),
  the bounded `Pty.wait`, PTY input drops, the upload overwrite guard, the
  config endpoint and reload errors, TOTP/`--json`/comment-preserving
  `config set`, and `completion show`.
- The browser suite covers the new-session dialog (including the SSH-required
  validation), the config tab, the upload overwrite prompt, the bounded
  connection budget, and a **real ZMODEM `sz`/`rz` byte-exact round trip**.
- `scripts/e2e/run_all.py` asserts that a share link keeps working across a
  full server restart, and reports the child's log when a scenario fails to
  start.

### Upgrade notes

- The database migrates to schema version 4 (adds `share_token`,
  `share_expires`, `share_writable` to `term_sessions`). The migration is
  idempotent, but **back up before downgrading** to 0.1.3 or older —
  `wsctl backup` first, since the older release does not know about these
  columns.
- New accounts and password changes must be at least 8 characters. Existing
  accounts keep working; `wsctl doctor` reports which ones are below the bar.

[0.1.4]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.4

## [0.1.3] - 2026-09-22

Operations and correctness release: a background lifecycle that needs no
systemd, strict validation of mutually exclusive options, and the last blocking
paths on the event loop removed. No new terminal protocols.

### Added

- **Background lifecycle without systemd**: `wsctl start` / `stop` / `restart` /
  `status` / `logs` / `reload`, plus `wsctl serve --daemon`. The child is
  detached (`start_new_session`), its output goes to a per-port log file, and a
  pid file carries a **process-identity fingerprint** (Linux start time, `ps`
  elsewhere) so a recycled PID is never signalled. `start` refuses to clobber a
  live instance (`start --force` replaces a running one); `status --json` is
  machine-readable; `reload` sends `SIGHUP`, which the server now handles (in
  addition to the mtime watcher).
- **Half-open connection detection**: a WebSocket that has sent nothing for
  `IDLE_TIMEOUT` (120s) is closed with code `4408`. Both clients ping every 25s,
  so a peer that vanished without a FIN is reclaimed instead of lingering.
- **Declarative CLI validation** (`wsctl/cli/validate.py`): options that cannot
  be combined or that depend on each other now fail fast with exit code 2 and an
  actionable message — `--daemon` vs `--reuse-port`, `--ssl-cert`/`--ssl-key`,
  `serve --backend ssh`, `session new --ssh` vs `--backend`, `--ssh-*` without
  `--ssh`, unknown roles, and more. `config set` now type-checks the value
  against the settings model and validates the whole file before writing.
- **One set of user invariants for both surfaces** (`wsctl/core/user_admin.py`):
  the CLI can no longer disable, demote or delete the last admin (which could
  lock the instance out), and `wsctl user passwd` now revokes that user's
  existing logins just like the API does.
- **Consistent backups and restores**: `backup` uses SQLite's online backup API
  (a WAL-mode database copied as a plain file could miss recent commits), and
  `restore` validates the archive, refuses to overwrite without `--force`
  (keeping a `.bak`), and rejects path-traversal members.
- `wsctl connect` reconnects automatically with exponential backoff and
  re-attaches to the same session (server-side replay restores the screen);
  `--no-reconnect` opts out.
- `doctor` now reports the running instance, port availability, `SO_REUSEPORT`
  support and background-start capability.
- Metrics `wsctl_audit_write_errors_total` and `wsctl_maintenance_lag_seconds`.

### Changed

- **WebSocket close codes are now meaningful**: `4400` bad request, `4401`
  unauthenticated, `4403` forbidden, `4404` session not found, `4409` limit
  reached, `4500` server error. The web client stops reconnecting on a permanent
  failure instead of looping, and a missing session no longer pops the login
  dialog at an already-authenticated user.
- Session ids are hex (`token_hex`) so they can never start with `-` and be
  mistaken for a CLI option.
- `--reuse-port` now fails loudly when `SO_REUSEPORT` is unavailable instead of
  silently binding without it (a silent downgrade would defeat zero-downtime
  handovers).

### Fixed

- **Argon2 verification and hashing no longer run on the event loop**: login,
  user creation and password changes run on a worker thread, so a login storm
  cannot stall every terminal.
- `Recorder.close()` (which joins its writer thread) no longer blocks the event
  loop; retention purging, recording-capacity checks and directory listings run
  on worker threads.
- The audit writer survives a failing database write (it logs, counts and keeps
  going instead of dying silently), and concurrent `flush()` calls can no longer
  reorder events.
- `serve --new` with a `tmux` backend that is not installed no longer crashes the
  server at startup.
- The web UI's Esc key closes the topmost modal (previously it could close the
  admin panel behind the QR dialog), the QR dialog is a normal, focus-trapped
  modal, password reset uses a masked input, and assigning a hotkey that is
  already taken is rejected with a warning.

### Testing

- New suites for the validation layer, the pid-file lifecycle, consistent
  backups/restores, and the shared user-admin invariants; WebSocket close-code
  tests; audit-writer failure and ordering tests; a `daemon` end-to-end scenario;
  and browser coverage for the 2FA QR modal.

[0.1.3]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.3

## [0.1.2] - 2026-09-22

Polish and robustness release: disk/DB I/O is moved off the event loop, the web
UI gains an admin surface and a design system, and the CLI gets a few
conveniences. No new terminal protocols.

### Added

- **Admin surface in the web UI**: user management (create, role, enable/disable,
  password reset, TOTP with an inline QR code), an audit log viewer with filters
  and pagination, and recording management (list / replay / download / delete).
- **Terminal search** over the buffer (toolbar button or `Ctrl+Shift+F`).
- **Toasts**, a unified modal component, an inline rename dialog, and a
  confirmation before terminating a session.
- Font-family selection, `Ctrl+Shift+C` / `Ctrl+Shift+V` copy/paste (and `Ctrl+C`
  to copy a selection, right-click to paste), and "follow system" light/dark
  theme.
- Session list enhancements: uptime / idle / buffer size, name filter, and
  "detach all".
- A `wsctl_audit_dropped_total` metric for the async audit queue.
- A **design system** (tokens + components), an icon-based toolbar with a
  mobile overflow menu, theme-gallery colour previews, empty/loading skeleton
  states, a branded login screen, `favicon.svg`, and a PWA manifest.
- README screenshots under `docs/screenshots/`.
- CLI: `config get` / `config validate`, `doctor --json`, `backup`, an inline
  TOTP QR code in `user totp`, and shell completion.
- `contrib/docker/` (Dockerfile + compose).
- `session_sliding_ttl` to keep an active login alive.

### Changed

- **Audit logging is now asynchronous**: events are queued and written in
  batches off the event loop, so `audit_input` and connection events no longer
  add latency to other sessions.
- Recording writes run on a background thread instead of the event loop.
- File uploads are written on a worker thread instead of the event loop.
- `resolve_auth_session` throttles its `last_seen` write (every 30s) instead of
  writing on every request.
- tmux probing is asynchronous and only runs when tmux-backed sessions exist.
- Webhook delivery retries with exponential backoff.

### Fixed

- A client dropped by per-session memory backpressure now has its socket closed
  and can no longer inject input, instead of lingering.
- The audit input line buffer is bounded.
- The last enabled admin can no longer be demoted, disabled or deleted, and an
  admin cannot disable or delete itself (prevents locking the instance out).
- Changing a user's password (or disabling the user) now revokes that user's
  existing login sessions.
- A failed upload no longer leaves a partially written file behind.
- Unified modals close on `Esc` or a backdrop click and trap keyboard focus.
- Enabling TOTP for a user is now audited.

### Testing

- New tests for the async audit writer, admin APIs (users / audit / recordings),
  TOTP endpoints, `last_seen` throttling, and sliding TTL.
- Browser test extended to cover the admin panel, terminal search, the inline
  rename dialog, font selection and the "follow system" theme.

[0.1.2]: https://github.com/ThzxxArt/wsctl/releases/tag/v0.1.2

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
