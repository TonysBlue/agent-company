# Release Decision: <name>

- ID: `<release-id>`
- Version: `v1`
- Candidate: `<commit/release/artifact>`
- Review decision: `<review-id/version>`
- Decision authority: `<CEO | Chairman>`
- Timestamp: `<ISO-8601>`
- Decision: `enable_internal | controlled_beta | production_release | reject | hold | rollback`
- Decision expires at: `<ISO-8601>`
- Conditions expire at: `<ISO-8601 | none>`
- Condition verification: `<condition-verification-id | none>`

## Scope

Define exactly what capability, users, data, environment, duration, and traffic are authorized.

## Gate Summary

Record hard-gate, regression, capability, comparative, reliability, security, cost, and review outcomes.

## Preconditions

List approvals, migrations, backups, observability, support readiness, and evidence required before action. For a conditional decision, list every condition, its evidence source, verifier principal, verification decision ID, and expiry. `controlled_beta` or production enablement is forbidden until an unexpired verification proves all conditions satisfied.

## Rollout Plan

Define stages, monitoring, owners, timing, and progression criteria.

## Stop And Rollback Conditions

Define automatic and human stop triggers, rollback procedure, data recovery, and communication path.

## Residual Risk Acceptance

Record each accepted risk, owner, expiry, mitigation, and accepting authority.

## Outcome Observation Plan

Define real-world measures, observation window, baseline, decision threshold, and follow-up review date.

## Authority Record

Record the exact approved scope, authority, conditions, artifact hash, and audit reference. Approval does not extend beyond this scope.
