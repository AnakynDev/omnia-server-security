"""
Mock data layer for Omnia. Everything here is fake/simulated so the UI can
be built and demoed without real system access. Every screen only talks to
`self.app.backend`, using the method names below — swap this class for a
real one (same names) to go live. Suggested real-world sources are noted
next to each method.
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

ConnState = Literal["connected", "reconnecting", "disconnected"]
LoginResult = Literal["success", "failure", "invalid_user", "disconnected"]

USERNAMES = ["miguel", "root", "deploy", "backup", "admin", "ubuntu"]
KEY_LABELS = ["laptop-thinkpad", "desktop-home", "phone-termux", "ci-runner"]
KEY_TYPES = ["ED25519", "RSA", "ECDSA"]
SERVER_NAMES = ["São Paulo, BR (Vivo)", "Rio de Janeiro, BR (Claro)", "Fortaleza, BR (Oi)"]
SERVICE_NAMES = ["nginx", "postgresql", "docker", "ssh", "fail2ban", "cron", "atlas-worker"]
PROCESS_NAMES = [
    "python3.12", "postgres", "nginx", "sshd", "docker", "systemd",
    "atlas-worker", "redis-server", "node", "cron",
]


def fmt_bytes(mb: float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.0f} MB"


def fmt_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}min"
    if hours:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"


def fmt_ago(ts: float) -> str:
    delta = max(0, int(time.time() - ts))
    if delta < 60:
        return f"{delta}s atrás"
    if delta < 3600:
        return f"{delta // 60}min atrás"
    if delta < 86400:
        return f"{delta // 3600}h atrás"
    return f"{delta // 86400}d atrás"


def fake_ip(private: bool = False) -> str:
    if private:
        return f"192.168.{random.randint(0,4)}.{random.randint(2,254)}"
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


# --------------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------------


@dataclass
class SSHActiveSession:
    user: str
    ip: str
    tty: str
    key_label: str
    key_type: str
    connected_since: float


@dataclass
class LoginEvent:
    ts: float
    user: str
    ip: str
    result: LoginResult
    key_label: str = ""
    detail: str = ""


@dataclass
class SSHKey:
    label: str
    key_type: str
    fingerprint: str
    last_used: float | None


@dataclass
class BlockedIP:
    ip: str
    rule: str
    banned_at: float


@dataclass
class Disk:
    mount: str
    device: str
    fstype: str
    used_gb: float
    total_gb: float

    @property
    def pct(self) -> float:
        return (self.used_gb / self.total_gb * 100) if self.total_gb else 0.0


@dataclass
class DiskHealth:
    device: str
    status: str  # ok | failing | unknown


@dataclass
class Process:
    pid: int
    name: str
    cpu_pct: float
    mem_pct: float


@dataclass
class SpeedTestResult:
    ts: float
    download_mbps: float
    upload_mbps: float
    ping_ms: float
    server_name: str


@dataclass
class Service:
    name: str
    status: str  # active | inactive | failed


@dataclass
class ContainerInfo:
    name: str
    image: str
    status: str
    state: str


@dataclass
class Alert:
    ts: float
    kind: str
    description: str


class OmniaMockBackend:
    def __init__(self) -> None:
        now = time.time()
        self.hostname = "atlas.local"
        self.start_ts = now
        self.ready = False  # simula loading inicial
        self.connection_state: ConnState = "connected"
        self._connection_ticks_until_flip = random.randint(40, 90)

        # ---- SSH -----------------------------------------------------
        self.ssh_active_sessions: list[SSHActiveSession] = [
            SSHActiveSession("miguel", fake_ip(private=True), "pts/0", "laptop-thinkpad", "ED25519", now - 1800),
        ]
        self.login_history: list[LoginEvent] = []
        for _ in range(80):
            self.login_history.append(self._make_login_event(ts_offset=-random.uniform(0, 3600 * 72)))
        self.ssh_keys: list[SSHKey] = [
            SSHKey("laptop-thinkpad", "ED25519", "SHA256:k3n2...ax9", now - 1800),
            SSHKey("desktop-home", "ED25519", "SHA256:pz81...q2m", now - 3600 * 30),
            SSHKey("phone-termux", "RSA", "SHA256:9fd2...117", now - 3600 * 24 * 9),
            SSHKey("ci-runner", "ED25519", "SHA256:aa02...9zz", None),
        ]
        self.fail2ban_available = True
        self.blocked_ips: list[BlockedIP] = []

        # ---- Sistema ----------------------------------------------------
        self.cpu_core_count = 8
        self.cpu_per_core = [random.uniform(5, 40) for _ in range(self.cpu_core_count)]
        self.mem_total_mb = 16384.0
        self.mem_used_mb = random.uniform(4000, 8000)
        self.swap_pct = random.uniform(0, 15)
        self.temps_available = True
        self.temps = {"CPU": random.uniform(38, 55), "NVMe": random.uniform(30, 45)}
        self.disks = [
            Disk("/", "/dev/sda1", "ext4", used_gb=random.uniform(60, 90), total_gb=118.0),
            Disk("/data", "/dev/sdb1", "ext4", used_gb=random.uniform(600, 1600), total_gb=2000.0),
        ]
        self.disk_health = [
            DiskHealth("/dev/sda", "ok"),
            DiskHealth("/dev/sdb", "ok"),
        ]
        self.net_upload_kbps = 0.0
        self.net_download_kbps = 0.0
        self.net_history_up: deque = deque([0.0] * 40, maxlen=40)
        self.net_history_down: deque = deque([0.0] * 40, maxlen=40)
        self.gpu_available = False
        self.gpu_engines: dict[str, float] = {}
        self.processes: list[Process] = [
            Process(pid=2000 + i, name=name, cpu_pct=0.0, mem_pct=0.0)
            for i, name in enumerate(PROCESS_NAMES)
        ]

        # ---- Speed test ------------------------------------------------
        self.speedtest_running = False
        self.speedtest_ticks_remaining = 0
        self.speedtest_error: str | None = None
        self.speedtest_history: list[SpeedTestResult] = []

        # ---- Ferramentas extras -----------------------------------------
        self.services = [Service(name, random.choice(["active", "active", "active", "failed"]))
                          for name in SERVICE_NAMES]
        self.containers_available = False
        self.containers: list[ContainerInfo] = []
        self.pending_updates_count = random.randint(0, 14)
        self.pending_updates_manager = "apt"
        self.dangerous_actions_enabled = True
        self._restarting_service: str | None = None
        self._restart_ticks_remaining = 0

        # ---- Alertas ------------------------------------------------------
        self.alerts: list[Alert] = []
        for _ in range(6):
            self._spawn_alert(ts_offset=-random.uniform(0, 3600 * 48))

    # -- geração de eventos falsos --------------------------------------

    def _make_login_event(self, ts_offset: float = 0.0) -> LoginEvent:
        roll = random.random()
        user = random.choice(USERNAMES)
        ip = fake_ip()
        if roll < 0.55:
            return LoginEvent(time.time() + ts_offset, user, ip, "success", key_label=random.choice(KEY_LABELS))
        elif roll < 0.85:
            return LoginEvent(
                time.time() + ts_offset, user, ip, "failure",
                detail=random.choice(["chave não autorizada", "senha incorreta", "timeout"]),
            )
        elif roll < 0.95:
            return LoginEvent(time.time() + ts_offset, "???", ip, "invalid_user", detail="usuário inexistente")
        else:
            return LoginEvent(time.time() + ts_offset, user, ip, "disconnected", detail="conexão encerrada")

    def _spawn_alert(self, ts_offset: float = 0.0) -> None:
        kind, desc = random.choice([
            ("login_bruteforce", "Múltiplas tentativas de login falhas do mesmo IP"),
            ("new_key", "Uso de uma chave SSH nunca vista antes"),
            ("disk_almost_full", "Disco /data acima de 85% de uso"),
            ("service_failed", "Serviço fail2ban reiniciado após falha"),
        ])
        self.alerts.append(Alert(time.time() + ts_offset, kind, desc))

    # -- simulação viva ---------------------------------------------------

    def tick(self) -> None:
        if not self.ready:
            self.ready = True
            return

        self._connection_ticks_until_flip -= 1
        if self._connection_ticks_until_flip <= 0:
            self._connection_ticks_until_flip = random.randint(40, 90)
            if self.connection_state == "connected":
                self.connection_state = random.choice(["connected", "connected", "reconnecting"])
            elif self.connection_state == "reconnecting":
                self.connection_state = random.choice(["connected", "disconnected"])
            else:
                self.connection_state = "reconnecting"

        if random.random() < 0.08:
            event = self._make_login_event()
            self.login_history.insert(0, event)
            if event.result == "failure":
                recent_fails = [
                    e for e in self.login_history[:6]
                    if e.ip == event.ip and e.result == "failure"
                ]
                if len(recent_fails) >= 3 and self.fail2ban_available:
                    if not any(b.ip == event.ip for b in self.blocked_ips):
                        self.blocked_ips.append(BlockedIP(event.ip, "sshd", time.time()))
                        self.alerts.append(Alert(
                            time.time(), "login_bruteforce",
                            f"IP {event.ip} banido após múltiplas falhas de login",
                        ))

        self.cpu_per_core = [
            max(0.0, min(100.0, c + random.uniform(-8, 8))) for c in self.cpu_per_core
        ]
        self.mem_used_mb = max(500.0, min(self.mem_total_mb * 0.95, self.mem_used_mb + random.uniform(-200, 200)))
        self.swap_pct = max(0.0, min(100.0, self.swap_pct + random.uniform(-1, 1)))
        for label in self.temps:
            self.temps[label] = max(25.0, min(85.0, self.temps[label] + random.uniform(-1.5, 1.5)))

        self.net_upload_kbps = max(0.0, self.net_upload_kbps + random.uniform(-80, 120))
        self.net_download_kbps = max(0.0, self.net_download_kbps + random.uniform(-150, 250))
        self.net_history_up.append(self.net_upload_kbps)
        self.net_history_down.append(self.net_download_kbps)

        for proc in self.processes:
            proc.cpu_pct = max(0.0, min(100.0, proc.cpu_pct + random.uniform(-5, 5)))
            proc.mem_pct = max(0.0, min(100.0, proc.mem_pct + random.uniform(-1, 1)))

        if random.random() < 0.05:
            self.disks[1].used_gb = min(self.disks[1].total_gb * 0.98, self.disks[1].used_gb + random.uniform(0, 2))
            if self.disks[1].pct > 85 and random.random() < 0.3:
                self.alerts.append(Alert(time.time(), "disk_almost_full", "Disco /data acima de 85% de uso"))

        if self.speedtest_running:
            self.speedtest_ticks_remaining -= 1
            if self.speedtest_ticks_remaining <= 0:
                self.speedtest_running = False
                if random.random() < 0.12:
                    self.speedtest_error = "Falha ao conectar ao servidor de teste. Tente novamente."
                else:
                    self.speedtest_error = None
                    self.speedtest_history.append(
                        SpeedTestResult(
                            ts=time.time(),
                            download_mbps=random.uniform(180, 480),
                            upload_mbps=random.uniform(40, 180),
                            ping_ms=random.uniform(4, 28),
                            server_name=random.choice(SERVER_NAMES),
                        )
                    )

        if self._restarting_service is not None:
            self._restart_ticks_remaining -= 1
            if self._restart_ticks_remaining <= 0:
                for svc in self.services:
                    if svc.name == self._restarting_service:
                        svc.status = "active"
                self._restarting_service = None

        if random.random() < 0.02:
            self.pending_updates_count = max(0, self.pending_updates_count + random.choice([-1, 1]))

    # -- API pública de leitura -------------------------------------------

    def cpu_total_pct(self) -> float:
        return sum(self.cpu_per_core) / len(self.cpu_per_core) if self.cpu_per_core else 0.0

    def mem_pct(self) -> float:
        return self.mem_used_mb / self.mem_total_mb * 100 if self.mem_total_mb else 0.0

    def uptime_seconds(self) -> float:
        return time.time() - self.start_ts + 3600 * 240

    def sorted_processes(self, by: str = "cpu") -> list[Process]:
        key = (lambda p: p.cpu_pct) if by == "cpu" else (lambda p: p.mem_pct)
        return sorted(self.processes, key=key, reverse=True)

    def restarting_service_name(self) -> str | None:
        return self._restarting_service

    # -- ações (efeitos colaterais) ----------------------------------------

    def restart_service(self, name: str) -> None:
        for svc in self.services:
            if svc.name == name:
                svc.status = "inactive"
        self._restarting_service = name
        self._restart_ticks_remaining = random.randint(3, 6)

    def reboot_server(self) -> None:
        self.start_ts = time.time()
        self.alerts.append(Alert(time.time(), "reboot", "Servidor reiniciado manualmente via Omnia"))

    def start_speed_test(self) -> None:
        if self.speedtest_running:
            return
        self.speedtest_running = True
        self.speedtest_error = None
        self.speedtest_ticks_remaining = random.randint(8, 18)
