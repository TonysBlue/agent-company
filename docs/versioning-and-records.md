# Versioning And Records Policy

## Sources of truth

- Git is the source of truth for code, configuration templates, governance, prompts, operating policies, and documentation.
- `data/company.sqlite3` is the live operational ledger for tasks, approvals, metrics, experiments, cycles, roles, RACI, and audit events.
- `data/chairman/` holds decision request and decision evidence.
- `data/artifacts/` and `logs/` hold runtime evidence. They are archived, not committed by default.
- `archives/` contains immutable operational snapshots with checksums and SQLite-consistent backups. It is excluded from Git because it may contain sensitive operational material.

## Change control

Every material change must follow this sequence:

1. Record the objective and affected business capability.
2. Inspect the current Git status; never overwrite unrelated work.
3. Make a bounded change.
4. Run relevant tests plus `python -m agent_company validate`.
5. Update governance or operating documentation when behavior changes.
6. Commit the coherent change with `type: concise subject` (`feat`, `fix`, `docs`, `chore`, `refactor`).
7. Create an annotated version tag for business milestones, production releases, schema migrations, and governance changes.
8. Create an operating archive after material decisions, before/after migrations or releases, and at least daily.

Code commits and runtime archives are separate: a commit explains what definition changed; an archive proves what operational state existed at a point in time.

## Version scheme

Use semantic versions:

- Major: incompatible product, governance, data-model, or operating changes.
- Minor: backward-compatible capability or material organizational addition.
- Patch: fixes, documentation corrections, and low-risk operating improvements.

Pre-revenue/internal milestones may use `v0.x.y`. The first working company baseline is `v0.1.0`.

## Decision and evidence rules

- Chairman decisions remain in SQLite audit/approval records and archived inbox/outbox files.
- Customer, revenue, contract, deployment, cost, and interview claims require linked evidence.
- Campaign render bundles are internal review evidence only. Their SVG files, `review-gallery.html`, and `render-manifest.json` must be produced through the atomic bundle publication path so reviewers never rely on partial output. The manifest must bind the local rendering provider, per-asset media type, render provenance, and SVG checksums.
- Campaign review records are internal decisions only. They must be generated from a verified `campaign-render/v2` bundle, bind to bundle and SVG checksums, and must not authorize external publication.
- Never commit secrets, API keys, customer personal data, credentials, raw contracts, or payment details.
- Archive access is local and restricted. Encrypt and replicate archives before real customer or financial data is introduced.
- Retention: daily snapshots 30 days, weekly snapshots 12 months, milestone and legal/financial decision snapshots indefinitely unless a lawful deletion policy requires otherwise.

## Recovery test

An archive is valid only when:

- every manifest checksum matches;
- SQLite `PRAGMA integrity_check` returns `ok`;
- the associated Git commit/tag is recorded when available;
- the restore procedure has been tested periodically in a separate directory.

Commands:

```bash
make archive LABEL=daily
python3 scripts/archive_company.py verify archives/<snapshot>
git log --oneline --decorate
```
