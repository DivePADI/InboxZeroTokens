from __future__ import annotations

import re
from pathlib import Path

from inboxzerotokens.hygiene import SKIP_DIRS, scan_text, scan_tree, token_hash

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {
    ".py",
    ".md",
    ".mdc",
    ".toml",
    ".yml",
    ".yaml",
    ".sh",
    ".txt",
    ".xml",
    ".json",
}

# Built in pieces so this file does not contain the deny literals.
LEAK_PATTERNS = (
    re.compile("@" + "gmail" + r"\.com", re.I),
    re.compile("/" + "Users" + "/"),
    re.compile("/" + "Volumes" + "/"),
)


def _iter_text_files():
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield path


def test_repo_has_no_generic_or_hashed_leaks():
    hits = scan_tree(ROOT)
    assert hits == []


def test_repo_has_no_personal_paths_or_gmail():
    found = []
    for path in _iter_text_files():
        text = path.read_text(encoding="utf-8")
        for pat in LEAK_PATTERNS:
            if pat.search(text):
                found.append(f"{path.relative_to(ROOT)}:{pat.pattern}")
    assert found == []


def test_generic_pattern_catches_private_key_block():
    pem = "-----" + "BEGIN RSA " + "PRIVATE" + " KEY-----\nAAAA\n"
    assert any(h.startswith("generic:") for h in scan_text(pem))


def test_hashed_token_match_without_storing_preimage_in_assert():
    canary = "izt-hygiene-canary"
    digest = token_hash(canary)
    assert scan_text(f"contact {canary} please", extra_hashes={digest}) == ["hashed-token"]
    assert scan_text("contact nobody@example.com please", extra_hashes={digest}) == []


def test_untracked_pattern_file(tmp_path, monkeypatch):
    deny = tmp_path / "deny.txt"
    deny.write_text("re:canary-from-file-xyz\n", encoding="utf-8")
    monkeypatch.setenv("IZT_PRIVATE_PATTERNS_FILE", str(deny))
    hits = scan_tree(tmp_path)
    # the deny file itself contains the pattern
    assert hits
    empty = tmp_path / "notes.md"
    empty.write_text("nothing to see\n", encoding="utf-8")
    deny.unlink()
    assert scan_tree(tmp_path) == []
