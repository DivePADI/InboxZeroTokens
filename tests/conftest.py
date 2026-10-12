from __future__ import annotations

import pytest

from inboxzerotokens.config import parse_config
from inboxzerotokens.fakeimap import FakeIMAP, mailbox_from_items


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture
def secret_file(tmp_path):
    path = tmp_path / "secrets" / "imap"
    path.parent.mkdir(parents=True)
    path.write_text("test-password\n", encoding="utf-8")
    path.chmod(0o600)
    return path


@pytest.fixture
def sample_raw(tmp_path, secret_file):
    return {
        "settings": {
            "state_dir": str(tmp_path / "state"),
            "log_file": str(tmp_path / "state" / "izt.log"),
            "pace_seconds": 0,
            "max_login_failures": 3,
            "imap_timeout": 5,
            "max_items_per_wake": 50,
            "include_bodies": False,
            "internal_domains": ["agents.example.com"],
            "skip_auto_replies": True,
            "skip_mailing_lists": True,
            "skip_bounces": True,
            "skip_spam": True,
        },
        "quiet_hours": {
            "enabled": True,
            "start": 22,
            "end": 7,
            "priority_from": ["boss@example.com"],
        },
        "filters": {"allow_from": [], "deny_from": ["blocked@example.com"]},
        "accounts": [
            {
                "name": "work",
                "host": "imap.example.com",
                "port": 993,
                "user": "agent@example.com",
                "folder": "INBOX",
                "password": {"file": str(secret_file)},
                "wake": ["hook"],
            }
        ],
        "wakes": [
            {
                "id": "hook",
                "type": "file",
                "path": str(tmp_path / "drop.json"),
            }
        ],
        "handoffs": [
            {
                "path": str(tmp_path / "handoff.md"),
                "debounce_seconds": 0,
                "wake": ["hook"],
            }
        ],
    }


@pytest.fixture
def cfg(sample_raw, tmp_path):
    return parse_config(sample_raw, tmp_path / "inboxzerotokens.toml")


def make_client(messages, uidvalidity="1"):
    return FakeIMAP(mailbox_from_items(messages, uidvalidity=uidvalidity))


def msg(
    uid: int,
    sender: str = "human@example.net",
    subject: str = "Hello",
    **headers,
):
    return {
        "uid": uid,
        "from": sender,
        "subject": subject,
        "message_id": headers.pop("message_id", f"<{uid}@example.net>"),
        "date": "Mon, 12 Oct 2026 01:00:00 +0000",
        "headers": headers,
        "flags": headers.pop("flags", []) if False else headers.get("flags", []),
    }
