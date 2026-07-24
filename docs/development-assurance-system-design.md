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

Every material initiative, change, or task follows a governed lifecycle. `blocked` is an overlay with an explicit blocker and resume target; it is not a dead-end lifecycle state.

```text
idea -> discovery -> goal_review
     -> design_draft -> design_review
     -> spec_ready -> eval_contract_approved
     -> baseline_recorded -> approved_for_build
     -> implementation -> independent_evaluation
     -> release_candidate -> release_decision
     -> enabled_or_deployed -> outcome_observation -> closed
```

Decision and recovery paths:

```text
goal_review/design_review/spec_ready -> prior authoring state
independent_evaluation -> implementation        # bounded remediation is valid
independent_evaluation -> design_draft          # design/goal/eval premise is defective
independent_evaluation -> evaluation_rejected   # no compliant remediation
release_decision -> release_rejected | release_approved | release_approved_conditional
release_approved[_conditional] -> enabled_or_deployed
release_approved_conditional -> release_expired # unmet or expired condition
any nonterminal state -> blocked -> recorded resume_state
any pre-release state -> cancelled
release_candidate/enabled_or_deployed/outcome_observation
  -> incident_declared -> rollback_in_progress
  -> rolled_back | disabled | incident_resolved
rolled_back/disabled/incident_resolved -> reopened -> discovery | design_draft
outcome_observation -> reopened                 # outcome target missed or new harm found
```

`closed`, `cancelled`, `release_rejected`, `evaluation_rejected`, `rolled_back`, and `disabled` are terminal for that version but preserve evidence; a new or `reopened` initiative/version is required to resume. A release decision and each condition have an expiry. Conditional approval cannot waive a hard gate.

A complete transition rule records:

| Transition category | Guard | Required artifact/evidence | Authority |
|---|---|---|---|
| authoring forward | previous gate predicates pass | exact approved artifact hashes | designated gate approver |
| return/remediation | finding identifies failed goal/spec/eval | Review Decision and bounded remediation scope | independent reviewer |
| blocked/resume | blocker and exact `resume_state` recorded/resolved | Blocker Decision and resolution evidence | accountable owner; reserved blocker by Chairman |
| release approve/reject | G6 predicates and residual risks complete | Review Decision + Release Decision | CEO internally; Chairman for reserved scope |
| incident/rollback/disable | active harm, hard-gate breach, or rollback threshold | Incident Record and immutable observed evidence | incident commander within pre-authorized containment; Chairman notified for reserved scope |
| reopen | new evidence invalidates closure/release premise | Outcome/Incident/Change Decision | CEO or original decision authority |

Remediation loops are bounded by the Eval Contract, defaulting to at most two candidate attempts after the first independent failure. Exceeding the limit returns the initiative to `design_draft` or `evaluation_rejected`; it cannot continue optimizing indefinitely against the holdout.

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

Every implementation and release is represented by a typed provenance DAG, not merely a display chain:

```text
directive/strategy
  -> Goal Contract
  -> Design Manifest -> Design/ADRs -> Behavior Spec -> Eval Contract -> Baseline Run
  -> source commit(s) + dependency lockfile + build recipe
  -> immutable candidate (commit/image/package + candidate manifest)
  -> evaluation environment + scenario/data snapshot + sampling manifest
  -> grader code/model/prompt/config + seeds + Eval Run(s)
  -> Review Decision -> Release Decision -> deployment/enablement record
  -> Outcome Observation -> Incident/Change Decision when applicable
```

Each node and edge records immutable ID, version, SHA-256, timestamp, producing principal, source repository or archive, and signature/audit reference. Candidate provenance includes commit, uncommitted-tree status, dependency lockfile, tool/model versions, build command, artifact digest, configuration, and deployed release ID. Eval provenance includes environment digest, dataset/scenario snapshot, rights/provenance, sampling and random seeds, grader implementation/model/prompt/configuration, attempt number, and all candidate attempts in the approved budget. The Release Decision binds the exact candidate and deployment record; a passing source commit alone is not a release.

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

Classification is based on impact and uncertainty, not diff size. Splitting a material change into small commits does not reduce its class. The CEO proposes the class; the independent reviewer may raise it. Lowering a proposed class requires an audited rationale. Emergency containment is a governed path, not a waiver from evidence. It is limited to stopping active harm, disabling a capability, rollback, credential revocation, isolation, or restoring the last known safe state. It cannot launch new functionality, expand authority, change an evaluation threshold, erase evidence, perform unapproved public communication, or convert an unknown outcome into success.

The incident commander must be a stable authorized principal, record the incident and exact scope before acting when feasible, and obtain two-principal approval for C3 containment when a second qualified principal is available. If delay would materially increase harm, one authorized principal may act within a pre-approved containment playbook and must notify CEO/Chairman immediately. Break-glass authority expires after 60 minutes by default; renewal is explicit and audited. Enhanced logs, before/after state, commands/actions, artifacts, and affected data are retained. A retrospective Goal/Design/Spec/Eval or incident-design amendment is due within 24 hours, and normal development remains blocked until independent review closes it.

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

### 5.6 Design Manifest and dependency invalidation

Each C2/C3 initiative has one approved Design Manifest that enumerates every governing document, exact version/hash, dependency edge, required profile/risk class, approval, and applicability. Cardinality rules are:

- exactly one active Goal Contract;
- exactly one primary Capability/Initiative Design;
- zero or more Architecture Decisions, each with explicit scope;
- exactly one active Behavior Specification per independently releasable capability;
- exactly one active Eval Contract for each release decision endpoint;
- exactly one accepted Baseline per active Eval Contract/comparator set;
- one Release Design for C3 and for any external/production rollout;
- one Evaluation Design whenever model/human graders, hidden holdouts, or a competitive claim are used.

The dependency DAG, not file naming, controls impact. Superseding a Goal invalidates all dependent designs/specs/evals/baselines. Superseding an architecture decision invalidates dependent designs/specs and any eval whose environment or behavior premise changes. Superseding a rubric, grader, dataset, comparator, sampling rule, or threshold invalidates affected baselines and Eval Runs. Cross-repository dependencies require both repository owners and the relevant independent reviewer. Conflicting approved documents fail closed until a Change Decision establishes precedence and supersession.

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

### 6.8 Standard subjective-evaluation protocol

For a C2/C3 subjective product decision:

- presentation is blinded, randomized, and balanced; pairwise order is reversed on a controlled subset;
- each item receives at least two independent judgments; disagreement, low confidence, or a hard-gate concern receives a third qualified adjudication;
- scenario/rater sampling covers target workflow, difficulty, accessibility, language, cultural/brand context, and known failure strata identified in the Goal Contract;
- rater qualification, attention/canary checks, duplicate consistency, fatigue limits, and calibration drift are measured;
- the Eval Contract sets minimum inter-rater agreement and model-to-human agreement; falling below either invalidates automated conclusions rather than lowering the threshold;
- adjudicators do not see product identity or prior aggregate scores, and overrides record reason/evidence;
- competitor and PixWeave receive equivalent instruction, attempt, time, tool, and manual-intervention budgets unless a non-equivalence is itself the object of study;
- user studies require consent, data minimization, withdrawal/deletion route, eligibility/exclusion criteria, adverse-event stop rules, and Chairman approval before external recruiting or outreach.

### 6.9 Product statistical standard

The Evaluation Design pre-registers:

- the primary estimand and no more than a small declared set of secondary endpoints;
- unit of analysis and clustering by source asset, scenario family, user, rater, and repeated generation;
- minimum practical superiority or non-inferiority margin;
- expected baseline, power/sample-size rationale, alpha/error control, and uncertainty method;
- multiplicity correction across dimensions/comparators;
- handling of ties, failed generations, abstentions, missing-not-at-random outcomes, and protocol violations;
- fixed versus sequential design, permitted interim looks, stopping boundary, and maximum attempts.

Default for an initial pairwise pilot is a stratified win/tie/loss analysis with source/scenario clustered bootstrap confidence intervals and two independent ratings plus adjudication. A competitive pass requires the lower confidence bound to exceed the pre-registered practical margin on the primary endpoint, all hard gates to pass, and every protected dimension to meet its non-inferiority margin. The Design may choose a hierarchical model when repeated users/raters justify it, but the choice is frozen before candidate evaluation. Exploratory small samples may guide design but cannot support a superiority or release claim.

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

### 7.5 Reliability evidence semantics

`Zero tolerance` is a decision policy, not a statistical claim that finite testing proves zero universal risk. Safety, authorization, privacy, false-success, and audit-integrity violations are hard gates: any observed violation rejects the candidate. Evidence must distinguish:

- exhaustive proof/model checking over a declared finite state space;
- deterministic invariant/property checks over generated cases;
- sampled fault/concurrency testing with exposure count and coverage;
- production SLI/SLO observations over a stated time/event denominator.

Reports say `no violation observed in N cases/exposures under scope S` and, where relevant, provide a one-sided confidence bound. They do not say a risk is impossible unless backed by a valid proof whose assumptions are explicit. Availability/latency SLOs may use error budgets; constitutional safety invariants do not.

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

### 8.1 Stable principals and conflict rules

Authorization is enforced against a stable `principal_id` plus current role assignment, not a role label or a model persona. One principal cannot satisfy incompatible duties by switching prompts, sessions, models, or role names. Every approval records principal, role, session/executor identity, artifact set, and conflict declaration.

| Decision | C1 bounded | C2 material | C3 critical/reserved |
|---|---|---|---|
| risk classification | CEO; reviewer may raise | CEO + independent reviewer | CEO + specialist reviewer; Chairman for reserved scope |
| Goal approval | CEO | CEO; Chairman when strategy changes | Chairman |
| Design approval | accountable owner other than author | CEO + independent profile reviewer | CEO + specialist; Chairman for reserved architecture/risk choice |
| Eval Contract approval | owner + reviewer, neither sole implementer | independent evaluator-author + separate approver | independent evaluator-author + specialist/Chairman as applicable |
| Eval execution | non-author implementation executor allowed only for deterministic local check | trusted evaluator principal/runner | trusted evaluator principal/runner with protected custody |
| Review Decision | reviewer not involved in implementation | independent reviewer | independent reviewer plus required specialist/quorum |
| internal enablement | CEO | CEO | CEO, unless reserved |
| public/production/irreversible release | prohibited | Chairman | Chairman |
| residual hard-gate waiver | prohibited | prohibited | prohibited; redesign or containment only |
| rollback/disable | pre-authorized owner | incident commander + independent confirmation | break-glass rules and immediate CEO/Chairman notification |

Conflict rules:

- Design author cannot be the sole design approver.
- Implementer cannot approve the Eval Contract, execute protected holdout evaluation, issue the independent Review Decision, or authorize release.
- Eval Contract author may operate the trusted evaluator only if a different principal approves the contract and a different independent reviewer decides release quality.
- Material implementation assistance by a reviewer requires recusal and reassignment for final review.
- If no independent qualified principal is available, the initiative is blocked; convenience is not independence.
- Delegation names the principal, scope, expiry, and non-delegable Chairman powers. Absence does not transfer reserved authority.
- C3 review uses two qualified principals when available; dissent is preserved and escalated rather than averaged away.

### 8.2 RACI by artifact

| Artifact/action | Responsible | Accountable/approver | Consulted | Informed |
|---|---|---|---|---|
| Product Goal | Product Engineer + Customer & Revenue | CEO/Chairman by class | Independent Quality | all affected roles |
| Product Design/Spec | Product Engineer | CEO + Independent Quality | Customer & Revenue | Chairman |
| Product Eval Design/Contract | Independent Quality | CEO or separate qualified approver | Product Engineer, Customer & Revenue | Chairman |
| Control Goal | Company Platform Engineer | CEO/Chairman by class | Control Reviewer | affected roles |
| Control Design/Spec | Company Platform Engineer | CEO + Control Reviewer | affected repository owner | Chairman |
| Control Eval Contract | Control Reviewer | CEO or separate qualified approver | Platform Engineer | Chairman |
| Release/rollback | designated operator | CEO/Chairman by authority | independent reviewer/specialist | affected roles |

The evaluator must receive the approved artifact versions and execute them independently. Collaboration is allowed during discovery and design critique; protected evaluation and final review obey the stable-principal conflict rules.

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

### 10.4 Holdout and grader custody

Hidden holdouts, private rubric keys, and trusted grader code are assigned to a non-implementing custodian or isolated evaluation service. Custody records principal, storage boundary, encryption/key reference (never the secret), access purpose, expiry, and rotation history. Every read, materialization, run, and export is logged. Implementer access is denied by capability, not merely by prompt instruction.

An Eval Contract defines a maximum candidate-attempt budget and requires the evaluator to retain every attempted candidate/result, including failed and discarded outputs. Adaptive probing against holdouts is prohibited; repeated runs consume the budget. Canary leakage scenarios and contamination declarations are mandatory. If holdout or grader contamination is suspected, affected results are quarantined, the set is rotated or reconstituted, the baseline and candidates are rerun, and no superiority claim uses contaminated results.

The trusted evaluator accepts an immutable candidate reference and executes the same blinded protocol against all comparators within equivalent budgets. Implementers submit a candidate; they do not provide self-selected output evidence as the authoritative result.

### 10.5 Governance effectiveness metrics

The assurance system must prove value, not merely produce documents. In addition to gate completion and evidence completeness, the pilot and ongoing reviews measure:

- escaped material defects and severity-weighted defect escape;
- invalid approvals, stale-context executions, and unauthorized transitions;
- evaluator/implementer disagreement and human/model grader disagreement;
- holdout contamination and anti-gaming incidents;
- emergency-path usage, override count, and retrospective completion time;
- false-block and false-reject rate;
- reviewer workload, queue age, p50/p90 gate cycle time, and assurance cost per initiative;
- rework after independent review versus current-workflow baseline;
- defects prevented before implementation/release and outcome improvement per unit cost;
- percentage of claims with complete provenance and percentage of releases with successful rollback rehearsal.

A process change expands only if its measured defect-prevention and decision-confidence benefit justifies its latency and cost. A high artifact-completion rate alone is not success.

### 10.6 Capacity and service availability

The CEO owns a reviewer-capacity forecast. The Gate Engine, trusted evaluator, evidence archive, and reviewer queue have health indicators and SLOs. Service outage produces `assurance_unavailable` and blocks affected C2/C3 transitions; it never auto-approves. A pre-authorized manual review path may record the same predicates and hashes through a controlled form, with two-person verification for C3 and later reconciliation. Capacity shortfall is surfaced as a blocker and may narrow WIP; it is not solved by assigning the implementer as its own reviewer.

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

### 11.6 Change Decision and store reconciliation

Every material change to an approved node uses a `ChangeDecision` containing changed nodes, reason/evidence, principal/authority, dependency traversal, affected active tasks/candidates/Eval Runs/releases, rebaseline decision, migration/rollback, effective time, and grandfathered scope if any. Hard-gate, authority, target, comparator, threshold, or data-boundary changes fail closed: active task bindings are paused as stale and unfinished Eval Runs become invalidated evidence. Low-risk C0/C1 changes may be grandfathered only through an explicit decision whose rationale and expiry are audited. Revalidation uses a new artifact-set hash and new gate decisions; a stale binding never becomes valid merely because files are restored.

Source precedence is:

1. Git content at the registered commit is the definition body.
2. SQLite is the authoritative lifecycle/approval/link record and stores the expected Git/evidence hashes.
3. The evidence archive is authoritative for large immutable run/deployment/observation bodies referenced by SQLite.

All three must agree. Missing content, hash mismatch, unregistered Git change, approval without a matching body, or archive loss produces `integrity_conflict`, blocks transitions/releases, preserves the discrepancy, and requires an independently reviewed reconciliation record. No store silently overwrites another.

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

### Phase 0: Shadow mode and bootstrap charter

- Approve a temporary, versioned bootstrap charter that authorizes only building the assurance kernel and names its expiry, owners, reviewers, budget, and rollback.
- Classify changes C0-C3 manually and generate proposed artifact/gate records without blocking existing task dispatch.
- Measure reviewer capacity, p50/p90 lifecycle delay, artifact preparation time, disagreement, false blocks, escaped defects, and assurance cost.
- Test store reconciliation, staleness impact, emergency containment, and principal separation using fixtures.
- Exit only when the kernel can represent its own design and the independent reviewer accepts migration to enforced mode.

### Phase A: Artifact and governance foundation

- Add schemas and validators for Goal, Design Manifest, Design/ADR, Spec, Eval, Baseline, Review, Change, Incident, and Release artifacts.
- Add version/hash/provenance rules, stable principal identity, conflict checks, and access audit.
- Add lifecycle states, complete transition table, gate decisions, source reconciliation, and dependency invalidation.
- Register the current approved pilot artifacts; do not yet block C0/C1 work.

### Phase B: Profile and trusted evaluation enforcement

- Implement Product Profile manifests, subjective/statistical protocol, scenario custody, attempt budgets, and competitor baselines.
- Implement Control-Plane Profile invariants, fault scenarios, reliability evidence semantics, and architecture fitness manifests.
- Implement trusted evaluator assignment, hidden-holdout access controls, contamination response, and immutable candidate provenance.
- Enable blocking G0-G5 first for pilot C2/C3 initiatives only.

### Phase C: Runner and CEO integration

- Compile exact approved artifacts and artifact-set hash into task context.
- Prevent pilot C2/C3 dispatch before `approved_for_build`.
- Prevent completion before independent evaluation and Review Decision.
- Add stale artifact, changed-threshold, changed-authority, and execution-generation fencing.
- Add dashboard views for lifecycle, lineage, gate predicates, exceptions, unresolved risks, review independence, and governance effectiveness.

### Phase D: Two pilots and calibration

- Pilot one PixWeave wedge scenario and one Company OS mechanism.
- Freeze pilot budget, reviewer hours, cycle-time ceiling, evaluator attempt limit, and abort conditions before starting.
- Compare defect escape, rework, quality, cycle time, cost, reviewer load, false rejection, and outcome evidence against the current workflow.
- Calibrate model graders against qualified human ratings and report disagreement/drift.
- Abort or redesign if either pilot exceeds the approved cost/cycle ceiling without detecting material defects or improving decision confidence.

### Phase E: Risk-tier adoption

- Enforce C3 first, then C2 only after pilot evidence; keep C0/C1 proportional.
- Migrate active/high-value initiatives only; legacy records explicitly state missing assurance artifacts without fabricating them.
- Set gate service SLOs and a safe manual review path when the service is unavailable; unavailability never auto-passes work.
- Review emergency use, overrides, holdout health, reviewer capacity, and assurance ROI periodically.
- Expand only when escaped material defects, invalid approvals, and rework improve enough to justify measured process cost.

### 13.1 Pilot success and abort criteria

Before implementation approval, the Chairman/CEO fixes numerical pilot budgets after baseline measurement. At minimum, the pilot contract defines:

- maximum calendar duration, engineering/evaluator hours, model/token spend, and holdout attempts;
- maximum p90 wait at each gate and qualified reviewer capacity;
- success: complete lineage, no unauthorized transition, all seeded governance faults detected, at least one real ambiguity/defect found before implementation or release, and decision confidence/rework better than baseline;
- abort/pivot: gate deadlock, integrity conflict not safely recoverable, material hidden-set leakage, reviewer independence unavailable, process cost above ceiling without commensurate defects prevented, or product/control-plane throughput degradation beyond the approved margin;
- an explicit decision after each pilot: adopt, revise/repeat, or reject the mechanism.

## 14. Open Decisions for Chairman Review

1. Which single PixWeave wedge scenario should be the first Product Profile pilot?
2. Which one Company OS mechanism should be the first Control-Plane Profile pilot?
3. What numerical pilot budgets and cycle-time ceilings should be fixed after shadow-baseline measurement?
4. Which product quality dimensions are hard gates versus weighted dimensions?
5. Which qualified human principals form the initial calibration and adjudication pool?
6. What competitive primary estimand is strategically meaningful: scenario blind preference, real-user outcome, or a quality/cost frontier?
7. What minimum practical superiority and protected-dimension non-inferiority margins should the pilot pre-register?
8. What exact bootstrap charter expiry and Phase 0 exit deadline should be approved?

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
