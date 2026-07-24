# Design Manifest: <name>

- ID: `<manifest-id>`
- Version: `v1`
- Status: `draft | approved | superseded | rejected`
- Initiative: `<initiative-id>`
- Profile: `product-competitive | control-plane-reliability`
- Risk class: `C0 | C1 | C2 | C3`
- Owner principal/role: `<principal-id and role>`
- Decision authority: `<principal-id and role>`
- Repository scope: `<repository IDs>`
- Supersedes: `<manifest-id/version | none>`

## Governing Artifact Set

List each artifact with kind, ID, version, SHA-256, repository/commit or archive reference, status, owner principal, approver principal, and applicability:

- exactly one active Goal Contract;
- exactly one primary Initiative/Capability Design;
- zero or more Architecture Decisions;
- one Behavior Specification per independently releasable capability;
- one Eval Contract per release decision endpoint;
- one accepted Baseline per Eval Contract/comparator set;
- required Evaluation Design and Release Design according to profile/risk.

## Dependency DAG

Encode typed directed edges such as `governs`, `refines`, `constrains`, `evaluates`, `baselines`, `supersedes`, and `invalidates`. The graph must be acyclic except explicitly modeled version history.

## Profile And Risk Requirements

Record the required artifact kinds, gate owners, grader categories, hard-gate categories, reviewer independence, holdout custody, release authority, and any approved proportional C0/C1 rationale.

## Cross-Repository Impact

List affected repositories, interfaces, owners, compatibility requirements, and required approvals.

## Invalidation Rules

For each governing node, define dependent artifacts, task bindings, candidates, Eval Runs, reviews, releases, and deployments invalidated by a new version or integrity conflict.

## Open Conflicts

List conflicting or unresolved documents. Any material conflict blocks approval.

## Artifact-Set Hash

Record the canonical ordering algorithm and resulting `artifact_set_sha256` over all IDs, versions, hashes, edges, profile, risk class, and approvals.

## Approval Record

Record reviewers, stable principal IDs, conflict declarations, conditions/expiry, timestamp, and exact manifest hash.
