from __future__ import annotations

from inboxzerotokens.fakeimap import FakeIMAP, mailbox_from_items
from inboxzerotokens.filters import Filters
from inboxzerotokens.imapscan import scan_mailbox
from inboxzerotokens.sanitize import clean_header, email_address
from inboxzerotokens.state import AppState, MailboxState


def test_header_sanitization_strips_controls():
    dirty = "Hello\x00\x1b[31m world\nNext"
    assert "\x00" not in clean_header(dirty)
    assert "\n" not in clean_header(dirty)
    assert email_address('Name <A.B@Example.COM>') == "a.b@example.com"


def test_uidvalidity_change_reseeds():
    from inboxzerotokens.config import Account
    from inboxzerotokens.secrets import SecretRef

    account = Account(
        name="work",
        host="h",
        port=993,
        user="agent@example.com",
        folder="INBOX",
        password=SecretRef(env="X"),
        wake=["hook"],
        timeout=5,
    )
    state = AppState()
    state.mailboxes["agent@example.com|INBOX"] = MailboxState(
        uidvalidity="old", wake_uid=99, seeded_at=1
    )
    client = FakeIMAP(mailbox_from_items([{"uid": 3, "from": "a@b.com"}], uidvalidity="new"))
    result = scan_mailbox(client, account, state, Filters())
    assert result.seeded
    assert result.items == []
    assert state.mailbox("agent@example.com|INBOX").wake_uid == 3
    assert state.mailbox("agent@example.com|INBOX").uidvalidity == "new"
