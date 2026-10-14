from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / ".cursor-plugin" / "plugin.json"
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$")
ALLOWED_KEYS = {
    "name",
    "displayName",
    "description",
    "version",
    "minClientVersions",
    "author",
    "publisher",
    "homepage",
    "repository",
    "license",
    "logo",
    "keywords",
    "category",
    "tags",
    "commands",
    "agents",
    "skills",
    "rules",
    "hooks",
    "variables",
    "mcpServers",
}


def test_cursor_plugin_manifest_matches_current_schema():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    extra = set(data) - ALLOWED_KEYS
    assert not extra
    assert NAME_RE.fullmatch(data["name"])
    assert data["version"] == "1.0.0"
    assert "empty inbox" in data["description"].lower()
    assert data["author"]["name"]
    assert "email" not in data["author"]
    assert data["license"] == "MIT"
    assert data["skills"] == "./skills/"
    assert data["rules"] == "./rules/"
    assert (ROOT / "skills" / "inboxzerotokens" / "SKILL.md").is_file()
    assert (ROOT / "rules" / "inbox-precheck.mdc").is_file()


def test_skill_explains_precheck_and_zero_wakes():
    text = (ROOT / "skills" / "inboxzerotokens" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: inboxzerotokens" in text
    assert "scripted inbox pre-check" in text.lower()
    assert "zero wakes" in text.lower()
    assert "Never poll email" in text
