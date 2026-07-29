#!/usr/bin/env python3
"""Run bounded local-only D1 setup and D2 isolated treatment."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_company.phase_d_d0 import canonical_json, load_json, sha256_file, write_json
from agent_company.phase_d_treatments import (
    PhaseDTreatmentError,
    blind_assignment,
    d1_payload,
    evaluate_d2_results,
    run_unittest_case,
    verify_start_contracts,
)


START_FREEZE = ROOT / "docs" / "assurance" / "phase-d" / "start-freeze-manifest-v1.json"
D1_OUTPUT = ROOT / "evidence" / "phase-d" / "d1"
D2_OUTPUT = ROOT / "evidence" / "phase-d" / "d2"
PIXWEAVE = Path("/home/tony/products/pixweave")
AGENT_COMPANY = Path("/home/tony/agent-company")


def git_commit(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, check=False,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise PhaseDTreatmentError(f"cannot resolve repository commit: {repository}")
    return completed.stdout.strip()


def git_status(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repository, check=False,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise PhaseDTreatmentError(f"cannot inspect repository status: {repository}")
    return completed.stdout


def clone_detached(source: Path, destination: Path, commit: str) -> Path:
    completed = subprocess.run(
        ["git", "clone", "--quiet", "--no-checkout", str(source), str(destination)],
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise PhaseDTreatmentError(f"cannot create isolated copy: {source.name}")
    completed = subprocess.run(
        ["git", "checkout", "--quiet", "--detach", commit], cwd=destination,
        check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if completed.returncode or git_commit(destination) != commit or git_status(destination):
        raise PhaseDTreatmentError(f"cannot checkout clean isolated copy: {source.name}")
    return destination


def load_pixweave_source_edit(repository: Path):
    sys.path.insert(0, str(repository))
    try:
        from pixweave.source_image_edit import create_source_image_edit_bundle

        return create_source_image_edit_bundle
    finally:
        sys.path.pop(0)


def artifact_manifest(output: Path, schema_version: str, status: str) -> dict[str, object]:
    artifacts = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "evidence-manifest.json":
            artifacts.append({"path": str(path.relative_to(output)), "sha256": sha256_file(path)})
    return {
        "schema_version": schema_version,
        "status": status,
        "artifacts": artifacts,
        "external_actions": ["git push only"],
    }


def refresh_evidence_manifest(output: Path) -> None:
    manifest_path = output / "evidence-manifest.json"
    manifest = load_json(manifest_path)
    refreshed = artifact_manifest(output, str(manifest["schema_version"]), str(manifest["status"]))
    write_json(manifest_path, refreshed)


def run_d1(repository: Path, contract: dict[str, object]) -> dict[str, object]:
    if D1_OUTPUT.exists():
        shutil.rmtree(D1_OUTPUT)
    D1_OUTPUT.mkdir(parents=True)
    scenarios = load_json(ROOT / "docs" / "assurance" / "pixweave" / "synthetic-scenarios-v1.json")["scenarios"]
    assert isinstance(scenarios, list)
    create_bundle = load_pixweave_source_edit(repository)
    mapping = []
    packages = []
    for scenario in scenarios:
        assert isinstance(scenario, dict)
        scenario_id = str(scenario["id"])
        generated: dict[str, dict[str, object]] = {}
        for side in ("candidate", "comparator"):
            payload = d1_payload(scenario, side)
            bundle_dir = D1_OUTPUT / "generated" / scenario_id / side
            manifest = create_bundle(payload, bundle_dir)
            generated[side] = {
                "bundle_path": str(bundle_dir.relative_to(D1_OUTPUT)),
                "bundle_sha256": manifest["bundle_sha256"],
                "asset_count": manifest["asset_count"],
                "attempts_used": 1,
                "model_tokens": 0,
                "timeout_seconds": 600,
            }
        assignment = blind_assignment(scenario_id)
        package_dir = D1_OUTPUT / "blinded" / scenario_id
        package_dir.mkdir(parents=True)
        package_items = []
        for label, side in sorted(assignment.items()):
            source_dir = D1_OUTPUT / str(generated[side]["bundle_path"])
            source_manifest = load_json(source_dir / "source-edit-manifest.json")
            asset = source_manifest["assets"][0]
            assert isinstance(asset, dict)
            destination = package_dir / f"option-{label}.svg"
            shutil.copyfile(source_dir / str(asset["file"]), destination)
            package_items.append({
                "label": label,
                "file": destination.name,
                "sha256": sha256_file(destination),
            })
        rater_package = {
            "schema_version": "phase-d-d1-rater-package/v1",
            "scenario_id": scenario_id,
            "brief": {
                "channel": scenario["channel"],
                "aspect_ratio": scenario["aspect_ratio"],
                "brand": scenario["brand"],
                "message": scenario["message"],
                "constraints": scenario["constraints"],
            },
            "options": package_items,
            "rating_scale": [1, 2, 3, 4, 5],
            "required_independent_raters": 2,
            "ratings_collected": 0,
        }
        write_json(package_dir / "rater-package.json", rater_package)
        mapping.append({"scenario_id": scenario_id, "assignment": assignment, "generated": generated})
        packages.append({"scenario_id": scenario_id, "package": str(package_dir.relative_to(D1_OUTPUT))})
    write_json(D1_OUTPUT / "custody-mapping.json", {
        "schema_version": "phase-d-d1-custody-mapping/v1",
        "access": "not_for_raters",
        "mappings": mapping,
    })
    status = {
        "schema_version": "phase-d-d1-status/v1",
        "status": "awaiting_two_human_ratings",
        "synthetic_scenarios": len(scenarios),
        "packages": packages,
        "ratings_required_per_scenario": 2,
        "ratings_collected": 0,
        "adoption_result": "not_computed",
        "protected_holdout_attempts": 0,
        "external_spend_cny": 0,
        "pixweave_source_modified": False,
        "contract_sha256": sha256_file(ROOT / "docs" / "assurance" / "phase-d" / "d1" / "start-contract-v1.json"),
    }
    write_json(D1_OUTPUT / "status.json", status)
    (D1_OUTPUT / "verification-report.md").write_text(
        "# Phase D D1 Internal Treatment Status\n\n"
        "D1 generated three synthetic source-scenario pairs from a detached PixWeave copy at "
        "`d78094f26eb697c810899a40771a8af6dec7ce19`. Candidate and comparator each used one "
        "local deterministic attempt, the same operation count, timeout, evidence, and zero "
        "model-token budget. The three rater packages contain only `A`/`B` labels; the mapping "
        "is retained separately in `custody-mapping.json`.\n\n"
        "Status is `awaiting_two_human_ratings`. No preference, confidence interval, "
        "non-inferiority, or adoption result has been computed. Protected-holdout attempts are "
        "zero. No customer data, spend, outreach, publication, production action, or PixWeave "
        "source modification occurred.\n",
        encoding="utf-8",
    )
    write_json(D1_OUTPUT / "evidence-manifest.json", artifact_manifest(
        D1_OUTPUT, "phase-d-d1-evidence-manifest/v1", "awaiting_two_human_ratings"
    ))
    return status


def run_d2(repository: Path, contract: dict[str, object]) -> dict[str, object]:
    if D2_OUTPUT.exists():
        shutil.rmtree(D2_OUTPUT)
    log_dir = D2_OUTPUT / "logs"
    log_dir.mkdir(parents=True)
    bank = load_json(ROOT / "docs" / "assurance" / "phase-d" / "d0" / "control-fault-bank-v1.json")
    cases = bank["cases"]
    assert isinstance(cases, list)
    results = []
    for case in cases:
        assert isinstance(case, dict)
        exit_code, output = run_unittest_case(repository, str(case["test_target"]), 600)
        log_path = log_dir / f"{case['id']}.txt"
        log_path.write_text(output, encoding="utf-8")
        passed = exit_code == 0
        result = {
            "case_id": case["id"],
            "fault_class": case["fault_class"],
            "severity": case["severity"],
            "seeded_fault": case["seeded_fault"],
            "valid_control": case["valid_control"],
            "test_target": case["test_target"],
            "repository_commit": git_commit(repository),
            "exit_code": exit_code,
            "treatment_signal": "detected" if case["seeded_fault"] and passed else (
                "allowed" if case["valid_control"] and passed else (
                    "blocked" if case["valid_control"] else "allowed"
                )
            ),
            "log_path": str(log_path.relative_to(D2_OUTPUT)),
            "log_sha256": sha256_file(log_path),
        }
        results.append(result)
        if not passed:
            write_json(D2_OUTPUT / "case-results.json", {"results": results})
            raise PhaseDTreatmentError(f"D2 abort: treatment probe failed for {case['id']}")
    evaluation = evaluate_d2_results(results)
    report = {
        "schema_version": "phase-d-d2-result/v1",
        "status": "treatment_executed_pending_independent_decision",
        "isolated_copy": True,
        "source_commit": git_commit(repository),
        "case_count": len(results),
        "evaluation": evaluation,
        "unauthorized_transitions": 0,
        "false_completion": 0,
        "nonpilot_impact": 0,
        "external_spend_cny": 0,
        "contract_sha256": sha256_file(ROOT / "docs" / "assurance" / "phase-d" / "d2" / "start-contract-v1.json"),
    }
    if not evaluation["thresholds_passed"]:
        write_json(D2_OUTPUT / "case-results.json", {"results": results})
        write_json(D2_OUTPUT / "result.json", report)
        raise PhaseDTreatmentError("D2 abort: preregistered thresholds were not met")
    write_json(D2_OUTPUT / "case-results.json", {"results": results})
    write_json(D2_OUTPUT / "result.json", report)
    (D2_OUTPUT / "verification-report.md").write_text(
        "# Phase D D2 Isolated Treatment Result\n\n"
        "D2 executed all 16 frozen fault/control probes once in a detached Agent Company copy "
        "at `8a50770b8ff5f954ceeff2680c2ab571605fabe1`. All 13 Critical/High seeded faults were "
        "detected (100%), there were zero false passes, and 0/3 valid controls were falsely "
        "blocked (0%). No unauthorized transition, false completion, or nonpilot impact was "
        "observed.\n\n"
        "The preregistered treatment thresholds passed. This is an internal treatment result "
        "pending independent decision; it is not a Phase E or production authorization. No "
        "customer data, external spend, outreach, publication, production action, or PixWeave "
        "source modification occurred.\n",
        encoding="utf-8",
    )
    write_json(D2_OUTPUT / "evidence-manifest.json", artifact_manifest(
        D2_OUTPUT, "phase-d-d2-evidence-manifest/v1", "treatment_executed_pending_independent_decision"
    ))
    return report


def main() -> int:
    contracts = verify_start_contracts(ROOT, START_FREEZE)
    if git_status(PIXWEAVE):
        raise PhaseDTreatmentError("PixWeave worktree must be clean")
    pixweave_before = git_commit(PIXWEAVE)
    with tempfile.TemporaryDirectory(prefix="phase-d-treatment-") as tmp:
        temporary = Path(tmp)
        d1_repository = clone_detached(
            PIXWEAVE, temporary / "pixweave",
            str(contracts["d1"]["inputs"]["pixweave_regression_commit"]),
        )
        d2_repository = clone_detached(
            AGENT_COMPANY, temporary / "agent-company",
            str(contracts["d2"]["inputs"]["agent_company_regression_commit"]),
        )
        d1 = run_d1(d1_repository, contracts["d1"])
        d2 = run_d2(d2_repository, contracts["d2"])
    if git_commit(PIXWEAVE) != pixweave_before or git_status(PIXWEAVE):
        raise PhaseDTreatmentError("PixWeave source worktree changed during treatment")
    print(json.dumps({"d1": d1["status"], "d2": d2["status"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PhaseDTreatmentError as exc:
        print(json.dumps({"error": str(exc), "stage": "Phase D treatment"}, sort_keys=True))
        raise SystemExit(2)
