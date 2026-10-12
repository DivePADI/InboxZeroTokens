"""Decide whether a message should wake an agent."""

from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from email.message import Message
from typing import Optional

from inboxzerotokens.config import Filters, QuietHours
from inboxzerotokens.sanitize import domain_of, email_address

AUTO_SUBMITTED_OK = {"", "no"}
PRECEDENCE_SKIP = {"bulk", "list", "junk"}
BOUNCE_LOCALS = {"mailer-daemon", "postmaster", "mailer", "noreply-bounce"}
SPAM_FLAGS = {"\\junk", "$junk", "junk", "\\spam", "$spam", "spam"}
SPAM_HEADERS = (
    ("x-spam-flag", {"yes", "true", "1"}),
    ("x-spam-status", None),  # special: startswith yes
)


def skip_reason(
    headers: Message,
    sender: str,
    flags: Iterable[str],
    filters: Filters,
    mailbox_user: str,
) -> Optional[str]:
    sender = sender.lower()
    mailbox_user = mailbox_user.lower()

    if sender and sender == mailbox_user:
        return "self-sender"

    if filters.allow_from and sender not in filters.allow_from:
        return "not-allowlisted"
    if sender and sender in filters.deny_from:
        return "deny-from"

    if sender and domain_of(sender) in filters.internal_domains:
        return "internal-sender"

    if filters.skip_auto_replies:
        auto = (headers.get("Auto-Submitted") or "").strip().lower()
        if auto and auto not in AUTO_SUBMITTED_OK:
            return "auto-submitted"
        if headers.get("X-Autoreply") or headers.get("X-Autoreply-From"):
            return "x-autoreply"
        prec = (headers.get("Precedence") or "").strip().lower()
        if prec in PRECEDENCE_SKIP:
            return "precedence"

    if filters.skip_mailing_lists:
        if headers.get("List-Id") or headers.get("List-Unsubscribe"):
            return "mailing-list"

    if filters.skip_bounces:
        if sender and sender.split("@", 1)[0] in BOUNCE_LOCALS:
            return "bounce-sender"
        ret = (headers.get("Return-Path") or "").strip()
        if ret in {"<>", ""}:
            if headers.get("Return-Path") is not None and ret == "<>":
                return "null-return-path"
        from_addr = email_address(headers.get("From"))
        if from_addr.split("@", 1)[0] in BOUNCE_LOCALS:
            return "bounce-sender"

    if filters.skip_spam:
        flagset = {f.lower() for f in flags}
        if flagset.intersection(SPAM_FLAGS):
            return "spam-flag"
        for name, values in SPAM_HEADERS:
            raw = (headers.get(name) or "").strip().lower()
            if not raw:
                continue
            if values is None:
                if raw.startswith("yes"):
                    return "spam-header"
            elif raw in values:
                return "spam-header"
    return None


def in_quiet_hours(quiet: QuietHours, now: Optional[time.struct_time] = None) -> bool:
    if not quiet.enabled:
        return False
    hour = (now or time.localtime()).tm_hour
    if quiet.start == quiet.end:
        return False
    if quiet.start < quiet.end:
        return quiet.start <= hour < quiet.end
    return hour >= quiet.start or hour < quiet.end


def is_priority(sender: str, priority_from: Sequence[str]) -> bool:
    return sender.lower() in {p.lower() for p in priority_from}
