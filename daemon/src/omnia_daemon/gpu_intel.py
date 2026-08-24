"""Best-effort integrated Intel GPU utilization via `intel_gpu_top -J`.

There is no universal sysfs percentage for Intel GPU busy-ness (unlike AMD's
`gpu_busy_percent`), and `intel_gpu_top` itself often needs elevated
perf-event permissions (`setcap cap_perfmon+ep $(which intel_gpu_top)` as an
install-time step) - so this is explicitly allowed to fail. On failure it
marks the metric unavailable and backs off to an occasional retry instead of
spinning, since a tight retry loop would itself be the kind of overhead this
whole project is trying to avoid.

`intel_gpu_top -J` streams JSON objects back-to-back without ever closing
the enclosing array, and the exact field layout has drifted across
igt-gpu-tools versions - so parsing uses an incremental raw_decode() over a
byte buffer (tolerant of stray `[`, `,`, `]`) and defensive field lookups
rather than assuming one fixed schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil

logger = logging.getLogger(__name__)

_READ_CHUNK = 4096


def _extract(obj: dict) -> dict:
    engines = obj.get("engines")
    busy: dict[str, float] = {}
    if isinstance(engines, dict):
        for name, data in engines.items():
            if isinstance(data, dict) and isinstance(data.get("busy"), (int, float)):
                busy[name] = data["busy"]
    return {"available": True, "engines_busy_percent": busy}


class GpuMonitor:
    def __init__(self, retry_minutes: float = 10.0):
        self._retry_seconds = max(retry_minutes, 1.0) * 60
        self._latest: dict = {"available": False, "reason": "not checked yet"}

    def latest(self) -> dict:
        return self._latest

    def disable(self) -> None:
        self._latest = {"available": False, "reason": "disabled in config"}

    async def run_forever(self) -> None:
        if not shutil.which("intel_gpu_top"):
            self._latest = {"available": False, "reason": "intel_gpu_top not installed"}
            return

        while True:
            got_any_reading = await self._try_stream()
            if not got_any_reading:
                self._latest = {"available": False, "reason": "intel_gpu_top failed (permissions?)"}
                await asyncio.sleep(self._retry_seconds)
            else:
                await asyncio.sleep(5)  # stream ended unexpectedly; short pause then reconnect

    async def _try_stream(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "intel_gpu_top",
                "-J",
                "-s",
                "1000",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return False

        decoder = json.JSONDecoder()
        buffer = ""
        got_any = False
        try:
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(_READ_CHUNK)
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                while True:
                    stripped = buffer.lstrip(" \t\r\n,[]")
                    if not stripped:
                        buffer = stripped
                        break
                    try:
                        obj, idx = decoder.raw_decode(stripped)
                    except json.JSONDecodeError:
                        buffer = stripped
                        break
                    buffer = stripped[idx:]
                    if isinstance(obj, dict):
                        got_any = True
                        self._latest = _extract(obj)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        return got_any
