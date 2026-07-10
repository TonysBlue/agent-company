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
