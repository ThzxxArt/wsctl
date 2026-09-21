# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (M4 — file panel & observability)

- Web file panel: browse, download and upload (incl. drag-and-drop) within a
  configurable `file_root`, with traversal-proof path resolution and an upload
  size limit.
- Prometheus metrics at `/metrics`: uptime, live sessions/clients, sessions
  created, WebSocket connections, uploads and logins by result; toggleable via
  `metrics_enabled`.
- Structured JSON logging via `--log-json` / `log_json`.

### Added (M3 — audit & hardening)

- Full audit trail: login success/failure/TOTP failure/rate-limit, logout, IP
  rejection, session create/attach/detach/kill and user management, queryable via
  the admin-only `GET /api/audit` and the `wsctl audit` command.
- Optional submitted-input auditing (`audit_input`), disabled by default.
- Login rate limiting (sliding window per IP + username) returning `429` with
  `Retry-After`; successful logins reset the counter.
- IP allowlist (`allowed_ips`, CIDR-capable) enforced on HTTP and WebSocket, with
  optional `X-Forwarded-For` support behind a trusted proxy.
- TOTP two-factor authentication: enforced at login, managed with
  `wsctl user totp <user> [--disable]`.
- Security response headers (nosniff, frame DENY, referrer policy, CSP).

### Added (M2 — multi-session, RBAC, CLI)

- Per-session ownership and role-based access control: admins see and control every
  session, regular users only their own; enforced on the REST API and WebSocket attach.
- Multi-tab web UI: create/switch/close sessions, per-tab connection status,
  auto-reconnect with scrollback replay.
- Session idle-timeout and max-lifetime enforcement with a maintenance loop that
  also reconciles `term_sessions` metadata in the database.
- Admin-only user management API (`GET/POST/DELETE/PATCH /api/users`).
- CLI: `login` / `logout` and `session list` / `session kill` against a running
  server using a dependency-free HTTP client.

### Fixed

- WebSocket teardown now drains queued control messages (e.g. a final error) before
  closing, instead of cancelling the writer prematurely.

## [0.1.0] - 2026-09-21

### Added

- Initial project scaffolding (M0): packaging, CI, lint/type configuration.
- Design documentation (`DESIGN.md`).
