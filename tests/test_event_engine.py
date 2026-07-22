from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.cli import main as cli_main
from agent_company.config import load_config
from agent_company.db import Store, utcnow
from agent_company.event_engine import EventEngine, WorkerAlreadyRunning
from agent_company.ops import CompanyOS


class EventEngineTest(unittest.TestCase):
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
        self.osys.init()
        self.store = Store(self.config.db_path)
        with self.store.connect() as conn:
            conn.execute("DELETE FROM execution_events")
            conn.execute("DELETE FROM task_executions")
            conn.execute("DELETE FROM tasks")

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _insert_task(self, title: str, owner: str, domain: str, priority: int = 90) -> int:
        now = utcnow()
        with self.store.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO tasks(
                       created_at, updated_at, owner, title, domain, status,
                       priority, acceptance_criteria
                   ) VALUES (?, ?, ?, ?, ?, 'open', ?, 'Reviewable evidence passes.')""",
                (now, now, owner, title, domain, priority),
            )
            return int(cursor.lastrowid)

    def test_task_lifecycle_and_chairman_decision_enqueue_durable_events(self) -> None:
        task_id = self._insert_task("Internal product improvement", "Product Engineer", "engineering")
        self.osys.run_cycle()
        self.osys.claim_task(task_id, "Product Engineer", executor_id="real-executor-1", backend="local")
        self.osys.fail_task(task_id, "real-executor-1", "transient", recoverable=True)
        self.osys.recover_task(task_id, "CEO", "executor restart")
        self.osys.run_cycle()
        self.osys.claim_task(task_id, "Product Engineer", executor_id="real-executor-2", backend="local")
        evidence = self.root / "evidence.txt"
        evidence.write_text("verified\n", encoding="utf-8")
        self.osys.complete_task(task_id, "Product Engineer", "done", [evidence])

        cancelled_id = self._insert_task("Cancel obsolete work", "Product Engineer", "engineering")
        self.osys.cancel_task(cancelled_id, "CEO", "Superseded by reviewed work.")

        reserved_id = self._insert_task("Change pricing", "Customer & Revenue", "gtm", 100)
        self.osys.run_cycle()
        approval = self.osys.chairman_inbox()[0]
        self.osys.decide(approval["id"], "deny", "Do not proceed.")

        event_types = [
            row["event_type"]
            for row in self.store.fetch_all("SELECT event_type FROM execution_events ORDER BY id")
        ]
        for event_type in [
            "task.created",
            "task.failed",
            "task.recovered",
            "task.completed",
            "task.cancelled",
            "chairman.decided",
        ]:
            self.assertIn(event_type, event_types)
        decision = self.store.fetch_one(
            "SELECT entity_id, payload FROM execution_events WHERE event_type='chairman.decided'"
        )
        self.assertEqual(decision["entity_id"], str(approval["id"]))
        self.assertEqual(json.loads(decision["payload"])["decision"], "deny")
        self.assertEqual(
            self.store.fetch_one("SELECT status FROM tasks WHERE id=?", (reserved_id,))["status"],
            "done",
        )

    def test_unclaimed_task_event_stays_pending_instead_of_reporting_dispatch_success(self) -> None:
        task_id = self._insert_task(
            "Prepare bounded commercial evidence", "Customer & Revenue", "customer"
        )

        result = EventEngine(self.config).step()

        self.assertEqual(result["status"], "deferred")
        event = self.store.fetch_one(
            "SELECT status, last_error FROM execution_events WHERE event_type='task.created' AND entity_id=?",
            (str(task_id),),
        )
        self.assertEqual(event["status"], "pending")
        self.assertIn("no executor claimed task", event["last_error"])
        self.assertIsNone(self.store.fetch_one("SELECT id FROM task_executions WHERE task_id=?", (task_id,)))

    def test_unclaimed_task_exhaustion_blocks_with_explicit_reason(self) -> None:
        task_id = self._insert_task(
            "Prepare bounded commercial evidence", "Customer & Revenue", "customer"
        )
        engine = EventEngine(self.config)
        engine.step()
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE execution_events SET available_at=?, attempts=2 WHERE event_type='task.created' AND entity_id=?",
                (utcnow(), str(task_id)),
            )

        result = engine.step()

        self.assertEqual(result["status"], "processed")
        task = self.store.fetch_one("SELECT status, blocked_reason FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(task["status"], "blocked")
        self.assertIn("No healthy executor claimed task", task["blocked_reason"])

    def test_claimed_task_event_can_be_processed(self) -> None:
        task_id = self._insert_task("Internal product improvement", "Product Engineer", "engineering")
        self.osys.claim_task(task_id, "Product Engineer", executor_id="runner-1", backend="local")

        result = EventEngine(self.config).step()

        self.assertEqual(result["status"], "processed")
        event = self.store.fetch_one(
            "SELECT status FROM execution_events WHERE event_type='task.created' AND entity_id=?",
            (str(task_id),),
        )
        self.assertEqual(event["status"], "processed")

    def test_approval_block_only_blocks_linked_task_and_safe_work_dispatches(self) -> None:
        blocked_id = self._insert_task("Change pricing", "Customer & Revenue", "gtm", 100)
        safe_id = self._insert_task("Improve internal editor", "Product Engineer", "engineering", 90)

        result = EventEngine(self.config).step()

        self.assertEqual(result["event_type"], "task.created")
        self.assertEqual(
            self.store.fetch_one("SELECT status FROM tasks WHERE id=?", (blocked_id,))["status"],
            "blocked",
        )
        self.assertEqual(
            self.store.fetch_one("SELECT status FROM tasks WHERE id=?", (safe_id,))["status"],
            "open",
        )
        ready = self.store.fetch_one(
            "SELECT action FROM audit_log WHERE action='task_ready_for_executor' AND entity_id=?",
            (str(safe_id),),
        )
        self.assertIsNotNone(ready)

    def test_idle_wait_blocks_without_running_cycles_or_creating_work(self) -> None:
        engine = EventEngine(self.config)
        before_cycles = self.store.fetch_one("SELECT COUNT(*) AS count FROM cycles")["count"]
        before_tasks = self.store.fetch_one("SELECT COUNT(*) AS count FROM tasks")["count"]

        woke = engine.wait_for_wake(timeout=0.02)

        self.assertFalse(woke)
        self.assertEqual(self.store.fetch_one("SELECT COUNT(*) AS count FROM cycles")["count"], before_cycles)
        self.assertEqual(self.store.fetch_one("SELECT COUNT(*) AS count FROM tasks")["count"], before_tasks)

    def test_empty_active_phase_schedules_immediate_strategic_review(self) -> None:
        now = utcnow()
        with self.store.connect() as conn:
            conn.execute(
                """INSERT INTO strategic_phases(
                       phase_key, name, objective, success_metrics, deadline,
                       dependencies, evidence_requirements, status, created_at, activated_at
                   ) VALUES ('phase-review', 'Customer validation', 'Produce customer evidence',
                             '[\"5 sessions\"]', '2099-01-01T00:00:00+00:00', '[]',
                             '[\"session records\"]', 'active', ?, ?)""",
                (now, now),
            )

        engine = EventEngine(self.config)
        engine.init()

        event = self.store.fetch_one(
            """SELECT event_type, entity_type, entity_id, status, available_at
               FROM execution_events WHERE event_type='ceo.strategic_review'"""
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["entity_type"], "strategic_phase")
        self.assertEqual(event["status"], "pending")
        self.assertLessEqual(event["available_at"], utcnow())

    def test_future_strategic_review_wakes_worker_when_due(self) -> None:
        with self.store.connect() as conn:
            self.store.enqueue_event(
                conn,
                "ceo.strategic_review",
                "strategic_phase",
                1,
                {"reason": "scheduled business review"},
                available_at="2000-01-01T00:00:00+00:00",
                priority=80,
            )

        self.assertTrue(EventEngine(self.config).wait_for_wake(timeout=30))

    def test_init_backfills_wake_events_for_active_pre_engine_tasks(self) -> None:
        task_id = self._insert_task("Pre-engine task", "Product Engineer", "engineering")
        with self.store.connect() as conn:
            conn.execute("DELETE FROM execution_events WHERE entity_id=?", (str(task_id),))

        self.osys.init()

        event = self.store.fetch_one(
            "SELECT event_type, status FROM execution_events WHERE entity_id=?",
            (str(task_id),),
        )
        self.assertEqual(dict(event), {"event_type": "task.created", "status": "pending"})

    def test_step_persists_wake_and_recovers_an_expired_execution(self) -> None:
        task_id = self._insert_task("Expired execution", "Product Engineer", "engineering")
        self.osys.claim_task(task_id, "Product Engineer", executor_id="expired", backend="local")
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE execution_events SET status='processed', processed_at=?",
                (utcnow(),),
            )
            conn.execute(
                "UPDATE task_executions SET lease_expires_at=? WHERE task_id=?",
                ("2026-01-01T00:00:00+00:00", task_id),
            )

        result = EventEngine(self.config).step()

        self.assertEqual(result["event_type"], "worker.wake")
        self.assertIn(task_id, result["dispatch"]["recovered"])
        wake = self.store.fetch_one(
            "SELECT payload FROM execution_events WHERE event_type='worker.wake' ORDER BY id DESC LIMIT 1"
        )
        self.assertEqual(json.loads(wake["payload"])["reason"], "task execution recovery due")

    def test_worker_lock_is_single_instance_and_audited(self) -> None:
        first = EventEngine(self.config)
        second = EventEngine(self.config)
        with first.worker_lock():
            with self.assertRaises(WorkerAlreadyRunning):
                with second.worker_lock():
                    pass

        actions = [
            row["action"]
            for row in self.store.fetch_all(
                "SELECT action FROM audit_log WHERE entity='event_worker' ORDER BY id"
            )
        ]
        self.assertIn("worker_lock_acquired", actions)
        self.assertIn("worker_lock_rejected", actions)
        self.assertIn("worker_lock_released", actions)

    def test_restart_recovers_claimed_event_and_reports_health(self) -> None:
        task_id = self._insert_task("Recover after restart", "Product Engineer", "engineering")
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE execution_events
                   SET status='processing', claimed_at=?, worker_id='dead-worker'
                   WHERE entity_id=?""",
                (utcnow(), str(task_id)),
            )

        result = EventEngine(self.config).step()
        health = EventEngine(self.config).status()

        self.assertEqual(result["entity_id"], str(task_id))
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(health["pending_events"], 1)
        self.assertEqual(health["processing_events"], 0)
        recovery = self.store.fetch_one(
            "SELECT action FROM audit_log WHERE action='recover_processing_events'"
        )
        self.assertIsNotNone(recovery)

    def test_stale_running_state_without_lock_is_degraded(self) -> None:
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE event_worker_state
                   SET status='waiting', worker_id='dead-worker', process_id=999999
                   WHERE singleton=1"""
            )

        health = EventEngine(self.config).status()

        self.assertEqual(health["health"], "degraded")
        self.assertFalse(health["lock_held"])

    def test_cli_exposes_step_status_and_wake_without_starting_forever_worker(self) -> None:
        config = str(self.root / "config" / "sample.ini")
        outputs: list[dict[str, object]] = []
        for args in [["worker-wake", "--reason", "operator test"], ["worker-step"], ["worker-status"]]:
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(cli_main(["--config", config, *args]), 0)
            outputs.append(json.loads(stream.getvalue()))

        self.assertEqual(outputs[0]["event_type"], "worker.wake")
        self.assertEqual(outputs[1]["status"], "processed")
        self.assertIn("health", outputs[2])


if __name__ == "__main__":
    unittest.main()
