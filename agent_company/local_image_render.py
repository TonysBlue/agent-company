"""Dependency-free local image rendering provider for PixWeave workflows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from html import escape
from typing import Any, Protocol


LOCAL_IMAGE_RENDER_PROVIDER = "local-svg"
LOCAL_IMAGE_RENDER_PROVIDER_VERSION = "1.0.0"
LOCAL_IMAGE_RENDER_MEDIA_TYPE = "image/svg+xml"
MAX_RENDER_ASSETS = 64
MAX_RENDER_DIMENSION_PX = 2400
MAX_RENDER_PIXELS = 4_000_000


class LocalImageRenderError(ValueError):
    """Raised when a local image render request exceeds bounded provider limits."""


@dataclass(frozen=True)
class RenderedImageAsset:
    """One reviewable image asset produced by a rendering provider."""

    variant_id: str
    file_name: str
    media_type: str
    width: int
    height: int
    content: str
    sha256: str
    provider: str
    provider_version: str
    provenance: dict[str, Any]


class ImageRenderingProvider(Protocol):
    """Interface for bounded PixWeave workflow image rendering providers."""

    name: str
    version: str
    media_type: str

    def render_campaign_variants(
        self,
        manifest: dict[str, Any],
        brand_kit: dict[str, Any],
    ) -> list[RenderedImageAsset]:
        """Render a validated campaign manifest into reviewable image assets."""


class LocalSvgImageRenderingProvider:
    """Stdlib-only deterministic SVG provider for internal PixWeave beta workflows."""

    name = LOCAL_IMAGE_RENDER_PROVIDER
    version = LOCAL_IMAGE_RENDER_PROVIDER_VERSION
    media_type = LOCAL_IMAGE_RENDER_MEDIA_TYPE
    max_assets = MAX_RENDER_ASSETS
    max_dimension_px = MAX_RENDER_DIMENSION_PX
    max_pixels = MAX_RENDER_PIXELS

    def render_campaign_variants(
        self,
        manifest: dict[str, Any],
        brand_kit: dict[str, Any],
    ) -> list[RenderedImageAsset]:
        variants = manifest["variants"]
        if len(variants) > self.max_assets:
            raise LocalImageRenderError(f"variant_count must be at most {self.max_assets} for local rendering")

        rendered = []
        for variant in variants:
            self._enforce_bounds(variant)
            svg = self.build_svg(variant, brand_kit, manifest["brand"]["name"])
            digest = hashlib.sha256(svg.encode("utf-8")).hexdigest()
            provenance = self._asset_provenance(variant, digest)
            rendered.append(
                RenderedImageAsset(
                    variant_id=variant["id"],
                    file_name=f"{variant['id']}.svg",
                    media_type=self.media_type,
                    width=variant["format"]["width"],
                    height=variant["format"]["height"],
                    content=svg,
                    sha256=digest,
                    provider=self.name,
                    provider_version=self.version,
                    provenance=provenance,
                )
            )
        return rendered

    def build_svg(self, variant: dict[str, Any], brand_kit: dict[str, Any], brand_name: str) -> str:
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

    def _enforce_bounds(self, variant: dict[str, Any]) -> None:
        width = variant["format"]["width"]
        height = variant["format"]["height"]
        if width > self.max_dimension_px or height > self.max_dimension_px:
            raise LocalImageRenderError(f"{variant['id']}: dimensions must be at most {self.max_dimension_px}px")
        if width * height > self.max_pixels:
            raise LocalImageRenderError(f"{variant['id']}: pixel area must be at most {self.max_pixels}")

    def _asset_provenance(self, variant: dict[str, Any], digest: str) -> dict[str, Any]:
        source = variant["provenance"]
        policy_flags = list(source["policy_flags"])
        if "local_svg_render" not in policy_flags:
            policy_flags.append("local_svg_render")
        return {
            "schema_version": source["schema_version"],
            "source_id": variant["id"],
            "parent_lineage": list(source["parent_lineage"]),
            "source_category": "rendered_svg_draft",
            "origin": f"pixweave_{self.name}_provider",
            "rights_basis": source["rights_basis"],
            "rights_evidence_ref": source["rights_evidence_ref"],
            "likeness_status": source["likeness_status"],
            "trademark_review_status": source["trademark_review_status"],
            "data_classification": source["data_classification"],
            "retention_class": source["retention_class"],
            "policy_flags": policy_flags,
            "reviewer_ref": source["reviewer_ref"],
            "review_decision": "approved_internal",
            "render_sha256": digest,
        }
