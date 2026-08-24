"""Alert rules and email delivery.

Every alert is keyed (e.g. `failed_login:<ip>`, `new_key:<fingerprint>`,
`disk_usage:<mountpoint>`) so the cooldown in config applies per-offender,
not globally - one noisy IP shouldn't suppress a disk-space warning, and
two different attacking IPs should each still get their own alert.
"""

from __future__ import annotations

import logging
import smtplib
import time
from email.message import EmailMessage

from .config import AlertsConfig, SmtpConfig
from .db import Database

logger = logging.getLogger(__name__)

_FAILED_EVENT_TYPES = {"failed_publickey", "invalid_user", "failed_other"}


def _send_email(smtp_cfg: SmtpConfig, subject: str, body: str) -> None:
    if not smtp_cfg.enabled:
        logger.info("email alerts disabled, alert was: %s - %s", subject, body)
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_cfg.from_addr
    msg["To"] = ", ".join(smtp_cfg.to_addrs)
    msg.set_content(body)
    try:
        with smtplib.SMTP(smtp_cfg.host, smtp_cfg.port, timeout=15) as server:
            if smtp_cfg.use_tls:
                server.starttls()
            if smtp_cfg.username:
                server.login(smtp_cfg.username, smtp_cfg.password)
            server.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        logger.error("failed to send alert email: %s", exc)


class AlertManager:
    def __init__(self, alerts_cfg: AlertsConfig, smtp_cfg: SmtpConfig, db: Database):
        self._cfg = alerts_cfg
        self._smtp_cfg = smtp_cfg
        self._db = db

    def _should_fire(self, alert_key: str) -> bool:
        last = self._db.last_alert_ts(alert_key)
        return last is None or (time.time() - last) >= self._cfg.cooldown_minutes * 60

    def _fire(self, alert_key: str, *, subject: str, body: str) -> None:
        self._db.insert_alert(ts=time.time(), alert_type=alert_key, detail=body)
        _send_email(self._smtp_cfg, subject, body)
        logger.warning("ALERT [%s]: %s", alert_key, subject)

    def on_ssh_event(self, event: dict) -> None:
        if not self._cfg.enabled:
            return

        if event["event_type"] in _FAILED_EVENT_TYPES:
            self._check_failed_login(event)
        elif event["event_type"] == "accepted_publickey" and self._cfg.alert_on_new_key:
            self._check_new_key(event)

    def _check_failed_login(self, event: dict) -> None:
        ip = event.get("source_ip")
        if not ip:
            return
        # Windowed off the event's own timestamp, not wall-clock time.time():
        # matters when catching up on a backlog (e.g. right after a restart),
        # where "now" and "when this event happened" can differ a lot.
        since = event["ts"] - self._cfg.failed_login_window_minutes * 60
        count = self._db.count_failed_logins_since(ip, since)
        if count < self._cfg.failed_login_threshold:
            return
        alert_key = f"failed_login:{ip}"
        if self._should_fire(alert_key):
            self._fire(
                alert_key,
                subject=f"[Omnia] {count} tentativas de login SSH falhas de {ip}",
                body=(
                    f"{count} tentativas de login falhas do IP {ip} nos ultimos "
                    f"{self._cfg.failed_login_window_minutes} minutos."
                ),
            )

    def _check_new_key(self, event: dict) -> None:
        fingerprint = event.get("key_fingerprint")
        if not fingerprint:
            return
        prior_count = self._db.count_accepted_with_key_before(fingerprint, event["ts"])
        if prior_count > 0:
            return
        alert_key = f"new_key:{fingerprint}"
        if self._should_fire(alert_key):
            label = event.get("key_label") or fingerprint
            self._fire(
                alert_key,
                subject=f"[Omnia] Novo login SSH com chave nunca usada antes ({label})",
                body=(
                    f"A chave '{label}' ({event.get('key_type')}, {fingerprint}) foi usada para logar "
                    f"como {event.get('username')} a partir de {event.get('source_ip')} pela primeira vez."
                ),
            )

    def on_metrics_snapshot(self, snapshot: dict) -> None:
        if not self._cfg.enabled:
            return
        for disk in snapshot.get("disks", []):
            if disk["percent"] < self._cfg.disk_usage_percent_threshold:
                continue
            alert_key = f"disk_usage:{disk['mountpoint']}"
            if self._should_fire(alert_key):
                self._fire(
                    alert_key,
                    subject=f"[Omnia] Disco {disk['mountpoint']} em {disk['percent']:.0f}% de uso",
                    body=(
                        f"O disco montado em {disk['mountpoint']} ({disk['device']}) esta em "
                        f"{disk['percent']:.1f}% de uso."
                    ),
                )
