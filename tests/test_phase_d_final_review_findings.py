from __future__ import annotations

import copy
import json
import os
import re
import shutil
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_company.phase_d_redesign as redesign


ROOT = Path(__file__).resolve().parents[1]
REDESIGN = ROOT / "docs" / "assurance" / "phase-d" / "redesign"
V4_FREEZE = REDESIGN / "corrected-freeze-v4.json"
V4_SUPERSESSION = REDESIGN / "supersession-record-v4.json"
V4_SCENARIO_BANK = REDESIGN / "d1" / "scenario-bank-v3.json"
V4_CONTRACT = REDESIGN / "d2" / "contract-v4.json"
V4_BANK = REDESIGN / "d2" / "mutation-bank-v4.json"
REPORT = ROOT / "evidence" / "phase-d" / "redesign-v4" / "verification-report.md"

POSITIVE_V4_EVIDENCE = {
    "evidence/phase-d/redesign-v4/protocol-candidate-path-final.txt",
    "evidence/phase-d/redesign-v4/protocol-candidate-verify-final.txt",
    "evidence/phase-d/redesign-v4/protocol-default-verify-handoff.txt",
    "evidence/phase-d/redesign-v4/protocol-final-aggregate-after-verify.txt",
    "evidence/phase-d/redesign-v4/protocol-final-aggregate-before-verify.txt",
    "evidence/phase-d/redesign-v4/protocol-final/evidence-manifest.json",
    "evidence/phase-d/redesign-v4/protocol-final/protocol-result.json",
    "evidence/phase-d/redesign-v4/protocol-handoff-aggregate-after.txt",
    "evidence/phase-d/redesign-v4/protocol-handoff-aggregate-before.txt",
    "evidence/phase-d/redesign-v4/protocol-handoff/evidence-manifest.json",
    "evidence/phase-d/redesign-v4/protocol-handoff/protocol-result.json",
    "evidence/phase-d/redesign-v4/protocol-run-final-definitive.txt",
    "evidence/phase-d/redesign-v4/protocol-run-final.txt",
    "evidence/phase-d/redesign-v4/protocol-run-handoff.txt",
    "evidence/phase-d/redesign-v4/protocol-run.txt",
    "evidence/phase-d/redesign-v4/protocol-verify-definitive.txt",
    "evidence/phase-d/redesign-v4/protocol-verify-final-definitive.txt",
    "evidence/phase-d/redesign-v4/protocol-verify-final.txt",
    "evidence/phase-d/redesign-v4/protocol-verify-handoff.txt",
    "evidence/phase-d/redesign-v4/protocol-verify-svg-final.txt",
    "evidence/phase-d/redesign-v4/protocol-verify.txt",
    "evidence/phase-d/redesign-v4/protocol/evidence-manifest.json",
    "evidence/phase-d/redesign-v4/protocol/protocol-result.json",
}


def _git(repository: Path, *args: str) -> str:
    completed = redesign.subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        text=True,
        stdout=redesign.subprocess.PIPE,
        stderr=redesign.subprocess.PIPE,
    )
    return completed.stdout.strip()


def _git_input(repository: Path, input_bytes: bytes, *args: str) -> str:
    completed = redesign.subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        input=input_bytes,
        stdout=redesign.subprocess.PIPE,
        stderr=redesign.subprocess.PIPE,
    )
    return os.fsdecode(completed.stdout).strip()


def _repository(root: Path) -> Path:
    repository = root / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Phase D Test")
    _git(repository, "config", "user.email", "phase-d@example.invalid")
    (repository / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    (repository / "bound.txt").write_text("reviewed\n", encoding="utf-8")
    _git(repository, "add", ".gitignore", "bound.txt")
    _git(repository, "commit", "-qm", "review target")
    return repository


def _review_target(repository: Path) -> dict[str, object]:
    return {
        "commit": _git(repository, "rev-parse", "HEAD"),
        "tree": _git(repository, "rev-parse", "HEAD^{tree}"),
        "scope": "entire_git_tree",
        "require_clean_worktree": True,
    }


class PhaseDFinalImmutableTargetFindingTest(unittest.TestCase):
    def test_clean_mutable_checkout_cannot_produce_acceptance_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)

            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "^blocked_unavailable_atomic_snapshot:",
            ):
                redesign.verify_immutable_review_target(repository, target)

    def test_mutations_after_the_last_public_hook_still_cannot_produce_acceptance(self) -> None:
        for mutation in ("head", "tracked"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                repository = _repository(Path(tmp))
                target = _review_target(repository)
                alternate_commit = _git_input(
                    repository,
                    b"post-boundary HEAD\n",
                    "commit-tree",
                    str(target["tree"]),
                )
                original_boundary = redesign._assert_static_worktree_inspection_boundary
                injected = False

                def mutate_after_last_hook(*args: object, **kwargs: object) -> None:
                    nonlocal injected
                    original_boundary(*args, **kwargs)
                    injected = True
                    if mutation == "head":
                        _git(repository, "update-ref", "HEAD", alternate_commit)
                    else:
                        (repository / "bound.txt").write_bytes(b"post-boundary drift\n")

                try:
                    with (
                        patch.object(
                            redesign,
                            "_assert_static_worktree_inspection_boundary",
                            side_effect=mutate_after_last_hook,
                        ),
                        self.assertRaisesRegex(
                            redesign.PhaseDRedesignError,
                            "^blocked_unavailable_atomic_snapshot:",
                        ),
                    ):
                        redesign.verify_immutable_review_target(repository, target)
                finally:
                    if mutation == "head" and injected:
                        _git(repository, "update-ref", "HEAD", str(target["commit"]))
                    elif mutation == "tracked" and injected:
                        (repository / "bound.txt").write_bytes(b"reviewed\n")
                self.assertTrue(injected)

    def test_git_object_diagnostic_is_unverified_and_cannot_authorize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)

            diagnostic = redesign._inspect_development_git_object_target(
                repository,
                target,
            )

        self.assertEqual(
            diagnostic["status"],
            "development_only_unverified_git_object_diagnostic",
        )
        self.assertTrue(diagnostic["development_only"])
        self.assertFalse(diagnostic["verified"])
        self.assertFalse(diagnostic["candidate_evidence"])
        self.assertFalse(diagnostic["authorization_eligible"])
        self.assertEqual(
            diagnostic["diagnostic_scope"],
            "git_objects_only_not_worktree",
        )
        self.assertNotIn("scope", diagnostic)
        self.assertNotIn("entire_git_tree", json.dumps(diagnostic, sort_keys=True))

        freeze = redesign.verify_corrected_freeze(
            ROOT,
            V4_FREEZE,
            allow_development_overlay=True,
        )
        freeze_diagnostic = freeze["development_git_object_diagnostic"]
        self.assertEqual(freeze_diagnostic["status"], diagnostic["status"])
        self.assertFalse(freeze_diagnostic["verified"])
        self.assertFalse(freeze_diagnostic["candidate_evidence"])
        self.assertFalse(freeze_diagnostic["authorization_eligible"])
        self.assertNotIn("scope", freeze_diagnostic)
        self.assertNotIn("target_verification", freeze)
        self.assertFalse(freeze["execution_authorized"])

    def test_transient_replace_ref_is_rejected_at_each_object_read_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            replacement_commit = _git_input(
                repository,
                b"transient replacement\n",
                "commit-tree",
                str(target["tree"]),
            )
            original_run_git_bytes = redesign._run_git_bytes
            injected = False

            def read_with_transient_replace(root: Path, *args: str) -> bytes:
                nonlocal injected
                if injected or not args or args[0] != "cat-file":
                    return original_run_git_bytes(root, *args)
                injected = True
                _git(
                    repository,
                    "update-ref",
                    f"refs/replace/{target['commit']}",
                    replacement_commit,
                )
                try:
                    return original_run_git_bytes(root, *args)
                finally:
                    _git(
                        repository,
                        "update-ref",
                        "-d",
                        f"refs/replace/{target['commit']}",
                    )

            with (
                patch.object(
                    redesign,
                    "_run_git_bytes",
                    side_effect=read_with_transient_replace,
                ),
                self.assertRaisesRegex(redesign.PhaseDRedesignError, "replace ref"),
            ):
                redesign.verify_immutable_review_target(repository, target)
            self.assertTrue(injected)

    def test_transient_object_metadata_is_rejected_at_each_object_read_boundary(self) -> None:
        metadata_cases = {
            "alternates": ("objects/info/alternates", b"{alternate}\n"),
            "grafts": ("info/grafts", b""),
            "shallow": ("shallow", b""),
        }
        for name, (relative, template) in metadata_cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                repository = _repository(Path(tmp))
                target = _review_target(repository)
                alternate = Path(tmp) / "alternate-objects"
                alternate.mkdir()
                metadata_path = repository / ".git" / relative
                content = template.replace(b"{alternate}", os.fsencode(alternate))
                original_run_git_bytes = redesign._run_git_bytes
                injected = False

                def read_with_transient_metadata(root: Path, *args: str) -> bytes:
                    nonlocal injected
                    if injected or not args or args[0] != "cat-file":
                        return original_run_git_bytes(root, *args)
                    injected = True
                    metadata_path.parent.mkdir(parents=True, exist_ok=True)
                    metadata_path.write_bytes(content)
                    try:
                        return original_run_git_bytes(root, *args)
                    finally:
                        metadata_path.unlink(missing_ok=True)

                with (
                    patch.object(
                        redesign,
                        "_run_git_bytes",
                        side_effect=read_with_transient_metadata,
                    ),
                    self.assertRaisesRegex(
                        redesign.PhaseDRedesignError,
                        "alternate|graft|shallow|metadata",
                    ),
                ):
                    redesign.verify_immutable_review_target(repository, target)
                self.assertTrue(injected)

    def test_transient_object_directory_indirection_is_rejected_around_object_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            object_directory = repository / ".git" / "objects"
            saved_directory = repository / ".git" / "objects.saved"
            original_run_git_bytes = redesign._run_git_bytes
            injected = False

            def read_through_transient_symlink(root: Path, *args: str) -> bytes:
                nonlocal injected
                if injected or not args or args[0] != "cat-file":
                    return original_run_git_bytes(root, *args)
                injected = True
                object_directory.rename(saved_directory)
                object_directory.symlink_to(saved_directory, target_is_directory=True)
                try:
                    return original_run_git_bytes(root, *args)
                finally:
                    object_directory.unlink()
                    saved_directory.rename(object_directory)

            with (
                patch.object(
                    redesign,
                    "_run_git_bytes",
                    side_effect=read_through_transient_symlink,
                ),
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "object directory|metadata|symlink|indirection",
                ),
            ):
                redesign.verify_immutable_review_target(repository, target)
            self.assertTrue(injected)

    def test_transient_git_config_and_nested_object_metadata_are_rejected_around_reads(self) -> None:
        for name in ("config", "nested_object_metadata"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                repository = _repository(Path(tmp))
                target = _review_target(repository)
                config_path = repository / ".git" / "config"
                pack_metadata = repository / ".git" / "objects" / "pack" / "transient-metadata"
                original_config = config_path.read_bytes()
                original_run_git_bytes = redesign._run_git_bytes
                injected = False

                def read_with_transient_metadata(root: Path, *args: str) -> bytes:
                    nonlocal injected
                    if injected or not args or args[0] != "cat-file":
                        return original_run_git_bytes(root, *args)
                    injected = True
                    if name == "config":
                        config_path.write_bytes(original_config + b"# transient\n")
                    else:
                        pack_metadata.write_bytes(b"transient object metadata\n")
                    try:
                        return original_run_git_bytes(root, *args)
                    finally:
                        if name == "config":
                            config_path.write_bytes(original_config)
                        else:
                            pack_metadata.unlink()

                with (
                    patch.object(
                        redesign,
                        "_run_git_bytes",
                        side_effect=read_with_transient_metadata,
                    ),
                    self.assertRaisesRegex(
                        redesign.PhaseDRedesignError,
                        "config|object metadata|changed around",
                    ),
                ):
                    redesign.verify_immutable_review_target(repository, target)
                self.assertTrue(injected)

    def test_supplied_root_must_be_the_exact_canonical_worktree_top_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            descendant = repository / "ignored"
            descendant.mkdir()
            shutil.copy2(repository / ".gitignore", descendant / ".gitignore")
            shutil.copy2(repository / "bound.txt", descendant / "bound.txt")

            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "top-level|root|canonical|worktree",
            ):
                redesign.verify_immutable_review_target(descendant, target)

            alias = Path(tmp) / "repository-alias"
            alias.symlink_to(repository, target_is_directory=True)
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "top-level|root|canonical|symlink|identity",
            ):
                redesign._inspect_development_git_object_target(alias, target)

    def test_linked_worktree_is_rejected_as_an_ambiguous_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            linked = Path(tmp) / "linked"
            _git(repository, "worktree", "add", "--detach", "-q", str(linked))

            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError,
                "linked worktree|ambiguous|common.*directory|root",
            ):
                redesign.verify_immutable_review_target(linked, target)

    def test_root_identity_drift_at_the_final_boundary_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            displaced = Path(tmp) / "displaced-repository"
            original_metadata_check = redesign._assert_git_metadata_unchanged
            original_replace_check = redesign._assert_no_replace_refs
            final_boundary = False
            replaced = False

            def mark_final_boundary(*args: object, **kwargs: object) -> None:
                nonlocal final_boundary
                original_metadata_check(*args, **kwargs)
                final_boundary = True

            def replace_root_at_final_check(root: Path) -> None:
                nonlocal replaced
                if final_boundary and not replaced:
                    repository.rename(displaced)
                    shutil.copytree(displaced, repository, symlinks=True)
                    replaced = True
                original_replace_check(root)

            with (
                patch.object(
                    redesign,
                    "_assert_git_metadata_unchanged",
                    side_effect=mark_final_boundary,
                ),
                patch.object(
                    redesign,
                    "_assert_no_replace_refs",
                    side_effect=replace_root_at_final_check,
                ),
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "root.*(?:changed|identity|drift)|identity.*root",
                ),
            ):
                redesign.verify_immutable_review_target(repository, target)
            self.assertTrue(final_boundary)
            self.assertTrue(replaced)

    def test_actual_return_boundary_rejects_head_and_checkout_mutations(self) -> None:
        cases = (
            "head",
            "tracked",
            "untracked",
            "fifo",
            "socket",
            "symlink",
        )
        for trigger in ("last_object_read", "final_metadata_read"):
            for case in cases:
                with (
                    self.subTest(trigger=trigger, case=case),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    repository = _repository(Path(tmp))
                    target = _review_target(repository)
                    alternate_commit = _git_input(
                        repository,
                        b"alternate HEAD\n",
                        "commit-tree",
                        str(target["tree"]),
                    )
                    injected = False
                    open_socket: socket.socket | None = None
                    changed_path = repository / f"late-{case}"

                    def mutate_after_current_final_check() -> None:
                        nonlocal injected, open_socket
                        if injected:
                            return
                        injected = True
                        if case == "head":
                            _git(repository, "update-ref", "HEAD", alternate_commit)
                        elif case == "tracked":
                            (repository / "bound.txt").write_bytes(b"late drift\n")
                        elif case == "untracked":
                            changed_path.write_bytes(b"late untracked drift\n")
                        elif case == "fifo":
                            os.mkfifo(changed_path)
                        elif case == "socket":
                            open_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                            open_socket.bind(str(changed_path))
                        else:
                            changed_path.symlink_to(repository / "bound.txt")

                    original_object_read = redesign._read_bound_git_object
                    tree_reads = 0

                    def mutate_during_last_object_read(
                        root: Path,
                        object_id: str,
                        object_kind: str,
                        object_format: str,
                        repository_binding: object = None,
                    ) -> bytes:
                        nonlocal tree_reads
                        content = original_object_read(
                            root,
                            object_id,
                            object_kind,
                            object_format,
                            repository_binding,
                        )
                        if object_kind == "tree" and object_id == target["tree"]:
                            tree_reads += 1
                            if tree_reads == 2:
                                mutate_after_current_final_check()
                        return content

                    original_metadata_check = (
                        redesign._assert_git_repository_snapshot_unchanged
                    )

                    def mutate_during_last_metadata_read(
                        *args: object, **kwargs: object
                    ) -> None:
                        original_metadata_check(*args, **kwargs)
                        mutate_after_current_final_check()

                    try:
                        object_patch = patch.object(
                            redesign,
                            "_read_bound_git_object",
                            side_effect=mutate_during_last_object_read,
                        )
                        metadata_patch = patch.object(
                            redesign,
                            "_assert_git_repository_snapshot_unchanged",
                            side_effect=mutate_during_last_metadata_read,
                        )
                        if trigger == "last_object_read":
                            with (
                                object_patch,
                                self.assertRaisesRegex(
                                    redesign.PhaseDRedesignError,
                                    "HEAD|checkout|filesystem|worktree|tracked|unbound|drift|repository metadata",
                                ),
                            ):
                                redesign.verify_immutable_review_target(repository, target)
                        else:
                            with (
                                metadata_patch,
                                self.assertRaisesRegex(
                                    redesign.PhaseDRedesignError,
                                    "HEAD|checkout|filesystem|worktree|tracked|unbound|drift|repository metadata",
                                ),
                            ):
                                redesign.verify_immutable_review_target(repository, target)
                    finally:
                        if open_socket is not None:
                            open_socket.close()
                        if case == "head" and injected:
                            _git(repository, "update-ref", "HEAD", str(target["commit"]))
                        elif case == "tracked" and injected:
                            (repository / "bound.txt").write_bytes(b"reviewed\n")
                        elif changed_path.is_symlink() or changed_path.exists():
                            changed_path.unlink()
                    self.assertTrue(injected)

    def test_development_object_target_has_a_final_repository_boundary(self) -> None:
        for trigger in ("last_object_read", "final_metadata_read"):
            for case in ("head", "config"):
                with (
                    self.subTest(trigger=trigger, case=case),
                    tempfile.TemporaryDirectory() as tmp,
                ):
                    repository = _repository(Path(tmp))
                    target = _review_target(repository)
                    alternate_commit = _git_input(
                        repository,
                        b"development alternate HEAD\n",
                        "commit-tree",
                        str(target["tree"]),
                    )
                    config_path = repository / ".git" / "config"
                    original_config = config_path.read_bytes()
                    original_object_read = redesign._read_bound_git_object
                    original_metadata_check = (
                        redesign._assert_git_repository_snapshot_unchanged
                    )
                    tree_reads = 0
                    injected = False

                    def inject() -> None:
                        nonlocal injected
                        if injected:
                            return
                        injected = True
                        if case == "head":
                            _git(repository, "update-ref", "HEAD", alternate_commit)
                        else:
                            config_path.write_bytes(original_config + b"# late drift\n")

                    def mutate_after_last_object_read(
                        root: Path,
                        object_id: str,
                        object_kind: str,
                        object_format: str,
                        repository_binding: object = None,
                    ) -> bytes:
                        nonlocal tree_reads
                        content = original_object_read(
                            root,
                            object_id,
                            object_kind,
                            object_format,
                            repository_binding,
                        )
                        if object_kind == "tree" and object_id == target["tree"]:
                            tree_reads += 1
                            if tree_reads == 2:
                                inject()
                        return content

                    def mutate_after_last_metadata_read(
                        *args: object, **kwargs: object
                    ) -> None:
                        original_metadata_check(*args, **kwargs)
                        inject()

                    try:
                        if trigger == "last_object_read":
                            selected_patch = patch.object(
                                redesign,
                                "_read_bound_git_object",
                                side_effect=mutate_after_last_object_read,
                            )
                        else:
                            selected_patch = patch.object(
                                redesign,
                                "_assert_git_repository_snapshot_unchanged",
                                side_effect=mutate_after_last_metadata_read,
                            )
                        with (
                            selected_patch,
                            self.assertRaisesRegex(
                                redesign.PhaseDRedesignError,
                                "repository|metadata|config|HEAD|changed|boundary",
                            ),
                        ):
                            redesign._inspect_development_git_object_target(
                                repository, target
                            )
                    finally:
                        if case == "head" and injected:
                            _git(repository, "update-ref", "HEAD", str(target["commit"]))
                        config_path.write_bytes(original_config)
                    self.assertTrue(injected)

    def test_development_object_target_rejects_replace_refs_before_object_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            replacement_commit = _git_input(
                repository,
                b"development replacement\n",
                "commit-tree",
                str(target["tree"]),
            )
            _git(
                repository,
                "replace",
                str(target["commit"]),
                replacement_commit,
            )
            calls: list[list[str]] = []
            original_run = redesign.subprocess.run

            def observe_run(*args: object, **kwargs: object) -> object:
                command = args[0] if args else kwargs.get("args")
                if isinstance(command, list):
                    calls.append([str(item) for item in command])
                return original_run(*args, **kwargs)

            with (
                patch.object(redesign.subprocess, "run", side_effect=observe_run),
                self.assertRaisesRegex(redesign.PhaseDRedesignError, "replace ref"),
            ):
                redesign._inspect_development_git_object_target(repository, target)

            object_reads = [
                command
                for command in calls
                if "cat-file" in command
                or "ls-tree" in command
                or any("^{tree}" in argument for argument in command)
            ]
            self.assertEqual(object_reads, [])

    def test_real_commit_and_tree_replace_refs_fail_before_object_reads(self) -> None:
        for object_kind in ("commit", "tree"):
            with self.subTest(object_kind=object_kind), tempfile.TemporaryDirectory() as tmp:
                repository = _repository(Path(tmp))
                target = _review_target(repository)
                if object_kind == "commit":
                    replaced_object = target["commit"]
                    replacement_object = _git_input(
                        repository,
                        b"replacement commit\n",
                        "commit-tree",
                        str(target["tree"]),
                    )
                else:
                    replaced_object = target["tree"]
                    replacement_object = _git_input(repository, b"", "mktree")
                _git(
                    repository,
                    "replace",
                    str(replaced_object),
                    replacement_object,
                )

                calls: list[list[str]] = []
                original_run = redesign.subprocess.run

                def observe_run(*args: object, **kwargs: object) -> object:
                    command = args[0] if args else kwargs.get("args")
                    if isinstance(command, list):
                        calls.append([str(item) for item in command])
                    return original_run(*args, **kwargs)

                with (
                    patch.object(redesign.subprocess, "run", side_effect=observe_run),
                    self.assertRaisesRegex(redesign.PhaseDRedesignError, "replace ref"),
                ):
                    redesign.verify_immutable_review_target(repository, target)

                object_reads = [
                    command
                    for command in calls
                    if "cat-file" in command
                    or "ls-tree" in command
                    or any("^{tree}" in argument for argument in command)
                ]
                self.assertEqual(
                    object_reads,
                    [],
                    "replace refs must be rejected before immutable object/tree reads",
                )

    def test_replace_ref_created_during_verification_is_rejected_at_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            replacement_commit = _git_input(
                repository,
                b"same-tree replacement\n",
                "commit-tree",
                str(target["tree"]),
            )
            original_verify_worktree = redesign._verify_complete_worktree

            def inject_replace_ref(*args: object, **kwargs: object) -> None:
                original_verify_worktree(*args, **kwargs)
                _git(
                    repository,
                    "replace",
                    str(target["commit"]),
                    replacement_commit,
                )

            with (
                patch.object(
                    redesign,
                    "_verify_complete_worktree",
                    side_effect=inject_replace_ref,
                ),
                self.assertRaisesRegex(redesign.PhaseDRedesignError, "replace ref"),
            ):
                redesign.verify_immutable_review_target(repository, target)

    def test_git_reads_disable_replacements_and_reject_semantic_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            calls: list[list[str]] = []
            original_run = redesign.subprocess.run

            def observe_run(*args: object, **kwargs: object) -> object:
                command = args[0] if args else kwargs.get("args")
                if isinstance(command, list):
                    calls.append([str(item) for item in command])
                return original_run(*args, **kwargs)

            with (
                patch.object(redesign.subprocess, "run", side_effect=observe_run),
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "^blocked_unavailable_atomic_snapshot:",
                ),
            ):
                redesign.verify_immutable_review_target(repository, target)

            git_reads = [command for command in calls if command and command[0] == "git"]
            self.assertTrue(git_reads)
            self.assertTrue(
                all("--no-replace-objects" in command for command in git_reads),
                "every Git verification read must explicitly disable replacement objects",
            )
            self.assertFalse(any("ls-tree" in command for command in git_reads))
            self.assertTrue(
                any("cat-file" in command and "commit" in command for command in git_reads)
            )
            self.assertTrue(
                any("cat-file" in command and "tree" in command for command in git_reads)
            )

            semantic_environments = (
                {"GIT_REPLACE_REF_BASE": "refs/alternate-replacements/"},
                {"GIT_OBJECT_DIRECTORY": str(repository / ".git" / "objects")},
                {
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(
                        repository / ".git" / "objects"
                    )
                },
                {"GIT_GRAFT_FILE": str(repository / ".git" / "info" / "grafts")},
                {"GIT_SHALLOW_FILE": str(repository / ".git" / "shallow")},
                {"GIT_CONFIG_GLOBAL": os.devnull},
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "core.useReplaceRefs",
                    "GIT_CONFIG_VALUE_0": "false",
                },
            )
            for environment in semantic_environments:
                with (
                    self.subTest(environment=environment),
                    patch.dict(os.environ, environment, clear=False),
                    self.assertRaisesRegex(
                        redesign.PhaseDRedesignError,
                        "Git.*environment|environment.*Git",
                    ),
                ):
                    redesign.verify_immutable_review_target(repository, target)

    def test_only_bracketed_cat_file_commands_may_read_git_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            commands: list[tuple[str, ...]] = []
            original_run_git = redesign._run_git
            original_run_git_bytes = redesign._run_git_bytes

            def observe_text(root: Path, *args: str) -> str:
                commands.append(tuple(args))
                return original_run_git(root, *args)

            def observe_bytes(root: Path, *args: str) -> bytes:
                commands.append(tuple(args))
                return original_run_git_bytes(root, *args)

            with (
                patch.object(redesign, "_run_git", side_effect=observe_text),
                patch.object(redesign, "_run_git_bytes", side_effect=observe_bytes),
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "^blocked_unavailable_atomic_snapshot:",
                ),
            ):
                redesign.verify_immutable_review_target(repository, target)

            object_reading_commands = {
                "status",
                "diff",
                "show",
                "log",
                "ls-tree",
                "read-tree",
                "checkout",
                "archive",
            }
            unbracketed = [
                command
                for command in commands
                if command and command[0] in object_reading_commands
            ]
            self.assertEqual(
                unbracketed,
                [],
                "immutable object reads must use the explicitly bracketed cat-file path",
            )
            self.assertTrue(any(command and command[0] == "cat-file" for command in commands))

    def test_filesystem_walk_rejects_paths_even_when_git_inventory_reports_none(self) -> None:
        kinds = ("regular", "empty_directory", "fifo", "socket", "symlink")
        original_run_git = redesign._run_git

        def hide_git_inventory(root: Path, *args: str) -> str:
            if args and args[0] == "status":
                return ""
            if args and args[0] == "ls-files" and "--others" in args:
                return ""
            return original_run_git(root, *args)

        for kind in kinds:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                repository = _repository(Path(tmp))
                target = _review_target(repository)
                path = repository / f"untracked-{kind}"
                open_socket = None
                if kind == "regular":
                    path.write_bytes(b"untracked\n")
                elif kind == "empty_directory":
                    path.mkdir()
                elif kind == "fifo":
                    os.mkfifo(path)
                elif kind == "socket":
                    open_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    open_socket.bind(str(path))
                else:
                    path.symlink_to(repository / "bound.txt")
                try:
                    with (
                        patch.object(redesign, "_run_git", side_effect=hide_git_inventory),
                        self.assertRaisesRegex(
                            redesign.PhaseDRedesignError,
                            "filesystem|untracked|unbound|special|worktree drift",
                        ),
                    ):
                        redesign.verify_immutable_review_target(repository, target)
                finally:
                    if open_socket is not None:
                        open_socket.close()

    def test_tracked_file_replacement_between_lstat_and_open_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            original_open = os.open
            replaced = False

            def replace_before_open(
                path: str | bytes | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal replaced
                if path == "bound.txt" and dir_fd is not None and not replaced:
                    replacement = repository / "replacement.txt"
                    replacement.write_text("reviewed\n", encoding="utf-8")
                    os.replace(replacement, repository / "bound.txt")
                    replaced = True
                return original_open(path, flags, mode, dir_fd=dir_fd)

            with (
                patch.object(redesign.os, "open", side_effect=replace_before_open),
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "race|replaced|identity|worktree drift",
                ),
            ):
                redesign.verify_immutable_review_target(repository, target)

    def test_tracked_regular_files_are_opened_nonblocking_against_special_object_races(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repository = _repository(Path(tmp))
            target = _review_target(repository)
            original_open = os.open
            tracked_file_flags: list[int] = []

            def observe_open_flags(
                path: str | bytes | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                if path == "bound.txt" and dir_fd is not None:
                    tracked_file_flags.append(flags)
                return original_open(path, flags, mode, dir_fd=dir_fd)

            with (
                patch.object(redesign.os, "open", side_effect=observe_open_flags),
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "^blocked_unavailable_atomic_snapshot:",
                ),
            ):
                redesign.verify_immutable_review_target(repository, target)

            self.assertEqual(len(tracked_file_flags), 2)
            self.assertTrue(
                all(flags & os.O_NONBLOCK for flags in tracked_file_flags),
                "a tracked file open can block if a special object replaces it after lstat",
            )


class PhaseDFinalSupersessionFindingTest(unittest.TestCase):
    def test_every_preserved_positive_v4_artifact_and_claim_is_denied(self) -> None:
        evidence_root = ROOT / "evidence" / "phase-d" / "redesign-v4"
        observed = {
            path.relative_to(ROOT).as_posix()
            for path in evidence_root.rglob("*")
            if path.is_file()
            and (
                path.name.startswith("protocol-")
                or any(part in {"protocol", "protocol-final", "protocol-handoff"} for part in path.parts)
            )
        }
        self.assertEqual(observed, POSITIVE_V4_EVIDENCE)
        self.assertTrue(
            {
                "evidence/phase-d/redesign-v4/protocol-candidate-path-final.txt",
                "evidence/phase-d/redesign-v4/protocol-final-aggregate-before-verify.txt",
                "evidence/phase-d/redesign-v4/protocol-final-aggregate-after-verify.txt",
                "evidence/phase-d/redesign-v4/protocol-handoff-aggregate-before.txt",
                "evidence/phase-d/redesign-v4/protocol-handoff-aggregate-after.txt",
            }
            <= observed
        )

        record = redesign.load_json(V4_SUPERSESSION)
        denied = {
            str(item["path"]): str(item["scope"])
            for item in record["denylist"]["artifacts"]
        }
        self.assertEqual(
            {path for path in denied if path.startswith("evidence/phase-d/redesign-v4/")},
            POSITIVE_V4_EVIDENCE,
        )
        self.assertTrue(all(denied[path] == "file" for path in POSITIVE_V4_EVIDENCE))
        self.assertTrue(
            {"blocked_protocol_checks_complete", "evidence_reproduced"}
            <= set(record["denylist"]["invalid_claims"])
        )
        for relative in POSITIVE_V4_EVIDENCE:
            with self.subTest(relative=relative), self.assertRaisesRegex(
                redesign.PhaseDRedesignError, "superseded|denied"
            ):
                redesign.assert_phase_d_artifact_current(relative, record)


class PhaseDFinalFreezeBindingFindingTest(unittest.TestCase):
    @staticmethod
    def _freeze_fixture(root: Path) -> tuple[Path, dict[str, object], dict[str, object]]:
        freeze_path = (
            root
            / "docs"
            / "assurance"
            / "phase-d"
            / "redesign"
            / "corrected-freeze-v4.json"
        )
        freeze_path.parent.mkdir(parents=True)
        baseline = {
            "commit": "1" * 40,
            "tree": "2" * 40,
            "scope": "entire_git_tree",
            "require_clean_worktree": True,
        }
        freeze: dict[str, object] = {
            "schema_version": "phase-d-redesign-freeze/v4",
            "id": "test-freeze",
            "baseline_review_target": baseline,
            "protocol_inputs": {
                "supersession_record": (
                    "docs/assurance/phase-d/redesign/supersession-record-v4.json"
                )
            },
        }
        freeze_bytes = (json.dumps(freeze, sort_keys=True) + "\n").encode("utf-8")
        freeze_path.write_bytes(freeze_bytes)
        record: dict[str, object] = {
            "schema_version": "phase-d-redesign-supersession/v4",
            "reviewed_head": baseline["commit"],
            "reviewed_tree": baseline["tree"],
            "v4_freeze_binding": {
                "path": freeze_path.relative_to(root).as_posix(),
                "sha256": redesign.sha256_bytes(freeze_bytes),
                "schema_version": freeze["schema_version"],
                "id": freeze["id"],
                "baseline_review_target": baseline,
                "supersession_protocol_input": freeze["protocol_inputs"][
                    "supersession_record"
                ],
            },
            "denylist": {"artifacts": [], "invalid_claims": []},
            "historical_evidence_preservation": "do_not_delete_or_mutate",
            "historical_files_must_not_be_deleted_or_rewritten": True,
            "v4_status": {
                "execution_authorized": False,
                "treatment_execution_status": "blocked",
                "treatment_pass_possible": False,
            },
        }
        return freeze_path, freeze, record

    def test_validator_binds_the_exact_supplied_freeze_path_and_bytes(self) -> None:
        freeze = redesign.load_json(V4_FREEZE)
        record = redesign.load_json(V4_SUPERSESSION)
        redesign.validate_v4_supersession_record(ROOT, V4_FREEZE, freeze, record)

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            substitute = Path(tmp) / "corrected-freeze-v4.json"
            substitute.write_text(json.dumps(freeze, indent=4) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                redesign.PhaseDRedesignError, "freeze.*binding|path|sha256"
            ):
                redesign.validate_v4_supersession_record(
                    ROOT, substitute, freeze, record
                )

    def test_exact_freeze_path_rejects_symlinks_and_hardlinks(self) -> None:
        for kind in ("symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "root"
                root.mkdir()
                freeze_path, freeze, record = self._freeze_fixture(root)
                external = Path(tmp) / "external-freeze.json"
                freeze_path.replace(external)
                if kind == "symlink":
                    freeze_path.symlink_to(external)
                else:
                    os.link(external, freeze_path)

                with (
                    patch.object(redesign, "_V4_EXPECTED_DENIED_ARTIFACTS", {}),
                    patch.object(redesign, "_V4_EXPECTED_INVALID_CLAIMS", set()),
                    self.assertRaisesRegex(
                        redesign.PhaseDRedesignError,
                        "freeze.*(?:symlink|hardlink|link|regular|indirection)",
                    ),
                ):
                    redesign.validate_v4_supersession_record(
                        root, freeze_path, freeze, record
                    )

    def test_exact_freeze_descriptor_rejects_same_byte_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            freeze_path, freeze, record = self._freeze_fixture(root)
            original_loads = redesign.json.loads
            replaced = False

            def replace_after_parse(value: object, *args: object, **kwargs: object) -> object:
                nonlocal replaced
                parsed = original_loads(value, *args, **kwargs)
                if not replaced:
                    replacement = root / "replacement-freeze.json"
                    replacement.write_bytes(freeze_path.read_bytes())
                    os.replace(replacement, freeze_path)
                    replaced = True
                return parsed

            with (
                patch.object(redesign, "_V4_EXPECTED_DENIED_ARTIFACTS", {}),
                patch.object(redesign, "_V4_EXPECTED_INVALID_CLAIMS", set()),
                patch.object(redesign.json, "loads", side_effect=replace_after_parse),
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "freeze.*(?:changed|replaced|identity|boundary)",
                ),
            ):
                redesign.validate_v4_supersession_record(
                    root, freeze_path, freeze, record
                )
            self.assertTrue(replaced)

    def test_public_v4_verifier_binds_freeze_before_schema_read_and_through_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            freeze_path, _freeze, _record = self._freeze_fixture(root)
            external = Path(tmp) / "external-freeze.json"
            freeze_path.replace(external)
            freeze_path.symlink_to(external)

            with (
                patch.object(
                    redesign,
                    "_verify_corrected_freeze_v4",
                    side_effect=AssertionError("unsafe freeze reached V4 dispatch"),
                ) as verify,
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "freeze.*(?:symlink|link|indirection|regular)",
                ),
            ):
                redesign.verify_corrected_freeze(root, freeze_path)
            verify.assert_not_called()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            freeze_path, _freeze, _record = self._freeze_fixture(root)

            def replace_before_return(*args: object, **kwargs: object) -> dict[str, object]:
                replacement = root / "replacement-freeze.json"
                replacement.write_bytes(freeze_path.read_bytes())
                os.replace(replacement, freeze_path)
                return {"execution_authorized": False}

            with (
                patch.object(
                    redesign,
                    "_verify_corrected_freeze_v4",
                    side_effect=replace_before_return,
                ),
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError,
                    "freeze.*(?:changed|replaced|identity|boundary)",
                ),
            ):
                redesign.verify_corrected_freeze(root, freeze_path)


class PhaseDFinalMutationBankFindingTest(unittest.TestCase):
    def test_v4_contract_requires_exactly_thirteen_faults_and_three_controls(self) -> None:
        for fault_count in (12, 14):
            with self.subTest(fault_count=fault_count), tempfile.TemporaryDirectory() as tmp:
                contract = redesign.load_json(V4_CONTRACT)
                bank = redesign.load_json(V4_BANK)
                if fault_count == 12:
                    moved = contract["required_fault_classes"].pop()
                    contract["required_control_classes"].append(moved)
                    case = next(item for item in bank["cases"] if item["fault_class"] == moved)
                    case["seeded_fault"] = False
                    case["valid_control"] = True
                else:
                    moved = contract["required_control_classes"].pop()
                    contract["required_fault_classes"].append(moved)
                    case = next(item for item in bank["cases"] if item["fault_class"] == moved)
                    case["seeded_fault"] = True
                    case["valid_control"] = False
                    case["severity"] = "high"
                bank_path = Path(tmp) / "mutation-bank-v4.json"
                bank_path.write_text(json.dumps(bank), encoding="utf-8")

                with self.assertRaisesRegex(
                    redesign.PhaseDRedesignError, "13\+3|thirteen|three|exact"
                ):
                    redesign.validate_mutation_bank(bank_path, contract)

    def test_v4_contract_cannot_omit_both_required_class_lists(self) -> None:
        contract = redesign.load_json(V4_CONTRACT)
        contract.pop("required_fault_classes")
        contract.pop("required_control_classes")

        with self.assertRaisesRegex(
            redesign.PhaseDRedesignError, "13\+3|thirteen|three|required.*class|exact"
        ):
            redesign.validate_mutation_bank(V4_BANK, contract)


class PhaseDFinalSvgEncodingFindingTest(unittest.TestCase):
    def test_every_interpolated_scenario_field_rejects_xml_control_injection_before_parse(self) -> None:
        bank = redesign.load_json(V4_SCENARIO_BANK)
        fields = (
            ("id",),
            ("product_category",),
            ("recognizable_features", 0),
            ("brief", "goal"),
            ("brief", "brand_tone"),
            ("brief", "required_constraints", 0),
            ("messages", "headline"),
            ("messages", "support"),
            ("channel",),
            ("aspect_ratio",),
            ("source_media_type",),
        )
        injections = (
            '<?xml version="1.0"?>',
            "<?evil data?>",
            "<!DOCTYPE svg>",
            '<!ENTITY x "evil">',
            "&x;",
            "</text><script>evil</script>",
            '" onload="evil',
            "' onload='evil",
        )
        for field in fields:
            for injection in injections:
                adversarial = copy.deepcopy(bank)
                owner: object = adversarial["scenarios"][0]
                for part in field[:-1]:
                    owner = owner[part]  # type: ignore[index]
                owner[field[-1]] = injection  # type: ignore[index]
                with tempfile.TemporaryDirectory() as tmp:
                    bank_path = Path(tmp) / "scenario-bank-v3.json"
                    bank_path.write_text(json.dumps(adversarial), encoding="utf-8")
                    with (
                        self.subTest(field=field, injection=injection),
                        patch.object(
                            redesign.ET,
                            "fromstring",
                            side_effect=AssertionError("ElementTree received injected XML"),
                        ) as parse,
                        self.assertRaisesRegex(
                            redesign.PhaseDRedesignError,
                            "interpolated|XML|declaration|DOCTYPE|entity|instruction",
                        ),
                    ):
                        redesign.validate_scenario_bank(ROOT, bank_path)
                    parse.assert_not_called()

    def test_utf16_xml_and_encoded_raw_controls_are_rejected_before_element_tree(self) -> None:
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
            'viewBox="0 0 512 512"><rect width="10" height="10"/></svg>'
        )
        adversarial = {
            "valid_utf16": svg.encode("utf-16"),
            "declaration_utf16le": ('<?xml version="1.0"?>' + svg).encode("utf-16le"),
            "pi_utf16be": ('<?xml-stylesheet href="evil.css"?>' + svg).encode("utf-16be"),
            "doctype_utf16": ("<!DOCTYPE svg>" + svg).encode("utf-16"),
            "entity_declaration_utf16le": (
                '<!DOCTYPE svg [<!ENTITY x "safe">]>' + svg
            ).encode("utf-16le"),
            "entity_reference_utf16be": svg.replace(
                "</svg>", "<title>&amp;</title></svg>"
            ).encode("utf-16be"),
        }

        for name, artifact in adversarial.items():
            with (
                self.subTest(name=name),
                patch.object(
                    redesign.ET,
                    "fromstring",
                    side_effect=AssertionError("ElementTree received non-UTF-8 or raw XML"),
                ) as parse,
                self.assertRaisesRegex(
                    redesign.PhaseDRedesignError, "UTF-8|encoding|raw XML"
                ),
            ):
                redesign.validate_bounded_svg(artifact)
            parse.assert_not_called()


class PhaseDFinalReportFindingTest(unittest.TestCase):
    def test_current_follow_up_is_pending_without_no_findings_assurance(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        self.assertRegex(
            report,
            r"(?is)current follow-up.*pending.*post-fix independent review",
        )
        contradictory_assurance = (
            r"(?i)no remaining (?:issues|findings)",
            r"(?is)(?:independent )?re-review.{0,120}(?:confirmed|found).{0,60}\bno\b",
            r"(?is)final disposition.{0,80}no remaining",
        )
        for pattern in contradictory_assurance:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(report, pattern)

    def test_report_records_fixed_commit_and_has_no_semantic_no_commit_push_claim(self) -> None:
        report = REPORT.read_text(encoding="utf-8")
        self.assertIn("153b11f2faf4939adc0e66dc51e6602534efd745", report)
        self.assertRegex(report, r"(?is)153b11f2faf4939adc0e66dc51e6602534efd745.*pushed")
        stale_patterns = (
            r"(?i)\b(?:no|without|never|neither)\b[^.\n]{0,120}"
            r"\b(?:commit(?:ted)?|push(?:ed)?|repository revision|remote update)\b",
            r"(?i)\b(?:commit|push)(?:ted|ed)?\b[^.;\n]{0,60}"
            r"\b(?:not|never)\b[^.;\n]{0,30}\b(?:created|made|performed|uploaded)\b",
            r"(?i)\b(?:did|was|were|has|have)\s+not\b[^.;\n]{0,60}"
            r"\b(?:commit|push)(?:ted|ed)?\b",
        )
        for pattern in stale_patterns:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(report, pattern)
        self.assertRegex(report, r"(?is)\bno\b[^.]{0,100}\bservice\b")
        self.assertRegex(report, r"(?is)\bno\b[^.]{0,100}\bcredential\b")
        self.assertRegex(report, r"(?is)\bno\b[^.]{0,100}\bapproval\b")
        self.assertRegex(report, r"(?is)\bno\b[^.]{0,100}\btreatment\b[^.]{0,40}\bexecution\b")


if __name__ == "__main__":
    unittest.main()
