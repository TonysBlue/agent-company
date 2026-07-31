from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_company.phase_d_redesign as redesign
import agent_company.phase_d_treatments as legacy_treatments


ROOT = Path(__file__).resolve().parents[1]
REDESIGN = ROOT / "docs" / "assurance" / "phase-d" / "redesign"
V4_CONTRACT = REDESIGN / "d2" / "contract-v4.json"
V4_BANK = REDESIGN / "d2" / "mutation-bank-v4.json"
V4_EVIDENCE = ROOT / "evidence" / "phase-d" / "redesign-v4" / "protocol-handoff"
REPORT = ROOT / "evidence" / "phase-d" / "redesign-v4" / "verification-report.md"


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


def _review_target(repository: Path) -> dict[str, object]:
    return {
        "commit": _git(repository, "rev-parse", "HEAD"),
        "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
        "scope": "entire_git_tree",
        "require_clean_worktree": True,
    }


def _complete_observations() -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    contract = redesign.load_json(V4_CONTRACT)
    bank = redesign.load_json(V4_BANK)
    fault_classes = set(contract["required_fault_classes"])
    observations = []
    for frozen in bank["cases"]:
        observation = copy.deepcopy(frozen)
        observation["case_id"] = observation.pop("id")
        outcome = "denied" if observation["fault_class"] in fault_classes else "allowed"
        observation["baseline"] = {"observation": {"outcome": "allowed"}}
        observation["treatment"] = {"observation": {"outcome": outcome}}
        observations.append(observation)
    return contract, bank, observations


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class PhaseDReviewCertificationBlockerTest(unittest.TestCase):
    def test_threshold_api_has_no_injectable_root_attestation_or_verifier_pass_path(self) -> None:
        parameters = inspect.signature(redesign.derive_d2_observation_thresholds).parameters
        self.assertEqual(list(parameters), ["pairs", "contract", "bank"])
        self.assertNotIn("authoritative_root", parameters)
        self.assertNotIn("real_replay_attestation", parameters)

        contract, bank, observations = _complete_observations()
        status_flipped = copy.deepcopy(contract)
        status_flipped["real_production_replay"]["status"] = "implemented_and_verified"
        with patch.object(
            redesign,
            "verify_real_company_os_c2_replay",
            return_value={"verified": True},
            create=True,
        ):
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "certification.*unavailable|real.*replay verifier.*not implemented",
            ):
                redesign.derive_d2_observation_thresholds(
                    observations,
                    contract=status_flipped,
                    bank=bank,
                )

    def test_every_frozen_observation_metadata_field_is_compared_deeply(self) -> None:
        contract, bank, observations = _complete_observations()
        mutations = {
            "target": "invented_target",
            "mutation": {"kind": "invented_mutation"},
            "replay": {"control_id": "invented_control"},
            "protected_tables": ["invented_table"],
            "protected_repo_paths": ["invented/path"],
        }

        for field, replacement in mutations.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(observations)
                changed[0][field] = replacement
                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "frozen observation metadata.*does not match",
                ):
                    redesign.derive_d2_observation_thresholds(
                        changed,
                        contract=contract,
                        bank=bank,
                    )


class PhaseDReviewLegacyTombstoneTest(unittest.TestCase):
    def test_legacy_treatment_module_exposes_no_execution_or_threshold_helpers(self) -> None:
        removed = {
            "verify_start_contracts",
            "blind_assignment",
            "evaluate_d2_results",
            "synthetic_png",
            "d1_payload",
            "run_unittest_case",
        }
        self.assertTrue(removed.isdisjoint(vars(legacy_treatments)))

    def test_legacy_runner_is_a_tombstone_with_no_callable_helpers_or_output(self) -> None:
        runner_path = ROOT / "scripts" / "run_phase_d_treatments.py"
        spec = importlib.util.spec_from_file_location("phase_d_treatment_tombstone", runner_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)

        removed = {
            "git_commit",
            "git_status",
            "clone_detached",
            "load_pixweave_source_edit",
            "artifact_manifest",
            "refresh_evidence_manifest",
            "run_d1",
            "run_d2",
        }
        self.assertTrue(removed.isdisjoint(vars(runner)))
        before = _snapshot(ROOT / "evidence" / "phase-d")
        with self.assertRaisesRegex(runner.PhaseDTreatmentError, "permanently disabled|tombstone"):
            runner.main()
        self.assertEqual(before, _snapshot(ROOT / "evidence" / "phase-d"))


class PhaseDReviewAuthenticationBlockerTest(unittest.TestCase):
    def test_runtime_has_no_signing_helper_and_never_loads_hmac_secrets(self) -> None:
        self.assertNotIn("sign_governance_record", vars(redesign))
        freeze = redesign.load_json(REDESIGN / "corrected-freeze-v4.json")
        with patch("os.open", side_effect=AssertionError("credential file was opened")) as opened:
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "separate.*verifier|credential loading.*disabled",
            ):
                redesign.load_trusted_governance_credentials(ROOT, freeze)
        opened.assert_not_called()

    def test_external_manifest_authentication_fails_before_any_path_access(self) -> None:
        freeze = redesign.load_json(REDESIGN / "corrected-freeze-v4.json")
        with patch("os.open", side_effect=AssertionError("manifest path was opened")) as opened:
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "separate.*verifier|manifest verification.*not implemented",
            ):
                redesign.load_external_review_target(ROOT, freeze)
        opened.assert_not_called()

    def test_status_flips_and_records_cannot_create_execution_authorization(self) -> None:
        freeze = redesign.load_json(REDESIGN / "corrected-freeze-v4.json")
        flipped = copy.deepcopy(freeze)
        flipped["real_production_replay"]["status"] = "implemented_and_verified"
        flipped["execution_gate"]["execution_authorized"] = True
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp)
            authoritative = (
                repository / "docs" / "assurance" / "phase-d" / "redesign"
                / "corrected-freeze-v4.json"
            )
            authoritative.parent.mkdir(parents=True)
            authoritative.write_text(json.dumps(flipped), encoding="utf-8")
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "execution authorization.*unavailable|separate.*verifier",
            ):
                redesign.evaluate_v4_authorization(
                    repository,
                    flipped,
                    {"decision": "approve", "authentication": {"signature": "forged"}},
                    {"decision": "start", "authentication": {"signature": "forged"}},
                    require_execution_authorization=True,
                )


class PhaseDReviewGitBindingTest(unittest.TestCase):
    def _repository(self, root: Path) -> Path:
        repository = root / "repository"
        repository.mkdir()
        _git(repository, "init", "-q")
        _git(repository, "config", "user.name", "Phase D Test")
        _git(repository, "config", "user.email", "phase-d@example.invalid")
        (repository / ".gitignore").write_text("data/\n*.override.py\n", encoding="utf-8")
        (repository / "bound.txt").write_text("reviewed\n", encoding="utf-8")
        _git(repository, "add", ".gitignore", "bound.txt")
        _git(repository, "commit", "-qm", "review target")
        return repository

    def test_skip_worktree_and_assume_unchanged_cannot_hide_tracked_drift(self) -> None:
        flag_commands = ("--skip-worktree", "--assume-unchanged")
        for flag in flag_commands:
            with self.subTest(flag=flag), tempfile.TemporaryDirectory() as tmp:
                repository = self._repository(Path(tmp))
                target = _review_target(repository)
                _git(repository, "update-index", flag, "bound.txt")
                (repository / "bound.txt").write_text("hidden drift\n", encoding="utf-8")
                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "index flag|tracked content.*drift|skip-worktree|assume-unchanged",
                ):
                    redesign.verify_immutable_review_target(repository, target)

    def test_ignored_generated_data_prevents_strict_candidate_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = self._repository(Path(tmp))
            target = _review_target(repository)
            generated = repository / "data" / "artifacts" / "generated.json"
            generated.parent.mkdir(parents=True)
            generated.write_text('{"generated":true}\n', encoding="utf-8")
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "ignored.*forbidden|strict candidate clone",
            ):
                redesign.verify_immutable_review_target(repository, target)


class PhaseDReviewEvidenceBindingTest(unittest.TestCase):
    def test_strict_reproduction_is_blocked_without_external_signed_candidate_manifest(self) -> None:
        before = _snapshot(V4_EVIDENCE)
        with self.assertRaisesRegex(
            redesign.PhaseDRedesignError,
            "signed candidate manifest.*required|candidate verification.*blocked",
        ):
            redesign.verify_redesign_evidence(
                ROOT,
                V4_EVIDENCE,
                freeze_path=REDESIGN / "corrected-freeze-v4.json",
            )
        self.assertEqual(before, _snapshot(V4_EVIDENCE))


class PhaseDReviewSvgRawSyntaxTest(unittest.TestCase):
    def test_svg_rejects_raw_xml_control_syntax_before_element_tree_parse(self) -> None:
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
            b'viewBox="0 0 512 512"><rect width="10" height="10"/></svg>'
        )
        adversarial = {
            "doctype": b"<!DOCTYPE svg>" + svg,
            "xml_declaration": b'<?xml version="1.0"?>' + svg,
            "stylesheet_pi": b'<?xml-stylesheet href="evil.css"?>' + svg,
            "entity_declaration": (
                b'<!DOCTYPE svg [<!ENTITY x "safe">]>' + svg.replace(b"</svg>", b"<title>&x;</title></svg>")
            ),
            "entity_reference": svg.replace(b"</svg>", b"<title>&amp;</title></svg>"),
        }
        for name, artifact in adversarial.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "DOCTYPE|processing instruction|entity|raw XML",
                ):
                    redesign.validate_bounded_svg(artifact)


class PhaseDReviewReportTest(unittest.TestCase):
    def test_report_records_reviewed_candidate_findings_and_rejection_without_stale_claims(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        self.assertIn("3ab23d1630457f63aa310a9a206f5429493ed659", report)
        self.assertRegex(report, r"2\s+Critical.*6\s+High.*2\s+Medium")
        self.assertRegex(report, r"(?i)candidate.*rejected|rejected.*candidate")
        self.assertNotIn("V4 changes remain uncommitted", report)
        self.assertNotIn("No commit or push was performed", report)


if __name__ == "__main__":
    unittest.main()
