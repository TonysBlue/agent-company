"""Opt-in dispatch enforcement for the single approved Phase B pilot."""

from __future__ import annotations

import json
import os
import hashlib
from datetime import datetime, timezone
from typing import Any

from .config import CompanyConfig
from .assurance import AssuranceKernel
from .db import Store, utcnow


APPROVED_PILOT = "pilot-c2-approved-for-build"


class PilotGate:
    def __init__(self, config: CompanyConfig):
        self._config = config
        self.store = Store(config.db_path)

    def init(self) -> None:
        self.store.init_assurance()
        with self.store.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS assurance_task_bindings (
                    task_id INTEGER PRIMARY KEY,
                    initiative_id TEXT NOT NULL,
                    pilot INTEGER NOT NULL,
                    artifact_set_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assurance_pilot_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                INSERT OR IGNORE INTO assurance_pilot_config(key,value,updated_at)
                    VALUES ('kill_switch','false','bootstrap');
                """
            )

    def bind(
        self, task_id: int, initiative_id: str, *, pilot: bool,
        artifact_set_sha256: str | None = None, actor: str = "", principal_id: str = "",
    ) -> None:
        self.init()
        kernel = AssuranceKernel(self._config)
        kernel._assert_principal(actor, principal_id, {"executive", "chairman"})
        with self.store.connect() as conn:
            task = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("task does not exist")
            if task["status"] != "open":
                raise ValueError("pilot binding requires an open unclaimed task")
            existing = conn.execute(
                "SELECT initiative_id,pilot,artifact_set_sha256 FROM assurance_task_bindings WHERE task_id=?",
                (task_id,),
            ).fetchone()
        if pilot and initiative_id != APPROVED_PILOT:
            raise ValueError("only the approved pilot initiative may enable enforcement")
        if artifact_set_sha256 is not None and len(artifact_set_sha256) != 64:
            raise ValueError("artifact set sha256 must be 64 characters")
        now = utcnow()
        with self.store.connect() as conn:
            conn.execute(
                """INSERT INTO assurance_task_bindings(
                       task_id,initiative_id,pilot,artifact_set_sha256,created_at,updated_at
                   ) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(task_id) DO UPDATE SET initiative_id=excluded.initiative_id,
                     pilot=excluded.pilot,artifact_set_sha256=excluded.artifact_set_sha256,
                     updated_at=excluded.updated_at""",
                (task_id, initiative_id, int(pilot), artifact_set_sha256, now, now),
            )
            conn.execute(
                """INSERT INTO audit_log(ts,actor,action,entity,entity_id,details)
                   VALUES (?,?,?,?,?,?)""",
                (now, actor, "bind_pilot_task", "task", str(task_id), json.dumps({
                    "initiative_id": initiative_id, "pilot": pilot,
                    "replaced": existing is not None, "reason": "explicit assurance binding",
                }, sort_keys=True)),
            )

    def set_kill_switch(
        self, enabled: bool, *, actor: str, principal_id: str, reason: str,
    ) -> None:
        self.init()
        AssuranceKernel(self._config)._assert_principal(
            actor, principal_id, {"executive", "chairman"}
        )
        if not reason.strip():
            raise ValueError("a reason is required")
        now = utcnow()
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE assurance_pilot_config SET value=?,updated_at=? WHERE key='kill_switch'",
                ("true" if enabled else "false", now),
            )
            self.store.audit(
                conn, actor, "set_pilot_kill_switch", "assurance_pilot", APPROVED_PILOT,
                {"enabled": enabled, "reason": reason.strip()},
            )

    def dispatch_decision(self, task: dict[str, Any], *, conn: Any | None = None) -> dict[str, Any]:
        if conn is not None:
            return self._dispatch_decision(conn, task)
        self.init()
        with self.store.connect_readonly() as readonly:
            return self._dispatch_decision(readonly, task)

    def _dispatch_decision(self, conn: Any, task: dict[str, Any]) -> dict[str, Any]:
            binding = conn.execute(
                "SELECT * FROM assurance_task_bindings WHERE task_id=?", (int(task["id"]),)
            ).fetchone()
            if binding is None:
                return {"allowed": True, "reason": "unbound"}
            if not binding["pilot"]:
                return {"allowed": True, "reason": "non-pilot"}
            killed = conn.execute(
                "SELECT value FROM assurance_pilot_config WHERE key='kill_switch'"
            ).fetchone()["value"] == "true"
            if killed:
                return {"allowed": True, "reason": "pilot enforcement killed"}
            if binding["initiative_id"] != APPROVED_PILOT:
                return {"allowed": False, "reason": "unapproved pilot initiative"}
            initiative = conn.execute(
                "SELECT status,mode FROM assurance_initiatives WHERE initiative_id=?",
                (binding["initiative_id"],),
            ).fetchone()
            if initiative is None or initiative["status"] != "approved_for_build":
                return {"allowed": False, "reason": "pilot requires approved_for_build and passing G4"}
            decision = conn.execute(
                """SELECT decision,artifact_set_sha256,expires_at FROM assurance_gate_decisions
                   WHERE initiative_id=? AND gate='G4'
                   ORDER BY id DESC LIMIT 1""",
                (binding["initiative_id"],),
            ).fetchone()
            if decision is None or decision["decision"] != "pass":
                return {"allowed": False, "reason": "pilot requires passing G4"}
            kernel = AssuranceKernel(self._config)
            conflicts = []
            for artifact in conn.execute(
                "SELECT artifact_id,version,content_json,content_sha256 FROM assurance_artifacts"
            ):
                actual = hashlib.sha256(artifact["content_json"].encode("utf-8")).hexdigest()
                if actual != artifact["content_sha256"]:
                    conflicts.append(f"{artifact['artifact_id']}:v{artifact['version']}")
            if conflicts:
                return {"allowed": False, "reason": "assurance integrity conflict"}
            current_hash = kernel._initiative_artifact_set_sha256(conn, binding["initiative_id"])
            stale = conn.execute(
                "SELECT 1 FROM assurance_artifacts WHERE initiative_id=? AND status='stale' LIMIT 1",
                (binding["initiative_id"],),
            ).fetchone()
            if stale or current_hash != decision["artifact_set_sha256"]:
                return {"allowed": False, "reason": "G4 artifact set is stale"}
            if decision["expires_at"]:
                expiry = datetime.fromisoformat(decision["expires_at"])
                if expiry <= datetime.now(timezone.utc):
                    return {"allowed": False, "reason": "G4 decision expired"}
            if not binding["artifact_set_sha256"] or binding["artifact_set_sha256"] != decision["artifact_set_sha256"]:
                return {"allowed": False, "reason": "pilot artifact set hash mismatch"}
            return {
                "allowed": True, "reason": "approved pilot G4",
                "initiative_id": binding["initiative_id"],
                "artifact_set_sha256": binding["artifact_set_sha256"],
            }
