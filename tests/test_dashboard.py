from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_company.config import load_config
from agent_company.dashboard import DashboardApp, build_snapshot
from agent_company.db import Store


class DashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            """
[company]
product_name = Test PixWeave

[paths]
database = data/company.sqlite3
chairman_inbox = data/chairman/inbox
chairman_outbox = data/chairman/outbox
artifacts = data/artifacts
logs = logs
""".strip()
            + "\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        self.store = Store(self.config.db_path)
        self.store.init()
        self._seed_operating_data()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _seed_operating_data(self) -> None:
        now = "2026-07-11T01:02:03+00:00"
        with self.store.connect() as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM approvals")
            conn.execute("DELETE FROM cycles")
            conn.execute("DELETE FROM experiments")
            conn.execute("DELETE FROM audit_log")
            conn.execute(
                """INSERT INTO tasks(
                       id, created_at, updated_at, owner, title, domain, status,
                       priority, blocked_reason, result, acceptance_criteria
                   ) VALUES (1, ?, ?, 'CTO', 'Build dashboard', 'engineering',
                   'in_progress', 90, NULL, NULL, 'Tests and curl evidence pass.')""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO tasks(
                       id, created_at, updated_at, owner, title, domain, status,
                       priority, blocked_reason, result, acceptance_criteria
                   ) VALUES (2, ?, ?, 'CRO', 'Publish launch pricing', 'gtm',
                   'blocked', 80, 'Pending Chairman approval #1', NULL,
                   'Chairman decision recorded before external action.')""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO approvals(
                       id, created_at, requested_by, action_type, summary, status
                   ) VALUES (1, ?, 'CRO', 'pricing_change',
                   'Task 2 requires Chairman decision before continuing: Publish launch pricing',
                   'pending')""",
                (now,),
            )
            conn.execute(
                "INSERT INTO cycles(id, started_at, finished_at, summary) VALUES (1, ?, ?, ?)",
                (now, now, json.dumps({"processed": 2, "progressed": [1], "escalated": [2]})),
            )
            conn.execute(
                """INSERT INTO experiments(
                       id, created_at, owner, name, hypothesis, metric, status, result
                   ) VALUES (1, ?, 'CRO', 'Message test', 'ICP responds to control',
                   'rubric_score_difference', 'draft', NULL)""",
                (now,),
            )
            conn.execute(
                """INSERT INTO audit_log(
                       id, ts, actor, action, entity, entity_id, details
                   ) VALUES (1, ?, 'CEO', 'run_cycle', 'cycle', '1', ?)""",
                (now, json.dumps({"processed": 2})),
            )
        artifact = self.config.artifacts_dir / "build-evidence.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"schema_version":"test/v1"}\n', encoding="utf-8")

    def test_snapshot_uses_live_sources_and_no_fabricated_operations_metrics(self) -> None:
        snapshot = build_snapshot(self.config)

        self.assertEqual(snapshot["management"]["task_counts_by_status"]["blocked"], 1)
        self.assertEqual(snapshot["management"]["task_counts_by_owner"]["CTO"], 1)
        self.assertEqual(snapshot["management"]["human_dependencies"][0]["approval_id"], 1)
        self.assertEqual(snapshot["project"]["experiments"][0]["name"], "Message test")
        self.assertIn("git", snapshot["project"])
        self.assertEqual(snapshot["operations"]["launch_state"], "pre_launch")
        self.assertIsNone(snapshot["operations"]["fields"][0]["value"])
        self.assertEqual(snapshot["operations"]["fields"][0]["state"], "placeholder")
        sources = {source["id"] for source in snapshot["sources"]}
        self.assertIn("sqlite", sources)
        self.assertIn("git", sources)

    def test_dashboard_pages_are_separate_and_chinese_labeled(self) -> None:
        app = DashboardApp(self.config)

        management = app.render_path("/management")
        project = app.render_path("/project")
        operations = app.render_path("/operations")

        self.assertIn("公司日常管理", management.body)
        self.assertIn("任务状态", management.body)
        self.assertIn("产品 / 项目状态", project.body)
        self.assertIn("Git 版本", project.body)
        self.assertIn("产品运营", operations.body)
        self.assertIn("尚未上线", operations.body)
        self.assertNotEqual(management.body, project.body)
        self.assertNotEqual(project.body, operations.body)

    def test_json_endpoints_and_health(self) -> None:
        app = DashboardApp(self.config)

        health = app.render_path("/healthz")
        api = app.render_path("/api/status")

        self.assertEqual(health.content_type, "application/json; charset=utf-8")
        self.assertEqual(json.loads(health.body)["ok"], True)
        payload = json.loads(api.body)
        self.assertEqual(payload["product"], "Test PixWeave")
        self.assertIn("management", payload)
        self.assertIn("project", payload)
        self.assertIn("operations", payload)

    def test_missing_database_is_reported_without_creating_it(self) -> None:
        missing_config = load_config()
        missing_config.db_path.unlink()

        snapshot = build_snapshot(missing_config)

        self.assertFalse(missing_config.db_path.exists())
        self.assertEqual(snapshot["database"]["available"], False)
        self.assertEqual(snapshot["management"]["tasks"], [])


if __name__ == "__main__":
    unittest.main()
