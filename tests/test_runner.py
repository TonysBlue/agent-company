from __future__ import annotations

import json
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
            (evidence.parent / "CONTINUITY.json").write_text(json.dumps({
                "schema_version": "agent-company-continuity/v1",
                "role": "Customer & Revenue",
                "summary": "Prepared an internal approval package.",
                "verified_facts": ["Reviewable package exists"],
                "open_items": [],
                "project_summary": None,
                "project_decisions": [],
                "known_limits": [],
                "handoffs": [],
            }), encoding="utf-8")
            return FakeProcess()

        runner = ExecutionRunner(
            self.config, "test-runner", "Customer & Revenue",
            ["customer", "commercial", "gtm"], poll_seconds=0.01,
        )
        with patch.object(runner, "_ensure_workspace"):
            with patch.object(runner.contexts, "compile", return_value={"provenance": {"bundle_sha256": "a" * 64}}):
                with patch.object(runner.contexts, "materialize", return_value={"bundle_sha256": "a" * 64}):
                    with patch.object(runner.contexts, "assert_current"):
                        with patch.object(runner, "_publish_delivery", return_value={"repository": "pixweave", "branch": "task/1", "commit": "abc"}):
                            with patch("agent_company.runner.subprocess.Popen", side_effect=launch):
                                result = runner.run_once()

        self.assertEqual(result["status"], "done")
        task = self.osys.store.fetch_one("SELECT status FROM tasks WHERE id=?", (self.task_id,))
        self.assertEqual(task["status"], "done")
        execution = self.osys.inspect_execution(self.task_id)["execution"]
        self.assertEqual(execution["recovery_status"], "completed")
        executor = self.osys.store.fetch_one("SELECT status FROM executors WHERE executor_id='test-runner'")
        self.assertEqual(executor["status"], "healthy")

    def test_platform_tasks_are_owned_only_by_platform_roles(self) -> None:
        platform = self.osys.create_task(
            actor="CEO", owner="Company Platform Engineer",
            title="Maintain the company control plane", domain="platform", priority=70,
            acceptance_criteria="Control-plane tests and review evidence pass.",
        )
        self.assertEqual(platform["owner"], "Company Platform Engineer")
        with self.assertRaisesRegex(ValueError, "not authorized"):
            self.osys.create_task(
                actor="CEO", owner="Product Engineer",
                title="Wrongly route platform work", domain="governance", priority=60,
                acceptance_criteria="Must not be accepted.",
            )

    def test_runner_creates_remote_backed_task_branch_workspace(self) -> None:
        runner = ExecutionRunner(
            self.config, "test-runner", "Customer & Revenue", ["customer"], poll_seconds=0.01,
        )
        repository = runner.registry.get("pixweave", role="Customer & Revenue")
        workdir = self.root / "workspaces" / "customer-revenue" / "task-42-pixweave"
        with patch("agent_company.runner.subprocess.run") as run:
            runner._ensure_workspace(repository, workdir)
        self.assertEqual(run.call_count, 2)
        clone_args = run.call_args_list[0].args[0]
        self.assertEqual(clone_args[0:4], ["git", "clone", "--origin", "origin"])
        self.assertIn("--branch", clone_args)
        self.assertIn(repository.default_branch, clone_args)
        self.assertIn(repository.remote, clone_args)
        self.assertEqual(run.call_args_list[1].args[0][-2:], ["-b", "task/42"])

    def test_runner_launch_uses_external_bwrap_isolation_and_sanitized_environment(self) -> None:
        runner = ExecutionRunner(
            self.config, "test-runner", "Product Engineer", ["product"], poll_seconds=0.01,
        )
        repository = runner.registry.get("pixweave", role="Product Engineer")
        workdir = self.root / "task-42-pixweave"
        evidence = self.root / "evidence"
        log = self.root / "runner.log"
        workdir.mkdir()
        (workdir / ".git" / "info").mkdir(parents=True)
        (workdir / ".agent-company").mkdir()
        evidence.mkdir()
        process = FakeProcess()
        with patch.object(runner, "_prepare_codex_home"):
            with patch("agent_company.runner.subprocess.Popen", return_value=process) as popen:
                runner._launch(
                    {"id": 42, "title": "x", "acceptance_criteria": "y"},
                    repository, workdir, evidence, log,
                    {"bundle_sha256": "a" * 64},
                )
        command = popen.call_args.args[0]
        env = popen.call_args.kwargs["env"]
        self.assertEqual(command[0], "bwrap")
        self.assertIn("--tmpfs", command)
        self.assertIn("/home", command)
        self.assertNotIn("/home/tony/.ssh", command)
        self.assertNotIn("SSH_AUTH_SOCK", env)
        self.assertEqual(env["SSL_CERT_FILE"], "/etc/ssl/certs/ca-certificates.crt")

    def test_existing_workspace_requires_expected_origin_branch_and_clean_tree(self) -> None:
        runner = ExecutionRunner(
            self.config, "test-runner", "Product Engineer", ["product"], poll_seconds=0.01,
        )
        repository = runner.registry.get("pixweave", role="Product Engineer")
        workdir = self.root / "task-42-pixweave"
        (workdir / ".git").mkdir(parents=True)
        with patch.object(runner, "_git", side_effect=["git@example.invalid:wrong.git", "task/42", ""]):
            with self.assertRaisesRegex(ValueError, "origin mismatch"):
                runner._ensure_workspace(repository, workdir)
        with patch.object(runner, "_git", side_effect=[repository.remote, "main", ""]):
            with self.assertRaisesRegex(ValueError, "branch mismatch"):
                runner._ensure_workspace(repository, workdir)
        with patch.object(runner, "_git", side_effect=[repository.remote, "task/42", " M file.py"]):
            with self.assertRaisesRegex(ValueError, "dirty"):
                runner._ensure_workspace(repository, workdir)

    def test_runner_rejects_invalid_continuity_before_pushing_delivery(self) -> None:
        runner = ExecutionRunner(
            self.config, "test-runner", "Customer & Revenue", ["customer"], poll_seconds=0.01,
        )
        evidence_dir = self.root / "evidence" / f"task-{self.task_id}"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "CONTINUITY.json").write_text("{}", encoding="utf-8")
        with patch.object(runner, "_ensure_workspace"):
            with patch.object(runner.contexts, "compile", return_value={"provenance": {"bundle_sha256": "a" * 64}}):
                with patch.object(runner.contexts, "materialize", return_value={"bundle_sha256": "a" * 64}):
                    with patch.object(runner.contexts, "assert_current"):
                        with patch.object(runner, "_publish_delivery") as publish:
                            with patch("agent_company.runner.subprocess.Popen", return_value=FakeProcess()):
                                result = runner.run_once()
        self.assertEqual(result["status"], "failed")
        publish.assert_not_called()

    def test_runner_ignores_blocked_work_instead_of_crash_looping(self) -> None:
        with self.osys.store.connect() as conn:
            conn.execute("UPDATE tasks SET status='blocked', blocked_reason='operator review' WHERE id=?", (self.task_id,))
        runner = ExecutionRunner(
            self.config, "test-runner", "Customer & Revenue", ["customer"], poll_seconds=0.01,
        )
        with patch("agent_company.runner.subprocess.Popen") as launch:
            result = runner.run_once()
        self.assertEqual(result["status"], "idle")
        launch.assert_not_called()

    def test_runner_records_failure_when_child_exits_without_evidence(self) -> None:
        process = FakeProcess()
        process.returncode = 2
        runner = ExecutionRunner(
            self.config, "test-runner", "Customer & Revenue",
            ["customer"], poll_seconds=0.01,
        )
        with patch.object(runner, "_ensure_workspace"):
            with patch("agent_company.runner.subprocess.Popen", return_value=process):
                result = runner.run_once()

        self.assertEqual(result["status"], "failed")
        execution = self.osys.inspect_execution(self.task_id)["execution"]
        self.assertEqual(execution["recovery_status"], "failed")


if __name__ == "__main__":
    unittest.main()
