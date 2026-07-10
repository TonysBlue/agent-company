"""Fail-closed beta launch readiness package evaluation."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .brandkit import stable_sha256, write_json


BETA_LAUNCH_PACKAGE_SCHEMA_VERSION = "beta-launch-package/v1"
BETA_LAUNCH_READINESS_SCHEMA_VERSION = "beta-launch-readiness-evidence/v1"
REQUIRED_GATES = (
    "product_capability_evidence",
    "feedback_controls",
    "risk_review",
    "onboarding",
    "support_ownership",
    "observability",
    "rollback",
    "security_privacy_readiness",
    "unit_economics_evidence",
)
RESERVED_ACTIONS = {
    "contract_signature",
    "data_export",
    "external_publish",
    "external_spend",
    "legal_commitment",
    "pricing_change",
    "production_deploy",
}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?$")


class BetaLaunchReadinessError(ValueError):
    """Raised when a beta launch package is not reviewable."""


def evaluate_beta_launch_package_file(input_path: Path, output_path: Path | None = None) -> dict[str, Any]:
    package = _load_package(input_path)
    record = evaluate_beta_launch_package(package, input_path.parent)
    if output_path is not None:
        write_json(output_path, record)
    return record


def evaluate_beta_launch_package(package: dict[str, Any], base_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    if package.get("schema_version") != BETA_LAUNCH_PACKAGE_SCHEMA_VERSION:
        errors.append(f"schema_version must be {BETA_LAUNCH_PACKAGE_SCHEMA_VERSION}")
    package_id = _require_identifier(package.get("package_id"), "package_id", errors)
    product = _require_string(package, "product", errors)
    beta_version = _require_string(package, "beta_version", errors)
    if beta_version and not VERSION_PATTERN.fullmatch(beta_version):
        errors.append("beta_version must use MAJOR.MINOR.PATCH with optional prerelease suffix")

    gates_input = package.get("gates")
    gate_results: list[dict[str, Any]] = []
    if not isinstance(gates_input, dict):
        errors.append("gates must be an object")
    else:
        unknown = sorted(set(gates_input) - set(REQUIRED_GATES))
        if unknown:
            errors.append(f"gates contains unsupported keys: {', '.join(unknown)}")
        for gate in REQUIRED_GATES:
            value = gates_input.get(gate)
            if not isinstance(value, dict):
                errors.append(f"gates.{gate} must be an object")
                continue
            gate_results.append(_evaluate_gate(gate, value, base_dir, errors))

    reserved_input = package.get("reserved_action_approvals")
    reserved_results: list[dict[str, Any]] = []
    if not isinstance(reserved_input, list) or not reserved_input:
        errors.append("reserved_action_approvals must be a non-empty list")
    else:
        seen_actions: set[str] = set()
        for index, approval in enumerate(reserved_input):
            if not isinstance(approval, dict):
                errors.append(f"reserved_action_approvals[{index}] must be an object")
                continue
            result = _evaluate_reserved_approval(index, approval, base_dir, errors)
            action_type = result.get("action_type")
            if isinstance(action_type, str):
                if action_type in seen_actions:
                    errors.append(f"reserved_action_approvals contains duplicate action_type: {action_type}")
                seen_actions.add(action_type)
                reserved_results.append(result)

    if errors:
        raise BetaLaunchReadinessError("; ".join(errors))

    approvals_complete = all(item["decision"] == "approved" for item in reserved_results)

    record: dict[str, Any] = {
        "schema_version": BETA_LAUNCH_READINESS_SCHEMA_VERSION,
        "package": {
            "id": package_id,
            "product": product,
            "beta_version": beta_version,
            "package_sha256": stable_sha256(package),
        },
        "status": "ready_for_chairman_review" if approvals_complete else "blocked_pending_chairman_approvals",
        "launch_authorized": False,
        "external_action_authorized": False,
        "authorization_statement": "This is internal readiness evidence only and never authorizes launch.",
        "gates": sorted(gate_results, key=lambda item: item["gate"]),
        "reserved_action_approvals": sorted(reserved_results, key=lambda item: item["action_type"]),
    }
    record["readiness_sha256"] = stable_sha256(record)
    return record


def _load_package(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BetaLaunchReadinessError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BetaLaunchReadinessError(f"{path}: top-level JSON value must be an object")
    return data


def _evaluate_gate(name: str, gate: dict[str, Any], base_dir: Path, errors: list[str]) -> dict[str, Any]:
    owner = _require_string(gate, "owner", errors, f"gates.{name}.owner")
    summary = _require_string(gate, "summary", errors, f"gates.{name}.summary")
    status = gate.get("status")
    if status != "pass":
        errors.append(f"gates.{name}.status must be pass")
    if gate.get("launch_authorized") is not False:
        errors.append(f"gates.{name}.launch_authorized must be false")
    artifacts = gate.get("artifacts")
    artifact_results = _evaluate_artifact_list(f"gates.{name}.artifacts", artifacts, base_dir, errors)
    return {
        "gate": name,
        "owner": owner,
        "status": status,
        "summary": summary,
        "artifact_count": len(artifact_results),
        "artifacts": artifact_results,
    }


def _evaluate_reserved_approval(
    index: int,
    approval: dict[str, Any],
    base_dir: Path,
    errors: list[str],
) -> dict[str, Any]:
    label = f"reserved_action_approvals[{index}]"
    action_type = approval.get("action_type")
    if action_type not in RESERVED_ACTIONS:
        errors.append(f"{label}.action_type must be one of {sorted(RESERVED_ACTIONS)}")
    approval_ref = _require_identifier(approval.get("approval_ref"), f"{label}.approval_ref", errors)
    decision = approval.get("decision")
    if decision not in {"pending", "approved", "denied"}:
        errors.append(f"{label}.decision must be pending, approved, or denied")
    decided_by = approval.get("decided_by")
    if decision == "pending":
        if decided_by is not None:
            errors.append(f"{label}.decided_by must be null while pending")
    elif decided_by != "Chairman":
        errors.append(f"{label}.decided_by must be Chairman for a decided approval")
    if approval.get("launch_authorized") is not False:
        errors.append(f"{label}.launch_authorized must be false")
    evidence = approval.get("evidence")
    evidence_result = _evaluate_artifact(f"{label}.evidence", evidence, base_dir, errors)
    return {
        "action_type": action_type,
        "approval_ref": approval_ref,
        "decision": decision,
        "decided_by": decided_by,
        "launch_authorized": False,
        "evidence": evidence_result,
    }


def _evaluate_artifact_list(label: str, artifacts: Any, base_dir: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not isinstance(artifacts, list) or not artifacts:
        errors.append(f"{label} must be a non-empty list")
        return []
    results: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, artifact in enumerate(artifacts):
        result = _evaluate_artifact(f"{label}[{index}]", artifact, base_dir, errors)
        path = result.get("path")
        if isinstance(path, str):
            if path in seen_paths:
                errors.append(f"{label} contains duplicate path: {path}")
            seen_paths.add(path)
            results.append(result)
    return results


def _evaluate_artifact(label: str, artifact: Any, base_dir: Path, errors: list[str]) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        errors.append(f"{label} must be an object")
        return {}
    path_value = _require_string(artifact, "path", errors, f"{label}.path")
    expected_sha = artifact.get("sha256")
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        errors.append(f"{label}.sha256 must be a lowercase SHA-256 hex digest")
    evidence_type = _require_string(artifact, "evidence_type", errors, f"{label}.evidence_type")
    description = _require_string(artifact, "description", errors, f"{label}.description")

    actual_sha: str | None = None
    size_bytes: int | None = None
    if path_value:
        try:
            resolved = _resolve_evidence_path(base_dir, path_value)
        except BetaLaunchReadinessError as exc:
            errors.append(f"{label}.path {exc}")
        else:
            if not resolved.is_file():
                errors.append(f"{label}.path missing evidence file: {path_value}")
            else:
                content = resolved.read_bytes()
                actual_sha = hashlib.sha256(content).hexdigest()
                size_bytes = len(content)
                if isinstance(expected_sha, str) and actual_sha != expected_sha:
                    errors.append(f"{label}.sha256 checksum mismatch for {path_value}")
    return {
        "path": path_value,
        "evidence_type": evidence_type,
        "description": description,
        "sha256": expected_sha,
        "verified_sha256": actual_sha,
        "size_bytes": size_bytes,
    }


def _resolve_evidence_path(base_dir: Path, path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        raise BetaLaunchReadinessError("must be relative")
    base = base_dir.resolve()
    resolved = (base / path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise BetaLaunchReadinessError("must not escape the package directory") from exc
    return resolved


def _require_string(data: dict[str, Any], key: str, errors: list[str], label: str | None = None) -> str | None:
    value = data.get(key)
    name = label or key
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{name} must be a non-empty string")
        return None
    return value.strip()


def _require_identifier(value: Any, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        errors.append(f"{label} must be a valid non-empty identifier")
        return None
    return value
