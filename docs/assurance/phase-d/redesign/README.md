# Phase D Corrected D1/D2 Redesign

This directory is the corrected design lineage prepared from independent findings at HEAD
`6626411`. The prior D1/D2 treatment evidence under `evidence/phase-d/d1` and
`evidence/phase-d/d2` is preserved unchanged and marked invalid/superseded by
`supersession-record-v1.json`.

## Approval gate

`corrected-freeze-v2.json` binds the independent findings, CEO start-decision proposal, D1/D2
contracts, scenario bank and mutation bank by SHA-256. It is deliberately
`blocked_pending_independent_approval`. A separate reviewer must create
`independent-approval-v2.json` with a non-author principal, bind the freeze and every document
hash, and resolve all Critical/High findings before corrected treatment execution can be
authorized. The CEO proposal is `do_not_start` until that approval exists.

## Allowed validation

Tooling, fixtures, RED tests, dry-run validation and regressions are allowed before approval:

```text
python3.11 scripts/run_phase_d_redesign_dry_run.py
python3.11 -m unittest tests.test_phase_d_redesign tests.test_phase_d_treatments tests.test_phase_d_d0 -v
```

The dry-run writes only synthetic evidence under `evidence/phase-d/redesign`, emits six D1
delivery bundles and three D2 mutation canaries, and records
`corrected_treatments_executed: false`. It never reads customer data, mutates a live database or
worktree, edits PixWeave source, sends outreach, spends money, publishes or performs production
actions.
