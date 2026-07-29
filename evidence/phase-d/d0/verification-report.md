# Phase D Stage D0 Verification Report

## Outcome

The corrected D0 baseline tooling replays 6 synthetic/replayed PixWeave cases and
16 Company OS fault/control cases from frozen manifests. It verifies input hashes,
executes each exact probe from a temporary detached copy at the comparator commit,
retains raw timestamps and hashed logs, and emits an internal report without executing
D1 or D2.

Baseline result: 22/22 hard gates passed, 16/16 seeded faults were detected, 0/6 valid
controls were falsely blocked, 0 unauthorized transitions were observed across 12
transition-sensitive cases, and lineage was complete for 22/22 cases. Unobserved
metrics remain `not_collected`.

The governance freeze now predates the regenerated run and hash-binds the approved
charter, independent D0 review, and Chairman confirmation. Host observations are
explicitly labeled host-local.

## Strict TDD

Five RED stages and their exact failure modes are retained in
`strict-tdd-evidence.md`. The final focused Phase D suite passes 14 tests covering
chronology, charter/start-contract hashes, comparator commits and regression counts,
case-bank validity, aggregation, blinded assignment, equal D1 recipes, D2 thresholds,
report gates, and detached replay.

## Regressions

- Frozen Agent Company comparator: `python3.11 -m unittest discover -s tests -v`
  passed the expected 220 tests at `8a50770`; output is
  `agent-company-comparator-regression.txt`.
- Final Agent Company worktree: the same command passed 234 tests; output is
  `../full-agent-company-regression.txt`.
- PixWeave: the clean `main` worktree at
  `d78094f26eb697c810899a40771a8af6dec7ce19` passed 58 tests; final output is
  `../full-pixweave-regression.txt`.
- Repository checks: `git diff --check`, Python compilation, and JSON parsing passed.

## Scope And Gates

No PixWeave source was modified. No customer/personal data, external spend, outreach,
publication, protected holdout, production action, or external action other than the
required Git push was used. Independent D0 review is recorded as approval conditioned
on the now-completed chronology correction. Chairman confirmation binds all five
approval items and the numerical ceilings. D1 and D2 have immutable bounded start
contracts; D1 remains awaiting two human ratings before any adoption result.
