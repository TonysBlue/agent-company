"""Execution backends for agent work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .brandkit import build_campaign_manifest, load_json, validate_brand_kit, write_json
from .campaign_render import (
    CAMPAIGN_RENDER_MANIFEST_FILE,
    CAMPAIGN_RENDER_SCHEMA_VERSION,
    render_campaign_bundle,
    verify_campaign_render_bundle,
)
from .campaign_review import build_campaign_review_record, record_campaign_review
from .config import CompanyConfig
from .product_shot import build_product_shot_manifest
from .prompt_pack import build_prompt_manifest
from .visual_qa import build_scorecard


class BackendError(RuntimeError):
    pass


class LocalBackend:
    """Deterministic stdlib-only backend for product prototyping."""

    name = "local"

    def __init__(self, config: CompanyConfig):
        self.config = config
        self.config.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def generate(self, prompt: str, mode: str = "generate", style: str = "commercial") -> dict[str, str]:
        digest = hashlib.sha256(f"{mode}|{style}|{prompt}".encode("utf-8")).hexdigest()
        file_name = f"{mode}-{digest[:12]}.json"
        path = self.config.artifacts_dir / file_name
        palette = [f"#{digest[i:i+6]}" for i in range(0, 30, 6)]
        payload = {
            "backend": self.name,
            "mode": mode,
            "style": style,
            "prompt": prompt,
            "seed": digest[:16],
            "artifact": {
                "description": f"Deterministic {style} image concept for: {prompt}",
                "palette": palette,
                "edit_plan": [
                    "preserve subject identity",
                    "apply requested style controls",
                    "produce commercially reviewable output metadata",
                ],
            },
        }
        write_json(path, payload)
        return {"path": str(path), "seed": digest[:16], "summary": payload["artifact"]["description"]}

    def validate_brand_kit_file(self, path: Path) -> dict[str, object]:
        brand_kit = load_json(path)
        errors = validate_brand_kit(brand_kit)
        return {"ok": not errors, "errors": errors, "path": str(path)}

    def generate_campaign_manifest_file(self, input_path: Path, output_path: Path | None = None) -> dict[str, object]:
        campaign_input = load_json(input_path)
        manifest = build_campaign_manifest(campaign_input)
        if output_path is None:
            digest = manifest["manifest_sha256"][:12]
            output_path = self.config.artifacts_dir / f"campaign-manifest-{digest}.json"
        write_json(output_path, manifest)
        return {
            "path": str(output_path),
            "manifest_sha256": manifest["manifest_sha256"],
            "variant_count": manifest["variant_count"],
            "brand_version": manifest["brand"]["version"],
        }

    def render_campaign_file(self, input_path: Path, output_dir: Path | None = None) -> dict[str, object]:
        campaign_input = load_json(input_path)
        if output_dir is None:
            manifest = build_campaign_manifest(campaign_input)
            render_version = CAMPAIGN_RENDER_SCHEMA_VERSION.rsplit("/", 1)[-1]
            output_dir = self.config.artifacts_dir / f"campaign-render-{render_version}-{manifest['manifest_sha256'][:12]}"
        rendered = render_campaign_bundle(campaign_input, output_dir)
        return {
            "path": str(output_dir),
            "bundle_sha256": rendered["bundle_sha256"],
            "asset_count": rendered["asset_count"],
            "external_publish_authorized": False,
        }

    def verify_campaign_render_bundle_dir(self, bundle_dir: Path) -> dict[str, object]:
        return verify_campaign_render_bundle(bundle_dir)

    def record_campaign_review_file(
        self,
        bundle_dir: Path,
        decisions_path: Path,
        output_path: Path | None = None,
    ) -> dict[str, object]:
        if output_path is None:
            render_manifest = load_json(bundle_dir / CAMPAIGN_RENDER_MANIFEST_FILE)
            decisions_input = load_json(decisions_path)
            record = build_campaign_review_record(render_manifest, decisions_input)
            output_path = self.config.artifacts_dir / (
                f"campaign-review-v1-{record['bundle_sha256'][:12]}-{record['review_sha256'][:12]}.json"
            )
        return record_campaign_review(bundle_dir, decisions_path, output_path)

    def generate_prompt_manifest_file(self, input_path: Path, output_path: Path | None = None) -> dict[str, object]:
        prompt_pack = load_json(input_path)
        manifest = build_prompt_manifest(prompt_pack)
        if output_path is None:
            digest = manifest["manifest_sha256"][:12]
            output_path = self.config.artifacts_dir / f"prompt-manifest-{digest}.json"
        write_json(output_path, manifest)
        return {
            "path": str(output_path),
            "manifest_sha256": manifest["manifest_sha256"],
            "prompt_count": manifest["prompt_count"],
            "pack_version": manifest["pack"]["version"],
        }

    def generate_product_shot_workflow_file(self, input_path: Path, output_path: Path | None = None) -> dict[str, object]:
        workflow_input = load_json(input_path)
        manifest = build_product_shot_manifest(workflow_input)
        if output_path is None:
            digest = manifest["manifest_sha256"][:12]
            output_path = self.config.artifacts_dir / f"product-shot-manifest-{digest}.json"
        write_json(output_path, manifest)
        return {
            "path": str(output_path),
            "manifest_sha256": manifest["manifest_sha256"],
            "scenario_count": manifest["scenario_count"],
            "workflow_version": manifest["workflow"]["version"],
        }

    def generate_visual_qa_scorecard_file(self, input_path: Path, output_path: Path | None = None) -> dict[str, object]:
        scorecard_input = load_json(input_path)
        scorecard = build_scorecard(scorecard_input)
        if output_path is None:
            digest = scorecard["scorecard_sha256"][:12]
            output_path = self.config.artifacts_dir / f"visual-qa-scorecard-{digest}.json"
        write_json(output_path, scorecard)
        return {
            "path": str(output_path),
            "scorecard_sha256": scorecard["scorecard_sha256"],
            "decision": scorecard["decision"],
            "composite_score": scorecard["measurements"]["composite_score"],
        }


class GuardedCodexBackend:
    """Placeholder for optional guarded Codex execution.

    The MVP intentionally does not call external services. Enabling this backend
    requires explicit config and should still route external or irreversible
    actions through Chairman approval.
    """

    name = "codex"

    def __init__(self, config: CompanyConfig):
        self.config = config
        if not config.codex_enabled:
            raise BackendError("Codex backend is configured but codex_enabled=false")

    def generate(self, prompt: str, mode: str = "generate", style: str = "commercial") -> dict[str, str]:
        raise BackendError("Guarded Codex backend is not implemented in stdlib-only MVP")


def make_backend(config: CompanyConfig) -> LocalBackend | GuardedCodexBackend:
    if config.backend == "local":
        return LocalBackend(config)
    if config.backend == "codex":
        return GuardedCodexBackend(config)
    raise BackendError(f"Unknown backend: {config.backend}")
