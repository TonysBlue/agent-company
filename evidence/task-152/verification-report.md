# Task 152 Verification Report

## Outcome

Task 152 closes the two final independent Phase C High findings.

Pilot claims now create a second, independently signed and append-only
`assurance_pilot_claim_history` record in the same transaction as the current claim
binding. Existing valid signed claims are migrated into that history atomically and
idempotently. Task-binding immutability, raw-SQL completion protection, runtime
fencing, and `CompanyOS.validate()` use the durable history instead of trusting only
the deletable current claim row or mutable `pilot` flag. Validation verifies every
history signature, requires the corresponding current claim and pilot binding to
match it exactly, and checks completion state against the signed initiative and
artifact-set identity. Deleting `assurance_claim_bindings` or demoting
`assurance_task_bindings.pilot` therefore remains visible after the other guards are
dropped. Never-bound and nonpilot completions have no pilot-claim history and remain
compatible.

`Store.init_assurance()` is now schema-only: it never synthesizes artifact lifecycle
anchors. The bounded task-151 migration opens one immediate transaction, reconciles
the approved legacy manifest against content bytes/hash, immutable metadata,
approval metadata, exact audit evidence, and principal identities before any write,
and writes candidates only after every artifact passes preflight. A partial signed
registration/approval pair plus tampered approval audit now returns
`integrity_conflict` while leaving the lifecycle count at zero.

## Delivery Basis

- Repository: `agent-company`
- Branch: `main`
- Base commit: `4cb798a0ce2f4e74372261d4e9a605d9c83820ee`
- Base tree: `2c4e551371dfe2930744cb796334c66ff0c6401a`
- Delivery commit: the Git commit containing this report
- Authorized external action: required `git push` only

## Implementation Audit

- `agent_company/pilot_gate.py` creates the signed history table and two immutable
  triggers, writes claim and history atomically, preflights old signed claims before
  idempotent history backfill, and keys task-binding/completion/runtime guards from
  the history record.
- `agent_company/ops.py` requires the durable table and validates all historical
  generations with left joins, so missing claims, missing tasks/bindings, pilot
  demotion, signature conflicts, completion-anchor gaps, and result drift cannot be
  removed from validation by an inner join or `pilot=1` filter.
- `agent_company/db.py` retains additive assurance schema/trigger initialization but
  removes every generic artifact lifecycle backfill path.
- `agent_company/assurance.py` executes legacy reconciliation and writes under one
  `BEGIN IMMEDIATE` transaction. Known partial legacy anchors are content/metadata/
  audit/principal-preflighted before their topology conflict is returned. Fully
  anchored modern artifacts remain outside the bounded legacy migration.
- `tests/test_completion_assurance_gate.py` covers both exact post-guard-drop forgeries,
  signed-history immutability, upgrade/idempotence/noninterference, and compatibility
  for genuine never-bound/nonpilot completions.
- `tests/test_assurance_cli.py` covers the exact partial-signed-anchor plus tampered
  audit conflict and asserts that generic initialization inserts no lifecycle row.

No unrelated source or test change is included. No live initialization, deployment,
or product-source mutation was performed.

## Strict TDD Evidence

The exact three probes were replayed in a disposable detached worktree at the clean
base commit:

```text
python3.11 -m unittest \
  tests.test_task152_red.Task152CompletionRed.test_claim_deletion_cannot_hide_forged_completion_from_validate \
  tests.test_task152_red.Task152CompletionRed.test_pilot_demotion_cannot_hide_forged_completion_from_validate \
  tests.test_task152_red.Task152InitRed.test_partial_signed_anchors_are_not_mutated_before_audit_conflict \
  -v
```

All three failed for the expected reasons: both forged completions produced
`CompanyOS.validate() == []`, and generic initialization inserted two lifecycle
rows before returning the audit conflict. The complete base output is retained in
`red-test-output.txt`. The disposable worktree was removed after capture.

## Final Verification

Focused assurance, trusted evaluation, pilot, completion, and operational suite:

```text
python3.11 -m unittest tests.test_assurance_cli tests.test_assurance_kernel \
  tests.test_trusted_evaluator tests.test_pilot_gate \
  tests.test_completion_assurance_gate tests.test_company_os -v
```

Result: 106 tests passed in 10.406 seconds. Full output is in
`focused-test-output.txt`.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -v
```

Result: 212 tests passed in 16.927 seconds. Full output is in
`full-test-output.txt`.

PixWeave canonical suite:

```text
git status --short --branch
git rev-parse HEAD
python3.11 -m unittest discover -s tests -v
```

Result: PixWeave remained clean on `main` at
`d78094f26eb697c810899a40771a8af6dec7ce19`; 58 tests passed in 0.299
seconds. Full output is in `pixweave-test-output.txt`.

## Backup-Only Migration Verification

The probe opened the live SQLite source with URI `mode=ro`, enabled `query_only`,
observed `total_changes=0`, and confirmed that a write probe failed with
`attempt to write a readonly database`. SQLite's online backup API created a
coherent temporary database; every initialization and tamper operation ran only on
temporary copies.

On the normal copy, the first bounded migration wrote exactly 6 registrations,
6 approvals, and 12 lifecycle rows. The second wrote 0/0/0. All 23 operational
table digests remained identical, including 148 tasks at
`a64f2314ec6975b2ab54a872763d52e0ba6a55b3d8d1aa38a035a1b3b6406ccf`.
`CompanyOS.validate()` returned no errors, signed integrity verification returned
`ok`, and SQLite `integrity_check` returned `ok`.

On an independent tamper copy, the goal artifact retained a valid signed
registration and approval but had no lifecycle rows, and its approval audit actor
was changed. Migration returned the exact approval-audit mismatch and preserved
anchor counts at 1 registration, 1 approval, and 0 lifecycle rows. Full structured
output is in `clean-copy-migration-output.txt`.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| Claim deletion cannot hide forged completion | Base RED returned no errors; GREEN validation is driven by immutable signed history and the exact test passes. |
| Pilot demotion cannot hide forged completion | Base RED returned no errors; GREEN rejects the binding/history mismatch and the exact test passes. |
| Durable evidence survives upgrade | Existing signed claims backfill once into signed history; a second init adds nothing and task/execution rows are identical. |
| Genuine never-bound/nonpilot compatibility | Exact compatibility test completes both categories with `CompanyOS.validate() == []`. |
| No generic early lifecycle mutation | Store initialization contains no backfill; partial signed anchors plus bad audit stay at 1/1/0. |
| Atomic, idempotent bounded migration | Global preflight precedes writes in one immediate transaction; backup runs write 6/6/12 then 0/0/0. |
| Operational noninterference | All 23 operational-table digests remain unchanged on the initialized backup. |
| Full regressions | 106 focused, 212 Agent Company, and 58 PixWeave tests pass. |
| No live mutation/deploy | Live SQLite access was read-only/query-only with zero connection changes; all mutations used disposable copies. |

## Limits

This delivery is limited to the requested final Phase C findings. It does not
authorize Phase D, deployment, live initialization, PixWeave source changes,
customer-data action, outreach, publication, pricing, payment, contract, or other
production/external action. Independent Control & Reliability review remains a
separate governance requirement and is not self-issued here.
