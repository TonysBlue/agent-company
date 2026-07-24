# Agent Company OS

Python 3.11 stdlib-only MVP for an AI-native company operating system. Chairman is the only human. CEO, Product Engineer, and Customer & Revenue are the three resident agents. Finance & Risk Reviewer, Legal/Compliance Specialist, Independent Quality Reviewer, and Codex workers are invoked on demand and are not resident roles.

The company develops and commercially operates 织象 PixWeave, a configurable AI image generation and editing product for controlled commercial visual workflows. The working name is independent from Raphael AI and does not imply affiliation. The system preserves human control: it never claims legal autonomy and blocks external, irreversible, financial, pricing, legal, production, or customer-data actions until Chairman decides.

Brand line: **让商业视觉稳定、批量、可控地产生。**

This repository operates a real commercial venture rather than a business simulation. Plans, deterministic metadata, and internal drafts are not customer evidence or shipped product. Completion requires reviewable evidence such as runnable code, passing tests, sourced research, approved real customer records, or measured operating results.

## Agent context operating system

Every execution generation currently receives a versioned context bundle compiled from:

- shared company constitution, governance, evidence, data, and collaboration rules;
- all public role charters and RACI relationship;
- the executing role's private playbook;
- current CEO strategy and active Chairman directives;
- task/repository contract, role continuity, project history, and open handoffs.

The approved future design adds Goal Contract, Design Record, Behavior Specification, Evaluation Contract, and Baseline summary to this bundle before build dispatch.

The trusted runner materializes the currently implemented context under `.agent-company/` in the task workspace, mounts it read-only inside Bubblewrap, records its SHA-256 and source versions in SQLite, rejects stale/tampered context before delivery, and requires structured `CONTINUITY.json` evidence to update role/project memory and handoffs. The management dashboard exposes context versions, continuity, and handoff status without exposing role-private content or secrets. Goal/Design/Spec/Eval/Baseline injection is proposed in `docs/development-assurance-system-design.md` and is not implemented or authorized until Chairman approval.

## Repository and asset boundaries

- `TonysBlue/agent-company` is the company control plane: CEO runtime, ledger, approvals, runners, workspace policy, and management dashboard.
- `TonysBlue/PixWeave` is the standalone product repository: PixWeave source, tests, product docs, examples, and service templates.
- Runtime product data lives outside Git under `/home/tony/product-data/pixweave`.
- Role execution uses task-scoped clones under `/home/tony/agent-workspaces/<role>/task-<id>-<repository>`.
- Company Platform Engineer maintains this repository under CEO accountability. Control & Reliability Reviewer independently verifies material control-plane changes. Product Engineer maintains PixWeave.

## CLI

```bash
python3.11 -m agent_company.cli init
python3.11 -m agent_company.cli org-migrate
python3.11 -m agent_company.cli status
python3.11 -m agent_company.cli run-cycle
python3.11 -m agent_company.cli worker-step
python3.11 -m agent_company.cli worker-status
python3.11 -m agent_company.cli worker-wake --reason "operator verification"
python3.11 -m agent_company.cli worker-run
python3.11 -m agent_company.cli ceo-status
python3.11 -m agent_company.cli chairman-directive-ingest --source-platform weixin --source-session-id SESSION --source-message-id MESSAGE --message "transient raw message" --directive-type priority --objective "Reviewed objective" --constraint "No external delivery"
python3.11 -m agent_company.cli ceo-step --fixture examples/ceo-actions-fixture.json --disable-external-delivery
python3.11 -m agent_company.cli task-list
python3.11 -m agent_company.cli task-create --actor CEO --owner "Product Engineer" --title "Implement bounded capability" --domain engineering --priority 80 --acceptance-criteria "Runnable implementation and regression evidence pass."
python3.11 -m agent_company.cli task-claim 1 --actor "Product Engineer" --executor-id product-engineer-local-1 --backend local
python3.11 -m agent_company.cli task-heartbeat 1 --executor-id product-engineer-local-1
python3.11 -m agent_company.cli task-checkpoint 1 --executor-id product-engineer-local-1 --checkpoint "Tests pass" --next-action "Attach evidence"
python3.11 -m agent_company.cli task-inspect 1
python3.11 -m agent_company.cli task-fail 1 --executor-id product-engineer-local-1 --error "Recoverable executor error"
python3.11 -m agent_company.cli task-recover 1 --actor CEO --reason "Lease expired during executor restart"
python3.11 -m agent_company.cli task-complete 1 --actor "Product Engineer" --summary "Acceptance criteria met" --evidence path/to/reviewable-evidence
python3.11 -m agent_company.cli task-cancel 1 --actor CEO --reason "Superseded by reviewed task 2."
python3.11 -m agent_company.cli token-record --agent "Product Engineer" --input-tokens 100 --output-tokens 25 --cache-tokens 10 --reasoning-tokens 5 --total-tokens 140 --source observed-log --model gpt-test --provider openai
python3.11 -m agent_company.cli token-list --agent "Product Engineer"
python3.11 -m agent_company.cli token-summary
python3.11 -m agent_company.cli chairman-inbox
python3.11 -m agent_company.cli decide 1 approve --rationale "Proceed internally only."
python3.11 -m agent_company.cli report
python3.11 -m agent_company.cli dashboard --host 0.0.0.0 --port 18080
python3.11 -m agent_company.cli demo
python3.11 -m agent_company.cli assurance-init
python3.11 -m agent_company.cli assurance-classify --actor CEO --principal-id principal-ceo --title "Proposed schema change" --persistent-schema
python3.11 -m agent_company.cli assurance-list
python3.11 -m agent_company.cli assurance-integrity
python3.11 -m agent_company.cli validate
```


`run-cycle` is governance dispatch only: it moves eligible work to `in_progress` and never
claims that work is complete. Agents must use `task-claim` for still-open work and
`task-complete` only after producing one or more existing, reviewable evidence files.
Obsolete or duplicate work must use `task-cancel`, which records that no completion occurred.
Only the CEO may use `task-create`; it requires a registered agent owner, a unique title,
bounded priority, explicit acceptance criteria, and records the new work in the audit trail.
The active WIP limit is two critical tasks: at most one product task and one commercial task.
Cycles do not manufacture follow-up, phase, or experiment tasks merely to keep the system active.

The 7x24 execution engine uses durable priority-ordered SQLite events plus a local FIFO
notification; there is no fixed CEO cron pulse. An idle `worker-run` blocks in the kernel
and neither calls an LLM nor creates work. Ordinary events retain deterministic
`run-cycle` dispatch. Complex events call the versioned persistent CEO control plane,
which accepts only a strict allowlist of internal actions or Chairman approval requests.
It invokes Hermes v0.18.2 as `hermes chat -q ... -Q --source tool` under the implicit
default profile, with no shell/file tools. Retryable or superseded reasoning requeues the
same event with backoff. Approval delivery uses `hermes send --to weixin`; a failed card
does not block lower-priority safe events. Raw Chairman conversation is never retained,
only structured directives, source identifiers, and a message SHA-256. Use `ceo-step
--fixture ... --disable-external-delivery` for a no-LLM/no-Weixin smoke test. See
`docs/event-worker-deployment.md`; do not enable the infinite user service until CEO
verification.

`org-migrate` applies the versioned `lean-org-v1` SQLite migration. It is safe to rerun,
updates live roles and RACI, retains retired role rows as historical compatibility records,
never rewrites historical task owners, and records one detailed migration audit event.

Task execution continuity is durable in SQLite. Each claimed task has an audited
`task_executions` row with `executor_id`, backend, optional local PID/start identity,
optional async session reference, claim/heartbeat/lease timestamps, attempt bounds,
checkpoint, next action, evidence/log paths, last error, and recovery status. Claiming
is atomic and prevents duplicate active ownership. CEO cycles inspect in-progress
executions before dispatching new work: valid leases are left alone and audited as
renewed observations, stale leases are requeued with bounded retry counts, and exhausted
retries are blocked/escalated instead of looped. Local PID checks never kill processes
and only treat a PID as alive when the recorded start identity still matches, protecting
against PID reuse. Codex may be registered as `--backend codex` with `--session-ref`,
but the core library records that reference only and does not launch Codex.

Observed token usage is stored in an additive audited SQLite `token_usage` ledger.
Records capture `agent`, optional `task_id`/`execution_id`/`session`/`model`/`provider`,
`input_tokens`, `output_tokens`, `cache_tokens`, `reasoning_tokens`, `total_tokens`,
optional `cost` and `currency`, `source`, and timestamp. Writes are validated as
nonnegative, reference-checked, and total-consistent; `agent` must be a registered
agent role. When no token records exist for an agent, the dashboard and summaries show
`未采集` instead of fabricating zeroes.

The default backend is deterministic and local. It writes reviewable JSON image-generation/editing artifacts under `data/artifacts/`. Brand-kit validation covers versioned palettes, typography, logo placement, and forbidden elements. Campaign manifest generation deterministically expands channel, format, asset, and copy combinations into draft variants with stable IDs and checksums.
Prompt-pack expansion validates a versioned template and variable matrix, then writes a deterministic manifest of uniquely identified rendered prompts. These are internal prompt artifacts, not generated images or evidence of visual quality.
The unit-economics command calculates internal low/base/high cost sensitivity from explicit assumptions. It does not set or authorize a price.
Product-shot workflow manifests validate required source provenance, explicit controls, ordered stages, and acceptance checks across at least three scenario inputs before writing deterministic internal workflow metadata.
Visual QA scorecards calculate pass/fail/stop results from explicitly measured edit-fidelity and brand-consistency observations. These tools do not measure images directly and do not measure or claim actual image quality.
Feedback capture rejects declared sensitive data and anti-abuse honeypots, requires explicit consent before retaining optional contact data, and binds submissions to product/workflow/artifact context. Feedback triage records acknowledgement-through-release states and requires backlog/release linkage for those claims; neither command authorizes outreach or publication.



## Read-only dashboard

The operations dashboard is a stdlib-only HTTP service that reads `data/company.sqlite3`, Git metadata, project docs, and local artifact files without mutating company state. It exposes four separate Chinese-labeled pages:

- `http://127.0.0.1:18080/management` for company daily management, tasks, approvals, cycles, audit, human dependencies, execution health, per-agent outcomes, and token usage charts/tables.
- `http://127.0.0.1:18080/project` for product/project status, Git version, roadmap, current tasks, experiments, artifacts, and validation evidence.
- `http://127.0.0.1:18080/operations` for pre-launch product operations placeholders. Unknown fields are shown as unavailable placeholders, not zeroes.
- `http://127.0.0.1:18080/company` for company introduction, mission, real-business operating principles, PixWeave product status, org chart, live SQLite roles/RACI, cadence, Chairman reserved decisions, Codex async-resource policy, and evidence/version/archive governance.

JSON endpoints:

- `http://127.0.0.1:18080/healthz`
- `http://127.0.0.1:18080/api/status`, including `company.roles`, `company.raci`, and management execution health derived from live SQLite.

Durable user service:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/agent-company-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agent-company-dashboard.service
```

The event worker has a separate user service template at
`deploy/agent-company-worker.service`. Its reviewed install and recovery procedure is
documented in `docs/event-worker-deployment.md`.

## Files

- `agent_company/`: package and CLI.
- `config/sample.ini`: configurable product, paths, backend, governance.
- `data/chairman/inbox`: CEO-only approval request files for Chairman.
- `data/chairman/outbox`: Chairman decision files.
- `docs/`: constitution, operating model, architecture, development assurance design, and plans.
- `docs/development-assurance-system-design.md`: Chairman-review draft for goal/design/spec/eval-driven product and control-plane development; it does not authorize implementation.
- `docs/templates/`: proposed versioned Goal, Design Manifest, Design, Behavior Spec, Eval, Baseline, Review, Change, Incident, and Release document templates.
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
