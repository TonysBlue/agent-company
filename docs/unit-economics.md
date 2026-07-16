# Unit Economics

Baseline model:

- Revenue: subscription plus usage tiers for generation/editing volume.
- COGS: inference cost, storage, support, QA review, payment fees.
- Gross margin target: 70%+ after inference optimization.
- CAC channels: founder-led outbound, creator communities, agency partnerships.
- Payback target: under 6 months for SMB and team plans.

No spend or price change is executed without Chairman approval.

## Internal sensitivity model

Run `python3 -m agent_company unit-economics examples/unit-economics.json` to reproduce the
current scenario calculation. Inputs are planning assumptions, not vendor quotes, invoices,
measured customer usage, or approved pricing.

Formula:

`cost_per_accepted_asset = (inference + storage + (QA minutes / 60 * QA hourly cost)) / acceptance rate`

| Scenario | Inference / attempt | Storage / attempt | QA minutes | QA hourly cost | Acceptance | Cost / attempt | Cost / accepted asset |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Low | $0.030 | $0.002 | 0.5 | $18 | 85% | $0.182 | $0.214118 |
| Base | $0.120 | $0.005 | 1.5 | $24 | 70% | $0.725 | $1.035714 |
| High | $0.400 | $0.015 | 4.0 | $36 | 45% | $2.815 | $6.255556 |

Holding base storage, QA, and acceptance assumptions constant, changing inference from
$0.03 to $0.12 to $0.40 changes cost per accepted asset from $0.907143 to $1.035714 to
$1.435714. The wider low/high scenario range is driven mainly by unvalidated QA time and
acceptance assumptions. Replace assumptions with measured, sourced values before using this
model for a pricing recommendation; any formal price still requires Chairman approval.

## Synthetic controlled-beta evidence

Run the local, reproducible sample summary:

```bash
python3.11 -m agent_company.cli beta-session-economics \
  examples/beta-session-economics.json \
  --output data/artifacts/beta-session-economics.json
```

Each retained session binds token components and cost, operation duration, human review
minutes, and quality score observations to the `session_id` and an observation source.
Token component counts must add to `total_tokens`. Cost observations require a currency;
quality observations retain their scale. Timestamp-derived operation duration also records
its source and session association.

The summary reports per-metric `collected` and `not_collected` counts. Aggregate fields with
no observations remain the literal `not_collected`, never numeric zero. A session contributes
to fully costed economics only when token cost in the summary currency and human review time
are both collected. Excluded session IDs are reported for audit. The cost formula is:

`estimated_cost = token cost + (human review minutes / 60 * human review hourly cost)`

The checked-in dataset contains only local synthetic fixtures. The command rejects any
`dataset_kind` other than `synthetic`; its output sets both `pricing_authorized` and
`external_action_authorized` to `false`. The result is internal validation evidence, not
customer, pricing, billing, margin, publication, outreach, or production evidence.
