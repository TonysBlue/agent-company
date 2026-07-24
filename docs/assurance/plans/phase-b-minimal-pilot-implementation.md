# Development Assurance Phase B Implementation Plan

> **For Hermes:** Implement this plan task-by-task using strict TDD and independent review.

**Goal:** Add executable assurance profiles, a trusted evaluator, isolated credentials, and one bounded C2 pilot dispatch gate without affecting unbound work or PixWeave source.

**Architecture:** Extend the additive assurance store with profile/candidate/dataset/grader/environment/eval-attempt records. A trusted evaluator service resolves protected inputs by capability and emits immutable result manifests. Runner enforcement is opt-in by an explicit task-to-pilot binding and guarded by a local kill switch; existing task behavior remains unchanged otherwise.

**Tech Stack:** Python 3.11, SQLite, unittest, systemd user services, existing Bubblewrap runner.

---

## Workstream 1: Executable Profiles

1. RED: add product-profile tests for dataset partitioning, hard gates, pairwise protocol, attempt limits, comparator version, and statistical decision metadata.
2. GREEN: implement strict Product Profile validation in `agent_company/assurance_profiles.py`.
3. RED: add control-profile tests for state machine, invariants, failure scenarios, evidence semantics, and architecture fitness checks.
4. GREEN: implement strict Control-Plane Profile validation.
5. Run profile tests and full regression; commit and push.

## Workstream 2: Trusted Evaluation

1. RED: test immutable candidate and content-addressed dataset/grader/environment manifests.
2. GREEN: add additive SQLite tables and registry operations.
3. RED: test protected holdout denial to implementers, attempt exhaustion, failed/abandoned result retention, contamination quarantine, stale hashes, and equivalent comparator budgets.
4. GREEN: implement `TrustedEvaluator` with evaluator-only authenticated principal.
5. RED: test evaluator output binds every input hash, seed, status, and evidence reference.
6. GREEN: emit immutable Eval Run records.
7. Run tests, security scan, full regression; commit and push.

## Workstream 3: Credential Isolation

1. RED: test bootstrap requires owner-only local secret and generated credentials are never returned by list/status APIs.
2. RED: test service-to-principal mismatch, revoked/rotated credential, inherited environment leakage, and log redaction.
3. GREEN: implement bootstrap/rotate/revoke CLI and systemd credential files with `0600` ownership.
4. Verify no secrets enter Git, SQLite plaintext, Dashboard, logs, task context, or subprocess environment.
5. Commit and push.

## Workstream 4: Minimal Pilot Runner Gate

1. RED: test an explicitly bound pilot C2 task is not claimed before G4/`approved_for_build`.
2. RED: test unbound tasks and non-pilot initiatives retain the existing runner path.
3. RED: test stale, expired, contradictory, unauthorized, integrity-conflicted, or killed pilot decisions fail safely.
4. GREEN: add task-assurance binding and pre-claim pilot policy check.
5. GREEN: add audited kill switch that bypasses only enforcement and never approves work.
6. Test concurrent claim, binding change, stale artifact, service restart, and evaluator unavailability.
7. Commit and push.

## Workstream 5: PixWeave Eval Design Only

1. Create versioned Goal Contract for source-image-to-brand-social-asset.
2. Create Scenario Bank schema and synthetic fixture set.
3. Create anchored Rubric, hard gates, blinded pairwise protocol, comparator protocol, statistical plan, and human calibration plan.
4. Validate all artifacts with Product Profile.
5. Verify PixWeave source tree and commit remain unchanged.
6. Commit and push Agent Company artifacts only.

## Workstream 6: Final Verification

1. Run targeted tests after every RED/GREEN cycle.
2. Run Agent Company full suite and PixWeave full suite.
3. Run live-copy additive migration and non-pilot byte-for-byte behavior probes.
4. Perform holdout, credential, gate, race, contamination, stale-hash, and kill-switch fault injection.
5. Deploy only internal user services; verify health and read-only redacted Dashboard.
6. Dispatch independent security/correctness review and fix all Critical/High issues.
7. Report measured cycle time, reviewer load, failures detected, false blocks, and residual risks; do not enter later phases.
