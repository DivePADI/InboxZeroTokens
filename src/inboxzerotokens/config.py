"""Load and validate InboxZeroTokens configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from inboxzerotokens.sanitize import email_address
from inboxzerotokens.secrets import SecretRef, parse_secret_ref
from inboxzerotokens.tomlutil import load_toml_path
from inboxzerotokens.util import expand_path

DEFAULT_CONFIG_PATHS = (
    Path("inboxzerotokens.toml"),
    Path("izt.toml"),
    expand_path("~/.config/inboxzerotokens/config.toml"),
)

SAMPLE_CONFIG = """# InboxZeroTokens config — passwords never go in this file.
# Secret refs: { env = "VAR" } | { file = "~/.config/inboxzerotokens/secrets/work" }
#              | { keyring_service = "inboxzerotokens", keyring_username = "user@host" }

[settings]
state_dir = "~/.local/state/inboxzerotokens"
log_file = "~/.local/state/inboxzerotokens/izt.log"
pace_seconds = 8.0
max_login_failures = 3
imap_timeout = 30
max_items_per_wake = 50
include_bodies = false
internal_domains = ["agents.example.com"]
skip_auto_replies = true
skip_mailing_lists = true
skip_bounces = true
skip_spam = true

[quiet_hours]
enabled = true
start = 22
end = 7
priority_from = ["owner@example.com"]

[filters]
allow_from = []
deny_from = []

[[accounts]]
name = "work"
host = "imap.example.com"
port = 993
user = "agent@example.com"
folder = "INBOX"
password = { env = "WORK_IMAP_PASSWORD" }
wake = ["primary"]

[[wakes]]
id = "primary"
type = "webhook"
url = "https://example.com/hooks/wake"
auth = "bearer"
token = { env = "WAKE_TOKEN" }
hmac_secret = { env = "WAKE_HMAC" }
hmac_header = "X-InboxZeroTokens-Signature"

# [[wakes]]
# id = "claude"
# type = "command"
# argv = ["claude", "-p", "New mail arrived. Read the JSON on stdin and decide."]

# [[wakes]]
# id = "drop"
# type = "file"
# path = "~/.local/share/inboxzerotokens/drops/new-mail.json"

# [[handoffs]]
# path = "~/handoff/status.md"
# debounce_seconds = 60
# wake = ["primary"]
"""


@dataclass
class QuietHours:
    enabled: bool = False
    start: int = 22
    end: int = 7
    priority_from: List[str] = field(default_factory=list)


@dataclass
class Filters:
    allow_from: List[str] = field(default_factory=list)
    deny_from: List[str] = field(default_factory=list)
    skip_auto_replies: bool = True
    skip_mailing_lists: bool = True
    skip_bounces: bool = True
    skip_spam: bool = True
    internal_domains: List[str] = field(default_factory=list)


@dataclass
class Account:
    name: str
    host: str
    port: int
    user: str
    folder: str
    password: SecretRef
    wake: List[str]
    timeout: int


@dataclass
class WakeTarget:
    id: str
    type: str
    url: Optional[str] = None
    url_env: Optional[str] = None
    auth: str = "bearer"
    header_name: str = "X-Automation-Key"
    token: Optional[SecretRef] = None
    hmac_secret: Optional[SecretRef] = None
    hmac_header: str = "X-InboxZeroTokens-Signature"
    github_dispatch: bool = False
    event_type: str = "inboxzerotokens"
    argv: List[str] = field(default_factory=list)
    path: Optional[str] = None
    timeout: int = 30
    extra_headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class Handoff:
    path: str
    debounce_seconds: int
    wake: List[str]


@dataclass
class AppConfig:
    path: Path
    state_dir: Path
    log_file: Path
    pace_seconds: float
    max_login_failures: int
    imap_timeout: int
    max_items_per_wake: int
    include_bodies: bool
    quiet: QuietHours
    filters: Filters
    accounts: List[Account]
    wakes: Dict[str, WakeTarget]
    handoffs: List[Handoff]


class ConfigError(ValueError):
    pass


def find_config(explicit: Optional[str] = None) -> Path:
    if explicit:
        path = expand_path(explicit)
        if not path.is_file():
            raise ConfigError(f"config not found: {path}")
        return path
    env = os.environ.get("IZT_CONFIG")
    if env:
        path = expand_path(env)
        if not path.is_file():
            raise ConfigError(f"IZT_CONFIG not found: {path}")
        return path
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.is_file():
            return candidate.resolve()
    raise ConfigError(
        "no config file found (looked for inboxzerotokens.toml, izt.toml, "
        "~/.config/inboxzerotokens/config.toml, or IZT_CONFIG)"
    )


def load_config(explicit: Optional[str] = None) -> AppConfig:
    path = find_config(explicit)
    try:
        raw = load_toml_path(str(path))
    except Exception as exc:
        raise ConfigError(f"cannot parse {path.name}: {exc}") from exc
    return parse_config(raw, path)


def parse_config(raw: Dict[str, Any], path: Path) -> AppConfig:
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a table")
    settings = raw.get("settings") or {}
    if not isinstance(settings, dict):
        raise ConfigError("[settings] must be a table")
    _forbid_plaintext(raw)

    quiet_raw = raw.get("quiet_hours") or settings.get("quiet_hours") or {}
    if not isinstance(quiet_raw, dict):
        raise ConfigError("[quiet_hours] must be a table")
    quiet = QuietHours(
        enabled=bool(quiet_raw.get("enabled", True if quiet_raw else False)),
        start=_hour(quiet_raw.get("start", 22), "quiet_hours.start"),
        end=_hour(quiet_raw.get("end", 7), "quiet_hours.end"),
        priority_from=_str_list(quiet_raw.get("priority_from"), "quiet_hours.priority_from"),
    )

    filt_raw = raw.get("filters") or {}
    if not isinstance(filt_raw, dict):
        raise ConfigError("[filters] must be a table")
    filters = Filters(
        allow_from=_norm_addrs(filt_raw.get("allow_from")),
        deny_from=_norm_addrs(filt_raw.get("deny_from")),
        skip_auto_replies=bool(settings.get("skip_auto_replies", True)),
        skip_mailing_lists=bool(settings.get("skip_mailing_lists", True)),
        skip_bounces=bool(settings.get("skip_bounces", True)),
        skip_spam=bool(settings.get("skip_spam", True)),
        internal_domains=[d.lower().lstrip("@") for d in _str_list(
            settings.get("internal_domains"), "settings.internal_domains"
        )],
    )

    wakes: Dict[str, WakeTarget] = {}
    for item in raw.get("wakes") or []:
        if not isinstance(item, dict):
            raise ConfigError("[[wakes]] entries must be tables")
        target = _parse_wake(item)
        if target.id in wakes:
            raise ConfigError(f"duplicate wake id: {target.id}")
        wakes[target.id] = target

    accounts: List[Account] = []
    for item in raw.get("accounts") or []:
        if not isinstance(item, dict):
            raise ConfigError("[[accounts]] entries must be tables")
        accounts.append(_parse_account(item, wakes, int(settings.get("imap_timeout", 30))))
    if not accounts:
        raise ConfigError("config must list at least one [[accounts]] entry")

    handoffs: List[Handoff] = []
    for item in raw.get("handoffs") or []:
        if not isinstance(item, dict):
            raise ConfigError("[[handoffs]] entries must be tables")
        hid_wakes = _str_list(item.get("wake"), "handoffs.wake")
        _require_wakes(hid_wakes, wakes)
        handoffs.append(
            Handoff(
                path=str(item.get("path") or ""),
                debounce_seconds=int(item.get("debounce_seconds", 60)),
                wake=hid_wakes,
            )
        )
        if not handoffs[-1].path:
            raise ConfigError("handoff path is required")

    state_dir = expand_path(str(settings.get("state_dir") or "~/.local/state/inboxzerotokens"))
    log_file = expand_path(str(settings.get("log_file") or str(state_dir / "izt.log")))
    return AppConfig(
        path=path,
        state_dir=state_dir,
        log_file=log_file,
        pace_seconds=float(settings.get("pace_seconds", 8.0)),
        max_login_failures=int(settings.get("max_login_failures", 3)),
        imap_timeout=int(settings.get("imap_timeout", 30)),
        max_items_per_wake=int(settings.get("max_items_per_wake", 50)),
        include_bodies=bool(settings.get("include_bodies", False)),
        quiet=quiet,
        filters=filters,
        accounts=accounts,
        wakes=wakes,
        handoffs=handoffs,
    )


def write_sample_config(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(SAMPLE_CONFIG, encoding="utf-8")


def _parse_account(item: Dict[str, Any], wakes: Dict[str, WakeTarget], timeout: int) -> Account:
    for banned in ("password_plain", "passwd"):
        if banned in item:
            raise ConfigError("plaintext passwords are not allowed in the config file")
    user = str(item.get("user") or "").strip()
    host = str(item.get("host") or "").strip()
    if not user or not host:
        raise ConfigError("each account needs host and user")
    name = str(item.get("name") or user)
    secret_raw = item.get("password")
    if secret_raw is None:
        # convenience aliases
        if item.get("password_env"):
            secret_raw = {"env": item["password_env"]}
        elif item.get("password_file"):
            secret_raw = {"file": item["password_file"]}
        elif item.get("password_keyring") is True:
            secret_raw = {
                "keyring_service": "inboxzerotokens",
                "keyring_username": f"{user}@{host}",
            }
    if isinstance(secret_raw, str) and not secret_raw.startswith(("env:", "file:", "keyring:")):
        raise ConfigError("plaintext passwords are not allowed in the config file")
    try:
        password = parse_secret_ref(secret_raw, default_keyring_user=f"{user}@{host}")
    except Exception as exc:
        raise ConfigError(str(exc)) from exc
    if not any([password.env, password.file, password.keyring_service]):
        raise ConfigError(f"account {name} is missing a password secret reference")
    wake_ids = _str_list(item.get("wake"), f"account {name}.wake")
    _require_wakes(wake_ids, wakes)
    port = int(item.get("port") or 993)
    if port < 1 or port > 65535:
        raise ConfigError(f"account {name} has an invalid port")
    return Account(
        name=name,
        host=host,
        port=port,
        user=user,
        folder=str(item.get("folder") or "INBOX"),
        password=password,
        wake=wake_ids,
        timeout=int(item.get("timeout") or timeout),
    )


def _parse_wake(item: Dict[str, Any]) -> WakeTarget:
    wid = str(item.get("id") or "").strip()
    wtype = str(item.get("type") or "").strip().lower()
    if not wid or wtype not in {"webhook", "command", "file"}:
        raise ConfigError("each wake needs id and type = webhook | command | file")
    token = None
    hmac = None
    if "token" in item or "token_env" in item:
        raw = item.get("token") or {"env": item.get("token_env")}
        token = parse_secret_ref(raw, default_keyring_user=wid)
    if "hmac_secret" in item or "hmac_secret_env" in item:
        raw = item.get("hmac_secret") or {"env": item.get("hmac_secret_env")}
        hmac = parse_secret_ref(raw, default_keyring_user=f"{wid}-hmac")
    argv = item.get("argv") or []
    if not isinstance(argv, list) or any(not isinstance(x, str) or not x for x in argv):
        if wtype == "command":
            raise ConfigError(f"wake {wid}: command argv must be a list of non-empty strings")
        argv = []
    extra = item.get("headers") or {}
    if extra and not isinstance(extra, dict):
        raise ConfigError(f"wake {wid}: headers must be a table")
    extra_headers = {str(k): str(v) for k, v in extra.items()} if extra else {}
    auth = str(item.get("auth") or "bearer").lower()
    if auth not in {"bearer", "header", "both", "none"}:
        raise ConfigError(f"wake {wid}: auth must be bearer, header, both, or none")
    target = WakeTarget(
        id=wid,
        type=wtype,
        url=item.get("url"),
        url_env=item.get("url_env"),
        auth=auth,
        header_name=str(item.get("header_name") or "X-Automation-Key"),
        token=token,
        hmac_secret=hmac,
        hmac_header=str(item.get("hmac_header") or "X-InboxZeroTokens-Signature"),
        github_dispatch=bool(item.get("github_dispatch", False)),
        event_type=str(item.get("event_type") or "inboxzerotokens"),
        argv=list(argv),
        path=item.get("path"),
        timeout=int(item.get("timeout") or 30),
        extra_headers=extra_headers,
    )
    if wtype == "webhook" and not (target.url or target.url_env):
        raise ConfigError(f"wake {wid}: webhook needs url or url_env")
    if wtype == "command" and not target.argv:
        raise ConfigError(f"wake {wid}: command needs argv")
    if wtype == "file" and not target.path:
        raise ConfigError(f"wake {wid}: file wake needs path")
    return target


def _require_wakes(ids: List[str], wakes: Dict[str, WakeTarget]) -> None:
    for wid in ids:
        if wid not in wakes:
            raise ConfigError(f"unknown wake id: {wid}")


def _forbid_plaintext(raw: Dict[str, Any]) -> None:
    blob = repr(raw).lower()
    # Structural check already happens per-account. This catches obvious mistakes.
    for item in raw.get("accounts") or []:
        if isinstance(item, dict) and isinstance(item.get("password"), str):
            if not item["password"].startswith(("env:", "file:", "keyring:")):
                raise ConfigError("plaintext passwords are not allowed in the config file")
    del blob


def _str_list(value: object, name: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(x, str) for x in value):
        return list(value)
    raise ConfigError(f"{name} must be a string or array of strings")


def _norm_addrs(value: object) -> List[str]:
    return [email_address(x) for x in _str_list(value, "address list") if email_address(x)]


def _hour(value: object, name: str) -> int:
    hour = int(value)  # type: ignore[arg-type]
    if hour < 0 or hour > 23:
        raise ConfigError(f"{name} must be 0-23")
    return hour
