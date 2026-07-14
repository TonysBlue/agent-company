from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
import zlib
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

        self.assertIn("织象 PixWeave", home.body)
        self.assertIn("上传商品图", home.body)
        self.assertIn("选择发布场景", home.body)
        self.assertIn("预览与反馈", home.body)
        self.assertNotIn("POST /api/beta", home.body)
        self.assertIn("结果为本地草稿，不代表发布授权", home.body)
        self.assertTrue(status["controls"]["internal_only"])
        self.assertFalse(status["controls"]["external_publish_authorized"])
        self.assertFalse(status["controls"]["production_deploy_authorized"])
        self.assertEqual(status["render_provider"]["name"], "local-svg")
        self.assertTrue(status["render_provider"]["dependency_free"])
        self.assertEqual(status["source_edit_provider"]["name"], "local-source-edit")
        self.assertEqual(status["source_edit_provider"]["operations"], ["crop", "branded_overlay"])
        self.assertIn("/api/beta/source-edit", home.body)
        self.assertIn("/api/beta/feedback", home.body)
        self.assertIn("/api/beta/artifact", home.body)

    def test_artifact_route_serves_only_local_beta_files(self) -> None:
        local = self.app.artifacts_dir / "preview.svg"
        local.write_text("<svg></svg>", encoding="utf-8")
        served = self.app.render_path(f"/api/beta/artifact?path={local}")
        outside = self.root / "outside.svg"
        outside.write_text("<svg></svg>", encoding="utf-8")
        rejected = self.app.render_path(f"/api/beta/artifact?path={outside}")

        self.assertEqual(served.status, 200)
        self.assertEqual(served.content_type, "image/svg+xml")
        self.assertEqual(rejected.status, 404)
        self.assertIn("local beta artifact root", rejected.body)

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

    def test_source_image_edit_accepts_bounded_upload_and_writes_lineage(self) -> None:
        payload = self._source_edit_payload()

        result = self._post_json("/api/beta/source-edit", payload)

        edit_dir = Path(result["path"])
        self.assertTrue(edit_dir.is_dir())
        self.assertEqual(result["schema_version"], "source-image-edit/v1")
        self.assertEqual(result["asset_count"], 2)
        self.assertEqual(result["provider"], "local-source-edit")
        self.assertEqual(result["operations"], ["crop", "branded_overlay"])
        self.assertFalse(result["external_publish_authorized"])
        manifest = json.loads(Path(result["edit_manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["source"]["media_type"], "image/png")
        self.assertEqual(manifest["source"]["sha256"], result["source_sha256"])
        self.assertEqual(manifest["assets"][0]["source_sha256"], result["source_sha256"])
        self.assertEqual(manifest["assets"][0]["provenance"]["source_sha256"], result["source_sha256"])
        self.assertEqual(manifest["assets"][0]["provenance"]["output_sha256"], manifest["assets"][0]["sha256"])
        self.assertTrue((edit_dir / manifest["source"]["file"]).is_file())
        self.assertTrue(all((edit_dir / asset["file"]).is_file() for asset in manifest["assets"]))
        gallery = Path(result["review_gallery"]).read_text(encoding="utf-8")
        self.assertIn("review_state: draft", gallery)
        self.assertIn("external_publish_authorized: false", gallery)

    def test_source_image_edit_rejects_unsafe_and_polyglot_uploads_without_artifacts(self) -> None:
        before = sorted(self.app.artifacts_dir.rglob("*"))

        traversal = self._source_edit_payload()
        traversal["source_image"]["file_name"] = "../source.png"  # type: ignore[index]
        traversal_response = self.app.handle_post(
            "/api/beta/source-edit",
            json.dumps(traversal).encode("utf-8"),
            "application/json",
        )

        self.assertEqual(traversal_response.status, 400)
        self.assertIn("safe file name", traversal_response.body)
        self.assertEqual(before, sorted(self.app.artifacts_dir.rglob("*")))

        polyglot = self._source_edit_payload()
        raw = base64.b64decode(polyglot["source_image"]["data_base64"])  # type: ignore[index]
        polyglot["source_image"]["data_base64"] = base64.b64encode(raw + b"<script>").decode("ascii")  # type: ignore[index]
        polyglot_response = self.app.handle_post(
            "/api/beta/source-edit",
            json.dumps(polyglot).encode("utf-8"),
            "application/json",
        )

        self.assertEqual(polyglot_response.status, 400)
        self.assertIn("polyglot", polyglot_response.body)
        self.assertEqual(before, sorted(self.app.artifacts_dir.rglob("*")))

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

    def _source_edit_payload(self) -> dict[str, object]:
        brand_kit = json.loads((self.repo / "examples" / "brand-kit.json").read_text(encoding="utf-8"))
        return {
            "schema_version": "source-image-edit/v1",
            "brand_kit": brand_kit,
            "source_image": {
                "source_id": "source-demo-1",
                "file_name": "source-demo.png",
                "media_type": "image/png",
                "data_base64": base64.b64encode(_png_bytes(12, 10)).decode("ascii"),
                "provenance": {
                    "schema_version": "provenance/v1",
                    "source_id": "source-demo-1",
                    "parent_lineage": [],
                    "source_category": "uploaded_source_image",
                    "origin": "local_test_fixture",
                    "rights_basis": "synthetic_internal_fixture",
                    "rights_evidence_ref": "tests/test_beta_product.py",
                    "likeness_status": "none",
                    "trademark_review_status": "approved_internal",
                    "data_classification": "synthetic",
                    "retention_class": "test_fixture",
                    "policy_flags": ["internal_only"],
                    "reviewer_ref": "test",
                    "review_decision": "approved_internal",
                },
            },
            "operations": [
                {"id": "crop-square", "type": "crop", "crop": {"x": 2, "y": 1, "width": 6, "height": 6}},
                {"id": "brand-overlay", "type": "branded_overlay", "text": "Internal draft", "placement": "bottom"},
            ],
        }


def _png_bytes(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return len(data).to_bytes(4, "big") + kind + data + crc.to_bytes(4, "big")

    scanline = b"\x00" + b"\xff\xff\xff" * width
    raw = scanline * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


if __name__ == "__main__":
    unittest.main()
