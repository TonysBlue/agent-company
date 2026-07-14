from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_company.cli import main as cli_main
from agent_company.config import load_config
from agent_company.dashboard import DashboardApp, build_snapshot
from agent_company.db import Store
from agent_company.ops import CompanyOS


class TaskExecutionContinuityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            """
[company]
product_name = Test PixWeave
cycle_task_limit = 2

[paths]
database = data/test.sqlite3
chairman_inbox = data/chairman/inbox
chairman_outbox = data/chairman/outbox
artifacts = data/artifacts
logs = logs

[backend]
name = local
codex_enabled = true
""".strip()
            + "\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        self.osys = CompanyOS(self.config)
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DELETE FROM tasks")

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _create_task(self, owner: str = "Product Engineer") -> int:
        return self.osys.create_task(
            "CEO",
            owner,
            f"Continuity task {owner}",
            "engineering" if owner == "Product Engineer" else "gtm",
            90,
            "Execution state is durable and recoverable.",
        )["task_id"]

    def test_migration_adds_durable_execution_state_table_without_losing_tasks(self) -> None:
        task_id = self._create_task()

        self.osys.init()

        tables = {row["name"] for row in Store(self.config.db_path).fetch_all("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("task_executions", tables)
        stored = Store(self.config.db_path).fetch_one("SELECT title FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(stored["title"], "Continuity task Product Engineer")

    def test_claim_is_atomic_and_records_runtime_identity(self) -> None:
        task_id = self._create_task()

        first = self.osys.claim_task(
            task_id,
            "Product Engineer",
            executor_id="exec-1",
            backend="codex",
            process_id=1234,
            process_started_at="2026-07-11T00:00:00+00:00",
            session_ref="codex-session-1",
            evidence_paths=[self.root / "evidence"],
            log_paths=[self.root / "logs" / "exec.log"],
        )

        self.assertEqual(first["status"], "in_progress")
        self.assertEqual(first["executor_id"], "exec-1")
        with self.assertRaisesRegex(ValueError, "already claimed"):
            self.osys.claim_task(task_id, "Product Engineer", executor_id="exec-2", backend="local")
        execution = self.osys.inspect_execution(task_id)["execution"]
        self.assertEqual(execution["backend"], "codex")
        self.assertEqual(execution["process_id"], 1234)
        self.assertEqual(execution["process_started_at"], "2026-07-11T00:00:00+00:00")
        audits = Store(self.config.db_path).fetch_all("SELECT action FROM audit_log WHERE entity='task_execution' ORDER BY id")
        self.assertIn("claim_task_execution", [row["action"] for row in audits])

    def test_heartbeat_checkpoint_and_failure_are_audited(self) -> None:
        task_id = self._create_task()
        self.osys.claim_task(task_id, "Product Engineer", executor_id="exec-1", backend="local", lease_seconds=60)

        heartbeat = self.osys.heartbeat_task(task_id, "exec-1", lease_seconds=120)
        checkpoint = self.osys.checkpoint_task(task_id, "exec-1", "tests passing", "write docs")
        failed = self.osys.fail_task(task_id, "exec-1", "unit test failed", recoverable=True)

        self.assertEqual(heartbeat["recovery_status"], "running")
        self.assertEqual(checkpoint["checkpoint"], "tests passing")
        self.assertEqual(checkpoint["next_action"], "write docs")
        self.assertEqual(failed["recovery_status"], "failed")
        stored = self.osys.inspect_execution(task_id)["execution"]
        self.assertEqual(stored["last_error"], "unit test failed")
        actions = [
            row["action"]
            for row in Store(self.config.db_path).fetch_all("SELECT action FROM audit_log WHERE entity='task_execution'")
        ]
        self.assertIn("heartbeat_task_execution", actions)
        self.assertIn("checkpoint_task_execution", actions)
        self.assertIn("fail_task_execution", actions)

    def test_run_cycle_recovers_stale_lease_before_dispatching_new_work(self) -> None:
        stale_task = self._create_task("Product Engineer")
        fresh_task = self._create_task("Customer & Revenue")
        self.osys.claim_task(stale_task, "Product Engineer", executor_id="stale-exec", backend="local")
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                "UPDATE task_executions SET heartbeat_at=?, lease_expires_at=?, recovery_status=? WHERE task_id=?",
                (
                    "2026-07-10T00:00:00+00:00",
                    "2026-07-10T00:01:00+00:00",
                    "running",
                    stale_task,
                ),
            )

        cycle = self.osys.run_cycle()

        self.assertIn(stale_task, cycle["recovered"])
        self.assertNotIn(fresh_task, cycle["progressed"])
        stale = Store(self.config.db_path).fetch_one("SELECT status FROM tasks WHERE id=?", (stale_task,))
        self.assertEqual(stale["status"], "open")
        execution = self.osys.inspect_execution(stale_task)["execution"]
        self.assertEqual(execution["attempt_count"], 1)
        self.assertEqual(execution["recovery_status"], "requeued")

    def test_retry_exhaustion_blocks_instead_of_looping(self) -> None:
        task_id = self._create_task()
        self.osys.claim_task(task_id, "Product Engineer", executor_id="exec-1", backend="local", max_attempts=1)
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                "UPDATE task_executions SET lease_expires_at=?, recovery_status=? WHERE task_id=?",
                ("2026-07-10T00:01:00+00:00", "running", task_id),
            )

        result = self.osys.recover_task(task_id, "CEO", reason="lease expired")

        self.assertEqual(result["status"], "blocked")
        task = Store(self.config.db_path).fetch_one("SELECT status, blocked_reason FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(task["status"], "blocked")
        self.assertIn("retry attempts exhausted", task["blocked_reason"])
        execution = self.osys.inspect_execution(task_id)["execution"]
        self.assertEqual(execution["recovery_status"], "exhausted")

    def test_run_cycle_renews_valid_in_progress_leases_without_new_dispatch(self) -> None:
        task_id = self._create_task()
        other_task = self._create_task("Customer & Revenue")
        with patch("agent_company.ops._process_start_identity", return_value="current-start"):
            self.osys.claim_task(
                task_id,
                "Product Engineer",
                executor_id="exec-1",
                backend="local",
                process_id=os.getpid(),
                process_started_at="current-start",
                lease_seconds=60,
            )
            before = self.osys.inspect_execution(task_id)["execution"]["lease_expires_at"]

            cycle = self.osys.run_cycle()

            after = self.osys.inspect_execution(task_id)["execution"]["lease_expires_at"]
        self.assertGreater(after, before)
        self.assertEqual(cycle["recovered"], [])
        self.assertIn(other_task, cycle["progressed"])
        audit = Store(self.config.db_path).fetch_one(
            "SELECT * FROM audit_log WHERE action='renew_task_execution' AND entity_id=?",
            (str(task_id),),
        )
        self.assertIsNotNone(audit)

    def test_run_cycle_dispatch_creates_durable_execution_state(self) -> None:
        task_id = self._create_task()

        cycle = self.osys.run_cycle()

        self.assertIn(task_id, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertIsNotNone(inspection["execution"])
        self.assertEqual(inspection["execution"]["recovery_status"], "running")
        self.assertEqual(inspection["execution"]["attempt_count"], 0)

    def test_run_cycle_recovers_fresh_lease_when_recorded_pid_identity_mismatches(self) -> None:
        task_id = self._create_task()
        other_task = self._create_task("Customer & Revenue")
        self.osys.claim_task(
            task_id,
            "Product Engineer",
            executor_id="exec-1",
            backend="local",
            process_id=os.getpid(),
            process_started_at="different-start-identity",
            lease_seconds=600,
        )

        cycle = self.osys.run_cycle()

        self.assertIn(task_id, cycle["recovered"])
        self.assertNotIn(other_task, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "open")
        self.assertEqual(inspection["execution"]["recovery_status"], "requeued")
        self.assertEqual(inspection["execution"]["attempt_count"], 1)

    def test_run_cycle_recovers_fresh_local_execution_without_recorded_pid(self) -> None:
        task_id = self._create_task()
        other_task = self._create_task("Customer & Revenue")
        self.osys.claim_task(task_id, "Product Engineer", executor_id="exec-1", backend="local", lease_seconds=600)

        cycle = self.osys.run_cycle()

        self.assertIn(task_id, cycle["recovered"])
        self.assertNotIn(other_task, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "open")
        self.assertEqual(inspection["execution"]["recovery_status"], "requeued")

    def test_run_cycle_recovers_fresh_local_execution_without_recorded_pid_identity(self) -> None:
        task_id = self._create_task()
        other_task = self._create_task("Customer & Revenue")
        self.osys.claim_task(
            task_id,
            "Product Engineer",
            executor_id="exec-1",
            backend="local",
            process_id=os.getpid(),
            lease_seconds=600,
        )

        cycle = self.osys.run_cycle()

        self.assertIn(task_id, cycle["recovered"])
        self.assertNotIn(other_task, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "open")
        self.assertEqual(inspection["execution"]["recovery_status"], "requeued")

    def test_run_cycle_recovers_failed_execution_without_waiting_for_lease_expiry(self) -> None:
        task_id = self._create_task()
        other_task = self._create_task("Customer & Revenue")
        with patch("agent_company.ops._process_start_identity", return_value="current-start"):
            self.osys.claim_task(
                task_id,
                "Product Engineer",
                executor_id="exec-1",
                backend="local",
                process_id=os.getpid(),
                process_started_at="current-start",
                lease_seconds=600,
            )
            self.osys.fail_task(task_id, "exec-1", "reported failure", recoverable=True)

            cycle = self.osys.run_cycle()

        self.assertIn(task_id, cycle["recovered"])
        self.assertNotIn(other_task, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "open")
        self.assertEqual(inspection["execution"]["recovery_status"], "requeued")
        self.assertEqual(inspection["execution"]["attempt_count"], 1)

    def test_manual_recovery_rejects_fresh_live_local_execution(self) -> None:
        task_id = self._create_task()
        with patch("agent_company.ops._process_start_identity", return_value="current-start"):
            self.osys.claim_task(
                task_id,
                "Product Engineer",
                executor_id="exec-1",
                backend="local",
                process_id=os.getpid(),
                process_started_at="current-start",
                lease_seconds=600,
            )

            with self.assertRaisesRegex(ValueError, "no recoverable execution"):
                self.osys.recover_task(task_id, "CEO", reason="operator check")

        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertEqual(inspection["execution"]["recovery_status"], "running")
        self.assertEqual(inspection["execution"]["attempt_count"], 0)

    def test_recorded_pid_is_not_considered_alive_when_start_identity_differs(self) -> None:
        task_id = self._create_task()
        self.osys.claim_task(
            task_id,
            "Product Engineer",
            executor_id="exec-1",
            backend="local",
            process_id=os.getpid(),
            process_started_at="different-start-identity",
        )

        inspection = self.osys.inspect_execution(task_id)

        self.assertFalse(inspection["process"]["alive"])
        self.assertEqual(inspection["process"]["reason"], "process identity mismatch")

    def test_cli_commands_cover_execution_lifecycle(self) -> None:
        task_id = self._create_task()

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli_main([
                "--config", str(self.root / "config" / "sample.ini"),
                "task-claim", str(task_id), "--actor", "Product Engineer", "--executor-id", "cli-exec", "--backend", "codex",
                "--session-ref", "session-1",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["executor_id"], "cli-exec")

        for args in [
            ["task-heartbeat", str(task_id), "--executor-id", "cli-exec"],
            ["task-checkpoint", str(task_id), "--executor-id", "cli-exec", "--checkpoint", "halfway", "--next-action", "finish"],
            ["task-inspect", str(task_id)],
            ["task-fail", str(task_id), "--executor-id", "cli-exec", "--error", "needs retry"],
            ["task-recover", str(task_id), "--actor", "CEO", "--reason", "manual retry"],
        ]:
            with contextlib.redirect_stdout(io.StringIO()) as command_out:
                self.assertEqual(cli_main(["--config", str(self.root / "config" / "sample.ini"), *args]), 0)
                self.assertTrue(command_out.getvalue().strip())

    def test_dashboard_exposes_execution_health(self) -> None:
        task_id = self._create_task()
        self.osys.claim_task(task_id, "Product Engineer", executor_id="exec-1", backend="local", lease_seconds=60)
        self.osys.checkpoint_task(task_id, "exec-1", "checkpoint A", "next B")
        self.osys.fail_task(task_id, "exec-1", "last error", recoverable=True)

        snapshot = build_snapshot(self.config)
        health = snapshot["management"]["execution_health"]

        self.assertEqual(health["counts_by_recovery_status"]["failed"], 1)
        self.assertEqual(health["executions"][0]["checkpoint"], "checkpoint A")
        self.assertEqual(health["executions"][0]["last_error"], "last error")
        response = DashboardApp(self.config).render_path("/management")
        self.assertIn("执行健康", response.body)
        self.assertIn("checkpoint A", response.body)

    def test_safe_sqlite_migration_from_legacy_schema(self) -> None:
        legacy_db = self.root / "data" / "legacy.sqlite3"
        legacy_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(legacy_db) as conn:
            conn.execute(
                "CREATE TABLE tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, owner TEXT NOT NULL, title TEXT NOT NULL, domain TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER NOT NULL, blocked_reason TEXT, result TEXT)"
            )
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'CTO', 'legacy task', 'engineering', 'open', 50)",
                ("2026-07-11T00:00:00+00:00", "2026-07-11T00:00:00+00:00"),
            )
        Store(legacy_db).init()

        with sqlite3.connect(legacy_db) as conn:
            table = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='task_executions'").fetchone()[0]
            self.assertIn("UNIQUE(task_id)", table)
            task_count = conn.execute("SELECT COUNT(*) FROM tasks WHERE title='legacy task'").fetchone()[0]
            self.assertEqual(task_count, 1)


if __name__ == "__main__":
    unittest.main()
