from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.assurance import AssuranceError, AssuranceKernel
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
        self.kernel = AssuranceKernel(self.config)
        self.kernel.init()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def artifact(self, kind: str, artifact_id: str, version: int = 1) -> dict[str, object]:
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
            "content": {"summary": f"{kind} evidence", "non_goals": ["production deployment"]},
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

    def test_rejects_unknown_fields_invalid_kind_and_wrong_owner_principal(self) -> None:
        payload = self.artifact("goal_contract", "goal-control-gate")
        payload["surprise"] = True
        with self.assertRaisesRegex(AssuranceError, "unknown or missing"):
            self.kernel.register_artifact(payload, actor="Company Platform Engineer", principal_id="principal-platform")

        payload = self.artifact("arbitrary", "bad-kind")
        with self.assertRaisesRegex(AssuranceError, "kind"):
            self.kernel.register_artifact(payload, actor="Company Platform Engineer", principal_id="principal-platform")

        payload = self.artifact("goal_contract", "goal-control-gate")
        with self.assertRaisesRegex(AssuranceError, "owner principal"):
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

    def test_author_cannot_approve_own_artifact(self) -> None:
        self.kernel.register_artifact(
            self.artifact("design_record", "design-1"),
            actor="Company Platform Engineer", principal_id="principal-platform",
        )
        with self.assertRaisesRegex(AssuranceError, "separation of duties"):
            self.kernel.approve_artifact(
                "design-1", 1, actor="Company Platform Engineer", principal_id="principal-platform"
            )


if __name__ == "__main__":
    unittest.main()
