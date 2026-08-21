# Task 154 Verification Report

## Outcome

The recovery write path and every governed consumer now bind the recovery record,
audit row, and `task.requeued` event to one canonical payload derived from the exact
signed recovery row. The payload includes the record/task/audit/event IDs, scope,
process-death proof, previous and new task/execution snapshots, recovery signature,
and canonical event priority `10`.

A separately signed `task_recovery_provenance` anchor fixes the exact
record-to-audit-to-event mapping and priority. Canonical insert and immutability
triggers reject substitutions and replays. Read-only recovery validation reconciles
the exact record, anchor, recovery-audit, and recovery-event ID sets, so missing,
additional, replayed, replaced, or duplicate governed evidence fails closed after
trigger restoration. Deleting the record/audit/event triplet while retaining the
durable anchor is detected by both `CompanyOS.validate()` and assurance integrity.

Recovery schema validation now accepts only the exact SQLite table-level unique
constraint: every unique index must have `PRAGMA index_list.origin='u'`, must be
non-partial, and must expose the exact ascending BINARY key columns. Explicit and
partial lookalike unique indexes are rejected. Event priority `10` is constrained by
schema, signed in the anchor, protected by the immutable event trigger, and checked by
integrity validation.

## Delivery Basis

- Repository: `agent-company`
- Branch: `main`
- Clean base commit: `c76d28854e43fffe6733b59d5f9e3861afb645bb`
- Clean base tree: `567a32cf7351b0f295f534006903602755661d3f`
- Delivery commit: the Git commit containing this report
- Authorized external action: required `git push` only

## Strict TDD

The nine finding-specific probes were first replayed in a disposable detached
worktree at the exact clean base. Seven failed as expected and two pre-existing
controls passed. The failures exposed payload substitution, audit replay, event
replay, complete recovery-triplet deletion, partial and explicit-index schema
lookalikes, and event priority mutation. Exact output is retained in
`red-test-output.txt`.

The identical nine probes pass after the implementation: 9/9 in 0.347 seconds. The
complete recovery module passes 36/36 in 1.650 seconds. Exact commands and results are
retained in `green-test-output.txt`.

## Final Verification

- Agent Company canonical suite: 409/409 passed in 69.203 seconds.
- PixWeave canonical suite: 58/58 passed in 0.292 seconds; repository remained clean
  at commit `d78094f26eb697c810899a40771a8af6dec7ce19`, tree
  `6f2d526d912fcf283937cd265d298004a31c00b2`.
- Fresh isolated database: `CompanyOS.validate() == []`, assurance integrity `ok`,
  SQLite `integrity_check=ok`.
- Isolated provenance-table deletion: validation reports recovery failure and
  assurance returns `integrity_conflict`; SQLite structural integrity remains `ok`.
- `python3.11 -m compileall -q agent_company tests` passed.
- `git diff --check` passed.
- Added-line security searches found no new network, subprocess, shell,
  service-control, or live-database path. Bandit, Ruff, mypy, and Semgrep are not
  installed, so no result is claimed for those tools.

The canonical suites contain inert unit tests whose names and synthetic fixtures refer
to Beta and Phase D. No Beta, Phase D, Treatment, or C2 operational workflow was
executed, and no customer or external action occurred.

## Live-State Noninterference

No live schema initialization, migration, deployment, recovery call, or write probe
was performed. All mutation tests used temporary databases. The live
`data/company.sqlite3` SHA-256 remained
`275ead1d61a0a0271e13b4aae92747750cf30bbb3f2e0a871e1e6afb4773c9a5`; size and mtime
remained `438964224` bytes and `1786164268`. All scoped `agent-company-*` services
were inactive when final verification began and remained inactive.

## Acceptance Mapping

| Finding | Verification |
| --- | --- |
| H1 exact signed payload binding | Audit and event payloads equal the one canonical representation of all signed recovery fields, IDs, proof, signature, and priority; substitution is detected. |
| H2 one-to-one provenance | Insert guards reject replay; exact record/audit/event ID-set reconciliation rejects missing, duplicate, replayed, or additional governed rows. |
| H3 deletion/replacement/duplicate | Signed durable anchor detects deleted triplet; record signatures and anchor reconciliation detect replacements and duplicates after trigger repair. |
| M1 exact unique constraint | Partial, explicit-index, wrong-origin, wrong-order/collation, and additional unique indexes fail schema validation. |
| M2 canonical priority | Priority `10` is written canonically, schema-constrained, anchor-signed, trigger-immutable, payload-bound, and integrity-checked. |

## Remaining Risks

The signed anchor remains in the same SQLite trust domain. A fully privileged attacker
who drops every guard and erases the recovery records, audit/events, provenance
anchors, and associated integrity key/metadata can remove all local evidence; this
control does not provide an external append-only ledger.

An existing store that already contains recovery history but lacks the new provenance
anchor fails closed and requires a separately reviewed migration. This change does not
auto-bless or rewrite that history. Independent Control & Reliability review is not
included and cannot be self-issued by the implementer.
