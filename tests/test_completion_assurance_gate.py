from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_company.assurance import AssuranceKernel
from agent_company.config import load_config
from agent_company.context_compiler import ContextCompiler
from agent_company.integrity import signature as integrity_signature
from agent_company.integrity import verify as verify_integrity_signature
from agent_company.ops import CompanyOS
from agent_company.pilot_gate import PilotGate
from agent_company.trusted_evaluator import TrustedEvaluator


class CompletionAssuranceGateTest(unittest.TestCase):
    initiative_id = "pilot-c2-approved-for-build"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[paths]\ndatabase=data/company.sqlite3\nartifacts=data/artifacts\nlogs=logs\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        self.osys = CompanyOS(self.config)
        self.osys.init()
        self.kernel = AssuranceKernel(self.config)
        self.kernel.init()
        self.gate = PilotGate(self.config)
        self.gate.init()
        self.credentials = {
            "principal-ceo": ("CEO", "executive"),
            "principal-platform": ("Company Platform Engineer", "implementer"),
            "principal-reviewer": ("Control & Reliability Reviewer", "reviewer"),
            "principal-evaluator": ("Trusted Evaluator", "operator"),
        }
        with self.osys.store.connect() as conn:
            conn.execute("DELETE FROM tasks")
            for principal_id, (actor, authority) in self.credentials.items():
                credential = f"credential-{principal_id}"
                os.environ[
                    f"ASSURANCE_CREDENTIAL_{principal_id.upper().replace('-', '_')}"
                ] = credential
                conn.execute(
                    """INSERT INTO assurance_principals(
                           principal_id,actor,authority,credential_sha256,status,created_at
                       ) VALUES (?,?,?,?, 'active','2026-07-26T00:00:00+00:00')""",
                    (principal_id, actor, authority, hashlib.sha256(credential.encode()).hexdigest()),
                )
        self.task_id = int(self.osys.create_task(
            "CEO", "Company Platform Engineer", "Complete guarded pilot", "platform", 99,
            "A bound Trusted Eval and independent Review Decision must pass.",
        )["task_id"])
        self.task_evidence = self.root / "evidence" / "task-result.md"
        self.task_evidence.parent.mkdir(parents=True)
        self.task_evidence.write_text("reviewable task result\n", encoding="utf-8")
        self.artifact_set_sha256 = self._prepare_build_gate()
        self.claim = self.osys.claim_task(
            self.task_id, "Company Platform Engineer", executor_id="platform-runner",
            backend="local",
        )
        self.context_bundle = ContextCompiler(
            self.config, context_root=self.old_cwd / "company_context",
        ).compile(
            self.task_id,
            generation=int(self.claim["generation"]),
            role="Company Platform Engineer",
            repository={"id": "agent-company"},
            fencing_token=str(self.claim["fencing_token"]),
        )

    def tearDown(self) -> None:
        for principal_id in self.credentials:
            os.environ.pop(
                f"ASSURANCE_CREDENTIAL_{principal_id.upper().replace('-', '_')}", None
            )
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _prepare_build_gate(self) -> str:
        self.kernel.create_initiative(
            self.initiative_id, "Completion assurance pilot",
            "control-plane-reliability", "C2", actor="CEO", principal_id="principal-ceo",
        )
        contract = {
            "schema_version": "assurance-artifact/v1",
            "artifact_id": "completion-eval-contract",
            "kind": "eval_contract",
            "version": 1,
            "status": "draft",
            "initiative_id": self.initiative_id,
            "profile": "control-plane-reliability",
            "risk_class": "C2",
            "owner_principal": "principal-platform",
            "repository_id": "agent-company",
            "content": {
                "hard_gates": ["completion is fenced"],
                "graders": ["focused unit tests"],
                "release_rule": "all hard gates pass",
            },
        }
        self.kernel.register_artifact(
            contract, actor="Company Platform Engineer", principal_id="principal-platform",
        )
        self.kernel.approve_artifact(
            "completion-eval-contract", 1, actor="CEO", principal_id="principal-ceo",
        )
        with self.osys.store.connect() as conn:
            digest = self.kernel._initiative_artifact_set_sha256(conn, self.initiative_id)
            conn.execute(
                "UPDATE assurance_initiatives SET status='approved_for_build',mode='pilot' "
                "WHERE initiative_id=?",
                (self.initiative_id,),
            )
            conn.execute(
                """INSERT INTO assurance_gate_decisions(
                       initiative_id,gate,decision,actor,principal_id,artifact_set_sha256,
                       conditions_json,expires_at,created_at
                   ) VALUES (?,'G4','pass','CEO','principal-ceo',?,'[]',NULL,
                             '2026-07-26T00:00:00+00:00')""",
                (self.initiative_id, digest),
            )
        self.gate.bind(
            self.task_id, self.initiative_id, pilot=True, artifact_set_sha256=digest,
            actor="CEO", principal_id="principal-ceo",
        )
        return digest

    def _record_eval(self) -> str:
        evaluator = TrustedEvaluator(self.config)
        content_dir = self.root / "data" / "trusted-eval-content"
        content_dir.mkdir(parents=True, exist_ok=True)
        refs: dict[str, str] = {}
        for kind in ("candidate", "dataset", "grader", "environment"):
            content = f"completion-{kind}".encode()
            content_sha256 = hashlib.sha256(content).hexdigest()
            (content_dir / content_sha256).write_bytes(content)
            refs[kind] = evaluator.register_manifest(
                kind,
                {
                    "schema_version": f"trusted-eval-{kind}/v1",
                    "id": f"completion-{kind}",
                    "content_sha256": content_sha256,
                    "protected": kind == "dataset",
                },
                actor="Trusted Evaluator", principal_id="principal-evaluator",
            )["manifest_sha256"]
        eval_evidence = self.root / "evidence" / "trusted-eval.json"
        eval_evidence.write_text('{"hard_gates":"pass"}\n', encoding="utf-8")
        return str(evaluator.record_run(
            initiative_id=self.initiative_id, refs=refs, seed=147, status="completed",
            evidence_ref="evidence/trusted-eval.json", max_attempts=3,
            actor="Trusted Evaluator", principal_id="principal-evaluator",
        )["result_sha256"])

    def _record_review(
        self, result_sha256: str, *, artifact_set_sha256: str | None = None,
        decision: str = "approve", artifact_id: str = "completion-review",
        owner_principal: str = "principal-reviewer",
        findings: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> None:
        actor = self.credentials[owner_principal][0]
        review = {
            "schema_version": "assurance-artifact/v1",
            "artifact_id": artifact_id,
            "kind": "review_decision",
            "version": 1,
            "status": "draft",
            "initiative_id": self.initiative_id,
            "profile": "control-plane-reliability",
            "risk_class": "C2",
            "owner_principal": owner_principal,
            "repository_id": "agent-company",
            "content": {
                "decision": decision,
                "findings": findings or [],
                "evidence_refs": evidence_refs or [
                    result_sha256,
                    artifact_set_sha256 or self.artifact_set_sha256,
                ],
            },
        }
        self.kernel.register_artifact(review, actor=actor, principal_id=owner_principal)
        self.kernel.approve_artifact(
            artifact_id, 1, actor="CEO", principal_id="principal-ceo",
        )

    def _completion_sql_material(
        self, result_sha256: str, *,
        review_ref: str = "completion-review:v1",
        task_evidence: list[str] | None = None,
        execution_evidence: list[str] | None = None,
        completed_at: str = "2026-07-28T12:00:00+00:00",
    ) -> tuple[dict[str, object], str, str]:
        task_evidence = task_evidence or [str(self.task_evidence)]
        execution_evidence = execution_evidence or list(task_evidence)
        assurance = {
            "initiative_id": self.initiative_id,
            "artifact_set_sha256": self.artifact_set_sha256,
            "result_sha256": result_sha256,
            "review_decision_ref": review_ref,
        }
        task_result_json = json.dumps({
            "summary": "signed direct SQL completion",
            "evidence": task_evidence,
            "assurance": assurance,
        }, sort_keys=True)
        evidence_paths_json = json.dumps(execution_evidence, sort_keys=True)
        review_id, _, version_text = review_ref.rpartition(":v")
        review = self.osys.store.fetch_one(
            "SELECT content_sha256 FROM assurance_artifacts "
            "WHERE artifact_id=? AND version=?",
            (review_id, int(version_text)),
        )
        values: dict[str, object] = {
            "task_id": self.task_id,
            "generation": int(self.claim["generation"]),
            "initiative_id": self.initiative_id,
            "artifact_set_sha256": self.artifact_set_sha256,
            "trusted_eval_result_sha256": result_sha256,
            "review_decision_ref": review_ref,
            "review_content_sha256": review["content_sha256"],
            "task_result_sha256": hashlib.sha256(
                task_result_json.encode("utf-8")
            ).hexdigest(),
            "evidence_paths_sha256": hashlib.sha256(
                evidence_paths_json.encode("utf-8")
            ).hexdigest(),
            "completed_at": completed_at,
            "created_at": completed_at,
        }
        return values, task_result_json, evidence_paths_json

    def _completion_insert_statement(
        self, values: dict[str, object], task_result_json: str,
        evidence_paths_json: str,
    ) -> tuple[str, tuple[object, ...]]:
        available = {
            row["name"] for row in self.osys.store.fetch_all(
                "PRAGMA table_info(assurance_completion_bindings)"
            )
        }
        insert_values = dict(values)
        if "task_result_json" in available:
            insert_values["task_result_json"] = task_result_json
        if "evidence_paths_json" in available:
            insert_values["evidence_paths_json"] = evidence_paths_json
        insert_values["integrity_signature"] = integrity_signature(
            self.config.db_path, "completion-binding", values,
        )
        columns = tuple(insert_values)
        statement = (
            f"INSERT INTO assurance_completion_bindings({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})"
        )
        return statement, tuple(insert_values[column] for column in columns)

    def _completion_state(self) -> dict[str, object]:
        with self.osys.store.connect_readonly() as conn:
            return {
                "task": dict(conn.execute(
                    "SELECT status,result,updated_at FROM tasks WHERE id=?",
                    (self.task_id,),
                ).fetchone()),
                "execution": dict(conn.execute(
                    "SELECT recovery_status,evidence_paths,updated_at "
                    "FROM task_executions WHERE task_id=?", (self.task_id,),
                ).fetchone()),
                "binding": dict(conn.execute(
                    "SELECT completion_result_sha256,review_decision_ref,completed_at,"
                    "updated_at FROM assurance_task_bindings WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()),
                "completions": conn.execute(
                    "SELECT COUNT(*) FROM assurance_completion_bindings WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()[0],
                "audits": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0],
            }

    def _refresh_execution_principal_snapshot(self) -> None:
        with self.osys.store.connect() as conn:
            binding = conn.execute(
                "SELECT * FROM assurance_execution_bindings WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
            snapshot = self.gate._execution_snapshot(conn, self.initiative_id)
            values = {
                "task_id": binding["task_id"],
                "generation": binding["generation"],
                "initiative_id": binding["initiative_id"],
                "artifact_set_sha256": binding["artifact_set_sha256"],
                "evaluation_policy_sha256": binding["evaluation_policy_sha256"],
                "principal_state_sha256": snapshot["principal_state_sha256"],
                "context_bundle_sha256": binding["context_bundle_sha256"],
                "fencing_token_sha256": binding["fencing_token_sha256"],
                "created_at": binding["created_at"],
            }
            conn.execute("DROP TRIGGER assurance_execution_bindings_immutable_update")
            conn.execute(
                "UPDATE assurance_execution_bindings SET principal_state_sha256=?,"
                "integrity_signature=? WHERE task_id=? AND generation=?",
                (
                    values["principal_state_sha256"],
                    integrity_signature(
                        self.config.db_path, "execution-binding", values,
                    ),
                    self.task_id, binding["generation"],
                ),
            )

    def _assert_signed_sql_completion_rejected(
        self, result_sha256: str, *,
        review_ref: str = "completion-review:v1",
        task_evidence: list[str] | None = None,
        execution_evidence: list[str] | None = None,
    ) -> None:
        values, task_result_json, evidence_paths_json = self._completion_sql_material(
            result_sha256, review_ref=review_ref, task_evidence=task_evidence,
            execution_evidence=execution_evidence,
        )
        statement, parameters = self._completion_insert_statement(
            values, task_result_json, evidence_paths_json,
        )
        before = self._completion_state()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
            with self.osys.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(statement, parameters)
                conn.execute(
                    "UPDATE assurance_task_bindings SET completion_result_sha256=?,"
                    "review_decision_ref=?,completed_at=?,updated_at=? WHERE task_id=?",
                    (
                        result_sha256, review_ref, values["completed_at"],
                        values["completed_at"], self.task_id,
                    ),
                )
                conn.execute(
                    "UPDATE task_executions SET recovery_status='completed',"
                    "evidence_paths=?,updated_at=? WHERE task_id=?",
                    (evidence_paths_json, values["completed_at"], self.task_id),
                )
                conn.execute(
                    "UPDATE tasks SET status='done',result=?,updated_at=? WHERE id=?",
                    (task_result_json, values["completed_at"], self.task_id),
                )
        self.assertEqual(self._completion_state(), before)

    def _assert_denial_is_atomic(self, expected: str) -> None:
        with self.osys.store.connect_readonly() as conn:
            audit_before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            events_before = conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0]
        with self.assertRaisesRegex(ValueError, expected):
            self.osys.complete_task(
                self.task_id, "Company Platform Engineer", "guarded result",
                [self.task_evidence], fencing_token=str(self.claim["fencing_token"]),
            )
        with self.osys.store.connect_readonly() as conn:
            task = conn.execute(
                "SELECT status,result FROM tasks WHERE id=?", (self.task_id,)
            ).fetchone()
            execution = conn.execute(
                "SELECT recovery_status,evidence_paths FROM task_executions WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], audit_before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0], events_before)
        self.assertEqual((task["status"], task["result"]), ("in_progress", None))
        self.assertEqual((execution["recovery_status"], execution["evidence_paths"]), ("running", "[]"))

    def _assert_runtime_fence_is_atomic(self, expected: str) -> None:
        with self.assertRaisesRegex(ValueError, expected):
            ContextCompiler(
                self.config, context_root=self.old_cwd / "company_context",
            ).assert_current(
                self.task_id,
                int(self.claim["generation"]),
                self.context_bundle["provenance"]["bundle_sha256"],
                fencing_token=str(self.claim["fencing_token"]),
            )
        with self.osys.store.connect_readonly() as conn:
            execution_before = dict(conn.execute(
                "SELECT heartbeat_at,lease_expires_at,updated_at FROM task_executions WHERE task_id=?",
                (self.task_id,),
            ).fetchone())
            executor_row = conn.execute(
                "SELECT heartbeat_at,updated_at FROM executors WHERE executor_id='platform-runner'"
            ).fetchone()
            executor_before = dict(executor_row) if executor_row is not None else None
            audit_before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            events_before = conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0]
        with self.assertRaisesRegex(ValueError, expected):
            self.osys.heartbeat_task(
                self.task_id, "platform-runner",
                fencing_token=str(self.claim["fencing_token"]),
            )
        with self.osys.store.connect_readonly() as conn:
            execution_after = dict(conn.execute(
                "SELECT heartbeat_at,lease_expires_at,updated_at FROM task_executions WHERE task_id=?",
                (self.task_id,),
            ).fetchone())
            executor_row = conn.execute(
                "SELECT heartbeat_at,updated_at FROM executors WHERE executor_id='platform-runner'"
            ).fetchone()
            executor_after = dict(executor_row) if executor_row is not None else None
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], audit_before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0], events_before)
        self.assertEqual(execution_after, execution_before)
        self.assertEqual(executor_after, executor_before)
        self._assert_denial_is_atomic(expected)

    def _tamper_build_artifact_body_preserving_declared_hashes(self) -> None:
        with self.osys.store.connect() as conn:
            artifact = conn.execute(
                "SELECT content_json,content_sha256 FROM assurance_artifacts "
                "WHERE artifact_id='completion-eval-contract' AND version=1"
            ).fetchone()
            declared_before = {
                "content_sha256": artifact["content_sha256"],
                "gate": conn.execute(
                    "SELECT artifact_set_sha256 FROM assurance_gate_decisions "
                    "WHERE initiative_id=? AND gate='G4' ORDER BY id DESC LIMIT 1",
                    (self.initiative_id,),
                ).fetchone()[0],
                "task": conn.execute(
                    "SELECT artifact_set_sha256 FROM assurance_task_bindings WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()[0],
                "claim": conn.execute(
                    "SELECT artifact_set_sha256 FROM assurance_claim_bindings WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()[0],
                "execution": conn.execute(
                    "SELECT artifact_set_sha256 FROM assurance_execution_bindings WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()[0],
            }
            payload = json.loads(artifact["content_json"])
            payload["content"]["hard_gates"].append("tampered body bypasses declared hash")
            tampered = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            )
            self.assertNotEqual(
                hashlib.sha256(tampered.encode("ascii")).hexdigest(),
                artifact["content_sha256"],
            )
            conn.execute(
                "UPDATE assurance_artifacts SET content_json=? "
                "WHERE artifact_id='completion-eval-contract' AND version=1",
                (tampered,),
            )
            declared_after = {
                "content_sha256": conn.execute(
                    "SELECT content_sha256 FROM assurance_artifacts "
                    "WHERE artifact_id='completion-eval-contract' AND version=1"
                ).fetchone()[0],
                "gate": conn.execute(
                    "SELECT artifact_set_sha256 FROM assurance_gate_decisions "
                    "WHERE initiative_id=? AND gate='G4' ORDER BY id DESC LIMIT 1",
                    (self.initiative_id,),
                ).fetchone()[0],
                "task": conn.execute(
                    "SELECT artifact_set_sha256 FROM assurance_task_bindings WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()[0],
                "claim": conn.execute(
                    "SELECT artifact_set_sha256 FROM assurance_claim_bindings WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()[0],
                "execution": conn.execute(
                    "SELECT artifact_set_sha256 FROM assurance_execution_bindings WHERE task_id=?",
                    (self.task_id,),
                ).fetchone()[0],
            }
        self.assertEqual(declared_after, declared_before)

    def test_unchanged_declared_hash_cannot_hide_body_tamper_from_context_compilation(self) -> None:
        self.osys.cancel_task(
            self.task_id, "Company Platform Engineer", "isolate pre-context tamper probe",
        )
        next_task_id = int(self.osys.create_task(
            "CEO", "Company Platform Engineer", "Compile a second guarded pilot context",
            "platform", 98, "The bound artifact body must retain its registered digest.",
        )["task_id"])
        self.gate.bind(
            next_task_id, self.initiative_id, pilot=True,
            artifact_set_sha256=self.artifact_set_sha256,
            actor="CEO", principal_id="principal-ceo",
        )
        next_claim = self.osys.claim_task(
            next_task_id, "Company Platform Engineer", executor_id="platform-runner-2",
            backend="local",
        )
        self._tamper_build_artifact_body_preserving_declared_hashes()

        with self.assertRaisesRegex(ValueError, "artifact content body hash"):
            ContextCompiler(
                self.config, context_root=self.old_cwd / "company_context",
            ).compile(
                next_task_id,
                generation=int(next_claim["generation"]),
                role="Company Platform Engineer",
                repository={"id": "agent-company"},
                fencing_token=str(next_claim["fencing_token"]),
            )
        with self.osys.store.connect_readonly() as conn:
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM task_contexts WHERE task_id=?", (next_task_id,),
            ).fetchone())
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM assurance_execution_bindings WHERE task_id=?",
                (next_task_id,),
            ).fetchone())

    def test_unchanged_declared_hash_cannot_hide_body_tamper_from_heartbeat(self) -> None:
        self._tamper_build_artifact_body_preserving_declared_hashes()
        with self.osys.store.connect_readonly() as conn:
            execution_before = dict(conn.execute(
                "SELECT heartbeat_at,lease_expires_at,updated_at FROM task_executions WHERE task_id=?",
                (self.task_id,),
            ).fetchone())
            executor_row = conn.execute(
                "SELECT heartbeat_at,updated_at FROM executors WHERE executor_id='platform-runner'"
            ).fetchone()
            executor_before = dict(executor_row) if executor_row is not None else None
            audit_before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
            events_before = conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0]

        with self.assertRaisesRegex(ValueError, "artifact content body hash"):
            self.osys.heartbeat_task(
                self.task_id, "platform-runner",
                fencing_token=str(self.claim["fencing_token"]),
            )

        with self.osys.store.connect_readonly() as conn:
            execution_after = dict(conn.execute(
                "SELECT heartbeat_at,lease_expires_at,updated_at FROM task_executions WHERE task_id=?",
                (self.task_id,),
            ).fetchone())
            executor_row = conn.execute(
                "SELECT heartbeat_at,updated_at FROM executors WHERE executor_id='platform-runner'"
            ).fetchone()
            executor_after = dict(executor_row) if executor_row is not None else None
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], audit_before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0], events_before)
        self.assertEqual(execution_after, execution_before)
        self.assertEqual(executor_after, executor_before)

    def test_completion_remains_protected_from_unchanged_declared_hash_body_tamper(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self._tamper_build_artifact_body_preserving_declared_hashes()
        self._assert_denial_is_atomic("artifact content body hash")

    def test_context_persists_immutable_execution_assurance_binding(self) -> None:
        serialized = json.dumps(self.context_bundle["assurance"], sort_keys=True)
        self.assertNotIn("principal-", serialized)
        self.assertNotIn("principal_state_sha256", serialized)
        self.assertNotIn("evaluation_policy_sha256", serialized)
        for principal_id in self.credentials:
            self.assertNotIn(
                hashlib.sha256(f"credential-{principal_id}".encode()).hexdigest(),
                serialized,
            )
        with self.osys.store.connect_readonly() as conn:
            binding = conn.execute(
                "SELECT * FROM assurance_execution_bindings WHERE task_id=? AND generation=?",
                (self.task_id, int(self.claim["generation"])),
            ).fetchone()
        self.assertIsNotNone(binding)
        self.assertEqual(binding["initiative_id"], self.initiative_id)
        self.assertEqual(binding["artifact_set_sha256"], self.artifact_set_sha256)
        self.assertEqual(
            binding["context_bundle_sha256"],
            self.context_bundle["provenance"]["bundle_sha256"],
        )
        for column in ("evaluation_policy_sha256", "principal_state_sha256"):
            self.assertEqual(len(binding[column]), 64)

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE assurance_execution_bindings SET artifact_set_sha256=? WHERE task_id=?",
                    ("0" * 64, self.task_id),
                )

    def test_claim_persists_immutable_pilot_binding_before_context(self) -> None:
        with self.osys.store.connect_readonly() as conn:
            binding = conn.execute(
                "SELECT * FROM assurance_claim_bindings WHERE task_id=? AND generation=?",
                (self.task_id, int(self.claim["generation"])),
            ).fetchone()
            history = conn.execute(
                """SELECT * FROM assurance_pilot_claim_history
                   WHERE task_id=? AND generation=?""",
                (self.task_id, int(self.claim["generation"])),
            ).fetchone()
        self.assertIsNotNone(binding)
        self.assertIsNotNone(history)
        self.assertEqual(binding["initiative_id"], self.initiative_id)
        self.assertEqual(binding["artifact_set_sha256"], self.artifact_set_sha256)
        self.assertEqual(len(binding["fencing_token_sha256"]), 64)
        self.assertEqual(len(binding["integrity_signature"]), 64)
        history_values = {
            key: history[key] for key in (
                "task_id", "generation", "initiative_id", "artifact_set_sha256",
                "fencing_token_sha256", "created_at",
            )
        }
        self.assertTrue(verify_integrity_signature(
            self.config.db_path, "pilot-claim-history", history_values,
            history["integrity_signature"],
        ))

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE assurance_claim_bindings SET initiative_id='forged' "
                    "WHERE task_id=? AND generation=?",
                    (self.task_id, int(self.claim["generation"])),
                )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "DELETE FROM assurance_pilot_claim_history "
                    "WHERE task_id=? AND generation=?",
                    (self.task_id, int(self.claim["generation"])),
                )

    def test_stale_artifact_fences_heartbeat_and_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self.kernel.supersede_artifact(
            "completion-eval-contract", 1, actor="CEO", principal_id="principal-ceo",
            reason="build contract replaced",
        )
        self._assert_runtime_fence_is_atomic("artifact set")

    def test_changed_evaluation_threshold_fences_heartbeat_and_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_gate_decisions SET conditions_json=? "
                "WHERE initiative_id=? AND gate='G4'",
                ('["raised completion threshold"]', self.initiative_id),
            )
        self._assert_runtime_fence_is_atomic("evaluation policy")

    def test_changed_assurance_profile_fences_heartbeat_and_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_initiatives SET profile='product-competitive' "
                "WHERE initiative_id=?",
                (self.initiative_id,),
            )
        self._assert_runtime_fence_is_atomic("evaluation policy")

    def test_changed_principal_authority_fences_heartbeat_and_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_principals SET authority='reviewer' "
                "WHERE principal_id='principal-platform'"
            )
        self._assert_runtime_fence_is_atomic("principal authority or credential")

    def test_rotated_principal_credential_fences_heartbeat_and_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_principals SET credential_sha256=? "
                "WHERE principal_id='principal-platform'",
                (hashlib.sha256(b"rotated-platform-credential").hexdigest(),),
            )
        self._assert_runtime_fence_is_atomic("principal authority or credential")

    def test_revoked_principal_fences_heartbeat_and_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_principals SET status='revoked',credential_sha256=NULL "
                "WHERE principal_id='principal-platform'"
            )
        self._assert_runtime_fence_is_atomic("principal authority or credential")

    def test_changed_execution_generation_fences_context_heartbeat_and_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE task_executions SET generation=generation+1 WHERE task_id=?",
                (self.task_id,),
            )
        self._assert_runtime_fence_is_atomic("generation")

    def test_execution_assurance_fence_survives_service_restart(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        restarted = CompanyOS(self.config)
        heartbeat = restarted.heartbeat_task(
            self.task_id, "platform-runner",
            fencing_token=str(self.claim["fencing_token"]),
        )
        self.assertEqual(heartbeat["generation"], int(self.claim["generation"]))
        completed = restarted.complete_task(
            self.task_id, "Company Platform Engineer", "guarded result",
            [self.task_evidence], fencing_token=str(self.claim["fencing_token"]),
        )
        self.assertEqual(completed["status"], "done")

    def test_missing_execution_binding_fails_closed_after_upgrade(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_execution_bindings_immutable_delete")
            conn.execute(
                "DELETE FROM assurance_execution_bindings WHERE task_id=?",
                (self.task_id,),
            )
        restarted = CompanyOS(self.config)
        restarted.init()
        self._assert_runtime_fence_is_atomic("assurance context is missing")

    def test_bound_completion_without_trusted_eval_fails_atomically(self) -> None:
        self._assert_denial_is_atomic("Trusted Eval")

    def test_bound_completion_with_eval_but_without_review_fails_atomically(self) -> None:
        self._record_eval()
        self._assert_denial_is_atomic("Review Decision")

    def test_review_must_bind_exact_trusted_eval_result(self) -> None:
        self._record_eval()
        self._record_review("e" * 64)
        self._assert_denial_is_atomic("exact Trusted Eval result")

    def test_bound_completion_requires_review_bound_to_exact_result_and_artifact_set(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256, artifact_set_sha256="f" * 64)
        self._assert_denial_is_atomic("artifact set")

    def test_quarantined_eval_fails_closed(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        TrustedEvaluator(self.config).quarantine(
            self.initiative_id, "contaminated holdout",
            actor="Trusted Evaluator", principal_id="principal-evaluator",
        )
        self._assert_denial_is_atomic("quarantined")

    def test_contradictory_review_fails_closed(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self._record_review(result_sha256, decision="reject", artifact_id="contradictory-review")
        self._assert_denial_is_atomic("not affirmative")

    def test_implementer_authored_review_is_not_independent(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256, owner_principal="principal-platform")
        self._assert_denial_is_atomic("independent")

    def test_review_metadata_cannot_be_rewritten_to_fake_independence(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256, owner_principal="principal-platform")
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_artifacts SET owner_principal='principal-reviewer' "
                "WHERE artifact_id='completion-review'"
            )
        self._assert_denial_is_atomic("integrity")

    def test_review_body_and_hash_cannot_be_rewritten_after_registration(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256, decision="reject")
        with self.osys.store.connect() as conn:
            row = conn.execute(
                "SELECT content_json FROM assurance_artifacts "
                "WHERE artifact_id='completion-review'"
            ).fetchone()
            payload = json.loads(row["content_json"])
            payload["content"]["decision"] = "approve"
            rewritten = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            )
            conn.execute(
                "UPDATE assurance_artifacts SET content_json=?,content_sha256=? "
                "WHERE artifact_id='completion-review'",
                (rewritten, hashlib.sha256(rewritten.encode("ascii")).hexdigest()),
            )
        self._assert_denial_is_atomic("integrity")

    def test_review_body_hash_and_registration_audit_cannot_be_rewritten(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256, decision="reject")
        with self.osys.store.connect() as conn:
            row = conn.execute(
                "SELECT content_json FROM assurance_artifacts "
                "WHERE artifact_id='completion-review'"
            ).fetchone()
            payload = json.loads(row["content_json"])
            payload["content"]["decision"] = "approve"
            rewritten = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            )
            rewritten_sha256 = hashlib.sha256(rewritten.encode("ascii")).hexdigest()
            conn.execute(
                "UPDATE assurance_artifacts SET content_json=?,content_sha256=? "
                "WHERE artifact_id='completion-review'",
                (rewritten, rewritten_sha256),
            )
            audit = conn.execute(
                "SELECT id,details FROM audit_log "
                "WHERE action='assurance_artifact_registered' "
                "AND entity_id='completion-review:v1'"
            ).fetchone()
            details = json.loads(audit["details"])
            details["sha256"] = rewritten_sha256
            conn.execute(
                "UPDATE audit_log SET details=? WHERE id=?",
                (json.dumps(details, sort_keys=True), audit["id"]),
            )
        self._assert_denial_is_atomic("integrity")

    def test_artifact_registration_integrity_anchor_is_immutable(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE assurance_artifact_registrations SET content_sha256=? "
                    "WHERE artifact_id='completion-review' AND version=1",
                    ("0" * 64,),
                )

    def test_init_does_not_bless_preexisting_unanchored_review_tamper(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256, decision="reject")
        with self.osys.store.connect() as conn:
            conn.execute("DROP TABLE assurance_artifact_registrations")
            row = conn.execute(
                "SELECT content_json FROM assurance_artifacts "
                "WHERE artifact_id='completion-review'"
            ).fetchone()
            payload = json.loads(row["content_json"])
            payload["content"]["decision"] = "approve"
            rewritten = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            )
            rewritten_sha256 = hashlib.sha256(rewritten.encode("ascii")).hexdigest()
            conn.execute(
                "UPDATE assurance_artifacts SET content_json=?,content_sha256=? "
                "WHERE artifact_id='completion-review'",
                (rewritten, rewritten_sha256),
            )
            audit = conn.execute(
                "SELECT id,details FROM audit_log "
                "WHERE action='assurance_artifact_registered' "
                "AND entity_id='completion-review:v1'"
            ).fetchone()
            details = json.loads(audit["details"])
            details["sha256"] = rewritten_sha256
            conn.execute(
                "UPDATE audit_log SET details=? WHERE id=?",
                (json.dumps(details, sort_keys=True), audit["id"]),
            )

        self.kernel.init()
        self._assert_denial_is_atomic("integrity")

    def test_partial_registration_restore_does_not_bless_missing_build_anchor(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            review = conn.execute(
                """SELECT artifact_id,version,content_sha256,created_at,integrity_signature
                   FROM assurance_artifact_registrations
                   WHERE artifact_id='completion-review' AND version=1"""
            ).fetchone()
            conn.execute("DROP TABLE assurance_artifact_registrations")
        self.kernel.init()
        with self.osys.store.connect() as conn:
            conn.execute(
                """INSERT INTO assurance_artifact_registrations(
                       artifact_id,version,content_sha256,created_at,integrity_signature
                   ) VALUES (?,?,?,?,?)""",
                tuple(review),
            )

        self._assert_denial_is_atomic("integrity")

    def test_review_without_valid_approval_metadata_fails_closed(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_artifacts SET approved_by_principal=NULL,approved_at=NULL "
                "WHERE artifact_id='completion-review'"
            )
        self._assert_denial_is_atomic("approval")

    def test_review_self_approval_metadata_fails_closed(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_artifacts SET approved_by_principal='principal-reviewer' "
                "WHERE artifact_id='completion-review'"
            )
        self._assert_denial_is_atomic("approval")

    def test_evaluator_authored_review_is_not_independent(self) -> None:
        result_sha256 = self._record_eval()
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_principals SET authority='reviewer' "
                "WHERE principal_id='principal-evaluator'"
            )
        self._refresh_execution_principal_snapshot()
        self._record_review(result_sha256, owner_principal="principal-evaluator")
        self._assert_denial_is_atomic("principal authority or credential")

    def test_rotated_evaluator_actor_and_authority_remain_nonindependent(self) -> None:
        result_sha256 = self._record_eval()
        rotated_actor = "Rotated Evaluator Reviewer"
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_principals SET actor=?,authority='reviewer' "
                "WHERE principal_id='principal-evaluator'",
                (rotated_actor,),
            )
        self.credentials["principal-evaluator"] = (rotated_actor, "reviewer")
        self._record_review(result_sha256, owner_principal="principal-evaluator")
        self._assert_denial_is_atomic("principal authority or credential")

    def test_post_build_review_does_not_stale_g4_redispatch(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)

        decision = self.gate.dispatch_decision({
            "id": self.task_id,
            "status": "open",
            "owner": "Company Platform Engineer",
            "domain": "platform",
        })

        self.assertEqual(decision["allowed"], True, decision["reason"])
        self.assertEqual(decision["artifact_set_sha256"], self.artifact_set_sha256)

    def test_stale_post_build_review_does_not_stale_g4_redispatch(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self.kernel.supersede_artifact(
            "completion-review", 1, actor="CEO", principal_id="principal-ceo",
            reason="review replaced",
        )

        decision = self.gate.dispatch_decision({
            "id": self.task_id,
            "status": "open",
            "owner": "Company Platform Engineer",
            "domain": "platform",
        })

        self.assertEqual(decision["allowed"], True, decision["reason"])
        self.assertEqual(decision["artifact_set_sha256"], self.artifact_set_sha256)

    def test_tampered_post_build_review_does_not_block_g4_redispatch(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_artifacts SET content_json='{}' "
                "WHERE artifact_id='completion-review'"
            )

        decision = self.gate.dispatch_decision({
            "id": self.task_id,
            "status": "open",
            "owner": "Company Platform Engineer",
            "domain": "platform",
        })

        self.assertEqual(decision["allowed"], True, decision["reason"])
        self.assertEqual(decision["artifact_set_sha256"], self.artifact_set_sha256)

    def test_post_build_review_is_excluded_from_bound_build_context(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)

        self.assertEqual(
            self.context_bundle["assurance"]["artifact_set_sha256"],
            self.artifact_set_sha256,
        )
        self.assertEqual(
            [artifact["ref"] for artifact in self.context_bundle["assurance"]["artifacts"]],
            ["completion-eval-contract:v1"],
        )

    def test_dispatch_kill_switch_cannot_bypass_completion_gate(self) -> None:
        self.gate.set_kill_switch(
            True, actor="CEO", principal_id="principal-ceo", reason="dispatch rollback",
        )
        self._assert_denial_is_atomic("Trusted Eval")

    def test_exact_eval_and_independent_review_allow_atomic_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        completed = self.osys.complete_task(
            self.task_id, "Company Platform Engineer", "guarded result",
            [self.task_evidence], fencing_token=str(self.claim["fencing_token"]),
        )
        self.assertEqual(completed["status"], "done")
        self.assertEqual(completed["assurance"], {
            "initiative_id": self.initiative_id,
            "artifact_set_sha256": self.artifact_set_sha256,
            "result_sha256": result_sha256,
            "review_decision_ref": "completion-review:v1",
        })
        stored = json.loads(self.osys.store.fetch_one(
            "SELECT result FROM tasks WHERE id=?", (self.task_id,)
        )["result"])
        self.assertEqual(stored["assurance"], completed["assurance"])
        binding = self.osys.store.fetch_one(
            """SELECT completion_result_sha256,review_decision_ref,completed_at
               FROM assurance_task_bindings WHERE task_id=?""",
            (self.task_id,),
        )
        self.assertEqual(binding["completion_result_sha256"], result_sha256)
        self.assertEqual(binding["review_decision_ref"], "completion-review:v1")
        self.assertIsNotNone(binding["completed_at"])

    def test_validate_accepts_legitimate_completed_pilot(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self.osys.complete_task(
            self.task_id, "Company Platform Engineer", "guarded result",
            [self.task_evidence], fencing_token=str(self.claim["fencing_token"]),
        )

        self.assertEqual(self.osys.validate(), [])

    def test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER trusted_eval_runs_immutable_update")
            run = conn.execute(
                "SELECT * FROM trusted_eval_runs WHERE initiative_id=?",
                (self.initiative_id,),
            ).fetchone()
            forged_values = {
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
                "evidence_sha256": "0" * 64,
                "evaluator_principal_id": run["evaluator_principal_id"],
                "result_sha256": run["result_sha256"],
                "created_at": run["created_at"],
            }
            conn.execute(
                "UPDATE trusted_eval_runs SET evidence_sha256=?,integrity_signature=? "
                "WHERE initiative_id=?",
                (
                    forged_values["evidence_sha256"],
                    integrity_signature(
                        self.config.db_path, "trusted-eval-run", forged_values,
                    ),
                    self.initiative_id,
                ),
            )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_nonlatest_completed_trusted_eval_atomically(self) -> None:
        stale_result_sha256 = self._record_eval()
        evaluator = TrustedEvaluator(self.config)
        refs = {
            row["kind"]: row["manifest_sha256"]
            for row in self.osys.store.fetch_all(
                "SELECT kind,manifest_sha256 FROM trusted_eval_manifests"
            )
        }
        latest_result_sha256 = str(evaluator.record_run(
            initiative_id=self.initiative_id, refs=refs, seed=148,
            status="completed", evidence_ref="evidence/trusted-eval.json",
            max_attempts=3, actor="Trusted Evaluator",
            principal_id="principal-evaluator",
        )["result_sha256"])
        self.assertNotEqual(latest_result_sha256, stale_result_sha256)
        self._record_review(stale_result_sha256)

        self._assert_signed_sql_completion_rejected(stale_result_sha256)

    def test_signed_sql_rejects_tampered_trusted_eval_manifest_and_content_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER trusted_eval_manifests_immutable_update")
            conn.execute(
                "UPDATE trusted_eval_manifests SET manifest_json='{}' "
                "WHERE kind='dataset'"
            )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_artifact_registrations_immutable_update")
            conn.execute(
                "UPDATE assurance_artifact_registrations SET integrity_signature=? "
                "WHERE artifact_id='completion-review' AND version=1",
                ("0" * 64,),
            )
            conn.execute("DROP TRIGGER assurance_artifact_lifecycle_immutable_update")
            conn.execute(
                "UPDATE assurance_artifact_lifecycle SET integrity_signature=? "
                "WHERE artifact_id='completion-review' "
                "AND version=1 AND sequence=2",
                ("0" * 64,),
            )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_review_with_invalid_approval_signature_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_artifact_approvals_immutable_update")
            conn.execute(
                "UPDATE assurance_artifact_approvals SET integrity_signature=? "
                "WHERE artifact_id='completion-review' AND version=1",
                ("0" * 64,),
            )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        refs = {
            row["kind"]: row["manifest_sha256"]
            for row in self.osys.store.fetch_all(
                "SELECT kind,manifest_sha256 FROM trusted_eval_manifests"
            )
        }
        historical_result = {
            "initiative_id": self.initiative_id,
            "attempt": 2,
            "refs": refs,
            "seed": 149,
            "status": "failed",
            "evidence_ref": "evidence/trusted-eval.json",
            "evidence_sha256": hashlib.sha256(
                (self.root / "evidence" / "trusted-eval.json").read_bytes()
            ).hexdigest(),
        }
        historical_sha256 = hashlib.sha256(json.dumps(
            historical_result, sort_keys=True, separators=(",", ":"),
        ).encode("ascii")).hexdigest()
        created_at = "2026-07-28T11:00:00+00:00"
        historical_values = {
            **historical_result,
            "evaluator_principal_id": "principal-reviewer",
            "result_sha256": historical_sha256,
            "created_at": created_at,
        }
        with self.osys.store.connect() as conn:
            conn.execute(
                """INSERT INTO trusted_eval_runs(
                       initiative_id,attempt,candidate_sha256,dataset_sha256,
                       grader_sha256,environment_sha256,seed,status,evidence_ref,
                       evidence_sha256,evaluator_principal_id,result_sha256,
                       integrity_signature,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.initiative_id, 2, refs["candidate"], refs["dataset"],
                    refs["grader"], refs["environment"], 149, "failed",
                    historical_result["evidence_ref"],
                    historical_result["evidence_sha256"], "principal-reviewer",
                    historical_sha256, integrity_signature(
                        self.config.db_path, "trusted-eval-run", historical_values,
                    ), created_at,
                ),
            )
        self._refresh_execution_principal_snapshot()
        self._record_review(result_sha256, owner_principal="principal-reviewer")

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_principals SET authority='reviewer' "
                "WHERE principal_id='principal-platform'"
            )
        self._refresh_execution_principal_snapshot()
        self._record_review(result_sha256, owner_principal="principal-platform")

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_task_owner_review_even_with_valid_anchors_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE tasks SET owner='Control & Reliability Reviewer' WHERE id=?",
                (self.task_id,),
            )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_any_contradictory_approved_review_atomically(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self._record_review(
            result_sha256, artifact_id="contradictory-review", findings=["HIGH unresolved"],
            evidence_refs=[result_sha256],
        )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_contradictory_exact_refs_and_findings_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self._record_review(
            result_sha256, artifact_id="contradictory-exact-review",
            findings=["HIGH unresolved"],
        )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_approved_reject_decision_atomically(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self._record_review(
            result_sha256, artifact_id="contradictory-reject-review",
            decision="reject",
        )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_rejects_approved_review_missing_exact_refs_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self._record_review(
            result_sha256, artifact_id="contradictory-missing-ref-review",
            evidence_refs=[result_sha256],
        )

        self._assert_signed_sql_completion_rejected(result_sha256)

    def test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        different = self.root / "evidence" / "different-result.md"
        different.write_text("different evidence\n", encoding="utf-8")

        self._assert_signed_sql_completion_rejected(
            result_sha256,
            task_evidence=[str(self.task_evidence)],
            execution_evidence=[str(different)],
        )

    def test_completion_insert_fails_closed_without_registered_semantic_udf(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        values, task_result_json, evidence_paths_json = self._completion_sql_material(
            result_sha256,
        )
        statement, parameters = self._completion_insert_statement(
            values, task_result_json, evidence_paths_json,
        )
        before = self._completion_state()

        with sqlite3.connect(self.config.db_path) as conn:
            with self.assertRaisesRegex(sqlite3.OperationalError, "no such function"):
                conn.execute(statement, parameters)
        self.assertEqual(self._completion_state(), before)

    def test_store_context_closes_connections_retained_by_udf_callbacks(self) -> None:
        descriptor_dir = Path("/proc/self/fd")
        if not descriptor_dir.is_dir():
            self.skipTest("descriptor inventory is Linux-specific")
        before = len(tuple(descriptor_dir.iterdir()))

        for _ in range(64):
            with self.osys.store.connect_readonly() as conn:
                self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)

        after = len(tuple(descriptor_dir.iterdir()))
        self.assertLessEqual(after - before, 4)

    def test_validate_and_integrity_reject_signed_evidence_semantic_mismatch(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        different = self.root / "evidence" / "different-persisted-result.md"
        different.write_text("different persisted evidence\n", encoding="utf-8")
        values, task_result_json, evidence_paths_json = self._completion_sql_material(
            result_sha256,
            task_evidence=[str(self.task_evidence)],
            execution_evidence=[str(different)],
        )
        statement, parameters = self._completion_insert_statement(
            values, task_result_json, evidence_paths_json,
        )
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_completion_bindings_insert_guard")
            conn.execute("DROP TRIGGER tasks_bound_pilot_completion_guard")
            conn.execute(statement, parameters)
            conn.execute(
                "UPDATE assurance_task_bindings SET completion_result_sha256=?,"
                "review_decision_ref=?,completed_at=?,updated_at=? WHERE task_id=?",
                (
                    result_sha256, "completion-review:v1", values["completed_at"],
                    values["completed_at"], self.task_id,
                ),
            )
            conn.execute(
                "UPDATE task_executions SET recovery_status='completed',evidence_paths=?,"
                "updated_at=? WHERE task_id=?",
                (evidence_paths_json, values["completed_at"], self.task_id),
            )
            conn.execute(
                "UPDATE tasks SET status='done',result=?,updated_at=? WHERE id=?",
                (task_result_json, values["completed_at"], self.task_id),
            )
        completion = self.osys.store.fetch_one(
            "SELECT * FROM assurance_completion_bindings WHERE task_id=?",
            (self.task_id,),
        )

        with self.osys.store.connect_readonly() as conn:
            self.assertFalse(self.gate.completion_binding_valid(conn, completion))
        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )
        integrity = self.kernel.verify_integrity()
        self.assertEqual(integrity["status"], "integrity_conflict")
        self.assertTrue(any(
            conflict.get("anchor") == "completion_binding"
            for conflict in integrity["conflicts"]
        ), integrity)

    def test_coordinated_raw_sql_cannot_forge_all_completion_state(self) -> None:
        completed_at = "2026-07-28T12:00:00+00:00"
        forged_assurance = {
            "initiative_id": self.initiative_id,
            "artifact_set_sha256": self.artifact_set_sha256,
            "result_sha256": "f" * 64,
            "review_decision_ref": "forged-review:v1",
        }
        forged_result = json.dumps({
            "summary": "forged completion",
            "evidence": [str(self.task_evidence)],
            "assurance": forged_assurance,
        }, sort_keys=True)
        before = {
            "task": dict(self.osys.store.fetch_one(
                "SELECT status,result,updated_at FROM tasks WHERE id=?", (self.task_id,),
            )),
            "execution": dict(self.osys.store.fetch_one(
                "SELECT recovery_status,evidence_paths,updated_at FROM task_executions "
                "WHERE task_id=?", (self.task_id,),
            )),
            "binding": dict(self.osys.store.fetch_one(
                "SELECT completion_result_sha256,review_decision_ref,completed_at,updated_at "
                "FROM assurance_task_bindings WHERE task_id=?", (self.task_id,),
            )),
        }

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable|completion"):
            with self.osys.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE assurance_task_bindings SET completion_result_sha256=?,"
                    "review_decision_ref=?,completed_at=?,updated_at=? WHERE task_id=?",
                    (
                        forged_assurance["result_sha256"],
                        forged_assurance["review_decision_ref"],
                        completed_at, completed_at, self.task_id,
                    ),
                )
                conn.execute(
                    "UPDATE task_executions SET recovery_status='completed',"
                    "evidence_paths=?,updated_at=? WHERE task_id=?",
                    (json.dumps([str(self.task_evidence)]), completed_at, self.task_id),
                )
                conn.execute(
                    "UPDATE tasks SET status='done',result=?,updated_at=? WHERE id=?",
                    (forged_result, completed_at, self.task_id),
                )

        after = {
            "task": dict(self.osys.store.fetch_one(
                "SELECT status,result,updated_at FROM tasks WHERE id=?", (self.task_id,),
            )),
            "execution": dict(self.osys.store.fetch_one(
                "SELECT recovery_status,evidence_paths,updated_at FROM task_executions "
                "WHERE task_id=?", (self.task_id,),
            )),
            "binding": dict(self.osys.store.fetch_one(
                "SELECT completion_result_sha256,review_decision_ref,completed_at,updated_at "
                "FROM assurance_task_bindings WHERE task_id=?", (self.task_id,),
            )),
        }
        self.assertEqual(after, before)

    def test_structurally_complete_raw_completion_row_fails_closed_atomically(self) -> None:
        completed_at = "2026-07-28T12:00:00+00:00"
        evidence_json = json.dumps([str(self.task_evidence)], sort_keys=True)
        task_result = json.dumps({
            "summary": "structurally complete forgery",
            "evidence": [str(self.task_evidence)],
            "assurance": {
                "initiative_id": self.initiative_id,
                "artifact_set_sha256": self.artifact_set_sha256,
                "result_sha256": "e" * 64,
                "review_decision_ref": "forged-review:v1",
            },
        }, sort_keys=True)
        before = {
            "task": dict(self.osys.store.fetch_one(
                "SELECT status,result,updated_at FROM tasks WHERE id=?", (self.task_id,),
            )),
            "execution": dict(self.osys.store.fetch_one(
                "SELECT recovery_status,evidence_paths,updated_at FROM task_executions "
                "WHERE task_id=?", (self.task_id,),
            )),
            "binding": dict(self.osys.store.fetch_one(
                "SELECT completion_result_sha256,review_decision_ref,completed_at,updated_at "
                "FROM assurance_task_bindings WHERE task_id=?", (self.task_id,),
            )),
        }

        with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
            with self.osys.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """INSERT INTO assurance_completion_bindings(
                           task_id,generation,initiative_id,artifact_set_sha256,
                           trusted_eval_result_sha256,review_decision_ref,
                           review_content_sha256,task_result_sha256,
                           evidence_paths_sha256,completed_at,created_at,
                           integrity_signature
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        self.task_id, int(self.claim["generation"]), self.initiative_id,
                        self.artifact_set_sha256, "e" * 64, "forged-review:v1",
                        "d" * 64,
                        hashlib.sha256(task_result.encode("utf-8")).hexdigest(),
                        hashlib.sha256(evidence_json.encode("utf-8")).hexdigest(),
                        completed_at, completed_at, "0" * 64,
                    ),
                )
                conn.execute(
                    "UPDATE assurance_task_bindings SET completion_result_sha256=?,"
                    "review_decision_ref=?,completed_at=?,updated_at=? WHERE task_id=?",
                    ("e" * 64, "forged-review:v1", completed_at, completed_at, self.task_id),
                )
                conn.execute(
                    "UPDATE task_executions SET recovery_status='completed',"
                    "evidence_paths=?,updated_at=? WHERE task_id=?",
                    (evidence_json, completed_at, self.task_id),
                )
                conn.execute(
                    "UPDATE tasks SET status='done',result=?,updated_at=? WHERE id=?",
                    (task_result, completed_at, self.task_id),
                )

        after = {
            "task": dict(self.osys.store.fetch_one(
                "SELECT status,result,updated_at FROM tasks WHERE id=?", (self.task_id,),
            )),
            "execution": dict(self.osys.store.fetch_one(
                "SELECT recovery_status,evidence_paths,updated_at FROM task_executions "
                "WHERE task_id=?", (self.task_id,),
            )),
            "binding": dict(self.osys.store.fetch_one(
                "SELECT completion_result_sha256,review_decision_ref,completed_at,updated_at "
                "FROM assurance_task_bindings WHERE task_id=?", (self.task_id,),
            )),
        }
        self.assertEqual(after, before)

    def test_public_record_completion_rejects_caller_forged_assurance_atomically(self) -> None:
        completed_at = "2026-07-28T12:00:00+00:00"
        forged = {
            "result_sha256": "c" * 64,
            "review_decision_ref": "forged-review:v1",
        }

        with self.assertRaisesRegex(ValueError, "completion assurance"):
            with self.osys.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.gate.record_completion(conn, self.task_id, forged, completed_at)

        binding = self.osys.store.fetch_one(
            "SELECT completion_result_sha256,review_decision_ref,completed_at "
            "FROM assurance_task_bindings WHERE task_id=?", (self.task_id,),
        )
        self.assertEqual(tuple(binding), (None, None, None))
        self.assertEqual(
            self.osys.store.fetch_one(
                "SELECT COUNT(*) AS count FROM assurance_completion_bindings "
                "WHERE task_id=?", (self.task_id,),
            )["count"],
            0,
        )

    def test_public_record_completion_rejects_semantically_forged_result_body(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        assurance = self.gate.completion_decision({
            "id": self.task_id,
            "owner": "Company Platform Engineer",
        })["assurance"]
        completed_at = "2026-07-28T12:00:00+00:00"
        evidence_paths_json = json.dumps([str(self.task_evidence)], sort_keys=True)
        forged_result_json = json.dumps({
            "summary": "public semantic forgery",
            "evidence": [str(self.task_evidence)],
            "assurance": {**assurance, "review_decision_ref": "forged-review:v1"},
        }, sort_keys=True)

        with self.assertRaisesRegex(ValueError, "result body"):
            with self.osys.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.gate.record_completion(
                    conn, self.task_id, assurance, completed_at,
                    task_result_json=forged_result_json,
                    evidence_paths_json=evidence_paths_json,
                )

        binding = self.osys.store.fetch_one(
            "SELECT completion_result_sha256,review_decision_ref,completed_at "
            "FROM assurance_task_bindings WHERE task_id=?", (self.task_id,),
        )
        self.assertEqual(tuple(binding), (None, None, None))
        self.assertEqual(
            self.osys.store.fetch_one(
                "SELECT COUNT(*) AS count FROM assurance_completion_bindings "
                "WHERE task_id=?", (self.task_id,),
            )["count"],
            0,
        )

    def test_validate_and_integrity_reject_signed_structural_completion_forgery(self) -> None:
        completed_at = "2026-07-28T12:00:00+00:00"
        evidence_json = json.dumps([str(self.task_evidence)], sort_keys=True)
        assurance = {
            "initiative_id": self.initiative_id,
            "artifact_set_sha256": self.artifact_set_sha256,
            "result_sha256": "b" * 64,
            "review_decision_ref": "forged-review:v1",
        }
        task_result = json.dumps({
            "summary": "signed structural forgery",
            "evidence": [str(self.task_evidence)],
            "assurance": assurance,
        }, sort_keys=True)
        completion_values = {
            "task_id": self.task_id,
            "generation": int(self.claim["generation"]),
            "initiative_id": self.initiative_id,
            "artifact_set_sha256": self.artifact_set_sha256,
            "trusted_eval_result_sha256": assurance["result_sha256"],
            "review_decision_ref": assurance["review_decision_ref"],
            "review_content_sha256": "a" * 64,
            "task_result_sha256": hashlib.sha256(task_result.encode("utf-8")).hexdigest(),
            "evidence_paths_sha256": hashlib.sha256(evidence_json.encode("utf-8")).hexdigest(),
            "completed_at": completed_at,
            "created_at": completed_at,
        }

        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER IF EXISTS assurance_completion_bindings_insert_guard")
            conn.execute("DROP TRIGGER IF EXISTS tasks_bound_pilot_completion_guard")
            conn.execute(
                """INSERT INTO assurance_completion_bindings(
                       task_id,generation,initiative_id,artifact_set_sha256,
                       trusted_eval_result_sha256,review_decision_ref,
                       review_content_sha256,task_result_sha256,
                       evidence_paths_sha256,completed_at,created_at,
                       integrity_signature
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (*completion_values.values(), integrity_signature(
                    self.config.db_path, "completion-binding", completion_values,
                )),
            )
            conn.execute(
                "UPDATE assurance_task_bindings SET completion_result_sha256=?,"
                "review_decision_ref=?,completed_at=?,updated_at=? WHERE task_id=?",
                (
                    assurance["result_sha256"], assurance["review_decision_ref"],
                    completed_at, completed_at, self.task_id,
                ),
            )
            conn.execute(
                "UPDATE task_executions SET recovery_status='completed',"
                "evidence_paths=?,updated_at=? WHERE task_id=?",
                (evidence_json, completed_at, self.task_id),
            )
            conn.execute(
                "UPDATE tasks SET status='done',result=?,updated_at=? WHERE id=?",
                (task_result, completed_at, self.task_id),
            )

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )
        integrity = self.kernel.verify_integrity()
        self.assertEqual(integrity["status"], "integrity_conflict")
        self.assertTrue(any(
            conflict.get("anchor") == "completion_binding"
            for conflict in integrity["conflicts"]
        ), integrity)

    def test_valid_signature_cannot_bind_semantically_forged_task_result(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        assurance = self.gate.completion_decision({
            "id": self.task_id,
            "owner": "Company Platform Engineer",
        })["assurance"]
        completed_at = "2026-07-28T12:00:00+00:00"
        evidence_json = json.dumps([str(self.task_evidence)], sort_keys=True)
        forged_task_result = json.dumps({
            "summary": "cryptographically signed semantic forgery",
            "evidence": [str(self.task_evidence)],
            "assurance": {**assurance, "result_sha256": "0" * 64},
        }, sort_keys=True)
        review = self.osys.store.fetch_one(
            "SELECT content_sha256 FROM assurance_artifacts "
            "WHERE artifact_id='completion-review' AND version=1"
        )
        values = {
            "task_id": self.task_id,
            "generation": int(self.claim["generation"]),
            "initiative_id": self.initiative_id,
            "artifact_set_sha256": self.artifact_set_sha256,
            "trusted_eval_result_sha256": result_sha256,
            "review_decision_ref": "completion-review:v1",
            "review_content_sha256": review["content_sha256"],
            "task_result_sha256": hashlib.sha256(
                forged_task_result.encode("utf-8")
            ).hexdigest(),
            "evidence_paths_sha256": hashlib.sha256(
                evidence_json.encode("utf-8")
            ).hexdigest(),
            "completed_at": completed_at,
            "created_at": completed_at,
        }

        with self.assertRaisesRegex(sqlite3.IntegrityError, "completion"):
            with self.osys.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """INSERT INTO assurance_completion_bindings(
                           task_id,generation,initiative_id,artifact_set_sha256,
                           trusted_eval_result_sha256,review_decision_ref,
                           review_content_sha256,task_result_sha256,
                           evidence_paths_sha256,completed_at,created_at,
                           integrity_signature
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (*values.values(), integrity_signature(
                        self.config.db_path, "completion-binding", values,
                    )),
                )
                conn.execute(
                    "UPDATE assurance_task_bindings SET completion_result_sha256=?,"
                    "review_decision_ref=?,completed_at=?,updated_at=? WHERE task_id=?",
                    (
                        result_sha256, "completion-review:v1", completed_at,
                        completed_at, self.task_id,
                    ),
                )
                conn.execute(
                    "UPDATE task_executions SET recovery_status='completed',"
                    "evidence_paths=?,updated_at=? WHERE task_id=?",
                    (evidence_json, completed_at, self.task_id),
                )
                conn.execute(
                    "UPDATE tasks SET status='done',result=?,updated_at=? WHERE id=?",
                    (forged_task_result, completed_at, self.task_id),
                )

        self.assertEqual(
            self.osys.store.fetch_one(
                "SELECT status FROM tasks WHERE id=?", (self.task_id,),
            )["status"],
            "in_progress",
        )

    def test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim(self) -> None:
        self.osys.cancel_task(
            self.task_id, "Company Platform Engineer", "isolate kill-switch claim probe",
        )
        next_task_id = int(self.osys.create_task(
            "CEO", "Company Platform Engineer", "Kill-switch body validation", "platform", 98,
            "A kill switch may bypass dispatch policy but never artifact integrity.",
        )["task_id"])
        self.gate.bind(
            next_task_id, self.initiative_id, pilot=True,
            artifact_set_sha256=self.artifact_set_sha256,
            actor="CEO", principal_id="principal-ceo",
        )
        self.gate.set_kill_switch(
            True, actor="CEO", principal_id="principal-ceo", reason="dispatch rollback",
        )
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_artifacts SET content_json='{}' "
                "WHERE artifact_id='completion-eval-contract' AND version=1"
            )
        with self.osys.store.connect_readonly() as conn:
            before = {
                "task": dict(conn.execute(
                    "SELECT status,result,updated_at FROM tasks WHERE id=?", (next_task_id,),
                ).fetchone()),
                "executions": conn.execute(
                    "SELECT COUNT(*) FROM task_executions WHERE task_id=?", (next_task_id,),
                ).fetchone()[0],
                "claims": conn.execute(
                    "SELECT COUNT(*) FROM assurance_claim_bindings WHERE task_id=?",
                    (next_task_id,),
                ).fetchone()[0],
                "history": conn.execute(
                    "SELECT COUNT(*) FROM assurance_pilot_claim_history WHERE task_id=?",
                    (next_task_id,),
                ).fetchone()[0],
                "audits": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0],
            }

        with self.assertRaisesRegex(ValueError, "integrity"):
            self.osys.claim_task(
                next_task_id, "Company Platform Engineer",
                executor_id="kill-switch-invalid-body", backend="local",
            )

        with self.osys.store.connect_readonly() as conn:
            after = {
                "task": dict(conn.execute(
                    "SELECT status,result,updated_at FROM tasks WHERE id=?", (next_task_id,),
                ).fetchone()),
                "executions": conn.execute(
                    "SELECT COUNT(*) FROM task_executions WHERE task_id=?", (next_task_id,),
                ).fetchone()[0],
                "claims": conn.execute(
                    "SELECT COUNT(*) FROM assurance_claim_bindings WHERE task_id=?",
                    (next_task_id,),
                ).fetchone()[0],
                "history": conn.execute(
                    "SELECT COUNT(*) FROM assurance_pilot_claim_history WHERE task_id=?",
                    (next_task_id,),
                ).fetchone()[0],
                "audits": conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
                "events": conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0],
            }
        self.assertEqual(after, before)

    def test_direct_sql_cannot_complete_a_claimed_bound_pilot(self) -> None:
        with self.assertRaisesRegex(sqlite3.IntegrityError, "pilot completion"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE tasks SET status='done',result=? WHERE id=?",
                    ('{"summary":"forged completion"}', self.task_id),
                )

        task = self.osys.store.fetch_one(
            "SELECT status,result FROM tasks WHERE id=?", (self.task_id,)
        )
        execution = self.osys.store.fetch_one(
            "SELECT recovery_status FROM task_executions WHERE task_id=?", (self.task_id,)
        )
        binding = self.osys.store.fetch_one(
            """SELECT completion_result_sha256,review_decision_ref,completed_at
               FROM assurance_task_bindings WHERE task_id=?""",
            (self.task_id,),
        )
        self.assertEqual((task["status"], task["result"]), ("in_progress", None))
        self.assertEqual(execution["recovery_status"], "running")
        self.assertEqual(tuple(binding), (None, None, None))

    def test_validate_reports_forged_bound_pilot_completion_state(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER IF EXISTS tasks_bound_pilot_completion_guard")
            conn.execute(
                "UPDATE tasks SET status='done',result=? WHERE id=?",
                ('{"summary":"forged completion"}', self.task_id),
            )

        errors = self.osys.validate()

        self.assertTrue(
            any(
                f"Bound pilot task {self.task_id} completion state is inconsistent" in error
                for error in errors
            ),
            errors,
        )

    def test_validate_reports_forged_completion_after_claim_anchor_deletion(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER tasks_bound_pilot_completion_guard")
            conn.execute("DROP TRIGGER assurance_claim_bindings_immutable_delete")
            conn.execute("DROP TRIGGER assurance_pilot_claim_history_immutable_delete")
            conn.execute(
                "DELETE FROM assurance_claim_bindings WHERE task_id=?", (self.task_id,)
            )
            conn.execute(
                "UPDATE tasks SET status='done',result=? WHERE id=?",
                ('{"summary":"forged after claim deletion"}', self.task_id),
            )

        errors = self.osys.validate()

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent", errors,
        )

    def test_validate_reports_current_claim_without_history_counterpart(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_claim_bindings_immutable_delete")
            conn.execute("DROP TRIGGER assurance_pilot_claim_history_immutable_delete")
            conn.execute(
                "DELETE FROM assurance_pilot_claim_history WHERE task_id=?",
                (self.task_id,),
            )

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )

    def test_validate_reports_history_without_current_claim_counterpart(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_claim_bindings_immutable_delete")
            conn.execute("DROP TRIGGER assurance_pilot_claim_history_immutable_delete")
            conn.execute(
                "DELETE FROM assurance_claim_bindings WHERE task_id=?", (self.task_id,)
            )

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )

    def test_validate_reports_forged_completion_after_history_deletion(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER tasks_bound_pilot_completion_guard")
            conn.execute("DROP TRIGGER assurance_claim_bindings_immutable_delete")
            conn.execute("DROP TRIGGER assurance_pilot_claim_history_immutable_delete")
            conn.execute(
                "DELETE FROM assurance_pilot_claim_history WHERE task_id=?",
                (self.task_id,),
            )
            conn.execute(
                "UPDATE tasks SET status='done',result=? WHERE id=?",
                ('{"summary":"forged after history deletion"}', self.task_id),
            )

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )

    def test_validate_reports_claim_history_value_mismatch(self) -> None:
        with self.osys.store.connect() as conn:
            history = conn.execute(
                "SELECT * FROM assurance_pilot_claim_history WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
            conn.execute("DROP TRIGGER assurance_pilot_claim_history_immutable_update")
            values = {
                key: history[key] for key in (
                    "task_id", "generation", "initiative_id", "artifact_set_sha256",
                    "fencing_token_sha256", "created_at",
                )
            }
            values["artifact_set_sha256"] = "0" * 64
            conn.execute(
                "UPDATE assurance_pilot_claim_history "
                "SET artifact_set_sha256=?,integrity_signature=? WHERE task_id=?",
                (
                    values["artifact_set_sha256"],
                    integrity_signature(
                        self.config.db_path, "pilot-claim-history", values,
                    ),
                    self.task_id,
                ),
            )

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )

    def test_validate_reports_claim_history_generation_mismatch(self) -> None:
        with self.osys.store.connect() as conn:
            history = conn.execute(
                "SELECT * FROM assurance_pilot_claim_history WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
            conn.execute("DROP TRIGGER assurance_pilot_claim_history_immutable_update")
            values = {
                key: history[key] for key in (
                    "task_id", "generation", "initiative_id", "artifact_set_sha256",
                    "fencing_token_sha256", "created_at",
                )
            }
            values["generation"] = int(values["generation"]) + 1
            conn.execute(
                "UPDATE assurance_pilot_claim_history "
                "SET generation=?,integrity_signature=? WHERE task_id=?",
                (
                    values["generation"],
                    integrity_signature(
                        self.config.db_path, "pilot-claim-history", values,
                    ),
                    self.task_id,
                ),
            )

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )

    def test_validate_reports_invalid_current_claim_signature(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_claim_bindings_immutable_update")
            conn.execute(
                "UPDATE assurance_claim_bindings SET integrity_signature=? WHERE task_id=?",
                ("0" * 64, self.task_id),
            )

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )

    def test_validate_reports_invalid_claim_history_signature(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_pilot_claim_history_immutable_update")
            conn.execute(
                "UPDATE assurance_pilot_claim_history SET integrity_signature=? "
                "WHERE task_id=?",
                ("0" * 64, self.task_id),
            )

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent",
            self.osys.validate(),
        )

    def test_validate_reports_forged_completion_after_pilot_demotion(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER tasks_bound_pilot_completion_guard")
            conn.execute("DROP TRIGGER assurance_task_bindings_claimed_immutable_update")
            conn.execute(
                "UPDATE assurance_task_bindings SET pilot=0 WHERE task_id=?",
                (self.task_id,),
            )
            conn.execute(
                "UPDATE tasks SET status='done',result=? WHERE id=?",
                ('{"summary":"forged after pilot demotion"}', self.task_id),
            )

        errors = self.osys.validate()

        self.assertIn(
            f"Bound pilot task {self.task_id} completion state is inconsistent", errors,
        )

    def test_validate_keeps_never_bound_and_nonpilot_completions_compatible(self) -> None:
        with self.osys.store.connect() as conn:
            now = "2026-07-28T12:00:00+00:00"
            never_bound = int(conn.execute(
                """INSERT INTO tasks(
                       created_at,updated_at,owner,title,domain,status,priority,result
                   ) VALUES (?,?,?,?,?,'done',1,'legacy result')""",
                (now, now, "Company Platform Engineer", "Never bound completion", "review"),
            ).lastrowid)
            nonpilot = int(conn.execute(
                """INSERT INTO tasks(
                       created_at,updated_at,owner,title,domain,status,priority
                   ) VALUES (?,?,?,?,?,'open',1)""",
                (now, now, "Company Platform Engineer", "Nonpilot completion", "review"),
            ).lastrowid)
        self.gate.bind(
            nonpilot, "nonpilot-initiative", pilot=False,
            actor="CEO", principal_id="principal-ceo",
        )
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE tasks SET status='done',result='legacy result' WHERE id=?",
                (nonpilot,),
            )

        self.assertEqual(self.osys.validate(), [])

    def test_init_backfills_signed_pilot_claim_history_idempotently(self) -> None:
        task_before = dict(self.osys.store.fetch_one(
            "SELECT * FROM tasks WHERE id=?", (self.task_id,)
        ))
        execution_before = dict(self.osys.store.fetch_one(
            "SELECT * FROM task_executions WHERE task_id=?", (self.task_id,)
        ))
        with self.osys.store.connect() as conn:
            conn.execute("DROP TABLE assurance_pilot_claim_history")

        self.gate.init()
        self.gate.init()

        histories = self.osys.store.fetch_all(
            "SELECT * FROM assurance_pilot_claim_history WHERE task_id=?",
            (self.task_id,),
        )
        self.assertEqual(len(histories), 1)
        values = {
            key: histories[0][key] for key in (
                "task_id", "generation", "initiative_id", "artifact_set_sha256",
                "fencing_token_sha256", "created_at",
            )
        }
        self.assertTrue(verify_integrity_signature(
            self.config.db_path, "pilot-claim-history", values,
            histories[0]["integrity_signature"],
        ))
        self.assertEqual(dict(self.osys.store.fetch_one(
            "SELECT * FROM tasks WHERE id=?", (self.task_id,)
        )), task_before)
        self.assertEqual(dict(self.osys.store.fetch_one(
            "SELECT * FROM task_executions WHERE task_id=?", (self.task_id,)
        )), execution_before)

    def test_superseded_artifact_status_cannot_be_rolled_back_with_raw_sql(self) -> None:
        self.kernel.supersede_artifact(
            "completion-eval-contract", 1,
            actor="CEO", principal_id="principal-ceo", reason="replace build contract",
        )

        with self.assertRaisesRegex(sqlite3.IntegrityError, "lifecycle"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE assurance_artifacts SET status='approved' "
                    "WHERE artifact_id='completion-eval-contract' AND version=1"
                )

    def test_dropped_status_guard_cannot_hide_supersession_rollback(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self.kernel.supersede_artifact(
            "completion-eval-contract", 1,
            actor="CEO", principal_id="principal-ceo", reason="replace build contract",
        )
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER IF EXISTS assurance_artifact_status_transition_guard")
            conn.execute(
                "UPDATE assurance_artifacts SET status='approved' "
                "WHERE artifact_id='completion-eval-contract' AND version=1"
            )

        integrity = self.kernel.verify_integrity()

        self.assertEqual(integrity["status"], "integrity_conflict")
        self.assertTrue(
            any(conflict.get("anchor") == "lifecycle" for conflict in integrity["conflicts"]),
            integrity,
        )
        self._assert_denial_is_atomic("lifecycle")

    def test_missing_evaluator_identity_lineage_fails_closed(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER trusted_eval_runs_immutable_update")
            conn.execute(
                "UPDATE trusted_eval_runs SET evaluator_principal_id='' "
                "WHERE initiative_id=?",
                (self.initiative_id,),
            )

        self._assert_denial_is_atomic("integrity anchor")

    def test_post_claim_binding_removal_fails_closed(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_task_bindings_claimed_immutable_delete")
            conn.execute("DELETE FROM assurance_task_bindings WHERE task_id=?", (self.task_id,))
        self._assert_runtime_fence_is_atomic("task binding")

    def test_post_claim_pilot_demotion_fails_closed(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_task_bindings_claimed_immutable_update")
            conn.execute(
                "UPDATE assurance_task_bindings SET pilot=0 WHERE task_id=?", (self.task_id,)
            )
        self._assert_runtime_fence_is_atomic("task binding")

    def test_post_claim_task_binding_is_immutable(self) -> None:
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE assurance_task_bindings SET pilot=0 WHERE task_id=?",
                    (self.task_id,),
                )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "DELETE FROM assurance_task_bindings WHERE task_id=?", (self.task_id,)
                )

    def test_claimed_binding_allows_one_completion_write_without_rebinding(self) -> None:
        completed_at = "2026-07-28T12:00:00+00:00"
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        assurance = self.gate.completion_decision({
            "id": self.task_id,
            "owner": "Company Platform Engineer",
        })["assurance"]
        evidence_paths_json = json.dumps([str(self.task_evidence)], sort_keys=True)
        task_result_json = json.dumps({
            "summary": "guarded result",
            "evidence": [str(self.task_evidence)],
            "assurance": assurance,
        }, sort_keys=True)
        before = self.osys.store.fetch_one(
            "SELECT * FROM assurance_task_bindings WHERE task_id=?", (self.task_id,)
        )

        for statement in (
            "UPDATE assurance_task_bindings SET completion_result_sha256='partial' "
            "WHERE task_id=?",
            "UPDATE assurance_task_bindings SET completion_result_sha256='partial',"
            "review_decision_ref='partial',completed_at='partial',updated_at='different' "
            "WHERE task_id=?",
        ):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                with self.osys.store.connect() as conn:
                    conn.execute(statement, (self.task_id,))

        with self.osys.store.connect() as conn:
            self.gate.record_completion(
                conn, self.task_id, assurance, completed_at,
                task_result_json=task_result_json,
                evidence_paths_json=evidence_paths_json,
            )

        after = self.osys.store.fetch_one(
            "SELECT * FROM assurance_task_bindings WHERE task_id=?", (self.task_id,)
        )
        for column in ("task_id", "initiative_id", "pilot", "artifact_set_sha256", "created_at"):
            self.assertEqual(after[column], before[column])
        self.assertEqual(after["completion_result_sha256"], assurance["result_sha256"])
        self.assertEqual(after["review_decision_ref"], assurance["review_decision_ref"])
        self.assertEqual(after["completed_at"], completed_at)
        self.assertEqual(after["updated_at"], completed_at)

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE assurance_task_bindings SET completion_result_sha256=NULL "
                    "WHERE task_id=?",
                    (self.task_id,),
                )
        with self.assertRaisesRegex(ValueError, "changed concurrently"):
            with self.osys.store.connect() as conn:
                self.gate.record_completion(
                    conn, self.task_id, assurance, completed_at,
                    task_result_json=task_result_json,
                    evidence_paths_json=evidence_paths_json,
                )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE assurance_task_bindings SET initiative_id='rebound' "
                    "WHERE task_id=?",
                    (self.task_id,),
                )

    def test_pilot_demotion_before_context_compilation_fails_closed(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_execution_bindings_immutable_delete")
            conn.execute("DROP TRIGGER assurance_task_bindings_claimed_immutable_update")
            conn.execute(
                "DELETE FROM assurance_execution_bindings WHERE task_id=?", (self.task_id,)
            )
            conn.execute("DELETE FROM task_contexts WHERE task_id=?", (self.task_id,))
            conn.execute(
                "UPDATE assurance_task_bindings SET pilot=0 WHERE task_id=?", (self.task_id,)
            )

        with self.assertRaisesRegex(ValueError, "claim.*task binding"):
            ContextCompiler(
                self.config, context_root=self.old_cwd / "company_context",
            ).compile(
                self.task_id,
                generation=int(self.claim["generation"]),
                role="Company Platform Engineer",
                repository={"id": "agent-company"},
                fencing_token=str(self.claim["fencing_token"]),
            )

    def test_task_binding_removal_before_context_compilation_fails_closed(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_execution_bindings_immutable_delete")
            conn.execute("DROP TRIGGER assurance_task_bindings_claimed_immutable_delete")
            conn.execute(
                "DELETE FROM assurance_execution_bindings WHERE task_id=?", (self.task_id,)
            )
            conn.execute("DELETE FROM task_contexts WHERE task_id=?", (self.task_id,))
            conn.execute(
                "DELETE FROM assurance_task_bindings WHERE task_id=?", (self.task_id,)
            )

        with self.assertRaisesRegex(ValueError, "claim.*task binding"):
            ContextCompiler(
                self.config, context_root=self.old_cwd / "company_context",
            ).compile(
                self.task_id,
                generation=int(self.claim["generation"]),
                role="Company Platform Engineer",
                repository={"id": "agent-company"},
                fencing_token=str(self.claim["fencing_token"]),
            )

    def test_bound_runtime_operations_require_exact_fencing_token(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        for operation in (
            lambda: self.osys.heartbeat_task(self.task_id, "platform-runner"),
            lambda: self.osys.checkpoint_task(
                self.task_id, "platform-runner", "tests pass", "complete",
            ),
            lambda: self.osys.complete_task(
                self.task_id, "Company Platform Engineer", "guarded result",
                [self.task_evidence],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "fencing token"):
                operation()

    def test_checkpoint_is_fenced_and_rolls_back_on_drift(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_gate_decisions SET conditions_json=? "
                "WHERE initiative_id=? AND gate='G4'",
                ('["raised completion threshold"]', self.initiative_id),
            )
        with self.osys.store.connect_readonly() as conn:
            before = dict(conn.execute(
                "SELECT checkpoint,next_action,heartbeat_at,lease_expires_at,updated_at "
                "FROM task_executions WHERE task_id=?", (self.task_id,),
            ).fetchone())
            audit_before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "evaluation policy"):
            self.osys.checkpoint_task(
                self.task_id, "platform-runner", "should not persist", "should not renew",
                fencing_token=str(self.claim["fencing_token"]),
            )
        with self.osys.store.connect_readonly() as conn:
            after = dict(conn.execute(
                "SELECT checkpoint,next_action,heartbeat_at,lease_expires_at,updated_at "
                "FROM task_executions WHERE task_id=?", (self.task_id,),
            ).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], audit_before)
        self.assertEqual(after, before)

    def test_cancelled_initiative_fences_runtime(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_initiatives SET status='cancelled',mode='pilot' "
                "WHERE initiative_id=?",
                (self.initiative_id,),
            )
        self._assert_runtime_fence_is_atomic("lifecycle")

    def test_elapsed_g4_expiry_fences_runtime(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        expired = "2026-07-26T00:00:00+00:00"
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_gate_decisions SET expires_at=? "
                "WHERE initiative_id=? AND gate='G4'",
                (expired, self.initiative_id),
            )
            binding = conn.execute(
                "SELECT * FROM assurance_execution_bindings WHERE task_id=?",
                (self.task_id,),
            ).fetchone()
            snapshot = self.gate._execution_snapshot(conn, self.initiative_id)
            conn.execute("DROP TRIGGER assurance_execution_bindings_immutable_update")
            values = {
                "task_id": binding["task_id"],
                "generation": binding["generation"],
                "initiative_id": binding["initiative_id"],
                "artifact_set_sha256": binding["artifact_set_sha256"],
                "evaluation_policy_sha256": snapshot["evaluation_policy_sha256"],
                "principal_state_sha256": binding["principal_state_sha256"],
                "context_bundle_sha256": binding["context_bundle_sha256"],
                "fencing_token_sha256": binding["fencing_token_sha256"],
                "created_at": binding["created_at"],
            }
            conn.execute(
                "UPDATE assurance_execution_bindings SET evaluation_policy_sha256=?,"
                "integrity_signature=? "
                "WHERE task_id=? AND generation=?",
                (
                    snapshot["evaluation_policy_sha256"],
                    integrity_signature(
                        self.config.db_path, "execution-binding", values,
                    ),
                    self.task_id, binding["generation"],
                ),
            )
        self._assert_runtime_fence_is_atomic("expired")

    def test_unrelated_principal_change_does_not_fence_pilot(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                """INSERT INTO assurance_principals(
                       principal_id,actor,authority,credential_sha256,status,created_at
                   ) VALUES ('principal-unrelated','Unrelated Reviewer','reviewer',?,
                             'active','2026-07-27T00:00:00+00:00')""",
                (hashlib.sha256(b"unrelated").hexdigest(),),
            )
        heartbeat = self.osys.heartbeat_task(
            self.task_id, "platform-runner",
            fencing_token=str(self.claim["fencing_token"]),
        )
        self.assertEqual(heartbeat["recovery_status"], "running")

    def test_forged_execution_binding_and_dropped_triggers_fail_closed(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_execution_bindings_immutable_update")
            conn.execute("DROP TRIGGER assurance_execution_bindings_immutable_delete")
            conn.execute(
                "UPDATE assurance_execution_bindings SET artifact_set_sha256=? "
                "WHERE task_id=?", ("0" * 64, self.task_id),
            )
        self.gate.init()
        with self.osys.store.connect_readonly() as conn:
            trigger_names = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE 'assurance_execution_bindings_immutable_%'"
                )
            }
        self.assertEqual(trigger_names, {
            "assurance_execution_bindings_immutable_update",
            "assurance_execution_bindings_immutable_delete",
        })
        self._assert_runtime_fence_is_atomic("integrity")

    def test_forged_review_registration_after_trigger_drop_fails_closed(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256, decision="reject")
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_artifact_registrations_immutable_update")
            row = conn.execute(
                "SELECT content_json FROM assurance_artifacts "
                "WHERE artifact_id='completion-review'"
            ).fetchone()
            payload = json.loads(row["content_json"])
            payload["content"]["decision"] = "approve"
            rewritten = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            )
            rewritten_sha256 = hashlib.sha256(rewritten.encode("ascii")).hexdigest()
            conn.execute(
                "UPDATE assurance_artifacts SET content_json=?,content_sha256=? "
                "WHERE artifact_id='completion-review'",
                (rewritten, rewritten_sha256),
            )
            conn.execute(
                "UPDATE assurance_artifact_registrations SET content_sha256=? "
                "WHERE artifact_id='completion-review' AND version=1",
                (rewritten_sha256,),
            )
        self.kernel.init()
        self._assert_denial_is_atomic("integrity")

    def test_initialization_repairs_all_canonical_immutability_triggers(self) -> None:
        self._record_eval()
        with self.osys.store.connect() as conn:
            for name in (
                "assurance_execution_bindings_immutable_update",
                "assurance_claim_bindings_immutable_delete",
                "assurance_pilot_claim_history_immutable_update",
                "assurance_task_bindings_claimed_immutable_update",
                "assurance_completion_bindings_insert_guard",
                "assurance_artifact_registrations_immutable_delete",
                "assurance_artifact_approvals_immutable_update",
                "trusted_eval_runs_immutable_delete",
                "trusted_eval_manifests_immutable_update",
            ):
                conn.execute(f"DROP TRIGGER {name}")

        self.gate.init()
        TrustedEvaluator(self.config).init()

        expected = {
            "assurance_execution_bindings_immutable_update",
            "assurance_execution_bindings_immutable_delete",
            "assurance_claim_bindings_immutable_update",
            "assurance_claim_bindings_immutable_delete",
            "assurance_pilot_claim_history_immutable_update",
            "assurance_pilot_claim_history_immutable_delete",
            "assurance_completion_bindings_insert_guard",
            "assurance_completion_bindings_immutable_update",
            "assurance_completion_bindings_immutable_delete",
            "assurance_task_bindings_claimed_immutable_update",
            "assurance_task_bindings_claimed_immutable_delete",
            "assurance_artifact_registrations_immutable_update",
            "assurance_artifact_registrations_immutable_delete",
            "assurance_artifact_approvals_immutable_update",
            "assurance_artifact_approvals_immutable_delete",
            "trusted_eval_runs_immutable_update",
            "trusted_eval_runs_immutable_delete",
            "trusted_eval_manifests_immutable_update",
            "trusted_eval_manifests_immutable_delete",
            "trusted_eval_contracts_immutable_update",
            "trusted_eval_contracts_immutable_delete",
            "trusted_eval_quarantines_append_only",
            "trusted_eval_quarantines_no_delete",
        }
        with self.osys.store.connect_readonly() as conn:
            actual = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
        self.assertTrue(expected <= actual)

    def test_forged_trusted_eval_run_after_trigger_drop_fails_closed(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER trusted_eval_runs_immutable_update")
            conn.execute(
                "UPDATE trusted_eval_runs SET evaluator_principal_id='principal-reviewer' "
                "WHERE initiative_id=?", (self.initiative_id,),
            )
        TrustedEvaluator(self.config).init()
        self._assert_denial_is_atomic("integrity anchor")

    def test_review_approval_anchor_fails_closed_after_trigger_drop(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_artifact_approvals_immutable_update")
            conn.execute(
                "UPDATE assurance_artifact_approvals "
                "SET approved_by_principal='principal-platform' "
                "WHERE artifact_id='completion-review'"
            )
        self.kernel.init()
        self._assert_denial_is_atomic("approval")

    def test_existing_binding_schema_is_upgraded_without_data_loss(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("DROP TABLE assurance_execution_bindings")
            conn.execute("DROP TABLE assurance_task_bindings")
            conn.execute(
                """CREATE TABLE assurance_task_bindings(
                       task_id INTEGER PRIMARY KEY,initiative_id TEXT NOT NULL,
                       pilot INTEGER NOT NULL,artifact_set_sha256 TEXT,
                       created_at TEXT NOT NULL,updated_at TEXT NOT NULL
                   )"""
            )
            conn.execute(
                """INSERT INTO assurance_task_bindings(
                       task_id,initiative_id,pilot,artifact_set_sha256,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?)""",
                (
                    self.task_id, self.initiative_id, 1, self.artifact_set_sha256,
                    "2026-07-26T00:00:00+00:00", "2026-07-26T00:00:00+00:00",
                ),
            )
        self.gate.init()
        self.gate.init()
        binding = self.osys.store.fetch_one(
            "SELECT * FROM assurance_task_bindings WHERE task_id=?", (self.task_id,)
        )
        self.assertEqual(binding["initiative_id"], self.initiative_id)
        self.assertEqual(binding["artifact_set_sha256"], self.artifact_set_sha256)
        self.assertIsNone(binding["completion_result_sha256"])
        columns = {
            row["name"] for row in self.osys.store.fetch_all(
                "PRAGMA table_info(assurance_execution_bindings)"
            )
        }
        self.assertEqual(columns, {
            "task_id", "generation", "initiative_id", "artifact_set_sha256",
            "evaluation_policy_sha256", "principal_state_sha256",
            "context_bundle_sha256", "fencing_token_sha256",
            "integrity_signature", "created_at",
        })
        claim_columns = {
            row["name"] for row in self.osys.store.fetch_all(
                "PRAGMA table_info(assurance_claim_bindings)"
            )
        }
        self.assertEqual(claim_columns, {
            "task_id", "generation", "initiative_id", "artifact_set_sha256",
            "fencing_token_sha256", "integrity_signature", "created_at",
        })
        history_columns = {
            row["name"] for row in self.osys.store.fetch_all(
                "PRAGMA table_info(assurance_pilot_claim_history)"
            )
        }
        self.assertEqual(history_columns, claim_columns)
        completion_columns = {
            row["name"] for row in self.osys.store.fetch_all(
                "PRAGMA table_info(assurance_completion_bindings)"
            )
        }
        self.assertEqual(completion_columns, {
            "task_id", "generation", "initiative_id", "artifact_set_sha256",
            "trusted_eval_result_sha256", "review_decision_ref",
            "review_content_sha256", "task_result_sha256",
            "evidence_paths_sha256", "task_result_json", "evidence_paths_json",
            "completed_at", "created_at",
            "integrity_signature",
        })

    def test_completed_binding_snapshot_migration_precedes_old_immutability_trigger(
        self,
    ) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        self.osys.complete_task(
            self.task_id, "Company Platform Engineer", "guarded result",
            [self.task_evidence], fencing_token=str(self.claim["fencing_token"]),
        )
        with self.osys.store.connect() as conn:
            conn.execute("DROP TRIGGER assurance_completion_bindings_insert_guard")
            conn.execute("DROP TRIGGER assurance_completion_bindings_immutable_update")
            conn.execute("DROP TRIGGER assurance_completion_bindings_immutable_delete")
            conn.execute("DROP TRIGGER assurance_task_bindings_claimed_immutable_update")
            conn.execute("DROP TRIGGER tasks_bound_pilot_completion_guard")
            conn.execute(
                """CREATE TABLE legacy_assurance_completion_bindings(
                       task_id INTEGER PRIMARY KEY,generation INTEGER NOT NULL,
                       initiative_id TEXT NOT NULL,artifact_set_sha256 TEXT NOT NULL,
                       trusted_eval_result_sha256 TEXT NOT NULL,
                       review_decision_ref TEXT NOT NULL,
                       review_content_sha256 TEXT NOT NULL,
                       task_result_sha256 TEXT NOT NULL,
                       evidence_paths_sha256 TEXT NOT NULL,completed_at TEXT NOT NULL,
                       created_at TEXT NOT NULL,integrity_signature TEXT NOT NULL
                   )"""
            )
            conn.execute(
                """INSERT INTO legacy_assurance_completion_bindings
                   SELECT task_id,generation,initiative_id,artifact_set_sha256,
                          trusted_eval_result_sha256,review_decision_ref,
                          review_content_sha256,task_result_sha256,
                          evidence_paths_sha256,completed_at,created_at,
                          integrity_signature
                   FROM assurance_completion_bindings"""
            )
            conn.execute("DROP TABLE assurance_completion_bindings")
            conn.execute(
                "ALTER TABLE legacy_assurance_completion_bindings "
                "RENAME TO assurance_completion_bindings"
            )
            conn.execute(
                """CREATE TRIGGER assurance_completion_bindings_immutable_update
                   BEFORE UPDATE ON assurance_completion_bindings
                   BEGIN SELECT RAISE(ABORT,
                       'assurance completion binding is immutable'); END"""
            )

        self.gate.init()

        completion = self.osys.store.fetch_one(
            "SELECT * FROM assurance_completion_bindings WHERE task_id=?",
            (self.task_id,),
        )
        task = self.osys.store.fetch_one(
            "SELECT result FROM tasks WHERE id=?", (self.task_id,),
        )
        execution = self.osys.store.fetch_one(
            "SELECT evidence_paths FROM task_executions WHERE task_id=?",
            (self.task_id,),
        )
        self.assertEqual(completion["task_result_json"], task["result"])
        self.assertEqual(completion["evidence_paths_json"], execution["evidence_paths"])
        with self.osys.store.connect_readonly() as conn:
            self.assertTrue(self.gate.completion_binding_valid(conn, completion))


if __name__ == "__main__":
    unittest.main()
