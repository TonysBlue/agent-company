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
                "findings": [],
                "evidence_refs": [
                    result_sha256,
                    artifact_set_sha256 or self.artifact_set_sha256,
                ],
            },
        }
        self.kernel.register_artifact(review, actor=actor, principal_id=owner_principal)
        self.kernel.approve_artifact(
            artifact_id, 1, actor="CEO", principal_id="principal-ceo",
        )

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
        self.assertIsNotNone(binding)
        self.assertEqual(binding["initiative_id"], self.initiative_id)
        self.assertEqual(binding["artifact_set_sha256"], self.artifact_set_sha256)
        self.assertEqual(len(binding["fencing_token_sha256"]), 64)
        self.assertEqual(len(binding["integrity_signature"]), 64)

        with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
            with self.osys.store.connect() as conn:
                conn.execute(
                    "UPDATE assurance_claim_bindings SET initiative_id='forged' "
                    "WHERE task_id=? AND generation=?",
                    (self.task_id, int(self.claim["generation"])),
                )

    def test_stale_artifact_fences_heartbeat_and_completion(self) -> None:
        result_sha256 = self._record_eval()
        self._record_review(result_sha256)
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_artifacts SET status='stale' "
                "WHERE artifact_id='completion-eval-contract'"
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
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_artifacts SET status='stale' "
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
        assurance = {
            "result_sha256": "a" * 64,
            "review_decision_ref": "completion-review:v1",
        }
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
            PilotGate.record_completion(conn, self.task_id, assurance, completed_at)

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
                PilotGate.record_completion(conn, self.task_id, assurance, completed_at)
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
                "assurance_task_bindings_claimed_immutable_update",
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


if __name__ == "__main__":
    unittest.main()
