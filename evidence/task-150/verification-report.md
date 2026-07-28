# Task 150 Verification Report

## Outcome

Task 150 closes three High assurance gaps in the explicitly bound Phase C pilot.
Claimed pilot completion is now protected against direct SQLite status writes and is
revalidated by `CompanyOS.validate()`. Artifact status transitions now have an
immutable, keyed lifecycle chain, so an approved artifact cannot be made current
again after an authorized supersession. The Dashboard snapshot and `/api/status`
now use explicit projections for tasks, CEO runs, and Chairman directives so raw
task results, source-message identity, and CEO runtime JSON are not exposed.

Authorized completion remains one transaction. The execution is marked completed,
the assurance completion binding is recorded, and only then may the task trigger
accept `status='done'`; any later failure rolls all three writes back. Authorized
register, approve, supersede, and dependent-stale transitions append signed lifecycle
records before changing the artifact row. Dropping the status guard does not hide a
rollback because lifecycle verification compares the signed chain's terminal state
with the artifact's current status.

## Delivery Basis

- Repository: `agent-company`
- Branch: `main`
- Base commit: `820eb5fe21c5cf5d94c3f936f9c30894038aff66`
- Base tree: `fe9ab380d4daf84f3396b664eb13a0c60ddd3fbc`
- Delivery commit: the Git commit containing this report
- Authorized external action: required `git push` only

## Diff Audit

Every uncommitted diff was reviewed before verification:

- `agent_company/assurance.py` records and validates signed artifact lifecycle chains
  during registration, approval, supersession, dependent invalidation, integrity
  checks, and bound-pilot completion checks.
- `agent_company/db.py` adds the lifecycle table, safely backfills only independently
  signed legacy registrations/approvals and audited terminal states, and installs
  immutable lifecycle plus anchored status-transition triggers.
- `agent_company/pilot_gate.py` installs the direct task-completion guard and rejects
  build artifacts whose lifecycle or integrity no longer matches.
- `agent_company/ops.py` orders authorized completion writes for the trigger and adds
  read-only consistency validation across task, execution, assurance binding, result,
  and timestamps.
- `agent_company/dashboard.py` replaces raw task, CEO-run, and Chairman-directive
  reads with explicit public field projections.
- `tests/test_completion_assurance_gate.py` adds direct completion, validation,
  supersession rollback, dropped-guard, and authorized transition regressions; two
  former raw stale writes now use the authorized supersession lifecycle.
- `tests/test_dashboard.py` injects six private markers and verifies full snapshot and
  API serialization rather than checking only selected subtrees.

`git diff --check` is clean. The audit found no unrelated source or test changes.

## Strict TDD Evidence

The retained task-150 RED run executed the five new regressions against the committed
base behavior:

```text
python3.11 -m unittest <five named task-150 completion, lifecycle, and redaction tests> -v
```

Result: 5 tests ran with 5 expected failures. The failures show that direct SQL could
mark a claimed pilot done, validation did not report the forged state, a superseded
artifact could be changed back to approved, dropped-trigger rollback was not detected,
and the whole Dashboard/API payload exposed private runtime markers. Full output is
in `red-test-output.txt`.

## Exact High Probes

All three requested High groups were rerun exactly by named test:

1. Direct DB completion, validation detection, and legitimate completion:

```text
python3.11 -m unittest \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_direct_sql_cannot_complete_a_claimed_bound_pilot \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_forged_bound_pilot_completion_state \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_exact_eval_and_independent_review_allow_atomic_completion -v
```

Result: 3 tests passed.

2. Supersede/raw status rollback and authorized lifecycle:

```text
python3.11 -m unittest \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_superseded_artifact_status_cannot_be_rolled_back_with_raw_sql \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_dropped_status_guard_cannot_hide_supersession_rollback \
  tests.test_assurance_kernel.AssuranceKernelTest.test_supersede_marks_dependent_artifacts_stale_in_shadow_mode -v
```

Result: 3 tests passed.

3. Full Dashboard/API private-marker redaction:

```text
python3.11 -m unittest \
  tests.test_dashboard.DashboardTest.test_complete_snapshot_and_api_redact_raw_runtime_fields \
  tests.test_dashboard.DashboardTest.test_snapshot_uses_live_sources_and_no_fabricated_operations_metrics -v
```

Result: 2 tests passed. Combined output for all 8 probes is in
`adversarial-probe-output.txt`.

## Final Verification

Focused completion, lifecycle, migration, execution, and dashboard verification:

```text
python3.11 -m unittest tests.test_completion_assurance_gate \
  tests.test_assurance_kernel tests.test_pilot_gate \
  tests.test_task_execution_continuity tests.test_dashboard -v
```

Result: 110 tests passed in 11.598 seconds. Full output is in
`focused-test-output.txt`.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -v
```

Result: 203 tests passed in 15.742 seconds. Full output is in
`full-test-output.txt`.

PixWeave canonical suite:

```text
git status --short --branch
git rev-parse HEAD
python3.11 -m unittest discover -s tests -v
```

Result: PixWeave remained clean on `main` at
`d78094f26eb697c810899a40771a8af6dec7ce19`; 58 tests passed in 0.314
seconds. Full output is in `pixweave-test-output.txt`.

Clean-copy migration/noninterference used SQLite's online backup API through a
read-only source connection and initialized only independent copies. Fresh
initialization passed. A signed pre-lifecycle fixture backfilled exactly two anchors
and remained idempotent on a second initialization. The operational snapshot kept
all 148 task rows at digest
`f190cc379118beb8cb4aeb53ad12d13ec2200c403e557d9dd7433914af54c442`;
the immutable source snapshot and live task digest were unchanged; all four task-150
guards were present; validation returned no errors; and `PRAGMA integrity_check`
returned `ok`. The copied operational database retained 18 preexisting assurance
conflicts rather than blessing unsigned legacy records. Full output is in
`clean-copy-migration-output.txt`.

Repository checks:

```text
git diff --check
python3.11 -m compileall -q agent_company tests
python3.11 -m json.tool evidence/task-150/CONTINUITY.json
python3.11 -m json.tool evidence/task-150/evidence-manifest.json
```

Result: all passed before commit.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| Direct completion cannot bypass assurance | A claimed bound pilot rejects raw `tasks.status='done'`; state remains in progress/running/uncompleted. |
| Forged completion remains detectable | `validate()` correlates task, execution, completion binding, embedded assurance result, and exact timestamps even if the guard is dropped. |
| Legitimate completion remains atomic | Trusted Eval plus independent Review Decision completes task, execution, and assurance binding together; existing rollback probes remain green. |
| Supersession cannot be rolled back | Authorized lifecycle entries are signed and immutable; the status trigger blocks raw rollback, and terminal mismatch is detected if that trigger is dropped. |
| Authorized lifecycle remains functional | Registration, approval, direct supersession, and dependent stale invalidation pass and retain signed lineage. |
| Dashboard/API redact private state | Full serialized snapshot and `/api/status` omit task results, Chairman session/message IDs, and raw CEO input/action/result JSON while preserving bounded status fields. |
| Migration is additive and non-interfering | Fresh and signed legacy copies migrate successfully; 148 operational tasks and source snapshots remain unchanged; unsigned legacy conflicts stay fail-closed. |
| Cross-repository regression | 203 Agent Company and 58 unchanged PixWeave tests pass. |

## Limits

This delivery is limited to the authorized task-150 Phase C hardening. It does not
authorize Phase D, global enforcement, PixWeave source changes, customer data,
outreach, publication, pricing, payment, contracts, production release, deployment,
or any external action other than the required Git push. No deployment was
performed. Independent Control & Reliability review remains a separate governance
requirement and is not self-issued here.
