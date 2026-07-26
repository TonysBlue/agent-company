# Task 147 Verification Report

## Outcome

Task 147 implements the Phase C bound-pilot completion assurance gate in
`agent-company`. Completion for the explicitly approved bound C2/C3 pilot now fails
closed unless the completion transaction can revalidate all of the following:

- the bound G4 build artifact-set hash is current and exact;
- a completed, content-addressed, non-quarantined Trusted Eval is valid;
- every approved Review Decision is affirmative and has no findings;
- every Review Decision binds the exact Trusted Eval result SHA-256 and bound build
  artifact-set SHA-256;
- the reviewer is an active reviewer principal distinct from the implementer, build
  artifact owners, and stable Trusted Evaluator identity;
- artifact bodies, registry metadata, registration audit hashes, approval metadata,
  and approval audit identity remain consistent.

Successful completion records the exact result hash and Review Decision reference in
`assurance_task_bindings` in the same SQLite transaction as the task state, execution
state, audits, and completion event. A denied completion rolls back without changing
the task, execution, audit count, or event count. Unbound and explicitly non-pilot
completion response/result shapes remain unchanged. The dispatch kill switch is not a
completion override.

## Delivery Identity

- Repository: `agent-company`
- Task branch: `task/147`
- Delivery commit: `3ab9f21b1e9da21460ebd0ccd36c8e774cd2b5e9`
- Git tree: `0354c88edbd99b6df33e0c4e068ed8a510c41833`
- Commit subject: `feat: gate bound pilot completion assurance`
- Base before task: `0e279aa37a32a758ce10a015c75338163a4ad1f8`

## Strict TDD Evidence

Tests were written before production implementation.

Initial RED command:

```text
python3.11 -m unittest tests.test_completion_assurance_gate -v
```

Observed initial result: 6 tests ran; 4 failures and 1 error exposed that bound tasks
completed without Trusted Eval/review enforcement and that successful results lacked
an assurance binding. The unbound compatibility control passed.

Review-driven RED probes subsequently reproduced three integrity bypasses before their
fixes:

- rewriting reviewer-owner metadata could fake independence;
- clearing or self-assigning Review Decision approval metadata was accepted;
- rewriting both a Review Decision body and its stored hash could convert reject to
  approve;
- rotating the Trusted Evaluator authority to reviewer could collide evaluator and
  reviewer identities.

Each probe failed before its corresponding implementation hardening and passed after
the change.

## Final Verification

Focused completion gate:

```text
python3.11 -m unittest tests.test_completion_assurance_gate -v
```

Result: 17 tests passed.

Focused assurance/execution integration:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler tests.test_assurance_kernel tests.test_trusted_evaluator -v
```

Result: 81 tests passed in 2.273 seconds.

Canonical full suite from the task contract:

```text
python3.11 -m unittest discover -s tests -v
```

Result: 163 tests passed in 4.464 seconds.

Clean temporary-database validation:

```text
python3.11 -m agent_company.cli init
python3.11 -m agent_company.cli validate
```

Result: `{"errors": [], "ok": true}` using a temporary workspace and the committed
source tree.

Repository checks:

```text
git diff --check
git diff --exit-code HEAD
python3.11 -m compileall -q agent_company tests
```

Result: all passed; the task worktree was clean immediately after commit.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| Reject missing/invalid Trusted Eval | Missing tables/run/evidence, content hashes, lineage, and quarantine are rejected by focused tests. |
| Require affirmative independent Review Decision | Missing, reject/contradictory, findings, implementer/evaluator collision, self-approval, invalid approval, and tamper cases are rejected. |
| Bind exact result and artifact set | Wrong result hash and wrong build artifact-set hash tests fail atomically; success persists both hashes and the review ref. |
| Preserve unbound behavior | Unbound and non-pilot response/result shape tests pass; full regression passes. |
| Atomic completion | Every denial asserts unchanged task result/status, execution state/evidence, audit count, and event count. |
| Upgrade safely | Legacy `assurance_task_bindings` is additively upgraded twice without data loss. |
| Focused and full tests | 81 focused integration tests and 163 canonical tests pass. |

## File Hashes

- `tests/test_completion_assurance_gate.py`: `ceaf42ef883ac631eb75830e034f69dec79cd9ba957ba1e727c4fac19c536a90`
- `agent_company/pilot_gate.py`: `2f5173e75878fc4e14cc2a15ffe97103c1bd359c9f5a232cda59f3b551e4e06a`
- `agent_company/ops.py`: `91953f437ee65c523940e98a1b2d34b8c8537cb7b49ceb5e1ba9dde3c8db49cc`
- `agent_company/assurance.py`: `55d5b421964ab1ed7504395647da481e079998b208ec08c51f29f1d3bdb93819`

## Limits And Next Decision

This delivery implements only task 147's Phase C completion-gate slice. It does not
authorize Phase D, global C2/C3 enforcement, customer data, outreach, publication,
pricing, payment, contracts, production release, or service deployment. Independent
Control & Reliability review remains the next governance action; this implementation
does not self-approve that review.
