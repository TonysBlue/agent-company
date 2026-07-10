# Beta Launch Readiness Gate

`beta-launch-readiness` evaluates a versioned internal package before any beta launch decision. It is stdlib-only, reads only package evidence files, and fails closed on malformed input, missing required gates, missing evidence, checksum mismatch, unsupported reserved-action approval records, or evidence paths that escape the package directory.

The gate covers:

- product capability evidence;
- feedback controls;
- risk review;
- onboarding;
- support ownership;
- observability;
- rollback;
- security/privacy readiness;
- unit-economics evidence;
- reserved-action approvals.

Output is deterministic internal readiness evidence with `launch_authorized: false` and `external_action_authorized: false`. Pending or denied reserved actions produce `blocked_pending_chairman_approvals`; only a package with pinned approved decisions is `ready_for_chairman_review`. Neither status authorizes execution. It is not a production deployment, publication, pricing decision, payment action, customer outreach, or legal authorization. Any external, irreversible, financial, pricing, legal, production, customer-data, or publication action still requires Chairman control according to `docs/constitution.md`.

Example:

```bash
python3.11 -m agent_company.cli beta-launch-readiness examples/beta-launch-package.json
python3.11 -m agent_company.cli beta-launch-readiness examples/beta-launch-package.json --output data/artifacts/beta-launch-readiness.json
```

Package schema:

- `schema_version`: `beta-launch-package/v1`.
- `package_id`: stable internal identifier.
- `product`: product name.
- `beta_version`: semantic version, optionally with prerelease suffix.
- `gates`: object containing every required gate key. Each gate must include `owner`, `status: "pass"`, `summary`, `launch_authorized: false`, and at least one artifact with `path`, lowercase `sha256`, `evidence_type`, and `description`.
- `reserved_action_approvals`: non-empty list of reserved-action decision records. Each record must include `action_type`, `approval_ref`, `decision` (`pending`, `approved`, or `denied`), `decided_by` (`null` while pending, otherwise `"Chairman"`), `launch_authorized: false`, and one pinned evidence artifact.

All evidence paths are relative to the package file directory and may not use absolute paths or parent-directory traversal.
