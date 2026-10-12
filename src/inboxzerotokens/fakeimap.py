"""In-process IMAP stand-in for dry-run, doctor, and tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any, Dict, List, Tuple


@dataclass
class FakeMessage:
    uid: int
    headers: Dict[str, str]
    flags: List[str] = field(default_factory=list)
    body: str = ""


@dataclass
class FakeMailbox:
    uidvalidity: str = "1"
    messages: List[FakeMessage] = field(default_factory=list)


class FakeIMAP:
    def __init__(self, mailbox: FakeMailbox) -> None:
        self.mailbox = mailbox
        self.untagged_responses: Dict[str, List[bytes]] = {
            "UIDVALIDITY": [mailbox.uidvalidity.encode("ascii")]
        }
        self.closed = False

    def examine(self, mailbox: str) -> Tuple[str, List[Any]]:
        if self.closed:
            return "NO", [b"closed"]
        return "OK", [b"READ-ONLY"]

    def uid(self, command: str, *args: str) -> Tuple[str, List[Any]]:
        cmd = command.upper()
        if cmd == "SEARCH":
            blob = b" ".join(str(m.uid).encode("ascii") for m in self.mailbox.messages)
            return "OK", [blob]
        if cmd == "FETCH":
            uid_spec, _parts = args[0], args[1] if len(args) > 1 else ""
            want = {int(x) for x in uid_spec.split(",") if x}
            data: List[Any] = []
            for msg in self.mailbox.messages:
                if msg.uid not in want:
                    continue
                header = _render_headers(msg.headers)
                flags = " ".join(msg.flags)
                meta = f"UID {msg.uid} FLAGS ({flags}) BODY[HEADER]".encode("ascii")
                data.append((meta, header))
                if msg.body:
                    data.append((b"BODY[TEXT]", msg.body.encode("utf-8")))
            return "OK", data
        return "NO", [b"unknown"]

    def logout(self) -> Tuple[str, List[Any]]:
        self.closed = True
        return "BYE", [b""]


def mailbox_from_items(items: List[Dict[str, Any]], uidvalidity: str = "1") -> FakeMailbox:
    messages = []
    for i, item in enumerate(items, start=1):
        uid = int(item.get("uid") or i)
        headers = {
            "From": item.get("from") or item.get("From") or "someone@example.com",
            "Subject": item.get("subject") or item.get("Subject") or "",
            "Date": item.get("date") or item.get("Date") or "",
            "Message-ID": item.get("message_id") or item.get("Message-ID") or f"<{uid}@example.com>",
        }
        extra = item.get("headers") or {}
        headers.update(extra)
        messages.append(
            FakeMessage(
                uid=uid,
                headers=headers,
                flags=list(item.get("flags") or []),
                body=str(item.get("body") or ""),
            )
        )
    return FakeMailbox(uidvalidity=uidvalidity, messages=messages)


def _render_headers(headers: Dict[str, str]) -> bytes:
    msg = EmailMessage()
    for key, value in headers.items():
        msg[key] = value
    return bytes(msg)
