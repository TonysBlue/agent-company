"""Opt-in dispatch and completion enforcement for the approved pilot."""

from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timezone
from typing import Any

from .config import CompanyConfig
from .assurance import AssuranceError, AssuranceKernel
from .db import Store, utcnow


APPROVED_PILOT = "pilot-c2-approved-for-build"


class PilotGate:
    def __init__(self, config: CompanyConfig):
        self._config = config
        self.store = Store(config.db_path)

    def init(self) -> None:
        self.store.init_assurance()
        with self.store.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS assurance_task_bindings (
                    task_id INTEGER PRIMARY KEY,
                    initiative_id TEXT NOT NULL,
                    pilot INTEGER NOT NULL,
                    artifact_set_sha256 TEXT,
                    completion_result_sha256 TEXT,
                    review_decision_ref TEXT,
                    completed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_pilot_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO assurance_pilot_config(key,value,updated_at)
                    VALUES ('kill_switch','false','bootstrap');
                """
            )
            columns = {
                row[1] for row in conn.execute("PRAGMA table_info(assurance_task_bindings)")
            }
            for name in (
                "completion_result_sha256", "review_decision_ref", "completed_at",
            ):
                if name not in columns:
                    conn.execute(f"ALTER TABLE assurance_task_bindings ADD COLUMN {name} TEXT")

    def bind(
        self, task_id: int, initiative_id: str, *, pilot: bool,
        artifact_set_sha256: str | None = None, actor: str = "", principal_id: str = "",
        reason: str = "initial pilot binding",
    ) -> None:
        self.init()
        kernel = AssuranceKernel(self._config)
        kernel._assert_principal(actor, principal_id, {"executive", "chairman"})
        if not reason.strip():
            raise ValueError("pilot binding reason is required")
        if pilot and initiative_id != APPROVED_PILOT:
            raise ValueError("only the approved pilot initiative may enable enforcement")
        if artifact_set_sha256 is not None and len(artifact_set_sha256) != 64:
            raise ValueError("artifact set sha256 must be 64 characters")
        now = utcnow()
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task does not exist")
            if task["status"] != "open":
                raise ValueError("pilot binding requires an open unclaimed task")
            existing = conn.execute(
                "SELECT initiative_id,pilot,artifact_set_sha256 FROM assurance_task_bindings WHERE task_id=?",
                (task_id,),
            ).fetchone()
            conn.execute(
                """INSERT INTO assurance_task_bindings(
                       task_id,initiative_id,pilot,artifact_set_sha256,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(task_id) DO UPDATE SET initiative_id=excluded.initiative_id,
                     pilot=excluded.pilot,artifact_set_sha256=excluded.artifact_set_sha256,
                     updated_at=excluded.updated_at""",
                (task_id, initiative_id, int(pilot), artifact_set_sha256, now, now),
            )
            conn.execute(
                """INSERT INTO audit_log(ts,actor,action,entity,entity_id,details)
                   VALUES (?,?,?,?,?,?)""",
                (now, actor, "bind_pilot_task", "task", str(task_id), json.dumps({
                    "initiative_id": initiative_id, "pilot": pilot,
                    "previous": dict(existing) if existing is not None else None,
                    "reason": reason.strip(),
                }, sort_keys=True)),
            )

    def set_kill_switch(
        self, enabled: bool, *, actor: str, principal_id: str, reason: str,
    ) -> None:
        self.init()
        AssuranceKernel(self._config)._assert_principal(
            actor, principal_id, {"executive", "chairman"}
        )
        if not reason.strip():
            raise ValueError("a reason is required")
        now = utcnow()
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_pilot_config SET value=?,updated_at=? WHERE key='kill_switch'",
                ("true" if enabled else "false", now),
            )
            self.store.audit(
                conn, actor, "set_pilot_kill_switch", "assurance_pilot", APPROVED_PILOT,
                {"enabled": enabled, "reason": reason.strip()},
            )

    def dispatch_decision(self, task: dict[str, Any], *, conn: Any | None = None) -> dict[str, Any]:
        if conn is not None:
            return self._dispatch_decision(conn, task)
        self.init()
        with self.store.connect_readonly() as readonly:
            return self._dispatch_decision(readonly, task)

    def _dispatch_decision(self, conn: Any, task: dict[str, Any]) -> dict[str, Any]:
            binding = conn.execute(
                "SELECT * FROM assurance_task_bindings WHERE task_id=?", (int(task["id"]),)
            ).fetchone()
            if binding is None:
                return {"allowed": True, "reason": "unbound"}
            if not binding["pilot"]:
                return {"allowed": True, "reason": "non-pilot"}
            killed = conn.execute(
                "SELECT value FROM assurance_pilot_config WHERE key='kill_switch'"
            ).fetchone()["value"] == "true"
            if killed:
                return {"allowed": True, "reason": "pilot enforcement killed"}
            if binding["initiative_id"] != APPROVED_PILOT:
                return {"allowed": False, "reason": "unapproved pilot initiative"}
            initiative = conn.execute(
                "SELECT status,mode FROM assurance_initiatives WHERE initiative_id=?",
                (binding["initiative_id"],),
            ).fetchone()
            if initiative is None or initiative["status"] != "approved_for_build":
                return {"allowed": False, "reason": "pilot requires approved_for_build and passing G4"}
            decision = conn.execute(
                """SELECT decision,artifact_set_sha256,expires_at FROM assurance_gate_decisions
                   WHERE initiative_id=? AND gate='G4'
                   ORDER BY id DESC LIMIT 1""",
                (binding["initiative_id"],),
            ).fetchone()
            if decision is None or decision["decision"] != "pass":
                return {"allowed": False, "reason": "pilot requires passing G4"}
            kernel = AssuranceKernel(self._config)
            conflicts = []
            for artifact in conn.execute(
                """SELECT artifact_id,version,content_json,content_sha256
                   FROM assurance_artifacts WHERE kind!='review_decision'"""
            ):
                actual = hashlib.sha256(artifact["content_json"].encode("utf-8")).hexdigest()
                if actual != artifact["content_sha256"]:
                    conflicts.append(f"{artifact['artifact_id']}:v{artifact['version']}")
            if conflicts:
                return {"allowed": False, "reason": "assurance integrity conflict"}
            current_hash = kernel._initiative_build_artifact_set_sha256(
                conn, binding["initiative_id"],
            )
            stale = conn.execute(
                """SELECT 1 FROM assurance_artifacts
                   WHERE initiative_id=? AND status='stale' AND kind!='review_decision'
                   LIMIT 1""",
                (binding["initiative_id"],),
            ).fetchone()
            if stale or current_hash != decision["artifact_set_sha256"]:
                return {"allowed": False, "reason": "G4 artifact set is stale"}
            if decision["expires_at"]:
                expiry = datetime.fromisoformat(decision["expires_at"])
                if expiry <= datetime.now(timezone.utc):
                    return {"allowed": False, "reason": "G4 decision expired"}
            if not binding["artifact_set_sha256"] or binding["artifact_set_sha256"] != decision["artifact_set_sha256"]:
                return {"allowed": False, "reason": "pilot artifact set hash mismatch"}
            return {
                "allowed": True, "reason": "approved pilot G4",
                "initiative_id": binding["initiative_id"],
                "artifact_set_sha256": binding["artifact_set_sha256"],
            }

    def completion_decision(
        self, task: dict[str, Any], *, conn: Any | None = None,
    ) -> dict[str, Any]:
        if conn is not None:
            return self._completion_decision(conn, task)
        self.init()
        with self.store.connect_readonly() as readonly:
            return self._completion_decision(readonly, task)

    def _completion_decision(self, conn: Any, task: dict[str, Any]) -> dict[str, Any]:
        binding = conn.execute(
            "SELECT * FROM assurance_task_bindings WHERE task_id=?", (int(task["id"]),)
        ).fetchone()
        if binding is None:
            return {"allowed": True, "reason": "unbound"}
        if not binding["pilot"]:
            return {"allowed": True, "reason": "non-pilot"}
        if binding["initiative_id"] != APPROVED_PILOT:
            return {"allowed": False, "reason": "unapproved pilot initiative"}

        initiative_id = str(binding["initiative_id"])
        initiative = conn.execute(
            "SELECT risk_class FROM assurance_initiatives WHERE initiative_id=?",
            (initiative_id,),
        ).fetchone()
        if initiative is None or initiative["risk_class"] not in {"C2", "C3"}:
            return {"allowed": False, "reason": "bound pilot completion requires C2/C3 assurance"}

        kernel = AssuranceKernel(self._config)
        artifacts = conn.execute(
            """SELECT artifact_id,version,kind,status,owner_principal,approved_by_principal,
                      approved_at,content_json,content_sha256
               FROM assurance_artifacts WHERE initiative_id=? ORDER BY id""",
            (initiative_id,),
        ).fetchall()
        for artifact in artifacts:
            actual = ""
            try:
                actual = hashlib.sha256(artifact["content_json"].encode("ascii")).hexdigest()
                payload = json.loads(artifact["content_json"])
                metadata_matches = (
                    payload["artifact_id"] == artifact["artifact_id"]
                    and payload["version"] == artifact["version"]
                    and payload["kind"] == artifact["kind"]
                    and payload["status"] == "draft"
                    and payload["owner_principal"] == artifact["owner_principal"]
                    and payload["initiative_id"] == initiative_id
                )
            except (KeyError, TypeError, UnicodeEncodeError, json.JSONDecodeError):
                metadata_matches = False
            registration = conn.execute(
                """SELECT content_sha256 FROM assurance_artifact_registrations
                   WHERE artifact_id=? AND version=?""",
                (artifact["artifact_id"], artifact["version"]),
            ).fetchone()
            registration_matches = (
                registration is not None
                and registration["content_sha256"] == artifact["content_sha256"]
            )
            if artifact["kind"] != "review_decision" and registration is None:
                registered_hashes = []
                artifact_ref = f"{artifact['artifact_id']}:v{artifact['version']}"
                for row in conn.execute(
                    """SELECT details FROM audit_log
                       WHERE action='assurance_artifact_registered'
                         AND entity='assurance_artifact' AND entity_id=?""",
                    (artifact_ref,),
                ):
                    try:
                        registered_hashes.append(json.loads(row["details"])["sha256"])
                    except (KeyError, TypeError, json.JSONDecodeError):
                        continue
                registration_matches = registered_hashes == [artifact["content_sha256"]]
            if (
                actual != artifact["content_sha256"]
                or not metadata_matches
                or not registration_matches
            ):
                return {"allowed": False, "reason": "bound pilot assurance integrity conflict"}

        build_hash = kernel._initiative_build_artifact_set_sha256(conn, initiative_id)
        build_gate = conn.execute(
            """SELECT decision,artifact_set_sha256,expires_at
               FROM assurance_gate_decisions
               WHERE initiative_id=? AND gate='G4' ORDER BY id DESC LIMIT 1""",
            (initiative_id,),
        ).fetchone()
        if (
            build_gate is None
            or build_gate["decision"] not in {"pass", "pass_with_conditions"}
            or not binding["artifact_set_sha256"]
            or binding["artifact_set_sha256"] != build_gate["artifact_set_sha256"]
            or binding["artifact_set_sha256"] != build_hash
        ):
            return {"allowed": False, "reason": "bound pilot artifact set is stale or mismatched"}
        if build_gate["expires_at"]:
            try:
                expiry = datetime.fromisoformat(build_gate["expires_at"])
            except ValueError:
                return {"allowed": False, "reason": "bound pilot build decision expiry is invalid"}
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                return {"allowed": False, "reason": "bound pilot build decision expired"}

        trusted_tables = {
            row["name"] for row in conn.execute(
                """SELECT name FROM sqlite_master WHERE type='table' AND name IN (
                       'trusted_eval_runs','trusted_eval_quarantines',
                       'trusted_eval_manifests','trusted_eval_contracts'
                   )"""
            )
        }
        if len(trusted_tables) != 4:
            return {
                "allowed": False,
                "reason": "bound pilot completion requires a valid Trusted Eval",
            }
        if conn.execute(
            "SELECT 1 FROM trusted_eval_quarantines WHERE initiative_id=?", (initiative_id,),
        ).fetchone():
            return {"allowed": False, "reason": "bound pilot Trusted Eval is quarantined"}
        try:
            result_sha256 = kernel._validate_trusted_g5(conn, initiative_id)
        except AssuranceError as exc:
            return {
                "allowed": False,
                "reason": f"bound pilot completion requires a valid Trusted Eval: {exc}",
            }

        reviews = [
            artifact for artifact in artifacts
            if artifact["kind"] == "review_decision" and artifact["status"] == "approved"
        ]
        if not reviews:
            return {
                "allowed": False,
                "reason": "bound pilot completion requires an affirmative independent Review Decision",
            }
        build_owners = {
            artifact["owner_principal"]
            for artifact in artifacts
            if artifact["kind"] != "review_decision" and artifact["status"] == "approved"
        }
        task_principal = conn.execute(
            "SELECT principal_id FROM assurance_principals WHERE actor=? AND status='active'",
            (task["owner"],),
        ).fetchone()
        evaluator_principals = {
            row["evaluator_principal_id"] for row in conn.execute(
                """SELECT evaluator_principal_id FROM trusted_eval_runs
                   WHERE initiative_id=?""",
                (initiative_id,),
            ) if row["evaluator_principal_id"]
        }
        if not evaluator_principals:
            return {
                "allowed": False,
                "reason": "bound pilot Trusted Eval lacks evaluator identity lineage",
            }
        review_ref = ""
        for review in reviews:
            approver = conn.execute(
                """SELECT authority,status FROM assurance_principals
                   WHERE principal_id=?""",
                (review["approved_by_principal"],),
            ).fetchone()
            approval_ref = f"{review['artifact_id']}:v{review['version']}"
            approval_audits = conn.execute(
                """SELECT details FROM audit_log
                   WHERE action='assurance_artifact_approved'
                     AND entity='assurance_artifact' AND entity_id=?""",
                (approval_ref,),
            ).fetchall()
            approval_audited = False
            for row in approval_audits:
                try:
                    details = json.loads(row["details"])
                except (TypeError, json.JSONDecodeError):
                    continue
                if details.get("principal_id") == review["approved_by_principal"]:
                    approval_audited = True
                    break
            if (
                not review["approved_by_principal"]
                or not review["approved_at"]
                or review["approved_by_principal"] == review["owner_principal"]
                or approver is None
                or approver["status"] != "active"
                or approver["authority"] not in {"executive", "chairman", "reviewer"}
                or not approval_audited
            ):
                return {"allowed": False, "reason": "Review Decision approval metadata is invalid"}
            payload = json.loads(review["content_json"])["content"]
            principal = conn.execute(
                """SELECT authority,status FROM assurance_principals
                   WHERE principal_id=?""",
                (review["owner_principal"],),
            ).fetchone()
            if (
                principal is None
                or principal["status"] != "active"
                or principal["authority"] != "reviewer"
                or review["owner_principal"] in build_owners
                or review["owner_principal"] in evaluator_principals
                or task_principal is None
                or review["owner_principal"] == task_principal["principal_id"]
            ):
                return {"allowed": False, "reason": "Review Decision is not independent"}
            if payload["decision"] not in {"approve", "pass"} or payload["findings"]:
                return {"allowed": False, "reason": "Review Decision is not affirmative"}
            refs = payload["evidence_refs"]
            if result_sha256 not in refs:
                return {"allowed": False, "reason": "Review Decision does not bind the exact Trusted Eval result"}
            if binding["artifact_set_sha256"] not in refs:
                return {"allowed": False, "reason": "Review Decision does not bind the exact artifact set"}
            review_ref = approval_ref

        return {
            "allowed": True,
            "reason": "bound pilot completion assurance passed",
            "assurance": {
                "initiative_id": initiative_id,
                "artifact_set_sha256": binding["artifact_set_sha256"],
                "result_sha256": result_sha256,
                "review_decision_ref": review_ref,
            },
        }

    @staticmethod
    def record_completion(
        conn: Any, task_id: int, assurance: dict[str, str], completed_at: str,
    ) -> None:
        updated = conn.execute(
            """UPDATE assurance_task_bindings
               SET completion_result_sha256=?,review_decision_ref=?,completed_at=?,updated_at=?
               WHERE task_id=? AND pilot=1 AND completion_result_sha256 IS NULL""",
            (
                assurance["result_sha256"], assurance["review_decision_ref"],
                completed_at, completed_at, task_id,
            ),
        ).rowcount
        if updated != 1:
            raise ValueError(f"task {task_id} assurance completion binding changed concurrently")
