from __future__ import annotations

from pathlib import Path

from inboxzerotokens.hygiene import scan_text, scan_tree, token_hash


def test_repo_has_no_generic_or_hashed_leaks():
    hits = scan_tree(Path(__file__).resolve().parents[1])
    assert hits == []


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
