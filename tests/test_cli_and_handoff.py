from __future__ import annotations

import json
import os
import re
from pathlib import Path

from inboxzerotokens.cli import main
from inboxzerotokens.handoff import check_handoff
from inboxzerotokens.service import launchd_plist, render, windows_task_xml


def test_dry_run_mock(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["dry-run", "--mock"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True
    assert out["pending_items"]
    subjects = [i["subject"] for i in out["pending_items"]]
    assert "Please review the brief" in subjects
    assert out["wakes"]["drop"] == "dry-run"


def test_doctor_and_init(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = main(["init", "-o", str(tmp_path / "inboxzerotokens.toml")])
    assert rc == 0
    text = (tmp_path / "inboxzerotokens.toml").read_text(encoding="utf-8")
    assert "password" in text
    assert "super-secret" not in text
    live = tmp_path / "cfg.toml"
    state_dir = (tmp_path / "state").as_posix()
    drop_path = (tmp_path / "drop.json").as_posix()
    live.write_text(
        f"""
[settings]
state_dir = "{state_dir}"
pace_seconds = 0
internal_domains = ["agents.example.com"]
[[accounts]]
name = "work"
host = "imap.example.com"
user = "agent@example.com"
password = {{ env = "WORK_IMAP_PASSWORD" }}
wake = ["drop"]
[[wakes]]
id = "drop"
type = "file"
path = "{drop_path}"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORK_IMAP_PASSWORD", "pw")
    rc = main(["doctor", "-c", str(live)])
    assert rc == 0
    assert "[ok] config:" in capsys.readouterr().out


def test_install_service_print():
    text = render("linux", 300, Path("/tmp/cfg.toml"))
    assert "OnUnitActiveSec" in text
    assert "inboxzerotokens.service" in text
    assert "-m" in text and "inboxzerotokens" in text
    assert "run --once" in text
    plist = launchd_plist(120, Path("/tmp/cfg.toml"))
    assert "StartInterval" in plist
    assert "-m" in plist
    xml = windows_task_xml(300, Path("/tmp/cfg.toml"))
    assert "InboxZeroTokens" in xml
    assert "StartBoundary" in xml
    assert "UTF-16" in xml


def test_windows_start_boundary_is_explicit_utc():
    xml = windows_task_xml(300, Path("/tmp/cfg.toml"))
    match = re.search(r"<StartBoundary>([^<]+)</StartBoundary>", xml)
    assert match is not None
    value = match.group(1)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value)


def test_handoff_debounce_and_wake(cfg, tmp_path):
    path = tmp_path / "handoff.md"
    path.write_text("v1", encoding="utf-8")
    spec = cfg.handoffs[0]
    first = check_handoff(cfg, spec, now=1_000_000)
    assert first["status"] == "ok"
    drop = json.loads((tmp_path / "drop.json").read_text(encoding="utf-8"))
    assert drop["kind"] == "handoff"
    second = check_handoff(cfg, spec, now=1_000_010)
    assert second["status"] == "unchanged"
    os_utime = path.stat()
    path.write_text("v2", encoding="utf-8")
    os.utime(path, (os_utime.st_atime, os_utime.st_mtime + 10))
    third = check_handoff(cfg, spec, now=1_000_100)
    assert third["status"] == "ok"
