from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

import agent_company.phase_d_redesign as redesign


ROOT = Path(__file__).resolve().parents[1]
REDESIGN = ROOT / "docs" / "assurance" / "phase-d" / "redesign"


def _governance_fixture() -> tuple[dict[str, object], str, dict[str, str], dict[str, str], dict[str, bytes]]:
    freeze = {
        "execution_gate": {
            "reviewer_identity": {
                "principal_id": "principal-control-review",
                "role": "Control & Reliability Reviewer",
                "key_id": "phase-d-reviewer-v3",
            },
            "ceo_identity": {
                "principal_id": "principal-ceo",
                "role": "CEO",
                "key_id": "phase-d-ceo-v3",
            },
        },
        "source_revision": {"commit": "f" * 40, "tree": "e" * 40},
    }
    return (
        freeze,
        "a" * 64,
        {"contract.json": "b" * 64},
        {"runner.py": "c" * 64},
        {
            "phase-d-reviewer-v3": b"reviewer credential known only to verifier",
            "phase-d-ceo-v3": b"ceo credential known only to verifier",
        },
    )


def _signed_approval(
    freeze: dict[str, object],
    freeze_hash: str,
    document_hashes: dict[str, str],
    binding_hashes: dict[str, str],
    secret: bytes,
    *,
    unresolved: object | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": "phase-d-redesign-independent-approval/v3",
        "decision": "approve",
        "reviewer_principal": "principal-control-review",
        "reviewer_role": "Control & Reliability Reviewer",
        "reviewed_freeze_sha256": freeze_hash,
        "reviewed_source_revision": copy.deepcopy(freeze["source_revision"]),
        "reviewed_document_sha256": copy.deepcopy(document_hashes),
        "reviewed_binding_sha256": copy.deepcopy(binding_hashes),
        "unresolved_findings": [] if unresolved is None else unresolved,
        "signed_at": "2026-07-30T10:00:00+08:00",
    }
    return redesign.sign_governance_record(
        record,
        principal_id="principal-control-review",
        key_id="phase-d-reviewer-v3",
        credential=secret,
    )


def _signed_ceo_decision(
    freeze: dict[str, object],
    freeze_hash: str,
    approval: dict[str, object],
    secret: bytes,
    *,
    decision: str = "start",
    effective: bool = True,
) -> dict[str, object]:
    record: dict[str, object] = {
        "schema_version": "phase-d-redesign-ceo-start-decision/v3",
        "decision": decision,
        "effective_authorization": effective,
        "ceo_principal": "principal-ceo",
        "ceo_role": "CEO",
        "approved_freeze_sha256": freeze_hash,
        "approved_independent_approval_sha256": redesign.sha256_bytes(
            redesign.canonical_json(approval).encode("ascii")
        ),
        "approved_source_revision": copy.deepcopy(freeze["source_revision"]),
        "signed_at": "2026-07-30T10:01:00+08:00",
    }
    return redesign.sign_governance_record(
        record,
        principal_id="principal-ceo",
        key_id="phase-d-ceo-v3",
        credential=secret,
    )


class PhaseDRedesignV3GovernanceRedTest(unittest.TestCase):
    def test_red_forged_approval_is_rejected(self) -> None:
        freeze, freeze_hash, documents, bindings, credentials = _governance_fixture()
        approval = _signed_approval(freeze, freeze_hash, documents, bindings, b"attacker credential")
        ceo = _signed_ceo_decision(
            freeze, freeze_hash, approval, credentials["phase-d-ceo-v3"]
        )

        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "signature|authentic"):
            redesign.evaluate_v3_authorization(
                freeze,
                freeze_hash,
                documents,
                bindings,
                approval,
                ceo,
                credentials,
                require_execution_authorization=True,
            )

    def test_red_unresolved_critical_high_string_and_list_are_rejected(self) -> None:
        freeze, freeze_hash, documents, bindings, credentials = _governance_fixture()
        representations = (
            "Critical: forged approval remains unresolved",
            ["HIGH - executable drift is unresolved"],
        )
        for unresolved in representations:
            with self.subTest(unresolved=unresolved):
                approval = _signed_approval(
                    freeze,
                    freeze_hash,
                    documents,
                    bindings,
                    credentials["phase-d-reviewer-v3"],
                    unresolved=unresolved,
                )
                with self.assertRaisesRegex(redesign.PhaseDRedesignError, "unresolved Critical/High"):
                    redesign.evaluate_v3_authorization(
                        freeze,
                        freeze_hash,
                        documents,
                        bindings,
                        approval,
                        None,
                        credentials,
                    )

    def test_red_approval_without_separate_post_approval_ceo_start_is_rejected(self) -> None:
        freeze, freeze_hash, documents, bindings, credentials = _governance_fixture()
        approval = _signed_approval(
            freeze, freeze_hash, documents, bindings, credentials["phase-d-reviewer-v3"]
        )

        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "CEO.*start decision"):
            redesign.evaluate_v3_authorization(
                freeze,
                freeze_hash,
                documents,
                bindings,
                approval,
                None,
                credentials,
                require_execution_authorization=True,
            )

    def test_red_current_do_not_start_cannot_authorize_execution(self) -> None:
        freeze, freeze_hash, documents, bindings, credentials = _governance_fixture()
        approval = _signed_approval(
            freeze, freeze_hash, documents, bindings, credentials["phase-d-reviewer-v3"]
        )
        ceo = _signed_ceo_decision(
            freeze,
            freeze_hash,
            approval,
            credentials["phase-d-ceo-v3"],
            decision="do_not_start",
            effective=False,
        )

        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "CEO.*start"):
            redesign.evaluate_v3_authorization(
                freeze,
                freeze_hash,
                documents,
                bindings,
                approval,
                ceo,
                credentials,
                require_execution_authorization=True,
            )

    def test_red_v3_freeze_rejects_bound_executable_drift(self) -> None:
        freeze_path = REDESIGN / "corrected-freeze-v3.json"
        verification = redesign.verify_corrected_freeze(ROOT, freeze_path)
        implementation = ROOT / "agent_company" / "phase_d_redesign.py"

        with tempfile.TemporaryDirectory() as tmp:
            copied_root = Path(tmp)
            for relative in verification["bound_paths"]:
                source = ROOT / relative
                target = copied_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            copied_freeze = copied_root / freeze_path.relative_to(ROOT)
            implementation_copy = copied_root / implementation.relative_to(ROOT)
            implementation_copy.write_bytes(implementation_copy.read_bytes() + b"\n# drift\n")

            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "binding hash mismatch"):
                redesign.verify_corrected_freeze(copied_root, copied_freeze)


class PhaseDRedesignV3D1RedTest(unittest.TestCase):
    def test_red_d1_uses_distinct_workflows_without_input_drift_or_artifact_identity(self) -> None:
        bank = redesign.validate_scenario_bank(REDESIGN.parents[3], REDESIGN / "d1" / "scenario-bank-v3.json")
        contract = redesign.load_json(REDESIGN / "d1" / "contract-v3.json")
        scenario = bank["scenarios"][0]
        source = (ROOT / scenario["source_path"]).read_bytes()
        specs = redesign.build_d1_run_specs(scenario, contract, source)
        candidate_inputs = copy.deepcopy(specs["candidate"])
        comparator_inputs = copy.deepcopy(specs["comparator"])
        candidate_inputs.pop("assurance_workflow")
        comparator_inputs.pop("assurance_workflow")

        candidate = redesign.execute_d1_workflow(scenario, specs["candidate"])
        comparator = redesign.execute_d1_workflow(scenario, specs["comparator"])

        self.assertEqual(candidate_inputs, comparator_inputs)
        self.assertNotEqual(candidate["workflow_trace"], comparator["workflow_trace"])
        self.assertNotEqual(candidate["artifact"], comparator["artifact"])
        self.assertNotIn(b"candidate", candidate["artifact"].lower())
        self.assertNotIn(b"comparator", comparator["artifact"].lower())

    def test_red_d1_assignment_is_randomized_and_exactly_balanced(self) -> None:
        bank = redesign.load_json(REDESIGN / "d1" / "scenario-bank-v3.json")
        scenario_ids = [str(item["id"]) for item in bank["scenarios"]]
        assignment = redesign.balanced_blind_assignments(
            scenario_ids, str(bank["randomization_seed"])
        )

        self.assertEqual(set(assignment), set(scenario_ids))
        candidate_in_a = sum(item["A"] == "candidate" for item in assignment.values())
        self.assertEqual(candidate_in_a, len(scenario_ids) // 2)
        self.assertTrue(all(set(item) == {"A", "B"} for item in assignment.values()))
        self.assertNotEqual(
            assignment,
            redesign.balanced_blind_assignments(scenario_ids, "different-frozen-seed"),
        )

    def test_red_rater_bundle_contains_exact_original_source_and_hash(self) -> None:
        bank = redesign.validate_scenario_bank(ROOT, REDESIGN / "d1" / "scenario-bank-v2.json")
        scenario = bank["scenarios"][0]
        source = (ROOT / scenario["source_path"]).read_bytes()
        artifact = redesign.render_bounded_artifact(scenario, source)

        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "rater-delivery" / str(scenario["id"])
            manifest = redesign.write_delivery_bundle(
                destination,
                scenario,
                artifact,
                artifact,
                redesign.build_rater_form(scenario),
            )

            source_records = [item for item in manifest["files"] if item["role"] == "original_source"]
            self.assertEqual(len(source_records), 1)
            delivered_source = destination / source_records[0]["path"]
            self.assertEqual(delivered_source.read_bytes(), source)
            self.assertEqual(source_records[0]["sha256"], scenario["source_sha256"])

    def test_red_svg_bounds_reject_paths_and_non_path_geometry_outside_canvas(self) -> None:
        artifacts = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512"><path d="M 10 10 L 700 10"/></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512"><circle cx="510" cy="20" r="10"/></svg>',
            b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512"><polygon points="0,0 40,0 40,600"/></svg>',
        )

        for artifact in artifacts:
            with self.subTest(artifact=artifact):
                result = redesign.validate_bounded_svg(artifact)
                self.assertFalse(result["bounded"])
                self.assertTrue(result["overflow"])


class PhaseDRedesignV3D2RedTest(unittest.TestCase):
    def test_red_d2_baseline_denial_cannot_hide_treatment_regression(self) -> None:
        pairs = [
            {
                "case_id": "fault-hidden-by-baseline",
                "severity": "critical",
                "seeded_fault": True,
                "valid_control": False,
                "baseline": {"observation": {"outcome": "denied"}},
                "treatment": {"observation": {"outcome": "allowed"}},
            },
            {
                "case_id": "control-evaluated",
                "severity": "low",
                "seeded_fault": False,
                "valid_control": True,
                "baseline": {"observation": {"outcome": "allowed"}},
                "treatment": {"observation": {"outcome": "allowed"}},
            },
        ]

        result = redesign.derive_d2_observation_thresholds(pairs)

        self.assertEqual(result["required_treatment_denial_ids"], ["fault-hidden-by-baseline"])
        self.assertEqual(result["evaluated_control_ids"], ["control-evaluated"])
        self.assertFalse(result["all_seeded_material_faults_denied"])
        self.assertFalse(result["thresholds_passed"])

    def test_red_d2_bank_has_sixteen_cases_and_every_charter_fault_class_and_control(self) -> None:
        contract = redesign.load_json(REDESIGN / "d2" / "contract-v3.json")
        bank = redesign.validate_mutation_bank(REDESIGN / "d2" / "mutation-bank-v3.json", contract)

        self.assertGreaterEqual(len(bank["cases"]), 16)
        observed = {str(case["fault_class"]) for case in bank["cases"]}
        self.assertEqual(observed, set(contract["required_fault_classes"]) | set(contract["required_control_classes"]))
        self.assertTrue(all(case["seeded_fault"] or case["valid_control"] for case in bank["cases"]))


class PhaseDRedesignV3FreezeRedTest(unittest.TestCase):
    def test_red_freeze_binds_source_and_every_executable_verification_class(self) -> None:
        freeze = redesign.load_json(REDESIGN / "corrected-freeze-v3.json")

        self.assertRegex(str(freeze["source_revision"]["commit"]), r"^[0-9a-f]{40}$")
        self.assertRegex(str(freeze["source_revision"]["tree"]), r"^[0-9a-f]{40}$")
        classes = {str(item["kind"]) for item in freeze["bindings"]}
        self.assertTrue(
            {
                "implementation",
                "runner",
                "test",
                "dry_run_evidence",
                "regression_evidence",
                "red_evidence",
            }.issubset(classes)
        )
        redesign.verify_corrected_freeze(ROOT, REDESIGN / "corrected-freeze-v3.json")


if __name__ == "__main__":
    unittest.main()
