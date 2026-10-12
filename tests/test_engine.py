from __future__ import annotations

import json
import time

from inboxzerotokens.engine import run_pass
from inboxzerotokens.fakeimap import FakeIMAP, mailbox_from_items
from inboxzerotokens.state import load_state

DAY = time.struct_time((2026, 10, 12, 10, 0, 0, 0, 0, -1))
NIGHT = time.struct_time((2026, 10, 12, 23, 15, 0, 0, 0, -1))


def _connect(items, uidvalidity="1"):
    box = mailbox_from_items(items, uidvalidity=uidvalidity)

    def connect(_account, _timeout):
        return FakeIMAP(box)

    return connect


def test_first_run_seeds_without_wake(cfg, tmp_path):
    items = [{"uid": 10, "from": "human@example.net", "subject": "Hi", "message_id": "<10@e>"}]
    report = run_pass(cfg, connect=_connect(items), skip_lock=True, echo=False, now=DAY)
    assert report.seeded == ["work"]
    assert report.pending_items == []
    assert not (tmp_path / "drop.json").exists()
    state = load_state(cfg.state_dir / "state.json")
    mb = state.mailbox("agent@example.com|INBOX")
    assert mb.wake_uid == 10
    assert mb.uidvalidity == "1"


def test_new_mail_wakes_and_advances_state(cfg, tmp_path):
    run_pass(
        cfg,
        connect=_connect([{"uid": 10, "from": "human@example.net", "subject": "seed"}]),
        skip_lock=True,
        now=DAY,
    )
    report = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 10, "from": "human@example.net", "subject": "seed"},
                {"uid": 11, "from": "human@example.net", "subject": "Real ask", "message_id": "<11@e>"},
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    assert len(report.pending_items) == 1
    assert report.pending_items[0]["subject"] == "Real ask"
    assert report.wakes["hook"] == "ok"
    payload = json.loads((tmp_path / "drop.json").read_text(encoding="utf-8"))
    assert payload["kind"] == "new-mail"
    assert payload["count"] == 1
    assert payload.get("more_count", 0) == 0
    assert "body" not in payload["items"][0]
    state = load_state(cfg.state_dir / "state.json")
    assert state.mailbox("agent@example.com|INBOX").wake_uid == 11
    assert state.mailbox("agent@example.com|INBOX").has_seen("11@e")


def test_filters_skip_and_advance_without_wake(cfg, tmp_path):
    run_pass(cfg, connect=_connect([{"uid": 1, "from": "a@b.com"}]), skip_lock=True, now=DAY)
    report = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {
                    "uid": 2,
                    "from": "list@news.example",
                    "subject": "digest",
                    "headers": {"List-Id": "<news.example>"},
                },
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    assert report.pending_items == []
    assert not (tmp_path / "drop.json").exists()
    assert load_state(cfg.state_dir / "state.json").mailbox("agent@example.com|INBOX").wake_uid == 2


def test_quiet_hours_hold_non_priority(cfg, tmp_path):
    run_pass(cfg, connect=_connect([{"uid": 1, "from": "a@b.com"}]), skip_lock=True, now=DAY)
    report = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 2, "from": "other@example.com", "subject": "later"},
            ]
        ),
        skip_lock=True,
        now=NIGHT,
    )
    assert report.held == 1
    assert report.pending_items == []
    assert not (tmp_path / "drop.json").exists()
    assert load_state(cfg.state_dir / "state.json").mailbox("agent@example.com|INBOX").wake_uid == 1


def test_quiet_hours_priority_goes_through(cfg, tmp_path):
    run_pass(cfg, connect=_connect([{"uid": 1, "from": "a@b.com"}]), skip_lock=True, now=DAY)
    report = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 2, "from": "boss@example.com", "subject": "urgent"},
            ]
        ),
        skip_lock=True,
        now=NIGHT,
    )
    assert report.held == 0
    assert report.wakes["hook"] == "ok"
    assert load_state(cfg.state_dir / "state.json").mailbox("agent@example.com|INBOX").wake_uid == 2


def test_batching_one_wake_per_target(cfg, tmp_path, sample_raw):
    from inboxzerotokens.config import parse_config

    sample_raw["accounts"].append(
        {
            "name": "other",
            "host": "imap.example.com",
            "user": "other@example.com",
            "password": sample_raw["accounts"][0]["password"],
            "wake": ["hook"],
        }
    )
    cfg2 = parse_config(sample_raw, tmp_path / "cfg.toml")
    extras = {
        "agent@example.com": {"uid": 2, "from": "a@b.com", "subject": "one"},
        "other@example.com": {"uid": 5, "from": "a@b.com", "subject": "two"},
    }
    seen = set()

    def connect(account, _timeout):
        base = [{"uid": 1, "from": "a@b.com", "subject": "seed"}]
        if account.user in seen:
            items = base + [extras[account.user]]
        else:
            seen.add(account.user)
            items = base
        return FakeIMAP(mailbox_from_items(items))

    run_pass(cfg2, connect=connect, skip_lock=True, now=DAY)
    report = run_pass(cfg2, connect=connect, skip_lock=True, now=DAY)
    assert len(report.pending_items) == 2
    payload = json.loads((tmp_path / "drop.json").read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert {i["account"] for i in payload["items"]} == {"work", "other"}


def test_state_does_not_advance_on_failed_wake(cfg, tmp_path, monkeypatch):
    from inboxzerotokens import engine
    from inboxzerotokens.wake import WakeError

    run_pass(cfg, connect=_connect([{"uid": 1, "from": "a@b.com"}]), skip_lock=True, now=DAY)

    def boom(_target, _payload):
        raise WakeError("nope")

    monkeypatch.setattr(engine, "deliver", boom)
    report = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 9, "from": "human@example.net", "subject": "retry me"},
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    assert report.wakes["hook"] == "failed"
    assert load_state(cfg.state_dir / "state.json").mailbox("agent@example.com|INBOX").wake_uid == 1
    assert not (tmp_path / "drop.json").exists()


def test_dedupe_by_message_id(cfg, tmp_path):
    run_pass(cfg, connect=_connect([{"uid": 1, "from": "a@b.com"}]), skip_lock=True, now=DAY)
    run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 2, "from": "human@example.net", "subject": "once", "message_id": "<same@e>"},
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    (tmp_path / "drop.json").unlink()
    report = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 2, "from": "human@example.net", "subject": "once", "message_id": "<same@e>"},
                {"uid": 3, "from": "human@example.net", "subject": "copy", "message_id": "<same@e>"},
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    assert report.pending_items == []
    assert any("duplicate-message-id" in s for s in report.skipped)


def test_quiet_hours_mixed_holds_non_priority_for_next_window(cfg, tmp_path):
    run_pass(cfg, connect=_connect([{"uid": 1, "from": "a@b.com"}]), skip_lock=True, now=DAY)
    report = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 2, "from": "other@example.com", "subject": "later"},
                {"uid": 3, "from": "boss@example.com", "subject": "urgent"},
            ]
        ),
        skip_lock=True,
        now=NIGHT,
    )
    assert report.held == 1
    assert [i["from"] for i in report.pending_items] == ["boss@example.com"]
    mb = load_state(cfg.state_dir / "state.json").mailbox("agent@example.com|INBOX")
    assert mb.targets.get("hook", mb.wake_uid) < 2
    day = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 2, "from": "other@example.com", "subject": "later"},
                {"uid": 3, "from": "boss@example.com", "subject": "urgent"},
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    assert [i["from"] for i in day.pending_items] == ["other@example.com"]
    assert day.wakes["hook"] == "ok"


def test_max_items_lists_more_ids_and_advances_past_them(cfg, tmp_path):
    cfg.max_items_per_wake = 1
    run_pass(cfg, connect=_connect([{"uid": 1, "from": "a@b.com"}]), skip_lock=True, now=DAY)
    report = run_pass(
        cfg,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 2, "from": "a@b.com", "subject": "one", "message_id": "<2@e>"},
                {"uid": 3, "from": "a@b.com", "subject": "two", "message_id": "<3@e>"},
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    payload = json.loads((tmp_path / "drop.json").read_text(encoding="utf-8"))
    assert payload["count"] == 1
    assert payload["more_count"] == 1
    assert payload["more_ids"][0]["uid"] == 3
    assert load_state(cfg.state_dir / "state.json").mailbox("agent@example.com|INBOX").wake_uid == 3
    assert report.wakes["hook"] == "ok"


def test_partial_multi_target_failure_skips_successful_target(cfg, tmp_path, sample_raw, monkeypatch):
    from inboxzerotokens import engine
    from inboxzerotokens.config import parse_config
    from inboxzerotokens.wake import WakeError, deliver

    sample_raw["wakes"].append({"id": "hook2", "type": "file", "path": str(tmp_path / "drop2.json")})
    sample_raw["accounts"][0]["wake"] = ["hook", "hook2"]
    cfg2 = parse_config(sample_raw, tmp_path / "cfg.toml")
    run_pass(cfg2, connect=_connect([{"uid": 1, "from": "a@b.com"}]), skip_lock=True, now=DAY)

    def selective(target, payload):
        if target.id == "hook2":
            raise WakeError("nope")
        return deliver(target, payload)

    monkeypatch.setattr(engine, "deliver", selective)
    report = run_pass(
        cfg2,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 4, "from": "human@example.net", "subject": "hi", "message_id": "<4@e>"},
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    assert report.wakes["hook"] == "ok"
    assert report.wakes["hook2"] == "failed"
    mb = load_state(cfg2.state_dir / "state.json").mailbox("agent@example.com|INBOX")
    assert mb.targets["hook"] == 4
    assert mb.targets["hook2"] == 1
    (tmp_path / "drop.json").unlink()
    again = run_pass(
        cfg2,
        connect=_connect(
            [
                {"uid": 1, "from": "a@b.com"},
                {"uid": 4, "from": "human@example.net", "subject": "hi", "message_id": "<4@e>"},
            ]
        ),
        skip_lock=True,
        now=DAY,
    )
    assert again.wakes.get("hook") != "ok"
    assert again.wakes["hook2"] == "failed"
    assert not (tmp_path / "drop.json").exists()
