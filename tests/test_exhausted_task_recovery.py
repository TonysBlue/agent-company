from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_company.config import load_config
from agent_company.db import Store
from agent_company.ops import CompanyOS


class ExhaustedTaskRecoveryTest(unittest.TestCase):
    """RED contract for the narrowly governed exhausted-task recovery path."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[paths]\ndatabase=data/test.sqlite3\nchairman_inbox=data/inbox\n"
            "chairman_outbox=data/outbox\nartifacts=data/artifacts\nlogs=logs\n",
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

    def _exhausted(self, task_id: int, *, status: str = "blocked") -> None:
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET status=? WHERE id=?", (status, task_id))
            conn.execute(
                """INSERT INTO task_executions(
                    task_id,executor_id,backend,process_id,process_started_at,
                    claimed_at,heartbeat_at,lease_expires_at,attempt_count,
                    max_attempts,recovery_status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, "dead-exec", "local", 4567, "start-1", "t", "t", "t", 3, 3, "exhausted", "t", "t"),
            )

    def _task(self, title: str, owner: str, domain: str, requested_id: int) -> int:
        with Store(self.config.db_path).connect() as conn:
            task_id = conn.execute(
                """INSERT INTO tasks(id,created_at,updated_at,owner,title,domain,status,priority)
                   VALUES (?, 't','t',?,?,?,'blocked',90)""",
                (requested_id, owner, title, domain),
            ).lastrowid
            # Keep the governance contract explicit without depending on production ids.
        return int(task_id)

    def test_requeues_allowlisted_exhausted_task_with_signed_immutable_record(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={
            "alive": False, "reason": "process not found"
        }):
            result = self.osys.requeue_exhausted_task(
                task_id, "CEO", "retry after verified executor death"
            )

        self.assertEqual(result["status"], "open")
        self.assertEqual(result["task_id"], task_id)
        task = Store(self.config.db_path).fetch_one("SELECT status,result FROM tasks WHERE id=?", (task_id,))
        execution = Store(self.config.db_path).fetch_one(
            "SELECT recovery_status,attempt_count,generation FROM task_executions WHERE task_id=?", (task_id,)
        )
        self.assertEqual(task["status"], "open")
        self.assertIsNone(task["result"])
        self.assertEqual(execution["recovery_status"], "requeued")
        self.assertEqual(execution["attempt_count"], 0)
        record = Store(self.config.db_path).fetch_one(
            "SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,)
        )
        self.assertIsNotNone(record)
        proof = json.loads(record["process_dead_proof"])
        self.assertEqual(proof["observed_reason"], "process not found")
        self.assertTrue(proof["identity_checked"])
        self.assertIsNotNone(record["integrity_signature"])
        event = Store(self.config.db_path).fetch_one(
            "SELECT event_type FROM execution_events WHERE entity_type='task' AND entity_id=? ORDER BY id DESC LIMIT 1",
            (str(task_id),),
        )
        self.assertEqual(event["event_type"], "task.requeued")

    def test_requeue_is_idempotent_and_reconciled_task_is_terminal(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            first = self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
            second = self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        self.assertEqual(first, second)
        self.assertEqual(
            Store(self.config.db_path).fetch_one("SELECT COUNT(*) AS c FROM task_recovery_records WHERE task_id=?", (task_id,))["c"],
            1,
        )
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET status='reconciled' WHERE id=?", (task_id,))
            conn.execute("UPDATE task_executions SET recovery_status='reconciled' WHERE task_id=?", (task_id,))
        with self.assertRaisesRegex(ValueError, "reconciled|terminal"):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")

    def test_existing_recovery_command_routes_exhausted_work_to_governed_path(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            result = self.osys.recover_task(task_id, "CEO", "verified death")
        self.assertEqual(result["status"], "open")
        self.assertIsNotNone(Store(self.config.db_path).fetch_one("SELECT 1 FROM task_recovery_records WHERE task_id=?", (task_id,)))

    def test_fail_closed_for_non_ceo_unsafe_state_and_process_identity(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        for actor in ("Product Engineer", "Chairman"):
            with self.assertRaisesRegex(ValueError, "CEO"):
                self.osys.requeue_exhausted_task(task_id, actor, "verified death")
        with patch.object(self.osys, "_process_status", return_value={"alive": None, "reason": "no process recorded"}):
            with self.assertRaisesRegex(ValueError, "process|proof|identity"):
                self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process identity mismatch"}):
            with self.assertRaisesRegex(ValueError, "process|proof|identity"):
                self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE task_executions SET process_id=NULL WHERE task_id=?", (task_id,))
        with self.assertRaisesRegex(ValueError, "PID|identity|process"):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")

        non_allowlisted = self._task("unlisted exhausted work", "Customer & Revenue", "commercial", 147)
        self._exhausted(non_allowlisted)
        with self.assertRaisesRegex(ValueError, "allowlist"):
            self.osys.requeue_exhausted_task(non_allowlisted, "CEO", "verified death")

    def test_task_146_requires_approved_task_145_and_never_allows_external_scope(self) -> None:
        task_id = self._task("准备受控Beta获批后内部执行就绪检查", "Product Engineer", "product", 146)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            with self.assertRaisesRegex(ValueError, "145|approval"):
                self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
            with Store(self.config.db_path).connect() as conn:
                conn.execute(
                    "INSERT INTO approvals(created_at,requested_by,action_type,summary,status,decision) VALUES ('t','CEO','internal_task_approval','Task 145 Chairman decision package approved','approved','approve')"
                )
            result = self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        self.assertEqual(result["status"], "open")
        record = Store(self.config.db_path).fetch_one("SELECT scope FROM task_recovery_records WHERE task_id=?", (task_id,))
        self.assertIn("internal readiness", record["scope"])

    def test_audit_failure_rolls_back_all_state_and_tampering_fails_closed(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}), \
             patch.object(self.osys.store, "audit", side_effect=RuntimeError("audit unavailable")):
            with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
                self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        state = Store(self.config.db_path).fetch_one(
            "SELECT t.status,e.recovery_status FROM tasks t JOIN task_executions e ON e.task_id=t.id WHERE t.id=?",
            (task_id,),
        )
        self.assertEqual((state["status"], state["recovery_status"]), ("blocked", "exhausted"))
        self.assertIsNone(Store(self.config.db_path).fetch_one("SELECT 1 FROM task_recovery_records WHERE task_id=?", (task_id,)))

        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        with Store(self.config.db_path).connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                conn.execute("UPDATE task_recovery_records SET reason='tampered' WHERE task_id=?", (task_id,))
            conn.execute("DROP TRIGGER task_recovery_records_immutable_update")
            conn.execute("UPDATE task_recovery_records SET reason='tampered' WHERE task_id=?", (task_id,))
        with self.assertRaisesRegex(ValueError, "integrity"):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")

    def test_concurrent_requests_produce_one_immutable_record(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        results: list[str] = []
        def call() -> None:
            try:
                with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
                    self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
                results.append("ok")
            except Exception:
                results.append("error")
        threads = [threading.Thread(target=call) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results, ["ok", "ok"])
        self.assertEqual(Store(self.config.db_path).fetch_one("SELECT COUNT(*) AS c FROM task_recovery_records WHERE task_id=?", (task_id,))["c"], 1)


if __name__ == "__main__":
    unittest.main()
