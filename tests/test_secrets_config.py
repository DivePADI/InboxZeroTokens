from __future__ import annotations

import os

import pytest

from inboxzerotokens.config import ConfigError, parse_config
from inboxzerotokens.secrets import SecretError, SecretRef, parse_secret_ref, resolve_secret
from inboxzerotokens.tomlutil import loads_subset


def test_refuses_plaintext_password(tmp_path):
    raw = {
        "accounts": [
            {
                "name": "x",
                "host": "h",
                "user": "u",
                "password": "super-secret",
                "wake": [],
            }
        ],
        "wakes": [],
    }
    with pytest.raises(ConfigError, match="plaintext"):
        parse_config(raw, tmp_path / "c.toml")


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are not enforced on Windows")
def test_refuses_world_readable_secret_file(tmp_path):
    path = tmp_path / "open.secret"
    path.write_text("pw\n", encoding="utf-8")
    path.chmod(0o644)
    with pytest.raises(SecretError, match="world-readable"):
        resolve_secret(SecretRef(file=str(path)))


def test_env_and_file_secret_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("IZT_PW", "from-env")
    assert resolve_secret(SecretRef(env="IZT_PW")) == "from-env"
    path = tmp_path / "closed.secret"
    path.write_text("from-file\n", encoding="utf-8")
    path.chmod(0o600)
    assert resolve_secret(SecretRef(file=str(path))) == "from-file"


def test_parse_secret_ref_prefixes():
    ref = parse_secret_ref("env:FOO", "user")
    assert ref.env == "FOO"
    ref = parse_secret_ref({"keyring_service": "inboxzerotokens"}, "user@host")
    assert ref.keyring_username == "user@host"


def test_toml_subset_roundtrip_shape():
    text = """
[settings]
pace_seconds = 8.0
internal_domains = ["agents.example.com"]
[quiet_hours]
enabled = true
start = 22
[[accounts]]
name = "work"
host = "imap.example.com"
password = { env = "WORK_IMAP_PASSWORD" }
wake = ["primary"]
[[wakes]]
id = "primary"
type = "webhook"
url = "https://example.com/hooks/wake"
"""
    data = loads_subset(text)
    assert data["settings"]["pace_seconds"] == 8.0
    assert data["accounts"][0]["password"]["env"] == "WORK_IMAP_PASSWORD"
    assert data["wakes"][0]["type"] == "webhook"
    assert data["quiet_hours"]["enabled"] is True
