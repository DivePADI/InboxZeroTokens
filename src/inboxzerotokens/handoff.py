"""Debounced file-change watcher that reuses wake targets."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

from inboxzerotokens.config import AppConfig, Handoff
from inboxzerotokens.state import load_state, save_state
from inboxzerotokens.util import SafeLogger, exclusive_lock, expand_path
from inboxzerotokens.wake import WakeError, build_handoff_payload, deliver


def handoff_key(path: Path) -> str:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return digest


def check_handoff(
    cfg: AppConfig,
    spec: Handoff,
    *,
    dry_run: bool = False,
    echo: bool = False,
    now: Optional[float] = None,
) -> dict:
    path = expand_path(spec.path)
    log = SafeLogger(cfg.log_file, echo=echo)
    state_path = cfg.state_dir / "state.json"
    lock_path = cfg.state_dir / "izt.lock"
    stamp = now if now is not None else time.time()
    if not path.exists():
        log.log(f"handoff missing: {path.name}")
        return {"status": "missing"}
    mtime = int(path.stat().st_mtime)
    key = handoff_key(path)
    with exclusive_lock(lock_path):
        state = load_state(state_path)
        rec = state.handoffs.get(key) or {"posted_at": 0, "mtime": 0}
        last_post = int(rec.get("posted_at") or 0)
        last_mtime = int(rec.get("mtime") or 0)
        wait = spec.debounce_seconds - (stamp - last_post)
        if wait > 0:
            time.sleep(min(wait, spec.debounce_seconds))
            stamp = time.time()
            mtime = int(path.stat().st_mtime)
        if mtime == last_mtime:
            log.log(f"handoff unchanged mtime={mtime}")
            return {"status": "unchanged", "mtime": mtime}
        payload = build_handoff_payload(str(path), mtime)
        if dry_run:
            state.handoffs[key] = {"posted_at": int(stamp), "mtime": mtime}
            save_state(state_path, state)
            log.log(f"handoff dry-run mtime={mtime}")
            return {"status": "dry-run", "payload": payload}
        ok = True
        for wid in spec.wake:
            target = cfg.wakes.get(wid)
            if target is None:
                ok = False
                log.log(f"handoff wake {wid} missing")
                continue
            try:
                deliver(target, payload)
                log.log(f"handoff wake {wid} ok mtime={mtime}")
            except WakeError as exc:
                ok = False
                log.log(f"handoff wake {wid} failed: {exc}")
        if ok:
            state.handoffs[key] = {"posted_at": int(stamp), "mtime": mtime}
            save_state(state_path, state)
        return {"status": "ok" if ok else "failed", "payload": payload}


def watch_handoffs(cfg: AppConfig, interval: float = 5.0, dry_run: bool = False) -> None:
    while True:
        for spec in cfg.handoffs:
            try:
                check_handoff(cfg, spec, dry_run=dry_run)
            except OSError:
                pass
        time.sleep(max(1.0, interval))
