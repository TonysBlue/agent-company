# Phase D Stage D0 Frozen Baseline Inputs

These files define the approved D0-only current-workflow replay. They use synthetic
fixtures and historical internal test metadata, do not execute D1/D2 treatment, and do
not access the protected holdout. `freeze-manifest-v1.json` records the byte-level
SHA-256 of the scenario bank, fault bank, comparator, rubric, and comparison plan.

Run from the Agent Company repository root:

```text
python3.11 scripts/run_phase_d_d0.py \
  --freeze docs/assurance/phase-d/d0/freeze-manifest-v1.json \
  --output evidence/phase-d/d0
```

The runner rejects hash drift, repository-commit drift, undersized banks, duplicate
case identities, repository-boundary violations, and non-allowlisted test targets.
D1/D2 remain blocked after a successful D0 run.
