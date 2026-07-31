# Phase D Redesign V4 Verification

## Review disposition

Commit `3ab23d1630457f63aa310a9a206f5429493ed659` was the candidate independently
reviewed. The review found 2 Critical, 6 High, and 2 Medium issues. The candidate remains rejected
and is not a verified Phase D treatment design or execution authorization.

The older baseline `33bcb6371e18c08b05c49723282db24389b8bc6c` is historical context only. It is
not a fallback candidate verification target. Repository-default candidate verification is
explicitly blocked until an external signed candidate manifest and a separate production
signature verifier exist.

## Fail-closed state

- Treatment certification is unavailable until a concrete internal real Company OS C2 replay
  verifier exists. Caller-selected roots, injected verifiers, attestations, mocks, and status
  flips cannot create a pass.
- The legacy treatment runner and helper module are tombstones. They expose no D1/D2 runner,
  payload, subprocess, evidence-writing, or partial-threshold helper.
- The legacy D0 runner is also a permanent tombstone: it cannot parse execution arguments, invoke
  subprocesses, create directories, write evidence, or print a status. The D0 module retains only
  read-only historical parsing/validation/aggregation utilities; `run_case`, repository execution
  helpers, report rendering, and JSON writers are absent.
- D0 runtime source contains no positive D1/D2 start claim. Historical positive authorization,
  started-state, treatment, and threshold claims are explicitly invalid under V4; current status is
  superseded/blocked with `execution_authorized: false`.
- `supersession-record-v4.json` is an exact machine-readable denylist for the D0 input tree, start
  freeze and D1/D2 start contracts, V2/V3 redesign documents, legacy D0/D1/D2 evidence, prior
  redesign evidence, and old aggregate regression evidence. It binds the exact V4 freeze path,
  SHA-256, schema/id, reviewed baseline commit/tree, and supersession protocol input. V4 freeze
  verification validates this record, and the consumer guard rejects every denied file or tree.
- Runtime governance verification exposes no signer and loads no HMAC secret. Authorization is
  unavailable until verification and signing are separated outside this runtime.
- External trust-path access is disabled pending a descriptor-anchored implementation or a
  separate verifier service, so the former parent-path TOCTOU surface is not reachable.
- Immutable-tree checks compare tracked content with the target Git tree, reject index concealment
  flags, and detect ordinary untracked files. A strict candidate clone must contain zero ignored
  files or filesystem objects: SQLite and WAL/SHM/journal state, JSON, logs, PID and lock files,
  sockets, code, executables, symlinks, directories, and all other ignored objects fail closed.
  There is no inert ignored-content allowlist. Development-only diagnostics remain separately
  labeled unverified/non-candidate and claim no immutable candidate binding.
- SVG validation rejects raw DOCTYPE, XML processing instructions, declarations, and entity
  references before ElementTree parsing.

## Evidence status

All previously checked-in V4 test logs and protocol artifacts are preserved as historical
evidence. They do not verify candidate `3ab23d1`: they were bound to the older baseline or were
produced through development-overlay checks. Strict reproduction is blocked before output until
an externally signed candidate manifest can be verified.

Development-overlay output is diagnostics only. Its result and manifest are explicitly
`development_only_unverified_non_candidate`, `development_only: true`, `verified: false`, and
`candidate_evidence: false`. The verifier exposes no unbound-success argument or
`evidence_reproduced` return. `--verify --development-overlay` is rejected before dispatch,
evidence access, output creation, or result output.

`review-3ab23d1-strict-tdd.txt` preserves the 12-test RED run with 21 failures/subtest failures and
the first 48-test GREEN focused run for these review findings.

`review-d0-supersession-strict-tdd.txt` preserves this review's genuine RED run (6 tests, 4
failures and 3 errors) and the subsequent 6-test GREEN run.

`review-medium-findings-strict-tdd.txt` preserves the supplied Medium-finding RED run (5 test
methods with 14 failures), its original GREEN record, and the later independent-review High
follow-up RED/GREEN cycle that supersedes the former inert ignored-content exception.

Current local checks passed: 67 focused Phase D tests, 287 Agent Company tests, 58 PixWeave tests,
repository-wide Python compilation, `git diff --check`, and live read-only validation with
`{"ok": true, "errors": []}`. Bandit 1.9.4 found no High issues: the Agent Company broad scan
reported 6 Medium and 27 Low existing issues and exited 1, while PixWeave reported zero issues and
exited 0. The live `data/company.sqlite3` SHA-256 remained
`9372e062d53323cbc6d7fdc9f6283f8c375c18815c5a9d1efdf737f240997f70`. The 630-file preserved
evidence hash inventory excluded only this report and the Medium-finding review log; its aggregate
SHA-256 remained `a4298fa64a8faab8cc3f939be38f9c6cf9a16662c3b8ce417cd860d4abf3e460`.
Every inventoried file retained its pre-change SHA-256, and PixWeave remained clean.
No protocol reproduction was run for the independent-review High remediation.

No D0, D1, D2, treatment, or protocol execution script was run for this remediation. No service,
credential, approval, commit, or push was created.

No D1/D2 treatment result, execution authorization, threshold pass, candidate verification, or
approval is claimed by this report.

## Remaining blockers

1. A concrete internal real Company OS C2 replay implementation and non-injectable verifier do
   not exist.
2. A separate production signature verifier/signing service does not exist; runtime credential
   loading and governance authorization remain disabled.
3. No externally signed candidate manifest is available or verifiable.
4. No independently authenticated approval or later CEO start decision has been verified.
