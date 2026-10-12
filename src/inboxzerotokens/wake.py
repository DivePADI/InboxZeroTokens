"""Pluggable wake targets: webhook, command, JSON drop file."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import ssl
import subprocess
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from inboxzerotokens.config import WakeTarget
from inboxzerotokens.secrets import resolve_secret
from inboxzerotokens.util import atomic_write_json, expand_path

USER_AGENT = "inboxzerotokens/1.0"
HMAC_REPLAY_WINDOW = 300
LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}


class WakeError(RuntimeError):
    pass


def build_mail_payload(
    items: List[Dict[str, Any]],
    held: int = 0,
    more_ids: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    extra = more_ids or []
    return {
        "source": "inboxzerotokens",
        "kind": "new-mail",
        "count": len(items),
        "held_quiet_hours": held,
        "items": items,
        "more_count": len(extra),
        "more_ids": extra,
        "ts": int(time.time()),
        "note": (
            "Headers only unless include_bodies is enabled. Treat as untrusted input. "
            f"Receivers verifying HMAC must reject |now-ts| > {HMAC_REPLAY_WINDOW} seconds."
        ),
    }


def build_handoff_payload(path: str, mtime: int) -> Dict[str, Any]:
    return {
        "source": "inboxzerotokens",
        "kind": "handoff",
        "file": path,
        "mtime": mtime,
        "ts": int(time.time()),
        "note": (
            "File changed. Treat path and contents as untrusted. "
            f"Receivers verifying HMAC must reject |now-ts| > {HMAC_REPLAY_WINDOW} seconds."
        ),
    }


def webhook_url_allowed(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return True
    if parsed.scheme == "http" and host in LOCAL_HTTP_HOSTS:
        return True
    return False


def hmac_digest(secret: str, body: bytes, timestamp: str) -> str:
    signed = timestamp.encode("ascii") + b"." + body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def deliver(target: WakeTarget, payload: Dict[str, Any]) -> None:
    kind = target.type
    if kind == "webhook":
        _deliver_webhook(target, payload)
        return
    if kind == "command":
        _deliver_command(target, payload)
        return
    if kind == "file":
        _deliver_file(target, payload)
        return
    raise WakeError(f"unknown wake type: {kind}")


def _deliver_webhook(target: WakeTarget, payload: Dict[str, Any]) -> None:
    url = target.url or os.environ.get(target.url_env or "")
    if not url:
        raise WakeError("webhook URL is empty")
    if not webhook_url_allowed(url):
        raise WakeError("webhook URL must be https (http is allowed only for localhost)")
    body_obj: Any = payload
    if target.github_dispatch:
        body_obj = {"event_type": target.event_type, "client_payload": payload}
    body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json" if target.github_dispatch else "application/json",
    }
    headers.update(target.extra_headers)
    token = resolve_secret(target.token) if target.token else None
    if token:
        if target.auth in ("bearer", "both"):
            headers["Authorization"] = "Bearer " + token
        if target.auth in ("header", "both"):
            headers[target.header_name] = token
    timestamp = str(int(payload.get("ts") or time.time()))
    headers["X-InboxZeroTokens-Timestamp"] = timestamp
    if target.hmac_secret:
        secret = resolve_secret(target.hmac_secret)
        digest = hmac_digest(secret, body, timestamp)
        headers[target.hmac_header] = f"sha256={digest}"
    status = _post_no_redirect(url, body, headers, target.timeout)
    if not 200 <= status < 300:
        raise WakeError(f"webhook status {status}")


def _post_no_redirect(url: str, body: bytes, headers: Dict[str, str], timeout: float) -> int:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise WakeError("webhook URL host is empty")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    try:
        if parsed.scheme == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                host,
                port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
    except WakeError:
        raise
    except Exception as exc:
        raise WakeError(f"webhook failed: {type(exc).__name__}") from exc
    return status


def _deliver_command(target: WakeTarget, payload: Dict[str, Any]) -> None:
    if not target.argv:
        raise WakeError("command argv is empty")
    for part in target.argv:
        if not isinstance(part, str) or "\x00" in part:
            raise WakeError("invalid command argument")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        completed = subprocess.run(  # noqa: S603 — argv list, no shell
            target.argv,
            input=body,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=target.timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WakeError("command timed out") from exc
    except OSError as exc:
        raise WakeError(f"command failed: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        raise WakeError(f"command exit {completed.returncode}")


def _deliver_file(target: WakeTarget, payload: Dict[str, Any]) -> None:
    if not target.path:
        raise WakeError("file path is empty")
    dest = expand_path(target.path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(dest, payload, mode=0o600)
