"""Auditable internal review decisions for verified campaign renders."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .brandkit import load_json, stable_sha256, write_json
from .campaign_render import CAMPAIGN_RENDER_MANIFEST_FILE, verify_campaign_render_bundle


CAMPAIGN_REVIEW_SCHEMA_VERSION = "campaign-review/v1"
CAMPAIGN_REVIEW_DECISIONS_SCHEMA_VERSION = "campaign-review-decisions/v1"
DECISIONS = {"approve", "reject"}
REVIEWER_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,127}$")


class CampaignReviewError(ValueError):
    """Raised when campaign review decisions cannot be recorded safely."""


def record_campaign_review(bundle_dir: Path, decisions_path: Path, output_path: Path) -> dict[str, Any]:
    """Verify a render bundle and atomically write a complete internal review record."""
    verified = verify_campaign_render_bundle(bundle_dir)
    render_manifest = _load_render_manifest(bundle_dir / CAMPAIGN_RENDER_MANIFEST_FILE)
    decisions_input = load_json(decisions_path)
    record = build_campaign_review_record(render_manifest, decisions_input)
    write_json(output_path, record)
    return {
        "path": str(output_path),
        "schema_version": record["schema_version"],
        "review_sha256": record["review_sha256"],
        "bundle_sha256": verified["bundle_sha256"],
        "asset_count": record["asset_count"],
        "approved_count": record["approved_count"],
        "rejected_count": record["rejected_count"],
        "external_publish_authorized": False,
    }


def build_campaign_review_record(render_manifest: dict[str, Any], decisions_input: dict[str, Any]) -> dict[str, Any]:
    """Build a deterministic review artifact from a verified render manifest and decisions."""
    errors = validate_campaign_review_input(render_manifest, decisions_input)
    if errors:
        raise CampaignReviewError("; ".join(errors))

    decisions_by_variant = {item["variant_id"]: item for item in decisions_input["decisions"]}
    assets = sorted(render_manifest["assets"], key=lambda item: item["variant_id"])
    decisions = []
    approved_count = 0
    rejected_count = 0
    for asset in assets:
        item = decisions_by_variant[asset["variant_id"]]
        decision = item["decision"]
        if decision == "approve":
            approved_count += 1
        else:
            rejected_count += 1
        decisions.append({
            "variant_id": asset["variant_id"],
            "file": asset["file"],
            "svg_sha256": asset["sha256"],
            "decision": decision,
            "rejection_reason": item.get("rejection_reason", ""),
        })

    record = {
        "schema_version": CAMPAIGN_REVIEW_SCHEMA_VERSION,
        "source_schema_version": render_manifest["schema_version"],
        "decision_input_schema_version": decisions_input["schema_version"],
        "campaign_manifest_sha256": render_manifest["campaign_manifest_sha256"],
        "bundle_sha256": render_manifest["bundle_sha256"],
        "render_manifest_sha256": hashlib.sha256(
            json.dumps(render_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "asset_count": len(assets),
        "approved_count": approved_count,
        "rejected_count": rejected_count,
        "reviewer": {
            "reviewer_ref": decisions_input["reviewer"]["reviewer_ref"].strip(),
            "role": decisions_input["reviewer"]["role"].strip(),
            "reviewed_at": decisions_input["reviewer"]["reviewed_at"].strip(),
        },
        "decisions": decisions,
        "external_publish_authorized": False,
        "publication_authorization": "none",
        "capability_disclaimer": (
            "Campaign review decisions are internal creative review records only; "
            "they do not authorize external publication."
        ),
    }
    record["review_sha256"] = stable_sha256(record)
    return record


def validate_campaign_review_input(render_manifest: dict[str, Any], decisions_input: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if decisions_input.get("schema_version") != CAMPAIGN_REVIEW_DECISIONS_SCHEMA_VERSION:
        errors.append(f"schema_version must be {CAMPAIGN_REVIEW_DECISIONS_SCHEMA_VERSION}")

    reviewer = decisions_input.get("reviewer")
    if not isinstance(reviewer, dict):
        errors.append("reviewer must be an object")
    else:
        reviewer_ref = _require_string(reviewer, "reviewer_ref", errors, "reviewer.reviewer_ref")
        if reviewer_ref and not REVIEWER_REF.match(reviewer_ref):
            errors.append("reviewer.reviewer_ref contains unsupported characters")
        _require_string(reviewer, "role", errors, "reviewer.role")
        reviewed_at = _require_string(reviewer, "reviewed_at", errors, "reviewer.reviewed_at")
        if reviewed_at and not _is_utc_timestamp(reviewed_at):
            errors.append("reviewer.reviewed_at must be an ISO-8601 UTC timestamp like 2026-07-11T00:00:00Z")

    assets = render_manifest.get("assets")
    if not isinstance(assets, list) or not assets:
        errors.append("render manifest assets must be a non-empty list")
        expected_variants: set[str] = set()
    else:
        expected_variants = {
            asset["variant_id"]
            for asset in assets
            if isinstance(asset, dict) and isinstance(asset.get("variant_id"), str)
        }

    decisions = decisions_input.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        errors.append("decisions must be a non-empty list")
        return errors

    seen: set[str] = set()
    for index, item in enumerate(decisions):
        label = f"decisions[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object")
            continue
        variant_id = _require_string(item, "variant_id", errors, f"{label}.variant_id")
        if variant_id:
            if variant_id in seen:
                errors.append(f"{label}.variant_id duplicates another decision")
            seen.add(variant_id)
            if variant_id not in expected_variants:
                errors.append(f"{label}.variant_id is not present in the verified render bundle")
        decision = item.get("decision")
        if decision not in DECISIONS:
            errors.append(f"{label}.decision must be one of approve, reject")
            continue
        reason = item.get("rejection_reason", "")
        if decision == "reject":
            if not isinstance(reason, str) or not reason.strip():
                errors.append(f"{label}.rejection_reason must be a non-empty string when decision is reject")
        elif "rejection_reason" in item and reason not in ("", None):
            errors.append(f"{label}.rejection_reason must be empty when decision is approve")

    missing = sorted(expected_variants - seen)
    extra = sorted(seen - expected_variants)
    if missing:
        errors.append(f"decisions missing variants: {', '.join(missing)}")
    if extra:
        errors.append(f"decisions include unknown variants: {', '.join(extra)}")
    return errors


def _load_render_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CampaignReviewError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CampaignReviewError(f"{path}: top-level JSON value must be an object")
    return data


def _require_string(data: dict[str, Any], key: str, errors: list[str], label: str) -> str | None:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be a non-empty string")
        return None
    return value


def _is_utc_timestamp(value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True
