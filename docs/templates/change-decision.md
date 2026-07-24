# Change Decision: <name>

- ID: `<change-id>`
- Version: `v1`
- Initiative: `<initiative-id>`
- Actor/principal: `<principal-id and role>`
- Authority: `<CEO | Chairman | delegated scope>`
- Effective time: `<ISO-8601>`
- Decision: `approve | reject | hold | emergency_containment`

## Reason And Evidence

State why the approved assurance graph must change and link immutable evidence.

## Changed Nodes

List exact old and proposed artifact IDs, versions, and hashes.

## Dependency Impact

Record the traversed dependency DAG and every affected task binding, candidate, Eval Run, Review Decision, Release Decision, deployment, and observation.

## Invalidation And Rebaseline

Define which work is paused, invalidated, quarantined, grandfathered, rerun, or rebaselined. Hard-gate, authority, target, threshold, comparator, or data-boundary changes fail closed.

## Migration, Rollback, And Effective Time

Define application order, safe stop, rollback, and when the new graph becomes authoritative.

## Store Reconciliation

Record expected Git commit, SQLite records, archive references, hashes, and integrity-conflict handling.

## Approval Record

Record independent review, decision authority, conditions/expiry, and exact Change Decision hash.
