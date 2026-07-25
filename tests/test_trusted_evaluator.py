from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.assurance import AssuranceKernel
from agent_company.config import load_config
from agent_company.db import Store
from agent_company.trusted_evaluator import EvaluationError, TrustedEvaluator


class TrustedEvaluatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[paths]\ndatabase=data/company.sqlite3\nartifacts=data/artifacts\nlogs=logs\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        Store(self.config.db_path).init()
        kernel = AssuranceKernel(self.config)
        kernel.init()
        credential = "trusted-evaluator-credential"
        os.environ["ASSURANCE_CREDENTIAL_PRINCIPAL_EVALUATOR"] = credential
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                """INSERT INTO assurance_principals(
                       principal_id,actor,authority,credential_sha256,status,created_at
                   ) VALUES (?,?,?,?, 'active','2026-07-24T00:00:00+00:00')""",
                ("principal-evaluator", "Trusted Evaluator", "operator",
                 hashlib.sha256(credential.encode()).hexdigest()),
            )
        self.evaluator = TrustedEvaluator(self.config)
        self.evaluator.init()
        with Store(self.config.db_path).connect() as conn:
            for initiative_id in ("pilot", "pilot-2"):
                conn.execute(
                    """INSERT INTO assurance_initiatives(
                           initiative_id,profile,risk_class,title,owner_principal,status,mode,created_at,updated_at
                       ) VALUES (?,'product-competitive','C2','eval','principal-ceo','implementation','pilot','2026-07-24','2026-07-24')""",
                    (initiative_id,),
                )
        content_dir = self.root / "data" / "trusted-eval-content"
        content_dir.mkdir(parents=True, exist_ok=True)
        for identifier in ("candidate-1", "holdout-1", "candidate-2", "dataset-2", "grader-2", "environment-2", "candidate-1", "dataset-1", "grader-1", "environment-1"):
            data = identifier.encode()
            (content_dir / hashlib.sha256(data).hexdigest()).write_bytes(data)

    def tearDown(self) -> None:
        os.environ.pop("ASSURANCE_CREDENTIAL_PRINCIPAL_EVALUATOR", None)
        os.chdir(self.old)
        self.tmp.cleanup()

    @staticmethod
    def manifest(kind: str, identifier: str, protected: bool = False) -> dict[str, object]:
        payload = {
            "schema_version": f"trusted-eval-{kind}/v1",
            "id": identifier,
            "content_sha256": hashlib.sha256(identifier.encode()).hexdigest(),
            "protected": protected,
        }
        return payload

    def test_registers_immutable_content_addressed_inputs_and_hides_holdout(self) -> None:
        candidate = self.evaluator.register_manifest("candidate", self.manifest("candidate", "candidate-1"), actor="Trusted Evaluator", principal_id="principal-evaluator")
        holdout = self.evaluator.register_manifest("dataset", self.manifest("dataset", "holdout-1", True), actor="Trusted Evaluator", principal_id="principal-evaluator")
        self.assertEqual(len(candidate["manifest_sha256"]), 64)
        self.assertNotIn("content_sha256", self.evaluator.list_manifests(actor="Company Platform Engineer", principal_id="principal-platform")[0] if self.evaluator.list_manifests(actor="Company Platform Engineer", principal_id="principal-platform") else {})
        self.assertTrue(holdout["protected"])
        with self.assertRaisesRegex(EvaluationError, "immutable"):
            changed = self.manifest("candidate", "candidate-1")
            changed["content_sha256"] = "0" * 64
            self.evaluator.register_manifest("candidate", changed, actor="Trusted Evaluator", principal_id="principal-evaluator")

    def test_attempt_budget_retains_failed_abandoned_and_completed_runs(self) -> None:
        refs = {}
        for kind in ("candidate", "dataset", "grader", "environment"):
            refs[kind] = self.evaluator.register_manifest(
                kind, self.manifest(kind, f"{kind}-1", protected=kind == "dataset"),
                actor="Trusted Evaluator", principal_id="principal-evaluator",
            )["manifest_sha256"]
        for status in ("failed", "abandoned", "completed"):
            run = self.evaluator.record_run(
                initiative_id="pilot", refs=refs, seed=7, status=status,
                evidence_ref=f"evidence/{status}.json", max_attempts=3,
                actor="Trusted Evaluator", principal_id="principal-evaluator",
            )
            self.assertEqual(run["attempt"], {"failed": 1, "abandoned": 2, "completed": 3}[status])
        with self.assertRaisesRegex(EvaluationError, "attempt budget"):
            self.evaluator.record_run(
                initiative_id="pilot", refs=refs, seed=8, status="completed",
                evidence_ref="evidence/fourth.json", max_attempts=3,
                actor="Trusted Evaluator", principal_id="principal-evaluator",
            )
        with self.assertRaisesRegex(EvaluationError, "immutable attempt budget"):
            self.evaluator.record_run(
                initiative_id="pilot", refs=refs, seed=8, status="completed",
                evidence_ref="evidence/reset.json", max_attempts=2,
                actor="Trusted Evaluator", principal_id="principal-evaluator",
            )
        self.assertEqual(len(self.evaluator.list_runs("pilot", actor="Trusted Evaluator", principal_id="principal-evaluator")), 3)

    def test_contamination_quarantines_runs_and_only_evaluator_can_execute(self) -> None:
        refs = {}
        for kind in ("candidate", "dataset", "grader", "environment"):
            refs[kind] = self.evaluator.register_manifest(
                kind, self.manifest(kind, f"{kind}-2", protected=kind == "dataset"),
                actor="Trusted Evaluator", principal_id="principal-evaluator",
            )["manifest_sha256"]
        with self.assertRaisesRegex(Exception, "principal"):
            self.evaluator.record_run(
                initiative_id="pilot-2", refs=refs, seed=1, status="completed",
                evidence_ref="evidence/no.json", max_attempts=3,
                actor="Company Platform Engineer", principal_id="principal-platform",
            )
        self.evaluator.record_run(
            initiative_id="pilot-2", refs=refs, seed=1, status="completed",
            evidence_ref="evidence/ok.json", max_attempts=3,
            actor="Trusted Evaluator", principal_id="principal-evaluator",
        )
        self.evaluator.quarantine("pilot-2", "holdout canary exposed", actor="Trusted Evaluator", principal_id="principal-evaluator")
        runs = self.evaluator.list_runs(
            "pilot-2", actor="Trusted Evaluator", principal_id="principal-evaluator",
        )
        self.assertEqual(runs[0]["status"], "completed")
        self.assertTrue(runs[0]["quarantined"])
        with self.assertRaisesRegex(Exception, "principal"):
            self.evaluator.list_runs(
                "pilot-2", actor="Company Platform Engineer", principal_id="principal-platform",
            )
        with self.assertRaisesRegex(EvaluationError, "quarantined"):
            self.evaluator.record_run(
                initiative_id="pilot-2", refs=refs, seed=2, status="completed",
                evidence_ref="evidence/blocked.json", max_attempts=3,
                actor="Trusted Evaluator", principal_id="principal-evaluator",
            )


if __name__ == "__main__":
    unittest.main()
