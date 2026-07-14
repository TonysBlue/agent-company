# Operating Cadence

## Continuous operations

- CEO resumes from durable events. There is no periodic cron pulse; an idle worker
  blocks on local notification and does not invoke an LLM or manufacture activity.
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
- CEO 必须把“技术运行正常”和“业务实质推进”分别评估；调度存活不能替代客户、产品、收入或利润进展。
- CEO 维持最多两个关键任务：一个产品任务和一个商业任务。队列耗尽是有效状态，不得为了活跃自动生成阶段、实验或后续任务。
- 战略阶段必须持久化目标、成功指标、截止时间、依赖、证据要求和业务结果，并通过审计记录关联到有限任务集。
- 每次问题或失败后立即复盘根因、纠正措施和预防措施，将其机制化并验证，避免复发。
- Never create numbered or otherwise repetitive work merely to keep the queue non-empty. New work must advance a reviewed strategic phase, respond to evidence, or implement a decision.
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
