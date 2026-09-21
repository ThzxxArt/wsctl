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
- Optional TOTP two-factor authentication
- Login rate limiting per IP + username (`429` + `Retry-After`)
- CIDR IP allowlist for HTTP and WebSocket
- Origin allowlist on WebSocket handshakes (anti-CSWSH)
- Traversal-proof file panel rooted at a configurable directory
- Upload size limits and session count/idle/lifetime limits
- Full audit log of logins, sessions, file transfers and admin actions
- Security response headers (nosniff, frame DENY, referrer policy, CSP)

## Supported versions

Only the latest release is supported with security fixes while the project is
pre-1.0.

## Reporting a vulnerability

Please **do not** open a public issue for security problems. Instead, use
GitHub's private vulnerability reporting on
[ThzxxArt/wsctl](https://github.com/ThzxxArt/wsctl/security/advisories/new), or
contact the maintainer directly.

Include a description, reproduction steps, and the affected version. You can
expect an acknowledgement within a few days. Please allow time for a fix before
public disclosure.
