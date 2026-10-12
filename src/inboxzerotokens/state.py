"""Per-mailbox UID / UIDVALIDITY / Message-ID state."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from inboxzerotokens.sanitize import clean_msgid
from inboxzerotokens.util import atomic_write_json, load_json

MAX_SEEN_IDS = 2000
SEEN_MAX_AGE = 30 * 24 * 60 * 60


@dataclass
class SeenId:
    msgid: str
    seen_at: int


@dataclass
class MailboxState:
    uidvalidity: str = ""
    wake_uid: int = 0
    seen_ids: List[SeenId] = field(default_factory=list)
    seeded_at: int = 0
    targets: Dict[str, int] = field(default_factory=dict)
    target_seen: Dict[str, List[SeenId]] = field(default_factory=dict)

    def scan_floor(self) -> int:
        floors = [self.wake_uid]
        floors.extend(self.targets.values())
        return min(floors) if floors else 0

    def has_seen(self, msgid: str) -> bool:
        token = clean_msgid(msgid)
        return bool(token) and any(s.msgid == token for s in self.seen_ids)

    def add_seen(self, msgid: str, now: Optional[int] = None) -> None:
        token = clean_msgid(msgid)
        if not token:
            return
        stamp = int(now if now is not None else time.time())
        self.seen_ids = [s for s in self.seen_ids if s.msgid != token]
        self.seen_ids.append(SeenId(token, stamp))
        self.prune_seen(stamp)

    def target_has_seen(self, wid: str, msgid: str) -> bool:
        token = clean_msgid(msgid)
        return bool(token) and any(s.msgid == token for s in self.target_seen.get(wid, []))

    def add_target_seen(self, wid: str, msgid: str, now: Optional[int] = None) -> None:
        token = clean_msgid(msgid)
        if not token:
            return
        stamp = int(now if now is not None else time.time())
        bucket = [s for s in self.target_seen.get(wid, []) if s.msgid != token]
        bucket.append(SeenId(token, stamp))
        cutoff = stamp - SEEN_MAX_AGE
        bucket = [s for s in bucket if s.seen_at >= cutoff][-MAX_SEEN_IDS:]
        self.target_seen[wid] = bucket

    def prune_seen(self, now: Optional[int] = None) -> None:
        stamp = int(now if now is not None else time.time())
        cutoff = stamp - SEEN_MAX_AGE
        kept = [s for s in self.seen_ids if s.seen_at >= cutoff]
        self.seen_ids = kept[-MAX_SEEN_IDS:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uidvalidity": self.uidvalidity,
            "wake_uid": self.wake_uid,
            "seen_ids": [{"id": s.msgid, "seen_at": s.seen_at} for s in self.seen_ids],
            "seeded_at": self.seeded_at,
            "targets": dict(self.targets),
            "target_seen": {
                wid: [{"id": s.msgid, "seen_at": s.seen_at} for s in ids]
                for wid, ids in self.target_seen.items()
            },
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> MailboxState:
        seen: List[SeenId] = []
        raw_ids = raw.get("seen_ids") or []
        now = int(time.time())
        for item in raw_ids:
            if isinstance(item, dict):
                seen.append(SeenId(clean_msgid(item.get("id")), int(item.get("seen_at") or now)))
            else:
                seen.append(SeenId(clean_msgid(item), now))
        targets = {str(k): int(v) for k, v in (raw.get("targets") or {}).items()}
        target_seen: Dict[str, List[SeenId]] = {}
        for wid, ids in (raw.get("target_seen") or {}).items():
            parsed = []
            for item in ids or []:
                if isinstance(item, dict):
                    parsed.append(SeenId(clean_msgid(item.get("id")), int(item.get("seen_at") or now)))
                else:
                    parsed.append(SeenId(clean_msgid(item), now))
            target_seen[str(wid)] = parsed
        box = cls(
            uidvalidity=str(raw.get("uidvalidity") or ""),
            wake_uid=int(raw.get("wake_uid") or 0),
            seen_ids=seen,
            seeded_at=int(raw.get("seeded_at") or 0),
            targets=targets,
            target_seen=target_seen,
        )
        box.prune_seen(now)
        return box


@dataclass
class AppState:
    version: int = 1
    pass_count: int = 0
    last_pass: int = 0
    mailboxes: Dict[str, MailboxState] = field(default_factory=dict)
    handoffs: Dict[str, Dict[str, int]] = field(default_factory=dict)

    def mailbox(self, key: str) -> MailboxState:
        if key not in self.mailboxes:
            self.mailboxes[key] = MailboxState()
        return self.mailboxes[key]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "pass": self.pass_count,
            "last_pass": self.last_pass,
            "mailboxes": {k: v.to_dict() for k, v in self.mailboxes.items()},
            "handoffs": self.handoffs,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> AppState:
        boxes = {
            k: MailboxState.from_dict(v)
            for k, v in (raw.get("mailboxes") or {}).items()
            if isinstance(v, dict)
        }
        return cls(
            version=int(raw.get("version") or 1),
            pass_count=int(raw.get("pass") or 0),
            last_pass=int(raw.get("last_pass") or 0),
            mailboxes=boxes,
            handoffs=dict(raw.get("handoffs") or {}),
        )


def load_state(path: Path) -> AppState:
    return AppState.from_dict(load_json(path, {"version": 1, "mailboxes": {}}))


def save_state(path: Path, state: AppState) -> None:
    state.last_pass = int(time.time())
    atomic_write_json(path, state.to_dict())
