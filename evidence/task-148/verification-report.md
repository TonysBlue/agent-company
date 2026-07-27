# Task 148 Verification Report

## Outcome

Task 148 implements Phase C runtime fencing for the explicitly bound C2/C3 pilot.
Context compilation now creates one immutable `assurance_execution_bindings` row for
the active execution generation. The row binds the context bundle, build artifact set,
G4 evaluation policy, and assurance principal registry state using canonical SHA-256
digests.

`ContextCompiler.assert_current`, execution heartbeat, and completion all revalidate
the same binding. They fail closed when any of the following changes after context
compilation:

- a build artifact is stale, superseded, removed, added, or otherwise changes the
  build artifact-set hash;
- the initiative profile/risk class or G4 decision, threshold conditions, artifact
  binding, or expiry changes;
- any assurance principal's actor, authority, credential digest, or active/revoked
  state changes;
- the active execution generation, fencing token, context bundle, or context status
  no longer matches the immutable execution binding.

Denied heartbeats and completions roll back without renewing the lease, updating the
executor, changing task/execution state, or adding audit/events. The binding survives
service-object restart. Missing bindings on already-running bound pilots fail closed
after additive upgrade. Unbound and explicit non-pilot heartbeat/completion behavior
and response shapes remain unchanged.

The task context exposes only the execution generation alongside the existing public
assurance projection. Principal identifiers, credential digests, policy-state digests,
and raw credential material remain outside the task context.

## Delivery Basis

- Repository: `agent-company`
- Branch: `main`
- Base commit before task: `09bfeb80d0bfcdf84aa9d22fb077adaffb3548b0`
- Base tree: `7578465aa1f672db672e09fb0cf76296ea88fb8c`
- Delivery commit: recorded after commit in Git history and `CONTINUITY.json`
- Authorized external action: required `git push` only

## Strict TDD Evidence

Tests were added before production implementation.

Initial RED command:

```text
python3.11 -m unittest tests.test_completion_assurance_gate -v
```

The normalized RED run is stored in `red-test-output.txt`. It ran 36 tests and ended
with seven failures plus one error. Existing completion and compatibility controls
passed, while the new probes demonstrated:

- no immutable execution-binding table existed;
- stale artifacts did not fence context/heartbeat;
- changed G4 threshold conditions or initiative profile did not fence runtime;
- authority changes, credential rotation, and credential revocation did not fence
  runtime;
- generation mismatch lacked the complete cross-boundary execution binding.

Observed RED result: `FAILED (failures=7, errors=1)` with exit status 1. The complete
captured output is retained for review.

## Final Verification

Focused assurance/runtime integration:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler tests.test_assurance_kernel tests.test_trusted_evaluator tests.test_assurance_credentials -v
```

Result: 104 tests passed in 4.008 seconds. Full output is in
`focused-test-output.txt`.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -v
```

Result: 183 tests passed in 6.252 seconds. Full output is in
`full-test-output.txt`.

PixWeave canonical suite, read-only source verification:

```text
git -C /home/tony/products/pixweave status --short --branch
git -C /home/tony/products/pixweave rev-parse HEAD
python3.11 -m unittest discover -s tests -v
```

Result: PixWeave remained clean at
`d78094f26eb697c810899a40771a8af6dec7ce19`; 58 tests passed in 0.371
seconds. Full output is in `pixweave-test-output.txt`.

Clean temporary-workspace validation:

```text
python3.11 -m agent_company.cli init
python3.11 -m agent_company.cli validate
```

Result: `{"errors": [], "ok": true}`. Initializing `PilotGate` in the temporary
database created exactly the eight expected binding columns and both immutable
update/delete triggers. Evidence is in `clean-init-validation.txt`.

Repository checks:

```text
git diff --check
python3.11 -m compileall -q agent_company tests
```

Result: both passed.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| Stale-artifact fencing | Stale build artifact probes reject context, heartbeat, and completion atomically. Existing completion tests cover mismatched and superseded build sets. |
| Changed threshold/profile fencing | G4 conditions and initiative profile drift each reject all three runtime boundaries. |
| Changed authority/credential fencing | Authority mutation, credential rotation, and revocation each reject all three runtime boundaries. |
| Execution-generation fencing | Generation mutation rejects context, heartbeat, and completion; immutable rows are keyed by task and generation. |
| Context binding | Compilation transaction stores context bundle, artifact, policy, principal-state, and generation bindings; task context does not disclose principal or credential state. |
| Heartbeat atomicity | Denial preserves task execution heartbeat/lease/update fields, optional executor state, audit count, and event count. |
| Completion atomicity | Denial preserves task status/result, execution status/evidence, audit count, and event count. |
| Restart and stale executor | Restarted `CompanyOS` validates and completes a current binding; missing binding and stale generation fail closed. |
| Preserve non-pilot behavior | Unbound and non-pilot heartbeat and completion controls pass with unchanged response/result shapes. |
| Additive migration | Legacy task-binding schema is upgraded without data loss; the new execution table and immutable triggers are idempotently created. |
| Full regression | 183 Agent Company tests and 58 clean PixWeave tests pass. |

## File Hashes Before Evidence Finalization

- `agent_company/context_compiler.py`: `5d7665b2e4046a934adb58c76f8883bf28e5f96d373858ccd3b1c9a24f41ba26`
- `agent_company/ops.py`: `74a5c44362d19277149202c7229130d89522e85b80146acdc33ef1bc3268686d`
- `agent_company/pilot_gate.py`: `7a7bd93fed29c63a78577b44ac3c35970bcb2632cd500841ad85c735e7f438f8`
- `tests/test_completion_assurance_gate.py`: `4df378efbc619d05838096bf56ceb2a19c3d383ed70aca0e2f5a28ca420371b8`
- `tests/test_context_compiler.py`: `47830e66680910fe2b9b7adc232b4f09220ab0888d9e8726df5ab546fc75d25a`

## Limits

This delivery implements only task 148's Phase C fencing workstream. It does not
authorize Phase D, global C2/C3 enforcement, PixWeave source changes, customer data,
outreach, publication, pricing, payment, contracts, production release, or service
deployment. No service or live database was mutated. Independent Control & Reliability
review remains a separate Phase C governance requirement and is not self-issued here.
