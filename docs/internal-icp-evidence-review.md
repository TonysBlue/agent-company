# Internal ICP Evidence Review

Date: 2026-07-11  
Owner: Customer & Revenue
Scope: internal analysis only; no outreach, customer claim, pricing decision, or external publication

## Evidence boundary

The current ICP statement—small commercial teams and agencies producing recurring image variants with brand constraints—is an unvalidated hypothesis (`docs/gtm.md`). The ledger contains no completed customer interviews, pilots, revenue, or willingness-to-pay results. Experiment 1 is still `draft` with no result. Consequently, repository artifacts can establish only that PixWeave's local workflow implements or specifies certain controls; they cannot establish demand.

Recorded product evidence relevant to the positioning hypothesis:

- The deterministic campaign example expands a controlled campaign matrix into 16 uniquely addressable variants; regression tests verify stable checksums and zero accepted identity collisions.
- Brand-kit validation covers versioned palette, typography, logo-placement, and forbidden-element constraints.
- Local artifacts and campaign manifests use atomic JSON replacement, reducing the risk that reviewers mistake a partial write for a complete artifact.
- Publishing, pricing, customer-data, financial, legal, production, and other reserved actions remain human-controlled.

These facts support testing a "controlled and reviewable campaign variation" proposition. They do not prove that either target segment values it, that the workflow saves time, or that generated visual output meets commercial quality requirements.

## Ranked evidence gaps

1. **Workflow pain and frequency by segment — highest priority.** No recorded customer evidence shows how often small commercial teams or agencies create multi-format variants, where rework occurs, or whether traceability is a material pain. Without this, the ICP and initial wedge are unverified.
2. **Outcome value versus generic generation speed.** There is no evidence that controllability/repeatability produces a better operator outcome than speed, output quality, or cost. The existing experiment hypothesis asserts this comparison but has no rubric definition or observations.
3. **End-to-end visual acceptance.** Current tests validate metadata, identity, input constraints, determinism, and write integrity—not perceived image quality, edit fidelity, operator time, or approval rate. Positioning must not imply demonstrated commercial visual performance.

## Internal-only experiment definition

Use only existing repository materials to test whether positioning claims are supported by currently reviewable evidence; do not contact prospects or publish results.

Compare three messages:

- A: controlled, repeatable campaign variations;
- B: faster generic image generation;
- C: reviewable artifact history and human-controlled publishing.

Score each message against the same four evidence dimensions, each on `0–2`:

1. runnable implementation (`0` absent, `1` specified, `2` exercised by a passing test);
2. workflow coverage (`0` absent, `1` partial, `2` end-to-end for the stated claim);
3. real-user evidence (`0` none, `1` qualitative evidence, `2` measured repeated use);
4. commercial-outcome evidence (`0` none, `1` proxy outcome, `2` measured customer outcome).

Primary metric: `positioning_evidence_support_rate = points_awarded / 8` for each message. Owner: Customer & Revenue. Cadence: once per material product-evidence change and before any request to externally test positioning. Data sources: Git-tracked requirements, runnable CLI behavior, passing regression output, and approved anonymized customer evidence when such evidence exists.

Guardrail: a message is not eligible for an external positioning-test approval request unless every awarded point links to reviewable evidence and the wording explicitly distinguishes implemented capability from unvalidated customer value. The internal test does not authorize outreach.

## Current conclusion

Message A is the best-supported internal hypothesis because campaign expansion, identity integrity, and brand constraints are runnable and tested. Message C has partial support from artifact records and governance controls. Message B has no recorded speed benchmark. None has real-user or commercial-outcome evidence, so no winning market message can yet be declared.
