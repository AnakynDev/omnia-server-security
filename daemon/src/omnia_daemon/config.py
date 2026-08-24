"""Configuration loading for the Omnia daemon.

Config lives in a single TOML file. Every field has a built-in default, so a
config file only needs to override what differs from the defaults (including
an empty/missing file, which yields an all-defaults config).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATHS = (
    Path("/etc/omnia/config.toml"),
    Path.home() / ".config" / "omnia" / "config.toml",
)


@dataclass
class PathsConfig:
    socket_path: str = "/run/omnia/omnia.sock"
    db_path: str = "/var/lib/omnia/omnia.db"
    authorized_keys_path: str = "~/.ssh/authorized_keys"
    auth_log_path: str = "/var/log/auth.log"


@dataclass
class SshConfig:
    systemd_unit_candidates: list[str] = field(default_factory=lambda: ["ssh.service", "sshd.service"])
    use_journald: bool = True


@dataclass
class AlertsConfig:
    enabled: bool = True
    failed_login_threshold: int = 5
    failed_login_window_minutes: int = 10
    disk_usage_percent_threshold: int = 90
    alert_on_new_key: bool = True
    cooldown_minutes: int = 30


@dataclass
class SmtpConfig:
    enabled: bool = False
    host: str = ""
    port: int = 587
    use_tls: bool = True
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)


@dataclass
class SpeedtestConfig:
    enabled: bool = True
    interval_hours: float = 6.0


@dataclass
class RetentionConfig:
    resource_snapshot_days: int = 30
    login_event_days: int = 90
    resource_snapshot_interval_minutes: int = 5


@dataclass
class MetricsConfig:
    poll_interval_seconds: float = 2.0


@dataclass
class GpuConfig:
    enabled: bool = True
    retry_minutes: int = 10


@dataclass
class FeaturesConfig:
    docker: bool = True
    fail2ban: bool = True
    smart_health: bool = True
    pending_updates: bool = True
    allow_service_restart: bool = False
    allow_reboot: bool = False


@dataclass
class OmniaConfig:
    paths: PathsConfig = field(default_factory=PathsConfig)
    ssh: SshConfig = field(default_factory=SshConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    speedtest: SpeedtestConfig = field(default_factory=SpeedtestConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    gpu: GpuConfig = field(default_factory=GpuConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)


_SECTION_TYPES = {
    "paths": PathsConfig,
    "ssh": SshConfig,
    "alerts": AlertsConfig,
    "smtp": SmtpConfig,
    "speedtest": SpeedtestConfig,
    "retention": RetentionConfig,
    "metrics": MetricsConfig,
    "gpu": GpuConfig,
    "features": FeaturesConfig,
}


def _build_section(section_cls: type, raw: dict) -> object:
    valid_fields = {f for f in section_cls.__dataclass_fields__}
    unknown = set(raw) - valid_fields
    if unknown:
        raise ValueError(f"Unknown keys for [{section_cls.__name__}]: {sorted(unknown)}")
    return section_cls(**raw)


def load_config(path: str | Path | None = None) -> OmniaConfig:
    """Load config from `path`, or the first existing default path, or pure defaults."""
    candidates = [Path(path)] if path is not None else list(DEFAULT_CONFIG_PATHS)
    raw: dict = {}
    for candidate in candidates:
        if candidate.is_file():
            with candidate.open("rb") as fh:
                raw = tomllib.load(fh)
            break

    kwargs = {}
    for section_name, section_cls in _SECTION_TYPES.items():
        section_raw = raw.get(section_name, {})
        kwargs[section_name] = _build_section(section_cls, section_raw)
    return OmniaConfig(**kwargs)


def expand_path(raw_path: str) -> Path:
    return Path(raw_path).expanduser()
