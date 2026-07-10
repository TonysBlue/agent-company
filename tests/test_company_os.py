from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_company.backend import LocalBackend
from agent_company.brandkit import BrandKitError, build_campaign_manifest, validate_brand_kit, write_json
from agent_company.config import load_config
from agent_company.db import Store
from agent_company.governance import DISCLAIMER, classify_reserved_action
from agent_company.ops import CompanyOS
from agent_company.unit_economics import UnitEconomicsError, calculate_scenarios


class TempWorkspaceTest(unittest.TestCase):
    @staticmethod
    def provenance(source_id: str, decision: str = "approved_internal") -> dict[str, object]:
        return {
            "schema_version": "provenance/v1",
            "source_id": source_id,
            "parent_lineage": [],
            "source_category": "synthetic_test",
            "origin": "unit test fixture",
            "rights_basis": "company-created synthetic fixture",
            "rights_evidence_ref": "tests/test_company_os.py",
            "likeness_status": "no_real_person",
            "trademark_review_status": "no_third_party_mark",
            "data_classification": "synthetic",
            "retention_class": "test_lifetime",
            "policy_flags": ["synthetic_fixture"],
            "reviewer_ref": "test-reviewer",
            "review_decision": decision,
        }

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
""".strip()
            + "\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        self.osys = CompanyOS(self.config)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def test_init_creates_schema_and_roles(self) -> None:
        self.osys.init()
        store = Store(self.config.db_path)
        chairman = store.fetch_one("SELECT kind FROM roles WHERE name='Chairman'")
        self.assertEqual(chairman["kind"], "human")
        self.assertTrue(self.config.chairman_inbox.exists())
        self.assertIn("legal autonomy", DISCLAIMER)

    def test_repeated_init_does_not_pollute_audit_log(self) -> None:
        self.osys.init()
        self.osys.status()
        self.osys.chairman_inbox()

        rows = Store(self.config.db_path).fetch_all(
            "SELECT * FROM audit_log WHERE actor='system' AND action='init'"
        )
        self.assertEqual(len(rows), 1)

    def test_run_cycle_progresses_safe_work_and_records_metrics(self) -> None:
        self.osys.init()
        result = self.osys.run_cycle()
        self.assertGreater(result["processed"], 0)
        self.assertGreaterEqual(len(result["progressed"]), 1)
        status = self.osys.status()
        self.assertGreaterEqual(status["cycles"], 1)
        metrics = Store(self.config.db_path).fetch_all("SELECT * FROM metrics")
        self.assertGreaterEqual(len(metrics), 3)
        active = Store(self.config.db_path).fetch_all("SELECT * FROM tasks WHERE status='in_progress'")
        self.assertGreaterEqual(len(active), 1)

    def test_reserved_action_blocks_and_writes_chairman_inbox(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'CRO', 'Change price tier', 'gtm', 'open', 999)",
                (now, now),
            )
        result = self.osys.run_cycle()
        self.assertGreaterEqual(len(result["escalated"]), 1)
        inbox = self.osys.chairman_inbox()
        approval = next(item for item in inbox if item["summary"].endswith("Change price tier"))
        self.assertEqual(approval["action_type"], "pricing_change")
        inbox_file = Path(approval["inbox_file"])
        payload = json.loads(inbox_file.read_text(encoding="utf-8"))
        self.assertEqual(payload["requested_by"], "CRO")

    def test_chairman_decision_reopens_approved_task(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'CRO', 'Change price tier', 'gtm', 'open', 999)",
                (now, now),
            )
        self.osys.run_cycle()
        approval = self.osys.chairman_inbox()[0]
        decision = self.osys.decide(approval["id"], "approve", "Approved for controlled internal continuation.")
        self.assertEqual(decision["decided_by"], "Chairman")
        task = Store(self.config.db_path).fetch_one("SELECT status FROM tasks WHERE title='Change price tier'")
        self.assertEqual(task["status"], "open")
        self.assertTrue((self.config.chairman_outbox / f"decision-{approval['id']}.json").exists())

    def test_chairman_approval_is_consumed_without_duplicate_escalation(self) -> None:
        self.osys.init()
        with Store(self.config.db_path).connect() as conn:
            now = "2026-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority) VALUES (?, ?, 'CRO', 'Change price tier', 'gtm', 'open', 999)",
                (now, now),
            )
        self.osys.run_cycle()
        approval = self.osys.chairman_inbox()[0]
        self.osys.decide(approval["id"], "approve", "Approved for controlled internal continuation.")

        cycle = self.osys.run_cycle()

        self.assertNotIn(7, cycle["escalated"])
        self.assertIn(7, cycle["progressed"])
        duplicate = [
            item for item in self.osys.chairman_inbox()
            if item["summary"].endswith("Change price tier")
        ]
        self.assertEqual(duplicate, [])
        task = Store(self.config.db_path).fetch_one("SELECT status FROM tasks WHERE title='Change price tier'")
        self.assertEqual(task["status"], "in_progress")

    def test_task_completion_requires_existing_evidence(self) -> None:
        self.osys.init()
        task_id = self.osys.run_cycle()["progressed"][0]
        task = Store(self.config.db_path).fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))
        with self.assertRaisesRegex(ValueError, "evidence files do not exist"):
            self.osys.complete_task(task_id, task["owner"], "done", [self.root / "missing.md"])

        evidence = self.root / "evidence.md"
        evidence.write_text("reviewable result\n", encoding="utf-8")
        result = self.osys.complete_task(task_id, task["owner"], "Acceptance criteria verified.", [evidence])
        self.assertEqual(result["status"], "done")
        stored = Store(self.config.db_path).fetch_one("SELECT status,result FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(stored["status"], "done")
        self.assertEqual(json.loads(stored["result"])["evidence"], [str(evidence.resolve())])

    def test_validate_passes(self) -> None:
        self.assertEqual(self.osys.validate(), [])

    def test_reserved_classifier_uses_word_boundaries(self) -> None:
        self.assertIsNone(classify_reserved_action("Design first ICP and offer backlog", self.config))
        self.assertEqual(classify_reserved_action("sign vendor agreement", self.config), "contract_signature")

    def test_cycle_replenishes_distinct_backlog_with_acceptance_criteria(self) -> None:
        self.osys.init()
        cycle = self.osys.run_cycle()
        for task_id in cycle["progressed"]:
            task = Store(self.config.db_path).fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))
            evidence = self.root / f"replenish-{task_id}.md"
            evidence.write_text("verified\n", encoding="utf-8")
            self.osys.complete_task(task_id, task["owner"], "Verified bounded result.", [evidence])
        store = Store(self.config.db_path)
        active = store.fetch_all(
            "SELECT title, acceptance_criteria FROM tasks WHERE status IN ('open', 'in_progress', 'blocked')"
        )
        self.assertGreaterEqual(len(active), self.config.cycle_task_limit)
        self.assertTrue(any(row["acceptance_criteria"] for row in active))
        titles = store.fetch_all("SELECT title, COUNT(*) AS c FROM tasks GROUP BY title")
        self.assertTrue(all(row["c"] == 1 for row in titles))

    def test_backlog_stops_after_reviewed_roadmap_is_exhausted(self) -> None:
        self.osys.init()
        for _ in range(12):
            cycle = self.osys.run_cycle()
            for task_id in cycle["progressed"]:
                task = Store(self.config.db_path).fetch_one("SELECT * FROM tasks WHERE id=?", (task_id,))
                evidence = self.root / f"evidence-{task_id}.md"
                evidence.write_text("verified\n", encoding="utf-8")
                self.osys.complete_task(task_id, task["owner"], "Verified bounded result.", [evidence])

        store = Store(self.config.db_path)
        titles = store.fetch_all("SELECT title FROM tasks")
        self.assertFalse(any("iteration" in row["title"] for row in titles))
        task_count = len(titles)

        # Further dispatches may leave a legitimate reserved task blocked, but they
        # must not manufacture replacement work after the finite list is consumed.
        for _ in range(3):
            self.osys.run_cycle()
        self.assertEqual(len(store.fetch_all("SELECT id FROM tasks")), task_count)

    def test_cycle_seeds_internal_draft_experiment(self) -> None:
        self.osys.run_cycle()
        experiments = Store(self.config.db_path).fetch_all("SELECT * FROM experiments")
        self.assertEqual(len(experiments), 1)
        self.assertEqual(experiments[0]["status"], "draft")

    def test_brand_kit_validation_and_deterministic_campaign_manifest(self) -> None:
        brand_kit = {
            "schema_version": "brand-kit/v1",
            "brand_name": "Test Brand",
            "brand_version": "1.2.0",
            "colors": {"primary": "#123ABC", "secondary": ["#FFFFFF"], "neutrals": ["#111111"]},
            "typography": {"heading": "Inter", "body": "Noto Sans"},
            "logo": {"clearspace_px": 16, "allowed_placements": ["top-left"]},
            "forbidden_elements": ["competitor logos"],
        }
        campaign_input = {
            "brand_kit": brand_kit,
            "campaign": {"name": "Launch", "objective": "Internal review", "channels": ["web", "social"]},
            "assets": [{"id": "product-a", "provenance": self.provenance("product-a")}],
            "copy_variants": [{"id": "copy-a", "headline": "Controlled creative"}],
            "formats": [
                {"id": "square", "width": 1080, "height": 1080},
                {"id": "banner", "width": 1200, "height": 628},
            ],
        }

        self.assertEqual(validate_brand_kit(brand_kit), [])
        first = build_campaign_manifest(campaign_input)
        self.assertEqual(first, build_campaign_manifest(campaign_input))
        self.assertEqual(first["variant_count"], 4)
        self.assertEqual(len({item["id"] for item in first["variants"]}), 4)
        self.assertTrue(all(item["provenance"]["parent_lineage"] == ["product-a"] for item in first["variants"]))

        input_path = self.root / "campaign.json"
        input_path.write_text(json.dumps(campaign_input), encoding="utf-8")
        result = LocalBackend(self.config).generate_campaign_manifest_file(input_path)
        self.assertTrue(Path(result["path"]).exists())
        self.assertEqual(result["manifest_sha256"], first["manifest_sha256"])

    def test_brand_kit_rejects_invalid_palette_version_and_dimensions(self) -> None:
        invalid_brand = {
            "schema_version": "brand-kit/v1",
            "brand_name": "Test Brand",
            "brand_version": "v1",
            "colors": {"primary": "blue", "secondary": []},
            "typography": {"heading": "Inter", "body": "Inter"},
            "logo": {"clearspace_px": 0, "allowed_placements": ["top-left"]},
            "forbidden_elements": [],
        }
        errors = validate_brand_kit(invalid_brand)
        self.assertIn("brand_version must use MAJOR.MINOR.PATCH", errors)
        self.assertIn("colors.primary must be a #RRGGBB color", errors)

        valid_brand = {
            **invalid_brand,
            "brand_version": "1.0.0",
            "colors": {"primary": "#000000", "secondary": ["#FFFFFF"]},
        }
        invalid_campaign = {
            "brand_kit": valid_brand,
            "campaign": {"name": "Launch", "objective": "Review", "channels": ["web"]},
            "assets": [{"id": "asset", "provenance": self.provenance("asset")}],
            "copy_variants": [{"id": "copy", "headline": "Headline"}],
            "formats": [{"id": "bad", "width": 0, "height": 100}],
        }
        with self.assertRaisesRegex(BrandKitError, "width must be a positive integer"):
            build_campaign_manifest(invalid_campaign)

    def test_campaign_manifest_rejects_duplicate_identity_dimensions(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        duplicates = {
            r"campaign\.channels": lambda data: data["campaign"]["channels"].append(data["campaign"]["channels"][0]),
            r"assets\[\]\.id": lambda data: data["assets"].append(dict(data["assets"][0])),
            r"copy_variants\[\]\.id": lambda data: data["copy_variants"].append(dict(data["copy_variants"][0])),
            r"formats\[\]\.id": lambda data: data["formats"].append(dict(data["formats"][0])),
        }
        for expected, mutate in duplicates.items():
            with self.subTest(field=expected):
                invalid = json.loads(json.dumps(campaign))
                mutate(invalid)
                with self.assertRaisesRegex(BrandKitError, expected):
                    build_campaign_manifest(invalid)

        first = build_campaign_manifest(campaign)
        second = build_campaign_manifest(campaign)
        self.assertEqual(first["variant_count"], 16)
        self.assertEqual(len({item["id"] for item in first["variants"]}), 16)
        self.assertEqual(first["manifest_sha256"], second["manifest_sha256"])

    def test_campaign_manifest_fails_closed_on_provenance(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))

        missing = json.loads(json.dumps(campaign))
        del missing["assets"][0]["provenance"]
        with self.assertRaisesRegex(BrandKitError, r"assets\[0\]\.provenance must be an object"):
            build_campaign_manifest(missing)

        pending = json.loads(json.dumps(campaign))
        pending["assets"][0]["provenance"]["review_decision"] = "pending"
        with self.assertRaisesRegex(BrandKitError, "must be approved_internal"):
            build_campaign_manifest(pending)

        mismatched = json.loads(json.dumps(campaign))
        mismatched["assets"][0]["provenance"]["source_id"] = "different-source"
        with self.assertRaisesRegex(BrandKitError, "source_id must match"):
            build_campaign_manifest(mismatched)

    def test_json_artifact_write_preserves_existing_file_when_replace_fails(self) -> None:
        output = self.root / "artifacts" / "manifest.json"
        write_json(output, {"version": "original"})
        original = output.read_bytes()

        with patch("agent_company.brandkit.os.replace", side_effect=OSError("simulated interruption")):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                write_json(output, {"version": "replacement"})

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(output.parent.glob(f".{output.name}.*.tmp")), [])

    def test_unit_economics_calculates_cost_per_accepted_asset(self) -> None:
        result = calculate_scenarios(
            {
                "schema_version": "unit-economics/v1",
                "currency": "USD",
                "scenarios": [
                    {
                        "name": "base",
                        "inference_cost_per_attempt": 0.12,
                        "storage_cost_per_attempt": 0.005,
                        "qa_minutes_per_attempt": 1.5,
                        "qa_hourly_cost": 24,
                        "acceptance_rate": 0.70,
                    }
                ],
            }
        )

        self.assertFalse(result["pricing_authorized"])
        self.assertEqual(result["scenarios"][0]["cost_per_attempt"], 0.725)
        self.assertEqual(result["scenarios"][0]["cost_per_accepted_asset"], 1.035714)

    def test_unit_economics_rejects_invalid_acceptance_rate(self) -> None:
        invalid = {
            "schema_version": "unit-economics/v1",
            "currency": "USD",
            "scenarios": [
                {
                    "name": "invalid",
                    "inference_cost_per_attempt": 0.1,
                    "storage_cost_per_attempt": 0,
                    "qa_minutes_per_attempt": 0,
                    "qa_hourly_cost": 0,
                    "acceptance_rate": 0,
                }
            ],
        }
        with self.assertRaisesRegex(UnitEconomicsError, "acceptance_rate"):
            calculate_scenarios(invalid)


if __name__ == "__main__":
    unittest.main()
