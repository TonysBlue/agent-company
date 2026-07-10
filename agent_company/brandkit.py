"""Brand-kit validation and batch campaign manifest generation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


BRAND_KIT_SCHEMA_VERSION = "brand-kit/v1"
CAMPAIGN_MANIFEST_SCHEMA_VERSION = "campaign-manifest/v1"
PROVENANCE_SCHEMA_VERSION = "provenance/v1"
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
PROVENANCE_DECISIONS = {"pending", "approved_internal", "rejected"}


class BrandKitError(ValueError):
    """Raised when brand-kit or campaign input validation fails."""


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BrandKitError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BrandKitError(f"{path}: top-level JSON value must be an object")
    return data


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def stable_sha256(data: Any) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def validate_brand_kit(brand_kit: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if brand_kit.get("schema_version") != BRAND_KIT_SCHEMA_VERSION:
        errors.append(f"schema_version must be {BRAND_KIT_SCHEMA_VERSION}")
    _require_string(brand_kit, "brand_name", errors)
    version = _require_string(brand_kit, "brand_version", errors)
    if version and not VERSION.match(version):
        errors.append("brand_version must use MAJOR.MINOR.PATCH")

    colors = brand_kit.get("colors")
    if not isinstance(colors, dict):
        errors.append("colors must be an object")
    else:
        primary = _require_string(colors, "primary", errors, "colors.primary")
        if primary and not HEX_COLOR.match(primary):
            errors.append("colors.primary must be a #RRGGBB color")
        secondary = colors.get("secondary")
        if not isinstance(secondary, list) or not secondary:
            errors.append("colors.secondary must be a non-empty list")
        else:
            _validate_color_list(secondary, "colors.secondary", errors)
        neutrals = colors.get("neutrals", [])
        if neutrals:
            if not isinstance(neutrals, list):
                errors.append("colors.neutrals must be a list when present")
            else:
                _validate_color_list(neutrals, "colors.neutrals", errors)

    typography = brand_kit.get("typography")
    if not isinstance(typography, dict):
        errors.append("typography must be an object")
    else:
        _require_string(typography, "heading", errors, "typography.heading")
        _require_string(typography, "body", errors, "typography.body")

    logo = brand_kit.get("logo")
    if not isinstance(logo, dict):
        errors.append("logo must be an object")
    else:
        clearspace = logo.get("clearspace_px")
        if not isinstance(clearspace, int) or clearspace < 0:
            errors.append("logo.clearspace_px must be a non-negative integer")
        allowed = logo.get("allowed_placements")
        if not isinstance(allowed, list) or not allowed or not all(isinstance(item, str) and item.strip() for item in allowed):
            errors.append("logo.allowed_placements must be a non-empty string list")

    forbidden = brand_kit.get("forbidden_elements")
    if not isinstance(forbidden, list) or not all(isinstance(item, str) and item.strip() for item in forbidden):
        errors.append("forbidden_elements must be a string list")
    return errors


def validate_campaign_input(data: dict[str, Any]) -> list[str]:
    errors = []
    brand_kit = data.get("brand_kit")
    if not isinstance(brand_kit, dict):
        errors.append("brand_kit must be an object")
    else:
        errors.extend(f"brand_kit.{error}" for error in validate_brand_kit(brand_kit))

    campaign = data.get("campaign")
    if not isinstance(campaign, dict):
        errors.append("campaign must be an object")
    else:
        _require_string(campaign, "name", errors, "campaign.name")
        _require_string(campaign, "objective", errors, "campaign.objective")
        channels = campaign.get("channels")
        if not isinstance(channels, list) or not channels:
            errors.append("campaign.channels must be a non-empty list")
        elif not all(isinstance(item, str) and item.strip() for item in channels):
            errors.append("campaign.channels must contain only strings")
        else:
            _reject_duplicate_values(channels, "campaign.channels", errors)

    assets = data.get("assets")
    if not isinstance(assets, list) or not assets:
        errors.append("assets must be a non-empty list")
    elif not all(isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip() for item in assets):
        errors.append("assets must contain objects with string id")
    else:
        _reject_duplicate_values([item["id"] for item in assets], "assets[].id", errors)
        for index, asset in enumerate(assets):
            provenance = asset.get("provenance")
            label = f"assets[{index}].provenance"
            if not isinstance(provenance, dict):
                errors.append(f"{label} must be an object")
                continue
            errors.extend(validate_provenance(provenance, label))
            if provenance.get("source_id") != asset["id"]:
                errors.append(f"{label}.source_id must match assets[{index}].id")
            if provenance.get("review_decision") != "approved_internal":
                errors.append(f"{label}.review_decision must be approved_internal before manifest generation")

    copy_variants = data.get("copy_variants")
    if not isinstance(copy_variants, list) or not copy_variants:
        errors.append("copy_variants must be a non-empty list")
    elif not all(isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("headline"), str) for item in copy_variants):
        errors.append("copy_variants must contain objects with id and headline strings")
    else:
        _reject_duplicate_values([item["id"] for item in copy_variants], "copy_variants[].id", errors)

    formats = data.get("formats")
    if not isinstance(formats, list) or not formats:
        errors.append("formats must be a non-empty list")
    else:
        for index, item in enumerate(formats):
            if not isinstance(item, dict):
                errors.append(f"formats[{index}] must be an object")
                continue
            if not isinstance(item.get("id"), str) or not item["id"].strip():
                errors.append(f"formats[{index}].id must be a non-empty string")
            width = item.get("width")
            height = item.get("height")
            if not isinstance(width, int) or width <= 0:
                errors.append(f"formats[{index}].width must be a positive integer")
            if not isinstance(height, int) or height <= 0:
                errors.append(f"formats[{index}].height must be a positive integer")
        valid_ids = [
            item["id"]
            for item in formats
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
        ]
        if len(valid_ids) == len(formats):
            _reject_duplicate_values(valid_ids, "formats[].id", errors)
    return errors


def validate_provenance(provenance: dict[str, Any], label: str = "provenance") -> list[str]:
    """Validate the non-sensitive provenance control record for one source."""
    errors: list[str] = []
    if provenance.get("schema_version") != PROVENANCE_SCHEMA_VERSION:
        errors.append(f"{label}.schema_version must be {PROVENANCE_SCHEMA_VERSION}")
    source_id = _require_string(provenance, "source_id", errors, f"{label}.source_id")
    if source_id and not SOURCE_ID.match(source_id):
        errors.append(f"{label}.source_id contains unsupported characters")
    for field in (
        "source_category", "origin", "rights_basis", "rights_evidence_ref",
        "likeness_status", "trademark_review_status", "data_classification",
        "retention_class", "reviewer_ref",
    ):
        _require_string(provenance, field, errors, f"{label}.{field}")
    lineage = provenance.get("parent_lineage")
    if not isinstance(lineage, list) or not all(
        isinstance(item, str) and SOURCE_ID.match(item) for item in lineage
    ):
        errors.append(f"{label}.parent_lineage must be a list of valid source identifiers")
    flags = provenance.get("policy_flags")
    if not isinstance(flags, list) or not all(isinstance(item, str) and item.strip() for item in flags):
        errors.append(f"{label}.policy_flags must be a string list")
    decision = provenance.get("review_decision")
    if decision not in PROVENANCE_DECISIONS:
        errors.append(f"{label}.review_decision must be one of {', '.join(sorted(PROVENANCE_DECISIONS))}")
    return errors


def build_campaign_manifest(data: dict[str, Any]) -> dict[str, Any]:
    errors = validate_campaign_input(data)
    if errors:
        raise BrandKitError("; ".join(errors))

    brand_kit = data["brand_kit"]
    campaign = data["campaign"]
    assets = sorted(data["assets"], key=lambda item: item["id"])
    copy_variants = sorted(data["copy_variants"], key=lambda item: item["id"])
    formats = sorted(data["formats"], key=lambda item: item["id"])
    channels = sorted(campaign["channels"])
    brand_fingerprint = stable_sha256(brand_kit)
    variants = []
    for channel in channels:
        for fmt in formats:
            for asset in assets:
                for copy in copy_variants:
                    basis = {
                        "asset_id": asset["id"],
                        "brand_fingerprint": brand_fingerprint,
                        "campaign": campaign["name"],
                        "channel": channel,
                        "copy_id": copy["id"],
                        "format_id": fmt["id"],
                    }
                    variant_id = hashlib.sha256(canonical_json(basis).encode("utf-8")).hexdigest()[:16]
                    variants.append(
                        {
                            "id": variant_id,
                            "asset_id": asset["id"],
                            "channel": channel,
                            "copy_id": copy["id"],
                            "format": {"id": fmt["id"], "width": fmt["width"], "height": fmt["height"]},
                            "headline": copy["headline"],
                            "review_state": "draft",
                            "provenance": {
                                "schema_version": PROVENANCE_SCHEMA_VERSION,
                                "source_id": variant_id,
                                "parent_lineage": [asset["id"]],
                                "source_category": "derived_campaign_variant",
                                "origin": "pixweave_campaign_manifest",
                                "rights_basis": asset["provenance"]["rights_basis"],
                                "rights_evidence_ref": asset["provenance"]["rights_evidence_ref"],
                                "likeness_status": asset["provenance"]["likeness_status"],
                                "trademark_review_status": asset["provenance"]["trademark_review_status"],
                                "data_classification": asset["provenance"]["data_classification"],
                                "retention_class": asset["provenance"]["retention_class"],
                                "policy_flags": list(asset["provenance"]["policy_flags"]),
                                "reviewer_ref": asset["provenance"]["reviewer_ref"],
                                "review_decision": "approved_internal",
                            },
                            "brand_controls": {
                                "brand_version": brand_kit["brand_version"],
                                "primary_color": brand_kit["colors"]["primary"].lower(),
                                "forbidden_elements": list(brand_kit["forbidden_elements"]),
                            },
                        }
                    )
    manifest = {
        "schema_version": CAMPAIGN_MANIFEST_SCHEMA_VERSION,
        "campaign": {
            "name": campaign["name"],
            "objective": campaign["objective"],
            "channels": channels,
        },
        "brand": {
            "name": brand_kit["brand_name"],
            "version": brand_kit["brand_version"],
            "fingerprint_sha256": brand_fingerprint,
        },
        "variant_count": len(variants),
        "variants": variants,
    }
    manifest["manifest_sha256"] = stable_sha256(manifest)
    return manifest


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomically replace a JSON artifact without exposing partial content."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _require_string(data: dict[str, Any], key: str, errors: list[str], label: str | None = None) -> str | None:
    value = data.get(key)
    name = label or key
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{name} must be a non-empty string")
        return None
    return value


def _validate_color_list(values: list[Any], label: str, errors: list[str]) -> None:
    for index, value in enumerate(values):
        if not isinstance(value, str) or not HEX_COLOR.match(value):
            errors.append(f"{label}[{index}] must be a #RRGGBB color")


def _reject_duplicate_values(values: list[str], label: str, errors: list[str]) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    if duplicates:
        errors.append(f"{label} contains duplicate values: {', '.join(duplicates)}")
