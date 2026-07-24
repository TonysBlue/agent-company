"""Shadow-mode development assurance artifact registry."""

from __future__ import annotations

import hashlib
import json
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
REQUIRED_MANIFEST_KINDS = {
    "goal_contract", "design_record", "behavior_spec", "eval_contract", "baseline_report",
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
        self.store.init()

    def register_artifact(
        self, payload: dict[str, Any], *, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
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
        for edge in edges:
            if not isinstance(edge, dict) or set(edge) != {"from", "relation", "to"}:
                raise AssuranceError("invalid design manifest edge")
            if edge["relation"] not in allowed_relations:
                raise AssuranceError("invalid design manifest relation")
            if edge["from"] == edge["to"]:
                raise AssuranceError("design manifest self-cycle is forbidden")
