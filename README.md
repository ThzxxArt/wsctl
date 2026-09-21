# wsctl

[![CI](https://github.com/ThzxxArt/wsctl/actions/workflows/ci.yml/badge.svg)](https://github.com/ThzxxArt/wsctl/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> A modern, Python-based web terminal server — a [ttyd](https://github.com/tsl0922/ttyd)
> superset with **persistent sessions**, **multi-user RBAC**, **auditing** and
> **observability**.

`wsctl` serves terminals over the web from a single process. Start it with one
command, then reach a full shell from a browser or from the CLI. Terminal
**sessions are decoupled from connections**: closing your laptop does not kill
the shell, and reconnecting replays the screen.

```
Browser (xterm.js) ─┐
                    ├─ WebSocket ─┐
CLI (wsctl connect) ┘             │
                        ┌─────────▼──────────┐        ┌──────────────┐
                        │   SessionManager    │──PTY──▶  bash / ssh   │
                        │  (asyncio, 1..N)    │        └──────────────┘
                        │  scrollback replay  │
                        └─────────────────────┘
```

## Features

- 🖥️ **Multi-session** — one server hosts many terminals with a multi-tab UI
- 🔌 **Connection-independent sessions** — disconnect freely; reconnect replays
  the scrollback so your screen comes back
- ♻️ **Restart-safe sessions (optional)** — with the `tmux` backend a session
  survives a full server restart and is reattached automatically
- 👥 **Multi-user + RBAC** — admins manage everything, users only their sessions
- 🧑💻 **CLI thin client** — `wsctl connect` bridges your local terminal to a server
- 📁 **Web file panel** — browse, download and upload within a configured root
- 📝 **Auditing** — logins, sessions, file transfers and admin actions recorded
- 🛡️ **Security built in** — Argon2, TOTP 2FA, login rate limiting, CIDR IP
  allowlist, Origin checks, security headers, optional TLS
- 📊 **Observability** — `/healthz`, Prometheus `/metrics`, structured JSON logs
- 📦 **Zero-compile install** — `pip install wsctl`, no build toolchain

## How it differs from ttyd

| | ttyd | wsctl |
|---|---|---|
| Sessions | one process = one terminal = one port | one server, many sessions |
| Multi-terminal | multiple processes / tmux | native, multi-tab UI |
| Users | single basic-auth credential | multi-user with RBAC |
| Auditing | none | structured audit log |
| Connection semantics | coupled to the process | decoupled, reconnect replays screen |
| Config | command-line flags | config file + CLI |
| Observability | basic logs | `/healthz` · `/metrics` · JSON logs |
| Language | C | Python |

### Honest performance note

wsctl does **not** try to beat ttyd on raw throughput. A pure-Python forwarder
cannot out-run a C + libuv implementation on pathological output (`cat` of huge
files, `yes`). wsctl's stability comes from **architecture** — decoupled
sessions, output backpressure and per-session isolation — not single-connection
speed. Please do not benchmark the two unfairly.

## Install

```bash
pip install wsctl
# optional: Windows PTY support
pip install "wsctl[win]"
```

## Quick start

```bash
wsctl serve            # http://127.0.0.1:7681
```

On first run an `admin` account is created and a generated password is printed
to stderr. Open the URL, sign in, and a shell session starts.

Attach from your own terminal instead:

```bash
wsctl login http://127.0.0.1:7681
wsctl connect          # opens a remote shell in this terminal
```

## CLI reference

```
wsctl serve                 Start the server
wsctl serve --new "htop"    Start the server with one session
wsctl serve --backend tmux  Start with tmux-backed sessions
wsctl connect [URL]         Attach this terminal to a server
wsctl login URL             Authenticate and cache a token
wsctl logout                Forget cached credentials
wsctl session list          List sessions on a running server
wsctl session new [-x CMD] [--backend tmux]  Create a session
wsctl session attach ID     Attach this terminal to a session
wsctl session kill ID       Kill a session
wsctl user add|list|del|passwd|role|totp
wsctl audit                 Show the audit log (admin)
wsctl config show|path|edit Inspect or edit configuration
wsctl version
```

## Configuration

Precedence: CLI flags → `WSCTL_*` environment variables → `config.toml`.

`~/.config/wsctl/config.toml`:

```toml
host = "127.0.0.1"
port = 7681

auth_required = true
session_ttl = 43200          # login session lifetime (seconds)
cookie_secure = false        # set true when serving over HTTPS
trust_proxy = false          # honour X-Forwarded-For from a reverse proxy

allowed_ips = ["10.0.0.0/8"] # CIDR allowlist; empty = allow all
login_rate_limit = 10        # failed attempts before a key is blocked
login_rate_window = 300      # sliding window (seconds)
audit_input = false          # record submitted command lines

idle_timeout = 3600          # kill sessions idle for this long (optional)
max_life = 86400             # kill sessions older than this (optional)
max_sessions = 64
session_max_clients = 0      # max clients per session (0 = unlimited)
input_rate_limit = 0         # per-connection input bytes/sec (0 = unlimited)
input_rate_burst = 0         # token bucket capacity (default: limit)
scrollback_bytes = 4194304

default_backend = "local"        # "local" or "tmux" (survives server restart)
tmux_preserve_on_shutdown = true # keep tmux sessions alive across restarts

file_root = "/home/me"       # root for the web file panel (default: home)
file_max_upload = 104857600

metrics_enabled = true
log_level = "info"
log_json = false

ssl_cert = "/etc/wsctl/cert.pem"
ssl_key = "/etc/wsctl/key.pem"
```

Every key also has an environment variable, e.g. `WSCTL_PORT=9000`,
`WSCTL_ALLOWED_IPS='["10.0.0.0/8"]'`.

## Deployment

**systemd**

```ini
[Unit]
Description=wsctl web terminal
After=network.target

[Service]
User=me
ExecStart=/usr/local/bin/wsctl serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

**nginx (TLS termination)**

```nginx
location / {
    proxy_pass http://127.0.0.1:7681;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

Set `trust_proxy = true` when running behind a proxy so rate limiting and the
IP allowlist see real client addresses. Prefer HTTPS/WSS in production and set
`cookie_secure = true`.

### Sessions that survive a restart

By default sessions run as direct children of the server (`default_backend =
"local"`). Set `default_backend = "tmux"` (or pass `--backend tmux` to
`wsctl serve` / `wsctl session new`) to run shells inside a tmux session. The
shell then keeps running when wsctl restarts, and on the next start the session
is reattached automatically with the same id and a redrawn screen. Requires
`tmux` on the host; the default backend stays dependency-free.

## Security

A web terminal is remote code execution by design. wsctl assumes you expose it
only to trusted users over a trusted network:

- authentication is on by default; passwords use Argon2
- optional TOTP two-factor auth (`wsctl user totp <user>`)
- login failures are rate limited; `allowed_ips` restricts source networks
- WebSocket handshakes are validated against an Origin allowlist (anti-CSWSH)
- every sensitive action is written to the audit log
- TLS via a reverse proxy (recommended) or `ssl_cert` / `ssl_key`

Report vulnerabilities as described in [SECURITY.md](SECURITY.md).

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ruff check . && mypy src && pytest
```

See [DESIGN.md](DESIGN.md) for the architecture and roadmap.

## License

[MIT](LICENSE) © 2026 ThzxxArt
