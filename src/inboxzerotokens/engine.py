"""One pass of mailbox scanning and batched wakes."""

from __future__ import annotations

import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from inboxzerotokens.config import Account, AppConfig
from inboxzerotokens.filters import in_quiet_hours, is_priority
from inboxzerotokens.imapscan import IMAPLike, MailItem, connect_account, scan_mailbox
from inboxzerotokens.state import AppState, MailboxState, load_state, save_state
from inboxzerotokens.util import SafeLogger, exclusive_lock
from inboxzerotokens.wake import WakeError, build_mail_payload, deliver

ConnectFn = Callable[[Account, int], IMAPLike]


@dataclass
class PassReport:
    seeded: List[str] = field(default_factory=list)
    scanned: int = 0
    pending_items: List[Dict[str, Any]] = field(default_factory=list)
    held: int = 0
    skipped: List[str] = field(default_factory=list)
    wakes: Dict[str, str] = field(default_factory=dict)
    login_errors: int = 0
    dry_run: bool = False
    payload: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "seeded": self.seeded,
            "scanned": self.scanned,
            "pending_items": self.pending_items,
            "held_quiet_hours": self.held,
            "skipped": self.skipped,
            "wakes": self.wakes,
            "login_errors": self.login_errors,
            "dry_run": self.dry_run,
            "payload": self.payload,
        }


def run_pass(
    cfg: AppConfig,
    *,
    dry_run: bool = False,
    include_internal: bool = False,
    connect: Optional[ConnectFn] = None,
    echo: bool = False,
    now: Optional[time.struct_time] = None,
    skip_lock: bool = False,
) -> PassReport:
    state_path = cfg.state_dir / "state.json"
    lock_path = cfg.state_dir / "izt.lock"
    log = SafeLogger(cfg.log_file, echo=echo)
    connect = connect or connect_account

    def _run() -> PassReport:
        state = load_state(state_path)
        report = _scan_all(cfg, state, dry_run, include_internal, connect, log, now)
        if not dry_run:
            state.pass_count += 1
            save_state(state_path, state)
        return report

    if skip_lock:
        return _run()
    try:
        with exclusive_lock(lock_path):
            return _run()
    except OSError:
        log.log("another pass holds the lock; exiting")
        return PassReport()


def _scan_all(
    cfg: AppConfig,
    state: AppState,
    dry_run: bool,
    include_internal: bool,
    connect: ConnectFn,
    log: SafeLogger,
    now: Optional[time.struct_time],
) -> PassReport:
    report = PassReport(dry_run=dry_run)
    pending_by_target: Dict[str, List[MailItem]] = defaultdict(list)
    held_by_box: Dict[str, List[MailItem]] = {}
    items_by_box: Dict[str, List[MailItem]] = {}
    login_errors = 0
    quiet = in_quiet_hours(cfg.quiet, now)

    for i, account in enumerate(cfg.accounts):
        if i and cfg.pace_seconds > 0:
            time.sleep(cfg.pace_seconds * random.uniform(0.6, 1.4))
        key = f"{account.user.lower()}|{account.folder}"
        mb = state.mailbox(key)
        for wid in account.wake:
            mb.targets.setdefault(wid, mb.wake_uid)
        try:
            client = connect(account, account.timeout)
        except Exception as exc:
            login_errors += 1
            report.login_errors = login_errors
            log.log(f"{account.name} LOGIN-ERROR {type(exc).__name__}")
            if login_errors >= cfg.max_login_failures:
                log.log(
                    f"{cfg.max_login_failures} login errors this pass; "
                    "stopping early to avoid provider blocks"
                )
                break
            continue
        try:
            result = scan_mailbox(
                client,
                account,
                state,
                cfg.filters,
                include_bodies=cfg.include_bodies,
                include_internal=include_internal,
            )
        except Exception as exc:
            log.log(f"{account.name} scan ERROR {type(exc).__name__}")
            continue
        finally:
            try:
                client.logout()
            except Exception:
                log.log(f"{account.name} logout failed")

        report.scanned += 1
        mb = state.mailbox(key)
        for wid in account.wake:
            mb.targets.setdefault(wid, mb.wake_uid)
        if result.seeded:
            for wid in account.wake:
                mb.targets[wid] = mb.wake_uid
            report.seeded.append(account.name)
            log.log(f"{account.name} {result.seed_note}")
            continue
        for uid, reason in result.skipped:
            report.skipped.append(f"{account.name}:{uid}:{reason}")
            log.log(f"{account.name} wake-skip uid={uid} reason={reason}")
        if result.items:
            items_by_box[key] = result.items
            offer, held_items = _split_quiet(result.items, cfg, quiet)
            if held_items:
                held_by_box[key] = held_items
            for item in offer:
                routed = False
                for wid in account.wake:
                    if item.uid > mb.targets.get(wid, mb.wake_uid) and not mb.target_has_seen(
                        wid, item.message_id
                    ):
                        pending_by_target[wid].append(item)
                        routed = True
                if routed:
                    report.pending_items.append(item.as_payload())
        elif result.filtered_only_uid is not None and not dry_run:
            mb.wake_uid = result.filtered_only_uid
            for wid in account.wake:
                mb.targets[wid] = max(mb.targets.get(wid, 0), result.filtered_only_uid)

    report.held = sum(len(v) for v in held_by_box.values())
    if report.held:
        log.log(f"quiet hours: holding {report.held} non-priority item(s)")

    payloads: Dict[str, Dict[str, Any]] = {}
    offered_by_target: Dict[str, List[MailItem]] = {}
    for wid, items in pending_by_target.items():
        if not items:
            continue
        unique = _dedupe_items(items)
        offered = unique[: cfg.max_items_per_wake]
        rest = unique[cfg.max_items_per_wake :]
        more_ids = [
            {"account": it.account, "uid": it.uid, "message_id": it.message_id} for it in rest
        ]
        offered_by_target[wid] = unique
        payloads[wid] = build_mail_payload(
            [it.as_payload() for it in offered],
            held=report.held,
            more_ids=more_ids,
        )

    report.payload = next(iter(payloads.values()), None) if payloads else None

    if dry_run:
        report.wakes = {wid: "dry-run" for wid in payloads}
        log.log(
            f"dry-run end would_wake_targets={len(payloads)} items={len(report.pending_items)}",
            {"targets": list(payloads), "items": len(report.pending_items)},
        )
        return report

    for wid, payload in payloads.items():
        target = cfg.wakes.get(wid)
        if target is None:
            report.wakes[wid] = "missing"
            continue
        try:
            deliver(target, payload)
            report.wakes[wid] = "ok"
            log.log(
                f"wake {wid} ok items={payload['count']}",
                {"target": wid, "items": payload["count"]},
            )
            _advance_target(
                cfg,
                state,
                wid,
                offered_by_target[wid],
                held_by_box,
            )
        except WakeError as exc:
            report.wakes[wid] = "failed"
            log.log(f"wake {wid} failed: {exc}", {"target": wid, "result": "failed"})
    return report


def _split_quiet(items: List[MailItem], cfg: AppConfig, quiet: bool):
    if not quiet:
        return items, []
    offer = [it for it in items if is_priority(it.sender, cfg.quiet.priority_from)]
    held = [it for it in items if not is_priority(it.sender, cfg.quiet.priority_from)]
    return offer, held


def _advance_target(
    cfg: AppConfig,
    state: AppState,
    wid: str,
    offered: List[MailItem],
    held_by_box: Dict[str, List[MailItem]],
) -> None:
    by_box: Dict[str, List[MailItem]] = defaultdict(list)
    for item in offered:
        by_box[_box_from_item(cfg, item)].append(item)
    for key, items in by_box.items():
        mb = state.mailbox(key)
        held_uids = [it.uid for it in held_by_box.get(key, [])]
        last_offered = max(it.uid for it in items)
        if held_uids:
            last_safe = min(held_uids) - 1
            new_uid = min(last_offered, last_safe)
        else:
            new_uid = last_offered
        mb.targets[wid] = max(mb.targets.get(wid, 0), new_uid)
        wake_ids = _wake_ids_for_box(cfg, key)
        for item in items:
            mb.add_target_seen(wid, item.message_id)
            if wake_ids and all(mb.targets.get(w, 0) >= item.uid for w in wake_ids):
                mb.add_seen(item.message_id)
        _sync_mailbox_floor(mb, wake_ids)


def _sync_mailbox_floor(mb: MailboxState, wake_ids: List[str]) -> None:
    floors = [mb.targets[w] for w in wake_ids if w in mb.targets]
    if floors:
        mb.wake_uid = min(floors)


def _wake_ids_for_box(cfg: AppConfig, key: str) -> List[str]:
    acct = _account_for_key(cfg, key)
    return list(acct.wake) if acct else []


def _account_for_key(cfg: AppConfig, key: str) -> Optional[Account]:
    for account in cfg.accounts:
        if f"{account.user.lower()}|{account.folder}" == key:
            return account
    return None


def _box_from_item(cfg: AppConfig, item: MailItem) -> str:
    for account in cfg.accounts:
        if account.name == item.account:
            return f"{account.user.lower()}|{account.folder}"
    return item.account


def _dedupe_items(items: List[MailItem]) -> List[MailItem]:
    seen: Set[tuple] = set()
    out = []
    for item in items:
        token = (item.account, item.uid, item.message_id)
        if token in seen:
            continue
        seen.add(token)
        out.append(item)
    return out


def watch_loop(cfg: AppConfig, interval: float, **kwargs: Any) -> None:
    while True:
        run_pass(cfg, **kwargs)
        time.sleep(max(1.0, interval))
