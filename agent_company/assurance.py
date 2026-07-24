"""Shadow-mode development assurance artifact registry."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .config import CompanyConfig
from .db import Store, utcnow


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


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class AssuranceKernel:
    """Record proposed controls without changing current task dispatch behavior."""

    def __init__(self, config: CompanyConfig):
        self.config = config
        self.store = Store(config.db_path)

    def init(self) -> None:
        self.store.init_assurance()

    def _principal(self, conn: Any, actor: str, principal_id: str) -> dict[str, Any]:
        row = conn.execute(
            """SELECT principal_id, actor, authority FROM assurance_principals
               WHERE principal_id=? AND actor=? AND status='active'""",
            (principal_id, actor),
        ).fetchone()
        if row is None:
            raise AssuranceError("unregistered or mismatched assurance principal")
        return dict(row)

    @staticmethod
    def _require_authority(principal: dict[str, Any], allowed: set[str]) -> None:
        if principal["authority"] not in allowed:
            raise AssuranceError("principal lacks required assurance authority")

    def _assert_principal(self, actor: str, principal_id: str, allowed: set[str]) -> dict[str, Any]:
        with self.store.connect_readonly() as conn:
            principal = self._principal(conn, actor, principal_id)
        self._require_authority(principal, allowed)
        return principal

    def _initiative_artifact_set_sha256(self, conn: Any, initiative_id: str) -> str:
        rows = conn.execute(
            """SELECT artifact_id, version, content_sha256 FROM assurance_artifacts
               WHERE initiative_id=? AND status='approved'
               ORDER BY artifact_id, version""",
            (initiative_id,),
        ).fetchall()
        return hashlib.sha256(_canonical([
            {"ref": f"{row['artifact_id']}:v{row['version']}", "sha256": row["content_sha256"]}
            for row in rows
        ]).encode("ascii")).hexdigest()

    def create_initiative(
        self, initiative_id: str, title: str, profile: str, risk_class: str,
        *, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
        if profile not in PROFILES or risk_class not in RISK_CLASSES:
            raise AssuranceError("invalid assurance profile or risk class")
        if not all(isinstance(v, str) and v.strip() for v in {initiative_id, title, principal_id}):
            raise AssuranceError("initiative fields must be non-empty")
        now = utcnow()
        try:
            with self.store.connect() as conn:
                principal = self._principal(conn, actor, principal_id)
                self._require_authority(principal, {"executive", "chairman"})
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
            if target not in LIFECYCLE_TRANSITIONS.get(current, set()):
                raise AssuranceError(f"illegal lifecycle transition: {current} -> {target}")
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
                    """SELECT initiative_id, owner_principal, status, content_sha256
                       FROM assurance_artifacts WHERE artifact_id=? AND version=?""",
                    (artifact_id, version),
                ).fetchone()
                if row is None or row["initiative_id"] != initiative_id or row["status"] != "approved":
                    raise AssuranceError("gate references must be approved artifacts in the initiative")
                if row["owner_principal"] == principal_id:
                    raise AssuranceError("separation of duties forbids author gate approval")
                ordered.append({"ref": ref, "sha256": row["content_sha256"]})
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
            for row in conn.execute("SELECT artifact_id, version, content_json, content_sha256 FROM assurance_artifacts ORDER BY id"):
                actual = hashlib.sha256(row["content_json"].encode("ascii")).hexdigest()
                if actual != row["content_sha256"]:
                    conflicts.append({
                        "artifact_id": row["artifact_id"], "version": row["version"],
                        "expected_sha256": row["content_sha256"], "actual_sha256": actual,
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
            now = utcnow()
            conn.execute(
                """UPDATE assurance_artifacts
                   SET status='approved', approved_by_principal=?, approved_at=?
                   WHERE artifact_id=? AND version=?""",
                (principal_id, now, artifact_id, version),
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
                    """SELECT kind, status, content_sha256, initiative_id
                       FROM assurance_artifacts WHERE artifact_id=? AND version=?""",
                    (ref["artifact_id"], ref["version"]),
                ).fetchone()
                if row is None or row["status"] != "approved" or row["kind"] != ref["kind"]:
                    raise AssuranceError("design manifest reference is not an approved matching artifact")
                if row["content_sha256"] != ref["sha256"] or row["initiative_id"] != payload["initiative_id"]:
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
