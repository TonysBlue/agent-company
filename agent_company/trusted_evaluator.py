"""Trusted evaluation registry and immutable run ledger."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .assurance import AssuranceError, AssuranceKernel, _canonical
from .config import CompanyConfig
from .db import Store, utcnow
from .integrity import (
    signature as integrity_signature,
    verify as verify_integrity_signature,
)


class EvaluationError(ValueError):
    pass


KINDS = {"candidate", "dataset", "grader", "environment"}
STATUSES = {"failed", "abandoned", "completed"}
CONTRACT_SIGNATURE_KEYS = ("initiative_id", "max_attempts", "created_at")
EVALUATOR_PROVENANCE_KEYS = (
    "principal_id", "sequence", "actor", "authority", "credential_sha256",
    "principal_created_at", "issued_at", "previous_signature",
)
RUN_PROVENANCE_KEYS = (
    "evaluator_actor", "evaluator_authority", "evaluator_credential_sha256",
    "evaluator_principal_created_at", "evaluator_provenance_signature",
    "contract_integrity_signature",
)


class TrustedEvaluator:
    def __init__(self, config: CompanyConfig):
        self.config = config
        self.store = Store(config.db_path, workspace=config.workspace)
        self.kernel = AssuranceKernel(config)

    def init(self) -> None:
        self.kernel.init()
        with self.store.connect() as conn:
            run_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(trusted_eval_runs)")
            }
            for column in (
                "evidence_sha256", "evaluator_principal_id", "integrity_signature",
                *RUN_PROVENANCE_KEYS,
            ):
                if run_columns and column not in run_columns:
                    conn.execute(
                        f"ALTER TABLE trusted_eval_runs ADD COLUMN {column} TEXT"
                    )
            contract_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(trusted_eval_contracts)")
            }
            if contract_columns and "integrity_signature" not in contract_columns:
                conn.execute(
                    "ALTER TABLE trusted_eval_contracts "
                    "ADD COLUMN integrity_signature TEXT"
                )
            provenance_columns = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(trusted_eval_evaluator_credentials)"
                )
            }
            for column, definition in (
                ("sequence", "INTEGER"),
                ("previous_signature", "TEXT"),
            ):
                if provenance_columns and column not in provenance_columns:
                    conn.execute(
                        "ALTER TABLE trusted_eval_evaluator_credentials "
                        f"ADD COLUMN {column} {definition}"
                    )
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
                    evaluator_actor TEXT,
                    evaluator_authority TEXT,
                    evaluator_credential_sha256 TEXT,
                    evaluator_principal_created_at TEXT,
                    evaluator_provenance_signature TEXT,
                    contract_integrity_signature TEXT,
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
                    created_at TEXT NOT NULL,
                    integrity_signature TEXT
                );
                CREATE TABLE IF NOT EXISTS trusted_eval_evaluator_credentials (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    principal_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    actor TEXT NOT NULL,
                    authority TEXT NOT NULL,
                    credential_sha256 TEXT NOT NULL,
                    principal_created_at TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    previous_signature TEXT,
                    integrity_signature TEXT NOT NULL UNIQUE,
                    UNIQUE(principal_id, sequence),
                    UNIQUE(principal_id, credential_sha256, principal_created_at)
                );
                DROP TRIGGER IF EXISTS trusted_eval_runs_immutable_update;
                DROP TRIGGER IF EXISTS trusted_eval_runs_immutable_delete;
                DROP TRIGGER IF EXISTS trusted_eval_manifests_immutable_update;
                DROP TRIGGER IF EXISTS trusted_eval_manifests_immutable_delete;
                DROP TRIGGER IF EXISTS trusted_eval_contracts_immutable_update;
                DROP TRIGGER IF EXISTS trusted_eval_contracts_immutable_delete;
                DROP TRIGGER IF EXISTS trusted_eval_quarantines_append_only;
                DROP TRIGGER IF EXISTS trusted_eval_quarantines_no_delete;
                DROP TRIGGER IF EXISTS trusted_eval_evaluator_credentials_immutable_update;
                DROP TRIGGER IF EXISTS trusted_eval_evaluator_credentials_immutable_delete;
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
                CREATE TRIGGER trusted_eval_evaluator_credentials_immutable_update
                    BEFORE UPDATE ON trusted_eval_evaluator_credentials
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluator credential provenance is immutable'); END;
                CREATE TRIGGER trusted_eval_evaluator_credentials_immutable_delete
                    BEFORE DELETE ON trusted_eval_evaluator_credentials
                    BEGIN SELECT RAISE(ABORT, 'trusted evaluator credential provenance is immutable'); END;
                """
            )

    def _evaluator(self, actor: str, principal_id: str) -> dict[str, Any]:
        principal = self.kernel._assert_principal(actor, principal_id, {"operator"})
        if principal["actor"] != "Trusted Evaluator":
            raise AssuranceError("principal is not the trusted evaluator")
        return principal

    @staticmethod
    def _created_at(value: Any) -> datetime:
        if not isinstance(value, str):
            raise EvaluationError("trusted evaluator creation lineage is invalid")
        try:
            created_at = datetime.fromisoformat(value)
        except ValueError as exc:
            raise EvaluationError(
                "trusted evaluator creation lineage is invalid"
            ) from exc
        if (
            created_at.tzinfo is None
            or created_at.utcoffset() != timezone.utc.utcoffset(created_at)
            or created_at.microsecond
            or created_at.isoformat() != value
        ):
            raise EvaluationError("trusted evaluator creation lineage is invalid")
        return created_at

    @staticmethod
    def _contract_values(contract: Any) -> dict[str, Any]:
        return {key: contract[key] for key in CONTRACT_SIGNATURE_KEYS}

    @staticmethod
    def _provenance_values(provenance: Any) -> dict[str, Any]:
        return {key: provenance[key] for key in EVALUATOR_PROVENANCE_KEYS}

    def _transaction_evaluator(
        self, conn: Any, authenticated: dict[str, Any],
    ) -> dict[str, Any]:
        principal = conn.execute(
            "SELECT principal_id,actor,authority,credential_sha256,status,created_at "
            "FROM assurance_principals WHERE principal_id=?",
            (authenticated["principal_id"],),
        ).fetchone()
        if (
            principal is None
            or principal["actor"] != "Trusted Evaluator"
            or principal["authority"] != "operator"
            or principal["status"] != "active"
            or not isinstance(principal["credential_sha256"], str)
            or len(principal["credential_sha256"]) != 64
            or any(
                principal[key] != authenticated[key]
                for key in (
                    "principal_id", "actor", "authority", "credential_sha256",
                )
            )
        ):
            raise AssuranceError("trusted evaluator principal changed during issuance")
        self._created_at(principal["created_at"])
        return dict(principal)

    def _ensure_evaluator_provenance(
        self, conn: Any, principal: dict[str, Any],
    ) -> dict[str, Any]:
        chain = conn.execute(
            "SELECT * FROM trusted_eval_evaluator_credentials "
            "WHERE principal_id=? ORDER BY sequence",
            (principal["principal_id"],),
        ).fetchall()
        previous_signature = None
        previous_issued_at = self._created_at(principal["created_at"])
        for sequence, entry in enumerate(chain, 1):
            values = self._provenance_values(entry)
            issued_at = self._created_at(entry["issued_at"])
            if (
                entry["sequence"] != sequence
                or entry["previous_signature"] != previous_signature
                or entry["actor"] != "Trusted Evaluator"
                or entry["authority"] != "operator"
                or entry["principal_created_at"] != principal["created_at"]
                or issued_at < previous_issued_at
                or not verify_integrity_signature(
                    self.config.db_path, "trusted-eval-evaluator-provenance",
                    values, entry["integrity_signature"],
                )
            ):
                raise EvaluationError(
                    "trusted evaluator credential provenance is invalid"
                )
            previous_signature = entry["integrity_signature"]
            previous_issued_at = issued_at
        if chain:
            if chain[-1]["credential_sha256"] != principal["credential_sha256"]:
                raise EvaluationError(
                    "trusted evaluator credential rotation is not officially recorded"
                )
            return dict(chain[-1])
        issued_at = utcnow()
        if self._created_at(principal["created_at"]) > self._created_at(issued_at):
            raise EvaluationError("trusted evaluator principal does not yet exist")
        values = {
            "principal_id": principal["principal_id"],
            "sequence": len(chain) + 1,
            "actor": principal["actor"],
            "authority": principal["authority"],
            "credential_sha256": principal["credential_sha256"],
            "principal_created_at": principal["created_at"],
            "issued_at": issued_at,
            "previous_signature": previous_signature,
        }
        signature = integrity_signature(
            self.config.db_path, "trusted-eval-evaluator-provenance", values,
        )
        conn.execute(
            """INSERT INTO trusted_eval_evaluator_credentials(
                   principal_id,sequence,actor,authority,credential_sha256,
                   principal_created_at,issued_at,previous_signature,
                   integrity_signature
               ) VALUES (?,?,?,?,?,?,?,?,?)""",
            (*values.values(), signature),
        )
        return {**values, "integrity_signature": signature}

    def _create_contract(
        self, conn: Any, initiative_id: str, max_attempts: int,
    ) -> dict[str, Any]:
        contract = conn.execute(
            "SELECT * FROM trusted_eval_contracts WHERE initiative_id=?",
            (initiative_id,),
        ).fetchone()
        if contract is not None:
            values = self._contract_values(contract)
            if contract["max_attempts"] != max_attempts:
                raise EvaluationError("immutable attempt budget mismatch")
            if not verify_integrity_signature(
                self.config.db_path, "trusted-eval-contract", values,
                contract["integrity_signature"],
            ):
                raise EvaluationError("immutable attempt contract is invalid")
            return dict(contract)
        created_at = utcnow()
        values = {
            "initiative_id": initiative_id,
            "max_attempts": max_attempts,
            "created_at": created_at,
        }
        signature = integrity_signature(
            self.config.db_path, "trusted-eval-contract", values,
        )
        conn.execute(
            "INSERT INTO trusted_eval_contracts("
            "initiative_id,max_attempts,created_at,integrity_signature"
            ") VALUES (?,?,?,?)",
            (initiative_id, max_attempts, created_at, signature),
        )
        return {**values, "integrity_signature": signature}

    def create_contract(
        self, initiative_id: str, max_attempts: int, *, actor: str,
        principal_id: str,
    ) -> dict[str, Any]:
        """Issue or return one immutable, signed attempt contract."""
        self.init()
        if type(max_attempts) is not int or not 1 <= max_attempts <= 3:
            raise EvaluationError("invalid attempt contract")
        authenticated = self._evaluator(actor, principal_id)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if not conn.execute(
                "SELECT 1 FROM assurance_initiatives WHERE initiative_id=?",
                (initiative_id,),
            ).fetchone():
                raise EvaluationError("assurance initiative does not exist")
            principal = self._transaction_evaluator(conn, authenticated)
            self._ensure_evaluator_provenance(conn, principal)
            contract = self._create_contract(conn, initiative_id, max_attempts)
        return {
            **self._contract_values(contract),
            "integrity_signature": contract["integrity_signature"],
        }

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
        authenticated = self._evaluator(actor, principal_id)
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
            principal = self._transaction_evaluator(conn, authenticated)
            provenance = self._ensure_evaluator_provenance(conn, principal)
            contract = self._create_contract(conn, initiative_id, max_attempts)
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
            if (
                self._created_at(contract["created_at"])
                > self._created_at(created_at)
                or self._created_at(provenance["issued_at"])
                > self._created_at(created_at)
            ):
                raise EvaluationError("trusted evaluation issuance chronology is invalid")
            run_values = {
                **result,
                "evaluator_principal_id": principal_id,
                "evaluator_actor": provenance["actor"],
                "evaluator_authority": provenance["authority"],
                "evaluator_credential_sha256": provenance["credential_sha256"],
                "evaluator_principal_created_at": provenance["principal_created_at"],
                "evaluator_provenance_signature": provenance["integrity_signature"],
                "contract_integrity_signature": contract["integrity_signature"],
                "result_sha256": digest,
                "created_at": created_at,
            }
            run_signature = integrity_signature(
                self.config.db_path, "trusted-eval-run", run_values,
            )
            conn.execute(
                """INSERT INTO trusted_eval_runs(
                       initiative_id,attempt,candidate_sha256,dataset_sha256,grader_sha256,
                       environment_sha256,seed,status,evidence_ref,evidence_sha256,
                       evaluator_principal_id,evaluator_actor,evaluator_authority,
                       evaluator_credential_sha256,evaluator_principal_created_at,
                       evaluator_provenance_signature,contract_integrity_signature,
                       result_sha256,integrity_signature,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (initiative_id, attempt, refs["candidate"], refs["dataset"], refs["grader"],
                 refs["environment"], seed, status, evidence_ref, evidence_sha256,
                 principal_id, provenance["actor"], provenance["authority"],
                 provenance["credential_sha256"], provenance["principal_created_at"],
                 provenance["integrity_signature"], contract["integrity_signature"], digest,
                 run_signature, created_at),
            )
        return {
            **result,
            "evaluator_principal_id": principal_id,
            "evaluator_credential_sha256": provenance["credential_sha256"],
            "evaluator_principal_created_at": provenance["principal_created_at"],
            "evaluator_provenance_signature": provenance["integrity_signature"],
            "contract_integrity_signature": contract["integrity_signature"],
            "result_sha256": digest,
            "integrity_signature": run_signature,
        }

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
