from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.assurance import ARTIFACT_CONTENT_SCHEMAS, AssuranceError, AssuranceKernel
from agent_company.config import load_config
from agent_company.db import Store


class AssuranceKernelTest(unittest.TestCase):
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
        Store(self.config.db_path).init()
        self.kernel = AssuranceKernel(self.config)
        self.kernel.init()
        principals = [
            ("principal-chairman", "Chairman", "chairman"),
            ("principal-ceo", "CEO", "executive"),
            ("principal-platform", "Company Platform Engineer", "implementer"),
            ("principal-control-reviewer", "Control & Reliability Reviewer", "reviewer"),
        ]
        with Store(self.config.db_path).connect() as conn:
            for principal_id, actor, authority in principals:
                credential = f"test-credential-{principal_id}"
                os.environ[f"ASSURANCE_CREDENTIAL_{principal_id.upper().replace('-', '_')}"] = credential
                conn.execute(
                    """INSERT INTO assurance_principals(
                           principal_id,actor,authority,credential_sha256,status,created_at
                       ) VALUES (?,?,?,?, 'active','2026-07-24T00:00:00+00:00')""",
                    (principal_id, actor, authority, hashlib.sha256(credential.encode()).hexdigest()),
                )

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def artifact(self, kind: str, artifact_id: str, version: int = 1) -> dict[str, object]:
        examples = {
            "goal_contract": {"outcome": "measurable outcome", "non_goals": ["production deployment"]},
            "design_record": {"problem": "governed change", "decision": "shadow registry", "alternatives": ["manual records"]},
            "architecture_decision": {"context": "control plane", "decision": "additive tables", "consequences": ["no task enforcement"]},
            "behavior_spec": {"behavior": "record without blocking", "scenarios": ["shadow classification"], "invariants": ["tasks unchanged"]},
            "eval_contract": {"hard_gates": ["tasks unchanged"], "graders": ["unit tests"], "release_rule": "all hard gates pass"},
            "baseline_report": {"subject": "legacy control plane", "measurements": ["task count"], "limitations": ["shadow only"]},
            "review_decision": {"decision": "approve", "findings": [], "evidence_refs": ["test-run"]},
            "release_decision": {"decision": "hold", "conditions": ["review"], "rollback": "disable shadow views"},
            "change_decision": {"reason": "new evidence", "changed_nodes": ["goal"], "invalidated_nodes": ["design"]},
            "incident_record": {"severity": "low", "impact": "none", "containment": ["disable"]},
        }
        content = examples.get(kind, {"summary": f"{kind} evidence"})
        return {
            "schema_version": "assurance-artifact/v1",
            "artifact_id": artifact_id,
            "kind": kind,
            "version": version,
            "status": "draft",
            "initiative_id": "pilot-control-gate",
            "profile": "control-plane-reliability",
            "risk_class": "C2",
            "owner_principal": "principal-platform",
            "repository_id": "agent-company",
            "content": content,
        }

    def test_registers_immutable_versioned_artifact_and_audits_hash(self) -> None:
        payload = self.artifact("goal_contract", "goal-control-gate")

        result = self.kernel.register_artifact(payload, actor="Company Platform Engineer", principal_id="principal-platform")

        expected = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(result["content_sha256"], expected)
        row = Store(self.config.db_path).fetch_one(
            "SELECT * FROM assurance_artifacts WHERE artifact_id=? AND version=1", ("goal-control-gate",)
        )
        self.assertEqual(row["content_sha256"], expected)
        self.assertEqual(row["status"], "draft")
        audit = Store(self.config.db_path).fetch_one(
            "SELECT * FROM audit_log WHERE action='assurance_artifact_registered'"
        )
        self.assertIsNotNone(audit)

        with self.assertRaisesRegex(AssuranceError, "immutable"):
            self.kernel.register_artifact(payload, actor="Company Platform Engineer", principal_id="principal-platform")

    def test_every_artifact_kind_has_strict_content_schema(self) -> None:
        self.assertEqual(set(ARTIFACT_CONTENT_SCHEMAS), {
            "goal_contract", "design_manifest", "design_record", "architecture_decision",
            "behavior_spec", "eval_contract", "baseline_report", "review_decision",
            "release_decision", "change_decision", "incident_record",
        })
        for kind in sorted(set(ARTIFACT_CONTENT_SCHEMAS) - {"design_manifest"}):
            payload = self.artifact(kind, f"strict-{kind}")
            payload["content"] = {"x": 1}
            with self.subTest(kind=kind), self.assertRaisesRegex(AssuranceError, "unknown or missing"):
                self.kernel.register_artifact(
                    payload, actor="Company Platform Engineer", principal_id="principal-platform"
                )

    def test_rejects_unknown_fields_invalid_kind_and_wrong_owner_principal(self) -> None:
        payload = self.artifact("goal_contract", "goal-control-gate")
        payload["surprise"] = True
        with self.assertRaisesRegex(AssuranceError, "unknown or missing"):
            self.kernel.register_artifact(payload, actor="Company Platform Engineer", principal_id="principal-platform")

        payload = self.artifact("arbitrary", "bad-kind")
        with self.assertRaisesRegex(AssuranceError, "kind"):
            self.kernel.register_artifact(payload, actor="Company Platform Engineer", principal_id="principal-platform")

        payload = self.artifact("goal_contract", "goal-control-gate")
        with self.assertRaisesRegex(AssuranceError, "principal"):
            self.kernel.register_artifact(payload, actor="Company Platform Engineer", principal_id="somebody-else")

    def test_shadow_classification_does_not_block_existing_tasks(self) -> None:
        result = self.kernel.classify_change(
            actor="CEO", principal_id="principal-ceo", title="Add assurance tables",
            indicators={"persistent_schema": True, "authorization": False, "editorial_only": False},
        )
        self.assertEqual(result["risk_class"], "C2")
        self.assertEqual(result["mode"], "shadow")
        tasks_before = Store(self.config.db_path).fetch_one("SELECT COUNT(*) AS count FROM tasks")["count"]
        tasks_after = Store(self.config.db_path).fetch_one("SELECT COUNT(*) AS count FROM tasks")["count"]
        self.assertEqual(tasks_before, tasks_after)

    def test_design_manifest_requires_exact_cardinality_and_approved_dependencies(self) -> None:
        refs = []
        for kind, artifact_id in [
            ("goal_contract", "goal-1"),
            ("design_record", "design-1"),
            ("behavior_spec", "spec-1"),
            ("eval_contract", "eval-1"),
            ("baseline_report", "baseline-1"),
        ]:
            registered = self.kernel.register_artifact(
                self.artifact(kind, artifact_id), actor="Company Platform Engineer", principal_id="principal-platform"
            )
            approved = self.kernel.approve_artifact(
                artifact_id, 1, actor="CEO", principal_id="principal-ceo"
            )
            refs.append({"kind": kind, "artifact_id": artifact_id, "version": 1, "sha256": registered["content_sha256"]})
            self.assertEqual(approved["status"], "approved")

        manifest = self.artifact("design_manifest", "manifest-1")
        manifest["content"] = {"artifact_refs": refs, "edges": [
            {"from": "goal-1", "relation": "governs", "to": "design-1"},
            {"from": "design-1", "relation": "refines", "to": "spec-1"},
            {"from": "spec-1", "relation": "evaluated_by", "to": "eval-1"},
            {"from": "eval-1", "relation": "baselined_by", "to": "baseline-1"},
        ]}

        result = self.kernel.register_artifact(
            manifest, actor="Company Platform Engineer", principal_id="principal-platform"
        )
        self.assertEqual(result["kind"], "design_manifest")

        broken = self.artifact("design_manifest", "manifest-broken")
        broken["content"] = {"artifact_refs": refs[:-1], "edges": []}
        with self.assertRaisesRegex(AssuranceError, "baseline_report"):
            self.kernel.register_artifact(
                broken, actor="Company Platform Engineer", principal_id="principal-platform"
            )

    def test_assurance_init_is_additive_on_legacy_operational_state(self) -> None:
        store = Store(self.config.db_path)
        store.init()
        with store.connect() as conn:
            now = "2026-07-24T00:00:00+00:00"
            conn.execute(
                "INSERT OR REPLACE INTO roles(name,kind,mandate,status) VALUES ('Legacy Agent','agent','legacy','historical')"
            )
            task_id = conn.execute(
                """INSERT INTO tasks(created_at,updated_at,owner,title,domain,status,priority)
                   VALUES (?,?,?,?,?,'in_progress',?)""",
                (now, now, "Legacy Agent", "Must remain active", "company_platform", 5),
            ).lastrowid
            before = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("tasks", "execution_events", "approvals", "roles", "raci")
            }
            audit_before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        self.kernel.init()
        with store.connect_readonly() as conn:
            self.assertEqual(conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()[0], "in_progress")
            after = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("tasks", "execution_events", "approvals", "roles", "raci")
            }
            self.assertEqual(after, before)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0], audit_before)

    def test_principal_identity_is_registry_bound_and_cannot_be_spoofed(self) -> None:
        self.kernel.register_artifact(
            self.artifact("design_record", "design-spoof"),
            actor="Company Platform Engineer", principal_id="principal-platform",
        )
        os.environ.pop("ASSURANCE_CREDENTIAL_PRINCIPAL_CEO", None)
        with self.assertRaisesRegex(AssuranceError, "unauthenticated"):
            self.kernel.create_initiative(
                "spoofed-c3", "Spoofed C3", "control-plane-reliability", "C3",
                actor="CEO", principal_id="principal-ceo",
            )
        os.environ["ASSURANCE_CREDENTIAL_PRINCIPAL_CEO"] = "test-credential-principal-ceo"
        with self.assertRaisesRegex(AssuranceError, "mismatched assurance principal"):
            self.kernel.approve_artifact(
                "design-spoof", 1, actor="Company Platform Engineer", principal_id="claimed-other"
            )
        with self.assertRaisesRegex(AssuranceError, "mismatched assurance principal"):
            self.kernel.approve_artifact(
                "design-spoof", 1, actor="Company Platform Engineer", principal_id="principal-ceo"
            )

    def test_author_cannot_approve_own_artifact(self) -> None:
        self.kernel.register_artifact(
            self.artifact("design_record", "design-1"),
            actor="Company Platform Engineer", principal_id="principal-platform",
        )
        with self.assertRaisesRegex(AssuranceError, "authority|separation of duties"):
            self.kernel.approve_artifact(
                "design-1", 1, actor="Company Platform Engineer", principal_id="principal-platform"
            )

    def test_rejects_artifact_contract_drift_within_existing_initiative(self) -> None:
        self.kernel.create_initiative(
            "contract-1", "Contract consistency", "control-plane-reliability", "C2",
            actor="CEO", principal_id="principal-ceo",
        )
        artifact = self.artifact("goal_contract", "goal-contract")
        artifact["initiative_id"] = "contract-1"
        artifact["profile"] = "product-competitive"
        with self.assertRaisesRegex(AssuranceError, "initiative contract mismatch"):
            self.kernel.register_artifact(
                artifact, actor="Company Platform Engineer", principal_id="principal-platform"
            )

    def test_design_manifest_rejects_indirect_cycle(self) -> None:
        refs = []
        for kind in sorted({
            "goal_contract", "design_record", "behavior_spec", "eval_contract", "baseline_report"
        }):
            item = self.artifact(kind, f"cycle-{kind}")
            registered = self.kernel.register_artifact(
                item, actor="Company Platform Engineer", principal_id="principal-platform"
            )
            self.kernel.approve_artifact(
                item["artifact_id"], 1, actor="CEO", principal_id="principal-ceo"
            )
            refs.append({
                "kind": kind, "artifact_id": item["artifact_id"], "version": 1,
                "sha256": registered["content_sha256"],
            })
        manifest = self.artifact("design_manifest", "manifest-cycle")
        manifest["content"] = {
            "artifact_refs": refs,
            "edges": [
                {"from": "cycle-goal_contract", "relation": "governs", "to": "cycle-design_record"},
                {"from": "cycle-design_record", "relation": "refines", "to": "cycle-behavior_spec"},
                {"from": "cycle-behavior_spec", "relation": "constrains", "to": "cycle-goal_contract"},
            ],
        }
        with self.assertRaisesRegex(AssuranceError, "cycle"):
            self.kernel.register_artifact(
                manifest, actor="Company Platform Engineer", principal_id="principal-platform"
            )

    def test_lifecycle_rejects_illegal_transition_and_records_block_resume(self) -> None:
        self.kernel.create_initiative(
            "lifecycle-1", "Control gate", "control-plane-reliability", "C2",
            actor="CEO", principal_id="principal-ceo",
        )
        with self.assertRaisesRegex(AssuranceError, "illegal lifecycle transition"):
            self.kernel.transition("lifecycle-1", "implementation", actor="CEO", principal_id="principal-ceo")
        with self.assertRaisesRegex(AssuranceError, "requires passing G0"):
            self.kernel.transition("lifecycle-1", "goal_review", actor="CEO", principal_id="principal-ceo")
        goal = self.artifact("goal_contract", "goal-lifecycle")
        goal["initiative_id"] = "lifecycle-1"
        self.kernel.register_artifact(goal, actor="Company Platform Engineer", principal_id="principal-platform")
        self.kernel.approve_artifact("goal-lifecycle", 1, actor="CEO", principal_id="principal-ceo")
        self.kernel.record_gate(
            "lifecycle-1", "G0", "pass", ["goal-lifecycle:v1"],
            actor="CEO", principal_id="principal-ceo",
        )
        self.kernel.transition("lifecycle-1", "goal_review", actor="CEO", principal_id="principal-ceo")
        blocked = self.kernel.block(
            "lifecycle-1", "missing design evidence", "goal_review",
            actor="CEO", principal_id="principal-ceo",
        )
        self.assertEqual(blocked["status"], "blocked")
        resumed = self.kernel.resume("lifecycle-1", actor="CEO", principal_id="principal-ceo")
        self.assertEqual(resumed["status"], "goal_review")

    def test_g6_rejects_negative_review_and_non_release_decision(self) -> None:
        self.kernel.create_initiative(
            "release-guard", "Release guard", "control-plane-reliability", "C3",
            actor="CEO", principal_id="principal-ceo",
        )
        for kind, artifact_id, decision in [
            ("review_decision", "review-reject", "reject"),
            ("release_decision", "release-hold", "hold"),
        ]:
            artifact = self.artifact(kind, artifact_id)
            artifact["initiative_id"] = "release-guard"
            artifact["risk_class"] = "C3"
            artifact["content"]["decision"] = decision
            self.kernel.register_artifact(
                artifact, actor="Company Platform Engineer", principal_id="principal-platform"
            )
            self.kernel.approve_artifact(artifact_id, 1, actor="CEO", principal_id="principal-ceo")
        with self.assertRaisesRegex(AssuranceError, "every approved review"):
            self.kernel.record_gate(
                "release-guard", "G6", "pass", ["review-reject:v1", "release-hold:v1"],
                actor="CEO", principal_id="principal-ceo",
            )

    def test_g6_rejects_contradictory_approved_decisions_even_when_positive_decisions_exist(self) -> None:
        self.kernel.create_initiative(
            "contradictory-release", "Contradictory release", "control-plane-reliability", "C3",
            actor="CEO", principal_id="principal-ceo",
        )
        items = [
            ("review_decision", "review-negative", "reject"),
            ("review_decision", "review-positive", "approve"),
            ("release_decision", "release-negative", "hold"),
            ("release_decision", "release-positive", "production_release"),
        ]
        refs = []
        for kind, artifact_id, decision in items:
            artifact = self.artifact(kind, artifact_id)
            artifact["initiative_id"] = "contradictory-release"
            artifact["risk_class"] = "C3"
            artifact["content"]["decision"] = decision
            self.kernel.register_artifact(
                artifact, actor="Company Platform Engineer", principal_id="principal-platform"
            )
            self.kernel.approve_artifact(artifact_id, 1, actor="CEO", principal_id="principal-ceo")
            refs.append(f"{artifact_id}:v1")
        with self.assertRaisesRegex(AssuranceError, "every approved review"):
            self.kernel.record_gate(
                "contradictory-release", "G6", "pass", refs,
                actor="CEO", principal_id="principal-ceo",
            )

    def test_gate_decision_binds_artifact_set_and_rejects_author_as_independent_reviewer(self) -> None:
        self.kernel.create_initiative(
            "gate-1", "Gate pilot", "control-plane-reliability", "C2",
            actor="CEO", principal_id="principal-ceo",
        )
        artifact = self.artifact("goal_contract", "goal-gate")
        artifact["initiative_id"] = "gate-1"
        self.kernel.register_artifact(artifact, actor="Company Platform Engineer", principal_id="principal-platform")
        self.kernel.approve_artifact("goal-gate", 1, actor="CEO", principal_id="principal-ceo")
        with self.assertRaisesRegex(AssuranceError, "authority|separation of duties"):
            self.kernel.record_gate(
                "gate-1", "G0", "pass", ["goal-gate:v1"],
                actor="Company Platform Engineer", principal_id="principal-platform",
            )
        decision = self.kernel.record_gate(
            "gate-1", "G0", "pass", ["goal-gate:v1"],
            actor="CEO", principal_id="principal-ceo",
        )
        self.assertEqual(len(decision["artifact_set_sha256"]), 64)
        self.assertEqual(decision["mode"], "shadow")

    def test_integrity_and_stale_impact_fail_closed_without_touching_tasks(self) -> None:
        artifact = self.artifact("design_record", "design-integrity")
        self.kernel.register_artifact(artifact, actor="Company Platform Engineer", principal_id="principal-platform")
        clean = self.kernel.verify_integrity()
        self.assertEqual(clean["conflicts"], [])
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                "UPDATE assurance_artifacts SET content_json='{}' WHERE artifact_id='design-integrity'"
            )
        conflict = self.kernel.verify_integrity()
        self.assertEqual(conflict["status"], "integrity_conflict")
        self.assertEqual(conflict["conflicts"][0]["artifact_id"], "design-integrity")
        self.assertEqual(Store(self.config.db_path).fetch_one("SELECT COUNT(*) AS c FROM tasks")["c"], 2)

    def test_supersede_marks_dependent_artifacts_stale_in_shadow_mode(self) -> None:
        for kind, artifact_id in [("goal_contract", "goal-old"), ("design_record", "design-old")]:
            self.kernel.register_artifact(
                self.artifact(kind, artifact_id), actor="Company Platform Engineer", principal_id="principal-platform"
            )
            self.kernel.approve_artifact(artifact_id, 1, actor="CEO", principal_id="principal-ceo")
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                """INSERT INTO assurance_links(
                       initiative_id, from_artifact_id, relation, to_artifact_id, created_at
                   ) VALUES ('pilot-control-gate','goal-old','governs','design-old','2026-01-01T00:00:00+00:00')"""
            )
        result = self.kernel.supersede_artifact(
            "goal-old", 1, actor="CEO", principal_id="principal-ceo", reason="new goal evidence"
        )
        self.assertEqual(result["invalidated"], ["design-old:v1"])
        dependent = Store(self.config.db_path).fetch_one(
            "SELECT status FROM assurance_artifacts WHERE artifact_id='design-old'"
        )
        self.assertEqual(dependent["status"], "stale")


if __name__ == "__main__":
    unittest.main()
