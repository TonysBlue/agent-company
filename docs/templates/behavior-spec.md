# Behavior Specification: <name>

- ID: `<spec-id>`
- Version: `v1`
- Status: `draft | approved | superseded | rejected`
- Related goal: `<goal-id/version>`
- Related design: `<design-id/version>`
- Owner: `<role>`

## Observable Behavior

Describe externally observable behavior in domain language, independent of private implementation details.

## Inputs, Preconditions, And Authorization

Define accepted input, caller authority, initial state, data classification, and validation.

## Outputs And Postconditions

Define successful output, state changes, evidence, side effects, and guarantees.

## Scenarios

For each critical scenario use Given/When/Then and link to a test/eval ID:

```text
Given <initial conditions>
When <action/event>
Then <observable result>
And <required evidence/invariant>
```

Cover normal, boundary, invalid, partial-failure, recovery, concurrency, and adversarial scenarios.

## State Machine

Define legal states, transitions, actors, guards, idempotency, and terminal states.

## Invariants And Prohibited Behavior

List properties that always hold and behavior that must never occur.

## Interfaces And Compatibility

Define schemas, API/CLI behavior, backward compatibility, migration, deprecation, and versioning.

## Non-Functional Requirements

Define reliability, latency, cost, accessibility, privacy, security, observability, maintainability, and portability requirements relevant to this scope.

## Non-Goals

State behavior deliberately excluded.

## Traceability

Map each outcome and invariant to its Eval Contract grader or test.

## Approval Record

Record reviewer, authority, conditions, timestamp, and approved hash.
