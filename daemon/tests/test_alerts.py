import pytest

from omnia_daemon.alerts import AlertManager
from omnia_daemon.config import AlertsConfig, SmtpConfig
from omnia_daemon.db import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "omnia.db")
    yield database
    database.close()


@pytest.fixture
def alerts_cfg():
    return AlertsConfig(
        enabled=True,
        failed_login_threshold=3,
        failed_login_window_minutes=10,
        disk_usage_percent_threshold=90,
        alert_on_new_key=True,
        cooldown_minutes=30,
    )


@pytest.fixture
def smtp_cfg():
    return SmtpConfig(enabled=False)  # logs instead of sending; safe for tests


def _failed_event(ts, ip="1.2.3.4"):
    return {"ts": ts, "event_type": "failed_publickey", "username": "root", "source_ip": ip}


def test_failed_login_alert_fires_once_threshold_reached(db, alerts_cfg, smtp_cfg):
    manager = AlertManager(alerts_cfg, smtp_cfg, db)
    now = 1_000_000.0

    for i, ts in enumerate([now, now + 1, now + 2]):
        db.insert_login_event(
            ts=ts, event_type="failed_publickey", username="root", source_ip="1.2.3.4",
            source_port=22, key_type=None, key_fingerprint=None, key_label=None, detail=None,
        )
        manager.on_ssh_event(_failed_event(ts))

    alerts = db.recent_alerts(limit=10)
    assert len(alerts) == 1  # only fires once threshold (3) is first reached, not before or repeatedly
    assert alerts[0]["alert_type"] == "failed_login:1.2.3.4"


def test_failed_login_alert_respects_cooldown(db, alerts_cfg, smtp_cfg):
    manager = AlertManager(alerts_cfg, smtp_cfg, db)
    now = 1_000_000.0
    for ts in [now, now + 1, now + 2, now + 3, now + 4]:
        db.insert_login_event(
            ts=ts, event_type="failed_publickey", username="root", source_ip="1.2.3.4",
            source_port=22, key_type=None, key_fingerprint=None, key_label=None, detail=None,
        )
        manager.on_ssh_event(_failed_event(ts))

    # threshold (3) was crossed once, then stayed crossed for events 4 and 5 -
    # cooldown must suppress re-firing on every subsequent failure.
    assert len(db.recent_alerts(limit=10)) == 1


def test_new_key_alert_fires_only_on_first_use(db, alerts_cfg, smtp_cfg):
    manager = AlertManager(alerts_cfg, smtp_cfg, db)

    first = {"ts": 100.0, "event_type": "accepted_publickey", "username": "miguel",
              "source_ip": "1.2.3.4", "key_type": "ED25519", "key_fingerprint": "SHA256:aaa",
              "key_label": "laptop"}
    db.insert_login_event(
        ts=100.0, event_type="accepted_publickey", username="miguel", source_ip="1.2.3.4",
        source_port=22, key_type="ED25519", key_fingerprint="SHA256:aaa", key_label="laptop", detail=None,
    )
    manager.on_ssh_event(first)

    second = dict(first, ts=200.0)
    db.insert_login_event(
        ts=200.0, event_type="accepted_publickey", username="miguel", source_ip="1.2.3.4",
        source_port=22, key_type="ED25519", key_fingerprint="SHA256:aaa", key_label="laptop", detail=None,
    )
    manager.on_ssh_event(second)

    alerts = db.recent_alerts(limit=10)
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "new_key:SHA256:aaa"


def test_disk_usage_alert_fires_above_threshold(db, alerts_cfg, smtp_cfg):
    manager = AlertManager(alerts_cfg, smtp_cfg, db)
    snapshot = {"disks": [{"mountpoint": "/", "device": "/dev/sda1", "percent": 95.0}]}
    manager.on_metrics_snapshot(snapshot)

    alerts = db.recent_alerts(limit=10)
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "disk_usage:/"


def test_disk_usage_alert_does_not_fire_below_threshold(db, alerts_cfg, smtp_cfg):
    manager = AlertManager(alerts_cfg, smtp_cfg, db)
    snapshot = {"disks": [{"mountpoint": "/", "device": "/dev/sda1", "percent": 50.0}]}
    manager.on_metrics_snapshot(snapshot)

    assert db.recent_alerts(limit=10) == []


def test_alerts_disabled_globally_suppresses_everything(db, alerts_cfg, smtp_cfg):
    alerts_cfg.enabled = False
    manager = AlertManager(alerts_cfg, smtp_cfg, db)
    manager.on_metrics_snapshot({"disks": [{"mountpoint": "/", "device": "/dev/sda1", "percent": 99.0}]})
    manager.on_ssh_event(_failed_event(100.0))

    assert db.recent_alerts(limit=10) == []
