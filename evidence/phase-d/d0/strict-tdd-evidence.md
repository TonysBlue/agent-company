# Phase D D0 Strict TDD Evidence

The D0 tooling was developed in small RED/GREEN cycles before the baseline run.

## RED 1: Frozen Inputs And Metrics Core

Command:

```text
python3.11 -m unittest tests.test_phase_d_d0 -v
```

Observed result before implementation: import failed with
`ModuleNotFoundError: No module named 'agent_company.phase_d_d0'`. The tests already
specified hash-drift rejection, bank minimums and uniqueness, raw-wait percentiles,
`not_collected`, defects, hard gates, human effort, false blocks, disagreement,
unauthorized transitions, and lineage completeness.

## RED 2: Report Gate

Command:

```text
python3.11 -m unittest tests.test_phase_d_d0 -v
```

Observed result before report implementation: import failed with
`ImportError: cannot import name 'render_report'`. The report test required D1/D2 to
remain `blocked` and independent review and Chairman confirmation to remain
`not_collected`.

## RED 3: Frozen Repository Replay

Command:

```text
python3.11 -m unittest \
  tests.test_phase_d_d0.PhaseD0BaselineTest.test_replay_can_run_against_a_frozen_detached_repository_path \
  -v
```

Observed result before detached-path support: `TypeError: run_case() got an unexpected
keyword argument 'repository_paths'`. The runner was then changed to execute from
temporary detached copies at the comparator commits instead of the mutable worktrees.

## GREEN

Final focused command:

```text
python3.11 -m unittest tests.test_phase_d_d0 -v
```

Result: 7 tests passed. The final Agent Company and PixWeave canonical regression
outputs are retained separately in this evidence directory.
