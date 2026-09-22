# Security Policy

## Threat model

wsctl is a **remote code execution service by design**. Anyone who can reach a
session can run commands as the server's user. Treat it like SSH:

- never expose it to the public internet without authentication and TLS
- run it as a dedicated, least-privileged user
- restrict source networks with `allowed_ips` where possible
- prefer a reverse proxy for TLS (`trust_proxy = true`, `cookie_secure = true`)

## Built-in protections

- Argon2 password hashing and server-side session tokens (HttpOnly cookies)
- **Minimum password policy** (non-empty, at least 8 characters), enforced in
  one shared validator at the storage boundary so the HTTP API and the CLI
  cannot disagree. Existing accounts are not force-changed; `wsctl doctor`
  reports which ones are below the bar rather than locking anyone out
- Optional TOTP two-factor authentication (available from both the web UI and
  the CLI — `wsctl login --totp` / `WSCTL_TOTP`)
- Login rate limiting per IP + username (`429` + `Retry-After`)
- CIDR IP allowlist for HTTP and WebSocket
- Origin allowlist on WebSocket handshakes (anti-CSWSH)
- Traversal-proof file panel rooted at a configurable directory
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
- Consistent backups via SQLite's online backup API; restoring refuses to
  overwrite an existing database without `--force` and rejects archive members
  that try to escape the data directory
- The background lifecycle verifies a process-identity fingerprint before
  signalling a PID, so it never kills an unrelated process that reused the PID
- Log rotation uses copy-and-truncate, so the daemon's inherited append-mode
  descriptor keeps writing to the live file across a rotation
- Share links are unguessable, optionally time-limited, revocable, and refused
  input when read-only. They are persisted alongside the session (schema v4), so
  a link keeps working across a server restart — the same promise the session
  itself makes. A rename never invalidates a distributed link.

## Supported versions

Only the latest release is supported with security fixes while the project is
pre-1.0.

## Upgrading and downgrading

Schema migrations are idempotent and run automatically at startup. Before
**downgrading** to an older release, take a backup (`wsctl backup`) — a newer
release may have written columns the older one does not know about (0.1.4 added
`term_sessions.share_token` / `share_expires` / `share_writable` so share links
survive a restart).

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, use
GitHub's private vulnerability reporting on
[ThzxxArt/wsctl](https://github.com/ThzxxArt/wsctl/security/advisories/new), or
contact the maintainer directly.

Include a description, reproduction steps, and the affected version. You can
expect an acknowledgement within a few days. Please allow time for a fix before
public disclosure.
