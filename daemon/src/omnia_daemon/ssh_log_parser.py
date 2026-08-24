"""Pure parsing helpers for OpenSSH sshd log lines.

Kept separate from the streaming/tailing I/O (see `ssh_ingest.py`) so the
regexes can be unit-tested against fixed sample lines without a real
journald or log file. The same message text comes from journald's
`MESSAGE` field or a raw auth.log/secure line - only the timestamp framing
differs between those two sources, handled here too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_IP = r"(?P<ip>[0-9a-fA-F:.]+)"

ACCEPTED_RE = re.compile(
    rf"Accepted (?P<method>publickey|password|keyboard-interactive/pam) for (?P<user>\S+) "
    rf"from {_IP} port (?P<port>\d+) ssh2"
    rf"(?:: (?P<keytype>\S+) (?P<fingerprint>SHA256:\S+))?"
)

FAILED_RE = re.compile(
    rf"Failed (?P<method>publickey|password|keyboard-interactive/pam) for "
    rf"(?:(?P<invalid>invalid user) )?(?P<user>\S+) from {_IP} port (?P<port>\d+) ssh2"
    rf"(?:: (?P<keytype>\S+) (?P<fingerprint>SHA256:\S+))?"
)

INVALID_USER_RE = re.compile(rf"Invalid user (?P<user>\S+) from {_IP}(?: port (?P<port>\d+))?")

CLOSED_INVALID_RE = re.compile(
    rf"Connection closed by invalid user (?P<user>\S+) {_IP} port (?P<port>\d+)"
)

CLOSED_AUTH_RE = re.compile(
    rf"Connection closed by authenticating user (?P<user>\S+) {_IP} port (?P<port>\d+)"
)

DISCONNECTED_RE = re.compile(rf"Disconnected from user (?P<user>\S+) {_IP} port (?P<port>\d+)")

# Classic BSD syslog: "Mon DD HH:MM:SS host proc[pid]: message" (no year)
_SYSLOG_BSD_RE = re.compile(
    r"^(?P<mon>\w{3})\s+(?P<day>\d{1,2}) (?P<time>\d{2}:\d{2}:\d{2}) "
    r"(?P<host>\S+) (?P<proc>[^:]+): (?P<message>.*)$"
)
# RFC3339, used by newer rsyslog defaults: "2024-01-15T10:23:45.123456+00:00 host proc[pid]: message"
_SYSLOG_RFC3339_RE = re.compile(
    r"^(?P<iso>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[+-]\d{2}:\d{2}|Z)) "
    r"(?P<host>\S+) (?P<proc>[^:]+): (?P<message>.*)$"
)

_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


@dataclass(frozen=True)
class ParsedSshLine:
    event_type: str
    username: str | None
    source_ip: str | None
    source_port: int | None
    key_type: str | None
    key_fingerprint: str | None
    raw_message: str


def parse_sshd_message(message: str) -> ParsedSshLine | None:
    """Parse a bare sshd log message (no syslog timestamp prefix) into a structured event."""
    message = message.strip()

    m = ACCEPTED_RE.search(message)
    if m:
        event_type = "accepted_publickey" if m.group("method") == "publickey" else "accepted_other"
        return ParsedSshLine(
            event_type=event_type,
            username=m.group("user"),
            source_ip=m.group("ip"),
            source_port=int(m.group("port")),
            key_type=m.group("keytype"),
            key_fingerprint=m.group("fingerprint"),
            raw_message=message,
        )

    m = FAILED_RE.search(message)
    if m:
        event_type = "failed_publickey" if m.group("method") == "publickey" else "failed_other"
        return ParsedSshLine(
            event_type=event_type,
            username=m.group("user"),
            source_ip=m.group("ip"),
            source_port=int(m.group("port")),
            key_type=m.group("keytype"),
            key_fingerprint=m.group("fingerprint"),
            raw_message=message,
        )

    m = INVALID_USER_RE.search(message)
    if m:
        return ParsedSshLine(
            event_type="invalid_user",
            username=m.group("user"),
            source_ip=m.group("ip"),
            source_port=int(m.group("port")) if m.group("port") else None,
            key_type=None,
            key_fingerprint=None,
            raw_message=message,
        )

    for regex in (CLOSED_INVALID_RE, CLOSED_AUTH_RE):
        m = regex.search(message)
        if m:
            return ParsedSshLine(
                event_type="connection_closed",
                username=m.group("user"),
                source_ip=m.group("ip"),
                source_port=int(m.group("port")),
                key_type=None,
                key_fingerprint=None,
                raw_message=message,
            )

    m = DISCONNECTED_RE.search(message)
    if m:
        return ParsedSshLine(
            event_type="disconnected",
            username=m.group("user"),
            source_ip=m.group("ip"),
            source_port=int(m.group("port")),
            key_type=None,
            key_fingerprint=None,
            raw_message=message,
        )

    return None


def parse_syslog_line(line: str, *, now_year: int) -> tuple[float, str, str] | None:
    """Split a raw auth.log/secure line into (timestamp, proc, message).

    `now_year` is required because classic BSD syslog timestamps omit the
    year; callers should pass the current year and this function corrects
    for the (rare) case where that guess lands in the future (e.g. parsing
    a December 31 line just after midnight on January 1st).
    """
    import datetime as _dt

    m = _SYSLOG_RFC3339_RE.match(line)
    if m:
        iso = m.group("iso").replace("Z", "+00:00")
        try:
            dt = _dt.datetime.fromisoformat(iso)
        except ValueError:
            return None
        return dt.timestamp(), m.group("proc"), m.group("message")

    m = _SYSLOG_BSD_RE.match(line)
    if m:
        month = _MONTHS.get(m.group("mon"))
        if month is None:
            return None
        hh, mm, ss = (int(part) for part in m.group("time").split(":"))
        day = int(m.group("day"))
        dt = _dt.datetime(now_year, month, day, hh, mm, ss)
        now = _dt.datetime.now()
        if dt > now + _dt.timedelta(days=1):
            dt = dt.replace(year=now_year - 1)
        return dt.timestamp(), m.group("proc"), m.group("message")

    return None


def is_sshd_proc(proc_field: str) -> bool:
    """`proc` from parse_syslog_line looks like 'sshd[1234]' - match regardless of PID."""
    return proc_field.split("[", 1)[0] == "sshd"
