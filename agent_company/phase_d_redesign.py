"""Fail-closed tooling for the corrected Phase D D1/D2 design."""

from __future__ import annotations

import copy
import hashlib
import hmac
import importlib
import json
import math
import os
import re
import stat
import subprocess
import tempfile
import unittest
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
    """Reject the superseded V2 freeze and its unauthenticated approval model."""
    del (root, freeze_path, require_execution_approval)
    raise PhaseDRedesignError(
        "V2 corrected freeze is superseded by V4 and cannot authorize or verify execution"
    )


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


def _read_hardened_external_file(root: Path, path: Path, label: str) -> bytes:
    """Read an external trust file without following links or accepting weak modes."""
    if not path.is_absolute():
        raise PhaseDRedesignError(f"{label} path must be absolute and external")
    try:
        path.relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise PhaseDRedesignError(f"{label} path must be outside the repository")
    parent_chain = [path.parent, *path.parent.parents]
    for parent in parent_chain:
        if parent == parent.parent:
            continue
        if parent.is_symlink():
            raise PhaseDRedesignError(f"{label} parent directory must not be a symlink: {parent}")
        try:
            metadata = parent.stat()
        except OSError as exc:
            raise PhaseDRedesignError(f"cannot inspect {label} parent directory: {exc}") from exc
        if not stat.S_ISDIR(metadata.st_mode):
            raise PhaseDRedesignError(f"{label} parent must be a directory: {parent}")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode & 0o022 and not mode & stat.S_ISVTX:
            raise PhaseDRedesignError(
                f"{label} parent directory permissions are unsafe: {parent}"
            )
    if path.is_symlink():
        raise PhaseDRedesignError(f"{label} must be a regular file, not a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise PhaseDRedesignError(f"cannot open hardened {label}: {exc}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PhaseDRedesignError(f"{label} must be a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise PhaseDRedesignError(f"{label} permissions must be exactly 0600")
        if metadata.st_uid not in {os.geteuid(), 0}:
            raise PhaseDRedesignError(f"{label} owner is not trusted")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            return handle.read()
    finally:
        os.close(descriptor)


def load_trusted_governance_credentials(
    root: Path, freeze: dict[str, Any]
) -> dict[str, bytes]:
    """Load the two freeze-bound credentials only from a hardened external registry."""
    gate = freeze.get("execution_gate")
    if not isinstance(gate, dict):
        raise PhaseDRedesignError("v4 execution gate is invalid")
    registry_value = gate.get("trusted_registry_path")
    if not isinstance(registry_value, str) or not registry_value:
        raise PhaseDRedesignError("v4 trusted governance registry path is missing")
    raw = _read_hardened_external_file(
        root, Path(registry_value), "governance registry"
    )
    try:
        registry = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhaseDRedesignError(f"governance registry is malformed: {exc}") from exc
    if not isinstance(registry, dict) or registry.get("schema_version") != "phase-d-governance-registry/v4":
        raise PhaseDRedesignError("governance registry schema_version is invalid")
    if registry.get("freeze_id") != freeze.get("id"):
        raise PhaseDRedesignError("governance registry is not bound to the freeze identity")
    entries = registry.get("credentials")
    if not isinstance(entries, list) or len(entries) != 2:
        raise PhaseDRedesignError("governance registry must contain exactly two credentials")
    expected_identities = {}
    for identity_name in ("reviewer_identity", "ceo_identity"):
        identity = gate.get(identity_name)
        if not isinstance(identity, dict):
            raise PhaseDRedesignError("v4 governance identities are not frozen")
        key_id = identity.get("key_id")
        if not isinstance(key_id, str) or not key_id or key_id in expected_identities:
            raise PhaseDRedesignError("v4 governance key identities are invalid")
        expected_identities[key_id] = {
            "principal_id": identity.get("principal_id"),
            "role": identity.get("role"),
            "key_id": key_id,
        }
    credentials: dict[str, bytes] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PhaseDRedesignError("governance registry credential entry is invalid")
        key_id = entry.get("key_id")
        expected = expected_identities.get(str(key_id))
        if expected is None or any(entry.get(field) != value for field, value in expected.items()):
            raise PhaseDRedesignError("governance registry identity does not match the freeze")
        credential_value = entry.get("credential_path")
        if not isinstance(credential_value, str) or not credential_value:
            raise PhaseDRedesignError("governance credential path is invalid")
        credential = _read_hardened_external_file(
            root, Path(credential_value), f"governance credential {key_id}"
        ).strip()
        if not credential:
            raise PhaseDRedesignError("trusted governance credential is empty")
        if str(key_id) in credentials:
            raise PhaseDRedesignError("governance registry repeats a credential")
        credentials[str(key_id)] = credential
    if set(credentials) != set(expected_identities):
        raise PhaseDRedesignError("governance registry omits a freeze-bound credential")
    return credentials


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
    """Reject the superseded caller-credential trust model."""
    del (
        freeze, freeze_sha256, document_sha256, binding_sha256, approval,
        ceo_decision, credentials, require_execution_authorization,
    )
    raise PhaseDRedesignError(
        "V3 authorization is superseded because caller-provided credentials are not a trust root"
    )

def _run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise PhaseDRedesignError(
            f"cannot verify immutable Git review target ({' '.join(args)}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.rstrip("\r\n")


def verify_immutable_review_target(
    root: Path, target: dict[str, Any]
) -> dict[str, str]:
    """Require the current clean checkout to be the exact reviewed commit and tree."""
    commit = target.get("commit")
    tree = target.get("tree")
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not isinstance(tree, str)
        or not re.fullmatch(r"[0-9a-f]{40}", tree)
        or target.get("scope") != "entire_git_tree"
        or target.get("require_clean_worktree") is not True
    ):
        raise PhaseDRedesignError(
            "immutable review target commit/tree/scope and clean worktree requirement are invalid"
        )
    resolved_tree = _run_git(root, "rev-parse", f"{commit}^{{tree}}")
    if resolved_tree != tree:
        raise PhaseDRedesignError("immutable review target commit/tree binding is invalid")
    current_head = _run_git(root, "rev-parse", "HEAD")
    current_tree = _run_git(root, "rev-parse", "HEAD^{tree}")
    if current_head != commit or current_tree != tree:
        raise PhaseDRedesignError("current HEAD/tree drifted from the immutable review target")
    status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        changed_paths = []
        for line in status.splitlines():
            path = line[3:] if len(line) > 3 else line
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            changed_paths.append(path)
        raise PhaseDRedesignError(
            "unbound changed path or worktree drift: " + ", ".join(changed_paths)
        )
    return {"commit": commit, "tree": tree, "scope": "entire_git_tree"}


def _verify_git_object_target(root: Path, target: dict[str, Any]) -> dict[str, str]:
    commit = target.get("commit")
    tree = target.get("tree")
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or not isinstance(tree, str)
        or not re.fullmatch(r"[0-9a-f]{40}", tree)
        or target.get("scope") != "entire_git_tree"
        or target.get("require_clean_worktree") is not True
    ):
        raise PhaseDRedesignError("Git object review target is invalid")
    if _run_git(root, "rev-parse", f"{commit}^{{tree}}") != tree:
        raise PhaseDRedesignError("Git object review target commit/tree binding is invalid")
    return {"commit": commit, "tree": tree, "scope": "entire_git_tree"}


def load_external_review_target(root: Path, freeze: dict[str, Any]) -> dict[str, Any]:
    source = freeze.get("candidate_review_target_source")
    if not isinstance(source, dict) or source.get("kind") != "hardened_external_signed_manifest":
        raise PhaseDRedesignError("external candidate review target source is invalid")
    manifest_value = source.get("manifest_path")
    if not isinstance(manifest_value, str) or not manifest_value:
        raise PhaseDRedesignError("external candidate review target manifest is absent")
    raw = _read_hardened_external_file(
        root, Path(manifest_value), "candidate review target manifest"
    )
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PhaseDRedesignError(f"candidate review target manifest is malformed: {exc}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "phase-d-review-target/v4"
        or manifest.get("freeze_id") != freeze.get("id")
        or manifest.get("decision_scope") != "review_and_verification_only"
    ):
        raise PhaseDRedesignError("candidate review target manifest is not freeze-bound")
    gate = freeze.get("execution_gate")
    reviewer_identity = gate.get("reviewer_identity") if isinstance(gate, dict) else None
    if not isinstance(reviewer_identity, dict):
        raise PhaseDRedesignError("candidate review target reviewer identity is not frozen")
    credentials = load_trusted_governance_credentials(root, freeze)
    try:
        _verify_governance_signature(manifest, reviewer_identity, credentials)
    except PhaseDRedesignError as exc:
        raise PhaseDRedesignError(
            f"candidate review target manifest is not authenticated by a trusted signature: {exc}"
        ) from exc
    target = manifest.get("target")
    if not isinstance(target, dict):
        raise PhaseDRedesignError("candidate review target manifest lacks a target")
    return target


def evaluate_v4_authorization(
    root: Path,
    freeze: dict[str, Any],
    approval: dict[str, Any] | None,
    ceo_decision: dict[str, Any] | None,
    *,
    require_execution_authorization: bool = False,
) -> dict[str, Any]:
    """Authorize only an immutable candidate with real replay and external credentials."""
    authoritative_path = (
        root / "docs" / "assurance" / "phase-d" / "redesign" / "corrected-freeze-v4.json"
    )
    authoritative = load_json(authoritative_path)
    if canonical_json(freeze) != canonical_json(authoritative):
        raise PhaseDRedesignError(
            "caller freeze substitution does not match the authoritative repository freeze"
        )
    if freeze.get("candidate_review_target") is not None:
        raise PhaseDRedesignError(
            "candidate review target must come from the external signed manifest, not an embedded repository freeze"
        )
    try:
        candidate_target = load_external_review_target(root, freeze)
    except PhaseDRedesignError:
        candidate_target = None
    replay = freeze.get("real_production_replay")
    blocked_reasons = []
    if not isinstance(candidate_target, dict):
        blocked_reasons.append("immutable_committed_candidate_review_target_absent")
    if not isinstance(replay, dict) or replay.get("status") != "implemented_and_verified":
        blocked_reasons.append("real_company_os_c2_replay_not_implemented")
    if blocked_reasons:
        if require_execution_authorization:
            raise PhaseDRedesignError(
                "Phase D execution remains blocked: " + ", ".join(blocked_reasons)
            )
        return {
            "status": "blocked_pending_immutable_review_target_and_real_replay",
            "execution_authorized": False,
            "blockers": blocked_reasons,
        }
    verify_immutable_review_target(root, candidate_target)
    credentials = load_trusted_governance_credentials(root, freeze)
    gate = freeze.get("execution_gate")
    if not isinstance(gate, dict):
        raise PhaseDRedesignError("v4 execution gate is invalid")
    reviewer_identity = gate.get("reviewer_identity")
    ceo_identity = gate.get("ceo_identity")
    if not isinstance(reviewer_identity, dict) or not isinstance(ceo_identity, dict):
        raise PhaseDRedesignError("v4 governance identities are not frozen")
    if approval is None:
        if require_execution_authorization:
            raise PhaseDRedesignError("Phase D requires authenticated independent approval")
        return {"status": "blocked_pending_authenticated_independent_approval", "execution_authorized": False}
    if approval.get("schema_version") != "phase-d-redesign-independent-approval/v4":
        raise PhaseDRedesignError("independent approval schema_version is invalid")
    _verify_governance_signature(approval, reviewer_identity, credentials)
    if (
        approval.get("decision") != "approve"
        or approval.get("freeze_id") != freeze.get("id")
        or approval.get("reviewer_principal") != reviewer_identity.get("principal_id")
        or approval.get("reviewer_role") != reviewer_identity.get("role")
    ):
        raise PhaseDRedesignError(
            "independent approval freeze identity, reviewer identity or decision is invalid"
        )
    author_principals = freeze.get("author_principals")
    if (
        not isinstance(author_principals, list)
        or not author_principals
        or not all(isinstance(item, str) and item for item in author_principals)
        or len(set(author_principals)) != len(author_principals)
        or approval.get("reviewer_principal") in author_principals
        or approval.get("reviewer_principal") == ceo_identity.get("principal_id")
    ):
        raise PhaseDRedesignError("independent approval reviewer is not independent")
    if _contains_unresolved_material_finding(approval.get("unresolved_findings")):
        raise PhaseDRedesignError("independent approval has unresolved Critical/High findings")
    if not isinstance(approval.get("unresolved_findings"), list):
        raise PhaseDRedesignError("independent approval unresolved findings must be a list")
    if approval.get("reviewed_target") != candidate_target:
        raise PhaseDRedesignError("independent approval does not bind the immutable candidate")
    approval_time = _parse_signed_at(approval.get("signed_at"), "independent approval")
    if ceo_decision is None:
        if require_execution_authorization:
            raise PhaseDRedesignError("Phase D requires a separate signed CEO start decision")
        return {"status": "blocked_pending_signed_ceo_start_decision", "execution_authorized": False}
    if ceo_decision.get("schema_version") != "phase-d-redesign-ceo-start-decision/v4":
        raise PhaseDRedesignError("CEO start decision schema_version is invalid")
    _verify_governance_signature(ceo_decision, ceo_identity, credentials)
    approval_hash = sha256_bytes(canonical_json(approval).encode("ascii"))
    if (
        ceo_decision.get("decision") != "start"
        or ceo_decision.get("effective_authorization") is not True
        or ceo_decision.get("freeze_id") != freeze.get("id")
        or ceo_decision.get("ceo_principal") != ceo_identity.get("principal_id")
        or ceo_decision.get("ceo_role") != ceo_identity.get("role")
        or ceo_decision.get("approved_target") != candidate_target
        or ceo_decision.get("approved_independent_approval_sha256") != approval_hash
    ):
        raise PhaseDRedesignError(
            "CEO start decision freeze identity is invalid or the decision is not fully bound"
        )
    if _parse_signed_at(ceo_decision.get("signed_at"), "CEO start decision") <= approval_time:
        raise PhaseDRedesignError("CEO start decision must be signed after independent approval")
    return {"status": "authorized_v4", "execution_authorized": True, "blockers": []}


def _verify_corrected_freeze_v3(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool,
    governance_credentials: dict[str, bytes] | None,
) -> dict[str, Any]:
    """Reject the self-referential V3 freeze semantics."""
    del (root, freeze_path, require_execution_approval, governance_credentials)
    raise PhaseDRedesignError(
        "V3 corrected freeze is superseded by V4 and cannot authorize or verify execution"
    )

def _verify_corrected_freeze_v4(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool,
    allow_development_overlay: bool,
) -> dict[str, Any]:
    freeze = load_json(freeze_path)
    if freeze.get("schema_version") != "phase-d-redesign-freeze/v4":
        raise PhaseDRedesignError("v4 corrected freeze schema_version is invalid")
    if freeze.get("execution_gate", {}).get("execution_authorized") is not False:
        raise PhaseDRedesignError("v4 corrected freeze must remain non-authorizing")
    baseline_target = freeze.get("baseline_review_target")
    if not isinstance(baseline_target, dict):
        raise PhaseDRedesignError("v4 baseline review target is invalid")
    if freeze.get("candidate_review_target") is not None:
        raise PhaseDRedesignError(
            "v4 candidate review target must be supplied by the external signed manifest"
        )
    try:
        candidate_target = load_external_review_target(root, freeze)
    except PhaseDRedesignError:
        candidate_target = None
    active_target = candidate_target if isinstance(candidate_target, dict) else baseline_target
    if allow_development_overlay:
        target_verification = _verify_git_object_target(root, active_target)
    else:
        target_verification = verify_immutable_review_target(root, active_target)

    protocol_inputs = freeze.get("protocol_inputs")
    expected_inputs = {
        "d1_contract": "docs/assurance/phase-d/redesign/d1/contract-v4.json",
        "d1_scenario_bank": "docs/assurance/phase-d/redesign/d1/scenario-bank-v3.json",
        "d2_contract": "docs/assurance/phase-d/redesign/d2/contract-v4.json",
        "d2_mutation_bank": "docs/assurance/phase-d/redesign/d2/mutation-bank-v4.json",
        "supersession_record": "docs/assurance/phase-d/redesign/supersession-record-v4.json",
    }
    if protocol_inputs != expected_inputs:
        raise PhaseDRedesignError("v4 freeze does not name the exact protocol inputs")
    documents: dict[str, dict[str, Any]] = {}
    for kind, relative in expected_inputs.items():
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise PhaseDRedesignError(f"v4 protocol input is not a regular file: {kind}")
        documents[kind] = load_json(path)
    if (
        documents["d1_contract"].get("schema_version") != "phase-d-d1-corrected-contract/v4"
        or documents["d2_contract"].get("schema_version") != "phase-d-d2-corrected-contract/v4"
        or documents["d2_contract"].get("treatment_pass_possible") is not False
        or documents["d2_contract"].get("real_production_replay", {}).get("status") != "not_implemented"
    ):
        raise PhaseDRedesignError("v4 contracts do not preserve the blocked state")
    validate_scenario_bank(root, root / expected_inputs["d1_scenario_bank"])
    validate_mutation_bank(
        root / expected_inputs["d2_mutation_bank"], documents["d2_contract"]
    )
    authorization = evaluate_v4_authorization(
        root,
        freeze,
        None,
        None,
        require_execution_authorization=require_execution_approval,
    )
    return {
        **authorization,
        "documents": documents,
        "target_verification": target_verification,
        "development_overlay": allow_development_overlay,
        "bound_paths": sorted(
            [freeze_path.relative_to(root).as_posix(), *expected_inputs.values()]
        ),
    }


def verify_corrected_freeze(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool = False,
    governance_credentials: dict[str, bytes] | None = None,
    allow_development_overlay: bool = False,
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
    if schema_version == "phase-d-redesign-freeze/v4":
        if governance_credentials is not None:
            raise PhaseDRedesignError("v4 rejects caller-provided governance credentials")
        return _verify_corrected_freeze_v4(
            root,
            freeze_path,
            require_execution_approval=require_execution_approval,
            allow_development_overlay=allow_development_overlay,
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


def render_bounded_artifact(scenario: dict[str, Any], source_bytes: bytes) -> bytes:
    """Reject the superseded D1 artifact-generation helper while V4 is blocked."""
    del (scenario, source_bytes)
    raise PhaseDRedesignError(
        "D1 artifact generation is blocked and the legacy renderer is superseded"
    )


def _render_structured_artifact(scenario: dict[str, Any], source_bytes: bytes) -> bytes:
    """Reject the superseded V3 structured artifact renderer while V4 is blocked."""
    del (scenario, source_bytes)
    raise PhaseDRedesignError(
        "D1 artifact generation is blocked and the V3 structured renderer is superseded"
    )


def execute_d1_workflow(
    scenario: dict[str, Any], run_spec: dict[str, Any]
) -> dict[str, Any]:
    """Reject the superseded D1 treatment workflow while Phase D remains blocked."""
    del (scenario, run_spec)
    raise PhaseDRedesignError(
        "D1 treatment workflow execution is blocked and the V3 helper is superseded"
    )

def validate_bounded_svg(artifact: bytes) -> dict[str, Any]:
    svg_namespace = "http://www.w3.org/2000/svg"
    try:
        root = ET.fromstring(artifact)
    except ET.ParseError as exc:
        raise PhaseDRedesignError(f"D1 artifact is not valid SVG: {exc}") from exc
    if root.tag != f"{{{svg_namespace}}}svg":
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
        number_pattern = re.compile(
            r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
        )
        matches = list(number_pattern.finditer(value))
        numbers = [float(match.group(0)) for match in matches]
        if not numbers or not all(math.isfinite(item) for item in numbers):
            raise ValueError("geometry contains no finite coordinates")
        cursor = 0
        for index, match in enumerate(matches):
            separator = value[cursor:match.start()]
            if index == 0:
                valid_separator = not separator.strip()
            else:
                valid_separator = bool(
                    re.fullmatch(r"(?:\s+|\s*,\s*|\s*,?\s+)", separator)
                )
            if not valid_separator:
                raise ValueError("geometry contains malformed separators")
            cursor = match.end()
        if value[cursor:].strip():
            raise ValueError("geometry contains malformed trailing tokens")
        return numbers

    def outside_canvas(x_min: float, y_min: float, x_max: float, y_max: float) -> bool:
        values = (x_min, y_min, x_max, y_max)
        return not all(math.isfinite(item) for item in values) or x_min < 0 or y_min < 0 or x_max > 512 or y_max > 512

    supported_elements = {
        "svg", "title", "rect", "circle", "ellipse", "line", "polyline",
        "polygon", "path",
    }
    supported_attributes = {
        "svg": {"width", "height", "viewBox", "data-safe-area"},
        "title": set(),
        "rect": {"x", "y", "width", "height", "fill", "stroke"},
        "circle": {"cx", "cy", "r", "fill", "stroke"},
        "ellipse": {"cx", "cy", "rx", "ry", "fill", "stroke"},
        "line": {"x1", "y1", "x2", "y2", "stroke"},
        "polyline": {"points", "fill", "stroke"},
        "polygon": {"points", "fill", "stroke"},
        "path": {"d", "fill", "stroke"},
    }

    def path_bounds(value: str) -> tuple[float, float, float, float]:
        number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
        token_pattern = re.compile(rf"[MLHVZmlhvz]|{number}")
        matches = list(token_pattern.finditer(value))
        if not matches:
            raise ValueError("path contains no tokens")
        tokens = [match.group(0) for match in matches]
        if tokens[0] != "M":
            raise ValueError("path must begin with an absolute moveto")
        if any(token in {"m", "l", "h", "v", "z"} for token in tokens):
            raise ValueError("relative paths are outside the supported subset")

        # This supported subset requires explicit whitespace or one comma between
        # coordinates, preventing the token extractor from hiding bad punctuation.
        cursor = 0
        previous: str | None = None
        commands = {"M", "L", "H", "V", "Z", "m", "l", "h", "v", "z"}
        for match in matches:
            token = match.group(0)
            separator = value[cursor:match.start()]
            current_is_command = token in commands
            previous_is_command = previous in commands if previous is not None else False
            if previous is None:
                valid_separator = not separator.strip()
            elif previous_is_command or current_is_command:
                valid_separator = not separator.strip()
            else:
                valid_separator = bool(
                    re.fullmatch(r"(?:\s+|\s*,\s*|\s*,?\s+)", separator)
                )
            if not valid_separator:
                raise ValueError("path contains malformed separators or tokens")
            previous = token
            cursor = match.end()
        if value[cursor:].strip():
            raise ValueError("path contains malformed trailing tokens")

        coordinates: list[tuple[float, float]] = []
        cursor_x = cursor_y = 0.0
        command: str | None = None
        command_pending = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"M", "L", "H", "V", "Z"}:
                if command_pending:
                    raise ValueError("path command has incomplete coordinates")
                command = token
                command_pending = command != "Z"
                index += 1
                if command == "Z":
                    command = None
                continue
            if command is None:
                raise ValueError("path coordinate has no command")
            arity = 2 if command in {"M", "L"} else 1
            if index + arity > len(tokens) or any(
                item in {"M", "L", "H", "V", "Z"}
                for item in tokens[index:index + arity]
            ):
                raise ValueError("path command has incomplete coordinates")
            numbers = [float(item) for item in tokens[index:index + arity]]
            if not all(math.isfinite(item) for item in numbers):
                raise ValueError("path contains non-finite coordinates")
            if command in {"M", "L"}:
                cursor_x, cursor_y = numbers
                if command == "M":
                    command = "L"
            elif command == "H":
                cursor_x = numbers[0]
            else:
                cursor_y = numbers[0]
            coordinates.append((cursor_x, cursor_y))
            command_pending = False
            index += arity
        if not coordinates or command_pending:
            raise ValueError("path has no provable coordinates")
        xs = [item[0] for item in coordinates]
        ys = [item[1] for item in coordinates]
        return min(xs), min(ys), max(xs), max(ys)

    for element in root.iter():
        namespace_prefix = f"{{{svg_namespace}}}"
        if not isinstance(element.tag, str) or not element.tag.startswith(namespace_prefix):
            overflow_reasons.append("unsupported_foreign_namespace_element")
            continue
        kind = element.tag[len(namespace_prefix):]
        if kind == "svg" and element is not root:
            overflow_reasons.append("unsupported_nested_svg_viewport")
            continue
        if kind not in supported_elements:
            overflow_reasons.append(f"unsupported_{kind}_geometry")
            continue
        if any("}" in key or key not in supported_attributes[kind] for key in element.attrib):
            overflow_reasons.append(f"unsupported_{kind}_attribute")
            continue
        if any(
            key == "transform"
            or key == "style"
            or key == "class"
            or "transform" in str(value).lower()
            for key, value in element.attrib.items()
        ):
            overflow_reasons.append(f"unsupported_{kind}_style_or_transform")
            continue
        unsupported_paint = False
        for paint_attribute in ("fill", "stroke"):
            paint = element.attrib.get(paint_attribute)
            if paint is not None and not re.fullmatch(
                r"(?:none|transparent|#[0-9a-fA-F]{3,4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8})",
                paint.strip(),
            ):
                overflow_reasons.append(f"unsupported_{kind}_{paint_attribute}_paint")
                unsupported_paint = True
        if unsupported_paint:
            continue
        stroke = element.attrib.get("stroke")
        if stroke is not None and stroke.strip().lower() not in {"", "none", "transparent"}:
            overflow_reasons.append(f"unsupported_{kind}_stroke_bounds")
            continue
        if kind == "rect":
            try:
                x = float(element.attrib.get("x", "0"))
                y = float(element.attrib.get("y", "0"))
                item_width = float(element.attrib["width"])
                item_height = float(element.attrib["height"])
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_rect_bounds")
                continue
            if item_width < 0 or item_height < 0 or outside_canvas(x, y, x + item_width, y + item_height):
                overflow_reasons.append("rect_overflow")
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
            try:
                bounds = path_bounds(path_data)
            except ValueError:
                overflow_reasons.append("unsupported_or_invalid_path_geometry")
                continue
            if outside_canvas(*bounds):
                overflow_reasons.append("path_overflow")
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
    """Reject the superseded D1 rater-delivery writer while V4 is blocked."""
    del (destination, scenario, option_a, option_b, rater_form)
    raise PhaseDRedesignError(
        "D1 rater delivery is blocked and the legacy bundle writer is superseded"
    )


def create_harness_fixture(destination: Path) -> None:
    """Reject the superseded surrogate D2 fixture."""
    del destination
    raise PhaseDRedesignError(
        "D2 surrogate harness fixture is superseded and blocked; real Company OS replay is required"
    )

def execute_mutation_pair(
    frozen_fixture: Path,
    case: dict[str, Any],
    baseline_workflow: dict[str, Any],
    treatment_workflow: dict[str, Any],
    evidence_root: Path,
) -> dict[str, Any]:
    """Reject the surrogate D2 treatment workflow while real replay is unavailable."""
    del (frozen_fixture, case, baseline_workflow, treatment_workflow, evidence_root)
    raise PhaseDRedesignError(
        "D2 treatment workflow execution is blocked and the surrogate V3 helper is superseded"
    )

def validate_mutation_bank(bank_path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate exact V2/V3 mutation coverage and mutually exclusive case roles."""
    bank = load_json(bank_path)
    if bank.get("schema_version") not in {
        "phase-d-d2-mutation-bank/v2",
        "phase-d-d2-mutation-bank/v3",
        "phase-d-d2-mutation-bank/v4",
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
        mutation = case.get("mutation")
        if bank.get("schema_version") == "phase-d-d2-mutation-bank/v4":
            replay = case.get("replay")
            if (
                case.get("target") != "production_control_replay"
                or not isinstance(mutation, dict)
                or mutation.get("kind") != "named_control_probe"
                or not isinstance(replay, dict)
                or not all(
                    isinstance(replay.get(field), str) and replay.get(field)
                    for field in ("control_id", "module", "entrypoint", "existing_regression_test")
                )
                or not str(replay.get("module")).startswith("agent_company.")
            ):
                raise PhaseDRedesignError(
                    f"D2 V4 real production control replay mapping is invalid: {case_id}"
                )
        elif case.get("target") not in {"database", "repository"} or not isinstance(mutation, dict):
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
        if bank.get("schema_version") == "phase-d-d2-mutation-bank/v4":
            if len(cases) != 16 or len(observed_classes) != 16:
                raise PhaseDRedesignError("D2 V4 mutation bank requires exactly sixteen named classes")
            control_ids = [str(case["replay"]["control_id"]) for case in cases]
            if len(set(control_ids)) != 16:
                raise PhaseDRedesignError("D2 V4 cases must target distinct real controls")
        elif len(cases) < 16:
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


def derive_d2_observation_thresholds(
    pairs: list[dict[str, Any]],
    *,
    contract: dict[str, Any] | None = None,
    bank: dict[str, Any] | None = None,
    real_replay_attestation: dict[str, Any] | None = None,
    authoritative_root: Path | None = None,
) -> dict[str, Any]:
    """Require every material fault denial independent of the baseline comparison."""
    real_replay_verifier = None
    if not pairs:
        raise PhaseDRedesignError("D2 threshold derivation requires paired observations")
    if contract is None or bank is None:
        raise PhaseDRedesignError(
            "D2 threshold derivation requires the exact contract and bank"
        )
    if contract is not None and bank is not None:
        if authoritative_root is None:
            raise PhaseDRedesignError(
                "D2 real production replay certification requires authoritative repository inputs"
            )
        authoritative_contract = load_json(
            authoritative_root / "docs" / "assurance" / "phase-d" / "redesign" / "d2" / "contract-v4.json"
        )
        authoritative_bank = load_json(
            authoritative_root / "docs" / "assurance" / "phase-d" / "redesign" / "d2" / "mutation-bank-v4.json"
        )
        if (
            canonical_json(contract) != canonical_json(authoritative_contract)
            or canonical_json(bank) != canonical_json(authoritative_bank)
        ):
            raise PhaseDRedesignError(
                "D2 real production replay contract or bank is not authoritative"
            )
        required_faults = contract.get("required_fault_classes")
        required_controls = contract.get("required_control_classes")
        cases = bank.get("cases")
        attestation_path = contract.get("real_production_replay", {}).get("attestation_path")
        if real_replay_attestation is None and isinstance(attestation_path, str) and attestation_path:
            attestation_file = authoritative_root / attestation_path
            if attestation_file.exists():
                real_replay_attestation = load_json(attestation_file)
        if (
            not isinstance(required_faults, list)
            or len(required_faults) != 13
            or len(set(required_faults)) != 13
            or not all(isinstance(item, str) and item for item in required_faults)
            or not isinstance(required_controls, list)
            or len(required_controls) != 3
            or len(set(required_controls)) != 3
            or not all(isinstance(item, str) and item for item in required_controls)
            or set(required_faults) & set(required_controls)
            or not isinstance(cases, list)
            or len(cases) != 16
        ):
            raise PhaseDRedesignError("D2 contract/bank must define exact 13+3 named classes")
        bank_by_id = {
            str(case.get("id")): case for case in cases if isinstance(case, dict)
        }
        if len(bank_by_id) != 16:
            raise PhaseDRedesignError("D2 contract/bank case ids are incomplete or duplicate")
        for case_id, frozen_case in bank_by_id.items():
            fault_class = frozen_case.get("fault_class")
            if fault_class in required_faults:
                if (
                    frozen_case.get("seeded_fault") is not True
                    or frozen_case.get("valid_control") is not False
                    or str(frozen_case.get("severity", "")).lower() not in {"critical", "high"}
                ):
                    raise PhaseDRedesignError(
                        f"D2 required material fault has the wrong role or severity: {case_id}"
                    )
            elif fault_class in required_controls:
                if (
                    frozen_case.get("seeded_fault") is not False
                    or frozen_case.get("valid_control") is not True
                ):
                    raise PhaseDRedesignError(
                        f"D2 required control has the wrong role: {case_id}"
                    )
            else:
                raise PhaseDRedesignError(f"D2 bank contains an unknown class: {case_id}")
        pair_by_id: dict[str, dict[str, Any]] = {}
        for pair in pairs:
            if not isinstance(pair, dict):
                raise PhaseDRedesignError("D2 observation is malformed")
            case_id = pair.get("case_id")
            if not isinstance(case_id, str) or case_id not in bank_by_id or case_id in pair_by_id:
                raise PhaseDRedesignError("D2 observations are incomplete, duplicate, or unknown")
            frozen_case = bank_by_id[case_id]
            expected_pair_fields = (set(frozen_case) - {"id"}) | {
                "case_id", "baseline", "treatment"
            }
            if set(pair) != expected_pair_fields:
                raise PhaseDRedesignError(
                    f"D2 observation fields do not match the frozen schema: {case_id}"
                )
            for field in ("fault_class", "severity", "seeded_fault", "valid_control"):
                if pair.get(field) != frozen_case.get(field):
                    raise PhaseDRedesignError(f"D2 observation does not match frozen case: {case_id}")
            for side in ("baseline", "treatment"):
                side_record = pair.get(side)
                observation = (
                    side_record.get("observation")
                    if isinstance(side_record, dict)
                    else None
                )
                if (
                    not isinstance(side_record, dict)
                    or set(side_record) != {"observation"}
                    or not isinstance(observation, dict)
                    or set(observation) != {"outcome"}
                ):
                    raise PhaseDRedesignError(
                        f"D2 observation fields are malformed: {case_id} {side}"
                    )
                outcome = observation.get("outcome")
                if outcome not in {"allowed", "denied"}:
                    raise PhaseDRedesignError(f"D2 observation outcome is malformed: {case_id} {side}")
            pair_by_id[case_id] = pair
        if set(pair_by_id) != set(bank_by_id):
            raise PhaseDRedesignError("D2 observations do not exactly cover the frozen bank")
        observed_classes = {str(pair["fault_class"]) for pair in pairs}
        if observed_classes != set(required_faults) | set(required_controls):
            raise PhaseDRedesignError("D2 observations do not cover the exact named classes")
        replay = contract.get("real_production_replay")
        if (
            not isinstance(replay, dict)
            or replay.get("status") != "implemented_and_verified"
            or not isinstance(real_replay_attestation, dict)
            or real_replay_attestation.get("schema_version") != "phase-d-d2-real-replay-attestation/v4"
            or real_replay_attestation.get("contract_id") != contract.get("id")
            or real_replay_attestation.get("bank_id") != bank.get("id")
            or real_replay_attestation.get("isolated_real_schema") is not True
            or real_replay_attestation.get("named_public_controls_invoked") is not True
            or real_replay_attestation.get("case_ids") != sorted(bank_by_id)
        ):
            raise PhaseDRedesignError(
                "D2 real production Company OS control replay is not implemented"
            )
        real_replay_verifier = globals().get("verify_real_company_os_c2_replay")
        if not callable(real_replay_verifier):
            raise PhaseDRedesignError(
                "D2 executable real production replay verifier is not implemented"
            )
        control_ids = [
            case.get("replay", {}).get("control_id") for case in cases
            if isinstance(case, dict)
        ]
        if (
            len(control_ids) != 16
            or not all(isinstance(item, str) and item for item in control_ids)
            or len(set(control_ids)) != 16
        ):
            raise PhaseDRedesignError("D2 real control replay mappings are incomplete or duplicate")
        replay_verification = real_replay_verifier(
            authoritative_root,
            contract,
            bank,
            real_replay_attestation,
            pairs,
        )
        if (
            not isinstance(replay_verification, dict)
            or replay_verification.get("verified") is not True
            or replay_verification.get("case_ids") != sorted(bank_by_id)
            or replay_verification.get("control_ids") != sorted(control_ids)
        ):
            raise PhaseDRedesignError(
                "D2 executable real production replay verification failed"
            )
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


def _v4_svg_protocol_canaries() -> list[dict[str, Any]]:
    prefix = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
        b'viewBox="0 0 512 512">'
    )
    cases = {
        "absolute_path_inside": b'<path d="M 10 10 L 500 10"/>',
        "relative_path_rejected": b'<path d="M 500 10 l 20 0"/>',
        "stroke_rejected": b'<rect x="0" y="0" width="512" height="512" stroke="#000"/>',
        "style_transform_rejected": b'<rect x="0" y="0" width="10" height="10" style="transform:translate(600px,0)"/>',
        "nested_text_rejected": b'<text x="10" y="20" font-size="12" data-max-width="100"><tspan>nested</tspan></text>',
        "use_defs_rejected": b'<defs><rect id="r" x="700" y="0" width="10" height="10"/></defs><use href="#r"/>',
        "curve_rejected": b'<path d="M 0 0 C 0 0 700 0 700 1"/>',
        "arc_rejected": b'<path d="M 0 0 A 700 700 0 0 0 10 10"/>',
        "malformed_rejected": b'<polygon points="0,0 10,wat"/>',
        "foreign_namespace_rejected": b'<evil:rect xmlns:evil="urn:evil" x="0" y="0" width="10" height="10"/>',
        "external_image_rejected": b'<image x="0" y="0" width="10" height="10" href="https://example.invalid/image.png"/>',
        "paint_server_rejected": b'<rect x="0" y="0" width="10" height="10" fill="url(https://example.invalid/p.svg#x)"/>',
        "malformed_path_separators_rejected": b'<path d="M,,10,,10 L 20 20"/>',
        "path_without_moveto_rejected": b'<path d="L 10 10"/>',
        "incomplete_moveto_rejected": b'<path d="M L 10 10"/>',
        "incomplete_lineto_rejected": b'<path d="M 10 10 L Z"/>',
        "nested_svg_rejected": b'<svg width="512" height="512" viewBox="0 0 1 1"><rect x="2" y="0" width="1" height="1"/></svg>',
        "embedded_svg_image_rejected": b'<image x="0" y="0" width="10" height="10" href="data:image/svg+xml;base64,PHN2Zy8+"/>',
        "embedded_raster_image_rejected": b'<image x="0" y="0" width="10" height="10" href="data:image/png;base64,iVBORw0KGgo="/>',
        "leaf_text_rejected": b'<text x="10" y="20" font-family="sans-serif" font-size="12" fill="#000000" textLength="40" lengthAdjust="spacingAndGlyphs">text</text>',
        "malformed_points_rejected": b'<polygon points="0,,0 10,10 20,20"/>',
    }
    records = []
    for case_id, body in cases.items():
        validation = validate_bounded_svg(prefix + body + b"</svg>")
        expected_bounded = case_id == "absolute_path_inside"
        if validation["bounded"] is not expected_bounded:
            raise PhaseDRedesignError(f"D1 SVG protocol canary failed: {case_id}")
        records.append({
            "case_id": case_id,
            "expected_bounded": expected_bounded,
            "observed_bounded": validation["bounded"],
            "overflow_reasons": validation["overflow_reasons"],
        })
    return records


def run_redesign_dry_run(
    root: Path,
    output: Path,
    *,
    freeze_path: Path | None = None,
    allow_development_overlay: bool = False,
) -> dict[str, Any]:
    """Run only explicit non-treatment V4 protocol checks while Phase D is blocked."""
    freeze = freeze_path or (
        root / "docs" / "assurance" / "phase-d" / "redesign" / "corrected-freeze-v4.json"
    )
    freeze_document = load_json(freeze)
    if freeze_document.get("schema_version") != "phase-d-redesign-freeze/v4":
        raise PhaseDRedesignError(
            "V2/V3 blocked dry-run paths are superseded because they execute treatment workflows"
        )
    if output.exists():
        raise PhaseDRedesignError("blocked protocol output must not already exist")
    verification = verify_corrected_freeze(
        root,
        freeze,
        allow_development_overlay=allow_development_overlay,
    )
    if verification["execution_authorized"]:
        raise PhaseDRedesignError("blocked protocol checks cannot run after authorization")
    documents = verification["documents"]
    scenario_bank = validate_scenario_bank(
        root, root / freeze_document["protocol_inputs"]["d1_scenario_bank"]
    )
    scenario_ids = [str(item["id"]) for item in scenario_bank["scenarios"]]
    assignments = balanced_blind_assignments(
        scenario_ids, str(scenario_bank["randomization_seed"])
    )
    if sum(item["A"] == "candidate" for item in assignments.values()) != len(assignments) // 2:
        raise PhaseDRedesignError("D1 static assignment protocol is not exactly balanced")
    svg_canaries = _v4_svg_protocol_canaries()
    mutation_bank = validate_mutation_bank(
        root / freeze_document["protocol_inputs"]["d2_mutation_bank"],
        documents["d2_contract"],
    )
    loader = unittest.TestLoader()
    for case in mutation_bank["cases"]:
        replay = case["replay"]
        try:
            module = importlib.import_module(str(replay["module"]))
            entrypoint: Any = module
            for part in str(replay["entrypoint"]).split("."):
                entrypoint = getattr(entrypoint, part)
        except (ImportError, AttributeError) as exc:
            raise PhaseDRedesignError(
                f"D2 named production control entrypoint is not resolvable: {case['id']}"
            ) from exc
        if not callable(entrypoint):
            raise PhaseDRedesignError(
                f"D2 named production control entrypoint is not callable: {case['id']}"
            )
        suite = loader.loadTestsFromName(str(replay["existing_regression_test"]))
        if loader.errors or suite.countTestCases() != 1:
            raise PhaseDRedesignError(
                f"D2 named production control regression is not resolvable: {case['id']}"
            )
    replay_mappings = [
        {
            "case_id": case["id"],
            "fault_class": case["fault_class"],
            "control_id": case["replay"]["control_id"],
            "module": case["replay"]["module"],
            "entrypoint": case["replay"]["entrypoint"],
            "existing_regression_test": case["replay"]["existing_regression_test"],
        }
        for case in mutation_bank["cases"]
    ]
    checks = [
        "immutable_review_target_protocol",
        "external_governance_registry_protocol",
        "d1_static_input_and_renderer_contract",
        "d1_svg_adversarial_validator_canaries",
        "d2_named_production_control_mapping",
        "d2_real_replay_blocker",
        "evidence_manifest_reproducibility_protocol",
    ]
    result = {
        "schema_version": "phase-d-redesign-blocked-protocol/v4",
        "status": "blocked_protocol_checks_complete",
        "corrected_treatments_executed": False,
        "phase_d_treatment_pass_possible": False,
        "authorization": {
            "status": verification["status"],
            "execution_authorized": False,
            "blockers": verification.get("blockers", []),
        },
        "checks_executed": checks,
        "d1": {
            "status": "blocked_static_protocol_verified",
            "scenario_contracts_checked": len(scenario_ids),
            "assignment_protocol_checked": True,
            "svg_validator_canaries": svg_canaries,
            "treatment_workflows_executed": 0,
            "artifacts_generated": 0,
            "ratings_collected": 0,
        },
        "d2": {
            "status": "blocked_real_production_control_replay_not_implemented",
            "named_control_mappings_checked": len(replay_mappings),
            "control_mappings": replay_mappings,
            "real_production_replay_status": "not_implemented",
            "treatment_workflows_executed": 0,
            "database_mutations_attempted": 0,
            "repository_mutations_attempted": 0,
            "observations_collected": 0,
            "thresholds_passed": False,
        },
        "forbidden_actions_observed": [],
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "protocol-result.json", result)
    write_json(output / "evidence-manifest.json", {
        "schema_version": "phase-d-redesign-evidence-manifest/v4",
        "status": "blocked_protocol_checks_complete",
        "corrected_treatments_executed": False,
        "phase_d_treatment_pass_possible": False,
        "artifacts": [{
            "path": "protocol-result.json",
            "bytes": (output / "protocol-result.json").stat().st_size,
            "sha256": sha256_file(output / "protocol-result.json"),
        }],
        "external_actions_observed": [],
    })
    return result


def verify_redesign_evidence(
    root: Path,
    expected_output: Path,
    *,
    freeze_path: Path | None = None,
    require_immutable_head: bool = True,
) -> dict[str, Any]:
    """Reproduce evidence in a temporary directory and compare without touching expected files."""
    expected_manifest = load_json(expected_output / "evidence-manifest.json")
    records = expected_manifest.get("artifacts")
    if not isinstance(records, list):
        raise PhaseDRedesignError("expected evidence manifest is malformed")
    manifest_paths: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise PhaseDRedesignError("expected evidence manifest record is malformed")
        relative = record.get("path")
        expected_hash = record.get("sha256")
        expected_bytes = record.get("bytes")
        if (
            not isinstance(relative, str)
            or not relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
            or relative in manifest_paths
            or not isinstance(expected_hash, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
            or not isinstance(expected_bytes, int)
            or expected_bytes < 0
        ):
            raise PhaseDRedesignError("expected evidence manifest record is malformed")
        artifact = expected_output / relative
        if (
            artifact.is_symlink()
            or not artifact.is_file()
            or artifact.stat().st_size != expected_bytes
            or sha256_file(artifact) != expected_hash
        ):
            raise PhaseDRedesignError(f"expected evidence hash mismatch or tamper: {relative}")
        manifest_paths.add(relative)
    observed_paths = {
        path.relative_to(expected_output).as_posix()
        for path in expected_output.rglob("*")
        if path.is_file() and path.name != "evidence-manifest.json"
    }
    if observed_paths != manifest_paths:
        raise PhaseDRedesignError("expected evidence manifest does not cover exact files")
    before = {
        path.relative_to(expected_output).as_posix(): sha256_file(path)
        for path in expected_output.rglob("*")
        if path.is_file()
    }
    with tempfile.TemporaryDirectory(prefix="phase-d-v4-verify-") as temporary:
        reproduced = Path(temporary) / "evidence"
        run_redesign_dry_run(
            root,
            reproduced,
            freeze_path=freeze_path,
            allow_development_overlay=not require_immutable_head,
        )
        reproduced_manifest = load_json(reproduced / "evidence-manifest.json")
    after = {
        path.relative_to(expected_output).as_posix(): sha256_file(path)
        for path in expected_output.rglob("*")
        if path.is_file()
    }
    if before != after:
        raise PhaseDRedesignError("frozen evidence changed during verification")
    if expected_manifest != reproduced_manifest:
        raise PhaseDRedesignError("reproduced evidence manifest or hashes do not match")
    return {
        "status": "evidence_reproduced",
        "manifest_sha256": sha256_file(expected_output / "evidence-manifest.json"),
        "expected_evidence_unchanged": True,
    }
