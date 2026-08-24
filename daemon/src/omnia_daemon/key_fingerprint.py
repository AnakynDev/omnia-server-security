"""Parse ~/.ssh/authorized_keys and compute OpenSSH SHA256 key fingerprints.

The fingerprint sshd writes to the log (`SHA256:xxxx`) is computed directly
over the raw bytes of the key blob - the base64 field in the middle of an
authorized_keys line - not over a parsed key structure. That means no
`ssh-keygen` subprocess and no `cryptography` dependency are needed here,
just base64 + sha256 from the stdlib.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path

KNOWN_KEY_TYPES = {
    "ssh-rsa",
    "ssh-dss",
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}


@dataclass(frozen=True)
class KeyEntry:
    key_type: str
    fingerprint: str
    comment: str


def sha256_fingerprint(key_b64: str) -> str | None:
    """Compute the `SHA256:...` fingerprint for a base64 key blob, or None if malformed."""
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except (ValueError, base64.binascii.Error):
        return None
    digest = hashlib.sha256(raw).digest()
    return "SHA256:" + base64.b64encode(digest).rstrip(b"=").decode("ascii")


def parse_authorized_keys(path: str | Path) -> list[KeyEntry]:
    """Parse an authorized_keys file, tolerating leading option strings per line."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return []

    entries: list[KeyEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        key_type_idx = next((i for i, tok in enumerate(tokens) if tok in KNOWN_KEY_TYPES), None)
        if key_type_idx is None or key_type_idx + 1 >= len(tokens):
            continue
        key_type = tokens[key_type_idx]
        key_b64 = tokens[key_type_idx + 1]
        comment = " ".join(tokens[key_type_idx + 2 :])
        fingerprint = sha256_fingerprint(key_b64)
        if fingerprint is None:
            continue
        entries.append(KeyEntry(key_type=key_type, fingerprint=fingerprint, comment=comment))
    return entries


def _short_id(fingerprint: str) -> str:
    return fingerprint.removeprefix("SHA256:")[-12:]


class KeyStore:
    """Caches the parsed authorized_keys file, reloading only when its mtime changes."""

    def __init__(self, authorized_keys_path: str | Path):
        self.path = Path(authorized_keys_path).expanduser()
        self._mtime: float | None = None
        self._by_fingerprint: dict[str, KeyEntry] = {}

    def _reload_if_needed(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            self._by_fingerprint = {}
            self._mtime = None
            return
        if mtime == self._mtime:
            return
        self._by_fingerprint = {entry.fingerprint: entry for entry in parse_authorized_keys(self.path)}
        self._mtime = mtime

    def label_for_fingerprint(self, fingerprint: str) -> str:
        self._reload_if_needed()
        entry = self._by_fingerprint.get(fingerprint)
        if entry and entry.comment:
            return entry.comment
        return f"unlabeled-{_short_id(fingerprint)}" if entry else f"unknown-{_short_id(fingerprint)}"

    def all_keys(self) -> list[KeyEntry]:
        self._reload_if_needed()
        return list(self._by_fingerprint.values())
