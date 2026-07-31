# Phase D Redesign V4 Verification

## Review disposition

Commit `3ab23d1630457f63aa310a9a206f5429493ed659` was the candidate independently
reviewed. The review found 2 Critical, 6 High, and 2 Medium issues. The candidate remains rejected
and is not a verified Phase D treatment design or execution authorization.

Commit `153b11f2faf4939adc0e66dc51e6602534efd745` was subsequently created and pushed
as the fixed `main` baseline for this final independent review. The findings from that review are
addressed in the current working tree pending final verification and a later independent review.

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
  redesign evidence, old aggregate regression evidence, and all 23 preserved V4 protocol
  reproduction artifacts, including candidate-path and before/after aggregate collateral. Positive
  reproduction claims remain machine-readably invalid. The record binds the exact supplied V4
  freeze path and bytes plus its
  schema/id, reviewed baseline commit/tree, and supersession protocol input. V4 freeze verification
  validates this record, and the consumer guard rejects every denied file or tree.
- Runtime governance verification exposes no signer and loads no HMAC secret. Authorization is
  unavailable until verification and signing are separated outside this runtime.
- External trust-path access is disabled pending a descriptor-anchored implementation or a
  separate verifier service, so the former parent-path TOCTOU surface is not reachable.
- Mutable-checkout static inspection binds the supplied root exactly to the canonical primary Git
  worktree top-level and rejects descendant roots, symlink aliases, linked worktrees,
  common-directory ambiguity, and root identity drift. It walks the complete checkout through
  directory descriptors, excluding only
  `.git` internals. Every filesystem path must be represented by the reviewed tracked tree, so
  empty directories, ignored/untracked files, symlinks, FIFOs, sockets, devices, and all other
  special objects fail closed. Tracked regular files, symlinks, and directories use identity checks
  before and after descriptor-relative open/read operations to reject replacement races, alongside
  exact hash-verified raw commit/tree bytes and modes. Every immutable commit/tree object read is
  bracketed by replacement-ref and bound metadata snapshots. Git object reads disable replacement
  semantics; transient or persistent replacement refs, semantic Git environment overrides,
  alternates, grafts, shallow metadata, and object-directory indirection fail closed immediately
  before or after each read and again at the final inspection boundary. These checks can reject
  drift observed while they run, but no finite sequence proves a concurrently mutable checkout
  remains unchanged after the last check. Therefore even a clean mutable clone terminates with
  `blocked_unavailable_atomic_snapshot` and never returns `scope: entire_git_tree` acceptance
  evidence. Candidate success remains unavailable unless an atomic read-only filesystem snapshot
  or OS-enforced immutability primitive is implemented.
- The exact V4 freeze path is descriptor-opened through descriptor-bound parent directories,
  rejects symlinks and hardlinks, binds exact bytes and file identity, and remains held and rechecked
  through the public verifier's final return boundary.
- SVG validation requires strict UTF-8 and rejects encoded declarations, DOCTYPE, XML processing
  instructions, entity declarations, and entity references before ElementTree parsing. Every
  user-authored scenario label, message, feature, brief, and constraint is recursively rejected for
  XML declaration/PI/DOCTYPE/entity/raw-markup syntax before renderer or parser use.

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

The separate development Git-object diagnostic is scoped to Git objects only, not the worktree.
It returns `development_only: true`, `verified: false`, `candidate_evidence: false`, and
`authorization_eligible: false`; it has no `scope: entire_git_tree` field and cannot authorize any
candidate, treatment, threshold, D0/D1/D2 action, or C2 state transition.

`review-3ab23d1-strict-tdd.txt` preserves the 12-test RED run with 21 failures/subtest failures and
the first 48-test GREEN focused run for these review findings.

`review-d0-supersession-strict-tdd.txt` preserves this review's genuine RED run (6 tests, 4
failures and 3 errors) and the subsequent 6-test GREEN run.

`review-medium-findings-strict-tdd.txt` preserves the supplied Medium-finding RED run (5 test
methods with 14 failures), its original GREEN record, and the later independent-review High
follow-up RED/GREEN cycle that supersedes the former inert ignored-content exception.

`review-153b11f-final-findings-strict-tdd.txt` preserves this remediation's 7-test initial RED run
with 12 failures and 1 error, its initial GREEN result, the prior independent review's 2-test RED
follow-up, its 9-test GREEN result, and this latest independent-review follow-up's strict TDD
history. This current follow-up remains pending until a post-fix independent review; no post-fix
review outcome is claimed.

The current final-review remediation remains pending until a post-fix independent review. Exact
RED/GREEN and final verification evidence is appended to
`review-153b11f-final-findings-strict-tdd.txt`; no post-fix review outcome is claimed here.

Current exact-code checks passed: 98 focused Phase D tests, 318 Agent Company tests, 58 PixWeave
tests,
repository-wide Python compilation, `git diff --check`, and live read-only validation with
`{"ok": true, "errors": []}`. Bandit 1.9.4 found no High issues: the Agent Company broad scan
reported 6 Medium and 29 Low issues across 12,028 lines and exited 1, while PixWeave reported zero
issues across 3,218 lines and exited 0. The live `data/company.sqlite3` SHA-256 remained
`9372e062d53323cbc6d7fdc9f6283f8c375c18815c5a9d1efdf737f240997f70`. The 631-file protected
evidence inventory excluded only this report and the new final-findings review log; its aggregate
SHA-256 remained `85054d586c1f77f189e0b6f5ca6fcaccb3a7b2d4949e08844489db507fd953e0`.
Every inventoried file retained its pre-RED SHA-256, and PixWeave remained clean. HEAD remains the
already pushed fixed commit `153b11f2faf4939adc0e66dc51e6602534efd745`; the reviewed fixes are
present in the working tree for handoff. No protocol reproduction was run for this remediation.

No D0, D1, D2, treatment, or protocol execution script was run for this remediation. No service,
credential, or approval was created. No Treatment execution occurred.

No D1/D2 treatment result, execution authorization, threshold pass, candidate verification, or
approval is claimed by this report.

## Remaining blockers

1. A concrete internal real Company OS C2 replay implementation and non-injectable verifier do
   not exist.
2. A separate production signature verifier/signing service does not exist; runtime credential
   loading and governance authorization remain disabled.
3. No externally signed candidate manifest is available or verifiable.
4. No independently authenticated approval or later CEO start decision has been verified.
