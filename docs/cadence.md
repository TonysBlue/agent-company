# Operating Cadence

## Continuous operations

- CEO wakes every 10 minutes and resumes from persistent company state.
- Before taking new work, CEO inspects all `in_progress` task executions and records lease health.
- Active executors must heartbeat and checkpoint durable state with the task execution CLI/API.
- Stale leases are recovered without killing local processes; PID liveness is advisory and only trusted when the stored process start identity still matches.
- Retry recovery is bounded by `max_attempts`; exhausted executions are blocked/escalated for review instead of being requeued indefinitely.
- Process the highest-value safe work; escalate reserved actions immediately.
- Identify human dependencies 72 hours and 7 days before they block delivery.
- Record real evidence, metrics, and audit entries. Plans and placeholders are not results.
- Record observed token usage into the audited `token_usage` ledger only from real measurements; never fabricate zeroes or inferred totals.
- Use guarded asynchronous Codex execution for bounded, reviewable work when useful, across any function.
- Codex registration is a durable backend/session reference only. Core operating code must not launch Codex; external runners own their own process lifecycle.
- Keep work in progress small and aligned to the critical path.
- Never mark execution recovery, checkpoint, heartbeat, or retry as completion. Completion still requires existing reviewable evidence attached through `task-complete`.
- Never create numbered or otherwise repetitive work merely to keep the queue non-empty. An exhausted reviewed backlog is valid; replenish it only from new evidence, a decision, or a reviewed roadmap change.
- Cancel obsolete or duplicate work through the audited task-cancel workflow; never mark it complete or reuse another task's evidence.

## Chairman reporting

- 08:00 morning operating brief.
- 13:00 midday progress and decision brief.
- 20:00 evening results, risks, and next-priority brief.
- Decision requests, human blockers, severe incidents, and material milestones are reported immediately rather than waiting for routine briefs.

## Weekly review

- KPI and evidence review.
- Customer pipeline and experiment review.
- Product and engineering critical-path review.
- Risk register and human-dependency forecast.
- Roadmap reprioritization.

## Monthly review

- Strategy, budget, pricing, legal readiness, and external launch readiness, all under Chairman control.
