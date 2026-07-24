from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.config import load_config
from agent_company.context_knowledge import ContextKnowledge
from agent_company.ops import CompanyOS


class ContextKnowledgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[company]\nproduct_name = Test\ncycle_task_limit = 2\n\n"
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
            "CEO", "Product Engineer", "Ship feature", "product", 90, "Verified feature exists."
        )["task_id"])
        self.knowledge = ContextKnowledge(self.config)

    def tearDown(self) -> None:
        os.chdir(self.old)
        self.tmp.cleanup()

    def test_continuity_project_history_and_handoff_are_versioned_and_audited(self) -> None:
        continuity = self.knowledge.update_role_continuity(
            role="Product Engineer", summary="Implemented feature; review remains.",
            verified_facts=["Canonical tests passed"], open_items=["Independent review"],
            source_task_id=self.task_id, actor="Product Engineer",
        )
        self.assertEqual(continuity["version"], 1)
        updated = self.knowledge.update_role_continuity(
            role="Product Engineer", summary="Review accepted.", verified_facts=["Review passed"],
            open_items=[], source_task_id=self.task_id, actor="CEO",
        )
        self.assertEqual(updated["version"], 2)
        project = self.knowledge.update_project_history(
            repository_id="pixweave", summary="Feature delivered.", decisions=["Use local-first architecture"],
            known_limits=["No production launch"], actor="CEO",
        )
        self.assertEqual(project["version"], 1)
        handoff = self.knowledge.create_handoff(
            task_id=self.task_id, from_role="Product Engineer", to_role="Independent Quality Reviewer",
            handoff_type="review", summary="Review task branch and evidence.",
            artifact_refs=["task/1", "evidence/report.json"], decision_needed="approve_or_reject",
        )
        accepted = self.knowledge.transition_handoff(handoff["handoff_id"], "Independent Quality Reviewer", "accepted")
        closed = self.knowledge.transition_handoff(handoff["handoff_id"], "Independent Quality Reviewer", "closed")
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(closed["status"], "closed")
        audit = self.osys.store.fetch_all("SELECT action FROM audit_log ORDER BY id")
        actions = {row["action"] for row in audit}
        self.assertTrue({"update_role_continuity", "update_project_history", "create_handoff", "transition_handoff"} <= actions)

    def test_ingest_structured_continuity_rejects_wrong_role_and_unknown_keys(self) -> None:
        path = self.root / "CONTINUITY.json"
        path.write_text(json.dumps({
            "schema_version": "agent-company-continuity/v1",
            "role": "Customer & Revenue",
            "summary": "Wrong role",
            "verified_facts": [], "open_items": [], "project_summary": None,
            "project_decisions": [], "known_limits": [], "handoffs": [],
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "role mismatch"):
            self.knowledge.ingest_continuity(path, expected_role="Product Engineer", task_id=self.task_id, repository_id="pixweave")
        payload = json.loads(path.read_text())
        payload["role"] = "Product Engineer"
        payload["unexpected"] = True
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unknown or missing"):
            self.knowledge.ingest_continuity(path, expected_role="Product Engineer", task_id=self.task_id, repository_id="pixweave")

    def test_ingest_rolls_back_all_knowledge_when_late_handoff_is_invalid(self) -> None:
        path = self.root / "ROLLBACK.json"
        path.write_text(json.dumps({
            "schema_version": "agent-company-continuity/v1", "role": "Product Engineer",
            "summary": "Would otherwise persist", "verified_facts": ["x"], "open_items": [],
            "project_summary": "Would otherwise persist", "project_decisions": [], "known_limits": [],
            "handoffs": [{"to_role": "Unknown Role", "handoff_type": "review", "summary": "bad", "artifact_refs": [], "decision_needed": None}],
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "target role"):
            self.knowledge.ingest_continuity(path, expected_role="Product Engineer", task_id=self.task_id, repository_id="pixweave")
        self.assertIsNone(self.osys.store.fetch_one("SELECT * FROM role_continuity WHERE role='Product Engineer'"))
        self.assertIsNone(self.osys.store.fetch_one("SELECT * FROM project_history WHERE repository_id='pixweave'"))
        self.assertEqual(self.osys.store.fetch_all("SELECT * FROM handoffs"), [])

    def test_role_cannot_forge_continuity_or_handoff_for_another_roles_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "authorized"):
            self.knowledge.update_role_continuity(
                role="Product Engineer", summary="forged", verified_facts=[], open_items=[],
                source_task_id=self.task_id, actor="Customer & Revenue",
            )
        with self.assertRaisesRegex(ValueError, "does not own"):
            self.knowledge.create_handoff(
                task_id=self.task_id, from_role="Customer & Revenue", to_role="Product Engineer",
                handoff_type="review", summary="forged", artifact_refs=[], decision_needed=None,
            )


if __name__ == "__main__":
    unittest.main()
