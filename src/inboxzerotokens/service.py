"""Generate launchd, systemd --user, and Windows Task Scheduler units."""

from __future__ import annotations

import os
import platform
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from inboxzerotokens.util import expand_path

LAUNCHD_LABEL = "com.inboxzerotokens.watch"
SYSTEMD_NAME = "inboxzerotokens"


def detect_platform() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "windows":
        return "windows"
    return "linux"


def runner_argv() -> List[str]:
    return [os.path.abspath(sys.executable), "-m", "inboxzerotokens"]


def launchd_plist(interval: int, config: Path) -> str:
    argv = runner_argv()
    args = "".join(
        f"    <string>{_xml(part)}</string>\n"
        for part in [*argv, "run", "--once", "--config", str(config)]
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
{args}  </array>
  <key>StartInterval</key>
  <integer>{int(interval)}</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{_xml(str(expand_path("~/Library/Logs/inboxzerotokens.log")))}</string>
  <key>StandardErrorPath</key>
  <string>{_xml(str(expand_path("~/Library/Logs/inboxzerotokens.err.log")))}</string>
</dict>
</plist>
"""


def systemd_unit(interval: int, config: Path) -> Tuple[str, str]:
    argv = runner_argv()
    cmd = " ".join(shlex.quote(p) for p in [*argv, "run", "--once", "--config", str(config)])
    service = f"""[Unit]
Description=InboxZeroTokens mailbox watcher
After=network-online.target

[Service]
Type=oneshot
ExecStart={cmd}
"""
    timer = f"""[Unit]
Description=Run InboxZeroTokens on an interval

[Timer]
OnBootSec=1min
OnUnitActiveSec={int(interval)}s
AccuracySec=30s
Persistent=true
Unit={SYSTEMD_NAME}.service

[Install]
WantedBy=timers.target
"""
    return service, timer


def windows_task_xml(interval: int, config: Path) -> str:
    argv = runner_argv()
    minutes = max(1, int(interval) // 60)
    start = datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    command, *rest = argv
    args = " ".join([*rest, "run", "--once", "--config", str(config)])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>InboxZeroTokens mailbox watcher</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Repetition>
        <Interval>PT{minutes}M</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowHardTerminate>true</AllowHardTerminate>
    <AllowStartOnDemand>true</AllowStartOnDemand>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_xml(command)}</Command>
      <Arguments>{_xml(args)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def default_dest(kind: str) -> Path:
    if kind == "macos":
        return expand_path(f"~/Library/LaunchAgents/{LAUNCHD_LABEL}.plist")
    if kind == "windows":
        return expand_path("~/inboxzerotokens-task.xml")
    return expand_path(f"~/.config/systemd/user/{SYSTEMD_NAME}.service")


def render(kind: str, interval: int, config: Path) -> str:
    if kind == "macos":
        return launchd_plist(interval, config)
    if kind == "windows":
        return windows_task_xml(interval, config)
    service, timer = systemd_unit(interval, config)
    return service + "\n--- timer ---\n" + timer


def write_service(kind: str, interval: int, config: Path) -> list:
    written = []
    if kind == "macos":
        dest = default_dest(kind)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(launchd_plist(interval, config), encoding="utf-8")
        written.append(dest)
    elif kind == "windows":
        dest = default_dest(kind)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(windows_task_xml(interval, config).encode("utf-16"))
        written.append(dest)
    else:
        base = expand_path("~/.config/systemd/user")
        base.mkdir(parents=True, exist_ok=True)
        service, timer = systemd_unit(interval, config)
        svc = base / f"{SYSTEMD_NAME}.service"
        tmr = base / f"{SYSTEMD_NAME}.timer"
        svc.write_text(service, encoding="utf-8")
        tmr.write_text(timer, encoding="utf-8")
        written.extend([svc, tmr])
    return written


def load_commands(kind: str, written: list) -> str:
    if kind == "macos":
        return f"launchctl load {written[0]}"
    if kind == "linux":
        return (
            "systemctl --user daemon-reload && "
            "systemctl --user enable --now inboxzerotokens.timer"
        )
    return f'schtasks /Create /TN InboxZeroTokens /XML "{written[0]}"'


def _xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
