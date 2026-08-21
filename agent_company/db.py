"""SQLite persistence and audit helpers."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import ON_DEMAND_CAPABILITIES, RACI, ROLES, SEED_TASKS


ORGANIZATION_MIGRATION_VERSION = "lean-org-v1"
CEO_RUNTIME_SCHEMA_VERSION = "ceo-runtime-schema/v1"

# Recovery provenance triggers are part of the governed schema contract.  Keep
# their definitions centralized so initialization can always replace a
# same-name trigger that was dropped or modified out-of-band, and validators
# can compare canonical SQL rather than trusting trigger names alone.
RECOVERY_TRIGGER_DEFINITIONS = {
    "task_recovery_records_immutable_update": """CREATE TRIGGER task_recovery_records_immutable_update
        BEFORE UPDATE ON task_recovery_records
        BEGIN SELECT RAISE(ABORT, 'task recovery record is immutable'); END""",
    "task_recovery_records_immutable_delete": """CREATE TRIGGER task_recovery_records_immutable_delete
        BEFORE DELETE ON task_recovery_records
        BEGIN SELECT RAISE(ABORT, 'task recovery record is immutable'); END""",
    "task_recovery_records_require_canonical_insert": """CREATE TRIGGER task_recovery_records_require_canonical_insert
        BEFORE INSERT ON task_recovery_records
        WHEN assurance_recovery_signature_valid(
            NEW.id, NEW.task_id, NEW.recovered_at, NEW.actor, NEW.reason, NEW.scope,
            NEW.process_dead_proof, NEW.previous_task_state,
            NEW.previous_execution_state, NEW.new_task_state,
            NEW.new_execution_state, NEW.audit_log_id, NEW.event_id,
            NEW.integrity_signature
        ) != 1
        BEGIN SELECT RAISE(ABORT, 'task recovery record integrity signature required'); END""",
    "task_recovery_task_identity_immutable": """CREATE TRIGGER task_recovery_task_identity_immutable
        BEFORE UPDATE OF owner,title,domain,acceptance_criteria ON tasks
        WHEN EXISTS (SELECT 1 FROM task_recovery_records WHERE task_id=OLD.id)
         AND (NEW.owner IS NOT OLD.owner OR NEW.title IS NOT OLD.title
              OR NEW.domain IS NOT OLD.domain
              OR NEW.acceptance_criteria IS NOT OLD.acceptance_criteria)
        BEGIN SELECT RAISE(ABORT, 'task recovery identity is immutable'); END""",
    "recovery_audit_provenance_immutable_update": """CREATE TRIGGER recovery_audit_provenance_immutable_update
        BEFORE UPDATE OF ts,actor,action,entity,entity_id,details ON audit_log
        WHEN (OLD.action='requeue_exhausted_task' AND OLD.entity='task_execution')
          OR (NEW.action='requeue_exhausted_task' AND NEW.entity='task_execution')
        BEGIN SELECT RAISE(ABORT, 'recovery audit provenance is immutable'); END""",
    "recovery_audit_provenance_immutable_delete": """CREATE TRIGGER recovery_audit_provenance_immutable_delete
        BEFORE DELETE ON audit_log
        WHEN OLD.action='requeue_exhausted_task' AND OLD.entity='task_execution'
        BEGIN SELECT RAISE(ABORT, 'recovery audit provenance is immutable'); END""",
    "recovery_audit_provenance_require_canonical_insert": """CREATE TRIGGER recovery_audit_provenance_require_canonical_insert
        BEFORE INSERT ON audit_log
        WHEN NEW.action='requeue_exhausted_task' AND NEW.entity='task_execution'
         AND NOT EXISTS (
             SELECT 1 FROM task_recovery_records record
             WHERE record.audit_log_id=NEW.id
               AND CAST(record.task_id AS TEXT)=NEW.entity_id
               AND record.recovered_at=NEW.ts
               AND record.actor=NEW.actor
               AND NEW.details=assurance_recovery_payload(
                   record.id,record.task_id,record.recovered_at,record.actor,
                   record.reason,record.scope,record.process_dead_proof,
                   record.previous_task_state,record.previous_execution_state,
                   record.new_task_state,record.new_execution_state,
                   record.audit_log_id,record.event_id,record.integrity_signature
               )
         )
        BEGIN SELECT RAISE(ABORT, 'recovery audit provenance must be one-to-one'); END""",
    "recovery_event_provenance_immutable_update": """CREATE TRIGGER recovery_event_provenance_immutable_update
        BEFORE UPDATE OF created_at,available_at,event_type,entity_type,entity_id,payload,priority ON execution_events
        WHEN (OLD.event_type='task.requeued' AND OLD.entity_type='task')
          OR (NEW.event_type='task.requeued' AND NEW.entity_type='task')
        BEGIN SELECT RAISE(ABORT, 'recovery event provenance is immutable'); END""",
    "recovery_event_provenance_immutable_delete": """CREATE TRIGGER recovery_event_provenance_immutable_delete
        BEFORE DELETE ON execution_events
        WHEN OLD.event_type='task.requeued' AND OLD.entity_type='task'
        BEGIN SELECT RAISE(ABORT, 'recovery event provenance is immutable'); END""",
    "recovery_event_provenance_require_canonical_insert": """CREATE TRIGGER recovery_event_provenance_require_canonical_insert
        BEFORE INSERT ON execution_events
        WHEN NEW.event_type='task.requeued' AND NEW.entity_type='task'
         AND NOT EXISTS (
             SELECT 1 FROM task_recovery_records record
             WHERE record.event_id=NEW.id
               AND CAST(record.task_id AS TEXT)=NEW.entity_id
               AND record.recovered_at=NEW.created_at
               AND record.recovered_at=NEW.available_at
               AND NEW.priority=10
               AND NEW.payload=assurance_recovery_payload(
                   record.id,record.task_id,record.recovered_at,record.actor,
                   record.reason,record.scope,record.process_dead_proof,
                   record.previous_task_state,record.previous_execution_state,
                   record.new_task_state,record.new_execution_state,
                   record.audit_log_id,record.event_id,record.integrity_signature
               )
         )
        BEGIN SELECT RAISE(ABORT, 'recovery event provenance must be one-to-one'); END""",
    "task_recovery_provenance_immutable_update": """CREATE TRIGGER task_recovery_provenance_immutable_update
        BEFORE UPDATE ON task_recovery_provenance
        BEGIN SELECT RAISE(ABORT, 'task recovery provenance is immutable'); END""",
    "task_recovery_provenance_immutable_delete": """CREATE TRIGGER task_recovery_provenance_immutable_delete
        BEFORE DELETE ON task_recovery_provenance
        BEGIN SELECT RAISE(ABORT, 'task recovery provenance is immutable'); END""",
    "task_recovery_provenance_require_canonical_insert": """CREATE TRIGGER task_recovery_provenance_require_canonical_insert
        BEFORE INSERT ON task_recovery_provenance
        WHEN assurance_recovery_provenance_signature_valid(
            NEW.recovery_record_id,NEW.task_id,NEW.audit_log_id,NEW.event_id,
            NEW.record_integrity_signature,NEW.event_priority,NEW.integrity_signature
        ) != 1
        BEGIN SELECT RAISE(ABORT, 'task recovery provenance integrity signature required'); END""",
}

RECOVERY_SCHEMA_COLUMNS = {
    "id": ("INTEGER", 0, 1),
    "task_id": ("INTEGER", 1, 0),
    "recovered_at": ("TEXT", 1, 0),
    "actor": ("TEXT", 1, 0),
    "reason": ("TEXT", 1, 0),
    "scope": ("TEXT", 1, 0),
    "process_dead_proof": ("TEXT", 1, 0),
    "previous_task_state": ("TEXT", 1, 0),
    "previous_execution_state": ("TEXT", 1, 0),
    "new_task_state": ("TEXT", 1, 0),
    "new_execution_state": ("TEXT", 1, 0),
    "audit_log_id": ("INTEGER", 0, 0),
    "event_id": ("INTEGER", 0, 0),
    "integrity_signature": ("TEXT", 1, 0),
}
RECOVERY_SCHEMA_COLUMN_ORDER = tuple(RECOVERY_SCHEMA_COLUMNS)
RECOVERY_PROVENANCE_SCHEMA_COLUMNS = {
    "recovery_record_id": ("INTEGER", 0, 1),
    "task_id": ("INTEGER", 1, 0),
    "audit_log_id": ("INTEGER", 1, 0),
    "event_id": ("INTEGER", 1, 0),
    "record_integrity_signature": ("TEXT", 1, 0),
    "event_priority": ("INTEGER", 1, 0),
    "integrity_signature": ("TEXT", 1, 0),
}
RECOVERY_PROVENANCE_TABLE_SQL = """CREATE TABLE task_recovery_provenance (
                    recovery_record_id INTEGER PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    audit_log_id INTEGER NOT NULL UNIQUE,
                    event_id INTEGER NOT NULL UNIQUE,
                    record_integrity_signature TEXT NOT NULL,
                    event_priority INTEGER NOT NULL CHECK(event_priority=10),
                    integrity_signature TEXT NOT NULL
                )"""


def _unique_constraint_sets(
    conn: sqlite3.Connection, table: str,
) -> list[tuple[str, ...]] | None:
    constraints: list[tuple[str, ...]] = []
    for index in conn.execute(f"PRAGMA index_list('{table}')").fetchall():
        if not int(index[2]):
            continue
        if str(index[3]).lower() != "u" or int(index[4]) != 0:
            return None
        xinfo = conn.execute(f"PRAGMA index_xinfo('{index[1]}')").fetchall()
        key_rows = [row for row in xinfo if int(row[5]) == 1]
        if any(
            row[2] is None or int(row[3]) != 0 or str(row[4]).upper() != "BINARY"
            for row in key_rows
        ):
            return None
        constraints.append(tuple(str(row[2]) for row in key_rows))
    return constraints


def recovery_schema_status(conn: sqlite3.Connection) -> str:
    """Return canonical, recognized-legacy, invalid, or missing recovery schema."""
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='task_recovery_records'"
    ).fetchone()
    if table is None:
        return "missing"
    table_sql = _normalized_sql(table["sql"] or "")
    if "id integer primary key autoincrement" not in table_sql:
        return "invalid"
    info = conn.execute("PRAGMA table_info(task_recovery_records)").fetchall()
    if tuple(row[1] for row in info) != RECOVERY_SCHEMA_COLUMN_ORDER:
        return "invalid"
    shape = {
        row[1]: (str(row[2]).upper(), int(row[3]), int(row[5]))
        for row in info
    }
    if shape != RECOVERY_SCHEMA_COLUMNS:
        return "invalid"
    foreign_keys = conn.execute("PRAGMA foreign_key_list(task_recovery_records)").fetchall()
    if not any(
        row[2] == "tasks" and row[3] == "task_id" and row[4] == "id"
        and str(row[6]).upper() == "RESTRICT"
        for row in foreign_keys
    ):
        return "invalid"
    unique_sets = _unique_constraint_sets(conn, "task_recovery_records")
    if unique_sets == [("task_id", "previous_task_state")]:
        return "canonical"
    if unique_sets == [("task_id", "previous_execution_state")]:
        return "legacy"
    return "invalid"


def recovery_provenance_schema_status(conn: sqlite3.Connection) -> str:
    table = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='task_recovery_provenance'"
    ).fetchone()
    if table is None:
        return "missing"
    table_sql = _normalized_sql(table["sql"] or "")
    if table_sql != _normalized_sql(RECOVERY_PROVENANCE_TABLE_SQL):
        return "invalid"
    info = conn.execute("PRAGMA table_info(task_recovery_provenance)").fetchall()
    if tuple(row[1] for row in info) != tuple(RECOVERY_PROVENANCE_SCHEMA_COLUMNS):
        return "invalid"
    shape = {
        row[1]: (str(row[2]).upper(), int(row[3]), int(row[5]))
        for row in info
    }
    if shape != RECOVERY_PROVENANCE_SCHEMA_COLUMNS:
        return "invalid"
    if conn.execute(
        "PRAGMA foreign_key_list(task_recovery_provenance)"
    ).fetchone() is not None:
        return "invalid"
    unique_sets = _unique_constraint_sets(conn, "task_recovery_provenance")
    if unique_sets is None or sorted(unique_sets) != [("audit_log_id",), ("event_id",)]:
        return "invalid"
    return "canonical"


def migrate_legacy_recovery_schema(conn: sqlite3.Connection) -> None:
    """Migrate only the known d0 recovery layout; reject all other replacements."""
    if recovery_schema_status(conn) != "legacy":
        return
    # SQLite rewrites trigger references during RENAME; remove governed
    # triggers first and let Store.init recreate their canonical definitions.
    for name in RECOVERY_TRIGGER_DEFINITIONS:
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    conn.execute("ALTER TABLE task_recovery_records RENAME TO task_recovery_records_legacy")
    Store.execute_script(
        conn,
        """CREATE TABLE task_recovery_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            recovered_at TEXT NOT NULL,
            actor TEXT NOT NULL,
            reason TEXT NOT NULL,
            scope TEXT NOT NULL,
            process_dead_proof TEXT NOT NULL,
            previous_task_state TEXT NOT NULL,
            previous_execution_state TEXT NOT NULL,
            new_task_state TEXT NOT NULL,
            new_execution_state TEXT NOT NULL,
            audit_log_id INTEGER,
            event_id INTEGER,
            integrity_signature TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE RESTRICT,
            UNIQUE(task_id, previous_task_state)
        );
        INSERT INTO task_recovery_records
            (id,task_id,recovered_at,actor,reason,scope,process_dead_proof,
             previous_task_state,previous_execution_state,new_task_state,
             new_execution_state,audit_log_id,event_id,integrity_signature)
        SELECT id,task_id,recovered_at,actor,reason,scope,process_dead_proof,
               previous_task_state,previous_execution_state,new_task_state,
               new_execution_state,audit_log_id,event_id,integrity_signature
        FROM task_recovery_records_legacy;
        DROP TABLE task_recovery_records_legacy;"""
    )


def _normalized_sql(sql: str) -> str:
    return " ".join(sql.lower().split())


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class StoreConnection(sqlite3.Connection):
    """Commit or roll back and then release UDF closures at context exit."""

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        suppressed = False
        try:
            suppressed = bool(super().__exit__(exc_type, exc, traceback))
        finally:
            self.close()
        return suppressed


class Store:
    def __init__(self, db_path: Path, *, workspace: Path | None = None):
        self.db_path = db_path
        self.workspace = workspace or db_path.parent.parent
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, factory=StoreConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        self._register_assurance_sql_functions(conn)
        return conn

    def connect_readonly(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            f"file:{self.db_path}?mode=ro", uri=True, factory=StoreConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        self._register_assurance_sql_functions(conn)
        return conn

    @staticmethod
    def execute_script(conn: sqlite3.Connection, script: str) -> None:
        """Execute a SQL script without sqlite3.executescript's implicit commit."""
        statement = ""
        for character in script:
            statement += character
            if character == ";" and sqlite3.complete_statement(statement):
                conn.execute(statement)
                statement = ""
        if statement.strip():
            conn.execute(statement)

    def _register_assurance_sql_functions(self, conn: sqlite3.Connection) -> None:
        db_path = self.db_path
        workspace = self.workspace

        def sha256_text(value: object) -> str:
            return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

        def artifact_body_valid(content_json: object, declared_sha256: object) -> int:
            try:
                canonical = json.dumps(
                    json.loads(str(content_json)), ensure_ascii=True,
                    sort_keys=True, separators=(",", ":"),
                )
            except (TypeError, ValueError):
                return 0
            return int(
                hashlib.sha256(canonical.encode("ascii")).hexdigest()
                == str(declared_sha256)
            )

        def completion_signature_valid(*values: object) -> int:
            from .integrity import verify

            if len(values) != 12:
                return 0
            keys = (
                "task_id", "generation", "initiative_id", "artifact_set_sha256",
                "trusted_eval_result_sha256", "review_decision_ref",
                "review_content_sha256", "task_result_sha256",
                "evidence_paths_sha256", "completed_at", "created_at",
            )
            payload = dict(zip(keys, values[:-1], strict=True))
            return int(verify(db_path, "completion-binding", payload, str(values[-1])))

        def completion_binding_semantics_valid(*values: object) -> int:
            from .completion_verifier import (
                COMPLETION_SIGNATURE_KEYS,
                completion_binding_valid,
            )

            if len(values) != 14:
                return 0
            completion = dict(zip(
                (
                    *COMPLETION_SIGNATURE_KEYS,
                    "task_result_json", "evidence_paths_json", "integrity_signature",
                ),
                values,
                strict=True,
            ))
            try:
                return int(completion_binding_valid(
                    conn, db_path, workspace, completion, stage="insert",
                ))
            except Exception:
                return 0

        def persisted_completion_valid(
            task_id: object, task_result: object, task_updated_at: object,
        ) -> int:
            from .completion_verifier import completion_binding_valid

            try:
                completion = conn.execute(
                    "SELECT * FROM assurance_completion_bindings WHERE task_id=?",
                    (task_id,),
                ).fetchone()
                return int(completion_binding_valid(
                    conn, db_path, workspace, completion, stage="task_update",
                    task_result_override=str(task_result),
                    task_updated_at_override=str(task_updated_at),
                ))
            except Exception:
                return 0

        def reconciliation_signature_valid(*values: object) -> int:
            from .integrity import verify

            if len(values) != 12:
                return 0
            keys = (
                "task_id", "reconciled_at", "actor", "accepted_source_commit",
                "accepted_source_tree", "evidence_tip_commit", "evidence_tip_tree",
                "independent_verdict", "reason", "previous_task_state",
                "previous_execution_state",
            )
            payload = dict(zip(keys, values[:-1], strict=True))
            return int(verify(db_path, "task-reconciliation", payload, str(values[-1])))

        def recovery_signature_valid(*values: object) -> int:
            from .integrity import verify

            if len(values) != 14:
                return 0
            keys = (
                "id", "task_id", "recovered_at", "actor", "reason", "scope",
                "process_dead_proof", "previous_task_state", "previous_execution_state",
                "new_task_state", "new_execution_state", "audit_log_id", "event_id",
            )
            payload = dict(zip(keys, values[:-1], strict=True))
            return int(verify(db_path, "task-exhausted-recovery", payload, str(values[-1])))

        def recovery_payload(*values: object) -> str:
            from .integrity import recovery_provenance_payload

            if len(values) != 14:
                return ""
            keys = (
                "id", "task_id", "recovered_at", "actor", "reason", "scope",
                "process_dead_proof", "previous_task_state", "previous_execution_state",
                "new_task_state", "new_execution_state", "audit_log_id", "event_id",
                "integrity_signature",
            )
            payload = recovery_provenance_payload(dict(zip(keys, values, strict=True)))
            return json.dumps(payload, sort_keys=True)

        def recovery_provenance_signature_valid(*values: object) -> int:
            from .integrity import verify

            if len(values) != 7:
                return 0
            keys = (
                "recovery_record_id", "task_id", "audit_log_id", "event_id",
                "record_integrity_signature", "event_priority",
            )
            payload = dict(zip(keys, values[:-1], strict=True))
            return int(verify(
                db_path, "task-recovery-provenance", payload, str(values[-1]),
            ))

        def approval_binding_signature_valid(*values: object) -> int:
            from .integrity import verify
            if len(values) != 7:
                return 0
            keys = ("created_at", "requested_by", "action_type", "summary", "target_task_id", "target_action")
            return int(verify(db_path, "chairman-approval-binding", dict(zip(keys, values[:-1], strict=True)), str(values[-1])))

        def approval_decision_signature_valid(*values: object) -> int:
            from .integrity import verify
            if len(values) != 13:
                return 0
            keys = (
                "id", "created_at", "requested_by", "action_type", "summary",
                "target_task_id", "target_action", "status", "decision", "rationale",
                "decided_at", "decided_by",
            )
            return int(verify(db_path, "chairman-approval-decision", dict(zip(keys, values[:-1], strict=True)), str(values[-1])))

        class ArtifactSetSha256:
            def __init__(self) -> None:
                self.artifacts: list[dict[str, str]] = []

            def step(self, reference: object, digest: object) -> None:
                self.artifacts.append({"ref": str(reference), "sha256": str(digest)})

            def finalize(self) -> str:
                canonical = json.dumps(
                    self.artifacts, ensure_ascii=True, sort_keys=True,
                    separators=(",", ":"),
                )
                return hashlib.sha256(canonical.encode("ascii")).hexdigest()

        conn.create_function("assurance_sha256", 1, sha256_text, deterministic=True)
        conn.create_function(
            "assurance_artifact_body_valid", 2, artifact_body_valid,
            deterministic=True,
        )
        conn.create_function(
            "assurance_completion_signature_valid", 12,
            completion_signature_valid, deterministic=True,
        )
        conn.create_function(
            "assurance_completion_binding_semantics_valid", 14,
            completion_binding_semantics_valid,
        )
        conn.create_function(
            "assurance_persisted_completion_valid", 3,
            persisted_completion_valid,
        )
        conn.create_function(
            "assurance_reconciliation_signature_valid", 12,
            reconciliation_signature_valid, deterministic=True,
        )
        conn.create_function(
            "assurance_recovery_signature_valid", 14,
            recovery_signature_valid, deterministic=True,
        )
        conn.create_function(
            "assurance_recovery_payload", 14,
            recovery_payload, deterministic=True,
        )
        conn.create_function(
            "assurance_recovery_provenance_signature_valid", 7,
            recovery_provenance_signature_valid, deterministic=True,
        )
        conn.create_function(
            "assurance_approval_binding_signature_valid", 7,
            approval_binding_signature_valid, deterministic=True,
        )
        conn.create_function(
            "assurance_approval_decision_signature_valid", 13,
            approval_decision_signature_valid, deterministic=True,
        )
        conn.create_aggregate(
            "assurance_artifact_set_sha256", 2, ArtifactSetSha256,
        )

    def init_assurance(self) -> None:
        """Add shadow assurance tables without touching operational schema or data."""
        from .integrity import ensure_key

        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_key(self.db_path)
            operational = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if {"tasks", "task_executions"} <= operational:
                from .ops import recovery_conflicts

                recovery_issues = recovery_conflicts(conn, self.db_path)
                if recovery_issues:
                    raise sqlite3.IntegrityError(
                        "governed recovery history/provenance schema is not canonical"
                    )
            principal_columns = {
                row[1] for row in conn.execute("PRAGMA table_info(assurance_principals)")
            } if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='assurance_principals'"
            ).fetchone() else set()
            if principal_columns and "credential_sha256" not in principal_columns:
                conn.execute("ALTER TABLE assurance_principals ADD COLUMN credential_sha256 TEXT")
            self.execute_script(
                conn,
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    entity_id TEXT,
                    details TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_principals (
                    principal_id TEXT PRIMARY KEY,
                    actor TEXT NOT NULL UNIQUE,
                    authority TEXT NOT NULL,
                    credential_sha256 TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_initiatives (
                    initiative_id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    title TEXT NOT NULL,
                    owner_principal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'shadow',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL,
                    initiative_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    owner_principal TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    approved_by_principal TEXT,
                    approved_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(artifact_id, version)
                );
                CREATE TABLE IF NOT EXISTS assurance_artifact_registrations (
                    artifact_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    integrity_signature TEXT,
                    PRIMARY KEY(artifact_id, version)
                );
                CREATE TABLE IF NOT EXISTS assurance_artifact_approvals (
                    artifact_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    approved_by_principal TEXT NOT NULL,
                    approved_at TEXT NOT NULL,
                    integrity_signature TEXT NOT NULL,
                    PRIMARY KEY(artifact_id, version)
                );
                CREATE TABLE IF NOT EXISTS assurance_artifact_lifecycle (
                    artifact_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    transitioned_at TEXT NOT NULL,
                    actor_principal TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    previous_signature TEXT,
                    integrity_signature TEXT NOT NULL,
                    PRIMARY KEY(artifact_id, version, sequence)
                );
                CREATE TABLE IF NOT EXISTS assurance_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiative_id TEXT NOT NULL,
                    from_artifact_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    to_artifact_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(initiative_id, from_artifact_id, relation, to_artifact_id)
                );
                CREATE TABLE IF NOT EXISTS assurance_gate_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiative_id TEXT NOT NULL,
                    gate TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    artifact_set_sha256 TEXT NOT NULL,
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    expires_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_blocks (
                    initiative_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    resume_state TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_classifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    indicators_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'shadow',
                    created_at TEXT NOT NULL
                );
                """
            )
            registration_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(assurance_artifact_registrations)"
                )
            }
            if "integrity_signature" not in registration_columns:
                conn.execute(
                    "ALTER TABLE assurance_artifact_registrations "
                    "ADD COLUMN integrity_signature TEXT"
                )

            self.execute_script(
                conn,
                """
                DROP TRIGGER IF EXISTS assurance_artifact_registrations_immutable_update;
                DROP TRIGGER IF EXISTS assurance_artifact_registrations_immutable_delete;
                DROP TRIGGER IF EXISTS assurance_artifact_approvals_immutable_update;
                DROP TRIGGER IF EXISTS assurance_artifact_approvals_immutable_delete;
                DROP TRIGGER IF EXISTS assurance_artifact_lifecycle_immutable_update;
                DROP TRIGGER IF EXISTS assurance_artifact_lifecycle_immutable_delete;
                DROP TRIGGER IF EXISTS assurance_artifact_status_transition_guard;
                CREATE TRIGGER assurance_artifact_registrations_immutable_update
                    BEFORE UPDATE ON assurance_artifact_registrations
                    BEGIN SELECT RAISE(ABORT, 'assurance artifact registration is immutable'); END;
                CREATE TRIGGER assurance_artifact_registrations_immutable_delete
                    BEFORE DELETE ON assurance_artifact_registrations
                    BEGIN SELECT RAISE(ABORT, 'assurance artifact registration is immutable'); END;
                CREATE TRIGGER assurance_artifact_approvals_immutable_update
                    BEFORE UPDATE ON assurance_artifact_approvals
                    BEGIN SELECT RAISE(ABORT, 'assurance artifact approval is immutable'); END;
                CREATE TRIGGER assurance_artifact_approvals_immutable_delete
                    BEFORE DELETE ON assurance_artifact_approvals
                    BEGIN SELECT RAISE(ABORT, 'assurance artifact approval is immutable'); END;
                CREATE TRIGGER assurance_artifact_lifecycle_immutable_update
                    BEFORE UPDATE ON assurance_artifact_lifecycle
                    BEGIN SELECT RAISE(ABORT, 'assurance artifact lifecycle is immutable'); END;
                CREATE TRIGGER assurance_artifact_lifecycle_immutable_delete
                    BEFORE DELETE ON assurance_artifact_lifecycle
                    BEGIN SELECT RAISE(ABORT, 'assurance artifact lifecycle is immutable'); END;
                CREATE TRIGGER assurance_artifact_status_transition_guard
                    BEFORE UPDATE OF status ON assurance_artifacts
                    WHEN NEW.status IS NOT OLD.status
                    AND NOT EXISTS (
                        SELECT 1 FROM assurance_artifact_lifecycle lifecycle
                        WHERE lifecycle.artifact_id=OLD.artifact_id
                          AND lifecycle.version=OLD.version
                          AND lifecycle.sequence=(
                              SELECT MAX(latest.sequence)
                              FROM assurance_artifact_lifecycle latest
                              WHERE latest.artifact_id=OLD.artifact_id
                                AND latest.version=OLD.version
                          )
                          AND lifecycle.from_status IS OLD.status
                          AND lifecycle.to_status IS NEW.status
                    )
                    BEGIN SELECT RAISE(ABORT, 'artifact lifecycle transition is not anchored'); END;
                """
            )

    def init(self) -> None:
        from .integrity import ensure_recovery_ledger
        ensure_recovery_ledger(self.db_path)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            recovery_table_present_before = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_recovery_records'"
            ).fetchone() is not None
            recovery_provenance_present_before = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_recovery_provenance'"
            ).fetchone() is not None
            operational_schema_present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks'"
            ).fetchone() is not None
            governed_execution_present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_executions'"
            ).fetchone() is not None
            if operational_schema_present and governed_execution_present and not recovery_table_present_before:
                raise sqlite3.IntegrityError(
                    "governed recovery history schema is missing; refusing initialization"
                )
            if recovery_table_present_before:
                recovery_status = recovery_schema_status(conn)
                if recovery_status == "legacy":
                    migrate_legacy_recovery_schema(conn)
                elif recovery_status != "canonical":
                    raise sqlite3.IntegrityError(
                        "governed recovery history schema is not canonical"
                    )
                if recovery_provenance_present_before:
                    if recovery_provenance_schema_status(conn) != "canonical":
                        raise sqlite3.IntegrityError(
                            "governed recovery provenance schema is not canonical"
                        )
                elif conn.execute(
                    "SELECT 1 FROM task_recovery_records LIMIT 1"
                ).fetchone() is not None:
                    raise sqlite3.IntegrityError(
                        "governed recovery provenance is missing for existing history"
                    )
            # Older databases need this column before triggers referencing it are parsed.
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_events'"
            ).fetchone():
                event_columns = {row[1] for row in conn.execute("PRAGMA table_info(execution_events)")}
                if "priority" not in event_columns:
                    conn.execute("ALTER TABLE execution_events ADD COLUMN priority INTEGER NOT NULL DEFAULT 10")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='approvals'").fetchone():
                approval_columns = {row[1] for row in conn.execute("PRAGMA table_info(approvals)")}
                for name, definition in (
                    ("target_task_id", "INTEGER"), ("target_action", "TEXT"),
                    ("integrity_signature", "TEXT"), ("decided_by", "TEXT"),
                    ("decision_integrity_signature", "TEXT"),
                ):
                    if name not in approval_columns:
                        conn.execute(f"ALTER TABLE approvals ADD COLUMN {name} {definition}")
            # Add recovery provenance columns before parsing triggers on legacy stores.
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_recovery_records'"
            ).fetchone():
                recovery_columns = {row[1] for row in conn.execute("PRAGMA table_info(task_recovery_records)")}
                for name, definition in {
                    "task_id": "INTEGER", "recovered_at": "TEXT", "actor": "TEXT",
                    "reason": "TEXT", "scope": "TEXT", "process_dead_proof": "TEXT",
                    "previous_task_state": "TEXT", "previous_execution_state": "TEXT",
                    "new_task_state": "TEXT", "new_execution_state": "TEXT",
                    "audit_log_id": "INTEGER", "event_id": "INTEGER",
                    "integrity_signature": "TEXT",
                }.items():
                    if name not in recovery_columns:
                        conn.execute(f"ALTER TABLE task_recovery_records ADD COLUMN {name} {definition}")
            self.execute_script(
                conn,
                """
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity TEXT NOT NULL,
                    entity_id TEXT,
                    details TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS roles (
                    name TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    mandate TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'resident'
                );
                CREATE TABLE IF NOT EXISTS raci (
                    domain TEXT PRIMARY KEY,
                    responsible TEXT NOT NULL,
                    accountable TEXT NOT NULL,
                    consulted TEXT NOT NULL,
                    informed TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    title TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    status TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    blocked_reason TEXT,
                    result TEXT,
                    acceptance_criteria TEXT
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    decided_at TEXT,
                    requested_by TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT,
                    rationale TEXT,
                    inbox_file TEXT,
                    outbox_file TEXT,
                    target_task_id INTEGER,
                    target_action TEXT,
                    integrity_signature TEXT,
                    decided_by TEXT,
                    decision_integrity_signature TEXT
                );
                DROP TRIGGER IF EXISTS approvals_immutable_binding_update;
                DROP TRIGGER IF EXISTS approvals_require_canonical_binding_insert;
                DROP TRIGGER IF EXISTS approvals_require_canonical_decision_update;
                DROP TRIGGER IF EXISTS approvals_immutable_decision_update;
                DROP TRIGGER IF EXISTS approvals_immutable_signed_delete;
                CREATE TRIGGER IF NOT EXISTS approvals_require_canonical_binding_insert
                    BEFORE INSERT ON approvals
                    WHEN (NEW.target_task_id IS NOT NULL OR NEW.target_action IS NOT NULL
                          OR NEW.integrity_signature IS NOT NULL)
                     AND assurance_approval_binding_signature_valid(
                         NEW.created_at,NEW.requested_by,NEW.action_type,NEW.summary,
                         NEW.target_task_id,NEW.target_action,NEW.integrity_signature
                     ) != 1
                    BEGIN SELECT RAISE(ABORT, 'chairman approval binding integrity required'); END;
                CREATE TRIGGER IF NOT EXISTS approvals_immutable_binding_update
                    BEFORE UPDATE ON approvals
                    WHEN OLD.integrity_signature IS NOT NULL
                     AND (NEW.created_at IS NOT OLD.created_at
                          OR NEW.requested_by IS NOT OLD.requested_by
                          OR NEW.action_type IS NOT OLD.action_type
                          OR NEW.summary IS NOT OLD.summary
                          OR NEW.target_task_id IS NOT OLD.target_task_id
                          OR NEW.target_action IS NOT OLD.target_action
                          OR NEW.integrity_signature IS NOT OLD.integrity_signature)
                    BEGIN SELECT RAISE(ABORT, 'chairman approval binding is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS approvals_require_canonical_decision_update
                    BEFORE UPDATE OF status,decision,rationale,decided_at,decided_by,decision_integrity_signature ON approvals
                    WHEN NEW.status IN ('approved','denied')
                     AND assurance_approval_decision_signature_valid(
                         NEW.id,NEW.created_at,NEW.requested_by,NEW.action_type,NEW.summary,
                         NEW.target_task_id,NEW.target_action,NEW.status,NEW.decision,
                         NEW.rationale,NEW.decided_at,NEW.decided_by,
                         NEW.decision_integrity_signature
                     ) != 1
                    BEGIN SELECT RAISE(ABORT, 'chairman approval decision integrity required'); END;
                CREATE TRIGGER IF NOT EXISTS approvals_immutable_decision_update
                    BEFORE UPDATE ON approvals
                    WHEN OLD.decision_integrity_signature IS NOT NULL
                    BEGIN SELECT RAISE(ABORT, 'chairman approval decision is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS approvals_immutable_signed_delete
                    BEFORE DELETE ON approvals
                    WHEN OLD.integrity_signature IS NOT NULL
                    BEGIN SELECT RAISE(ABORT, 'signed chairman approval is immutable'); END;
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    name TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT NOT NULL,
                    source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS experiments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    name TEXT NOT NULL,
                    hypothesis TEXT NOT NULL,
                    metric TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result TEXT
                );
                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    summary TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS executors (
                    executor_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    capacity INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    process_id INTEGER,
                    process_started_at TEXT,
                    session_ref TEXT,
                    heartbeat_at TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_executions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    executor_id TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    process_id INTEGER,
                    process_started_at TEXT,
                    session_ref TEXT,
                    claimed_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    lease_expires_at TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    checkpoint TEXT,
                    next_action TEXT,
                    evidence_paths TEXT NOT NULL DEFAULT '[]',
                    log_paths TEXT NOT NULL DEFAULT '[]',
                    last_error TEXT,
                    recovery_status TEXT NOT NULL,
                    fencing_token TEXT,
                    generation INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    UNIQUE(task_id)
                );
                CREATE TABLE IF NOT EXISTS task_reconciliations (
                    task_id INTEGER PRIMARY KEY,
                    reconciled_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    accepted_source_commit TEXT NOT NULL,
                    accepted_source_tree TEXT NOT NULL,
                    evidence_tip_commit TEXT NOT NULL,
                    evidence_tip_tree TEXT NOT NULL,
                    independent_verdict TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    previous_task_state TEXT NOT NULL,
                    previous_execution_state TEXT NOT NULL,
                    integrity_signature TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE RESTRICT
                );
                CREATE TRIGGER IF NOT EXISTS task_reconciliations_immutable_update
                    BEFORE UPDATE ON task_reconciliations
                    BEGIN SELECT RAISE(ABORT, 'task reconciliation is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS task_reconciliations_immutable_delete
                    BEFORE DELETE ON task_reconciliations
                    BEGIN SELECT RAISE(ABORT, 'task reconciliation is immutable'); END;
                CREATE TABLE IF NOT EXISTS task_recovery_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    recovered_at TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    process_dead_proof TEXT NOT NULL,
                    previous_task_state TEXT NOT NULL,
                    previous_execution_state TEXT NOT NULL,
                    new_task_state TEXT NOT NULL,
                    new_execution_state TEXT NOT NULL,
                    audit_log_id INTEGER,
                    event_id INTEGER,
                    integrity_signature TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE RESTRICT,
                    UNIQUE(task_id, previous_execution_state)
                );
                CREATE TABLE IF NOT EXISTS task_recovery_provenance (
                    recovery_record_id INTEGER PRIMARY KEY,
                    task_id INTEGER NOT NULL,
                    audit_log_id INTEGER NOT NULL UNIQUE,
                    event_id INTEGER NOT NULL UNIQUE,
                    record_integrity_signature TEXT NOT NULL,
                    event_priority INTEGER NOT NULL CHECK(event_priority=10),
                    integrity_signature TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS task_recovery_records_immutable_update
                    BEFORE UPDATE ON task_recovery_records
                    BEGIN SELECT RAISE(ABORT, 'task recovery record is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS task_recovery_records_immutable_delete
                    BEFORE DELETE ON task_recovery_records
                    BEGIN SELECT RAISE(ABORT, 'task recovery record is immutable'); END;
                CREATE TRIGGER IF NOT EXISTS task_recovery_records_require_canonical_insert
                    BEFORE INSERT ON task_recovery_records
                    WHEN assurance_recovery_signature_valid(
                        NEW.id, NEW.task_id, NEW.recovered_at, NEW.actor, NEW.reason, NEW.scope,
                        NEW.process_dead_proof, NEW.previous_task_state,
                        NEW.previous_execution_state, NEW.new_task_state,
                        NEW.new_execution_state, NEW.audit_log_id, NEW.event_id,
                        NEW.integrity_signature
                    ) != 1
                    BEGIN SELECT RAISE(ABORT, 'task recovery record integrity signature required'); END;
                CREATE TRIGGER IF NOT EXISTS task_recovery_task_identity_immutable
                    BEFORE UPDATE OF owner,title,domain,acceptance_criteria ON tasks
                    WHEN EXISTS (SELECT 1 FROM task_recovery_records WHERE task_id=OLD.id)
                     AND (NEW.owner IS NOT OLD.owner OR NEW.title IS NOT OLD.title
                          OR NEW.domain IS NOT OLD.domain
                          OR NEW.acceptance_criteria IS NOT OLD.acceptance_criteria)
                    BEGIN SELECT RAISE(ABORT, 'task recovery identity is immutable'); END;
                CREATE TABLE IF NOT EXISTS task_contexts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    generation INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    company_context_version TEXT NOT NULL,
                    role_context_version TEXT NOT NULL,
                    directive_version INTEGER NOT NULL,
                    strategy_version INTEGER NOT NULL,
                    bundle_sha256 TEXT NOT NULL,
                    fencing_token TEXT,
                    bundle_path TEXT,
                    source_versions_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    superseded_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    UNIQUE(task_id, generation)
                );
                CREATE TABLE IF NOT EXISTS role_continuity (
                    role TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    verified_facts_json TEXT NOT NULL DEFAULT '[]',
                    open_items_json TEXT NOT NULL DEFAULT '[]',
                    source_task_id INTEGER,
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS project_history (
                    repository_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    decisions_json TEXT NOT NULL DEFAULT '[]',
                    known_limits_json TEXT NOT NULL DEFAULT '[]',
                    version INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS handoffs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    from_role TEXT NOT NULL,
                    to_role TEXT NOT NULL,
                    handoff_type TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL DEFAULT '[]',
                    decision_needed TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    accepted_at TEXT,
                    closed_at TEXT,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS handoffs_role_status
                    ON handoffs(to_role, status, id);
                CREATE TABLE IF NOT EXISTS assurance_initiatives (
                    initiative_id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    title TEXT NOT NULL,
                    owner_principal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'shadow',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_artifacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    artifact_id TEXT NOT NULL,
                    initiative_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    owner_principal TEXT NOT NULL,
                    repository_id TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    approved_by_principal TEXT,
                    approved_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(artifact_id, version)
                );
                CREATE TABLE IF NOT EXISTS assurance_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiative_id TEXT NOT NULL,
                    from_artifact_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    to_artifact_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(initiative_id, from_artifact_id, relation, to_artifact_id)
                );
                CREATE TABLE IF NOT EXISTS assurance_gate_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    initiative_id TEXT NOT NULL,
                    gate TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    artifact_set_sha256 TEXT NOT NULL,
                    conditions_json TEXT NOT NULL DEFAULT '[]',
                    expires_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_blocks (
                    initiative_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    resume_state TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_classifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    risk_class TEXT NOT NULL,
                    indicators_json TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT 'shadow',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS strategic_phases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    phase_key TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    success_metrics TEXT NOT NULL,
                    deadline TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    evidence_requirements TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    activated_at TEXT,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    agent TEXT NOT NULL,
                    task_id INTEGER,
                    execution_id INTEGER,
                    session TEXT,
                    model TEXT,
                    provider TEXT,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cache_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    total_tokens INTEGER NOT NULL,
                    cost REAL,
                    currency TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(agent) REFERENCES roles(name),
                    FOREIGN KEY(task_id) REFERENCES tasks(id),
                    FOREIGN KEY(execution_id) REFERENCES task_executions(id)
                );
                CREATE TABLE IF NOT EXISTS execution_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    claimed_at TEXT,
                    processed_at TEXT,
                    worker_id TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    priority INTEGER NOT NULL DEFAULT 10
                );
                CREATE INDEX IF NOT EXISTS execution_events_pending
                    ON execution_events(status, available_at, id);
                CREATE INDEX IF NOT EXISTS execution_events_priority_pending
                    ON execution_events(status, priority DESC, available_at, id);
                CREATE TABLE IF NOT EXISTS event_worker_state (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    status TEXT NOT NULL,
                    worker_id TEXT,
                    process_id INTEGER,
                    started_at TEXT,
                    heartbeat_at TEXT,
                    stopped_at TEXT,
                    last_error TEXT,
                    events_processed INTEGER NOT NULL DEFAULT 0
                );
                INSERT OR IGNORE INTO event_worker_state(singleton, status)
                    VALUES (1, 'stopped');
                CREATE TABLE IF NOT EXISTS ceo_runtime_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ceo_state_versions (
                    version INTEGER PRIMARY KEY,
                    schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_id TEXT,
                    strategy_json TEXT NOT NULL,
                    assumptions_json TEXT NOT NULL,
                    critical_path_json TEXT NOT NULL,
                    active_directive_version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chairman_directives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    directive_version INTEGER NOT NULL UNIQUE,
                    schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    source_platform TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    source_message_sha256 TEXT NOT NULL,
                    directive_type TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    UNIQUE(source_platform, source_session_id, source_message_id)
                );
                CREATE TABLE IF NOT EXISTS ceo_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    event_id INTEGER NOT NULL,
                    source_tag TEXT NOT NULL,
                    state_version_read INTEGER NOT NULL,
                    directive_version_read INTEGER NOT NULL,
                    input_snapshot_sha256 TEXT NOT NULL,
                    input_snapshot_json TEXT NOT NULL,
                    model TEXT,
                    provider TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    cache_tokens INTEGER,
                    reasoning_tokens INTEGER,
                    total_tokens INTEGER,
                    judgment TEXT,
                    actions_json TEXT,
                    result_json TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY(event_id) REFERENCES execution_events(id)
                );
                CREATE TABLE IF NOT EXISTS approval_deliveries (
                    approval_id INTEGER PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    available_at TEXT NOT NULL,
                    delivered_at TEXT,
                    delivery_ref TEXT,
                    last_error TEXT,
                    FOREIGN KEY(approval_id) REFERENCES approvals(id)
                );
                CREATE TRIGGER IF NOT EXISTS approvals_enqueue_pending_event
                AFTER INSERT ON approvals
                WHEN NEW.status = 'pending'
                BEGIN
                    INSERT INTO execution_events(
                        created_at, available_at, event_type, entity_type,
                        entity_id, payload, status, priority
                    ) VALUES (
                        NEW.created_at, NEW.created_at, 'approval.pending', 'approval',
                        CAST(NEW.id AS TEXT), '{}', 'pending', 90
                    );
                END;
                CREATE TRIGGER IF NOT EXISTS tasks_enqueue_created_event
                AFTER INSERT ON tasks
                BEGIN
                    INSERT INTO execution_events(
                        created_at, available_at, event_type, entity_type,
                        entity_id, payload, status, priority
                    ) VALUES (
                        NEW.created_at, NEW.created_at, 'task.created', 'task',
                        CAST(NEW.id AS TEXT), '{}', 'pending', 10
                    );
                END;
                """
            )
            reconciliation_columns = {row[1] for row in conn.execute("PRAGMA table_info(task_reconciliations)")}
            if "integrity_signature" not in reconciliation_columns:
                conn.execute("ALTER TABLE task_reconciliations ADD COLUMN integrity_signature TEXT")
            conn.execute(
                """CREATE TRIGGER IF NOT EXISTS task_reconciliations_require_canonical_insert
                   BEFORE INSERT ON task_reconciliations
                   WHEN assurance_reconciliation_signature_valid(
                       NEW.task_id, NEW.reconciled_at, NEW.actor,
                       NEW.accepted_source_commit, NEW.accepted_source_tree,
                       NEW.evidence_tip_commit, NEW.evidence_tip_tree,
                       NEW.independent_verdict, NEW.reason,
                       NEW.previous_task_state, NEW.previous_execution_state,
                       NEW.integrity_signature
                   ) != 1
                   BEGIN SELECT RAISE(ABORT, 'task reconciliation integrity signature required'); END"""
            )
            self.execute_script(
                conn,
                """
                DROP TRIGGER IF EXISTS task_recovery_records_require_canonical_insert;
                CREATE TRIGGER task_recovery_records_require_canonical_insert
                    BEFORE INSERT ON task_recovery_records
                    WHEN assurance_recovery_signature_valid(
                        NEW.id, NEW.task_id, NEW.recovered_at, NEW.actor, NEW.reason, NEW.scope,
                        NEW.process_dead_proof, NEW.previous_task_state,
                        NEW.previous_execution_state, NEW.new_task_state,
                        NEW.new_execution_state, NEW.audit_log_id, NEW.event_id,
                        NEW.integrity_signature
                    ) != 1
                    BEGIN SELECT RAISE(ABORT, 'task recovery record integrity signature required'); END;
                DROP TRIGGER IF EXISTS recovery_audit_provenance_immutable_update;
                DROP TRIGGER IF EXISTS recovery_audit_provenance_immutable_delete;
                DROP TRIGGER IF EXISTS recovery_event_provenance_immutable_update;
                DROP TRIGGER IF EXISTS recovery_event_provenance_immutable_delete;
                CREATE TRIGGER recovery_audit_provenance_immutable_update
                    BEFORE UPDATE OF ts,actor,action,entity,entity_id,details ON audit_log
                    WHEN OLD.action='requeue_exhausted_task' AND OLD.entity='task_execution'
                    BEGIN SELECT RAISE(ABORT, 'recovery audit provenance is immutable'); END;
                CREATE TRIGGER recovery_audit_provenance_immutable_delete
                    BEFORE DELETE ON audit_log
                    WHEN OLD.action='requeue_exhausted_task' AND OLD.entity='task_execution'
                    BEGIN SELECT RAISE(ABORT, 'recovery audit provenance is immutable'); END;
                CREATE TRIGGER recovery_event_provenance_immutable_update
                    BEFORE UPDATE OF created_at,available_at,event_type,entity_type,entity_id,payload ON execution_events
                    WHEN OLD.event_type='task.requeued' AND OLD.entity_type='task'
                    BEGIN SELECT RAISE(ABORT, 'recovery event provenance is immutable'); END;
                CREATE TRIGGER recovery_event_provenance_immutable_delete
                    BEFORE DELETE ON execution_events
                    WHEN OLD.event_type='task.requeued' AND OLD.entity_type='task'
                    BEGIN SELECT RAISE(ABORT, 'recovery event provenance is immutable'); END;
                """
            )
            task_columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
            if "acceptance_criteria" not in task_columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN acceptance_criteria TEXT")
            if "strategic_phase_id" not in task_columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN strategic_phase_id INTEGER REFERENCES strategic_phases(id)")
            if "business_outcome" not in task_columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN business_outcome TEXT")
            role_columns = {row[1] for row in conn.execute("PRAGMA table_info(roles)")}
            if "status" not in role_columns:
                conn.execute("ALTER TABLE roles ADD COLUMN status TEXT NOT NULL DEFAULT 'resident'")
            execution_columns = {row[1] for row in conn.execute("PRAGMA table_info(task_executions)")}
            if "fencing_token" not in execution_columns:
                conn.execute("ALTER TABLE task_executions ADD COLUMN fencing_token TEXT")
            if "generation" not in execution_columns:
                conn.execute("ALTER TABLE task_executions ADD COLUMN generation INTEGER NOT NULL DEFAULT 0")
            context_columns = {row[1] for row in conn.execute("PRAGMA table_info(task_contexts)")}
            if "fencing_token" not in context_columns:
                conn.execute("ALTER TABLE task_contexts ADD COLUMN fencing_token TEXT")
            event_columns = {row[1] for row in conn.execute("PRAGMA table_info(execution_events)")}
            if "priority" not in event_columns:
                conn.execute("ALTER TABLE execution_events ADD COLUMN priority INTEGER NOT NULL DEFAULT 10")
            # The bootstrap DDL above preserves the historical layout for
            # compatibility; immediately normalize the one recognized legacy
            # uniqueness key before any governed consumer validates it.
            if recovery_schema_status(conn) == "legacy":
                migrate_legacy_recovery_schema(conn)
            elif recovery_schema_status(conn) != "canonical":
                raise sqlite3.IntegrityError(
                    "governed recovery history schema is not canonical"
                )
            if recovery_provenance_schema_status(conn) != "canonical":
                raise sqlite3.IntegrityError(
                    "governed recovery provenance schema is not canonical"
                )
            # CREATE TRIGGER IF NOT EXISTS is intentionally not used here:
            # trigger names are not a trust boundary when an existing trigger
            # can have been replaced with a permissive definition.
            self._repair_recovery_triggers(conn)
            conn.execute(
                "INSERT OR IGNORE INTO ceo_runtime_migrations(version, applied_at) VALUES (?, ?)",
                (CEO_RUNTIME_SCHEMA_VERSION, utcnow()),
            )
            if conn.execute("SELECT 1 FROM ceo_state_versions LIMIT 1").fetchone() is None:
                conn.execute(
                    """INSERT INTO ceo_state_versions(
                           version, schema_version, created_at, source_kind, source_id,
                           strategy_json, assumptions_json, critical_path_json,
                           active_directive_version
                       ) VALUES (1, 'ceo-state/v1', ?, 'migration', ?, '{}', '[]', '[]', 0)""",
                    (utcnow(), CEO_RUNTIME_SCHEMA_VERSION),
                )
            self._seed(conn)
            conn.execute(
                """INSERT INTO execution_events(
                       created_at, available_at, event_type, entity_type,
                       entity_id, payload, status
                   )
                   SELECT tasks.updated_at, tasks.updated_at, 'task.created', 'task',
                          CAST(tasks.id AS TEXT), '{"migration_backfill": true}', 'pending'
                   FROM tasks
                   WHERE tasks.status IN ('open', 'in_progress', 'blocked')
                     AND NOT EXISTS (
                         SELECT 1 FROM execution_events
                         WHERE execution_events.entity_type='task'
                           AND execution_events.entity_id=CAST(tasks.id AS TEXT)
                     )"""
            )
            conn.execute(
                """INSERT INTO execution_events(
                       created_at, available_at, event_type, entity_type,
                       entity_id, payload, status, priority
                   )
                   SELECT approvals.created_at, approvals.created_at,
                          'approval.pending', 'approval', CAST(approvals.id AS TEXT),
                          '{"migration_backfill": true}', 'pending', 90
                   FROM approvals
                   WHERE approvals.status='pending'
                     AND NOT EXISTS (
                         SELECT 1 FROM approval_deliveries
                         WHERE approval_deliveries.approval_id=approvals.id
                           AND approval_deliveries.status='delivered'
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM execution_events
                         WHERE execution_events.event_type='approval.pending'
                           AND execution_events.entity_id=CAST(approvals.id AS TEXT)
                           AND execution_events.status IN ('pending', 'processing')
                     )"""
            )

    @staticmethod
    def _repair_recovery_triggers(conn: sqlite3.Connection) -> None:
        for name, definition in RECOVERY_TRIGGER_DEFINITIONS.items():
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.execute(definition)

    def _seed(self, conn: sqlite3.Connection) -> None:
        self._migrate_organization(conn)
        initialized = conn.execute(
            "SELECT 1 FROM audit_log WHERE actor='system' AND action='init' LIMIT 1"
        ).fetchone()
        if initialized is None:
            now = utcnow()
            conn.executemany(
                "INSERT INTO tasks(created_at, updated_at, owner, title, domain, status, priority, blocked_reason, result) VALUES (?, ?, ?, ?, ?, 'open', ?, NULL, NULL)",
                [(now, now, owner, title, domain, priority) for owner, title, domain, priority in SEED_TASKS],
            )
            self.audit(conn, "system", "init", "database", None, {"seeded": True})

    def _migrate_organization(self, conn: sqlite3.Connection) -> dict[str, Any]:
        before = {
            row["name"]: {"kind": row["kind"], "mandate": row["mandate"]}
            for row in conn.execute("SELECT name, kind, mandate FROM roles")
        }
        resident_names = set(ROLES)
        known_names = resident_names | (set(ON_DEMAND_CAPABILITIES) - {"Codex workers"})
        historical_names = sorted(set(before) - known_names)

        placeholders = ",".join("?" for _ in known_names)
        obsolete_active = list(conn.execute(
            f"""SELECT id, owner, status FROM tasks
                WHERE status IN ('open', 'in_progress', 'blocked')
                  AND owner NOT IN ({placeholders})""",
            tuple(sorted(known_names)),
        ))
        now = utcnow()
        for task in obsolete_active:
            result = {
                "migration_version": ORGANIZATION_MIGRATION_VERSION,
                "reason": "closed during lean organization migration; historical owner retained",
                "previous_status": task["status"],
            }
            conn.execute(
                "UPDATE tasks SET status='cancelled', updated_at=?, blocked_reason=NULL, result=? WHERE id=?",
                (now, json.dumps(result, sort_keys=True), task["id"]),
            )
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_executions'"
            ).fetchone():
                conn.execute(
                    "UPDATE task_executions SET recovery_status='cancelled', updated_at=? WHERE task_id=?",
                    (now, task["id"]),
                )

        conn.execute("UPDATE roles SET status='historical' WHERE name NOT IN ({})".format(
            ",".join("?" for _ in known_names)
        ), tuple(sorted(known_names)))
        for name, mandate in ROLES.items():
            kind = "human" if name == "Chairman" else "agent"
            conn.execute(
                """INSERT INTO roles(name, kind, mandate, status) VALUES (?, ?, ?, 'resident')
                   ON CONFLICT(name) DO UPDATE SET
                       kind=excluded.kind,
                       mandate=excluded.mandate,
                       status='resident'""",
                (name, kind, mandate),
            )
        for name, mandate in ON_DEMAND_CAPABILITIES.items():
            if name == "Codex workers":
                continue
            conn.execute(
                """INSERT INTO roles(name, kind, mandate, status) VALUES (?, 'agent', ?, 'on_demand')
                   ON CONFLICT(name) DO UPDATE SET
                       kind='agent', mandate=excluded.mandate, status='on_demand'""",
                (name, mandate),
            )
        conn.execute("DELETE FROM raci")
        for domain, values in RACI.items():
            conn.execute(
                "INSERT INTO raci(domain, responsible, accountable, consulted, informed) VALUES (?, ?, ?, ?, ?)",
                (domain, *values),
            )

        details = {
            "migration_version": ORGANIZATION_MIGRATION_VERSION,
            "resident_roles": sorted(name for name in resident_names if name != "Chairman"),
            "historical_roles": historical_names,
            "raci_domains": sorted(RACI),
            "task_owner_rows_changed": 0,
            "closed_obsolete_active_task_ids": [task["id"] for task in obsolete_active],
        }
        migrated = conn.execute(
            "SELECT 1 FROM audit_log WHERE action='migrate_organization' AND entity_id=?",
            (ORGANIZATION_MIGRATION_VERSION,),
        ).fetchone()
        if migrated is None:
            self.audit(
                conn,
                "system",
                "migrate_organization",
                "organization",
                ORGANIZATION_MIGRATION_VERSION,
                details,
            )
        return details

    def migrate_organization(self) -> dict[str, Any]:
        self.init()
        with self.connect() as conn:
            return self._migrate_organization(conn)

    def audit(self, conn: sqlite3.Connection, actor: str, action: str, entity: str, entity_id: Any, details: dict[str, Any], timestamp: str | None = None, row_id: int | None = None) -> int:
        if row_id is None:
            cursor = conn.execute(
                "INSERT INTO audit_log(ts, actor, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?, ?)",
                (timestamp or utcnow(), actor, action, entity, str(entity_id) if entity_id is not None else None, json.dumps(details, sort_keys=True)),
            )
        else:
            cursor = conn.execute(
                "INSERT INTO audit_log(id,ts, actor, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (row_id, timestamp or utcnow(), actor, action, entity, str(entity_id) if entity_id is not None else None, json.dumps(details, sort_keys=True)),
            )
        return int(cursor.lastrowid)

    @property
    def worker_wake_path(self) -> Path:
        return self.db_path.with_name(f"{self.db_path.name}-worker.wake")

    @property
    def worker_lock_path(self) -> Path:
        return self.db_path.with_name(f"{self.db_path.name}-worker.lock")

    def enqueue_event(
        self,
        conn: sqlite3.Connection,
        event_type: str,
        entity_type: str,
        entity_id: Any,
        payload: dict[str, Any],
        priority: int = 10,
        available_at: str | None = None,
        timestamp: str | None = None,
        event_id: int | None = None,
    ) -> int:
        now = timestamp or utcnow()
        if event_id is None:
            cursor = conn.execute(
                """INSERT INTO execution_events(
                   created_at, available_at, event_type, entity_type,
                   entity_id, payload, status, priority
               ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (now, available_at or now, event_type, entity_type,
                 str(entity_id) if entity_id is not None else None,
                 json.dumps(payload, sort_keys=True), priority),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO execution_events(
                   id,created_at, available_at, event_type, entity_type,
                   entity_id, payload, status, priority
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (event_id, now, available_at or now, event_type, entity_type,
                 str(entity_id) if entity_id is not None else None,
                 json.dumps(payload, sort_keys=True), priority),
            )
        return int(cursor.lastrowid)

    def notify_worker(self) -> bool:
        """Best-effort edge notification; SQLite remains the durable queue."""
        path = self.worker_wake_path
        try:
            fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno in {errno.ENOENT, errno.ENXIO}:
                return False
            raise
        try:
            os.write(fd, b"1")
        finally:
            os.close(fd)
        return True

    def fetch_all(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(query, tuple(params)))

    def fetch_one(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(query, tuple(params)).fetchone()
