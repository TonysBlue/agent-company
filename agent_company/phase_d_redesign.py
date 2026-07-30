"""Fail-closed tooling for the corrected Phase D D1/D2 design."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import math
import re
import shutil
import sqlite3
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any


class PhaseDRedesignError(ValueError):
    """Raised when corrected Phase D inputs or evidence violate their freeze."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhaseDRedesignError(f"cannot load JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PhaseDRedesignError(f"JSON input must be an object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_independent_approval(
    root: Path,
    freeze_path: Path,
    approval_path: Path,
    documents: dict[str, dict[str, Any]],
    author_principals: set[str],
) -> dict[str, Any]:
    approval = load_json(approval_path)
    if approval.get("schema_version") != "phase-d-redesign-independent-approval/v2":
        raise PhaseDRedesignError("independent approval schema_version is invalid")
    if approval.get("decision") != "approve":
        raise PhaseDRedesignError("independent approval decision is not approve")
    reviewer = approval.get("reviewer_principal")
    if not isinstance(reviewer, str) or not reviewer or reviewer in author_principals:
        raise PhaseDRedesignError("independent approval reviewer is not independent")
    if approval.get("reviewed_freeze_sha256") != sha256_file(freeze_path):
        raise PhaseDRedesignError("independent approval does not bind the corrected freeze")
    expected_hashes = {
        str(value["path"]): str(value["sha256"])
        for value in documents.values()
    }
    if approval.get("reviewed_document_sha256") != expected_hashes:
        raise PhaseDRedesignError("independent approval does not bind every corrected document")
    unresolved = approval.get("unresolved_findings")
    if not isinstance(unresolved, list) or any(
        isinstance(item, dict) and str(item.get("severity", "")).lower() in {"critical", "high"}
        for item in unresolved
    ):
        raise PhaseDRedesignError("independent approval has unresolved Critical/High findings")
    return approval


def _verify_corrected_freeze_v2(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool = False,
) -> dict[str, Any]:
    """Verify every corrected input and keep execution blocked absent independent approval."""
    freeze = load_json(freeze_path)
    if freeze.get("schema_version") != "phase-d-redesign-freeze/v2":
        raise PhaseDRedesignError("corrected freeze schema_version is invalid")
    entries = freeze.get("documents")
    if not isinstance(entries, list) or not entries:
        raise PhaseDRedesignError("corrected freeze must bind documents")
    documents: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PhaseDRedesignError("corrected freeze document entry must be an object")
        kind = entry.get("kind")
        relative = entry.get("path")
        expected = entry.get("sha256")
        if not all(isinstance(value, str) and value for value in (kind, relative, expected)):
            raise PhaseDRedesignError("corrected freeze document entry is invalid")
        if kind in documents:
            raise PhaseDRedesignError(f"corrected freeze repeats document kind: {kind}")
        path = root / str(relative)
        if sha256_file(path) != expected:
            raise PhaseDRedesignError(f"corrected freeze hash mismatch: {kind}")
        documents[str(kind)] = {**entry, "content": load_json(path)}

    required = {
        "independent_findings",
        "ceo_start_proposal",
        "supersession_record",
        "d1_contract",
        "d1_scenario_bank",
        "d2_contract",
        "d2_mutation_bank",
    }
    if not required.issubset(documents):
        missing = ", ".join(sorted(required - set(documents)))
        raise PhaseDRedesignError(f"corrected freeze is missing required documents: {missing}")

    findings = documents["independent_findings"]["content"]
    proposal = documents["ceo_start_proposal"]["content"]
    d1_contract = documents["d1_contract"]["content"]
    d2_contract = documents["d2_contract"]["content"]
    if findings.get("reviewed_head") != "6626411" or findings.get("prior_treatment_conclusions_invalid") is not True:
        raise PhaseDRedesignError("independent findings do not invalidate the reviewed D1/D2 conclusions")
    if proposal.get("current_decision") != "do_not_start" or proposal.get("effective_authorization") is not False:
        raise PhaseDRedesignError("CEO start proposal must remain non-authorizing before review")
    for pilot, contract in (("D1", d1_contract), ("D2", d2_contract)):
        if contract.get("status") != "blocked_pending_independent_approval":
            raise PhaseDRedesignError(f"{pilot} corrected contract is not blocked")
        if contract.get("treatment_execution_authorized") is not False:
            raise PhaseDRedesignError(f"{pilot} corrected contract incorrectly authorizes execution")

    gate = freeze.get("execution_gate")
    if not isinstance(gate, dict) or not isinstance(gate.get("independent_approval_path"), str):
        raise PhaseDRedesignError("corrected freeze execution gate is invalid")
    approval_path = root / str(gate["independent_approval_path"])
    approval: dict[str, Any] | None = None
    if approval_path.exists():
        authors = freeze.get("author_principals")
        if not isinstance(authors, list) or not all(isinstance(item, str) for item in authors):
            raise PhaseDRedesignError("corrected freeze author principals are invalid")
        approval = _validate_independent_approval(
            root,
            freeze_path,
            approval_path,
            documents,
            set(authors),
        )
    execution_authorized = approval is not None
    if require_execution_approval and not execution_authorized:
        raise PhaseDRedesignError("corrected D1/D2 execution requires independent approval")
    return {
        "status": "independently_approved" if execution_authorized else "blocked_pending_independent_approval",
        "execution_authorized": execution_authorized,
        "documents": {kind: entry["content"] for kind, entry in documents.items()},
        "document_sha256": {str(entry["path"]): str(entry["sha256"]) for entry in documents.values()},
        "approval": approval,
    }


def _signature_payload(record: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key != "authentication"}
    return canonical_json(unsigned).encode("ascii")


def sign_governance_record(
    record: dict[str, Any],
    *,
    principal_id: str,
    key_id: str,
    credential: bytes,
) -> dict[str, Any]:
    """Authenticate a governance record without placing the credential in the record."""
    if not credential:
        raise PhaseDRedesignError("governance signing credential is empty")
    signed = copy.deepcopy(record)
    signed["authentication"] = {
        "algorithm": "hmac-sha256",
        "principal_id": principal_id,
        "key_id": key_id,
        "signature": hmac.new(credential, _signature_payload(signed), hashlib.sha256).hexdigest(),
    }
    return signed


def _verify_governance_signature(
    record: dict[str, Any],
    identity: dict[str, Any],
    credentials: dict[str, bytes],
) -> None:
    authentication = record.get("authentication")
    if not isinstance(authentication, dict):
        raise PhaseDRedesignError("governance record lacks an authenticated signature")
    principal_id = identity.get("principal_id")
    key_id = identity.get("key_id")
    if (
        authentication.get("algorithm") != "hmac-sha256"
        or authentication.get("principal_id") != principal_id
        or authentication.get("key_id") != key_id
    ):
        raise PhaseDRedesignError("governance signature identity does not match the freeze")
    credential = credentials.get(str(key_id))
    if not isinstance(credential, bytes) or not credential:
        raise PhaseDRedesignError("trusted credential is unavailable for governance authentication")
    signature = authentication.get("signature")
    expected = hmac.new(credential, _signature_payload(record), hashlib.sha256).hexdigest()
    if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
        raise PhaseDRedesignError("governance signature is forged or not authentic")


def _contains_unresolved_material_finding(value: object) -> bool:
    if isinstance(value, str):
        return re.search(r"(?i)(?:^|\W)(critical|high)(?:\W|$)", value) is not None
    if isinstance(value, dict):
        severity = value.get("severity")
        if isinstance(severity, str) and severity.strip().lower() in {"critical", "high"}:
            return True
        return any(
            _contains_unresolved_material_finding(item)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_unresolved_material_finding(item) for item in value)
    return False


def _parse_signed_at(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise PhaseDRedesignError(f"{label} signed_at is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise PhaseDRedesignError(f"{label} signed_at is invalid") from exc
    if parsed.tzinfo is None:
        raise PhaseDRedesignError(f"{label} signed_at must include a timezone")
    return parsed


def evaluate_v3_authorization(
    freeze: dict[str, Any],
    freeze_sha256: str,
    document_sha256: dict[str, str],
    binding_sha256: dict[str, str],
    approval: dict[str, Any] | None,
    ceo_decision: dict[str, Any] | None,
    credentials: dict[str, bytes],
    *,
    require_execution_authorization: bool = False,
) -> dict[str, Any]:
    """Require two authenticated, ordered decisions before corrected treatment execution."""
    gate = freeze.get("execution_gate")
    if not isinstance(gate, dict):
        raise PhaseDRedesignError("v3 execution gate is invalid")
    reviewer_identity = gate.get("reviewer_identity")
    ceo_identity = gate.get("ceo_identity")
    if not isinstance(reviewer_identity, dict) or not isinstance(ceo_identity, dict):
        raise PhaseDRedesignError("v3 execution identities are not frozen")

    if approval is None:
        if require_execution_authorization:
            raise PhaseDRedesignError("corrected D1/D2 execution requires authenticated independent approval")
        return {"status": "blocked_pending_authenticated_independent_approval", "execution_authorized": False}

    if approval.get("schema_version") != "phase-d-redesign-independent-approval/v3":
        raise PhaseDRedesignError("independent approval schema_version is invalid")
    _verify_governance_signature(approval, reviewer_identity, credentials)
    if approval.get("decision") != "approve":
        raise PhaseDRedesignError("independent approval decision is not approve")
    if (
        approval.get("reviewer_principal") != reviewer_identity.get("principal_id")
        or approval.get("reviewer_role") != reviewer_identity.get("role")
    ):
        raise PhaseDRedesignError("independent approval reviewer identity is not freeze-bound")
    author_principals = freeze.get("author_principals", [])
    if not isinstance(author_principals, list):
        raise PhaseDRedesignError("v3 freeze author principals are invalid")
    if approval.get("reviewer_principal") in author_principals:
        raise PhaseDRedesignError("independent approval reviewer is not independent")
    if approval.get("reviewer_principal") == ceo_identity.get("principal_id"):
        raise PhaseDRedesignError("independent approval reviewer is not independent from the CEO")
    if approval.get("reviewed_freeze_sha256") != freeze_sha256:
        raise PhaseDRedesignError("independent approval does not bind the v3 freeze")
    if approval.get("reviewed_source_revision") != freeze.get("source_revision"):
        raise PhaseDRedesignError("independent approval does not bind the frozen source revision")
    if approval.get("reviewed_document_sha256") != document_sha256:
        raise PhaseDRedesignError("independent approval does not bind every v3 document")
    if approval.get("reviewed_binding_sha256") != binding_sha256:
        raise PhaseDRedesignError("independent approval does not bind every executable and evidence hash")
    unresolved = approval.get("unresolved_findings")
    if _contains_unresolved_material_finding(unresolved):
        raise PhaseDRedesignError("independent approval has unresolved Critical/High findings")
    if not isinstance(unresolved, list):
        raise PhaseDRedesignError("independent approval unresolved findings must be a list")
    approval_time = _parse_signed_at(approval.get("signed_at"), "independent approval")

    if ceo_decision is None:
        if require_execution_authorization:
            raise PhaseDRedesignError("corrected D1/D2 execution requires a separate signed CEO start decision")
        return {"status": "blocked_pending_signed_ceo_start_decision", "execution_authorized": False}
    if ceo_decision.get("schema_version") != "phase-d-redesign-ceo-start-decision/v3":
        raise PhaseDRedesignError("CEO start decision schema_version is invalid")
    _verify_governance_signature(ceo_decision, ceo_identity, credentials)
    if (
        ceo_decision.get("ceo_principal") != ceo_identity.get("principal_id")
        or ceo_decision.get("ceo_role") != ceo_identity.get("role")
    ):
        raise PhaseDRedesignError("CEO start decision identity is not freeze-bound")
    if ceo_decision.get("decision") != "start" or ceo_decision.get("effective_authorization") is not True:
        if require_execution_authorization:
            raise PhaseDRedesignError("CEO decision does not provide an effective post-approval start")
        return {"status": "blocked_by_ceo_do_not_start", "execution_authorized": False}
    if ceo_decision.get("approved_freeze_sha256") != freeze_sha256:
        raise PhaseDRedesignError("CEO start decision does not bind the v3 freeze")
    approval_hash = sha256_bytes(canonical_json(approval).encode("ascii"))
    if ceo_decision.get("approved_independent_approval_sha256") != approval_hash:
        raise PhaseDRedesignError("CEO start decision does not bind the authenticated approval")
    if ceo_decision.get("approved_source_revision") != freeze.get("source_revision"):
        raise PhaseDRedesignError("CEO start decision does not bind the frozen source revision")
    ceo_time = _parse_signed_at(ceo_decision.get("signed_at"), "CEO start decision")
    if ceo_time <= approval_time:
        raise PhaseDRedesignError("CEO start decision must be signed after independent approval")
    return {"status": "authorized_by_two_signed_decisions", "execution_authorized": True}


def _verify_corrected_freeze_v3(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool,
    governance_credentials: dict[str, bytes] | None,
) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if freeze.get("status") != "blocked_pending_authenticated_approval_and_ceo_start":
        raise PhaseDRedesignError("v3 corrected freeze must preserve the blocked state")
    source_revision = freeze.get("source_revision")
    if not isinstance(source_revision, dict):
        raise PhaseDRedesignError("v3 freeze source revision is invalid")
    commit = source_revision.get("commit")
    tree = source_revision.get("tree")
    if not all(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{40}", item) for item in (commit, tree)):
        raise PhaseDRedesignError("v3 freeze source commit/tree is invalid")
    if (root / ".git").exists():
        completed = subprocess.run(
            ["git", "rev-parse", f"{commit}^{{tree}}"],
            cwd=root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode != 0 or completed.stdout.strip() != tree:
            raise PhaseDRedesignError("v3 freeze source commit/tree binding is not resolvable")

    documents: dict[str, dict[str, Any]] = {}
    for entry in freeze.get("documents", []):
        if not isinstance(entry, dict):
            raise PhaseDRedesignError("v3 freeze document entry must be an object")
        kind, relative, expected = entry.get("kind"), entry.get("path"), entry.get("sha256")
        if not all(isinstance(item, str) and item for item in (kind, relative, expected)):
            raise PhaseDRedesignError("v3 freeze document entry is invalid")
        if kind in documents:
            raise PhaseDRedesignError(f"v3 freeze repeats document kind: {kind}")
        path = root / relative
        if sha256_file(path) != expected:
            raise PhaseDRedesignError(f"v3 freeze document hash mismatch: {kind}")
        documents[str(kind)] = {**entry, "content": load_json(path)}
    required_documents = {
        "independent_findings",
        "ceo_start_proposal",
        "supersession_record",
        "d1_contract",
        "d1_scenario_bank",
        "d2_contract",
        "d2_mutation_bank",
    }
    if set(documents) != required_documents:
        raise PhaseDRedesignError("v3 freeze does not bind the exact required document set")

    bindings: dict[str, str] = {}
    binding_kinds: set[str] = set()
    bound_paths = [str(entry["path"]) for entry in freeze.get("documents", []) if isinstance(entry, dict)]
    entries = freeze.get("bindings")
    if not isinstance(entries, list) or not entries:
        raise PhaseDRedesignError("v3 freeze must bind executable and verification artifacts")
    for entry in entries:
        if not isinstance(entry, dict):
            raise PhaseDRedesignError("v3 freeze binding entry must be an object")
        kind, relative, expected = entry.get("kind"), entry.get("path"), entry.get("sha256")
        if not all(isinstance(item, str) and item for item in (kind, relative, expected)):
            raise PhaseDRedesignError("v3 freeze binding entry is invalid")
        if relative in bindings:
            raise PhaseDRedesignError(f"v3 freeze repeats bound path: {relative}")
        if kind in binding_kinds:
            raise PhaseDRedesignError(f"v3 freeze repeats binding kind: {kind}")
        path = root / relative
        if sha256_file(path) != expected:
            raise PhaseDRedesignError(f"v3 freeze binding hash mismatch: {relative}")
        bindings[str(relative)] = str(expected)
        binding_kinds.add(str(kind))
        bound_paths.append(str(relative))
    required_binding_kinds = {
        "implementation",
        "runner",
        "test",
        "dry_run_evidence",
        "regression_evidence",
        "red_evidence",
        "evidence_manifest",
    }
    if binding_kinds != required_binding_kinds:
        raise PhaseDRedesignError("v3 freeze must bind the exact executable and verification classes")
    expected_binding_paths = {
        "implementation": "agent_company/phase_d_redesign.py",
        "runner": "scripts/run_phase_d_redesign_v3_dry_run.py",
        "test": "tests/test_phase_d_redesign_v3.py",
        "dry_run_evidence": "evidence/phase-d/redesign-v3/dry-run-result.json",
        "regression_evidence": "evidence/phase-d/redesign-v3/regression-results.txt",
        "red_evidence": "evidence/phase-d/redesign-v3/red-probes.txt",
        "evidence_manifest": "evidence/phase-d/redesign-v3/evidence-manifest.json",
    }
    observed_binding_paths = {
        str(entry["kind"]): str(entry["path"])
        for entry in entries
        if isinstance(entry, dict)
    }
    if observed_binding_paths != expected_binding_paths:
        raise PhaseDRedesignError("v3 freeze binding classes do not use the exact required paths")

    proposal = documents["ceo_start_proposal"]["content"]
    if proposal.get("current_decision") != "do_not_start" or proposal.get("effective_authorization") is not False:
        raise PhaseDRedesignError("v3 CEO proposal must remain do_not_start and non-authorizing")
    for pilot in ("d1_contract", "d2_contract"):
        contract = documents[pilot]["content"]
        if (
            contract.get("status") != "blocked_pending_authenticated_approval_and_ceo_start"
            or contract.get("treatment_execution_authorized") is not False
        ):
            raise PhaseDRedesignError(f"{pilot} does not preserve the blocked state")

    gate = freeze.get("execution_gate")
    if not isinstance(gate, dict):
        raise PhaseDRedesignError("v3 execution gate is invalid")
    approval_relative = gate.get("independent_approval_path")
    ceo_relative = gate.get("ceo_start_decision_path")
    if not isinstance(approval_relative, str) or not isinstance(ceo_relative, str):
        raise PhaseDRedesignError("v3 execution decision paths are invalid")
    approval = load_json(root / approval_relative) if (root / approval_relative).exists() else None
    ceo_decision = load_json(root / ceo_relative) if (root / ceo_relative).exists() else None
    authorization = evaluate_v3_authorization(
        freeze,
        sha256_file(freeze_path),
        {str(entry["path"]): str(entry["sha256"]) for entry in documents.values()},
        bindings,
        approval,
        ceo_decision,
        governance_credentials or {},
        require_execution_authorization=require_execution_approval,
    )
    return {
        **authorization,
        "documents": {kind: entry["content"] for kind, entry in documents.items()},
        "document_sha256": {str(entry["path"]): str(entry["sha256"]) for entry in documents.values()},
        "binding_sha256": bindings,
        "approval": approval,
        "ceo_start_decision": ceo_decision,
        "bound_paths": sorted(set(bound_paths + [freeze_path.relative_to(root).as_posix()])),
    }


def verify_corrected_freeze(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool = False,
    governance_credentials: dict[str, bytes] | None = None,
) -> dict[str, Any]:
    schema_version = load_json(freeze_path).get("schema_version")
    if schema_version == "phase-d-redesign-freeze/v2":
        return _verify_corrected_freeze_v2(
            root, freeze_path, require_execution_approval=require_execution_approval
        )
    if schema_version == "phase-d-redesign-freeze/v3":
        return _verify_corrected_freeze_v3(
            root,
            freeze_path,
            require_execution_approval=require_execution_approval,
            governance_credentials=governance_credentials,
        )
    raise PhaseDRedesignError("corrected freeze schema_version is invalid")


def validate_scenario_bank(root: Path, bank_path: Path) -> dict[str, Any]:
    bank = load_json(bank_path)
    if bank.get("schema_version") not in {
        "phase-d-d1-scenario-bank/v2",
        "phase-d-d1-scenario-bank/v3",
    }:
        raise PhaseDRedesignError("D1 scenario bank schema_version is invalid")
    scenarios = bank.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) < 6:
        raise PhaseDRedesignError("D1 requires at least six recognizable source scenarios")
    seen_ids: set[str] = set()
    seen_categories: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise PhaseDRedesignError("D1 scenarios must be objects")
        scenario_id = scenario.get("id")
        category = scenario.get("product_category")
        features = scenario.get("recognizable_features")
        if not isinstance(scenario_id, str) or scenario_id in seen_ids:
            raise PhaseDRedesignError("D1 scenario ids must be unique strings")
        if not isinstance(category, str) or category in seen_categories:
            raise PhaseDRedesignError("D1 product categories must be unique strings")
        if not isinstance(features, list) or len(features) < 3 or not all(isinstance(item, str) for item in features):
            raise PhaseDRedesignError(f"D1 scenario is not recognizably specified: {scenario_id}")
        source_path = scenario.get("source_path")
        expected_hash = scenario.get("source_sha256")
        if not isinstance(source_path, str) or not isinstance(expected_hash, str):
            raise PhaseDRedesignError(f"D1 source binding is invalid: {scenario_id}")
        source = root / source_path
        if sha256_file(source) != expected_hash:
            raise PhaseDRedesignError(f"D1 source hash mismatch: {scenario_id}")
        if source.stat().st_size > int(bank.get("maximum_source_bytes", 0)):
            raise PhaseDRedesignError(f"D1 source exceeds the frozen byte ceiling: {scenario_id}")
        for required in ("brief", "messages", "channel", "aspect_ratio"):
            if required not in scenario:
                raise PhaseDRedesignError(f"D1 scenario omits {required}: {scenario_id}")
        seen_ids.add(scenario_id)
        seen_categories.add(category)
    return bank


def balanced_blind_assignments(
    scenario_ids: list[str], randomization_seed: str
) -> dict[str, dict[str, str]]:
    """Create a deterministic, opaque, exactly balanced assignment from a frozen seed."""
    if (
        not scenario_ids
        or len(scenario_ids) % 2
        or len(set(scenario_ids)) != len(scenario_ids)
        or not all(isinstance(item, str) and item for item in scenario_ids)
    ):
        raise PhaseDRedesignError("D1 balanced assignment requires unique, nonempty, even scenario ids")
    if not isinstance(randomization_seed, str) or not randomization_seed:
        raise PhaseDRedesignError("D1 randomization seed is invalid")
    ranked = sorted(
        scenario_ids,
        key=lambda scenario_id: hashlib.sha256(
            f"{randomization_seed}\0{scenario_id}".encode("utf-8")
        ).digest(),
    )
    candidate_in_a = set(ranked[: len(ranked) // 2])
    return {
        scenario_id: (
            {"A": "candidate", "B": "comparator"}
            if scenario_id in candidate_in_a
            else {"A": "comparator", "B": "candidate"}
        )
        for scenario_id in scenario_ids
    }


def build_d1_run_specs(
    scenario: dict[str, Any],
    contract: dict[str, Any],
    source_bytes: bytes,
) -> dict[str, dict[str, Any]]:
    """Build paired run specs whose sole difference is assurance workflow."""
    parity = contract.get("paired_input_parity")
    workflows = contract.get("workflows")
    if not isinstance(parity, dict) or not isinstance(workflows, dict):
        raise PhaseDRedesignError("D1 paired-input contract is invalid")
    if sha256_bytes(source_bytes) != scenario.get("source_sha256"):
        raise PhaseDRedesignError("D1 source bytes do not match the frozen scenario")
    common = {
        "scenario_id": scenario["id"],
        "source_bytes": bytes(source_bytes),
        "source_sha256": sha256_bytes(source_bytes),
        "brief": copy.deepcopy(scenario["brief"]),
        "messages": copy.deepcopy(scenario["messages"]),
        "attempt_budget": int(parity["attempts_per_option"]),
        "model": str(parity["model"]),
        "tool": str(parity["tool"]),
        "timeout_seconds": int(parity["timeout_seconds"]),
        "evidence_budget_bytes": int(parity["evidence_budget_bytes_per_option"]),
    }
    return {
        "candidate": {**copy.deepcopy(common), "assurance_workflow": copy.deepcopy(workflows["candidate"])},
        "comparator": {**copy.deepcopy(common), "assurance_workflow": copy.deepcopy(workflows["comparator"])},
    }


def _escape_xml(value: object) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _bounded_lines(text: str, *, maximum_characters: int = 96, width: int = 32) -> list[str]:
    if len(text) > maximum_characters:
        raise PhaseDRedesignError("D1 message exceeds the bounded artifact text ceiling")
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
        else:
            if not current or len(lines) == 2:
                raise PhaseDRedesignError("D1 message cannot fit the bounded artifact")
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def render_bounded_artifact(scenario: dict[str, Any], source_bytes: bytes) -> bytes:
    """Render a deterministic 512px SVG with an enforced message safe area."""
    if sha256_bytes(source_bytes) != scenario.get("source_sha256"):
        raise PhaseDRedesignError("D1 render source does not match the frozen scenario")
    messages = scenario.get("messages")
    if not isinstance(messages, dict):
        raise PhaseDRedesignError("D1 scenario messages are invalid")
    headline = str(messages.get("headline", ""))
    support = str(messages.get("support", ""))
    lines = _bounded_lines(f"{headline} - {support}")
    source_media_type = str(scenario.get("source_media_type", "image/svg+xml"))
    source_b64 = base64.b64encode(source_bytes).decode("ascii")
    text_elements = []
    for index, line in enumerate(lines):
        text_elements.append(
            f'<text x="32" y="{414 + index * 30}" font-family="sans-serif" font-size="24" '
            f'font-weight="700" fill="#ffffff" data-max-width="448">{_escape_xml(line)}</text>'
        )
    title = _escape_xml(scenario.get("product_category", "synthetic product"))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512" '
        'data-safe-area="0,0,512,512">\n'
        f'  <title>Phase D synthetic {title}</title>\n'
        f'  <image x="0" y="0" width="512" height="384" preserveAspectRatio="xMidYMid meet" '
        f'href="data:{source_media_type};base64,{source_b64}"/>\n'
        '  <rect x="0" y="384" width="512" height="128" fill="#111827"/>\n'
        f'  {"".join(text_elements)}\n'
        '</svg>\n'
    )
    artifact = svg.encode("utf-8")
    validation = validate_bounded_svg(artifact)
    if not validation["bounded"]:
        raise PhaseDRedesignError("D1 renderer produced an overflowing artifact")
    return artifact


def _render_structured_artifact(scenario: dict[str, Any], source_bytes: bytes) -> bytes:
    """Render the V3 assurance layout while retaining identical content inputs."""
    if sha256_bytes(source_bytes) != scenario.get("source_sha256"):
        raise PhaseDRedesignError("D1 render source does not match the frozen scenario")
    messages = scenario.get("messages")
    if not isinstance(messages, dict):
        raise PhaseDRedesignError("D1 scenario messages are invalid")
    headline = str(messages.get("headline", ""))
    support = str(messages.get("support", ""))
    if len(f"{headline} - {support}") > 96:
        raise PhaseDRedesignError("D1 message exceeds the bounded artifact text ceiling")
    headline_lines = _bounded_lines(headline, maximum_characters=64, width=32)
    support_lines = _bounded_lines(support, maximum_characters=64, width=38)
    if len(headline_lines) + len(support_lines) > 3:
        raise PhaseDRedesignError("D1 message cannot fit the structured bounded artifact")
    source_media_type = str(scenario.get("source_media_type", "image/svg+xml"))
    source_b64 = base64.b64encode(source_bytes).decode("ascii")
    text_elements: list[str] = []
    y = 390
    for line in headline_lines:
        text_elements.append(
            f'<text x="32" y="{y}" font-family="sans-serif" font-size="24" '
            f'font-weight="700" fill="#ffffff" data-max-width="448">{_escape_xml(line)}</text>'
        )
        y += 29
    for line in support_lines:
        text_elements.append(
            f'<text x="32" y="{y}" font-family="sans-serif" font-size="18" '
            f'font-weight="400" fill="#dbeafe" data-max-width="448">{_escape_xml(line)}</text>'
        )
        y += 24
    title = _escape_xml(scenario.get("product_category", "synthetic product"))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512" '
        'data-safe-area="0,0,512,512">\n'
        f'  <title>Phase D synthetic {title}</title>\n'
        f'  <image x="0" y="0" width="512" height="352" preserveAspectRatio="xMidYMid meet" '
        f'href="data:{source_media_type};base64,{source_b64}"/>\n'
        '  <rect x="0" y="352" width="512" height="160" fill="#0f172a"/>\n'
        '  <rect x="32" y="352" width="96" height="8" fill="#38bdf8"/>\n'
        f'  {"".join(text_elements)}\n'
        '</svg>\n'
    )
    artifact = svg.encode("utf-8")
    if not validate_bounded_svg(artifact)["bounded"]:
        raise PhaseDRedesignError("D1 structured renderer produced an overflowing artifact")
    return artifact


def execute_d1_workflow(
    scenario: dict[str, Any], run_spec: dict[str, Any]
) -> dict[str, Any]:
    """Execute one frozen D1 workflow without allowing paired-input drift."""
    workflow = run_spec.get("assurance_workflow")
    source_bytes = run_spec.get("source_bytes")
    if not isinstance(workflow, dict) or not isinstance(source_bytes, bytes):
        raise PhaseDRedesignError("D1 workflow run spec is invalid")
    if run_spec.get("scenario_id") != scenario.get("id"):
        raise PhaseDRedesignError("D1 workflow scenario binding is invalid")
    if run_spec.get("source_sha256") != sha256_bytes(source_bytes):
        raise PhaseDRedesignError("D1 workflow source hash is invalid")
    if run_spec.get("brief") != scenario.get("brief") or run_spec.get("messages") != scenario.get("messages"):
        raise PhaseDRedesignError("D1 workflow brief or message input drifted")
    if int(run_spec.get("attempt_budget", 0)) != 1:
        raise PhaseDRedesignError("D1 workflow attempt budget is invalid")
    renderer = workflow.get("renderer")
    if renderer == "structured_safe_area_v3":
        artifact = _render_structured_artifact(scenario, source_bytes)
    elif renderer == "single_banner_v2":
        artifact = render_bounded_artifact(scenario, source_bytes)
    else:
        raise PhaseDRedesignError("D1 workflow renderer is not frozen or supported")
    steps = workflow.get("steps")
    if not isinstance(steps, list) or not steps or not all(isinstance(item, str) for item in steps):
        raise PhaseDRedesignError("D1 workflow trace is invalid")
    return {
        "workflow_trace": copy.deepcopy(steps),
        "artifact": artifact,
        "artifact_sha256": sha256_bytes(artifact),
        "artifact_validation": validate_bounded_svg(artifact),
    }


def validate_bounded_svg(artifact: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(artifact)
    except ET.ParseError as exc:
        raise PhaseDRedesignError(f"D1 artifact is not valid SVG: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise PhaseDRedesignError("D1 artifact root is not SVG")
    try:
        width = float(root.attrib["width"])
        height = float(root.attrib["height"])
        view_box = [float(item) for item in root.attrib["viewBox"].split()]
    except (KeyError, ValueError) as exc:
        raise PhaseDRedesignError("D1 artifact bounds are invalid") from exc
    overflow_reasons: list[str] = []
    if width != 512 or height != 512 or view_box != [0.0, 0.0, 512.0, 512.0]:
        overflow_reasons.append("canvas_or_viewbox_mismatch")
    def parse_numbers(value: str) -> list[float]:
        numbers = [
            float(item)
            for item in re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", value)
        ]
        if not numbers or not all(math.isfinite(item) for item in numbers):
            raise ValueError("geometry contains no finite coordinates")
        return numbers

    def outside_canvas(x_min: float, y_min: float, x_max: float, y_max: float) -> bool:
        values = (x_min, y_min, x_max, y_max)
        return not all(math.isfinite(item) for item in values) or x_min < 0 or y_min < 0 or x_max > 512 or y_max > 512

    unsupported_geometry = {
        "use",
        "foreignObject",
        "symbol",
    }
    for element in root.iter():
        kind = element.tag.rsplit("}", 1)[-1]
        if "transform" in element.attrib:
            overflow_reasons.append(f"unsupported_{kind}_transform")
            continue
        if kind in unsupported_geometry:
            overflow_reasons.append(f"unsupported_{kind}_geometry")
            continue
        if kind in {"rect", "image"}:
            try:
                x = float(element.attrib.get("x", "0"))
                y = float(element.attrib.get("y", "0"))
                item_width = float(element.attrib["width"])
                item_height = float(element.attrib["height"])
            except (KeyError, ValueError):
                overflow_reasons.append(f"invalid_{kind}_bounds")
                continue
            if item_width < 0 or item_height < 0 or outside_canvas(x, y, x + item_width, y + item_height):
                overflow_reasons.append(f"{kind}_overflow")
        elif kind == "circle":
            try:
                cx = float(element.attrib["cx"])
                cy = float(element.attrib["cy"])
                radius = float(element.attrib["r"])
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_circle_bounds")
                continue
            if radius < 0 or outside_canvas(cx - radius, cy - radius, cx + radius, cy + radius):
                overflow_reasons.append("circle_overflow")
        elif kind == "ellipse":
            try:
                cx = float(element.attrib["cx"])
                cy = float(element.attrib["cy"])
                rx = float(element.attrib["rx"])
                ry = float(element.attrib["ry"])
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_ellipse_bounds")
                continue
            if rx < 0 or ry < 0 or outside_canvas(cx - rx, cy - ry, cx + rx, cy + ry):
                overflow_reasons.append("ellipse_overflow")
        elif kind == "line":
            try:
                coordinates = [float(element.attrib[item]) for item in ("x1", "y1", "x2", "y2")]
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_line_bounds")
                continue
            if outside_canvas(
                min(coordinates[0], coordinates[2]),
                min(coordinates[1], coordinates[3]),
                max(coordinates[0], coordinates[2]),
                max(coordinates[1], coordinates[3]),
            ):
                overflow_reasons.append("line_overflow")
        elif kind in {"polyline", "polygon"}:
            try:
                coordinates = parse_numbers(element.attrib["points"])
                if len(coordinates) < 4 or len(coordinates) % 2:
                    raise ValueError("invalid point pairs")
            except (KeyError, ValueError):
                overflow_reasons.append(f"invalid_{kind}_bounds")
                continue
            xs, ys = coordinates[0::2], coordinates[1::2]
            if outside_canvas(min(xs), min(ys), max(xs), max(ys)):
                overflow_reasons.append(f"{kind}_overflow")
        elif kind == "path":
            path_data = element.attrib.get("d")
            if not isinstance(path_data, str) or not path_data.strip():
                overflow_reasons.append("invalid_path_bounds")
                continue
            commands = re.findall(r"[A-Za-z]", path_data)
            if any(command not in "MmLlHhVvZz" for command in commands):
                overflow_reasons.append("unsupported_path_geometry")
                continue
            try:
                coordinates = parse_numbers(path_data)
            except ValueError:
                overflow_reasons.append("invalid_path_bounds")
                continue
            # Simple line paths are sufficient for current artifacts; complex curves fail closed.
            if any(item < 0 or item > 512 for item in coordinates):
                overflow_reasons.append("path_overflow")
        elif kind == "text":
            try:
                x = float(element.attrib["x"])
                y = float(element.attrib["y"])
                font_size = float(element.attrib["font-size"])
                maximum_width = float(element.attrib["data-max-width"])
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_text_bounds")
                continue
            estimated_width = len(element.text or "") * font_size * 0.58
            if x < 0 or y - font_size < 0 or y > 512 or estimated_width > maximum_width or x + maximum_width > 512:
                overflow_reasons.append("text_overflow")
    return {
        "bounded": not overflow_reasons,
        "overflow": bool(overflow_reasons),
        "canvas": {"width": int(width), "height": int(height)},
        "overflow_reasons": sorted(set(overflow_reasons)),
    }


def build_rater_form(scenario: dict[str, Any]) -> dict[str, Any]:
    hard_gates = [
        {
            "id": "source_product_integrity",
            "question": "Is the source product recognizable and free of material geometry loss or invention?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "message_and_brief_accuracy",
            "question": "Are every required message and brief constraint accurate, legible, and unsupported claims absent?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "brand_and_rights_safety",
            "question": "Does the option obey the brand rules and avoid people, third-party marks, or rights ambiguity?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "technical_delivery_integrity",
            "question": "Is the option complete, readable, within the canvas, and free of clipping or overflow?",
            "allowed": ["pass", "fail", "uncertain"],
        },
    ]
    dimensions = [
        (
            "product_recognizability",
            "1: product identity is lost or materially distorted",
            "3: product is recognizable with minor ambiguity",
            "5: product is immediately recognizable and faithfully preserved",
        ),
        (
            "brief_and_message_fidelity",
            "1: key brief or message requirements are missing or wrong",
            "3: core requirements are present with minor weakness",
            "5: every requirement is accurate, clear, and well integrated",
        ),
        (
            "visual_hierarchy_and_legibility",
            "1: hierarchy is confusing or required text is unreadable",
            "3: hierarchy and text are usable with visible weaknesses",
            "5: attention and reading order are immediate at intended size",
        ),
        (
            "brand_coherence",
            "1: visual language conflicts with the stated brand",
            "3: broadly consistent with some generic or uneven choices",
            "5: distinctive, internally consistent, and on-brief",
        ),
        (
            "craft_and_delivery_readiness",
            "1: obvious artifacts, clipping, overflow, or unfinished details",
            "3: deliverable with minor polish issues",
            "5: clean, bounded, polished, and channel-ready",
        ),
    ]
    return {
        "schema_version": "phase-d-d1-rater-form/v2",
        "scenario_id": scenario["id"],
        "instructions": {
            "task": "Evaluate A and B independently, then state a comparative preference.",
            "independence": "Do not seek system identity or custody mapping. Record any accidental disclosure.",
            "hard_gate_rule": "Complete all four gates for each option before dimension scores or preference.",
            "abstention_rule": "Use abstain when evidence is insufficient; use tie only when both are judgeable and equivalent.",
        },
        "scenario": {
            "product_category": scenario["product_category"],
            "recognizable_features": copy.deepcopy(scenario["recognizable_features"]),
            "channel": scenario["channel"],
            "aspect_ratio": scenario["aspect_ratio"],
            "brief": copy.deepcopy(scenario["brief"]),
            "messages": copy.deepcopy(scenario["messages"]),
        },
        "hard_gates": {"per_option": hard_gates, "options": ["A", "B"]},
        "anchored_dimensions": [
            {"id": item[0], "scale": [1, 2, 3, 4, 5], "anchors": {"1": item[1], "3": item[2], "5": item[3]}}
            for item in dimensions
        ],
        "response_template": {
            "hard_gate_results": {"A": {}, "B": {}},
            "dimension_scores": {"A": {}, "B": {}},
            "preference": {"value": None, "allowed": ["A", "B", "tie", "abstain"]},
            "confidence": {"value": None, "scale": [1, 2, 3, 4, 5]},
            "rationale": "",
            "elapsed_minutes": None,
            "protocol_violations": [],
        },
    }


def write_delivery_bundle(
    destination: Path,
    scenario: dict[str, Any],
    option_a: bytes,
    option_b: bytes,
    rater_form: dict[str, Any],
) -> dict[str, Any]:
    """Create a rater-only bundle with no custody or generated-tree references."""
    lowered_parts = {part.lower() for part in destination.parts}
    if lowered_parts & {"custody", "mapping", "generated", "candidate", "comparator"}:
        raise PhaseDRedesignError("D1 rater delivery destination leaks custody or generation identity")
    destination.mkdir(parents=True, exist_ok=False)
    brief = {
        "schema_version": "phase-d-d1-rater-brief/v2",
        "scenario_id": scenario["id"],
        "product_category": scenario["product_category"],
        "recognizable_features": scenario["recognizable_features"],
        "channel": scenario["channel"],
        "aspect_ratio": scenario["aspect_ratio"],
        "brief": scenario["brief"],
        "messages": scenario["messages"],
    }
    source_path = scenario.get("source_path")
    source_hash = scenario.get("source_sha256")
    if not isinstance(source_path, str) or not isinstance(source_hash, str):
        raise PhaseDRedesignError("D1 rater source binding is invalid")
    source_file = Path(source_path)
    if source_file.is_absolute() or ".." in source_file.parts:
        raise PhaseDRedesignError("D1 rater source path is unsafe")
    repository_root = Path(__file__).resolve().parents[1]
    source_bytes = (repository_root / source_file).read_bytes()
    if sha256_bytes(source_bytes) != source_hash:
        raise PhaseDRedesignError("D1 rater source hash does not match the scenario")
    source_suffix = source_file.suffix.lower()
    if source_suffix not in {".svg", ".png", ".jpg", ".jpeg", ".webp"}:
        raise PhaseDRedesignError("D1 rater source media type is unsupported")
    delivered_source_path = f"original-source{source_suffix}"
    files: dict[str, bytes] = {
        "brief.json": (json.dumps(brief, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "option-A.svg": bytes(option_a),
        "option-B.svg": bytes(option_b),
        "rater-form.json": (json.dumps(rater_form, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    roles = {
        "brief.json": "scenario_brief",
        delivered_source_path: "original_source",
        "option-A.svg": "blind_option",
        "option-B.svg": "blind_option",
        "rater-form.json": "rater_form",
    }
    if "rater-delivery" in {part.lower() for part in destination.parts}:
        files[delivered_source_path] = source_bytes
    records = []
    for relative, content in sorted(files.items()):
        if len(content) > 1024 * 1024:
            raise PhaseDRedesignError(f"D1 delivery artifact exceeds one MiB: {relative}")
        path = destination / relative
        path.write_bytes(content)
        records.append({
            "path": relative,
            "role": roles[relative],
            "bytes": len(content),
            "sha256": sha256_bytes(content),
        })
    bundle_hash = sha256_bytes(canonical_json(records).encode("ascii"))
    manifest = {
        "schema_version": "phase-d-d1-delivery-bundle/v2",
        "scenario_id": scenario["id"],
        "files": records,
        "bundle_sha256": bundle_hash,
        "identity_disclosure": False,
    }
    serialized = canonical_json(manifest).lower()
    if any(term in serialized for term in ("candidate", "comparator", "custody", "mapping", "generated/")):
        raise PhaseDRedesignError("D1 delivery manifest leaks option identity or custody paths")
    write_json(destination / "bundle-manifest.json", manifest)
    return manifest


def create_harness_fixture(destination: Path) -> None:
    """Create a synthetic DB/repository fixture for harness tests and dry runs."""
    destination.mkdir(parents=True, exist_ok=False)
    database = destination / "company.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT NOT NULL, title TEXT NOT NULL);
            CREATE TABLE approvals(id INTEGER PRIMARY KEY, status TEXT NOT NULL, decision TEXT);
            CREATE TABLE audit_log(id INTEGER PRIMARY KEY, action TEXT NOT NULL, details TEXT NOT NULL);
            CREATE TABLE operational_counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE assurance_execution_bindings(id INTEGER PRIMARY KEY, binding_sha256 TEXT NOT NULL);
            CREATE TABLE assurance_state(name TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO tasks VALUES(1, 'running', 'synthetic canary task');
            INSERT INTO tasks VALUES(2, 'running', 'synthetic approved bound task');
            INSERT INTO approvals VALUES(1, 'pending', NULL);
            INSERT INTO audit_log VALUES(1, 'fixture_created', '{}');
            INSERT INTO operational_counters VALUES('worker_heartbeats', 7);
            INSERT INTO assurance_execution_bindings VALUES(1, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
            INSERT INTO assurance_state VALUES('approved_artifact', 'fresh-and-present');
            INSERT INTO assurance_state VALUES('threshold', 'frozen-threshold-v1');
            INSERT INTO assurance_state VALUES('profile', 'frozen-profile-v1');
            INSERT INTO assurance_state VALUES('authority', 'principal-evaluator');
            INSERT INTO assurance_state VALUES('credential', 'credential-generation-v1');
            INSERT INTO assurance_state VALUES('generation', 'execution-generation-v1');
            INSERT INTO assurance_state VALUES('evaluation', 'trusted-and-clean');
            INSERT INTO assurance_state VALUES('review', 'independent-bound-consistent');
            INSERT INTO assurance_state VALUES('dashboard_context', 'redacted');
            INSERT INTO assurance_state VALUES('lifecycle', 'monotonic');
            """
        )
    write_json(destination / "repo" / "contracts" / "freeze.json", {"id": "synthetic-freeze", "status": "frozen"})
    write_json(destination / "repo" / "evidence" / "baseline.json", {"id": "synthetic-baseline", "observed": True})


def _install_database_controls(database: Path, controls: list[str]) -> None:
    statements = {
        "deny_direct_completion": """
            CREATE TRIGGER phase_d_deny_direct_completion
            BEFORE UPDATE OF status ON tasks
            WHEN NEW.id=1 AND NEW.status='completed' AND OLD.status!='completed'
            BEGIN SELECT RAISE(ABORT, 'direct completion denied'); END;
        """,
        "protect_approval_records": """
            CREATE TRIGGER phase_d_approvals_immutable_update BEFORE UPDATE ON approvals
            BEGIN SELECT RAISE(ABORT, 'approval mutation denied'); END;
            CREATE TRIGGER phase_d_approvals_immutable_delete BEFORE DELETE ON approvals
            BEGIN SELECT RAISE(ABORT, 'approval deletion denied'); END;
        """,
        "protect_audit_records": """
            CREATE TRIGGER phase_d_audit_immutable_update BEFORE UPDATE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit mutation denied'); END;
            CREATE TRIGGER phase_d_audit_immutable_delete BEFORE DELETE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit deletion denied'); END;
        """,
        "protect_execution_bindings": """
            CREATE TRIGGER phase_d_bindings_immutable_update BEFORE UPDATE ON assurance_execution_bindings
            BEGIN SELECT RAISE(ABORT, 'binding mutation denied'); END;
            CREATE TRIGGER phase_d_bindings_immutable_delete BEFORE DELETE ON assurance_execution_bindings
            BEGIN SELECT RAISE(ABORT, 'binding deletion denied'); END;
        """,
        "protect_assurance_state": """
            CREATE TRIGGER phase_d_assurance_state_immutable_update BEFORE UPDATE ON assurance_state
            BEGIN SELECT RAISE(ABORT, 'assurance state mutation denied'); END;
            CREATE TRIGGER phase_d_assurance_state_immutable_delete BEFORE DELETE ON assurance_state
            BEGIN SELECT RAISE(ABORT, 'assurance state deletion denied'); END;
        """,
    }
    unknown = set(controls) - set(statements)
    if unknown:
        raise PhaseDRedesignError(f"unknown D2 database controls: {', '.join(sorted(unknown))}")
    with sqlite3.connect(database) as conn:
        for control in controls:
            conn.executescript(statements[control])


def _database_snapshot(database: Path) -> dict[str, Any]:
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        triggers = [
            {"name": row[0], "table": row[1], "sql": row[2]}
            for row in conn.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        ]
        rows = {
            table: [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in tables
        }
    return {"file_sha256": sha256_file(database), "tables": rows, "triggers": triggers}


def _tree_snapshot(fixture: Path) -> dict[str, Any]:
    database = _database_snapshot(fixture / "company.sqlite3")
    repo_root = fixture / "repo"
    repo_files = {
        path.relative_to(repo_root).as_posix(): {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(repo_root.rglob("*"))
        if path.is_file()
    }
    state = {"database": database, "repository": repo_files}
    return {**state, "state_sha256": sha256_bytes(canonical_json(state).encode("ascii"))}


def _copy_fixture(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def _restore_fixture(source: Path, destination: Path) -> None:
    source_files = {
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file()
    }
    for path in sorted((item for item in destination.rglob("*") if item.is_file()), reverse=True):
        if path.relative_to(destination) not in source_files:
            path.unlink()
    for relative in sorted(source_files):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)


def _apply_mutation(fixture: Path, mutation: dict[str, Any], protected_paths: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    kind = mutation.get("kind")
    event: dict[str, Any] = {"kind": kind, "attempted": True}
    if kind == "sql":
        statement = mutation.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise PhaseDRedesignError("D2 SQL mutation is invalid")
        try:
            with sqlite3.connect(fixture / "company.sqlite3") as conn:
                conn.execute(statement)
            event.update({"applied": True, "error": None})
            observation = {"outcome": "allowed", "mechanism": "database_commit", "detail": "mutation committed in isolated copy"}
        except sqlite3.DatabaseError as exc:
            event.update({"applied": False, "error": str(exc)})
            observation = {"outcome": "denied", "mechanism": "sqlite_control", "detail": str(exc)}
        return event, observation
    if kind in {"repo_write", "repo_delete"}:
        relative = mutation.get("path")
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise PhaseDRedesignError("D2 repository mutation path is invalid")
        target = fixture / "repo" / relative
        if relative in protected_paths:
            event.update({
                "applied": False,
                "path": relative,
                "error": "protected repository path mutation denied",
            })
            observation = {
                "outcome": "denied",
                "mechanism": "repository_integrity_guard",
                "detail": "protected path mutation denied in isolated copy",
            }
            return event, observation
        if kind == "repo_write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(mutation.get("content", "")), encoding="utf-8")
        elif target.exists():
            target.unlink()
        event.update({"applied": True, "path": relative, "error": None})
        observation = {"outcome": "allowed", "mechanism": "repository_write", "detail": "mutation allowed in isolated copy"}
        return event, observation
    raise PhaseDRedesignError(f"unsupported D2 mutation kind: {kind}")


def _select_noninterference(snapshot: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    tables = snapshot["database"]["tables"]
    repo = snapshot["repository"]
    protected_tables = case.get("protected_tables", [])
    protected_paths = case.get("protected_repo_paths", [])
    if not isinstance(protected_tables, list) or not isinstance(protected_paths, list):
        raise PhaseDRedesignError("D2 noninterference protections are invalid")
    return {
        "tables": {str(name): tables.get(str(name)) for name in protected_tables},
        "repository": {str(path): repo.get(str(path)) for path in protected_paths},
    }


def _execute_mutation_side(
    frozen_fixture: Path,
    case: dict[str, Any],
    workflow: dict[str, Any],
    side: str,
    evidence_root: Path,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"phase-d-d2-{side}-") as temporary:
        temporary_root = Path(temporary)
        isolated = temporary_root / "isolated"
        _copy_fixture(frozen_fixture, isolated)
        controls = workflow.get("database_controls", [])
        protected_paths = workflow.get("protected_repo_paths", [])
        if not isinstance(controls, list) or not isinstance(protected_paths, list):
            raise PhaseDRedesignError("D2 workflow controls are invalid")
        _install_database_controls(isolated / "company.sqlite3", [str(item) for item in controls])
        rollback_source = temporary_root / "rollback-source"
        _copy_fixture(isolated, rollback_source)
        before = _tree_snapshot(isolated)
        protected_before = _select_noninterference(before, case)
        mutation_event, observation = _apply_mutation(
            isolated,
            copy.deepcopy(case["mutation"]),
            {str(item) for item in protected_paths},
        )
        observed = _tree_snapshot(isolated)
        protected_observed = _select_noninterference(observed, case)
        noninterference = {
            "passed": protected_before == protected_observed,
            "protected_before_sha256": sha256_bytes(canonical_json(protected_before).encode("ascii")),
            "protected_observed_sha256": sha256_bytes(canonical_json(protected_observed).encode("ascii")),
            "protected_tables": case.get("protected_tables", []),
            "protected_repo_paths": case.get("protected_repo_paths", []),
        }
        _restore_fixture(rollback_source, isolated)
        after = _tree_snapshot(isolated)
        rollback = {
            "completed": before["state_sha256"] == after["state_sha256"],
            "method": "restore_frozen_pre_mutation_copy",
            "before_state_sha256": before["state_sha256"],
            "after_state_sha256": after["state_sha256"],
        }
        audit_rows = observed["database"]["tables"].get("audit_log", [])
        event = {
            "case_id": case["id"],
            "workflow_id": workflow["id"],
            "side": side,
            "mutation": mutation_event,
            "observation": observation,
        }
        audit_event_evidence = {
            "event": event,
            "event_sha256": sha256_bytes(canonical_json(event).encode("ascii")),
            "observed_audit_rows": audit_rows,
            "observed_audit_rows_sha256": sha256_bytes(canonical_json(audit_rows).encode("ascii")),
        }
        record = {
            "workflow": copy.deepcopy(workflow),
            "before_snapshot": before,
            "mutation": mutation_event,
            "observation": observation,
            "rollback": rollback,
            "after_snapshot": after,
            "audit_event_evidence": audit_event_evidence,
            "noninterference": noninterference,
        }
        if not rollback["completed"]:
            raise PhaseDRedesignError(f"D2 rollback failed: {case['id']} {side}")
        if not noninterference["passed"]:
            raise PhaseDRedesignError(f"D2 noninterference failed: {case['id']} {side}")
        destination = evidence_root / str(case["id"]) / side
        artifacts = {
            "before-snapshot.json": before,
            "mutation.json": mutation_event,
            "observation.json": observation,
            "rollback.json": rollback,
            "after-snapshot.json": after,
            "audit-event-evidence.json": audit_event_evidence,
            "noninterference.json": noninterference,
        }
        for name, value in artifacts.items():
            write_json(destination / name, value)
        return record


def execute_mutation_pair(
    frozen_fixture: Path,
    case: dict[str, Any],
    baseline_workflow: dict[str, Any],
    treatment_workflow: dict[str, Any],
    evidence_root: Path,
) -> dict[str, Any]:
    """Run one fault against separate baseline and treatment copies, then restore both."""
    if baseline_workflow.get("id") == treatment_workflow.get("id") or canonical_json(baseline_workflow) == canonical_json(treatment_workflow):
        raise PhaseDRedesignError("D2 baseline and treatment workflows must be distinct")
    if not (frozen_fixture / "company.sqlite3").is_file() or not (frozen_fixture / "repo").is_dir():
        raise PhaseDRedesignError("D2 frozen fixture must include a database and repository copy")
    return {
        "case_id": case["id"],
        "fault_class": case.get("fault_class"),
        "severity": case.get("severity"),
        "seeded_fault": bool(case.get("seeded_fault")),
        "valid_control": bool(case.get("valid_control")),
        "baseline": _execute_mutation_side(
            frozen_fixture, case, baseline_workflow, "baseline", evidence_root
        ),
        "treatment": _execute_mutation_side(
            frozen_fixture, case, treatment_workflow, "treatment", evidence_root
        ),
    }


def validate_mutation_bank(bank_path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate exact V2/V3 mutation coverage and mutually exclusive case roles."""
    bank = load_json(bank_path)
    if bank.get("schema_version") not in {
        "phase-d-d2-mutation-bank/v2",
        "phase-d-d2-mutation-bank/v3",
    }:
        raise PhaseDRedesignError("D2 mutation bank schema_version is invalid")
    cases = bank.get("cases")
    if not isinstance(cases, list) or not cases:
        raise PhaseDRedesignError("D2 mutation bank cases are invalid")
    seen_ids: set[str] = set()
    observed_classes: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise PhaseDRedesignError("D2 mutation cases must be objects")
        case_id = case.get("id")
        fault_class = case.get("fault_class")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise PhaseDRedesignError("D2 mutation case ids must be unique nonempty strings")
        if not isinstance(fault_class, str) or not fault_class:
            raise PhaseDRedesignError(f"D2 mutation fault class is invalid: {case_id}")
        seeded_fault = case.get("seeded_fault") is True
        valid_control = case.get("valid_control") is True
        if seeded_fault == valid_control:
            raise PhaseDRedesignError(f"D2 case must be exactly one fault or control: {case_id}")
        severity = case.get("severity")
        if not isinstance(severity, str) or severity.lower() not in {"critical", "high", "medium", "low"}:
            raise PhaseDRedesignError(f"D2 case severity is invalid: {case_id}")
        if seeded_fault and severity.lower() not in {"critical", "high"}:
            raise PhaseDRedesignError(f"D2 seeded material fault is not Critical/High: {case_id}")
        if case.get("target") not in {"database", "repository"} or not isinstance(case.get("mutation"), dict):
            raise PhaseDRedesignError(f"D2 mutation target or operation is invalid: {case_id}")
        for protected in ("protected_tables", "protected_repo_paths"):
            values = case.get(protected)
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise PhaseDRedesignError(f"D2 {protected} is invalid: {case_id}")
        seen_ids.add(case_id)
        observed_classes.add(fault_class)

    required_faults = contract.get("required_fault_classes")
    required_controls = contract.get("required_control_classes")
    if required_faults is not None or required_controls is not None:
        if (
            not isinstance(required_faults, list)
            or not isinstance(required_controls, list)
            or not required_faults
            or not required_controls
            or not all(isinstance(item, str) for item in required_faults + required_controls)
        ):
            raise PhaseDRedesignError("D2 contract required class lists are invalid")
        required = set(required_faults) | set(required_controls)
        if len(required) != len(required_faults) + len(required_controls):
            raise PhaseDRedesignError("D2 fault and control classes must be distinct")
        if observed_classes != required:
            raise PhaseDRedesignError("D2 mutation bank does not exactly cover every required class")
        if len(cases) < 16:
            raise PhaseDRedesignError("D2 V3 mutation bank requires at least sixteen cases")
        role_by_class = {
            str(case["fault_class"]): (case.get("seeded_fault") is True, case.get("valid_control") is True)
            for case in cases
        }
        if any(role_by_class[item] != (True, False) for item in required_faults):
            raise PhaseDRedesignError("D2 required fault class is not seeded independently")
        if any(role_by_class[item] != (False, True) for item in required_controls):
            raise PhaseDRedesignError("D2 required control class is not evaluated independently")
    dry_run_subset = bank.get("dry_run_subset", [])
    if not isinstance(dry_run_subset, list) or any(item not in seen_ids for item in dry_run_subset):
        raise PhaseDRedesignError("D2 dry-run subset contains an unknown case")
    return bank


def derive_d2_observation_thresholds(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Require every material fault denial independent of the baseline comparison."""
    if not pairs:
        raise PhaseDRedesignError("D2 threshold derivation requires paired observations")
    baseline_escapes = sorted(
        str(pair["case_id"])
        for pair in pairs
        if pair.get("seeded_fault") is True and pair["baseline"]["observation"]["outcome"] == "allowed"
    )
    baseline_allowed_controls = sorted(
        str(pair["case_id"])
        for pair in pairs
        if pair.get("valid_control") is True and pair["baseline"]["observation"]["outcome"] == "allowed"
    )
    material_faults = sorted(
        str(pair["case_id"])
        for pair in pairs
        if pair.get("seeded_fault") is True
        and str(pair.get("severity", "")).strip().lower() in {"critical", "high"}
    )
    required_denials = sorted(set(baseline_escapes) | set(material_faults))
    evaluated_controls = sorted(
        str(pair["case_id"])
        for pair in pairs
        if pair.get("valid_control") is True
        and pair.get("treatment", {}).get("observation", {}).get("outcome") in {"allowed", "denied"}
    )
    all_control_ids = sorted(
        str(pair["case_id"]) for pair in pairs if pair.get("valid_control") is True
    )
    treatment_denied = {
        str(pair["case_id"])
        for pair in pairs
        if pair["treatment"]["observation"]["outcome"] == "denied"
    }
    treatment_allowed = {
        str(pair["case_id"])
        for pair in pairs
        if pair["treatment"]["observation"]["outcome"] == "allowed"
    }
    return {
        "threshold_source": "paired_baseline_observations",
        "observed_baseline_escape_ids": baseline_escapes,
        "seeded_material_fault_ids": material_faults,
        "required_treatment_denial_ids": required_denials,
        "observed_baseline_allowed_control_ids": baseline_allowed_controls,
        "evaluated_control_ids": evaluated_controls,
        "required_treatment_allow_ids": all_control_ids,
        "treatment_denied_required_ids": sorted(set(required_denials) & treatment_denied),
        "treatment_allowed_required_control_ids": sorted(set(all_control_ids) & treatment_allowed),
        "all_seeded_material_faults_denied": set(material_faults).issubset(treatment_denied),
        "all_controls_evaluated": set(all_control_ids) == set(evaluated_controls),
        "all_valid_controls_allowed": set(all_control_ids).issubset(treatment_allowed),
        "observation_derived_comparison_passed": (
            bool(baseline_escapes)
            and set(baseline_escapes).issubset(treatment_denied)
            and set(baseline_allowed_controls).issubset(treatment_allowed)
        ),
        "thresholds_passed": (
            bool(required_denials)
            and set(required_denials).issubset(treatment_denied)
            and set(all_control_ids) == set(evaluated_controls)
            and set(all_control_ids).issubset(treatment_allowed)
        ),
    }


def _artifact_manifest(output: Path) -> dict[str, Any]:
    records = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "evidence-manifest.json":
            records.append({
                "path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return {
        "schema_version": "phase-d-redesign-evidence-manifest/v1",
        "status": "dry_run_complete_treatments_blocked",
        "corrected_treatments_executed": False,
        "artifacts": records,
        "external_actions_observed": [],
    }


def run_redesign_dry_run(
    root: Path, output: Path, *, freeze_path: Path | None = None
) -> dict[str, Any]:
    """Validate corrected tooling on synthetic canaries without executing treatments."""
    freeze = freeze_path or (
        root / "docs" / "assurance" / "phase-d" / "redesign" / "corrected-freeze-v2.json"
    )
    verification = verify_corrected_freeze(root, freeze)
    if verification["execution_authorized"]:
        raise PhaseDRedesignError("redesign dry run must not run after treatment authorization")
    if output.exists():
        allowed_bootstrap_files = {
            "dry-run-result.json",
            "evidence-manifest.json",
            "red-probes.txt",
            "regression-results.txt",
        }
        existing = {path.name for path in output.iterdir() if path.is_file()}
        has_directories = any(path.is_dir() for path in output.iterdir())
        if freeze.name != "corrected-freeze-v3.json" or has_directories or not existing.issubset(allowed_bootstrap_files):
            raise PhaseDRedesignError("redesign dry-run output must not already exist")
    else:
        output.mkdir(parents=True)

    documents = verification["documents"]
    bank_path = root / documents["d1_contract"]["scenario_bank"]
    scenario_bank = validate_scenario_bank(root, bank_path)
    d1_generated_root = output / "dry-run" / "d1" / "working"
    d1_delivery_root = output / "dry-run" / "d1" / "rater-delivery"
    custody_mappings = []
    delivery_records = []
    assignments = (
        balanced_blind_assignments(
            [str(item["id"]) for item in scenario_bank["scenarios"]],
            str(scenario_bank["randomization_seed"]),
        )
        if scenario_bank.get("schema_version") == "phase-d-d1-scenario-bank/v3"
        else {
            str(item["id"]): {"A": "candidate", "B": "comparator"}
            for item in scenario_bank["scenarios"]
        }
    )
    for scenario in scenario_bank["scenarios"]:
        source = (root / scenario["source_path"]).read_bytes()
        specs = build_d1_run_specs(scenario, documents["d1_contract"], source)
        candidate = copy.deepcopy(specs["candidate"])
        comparator = copy.deepcopy(specs["comparator"])
        candidate_workflow = candidate.pop("assurance_workflow")
        comparator_workflow = comparator.pop("assurance_workflow")
        if candidate != comparator or candidate_workflow == comparator_workflow:
            raise PhaseDRedesignError(f"D1 dry-run parity failed: {scenario['id']}")
        executions = {
            side: execute_d1_workflow(scenario, specs[side])
            if scenario_bank.get("schema_version") == "phase-d-d1-scenario-bank/v3"
            else {
                "artifact": render_bounded_artifact(scenario, source),
                "workflow_trace": specs[side]["assurance_workflow"]["steps"],
            }
            for side in ("candidate", "comparator")
        }
        if scenario_bank.get("schema_version") == "phase-d-d1-scenario-bank/v3" and (
            executions["candidate"]["workflow_trace"] == executions["comparator"]["workflow_trace"]
            or executions["candidate"]["artifact"] == executions["comparator"]["artifact"]
        ):
            raise PhaseDRedesignError(f"D1 dry-run workflow distinction failed: {scenario['id']}")
        for side in ("candidate", "comparator"):
            artifact = executions[side]["artifact"]
            working = d1_generated_root / str(scenario["id"]) / side
            working.mkdir(parents=True)
            (working / "artifact.svg").write_bytes(artifact)
            write_json(working / "run-spec.json", {
                **{key: value for key, value in specs[side].items() if key != "source_bytes"},
                "source_bytes_sha256": sha256_bytes(specs[side]["source_bytes"]),
                "artifact_sha256": sha256_bytes(artifact),
                "artifact_validation": validate_bounded_svg(artifact),
                "dry_run_only": True,
            })
        assignment = assignments[str(scenario["id"])]
        option_artifacts = {
            option: executions[workflow]["artifact"] for option, workflow in assignment.items()
        }
        delivery = d1_delivery_root / str(scenario["id"])
        manifest = write_delivery_bundle(
            delivery,
            scenario,
            option_artifacts["A"],
            option_artifacts["B"],
            build_rater_form(scenario),
        )
        custody_mappings.append({
            "scenario_id": scenario["id"],
            "assignment": assignment,
            "candidate_artifact_sha256": sha256_bytes(executions["candidate"]["artifact"]),
            "comparator_artifact_sha256": sha256_bytes(executions["comparator"]["artifact"]),
            "delivery_bundle_sha256": manifest["bundle_sha256"],
            "dry_run_only": True,
        })
        delivery_records.append({
            "scenario_id": scenario["id"],
            "bundle_sha256": manifest["bundle_sha256"],
            "delivery_path": delivery.relative_to(output).as_posix(),
        })
    write_json(output / "custody" / "d1" / "custody-mapping.json", {
        "schema_version": "phase-d-d1-dry-run-custody/v2",
        "access": "not_for_raters",
        "treatment_execution": False,
        "mappings": custody_mappings,
    })
    d1_result = {
        "schema_version": "phase-d-d1-redesign-dry-run/v1",
        "status": "dry_run_complete_treatment_blocked",
        "scenario_count": len(delivery_records),
        "paired_input_parity_verified": True,
        "workflow_distinction_verified": scenario_bank.get("schema_version") == "phase-d-d1-scenario-bank/v3",
        "randomized_exact_balance_verified": (
            sum(item["A"] == "candidate" for item in assignments.values())
            == len(assignments) // 2
        ),
        "bounded_nonoverflow_artifacts_verified": True,
        "delivery_bundles": delivery_records,
        "ratings_collected": 0,
        "treatment_execution": False,
    }
    write_json(output / "dry-run" / "d1" / "result.json", d1_result)

    mutation_bank = validate_mutation_bank(
        root / documents["d2_contract"]["mutation_bank"], documents["d2_contract"]
    )
    cases = mutation_bank.get("cases")
    dry_run_ids = mutation_bank.get("dry_run_subset")
    if not isinstance(cases, list) or not isinstance(dry_run_ids, list):
        raise PhaseDRedesignError("D2 mutation bank dry-run subset is invalid")
    by_id = {str(case["id"]): case for case in cases if isinstance(case, dict)}
    selected = []
    selected_ids = (
        [str(case["id"]) for case in cases]
        if mutation_bank.get("schema_version") == "phase-d-d2-mutation-bank/v3"
        else dry_run_ids
    )
    for case_id in selected_ids:
        if case_id not in by_id:
            raise PhaseDRedesignError(f"D2 dry-run case is missing: {case_id}")
        selected.append(by_id[str(case_id)])
    fixture = output / "dry-run" / "d2" / "frozen-fixture"
    create_harness_fixture(fixture)
    fixture_before = _tree_snapshot(fixture)
    baseline = documents["d2_contract"]["workflows"]["baseline"]
    treatment = documents["d2_contract"]["workflows"]["treatment"]
    pairs = [
        execute_mutation_pair(
            fixture,
            case,
            baseline,
            treatment,
            output / "dry-run" / "d2" / "cases",
        )
        for case in selected
    ]
    fixture_after = _tree_snapshot(fixture)
    if fixture_before["state_sha256"] != fixture_after["state_sha256"]:
        raise PhaseDRedesignError("D2 dry run changed the frozen source fixture")
    comparison = derive_d2_observation_thresholds(pairs)
    d2_result = {
        "schema_version": "phase-d-d2-redesign-dry-run/v1",
        "status": "dry_run_complete_treatment_blocked",
        "canary_count": len(pairs),
        "canary_ids": [pair["case_id"] for pair in pairs],
        "threshold_source": comparison["threshold_source"],
        "comparison": comparison,
        "all_seeded_material_faults_denied": comparison["all_seeded_material_faults_denied"],
        "all_controls_evaluated": comparison["all_controls_evaluated"],
        "thresholds_passed": comparison["thresholds_passed"],
        "frozen_fixture_unchanged": True,
        "treatment_execution": False,
    }
    write_json(output / "dry-run" / "d2" / "result.json", d2_result)

    result = {
        "schema_version": "phase-d-redesign-dry-run/v1",
        "status": "dry_run_complete_treatments_blocked",
        "corrected_treatments_executed": False,
        "independent_approval_present": False,
        "d1": d1_result,
        "d2": d2_result,
        "forbidden_actions_observed": [],
    }
    write_json(output / "dry-run-result.json", result)
    write_json(output / "evidence-manifest.json", _artifact_manifest(output))
    return result
