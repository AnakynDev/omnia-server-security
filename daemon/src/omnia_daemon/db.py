"""SQLite persistence for the Omnia daemon.

Single-writer, low-volume workload (log events as they happen, one
downsampled resource snapshot every few minutes) so plain synchronous
sqlite3 calls on the event-loop thread are fast enough - no connection
pool or executor thread needed. WAL mode lets the socket server read
history concurrently with the ingest/collector writes.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS login_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event_type TEXT NOT NULL,
    username TEXT,
    source_ip TEXT,
    source_port INTEGER,
    key_type TEXT,
    key_fingerprint TEXT,
    key_label TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_login_events_ts ON login_events(ts);
CREATE INDEX IF NOT EXISTS idx_login_events_ip ON login_events(source_ip, ts);

CREATE TABLE IF NOT EXISTS speedtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    success INTEGER NOT NULL,
    download_mbps REAL,
    upload_mbps REAL,
    ping_ms REAL,
    server_name TEXT,
    server_id TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_speedtest_ts ON speedtest_results(ts);

CREATE TABLE IF NOT EXISTS resource_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    cpu_percent REAL,
    ram_percent REAL,
    ram_used_bytes INTEGER,
    ram_total_bytes INTEGER,
    swap_percent REAL,
    net_rx_bytes_per_sec REAL,
    net_tx_bytes_per_sec REAL,
    disk_json TEXT,
    gpu_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_resource_snapshots_ts ON resource_snapshots(ts);

CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    alert_type TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_alert_log_type_ts ON alert_log(alert_type, ts);
"""


class Database:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- ingest state (journald cursor / log tail offsets) -----------------

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM ingest_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO ingest_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    # -- login events --------------------------------------------------

    def insert_login_event(
        self,
        *,
        ts: float,
        event_type: str,
        username: str | None,
        source_ip: str | None,
        source_port: int | None,
        key_type: str | None,
        key_fingerprint: str | None,
        key_label: str | None,
        detail: str | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO login_events "
            "(ts, event_type, username, source_ip, source_port, key_type, key_fingerprint, key_label, detail) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, event_type, username, source_ip, source_port, key_type, key_fingerprint, key_label, detail),
        )

    def recent_login_events(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM login_events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    def count_failed_logins_since(self, source_ip: str, since_ts: float) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM login_events "
            "WHERE source_ip = ? AND ts >= ? AND event_type IN ('failed_publickey', 'invalid_user')",
            (source_ip, since_ts),
        ).fetchone()
        return row["n"]

    def latest_accepted_key(self, *, username: str, source_ip: str, before_ts: float) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM login_events "
            "WHERE username = ? AND source_ip = ? AND event_type = 'accepted_publickey' AND ts <= ? "
            "ORDER BY ts DESC LIMIT 1",
            (username, source_ip, before_ts),
        ).fetchone()

    def count_accepted_with_key_before(self, key_fingerprint: str, before_ts: float) -> int:
        """Count prior successful logins with this key, strictly before `before_ts`.

        Used to detect "first time this key was ever used" even though the
        triggering event has typically already been inserted by the caller.
        """
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM login_events "
            "WHERE key_fingerprint = ? AND event_type = 'accepted_publickey' AND ts < ?",
            (key_fingerprint, before_ts),
        ).fetchone()
        return row["n"]

    # -- speedtest -------------------------------------------------------

    def insert_speedtest_result(
        self,
        *,
        ts: float,
        success: bool,
        download_mbps: float | None,
        upload_mbps: float | None,
        ping_ms: float | None,
        server_name: str | None,
        server_id: str | None,
        error: str | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO speedtest_results "
            "(ts, success, download_mbps, upload_mbps, ping_ms, server_name, server_id, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, int(success), download_mbps, upload_mbps, ping_ms, server_name, server_id, error),
        )

    def recent_speedtest_results(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM speedtest_results ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    # -- resource snapshots ------------------------------------------------

    def insert_resource_snapshot(
        self,
        *,
        ts: float,
        cpu_percent: float,
        ram_percent: float,
        ram_used_bytes: int,
        ram_total_bytes: int,
        swap_percent: float,
        net_rx_bytes_per_sec: float,
        net_tx_bytes_per_sec: float,
        disk: object,
        gpu: object,
    ) -> None:
        self.conn.execute(
            "INSERT INTO resource_snapshots "
            "(ts, cpu_percent, ram_percent, ram_used_bytes, ram_total_bytes, swap_percent, "
            " net_rx_bytes_per_sec, net_tx_bytes_per_sec, disk_json, gpu_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ts, cpu_percent, ram_percent, ram_used_bytes, ram_total_bytes, swap_percent,
                net_rx_bytes_per_sec, net_tx_bytes_per_sec, json.dumps(disk), json.dumps(gpu),
            ),
        )

    def resource_snapshots_since(self, since_ts: float) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM resource_snapshots WHERE ts >= ? ORDER BY ts ASC", (since_ts,)
        ).fetchall()

    # -- alerts (with cooldown support) -------------------------------------

    def last_alert_ts(self, alert_type: str) -> float | None:
        row = self.conn.execute(
            "SELECT ts FROM alert_log WHERE alert_type = ? ORDER BY ts DESC LIMIT 1", (alert_type,)
        ).fetchone()
        return row["ts"] if row else None

    def insert_alert(self, *, ts: float, alert_type: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO alert_log (ts, alert_type, detail) VALUES (?, ?, ?)", (ts, alert_type, detail)
        )

    def recent_alerts(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM alert_log ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()

    # -- retention -------------------------------------------------------

    def cleanup_retention(self, *, login_event_days: int, resource_snapshot_days: int) -> None:
        now = time.time()
        self.conn.execute(
            "DELETE FROM login_events WHERE ts < ?", (now - login_event_days * 86400,)
        )
        self.conn.execute(
            "DELETE FROM resource_snapshots WHERE ts < ?", (now - resource_snapshot_days * 86400,)
        )
