"""Validate and escape untrusted mail-header text for logs, JSON, and argv safety."""

from __future__ import annotations

import re
import unicodedata

_CTRL = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")
_WS = re.compile(r"\s+")


def clean_header(value: object, max_len: int = 200) -> str:
    if value is None:
        return ""
    text = str(value)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cc" or ch in "\t ")
    text = _CTRL.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def clean_msgid(value: object) -> str:
    text = clean_header(value, 300)
    return text.strip("<> ").lower()


def email_address(value: object) -> str:
    """Extract a lowercase addr-spec from a From/Sender header."""
    text = clean_header(value, 320)
    if not text:
        return ""
    if "<" in text and ">" in text:
        start = text.rfind("<")
        end = text.find(">", start)
        if end > start:
            text = text[start + 1 : end]
    text = text.strip().strip('"').lower()
    if "@" not in text:
        return text
    local, _, domain = text.partition("@")
    local = local.strip()
    domain = domain.strip().strip(">")
    if not local or not domain:
        return ""
    return f"{local}@{domain}"


def domain_of(addr: str) -> str:
    if "@" not in addr:
        return ""
    return addr.rsplit("@", 1)[-1].lower()
