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

        self.assertEqual(result["status"], "blocked_protocol_checks_complete")
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
                "immutable_review_target_protocol",
                "external_governance_registry_protocol",
                "d1_static_input_and_renderer_contract",
                "d1_svg_adversarial_validator_canaries",
                "d2_named_production_control_mapping",
                "d2_real_replay_blocker",
                "evidence_manifest_reproducibility_protocol",
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
    def _trust_fixture(self, root: Path) -> tuple[Path, dict[str, object], dict[str, bytes]]:
        repository = root / "repository"
        repository.mkdir(mode=0o700)
        _git(repository, "init", "-q")
        _git(repository, "config", "user.name", "Phase D Test")
        _git(repository, "config", "user.email", "phase-d@example.invalid")
        (repository / "target.txt").write_text("target\n", encoding="utf-8")
        _git(repository, "add", "target.txt")
        _git(repository, "commit", "-qm", "target")
        target = {
            "commit": _git(repository, "rev-parse", "HEAD"),
            "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
            "scope": "entire_git_tree",
            "require_clean_worktree": True,
        }
        trust = root / "external-trust"
        trust.mkdir(mode=0o700)
        secrets = {
            "phase-d-reviewer-v4": b"trusted reviewer secret",
            "phase-d-ceo-v4": b"trusted ceo secret",
        }
        credential_paths = {}
        for key_id, secret in secrets.items():
            path = trust / f"{key_id}.credential"
            path.write_bytes(secret)
            path.chmod(0o600)
            credential_paths[key_id] = str(path)
        registry = trust / "registry.json"
        registry.write_text(json.dumps({
            "schema_version": "phase-d-governance-registry/v4",
            "freeze_id": "phase-d-v4-test-freeze",
            "credentials": [
                {
                    "principal_id": "principal-control-review",
                    "role": "Control & Reliability Reviewer",
                    "key_id": "phase-d-reviewer-v4",
                    "credential_path": credential_paths["phase-d-reviewer-v4"],
                },
                {
                    "principal_id": "principal-ceo",
                    "role": "CEO",
                    "key_id": "phase-d-ceo-v4",
                    "credential_path": credential_paths["phase-d-ceo-v4"],
                },
            ],
        }), encoding="utf-8")
        registry.chmod(0o600)
        freeze = {
            "schema_version": "phase-d-redesign-freeze/v4",
            "id": "phase-d-v4-test-freeze",
            "author_principals": ["principal-ceo", "codex-implementer"],
            "execution_gate": {
                "trusted_registry_path": str(registry),
                "reviewer_identity": {
                    "principal_id": "principal-control-review",
                    "role": "Control & Reliability Reviewer",
                    "key_id": "phase-d-reviewer-v4",
                },
                "ceo_identity": {
                    "principal_id": "principal-ceo",
                    "role": "CEO",
                    "key_id": "phase-d-ceo-v4",
                },
            },
            "real_production_replay": {"status": "implemented_and_verified"},
            "candidate_review_target": None,
        }
        manifest_path = trust / "review-target.json"
        freeze["candidate_review_target_source"] = {
            "kind": "hardened_external_signed_manifest",
            "manifest_path": str(manifest_path),
        }
        manifest = redesign.sign_governance_record(
            {
                "schema_version": "phase-d-review-target/v4",
                "freeze_id": freeze["id"],
                "decision_scope": "review_and_verification_only",
                "target": target,
            },
            principal_id="principal-control-review",
            key_id="phase-d-reviewer-v4",
            credential=secrets["phase-d-reviewer-v4"],
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_path.chmod(0o600)
        freeze_path = repository / "docs" / "assurance" / "phase-d" / "redesign" / "corrected-freeze-v4.json"
        freeze_path.parent.mkdir(parents=True)
        freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
        _git(repository, "add", freeze_path.relative_to(repository).as_posix())
        _git(repository, "commit", "-qm", "authoritative freeze")
        target = {
            "commit": _git(repository, "rev-parse", "HEAD"),
            "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
            "scope": "entire_git_tree",
            "require_clean_worktree": True,
        }
        manifest = redesign.sign_governance_record(
            {
                "schema_version": "phase-d-review-target/v4",
                "freeze_id": freeze["id"],
                "decision_scope": "review_and_verification_only",
                "target": target,
            },
            principal_id="principal-control-review",
            key_id="phase-d-reviewer-v4",
            credential=secrets["phase-d-reviewer-v4"],
        )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_path.chmod(0o600)
        return repository, freeze, secrets

    def test_credentials_load_only_from_freeze_bound_external_hardened_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, freeze, secrets = self._trust_fixture(Path(tmp))

            loaded = redesign.load_trusted_governance_credentials(repository, freeze)

            self.assertEqual(loaded, secrets)

    def test_arbitrary_caller_keys_cannot_authorize_governance_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, freeze, _ = self._trust_fixture(Path(tmp))
            approval = redesign.sign_governance_record(
                {
                    "schema_version": "phase-d-redesign-independent-approval/v4",
                    "decision": "approve",
                    "reviewer_principal": "principal-control-review",
                    "reviewer_role": "Control & Reliability Reviewer",
                    "unresolved_findings": [],
                    "signed_at": "2026-07-30T10:00:00+08:00",
                },
                principal_id="principal-control-review",
                key_id="phase-d-reviewer-v4",
                credential=b"caller supplied attacker key",
            )

            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "forged|authentic|signature"):
                redesign.evaluate_v4_authorization(
                    repository,
                    freeze,
                    approval,
                    None,
                    require_execution_authorization=True,
                )

    def test_repository_freeze_cannot_embed_a_candidate_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, freeze, _ = self._trust_fixture(Path(tmp))
            freeze["candidate_review_target"] = redesign.load_external_review_target(
                repository, freeze
            )
            freeze_path = (
                repository / "docs" / "assurance" / "phase-d" / "redesign"
                / "corrected-freeze-v4.json"
            )
            freeze_path.write_text(json.dumps(freeze), encoding="utf-8")

            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError, "external.*manifest|embedded|self-referential"
            ):
                redesign.evaluate_v4_authorization(repository, freeze, None, None)

    def test_governance_decisions_must_bind_the_freeze_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository, freeze, secrets = self._trust_fixture(Path(tmp))
            target = redesign.load_external_review_target(repository, freeze)
            approval = redesign.sign_governance_record(
                {
                    "schema_version": "phase-d-redesign-independent-approval/v4",
                    "decision": "approve",
                    "reviewer_principal": "principal-control-review",
                    "reviewer_role": "Control & Reliability Reviewer",
                    "reviewed_target": target,
                    "unresolved_findings": [],
                    "signed_at": "2026-07-30T10:00:00+08:00",
                },
                principal_id="principal-control-review",
                key_id="phase-d-reviewer-v4",
                credential=secrets["phase-d-reviewer-v4"],
            )

            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "freeze.*identity"):
                redesign.evaluate_v4_authorization(
                    repository,
                    freeze,
                    approval,
                    None,
                    require_execution_authorization=True,
                )

    def test_caller_cannot_substitute_freeze_registry_and_identities_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, freeze, _ = self._trust_fixture(root)
            substituted = copy.deepcopy(freeze)
            attack_trust = root / "attack-trust"
            attack_trust.mkdir(mode=0o700)
            attack_credentials = {
                "attacker-reviewer": b"attacker reviewer key",
                "attacker-ceo": b"attacker ceo key",
            }
            entries = []
            identities = (
                ("reviewer_identity", "attacker-reviewer-principal", "Attacker Reviewer", "attacker-reviewer"),
                ("ceo_identity", "attacker-ceo-principal", "Attacker CEO", "attacker-ceo"),
            )
            for identity_name, principal_id, role, key_id in identities:
                credential_path = attack_trust / f"{key_id}.credential"
                credential_path.write_bytes(attack_credentials[key_id])
                credential_path.chmod(0o600)
                substituted["execution_gate"][identity_name] = {
                    "principal_id": principal_id,
                    "role": role,
                    "key_id": key_id,
                }
                entries.append({
                    "principal_id": principal_id,
                    "role": role,
                    "key_id": key_id,
                    "credential_path": str(credential_path),
                })
            registry = attack_trust / "registry.json"
            registry.write_text(json.dumps({
                "schema_version": "phase-d-governance-registry/v4",
                "freeze_id": substituted["id"],
                "credentials": entries,
            }), encoding="utf-8")
            registry.chmod(0o600)
            substituted["execution_gate"]["trusted_registry_path"] = str(registry)
            substituted["author_principals"] = []
            substituted_target = redesign.load_external_review_target(repository, freeze)
            approval = redesign.sign_governance_record(
                {
                    "schema_version": "phase-d-redesign-independent-approval/v4",
                    "decision": "approve",
                    "reviewer_principal": "attacker-reviewer-principal",
                    "reviewer_role": "Attacker Reviewer",
                    "reviewed_target": substituted_target,
                    "unresolved_findings": [],
                    "signed_at": "2026-07-30T10:00:00+08:00",
                },
                principal_id="attacker-reviewer-principal",
                key_id="attacker-reviewer",
                credential=attack_credentials["attacker-reviewer"],
            )
            ceo = redesign.sign_governance_record(
                {
                    "schema_version": "phase-d-redesign-ceo-start-decision/v4",
                    "decision": "start",
                    "effective_authorization": True,
                    "ceo_principal": "attacker-ceo-principal",
                    "ceo_role": "Attacker CEO",
                    "approved_target": substituted_target,
                    "approved_independent_approval_sha256": redesign.sha256_bytes(
                        redesign.canonical_json(approval).encode("ascii")
                    ),
                    "signed_at": "2026-07-30T10:01:00+08:00",
                },
                principal_id="attacker-ceo-principal",
                key_id="attacker-ceo",
                credential=attack_credentials["attacker-ceo"],
            )

            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "authoritative.*freeze|substitution"):
                redesign.evaluate_v4_authorization(
                    repository,
                    substituted,
                    approval,
                    ceo,
                    require_execution_authorization=True,
                )

    def test_external_review_target_manifest_requires_trusted_reviewer_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, freeze, secrets = self._trust_fixture(root)
            manifest_path = root / "external-trust" / "review-target.json"
            freeze["candidate_review_target"] = None
            freeze["candidate_review_target_source"] = {
                "kind": "hardened_external_signed_manifest",
                "manifest_path": str(manifest_path),
            }
            manifest = {
                "schema_version": "phase-d-review-target/v4",
                "freeze_id": freeze["id"],
                "decision_scope": "review_and_verification_only",
                "target": {
                    "commit": _git(repository, "rev-parse", "HEAD"),
                    "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
                    "scope": "entire_git_tree",
                    "require_clean_worktree": True,
                },
            }
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_path.chmod(0o600)
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "signature|authenticated"):
                redesign.load_external_review_target(repository, freeze)

            signed = redesign.sign_governance_record(
                manifest,
                principal_id="principal-control-review",
                key_id="phase-d-reviewer-v4",
                credential=secrets["phase-d-reviewer-v4"],
            )
            manifest_path.write_text(json.dumps(signed), encoding="utf-8")
            manifest_path.chmod(0o600)
            self.assertEqual(
                redesign.load_external_review_target(repository, freeze),
                manifest["target"],
            )

    def test_registry_and_credentials_reject_symlinks_and_unsafe_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repository, freeze, _ = self._trust_fixture(root)
            registry = Path(freeze["execution_gate"]["trusted_registry_path"])
            registry.chmod(0o644)
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "0600|permissions"):
                redesign.load_trusted_governance_credentials(repository, freeze)

            registry.chmod(0o600)
            registry.parent.chmod(0o777)
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "directory|permissions"):
                redesign.load_trusted_governance_credentials(repository, freeze)

            registry.parent.chmod(0o700)
            target = registry.with_name("registry-target.json")
            registry.rename(target)
            registry.symlink_to(target)
            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "symlink|regular"):
                redesign.load_trusted_governance_credentials(repository, freeze)


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

            verified = redesign.verify_immutable_review_target(repository, target)
            self.assertEqual(verified["commit"], target["commit"])

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
                        authoritative_root=ROOT,
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
                authoritative_root=ROOT,
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
                real_replay_attestation=fabricated_attestation,
                authoritative_root=ROOT,
            )

        with tempfile.TemporaryDirectory() as tmp:
            authoritative_root = Path(tmp)
            d2_root = (
                authoritative_root / "docs" / "assurance" / "phase-d"
                / "redesign" / "d2"
            )
            d2_root.mkdir(parents=True)
            (d2_root / "contract-v4.json").write_text(
                json.dumps(implemented), encoding="utf-8"
            )
            (d2_root / "mutation-bank-v4.json").write_text(
                json.dumps(bank), encoding="utf-8"
            )
            verifier_result = {
                "verified": True,
                "case_ids": sorted(item["id"] for item in bank["cases"]),
                "control_ids": sorted(
                    item["replay"]["control_id"] for item in bank["cases"]
                ),
            }
            for malformed_name in (
                "extra_top_level_field", "extra_nested_observation_field"
            ):
                with self.subTest(malformed_name=malformed_name):
                    with patch.object(
                        redesign,
                        "verify_real_company_os_c2_replay",
                        return_value=verifier_result,
                        create=True,
                    ):
                        with self.assertRaisesRegex(
                            redesign.PhaseDRedesignError,
                            "malformed|fields|schema",
                        ):
                            redesign.derive_d2_observation_thresholds(
                                malformed_sets[malformed_name],
                                contract=implemented,
                                bank=bank,
                                real_replay_attestation=fabricated_attestation,
                                authoritative_root=authoritative_root,
                            )
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "executable.*replay|real production.*replay|not implemented",
            ):
                redesign.derive_d2_observation_thresholds(
                    pairs,
                    contract=implemented,
                    bank=bank,
                    real_replay_attestation=fabricated_attestation,
                    authoritative_root=authoritative_root,
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
                authoritative_root=ROOT,
            )


class PhaseDRedesignV4EvidenceVerificationTest(unittest.TestCase):
    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def test_verify_mode_uses_temporary_output_and_never_mutates_frozen_evidence(self) -> None:
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

            result = redesign.verify_redesign_evidence(
                ROOT,
                expected,
                freeze_path=V4_FREEZE,
                require_immutable_head=False,
            )

            self.assertEqual(result["status"], "evidence_reproduced")
            self.assertEqual(before, self._snapshot(expected))
            self.assertFalse(any(path.name.startswith("verify-") for path in expected.rglob("*")))

    def test_verify_mode_rejects_evidence_tamper_without_rewriting_it(self) -> None:
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

            with self.assertRaisesRegex(redesign.PhaseDRedesignError, "hash|manifest|tamper"):
                redesign.verify_redesign_evidence(
                    ROOT,
                    expected,
                    freeze_path=V4_FREEZE,
                    require_immutable_head=False,
                )

            self.assertEqual(before, self._snapshot(expected))


if __name__ == "__main__":
    unittest.main()
