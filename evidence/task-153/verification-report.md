# Task 153 Verification Report

## Outcome

`CompanyOS.validate()` now reconciles the union of signed current pilot claims in
`assurance_claim_bindings` and signed durable claims in
`assurance_pilot_claim_history`, keyed by exact `(task_id, generation)`. Every union
member must have both counterparts, identical signed values, and valid domain-specific
signatures. Missing current claims, missing history, generation/value drift, and either
invalid signature all report the existing fail-closed bound-pilot inconsistency error.

Dropping the current/history delete guards and deleting either side no longer removes
the task-generation identity from validation. In particular, deleting history and then
forging `tasks.status='done'` is detected. Genuine never-bound/nonpilot completion and
a legitimate assurance-gated completed pilot remain valid.

## Delivery Basis

- Repository: `agent-company`
- Branch: `main`
- Clean base commit: `f65138181da8f3fee4b76f733cf255fda8d9967b`
- Clean base tree: `93c9b284dad6d74012d898df29dbe56b9a5192aa`
- Delivery commit: the Git commit containing this report
- Authorized external action: required `git push` only

## Implementation Audit

- `agent_company/ops.py` builds a distinct union of task/generation keys from both
  claim tables, left-joins each signed counterpart, and requires both rows before
  accepting their exact value and signature reconciliation.
- Existing completion reconciliation remains anchored to the signed initiative and
  artifact set, pilot task binding, execution state, completion anchors, and embedded
  result assurance.
- `tests/test_completion_assurance_gate.py` adds exact missing-counterpart, deletion,
  generation/value mismatch, signature, legitimate completed-pilot, and compatibility
  probes. The earlier current-claim deletion probe now explicitly drops both claim
  deletion guards before deleting one side.
- No migration or schema code changed. The backup-only migration/noninterference probe
  confirms the existing migration remains idempotent and operationally noninterfering.

No live initialization, deployment, product-source mutation, or external action other
than the required Git push was performed.

## Strict TDD Evidence

The complete ten-probe reconciliation set was applied to a disposable detached
worktree at clean base `f651381` and executed before the implementation change:

```text
python3.11 -m unittest \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_current_claim_without_history_counterpart \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_history_without_current_claim_counterpart \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_forged_completion_after_history_deletion \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_forged_completion_after_claim_anchor_deletion \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_claim_history_value_mismatch \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_claim_history_generation_mismatch \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_invalid_current_claim_signature \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_reports_invalid_claim_history_signature \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_accepts_legitimate_completed_pilot \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_keeps_never_bound_and_nonpilot_completions_compatible \
  -v
```

Result: 10 probes ran with the two expected failures. A current claim with deleted
history was invisible to validation, including after forged task completion. The
history-driven missing-current direction, mismatch/signature checks, and both genuine
behavior controls already passed. Full clean-base output is retained in
`red-test-output.txt`. The disposable worktree was removed after capture.

The identical set passes after the union reconciliation; output is retained in
`adversarial-probe-output.txt`.

## Final Verification

Focused assurance and operational suite:

```text
python3.11 -m unittest tests.test_assurance_cli tests.test_assurance_kernel \
  tests.test_trusted_evaluator tests.test_pilot_gate \
  tests.test_completion_assurance_gate tests.test_company_os -v
```

Result: 114 tests passed in 12.922 seconds. Full output is in
`focused-test-output.txt`.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -v
```

Result: 220 tests passed in 19.834 seconds. Full output is in
`full-test-output.txt`.

PixWeave canonical suite:

```text
git status --short --branch
git rev-parse HEAD
python3.11 -m unittest discover -s tests -v
```

Result: PixWeave remained clean on `main` at
`d78094f26eb697c810899a40771a8af6dec7ce19`; 58 tests passed in 0.352
seconds. Full output is in `pixweave-test-output.txt`.

Repository checks:

```text
git diff --check
python3.11 -m compileall -q agent_company tests
python3.11 -m json.tool evidence/task-153/CONTINUITY.json
python3.11 -m json.tool evidence/task-153/evidence-manifest.json
```

## Backup-Only Migration Verification

The probe opened the live SQLite source through URI `mode=ro`, enabled `query_only`,
observed `total_changes=0`, and confirmed that a write probe failed with `attempt to
write a readonly database`. SQLite's online backup API created a temporary database;
all initialization and migration writes ran only on that copy with a copied integrity
key.

The first copy migration wrote exactly 6 registrations, 6 approvals, and 12 lifecycle
rows; the second wrote 0/0/0. All 23 operational-table digests remained identical,
including 148 tasks at
`f190cc379118beb8cb4aeb53ad12d13ec2200c403e557d9dd7433914af54c442`.
`CompanyOS.validate()` returned no errors, signed integrity verification returned
`ok`, current/history claim cardinalities matched, and SQLite `integrity_check`
returned `ok`. Full structured output is in `clean-copy-migration-output.txt`.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| Reconcile the union of signed current/history claims | Validation starts from the distinct union of exact task/generation keys and left-joins both counterparts. |
| Fail closed when either counterpart is missing | Independent current-without-history and history-without-current probes return the bound-pilot inconsistency error. |
| Fail closed on values or generation drift | Independently re-signed artifact-set and generation mismatch probes return the inconsistency error. |
| Fail closed on either invalid signature | Current-claim and history signature corruption probes both return the inconsistency error. |
| Detect forged done after deleting either side | Both trigger-drop/deletion directions are covered; the newly exposed history-deletion forgery is RED at base and GREEN after implementation. |
| Preserve genuine behavior | Legitimate gated pilot completion and genuine never-bound/nonpilot completion both validate with no errors. |
| Migration noninterference | Read-only-source backup migration writes 6/6/12 then 0/0/0; all 23 operational table digests remain unchanged. |
| Full regressions | 114 focused, 220 Agent Company, and 58 unchanged PixWeave tests pass. |
| No live mutation/deploy | Live SQLite access was read-only/query-only with zero connection changes; no deploy or live initialization occurred. |

## Limits

This delivery is limited to Task 153's validation reconciliation. It does not authorize
deployment, live initialization, PixWeave changes, customer-data action, outreach,
publication, pricing, payment, contract, or any other production/external action.
Independent Control & Reliability review remains separate and is not self-issued here.
