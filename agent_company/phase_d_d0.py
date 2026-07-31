"""Read-only historical parsing and aggregation for superseded Phase D D0 data."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


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
    charter_binding = freeze.get("charter_binding")
    if isinstance(charter_binding, dict):
        charter_path = charter_binding.get("path")
        charter_sha256 = charter_binding.get("sha256")
        if (
            not isinstance(charter_path, str)
            or Path(charter_path).is_absolute()
            or ".." in Path(charter_path).parts
            or not isinstance(charter_sha256, str)
            or len(charter_sha256) != 64
        ):
            raise D0Error("freeze charter binding is invalid")
        if sha256_file(root / charter_path) != charter_sha256:
            raise D0Error("frozen charter hash mismatch")
        verified[charter_path] = charter_sha256
    return verified


def parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise D0Error(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise D0Error(f"{label} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise D0Error(f"{label} must include a timezone")
    return parsed


def validate_freeze_chronology(
    root: Path,
    freeze: dict[str, object],
    run_started_at: str,
) -> None:
    if "charter_binding" not in freeze:
        raise D0Error("freeze manifest must bind the approved charter hash")
    frozen_at = parse_timestamp(freeze.get("frozen_at"), "frozen_at")
    started_at = parse_timestamp(run_started_at, "run_started_at")
    if frozen_at >= started_at:
        raise D0Error("freeze manifest and charter binding must predate baseline run")


def comparator_commits(comparator: dict[str, object]) -> dict[str, str]:
    repositories = comparator.get("repositories")
    if not isinstance(repositories, dict):
        raise D0Error("comparator repositories must be an object")
    expected: dict[str, str] = {}
    for repository_id in ALLOWED_REPOSITORIES:
        entry = repositories.get(repository_id)
        if not isinstance(entry, dict):
            raise D0Error(f"comparator is missing repository binding: {repository_id}")
        commit = entry.get("regression_commit")
        count = entry.get("expected_regression_tests")
        if not isinstance(commit, str) or len(commit) != 40:
            raise D0Error(f"comparator regression commit is invalid: {repository_id}")
        if not isinstance(count, int) or count <= 0:
            raise D0Error(f"comparator expected regression test count is invalid: {repository_id}")
        expected[repository_id] = commit
    if comparator.get("treatment_execution_authorized") is not False:
        raise D0Error("D0 comparator must not authorize treatment execution")
    if comparator.get("external_spend_cny") != 0:
        raise D0Error("D0 comparator external spend must be zero")
    if comparator.get("protected_holdout_attempts") != 0:
        raise D0Error("D0 comparator protected holdout attempts must be zero")
    return expected


def parse_regression_test_count(output: str) -> int:
    matches = re.findall(r"^Ran (\d+) tests? in ", output, flags=re.MULTILINE)
    if len(matches) != 1:
        raise D0Error("cannot determine exact regression test count")
    return int(matches[0])


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
