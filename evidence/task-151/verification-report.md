# Task 151 Verification Report

## Outcome

Task 151 makes `assurance-init` initialize the complete authorized Phase C schema
surface: AssuranceKernel, TrustedEvaluator, and PilotGate. It then performs one
bounded migration for the six historical bootstrap artifacts and immediately runs
signed integrity verification. A reconciliation or integrity conflict is printed as
structured JSON and makes the command exit nonzero.

The migration recognizes only the six artifact IDs, version 1 content hashes, kinds,
approval metadata, immutable payload metadata, two exact historical audit events,
and active historical principals recorded in the approved legacy manifest. It writes
registration, approval, and lifecycle anchors only when every candidate reconciles.
Any candidate conflict aborts the transaction before any anchor is written. Existing
anchors make later runs idempotent, while approved legacy rows are still reconciled
against their historical audit evidence so later audit tampering remains fail-closed.

## Delivery Basis

- Repository: `agent-company`
- Branch: `main`
- Base commit: `a96321066829e047453ee53dad8c27fd83091213`
- Base tree: `2c652e63cd82b34449bf93a48d90da8494a997c9`
- Delivery commit: the Git commit containing this report
- Authorized external action: required `git push` only

## Implementation Audit

Every dirty source and test change was audited before final verification:

- `agent_company/cli.py` initializes AssuranceKernel, TrustedEvaluator, and PilotGate,
  runs the legacy migration, verifies integrity, returns the structured outcome, and
  exits 1 for either a migration or integrity conflict.
- `agent_company/assurance.py` defines the exact six-row legacy manifest and validates
  version, kind, content bytes, stored hash, immutable payload metadata, approval
  metadata, registration/approval audit events, and principal identity before writing.
- Candidate collection precedes writes. The transaction returns with zero writes if
  any candidate conflicts; a partial anchor is never repaired or treated as evidence.
- Successful candidates receive one signed registration, one signed approval, and a
  two-entry signed lifecycle chain with the historical timestamps and principals.
- Existing lifecycle rows are never duplicated. Approved manifest rows are still
  audit-reconciled on rerun, and signed integrity verification validates all anchors.
- `tests/test_assurance_cli.py` verifies the full Phase C schema/trigger surface,
  exact migration, idempotence, operational noninterference, validation, content and
  stored-hash tamper, approval tamper, pre-migration audit tamper, and post-migration
  audit tamper.

No unrelated source or test change is included. `git diff --check` is clean.

## Strict TDD Evidence

The retained interrupted RED run executed the five new CLI migration test methods
against the committed base behavior:

```text
python3.11 -m unittest tests.test_assurance_cli -v
```

Result: 5 tests ran with 4 expected failures and 1 expected error. The base command
did not initialize PilotGate, did not report or run a migration, and did not reject
the content, approval, or audit mismatch fixtures. Full output is retained in
`red-test-output.txt`.

## Final Verification

Focused assurance, trusted evaluation, pilot, completion, and operational suite:

```text
python3.11 -m unittest tests.test_assurance_cli tests.test_assurance_kernel \
  tests.test_trusted_evaluator tests.test_pilot_gate \
  tests.test_completion_assurance_gate tests.test_company_os -v
```

Result: 101 tests passed in 9.531 seconds. Full output is in
`focused-test-output.txt`.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -v
```

Result: 207 tests passed in 15.658 seconds. Full output is in
`full-test-output.txt`.

PixWeave canonical suite:

```text
git status --short --branch
git rev-parse HEAD
python3.11 -m unittest discover -s tests -v
```

Result: PixWeave remained clean on `main` at
`d78094f26eb697c810899a40771a8af6dec7ce19`; 58 tests passed in 0.294
seconds. Full output is in `pixweave-test-output.txt`.

Repository checks:

```text
git diff --check
python3.11 -m compileall -q agent_company tests
python3.11 -m json.tool evidence/task-151/CONTINUITY.json
python3.11 -m json.tool evidence/task-151/evidence-manifest.json
```

These checks passed before commit.

## Backup-Only Dry Run

The dry-run opened `/home/tony/agent-company/data/company.sqlite3` with SQLite URI
`mode=ro`, enabled `query_only`, observed `total_changes=0`, and confirmed a write
probe failed with `attempt to write a readonly database`. SQLite's online backup API
then produced a coherent temporary database. All initialization and tamper mutations
were performed only on independent temporary copies; no CLI initialization was run
against the live database.

The normal copy produced exactly 6 signed registrations, 6 signed approvals, and 12
signed lifecycle records. The second initialization wrote zero additional anchors.
All 11 Phase C PilotGate/TrustedEvaluator tables and 22 related triggers were present,
`CompanyOS.validate()` returned no errors, and `PRAGMA integrity_check` returned `ok`.

Every non-assurance/non-trusted-eval operational table was compared by canonical row
digest before and after both initializations. All remained identical, including all
148 task rows at
`f190cc379118beb8cb4aeb53ad12d13ec2200c403e557d9dd7433914af54c442`.

Four independent copies then changed, respectively, artifact content bytes, the
stored content hash, approval metadata, or registration audit evidence. Every command
exited 1 with `integrity_conflict`, and every tampered copy retained zero registration,
approval, and lifecycle anchors. Full structured output and assertions are in
`clean-copy-migration-output.txt`, ending with `LIVE_COPY_DRY_RUN=PASS`.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| `assurance-init` initializes PilotGate and TrustedEvaluator | Fresh and backup-copy checks find all 11 required tables and 22 triggers; focused CLI coverage passes. |
| Only exact audit-reconciled legacy bootstrap artifacts are backfilled | Six fixed IDs/hashes/kinds plus exact immutable metadata, approval, audits, and principals are required before the all-or-nothing write phase. |
| Tampered content/hash/approval/audit remains fail-closed | Focused tests and four backup tamper copies exit nonzero; no tampered copy receives any anchor. Post-backfill audit tamper is also rejected on rerun. |
| Migration is idempotent | First backup init writes 6/6/12 anchors; second init writes 0/0/0 and retains totals 6/6/12. |
| Migration is operationally noninterfering | Every operational table digest is unchanged after both runs; validation and SQLite integrity pass. |
| Live state is not mutated | Live connection is `mode=ro`/`query_only`, write probe is rejected, `total_changes=0`, and all mutations occur on temporary backups. |
| Cross-repository regression | 207 Agent Company and 58 unchanged PixWeave tests pass. |

## Limits

This delivery is limited to the authorized task-151 initialization and legacy-anchor
migration. It does not authorize live mutation, deployment, Phase D, global assurance
enforcement, PixWeave source changes, customer-data action, outreach, publication,
pricing, payment, contract, or production action. No deployment or live initialization
was performed. Independent Control & Reliability review remains a separate governance
requirement and is not self-issued here.
