"""Keyed integrity anchors for assurance records stored in SQLite."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any


class IntegrityError(ValueError):
    pass


def key_path(db_path: Path) -> Path:
    return db_path.parent / "assurance-integrity.key"


def ensure_key(db_path: Path) -> bytes:
    path = key_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists() and not path.is_file():
        raise IntegrityError("assurance integrity key must be a regular file")
    if not path.exists():
        fd = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            stream.write(secrets.token_hex(32) + "\n")
    if path.stat().st_mode & 0o777 != 0o600:
        raise IntegrityError("assurance integrity key must be a 0600 file")
    try:
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        raise IntegrityError("assurance integrity key is invalid") from exc
    if len(key) != 32:
        raise IntegrityError("assurance integrity key is invalid")
    return key


def signature(db_path: Path, domain: str, values: dict[str, Any]) -> str:
    payload = json.dumps(
        {"domain": domain, "values": values},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hmac.new(ensure_key(db_path), payload, hashlib.sha256).hexdigest()


def verify(
    db_path: Path, domain: str, values: dict[str, Any], supplied: str | None,
) -> bool:
    return bool(supplied) and hmac.compare_digest(
        signature(db_path, domain, values), supplied,
    )
