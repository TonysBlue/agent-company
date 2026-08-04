# Task 148 Same-Hash Tamper Remediation Verification Report

## Outcome

The independent-review High is fixed for the Phase C bound C2/C3 pilot.

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

Unbound and explicit non-pilot behavior is unchanged. Post-build Review Decisions
remain excluded from the G4 build set, and all existing completion protections remain
in force.

## Fixed Review Target

- Repository: `agent-company`
- Base/working-tree HEAD: `8cf986c3427863cb41ae86c71afc0f48d1ede5fd`
- Base tree: `12c4d0e20b9c9085c1b6b020c6f55c7750c72134`
- Fixed review commit object: `d5467f1cb51440353e10caf3eedf49d47bb15ff7`
- Fixed review tree: `e667b4b04f7870c3f0a60ca99547cc867934e526`
- Fixed review subject: `fix: reject phase c artifact body same-hash tampering`
- Fixed review parent: `8cf986c3427863cb41ae86c71afc0f48d1ede5fd`

The fixed review commit object was created from exactly the three changed source/test
files with a temporary Git index. No branch or tag points to it, `HEAD` was not
advanced, the repository index was not staged, and the same changes remain visible as
an uncommitted working-tree diff for main to inspect. Evidence files are not part of
the fixed source/test tree.

Independent review status is **pending**. No post-fix approval or independent review
outcome is claimed. A fresh independent reviewer must review the exact fixed commit
object/tree above.

## Strict TDD Evidence

The three regression tests were added before production code changed. Their helper
changes the canonical artifact body while proving that the stored
`content_sha256` and the G4/task/claim/execution artifact-set bindings remain
unchanged.

Authoritative RED command:

```text
python3.11 -m unittest \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_unchanged_declared_hash_cannot_hide_body_tamper_from_context_compilation \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_unchanged_declared_hash_cannot_hide_body_tamper_from_heartbeat \
  tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completion_remains_protected_from_unchanged_declared_hash_body_tamper -v
```

Result: 3 tests ran in 0.403 seconds. Context compilation and heartbeat produced the
two expected failures because neither rejected the tamper. The completion control
passed, proving completion was already protected. Exact output is in
`red-test-output.txt`.

The same exact command after the minimal implementation and final assertion
strengthening passed all 3 tests in 0.370 seconds. Exact output is in
`green-test-output.txt`.

During assertion hardening, one intermediate GREEN replay errored because the optional
executor row was absent; the assertion was corrected to preserve the existing
optional-row semantics before final verification. No production change was made for
that fixture-only correction.

## Final Verification

Focused Phase C assurance/runtime integration:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler tests.test_assurance_kernel tests.test_trusted_evaluator tests.test_assurance_credentials -v
```

Final result: 138 tests passed in 14.114 seconds. Exact output is in
`focused-test-output.txt`. An earlier post-implementation run of the same command
also passed 138 tests in 14.032 seconds.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -v
```

Final result: 321 tests passed in 44.811 seconds. Exact output is in
`full-test-output.txt`. Two earlier current-tree replays also passed all 321 tests
in 44.865 and 44.307 seconds.

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
0.366 seconds. Exact output is in `pixweave-test-output.txt`. An earlier
current-tree replay also passed all 58 tests in 0.319 seconds.

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

Result: Agent Company reported 0 High, 6 Medium, and 26 Low findings across 11,808
lines; PixWeave reported 0 High, 0 Medium, and 0 Low findings across 3,218 lines.
No security High remains.

## Acceptance Mapping

| Acceptance criterion | Verification |
| --- | --- |
| Rehash bodies at use time | One canonical helper parses and canonicalizes each consumed artifact body, recomputes SHA-256, and requires equality with stored `content_sha256`. |
| Context fails before work proceeds | The pre-context probe rejects compilation and proves no `task_contexts` or `assurance_execution_bindings` row was created. |
| Runtime heartbeat fails atomically | The post-context probe rejects before lease/executor/audit/event mutation and asserts all observed state is unchanged. |
| Completion remains protected | A fully evaluated and reviewed pilot still rejects the same tamper before task/execution/audit/event mutation. |
| Artifact-set binding is sound | Artifact-set construction uses recomputed validated digests, so an unchanged declared hash cannot reproduce a trusted set from a changed body. |
| Preserve unbound behavior | Existing unbound/non-pilot and post-build Review Decision assertions pass in the 138-test focused suite and 321-test full suite. |
| Cross-repository noninterference | PixWeave is clean and all 58 canonical tests pass. |

## Code Hashes

- `agent_company/assurance.py`: `d3875853d4d9328342781a86e47f6725f88683698e0caeffa1ce08a23e53e15f`
- `agent_company/pilot_gate.py`: `2a2efec9faa37601b4887e5b23c27ebdb89323e5f0d1bb0e48e945fdda30b394`
- `tests/test_completion_assurance_gate.py`: `f411ec0bfd77ef829a117748f0bf95e3342ed43acff3801dbdaecc0b052c0f76`
- Source/test patch SHA-256 against HEAD: `2b37a636fb5cc2d5b90b7d54935a31d15062098f5fff6824f39f2580fd80e359`

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
review of fixed commit `d5467f1cb51440353e10caf3eedf49d47bb15ff7` and tree
`e667b4b04f7870c3f0a60ca99547cc867934e526`.
