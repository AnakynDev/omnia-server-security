"""Fingerprint values below are ground truth from a real `ssh-keygen -lf`
run, not re-derived from our own implementation - a change that breaks the
SHA256-over-raw-blob formula should fail these."""

from omnia_daemon.key_fingerprint import KeyStore, parse_authorized_keys, sha256_fingerprint

EXPECTED_ED25519_FINGERPRINT = "SHA256:JmdMCo8OuKD4nhodbFXbenC3kSr9t9vKJrCyqFYYYpI"
ED25519_KEY_B64 = "AAAAC3NzaC1lZDI1NTE5AAAAICBLjcmpG0l+TnJ11s7XxWvYZONi8VUwUuhEZ0r7Dhzh"

EXPECTED_RSA_FINGERPRINT = "SHA256:PfABXAXHJaoX31b0GYD6RlnxZ7jf7C7HCgnRBh8TSBQ"
RSA_KEY_B64 = (
    "AAAAB3NzaC1yc2EAAAADAQABAAABAQCqHoZ2a6nnueXJZK01KmGBK6vxvYc20BD/GedAuTThS1q8"
    "JSHlGiLIXKQT+NseRou7Y3D5pe0YrOy+ScDdRKOp4jRYXzizSpbathKnYI9Z8qvtcjdWG57KvQsx"
    "eim+mh23RZL1IyxWK+rV58fEMOgK6RoLMIvB4DeNe0gRM/T+6EirzhchYWOy2cm8FfEoM26g7MLy"
    "gMYQ0eaFvFsgXrF2C3xWh/Z3oibYPIBmrzbwg+ZwqD0y5y8MVUtXLn8/xNMkt/UhU2CZNb0bTFef"
    "RzRKODAGDDysLLZJPNOQq0uBun+08r/Kb7rcbY9EH5aJPF68fIbHqqz9xQ1TI0UeKJn7"
)


def test_sha256_fingerprint_ed25519_matches_ssh_keygen():
    assert sha256_fingerprint(ED25519_KEY_B64) == EXPECTED_ED25519_FINGERPRINT


def test_sha256_fingerprint_rsa_matches_ssh_keygen():
    assert sha256_fingerprint(RSA_KEY_B64) == EXPECTED_RSA_FINGERPRINT


def test_sha256_fingerprint_malformed_base64_returns_none():
    assert sha256_fingerprint("not-valid-base64!!!") is None


def test_parse_authorized_keys_tolerates_options_comments_and_blank_lines(tmp_path):
    content = "\n".join(
        [
            "# a comment line, should be skipped",
            "",
            f"ssh-ed25519 {ED25519_KEY_B64} alice@laptop",
            f'command="/usr/bin/rsync",no-port-forwarding ssh-rsa {RSA_KEY_B64}',
            "this is not a valid key line at all",
        ]
    )
    path = tmp_path / "authorized_keys"
    path.write_text(content, encoding="utf-8")

    entries = parse_authorized_keys(path)

    assert len(entries) == 2
    ed25519_entry = next(e for e in entries if e.key_type == "ssh-ed25519")
    assert ed25519_entry.fingerprint == EXPECTED_ED25519_FINGERPRINT
    assert ed25519_entry.comment == "alice@laptop"

    rsa_entry = next(e for e in entries if e.key_type == "ssh-rsa")
    assert rsa_entry.fingerprint == EXPECTED_RSA_FINGERPRINT
    assert rsa_entry.comment == ""  # no comment field after the key blob


def test_parse_authorized_keys_missing_file_returns_empty(tmp_path):
    assert parse_authorized_keys(tmp_path / "does_not_exist") == []


def test_keystore_labels_by_comment_or_falls_back_to_fingerprint_suffix(tmp_path):
    path = tmp_path / "authorized_keys"
    path.write_text(f"ssh-ed25519 {ED25519_KEY_B64} alice@laptop\n", encoding="utf-8")
    store = KeyStore(path)

    assert store.label_for_fingerprint(EXPECTED_ED25519_FINGERPRINT) == "alice@laptop"
    assert store.label_for_fingerprint("SHA256:" + "A" * 20 + "xyz123456789") == "unknown-xyz123456789"


def test_keystore_reloads_when_file_mtime_changes(tmp_path):
    path = tmp_path / "authorized_keys"
    path.write_text(f"ssh-ed25519 {ED25519_KEY_B64} old-label\n", encoding="utf-8")
    store = KeyStore(path)
    assert store.label_for_fingerprint(EXPECTED_ED25519_FINGERPRINT) == "old-label"

    import os
    import time

    time.sleep(0.01)
    path.write_text(f"ssh-ed25519 {ED25519_KEY_B64} new-label\n", encoding="utf-8")
    os.utime(path, None)  # force a distinct mtime on fast filesystems

    assert store.label_for_fingerprint(EXPECTED_ED25519_FINGERPRINT) == "new-label"
