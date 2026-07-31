"""Fail-closed tooling for the corrected Phase D D1/D2 design."""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import math
import os
import re
import stat
import subprocess
import unittest
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn


class PhaseDRedesignError(ValueError):
    """Raised when corrected Phase D inputs or evidence violate their freeze."""


_V4_EXPECTED_DENIED_ARTIFACTS = {
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

_V4_FREEZE_PATH = Path(
    "docs/assurance/phase-d/redesign/corrected-freeze-v4.json"
)

_V4_EXPECTED_INVALID_CLAIMS = {
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


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhaseDRedesignError(f"cannot load JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PhaseDRedesignError(f"JSON input must be an object: {path}")
    return value


@dataclass(frozen=True)
class _BoundRegularFile:
    root: Path
    relative: Path
    directory_fds: tuple[int, ...]
    directory_metadata: tuple[os.stat_result, ...]
    file_descriptor: int
    file_metadata: os.stat_result
    content: bytes


def _safe_open_flags(*, directory: bool) -> int:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if directory:
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _read_open_descriptor(file_descriptor: int) -> bytes:
    os.lseek(file_descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(file_descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _open_bound_regular_file(
    root: Path,
    path: Path,
    expected_relative: Path,
) -> _BoundRegularFile:
    absolute_root = Path(os.path.abspath(os.fspath(root)))
    absolute_path = Path(os.path.abspath(os.fspath(path)))
    expected_path = absolute_root / expected_relative
    if root != absolute_root or path != absolute_path or absolute_path != expected_path:
        raise PhaseDRedesignError(
            f"V4 supplied freeze path is not the exact frozen path: {path}"
        )
    try:
        if absolute_root.resolve(strict=True) != absolute_root:
            raise PhaseDRedesignError("V4 freeze root contains symlink indirection")
        root_metadata = absolute_root.lstat()
        root_fd = os.open(absolute_root, _safe_open_flags(directory=True))
    except OSError as exc:
        raise PhaseDRedesignError(f"cannot bind V4 freeze root safely: {exc}") from exc
    directory_fds = [root_fd]
    directory_metadata = [root_metadata]
    file_descriptor: int | None = None
    try:
        opened_root = os.fstat(root_fd)
        if not stat.S_ISDIR(root_metadata.st_mode) or not _same_filesystem_identity(
            root_metadata, opened_root
        ):
            raise PhaseDRedesignError("V4 freeze root identity changed before open")
        current_fd = root_fd
        for component in expected_relative.parts[:-1]:
            before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise PhaseDRedesignError("V4 freeze parent path uses unsafe indirection")
            child_fd = os.open(
                component,
                _safe_open_flags(directory=True),
                dir_fd=current_fd,
            )
            opened = os.fstat(child_fd)
            if not _same_filesystem_identity(before, opened):
                os.close(child_fd)
                raise PhaseDRedesignError("V4 freeze parent identity changed before open")
            directory_fds.append(child_fd)
            directory_metadata.append(before)
            current_fd = child_fd

        filename = expected_relative.name
        file_metadata = os.stat(filename, dir_fd=current_fd, follow_symlinks=False)
        if not stat.S_ISREG(file_metadata.st_mode) or file_metadata.st_nlink != 1:
            raise PhaseDRedesignError(
                "V4 supplied freeze must be a non-symlink, non-hardlinked regular file"
            )
        file_descriptor = os.open(
            filename,
            _safe_open_flags(directory=False),
            dir_fd=current_fd,
        )
        opened_file = os.fstat(file_descriptor)
        if not _same_filesystem_identity(file_metadata, opened_file):
            raise PhaseDRedesignError("V4 freeze identity changed before descriptor open")
        content = _read_open_descriptor(file_descriptor)
        final_opened = os.fstat(file_descriptor)
        if (
            len(content) != file_metadata.st_size
            or not _same_filesystem_identity(file_metadata, final_opened)
        ):
            raise PhaseDRedesignError("V4 freeze changed while reading exact bytes")
        return _BoundRegularFile(
            root=absolute_root,
            relative=expected_relative,
            directory_fds=tuple(directory_fds),
            directory_metadata=tuple(directory_metadata),
            file_descriptor=file_descriptor,
            file_metadata=file_metadata,
            content=content,
        )
    except (OSError, PhaseDRedesignError) as exc:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for directory_fd in reversed(directory_fds):
            os.close(directory_fd)
        if isinstance(exc, PhaseDRedesignError):
            raise
        raise PhaseDRedesignError(f"cannot bind exact V4 freeze bytes safely: {exc}") from exc


def _assert_bound_regular_file_unchanged(binding: _BoundRegularFile) -> None:
    try:
        root_now = binding.root.lstat()
        if not _same_filesystem_identity(binding.directory_metadata[0], root_now):
            raise PhaseDRedesignError("V4 freeze root identity changed at final boundary")
        for index, component in enumerate(binding.relative.parts[:-1], start=1):
            parent_fd = binding.directory_fds[index - 1]
            path_now = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            opened_now = os.fstat(binding.directory_fds[index])
            if not _same_filesystem_identity(
                binding.directory_metadata[index], path_now
            ) or not _same_filesystem_identity(
                binding.directory_metadata[index], opened_now
            ):
                raise PhaseDRedesignError(
                    "V4 freeze parent identity changed at final boundary"
                )
        current_file = os.stat(
            binding.relative.name,
            dir_fd=binding.directory_fds[-1],
            follow_symlinks=False,
        )
        opened_file = os.fstat(binding.file_descriptor)
        if not _same_filesystem_identity(
            binding.file_metadata, current_file
        ) or not _same_filesystem_identity(binding.file_metadata, opened_file):
            raise PhaseDRedesignError(
                "V4 freeze path was replaced or changed at final boundary"
            )
        if _read_open_descriptor(binding.file_descriptor) != binding.content or not (
            _same_filesystem_identity(
                binding.file_metadata, os.fstat(binding.file_descriptor)
            )
        ):
            raise PhaseDRedesignError("V4 freeze exact bytes changed at final boundary")
    except OSError as exc:
        raise PhaseDRedesignError(
            f"cannot recheck V4 freeze at final boundary: {exc}"
        ) from exc


def _close_bound_regular_file(binding: _BoundRegularFile) -> None:
    os.close(binding.file_descriptor)
    for directory_fd in reversed(binding.directory_fds):
        os.close(directory_fd)


def _supersession_artifacts(record: dict[str, Any]) -> dict[str, str]:
    denylist = record.get("denylist")
    artifacts = denylist.get("artifacts") if isinstance(denylist, dict) else None
    if not isinstance(artifacts, list):
        raise PhaseDRedesignError("V4 supersession denylist artifacts are malformed")
    denied: dict[str, str] = {}
    for item in artifacts:
        if not isinstance(item, dict):
            raise PhaseDRedesignError("V4 supersession denylist artifact is malformed")
        path = item.get("path")
        scope = item.get("scope")
        if (
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or ".." in Path(path).parts
            or path in denied
            or scope not in {"file", "tree"}
        ):
            raise PhaseDRedesignError("V4 supersession denylist artifact is malformed")
        denied[path] = str(scope)
    return denied


def assert_phase_d_artifact_current(path: str | Path, record: dict[str, Any]) -> None:
    """Reject a file or descendant of a tree explicitly denied by V4."""
    relative = Path(path).as_posix()
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise PhaseDRedesignError("Phase D artifact path is invalid")
    for denied, scope in _supersession_artifacts(record).items():
        if relative == denied or (
            scope == "tree" and relative.startswith(f"{denied.rstrip('/')}/")
        ):
            raise PhaseDRedesignError(
                f"Phase D artifact is superseded and denied by V4: {relative}"
            )


def validate_v4_supersession_record(
    root: Path,
    freeze_path: Path,
    freeze: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    """Validate exhaustive historical denial and its exact V4 freeze binding."""
    if record.get("schema_version") != "phase-d-redesign-supersession/v4":
        raise PhaseDRedesignError("V4 supersession schema_version is invalid")
    denied = _supersession_artifacts(record)
    if denied != _V4_EXPECTED_DENIED_ARTIFACTS:
        raise PhaseDRedesignError("V4 supersession denylist coverage is not exhaustive")
    invalid_claims = record.get("denylist", {}).get("invalid_claims")
    if (
        not isinstance(invalid_claims, list)
        or len(invalid_claims) != len(set(invalid_claims))
        or set(invalid_claims) != _V4_EXPECTED_INVALID_CLAIMS
    ):
        raise PhaseDRedesignError("V4 supersession invalid-claim coverage is not exhaustive")
    binding = _open_bound_regular_file(root, freeze_path, _V4_FREEZE_PATH)
    try:
        try:
            supplied_freeze = json.loads(binding.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PhaseDRedesignError(
                f"V4 supplied freeze bytes are invalid: {freeze_path}: {exc}"
            ) from exc
        if supplied_freeze != freeze:
            raise PhaseDRedesignError("V4 supplied freeze object does not match its exact bytes")
        expected_binding = {
            "path": _V4_FREEZE_PATH.as_posix(),
            "sha256": sha256_bytes(binding.content),
            "schema_version": freeze.get("schema_version"),
            "id": freeze.get("id"),
            "baseline_review_target": freeze.get("baseline_review_target"),
            "supersession_protocol_input": freeze.get("protocol_inputs", {}).get(
                "supersession_record"
            ),
        }
        if record.get("v4_freeze_binding") != expected_binding:
            raise PhaseDRedesignError("V4 supersession freeze binding is invalid")
        baseline = freeze.get("baseline_review_target")
        if (
            not isinstance(baseline, dict)
            or record.get("reviewed_head") != baseline.get("commit")
            or record.get("reviewed_tree") != baseline.get("tree")
        ):
            raise PhaseDRedesignError("V4 supersession reviewed target binding is invalid")
        status = record.get("v4_status")
        if (
            not isinstance(status, dict)
            or status.get("execution_authorized") is not False
            or status.get("treatment_execution_status") != "blocked"
            or status.get("treatment_pass_possible") is not False
            or record.get("historical_evidence_preservation") != "do_not_delete_or_mutate"
            or record.get("historical_files_must_not_be_deleted_or_rewritten") is not True
        ):
            raise PhaseDRedesignError("V4 supersession status or preservation policy is invalid")
        for relative, scope in denied.items():
            resolved = root / relative
            exists = resolved.is_file() if scope == "file" else resolved.is_dir()
            if resolved.is_symlink() or not exists:
                raise PhaseDRedesignError(
                    f"V4 supersession denied artifact is missing or unsafe: {relative}"
                )
        _assert_bound_regular_file_unchanged(binding)
        return {
            "denied_artifacts": denied,
            "invalid_claims": sorted(_V4_EXPECTED_INVALID_CLAIMS),
            "execution_authorized": False,
        }
    finally:
        _close_bound_regular_file(binding)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_independent_approval(
    root: Path,
    freeze_path: Path,
    approval_path: Path,
    documents: dict[str, dict[str, Any]],
    author_principals: set[str],
) -> dict[str, Any]:
    approval = load_json(approval_path)
    if approval.get("schema_version") != "phase-d-redesign-independent-approval/v2":
        raise PhaseDRedesignError("independent approval schema_version is invalid")
    if approval.get("decision") != "approve":
        raise PhaseDRedesignError("independent approval decision is not approve")
    reviewer = approval.get("reviewer_principal")
    if not isinstance(reviewer, str) or not reviewer or reviewer in author_principals:
        raise PhaseDRedesignError("independent approval reviewer is not independent")
    if approval.get("reviewed_freeze_sha256") != sha256_file(freeze_path):
        raise PhaseDRedesignError("independent approval does not bind the corrected freeze")
    expected_hashes = {
        str(value["path"]): str(value["sha256"])
        for value in documents.values()
    }
    if approval.get("reviewed_document_sha256") != expected_hashes:
        raise PhaseDRedesignError("independent approval does not bind every corrected document")
    unresolved = approval.get("unresolved_findings")
    if not isinstance(unresolved, list) or any(
        isinstance(item, dict) and str(item.get("severity", "")).lower() in {"critical", "high"}
        for item in unresolved
    ):
        raise PhaseDRedesignError("independent approval has unresolved Critical/High findings")
    return approval


def _verify_corrected_freeze_v2(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool = False,
) -> dict[str, Any]:
    """Reject the superseded V2 freeze and its unauthenticated approval model."""
    del (root, freeze_path, require_execution_approval)
    raise PhaseDRedesignError(
        "V2 corrected freeze is superseded by V4 and cannot authorize or verify execution"
    )


def load_trusted_governance_credentials(
    root: Path, freeze: dict[str, Any]
) -> dict[str, bytes]:
    """Reject in-process credential access until an isolated verifier exists."""
    del (root, freeze)
    raise PhaseDRedesignError(
        "production credential loading is disabled until a separate verifier/signing service exists"
    )


def evaluate_v3_authorization(
    freeze: dict[str, Any],
    freeze_sha256: str,
    document_sha256: dict[str, str],
    binding_sha256: dict[str, str],
    approval: dict[str, Any] | None,
    ceo_decision: dict[str, Any] | None,
    credentials: dict[str, bytes],
    *,
    require_execution_authorization: bool = False,
) -> dict[str, Any]:
    """Reject the superseded caller-credential trust model."""
    del (
        freeze, freeze_sha256, document_sha256, binding_sha256, approval,
        ceo_decision, credentials, require_execution_authorization,
    )
    raise PhaseDRedesignError(
        "V3 authorization is superseded because caller-provided credentials are not a trust root"
    )

_FORBIDDEN_GIT_ENVIRONMENT = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_USER",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_GRAFT_FILE",
    "GIT_INDEX_FILE",
    "GIT_NAMESPACE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
}


def _git_command_environment() -> dict[str, str]:
    forbidden = sorted(
        name
        for name in os.environ
        if name in _FORBIDDEN_GIT_ENVIRONMENT or name.startswith("GIT_CONFIG_")
    )
    if forbidden:
        raise PhaseDRedesignError(
            "semantic Git environment is forbidden during immutable verification: "
            + ", ".join(forbidden)
        )
    environment = os.environ.copy()
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def _run_git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "--no-replace-objects", *args],
        cwd=root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_command_environment(),
    )
    if completed.returncode:
        raise PhaseDRedesignError(
            f"cannot verify immutable Git review target ({' '.join(args)}): "
            f"{completed.stderr.strip()}"
        )
    return completed.stdout.rstrip("\r\n")


def _run_git_bytes(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "--no-replace-objects", *args],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_git_command_environment(),
    )
    if completed.returncode:
        raise PhaseDRedesignError(
            f"cannot verify immutable Git review target ({' '.join(args)}): "
            f"{os.fsdecode(completed.stderr).strip()}"
        )
    return completed.stdout


def _assert_no_replace_refs(root: Path) -> None:
    replace_refs = _run_git(
        root,
        "for-each-ref",
        "--format=%(refname)",
        "refs/replace/",
    ).splitlines()
    if replace_refs:
        raise PhaseDRedesignError(
            "replace refs are forbidden during immutable verification: "
            + ", ".join(replace_refs)
        )


def _git_metadata_paths(root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name in ("objects", "objects/info/alternates", "info/grafts", "shallow"):
        raw_path = _run_git(root, "rev-parse", "--path-format=absolute", "--git-path", name)
        paths[name] = Path(raw_path)
    return paths


@dataclass(frozen=True)
class _GitRepositoryBinding:
    root: Path
    root_descriptor: int
    root_metadata: os.stat_result
    git_directory: Path
    git_directory_metadata: os.stat_result
    common_directory: Path
    object_directory: Path
    object_directory_metadata: os.stat_result


def _bind_exact_git_worktree_root(root: Path) -> _GitRepositoryBinding:
    absolute_root = Path(os.path.abspath(os.fspath(root)))
    if root != absolute_root:
        raise PhaseDRedesignError("supplied root must be an exact absolute Git worktree top-level")
    try:
        if absolute_root.resolve(strict=True) != absolute_root:
            raise PhaseDRedesignError(
                "supplied root must not use symlink or canonical path indirection"
            )
        root_metadata = absolute_root.lstat()
        root_descriptor = os.open(absolute_root, _safe_open_flags(directory=True))
    except OSError as exc:
        raise PhaseDRedesignError(f"cannot bind immutable worktree root: {exc}") from exc
    try:
        opened_root = os.fstat(root_descriptor)
        if not stat.S_ISDIR(root_metadata.st_mode) or not _same_filesystem_identity(
            root_metadata, opened_root
        ):
            raise PhaseDRedesignError("immutable worktree root identity changed before open")
        top_level = Path(
            _run_git(root, "rev-parse", "--path-format=absolute", "--show-toplevel")
        )
        git_directory = Path(_run_git(root, "rev-parse", "--absolute-git-dir"))
        common_raw = _run_git(
            root, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
        common_directory = Path(common_raw)
        if not common_directory.is_absolute():
            common_directory = root / common_directory
        if top_level != absolute_root:
            raise PhaseDRedesignError(
                "supplied root is not the exact canonical Git worktree top-level"
            )
        expected_git_directory = absolute_root / ".git"
        if git_directory != expected_git_directory or common_directory != git_directory:
            raise PhaseDRedesignError(
                "linked worktree or ambiguous Git common-directory roots are forbidden"
            )
        git_directory_metadata = git_directory.lstat()
        if not stat.S_ISDIR(git_directory_metadata.st_mode):
            raise PhaseDRedesignError(
                "Git metadata must be a non-symlink directory inside the exact root"
            )
        object_directory, object_directory_metadata = _assert_safe_git_metadata(root)
        if object_directory != git_directory / "objects":
            raise PhaseDRedesignError("Git object directory is not bound inside the exact root")
        return _GitRepositoryBinding(
            root=absolute_root,
            root_descriptor=root_descriptor,
            root_metadata=root_metadata,
            git_directory=git_directory,
            git_directory_metadata=git_directory_metadata,
            common_directory=common_directory,
            object_directory=object_directory,
            object_directory_metadata=object_directory_metadata,
        )
    except Exception:
        os.close(root_descriptor)
        raise


def _assert_git_root_unchanged(binding: _GitRepositoryBinding) -> None:
    try:
        root_now = binding.root.lstat()
        root_opened = os.fstat(binding.root_descriptor)
        git_directory_now = binding.git_directory.lstat()
    except OSError as exc:
        raise PhaseDRedesignError(f"immutable Git root changed or disappeared: {exc}") from exc
    if not _same_inode_identity(
        binding.root_metadata, root_now
    ) or not _same_inode_identity(binding.root_metadata, root_opened):
        raise PhaseDRedesignError("immutable Git root identity changed during verification")
    if not _same_inode_identity(
        binding.git_directory_metadata, git_directory_now
    ):
        raise PhaseDRedesignError("immutable Git metadata root identity changed")


def _close_git_repository_binding(binding: _GitRepositoryBinding) -> None:
    os.close(binding.root_descriptor)


def _metadata_identity(path: Path) -> tuple[int, int, int, int, int, int] | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PhaseDRedesignError(f"cannot inspect immutable Git metadata {path}: {exc}") from exc
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _metadata_tree_snapshot(
    root: Path,
) -> dict[Path, tuple[int, int, int, int, int, int]]:
    snapshot: dict[Path, tuple[int, int, int, int, int, int]] = {}

    def walk(path: Path) -> None:
        identity = _metadata_identity(path)
        if identity is None:
            raise PhaseDRedesignError(f"immutable Git metadata disappeared: {path}")
        mode = identity[2]
        if mode not in {stat.S_IFDIR, stat.S_IFREG}:
            raise PhaseDRedesignError(
                f"immutable Git object metadata uses unsafe indirection or type: {path}"
            )
        snapshot[path] = identity
        if mode != stat.S_IFDIR:
            return
        try:
            names_before = sorted(entry.name for entry in os.scandir(path))
            for name in names_before:
                walk(path / name)
            names_after = sorted(entry.name for entry in os.scandir(path))
        except OSError as exc:
            raise PhaseDRedesignError(
                f"cannot enumerate immutable Git object metadata: {path}: {exc}"
            ) from exc
        if names_after != names_before or _metadata_identity(path) != identity:
            raise PhaseDRedesignError(
                f"immutable Git object metadata changed while snapshotting: {path}"
            )

    walk(root)
    return snapshot


def _git_read_metadata_snapshot(
    binding: _GitRepositoryBinding,
) -> dict[Path, tuple[int, int, int, int, int, int] | None]:
    _assert_git_root_unchanged(binding)
    paths = {
        binding.git_directory,
        binding.git_directory / "config",
        binding.git_directory / "config.worktree",
        binding.object_directory,
        binding.object_directory / "info",
        binding.object_directory / "info" / "alternates",
        binding.git_directory / "info",
        binding.git_directory / "info" / "grafts",
        binding.git_directory / "shallow",
        binding.common_directory / "refs",
        binding.common_directory / "refs" / "replace",
        binding.common_directory / "packed-refs",
    }
    forbidden = {
        path
        for path in (
            binding.object_directory / "info" / "alternates",
            binding.git_directory / "info" / "grafts",
            binding.git_directory / "shallow",
        )
        if _metadata_identity(path) is not None
    }
    if forbidden:
        raise PhaseDRedesignError(
            "alternate, graft, shallow, or object metadata is forbidden during immutable reads: "
            + ", ".join(str(path) for path in sorted(forbidden))
        )
    current_object = binding.object_directory.lstat()
    if not stat.S_ISDIR(current_object.st_mode) or not _same_filesystem_identity(
        binding.object_directory_metadata, current_object
    ):
        raise PhaseDRedesignError("Git object directory identity or indirection changed")
    snapshot = {path: _metadata_identity(path) for path in paths}
    snapshot.update(_metadata_tree_snapshot(binding.object_directory))
    return snapshot


def _assert_git_read_boundary_unchanged(
    binding: _GitRepositoryBinding,
    before: dict[Path, tuple[int, int, int, int, int, int] | None],
) -> None:
    after = _git_read_metadata_snapshot(binding)
    if after != before:
        raise PhaseDRedesignError(
            "Git replace refs, alternates, grafts, shallow, or object metadata changed around an immutable object read"
        )


def _assert_safe_git_metadata(root: Path) -> tuple[Path, os.stat_result]:
    paths = _git_metadata_paths(root)
    object_directory = paths["objects"]
    try:
        object_metadata = object_directory.lstat()
    except OSError as exc:
        raise PhaseDRedesignError(f"cannot bind Git object directory: {exc}") from exc
    if not stat.S_ISDIR(object_metadata.st_mode):
        raise PhaseDRedesignError("Git object directory must be a local non-symlink directory")
    forbidden = [
        name
        for name in ("objects/info/alternates", "info/grafts", "shallow")
        if paths[name].exists() or paths[name].is_symlink()
    ]
    if forbidden:
        raise PhaseDRedesignError(
            "alternate, graft, or shallow Git metadata is forbidden during immutable verification: "
            + ", ".join(forbidden)
        )
    return object_directory, object_metadata


def _assert_git_metadata_unchanged(
    root: Path, object_directory: Path, object_metadata: os.stat_result
) -> None:
    final_directory, final_metadata = _assert_safe_git_metadata(root)
    if final_directory != object_directory or not _same_filesystem_identity(
        object_metadata, final_metadata
    ):
        raise PhaseDRedesignError("Git object directory changed during immutable verification")


def _assert_exact_git_repository_binding(
    binding: _GitRepositoryBinding,
) -> None:
    _assert_git_root_unchanged(binding)
    rebound = _bind_exact_git_worktree_root(binding.root)
    try:
        if (
            rebound.root != binding.root
            or rebound.git_directory != binding.git_directory
            or rebound.common_directory != binding.common_directory
            or rebound.object_directory != binding.object_directory
            or not _same_inode_identity(binding.root_metadata, rebound.root_metadata)
            or not _same_inode_identity(
                binding.git_directory_metadata,
                rebound.git_directory_metadata,
            )
            or not _same_filesystem_identity(
                binding.object_directory_metadata,
                rebound.object_directory_metadata,
            )
        ):
            raise PhaseDRedesignError(
                "exact canonical Git root or repository binding changed"
            )
    finally:
        _close_git_repository_binding(rebound)
    _assert_git_root_unchanged(binding)


def _assert_git_repository_snapshot_unchanged(
    binding: _GitRepositoryBinding,
    expected: dict[Path, tuple[int, int, int, int, int, int] | None],
) -> None:
    if _git_read_metadata_snapshot(binding) != expected:
        raise PhaseDRedesignError(
            "Git repository metadata changed before the static inspection boundary"
        )


def _read_bound_head(
    binding: _GitRepositoryBinding,
    expected_repository_snapshot: dict[
        Path, tuple[int, int, int, int, int, int] | None
    ],
) -> str:
    _assert_git_repository_snapshot_unchanged(
        binding,
        expected_repository_snapshot,
    )
    head = _run_git(binding.root, "rev-parse", "HEAD")
    _assert_git_repository_snapshot_unchanged(
        binding,
        expected_repository_snapshot,
    )
    return head


def _git_object_id(object_format: str, object_kind: str, content: bytes) -> str:
    header = f"{object_kind} {len(content)}\0".encode("ascii")
    return hashlib.new(object_format, header + content).hexdigest()


def _read_bound_git_object(
    root: Path,
    object_id: str,
    object_kind: str,
    object_format: str,
    repository_binding: _GitRepositoryBinding | None = None,
) -> bytes:
    owned_binding = repository_binding is None
    binding = repository_binding or _bind_exact_git_worktree_root(root)
    try:
        _assert_no_replace_refs(root)
        before = _git_read_metadata_snapshot(binding)
        content = _run_git_bytes(root, "cat-file", object_kind, object_id)
        _assert_git_read_boundary_unchanged(binding, before)
        _assert_no_replace_refs(root)
    finally:
        if owned_binding:
            _close_git_repository_binding(binding)
    if _git_object_id(object_format, object_kind, content) != object_id:
        raise PhaseDRedesignError(
            f"immutable {object_kind} object bytes do not match object id {object_id}"
        )
    return content


def _commit_tree_from_bytes(commit: bytes, object_format: str) -> str:
    first_line, separator, _ = commit.partition(b"\n")
    expected_length = hashlib.new(object_format).digest_size * 2
    match = re.fullmatch(rb"tree ([0-9a-f]+)", first_line)
    if separator != b"\n" or match is None or len(match.group(1)) != expected_length:
        raise PhaseDRedesignError("immutable commit object has an invalid tree binding")
    return match.group(1).decode("ascii")


def _read_bound_tree_inventory(
    root: Path,
    tree: str,
    object_format: str,
    repository_binding: _GitRepositoryBinding | None = None,
) -> tuple[dict[str, tuple[str, str]], set[str], dict[str, bytes]]:
    tracked: dict[str, tuple[str, str]] = {}
    tracked_directories: set[str] = set()
    tree_bytes: dict[str, bytes] = {}
    object_id_bytes = hashlib.new(object_format).digest_size
    active_trees: set[str] = set()

    def read_tree(tree_id: str, relative_directory: str) -> bytes:
        if tree_id in active_trees:
            raise PhaseDRedesignError("immutable Git tree contains a recursive object cycle")
        active_trees.add(tree_id)
        try:
            content = _read_bound_git_object(
                root,
                tree_id,
                "tree",
                object_format,
                repository_binding,
            )
            tree_bytes[tree_id] = content
            cursor = 0
            names: set[bytes] = set()
            while cursor < len(content):
                mode_end = content.find(b" ", cursor)
                name_end = content.find(b"\0", mode_end + 1)
                if mode_end <= cursor or name_end <= mode_end + 1:
                    raise PhaseDRedesignError("immutable Git tree bytes are malformed")
                mode_bytes = content[cursor:mode_end]
                raw_name = content[mode_end + 1:name_end]
                object_end = name_end + 1 + object_id_bytes
                if object_end > len(content):
                    raise PhaseDRedesignError("immutable Git tree object id is truncated")
                raw_object_id = content[name_end + 1:object_end]
                cursor = object_end
                if raw_name in names or raw_name in {b".", b".."} or b"/" in raw_name:
                    raise PhaseDRedesignError("immutable Git tree contains an unsafe path name")
                names.add(raw_name)
                relative_name = os.fsdecode(raw_name)
                relative = (
                    f"{relative_directory}/{relative_name}"
                    if relative_directory
                    else relative_name
                )
                try:
                    mode = mode_bytes.decode("ascii")
                except UnicodeDecodeError as exc:
                    raise PhaseDRedesignError("immutable Git tree mode is malformed") from exc
                object_id = raw_object_id.hex()
                if mode == "40000":
                    tracked_directories.add(relative)
                    read_tree(object_id, relative)
                elif mode in {"100644", "100755", "120000"}:
                    tracked[relative] = (mode, object_id)
                else:
                    raise PhaseDRedesignError(
                        f"tracked content kind cannot be verified exactly: {relative}"
                    )
            return content
        finally:
            active_trees.remove(tree_id)

    read_tree(tree, "")
    return tracked, tracked_directories, tree_bytes


def _same_filesystem_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _same_inode_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        stat.S_IFMT(left.st_mode),
    ) == (
        right.st_dev,
        right.st_ino,
        stat.S_IFMT(right.st_mode),
    )


def _git_object_for_open_file(
    file_descriptor: int,
    object_format: str,
    size: int,
) -> tuple[str, os.stat_result]:
    digest = hashlib.new(object_format)
    digest.update(f"blob {size}\0".encode("ascii"))
    observed_size = 0
    while True:
        chunk = os.read(file_descriptor, 1024 * 1024)
        if not chunk:
            break
        observed_size += len(chunk)
        digest.update(chunk)
    final_metadata = os.fstat(file_descriptor)
    if observed_size != size:
        raise PhaseDRedesignError("tracked content changed while it was being read")
    return digest.hexdigest(), final_metadata


def _verify_complete_worktree(
    root: Path,
    tracked: dict[str, tuple[str, str]],
    tracked_directories: set[str],
    object_format: str,
) -> None:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY
    file_flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
        file_flags |= os.O_NOFOLLOW

    try:
        root_before = root.lstat()
        root_fd = os.open(root, directory_flags)
    except OSError as exc:
        raise PhaseDRedesignError(f"cannot open immutable worktree root safely: {exc}") from exc
    try:
        root_opened = os.fstat(root_fd)
        if not stat.S_ISDIR(root_opened.st_mode) or not _same_filesystem_identity(
            root_before, root_opened
        ):
            raise PhaseDRedesignError("immutable worktree root changed during verification")

        def walk(directory_fd: int, relative_directory: str) -> None:
            before = os.fstat(directory_fd)
            try:
                names_before = sorted(entry.name for entry in os.scandir(directory_fd))
            except OSError as exc:
                raise PhaseDRedesignError(
                    f"cannot enumerate immutable worktree directory: {relative_directory or '.'}: {exc}"
                ) from exc
            for name in names_before:
                if not relative_directory and name == ".git":
                    continue
                relative = f"{relative_directory}/{name}" if relative_directory else name
                try:
                    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError as exc:
                    raise PhaseDRedesignError(
                        f"filesystem path changed during immutable verification: {relative}: {exc}"
                    ) from exc

                if relative in tracked_directories:
                    if not stat.S_ISDIR(metadata.st_mode):
                        raise PhaseDRedesignError(
                            f"tracked tree directory mode drift: {relative}"
                        )
                    try:
                        child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                    except OSError as exc:
                        raise PhaseDRedesignError(
                            f"tracked directory changed before open: {relative}: {exc}"
                        ) from exc
                    try:
                        opened = os.fstat(child_fd)
                        if not _same_filesystem_identity(metadata, opened):
                            raise PhaseDRedesignError(
                                f"tracked directory identity changed before open: {relative}"
                            )
                        walk(child_fd, relative)
                    finally:
                        os.close(child_fd)
                    after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if not _same_filesystem_identity(metadata, after):
                        raise PhaseDRedesignError(
                            f"tracked directory changed during verification: {relative}"
                        )
                    continue

                expected = tracked.get(relative)
                if expected is None:
                    raise PhaseDRedesignError(
                        "ignored or unbound filesystem path is forbidden in a strict candidate "
                        f"clone (worktree drift): {relative}"
                    )
                mode, expected_object = expected
                if mode == "120000":
                    if not stat.S_ISLNK(metadata.st_mode):
                        raise PhaseDRedesignError(f"tracked content mode drift: {relative}")
                    try:
                        content = os.fsencode(os.readlink(name, dir_fd=directory_fd))
                        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    except OSError as exc:
                        raise PhaseDRedesignError(
                            f"tracked symlink changed while reading: {relative}: {exc}"
                        ) from exc
                    if not _same_filesystem_identity(metadata, after):
                        raise PhaseDRedesignError(
                            f"tracked symlink identity changed while reading: {relative}"
                        )
                    header = f"blob {len(content)}\0".encode("ascii")
                    observed_object = hashlib.new(object_format, header + content).hexdigest()
                else:
                    expected_executable = mode == "100755"
                    if mode not in {"100644", "100755"} or not stat.S_ISREG(
                        metadata.st_mode
                    ) or bool(metadata.st_mode & 0o111) != expected_executable:
                        raise PhaseDRedesignError(f"tracked content mode drift: {relative}")
                    try:
                        file_fd = os.open(name, file_flags, dir_fd=directory_fd)
                    except OSError as exc:
                        raise PhaseDRedesignError(
                            f"tracked file changed before open: {relative}: {exc}"
                        ) from exc
                    try:
                        opened = os.fstat(file_fd)
                        if not _same_filesystem_identity(metadata, opened):
                            raise PhaseDRedesignError(
                                f"tracked file identity changed before open: {relative}"
                            )
                        observed_object, final_opened = _git_object_for_open_file(
                            file_fd, object_format, metadata.st_size
                        )
                    finally:
                        os.close(file_fd)
                    after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if not _same_filesystem_identity(metadata, final_opened) or not (
                        _same_filesystem_identity(metadata, after)
                    ):
                        raise PhaseDRedesignError(
                            f"tracked file changed during verification: {relative}"
                        )
                if observed_object != expected_object:
                    raise PhaseDRedesignError(f"tracked content or worktree drift: {relative}")

            names_after = sorted(entry.name for entry in os.scandir(directory_fd))
            after = os.fstat(directory_fd)
            if names_after != names_before or not _same_filesystem_identity(before, after):
                raise PhaseDRedesignError(
                    f"filesystem directory changed during immutable verification: "
                    f"{relative_directory or '.'}"
                )

        walk(root_fd, "")
        root_after = root.lstat()
        if not _same_filesystem_identity(root_before, root_after):
            raise PhaseDRedesignError("immutable worktree root changed during verification")
    finally:
        os.close(root_fd)


def _assert_static_worktree_inspection_boundary(
    root: Path,
    repository_binding: _GitRepositoryBinding,
    repository_snapshot: dict[
        Path, tuple[int, int, int, int, int, int] | None
    ],
    commit: str,
    tree: str,
    object_format: str,
    commit_bytes: bytes,
    tree_objects: dict[str, bytes],
    tracked: dict[str, tuple[str, str]],
    tracked_directories: set[str],
) -> None:
    final_commit_bytes = _read_bound_git_object(
        root, commit, "commit", object_format, repository_binding
    )
    final_tracked, final_directories, final_tree_objects = _read_bound_tree_inventory(
        root, tree, object_format, repository_binding
    )
    if (
        final_commit_bytes != commit_bytes
        or final_tree_objects != tree_objects
        or final_tracked != tracked
        or final_directories != tracked_directories
    ):
        raise PhaseDRedesignError("immutable commit/tree bytes changed during verification")
    if _commit_tree_from_bytes(final_commit_bytes, object_format) != tree:
        raise PhaseDRedesignError("immutable commit/tree binding changed during verification")

    _assert_exact_git_repository_binding(repository_binding)
    _assert_no_replace_refs(root)
    _assert_git_repository_snapshot_unchanged(
        repository_binding,
        repository_snapshot,
    )
    if _read_bound_head(repository_binding, repository_snapshot) != commit:
        raise PhaseDRedesignError(
            "current HEAD changed before the static worktree inspection boundary"
        )
    _verify_complete_worktree(root, tracked, tracked_directories, object_format)


def verify_immutable_review_target(
    root: Path, target: dict[str, Any]
) -> NoReturn:
    """Inspect a mutable checkout, then block because the inspection is not atomic."""
    commit = target.get("commit")
    tree = target.get("tree")
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit)
        or not isinstance(tree, str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tree)
        or target.get("scope") != "entire_git_tree"
        or target.get("require_clean_worktree") is not True
    ):
        raise PhaseDRedesignError(
            "immutable review target commit/tree/scope and clean worktree requirement are invalid"
        )

    _git_command_environment()
    _assert_no_replace_refs(root)
    repository_binding = _bind_exact_git_worktree_root(root)
    try:
        repository_snapshot = _git_read_metadata_snapshot(repository_binding)
        object_format = _run_git(root, "rev-parse", "--show-object-format")
        if object_format not in {"sha1", "sha256"}:
            raise PhaseDRedesignError("unsupported Git object format for exact content verification")
        expected_hex_length = hashlib.new(object_format).digest_size * 2
        if len(commit) != expected_hex_length or len(tree) != expected_hex_length:
            raise PhaseDRedesignError("immutable review target object ids have the wrong format")

        commit_bytes = _read_bound_git_object(
            root, commit, "commit", object_format, repository_binding
        )
        if _commit_tree_from_bytes(commit_bytes, object_format) != tree:
            raise PhaseDRedesignError("immutable review target commit/tree binding is invalid")
        tracked, tracked_directories, tree_objects = _read_bound_tree_inventory(
            root, tree, object_format, repository_binding
        )
        current_head = _run_git(root, "rev-parse", "HEAD")
        if current_head != commit:
            raise PhaseDRedesignError("current HEAD/tree drifted from the immutable review target")
        flagged = [
            os.fsdecode(item[2:])
            for item in _run_git_bytes(root, "ls-files", "-v", "-z").split(b"\0")
            if item and item[:1] != b"H"
        ]
        if flagged:
            raise PhaseDRedesignError(
                "tracked index flag (skip-worktree or assume-unchanged) is forbidden: "
                + ", ".join(flagged)
            )

        _verify_complete_worktree(root, tracked, tracked_directories, object_format)

        _assert_git_metadata_unchanged(
            root,
            repository_binding.object_directory,
            repository_binding.object_directory_metadata,
        )
        _assert_no_replace_refs(root)
        _assert_git_root_unchanged(repository_binding)
        _assert_static_worktree_inspection_boundary(
            root,
            repository_binding,
            repository_snapshot,
            commit,
            tree,
            object_format,
            commit_bytes,
            tree_objects,
            tracked,
            tracked_directories,
        )
        raise PhaseDRedesignError(
            "blocked_unavailable_atomic_snapshot: mutable Git checkout static inspection "
            "cannot produce acceptance evidence without an atomic read-only filesystem "
            "snapshot or OS-enforced immutability primitive"
        )
    finally:
        _close_git_repository_binding(repository_binding)


def _inspect_development_git_object_target(
    root: Path, target: dict[str, Any]
) -> dict[str, Any]:
    commit = target.get("commit")
    tree = target.get("tree")
    if (
        not isinstance(commit, str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit)
        or not isinstance(tree, str)
        or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", tree)
        or target.get("scope") != "entire_git_tree"
        or target.get("require_clean_worktree") is not True
    ):
        raise PhaseDRedesignError("Git object review target is invalid")

    _git_command_environment()
    _assert_no_replace_refs(root)
    repository_binding = _bind_exact_git_worktree_root(root)
    try:
        repository_snapshot = _git_read_metadata_snapshot(repository_binding)
        initial_head = _run_git(root, "rev-parse", "HEAD")
        object_format = _run_git(root, "rev-parse", "--show-object-format")
        if object_format not in {"sha1", "sha256"}:
            raise PhaseDRedesignError("unsupported Git object format for exact content verification")
        expected_hex_length = hashlib.new(object_format).digest_size * 2
        if len(commit) != expected_hex_length or len(tree) != expected_hex_length:
            raise PhaseDRedesignError("Git object review target object ids have the wrong format")
        commit_bytes = _read_bound_git_object(
            root, commit, "commit", object_format, repository_binding
        )
        if _commit_tree_from_bytes(commit_bytes, object_format) != tree:
            raise PhaseDRedesignError("Git object review target commit/tree binding is invalid")
        tree_bytes = _read_bound_git_object(
            root, tree, "tree", object_format, repository_binding
        )
        if _read_bound_git_object(
            root, commit, "commit", object_format, repository_binding
        ) != commit_bytes or (
            _read_bound_git_object(
                root, tree, "tree", object_format, repository_binding
            )
            != tree_bytes
        ):
            raise PhaseDRedesignError("Git object review target changed during verification")
        _assert_git_metadata_unchanged(
            root,
            repository_binding.object_directory,
            repository_binding.object_directory_metadata,
        )
        _assert_no_replace_refs(root)
        _assert_exact_git_repository_binding(repository_binding)
        _assert_git_repository_snapshot_unchanged(
            repository_binding,
            repository_snapshot,
        )
        if _read_bound_head(repository_binding, repository_snapshot) != initial_head:
            raise PhaseDRedesignError(
                "development Git object target HEAD changed before its return boundary"
            )
        return {
            "status": "development_only_unverified_git_object_diagnostic",
            "development_only": True,
            "verified": False,
            "candidate_evidence": False,
            "authorization_eligible": False,
            "diagnostic_scope": "git_objects_only_not_worktree",
            "observed_commit_object": commit,
            "observed_tree_object": tree,
        }
    finally:
        _close_git_repository_binding(repository_binding)


def load_external_review_target(root: Path, freeze: dict[str, Any]) -> dict[str, Any]:
    del (root, freeze)
    raise PhaseDRedesignError(
        "external signed candidate manifest verification is not implemented; "
        "a separate signature verifier is required"
    )


def evaluate_v4_authorization(
    root: Path,
    freeze: dict[str, Any],
    approval: dict[str, Any] | None,
    ceo_decision: dict[str, Any] | None,
    *,
    require_execution_authorization: bool = False,
) -> dict[str, Any]:
    """Keep execution structurally unavailable until isolated trust services exist."""
    del (root, freeze, approval, ceo_decision)
    blockers = [
        "external_signed_candidate_manifest_verifier_not_implemented",
        "real_company_os_c2_replay_verifier_not_implemented",
        "separate_governance_signature_verifier_not_implemented",
        "execution_authorization_path_intentionally_absent",
    ]
    if require_execution_authorization:
        raise PhaseDRedesignError(
            "Phase D execution authorization is unavailable until a separate verifier "
            "and a concrete internal real Company OS C2 replay verifier exist"
        )
    return {
        "status": "blocked_no_production_verifier_or_authorization_path",
        "execution_authorized": False,
        "blockers": blockers,
    }


def _verify_corrected_freeze_v3(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool,
    governance_credentials: dict[str, bytes] | None,
) -> dict[str, Any]:
    """Reject the self-referential V3 freeze semantics."""
    del (root, freeze_path, require_execution_approval, governance_credentials)
    raise PhaseDRedesignError(
        "V3 corrected freeze is superseded by V4 and cannot authorize or verify execution"
    )

def _verify_corrected_freeze_v4(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool,
    allow_development_overlay: bool,
    freeze: dict[str, Any] | None = None,
) -> dict[str, Any]:
    freeze = load_json(freeze_path) if freeze is None else freeze
    if freeze.get("schema_version") != "phase-d-redesign-freeze/v4":
        raise PhaseDRedesignError("v4 corrected freeze schema_version is invalid")
    if freeze.get("execution_gate", {}).get("execution_authorized") is not False:
        raise PhaseDRedesignError("v4 corrected freeze must remain non-authorizing")
    baseline_target = freeze.get("baseline_review_target")
    if not isinstance(baseline_target, dict):
        raise PhaseDRedesignError("v4 baseline review target is invalid")
    if freeze.get("candidate_review_target") is not None:
        raise PhaseDRedesignError(
            "v4 candidate review target must be supplied by the external signed manifest"
        )
    try:
        candidate_target = load_external_review_target(root, freeze)
    except PhaseDRedesignError:
        candidate_target = None
    if allow_development_overlay:
        development_git_object_diagnostic = _inspect_development_git_object_target(
            root, baseline_target
        )
    else:
        if not isinstance(candidate_target, dict):
            raise PhaseDRedesignError(
                "repository default verification is blocked until an externally signed "
                "candidate manifest can be verified by a separate signature verifier"
            )
        verify_immutable_review_target(root, candidate_target)

    protocol_inputs = freeze.get("protocol_inputs")
    expected_inputs = {
        "d1_contract": "docs/assurance/phase-d/redesign/d1/contract-v4.json",
        "d1_scenario_bank": "docs/assurance/phase-d/redesign/d1/scenario-bank-v3.json",
        "d2_contract": "docs/assurance/phase-d/redesign/d2/contract-v4.json",
        "d2_mutation_bank": "docs/assurance/phase-d/redesign/d2/mutation-bank-v4.json",
        "supersession_record": "docs/assurance/phase-d/redesign/supersession-record-v4.json",
    }
    if protocol_inputs != expected_inputs:
        raise PhaseDRedesignError("v4 freeze does not name the exact protocol inputs")
    documents: dict[str, dict[str, Any]] = {}
    for kind, relative in expected_inputs.items():
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise PhaseDRedesignError(f"v4 protocol input is not a regular file: {kind}")
        documents[kind] = load_json(path)
    supersession = validate_v4_supersession_record(
        root, freeze_path, freeze, documents["supersession_record"]
    )
    for kind, relative in expected_inputs.items():
        if kind != "supersession_record":
            assert_phase_d_artifact_current(relative, documents["supersession_record"])
    if (
        documents["d1_contract"].get("schema_version") != "phase-d-d1-corrected-contract/v4"
        or documents["d2_contract"].get("schema_version") != "phase-d-d2-corrected-contract/v4"
        or documents["d2_contract"].get("treatment_pass_possible") is not False
        or documents["d2_contract"].get("real_production_replay", {}).get("status") != "not_implemented"
    ):
        raise PhaseDRedesignError("v4 contracts do not preserve the blocked state")
    validate_scenario_bank(root, root / expected_inputs["d1_scenario_bank"])
    validate_mutation_bank(
        root / expected_inputs["d2_mutation_bank"], documents["d2_contract"]
    )
    authorization = evaluate_v4_authorization(
        root,
        freeze,
        None,
        None,
        require_execution_authorization=require_execution_approval,
    )
    return {
        **authorization,
        "documents": documents,
        "supersession": supersession,
        "development_git_object_diagnostic": development_git_object_diagnostic,
        "development_overlay": allow_development_overlay,
        "bound_paths": sorted(
            [freeze_path.relative_to(root).as_posix(), *expected_inputs.values()]
        ),
    }


def verify_corrected_freeze(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool = False,
    governance_credentials: dict[str, bytes] | None = None,
    allow_development_overlay: bool = False,
) -> dict[str, Any]:
    expected_v4_path = Path(os.path.abspath(os.fspath(root))) / _V4_FREEZE_PATH
    if Path(os.path.abspath(os.fspath(freeze_path))) == expected_v4_path:
        binding = _open_bound_regular_file(root, freeze_path, _V4_FREEZE_PATH)
        try:
            try:
                freeze = json.loads(binding.content.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise PhaseDRedesignError(
                    f"V4 supplied freeze bytes are invalid: {freeze_path}: {exc}"
                ) from exc
            if not isinstance(freeze, dict):
                raise PhaseDRedesignError("V4 supplied freeze JSON must be an object")
            schema_version = freeze.get("schema_version")
            if schema_version != "phase-d-redesign-freeze/v4":
                raise PhaseDRedesignError("v4 corrected freeze schema_version is invalid")
            if governance_credentials is not None:
                raise PhaseDRedesignError("v4 rejects caller-provided governance credentials")
            result = _verify_corrected_freeze_v4(
                root,
                freeze_path,
                require_execution_approval=require_execution_approval,
                allow_development_overlay=allow_development_overlay,
                freeze=freeze,
            )
            _assert_bound_regular_file_unchanged(binding)
            return result
        finally:
            _close_bound_regular_file(binding)

    schema_version = load_json(freeze_path).get("schema_version")
    if schema_version == "phase-d-redesign-freeze/v2":
        return _verify_corrected_freeze_v2(
            root, freeze_path, require_execution_approval=require_execution_approval
        )
    if schema_version == "phase-d-redesign-freeze/v3":
        return _verify_corrected_freeze_v3(
            root,
            freeze_path,
            require_execution_approval=require_execution_approval,
            governance_credentials=governance_credentials,
        )
    if schema_version == "phase-d-redesign-freeze/v4":
        raise PhaseDRedesignError("V4 supplied freeze path is not the exact frozen path")
    raise PhaseDRedesignError("corrected freeze schema_version is invalid")


def validate_scenario_bank(root: Path, bank_path: Path) -> dict[str, Any]:
    bank = load_json(bank_path)
    if bank.get("schema_version") not in {
        "phase-d-d1-scenario-bank/v2",
        "phase-d-d1-scenario-bank/v3",
    }:
        raise PhaseDRedesignError("D1 scenario bank schema_version is invalid")
    scenarios = bank.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) < 6:
        raise PhaseDRedesignError("D1 requires at least six recognizable source scenarios")
    seen_ids: set[str] = set()
    seen_categories: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise PhaseDRedesignError("D1 scenarios must be objects")
        scenario_id = scenario.get("id")
        category = scenario.get("product_category")
        features = scenario.get("recognizable_features")
        if not isinstance(scenario_id, str) or scenario_id in seen_ids:
            raise PhaseDRedesignError("D1 scenario ids must be unique strings")
        if not isinstance(category, str) or category in seen_categories:
            raise PhaseDRedesignError("D1 product categories must be unique strings")
        if not isinstance(features, list) or len(features) < 3 or not all(isinstance(item, str) for item in features):
            raise PhaseDRedesignError(f"D1 scenario is not recognizably specified: {scenario_id}")
        source_path = scenario.get("source_path")
        expected_hash = scenario.get("source_sha256")
        if not isinstance(source_path, str) or not isinstance(expected_hash, str):
            raise PhaseDRedesignError(f"D1 source binding is invalid: {scenario_id}")
        source = root / source_path
        if sha256_file(source) != expected_hash:
            raise PhaseDRedesignError(f"D1 source hash mismatch: {scenario_id}")
        if source.stat().st_size > int(bank.get("maximum_source_bytes", 0)):
            raise PhaseDRedesignError(f"D1 source exceeds the frozen byte ceiling: {scenario_id}")
        for required in ("brief", "messages", "channel", "aspect_ratio"):
            if required not in scenario:
                raise PhaseDRedesignError(f"D1 scenario omits {required}: {scenario_id}")
        _validate_svg_interpolated_fields(scenario)
        seen_ids.add(scenario_id)
        seen_categories.add(category)
    return bank


def balanced_blind_assignments(
    scenario_ids: list[str], randomization_seed: str
) -> dict[str, dict[str, str]]:
    """Create a deterministic, opaque, exactly balanced assignment from a frozen seed."""
    if (
        not scenario_ids
        or len(scenario_ids) % 2
        or len(set(scenario_ids)) != len(scenario_ids)
        or not all(isinstance(item, str) and item for item in scenario_ids)
    ):
        raise PhaseDRedesignError("D1 balanced assignment requires unique, nonempty, even scenario ids")
    if not isinstance(randomization_seed, str) or not randomization_seed:
        raise PhaseDRedesignError("D1 randomization seed is invalid")
    ranked = sorted(
        scenario_ids,
        key=lambda scenario_id: hashlib.sha256(
            f"{randomization_seed}\0{scenario_id}".encode("utf-8")
        ).digest(),
    )
    candidate_in_a = set(ranked[: len(ranked) // 2])
    return {
        scenario_id: (
            {"A": "candidate", "B": "comparator"}
            if scenario_id in candidate_in_a
            else {"A": "comparator", "B": "candidate"}
        )
        for scenario_id in scenario_ids
    }


def build_d1_run_specs(
    scenario: dict[str, Any],
    contract: dict[str, Any],
    source_bytes: bytes,
) -> dict[str, dict[str, Any]]:
    """Build paired run specs whose sole difference is assurance workflow."""
    parity = contract.get("paired_input_parity")
    workflows = contract.get("workflows")
    if not isinstance(parity, dict) or not isinstance(workflows, dict):
        raise PhaseDRedesignError("D1 paired-input contract is invalid")
    if sha256_bytes(source_bytes) != scenario.get("source_sha256"):
        raise PhaseDRedesignError("D1 source bytes do not match the frozen scenario")
    common = {
        "scenario_id": scenario["id"],
        "source_bytes": bytes(source_bytes),
        "source_sha256": sha256_bytes(source_bytes),
        "brief": copy.deepcopy(scenario["brief"]),
        "messages": copy.deepcopy(scenario["messages"]),
        "attempt_budget": int(parity["attempts_per_option"]),
        "model": str(parity["model"]),
        "tool": str(parity["tool"]),
        "timeout_seconds": int(parity["timeout_seconds"]),
        "evidence_budget_bytes": int(parity["evidence_budget_bytes_per_option"]),
    }
    return {
        "candidate": {**copy.deepcopy(common), "assurance_workflow": copy.deepcopy(workflows["candidate"])},
        "comparator": {**copy.deepcopy(common), "assurance_workflow": copy.deepcopy(workflows["comparator"])},
    }


def render_bounded_artifact(scenario: dict[str, Any], source_bytes: bytes) -> bytes:
    """Reject the superseded D1 artifact-generation helper while V4 is blocked."""
    del (scenario, source_bytes)
    raise PhaseDRedesignError(
        "D1 artifact generation is blocked and the legacy renderer is superseded"
    )


def _render_structured_artifact(scenario: dict[str, Any], source_bytes: bytes) -> bytes:
    """Reject the superseded V3 structured artifact renderer while V4 is blocked."""
    del (scenario, source_bytes)
    raise PhaseDRedesignError(
        "D1 artifact generation is blocked and the V3 structured renderer is superseded"
    )


def execute_d1_workflow(
    scenario: dict[str, Any], run_spec: dict[str, Any]
) -> dict[str, Any]:
    """Reject the superseded D1 treatment workflow while Phase D remains blocked."""
    del (scenario, run_spec)
    raise PhaseDRedesignError(
        "D1 treatment workflow execution is blocked and the V3 helper is superseded"
    )


def _validate_svg_interpolated_fields(value: object, field: str = "scenario") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_svg_interpolated_fields(item, f"{field}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_svg_interpolated_fields(item, f"{field}[{index}]")
        return
    if not isinstance(value, str):
        return
    controls = (
        (r"(?i)<\?xml(?:\s|\?)", "XML declaration"),
        (r"<\?", "XML processing instruction"),
        (r"(?i)<!DOCTYPE", "XML DOCTYPE"),
        (r"(?i)<!ENTITY", "XML entity declaration"),
        (r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z_:][\w:.-]*);", "XML entity reference"),
        (r"[<>&\"']", "raw XML markup"),
    )
    for pattern, label in controls:
        if re.search(pattern, value):
            raise PhaseDRedesignError(
                f"D1 interpolated field {field} contains forbidden {label} syntax"
            )

def validate_bounded_svg(artifact: bytes) -> dict[str, Any]:
    svg_namespace = "http://www.w3.org/2000/svg"
    try:
        xml_text = artifact.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PhaseDRedesignError("D1 artifact XML must be strict UTF-8") from exc
    if "\x00" in xml_text:
        raise PhaseDRedesignError("D1 artifact XML must be strict UTF-8 without encoded NULs")
    raw_xml_controls = (
        (r"(?i)<!DOCTYPE", "DOCTYPE"),
        (r"(?i)<\?", "processing instruction"),
        (r"(?i)<!ENTITY", "entity declaration"),
        (r"&(?:#\d+|#x[0-9a-fA-F]+|[A-Za-z_:][\w:.-]*);", "entity reference"),
    )
    for pattern, label in raw_xml_controls:
        if re.search(pattern, xml_text):
            raise PhaseDRedesignError(
                f"D1 artifact contains forbidden raw XML {label} syntax"
            )
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise PhaseDRedesignError(f"D1 artifact is not valid SVG: {exc}") from exc
    if root.tag != f"{{{svg_namespace}}}svg":
        raise PhaseDRedesignError("D1 artifact root is not SVG")
    try:
        width = float(root.attrib["width"])
        height = float(root.attrib["height"])
        view_box = [float(item) for item in root.attrib["viewBox"].split()]
    except (KeyError, ValueError) as exc:
        raise PhaseDRedesignError("D1 artifact bounds are invalid") from exc
    overflow_reasons: list[str] = []
    if width != 512 or height != 512 or view_box != [0.0, 0.0, 512.0, 512.0]:
        overflow_reasons.append("canvas_or_viewbox_mismatch")
    def parse_numbers(value: str) -> list[float]:
        number_pattern = re.compile(
            r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
        )
        matches = list(number_pattern.finditer(value))
        numbers = [float(match.group(0)) for match in matches]
        if not numbers or not all(math.isfinite(item) for item in numbers):
            raise ValueError("geometry contains no finite coordinates")
        cursor = 0
        for index, match in enumerate(matches):
            separator = value[cursor:match.start()]
            if index == 0:
                valid_separator = not separator.strip()
            else:
                valid_separator = bool(
                    re.fullmatch(r"(?:\s+|\s*,\s*|\s*,?\s+)", separator)
                )
            if not valid_separator:
                raise ValueError("geometry contains malformed separators")
            cursor = match.end()
        if value[cursor:].strip():
            raise ValueError("geometry contains malformed trailing tokens")
        return numbers

    def outside_canvas(x_min: float, y_min: float, x_max: float, y_max: float) -> bool:
        values = (x_min, y_min, x_max, y_max)
        return not all(math.isfinite(item) for item in values) or x_min < 0 or y_min < 0 or x_max > 512 or y_max > 512

    supported_elements = {
        "svg", "title", "rect", "circle", "ellipse", "line", "polyline",
        "polygon", "path",
    }
    supported_attributes = {
        "svg": {"width", "height", "viewBox", "data-safe-area"},
        "title": set(),
        "rect": {"x", "y", "width", "height", "fill", "stroke"},
        "circle": {"cx", "cy", "r", "fill", "stroke"},
        "ellipse": {"cx", "cy", "rx", "ry", "fill", "stroke"},
        "line": {"x1", "y1", "x2", "y2", "stroke"},
        "polyline": {"points", "fill", "stroke"},
        "polygon": {"points", "fill", "stroke"},
        "path": {"d", "fill", "stroke"},
    }

    def path_bounds(value: str) -> tuple[float, float, float, float]:
        number = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
        token_pattern = re.compile(rf"[MLHVZmlhvz]|{number}")
        matches = list(token_pattern.finditer(value))
        if not matches:
            raise ValueError("path contains no tokens")
        tokens = [match.group(0) for match in matches]
        if tokens[0] != "M":
            raise ValueError("path must begin with an absolute moveto")
        if any(token in {"m", "l", "h", "v", "z"} for token in tokens):
            raise ValueError("relative paths are outside the supported subset")

        # This supported subset requires explicit whitespace or one comma between
        # coordinates, preventing the token extractor from hiding bad punctuation.
        cursor = 0
        previous: str | None = None
        commands = {"M", "L", "H", "V", "Z", "m", "l", "h", "v", "z"}
        for match in matches:
            token = match.group(0)
            separator = value[cursor:match.start()]
            current_is_command = token in commands
            previous_is_command = previous in commands if previous is not None else False
            if previous is None:
                valid_separator = not separator.strip()
            elif previous_is_command or current_is_command:
                valid_separator = not separator.strip()
            else:
                valid_separator = bool(
                    re.fullmatch(r"(?:\s+|\s*,\s*|\s*,?\s+)", separator)
                )
            if not valid_separator:
                raise ValueError("path contains malformed separators or tokens")
            previous = token
            cursor = match.end()
        if value[cursor:].strip():
            raise ValueError("path contains malformed trailing tokens")

        coordinates: list[tuple[float, float]] = []
        cursor_x = cursor_y = 0.0
        command: str | None = None
        command_pending = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"M", "L", "H", "V", "Z"}:
                if command_pending:
                    raise ValueError("path command has incomplete coordinates")
                command = token
                command_pending = command != "Z"
                index += 1
                if command == "Z":
                    command = None
                continue
            if command is None:
                raise ValueError("path coordinate has no command")
            arity = 2 if command in {"M", "L"} else 1
            if index + arity > len(tokens) or any(
                item in {"M", "L", "H", "V", "Z"}
                for item in tokens[index:index + arity]
            ):
                raise ValueError("path command has incomplete coordinates")
            numbers = [float(item) for item in tokens[index:index + arity]]
            if not all(math.isfinite(item) for item in numbers):
                raise ValueError("path contains non-finite coordinates")
            if command in {"M", "L"}:
                cursor_x, cursor_y = numbers
                if command == "M":
                    command = "L"
            elif command == "H":
                cursor_x = numbers[0]
            else:
                cursor_y = numbers[0]
            coordinates.append((cursor_x, cursor_y))
            command_pending = False
            index += arity
        if not coordinates or command_pending:
            raise ValueError("path has no provable coordinates")
        xs = [item[0] for item in coordinates]
        ys = [item[1] for item in coordinates]
        return min(xs), min(ys), max(xs), max(ys)

    for element in root.iter():
        namespace_prefix = f"{{{svg_namespace}}}"
        if not isinstance(element.tag, str) or not element.tag.startswith(namespace_prefix):
            overflow_reasons.append("unsupported_foreign_namespace_element")
            continue
        kind = element.tag[len(namespace_prefix):]
        if kind == "svg" and element is not root:
            overflow_reasons.append("unsupported_nested_svg_viewport")
            continue
        if kind not in supported_elements:
            overflow_reasons.append(f"unsupported_{kind}_geometry")
            continue
        if any("}" in key or key not in supported_attributes[kind] for key in element.attrib):
            overflow_reasons.append(f"unsupported_{kind}_attribute")
            continue
        if any(
            key == "transform"
            or key == "style"
            or key == "class"
            or "transform" in str(value).lower()
            for key, value in element.attrib.items()
        ):
            overflow_reasons.append(f"unsupported_{kind}_style_or_transform")
            continue
        unsupported_paint = False
        for paint_attribute in ("fill", "stroke"):
            paint = element.attrib.get(paint_attribute)
            if paint is not None and not re.fullmatch(
                r"(?:none|transparent|#[0-9a-fA-F]{3,4}|#[0-9a-fA-F]{6}|#[0-9a-fA-F]{8})",
                paint.strip(),
            ):
                overflow_reasons.append(f"unsupported_{kind}_{paint_attribute}_paint")
                unsupported_paint = True
        if unsupported_paint:
            continue
        stroke = element.attrib.get("stroke")
        if stroke is not None and stroke.strip().lower() not in {"", "none", "transparent"}:
            overflow_reasons.append(f"unsupported_{kind}_stroke_bounds")
            continue
        if kind == "rect":
            try:
                x = float(element.attrib.get("x", "0"))
                y = float(element.attrib.get("y", "0"))
                item_width = float(element.attrib["width"])
                item_height = float(element.attrib["height"])
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_rect_bounds")
                continue
            if item_width < 0 or item_height < 0 or outside_canvas(x, y, x + item_width, y + item_height):
                overflow_reasons.append("rect_overflow")
        elif kind == "circle":
            try:
                cx = float(element.attrib["cx"])
                cy = float(element.attrib["cy"])
                radius = float(element.attrib["r"])
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_circle_bounds")
                continue
            if radius < 0 or outside_canvas(cx - radius, cy - radius, cx + radius, cy + radius):
                overflow_reasons.append("circle_overflow")
        elif kind == "ellipse":
            try:
                cx = float(element.attrib["cx"])
                cy = float(element.attrib["cy"])
                rx = float(element.attrib["rx"])
                ry = float(element.attrib["ry"])
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_ellipse_bounds")
                continue
            if rx < 0 or ry < 0 or outside_canvas(cx - rx, cy - ry, cx + rx, cy + ry):
                overflow_reasons.append("ellipse_overflow")
        elif kind == "line":
            try:
                coordinates = [float(element.attrib[item]) for item in ("x1", "y1", "x2", "y2")]
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_line_bounds")
                continue
            if outside_canvas(
                min(coordinates[0], coordinates[2]),
                min(coordinates[1], coordinates[3]),
                max(coordinates[0], coordinates[2]),
                max(coordinates[1], coordinates[3]),
            ):
                overflow_reasons.append("line_overflow")
        elif kind in {"polyline", "polygon"}:
            try:
                coordinates = parse_numbers(element.attrib["points"])
                if len(coordinates) < 4 or len(coordinates) % 2:
                    raise ValueError("invalid point pairs")
            except (KeyError, ValueError):
                overflow_reasons.append(f"invalid_{kind}_bounds")
                continue
            xs, ys = coordinates[0::2], coordinates[1::2]
            if outside_canvas(min(xs), min(ys), max(xs), max(ys)):
                overflow_reasons.append(f"{kind}_overflow")
        elif kind == "path":
            path_data = element.attrib.get("d")
            if not isinstance(path_data, str) or not path_data.strip():
                overflow_reasons.append("invalid_path_bounds")
                continue
            try:
                bounds = path_bounds(path_data)
            except ValueError:
                overflow_reasons.append("unsupported_or_invalid_path_geometry")
                continue
            if outside_canvas(*bounds):
                overflow_reasons.append("path_overflow")
    return {
        "bounded": not overflow_reasons,
        "overflow": bool(overflow_reasons),
        "canvas": {"width": int(width), "height": int(height)},
        "overflow_reasons": sorted(set(overflow_reasons)),
    }


def build_rater_form(scenario: dict[str, Any]) -> dict[str, Any]:
    hard_gates = [
        {
            "id": "source_product_integrity",
            "question": "Is the source product recognizable and free of material geometry loss or invention?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "message_and_brief_accuracy",
            "question": "Are every required message and brief constraint accurate, legible, and unsupported claims absent?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "brand_and_rights_safety",
            "question": "Does the option obey the brand rules and avoid people, third-party marks, or rights ambiguity?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "technical_delivery_integrity",
            "question": "Is the option complete, readable, within the canvas, and free of clipping or overflow?",
            "allowed": ["pass", "fail", "uncertain"],
        },
    ]
    dimensions = [
        (
            "product_recognizability",
            "1: product identity is lost or materially distorted",
            "3: product is recognizable with minor ambiguity",
            "5: product is immediately recognizable and faithfully preserved",
        ),
        (
            "brief_and_message_fidelity",
            "1: key brief or message requirements are missing or wrong",
            "3: core requirements are present with minor weakness",
            "5: every requirement is accurate, clear, and well integrated",
        ),
        (
            "visual_hierarchy_and_legibility",
            "1: hierarchy is confusing or required text is unreadable",
            "3: hierarchy and text are usable with visible weaknesses",
            "5: attention and reading order are immediate at intended size",
        ),
        (
            "brand_coherence",
            "1: visual language conflicts with the stated brand",
            "3: broadly consistent with some generic or uneven choices",
            "5: distinctive, internally consistent, and on-brief",
        ),
        (
            "craft_and_delivery_readiness",
            "1: obvious artifacts, clipping, overflow, or unfinished details",
            "3: deliverable with minor polish issues",
            "5: clean, bounded, polished, and channel-ready",
        ),
    ]
    return {
        "schema_version": "phase-d-d1-rater-form/v2",
        "scenario_id": scenario["id"],
        "instructions": {
            "task": "Evaluate A and B independently, then state a comparative preference.",
            "independence": "Do not seek system identity or custody mapping. Record any accidental disclosure.",
            "hard_gate_rule": "Complete all four gates for each option before dimension scores or preference.",
            "abstention_rule": "Use abstain when evidence is insufficient; use tie only when both are judgeable and equivalent.",
        },
        "scenario": {
            "product_category": scenario["product_category"],
            "recognizable_features": copy.deepcopy(scenario["recognizable_features"]),
            "channel": scenario["channel"],
            "aspect_ratio": scenario["aspect_ratio"],
            "brief": copy.deepcopy(scenario["brief"]),
            "messages": copy.deepcopy(scenario["messages"]),
        },
        "hard_gates": {"per_option": hard_gates, "options": ["A", "B"]},
        "anchored_dimensions": [
            {"id": item[0], "scale": [1, 2, 3, 4, 5], "anchors": {"1": item[1], "3": item[2], "5": item[3]}}
            for item in dimensions
        ],
        "response_template": {
            "hard_gate_results": {"A": {}, "B": {}},
            "dimension_scores": {"A": {}, "B": {}},
            "preference": {"value": None, "allowed": ["A", "B", "tie", "abstain"]},
            "confidence": {"value": None, "scale": [1, 2, 3, 4, 5]},
            "rationale": "",
            "elapsed_minutes": None,
            "protocol_violations": [],
        },
    }


def write_delivery_bundle(
    destination: Path,
    scenario: dict[str, Any],
    option_a: bytes,
    option_b: bytes,
    rater_form: dict[str, Any],
) -> dict[str, Any]:
    """Reject the superseded D1 rater-delivery writer while V4 is blocked."""
    del (destination, scenario, option_a, option_b, rater_form)
    raise PhaseDRedesignError(
        "D1 rater delivery is blocked and the legacy bundle writer is superseded"
    )


def create_harness_fixture(destination: Path) -> None:
    """Reject the superseded surrogate D2 fixture."""
    del destination
    raise PhaseDRedesignError(
        "D2 surrogate harness fixture is superseded and blocked; real Company OS replay is required"
    )

def execute_mutation_pair(
    frozen_fixture: Path,
    case: dict[str, Any],
    baseline_workflow: dict[str, Any],
    treatment_workflow: dict[str, Any],
    evidence_root: Path,
) -> dict[str, Any]:
    """Reject the surrogate D2 treatment workflow while real replay is unavailable."""
    del (frozen_fixture, case, baseline_workflow, treatment_workflow, evidence_root)
    raise PhaseDRedesignError(
        "D2 treatment workflow execution is blocked and the surrogate V3 helper is superseded"
    )

def validate_mutation_bank(bank_path: Path, contract: dict[str, Any]) -> dict[str, Any]:
    """Validate exact V2/V3 mutation coverage and mutually exclusive case roles."""
    bank = load_json(bank_path)
    if bank.get("schema_version") not in {
        "phase-d-d2-mutation-bank/v2",
        "phase-d-d2-mutation-bank/v3",
        "phase-d-d2-mutation-bank/v4",
    }:
        raise PhaseDRedesignError("D2 mutation bank schema_version is invalid")
    cases = bank.get("cases")
    if not isinstance(cases, list) or not cases:
        raise PhaseDRedesignError("D2 mutation bank cases are invalid")
    seen_ids: set[str] = set()
    observed_classes: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise PhaseDRedesignError("D2 mutation cases must be objects")
        case_id = case.get("id")
        fault_class = case.get("fault_class")
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise PhaseDRedesignError("D2 mutation case ids must be unique nonempty strings")
        if not isinstance(fault_class, str) or not fault_class:
            raise PhaseDRedesignError(f"D2 mutation fault class is invalid: {case_id}")
        seeded_fault = case.get("seeded_fault") is True
        valid_control = case.get("valid_control") is True
        if seeded_fault == valid_control:
            raise PhaseDRedesignError(f"D2 case must be exactly one fault or control: {case_id}")
        severity = case.get("severity")
        if not isinstance(severity, str) or severity.lower() not in {"critical", "high", "medium", "low"}:
            raise PhaseDRedesignError(f"D2 case severity is invalid: {case_id}")
        if seeded_fault and severity.lower() not in {"critical", "high"}:
            raise PhaseDRedesignError(f"D2 seeded material fault is not Critical/High: {case_id}")
        mutation = case.get("mutation")
        if bank.get("schema_version") == "phase-d-d2-mutation-bank/v4":
            replay = case.get("replay")
            if (
                case.get("target") != "production_control_replay"
                or not isinstance(mutation, dict)
                or mutation.get("kind") != "named_control_probe"
                or not isinstance(replay, dict)
                or not all(
                    isinstance(replay.get(field), str) and replay.get(field)
                    for field in ("control_id", "module", "entrypoint", "existing_regression_test")
                )
                or not str(replay.get("module")).startswith("agent_company.")
            ):
                raise PhaseDRedesignError(
                    f"D2 V4 real production control replay mapping is invalid: {case_id}"
                )
        elif case.get("target") not in {"database", "repository"} or not isinstance(mutation, dict):
            raise PhaseDRedesignError(f"D2 mutation target or operation is invalid: {case_id}")
        for protected in ("protected_tables", "protected_repo_paths"):
            values = case.get(protected)
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                raise PhaseDRedesignError(f"D2 {protected} is invalid: {case_id}")
        seen_ids.add(case_id)
        observed_classes.add(fault_class)

    required_faults = contract.get("required_fault_classes")
    required_controls = contract.get("required_control_classes")
    if bank.get("schema_version") == "phase-d-d2-mutation-bank/v4" or (
        required_faults is not None or required_controls is not None
    ):
        if (
            not isinstance(required_faults, list)
            or not isinstance(required_controls, list)
            or not required_faults
            or not required_controls
            or not all(isinstance(item, str) for item in required_faults + required_controls)
        ):
            raise PhaseDRedesignError("D2 contract required class lists are invalid")
        if bank.get("schema_version") == "phase-d-d2-mutation-bank/v4" and (
            len(required_faults) != 13 or len(required_controls) != 3
        ):
            raise PhaseDRedesignError("D2 V4 contract requires exact 13+3 named classes")
        required = set(required_faults) | set(required_controls)
        if len(required) != len(required_faults) + len(required_controls):
            raise PhaseDRedesignError("D2 fault and control classes must be distinct")
        if observed_classes != required:
            raise PhaseDRedesignError("D2 mutation bank does not exactly cover every required class")
        if bank.get("schema_version") == "phase-d-d2-mutation-bank/v4":
            if len(cases) != 16 or len(observed_classes) != 16:
                raise PhaseDRedesignError("D2 V4 mutation bank requires exactly sixteen named classes")
            control_ids = [str(case["replay"]["control_id"]) for case in cases]
            if len(set(control_ids)) != 16:
                raise PhaseDRedesignError("D2 V4 cases must target distinct real controls")
        elif len(cases) < 16:
            raise PhaseDRedesignError("D2 V3 mutation bank requires at least sixteen cases")
        role_by_class = {
            str(case["fault_class"]): (case.get("seeded_fault") is True, case.get("valid_control") is True)
            for case in cases
        }
        if any(role_by_class[item] != (True, False) for item in required_faults):
            raise PhaseDRedesignError("D2 required fault class is not seeded independently")
        if any(role_by_class[item] != (False, True) for item in required_controls):
            raise PhaseDRedesignError("D2 required control class is not evaluated independently")
    dry_run_subset = bank.get("dry_run_subset", [])
    if not isinstance(dry_run_subset, list) or any(item not in seen_ids for item in dry_run_subset):
        raise PhaseDRedesignError("D2 dry-run subset contains an unknown case")
    return bank


def derive_d2_observation_thresholds(
    pairs: list[dict[str, Any]],
    *,
    contract: dict[str, Any] | None = None,
    bank: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate frozen observations, then refuse certification until replay exists."""
    if not pairs:
        raise PhaseDRedesignError("D2 threshold derivation requires paired observations")
    if contract is None or bank is None:
        raise PhaseDRedesignError(
            "D2 threshold derivation requires the exact contract and bank"
        )
    required_faults = contract.get("required_fault_classes")
    required_controls = contract.get("required_control_classes")
    cases = bank.get("cases")
    if (
        contract.get("schema_version") != "phase-d-d2-corrected-contract/v4"
        or bank.get("schema_version") != "phase-d-d2-mutation-bank/v4"
        or not isinstance(required_faults, list)
        or len(required_faults) != 13
        or len(set(required_faults)) != 13
        or not isinstance(required_controls, list)
        or len(required_controls) != 3
        or len(set(required_controls)) != 3
        or set(required_faults) & set(required_controls)
        or not isinstance(cases, list)
        or len(cases) != 16
    ):
        raise PhaseDRedesignError("D2 contract/bank must define exact V4 13+3 named classes")
    bank_by_id = {
        str(case.get("id")): case for case in cases if isinstance(case, dict)
    }
    if len(bank_by_id) != 16:
        raise PhaseDRedesignError("D2 contract/bank case ids are incomplete or duplicate")
    for case_id, frozen_case in bank_by_id.items():
        fault_class = frozen_case.get("fault_class")
        if fault_class in required_faults:
            valid_role = (
                frozen_case.get("seeded_fault") is True
                and frozen_case.get("valid_control") is False
                and str(frozen_case.get("severity", "")).lower() in {"critical", "high"}
            )
        elif fault_class in required_controls:
            valid_role = (
                frozen_case.get("seeded_fault") is False
                and frozen_case.get("valid_control") is True
            )
        else:
            valid_role = False
        if not valid_role:
            raise PhaseDRedesignError(
                f"D2 frozen case has unknown class or wrong role/severity: {case_id}"
            )

    pair_by_id: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        if not isinstance(pair, dict):
            raise PhaseDRedesignError("D2 observation is malformed")
        case_id = pair.get("case_id")
        if not isinstance(case_id, str) or case_id not in bank_by_id or case_id in pair_by_id:
            raise PhaseDRedesignError("D2 observations are incomplete, duplicate, or unknown")
        frozen_case = bank_by_id[case_id]
        expected_fields = (set(frozen_case) - {"id"}) | {"case_id", "baseline", "treatment"}
        if set(pair) != expected_fields:
            raise PhaseDRedesignError(
                f"D2 observation fields do not match the frozen schema: {case_id}"
            )
        observed_metadata = {
            key: value
            for key, value in pair.items()
            if key not in {"case_id", "baseline", "treatment"}
        }
        frozen_metadata = {
            key: value for key, value in frozen_case.items() if key != "id"
        }
        if observed_metadata != frozen_metadata:
            raise PhaseDRedesignError(
                f"D2 frozen observation metadata does not match deeply: {case_id}"
            )
        for side in ("baseline", "treatment"):
            side_record = pair.get(side)
            observation = side_record.get("observation") if isinstance(side_record, dict) else None
            if (
                not isinstance(side_record, dict)
                or set(side_record) != {"observation"}
                or not isinstance(observation, dict)
                or set(observation) != {"outcome"}
                or observation.get("outcome") not in {"allowed", "denied"}
            ):
                raise PhaseDRedesignError(
                    f"D2 observation fields or outcome are malformed: {case_id} {side}"
                )
        pair_by_id[case_id] = pair
    if set(pair_by_id) != set(bank_by_id):
        raise PhaseDRedesignError("D2 observations do not exactly cover the frozen bank")
    if {str(pair["fault_class"]) for pair in pairs} != set(required_faults) | set(required_controls):
        raise PhaseDRedesignError("D2 observations do not cover the exact named classes")
    raise PhaseDRedesignError(
        "D2 treatment certification is unavailable: the concrete internal real production "
        "Company OS C2 replay verifier is not implemented"
    )


def _artifact_manifest(output: Path) -> dict[str, Any]:
    records = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "evidence-manifest.json":
            records.append({
                "path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return {
        "schema_version": "phase-d-redesign-development-only-evidence-manifest/v1",
        "status": "development_only_unverified_non_candidate",
        "development_only": True,
        "verified": False,
        "candidate_evidence": False,
        "corrected_treatments_executed": False,
        "artifacts": records,
        "external_actions_observed": [],
    }


def _v4_svg_protocol_canaries() -> list[dict[str, Any]]:
    prefix = (
        b'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" '
        b'viewBox="0 0 512 512">'
    )
    cases = {
        "absolute_path_inside": b'<path d="M 10 10 L 500 10"/>',
        "relative_path_rejected": b'<path d="M 500 10 l 20 0"/>',
        "stroke_rejected": b'<rect x="0" y="0" width="512" height="512" stroke="#000"/>',
        "style_transform_rejected": b'<rect x="0" y="0" width="10" height="10" style="transform:translate(600px,0)"/>',
        "nested_text_rejected": b'<text x="10" y="20" font-size="12" data-max-width="100"><tspan>nested</tspan></text>',
        "use_defs_rejected": b'<defs><rect id="r" x="700" y="0" width="10" height="10"/></defs><use href="#r"/>',
        "curve_rejected": b'<path d="M 0 0 C 0 0 700 0 700 1"/>',
        "arc_rejected": b'<path d="M 0 0 A 700 700 0 0 0 10 10"/>',
        "malformed_rejected": b'<polygon points="0,0 10,wat"/>',
        "foreign_namespace_rejected": b'<evil:rect xmlns:evil="urn:evil" x="0" y="0" width="10" height="10"/>',
        "external_image_rejected": b'<image x="0" y="0" width="10" height="10" href="https://example.invalid/image.png"/>',
        "paint_server_rejected": b'<rect x="0" y="0" width="10" height="10" fill="url(https://example.invalid/p.svg#x)"/>',
        "malformed_path_separators_rejected": b'<path d="M,,10,,10 L 20 20"/>',
        "path_without_moveto_rejected": b'<path d="L 10 10"/>',
        "incomplete_moveto_rejected": b'<path d="M L 10 10"/>',
        "incomplete_lineto_rejected": b'<path d="M 10 10 L Z"/>',
        "nested_svg_rejected": b'<svg width="512" height="512" viewBox="0 0 1 1"><rect x="2" y="0" width="1" height="1"/></svg>',
        "embedded_svg_image_rejected": b'<image x="0" y="0" width="10" height="10" href="data:image/svg+xml;base64,PHN2Zy8+"/>',
        "embedded_raster_image_rejected": b'<image x="0" y="0" width="10" height="10" href="data:image/png;base64,iVBORw0KGgo="/>',
        "leaf_text_rejected": b'<text x="10" y="20" font-family="sans-serif" font-size="12" fill="#000000" textLength="40" lengthAdjust="spacingAndGlyphs">text</text>',
        "malformed_points_rejected": b'<polygon points="0,,0 10,10 20,20"/>',
    }
    records = []
    for case_id, body in cases.items():
        validation = validate_bounded_svg(prefix + body + b"</svg>")
        expected_bounded = case_id == "absolute_path_inside"
        if validation["bounded"] is not expected_bounded:
            raise PhaseDRedesignError(f"D1 SVG protocol canary failed: {case_id}")
        records.append({
            "case_id": case_id,
            "expected_bounded": expected_bounded,
            "observed_bounded": validation["bounded"],
            "overflow_reasons": validation["overflow_reasons"],
        })
    return records


def run_redesign_dry_run(
    root: Path,
    output: Path,
    *,
    freeze_path: Path | None = None,
    allow_development_overlay: bool = False,
) -> dict[str, Any]:
    """Run only explicit non-treatment V4 protocol checks while Phase D is blocked."""
    freeze = freeze_path or (
        root / "docs" / "assurance" / "phase-d" / "redesign" / "corrected-freeze-v4.json"
    )
    freeze_document = load_json(freeze)
    if freeze_document.get("schema_version") != "phase-d-redesign-freeze/v4":
        raise PhaseDRedesignError(
            "V2/V3 blocked dry-run paths are superseded because they execute treatment workflows"
        )
    if output.exists():
        raise PhaseDRedesignError("blocked protocol output must not already exist")
    verification = verify_corrected_freeze(
        root,
        freeze,
        allow_development_overlay=allow_development_overlay,
    )
    if verification["execution_authorized"]:
        raise PhaseDRedesignError("blocked protocol checks cannot run after authorization")
    documents = verification["documents"]
    scenario_bank = validate_scenario_bank(
        root, root / freeze_document["protocol_inputs"]["d1_scenario_bank"]
    )
    scenario_ids = [str(item["id"]) for item in scenario_bank["scenarios"]]
    assignments = balanced_blind_assignments(
        scenario_ids, str(scenario_bank["randomization_seed"])
    )
    if sum(item["A"] == "candidate" for item in assignments.values()) != len(assignments) // 2:
        raise PhaseDRedesignError("D1 static assignment protocol is not exactly balanced")
    svg_canaries = _v4_svg_protocol_canaries()
    mutation_bank = validate_mutation_bank(
        root / freeze_document["protocol_inputs"]["d2_mutation_bank"],
        documents["d2_contract"],
    )
    loader = unittest.TestLoader()
    for case in mutation_bank["cases"]:
        replay = case["replay"]
        try:
            module = importlib.import_module(str(replay["module"]))
            entrypoint: Any = module
            for part in str(replay["entrypoint"]).split("."):
                entrypoint = getattr(entrypoint, part)
        except (ImportError, AttributeError) as exc:
            raise PhaseDRedesignError(
                f"D2 named production control entrypoint is not resolvable: {case['id']}"
            ) from exc
        if not callable(entrypoint):
            raise PhaseDRedesignError(
                f"D2 named production control entrypoint is not callable: {case['id']}"
            )
        suite = loader.loadTestsFromName(str(replay["existing_regression_test"]))
        if loader.errors or suite.countTestCases() != 1:
            raise PhaseDRedesignError(
                f"D2 named production control regression is not resolvable: {case['id']}"
            )
    replay_mappings = [
        {
            "case_id": case["id"],
            "fault_class": case["fault_class"],
            "control_id": case["replay"]["control_id"],
            "module": case["replay"]["module"],
            "entrypoint": case["replay"]["entrypoint"],
            "existing_regression_test": case["replay"]["existing_regression_test"],
        }
        for case in mutation_bank["cases"]
    ]
    checks = [
        "baseline_git_object_diagnostic",
        "separate_signature_verifier_blocker",
        "d1_static_input_and_renderer_contract",
        "d1_svg_adversarial_validator_canaries",
        "d2_named_production_control_mapping",
        "d2_real_replay_blocker",
    ]
    result = {
        "schema_version": "phase-d-redesign-development-only-protocol/v4",
        "status": "development_only_unverified_non_candidate",
        "development_only": True,
        "verified": False,
        "candidate_evidence": False,
        "corrected_treatments_executed": False,
        "phase_d_treatment_pass_possible": False,
        "authorization": {
            "status": verification["status"],
            "execution_authorized": False,
            "blockers": verification.get("blockers", []),
        },
        "checks_executed": checks,
        "d1": {
            "status": "development_only_static_protocol_diagnostics_complete",
            "scenario_contracts_checked": len(scenario_ids),
            "assignment_protocol_checked": True,
            "svg_validator_canaries": svg_canaries,
            "treatment_workflows_executed": 0,
            "artifacts_generated": 0,
            "ratings_collected": 0,
        },
        "d2": {
            "status": "blocked_real_production_control_replay_not_implemented",
            "named_control_mappings_checked": len(replay_mappings),
            "control_mappings": replay_mappings,
            "real_production_replay_status": "not_implemented",
            "treatment_workflows_executed": 0,
            "database_mutations_attempted": 0,
            "repository_mutations_attempted": 0,
            "observations_collected": 0,
            "thresholds_passed": False,
        },
        "forbidden_actions_observed": [],
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "protocol-result.json", result)
    write_json(output / "evidence-manifest.json", {
        "schema_version": "phase-d-redesign-development-only-evidence-manifest/v4",
        "status": "development_only_unverified_non_candidate",
        "development_only": True,
        "verified": False,
        "candidate_evidence": False,
        "corrected_treatments_executed": False,
        "phase_d_treatment_pass_possible": False,
        "artifacts": [{
            "path": "protocol-result.json",
            "bytes": (output / "protocol-result.json").stat().st_size,
            "sha256": sha256_file(output / "protocol-result.json"),
        }],
        "external_actions_observed": [],
    })
    return result


def verify_redesign_evidence(
    root: Path,
    expected_output: Path,
    *,
    freeze_path: Path | None = None,
) -> dict[str, Any]:
    """Fail closed until candidate identity can be verified outside this process."""
    del (root, expected_output, freeze_path)
    raise PhaseDRedesignError(
        "strict candidate verification is blocked: an externally signed candidate manifest "
        "and separate signature verifier are required"
    )
