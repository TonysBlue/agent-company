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
from agent_company.db import Store
from agent_company.ops import CompanyOS


class TokenUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            """
[company]
product_name = Test PixWeave

[paths]
database = data/test.sqlite3
chairman_inbox = data/chairman/inbox
chairman_outbox = data/chairman/outbox
artifacts = data/artifacts
logs = logs
""".strip()
            + "\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        self.osys = CompanyOS(self.config)
        self.osys.init()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_schema_records_audited_token_usage_without_losing_existing_data(self) -> None:
        task_id = self.osys.create_task(
            "CEO", "CTO", "Measure model call", "engineering", 90, "Token usage is recorded from observed values."
        )["task_id"]

        record = self.osys.record_token_usage(
            agent="CTO",
            task_id=task_id,
            execution_id=None,
            session="session-1",
            model="gpt-test",
            provider="openai",
            input_tokens=100,
            output_tokens=25,
            cache_tokens=10,
            reasoning_tokens=5,
            total_tokens=140,
            cost=0.0123,
            currency="USD",
            source="unit-test-observed",
            timestamp="2026-07-11T00:00:00+00:00",
        )

        self.assertEqual(record["agent"], "CTO")
        self.assertEqual(record["total_tokens"], 140)
        rows = Store(self.config.db_path).fetch_all("SELECT * FROM token_usage")
        self.assertEqual(len(rows), 1)
        audit = Store(self.config.db_path).fetch_one(
            "SELECT * FROM audit_log WHERE action='record_token_usage' AND entity='token_usage'"
        )
        self.assertIsNotNone(audit)
        self.assertEqual(json.loads(audit["details"])["record_id"], record["id"])

    def test_token_usage_validation_rejects_fabricated_or_inconsistent_values(self) -> None:
        valid = {
            "agent": "CTO",
            "input_tokens": 1,
            "output_tokens": 2,
            "cache_tokens": 3,
            "reasoning_tokens": 4,
            "total_tokens": 10,
            "source": "observed-log",
        }

        with self.assertRaisesRegex(ValueError, "agent must be a registered agent role"):
            self.osys.record_token_usage(**{**valid, "agent": "Chairman"})
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            self.osys.record_token_usage(**{**valid, "input_tokens": -1})
        with self.assertRaisesRegex(ValueError, "total_tokens must equal"):
            self.osys.record_token_usage(**{**valid, "total_tokens": 99})
        with self.assertRaisesRegex(ValueError, "source is required"):
            self.osys.record_token_usage(**{**valid, "source": ""})
        with self.assertRaisesRegex(ValueError, "currency is required"):
            self.osys.record_token_usage(**{**valid, "cost": 1.2, "currency": None})
        with self.assertRaisesRegex(ValueError, "cost must be nonnegative"):
            self.osys.record_token_usage(**{**valid, "cost": -0.1, "currency": "USD"})
        with self.assertRaisesRegex(ValueError, "task not found"):
            self.osys.record_token_usage(**{**valid, "task_id": 999})

    def test_cli_record_list_and_summary_commands(self) -> None:
        config_path = str(self.root / "config" / "sample.ini")
        record_out = io.StringIO()
        with contextlib.redirect_stdout(record_out):
            code = cli_main([
                "--config", config_path,
                "token-record",
                "--agent", "CTO",
                "--input-tokens", "7",
                "--output-tokens", "8",
                "--cache-tokens", "0",
                "--reasoning-tokens", "5",
                "--total-tokens", "20",
                "--model", "gpt-test",
                "--provider", "openai",
                "--source", "observed-cli",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(record_out.getvalue())["total_tokens"], 20)

        list_out = io.StringIO()
        with contextlib.redirect_stdout(list_out):
            self.assertEqual(cli_main(["--config", config_path, "token-list"]), 0)
        listed = json.loads(list_out.getvalue())
        self.assertEqual(listed[0]["agent"], "CTO")

        summary_out = io.StringIO()
        with contextlib.redirect_stdout(summary_out):
            self.assertEqual(cli_main(["--config", config_path, "token-summary"]), 0)
        summary = json.loads(summary_out.getvalue())
        self.assertEqual(summary["agents"]["CTO"]["total_tokens"], 20)
        self.assertEqual(summary["agents"]["CEO"]["status_label"], "未采集")


if __name__ == "__main__":
    unittest.main()
