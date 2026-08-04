"""Shared fail-closed semantics for bound pilot completion records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .integrity import verify as verify_integrity_signature


class CompletionVerificationError(ValueError):
    """A completion record does not match its assurance provenance."""


COMPLETION_SIGNATURE_KEYS = (
    "task_id", "generation", "initiative_id", "artifact_set_sha256",
    "trusted_eval_result_sha256", "review_decision_ref",
    "review_content_sha256", "task_result_sha256",
    "evidence_paths_sha256", "completed_at", "created_at",
)
ARTIFACT_KEYS = {
    "schema_version", "artifact_id", "kind", "version", "status",
    "initiative_id", "profile", "risk_class", "owner_principal",
    "repository_id", "content",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _row_value(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _signed(
    db_path: Path, domain: str, values: dict[str, Any], supplied: Any,
) -> bool:
    try:
        return verify_integrity_signature(db_path, domain, values, supplied)
    except (OSError, TypeError, ValueError):
        return False


def _artifact_body(artifact: Any) -> dict[str, Any]:
    try:
        payload = json.loads(artifact["content_json"])
        digest = hashlib.sha256(_canonical(payload).encode("ascii")).hexdigest()
    except (KeyError, TypeError, UnicodeEncodeError, json.JSONDecodeError) as exc:
        raise CompletionVerificationError(
            "bound pilot assurance artifact body is invalid"
        ) from exc
    if digest != artifact["content_sha256"]:
        raise CompletionVerificationError(
            "bound pilot assurance artifact body hash is invalid"
        )
    return payload


def _registration_valid(conn: Any, db_path: Path, artifact: Any) -> bool:
    registration = conn.execute(
        "SELECT * FROM assurance_artifact_registrations "
        "WHERE artifact_id=? AND version=?",
        (artifact["artifact_id"], artifact["version"]),
    ).fetchone()
    if registration is None:
        return False
    values = {
        "artifact_id": registration["artifact_id"],
        "version": registration["version"],
        "content_sha256": registration["content_sha256"],
        "created_at": registration["created_at"],
    }
    return bool(
        registration["content_sha256"] == artifact["content_sha256"]
        and _signed(
            db_path, "artifact-registration", values,
            registration["integrity_signature"],
        )
    )


def _lifecycle_valid(conn: Any, db_path: Path, artifact: Any) -> bool:
    rows = conn.execute(
        "SELECT * FROM assurance_artifact_lifecycle "
        "WHERE artifact_id=? AND version=? ORDER BY sequence",
        (artifact["artifact_id"], artifact["version"]),
    ).fetchall()
    if not rows:
        return False
    previous_status = None
    previous_signature = None
    for expected_sequence, row in enumerate(rows, 1):
        values = {
            "artifact_id": row["artifact_id"],
            "version": row["version"],
            "sequence": row["sequence"],
            "from_status": row["from_status"],
            "to_status": row["to_status"],
            "transitioned_at": row["transitioned_at"],
            "actor_principal": row["actor_principal"],
            "reason": row["reason"],
            "previous_signature": row["previous_signature"],
        }
        if (
            row["sequence"] != expected_sequence
            or row["from_status"] != previous_status
            or row["previous_signature"] != previous_signature
            or not _signed(
                db_path, "artifact-lifecycle", values,
                row["integrity_signature"],
            )
        ):
            return False
        previous_status = row["to_status"]
        previous_signature = row["integrity_signature"]
    return previous_status == artifact["status"]


def _approval_valid(conn: Any, db_path: Path, artifact: Any) -> bool:
    approval = conn.execute(
        "SELECT * FROM assurance_artifact_approvals "
        "WHERE artifact_id=? AND version=?",
        (artifact["artifact_id"], artifact["version"]),
    ).fetchone()
    if approval is None:
        return False
    values = {
        "artifact_id": approval["artifact_id"],
        "version": approval["version"],
        "content_sha256": approval["content_sha256"],
        "approved_by_principal": approval["approved_by_principal"],
        "approved_at": approval["approved_at"],
    }
    return bool(
        artifact["approved_by_principal"]
        and artifact["approved_at"]
        and approval["content_sha256"] == artifact["content_sha256"]
        and approval["approved_by_principal"] == artifact["approved_by_principal"]
        and approval["approved_at"] == artifact["approved_at"]
        and _signed(
            db_path, "artifact-approval", values, approval["integrity_signature"],
        )
    )


def _approval_audited(conn: Any, artifact: Any) -> bool:
    reference = f"{artifact['artifact_id']}:v{artifact['version']}"
    for row in conn.execute(
        "SELECT details FROM audit_log WHERE action='assurance_artifact_approved' "
        "AND entity='assurance_artifact' AND entity_id=?",
        (reference,),
    ):
        try:
            details = json.loads(row["details"])
        except (TypeError, json.JSONDecodeError):
            continue
        if details.get("principal_id") == artifact["approved_by_principal"]:
            return True
    return False


def _trusted_eval_run_values(run: Any) -> tuple[dict[str, str], dict[str, Any]]:
    refs = {
        "candidate": run["candidate_sha256"],
        "dataset": run["dataset_sha256"],
        "grader": run["grader_sha256"],
        "environment": run["environment_sha256"],
    }
    values = {
        "initiative_id": run["initiative_id"],
        "attempt": run["attempt"],
        "refs": refs,
        "seed": run["seed"],
        "status": run["status"],
        "evidence_ref": run["evidence_ref"],
        "evidence_sha256": run["evidence_sha256"],
    }
    return refs, values


def _trusted_eval_contract_values(contract: Any) -> dict[str, Any]:
    return {
        "initiative_id": contract["initiative_id"],
        "max_attempts": contract["max_attempts"],
        "created_at": contract["created_at"],
    }


def _trusted_eval_provenance_values(provenance: Any) -> dict[str, Any]:
    return {
        "principal_id": provenance["principal_id"],
        "sequence": provenance["sequence"],
        "actor": provenance["actor"],
        "authority": provenance["authority"],
        "credential_sha256": provenance["credential_sha256"],
        "principal_created_at": provenance["principal_created_at"],
        "issued_at": provenance["issued_at"],
        "previous_signature": provenance["previous_signature"],
    }


def _trusted_eval_created_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval creation lineage is invalid"
        )
    try:
        created_at = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CompletionVerificationError(
            "bound pilot Trusted Eval creation lineage is invalid"
        ) from exc
    if (
        created_at.tzinfo is None
        or created_at.utcoffset() != timezone.utc.utcoffset(created_at)
        or created_at.microsecond
        or created_at.isoformat() != value
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval creation lineage is invalid"
        )
    return created_at


def _trusted_eval_manifest_valid(
    conn: Any, workspace: Path, kind: str, manifest_sha256: str,
    run_created_at: datetime,
) -> None:
    manifest = conn.execute(
        "SELECT * FROM trusted_eval_manifests "
        "WHERE kind=? AND manifest_sha256=?",
        (kind, manifest_sha256),
    ).fetchone()
    if manifest is None:
        raise CompletionVerificationError(
            f"bound pilot Trusted Eval {kind} manifest is invalid"
        )
    try:
        payload = json.loads(manifest["manifest_json"])
        manifest_digest = hashlib.sha256(
            _canonical(payload).encode("ascii")
        ).hexdigest()
    except (KeyError, TypeError, UnicodeEncodeError, json.JSONDecodeError) as exc:
        raise CompletionVerificationError(
            f"bound pilot Trusted Eval {kind} manifest is invalid"
        ) from exc
    if (
        manifest_digest != manifest_sha256
        or manifest["manifest_json"] != _canonical(payload)
        or manifest["manifest_sha256"] != manifest_sha256
        or _trusted_eval_created_at(manifest["created_at"]) > run_created_at
        or manifest["protected"] not in {0, 1}
        or payload != {
            "schema_version": f"trusted-eval-{kind}/v1",
            "id": manifest["manifest_id"],
            "content_sha256": manifest["content_sha256"],
            "protected": bool(manifest["protected"]),
        }
    ):
        raise CompletionVerificationError(
            f"bound pilot Trusted Eval {kind} manifest lineage is invalid"
        )
    content = workspace / "data" / "trusted-eval-content" / manifest["content_sha256"]
    if (
        not content.is_file()
        or hashlib.sha256(content.read_bytes()).hexdigest()
        != manifest["content_sha256"]
    ):
        raise CompletionVerificationError(
            f"bound pilot Trusted Eval {kind} content hash is invalid"
        )


def _trusted_eval_run_valid(
    conn: Any, db_path: Path, workspace: Path, initiative_id: str, run: Any,
    contract: Any,
) -> tuple[str, set[str], datetime]:
    if (
        run["initiative_id"] != initiative_id
        or type(run["attempt"]) is not int
        or run["attempt"] < 1
        or run["status"] not in {"failed", "abandoned", "completed"}
        or type(run["seed"]) is not int
        or not isinstance(run["evidence_ref"], str)
        or not run["evidence_ref"].strip()
        or not isinstance(run["evidence_sha256"], str)
        or len(run["evidence_sha256"]) != 64
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval run state is invalid"
        )
    created_at = _trusted_eval_created_at(run["created_at"])
    refs, result_values = _trusted_eval_run_values(run)
    signed_values = {
        **result_values,
        "evaluator_principal_id": run["evaluator_principal_id"],
        "evaluator_actor": run["evaluator_actor"],
        "evaluator_authority": run["evaluator_authority"],
        "evaluator_credential_sha256": run["evaluator_credential_sha256"],
        "evaluator_principal_created_at": run[
            "evaluator_principal_created_at"
        ],
        "evaluator_provenance_signature": run[
            "evaluator_provenance_signature"
        ],
        "contract_integrity_signature": run["contract_integrity_signature"],
        "result_sha256": run["result_sha256"],
        "created_at": run["created_at"],
    }
    if not _signed(
        db_path, "trusted-eval-run", signed_values, run["integrity_signature"],
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval integrity anchor is invalid"
        )
    provenance = conn.execute(
        "SELECT * FROM trusted_eval_evaluator_credentials "
        "WHERE integrity_signature=?",
        (run["evaluator_provenance_signature"],),
    ).fetchone()
    if provenance is None:
        raise CompletionVerificationError(
            "bound pilot Trusted Eval evaluator credential provenance is invalid"
        )
    provenance_values = _trusted_eval_provenance_values(provenance)
    issued_at = _trusted_eval_created_at(provenance["issued_at"])
    principal_created_at = _trusted_eval_created_at(
        provenance["principal_created_at"]
    )
    run_provenance = {
        "principal_id": run["evaluator_principal_id"],
        "sequence": provenance["sequence"],
        "actor": run["evaluator_actor"],
        "authority": run["evaluator_authority"],
        "credential_sha256": run["evaluator_credential_sha256"],
        "principal_created_at": run["evaluator_principal_created_at"],
        "issued_at": provenance["issued_at"],
        "previous_signature": provenance["previous_signature"],
    }
    if (
        provenance_values != run_provenance
        or not _signed(
            db_path, "trusted-eval-evaluator-provenance", provenance_values,
            provenance["integrity_signature"],
        )
        or principal_created_at > issued_at
        or issued_at > created_at
        or run["evaluator_actor"] != "Trusted Evaluator"
        or run["evaluator_authority"] != "operator"
        or not isinstance(run["evaluator_credential_sha256"], str)
        or len(run["evaluator_credential_sha256"]) != 64
        or run["contract_integrity_signature"]
        != contract["integrity_signature"]
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval evaluator credential provenance is invalid"
        )
    evaluator = conn.execute(
        "SELECT actor,authority,status,credential_sha256,created_at "
        "FROM assurance_principals "
        "WHERE principal_id=?",
        (run["evaluator_principal_id"],),
    ).fetchone()
    if (
        evaluator is None
        or evaluator["actor"] != "Trusted Evaluator"
        or evaluator["authority"] != "operator"
        or evaluator["status"] != "active"
        or not evaluator["credential_sha256"]
        or evaluator["created_at"] != run["evaluator_principal_created_at"]
        or _trusted_eval_created_at(evaluator["created_at"]) > created_at
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval evaluator principal authority or credential "
            "provenance is invalid"
        )
    current_provenance = conn.execute(
        "SELECT * FROM trusted_eval_evaluator_credentials "
        "WHERE principal_id=? AND credential_sha256=? "
        "AND principal_created_at=?",
        (
            run["evaluator_principal_id"], evaluator["credential_sha256"],
            evaluator["created_at"],
        ),
    ).fetchone()
    if current_provenance is None:
        raise CompletionVerificationError(
            "bound pilot Trusted Eval current evaluator credential issuance is invalid"
        )
    current_values = _trusted_eval_provenance_values(current_provenance)
    chain = conn.execute(
        "SELECT * FROM trusted_eval_evaluator_credentials "
        "WHERE principal_id=? ORDER BY sequence",
        (run["evaluator_principal_id"],),
    ).fetchall()
    previous_signature = None
    previous_issued_at = _trusted_eval_created_at(evaluator["created_at"])
    chain_valid = bool(chain)
    for sequence, entry in enumerate(chain, 1):
        values = _trusted_eval_provenance_values(entry)
        issued_at = _trusted_eval_created_at(entry["issued_at"])
        chain_valid = bool(
            chain_valid
            and entry["sequence"] == sequence
            and entry["previous_signature"] == previous_signature
            and entry["actor"] == "Trusted Evaluator"
            and entry["authority"] == "operator"
            and entry["principal_created_at"] == evaluator["created_at"]
            and issued_at >= previous_issued_at
            and _signed(
                db_path, "trusted-eval-evaluator-provenance", values,
                entry["integrity_signature"],
            )
        )
        previous_signature = entry["integrity_signature"]
        previous_issued_at = issued_at
    if (
        current_provenance["actor"] != evaluator["actor"]
        or current_provenance["authority"] != evaluator["authority"]
        or not _signed(
            db_path, "trusted-eval-evaluator-provenance", current_values,
            current_provenance["integrity_signature"],
        )
        or _trusted_eval_created_at(current_provenance["principal_created_at"])
        > _trusted_eval_created_at(current_provenance["issued_at"])
        or not chain_valid
        or current_provenance["integrity_signature"] != previous_signature
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval current evaluator credential issuance is invalid"
        )
    try:
        evidence_ref = Path(run["evidence_ref"])
        if evidence_ref.is_absolute():
            raise CompletionVerificationError(
                "bound pilot Trusted Eval evidence reference is invalid"
            )
        evidence = (workspace / evidence_ref).resolve()
        workspace_resolved = workspace.resolve()
    except (OSError, TypeError) as exc:
        raise CompletionVerificationError(
            "bound pilot Trusted Eval evidence reference is invalid"
        ) from exc
    if (
        workspace_resolved not in evidence.parents
        or not evidence.is_file()
        or hashlib.sha256(evidence.read_bytes()).hexdigest()
        != run["evidence_sha256"]
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval evidence hash is invalid"
        )
    for kind, manifest_sha256 in refs.items():
        if not isinstance(manifest_sha256, str) or len(manifest_sha256) != 64:
            raise CompletionVerificationError(
                f"bound pilot Trusted Eval {kind} manifest is invalid"
            )
        _trusted_eval_manifest_valid(
            conn, workspace, kind, manifest_sha256, created_at,
        )
    if (
        not isinstance(run["result_sha256"], str)
        or len(run["result_sha256"]) != 64
        or _sha256_text(_canonical(result_values)) != run["result_sha256"]
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval result lineage is invalid"
        )
    return (
        str(run["result_sha256"]),
        {str(run["evaluator_principal_id"])},
        created_at,
    )


def _trusted_eval_ledger(
    conn: Any, db_path: Path, workspace: Path, initiative_id: str,
) -> tuple[Any, set[str]]:
    contract = conn.execute(
        "SELECT * FROM trusted_eval_contracts "
        "WHERE initiative_id=?",
        (initiative_id,),
    ).fetchone()
    if (
        contract is None
        or contract["initiative_id"] != initiative_id
        or type(contract["max_attempts"]) is not int
        or not 1 <= contract["max_attempts"] <= 3
        or not _signed(
            db_path, "trusted-eval-contract",
            _trusted_eval_contract_values(contract),
            contract["integrity_signature"],
        )
    ):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval attempt contract is invalid"
        )
    contract_created_at = _trusted_eval_created_at(contract["created_at"])
    rows = conn.execute(
        "SELECT * FROM trusted_eval_runs WHERE initiative_id=? "
        "ORDER BY attempt,id",
        (initiative_id,),
    ).fetchall()
    if not rows or len(rows) > contract["max_attempts"]:
        raise CompletionVerificationError(
            "bound pilot Trusted Eval attempt ledger is invalid"
        )
    attempts = [row["attempt"] for row in rows]
    if attempts != list(range(1, len(rows) + 1)):
        raise CompletionVerificationError(
            "bound pilot Trusted Eval attempt ledger is not contiguous"
        )
    evaluator_principals: set[str] = set()
    previous_created_at = contract_created_at
    for row in rows:
        _, principals, created_at = _trusted_eval_run_valid(
            conn, db_path, workspace, initiative_id, row, contract,
        )
        if created_at < previous_created_at:
            raise CompletionVerificationError(
                "bound pilot Trusted Eval run creation lineage is invalid"
            )
        previous_created_at = created_at
        evaluator_principals.update(principals)
    latest = rows[-1]
    if latest["status"] != "completed":
        raise CompletionVerificationError(
            "bound pilot Trusted Eval latest attempt is not completed"
        )
    return latest, evaluator_principals


def _trusted_eval_result(
    conn: Any, db_path: Path, workspace: Path, initiative_id: str,
) -> tuple[str, set[str]]:
    required_tables = {
        "trusted_eval_runs", "trusted_eval_quarantines",
        "trusted_eval_manifests", "trusted_eval_contracts",
        "trusted_eval_evaluator_credentials",
    }
    available = {
        row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if not required_tables <= available:
        raise CompletionVerificationError(
            "bound pilot completion requires a valid Trusted Eval"
        )
    if conn.execute(
        "SELECT 1 FROM trusted_eval_quarantines WHERE initiative_id=?",
        (initiative_id,),
    ).fetchone():
        raise CompletionVerificationError("bound pilot Trusted Eval is quarantined")
    run, evaluator_principals = _trusted_eval_ledger(
        conn, db_path, workspace, initiative_id,
    )
    return str(run["result_sha256"]), evaluator_principals


def completion_assurance(
    conn: Any, db_path: Path, workspace: Path, task: Mapping[str, Any],
    binding: Any,
) -> tuple[dict[str, str], str]:
    """Return the one exact completion assurance object and review body hash."""
    initiative_id = str(binding["initiative_id"])
    initiative = conn.execute(
        "SELECT profile,risk_class,status,mode FROM assurance_initiatives "
        "WHERE initiative_id=?",
        (initiative_id,),
    ).fetchone()
    if (
        initiative is None
        or initiative["risk_class"] not in {"C2", "C3"}
        or initiative["status"] not in {
            "approved_for_build", "implementation", "independent_evaluation",
        }
        or initiative["mode"] != "pilot"
    ):
        raise CompletionVerificationError(
            "bound pilot completion requires C2/C3 executable pilot assurance"
        )
    artifacts = conn.execute(
        "SELECT * FROM assurance_artifacts WHERE initiative_id=? ORDER BY id",
        (initiative_id,),
    ).fetchall()
    payloads: dict[tuple[str, int], dict[str, Any]] = {}
    for artifact in artifacts:
        payload = _artifact_body(artifact)
        if (
            set(payload) != ARTIFACT_KEYS
            or payload.get("schema_version") != "assurance-artifact/v1"
            or payload.get("artifact_id") != artifact["artifact_id"]
            or payload.get("version") != artifact["version"]
            or payload.get("kind") != artifact["kind"]
            or payload.get("status") != "draft"
            or payload.get("owner_principal") != artifact["owner_principal"]
            or payload.get("initiative_id") != initiative_id
            or payload.get("profile") != artifact["profile"]
            or payload.get("risk_class") != artifact["risk_class"]
            or artifact["profile"] != initiative["profile"]
            or artifact["risk_class"] != initiative["risk_class"]
            or payload.get("repository_id") != artifact["repository_id"]
            or not isinstance(payload.get("content"), dict)
            or not payload["content"]
            or not _registration_valid(conn, db_path, artifact)
            or not _lifecycle_valid(conn, db_path, artifact)
        ):
            raise CompletionVerificationError(
                "bound pilot assurance lifecycle or integrity conflict"
            )
        if artifact["status"] == "approved" and not _approval_valid(
            conn, db_path, artifact,
        ):
            raise CompletionVerificationError(
                "bound pilot assurance artifact approval anchor is invalid"
            )
        payloads[(artifact["artifact_id"], artifact["version"])] = payload
    build_artifacts = sorted(
        (
            artifact for artifact in artifacts
            if artifact["status"] == "approved"
            and artifact["kind"] != "review_decision"
        ),
        key=lambda artifact: (artifact["artifact_id"], artifact["version"]),
    )
    artifact_set = [
        {
            "ref": f"{artifact['artifact_id']}:v{artifact['version']}",
            "sha256": artifact["content_sha256"],
        }
        for artifact in build_artifacts
    ]
    build_hash = _sha256_text(_canonical(artifact_set))
    build_gate = conn.execute(
        "SELECT decision,artifact_set_sha256,expires_at FROM assurance_gate_decisions "
        "WHERE initiative_id=? AND gate='G4' ORDER BY id DESC LIMIT 1",
        (initiative_id,),
    ).fetchone()
    if (
        build_gate is None
        or build_gate["decision"] not in {"pass", "pass_with_conditions"}
        or not binding["artifact_set_sha256"]
        or binding["artifact_set_sha256"] != build_hash
        or binding["artifact_set_sha256"] != build_gate["artifact_set_sha256"]
    ):
        raise CompletionVerificationError(
            "bound pilot artifact set is stale or mismatched"
        )
    if build_gate["expires_at"]:
        try:
            expiry = datetime.fromisoformat(build_gate["expires_at"])
        except (TypeError, ValueError) as exc:
            raise CompletionVerificationError(
                "bound pilot build decision expiry is invalid"
            ) from exc
        if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
            raise CompletionVerificationError("bound pilot build decision expired")
    result_sha256, evaluator_principals = _trusted_eval_result(
        conn, db_path, workspace, initiative_id,
    )
    reviews = [
        artifact for artifact in artifacts
        if artifact["kind"] == "review_decision" and artifact["status"] == "approved"
    ]
    if not reviews:
        raise CompletionVerificationError(
            "bound pilot completion requires an affirmative independent Review Decision"
        )
    build_owners = {
        artifact["owner_principal"] for artifact in artifacts
        if artifact["kind"] != "review_decision" and artifact["status"] == "approved"
    }
    task_principals = conn.execute(
        "SELECT principal_id FROM assurance_principals "
        "WHERE actor=? AND status='active'",
        (task["owner"],),
    ).fetchall()
    if len(task_principals) != 1:
        raise CompletionVerificationError(
            "bound pilot task owner principal lineage is invalid"
        )
    task_principal = task_principals[0]["principal_id"]
    exact_review = None
    for review in reviews:
        if not _approval_audited(conn, review):
            raise CompletionVerificationError(
                "Review Decision approval audit lineage is invalid"
            )
        reviewer = conn.execute(
            "SELECT authority,status,credential_sha256 FROM assurance_principals "
            "WHERE principal_id=?",
            (review["owner_principal"],),
        ).fetchone()
        approver = conn.execute(
            "SELECT authority,status,credential_sha256 FROM assurance_principals "
            "WHERE principal_id=?",
            (review["approved_by_principal"],),
        ).fetchone()
        if (
            reviewer is None
            or reviewer["status"] != "active"
            or reviewer["authority"] != "reviewer"
            or not reviewer["credential_sha256"]
            or review["owner_principal"] in build_owners
            or review["owner_principal"] in evaluator_principals
            or review["owner_principal"] == task_principal
        ):
            raise CompletionVerificationError("Review Decision is not independent")
        if (
            approver is None
            or approver["status"] != "active"
            or approver["authority"] not in {"executive", "chairman", "reviewer"}
            or not approver["credential_sha256"]
            or review["approved_by_principal"] == review["owner_principal"]
        ):
            raise CompletionVerificationError(
                "Review Decision approval metadata is invalid"
            )
        content = payloads[(review["artifact_id"], review["version"])].get("content")
        if (
            not isinstance(content, dict)
            or set(content) != {"decision", "findings", "evidence_refs"}
            or not isinstance(content.get("decision"), str)
            or not isinstance(content.get("findings"), list)
            or not all(
                isinstance(item, (str, dict)) for item in content.get("findings", [])
            )
        ):
            raise CompletionVerificationError("Review Decision body is invalid")
        refs = content.get("evidence_refs")
        if (
            not isinstance(refs, list)
            or not all(isinstance(item, str) for item in refs)
        ):
            raise CompletionVerificationError("Review Decision evidence refs are invalid")
        if result_sha256 not in refs:
            raise CompletionVerificationError(
                "Review Decision does not bind the exact Trusted Eval result"
            )
        if binding["artifact_set_sha256"] not in refs:
            raise CompletionVerificationError(
                "Review Decision does not bind the exact artifact set"
            )
        if (
            content.get("decision") not in {"approve", "pass"}
            or content.get("findings") != []
        ):
            raise CompletionVerificationError(
                "Review Decision is not affirmative or has contradictory findings"
            )
        exact_review = review
    if exact_review is None:
        raise CompletionVerificationError(
            "bound pilot completion lacks an exact Review Decision"
        )
    return ({
        "initiative_id": initiative_id,
        "artifact_set_sha256": str(binding["artifact_set_sha256"]),
        "result_sha256": result_sha256,
        "review_decision_ref": (
            f"{exact_review['artifact_id']}:v{exact_review['version']}"
        ),
    }, str(exact_review["content_sha256"]))


def completion_binding_valid(
    conn: Any, db_path: Path, workspace: Path, completion: Any, *,
    stage: str,
    task_result_override: str | None = None,
    task_updated_at_override: str | None = None,
) -> bool:
    """Validate one insert candidate or persisted completion with identical semantics."""
    try:
        if completion is None or stage not in {"insert", "persisted", "task_update"}:
            return False
        values = {key: completion[key] for key in COMPLETION_SIGNATURE_KEYS}
        if (
            any(len(str(values[key])) != 64 for key in (
                "artifact_set_sha256", "trusted_eval_result_sha256",
                "review_content_sha256", "task_result_sha256",
                "evidence_paths_sha256",
            ))
            or not _signed(
                db_path, "completion-binding", values,
                completion["integrity_signature"],
            )
        ):
            return False
        task = conn.execute(
            "SELECT * FROM tasks WHERE id=?", (completion["task_id"],),
        ).fetchone()
        execution = conn.execute(
            "SELECT * FROM task_executions WHERE task_id=?",
            (completion["task_id"],),
        ).fetchone()
        binding = conn.execute(
            "SELECT * FROM assurance_task_bindings WHERE task_id=?",
            (completion["task_id"],),
        ).fetchone()
        claim = conn.execute(
            "SELECT * FROM assurance_claim_bindings WHERE task_id=? AND generation=?",
            (completion["task_id"], completion["generation"]),
        ).fetchone()
        history = conn.execute(
            "SELECT * FROM assurance_pilot_claim_history WHERE task_id=? AND generation=?",
            (completion["task_id"], completion["generation"]),
        ).fetchone()
        if any(row is None for row in (task, execution, binding, claim, history)):
            return False
        claim_values = {
            key: claim[key] for key in (
                "task_id", "generation", "initiative_id", "artifact_set_sha256",
                "fencing_token_sha256", "created_at",
            )
        }
        history_values = {key: history[key] for key in claim_values}
        current_token_sha256 = _sha256_text(str(execution["fencing_token"] or ""))
        if (
            claim_values != history_values
            or not _signed(
                db_path, "claim-binding", claim_values,
                claim["integrity_signature"],
            )
            or not _signed(
                db_path, "pilot-claim-history", history_values,
                history["integrity_signature"],
            )
            or claim["fencing_token_sha256"] != current_token_sha256
            or claim["initiative_id"] != completion["initiative_id"]
            or claim["artifact_set_sha256"] != completion["artifact_set_sha256"]
        ):
            return False
        assurance, review_content_sha256 = completion_assurance(
            conn, db_path, workspace, task, binding,
        )
        candidate_assurance = {
            "initiative_id": completion["initiative_id"],
            "artifact_set_sha256": completion["artifact_set_sha256"],
            "result_sha256": completion["trusted_eval_result_sha256"],
            "review_decision_ref": completion["review_decision_ref"],
        }
        if (
            assurance != candidate_assurance
            or review_content_sha256 != completion["review_content_sha256"]
            or int(execution["generation"]) != int(completion["generation"])
            or binding["pilot"] != 1
            or binding["initiative_id"] != completion["initiative_id"]
            or binding["artifact_set_sha256"] != completion["artifact_set_sha256"]
        ):
            return False
        task_result_snapshot = _row_value(completion, "task_result_json")
        evidence_snapshot = _row_value(completion, "evidence_paths_json")
        if not isinstance(task_result_snapshot, str) or not isinstance(evidence_snapshot, str):
            return False
        if (
            _sha256_text(task_result_snapshot) != completion["task_result_sha256"]
            or _sha256_text(evidence_snapshot) != completion["evidence_paths_sha256"]
        ):
            return False
        task_result = json.loads(task_result_snapshot)
        evidence_paths = json.loads(evidence_snapshot)
        if (
            not isinstance(task_result, dict)
            or set(task_result.get("assurance", {})) != {
                "initiative_id", "artifact_set_sha256", "result_sha256",
                "review_decision_ref",
            }
            or task_result.get("assurance") != assurance
            or not isinstance(task_result.get("evidence"), list)
            or not all(isinstance(item, str) for item in task_result["evidence"])
            or task_result["evidence"] != evidence_paths
        ):
            return False
        if stage == "insert":
            return bool(
                task["status"] == "in_progress"
                and execution["recovery_status"] == "running"
                and binding["completion_result_sha256"] is None
                and binding["review_decision_ref"] is None
                and binding["completed_at"] is None
            )
        stored_task_result = (
            task_result_override if stage == "task_update" else task["result"]
        )
        stored_task_updated_at = (
            task_updated_at_override
            if stage == "task_update" else task["updated_at"]
        )
        stored_task_status = "done" if stage == "task_update" else task["status"]
        persisted_task_result = json.loads(stored_task_result)
        persisted_evidence = json.loads(execution["evidence_paths"])
        return bool(
            stored_task_status == "done"
            and execution["recovery_status"] == "completed"
            and binding["completion_result_sha256"]
                == completion["trusted_eval_result_sha256"]
            and binding["review_decision_ref"] == completion["review_decision_ref"]
            and binding["completed_at"] == completion["completed_at"]
            and stored_task_updated_at == completion["completed_at"]
            and execution["updated_at"] == completion["completed_at"]
            and _sha256_text(stored_task_result) == completion["task_result_sha256"]
            and _sha256_text(execution["evidence_paths"])
                == completion["evidence_paths_sha256"]
            and persisted_task_result == task_result
            and persisted_evidence == evidence_paths
            and persisted_task_result.get("evidence") == persisted_evidence
        )
    except (
        CompletionVerificationError, IndexError, KeyError, OSError, TypeError,
        ValueError, json.JSONDecodeError, sqlite3.Error,
    ):
        return False
