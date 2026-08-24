import pytest

from omnia_daemon.db import Database


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "omnia.db")
    yield database
    database.close()


def test_ingest_state_roundtrip(db):
    assert db.get_state("cursor") is None
    db.set_state("cursor", "abc123")
    assert db.get_state("cursor") == "abc123"
    db.set_state("cursor", "def456")  # upsert, not a duplicate row
    assert db.get_state("cursor") == "def456"


def _insert_event(db, *, ts, event_type, username="miguel", source_ip="1.2.3.4", **kwargs):
    db.insert_login_event(
        ts=ts,
        event_type=event_type,
        username=username,
        source_ip=source_ip,
        source_port=kwargs.get("source_port", 22),
        key_type=kwargs.get("key_type"),
        key_fingerprint=kwargs.get("key_fingerprint"),
        key_label=kwargs.get("key_label"),
        detail=kwargs.get("detail"),
    )


def test_count_failed_logins_since_only_counts_matching_ip_and_window(db):
    _insert_event(db, ts=100.0, event_type="failed_publickey", source_ip="1.2.3.4")
    _insert_event(db, ts=110.0, event_type="invalid_user", source_ip="1.2.3.4")
    _insert_event(db, ts=120.0, event_type="failed_publickey", source_ip="9.9.9.9")  # different IP
    _insert_event(db, ts=50.0, event_type="failed_publickey", source_ip="1.2.3.4")  # before window

    assert db.count_failed_logins_since("1.2.3.4", since_ts=90.0) == 2
    assert db.count_failed_logins_since("9.9.9.9", since_ts=90.0) == 1
    assert db.count_failed_logins_since("1.2.3.4", since_ts=0.0) == 3


def test_count_accepted_with_key_before_excludes_events_at_or_after_cutoff(db):
    _insert_event(db, ts=100.0, event_type="accepted_publickey", key_fingerprint="SHA256:aaa")
    assert db.count_accepted_with_key_before("SHA256:aaa", before_ts=100.0) == 0  # not strictly before
    assert db.count_accepted_with_key_before("SHA256:aaa", before_ts=101.0) == 1
    assert db.count_accepted_with_key_before("SHA256:bbb", before_ts=200.0) == 0


def test_latest_accepted_key_picks_most_recent_before_session_start(db):
    _insert_event(
        db, ts=100.0, event_type="accepted_publickey", username="miguel", source_ip="1.2.3.4",
        key_fingerprint="SHA256:old", key_label="old-key",
    )
    _insert_event(
        db, ts=200.0, event_type="accepted_publickey", username="miguel", source_ip="1.2.3.4",
        key_fingerprint="SHA256:new", key_label="new-key",
    )
    row = db.latest_accepted_key(username="miguel", source_ip="1.2.3.4", before_ts=205.0)
    assert row["key_label"] == "new-key"

    row = db.latest_accepted_key(username="miguel", source_ip="1.2.3.4", before_ts=150.0)
    assert row["key_label"] == "old-key"

    assert db.latest_accepted_key(username="someone-else", source_ip="1.2.3.4", before_ts=205.0) is None


def test_alert_cooldown_tracks_last_ts_per_alert_type(db):
    assert db.last_alert_ts("failed_login:1.2.3.4") is None
    db.insert_alert(ts=100.0, alert_type="failed_login:1.2.3.4", detail="first")
    assert db.last_alert_ts("failed_login:1.2.3.4") == 100.0
    db.insert_alert(ts=200.0, alert_type="failed_login:1.2.3.4", detail="second")
    assert db.last_alert_ts("failed_login:1.2.3.4") == 200.0
    assert db.last_alert_ts("failed_login:9.9.9.9") is None  # different key, unaffected


def test_cleanup_retention_deletes_old_rows_only(db):
    import time

    now = time.time()
    _insert_event(db, ts=now - 200 * 86400, event_type="accepted_publickey")  # older than retention
    _insert_event(db, ts=now - 1 * 86400, event_type="accepted_publickey")  # recent, kept
    db.insert_resource_snapshot(
        ts=now - 200 * 86400, cpu_percent=1, ram_percent=1, ram_used_bytes=1, ram_total_bytes=1,
        swap_percent=0, net_rx_bytes_per_sec=0, net_tx_bytes_per_sec=0, disk=[], gpu={},
    )
    db.insert_resource_snapshot(
        ts=now - 1 * 86400, cpu_percent=1, ram_percent=1, ram_used_bytes=1, ram_total_bytes=1,
        swap_percent=0, net_rx_bytes_per_sec=0, net_tx_bytes_per_sec=0, disk=[], gpu={},
    )

    db.cleanup_retention(login_event_days=90, resource_snapshot_days=30)

    remaining_events = db.recent_login_events(limit=100)
    assert len(remaining_events) == 1
    remaining_snapshots = db.resource_snapshots_since(0)
    assert len(remaining_snapshots) == 1
