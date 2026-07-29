# Phase D Stage D0 Internal Baseline Report

- Run ID: `phase-d-d0-baseline-v1`
- Scope: approved Stage D0 baseline replay only
- Raw run start: `2026-07-29T07:40:20.866158+00:00`
- Raw run end: `2026-07-29T07:40:25.635779+00:00`
- Agent Company commit: `8a50770b8ff5f954ceeff2680c2ab571605fabe1`
- PixWeave commit: `d78094f26eb697c810899a40771a8af6dec7ce19`
- Frozen-input manifest SHA-256: `05fbb3170bde78e86590bb551a60c65233dc025d5966873175bdad3300deae34`
- Independent baseline review: `not_collected`
- Chairman confirmation: `not_collected`

## Outcome

D0 replayed 6 synthetic/replayed PixWeave product cases and 16 Company OS fault/control cases. This report is a current-workflow baseline and does not infer treatment superiority, product quality, public performance, or pilot adoption.

## Frozen Inputs And Procedure

The runner verified the pre-recorded SHA-256 of the scenario bank, fault bank, comparator, rubric, and comparison plan before executing one exact allowlisted `unittest` probe per case. Each subprocess ran locally against a pinned repository commit with a one-attempt budget and retained an immutable log.

Exact command:

```text
python3.11 scripts/run_phase_d_d0.py --freeze docs/assurance/phase-d/d0/freeze-manifest-v1.json --output evidence/phase-d/d0
```

## Baseline Metrics

| Metric | Product | Control | Combined |
| --- | ---: | ---: | ---: |
| Valid cases | 6 | 16 | 22 |
| Hard gates | `{"failed":0,"passed":6}` | `{"failed":0,"passed":16}` | `{"failed":0,"passed":22}` |
| Defects | `{"after_nominal_completion":"not_collected","before_review":{"count":3,"seeded_faults_detected":3,"severity_weighted":9,"unexpected_probe_failures":0},"during_independent_review":"not_collected"}` | `{"after_nominal_completion":"not_collected","before_review":{"count":13,"seeded_faults_detected":13,"severity_weighted":49,"unexpected_probe_failures":0},"during_independent_review":"not_collected"}` | `{"after_nominal_completion":"not_collected","before_review":{"count":16,"seeded_faults_detected":16,"severity_weighted":58,"unexpected_probe_failures":0},"during_independent_review":"not_collected"}` |
| p50/p90 waits (ms) | `{"automated_gate":{"p50":108,"p90":136},"cycle":{"p50":108,"p90":136},"queue":{"p50":0,"p90":0}}` | `{"automated_gate":{"p50":246,"p90":314},"cycle":{"p50":246,"p90":314},"queue":{"p50":0,"p90":0}}` | `{"automated_gate":{"p50":216,"p90":305},"cycle":{"p50":216,"p90":305},"queue":{"p50":0,"p90":0}}` |
| Model tokens | `not_collected` | `not_collected` | `not_collected` |
| Human minutes | `{"engineering":"not_collected","evaluation":"not_collected","review":"not_collected"}` | `{"engineering":"not_collected","evaluation":"not_collected","review":"not_collected"}` | `{"engineering":"not_collected","evaluation":"not_collected","review":"not_collected"}` |
| Rework | `{"count":"not_collected","minutes":"not_collected"}` | `{"count":"not_collected","minutes":"not_collected"}` | `{"count":"not_collected","minutes":"not_collected"}` |
| False blocks | `{"count":0,"valid_controls":3}` | `{"count":0,"valid_controls":3}` | `{"count":0,"valid_controls":6}` |
| Reviewer disagreement | `not_collected` | `not_collected` | `not_collected` |
| Unauthorized transitions | `{"count":0,"observed":0}` | `{"count":0,"observed":12}` | `{"count":0,"observed":12}` |
| Lineage completeness | `{"complete":6,"rate":1.0,"total":6}` | `{"complete":16,"rate":1.0,"total":16}` | `{"complete":22,"rate":1.0,"total":22}` |

Artifact preparation retained raw start/end timestamps and measured `195` ms. Human review wait is `not_collected`; no human baseline review occurred during tooling execution.

## Case Results

| Case | Domain | Kind | Seeded fault | Valid control | Hard gate | Defects before review | Log |
| --- | --- | --- | ---: | ---: | --- | ---: | --- |
| `pw-d0-001-source-edit-lineage` | product | synthetic_product_replay | false | true | pass | 0 | `logs/pw-d0-001-source-edit-lineage.txt` |
| `pw-d0-002-unsafe-source-rejection` | product | synthetic_adversarial_replay | true | false | pass | 1 | `logs/pw-d0-002-unsafe-source-rejection.txt` |
| `pw-d0-003-brand-manifest` | product | synthetic_product_replay | false | true | pass | 0 | `logs/pw-d0-003-brand-manifest.txt` |
| `pw-d0-004-provenance-faults` | product | synthetic_adversarial_replay | true | false | pass | 1 | `logs/pw-d0-004-provenance-faults.txt` |
| `pw-d0-005-reviewable-render` | product | synthetic_product_replay | false | true | pass | 0 | `logs/pw-d0-005-reviewable-render.txt` |
| `pw-d0-006-hard-gate-thresholds` | product | synthetic_adversarial_replay | true | false | pass | 1 | `logs/pw-d0-006-hard-gate-thresholds.txt` |
| `os-d0-001-stale-artifact` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-001-stale-artifact.txt` |
| `os-d0-002-threshold-drift` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-002-threshold-drift.txt` |
| `os-d0-003-profile-drift` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-003-profile-drift.txt` |
| `os-d0-004-authority-drift` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-004-authority-drift.txt` |
| `os-d0-005-credential-drift` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-005-credential-drift.txt` |
| `os-d0-006-generation-drift` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-006-generation-drift.txt` |
| `os-d0-007-direct-completion-bypass` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-007-direct-completion-bypass.txt` |
| `os-d0-008-forged-binding-trigger` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-008-forged-binding-trigger.txt` |
| `os-d0-009-quarantined-eval` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-009-quarantined-eval.txt` |
| `os-d0-010-nonindependent-review` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-010-nonindependent-review.txt` |
| `os-d0-011-dashboard-context-leak` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-011-dashboard-context-leak.txt` |
| `os-d0-012-lifecycle-rollback` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-012-lifecycle-rollback.txt` |
| `os-d0-013-claim-history-asymmetry` | control | seeded_fault_replay | true | false | pass | 1 | `logs/os-d0-013-claim-history-asymmetry.txt` |
| `os-d0-014-never-bound-control` | control | valid_compatibility_control | false | true | pass | 0 | `logs/os-d0-014-never-bound-control.txt` |
| `os-d0-015-approved-completion-control` | control | valid_compatibility_control | false | true | pass | 0 | `logs/os-d0-015-approved-completion-control.txt` |
| `os-d0-016-unbound-dispatch-control` | control | valid_compatibility_control | false | true | pass | 0 | `logs/os-d0-016-unbound-dispatch-control.txt` |

## Missing Data And Limitations

Model tokens, engineering/evaluator/reviewer minutes, rework, independent-review defects, post-completion defects, reviewer disagreement, and human gate waits are `not_collected` because the replay probes and historical records do not expose them. Zero is never substituted for missing data.

The product cases replay deterministic PixWeave controls; they do not generate or human-rate new visual assets and cannot establish D1 preference or quality. Control cases replay existing unit-level fault/control evidence; they are not D2 treatment execution. Subprocess duration is a machine-gate observation on this host, not an estimate of historical implementation or reviewer time.

## Treatment Gates

- D1: `blocked` pending independent baseline review, Chairman confirmation of frozen comparison manifests and numerical ceilings, and a CEO-recorded D1 start decision.
- D2: `blocked` pending independent baseline review, Chairman confirmation of frozen comparison manifests and numerical ceilings, and a CEO-recorded D2 start decision.
- independent baseline review: `not_collected`
- Chairman confirmation: `not_collected`
- CEO D1/D2 start decision: `not_collected`

No treatment, holdout access, customer data, external spend, outreach, publication, production action, or PixWeave source modification occurred.
