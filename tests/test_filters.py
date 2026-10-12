from __future__ import annotations

import time
from email.message import EmailMessage

from inboxzerotokens.config import Filters, QuietHours
from inboxzerotokens.filters import in_quiet_hours, is_priority, skip_reason


def _hdr(**fields) -> EmailMessage:
    msg = EmailMessage()
    for k, v in fields.items():
        msg[k] = v
    return msg


def test_skips_auto_submitted():
    h = _hdr(**{"Auto-Submitted": "auto-replied", "From": "bot@example.com"})
    assert skip_reason(h, "bot@example.com", [], Filters(), "agent@example.com") == "auto-submitted"


def test_skips_mailing_list():
    h = _hdr(**{"List-Id": "<news.example>", "From": "list@news.example"})
    assert skip_reason(h, "list@news.example", [], Filters(), "agent@example.com") == "mailing-list"


def test_skips_bounce_and_null_return_path():
    h = _hdr(From="MAILER-DAEMON@host.example", **{"Return-Path": "<>"})
    assert skip_reason(h, "mailer-daemon@host.example", [], Filters(), "agent@example.com") == "bounce-sender"
    h2 = _hdr(From="person@example.com", **{"Return-Path": "<>"})
    assert skip_reason(h2, "person@example.com", [], Filters(), "agent@example.com") == "null-return-path"


def test_skips_spam_flag_and_header():
    h = _hdr(From="spammer@example.com")
    assert skip_reason(h, "spammer@example.com", ["$Junk"], Filters(), "a@b") == "spam-flag"
    h2 = _hdr(From="spammer@example.com", **{"X-Spam-Flag": "YES"})
    assert skip_reason(h2, "spammer@example.com", [], Filters(), "a@b") == "spam-header"


def test_internal_domains_and_deny_allow():
    filt = Filters(internal_domains=["agents.example.com"], deny_from=["blocked@x.com"])
    h = _hdr(From="peer@agents.example.com")
    assert skip_reason(h, "peer@agents.example.com", [], filt, "agent@example.com") == "internal-sender"
    h2 = _hdr(From="blocked@x.com")
    assert skip_reason(h2, "blocked@x.com", [], filt, "agent@example.com") == "deny-from"
    filt2 = Filters(allow_from=["only@x.com"])
    assert skip_reason(h, "peer@agents.example.com", [], filt2, "a@b") == "not-allowlisted"


def test_quiet_hours_wrap_and_priority():
    q = QuietHours(enabled=True, start=22, end=7, priority_from=["boss@example.com"])
    night = time.struct_time((2026, 10, 12, 23, 0, 0, 0, 0, -1))
    day = time.struct_time((2026, 10, 12, 10, 0, 0, 0, 0, -1))
    assert in_quiet_hours(q, night)
    assert not in_quiet_hours(q, day)
    assert is_priority("Boss@example.com", q.priority_from)
    assert not is_priority("other@example.com", q.priority_from)
