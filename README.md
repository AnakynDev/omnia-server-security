# Omnia Server Security

A lightweight dashboard for a personal Linux server: SSH security monitoring (active sessions, login history, which public key authenticated each login, failed attempts) plus system health (CPU, RAM, disk, network, integrated GPU), an on-demand speed test, and a handful of extra server-management read-outs (systemd services, Docker containers, fail2ban, SMART disk health, pending package updates).

Built for a single operator on a local network: no HTTP server, no web UI, no authentication layer of its own. Reaching the dashboard already requires being logged into the machine, which is the same trust boundary SSH itself uses.

## Architecture

Two independent processes, one project:

- **`daemon/`** — `omnia-daemon`, a background service (systemd) that tails the SSH log (journald, with an `auth.log`/`secure` fallback), computes OpenSSH key fingerprints to identify which key authenticated a login, polls system metrics via `psutil`, runs speed tests, sends email alerts, and persists everything to SQLite. It exposes a local **Unix domain socket** API (newline-delimited JSON, request/response plus pushed live events) — no TLS, no auth, since the socket is only reachable by someone already on the box.
- **`tui/`** — `omnia-tui`, a [Textual](https://github.com/Textualize/textual) terminal UI you run on demand (typically over SSH) that connects to the daemon's socket and renders the dashboard: 5 panels for SSH security, system state, speed test, extra tools, and alerts.

```
   ssh session                     background, always running
  ┌────────────┐   Unix socket    ┌──────────────────┐
  │ omnia-tui  │ ───────────────▶ │   omnia-daemon    │
  │ (Textual)  │ ◀─── live push ─ │  (systemd service)│
  └────────────┘                  └──────────────────┘
                                            │
                                     journald / auth.log
                                     psutil, speedtest-cli
                                     SQLite (WAL)
```

## Requirements

- Linux server with systemd (the daemon is Linux-only: it reads journald/auth.log and uses Unix domain sockets).
- SSH configured for public-key authentication (the login/key-tracking features assume this).
- Python 3.11+.

## Repository layout

| Path | What it is |
|---|---|
| `daemon/` | `omnia-daemon` package: SSH log ingestion, metrics collection, alerts, the socket API. |
| `daemon/tests/` | Unit tests for everything host-portable (log parsing, key fingerprints, config, alert rules, the database layer). |
| `tui/` | `omnia-tui` package: the Textual dashboard client. |
| `deploy/` | `omnia.service` systemd unit and an annotated `config.example.toml`. |

## Installation

```bash
# 0. Get the code
git clone https://github.com/AnakynDev/omnia-server-security.git
cd omnia-server-security

# 1. Daemon
sudo mkdir -p /opt/omnia
sudo python3 -m venv /opt/omnia/venv
sudo /opt/omnia/venv/bin/pip install ./daemon

sudo useradd --system --no-create-home omnia
sudo usermod -aG adm,systemd-journal omnia   # read auth.log / the journal without root

sudo mkdir -p /etc/omnia
sudo cp deploy/config.example.toml /etc/omnia/config.toml
# edit /etc/omnia/config.toml: authorized_keys_path (absolute path), SMTP settings, etc.

sudo cp deploy/omnia.service /etc/systemd/system/
# edit ExecStart in the unit file to point at /opt/omnia/venv/bin/python
sudo systemctl daemon-reload
sudo systemctl enable --now omnia

# 2. TUI (run on demand, e.g. inside a tmux session over SSH)
pip install --user ./tui
OMNIA_SOCKET_PATH=/run/omnia/omnia.sock omnia-tui
```

For the "which key logged in" feature to work at all, `sshd_config` needs `LogLevel VERBOSE` (or at least `INFO`) so OpenSSH logs the key fingerprint on accepted/failed public-key logins.

## Configuration

See `deploy/config.example.toml` for every option (alert thresholds, SMTP, speed test schedule, retention, which optional features are enabled). Anything left out of your `config.toml` falls back to its documented default.

Dangerous actions (restarting a service, rebooting the server from the TUI) are disabled by default (`features.allow_service_restart` / `features.allow_reboot`) and, if enabled, typically need a sudoers/polkit rule since the daemon should not run as root.

## Development

```bash
cd daemon
pip install -e ".[dev]"
pytest
```

The daemon's socket-based integration (journald streaming, log tailing, the Unix socket itself) can only be exercised on Linux. Everything else — SSH log line parsing, key fingerprint computation, config loading, alert rules, the SQLite layer — is unit-tested and portable.

To run the TUI against simulated data instead of a live daemon:

```bash
cd tui
pip install -e .
OMNIA_SIMULATE=1 omnia-tui
```
