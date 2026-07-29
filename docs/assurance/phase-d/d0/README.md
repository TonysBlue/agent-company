# Phase D Stage D0 Frozen Baseline Inputs

These files define the approved current-workflow replay and D0-to-treatment gate. They
use synthetic fixtures and historical internal test metadata and do not access the
protected holdout. `freeze-manifest-v1.json` records the byte-level SHA-256 of the
charter, scenario bank, fault bank, comparator, rubric, comparison plan, independent
review, and Chairman confirmation before the regenerated run begins.

Run from the Agent Company repository root:

```text
python3.11 scripts/run_phase_d_d0.py \
  --freeze docs/assurance/phase-d/d0/freeze-manifest-v1.json \
  --output evidence/phase-d/d0
```

The runner rejects chronology or hash drift, repository-commit drift, exact regression
count drift, undersized banks, duplicate case identities, repository-boundary
violations, and non-allowlisted test targets. After a successful reviewed D0 run, D1
and D2 may start only under `../start-freeze-manifest-v1.json`.
