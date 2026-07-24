# Bootstrap Charter: Development Assurance Phase 0/A

- ID: `bootstrap-development-assurance-2026-07-24`
- Version: `v1`
- Status: `approved`
- Approved by: `Chairman`
- Approval source: `Weixin direct confirmation in originating session`
- Approved at: `2026-07-24`
- Expires at: `2026-08-07T23:59:59+08:00`
- Accountable: `CEO`
- Implementer: `Company Platform Engineer`
- Independent reviewer: `Control & Reliability Reviewer`

## Authorized Scope

Implement Phase 0 shadow mode and Phase A artifact/governance foundations in the `agent-company` repository only:

- strict versioned assurance artifact schemas and validation;
- content-addressed registry, Design Manifest, lineage, hashes, and invalidation analysis;
- C0-C3 risk classification in shadow mode;
- lifecycle and gate-decision records;
- stable principal identity and separation-of-duty checks;
- Change Decision and Incident Record support;
- Git/SQLite/evidence integrity projections;
- bounded CLI and read-only dashboard views;
- tests, migrations, documentation, and internal evidence.

The first future Company OS pilot is the C2 Goal/Design/Eval-to-`approved_for_build` gate. PixWeave Phase 0 work is limited to design of the product-source-image-to-brand-social-asset Goal, Scenario Bank, and Eval; this charter does not authorize PixWeave source changes.

## Explicitly Not Authorized

- blocking existing task dispatch or completion;
- Phase B-E enforcement;
- PixWeave product implementation;
- external research, recruiting, outreach, publication, or paid competitor access;
- production deployment or public release;
- automatic approval or release;
- external, financial, legal, pricing, customer-data, or irreversible action.

## Operating Rules

- Shadow mode records what would pass or block but does not change current task behavior.
- Use strict TDD and bounded vertical slices.
- Independently review material control-plane changes.
- Commit and push each coherent, verified phase.
- Preserve existing repository isolation, Chairman reserved decisions, context fencing, and audit truthfulness.

## Stop Conditions

Stop and report if implementation causes or reveals an authorization bypass, audit loss, inconsistent lifecycle state, unrecoverable migration, store-integrity conflict, hidden task blocking, unsafe external effect, review-independence failure, or materially uncontrolled process cost.

## Exit Gate

Phase 0/A is complete only when the kernel can register and validate this charter/design lineage in shadow mode, migrations are idempotent, unauthorized transitions are rejected, existing tasks remain unblocked, full tests/validation pass, independent review passes, and the Chairman receives an evidence-backed report. Phase B requires a separate approval.
