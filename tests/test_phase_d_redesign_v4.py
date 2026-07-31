from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
import importlib
from pathlib import Path
from unittest.mock import patch

import agent_company.phase_d_redesign as redesign


ROOT = Path(__file__).resolve().parents[1]
REDESIGN = ROOT / "docs" / "assurance" / "phase-d" / "redesign"
V4_FREEZE = REDESIGN / "corrected-freeze-v4.json"
V4_D2_CONTRACT = REDESIGN / "d2" / "contract-v4.json"
V4_D2_BANK = REDESIGN / "d2" / "mutation-bank-v4.json"


def _git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.strip()


def _complete_observations(
    contract: dict[str, object], bank: dict[str, object]
) -> list[dict[str, object]]:
    fault_classes = set(contract["required_fault_classes"])
    observations = []
    for case in bank["cases"]:
        item = copy.deepcopy(case)
        item["case_id"] = item.pop("id")
        outcome = "denied" if item["fault_class"] in fault_classes else "allowed"
        item["baseline"] = {"observation": {"outcome": "allowed"}}
        item["treatment"] = {"observation": {"outcome": outcome}}
        observations.append(item)
    return observations


class PhaseDRedesignV4BlockedDryRunTest(unittest.TestCase):
    def test_superseded_treatment_helpers_are_not_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "surrogate|superseded|blocked"):
                redesign.create_harness_fixture(Path(tmp) / "surrogate")
            bank = redesign.validate_scenario_bank(
                ROOT, REDESIGN / "d1" / "scenario-bank-v3.json"
            )
            scenario = bank["scenarios"][0]
            source = (ROOT / scenario["source_path"]).read_bytes()
            destination = Path(tmp) / "rater-delivery"
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "superseded|blocked"):
                redesign.render_bounded_artifact(scenario, source)
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "superseded|blocked"):
                redesign._render_structured_artifact(scenario, source)
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "superseded|blocked"):
                redesign.write_delivery_bundle(
                    destination, scenario, b"option-a", b"option-b", {}
                )
            self.assertFalse(destination.exists())
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "superseded|blocked"):
            redesign.execute_d1_workflow({}, {})
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "superseded|blocked"):
            redesign.execute_mutation_pair(
                Path("unused"), {}, {}, {}, Path("unused-evidence")
            )
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "superseded"):
            redesign.verify_corrected_freeze(
                ROOT, REDESIGN / "corrected-freeze-v2.json"
            )

    def test_blocked_dry_run_executes_only_named_non_treatment_protocol_checks(self) -> None:
        forbidden = (
            "execute_d1_workflow",
            "execute_mutation_pair",
            "create_harness_fixture",
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "protocol-evidence"
            with patch.multiple(
                redesign,
                **{name: unittest.mock.DEFAULT for name in forbidden},
            ) as mocks:
                for mocked in mocks.values():
                    mocked.side_effect = AssertionError("treatment workflow was invoked")
                result = redesign.run_redesign_dry_run(
                    ROOT,
                    output,
                    freeze_path=V4_FREEZE,
                    allow_development_overlay=True,
                )

        self.assertEqual(
            result["status"], "development_only_unverified_non_candidate"
        )
        self.assertTrue(result["development_only"])
        self.assertFalse(result["verified"])
        self.assertFalse(result["candidate_evidence"])
        self.assertFalse(result["corrected_treatments_executed"])
        self.assertEqual(result["d1"]["treatment_workflows_executed"], 0)
        self.assertEqual(result["d1"]["artifacts_generated"], 0)
        self.assertEqual(result["d2"]["treatment_workflows_executed"], 0)
        self.assertEqual(result["d2"]["database_mutations_attempted"], 0)
        self.assertFalse(result["d2"]["thresholds_passed"])
        self.assertFalse(result["phase_d_treatment_pass_possible"])
        self.assertEqual(
            set(result["checks_executed"]),
            {
                "baseline_git_object_diagnostic",
                "separate_signature_verifier_blocker",
                "d1_static_input_and_renderer_contract",
                "d1_svg_adversarial_validator_canaries",
                "d2_named_production_control_mapping",
                "d2_real_replay_blocker",
            },
        )
        self.assertEqual(
            {item["case_id"] for item in result["d1"]["svg_validator_canaries"]},
            {
                "absolute_path_inside",
                "relative_path_rejected",
                "stroke_rejected",
                "style_transform_rejected",
                "nested_text_rejected",
                "use_defs_rejected",
                "curve_rejected",
                "arc_rejected",
                "malformed_rejected",
                "foreign_namespace_rejected",
                "external_image_rejected",
                "paint_server_rejected",
                "malformed_path_separators_rejected",
                "path_without_moveto_rejected",
                "incomplete_moveto_rejected",
                "incomplete_lineto_rejected",
                "nested_svg_rejected",
                "embedded_svg_image_rejected",
                "embedded_raster_image_rejected",
                "leaf_text_rejected",
                "malformed_points_rejected",
            },
        )

    def test_blocked_protocol_resolves_each_named_control_and_regression(self) -> None:
        bank = redesign.load_json(V4_D2_BANK)
        bank["cases"][0]["replay"]["entrypoint"] = "missing_control"
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "protocol-evidence"
            with patch.object(redesign, "validate_mutation_bank", return_value=bank):
                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError, "entrypoint|production control"
                ):
                    redesign.run_redesign_dry_run(
                        ROOT,
                        output,
                        freeze_path=V4_FREEZE,
                        allow_development_overlay=True,
                    )
            self.assertFalse(output.exists())


class PhaseDRedesignV4RealReplayTest(unittest.TestCase):
    def test_every_d2_case_names_a_distinct_real_company_os_control_probe(self) -> None:
        contract = redesign.load_json(V4_D2_CONTRACT)
        bank = redesign.validate_mutation_bank(V4_D2_BANK, contract)

        self.assertEqual(contract["real_production_replay"]["status"], "not_implemented")
        self.assertFalse(contract["real_production_replay"]["treatment_pass_possible"])
        cases = bank["cases"]
        self.assertEqual(len(cases), 16)
        self.assertEqual(len({case["replay"]["control_id"] for case in cases}), 16)
        for case in cases:
            replay = case["replay"]
            self.assertEqual(case["target"], "production_control_replay")
            self.assertEqual(case["mutation"]["kind"], "named_control_probe")
            self.assertTrue(str(replay["module"]).startswith("agent_company."))
            self.assertTrue(replay["entrypoint"])
            self.assertTrue(replay["existing_regression_test"])
            module = importlib.import_module(str(replay["module"]))
            entrypoint_parts = str(replay["entrypoint"]).split(".", 1)
            if len(entrypoint_parts) == 1:
                entrypoint = getattr(module, entrypoint_parts[0])
            else:
                entrypoint = getattr(getattr(module, entrypoint_parts[0]), entrypoint_parts[1])
            self.assertTrue(callable(entrypoint))
            loader = unittest.TestLoader()
            suite = loader.loadTestsFromName(
                str(replay["existing_regression_test"])
            )
            self.assertEqual(loader.errors, [])
            self.assertEqual(suite.countTestCases(), 1)
            self.assertNotIn("assurance_state", json.dumps(case, sort_keys=True))
            self.assertNotIn("protect_assurance_state", json.dumps(case, sort_keys=True))

    def test_complete_synthetic_observations_cannot_certify_before_real_replay_exists(self) -> None:
        contract = redesign.load_json(V4_D2_CONTRACT)
        bank = redesign.load_json(V4_D2_BANK)

        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "real production.*replay"):
            redesign.derive_d2_observation_thresholds(
                _complete_observations(contract, bank),
                contract=contract,
                bank=bank,
            )


class PhaseDRedesignV4CredentialTrustTest(unittest.TestCase):
    def test_runtime_credential_and_manifest_verification_are_fail_closed(self) -> None:
        freeze = redesign.load_json(V4_FREEZE)
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "credential loading.*disabled"):
            redesign.load_trusted_governance_credentials(ROOT, freeze)
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "separate signature verifier"):
            redesign.load_external_review_target(ROOT, freeze)

    def test_v4_has_no_execution_authorization_path(self) -> None:
        freeze = redesign.load_json(V4_FREEZE)
        blocked = redesign.evaluate_v4_authorization(ROOT, freeze, None, None)
        self.assertFalse(blocked["execution_authorized"])
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "authorization is unavailable"):
            redesign.evaluate_v4_authorization(
                ROOT,
                freeze,
                {"decision": "approve"},
                {"decision": "start"},
                require_execution_authorization=True,
            )


class PhaseDRedesignV4ImmutableTargetTest(unittest.TestCase):
    def test_exact_head_tree_and_entire_clean_worktree_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            _git(repository, "init", "-q")
            _git(repository, "config", "user.name", "Phase D Test")
            _git(repository, "config", "user.email", "phase-d@example.invalid")
            (repository / "bound.txt").write_text("bound\n", encoding="utf-8")
            _git(repository, "add", "bound.txt")
            _git(repository, "commit", "-qm", "review target")
            target = {
                "commit": _git(repository, "rev-parse", "HEAD"),
                "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
                "scope": "entire_git_tree",
                "require_clean_worktree": True,
            }

            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "^blocked_unavailable_atomic_snapshot:",
            ):
                redesign.verify_immutable_review_target(repository, target)

            weakened_target = {**target, "require_clean_worktree": False}
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError, "clean worktree|review target"
            ):
                redesign.verify_immutable_review_target(repository, weakened_target)

            (repository / "unbound.txt").write_text("drift\n", encoding="utf-8")
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "unbound changed path|worktree drift"):
                redesign.verify_immutable_review_target(repository, target)
            (repository / "unbound.txt").unlink()

            (repository / "bound.txt").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "unbound changed path|worktree drift"):
                redesign.verify_immutable_review_target(repository, target)
            _git(repository, "restore", "bound.txt")

            (repository / "next.txt").write_text("next\n", encoding="utf-8")
            _git(repository, "add", "next.txt")
            _git(repository, "commit", "-qm", "head drift")
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "HEAD.*drift"):
                redesign.verify_immutable_review_target(repository, target)

    def test_v4_freeze_has_no_self_referential_file_hash_graph(self) -> None:
        freeze = redesign.load_json(V4_FREEZE)

        self.assertEqual(freeze["baseline_review_target"], {
            "commit": "33bcb6371e18c08b05c49723282db24389b8bc6c",
            "tree": "1447dcf47adc67ee280720a64abaf094743bdd1c",
            "scope": "entire_git_tree",
            "require_clean_worktree": True,
        })
        self.assertIsNone(freeze["candidate_review_target"])
        self.assertEqual(
            freeze["author_principals"],
            ["principal-ceo", "codex-implementer"],
        )
        self.assertNotIn("bindings", freeze)
        self.assertNotIn("documents", freeze)
        self.assertFalse(freeze["execution_gate"]["execution_authorized"])


class PhaseDRedesignV4SvgSubsetTest(unittest.TestCase):
    def test_svg_validator_rejects_every_unsupported_or_unprovable_bounds_class(self) -> None:
        prefix = b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
        suffix = b"</svg>"
        adversarial = {
            "relative_path": b'<path d="M500 10 l20 0"/>',
            "stroke_overflow": b'<rect x="0" y="0" width="512" height="512" stroke="#000" stroke-width="2"/>',
            "inline_style_transform": b'<rect x="0" y="0" width="10" height="10" style="transform:translate(600px,0)"/>',
            "css_transform": b'<style>rect { transform: translate(600px, 0); }</style><rect x="0" y="0" width="10" height="10"/>',
            "nested_tspan": b'<text x="10" y="20" font-size="12" data-max-width="100"><tspan x="700">escape</tspan></text>',
            "nested_text": b'<text x="10" y="20" font-size="12" data-max-width="100"><text x="700">escape</text></text>',
            "attribute_transform": b'<rect transform="translate(600 0)" x="0" y="0" width="10" height="10"/>',
            "use_defs": b'<defs><rect id="r" x="700" y="0" width="10" height="10"/></defs><use href="#r"/>',
            "curve": b'<path d="M0 0 C0 0 700 0 700 1"/>',
            "arc": b'<path d="M0 0 A700 700 0 0 0 10 10"/>',
            "malformed_geometry": b'<polygon points="0,0 10,wat"/>',
            "unsupported_geometry": b'<g><rect x="700" y="0" width="10" height="10"/></g>',
            "foreign_namespace": b'<evil:rect xmlns:evil="urn:evil" x="0" y="0" width="10" height="10"/>',
            "external_image": b'<image x="0" y="0" width="10" height="10" href="https://example.invalid/image.svg"/>',
            "paint_server_reference": b'<rect x="0" y="0" width="10" height="10" fill="url(https://example.invalid/p.svg#x)"/>',
            "malformed_path_separators": b'<path d="M,,10,,10 L 20 20"/>',
            "path_without_moveto": b'<path d="L 10 10"/>',
            "moveto_without_coordinates": b'<path d="M L 10 10"/>',
            "lineto_closed_without_coordinates": b'<path d="M 10 10 L Z"/>',
            "nested_svg_viewport": b'<svg width="512" height="512" viewBox="0 0 1 1"><rect x="2" y="0" width="1" height="1"/></svg>',
            "embedded_svg_image": b'<image x="0" y="0" width="10" height="10" href="data:image/svg+xml;base64,PHN2Zy8+"/>',
            "embedded_raster_image": b'<image x="0" y="0" width="10" height="10" href="data:image/png;base64,iVBORw0KGgo="/>',
            "leaf_text_unprovable_renderer_bounds": b'<text x="10" y="20" font-family="sans-serif" font-size="12" fill="#000000" textLength="40" lengthAdjust="spacingAndGlyphs">text</text>',
            "malformed_point_separators": b'<polygon points="0,,0 10,10 20,20"/>',
        }

        for name, body in adversarial.items():
            with self.subTest(name=name):
                result = redesign.validate_bounded_svg(prefix + body + suffix)
                self.assertFalse(result["bounded"])
                self.assertTrue(result["overflow"])
                self.assertTrue(result["overflow_reasons"])


class PhaseDRedesignV4ThresholdContractTest(unittest.TestCase):
    def test_thresholds_require_exact_named_contract_and_bank_coverage(self) -> None:
        contract = redesign.load_json(V4_D2_CONTRACT)
        bank = redesign.load_json(V4_D2_BANK)
        pairs = _complete_observations(contract, bank)

        malformed_sets = {
            "incomplete": pairs[:-1],
            "duplicate": pairs + [copy.deepcopy(pairs[0])],
            "bad_outcome": [
                *copy.deepcopy(pairs[:-1]),
                {
                    **copy.deepcopy(pairs[-1]),
                    "treatment": {"observation": {"outcome": "maybe"}},
                },
            ],
            "wrong_class": [
                {**copy.deepcopy(pairs[0]), "fault_class": "invented_blanket_fault"},
                *copy.deepcopy(pairs[1:]),
            ],
            "extra_top_level_field": [
                {**copy.deepcopy(pairs[0]), "asserted_pass": True},
                *copy.deepcopy(pairs[1:]),
            ],
            "extra_nested_observation_field": [
                {
                    **copy.deepcopy(pairs[0]),
                    "treatment": {
                        "observation": {
                            "outcome": "denied",
                            "claimed_real_replay": True,
                        }
                    },
                },
                *copy.deepcopy(pairs[1:]),
            ],
        }
        for name, observations in malformed_sets.items():
            with self.subTest(name=name):
                with self.assertRaises(redesign.PhaseDRedesignError):
                    redesign.derive_d2_observation_thresholds(
                        observations,
                        contract=contract,
                        bank=bank,
                    )

        malformed_bank = copy.deepcopy(bank)
        malformed_bank["cases"][0]["seeded_fault"] = False
        malformed_bank["cases"][0]["valid_control"] = True
        malformed_pairs = _complete_observations(contract, malformed_bank)
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "authoritative|fault|control|material|role"):
            redesign.derive_d2_observation_thresholds(
                malformed_pairs,
                contract={
                    **copy.deepcopy(contract),
                    "real_production_replay": {"status": "implemented_and_verified"},
                },
                bank=malformed_bank,
            )

        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "contract.*bank"):
            redesign.derive_d2_observation_thresholds(pairs[:2])

        implemented = copy.deepcopy(contract)
        implemented["real_production_replay"]["status"] = "implemented_and_verified"
        fabricated_attestation = {
            "schema_version": "phase-d-d2-real-replay-attestation/v4",
            "contract_id": implemented["id"],
            "bank_id": bank["id"],
            "isolated_real_schema": True,
            "named_public_controls_invoked": True,
            "case_ids": sorted(item["id"] for item in bank["cases"]),
        }
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "real production.*replay"):
            redesign.derive_d2_observation_thresholds(
                pairs,
                contract=implemented,
                bank=bank,
            )

        duplicate_control_bank = copy.deepcopy(bank)
        duplicate_control_bank["cases"][1]["replay"]["control_id"] = (
            duplicate_control_bank["cases"][0]["replay"]["control_id"]
        )
        with self.assertRaisesRegex(redesign.PhaseDRedesignError, "real control|replay"):
            redesign.derive_d2_observation_thresholds(
                _complete_observations(implemented, duplicate_control_bank),
                contract=implemented,
                bank=duplicate_control_bank,
            )


class PhaseDRedesignV4EvidenceVerificationTest(unittest.TestCase):
    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_verify_mode_is_blocked_before_reading_development_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = root / "expected"
            redesign.run_redesign_dry_run(
                ROOT,
                expected,
                freeze_path=V4_FREEZE,
                allow_development_overlay=True,
            )
            before = self._snapshot(expected)

            with patch.object(
                redesign,
                "load_json",
                side_effect=AssertionError("development diagnostics were read"),
            ) as load:
                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "signed candidate manifest.*required|candidate verification.*blocked",
                ):
                    redesign.verify_redesign_evidence(
                        ROOT,
                        expected,
                        freeze_path=V4_FREEZE,
                    )

            load.assert_not_called()
            self.assertEqual(before, self._snapshot(expected))

    def test_verify_mode_does_not_inspect_or_rewrite_development_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected = Path(tmp) / "expected"
            redesign.run_redesign_dry_run(
                ROOT,
                expected,
                freeze_path=V4_FREEZE,
                allow_development_overlay=True,
            )
            result_path = expected / "protocol-result.json"
            result_path.write_bytes(result_path.read_bytes() + b"\n")
            before = self._snapshot(expected)

            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "signed candidate manifest.*required|candidate verification.*blocked",
            ):
                redesign.verify_redesign_evidence(
                    ROOT,
                    expected,
                    freeze_path=V4_FREEZE,
                )

            self.assertEqual(before, self._snapshot(expected))


if __name__ == "__main__":
    unittest.main()
