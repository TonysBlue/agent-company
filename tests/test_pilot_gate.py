from __future__ import annotations

import tempfile
import unittest
import os
import hashlib
from pathlib import Path

from agent_company.assurance import AssuranceKernel
from agent_company.config import load_config
from agent_company.db import Store
from agent_company.ops import CompanyOS
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
        self.osys = CompanyOS(self.config)
        credential = "pilot-ceo-credential"
        os.environ["ASSURANCE_CREDENTIAL_PRINCIPAL_CEO"] = credential
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                """INSERT INTO assurance_principals(
                       principal_id,actor,authority,credential_sha256,status,created_at
                   ) VALUES ('principal-ceo','CEO','executive',?,'active','2026-07-24')""",
                (hashlib.sha256(credential.encode()).hexdigest(),),
            )
        with self.osys.store.connect() as conn:
            conn.execute("DELETE FROM tasks")
        self.task_id = int(self.osys.create_task(
            "CEO", "Company Platform Engineer", "Direct claim pilot", "platform", 99,
            "Must not dispatch before G4.",
        )["task_id"])
        self.gate = PilotGate(self.config)
        self.gate.init()
        self.gate.bind(
            self.task_id, "pilot-c2-approved-for-build", pilot=True,
            actor="CEO", principal_id="principal-ceo",
        )
        with Store(self.config.db_path).connect() as conn:
            conn.commit()

    def tearDown(self) -> None:
        import os
        os.chdir(self.old)
        self.tmp.cleanup()
        os.environ.pop("ASSURANCE_CREDENTIAL_PRINCIPAL_CEO", None)

    def create_open_task(self, task_id: int) -> None:
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO tasks(
                       id,title,owner,domain,priority,status,acceptance_criteria,created_at,updated_at
                   ) VALUES (?,?,'Company Platform Engineer','platform',1,'open','test','2026-07-24','2026-07-24')""",
                (task_id, f"pilot-{task_id}"),
            )

    def task(self, task_id: int) -> dict[str, object]:
        return {"id": task_id, "status": "open", "owner": "Company Platform Engineer", "domain": "platform"}

    def test_direct_claim_cannot_bypass_pilot_gate(self) -> None:
        with self.assertRaisesRegex(ValueError, "blocked by assurance pilot gate"):
            self.osys.claim_task(
                self.task_id, "Company Platform Engineer", executor_id="direct-bypass",
                backend="local",
            )
        task = self.osys.store.fetch_one("SELECT status FROM tasks WHERE id=?", (self.task_id,))
        self.assertEqual(task["status"], "open")

    def test_unbound_and_nonpilot_tasks_are_unchanged(self) -> None:
        self.assertEqual(self.gate.dispatch_decision(self.task(1)), {"allowed": True, "reason": "unbound"})
        self.create_open_task(2)
        self.gate.bind(2, "other-initiative", pilot=False, actor="CEO", principal_id="principal-ceo")
        self.assertEqual(self.gate.dispatch_decision(self.task(2)), {"allowed": True, "reason": "non-pilot"})

    def test_bound_pilot_fails_closed_before_g4_and_allows_exact_approved_hash(self) -> None:
        initiative = "pilot-c2-approved-for-build"
        self.gate.bind(3, initiative, pilot=True, actor="CEO", principal_id="principal-ceo")
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
        # The fixture has no approved artifacts, so bind the exact current empty-set hash.
        with Store(self.config.db_path).connect_readonly() as conn:
            empty_hash = AssuranceKernel(self.config)._initiative_artifact_set_sha256(conn, initiative)
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                "UPDATE assurance_gate_decisions SET artifact_set_sha256=? WHERE initiative_id=?",
                (empty_hash, initiative),
            )
        self.gate.bind(3, initiative, pilot=True, artifact_set_sha256=empty_hash, actor="CEO", principal_id="principal-ceo")
        self.assertTrue(self.gate.dispatch_decision(self.task(3))["allowed"])
        self.gate.bind(3, initiative, pilot=True, artifact_set_sha256="b" * 64, actor="CEO", principal_id="principal-ceo")
        self.assertFalse(self.gate.dispatch_decision(self.task(3))["allowed"])

    def test_kill_switch_bypasses_dispatch_only_and_is_audited(self) -> None:
        self.create_open_task(4)
        self.gate.bind(4, "pilot-c2-approved-for-build", pilot=True, actor="CEO", principal_id="principal-ceo")
        self.gate.set_kill_switch(True, actor="CEO", principal_id="principal-ceo", reason="safe rollback")
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
