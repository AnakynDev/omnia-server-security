"""Entrypoint: wires all components together and runs the daemon until signaled to stop."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .alerts import AlertManager
from .config import expand_path, load_config
from .db import Database
from .gpu_intel import GpuMonitor
from .key_fingerprint import KeyStore
from .metrics_collector import MetricsCollector
from .scheduler import (
    run_extra_status_refresh,
    run_gpu_monitor_loop,
    run_metrics_loop,
    run_retention_cleanup,
    run_speedtest_schedule,
    run_ssh_ingest_loop,
)
from .socket_server import OmniaServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def _main_async(config_path: str | None) -> None:
    config = load_config(config_path)

    db = Database(expand_path(config.paths.db_path))
    key_store = KeyStore(expand_path(config.paths.authorized_keys_path))
    metrics = MetricsCollector()
    gpu_monitor = GpuMonitor(retry_minutes=config.gpu.retry_minutes)
    if not config.gpu.enabled:
        gpu_monitor.disable()
    alert_manager = AlertManager(config.alerts, config.smtp, db)
    server = OmniaServer(
        config=config,
        db=db,
        key_store=key_store,
        metrics=metrics,
        gpu_monitor=gpu_monitor,
        alert_manager=alert_manager,
    )

    unix_server = await server.start()
    logger.info("omnia daemon listening on %s", config.paths.socket_path)

    tasks = [
        asyncio.create_task(run_metrics_loop(config, db, metrics, gpu_monitor, server, alert_manager)),
        asyncio.create_task(run_ssh_ingest_loop(config, db, key_store, server, alert_manager)),
        asyncio.create_task(run_speedtest_schedule(config, db, server)),
        asyncio.create_task(run_extra_status_refresh(config, server)),
        asyncio.create_task(run_retention_cleanup(config, db)),
    ]
    if config.gpu.enabled:
        tasks.append(asyncio.create_task(run_gpu_monitor_loop(gpu_monitor)))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass  # not available on Windows; fine for dev, real deploy target is Linux/systemd

    try:
        await stop_event.wait()
    finally:
        logger.info("shutting down")
        unix_server.close()
        await unix_server.wait_closed()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        db.close()


def run() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        asyncio.run(_main_async(config_path))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
