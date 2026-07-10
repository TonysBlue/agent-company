"""Execution backends for agent work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .brandkit import build_campaign_manifest, load_json, validate_brand_kit, write_json
from .config import CompanyConfig


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
