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
from .integrity import signature as integrity_signature, verify as verify_integrity_signature


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
                CREATE TABLE IF NOT EXISTS assurance_execution_bindings (
                    task_id INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    initiative_id TEXT NOT NULL,
                    artifact_set_sha256 TEXT NOT NULL,
                    evaluation_policy_sha256 TEXT NOT NULL,
                    principal_state_sha256 TEXT NOT NULL,
                    context_bundle_sha256 TEXT NOT NULL,
                    fencing_token_sha256 TEXT,
                    integrity_signature TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id,generation)
                );
                CREATE TABLE IF NOT EXISTS assurance_claim_bindings (
                    task_id INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    initiative_id TEXT NOT NULL,
                    artifact_set_sha256 TEXT NOT NULL,
                    fencing_token_sha256 TEXT NOT NULL,
                    integrity_signature TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id,generation)
                );
                CREATE TABLE IF NOT EXISTS assurance_pilot_claim_history (
                    task_id INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    initiative_id TEXT NOT NULL,
                    artifact_set_sha256 TEXT NOT NULL,
                    fencing_token_sha256 TEXT NOT NULL,
                    integrity_signature TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id,generation)
                );
                CREATE TABLE IF NOT EXISTS assurance_completion_bindings (
                    task_id INTEGER PRIMARY KEY,
                    generation INTEGER NOT NULL,
                    initiative_id TEXT NOT NULL,
                    artifact_set_sha256 TEXT NOT NULL,
                    trusted_eval_result_sha256 TEXT NOT NULL,
                    review_decision_ref TEXT NOT NULL,
                    review_content_sha256 TEXT NOT NULL,
                    task_result_sha256 TEXT NOT NULL,
                    evidence_paths_sha256 TEXT NOT NULL,
                    completed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    integrity_signature TEXT NOT NULL
                );
                INSERT OR IGNORE INTO assurance_pilot_config(key,value,updated_at)
                    VALUES ('kill_switch','false','bootstrap');
                """
            )
            execution_binding_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(assurance_execution_bindings)"
                )
            }
            for name in ("fencing_token_sha256", "integrity_signature"):
                if name not in execution_binding_columns:
                    conn.execute(
                        f"ALTER TABLE assurance_execution_bindings ADD COLUMN {name} TEXT"
                    )
            conn.executescript(
                """
                DROP TRIGGER IF EXISTS assurance_execution_bindings_immutable_update;
                DROP TRIGGER IF EXISTS assurance_execution_bindings_immutable_delete;
                DROP TRIGGER IF EXISTS assurance_claim_bindings_immutable_update;
                DROP TRIGGER IF EXISTS assurance_claim_bindings_immutable_delete;
                DROP TRIGGER IF EXISTS assurance_pilot_claim_history_immutable_update;
                DROP TRIGGER IF EXISTS assurance_pilot_claim_history_immutable_delete;
                DROP TRIGGER IF EXISTS assurance_task_bindings_claimed_immutable_update;
                DROP TRIGGER IF EXISTS assurance_task_bindings_claimed_immutable_delete;
                DROP TRIGGER IF EXISTS assurance_completion_bindings_insert_guard;
                DROP TRIGGER IF EXISTS assurance_completion_bindings_immutable_update;
                DROP TRIGGER IF EXISTS assurance_completion_bindings_immutable_delete;
                DROP TRIGGER IF EXISTS tasks_bound_pilot_completion_guard;
                CREATE TRIGGER assurance_execution_bindings_immutable_update
                    BEFORE UPDATE ON assurance_execution_bindings
                    BEGIN SELECT RAISE(ABORT, 'assurance execution binding is immutable'); END;
                CREATE TRIGGER assurance_execution_bindings_immutable_delete
                    BEFORE DELETE ON assurance_execution_bindings
                    BEGIN SELECT RAISE(ABORT, 'assurance execution binding is immutable'); END;
                CREATE TRIGGER assurance_claim_bindings_immutable_update
                    BEFORE UPDATE ON assurance_claim_bindings
                    BEGIN SELECT RAISE(ABORT, 'assurance claim binding is immutable'); END;
                CREATE TRIGGER assurance_claim_bindings_immutable_delete
                    BEFORE DELETE ON assurance_claim_bindings
                    BEGIN SELECT RAISE(ABORT, 'assurance claim binding is immutable'); END;
                CREATE TRIGGER assurance_pilot_claim_history_immutable_update
                    BEFORE UPDATE ON assurance_pilot_claim_history
                    BEGIN SELECT RAISE(ABORT, 'assurance pilot claim history is immutable'); END;
                CREATE TRIGGER assurance_pilot_claim_history_immutable_delete
                    BEFORE DELETE ON assurance_pilot_claim_history
                    BEGIN SELECT RAISE(ABORT, 'assurance pilot claim history is immutable'); END;
                CREATE TRIGGER assurance_completion_bindings_insert_guard
                    BEFORE INSERT ON assurance_completion_bindings
                    WHEN
                        length(NEW.artifact_set_sha256)!=64
                        OR length(NEW.trusted_eval_result_sha256)!=64
                        OR length(NEW.review_content_sha256)!=64
                        OR length(NEW.task_result_sha256)!=64
                        OR length(NEW.evidence_paths_sha256)!=64
                        OR assurance_completion_signature_valid(
                            NEW.task_id,NEW.generation,NEW.initiative_id,
                            NEW.artifact_set_sha256,NEW.trusted_eval_result_sha256,
                            NEW.review_decision_ref,NEW.review_content_sha256,
                            NEW.task_result_sha256,NEW.evidence_paths_sha256,
                            NEW.completed_at,NEW.created_at,NEW.integrity_signature
                        )!=1
                        OR NOT EXISTS (
                            SELECT 1
                            FROM tasks task
                            JOIN task_executions execution ON execution.task_id=task.id
                            JOIN assurance_task_bindings binding ON binding.task_id=task.id
                            JOIN assurance_claim_bindings claim
                              ON claim.task_id=task.id AND claim.generation=NEW.generation
                            JOIN assurance_pilot_claim_history history
                              ON history.task_id=task.id AND history.generation=NEW.generation
                            WHERE task.id=NEW.task_id
                              AND task.status='in_progress'
                              AND execution.recovery_status='running'
                              AND execution.generation=NEW.generation
                              AND binding.pilot=1
                              AND binding.initiative_id=NEW.initiative_id
                              AND binding.artifact_set_sha256=NEW.artifact_set_sha256
                              AND binding.completion_result_sha256 IS NULL
                              AND binding.review_decision_ref IS NULL
                              AND binding.completed_at IS NULL
                              AND claim.initiative_id=NEW.initiative_id
                              AND claim.artifact_set_sha256=NEW.artifact_set_sha256
                              AND history.initiative_id=claim.initiative_id
                              AND history.artifact_set_sha256=claim.artifact_set_sha256
                              AND history.fencing_token_sha256=claim.fencing_token_sha256
                        )
                        OR EXISTS (
                            SELECT 1 FROM trusted_eval_quarantines
                            WHERE initiative_id=NEW.initiative_id
                        )
                        OR NEW.trusted_eval_result_sha256 IS NOT (
                            SELECT result_sha256 FROM trusted_eval_runs
                            WHERE initiative_id=NEW.initiative_id
                              AND status='completed'
                            ORDER BY attempt DESC LIMIT 1
                        )
                        OR NOT EXISTS (
                            SELECT 1 FROM trusted_eval_runs run
                            WHERE run.initiative_id=NEW.initiative_id
                              AND run.result_sha256=NEW.trusted_eval_result_sha256
                              AND run.status='completed'
                              AND run.evidence_sha256 IS NOT NULL
                        )
                        OR NOT EXISTS (
                            SELECT 1 FROM assurance_artifacts review
                            JOIN assurance_artifact_approvals approval
                              ON approval.artifact_id=review.artifact_id
                             AND approval.version=review.version
                            JOIN assurance_principals reviewer
                              ON reviewer.principal_id=review.owner_principal
                            JOIN assurance_principals approver
                              ON approver.principal_id=review.approved_by_principal
                            WHERE review.initiative_id=NEW.initiative_id
                              AND review.kind='review_decision'
                              AND review.status='approved'
                              AND review.artifact_id || ':v' || review.version
                                  =NEW.review_decision_ref
                              AND review.content_sha256=NEW.review_content_sha256
                              AND assurance_artifact_body_valid(
                                  review.content_json,review.content_sha256
                              )=1
                              AND review.approved_by_principal IS NOT review.owner_principal
                              AND approval.content_sha256=review.content_sha256
                              AND approval.approved_by_principal=review.approved_by_principal
                              AND approval.approved_at=review.approved_at
                              AND reviewer.status='active'
                              AND reviewer.authority='reviewer'
                              AND approver.status='active'
                              AND approver.authority IN ('executive','chairman','reviewer')
                              AND EXISTS (
                                  SELECT 1 FROM json_each(
                                      review.content_json,'$.content.evidence_refs'
                                  ) WHERE value=NEW.trusted_eval_result_sha256
                              )
                              AND EXISTS (
                                  SELECT 1 FROM json_each(
                                      review.content_json,'$.content.evidence_refs'
                                  ) WHERE value=NEW.artifact_set_sha256
                              )
                              AND json_extract(
                                  review.content_json,'$.content.decision'
                              ) IN ('approve','pass')
                              AND json_array_length(
                                  review.content_json,'$.content.findings'
                              )=0
                        )
                        OR EXISTS (
                            SELECT 1 FROM assurance_artifacts artifact
                            WHERE artifact.initiative_id=NEW.initiative_id
                              AND assurance_artifact_body_valid(
                                  artifact.content_json,artifact.content_sha256
                              )!=1
                        )
                        OR NEW.artifact_set_sha256 IS NOT (
                            SELECT assurance_artifact_set_sha256(
                                artifact_id || ':v' || version,content_sha256
                            ) FROM (
                                SELECT artifact_id,version,content_sha256
                                FROM assurance_artifacts
                                WHERE initiative_id=NEW.initiative_id
                                  AND status='approved'
                                  AND kind!='review_decision'
                                ORDER BY artifact_id,version
                            )
                        )
                    BEGIN SELECT RAISE(ABORT, 'completion binding integrity conflict'); END;
                CREATE TRIGGER assurance_completion_bindings_immutable_update
                    BEFORE UPDATE ON assurance_completion_bindings
                    BEGIN SELECT RAISE(ABORT, 'assurance completion binding is immutable'); END;
                CREATE TRIGGER assurance_completion_bindings_immutable_delete
                    BEFORE DELETE ON assurance_completion_bindings
                    BEGIN SELECT RAISE(ABORT, 'assurance completion binding is immutable'); END;
                CREATE TRIGGER assurance_task_bindings_claimed_immutable_update
                    BEFORE UPDATE ON assurance_task_bindings
                    WHEN EXISTS (
                        SELECT 1 FROM assurance_pilot_claim_history
                        WHERE task_id=OLD.task_id
                    )
                    AND (
                        NEW.task_id IS NOT OLD.task_id
                        OR NEW.initiative_id IS NOT OLD.initiative_id
                        OR NEW.pilot IS NOT OLD.pilot
                        OR NEW.artifact_set_sha256 IS NOT OLD.artifact_set_sha256
                        OR NEW.created_at IS NOT OLD.created_at
                        OR OLD.completion_result_sha256 IS NOT NULL
                        OR OLD.review_decision_ref IS NOT NULL
                        OR OLD.completed_at IS NOT NULL
                        OR NEW.completion_result_sha256 IS NULL
                        OR NEW.review_decision_ref IS NULL
                        OR NEW.completed_at IS NULL
                        OR NEW.updated_at IS NOT NEW.completed_at
                        OR NOT EXISTS (
                            SELECT 1 FROM assurance_completion_bindings completion
                            WHERE completion.task_id=OLD.task_id
                              AND completion.initiative_id=OLD.initiative_id
                              AND completion.artifact_set_sha256=OLD.artifact_set_sha256
                              AND completion.trusted_eval_result_sha256
                                  =NEW.completion_result_sha256
                              AND completion.review_decision_ref=NEW.review_decision_ref
                              AND completion.completed_at=NEW.completed_at
                              AND assurance_completion_signature_valid(
                                  completion.task_id,completion.generation,
                                  completion.initiative_id,completion.artifact_set_sha256,
                                  completion.trusted_eval_result_sha256,
                                  completion.review_decision_ref,
                                  completion.review_content_sha256,
                                  completion.task_result_sha256,
                                  completion.evidence_paths_sha256,
                                  completion.completed_at,completion.created_at,
                                  completion.integrity_signature
                              )=1
                        )
                    )
                    BEGIN SELECT RAISE(ABORT, 'claimed assurance task binding is immutable'); END;
                CREATE TRIGGER assurance_task_bindings_claimed_immutable_delete
                    BEFORE DELETE ON assurance_task_bindings
                    WHEN EXISTS (
                        SELECT 1 FROM assurance_pilot_claim_history
                        WHERE task_id=OLD.task_id
                    )
                    BEGIN SELECT RAISE(ABORT, 'claimed assurance task binding is immutable'); END;
                CREATE TRIGGER tasks_bound_pilot_completion_guard
                    BEFORE UPDATE OF status,result ON tasks
                    WHEN EXISTS (
                        SELECT 1 FROM assurance_pilot_claim_history
                        WHERE task_id=OLD.id
                    )
                    AND NEW.status='done'
                    AND (
                        OLD.status!='in_progress'
                        OR NEW.result IS NULL
                        OR NOT EXISTS (
                            SELECT 1 FROM task_executions execution
                            WHERE execution.task_id=OLD.id
                              AND execution.recovery_status='completed'
                        )
                        OR NOT EXISTS (
                            SELECT 1 FROM assurance_task_bindings binding
                            WHERE binding.task_id=OLD.id
                              AND binding.completion_result_sha256 IS NOT NULL
                              AND binding.review_decision_ref IS NOT NULL
                              AND binding.completed_at IS NOT NULL
                        )
                        OR NOT EXISTS (
                            SELECT 1
                            FROM assurance_completion_bindings completion
                            JOIN assurance_task_bindings binding
                              ON binding.task_id=completion.task_id
                            JOIN task_executions execution
                              ON execution.task_id=completion.task_id
                            WHERE completion.task_id=OLD.id
                              AND completion.generation=execution.generation
                              AND completion.initiative_id=binding.initiative_id
                              AND completion.artifact_set_sha256
                                  =binding.artifact_set_sha256
                              AND completion.trusted_eval_result_sha256
                                  =binding.completion_result_sha256
                              AND completion.review_decision_ref
                                  =binding.review_decision_ref
                              AND completion.completed_at=binding.completed_at
                              AND completion.task_result_sha256
                                  =assurance_sha256(NEW.result)
                              AND completion.evidence_paths_sha256
                                  =assurance_sha256(execution.evidence_paths)
                              AND json_extract(
                                  NEW.result,'$.assurance.initiative_id'
                              )=completion.initiative_id
                              AND json_extract(
                                  NEW.result,'$.assurance.artifact_set_sha256'
                              )=completion.artifact_set_sha256
                              AND json_extract(
                                  NEW.result,'$.assurance.result_sha256'
                              )=completion.trusted_eval_result_sha256
                              AND json_extract(
                                  NEW.result,'$.assurance.review_decision_ref'
                              )=completion.review_decision_ref
                              AND (
                                  SELECT COUNT(*) FROM json_each(
                                      NEW.result,'$.assurance'
                                  )
                              )=4
                              AND assurance_completion_signature_valid(
                                  completion.task_id,completion.generation,
                                  completion.initiative_id,completion.artifact_set_sha256,
                                  completion.trusted_eval_result_sha256,
                                  completion.review_decision_ref,
                                  completion.review_content_sha256,
                                  completion.task_result_sha256,
                                  completion.evidence_paths_sha256,
                                  completion.completed_at,completion.created_at,
                                  completion.integrity_signature
                              )=1
                        )
                    )
                    BEGIN SELECT RAISE(ABORT, 'bound pilot completion is not atomic'); END;
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
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            history_candidates: list[dict[str, Any]] = []
            for claim in conn.execute(
                "SELECT * FROM assurance_claim_bindings ORDER BY task_id,generation"
            ).fetchall():
                values = {
                    key: claim[key] for key in (
                        "task_id", "generation", "initiative_id", "artifact_set_sha256",
                        "fencing_token_sha256", "created_at",
                    )
                }
                if not verify_integrity_signature(
                    self._config.db_path, "claim-binding", values,
                    claim["integrity_signature"],
                ):
                    raise ValueError("assurance claim binding integrity conflict")
                history = conn.execute(
                    """SELECT * FROM assurance_pilot_claim_history
                       WHERE task_id=? AND generation=?""",
                    (claim["task_id"], claim["generation"]),
                ).fetchone()
                if history is None:
                    history_candidates.append(values)
                    continue
                history_values = {key: history[key] for key in values}
                if history_values != values or not verify_integrity_signature(
                    self._config.db_path, "pilot-claim-history", history_values,
                    history["integrity_signature"],
                ):
                    raise ValueError("assurance pilot claim history integrity conflict")
            for values in history_candidates:
                conn.execute(
                    """INSERT INTO assurance_pilot_claim_history(
                           task_id,generation,initiative_id,artifact_set_sha256,
                           fencing_token_sha256,integrity_signature,created_at
                       ) VALUES (?,?,?,?,?,?,?)""",
                    (
                        values["task_id"], values["generation"], values["initiative_id"],
                        values["artifact_set_sha256"], values["fencing_token_sha256"],
                        integrity_signature(
                            self._config.db_path, "pilot-claim-history", values,
                        ),
                        values["created_at"],
                    ),
                )

    @staticmethod
    def _snapshot_sha256(value: Any) -> str:
        canonical = json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()

    def _execution_snapshot(self, conn: Any, initiative_id: str) -> dict[str, str]:
        initiative = conn.execute(
            """SELECT profile,risk_class,status,mode,owner_principal
               FROM assurance_initiatives WHERE initiative_id=?""",
            (initiative_id,),
        ).fetchone()
        gate = conn.execute(
            """SELECT decision,artifact_set_sha256,conditions_json,expires_at,principal_id
               FROM assurance_gate_decisions
               WHERE initiative_id=? AND gate='G4' ORDER BY id DESC LIMIT 1""",
            (initiative_id,),
        ).fetchone()
        if initiative is None or gate is None:
            raise ValueError("bound pilot evaluation policy is missing")
        try:
            conditions = json.loads(gate["conditions_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("bound pilot evaluation policy is invalid") from exc
        policy = {
            "profile": initiative["profile"],
            "risk_class": initiative["risk_class"],
            "status": initiative["status"],
            "mode": initiative["mode"],
            "g4": {
                "decision": gate["decision"],
                "artifact_set_sha256": gate["artifact_set_sha256"],
                "conditions": conditions,
                "expires_at": gate["expires_at"],
            },
        }
        principal_ids = {initiative["owner_principal"], gate["principal_id"]}
        principal_ids.update(
            row["principal_id"] for row in conn.execute(
                """SELECT owner_principal AS principal_id FROM assurance_artifacts
                   WHERE initiative_id=?
                   UNION SELECT approved_by_principal FROM assurance_artifacts
                   WHERE initiative_id=? AND approved_by_principal IS NOT NULL""",
                (initiative_id, initiative_id),
            )
        )
        principal_ids.update(
            row["principal_id"] for row in conn.execute(
                """SELECT principal_id FROM assurance_principals
                   WHERE actor IN ('Trusted Evaluator','Control & Reliability Reviewer')"""
            )
        )
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trusted_eval_runs'"
        ).fetchone():
            principal_ids.update(
                row["evaluator_principal_id"] for row in conn.execute(
                    """SELECT evaluator_principal_id FROM trusted_eval_runs
                       WHERE initiative_id=? AND evaluator_principal_id IS NOT NULL""",
                    (initiative_id,),
                )
            )
        principals = [
            {
                "principal_id": row["principal_id"],
                "actor": row["actor"],
                "authority": row["authority"],
                "credential_sha256": row["credential_sha256"],
                "status": row["status"],
            }
            for row in conn.execute(
                """SELECT principal_id,actor,authority,credential_sha256,status
                   FROM assurance_principals ORDER BY principal_id"""
            )
            if row["principal_id"] in principal_ids
        ]
        return {
            "artifact_set_sha256": AssuranceKernel(
                self._config,
            )._initiative_build_artifact_set_sha256(conn, initiative_id),
            "evaluation_policy_sha256": self._snapshot_sha256(policy),
            "principal_state_sha256": self._snapshot_sha256(principals),
        }

    def record_execution_binding(
        self, conn: Any, task_id: int, generation: int, fencing_token: str,
        context_bundle_sha256: str, artifact_set_sha256: str,
        evaluation_policy_sha256: str, principal_state_sha256: str,
    ) -> None:
        task_binding = conn.execute(
            "SELECT initiative_id,pilot,artifact_set_sha256 FROM assurance_task_bindings WHERE task_id=?",
            (task_id,),
        ).fetchone()
        execution = conn.execute(
            "SELECT generation,fencing_token,recovery_status FROM task_executions WHERE task_id=?",
            (task_id,),
        ).fetchone()
        claim_fence = self._claim_fence_decision(conn, task_id, execution)
        if not claim_fence["allowed"]:
            raise ValueError(claim_fence["reason"])
        if task_binding is None or not task_binding["pilot"]:
            return
        if (
            execution is None
            or int(execution["generation"]) != generation
            or execution["fencing_token"] != fencing_token
            or execution["recovery_status"] not in {"running", "claimed"}
        ):
            raise ValueError("bound pilot context generation does not match the active execution")
        snapshot = self._execution_snapshot(conn, task_binding["initiative_id"])
        if (
            not task_binding["artifact_set_sha256"]
            or task_binding["artifact_set_sha256"] != artifact_set_sha256
            or snapshot["artifact_set_sha256"] != artifact_set_sha256
        ):
            raise ValueError("bound pilot assurance artifact set is stale")
        if snapshot["evaluation_policy_sha256"] != evaluation_policy_sha256:
            raise ValueError("bound pilot evaluation policy changed during context compilation")
        if snapshot["principal_state_sha256"] != principal_state_sha256:
            raise ValueError(
                "bound pilot principal authority or credential changed during context compilation"
            )
        created_at = utcnow()
        fencing_token_sha256 = hashlib.sha256(fencing_token.encode("utf-8")).hexdigest()
        binding_values = {
            "task_id": task_id,
            "generation": generation,
            "initiative_id": task_binding["initiative_id"],
            "artifact_set_sha256": artifact_set_sha256,
            "evaluation_policy_sha256": evaluation_policy_sha256,
            "principal_state_sha256": principal_state_sha256,
            "context_bundle_sha256": context_bundle_sha256,
            "fencing_token_sha256": fencing_token_sha256,
            "created_at": created_at,
        }
        conn.execute(
            """INSERT INTO assurance_execution_bindings(
                   task_id,generation,initiative_id,artifact_set_sha256,
                   evaluation_policy_sha256,principal_state_sha256,
                   context_bundle_sha256,fencing_token_sha256,integrity_signature,created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, generation, task_binding["initiative_id"], artifact_set_sha256,
                evaluation_policy_sha256, principal_state_sha256,
                context_bundle_sha256, fencing_token_sha256,
                integrity_signature(
                    self._config.db_path, "execution-binding", binding_values,
                ),
                created_at,
            ),
        )

    def record_claim_binding(
        self, conn: Any, task_id: int, generation: int, fencing_token: str,
    ) -> None:
        task_binding = conn.execute(
            """SELECT initiative_id,pilot,artifact_set_sha256
               FROM assurance_task_bindings WHERE task_id=?""",
            (task_id,),
        ).fetchone()
        if task_binding is None or not task_binding["pilot"]:
            return
        execution = conn.execute(
            "SELECT generation,fencing_token,recovery_status FROM task_executions "
            "WHERE task_id=?", (task_id,),
        ).fetchone()
        if (
            execution is None
            or int(execution["generation"]) != generation
            or execution["fencing_token"] != fencing_token
            or execution["recovery_status"] != "running"
        ):
            raise ValueError("bound pilot claim does not match the active execution")
        kernel = AssuranceKernel(self._config)
        artifacts = conn.execute(
            "SELECT content_json,content_sha256 FROM assurance_artifacts "
            "WHERE initiative_id=?", (task_binding["initiative_id"],),
        ).fetchall()
        try:
            for artifact in artifacts:
                kernel._artifact_content_sha256(artifact)
            current_artifact_set = kernel._initiative_build_artifact_set_sha256(
                conn, task_binding["initiative_id"],
            )
        except AssuranceError as exc:
            raise ValueError("bound pilot claim assurance integrity conflict") from exc
        if (
            not task_binding["artifact_set_sha256"]
            or task_binding["artifact_set_sha256"] != current_artifact_set
        ):
            raise ValueError("bound pilot claim artifact set integrity conflict")
        created_at = utcnow()
        values = {
            "task_id": task_id,
            "generation": generation,
            "initiative_id": task_binding["initiative_id"],
            "artifact_set_sha256": str(task_binding["artifact_set_sha256"] or ""),
            "fencing_token_sha256": hashlib.sha256(
                fencing_token.encode("utf-8")
            ).hexdigest(),
            "created_at": created_at,
        }
        conn.execute(
            """INSERT INTO assurance_claim_bindings(
                   task_id,generation,initiative_id,artifact_set_sha256,
                   fencing_token_sha256,integrity_signature,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                task_id, generation, task_binding["initiative_id"],
                values["artifact_set_sha256"], values["fencing_token_sha256"],
                integrity_signature(
                    self._config.db_path, "claim-binding", values,
                ),
                created_at,
            ),
        )
        conn.execute(
            """INSERT INTO assurance_pilot_claim_history(
                   task_id,generation,initiative_id,artifact_set_sha256,
                   fencing_token_sha256,integrity_signature,created_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                task_id, generation, task_binding["initiative_id"],
                values["artifact_set_sha256"], values["fencing_token_sha256"],
                integrity_signature(
                    self._config.db_path, "pilot-claim-history", values,
                ),
                created_at,
            ),
        )

    def claim_fence_decision(
        self, task_id: int, *, conn: Any | None = None,
    ) -> dict[str, Any]:
        if conn is not None:
            execution = conn.execute(
                "SELECT generation,fencing_token FROM task_executions WHERE task_id=?",
                (task_id,),
            ).fetchone()
            return self._claim_fence_decision(conn, task_id, execution)
        self.init()
        with self.store.connect_readonly() as readonly:
            execution = readonly.execute(
                "SELECT generation,fencing_token FROM task_executions WHERE task_id=?",
                (task_id,),
            ).fetchone()
            return self._claim_fence_decision(readonly, task_id, execution)

    def _claim_fence_decision(
        self, conn: Any, task_id: int, execution: Any | None,
    ) -> dict[str, Any]:
        task_binding = conn.execute(
            """SELECT initiative_id,pilot,artifact_set_sha256
               FROM assurance_task_bindings WHERE task_id=?""",
            (task_id,),
        ).fetchone()
        if execution is None:
            return {"allowed": True, "reason": "no active execution"}
        claim = conn.execute(
            """SELECT * FROM assurance_claim_bindings
               WHERE task_id=? AND generation=?""",
            (task_id, int(execution["generation"])),
        ).fetchone()
        if claim is None:
            prior_claim = conn.execute(
                "SELECT 1 FROM assurance_pilot_claim_history WHERE task_id=? LIMIT 1",
                (task_id,),
            ).fetchone()
            if prior_claim is not None:
                return {
                    "allowed": False,
                    "reason": "bound pilot claim execution generation changed",
                }
            if task_binding is not None and task_binding["pilot"]:
                return {
                    "allowed": False,
                    "reason": "bound pilot claim task binding anchor is missing",
                }
            return {"allowed": True, "reason": "claim was not pilot-bound"}
        values = {
            key: claim[key] for key in (
                "task_id", "generation", "initiative_id", "artifact_set_sha256",
                "fencing_token_sha256", "created_at",
            )
        }
        if not verify_integrity_signature(
            self._config.db_path, "claim-binding", values,
            claim["integrity_signature"],
        ):
            return {
                "allowed": False,
                "reason": "bound pilot claim binding integrity conflict",
            }
        current_token_sha256 = hashlib.sha256(
            str(execution["fencing_token"] or "").encode("utf-8")
        ).hexdigest()
        if claim["fencing_token_sha256"] != current_token_sha256:
            return {"allowed": False, "reason": "bound pilot claim fencing token changed"}
        if (
            task_binding is None
            or not task_binding["pilot"]
            or task_binding["initiative_id"] != claim["initiative_id"]
            or str(task_binding["artifact_set_sha256"] or "")
            != claim["artifact_set_sha256"]
        ):
            return {
                "allowed": False,
                "reason": "bound pilot claim task binding changed or is missing",
            }
        return {"allowed": True, "reason": "bound pilot claim binding is current"}

    def runtime_fence_decision(
        self, task_id: int, *, conn: Any | None = None,
    ) -> dict[str, Any]:
        if conn is not None:
            return self._runtime_fence_decision(conn, task_id)
        self.init()
        with self.store.connect_readonly() as readonly:
            return self._runtime_fence_decision(readonly, task_id)

    @staticmethod
    def execution_requires_fencing_token(conn: Any, task_id: int) -> bool:
        return conn.execute(
            """SELECT 1 FROM assurance_execution_bindings WHERE task_id=?
               UNION SELECT 1 FROM assurance_claim_bindings WHERE task_id=?
               UNION SELECT 1 FROM assurance_pilot_claim_history WHERE task_id=? LIMIT 1""",
            (task_id, task_id, task_id),
        ).fetchone() is not None

    def _runtime_fence_decision(self, conn: Any, task_id: int) -> dict[str, Any]:
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assurance_task_bindings'"
        ).fetchone() is None:
            return {"allowed": True, "reason": "unbound"}
        task_binding = conn.execute(
            "SELECT initiative_id,pilot,artifact_set_sha256 FROM assurance_task_bindings WHERE task_id=?",
            (task_id,),
        ).fetchone()
        execution = conn.execute(
            "SELECT generation,fencing_token FROM task_executions WHERE task_id=?",
            (task_id,),
        ).fetchone()
        claim_fence = self._claim_fence_decision(conn, task_id, execution)
        if not claim_fence["allowed"]:
            return claim_fence
        prior_execution_binding = conn.execute(
            "SELECT 1 FROM assurance_execution_bindings WHERE task_id=? LIMIT 1",
            (task_id,),
        ).fetchone()
        if task_binding is None:
            return (
                {"allowed": False, "reason": "bound pilot task binding is missing"}
                if prior_execution_binding is not None
                else {"allowed": True, "reason": "unbound"}
            )
        if not task_binding["pilot"]:
            return (
                {"allowed": False, "reason": "bound pilot task binding was demoted"}
                if prior_execution_binding is not None
                else {"allowed": True, "reason": "non-pilot"}
            )
        if execution is None:
            return {"allowed": False, "reason": "bound pilot execution generation is missing"}
        binding = conn.execute(
            """SELECT * FROM assurance_execution_bindings
               WHERE task_id=? AND generation=?""",
            (task_id, int(execution["generation"])),
        ).fetchone()
        if binding is None:
            prior = conn.execute(
                "SELECT 1 FROM assurance_execution_bindings WHERE task_id=? LIMIT 1",
                (task_id,),
            ).fetchone()
            reason = (
                "bound pilot execution generation changed"
                if prior is not None
                else "bound pilot execution assurance context is missing"
            )
            return {"allowed": False, "reason": reason}
        binding_values = {
            key: binding[key] for key in (
                "task_id", "generation", "initiative_id", "artifact_set_sha256",
                "evaluation_policy_sha256", "principal_state_sha256",
                "context_bundle_sha256", "fencing_token_sha256", "created_at",
            )
        }
        if not verify_integrity_signature(
            self._config.db_path, "execution-binding", binding_values,
            binding["integrity_signature"],
        ):
            return {"allowed": False, "reason": "bound pilot execution binding integrity conflict"}
        current_token_sha256 = hashlib.sha256(
            str(execution["fencing_token"] or "").encode("utf-8")
        ).hexdigest()
        if binding["fencing_token_sha256"] != current_token_sha256:
            return {"allowed": False, "reason": "bound pilot fencing token changed"}
        context = conn.execute(
            """SELECT bundle_sha256,fencing_token,status FROM task_contexts
               WHERE task_id=? AND generation=?""",
            (task_id, int(execution["generation"])),
        ).fetchone()
        if (
            context is None
            or context["status"] != "active"
            or context["bundle_sha256"] != binding["context_bundle_sha256"]
            or context["fencing_token"] != execution["fencing_token"]
        ):
            return {"allowed": False, "reason": "bound pilot execution assurance context changed"}
        if (
            binding["initiative_id"] != task_binding["initiative_id"]
            or binding["artifact_set_sha256"] != task_binding["artifact_set_sha256"]
        ):
            return {"allowed": False, "reason": "bound pilot artifact set binding changed"}
        try:
            current = self._execution_snapshot(conn, task_binding["initiative_id"])
        except ValueError as exc:
            return {"allowed": False, "reason": str(exc)}
        if current["artifact_set_sha256"] != binding["artifact_set_sha256"]:
            return {"allowed": False, "reason": "bound pilot artifact set changed or became stale"}
        initiative = conn.execute(
            "SELECT status,mode FROM assurance_initiatives WHERE initiative_id=?",
            (task_binding["initiative_id"],),
        ).fetchone()
        if (
            initiative is None
            or initiative["status"] not in {"approved_for_build", "implementation", "independent_evaluation"}
            or initiative["mode"] != "pilot"
        ):
            return {"allowed": False, "reason": "bound pilot lifecycle is no longer executable"}
        if current["evaluation_policy_sha256"] != binding["evaluation_policy_sha256"]:
            return {"allowed": False, "reason": "bound pilot evaluation policy changed"}
        gate = conn.execute(
            """SELECT expires_at FROM assurance_gate_decisions
               WHERE initiative_id=? AND gate='G4' ORDER BY id DESC LIMIT 1""",
            (task_binding["initiative_id"],),
        ).fetchone()
        if gate is None:
            return {"allowed": False, "reason": "bound pilot evaluation policy is missing"}
        if gate["expires_at"]:
            try:
                expiry = datetime.fromisoformat(gate["expires_at"])
            except ValueError:
                return {"allowed": False, "reason": "bound pilot G4 expiry is invalid"}
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                return {"allowed": False, "reason": "bound pilot G4 decision expired"}
        if current["principal_state_sha256"] != binding["principal_state_sha256"]:
            return {
                "allowed": False,
                "reason": "bound pilot principal authority or credential changed",
            }
        return {"allowed": True, "reason": "bound pilot execution assurance binding is current"}

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
                kernel = AssuranceKernel(self._config)
                try:
                    for artifact in conn.execute(
                        "SELECT content_json,content_sha256 FROM assurance_artifacts "
                        "WHERE initiative_id=?", (binding["initiative_id"],),
                    ):
                        kernel._artifact_content_sha256(artifact)
                except AssuranceError:
                    return {
                        "allowed": False,
                        "reason": "kill-switch dispatch assurance integrity conflict",
                    }
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
                """SELECT artifact_id,version,status,content_json,content_sha256,
                          approved_by_principal,approved_at
                   FROM assurance_artifacts
                   WHERE initiative_id=? AND kind!='review_decision'""",
                (binding["initiative_id"],),
            ):
                try:
                    kernel._artifact_content_sha256(artifact)
                    body_valid = True
                except AssuranceError:
                    body_valid = False
                registration = conn.execute(
                    """SELECT * FROM assurance_artifact_registrations
                       WHERE artifact_id=? AND version=?""",
                    (artifact["artifact_id"], artifact["version"]),
                ).fetchone()
                registration_valid = registration is not None and verify_integrity_signature(
                    self._config.db_path, "artifact-registration", {
                        "artifact_id": registration["artifact_id"],
                        "version": registration["version"],
                        "content_sha256": registration["content_sha256"],
                        "created_at": registration["created_at"],
                    }, registration["integrity_signature"],
                ) and registration["content_sha256"] == artifact["content_sha256"]
                approval_valid = True
                if artifact["status"] == "approved":
                    approval = conn.execute(
                        """SELECT * FROM assurance_artifact_approvals
                           WHERE artifact_id=? AND version=?""",
                        (artifact["artifact_id"], artifact["version"]),
                    ).fetchone()
                    approval_valid = approval is not None and verify_integrity_signature(
                        self._config.db_path, "artifact-approval", {
                            "artifact_id": approval["artifact_id"],
                            "version": approval["version"],
                            "content_sha256": approval["content_sha256"],
                            "approved_by_principal": approval["approved_by_principal"],
                            "approved_at": approval["approved_at"],
                        }, approval["integrity_signature"],
                    ) and (
                        approval["content_sha256"] == artifact["content_sha256"]
                        and approval["approved_by_principal"] == artifact["approved_by_principal"]
                        and approval["approved_at"] == artifact["approved_at"]
                    )
                if (
                    not body_valid
                    or not registration_valid
                    or not approval_valid
                ):
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
            try:
                kernel._artifact_content_sha256(artifact)
                payload = json.loads(artifact["content_json"])
                metadata_matches = (
                    payload["artifact_id"] == artifact["artifact_id"]
                    and payload["version"] == artifact["version"]
                    and payload["kind"] == artifact["kind"]
                    and payload["status"] == "draft"
                    and payload["owner_principal"] == artifact["owner_principal"]
                    and payload["initiative_id"] == initiative_id
                )
            except (AssuranceError, KeyError, TypeError, json.JSONDecodeError):
                metadata_matches = False
            registration = conn.execute(
                """SELECT content_sha256,created_at,integrity_signature
                   FROM assurance_artifact_registrations
                   WHERE artifact_id=? AND version=?""",
                (artifact["artifact_id"], artifact["version"]),
            ).fetchone()
            registration_matches = (
                registration is not None
                and registration["content_sha256"] == artifact["content_sha256"]
                and verify_integrity_signature(
                    self._config.db_path, "artifact-registration", {
                        "artifact_id": artifact["artifact_id"],
                        "version": artifact["version"],
                        "content_sha256": registration["content_sha256"],
                        "created_at": registration["created_at"],
                    }, registration["integrity_signature"],
                )
            )
            if (
                not metadata_matches
                or not registration_matches
                or not kernel._artifact_lifecycle_valid(conn, artifact)
            ):
                return {
                    "allowed": False,
                    "reason": "bound pilot assurance lifecycle or integrity conflict",
                }

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
            approval_anchor = conn.execute(
                """SELECT * FROM assurance_artifact_approvals
                   WHERE artifact_id=? AND version=?""",
                (review["artifact_id"], review["version"]),
            ).fetchone()
            approval_anchor_valid = approval_anchor is not None and verify_integrity_signature(
                self._config.db_path, "artifact-approval", {
                    "artifact_id": approval_anchor["artifact_id"],
                    "version": approval_anchor["version"],
                    "content_sha256": approval_anchor["content_sha256"],
                    "approved_by_principal": approval_anchor["approved_by_principal"],
                    "approved_at": approval_anchor["approved_at"],
                }, approval_anchor["integrity_signature"],
            )
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
                or not approval_anchor_valid
                or approval_anchor["content_sha256"] != review["content_sha256"]
                or approval_anchor["approved_by_principal"] != review["approved_by_principal"]
                or approval_anchor["approved_at"] != review["approved_at"]
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

    def record_completion(
        self, conn: Any, task_id: int, assurance: dict[str, str], completed_at: str,
        *, task_result_json: str | None = None,
        evidence_paths_json: str | None = None,
    ) -> None:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        execution = conn.execute(
            "SELECT generation,recovery_status FROM task_executions WHERE task_id=?",
            (task_id,),
        ).fetchone()
        if (
            task is None
            or execution is None
            or task_result_json is None
            or evidence_paths_json is None
        ):
            raise ValueError(f"task {task_id} completion assurance context is incomplete")
        if conn.execute(
            "SELECT 1 FROM assurance_completion_bindings WHERE task_id=?", (task_id,),
        ).fetchone():
            raise ValueError(f"task {task_id} assurance completion binding changed concurrently")
        decision = self._completion_decision(conn, dict(task))
        expected = decision.get("assurance") if decision.get("allowed") else None
        if expected is None or assurance != expected:
            raise ValueError(f"task {task_id} completion assurance is not current and exact")
        try:
            result_payload = json.loads(task_result_json)
            embedded_assurance = result_payload["assurance"]
            evidence = result_payload["evidence"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"task {task_id} completion result body is invalid") from exc
        if embedded_assurance != assurance or evidence != json.loads(evidence_paths_json):
            raise ValueError(f"task {task_id} completion result body is not exact")
        review_id, separator, version_text = assurance["review_decision_ref"].rpartition(":v")
        if not separator or not version_text.isdigit():
            raise ValueError(f"task {task_id} completion assurance review is invalid")
        review = conn.execute(
            "SELECT content_sha256 FROM assurance_artifacts "
            "WHERE artifact_id=? AND version=? AND initiative_id=? "
            "AND kind='review_decision' AND status='approved'",
            (review_id, int(version_text), assurance["initiative_id"]),
        ).fetchone()
        if review is None:
            raise ValueError(f"task {task_id} completion assurance review is missing")
        created_at = utcnow()
        values = {
            "task_id": task_id,
            "generation": int(execution["generation"]),
            "initiative_id": assurance["initiative_id"],
            "artifact_set_sha256": assurance["artifact_set_sha256"],
            "trusted_eval_result_sha256": assurance["result_sha256"],
            "review_decision_ref": assurance["review_decision_ref"],
            "review_content_sha256": review["content_sha256"],
            "task_result_sha256": hashlib.sha256(
                task_result_json.encode("utf-8")
            ).hexdigest(),
            "evidence_paths_sha256": hashlib.sha256(
                evidence_paths_json.encode("utf-8")
            ).hexdigest(),
            "completed_at": completed_at,
            "created_at": created_at,
        }
        conn.execute(
            """INSERT INTO assurance_completion_bindings(
                   task_id,generation,initiative_id,artifact_set_sha256,
                   trusted_eval_result_sha256,review_decision_ref,
                   review_content_sha256,task_result_sha256,evidence_paths_sha256,
                   completed_at,created_at,integrity_signature
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*values.values(), integrity_signature(
                self._config.db_path, "completion-binding", values,
            )),
        )
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

    def completion_binding_valid(self, conn: Any, completion: Any) -> bool:
        if completion is None:
            return False
        keys = (
            "task_id", "generation", "initiative_id", "artifact_set_sha256",
            "trusted_eval_result_sha256", "review_decision_ref",
            "review_content_sha256", "task_result_sha256",
            "evidence_paths_sha256", "completed_at", "created_at",
        )
        values = {key: completion[key] for key in keys}
        if not verify_integrity_signature(
            self._config.db_path, "completion-binding", values,
            completion["integrity_signature"],
        ):
            return False
        task = conn.execute(
            "SELECT * FROM tasks WHERE id=?", (completion["task_id"],),
        ).fetchone()
        execution = conn.execute(
            "SELECT generation,recovery_status,evidence_paths,updated_at "
            "FROM task_executions WHERE task_id=?", (completion["task_id"],),
        ).fetchone()
        binding = conn.execute(
            "SELECT * FROM assurance_task_bindings WHERE task_id=?",
            (completion["task_id"],),
        ).fetchone()
        if task is None or execution is None or binding is None:
            return False
        try:
            task_result = json.loads(task["result"])
            task_assurance = task_result["assurance"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return False
        expected = self._completion_decision(conn, dict(task)).get("assurance")
        review_id, separator, version_text = completion["review_decision_ref"].rpartition(":v")
        review = None
        if separator and version_text.isdigit():
            review = conn.execute(
                "SELECT content_sha256 FROM assurance_artifacts "
                "WHERE artifact_id=? AND version=? AND initiative_id=? "
                "AND kind='review_decision' AND status='approved'",
                (review_id, int(version_text), completion["initiative_id"]),
            ).fetchone()
        assurance = {
            "initiative_id": completion["initiative_id"],
            "artifact_set_sha256": completion["artifact_set_sha256"],
            "result_sha256": completion["trusted_eval_result_sha256"],
            "review_decision_ref": completion["review_decision_ref"],
        }
        return bool(
            task["status"] == "done"
            and execution["recovery_status"] == "completed"
            and int(execution["generation"]) == int(completion["generation"])
            and binding["pilot"] == 1
            and binding["initiative_id"] == completion["initiative_id"]
            and binding["artifact_set_sha256"] == completion["artifact_set_sha256"]
            and binding["completion_result_sha256"]
                == completion["trusted_eval_result_sha256"]
            and binding["review_decision_ref"] == completion["review_decision_ref"]
            and binding["completed_at"] == completion["completed_at"]
            and task["updated_at"] == completion["completed_at"]
            and execution["updated_at"] == completion["completed_at"]
            and hashlib.sha256(task["result"].encode("utf-8")).hexdigest()
                == completion["task_result_sha256"]
            and hashlib.sha256(execution["evidence_paths"].encode("utf-8")).hexdigest()
                == completion["evidence_paths_sha256"]
            and task_assurance == assurance
            and expected == assurance
            and review is not None
            and review["content_sha256"] == completion["review_content_sha256"]
        )
