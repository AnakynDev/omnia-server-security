"""Bonus server-management read-outs: services, Docker, fail2ban, SMART, pending updates.

Everything here is best-effort and called only occasionally (periodic
refresh or on-demand from the socket, never per-metrics-tick) so a plain
blocking `subprocess.run` per check is fine - these are not hot-path calls.
Every check degrades to `{"available": False}` rather than raising when the
underlying tool isn't installed, since most personal servers won't have all
of Docker/fail2ban/smartmontools present at once.

The two mutating actions (`restart_service`, `reboot_system`) are gated by
config flags the caller must check (`features.allow_service_restart` /
`features.allow_reboot`, both default off) - they are exposed here but not
wired to anything by default, and typically need a sudoers/polkit rule since
the daemon should not run as root.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

_PARTITION_SUFFIX_RE = re.compile(r"(p\d+|\d+)$")


def _run(cmd: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def service_statuses(unit_names: list[str]) -> list[dict]:
    results = []
    for unit in unit_names:
        proc = _run(["systemctl", "is-active", unit])
        state = proc.stdout.strip() if proc else "unknown"
        results.append({"unit": unit, "state": state})
    return results


def docker_containers() -> dict:
    if not shutil.which("docker"):
        return {"available": False}
    proc = _run(["docker", "ps", "--format", "{{json .}}"], timeout=8)
    if proc is None or proc.returncode != 0:
        return {"available": False}
    containers = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append(
            {
                "name": data.get("Names"),
                "image": data.get("Image"),
                "status": data.get("Status"),
                "state": data.get("State"),
            }
        )
    return {"available": True, "containers": containers}


def fail2ban_banned_ips() -> dict:
    if not shutil.which("fail2ban-client"):
        return {"available": False}
    jails_proc = _run(["fail2ban-client", "status"], timeout=5)
    if jails_proc is None or jails_proc.returncode != 0:
        return {"available": False}

    jail_line = next((line for line in jails_proc.stdout.splitlines() if "Jail list" in line), "")
    jails = [j.strip() for j in jail_line.split(":", 1)[-1].split(",") if j.strip()]

    banned_by_jail: dict[str, list[str]] = {}
    for jail in jails:
        proc = _run(["fail2ban-client", "status", jail], timeout=5)
        if proc is None or proc.returncode != 0:
            continue
        ip_line = next((line for line in proc.stdout.splitlines() if "Banned IP list" in line), "")
        banned_by_jail[jail] = [ip.strip() for ip in ip_line.split(":", 1)[-1].split() if ip.strip()]
    return {"available": True, "banned_by_jail": banned_by_jail}


def guess_base_devices(disk_devices: list[str]) -> list[str]:
    """Map partition device paths to their base disk, e.g. /dev/sda1 -> /dev/sda,
    /dev/nvme0n1p1 -> /dev/nvme0n1 - SMART health is a per-disk concept, not per-partition."""
    bases = set()
    for dev in disk_devices:
        match = _PARTITION_SUFFIX_RE.search(dev)
        bases.add(dev[: match.start()] if match else dev)
    return sorted(bases)


def disk_smart_health(devices: list[str]) -> dict:
    if not shutil.which("smartctl"):
        return {"available": False}
    statuses = {}
    for device in devices:
        proc = _run(["smartctl", "-H", device], timeout=10)
        if proc is None:
            statuses[device] = "unknown"
            continue
        output = proc.stdout.lower()
        if "passed" in output:
            statuses[device] = "passed"
        elif "failed" in output:
            statuses[device] = "failed"
        else:
            statuses[device] = "unknown"
    return {"available": True, "devices": statuses}


def pending_updates() -> dict:
    if shutil.which("apt"):
        proc = _run(["apt", "list", "--upgradable"], timeout=15)
        if proc is not None and proc.returncode == 0:
            lines = [line for line in proc.stdout.splitlines() if "/" in line and not line.startswith("Listing")]
            return {"available": True, "manager": "apt", "count": len(lines)}
    if shutil.which("dnf"):
        proc = _run(["dnf", "check-update"], timeout=20)
        if proc is not None:
            # dnf check-update exits 100 (updates available) or 0 (none), not just 0-on-success
            lines = [
                line
                for line in proc.stdout.splitlines()
                if line.strip() and not line.startswith(("Last metadata", "Obsoleting"))
            ]
            return {"available": True, "manager": "dnf", "count": len(lines)}
    return {"available": False}


def restart_service(unit: str) -> bool:
    proc = _run(["systemctl", "restart", unit], timeout=15)
    return proc is not None and proc.returncode == 0


def reboot_system() -> bool:
    proc = _run(["systemctl", "reboot"], timeout=5)
    return proc is not None and proc.returncode == 0
