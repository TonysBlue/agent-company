# PixWeave Controlled Beta Validation Protocol

Protocol version: `pixweave-controlled-beta/v1.0`
Status: internal draft for Chairman review
Owner: CEO
Effective scope: local, controlled sessions only

## Governance Boundary

This protocol does not authorize customer outreach, account creation, public release, production deployment, pricing, payment, contracts, or processing real customer data. Those actions remain reserved for Chairman approval. Until approval is recorded, sessions may only use internal participants and non-sensitive test assets with documented provenance.

## Target Participant

The intended beta participant is a small Chinese e-commerce or brand-content team that repeatedly creates product-detail, campaign, or social-channel visuals; has an identifiable visual reviewer; can provide rights-cleared source assets; and can compare PixWeave with its current workflow. Exclude minors, regulated/high-risk imagery, biometric or other sensitive personal data, unclear image rights, and participants unable to give explicit consent.

## Core Scenarios

Each consented session uses at least one and preferably two of these bounded workflows:

1. Generate a channel-ready campaign variant from an approved brand kit and brief.
2. Edit a rights-cleared product image using crop or branded-overlay controls while retaining source lineage.
3. Review generated variants, record approve/reject decisions, and submit structured feedback linked to the artifact.

A facilitator records the scenario, start/end timestamps, result, review decision, defects, token fields when observed, and support/review effort. Unknown values remain `not_collected`; they are never converted to zero.

## Session Procedure

1. Confirm approval exists for the participant and planned data handling.
2. Present the consent text and record protocol version, timestamp, participant pseudonym, consent status, asset-rights attestation, and withdrawal route.
3. Stop immediately if consent or asset rights are absent.
4. Record the participant's intended outcome before operating the product.
5. Run the bounded workflow locally without publishing outputs.
6. Record task outcome and elapsed time from observed timestamps.
7. Ask the participant for a 1-5 satisfaction score and one free-text reason; declining is valid and recorded as `not_collected`.
8. Triage defects and link each issue to the session and artifact identifier.
9. Ask whether retained optional contact details should be deleted; apply the approved retention rule.

## Metric Definitions

- `eligible_session`: consent and rights checks pass, a core scenario is attempted, and required timestamps and outcome are retained.
- `task_success`: the participant reaches the stated outcome and the final artifact is approved by the participant/reviewer without a facilitator performing the substantive task. Values are `success`, `failure`, or `not_evaluable` with reason.
- `task_success_rate`: successful tasks divided by evaluable tasks. `not_evaluable` tasks are reported separately.
- `satisfaction`: participant-reported integer 1-5. Report count, median, distribution, and missing count; never infer missing responses.
- `elapsed_minutes`: observed workflow end minus start. Report median and range by scenario; setup interruption is separately labeled.
- `quality_pass`: all required deterministic checks pass and the reviewer approves the artifact. This is not a claim of independently measured aesthetic quality.
- `critical_defect`: data-rights/privacy breach, unauthorized external exposure, unrecoverable loss of a session record, or output capable of materially misleading a customer.
- `high_defect`: prevents task completion, corrupts source lineage, or produces repeatably unusable output without a safe workaround.

## Phase Success Thresholds

The phase may be recommended to advance only when all are true:

- At least 5 Chairman-approved eligible sessions are completed, including at least 2 distinct participants and at least 2 core scenarios.
- At least 80% task success across at least 5 evaluable tasks.
- Median satisfaction is at least 4/5 with at least 4 collected ratings; missing ratings are disclosed.
- At least 80% of reviewed final artifacts achieve `quality_pass`.
- No open critical defect and no unresolved high defect affecting the proposed workflow.
- Every session has consent evidence, asset provenance, timestamps, linked feedback/defects, and an auditable artifact identifier.
- Token and human review/support cost fields are observed or explicitly marked `not_collected`; no fabricated economics are used.

Meeting these thresholds supports an internal recommendation only. It does not authorize launch, outreach, pricing, payment, or production use.

## Pause And Stop Thresholds

Pause new sessions pending owner review when any of these occurs:

- One critical defect or suspected privacy, consent, image-rights, or unauthorized-exposure event.
- Two high defects in the same workflow within three consecutive sessions.
- Task success falls below 60% after at least 5 evaluable tasks.
- Median satisfaction falls below 3/5 after at least 4 collected ratings.
- Required consent, provenance, session, artifact, or issue-link records cannot be reproduced from retained evidence.
- Backup recovery or access-control verification required by the beta readiness gate fails.

The CEO owns protocol interpretation and response tracking; Product Engineer owns product defects and reliability incidents. Legal/Compliance Specialist reviews privacy and rights questions on demand. The Chairman decides whether external recruitment or real customer sessions may begin and whether a paused beta may resume after a reserved-action incident.

## Evidence Record

Each session record must bind: protocol version, approval reference, participant pseudonym, consent record, asset-rights attestation, scenario, intended outcome, timestamps, task outcome, satisfaction response or missing status, artifact IDs/checksums, quality review, feedback ID, defect IDs, token observations or `not_collected`, human review/support duration or `not_collected`, quality score or `not_collected`, and retention/deletion status. Collected token, duration, review-effort, and quality observations retain their source and `session_id`; token components must reconcile to the recorded total.

Phase reporting must include denominators, missing values, exclusions with reasons, and unresolved defects. Internal demos and synthetic fixtures are product evidence but do not count as real beta sessions or customer satisfaction evidence.

## Change Control

Any metric-definition or threshold change creates a new protocol version. Never rewrite historical session results under a newer definition; instead preserve the original version and provide a reconciled comparison. Protocol approval and subsequent reserved actions must be independently recorded in the company ledger.
