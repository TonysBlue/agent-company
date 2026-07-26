# Bootstrap Charter: Development Assurance Phase C

- ID: `bootstrap-development-assurance-phase-c-2026-07-26`
- Version: `v1`
- Status: `approved`
- Approved by: `Chairman`
- Approval source: `Weixin direct confirmation in originating session`
- Approved at: `2026-07-26`
- Expires at: `2026-08-09T23:59:59+08:00`
- Accountable: `CEO`
- Implementer: `Company Platform Engineer`
- Independent reviewer: `Control & Reliability Reviewer`

## Authorized Scope

Within `agent-company`, implement Phase C Runner and CEO integration:

- compile the exact approved assurance artifacts and complete artifact-set hash into bound pilot task context;
- block completion of bound pilot C2/C3 work until a valid independent trusted evaluation and Review Decision exist;
- enforce stale-artifact, changed-threshold, changed-authority, and execution-generation fencing;
- add redacted Dashboard views for lifecycle, lineage, gate predicates, exceptions, unresolved risks, review independence, and governance effectiveness;
- preserve Phase B controls and continue enforcement only for explicitly bound pilot tasks.

## Explicitly Not Authorized

- Phase D pilots, external evaluators, customer data, outreach, publication, pricing, payment, contracts, or spend;
- PixWeave source changes;
- global C2/C3 enforcement or changes to unbound/C0/C1 behavior;
- production release, public deployment, G6 global enforcement, or irreversible action;
- automatic approval or automatic progression to Phase D/E.

## Hard Invariants

- Existing unbound and non-pilot task dispatch/completion behavior remains unchanged.
- Task context contains only approved artifacts for the bound initiative, exactly matching its artifact-set hash.
- Context never exposes protected holdouts, credentials, private principal identifiers, or raw secret material.
- Completion fails closed after stale artifacts, threshold drift, authority drift, invalid/contaminated evaluation, missing independent review, or execution-generation mismatch.
- Dashboard is read-only and redacted.
- Every code slice follows RED-GREEN-full regression, then commit and push after verification.

## Stop Conditions

Stop and report on non-pilot impact, credential/holdout disclosure, gate bypass, false completion, lifecycle deadlock, integrity conflict, unavailable independent reviewer, service degradation, external action, or authorization expiry.

## Exit Gate

Complete only when all Phase C workstreams pass positive and adversarial tests, Agent Company and PixWeave regressions pass, live-copy migration is non-interfering, deployed services are healthy, Git is clean and pushed, and an independent reviewer reports no Critical/High findings. No Phase D authorization is implied.
