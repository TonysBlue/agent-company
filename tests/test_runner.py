from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_company.config import load_config
from agent_company.ops import CompanyOS
from agent_company.runner import ExecutionRunner


class FakeProcess:
    pid = 43210
    returncode = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class ExecutionRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[company]\nproduct_name = Test PixWeave\ncycle_task_limit = 2\n\n"
            "[paths]\ndatabase = data/test.sqlite3\nchairman_inbox = data/chairman/inbox\n"
            "chairman_outbox = data/chairman/outbox\nartifacts = data/artifacts\nlogs = logs\n"
            "\n[backend]\nname = local\ncodex_enabled = false\n",
            encoding="utf-8",
        )
        self.old = Path.cwd()
        import os
        os.chdir(self.root)
        self.config = load_config()
        self.osys = CompanyOS(self.config)
        self.osys.init()
        with self.osys.store.connect() as conn:
            conn.execute("DELETE FROM tasks")
        self.task_id = self.osys.create_task(
            "CEO", "Customer & Revenue", "Prepare approval package", "customer", 90,
            "A reviewable internal approval package exists.",
        )["task_id"]

    def tearDown(self) -> None:
        import os
        os.chdir(self.old)
        self.tmp.cleanup()

    def test_runner_registers_claims_heartbeats_and_completes_verified_evidence(self) -> None:
        evidence = self.root / "evidence" / f"task-{self.task_id}" / "approval.md"
        evidence.parent.mkdir(parents=True)

        def launch(*args, **kwargs):
            evidence.write_text("reviewable approval package\n", encoding="utf-8")
            return FakeProcess()

        runner = ExecutionRunner(
            self.config, "test-runner", "Customer & Revenue",
            ["customer", "commercial", "gtm"], poll_seconds=0.01,
        )
        with patch("agent_company.runner.subprocess.Popen", side_effect=launch):
            result = runner.run_once()

        self.assertEqual(result["status"], "done")
        task = self.osys.store.fetch_one("SELECT status FROM tasks WHERE id=?", (self.task_id,))
        self.assertEqual(task["status"], "done")
        execution = self.osys.inspect_execution(self.task_id)["execution"]
        self.assertEqual(execution["recovery_status"], "completed")
        executor = self.osys.store.fetch_one("SELECT status FROM executors WHERE executor_id='test-runner'")
        self.assertEqual(executor["status"], "healthy")

    def test_runner_records_failure_when_child_exits_without_evidence(self) -> None:
        process = FakeProcess()
        process.returncode = 2
        runner = ExecutionRunner(
            self.config, "test-runner", "Customer & Revenue",
            ["customer"], poll_seconds=0.01,
        )
        with patch("agent_company.runner.subprocess.Popen", return_value=process):
            result = runner.run_once()

        self.assertEqual(result["status"], "failed")
        execution = self.osys.inspect_execution(self.task_id)["execution"]
        self.assertEqual(execution["recovery_status"], "failed")


if __name__ == "__main__":
    unittest.main()
