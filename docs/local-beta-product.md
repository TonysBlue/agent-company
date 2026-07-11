# Local Beta Product Interface

`beta-product` is a bounded local HTTP interface for internal product review:

```bash
python3.11 -m agent_company.cli beta-product --host 127.0.0.1 --port 18112
```

Open `http://127.0.0.1:18112/beta` for the internal control page. The service is stdlib-only, binds to localhost by default, does not read or write runtime SQLite task status, and does not call external services.

## JSON Endpoints

- `GET /healthz`: local service health and no-publish controls.
- `GET /api/beta/status`: route, local render-provider, and authorization controls.
- `POST /api/beta/render`: accepts a campaign JSON object using the existing `brand-kit/v1`, provenance, and campaign validation path. It writes a deterministic `campaign-render/v2` SVG bundle under `data/artifacts/local-beta/` through the dependency-free `local-svg` provider.
- `POST /api/beta/review`: accepts `{"bundle_path": "...", "decisions": {...}}`, rejects bundle paths outside `data/artifacts/local-beta/`, verifies the render bundle through the existing review path, and writes a checksum-bound `campaign-review/v1` record.
- `POST /api/beta/feedback`: accepts `feedback-submission/v1`, rejects sensitive-data declarations and honeypot submissions, and retains only privacy-bounded local feedback records.
- `POST /api/beta/source-edit`: accepts `source-image-edit/v1` with one base64 PNG/JPEG source image, safe non-sensitive provenance metadata, a validated `brand-kit/v1`, and crop or branded-overlay operations. It writes a deterministic `source-image-edit/v1` bundle with retained source bytes, reviewable SVG outputs, a gallery, and source/output SHA-256 lineage.

All POST routes require `application/json`, limit request bodies to 3 MiB, reject malformed JSON, and return a non-2xx response without writing artifacts when validation fails before the domain action.

## Controls

The interface always displays or returns:

- `internal_only: true`;
- `external_publish_authorized: false`;
- `external_action_authorized: false`;
- `production_deploy_authorized: false`;
- `pricing_authorized: false`;
- `payments_authorized: false`;
- `outreach_authorized: false`.

Render bundles and source-edit bundles remain internal drafts and include visible `review_state: draft` and `external_publish_authorized: false` controls. Review records set `publication_authorization: none`. Feedback capture does not authorize external contact, publication, production release, pricing, or customer-data export.

## Local SVG Provider

The `local-svg` provider is stdlib-only and deterministic. It renders each validated PixWeave campaign variant into a genuine SVG image file, not metadata-only output. The render manifest records the provider name/version, `image/svg+xml` media type, stable SHA-256 checksum, and render provenance for every asset. Verification binds each asset's provenance `render_sha256` to the actual SVG bytes and fails closed on provider changes, checksum mismatches, path traversal, malformed manifests, or missing no-publish controls.

Local rendering is bounded to 64 assets per request, 2400 px per dimension, and 4,000,000 pixels per SVG. These bounds are local beta safety limits, not production performance claims.

## Source Image Edit Provider

The `local-source-edit` provider is stdlib-only and deterministic. It validates PNG/JPEG magic bytes, PNG IHDR/IEND or JPEG frame dimensions, maximum source size of 2 MiB, maximum 2400 px per dimension, maximum 4,000,000 source pixels, safe file names, and non-sensitive approved provenance before staging any artifact. PNG/JPEG files with trailing polyglot data, path traversal names, unsupported media types, oversized images, invalid crop bounds, or malformed operations fail closed without retained partial output.

Source edits currently support `crop` and `branded_overlay`. Outputs are SVG review artifacts that embed the uploaded source as a local data URI and record the uploaded source SHA-256, output SHA-256, provider/version, operation details, and derived provenance. These artifacts are internal review evidence only and are not ML-generated edits, visual quality measurements, external publication authorization, or production deployment evidence.
