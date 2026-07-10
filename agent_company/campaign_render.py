"""Deterministic SVG rendering for provenance-gated campaign variants."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from html import escape
from pathlib import Path
from typing import Any

from .brandkit import build_campaign_manifest, stable_sha256, write_json


CAMPAIGN_RENDER_SCHEMA_VERSION = "campaign-render/v1"


class CampaignRenderError(ValueError):
    """Raised when a campaign render bundle cannot be produced safely."""


def build_svg(variant: dict[str, Any], brand_kit: dict[str, Any], brand_name: str) -> str:
    """Build a self-contained SVG using only validated campaign fields."""
    width = variant["format"]["width"]
    height = variant["format"]["height"]
    primary = brand_kit["colors"]["primary"].lower()
    secondary = brand_kit["colors"]["secondary"][0].lower()
    neutral_values = brand_kit["colors"].get("neutrals", [])
    dark = neutral_values[0].lower() if neutral_values else "#111827"
    light = neutral_values[-1].lower() if neutral_values else "#f9fafb"
    margin = max(24, min(width, height) // 18)
    headline_size = max(28, min(width, height) // 13)
    label_size = max(14, min(width, height) // 40)
    product_width = width * 0.34
    product_height = height * 0.42
    product_x = width * 0.58
    product_y = height * 0.25
    headline = escape(variant["headline"], quote=True)
    safe_brand = escape(brand_name, quote=True)
    safe_asset = escape(variant["asset_id"], quote=True)
    safe_channel = escape(variant["channel"], quote=True)
    safe_variant = escape(variant["id"], quote=True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">\n'
        f'  <title id="title">{headline}</title>\n'
        f'  <desc id="desc">Internal draft for {safe_brand}; source {safe_asset}; variant {safe_variant}</desc>\n'
        f'  <rect width="{width}" height="{height}" fill="{light}"/>\n'
        f'  <rect width="{max(12, width // 90)}" height="{height}" fill="{primary}"/>\n'
        f'  <circle cx="{width * 0.82:.2f}" cy="{height * 0.16:.2f}" r="{min(width, height) * 0.22:.2f}" fill="{secondary}" opacity="0.22"/>\n'
        f'  <rect x="{product_x:.2f}" y="{product_y:.2f}" width="{product_width:.2f}" height="{product_height:.2f}" rx="{min(width, height) * 0.035:.2f}" fill="{primary}"/>\n'
        f'  <circle cx="{product_x + product_width / 2:.2f}" cy="{product_y + product_height / 2:.2f}" r="{min(product_width, product_height) * 0.24:.2f}" fill="{secondary}"/>\n'
        f'  <text x="{product_x + product_width / 2:.2f}" y="{product_y + product_height + label_size * 1.8:.2f}" text-anchor="middle" font-family="sans-serif" font-size="{label_size}" fill="{dark}">{safe_asset}</text>\n'
        f'  <text x="{margin}" y="{margin + label_size}" font-family="sans-serif" font-size="{label_size}" font-weight="700" fill="{primary}">{safe_brand}</text>\n'
        f'  <foreignObject x="{margin}" y="{height * 0.30:.2f}" width="{width * 0.48:.2f}" height="{height * 0.42:.2f}">\n'
        f'    <div xmlns="http://www.w3.org/1999/xhtml" style="font-family:sans-serif;font-size:{headline_size}px;font-weight:800;line-height:1.08;color:{dark};overflow-wrap:anywhere">{headline}</div>\n'
        '  </foreignObject>\n'
        f'  <text x="{margin}" y="{height - margin:.2f}" font-family="sans-serif" font-size="{label_size}" fill="{dark}" opacity="0.72">DRAFT | {safe_channel} | {safe_variant}</text>\n'
        '</svg>\n'
    )


def render_campaign_bundle(data: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Render a complete bundle through a staging directory, then rename it atomically."""
    manifest = build_campaign_manifest(data)
    bundle_basis = {
        "schema_version": CAMPAIGN_RENDER_SCHEMA_VERSION,
        "campaign_manifest_sha256": manifest["manifest_sha256"],
    }
    bundle_id = stable_sha256(bundle_basis)
    if output_dir.exists():
        existing_manifest = output_dir / "render-manifest.json"
        if existing_manifest.is_file():
            import json

            try:
                existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CampaignRenderError(f"output directory exists but is not a valid render bundle: {output_dir}") from exc
            if existing.get("bundle_sha256") == bundle_id:
                return existing
        raise CampaignRenderError(f"output directory already exists with different content: {output_dir}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        rendered = []
        for variant in manifest["variants"]:
            svg = build_svg(variant, data["brand_kit"], manifest["brand"]["name"])
            name = f"{variant['id']}.svg"
            path = staging / name
            path.write_text(svg, encoding="utf-8")
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
            rendered.append({
                "variant_id": variant["id"],
                "file": name,
                "width": variant["format"]["width"],
                "height": variant["format"]["height"],
                "sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
                "review_state": "draft",
            })
        render_manifest = {
            **bundle_basis,
            "bundle_sha256": bundle_id,
            "asset_count": len(rendered),
            "assets": rendered,
            "external_publish_authorized": False,
            "capability_disclaimer": "Rendered SVG files are internal draft creatives, not published assets or evidence of visual quality.",
        }
        write_json(staging / "render-manifest.json", render_manifest)
        os.replace(staging, output_dir)
        staging = None
        return render_manifest
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
