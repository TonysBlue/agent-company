"""Bounded local source-image upload and edit workflow for PixWeave beta."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

from .brandkit import HEX_COLOR, stable_sha256, validate_brand_kit, validate_provenance, write_json


SOURCE_IMAGE_EDIT_SCHEMA_VERSION = "source-image-edit/v1"
SOURCE_IMAGE_EDIT_MANIFEST_FILE = "source-edit-manifest.json"
SOURCE_IMAGE_EDIT_GALLERY_FILE = "source-edit-gallery.html"
SOURCE_IMAGE_EDIT_PROVIDER = "local-source-edit"
SOURCE_IMAGE_EDIT_PROVIDER_VERSION = "1.0.0"
MAX_SOURCE_IMAGE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_IMAGE_DIMENSION_PX = 2400
MAX_SOURCE_IMAGE_PIXELS = 4_000_000
ALLOWED_MEDIA_TYPES = {"image/png": ".png", "image/jpeg": ".jpg"}
SAFE_DATA_CLASSIFICATIONS = {
    "synthetic",
    "licensed_stock",
    "internal_public",
    "internal_non_sensitive",
    "commercial_rights_reviewed",
}


class SourceImageEditError(ValueError):
    """Raised when local source-image upload or edit validation fails."""


@dataclass(frozen=True)
class SourceImage:
    file_name: str
    media_type: str
    content: bytes
    width: int
    height: int
    sha256: str
    provenance: dict[str, Any]


@dataclass(frozen=True)
class PlannedEdit:
    operation_id: str
    operation_type: str
    file_name: str
    width: int
    height: int
    svg: str
    operation: dict[str, Any]


def create_source_image_edit_bundle(payload: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Validate an uploaded PNG/JPEG and atomically publish deterministic edit outputs."""
    source, brand_kit, operations = _validate_payload(payload)
    planned = [_plan_operation(source, brand_kit, operation) for operation in operations]
    bundle_basis = {
        "schema_version": SOURCE_IMAGE_EDIT_SCHEMA_VERSION,
        "source_sha256": source.sha256,
        "brand_sha256": stable_sha256(brand_kit),
        "operations_sha256": stable_sha256(operations),
        "provider": SOURCE_IMAGE_EDIT_PROVIDER,
        "provider_version": SOURCE_IMAGE_EDIT_PROVIDER_VERSION,
    }
    bundle_sha256 = stable_sha256(bundle_basis)

    if output_dir.exists():
        existing_path = output_dir / SOURCE_IMAGE_EDIT_MANIFEST_FILE
        if existing_path.is_file():
            try:
                existing = json.loads(existing_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise SourceImageEditError(f"output directory exists but is not a valid source edit bundle: {output_dir}") from exc
            if _is_complete_bundle(output_dir, existing, bundle_sha256, len(planned)):
                return existing
        raise SourceImageEditError(f"output directory already exists with different content: {output_dir}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        source_file = f"source-{source.sha256[:12]}{ALLOWED_MEDIA_TYPES[source.media_type]}"
        source_path = staging / source_file
        source_path.write_bytes(source.content)
        with source_path.open("rb") as handle:
            os.fsync(handle.fileno())

        assets = []
        rendered_svgs: dict[str, str] = {}
        for edit in planned:
            path = staging / edit.file_name
            path.write_text(edit.svg, encoding="utf-8")
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
            output_sha256 = hashlib.sha256(edit.svg.encode("utf-8")).hexdigest()
            assets.append({
                "operation_id": edit.operation_id,
                "operation_type": edit.operation_type,
                "file": edit.file_name,
                "media_type": "image/svg+xml",
                "width": edit.width,
                "height": edit.height,
                "sha256": output_sha256,
                "source_sha256": source.sha256,
                "provider": SOURCE_IMAGE_EDIT_PROVIDER,
                "provider_version": SOURCE_IMAGE_EDIT_PROVIDER_VERSION,
                "review_state": "draft",
                "external_publish_authorized": False,
                "provenance": _edit_provenance(source, edit, output_sha256),
                "operation": edit.operation,
            })
            rendered_svgs[edit.file_name] = edit.svg

        manifest = {
            **bundle_basis,
            "bundle_sha256": bundle_sha256,
            "source": {
                "file": source_file,
                "original_file_name": source.file_name,
                "media_type": source.media_type,
                "width": source.width,
                "height": source.height,
                "bytes": len(source.content),
                "sha256": source.sha256,
                "provenance": source.provenance,
            },
            "asset_count": len(assets),
            "assets": assets,
            "external_publish_authorized": False,
            "external_action_authorized": False,
            "capability_disclaimer": "Local source-image edits are internal draft SVG review artifacts, not published assets or evidence of visual quality.",
        }
        gallery = _build_gallery(manifest, rendered_svgs)
        gallery_path = staging / SOURCE_IMAGE_EDIT_GALLERY_FILE
        gallery_path.write_text(gallery, encoding="utf-8")
        with gallery_path.open("rb") as handle:
            os.fsync(handle.fileno())
        manifest["review_gallery"] = {
            "file": SOURCE_IMAGE_EDIT_GALLERY_FILE,
            "sha256": hashlib.sha256(gallery.encode("utf-8")).hexdigest(),
        }
        write_json(staging / SOURCE_IMAGE_EDIT_MANIFEST_FILE, manifest)
        os.replace(staging, output_dir)
        staging = None
        return manifest
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def _validate_payload(payload: dict[str, Any]) -> tuple[SourceImage, dict[str, Any], list[dict[str, Any]]]:
    if payload.get("schema_version") != SOURCE_IMAGE_EDIT_SCHEMA_VERSION:
        raise SourceImageEditError(f"schema_version must be {SOURCE_IMAGE_EDIT_SCHEMA_VERSION}")
    brand_kit = payload.get("brand_kit")
    if not isinstance(brand_kit, dict):
        raise SourceImageEditError("brand_kit must be an object")
    brand_errors = validate_brand_kit(brand_kit)
    if brand_errors:
        raise SourceImageEditError("; ".join(f"brand_kit.{error}" for error in brand_errors))
    source_input = payload.get("source_image")
    if not isinstance(source_input, dict):
        raise SourceImageEditError("source_image must be an object")
    source = _validate_source_image(source_input)
    operations = payload.get("operations")
    if not isinstance(operations, list) or not operations:
        raise SourceImageEditError("operations must be a non-empty list")
    if len(operations) > 8:
        raise SourceImageEditError("operations must contain at most 8 edits")
    ids = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise SourceImageEditError(f"operations[{index}] must be an object")
        operation_id = operation.get("id")
        if not _safe_id(operation_id):
            raise SourceImageEditError(f"operations[{index}].id must be a safe identifier")
        ids.append(operation_id)
        operation_type = operation.get("type")
        if operation_type == "crop":
            _validate_crop(source, operation, f"operations[{index}]")
        elif operation_type == "branded_overlay":
            _validate_overlay(brand_kit, operation, f"operations[{index}]")
        else:
            raise SourceImageEditError(f"operations[{index}].type must be crop or branded_overlay")
    if len(set(ids)) != len(ids):
        raise SourceImageEditError("operations[].id values must be unique")
    return source, brand_kit, operations


def _validate_source_image(source_input: dict[str, Any]) -> SourceImage:
    file_name = source_input.get("file_name")
    if not isinstance(file_name, str) or Path(file_name).name != file_name or not file_name.strip():
        raise SourceImageEditError("source_image.file_name must be a safe file name")
    media_type = source_input.get("media_type")
    if media_type not in ALLOWED_MEDIA_TYPES:
        raise SourceImageEditError("source_image.media_type must be image/png or image/jpeg")
    allowed_extensions = {ALLOWED_MEDIA_TYPES[media_type]}
    if media_type == "image/jpeg":
        allowed_extensions.add(".jpeg")
    if Path(file_name).suffix.lower() not in allowed_extensions:
        raise SourceImageEditError("source_image.file_name extension must match media_type")
    data_base64 = source_input.get("data_base64")
    if not isinstance(data_base64, str) or not data_base64.strip():
        raise SourceImageEditError("source_image.data_base64 must be a non-empty base64 string")
    try:
        content = base64.b64decode(data_base64, validate=True)
    except binascii.Error as exc:
        raise SourceImageEditError("source_image.data_base64 is not valid base64") from exc
    if not content:
        raise SourceImageEditError("source image must not be empty")
    if len(content) > MAX_SOURCE_IMAGE_BYTES:
        raise SourceImageEditError(f"source image must be at most {MAX_SOURCE_IMAGE_BYTES} bytes")
    width, height = _inspect_image(media_type, content)
    if width > MAX_SOURCE_IMAGE_DIMENSION_PX or height > MAX_SOURCE_IMAGE_DIMENSION_PX:
        raise SourceImageEditError(f"source image dimensions must be at most {MAX_SOURCE_IMAGE_DIMENSION_PX}px")
    if width * height > MAX_SOURCE_IMAGE_PIXELS:
        raise SourceImageEditError(f"source image pixel area must be at most {MAX_SOURCE_IMAGE_PIXELS}")
    provenance = source_input.get("provenance")
    if not isinstance(provenance, dict):
        raise SourceImageEditError("source_image.provenance must be an object")
    provenance_errors = validate_provenance(provenance, "source_image.provenance")
    if provenance_errors:
        raise SourceImageEditError("; ".join(provenance_errors))
    if provenance.get("review_decision") != "approved_internal":
        raise SourceImageEditError("source_image.provenance.review_decision must be approved_internal")
    if provenance.get("data_classification") not in SAFE_DATA_CLASSIFICATIONS:
        raise SourceImageEditError("source_image.provenance.data_classification must be non-sensitive")
    if provenance.get("source_id") != source_input.get("source_id"):
        raise SourceImageEditError("source_image.source_id must match provenance.source_id")
    return SourceImage(
        file_name=file_name,
        media_type=media_type,
        content=content,
        width=width,
        height=height,
        sha256=hashlib.sha256(content).hexdigest(),
        provenance=provenance,
    )


def _inspect_image(media_type: str, content: bytes) -> tuple[int, int]:
    if media_type == "image/png":
        return _inspect_png(content)
    if media_type == "image/jpeg":
        return _inspect_jpeg(content)
    raise AssertionError(media_type)


def _inspect_png(content: bytes) -> tuple[int, int]:
    signature = b"\x89PNG\r\n\x1a\n"
    if not content.startswith(signature):
        raise SourceImageEditError("PNG source image has an invalid signature")
    if len(content) < 33 or content[12:16] != b"IHDR":
        raise SourceImageEditError("PNG source image is missing IHDR")
    width = int.from_bytes(content[16:20], "big")
    height = int.from_bytes(content[20:24], "big")
    if width <= 0 or height <= 0:
        raise SourceImageEditError("PNG dimensions must be positive")
    offset = 8
    saw_iend = False
    while offset + 12 <= len(content):
        length = int.from_bytes(content[offset:offset + 4], "big")
        chunk_type = content[offset + 4:offset + 8]
        next_offset = offset + 12 + length
        if next_offset > len(content):
            raise SourceImageEditError("PNG chunk length exceeds file size")
        if chunk_type == b"IEND":
            saw_iend = True
            trailing = content[next_offset:]
            if trailing.strip(b"\x00\r\n\t "):
                raise SourceImageEditError("PNG source image must not contain trailing polyglot data")
            break
        offset = next_offset
    if not saw_iend:
        raise SourceImageEditError("PNG source image is missing IEND")
    return width, height


def _inspect_jpeg(content: bytes) -> tuple[int, int]:
    if not content.startswith(b"\xff\xd8"):
        raise SourceImageEditError("JPEG source image has an invalid signature")
    if not content.rstrip(b"\x00\r\n\t ").endswith(b"\xff\xd9"):
        raise SourceImageEditError("JPEG source image must end at EOI without trailing polyglot data")
    offset = 2
    while offset + 4 <= len(content):
        if content[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(content) and content[offset] == 0xFF:
            offset += 1
        if offset >= len(content):
            break
        marker = content[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(content):
            break
        segment_length = int.from_bytes(content[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(content):
            raise SourceImageEditError("JPEG segment length exceeds file size")
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if segment_length < 7:
                raise SourceImageEditError("JPEG frame segment is too short")
            height = int.from_bytes(content[offset + 3:offset + 5], "big")
            width = int.from_bytes(content[offset + 5:offset + 7], "big")
            if width <= 0 or height <= 0:
                raise SourceImageEditError("JPEG dimensions must be positive")
            return width, height
        offset += segment_length
    raise SourceImageEditError("JPEG source image is missing a frame header")


def _validate_crop(source: SourceImage, operation: dict[str, Any], label: str) -> None:
    crop = operation.get("crop")
    if not isinstance(crop, dict):
        raise SourceImageEditError(f"{label}.crop must be an object")
    x = crop.get("x")
    y = crop.get("y")
    width = crop.get("width")
    height = crop.get("height")
    if not all(isinstance(value, int) for value in (x, y, width, height)):
        raise SourceImageEditError(f"{label}.crop x/y/width/height must be integers")
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise SourceImageEditError(f"{label}.crop must use positive dimensions within the source")
    if x + width > source.width or y + height > source.height:
        raise SourceImageEditError(f"{label}.crop must stay within the source dimensions")


def _validate_overlay(brand_kit: dict[str, Any], operation: dict[str, Any], label: str) -> None:
    text = operation.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 120:
        raise SourceImageEditError(f"{label}.text must be a non-empty string up to 120 characters")
    placement = operation.get("placement", "bottom")
    if placement not in {"top", "bottom"}:
        raise SourceImageEditError(f"{label}.placement must be top or bottom")
    color = operation.get("color", brand_kit["colors"]["primary"])
    if not isinstance(color, str) or not HEX_COLOR.match(color):
        raise SourceImageEditError(f"{label}.color must be a #RRGGBB color")


def _plan_operation(source: SourceImage, brand_kit: dict[str, Any], operation: dict[str, Any]) -> PlannedEdit:
    operation_id = operation["id"]
    operation_type = operation["type"]
    if operation_type == "crop":
        crop = operation["crop"]
        width = crop["width"]
        height = crop["height"]
        file_name = f"{_file_safe(operation_id)}-crop.svg"
        svg = _build_crop_svg(source, operation_id, crop)
    elif operation_type == "branded_overlay":
        width = source.width
        height = source.height
        file_name = f"{_file_safe(operation_id)}-branded-overlay.svg"
        svg = _build_overlay_svg(source, brand_kit, operation_id, operation)
    else:
        raise AssertionError(operation_type)
    return PlannedEdit(
        operation_id=operation_id,
        operation_type=operation_type,
        file_name=file_name,
        width=width,
        height=height,
        svg=svg,
        operation=dict(operation),
    )


def _build_crop_svg(source: SourceImage, operation_id: str, crop: dict[str, int]) -> str:
    source_data = base64.b64encode(source.content).decode("ascii")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{crop["width"]}" height="{crop["height"]}" '
        f'viewBox="0 0 {crop["width"]} {crop["height"]}" role="img" aria-labelledby="title desc">\n'
        f'  <title id="title">Internal crop edit {escape(operation_id, quote=True)}</title>\n'
        f'  <desc id="desc">DRAFT crop from source sha256 {source.sha256}</desc>\n'
        f'  <image href="data:{source.media_type};base64,{source_data}" x="{-crop["x"]}" y="{-crop["y"]}" width="{source.width}" height="{source.height}"/>\n'
        '  <rect x="0" y="0" width="100%" height="100%" fill="none" stroke="#111827" stroke-width="2"/>\n'
        '</svg>\n'
    )


def _build_overlay_svg(source: SourceImage, brand_kit: dict[str, Any], operation_id: str, operation: dict[str, Any]) -> str:
    source_data = base64.b64encode(source.content).decode("ascii")
    color = operation.get("color", brand_kit["colors"]["primary"]).lower()
    placement = operation.get("placement", "bottom")
    bar_height = max(36, min(source.height // 4, 96))
    bar_y = 0 if placement == "top" else source.height - bar_height
    text_y = bar_y + bar_height * 0.62
    font_size = max(16, min(bar_height // 2, 34))
    brand = escape(brand_kit["brand_name"], quote=True)
    text = escape(operation["text"], quote=True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{source.width}" height="{source.height}" '
        f'viewBox="0 0 {source.width} {source.height}" role="img" aria-labelledby="title desc">\n'
        f'  <title id="title">Internal branded overlay edit {escape(operation_id, quote=True)}</title>\n'
        f'  <desc id="desc">DRAFT overlay from source sha256 {source.sha256}</desc>\n'
        f'  <image href="data:{source.media_type};base64,{source_data}" x="0" y="0" width="{source.width}" height="{source.height}"/>\n'
        f'  <rect x="0" y="{bar_y}" width="{source.width}" height="{bar_height}" fill="{color}" opacity="0.88"/>\n'
        f'  <text x="{max(12, source.width // 24)}" y="{text_y:.2f}" font-family="sans-serif" font-size="{font_size}" font-weight="700" fill="#ffffff">{brand}: {text}</text>\n'
        '</svg>\n'
    )


def _edit_provenance(source: SourceImage, edit: PlannedEdit, output_sha256: str) -> dict[str, Any]:
    policy_flags = list(source.provenance["policy_flags"])
    for flag in ("local_source_image_upload", f"local_edit_{edit.operation_type}"):
        if flag not in policy_flags:
            policy_flags.append(flag)
    return {
        "schema_version": source.provenance["schema_version"],
        "source_id": edit.operation_id,
        "parent_lineage": [source.provenance["source_id"], source.sha256],
        "source_category": f"derived_{edit.operation_type}_draft",
        "origin": "pixweave_local_source_edit",
        "rights_basis": source.provenance["rights_basis"],
        "rights_evidence_ref": source.provenance["rights_evidence_ref"],
        "likeness_status": source.provenance["likeness_status"],
        "trademark_review_status": source.provenance["trademark_review_status"],
        "data_classification": source.provenance["data_classification"],
        "retention_class": source.provenance["retention_class"],
        "policy_flags": policy_flags,
        "reviewer_ref": source.provenance["reviewer_ref"],
        "review_decision": "approved_internal",
        "source_sha256": source.sha256,
        "output_sha256": output_sha256,
    }


def _build_gallery(manifest: dict[str, Any], rendered_svgs: dict[str, str]) -> str:
    cards = []
    for asset in manifest["assets"]:
        svg_src = "data:image/svg+xml;base64," + base64.b64encode(rendered_svgs[asset["file"]].encode("utf-8")).decode("ascii")
        metadata = json.dumps(asset, ensure_ascii=False, indent=2, sort_keys=True)
        cards.append(
            '    <article class="asset">\n'
            f'      <h2>{escape(asset["operation_id"], quote=True)}</h2>\n'
            f'      <img src="{escape(svg_src, quote=True)}" alt="Draft edit {escape(asset["operation_id"], quote=True)}" />\n'
            f'      <p><strong>{escape(asset["operation_type"], quote=True)}</strong> | <code>{escape(asset["sha256"], quote=True)}</code></p>\n'
            f'      <pre>{escape(metadata, quote=True)}</pre>\n'
            '    </article>\n'
        )
    return (
        '<!doctype html>\n'
        '<html lang="en">\n'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<title>Source Edit Review Gallery</title>\n'
        '<style>body{margin:0;font-family:Arial,sans-serif;background:#f7f7f4;color:#222}header{padding:24px;background:#fff;border-bottom:1px solid #d8d8d2}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;padding:16px}.asset{background:#fff;border:1px solid #d8d8d2;border-radius:6px;padding:12px}img{display:block;width:100%;height:auto;border:1px solid #e5e5df}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f5f0;padding:10px;font-size:12px}.control{display:inline-block;border:1px solid #999;background:#fff7d6;padding:6px 10px;font-weight:700;margin-right:8px}</style>\n'
        '</head>\n'
        '<body><header>\n'
        '  <h1>Source image edit review</h1>\n'
        f'  <p>{escape(manifest["capability_disclaimer"], quote=True)}</p>\n'
        '  <span class="control">review_state: draft</span>\n'
        '  <span class="control">external_publish_authorized: false</span>\n'
        f'  <span class="control">source_sha256: {escape(manifest["source"]["sha256"], quote=True)}</span>\n'
        '</header><main>\n'
        + ''.join(cards) +
        '</main></body></html>\n'
    )


def _is_complete_bundle(output_dir: Path, manifest: dict[str, Any], bundle_sha256: str, asset_count: int) -> bool:
    if manifest.get("schema_version") != SOURCE_IMAGE_EDIT_SCHEMA_VERSION:
        return False
    if manifest.get("bundle_sha256") != bundle_sha256:
        return False
    if manifest.get("external_publish_authorized") is not False:
        return False
    if manifest.get("asset_count") != asset_count:
        return False
    source = manifest.get("source")
    if not isinstance(source, dict) or not _safe_file(source.get("file")) or not isinstance(source.get("sha256"), str):
        return False
    source_path = output_dir / source["file"]
    if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != source["sha256"]:
        return False
    gallery = manifest.get("review_gallery")
    if not isinstance(gallery, dict) or gallery.get("file") != SOURCE_IMAGE_EDIT_GALLERY_FILE:
        return False
    gallery_path = output_dir / SOURCE_IMAGE_EDIT_GALLERY_FILE
    if not gallery_path.is_file() or hashlib.sha256(gallery_path.read_bytes()).hexdigest() != gallery.get("sha256"):
        return False
    assets = manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != asset_count:
        return False
    for asset in assets:
        if not isinstance(asset, dict) or not _safe_file(asset.get("file")) or not isinstance(asset.get("sha256"), str):
            return False
        path = output_dir / asset["file"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            return False
    return True


def _safe_id(value: Any) -> bool:
    return isinstance(value, str) and value.strip() and len(value) <= 64 and all(
        char.isalnum() or char in "._:-" for char in value
    )


def _file_safe(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "-" for char in value).strip(".-_") or "edit"


def _safe_file(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).name == value and not Path(value).is_absolute()
