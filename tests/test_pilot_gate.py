from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_company.assurance import AssuranceKernel
from agent_company.config import load_config
from agent_company.db import Store
from agent_company.pilot_gate import PilotGate


class PilotGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[paths]\ndatabase=data/company.sqlite3\nartifacts=data/artifacts\nlogs=logs\n",
            encoding="utf-8",
        )
        import os
        os.chdir(self.root)
        self.config = load_config()
        Store(self.config.db_path).init()
        AssuranceKernel(self.config).init()
        self.gate = PilotGate(self.config)
        self.gate.init()

    def tearDown(self) -> None:
        import os
        os.chdir(self.old)
        self.tmp.cleanup()

    def task(self, task_id: int) -> dict[str, object]:
        return {"id": task_id, "status": "open", "owner": "Company Platform Engineer", "domain": "platform"}

    def test_unbound_and_nonpilot_tasks_are_unchanged(self) -> None:
        self.assertEqual(self.gate.dispatch_decision(self.task(1)), {"allowed": True, "reason": "unbound"})
        self.gate.bind(2, "other-initiative", pilot=False)
        self.assertEqual(self.gate.dispatch_decision(self.task(2)), {"allowed": True, "reason": "non-pilot"})

    def test_bound_pilot_fails_closed_before_g4_and_allows_exact_approved_hash(self) -> None:
        initiative = "pilot-c2-approved-for-build"
        self.gate.bind(3, initiative, pilot=True)
        denied = self.gate.dispatch_decision(self.task(3))
        self.assertFalse(denied["allowed"])
        self.assertIn("G4", denied["reason"])
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                """INSERT INTO assurance_initiatives(
                       initiative_id,profile,risk_class,title,owner_principal,status,mode,created_at,updated_at
                   ) VALUES (?, 'control-plane-reliability','C2','pilot','principal-ceo',
                             'approved_for_build','pilot','2026-07-24','2026-07-24')""",
                (initiative,),
            )
            conn.execute(
                """INSERT INTO assurance_gate_decisions(
                       initiative_id,gate,decision,actor,principal_id,artifact_set_sha256,
                       conditions_json,expires_at,created_at
                   ) VALUES (?,'G4','pass','CEO','principal-ceo',?,'[]',NULL,'2026-07-24')""",
                (initiative, "a" * 64),
            )
        self.gate.bind(3, initiative, pilot=True, artifact_set_sha256="a" * 64)
        self.assertTrue(self.gate.dispatch_decision(self.task(3))["allowed"])
        self.gate.bind(3, initiative, pilot=True, artifact_set_sha256="b" * 64)
        self.assertFalse(self.gate.dispatch_decision(self.task(3))["allowed"])

    def test_kill_switch_bypasses_dispatch_only_and_is_audited(self) -> None:
        self.gate.bind(4, "pilot-c2-approved-for-build", pilot=True)
        self.gate.set_kill_switch(True, actor="CEO", reason="safe rollback")
        decision = self.gate.dispatch_decision(self.task(4))
        self.assertEqual(decision, {"allowed": True, "reason": "pilot enforcement killed"})
        with Store(self.config.db_path).connect_readonly() as conn:
            audit = conn.execute(
                "SELECT action,details FROM audit_log WHERE action='set_pilot_kill_switch' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(audit)
        self.assertNotEqual(self.task(4)["status"], "done")


if __name__ == "__main__":
    unittest.main()
