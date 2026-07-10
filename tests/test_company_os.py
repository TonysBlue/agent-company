from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

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
        self.assertGreaterEqual(len(result["progressed"]), 1)
        status = self.osys.status()
        self.assertGreaterEqual(status["cycles"], 1)
        metrics = Store(self.config.db_path).fetch_all("SELECT * FROM metrics")
        self.assertGreaterEqual(len(metrics), 3)
        artifacts = list(self.config.artifacts_dir.glob("*.json"))
        self.assertGreaterEqual(len(artifacts), 1)

    def test_reserved_action_blocks_and_writes_chairman_inbox(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'CRO', 'Change price tier', 'gtm', 'open', 999)",
                (now, now),
            )
        result = self.osys.run_cycle()
        self.assertGreaterEqual(len(result["escalated"]), 1)
        inbox = self.osys.chairman_inbox()
        approval = next(item for item in inbox if item["summary"].endswith("Change price tier"))
        self.assertEqual(approval["action_type"], "pricing_change")
        inbox_file = Path(approval["inbox_file"])
        payload = json.loads(inbox_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["requested_by"], "CRO")

    def test_chairman_decision_reopens_approved_task(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'CRO', 'Change price tier', 'gtm', 'open', 999)",
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
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'CRO', 'Change price tier', 'gtm', 'open', 999)",
                (now, now),
            )
        self.osys.run_cycle()
        approval = self.osys.chairman_inbox()[0]
        self.osys.decide(approval["id"], "approve", "Approved for controlled internal continuation.")

        cycle = self.osys.run_cycle()

        self.assertNotIn(7, cycle["escalated"])
        self.assertIn(7, cycle["progressed"])
        duplicate = [
            item for item in self.osys.chairman_inbox()
            if item["summary"].endswith("Change price tier")
        ]
        self.assertEqual(duplicate, [])
        task = Store(self.config.db_path).fetch_one("SELECT status FROM tasks WHERE title='Change price tier'")
        self.assertEqual(task["status"], "done")

    def test_validate_passes(self) -> None:
        self.assertEqual(self.osys.validate(), [])

    def test_reserved_classifier_uses_word_boundaries(self) -> None:
        self.assertIsNone(classify_reserved_action("Design first ICP and offer backlog", self.config))
        self.assertEqual(classify_reserved_action("sign vendor agreement", self.config), "contract_signature")

    def test_cycle_replenishes_distinct_backlog_with_acceptance_criteria(self) -> None:
        self.osys.init()
        for _ in range(3):
            self.osys.run_cycle()
        store = Store(self.config.db_path)
        active = store.fetch_all(
            "SELECT title, acceptance_criteria FROM tasks WHERE status IN ('open', 'blocked')"
        )
        self.assertGreaterEqual(len(active), self.config.cycle_task_limit)
        self.assertTrue(any(row["acceptance_criteria"] for row in active))
        titles = store.fetch_all("SELECT title, COUNT(*) AS c FROM tasks GROUP BY title")
        self.assertTrue(all(row["c"] == 1 for row in titles))

    def test_backlog_continues_after_first_roadmap_batch(self) -> None:
        self.osys.init()
        for _ in range(12):
            self.osys.run_cycle()

        active = Store(self.config.db_path).fetch_all(
            "SELECT title, acceptance_criteria FROM tasks WHERE status IN ('open', 'blocked')"
        )
        self.assertGreaterEqual(len(active), self.config.cycle_task_limit)
        self.assertTrue(any(row["acceptance_criteria"] for row in active))
        self.assertTrue(any("iteration" in row["title"] for row in active))

    def test_cycle_seeds_internal_draft_experiment(self) -> None:
        self.osys.run_cycle()
        experiments = Store(self.config.db_path).fetch_all("SELECT * FROM experiments")
        self.assertEqual(len(experiments), 1)
        self.assertEqual(experiments[0]["status"], "draft")


if __name__ == "__main__":
    unittest.main()
