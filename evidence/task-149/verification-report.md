# Task 149 Verification Report

## Outcome

Task 149 hardens the explicitly bound Phase C C2/C3 pilot from claim through
completion. A pilot claim now creates a keyed, immutable per-generation binding to
the task's initiative, build artifact set, and fencing token before context
compilation. Context use, heartbeat, checkpoint, and completion validate the claim
and execution bindings inside their existing transactions. The kill switch remains a
dispatch-only rollback and cannot disable runtime or completion assurance.

Artifact registrations, approvals, trusted-evaluation runs, claim bindings, and
execution bindings now carry keyed integrity anchors outside SQLite. Canonical
immutability triggers are repaired on initialization, while validators still fail
closed if an attacker drops a trigger and rewrites a protected row. Completion also
requires the exact trusted-evaluation result and an affirmative, independently
authored and approved Review Decision.

Task context now exposes only artifact references and content hashes. Dashboard
queries omit audit details, fencing tokens, execution session references, and other
private runtime identity fields.

## Completion Blocker Diagnosis

The interrupted full suite reached 197 tests with two errors in
`PilotGate.record_completion`. The new
`assurance_task_bindings_claimed_immutable_update` trigger rejected every update to a
claimed task binding. That correctly blocked pilot demotion and rebinding, but also
blocked the intended update that atomically records `completion_result_sha256`,
`review_decision_ref`, `completed_at`, and `updated_at`.

The canonical trigger now permits exactly one post-claim completion transition when:

- task ID, initiative ID, pilot flag, artifact-set hash, and creation time are
  unchanged;
- all three completion fields were `NULL` and all three become non-`NULL`; and
- `updated_at` is identical to `completed_at`.

Every partial completion, mismatched timestamp, clearing, second completion write,
rebind, demotion, identity change, or deletion still aborts. `record_completion`
retains its `pilot=1 AND completion_result_sha256 IS NULL` compare-and-set predicate
and one-row assertion. It executes in the same `BEGIN IMMEDIATE` transaction as task
and execution completion, so a late failure rolls the entire completion back.

## Delivery Basis

- Repository: `agent-company`
- Branch: `main`
- Base commit before task 149: `75289a18642148bc8e4cd0a1c6f6eed9d5302776`
- Base tree: `4911aa77bfe9b30932accc002896c19f944ca28b`
- Delivery commit: the Git commit containing this report
- Authorized external action: required `git push` only

## Strict TDD Evidence

The original task-149 RED run is retained in `red-test-output.txt`. It overlays the
new tests on committed base `75289a1` and runs:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_context_compiler tests.test_dashboard -v
```

Result: 67 tests ran with 10 failures and 11 errors, exit status 1. The failures prove
the missing claim anchor, fencing-token requirements, lifecycle/expiry enforcement,
keyed integrity anchors, trigger repair, context minimization, and dashboard
redaction before implementation.

After the interrupted worktree exposed the completion-trigger regression, a new
boundary regression was added before the fix and run with the end-to-end completion
test:

```text
python3.11 -m unittest \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_claimed_binding_allows_one_completion_write_without_rebinding \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_exact_eval_and_independent_review_allow_atomic_completion -v
```

Result: both tests errored with `sqlite3.IntegrityError: claimed assurance task
binding is immutable` at `PilotGate.record_completion`. The exact output is retained
in `completion-trigger-red-test-output.txt`.

The repaired regression additionally probes partial completion, mismatched
completion/update timestamps, clearing, a second write, and initiative rebinding.

## Final Verification

Focused assurance, execution, runner, context, credential, and dashboard integration:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate \
  tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler \
  tests.test_assurance_kernel tests.test_trusted_evaluator \
  tests.test_assurance_credentials tests.test_dashboard -v
```

Result: 128 tests passed in 10.640 seconds. Full output is in
`focused-test-output.txt`.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -v
```

Result: 198 tests passed in 13.073 seconds. The interrupted suite had 197 tests; the
final count is 198 because the completion-trigger boundary regression was added.
Full output is in `full-test-output.txt`.

PixWeave canonical suite:

```text
git -C /home/tony/products/pixweave status --short --branch
git -C /home/tony/products/pixweave rev-parse HEAD
python3.11 -m unittest discover -s tests -v
```

Result: PixWeave remained clean on `main` at
`d78094f26eb697c810899a40771a8af6dec7ce19`; 58 tests passed in 0.316
seconds. Full output is in `pixweave-test-output.txt`.

Explicit adversarial subset:

```text
python3.11 -m unittest <12 named completion, immutability, trigger-drop,
integrity-tamper, and rollback probes> -v
```

Result: 12 tests passed in 1.217 seconds. Full names and output are in
`adversarial-probe-output.txt`.

Clean-copy migration used SQLite's online backup API in read-only mode to create a
consistent source snapshot, then initialized only a second copy. Fresh initialization
and validation returned `ok=true`; the copied 148-task operational ledger digest was
unchanged; all 18 canonical triggers were present; `PRAGMA integrity_check` returned
`ok`; and the immutable source snapshot's hash, stat, and task digest were identical
before and after. Full output is in `clean-copy-migration-output.txt`.

Repository checks:

```text
git diff --check
python3.11 -m compileall -q agent_company tests
python3.11 -m json.tool evidence/task-149/CONTINUITY.json
python3.11 -m json.tool evidence/task-149/evidence-manifest.json
```

Result: all passed.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| Atomic completion | End-to-end approved completion and the trigger-boundary regression persist task, execution, result, review reference, and completion binding together. Audit-failure injection rolls all state back. |
| Immutable claimed binding | Claim-time keyed rows bind generation, initiative, artifact set, and fencing token. Demotion, rebinding, deletion, partial completion, timestamp mismatch, clearing, and second completion are rejected. |
| Fail-closed trigger loss | Dropped-trigger rewrites of execution, evaluation, registration, and approval rows fail keyed-integrity validation; initialization restores every canonical trigger without blessing tamper. |
| Runtime fencing | Context, heartbeat, checkpoint, and completion require current generation/token/context, executable lifecycle, unexpired G4, unchanged policy/artifacts, and unchanged relevant principals. |
| Review independence | Evaluator, implementer, self-authored, self-approved, negative, contradictory, quarantined, unanchored, and mismatched-result reviews cannot complete the pilot. |
| Redaction | Task context contains refs and hashes rather than protected artifact bodies. Dashboard snapshots omit audit details, credentials/principals, fencing tokens, and execution session references. |
| Compatibility | Unbound/non-pilot behavior remains unchanged; the kill switch bypasses dispatch only; legacy binding schemas upgrade additively and idempotently. |
| Cross-repository regression | 198 Agent Company and 58 unchanged PixWeave tests pass. |

## Limits

This delivery is limited to the authorized task-149 Phase C controls. It does not
authorize Phase D, global C2/C3 enforcement, PixWeave source changes, customer data,
outreach, publication, pricing, payment, contracts, production release, deployment,
or any external action other than the required Git push. No deployment was
performed. Independent Control & Reliability review remains a separate governance
requirement and is not self-issued here.
