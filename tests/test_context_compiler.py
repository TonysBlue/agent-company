from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.config import load_config
from agent_company.context_compiler import ContextCompiler
from agent_company.ops import CompanyOS


class ContextCompilerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[company]\nproduct_name = Test Company\ncycle_task_limit = 2\n\n"
            "[paths]\ndatabase = data/test.sqlite3\nchairman_inbox = data/chairman/inbox\n"
            "chairman_outbox = data/chairman/outbox\nartifacts = data/artifacts\nlogs = logs\n",
            encoding="utf-8",
        )
        self.old = Path.cwd()
        os.chdir(self.root)
        self.config = load_config()
        self.osys = CompanyOS(self.config)
        self.osys.init()
        with self.osys.store.connect() as conn:
            conn.execute("DELETE FROM tasks")
        self.task_id = int(self.osys.create_task(
            "CEO", "Product Engineer", "Implement context-aware feature", "product", 90,
            "Tests, evidence, and a task-branch commit pass.",
        )["task_id"])

    def tearDown(self) -> None:
        os.chdir(self.old)
        self.tmp.cleanup()

    def test_bundle_contains_common_rules_all_public_roles_private_role_and_history(self) -> None:
        compiler = ContextCompiler(self.config, context_root=Path("/home/tony/agent-company/company_context"))
        bundle = compiler.compile(
            self.task_id, generation=1, role="Product Engineer",
            repository={"id": "pixweave", "remote": "git@example/pix.git", "branch": "task/1", "canonical_test": "python -m unittest"},
        )
        self.assertEqual(bundle["schema_version"], "agent-company-task-context/v1")
        self.assertIn("constitution", bundle["company"])
        self.assertIn("Product Engineer", bundle["organization"]["public_roles"])
        self.assertIn("Customer & Revenue", bundle["organization"]["public_roles"])
        self.assertIn("private_playbook", bundle["role"])
        self.assertNotIn("private_playbook", bundle["organization"]["public_roles"]["Customer & Revenue"])
        self.assertEqual(bundle["history"]["role"], "Product Engineer")
        self.assertFalse(bundle["governance"]["external_action_authorized"])
        digest = bundle["provenance"]["bundle_sha256"]
        unsigned = {**bundle, "provenance": {**bundle["provenance"], "bundle_sha256": None}}
        canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(digest, hashlib.sha256(canonical.encode()).hexdigest())

    def test_materialize_writes_read_only_context_and_binds_execution_generation(self) -> None:
        compiler = ContextCompiler(self.config, context_root=Path("/home/tony/agent-company/company_context"))
        bundle = compiler.compile(
            self.task_id, generation=2, role="Product Engineer",
            repository={"id": "pixweave", "remote": "git@example/pix.git", "branch": "task/1", "canonical_test": "test"},
        )
        workspace = self.root / "workspace"
        workspace.mkdir()
        manifest = compiler.materialize(workspace, bundle)
        context_path = workspace / ".agent-company" / "TASK_CONTEXT.json"
        self.assertTrue(context_path.is_file())
        self.assertEqual(context_path.stat().st_mode & 0o222, 0)
        self.assertEqual(manifest["bundle_sha256"], bundle["provenance"]["bundle_sha256"])
        with self.osys.store.connect() as conn:
            row = conn.execute(
                "SELECT generation, bundle_sha256, status FROM task_contexts WHERE task_id=?", (self.task_id,)
            ).fetchone()
        self.assertEqual((row["generation"], row["bundle_sha256"], row["status"]), (2, manifest["bundle_sha256"], "active"))
        compiler.assert_current(self.task_id, 2, manifest["bundle_sha256"])
        with self.assertRaisesRegex(ValueError, "tampered"):
            compiler.assert_current(self.task_id, 2, "0" * 64)

    def test_context_becomes_stale_when_strategy_or_directive_version_changes(self) -> None:
        compiler = ContextCompiler(self.config, context_root=Path("/home/tony/agent-company/company_context"))
        bundle = compiler.compile(
            self.task_id, generation=3, role="Product Engineer",
            repository={"id": "pixweave", "remote": "git@example/pix.git", "branch": "task/1", "canonical_test": "test"},
        )
        digest = bundle["provenance"]["bundle_sha256"]
        with self.osys.store.connect() as conn:
            state = conn.execute("SELECT * FROM ceo_state_versions ORDER BY version DESC LIMIT 1").fetchone()
            conn.execute(
                """INSERT INTO ceo_state_versions(version, schema_version, created_at, source_kind, source_id,
                       strategy_json, assumptions_json, critical_path_json, active_directive_version)
                   VALUES (?, ?, ?, 'test', 'stale', ?, ?, ?, ?)""",
                (int(state["version"]) + 1, state["schema_version"], state["created_at"],
                 state["strategy_json"], state["assumptions_json"], state["critical_path_json"], state["active_directive_version"]),
            )
        with self.assertRaisesRegex(ValueError, "stale"):
            compiler.assert_current(self.task_id, 3, digest)

    def test_materialized_context_tamper_and_same_generation_recompile_are_rejected(self) -> None:
        compiler = ContextCompiler(self.config, context_root=Path("/home/tony/agent-company/company_context"))
        kwargs = {
            "generation": 4,
            "role": "Product Engineer",
            "repository": {"id": "pixweave", "remote": "git@example/pix.git", "branch": "task/1", "canonical_test": "test"},
        }
        bundle = compiler.compile(self.task_id, **kwargs)
        workspace = self.root / "tamper-workspace"
        workspace.mkdir()
        manifest = compiler.materialize(workspace, bundle)
        with self.assertRaisesRegex(ValueError, "immutable"):
            compiler.compile(self.task_id, **kwargs)
        context_path = workspace / ".agent-company" / "TASK_CONTEXT.json"
        context_path.chmod(0o644)
        payload = json.loads(context_path.read_text())
        payload["task"]["title"] = "tampered"
        context_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "tampered"):
            compiler.assert_current(self.task_id, 4, manifest["bundle_sha256"], workspace=workspace)

    def test_active_execution_generation_and_fencing_token_are_enforced(self) -> None:
        compiler = ContextCompiler(self.config, context_root=Path("/home/tony/agent-company/company_context"))
        self.osys.register_executor("ctx-test", "Product Engineer", "local", ["product"], 1)
        claim = self.osys.claim_task(self.task_id, "Product Engineer", executor_id="ctx-test", backend="local")
        with self.assertRaisesRegex(ValueError, "active fenced"):
            compiler.compile(
                self.task_id, generation=int(claim["generation"]) + 1, role="Product Engineer",
                repository={"id": "pixweave"}, fencing_token=str(claim["fencing_token"]),
            )


if __name__ == "__main__":
    unittest.main()
