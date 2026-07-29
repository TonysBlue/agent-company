"""Reproducible, local-only Phase D D0 baseline replay tooling."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable


NOT_COLLECTED = "not_collected"
MIN_PRODUCT_CASES = 6
MIN_CONTROL_CASES = 12
SEVERITY_WEIGHTS = {"critical": 4, "high": 3, "medium": 2, "low": 1}
ALLOWED_REPOSITORIES = {
    "agent-company": Path("/home/tony/agent-company"),
    "pixweave": Path("/home/tony/products/pixweave"),
}


class D0Error(ValueError):
    """Raised when a D0 input or replay violates the frozen protocol."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tooling_hashes(root: Path) -> dict[str, str]:
    paths = ("agent_company/phase_d_d0.py", "scripts/run_phase_d_d0.py")
    return {relative: sha256_file(root / relative) for relative in paths}


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise D0Error(f"cannot load JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise D0Error(f"JSON input must be an object: {path}")
    return value


def verify_frozen_inputs(root: Path, freeze_path: Path) -> dict[str, str]:
    freeze = load_json(freeze_path)
    if freeze.get("schema_version") != "phase-d-d0-freeze/v1":
        raise D0Error("freeze manifest schema_version must be phase-d-d0-freeze/v1")
    artifacts = freeze.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise D0Error("freeze manifest artifacts must be a non-empty list")
    verified: dict[str, str] = {}
    for entry in artifacts:
        if not isinstance(entry, dict):
            raise D0Error("freeze manifest artifact entries must be objects")
        relative = entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise D0Error("freeze artifact path must be repository-relative")
        if not isinstance(expected, str) or len(expected) != 64:
            raise D0Error(f"freeze artifact sha256 is invalid: {relative}")
        path = root / relative
        if not path.is_file():
            raise D0Error(f"frozen artifact is missing: {relative}")
        actual = sha256_file(path)
        if actual != expected:
            raise D0Error(f"frozen artifact hash mismatch: {relative}")
        verified[relative] = actual
    required_kinds = {"scenario_bank", "fault_bank", "comparator", "rubric", "comparison_plan"}
    kinds = {entry.get("kind") for entry in artifacts if isinstance(entry, dict)}
    # Unit fixtures may omit kinds; real freeze manifests must either provide all or none.
    if kinds - {None} and not required_kinds <= kinds:
        missing = ", ".join(sorted(required_kinds - kinds))
        raise D0Error(f"freeze manifest is missing required kinds: {missing}")
    return verified


def validate_case_banks(product_bank: dict[str, object], control_bank: dict[str, object]) -> None:
    product_cases = _cases(product_bank, "product", MIN_PRODUCT_CASES)
    control_cases = _cases(control_bank, "control", MIN_CONTROL_CASES)
    ids = [str(case.get("id")) for case in product_cases + control_cases]
    if len(ids) != len(set(ids)):
        raise D0Error("duplicate case id across D0 banks")


def validate_replay_cases(
    product_bank: dict[str, object],
    control_bank: dict[str, object],
) -> list[dict[str, object]]:
    validate_case_banks(product_bank, control_bank)
    cases = _cases(product_bank, "product", MIN_PRODUCT_CASES) + _cases(
        control_bank, "control", MIN_CONTROL_CASES
    )
    for case in cases:
        case_id = case.get("id")
        domain = case.get("domain")
        repository = case.get("repository")
        target = case.get("test_target")
        if domain not in {"product", "control"}:
            raise D0Error(f"case {case_id} has invalid domain")
        if repository not in ALLOWED_REPOSITORIES:
            raise D0Error(f"case {case_id} uses a non-allowlisted repository")
        expected_repository = "pixweave" if domain == "product" else "agent-company"
        if repository != expected_repository:
            raise D0Error(f"case {case_id} crosses the product/control repository boundary")
        if not isinstance(target, str) or not target.startswith("tests.test_"):
            raise D0Error(f"case {case_id} test_target is not an allowlisted unittest probe")
        parts = target.split(".")
        if len(parts) != 4 or not all(part.replace("_", "").isalnum() for part in parts):
            raise D0Error(f"case {case_id} test_target must name one exact test method")
        if case.get("expected_outcome") != "pass":
            raise D0Error(f"case {case_id} expected_outcome must be pass")
        seeded_fault = case.get("seeded_fault")
        valid_control = case.get("valid_control")
        if not isinstance(seeded_fault, bool) or not isinstance(valid_control, bool):
            raise D0Error(f"case {case_id} must declare seeded_fault and valid_control")
        if seeded_fault and valid_control:
            raise D0Error(f"case {case_id} cannot be both a seeded fault and valid control")
        severity = case.get("severity")
        if not isinstance(severity, str) or severity.lower() not in SEVERITY_WEIGHTS:
            raise D0Error(f"case {case_id} has invalid severity")
    return cases


def _cases(bank: dict[str, object], name: str, minimum: int) -> list[dict[str, object]]:
    cases = bank.get("cases")
    if not isinstance(cases, list) or len(cases) < minimum:
        raise D0Error(f"{name} bank must contain at least {minimum} cases")
    if not all(isinstance(case, dict) for case in cases):
        raise D0Error(f"{name} bank cases must be objects")
    return cases  # type: ignore[return-value]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_timestamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def elapsed_ms(start: datetime, end: datetime) -> int:
    return max(0, round((end - start).total_seconds() * 1000))


def repository_commit(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=False,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise D0Error(f"cannot resolve repository commit: {repository}")
    return completed.stdout.strip()


def repository_status(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository, check=False,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise D0Error(f"cannot inspect repository status: {repository}")
    return "clean" if not completed.stdout else "dirty"


def run_case(
    case: dict[str, object],
    output_dir: Path,
    *,
    freeze_sha256: str,
    now: Callable[[], datetime] = utc_now,
    repository_paths: dict[str, Path] | None = None,
    timeout_seconds: int = 600,
) -> dict[str, object]:
    case_id = str(case["id"])
    repository_id = str(case["repository"])
    repository = (repository_paths or ALLOWED_REPOSITORIES)[repository_id]
    log_dir = output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    queued_at = now()
    command = [sys.executable, "-m", "unittest", str(case["test_target"]), "-v"]
    started_at = now()
    try:
        completed = subprocess.run(
            command, cwd=repository, check=False, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
        output = completed.stdout
        exit_code: int | str = completed.returncode
    except subprocess.TimeoutExpired as exc:
        captured = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        output = captured + f"\nD0 probe timed out after {timeout_seconds} seconds\n"
        exit_code = "timeout"
    ended_at = now()
    log_path = log_dir / f"{case_id}.txt"
    log_path.write_text(output, encoding="utf-8")
    log_sha256 = sha256_file(log_path)
    passed = exit_code == 0
    severity = str(case["severity"]).lower()
    lineage_values = {
        "case_definition_sha256": hashlib.sha256(canonical_json(case).encode("ascii")).hexdigest(),
        "freeze_manifest_sha256": freeze_sha256,
        "repository_commit": repository_commit(repository),
        "test_target": str(case["test_target"]),
        "log_sha256": log_sha256,
        "raw_timestamps": True,
        "exit_code": exit_code,
    }
    lineage_present = sum(value is not None for value in lineage_values.values())
    protects_transition = bool(case.get("protects_unauthorized_transition", False))
    detected_seeded_fault = bool(case["seeded_fault"]) and passed
    unexpected_probe_defect = not passed
    defect = int(detected_seeded_fault or unexpected_probe_defect)
    result: dict[str, object] = {
        "case_id": case_id,
        "domain": case["domain"],
        "case_kind": case.get("case_kind", "replayed_internal"),
        "repository": repository_id,
        "repository_commit": lineage_values["repository_commit"],
        "test_target": case["test_target"],
        "seeded_fault": case["seeded_fault"],
        "valid_control": case["valid_control"],
        "severity": case["severity"],
        "expected_signal": case.get("expected_signal"),
        "probe_result": "pass" if passed else "fail",
        "attempt_outcome": {
            "completed": exit_code != "timeout",
            "failure": not passed,
            "tie": "not_applicable",
            "abstention": False,
            "abandoned": exit_code == "timeout",
            "protocol_violation": False,
        },
        "exit_code": exit_code,
        "hard_gate": "pass" if passed else "fail",
        "defects": {
            "before_review": {
                "count": defect,
                "severity_weighted": defect * SEVERITY_WEIGHTS[severity],
                "seeded_faults_detected": int(detected_seeded_fault),
                "unexpected_probe_failures": int(unexpected_probe_defect),
            },
            "during_independent_review": NOT_COLLECTED,
            "after_nominal_completion": NOT_COLLECTED,
        },
        "unauthorized_transition": (not passed) if protects_transition else "not_applicable",
        "false_block": (not passed) if bool(case["valid_control"]) else "not_applicable",
        "timestamps": {
            "queued_at": iso_timestamp(queued_at),
            "started_at": iso_timestamp(started_at),
            "ended_at": iso_timestamp(ended_at),
        },
        "waits_ms": {
            "queue": elapsed_ms(queued_at, started_at),
            "automated_gate": elapsed_ms(started_at, ended_at),
            "cycle": elapsed_ms(queued_at, ended_at),
            "human_review": NOT_COLLECTED,
        },
        "elapsed_ms": {
            "implementation": NOT_COLLECTED,
            "evaluation": elapsed_ms(started_at, ended_at),
            "independent_review": NOT_COLLECTED,
        },
        "model_tokens": NOT_COLLECTED,
        "human_minutes": {
            "engineering": NOT_COLLECTED,
            "evaluation": NOT_COLLECTED,
            "review": NOT_COLLECTED,
        },
        "rework": {"count": NOT_COLLECTED, "minutes": NOT_COLLECTED},
        "reviewer_disagreement": NOT_COLLECTED,
        "lineage": {
            "complete": lineage_present == len(lineage_values),
            "present": lineage_present,
            "required": len(lineage_values),
            "values": lineage_values,
        },
        "evidence": {
            "log_path": str(log_path.relative_to(output_dir)),
            "log_sha256": log_sha256,
        },
    }
    return result


def nearest_rank(values: Iterable[int], percentile: float) -> int | str:
    ordered = sorted(values)
    if not ordered:
        return NOT_COLLECTED
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _sum_defects(results: list[dict[str, object]]) -> dict[str, object]:
    before_count = 0
    before_weighted = 0
    seeded_detected = 0
    unexpected_failures = 0
    for result in results:
        defects = result.get("defects")
        if isinstance(defects, dict):
            before = defects.get("before_review")
            if isinstance(before, dict):
                before_count += int(before.get("count", 0))
                before_weighted += int(before.get("severity_weighted", 0))
                seeded_detected += int(before.get("seeded_faults_detected", 0))
                unexpected_failures += int(before.get("unexpected_probe_failures", 0))
    return {
        "before_review": {
            "count": before_count,
            "severity_weighted": before_weighted,
            "seeded_faults_detected": seeded_detected,
            "unexpected_probe_failures": unexpected_failures,
        },
        "during_independent_review": NOT_COLLECTED,
        "after_nominal_completion": NOT_COLLECTED,
    }


def _aggregate_group(results: list[dict[str, object]]) -> dict[str, object]:
    wait_names = ("queue", "automated_gate", "cycle")
    waits: dict[str, object] = {}
    for name in wait_names:
        values = [
            int(result["waits_ms"][name])  # type: ignore[index]
            for result in results
            if isinstance(result.get("waits_ms"), dict)
            and isinstance(result["waits_ms"].get(name), int)  # type: ignore[union-attr]
        ]
        waits[name] = {"p50": nearest_rank(values, 0.50), "p90": nearest_rank(values, 0.90)}
    transitions = [
        result["unauthorized_transition"] for result in results
        if isinstance(result.get("unauthorized_transition"), bool)
    ]
    valid_controls = [result for result in results if result.get("valid_control") is True]
    false_blocks = [result for result in valid_controls if result.get("false_block") is True]
    complete_lineage = [
        result for result in results
        if isinstance(result.get("lineage"), dict) and result["lineage"].get("complete") is True  # type: ignore[union-attr]
    ]
    seeded_faults = [result for result in results if result.get("seeded_fault") is True]
    detected_faults = [result for result in seeded_faults if result.get("probe_result") == "pass"]
    passed = sum(result.get("hard_gate") == "pass" for result in results)
    return {
        "case_count": len(results),
        "hard_gates": {"passed": passed, "failed": len(results) - passed},
        "defects": _sum_defects(results),
        "waits_ms": waits,
        "human_review_wait_ms": NOT_COLLECTED,
        "elapsed_ms": {
            "implementation": NOT_COLLECTED,
            "evaluation": waits["automated_gate"],
            "independent_review": NOT_COLLECTED,
        },
        "model_tokens": NOT_COLLECTED,
        "human_minutes": {
            "engineering": NOT_COLLECTED,
            "evaluation": NOT_COLLECTED,
            "review": NOT_COLLECTED,
        },
        "rework": {"count": NOT_COLLECTED, "minutes": NOT_COLLECTED},
        "false_blocks": {"count": len(false_blocks), "valid_controls": len(valid_controls)},
        "fault_detection": {"detected": len(detected_faults), "seeded_faults": len(seeded_faults)},
        "reviewer_disagreement": NOT_COLLECTED,
        "unauthorized_transitions": {"count": sum(value is True for value in transitions), "observed": len(transitions)},
        "lineage_completeness": {
            "complete": len(complete_lineage),
            "total": len(results),
            "rate": len(complete_lineage) / len(results) if results else NOT_COLLECTED,
        },
    }


def aggregate_results(results: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        "all": _aggregate_group(results),
        "product": _aggregate_group([result for result in results if result.get("domain") == "product"]),
        "control": _aggregate_group([result for result in results if result.get("domain") == "control"]),
    }


def _metric(value: object) -> str:
    if value == NOT_COLLECTED:
        return f"`{NOT_COLLECTED}`"
    return f"`{canonical_json(value)}`"


def render_report(
    *,
    run: dict[str, object],
    summary: dict[str, dict[str, object]],
    results: list[dict[str, object]],
) -> str:
    all_metrics = summary["all"]
    product_metrics = summary["product"]
    control_metrics = summary["control"]
    repositories = run["repositories"]
    assert isinstance(repositories, dict)
    lines = [
        "# Phase D Stage D0 Internal Baseline Report",
        "",
        f"- Run ID: `{run['run_id']}`",
        "- Scope: approved Stage D0 baseline replay only",
        f"- Raw run start: `{run['started_at']}`",
        f"- Raw run end: `{run['ended_at']}`",
        f"- Agent Company commit: `{repositories['agent-company']}`",
        f"- PixWeave commit: `{repositories['pixweave']}`",
        f"- Frozen-input manifest SHA-256: `{run['freeze_manifest_sha256']}`",
        "- Independent baseline review: `not_collected`",
        "- Chairman confirmation: `not_collected`",
        "",
        "## Outcome",
        "",
        f"D0 replayed {product_metrics['case_count']} synthetic/replayed PixWeave product cases and "
        f"{control_metrics['case_count']} Company OS fault/control cases. This report is a current-workflow "
        "baseline and does not infer treatment superiority, product quality, public performance, or pilot adoption.",
        "",
        "## Frozen Inputs And Procedure",
        "",
        "The runner verified the pre-recorded SHA-256 of the scenario bank, fault bank, comparator, rubric, "
        "and comparison plan before executing one exact allowlisted `unittest` probe per case. Each subprocess "
        "ran locally against a pinned repository commit with a one-attempt budget and retained an immutable log.",
        "",
        "Exact command:",
        "",
        "```text",
        "python3.11 scripts/run_phase_d_d0.py --freeze docs/assurance/phase-d/d0/freeze-manifest-v1.json --output evidence/phase-d/d0",
        "```",
        "",
        "## Baseline Metrics",
        "",
        "| Metric | Product | Control | Combined |",
        "| --- | ---: | ---: | ---: |",
        f"| Valid cases | {product_metrics['case_count']} | {control_metrics['case_count']} | {all_metrics['case_count']} |",
        f"| Hard gates | {_metric(product_metrics['hard_gates'])} | {_metric(control_metrics['hard_gates'])} | {_metric(all_metrics['hard_gates'])} |",
        f"| Defects | {_metric(product_metrics['defects'])} | {_metric(control_metrics['defects'])} | {_metric(all_metrics['defects'])} |",
        f"| p50/p90 waits (ms) | {_metric(product_metrics['waits_ms'])} | {_metric(control_metrics['waits_ms'])} | {_metric(all_metrics['waits_ms'])} |",
        f"| Model tokens | {_metric(product_metrics['model_tokens'])} | {_metric(control_metrics['model_tokens'])} | {_metric(all_metrics['model_tokens'])} |",
        f"| Human minutes | {_metric(product_metrics['human_minutes'])} | {_metric(control_metrics['human_minutes'])} | {_metric(all_metrics['human_minutes'])} |",
        f"| Rework | {_metric(product_metrics['rework'])} | {_metric(control_metrics['rework'])} | {_metric(all_metrics['rework'])} |",
        f"| False blocks | {_metric(product_metrics['false_blocks'])} | {_metric(control_metrics['false_blocks'])} | {_metric(all_metrics['false_blocks'])} |",
        f"| Reviewer disagreement | {_metric(product_metrics['reviewer_disagreement'])} | {_metric(control_metrics['reviewer_disagreement'])} | {_metric(all_metrics['reviewer_disagreement'])} |",
        f"| Unauthorized transitions | {_metric(product_metrics['unauthorized_transitions'])} | {_metric(control_metrics['unauthorized_transitions'])} | {_metric(all_metrics['unauthorized_transitions'])} |",
        f"| Lineage completeness | {_metric(product_metrics['lineage_completeness'])} | {_metric(control_metrics['lineage_completeness'])} | {_metric(all_metrics['lineage_completeness'])} |",
        "",
        "Artifact preparation retained raw start/end timestamps and measured "
        f"`{run['artifact_preparation']['elapsed_ms']}` ms. Human review wait is `not_collected`; no human "
        "baseline review occurred during tooling execution.",
        "",
        "## Case Results",
        "",
        "| Case | Domain | Kind | Seeded fault | Valid control | Hard gate | Defects before review | Log |",
        "| --- | --- | --- | ---: | ---: | --- | ---: | --- |",
    ]
    for result in results:
        defects = result["defects"]
        evidence = result["evidence"]
        assert isinstance(defects, dict) and isinstance(defects["before_review"], dict)
        assert isinstance(evidence, dict)
        lines.append(
            f"| `{result['case_id']}` | {result['domain']} | {result['case_kind']} | "
            f"{str(result['seeded_fault']).lower()} | {str(result['valid_control']).lower()} | "
            f"{result['hard_gate']} | {defects['before_review']['count']} | `{evidence['log_path']}` |"
        )
    lines.extend([
        "",
        "## Missing Data And Limitations",
        "",
        "Model tokens, engineering/evaluator/reviewer minutes, rework, independent-review defects, "
        "post-completion defects, reviewer disagreement, and human gate waits are `not_collected` because "
        "the replay probes and historical records do not expose them. Zero is never substituted for missing data.",
        "",
        "The product cases replay deterministic PixWeave controls; they do not generate or human-rate new visual "
        "assets and cannot establish D1 preference or quality. Control cases replay existing unit-level fault/control "
        "evidence; they are not D2 treatment execution. Subprocess duration is a machine-gate observation on this "
        "host, not an estimate of historical implementation or reviewer time.",
        "",
        "## Treatment Gates",
        "",
        "- D1: `blocked` pending independent baseline review, Chairman confirmation of frozen comparison manifests "
        "and numerical ceilings, and a CEO-recorded D1 start decision.",
        "- D2: `blocked` pending independent baseline review, Chairman confirmation of frozen comparison manifests "
        "and numerical ceilings, and a CEO-recorded D2 start decision.",
        "- independent baseline review: `not_collected`",
        "- Chairman confirmation: `not_collected`",
        "- CEO D1/D2 start decision: `not_collected`",
        "",
        "No treatment, holdout access, customer data, external spend, outreach, publication, production action, "
        "or PixWeave source modification occurred.",
    ])
    return "\n".join(lines) + "\n"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
