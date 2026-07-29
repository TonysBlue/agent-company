from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_company.phase_d_d0 import (
    D0Error,
    aggregate_results,
    comparator_commits,
    render_report,
    run_case,
    load_json,
    parse_regression_test_count,
    tooling_hashes,
    validate_freeze_chronology,
    validate_case_banks,
    validate_replay_cases,
    verify_frozen_inputs,
)


class PhaseD0BaselineTest(unittest.TestCase):
    def test_repository_freeze_and_case_banks_verify(self) -> None:
        root = Path(__file__).resolve().parents[1]
        freeze_path = root / "docs" / "assurance" / "phase-d" / "d0" / "freeze-manifest-v1.json"

        verified = verify_frozen_inputs(root, freeze_path)
        freeze = load_json(freeze_path)
        product = load_json(root / "docs" / "assurance" / "phase-d" / "d0" / "product-scenario-bank-v1.json")
        controls = load_json(root / "docs" / "assurance" / "phase-d" / "d0" / "control-fault-bank-v1.json")
        comparator = load_json(root / "docs" / "assurance" / "phase-d" / "d0" / "comparator-v1.json")
        cases = validate_replay_cases(product, controls)
        commits = comparator_commits(comparator)

        self.assertEqual(len(verified), 8)
        self.assertEqual(freeze["charter_binding"], {
            "path": "docs/assurance/charters/development-assurance-phase-d-2026-07-29-v1.md",
            "sha256": "54ad4e4d4cc1fd18e30d7ac8f81e328ea1f4cfc08e3c58f8e76079265e5ff21f",
        })
        self.assertEqual(commits, {
            "agent-company": "8a50770b8ff5f954ceeff2680c2ab571605fabe1",
            "pixweave": "d78094f26eb697c810899a40771a8af6dec7ce19",
        })
        self.assertEqual(comparator["repositories"]["agent-company"]["expected_regression_tests"], 220)
        self.assertEqual(comparator["repositories"]["pixweave"]["expected_regression_tests"], 58)
        self.assertEqual(len([case for case in cases if case["domain"] == "product"]), 6)
        self.assertGreaterEqual(len([case for case in cases if case["domain"] == "control"]), 12)

    def test_freeze_and_charter_binding_must_predate_run(self) -> None:
        root = Path(__file__).resolve().parents[1]
        freeze_path = root / "docs" / "assurance" / "phase-d" / "d0" / "freeze-manifest-v1.json"
        freeze = load_json(freeze_path)

        validate_freeze_chronology(
            root,
            freeze,
            "2026-07-29T10:00:01.000000+00:00",
        )

        with self.assertRaisesRegex(D0Error, "must predate baseline run"):
            validate_freeze_chronology(
                root,
                freeze,
                "2026-07-29T08:00:00.000000+00:00",
            )

    def test_tooling_hashes_pin_runner_and_library(self) -> None:
        root = Path(__file__).resolve().parents[1]

        hashes = tooling_hashes(root)

        self.assertEqual(set(hashes), {
            "agent_company/phase_d_d0.py",
            "scripts/run_phase_d_d0.py",
        })
        self.assertTrue(all(len(digest) == 64 for digest in hashes.values()))

    def test_regression_count_is_parsed_and_missing_summary_fails_closed(self) -> None:
        self.assertEqual(parse_regression_test_count("Ran 220 tests in 18.081s\n\nOK\n"), 220)
        with self.assertRaisesRegex(D0Error, "regression test count"):
            parse_regression_test_count("OK without unittest summary")

    def test_frozen_input_verification_rejects_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frozen = root / "bank.json"
            frozen.write_text('{"schema_version":"test/v1"}\n', encoding="utf-8")
            digest = hashlib.sha256(frozen.read_bytes()).hexdigest()
            freeze = root / "freeze.json"
            freeze.write_text(json.dumps({
                "schema_version": "phase-d-d0-freeze/v1",
                "artifacts": [{"path": "bank.json", "sha256": digest}],
            }), encoding="utf-8")

            verified = verify_frozen_inputs(root, freeze)
            self.assertEqual(verified["bank.json"], digest)

            frozen.write_text('{"schema_version":"tampered/v1"}\n', encoding="utf-8")
            with self.assertRaisesRegex(D0Error, "hash mismatch"):
                verify_frozen_inputs(root, freeze)

    def test_case_bank_minimums_and_unique_ids_are_fail_closed(self) -> None:
        product = {"cases": [{"id": f"product-{index}"} for index in range(6)]}
        controls = {"cases": [{"id": f"control-{index}"} for index in range(12)]}

        validate_case_banks(product, controls)

        with self.assertRaisesRegex(D0Error, "at least 6"):
            validate_case_banks({"cases": product["cases"][:5]}, controls)
        duplicate = {"cases": controls["cases"] + [{"id": "product-0"}]}
        with self.assertRaisesRegex(D0Error, "duplicate case id"):
            validate_case_banks(product, duplicate)

    def test_aggregation_preserves_not_collected_and_uses_raw_waits(self) -> None:
        results = [
            self.result("product-1", "product", 10, 30, seeded_fault=False),
            self.result("product-2", "product", 20, 90, seeded_fault=True),
            self.result("control-1", "control", 30, 60, seeded_fault=False),
        ]

        summary = aggregate_results(results)

        self.assertEqual(summary["all"]["case_count"], 3)
        self.assertEqual(summary["all"]["waits_ms"], {
            "queue": {"p50": 20, "p90": 30},
            "automated_gate": {"p50": 60, "p90": 90},
            "cycle": {"p50": 90, "p90": 110},
        })
        self.assertEqual(summary["all"]["model_tokens"], "not_collected")
        self.assertEqual(summary["all"]["hard_gates"], {"passed": 3, "failed": 0})
        self.assertEqual(summary["all"]["defects"], {
            "before_review": {
                "count": 0,
                "severity_weighted": 0,
                "seeded_faults_detected": 0,
                "unexpected_probe_failures": 0,
            },
            "during_independent_review": "not_collected",
            "after_nominal_completion": "not_collected",
        })
        self.assertEqual(summary["all"]["unauthorized_transitions"], {"count": 0, "observed": 3})
        self.assertEqual(summary["all"]["fault_detection"], {"detected": 1, "seeded_faults": 1})
        self.assertEqual(summary["all"]["human_minutes"], {
            "engineering": "not_collected",
            "evaluation": "not_collected",
            "review": "not_collected",
        })
        self.assertEqual(summary["all"]["rework"], {
            "count": "not_collected",
            "minutes": "not_collected",
        })
        self.assertEqual(summary["all"]["reviewer_disagreement"], "not_collected")
        self.assertEqual(summary["all"]["lineage_completeness"], {
            "complete": 3,
            "total": 3,
            "rate": 1.0,
        })
        self.assertEqual(summary["control"]["false_blocks"], {"count": 0, "valid_controls": 1})

    def test_report_labels_host_local_timing_and_records_governance(self) -> None:
        results = [self.result("product-1", "product", 10, 30, seeded_fault=False)]
        report = render_report(
            run={
                "run_id": "phase-d-d0-baseline-v1",
                "started_at": "2026-07-29T08:00:00.000000+00:00",
                "ended_at": "2026-07-29T08:00:01.000000+00:00",
                "repositories": {"agent-company": "a" * 40, "pixweave": "b" * 40},
                "regression_test_counts": {"agent-company": 220, "pixweave": 58},
                "freeze_manifest_sha256": "c" * 64,
                "timing_scope": "host_local",
                "governance": {
                    "independent_review": "approve",
                    "chairman_confirmation": "confirmed_all_five_items",
                },
                "artifact_preparation": {
                    "started_at": "2026-07-29T08:00:00.000000+00:00",
                    "ended_at": "2026-07-29T08:00:00.010000+00:00",
                    "elapsed_ms": 10,
                },
            },
            summary=aggregate_results(results),
            results=results,
        )

        self.assertIn("host-local p50/p90 waits", report)
        self.assertIn("Independent baseline review: `approve`", report)
        self.assertIn("Chairman confirmation: `confirmed_all_five_items`", report)

    def test_replay_can_run_against_a_frozen_detached_repository_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository = root / "frozen-pixweave"
            output = root / "evidence"
            (repository / "tests").mkdir(parents=True)
            (repository / "tests" / "__init__.py").write_text("", encoding="utf-8")
            (repository / "tests" / "test_probe.py").write_text(
                "import unittest\n\n"
                "class ProbeTest(unittest.TestCase):\n"
                "    def test_control(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
            subprocess.run(["git", "add", "."], cwd=repository, check=True)
            subprocess.run(
                ["git", "-c", "user.name=D0", "-c", "user.email=d0@example.invalid", "commit", "-qm", "fixture"],
                cwd=repository,
                check=True,
            )
            result = run_case(
                {
                    "id": "frozen-path-control",
                    "domain": "product",
                    "case_kind": "synthetic_product_replay",
                    "repository": "pixweave",
                    "test_target": "tests.test_probe.ProbeTest.test_control",
                    "seeded_fault": False,
                    "valid_control": True,
                    "severity": "low",
                },
                output,
                freeze_sha256="f" * 64,
                repository_paths={"pixweave": repository},
            )

            self.assertEqual(result["probe_result"], "pass")
            self.assertEqual(result["repository_commit"], subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repository, check=True,
                text=True, stdout=subprocess.PIPE,
            ).stdout.strip())

    @staticmethod
    def result(
        case_id: str,
        domain: str,
        queue_ms: int,
        gate_ms: int,
        *,
        seeded_fault: bool,
    ) -> dict[str, object]:
        result = {
            "case_id": case_id,
            "domain": domain,
            "seeded_fault": seeded_fault,
            "valid_control": not seeded_fault,
            "probe_result": "pass",
            "hard_gate": "pass",
            "defects": {
                "before_review": {"count": 0, "severity_weighted": 0},
                "during_independent_review": "not_collected",
                "after_nominal_completion": "not_collected",
            },
            "unauthorized_transition": False,
            "timestamps": {
                "queued_at": "2026-07-29T08:00:00.000000+00:00",
                "started_at": "2026-07-29T08:00:00.010000+00:00",
                "ended_at": "2026-07-29T08:00:00.040000+00:00",
            },
            "waits_ms": {
                "queue": queue_ms,
                "automated_gate": gate_ms,
                "cycle": queue_ms + gate_ms,
            },
            "model_tokens": "not_collected",
            "human_minutes": {
                "engineering": "not_collected",
                "evaluation": "not_collected",
                "review": "not_collected",
            },
            "rework": {"count": "not_collected", "minutes": "not_collected"},
            "reviewer_disagreement": "not_collected",
            "false_block": False if not seeded_fault else "not_applicable",
            "lineage": {"complete": True, "present": 7, "required": 7},
        }
        result["case_kind"] = "replayed_internal"
        result["evidence"] = {"log_path": f"logs/{case_id}.txt", "log_sha256": "d" * 64}
        return result


if __name__ == "__main__":
    unittest.main()
