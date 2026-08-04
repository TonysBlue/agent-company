"""Trusted evaluation registry and immutable run ledger."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .assurance import AssuranceError, AssuranceKernel, _canonical
from .config import CompanyConfig
from .db import Store, utcnow
from .integrity import signature as integrity_signature


class EvaluationError(ValueError):
    pass


KINDS = {"candidate", "dataset", "grader", "environment"}
STATUSES = {"failed", "abandoned", "completed"}


class TrustedEvaluator:
    def __init__(self, config: CompanyConfig):
        self.config = config
        self.store = Store(config.db_path, workspace=config.workspace)
        self.kernel = AssuranceKernel(config)

    def init(self) -> None:
        self.kernel.init()
        with self.store.connect() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(trusted_eval_runs)")}
            if columns and "evidence_sha256" not in columns:
                conn.execute("ALTER TABLE trusted_eval_runs ADD COLUMN evidence_sha256 TEXT")
            if columns and "evaluator_principal_id" not in columns:
                conn.execute("ALTER TABLE trusted_eval_runs ADD COLUMN evaluator_principal_id TEXT")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS trusted_eval_manifests (
                    kind TEXT NOT NULL,
                    manifest_id TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL UNIQUE,
                    content_sha256 TEXT NOT NULL,
                    protected INTEGER NOT NULL,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(kind, manifest_id)
                );
                CREATE TABLE IF NOT EXISTS trusted_eval_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiative_id TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    candidate_sha256 TEXT NOT NULL,
                    dataset_sha256 TEXT NOT NULL,
                    grader_sha256 TEXT NOT NULL,
                    environment_sha256 TEXT NOT NULL,
                    seed INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    evidence_ref TEXT NOT NULL,
                    evidence_sha256 TEXT,
                    evaluator_principal_id TEXT NOT NULL,
                    result_sha256 TEXT NOT NULL UNIQUE,
                    integrity_signature TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(initiative_id, attempt)
                );
                CREATE TABLE IF NOT EXISTS trusted_eval_quarantines (
                    initiative_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS trusted_eval_contracts (
                    initiative_id TEXT PRIMARY KEY,
                    max_attempts INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                DROP TRIGGER IF EXISTS trusted_eval_runs_immutable_update;
                DROP TRIGGER IF EXISTS trusted_eval_runs_immutable_delete;
                DROP TRIGGER IF EXISTS trusted_eval_manifests_immutable_update;
                DROP TRIGGER IF EXISTS trusted_eval_manifests_immutable_delete;
                DROP TRIGGER IF EXISTS trusted_eval_contracts_immutable_update;
                DROP TRIGGER IF EXISTS trusted_eval_contracts_immutable_delete;
                DROP TRIGGER IF EXISTS trusted_eval_quarantines_append_only;
                DROP TRIGGER IF EXISTS trusted_eval_quarantines_no_delete;
                CREATE TRIGGER trusted_eval_runs_immutable_update
                    BEFORE UPDATE ON trusted_eval_runs
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluation runs are immutable'); END;
                CREATE TRIGGER trusted_eval_runs_immutable_delete
                    BEFORE DELETE ON trusted_eval_runs
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluation runs are immutable'); END;
                CREATE TRIGGER trusted_eval_manifests_immutable_update
                    BEFORE UPDATE ON trusted_eval_manifests
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluation manifests are immutable'); END;
                CREATE TRIGGER trusted_eval_manifests_immutable_delete
                    BEFORE DELETE ON trusted_eval_manifests
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluation manifests are immutable'); END;
                CREATE TRIGGER trusted_eval_contracts_immutable_update
                    BEFORE UPDATE ON trusted_eval_contracts
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluation contracts are immutable'); END;
                CREATE TRIGGER trusted_eval_contracts_immutable_delete
                    BEFORE DELETE ON trusted_eval_contracts
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluation contracts are immutable'); END;
                CREATE TRIGGER trusted_eval_quarantines_append_only
                    BEFORE UPDATE ON trusted_eval_quarantines
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluation quarantine is append-only'); END;
                CREATE TRIGGER trusted_eval_quarantines_no_delete
                    BEFORE DELETE ON trusted_eval_quarantines
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluation quarantine is append-only'); END;
                """
            )
            run_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(trusted_eval_runs)")
            }
            if "integrity_signature" not in run_columns:
                conn.execute(
                    "ALTER TABLE trusted_eval_runs ADD COLUMN integrity_signature TEXT"
                )

    def _evaluator(self, actor: str, principal_id: str) -> None:
        principal = self.kernel._assert_principal(actor, principal_id, {"operator"})
        if principal["actor"] != "Trusted Evaluator":
            raise AssuranceError("principal is not the trusted evaluator")

    def register_manifest(
        self, kind: str, manifest: dict[str, Any], *, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
        self._evaluator(actor, principal_id)
        if kind not in KINDS or set(manifest) != {"schema_version", "id", "content_sha256", "protected"}:
            raise EvaluationError("invalid trusted evaluation manifest")
        if manifest["schema_version"] != f"trusted-eval-{kind}/v1":
            raise EvaluationError("manifest schema does not match kind")
        if not isinstance(manifest["id"], str) or not manifest["id"].strip():
            raise EvaluationError("manifest id is required")
        if not isinstance(manifest["content_sha256"], str) or len(manifest["content_sha256"]) != 64:
            raise EvaluationError("content sha256 is required")
        if type(manifest["protected"]) is not bool:
            raise EvaluationError("protected must be boolean")
        digest = hashlib.sha256(_canonical(manifest).encode("ascii")).hexdigest()
        with self.store.connect_readonly() as conn:
            existing = conn.execute(
                "SELECT manifest_sha256 FROM trusted_eval_manifests WHERE kind=? AND manifest_id=?",
                (kind, manifest["id"]),
            ).fetchone()
        if existing and existing["manifest_sha256"] != digest:
            raise EvaluationError("trusted evaluation manifest is immutable")
        content_ref = self.config.workspace / "data" / "trusted-eval-content" / manifest["content_sha256"]
        if not content_ref.is_file():
            raise EvaluationError("content-addressed evaluation input is missing")
        actual_content_sha256 = hashlib.sha256(content_ref.read_bytes()).hexdigest()
        if actual_content_sha256 != manifest["content_sha256"]:
            raise EvaluationError("evaluation input content hash mismatch")
        with self.store.connect() as conn:
            existing = conn.execute(
                "SELECT manifest_sha256 FROM trusted_eval_manifests WHERE kind=? AND manifest_id=?",
                (kind, manifest["id"]),
            ).fetchone()
            if existing and existing["manifest_sha256"] != digest:
                raise EvaluationError("trusted evaluation manifest is immutable")
            conn.execute(
                """INSERT OR IGNORE INTO trusted_eval_manifests(
                       kind,manifest_id,manifest_sha256,content_sha256,protected,manifest_json,created_at
                   ) VALUES (?,?,?,?,?,?,?)""",
                (kind, manifest["id"], digest, manifest["content_sha256"], int(manifest["protected"]), _canonical(manifest), utcnow()),
            )
        return {"kind": kind, "id": manifest["id"], "manifest_sha256": digest, "protected": manifest["protected"]}

    def list_manifests(self, *, actor: str, principal_id: str) -> list[dict[str, Any]]:
        self.init()
        try:
            self._evaluator(actor, principal_id)
            trusted = True
        except AssuranceError:
            trusted = False
        with self.store.connect_readonly() as conn:
            rows = conn.execute(
                "SELECT kind,manifest_id,manifest_sha256,protected FROM trusted_eval_manifests ORDER BY kind,manifest_id"
            ).fetchall()
        return [
            {"kind": row["kind"], "id": row["manifest_id"], "manifest_sha256": row["manifest_sha256"],
             "protected": bool(row["protected"]), "access": "trusted" if trusted else "redacted"}
            for row in rows if trusted or not row["protected"]
        ]

    def record_run(
        self, *, initiative_id: str, refs: dict[str, str], seed: int, status: str,
        evidence_ref: str, max_attempts: int, actor: str, principal_id: str,
    ) -> dict[str, Any]:
        self.init()
        self._evaluator(actor, principal_id)
        if set(refs) != KINDS or status not in STATUSES or type(seed) is not int:
            raise EvaluationError("invalid trusted evaluation run")
        if not evidence_ref.strip() or type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise EvaluationError("invalid attempt contract")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM assurance_initiatives WHERE initiative_id=?", (initiative_id,)
            ).fetchone():
                raise EvaluationError("assurance initiative does not exist")
            contract = conn.execute(
                "SELECT max_attempts FROM trusted_eval_contracts WHERE initiative_id=?", (initiative_id,)
            ).fetchone()
            if contract is None:
                conn.execute(
                    "INSERT INTO trusted_eval_contracts(initiative_id,max_attempts,created_at) VALUES (?,?,?)",
                    (initiative_id, max_attempts, utcnow()),
                )
            elif contract["max_attempts"] != max_attempts:
                raise EvaluationError("immutable attempt budget mismatch")
            if conn.execute("SELECT 1 FROM trusted_eval_quarantines WHERE initiative_id=?", (initiative_id,)).fetchone():
                raise EvaluationError("initiative is quarantined")
            for kind, digest in refs.items():
                manifest = conn.execute(
                    "SELECT content_sha256 FROM trusted_eval_manifests WHERE kind=? AND manifest_sha256=?",
                    (kind, digest),
                ).fetchone()
                if manifest is None:
                    raise EvaluationError(f"unknown {kind} manifest")
                content_ref = self.config.workspace / "data" / "trusted-eval-content" / manifest["content_sha256"]
                if not content_ref.is_file() or hashlib.sha256(content_ref.read_bytes()).hexdigest() != manifest["content_sha256"]:
                    raise EvaluationError(f"{kind} content hash mismatch")
            evidence_path = (self.config.workspace / evidence_ref).resolve()
            workspace = self.config.workspace.resolve()
            if workspace not in evidence_path.parents or not evidence_path.is_file():
                raise EvaluationError("evaluation evidence is missing or outside workspace")
            evidence_sha256 = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
            attempt = conn.execute(
                "SELECT COUNT(*) AS c FROM trusted_eval_runs WHERE initiative_id=?", (initiative_id,)
            ).fetchone()["c"] + 1
            if attempt > max_attempts:
                raise EvaluationError("candidate attempt budget exhausted")
            result = {
                "initiative_id": initiative_id, "attempt": attempt, "refs": refs,
                "seed": seed, "status": status, "evidence_ref": evidence_ref,
                "evidence_sha256": evidence_sha256,
            }
            digest = hashlib.sha256(_canonical(result).encode("ascii")).hexdigest()
            created_at = utcnow()
            run_values = {
                **result,
                "evaluator_principal_id": principal_id,
                "result_sha256": digest,
                "created_at": created_at,
            }
            conn.execute(
                """INSERT INTO trusted_eval_runs(
                       initiative_id,attempt,candidate_sha256,dataset_sha256,grader_sha256,
                       environment_sha256,seed,status,evidence_ref,evidence_sha256,
                       evaluator_principal_id,result_sha256,integrity_signature,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (initiative_id, attempt, refs["candidate"], refs["dataset"], refs["grader"],
                 refs["environment"], seed, status, evidence_ref, evidence_sha256,
                 principal_id, digest,
                 integrity_signature(self.config.db_path, "trusted-eval-run", run_values),
                 created_at),
            )
        return {**result, "result_sha256": digest}

    def quarantine(self, initiative_id: str, reason: str, *, actor: str, principal_id: str) -> None:
        self.init()
        self._evaluator(actor, principal_id)
        if not reason.strip():
            raise EvaluationError("quarantine reason is required")
        with self.store.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO trusted_eval_quarantines(initiative_id,reason,created_at) VALUES (?,?,?)",
                (initiative_id, reason.strip(), utcnow()),
            )

    def list_runs(
        self, initiative_id: str, *, actor: str, principal_id: str,
    ) -> list[dict[str, Any]]:
        self.init()
        self._evaluator(actor, principal_id)
        with self.store.connect_readonly() as conn:
            quarantine = conn.execute(
                "SELECT reason,created_at FROM trusted_eval_quarantines WHERE initiative_id=?", (initiative_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT * FROM trusted_eval_runs WHERE initiative_id=? ORDER BY attempt", (initiative_id,)
            ).fetchall()
        return [{**dict(row), "quarantined": quarantine is not None} for row in rows]
