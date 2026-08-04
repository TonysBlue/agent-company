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
