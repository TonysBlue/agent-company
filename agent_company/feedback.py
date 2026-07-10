"""Privacy-bounded feedback capture and auditable triage records."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .brandkit import canonical_json, load_json, write_json

SUBMISSION_SCHEMA = "feedback-submission/v1"
TRIAGE_SCHEMA = "feedback-triage/v1"
CATEGORIES = {"bug", "quality", "workflow", "usability", "performance", "privacy", "other"}
SEVERITIES = {"low", "medium", "high", "critical"}
STATES = {"received", "acknowledged", "triaged", "planned", "released", "closed", "rejected"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class FeedbackError(ValueError):
    """Raised when feedback input violates the capture or triage contract."""


def _text(data: dict[str, Any], key: str, errors: list[str], *, required: bool = True, limit: int = 4000) -> str | None:
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key} must be a non-empty string")
        return None
    if len(value) > limit:
        errors.append(f"{key} must be at most {limit} characters")
    return value.strip()


def _validate_submission(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != SUBMISSION_SCHEMA:
        errors.append(f"schema_version must be {SUBMISSION_SCHEMA}")
    for key in ("submission_id", "product_version", "entry_point"):
        value = _text(data, key, errors, limit=128)
        if value and not ID_PATTERN.fullmatch(value):
            errors.append(f"{key} contains unsupported characters")
    _text(data, "message", errors)
    if data.get("category") not in CATEGORIES:
        errors.append(f"category must be one of {sorted(CATEGORIES)}")
    if data.get("severity") not in SEVERITIES:
        errors.append(f"severity must be one of {sorted(SEVERITIES)}")
    context = data.get("context")
    if not isinstance(context, dict):
        errors.append("context must be an object")
    else:
        for key in ("workflow_id", "artifact_ref"):
            value = context.get(key)
            if value is not None and (not isinstance(value, str) or not ID_PATTERN.fullmatch(value)):
                errors.append(f"context.{key} must be a bounded identifier when present")
    consent = data.get("contact_consent")
    if not isinstance(consent, bool):
        errors.append("contact_consent must be boolean")
    contact = data.get("contact")
    if contact is not None and (not consent or not isinstance(contact, str) or not contact.strip() or len(contact) > 320):
        errors.append("contact requires explicit contact_consent and a value of at most 320 characters")
    if data.get("contains_sensitive_data") is not False:
        errors.append("contains_sensitive_data must be false; sensitive submissions are rejected")
    if data.get("honeypot") not in (None, ""):
        errors.append("anti-abuse check failed")
    return errors


def capture_feedback(data: dict[str, Any], output: Path) -> dict[str, Any]:
    errors = _validate_submission(data)
    if errors:
        raise FeedbackError("; ".join(errors))
    record = dict(data)
    record.pop("honeypot", None)
    record["state"] = "received"
    record["external_action_authorized"] = False
    record["submission_sha256"] = hashlib.sha256(canonical_json(data).encode()).hexdigest()
    write_json(output, record)
    return record


def triage_feedback(submission_path: Path, decision: dict[str, Any], output: Path) -> dict[str, Any]:
    submission = load_json(submission_path)
    errors = _validate_submission(submission)
    if errors:
        raise FeedbackError("invalid submission: " + "; ".join(errors))
    if decision.get("schema_version") != TRIAGE_SCHEMA:
        errors.append(f"schema_version must be {TRIAGE_SCHEMA}")
    for key in ("reviewer_ref", "decision_at", "rationale"):
        _text(decision, key, errors, limit=1000)
    state = decision.get("state")
    if state not in STATES - {"received"}:
        errors.append(f"state must be one of {sorted(STATES - {'received'})}")
    backlog_task_id = decision.get("backlog_task_id")
    if state in {"planned", "released"} and (not isinstance(backlog_task_id, int) or backlog_task_id <= 0):
        errors.append("planned/released state requires a positive backlog_task_id")
    release_version = decision.get("release_version")
    if state == "released" and (not isinstance(release_version, str) or not release_version.strip()):
        errors.append("released state requires release_version")
    if errors:
        raise FeedbackError("; ".join(errors))
    record = {
        "schema_version": TRIAGE_SCHEMA,
        "submission_id": submission["submission_id"],
        "submission_sha256": hashlib.sha256(canonical_json(submission).encode()).hexdigest(),
        "category": submission["category"],
        "severity": submission["severity"],
        "state": state,
        "reviewer_ref": decision["reviewer_ref"],
        "decision_at": decision["decision_at"],
        "rationale": decision["rationale"],
        "backlog_task_id": backlog_task_id,
        "release_version": release_version,
        "external_action_authorized": False,
    }
    record["triage_sha256"] = hashlib.sha256(canonical_json(record).encode()).hexdigest()
    write_json(output, record)
    return record


def capture_feedback_file(input_path: Path, output: Path) -> dict[str, Any]:
    return capture_feedback(load_json(input_path), output)


def triage_feedback_file(submission: Path, decision: Path, output: Path) -> dict[str, Any]:
    return triage_feedback(submission, load_json(decision), output)
