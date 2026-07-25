"""Opt-in dispatch enforcement for the single approved Phase B pilot."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import CompanyConfig
from .db import Store, utcnow


APPROVED_PILOT = "pilot-c2-approved-for-build"


class PilotGate:
    def __init__(self, config: CompanyConfig):
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
        artifact_set_sha256: str | None = None,
    ) -> None:
        self.init()
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

    def set_kill_switch(self, enabled: bool, *, actor: str, reason: str) -> None:
        self.init()
        if actor not in {"CEO", "Chairman"} or not reason.strip():
            raise ValueError("CEO or Chairman and a reason are required")
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

    def dispatch_decision(self, task: dict[str, Any]) -> dict[str, Any]:
        self.init()
        with self.store.connect_readonly() as conn:
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
