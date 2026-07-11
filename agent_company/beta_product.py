"""Local-only beta product HTTP interface for internal campaign review."""

from __future__ import annotations

import argparse
import html
import json
import os
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .brandkit import BrandKitError, build_campaign_manifest, stable_sha256, write_json
from .campaign_render import (
    CAMPAIGN_GALLERY_FILE,
    CAMPAIGN_RENDER_MANIFEST_FILE,
    render_campaign_bundle,
    verify_campaign_render_bundle,
)
from .campaign_review import CampaignReviewError, build_campaign_review_record, record_campaign_review
from .config import CompanyConfig, load_config
from .feedback import FeedbackError, capture_feedback
from .local_image_render import LOCAL_IMAGE_RENDER_PROVIDER, LOCAL_IMAGE_RENDER_PROVIDER_VERSION
from .source_image_edit import (
    SOURCE_IMAGE_EDIT_GALLERY_FILE,
    SOURCE_IMAGE_EDIT_MANIFEST_FILE,
    SOURCE_IMAGE_EDIT_PROVIDER,
    SOURCE_IMAGE_EDIT_PROVIDER_VERSION,
    SourceImageEditError,
    create_source_image_edit_bundle,
)


MAX_JSON_BYTES = 3 * 1024 * 1024


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: str


def _json_response(payload: dict[str, Any], status: int = 200) -> Response:
    return Response(
        status,
        "application/json; charset=utf-8",
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _error(message: str, status: int = 400) -> Response:
    return _json_response({
        "ok": False,
        "error": message,
        "external_publish_authorized": False,
        "external_action_authorized": False,
    }, status)


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _safe_suffix(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value)[:80].strip(".-_") or "record"


class LocalBetaProductApp:
    """Bounded local beta interface that composes validated domain functions."""

    def __init__(self, config: CompanyConfig):
        self.config = config
        self.artifacts_dir = config.artifacts_dir / "local-beta"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def render_path(self, path: str) -> Response:
        route = urlparse(path).path
        if route == "/":
            return Response(302, "text/plain; charset=utf-8", "/beta")
        if route == "/healthz":
            return _json_response({
                "ok": True,
                "product": self.config.product_name,
                "mode": "local_internal_beta",
                "external_publish_authorized": False,
                "external_action_authorized": False,
            })
        if route == "/api/beta/status":
            return _json_response(self.status_payload())
        if route == "/beta":
            return Response(200, "text/html; charset=utf-8", self.render_home())
        return Response(404, "text/plain; charset=utf-8", "not found\n")

    def handle_post(self, path: str, raw_body: bytes, content_type: str = "application/json") -> Response:
        route = urlparse(path).path
        if route not in {"/api/beta/render", "/api/beta/review", "/api/beta/feedback", "/api/beta/source-edit"}:
            return Response(404, "text/plain; charset=utf-8", "not found\n")
        if len(raw_body) > MAX_JSON_BYTES:
            return _error(f"JSON body must be at most {MAX_JSON_BYTES} bytes", 413)
        if "application/json" not in content_type.lower():
            return _error("content-type must be application/json", 415)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _error(f"invalid JSON: {exc}")
        if not isinstance(payload, dict):
            return _error("top-level JSON value must be an object")

        try:
            if route == "/api/beta/render":
                return _json_response(self.render_campaign(payload))
            if route == "/api/beta/review":
                return _json_response(self.record_review(payload))
            if route == "/api/beta/feedback":
                return _json_response(self.capture_feedback(payload))
            if route == "/api/beta/source-edit":
                return _json_response(self.edit_source_image(payload))
        except (BrandKitError, CampaignReviewError, FeedbackError, SourceImageEditError, OSError, ValueError) as exc:
            return _error(str(exc))
        raise AssertionError(route)

    def status_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "product": self.config.product_name,
            "mode": "local_internal_beta",
            "artifact_root": str(self.artifacts_dir),
            "routes": {
                "render": "/api/beta/render",
                "review": "/api/beta/review",
                "feedback": "/api/beta/feedback",
                "source_edit": "/api/beta/source-edit",
            },
            "render_provider": {
                "name": LOCAL_IMAGE_RENDER_PROVIDER,
                "version": LOCAL_IMAGE_RENDER_PROVIDER_VERSION,
                "dependency_free": True,
                "asset_type": "svg",
            },
            "source_edit_provider": {
                "name": SOURCE_IMAGE_EDIT_PROVIDER,
                "version": SOURCE_IMAGE_EDIT_PROVIDER_VERSION,
                "dependency_free": True,
                "accepted_media_types": ["image/png", "image/jpeg"],
                "operations": ["crop", "branded_overlay"],
                "asset_type": "svg",
            },
            "controls": {
                "internal_only": True,
                "external_publish_authorized": False,
                "external_action_authorized": False,
                "production_deploy_authorized": False,
                "pricing_authorized": False,
                "payments_authorized": False,
                "outreach_authorized": False,
            },
        }

    def edit_source_image(self, payload: dict[str, Any]) -> dict[str, Any]:
        bundle_basis = stable_sha256({
            "source_image": payload.get("source_image", {}),
            "brand_kit": payload.get("brand_kit", {}),
            "operations": payload.get("operations", []),
        })
        edit_dir = self.artifacts_dir / f"source-edit-v1-{bundle_basis[:12]}"
        manifest = create_source_image_edit_bundle(payload, edit_dir)
        return {
            "ok": True,
            "schema_version": manifest["schema_version"],
            "path": str(edit_dir),
            "edit_manifest": str(edit_dir / SOURCE_IMAGE_EDIT_MANIFEST_FILE),
            "review_gallery": str(edit_dir / SOURCE_IMAGE_EDIT_GALLERY_FILE),
            "bundle_sha256": manifest["bundle_sha256"],
            "source_sha256": manifest["source"]["sha256"],
            "asset_count": manifest["asset_count"],
            "provider": manifest["provider"],
            "provider_version": manifest["provider_version"],
            "operations": [asset["operation_type"] for asset in manifest["assets"]],
            "external_publish_authorized": False,
            "external_action_authorized": False,
            "capability_disclaimer": manifest["capability_disclaimer"],
        }

    def render_campaign(self, campaign_input: dict[str, Any]) -> dict[str, Any]:
        manifest = build_campaign_manifest(campaign_input)
        render_dir = self.artifacts_dir / f"campaign-render-v2-{manifest['manifest_sha256'][:12]}"
        rendered = render_campaign_bundle(campaign_input, render_dir)
        return {
            "ok": True,
            "schema_version": rendered["schema_version"],
            "path": str(render_dir),
            "render_manifest": str(render_dir / CAMPAIGN_RENDER_MANIFEST_FILE),
            "review_gallery": str(render_dir / CAMPAIGN_GALLERY_FILE),
            "bundle_sha256": rendered["bundle_sha256"],
            "campaign_manifest_sha256": rendered["campaign_manifest_sha256"],
            "asset_count": rendered["asset_count"],
            "provider": rendered["provider"],
            "provider_version": rendered["provider_version"],
            "external_publish_authorized": False,
            "external_action_authorized": False,
            "capability_disclaimer": rendered["capability_disclaimer"],
        }

    def record_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        bundle_value = payload.get("bundle_path")
        decisions = payload.get("decisions")
        if not isinstance(bundle_value, str) or not bundle_value.strip():
            raise CampaignReviewError("bundle_path must be a non-empty string")
        if not isinstance(decisions, dict):
            raise CampaignReviewError("decisions must be an object")
        bundle_dir = self._resolve_local_beta_bundle(bundle_value)
        verify_campaign_render_bundle(bundle_dir)
        render_manifest = json.loads((bundle_dir / CAMPAIGN_RENDER_MANIFEST_FILE).read_text(encoding="utf-8"))
        review_record = build_campaign_review_record(render_manifest, decisions)
        basis = stable_sha256({
            "bundle_sha256": review_record["bundle_sha256"],
            "review_sha256": review_record["review_sha256"],
        })
        decisions_path = self.artifacts_dir / f"campaign-review-decisions-{basis[:12]}.json"
        output_path = self.artifacts_dir / f"campaign-review-v1-{review_record['bundle_sha256'][:12]}-{review_record['review_sha256'][:12]}.json"
        write_json(decisions_path, decisions)
        result = record_campaign_review(bundle_dir, decisions_path, output_path)
        return {
            "ok": True,
            **result,
            "decisions_path": str(decisions_path),
            "external_action_authorized": False,
        }

    def _resolve_local_beta_bundle(self, value: str) -> Path:
        try:
            root = self.artifacts_dir.resolve(strict=True)
            bundle_dir = Path(value).expanduser().resolve(strict=True)
        except OSError as exc:
            raise CampaignReviewError(f"bundle_path is not a readable local beta bundle: {value}") from exc
        if not bundle_dir.is_dir():
            raise CampaignReviewError("bundle_path must be a directory")
        if not bundle_dir.is_relative_to(root):
            raise CampaignReviewError("bundle_path must stay under the local beta artifact root")
        return bundle_dir

    def capture_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        submission_id = payload.get("submission_id")
        if not isinstance(submission_id, str) or not submission_id.strip():
            raise FeedbackError("submission_id must be a non-empty string")
        output = self.artifacts_dir / f"feedback-submission-{_safe_suffix(submission_id)}.json"
        record = capture_feedback(payload, output)
        return {
            "ok": True,
            "path": str(output),
            "schema_version": record["schema_version"],
            "submission_id": record["submission_id"],
            "submission_sha256": record["submission_sha256"],
            "state": record["state"],
            "category": record["category"],
            "severity": record["severity"],
            "contact_retained": bool(record.get("contact")),
            "external_action_authorized": False,
        }

    def render_home(self) -> str:
        status = self.status_payload()
        controls = "".join(
            f"<li><span>{_escape(key)}</span><strong>{_escape(value)}</strong></li>"
            for key, value in status["controls"].items()
        )
        example = json.dumps({
            "render": "POST /api/beta/render with examples/campaign.json body",
            "review": "POST /api/beta/review with bundle_path and campaign-review-decisions/v1 decisions",
            "feedback": "POST /api/beta/feedback with feedback-submission/v1 body",
            "source_edit": "POST /api/beta/source-edit with source-image-edit/v1 PNG/JPEG base64 body and crop/branded_overlay operations",
            "provider": f"{LOCAL_IMAGE_RENDER_PROVIDER} {LOCAL_IMAGE_RENDER_PROVIDER_VERSION} dependency-free SVG",
            "source_edit_provider": f"{SOURCE_IMAGE_EDIT_PROVIDER} {SOURCE_IMAGE_EDIT_PROVIDER_VERSION} dependency-free SVG edits",
        }, indent=2)
        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Local Beta · {_escape(self.config.product_name)}</title>
  <style>{CSS}</style>
</head>
<body>
  <main>
    <header>
      <p>LOCAL INTERNAL BETA</p>
      <h1>{_escape(self.config.product_name)} campaign review</h1>
      <div class="controls">
        <span>internal_only: true</span>
        <span>external_publish_authorized: false</span>
        <span>production_deploy_authorized: false</span>
      </div>
    </header>
    <section>
      <h2>Controls</h2>
      <ul class="controls-list">{controls}</ul>
    </section>
    <section>
      <h2>Local API</h2>
      <pre>{_escape(example)}</pre>
    </section>
  </main>
</body>
</html>"""


class LocalBetaProductHandler(BaseHTTPRequestHandler):
    app: LocalBetaProductApp

    def do_GET(self) -> None:
        self._send(self.app.render_path(self.path))

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(_error("invalid content-length"))
            return
        if length > MAX_JSON_BYTES:
            self._send(_error(f"JSON body must be at most {MAX_JSON_BYTES} bytes", 413))
            return
        body = self.rfile.read(length)
        self._send(self.app.handle_post(self.path, body, self.headers.get("Content-Type", "")))

    def _send(self, response: Response) -> None:
        if response.status == 302:
            self.send_response(302)
            self.send_header("Location", response.body)
            self.end_headers()
            return
        encoded = response.body.encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def serve(config: CompanyConfig, host: str = "127.0.0.1", port: int = 18112) -> None:
    app = LocalBetaProductApp(config)

    class BoundHandler(LocalBetaProductHandler):
        pass

    BoundHandler.app = app
    server = ThreadingHTTPServer((host, port), BoundHandler)
    print(f"local beta product listening on http://{host}:{port}/beta", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-only internal beta product interface")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default=os.environ.get("AGENT_COMPANY_BETA_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_COMPANY_BETA_PORT", "18112")))
    args = parser.parse_args(argv)
    serve(load_config(args.config), args.host, args.port)
    return 0


CSS = """
:root {
  color-scheme: light;
  --bg: #f6f7f4;
  --panel: #ffffff;
  --line: #d8ddd3;
  --text: #1f2724;
  --muted: #65716b;
  --accent: #0f8b7c;
  --warn: #9a6a00;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
main { max-width: 980px; margin: 0 auto; padding: 28px; }
header, section {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 18px;
  margin-bottom: 14px;
}
header p { margin: 0 0 6px; color: var(--accent); font-weight: 700; }
h1 { margin: 0; font-size: 28px; letter-spacing: 0; }
h2 { margin: 0 0 12px; font-size: 17px; letter-spacing: 0; }
.controls { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.controls span {
  border: 1px solid #d6bd72;
  background: #fff7dc;
  color: var(--warn);
  border-radius: 6px;
  padding: 6px 9px;
  font-weight: 700;
}
.controls-list { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
.controls-list li { display: flex; justify-content: space-between; gap: 14px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
.controls-list span { color: var(--muted); }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f0f2ec; padding: 12px; border-radius: 6px; }
@media (max-width: 720px) {
  main { padding: 16px; }
  .controls-list li { display: block; }
}
"""


if __name__ == "__main__":
    raise SystemExit(main())
