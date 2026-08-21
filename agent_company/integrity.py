"""Keyed integrity anchors for assurance records stored in SQLite."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import stat
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


def recovery_ledger_path(db_path: Path) -> Path:
    return db_path.parent / "task-recovery-ledger.jsonl"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise IntegrityError("duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


def ensure_recovery_ledger(db_path: Path) -> Path:
    path = recovery_ledger_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists() and not path.is_file():
        raise IntegrityError("recovery ledger must be a regular file")
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    info = path.stat()
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
        raise IntegrityError("recovery ledger must be an owner-only regular file")
    return path


def read_recovery_ledger(db_path: Path) -> list[dict[str, Any]]:
    path = ensure_recovery_ledger(db_path)
    entries: list[dict[str, Any]] = []
    previous = "0" * 64
    for raw in path.read_text(encoding="ascii").splitlines():
        entry = _strict_json(raw)
        if not isinstance(entry, dict) or raw != _canonical_json(entry):
            raise IntegrityError("recovery ledger is not canonical JSON")
        values = entry.get("values")
        signed = {"sequence": len(entries) + 1, "previous_sha256": previous, "values": values}
        if (
            not isinstance(values, dict)
            or entry.get("sequence") != signed["sequence"]
            or entry.get("previous_sha256") != previous
            or not verify(db_path, "task-recovery-external-ledger", signed, entry.get("integrity_signature"))
        ):
            raise IntegrityError("recovery ledger integrity mismatch")
        previous = hashlib.sha256(raw.encode("ascii")).hexdigest()
        entries.append(entry)
    return entries


def append_recovery_ledger(db_path: Path, values: dict[str, Any]) -> None:
    entries = read_recovery_ledger(db_path)
    previous = hashlib.sha256(_canonical_json(entries[-1]).encode("ascii")).hexdigest() if entries else "0" * 64
    signed = {"sequence": len(entries) + 1, "previous_sha256": previous, "values": values}
    entry = {**signed, "integrity_signature": signature(db_path, "task-recovery-external-ledger", signed)}
    path = ensure_recovery_ledger(db_path)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        os.write(fd, (_canonical_json(entry) + "\n").encode("ascii"))
        os.fsync(fd)
    finally:
        os.close(fd)


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
RECOVERY_EVENT_PRIORITY = 10
RECOVERY_PROVENANCE_SIGNATURE_FIELDS = (
    "recovery_record_id", "task_id", "audit_log_id", "event_id",
    "record_integrity_signature", "event_priority",
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


def recovery_provenance_payload(row: Any) -> dict[str, Any]:
    """Return the exact audit/event representation of a signed recovery row."""
    return {
        "record_id": int(row["id"]),
        "task_id": int(row["task_id"]),
        "recovered_at": row["recovered_at"],
        "actor": row["actor"],
        "reason": row["reason"],
        "scope": row["scope"],
        "process_dead_proof": row["process_dead_proof"],
        "previous_task_state": row["previous_task_state"],
        "previous_execution_state": row["previous_execution_state"],
        "new_task_state": row["new_task_state"],
        "new_execution_state": row["new_execution_state"],
        "audit_log_id": int(row["audit_log_id"]),
        "event_id": int(row["event_id"]),
        "recovery_integrity_signature": row["integrity_signature"],
        "event_priority": RECOVERY_EVENT_PRIORITY,
    }


def recovery_provenance_values(row: Any) -> dict[str, Any]:
    return {field: row[field] for field in RECOVERY_PROVENANCE_SIGNATURE_FIELDS}


def recovery_provenance_signature(db_path: Path, values: dict[str, Any]) -> str:
    return signature(db_path, "task-recovery-provenance", values)


def verify_recovery_provenance_signature(db_path: Path, row: Any) -> bool:
    return verify(
        db_path, "task-recovery-provenance", recovery_provenance_values(row),
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
