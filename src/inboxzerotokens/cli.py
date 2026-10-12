"""InboxZeroTokens command-line interface."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from inboxzerotokens import __version__
from inboxzerotokens.config import (
    AppConfig,
    ConfigError,
    find_config,
    load_config,
    parse_config,
    write_sample_config,
)
from inboxzerotokens.doctor import run_doctor
from inboxzerotokens.engine import run_pass, watch_loop
from inboxzerotokens.fakeimap import FakeIMAP, mailbox_from_items
from inboxzerotokens.handoff import check_handoff, watch_handoffs
from inboxzerotokens.secrets import SecretError, store_keyring
from inboxzerotokens.service import detect_platform, load_commands, render, write_service
from inboxzerotokens.state import AppState, save_state
from inboxzerotokens.util import expand_path
from inboxzerotokens.wake import WakeError, build_mail_payload, deliver


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    except SecretError as exc:
        print(f"secret error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="izt",
        description="Wake AI agents only when real IMAP mail arrives. Zero tokens on empty inboxes.",
    )
    p.add_argument("--version", action="version", version=f"inboxzerotokens {__version__}")
    sub = p.add_subparsers(dest="command")

    init = sub.add_parser("init", help="write a sample config file")
    init.add_argument("-o", "--output", default="inboxzerotokens.toml")
    init.set_defaults(handler=cmd_init)

    run = sub.add_parser("run", help="scan accounts once (or loop with --watch)")
    _add_common(run)
    run.add_argument("--once", action="store_true", help="single pass (default)")
    run.add_argument("--watch", action="store_true", help="loop using --interval")
    run.add_argument("--interval", type=float, default=300.0)
    run.add_argument("--include-internal", action="store_true")
    run.set_defaults(handler=cmd_run)

    watch = sub.add_parser("watch", help="loop forever, scanning on an interval")
    _add_common(watch)
    watch.add_argument("--interval", type=float, default=300.0)
    watch.set_defaults(handler=cmd_watch)

    dry = sub.add_parser("dry-run", help="scan without waking or writing wake state")
    _add_common(dry)
    dry.add_argument("--include-internal", action="store_true")
    dry.add_argument("--mock", action="store_true", help="use a built-in fake mailbox")
    dry.add_argument("--fixture", help="JSON list of fake messages (implies --mock)")
    dry.set_defaults(handler=cmd_dry_run)

    tw = sub.add_parser("test-wake", help="send a sample payload to one wake target")
    _add_common(tw)
    tw.add_argument("--target", help="wake id (default: first configured)")
    tw.set_defaults(handler=cmd_test_wake)

    doc = sub.add_parser("doctor", help="check config, secrets, and permissions")
    _add_common(doc)
    doc.add_argument("--no-secrets", action="store_true")
    doc.set_defaults(handler=cmd_doctor)

    inst = sub.add_parser("install-service", help="print or write a scheduler unit")
    _add_common(inst)
    inst.add_argument("--platform", choices=["macos", "linux", "windows", "auto"], default="auto")
    inst.add_argument("--interval", type=int, default=300, help="seconds between runs")
    inst.add_argument("--write", action="store_true", help="write files (default: print)")
    inst.set_defaults(handler=cmd_install_service)

    wf = sub.add_parser("watch-file", help="debounced handoff-file watcher")
    _add_common(wf)
    wf.add_argument("--interval", type=float, default=5.0)
    wf.add_argument("--once", action="store_true")
    wf.add_argument("--dry-run", action="store_true")
    wf.set_defaults(handler=cmd_watch_file)

    pw = sub.add_parser("set-password", help="store an IMAP password in the OS keychain")
    pw.add_argument("--service", default="inboxzerotokens")
    pw.add_argument("--username", required=True, help="usually user@imap-host")
    pw.add_argument("--password-env", help="read the password from this env var")
    pw.set_defaults(handler=cmd_set_password)

    return p


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("-c", "--config", help="path to inboxzerotokens.toml")
    p.add_argument("-v", "--verbose", action="store_true")


def _cfg(args: argparse.Namespace) -> AppConfig:
    return load_config(getattr(args, "config", None))


def cmd_init(args: argparse.Namespace) -> int:
    dest = expand_path(args.output)
    if dest.exists():
        print(f"already exists: {dest}", file=sys.stderr)
        return 1
    write_sample_config(dest)
    print(f"wrote {dest}")
    print("Next: set secret references, then run  izt doctor")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if args.watch:
        watch_loop(cfg, args.interval, echo=args.verbose)
        return 0
    report = run_pass(cfg, echo=args.verbose, include_internal=args.include_internal)
    print(json.dumps(report.as_dict(), indent=2))
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    watch_loop(cfg, args.interval, echo=args.verbose)
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    if args.fixture or args.mock:
        cfg = _mock_config(args)
        items = _load_fixture(args.fixture) if args.fixture else _default_fixture()
        box = mailbox_from_items(items)
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        seeded = AppState()
        mb = seeded.mailbox("agent@example.com|INBOX")
        mb.uidvalidity = box.uidvalidity
        mb.wake_uid = 0
        mb.seeded_at = 1
        save_state(cfg.state_dir / "state.json", seeded)

        def connect(_account, _timeout):
            return FakeIMAP(box)

        report = run_pass(
            cfg,
            dry_run=True,
            include_internal=args.include_internal,
            connect=connect,
            echo=args.verbose,
            skip_lock=True,
        )
        print(json.dumps(report.as_dict(), indent=2))
        return 0
    cfg = _cfg(args)
    report = run_pass(
        cfg,
        dry_run=True,
        include_internal=args.include_internal,
        echo=args.verbose,
    )
    print(json.dumps(report.as_dict(), indent=2))
    return 0


def cmd_test_wake(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if not cfg.wakes:
        print("no wake targets configured", file=sys.stderr)
        return 2
    wid = args.target or next(iter(cfg.wakes))
    target = cfg.wakes.get(wid)
    if target is None:
        print(f"unknown wake id: {wid}", file=sys.stderr)
        return 2
    payload = build_mail_payload(
        [
            {
                "account": "test",
                "uid": 1,
                "from": "sample@example.com",
                "subject": "InboxZeroTokens test-wake",
                "date": "",
                "message_id": "<izt-test@localhost>",
            }
        ]
    )
    try:
        deliver(target, payload)
    except WakeError as exc:
        print(f"wake failed: {exc}", file=sys.stderr)
        return 1
    print(f"wake {wid} ok")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    checks = run_doctor(cfg, resolve_secrets=not args.no_secrets)
    worst = 0
    for status, name, detail in checks:
        print(f"[{status}] {name}: {detail}")
        if status == "fail":
            worst = 1
    return worst


def cmd_install_service(args: argparse.Namespace) -> int:
    kind = detect_platform() if args.platform == "auto" else args.platform
    try:
        config = find_config(args.config)
    except ConfigError:
        config = expand_path("~/.config/inboxzerotokens/config.toml")
    text = render(kind, args.interval, config)
    if not args.write:
        print(text)
        return 0
    written = write_service(kind, args.interval, config)
    for path in written:
        print(f"wrote {path}")
    print(f"not loaded. load with: {load_commands(kind, written)}")
    return 0


def cmd_watch_file(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    if not cfg.handoffs:
        print("no [[handoffs]] configured", file=sys.stderr)
        return 2
    if args.once:
        out = [check_handoff(cfg, spec, dry_run=args.dry_run, echo=args.verbose) for spec in cfg.handoffs]
        print(json.dumps(out, indent=2))
        return 0
    watch_handoffs(cfg, interval=args.interval, dry_run=args.dry_run)
    return 0


def cmd_set_password(args: argparse.Namespace) -> int:
    if args.password_env:
        password = os.environ.get(args.password_env) or ""
        if not password:
            print(f"{args.password_env} is empty", file=sys.stderr)
            return 2
    else:
        password = getpass.getpass("IMAP password: ")
    store_keyring(args.service, args.username, password)
    print(f"stored keyring secret for {args.service}/{args.username}")
    return 0


def _mock_config(args: argparse.Namespace) -> AppConfig:
    raw = {
        "settings": {
            "state_dir": str(Path.cwd() / ".izt-mock-state"),
            "log_file": str(Path.cwd() / ".izt-mock-state" / "izt.log"),
            "pace_seconds": 0,
            "max_login_failures": 3,
            "include_bodies": False,
            "internal_domains": ["agents.example.com"],
        },
        "quiet_hours": {"enabled": False, "start": 22, "end": 7, "priority_from": []},
        "accounts": [
            {
                "name": "mock",
                "host": "127.0.0.1",
                "port": 993,
                "user": "agent@example.com",
                "folder": "INBOX",
                "password": {"env": "IZT_MOCK_PASSWORD"},
                "wake": ["drop"],
            }
        ],
        "wakes": [{"id": "drop", "type": "file", "path": str(Path.cwd() / ".izt-mock-state" / "drop.json")}],
    }
    os.environ.setdefault("IZT_MOCK_PASSWORD", "unused")
    return parse_config(raw, Path("<mock>"))


def _load_fixture(path: str) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "items" in data:
        data = data["items"]
    if not isinstance(data, list):
        raise ConfigError("fixture must be a JSON list of messages")
    return data


def _default_fixture() -> list:
    return [
        {
            "uid": 11,
            "from": "human@example.net",
            "subject": "Please review the brief",
            "date": "Mon, 12 Oct 2026 01:00:00 +0000",
            "message_id": "<brief-11@example.net>",
        },
        {
            "uid": 12,
            "from": "list@news.example",
            "subject": "weekly digest",
            "headers": {"List-Id": "<news.example>"},
        },
    ]
