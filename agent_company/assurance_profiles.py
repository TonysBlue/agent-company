"""Strict executable assurance profile contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


class ProfileError(ValueError):
    """An assurance profile violates its executable contract."""


PRODUCT_KEYS = {
    "schema_version", "scenario_id", "datasets", "hard_gates", "pairwise",
    "comparator", "statistics", "holdout",
}
CONTROL_KEYS = {
    "schema_version", "mechanism_id", "states", "invariants", "failure_scenarios",
    "fitness_checks", "evidence_semantics", "slo",
}


def _nonempty_strings(values: Any, name: str) -> None:
    if not isinstance(values, list) or not values or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ProfileError(f"{name} must contain non-empty strings")


def _result(profile: str, payload: dict[str, Any]) -> dict[str, str]:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {"profile": profile, "sha256": hashlib.sha256(canonical.encode("ascii")).hexdigest()}


def validate_profile(payload: dict[str, Any]) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ProfileError("profile must be an object")
    schema = payload.get("schema_version")
    if schema == "product-competitive-profile/v1":
        return _validate_product(payload)
    if schema == "control-plane-reliability-profile/v1":
        return _validate_control(payload)
    raise ProfileError("unsupported assurance profile schema")


def _validate_product(payload: dict[str, Any]) -> dict[str, str]:
    if set(payload) != PRODUCT_KEYS:
        raise ProfileError("product profile has unknown or missing fields")
    if not isinstance(payload["scenario_id"], str) or not payload["scenario_id"].strip():
        raise ProfileError("scenario_id must be non-empty")
    datasets = payload["datasets"]
    required_partitions = {"development", "regression", "hidden_holdout", "adversarial"}
    if not isinstance(datasets, dict) or set(datasets) != required_partitions or any(
        not isinstance(value, str) or not value.strip() for value in datasets.values()
    ):
        raise ProfileError("product profile requires all dataset partitions")
    _nonempty_strings(payload["hard_gates"], "hard_gates")
    pairwise = payload["pairwise"]
    if not isinstance(pairwise, dict) or set(pairwise) != {"blinded", "balanced", "minimum_raters"}:
        raise ProfileError("invalid pairwise protocol")
    if pairwise["blinded"] is not True or pairwise["balanced"] is not True or type(pairwise["minimum_raters"]) is not int or pairwise["minimum_raters"] < 2:
        raise ProfileError("pairwise evaluation must be blinded, balanced, and independently rated")
    comparator = payload["comparator"]
    if not isinstance(comparator, dict) or set(comparator) != {"name", "version", "attempt_budget"}:
        raise ProfileError("invalid comparator contract")
    if any(not isinstance(comparator[key], str) or not comparator[key].strip() for key in ("name", "version")):
        raise ProfileError("comparator identity must be frozen")
    if type(comparator["attempt_budget"]) is not int or comparator["attempt_budget"] < 1:
        raise ProfileError("comparator attempt budget must be positive")
    statistics = payload["statistics"]
    if not isinstance(statistics, dict) or set(statistics) != {
        "primary_estimand", "unit", "minimum_practical_advantage", "uncertainty"
    }:
        raise ProfileError("invalid statistical decision contract")
    margin = statistics["minimum_practical_advantage"]
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or margin <= 0:
        raise ProfileError("minimum practical advantage must be positive")
    holdout = payload["holdout"]
    if not isinstance(holdout, dict) or set(holdout) != {"custodian_principal", "max_attempts", "canary_required"}:
        raise ProfileError("invalid holdout custody contract")
    if not isinstance(holdout["custodian_principal"], str) or not holdout["custodian_principal"].strip():
        raise ProfileError("holdout custodian is required")
    if type(holdout["max_attempts"]) is not int or holdout["max_attempts"] < 1 or holdout["canary_required"] is not True:
        raise ProfileError("holdout attempts and canary controls are required")
    return _result("product-competitive", payload)


def _validate_control(payload: dict[str, Any]) -> dict[str, str]:
    if set(payload) != CONTROL_KEYS:
        raise ProfileError("control profile has unknown or missing fields")
    if not isinstance(payload["mechanism_id"], str) or not payload["mechanism_id"].strip():
        raise ProfileError("mechanism_id must be non-empty")
    for field in ("states", "invariants", "failure_scenarios", "fitness_checks", "evidence_semantics"):
        _nonempty_strings(payload[field], field)
    semantics = set(payload["evidence_semantics"])
    if not semantics <= {"formal_proof", "model_check", "property_test", "fault_injection", "observed_sli"}:
        raise ProfileError("unsupported reliability evidence semantics")
    slo = payload["slo"]
    if not isinstance(slo, dict) or not slo or any(not isinstance(k, str) or not isinstance(v, (int, float)) for k, v in slo.items()):
        raise ProfileError("slo must be a non-empty numeric mapping")
    return _result("control-plane-reliability", payload)
