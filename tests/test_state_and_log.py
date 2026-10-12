from __future__ import annotations

import pytest

from inboxzerotokens.state import MailboxState, SeenId
from inboxzerotokens.util import SafeLogger


def test_seen_ids_prune_by_age_and_insertion_order():
    now = 1_700_000_000
    mb = MailboxState(
        seen_ids=[
            SeenId("old@e", now - 40 * 24 * 60 * 60),
            SeenId("mid@e", now - 10),
        ]
    )
    mb.add_seen("new@e", now)
    mb.prune_seen(now)
    ids = [s.msgid for s in mb.seen_ids]
    assert "old@e" not in ids
    assert ids[-1] == "new@e"
    assert "mid@e" in ids


def test_logger_refuses_secret_fields(tmp_path):
    log = SafeLogger(tmp_path / "izt.log")
    log.log("ok", {"target": "hook", "items": 1})
    with pytest.raises(ValueError, match="secret"):
        log.log("nope", {"authorization": "Bearer x"})
    with pytest.raises(ValueError, match="secret"):
        log.refuse_secrets({"imap_password": "x"})
    text = (tmp_path / "izt.log").read_text(encoding="utf-8")
    assert "Bearer" not in text
