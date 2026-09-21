# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
