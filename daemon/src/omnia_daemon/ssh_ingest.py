"""Streaming/tailing I/O that feeds sshd log lines into the parser and the DB.

Primary source: a single long-lived `journalctl --follow` subprocess, with
its cursor persisted so a restart resumes exactly where it left off
(no replay, no gap). Fallback (no journald, e.g. non-systemd or non-persistent
journal): incremental tail of auth.log/secure, tracked by (inode, byte
offset) to survive both logrotate styles (create+rename and copytruncate),
polled on a cheap stat() interval rather than a filesystem-watch dependency -
a stat() call is microseconds, and this path is only exercised when journald
is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path

from .config import OmniaConfig, expand_path
from .db import Database
from .key_fingerprint import KeyStore
from .ssh_log_parser import is_sshd_proc, parse_sshd_message, parse_syslog_line

logger = logging.getLogger(__name__)

CURSOR_STATE_KEY = "journald_cursor"
TAIL_INODE_KEY = "authlog_inode"
TAIL_OFFSET_KEY = "authlog_offset"

_MAX_BACKOFF_SECONDS = 30.0


def _find_working_unit(candidates: list[str]) -> str | None:
    for unit in candidates:
        try:
            result = subprocess.run(
                ["journalctl", "-u", unit, "-n", "1", "--no-pager"],
                capture_output=True,
                timeout=5,
                text=True,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode == 0:
            return unit
    return None


async def _stream_journald(unit: str, db: Database) -> AsyncIterator[tuple[float, str]]:
    cursor = db.get_state(CURSOR_STATE_KEY)
    cmd = ["journalctl", "-u", unit, "-o", "json", "--follow", "--no-pager"]
    if cursor:
        cmd += ["--after-cursor", cursor]
    else:
        cmd += ["-n", "0"]  # first run: start from now, don't replay entire history

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        assert proc.stdout is not None
        async for raw_line in proc.stdout:
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            message = entry.get("MESSAGE")
            if not isinstance(message, str):
                continue
            cursor_val = entry.get("__CURSOR")
            if cursor_val:
                db.set_state(CURSOR_STATE_KEY, cursor_val)
            ts_micro = entry.get("__REALTIME_TIMESTAMP")
            ts = float(ts_micro) / 1_000_000 if ts_micro else time.time()
            yield ts, message
        raise ConnectionError(f"journalctl --follow exited with code {proc.returncode}")
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()


async def _tail_auth_log(path: Path, db: Database, poll_interval: float = 2.0) -> AsyncIterator[tuple[float, str]]:
    buffer = b""
    while True:
        try:
            stat = path.stat()
        except FileNotFoundError:
            await asyncio.sleep(poll_interval)
            continue

        stored_inode = db.get_state(TAIL_INODE_KEY)
        stored_offset_raw = db.get_state(TAIL_OFFSET_KEY)
        inode = str(stat.st_ino)
        offset = int(stored_offset_raw) if stored_offset_raw else 0

        if stored_inode is None:
            # First run: start at end of file, don't replay the entire history.
            offset = stat.st_size
            db.set_state(TAIL_INODE_KEY, inode)
            db.set_state(TAIL_OFFSET_KEY, str(offset))
        elif stored_inode != inode:
            # logrotate create/rename style: new inode at the same path.
            offset = 0
            buffer = b""
            db.set_state(TAIL_INODE_KEY, inode)
            db.set_state(TAIL_OFFSET_KEY, str(offset))
        elif stat.st_size < offset:
            # logrotate copytruncate style: same inode, file shrank.
            offset = 0
            buffer = b""
            db.set_state(TAIL_OFFSET_KEY, str(offset))

        if stat.st_size > offset:
            with path.open("rb") as fh:
                fh.seek(offset)
                chunk = fh.read()
            buffer += chunk
            lines = buffer.split(b"\n")
            buffer = lines.pop()  # trailing partial line, kept for next read
            consumed = sum(len(line) + 1 for line in lines)
            db.set_state(TAIL_OFFSET_KEY, str(offset + consumed))

            now_year = time.localtime().tm_year
            for line_bytes in lines:
                if not line_bytes:
                    continue
                line = line_bytes.decode("utf-8", errors="replace")
                parsed = parse_syslog_line(line, now_year=now_year)
                if parsed is None:
                    continue
                ts, proc_field, message = parsed
                if not is_sshd_proc(proc_field):
                    continue
                yield ts, message

        await asyncio.sleep(poll_interval)


def _pick_source(config: OmniaConfig, db: Database) -> AsyncIterator[tuple[float, str]]:
    unit = None
    if config.ssh.use_journald and shutil.which("journalctl"):
        unit = _find_working_unit(config.ssh.systemd_unit_candidates)

    if unit:
        logger.info("ssh_ingest: using journald unit %s", unit)
        return _stream_journald(unit, db)

    auth_log_path = expand_path(config.paths.auth_log_path)
    logger.info("ssh_ingest: journald unavailable, tailing %s", auth_log_path)
    return _tail_auth_log(auth_log_path, db)


async def run_ssh_ingest(
    config: OmniaConfig,
    db: Database,
    key_store: KeyStore,
    on_event: Callable[[dict], None],
) -> None:
    """Runs forever, persisting parsed SSH events and handing them to `on_event`.

    Reconnects with backoff if the log source drops (journald restart,
    transient I/O error) instead of taking the whole daemon down with it.
    """
    backoff = 1.0
    while True:
        try:
            source = _pick_source(config, db)
            async for ts, message in source:
                backoff = 1.0
                parsed = parse_sshd_message(message)
                if parsed is None:
                    continue
                key_label = (
                    key_store.label_for_fingerprint(parsed.key_fingerprint)
                    if parsed.key_fingerprint
                    else None
                )
                db.insert_login_event(
                    ts=ts,
                    event_type=parsed.event_type,
                    username=parsed.username,
                    source_ip=parsed.source_ip,
                    source_port=parsed.source_port,
                    key_type=parsed.key_type,
                    key_fingerprint=parsed.key_fingerprint,
                    key_label=key_label,
                    detail=parsed.raw_message,
                )
                on_event(
                    {
                        "ts": ts,
                        "event_type": parsed.event_type,
                        "username": parsed.username,
                        "source_ip": parsed.source_ip,
                        "source_port": parsed.source_port,
                        "key_type": parsed.key_type,
                        "key_fingerprint": parsed.key_fingerprint,
                        "key_label": key_label,
                        "detail": parsed.raw_message,
                    }
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ssh_ingest: source failed, retrying in %.0fs", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
