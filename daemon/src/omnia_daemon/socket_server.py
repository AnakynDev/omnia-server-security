"""Local Unix-domain-socket API for the TUI client.

Protocol: newline-delimited JSON. Client sends `{"cmd": ..., "args": {...},
"id": ...}`, server replies `{"ok": true/false, "id": ..., "data"/"error": ...}`
on the same connection, and independently pushes `{"event": ..., "data": ...}`
to any connection that has subscribed to that event name. No auth, no TLS -
reaching the socket already requires being logged into the machine, which is
the same trust boundary SSH itself uses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from . import extra_tools
from .alerts import AlertManager
from .config import OmniaConfig
from .db import Database
from .gpu_intel import GpuMonitor
from .key_fingerprint import KeyStore
from .metrics_collector import MetricsCollector, active_ssh_sessions
from .speedtest_runner import run_speedtest

logger = logging.getLogger(__name__)


class ClientConnection:
    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.subscriptions: set[str] = set()

    async def send(self, message: dict) -> None:
        try:
            self.writer.write((json.dumps(message, default=str) + "\n").encode("utf-8"))
            await self.writer.drain()
        except (ConnectionError, OSError):
            pass


class OmniaServer:
    def __init__(
        self,
        *,
        config: OmniaConfig,
        db: Database,
        key_store: KeyStore,
        metrics: MetricsCollector,
        gpu_monitor: GpuMonitor,
        alert_manager: AlertManager,
    ):
        self.config = config
        self.db = db
        self.key_store = key_store
        self.metrics = metrics
        self.gpu_monitor = gpu_monitor
        self.alert_manager = alert_manager
        self._clients: set[ClientConnection] = set()
        self._latest_snapshot: dict = {}
        self._extra_status_cache: dict = {"available": False}

    def set_latest_snapshot(self, snapshot: dict) -> None:
        self._latest_snapshot = snapshot

    def get_latest_snapshot(self) -> dict:
        return self._latest_snapshot

    def set_extra_status_cache(self, status: dict) -> None:
        self._extra_status_cache = status

    async def broadcast(self, event: str, data: dict) -> None:
        # client.send() swallows connection errors silently; a dead connection is
        # reaped by _handle_client's read loop noticing EOF, not from here.
        message = {"event": event, "data": data}
        for client in list(self._clients):
            if event in client.subscriptions:
                await client.send(message)

    async def start(self) -> asyncio.AbstractServer:
        socket_path = Path(self.config.paths.socket_path)
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            socket_path.unlink()
        server = await asyncio.start_unix_server(self._handle_client, path=str(socket_path))
        socket_path.chmod(0o600)
        return server

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = ClientConnection(writer)
        self._clients.add(client)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    await client.send({"ok": False, "error": "invalid_json"})
                    continue
                await self._dispatch(client, request)
        except (ConnectionError, OSError):
            pass
        finally:
            self._clients.discard(client)
            writer.close()

    async def _dispatch(self, client: ClientConnection, request: dict) -> None:
        cmd = request.get("cmd")
        args = request.get("args") or {}
        req_id = request.get("id")

        handler = self._COMMANDS.get(cmd)
        if handler is None:
            await client.send({"ok": False, "id": req_id, "error": f"unknown_cmd:{cmd}"})
            return
        try:
            data = await handler(self, client, args)
            await client.send({"ok": True, "id": req_id, "data": data})
        except Exception as exc:
            logger.exception("error handling cmd %s", cmd)
            await client.send({"ok": False, "id": req_id, "error": str(exc)})

    # -- command handlers ----------------------------------------------

    async def _cmd_ping(self, client: ClientConnection, args: dict) -> dict:
        return {"pong": True}

    async def _cmd_subscribe(self, client: ClientConnection, args: dict) -> dict:
        client.subscriptions |= set(args.get("streams", []))
        return {"subscribed": sorted(client.subscriptions)}

    async def _cmd_unsubscribe(self, client: ClientConnection, args: dict) -> dict:
        client.subscriptions -= set(args.get("streams", []))
        return {"subscribed": sorted(client.subscriptions)}

    async def _cmd_get_snapshot(self, client: ClientConnection, args: dict) -> dict:
        return {
            "metrics": self._latest_snapshot,
            "active_sessions": active_ssh_sessions(self.db),
        }

    async def _cmd_get_login_history(self, client: ClientConnection, args: dict) -> dict:
        limit = int(args.get("limit", 100))
        return {"events": [dict(row) for row in self.db.recent_login_events(limit=limit)]}

    async def _cmd_get_speedtest_history(self, client: ClientConnection, args: dict) -> dict:
        limit = int(args.get("limit", 50))
        return {"results": [dict(row) for row in self.db.recent_speedtest_results(limit=limit)]}

    async def _cmd_run_speedtest(self, client: ClientConnection, args: dict) -> dict:
        asyncio.create_task(self._run_speedtest_and_broadcast())
        return {"started": True}

    async def _run_speedtest_and_broadcast(self) -> None:
        outcome = await run_speedtest(self.db)
        await self.broadcast("speedtest_result", outcome.__dict__)

    async def _cmd_get_resource_history(self, client: ClientConnection, args: dict) -> dict:
        since_seconds = float(args.get("since_seconds", 3600))
        rows = self.db.resource_snapshots_since(time.time() - since_seconds)
        return {"snapshots": [dict(row) for row in rows]}

    async def _cmd_get_keys(self, client: ClientConnection, args: dict) -> dict:
        keys = self.key_store.all_keys()
        return {
            "keys": [
                {"key_type": k.key_type, "fingerprint": k.fingerprint, "comment": k.comment} for k in keys
            ]
        }

    async def _cmd_get_extra_status(self, client: ClientConnection, args: dict) -> dict:
        status = dict(self._extra_status_cache)
        status["dangerous_actions_enabled"] = (
            self.config.features.allow_service_restart or self.config.features.allow_reboot
        )
        return status

    async def _cmd_get_alerts(self, client: ClientConnection, args: dict) -> dict:
        limit = int(args.get("limit", 50))
        return {"alerts": [dict(row) for row in self.db.recent_alerts(limit=limit)]}

    async def _cmd_restart_service(self, client: ClientConnection, args: dict) -> dict:
        if not self.config.features.allow_service_restart:
            raise PermissionError("service restart is disabled in config (features.allow_service_restart)")
        unit = args["unit"]
        return {"restarted": extra_tools.restart_service(unit), "unit": unit}

    async def _cmd_reboot_system(self, client: ClientConnection, args: dict) -> dict:
        if not self.config.features.allow_reboot:
            raise PermissionError("reboot is disabled in config (features.allow_reboot)")
        return {"rebooting": extra_tools.reboot_system()}

    _COMMANDS = {
        "ping": _cmd_ping,
        "subscribe": _cmd_subscribe,
        "unsubscribe": _cmd_unsubscribe,
        "get_snapshot": _cmd_get_snapshot,
        "get_login_history": _cmd_get_login_history,
        "get_speedtest_history": _cmd_get_speedtest_history,
        "run_speedtest": _cmd_run_speedtest,
        "get_resource_history": _cmd_get_resource_history,
        "get_keys": _cmd_get_keys,
        "get_extra_status": _cmd_get_extra_status,
        "get_alerts": _cmd_get_alerts,
        "restart_service": _cmd_restart_service,
        "reboot_system": _cmd_reboot_system,
    }
