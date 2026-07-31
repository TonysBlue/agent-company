from __future__ import annotations

import copy
import importlib.util
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_company.phase_d_d0 as legacy_d0
import agent_company.phase_d_redesign as redesign


ROOT = Path(__file__).resolve().parents[1]
REDESIGN = ROOT / "docs" / "assurance" / "phase-d" / "redesign"
V4_FREEZE = REDESIGN / "corrected-freeze-v4.json"
V4_SUPERSESSION = REDESIGN / "supersession-record-v4.json"

EXPECTED_DENIED_ARTIFACTS = {
    "docs/assurance/phase-d/d0": "tree",
    "docs/assurance/phase-d/start-freeze-manifest-v1.json": "file",
    "docs/assurance/phase-d/d1/start-contract-v1.json": "file",
    "docs/assurance/phase-d/d2/start-contract-v1.json": "file",
    "docs/assurance/phase-d/redesign/ceo-start-decision-proposal-v2.json": "file",
    "docs/assurance/phase-d/redesign/ceo-start-decision-proposal-v3.json": "file",
    "docs/assurance/phase-d/redesign/corrected-freeze-v2.json": "file",
    "docs/assurance/phase-d/redesign/corrected-freeze-v3.json": "file",
    "docs/assurance/phase-d/redesign/independent-findings-at-6626411-v1.json": "file",
    "docs/assurance/phase-d/redesign/independent-findings-v3.json": "file",
    "docs/assurance/phase-d/redesign/supersession-record-v1.json": "file",
    "docs/assurance/phase-d/redesign/supersession-record-v3.json": "file",
    "docs/assurance/phase-d/redesign/d1/contract-v2.json": "file",
    "docs/assurance/phase-d/redesign/d1/contract-v3.json": "file",
    "docs/assurance/phase-d/redesign/d1/scenario-bank-v2.json": "file",
    "docs/assurance/phase-d/redesign/d2/contract-v2.json": "file",
    "docs/assurance/phase-d/redesign/d2/contract-v3.json": "file",
    "docs/assurance/phase-d/redesign/d2/mutation-bank-v2.json": "file",
    "docs/assurance/phase-d/redesign/d2/mutation-bank-v3.json": "file",
    "evidence/phase-d/d0": "tree",
    "evidence/phase-d/d1": "tree",
    "evidence/phase-d/d2": "tree",
    "evidence/phase-d/redesign": "tree",
    "evidence/phase-d/redesign-v3": "tree",
    "evidence/phase-d/full-agent-company-regression.txt": "file",
    "evidence/phase-d/full-pixweave-regression.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-candidate-path-final.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-candidate-verify-final.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-default-verify-handoff.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-final-aggregate-after-verify.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-final-aggregate-before-verify.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-final/evidence-manifest.json": "file",
    "evidence/phase-d/redesign-v4/protocol-final/protocol-result.json": "file",
    "evidence/phase-d/redesign-v4/protocol-handoff/evidence-manifest.json": "file",
    "evidence/phase-d/redesign-v4/protocol-handoff/protocol-result.json": "file",
    "evidence/phase-d/redesign-v4/protocol-handoff-aggregate-after.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-handoff-aggregate-before.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-run-final-definitive.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-run-final.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-run-handoff.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-run.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-verify-definitive.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-verify-final-definitive.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-verify-final.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-verify-handoff.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-verify-svg-final.txt": "file",
    "evidence/phase-d/redesign-v4/protocol-verify.txt": "file",
    "evidence/phase-d/redesign-v4/protocol/evidence-manifest.json": "file",
    "evidence/phase-d/redesign-v4/protocol/protocol-result.json": "file",
}

EXPECTED_INVALID_CLAIMS = {
    "d0_execution_authorized",
    "d0_baseline_current_or_authoritative",
    "d1_start_authorized",
    "d2_start_authorized",
    "d1_started_awaiting_two_human_ratings",
    "d2_started_isolated_treatment",
    "start_bounded_internal_treatment",
    "d1_treatment_execution_authorized",
    "d2_treatment_execution_authorized",
    "d1_treatment_quality_or_preference",
    "d1_candidate_or_comparator_effect",
    "d2_treatment_detection_rate",
    "d2_false_pass_or_false_block_rate",
    "d2_threshold_attainment",
    "d1_or_d2_adoption_or_phase_progression",
    "blocked_dry_run_executed_no_treatments",
    "d2_replayed_real_company_os_controls",
    "d2_thresholds_passed",
    "v3_credentials_provided_an_external_trust_root",
    "v3_freeze_bound_current_head_and_complete_tree",
    "blocked_protocol_checks_complete",
    "evidence_reproduced",
}


def _load_runner():
    runner_path = ROOT / "scripts" / "run_phase_d_d0.py"
    spec = importlib.util.spec_from_file_location("phase_d_d0_tombstone", runner_path)
    if spec is None or spec.loader is None:
        raise AssertionError("D0 runner is not importable")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    return runner


class PhaseDD0PermanentTombstoneTest(unittest.TestCase):
    def test_module_exposes_no_execution_evidence_writer_or_report_helpers(self) -> None:
        removed = {
            "repository_commit",
            "repository_status",
            "run_case",
            "render_report",
            "write_json",
        }
        self.assertTrue(removed.isdisjoint(vars(legacy_d0)))
        self.assertNotIn("subprocess", vars(legacy_d0))
        self.assertNotIn("sys", vars(legacy_d0))

    def test_runner_fails_before_subprocess_write_or_output(self) -> None:
        runner = _load_runner()
        removed = {
            "parse_args",
            "freeze_artifact",
            "materialize_frozen_repositories",
            "verify_regression_counts",
            "build_evidence_manifest",
            "refresh_evidence_manifest",
        }
        self.assertTrue(removed.isdisjoint(vars(runner)))
        self.assertNotIn("subprocess", vars(runner))

        with (
            patch("subprocess.run", side_effect=AssertionError("subprocess invoked")),
            patch.object(Path, "mkdir", side_effect=AssertionError("directory write invoked")),
            patch.object(Path, "write_text", side_effect=AssertionError("file write invoked")),
            patch("builtins.print") as printed,
        ):
            with self.assertRaisesRegex(legacy_d0.D0Error, "tombstone|superseded|disabled"):
                runner.main()
        printed.assert_not_called()

    def test_runtime_sources_have_no_positive_d1_d2_authorization_claims(self) -> None:
        forbidden = {
            "start_authorized",
            "started_awaiting_two_human_ratings",
            "started_isolated_treatment",
            "start_bounded_internal_treatment",
        }
        for relative in ("agent_company/phase_d_d0.py", "scripts/run_phase_d_d0.py"):
            source = (ROOT / relative).read_text(encoding="utf-8").lower()
            with self.subTest(relative=relative):
                self.assertTrue(forbidden.isdisjoint(source.split()))
                self.assertFalse(any(claim in source for claim in forbidden))


class PhaseDV4ExhaustiveSupersessionTest(unittest.TestCase):
    def test_record_has_exact_denylist_invalid_claims_and_freeze_binding(self) -> None:
        freeze = json.loads(V4_FREEZE.read_text(encoding="utf-8"))
        record = json.loads(V4_SUPERSESSION.read_text(encoding="utf-8"))

        denied = {
            str(item["path"]): str(item["scope"])
            for item in record["denylist"]["artifacts"]
        }
        self.assertEqual(denied, EXPECTED_DENIED_ARTIFACTS)
        self.assertEqual(set(record["denylist"]["invalid_claims"]), EXPECTED_INVALID_CLAIMS)
        self.assertEqual(
            record["v4_freeze_binding"],
            {
                "path": "docs/assurance/phase-d/redesign/corrected-freeze-v4.json",
                "sha256": redesign.sha256_file(V4_FREEZE),
                "schema_version": freeze["schema_version"],
                "id": freeze["id"],
                "baseline_review_target": freeze["baseline_review_target"],
                "supersession_protocol_input": freeze["protocol_inputs"]["supersession_record"],
            },
        )
        self.assertFalse(record["v4_status"]["execution_authorized"])
        self.assertFalse(record["v4_status"]["treatment_pass_possible"])

    def test_validator_rejects_coverage_or_freeze_drift_and_consumers_deny_artifacts(self) -> None:
        freeze = redesign.load_json(V4_FREEZE)
        record = redesign.load_json(V4_SUPERSESSION)
        validated = redesign.validate_v4_supersession_record(
            ROOT, V4_FREEZE, freeze, record
        )
        self.assertEqual(validated["denied_artifacts"], EXPECTED_DENIED_ARTIFACTS)

        for denied in EXPECTED_DENIED_ARTIFACTS:
            with self.subTest(denied=denied):
                with self.assertRaisesRegex(redesign.PhaseDRedesignError, "superseded|denied"):
                    redesign.assert_phase_d_artifact_current(denied, record)

        redesign.assert_phase_d_artifact_current(
            "docs/assurance/phase-d/redesign/d1/contract-v4.json", record
        )
        redesign.assert_phase_d_artifact_current(
            "docs/assurance/phase-d/redesign/d1/scenario-bank-v3.json", record
        )

        missing = copy.deepcopy(record)
        missing["denylist"]["artifacts"].pop()
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "exhaustive|denylist|coverage"):
            redesign.validate_v4_supersession_record(ROOT, V4_FREEZE, freeze, missing)

        rebound = copy.deepcopy(record)
        rebound["v4_freeze_binding"]["id"] = "different-freeze"
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "freeze.*binding"):
            redesign.validate_v4_supersession_record(ROOT, V4_FREEZE, freeze, rebound)

        rehashed = copy.deepcopy(record)
        rehashed["v4_freeze_binding"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "freeze.*binding"):
            redesign.validate_v4_supersession_record(ROOT, V4_FREEZE, freeze, rehashed)

    def test_v4_freeze_verification_invokes_supersession_validation(self) -> None:
        with patch.object(
            redesign,
            "validate_v4_supersession_record",
            wraps=redesign.validate_v4_supersession_record,
        ) as validate:
            result = redesign.verify_corrected_freeze(
                ROOT,
                V4_FREEZE,
                allow_development_overlay=True,
            )
        validate.assert_called_once()
        self.assertFalse(result["execution_authorized"])


if __name__ == "__main__":
    unittest.main()
