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
from agent_company.ops import recovery_conflicts
from agent_company.integrity import approval_binding_signature


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

    def _approve_task_145(self) -> int:
        created_at = "2026-01-01T00:00:00+00:00"
        summary = "Task 145 Chairman decision package approved"
        values = {
            "created_at": created_at, "requested_by": "CEO",
            "action_type": "internal_task_approval", "summary": summary,
            "target_task_id": 145, "target_action": "internal_task_approval",
        }
        with Store(self.config.db_path).connect() as conn:
            approval_id = conn.execute(
                "INSERT INTO approvals(created_at,requested_by,action_type,summary,status,target_task_id,target_action,integrity_signature) "
                "VALUES (?,?,?,?, 'pending',?,?,?)",
                (*values.values(), approval_binding_signature(self.config.db_path, values)),
            ).lastrowid
        self.osys.decide(approval_id, "approve", "Approved exact Task 145 decision.", "Chairman")
        return int(approval_id)

    def _replace_recovery_unique_constraint(self, index_sql: str) -> None:
        from agent_company.db import RECOVERY_TRIGGER_DEFINITIONS

        with Store(self.config.db_path).connect() as conn:
            conn.execute("ALTER TABLE task_recovery_records RENAME TO task_recovery_records_legacy")
            conn.execute(
                """CREATE TABLE task_recovery_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    recovered_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    process_dead_proof TEXT NOT NULL,
                    previous_task_state TEXT NOT NULL,
                    previous_execution_state TEXT NOT NULL,
                    new_task_state TEXT NOT NULL,
                    new_execution_state TEXT NOT NULL,
                    audit_log_id INTEGER,
                    event_id INTEGER,
                    integrity_signature TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE RESTRICT
                )"""
            )
            conn.execute(index_sql)
            for name in RECOVERY_TRIGGER_DEFINITIONS:
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            for definition in RECOVERY_TRIGGER_DEFINITIONS.values():
                conn.execute(definition)
            conn.execute("DROP TABLE task_recovery_records_legacy")

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
            self._approve_task_145()
            result = self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        self.assertEqual(result["status"], "open")
        record = Store(self.config.db_path).fetch_one("SELECT scope FROM task_recovery_records WHERE task_id=?", (task_id,))
        self.assertIn("internal readiness", record["scope"])

    def test_task_146_rejects_unrelated_or_malformed_task_145_approval(self) -> None:
        task_id = self._task("准备受控Beta获批后内部执行就绪检查", "Product Engineer", "product", 146)
        self._exhausted(task_id)
        with Store(self.config.db_path).connect() as conn:
            conn.execute(
                "INSERT INTO approvals(created_at,requested_by,action_type,summary,status,decision) "
                "VALUES ('t','CEO','external_publish','Unrelated request mentions Task 145','approved','approve')"
            )
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            with self.assertRaisesRegex(ValueError, "145|approval"):
                self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")

    def test_decide_requires_authenticated_chairman_actor(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            approval_id = conn.execute(
                "INSERT INTO approvals(created_at,requested_by,action_type,summary,status) "
                "VALUES (?, 'CEO', 'pricing_change', 'Task 1 requires Chairman decision before continuing: Change price tier', 'pending')",
                (now,),
            ).lastrowid
        with self.assertRaisesRegex(ValueError, "Chairman|actor|auth"):
            self.osys.decide(approval_id, "approve", "spoofed", actor="CEO")

    def test_signed_approval_and_decision_are_immutable_and_forgery_is_rejected(self) -> None:
        approval_id = self._approve_task_145()
        with Store(self.config.db_path).connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable|integrity"):
                conn.execute("UPDATE approvals SET target_task_id=999 WHERE id=?", (approval_id,))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable|integrity"):
                conn.execute("UPDATE approvals SET decided_by='CEO' WHERE id=?", (approval_id,))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                conn.execute("DELETE FROM approvals WHERE id=?", (approval_id,))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "integrity"):
                conn.execute(
                    "INSERT INTO approvals(created_at,requested_by,action_type,summary,status,target_task_id,target_action,integrity_signature) "
                    "VALUES ('t','CEO','internal_task_approval','Task 145 Chairman decision package approved','pending',145,'internal_task_approval','forged')"
                )

    def test_recovery_evidence_remains_valid_across_later_lifecycle(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET status='in_progress',updated_at='later' WHERE id=?", (task_id,))
            conn.execute(
                "UPDATE task_executions SET recovery_status='running',attempt_count=1,updated_at='later' WHERE task_id=?",
                (task_id,),
            )
        with Store(self.config.db_path).connect_readonly() as conn:
            self.assertEqual(recovery_conflicts(conn, self.config.db_path), [])

    def test_recovery_evidence_supports_reexhaustion_without_duplicate_conflict(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death 1")
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET status='blocked',blocked_reason='again' WHERE id=?", (task_id,))
            conn.execute(
                "UPDATE task_executions SET recovery_status='exhausted',attempt_count=3,max_attempts=3,"
                "process_id=4568,process_started_at='start-2',updated_at='later' WHERE task_id=?",
                (task_id,),
            )
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            second = self.osys.requeue_exhausted_task(task_id, "CEO", "verified death 2")
        self.assertEqual(second["status"], "open")
        self.assertEqual(
            Store(self.config.db_path).fetch_one("SELECT COUNT(*) AS c FROM task_recovery_records WHERE task_id=?", (task_id,))["c"],
            2,
        )

    def test_recovery_detects_signed_task_identity_drift(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TRIGGER task_recovery_task_identity_immutable")
            conn.execute("UPDATE tasks SET owner='Product Engineer' WHERE id=?", (task_id,))
        with Store(self.config.db_path).connect_readonly() as conn:
            self.assertTrue(any(item.get("task_id") == task_id for item in recovery_conflicts(conn, self.config.db_path)))

    def test_recovery_task_identity_trigger_blocks_scope_drift(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        with Store(self.config.db_path).connect() as conn:
            for column, value in (
                ("owner", "Product Engineer"), ("domain", "product"),
                ("title", "different"), ("acceptance_criteria", "expanded scope"),
            ):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "identity.*immutable"):
                    conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task_id))

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

    def test_signed_recovery_binds_durable_audit_and_event_row_ids(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one("SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,))
        self.assertIsNotNone(record["audit_log_id"])
        self.assertIsNotNone(record["event_id"])
        audit = Store(self.config.db_path).fetch_one("SELECT * FROM audit_log WHERE id=?", (record["audit_log_id"],))
        event = Store(self.config.db_path).fetch_one("SELECT * FROM execution_events WHERE id=?", (record["event_id"],))
        self.assertEqual(json.loads(audit["details"])["audit_log_id"], record["audit_log_id"])
        self.assertEqual(json.loads(event["payload"])["event_id"], record["event_id"])
        self.assertEqual(audit["ts"], record["recovered_at"])
        self.assertEqual(event["created_at"], record["recovered_at"])

    def test_recovery_provenance_replacement_or_deletion_fails_closed(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one("SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,))
        with Store(self.config.db_path).connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable|provenance"):
                conn.execute("DELETE FROM audit_log WHERE id=?", (record["audit_log_id"],))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable|provenance"):
                conn.execute("UPDATE execution_events SET payload=payload WHERE id=?", (record["event_id"],))
            conn.execute("DROP TRIGGER recovery_audit_provenance_immutable_delete")
            conn.execute("DELETE FROM audit_log WHERE id=?", (record["audit_log_id"],))
            with self.assertRaisesRegex(sqlite3.IntegrityError, "provenance|one-to-one"):
                conn.execute(
                    "INSERT INTO audit_log(ts,actor,action,entity,entity_id,details) VALUES (?,?,?,?,?,?)",
                    (record["recovered_at"], record["actor"], "requeue_exhausted_task", "task_execution", str(task_id),
                     json.dumps({**{k: record[k] for k in ("task_id", "recovered_at", "actor", "reason", "scope")}, "record_id": record["id"]}, sort_keys=True)),
                )
        self.assertTrue(any(item.get("task_id") == task_id for item in recovery_conflicts(Store(self.config.db_path).connect_readonly(), self.config.db_path)))

    def test_init_repairs_modified_recovery_triggers_unconditionally(self) -> None:
        """A same-name permissive trigger must not survive Store initialization."""
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TRIGGER task_recovery_records_immutable_update")
            conn.execute(
                """CREATE TRIGGER task_recovery_records_immutable_update
                   BEFORE UPDATE ON task_recovery_records BEGIN SELECT 1; END"""
            )
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                conn.execute("UPDATE task_recovery_records SET reason='tampered' WHERE task_id=?", (task_id,))

    def test_recovery_conflicts_reject_same_name_noncanonical_provenance_trigger(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TRIGGER recovery_audit_provenance_immutable_delete")
            conn.execute(
                """CREATE TRIGGER recovery_audit_provenance_immutable_delete
                   BEFORE DELETE ON audit_log
                   WHEN OLD.action='requeue_exhausted_task' AND OLD.entity='task_execution'
                   AND 0=1
                   BEGIN SELECT RAISE(ABORT, 'recovery audit provenance is immutable'); END"""
            )
        with Store(self.config.db_path).connect_readonly() as conn:
            self.assertTrue(any(item.get("trigger") == "recovery_audit_provenance_immutable_delete" for item in recovery_conflicts(conn, self.config.db_path)))

    def test_validate_requires_recovery_history_table(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TABLE task_recovery_records")
        errors = self.osys.validate()
        self.assertTrue(any("task_recovery_records" in error for error in errors))

    def test_assurance_integrity_reports_deleted_recovery_history(self) -> None:
        from agent_company.assurance import AssuranceKernel

        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TABLE task_recovery_records")
        result = AssuranceKernel(self.config).verify_integrity()
        self.assertTrue(any(conflict.get("anchor") == "recovery_schema" for conflict in result["conflicts"]))

    def test_init_does_not_recreate_deleted_governed_recovery_history(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TABLE task_recovery_records")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "recovery history"):
            self.osys.init()

    def test_assurance_init_rejects_deleted_governed_recovery_history(self) -> None:
        from agent_company.assurance import AssuranceKernel

        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TABLE task_recovery_records")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "recovery history"):
            AssuranceKernel(self.config).init()

    def test_recovery_uses_one_canonical_timestamp_for_audit_and_event(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}), \
             patch("agent_company.ops.utcnow", return_value="2026-08-08T12:00:00+00:00"), \
             patch("agent_company.db.utcnow", side_effect=["2099-01-01T00:00:00+00:00", "2099-01-01T00:00:01+00:00"]):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one("SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,))
        audit = Store(self.config.db_path).fetch_one("SELECT ts FROM audit_log WHERE id=?", (record["audit_log_id"],))
        event = Store(self.config.db_path).fetch_one("SELECT created_at FROM execution_events WHERE id=?", (record["event_id"],))
        self.assertEqual((record["recovered_at"], audit["ts"], event["created_at"]), ("2026-08-08T12:00:00+00:00",) * 3)

    def test_requeue_requires_null_task_result_and_rolls_back_atomically(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET result=? WHERE id=?", (json.dumps({"malformed": True}), task_id))
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            with self.assertRaisesRegex(ValueError, "result.*NULL|result.*present"):
                self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        state = Store(self.config.db_path).fetch_one("SELECT status,result FROM tasks WHERE id=?", (task_id,))
        execution = Store(self.config.db_path).fetch_one("SELECT recovery_status FROM task_executions WHERE task_id=?", (task_id,))
        self.assertEqual(state["status"], "blocked")
        self.assertEqual(state["result"], json.dumps({"malformed": True}))
        self.assertEqual(execution["recovery_status"], "exhausted")
        self.assertIsNone(Store(self.config.db_path).fetch_one("SELECT 1 FROM task_recovery_records WHERE task_id=?", (task_id,)))

    def test_recovery_conflicts_bind_available_at_and_survive_trigger_restore(self) -> None:
        """The immutable recovery event schedule is part of the signed binding."""
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one("SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,))
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TRIGGER recovery_event_provenance_immutable_update")
            conn.execute(
                "UPDATE execution_events SET available_at='2099-01-01T00:00:00+00:00' WHERE id=?",
                (record["event_id"],),
            )
            from agent_company.db import Store as DbStore
            DbStore._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(
            any(item.get("anchor") == "task_recovery_record" and item.get("task_id") == task_id for item in conflicts),
            conflicts,
        )

    def test_recovery_payloads_bind_all_signed_state_and_reject_substitution(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one(
            "SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,)
        )
        audit = Store(self.config.db_path).fetch_one(
            "SELECT details FROM audit_log WHERE id=?", (record["audit_log_id"],)
        )
        event = Store(self.config.db_path).fetch_one(
            "SELECT payload FROM execution_events WHERE id=?", (record["event_id"],)
        )
        expected = {
            "record_id": int(record["id"]),
            "task_id": int(record["task_id"]),
            "scope": record["scope"],
            "previous_task_state": record["previous_task_state"],
            "previous_execution_state": record["previous_execution_state"],
            "new_task_state": record["new_task_state"],
            "new_execution_state": record["new_execution_state"],
            "audit_log_id": int(record["audit_log_id"]),
            "event_id": int(record["event_id"]),
            "process_dead_proof": record["process_dead_proof"],
            "recovery_integrity_signature": record["integrity_signature"],
        }
        audit_payload = json.loads(audit["details"])
        event_payload = json.loads(event["payload"])
        for key, value in expected.items():
            self.assertEqual(audit_payload.get(key), value, key)
            self.assertEqual(event_payload.get(key), value, key)

        substituted = dict(event_payload)
        substituted["previous_task_state"] = record["new_task_state"]
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TRIGGER recovery_audit_provenance_immutable_update")
            conn.execute("DROP TRIGGER recovery_event_provenance_immutable_update")
            conn.execute(
                "UPDATE audit_log SET details=? WHERE id=?",
                (json.dumps(substituted, sort_keys=True), record["audit_log_id"]),
            )
            conn.execute(
                "UPDATE execution_events SET payload=? WHERE id=?",
                (json.dumps(substituted, sort_keys=True), record["event_id"]),
            )
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("task_id") == task_id for item in conflicts), conflicts)

    def test_recovery_audit_replay_is_rejected_and_detected_after_trigger_bypass(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one(
            "SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,)
        )
        with Store(self.config.db_path).connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "provenance|one-to-one|recovery"):
                conn.execute(
                    """INSERT INTO audit_log(ts,actor,action,entity,entity_id,details)
                       SELECT ts,actor,action,entity,entity_id,details FROM audit_log WHERE id=?""",
                    (record["audit_log_id"],),
                )
            conn.execute("DROP TRIGGER recovery_audit_provenance_require_canonical_insert")
            conn.execute(
                """INSERT INTO audit_log(ts,actor,action,entity,entity_id,details)
                   SELECT ts,actor,action,entity,entity_id,details FROM audit_log WHERE id=?""",
                (record["audit_log_id"],),
            )
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("task_id") == task_id for item in conflicts), conflicts)

    def test_recovery_event_replay_is_rejected_and_detected_after_trigger_bypass(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one(
            "SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,)
        )
        with Store(self.config.db_path).connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "provenance|one-to-one|recovery"):
                conn.execute(
                    """INSERT INTO execution_events(
                           created_at,available_at,event_type,entity_type,entity_id,payload,status,priority)
                       SELECT created_at,available_at,event_type,entity_type,entity_id,payload,status,priority
                       FROM execution_events WHERE id=?""",
                    (record["event_id"],),
                )
            conn.execute("DROP TRIGGER recovery_event_provenance_require_canonical_insert")
            conn.execute(
                """INSERT INTO execution_events(
                       created_at,available_at,event_type,entity_type,entity_id,payload,status,priority)
                   SELECT created_at,available_at,event_type,entity_type,entity_id,payload,status,priority
                   FROM execution_events WHERE id=?""",
                (record["event_id"],),
            )
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("task_id") == task_id for item in conflicts), conflicts)

    def test_deleted_recovery_triplet_is_detected_by_durable_anchor(self) -> None:
        from agent_company.assurance import AssuranceKernel

        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one(
            "SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,)
        )
        with Store(self.config.db_path).connect() as conn:
            for trigger in (
                "task_recovery_records_immutable_delete",
                "recovery_audit_provenance_immutable_delete",
                "recovery_event_provenance_immutable_delete",
            ):
                conn.execute(f"DROP TRIGGER {trigger}")
            conn.execute("DELETE FROM execution_events WHERE id=?", (record["event_id"],))
            conn.execute("DELETE FROM audit_log WHERE id=?", (record["audit_log_id"],))
            conn.execute("DELETE FROM task_recovery_records WHERE id=?", (record["id"],))
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("anchor") == "recovery_provenance" for item in conflicts), conflicts)
        self.assertTrue(any("recovery" in error.lower() for error in self.osys.validate()))
        integrity = AssuranceKernel(self.config).verify_integrity()
        self.assertEqual(integrity["status"], "integrity_conflict")
        self.assertTrue(
            any(item.get("anchor") == "recovery_provenance" for item in integrity["conflicts"]),
            integrity,
        )

    def test_wholesale_sqlite_recovery_erasure_is_detected_by_external_ledger(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        ledger = self.config.db_path.parent / "task-recovery-ledger.jsonl"
        self.assertTrue(ledger.is_file())
        self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)
        with Store(self.config.db_path).connect() as conn:
            for trigger in (
                "task_recovery_records_immutable_delete",
                "task_recovery_provenance_immutable_delete",
                "recovery_audit_provenance_immutable_delete",
                "recovery_event_provenance_immutable_delete",
            ):
                conn.execute(f"DROP TRIGGER {trigger}")
            conn.execute("DELETE FROM execution_events WHERE event_type='task.requeued'")
            conn.execute("DELETE FROM audit_log WHERE action='requeue_exhausted_task'")
            conn.execute("DELETE FROM task_recovery_provenance")
            conn.execute("DELETE FROM task_recovery_records")
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("anchor") == "recovery_external_ledger" for item in conflicts), conflicts)

    def test_recovery_fails_closed_when_external_ledger_append_fails(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}), \
             patch("agent_company.ops.append_recovery_ledger", side_effect=OSError("ledger unavailable")):
            with self.assertRaisesRegex(OSError, "ledger unavailable"):
                self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        task = Store(self.config.db_path).fetch_one("SELECT status FROM tasks WHERE id=?", (task_id,))
        record = Store(self.config.db_path).fetch_one("SELECT 1 FROM task_recovery_records WHERE task_id=?", (task_id,))
        self.assertEqual(task["status"], "blocked")
        self.assertIsNone(record)

    def test_noncanonical_and_duplicate_key_recovery_json_is_detected(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one("SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,))
        event = Store(self.config.db_path).fetch_one("SELECT payload FROM execution_events WHERE id=?", (record["event_id"],))
        parsed = json.loads(event["payload"])
        duplicate = '{"task_id":145,"task_id":145,' + json.dumps(parsed, sort_keys=True)[1:]
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TRIGGER recovery_event_provenance_immutable_update")
            conn.execute("UPDATE execution_events SET payload=? WHERE id=?", (duplicate, record["event_id"]))
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("task_id") == task_id for item in conflicts), conflicts)

    def test_provenance_schema_rejects_lexical_check_spoof(self) -> None:
        from agent_company.db import recovery_provenance_schema_status
        with Store(self.config.db_path).connect() as conn:
            for name in tuple(__import__("agent_company.db", fromlist=["RECOVERY_TRIGGER_DEFINITIONS"]).RECOVERY_TRIGGER_DEFINITIONS):
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute("ALTER TABLE task_recovery_provenance RENAME TO old_provenance")
            conn.execute("""CREATE TABLE task_recovery_provenance (
                recovery_record_id INTEGER PRIMARY KEY,
                task_id INTEGER NOT NULL,
                audit_log_id INTEGER NOT NULL UNIQUE,
                event_id INTEGER NOT NULL UNIQUE,
                record_integrity_signature TEXT NOT NULL,
                event_priority INTEGER NOT NULL DEFAULT 10,
                integrity_signature TEXT NOT NULL /* check(event_priority=10) */
            )""")
            conn.execute("DROP TABLE old_provenance")
        with Store(self.config.db_path).connect_readonly() as conn:
            self.assertEqual(recovery_provenance_schema_status(conn), "invalid")

    def test_replaced_recovery_row_is_detected_after_trigger_restore(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = dict(Store(self.config.db_path).fetch_one(
            "SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,)
        ))
        record["reason"] = "substituted recovery"
        columns = tuple(record)
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TRIGGER task_recovery_records_immutable_delete")
            conn.execute("DROP TRIGGER task_recovery_records_require_canonical_insert")
            conn.execute("DELETE FROM task_recovery_records WHERE id=?", (record["id"],))
            conn.execute(
                f"INSERT INTO task_recovery_records({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(record[column] for column in columns),
            )
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("task_id") == task_id for item in conflicts), conflicts)

    def test_duplicate_recovery_row_is_detected_after_trigger_restore(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = dict(Store(self.config.db_path).fetch_one(
            "SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,)
        ))
        record["id"] = int(record["id"]) + 100
        record["previous_task_state"] = json.dumps({"duplicate": True}, sort_keys=True)
        columns = tuple(record)
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DROP TRIGGER task_recovery_records_require_canonical_insert")
            conn.execute(
                f"INSERT INTO task_recovery_records({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                tuple(record[column] for column in columns),
            )
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("task_id") == task_id for item in conflicts), conflicts)

    def test_recovery_schema_rejects_partial_unique_index(self) -> None:
        from agent_company.db import recovery_schema_status

        self._replace_recovery_unique_constraint(
            """CREATE UNIQUE INDEX forged_recovery_unique
               ON task_recovery_records(task_id,previous_task_state)
               WHERE actor='CEO'"""
        )
        with Store(self.config.db_path).connect_readonly() as conn:
            self.assertEqual(recovery_schema_status(conn), "invalid")

    def test_recovery_schema_rejects_nonconstraint_unique_index(self) -> None:
        from agent_company.db import recovery_schema_status

        self._replace_recovery_unique_constraint(
            """CREATE UNIQUE INDEX forged_recovery_unique
               ON task_recovery_records(task_id,previous_task_state)"""
        )
        with Store(self.config.db_path).connect_readonly() as conn:
            self.assertEqual(recovery_schema_status(conn), "invalid")

    def test_recovery_event_priority_is_canonical_immutable_and_validated(self) -> None:
        task_id = self._task("登记受控Beta真实客户验证董事长决策事项", "Customer & Revenue", "commercial", 145)
        self._exhausted(task_id)
        with patch.object(self.osys, "_process_status", return_value={"alive": False, "reason": "process not found"}):
            self.osys.requeue_exhausted_task(task_id, "CEO", "verified death")
        record = Store(self.config.db_path).fetch_one(
            "SELECT * FROM task_recovery_records WHERE task_id=?", (task_id,)
        )
        event = Store(self.config.db_path).fetch_one(
            "SELECT priority FROM execution_events WHERE id=?", (record["event_id"],)
        )
        self.assertEqual(event["priority"], 10)
        with Store(self.config.db_path).connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable|provenance"):
                conn.execute("UPDATE execution_events SET priority=100 WHERE id=?", (record["event_id"],))
            conn.execute("DROP TRIGGER recovery_event_provenance_immutable_update")
            conn.execute(
                "UPDATE execution_events SET priority=100 WHERE id=?", (record["event_id"],)
            )
            Store._repair_recovery_triggers(conn)
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("task_id") == task_id for item in conflicts), conflicts)

    def test_recovery_conflicts_reject_weakened_same_name_schema(self) -> None:
        """Column-name compatibility is insufficient for governed recovery history."""
        from agent_company.db import RECOVERY_TRIGGER_DEFINITIONS

        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("ALTER TABLE task_recovery_records RENAME TO task_recovery_records_legacy")
            conn.execute(
                """CREATE TABLE task_recovery_records (
                    id INTEGER, task_id INTEGER, recovered_at TEXT, actor TEXT, reason TEXT,
                    scope TEXT, process_dead_proof TEXT, previous_task_state TEXT,
                    previous_execution_state TEXT, new_task_state TEXT, new_execution_state TEXT,
                    audit_log_id INTEGER, event_id INTEGER, integrity_signature TEXT
                )"""
            )
            for name in RECOVERY_TRIGGER_DEFINITIONS:
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            for name, definition in RECOVERY_TRIGGER_DEFINITIONS.items():
                conn.execute(definition)
            conn.execute("DROP TABLE task_recovery_records_legacy")
        with Store(self.config.db_path).connect_readonly() as conn:
            conflicts = recovery_conflicts(conn, self.config.db_path)
        self.assertTrue(any(item.get("anchor") == "recovery_schema" for item in conflicts), conflicts)

    def test_store_init_migrates_recognized_legacy_recovery_unique_layout(self) -> None:
        """The d0 recovery layout is recognized and upgraded to the canonical key."""
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("ALTER TABLE task_recovery_records RENAME TO task_recovery_records_legacy")
            conn.execute(
                """CREATE TABLE task_recovery_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    recovered_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    process_dead_proof TEXT NOT NULL,
                    previous_task_state TEXT NOT NULL,
                    previous_execution_state TEXT NOT NULL,
                    new_task_state TEXT NOT NULL,
                    new_execution_state TEXT NOT NULL,
                    audit_log_id INTEGER,
                    event_id INTEGER,
                    integrity_signature TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE RESTRICT,
                    UNIQUE(task_id, previous_execution_state)
                )"""
            )
            conn.execute("DROP TABLE task_recovery_records_legacy")
        Store(self.config.db_path).init()
        with Store(self.config.db_path).connect_readonly() as conn:
            indexes = conn.execute("PRAGMA index_list(task_recovery_records)").fetchall()
            unique_columns = [
                tuple(row[2] for row in conn.execute(f"PRAGMA index_info('{idx['name']}')").fetchall())
                for idx in indexes if idx["unique"]
            ]
        self.assertIn(("task_id", "previous_task_state"), unique_columns)


if __name__ == "__main__":
    unittest.main()
