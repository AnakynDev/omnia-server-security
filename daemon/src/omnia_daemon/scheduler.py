"""Background async tasks the daemon runs for its whole lifetime.

Kept as plain asyncio loops (no APScheduler or similar) - a handful of
`while True: ...; await asyncio.sleep(...)` tasks is all this needs, and it
keeps the dependency list to just psutil + speedtest-cli.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time

from . import extra_tools
from .alerts import AlertManager
from .config import OmniaConfig
from .db import Database
from .gpu_intel import GpuMonitor
from .key_fingerprint import KeyStore
from .metrics_collector import MetricsCollector
from .socket_server import OmniaServer
from .speedtest_runner import run_speedtest
from .ssh_ingest import run_ssh_ingest

logger = logging.getLogger(__name__)

_KEY_SERVICE_UNITS = ["ssh.service", "sshd.service", "docker.service", "fail2ban.service"]
_RETENTION_INTERVAL_SECONDS = 24 * 3600
_EXTRA_STATUS_INTERVAL_SECONDS = 5 * 60


async def run_metrics_loop(
    config: OmniaConfig,
    db: Database,
    metrics: MetricsCollector,
    gpu_monitor: GpuMonitor,
    server: OmniaServer,
    alert_manager: AlertManager,
) -> None:
    buffer: list[dict] = []
    last_flush = time.monotonic()
    flush_interval = config.retention.resource_snapshot_interval_minutes * 60

    while True:
        snapshot = metrics.snapshot()
        snapshot["gpu"] = gpu_monitor.latest()
        server.set_latest_snapshot(snapshot)
        await server.broadcast("metrics", snapshot)
        alert_manager.on_metrics_snapshot(snapshot)
        buffer.append(snapshot)

        now = time.monotonic()
        if buffer and now - last_flush >= flush_interval:
            _flush_snapshot_buffer(db, buffer)
            buffer.clear()
            last_flush = now

        await asyncio.sleep(config.metrics.poll_interval_seconds)


def _flush_snapshot_buffer(db: Database, buffer: list[dict]) -> None:
    latest = buffer[-1]
    db.insert_resource_snapshot(
        ts=latest["ts"],
        cpu_percent=statistics.fmean(s["cpu_percent"] for s in buffer),
        ram_percent=statistics.fmean(s["ram_percent"] for s in buffer),
        ram_used_bytes=latest["ram_used_bytes"],
        ram_total_bytes=latest["ram_total_bytes"],
        swap_percent=statistics.fmean(s["swap_percent"] for s in buffer),
        net_rx_bytes_per_sec=statistics.fmean(s["net_rx_bytes_per_sec"] for s in buffer),
        net_tx_bytes_per_sec=statistics.fmean(s["net_tx_bytes_per_sec"] for s in buffer),
        disk=latest["disks"],
        gpu=latest.get("gpu"),
    )


async def run_ssh_ingest_loop(
    config: OmniaConfig, db: Database, key_store: KeyStore, server: OmniaServer, alert_manager: AlertManager
) -> None:
    def on_event(event: dict) -> None:
        alert_manager.on_ssh_event(event)
        asyncio.create_task(server.broadcast("login_event", event))

    await run_ssh_ingest(config, db, key_store, on_event)


async def run_gpu_monitor_loop(gpu_monitor: GpuMonitor) -> None:
    await gpu_monitor.run_forever()


async def run_speedtest_schedule(config: OmniaConfig, db: Database, server: OmniaServer) -> None:
    if not config.speedtest.enabled:
        return
    interval_seconds = config.speedtest.interval_hours * 3600
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            outcome = await run_speedtest(db)
            await server.broadcast("speedtest_result", outcome.__dict__)
        except Exception:
            logger.exception("scheduled speedtest failed")


async def run_extra_status_refresh(config: OmniaConfig, server: OmniaServer) -> None:
    loop = asyncio.get_running_loop()
    while True:
        status: dict = {"available": True}
        try:
            status["services"] = await loop.run_in_executor(
                None, extra_tools.service_statuses, _KEY_SERVICE_UNITS
            )
            if config.features.docker:
                status["docker"] = await loop.run_in_executor(None, extra_tools.docker_containers)
            if config.features.fail2ban:
                status["fail2ban"] = await loop.run_in_executor(None, extra_tools.fail2ban_banned_ips)
            if config.features.pending_updates:
                status["updates"] = await loop.run_in_executor(None, extra_tools.pending_updates)
            if config.features.smart_health:
                devices = extra_tools.guess_base_devices(
                    [d["device"] for d in server.get_latest_snapshot().get("disks", [])]
                )
                status["smart"] = await loop.run_in_executor(None, extra_tools.disk_smart_health, devices)
        except Exception:
            logger.exception("extra status refresh failed")
        server.set_extra_status_cache(status)
        await asyncio.sleep(_EXTRA_STATUS_INTERVAL_SECONDS)


async def run_retention_cleanup(config: OmniaConfig, db: Database) -> None:
    while True:
        try:
            db.cleanup_retention(
                login_event_days=config.retention.login_event_days,
                resource_snapshot_days=config.retention.resource_snapshot_days,
            )
        except Exception:
            logger.exception("retention cleanup failed")
        await asyncio.sleep(_RETENTION_INTERVAL_SECONDS)
