"""Load TOML using stdlib tomllib (3.11+) or a documented subset parser."""

from __future__ import annotations

import ast
import re
from typing import Any, Tuple

try:
    import tomllib as _tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    _tomllib = None  # type: ignore[assignment]


def load_toml_bytes(data: bytes) -> dict:
    if _tomllib is not None:
        return _tomllib.loads(data.decode("utf-8"))
    return loads_subset(data.decode("utf-8"))


def load_toml_path(path: str) -> dict:
    with open(path, "rb") as fh:
        return load_toml_bytes(fh.read())


def loads_subset(text: str) -> dict:
    """Parse a practical TOML subset used by InboxZeroTokens configs.

    Supports comments, basic/literal strings, numbers, booleans, arrays,
    inline tables, [tables], [[array-of-tables]], and dotted keys.
    """
    root: dict = {}
    current: dict = root
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = _strip_comment(raw).strip()
        i += 1
        if not line:
            continue
        if line.startswith("[[") and line.endswith("]]"):
            keys = _split_keys(line[2:-2].strip())
            parent = _ensure_path(root, keys[:-1])
            bucket = parent.setdefault(keys[-1], [])
            if not isinstance(bucket, list):
                raise ValueError(f"TOML key {'.'.join(keys)} is not an array")
            current = {}
            bucket.append(current)
            continue
        if line.startswith("[") and line.endswith("]"):
            keys = _split_keys(line[1:-1].strip())
            current = _ensure_path(root, keys)
            continue
        if "=" not in line:
            raise ValueError(f"Invalid TOML line: {raw!r}")
        key_part, value_part = line.split("=", 1)
        keys = _split_keys(key_part.strip())
        value_text = value_part.strip()
        while not _value_complete(value_text) and i < len(lines):
            value_text += " " + _strip_comment(lines[i]).strip()
            i += 1
        value = _parse_value(value_text)
        target = _ensure_path(current, keys[:-1])
        target[keys[-1]] = value
    return root


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    out = []
    for ch in line:
        if escaped:
            out.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_double:
            out.append(ch)
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            break
        out.append(ch)
    return "".join(out)


def _split_keys(path: str) -> Tuple[str, ...]:
    parts = []
    buf = ""
    in_quotes = False
    quote = ""
    for ch in path:
        if in_quotes:
            buf += ch
            if ch == quote:
                in_quotes = False
            continue
        if ch in "\"'":
            in_quotes = True
            quote = ch
            buf += ch
            continue
        if ch == ".":
            parts.append(_unquote_key(buf.strip()))
            buf = ""
            continue
        buf += ch
    if buf.strip():
        parts.append(_unquote_key(buf.strip()))
    if not parts:
        raise ValueError("Empty TOML key")
    return tuple(parts)


def _unquote_key(key: str) -> str:
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        return key[1:-1]
    return key


def _ensure_path(root: dict, keys: Tuple[str, ...]) -> dict:
    node = root
    for key in keys:
        nxt = node.get(key)
        if nxt is None:
            nxt = {}
            node[key] = nxt
        elif isinstance(nxt, list):
            if not nxt or not isinstance(nxt[-1], dict):
                raise ValueError(f"Cannot descend into TOML array {key}")
            nxt = nxt[-1]
        elif not isinstance(nxt, dict):
            raise ValueError(f"TOML key {key} is not a table")
        node = nxt
    return node


def _value_complete(text: str) -> bool:
    s = text.strip()
    if not s:
        return False
    if s[0] in "[{":
        return _balanced(s)
    if s[0] in "\"'":
        return len(s) >= 2 and s[-1] == s[0] and not _odd_trailing_escapes(s[:-1])
    return True


def _odd_trailing_escapes(s: str) -> bool:
    n = 0
    for ch in reversed(s):
        if ch != "\\":
            break
        n += 1
    return n % 2 == 1


def _balanced(s: str) -> bool:
    stack = []
    in_single = False
    in_double = False
    escaped = False
    for ch in s:
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            continue
        if in_single or in_double:
            continue
        if ch in "[{":
            stack.append("]" if ch == "[" else "}")
        elif ch in "]}":
            if not stack or stack.pop() != ch:
                return False
    return not stack and not in_single and not in_double


def _parse_value(text: str) -> Any:
    s = text.strip()
    if not s:
        raise ValueError("Empty TOML value")
    if s in ("true", "false"):
        return s == "true"
    if s[0] == '"':
        return ast.literal_eval(s)
    if s[0] == "'":
        return s[1:-1]
    if s[0] == "[":
        return _parse_array(s)
    if s[0] == "{":
        return _parse_inline_table(s)
    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)(?:[eE][+-]?\d+)?", s) or re.fullmatch(
        r"[+-]?\d+[eE][+-]?\d+", s
    ):
        return float(s)
    raise ValueError(f"Unsupported TOML value: {s!r}")


def _parse_array(text: str) -> list:
    inner = text.strip()[1:-1].strip()
    if not inner:
        return []
    items = []
    buf = ""
    depth = 0
    in_single = False
    in_double = False
    escaped = False
    for ch in inner:
        if escaped:
            buf += ch
            escaped = False
            continue
        if ch == "\\" and in_double:
            buf += ch
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "," and depth == 0:
                if buf.strip():
                    items.append(_parse_value(buf.strip()))
                buf = ""
                continue
        buf += ch
    if buf.strip():
        items.append(_parse_value(buf.strip()))
    return items


def _parse_inline_table(text: str) -> dict:
    inner = text.strip()[1:-1].strip()
    if not inner:
        return {}
    out = {}
    buf = ""
    depth = 0
    in_single = False
    in_double = False
    escaped = False
    parts = []
    for ch in inner:
        if escaped:
            buf += ch
            escaped = False
            continue
        if ch == "\\" and in_double:
            buf += ch
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append(buf)
                buf = ""
                continue
        buf += ch
    if buf.strip():
        parts.append(buf)
    for part in parts:
        if "=" not in part:
            raise ValueError(f"Invalid inline table field: {part!r}")
        k, v = part.split("=", 1)
        keys = _split_keys(k.strip())
        target = _ensure_path(out, keys[:-1])
        target[keys[-1]] = _parse_value(v.strip())
    return out
