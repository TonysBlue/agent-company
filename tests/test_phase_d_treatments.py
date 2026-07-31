from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

import agent_company.phase_d_treatments as treatments


ROOT = Path(__file__).resolve().parents[1]


class PhaseDTreatmentTombstoneTest(unittest.TestCase):
    def test_legacy_module_has_no_treatment_or_threshold_helpers(self) -> None:
        removed = {
            "verify_start_contracts",
            "blind_assignment",
            "evaluate_d2_results",
            "synthetic_png",
            "d1_payload",
            "run_unittest_case",
        }
        self.assertTrue(removed.isdisjoint(vars(treatments)))
        self.assertIn("permanently disabled", treatments.BLOCKED_REASON)

    def test_legacy_runner_fails_closed(self) -> None:
        path = ROOT / "scripts" / "run_phase_d_treatments.py"
        spec = importlib.util.spec_from_file_location("phase_d_treatment_tombstone_test", path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)

        with self.assertRaisesRegex(treatments.PhaseDTreatmentError, "tombstone|permanently disabled"):
            runner.main()


if __name__ == "__main__":
    unittest.main()
