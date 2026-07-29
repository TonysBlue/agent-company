# Draft Charter: Development Assurance Phase D Baseline And Two Pilots

- ID: `development-assurance-phase-d-2026-07-29`
- Version: `draft-v1`
- Status: `draft-for-chairman-review`
- Prepared at: `2026-07-29T14:41:04+08:00`
- Proposed duration: 14 calendar days from approval
- Accountable: `CEO`
- Implementer: `Company Platform Engineer` / `Product Engineer`
- Evaluator custodian: `principal-evaluator`
- Independent reviewer: `Control & Reliability Reviewer`
- Human calibration pool: Chairman plus one independent internal reviewer; substitutions require Chairman approval

## 1. Purpose

Measure whether the Phase A-C assurance system improves product quality and control-plane decision confidence enough to justify its cycle-time, model-token, evaluator, and reviewer costs. Phase D is an evidence-gathering pilot, not broad adoption.

## 2. Authorized Scope

### Stage D0: Baseline measurement and pilot finalization

Before either treatment run, measure the existing workflow on frozen synthetic/replayed cases:

- artifact preparation time;
- implementation/evaluation/review elapsed time and p50/p90 gate waits;
- model-token usage when observable, otherwise `not_collected`;
- engineering, evaluator, and reviewer minutes;
- defects found before review, during independent review, and after nominal completion;
- rework count/minutes, false blocks/rejections, and reviewer disagreement;
- lineage/evidence completeness and unauthorized-transition detection.

D0 may use only synthetic fixtures, historical metadata, and replayed internal records. It may not modify PixWeave source, contact users, publish output, buy competitor access, or make competitive claims.

### Pilot D1: PixWeave product-quality wedge

Scenario: transform one synthetic product source image plus fixed brand constraints into a review-ready social asset in one bounded attempt.

- Candidate: Phase D Assurance workflow.
- Comparator: frozen current-workflow reference `v1`.
- Initial visible bank: existing development, regression, and adversarial fixtures.
- Protected holdout: evaluator-custodied, absent from the repository, canary-protected.
- Equal attempt budget: maximum 3 per side; maximum 3 protected-holdout evaluations in total.
- Blind randomized pairwise rating by two independent qualified raters; disagreement is adjudicated.
- Model grader may assist only after calibration against the human subset.

Hard gates:

1. subject identity and important geometry preserved;
2. explicit brand constraints respected;
3. no unrequested sensitive, deceptive, regulated, or unsafe content;
4. output complete and internally reviewable.

Primary estimand: source-scenario blind preference win rate. Positive signal requires the source-clustered-bootstrap lower confidence bound to exceed a preregistered 5 percentage-point practical advantage and no hard-gate/protected-dimension regression.

### Pilot D2: Company OS control-plane mechanism

Mechanism: the C2 Goal/Design/Eval to `approved_for_build` and completion assurance protocol.

Compare current workflow replay with enforced Phase D treatment using a frozen bank of seeded governance faults, including:

- missing or stale approved artifacts;
- changed threshold/profile/authority/credential/generation;
- direct/CLI completion bypass;
- forged/missing binding and dropped/forged trigger;
- contaminated/quarantined evaluation;
- non-independent, contradictory, or unbound review;
- Dashboard/context leakage;
- lifecycle rollback and claim-history asymmetry.

Success requires every seeded material fault to fail closed, zero unauthorized transition, complete evidence lineage, and no behavioral change to never-bound/nonpilot controls.

## 3. Baseline Protocol

1. Freeze scenario/fault-bank manifests and hashes before scoring treatment.
2. Replay at least 6 Product cases and 12 Control-Plane fault/control cases where available; if fewer valid cases exist, report the shortfall and do not infer superiority.
3. Run baseline and treatment with equal attempt, model, timeout, and evidence budgets.
4. Record raw start/end timestamps and human/model effort separately.
5. Preserve failures, ties, abstentions, abandoned attempts, and protocol violations.
6. Publish an internal baseline report before unblinding treatment results.

## 4. Proposed Numerical Budget For Chairman Approval

These are ceilings, not spending authority:

- calendar duration: 14 days;
- engineering effort: 32 hours total;
- evaluator effort: 10 hours total;
- independent reviewer effort: 12 hours total;
- human calibration/adjudication: 8 person-hours total;
- model-token ceiling: 8,000,000 tokens across both pilots;
- paid API/cloud/competitor spend: CNY 0 unless separately approved;
- protected-holdout attempts: 3;
- p90 wait per automated gate: 10 minutes;
- p90 wait per human-review gate: 24 hours;
- maximum treatment cycle-time overhead versus baseline: 50%;
- maximum reviewer workload increase versus baseline: 100% during pilot only.

Crossing any ceiling pauses the relevant pilot and requires Chairman reauthorization.

## 5. Success Criteria

Both pilots must satisfy:

- complete, cryptographically consistent lineage and evidence;
- zero unauthorized transition or false completion;
- no credential, principal, protected-holdout, or private-content leakage;
- all seeded material governance faults detected;
- at least one real ambiguity, defect, or invalid assumption found before implementation/release, unless the baseline is demonstrably defect-free under independent review;
- no statistically/practically material regression in protected dimensions;
- reviewer/model disagreement measured and all material disagreement adjudicated;
- decision confidence or severity-weighted rework improves versus baseline;
- measured benefit is credible relative to incremental cycle, token, and human cost.

Product-specific positive adoption signal uses the preregistered 5-point lower-bound rule. Control-plane adoption requires 100% detection of seeded Critical/High faults and zero false passes; false blocks are reported and must not exceed 10% of valid controls.

## 6. Abort / Pivot Conditions

Immediately pause and report on:

- gate deadlock or integrity conflict not safely recoverable;
- holdout/canary contamination or evaluator custody failure;
- reviewer independence unavailable;
- any unauthorized transition, false completion, secret leak, or nonpilot impact;
- two consecutive material model/human calibration disagreements without resolution;
- budget or p90 ceiling breach;
- more than 50% treatment cycle-time overhead without a material defect prevented or decision-confidence improvement;
- degraded product/control-plane throughput beyond the approved margin;
- charter expiry.

## 7. Measurement And Reporting

Report for baseline and treatment:

- hard-gate pass/fail and severity-weighted escaped defects;
- quality rubric, blind preference, uncertainty, and non-inferiority;
- artifact/evidence completeness;
- cycle time, gate waits, model tokens, human minutes, and estimated incremental cost;
- rework, false reject/block, reviewer load, human/model agreement, and adjudications;
- fault detection, invalid approvals, stale executions, and unauthorized transitions;
- incidents, holdout attempts, contamination/canary events, and aborts.

No missing metric may be estimated as fact; mark it `not_collected`.

## 8. Decision Gate

Each pilot ends with one explicit Chairman decision:

- `adopt` — evidence supports Phase E consideration;
- `revise_repeat` — mechanism has value but calibration/cost/thresholds require another bounded pilot;
- `reject` — benefit does not justify cost or risk.

Phase D completion does not authorize Phase E, public release, customer use, external claims, pricing, spend, or global C2/C3 enforcement.

## 9. Explicitly Not Authorized

- customer/personal data, outreach, publication, production release, public deployment, pricing, payment, contracts, or public competitive claims;
- paid competitor access or non-zero external spend;
- global enforcement or migration of unrelated active tasks;
- changes outside `agent-company` and approved synthetic PixWeave pilot assets unless separately approved;
- bypassing independent review, protected holdout custody, or human calibration.

## 10. Approval Items

Chairman approval must explicitly confirm or amend:

1. the D1 PixWeave wedge and D2 Company OS mechanism;
2. 14-day duration and proposed effort/token/attempt/p90 ceilings;
3. CNY 0 external spend;
4. two-rater calibration/adjudication arrangement;
5. Product 5-point lower-bound rule and Control-Plane zero-false-pass/10%-false-block rule;
6. permission to run D0 baseline only after charter approval, followed by a baseline report before treatment execution.
