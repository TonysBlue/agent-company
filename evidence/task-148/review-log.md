# Task 148 Independent-Review Remediation Log

This file is the append-only RED/GREEN and verification record for the independent
review of commit `64010dfab9cc3074af5be74616572b01ef73563c`. Earlier files in
`evidence/task-148` are preserved historical evidence for prior task-148 work and
were not rewritten for this follow-up.

Independent acceptance remains pending. Nothing in this log is an approval.

## 2026-08-04 — RED 1: coordinated completion and kill-switch bypasses

Production code was unchanged from `64010df` when this command ran:

```text
python3.11 -m unittest tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_coordinated_raw_sql_cannot_forge_all_completion_state tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_structurally_complete_raw_completion_row_fails_closed_atomically tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_public_record_completion_rejects_caller_forged_assurance_atomically tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim -v
```

Exact terminal result:

```text
test_coordinated_raw_sql_cannot_forge_all_completion_state (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_coordinated_raw_sql_cannot_forge_all_completion_state) ... FAIL
test_structurally_complete_raw_completion_row_fails_closed_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_structurally_complete_raw_completion_row_fails_closed_atomically) ... ERROR
test_public_record_completion_rejects_caller_forged_assurance_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_public_record_completion_rejects_caller_forged_assurance_atomically) ... FAIL
test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim) ... FAIL

======================================================================
ERROR: test_structurally_complete_raw_completion_row_fails_closed_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_structurally_complete_raw_completion_row_fails_closed_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 989, in test_structurally_complete_raw_completion_row_fails_closed_atomically
    conn.execute(
sqlite3.OperationalError: no such table: assurance_completion_bindings

======================================================================
FAIL: test_coordinated_raw_sql_cannot_forge_all_completion_state (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_coordinated_raw_sql_cannot_forge_all_completion_state)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 922, in test_coordinated_raw_sql_cannot_forge_all_completion_state
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion"):
AssertionError: IntegrityError not raised

======================================================================
FAIL: test_public_record_completion_rejects_caller_forged_assurance_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_public_record_completion_rejects_caller_forged_assurance_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1042, in test_public_record_completion_rejects_caller_forged_assurance_atomically
    with self.assertRaisesRegex(ValueError, "completion assurance"):
AssertionError: ValueError not raised

======================================================================
FAIL: test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1101, in test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim
    with self.assertRaisesRegex(ValueError, "integrity"):
AssertionError: ValueError not raised

----------------------------------------------------------------------
Ran 4 tests in 0.412s

FAILED (failures=3, errors=1)
```

Exit status: `1`, expected RED. The results proved that coordinated raw SQL could
populate every legacy completion field and terminal state, caller-selected values
could enter `record_completion`, no cryptographic completion record existed, and the
kill switch could claim a task whose artifact body no longer matched its declared
hash.

## 2026-08-04 — RED 2: valid signature without semantic binding

After the first implementation pass, this additional test was added before its
production guard:

```text
python3.11 -m unittest tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_valid_signature_cannot_bind_semantically_forged_task_result -v
```

Exact terminal result:

```text
test_valid_signature_cannot_bind_semantically_forged_task_result (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_valid_signature_cannot_bind_semantically_forged_task_result) ... FAIL

======================================================================
FAIL: test_valid_signature_cannot_bind_semantically_forged_task_result (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_valid_signature_cannot_bind_semantically_forged_task_result)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1168, in test_valid_signature_cannot_bind_semantically_forged_task_result
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion"):
AssertionError: IntegrityError not raised

----------------------------------------------------------------------
Ran 1 test in 0.188s

FAILED (failures=1)
```

Exit status: `1`, expected RED. This proved that a valid HMAC and matching serialized
body digest were insufficient unless the task-result assurance object was also
semantically equal to the completion record.

## 2026-08-04 — GREEN: exact completion, claim, and compatibility controls

Final security-control command:

```text
python3.11 -m unittest tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_coordinated_raw_sql_cannot_forge_all_completion_state tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_structurally_complete_raw_completion_row_fails_closed_atomically tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_public_record_completion_rejects_caller_forged_assurance_atomically tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_valid_signature_cannot_bind_semantically_forged_task_result tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_and_integrity_reject_signed_structural_completion_forgery tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_exact_eval_and_independent_review_allow_atomic_completion -v
```

Exact terminal result:

```text
test_coordinated_raw_sql_cannot_forge_all_completion_state (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_coordinated_raw_sql_cannot_forge_all_completion_state) ... ok
test_structurally_complete_raw_completion_row_fails_closed_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_structurally_complete_raw_completion_row_fails_closed_atomically) ... ok
test_public_record_completion_rejects_caller_forged_assurance_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_public_record_completion_rejects_caller_forged_assurance_atomically) ... ok
test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim) ... ok
test_valid_signature_cannot_bind_semantically_forged_task_result (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_valid_signature_cannot_bind_semantically_forged_task_result) ... ok
test_validate_and_integrity_reject_signed_structural_completion_forgery (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_and_integrity_reject_signed_structural_completion_forgery) ... ok
test_exact_eval_and_independent_review_allow_atomic_completion (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_exact_eval_and_independent_review_allow_atomic_completion) ... ok

----------------------------------------------------------------------
Ran 7 tests in 1.017s

OK
```

Exit status: `0`. The direct forgery cases ran without dropping canonical triggers;
each asserted transaction rollback and unchanged task, execution, binding, claim,
history, audit, and event state as applicable. The final control proves legitimate
exact Trusted Eval plus affirmative independent Review Decision completion remains
available.

## 2026-08-04 — Final branch target and verification

Authoritative source/test target:

```text
commit  228eb6b9299c8ac2fd2e39e7bbca2d10205d5b7e
tree    f705fa87f50bc4b2017d6bd47f82a47163e48b4d
parent  64010dfab9cc3074af5be74616572b01ef73563c
subject fix: bind pilot completion to exact assurance
branch  main (ahead of origin/main by 1; no push performed)
```

Focused command:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler tests.test_assurance_kernel tests.test_trusted_evaluator tests.test_assurance_credentials -v
```

Exact terminal summary:

```text
----------------------------------------------------------------------
Ran 145 tests in 16.852s

OK
```

Canonical command:

```text
python3.11 -m unittest discover -s tests -v
```

Exact terminal summary:

```text
----------------------------------------------------------------------
Ran 328 tests in 48.471s

OK
```

The canonical command executed unit tests that inspect Phase D denial/tombstone
controls. It did not invoke a D0/D1/D2 runner, treatment, or protocol command.

PixWeave read-only verification:

```text
## main...origin/main
d78094f26eb697c810899a40771a8af6dec7ce19
6f2d526d912fcf283937cd265d298004a31c00b2
----------------------------------------------------------------------
Ran 58 tests in 0.313s

OK
```

Validation and compilation:

```text
python3.11 -m agent_company.cli validate
{
  "errors": [],
  "ok": true
}

fresh temporary workspace:
initialized
{
  "errors": [],
  "ok": true
}

git diff --check
exit 0, no output

rg --files -0 agent_company tests -g '*.py' | xargs -0 python3.11 -m py_compile
exit 0, no output

python3.11 -m compileall -q agent_company tests
exit 0, no output
```

Bandit 1.9.4 final summaries:

```text
Agent Company: 0 High, 6 Medium, 26 Low, 12294 lines; no scan errors
PixWeave:      0 High, 0 Medium, 0 Low, 3218 lines; no scan errors
```

Protected-state exact results:

```text
Phase D protected inventory: 611 files
387d8bd7c7f774e7a7ee059943de864a49a759d57ceba3432d7520e772cb065f

data inventory: 120 files
9e431ac06cbc41fb690b1b29fa0d476363627746af0f726b5a6d1105464a1664

data/company.sqlite3
dc4639df347b1c76178d8bd51e283e9032deef06668a0804082710a6fa0dbb48

git diff 64010df --name-only -- docs/assurance/phase-d evidence/phase-d data credentials approvals
exit 0, no output

PixWeave status
## main...origin/main
```

No protected Phase D evidence, PixWeave source, data, credentials, approvals, or
external system was modified. No service was started. No external action occurred.

Final source/test hashes and patch identity:

```text
agent_company/assurance.py              d86c0fe581a6c0e4f40a0b655a7ef4df5784acd2fa9f0d5e0c6d525dbd353199
agent_company/db.py                     fd3aa0ed2b554862dda888021123f695b027c599f69546169b98732c7bdbd444
agent_company/ops.py                    70a283d58dddcfbe5f0d20345b11ea144038a15c1ba9a5743ec344270ef3b54c
agent_company/pilot_gate.py             a5b0eda85a431e58f1a772bf24db01db098aa5514cbcd1d2c26c0254538b301c
tests/test_assurance_cli.py             b16ad0fca8c47bbe437497cc91e70cfe5f8cf26b1aa97504b2889f390f6fb34c
tests/test_completion_assurance_gate.py 81db24ab85c3e3a2a729a86f48596bf8f7758213c652c2cc2666e464881c0495
tests/test_pilot_gate.py                 5f251e0ddadef4c7ad31a8a989a7bbe48b12c36e44dde8d6837a160722fee65f
git diff 64010df HEAD --binary SHA-256 c6f467ff4513dc0d3a3246a8ebe998337a6903790cf48a5594bfe9a97ab88cba
```

The public API semantic-result-body RED above was followed by the implementation
check and a final eight-test replay (the seven tests listed in the GREEN command plus
`test_public_record_completion_rejects_semantically_forged_result_body`):

```text
python3.11 -m unittest tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_coordinated_raw_sql_cannot_forge_all_completion_state tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_structurally_complete_raw_completion_row_fails_closed_atomically tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_public_record_completion_rejects_caller_forged_assurance_atomically tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_public_record_completion_rejects_semantically_forged_result_body tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_kill_switch_rejects_invalid_artifact_body_before_atomic_claim tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_valid_signature_cannot_bind_semantically_forged_task_result tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_and_integrity_reject_signed_structural_completion_forgery tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_exact_eval_and_independent_review_allow_atomic_completion -v

----------------------------------------------------------------------
Ran 8 tests in 1.210s

OK
```

## 2026-08-04 — Final-review RED: four completion-assurance gaps

The source was still the requested clean tip
`3cb5b4ad354e59359f76d9958d971b664f189429` when the following eight-test
adversarial command ran. The direct-SQL cases used structurally complete completion
rows and valid completion signatures; the no-UDF control used a raw SQLite
connection.

```text
python3.11 -m unittest \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_approval_signature_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_any_contradictory_approved_review_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completion_insert_fails_closed_without_registered_semantic_udf -v
```

Exact captured terminal result:

```text
test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically) ... FAIL
test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically) ... FAIL
test_signed_sql_rejects_review_with_invalid_approval_signature_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_approval_signature_atomically) ... FAIL
test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically) ... FAIL
test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically) ... FAIL
test_signed_sql_rejects_any_contradictory_approved_review_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_any_contradictory_approved_review_atomically) ... FAIL
test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically) ... FAIL
test_completion_insert_fails_closed_without_registered_semantic_udf (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completion_insert_fails_closed_without_registered_semantic_udf) ... ok

======================================================================
FAIL: test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1068, in test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically
    self._assert_signed_sql_completion_rejected(result_sha256)
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 307, in _assert_signed_sql_completion_rejected
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
AssertionError: IntegrityError not raised

======================================================================
FAIL: test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1090, in test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically
    self._assert_signed_sql_completion_rejected(result_sha256)
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 307, in _assert_signed_sql_completion_rejected
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
AssertionError: IntegrityError not raised

======================================================================
FAIL: test_signed_sql_rejects_review_with_invalid_approval_signature_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_approval_signature_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1105, in test_signed_sql_rejects_review_with_invalid_approval_signature_atomically
    self._assert_signed_sql_completion_rejected(result_sha256)
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 307, in _assert_signed_sql_completion_rejected
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
AssertionError: IntegrityError not raised

======================================================================
FAIL: test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1131, in test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically
    self._assert_signed_sql_completion_rejected(result_sha256)
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 307, in _assert_signed_sql_completion_rejected
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
AssertionError: IntegrityError not raised

======================================================================
FAIL: test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1118, in test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically
    self._assert_signed_sql_completion_rejected(result_sha256)
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 307, in _assert_signed_sql_completion_rejected
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
AssertionError: IntegrityError not raised

======================================================================
FAIL: test_signed_sql_rejects_any_contradictory_approved_review_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_any_contradictory_approved_review_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1141, in test_signed_sql_rejects_any_contradictory_approved_review_atomically
    self._assert_signed_sql_completion_rejected(result_sha256)
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 307, in _assert_signed_sql_completion_rejected
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
AssertionError: IntegrityError not raised

======================================================================
FAIL: test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1151, in test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically
    self._assert_signed_sql_completion_rejected(
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 307, in _assert_signed_sql_completion_rejected
    with self.assertRaisesRegex(sqlite3.IntegrityError, "completion.*integrity"):
AssertionError: IntegrityError not raised

----------------------------------------------------------------------
Ran 8 tests in 1.280s

FAILED (failures=7)
EXIT_CODE=1
```

Exit status `1` was the expected RED. Seven signed direct-SQL attacks were accepted:
invalid Trusted Eval runtime lineage; invalid Review Decision registration/lifecycle;
invalid approval signature; reviewer overlap with build ownership and evaluator
lineage; a contradictory approved review; and semantic divergence between
`task.result.evidence` and `task_executions.evidence_paths`. The raw-connection
control already proved the old trigger failed closed when an SQL function was absent.
Every direct-SQL helper compared task, execution, task binding, completion count,
audit count, and event count before and after the rejected transaction; those
atomicity assertions pass in GREEN.

## 2026-08-04 — Additional implementation REDs

The completed-row migration test first exposed an old immutable trigger firing before
the new signed snapshots could be backfilled. After the test fixture represented the
actual legacy table correctly, the production migration remained RED:

```text
test_completed_binding_snapshot_migration_precedes_old_immutability_trigger (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completed_binding_snapshot_migration_precedes_old_immutability_trigger) ... ERROR

======================================================================
ERROR: test_completed_binding_snapshot_migration_precedes_old_immutability_trigger (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completed_binding_snapshot_migration_precedes_old_immutability_trigger)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 2514, in test_completed_binding_snapshot_migration_precedes_old_immutability_trigger
    self.gate.init()
  File "/home/tony/agent-company/agent_company/pilot_gate.py", line 128, in init
    conn.execute(
sqlite3.IntegrityError: assurance completion binding is immutable

----------------------------------------------------------------------
Ran 1 test in 0.167s

FAILED (errors=1)
EXIT=1
```

The UDF callbacks also retained connections after context-manager exit. Two canonical
suite attempts exposed the operational consequence in the event engine
(`filedescriptor out of range in select()`), after 339 tests in 148.027 seconds and
144.083 seconds respectively. The focused descriptor RED made the leak deterministic:

```text
test_store_context_closes_connections_retained_by_udf_callbacks (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_store_context_closes_connections_retained_by_udf_callbacks) ... FAIL

======================================================================
FAIL: test_store_context_closes_connections_retained_by_udf_callbacks (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_store_context_closes_connections_retained_by_udf_callbacks)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/tony/agent-company/tests/test_completion_assurance_gate.py", line 1230, in test_store_context_closes_connections_retained_by_udf_callbacks
    self.assertLessEqual(after - before, 4)
AssertionError: 64 not less than or equal to 4

----------------------------------------------------------------------
Ran 1 test in 0.172s

FAILED (failures=1)
EXIT=1
```

The migration now removes old completion immutability guards before backfill and
recreates the canonical guards afterward. `StoreConnection` now commits or rolls
back and explicitly closes on context exit, releasing the UDF closures.

## 2026-08-04 — Final-review GREEN

Final security and compatibility command:

```text
python3.11 -m unittest \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_nonlatest_completed_trusted_eval_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_tampered_trusted_eval_manifest_and_content_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_approval_signature_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_task_owner_review_even_with_valid_anchors_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_any_contradictory_approved_review_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_contradictory_exact_refs_and_findings_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_approved_reject_decision_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_approved_review_missing_exact_refs_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completion_insert_fails_closed_without_registered_semantic_udf \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_and_integrity_reject_signed_evidence_semantic_mismatch \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_store_context_closes_connections_retained_by_udf_callbacks \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completed_binding_snapshot_migration_precedes_old_immutability_trigger \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_exact_eval_and_independent_review_allow_atomic_completion \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_keeps_never_bound_and_nonpilot_completions_compatible -v
```

Exact captured terminal result:

```text
test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_trusted_eval_with_invalid_runtime_lineage_atomically) ... ok
test_signed_sql_rejects_nonlatest_completed_trusted_eval_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_nonlatest_completed_trusted_eval_atomically) ... ok
test_signed_sql_rejects_tampered_trusted_eval_manifest_and_content_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_tampered_trusted_eval_manifest_and_content_atomically) ... ok
test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_registration_and_lifecycle_atomically) ... ok
test_signed_sql_rejects_review_with_invalid_approval_signature_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_review_with_invalid_approval_signature_atomically) ... ok
test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_build_owner_review_even_with_valid_anchors_atomically) ... ok
test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_evaluator_review_even_with_valid_anchors_atomically) ... ok
test_signed_sql_rejects_task_owner_review_even_with_valid_anchors_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_task_owner_review_even_with_valid_anchors_atomically) ... ok
test_signed_sql_rejects_any_contradictory_approved_review_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_any_contradictory_approved_review_atomically) ... ok
test_signed_sql_rejects_contradictory_exact_refs_and_findings_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_contradictory_exact_refs_and_findings_atomically) ... ok
test_signed_sql_rejects_approved_reject_decision_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_approved_reject_decision_atomically) ... ok
test_signed_sql_rejects_approved_review_missing_exact_refs_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_approved_review_missing_exact_refs_atomically) ... ok
test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_requires_task_and_execution_evidence_semantic_equality_atomically) ... ok
test_completion_insert_fails_closed_without_registered_semantic_udf (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completion_insert_fails_closed_without_registered_semantic_udf) ... ok
test_validate_and_integrity_reject_signed_evidence_semantic_mismatch (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_and_integrity_reject_signed_evidence_semantic_mismatch) ... ok
test_store_context_closes_connections_retained_by_udf_callbacks (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_store_context_closes_connections_retained_by_udf_callbacks) ... ok
test_completed_binding_snapshot_migration_precedes_old_immutability_trigger (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_completed_binding_snapshot_migration_precedes_old_immutability_trigger) ... ok
test_exact_eval_and_independent_review_allow_atomic_completion (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_exact_eval_and_independent_review_allow_atomic_completion) ... ok
test_validate_keeps_never_bound_and_nonpilot_completions_compatible (tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_validate_keeps_never_bound_and_nonpilot_completions_compatible) ... ok

----------------------------------------------------------------------
Ran 19 tests in 2.756s

OK
EXIT_CODE=0
```

All 19 passed. The adversarial helpers prove transaction rollback; the control tests
prove legitimate approved completion, never-bound completion, and non-pilot
completion remain compatible.

## 2026-08-04 — Final-review source target and verification

```text
commit  4cde805d6ece9e0b7532b4620007036a3c8f9217
tree    dc09a890df65cb64b31ac848c241e75572035ff0
parent  3cb5b4ad354e59359f76d9958d971b664f189429
subject fix: close task 148 completion assurance gaps
branch  main (ahead of origin/main by 1; no push performed)
patch SHA-256 from parent fd31ca83fbae1a164ccb81e8b6afe0b9721eb98b4d0128364a3bedbf57ac698d
```

Focused Phase C/runtime command and exact result:

```text
python3.11 -m unittest tests.test_completion_assurance_gate tests.test_pilot_gate tests.test_task_execution_continuity tests.test_runner tests.test_context_compiler tests.test_assurance_kernel tests.test_trusted_evaluator tests.test_assurance_credentials tests.test_dashboard tests.test_event_engine -q

----------------------------------------------------------------------
Ran 186 tests in 20.421s

OK
EXIT_CODE=0
```

Canonical Agent Company command and exact result:

```text
python3.11 -m unittest discover -s tests -q

----------------------------------------------------------------------
Ran 345 tests in 50.303s

OK
EXIT_CODE=0
```

Phase D unit tests inspected existing controls. No D0, D1, D2, treatment, protocol,
or Phase D runner command was invoked.

PixWeave read-only verification:

```text
branch  main (clean and aligned with origin/main)
commit  d78094f26eb697c810899a40771a8af6dec7ce19
tree    6f2d526d912fcf283937cd265d298004a31c00b2
python3.11 -m unittest discover -s tests -v

----------------------------------------------------------------------
Ran 58 tests in 0.307s

OK
```

Validation, compilation, and diff checks:

```text
python3.11 -m agent_company.cli validate
{"errors": [], "ok": true}

rg --files -0 agent_company tests -g '*.py' | xargs -0 python3.11 -m py_compile
exit 0, no output

python3.11 -m compileall -q agent_company tests
exit 0, no output

git diff --check
exit 0, no output
```

Bandit 1.9.4 final summaries:

```text
Agent Company: 0 High, 6 Medium, 26 Low, 12625 lines; no scan errors
PixWeave:      0 High, 0 Medium, 0 Low, 3218 lines; no scan errors
```

Protected-state and service results:

```text
docs/assurance/phase-d + evidence/phase-d: 611 files
387d8bd7c7f774e7a7ee059943de864a49a759d57ceba3432d7520e772cb065f

data: 120 files
9e431ac06cbc41fb690b1b29fa0d476363627746af0f726b5a6d1105464a1664

data/company.sqlite3
dc4639df347b1c76178d8bd51e283e9032deef06668a0804082710a6fa0dbb48

git diff 3cb5b4ad354e59359f76d9958d971b664f189429 --name-only -- docs/assurance/phase-d evidence/phase-d data credentials approvals
exit 0, no output

PixWeave status
## main...origin/main

all six checked Agent Company systemd units
inactive
```

No protected Phase D evidence, PixWeave source, data, credentials, approvals, or
external system was modified. Services remain stopped; none was started. Independent
review of commit `4cde805d6ece9e0b7532b4620007036a3c8f9217` and tree
`dc09a890df65cb64b31ac848c241e75572035ff0` remains pending. Nothing in this log
constitutes approval or independent acceptance.
