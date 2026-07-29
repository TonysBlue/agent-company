# Phase D D1/D2 Corrected Redesign Verification

## Decision boundary

Independent findings were recorded against HEAD `6626411` in
`docs/assurance/phase-d/redesign/independent-findings-at-6626411-v1.json`. The existing
D1/D2 treatment conclusions remain unchanged on disk, but are explicitly marked invalid and
superseded by `supersession-record-v1.json`. The CEO start-decision proposal is explicit:
`do_not_start` until an independent reviewer approves the corrected freeze and binds every
document hash. The corrected freeze therefore reports
`blocked_pending_independent_approval`; no corrected treatment is authorized.

## Corrected D1

- Six distinct synthetic product sources are frozen with hashes and at least three
  recognizable features each: lotion, coffee pouch, headphones, running shoe, smartwatch and
  citrus candle.
- Candidate and comparator run specs are byte-identical for source, brief, messages, attempts,
  model/tool, timeout and evidence budgets. Their only difference is `assurance_workflow`.
- The renderer enforces a 512x512 canvas, safe-area and message line/length ceilings and fails
  closed on overflow.
- Each dry-run delivery bundle contains only `brief.json`, `option-A.svg`, `option-B.svg` and
  `rater-form.json`, plus a hash manifest. The custody mapping is outside the delivery root and
  no delivery manifest contains candidate/comparator, mapping, custody or generated paths.
- The form has four hard gates per option, five dimensions anchored at 1/3/5, A/B/tie/abstain,
  confidence, rationale, elapsed minutes and protocol-violation fields.

## Corrected D2

- The harness copies a frozen synthetic database/repository fixture into separate baseline and
  treatment temporary copies for every canary.
- It applies a real SQL or repository mutation, records the observed allow/deny mechanism,
  restores from a pre-mutation copy, and retains before snapshot, mutation, observation,
  rollback, after snapshot, audit/event evidence and noninterference evidence for each side.
- Dry-run canaries are `d2m-001-direct-completion`, `d2m-005-frozen-contract-rewrite` and
  `d2m-009-valid-running-control`. The fixture is unchanged after both sides complete.
- Comparison requirements are derived from observed baseline escapes and allowed controls under
  `threshold_source: paired_baseline_observations`; no asserted numerical success constants are
  used.

## Strict TDD and regressions

- RED evidence: `strict-tdd-red-cycle.txt` records the initial missing-module failure.
- GREEN evidence: `strict-tdd-redesign.txt` reports 23 focused Phase D tests passing, including
  the corrected freeze, D1, D2 and dry-run tests plus legacy Phase D regression tests.
- Agent Company full regression: `agent-company-regression-rerun.txt`, 243 tests passed. The
  first run had one timing-sensitive concurrency miss; the immediate rerun passed.
- PixWeave full regression: `pixweave-regression.txt`, 58 tests passed. No PixWeave source file
  was modified.

## Scope and non-actions

The dry run generated synthetic internal evidence only. Corrected D1/D2 treatments, human rater
delivery/outreach, customer data, external spend, publication, production action and PixWeave
source edits did not occur. The only permitted external action remains a git push.
