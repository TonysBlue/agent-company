# Task 148 Final Trusted Eval Ledger Remediation

## Outcome

The final HIGH finding against clean tip
`334b622615cd66755d15011a1fa95cdecfc985d4` is implemented and verified on exact
source/test commit `d25f3272ef7ed87674f6e3fe5c6d974af44e7a96`, tree
`1386710e89156b0621fe49427cc431ab9c0174a3`. Independent review remains pending.

The shared verifier in `agent_company/completion_verifier.py` now validates the
complete `trusted_eval_runs` ledger for the bound initiative before accepting a
result. The same `completion_assurance` path is already called by the SQLite
completion-binding semantic UDF/trigger and by runtime completion decisions, so the
full-ledger rule is shared rather than duplicated.

The verifier requires:

- one immutable attempt contract with `max_attempts` from 1 through 3;
- exactly one row for each attempt 1 through the actual latest row, with the row
  count within budget;
- every row to carry an official terminal status (`failed`, `abandoned`, or
  `completed`), an integer seed, canonical result digest, valid HMAC, active Trusted
  Evaluator/operator principal provenance, content-addressed evidence, and valid
  manifest JSON/hash/content relationships;
- canonical UTC second-resolution creation timestamps, contract before or equal to
  attempt 1, manifests before or equal to the run that uses them, and nondecreasing
  run chronology;
- no quarantine and a latest ledger row whose status is `completed`.

No constraint was invented requiring refs, seed, evidence, evaluator identity, or
status to be equal across attempts. The official `TrustedEvaluator.record_run`
implementation does not promise those values are constant. The GREEN compatibility
test varies status, seed, and the final candidate manifest through the official API.

## Fixed Review Target

- Reviewed clean tip: `334b622615cd66755d15011a1fa95cdecfc985d4`
- Reviewed clean tree: `6f7dcb1581986408fc8ba3e1556c938ee2db3c61`
- Final source/test commit: `d25f3272ef7ed87674f6e3fe5c6d974af44e7a96`
- Final source/test tree: `1386710e89156b0621fe49427cc431ab9c0174a3`
- Subject: `fix: validate trusted eval attempt ledgers`
- Patch SHA-256 from parent: `001bd896ae01aaa9062724acf0913a0de3b2273e6fa29a7f9818690e7ba8d9c7`
- Source/tests: committed at the exact target above; no push performed

The source and tests are committed in that exact object/tree. This updated Task 148
evidence is a working-tree handoff for inspection and does not alter the source/test
tree. Unlike the superseded metadata, it records the prior evidence as integrated at
reviewed parent `334b622`.

## Strict TDD Evidence

The final eight tests were replayed against an isolated `git archive` of exact parent
`334b622`, with the final test file copied into that temporary tree and the parent
production code left unchanged. Seven adversarial tests failed because the old
verifier accepted the forged ledger; the official multi-attempt control passed.
Exact result: 8 tests in 1.249 seconds, `FAILED (failures=7)`, exit status 1.

The attacks cover:

- a valid-HMAC gap with attempt 1 rewritten to attempt 2;
- a valid-HMAC duplicate attempt inserted after direct-SQL removal of the uniqueness
  guard, proving verifier-level fail-closed behavior;
- stale/latest manipulation where attempt 1 completed but official attempt 2 failed;
- a bad signature on prior attempt 1 hidden by valid completed attempt 2;
- a valid-HMAC prior attempt with nonexistent evaluator provenance;
- a valid-HMAC prior attempt with impossible `running` status;
- a valid-HMAC prior attempt with a timestamp the official evaluator cannot emit.

The exact GREEN on final commit `d25f327` passed all eight tests in 1.173 seconds,
exit status 0. Direct-SQL denial uses the existing atomicity helper, which verifies
unchanged task, execution, task binding, completion count, audit count, and event
count. The stale/latest test also exercises runtime `complete_task` denial before
the signed direct-SQL denial.

## Final Verification

Focused Phase C/runtime suite:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler tests.test_assurance_kernel tests.test_trusted_evaluator tests.test_assurance_credentials tests.test_dashboard tests.test_event_engine -q
```

Result: 194 tests passed in 21.077 seconds.

Canonical Agent Company suite:

```text
python3.11 -m unittest discover -s tests -v
```

Result: 353 tests passed in 49.542 seconds, exit status 0; a final isolated quiet
confirmation also passed 353 tests in 46.019 seconds. An earlier overlapping
canonical run reported one temporary Git metadata concurrency error in a Phase D
unit test after 353 tests; the isolated canonical rerun passed. No D0, D1, D2,
treatment, protocol, or Phase D runner command was invoked.

PixWeave read-only verification:

```text
git status --short --branch
git rev-parse HEAD HEAD^{tree}
python3.11 -m unittest discover -s tests -v
```

PixWeave remained clean and aligned with `origin/main` at commit
`d78094f26eb697c810899a40771a8af6dec7ce19`, tree
`6f2d526d912fcf283937cd265d298004a31c00b2`; 58 tests passed in 0.312 seconds.

Validation and compilation:

```text
python3.11 -m agent_company.cli validate
rg --files -0 agent_company tests -g '*.py' | xargs -0 python3.11 -m py_compile
python3.11 -m compileall -q agent_company tests
git diff --check
```

Validation returned `{"errors": [], "ok": true}`. All four commands passed.

Bandit 1.9.4:

- Agent Company: 0 High, 6 Medium, 26 Low, 12,706 lines, no scan errors.
- PixWeave: 0 High, 0 Medium, 0 Low, 3,218 lines, no scan errors.

## Protected State and Limits

- Phase D protected inventory: 611 files,
  `387d8bd7c7f774e7a7ee059943de864a49a759d57ceba3432d7520e772cb065f`.
- Data inventory: 120 files,
  `9e431ac06cbc41fb690b1b29fa0d476363627746af0f726b5a6d1105464a1664`.
- `data/company.sqlite3` SHA-256:
  `dc4639df347b1c76178d8bd51e283e9032deef06668a0804082710a6fa0dbb48`.
- Diff from source parent across protected Phase D, data, credentials, and approvals:
  empty.
- Six checked Agent Company/PixWeave systemd units: all inactive.

No protected Phase D evidence, PixWeave source, data, credentials, approvals, or
external system was modified. Services remain stopped. No source/evidence was pushed.
Nothing in this report constitutes approval or independent acceptance.

## Code Hashes

- `agent_company/completion_verifier.py`:
  `4c7b96bd2dd4367de3ea82d095ac1a3545f91f8c470d3667142ae4e1430bafc2`
- `tests/test_completion_assurance_gate.py`:
  `3c4b732d43b5e4147809fcc3d0e7b7e90f4db9ebd9bd943effe8b7123e3e1355`
