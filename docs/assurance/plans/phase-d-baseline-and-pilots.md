# Phase D Baseline And Pilot Design Notes

## Baseline worksheet

| Metric | D1 Product | D2 Control Plane | Collection rule |
|---|---|---|---|
| Valid cases | >=6 | >=12 | Count frozen valid cases; report shortfall |
| Hard-gate failure | each case | each seeded fault | Binary; no weighted offset |
| Quality/preference | blind pairwise 1-5 | decision confidence | Two qualified raters; retain ties |
| Defect escape | post-review defects | false passes/unauthorized transitions | Severity-weighted and raw counts |
| Rework | cycles and minutes | remediation cycles | Timestamped evidence only |
| Cycle time | start-to-review | gate p50/p90 | Separate machine and human waits |
| Cost | tokens/human minutes | tokens/human minutes | Missing values are `not_collected` |
| Reviewer load | rating/adjudication minutes | review/incident minutes | Principal-scoped aggregate only |
| Evidence | lineage completeness | lineage completeness | Hash and reference validation |

## Frozen D1 execution outline

1. Hash and lock scenario bank, product profile, eval contract, comparator version, rubric, and holdout manifest reference.
2. Generate candidate and comparator artifacts with equal budgets and randomized presentation order.
3. Evaluator alone accesses protected holdout; implementers receive only refs and hashes.
4. Two raters score blinded pairs; record hard-gate failures separately from weighted scores.
5. Adjudicate disagreement without changing original ratings.
6. Compute source-scenario clustered bootstrap; do not report a superiority claim.

## Frozen D2 execution outline

1. Hash and lock the control fault bank, expected outcomes, and current-workflow replay procedure.
2. Execute baseline replay without Phase D treatment and record observed escapes, remediation, and timing.
3. Execute treatment with Phase C gates enabled only for the explicitly bound pilot.
4. Inject each fault once in a controlled copy and retain mutation, denial, rollback, and audit evidence.
5. Re-run ordinary never-bound and nonpilot controls to prove compatibility.
6. Independently review all results before the Chairman decision.

## Baseline report template

- Scope, manifests, hashes, dates, and personnel
- Valid-case inventory and exclusions
- Baseline metric table with numerator/denominator
- Missing data and limitations
- Seeded fault outcomes
- Cost and cycle-time distribution
- Reviewer/model agreement
- Pre-registered treatment comparison plan
- Open decisions before D1/D2 execution
