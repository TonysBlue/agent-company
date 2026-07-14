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
        parsed = urlparse(path)
        route = parsed.path
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
        if route == "/api/beta/artifact":
            from urllib.parse import parse_qs

            value = parse_qs(parsed.query).get("path", [""])[0]
            try:
                artifact = Path(value).expanduser().resolve(strict=True)
                root = self.artifacts_dir.resolve(strict=True)
                if not artifact.is_file() or not artifact.is_relative_to(root):
                    raise ValueError("artifact path must stay under the local beta artifact root")
                media_type = "image/svg+xml" if artifact.suffix == ".svg" else "application/json; charset=utf-8"
                return Response(200, media_type, artifact.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                return _error(str(exc), 404)
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
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>织象 PixWeave · 商品视觉工作台</title>
  <style>{CSS}</style>
</head>
<body>
  <nav><a class="brand" href="/beta"><i>织</i><span>织象 <small>PixWeave</small></span></a><span class="beta">BETA · 本地工作区</span></nav>
  <main>
    <header class="hero">
      <div><p class="eyebrow">AI 商品视觉工作台</p><h1>一张商品图，快速适配每个销售渠道</h1><p class="subtitle">上传原图，选择发布场景和视觉风格，在一个工作流里完成生成、预览与反馈。</p></div>
      <div class="privacy"><b>素材仅在本机处理</b><span>当前版本不会发布或上传您的内容</span></div>
    </header>
    <ol class="steps"><li class="active"><b>1</b><span>上传商品图</span></li><li><b>2</b><span>设置视觉方案</span></li><li><b>3</b><span>预览与反馈</span></li></ol>
    <section class="workspace">
      <div class="card input-card">
        <div class="section-title"><span>01</span><div><h2>上传商品图</h2><p>支持 PNG、JPG，建议使用纯净背景</p></div></div>
        <label class="dropzone" id="dropzone"><input id="image" type="file" accept="image/png,image/jpeg"><span class="upload-icon">↑</span><strong id="file-label">点击选择或拖放商品图</strong><small>最大 3 MB · 图片不会离开本机</small><img id="source-preview" alt="商品原图预览"></label>
        <div class="section-title compact"><span>02</span><div><h2>选择发布场景</h2><p>我们会自动适配对应构图</p></div></div>
        <div class="choices" id="channels"><button class="choice selected" data-value="电商主图"><b>电商主图</b><small>1:1 聚焦商品</small></button><button class="choice" data-value="社交媒体"><b>社交媒体</b><small>吸睛品牌内容</small></button><button class="choice" data-value="广告横幅"><b>广告横幅</b><small>横版营销素材</small></button></div>
        <div class="section-title compact"><span>03</span><div><h2>选择视觉风格</h2></div></div>
        <div class="style-row" id="styles"><button class="style selected" data-value="清新极简"><i class="mint"></i>清新极简</button><button class="style" data-value="活力促销"><i class="sunset"></i>活力促销</button><button class="style" data-value="高级质感"><i class="night"></i>高级质感</button></div>
        <button class="primary" id="generate" disabled>生成视觉方案 <span>→</span></button>
      </div>
      <div class="card result-card">
        <div class="result-head"><div><p class="eyebrow">实时预览</p><h2>您的视觉方案</h2></div><span id="status-pill">等待上传</span></div>
        <div class="canvas empty" id="canvas"><div class="empty-state"><i>✦</i><h3>创意即将在这里呈现</h3><p>完成左侧三步，即可生成可预览的商品视觉方案</p></div></div>
        <div class="result-actions" id="result-actions" hidden><a id="open-result" class="secondary" target="_blank" rel="noopener">查看完整结果</a><button class="secondary" id="regenerate">重新生成</button></div>
        <div class="feedback" id="feedback" hidden><h3>这个结果对您有帮助吗？</h3><div class="rating" id="rating"><button data-score="5">非常满意</button><button data-score="4">满意</button><button data-score="3">一般</button><button data-score="2">需改进</button></div><textarea id="feedback-text" maxlength="1000" placeholder="告诉我们哪里可以做得更好（选填）"></textarea><button class="feedback-submit" id="feedback-submit">提交反馈</button><p id="feedback-state"></p></div>
      </div>
    </section>
    <footer>织象 PixWeave 内部受控 Beta · 结果为本地草稿，不代表发布授权</footer>
  </main>
<script>{CLIENT_JS}</script>
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
:root { color-scheme: light; --ink:#17211d; --muted:#68766f; --green:#126f5b; --green2:#1a8c73; --mint:#e8f5ef; --line:#dce5df; --paper:#fff; --bg:#f4f7f3; }
* { box-sizing:border-box; }
body { margin:0; background:radial-gradient(circle at 80% 0,#e6f2ec 0,transparent 28%),var(--bg); color:var(--ink); font:14px/1.5 Inter,"Noto Sans SC",system-ui,sans-serif; }
button,a,label { -webkit-tap-highlight-color:transparent; }
button { font:inherit; }
nav { height:66px; padding:0 max(24px,calc((100vw - 1180px)/2)); display:flex; align-items:center; justify-content:space-between; background:rgba(255,255,255,.9); border-bottom:1px solid var(--line); backdrop-filter:blur(12px); }
.brand { display:flex; align-items:center; gap:11px; color:var(--ink); text-decoration:none; font-size:19px; font-weight:800; }
.brand i { display:grid; place-items:center; width:36px; height:36px; border-radius:11px; background:var(--green); color:white; font-style:normal; }
.brand small { display:block; color:var(--muted); font-size:10px; letter-spacing:1.4px; font-weight:600; }
.beta { padding:6px 10px; border-radius:99px; color:var(--green); background:var(--mint); font-size:11px; font-weight:800; letter-spacing:.6px; }
main { max-width:1180px; margin:auto; padding:46px 24px 24px; }
.hero { display:flex; align-items:flex-end; justify-content:space-between; gap:30px; }
.eyebrow { margin:0 0 7px; color:var(--green); text-transform:uppercase; letter-spacing:1.5px; font-size:11px; font-weight:800; }
h1 { max-width:690px; margin:0; font-size:clamp(30px,4.2vw,52px); line-height:1.08; letter-spacing:-2px; }
.subtitle { margin:14px 0 0; color:var(--muted); font-size:16px; }
.privacy { min-width:260px; display:flex; flex-direction:column; padding:14px 17px; border:1px solid #cfe4d8; border-radius:12px; background:rgba(255,255,255,.7); }
.privacy b { color:var(--green); font-size:13px; }.privacy span { color:var(--muted); font-size:11px; }
.steps { margin:36px 0 22px; padding:0; display:grid; grid-template-columns:repeat(3,1fr); list-style:none; }
.steps li { position:relative; display:flex; align-items:center; gap:9px; color:#89958f; font-weight:700; }
.steps li:not(:last-child):after { content:""; height:1px; background:#cfd9d3; position:absolute; left:110px; right:20px; }
.steps b { width:27px; height:27px; display:grid; place-items:center; border:1px solid #cfd9d3; border-radius:50%; background:var(--bg); font-size:12px; z-index:1; }
.steps .active { color:var(--green); }.steps .active b { background:var(--green); border-color:var(--green); color:#fff; }
.workspace { display:grid; grid-template-columns:minmax(390px,.88fr) minmax(460px,1.12fr); gap:20px; align-items:start; }
.card { background:rgba(255,255,255,.94); border:1px solid var(--line); border-radius:18px; box-shadow:0 18px 50px rgba(28,52,42,.07); }
.input-card { padding:24px; }.result-card { padding:24px; position:sticky; top:18px; }
.section-title { display:flex; gap:11px; align-items:flex-start; }.section-title.compact { margin-top:24px; }
.section-title>span { color:var(--green); font-size:11px; font-weight:900; letter-spacing:1px; padding-top:4px; }
h2 { margin:0; font-size:18px; }.section-title p { margin:2px 0 0; color:var(--muted); font-size:12px; }
.dropzone { min-height:174px; margin-top:14px; border:1.5px dashed #a9c5b8; border-radius:14px; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:5px; cursor:pointer; overflow:hidden; background:#f8fbf9; transition:.2s; }
.dropzone:hover,.dropzone.drag { border-color:var(--green); background:var(--mint); }.dropzone input { display:none; }.upload-icon { width:38px;height:38px;display:grid;place-items:center;border-radius:50%;background:var(--mint);color:var(--green);font-size:22px; }.dropzone small { color:var(--muted); }
#source-preview { display:none; width:100%; height:172px; object-fit:contain; background:#eef2ef; }.dropzone.has-image>*:not(img) { display:none; }.dropzone.has-image #source-preview { display:block; }
.choices { margin-top:12px; display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }.choice,.style { border:1px solid var(--line); background:#fff; border-radius:10px; cursor:pointer; color:var(--ink); transition:.15s; }.choice { padding:11px 8px; text-align:left; }.choice b,.choice small { display:block; }.choice small { margin-top:2px; color:var(--muted); font-size:10px; }.choice:hover,.choice.selected,.style:hover,.style.selected { border-color:var(--green); box-shadow:0 0 0 2px var(--mint); }
.style-row { margin-top:12px; display:grid; grid-template-columns:repeat(3,1fr); gap:8px; }.style { padding:8px; text-align:left; font-size:11px; font-weight:700; }.style i { display:block; height:36px; border-radius:6px; margin-bottom:7px; }.mint { background:linear-gradient(135deg,#edf8f2,#b8decf); }.sunset { background:linear-gradient(135deg,#fff0c2,#ee856e); }.night { background:linear-gradient(135deg,#292c36,#8b7c9d); }
.primary { width:100%; margin-top:22px; padding:13px 18px; border:0; border-radius:10px; background:linear-gradient(135deg,var(--green),var(--green2)); color:#fff; font-weight:800; cursor:pointer; box-shadow:0 8px 20px rgba(18,111,91,.2); }.primary:disabled { background:#c8d1cc; box-shadow:none; cursor:not-allowed; }.primary span { float:right; }
.result-head { display:flex; justify-content:space-between; align-items:start; }.result-head>span { padding:5px 9px; border-radius:99px; background:#f0f3f1; color:var(--muted); font-size:10px; font-weight:800; }
.canvas { margin-top:18px; min-height:510px; border-radius:14px; overflow:hidden; display:grid; place-items:center; background:linear-gradient(145deg,#eef4f0,#dfeae4); position:relative; }.canvas:after { content:"内部草稿"; position:absolute; right:12px; bottom:10px; color:rgba(23,33,29,.35); font-size:10px; font-weight:900; letter-spacing:1px; }.empty-state { text-align:center; max-width:260px; color:var(--muted); }.empty-state i { display:grid; place-items:center; width:56px;height:56px;margin:0 auto 14px;border-radius:18px;background:rgba(255,255,255,.7);color:var(--green);font-size:25px;font-style:normal; }.empty-state h3 { margin:0;color:var(--ink); }.empty-state p { font-size:12px; }.canvas img { width:100%;height:510px;object-fit:contain; }.canvas.loading:before { content:"正在编织您的视觉方案…"; color:var(--green); font-weight:800; }
.result-actions { display:flex; gap:8px; margin-top:12px; }.secondary,.feedback-submit { flex:1; border:1px solid var(--line); background:#fff; color:var(--ink); padding:10px; border-radius:9px; text-align:center; text-decoration:none; cursor:pointer; font-weight:700; }
.feedback { margin-top:18px; padding-top:18px; border-top:1px solid var(--line); }.feedback h3 { margin:0 0 10px; font-size:14px; }.rating { display:flex; flex-wrap:wrap; gap:6px; }.rating button { border:1px solid var(--line); background:#fff; border-radius:99px; padding:6px 10px; cursor:pointer; }.rating button.selected { background:var(--mint); border-color:var(--green); color:var(--green); }.feedback textarea { width:100%; min-height:70px; margin-top:10px; padding:10px; border:1px solid var(--line); border-radius:9px; resize:vertical; }.feedback-submit { margin-top:8px; color:#fff; background:var(--green); }.feedback p { margin:6px 0 0; color:var(--green); font-size:11px; }
footer { padding:24px; text-align:center; color:var(--muted); font-size:11px; }
@media(max-width:860px){main{padding-top:30px}.hero{display:block}.privacy{margin-top:20px}.workspace{grid-template-columns:1fr}.result-card{position:static}.steps li:after{display:none}.canvas{min-height:400px}.canvas img{height:400px}}
@media(max-width:520px){nav{padding:0 16px}.beta{font-size:9px}main{padding:24px 14px}.hero h1{letter-spacing:-1px}.steps span{font-size:11px}.choices,.style-row{grid-template-columns:1fr}.choice{text-align:center}.workspace{display:block}.result-card{margin-top:14px}.input-card,.result-card{padding:18px}.canvas{min-height:330px}.canvas img{height:330px}}
"""

CLIENT_JS = r"""
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
let file=null, score=null, result=null;
function select(group,button){$$(group+' button').forEach(x=>x.classList.remove('selected'));button.classList.add('selected')}
$$('#channels button').forEach(b=>b.onclick=()=>select('#channels',b)); $$('#styles button').forEach(b=>b.onclick=()=>select('#styles',b));
const input=$('#image'), drop=$('#dropzone');
function choose(f){if(!f)return;if(!['image/png','image/jpeg'].includes(f.type)||f.size>3*1024*1024){alert('请选择不超过 3 MB 的 PNG 或 JPG 图片');return}file=f;drop.classList.add('has-image');$('#source-preview').src=URL.createObjectURL(f);$('#generate').disabled=false;$('.steps li:nth-child(2)').classList.add('active');$('#status-pill').textContent='可以生成'}
input.onchange=()=>choose(input.files[0]); drop.ondragover=e=>{e.preventDefault();drop.classList.add('drag')};drop.ondragleave=()=>drop.classList.remove('drag');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('drag');choose(e.dataTransfer.files[0])};
function bytesToBase64(buffer){let s='',a=new Uint8Array(buffer);for(let i=0;i<a.length;i+=8192)s+=String.fromCharCode(...a.subarray(i,i+8192));return btoa(s)}
async function generate(){if(!file)return;const button=$('#generate');button.disabled=true;button.firstChild.textContent='正在生成… ';$('#canvas').className='canvas loading';$('#canvas').innerHTML='';$('#status-pill').textContent='生成中';try{const sourceId='product-'+Date.now();const payload={schema_version:'source-image-edit/v1',brand_kit:{brand_name:'PixWeave Beta Brand',brand_version:'1.0.0',colors:{neutrals:['#17211D','#F7FAF8'],primary:'#126F5B',secondary:['#A8D5C2','#E7B86E']},forbidden_elements:['competitor logos','unapproved claims'],logo:{allowed_placements:['top-left','bottom-right'],clearspace_px:24},schema_version:'brand-kit/v1',typography:{body:'Noto Sans',heading:'Inter'}},source_image:{source_id:sourceId,file_name:file.name,media_type:file.type,data_base64:bytesToBase64(await file.arrayBuffer()),provenance:{schema_version:'provenance/v1',source_id:sourceId,parent_lineage:[],source_category:'uploaded_source_image',origin:'local_beta_user_upload',rights_basis:'user_attested_for_internal_beta',rights_evidence_ref:'local-beta-upload',likeness_status:'none',trademark_review_status:'pending',data_classification:'user_provided_non_sensitive',retention_class:'local_beta',policy_flags:['internal_only'],reviewer_ref:'local-user',review_decision:'approved_internal'}},operations:[{id:'brand-style',type:'branded_overlay',text:$('#channels .selected').dataset.value+' · '+$('#styles .selected').dataset.value,placement:'bottom'}]};
const res=await fetch('/api/beta/source-edit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}),data=await res.json();if(!res.ok)throw Error(data.error||'生成失败');result=data;const manifestRes=await fetch('/api/beta/artifact?path='+encodeURIComponent(data.edit_manifest)),manifest=await manifestRes.json();if(!manifestRes.ok)throw Error(manifest.error||'结果读取失败');const assetPath=data.path+'/'+manifest.assets[0].file;$('#canvas').className='canvas';$('#canvas').innerHTML='<img src="/api/beta/artifact?path='+encodeURIComponent(assetPath)+'" alt="生成结果预览">';$('#status-pill').textContent='本地草稿已生成';$('#open-result').href='/api/beta/artifact?path='+encodeURIComponent(data.review_gallery);$('#result-actions').hidden=false;$('#feedback').hidden=false;$('.steps li:nth-child(3)').classList.add('active');}catch(e){$('#canvas').className='canvas empty';$('#canvas').innerHTML='<div class="empty-state"><h3>暂时无法生成</h3><p>'+String(e.message).replace(/[<>]/g,'')+'</p></div>';$('#status-pill').textContent='请重试'}finally{button.disabled=false;button.firstChild.textContent='生成视觉方案 '}}
$('#generate').onclick=generate;$('#regenerate').onclick=generate;$$('#rating button').forEach(b=>b.onclick=()=>{select('#rating',b);score=Number(b.dataset.score)});
$('#feedback-submit').onclick=async()=>{if(!score){$('#feedback-state').textContent='请先选择满意度';return}const id='beta-feedback-'+Date.now(),message=$('#feedback-text').value.trim()||('满意度 '+score+'/5');const payload={schema_version:'feedback-submission/v1',submission_id:id,product_version:'local-beta',entry_point:'workflow-result',category:score>=4?'workflow':'quality',severity:score>=3?'low':'medium',message,context:{workflow_id:'source-image-edit',artifact_ref:result?result.bundle_sha256.slice(0,24):'local-draft'},contact_consent:false,contains_sensitive_data:false,honeypot:''};const res=await fetch('/api/beta/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const data=await res.json();$('#feedback-state').textContent=res.ok?'感谢反馈，已安全保存在本机。':(data.error||'提交失败')};
"""



if __name__ == "__main__":
    raise SystemExit(main())
