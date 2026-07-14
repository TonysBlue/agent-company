# Provenance Gate

PixWeave campaign manifest generation fails closed unless every source asset has a
`provenance/v1` control record and its `review_decision` is `approved_internal`. This is
an internal technical control, not a determination of ownership, consent, privacy
compliance, or permission to publish, process customer data, or deploy to production.

## Required Record

Each `assets[]` entry requires a provenance object containing a stable source ID and
parent lineage; source category and origin; asserted rights basis and a restricted-record
evidence reference; likeness and trademark review statuses; data classification;
retention class; policy flags; reviewer reference; and one of `pending`,
`approved_internal`, or `rejected` as the review decision. The source ID must match the
asset ID. Evidence references identify controlled records and must not embed contracts,
identity documents, personal data, credentials, or payment information.

Missing, malformed, pending, or rejected records prevent manifest creation. Every
derived variant carries a provenance record linked to its source asset and inherits the
source control classifications. `approved_internal` permits only this internal manifest
operation; all reserved actions remain subject to Chairman approval.

## Demo Fixtures

`examples/campaign.json` uses explicitly synthetic, company-created repository fixtures.
Its evidence references point to this section; they are test evidence only and make no
claim about a real source asset or commercial visual output.

## Verification Signal

`provenance_gate_escape_rate = accepted manifests containing a source or derived variant
with missing, invalid, pending, or rejected provenance / accepted manifests`.

Owner: Product Engineer. Cadence: every manifest build and CI regression
run, independently reviewed on demand by Independent Quality Reviewer and escalated to a Legal/Compliance Specialist when required. Required threshold: `0`; any escape is a stop
event and CEO escalation. Data sources: campaign inputs, generated manifests, validation
errors, and CI results.
