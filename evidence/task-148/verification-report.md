# Task 148 Independent-Review Remediation Report

## Outcome

All findings from the independent review of commit `64010df` are implemented and
verified on the authoritative branch target below. Independent acceptance remains
pending and is not claimed by this report.

Every assurance artifact body used to form an artifact-set digest or consumed by
gate binding, design-manifest validation, context compilation, runtime fencing,
dispatch, integrity verification, or completion validation is parsed, serialized
with the repository's canonical JSON function, and SHA-256 hashed. The recomputed
body digest must equal the row's stored `content_sha256`; a declared hash is never
accepted as proof of the current body.

Consequently, changing only `content_json` while leaving `content_sha256`, the
G4 artifact-set digest, task binding, claim binding, and execution binding unchanged
now fails closed:

- before a bound pilot context or execution assurance binding is created;
- before a heartbeat can renew the execution lease, update an executor, or add
  audit/event records;
- before completion can change task/execution state or add audit/event records.

The completion path now creates an immutable HMAC-authenticated
`assurance_completion_bindings` row before terminal state changes. Its trigger and
runtime validators bind the exact current non-quarantined Trusted Eval result,
affirmative independent Review Decision and body hash, current build artifact-set
hash, execution generation, serialized task result, evidence-path list, and completion
timestamp. A structurally complete row, even with a valid HMAC, cannot authorize a
semantically different task-result assurance object. `record_completion` recomputes
the exact current decision and rejects caller-selected assurance.

Kill-switch dispatch still bypasses dispatch policy, but artifact bodies and the exact
artifact-set anchor are validated before a claim or history row can persist. All
claim denials roll back atomically.

## Fixed Review Target

- Repository: `agent-company`
- Reviewed base commit: `64010dfab9cc3074af5be74616572b01ef73563c`
- Final branch commit: `228eb6b9299c8ac2fd2e39e7bbca2d10205d5b7e`
- Final branch tree: `f705fa87f50bc4b2017d6bd47f82a47163e48b4d`
- Final subject: `fix: bind pilot completion to exact assurance`
- Branch: `main`, one commit ahead of `origin/main`; no push performed

The source and tests are committed in that exact branch commit/tree. Task-148
evidence remains outside the source/test tree and uncommitted for main inspection,
which avoids self-referential evidence while binding the authoritative present-tense
target to the actual branch tip.

Independent review status is **pending**. No post-fix approval or independent review
outcome is claimed. A fresh independent reviewer must review the exact fixed commit
object/tree above.

## Strict TDD Evidence

Two RED iterations preceded their corresponding production controls. The first
directly forged all legacy completion fields plus completed task/execution state,
called the public completion writer with invented values, attempted a structural
completion row, and claimed through the kill switch after body tampering. It ran four
tests with three failures and one error. The second used a valid completion HMAC over
a task result whose embedded assurance was semantically forged; it failed as expected.

The final eight-test GREEN passed in 1.210 seconds and includes legitimate approved
completion plus the public semantic-result-body control. Exact commands, complete RED tracebacks, and
the complete GREEN output are in `review-log.md`. Historical `red-test-output.txt`
and `green-test-output.txt` remain unchanged records of the earlier same-hash fix.

## Final Verification

Focused Phase C assurance/runtime integration:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler tests.test_assurance_kernel tests.test_trusted_evaluator tests.test_assurance_credentials -v
```

Final result: 145 tests passed in 16.852 seconds.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -v
```

Final result: 328 tests passed in 48.471 seconds. Phase D tests inspected denial and
tombstone controls; no Phase D runner, treatment, or protocol command was invoked.

PixWeave canonical suite, read-only source verification:

```text
git status --short --branch
git rev-parse HEAD
git rev-parse HEAD^{tree}
python3.11 -m unittest discover -s tests -v
```

Final result: PixWeave remained clean on `main` at commit
`d78094f26eb697c810899a40771a8af6dec7ce19`, tree
`6f2d526d912fcf283937cd265d298004a31c00b2`; 58 tests passed in
0.313 seconds. PixWeave source remained untouched.

Validation and compilation:

```text
python3.11 -m agent_company.cli validate
rg --files -0 agent_company tests -g '*.py' | xargs -0 python3.11 -m py_compile
python3.11 -m compileall -q agent_company tests
git diff --check
```

Result: live read-only validation returned `{"errors": [], "ok": true}`;
`py_compile`, `compileall`, and diff check passed with no output. A fresh temporary workspace was also
initialized and validated successfully; exact output and schema/trigger inventory are
in `clean-init-validation.txt`.

Security scan used Bandit 1.9.4 from a disposable `/tmp` virtual environment because
Bandit was not installed in the repository Python:

```text
bandit -r agent_company -f json
bandit -r /home/tony/products/pixweave/pixweave -f json
```

Result: Agent Company reported 0 High, 6 Medium, and 26 Low findings across 12,294
lines; PixWeave reported 0 High, 0 Medium, and 0 Low findings across 3,218 lines.
No security High remains.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| Rehash bodies at use time | One canonical helper parses and canonicalizes each consumed artifact body, recomputes SHA-256, and requires equality with stored `content_sha256`. |
| Context fails before work proceeds | The pre-context probe rejects compilation and proves no `task_contexts` or `assurance_execution_bindings` row was created. |
| Runtime heartbeat fails atomically | The post-context probe rejects before lease/executor/audit/event mutation and asserts all observed state is unchanged. |
| Coordinated raw SQL fails closed | Direct tests populate every legacy completion field plus completed task/execution without dropping triggers; the transaction aborts and all observed state is unchanged. |
| Exact cryptographic and semantic binding | The immutable completion row HMAC covers exact assurance, result/evidence digests, generation, and time; triggers also compare the embedded assurance fields and current artifact bodies/set. |
| Public writer is not a bypass | `record_completion` recomputes the current completion decision and rejects caller-invented values or missing result/evidence bodies. |
| Kill switch bypasses dispatch only | Valid anchored empty-set dispatch remains compatible; invalid artifact bodies deny claim/history persistence atomically. |
| Validation and integrity fail closed | Both consumers reject signed structural completion forgeries that are not current and semantically exact. |
| Legitimate completion preserved | Exact Trusted Eval plus affirmative independent Review Decision completes atomically and validates cleanly. |
| Artifact-set binding is sound | Artifact-set construction uses recomputed validated digests, so an unchanged declared hash cannot reproduce a trusted set from a changed body. |
| Preserve unbound behavior | Existing unbound/non-pilot and post-build Review Decision assertions remain green in the final 145-test focused suite and 328-test full suite. |
| Cross-repository noninterference | PixWeave is clean and all 58 canonical tests pass. |

## Code Hashes

- `agent_company/assurance.py`: `d86c0fe581a6c0e4f40a0b655a7ef4df5784acd2fa9f0d5e0c6d525dbd353199`
- `agent_company/db.py`: `fd3aa0ed2b554862dda888021123f695b027c599f69546169b98732c7bdbd444`
- `agent_company/ops.py`: `70a283d58dddcfbe5f0d20345b11ea144038a15c1ba9a5743ec344270ef3b54c`
- `agent_company/pilot_gate.py`: `a5b0eda85a431e58f1a772bf24db01db098aa5514cbcd1d2c26c0254538b301c`
- `tests/test_assurance_cli.py`: `b16ad0fca8c47bbe437497cc91e70cfe5f8cf26b1aa97504b2889f390f6fb34c`
- `tests/test_completion_assurance_gate.py`: `81db24ab85c3e3a2a729a86f48596bf8f7758213c652c2cc2666e464881c0495`
- `tests/test_pilot_gate.py`: `5f251e0ddadef4c7ad31a8a989a7bbe48b12c36e44dde8d6837a160722fee65f`
- Source/test patch SHA-256 from `64010df` to final commit: `c6f467ff4513dc0d3a3246a8ebe998337a6903790cf48a5594bfe9a97ab88cba`

## Protected-State Checks and Limits

The pre-change and final inventories matched byte-for-byte:

- 611 files under `docs/assurance/phase-d` and `evidence/phase-d`;
  aggregate inventory SHA-256
  `387d8bd7c7f774e7a7ee059943de864a49a759d57ceba3432d7520e772cb065f`.
- 120 files under `data`; aggregate inventory SHA-256
  `9e431ac06cbc41fb690b1b29fa0d476363627746af0f726b5a6d1105464a1664`.
- `data/company.sqlite3` SHA-256 remained
  `dc4639df347b1c76178d8bd51e283e9032deef06668a0804082710a6fa0dbb48`.

No protected historical Phase D evidence, PixWeave source, data, credentials,
approvals, or external system was changed. No D0, D1, D2, treatment, or protocol
command was run. No service was started. No external action was performed.

This remediation does not authorize or claim any Phase D execution, pilot treatment,
protocol result, production action, credential/approval change, or independent
approval. Independent Control & Reliability review remains pending until a fresh
review of final commit `228eb6b9299c8ac2fd2e39e7bbca2d10205d5b7e` and tree
`f705fa87f50bc4b2017d6bd47f82a47163e48b4d`.
