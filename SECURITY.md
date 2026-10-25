# Security Policy

## Threat model

wsctl is a **remote code execution service by design**. Anyone who can reach a
session can run commands as the server's user. Treat it like SSH:

- never expose it to the public internet without authentication and TLS
- run it as a dedicated, least-privileged user
- restrict source networks with `allowed_ips` where possible
- prefer a reverse proxy for TLS (`trust_proxy = true`,
  `trusted_proxies = ["<proxy CIDR>"]`, `cookie_secure = true`)

## Built-in protections

- Argon2 password hashing and server-side session tokens (HttpOnly cookies)
- **Minimum password policy** (non-empty, at least 8 characters), enforced in
  one shared validator at the storage boundary so the HTTP API and the CLI
  cannot disagree. Existing accounts are not force-changed; `wsctl doctor`
  reports which ones are below the bar rather than locking anyone out
- Optional TOTP two-factor authentication (available from both the web UI and
  the CLI — `wsctl login --totp` / `WSCTL_TOTP`). Enrolment is **two-step** on
  every surface: the secret becomes active only after a code from the
  authenticator verifies, so a scan that never happens can never lock the
  account out of its own login.
- Login rate limiting per `IP + username` **and per IP** (`429` + `Retry-After`),
  with an 8 KiB body cap checked from `Content-Length` before the body is read
  and a bounded number of concurrent Argon2 verifications (a login storm cannot
  become an unbounded queue of threads)
- **`X-Forwarded-For` is only read from a trusted peer** (loopback, plus any
  `trusted_proxies` CIDR) and only when `trust_proxy = true`; it is read from the
  rightmost untrusted hop. The header is *appended* to by every proxy (the nginx
  example uses `proxy_add_x_forwarded_for`), so the leftmost entry is whatever
  the client sent. uvicorn's own `proxy_headers` is turned **off**: its default
  rewrites `scope["client"]` before wsctl sees the request, which made
  `trust_proxy = false` meaningless for any local process.
- **Same-origin check on every non-GET `/api/*` request** (`Origin`, falling
  back to `Sec-Fetch-Site`). The cookie is `SameSite=Lax`, which does not stop a
  same-site page (another port, or a sibling subdomain); file upload is a CORS
  *simple request*, so only this check stands between such a page and
  `~/.ssh/authorized_keys`. Non-browser clients send no `Origin` and are
  unaffected.
- CIDR IP allowlist for HTTP and WebSocket
- Origin allowlist on WebSocket handshakes (anti-CSWSH)
- WebSocket admission before the handshake frame: a 5-second attach timeout, a
  process-wide and per-IP connection cap, and token buckets for control frames
  and `resync` (a replay is not free)
- Disabling, deleting or re-password-ing an account terminates that account's
  running terminals and revokes its share links — deleting the login tokens
  alone left a live shell (and a writable share) behind. The API does it
  immediately; the maintenance loop catches changes made by another process
  (`wsctl user disable`) within one tick.
- Credential-shaped variables (`WSCTL_ADMIN_PASSWORD`, `WSCTL_TOKEN`,
  `WSCTL_TOTP`, and any `WSCTL_*_PASSWORD`/`_TOKEN`/`_SECRET`) are stripped from
  every terminal's environment, and the bootstrap `WSCTL_ADMIN_PASSWORD` is
  removed from the server's own environment as soon as it is consumed (it used
  to be inherited by every shell opened from the panel). Any *non-credential*
  `WSCTL_*` variable survives -- `WSCTL_DATA_DIR`, `WSCTL_PORT`, `WSCTL_CONFIG`
  and friends -- because a `wsctl` command typed inside a session has to
  resolve the same instance.
- Traversal-proof file panel rooted at a configurable directory. Renames are
  atomic-and-non-clobbering (`os.link` + unlink) and uploads edit through a
  unique `O_EXCL` sidecar, so two concurrent same-name operations cannot
  interleave or silently destroy each other. Symlink guards act on the *link*,
  not on whatever it points at.
- Upload size limits and session count/idle/lifetime limits
- Uploads never silently replace an existing file: a name collision is refused
  (`409`) unless the client explicitly opts in to overwriting
- Full audit log of logins, sessions, file transfers and admin actions
- Security response headers (nosniff, frame DENY, referrer policy, CSP)
- Bounded resources: audit/session/recording retention, per-user session quotas,
  bounded directory listings, bounded per-client and per-session buffers
- A dropped keystroke is reported rather than silently discarded, so input
  backpressure cannot masquerade as a dead terminal
- Optional authentication on `/metrics` (`metrics_require_auth`)
- Multi-instance leases so a peer sharing the data directory can never reconcile
  or reap another instance's sessions
- The CLI enforces the same user-management invariants as the API (the last
  admin cannot be demoted/disabled/deleted, and a password change revokes that
  user's logins), so local administration cannot lock the instance out
- Consistent backups via SQLite's online backup API; a restore refuses to run
  while an instance lease is alive (a running server holds its own handle),
  validates the archive database with `PRAGMA integrity_check`, replaces the
  file atomically, deletes the stale `-wal`/`-shm` sidecars, keeps the previous
  database as a consistent `.bak`, refuses to overwrite without `--force`, and
  rejects archive members that try to escape the data directory
- The background lifecycle verifies a process-identity fingerprint before
  signalling a PID, so it never kills an unrelated process that reused the PID
- Log rotation uses copy-and-truncate, so the daemon's inherited append-mode
  descriptor keeps writing to the live file across a rotation
- Session history and detail expose ``argv`` and ``cwd``, which can contain
  sensitive paths. An admin sees every row; a normal user sees only their own,
  and the read path enforces that (403 otherwise). This is the same visibility
  the session list already had -- it is called out here because the history is
  retained for ``term_session_retention_days`` and therefore outlives the
  session itself
- The file panel refuses recursive delete on purpose: only a file, or a
  directory that is already empty, can be removed. Emptying a tree is several
  deliberate acts rather than one misplaced click
- Share links are unguessable, optionally time-limited, revocable, and refused
  input when read-only. They are persisted alongside the session (schema v4;
  current is v5), so a link keeps working across a server restart — the same
  promise the session itself makes. A rename never invalidates a distributed
  link.
- Inline image preview decides the media type from the file's **leading bytes**,
  never from its name, and serves it with `nosniff`. Renaming a script to
  `.png` therefore does not make it an image and does not make it executable.
  SVG is deliberately not served inline: it is a document with a scripting
  surface, and the panel previews it as text (which is what it is).
  The CSP's `img-src` carries `blob:` for this (an object URL, so an 8 MB
  picture is not base64-inflated into one string); a `blob:` URL can only name
  bytes this origin already holds, which is why that is not a widening a
  script could exploit.
- Online configuration editing goes through the same writer as `wsctl config
  set` (`core/config_edit`): the value is type-checked against the settings
  model, the whole resulting file is validated, and a rejected edit is rolled
  back rather than published. Only fields a reload can reach are writable; a
  `restart`-tier field is refused with that reason instead of being accepted
  and silently doing nothing. Every write is audited (`config_set`).

## Supported versions

Only the latest release is supported with security fixes while the project is
pre-1.0.

## Upgrading and downgrading

Schema migrations are idempotent and run automatically at startup. Before
**downgrading** to an older release, take a backup (`wsctl backup`) — a newer
release may have written columns the older one does not know about (0.1.4 added
`term_sessions.share_token` / `share_expires` / `share_writable` so share links
survive a restart; 0.1.30 added `users.password_changed_at`, schema v5, so a
password rotation can end terminals opened under the old credential).

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, use
GitHub's private vulnerability reporting on
[ThzxxArt/wsctl](https://github.com/ThzxxArt/wsctl/security/advisories/new), or
contact the maintainer directly.

Include a description, reproduction steps, and the affected version. You can
expect an acknowledgement within a few days. Please allow time for a fix before
public disclosure.
