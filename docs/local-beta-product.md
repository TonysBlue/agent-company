# Local Beta Product Interface

`beta-product` is a bounded local HTTP interface for internal product review:

```bash
python3.11 -m agent_company.cli beta-product --host 127.0.0.1 --port 18112
```

Open `http://127.0.0.1:18112/beta` for the internal control page. The service is stdlib-only, binds to localhost by default, does not read or write runtime SQLite task status, and does not call external services.

## JSON Endpoints

- `GET /healthz`: local service health and no-publish controls.
- `GET /api/beta/status`: route and authorization controls.
- `POST /api/beta/render`: accepts a campaign JSON object using the existing `brand-kit/v1`, provenance, and campaign validation path. It writes a deterministic `campaign-render/v2` SVG bundle under `data/artifacts/local-beta/`.
- `POST /api/beta/review`: accepts `{"bundle_path": "...", "decisions": {...}}`, verifies the render bundle through the existing review path, and writes a checksum-bound `campaign-review/v1` record.
- `POST /api/beta/feedback`: accepts `feedback-submission/v1`, rejects sensitive-data declarations and honeypot submissions, and retains only privacy-bounded local feedback records.

All POST routes require `application/json`, limit request bodies to 256 KiB, reject malformed JSON, and return a non-2xx response without writing artifacts when validation fails before the domain action.

## Controls

The interface always displays or returns:

- `internal_only: true`;
- `external_publish_authorized: false`;
- `external_action_authorized: false`;
- `production_deploy_authorized: false`;
- `pricing_authorized: false`;
- `payments_authorized: false`;
- `outreach_authorized: false`.

Render bundles remain internal drafts and include visible `review_state: draft` and `external_publish_authorized: false` controls. Review records set `publication_authorization: none`. Feedback capture does not authorize external contact, publication, production release, pricing, or customer-data export.
