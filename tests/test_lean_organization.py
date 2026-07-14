from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_company.cli import main as cli_main
from agent_company.config import load_config
from agent_company.dashboard import DashboardApp, build_snapshot
from agent_company.db import Store
from agent_company.models import ON_DEMAND_CAPABILITIES, RACI, ROLES, SEED_TASKS
from agent_company.ops import CompanyOS


RESIDENT_ROLES = {"CEO", "Product Engineer", "Customer & Revenue"}
HISTORICAL_ROLES = {
    "CPO",
    "CTO",
    "CRO",
    "COO",
    "CFO",
    "Counsel",
    "AI Platform & Quality Engineer",
}


class LeanOrganizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            """
[company]
product_name = Test PixWeave
cycle_task_limit = 6

[paths]
database = data/company.sqlite3
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

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _create_legacy_database(self) -> None:
        self.config.db_path.parent.mkdir(parents=True)
        with sqlite3.connect(self.config.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    entity_id TEXT,
                    details TEXT NOT NULL
                );
                CREATE TABLE roles (
                    name TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    mandate TEXT NOT NULL
                );
                CREATE TABLE raci (
                    domain TEXT PRIMARY KEY,
                    responsible TEXT NOT NULL,
                    accountable TEXT NOT NULL,
                    consulted TEXT NOT NULL,
                    informed TEXT NOT NULL
                );
                CREATE TABLE tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    title TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    blocked_reason TEXT,
                    result TEXT,
                    acceptance_criteria TEXT
                );
                INSERT INTO roles(name, kind, mandate) VALUES
                    ('Chairman', 'human', 'Legacy Chairman mandate'),
                    ('CEO', 'agent', 'Legacy CEO mandate'),
                    ('CPO', 'agent', 'Legacy product mandate'),
                    ('CTO', 'agent', 'Legacy engineering mandate'),
                    ('CRO', 'agent', 'Legacy revenue mandate'),
                    ('COO', 'agent', 'Legacy operations mandate'),
                    ('CFO', 'agent', 'Legacy finance mandate'),
                    ('Counsel', 'agent', 'Legacy legal mandate'),
                    ('Product Engineer', 'agent', 'Legacy product engineer mandate'),
                    ('AI Platform & Quality Engineer', 'agent', 'Legacy quality mandate');
                INSERT INTO raci(domain, responsible, accountable, consulted, informed)
                VALUES ('roadmap', 'CPO', 'CEO', 'CTO,CRO', 'Chairman');
                INSERT INTO tasks(
                    id, created_at, updated_at, owner, title, domain, status, priority
                ) VALUES (
                    123, '2026-07-01T00:00:00+00:00', '2026-07-01T00:00:00+00:00',
                    'CTO', 'Historical product frontend task', 'engineering', 'done', 99
                );
                """
            )

    def test_models_define_three_resident_agents_and_on_demand_capabilities(self) -> None:
        self.assertEqual(set(ROLES), {"Chairman", *RESIDENT_ROLES})
        self.assertEqual(
            set(ON_DEMAND_CAPABILITIES),
            {
                "Finance & Risk Reviewer",
                "Legal/Compliance Specialist",
                "Independent Quality Reviewer",
                "Codex workers",
            },
        )
        self.assertNotIn("Codex workers", ROLES)
        self.assertEqual(len(SEED_TASKS), 2)
        self.assertEqual(
            {(owner, domain) for owner, _title, domain, _priority in SEED_TASKS},
            {("Product Engineer", "product"), ("Customer & Revenue", "gtm")},
        )
        self.assertEqual(RACI["strategy"][1], "Chairman")
        for domain in ("customer_outreach", "pricing", "spend", "legal", "public_release", "production_deploy"):
            self.assertEqual(RACI[domain][1], "Chairman")

    def test_existing_database_migration_is_audited_idempotent_and_preserves_task_owner(self) -> None:
        self._create_legacy_database()
        store = Store(self.config.db_path)

        store.init()
        store.init()

        task = store.fetch_one("SELECT owner, title, status FROM tasks WHERE id=123")
        self.assertEqual(dict(task), {
            "owner": "CTO",
            "title": "Historical product frontend task",
            "status": "done",
        })
        resident = store.fetch_all(
            "SELECT name FROM roles WHERE status='resident' AND kind='agent' ORDER BY name"
        )
        self.assertEqual({row["name"] for row in resident}, RESIDENT_ROLES)
        historical = store.fetch_all("SELECT name FROM roles WHERE status='historical'")
        self.assertTrue(HISTORICAL_ROLES.issubset({row["name"] for row in historical}))
        self.assertEqual(
            [row["domain"] for row in store.fetch_all("SELECT domain FROM raci ORDER BY domain")],
            sorted(RACI),
        )
        audits = store.fetch_all(
            "SELECT details FROM audit_log WHERE action='migrate_organization' AND entity_id='lean-org-v1'"
        )
        self.assertEqual(len(audits), 1)
        details = json.loads(audits[0]["details"])
        self.assertEqual(details["migration_version"], "lean-org-v1")
        self.assertIn("CTO", details["historical_roles"])
        self.assertEqual(details["task_owner_rows_changed"], 0)

    def test_migration_closes_active_work_owned_by_retired_roles_without_rewriting_owner(self) -> None:
        self._create_legacy_database()
        with sqlite3.connect(self.config.db_path) as conn:
            conn.execute(
                """INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority)
                   VALUES ('2026-07-02T00:00:00+00:00', '2026-07-02T00:00:00+00:00',
                           'CRO', 'Legacy commercial work', 'gtm', 'blocked', 95)"""
            )

        Store(self.config.db_path).init()

        task = Store(self.config.db_path).fetch_one(
            "SELECT owner, status, result FROM tasks WHERE title='Legacy commercial work'"
        )
        self.assertEqual(task["owner"], "CRO")
        self.assertEqual(task["status"], "cancelled")
        self.assertEqual(json.loads(task["result"])["migration_version"], "lean-org-v1")

    def test_new_database_seeds_only_one_product_and_one_commercial_task(self) -> None:
        store = Store(self.config.db_path)

        store.init()
        tasks = store.fetch_all("SELECT owner, domain, status FROM tasks ORDER BY id")

        self.assertEqual(len(tasks), 2)
        self.assertEqual(
            {(row["owner"], row["domain"], row["status"]) for row in tasks},
            {
                ("Product Engineer", "product", "open"),
                ("Customer & Revenue", "gtm", "open"),
            },
        )

    def test_cycle_enforces_one_product_and_one_commercial_wip_without_auto_backfill(self) -> None:
        osys = CompanyOS(self.config)
        osys.init()
        store = Store(self.config.db_path)
        with store.connect() as conn:
            now = "2026-07-14T00:00:00+00:00"
            conn.execute(
                """INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority)
                   VALUES (?, ?, 'Product Engineer', 'Second product item', 'engineering', 'open', 99)""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority)
                   VALUES (?, ?, 'Customer & Revenue', 'Second commercial item', 'revenue', 'open', 98)""",
                (now, now),
            )

        cycle = osys.run_cycle()

        in_progress = store.fetch_all("SELECT domain FROM tasks WHERE status='in_progress'")
        self.assertEqual(len(in_progress), 2)
        self.assertEqual(sum(row["domain"] in {"product", "engineering"} for row in in_progress), 1)
        self.assertEqual(sum(row["domain"] in {"gtm", "revenue", "customer"} for row in in_progress), 1)
        self.assertEqual(len(cycle["progressed"]), 2)
        self.assertEqual(store.fetch_all("SELECT id FROM strategic_phases"), [])
        self.assertEqual(len(store.fetch_all("SELECT id FROM tasks")), 4)

    def test_ceo_cannot_create_a_second_active_item_in_a_wip_lane(self) -> None:
        osys = CompanyOS(self.config)
        osys.init()

        with self.assertRaisesRegex(ValueError, "product WIP lane already has an active task"):
            osys.create_task(
                "CEO",
                "Product Engineer",
                "Another product priority",
                "engineering",
                85,
                "One reviewed product result.",
            )
        with self.assertRaisesRegex(ValueError, "commercial WIP lane already has an active task"):
            osys.create_task(
                "CEO",
                "Customer & Revenue",
                "Another commercial priority",
                "revenue",
                75,
                "One reviewed commercial result.",
            )
        with self.assertRaisesRegex(ValueError, "domain must belong"):
            osys.create_task(
                "CEO",
                "CEO",
                "Standalone operations activity",
                "operations",
                50,
                "Should not be manufactured as a third lane.",
            )

    def test_explicit_org_migration_command_reports_version_without_duplicate_audit(self) -> None:
        self._create_legacy_database()

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = cli_main(["org-migrate"])
        with contextlib.redirect_stdout(io.StringIO()):
            second_exit_code = cli_main(["org-migrate"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(second_exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["migration_version"], "lean-org-v1")
        audits = Store(self.config.db_path).fetch_all(
            "SELECT id FROM audit_log WHERE action='migrate_organization' AND entity_id='lean-org-v1'"
        )
        self.assertEqual(len(audits), 1)

    def test_dashboard_shows_lean_org_and_separates_historical_roles(self) -> None:
        self._create_legacy_database()
        Store(self.config.db_path).init()

        snapshot = build_snapshot(self.config)
        company = DashboardApp(self.config).render_path("/company").body

        self.assertEqual(
            {row["name"] for row in snapshot["company"]["roles"] if row["kind"] == "agent"},
            RESIDENT_ROLES,
        )
        self.assertIn("CTO", {row["name"] for row in snapshot["company"]["historical_roles"]})
        self.assertIn("客户与营收（Customer &amp; Revenue）", company)
        self.assertIn("按需能力", company)
        self.assertNotIn('<div class="org-node">CPO</div>', company)
        self.assertNotIn('<div class="org-node cto">CTO', company)


if __name__ == "__main__":
    unittest.main()
