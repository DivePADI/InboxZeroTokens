"""Read-only IMAP new-mail detection (EXAMINE + BODY.PEEK)."""

from __future__ import annotations

import email
import email.policy
import imaplib
import re
import ssl
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from email.message import Message
from typing import Any, Dict, List, Optional, Protocol, Tuple

from inboxzerotokens.config import Account, Filters
from inboxzerotokens.filters import skip_reason
from inboxzerotokens.sanitize import clean_header, clean_msgid, email_address
from inboxzerotokens.secrets import resolve_secret
from inboxzerotokens.state import AppState, MailboxState

UID_RE = re.compile(rb"UID\s+(\d+)", re.I)
FLAGS_RE = re.compile(rb"FLAGS\s+\(([^)]*)\)", re.I)


class IMAPLike(Protocol):
    untagged_responses: Dict[str, List[bytes]]

    def examine(self, mailbox: str) -> Tuple[str, List[Any]]: ...
    def uid(self, command: str, *args: str) -> Tuple[str, List[Any]]: ...
    def logout(self) -> Tuple[str, List[Any]]: ...


@dataclass
class MailItem:
    account: str
    uid: int
    sender: str
    subject: str
    date: str
    message_id: str
    body: Optional[str] = None

    def as_payload(self) -> Dict[str, Any]:
        data = {
            "account": self.account,
            "uid": self.uid,
            "from": self.sender,
            "subject": self.subject,
            "date": self.date,
            "message_id": self.message_id,
        }
        if self.body is not None:
            data["body"] = self.body
        return data


@dataclass
class ScanResult:
    seeded: bool = False
    seed_note: str = ""
    items: List[MailItem] = field(default_factory=list)
    skipped: List[Tuple[int, str]] = field(default_factory=list)
    advance_uid: Optional[int] = None
    filtered_only_uid: Optional[int] = None


class IMAPError(RuntimeError):
    pass


def connect_account(account: Account, timeout: int) -> IMAPLike:
    password = resolve_secret(account.password)
    ctx = ssl.create_default_context()
    client = imaplib.IMAP4_SSL(account.host, account.port, ssl_context=ctx, timeout=timeout)
    try:
        typ, _ = client.login(account.user, password)
        if typ != "OK":
            raise IMAPError("login rejected")
    except Exception:
        try:
            client.shutdown()
        except Exception:  # noqa: S110 — best-effort close after login failure
            pass
        raise
    return client


def quote_mailbox(name: str) -> str:
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def scan_mailbox(
    client: IMAPLike,
    account: Account,
    state: AppState,
    filters: Filters,
    include_bodies: bool = False,
    include_internal: bool = False,
    max_fetch: int = 200,
) -> ScanResult:
    typ, _ = client.examine(quote_mailbox(account.folder))
    if typ != "OK":
        raise IMAPError(f"cannot EXAMINE {account.folder}")
    uv = _uidvalidity(client)
    typ, data = client.uid("SEARCH", "ALL")
    if typ != "OK":
        raise IMAPError("UID SEARCH failed")
    uids = [int(x) for x in (data[0] or b"").split() if x]
    top = max(uids) if uids else 0
    key = _box_key(account)
    mb = state.mailbox(key)
    if not mb.uidvalidity or mb.uidvalidity != uv:
        mb.uidvalidity = uv
        mb.wake_uid = top
        mb.seeded_at = int(time.time())
        for wid in list(mb.targets):
            mb.targets[wid] = top
        return ScanResult(seeded=True, seed_note=f"seed wake_uid={top} uidvalidity={uv}")

    new_uids = sorted(u for u in uids if u > mb.scan_floor())[:max_fetch]
    if not new_uids:
        return ScanResult()

    fetched = _fetch_headers(client, new_uids, include_bodies)
    items: List[MailItem] = []
    skipped: List[Tuple[int, str]] = []
    for uid in new_uids:
        rec = fetched.get(uid)
        if rec is None:
            skipped.append((uid, "missing-headers"))
            continue
        headers, flags, body = rec
        sender = email_address(headers.get("From"))
        msgid = clean_msgid(headers.get("Message-ID"))
        if msgid and mb.has_seen(msgid):
            skipped.append((uid, "duplicate-message-id"))
            continue
        reason = skip_reason(headers, sender, flags, filters, account.user)
        if reason == "internal-sender" and include_internal:
            reason = None
        if reason:
            skipped.append((uid, reason))
            continue
        items.append(
            MailItem(
                account=account.name,
                uid=uid,
                sender=sender,
                subject=clean_header(headers.get("Subject"), 200),
                date=clean_header(headers.get("Date"), 64),
                message_id=clean_header(headers.get("Message-ID"), 200),
                body=body,
            )
        )
    result = ScanResult(items=items, skipped=skipped)
    if items:
        result.advance_uid = new_uids[-1]
    elif skipped:
        result.filtered_only_uid = new_uids[-1]
    return result


def mark_seen(mb: MailboxState, items: Sequence[MailItem], uid: int) -> None:
    mb.wake_uid = max(mb.wake_uid, uid)
    for item in items:
        mb.add_seen(item.message_id)


def _box_key(account: Account) -> str:
    return f"{account.user.lower()}|{account.folder}"


def _uidvalidity(client: IMAPLike) -> str:
    raw = client.untagged_responses.get("UIDVALIDITY", [b"0"])[-1]
    if isinstance(raw, bytes):
        return raw.decode("ascii", "replace")
    return str(raw)


def _fetch_headers(
    client: IMAPLike, uids: Sequence[int], include_bodies: bool
) -> Dict[int, Tuple[Message, List[str], Optional[str]]]:
    spec = "(UID FLAGS BODY.PEEK[HEADER])"
    if include_bodies:
        spec = "(UID FLAGS BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.4096>)"
    typ, data = client.uid("FETCH", ",".join(str(u) for u in uids), spec)
    if typ != "OK" or not data:
        return {}
    out: Dict[int, Tuple[Message, List[str], Optional[str]]] = {}
    i = 0
    while i < len(data):
        item = data[i]
        if not isinstance(item, tuple) or len(item) < 2:
            i += 1
            continue
        meta = item[0] if isinstance(item[0], bytes) else b""
        match = UID_RE.search(meta)
        if not match:
            i += 1
            continue
        uid = int(match.group(1))
        flags = []
        fm = FLAGS_RE.search(meta)
        if fm:
            flags = [p.decode("ascii", "replace") for p in fm.group(1).split() if p]
        header_bytes = item[1] if isinstance(item[1], (bytes, bytearray)) else b""
        headers = email.message_from_bytes(bytes(header_bytes), policy=email.policy.compat32)
        body = None
        if include_bodies:
            body = _maybe_body(data, i + 1)
        out[uid] = (headers, flags, body)
        i += 1
    return out


def _maybe_body(data: List[Any], index: int) -> Optional[str]:
    if index >= len(data):
        return None
    item = data[index]
    if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
        text = bytes(item[1]).decode("utf-8", "replace")
        return clean_header(text, 4000)
    return None
