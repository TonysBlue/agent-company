# Development Design and Assurance Policy

Version: company-context/v1-draft
Status: proposed; effective only after Chairman approval

Material product and company-control-plane development uses one shared assurance lifecycle with separate product-competitive and control-plane-reliability profiles. A feature title or prose acceptance criterion is not sufficient authorization to build.

Before implementation, work must have an approved Goal Contract, Design Record, Behavior Specification, Eval Contract, reproducible baseline, bounded implementation plan, repository/role assignment, and evidence plan. The design must state non-goals, alternatives, invariants, failure/recovery behavior, risks, unknowns, rollback, and stop conditions.

Design documents are versioned governance artifacts. Approved versions are immutable and mounted read-only into task context. Material changes create a new version, record what was superseded, identify affected tasks, and require re-review or re-baselining. Stale or conflicting design/evaluation context pauses affected work.

Implementation Agents cannot approve their own design or release, modify hidden holdouts, silently move thresholds, or treat passing unit tests as proof of user, competitive, production, or commercial success. Hard safety, correctness, authorization, data, audit, and critical-regression gates cannot be offset by weighted quality scores.

Product work is judged through user outcomes, scenario banks, deterministic checks, anchored rubrics, blind pairwise comparison, human calibration, cost/performance constraints, and real-user observation. Control-plane work is judged through state-machine invariants, transaction and authorization rules, concurrency, failure injection, migration/recovery, architecture fitness functions, real-service verification, and independent Control Review.

No implementation is authorized by this draft policy or its parent design until the Chairman approves the design and selects the staged pilot scope.
