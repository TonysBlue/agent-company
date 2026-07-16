"""Controlled-beta session records with consent, provenance, metrics, and issue linkage."""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_tokens", "reasoning_tokens")
OBSERVATION_FIELDS = (
    "token_usage",
    "operation_duration_minutes",
    "human_review_minutes",
    "quality_score",
)


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


def _source(value: Any, field: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        errors.append(f"{field}.source must be a non-empty string of at most 256 characters")


def _session_link(observation: dict[str, Any], session_id: str, field: str, errors: list[str]) -> None:
    linked = observation.get("session_id", session_id)
    if linked != session_id:
        errors.append(f"{field}.session_id must match session_id")


def _number(value: Any, field: str, errors: list[str], *, maximum: float | None = None) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        errors.append(f"{field} must be nonnegative")
    elif maximum is not None and value > maximum:
        errors.append(f"{field} must be at most {maximum:g}")


def _token_observation(value: Any, session_id: str, errors: list[str]) -> dict[str, Any] | str:
    if value == MISSING:
        return MISSING
    if not isinstance(value, dict):
        errors.append("token_usage must be an observation object or not_collected")
        return MISSING
    observation = dict(value)
    _session_link(observation, session_id, "token_usage", errors)
    _source(observation.get("source"), "token_usage", errors)
    for field in TOKEN_FIELDS + ("total_tokens",):
        field_value = observation.get(field)
        if isinstance(field_value, bool) or not isinstance(field_value, int) or field_value < 0:
            errors.append(f"token_usage.{field} must be a nonnegative integer")
    if all(isinstance(observation.get(field), int) and not isinstance(observation.get(field), bool) for field in TOKEN_FIELDS + ("total_tokens",)):
        if observation["total_tokens"] != sum(observation[field] for field in TOKEN_FIELDS):
            errors.append("token_usage.total_tokens must equal the component token counts")
    cost = observation.get("cost", MISSING)
    if cost != MISSING:
        _number(cost, "token_usage.cost", errors)
        currency = observation.get("currency")
        if not isinstance(currency, str) or not currency.strip():
            errors.append("token_usage.currency is required when cost is collected")
    observation.setdefault("cost", MISSING)
    observation.setdefault("currency", MISSING)
    observation["session_id"] = session_id
    return observation


def _numeric_observation(
    value: Any,
    session_id: str,
    field: str,
    errors: list[str],
    *,
    maximum: float | None = None,
) -> dict[str, Any] | str:
    if value == MISSING:
        return MISSING
    if not isinstance(value, dict):
        errors.append(f"{field} must be an observation object or not_collected")
        return MISSING
    observation = dict(value)
    _session_link(observation, session_id, field, errors)
    _source(observation.get("source"), field, errors)
    _number(observation.get("value"), f"{field}.value", errors, maximum=maximum)
    observation["session_id"] = session_id
    return observation


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

    session_id = data.get("session_id")
    satisfaction = data.get("satisfaction", MISSING)
    if satisfaction != MISSING and (isinstance(satisfaction, bool) or not isinstance(satisfaction, int) or not 1 <= satisfaction <= 5):
        errors.append("satisfaction must be an integer from 1 to 5 or not_collected")
    token_usage = _token_observation(data.get("token_usage", MISSING), session_id, errors)
    human_review = _numeric_observation(
        data.get("human_review_minutes", MISSING), session_id, "human_review_minutes", errors
    )
    quality_score = _numeric_observation(
        data.get("quality_score", MISSING), session_id, "quality_score", errors
    )
    human_support = data.get("human_support_minutes", MISSING)
    if human_support != MISSING:
        _number(human_support, "human_support_minutes", errors)
    if quality_score != MISSING:
        scale_max = quality_score.get("scale_max")
        if isinstance(scale_max, bool) or not isinstance(scale_max, int) or scale_max <= 0:
            errors.append("quality_score.scale_max must be a positive integer")
        elif quality_score.get("value", scale_max + 1) > scale_max:
            errors.append("quality_score.value must not exceed quality_score.scale_max")
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
    record["token_usage"] = token_usage
    record["human_review_minutes"] = human_review
    record["quality_score"] = quality_score
    record["human_support_minutes"] = human_support
    record["elapsed_minutes"] = round((ended - started).total_seconds() / 60, 3) if started and ended else None
    record["operation_duration_minutes"] = {
        "value": record["elapsed_minutes"],
        "source": "session_timestamps",
        "session_id": session_id,
    }
    record["eligible_session"] = True
    record["external_action_authorized"] = False
    record["session_sha256"] = hashlib.sha256(canonical_json(data).encode()).hexdigest()
    return record


def capture_session_file(input_path: Path, output: Path) -> dict[str, Any]:
    record = build_session_record(load_json(input_path))
    write_json(output, record)
    return record


def summarize_session_economics(data: dict[str, Any]) -> dict[str, Any]:
    """Aggregate local synthetic session evidence without imputing missing observations."""
    if data.get("schema_version") != "pixweave-beta-session-economics/v1":
        raise BetaSessionError("schema_version must be pixweave-beta-session-economics/v1")
    if data.get("dataset_kind") != "synthetic":
        raise BetaSessionError("dataset_kind must be synthetic; customer data is not accepted")
    currency = data.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        raise BetaSessionError("currency must be a non-empty string")
    hourly_cost = _decimal(data.get("human_review_hourly_cost"), "human_review_hourly_cost")
    if hourly_cost < 0:
        raise BetaSessionError("human_review_hourly_cost must be nonnegative")
    raw_sessions = data.get("sessions")
    if not isinstance(raw_sessions, list) or not raw_sessions:
        raise BetaSessionError("sessions must be a non-empty list")

    sessions = [build_session_record(item) if isinstance(item, dict) else _invalid_session(index) for index, item in enumerate(raw_sessions)]
    session_ids = [session["session_id"] for session in sessions]
    if len(session_ids) != len(set(session_ids)):
        raise BetaSessionError("session_id values must be unique")

    coverage = {
        field: {
            "collected": sum(session[field] != MISSING for session in sessions),
            "not_collected": sum(session[field] == MISSING for session in sessions),
        }
        for field in OBSERVATION_FIELDS
    }
    observed_tokens = [session["token_usage"] for session in sessions if session["token_usage"] != MISSING]
    observed_review = [session["human_review_minutes"] for session in sessions if session["human_review_minutes"] != MISSING]
    observed_quality = [session["quality_score"] for session in sessions if session["quality_score"] != MISSING]
    quality_scales = {item["scale_max"] for item in observed_quality}
    if len(quality_scales) > 1:
        raise BetaSessionError("quality_score.scale_max must be consistent across collected sessions")
    operation_minutes = sum(Decimal(str(session["operation_duration_minutes"]["value"])) for session in sessions)
    success_count = sum(session["task_outcome"] == "success" for session in sessions)

    fully_costed = [
        session
        for session in sessions
        if session["token_usage"] != MISSING
        and session["token_usage"]["cost"] != MISSING
        and session["token_usage"]["currency"] == currency
        and session["human_review_minutes"] != MISSING
    ]
    estimated_cost = sum(
        Decimal(str(session["token_usage"]["cost"]))
        + Decimal(str(session["human_review_minutes"]["value"])) / Decimal("60") * hourly_cost
        for session in fully_costed
    )
    fully_costed_successes = sum(session["task_outcome"] == "success" for session in fully_costed)

    return {
        "schema_version": "pixweave-beta-session-economics-result/v1",
        "dataset_kind": "synthetic",
        "currency": currency,
        "session_count": len(sessions),
        "session_links": [
            {"session_id": session["session_id"], "session_sha256": session["session_sha256"]}
            for session in sessions
        ],
        "coverage": coverage,
        "outcomes": {
            "successful_sessions": success_count,
            "success_rate": _ratio(success_count, len(sessions)),
        },
        "totals": {
            "total_tokens": sum(item["total_tokens"] for item in observed_tokens) if observed_tokens else MISSING,
            "operation_duration_minutes": _decimal_number(operation_minutes),
            "human_review_minutes": _decimal_number(sum(Decimal(str(item["value"])) for item in observed_review)) if observed_review else MISSING,
        },
        "efficiency": {
            "successful_sessions_per_operation_hour": _decimal_number(Decimal(success_count) / (operation_minutes / Decimal("60"))) if operation_minutes else MISSING,
        },
        "quality": {
            "average_score": _decimal_number(sum(Decimal(str(item["value"])) for item in observed_quality) / len(observed_quality)) if observed_quality else MISSING,
            "scale_max": next(iter(quality_scales)) if quality_scales else MISSING,
        },
        "unit_economics": {
            "human_review_hourly_cost": _decimal_number(hourly_cost),
            "fully_costed_session_count": len(fully_costed),
            "estimated_cost": _decimal_number(estimated_cost) if fully_costed else MISSING,
            "estimated_cost_per_success": _decimal_number(estimated_cost / fully_costed_successes) if fully_costed_successes else MISSING,
            "excluded_session_ids": [session["session_id"] for session in sessions if session not in fully_costed],
        },
        "pricing_authorized": False,
        "external_action_authorized": False,
        "limitations": "Synthetic local evidence only; not customer, pricing, billing, or production evidence.",
    }


def summarize_session_economics_file(input_path: Path, output: Path) -> dict[str, Any]:
    result = summarize_session_economics(load_json(input_path))
    write_json(output, result)
    return result


def _invalid_session(index: int) -> dict[str, Any]:
    raise BetaSessionError(f"sessions[{index}] must be an object")


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BetaSessionError(f"{field} must be numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise BetaSessionError(f"{field} must be numeric") from exc
    if not number.is_finite():
        raise BetaSessionError(f"{field} must be finite")
    return number


def _decimal_number(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _ratio(numerator: int, denominator: int) -> float:
    return _decimal_number(Decimal(numerator) / Decimal(denominator))
