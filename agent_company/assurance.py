"""Shadow-mode development assurance artifact registry."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

from .config import CompanyConfig
from .db import Store, utcnow
from .integrity import signature as integrity_signature, verify as verify_integrity_signature


class AssuranceError(ValueError):
    """An assurance contract or authorization is invalid."""


ARTIFACT_SCHEMA = "assurance-artifact/v1"
PROFILES = {"product-competitive", "control-plane-reliability"}
RISK_CLASSES = {"C0", "C1", "C2", "C3"}
ARTIFACT_KINDS = {
    "goal_contract", "design_manifest", "design_record", "architecture_decision",
    "behavior_spec", "eval_contract", "baseline_report", "review_decision",
    "release_decision", "change_decision", "incident_record",
}
ARTIFACT_CONTENT_SCHEMAS: dict[str, dict[str, type | tuple[type, ...]]] = {
    "goal_contract": {"outcome": str, "non_goals": list},
    "design_manifest": {"artifact_refs": list, "edges": list},
    "design_record": {"problem": str, "decision": str, "alternatives": list},
    "architecture_decision": {"context": str, "decision": str, "consequences": list},
    "behavior_spec": {"behavior": str, "scenarios": list, "invariants": list},
    "eval_contract": {"hard_gates": list, "graders": list, "release_rule": str},
    "baseline_report": {"subject": str, "measurements": list, "limitations": list},
    "review_decision": {"decision": str, "findings": list, "evidence_refs": list},
    "release_decision": {"decision": str, "conditions": list, "rollback": str},
    "change_decision": {"reason": str, "changed_nodes": list, "invalidated_nodes": list},
    "incident_record": {"severity": str, "impact": str, "containment": list},
}
REQUIRED_MANIFEST_KINDS = {
    "goal_contract", "design_record", "behavior_spec", "eval_contract", "baseline_report",
}
GATES = {f"G{i}" for i in range(8)}
GATE_DECISIONS = {"pass", "pass_with_conditions", "return", "blocked", "reject"}
LIFECYCLE_TRANSITIONS = {
    "discovery": {"goal_review", "cancelled"},
    "goal_review": {"design_draft", "discovery", "cancelled"},
    "design_draft": {"design_review", "discovery", "cancelled"},
    "design_review": {"spec_ready", "design_draft", "cancelled"},
    "spec_ready": {"eval_contract_approved", "design_draft", "cancelled"},
    "eval_contract_approved": {"baseline_recorded", "design_draft", "cancelled"},
    "baseline_recorded": {"approved_for_build", "design_draft", "cancelled"},
    "approved_for_build": {"implementation", "cancelled"},
    "implementation": {"independent_evaluation", "cancelled"},
    "independent_evaluation": {"implementation", "design_draft", "evaluation_rejected", "release_candidate"},
    "release_candidate": {"release_decision", "incident_declared"},
    "release_decision": {"release_rejected", "release_approved", "release_approved_conditional"},
    "release_approved": {"enabled_or_deployed"},
    "release_approved_conditional": {"conditions_verified", "release_expired"},
    "conditions_verified": {"enabled_or_deployed"},
    "enabled_or_deployed": {"outcome_observation", "incident_declared"},
    "outcome_observation": {"closed", "incident_declared", "reopened"},
    "incident_declared": {"rollback_in_progress"},
    "rollback_in_progress": {"rolled_back", "disabled", "incident_resolved"},
    "incident_resolved": {"enabled_or_deployed", "outcome_observation", "closed", "reopened"},
    "reopened": {"discovery", "design_draft"},
}
GATE_FOR_TARGET = {
    "goal_review": "G0", "design_review": "G1", "eval_contract_approved": "G2",
    "baseline_recorded": "G3", "approved_for_build": "G4",
    "release_candidate": "G5", "release_approved": "G6",
    "release_approved_conditional": "G6", "closed": "G7",
}
GATE_REQUIRED_KINDS = {
    "G0": {"goal_contract"},
    "G1": {"goal_contract", "design_record"},
    "G2": {"goal_contract", "design_record", "behavior_spec", "eval_contract"},
    "G3": {"baseline_report", "eval_contract"},
    "G4": REQUIRED_MANIFEST_KINDS | {"design_manifest"},
    "G5": {"review_decision", "eval_contract"},
    "G6": {"review_decision", "release_decision"},
    "G7": {"review_decision"},
}
ARTIFACT_KEYS = {
    "schema_version", "artifact_id", "kind", "version", "status", "initiative_id",
    "profile", "risk_class", "owner_principal", "repository_id", "content",
}
LEGACY_PHASE_C_MIGRATION = "legacy-phase-c-artifact-anchors/v1"
LEGACY_PHASE_C_ARTIFACTS = {
    "assurance-bootstrap-goal": {
        "kind": "goal_contract",
        "content_sha256": "57517bbca04010778f43cb61d641670f6ad3effb03946de5ef75b670370d65f1",
        "registered_at": "2026-07-24T03:59:59+00:00",
        "approved_at": "2026-07-24T03:59:59+00:00",
    },
    "assurance-system-design": {
        "kind": "design_record",
        "content_sha256": "57cf1ceda798b58ed1922767a6712f086b7a491cbe8e19fbb8ff04b0edd87c51",
        "registered_at": "2026-07-24T03:59:59+00:00",
        "approved_at": "2026-07-24T03:59:59+00:00",
    },
    "assurance-shadow-spec": {
        "kind": "behavior_spec",
        "content_sha256": "38b3c95aaab93e061a475356f4d764e9f68dbab3ea953243882a53bdfd7a680a",
        "registered_at": "2026-07-24T03:59:59+00:00",
        "approved_at": "2026-07-24T03:59:59+00:00",
    },
    "assurance-shadow-eval": {
        "kind": "eval_contract",
        "content_sha256": "9740c2c64b4d2ae94710de56668fef2739d8f59b01d9c6d0b4db7518d86cdcc7",
        "registered_at": "2026-07-24T03:59:59+00:00",
        "approved_at": "2026-07-24T03:59:59+00:00",
    },
    "assurance-shadow-baseline": {
        "kind": "baseline_report",
        "content_sha256": "728cc195f50d50d68f082e62b0184a07280042869a22a106dba1f646505dba49",
        "registered_at": "2026-07-24T03:59:59+00:00",
        "approved_at": "2026-07-24T03:59:59+00:00",
    },
    "assurance-bootstrap-manifest": {
        "kind": "design_manifest",
        "content_sha256": "14768cc283423018ecac38768180dc26e31714c339b470680eb2ad9dd74685a7",
        "registered_at": "2026-07-24T03:59:59+00:00",
        "approved_at": "2026-07-24T04:00:00+00:00",
    },
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class AssuranceKernel:
    """Record proposed controls without changing current task dispatch behavior."""

    def __init__(self, config: CompanyConfig):
        self.config = config
        self.store = Store(config.db_path, workspace=config.workspace)

    def init(self) -> None:
        self.store.init_assurance()

    def migrate_legacy_phase_c_artifacts(self) -> dict[str, Any]:
        """Anchor only the known bootstrap artifacts and their exact audit lineage."""
        self.init()
        counts = {"registrations": 0, "approvals": 0, "lifecycle": 0}
        conflicts: list[dict[str, Any]] = []
        candidates: list[tuple[Any, dict[str, str]]] = []
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for artifact in conn.execute(
                "SELECT * FROM assurance_artifacts ORDER BY id"
            ).fetchall():
                key = (artifact["artifact_id"], artifact["version"])
                registration = conn.execute(
                    """SELECT 1 FROM assurance_artifact_registrations
                       WHERE artifact_id=? AND version=?""",
                    key,
                ).fetchone()
                approval = conn.execute(
                    """SELECT 1 FROM assurance_artifact_approvals
                       WHERE artifact_id=? AND version=?""",
                    key,
                ).fetchone()
                lifecycle = conn.execute(
                    """SELECT 1 FROM assurance_artifact_lifecycle
                       WHERE artifact_id=? AND version=? LIMIT 1""",
                    key,
                ).fetchone()
                anchors = (registration is not None, approval is not None, lifecycle is not None)
                manifest = LEGACY_PHASE_C_ARTIFACTS.get(artifact["artifact_id"])
                if lifecycle is not None:
                    if (
                        manifest is not None
                        and artifact["version"] == 1
                        and artifact["status"] == "approved"
                    ):
                        reason = self._legacy_phase_c_conflict(conn, artifact, manifest)
                        if reason:
                            conflicts.append(self._legacy_conflict(artifact, reason))
                    continue
                if manifest is not None:
                    reason = self._legacy_phase_c_conflict(conn, artifact, manifest)
                    if reason:
                        conflicts.append(self._legacy_conflict(artifact, reason))
                        continue
                if any(anchors):
                    conflicts.append(self._legacy_conflict(
                        artifact, "partial integrity anchors require independent review",
                    ))
                    continue
                if manifest is None:
                    conflicts.append(self._legacy_conflict(
                        artifact, "artifact is not in the approved legacy migration manifest",
                    ))
                    continue
                candidates.append((artifact, manifest))

            if conflicts:
                return {
                    "migration_version": LEGACY_PHASE_C_MIGRATION,
                    "status": "integrity_conflict",
                    "anchors_backfilled": counts,
                    "conflicts": conflicts,
                }

            for artifact, manifest in candidates:
                registration_values = {
                    "artifact_id": artifact["artifact_id"],
                    "version": artifact["version"],
                    "content_sha256": artifact["content_sha256"],
                    "created_at": manifest["registered_at"],
                }
                conn.execute(
                    """INSERT INTO assurance_artifact_registrations(
                           artifact_id,version,content_sha256,created_at,integrity_signature
                       ) VALUES (?,?,?,?,?)""",
                    (*registration_values.values(), integrity_signature(
                        self.config.db_path, "artifact-registration", registration_values,
                    )),
                )
                approval_values = {
                    "artifact_id": artifact["artifact_id"],
                    "version": artifact["version"],
                    "content_sha256": artifact["content_sha256"],
                    "approved_by_principal": artifact["approved_by_principal"],
                    "approved_at": manifest["approved_at"],
                }
                conn.execute(
                    """INSERT INTO assurance_artifact_approvals(
                           artifact_id,version,content_sha256,approved_by_principal,
                           approved_at,integrity_signature
                       ) VALUES (?,?,?,?,?,?)""",
                    (*approval_values.values(), integrity_signature(
                        self.config.db_path, "artifact-approval", approval_values,
                    )),
                )
                self._record_artifact_lifecycle(
                    conn, artifact["artifact_id"], artifact["version"], None, "draft",
                    manifest["registered_at"], "principal-platform", "artifact registered",
                )
                self._record_artifact_lifecycle(
                    conn, artifact["artifact_id"], artifact["version"], "draft", "approved",
                    manifest["approved_at"], "principal-ceo", "artifact approved",
                )
                counts["registrations"] += 1
                counts["approvals"] += 1
                counts["lifecycle"] += 2
        return {
            "migration_version": LEGACY_PHASE_C_MIGRATION,
            "status": "ok",
            "anchors_backfilled": counts,
            "conflicts": [],
        }

    @staticmethod
    def _legacy_conflict(artifact: Any, reason: str) -> dict[str, Any]:
        return {
            "artifact_id": artifact["artifact_id"],
            "version": artifact["version"],
            "reason": reason,
        }

    def _legacy_phase_c_conflict(
        self, conn: Any, artifact: Any, manifest: dict[str, str] | None,
    ) -> str | None:
        if manifest is None or artifact["version"] != 1:
            return "artifact is not in the approved legacy migration manifest"
        try:
            actual_sha256 = self._artifact_content_sha256(artifact)
        except AssuranceError:
            return "content hash does not match approved legacy manifest"
        if actual_sha256 != manifest["content_sha256"] or artifact["kind"] != manifest["kind"]:
            return "content hash does not match approved legacy manifest"
        if (
            artifact["initiative_id"] != "development-assurance-bootstrap"
            or artifact["status"] != "approved"
            or artifact["profile"] != "control-plane-reliability"
            or artifact["risk_class"] != "C2"
            or artifact["owner_principal"] != "principal-platform"
            or artifact["repository_id"] != "agent-company"
            or artifact["approved_by_principal"] != "principal-ceo"
            or artifact["created_at"] != manifest["registered_at"]
            or artifact["approved_at"] != manifest["approved_at"]
        ):
            return "approval metadata does not match approved legacy manifest"
        try:
            payload = json.loads(artifact["content_json"])
        except (TypeError, json.JSONDecodeError):
            return "content hash does not match approved legacy manifest"
        if (
            not isinstance(payload, dict)
            or payload.get("artifact_id") != artifact["artifact_id"]
            or payload.get("version") != artifact["version"]
            or payload.get("kind") != artifact["kind"]
            or payload.get("initiative_id") != artifact["initiative_id"]
            or payload.get("profile") != artifact["profile"]
            or payload.get("risk_class") != artifact["risk_class"]
            or payload.get("owner_principal") != artifact["owner_principal"]
            or payload.get("repository_id") != artifact["repository_id"]
            or payload.get("status") != "draft"
        ):
            return "artifact metadata does not match immutable content"

        ref = f"{artifact['artifact_id']}:v{artifact['version']}"
        audits = conn.execute(
            """SELECT id,ts,actor,action,entity,entity_id,details FROM audit_log
               WHERE entity_id=? AND action IN (
                   'assurance_artifact_registered', 'assurance_artifact_approved',
                   'assurance_artifact_superseded'
               ) ORDER BY id""",
            (ref,),
        ).fetchall()
        expected = [
            {
                "ts": manifest["registered_at"],
                "actor": "Company Platform Engineer",
                "action": "assurance_artifact_registered",
                "entity": "assurance_artifact",
                "entity_id": ref,
                "details": {
                    "kind": manifest["kind"], "mode": "shadow",
                    "principal_id": "principal-platform",
                    "sha256": manifest["content_sha256"],
                },
            },
            {
                "ts": manifest["approved_at"],
                "actor": "CEO",
                "action": "assurance_artifact_approved",
                "entity": "assurance_artifact",
                "entity_id": ref,
                "details": {"mode": "shadow", "principal_id": "principal-ceo"},
            },
        ]
        if len(audits) != len(expected):
            return "historical lifecycle audit evidence is incomplete or ambiguous"
        for row, wanted in zip(audits, expected):
            try:
                details = json.loads(row["details"])
            except (TypeError, json.JSONDecodeError):
                return "historical lifecycle audit evidence is invalid"
            actual = {name: row[name] for name in wanted if name != "details"}
            actual["details"] = details
            if actual != wanted:
                audit_kind = {
                    "assurance_artifact_registered": "registration",
                    "assurance_artifact_approved": "approval",
                }[wanted["action"]]
                return f"{audit_kind} audit evidence mismatch"

        for principal_id, actor, authority in (
            ("principal-platform", "Company Platform Engineer", "implementer"),
            ("principal-ceo", "CEO", "executive"),
        ):
            principal = conn.execute(
                """SELECT actor,authority,status FROM assurance_principals
                   WHERE principal_id=?""",
                (principal_id,),
            ).fetchone()
            if (
                principal is None
                or principal["actor"] != actor
                or principal["authority"] != authority
                or principal["status"] != "active"
            ):
                return "historical principal identity does not reconcile"
        return None

    def register_principal(
        self, principal_id: str, actor: str, authority: str, *, bootstrap_secret: str,
    ) -> str:
        """Bootstrap a principal with a secret supplied through a trusted local channel."""
        self.init()
        allowed = {"chairman", "executive", "reviewer", "implementer", "operator"}
        expected = self.config.workspace / "data" / "assurance-bootstrap.secret"
        if not expected.exists() or not hmac.compare_digest(expected.read_text().strip(), bootstrap_secret):
            raise AssuranceError("invalid assurance bootstrap credential")
        if authority not in allowed or not principal_id.strip() or not actor.strip():
            raise AssuranceError("invalid assurance principal registration")
        credential = secrets.token_urlsafe(32)
        digest = hashlib.sha256(credential.encode("utf-8")).hexdigest()
        with self.store.connect() as conn:
            now = utcnow()
            conn.execute(
                """INSERT INTO assurance_principals(
                       principal_id, actor, authority, credential_sha256, status, created_at
                   ) VALUES (?, ?, ?, ?, 'active', ?)
                   ON CONFLICT(principal_id) DO UPDATE SET actor=excluded.actor,
                     authority=excluded.authority, credential_sha256=excluded.credential_sha256,
                     status='active'""",
                (principal_id, actor, authority, digest, now),
            )
            if actor == "Trusted Evaluator" and authority == "operator":
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS trusted_eval_evaluator_credentials (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           principal_id TEXT NOT NULL,
                           sequence INTEGER NOT NULL,
                           actor TEXT NOT NULL,
                           authority TEXT NOT NULL,
                           credential_sha256 TEXT NOT NULL,
                           principal_created_at TEXT NOT NULL,
                           issued_at TEXT NOT NULL,
                           previous_signature TEXT,
                           integrity_signature TEXT NOT NULL UNIQUE,
                           UNIQUE(principal_id, sequence),
                           UNIQUE(principal_id, credential_sha256,
                                  principal_created_at)
                       )"""
                )
                conn.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           trusted_eval_evaluator_credentials_immutable_update
                       BEFORE UPDATE ON trusted_eval_evaluator_credentials
                       BEGIN
                           SELECT RAISE(
                               ABORT,
                               'trusted evaluator credential provenance is immutable'
                           );
                       END"""
                )
                conn.execute(
                    """CREATE TRIGGER IF NOT EXISTS
                           trusted_eval_evaluator_credentials_immutable_delete
                       BEFORE DELETE ON trusted_eval_evaluator_credentials
                       BEGIN
                           SELECT RAISE(
                               ABORT,
                               'trusted evaluator credential provenance is immutable'
                           );
                       END"""
                )
                principal = conn.execute(
                    "SELECT created_at FROM assurance_principals "
                    "WHERE principal_id=?",
                    (principal_id,),
                ).fetchone()
                previous = conn.execute(
                    "SELECT sequence,issued_at,integrity_signature "
                    "FROM trusted_eval_evaluator_credentials "
                    "WHERE principal_id=? ORDER BY sequence DESC LIMIT 1",
                    (principal_id,),
                ).fetchone()
                provenance = {
                    "principal_id": principal_id,
                    "sequence": 1 if previous is None else previous["sequence"] + 1,
                    "actor": actor,
                    "authority": authority,
                    "credential_sha256": digest,
                    "principal_created_at": principal["created_at"],
                    "issued_at": now,
                    "previous_signature": (
                        None if previous is None
                        else previous["integrity_signature"]
                    ),
                }
                conn.execute(
                    """INSERT OR IGNORE INTO trusted_eval_evaluator_credentials(
                           principal_id,sequence,actor,authority,credential_sha256,
                           principal_created_at,issued_at,previous_signature,
                           integrity_signature
                       ) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        *provenance.values(),
                        integrity_signature(
                            self.config.db_path,
                            "trusted-eval-evaluator-provenance", provenance,
                        ),
                    ),
                )
        return credential

    def _principal(
        self, conn: Any, actor: str, principal_id: str, credential: str | None,
    ) -> dict[str, Any]:
        row = conn.execute(
            """SELECT principal_id, actor, authority, credential_sha256
               FROM assurance_principals
               WHERE principal_id=? AND actor=? AND status='active'""",
            (principal_id, actor),
        ).fetchone()
        supplied = hashlib.sha256((credential or "").encode("utf-8")).hexdigest()
        if row is None or not row["credential_sha256"] or not hmac.compare_digest(row["credential_sha256"], supplied):
            raise AssuranceError("unauthenticated or mismatched assurance principal")
        return dict(row)

    @staticmethod
    def _require_authority(principal: dict[str, Any], allowed: set[str]) -> None:
        if principal["authority"] not in allowed:
            raise AssuranceError("principal lacks required assurance authority")

    def _assert_principal(self, actor: str, principal_id: str, allowed: set[str]) -> dict[str, Any]:
        credential = os.environ.get(f"ASSURANCE_CREDENTIAL_{principal_id.upper().replace('-', '_')}")
        with self.store.connect_readonly() as conn:
            principal = self._principal(conn, actor, principal_id, credential)
        self._require_authority(principal, allowed)
        return principal

    def _initiative_artifact_set_sha256(
        self, conn: Any, initiative_id: str, *, exclude_kinds: set[str] | None = None,
    ) -> str:
        exclude_kinds = exclude_kinds or set()
        rows = conn.execute(
            """SELECT artifact_id,version,kind,content_json,content_sha256
               FROM assurance_artifacts
               WHERE initiative_id=? AND status='approved'
               ORDER BY artifact_id, version""",
            (initiative_id,),
        ).fetchall()
        artifact_set = []
        for row in rows:
            if row["kind"] in exclude_kinds:
                continue
            digest = self._artifact_content_sha256(row)
            artifact_set.append({
                "ref": f"{row['artifact_id']}:v{row['version']}", "sha256": digest,
            })
        return hashlib.sha256(_canonical(artifact_set).encode("ascii")).hexdigest()

    @staticmethod
    def _artifact_content_sha256(artifact: Any) -> str:
        try:
            payload = json.loads(artifact["content_json"])
            canonical = _canonical(payload)
            digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        except (KeyError, TypeError, UnicodeEncodeError, json.JSONDecodeError) as exc:
            raise AssuranceError("artifact content body is not valid canonical JSON") from exc
        if digest != artifact["content_sha256"]:
            raise AssuranceError("artifact content body hash does not match stored content_sha256")
        return digest

    def _initiative_build_artifact_set_sha256(self, conn: Any, initiative_id: str) -> str:
        return self._initiative_artifact_set_sha256(
            conn, initiative_id, exclude_kinds={"review_decision"},
        )

    def _record_artifact_lifecycle(
        self, conn: Any, artifact_id: str, version: int, from_status: str | None,
        to_status: str, transitioned_at: str, actor_principal: str, reason: str,
    ) -> None:
        previous = conn.execute(
            """SELECT sequence,integrity_signature FROM assurance_artifact_lifecycle
               WHERE artifact_id=? AND version=? ORDER BY sequence DESC LIMIT 1""",
            (artifact_id, version),
        ).fetchone()
        values = {
            "artifact_id": artifact_id,
            "version": version,
            "sequence": int(previous["sequence"]) + 1 if previous else 1,
            "from_status": from_status,
            "to_status": to_status,
            "transitioned_at": transitioned_at,
            "actor_principal": actor_principal,
            "reason": reason,
            "previous_signature": previous["integrity_signature"] if previous else None,
        }
        conn.execute(
            """INSERT INTO assurance_artifact_lifecycle(
                   artifact_id,version,sequence,from_status,to_status,transitioned_at,
                   actor_principal,reason,previous_signature,integrity_signature
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (*values.values(), integrity_signature(
                self.config.db_path, "artifact-lifecycle", values,
            )),
        )

    def _artifact_lifecycle_valid(self, conn: Any, artifact: Any) -> bool:
        previous_status = None
        previous_signature = None
        rows = conn.execute(
            """SELECT * FROM assurance_artifact_lifecycle
               WHERE artifact_id=? AND version=? ORDER BY sequence""",
            (artifact["artifact_id"], artifact["version"]),
        ).fetchall()
        if not rows:
            return False
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
                or not verify_integrity_signature(
                    self.config.db_path, "artifact-lifecycle", values,
                    row["integrity_signature"],
                )
            ):
                return False
            previous_status = row["to_status"]
            previous_signature = row["integrity_signature"]
        return previous_status == artifact["status"]

    def create_initiative(
        self, initiative_id: str, title: str, profile: str, risk_class: str,
        *, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
        if profile not in PROFILES or risk_class not in RISK_CLASSES:
            raise AssuranceError("invalid assurance profile or risk class")
        if not all(isinstance(v, str) and v.strip() for v in {initiative_id, title, principal_id}):
            raise AssuranceError("initiative fields must be non-empty")
        principal = self._assert_principal(actor, principal_id, {"executive", "chairman"})
        now = utcnow()
        try:
            with self.store.connect() as conn:
                conn.execute(
                    """INSERT INTO assurance_initiatives(
                           initiative_id, profile, risk_class, title, owner_principal,
                           status, mode, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'discovery', 'shadow', ?, ?)""",
                    (initiative_id, profile, risk_class, title.strip(), principal_id, now, now),
                )
                self.store.audit(
                    conn, actor, "assurance_initiative_created", "assurance_initiative",
                    initiative_id, {"principal_id": principal_id, "risk_class": risk_class, "mode": "shadow"},
                )
        except Exception as exc:
            if "UNIQUE constraint" in str(exc):
                raise AssuranceError("initiative already exists") from exc
            raise
        return {"initiative_id": initiative_id, "status": "discovery", "mode": "shadow"}

    def _validate_trusted_g5(self, conn: Any, initiative_id: str, expected_result_sha256: str | None = None) -> str:
        if expected_result_sha256:
            run = conn.execute(
                """SELECT * FROM trusted_eval_runs
                   WHERE initiative_id=? AND result_sha256=? AND status='completed'""",
                (initiative_id, expected_result_sha256),
            ).fetchone()
        else:
            run = conn.execute(
                """SELECT * FROM trusted_eval_runs
                   WHERE initiative_id=? AND status='completed' ORDER BY attempt DESC LIMIT 1""",
                (initiative_id,),
            ).fetchone()
        quarantined = conn.execute(
            "SELECT 1 FROM trusted_eval_quarantines WHERE initiative_id=?", (initiative_id,),
        ).fetchone()
        if run is None or quarantined or not run["evidence_sha256"]:
            raise AssuranceError("G5 requires a completed non-quarantined content-addressed evaluation")
        run_values = {
            "initiative_id": run["initiative_id"],
            "attempt": run["attempt"],
            "refs": {
                "candidate": run["candidate_sha256"],
                "dataset": run["dataset_sha256"],
                "grader": run["grader_sha256"],
                "environment": run["environment_sha256"],
            },
            "seed": run["seed"],
            "status": run["status"],
            "evidence_ref": run["evidence_ref"],
            "evidence_sha256": run["evidence_sha256"],
            "evaluator_principal_id": run["evaluator_principal_id"],
            "result_sha256": run["result_sha256"],
            "created_at": run["created_at"],
        }
        if not verify_integrity_signature(
            self.config.db_path, "trusted-eval-run", run_values,
            run["integrity_signature"],
        ):
            raise AssuranceError("G5 trusted evaluation integrity anchor mismatch")
        evidence = (self.config.workspace / run["evidence_ref"]).resolve()
        workspace = self.config.workspace.resolve()
        if workspace not in evidence.parents or not evidence.is_file():
            raise AssuranceError("G5 trusted evaluation evidence is missing")
        if hashlib.sha256(evidence.read_bytes()).hexdigest() != run["evidence_sha256"]:
            raise AssuranceError("G5 trusted evaluation evidence hash mismatch")
        manifest_payloads: dict[str, dict[str, Any]] = {}
        for kind, column in {
            "candidate": "candidate_sha256", "dataset": "dataset_sha256",
            "grader": "grader_sha256", "environment": "environment_sha256",
        }.items():
            manifest = conn.execute(
                "SELECT content_sha256 FROM trusted_eval_manifests WHERE kind=? AND manifest_sha256=?",
                (kind, run[column]),
            ).fetchone()
            if manifest is None:
                raise AssuranceError(f"G5 trusted evaluation {kind} manifest is missing")
            payload_row = conn.execute(
                "SELECT manifest_json FROM trusted_eval_manifests WHERE kind=? AND manifest_sha256=?",
                (kind, run[column]),
            ).fetchone()
            if payload_row is None or hashlib.sha256(payload_row["manifest_json"].encode("ascii")).hexdigest() != run[column]:
                raise AssuranceError(f"G5 trusted evaluation {kind} manifest integrity mismatch")
            manifest_payload = json.loads(payload_row["manifest_json"])
            if manifest_payload.get("content_sha256") != manifest["content_sha256"]:
                raise AssuranceError(f"G5 trusted evaluation {kind} manifest content mapping mismatch")
            manifest_payloads[kind] = manifest_payload
            content = self.config.workspace / "data" / "trusted-eval-content" / manifest["content_sha256"]
            if not content.is_file() or hashlib.sha256(content.read_bytes()).hexdigest() != manifest["content_sha256"]:
                raise AssuranceError(f"G5 trusted evaluation {kind} content hash mismatch")
        recomputed = {
            "initiative_id": initiative_id, "attempt": run["attempt"],
            "refs": {kind: run[column] for kind, column in {
                "candidate": "candidate_sha256", "dataset": "dataset_sha256",
                "grader": "grader_sha256", "environment": "environment_sha256",
            }.items()},
            "seed": run["seed"], "status": run["status"],
            "evidence_ref": run["evidence_ref"], "evidence_sha256": run["evidence_sha256"],
        }
        if hashlib.sha256(_canonical(recomputed).encode("ascii")).hexdigest() != run["result_sha256"]:
            raise AssuranceError("G5 trusted evaluation result lineage mismatch")
        return str(run["result_sha256"])

    def transition(
        self, initiative_id: str, target: str, *, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
        self._assert_principal(actor, principal_id, {"executive", "chairman", "reviewer"})
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT status FROM assurance_initiatives WHERE initiative_id=?", (initiative_id,)
            ).fetchone()
            if row is None:
                raise AssuranceError("initiative not found")
            current = row["status"]
            required_gate = GATE_FOR_TARGET.get(target)
            if required_gate:
                gate = conn.execute(
                    """SELECT decision, artifact_set_sha256, expires_at
                       FROM assurance_gate_decisions
                       WHERE initiative_id=? AND gate=?
                       ORDER BY id DESC LIMIT 1""",
                    (initiative_id, required_gate),
                ).fetchone()
                if gate is None or gate["decision"] not in {"pass", "pass_with_conditions"}:
                    raise AssuranceError(f"lifecycle transition requires passing {required_gate}")
                if gate["expires_at"]:
                    expires = datetime.fromisoformat(gate["expires_at"])
                    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                        raise AssuranceError(f"lifecycle transition requires unexpired {required_gate}")
                if gate["artifact_set_sha256"] != self._initiative_artifact_set_sha256(conn, initiative_id):
                    raise AssuranceError(f"lifecycle transition {required_gate} artifact set is stale")
                if required_gate in {"G5", "G6"}:
                    review_rows = conn.execute(
                        """SELECT content_json,content_sha256 FROM assurance_artifacts
                           WHERE initiative_id=? AND kind='review_decision' AND status='approved'""",
                        (initiative_id,),
                    ).fetchall()
                    bound = set()
                    for review in review_rows:
                        self._artifact_content_sha256(review)
                        bound.update(json.loads(review["content_json"])["content"]["evidence_refs"])
                    matching = conn.execute(
                        """SELECT result_sha256 FROM trusted_eval_runs
                           WHERE initiative_id=? AND status='completed' ORDER BY attempt""",
                        (initiative_id,),
                    ).fetchall()
                    result_hash = next((row["result_sha256"] for row in matching if row["result_sha256"] in bound), None)
                    if not result_hash:
                        raise AssuranceError(f"lifecycle transition {required_gate} lacks a bound trusted evaluation")
                    self._validate_trusted_g5(conn, initiative_id, result_hash)
            if target not in LIFECYCLE_TRANSITIONS.get(current, set()):
                raise AssuranceError(f"illegal lifecycle transition: {current} -> {target}")
            if target in {"release_candidate", "release_decision", "release_approved", "release_approved_conditional", "conditions_verified", "enabled_or_deployed"}:
                review_rows = conn.execute(
                    """SELECT content_json,content_sha256 FROM assurance_artifacts
                       WHERE initiative_id=? AND kind='review_decision' AND status='approved'""",
                    (initiative_id,),
                ).fetchall()
                bound = set()
                for review in review_rows:
                    self._artifact_content_sha256(review)
                    bound.update(json.loads(review["content_json"])["content"]["evidence_refs"])
                latest = conn.execute(
                    "SELECT result_sha256 FROM trusted_eval_runs WHERE initiative_id=? AND status='completed' ORDER BY attempt DESC LIMIT 1",
                    (initiative_id,),
                ).fetchone()
                if latest is None or latest["result_sha256"] not in bound:
                    raise AssuranceError("release lifecycle requires review binding to latest trusted evaluation")
                self._validate_trusted_g5(conn, initiative_id, latest["result_sha256"])
            now = utcnow()
            conn.execute(
                "UPDATE assurance_initiatives SET status=?, updated_at=? WHERE initiative_id=?",
                (target, now, initiative_id),
            )
            self.store.audit(
                conn, actor, "assurance_lifecycle_transition", "assurance_initiative",
                initiative_id, {"principal_id": principal_id, "from": current, "to": target, "mode": "shadow"},
            )
        return {"initiative_id": initiative_id, "status": target, "mode": "shadow"}

    def block(
        self, initiative_id: str, reason: str, resume_state: str,
        *, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
        self._assert_principal(actor, principal_id, {"executive", "chairman", "reviewer"})
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT status FROM assurance_initiatives WHERE initiative_id=?", (initiative_id,)
            ).fetchone()
            if row is None or row["status"] != resume_state or not reason.strip():
                raise AssuranceError("invalid assurance blocker or resume state")
            now = utcnow()
            conn.execute(
                """INSERT INTO assurance_blocks(
                       initiative_id, reason, resume_state, actor, principal_id, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(initiative_id) DO UPDATE SET
                     reason=excluded.reason, resume_state=excluded.resume_state,
                     actor=excluded.actor, principal_id=excluded.principal_id,
                     created_at=excluded.created_at""",
                (initiative_id, reason.strip(), resume_state, actor, principal_id, now),
            )
            conn.execute(
                "UPDATE assurance_initiatives SET status='blocked', updated_at=? WHERE initiative_id=?",
                (now, initiative_id),
            )
            self.store.audit(
                conn, actor, "assurance_blocked", "assurance_initiative", initiative_id,
                {"principal_id": principal_id, "reason": reason.strip(), "resume_state": resume_state, "mode": "shadow"},
            )
        return {"initiative_id": initiative_id, "status": "blocked", "resume_state": resume_state, "mode": "shadow"}

    def resume(self, initiative_id: str, *, actor: str, principal_id: str) -> dict[str, Any]:
        self.init()
        self._assert_principal(actor, principal_id, {"executive", "chairman", "reviewer"})
        with self.store.connect() as conn:
            row = conn.execute(
                """SELECT i.status, b.resume_state FROM assurance_initiatives i
                   JOIN assurance_blocks b ON b.initiative_id=i.initiative_id
                   WHERE i.initiative_id=?""",
                (initiative_id,),
            ).fetchone()
            if row is None or row["status"] != "blocked":
                raise AssuranceError("initiative is not blocked with a resume state")
            now = utcnow()
            conn.execute(
                "UPDATE assurance_initiatives SET status=?, updated_at=? WHERE initiative_id=?",
                (row["resume_state"], now, initiative_id),
            )
            conn.execute("DELETE FROM assurance_blocks WHERE initiative_id=?", (initiative_id,))
            self.store.audit(
                conn, actor, "assurance_resumed", "assurance_initiative", initiative_id,
                {"principal_id": principal_id, "resume_state": row["resume_state"], "mode": "shadow"},
            )
        return {"initiative_id": initiative_id, "status": row["resume_state"], "mode": "shadow"}

    def record_gate(
        self, initiative_id: str, gate: str, decision: str, artifact_refs: list[str],
        *, actor: str, principal_id: str, conditions: list[str] | None = None,
        expires_at: str | None = None,
    ) -> dict[str, Any]:
        self.init()
        self._assert_principal(actor, principal_id, {"executive", "chairman", "reviewer"})
        if gate not in GATES or decision not in GATE_DECISIONS or not artifact_refs:
            raise AssuranceError("invalid gate decision")
        conditions = conditions or []
        if decision == "pass_with_conditions" and (not conditions or not expires_at):
            raise AssuranceError("conditional gate requires conditions and expiry")
        ordered = []
        with self.store.connect() as conn:
            for ref in sorted(artifact_refs):
                try:
                    artifact_id, raw_version = ref.rsplit(":v", 1)
                    version = int(raw_version)
                except (ValueError, TypeError) as exc:
                    raise AssuranceError("invalid gate artifact reference") from exc
                row = conn.execute(
                    """SELECT initiative_id,owner_principal,status,content_json,content_sha256
                       FROM assurance_artifacts WHERE artifact_id=? AND version=?""",
                    (artifact_id, version),
                ).fetchone()
                if row is None or row["initiative_id"] != initiative_id or row["status"] != "approved":
                    raise AssuranceError("gate references must be approved artifacts in the initiative")
                if row["owner_principal"] == principal_id:
                    raise AssuranceError("separation of duties forbids author gate approval")
                ordered.append({"ref": ref, "sha256": self._artifact_content_sha256(row)})
            required_kinds = GATE_REQUIRED_KINDS[gate]
            rows = conn.execute(
                """SELECT kind, artifact_id, version FROM assurance_artifacts
                   WHERE initiative_id=? AND status='approved'""",
                (initiative_id,),
            ).fetchall()
            approved_kinds = {row["kind"] for row in rows}
            if not required_kinds <= approved_kinds:
                missing = sorted(required_kinds - approved_kinds)
                raise AssuranceError(f"gate {gate} missing approved artifact kinds: {missing}")
            content_by_kind: dict[str, list[dict[str, Any]]] = {}
            for artifact_row in conn.execute(
                """SELECT kind,content_json,content_sha256 FROM assurance_artifacts
                   WHERE initiative_id=? AND status='approved'""",
                (initiative_id,),
            ):
                self._artifact_content_sha256(artifact_row)
                content_by_kind.setdefault(artifact_row["kind"], []).append(
                    json.loads(artifact_row["content_json"])["content"]
                )
            if gate in {"G5", "G6", "G7"}:
                reviews = content_by_kind.get("review_decision", [])
                if not reviews or any(
                    review["decision"] not in {"approve", "pass"} or review["findings"]
                    for review in reviews
                ):
                    raise AssuranceError(
                        f"gate {gate} requires every approved review to approve with no blocking findings"
                    )
            if gate == "G5":
                reviews = content_by_kind.get("review_decision", [])
                result_hash = self._validate_trusted_g5(conn, initiative_id)
                if not all(result_hash in review["evidence_refs"] for review in reviews):
                    raise AssuranceError("gate G5 review must bind the trusted evaluation result hash")
            if gate == "G6":
                self._validate_trusted_g5(conn, initiative_id)
                releases = content_by_kind.get("release_decision", [])
                if not releases or any(
                    release["decision"] not in {
                        "enable_internal", "controlled_beta", "production_release"
                    }
                    for release in releases
                ):
                    raise AssuranceError("gate G6 requires every approved release decision to be affirmative")
            all_refs = {f"{row['artifact_id']}:v{row['version']}" for row in rows}
            if set(artifact_refs) != all_refs:
                raise AssuranceError("gate must bind the complete approved initiative artifact set")
            digest = self._initiative_artifact_set_sha256(conn, initiative_id)
            now = utcnow()
            cur = conn.execute(
                """INSERT INTO assurance_gate_decisions(
                       initiative_id, gate, decision, actor, principal_id,
                       artifact_set_sha256, conditions_json, expires_at, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (initiative_id, gate, decision, actor, principal_id, digest, _canonical(conditions), expires_at, now),
            )
            self.store.audit(
                conn, actor, "assurance_gate_recorded", "assurance_gate_decision",
                cur.lastrowid, {"principal_id": principal_id, "gate": gate, "decision": decision, "artifact_set_sha256": digest, "mode": "shadow"},
            )
        return {"gate_decision_id": cur.lastrowid, "artifact_set_sha256": digest, "decision": decision, "mode": "shadow"}

    def supersede_artifact(
        self, artifact_id: str, version: int, *, actor: str, principal_id: str, reason: str,
    ) -> dict[str, Any]:
        self.init()
        self._assert_principal(actor, principal_id, {"executive", "chairman"})
        if not reason.strip():
            raise AssuranceError("supersession reason must be non-empty")
        invalidated: list[str] = []
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT status FROM assurance_artifacts WHERE artifact_id=? AND version=?",
                (artifact_id, version),
            ).fetchone()
            if row is None or row["status"] != "approved":
                raise AssuranceError("only approved artifact may be superseded")
            self._record_artifact_lifecycle(
                conn, artifact_id, version, "approved", "superseded", utcnow(),
                principal_id, reason.strip(),
            )
            conn.execute(
                "UPDATE assurance_artifacts SET status='superseded' WHERE artifact_id=? AND version=?",
                (artifact_id, version),
            )
            queue = [artifact_id]
            seen = {artifact_id}
            while queue:
                source = queue.pop(0)
                for link in conn.execute(
                    "SELECT to_artifact_id FROM assurance_links WHERE from_artifact_id=?", (source,)
                ):
                    dependent = link["to_artifact_id"]
                    if dependent in seen:
                        continue
                    seen.add(dependent)
                    queue.append(dependent)
                    rows = conn.execute(
                        "SELECT version FROM assurance_artifacts WHERE artifact_id=? AND status='approved'",
                        (dependent,),
                    ).fetchall()
                    for dep_row in rows:
                        self._record_artifact_lifecycle(
                            conn, dependent, dep_row["version"], "approved", "stale",
                            utcnow(), principal_id,
                            f"dependency {artifact_id}:v{version} superseded",
                        )
                        conn.execute(
                            "UPDATE assurance_artifacts SET status='stale' WHERE artifact_id=? AND version=?",
                            (dependent, dep_row["version"]),
                        )
                        invalidated.append(f"{dependent}:v{dep_row['version']}")
            self.store.audit(
                conn, actor, "assurance_artifact_superseded", "assurance_artifact",
                f"{artifact_id}:v{version}", {"principal_id": principal_id, "reason": reason.strip(), "invalidated": sorted(invalidated), "mode": "shadow"},
            )
        return {"artifact_id": artifact_id, "version": version, "status": "superseded", "invalidated": sorted(invalidated), "mode": "shadow"}

    def verify_integrity(self) -> dict[str, Any]:
        self.init()
        conflicts = []
        with self.store.connect_readonly() as conn:
            for row in conn.execute(
                """SELECT artifact_id,version,status,content_json,content_sha256,
                          approved_by_principal,approved_at
                   FROM assurance_artifacts ORDER BY id"""
            ):
                try:
                    actual = self._artifact_content_sha256(row)
                except AssuranceError:
                    actual = ""
                    conflicts.append({
                        "artifact_id": row["artifact_id"], "version": row["version"],
                        "expected_sha256": row["content_sha256"], "actual_sha256": actual,
                    })
                registration = conn.execute(
                    """SELECT * FROM assurance_artifact_registrations
                       WHERE artifact_id=? AND version=?""",
                    (row["artifact_id"], row["version"]),
                ).fetchone()
                registration_valid = registration is not None and verify_integrity_signature(
                    self.config.db_path, "artifact-registration", {
                        "artifact_id": registration["artifact_id"],
                        "version": registration["version"],
                        "content_sha256": registration["content_sha256"],
                        "created_at": registration["created_at"],
                    }, registration["integrity_signature"],
                ) and registration["content_sha256"] == row["content_sha256"]
                if not registration_valid:
                    conflicts.append({
                        "artifact_id": row["artifact_id"], "version": row["version"],
                        "anchor": "registration",
                    })
                if not self._artifact_lifecycle_valid(conn, row):
                    conflicts.append({
                        "artifact_id": row["artifact_id"], "version": row["version"],
                        "anchor": "lifecycle",
                    })
                if row["status"] == "approved":
                    approval = conn.execute(
                        """SELECT * FROM assurance_artifact_approvals
                           WHERE artifact_id=? AND version=?""",
                        (row["artifact_id"], row["version"]),
                    ).fetchone()
                    approval_valid = approval is not None and verify_integrity_signature(
                        self.config.db_path, "artifact-approval", {
                            "artifact_id": approval["artifact_id"],
                            "version": approval["version"],
                            "content_sha256": approval["content_sha256"],
                            "approved_by_principal": approval["approved_by_principal"],
                            "approved_at": approval["approved_at"],
                        }, approval["integrity_signature"],
                    ) and (
                        approval["content_sha256"] == row["content_sha256"]
                        and approval["approved_by_principal"] == row["approved_by_principal"]
                        and approval["approved_at"] == row["approved_at"]
                    )
                    if not approval_valid:
                        conflicts.append({
                            "artifact_id": row["artifact_id"], "version": row["version"],
                            "anchor": "approval",
                        })
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trusted_eval_runs'"
            ).fetchone():
                for contract in conn.execute(
                    "SELECT * FROM trusted_eval_contracts ORDER BY initiative_id"
                ):
                    contract_values = {
                        "initiative_id": contract["initiative_id"],
                        "max_attempts": contract["max_attempts"],
                        "created_at": contract["created_at"],
                    }
                    if not verify_integrity_signature(
                        self.config.db_path, "trusted-eval-contract",
                        contract_values, contract["integrity_signature"],
                    ):
                        conflicts.append({
                            "initiative_id": contract["initiative_id"],
                            "anchor": "trusted_eval_contract",
                        })
                previous_principal = None
                previous_sequence = 0
                previous_signature = None
                previous_issued_at = None
                for provenance in conn.execute(
                    "SELECT * FROM trusted_eval_evaluator_credentials "
                    "ORDER BY principal_id,sequence"
                ):
                    provenance_values = {
                        "principal_id": provenance["principal_id"],
                        "sequence": provenance["sequence"],
                        "actor": provenance["actor"],
                        "authority": provenance["authority"],
                        "credential_sha256": provenance["credential_sha256"],
                        "principal_created_at": provenance[
                            "principal_created_at"
                        ],
                        "issued_at": provenance["issued_at"],
                        "previous_signature": provenance[
                            "previous_signature"
                        ],
                    }
                    try:
                        principal_created_at = datetime.fromisoformat(
                            provenance["principal_created_at"]
                        )
                        issued_at = datetime.fromisoformat(provenance["issued_at"])
                    except (TypeError, ValueError):
                        principal_created_at = datetime.max.replace(
                            tzinfo=timezone.utc
                        )
                        issued_at = datetime.min.replace(tzinfo=timezone.utc)
                    if provenance["principal_id"] != previous_principal:
                        previous_principal = provenance["principal_id"]
                        previous_sequence = 0
                        previous_signature = None
                        previous_issued_at = principal_created_at
                    provenance_valid = bool(
                        provenance["sequence"] == previous_sequence + 1
                        and provenance["previous_signature"] == previous_signature
                        and provenance["actor"] == "Trusted Evaluator"
                        and provenance["authority"] == "operator"
                        and issued_at >= previous_issued_at
                        and verify_integrity_signature(
                            self.config.db_path,
                            "trusted-eval-evaluator-provenance",
                            provenance_values,
                            provenance["integrity_signature"],
                        )
                    )
                    if not provenance_valid:
                        conflicts.append({
                            "principal_id": provenance["principal_id"],
                            "anchor": "trusted_eval_evaluator_provenance",
                        })
                    previous_sequence = provenance["sequence"]
                    previous_signature = provenance["integrity_signature"]
                    previous_issued_at = issued_at
                for run in conn.execute("SELECT * FROM trusted_eval_runs ORDER BY id"):
                    run_values = {
                        "initiative_id": run["initiative_id"],
                        "attempt": run["attempt"],
                        "refs": {
                            "candidate": run["candidate_sha256"],
                            "dataset": run["dataset_sha256"],
                            "grader": run["grader_sha256"],
                            "environment": run["environment_sha256"],
                        },
                        "seed": run["seed"], "status": run["status"],
                        "evidence_ref": run["evidence_ref"],
                        "evidence_sha256": run["evidence_sha256"],
                        "evaluator_principal_id": run["evaluator_principal_id"],
                        "evaluator_actor": run["evaluator_actor"],
                        "evaluator_authority": run["evaluator_authority"],
                        "evaluator_credential_sha256": run[
                            "evaluator_credential_sha256"
                        ],
                        "evaluator_principal_created_at": run[
                            "evaluator_principal_created_at"
                        ],
                        "evaluator_provenance_signature": run[
                            "evaluator_provenance_signature"
                        ],
                        "contract_integrity_signature": run[
                            "contract_integrity_signature"
                        ],
                        "result_sha256": run["result_sha256"],
                        "created_at": run["created_at"],
                    }
                    if not verify_integrity_signature(
                        self.config.db_path, "trusted-eval-run", run_values,
                        run["integrity_signature"],
                    ):
                        conflicts.append({
                            "initiative_id": run["initiative_id"],
                            "attempt": run["attempt"], "anchor": "trusted_eval_run",
                        })
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='assurance_completion_bindings'"
            ).fetchone():
                from .pilot_gate import PilotGate

                gate = PilotGate(self.config)
                for completion in conn.execute(
                    "SELECT * FROM assurance_completion_bindings ORDER BY task_id"
                ):
                    if not gate.completion_binding_valid(conn, completion):
                        conflicts.append({
                            "task_id": completion["task_id"],
                            "anchor": "completion_binding",
                        })
        return {"status": "integrity_conflict" if conflicts else "ok", "conflicts": conflicts, "mode": "shadow"}

    def register_artifact(
        self, payload: dict[str, Any], *, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
        self._assert_principal(actor, principal_id, {"implementer", "executive", "chairman", "reviewer", "operator"})
        self._validate_artifact(payload, principal_id)
        digest = hashlib.sha256(_canonical(payload).encode("ascii")).hexdigest()
        now = utcnow()
        try:
            with self.store.connect() as conn:
                existing = conn.execute(
                    "SELECT 1 FROM assurance_artifacts WHERE artifact_id=? AND version=?",
                    (payload["artifact_id"], payload["version"]),
                ).fetchone()
                if existing:
                    raise AssuranceError("artifact versions are immutable")
                initiative = conn.execute(
                    """SELECT profile, risk_class FROM assurance_initiatives
                       WHERE initiative_id=?""",
                    (payload["initiative_id"],),
                ).fetchone()
                if initiative and (
                    initiative["profile"] != payload["profile"]
                    or initiative["risk_class"] != payload["risk_class"]
                ):
                    raise AssuranceError("initiative contract mismatch")
                conn.execute(
                    """INSERT INTO assurance_initiatives(
                           initiative_id, profile, risk_class, title, owner_principal,
                           status, mode, created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, 'discovery', 'shadow', ?, ?)
                       ON CONFLICT(initiative_id) DO NOTHING""",
                    (
                        payload["initiative_id"], payload["profile"], payload["risk_class"],
                        payload["initiative_id"], payload["owner_principal"], now, now,
                    ),
                )
                conn.execute(
                    """INSERT INTO assurance_artifacts(
                           artifact_id, initiative_id, kind, version, status, profile,
                           risk_class, owner_principal, repository_id, content_json,
                           content_sha256, created_at
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        payload["artifact_id"], payload["initiative_id"], payload["kind"],
                        payload["version"], payload["status"], payload["profile"],
                        payload["risk_class"], payload["owner_principal"],
                        payload["repository_id"], _canonical(payload), digest, now,
                    ),
                )
                conn.execute(
                    """INSERT INTO assurance_artifact_registrations(
                           artifact_id,version,content_sha256,created_at,integrity_signature
                       ) VALUES (?,?,?,?,?)""",
                    (
                        payload["artifact_id"], payload["version"], digest, now,
                        integrity_signature(self.config.db_path, "artifact-registration", {
                            "artifact_id": payload["artifact_id"],
                            "version": payload["version"],
                            "content_sha256": digest,
                            "created_at": now,
                        }),
                    ),
                )
                self._record_artifact_lifecycle(
                    conn, payload["artifact_id"], payload["version"], None, "draft",
                    now, principal_id, "artifact registered",
                )
                if payload["kind"] == "design_manifest":
                    for edge in payload["content"]["edges"]:
                        conn.execute(
                            """INSERT INTO assurance_links(
                                   initiative_id, from_artifact_id, relation,
                                   to_artifact_id, created_at
                               ) VALUES (?, ?, ?, ?, ?)""",
                            (
                                payload["initiative_id"], edge["from"], edge["relation"],
                                edge["to"], now,
                            ),
                        )
                self.store.audit(
                    conn, actor, "assurance_artifact_registered", "assurance_artifact",
                    f"{payload['artifact_id']}:v{payload['version']}",
                    {"principal_id": principal_id, "kind": payload["kind"], "sha256": digest, "mode": "shadow"},
                )
        except AssuranceError:
            raise
        return {
            "artifact_id": payload["artifact_id"], "version": payload["version"],
            "kind": payload["kind"], "status": payload["status"],
            "content_sha256": digest, "mode": "shadow",
        }

    def approve_artifact(
        self, artifact_id: str, version: int, *, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
        self._assert_principal(actor, principal_id, {"executive", "chairman", "reviewer"})
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM assurance_artifacts WHERE artifact_id=? AND version=?",
                (artifact_id, version),
            ).fetchone()
            if row is None:
                raise AssuranceError("artifact not found")
            if row["owner_principal"] == principal_id:
                raise AssuranceError("separation of duties forbids author self-approval")
            if row["status"] != "draft":
                raise AssuranceError("only draft artifacts may be approved")
            self._artifact_content_sha256(row)
            now = utcnow()
            self._record_artifact_lifecycle(
                conn, artifact_id, version, "draft", "approved", now,
                principal_id, "artifact approved",
            )
            conn.execute(
                """UPDATE assurance_artifacts
                   SET status='approved', approved_by_principal=?, approved_at=?
                   WHERE artifact_id=? AND version=?""",
                (principal_id, now, artifact_id, version),
            )
            approval_values = {
                "artifact_id": artifact_id,
                "version": version,
                "content_sha256": row["content_sha256"],
                "approved_by_principal": principal_id,
                "approved_at": now,
            }
            conn.execute(
                """INSERT INTO assurance_artifact_approvals(
                       artifact_id,version,content_sha256,approved_by_principal,
                       approved_at,integrity_signature
                   ) VALUES (?,?,?,?,?,?)""",
                (
                    artifact_id, version, row["content_sha256"], principal_id, now,
                    integrity_signature(
                        self.config.db_path, "artifact-approval", approval_values,
                    ),
                ),
            )
            self.store.audit(
                conn, actor, "assurance_artifact_approved", "assurance_artifact",
                f"{artifact_id}:v{version}", {"principal_id": principal_id, "mode": "shadow"},
            )
        return {"artifact_id": artifact_id, "version": version, "status": "approved", "mode": "shadow"}

    def classify_change(
        self, *, actor: str, principal_id: str, title: str, indicators: dict[str, bool],
    ) -> dict[str, Any]:
        self.init()
        self._assert_principal(actor, principal_id, {"executive", "chairman", "reviewer", "implementer", "operator"})
        allowed = {
            "editorial_only", "local_behavior", "public_contract", "persistent_schema",
            "cross_role", "authorization", "sensitive_data", "production",
            "irreversible_migration", "public_competitive_claim",
        }
        unknown = set(indicators) - allowed
        if unknown or not title.strip() or not indicators or any(type(v) is not bool for v in indicators.values()):
            raise AssuranceError("invalid shadow classification indicators")
        if any(indicators.get(k, False) for k in {
            "authorization", "sensitive_data", "production", "irreversible_migration", "public_competitive_claim",
        }):
            risk = "C3"
        elif any(indicators.get(k, False) for k in {"public_contract", "persistent_schema", "cross_role"}):
            risk = "C2"
        elif indicators.get("editorial_only") and not any(v for k, v in indicators.items() if k != "editorial_only"):
            risk = "C0"
        else:
            risk = "C1"
        now = utcnow()
        with self.store.connect() as conn:
            cur = conn.execute(
                """INSERT INTO assurance_classifications(
                       title, risk_class, indicators_json, actor, principal_id, mode, created_at
                   ) VALUES (?, ?, ?, ?, ?, 'shadow', ?)""",
                (title.strip(), risk, _canonical(indicators), actor, principal_id, now),
            )
            self.store.audit(
                conn, actor, "assurance_change_classified", "assurance_classification",
                cur.lastrowid, {"principal_id": principal_id, "risk_class": risk, "mode": "shadow"},
            )
        return {"classification_id": cur.lastrowid, "risk_class": risk, "mode": "shadow"}

    def list_artifacts(self) -> list[dict[str, Any]]:
        self.init()
        return [dict(row) for row in self.store.fetch_all(
            """SELECT artifact_id, initiative_id, kind, version, status, profile,
                      risk_class, owner_principal, repository_id, content_sha256,
                      approved_by_principal, approved_at, created_at
               FROM assurance_artifacts ORDER BY id"""
        )]

    def _validate_artifact(self, payload: dict[str, Any], principal_id: str) -> None:
        if not isinstance(payload, dict) or set(payload) != ARTIFACT_KEYS:
            raise AssuranceError("artifact has unknown or missing fields")
        if payload["schema_version"] != ARTIFACT_SCHEMA:
            raise AssuranceError("unsupported assurance artifact schema")
        if payload["kind"] not in ARTIFACT_KINDS:
            raise AssuranceError("unsupported artifact kind")
        if payload["profile"] not in PROFILES or payload["risk_class"] not in RISK_CLASSES:
            raise AssuranceError("invalid assurance profile or risk class")
        if payload["status"] != "draft":
            raise AssuranceError("new artifacts must be draft")
        if type(payload["version"]) is not int or payload["version"] < 1:
            raise AssuranceError("artifact version must be a positive integer")
        for key in {"artifact_id", "initiative_id", "owner_principal", "repository_id"}:
            if not isinstance(payload[key], str) or not payload[key].strip():
                raise AssuranceError(f"{key} must be non-empty")
        if payload["owner_principal"] != principal_id:
            raise AssuranceError("owner principal must match registering principal")
        if not isinstance(payload["content"], dict) or not payload["content"]:
            raise AssuranceError("artifact content must be a non-empty object")
        schema = ARTIFACT_CONTENT_SCHEMAS[payload["kind"]]
        if set(payload["content"]) != set(schema):
            raise AssuranceError(f"{payload['kind']} content has unknown or missing fields")
        for key, expected_type in schema.items():
            value = payload["content"][key]
            if not isinstance(value, expected_type) or isinstance(value, str) and not value.strip():
                raise AssuranceError(f"{payload['kind']} content field {key} has invalid type or value")
            if isinstance(value, list) and any(not isinstance(item, (str, dict)) for item in value):
                raise AssuranceError(f"{payload['kind']} content field {key} has invalid list values")
        if payload["kind"] == "design_manifest":
            self._validate_manifest(payload)

    def _validate_manifest(self, payload: dict[str, Any]) -> None:
        content = payload["content"]
        if set(content) != {"artifact_refs", "edges"}:
            raise AssuranceError("design manifest content has unknown or missing fields")
        refs = content["artifact_refs"]
        edges = content["edges"]
        if not isinstance(refs, list) or not isinstance(edges, list):
            raise AssuranceError("design manifest refs and edges must be arrays")
        kinds = [ref.get("kind") for ref in refs if isinstance(ref, dict)]
        for required in sorted(REQUIRED_MANIFEST_KINDS):
            if kinds.count(required) != 1:
                raise AssuranceError(f"design manifest requires exactly one {required}")
        with self.store.connect_readonly() as conn:
            for ref in refs:
                if not isinstance(ref, dict) or set(ref) != {"kind", "artifact_id", "version", "sha256"}:
                    raise AssuranceError("invalid design manifest artifact reference")
                row = conn.execute(
                    """SELECT kind,status,content_json,content_sha256,initiative_id
                       FROM assurance_artifacts WHERE artifact_id=? AND version=?""",
                    (ref["artifact_id"], ref["version"]),
                ).fetchone()
                if row is None or row["status"] != "approved" or row["kind"] != ref["kind"]:
                    raise AssuranceError("design manifest reference is not an approved matching artifact")
                if (
                    self._artifact_content_sha256(row) != ref["sha256"]
                    or row["initiative_id"] != payload["initiative_id"]
                ):
                    raise AssuranceError("design manifest reference hash or initiative mismatch")
        allowed_relations = {"governs", "refines", "evaluated_by", "baselined_by", "constrains"}
        nodes = {ref["artifact_id"] for ref in refs}
        graph = {node: set() for node in nodes}
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {"from", "relation", "to"}:
                raise AssuranceError("invalid design manifest edge")
            if edge["relation"] not in allowed_relations:
                raise AssuranceError("invalid design manifest relation")
            if edge["from"] not in nodes or edge["to"] not in nodes:
                raise AssuranceError("design manifest edge references an undeclared artifact")
            if edge["from"] == edge["to"]:
                raise AssuranceError("design manifest self-cycle is forbidden")
            graph[edge["from"]].add(edge["to"])
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise AssuranceError("design manifest cycle is forbidden")
            if node in visited:
                return
            visiting.add(node)
            for dependent in graph[node]:
                visit(dependent)
            visiting.remove(node)
            visited.add(node)

        for node in sorted(nodes):
            visit(node)
