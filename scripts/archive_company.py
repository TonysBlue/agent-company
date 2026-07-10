#!/usr/bin/env python3
"""Create a consistent, checksummed archive of Agent Company operating state."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ARCHIVES = ROOT / "archives"
RUNTIME_PATHS = [
    DATA / "artifacts",
    DATA / "chairman" / "inbox",
    DATA / "chairman" / "outbox",
    ROOT / "logs",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip()


def copy_database(source: Path, destination: Path) -> None:
    source_conn = sqlite3.connect(source)
    destination_conn = sqlite3.connect(destination)
    try:
        source_conn.backup(destination_conn)
    finally:
        destination_conn.close()
        source_conn.close()


def create_archive(label: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label).strip("-") or "snapshot"
    target = ARCHIVES / f"{timestamp}-{safe_label}"
    target.mkdir(parents=True, exist_ok=False)

    database = DATA / "company.sqlite3"
    if not database.exists():
        raise FileNotFoundError(f"Company database not found: {database}")
    copy_database(database, target / "company.sqlite3")

    copied: list[str] = []
    for source_dir in RUNTIME_PATHS:
        if not source_dir.exists():
            continue
        relative = source_dir.relative_to(ROOT)
        destination_dir = target / relative
        destination_dir.mkdir(parents=True, exist_ok=True)
        for source in source_dir.rglob("*"):
            if not source.is_file() or source.name == ".gitkeep":
                continue
            destination = target / source.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied.append(str(source.relative_to(ROOT)))

    files = sorted(path for path in target.rglob("*") if path.is_file())
    manifest = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "git_commit": git_value("rev-parse", "HEAD") or None,
        "git_branch": git_value("branch", "--show-current") or None,
        "git_dirty": bool(git_value("status", "--porcelain")),
        "runtime_files_copied": copied,
        "files": [
            {
                "path": str(path.relative_to(target)),
                "size": path.stat().st_size,
                "sha256": sha256(path),
            }
            for path in files
        ],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


def verify_archive(target: Path) -> list[str]:
    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        return ["manifest.json missing"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    for entry in manifest.get("files", []):
        path = target / entry["path"]
        if not path.exists():
            errors.append(f"missing: {entry['path']}")
        elif sha256(path) != entry["sha256"]:
            errors.append(f"checksum mismatch: {entry['path']}")
    db = target / "company.sqlite3"
    if db.exists():
        conn = sqlite3.connect(db)
        try:
            result = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                errors.append(f"sqlite integrity: {result}")
        finally:
            conn.close()
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create")
    create.add_argument("--label", default="operating-snapshot")
    verify = sub.add_parser("verify")
    verify.add_argument("path", type=Path)
    args = parser.parse_args()

    if args.command == "create":
        target = create_archive(args.label)
        errors = verify_archive(target)
        print(json.dumps({"archive": str(target), "ok": not errors, "errors": errors}, ensure_ascii=False))
        return 1 if errors else 0
    errors = verify_archive(args.path.resolve())
    print(json.dumps({"archive": str(args.path.resolve()), "ok": not errors, "errors": errors}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
