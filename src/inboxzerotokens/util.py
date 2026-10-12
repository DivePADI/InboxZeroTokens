"""Locking, atomic JSON, and safe logging."""

from __future__ import annotations

import json
import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, TextIO

if os.name == "nt":
    import msvcrt

    fcntl = None
else:
    import fcntl

    msvcrt = None

_SECRET_HINTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "apikey",
    "api_key",
    "authorization",
    "bearer",
    "hmac",
    "wakeup_key",
    "wake_key",
)


def expand_path(value: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(value))).resolve()


def ensure_dir(path: Path, mode: int = 0o700) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, mode)
    except OSError:
        pass
    return path


def is_world_readable(path: Path) -> bool:
    if os.name == "nt":
        return False
    try:
        mode = path.stat().st_mode
    except OSError:
        return False
    return bool(mode & (stat.S_IRWXG | stat.S_IRWXO))


def atomic_write_json(path: Path, obj: Any, mode: int = 0o600) -> None:
    ensure_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    data = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def load_json(path: Path, default: Any) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default


class SafeLogger:
    """Append-only logger that refuses to echo secret-looking fields."""

    def __init__(self, path: Optional[Path], echo: bool = False) -> None:
        self.path = path
        self.echo = echo
        if path is not None:
            ensure_dir(path.parent)

    def log(self, line: str, fields: Optional[dict] = None) -> None:
        if fields is not None:
            self.refuse_secrets(fields)
        text = f"{time.strftime('%Y-%m-%d %H:%M:%S %Z')} {line}"
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        if self.echo:
            print(text, file=sys.stderr)

    def refuse_secrets(self, mapping: dict) -> None:
        for key in mapping:
            lk = str(key).lower()
            if any(hint in lk for hint in _SECRET_HINTS):
                raise ValueError("refusing to log a secret-looking field")


@contextmanager
def exclusive_lock(path: Path) -> Iterator[TextIO]:
    ensure_dir(path.parent)
    fh = path.open("a+")
    try:
        _lock(fh)
        yield fh
    finally:
        try:
            _unlock(fh)
        finally:
            fh.close()


def _lock(fh: TextIO) -> None:
    if msvcrt is not None:
        fh.seek(0)
        fh.write("0")
        fh.flush()
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fh: TextIO) -> None:
    try:
        if msvcrt is not None:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            return
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
