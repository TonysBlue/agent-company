from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_company.phase_d_treatments import (
    PhaseDTreatmentError,
    blind_assignment,
    d1_payload,
    evaluate_d2_results,
    verify_start_contracts,
)


class PhaseDTreatmentTest(unittest.TestCase):
    def test_immutable_start_contracts_bind_governance_and_thresholds(self) -> None:
        root = Path(__file__).resolve().parents[1]
        manifest = root / "docs" / "assurance" / "phase-d" / "start-freeze-manifest-v1.json"

        contracts = verify_start_contracts(root, manifest)

        d1 = contracts["d1"]
        d2 = contracts["d2"]
        self.assertEqual(d1["status"], "started_awaiting_two_human_ratings")
        self.assertTrue(d1["data_policy"]["synthetic_only"])
        self.assertEqual(d1["budgets"]["attempts_per_side"], 1)
        self.assertEqual(d1["budgets"]["maximum_attempts_per_side"], 3)
        self.assertEqual(d1["human_rating"]["required_independent_raters"], 2)
        self.assertEqual(d1["decision_rule"]["lower_confidence_bound_must_exceed"], 0.05)
        self.assertTrue(d1["decision_rule"]["hard_gates_and_protected_dimensions_noninferior"])
        self.assertEqual(d2["success_thresholds"], {
            "critical_high_detection_rate": 1.0,
            "false_pass_count": 0,
            "maximum_false_block_rate": 0.10,
        })
        self.assertTrue(d2["execution"]["isolated_copies_only"])

    def test_start_contract_hash_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "d1.json"
            contract.write_text('{"schema_version":"phase-d-d1-start-contract/v1"}\n', encoding="utf-8")
            d2 = root / "d2.json"
            d2.write_text('{"schema_version":"phase-d-d2-start-contract/v1"}\n', encoding="utf-8")
            manifest = root / "start.json"
            manifest.write_text(json.dumps({
                "schema_version": "phase-d-start-freeze/v1",
                "frozen_at": "2026-07-29T10:00:00+00:00",
                "contracts": [
                    {"pilot": "d1", "path": "d1.json", "sha256": "0" * 64},
                    {"pilot": "d2", "path": "d2.json", "sha256": "0" * 64},
                ],
            }), encoding="utf-8")

            with self.assertRaisesRegex(PhaseDTreatmentError, "hash mismatch"):
                verify_start_contracts(root, manifest)

    def test_blind_assignment_is_deterministic_balanced_and_opaque(self) -> None:
        first = blind_assignment("synthetic-scenario-1")
        second = blind_assignment("synthetic-scenario-1")

        self.assertEqual(first, second)
        self.assertEqual(set(first), {"A", "B"})
        self.assertEqual(set(first.values()), {"candidate", "comparator"})

    def test_d1_recipes_use_equal_operations_but_distinct_treatment_content(self) -> None:
        scenario = {
            "id": "synthetic-1",
            "aspect_ratio": "1:1",
            "brand": {"palette": ["#112233", "#DDEEFF"], "logo_clearance_percent": 8},
            "message": {"primary": "Primary", "secondary": "Secondary"},
            "constraints": ["no claims"],
            "seed": 101,
        }

        candidate = d1_payload(scenario, "candidate")
        comparator = d1_payload(scenario, "comparator")

        self.assertEqual(len(candidate["operations"]), len(comparator["operations"]))
        self.assertEqual(candidate["operations"][0]["type"], comparator["operations"][0]["type"])
        self.assertNotEqual(candidate["operations"][0]["text"], comparator["operations"][0]["text"])

    def test_d2_threshold_evaluation_counts_false_pass_and_false_block(self) -> None:
        passing = [
            {"case_id": "fault-critical", "severity": "critical", "seeded_fault": True,
             "valid_control": False, "treatment_signal": "detected"},
            {"case_id": "fault-high", "severity": "high", "seeded_fault": True,
             "valid_control": False, "treatment_signal": "detected"},
            {"case_id": "control", "severity": "high", "seeded_fault": False,
             "valid_control": True, "treatment_signal": "allowed"},
        ]

        result = evaluate_d2_results(passing)

        self.assertEqual(result["critical_high_detection"], {"detected": 2, "total": 2, "rate": 1.0})
        self.assertEqual(result["false_passes"], 0)
        self.assertEqual(result["false_blocks"], {"blocked": 0, "controls": 1, "rate": 0.0})
        self.assertTrue(result["thresholds_passed"])

        passing[0]["treatment_signal"] = "allowed"
        self.assertFalse(evaluate_d2_results(passing)["thresholds_passed"])


if __name__ == "__main__":
    unittest.main()
