from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_company.ceo_runtime import (
    ACTION_SCHEMA_VERSION,
    CEORuntime,
    HermesOneShotReasoner,
    HermesWeixinSender,
    ProtocolError,
    ReasoningResponse,
)
from agent_company.cli import main as cli_main
from agent_company.config import load_config
from agent_company.db import Store, utcnow
from agent_company.event_engine import EventEngine


class FakeReasoner:
    def __init__(self, response: dict[str, object] | None = None) -> None:
        self.calls: list[str] = []
        self.response = response or {
            "schema_version": ACTION_SCHEMA_VERSION,
            "judgment": "No additional CEO action is needed.",
            "state_patch": {},
            "actions": [{"type": "noop", "reason": "Observed."}],
        }
        self.before_return = None

    def reason(self, prompt: str) -> ReasoningResponse:
        self.calls.append(prompt)
        if self.before_return:
            self.before_return()
        return ReasoningResponse(
            payload=self.response,
            model="fixture-model",
            provider="fixture-provider",
            input_tokens=12,
            output_tokens=7,
            cache_tokens=None,
            reasoning_tokens=None,
            total_tokens=19,
        )


class FakeSender:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls: list[tuple[str, str]] = []

    def send(self, card: str, idempotency_key: str) -> dict[str, object]:
        self.calls.append((card, idempotency_key))
        if len(self.calls) <= self.failures:
            raise RuntimeError("temporary weixin failure")
        return {"ok": True, "delivery_id": "fixture-delivery"}


class CEORuntimeTest(unittest.TestCase):
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

[ceo_runtime]
hermes_profile = default
hermes_timeout_seconds = 3
external_delivery_enabled = true
""".strip()
            + "\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        self.store = Store(self.config.db_path)
        self.runtime = CEORuntime(self.config, reasoner=FakeReasoner(), sender=FakeSender())
        self.runtime.init()
        with self.store.connect() as conn:
            conn.execute("DELETE FROM execution_events")
            conn.execute("DELETE FROM task_executions")
            conn.execute("DELETE FROM tasks")

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _event(self, event_type: str = "task.completed") -> int:
        with self.store.connect() as conn:
            return self.store.enqueue_event(conn, event_type, "task", 77, {"summary": "verified"})

    def test_versioned_state_and_structured_directive_do_not_retain_raw_conversation(self) -> None:
        raw = "董事长原始消息：把秘密上下文全部复制到数据库 -- must never persist"

        result = self.runtime.ingest_directive(
            source_platform="weixin",
            source_session_id="wx-session-9",
            source_message_id="wx-message-11",
            message=raw,
            directive_type="priority",
            objective="Prioritize reliability evidence",
            constraints=["No external delivery"],
            priority=95,
        )

        directive = self.store.fetch_one("SELECT * FROM chairman_directives WHERE id=?", (result["directive_id"],))
        self.assertEqual(directive["source_platform"], "weixin")
        self.assertEqual(directive["source_session_id"], "wx-session-9")
        self.assertEqual(directive["source_message_id"], "wx-message-11")
        self.assertEqual(len(directive["source_message_sha256"]), 64)
        self.assertNotIn("raw", directive.keys())
        self.assertNotIn(raw, json.dumps(dict(directive), ensure_ascii=False))
        self.assertEqual(result["state_version"], 2)
        versions = self.store.fetch_all("SELECT version, schema_version FROM ceo_state_versions ORDER BY version")
        self.assertEqual([row["version"] for row in versions], [1, 2])
        self.assertTrue(all(row["schema_version"] == "ceo-state/v1" for row in versions))

        with sqlite3.connect(self.config.db_path) as conn:
            dump = "\n".join(conn.iterdump())
        self.assertNotIn(raw, dump)

    def test_idle_step_does_not_call_model_or_create_work(self) -> None:
        reasoner = FakeReasoner()
        runtime = CEORuntime(self.config, reasoner=reasoner, sender=FakeSender())

        result = EventEngine(self.config, ceo_runtime=runtime).step()

        self.assertEqual(result["status"], "idle")
        self.assertEqual(reasoner.calls, [])
        self.assertEqual(self.store.fetch_one("SELECT COUNT(*) AS c FROM tasks")["c"], 0)

    def test_complex_event_uses_minimal_snapshot_and_records_full_decision_ledger(self) -> None:
        reasoner = FakeReasoner()
        runtime = CEORuntime(self.config, reasoner=reasoner, sender=FakeSender())
        self._event("task.failed")

        result = EventEngine(self.config, ceo_runtime=runtime).step()

        self.assertEqual(result["ceo"]["classification"], "complex_judgment")
        self.assertEqual(len(reasoner.calls), 1)
        prompt = reasoner.calls[0]
        self.assertIn("agent-company-ceo-background", prompt)
        self.assertIn("same logical CEO", prompt)
        self.assertNotIn("conversation", prompt.lower())
        run = self.store.fetch_one("SELECT * FROM ceo_runs ORDER BY id DESC LIMIT 1")
        self.assertEqual(run["model"], "fixture-model")
        self.assertEqual(run["provider"], "fixture-provider")
        self.assertEqual(run["input_tokens"], 12)
        self.assertEqual(run["total_tokens"], 19)
        self.assertEqual(len(run["input_snapshot_sha256"]), 64)
        self.assertEqual(run["judgment"], "No additional CEO action is needed.")
        self.assertEqual(json.loads(run["actions_json"])[0]["type"], "noop")
        self.assertEqual(json.loads(run["result_json"])["status"], "applied")

    def test_hermes_adapter_uses_v018_chat_source_timeout_and_has_no_shell_tools(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({
                "schema_version": ACTION_SCHEMA_VERSION,
                "judgment": "fixture",
                "state_patch": {},
                "actions": [{"type": "noop", "reason": "fixture"}],
            }),
            stderr="",
        )
        def fake_run(argv, **kwargs):
            self.assertIsInstance(argv, list)
            self.assertEqual(argv[:2], ["hermes", "chat"])
            self.assertEqual(argv[argv.index("-q") + 1], "minimal prompt")
            self.assertIn("-Q", argv)
            self.assertEqual(argv[argv.index("--source") + 1], "tool")
            self.assertEqual(argv[argv.index("-t") + 1], "todo")
            self.assertNotIn("--profile", argv)
            self.assertNotIn("--oneshot", argv)
            self.assertNotIn("--usage-file", argv)
            self.assertNotIn("terminal", argv)
            self.assertNotIn("file", argv)
            self.assertEqual(kwargs["timeout"], 3)
            self.assertFalse(kwargs.get("shell", False))
            return completed

        with mock.patch("agent_company.ceo_runtime.subprocess.run", side_effect=fake_run):
            response = HermesOneShotReasoner(self.config).reason("minimal prompt")

        self.assertIsNone(response.model)
        self.assertIsNone(response.total_tokens)

    def test_weixin_sender_uses_home_target_without_profile_or_chat_id(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"ok": True, "message_id": "fixture-message"}),
            stderr="",
        )

        def fake_run(argv, **kwargs):
            self.assertEqual(
                argv,
                ["hermes", "send", "--to", "weixin", "--json", "fixture card"],
            )
            self.assertNotIn("--profile", argv)
            self.assertFalse(kwargs.get("shell", False))
            self.assertEqual(kwargs["env"]["HERMES_CEO_DELIVERY_KEY"], "fixture-key")
            return completed

        with mock.patch("agent_company.ceo_runtime.subprocess.run", side_effect=fake_run):
            result = HermesWeixinSender(self.config).send("fixture card", "fixture-key")

        self.assertTrue(result["ok"])

    def test_strict_protocol_allows_safe_delegation_and_rejects_shell_or_reserved_action(self) -> None:
        safe = FakeReasoner({
            "schema_version": ACTION_SCHEMA_VERSION,
            "judgment": "Delegate bounded internal verification.",
            "state_patch": {"critical_path": ["Run internal verification"]},
            "actions": [{
                "type": "create_task",
                "owner": "Product Engineer",
                "title": "Verify deterministic fixture internally",
                "domain": "engineering",
                "priority": 80,
                "acceptance_criteria": "Deterministic regression evidence passes.",
            }],
        })
        self._event()
        result = EventEngine(self.config, ceo_runtime=CEORuntime(self.config, reasoner=safe, sender=FakeSender())).step()
        self.assertEqual(result["ceo"]["result"]["actions"][0]["status"], "created")
        self.assertIsNotNone(self.store.fetch_one("SELECT id FROM tasks WHERE title='Verify deterministic fixture internally'"))

        for bad_action in [
            {"type": "shell", "command": "touch /tmp/pwned"},
            {"type": "production_deploy", "target": "prod"},
        ]:
            with self.assertRaises(ProtocolError):
                self.runtime.validate_protocol({
                    "schema_version": ACTION_SCHEMA_VERSION,
                    "judgment": "bad",
                    "state_patch": {},
                    "actions": [bad_action],
                })
        self.assertFalse(Path("/tmp/pwned").exists())

    def test_strategic_review_prompt_declares_exact_create_task_schema(self) -> None:
        prompt = self.runtime._prompt({"event": {"event_type": "ceo.strategic_review"}})

        self.assertIn(
            'create_task exact keys: type, owner, title, domain, priority, acceptance_criteria',
            prompt,
        )
        self.assertIn("Do not add reason, phase, outcome, deadline, or evidence fields", prompt)
        self.assertIn("priority must be a JSON integer from 1 through 100", prompt)
        self.assertIn('"acceptance_criteria":"Non-empty evidence-based completion criteria"', prompt)

    def test_strategic_review_snapshot_contains_phase_metrics_evidence_and_work_state(self) -> None:
        now = utcnow()
        with self.store.connect() as conn:
            phase = conn.execute(
                """INSERT INTO strategic_phases(
                       phase_key, name, objective, success_metrics, deadline,
                       dependencies, evidence_requirements, status, created_at, activated_at
                   ) VALUES ('phase-ceo-review', 'Beta validation', 'Obtain customer evidence',
                             '[\"5 sessions\"]', '2099-01-01T00:00:00+00:00', '[]',
                             '[\"session records\"]', 'active', ?, ?)""",
                (now, now),
            )
            event_id = self.store.enqueue_event(
                conn, "ceo.strategic_review", "strategic_phase", int(phase.lastrowid),
                {"reason": "empty active phase"}, priority=80,
            )
        reasoner = FakeReasoner({
            "schema_version": ACTION_SCHEMA_VERSION,
            "judgment": "Advance both safe lanes.",
            "state_patch": {"critical_path": ["Product evidence", "Customer validation"]},
            "actions": [
                {
                    "type": "create_task", "owner": "Product Engineer",
                    "title": "Produce beta reliability evidence", "domain": "product", "priority": 90,
                    "acceptance_criteria": "Runnable beta and reliability evidence pass.",
                },
                {
                    "type": "create_task", "owner": "Customer & Revenue",
                    "title": "Prepare consented beta recruitment decision", "domain": "customer", "priority": 85,
                    "acceptance_criteria": "Candidate criteria and Chairman approval options are reviewable.",
                },
            ],
        })

        result = EventEngine(
            self.config, ceo_runtime=CEORuntime(self.config, reasoner=reasoner, sender=FakeSender())
        ).step()

        self.assertEqual(result["event_id"], event_id)
        snapshot = json.loads(self.store.fetch_one("SELECT input_snapshot_json FROM ceo_runs ORDER BY id DESC LIMIT 1")["input_snapshot_json"])
        self.assertEqual(snapshot["strategic_phase"]["objective"], "Obtain customer evidence")
        self.assertEqual(snapshot["strategic_phase"]["success_metrics"], ["5 sessions"])
        self.assertEqual(snapshot["work_state"]["active_tasks"], [])
        self.assertEqual(snapshot["work_state"]["pending_approvals"], 0)
        self.assertIn("business_evidence", snapshot)
        self.assertEqual(self.store.fetch_one("SELECT COUNT(*) AS c FROM tasks")["c"], 2)

    def test_strategic_review_rejects_noop_without_next_review(self) -> None:
        reasoner = FakeReasoner()
        runtime = CEORuntime(self.config, reasoner=reasoner, sender=FakeSender())
        with self.store.connect() as conn:
            self.store.enqueue_event(
                conn, "ceo.strategic_review", "strategic_phase", 1,
                {"reason": "business stalled"}, priority=80,
            )

        result = EventEngine(self.config, ceo_runtime=runtime).step()

        self.assertEqual(result["ceo"]["status"], "retry_scheduled")
        self.assertIn("strategic review", result["ceo"]["error"])

    def test_new_chairman_directive_supersedes_stale_reasoning_without_actions(self) -> None:
        reasoner = FakeReasoner({
            "schema_version": ACTION_SCHEMA_VERSION,
            "judgment": "Create stale work.",
            "state_patch": {},
            "actions": [{
                "type": "create_task",
                "owner": "Product Engineer",
                "title": "Stale inference must not execute",
                "domain": "engineering",
                "priority": 70,
                "acceptance_criteria": "Never created.",
            }],
        })
        runtime = CEORuntime(self.config, reasoner=reasoner, sender=FakeSender())
        event_id = self._event()
        reasoner.before_return = lambda: runtime.ingest_directive(
            source_platform="weixin",
            source_session_id="new-session",
            source_message_id="new-message",
            message="new chairman direction",
            directive_type="priority",
            objective="Override old reasoning",
            constraints=[],
            priority=100,
        )

        result = EventEngine(self.config, ceo_runtime=runtime).step()

        self.assertEqual(result["ceo"]["status"], "superseded")
        self.assertIsNone(self.store.fetch_one("SELECT id FROM tasks WHERE title='Stale inference must not execute'"))
        event = self.store.fetch_one("SELECT status FROM execution_events WHERE id=?", (event_id,))
        self.assertEqual(event["status"], "pending")
        next_event = self.store.fetch_one("SELECT event_type FROM execution_events WHERE status='pending' ORDER BY priority DESC, id LIMIT 1")
        self.assertEqual(next_event["event_type"], "chairman.directive")

    def test_timeout_and_524_backoff_requeue_event_without_loss(self) -> None:
        for failure in [subprocess.TimeoutExpired("hermes", 3), RuntimeError("HTTP 524 timeout")]:
            with self.subTest(failure=type(failure).__name__):
                with self.store.connect() as conn:
                    conn.execute("DELETE FROM ceo_runs")
                    conn.execute("DELETE FROM execution_events")
                reasoner = mock.Mock()
                reasoner.reason.side_effect = failure
                event_id = self._event("task.failed")

                result = EventEngine(
                    self.config,
                    ceo_runtime=CEORuntime(self.config, reasoner=reasoner, sender=FakeSender()),
                ).step()

                self.assertEqual(result["ceo"]["status"], "retry_scheduled")
                event = self.store.fetch_one("SELECT status, available_at, last_error FROM execution_events WHERE id=?", (event_id,))
                self.assertEqual(event["status"], "pending")
                self.assertGreater(event["available_at"], utcnow())
                self.assertIn("timeout", event["last_error"].lower())

    def test_invalid_model_protocol_requeues_event_without_crashing_worker(self) -> None:
        reasoner = FakeReasoner({
            "schema_version": ACTION_SCHEMA_VERSION,
            "judgment": "invalid action generated by the model",
            "state_patch": {},
            "actions": [{"type": "shell", "command": "must not run"}],
        })
        event_id = self._event("task.failed")

        result = EventEngine(
            self.config,
            ceo_runtime=CEORuntime(self.config, reasoner=reasoner, sender=FakeSender()),
        ).step()

        self.assertEqual(result["ceo"]["status"], "retry_scheduled")
        event = self.store.fetch_one(
            "SELECT status, available_at, last_error FROM execution_events WHERE id=?",
            (event_id,),
        )
        self.assertEqual(event["status"], "pending")
        self.assertGreater(event["available_at"], utcnow())
        self.assertIn("allowlisted", event["last_error"])

    def test_invalid_model_protocol_is_dead_lettered_after_three_attempts(self) -> None:
        reasoner = FakeReasoner({
            "schema_version": ACTION_SCHEMA_VERSION,
            "judgment": "invalid state update",
            "state_patch": {"unsupported": "field"},
            "actions": [{"type": "noop", "reason": "invalid"}],
        })
        event_id = self._event("task.failed")
        engine = EventEngine(
            self.config,
            ceo_runtime=CEORuntime(self.config, reasoner=reasoner, sender=FakeSender()),
        )

        for _ in range(3):
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE execution_events SET available_at=? WHERE id=?",
                    ("2000-01-01T00:00:00+00:00", event_id),
                )
            result = engine.step()

        self.assertEqual(result["ceo"]["status"], "protocol_rejected")
        event = self.store.fetch_one(
            "SELECT status, attempts, last_error FROM execution_events WHERE id=?",
            (event_id,),
        )
        self.assertEqual(event["status"], "failed")
        self.assertEqual(event["attempts"], 3)
        self.assertIn("unsupported fields", event["last_error"])
        self.assertEqual(len(reasoner.calls), 3)

    def test_ceo_task_action_respects_existing_wip_lane(self) -> None:
        now = utcnow()
        with self.store.connect() as conn:
            conn.execute(
                """INSERT INTO tasks(
                       created_at, updated_at, owner, title, domain, status,
                       priority, acceptance_criteria
                   ) VALUES (?, ?, 'Product Engineer', 'Existing product WIP',
                             'engineering', 'open', 90, 'Existing work finishes.')""",
                (now, now),
            )
            conn.execute("UPDATE execution_events SET status='processed', processed_at=?", (now,))
        reasoner = FakeReasoner({
            "schema_version": ACTION_SCHEMA_VERSION,
            "judgment": "Create another product task.",
            "state_patch": {},
            "actions": [{
                "type": "create_task",
                "owner": "Product Engineer",
                "title": "Duplicate product WIP",
                "domain": "engineering",
                "priority": 80,
                "acceptance_criteria": "Should not be created while lane is occupied.",
            }],
        })
        self._event("task.failed")

        result = EventEngine(
            self.config,
            ceo_runtime=CEORuntime(self.config, reasoner=reasoner, sender=FakeSender()),
        ).step()

        action = result["ceo"]["result"]["actions"][0]
        self.assertEqual(action["status"], "wip_lane_occupied")
        self.assertIsNone(
            self.store.fetch_one("SELECT id FROM tasks WHERE title='Duplicate product WIP'")
        )

    def test_pending_approval_delivery_is_idempotent_retryable_and_does_not_block_safe_event(self) -> None:
        sender = FakeSender(failures=1)
        runtime = CEORuntime(self.config, reasoner=FakeReasoner(), sender=sender)
        with self.store.connect() as conn:
            cur = conn.execute(
                "INSERT INTO approvals(created_at, requested_by, action_type, summary, status) VALUES (?, 'CEO', 'external_publish', 'Choose whether to publish', 'pending')",
                (utcnow(),),
            )
            approval_id = int(cur.lastrowid)

        first = EventEngine(self.config, ceo_runtime=runtime).step()
        self.assertEqual(first["ceo"]["status"], "delivery_retry_scheduled")
        delivery = self.store.fetch_one("SELECT * FROM approval_deliveries WHERE approval_id=?", (approval_id,))
        self.assertEqual(delivery["status"], "pending")
        self.assertEqual(delivery["attempts"], 1)

        safe_event = self._event("task.created")
        second = EventEngine(self.config, ceo_runtime=runtime).step()
        self.assertEqual(second["event_id"], safe_event)
        self.assertEqual(second["status"], "processed")

        with self.store.connect() as conn:
            conn.execute("UPDATE execution_events SET available_at=? WHERE event_type='approval.pending'", ("2000-01-01T00:00:00+00:00",))
        third = EventEngine(self.config, ceo_runtime=runtime).step()
        self.assertEqual(third["ceo"]["status"], "delivered")
        self.assertIn("Weixin home", sender.calls[-1][0])
        self.assertIn("approve", sender.calls[-1][0])
        self.assertIn("deny", sender.calls[-1][0])

        with self.store.connect() as conn:
            duplicate = self.store.enqueue_event(conn, "approval.pending", "approval", approval_id, {})
        duplicate_result = EventEngine(self.config, ceo_runtime=runtime).step()
        self.assertEqual(duplicate_result["event_id"], duplicate)
        self.assertEqual(duplicate_result["ceo"]["status"], "already_delivered")
        self.assertEqual(len(sender.calls), 2)

    def test_cli_exposes_status_directive_ingest_and_fixture_step(self) -> None:
        config_path = str(self.root / "config" / "sample.ini")
        fixture = self.root / "fixture.json"
        fixture.write_text(json.dumps({
            "schema_version": ACTION_SCHEMA_VERSION,
            "judgment": "deterministic fixture",
            "state_patch": {},
            "actions": [{"type": "noop", "reason": "smoke"}],
        }), encoding="utf-8")

        commands = [
            ["chairman-directive-ingest", "--source-platform", "weixin", "--source-session-id", "s1", "--source-message-id", "m1", "--message", "raw transient message", "--directive-type", "priority", "--objective", "Test fixture path"],
            ["ceo-status"],
            ["ceo-step", "--fixture", str(fixture), "--disable-external-delivery"],
        ]
        outputs = []
        for command in commands:
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                self.assertEqual(cli_main(["--config", config_path, *command]), 0)
            outputs.append(json.loads(stream.getvalue()))

        self.assertEqual(outputs[0]["event_type"], "chairman.directive")
        self.assertEqual(outputs[1]["identity"], "Hermes default profile CEO")
        self.assertEqual(outputs[2]["event_type"], "ceo.fixture")
        self.assertEqual(outputs[2]["ceo"]["status"], "applied")
        self.assertFalse(outputs[2]["ceo"]["external_delivery_enabled"])


if __name__ == "__main__":
    unittest.main()
