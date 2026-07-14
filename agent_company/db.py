"""SQLite persistence and audit helpers."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import RACI, ROLES, SEED_TASKS


ORGANIZATION_MIGRATION_VERSION = "lean-org-v1"


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
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
                    outbox_file TEXT
                );
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
                    UNIQUE(task_id)
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
            self._seed(conn)

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
        historical_names = sorted(set(before) - resident_names)

        obsolete_active = list(conn.execute(
            """SELECT id, owner, status FROM tasks
               WHERE status IN ('open', 'in_progress', 'blocked')
                 AND owner NOT IN (?, ?, ?)""",
            ("CEO", "Product Engineer", "Customer & Revenue"),
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
            ",".join("?" for _ in resident_names)
        ), tuple(sorted(resident_names)))
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

    def audit(self, conn: sqlite3.Connection, actor: str, action: str, entity: str, entity_id: Any, details: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO audit_log(ts, actor, action, entity, entity_id, details) VALUES (?, ?, ?, ?, ?, ?)",
            (utcnow(), actor, action, entity, str(entity_id) if entity_id is not None else None, json.dumps(details, sort_keys=True)),
        )

    def fetch_all(self, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute(query, tuple(params)))

    def fetch_one(self, query: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(query, tuple(params)).fetchone()
