# Deterministic Campaign Rendering

`campaign-render` converts a validated `campaign-manifest/v1` input into one real SVG image per variant. It is an internal creative-production capability rather than image-generation metadata.

The renderer inherits the campaign provenance gate, uses validated brand colors and dimensions, XML-escapes all text, and records a SHA-256 checksum for each stable variant filename. It stages the full bundle in a temporary directory and atomically renames it, so malformed inputs or interrupted rendering cannot expose a partial destination bundle.

```bash
python3 -m agent_company campaign-render examples/campaign.json
```

The bundle remains `draft`, records `external_publish_authorized: false`, and is not evidence of visual quality. External publishing still requires Chairman approval.
