# Phase D Corrected D1/D2 Redesign V4

Commit `3ab23d1630457f63aa310a9a206f5429493ed659` was independently reviewed as the
V4 candidate and rejected with 2 Critical, 6 High and 2 Medium findings. Historical D1/D2, V3,
and V4 evidence is preserved but cannot support treatment, threshold, authorization, or candidate
verification conclusions.

## Approval gate

`corrected-freeze-v4.json` is deliberately blocked. Treatment cannot become passable until all
sixteen named cases replay the real Company OS C2 schemas through a concrete internal verifier,
an external signed manifest binds a clean candidate commit/tree, and a separate production
signature verifier authenticates independent review and a later CEO start decision. The runtime
loads no signing secrets and exposes no authorization path. None of the missing services or
records exists now.

## Allowed validation

Only non-treatment protocol checks, RED/GREEN tests and regressions are allowed while blocked:

```text
python3.11 scripts/run_phase_d_redesign_v4_protocol.py --development-overlay --output /tmp/phase-d-v4-protocol
python3.11 -m unittest tests.test_phase_d_medium_findings tests.test_phase_d_review_findings tests.test_phase_d_treatments tests.test_phase_d_redesign tests.test_phase_d_redesign_v3 tests.test_phase_d_redesign_v4 -v
```

Development-overlay mode performs diagnostics only. Its result and manifest use development-only
schemas and the status `development_only_unverified_non_candidate`, with `development_only: true`,
`verified: false`, and `candidate_evidence: false`. It validates scenario/bank structure,
assignment logic, adversarial SVG canaries, sixteen distinct production-control mappings, and the
real-replay and external-trust-service blockers. It executes zero D1/D2 workflows, creates zero D1
artifacts, attempts zero D2 mutations, collects zero observations and always reports thresholds
false.

`--verify` has no caller-selectable unbound mode and fails before reading expected evidence until
an externally signed candidate manifest and separate signature verifier exist. Combining
`--verify` with `--development-overlay` is rejected before verification, diagnostics, output
creation, or JSON result output.

The legacy treatment script and module are tombstones; legacy D1 renderer/rater-delivery helpers,
legacy D2 surrogate helpers, V3 authorization and V2/V3 dry-run entrypoints fail closed. The D2
threshold API has no injectable root, attestation, or verifier path. Changing contract status or
installing a mock cannot make certification pass.

Strict immutable review-target validation requires a dedicated candidate clone containing zero
ignored files or filesystem objects. SQLite databases and WAL/SHM/journal sidecars, JSON, logs,
PID and lock files, sockets, code, executables, symlinks, directories, and every other ignored
object fail candidate verification; there is no inert-content allowlist. Runtime state may exist
only outside the strict candidate clone. Development-overlay diagnostics remain explicitly
unverified and non-candidate and do not claim immutable-target verification or candidate binding.
