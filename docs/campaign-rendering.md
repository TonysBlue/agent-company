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

Record internal review decisions only after verification:

```bash
python3 -m agent_company campaign-review data/artifacts/campaign-render-v2-95ca21758bde examples/campaign-review-decisions.json
```

The decisions file must use `campaign-review-decisions/v1`, include reviewer metadata, and provide exactly one decision for every variant in the verified bundle. Decisions are limited to `approve` and `reject`; rejected variants require a non-empty `rejection_reason`, and approved variants cannot carry a rejection reason. The generated `campaign-review/v1` artifact binds the review to the render bundle SHA-256, campaign manifest SHA-256, render manifest SHA-256, and each variant SVG checksum. It is written atomically so failed validation, bundle tampering, or interrupted replacement cannot expose a partial review artifact.

The render bundle remains `draft`, records `external_publish_authorized: false`, and is not evidence of visual quality. The review artifact also records `external_publish_authorized: false` and `publication_authorization: none`; external publishing still requires Chairman approval.
