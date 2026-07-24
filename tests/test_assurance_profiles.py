from __future__ import annotations

import unittest

from agent_company.assurance_profiles import ProfileError, validate_profile


class AssuranceProfileTest(unittest.TestCase):
    def test_product_profile_requires_custody_statistics_and_competitor_budget(self) -> None:
        profile = {
            "schema_version": "product-competitive-profile/v1",
            "scenario_id": "source-image-brand-social",
            "datasets": {
                "development": "dev-v1", "regression": "reg-v1",
                "hidden_holdout": "hold-v1", "adversarial": "adv-v1",
            },
            "hard_gates": ["subject_identity", "no_sensitive_data"],
            "pairwise": {"blinded": True, "balanced": True, "minimum_raters": 2},
            "comparator": {"name": "frozen-reference", "version": "v1", "attempt_budget": 3},
            "statistics": {
                "primary_estimand": "blind_preference_win_rate",
                "unit": "source_scenario", "minimum_practical_advantage": 0.05,
                "uncertainty": "clustered_bootstrap",
            },
            "holdout": {"custodian_principal": "principal-evaluator", "max_attempts": 3, "canary_required": True},
        }
        result = validate_profile(profile)
        self.assertEqual(result["profile"], "product-competitive")
        self.assertEqual(len(result["sha256"]), 64)
        broken = dict(profile)
        broken["datasets"] = {"development": "dev-v1"}
        with self.assertRaisesRegex(ProfileError, "dataset partitions"):
            validate_profile(broken)

    def test_control_profile_requires_invariants_faults_and_evidence_semantics(self) -> None:
        profile = {
            "schema_version": "control-plane-reliability-profile/v1",
            "mechanism_id": "c2-approved-for-build",
            "states": ["goal_review", "design_review", "approved_for_build"],
            "invariants": ["no dispatch before G4", "no self approval"],
            "failure_scenarios": ["stale artifact", "reviewer unavailable", "restart"],
            "fitness_checks": ["repository isolation", "principal separation"],
            "evidence_semantics": ["property_test", "fault_injection", "observed_sli"],
            "slo": {"unauthorized_transitions": 0, "audit_completeness": 1.0},
        }
        result = validate_profile(profile)
        self.assertEqual(result["profile"], "control-plane-reliability")
        broken = dict(profile)
        broken["invariants"] = []
        with self.assertRaisesRegex(ProfileError, "invariants"):
            validate_profile(broken)

    def test_profiles_reject_unknown_fields_and_weak_subjective_protocol(self) -> None:
        product = {
            "schema_version": "product-competitive-profile/v1",
            "scenario_id": "x", "datasets": {
                "development": "d", "regression": "r", "hidden_holdout": "h", "adversarial": "a"
            },
            "hard_gates": ["identity"],
            "pairwise": {"blinded": False, "balanced": True, "minimum_raters": 1},
            "comparator": {"name": "ref", "version": "v1", "attempt_budget": 3},
            "statistics": {"primary_estimand": "win", "unit": "scenario", "minimum_practical_advantage": 0.1, "uncertainty": "bootstrap"},
            "holdout": {"custodian_principal": "principal-evaluator", "max_attempts": 3, "canary_required": True},
        }
        with self.assertRaisesRegex(ProfileError, "blinded"):
            validate_profile(product)
        product["pairwise"] = {"blinded": True, "balanced": True, "minimum_raters": 2}
        product["surprise"] = True
        with self.assertRaisesRegex(ProfileError, "unknown or missing"):
            validate_profile(product)


if __name__ == "__main__":
    unittest.main()
