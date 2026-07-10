"""Deterministic SVG rendering for provenance-gated campaign variants."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
from html import escape
from pathlib import Path
from typing import Any

from .brandkit import build_campaign_manifest, stable_sha256, write_json


CAMPAIGN_RENDER_SCHEMA_VERSION = "campaign-render/v2"
CAMPAIGN_GALLERY_FILE = "review-gallery.html"


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


def build_review_gallery(
    manifest: dict[str, Any],
    render_manifest: dict[str, Any],
    rendered_svgs: dict[str, str],
) -> str:
    """Build a deterministic offline HTML review gallery for a render bundle."""
    assets_by_variant = {asset["variant_id"]: asset for asset in render_manifest["assets"]}
    cards = []
    for variant in manifest["variants"]:
        asset = assets_by_variant[variant["id"]]
        svg = rendered_svgs[asset["file"]]
        metadata = {
            "variant_id": variant["id"],
            "campaign": manifest["campaign"]["name"],
            "brand": manifest["brand"],
            "asset_id": variant["asset_id"],
            "copy_id": variant["copy_id"],
            "channel": variant["channel"],
            "format": variant["format"],
            "headline": variant["headline"],
            "review_state": variant["review_state"],
            "external_publish_authorized": render_manifest["external_publish_authorized"],
            "sha256": asset["sha256"],
            "provenance": variant["provenance"],
            "brand_controls": variant["brand_controls"],
        }
        metadata_json = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True)
        svg_src = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
        cards.append(
            '    <article class="variant">\n'
            f'      <h2>{escape(variant["id"], quote=True)}</h2>\n'
            f'      <img src="{escape(svg_src, quote=True)}" alt="Draft SVG {escape(variant["id"], quote=True)}" />\n'
            '      <dl>\n'
            f'        <dt>File</dt><dd>{escape(asset["file"], quote=True)}</dd>\n'
            f'        <dt>Checksum</dt><dd><code>{escape(asset["sha256"], quote=True)}</code></dd>\n'
            f'        <dt>Review state</dt><dd>{escape(asset["review_state"], quote=True)}</dd>\n'
            f'        <dt>External publish authorized</dt><dd>{escape(str(render_manifest["external_publish_authorized"]).lower(), quote=True)}</dd>\n'
            '      </dl>\n'
            f'      <pre>{escape(metadata_json, quote=True)}</pre>\n'
            '    </article>\n'
        )
    return (
        '<!doctype html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '  <meta charset="utf-8" />\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1" />\n'
        f'  <title>Campaign Review Gallery - {escape(manifest["campaign"]["name"], quote=True)}</title>\n'
        '  <style>\n'
        '    :root { color-scheme: light; font-family: Arial, sans-serif; background: #f7f7f4; color: #222; }\n'
        '    body { margin: 0; }\n'
        '    header { padding: 24px; background: #ffffff; border-bottom: 1px solid #d8d8d2; }\n'
        '    h1 { margin: 0 0 8px; font-size: 24px; }\n'
        '    .controls { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }\n'
        '    .control { border: 1px solid #999; background: #fff7d6; padding: 6px 10px; font-weight: 700; }\n'
        '    main { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; padding: 16px; }\n'
        '    .variant { background: #fff; border: 1px solid #d8d8d2; border-radius: 6px; padding: 12px; }\n'
        '    .variant h2 { margin: 0 0 10px; font-size: 15px; overflow-wrap: anywhere; }\n'
        '    img { display: block; width: 100%; height: auto; border: 1px solid #e5e5df; background: #fff; }\n'
        '    dl { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 6px 10px; font-size: 13px; }\n'
        '    dt { font-weight: 700; }\n'
        '    dd { margin: 0; overflow-wrap: anywhere; }\n'
        '    pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f5f5f0; padding: 10px; font-size: 12px; }\n'
        '  </style>\n'
        '</head>\n'
        '<body>\n'
        '  <header>\n'
        f'    <h1>{escape(manifest["campaign"]["name"], quote=True)}</h1>\n'
        f'    <p>{escape(render_manifest["capability_disclaimer"], quote=True)}</p>\n'
        '    <div class="controls">\n'
        '      <span class="control">review_state: draft</span>\n'
        '      <span class="control">external_publish_authorized: false</span>\n'
        f'      <span class="control">asset_count: {escape(str(render_manifest["asset_count"]), quote=True)}</span>\n'
        '    </div>\n'
        '  </header>\n'
        '  <main>\n'
        + ''.join(cards) +
        '  </main>\n'
        '</body>\n'
        '</html>\n'
    )


def is_complete_render_bundle(
    output_dir: Path,
    render_manifest: dict[str, Any],
    bundle_id: str,
    expected_asset_count: int,
) -> bool:
    """Return whether an existing directory contains the complete deterministic bundle."""
    if render_manifest.get("bundle_sha256") != bundle_id:
        return False
    gallery = render_manifest.get("review_gallery")
    if (
        not isinstance(gallery, dict)
        or gallery.get("file") != CAMPAIGN_GALLERY_FILE
        or not isinstance(gallery.get("sha256"), str)
    ):
        return False
    gallery_path = output_dir / gallery["file"]
    if not gallery_path.is_file() or hashlib.sha256(gallery_path.read_bytes()).hexdigest() != gallery["sha256"]:
        return False
    assets = render_manifest.get("assets")
    if not isinstance(assets, list):
        return False
    if render_manifest.get("asset_count") != expected_asset_count or len(assets) != expected_asset_count:
        return False
    for asset in assets:
        if (
            not isinstance(asset, dict)
            or not isinstance(asset.get("file"), str)
            or Path(asset["file"]).name != asset["file"]
            or Path(asset["file"]).suffix != ".svg"
            or not isinstance(asset.get("sha256"), str)
        ):
            return False
        path = output_dir / asset["file"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            return False
    return True


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
            try:
                existing = json.loads(existing_manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise CampaignRenderError(f"output directory exists but is not a valid render bundle: {output_dir}") from exc
            if is_complete_render_bundle(output_dir, existing, bundle_id, manifest["variant_count"]):
                return existing
        raise CampaignRenderError(f"output directory already exists with different content: {output_dir}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        rendered = []
        rendered_svgs = {}
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
            rendered_svgs[name] = svg
        render_manifest = {
            **bundle_basis,
            "bundle_sha256": bundle_id,
            "asset_count": len(rendered),
            "assets": rendered,
            "external_publish_authorized": False,
            "capability_disclaimer": "Rendered SVG files are internal draft creatives, not published assets or evidence of visual quality.",
        }
        gallery = build_review_gallery(manifest, render_manifest, rendered_svgs)
        gallery_path = staging / CAMPAIGN_GALLERY_FILE
        gallery_path.write_text(gallery, encoding="utf-8")
        with gallery_path.open("rb") as handle:
            os.fsync(handle.fileno())
        render_manifest["review_gallery"] = {
            "file": CAMPAIGN_GALLERY_FILE,
            "sha256": hashlib.sha256(gallery.encode("utf-8")).hexdigest(),
        }
        write_json(staging / "render-manifest.json", render_manifest)
        os.replace(staging, output_dir)
        staging = None
        return render_manifest
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
