"""Resolve mailbox and wake secrets without storing passwords in config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from inboxzerotokens.util import expand_path, is_world_readable

try:
    import keyring as _keyring
except ImportError:
    _keyring = None


class SecretError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecretRef:
    env: Optional[str] = None
    file: Optional[str] = None
    keyring_service: Optional[str] = None
    keyring_username: Optional[str] = None

    def described(self) -> str:
        if self.env:
            return f"env:{self.env}"
        if self.file:
            return f"file:{self.file}"
        if self.keyring_service:
            return f"keyring:{self.keyring_service}/{self.keyring_username}"
        return "unset"


def parse_secret_ref(raw: object, default_keyring_user: str) -> SecretRef:
    if raw is None:
        return SecretRef()
    if isinstance(raw, str):
        if raw.startswith("env:"):
            return SecretRef(env=raw[4:])
        if raw.startswith("file:"):
            return SecretRef(file=raw[5:])
        if raw.startswith("keyring:"):
            rest = raw[8:]
            if "/" in rest:
                svc, user = rest.split("/", 1)
            else:
                svc, user = rest, default_keyring_user
            return SecretRef(keyring_service=svc or "inboxzerotokens", keyring_username=user)
        raise SecretError("secret strings must start with env:, file:, or keyring:")
    if not isinstance(raw, dict):
        raise SecretError("secret must be a string or table")
    forbidden = {"password", "value", "secret", "token"}
    if forbidden.intersection(raw):
        raise SecretError("plaintext secrets are not allowed in the config file")
    return SecretRef(
        env=raw.get("env"),
        file=raw.get("file"),
        keyring_service=raw.get("keyring_service") or raw.get("service"),
        keyring_username=raw.get("keyring_username") or raw.get("username") or default_keyring_user,
    )


def resolve_secret(ref: SecretRef) -> str:
    if not any([ref.env, ref.file, ref.keyring_service]):
        raise SecretError("no secret reference configured")
    if ref.env:
        value = os.environ.get(ref.env)
        if not value:
            raise SecretError(f"environment variable {ref.env} is empty or unset")
        return value
    if ref.file:
        path = expand_path(ref.file)
        if not path.is_file():
            raise SecretError(f"secret file not found: {path}")
        if is_world_readable(path):
            raise SecretError(
                f"refusing world-readable secret file {path} (chmod 0600 required)"
            )
        value = path.read_text(encoding="utf-8").strip()
        if not value:
            raise SecretError(f"secret file is empty: {path}")
        return value
    if _keyring is None:
        raise SecretError("keyring extra is not installed (pip install inboxzerotokens[keyring])")
    value = _keyring.get_password(ref.keyring_service or "inboxzerotokens", ref.keyring_username or "")
    if not value:
        raise SecretError(
            f"no password in keyring for {ref.keyring_service}/{ref.keyring_username}"
        )
    return value


def store_keyring(service: str, username: str, password: str) -> None:
    if _keyring is None:
        raise SecretError("keyring extra is not installed (pip install inboxzerotokens[keyring])")
    _keyring.set_password(service, username, password)
