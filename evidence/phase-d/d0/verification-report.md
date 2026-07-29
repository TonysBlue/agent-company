# Phase D Stage D0 Verification Report

## Outcome

The approved D0-only baseline tooling replays 6 synthetic/replayed PixWeave cases and
16 Company OS fault/control cases from frozen manifests. It verifies input hashes,
executes each exact probe from a temporary detached copy at the comparator commit,
retains raw timestamps and hashed logs, and emits an internal report without executing
D1 or D2.

Baseline result: 22/22 hard gates passed, 16/16 seeded faults were detected, 0/6 valid
controls were falsely blocked, 0 unauthorized transitions were observed across 12
transition-sensitive cases, and lineage was complete for 22/22 cases. Unobserved
metrics remain `not_collected`.

## Strict TDD

Three RED stages and their exact failure modes are retained in
`strict-tdd-evidence.md`. The final focused D0 suite passes 7 tests covering frozen
hashes, case-bank validity, aggregation and missingness, report gates, detached replay,
and the repository inputs.

## Regressions

- Agent Company: `python3.11 -m unittest discover -s tests -v` passed 227 tests; output
  is `agent-company-regression.txt`.
- PixWeave: the clean `main` worktree at
  `d78094f26eb697c810899a40771a8af6dec7ce19` passed 58 tests in 0.374 seconds; output
  is `pixweave-regression.txt`.
- Repository checks: `git diff --check`, Python compilation, and JSON parsing passed.

## Scope And Gates

No PixWeave source was modified. No customer/personal data, external spend, outreach,
publication, protected holdout, production action, treatment execution, or external
action other than the required Git push was used. Independent D0 review has not been
self-issued. D1 and D2 remain blocked pending independent baseline review, Chairman
confirmation of the frozen comparison manifests and numerical ceilings, and the
relevant CEO-recorded start decision.
