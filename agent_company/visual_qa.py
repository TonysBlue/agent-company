"""Deterministic visual QA scorecard from explicit human/tool observations."""

from __future__ import annotations

import re
from typing import Any

from .brandkit import stable_sha256


VISUAL_QA_INPUT_SCHEMA_VERSION = "visual-qa-observations/v1"
VISUAL_QA_SCORECARD_SCHEMA_VERSION = "visual-qa-scorecard/v1"
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MEASUREMENT_TYPES = ("edit_fidelity", "brand_consistency")
PASS_THRESHOLD = 85.0
FAIL_THRESHOLD = 70.0
STOP_THRESHOLD = 50.0
STOP_SEVERITIES = {"stop", "critical"}


class VisualQAScorecardError(ValueError):
    """Raised when a visual QA scorecard input is invalid."""


def validate_scorecard_input(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != VISUAL_QA_INPUT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {VISUAL_QA_INPUT_SCHEMA_VERSION}")
    subject = data.get("subject")
    if not isinstance(subject, dict):
        errors.append("subject must be an object")
    else:
        _require_string(subject, "id", errors, "subject.id")
        version = _require_string(subject, "version", errors, "subject.version")
        if version and not VERSION.fullmatch(version):
            errors.append("subject.version must use MAJOR.MINOR.PATCH")
    observations = data.get("observations")
    if not isinstance(observations, list) or not observations:
        errors.append("observations must be a non-empty list")
    else:
        seen_types: set[str] = set()
        for index, observation in enumerate(observations):
            label = f"observations[{index}]"
            if not isinstance(observation, dict):
                errors.append(f"{label} must be an object")
                continue
            measurement_type = observation.get("type")
            if measurement_type not in MEASUREMENT_TYPES:
                errors.append(f"{label}.type must be one of {', '.join(MEASUREMENT_TYPES)}")
            else:
                seen_types.add(measurement_type)
            value = observation.get("value")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 100:
                errors.append(f"{label}.value must be a number from 0 to 100")
            _require_string(observation, "method", errors, f"{label}.method")
            _require_string(observation, "observer_ref", errors, f"{label}.observer_ref")
            severity = observation.get("severity", "normal")
            if severity not in {"normal", "warn", "stop", "critical"}:
                errors.append(f"{label}.severity must be one of normal, warn, stop, critical")
        for measurement_type in MEASUREMENT_TYPES:
            if measurement_type not in seen_types:
                errors.append(f"observations must include {measurement_type}")
    return errors


def build_scorecard(data: dict[str, Any]) -> dict[str, Any]:
    errors = validate_scorecard_input(data)
    if errors:
        raise VisualQAScorecardError("; ".join(errors))

    observations = sorted(
        data["observations"],
        key=lambda item: (item["type"], item["observer_ref"], item["method"], item["value"], item.get("severity", "normal")),
    )
    grouped = {measurement_type: [] for measurement_type in MEASUREMENT_TYPES}
    stop_reasons: list[str] = []
    for observation in observations:
        grouped[observation["type"]].append(float(observation["value"]))
        if observation.get("severity", "normal") in STOP_SEVERITIES:
            stop_reasons.append(f"{observation['type']} severity={observation.get('severity')}")

    edit_fidelity = _average(grouped["edit_fidelity"])
    brand_consistency = _average(grouped["brand_consistency"])
    composite_score = round((edit_fidelity * 0.6) + (brand_consistency * 0.4), 4)
    if edit_fidelity < STOP_THRESHOLD:
        stop_reasons.append("edit_fidelity below stop threshold")
    if brand_consistency < STOP_THRESHOLD:
        stop_reasons.append("brand_consistency below stop threshold")

    if stop_reasons:
        decision = "stop"
    elif composite_score >= PASS_THRESHOLD and edit_fidelity >= FAIL_THRESHOLD and brand_consistency >= FAIL_THRESHOLD:
        decision = "pass"
    else:
        decision = "fail"

    scorecard = {
        "schema_version": VISUAL_QA_SCORECARD_SCHEMA_VERSION,
        "subject": dict(sorted(data["subject"].items())),
        "scoring": {
            "inputs": MEASUREMENT_TYPES,
            "weights": {"edit_fidelity": 0.6, "brand_consistency": 0.4},
            "pass_threshold": PASS_THRESHOLD,
            "fail_threshold": FAIL_THRESHOLD,
            "stop_threshold": STOP_THRESHOLD,
            "stop_severities": sorted(STOP_SEVERITIES),
        },
        "capability_disclaimer": (
            "This scorecard only scores explicit measured observations supplied in the input; "
            "it does not measure or claim actual image quality."
        ),
        "measurements": {
            "edit_fidelity": edit_fidelity,
            "brand_consistency": brand_consistency,
            "composite_score": composite_score,
        },
        "decision": decision,
        "stop_reasons": stop_reasons,
        "observations": observations,
    }
    scorecard["scorecard_sha256"] = stable_sha256(scorecard)
    return scorecard


def _average(values: list[float]) -> float:
    return round(sum(values) / len(values), 4)


def _require_string(data: dict[str, Any], key: str, errors: list[str], label: str) -> str | None:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return None
    return value
