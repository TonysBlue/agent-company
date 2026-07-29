#!/usr/bin/env python3
"""Run the frozen, local-only Phase D Stage D0 baseline replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_company.phase_d_d0 import (
    ALLOWED_REPOSITORIES,
    D0Error,
    aggregate_results,
    canonical_json,
    comparator_commits,
    elapsed_ms,
    iso_timestamp,
    load_json,
    parse_regression_test_count,
    render_report,
    repository_commit,
    repository_status,
    run_case,
    sha256_file,
    tooling_hashes,
    utc_now,
    validate_freeze_chronology,
    validate_replay_cases,
    verify_frozen_inputs,
    write_json,
)


DEFAULT_FREEZE = ROOT / "docs" / "assurance" / "phase-d" / "d0" / "freeze-manifest-v1.json"
DEFAULT_OUTPUT = ROOT / "evidence" / "phase-d" / "d0"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def freeze_artifact(freeze: dict[str, object], kind: str) -> str:
    artifacts = freeze["artifacts"]
    assert isinstance(artifacts, list)
    matches = [entry for entry in artifacts if isinstance(entry, dict) and entry.get("kind") == kind]
    if len(matches) != 1 or not isinstance(matches[0].get("path"), str):
        raise D0Error(f"freeze manifest must contain exactly one {kind}")
    return str(matches[0]["path"])


def materialize_frozen_repositories(
    temporary_root: Path,
    commits: dict[str, str],
) -> dict[str, Path]:
    repositories: dict[str, Path] = {}
    for repository_id, source in ALLOWED_REPOSITORIES.items():
        destination = temporary_root / repository_id
        completed = subprocess.run(
            ["git", "clone", "--quiet", "--no-checkout", str(source), str(destination)],
            check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            raise D0Error(f"cannot create frozen repository copy: {repository_id}")
        completed = subprocess.run(
            ["git", "checkout", "--quiet", "--detach", commits[repository_id]],
            cwd=destination, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0 or repository_commit(destination) != commits[repository_id]:
            raise D0Error(f"cannot checkout frozen comparator commit: {repository_id}")
        if repository_status(destination) != "clean":
            raise D0Error(f"frozen comparator copy is dirty: {repository_id}")
        repositories[repository_id] = destination
    return repositories


def verify_regression_counts(
    repositories: dict[str, Path],
    comparator: dict[str, object],
    output: Path,
) -> dict[str, int]:
    bindings = comparator["repositories"]
    assert isinstance(bindings, dict)
    observed: dict[str, int] = {}
    for repository_id, repository in repositories.items():
        entry = bindings[repository_id]
        assert isinstance(entry, dict)
        command = entry.get("regression_command")
        expected = entry.get("expected_regression_tests")
        if command != "python3.11 -m unittest discover -s tests -v" or not isinstance(expected, int):
            raise D0Error(f"comparator regression protocol is invalid: {repository_id}")
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=repository, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        log_path = output / f"{repository_id}-comparator-regression.txt"
        log_path.write_text(completed.stdout, encoding="utf-8")
        count = parse_regression_test_count(completed.stdout)
        if completed.returncode or count != expected:
            raise D0Error(
                f"comparator regression mismatch for {repository_id}: expected {expected}, observed {count}"
            )
        observed[repository_id] = count
    return observed


def build_evidence_manifest(
    output: Path,
    run: dict[str, object],
    frozen: dict[str, str],
) -> dict[str, object]:
    artifacts = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "evidence-manifest.json":
            artifacts.append({
                "path": str(path.relative_to(output)),
                "sha256": sha256_file(path),
            })
    return {
        "schema_version": "phase-d-d0-evidence-manifest/v1",
        "run_id": run["run_id"],
        "scope": "Phase D Stage D0 only",
        "frozen_inputs": frozen,
        "repositories": run["repositories"],
        "artifacts": artifacts,
        "external_actions": ["git push only"],
        "stage_gates": {"d0": "baseline_reviewed", "d1": "started", "d2": "started"},
    }


def main() -> int:
    args = parse_args()
    freeze_path = args.freeze.resolve()
    output = args.output.resolve()
    if freeze_path != DEFAULT_FREEZE.resolve():
        raise D0Error("only the approved D0 freeze manifest may be executed")
    if output != DEFAULT_OUTPUT.resolve():
        raise D0Error("D0 evidence output must be evidence/phase-d/d0")
    output.mkdir(parents=True, exist_ok=True)
    preparation_started = utc_now()
    frozen = verify_frozen_inputs(ROOT, freeze_path)
    freeze = load_json(freeze_path)
    if freeze.get("stage_gates") != {"d0": "authorized", "d1": "start_authorized", "d2": "start_authorized"}:
        raise D0Error("freeze manifest stage gates do not match the Chairman decision")
    freeze_sha256 = sha256_file(freeze_path)
    product = load_json(ROOT / freeze_artifact(freeze, "scenario_bank"))
    controls = load_json(ROOT / freeze_artifact(freeze, "fault_bank"))
    comparator = load_json(ROOT / freeze_artifact(freeze, "comparator"))
    cases = validate_replay_cases(product, controls)
    repositories = comparator_commits(comparator)
    timeouts = comparator.get("timeouts")
    if not isinstance(timeouts, dict) or not isinstance(timeouts.get("case_timeout_seconds"), int):
        raise D0Error("comparator must define an integer case_timeout_seconds")
    case_timeout_seconds = int(timeouts["case_timeout_seconds"])
    if case_timeout_seconds <= 0 or case_timeout_seconds > 600:
        raise D0Error("case_timeout_seconds must be between 1 and 600")
    if repository_status(ALLOWED_REPOSITORIES["pixweave"]) != "clean":
        raise D0Error("PixWeave worktree must be clean before D0 replay")
    with tempfile.TemporaryDirectory(prefix="phase-d-d0-") as tmp:
        frozen_repositories = materialize_frozen_repositories(Path(tmp), repositories)
        regression_counts = verify_regression_counts(frozen_repositories, comparator, output)
        preparation_ended = utc_now()
        started_at = utc_now()
        validate_freeze_chronology(ROOT, freeze, iso_timestamp(started_at))
        results = [
            run_case(
                case, output, freeze_sha256=freeze_sha256,
                repository_paths=frozen_repositories,
                timeout_seconds=case_timeout_seconds,
            )
            for case in cases
        ]
    ended_at = utc_now()
    run: dict[str, object] = {
        "schema_version": "phase-d-d0-run/v1",
        "run_id": "phase-d-d0-baseline-v1",
        "scope": "Phase D Stage D0 only",
        "started_at": iso_timestamp(started_at),
        "ended_at": iso_timestamp(ended_at),
        "artifact_preparation": {
            "started_at": iso_timestamp(preparation_started),
            "ended_at": iso_timestamp(preparation_ended),
            "elapsed_ms": elapsed_ms(preparation_started, preparation_ended),
        },
        "freeze_manifest_sha256": freeze_sha256,
        "repositories": repositories,
        "regression_test_counts": regression_counts,
        "timing_scope": "host_local",
        "governance": {
            "independent_review": "approve",
            "chairman_confirmation": "confirmed_all_five_items",
        },
        "tooling_sha256": tooling_hashes(ROOT),
        "attempt_budget_per_case": 1,
        "case_count": len(results),
        "results_sha256": hashlib.sha256(canonical_json(results).encode("ascii")).hexdigest(),
        "stage_gates": {"d1": "start_authorized", "d2": "start_authorized"},
    }
    summary = aggregate_results(results)
    write_json(output / "case-results.json", {
        "schema_version": "phase-d-d0-case-results/v1",
        "run": run,
        "results": results,
    })
    write_json(output / "metrics-summary.json", {
        "schema_version": "phase-d-d0-metrics/v1",
        "run_id": run["run_id"],
        "metrics": summary,
    })
    (output / "baseline-report.md").write_text(
        render_report(run=run, summary=summary, results=results), encoding="utf-8"
    )
    manifest = build_evidence_manifest(output, run, frozen)
    write_json(output / "evidence-manifest.json", manifest)
    failed = [result for result in results if result["probe_result"] != "pass"]
    print(json.dumps({
        "run_id": run["run_id"],
        "cases": len(results),
        "failed": len(failed),
        "evidence": str(output.relative_to(ROOT)),
        "d1": "start_authorized",
        "d2": "start_authorized",
    }, indent=2, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except D0Error as exc:
        print(json.dumps({"error": str(exc), "stage": "D0", "d1": "blocked", "d2": "blocked"}))
        raise SystemExit(2)
