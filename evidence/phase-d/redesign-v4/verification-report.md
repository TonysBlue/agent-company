# Phase D Redesign V4 Verification

## Review target and disposition

- Reviewed baseline HEAD: `33bcb6371e18c08b05c49723282db24389b8bc6c`
- Reviewed baseline tree: `1447dcf47adc67ee280720a64abaf094743bdd1c`
- Current checkout HEAD/tree: unchanged from the reviewed baseline; V4 changes remain uncommitted
- V4 execution authorization: blocked
- D1 treatment workflows executed: 0
- D1 treatment artifacts generated: 0
- D2 treatment workflows executed: 0
- D2 database/repository mutations attempted: 0
- D2 observations collected: 0
- D2 thresholds passed: false
- Phase D treatment pass possible: false
- Approvals or credentials fabricated: no

V4 supersedes V2/V3 authorization, runners and treatment helpers. The V3 result that described
sixteen synthetic surrogate mutations as a blocked dry run is invalid and retained only as
historical evidence. The V4 runner performs seven explicit non-treatment checks and has no
treatment execution path.

## Implemented findings

- Blocked protocol execution performs only seven named static/canary checks. Legacy D1 render,
  workflow and delivery helpers and legacy D2 surrogate helpers fail before writing output.
- The V4 D2 bank maps the exact thirteen material fault classes and three controls to sixteen
  distinct, importable Company OS modules/entrypoints and resolvable one-test regressions. No
  surrogate schema, blanket treatment trigger or replay result remains in executable V4 code.
- Threshold derivation requires the exact authoritative V4 contract/bank, exact 13+3 case and
  role/severity coverage, exact observation schemas, and an executable real-replay verifier. A
  status flip plus attestation booleans cannot certify treatment.
- Governance reads only freeze-bound reviewer/CEO credentials from external regular 0600 files
  through an external hardened registry. Caller credentials, registry/freeze substitution,
  symlinks, unsafe parent permissions and missing freeze identity bindings are rejected.
- A candidate review target can come only from the trusted reviewer-signed external manifest.
  Exact Git commit/tree binding and a clean entire worktree are mandatory; embedded candidate
  targets, HEAD/tree drift and any tracked or untracked path drift fail closed.
- SVG validation is an explicit geometry-only supported subset. It rejects foreign namespaces,
  relative/unsupported/malformed/incomplete paths, malformed points, strokes, transforms/styles,
  nested SVG/text, text and image elements, external/paint references, use/defs/groups, curves and
  arcs. Text/image bounds require future renderer-grade validation and are therefore unsupported.
- Evidence verification validates the stored exact manifest first, reproduces into a temporary
  directory and compares manifests/hashes without deleting or rewriting expected evidence.

## Strict TDD evidence

Initial and incremental RED failures are preserved in `red-tests.txt`,
`red-treatment-helper-guards.txt`, `red-surrogate-and-evidence-tamper.txt`,
`red-threshold-bank-role.txt`, `red-caller-replay-and-parent-path.txt`,
`red-freeze-substitution.txt`, `red-signed-target-and-authoritative-replay.txt`,
`red-svg-supported-subset.txt`, `red-svg-incomplete-command.txt`,
`red-fabricated-replay-attestation.txt`, `red-d1-artifact-helper-guards.txt`,
`red-final-fail-closed-audit.txt`, `red-external-target-and-freeze-identity.txt`,
`red-protocol-control-resolution.txt`, `red-malformed-observation-extra-fields.txt`,
`red-unprovable-svg-text-image.txt`, `red-clean-worktree-required.txt` and
`red-freeze-author-principals.txt`.

Definitive GREEN results:

- `focused-phase-d-tests-handoff.txt`: 53 tests passed.
- `full-agent-company-tests-handoff.txt`: 273 tests passed.
- `pixweave-tests-final-definitive.txt`: 58 tests passed without PixWeave edits.
- `isolated-validation-final-definitive.txt`: copied Company OS validation returned
  `{"ok": true, "errors": []}`.
- Python compilation and `git diff --check`: passed.

One earlier full run in `full-agent-company-tests-final-definitive.txt` hit the pre-existing
concurrent schema-initialization race (`trigger ... already exists`) in
`test_concurrent_real_executors_cannot_duplicate_claim`. The exact test then passed 5/5 in
`concurrency-regression-reruns.txt`, and the two subsequent full suites passed 273/273. No change
to that unrelated concurrency path was made.

## Reproducibility and nonmutation

`protocol-handoff/protocol-result.json` reports `blocked_protocol_checks_complete`, seven allowed
checks, 21 SVG validator canaries, sixteen resolved production-control mappings, zero
workflows/artifacts/mutations/observations, and false threshold/pass status.
`protocol-verify-handoff.txt` reports `evidence_reproduced`, manifest SHA-256
`c0e95a178fecc27da539c20e05e19b6347eb7a707c87e9d18d15a2f79e3e64a5`, and
`expected_evidence_unchanged: true`. The path-local protocol aggregate was identical before and
after verification: `06b309896a7e73c79e82f6379b73ff050575d630597db410c395ec5ff41dff3b`.

The live `data/company.sqlite3` pre/post SHA-256 is
`9372e062d53323cbc6d7fdc9f6283f8c375c18815c5a9d1efdf737f240997f70`.
The preserved V3 evidence aggregate pre/post SHA-256 is
`9e1e36be9ff456b0a8e2341638d1ea874ccaab4db88eef2bb39d486b625585a6`.
PixWeave remained at `d78094f26eb697c810899a40771a8af6dec7ce19` with a clean worktree.

No D1/D2 treatment, live database mutation, service restart, customer contact, spend,
publication, production action, PixWeave edit, commit or push occurred.

## Remaining blockers

1. Real Company OS C2 replay for all thirteen named material faults and three named controls is
   not implemented. Mapping and existing regressions are not a Phase D paired replay result.
2. No executable V4 real-replay verifier or authenticated replay attestation exists, so threshold
   derivation and treatment pass remain impossible.
3. No clean committed V4 candidate HEAD/tree or trusted reviewer-signed external target manifest
   exists. The current uncommitted development checkout cannot authorize execution.
4. The external hardened governance registry and reviewer/CEO credential files are absent.
5. No authenticated independent approval or later separately authenticated CEO start decision
   exists.

Authorization remains blocked. No commit or push was performed.
