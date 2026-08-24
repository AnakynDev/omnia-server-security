"""On-demand / scheduled internet speed test, backed by the `speedtest-cli` package.

The underlying library is fully blocking (sockets, no asyncio support), so
it always runs in a thread executor - never on the event loop - to avoid
stalling metrics collection or SSH ingest for the ~10-20s a test takes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import speedtest

from .db import Database

logger = logging.getLogger(__name__)


@dataclass
class SpeedtestOutcome:
    ts: float
    success: bool
    download_mbps: float | None
    upload_mbps: float | None
    ping_ms: float | None
    server_name: str | None
    server_id: str | None
    error: str | None


def _run_speedtest_blocking(ts: float) -> SpeedtestOutcome:
    try:
        st = speedtest.Speedtest()
        st.get_best_server()
        st.download()
        st.upload()
        results = st.results.dict()
        server = results.get("server", {})
        return SpeedtestOutcome(
            ts=ts,
            success=True,
            download_mbps=results["download"] / 1_000_000,
            upload_mbps=results["upload"] / 1_000_000,
            ping_ms=results["ping"],
            server_name=server.get("name"),
            server_id=str(server["id"]) if server.get("id") is not None else None,
            error=None,
        )
    except Exception as exc:  # speedtest.SpeedtestException subclasses, network/socket errors
        logger.warning("speedtest failed: %s", exc)
        return SpeedtestOutcome(
            ts=ts,
            success=False,
            download_mbps=None,
            upload_mbps=None,
            ping_ms=None,
            server_name=None,
            server_id=None,
            error=str(exc),
        )


async def run_speedtest(db: Database) -> SpeedtestOutcome:
    loop = asyncio.get_running_loop()
    outcome = await loop.run_in_executor(None, _run_speedtest_blocking, time.time())
    db.insert_speedtest_result(
        ts=outcome.ts,
        success=outcome.success,
        download_mbps=outcome.download_mbps,
        upload_mbps=outcome.upload_mbps,
        ping_ms=outcome.ping_ms,
        server_name=outcome.server_name,
        server_id=outcome.server_id,
        error=outcome.error,
    )
    return outcome
