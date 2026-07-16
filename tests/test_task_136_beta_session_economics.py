from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_company.beta_session import (
    BetaSessionError,
    build_session_record,
    summarize_session_economics,
)
from agent_company.cli import main as cli_main


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "beta-session-economics.json"


class Task136BetaSessionEconomicsTest(unittest.TestCase):
    def session(self, session_id: str = "synthetic-session-001") -> dict:
        return {
            "schema_version": "pixweave-beta-session/v1",
            "protocol_version": "pixweave-controlled-beta/v1.0",
            "session_id": session_id,
            "approval_reference": "internal-synthetic-only",
            "participant_pseudonym": f"reviewer-{session_id}",
            "feedback_id": f"feedback-{session_id}",
            "consent": {
                "granted": True,
                "recorded_at": "2026-07-16T09:00:00+08:00",
                "withdrawal_route_recorded": True,
            },
            "asset_rights": {
                "attested": True,
                "provenance": "locally generated synthetic fixture",
            },
            "contains_sensitive_data": False,
            "scenario": "source-image-edit",
            "intended_outcome": "Exercise local evidence capture",
            "started_at": "2026-07-16T09:01:00+08:00",
            "ended_at": "2026-07-16T09:11:00+08:00",
            "task_outcome": "success",
            "token_usage": {
                "input_tokens": 100,
                "output_tokens": 40,
                "cache_tokens": 10,
                "reasoning_tokens": 5,
                "total_tokens": 155,
                "cost": 0.12,
                "currency": "USD",
                "source": "synthetic-local-usage-log",
            },
            "human_review_minutes": {
                "value": 6,
                "source": "synthetic-review-timer",
            },
            "quality_score": {
                "value": 4,
                "scale_max": 5,
                "source": "synthetic-rubric-v1",
            },
            "artifacts": [{"artifact_id": f"artifact-{session_id}", "sha256": "a" * 64}],
            "quality_review": {"passed": True},
            "issues": [],
            "retention_status": "retained",
        }

    def test_session_record_audits_metrics_and_links_each_observation(self) -> None:
        record = build_session_record(self.session())

        self.assertEqual(record["token_usage"]["total_tokens"], 155)
        self.assertEqual(record["operation_duration_minutes"]["value"], 10.0)
        self.assertEqual(record["human_review_minutes"]["value"], 6)
        self.assertEqual(record["quality_score"]["value"], 4)
        for field in (
            "token_usage",
            "operation_duration_minutes",
            "human_review_minutes",
            "quality_score",
        ):
            self.assertEqual(record[field]["session_id"], "synthetic-session-001")
            self.assertTrue(record[field]["source"])

    def test_missing_observations_remain_not_collected_not_zero(self) -> None:
        data = self.session()
        del data["token_usage"]
        del data["human_review_minutes"]
        del data["quality_score"]

        record = build_session_record(data)

        self.assertEqual(record["token_usage"], "not_collected")
        self.assertEqual(record["human_review_minutes"], "not_collected")
        self.assertEqual(record["quality_score"], "not_collected")
        self.assertNotEqual(record["token_usage"], 0)

    def test_rejects_untraceable_or_inconsistent_metric_observations(self) -> None:
        no_source = self.session()
        del no_source["token_usage"]["source"]
        with self.assertRaisesRegex(BetaSessionError, "token_usage.source"):
            build_session_record(no_source)

        bad_total = self.session()
        bad_total["token_usage"]["total_tokens"] = 999
        with self.assertRaisesRegex(BetaSessionError, "total_tokens must equal"):
            build_session_record(bad_total)

        wrong_link = self.session()
        wrong_link["quality_score"]["session_id"] = "different-session"
        with self.assertRaisesRegex(BetaSessionError, "quality_score.session_id"):
            build_session_record(wrong_link)

    def test_synthetic_summary_reports_cost_efficiency_quality_and_missingness(self) -> None:
        first = self.session("synthetic-session-001")
        second = self.session("synthetic-session-002")
        second["started_at"] = "2026-07-16T10:00:00+08:00"
        second["ended_at"] = "2026-07-16T10:05:00+08:00"
        second["task_outcome"] = "failure"
        second["token_usage"]["cost"] = 0.08
        second["human_review_minutes"]["value"] = 3
        second["quality_score"]["value"] = 5
        third = self.session("synthetic-session-003")
        third["task_outcome"] = "failure"
        for field in ("token_usage", "human_review_minutes", "quality_score"):
            del third[field]

        summary = summarize_session_economics(
            {
                "schema_version": "pixweave-beta-session-economics/v1",
                "dataset_kind": "synthetic",
                "currency": "USD",
                "human_review_hourly_cost": 30,
                "sessions": [first, second, third],
            }
        )

        self.assertFalse(summary["pricing_authorized"])
        self.assertEqual(summary["session_count"], 3)
        self.assertEqual(summary["coverage"]["token_usage"], {"collected": 2, "not_collected": 1})
        self.assertEqual(summary["totals"]["total_tokens"], 310)
        self.assertEqual(summary["efficiency"]["successful_sessions_per_operation_hour"], 2.4)
        self.assertEqual(summary["quality"]["average_score"], 4.5)
        self.assertEqual(summary["unit_economics"]["fully_costed_session_count"], 2)
        self.assertEqual(summary["unit_economics"]["estimated_cost"], 4.7)
        self.assertEqual(summary["unit_economics"]["estimated_cost_per_success"], 4.7)
        self.assertEqual(summary["unit_economics"]["excluded_session_ids"], ["synthetic-session-003"])

    def test_summary_keeps_unobserved_aggregates_not_collected_and_rejects_non_synthetic_data(self) -> None:
        missing = self.session()
        for field in ("token_usage", "human_review_minutes", "quality_score"):
            del missing[field]
        payload = {
            "schema_version": "pixweave-beta-session-economics/v1",
            "dataset_kind": "synthetic",
            "currency": "USD",
            "human_review_hourly_cost": 30,
            "sessions": [missing],
        }

        summary = summarize_session_economics(payload)
        self.assertEqual(summary["totals"]["total_tokens"], "not_collected")
        self.assertEqual(summary["totals"]["human_review_minutes"], "not_collected")
        self.assertEqual(summary["quality"]["average_score"], "not_collected")
        self.assertEqual(summary["unit_economics"]["estimated_cost_per_success"], "not_collected")

        real_data = copy.deepcopy(payload)
        real_data["dataset_kind"] = "customer"
        with self.assertRaisesRegex(BetaSessionError, "dataset_kind must be synthetic"):
            summarize_session_economics(real_data)

    def test_summary_rejects_incomparable_quality_scales(self) -> None:
        first = self.session("synthetic-session-001")
        second = self.session("synthetic-session-002")
        second["quality_score"]["scale_max"] = 10

        with self.assertRaisesRegex(BetaSessionError, "quality_score.scale_max must be consistent"):
            summarize_session_economics(
                {
                    "schema_version": "pixweave-beta-session-economics/v1",
                    "dataset_kind": "synthetic",
                    "currency": "USD",
                    "human_review_hourly_cost": 30,
                    "sessions": [first, second],
                }
            )

    def test_checked_in_synthetic_sample_is_reproducible_through_cli(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "summary.json"
            exit_code = cli_main(["beta-session-economics", str(SAMPLE), "--output", str(output)])

            self.assertEqual(exit_code, 0)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["dataset_kind"], "synthetic")
            self.assertEqual(result["session_count"], 3)
            self.assertFalse(result["external_action_authorized"])


if __name__ == "__main__":
    unittest.main()
