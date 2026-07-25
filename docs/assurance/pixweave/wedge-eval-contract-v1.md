# PixWeave Wedge Eval Contract v1

## Goal

Enable a small-brand content operator to transform one product source image into a brand-conforming social asset that is ready for internal review in one bounded attempt.

## Non-Goals

- No PixWeave implementation change.
- No customer data, paid competitor access, publication, or superiority claim.
- No production release decision.

## Scenario Bank

Each synthetic scenario fixes:

- a generated product/source-image reference;
- brand palette, typography class, logo-clearance, tone, and prohibited-content constraints;
- target channel and aspect ratio;
- required message hierarchy;
- ambiguity or adversarial condition;
- deterministic seed and expected hard-gate observations.

Partitions are development, regression, protected holdout, and adversarial. The implementer cannot access protected cases.

## Hard Gates

1. Subject identity and important product geometry remain recognizable.
2. Explicit brand constraints are satisfied.
3. No unrequested personal, regulated, deceptive, or unsafe content appears.
4. Output is complete and reviewable rather than visibly broken or placeholder content.

Any hard-gate failure loses the comparison regardless of weighted quality.

## Anchored Quality Rubric

Rate each surviving output from 1 to 5:

- **Brand consistency:** 1 contradicts the brief; 3 broadly follows it with visible drift; 5 is immediately recognizable and precise.
- **Subject fidelity:** 1 changes identity/function; 3 preserves identity with artifacts; 5 preserves defining details cleanly.
- **Message hierarchy:** 1 unreadable/confused; 3 understandable after inspection; 5 communicates primary and secondary messages immediately.
- **Visual composition:** 1 broken/imbalanced; 3 usable with edits; 5 polished and channel-appropriate.
- **One-pass usability:** 1 must restart; 3 requires moderate edits; 5 can enter internal review unchanged.

## Blind Pairwise Protocol

- Randomize candidate/comparator left-right placement.
- Remove product/system identifiers from rating payloads.
- Use two independent ratings per source-scenario and adjudicate disagreement.
- Retain ties, failures, abstentions, protocol violations, and abandoned attempts.
- Give candidate and comparator equal generation and retry budgets.

## Decision Rule

Primary estimand is source-scenario-level blind preference win rate. Use source-clustered bootstrap uncertainty. A positive pilot signal requires the lower confidence bound to exceed the preregistered 5 percentage-point practical advantage and every hard gate/protected dimension to be non-inferior. This design is not authorization to make a public competitive claim.

## Human Calibration

Before relying on a model grader, two qualified internal principals independently rate a calibration subset. Record agreement, systematic disagreement, rubric ambiguity, and adjudication. Drift or unresolved material disagreement blocks use of the grader.
