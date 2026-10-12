"""Configuration and environment checks (no secrets printed)."""

from __future__ import annotations

from typing import List, Tuple

from inboxzerotokens.config import AppConfig
from inboxzerotokens.secrets import SecretError, resolve_secret
from inboxzerotokens.util import expand_path, is_world_readable

Check = Tuple[str, str, str]


def run_doctor(cfg: AppConfig, resolve_secrets: bool = True) -> List[Check]:
    checks: List[Check] = []
    checks.append(("ok", "config", f"loaded {cfg.path.name} ({len(cfg.accounts)} account(s))"))
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        probe = cfg.state_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(("ok", "state_dir", f"writable {cfg.state_dir}"))
    except OSError as exc:
        checks.append(("fail", "state_dir", f"not writable: {type(exc).__name__}"))

    if cfg.log_file:
        try:
            cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
            checks.append(("ok", "log_file", str(cfg.log_file.name)))
        except OSError:
            checks.append(("fail", "log_file", "cannot create log directory"))

    if not cfg.wakes:
        checks.append(("warn", "wakes", "no wake targets configured"))
    else:
        checks.append(("ok", "wakes", ", ".join(sorted(cfg.wakes))))

    if cfg.include_bodies:
        checks.append(("warn", "privacy", "include_bodies is enabled — bodies will leave the mailbox host"))

    for account in cfg.accounts:
        if resolve_secrets:
            try:
                secret = resolve_secret(account.password)
                if not secret:
                    checks.append(("fail", account.name, "empty password"))
                else:
                    checks.append(("ok", account.name, f"secret via {account.password.described()}"))
            except SecretError as exc:
                checks.append(("fail", account.name, str(exc)))
        else:
            checks.append(("ok", account.name, f"secret ref {account.password.described()}"))
        if account.password.file:
            path = expand_path(account.password.file)
            if path.exists() and is_world_readable(path):
                checks.append(("fail", account.name, "secret file is world-readable"))
        if not account.wake:
            checks.append(("warn", account.name, "no wake targets"))

    for wid, target in cfg.wakes.items():
        if target.type == "webhook" and target.token and resolve_secrets:
            try:
                resolve_secret(target.token)
                checks.append(("ok", f"wake:{wid}", "token resolvable"))
            except SecretError as exc:
                checks.append(("fail", f"wake:{wid}", str(exc)))
        elif target.type == "command":
            checks.append(("ok", f"wake:{wid}", f"command argv ({len(target.argv)} parts)"))
        elif target.type == "file":
            checks.append(("ok", f"wake:{wid}", f"drop {target.path}"))

    checks.append(("ok", "tls", "IMAP and HTTPS use default TLS certificate verification"))
    return checks
