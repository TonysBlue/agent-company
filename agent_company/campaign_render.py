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
from .local_image_render import (
    LOCAL_IMAGE_RENDER_MEDIA_TYPE,
    LOCAL_IMAGE_RENDER_PROVIDER,
    LOCAL_IMAGE_RENDER_PROVIDER_VERSION,
    ImageRenderingProvider,
    LocalSvgImageRenderingProvider,
)


CAMPAIGN_RENDER_SCHEMA_VERSION = "campaign-render/v2"
CAMPAIGN_GALLERY_FILE = "review-gallery.html"
CAMPAIGN_RENDER_MANIFEST_FILE = "render-manifest.json"
VARIANT_ID_LENGTH = 16


class CampaignRenderError(ValueError):
    """Raised when a campaign render bundle cannot be produced safely."""


class CampaignRenderVerificationError(ValueError):
    """Raised when a campaign render bundle fails integrity verification."""


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
            "media_type": asset["media_type"],
            "provider": asset["provider"],
            "provenance": variant["provenance"],
            "render_provenance": asset["provenance"],
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
            f'        <dt>Provider</dt><dd>{escape(asset["provider"], quote=True)} {escape(asset["provider_version"], quote=True)}</dd>\n'
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


def render_campaign_bundle(
    data: dict[str, Any],
    output_dir: Path,
    provider: ImageRenderingProvider | None = None,
) -> dict[str, Any]:
    """Render a complete bundle through a staging directory, then rename it atomically."""
    manifest = build_campaign_manifest(data)
    renderer = provider or LocalSvgImageRenderingProvider()
    bundle_basis = {
        "schema_version": CAMPAIGN_RENDER_SCHEMA_VERSION,
        "campaign_manifest_sha256": manifest["manifest_sha256"],
        "provider": renderer.name,
        "provider_version": renderer.version,
    }
    bundle_id = stable_sha256(bundle_basis)
    if output_dir.exists():
        existing_manifest = output_dir / CAMPAIGN_RENDER_MANIFEST_FILE
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
        for asset in renderer.render_campaign_variants(manifest, data["brand_kit"]):
            name = asset.file_name
            path = staging / name
            path.write_text(asset.content, encoding="utf-8")
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
            rendered.append({
                "variant_id": asset.variant_id,
                "file": name,
                "media_type": asset.media_type,
                "width": asset.width,
                "height": asset.height,
                "sha256": asset.sha256,
                "provider": asset.provider,
                "provider_version": asset.provider_version,
                "provenance": asset.provenance,
                "review_state": "draft",
            })
            rendered_svgs[name] = asset.content
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
        write_json(staging / CAMPAIGN_RENDER_MANIFEST_FILE, render_manifest)
        os.replace(staging, output_dir)
        staging = None
        return render_manifest
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def verify_campaign_render_bundle(bundle_dir: Path) -> dict[str, Any]:
    """Verify a campaign-render/v2 bundle without trusting its manifest claims."""
    errors: list[str] = []
    if not bundle_dir.is_dir():
        raise CampaignRenderVerificationError(f"{bundle_dir}: bundle directory does not exist")

    manifest_path = bundle_dir / CAMPAIGN_RENDER_MANIFEST_FILE
    manifest = _load_render_manifest(manifest_path)
    _verify_manifest_shape(manifest, errors)
    if errors:
        raise CampaignRenderVerificationError("; ".join(errors))

    assets = manifest["assets"]
    expected_files = {CAMPAIGN_RENDER_MANIFEST_FILE, manifest["review_gallery"]["file"]}
    expected_files.update(asset["file"] for asset in assets)
    actual_files = {path.name for path in bundle_dir.iterdir() if path.is_file()}
    directories = sorted(path.name for path in bundle_dir.iterdir() if path.is_dir())
    if directories:
        errors.append(f"bundle contains unexpected directories: {', '.join(directories)}")
    missing = sorted(expected_files - actual_files)
    extra = sorted(actual_files - expected_files)
    if missing:
        errors.append(f"bundle is missing expected files: {', '.join(missing)}")
    if extra:
        errors.append(f"bundle contains unexpected files: {', '.join(extra)}")

    gallery = manifest["review_gallery"]
    gallery_path = bundle_dir / gallery["file"]
    if gallery_path.is_file():
        gallery_sha256 = _sha256_file(gallery_path)
        if gallery_sha256 != gallery["sha256"]:
            errors.append(f"{gallery['file']} checksum mismatch")
        gallery_text = gallery_path.read_text(encoding="utf-8", errors="replace")
        if "review_state: draft" not in gallery_text:
            errors.append("review gallery missing draft review control")
        if "external_publish_authorized: false" not in gallery_text:
            errors.append("review gallery missing no-publish control")

    for asset in assets:
        path = bundle_dir / asset["file"]
        if path.is_file() and _sha256_file(path) != asset["sha256"]:
            errors.append(f"{asset['file']} checksum mismatch")

    if errors:
        raise CampaignRenderVerificationError("; ".join(errors))
    return {
        "ok": True,
        "path": str(bundle_dir),
        "schema_version": manifest["schema_version"],
        "bundle_sha256": manifest["bundle_sha256"],
        "asset_count": manifest["asset_count"],
        "review_gallery": gallery["file"],
        "external_publish_authorized": manifest["external_publish_authorized"],
    }


def _load_render_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CampaignRenderVerificationError(f"{path}: missing render manifest") from exc
    except json.JSONDecodeError as exc:
        raise CampaignRenderVerificationError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CampaignRenderVerificationError(f"{path}: top-level JSON value must be an object")
    return data


def _verify_manifest_shape(manifest: dict[str, Any], errors: list[str]) -> None:
    if manifest.get("schema_version") != CAMPAIGN_RENDER_SCHEMA_VERSION:
        errors.append(f"schema_version must be {CAMPAIGN_RENDER_SCHEMA_VERSION}")
    campaign_sha = manifest.get("campaign_manifest_sha256")
    if not _is_sha256(campaign_sha):
        errors.append("campaign_manifest_sha256 must be a SHA-256 hex digest")
    bundle_sha = manifest.get("bundle_sha256")
    if not _is_sha256(bundle_sha):
        errors.append("bundle_sha256 must be a SHA-256 hex digest")
    elif _is_sha256(campaign_sha):
        expected_bundle = stable_sha256({
            "schema_version": CAMPAIGN_RENDER_SCHEMA_VERSION,
            "campaign_manifest_sha256": campaign_sha,
            "provider": manifest.get("provider"),
            "provider_version": manifest.get("provider_version"),
        })
        if bundle_sha != expected_bundle:
            errors.append("bundle_sha256 does not match schema and campaign manifest digest")
    if manifest.get("external_publish_authorized") is not False:
        errors.append("external_publish_authorized must be false")
    if manifest.get("provider") != LOCAL_IMAGE_RENDER_PROVIDER:
        errors.append(f"provider must be {LOCAL_IMAGE_RENDER_PROVIDER}")
    if manifest.get("provider_version") != LOCAL_IMAGE_RENDER_PROVIDER_VERSION:
        errors.append(f"provider_version must be {LOCAL_IMAGE_RENDER_PROVIDER_VERSION}")
    disclaimer = manifest.get("capability_disclaimer")
    if not isinstance(disclaimer, str) or "internal draft" not in disclaimer or "not published" not in disclaimer:
        errors.append("capability_disclaimer must describe internal draft/no-publish scope")

    gallery = manifest.get("review_gallery")
    if not isinstance(gallery, dict):
        errors.append("review_gallery must be an object")
    else:
        _verify_safe_file(gallery.get("file"), CAMPAIGN_GALLERY_FILE, "review_gallery.file", errors)
        if not _is_sha256(gallery.get("sha256")):
            errors.append("review_gallery.sha256 must be a SHA-256 hex digest")

    assets = manifest.get("assets")
    asset_count = manifest.get("asset_count")
    if not isinstance(asset_count, int) or asset_count < 1:
        errors.append("asset_count must be a positive integer")
    if not isinstance(assets, list):
        errors.append("assets must be a list")
        return
    if isinstance(asset_count, int) and asset_count != len(assets):
        errors.append("asset_count must match assets length")

    files: set[str] = set()
    variant_ids: set[str] = set()
    for index, asset in enumerate(assets):
        label = f"assets[{index}]"
        if not isinstance(asset, dict):
            errors.append(f"{label} must be an object")
            continue
        variant_id = asset.get("variant_id")
        if not _is_variant_id(variant_id):
            errors.append(f"{label}.variant_id must be a stable 16-character lowercase hex id")
        elif variant_id in variant_ids:
            errors.append(f"{label}.variant_id duplicates another asset")
        else:
            variant_ids.add(variant_id)
        expected_file = f"{variant_id}.svg" if isinstance(variant_id, str) else None
        _verify_safe_file(asset.get("file"), expected_file, f"{label}.file", errors)
        file_name = asset.get("file")
        if isinstance(file_name, str):
            if file_name in files:
                errors.append(f"{label}.file duplicates another asset")
            files.add(file_name)
        if not _is_sha256(asset.get("sha256")):
            errors.append(f"{label}.sha256 must be a SHA-256 hex digest")
        if asset.get("media_type") != LOCAL_IMAGE_RENDER_MEDIA_TYPE:
            errors.append(f"{label}.media_type must be {LOCAL_IMAGE_RENDER_MEDIA_TYPE}")
        if asset.get("provider") != LOCAL_IMAGE_RENDER_PROVIDER:
            errors.append(f"{label}.provider must be {LOCAL_IMAGE_RENDER_PROVIDER}")
        if asset.get("provider_version") != LOCAL_IMAGE_RENDER_PROVIDER_VERSION:
            errors.append(f"{label}.provider_version must be {LOCAL_IMAGE_RENDER_PROVIDER_VERSION}")
        provenance = asset.get("provenance")
        if not isinstance(provenance, dict):
            errors.append(f"{label}.provenance must be an object")
        else:
            if provenance.get("source_id") != variant_id:
                errors.append(f"{label}.provenance.source_id must match variant_id")
            if provenance.get("origin") != f"pixweave_{LOCAL_IMAGE_RENDER_PROVIDER}_provider":
                errors.append(f"{label}.provenance.origin must identify the local provider")
            if provenance.get("review_decision") != "approved_internal":
                errors.append(f"{label}.provenance.review_decision must be approved_internal")
            if provenance.get("render_sha256") != asset.get("sha256"):
                errors.append(f"{label}.provenance.render_sha256 must match asset sha256")
        if asset.get("review_state") != "draft":
            errors.append(f"{label}.review_state must be draft")
        if not isinstance(asset.get("width"), int) or asset["width"] <= 0:
            errors.append(f"{label}.width must be a positive integer")
        if not isinstance(asset.get("height"), int) or asset["height"] <= 0:
            errors.append(f"{label}.height must be a positive integer")


def _verify_safe_file(value: Any, expected: str | None, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not value:
        errors.append(f"{label} must be a file name")
        return
    path = Path(value)
    if path.name != value or path.is_absolute() or ".." in path.parts:
        errors.append(f"{label} must not contain path traversal")
        return
    if expected is not None and value != expected:
        errors.append(f"{label} must be {expected}")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _is_variant_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == VARIANT_ID_LENGTH
        and all(char in "0123456789abcdef" for char in value)
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
