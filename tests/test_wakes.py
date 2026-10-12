from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest

from inboxzerotokens.config import WakeTarget
from inboxzerotokens.secrets import SecretRef
from inboxzerotokens.wake import WakeError, build_mail_payload, deliver, hmac_digest


def test_file_wake(tmp_path):
    dest = tmp_path / "out.json"
    target = WakeTarget(id="drop", type="file", path=str(dest))
    payload = build_mail_payload([{"account": "a", "uid": 1, "from": "x@y", "subject": "s", "date": "", "message_id": "<1>"}])
    deliver(target, payload)
    data = json.loads(dest.read_text(encoding="utf-8"))
    assert data["source"] == "inboxzerotokens"
    assert data["items"][0]["uid"] == 1
    if dest.stat().st_mode & 0o077:
        pytest.skip("POSIX permission bits not applied on this platform")


def test_command_wake_reads_stdin(tmp_path):
    out = tmp_path / "stdin.json"
    script = tmp_path / "reader.py"
    script.write_text(
        "import sys, pathlib\npathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())\n",
        encoding="utf-8",
    )
    target = WakeTarget(id="cmd", type="command", argv=[sys.executable, str(script), str(out)])
    payload = {"hello": "world", "from": "untrusted@example.com"}
    deliver(target, payload)
    assert json.loads(out.read_text(encoding="utf-8")) == payload


def test_command_wake_rejects_nonzero(tmp_path):
    target = WakeTarget(id="cmd", type="command", argv=[sys.executable, "-c", "raise SystemExit(3)"])
    with pytest.raises(WakeError, match="exit 3"):
        deliver(target, {"x": 1})


def test_webhook_hmac_and_bearer(tmp_path, monkeypatch):
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            seen["body"] = body
            seen["auth"] = self.headers.get("Authorization")
            seen["sig"] = self.headers.get("X-InboxZeroTokens-Signature")
            seen["ts"] = self.headers.get("X-InboxZeroTokens-Timestamp")
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("WAKE_TOKEN", "tok-123")
        monkeypatch.setenv("WAKE_HMAC", "hmac-secret")
        target = WakeTarget(
            id="hook",
            type="webhook",
            url=f"http://127.0.0.1:{server.server_address[1]}/wake",
            auth="bearer",
            token=SecretRef(env="WAKE_TOKEN"),
            hmac_secret=SecretRef(env="WAKE_HMAC"),
        )
        payload = {"kind": "new-mail", "count": 1}
        deliver(target, payload)
    finally:
        server.shutdown()
    assert seen["auth"] == "Bearer tok-123"
    expected = hmac_digest("hmac-secret", seen["body"], seen["ts"])
    assert seen["sig"] == f"sha256={expected}"
    assert json.loads(seen["body"])["kind"] == "new-mail"


def test_webhook_rejects_plain_http_remote():
    target = WakeTarget(id="hook", type="webhook", url="http://example.com/wake")
    with pytest.raises(WakeError, match="https"):
        deliver(target, {"ts": 1})


def test_webhook_redirect_is_failure_and_does_not_follow(monkeypatch):
    second_hits = {"n": 0, "auth": None}

    class Second(BaseHTTPRequestHandler):
        def do_POST(self):
            second_hits["n"] += 1
            second_hits["auth"] = self.headers.get("Authorization")
            self.send_response(204)
            self.end_headers()

        def do_GET(self):
            self.do_POST()

        def log_message(self, *_args):
            return

    second = HTTPServer(("127.0.0.1", 0), Second)
    Thread(target=second.serve_forever, daemon=True).start()
    dest = f"http://127.0.0.1:{second.server_address[1]}/other"

    class First(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            self.send_response(302)
            self.send_header("Location", dest)
            self.end_headers()

        def log_message(self, *_args):
            return

    first = HTTPServer(("127.0.0.1", 0), First)
    Thread(target=first.serve_forever, daemon=True).start()
    monkeypatch.setenv("WAKE_TOKEN", "tok-123")
    target = WakeTarget(
        id="hook",
        type="webhook",
        url=f"http://127.0.0.1:{first.server_address[1]}/wake",
        auth="bearer",
        token=SecretRef(env="WAKE_TOKEN"),
    )
    try:
        with pytest.raises(WakeError, match="status 30"):
            deliver(target, {"ts": 1, "kind": "new-mail"})
    finally:
        first.shutdown()
        second.shutdown()
    assert second_hits["n"] == 0
    assert second_hits["auth"] is None


def test_webhook_failure_raises():
    target = WakeTarget(id="hook", type="webhook", url="http://127.0.0.1:1/nope")
    with pytest.raises(WakeError):
        deliver(target, {"x": 1})
