"""Scan a tree for leaked secrets and optional maintainer-private tokens.

The public scan uses only generic patterns. Extra deny tokens come only from
``IZT_PRIVATE_HASHES`` or the gitignored ``IZT_PRIVATE_PATTERNS_FILE``.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Iterable, List, Optional, Pattern, Set, Tuple

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".izt-mock-state",
    ".pytest_cache",
    ".ruff_cache",
    ".eggs",
}

SCAN_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".sh", ".txt", ".xml", ".json"}

# Generic built-in list. No personal mailboxes, names, or private hostnames.
GENERIC_PATTERNS: Tuple[Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{20,}\b"),
)

TOKEN_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"|[A-Za-z][A-Za-z0-9._-]{2,}"
)

Hit = Tuple[str, str]


def normalize_token(value: str) -> str:
    return value.strip().strip("<>\"'").lower()


def token_hash(value: str) -> str:
    return hashlib.sha256(normalize_token(value).encode("utf-8")).hexdigest()


def load_extra_hashes(env: Optional[dict] = None) -> Set[str]:
    source = env if env is not None else os.environ
    hashes: Set[str] = set()
    raw = source.get("IZT_PRIVATE_HASHES") or ""
    for part in raw.split(","):
        digest = part.strip().lower()
        if _is_sha256(digest):
            hashes.add(digest)
    return hashes


def load_pattern_file(path: Path) -> Tuple[Set[str], List[Pattern[str]]]:
    hashes: Set[str] = set()
    regexes: List[Pattern[str]] = []
    if not path.is_file():
        return hashes, regexes
    for line in path.read_text(encoding="utf-8").splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        if item.startswith("sha256:"):
            digest = item[7:].strip().lower()
            if _is_sha256(digest):
                hashes.add(digest)
            continue
        if item.startswith("re:"):
            regexes.append(re.compile(item[3:]))
            continue
        if _is_sha256(item.lower()):
            hashes.add(item.lower())
            continue
        hashes.add(token_hash(item))
        regexes.append(re.compile(re.escape(item), re.I))
    return hashes, regexes


def extra_from_env(env: Optional[dict] = None) -> Tuple[Set[str], List[Pattern[str]]]:
    source = env if env is not None else os.environ
    hashes = load_extra_hashes(source)
    regexes: List[Pattern[str]] = []
    raw_path = source.get("IZT_PRIVATE_PATTERNS_FILE")
    if raw_path:
        more_hashes, more_re = load_pattern_file(Path(raw_path))
        hashes.update(more_hashes)
        regexes.extend(more_re)
    return hashes, regexes


def scan_text(
    text: str,
    *,
    extra_hashes: Optional[Iterable[str]] = None,
    extra_regexes: Optional[Iterable[Pattern[str]]] = None,
) -> List[str]:
    hits: List[str] = []
    for pat in GENERIC_PATTERNS:
        if pat.search(text):
            hits.append(f"generic:{pat.pattern}")
    for pat in extra_regexes or []:
        if pat.search(text):
            hits.append("deny-file-pattern")
    wanted = set(extra_hashes or [])
    if wanted:
        for token in TOKEN_RE.findall(text):
            if token_hash(token) in wanted:
                hits.append("hashed-token")
                break
    return hits


def scan_tree(
    root: Path,
    *,
    extra_hashes: Optional[Iterable[str]] = None,
    extra_regexes: Optional[Iterable[Pattern[str]]] = None,
) -> List[Hit]:
    found: List[Hit] = []
    extra_h = set(extra_hashes or [])
    extra_r = list(extra_regexes or [])
    env_h, env_r = extra_from_env()
    extra_h.update(env_h)
    extra_r.extend(env_r)
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in SCAN_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(root))
        for reason in scan_text(text, extra_hashes=extra_h, extra_regexes=extra_r):
            found.append((rel, reason))
    return found


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)
