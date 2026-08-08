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


RECONCILIATION_SIGNATURE_FIELDS = (
    "task_id", "reconciled_at", "actor", "accepted_source_commit",
    "accepted_source_tree", "evidence_tip_commit", "evidence_tip_tree",
    "independent_verdict", "reason", "previous_task_state",
    "previous_execution_state",
)


def reconciliation_values(row: Any) -> dict[str, Any]:
    return {field: row[field] for field in RECONCILIATION_SIGNATURE_FIELDS}


def reconciliation_signature(db_path: Path, values: dict[str, Any]) -> str:
    return signature(db_path, "task-reconciliation", values)


def verify_reconciliation_signature(db_path: Path, row: Any) -> bool:
    return verify(
        db_path, "task-reconciliation", reconciliation_values(row),
        row["integrity_signature"],
    )


TASK_RECOVERY_SIGNATURE_FIELDS = (
    "id", "task_id", "recovered_at", "actor", "reason", "scope",
    "process_dead_proof", "previous_task_state", "previous_execution_state",
    "new_task_state", "new_execution_state", "audit_log_id", "event_id",
)


def recovery_values(row: Any) -> dict[str, Any]:
    return {field: row[field] for field in TASK_RECOVERY_SIGNATURE_FIELDS}


def recovery_signature(db_path: Path, values: dict[str, Any]) -> str:
    return signature(db_path, "task-exhausted-recovery", values)


def verify_recovery_signature(db_path: Path, row: Any) -> bool:
    return verify(
        db_path, "task-exhausted-recovery", recovery_values(row),
        row["integrity_signature"],
    )


APPROVAL_BINDING_FIELDS = (
    "created_at", "requested_by", "action_type", "summary", "target_task_id",
    "target_action",
)


def approval_binding_values(row: Any) -> dict[str, Any]:
    return {field: row[field] for field in APPROVAL_BINDING_FIELDS}


def approval_binding_signature(db_path: Path, values: dict[str, Any]) -> str:
    return signature(db_path, "chairman-approval-binding", values)


def verify_approval_binding_signature(db_path: Path, row: Any) -> bool:
    return verify(
        db_path, "chairman-approval-binding", approval_binding_values(row),
        row["integrity_signature"],
    )


APPROVAL_DECISION_FIELDS = (
    "id", "created_at", "requested_by", "action_type", "summary", "target_task_id",
    "target_action", "status", "decision", "rationale", "decided_at", "decided_by",
)


def approval_decision_values(row: Any) -> dict[str, Any]:
    return {field: row[field] for field in APPROVAL_DECISION_FIELDS}


def approval_decision_signature(db_path: Path, values: dict[str, Any]) -> str:
    return signature(db_path, "chairman-approval-decision", values)


def verify_approval_decision_signature(db_path: Path, row: Any) -> bool:
    return verify(
        db_path, "chairman-approval-decision", approval_decision_values(row),
        row["decision_integrity_signature"],
    )
