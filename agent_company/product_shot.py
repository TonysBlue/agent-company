"""Versioned product-shot workflow manifest validation and expansion."""

from __future__ import annotations

import re
from typing import Any

from .brandkit import PROVENANCE_SCHEMA_VERSION, stable_sha256, validate_provenance


PRODUCT_SHOT_WORKFLOW_SCHEMA_VERSION = "product-shot-workflow/v1"
PRODUCT_SHOT_MANIFEST_SCHEMA_VERSION = "product-shot-manifest/v1"
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
CONTROL_VALUE_TYPES = (str, int, float, bool)


class ProductShotWorkflowError(ValueError):
    """Raised when a product-shot workflow cannot be accepted safely."""


def validate_workflow_input(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != PRODUCT_SHOT_WORKFLOW_SCHEMA_VERSION:
        errors.append(f"schema_version must be {PRODUCT_SHOT_WORKFLOW_SCHEMA_VERSION}")
    workflow = data.get("workflow")
    if not isinstance(workflow, dict):
        errors.append("workflow must be an object")
    else:
        _require_identifier(workflow.get("id"), "workflow.id", errors)
        version = _require_string(workflow, "version", errors, "workflow.version")
        if version and not VERSION.fullmatch(version):
            errors.append("workflow.version must use MAJOR.MINOR.PATCH")
        _validate_controls(workflow.get("controls"), "workflow.controls", errors)
        _validate_stages(workflow.get("stages"), errors)
        _validate_acceptance_checks(workflow.get("acceptance_checks"), errors)

    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) < 3:
        errors.append("scenarios must contain at least three scenario objects")
    else:
        scenario_ids: list[str] = []
        source_ids: list[str] = []
        for index, scenario in enumerate(scenarios):
            label = f"scenarios[{index}]"
            if not isinstance(scenario, dict):
                errors.append(f"{label} must be an object")
                continue
            scenario_id = _require_identifier(scenario.get("id"), f"{label}.id", errors)
            if scenario_id:
                scenario_ids.append(scenario_id)
            _require_string(scenario, "shot_type", errors, f"{label}.shot_type")
            _validate_controls(scenario.get("controls"), f"{label}.controls", errors)
            source = scenario.get("source")
            if not isinstance(source, dict):
                errors.append(f"{label}.source must be an object")
                continue
            source_id = _require_identifier(source.get("id"), f"{label}.source.id", errors)
            if source_id:
                source_ids.append(source_id)
            provenance = source.get("provenance")
            provenance_label = f"{label}.source.provenance"
            if not isinstance(provenance, dict):
                errors.append(f"{provenance_label} must be an object")
                continue
            errors.extend(validate_provenance(provenance, provenance_label))
            if source_id and provenance.get("source_id") != source_id:
                errors.append(f"{provenance_label}.source_id must match {label}.source.id")
            if provenance.get("review_decision") != "approved_internal":
                errors.append(f"{provenance_label}.review_decision must be approved_internal before workflow manifest generation")
        _reject_duplicate_values(scenario_ids, "scenarios[].id", errors)
        _reject_duplicate_values(source_ids, "scenarios[].source.id", errors)
    return errors


def build_product_shot_manifest(data: dict[str, Any]) -> dict[str, Any]:
    errors = validate_workflow_input(data)
    if errors:
        raise ProductShotWorkflowError("; ".join(errors))

    workflow = data["workflow"]
    workflow_fingerprint = stable_sha256(workflow)
    scenarios = []
    for scenario in sorted(data["scenarios"], key=lambda item: item["id"]):
        source = scenario["source"]
        identity = {
            "scenario_id": scenario["id"],
            "source_id": source["id"],
            "workflow_fingerprint": workflow_fingerprint,
        }
        manifest_id = stable_sha256(identity)[:16]
        controls = dict(sorted({**workflow["controls"], **scenario["controls"]}.items()))
        scenarios.append(
            {
                "id": manifest_id,
                "scenario_id": scenario["id"],
                "source_id": source["id"],
                "shot_type": scenario["shot_type"],
                "controls": controls,
                "ordered_stages": list(workflow["stages"]),
                "acceptance_checks": list(workflow["acceptance_checks"]),
                "provenance": {
                    "schema_version": PROVENANCE_SCHEMA_VERSION,
                    "source_id": manifest_id,
                    "parent_lineage": [source["id"]],
                    "source_category": "derived_product_shot_workflow",
                    "origin": "pixweave_product_shot_workflow",
                    "rights_basis": source["provenance"]["rights_basis"],
                    "rights_evidence_ref": source["provenance"]["rights_evidence_ref"],
                    "likeness_status": source["provenance"]["likeness_status"],
                    "trademark_review_status": source["provenance"]["trademark_review_status"],
                    "data_classification": source["provenance"]["data_classification"],
                    "retention_class": source["provenance"]["retention_class"],
                    "policy_flags": list(source["provenance"]["policy_flags"]),
                    "reviewer_ref": source["provenance"]["reviewer_ref"],
                    "review_decision": "approved_internal",
                },
            }
        )
    manifest = {
        "schema_version": PRODUCT_SHOT_MANIFEST_SCHEMA_VERSION,
        "workflow": {
            "id": workflow["id"],
            "version": workflow["version"],
            "fingerprint_sha256": workflow_fingerprint,
        },
        "capability_disclaimer": (
            "This manifest records controlled product-shot workflow metadata only; "
            "it does not measure, generate, or claim actual image quality."
        ),
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
    }
    manifest["manifest_sha256"] = stable_sha256(manifest)
    return manifest


def _validate_controls(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or not value:
        errors.append(f"{label} must be a non-empty object")
        return
    for key, item in value.items():
        if not isinstance(key, str) or not IDENTIFIER.fullmatch(key):
            errors.append(f"{label} keys must be valid identifiers")
        elif not isinstance(item, CONTROL_VALUE_TYPES) or (isinstance(item, str) and not item.strip()):
            errors.append(f"{label}.{key} must be a non-empty scalar value")


def _validate_stages(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append("workflow.stages must be a non-empty ordered list")
        return
    stage_ids: list[str] = []
    for index, stage in enumerate(value):
        label = f"workflow.stages[{index}]"
        if not isinstance(stage, dict):
            errors.append(f"{label} must be an object")
            continue
        stage_id = _require_identifier(stage.get("id"), f"{label}.id", errors)
        if stage_id:
            stage_ids.append(stage_id)
        _require_string(stage, "name", errors, f"{label}.name")
        _require_string(stage, "purpose", errors, f"{label}.purpose")
    _reject_duplicate_values(stage_ids, "workflow.stages[].id", errors)


def _validate_acceptance_checks(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append("workflow.acceptance_checks must be a non-empty list")
        return
    check_ids: list[str] = []
    for index, check in enumerate(value):
        label = f"workflow.acceptance_checks[{index}]"
        if not isinstance(check, dict):
            errors.append(f"{label} must be an object")
            continue
        check_id = _require_identifier(check.get("id"), f"{label}.id", errors)
        if check_id:
            check_ids.append(check_id)
        _require_string(check, "description", errors, f"{label}.description")
        severity = check.get("severity")
        if severity not in {"info", "warn", "stop"}:
            errors.append(f"{label}.severity must be one of info, warn, stop")
    _reject_duplicate_values(check_ids, "workflow.acceptance_checks[].id", errors)


def _require_string(data: dict[str, Any], key: str, errors: list[str], label: str) -> str | None:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return None
    return value


def _require_identifier(value: Any, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        errors.append(f"{label} must be a valid non-empty identifier")
        return None
    return value


def _reject_duplicate_values(values: list[str], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        errors.append(f"{label} contains duplicate values: {', '.join(duplicates)}")
