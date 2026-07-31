from __future__ import annotations

import importlib.util
import inspect
import json
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_company.phase_d_redesign as redesign


ROOT = Path(__file__).resolve().parents[1]
REDESIGN = ROOT / "docs" / "assurance" / "phase-d" / "redesign"
V4_FREEZE = REDESIGN / "corrected-freeze-v4.json"
RUNNER = ROOT / "scripts" / "run_phase_d_redesign_v4_protocol.py"


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


def _repository(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Phase D Test")
    _git(repository, "config", "user.email", "phase-d@example.invalid")
    (repository / ".gitignore").write_text(
        "data/\nlogs/\narchives/\n.agent-company/\n.venv/\nvenv/\n*.pth\n",
        encoding="utf-8",
    )
    (repository / "bound.txt").write_text("reviewed\n", encoding="utf-8")
    _git(repository, "add", ".gitignore", "bound.txt")
    _git(repository, "commit", "-qm", "review target")
    return repository


class PhaseDMediumDevelopmentOverlayTest(unittest.TestCase):
    def test_public_runner_rejects_verify_with_development_overlay_before_dispatch(self) -> None:
        spec = importlib.util.spec_from_file_location("phase_d_v4_protocol_runner", RUNNER)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "must-not-be-read-or-created"
            with (
                patch.object(
                    runner,
                    "verify_redesign_evidence",
                    side_effect=AssertionError("verification was dispatched"),
                ) as verify,
                patch.object(
                    runner,
                    "run_redesign_dry_run",
                    side_effect=AssertionError("diagnostics were dispatched"),
                ) as diagnose,
            ):
                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "development-overlay.*cannot.*verify|verify.*cannot.*development-overlay",
                ):
                    runner.main(
                        [
                            "--development-overlay",
                            "--verify",
                            "--output",
                            str(output),
                        ]
                    )

            verify.assert_not_called()
            diagnose.assert_not_called()
            self.assertFalse(output.exists())

    def test_verifier_has_no_caller_selected_unbound_success_mode(self) -> None:
        parameters = inspect.signature(redesign.verify_redesign_evidence).parameters
        self.assertEqual(list(parameters), ["root", "expected_output", "freeze_path"])
        self.assertNotIn("require_immutable_head", parameters)

        inaccessible = Path("/expected/evidence/must/not/be/accessed")
        with patch.object(
            redesign,
            "load_json",
            side_effect=AssertionError("unverified evidence was accessed"),
        ) as load:
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "signed candidate manifest.*required|candidate verification.*blocked",
            ):
                redesign.verify_redesign_evidence(
                    ROOT,
                    inaccessible,
                    freeze_path=V4_FREEZE,
                )
        load.assert_not_called()

    def test_development_overlay_outputs_are_explicitly_unverified_non_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "development-diagnostics"
            result = redesign.run_redesign_dry_run(
                ROOT,
                output,
                freeze_path=V4_FREEZE,
                allow_development_overlay=True,
            )
            manifest = redesign.load_json(output / "evidence-manifest.json")
            persisted = redesign.load_json(output / "protocol-result.json")

        for document in (result, persisted, manifest):
            serialized = json.dumps(document, sort_keys=True)
            self.assertIn("development-only", document["schema_version"])
            self.assertEqual(
                document["status"],
                "development_only_unverified_non_candidate",
            )
            self.assertTrue(document["development_only"])
            self.assertFalse(document["verified"])
            self.assertFalse(document["candidate_evidence"])
            self.assertNotIn("evidence_reproduced", serialized)


class PhaseDMediumIgnoredGeneratedContentTest(unittest.TestCase):
    def test_every_ignored_file_category_is_rejected(self) -> None:
        ignored_files = {
            "sqlite": ("data/company.sqlite3", b"sqlite bytes"),
            "wal": ("data/company.sqlite3-wal", b"wal bytes"),
            "shm": ("data/company.sqlite3-shm", b"shm bytes"),
            "journal": ("data/company.sqlite3-journal", b"journal bytes"),
            "json": ("data/artifacts/generated.json", b'{"generated":true}\n'),
            "log": ("logs/worker.log", b"worker stopped\n"),
            "pid": (".agent-company/worker.pid", b"123\n"),
            "lock": ("data/company.sqlite3-worker.lock", b""),
            "code": ("data/foo.py", b"AUTHORIZED = True\n"),
        }
        for name, (relative, content) in ignored_files.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                repository = _repository(Path(tmp))
                target = _review_target(repository)
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "ignored.*forbidden|worktree drift",
                ):
                    redesign.verify_immutable_review_target(repository, target)

    def test_every_ignored_filesystem_object_category_is_rejected(self) -> None:
        object_kinds = ("directory", "socket", "symlink", "executable")
        for object_kind in object_kinds:
            with self.subTest(object_kind=object_kind), tempfile.TemporaryDirectory() as tmp:
                repository = _repository(Path(tmp))
                target = _review_target(repository)
                path = repository / "data" / f"ignored-{object_kind}.sock"
                path.parent.mkdir(parents=True, exist_ok=True)
                open_socket = None
                if object_kind == "directory":
                    path.mkdir()
                elif object_kind == "socket":
                    open_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    open_socket.bind(str(path))
                elif object_kind == "symlink":
                    path.symlink_to(repository / "bound.txt")
                else:
                    path.write_bytes(b"#!/bin/sh\nexit 0\n")
                    os.chmod(path, 0o755)
                try:
                    with self.assertRaisesRegex(
                        redesign.PhaseDRedesignError,
                        "ignored.*forbidden|worktree drift",
                    ):
                        redesign.verify_immutable_review_target(repository, target)
                finally:
                    if open_socket is not None:
                        open_socket.close()

    def test_ignored_runtime_sensitive_unknown_executable_and_link_content_is_rejected(self) -> None:
        attacks = {
            "python_source": ("data/foo.py", b"AUTHORIZED = True\n", False, False),
            "shell_hook": ("logs/deploy.sh", b"#!/bin/sh\nexit 0\n", False, False),
            "native_library": ("archives/inject.so", b"native", False, False),
            "config": (".agent-company/runtime.toml", b"enabled=true\n", False, False),
            "named_config_json": ("data/config.json", b'{"enabled":true}\n', False, False),
            "package_config_json": ("logs/package.json", b'{"scripts":{}}\n', False, False),
            "unknown_json_location": ("data/runtime.json", b'{"enabled":true}\n', False, False),
            "hook_path": ("data/hooks/pre-commit", b"#!/bin/sh\n", False, False),
            "venv_sitecustomize": ("venv/sitecustomize.py", b"import os\n", False, False),
            "pth_injection": (".venv/lib/python/site-packages/inject.pth", b"import payload\n", False, False),
            "unknown_extension": ("data/payload.bin", b"opaque", False, False),
            "executable_json": ("data/artifacts/generated.json", b"{}\n", True, False),
            "symlink_with_allowed_name": ("data/artifacts/generated.json", b"", False, True),
        }
        for name, (relative, content, executable, symlink) in attacks.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                repository = _repository(Path(tmp))
                target = _review_target(repository)
                path = repository / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                if symlink:
                    path.symlink_to(repository / "bound.txt")
                else:
                    path.write_bytes(content)
                if executable:
                    os.chmod(path, 0o755)

                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "ignored.*forbidden|worktree drift",
                ):
                    redesign.verify_immutable_review_target(repository, target)


if __name__ == "__main__":
    unittest.main()
