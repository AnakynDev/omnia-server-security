import datetime

from omnia_daemon.ssh_log_parser import is_sshd_proc, parse_sshd_message, parse_syslog_line


def test_accepted_publickey():
    ev = parse_sshd_message(
        "Accepted publickey for miguel from 192.168.1.50 port 54321 ssh2: "
        "ED25519 SHA256:JmdMCo8OuKD4nhodbFXbenC3kSr9t9vKJrCyqFYYYpI"
    )
    assert ev.event_type == "accepted_publickey"
    assert ev.username == "miguel"
    assert ev.source_ip == "192.168.1.50"
    assert ev.source_port == 54321
    assert ev.key_type == "ED25519"
    assert ev.key_fingerprint == "SHA256:JmdMCo8OuKD4nhodbFXbenC3kSr9t9vKJrCyqFYYYpI"


def test_accepted_publickey_with_trailing_signature_algorithm():
    # newer OpenSSH versions append the signature algorithm in parentheses
    ev = parse_sshd_message(
        "Accepted publickey for root from 10.0.0.5 port 22 ssh2: "
        "RSA SHA256:PfABXAXHJaoX31b0GYD6RlnxZ7jf7C7HCgnRBh8TSBQ (RSA-SHA2-512)"
    )
    assert ev.event_type == "accepted_publickey"
    assert ev.key_fingerprint == "SHA256:PfABXAXHJaoX31b0GYD6RlnxZ7jf7C7HCgnRBh8TSBQ"


def test_failed_publickey_for_invalid_user():
    ev = parse_sshd_message(
        "Failed publickey for invalid user admin from 203.0.113.9 port 41000 ssh2: "
        "ED25519 SHA256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert ev.event_type == "failed_publickey"
    assert ev.username == "admin"
    assert ev.source_ip == "203.0.113.9"


def test_invalid_user():
    ev = parse_sshd_message("Invalid user postgres from 203.0.113.9 port 41001")
    assert ev.event_type == "invalid_user"
    assert ev.username == "postgres"
    assert ev.source_ip == "203.0.113.9"


def test_connection_closed_by_invalid_user():
    ev = parse_sshd_message("Connection closed by invalid user test 203.0.113.9 port 41002 [preauth]")
    assert ev.event_type == "connection_closed"
    assert ev.username == "test"


def test_connection_closed_by_authenticating_user():
    ev = parse_sshd_message("Connection closed by authenticating user miguel 192.168.1.50 port 54322 [preauth]")
    assert ev.event_type == "connection_closed"
    assert ev.username == "miguel"


def test_disconnected_from_user():
    ev = parse_sshd_message("Disconnected from user miguel 192.168.1.50 port 54321")
    assert ev.event_type == "disconnected"
    assert ev.username == "miguel"


def test_unrecognized_line_returns_none():
    assert parse_sshd_message("pam_unix(sshd:session): session opened for user miguel") is None


def test_parse_syslog_line_rfc3339():
    result = parse_syslog_line(
        "2024-01-15T10:23:45.123456+00:00 myhost sshd[1234]: Accepted publickey for miguel",
        now_year=2024,
    )
    assert result is not None
    ts, proc, message = result
    assert proc == "sshd[1234]"
    assert message == "Accepted publickey for miguel"
    assert datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).year == 2024


def test_parse_syslog_line_bsd_format():
    result = parse_syslog_line("Jan 15 10:23:45 myhost sshd[1234]: Invalid user test", now_year=2024)
    assert result is not None
    ts, proc, message = result
    assert proc == "sshd[1234]"
    assert message == "Invalid user test"


def test_parse_syslog_line_bsd_year_rollover():
    # A Dec 31 line parsed with "now_year" pointing at the *next* year (e.g. read
    # on Jan 1st just after midnight) must fall back to the previous year rather
    # than being placed in the future.
    now_year = datetime.datetime.now().year
    result = parse_syslog_line(f"Dec 31 23:59:00 myhost sshd[1]: test", now_year=now_year + 1)
    assert result is not None
    ts, _, _ = result
    parsed_year = datetime.datetime.fromtimestamp(ts).year
    assert parsed_year == now_year


def test_parse_syslog_line_unrecognized_format_returns_none():
    assert parse_syslog_line("not a syslog line at all", now_year=2024) is None


def test_is_sshd_proc():
    assert is_sshd_proc("sshd[1234]") is True
    assert is_sshd_proc("sshd") is True
    assert is_sshd_proc("systemd[1]") is False
    assert is_sshd_proc("CRON[999]") is False
