"""System resource metrics collection via psutil.

A single MetricsCollector instance is polled once per interval by the
scheduler and fanned out to every connected socket client - never one
psutil call per connected client. Per-process CPU% needs a persistent
`psutil.Process` handle across polls (psutil computes it as a delta since
the object's last `cpu_percent()` call); a freshly-constructed Process
per poll would always read back 0%.
"""

from __future__ import annotations

import time

import psutil

from .db import Database

_IGNORED_FSTYPES = {"tmpfs", "devtmpfs", "overlay", "squashfs", "proc", "sysfs", "cgroup", "cgroup2"}


def active_ssh_sessions(db: Database) -> list[dict]:
    """Currently logged-in sessions that came in over SSH (psutil.users() is utmp-backed,
    same source `who`/`w` read, without paying for a subprocess spawn)."""
    now = time.time()
    sessions = []
    for u in psutil.users():
        if not u.host:
            continue  # empty host = local console/X11 session, not remote SSH
        row = db.latest_accepted_key(username=u.name, source_ip=u.host, before_ts=u.started + 5)
        sessions.append(
            {
                "username": u.name,
                "source_ip": u.host,
                "terminal": u.terminal,
                "started_ts": u.started,
                "duration_seconds": now - u.started,
                "key_type": row["key_type"] if row else None,
                "key_fingerprint": row["key_fingerprint"] if row else None,
                "key_label": row["key_label"] if row else None,
            }
        )
    return sessions


class MetricsCollector:
    def __init__(self) -> None:
        psutil.cpu_percent(percpu=True)  # discard meaningless first reading
        self._last_net = psutil.net_io_counters()
        self._last_net_ts = time.monotonic()
        self._process_cache: dict[int, psutil.Process] = {}
        self._prime_processes()

    def _prime_processes(self) -> None:
        for p in psutil.process_iter():
            try:
                p.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            self._process_cache[p.pid] = p

    def _top_processes(self, limit: int) -> list[dict]:
        current_pids: set[int] = set()
        results = []
        for p in psutil.process_iter():
            pid = p.pid
            current_pids.add(pid)
            cached = self._process_cache.get(pid)
            if cached is None:
                try:
                    p.cpu_percent(None)  # prime; first read is a throwaway baseline
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
                self._process_cache[pid] = p
                continue
            try:
                cpu = cached.cpu_percent(None)
                mem = cached.memory_percent()
                name = cached.name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._process_cache.pop(pid, None)
                continue
            results.append({"pid": pid, "name": name, "cpu_percent": cpu, "mem_percent": mem})

        for pid in list(self._process_cache):
            if pid not in current_pids:
                self._process_cache.pop(pid, None)

        results.sort(key=lambda item: item["cpu_percent"], reverse=True)
        return results[:limit]

    def _disks(self) -> list[dict]:
        # psutil.disk_usage() on a stalled network mount can block the event loop;
        # all=False already excludes most virtual filesystems, and a personal
        # single-user server is not expected to have flaky NFS/CIFS mounts.
        disks = []
        for part in psutil.disk_partitions(all=False):
            if part.fstype in _IGNORED_FSTYPES:
                continue
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            disks.append(
                {
                    "mountpoint": part.mountpoint,
                    "device": part.device,
                    "fstype": part.fstype,
                    "total_bytes": usage.total,
                    "used_bytes": usage.used,
                    "percent": usage.percent,
                }
            )
        return disks

    def _temperatures(self) -> dict:
        get_temps = getattr(psutil, "sensors_temperatures", None)
        if get_temps is None:
            return {}
        try:
            sensors = get_temps()
        except OSError:
            return {}
        return {
            name: [{"label": e.label or name, "current": e.current} for e in entries]
            for name, entries in sensors.items()
        }

    def snapshot(self) -> dict:
        now_monotonic = time.monotonic()
        vm = psutil.virtual_memory()
        swap = psutil.swap_memory()

        net = psutil.net_io_counters()
        elapsed = max(now_monotonic - self._last_net_ts, 1e-6)
        rx_rate = max((net.bytes_recv - self._last_net.bytes_recv) / elapsed, 0.0)
        tx_rate = max((net.bytes_sent - self._last_net.bytes_sent) / elapsed, 0.0)
        self._last_net = net
        self._last_net_ts = now_monotonic

        return {
            "ts": time.time(),
            "cpu_percent": psutil.cpu_percent(percpu=False),
            "cpu_per_core": psutil.cpu_percent(percpu=True),
            "ram_percent": vm.percent,
            "ram_used_bytes": vm.used,
            "ram_total_bytes": vm.total,
            "swap_percent": swap.percent,
            "net_rx_bytes_per_sec": rx_rate,
            "net_tx_bytes_per_sec": tx_rate,
            "disks": self._disks(),
            "top_processes": self._top_processes(limit=8),
            "uptime_seconds": time.time() - psutil.boot_time(),
            "temperatures": self._temperatures(),
        }
