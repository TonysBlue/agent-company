# Post-Commit Concurrency Verification

## Finding And Fix

At `b0e9bc4342f7d49bce1cc91a3a82b042b9b8e8a4`, concurrent initialization was not
atomic. Python 3.11 `sqlite3.Connection.executescript()` implicitly committed before
executing its script. A second initializer could therefore observe
`task_recovery_records` after its creation but before `task_recovery_provenance` and
the canonical triggers existed. `Store.init_assurance()` correctly failed closed on
that transient state, causing one real executor thread to exit before claim
arbitration.

The fix reserves the SQLite writer before any schema observation and executes each
initializer's existing DDL inside that explicit transaction. A small statement
executor uses `sqlite3.complete_statement()` so trigger bodies remain intact without
`executescript()`'s implicit commit. Operational, assurance, PilotGate, and trusted
evaluator schema initialization are serialized across threads and processes by
SQLite. Existing recovery schema/provenance validation is unchanged and still runs
after the writer reservation.

## Strict TDD Evidence

The existing real-executor test reproduced the reported failure on repetition 60.
A deterministic barrier test then failed on the unmodified implementation because
assurance initialization completed while operational initialization was paused at a
partial governed schema. Exact RED summaries are in `red-test-output.txt`.

After the fix, 100 subprocess repetitions of both focused concurrency tests passed:
200/200 test invocations. Recovery passed 36/36 and task execution continuity passed
28/28; continuity contains the prior 27 tests plus the new deterministic regression.

## Verification

- Focused concurrency stress: 100/100 iterations, 200/200 tests passed.
- Exhausted recovery: 36/36 passed in 1.238 seconds.
- Task execution continuity: 28/28 passed in 4.511 seconds.
- Agent Company canonical suite: 410/410 passed in 59.871 seconds.
- PixWeave canonical suite: 58/58 passed in 0.290 seconds.
- PixWeave remained clean at commit
  `d78094f26eb697c810899a40771a8af6dec7ce19`, tree
  `6f2d526d912fcf283937cd265d298004a31c00b2`.
- `python3.11 -m compileall -q agent_company tests` passed.
- `git diff --check` passed.
- Fresh isolated DB: validation `[]`, assurance `ok`, recovery provenance
  `canonical`, SQLite integrity `ok`.
- Malformed isolated provenance schema: initialization raised
  `sqlite3.IntegrityError: governed recovery provenance schema is not canonical`;
  schema was unchanged, schema status remained `invalid`, SQLite integrity was `ok`.

## Protected State

No live initialization, migration, deployment, recovery, customer, Beta, Phase D,
Treatment, or C2 operation was invoked. The live `data/company.sqlite3` remained at
SHA-256 `275ead1d61a0a0271e13b4aae92747750cf30bbb3f2e0a871e1e6afb4773c9a5`,
size `438964224`, mtime `1786164268`. The dashboard, worker, control-review,
customer, platform, and product runner services remained inactive in both user and
system scopes.
