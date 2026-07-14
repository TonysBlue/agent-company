# Controlled Beta Issue SLA and Satisfaction Loop

Status: internal operating policy; no external outreach authorized
Owner: CEO
Scope: Chairman-approved controlled Beta sessions only

## Classification and ownership

| Severity | Definition | Accountable owner | Acknowledge | Triage / containment | Resolution target |
|---|---|---|---|---|---|
| Critical | Privacy, consent, image-rights, unauthorized exposure, unrecoverable evidence loss, or materially misleading output | CEO incident lead; Legal/Compliance Specialist and Product Engineer consulted | 1 hour | Pause sessions immediately; contain within 4 hours | Chairman-approved recovery before sessions resume |
| High | Task blocked, lineage corrupted, or repeatably unusable output without safe workaround | Product Engineer | 1 business day | 1 business day | 3 business days or documented mitigation |
| Medium | Material friction or quality loss with a safe workaround | Product owner | 3 business days | 3 business days | 10 business days or planned milestone |
| Low | Minor usability issue or enhancement | Product owner | 5 business days | 5 business days | Backlog decision within 20 business days |

Targets are internal service objectives, not claims of achieved performance. The issue register records session ID, artifact ID, reporter consent scope, severity, owner, timestamps, status, workaround, evidence links, and satisfaction follow-up. Missing values remain `not_collected`.

## Workflow

1. Capture structured feedback and bind it to the session/artifact without placing sensitive raw text in Git.
2. CEO validates severity and assigns one accountable owner; critical reports pause new sessions.
3. Owner acknowledges, reproduces where safe, records containment and links the corrective task.
4. Before closure, an independent reviewer verifies the fix or mitigation against retained evidence.
5. Close only after recording root cause, corrective action, verification, and any remaining risk. Reopen on recurrence.

## Review and recurrence prevention

Critical and high issues require a written review within five business days of containment. It records timeline, impact, contributing controls, root cause, corrective/preventive actions, owners, deadlines, and verification evidence. The CEO reviews open high/critical items weekly and recurring categories monthly. Two high issues in one workflow across three consecutive sessions trigger a session pause and control review.

## Satisfaction follow-up

After resolution, request (but never infer) a 1-5 satisfaction score and optional reason through the approved session channel. Record `declined` or `not_collected` when applicable. Compare pre/post-resolution score only when both are genuinely observed. Monthly reporting includes counts, severity distribution, acknowledgement and resolution attainment, reopen/recurrence rate, rating distribution/median, missing ratings, and unresolved items with denominators.

No external contact, customer-data processing, production action, compensation, or promise is authorized by this policy. Those actions require Chairman approval.
