"""Real backend: talks to the Omnia daemon over its local Unix-domain-socket
API (see the daemon's `socket_server.py` for the protocol - NDJSON commands
with request/response plus pushed events, no auth, no TLS).

Exposes exactly the same attribute/method surface as `OmniaMockBackend`
(see data.py) so the screens don't need to change at all - only
`app.py` needs to instantiate this class instead of the mock.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket as socket_module
import time
from collections import deque

from .data import (
    Alert,
    BlockedIP,
    ContainerInfo,
    Disk,
    DiskHealth,
    LoginEvent,
    Process,
    Service,
    SpeedTestResult,
    SSHActiveSession,
    SSHKey,
)

logger = logging.getLogger(__name__)

_RECONNECT_DELAY_SECONDS = 3.0
_REQUEST_TIMEOUT_SECONDS = 15.0
_SESSION_REFRESH_EVERY_TICKS = 5  # active sessions aren't pushed, only fetched on demand
_MAX_LOGIN_HISTORY = 500

_EVENT_TYPE_TO_RESULT = {
    "accepted_publickey": "success",
    "accepted_other": "success",
    "failed_publickey": "failure",
    "failed_other": "failure",
    "invalid_user": "invalid_user",
    "connection_closed": "disconnected",
    "disconnected": "disconnected",
}


def _login_event_from_dict(row: dict) -> LoginEvent:
    result = _EVENT_TYPE_TO_RESULT.get(row.get("event_type"), "disconnected")
    detail = row.get("detail") or ""
    if result == "failure" and not detail:
        detail = "tentativa de login falhou"
    elif result == "invalid_user" and not detail:
        detail = "usuário inexistente"
    return LoginEvent(
        ts=row["ts"],
        user=row.get("username") or "?",
        ip=row.get("source_ip") or "?",
        result=result,
        key_label=row.get("key_label") or "",
        detail=detail,
    )


def _map_service_state(state: str) -> str:
    if state == "active":
        return "active"
    if state == "failed":
        return "failed"
    return "inactive"


def _key_display_label(key: dict) -> str:
    return key.get("comment") or f"chave-{key['fingerprint'][-8:]}"


class OmniaBackend:
    """Same public surface as OmniaMockBackend, backed by real daemon data."""

    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.hostname = socket_module.gethostname()
        self.ready = False
        self.connection_state = "disconnected"

        # ---- SSH -----------------------------------------------------
        self.ssh_active_sessions: list[SSHActiveSession] = []
        self.login_history: list[LoginEvent] = []
        self.ssh_keys: list[SSHKey] = []
        self.fail2ban_available = False
        self.blocked_ips: list[BlockedIP] = []

        # ---- Sistema ----------------------------------------------------
        self.cpu_per_core: list[float] = []
        self.mem_total_mb = 0.0
        self.mem_used_mb = 0.0
        self.swap_pct = 0.0
        self.temps_available = False
        self.temps: dict[str, float] = {}
        self.disks: list[Disk] = []
        self.disk_health: list[DiskHealth] = []
        self.net_upload_kbps = 0.0
        self.net_download_kbps = 0.0
        self.net_history_up: deque = deque([0.0] * 40, maxlen=40)
        self.net_history_down: deque = deque([0.0] * 40, maxlen=40)
        self.gpu_available = False
        self.gpu_engines: dict[str, float] = {}
        self.processes: list[Process] = []

        # ---- Speed test ------------------------------------------------
        self.speedtest_running = False
        self.speedtest_error: str | None = None
        self.speedtest_history: list[SpeedTestResult] = []

        # ---- Ferramentas extras -----------------------------------------
        self.services: list[Service] = []
        self.containers_available = False
        self.containers: list[ContainerInfo] = []
        self.pending_updates_count = 0
        self.pending_updates_manager = ""
        self.dangerous_actions_enabled = False

        # ---- Alertas ------------------------------------------------------
        self.alerts: list[Alert] = []

        # ---- internal state --------------------------------------------
        self._cpu_total = 0.0
        self._uptime_seconds = 0.0
        self._raw_keys: list[dict] = []
        self._blocked_ip_since: dict[str, float] = {}
        self._restarting_service: str | None = None
        self._tick_count = 0
        self._sessions_inflight = False

        self._writer: asyncio.StreamWriter | None = None
        self._next_req_id = 1
        self._pending: dict[int, asyncio.Future] = {}

    # -- connection lifecycle ------------------------------------------

    async def run_forever(self) -> None:
        """Connect/subscribe/load, then reconnect with a fixed backoff on drop.
        Meant to be launched once as a background worker for the app's lifetime."""
        while True:
            try:
                await self._connect_and_run()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("omnia backend: connection loop failed")
            self.connection_state = "disconnected"
            self._writer = None
            self._fail_pending_requests()
            await asyncio.sleep(_RECONNECT_DELAY_SECONDS)

    async def _connect_and_run(self) -> None:
        reader, writer = await asyncio.open_unix_connection(self.socket_path)
        self._writer = writer
        self.connection_state = "connected"
        # _read_loop must be running *before* any _send_and_wait call: that's
        # the only thing that ever reads a response off the socket and
        # resolves the pending-request future. Awaiting _load_initial_state()
        # first (as this used to) deadlocks forever - nothing reads the
        # daemon's replies, so every request future just hangs.
        read_task = asyncio.create_task(self._read_loop(reader))
        try:
            await self._send_and_wait("subscribe", {"streams": ["metrics", "login_event", "speedtest_result"]})
            await self._load_initial_state()
            self.ready = True
            await read_task
        finally:
            read_task.cancel()
            writer.close()
            self._writer = None

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line:
                raise ConnectionError("omnia daemon closed the connection")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "event" in message:
                self._handle_event(message["event"], message.get("data") or {})
            elif "id" in message and message["id"] in self._pending:
                future = self._pending.pop(message["id"])
                if not future.done():
                    future.set_result(message)

    def _fail_pending_requests(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(ConnectionError("connection to omnia daemon lost"))
        self._pending.clear()

    async def _send_and_wait(self, cmd: str, args: dict | None = None) -> dict:
        if self._writer is None:
            raise ConnectionError("not connected to omnia daemon")
        req_id = self._next_req_id
        self._next_req_id += 1
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        payload = json.dumps({"cmd": cmd, "id": req_id, "args": args or {}}) + "\n"
        self._writer.write(payload.encode("utf-8"))
        await self._writer.drain()
        response = await asyncio.wait_for(future, timeout=_REQUEST_TIMEOUT_SECONDS)
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "unknown error"))
        return response.get("data") or {}

    async def _load_initial_state(self) -> None:
        snapshot = await self._send_and_wait("get_snapshot")
        self._apply_metrics(snapshot.get("metrics") or {})
        self._apply_active_sessions(snapshot.get("active_sessions") or [])

        history = await self._send_and_wait("get_login_history", {"limit": _MAX_LOGIN_HISTORY})
        self._apply_login_history(history.get("events") or [])

        keys = await self._send_and_wait("get_keys")
        self._apply_keys(keys.get("keys") or [])

        speedtests = await self._send_and_wait("get_speedtest_history", {"limit": 200})
        self._apply_speedtest_history(speedtests.get("results") or [])

        extra = await self._send_and_wait("get_extra_status")
        self._apply_extra_status(extra)

        alerts = await self._send_and_wait("get_alerts", {"limit": 200})
        self._apply_alerts(alerts.get("alerts") or [])

    # -- pushed events --------------------------------------------------

    def _handle_event(self, event: str, data: dict) -> None:
        if event == "metrics":
            self._apply_metrics(data)
        elif event == "login_event":
            self._apply_new_login_event(data)
        elif event == "speedtest_result":
            self._apply_new_speedtest_result(data)

    # -- applying daemon payloads to the mock-shaped attributes ------------

    def _apply_metrics(self, data: dict) -> None:
        if not data:
            return
        self._uptime_seconds = data.get("uptime_seconds", self._uptime_seconds)
        self.cpu_per_core = list(data.get("cpu_per_core") or [])
        self._cpu_total = data.get("cpu_percent", self._cpu_total)
        self.mem_total_mb = (data.get("ram_total_bytes") or 0) / 1_000_000
        self.mem_used_mb = (data.get("ram_used_bytes") or 0) / 1_000_000
        self.swap_pct = data.get("swap_percent") or 0.0

        flat_temps: dict[str, float] = {}
        for sensor_name, entries in (data.get("temperatures") or {}).items():
            for entry in entries:
                flat_temps[entry.get("label") or sensor_name] = entry.get("current", 0.0)
        self.temps_available = bool(flat_temps)
        self.temps = flat_temps

        self.disks = [
            Disk(
                mount=d["mountpoint"],
                device=d["device"],
                fstype=d["fstype"],
                used_gb=d["used_bytes"] / 1_000_000_000,
                total_gb=d["total_bytes"] / 1_000_000_000,
            )
            for d in (data.get("disks") or [])
        ]

        self.net_download_kbps = (data.get("net_rx_bytes_per_sec") or 0.0) / 1000
        self.net_upload_kbps = (data.get("net_tx_bytes_per_sec") or 0.0) / 1000
        self.net_history_down.append(self.net_download_kbps)
        self.net_history_up.append(self.net_upload_kbps)

        gpu = data.get("gpu") or {"available": False}
        self.gpu_available = bool(gpu.get("available"))
        self.gpu_engines = gpu.get("engines_busy_percent") or {}

        self.processes = [
            Process(
                pid=p["pid"],
                name=p.get("name") or "?",
                cpu_pct=p.get("cpu_percent") or 0.0,
                mem_pct=p.get("mem_percent") or 0.0,
            )
            for p in (data.get("top_processes") or [])
        ]

    def _apply_active_sessions(self, sessions: list[dict]) -> None:
        self.ssh_active_sessions = [
            SSHActiveSession(
                user=s.get("username") or "?",
                ip=s.get("source_ip") or "?",
                tty=s.get("terminal") or "-",
                key_label=s.get("key_label") or "desconhecida",
                key_type=s.get("key_type") or "-",
                connected_since=s.get("started_ts") or time.time(),
            )
            for s in sessions
        ]

    def _apply_login_history(self, rows: list[dict]) -> None:
        # daemon returns newest-first already, matching what the screens expect
        self.login_history = [_login_event_from_dict(r) for r in rows]
        self._rebuild_keys()

    def _apply_new_login_event(self, data: dict) -> None:
        self.login_history.insert(0, _login_event_from_dict(data))
        del self.login_history[_MAX_LOGIN_HISTORY:]
        self._rebuild_keys()

    def _apply_keys(self, keys: list[dict]) -> None:
        self._raw_keys = keys
        self._rebuild_keys()

    def _rebuild_keys(self) -> None:
        last_used: dict[str, float] = {}
        for event in self.login_history:  # newest-first, so setdefault keeps the most recent
            if event.result == "success" and event.key_label:
                last_used.setdefault(event.key_label, event.ts)
        self.ssh_keys = [
            SSHKey(
                label=_key_display_label(k),
                key_type=k["key_type"],
                fingerprint=k["fingerprint"],
                last_used=last_used.get(_key_display_label(k)),
            )
            for k in self._raw_keys
        ]

    def _apply_speedtest_history(self, rows: list[dict]) -> None:
        # daemon returns newest-first; the screens expect oldest-first (they read history[-N:])
        ordered = list(reversed(rows))
        self.speedtest_history = [
            SpeedTestResult(
                ts=r["ts"],
                download_mbps=r.get("download_mbps") or 0.0,
                upload_mbps=r.get("upload_mbps") or 0.0,
                ping_ms=r.get("ping_ms") or 0.0,
                server_name=r.get("server_name") or "-",
            )
            for r in ordered
            if r.get("success")
        ]
        if rows:
            latest = rows[0]
            self.speedtest_error = None if latest.get("success") else (latest.get("error") or "Falha desconhecida no teste.")

    def _apply_new_speedtest_result(self, data: dict) -> None:
        self.speedtest_running = False
        if data.get("success"):
            self.speedtest_error = None
            self.speedtest_history.append(
                SpeedTestResult(
                    ts=data["ts"],
                    download_mbps=data.get("download_mbps") or 0.0,
                    upload_mbps=data.get("upload_mbps") or 0.0,
                    ping_ms=data.get("ping_ms") or 0.0,
                    server_name=data.get("server_name") or "-",
                )
            )
        else:
            self.speedtest_error = data.get("error") or "Falha desconhecida no teste."

    def _apply_extra_status(self, data: dict) -> None:
        if not data:
            return

        self.services = [
            Service(name=s["unit"], status=_map_service_state(s["state"])) for s in (data.get("services") or [])
        ]

        docker = data.get("docker") or {"available": False}
        self.containers_available = bool(docker.get("available"))
        self.containers = [
            ContainerInfo(
                name=c.get("name") or "?", image=c.get("image") or "?",
                status=c.get("status") or "?", state=c.get("state") or "?",
            )
            for c in (docker.get("containers") or [])
        ]

        fail2ban = data.get("fail2ban") or {"available": False}
        self.fail2ban_available = bool(fail2ban.get("available"))
        banned_by_jail = fail2ban.get("banned_by_jail") or {}
        now = time.time()
        current_ips = set()
        blocked: list[BlockedIP] = []
        for jail, ips in banned_by_jail.items():
            for ip in ips:
                current_ips.add(ip)
                since = self._blocked_ip_since.setdefault(ip, now)
                blocked.append(BlockedIP(ip=ip, rule=jail, banned_at=since))
        for ip in list(self._blocked_ip_since):
            if ip not in current_ips:
                del self._blocked_ip_since[ip]
        self.blocked_ips = blocked

        updates = data.get("updates") or {"available": False}
        if updates.get("available"):
            self.pending_updates_count = updates.get("count", 0)
            self.pending_updates_manager = updates.get("manager", "?")

        smart = data.get("smart") or {"available": False}
        self.disk_health = (
            [DiskHealth(device=dev, status=status) for dev, status in (smart.get("devices") or {}).items()]
            if smart.get("available")
            else []
        )

        self.dangerous_actions_enabled = bool(data.get("dangerous_actions_enabled"))

    def _apply_alerts(self, rows: list[dict]) -> None:
        self.alerts = [Alert(ts=r["ts"], kind=r["alert_type"], description=r.get("detail") or "") for r in rows]

    # -- public read API expected by the screens (same names as the mock) ---

    def cpu_total_pct(self) -> float:
        return self._cpu_total

    def mem_pct(self) -> float:
        return self.mem_used_mb / self.mem_total_mb * 100 if self.mem_total_mb else 0.0

    def uptime_seconds(self) -> float:
        return self._uptime_seconds

    def sorted_processes(self, by: str = "cpu") -> list[Process]:
        key = (lambda p: p.cpu_pct) if by == "cpu" else (lambda p: p.mem_pct)
        return sorted(self.processes, key=key, reverse=True)

    def restarting_service_name(self) -> str | None:
        return self._restarting_service

    # -- tick: lightweight per-second hook called by the app --------------

    def tick(self) -> None:
        """Called every second by the app. Real data arrives asynchronously via
        the socket (see run_forever); this only triggers the occasional
        refresh of data the daemon doesn't proactively push (active sessions,
        alerts)."""
        self._tick_count += 1
        if not self.ready or self._sessions_inflight:
            return
        if self._tick_count % _SESSION_REFRESH_EVERY_TICKS == 0:
            self._sessions_inflight = True
            asyncio.create_task(self._refresh_active_sessions())

    async def _refresh_active_sessions(self) -> None:
        try:
            snapshot = await self._send_and_wait("get_snapshot")
            self._apply_active_sessions(snapshot.get("active_sessions") or [])
        except Exception:
            pass
        finally:
            self._sessions_inflight = False

    # -- actions (side effects) --------------------------------------------

    def start_speed_test(self) -> None:
        if self.speedtest_running:
            return
        self.speedtest_running = True
        self.speedtest_error = None
        asyncio.create_task(self._start_speed_test())

    async def _start_speed_test(self) -> None:
        try:
            await self._send_and_wait("run_speedtest")
        except Exception as exc:
            self.speedtest_running = False
            self.speedtest_error = str(exc)

    def restart_service(self, name: str) -> None:
        self._restarting_service = name
        asyncio.create_task(self._restart_service(name))

    async def _restart_service(self, name: str) -> None:
        try:
            await self._send_and_wait("restart_service", {"unit": name})
        except Exception:
            pass
        finally:
            self._restarting_service = None
            try:
                extra = await self._send_and_wait("get_extra_status")
                self._apply_extra_status(extra)
            except Exception:
                pass

    def reboot_server(self) -> None:
        asyncio.create_task(self._reboot_server())

    async def _reboot_server(self) -> None:
        try:
            await self._send_and_wait("reboot_system")
        except Exception:
            pass
