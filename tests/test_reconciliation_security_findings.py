from __future__ import annotations

import json
import os
import subprocess
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_company.assurance import AssuranceKernel
from agent_company.config import load_config
from agent_company.dashboard import build_snapshot, _recovery_status_label, _task_status_label
from agent_company.db import Store
from agent_company.ops import CompanyOS
from agent_company.integrity import reconciliation_signature


class ReconciliationSecurityFindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[paths]\ndatabase=data/company.sqlite3\nchairman_inbox=data/inbox\nchairman_outbox=data/outbox\nartifacts=data/artifacts\nlogs=logs\n",
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

    def _task(self) -> int:
        return self.osys.create_task(
            "CEO", "Product Engineer", "reconciliation security", "engineering", 90,
            "must remain uncompleted",
        )["task_id"]

    def _git_pair(self, repo: Path) -> tuple[str, str, str, str]:
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        (repo / "delivery.txt").write_text("accepted\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "delivery.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "accepted"], check=True)
        source = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        source_tree = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], text=True).strip()
        (repo / "evidence.txt").write_text("evidence\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "evidence.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "evidence"], check=True)
        evidence = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        evidence_tree = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"], text=True).strip()
        return source, source_tree, evidence, evidence_tree

    def _args(self, task_id: int, repo: Path) -> tuple[object, ...]:
        (repo / "config").mkdir(exist_ok=True)
        (repo / "config" / "repositories.json").write_text(json.dumps({
            "repositories": [{"id": "agent-company", "local_path": str(repo)}]
        }), encoding="utf-8")
        return (task_id, "CEO", *self._git_pair(repo), {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}, "accepted")

    def _make_exhausted(self, task_id: int) -> None:
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET status='blocked' WHERE id=?", (task_id,))
            conn.execute(
                "INSERT INTO task_executions(task_id,executor_id,backend,claimed_at,heartbeat_at,lease_expires_at,attempt_count,max_attempts,recovery_status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, "dead", "codex", "t", "t", "t", 3, 3, "exhausted", "t", "t"),
            )

    def test_rejects_noncanonical_repository_identity(self) -> None:
        task_id = self._task(); self._make_exhausted(task_id)
        repo = self.root / "untrusted-repository"; repo.mkdir()
        args = self._args(task_id, repo)
        object.__setattr__(self.config, "workspace", repo)
        (repo / "config").mkdir(exist_ok=True)
        (repo / "config" / "repositories.json").write_text(json.dumps({"repositories": [{"id": "agent-company", "local_path": str(self.root / "canonical")}]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "canonical|identity|repository"):
            self.osys.reconcile_task(*args)

    def test_idempotent_call_revalidates_git_objects_and_pairs(self) -> None:
        task_id = self._task(); self._make_exhausted(task_id)
        repo = self.root / "repo"; repo.mkdir(); args = self._args(task_id, repo)
        object.__setattr__(self.config, "workspace", repo)
        self.osys.reconcile_task(*args)
        with patch.object(self.osys, "_git_object_type", return_value=None):
            with self.assertRaisesRegex(ValueError, "available exact Git"):
                self.osys.reconcile_task(*args)

    def test_git_verification_disables_replacements_and_rejects_semantic_environment(self) -> None:
        task_id = self._task(); self._make_exhausted(task_id)
        repo = self.root / "repo"; repo.mkdir(); args = self._args(task_id, repo)
        object.__setattr__(self.config, "workspace", repo)
        with patch.dict(os.environ, {"GIT_OBJECT_DIRECTORY": str(repo / ".git" / "objects")}, clear=False):
            with self.assertRaisesRegex(ValueError, "Git.*environment|environment.*Git"):
                self.osys.reconcile_task(*args)

    def test_reconciliation_insert_requires_signed_canonical_row(self) -> None:
        task_id = self._task()
        values = {
            "task_id": task_id, "reconciled_at": "t", "actor": "CEO",
            "accepted_source_commit": "a" * 40, "accepted_source_tree": "b" * 40,
            "evidence_tip_commit": "c" * 40, "evidence_tip_tree": "d" * 40,
            "independent_verdict": json.dumps({"Critical": 0, "High": 0, "Medium": 0, "Low": 0}, sort_keys=True),
            "reason": "x", "previous_task_state": "{}", "previous_execution_state": "{}",
        }
        with Store(self.config.db_path).connect() as conn:
            with self.assertRaisesRegex(sqlite3.IntegrityError, "reconciliation.*integrity|signature"):
                conn.execute(
                    "INSERT INTO task_reconciliations(task_id,reconciled_at,actor,accepted_source_commit,accepted_source_tree,evidence_tip_commit,evidence_tip_tree,independent_verdict,reason,previous_task_state,previous_execution_state,integrity_signature) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, "t", "CEO", "a", "b", "c", "d", values["independent_verdict"], "x", "{}", "{}", "forged"),
                )
            conn.execute("DROP TRIGGER task_reconciliations_require_canonical_insert")
            conn.execute(
                "INSERT INTO task_reconciliations(task_id,reconciled_at,actor,accepted_source_commit,accepted_source_tree,evidence_tip_commit,evidence_tip_tree,independent_verdict,reason,previous_task_state,previous_execution_state,integrity_signature) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (*values.values(), reconciliation_signature(self.config.db_path, values)),
            )
        self.osys._init_reconciliation_schema()
        with Store(self.config.db_path).connect() as conn:
            trigger = conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='task_reconciliations_require_canonical_insert'").fetchone()
            self.assertIn("assurance_reconciliation_signature_valid", trigger["sql"])

    def test_validate_and_integrity_report_reconciliation_state_audit_event_and_triggers(self) -> None:
        task_id = self._task(); self._make_exhausted(task_id)
        repo = self.root / "repo"; repo.mkdir(); args = self._args(task_id, repo)
        object.__setattr__(self.config, "workspace", repo)
        self.osys.reconcile_task(*args)
        with Store(self.config.db_path).connect() as conn:
            conn.execute("DELETE FROM audit_log WHERE action='reconcile_task_execution'")
            conn.execute("DELETE FROM execution_events WHERE event_type='task.reconciled'")
            conn.execute("DROP TRIGGER task_reconciliations_immutable_update")
        errors = self.osys.validate()
        self.assertTrue(any("reconcil" in error.lower() for error in errors))
        integrity = AssuranceKernel(self.config).verify_integrity()
        self.assertEqual(integrity["status"], "integrity_conflict")

    def test_non_ceo_reconciliation_does_not_initialize_or_migrate(self) -> None:
        with patch.object(self.osys, "init", side_effect=AssertionError("init must not run")):
            with self.assertRaisesRegex(ValueError, "only CEO"):
                self.osys.reconcile_task(1, "Product Engineer", "0" * 40, "1" * 40, "2" * 40, "3" * 40, {"Critical": 0, "High": 0, "Medium": 0, "Low": 0}, "x")

    def test_dashboard_labels_and_counts_make_reconciled_terminal_semantics_explicit(self) -> None:
        task_id = self._task()
        with Store(self.config.db_path).connect() as conn:
            conn.execute("UPDATE tasks SET status='reconciled' WHERE id=?", (task_id,))
            conn.execute("INSERT INTO task_executions(task_id,executor_id,backend,claimed_at,heartbeat_at,lease_expires_at,attempt_count,max_attempts,recovery_status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (task_id, "dead", "codex", "t", "t", "t", 3, 3, "reconciled", "t", "t"))
        snapshot = build_snapshot(self.config)
        self.assertEqual(snapshot["management"]["task_counts_by_status"]["reconciled"], 1)
        self.assertIn("未完成", _task_status_label("reconciled"))
        self.assertIn("对账", _recovery_status_label("reconciled"))


if __name__ == "__main__":
    unittest.main()
