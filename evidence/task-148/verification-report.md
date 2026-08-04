# Task 148 Final-Review Remediation Report

## Outcome

All four findings from the final Task 148 review of clean tip
`3cb5b4ad354e59359f76d9958d971b664f189429` are implemented and verified on the
fixed source/test target below. Independent acceptance remains **pending** and is not
claimed by this report.

A single shared verifier in `agent_company/completion_verifier.py` now supplies the
completion semantics used by the SQL insertion guard, task-completion guard, runtime
completion decision, `completion_binding_valid`, validation, and integrity checks.
The completion record persists the exact signed task-result and execution-evidence
JSON snapshots so every consumer can enforce the same meaning.

The verifier fails closed unless all of the following agree:

- the exact latest completed, non-quarantined Trusted Eval run, including its HMAC,
  attempt contract, evaluator principal provenance, evidence file digest, manifest
  JSON/hash/content lineage, input references, and result digest;
- the exact approved Review Decision body, registration signature, lifecycle chain,
  approval signature and audit anchor, plus reviewer independence from every build
  owner, evaluator in the run lineage, and the task owner;
- every approved Review Decision for the initiative is affirmative, findings-empty,
  and contains both exact Trusted Eval result and build artifact-set references;
- the signed completion claim, current artifact bodies/set, execution generation,
  fencing/claim/history provenance, task result, evidence paths, and completion time;
- parsed `task.result.evidence` is semantically equal to
  `task_executions.evidence_paths`, in addition to the individual snapshot hashes.

Every `Store` read/write connection and the dashboard read-only path registers the
completion UDFs. A connection without the semantic UDF cannot execute the trigger and
therefore fails closed. Context-manager exit explicitly closes connections retained
by UDF callbacks. Migration removes legacy immutability guards before snapshot
backfill and recreates the canonical guards afterward.

## Fixed Review Target

- Repository: `agent-company`
- Reviewed base commit: `3cb5b4ad354e59359f76d9958d971b664f189429`
- Reviewed base tree: `588f2c0de9f9de4dd1887adbbc963ff31407b1fc`
- Final source/test commit: `4cde805d6ece9e0b7532b4620007036a3c8f9217`
- Final source/test tree: `dc09a890df65cb64b31ac848c241e75572035ff0`
- Final subject: `fix: close task 148 completion assurance gaps`
- Source/test patch SHA-256 from base: `fd31ca83fbae1a164ccb81e8b6afe0b9721eb98b4d0128364a3bedbf57ac698d`
- Branch: `main`, one local commit ahead of `origin/main`; no push performed

The source and tests are committed in that exact object/tree. These four final
Task-148 evidence files remain uncommitted and outside the fixed source/test tree for
main inspection, avoiding a self-referential source target.

## Strict TDD Evidence

The initial eight-test adversarial run used direct SQL and valid completion signatures
where needed. Seven attacks were incorrectly accepted; the raw connection without
the semantic UDF already failed closed. Result: 8 tests in 1.280 seconds,
`FAILED (failures=7)`, exit status 1.

Implementation exposed two more RED regressions: legacy completion migration failed
with `assurance completion binding is immutable`, and 64 read-only connection
contexts leaked exactly 64 descriptors. The leak also caused two canonical-suite
failures with `filedescriptor out of range in select()`.

The final 19-test GREEN passed in 2.756 seconds. It covers:

- invalid Trusted Eval runtime provenance, stale latest result, and manifest/content
  tampering;
- invalid Review Decision registration/lifecycle and approval signatures;
- reviewer collision with build owner, evaluator lineage, and task owner;
- contradictory reviews with findings, reject decisions, and missing exact refs;
- semantic evidence mismatch in the trigger, runtime validation, and integrity;
- missing-UDF fail-closed behavior, migration order, and connection lifecycle;
- legitimate exact approved completion plus never-bound/non-pilot compatibility.

Every signed direct-SQL denial compares task, execution, task binding, completion
count, audit count, and event count before and after the transaction. The final GREEN
therefore verifies atomic rollback as well as rejection. The exact commands and full
RED/GREEN output are appended to `review-log.md`.

## Final Verification

Focused Phase C/runtime suite:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler tests.test_assurance_kernel tests.test_trusted_evaluator tests.test_assurance_credentials tests.test_dashboard tests.test_event_engine -q
```

Result: 186 tests passed in 20.421 seconds.

Agent Company canonical suite:

```text
python3.11 -m unittest discover -s tests -q
```

Result: 345 tests passed in 50.303 seconds. Phase D unit tests inspected existing
controls; no D0, D1, D2, treatment, protocol, or Phase D runner command was invoked.

PixWeave canonical suite, read-only source verification:

```text
git status --short --branch
git rev-parse HEAD HEAD^{tree}
python3.11 -m unittest discover -s tests -v
```

Result: PixWeave remained clean on `main` at commit
`d78094f26eb697c810899a40771a8af6dec7ce19`, tree
`6f2d526d912fcf283937cd265d298004a31c00b2`; 58 tests passed in 0.307 seconds.
PixWeave source remained untouched.

Validation, compilation, and diff checks:

```text
python3.11 -m agent_company.cli validate
rg --files -0 agent_company tests -g '*.py' | xargs -0 python3.11 -m py_compile
python3.11 -m compileall -q agent_company tests
git diff --check
```

Result: validation returned `{"errors": [], "ok": true}`; `py_compile`,
`compileall`, and diff check passed with no output.

Bandit 1.9.4 results:

- Agent Company: 0 High, 6 Medium, 26 Low, 12,625 lines, no scan errors.
- PixWeave: 0 High, 0 Medium, 0 Low, 3,218 lines, no scan errors.

## Acceptance Mapping

| Finding or control | Final verification |
| --- | --- |
| HIGH1 exact Trusted Eval provenance | Shared verifier requires latest completed/non-quarantined run, valid run signature, attempt contract, evidence file digest, exact manifest JSON/hash/content lineage, input refs, evaluator credential/provenance, and result digest. |
| HIGH2 Review Decision lifecycle and independence | Exact body, registration, lifecycle, approval signature/audit and reviewer independence from build owners, evaluator lineage, and task owner are mandatory. |
| HIGH3 contradictory approved reviews | Every approved review must be affirmative, findings-empty, and reference the exact eval result and artifact-set hash; one good review cannot hide a bad one. |
| MEDIUM4 semantic evidence equality | Signed task-result and evidence snapshots are persisted; parsed `task.result.evidence` must equal execution evidence in trigger, runtime validation, and integrity. |
| Shared SQL/runtime semantics | SQL triggers and runtime consumers call one verifier; incomplete duplicate trigger SQL was removed. |
| UDF availability and failure mode | All application and dashboard Store connections register UDFs; raw missing-UDF inserts abort before persistence. |
| Transaction atomicity | Direct-SQL adversarial helpers prove unchanged task/execution/binding/completion/audit/event state after denial. |
| Legitimate and unbound compatibility | Exact approved pilot completion, never-bound completion, and non-pilot completion pass. |

## Code Hashes

- `agent_company/assurance.py`: `7e929d35a72a01e84fc10881b6c7c7ab8f17d95537214b208d3391715c63a0c6`
- `agent_company/assurance_credentials.py`: `ca7bfff8bd7c62a31b0e94135c8c7446452291c0c950ceaccfaf709385940505`
- `agent_company/ceo_runtime.py`: `1b3f6fdf730b2d31eb14baa10ab6575d4f8d3cab5facddd2d42cdaaa90940eb5`
- `agent_company/completion_verifier.py`: `a00d22875a0bec1a950f4100a576521a82e945895e82ad42031195a087d9c227`
- `agent_company/context_compiler.py`: `f0c0977fab8f34db4b8f29c6dd26cd6231e6d4fe3ca22b98a133517a57637bb1`
- `agent_company/context_knowledge.py`: `1b39d907b8fec10887cc4f7a15188194f09a08b7860ce06d03e58ad7a71653d7`
- `agent_company/dashboard.py`: `c74a48c45acb572189246f74fe7c5cf8ad45ca6468cf1c4b987802c53ca85b8a`
- `agent_company/db.py`: `4ab85b86c7aabe80885f77b09b3f73c5cf1b092df79858cc9c91e63f48f997ff`
- `agent_company/event_engine.py`: `3d47f7e45b56b11a71d4f874847e5f728e78c1b6857ffae845c83c2b568b8e1f`
- `agent_company/ops.py`: `1b022ba791e85d1fe87a1532c212f019cd4db5ddd1491cfb11f96c5f4d939d2e`
- `agent_company/pilot_gate.py`: `f0b83b2322035942a9f7ca7b8f907a6e370608dbb0acd050fa4fed4c51d381a5`
- `agent_company/trusted_evaluator.py`: `307e127d13f7024b0a0accef095cfd6e483a652c92a0e355d3af7fb72fc86e8c`
- `tests/test_completion_assurance_gate.py`: `1b94fe6a3e50d43fed0ff922a5a74fa93ae10b37178e55b89921305433d528fd`

## Protected State and Limits

Final inventories reproduce the protected baseline exactly:

- 611 files under `docs/assurance/phase-d` plus `evidence/phase-d`; aggregate
  inventory SHA-256
  `387d8bd7c7f774e7a7ee059943de864a49a759d57ceba3432d7520e772cb065f`.
- 120 files under `data`; aggregate inventory SHA-256
  `9e431ac06cbc41fb690b1b29fa0d476363627746af0f726b5a6d1105464a1664`.
- `data/company.sqlite3` SHA-256 remained
  `dc4639df347b1c76178d8bd51e283e9032deef06668a0804082710a6fa0dbb48`.
- All six checked Agent Company systemd units are inactive.

No protected Phase D evidence, PixWeave source, data, credentials, approvals, or
external system was changed. No service was started. No D0, D1, D2, treatment,
protocol, or Phase D runner command was run. No source or evidence was pushed.

This remediation does not authorize or claim Phase D execution, treatment or
protocol results, production action, credential/approval changes, or independent
acceptance. A fresh Control & Reliability reviewer must review exact commit
`4cde805d6ece9e0b7532b4620007036a3c8f9217` and tree
`dc09a890df65cb64b31ac848c241e75572035ff0` before review status can change.
