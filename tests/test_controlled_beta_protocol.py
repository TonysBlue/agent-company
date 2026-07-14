from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from agent_company.beta_session import BetaSessionError, build_session_record
from agent_company.cli import main as cli_main


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs" / "controlled-beta-validation-protocol-v1.md"


class ControlledBetaProtocolTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = PROTOCOL.read_text(encoding="utf-8")
        cls.lower = cls.text.lower()

    def test_protocol_is_versioned_and_bounded_by_governance(self) -> None:
        self.assertIn("Protocol version: `pixweave-controlled-beta/v1.0`", self.text)
        self.assertIn("Status: internal draft for Chairman review", self.text)
        self.assertIn("Owner: CEO", self.text)
        self.assertIn("This protocol does not authorize", self.text)
        for reserved_action in [
            "customer outreach",
            "account creation",
            "public release",
            "production deployment",
            "pricing",
            "payment",
            "contracts",
            "processing real customer data",
        ]:
            self.assertIn(reserved_action, self.lower)

    def test_protocol_defines_target_customer_and_core_scenarios(self) -> None:
        self.assertIn("## Target Participant", self.text)
        for required in [
            "small Chinese e-commerce or brand-content team",
            "identifiable visual reviewer",
            "rights-cleared source assets",
            "compare PixWeave with its current workflow",
        ]:
            self.assertIn(required, self.text)
        for scenario in [
            "Generate a channel-ready campaign variant",
            "Edit a rights-cleared product image",
            "Review generated variants",
            "submit structured feedback linked to the artifact",
        ]:
            self.assertIn(scenario, self.text)

    def test_protocol_defines_metrics_and_thresholds_without_inferred_values(self) -> None:
        for metric in [
            "`eligible_session`",
            "`task_success`",
            "`task_success_rate`",
            "`satisfaction`",
            "`elapsed_minutes`",
            "`quality_pass`",
            "`critical_defect`",
            "`high_defect`",
        ]:
            self.assertIn(metric, self.text)
        self.assertIn("Unknown values remain `not_collected`; they are never converted to zero.", self.text)
        self.assertIn("never infer missing responses", self.text)
        self.assertRegex(self.text, re.compile(r"At least 5 .* eligible sessions", re.IGNORECASE))
        self.assertIn("At least 80% task success", self.text)
        self.assertIn("Median satisfaction is at least 4/5", self.text)
        self.assertIn("At least 80% of reviewed final artifacts achieve `quality_pass`", self.text)

    def test_protocol_sets_pause_stop_and_evidence_requirements(self) -> None:
        for stop_condition in [
            "One critical defect",
            "Two high defects in the same workflow within three consecutive sessions",
            "Task success falls below 60%",
            "Median satisfaction falls below 3/5",
            "Required consent, provenance, session, artifact, or issue-link records cannot be reproduced",
            "Backup recovery or access-control verification",
        ]:
            self.assertIn(stop_condition, self.text)
        for evidence_field in [
            "protocol version",
            "approval reference",
            "participant pseudonym",
            "consent record",
            "asset-rights attestation",
            "scenario",
            "intended outcome",
            "timestamps",
            "task outcome",
            "artifact IDs/checksums",
            "quality review",
            "feedback ID",
            "defect IDs",
            "retention/deletion status",
        ]:
            self.assertIn(evidence_field, self.text)
        self.assertIn("Internal demos and synthetic fixtures are product evidence but do not count as real beta sessions", self.text)


class ControlledBetaSessionCaptureTest(unittest.TestCase):
    def fixture(self) -> dict:
        return {
            "schema_version": "pixweave-beta-session/v1",
            "protocol_version": "pixweave-controlled-beta/v1.0",
            "session_id": "internal-session-001",
            "approval_reference": "internal-only-no-customer-data",
            "participant_pseudonym": "internal-reviewer-01",
            "feedback_id": "synthetic-feedback-001",
            "consent": {"granted": True, "recorded_at": "2026-07-13T08:00:00+08:00", "withdrawal_route_recorded": True},
            "asset_rights": {"attested": True, "provenance": "synthetic fixture generated for internal testing"},
            "contains_sensitive_data": False,
            "scenario": "source-image-edit",
            "intended_outcome": "Create a reviewable branded crop",
            "started_at": "2026-07-13T08:01:00+08:00",
            "ended_at": "2026-07-13T08:07:30+08:00",
            "task_outcome": "success",
            "artifacts": [{"artifact_id": "fixture-svg-001", "sha256": "a" * 64}],
            "quality_review": {"passed": True},
            "issues": [{"issue_id": "issue-001", "severity": "low"}],
            "retention_status": "retained",
        }

    def test_builds_auditable_record_and_preserves_missing_values(self) -> None:
        record = build_session_record(self.fixture())
        self.assertEqual(record["elapsed_minutes"], 6.5)
        self.assertEqual(record["satisfaction"], "not_collected")
        self.assertEqual(record["token_usage"], "not_collected")
        self.assertFalse(record["external_action_authorized"])
        self.assertEqual(len(record["session_sha256"]), 64)

    def test_rejects_unsafe_or_invalid_evidence(self) -> None:
        data = self.fixture()
        data["consent"]["granted"] = False
        data["contains_sensitive_data"] = True
        data["artifacts"][0]["sha256"] = "not-a-checksum"
        with self.assertRaises(BetaSessionError) as raised:
            build_session_record(data)
        self.assertIn("consent.granted", str(raised.exception))
        self.assertIn("contains_sensitive_data", str(raised.exception))
        self.assertIn("SHA-256", str(raised.exception))

    def test_cli_writes_validated_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "input.json"
            output_path = Path(temp_dir) / "output.json"
            input_path.write_text(json.dumps(self.fixture()), encoding="utf-8")
            self.assertEqual(cli_main(["beta-session-capture", str(input_path), "--output", str(output_path)]), 0)
            retained = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertTrue(retained["eligible_session"])
            self.assertFalse(retained["external_action_authorized"])


if __name__ == "__main__":
    unittest.main()
