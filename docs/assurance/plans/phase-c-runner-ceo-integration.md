# Development Assurance Phase C Implementation Plan

> **For Hermes:** Implement task-by-task with strict RED-GREEN TDD and independent review.

**Goal:** Integrate assurance artifacts and gates into task context, execution completion, fencing, and the read-only Dashboard without affecting unbound work.

**Architecture:** Extend the existing additive assurance kernel, pilot binding, context compiler, task-execution transaction, and Dashboard snapshot. A bound pilot execution receives a canonical assurance context projection and records its artifact-set, authority, threshold, and generation bindings. Completion revalidates those bindings and the exact review-bound Trusted Eval result in one transaction.

**Tech Stack:** Python 3.11, SQLite, unittest, systemd user services, existing Bubblewrap runner.

---

## Workstream 1: Assurance Context Compilation

1. RED: bound pilot context must include only approved initiative artifacts, canonical content hashes, artifact-set hash, lifecycle, and current gate predicates.
2. RED: reject incomplete sets, stale artifacts, cross-initiative refs, hash drift, protected evaluator inputs, or credentials.
3. GREEN: extend `agent_company/context_compiler.py` with a read-only assurance projection and bind it to execution generation.
4. Verify focused tests, full tests, non-pilot byte-equivalence; commit and push.

## Workstream 2: Completion Gate

1. RED: bound C2/C3 completion fails without completed non-quarantined Trusted Eval and affirmative independent Review Decision bound to the exact result and artifact set.
2. RED: author/evaluator/reviewer identity collision, negative/contradictory review, expired decision, and missing evidence fail closed.
3. GREEN: add pre-completion assurance decision inside the existing atomic task/execution completion transaction.
4. Verify rollback leaves task, execution, events, and audit unchanged on denial; commit and push.

## Workstream 3: Fencing

1. RED: completion rejects artifact supersession/staleness, Eval threshold/profile drift, principal authority or credential rotation/revocation drift, and execution-generation mismatch.
2. GREEN: persist the minimum immutable execution assurance binding and compare current values at heartbeat/completion.
3. RED/GREEN: service restart and stale-executor probes.
4. Run race/fault tests and full regression; commit and push.

## Workstream 4: Dashboard

1. RED: add read-only redacted summaries for lifecycle, artifact lineage, gate predicates, exceptions, unresolved risks, reviewer independence, and governance effectiveness.
2. RED: prove no credentials, principal IDs, protected holdouts, raw conditions, sensitive artifact bodies, or task-context private content leak.
3. GREEN: extend `agent_company/dashboard.py` snapshot and dedicated assurance page/API.
4. Verify JSON and rendered HTML, health, missing-DB handling; commit and push.

## Workstream 5: Final Verification

1. Run Agent Company and PixWeave full suites.
2. Run live-copy additive migration/non-interference probe.
3. Run direct/CLI completion bypass, stale/threshold/authority/generation races, contaminated Eval, contradictory review, context tamper, and service-restart fault injection.
4. Deploy internal services only; verify `/healthz`, effective systemd hardening, integrity, and redacted Dashboard.
5. Dispatch independent adversarial review and fix every Critical/High finding.
6. Report measured evidence and stop before Phase D.
