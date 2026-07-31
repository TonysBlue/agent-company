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
    def test_v2_freeze_is_superseded_and_cannot_authorize(self) -> None:
        freeze = REDESIGN / "corrected-freeze-v2.json"

        with self.assertRaisesRegex(PhaseDRedesignError, "superseded"):
            verify_corrected_freeze(ROOT, freeze)
        with self.assertRaisesRegex(PhaseDRedesignError, "superseded"):
            verify_corrected_freeze(ROOT, freeze, require_execution_approval=True)

    def test_v2_freeze_fails_closed_before_hash_or_approval_processing(self) -> None:
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

            with self.assertRaisesRegex(PhaseDRedesignError, "superseded"):
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

    def test_svg_validator_accepts_supported_subset_and_rater_form_is_self_contained(self) -> None:
        bank = validate_scenario_bank(ROOT, self.bank_path)
        scenario = bank["scenarios"][0]
        artifact = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
            b'viewBox="0 0 512 512"><rect x="0" y="0" width="512" '
            b'height="512" fill="#111827"/></svg>'
        )
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

    def test_legacy_renderer_and_delivery_bundle_are_blocked_without_output(self) -> None:
        bank = validate_scenario_bank(ROOT, self.bank_path)
        scenario = bank["scenarios"][0]
        source = (ROOT / scenario["source_path"]).read_bytes()

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "delivery" / str(scenario["id"])
            with self.assertRaisesRegex(PhaseDRedesignError, "blocked|superseded"):
                render_bounded_artifact(scenario, source)
            with self.assertRaisesRegex(PhaseDRedesignError, "blocked|superseded"):
                write_delivery_bundle(destination, scenario, b"A", b"B", {})
            self.assertFalse(destination.exists())


class PhaseDRedesignD2Test(unittest.TestCase):
    def test_surrogate_mutation_fixture_is_blocked_without_evidence(self) -> None:
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
            with self.assertRaisesRegex(PhaseDRedesignError, "surrogate|blocked|superseded"):
                create_harness_fixture(frozen)
            self.assertFalse(frozen.exists())
            self.assertFalse((root / "evidence").exists())

    def test_surrogate_repository_mutation_is_blocked_without_evidence(self) -> None:
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
            with self.assertRaisesRegex(PhaseDRedesignError, "surrogate|blocked|superseded"):
                create_harness_fixture(frozen)
            self.assertFalse(frozen.exists())
            self.assertFalse((root / "evidence").exists())

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

        with self.assertRaisesRegex(PhaseDRedesignError, "exact contract and bank"):
            derive_d2_observation_thresholds(pairs)


class PhaseDRedesignDryRunTest(unittest.TestCase):
    def test_dry_run_emits_complete_evidence_without_treatment_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "phase-d-redesign"

            result = run_redesign_dry_run(
                ROOT,
                output,
                freeze_path=REDESIGN / "corrected-freeze-v4.json",
                allow_development_overlay=True,
            )

            self.assertEqual(
                result["status"], "development_only_unverified_non_candidate"
            )
            self.assertTrue(result["development_only"])
            self.assertFalse(result["verified"])
            self.assertFalse(result["candidate_evidence"])
            self.assertFalse(result["corrected_treatments_executed"])
            self.assertEqual(result["d1"]["scenario_contracts_checked"], 6)
            self.assertEqual(result["d1"]["treatment_workflows_executed"], 0)
            self.assertEqual(result["d1"]["artifacts_generated"], 0)
            self.assertEqual(result["d2"]["named_control_mappings_checked"], 16)
            self.assertEqual(result["d2"]["database_mutations_attempted"], 0)
            self.assertFalse(result["d2"]["thresholds_passed"])
            self.assertFalse(any(output.rglob("*.svg")))
            self.assertTrue((output / "evidence-manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
