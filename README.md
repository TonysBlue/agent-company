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
python3.11 -m agent_company.cli task-list
python3.11 -m agent_company.cli task-create --actor CEO --owner CTO --title "Implement bounded capability" --domain engineering --priority 80 --acceptance-criteria "Runnable implementation and regression evidence pass."
python3.11 -m agent_company.cli task-claim 1 --actor CPO
python3.11 -m agent_company.cli task-complete 1 --actor CPO --summary "Acceptance criteria met" --evidence path/to/reviewable-evidence
python3.11 -m agent_company.cli task-cancel 1 --actor CEO --reason "Superseded by reviewed task 2."
python3.11 -m agent_company.cli chairman-inbox
python3.11 -m agent_company.cli decide 1 approve --rationale "Proceed internally only."
python3.11 -m agent_company.cli report
python3.11 -m agent_company.cli demo
python3.11 -m agent_company.cli validate
python3.11 -m agent_company.cli validate-brand-kit examples/brand-kit.json
python3.11 -m agent_company.cli campaign-manifest examples/campaign.json
python3.11 -m agent_company.cli campaign-render examples/campaign.json
python3.11 -m agent_company.cli prompt-pack examples/prompt-pack.json
python3.11 -m agent_company.cli unit-economics examples/unit-economics.json
python3.11 -m agent_company.cli product-shot-workflow examples/product-shot-workflow.json
python3.11 -m agent_company.cli visual-qa-scorecard examples/visual-qa-scorecard.json
```

`campaign-render` turns a validated, provenance-gated campaign into deterministic internal-draft SVG creatives, per-variant checksums, and a self-contained offline `review-gallery.html` for internal review. It does not authorize publishing or claim measured visual quality.

`run-cycle` is governance dispatch only: it moves eligible work to `in_progress` and never
claims that work is complete. Agents must use `task-claim` for still-open work and
`task-complete` only after producing one or more existing, reviewable evidence files.
Obsolete or duplicate work must use `task-cancel`, which records that no completion occurred.
Only the CEO may use `task-create`; it requires a registered agent owner, a unique title,
bounded priority, explicit acceptance criteria, and records the new work in the audit trail.

The default backend is deterministic and local. It writes reviewable JSON image-generation/editing artifacts under `data/artifacts/`. Brand-kit validation covers versioned palettes, typography, logo placement, and forbidden elements. Campaign manifest generation deterministically expands channel, format, asset, and copy combinations into draft variants with stable IDs and checksums.
Prompt-pack expansion validates a versioned template and variable matrix, then writes a deterministic manifest of uniquely identified rendered prompts. These are internal prompt artifacts, not generated images or evidence of visual quality.
The unit-economics command calculates internal low/base/high cost sensitivity from explicit assumptions. It does not set or authorize a price.
Product-shot workflow manifests validate required source provenance, explicit controls, ordered stages, and acceptance checks across at least three scenario inputs before writing deterministic internal workflow metadata.
Visual QA scorecards calculate pass/fail/stop results from explicitly measured edit-fidelity and brand-consistency observations. These tools do not measure images directly and do not measure or claim actual image quality.

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
