from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_company.backend import LocalBackend
from agent_company.beta_launch import (
    BetaLaunchReadinessError,
    evaluate_beta_launch_package,
    evaluate_beta_launch_package_file,
)
from agent_company.brandkit import BrandKitError, build_campaign_manifest, validate_brand_kit, write_json
from agent_company.campaign_render import (
    CampaignRenderError,
    CampaignRenderVerificationError,
    render_campaign_bundle,
    verify_campaign_render_bundle,
)
from agent_company.campaign_review import CampaignReviewError, record_campaign_review
from agent_company.cli import main as cli_main
from agent_company.config import load_config
from agent_company.db import Store
from agent_company.governance import DISCLAIMER, classify_reserved_action
from agent_company.feedback import FeedbackError, capture_feedback, triage_feedback
from agent_company.ops import CompanyOS
from agent_company.product_shot import ProductShotWorkflowError, build_product_shot_manifest
from agent_company.prompt_pack import PromptPackError, build_prompt_manifest
from agent_company.unit_economics import UnitEconomicsError, calculate_scenarios
from agent_company.visual_qa import VisualQAScorecardError, build_scorecard


class TempWorkspaceTest(unittest.TestCase):
    def test_feedback_capture_and_triage_are_bounded_and_auditable(self) -> None:
        submission = {
            "schema_version": "feedback-submission/v1", "submission_id": "f-1",
            "product_version": "0.12.0", "entry_point": "result", "category": "bug",
            "severity": "high", "message": "Synthetic failure report", "context": {},
            "contact_consent": False, "contains_sensitive_data": False, "honeypot": ""
        }
        captured_path = self.root / "captured.json"
        captured = capture_feedback(submission, captured_path)
        self.assertEqual(captured["state"], "received")
        decision = {"schema_version": "feedback-triage/v1", "reviewer_ref": "CPO",
                    "decision_at": "2026-07-11T00:00:00Z", "state": "planned",
                    "rationale": "Accepted", "backlog_task_id": 7}
        triaged = triage_feedback(captured_path, decision, self.root / "triage.json")
        self.assertEqual(triaged["backlog_task_id"], 7)
        with self.assertRaises(FeedbackError):
            capture_feedback({**submission, "contact": "person@example.test"}, self.root / "bad.json")
        with self.assertRaises(FeedbackError):
            capture_feedback({**submission, "contains_sensitive_data": True}, self.root / "sensitive.json")

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

    def test_ceo_creates_audited_reviewed_task(self) -> None:
        created = self.osys.create_task(
            "CEO", "CTO", "Implement bounded capability", "engineering", 80,
            "A runnable command and regression test pass.",
        )
        self.assertEqual(created["status"], "open")
        task = Store(self.config.db_path).fetch_one("SELECT * FROM tasks WHERE id=?", (created["task_id"],))
        self.assertEqual(task["acceptance_criteria"], "A runnable command and regression test pass.")
        audit = Store(self.config.db_path).fetch_one(
            "SELECT * FROM audit_log WHERE action='create_task' AND entity_id=?", (str(created["task_id"]),)
        )
        self.assertIsNotNone(audit)
        with self.assertRaisesRegex(ValueError, "only CEO"):
            self.osys.create_task("CTO", "CTO", "Unauthorized", "engineering", 50, "Must fail.")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.osys.create_task(
                "CEO", "CTO", "Implement bounded capability", "engineering", 80, "Duplicate must fail."
            )

    def test_task_cancel_closes_work_without_claiming_completion(self) -> None:
        self.osys.init()
        task_id = self.osys.run_cycle()["progressed"][0]
        result = self.osys.cancel_task(task_id, "CEO", "Superseded by reviewed task 42.")
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(result["completed"])
        stored = Store(self.config.db_path).fetch_one("SELECT status,result FROM tasks WHERE id=?", (task_id,))
        self.assertEqual(stored["status"], "cancelled")
        self.assertFalse(json.loads(stored["result"])["completed"])

    def test_task_cancel_rejects_unrelated_actor(self) -> None:
        self.osys.init()
        task_id = self.osys.run_cycle()["progressed"][0]
        with self.assertRaisesRegex(ValueError, "may only be cancelled"):
            self.osys.cancel_task(task_id, "CFO", "Not my task.")

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

    def test_campaign_render_writes_deterministic_escaped_svg_bundle(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        campaign["campaign"]["name"] = 'Launch <iframe src="x"></iframe>'
        campaign["brand_kit"]["brand_name"] = "Pix <b>Weave</b>"
        campaign["copy_variants"][0]["headline"] = '<script>alert("x")</script> & controlled'
        first_dir = self.root / "first-render"
        second_dir = self.root / "second-render"

        first = render_campaign_bundle(campaign, first_dir)
        second = render_campaign_bundle(campaign, second_dir)

        self.assertEqual(first, second)
        self.assertEqual(first["asset_count"], 16)
        self.assertFalse(first["external_publish_authorized"])
        self.assertEqual(first["review_gallery"]["file"], "review-gallery.html")
        self.assertEqual(first["schema_version"], "campaign-render/v2")
        self.assertTrue((first_dir / "review-gallery.html").is_file())
        self.assertEqual(
            first["review_gallery"]["sha256"],
            __import__("hashlib").sha256((first_dir / "review-gallery.html").read_bytes()).hexdigest(),
        )
        self.assertEqual((first_dir / "review-gallery.html").read_bytes(), (second_dir / "review-gallery.html").read_bytes())
        for asset in first["assets"]:
            first_bytes = (first_dir / asset["file"]).read_bytes()
            self.assertEqual(first_bytes, (second_dir / asset["file"]).read_bytes())
            self.assertEqual(asset["sha256"], __import__("hashlib").sha256(first_bytes).hexdigest())
        gallery = (first_dir / "review-gallery.html").read_text(encoding="utf-8")
        self.assertEqual(gallery.count('<article class="variant">'), first["asset_count"])
        self.assertEqual(gallery.count("data:image/svg+xml;base64,"), first["asset_count"])
        self.assertIn("review_state: draft", gallery)
        self.assertIn("external_publish_authorized: false", gallery)
        self.assertNotIn("<script>", gallery)
        self.assertNotIn("<iframe", gallery)
        self.assertNotIn("<b>Weave</b>", gallery)
        self.assertIn("&lt;script&gt;", gallery)
        self.assertIn("&lt;iframe", gallery)
        self.assertIn("Pix &lt;b&gt;Weave&lt;/b&gt;", gallery)
        for asset in first["assets"]:
            self.assertIn(asset["variant_id"], gallery)
            self.assertIn(asset["sha256"], gallery)
        injected = [asset for asset in first["assets"] if asset["variant_id"] in {
            item["id"] for item in build_campaign_manifest(campaign)["variants"] if item["copy_id"] == "control"
        }]
        self.assertTrue(injected)
        for asset in injected:
            first_bytes = (first_dir / asset["file"]).read_bytes()
            self.assertNotIn(b"<script>", first_bytes)
            self.assertIn(b"&lt;script&gt;", first_bytes)

    def test_campaign_render_rejects_malformed_input_and_preserves_existing_output(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        del campaign["assets"][0]["provenance"]
        output = self.root / "render"
        output.mkdir()
        marker = output / "keep.txt"
        marker.write_text("unchanged", encoding="utf-8")

        with self.assertRaisesRegex(BrandKitError, "provenance"):
            render_campaign_bundle(campaign, output)
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

        valid = json.loads(campaign_path.read_text(encoding="utf-8"))
        with self.assertRaisesRegex(CampaignRenderError, "already exists"):
            render_campaign_bundle(valid, output)
        self.assertEqual(marker.read_text(encoding="utf-8"), "unchanged")

        partial = self.root / "partial-render"
        manifest = render_campaign_bundle(valid, partial)
        (partial / manifest["review_gallery"]["file"]).unlink()
        with self.assertRaisesRegex(CampaignRenderError, "already exists"):
            render_campaign_bundle(valid, partial)

        traversal = self.root / "traversal-render"
        traversal_manifest = render_campaign_bundle(valid, traversal)
        manifest_path = traversal / "render-manifest.json"
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
        stored["review_gallery"]["file"] = "../review-gallery.html"
        manifest_path.write_text(json.dumps(stored), encoding="utf-8")
        with self.assertRaisesRegex(CampaignRenderError, "already exists"):
            render_campaign_bundle(valid, traversal)

    def test_campaign_render_publication_failure_leaves_no_partial_output(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        output = self.root / "render"

        with patch("agent_company.campaign_render.os.replace", side_effect=OSError("simulated publish failure")):
            with self.assertRaisesRegex(OSError, "simulated publish failure"):
                render_campaign_bundle(campaign, output)

        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.glob(".render.*")), [])

    def test_campaign_render_verify_accepts_complete_bundle_through_cli(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        output = self.root / "render"
        manifest = render_campaign_bundle(campaign, output)

        result = verify_campaign_render_bundle(output)

        self.assertTrue(result["ok"])
        self.assertEqual(result["schema_version"], "campaign-render/v2")
        self.assertEqual(result["bundle_sha256"], manifest["bundle_sha256"])
        self.assertEqual(result["asset_count"], 16)
        self.assertFalse(result["external_publish_authorized"])
        self.assertEqual(self._quiet_cli(["campaign-render-verify", str(output)]), 0)

    def test_campaign_render_verify_fails_closed_on_tampering(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        source = self.root / "render-source"
        manifest = render_campaign_bundle(campaign, source)
        first_asset = manifest["assets"][0]["file"]

        def copied_bundle(name: str) -> Path:
            target = self.root / name
            shutil.copytree(source, target)
            return target

        cases = [
            ("tampered-svg", lambda path: (path / first_asset).write_text("changed", encoding="utf-8"), "checksum mismatch"),
            ("missing-svg", lambda path: (path / first_asset).unlink(), "missing expected files"),
            ("extra-svg", lambda path: (path / "extra.svg").write_text("<svg />", encoding="utf-8"), "unexpected files"),
            (
                "publish-control",
                lambda path: self._mutate_render_manifest(path, lambda data: data.update({"external_publish_authorized": True})),
                "external_publish_authorized must be false",
            ),
            (
                "traversal",
                lambda path: self._mutate_render_manifest(path, lambda data: data["assets"][0].update({"file": "../escape.svg"})),
                "path traversal",
            ),
            (
                "unstable-filename",
                lambda path: self._mutate_render_manifest(path, lambda data: data["assets"][0].update({"file": "creative.svg"})),
                "must be",
            ),
            (
                "duplicate-asset",
                lambda path: self._mutate_render_manifest(path, lambda data: data["assets"].append(dict(data["assets"][0]))),
                "asset_count must match",
            ),
            (
                "gallery-control",
                lambda path: (path / "review-gallery.html").write_text("external_publish_authorized: true", encoding="utf-8"),
                "checksum mismatch",
            ),
        ]
        for name, mutate, expected in cases:
            with self.subTest(name=name):
                bundle = copied_bundle(name)
                mutate(bundle)
                with self.assertRaisesRegex(CampaignRenderVerificationError, expected):
                    verify_campaign_render_bundle(bundle)
                self.assertEqual(self._quiet_cli(["campaign-render-verify", str(bundle)]), 2)

        malformed = copied_bundle("malformed")
        (malformed / "render-manifest.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(CampaignRenderVerificationError, "invalid JSON"):
            verify_campaign_render_bundle(malformed)

    def test_campaign_review_records_complete_internal_decisions_through_cli(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        bundle = self.root / "render"
        render_manifest = render_campaign_bundle(campaign, bundle)
        decisions_path = Path(__file__).parents[1] / "examples" / "campaign-review-decisions.json"
        output = self.root / "review.json"

        result = record_campaign_review(bundle, decisions_path, output)

        self.assertEqual(result["asset_count"], 16)
        self.assertEqual(result["approved_count"], 15)
        self.assertEqual(result["rejected_count"], 1)
        self.assertFalse(result["external_publish_authorized"])
        record = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(record["schema_version"], "campaign-review/v1")
        self.assertEqual(record["bundle_sha256"], render_manifest["bundle_sha256"])
        self.assertEqual(record["campaign_manifest_sha256"], render_manifest["campaign_manifest_sha256"])
        self.assertEqual(record["publication_authorization"], "none")
        self.assertFalse(record["external_publish_authorized"])
        self.assertEqual(len(record["decisions"]), 16)
        self.assertEqual(
            {item["variant_id"]: item["svg_sha256"] for item in record["decisions"]},
            {item["variant_id"]: item["sha256"] for item in render_manifest["assets"]},
        )
        self.assertEqual(self._quiet_cli(["campaign-review", str(bundle), str(decisions_path), "--output", str(self.root / "cli-review.json")]), 0)

    def test_campaign_review_fails_on_tampered_bundle_and_incomplete_or_malformed_decisions(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        bundle = self.root / "render"
        render_manifest = render_campaign_bundle(campaign, bundle)
        decisions_path = self.root / "decisions.json"
        write_json(decisions_path, self._review_decisions(render_manifest))
        tampered = self.root / "tampered"
        shutil.copytree(bundle, tampered)
        first_asset = render_manifest["assets"][0]["file"]
        (tampered / first_asset).write_text("changed", encoding="utf-8")

        with self.assertRaisesRegex(CampaignRenderVerificationError, "checksum mismatch"):
            record_campaign_review(tampered, decisions_path, self.root / "tampered-review.json")
        self.assertFalse((self.root / "tampered-review.json").exists())

        incomplete = self._review_decisions(render_manifest)
        incomplete["decisions"].pop()
        incomplete_path = self.root / "incomplete.json"
        write_json(incomplete_path, incomplete)
        with self.assertRaisesRegex(CampaignReviewError, "decisions missing variants"):
            record_campaign_review(bundle, incomplete_path, self.root / "incomplete-review.json")
        self.assertFalse((self.root / "incomplete-review.json").exists())

        malformed = self._review_decisions(render_manifest, reject_first=True)
        del malformed["decisions"][0]["rejection_reason"]
        malformed["reviewer"]["reviewer_ref"] = "?"
        malformed_path = self.root / "malformed.json"
        write_json(malformed_path, malformed)
        with self.assertRaisesRegex(CampaignReviewError, "reviewer_ref|rejection_reason"):
            record_campaign_review(bundle, malformed_path, self.root / "malformed-review.json")
        self.assertEqual(self._quiet_cli(["campaign-review", str(bundle), str(malformed_path), "--output", str(self.root / "cli-bad.json")]), 2)

        invalid_timestamp = self._review_decisions(render_manifest)
        invalid_timestamp["reviewer"]["reviewed_at"] = "2026-02-30T25:61:61Z"
        invalid_timestamp_path = self.root / "invalid-timestamp.json"
        write_json(invalid_timestamp_path, invalid_timestamp)
        with self.assertRaisesRegex(CampaignReviewError, "ISO-8601 UTC timestamp"):
            record_campaign_review(bundle, invalid_timestamp_path, self.root / "invalid-timestamp-review.json")

    def test_campaign_review_is_deterministic_and_leaves_no_partial_output(self) -> None:
        campaign_path = Path(__file__).parents[1] / "examples" / "campaign.json"
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        bundle = self.root / "render"
        render_manifest = render_campaign_bundle(campaign, bundle)
        decisions_path = self.root / "decisions.json"
        write_json(decisions_path, self._review_decisions(render_manifest, reject_first=True))
        first = self.root / "review-first.json"
        second = self.root / "review-second.json"

        first_result = record_campaign_review(bundle, decisions_path, first)
        second_result = record_campaign_review(bundle, decisions_path, second)

        self.assertEqual(first_result["review_sha256"], second_result["review_sha256"])
        self.assertEqual(first.read_bytes(), second.read_bytes())
        existing = self.root / "existing-review.json"
        write_json(existing, {"version": "original"})
        original = existing.read_bytes()
        with patch("agent_company.brandkit.os.replace", side_effect=OSError("simulated review write failure")):
            with self.assertRaisesRegex(OSError, "simulated review write failure"):
                record_campaign_review(bundle, decisions_path, existing)
        self.assertEqual(existing.read_bytes(), original)
        self.assertEqual(list(self.root.glob(".existing-review.json.*.tmp")), [])

    def _mutate_render_manifest(self, bundle: Path, mutate: object) -> None:
        manifest_path = bundle / "render-manifest.json"
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        mutate(data)  # type: ignore[operator]
        manifest_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _review_decisions(self, render_manifest: dict[str, object], reject_first: bool = False) -> dict[str, object]:
        decisions = []
        assets = sorted(render_manifest["assets"], key=lambda item: item["variant_id"])  # type: ignore[index]
        for index, asset in enumerate(assets):
            item = {
                "variant_id": asset["variant_id"],
                "decision": "reject" if reject_first and index == 0 else "approve",
            }
            if item["decision"] == "reject":
                item["rejection_reason"] = "Needs stronger hierarchy before internal approval."
            decisions.append(item)
        return {
            "schema_version": "campaign-review-decisions/v1",
            "reviewer": {
                "reviewer_ref": "cpo.internal",
                "role": "CPO internal creative reviewer",
                "reviewed_at": "2026-07-11T00:00:00Z",
            },
            "decisions": decisions,
        }

    def _quiet_cli(self, argv: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return cli_main(argv)

    def test_beta_launch_readiness_example_is_deterministic_and_never_authorizes_launch(self) -> None:
        package_path = Path(__file__).parents[1] / "examples" / "beta-launch-package.json"

        first = evaluate_beta_launch_package_file(package_path)
        second = evaluate_beta_launch_package_file(package_path)

        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], "beta-launch-readiness-evidence/v1")
        self.assertEqual(first["status"], "blocked_pending_chairman_approvals")
        self.assertFalse(first["launch_authorized"])
        self.assertFalse(first["external_action_authorized"])
        self.assertIn("never authorizes launch", first["authorization_statement"])
        self.assertEqual(len(first["gates"]), 9)
        self.assertEqual(
            {gate["gate"] for gate in first["gates"]},
            {
                "product_capability_evidence",
                "feedback_controls",
                "risk_review",
                "onboarding",
                "support_ownership",
                "observability",
                "rollback",
                "security_privacy_readiness",
                "unit_economics_evidence",
            },
        )
        self.assertEqual(
            [item["action_type"] for item in first["reserved_action_approvals"]],
            ["external_publish", "pricing_change", "production_deploy"],
        )
        self.assertTrue(all(not item["launch_authorized"] for item in first["reserved_action_approvals"]))

        output = self.root / "readiness.json"
        result = evaluate_beta_launch_package_file(package_path, output)
        self.assertEqual(result, json.loads(output.read_text(encoding="utf-8")))
        self.assertEqual(self._quiet_cli(["beta-launch-readiness", str(package_path), "--output", str(self.root / "cli-readiness.json")]), 0)

    def test_beta_launch_readiness_fails_closed_on_missing_and_tampered_evidence(self) -> None:
        package_path = Path(__file__).parents[1] / "examples" / "beta-launch-package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        evidence_source = package_path.parent / "beta-launch-evidence"
        evidence_target = self.root / "beta-launch-evidence"
        shutil.copytree(evidence_source, evidence_target)

        missing = json.loads(json.dumps(package))
        missing["gates"]["rollback"]["artifacts"][0]["path"] = "beta-launch-evidence/missing.json"
        with self.assertRaisesRegex(BetaLaunchReadinessError, "missing evidence file"):
            evaluate_beta_launch_package(missing, self.root)

        tampered = json.loads(json.dumps(package))
        (evidence_target / "unit-economics.json").write_text('{"changed": true}\n', encoding="utf-8")
        with self.assertRaisesRegex(BetaLaunchReadinessError, "checksum mismatch"):
            evaluate_beta_launch_package(tampered, self.root)

        traversal = json.loads(json.dumps(package))
        traversal["gates"]["onboarding"]["artifacts"][0]["path"] = "../README.md"
        with self.assertRaisesRegex(BetaLaunchReadinessError, "must not escape"):
            evaluate_beta_launch_package(traversal, self.root)

    def test_beta_launch_readiness_rejects_malformed_input_and_no_partial_cli_output(self) -> None:
        package_path = Path(__file__).parents[1] / "examples" / "beta-launch-package.json"
        package = json.loads(package_path.read_text(encoding="utf-8"))
        evidence_source = package_path.parent / "beta-launch-evidence"
        evidence_target = self.root / "beta-launch-evidence"
        shutil.copytree(evidence_source, evidence_target)

        malformed = json.loads(json.dumps(package))
        del malformed["gates"]["security_privacy_readiness"]
        with self.assertRaisesRegex(BetaLaunchReadinessError, "security_privacy_readiness"):
            evaluate_beta_launch_package(malformed, self.root)

        bad_approval = json.loads(json.dumps(package))
        bad_approval["reserved_action_approvals"][0]["decided_by"] = "CEO"
        with self.assertRaisesRegex(BetaLaunchReadinessError, "decided_by must be null while pending"):
            evaluate_beta_launch_package(bad_approval, self.root)

        invalid_json = self.root / "invalid-beta.json"
        invalid_json.write_text("{", encoding="utf-8")
        output = self.root / "should-not-exist.json"
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli_main(["beta-launch-readiness", str(invalid_json), "--output", str(output)])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("invalid JSON", stderr.getvalue())
        self.assertFalse(output.exists())

    def test_json_artifact_write_preserves_existing_file_when_replace_fails(self) -> None:
        output = self.root / "artifacts" / "manifest.json"
        write_json(output, {"version": "original"})
        original = output.read_bytes()

        with patch("agent_company.brandkit.os.replace", side_effect=OSError("simulated interruption")):
            with self.assertRaisesRegex(OSError, "simulated interruption"):
                write_json(output, {"version": "replacement"})

        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(list(output.parent.glob(f".{output.name}.*.tmp")), [])

    def test_prompt_pack_expands_deterministically_and_writes_manifest(self) -> None:
        prompt_pack = {
            "schema_version": "prompt-pack/v1",
            "name": "product-shot",
            "version": "1.0.0",
            "template": "A {view} product shot on a {background} background",
            "variables": {"view": ["front", "detail"], "background": ["white", "gray"]},
        }
        first = build_prompt_manifest(prompt_pack)
        self.assertEqual(first, build_prompt_manifest(prompt_pack))
        self.assertEqual(first["prompt_count"], 4)
        self.assertEqual(len({item["id"] for item in first["prompts"]}), 4)

        input_path = self.root / "prompt-pack.json"
        input_path.write_text(json.dumps(prompt_pack), encoding="utf-8")
        result = LocalBackend(self.config).generate_prompt_manifest_file(input_path)
        self.assertTrue(Path(result["path"]).exists())
        self.assertEqual(result["manifest_sha256"], first["manifest_sha256"])

    def test_prompt_pack_fails_closed_on_invalid_variables(self) -> None:
        invalid = {
            "schema_version": "prompt-pack/v1",
            "name": "product-shot",
            "version": "1.0.0",
            "template": "A {view} product shot with {missing}",
            "variables": {"view": ["front", "front"], "unused": ["value"]},
        }
        with self.assertRaisesRegex(PromptPackError, "duplicate values"):
            build_prompt_manifest(invalid)

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

    def test_product_shot_workflow_manifest_is_deterministic_and_atomic(self) -> None:
        workflow_path = Path(__file__).parents[1] / "examples" / "product-shot-workflow.json"
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

        first = build_product_shot_manifest(workflow)
        self.assertEqual(first, build_product_shot_manifest(workflow))
        self.assertEqual(first["scenario_count"], 3)
        self.assertEqual([stage["id"] for stage in first["scenarios"][0]["ordered_stages"]], [
            "source-review",
            "shot-plan",
            "internal-qa",
        ])
        self.assertIn("does not measure", first["capability_disclaimer"])

        result = LocalBackend(self.config).generate_product_shot_workflow_file(workflow_path)
        self.assertTrue(Path(result["path"]).exists())
        self.assertEqual(result["manifest_sha256"], first["manifest_sha256"])

    def test_product_shot_workflow_fails_closed_on_provenance_and_scenario_count(self) -> None:
        workflow_path = Path(__file__).parents[1] / "examples" / "product-shot-workflow.json"
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

        too_few = json.loads(json.dumps(workflow))
        too_few["scenarios"] = too_few["scenarios"][:2]
        with self.assertRaisesRegex(ProductShotWorkflowError, "at least three"):
            build_product_shot_manifest(too_few)

        missing = json.loads(json.dumps(workflow))
        del missing["scenarios"][0]["source"]["provenance"]
        with self.assertRaisesRegex(ProductShotWorkflowError, r"scenarios\[0\]\.source\.provenance must be an object"):
            build_product_shot_manifest(missing)

        pending = json.loads(json.dumps(workflow))
        pending["scenarios"][0]["source"]["provenance"]["review_decision"] = "pending"
        with self.assertRaisesRegex(ProductShotWorkflowError, "must be approved_internal"):
            build_product_shot_manifest(pending)

    def test_visual_qa_scorecard_repeatability_and_thresholds(self) -> None:
        scorecard_input = {
            "schema_version": "visual-qa-observations/v1",
            "subject": {"id": "internal-test-shot", "version": "1.0.0"},
            "observations": [
                {
                    "type": "edit_fidelity",
                    "value": 90,
                    "method": "explicit measurement fixture",
                    "observer_ref": "qa-a",
                    "severity": "normal",
                },
                {
                    "type": "brand_consistency",
                    "value": 90,
                    "method": "explicit measurement fixture",
                    "observer_ref": "brand-a",
                    "severity": "normal",
                },
            ],
        }
        first = build_scorecard(scorecard_input)
        self.assertEqual(first, build_scorecard(scorecard_input))
        self.assertEqual(first["decision"], "pass")
        self.assertEqual(first["measurements"]["composite_score"], 90.0)
        self.assertIn("does not measure", first["capability_disclaimer"])

        input_path = self.root / "visual-qa.json"
        input_path.write_text(json.dumps(scorecard_input), encoding="utf-8")
        result = LocalBackend(self.config).generate_visual_qa_scorecard_file(input_path)
        self.assertTrue(Path(result["path"]).exists())
        self.assertEqual(result["scorecard_sha256"], first["scorecard_sha256"])

    def test_visual_qa_rejects_invalid_measurements_and_stop_thresholds(self) -> None:
        invalid = {
            "schema_version": "visual-qa-observations/v1",
            "subject": {"id": "internal-test-shot", "version": "1.0.0"},
            "observations": [
                {
                    "type": "edit_fidelity",
                    "value": 101,
                    "method": "invalid fixture",
                    "observer_ref": "qa-a",
                },
                {
                    "type": "brand_consistency",
                    "value": 80,
                    "method": "fixture",
                    "observer_ref": "brand-a",
                },
            ],
        }
        with self.assertRaisesRegex(VisualQAScorecardError, "from 0 to 100"):
            build_scorecard(invalid)

        stop_input = {
            **invalid,
            "observations": [
                {
                    "type": "edit_fidelity",
                    "value": 49,
                    "method": "explicit measurement fixture",
                    "observer_ref": "qa-a",
                    "severity": "normal",
                },
                {
                    "type": "brand_consistency",
                    "value": 95,
                    "method": "explicit measurement fixture",
                    "observer_ref": "brand-a",
                    "severity": "normal",
                },
            ],
        }
        stop = build_scorecard(stop_input)
        self.assertEqual(stop["decision"], "stop")
        self.assertIn("edit_fidelity below stop threshold", stop["stop_reasons"])


if __name__ == "__main__":
    unittest.main()
