# Eval Contract: <name>

- ID: `<eval-id>`
- Version: `v1`
- Status: `draft | approved | superseded`
- Profile: `product-competitive | control-plane-reliability`
- Related goal: `<goal-id/version>`
- Related design: `<design-id/version>`
- Related specification: `<spec-id/version>`
- Evaluation owner: `<role>`
- Independent reviewer: `<role>`

## Evaluation Question

State the decision this evaluation enables. Avoid generic questions such as "is quality good?"

## Baselines And Comparators

Pin product/system versions, commits, competitor version/date/configuration, execution environment, and repeatability requirements.

## Scenario Sets

For each set, define source, version, rights, inclusion criteria, size, sampling, visibility, and contamination protection:

- development;
- regression;
- hidden holdout;
- adversarial/fault;
- post-release failures.

## Hard Gates

List binary safety, correctness, governance, data, provenance, workflow completion, and critical regression checks. Any failure rejects the candidate.

## Graded Dimensions

For each dimension, define:

- user/control-plane meaning;
- rubric with decision-anchored levels;
- weight, if used;
- minimum dimension threshold;
- grader type;
- evidence source;
- known bias and calibration method.

## Graders

Define deterministic code/state graders, model graders, blind pairwise graders, human graders, and required consensus. Record model/prompt versions for model graders.

## Statistical Decision Rule

Define sample size, repetitions/seeds, tie handling, confidence interval or uncertainty rule, minimum practical advantage, aggregation, and missing-data behavior.

## Regression Policy

Identify capabilities that must remain near-perfect and what constitutes an allowed or prohibited regression.

## Cost And Performance Bounds

Define latency, operation count, token/inference cost, human effort, and resource limits where relevant.

## Human Calibration

Define gold examples, annotator qualifications, blind protocol, inter-rater agreement target, disagreement resolution, and recalibration cadence.

## Anti-Gaming Controls

Protect holdout visibility, evaluator independence, threshold immutability, randomized ordering, source blinding, and eval-code ownership.

## Release Rule

Write an executable decision expression combining hard gates, per-dimension thresholds, comparative result, regression policy, cost/performance bounds, and independent review.

## Failure Analysis Output

Specify required per-scenario evidence, transcripts/logs, grader rationale, uncertainty, and clustering of failure modes.

## Approval Record

Record approvers, conditions, timestamp, exact artifact hash, and baseline run required before implementation.
