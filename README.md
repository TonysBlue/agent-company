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
python3.11 -m agent_company.cli dashboard --host 0.0.0.0 --port 18080
python3.11 -m agent_company.cli beta-product --host 127.0.0.1 --port 18112
python3.11 -m agent_company.cli demo
python3.11 -m agent_company.cli validate
python3.11 -m agent_company.cli validate-brand-kit examples/brand-kit.json
python3.11 -m agent_company.cli campaign-manifest examples/campaign.json
python3.11 -m agent_company.cli campaign-render examples/campaign.json
python3.11 -m agent_company.cli campaign-render-verify data/artifacts/campaign-render-v2-95ca21758bde
python3.11 -m agent_company.cli campaign-review data/artifacts/campaign-render-v2-95ca21758bde examples/campaign-review-decisions.json
python3.11 -m agent_company.cli prompt-pack examples/prompt-pack.json
python3.11 -m agent_company.cli unit-economics examples/unit-economics.json
python3.11 -m agent_company.cli product-shot-workflow examples/product-shot-workflow.json
python3.11 -m agent_company.cli visual-qa-scorecard examples/visual-qa-scorecard.json
python3.11 -m agent_company.cli feedback-capture examples/feedback-submission.json --output data/artifacts/feedback-submission.json
python3.11 -m agent_company.cli feedback-triage data/artifacts/feedback-submission.json examples/feedback-triage.json --output data/artifacts/feedback-triage.json
python3.11 -m agent_company.cli beta-launch-readiness examples/beta-launch-package.json
```

`campaign-render` turns a validated, provenance-gated campaign into deterministic internal-draft SVG creatives, per-variant checksums, and a self-contained offline `review-gallery.html` for internal review. It does not authorize publishing or claim measured visual quality.
`campaign-render-verify` fails closed unless a retained `campaign-render/v2` bundle has a valid manifest, gallery, exact SVG inventory, stable variant filenames, matching SHA-256 checksums, and draft/no-publish controls.
`campaign-review` consumes a verified `campaign-render/v2` bundle plus complete per-variant approve/reject decisions, validates reviewer metadata and rejection reasons, and writes a deterministic internal review record bound to the bundle and SVG checksums. It explicitly records no external publication authorization.

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
Feedback capture rejects declared sensitive data and anti-abuse honeypots, requires explicit consent before retaining optional contact data, and binds submissions to product/workflow/artifact context. Feedback triage records acknowledgement-through-release states and requires backlog/release linkage for those claims; neither command authorizes outreach or publication.
`beta-launch-readiness` evaluates a versioned internal readiness package with pinned evidence for product capability, feedback controls, risk review, onboarding, support ownership, observability, rollback, security/privacy, unit economics, and reserved-action approvals. It fails closed on malformed, missing, or tampered evidence and always records `launch_authorized: false`; it never authorizes production deployment, publication, pricing, payment, outreach, or launch.

`beta-product` runs a local-only internal HTTP interface for campaign render, review, and feedback capture at `http://127.0.0.1:18112/beta` by default. It composes the existing validated campaign, render, review, and feedback domain functions, writes only local artifacts under `data/artifacts/local-beta/`, shows draft/no-publish controls, and records no production deployment, publication, pricing, payment, outreach, or legal authorization.

## Read-only dashboard

The operations dashboard is a stdlib-only HTTP service that reads `data/company.sqlite3`, Git metadata, project docs, and local artifact files without mutating company state. It exposes three separate Chinese-labeled pages:

- `http://127.0.0.1:18080/management` for company daily management, tasks, approvals, cycles, audit, and human dependencies.
- `http://127.0.0.1:18080/project` for product/project status, Git version, roadmap, current tasks, experiments, artifacts, and validation evidence.
- `http://127.0.0.1:18080/operations` for pre-launch product operations placeholders. Unknown fields are shown as unavailable placeholders, not zeroes.

JSON endpoints:

- `http://127.0.0.1:18080/healthz`
- `http://127.0.0.1:18080/api/status`

Durable user service:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/agent-company-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-company-dashboard.service
```

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
