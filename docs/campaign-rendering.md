# Deterministic Campaign Rendering

`campaign-render` converts a validated `campaign-manifest/v1` input into a `campaign-render/v2` bundle with one real SVG image per variant. It is an internal creative-production capability rather than image-generation metadata. Default output directories include the render schema version so earlier immutable bundles remain available across format upgrades.

The renderer inherits the campaign provenance gate, uses validated brand colors and dimensions, XML-escapes all SVG text, and records a SHA-256 checksum for each stable variant filename. It also writes a deterministic, self-contained offline `review-gallery.html` with embedded SVG previews, escaped variant metadata, each SVG checksum, and visible `review_state: draft` plus `external_publish_authorized: false` controls.

The full bundle is staged in a temporary directory and atomically renamed only after every SVG, the gallery, and `render-manifest.json` have been written. Malformed inputs, gallery failures, or interrupted publication cannot expose a partial destination bundle.

```bash
python3 -m agent_company campaign-render examples/campaign.json
```

Verify a retained bundle before review or handoff:

```bash
python3 -m agent_company campaign-render-verify data/artifacts/campaign-render-v2-95ca21758bde
```

`campaign-render-verify` independently checks `render-manifest.json`, `review-gallery.html`, the exact SVG inventory, stable `{variant_id}.svg` filenames, per-file SHA-256 checksums, and the required draft/no-publish controls. It exits non-zero on malformed input, tampering, missing files, extra files, path traversal, checksum mismatches, or any publish authorization flag.

The bundle remains `draft`, records `external_publish_authorized: false`, and is not evidence of visual quality. External publishing still requires Chairman approval.
