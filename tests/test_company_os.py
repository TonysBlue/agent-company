from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_company.cli import main as cli_main
from agent_company.config import load_config
from agent_company.db import Store
from agent_company.governance import DISCLAIMER, classify_reserved_action
from agent_company.ops import CompanyOS


class TempWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            """
[company]
product_name = Test PixWeave
cycle_task_limit = 6

[paths]
database = data/test.sqlite3
chairman_inbox = data/chairman/inbox
chairman_outbox = data/chairman/outbox
artifacts = data/artifacts
logs = logs

[backend]
name = local
codex_enabled = false

[governance]
reserved_actions = external_publish,external_spend,legal_commitment,contract_signature,production_deploy,data_export,pricing_change
""".strip()
            + "\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        self.osys = CompanyOS(self.config)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_init_creates_schema_and_roles(self) -> None:
        self.osys.init()
        store = Store(self.config.db_path)
        chairman = store.fetch_one("SELECT kind FROM roles WHERE name='Chairman'")
        self.assertEqual(chairman["kind"], "human")
        self.assertTrue(self.config.chairman_inbox.exists())
        self.assertIn("legal autonomy", DISCLAIMER)

    def test_repeated_init_does_not_pollute_audit_log(self) -> None:
        self.osys.init()
        self.osys.status()
        self.osys.chairman_inbox()

        rows = Store(self.config.db_path).fetch_all(
            "SELECT * FROM audit_log WHERE actor='system' AND action='init'"
        )
        self.assertEqual(len(rows), 1)

    def test_run_cycle_progresses_safe_work_and_records_metrics(self) -> None:
        self.osys.init()
        result = self.osys.run_cycle()
        self.assertGreater(result["processed"], 0)
        self.assertEqual(result["progressed"], [])
        status = self.osys.status()
        self.assertGreaterEqual(status["cycles"], 1)
        metrics = Store(self.config.db_path).fetch_all("SELECT * FROM metrics")
        self.assertGreaterEqual(len(metrics), 3)
        active = Store(self.config.db_path).fetch_all("SELECT * FROM tasks WHERE status='in_progress'")
        self.assertEqual(active, [])
        ready = Store(self.config.db_path).fetch_all(
            "SELECT * FROM audit_log WHERE action='task_ready_for_executor'"
        )
        self.assertGreaterEqual(len(ready), 1)

    def test_reserved_action_blocks_and_writes_chairman_inbox(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'Customer & Revenue', 'Change price tier', 'gtm', 'open', 999)",
                (now, now),
            )
        result = self.osys.run_cycle()
        self.assertGreaterEqual(len(result["escalated"]), 1)
        inbox = self.osys.chairman_inbox()
        approval = next(item for item in inbox if item["summary"].endswith("Change price tier"))
        self.assertEqual(approval["action_type"], "pricing_change")
        inbox_file = Path(approval["inbox_file"])
        payload = json.loads(inbox_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["requested_by"], "Customer & Revenue")

    def test_chairman_decision_reopens_approved_task(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'Customer & Revenue', 'Change price tier', 'gtm', 'open', 999)",
                (now, now),
            )
        self.osys.run_cycle()
        approval = self.osys.chairman_inbox()[0]
        decision = self.osys.decide(approval["id"], "approve", "Approved for controlled internal continuation.")
        self.assertEqual(decision["decided_by"], "Chairman")
        task = Store(self.config.db_path).fetch_one("SELECT status FROM tasks WHERE title='Change price tier'")
        self.assertEqual(task["status"], "open")
        self.assertTrue((self.config.chairman_outbox / f"decision-{approval['id']}.json").exists())

    def test_chairman_approval_is_consumed_without_duplicate_escalation(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'Customer & Revenue', 'Change price tier', 'gtm', 'open', 999)",
                (now, now),
            )
        self.osys.run_cycle()
        approval = self.osys.chairman_inbox()[0]
        self.osys.decide(approval["id"], "approve", "Approved for controlled internal continuation.")

        cycle = self.osys.run_cycle()
        task_id = Store(self.config.db_path).fetch_one(
            "SELECT id FROM tasks WHERE title='Change price tier'"
        )["id"]

        self.assertNotIn(task_id, cycle["escalated"])
        self.assertNotIn(task_id, cycle["progressed"])
        duplicate = [
            item for item in self.osys.chairman_inbox()
            if item["summary"].endswith("Change price tier")
        ]
        self.assertEqual(duplicate, [])
        task = Store(self.config.db_path).fetch_one("SELECT status FROM tasks WHERE title='Change price tier'")
        self.assertEqual(task["status"], "open")

    def test_status_marks_unclaimed_open_work_as_stalled(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DELETE FROM task_executions")
            conn.execute("UPDATE tasks SET status='done'")
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                """INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority)
                   VALUES (?, ?, 'Customer & Revenue', 'Unclaimed customer work', 'customer', 'open', 90)""",
                (now, now),
            )

        status = self.osys.status()

        self.assertEqual(status["business_progress"], "stalled")
        self.assertEqual(status["unclaimed_tasks"], 1)

    def test_task_completion_requires_existing_evidence(self) -> None:
        self.osys.init()
        task = Store(self.config.db_path).fetch_one("SELECT * FROM tasks WHERE status='open' ORDER BY priority DESC, id")
        task_id = task["id"]
        self.osys.claim_task(task_id, task["owner"], executor_id="test-executor", backend="local")
        with self.assertRaisesRegex(ValueError, "evidence files do not exist"):
            self.osys.complete_task(task_id, task["owner"], "done", [self.root / "missing.md"])

        evidence = self.root / "evidence.md"
        evidence.write_text("reviewable result\n", encoding="utf-8")
        result = self.osys.complete_task(task_id, task["owner"], "Acceptance criteria verified.", [evidence])
        self.assertEqual(result["status"], "done")
        stored = Store(self.config.db_path).fetch_one("SELECT status,result FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(stored["status"], "done")
        self.assertEqual(json.loads(stored["result"])["evidence"], [str(evidence.resolve())])

    def test_ceo_creates_audited_reviewed_task(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DELETE FROM tasks")
        created = self.osys.create_task(
            "CEO", "Product Engineer", "Implement bounded capability", "engineering", 80,
            "A runnable command and regression test pass.",
        )
        self.assertEqual(created["status"], "open")
        task = Store(self.config.db_path).fetch_one("SELECT * FROM tasks WHERE id=?", (created["task_id"],))
        self.assertEqual(task["acceptance_criteria"], "A runnable command and regression test pass.")
        audit = Store(self.config.db_path).fetch_one(
            "SELECT * FROM audit_log WHERE action='create_task' AND entity_id=?", (str(created["task_id"]),)
        )
        self.assertIsNotNone(audit)
        with self.assertRaisesRegex(ValueError, "only CEO"):
            self.osys.create_task("Product Engineer", "Product Engineer", "Unauthorized", "engineering", 50, "Must fail.")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.osys.create_task(
                "CEO", "Product Engineer", "Implement bounded capability", "engineering", 80, "Duplicate must fail."
            )

    def test_task_cancel_closes_work_without_claiming_completion(self) -> None:
        self.osys.init()
        task_id = Store(self.config.db_path).fetch_one(
            "SELECT id FROM tasks WHERE status='open' ORDER BY priority DESC, id"
        )["id"]
        result = self.osys.cancel_task(task_id, "CEO", "Superseded by reviewed task 42.")
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["completed"])
        stored = Store(self.config.db_path).fetch_one("SELECT status,result FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(stored["status"], "cancelled")
        self.assertFalse(json.loads(stored["result"])["completed"])

    def test_task_cancel_rejects_unrelated_actor(self) -> None:
        self.osys.init()
        task_id = Store(self.config.db_path).fetch_one(
            "SELECT id FROM tasks WHERE status='open' ORDER BY priority DESC, id"
        )["id"]
        with self.assertRaisesRegex(ValueError, "may only be cancelled"):
            self.osys.cancel_task(task_id, "CFO", "Not my task.")

    def test_validate_passes(self) -> None:
        self.osys.init()
        before = self.config.db_path.read_bytes()
        self.assertEqual(self.osys.validate(), [])
        self.assertEqual(self.config.db_path.read_bytes(), before)

    def test_reserved_classifier_uses_word_boundaries(self) -> None:
        self.assertIsNone(classify_reserved_action("Design first ICP and offer backlog", self.config))
        self.assertEqual(classify_reserved_action("sign vendor agreement", self.config), "contract_signature")

    def test_cycle_does_not_replenish_backlog_after_reviewed_work_completes(self) -> None:
        self.osys.init()
        cycle = self.osys.run_cycle()
        for task in Store(self.config.db_path).fetch_all("SELECT * FROM tasks WHERE status='open'"):
            task_id = task["id"]
            self.osys.claim_task(task_id, task["owner"], executor_id=f"test-executor-{task_id}", backend="local")
            evidence = self.root / f"replenish-{task_id}.md"
            evidence.write_text("verified\n", encoding="utf-8")
            self.osys.complete_task(task_id, task["owner"], "Verified bounded result.", [evidence])
        store = Store(self.config.db_path)
        active = store.fetch_all(
            "SELECT title, acceptance_criteria FROM tasks WHERE status IN ('open', 'in_progress', 'blocked')"
        )
        self.assertEqual(active, [])
        titles = store.fetch_all("SELECT title, COUNT(*) AS c FROM tasks GROUP BY title")
        self.assertTrue(all(row["c"] == 1 for row in titles))

    def test_ceo_does_not_auto_create_strategic_phase_before_backlog_exhaustion(self) -> None:
        self.osys.init()
        store = Store(self.config.db_path)
        with store.connect() as conn:
            conn.execute("UPDATE tasks SET status='done', result='{}'")
            now = "2026-07-13T00:00:00+00:00"
            conn.execute(
                """INSERT INTO tasks(created_at, updated_at, owner, title, domain, status,
                                      priority, acceptance_criteria)
                   VALUES (?, ?, 'Product Engineer', 'Last current-phase task', 'product', 'open', 99,
                           'One bounded current-phase result is verified.')""",
                (now, now),
            )

        cycle = self.osys.run_cycle()

        phases = store.fetch_all("SELECT * FROM strategic_phases ORDER BY id")
        self.assertEqual(phases, [])
        self.assertIsNone(cycle["planned_phase_id"])
        audits = store.fetch_all("SELECT action FROM audit_log WHERE entity='strategic_phase'")
        self.assertNotIn("activate_strategic_phase", [row["action"] for row in audits])

    def test_status_distinguishes_business_stall_from_technical_health(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET status='done', result='{}'")
            conn.execute("DELETE FROM strategic_phases")
            for index in range(3):
                conn.execute(
                    "INSERT INTO cycles(started_at, finished_at, summary) VALUES (?, ?, ?)",
                    (f"2026-07-13T00:0{index}:00+00:00", f"2026-07-13T00:0{index}:01+00:00", '{"processed": 0, "progressed": []}'),
                )
        status = self.osys.status()
        self.assertEqual(status["technical_health"], "healthy")
        self.assertEqual(status["business_progress"], "stalled")
        self.assertGreaterEqual(status["consecutive_empty_cycles"], 3)

    def test_cycle_does_not_seed_activity_experiment(self) -> None:
        self.osys.run_cycle()
        experiments = Store(self.config.db_path).fetch_all("SELECT * FROM experiments")
        self.assertEqual(experiments, [])


if __name__ == "__main__":
    unittest.main()
