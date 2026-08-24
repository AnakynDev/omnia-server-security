import pytest

from omnia_daemon.config import load_config


def test_load_config_with_no_file_uses_defaults(tmp_path):
    config = load_config(tmp_path / "does_not_exist.toml")
    assert config.metrics.poll_interval_seconds == 2.0
    assert config.alerts.failed_login_threshold == 5
    assert config.smtp.enabled is False


def test_load_config_overrides_only_specified_fields(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
        [alerts]
        failed_login_threshold = 10

        [smtp]
        enabled = true
        host = "smtp.example.com"
        to_addrs = ["me@example.com"]
        """,
        encoding="utf-8",
    )
    config = load_config(path)

    assert config.alerts.failed_login_threshold == 10
    assert config.alerts.failed_login_window_minutes == 10  # untouched default
    assert config.smtp.enabled is True
    assert config.smtp.host == "smtp.example.com"
    assert config.smtp.to_addrs == ["me@example.com"]
    assert config.speedtest.enabled is True  # untouched section, still defaults


def test_load_config_rejects_unknown_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
        [alerts]
        typo_field = 1
        """,
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_config(path)
