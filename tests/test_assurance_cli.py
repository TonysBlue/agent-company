from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.assurance import AssuranceKernel
from agent_company.cli import main as cli_main
from agent_company.config import load_config
from agent_company.db import Store


class AssuranceCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[paths]\ndatabase=data/company.sqlite3\nartifacts=data/artifacts\nlogs=logs\n",
            encoding="utf-8",
        )
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main([*args])
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_shadow_commands_init_classify_list_and_integrity(self) -> None:
        Store(load_config().db_path).init()
        initialized = self.run_cli("assurance-init")
        self.assertEqual(initialized["mode"], "shadow")
        result = self.run_cli(
            "assurance-classify", "--actor", "CEO", "--principal-id", "principal-ceo",
            "--title", "Assurance bootstrap", "--persistent-schema",
        )
        self.assertEqual(result["risk_class"], "C2")
        self.assertEqual(result["mode"], "shadow")
        listing = self.run_cli("assurance-list")
        self.assertEqual(listing, [])
        integrity = self.run_cli("assurance-integrity")
        self.assertEqual(integrity["status"], "ok")
        self.assertEqual(Store(load_config().db_path).fetch_one("SELECT COUNT(*) AS c FROM tasks")["c"], 2)


if __name__ == "__main__":
    unittest.main()
