"""Persistent control plane for the single logical Hermes CEO."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import CompanyConfig
from .db import Store, utcnow


STATE_SCHEMA_VERSION = "ceo-state/v1"
DIRECTIVE_SCHEMA_VERSION = "chairman-directive/v1"
ACTION_SCHEMA_VERSION = "ceo-actions/v1"
SOURCE_TAG = "agent-company-ceo-background"
SAFE_ACTIONS = {"noop", "create_task", "request_approval", "update_state"}
SAFE_OWNERS = {"Product Engineer", "Customer & Revenue"}
SAFE_DOMAINS = {"product", "engineering", "gtm", "revenue", "customer", "commercial"}
COMPLEX_EVENT_TYPES = {
    "chairman.directive",
    "chairman.decided",
    "task.completed",
    "task.failed",
    "task.recovered",
    "ceo.fixture",
    "ceo.strategic_review",
    "ceo.business_stall_review",
}


class ProtocolError(ValueError):
    pass


class VersionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ReasoningResponse:
    payload: dict[str, Any]
    model: str | None = None
    provider: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


class Reasoner(Protocol):
    def reason(self, prompt: str) -> ReasoningResponse: ...


class DecisionSender(Protocol):
    def send(self, card: str, idempotency_key: str) -> dict[str, object]: ...


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _retry_at(attempt: int) -> str:
    delay = min(300, 2 ** min(max(attempt, 1), 8))
    return (datetime.now(timezone.utc) + timedelta(seconds=delay)).replace(microsecond=0).isoformat()


class HermesOneShotReasoner:
    """Invoke the current CEO's default Hermes profile with no execution tools."""

    def __init__(self, config: CompanyConfig):
        self.config = config

    def reason(self, prompt: str) -> ReasoningResponse:
        argv = [
            "hermes",
            "chat",
            "-q",
            prompt,
            "-Q",
            "--source",
            "tool",
            "-t",
            "todo",
            "--max-turns",
            "1",
        ]
        completed = subprocess.run(
            argv,
            cwd=self.config.workspace,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            timeout=self.config.ceo_hermes_timeout_seconds,
            check=False,
        )
        if completed.returncode:
            error = completed.stderr.strip() or f"Hermes exited {completed.returncode}"
            raise RuntimeError(error)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProtocolError("Hermes response must be one JSON object") from exc
        return ReasoningResponse(payload=payload)


class HermesWeixinSender:
    def __init__(self, config: CompanyConfig):
        self.config = config

    def send(self, card: str, idempotency_key: str) -> dict[str, object]:
        completed = subprocess.run(
            ["hermes", "send", "--to", "weixin", "--json", card],
            cwd=self.config.workspace,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env={**os.environ, "HERMES_CEO_DELIVERY_KEY": idempotency_key},
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr.strip() or f"hermes send exited {completed.returncode}")
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError:
            result = {"ok": True, "output": completed.stdout.strip()}
        return result if isinstance(result, dict) else {"ok": True, "result": result}


class FixtureReasoner:
    def __init__(self, path: Path):
        self.path = path

    def reason(self, prompt: str) -> ReasoningResponse:
        del prompt
        return ReasoningResponse(payload=json.loads(self.path.read_text(encoding="utf-8")), provider="fixture")


class DisabledSender:
    def send(self, card: str, idempotency_key: str) -> dict[str, object]:
        del card, idempotency_key
        raise RuntimeError("external delivery is disabled")


class CEORuntime:
    def __init__(
        self,
        config: CompanyConfig,
        reasoner: Reasoner | None = None,
        sender: DecisionSender | None = None,
        external_delivery_enabled: bool | None = None,
    ):
        self.config = config
        self.store = Store(config.db_path)
        self.reasoner = reasoner or HermesOneShotReasoner(config)
        self.sender = sender or HermesWeixinSender(config)
        self.external_delivery_enabled = (
            config.ceo_external_delivery_enabled
            if external_delivery_enabled is None
            else external_delivery_enabled
        )

    @property
    def lock_path(self) -> Path:
        return self.config.db_path.with_name(f"{self.config.db_path.name}-ceo.lock")

    def init(self) -> None:
        self.store.init()

    @contextlib.contextmanager
    def writer_lock(self):
        self.init()
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def status(self) -> dict[str, Any]:
        self.init()
        state = self.store.fetch_one("SELECT * FROM ceo_state_versions ORDER BY version DESC LIMIT 1")
        counts = self.store.fetch_one(
            """SELECT
                   SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) AS pending,
                   SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active
               FROM chairman_directives"""
        )
        last_run = self.store.fetch_one(
            "SELECT id, created_at, finished_at, event_id, status, model, provider, error FROM ceo_runs ORDER BY id DESC LIMIT 1"
        )
        return {
            "identity": "Hermes default profile CEO",
            "logical_ceo_count": 1,
            "source_tag": SOURCE_TAG,
            "state_version": state["version"],
            "state_schema_version": state["schema_version"],
            "active_directive_version": state["active_directive_version"],
            "pending_directives": int(counts["pending"] or 0),
            "active_directives": int(counts["active"] or 0),
            "external_delivery_enabled": self.external_delivery_enabled,
            "last_run": dict(last_run) if last_run else None,
        }

    def ingest_directive(
        self,
        *,
        source_platform: str,
        source_session_id: str,
        source_message_id: str,
        message: str,
        directive_type: str,
        objective: str,
        constraints: list[str],
        priority: int,
    ) -> dict[str, Any]:
        values = [source_platform, source_session_id, source_message_id, message, directive_type, objective]
        if any(not value.strip() for value in values):
            raise ValueError("directive source, message, type, and objective must not be empty")
        if not 1 <= priority <= 100:
            raise ValueError("directive priority must be between 1 and 100")
        if any(not item.strip() for item in constraints):
            raise ValueError("directive constraints must not be empty")
        with self.writer_lock(), self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = conn.execute(
                """SELECT id, directive_version FROM chairman_directives
                   WHERE source_platform=? AND source_session_id=? AND source_message_id=?""",
                (source_platform.strip(), source_session_id.strip(), source_message_id.strip()),
            ).fetchone()
            if duplicate:
                state = conn.execute("SELECT version FROM ceo_state_versions ORDER BY version DESC LIMIT 1").fetchone()
                return {
                    "directive_id": duplicate["id"],
                    "directive_version": duplicate["directive_version"],
                    "state_version": state["version"],
                    "event_type": "chairman.directive",
                    "duplicate": True,
                }
            current = conn.execute("SELECT * FROM ceo_state_versions ORDER BY version DESC LIMIT 1").fetchone()
            directive_version = int(current["active_directive_version"]) + 1
            cursor = conn.execute(
                """INSERT INTO chairman_directives(
                       directive_version, schema_version, created_at, source_platform,
                       source_session_id, source_message_id, source_message_sha256,
                       directive_type, objective, constraints_json, priority, status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (
                    directive_version,
                    DIRECTIVE_SCHEMA_VERSION,
                    utcnow(),
                    source_platform.strip(),
                    source_session_id.strip(),
                    source_message_id.strip(),
                    _sha256(message),
                    directive_type.strip(),
                    objective.strip(),
                    _canonical([item.strip() for item in constraints]),
                    priority,
                ),
            )
            directive_id = int(cursor.lastrowid)
            state_version = self._append_state(
                conn,
                expected_version=current["version"],
                strategy=json.loads(current["strategy_json"]),
                assumptions=json.loads(current["assumptions_json"]),
                critical_path=json.loads(current["critical_path_json"]),
                active_directive_version=directive_version,
                source_kind="chairman_directive",
                source_id=directive_id,
            )
            event_id = self.store.enqueue_event(
                conn,
                "chairman.directive",
                "chairman_directive",
                directive_id,
                {"directive_version": directive_version},
                priority=100,
            )
            self.store.audit(
                conn,
                "Chairman",
                "ingest_chairman_directive",
                "chairman_directive",
                directive_id,
                {
                    "directive_version": directive_version,
                    "source_platform": source_platform.strip(),
                    "source_session_id": source_session_id.strip(),
                    "source_message_id": source_message_id.strip(),
                    "source_message_sha256": _sha256(message),
                    "directive_type": directive_type.strip(),
                    "objective": objective.strip(),
                    "constraints": [item.strip() for item in constraints],
                    "priority": priority,
                    "raw_message_retained": False,
                },
            )
        self.store.notify_worker()
        return {
            "directive_id": directive_id,
            "directive_version": directive_version,
            "state_version": state_version,
            "event_id": event_id,
            "event_type": "chairman.directive",
            "duplicate": False,
        }

    def classify(self, event: dict[str, Any]) -> str:
        if event["event_type"] == "approval.pending":
            return "delivery"
        if event["event_type"] in COMPLEX_EVENT_TYPES:
            return "complex_judgment"
        return "deterministic_dispatch"

    def process_event(self, event: dict[str, Any]) -> dict[str, Any]:
        classification = self.classify(event)
        if classification == "delivery":
            result = self._deliver_approval(int(event["entity_id"]))
            return {"classification": classification, **result}
        if classification != "complex_judgment":
            return {"classification": classification, "status": "not_invoked"}
        return {"classification": classification, **self._reason_about(event)}

    def _snapshot(self, event: dict[str, Any]) -> tuple[dict[str, Any], int, int]:
        with self.store.connect() as conn:
            state = conn.execute("SELECT * FROM ceo_state_versions ORDER BY version DESC LIMIT 1").fetchone()
            directives = [
                {
                    "directive_version": row["directive_version"],
                    "directive_type": row["directive_type"],
                    "objective": row["objective"],
                    "constraints": json.loads(row["constraints_json"]),
                    "priority": row["priority"],
                    "status": row["status"],
                    "source": {
                        "platform": row["source_platform"],
                        "session_id": row["source_session_id"],
                        "message_id": row["source_message_id"],
                        "message_sha256": row["source_message_sha256"],
                    },
                }
                for row in conn.execute(
                    "SELECT * FROM chairman_directives WHERE status IN ('pending', 'active') ORDER BY priority DESC, directive_version DESC LIMIT 5"
                )
            ]
            entity: dict[str, Any] | None = None
            if event["entity_type"] == "task" and event["entity_id"]:
                row = conn.execute(
                    "SELECT id, owner, title, domain, status, priority, blocked_reason, acceptance_criteria FROM tasks WHERE id=?",
                    (event["entity_id"],),
                ).fetchone()
                entity = dict(row) if row else None
            elif event["entity_type"] == "approval" and event["entity_id"]:
                row = conn.execute(
                    "SELECT id, action_type, summary, status, decision, rationale FROM approvals WHERE id=?",
                    (event["entity_id"],),
                ).fetchone()
                entity = dict(row) if row else None
            elif event["entity_type"] == "strategic_phase" and event["entity_id"]:
                row = conn.execute(
                    "SELECT * FROM strategic_phases WHERE id=?", (event["entity_id"],)
                ).fetchone()
                entity = dict(row) if row else None
            strategic_phase = conn.execute(
                "SELECT * FROM strategic_phases WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            active_tasks = [
                dict(row) for row in conn.execute(
                    """SELECT id, owner, title, domain, status, priority, acceptance_criteria,
                              strategic_phase_id, business_outcome
                       FROM tasks WHERE status IN ('open', 'in_progress', 'blocked')
                       ORDER BY priority DESC, id"""
                )
            ]
            metrics = [dict(row) for row in conn.execute("SELECT name, value, unit, ts FROM metrics ORDER BY id DESC LIMIT 20")]
            experiments = [dict(row) for row in conn.execute("SELECT * FROM experiments ORDER BY id DESC LIMIT 10")]
            completed_phase_tasks = [
                dict(row) for row in conn.execute(
                    """SELECT id, owner, title, status, business_outcome, result
                       FROM tasks WHERE strategic_phase_id=? AND status IN ('done', 'cancelled')
                       ORDER BY id""",
                    (strategic_phase["id"],),
                )
            ] if strategic_phase else []
            organization = [dict(row) for row in conn.execute(
                "SELECT name, mandate, status FROM roles ORDER BY status, name"
            )]
            raci = [dict(row) for row in conn.execute(
                "SELECT domain, responsible, accountable, consulted, informed FROM raci ORDER BY domain"
            )]
            role_continuity = [dict(row) for row in conn.execute(
                "SELECT role, summary, open_items_json, source_task_id, version, updated_at FROM role_continuity ORDER BY updated_at DESC"
            )]
            open_handoffs = [dict(row) for row in conn.execute(
                "SELECT id, task_id, from_role, to_role, handoff_type, summary, decision_needed, status FROM handoffs WHERE status IN ('offered','accepted','needs_clarification') ORDER BY id"
            )]
            for row in role_continuity:
                row["open_items"] = json.loads(row.pop("open_items_json"))
            phase_snapshot = None
            if strategic_phase:
                phase_snapshot = dict(strategic_phase)
                for key in ("success_metrics", "dependencies", "evidence_requirements"):
                    phase_snapshot[key] = json.loads(phase_snapshot[key])
            snapshot = {
                "schema_version": "ceo-input-snapshot/v1",
                "source_tag": SOURCE_TAG,
                "logical_identity": "same logical CEO as the interactive Hermes default profile",
                "event": {
                    "id": event["id"],
                    "event_type": event["event_type"],
                    "entity_type": event["entity_type"],
                    "entity_id": event["entity_id"],
                    "payload": json.loads(event["payload"]),
                    "attempts": event["attempts"],
                },
                "state": {
                    "version": state["version"],
                    "strategy": json.loads(state["strategy_json"]),
                    "assumptions": json.loads(state["assumptions_json"]),
                    "critical_path": json.loads(state["critical_path_json"]),
                },
                "directives": directives,
                "organization_context": {
                    "roles": organization,
                    "raci": raci,
                    "role_continuity": role_continuity,
                    "open_handoffs": open_handoffs,
                },
                "entity": entity,
                "strategic_phase": phase_snapshot,
                "work_state": {
                    "active_tasks": active_tasks,
                    "pending_approvals": int(conn.execute(
                        "SELECT COUNT(*) FROM approvals WHERE status='pending'"
                    ).fetchone()[0]),
                },
                "business_evidence": {
                    "metrics": metrics,
                    "experiments": experiments,
                    "completed_phase_tasks": completed_phase_tasks,
                },
            }
            return snapshot, int(state["version"]), int(state["active_directive_version"])

    def _prompt(self, snapshot: dict[str, Any]) -> str:
        return (
            "Source tag: agent-company-ceo-background. You are the same logical CEO as the "
            "interactive Hermes default profile, operating as its unattended background form. "
            "The snapshot includes the public organization map, RACI, role continuity, and open handoffs; "
            "use them to preserve cross-role coordination and do not absorb another role's accountability. "
            "Do not use tools. Decide only from this self-contained minimal snapshot. Never execute "
            "external, irreversible, financial, legal, pricing, publication, production, customer-data, "
            "or shell actions. Return exactly one JSON object conforming to ceo-actions/v1. Allowed actions: "
            "noop, update_state, create_task for a bounded internal task, or request_approval. "
            "create_task exact keys: type, owner, title, domain, priority, acceptance_criteria; "
            "priority must be a JSON integer from 1 through 100. "
            "Allowed owners are Product Engineer and Customer & Revenue; allowed domains are product, "
            "engineering, gtm, revenue, customer, commercial. Do not add reason, phase, outcome, deadline, "
            "or evidence fields to create_task. noop and update_state exact keys are type and reason; "
            "state changes belong only in the top-level state_patch object. Example create_task: "
            "{\"type\":\"create_task\",\"owner\":\"Product Engineer\",\"title\":\"Bounded work\","
            "\"domain\":\"product\",\"priority\":90,\"acceptance_criteria\":"
            "\"Non-empty evidence-based completion criteria\"}. For strategic reviews, create at most one product task and "
            "one commercial task, or request Chairman approval; noop alone is invalid. "
            "Top-level keys must be schema_version, judgment, state_patch, actions.\nSNAPSHOT:\n"
            + _canonical(snapshot)
        )

    def _reason_about(self, event: dict[str, Any]) -> dict[str, Any]:
        snapshot, state_version, directive_version = self._snapshot(event)
        snapshot_json = _canonical(snapshot)
        with self.store.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO ceo_runs(
                       created_at, event_id, source_tag, state_version_read,
                       directive_version_read, input_snapshot_sha256,
                       input_snapshot_json, status
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'reasoning')""",
                (utcnow(), event["id"], SOURCE_TAG, state_version, directive_version, _sha256(snapshot_json), snapshot_json),
            )
            run_id = int(cursor.lastrowid)
        try:
            response = self.reasoner.reason(self._prompt(snapshot))
            payload = self.validate_protocol(response.payload)
            if event["event_type"] in {"ceo.strategic_review", "ceo.business_stall_review"}:
                has_forward_action = any(
                    action["type"] in {"create_task", "request_approval"}
                    for action in payload["actions"]
                )
                if not has_forward_action:
                    raise ProtocolError(
                        "strategic review must create bounded work or request Chairman approval"
                    )
        except Exception as exc:
            protocol_rejected = isinstance(exc, ProtocolError) and int(event["attempts"]) >= 3
            retryable = (
                isinstance(exc, (subprocess.TimeoutExpired, ProtocolError))
                or "524" in str(exc)
                or "timeout" in str(exc).lower()
            ) and not protocol_rejected
            status = "retry_scheduled" if retryable else (
                "protocol_rejected" if protocol_rejected else "failed"
            )
            error = f"timeout: {exc}" if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
            with self.store.connect() as conn:
                conn.execute(
                    "UPDATE ceo_runs SET finished_at=?, status=?, error=? WHERE id=?",
                    (utcnow(), status, error, run_id),
                )
            if retryable or protocol_rejected:
                return {"status": status, "run_id": run_id, "error": error}
            raise
        with self.writer_lock(), self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM ceo_state_versions ORDER BY version DESC LIMIT 1").fetchone()
            if int(current["version"]) != state_version or int(current["active_directive_version"]) != directive_version:
                conn.execute(
                    """UPDATE ceo_runs SET finished_at=?, model=?, provider=?, input_tokens=?,
                           output_tokens=?, cache_tokens=?, reasoning_tokens=?, total_tokens=?,
                           judgment=?, actions_json=?, result_json=?, status='superseded'
                       WHERE id=?""",
                    (
                        utcnow(), response.model, response.provider, response.input_tokens,
                        response.output_tokens, response.cache_tokens, response.reasoning_tokens,
                        response.total_tokens, payload["judgment"], _canonical(payload["actions"]),
                        _canonical({"status": "superseded_by_newer_state"}), run_id,
                    ),
                )
                return {"status": "superseded", "run_id": run_id, "result": {"actions": []}}
            result = self._apply_actions(conn, payload, current, run_id)
            conn.execute(
                """UPDATE ceo_runs SET finished_at=?, model=?, provider=?, input_tokens=?,
                       output_tokens=?, cache_tokens=?, reasoning_tokens=?, total_tokens=?,
                       judgment=?, actions_json=?, result_json=?, status='applied'
                   WHERE id=?""",
                (
                    utcnow(), response.model, response.provider, response.input_tokens,
                    response.output_tokens, response.cache_tokens, response.reasoning_tokens,
                    response.total_tokens, payload["judgment"], _canonical(payload["actions"]),
                    _canonical(result), run_id,
                ),
            )
        return {
            "status": "applied",
            "run_id": run_id,
            "model": response.model,
            "provider": response.provider,
            "result": result,
            "external_delivery_enabled": self.external_delivery_enabled,
        }

    def validate_protocol(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "judgment", "state_patch", "actions"}:
            raise ProtocolError("CEO response has unknown or missing top-level fields")
        if payload["schema_version"] != ACTION_SCHEMA_VERSION:
            raise ProtocolError("unsupported CEO action schema")
        if not isinstance(payload["judgment"], str) or not payload["judgment"].strip():
            raise ProtocolError("judgment must be a non-empty string")
        patch = payload["state_patch"]
        if not isinstance(patch, dict) or not set(patch) <= {"strategy", "assumptions", "critical_path"}:
            raise ProtocolError("state_patch contains unsupported fields")
        if "strategy" in patch and not isinstance(patch["strategy"], dict):
            raise ProtocolError("strategy must be an object")
        for key in ("assumptions", "critical_path"):
            if key in patch and (not isinstance(patch[key], list) or not all(isinstance(item, str) and item.strip() for item in patch[key])):
                raise ProtocolError(f"{key} must be a list of non-empty strings")
        actions = payload["actions"]
        if not isinstance(actions, list) or not 1 <= len(actions) <= 5:
            raise ProtocolError("actions must contain one to five items")
        for action in actions:
            self._validate_action(action)
        return payload

    def _validate_action(self, action: Any) -> None:
        if not isinstance(action, dict) or action.get("type") not in SAFE_ACTIONS:
            raise ProtocolError("action type is not allowlisted")
        action_type = action["type"]
        allowed = {
            "noop": {"type", "reason"},
            "update_state": {"type", "reason"},
            "create_task": {"type", "owner", "title", "domain", "priority", "acceptance_criteria"},
            "request_approval": {"type", "action_type", "summary", "options", "recommendation", "risk"},
        }[action_type]
        if set(action) != allowed:
            raise ProtocolError(f"{action_type} has unknown or missing fields")
        if action_type in {"noop", "update_state"}:
            if not isinstance(action["reason"], str) or not action["reason"].strip():
                raise ProtocolError("action reason must be non-empty")
        elif action_type == "create_task":
            if action["owner"] not in SAFE_OWNERS or action["domain"] not in SAFE_DOMAINS:
                raise ProtocolError("task owner or domain is not allowlisted")
            if not isinstance(action["priority"], int) or not 1 <= action["priority"] <= 100:
                raise ProtocolError("task priority is invalid")
            for key in ("title", "acceptance_criteria"):
                if not isinstance(action[key], str) or not action[key].strip():
                    raise ProtocolError(f"task {key} must be non-empty")
        else:
            if action["action_type"] not in self.config.reserved_actions:
                raise ProtocolError("approval action_type must be a configured reserved action")
            if not isinstance(action["options"], list) or len(action["options"]) < 2:
                raise ProtocolError("approval must include at least two options")
            for key in ("summary", "recommendation", "risk"):
                if not isinstance(action[key], str) or not action[key].strip():
                    raise ProtocolError(f"approval {key} must be non-empty")

    def _apply_actions(self, conn, payload: dict[str, Any], current, run_id: int) -> dict[str, Any]:
        state_patch = payload["state_patch"]
        action_results: list[dict[str, Any]] = []
        if state_patch:
            state_version = self._append_state(
                conn,
                expected_version=current["version"],
                strategy=state_patch.get("strategy", json.loads(current["strategy_json"])),
                assumptions=state_patch.get("assumptions", json.loads(current["assumptions_json"])),
                critical_path=state_patch.get("critical_path", json.loads(current["critical_path_json"])),
                active_directive_version=current["active_directive_version"],
                source_kind="ceo_run",
                source_id=run_id,
            )
        else:
            state_version = current["version"]
        for action in payload["actions"]:
            if action["type"] in {"noop", "update_state"}:
                action_results.append({"type": action["type"], "status": "recorded"})
            elif action["type"] == "create_task":
                existing = conn.execute("SELECT id FROM tasks WHERE title=?", (action["title"].strip(),)).fetchone()
                if existing:
                    action_results.append({"type": "create_task", "status": "already_exists", "task_id": existing["id"]})
                    continue
                lane = "product" if action["domain"] in {"product", "engineering"} else "commercial"
                active_domains = [
                    row["domain"]
                    for row in conn.execute(
                        "SELECT domain FROM tasks WHERE status IN ('open', 'in_progress', 'blocked')"
                    )
                ]
                active_lanes = {
                    "product" if domain in {"product", "engineering"} else "commercial"
                    for domain in active_domains
                }
                if lane in active_lanes:
                    action_results.append({
                        "type": "create_task",
                        "status": "wip_lane_occupied",
                        "lane": lane,
                    })
                    continue
                now = utcnow()
                active_phase = conn.execute(
                    "SELECT id, objective FROM strategic_phases WHERE status='active' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                cursor = conn.execute(
                    """INSERT INTO tasks(
                           created_at, updated_at, owner, title, domain, status, priority,
                           acceptance_criteria, strategic_phase_id, business_outcome
                       ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
                    (
                        now, now, action["owner"], action["title"].strip(), action["domain"],
                        action["priority"], action["acceptance_criteria"].strip(),
                        active_phase["id"] if active_phase else None,
                        f"推进战略阶段目标：{active_phase['objective']}" if active_phase else None,
                    ),
                )
                action_results.append({"type": "create_task", "status": "created", "task_id": int(cursor.lastrowid)})
            else:
                now = utcnow()
                summary = (
                    f"{action['summary']} | Options: {', '.join(action['options'])} | "
                    f"Recommendation: {action['recommendation']} | Risk: {action['risk']}"
                )
                cursor = conn.execute(
                    "INSERT INTO approvals(created_at, requested_by, action_type, summary, status) VALUES (?, 'CEO', ?, ?, 'pending')",
                    (now, action["action_type"], summary),
                )
                action_results.append({"type": "request_approval", "status": "pending", "approval_id": int(cursor.lastrowid)})
        self.store.audit(
            conn, "CEO", "apply_ceo_actions", "ceo_run", run_id,
            {"judgment": payload["judgment"], "actions": payload["actions"], "result": action_results},
        )
        return {"status": "applied", "state_version": state_version, "actions": action_results}

    def _append_state(
        self,
        conn,
        *,
        expected_version: int,
        strategy: dict[str, Any],
        assumptions: list[str],
        critical_path: list[str],
        active_directive_version: int,
        source_kind: str,
        source_id: Any,
    ) -> int:
        latest = conn.execute("SELECT version FROM ceo_state_versions ORDER BY version DESC LIMIT 1").fetchone()
        if latest is None or int(latest["version"]) != int(expected_version):
            raise VersionConflict("CEO state optimistic version conflict")
        version = int(expected_version) + 1
        conn.execute(
            """INSERT INTO ceo_state_versions(
                   version, schema_version, created_at, source_kind, source_id,
                   strategy_json, assumptions_json, critical_path_json,
                   active_directive_version
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                version, STATE_SCHEMA_VERSION, utcnow(), source_kind, str(source_id),
                _canonical(strategy), _canonical(assumptions), _canonical(critical_path), active_directive_version,
            ),
        )
        return version

    def _deliver_approval(self, approval_id: int) -> dict[str, Any]:
        with self.store.connect() as conn:
            approval = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if approval is None:
                return {"status": "missing_approval"}
            key = f"approval:{approval_id}:{_sha256(approval['summary'])[:16]}"
            row = conn.execute("SELECT * FROM approval_deliveries WHERE approval_id=?", (approval_id,)).fetchone()
            if row and row["status"] == "delivered":
                return {"status": "already_delivered", "idempotency_key": key}
            if row is None:
                conn.execute(
                    "INSERT INTO approval_deliveries(approval_id, idempotency_key, status, available_at) VALUES (?, ?, 'pending', ?)",
                    (approval_id, key, utcnow()),
                )
                attempts = 0
            else:
                attempts = int(row["attempts"])
        if not self.external_delivery_enabled:
            return {"status": "delivery_disabled", "idempotency_key": key, "external_delivery_enabled": False}
        card = (
            f"Weixin home CEO Decision Card #{approval_id}\n"
            f"Type: {approval['action_type']}\nDecision: {approval['summary']}\n"
            "Reply with: approve or deny, plus rationale. No external action is executed by this card."
        )
        try:
            result = self.sender.send(card, key)
        except Exception as exc:
            with self.store.connect() as conn:
                conn.execute(
                    """UPDATE approval_deliveries SET status='pending', attempts=?, available_at=?, last_error=?
                       WHERE approval_id=?""",
                    (attempts + 1, _retry_at(attempts + 1), str(exc), approval_id),
                )
            return {"status": "delivery_retry_scheduled", "idempotency_key": key, "error": str(exc)}
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE approval_deliveries SET status='delivered', attempts=?, delivered_at=?, delivery_ref=?, last_error=NULL
                   WHERE approval_id=?""",
                (attempts + 1, utcnow(), _canonical(result), approval_id),
            )
        return {"status": "delivered", "idempotency_key": key, "delivery": result}
