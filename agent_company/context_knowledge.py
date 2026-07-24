"""Durable role continuity, project history, and cross-role handoffs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import CompanyConfig
from .db import Store, utcnow

CONTINUITY_KEYS = {
    "schema_version", "role", "summary", "verified_facts", "open_items",
    "project_summary", "project_decisions", "known_limits", "handoffs",
}
HANDOFF_STATES = {
    "offered": {"accepted", "rejected", "needs_clarification"},
    "accepted": {"closed", "needs_clarification"},
    "needs_clarification": {"accepted", "rejected"},
    "rejected": set(),
    "closed": set(),
}


class ContextKnowledge:
    def __init__(self, config: CompanyConfig):
        self.store = Store(config.db_path)
        self.store.init()

    def update_role_continuity(
        self, *, role: str, summary: str, verified_facts: list[str], open_items: list[str],
        source_task_id: int, actor: str,
    ) -> dict[str, Any]:
        if not role.strip() or not summary.strip() or any(not str(item).strip() for item in verified_facts + open_items):
            raise ValueError("role continuity fields must not be empty")
        if actor not in {role, "CEO"}:
            raise ValueError("actor is not authorized to update role continuity")
        with self.store.connect() as conn:
            role_row = conn.execute("SELECT 1 FROM roles WHERE name=?", (role,)).fetchone()
            task = conn.execute("SELECT owner FROM tasks WHERE id=?", (source_task_id,)).fetchone()
            if role_row is None:
                raise ValueError(f"unknown role: {role}")
            if task is None:
                raise ValueError(f"unknown source task: {source_task_id}")
            if actor != "CEO" and task["owner"] != role:
                raise ValueError("role continuity source task belongs to another role")
            current = conn.execute("SELECT version FROM role_continuity WHERE role=?", (role,)).fetchone()
            version = int(current["version"]) + 1 if current else 1
            now = utcnow()
            conn.execute(
                """INSERT INTO role_continuity(role, summary, verified_facts_json, open_items_json, source_task_id, version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(role) DO UPDATE SET summary=excluded.summary,
                     verified_facts_json=excluded.verified_facts_json, open_items_json=excluded.open_items_json,
                     source_task_id=excluded.source_task_id, version=excluded.version, updated_at=excluded.updated_at""",
                (role, summary.strip(), json.dumps(verified_facts), json.dumps(open_items), source_task_id, version, now),
            )
            self.store.audit(conn, actor, "update_role_continuity", "role", role, {
                "version": version, "source_task_id": source_task_id,
            })
            return {"role": role, "version": version, "updated_at": now}

    def update_project_history(
        self, *, repository_id: str, summary: str, decisions: list[str], known_limits: list[str], actor: str,
    ) -> dict[str, Any]:
        if not repository_id.strip() or not summary.strip() or any(not str(item).strip() for item in decisions + known_limits):
            raise ValueError("project history fields must not be empty")
        if actor not in {"CEO", "Product Engineer", "Customer & Revenue", "Company Platform Engineer", "Control & Reliability Reviewer", "Finance & Risk Reviewer", "Legal/Compliance Specialist", "Independent Quality Reviewer"}:
            raise ValueError("actor is not authorized to update project history")
        with self.store.connect() as conn:
            current = conn.execute("SELECT version FROM project_history WHERE repository_id=?", (repository_id,)).fetchone()
            version = int(current["version"]) + 1 if current else 1
            now = utcnow()
            conn.execute(
                """INSERT INTO project_history(repository_id, summary, decisions_json, known_limits_json, version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(repository_id) DO UPDATE SET summary=excluded.summary,
                     decisions_json=excluded.decisions_json, known_limits_json=excluded.known_limits_json,
                     version=excluded.version, updated_at=excluded.updated_at""",
                (repository_id, summary.strip(), json.dumps(decisions), json.dumps(known_limits), version, now),
            )
            self.store.audit(conn, actor, "update_project_history", "repository", repository_id, {"version": version})
            return {"repository_id": repository_id, "version": version, "updated_at": now}

    def create_handoff(
        self, *, task_id: int, from_role: str, to_role: str, handoff_type: str,
        summary: str, artifact_refs: list[str], decision_needed: str | None,
    ) -> dict[str, Any]:
        if from_role == to_role:
            raise ValueError("handoff roles must differ")
        if not handoff_type.strip() or not summary.strip() or any(not str(item).strip() for item in artifact_refs):
            raise ValueError("handoff fields must not be empty")
        with self.store.connect() as conn:
            task = conn.execute("SELECT owner FROM tasks WHERE id=?", (task_id,)).fetchone()
            roles = {row["name"] for row in conn.execute("SELECT name FROM roles")}
            if task is None or from_role not in roles or to_role not in roles:
                raise ValueError("handoff task and roles must exist")
            if from_role != "CEO" and task["owner"] != from_role:
                raise ValueError("handoff sender does not own the source task")
            cursor = conn.execute(
                """INSERT INTO handoffs(task_id, from_role, to_role, handoff_type, summary,
                       artifact_refs_json, decision_needed, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'offered', ?)""",
                (task_id, from_role, to_role, handoff_type.strip(), summary.strip(),
                 json.dumps(artifact_refs), decision_needed, utcnow()),
            )
            handoff_id = int(cursor.lastrowid)
            self.store.audit(conn, from_role, "create_handoff", "handoff", handoff_id, {
                "task_id": task_id, "to_role": to_role, "type": handoff_type,
            })
            return {"handoff_id": handoff_id, "status": "offered"}

    def transition_handoff(self, handoff_id: int, actor: str, status: str) -> dict[str, Any]:
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id,)).fetchone()
            if row is None:
                raise ValueError(f"handoff not found: {handoff_id}")
            allowed_actors = {"CEO"}
            if status in {"accepted", "rejected", "needs_clarification"}:
                allowed_actors.add(row["to_role"])
            if status == "closed":
                allowed_actors.update({row["from_role"], row["to_role"]})
            if actor not in allowed_actors:
                raise ValueError("actor is not authorized for handoff transition")
            if status not in HANDOFF_STATES.get(row["status"], set()):
                raise ValueError(f"invalid handoff transition: {row['status']} -> {status}")
            now = utcnow()
            accepted_at = now if status == "accepted" else row["accepted_at"]
            closed_at = now if status in {"closed", "rejected"} else row["closed_at"]
            conn.execute(
                "UPDATE handoffs SET status=?, accepted_at=?, closed_at=? WHERE id=?",
                (status, accepted_at, closed_at, handoff_id),
            )
            self.store.audit(conn, actor, "transition_handoff", "handoff", handoff_id, {
                "from": row["status"], "to": status,
            })
            return {"handoff_id": handoff_id, "status": status}

    def ingest_continuity(
        self, path: Path, *, expected_role: str, task_id: int, repository_id: str,
    ) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != CONTINUITY_KEYS:
            raise ValueError("continuity payload has unknown or missing keys")
        if payload["schema_version"] != "agent-company-continuity/v1":
            raise ValueError("unsupported continuity schema")
        if payload["role"] != expected_role:
            raise ValueError("continuity role mismatch")
        for key in ("verified_facts", "open_items", "project_decisions", "known_limits", "handoffs"):
            if not isinstance(payload[key], list):
                raise ValueError(f"continuity {key} must be a list")
        for item in payload["handoffs"]:
            if not isinstance(item, dict) or set(item) != {"to_role", "handoff_type", "summary", "artifact_refs", "decision_needed"}:
                raise ValueError("handoff payload has unknown or missing keys")
            if not isinstance(item["artifact_refs"], list):
                raise ValueError("handoff artifact_refs must be a list")
        if not str(payload["summary"]).strip():
            raise ValueError("continuity summary must not be empty")
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute("SELECT owner FROM tasks WHERE id=?", (task_id,)).fetchone()
            roles = {row["name"] for row in conn.execute("SELECT name FROM roles")}
            if task is None or task["owner"] != expected_role or expected_role not in roles:
                raise ValueError("continuity source task is not owned by the expected role")
            for item in payload["handoffs"]:
                if item["to_role"] not in roles or item["to_role"] == expected_role:
                    raise ValueError("handoff target role is invalid")
            now = utcnow()
            current = conn.execute("SELECT version FROM role_continuity WHERE role=?", (expected_role,)).fetchone()
            role_version = int(current["version"]) + 1 if current else 1
            conn.execute(
                """INSERT INTO role_continuity(role, summary, verified_facts_json, open_items_json, source_task_id, version, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(role) DO UPDATE SET summary=excluded.summary,
                     verified_facts_json=excluded.verified_facts_json, open_items_json=excluded.open_items_json,
                     source_task_id=excluded.source_task_id, version=excluded.version, updated_at=excluded.updated_at""",
                (expected_role, payload["summary"].strip(), json.dumps(payload["verified_facts"]),
                 json.dumps(payload["open_items"]), task_id, role_version, now),
            )
            self.store.audit(conn, expected_role, "update_role_continuity", "role", expected_role, {"version": role_version, "source_task_id": task_id})
            project_result = None
            if payload["project_summary"]:
                current = conn.execute("SELECT version FROM project_history WHERE repository_id=?", (repository_id,)).fetchone()
                project_version = int(current["version"]) + 1 if current else 1
                conn.execute(
                    """INSERT INTO project_history(repository_id, summary, decisions_json, known_limits_json, version, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(repository_id) DO UPDATE SET summary=excluded.summary,
                         decisions_json=excluded.decisions_json, known_limits_json=excluded.known_limits_json,
                         version=excluded.version, updated_at=excluded.updated_at""",
                    (repository_id, str(payload["project_summary"]).strip(), json.dumps(payload["project_decisions"]),
                     json.dumps(payload["known_limits"]), project_version, now),
                )
                self.store.audit(conn, expected_role, "update_project_history", "repository", repository_id, {"version": project_version})
                project_result = {"repository_id": repository_id, "version": project_version, "updated_at": now}
            handoff_results = []
            for item in payload["handoffs"]:
                cursor = conn.execute(
                    """INSERT INTO handoffs(task_id, from_role, to_role, handoff_type, summary,
                           artifact_refs_json, decision_needed, status, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'offered', ?)""",
                    (task_id, expected_role, item["to_role"], str(item["handoff_type"]).strip(),
                     str(item["summary"]).strip(), json.dumps(item["artifact_refs"]), item["decision_needed"], now),
                )
                handoff_id = int(cursor.lastrowid)
                self.store.audit(conn, expected_role, "create_handoff", "handoff", handoff_id, {"task_id": task_id, "to_role": item["to_role"], "type": item["handoff_type"]})
                handoff_results.append({"handoff_id": handoff_id, "status": "offered"})
            return {
                "role": {"role": expected_role, "version": role_version, "updated_at": now},
                "project": project_result,
                "handoffs": handoff_results,
            }
