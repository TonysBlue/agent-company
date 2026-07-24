# Design: <name>

- ID: `<stable-id>`
- Type: `initiative-design | capability-design | architecture-decision | evaluation-design | release-design`
- Version: `v1`
- Status: `draft | approved | superseded | rejected`
- Profile: `product-competitive | control-plane-reliability`
- Owner: `<role>`
- Decision authority: `<CEO | Chairman>`
- Reviewers: `<roles>`
- Related goal: `<goal-id>`
- Related repository: `<repository-id>`
- Supersedes: `<design-id/version | none>`
- Last updated: `<ISO-8601>`

## Decision Summary

State the proposed decision and intended outcome in a short paragraph.

## Problem And Evidence

Describe the current problem, observed failures, affected stakeholders, evidence sources, and confidence. Label claims as `measured`, `observed`, `estimated`, `target`, or `unknown`.

## Target Users Or Protected Stakeholders

Identify who benefits, who may be harmed, and whose workflow or control boundary changes.

## Desired Outcomes

Define observable outcomes independent of the implementation. Link each outcome to its future evaluation method.

## Non-Goals

State what this design does not attempt to solve.

## Current State And Baseline

Describe current behavior, relevant versions, known performance, limitations, and the reproducible baseline procedure.

## Proposed Design

Explain system boundaries, components, responsibilities, information flow, authority flow, and why this is the smallest adequate solution.

## Alternatives Considered

For each alternative, state benefits, drawbacks, evidence, and rejection reason. Include the option to make no change.

## Interfaces And Data Model

Define public interfaces, schemas, persistent records, identifiers, versioning, transaction boundaries, and compatibility requirements.

## State Transitions And Invariants

Define legal states/transitions and properties that must always hold. Mark safety and governance invariants as hard gates.

## Failure, Threat, And Recovery Model

Cover partial failure, concurrency, restart, stale actors, malformed input, authorization, data leakage, external dependencies, resource exhaustion, rollback, and safe stop.

## Evaluation Strategy

Link the Eval Contract and describe deterministic graders, model graders, human calibration, scenario sets, holdout protection, thresholds, baselines, non-regression checks, and statistical decision rules.

## Rollout, Rollback, And Stop Conditions

Define internal enablement, controlled rollout, observability, rollback trigger, stop-loss rule, and required authority.

## Risks, Assumptions, And Unknowns

Separate verified facts from assumptions and unknowns. Assign an owner and resolution plan to each material item.

## Open Decisions

List unresolved choices. A material unresolved choice blocks approval.

## Evidence References

Use immutable artifact IDs, repository commits, checksums, timestamps, and source links. Do not embed secrets or sensitive raw data.

## Approval Record

Record reviewer decisions, conditions, residual risk, decision authority, timestamp, and the exact approved document hash.
