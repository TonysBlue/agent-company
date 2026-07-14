"""Controlled-beta session records with consent, provenance, metrics, and issue linkage."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .brandkit import canonical_json, load_json, write_json

SCHEMA = "pixweave-beta-session/v1"
PROTOCOL = "pixweave-controlled-beta/v1.0"
SCENARIOS = {"campaign-generation", "source-image-edit", "variant-review-feedback"}
OUTCOMES = {"success", "failure", "not_evaluable"}
MISSING = "not_collected"
ID_FIELDS = ("session_id", "approval_reference", "participant_pseudonym", "feedback_id")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BetaSessionError(ValueError):
    """Raised when a controlled-beta record is incomplete or unsafe."""


def _timestamp(value: Any, field: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str):
        errors.append(f"{field} must be an ISO-8601 timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field} must be an ISO-8601 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{field} must include a timezone")
        return None
    return parsed


def _bounded_id(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        errors.append(f"{field} must be a non-empty string of at most 128 characters")


def build_session_record(data: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if data.get("schema_version") != SCHEMA:
        errors.append(f"schema_version must be {SCHEMA}")
    if data.get("protocol_version") != PROTOCOL:
        errors.append(f"protocol_version must be {PROTOCOL}")
    for field in ID_FIELDS:
        _bounded_id(data.get(field), field, errors)

    consent = data.get("consent")
    if not isinstance(consent, dict) or consent.get("granted") is not True:
        errors.append("consent.granted must be true")
    else:
        _timestamp(consent.get("recorded_at"), "consent.recorded_at", errors)
        if consent.get("withdrawal_route_recorded") is not True:
            errors.append("consent.withdrawal_route_recorded must be true")
    rights = data.get("asset_rights")
    if not isinstance(rights, dict) or rights.get("attested") is not True or not rights.get("provenance"):
        errors.append("asset_rights must include attested=true and provenance")
    if data.get("contains_sensitive_data") is not False:
        errors.append("contains_sensitive_data must be false")
    if data.get("scenario") not in SCENARIOS:
        errors.append(f"scenario must be one of {sorted(SCENARIOS)}")
    if not isinstance(data.get("intended_outcome"), str) or not data["intended_outcome"].strip():
        errors.append("intended_outcome must be a non-empty string")

    started = _timestamp(data.get("started_at"), "started_at", errors)
    ended = _timestamp(data.get("ended_at"), "ended_at", errors)
    if started and ended and ended < started:
        errors.append("ended_at must not precede started_at")
    outcome = data.get("task_outcome")
    if outcome not in OUTCOMES:
        errors.append(f"task_outcome must be one of {sorted(OUTCOMES)}")
    if outcome == "not_evaluable" and not data.get("outcome_reason"):
        errors.append("not_evaluable requires outcome_reason")

    satisfaction = data.get("satisfaction", MISSING)
    if satisfaction != MISSING and (isinstance(satisfaction, bool) or not isinstance(satisfaction, int) or not 1 <= satisfaction <= 5):
        errors.append("satisfaction must be an integer from 1 to 5 or not_collected")
    for field in ("token_usage", "human_support_minutes"):
        value = data.get(field, MISSING)
        if value != MISSING and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
            errors.append(f"{field} must be nonnegative or not_collected")
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("artifacts must contain at least one artifact reference")
    elif any(
        not isinstance(item, dict)
        or not item.get("artifact_id")
        or not isinstance(item.get("sha256"), str)
        or SHA256_RE.fullmatch(item["sha256"]) is None
        for item in artifacts
    ):
        errors.append("each artifact requires artifact_id and a lowercase SHA-256 checksum")
    if not isinstance(data.get("quality_review"), dict) or not isinstance(data["quality_review"].get("passed"), bool):
        errors.append("quality_review.passed must be boolean")
    issues = data.get("issues")
    if not isinstance(issues, list) or any(not isinstance(item, dict) or not item.get("issue_id") or item.get("severity") not in {"low", "medium", "high", "critical"} for item in issues):
        errors.append("issues must be a list of linked issue_id/severity records")
    if data.get("retention_status") not in {"retained", "deleted", "scheduled_for_deletion"}:
        errors.append("retention_status is invalid")
    if errors:
        raise BetaSessionError("; ".join(errors))

    record = dict(data)
    record.setdefault("satisfaction", MISSING)
    record.setdefault("token_usage", MISSING)
    record.setdefault("human_support_minutes", MISSING)
    record["elapsed_minutes"] = round((ended - started).total_seconds() / 60, 3) if started and ended else None
    record["eligible_session"] = True
    record["external_action_authorized"] = False
    record["session_sha256"] = hashlib.sha256(canonical_json(data).encode()).hexdigest()
    return record


def capture_session_file(input_path: Path, output: Path) -> dict[str, Any]:
    record = build_session_record(load_json(input_path))
    write_json(output, record)
    return record
