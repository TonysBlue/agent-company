# Task 148 Final Issuance-Provenance Remediation

## Outcome

The remaining HIGH finding against clean tip
`1f67aec09dd22fcb140192328dd02c1e3c2b591c` is implemented and verified on exact
source/test commit `8f48f6cc947ad7aa7f91bc5660176b3bcaded4c0`, tree
`e4a8adcbb60e510d05bf1f58ab053f86f530af55`. The source and tests are committed,
`main` is aligned to that commit after local integration, no push was performed, and
independent review remains pending.

The shared `completion_assurance` verifier used by both runtime completion and the
SQLite completion-binding semantic UDF/trigger now requires durable issuance
provenance:

- `trusted_eval_contracts` signs the immutable initiative, attempt budget, and
  creation timestamp;
- official evaluator credential provision/rotation appends a signed, hash-chained
  issuance event with principal identity, credential hash, principal creation time,
  issuance time, sequence, and previous signature;
- each official run HMAC binds the exact evaluator actor/authority, credential hash,
  principal creation time, issuance-event signature, and contract signature that
  were valid when the run was issued;
- verification requires the contract before the run, the principal before issuance
  and the run, an exact run-bound issuance event, a valid complete rotation chain,
  and an active current principal whose current credential is the latest officially
  issued event;
- raw current-principal mutation, valid-HMAC fake issuance, contract mutation,
  post-dated principal provenance, and re-signed impossible chronology all fail
  closed through both SQL-trigger and runtime paths;
- official credential rotation preserves old run provenance, and legitimate
  multi-attempt variation and unbound behavior remain supported.

The migration is additive. New contract/run provenance columns are nullable for
legacy rows, and initialization does not synthesize signatures or credential
snapshots for unverifiable historical state. Consequently, an old unsigned active
completion remains unverifiable rather than being blessed during migration.

## Fixed Review Target

- Reviewed clean tip: `1f67aec09dd22fcb140192328dd02c1e3c2b591c`
- Reviewed clean tree: `1577b213f07c27733ceba1ccf37adf4e6ba58f28`
- Final source/test commit: `8f48f6cc947ad7aa7f91bc5660176b3bcaded4c0`
- Final source/test tree: `e4a8adcbb60e510d05bf1f58ab053f86f530af55`
- Subject: `fix: bind trusted eval issuance provenance`
- Patch SHA-256 from parent: `d5d8e3c4db67a6c1c2e59d6c8f11d0dd47cc80a587f4e85aabf375f020b39ef0`
- Current source/evidence status: source, tests, and current Task 148 evidence are
  committed; `main` is aligned to the final evidence tip; historical command captures
  retain their original status labels; no push performed.

Historical status text in preserved earlier Task 148 evidence and earlier sections
of `review-log.md` remains historical and is not a current-state claim.

## Strict TDD Evidence

Production code was unchanged at `1f67aec` when the first four RED attacks ran:

```text
python3.11 -m unittest \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_mutated_trusted_eval_contract_budget_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_mutated_trusted_eval_contract_creation_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_evaluator_principal_created_after_run_atomically \
tests.test_completion_assurance_gate.CompletionAssuranceGateTest.test_signed_sql_rejects_unrecorded_evaluator_credential_substitution_atomically -v
```

Exact summary: `Ran 4 tests in 0.553s`, `FAILED (failures=4)`, exit status `1`.
Every attack was accepted, proving the unsigned contract and mutable-current-principal
findings.

Two additional RED tests then proved that official signed contract issuance and safe
legacy migration did not exist: `create_contract` raised `AttributeError`, and the
expected additive integrity column was absent. Exact summary: `Ran 2 tests in
0.205s`, `FAILED (errors=2)`, exit status `1`.

The final focused GREEN command covered official issuance, unsigned and re-signed
contract mutation, principal chronology, raw credential and fake-provenance
substitution, official rotation, safe legacy migration, multi-attempt variation,
and legitimate completion. Exact result: 12 tests passed in 1.944 seconds, exit
status `0`. Direct-SQL denial uses the atomicity helper, which proves unchanged task,
execution, task binding, completion count, audit count, and event count.

## Final Verification

- Focused Phase C/runtime: 204 tests passed in 25.870 seconds.
- Canonical Agent Company discovery: 363 tests passed in 54.891 seconds, exit 0.
- PixWeave read-only suite: 58 tests passed in 0.350 seconds; repository remained
  clean and aligned with `origin/main` at commit
  `d78094f26eb697c810899a40771a8af6dec7ce19`, tree
  `6f2d526d912fcf283937cd265d298004a31c00b2`.
- Live read-only validation returned `{"errors": [], "ok": true}`.
- Fresh temporary initialization, assurance initialization, migration/integrity,
  validation, expanded schemas, and all 10 Trusted Eval triggers passed.
- `compileall`, `py_compile`, and `git diff --check` passed.
- Bandit 1.9.4: Agent Company 0 High, 6 Medium, 26 Low across 13,263 lines;
  PixWeave 0 High, 0 Medium, 0 Low across 3,218 lines; no scan errors.
- Phase D unit tests only inspected existing controls. No D0, D1, D2, treatment,
  protocol, or Phase D runner command was invoked.

## Protected State and Limits

- Phase D protected inventory: 611 files,
  `73e4ba77313ae9dd6862e92ca6cc402adabd3cdbd56ed89ddc49cd6b289a6903`.
- Data inventory: 120 files,
  `287e9a02085d1c24b3d0575ff93e70d6f54021eb15affdbf480bf93074daddae`.
- `data/company.sqlite3` SHA-256:
  `dc4639df347b1c76178d8bd51e283e9032deef06668a0804082710a6fa0dbb48`.
- Diff from reviewed tip across protected Phase D, data, credentials, and approvals:
  empty.
- Six checked Agent Company/PixWeave systemd units: all inactive; none started.

No protected Phase D evidence, PixWeave source, live data, credential, approval, or
external system was modified. No source/evidence was pushed. Nothing in this report
constitutes approval or independent acceptance.

## Source/Test Hashes

- `agent_company/assurance.py`: `826f71e78c7ee002e20e42afe95f6e9d083df72222d712984a9363a0b088951a`
- `agent_company/completion_verifier.py`: `16ff73e07ec387407157e4a54409f494079a61bb760704bd4951531c0ca5ec5f`
- `agent_company/trusted_evaluator.py`: `4c56dc4db3f139f57d055fb84c46b24462a5053235cdca5ee8712ae829ab662b`
- `tests/test_assurance_cli.py`: `9918a975fa1b9255a8910a3fbec5c28cd1167889c35c19a0051710918bff6666`
- `tests/test_completion_assurance_gate.py`: `e433054854112a694a5ac0a69f1cb7f8f174553da61163fdb3f8f82dfaca5244`
- `tests/test_trusted_evaluator.py`: `3a196a2725b3cf1bd805ce90eab3f4b305527c74706aeed969b161a1fe541ed2`
