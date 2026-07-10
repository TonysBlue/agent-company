# Agent Company OS

Python 3.11 stdlib-only MVP for an AI-native company operating system. Chairman is the only human. CEO, CPO, CTO, CRO, COO, CFO, and Counsel are agents.

The company develops and commercially operates 织象 PixWeave, a configurable AI image generation and editing product for controlled commercial visual workflows. The working name is independent from Raphael AI and does not imply affiliation. The system preserves human control: it never claims legal autonomy and blocks external, irreversible, financial, pricing, legal, production, or customer-data actions until Chairman decides.

Brand line: **让商业视觉稳定、批量、可控地产生。**

This repository operates a real commercial venture rather than a business simulation. Plans, deterministic metadata, and internal drafts are not customer evidence or shipped product. Completion requires reviewable evidence such as runnable code, passing tests, sourced research, approved real customer records, or measured operating results.

## CLI

```bash
python3.11 -m agent_company.cli init
python3.11 -m agent_company.cli status
python3.11 -m agent_company.cli run-cycle
python3.11 -m agent_company.cli chairman-inbox
python3.11 -m agent_company.cli decide 1 approve --rationale "Proceed internally only."
python3.11 -m agent_company.cli report
python3.11 -m agent_company.cli demo
python3.11 -m agent_company.cli validate
python3.11 -m agent_company.cli validate-brand-kit examples/brand-kit.json
python3.11 -m agent_company.cli campaign-manifest examples/campaign.json
```

The default backend is deterministic and local. It writes reviewable JSON image-generation/editing artifacts under `data/artifacts/`. Brand-kit validation covers versioned palettes, typography, logo placement, and forbidden elements. Campaign manifest generation deterministically expands channel, format, asset, and copy combinations into draft variants with stable IDs and checksums.

## Files

- `agent_company/`: package and CLI.
- `config/sample.ini`: configurable product, paths, backend, governance.
- `data/chairman/inbox`: CEO-only approval request files for Chairman.
- `data/chairman/outbox`: Chairman decision files.
- `docs/`: constitution, operating model, architecture, and plans.
- `tests/`: unittest coverage.
- `docs/versioning-and-records.md`: Git change control, evidence, retention, and recovery policy.
- `scripts/archive_company.py`: consistent SQLite/runtime snapshots with SHA-256 manifests.

## Version and archive

```bash
make test
python3 -m agent_company validate
make archive LABEL=manual
git log --oneline --decorate
```

Git versions definitions and governance; operating archives preserve the live ledger, decisions, artifacts, and logs. Runtime archives are deliberately excluded from Git and must not contain unprotected secrets or customer personal data.
