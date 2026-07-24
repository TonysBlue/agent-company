# Incident Record: <name>

- ID: `<incident-id>`
- Version: `v1`
- Initiative/release: `<references>`
- Incident commander principal: `<principal-id and role>`
- Declared at: `<ISO-8601>`
- Severity: `<level>`
- Status: `declared | containing | rollback_in_progress | rolled_back | disabled | resolved`

## Observed Harm Or Hard-Gate Breach

Record measured/observed facts, affected users/systems/data, scope, and uncertainty.

## Containment Authority

Record normal or break-glass authority, approver/quorum, scope, expiry, and notification requirements.

## Actions And Evidence

Record immutable ordered actions, commands/tool calls, before/after state, logs, artifacts, and actor identity. Do not include secrets.

## Rollback Or Disablement

Define target known-safe state, data recovery, verification, and current outcome. If an incident is contained without rollback/disablement, record whether the affected version resumes `enabled_or_deployed`, `outcome_observation`, or `closed`, with authority and evidence.

## Residual Risk And Monitoring

State remaining uncertainty, owner, alerts/SLOs, and stop conditions.

## Retrospective Assurance

Link the retrospective Goal/Design/Spec/Eval or Change Decision due within the governed deadline. Normal development remains blocked until independent closure.

## Closure

Record independent review, decision authority, evidence hashes, lessons converted to scenarios/regressions, and whether the initiative is reopened.
