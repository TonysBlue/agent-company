from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.beta_product import LocalBetaProductApp
from agent_company.config import load_config


class LocalBetaProductTest(unittest.TestCase):
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
        self.app = LocalBetaProductApp(self.config)
        self.repo = Path(__file__).parents[1]

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _post_json(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        response = self.app.handle_post(path, json.dumps(payload).encode("utf-8"), "application/json")
        self.assertEqual(response.status, 200, response.body)
        return json.loads(response.body)

    def test_home_and_status_show_internal_no_publish_controls(self) -> None:
        home = self.app.render_path("/beta")
        status = json.loads(self.app.render_path("/api/beta/status").body)

        self.assertIn("LOCAL INTERNAL BETA", home.body)
        self.assertIn("external_publish_authorized: false", home.body)
        self.assertTrue(status["controls"]["internal_only"])
        self.assertFalse(status["controls"]["external_publish_authorized"])
        self.assertFalse(status["controls"]["production_deploy_authorized"])
        self.assertEqual(status["render_provider"]["name"], "local-svg")
        self.assertTrue(status["render_provider"]["dependency_free"])

    def test_render_review_and_feedback_flow_writes_bounded_local_artifacts(self) -> None:
        campaign = json.loads((self.repo / "examples" / "campaign.json").read_text(encoding="utf-8"))

        render = self._post_json("/api/beta/render", campaign)

        render_dir = Path(render["path"])
        self.assertTrue(render_dir.is_dir())
        self.assertEqual(render["schema_version"], "campaign-render/v2")
        self.assertEqual(render["asset_count"], 16)
        self.assertEqual(render["provider"], "local-svg")
        self.assertFalse(render["external_publish_authorized"])
        manifest = json.loads(Path(render["render_manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["provider"], "local-svg")
        self.assertEqual(manifest["assets"][0]["media_type"], "image/svg+xml")
        self.assertEqual(manifest["assets"][0]["provenance"]["render_sha256"], manifest["assets"][0]["sha256"])
        gallery = Path(render["review_gallery"]).read_text(encoding="utf-8")
        self.assertIn("review_state: draft", gallery)
        self.assertIn("external_publish_authorized: false", gallery)

        decisions = json.loads((self.repo / "examples" / "campaign-review-decisions.json").read_text(encoding="utf-8"))
        review = self._post_json("/api/beta/review", {"bundle_path": str(render_dir), "decisions": decisions})

        self.assertEqual(review["schema_version"], "campaign-review/v1")
        self.assertEqual(review["asset_count"], 16)
        self.assertFalse(review["external_publish_authorized"])
        review_record = json.loads(Path(review["path"]).read_text(encoding="utf-8"))
        self.assertEqual(review_record["publication_authorization"], "none")
        self.assertFalse(review_record["external_publish_authorized"])

        feedback_input = json.loads((self.repo / "examples" / "feedback-submission.json").read_text(encoding="utf-8"))
        feedback = self._post_json("/api/beta/feedback", feedback_input)

        self.assertEqual(feedback["state"], "received")
        self.assertFalse(feedback["external_action_authorized"])
        self.assertFalse(feedback["contact_retained"])
        retained = json.loads(Path(feedback["path"]).read_text(encoding="utf-8"))
        self.assertNotIn("honeypot", retained)
        self.assertFalse(retained["external_action_authorized"])

    def test_malformed_and_sensitive_input_fail_closed_without_artifacts(self) -> None:
        before = sorted(self.app.artifacts_dir.rglob("*"))

        malformed = self.app.handle_post("/api/beta/render", b"{", "application/json")
        self.assertEqual(malformed.status, 400)

        campaign = json.loads((self.repo / "examples" / "campaign.json").read_text(encoding="utf-8"))
        del campaign["assets"][0]["provenance"]
        invalid_campaign = self.app.handle_post("/api/beta/render", json.dumps(campaign).encode("utf-8"), "application/json")
        self.assertEqual(invalid_campaign.status, 400)
        self.assertIn("provenance", invalid_campaign.body)

        feedback_input = json.loads((self.repo / "examples" / "feedback-submission.json").read_text(encoding="utf-8"))
        feedback_input["contains_sensitive_data"] = True
        sensitive = self.app.handle_post("/api/beta/feedback", json.dumps(feedback_input).encode("utf-8"), "application/json")
        self.assertEqual(sensitive.status, 400)
        self.assertIn("sensitive submissions are rejected", sensitive.body)

        after = sorted(self.app.artifacts_dir.rglob("*"))
        self.assertEqual(before, after)

    def test_review_rejects_tampered_bundle_without_retaining_decisions(self) -> None:
        campaign = json.loads((self.repo / "examples" / "campaign.json").read_text(encoding="utf-8"))
        render = self._post_json("/api/beta/render", campaign)
        render_dir = Path(render["path"])
        render_manifest = json.loads((render_dir / "render-manifest.json").read_text(encoding="utf-8"))
        first_asset = render_manifest["assets"][0]["file"]
        (render_dir / first_asset).write_text("tampered", encoding="utf-8")
        before = sorted(path.relative_to(self.app.artifacts_dir) for path in self.app.artifacts_dir.rglob("*"))

        decisions = json.loads((self.repo / "examples" / "campaign-review-decisions.json").read_text(encoding="utf-8"))
        response = self.app.handle_post(
            "/api/beta/review",
            json.dumps({"bundle_path": str(render_dir), "decisions": decisions}).encode("utf-8"),
            "application/json",
        )

        self.assertEqual(response.status, 400)
        self.assertIn("checksum mismatch", response.body)
        after = sorted(path.relative_to(self.app.artifacts_dir) for path in self.app.artifacts_dir.rglob("*"))
        self.assertEqual(before, after)

    def test_review_rejects_bundle_path_outside_local_beta_root(self) -> None:
        outside = self.root / "outside-render"
        outside.mkdir()
        before = sorted(path.relative_to(self.app.artifacts_dir) for path in self.app.artifacts_dir.rglob("*"))

        decisions = json.loads((self.repo / "examples" / "campaign-review-decisions.json").read_text(encoding="utf-8"))
        response = self.app.handle_post(
            "/api/beta/review",
            json.dumps({"bundle_path": str(outside), "decisions": decisions}).encode("utf-8"),
            "application/json",
        )

        self.assertEqual(response.status, 400)
        self.assertIn("local beta artifact root", response.body)
        after = sorted(path.relative_to(self.app.artifacts_dir) for path in self.app.artifacts_dir.rglob("*"))
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
