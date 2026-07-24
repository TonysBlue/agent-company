# Bootstrap Charter: Development Assurance Phase B and Minimal Pilot

- ID: `bootstrap-development-assurance-phase-b-2026-07-24`
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

Within `agent-company`, implement:

- executable Product Competitive and Control-Plane Reliability profile validation;
- a trusted evaluation runner with immutable candidate, dataset, grader, environment, result lineage, holdout custody, contamination response, and candidate-attempt budgets;
- locally bootstrapped, isolated principal credentials with rotation and revocation;
- G0-G5 fail-closed enforcement only for one explicitly marked Company OS C2 pilot initiative;
- the minimum runner binding needed to reject dispatch of that pilot before `approved_for_build`;
- a tested kill switch that disables pilot enforcement without altering assurance evidence;
- PixWeave source-image-to-brand-social-asset Goal, Scenario Bank, Rubric, Eval design, and synthetic fixtures only.

## Pilot

- Initiative: `pilot-c2-approved-for-build`
- Mechanism: C2 Goal/Design/Spec/Eval/Baseline through `approved_for_build`
- Enforcement scope: tasks explicitly bound to this initiative only
- Maximum candidate attempts: 3
- Calendar duration: 14 days
- External spend: 0

## Explicitly Not Authorized

- changing PixWeave source code;
- using customer data or recruiting external evaluators;
- paid competitor access, external research, outreach, publication, or competitive claims;
- production deployment or public release;
- G6 production-release enforcement;
- global C2/C3 enforcement or any effect on unbound tasks;
- automatic approval, financial, legal, pricing, customer-data, or irreversible action;
- automatic progression into Phase C-D-E beyond the minimum pilot runner slice.

## Hard Invariants

- `mode=pilot` may affect only tasks bound to the approved pilot initiative.
- Unbound and non-pilot tasks follow the pre-existing path byte-for-byte.
- Implementers cannot read protected holdouts or execute the trusted evaluator identity.
- Every candidate, dataset, grader, environment, attempt, and result is content-addressed and immutable.
- Failed, abandoned, contradictory, expired, stale, contaminated, or unauthorized evidence never passes a gate.
- No credential or holdout content enters Git, logs, Dashboard, task context, or model prompts.
- Kill-switch activation is audited and fail-safe; it cannot approve or complete work.

## Stop Conditions

Stop and report on any non-pilot task impact, holdout disclosure, credential leakage, identity collision, hash conflict, stale candidate evaluation, gate bypass, unavailable independent reviewer, unrecoverable deadlock, external action, spend, material service degradation, or budget/expiry exhaustion.

## Exit Gate

Complete only when both profiles and the trusted evaluator pass positive and adversarial tests, the pilot runner rejects pre-G4 dispatch while leaving controls unaffected when unbound or killed, credentials and holdouts are isolated, PixWeave design artifacts validate without product changes, all regressions pass, and an independent reviewer reports no Critical/High findings. No later phase is implied.
