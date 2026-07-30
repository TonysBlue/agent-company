# Phase D Corrected D1/D2 Redesign V4

V4 supersedes V3 after review of immutable HEAD
`33bcb6371e18c08b05c49723282db24389b8bc6c` and tree
`1447dcf47adc67ee280720a64abaf094743bdd1c`. Historical D1/D2 and V3 evidence is
preserved but cannot support treatment or threshold conclusions.

## Approval gate

`corrected-freeze-v4.json` is deliberately blocked. It uses Git commit/tree objects and a future
hardened external review-target manifest instead of circular repository file hashes. Treatment
cannot become passable until all sixteen named cases replay the real Company OS C2 schemas and
public control APIs in isolated copies, a clean committed candidate HEAD/tree is externally
bound, reviewer and CEO credentials load from the frozen external 0600 registry, an independent
approval exists, and a separate later CEO start decision exists. None exists now.

## Allowed validation

Only non-treatment protocol checks, RED/GREEN tests and regressions are allowed while blocked:

```text
python3.11 scripts/run_phase_d_redesign_v4_protocol.py --development-overlay --output /tmp/phase-d-v4-protocol
python3.11 scripts/run_phase_d_redesign_v4_protocol.py --development-overlay --verify --output /tmp/phase-d-v4-protocol
python3.11 -m unittest tests.test_phase_d_redesign tests.test_phase_d_redesign_v3 tests.test_phase_d_redesign_v4 -v
```

The protocol runner validates scenario/bank structure, assignment logic, adversarial SVG
canaries, sixteen distinct production-control mappings, the real-replay blocker, external trust
requirements and reproducibility. It executes zero D1/D2 workflows, creates zero D1 artifacts,
attempts zero D2 mutations, collects zero observations and always reports thresholds false. Verify
mode reproduces into a temporary directory and compares manifests without deleting or mutating
the expected evidence.

Legacy D1 renderer and rater-delivery helpers, legacy D2 surrogate helpers, V3 authorization and
the V2/V3 dry-run entrypoints fail closed. A future real-replay implementation must include an
executable verifier; changing contract status and supplying attestation booleans cannot make D2
threshold derivation pass.
