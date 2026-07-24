# AI-Native Development Assurance System

> Status: Design for Chairman review
> Version: v1.0-draft
> Scope: Agent Company control-plane development and PixWeave product development
> Decision gate: No implementation phase starts until this design is approved or explicitly amended

## 1. Executive Decision

The company adopts one shared development assurance kernel with two specialized profiles:

- `product-competitive`: for PixWeave product capabilities, user outcomes, subjective quality, and competitor comparison.
- `control-plane-reliability`: for Agent Company mechanisms, governance, state machines, safety, recovery, and auditability.

The kernel is shared because both systems need the same development truth chain:

```text
Goal -> Design -> Specification -> Evaluation Contract -> Baseline
     -> Bounded Implementation -> Independent Evaluation
     -> Release Decision -> Outcome Observation -> Learning
```

The profiles are intentionally different because PixWeave wins through user and market outcomes, while Agent Company must be correct under failure, adversarial behavior, concurrency, and governance constraints.

No task may enter implementation merely because it has a title, a feature idea, or a prose acceptance criterion. It must first have an approved Goal Contract, Design Record, Specification, and Evaluation Contract appropriate to its profile.

## 2. Problems This Design Solves

The previous development mode allowed an Agent to infer too much from a broad task description. That created five failure modes:

1. A business ambition was mistaken for a feature specification.
2. Acceptance prose was mistaken for an executable evaluation.
3. The implementing Agent could define and judge its own success.
4. Tests proved that code ran but not that the intended outcome was achieved.
5. No explicit baseline, non-goals, stop conditions, or release decision existed.

This design separates value judgment, system design, implementation, evaluation, and release authority while keeping feedback cycles short.

## 3. Design Principles

### 3.1 Goal before solution

A feature is an implementation hypothesis, not the goal. The goal describes who needs what progress and what measurable or observable outcome proves progress.

### 3.2 Design before implementation

A design document records the problem model, alternatives, boundaries, invariants, risks, and verification plan before code changes begin. It is not a retrospective explanation written after implementation.

### 3.3 Evaluation before implementation

The team must know how success and failure will be judged before the implementation Agent starts. Thresholds cannot be moved after seeing results without a versioned decision and rationale.

### 3.4 Hard gates plus graded quality

Security, correctness, data integrity, governance, and critical regressions are hard gates. They cannot be compensated for by a high weighted score elsewhere. Subjective quality and comparative performance may use weighted scores only after hard gates pass.

### 3.5 Independent verification

The implementing role cannot be the sole final evaluator. The evaluator receives the approved contract and evidence, not an invitation to redefine the goal.

### 3.6 Small vertical slices

Work proceeds through end-to-end tracer bullets. Each slice contains one user or control-plane scenario, its test/eval, minimal implementation, evidence, and review.

### 3.7 Evidence is typed and traceable

Every claim is labeled `measured`, `observed`, `estimated`, `target`, or `unknown`, and links to a source, timestamp, version, and evaluation run.

### 3.8 Human authority remains explicit

AI may expand scenarios, generate candidate rubrics, execute evaluations, identify risks, and propose decisions. The Chairman retains strategy, capital, identity, legal, public release, production, material pricing, sensitive data, and irreversible-action authority.

## 4. Shared Assurance Kernel

### 4.1 Lifecycle states

Every material initiative, change, or task follows this state machine:

```text
idea
  -> discovery
  -> goal_review
  -> design_draft
  -> design_review
  -> spec_ready
  -> eval_ready
  -> baseline_recorded
  -> approved_for_build
  -> implementation
  -> independent_evaluation
  -> release_candidate
  -> enabled_or_deployed
  -> outcome_observation
  -> closed
```

Allowed failure and return paths:

```text
design_review -> discovery          # problem or scope unclear
eval_ready -> design_draft          # evaluation exposes a design gap
independent_evaluation -> implementation # bounded remediation
independent_evaluation -> design_draft   # target or architecture is wrong
any pre-release state -> cancelled   # obsolete, unsafe, or superseded
any state -> blocked                  # missing Chairman decision or required evidence
```

A state transition is durable, audited, and includes the actor, artifact versions, decision, and evidence references.

### 4.2 Core artifacts

| Artifact | Purpose | Owner | Required before |
|---|---|---|---|
| `GoalContract` | Defines desired outcome, user/value owner, scope, non-goals, and target | CEO with relevant product/control owner | design draft |
| `DesignRecord` | Defines problem model, alternatives, architecture, risks, and verification strategy | Responsible role | design review |
| `BehaviorSpec` | Defines observable behavior, interfaces, state transitions, and invariants | Responsible role | spec ready |
| `EvalContract` | Defines datasets, graders, thresholds, hard gates, and release rule | Responsible role; independent review | eval ready |
| `BaselineReport` | Records current system, competitor, or prior mechanism performance | Evaluation owner | approved for build |
| `ImplementationPlan` | Decomposes the design into bounded vertical slices | Implementing role | approved for build |
| `EvalRun` | Records actual execution, grader outputs, environment, and evidence | Evaluator | independent evaluation |
| `ReviewDecision` | Independent quality and risk conclusion | Reviewer | release candidate |
| `ReleaseDecision` | Determines whether to enable, deploy, continue beta, or stop | CEO/Chairman per authority | enabled/deployed |
| `OutcomeObservation` | Measures real-world impact after release | Product or Platform owner | closed |

### 4.3 Required artifact lineage

Every implementation and release must be traceable:

```text
release_decision
  -> review_decision
  -> eval_run(s)
  -> baseline_report
  -> eval_contract version
  -> behavior_spec version
  -> design_record version
  -> goal_contract version
  -> source directive / strategy version
  -> implementation commit(s)
```

A claim without this chain is not completion evidence.

### 4.4 Common gates

#### G0 Goal Review

Required answers:

- Who is the beneficiary or protected stakeholder?
- What job, risk, or company capability is being addressed?
- What observable outcome changes if successful?
- Why now?
- What is explicitly out of scope?
- What is the largest uncertainty?
- What would cause us to stop or change direction?

#### G1 Design Review

Required answers:

- What is the current system and failure/problem model?
- What alternatives were considered?
- Why is this design the smallest adequate solution?
- What state, interfaces, dependencies, and invariants change?
- What could go wrong, including partial failure and adversarial behavior?
- What is the rollback or safe-stop strategy?
- Which claims are assumptions rather than facts?

#### G2 Specification Review

Required answers:

- Can two independent implementers derive the same externally observable behavior?
- Are normal, boundary, error, recovery, and security scenarios covered?
- Are inputs, outputs, preconditions, postconditions, and invariants explicit?
- Does the specification identify non-goals and prohibited behavior?

#### G3 Evaluation Readiness

Required answers:

- Is there at least one test/eval that would fail before implementation?
- Is the baseline frozen and reproducible?
- Are thresholds and hard gates pre-committed?
- Is the evaluator independent from the implementer?
- Are hidden or holdout cases protected from the implementer?
- Is human calibration required and scheduled where quality is subjective?

#### G4 Build Approval

The CEO may dispatch only when G0-G3 pass and the task has a bounded scope, repository, owner, generation, context bundle, and evidence plan.

#### G5 Independent Evaluation

The evaluator runs the approved contract without silently changing it. Any contract defect is reported as a design/evaluation failure, not patched by changing the expected result.

#### G6 Release Decision

Release requires hard gates, regression protection, independent review, explicit residual risk, and the correct authority. Passing offline evaluation is not evidence of market success or production readiness.

#### G7 Outcome Observation

After enablement, actual usage, failures, user outcomes, incidents, cost, and support burden are measured. The result may graduate a capability evaluation into a regression evaluation or create a new failure scenario.

### 4.5 Change classification and proportionality

The assurance process is risk-scaled; it must prevent both under-specification and process theater.

| Class | Typical scope | Required artifacts and gates |
|---|---|---|
| `C0 editorial` | wording, comments, non-behavioral formatting | change intent, canonical checks, commit; no Design Record |
| `C1 bounded` | local behavior with no public contract, data, authority, or architecture change | lightweight Goal/Behavior/Eval sections may be one combined document; G0-G3 reviewed by accountable owner |
| `C2 material` | user-visible capability, persistent schema, workflow, public interface, quality target, cross-role behavior | separate Goal, Design, Spec, Eval, Baseline, independent review, full G0-G7 |
| `C3 critical` | authorization, secrets/data boundary, financial/legal/public/production action, recovery/fencing, irreversible migration, competitive/public claim | full C2 plus threat model, rollback rehearsal, independent specialist review, and Chairman approval where reserved |

Classification is based on impact and uncertainty, not diff size. Splitting a material change into small commits does not reduce its class. The CEO proposes the class; the independent reviewer may raise it. Lowering a proposed class requires an audited rationale. Emergency containment may use a narrow exception only to stop active harm: it must be reversible, cannot expand authority, must preserve evidence, and requires retrospective Goal/Design/Eval review before normal development resumes.

### 4.6 Gate decision records

Every gate decision records `pass`, `pass_with_conditions`, `return`, `blocked`, or `reject`; the deciding role; artifact hashes; conditions and expiry; and evidence. `Pass_with_conditions` cannot waive a hard gate and must name the later gate at which each condition becomes blocking. A gate does not pass through silence, elapsed time, or model self-report.

## 5. Design Document Mechanism

Design documents are part of the development system, not optional documentation.

### 5.1 Design document types

- `initiative-design`: product or company-level direction and outcome model.
- `capability-design`: a bounded product/control-plane capability.
- `architecture-decision`: a cross-cutting technical choice with alternatives and consequences.
- `evaluation-design`: grader, dataset, rubric, sampling, calibration, and statistical decision method.
- `release-design`: rollout, observability, rollback, risk controls, and post-release measurement.

### 5.2 Design document lifecycle

```text
draft -> owner_review -> cross_role_review -> approved -> superseded
                                  \-> rejected
```

A document is immutable after approval. Changes create a new version and record the superseded version. The approved document is mounted read-only into task context.

### 5.3 Minimum design document structure

```markdown
# Design: <name>

- ID: <stable-id>
- Type: capability-design | initiative-design | architecture-decision | evaluation-design | release-design
- Version: vN
- Status: draft | approved | superseded | rejected
- Owner: <role>
- Decision authority: <CEO or Chairman>
- Reviewers: <roles>
- Related goal: <goal-id>
- Related repository: <repository-id>
- Last updated: <timestamp>

## Decision summary
## Problem and evidence
## Target users/stakeholders
## Desired outcomes
## Non-goals
## Current state and baseline
## Proposed design
## Alternatives considered
## Interfaces and data model
## State transitions and invariants
## Failure, threat, and recovery model
## Evaluation strategy
## Rollout, rollback, and stop conditions
## Risks, assumptions, and unknowns
## Open decisions
## Evidence references
## Approval record
```

### 5.4 Design review rules

- The author must state what evidence is missing.
- The reviewer must challenge the problem framing, not only grammar or implementation detail.
- The implementation Agent may propose changes but cannot approve its own design.
- A design with unresolved material open decisions cannot enter `approved_for_build`.
- A design may be intentionally provisional, but its expiration date and learning experiment must be explicit.
- Design quality itself is evaluated: ambiguity, untestable claims, missing failure cases, and excessive scope are defects.

### 5.5 Design document storage

Git stores approved definitions:

```text
docs/development-assurance/
  system-design.md
  profiles/
    product-competitive.md
    control-plane-reliability.md
  templates/
    goal-contract.md
    design-record.md
    behavior-spec.md
    eval-contract.md
    baseline-report.md
    review-decision.md
    release-decision.md
```

Runtime SQLite stores state, approvals, links, hashes, and transition history. Large eval datasets, images, logs, and reports live in the evidence archive and are referenced by immutable IDs and checksums.

## 6. Product Profile: PixWeave

### 6.1 Product goal model

PixWeave goals use a JTBD/outcome structure:

```text
Target user + situation
  -> job to be done
  -> desired progress
  -> observable product outcome
  -> capability hypothesis
  -> scenario bank
  -> comparative and real-user evaluation
```

A product goal must not say only "add feature X" or "beat competitor Y". It must specify the wedge scenario where PixWeave intends to win.

### 6.2 Competitive objective definition

"Exceed a competitor" is valid only after fixing:

- target user and job;
- scenario and input set;
- competitor product/version/date/configuration;
- comparison dimensions;
- hard failures;
- minimum practical advantage;
- sample size and confidence rule;
- non-regression dimensions;
- data rights and test reproducibility.

Example objective:

```text
For small brand content operators producing a product image for a social channel,
PixWeave vX must achieve a higher blind preference than Competitor Y vZ on
brand consistency, subject fidelity, and one-pass usability, while maintaining
hard-failure rate <= baseline, median completion time <= target, and cost per
successful asset <= budget. The result is accepted only on the frozen holdout
scenario bank with the pre-registered comparison rule.
```

This is a scoped competitive claim, not a claim of universal superiority.

### 6.3 Product scenario bank

Each scenario contains:

- scenario ID and version;
- user/job description;
- source asset and rights/provenance;
- explicit brand constraints;
- input prompt/instructions;
- expected useful outcome;
- forbidden outcomes;
- competitor execution instructions;
- rubric and hard gates;
- known difficulty and failure tags.

The bank is divided into:

- development set: visible to implementers;
- regression set: stable known behavior;
- hidden holdout: protected from implementation Agents;
- adversarial set: edge cases, ambiguity, abuse, and failure recovery;
- post-release set: real failures converted into de-identified tests.

### 6.4 Product graders

Product evaluation combines:

1. Deterministic graders: file validity, dimensions, required controls, text exactness where applicable, workflow state, latency, cost, crashes, and provenance.
2. Visual/content graders: subject fidelity, composition, material realism, typography, brand consistency, edit controllability, and artifact defects using anchored rubrics.
3. Blind pairwise graders: randomized A/B comparison without product identity, with tie allowed.
4. Human calibration: expert gold set and periodic blind review of a sample.
5. Real-user graders: task completion, rework, acceptance, repeat use, satisfaction, and willingness to use/pay when authorized.

Subjective scores must include anchor examples and failure definitions. A raw 1-10 score without anchors is invalid.

### 6.5 Product release rule

```text
All hard gates pass
AND regression suite does not regress
AND capability target reaches threshold
AND comparative result meets pre-registered advantage/confidence rule
AND cost/latency stay within approved bounds
AND independent review passes
```

A single aggregate score cannot hide a critical failure in subject identity, text correctness, data protection, or workflow completion.

### 6.6 Competitive statistics and judge validity

The Eval Contract pre-registers the primary endpoint, minimum practical advantage, sampling unit, tie policy, repetition count, uncertainty method, and missing/failed-run treatment. Results report effect size and uncertainty, not only a point estimate. Scenario families are stratified so repeated variants of one source asset do not masquerade as independent evidence. Multiple prompts/seeds are treated as repeated measurements, not extra users.

Model judges are never assumed to be ground truth. Before use, they are tested against a human gold set for agreement, position/order bias, source identity leakage, verbosity/style bias, and sensitivity to irrelevant changes. Pairwise A/B order is randomized and, for material decisions, evaluated in both orientations. Material disagreement, low agreement, or drift triggers human adjudication and blocks competitive claims. Judge model, prompt, temperature/configuration, rubric, and calibration set versions are part of the immutable Eval Run.

### 6.7 Competitor evidence and reproducibility

Competitor evaluation must comply with access terms and asset rights. The Baseline Report records account/tier, version/date, settings, number of attempts, selection policy, manual intervention, failure handling, and all non-equivalent conditions. Cherry-picking the best PixWeave attempt against a default competitor attempt, or vice versa, is prohibited. If the competitor cannot be reproduced or compared fairly, the result is labeled exploratory and cannot support a public superiority claim.

## 7. Control-Plane Profile: Agent Company

### 7.1 Control-plane goal model

Company OS goals use:

```text
operational failure or governance risk
  -> protected invariant
  -> state machine / control behavior
  -> fault scenario bank
  -> deterministic and adversarial evaluation
  -> SLO/error budget or hard safety gate
```

Example: "improve execution continuity" becomes explicit invariants such as no duplicate live ownership, no stale-generation write, no false completion, complete audit lineage, and safe recovery under restart.

### 7.2 Control-plane specification requirements

Every material mechanism change must define:

- state machine and legal transitions;
- database constraints and transaction boundaries;
- authority and actor matrix;
- fencing/generation semantics;
- idempotency rules;
- event and audit lineage;
- context and secret boundaries;
- failure and threat model;
- recovery/quarantine behavior;
- migration and rollback plan;
- observability and SLOs.

### 7.3 Control-plane evaluation layers

1. Unit and integration tests for behavior.
2. Property/invariant tests for state and data correctness.
3. Concurrency tests for claims, leases, fencing, and duplicate events.
4. Fault injection for crashes, partial writes, restart, timeout, network/Git failure, stale process, PID reuse, and disk/resource exhaustion.
5. Security tests for authorization, repository isolation, context privacy, secrets, and prompt/data leakage.
6. Migration tests against legacy and current databases.
7. Architecture fitness functions for repository separation, review independence, approval gates, and audit completeness.
8. Real service tests for systemd, health, dashboard truthfulness, and recovery.
9. Independent Control & Reliability Review.

### 7.4 Control-plane release rule

```text
All safety and governance hard gates pass
AND all protected invariants pass
AND migration/rollback passes
AND failure/recovery scenarios pass
AND no audit or dashboard inconsistency exists
AND independent Control Review approves
AND Chairman approval exists for reserved or irreversible scope
```

Reliability SLOs may use error budgets. Safety, authorization, data leakage, and false-success metrics have zero tolerance and no compensating budget.

## 8. Roles and Separation of Duties

| Activity | Product | Company OS |
|---|---|---|
| Goal owner | CEO + Product Engineer + Customer & Revenue | CEO + Company Platform Engineer |
| Design author | Product Engineer | Company Platform Engineer |
| Evaluation author | Product Eval/Independent Quality Reviewer | Control & Reliability Reviewer with Platform input |
| Implementer | Product Engineer | Company Platform Engineer |
| Independent evaluator | Independent Quality Reviewer | Control & Reliability Reviewer |
| Release recommendation | CEO | CEO |
| Reserved release authority | Chairman | Chairman |

The evaluator must receive the approved artifact versions and execute them independently. If evaluator and implementer must collaborate, their actions, role changes, and final independent review must be recorded.

## 9. AI Agent Operating Protocol

### 9.1 Before implementation

The Agent receives a read-only context bundle containing:

- company constitution and public role map;
- profile rules;
- approved Goal Contract;
- approved Design Record and Behavior Spec;
- approved Eval Contract;
- baseline summary;
- task contract and repository policy;
- relevant history and open handoffs;
- explicit authority and prohibited actions.

### 9.2 During implementation

The Agent must:

- work only within the assigned repository and task workspace;
- follow the approved design and report deviations before acting;
- write failing tests/evals before production behavior changes where applicable;
- run vertical slices and canonical checks;
- preserve evidence and provenance;
- stop on ambiguity, scope expansion, stale context, or contract conflict;
- never edit hidden holdout data, thresholds, or evaluator logic without an approved design change.

### 9.3 After implementation

The Agent submits:

- implementation commit and clean-tree proof;
- test/eval run manifest;
- changed behavior and design deviations;
- known limitations and residual risks;
- continuity update and handoffs;
- recommendation only, never a self-approved release decision.

## 10. Metrics and Decision Policy

### 10.1 Common metrics

- lead time from approved build to reviewable candidate;
- rework rate after independent evaluation;
- first-pass hard-gate rate;
- regression rate;
- evidence completeness;
- ambiguity defects found after implementation;
- cost and token usage;
- time spent in blocked states;
- outcome improvement after enablement.

### 10.2 Product metrics

- scenario success rate;
- hard-failure rate;
- blind preference/win-tie-loss;
- rubric dimension scores and worst-percentile score;
- task completion time;
- revision count;
- cost per successful asset;
- real-user acceptance and repeat-use rate.

### 10.3 Company OS metrics

- false completion rate;
- duplicate live execution rate;
- stale-generation write attempts;
- audit completeness;
- context correctness and stale rejection rate;
- recovery time and safe quarantine rate;
- unclaimed task age;
- dashboard-to-ledger inconsistency rate;
- governance bypass attempts.

Metrics labeled `target` or `estimated` never count as `measured` outcome evidence.

## 11. Governance, Change, and Versioning

### 11.1 Immutable approved artifacts

Approved Goal, Design, Spec, Eval, Baseline, and Release artifacts are content-addressed and immutable. A change creates a new version and records:

- superseded artifact;
- reason;
- changed assumptions or thresholds;
- affected tasks/evals;
- re-baseline requirement;
- approval authority.

### 11.2 Changes that force re-approval

- target user/job or business outcome changes;
- competitor/version/dataset changes;
- rubric or threshold changes;
- hard-gate changes;
- state machine, schema, authorization, or data boundary changes;
- repository or deployment boundary changes;
- material scope expansion;
- changed Chairman directive or strategy;
- evaluator/implementer independence changes.

### 11.3 No silent drift

When an approved design or evaluation becomes stale, affected tasks are paused or marked stale. Agents cannot silently continue under old criteria.

### 11.4 Artifact access and holdout custody

Artifact metadata is visible according to governance need, but contents follow least privilege. All roles may see approved goals, design summaries, public specifications, gate status, and release decisions. The implementer sees development/regression scenarios but not hidden holdout contents or private judge keys. The independent evaluator or trusted evaluation runner holds hidden sets and materializes only aggregate/failure evidence allowed by the Eval Contract. The CEO and Chairman may audit protected contents; access is logged. Secrets and raw sensitive data are never stored in design artifacts or model context.

### 11.5 Evaluation contract defects and legitimate novelty

An implementation can expose that an Eval Contract is wrong or incomplete. The evaluator must preserve the original result, mark `contract_disputed`, and provide evidence. The contract cannot be edited to make the candidate pass. A separate review decides whether to reject the candidate, amend the contract and rerun every affected baseline/candidate, or accept an explicitly scoped exception. Creative outcomes that outperform the written scenario are therefore reviewable without weakening auditability.

## 12. Target Technical Architecture

### 12.1 Components and trust boundaries

```text
Chairman directives / company strategy
                 |
                 v
        Goal and Design Registry <---- Git-reviewed document artifacts
                 |
                 v
     Assurance Validator and Gate Engine <---- profile + risk-class policy
          |                 |
          |                 +----> SQLite lifecycle, approvals, hashes, audit
          v
     approved_for_build
          |
          v
   Task Context Compiler ----> read-only approved artifacts + task generation
          |
          v
  Sandboxed Implementation Runner
          |
          v
 Candidate + evidence manifest
          |
          v
 Trusted Evaluation Runner <---- protected holdout + immutable Eval Contract
          |
          v
 Independent Review Gate ----> Release Decision ----> controlled enablement
          |                                             |
          +---------------- Dashboard / audit <---------+
                                                        |
                                              Outcome Observation
```

Trust rules:

- Git review controls human-readable artifact definitions and templates.
- SQLite controls lifecycle state, authority, approvals, exact hashes, and transactional links.
- The Context Compiler may read only approved artifact versions selected by the Gate Engine.
- The implementation sandbox cannot write assurance definitions, evaluation policy, or hidden holdouts.
- The Evaluation Runner uses a separate workspace/capability and publishes signed or checksummed results.
- The task runner cannot mark a C2/C3 task complete until the Review Gate references a passing Eval Run.
- Release execution remains separate from release approval and follows existing reserved-action controls.

### 12.2 Proposed package boundaries

```text
agent_company/assurance/
  models.py          # typed artifact metadata and state enums
  schemas.py         # strict versioned JSON validation
  registry.py        # content-addressed artifact registration and lookup
  lifecycle.py       # legal transitions and gate decisions
  policy.py          # risk classification and profile requirements
  gates.py           # G0-G7 evaluation and blocking reasons
  evaluator.py       # eval run orchestration interface, not product graders
  lineage.py         # graph validation and stale-impact calculation
  access.py          # artifact visibility and holdout custody
  reporting.py       # redacted dashboard projections

config/assurance/
  profiles/product-competitive.json
  profiles/control-plane-reliability.json
  risk-policy.json

docs/assurance/
  goals/<id>/vN.md
  designs/<id>/vN.md
  specs/<id>/vN.md
  evals/<id>/vN.md
  baselines/<id>/vN.md
  reviews/<id>/vN.md
  releases/<id>/vN.md
```

Profile configuration declares required artifact kinds, gate owners, mandatory grader categories, hard-gate categories, and release authority. It cannot weaken constitution-level controls.

### 12.3 Proposed persistent model

The following is the minimum normalized model; large artifact bodies stay in Git/evidence storage.

```text
assurance_initiatives
  id, profile, risk_class, title, owner, status,
  strategy_version, directive_version, created_at, updated_at

assurance_artifacts
  id, initiative_id, kind, version, status, owner,
  repository_id, git_commit, relative_path, content_sha256,
  supersedes_artifact_id, approved_by, approved_at, created_at
  UNIQUE(id, version)

assurance_links
  from_artifact_id, relation, to_artifact_id
  UNIQUE(from_artifact_id, relation, to_artifact_id)

assurance_gate_decisions
  id, initiative_id, gate, decision, actor, rationale,
  conditions_json, expires_at, artifact_set_sha256, created_at

assurance_eval_runs
  id, initiative_id, eval_artifact_id, baseline_artifact_id,
  candidate_commit, environment_sha256, scenario_set_sha256,
  grader_set_sha256, status, hard_gate_passed, result_sha256,
  started_at, completed_at

assurance_review_decisions
  id, initiative_id, eval_run_id, reviewer, independence_json,
  decision, findings_sha256, residual_risk_json, created_at

assurance_release_decisions
  id, initiative_id, review_decision_id, authority,
  scope_json, decision, conditions_json, rollback_ref, created_at

assurance_task_bindings
  task_id, execution_generation, initiative_id,
  artifact_set_sha256, build_gate_decision_id,
  review_decision_id, status
  UNIQUE(task_id, execution_generation)

assurance_access_log
  id, actor, artifact_id, access_kind, purpose, created_at
```

Foreign keys and transactions enforce lineage. Approved artifacts are never updated in place. `superseded` is a state transition, not deletion. Artifact contents and an ordered artifact-set manifest are both hashed.

### 12.4 Task contract changes

A C2/C3 task must add:

```json
{
  "initiative_id": "...",
  "risk_class": "C2",
  "profile": "product-competitive",
  "goal_ref": {"id": "...", "version": 1, "sha256": "..."},
  "design_ref": {"id": "...", "version": 1, "sha256": "..."},
  "spec_ref": {"id": "...", "version": 1, "sha256": "..."},
  "eval_ref": {"id": "...", "version": 1, "sha256": "..."},
  "baseline_ref": {"id": "...", "version": 1, "sha256": "..."},
  "build_gate_decision_id": 123,
  "artifact_set_sha256": "..."
}
```

The existing prose `acceptance_criteria` remains a concise human summary but is no longer the source of truth for C2/C3 completion. C0/C1 tasks use the proportional policy and record why the lighter contract is sufficient.

### 12.5 Gate and staleness algorithm

Before dispatch, the Gate Engine performs, in order:

1. Validate artifact schemas and profile/risk requirements.
2. Resolve exact approved versions and verify content hashes.
3. Validate links Goal -> Design -> Spec -> Eval -> Baseline.
4. Verify required reviewers, authority, and independence.
5. Verify G0-G3 decisions have no expired blocking condition.
6. Compare strategy/directive/repository/task contract versions.
7. Compute the ordered `artifact_set_sha256`.
8. Bind it to task ID, execution generation, and fencing token.
9. Emit `approved_for_build` and allow Context Compiler materialization.

Before candidate publication and again before completion, the Runner recalculates the binding. Any changed artifact, strategy/directive, task contract, evaluation threshold, baseline, reviewer assignment, or execution generation marks the binding stale. The task stops safely and returns to the appropriate gate; it does not auto-recompile under changed intent.

### 12.6 Evaluation execution contract

The Evaluation Runner accepts only:

```text
candidate immutable reference
approved Eval Contract reference
approved Baseline reference
protected scenario-set reference
approved grader-set reference
environment manifest
run budget and repetition policy
```

It emits a content-addressed Eval Run containing per-scenario outcomes, aggregate statistics, hard-gate results, grader/version metadata, logs, costs, failures, and uncertainty. Failed/incomplete runs remain evidence and cannot be deleted or converted to pass. Product visual graders are product-repository adapters; control-plane fault graders are control-plane adapters. The shared kernel coordinates and verifies them but does not pretend one generic score fits both.

### 12.7 Transaction boundaries and failure behavior

- Artifact registration and approval are separate transactions; registration cannot imply approval.
- A gate decision commits only with the exact artifact-set hash it reviewed.
- Task binding and transition to `approved_for_build` commit atomically.
- Eval Run finalization commits only after its evidence manifest is durably checksummed.
- Review decision cannot reference a running/incomplete Eval Run.
- Task completion and binding of the passing Review Decision commit atomically.
- A Git push, evidence write, database write, or notification failure is never translated into success; recovery uses idempotency keys and preserves unknown/quarantine states where external outcome is ambiguous.

### 12.8 CLI and read-only dashboard surface

Proposed bounded commands:

```text
assurance-initiate
assurance-artifact-register
assurance-artifact-inspect
assurance-gate-review
assurance-baseline-record
assurance-eval-run
assurance-review-record
assurance-release-decide
assurance-lineage
assurance-stale-impact
```

All mutating commands require explicit actor and validate role authority. Dashboard views show initiative state, risk/profile, artifact versions/hashes, gate decisions, blocking conditions, eval summaries, reviewer independence, residual risks, and release/outcome status. Hidden scenarios, private rubrics/keys, sensitive evidence, secrets, and full role-private notes remain redacted.

## 13. Implementation Sequence After Approval

This section is a proposed implementation plan, not an authorization to start.

### Phase A: Artifact and governance foundation

- Add schemas and validators for Goal, Design, Spec, Eval, Baseline, Review, and Release artifacts.
- Add document templates and version/hash/provenance rules.
- Add lifecycle states and audit transitions.
- Make task creation require an approved contract reference.

### Phase B: Profile enforcement

- Implement Product Profile validation and scenario/eval manifests.
- Implement Control-Plane Profile validation, invariants, fault scenarios, and architecture fitness manifests.
- Add independent evaluator assignment and hidden-holdout protection.

### Phase C: Runner and CEO integration

- Compile approved artifacts into task context.
- Prevent dispatch before `approved_for_build`.
- Prevent completion before independent evaluation.
- Add stale artifact and threshold-change fencing.
- Add dashboard views for lifecycle, artifact lineage, gates, and unresolved risks.

### Phase D: Pilot and calibration

- Pilot one PixWeave wedge scenario.
- Pilot one Company OS mechanism change.
- Compare time, cost, failure rate, rework, and quality against current workflow.
- Calibrate model graders against human experts.
- Revise the design only through versioned change decisions.

### Phase E: Organization-wide adoption

- Convert new material work to the assurance lifecycle.
- Migrate only active/high-value work first.
- Keep historical tasks as legacy records with explicit missing-artifact status.
- Add periodic governance review and outcome audits.

## 14. Open Decisions for Chairman Review

1. Which single PixWeave wedge scenario should be the first Product Profile pilot?
2. Which one Company OS mechanism should be the first Control-Plane Profile pilot?
3. What is the acceptable balance between evaluation rigor, cost, and development speed for the pilot?
4. Which product quality dimensions are hard gates versus weighted dimensions?
5. Which human experts or reviewers will form the initial calibration set?
6. Should `approved_for_build` be a mandatory gate for every change immediately, or first apply to material changes above a defined risk threshold?
7. What competitive claim is strategically meaningful: scenario superiority, user outcome superiority, or cost/quality combination?

## 15. Acceptance Criteria for This Design

This design is ready for implementation only if the Chairman confirms:

- the shared kernel and two profiles are understood and accepted;
- design documents are mandatory artifacts with a versioned lifecycle;
- goals, specs, evaluations, and baselines are separate objects;
- hard gates cannot be offset by weighted scores;
- independent evaluation and human authority boundaries are preserved;
- the product and control-plane pilot choices are identified;
- unresolved strategic choices are recorded rather than guessed.

## 16. Research Basis And Adaptation

This design synthesizes rather than copies the following practices:

- Anthropic, *Demystifying evals for AI agents*: capability versus regression evals; code-, model-, and human-based graders; realistic task banks; grader calibration; transcript and outcome evaluation.
- Anthropic, *Building effective agents*: use the simplest sufficient workflow; evaluator-optimizer and orchestrator-worker patterns; ground agents in environment feedback; test tools and interfaces.
- Thoughtworks, *Spec-driven development*: specifications define external behavior, constraints, and interfaces; planning and implementation are separated; deterministic CI remains necessary to prevent spec drift.
- Google SRE Workbook, *Implementing SLOs*: user-centered indicators, stakeholder-owned targets, error budgets that change prioritization, and continuous refinement rather than decorative metrics.
- TDD/BDD and evolutionary architecture: failing behavior tests before implementation, Given/When/Then examples, and architecture fitness functions that continuously protect non-functional characteristics.
- JTBD/Outcome-Driven product thinking: define progress in a user circumstance before selecting features.

The adaptation is deliberate: open-ended product quality uses calibrated comparative evaluation, while control-plane correctness uses invariants and faults. Neither practice alone is accepted as a universal development method.

Primary references:

- https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- https://www.anthropic.com/engineering/building-effective-agents
- https://www.thoughtworks.com/en-us/insights/blog/agile-engineering-practices/spec-driven-development-unpacking-2025-new-engineering-practices
- https://sre.google/workbook/implementing-slos
- https://platform.openai.com/docs/guides/evals

Until Chairman approval, this document remains a reviewed design draft and must not be treated as authorization to modify the runtime or product repositories.
