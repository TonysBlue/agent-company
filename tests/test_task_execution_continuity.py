from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import threading
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

    def test_stale_generation_cannot_complete_after_reclaim(self) -> None:
        task_id = self._create_task()
        first = self.osys.claim_task(
            task_id, "Product Engineer", executor_id="exec-1", backend="local"
        )
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE task_executions SET recovery_status='requeued' WHERE task_id=?", (task_id,))
            conn.execute("UPDATE tasks SET status='open' WHERE id=?", (task_id,))
        second = self.osys.claim_task(
            task_id, "Product Engineer", executor_id="exec-2", backend="local"
        )
        evidence = self.root / "fenced-evidence.txt"
        evidence.write_text("reviewable\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "stale fencing token"):
            self.osys.complete_task(
                task_id, "Product Engineer", "stale", [evidence],
                fencing_token=first["fencing_token"],
            )
        completed = self.osys.complete_task(
            task_id, "Product Engineer", "current", [evidence],
            fencing_token=second["fencing_token"],
        )
        self.assertEqual(completed["status"], "done")

    def test_registered_executor_claim_issues_fencing_token_and_rejects_stale_generation(self) -> None:
        task_id = self._create_task()
        registered = self.osys.register_executor(
            "exec-registered", "Product Engineer", "local",
            capabilities=["product", "engineering"], capacity=1,
            process_id=os.getpid(), process_started_at="current-start",
            session_ref="session-1",
        )
        self.assertEqual(registered["status"], "healthy")

        with patch("agent_company.ops._process_start_identity", return_value="current-start"):
            claim = self.osys.claim_task(
                task_id, "Product Engineer", executor_id="exec-registered", backend="local",
                process_id=os.getpid(), process_started_at="current-start", session_ref="session-1",
            )
        token = claim["fencing_token"]
        self.assertTrue(token)
        self.osys.heartbeat_task(task_id, "exec-registered", fencing_token=token)

        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                "UPDATE task_executions SET recovery_status='requeued', attempt_count=1 WHERE task_id=?",
                (task_id,),
            )
            conn.execute("UPDATE tasks SET status='open' WHERE id=?", (task_id,))
        second = self.osys.claim_task(
            task_id, "Product Engineer", executor_id="exec-registered", backend="local"
        )
        self.assertNotEqual(second["fencing_token"], token)
        with self.assertRaisesRegex(ValueError, "stale fencing token"):
            self.osys.heartbeat_task(task_id, "exec-registered", fencing_token=token)

    def test_live_process_failure_enters_unknown_quarantine_instead_of_requeue(self) -> None:
        task_id = self._create_task()
        self.osys.register_executor(
            "exec-live", "Product Engineer", "local", capabilities=["engineering"],
            capacity=1, process_id=os.getpid(), process_started_at="current-start", session_ref="live",
        )
        with patch("agent_company.ops._process_start_identity", return_value="current-start"):
            claim = self.osys.claim_task(
                task_id, "Product Engineer", executor_id="exec-live", backend="local",
                process_id=os.getpid(), process_started_at="current-start",
            )
            self.osys.fail_task(
                task_id, "exec-live", "tracker lost", recoverable=True,
                fencing_token=claim["fencing_token"],
            )
            cycle = self.osys.run_cycle()

        self.assertNotIn(task_id, cycle["recovered"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertEqual(inspection["execution"]["recovery_status"], "unknown")
        executor = Store(self.config.db_path).fetch_one(
            "SELECT status FROM executors WHERE executor_id='exec-live'"
        )
        self.assertEqual(executor["status"], "quarantined")

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

    def test_run_cycle_leaves_valid_in_progress_leases_to_the_executor(self) -> None:
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
        self.assertEqual(after, before)
        self.assertEqual(cycle["recovered"], [])
        self.assertNotIn(other_task, cycle["progressed"])
        self.assertEqual(
            Store(self.config.db_path).fetch_one("SELECT status FROM tasks WHERE id=?", (other_task,))["status"],
            "open",
        )
        audit = Store(self.config.db_path).fetch_one(
            "SELECT * FROM audit_log WHERE action='renew_task_execution' AND entity_id=?",
            (str(task_id),),
        )
        self.assertIsNone(audit)

    def test_run_cycle_does_not_claim_work_without_a_real_executor(self) -> None:
        task_id = self._create_task()

        cycle = self.osys.run_cycle()

        self.assertNotIn(task_id, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "open")
        self.assertIsNone(inspection["execution"])
        audit = Store(self.config.db_path).fetch_one(
            "SELECT action FROM audit_log WHERE entity='task' AND entity_id=? ORDER BY id DESC LIMIT 1",
            (str(task_id),),
        )
        self.assertEqual(audit["action"], "task_ready_for_executor")

    def test_run_cycle_does_not_recover_valid_lease_when_recorded_pid_identity_mismatches(self) -> None:
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

        self.assertNotIn(task_id, cycle["recovered"])
        self.assertNotIn(other_task, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertEqual(inspection["execution"]["recovery_status"], "running")
        self.assertEqual(inspection["execution"]["attempt_count"], 0)

    def test_run_cycle_does_not_recover_valid_local_lease_without_recorded_pid(self) -> None:
        task_id = self._create_task()
        other_task = self._create_task("Customer & Revenue")
        self.osys.claim_task(task_id, "Product Engineer", executor_id="exec-1", backend="local", lease_seconds=600)

        cycle = self.osys.run_cycle()

        self.assertNotIn(task_id, cycle["recovered"])
        self.assertNotIn(other_task, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertEqual(inspection["execution"]["recovery_status"], "running")

    def test_run_cycle_does_not_recover_valid_local_lease_without_recorded_pid_identity(self) -> None:
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

        self.assertNotIn(task_id, cycle["recovered"])
        self.assertNotIn(other_task, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertEqual(inspection["execution"]["recovery_status"], "running")

    def test_run_cycle_quarantines_failed_execution_when_process_is_still_alive(self) -> None:
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

        self.assertNotIn(task_id, cycle["recovered"])
        self.assertNotIn(other_task, cycle["progressed"])
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertEqual(inspection["execution"]["recovery_status"], "unknown")
        self.assertEqual(inspection["execution"]["attempt_count"], 0)

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

    def test_expired_executor_cannot_renew_or_complete_its_lease(self) -> None:
        task_id = self._create_task()
        evidence = self.root / "expired-evidence.txt"
        evidence.write_text("not accepted\n", encoding="utf-8")
        self.osys.claim_task(task_id, "Product Engineer", executor_id="expired-exec", backend="codex")
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                "UPDATE task_executions SET lease_expires_at=? WHERE task_id=?",
                ("2026-07-10T00:01:00+00:00", task_id),
            )

        with self.assertRaisesRegex(ValueError, "lease has expired"):
            self.osys.heartbeat_task(task_id, "expired-exec")
        with self.assertRaisesRegex(ValueError, "lease has expired"):
            self.osys.complete_task(task_id, "Product Engineer", "too late", [evidence])

        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertEqual(inspection["execution"]["recovery_status"], "running")

    def test_completion_rejects_missing_execution_without_partial_task_update(self) -> None:
        task_id = self._create_task()
        evidence = self.root / "completion-evidence.txt"
        evidence.write_text("verified\n", encoding="utf-8")
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (task_id,))

        with self.assertRaisesRegex(ValueError, "no active execution"):
            self.osys.complete_task(task_id, "Product Engineer", "verified", [evidence])

        task = Store(self.config.db_path).fetch_one("SELECT status, result FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(task["status"], "in_progress")
        self.assertIsNone(task["result"])

    def test_completion_rolls_back_task_and_execution_when_audit_fails(self) -> None:
        task_id = self._create_task()
        evidence = self.root / "rollback-evidence.txt"
        evidence.write_text("verified\n", encoding="utf-8")
        self.osys.claim_task(task_id, "Product Engineer", executor_id="real-exec", backend="codex")

        with patch.object(self.osys.store, "audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.osys.complete_task(task_id, "Product Engineer", "verified", [evidence])

        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "in_progress")
        self.assertIsNone(inspection["task"]["result"])
        self.assertEqual(inspection["execution"]["recovery_status"], "running")

    def test_concurrent_real_executors_cannot_duplicate_claim(self) -> None:
        task_id = self._create_task()
        barrier = threading.Barrier(2)
        results: list[str] = []

        def claim(executor_id: str) -> None:
            barrier.wait()
            try:
                self.osys.claim_task(task_id, "Product Engineer", executor_id=executor_id, backend="codex")
            except ValueError:
                results.append("rejected")
            else:
                results.append(executor_id)

        threads = [threading.Thread(target=claim, args=(f"real-exec-{index}",)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 2)
        self.assertEqual(results.count("rejected"), 1)
        execution = self.osys.inspect_execution(task_id)["execution"]
        self.assertIn(execution["executor_id"], {"real-exec-0", "real-exec-1"})

    def test_local_end_to_end_stale_recovery_reclaim_and_atomic_completion(self) -> None:
        task_id = self._create_task()
        first = self.osys.claim_task(
            task_id,
            "Product Engineer",
            executor_id="real-exec-1",
            backend="codex",
            lease_seconds=60,
        )
        renewed = self.osys.heartbeat_task(task_id, "real-exec-1", lease_seconds=120)
        self.assertGreater(renewed["lease_expires_at"], first["lease_expires_at"])
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                "UPDATE task_executions SET lease_expires_at=? WHERE task_id=?",
                ("2026-07-10T00:01:00+00:00", task_id),
            )

        cycle = self.osys.run_cycle()
        self.assertIn(task_id, cycle["recovered"])
        claimed = self.osys.claim_task(
            task_id,
            "Product Engineer",
            executor_id="real-exec-2",
            backend="codex",
        )
        self.assertEqual(claimed["executor_id"], "real-exec-2")
        evidence = self.root / "e2e-evidence.txt"
        evidence.write_text("verified locally\n", encoding="utf-8")

        completed = self.osys.complete_task(task_id, "Product Engineer", "verified", [evidence])

        self.assertEqual(completed["status"], "done")
        inspection = self.osys.inspect_execution(task_id)
        self.assertEqual(inspection["task"]["status"], "done")
        self.assertEqual(inspection["execution"]["recovery_status"], "completed")
        executor_ids = {
            row["executor_id"]
            for row in Store(self.config.db_path).fetch_all("SELECT executor_id FROM task_executions")
        }
        self.assertFalse(any(executor_id.startswith("cycle-") for executor_id in executor_ids))

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
