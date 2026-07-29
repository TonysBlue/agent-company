from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_company.phase_d_redesign import (
    PhaseDRedesignError,
    build_d1_run_specs,
    build_rater_form,
    create_harness_fixture,
    derive_d2_observation_thresholds,
    execute_mutation_pair,
    load_json,
    render_bounded_artifact,
    run_redesign_dry_run,
    validate_bounded_svg,
    validate_scenario_bank,
    verify_corrected_freeze,
    write_delivery_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
REDESIGN = ROOT / "docs" / "assurance" / "phase-d" / "redesign"


class PhaseDRedesignContractTest(unittest.TestCase):
    def test_corrected_freeze_preserves_findings_and_blocks_unreviewed_execution(self) -> None:
        freeze = REDESIGN / "corrected-freeze-v2.json"

        verification = verify_corrected_freeze(ROOT, freeze)

        self.assertEqual(verification["status"], "blocked_pending_independent_approval")
        self.assertFalse(verification["execution_authorized"])
        findings = verification["documents"]["independent_findings"]
        self.assertEqual(findings["reviewed_head"], "6626411")
        self.assertEqual({item["pilot"] for item in findings["findings"]}, {"d1", "d2", "governance"})
        self.assertTrue(findings["prior_treatment_conclusions_invalid"])
        proposal = verification["documents"]["ceo_start_proposal"]
        self.assertEqual(proposal["current_decision"], "do_not_start")
        self.assertFalse(proposal["effective_authorization"])
        with self.assertRaisesRegex(PhaseDRedesignError, "independent approval"):
            verify_corrected_freeze(ROOT, freeze, require_execution_approval=True)

    def test_freeze_hash_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            document = root / "contract.json"
            document.write_text('{"id":"original"}\n', encoding="utf-8")
            freeze = root / "freeze.json"
            freeze.write_text(json.dumps({
                "schema_version": "phase-d-redesign-freeze/v2",
                "status": "blocked_pending_independent_approval",
                "documents": [{
                    "kind": "d1_contract",
                    "path": "contract.json",
                    "sha256": "0" * 64,
                }],
                "execution_gate": {"independent_approval_path": "approval.json"},
            }), encoding="utf-8")

            with self.assertRaisesRegex(PhaseDRedesignError, "hash mismatch"):
                verify_corrected_freeze(root, freeze)


class PhaseDRedesignD1Test(unittest.TestCase):
    def setUp(self) -> None:
        self.bank_path = REDESIGN / "d1" / "scenario-bank-v2.json"
        self.contract = load_json(REDESIGN / "d1" / "contract-v2.json")

    def test_six_recognizable_sources_and_byte_identical_paired_inputs(self) -> None:
        bank = validate_scenario_bank(ROOT, self.bank_path)

        scenarios = bank["scenarios"]
        self.assertGreaterEqual(len(scenarios), 6)
        self.assertEqual(len({item["product_category"] for item in scenarios}), len(scenarios))
        for scenario in scenarios:
            self.assertGreaterEqual(len(scenario["recognizable_features"]), 3)
            source = (ROOT / scenario["source_path"]).read_bytes()
            specs = build_d1_run_specs(scenario, self.contract, source)
            candidate = copy.deepcopy(specs["candidate"])
            comparator = copy.deepcopy(specs["comparator"])
            self.assertNotEqual(candidate.pop("assurance_workflow"), comparator.pop("assurance_workflow"))
            self.assertEqual(candidate, comparator)
            self.assertEqual(specs["candidate"]["source_bytes"], specs["comparator"]["source_bytes"])
            for field in ("brief", "messages", "attempt_budget", "model", "tool", "timeout_seconds", "evidence_budget_bytes"):
                self.assertEqual(specs["candidate"][field], specs["comparator"][field])

    def test_artifacts_are_bounded_and_rater_form_is_self_contained(self) -> None:
        bank = validate_scenario_bank(ROOT, self.bank_path)
        scenario = bank["scenarios"][0]
        source = (ROOT / scenario["source_path"]).read_bytes()

        artifact = render_bounded_artifact(scenario, source)
        validation = validate_bounded_svg(artifact)
        form = build_rater_form(scenario)

        self.assertTrue(validation["bounded"])
        self.assertFalse(validation["overflow"])
        self.assertEqual(len(form["hard_gates"]["per_option"]), 4)
        self.assertEqual(len(form["anchored_dimensions"]), 5)
        for dimension in form["anchored_dimensions"]:
            self.assertEqual(set(dimension["anchors"]), {"1", "3", "5"})
        response = form["response_template"]
        self.assertEqual(response["preference"]["allowed"], ["A", "B", "tie", "abstain"])
        self.assertIn("confidence", response)
        self.assertIn("rationale", response)
        self.assertIn("elapsed_minutes", response)
        self.assertIn("protocol_violations", response)
        self.assertEqual(set(response["hard_gate_results"]), {"A", "B"})

    def test_delivery_bundle_is_hashed_and_contains_no_custody_or_generated_paths(self) -> None:
        bank = validate_scenario_bank(ROOT, self.bank_path)
        scenario = bank["scenarios"][0]
        source = (ROOT / scenario["source_path"]).read_bytes()
        artifact = render_bounded_artifact(scenario, source)
        form = build_rater_form(scenario)

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "delivery" / str(scenario["id"])
            manifest = write_delivery_bundle(destination, scenario, artifact, artifact, form)

            self.assertEqual(manifest["schema_version"], "phase-d-d1-delivery-bundle/v2")
            self.assertRegex(manifest["bundle_sha256"], r"^[0-9a-f]{64}$")
            delivered = [item["path"] for item in manifest["files"]]
            self.assertEqual(set(delivered), {"brief.json", "option-A.svg", "option-B.svg", "rater-form.json"})
            serialized = json.dumps(manifest, sort_keys=True).lower()
            for forbidden in ("candidate", "comparator", "mapping", "custody", "generated/"):
                self.assertNotIn(forbidden, serialized)
            self.assertFalse(any("custody" in path.name.lower() for path in destination.rglob("*")))


class PhaseDRedesignD2Test(unittest.TestCase):
    def test_isolated_mutation_pair_retains_complete_rollback_evidence(self) -> None:
        case = {
            "id": "canary-direct-completion",
            "seeded_fault": True,
            "target": "database",
            "mutation": {
                "kind": "sql",
                "statement": "UPDATE tasks SET status='completed' WHERE id=1",
            },
            "protected_tables": ["approvals", "audit_log", "operational_counters"],
            "protected_repo_paths": ["contracts/freeze.json", "evidence/baseline.json"],
        }
        baseline = {"id": "current_workflow", "database_controls": [], "protected_repo_paths": []}
        treatment = {
            "id": "assurance_workflow",
            "database_controls": ["deny_direct_completion"],
            "protected_repo_paths": ["contracts/freeze.json", "evidence/baseline.json"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "frozen"
            create_harness_fixture(frozen)
            source_before = {path.relative_to(frozen).as_posix(): path.read_bytes() for path in frozen.rglob("*") if path.is_file()}
            result = execute_mutation_pair(frozen, case, baseline, treatment, root / "evidence")
            source_after = {path.relative_to(frozen).as_posix(): path.read_bytes() for path in frozen.rglob("*") if path.is_file()}

            self.assertEqual(source_before, source_after)
            self.assertEqual(result["baseline"]["observation"]["outcome"], "allowed")
            self.assertEqual(result["treatment"]["observation"]["outcome"], "denied")
            for side in ("baseline", "treatment"):
                record = result[side]
                self.assertEqual(record["before_snapshot"]["state_sha256"], record["after_snapshot"]["state_sha256"])
                self.assertTrue(record["rollback"]["completed"])
                self.assertTrue(record["noninterference"]["passed"])
                self.assertIn("event_sha256", record["audit_event_evidence"])
                evidence_dir = root / "evidence" / case["id"] / side
                self.assertEqual(
                    {path.name for path in evidence_dir.iterdir()},
                    {
                        "before-snapshot.json",
                        "mutation.json",
                        "observation.json",
                        "rollback.json",
                        "after-snapshot.json",
                        "audit-event-evidence.json",
                        "noninterference.json",
                    },
                )

    def test_repository_mutation_is_real_isolated_and_rolled_back(self) -> None:
        case = {
            "id": "canary-contract-rewrite",
            "seeded_fault": True,
            "target": "repository",
            "mutation": {
                "kind": "repo_write",
                "path": "contracts/freeze.json",
                "content": "tampered\n",
            },
            "protected_tables": ["tasks", "approvals", "audit_log", "operational_counters"],
            "protected_repo_paths": ["evidence/baseline.json"],
        }
        baseline = {"id": "current_workflow", "database_controls": [], "protected_repo_paths": []}
        treatment = {
            "id": "assurance_workflow",
            "database_controls": [],
            "protected_repo_paths": ["contracts/freeze.json"],
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "frozen"
            create_harness_fixture(frozen)
            result = execute_mutation_pair(frozen, case, baseline, treatment, root / "evidence")

            self.assertEqual(result["baseline"]["observation"]["outcome"], "allowed")
            self.assertEqual(result["treatment"]["observation"]["outcome"], "denied")
            self.assertTrue(result["baseline"]["mutation"]["attempted"])
            self.assertTrue(result["treatment"]["mutation"]["attempted"])

    def test_thresholds_are_derived_only_from_paired_observations(self) -> None:
        pairs = [
            {
                "case_id": "fault-a",
                "seeded_fault": True,
                "valid_control": False,
                "baseline": {"observation": {"outcome": "allowed"}},
                "treatment": {"observation": {"outcome": "denied"}},
            },
            {
                "case_id": "fault-b",
                "seeded_fault": True,
                "valid_control": False,
                "baseline": {"observation": {"outcome": "denied"}},
                "treatment": {"observation": {"outcome": "denied"}},
            },
            {
                "case_id": "control-a",
                "seeded_fault": False,
                "valid_control": True,
                "baseline": {"observation": {"outcome": "allowed"}},
                "treatment": {"observation": {"outcome": "allowed"}},
            },
        ]

        result = derive_d2_observation_thresholds(pairs)

        self.assertEqual(result["threshold_source"], "paired_baseline_observations")
        self.assertEqual(result["observed_baseline_escape_ids"], ["fault-a"])
        self.assertEqual(result["required_treatment_denial_ids"], ["fault-a"])
        self.assertEqual(result["observed_baseline_allowed_control_ids"], ["control-a"])
        self.assertEqual(result["required_treatment_allow_ids"], ["control-a"])
        self.assertTrue(result["observation_derived_comparison_passed"])
        self.assertNotIn("asserted_thresholds", result)


class PhaseDRedesignDryRunTest(unittest.TestCase):
    def test_dry_run_emits_complete_evidence_without_treatment_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "phase-d-redesign"

            result = run_redesign_dry_run(ROOT, output)

            self.assertEqual(result["status"], "dry_run_complete_treatments_blocked")
            self.assertFalse(result["corrected_treatments_executed"])
            self.assertEqual(result["d1"]["scenario_count"], 6)
            self.assertEqual(result["d2"]["canary_count"], 3)
            self.assertEqual(result["d2"]["threshold_source"], "paired_baseline_observations")
            deliveries = list((output / "dry-run" / "d1" / "rater-delivery").glob("*/bundle-manifest.json"))
            self.assertEqual(len(deliveries), 6)
            self.assertTrue((output / "custody" / "d1" / "custody-mapping.json").is_file())
            self.assertFalse((output / "dry-run" / "d1" / "rater-delivery" / "custody-mapping.json").exists())
            self.assertTrue((output / "evidence-manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
