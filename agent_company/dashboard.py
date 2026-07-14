"""Read-only company operations dashboard."""

from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import CompanyConfig, load_config
from .models import ON_DEMAND_CAPABILITIES


PAGES = {
    "/management": "公司日常管理",
    "/project": "项目状态",
    "/operations": "产品运营",
    "/company": "公司介绍",
}

ROLE_LABELS = {
    "CEO": "首席执行官（CEO）",
    "Customer & Revenue": "客户与营收（Customer & Revenue）",
    "CPO": "首席产品官（CPO）",
    "CTO": "首席技术官（CTO）",
    "CRO": "首席营收官（CRO）",
    "COO": "首席运营官（COO）",
    "CFO": "首席财务官（CFO）",
    "Counsel": "法律顾问",
    "Product Engineer": "产品工程师",
    "AI Platform & Quality Engineer": "AI 平台与质量工程师",
}


@dataclass(frozen=True)
class Response:
    status: int
    content_type: str
    body: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _row_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(microsecond=0).isoformat()
    except FileNotFoundError:
        return None


def _git(args: list[str], workspace: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip()


def _read_doc(path: Path, max_lines: int = 16) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "available": False, "updated_at": None, "lines": []}
    lines = path.read_text(encoding="utf-8").splitlines()
    useful = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    return {
        "path": str(path),
        "available": True,
        "updated_at": _mtime(path),
        "lines": useful[:max_lines],
    }


def _artifact_evidence(config: CompanyConfig) -> list[dict[str, Any]]:
    if not config.artifacts_dir.exists():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(config.artifacts_dir.glob("*"), key=lambda item: item.stat().st_mtime, reverse=True)[:12]:
        if path.is_file():
            artifacts.append({
                "name": path.name,
                "path": str(path),
                "bytes": path.stat().st_size,
                "updated_at": _mtime(path),
            })
    return artifacts


def _test_status(config: CompanyConfig) -> dict[str, Any]:
    log_candidates = sorted(config.logs_dir.glob("*test*")) if config.logs_dir.exists() else []
    if log_candidates:
        latest = max(log_candidates, key=lambda item: item.stat().st_mtime)
        return {
            "state": "evidence_available",
            "label": "发现测试日志",
            "source": str(latest),
            "updated_at": _mtime(latest),
        }
    return {
        "state": "not_observed",
        "label": "未发现持久化测试日志",
        "source": str(config.logs_dir),
        "updated_at": _mtime(config.logs_dir),
    }


def _sqlite_snapshot(config: CompanyConfig) -> dict[str, Any]:
    empty = {
        "database": {"path": str(config.db_path), "available": False, "updated_at": None, "error": None},
        "tasks": [],
        "approvals": [],
        "cycles": [],
        "audit": [],
        "experiments": [],
        "roles": [],
        "raci": [],
        "task_executions": [],
        "token_usage": [],
        "strategic_phases": [],
    }
    if not config.db_path.exists():
        empty["database"]["error"] = "database file not found"
        return empty
    try:
        with _connect_readonly(config.db_path) as conn:
            task_execution_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_executions'"
            ).fetchone()
            token_usage_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='token_usage'"
            ).fetchone()
            strategic_phase_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strategic_phases'"
            ).fetchone()
            return {
                "database": {
                    "path": str(config.db_path),
                    "available": True,
                    "updated_at": _mtime(config.db_path),
                    "error": None,
                },
                "tasks": _row_dicts(list(conn.execute(
                    "SELECT * FROM tasks ORDER BY CASE status WHEN 'blocked' THEN 0 WHEN 'in_progress' THEN 1 WHEN 'open' THEN 2 ELSE 3 END, priority DESC, id ASC"
                ))),
                "approvals": _row_dicts(list(conn.execute(
                    "SELECT * FROM approvals ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC LIMIT 20"
                ))),
                "cycles": _row_dicts(list(conn.execute("SELECT * FROM cycles ORDER BY id DESC LIMIT 12"))),
                "audit": _row_dicts(list(conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 20"))),
                "experiments": _row_dicts(list(conn.execute("SELECT * FROM experiments ORDER BY id DESC LIMIT 12"))),
                "roles": _row_dicts(list(conn.execute("SELECT name, kind, mandate, status FROM roles ORDER BY name ASC"))),
                "raci": _row_dicts(list(conn.execute(
                    "SELECT domain, responsible, accountable, consulted, informed FROM raci ORDER BY domain ASC"
                ))),
                "task_executions": _row_dicts(list(conn.execute(
                    """SELECT e.*, t.status AS task_status, t.owner AS task_owner, t.title AS task_title
                       FROM task_executions e
                       JOIN tasks t ON t.id=e.task_id
                       ORDER BY CASE e.recovery_status WHEN 'failed' THEN 0 WHEN 'running' THEN 1 WHEN 'requeued' THEN 2 ELSE 3 END,
                                e.lease_expires_at ASC, e.task_id ASC"""
                ))) if task_execution_table else [],
                "token_usage": _row_dicts(list(conn.execute(
                    "SELECT * FROM token_usage ORDER BY ts DESC, id DESC"
                ))) if token_usage_table else [],
                "strategic_phases": _row_dicts(list(conn.execute(
                    "SELECT * FROM strategic_phases ORDER BY id DESC"
                ))) if strategic_phase_table else [],
            }
    except sqlite3.Error as exc:
        empty["database"]["error"] = str(exc)
        return empty


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "未设置")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _task_status_label(value: Any) -> str:
    mapping = {
        "open": "待处理",
        "in_progress": "进行中",
        "blocked": "阻塞",
        "done": "完成",
        "cancelled": "已取消",
    }
    return mapping.get(str(value), str(value))


def _recovery_status_label(value: Any) -> str:
    mapping = {
        "running": "运行中",
        "failed": "失败待恢复",
        "requeued": "已重新排队",
        "exhausted": "已耗尽",
        "completed": "已完成",
        "cancelled": "已取消",
    }
    return mapping.get(str(value), str(value))


def _role_label(value: Any) -> str:
    canonical = str(value)
    return ROLE_LABELS.get(canonical, canonical)


def _execution_health(rows: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    executions: list[dict[str, Any]] = []
    stale = 0
    for row in rows:
        enriched = dict(row)
        try:
            expires = datetime.fromisoformat(str(row.get("lease_expires_at")))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            is_stale = expires <= now and row.get("recovery_status") in {"running", "failed"}
        except ValueError:
            is_stale = True
        enriched["stale"] = is_stale
        stale += 1 if is_stale else 0
        executions.append(enriched)
    return {
        "executions": executions,
        "counts_by_recovery_status": _counts(rows, "recovery_status"),
        "stale_count": stale,
        "retry_attention_count": sum(
            1
            for row in rows
            if int(row.get("attempt_count") or 0) + 1 >= int(row.get("max_attempts") or 1)
            and row.get("recovery_status") in {"running", "failed", "exhausted"}
        ),
    }


def _token_usage_snapshot(conn: sqlite3.Connection, agents: list[str]) -> dict[str, Any]:
    if not _has_table(conn, "token_usage"):
        return {agent: {"agent": agent, "display_label": _role_label(agent), "status_label": "未采集"} for agent in agents}
    rows = _row_dicts(list(conn.execute("SELECT * FROM token_usage ORDER BY ts DESC, id DESC")))
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_agent.setdefault(str(row.get("agent")), []).append(row)
    snapshot: dict[str, Any] = {}
    for agent in agents:
        rows_for_agent = by_agent.get(agent, [])
        if not rows_for_agent:
            snapshot[agent] = {"agent": agent, "display_label": _role_label(agent), "status_label": "未采集"}
            continue
        cost_values = [float(row.get("cost")) for row in rows_for_agent if row.get("cost") is not None]
        totals = {
            key: sum(int(row.get(key) or 0) for row in rows_for_agent)
            for key in ["input_tokens", "output_tokens", "cache_tokens", "reasoning_tokens", "total_tokens"]
        }
        snapshot[agent] = {
            "agent": agent,
            "display_label": _role_label(agent),
            "status_label": "已采集",
            "record_count": len(rows_for_agent),
            **totals,
            "cost": sum(cost_values) if cost_values else None,
            "currency": next((row.get("currency") for row in rows_for_agent if row.get("currency")), None),
        }
    return snapshot


def _agent_workload(tasks: list[dict[str, Any]], executions: list[dict[str, Any]], agents: list[str], token_usage: dict[str, Any]) -> dict[str, Any]:
    outcomes: dict[str, dict[str, int]] = {
        agent: {"open": 0, "in_progress": 0, "blocked": 0, "done": 0, "cancelled": 0, "total": 0, "retries": 0, "failures": 0}
        for agent in agents
    }
    for task in tasks:
        owner = str(task.get("owner") or "")
        if owner not in outcomes:
            continue
        bucket = outcomes[owner]
        status = str(task.get("status") or "open")
        bucket[status] = bucket.get(status, 0) + 1
        bucket["total"] += 1
    for execution in executions:
        owner = str(execution.get("task_owner") or "")
        if owner not in outcomes:
            continue
        bucket = outcomes[owner]
        attempts = int(execution.get("attempt_count") or 0)
        bucket["retries"] += attempts
        if execution.get("recovery_status") in {"failed", "exhausted"}:
            bucket["failures"] += 1
    workload: dict[str, Any] = {}
    for agent in agents:
        bucket = outcomes[agent]
        completed = bucket.get("done", 0)
        total = bucket.get("total", 0)
        workload[agent] = {
            "agent": agent,
            "display_label": _role_label(agent),
            "status_label": "未采集" if total == 0 and not token_usage.get(agent, {}).get("record_count") else "已采集",
            "task_outcomes": {
                "open": bucket["open"],
                "in_progress": bucket["in_progress"],
                "blocked": bucket["blocked"],
                "done": bucket["done"],
                "cancelled": bucket["cancelled"],
                "total": total,
                "completion_rate": (completed / total) if total else None,
                "retries": bucket["retries"],
                "failures": bucket["failures"],
            },
            "token_usage": token_usage.get(agent, {"status_label": "未采集"}),
        }
    return workload


def _execution_timing(
    rows: list[dict[str, Any]], cycles: list[dict[str, Any]], agents: list[str]
) -> tuple[dict[str, Any], dict[str, float | None]]:
    now = datetime.now(timezone.utc)
    intervals: list[dict[str, Any]] = []
    cycle_durations: dict[str, dict[int, float]] = {agent: {} for agent in agents}
    parsed_cycles: list[tuple[int, datetime]] = []
    for cycle in cycles:
        try:
            parsed_cycles.append((int(cycle["id"]), datetime.fromisoformat(str(cycle["started_at"]))))
        except (KeyError, TypeError, ValueError):
            continue
    parsed_cycles.sort(key=lambda item: item[1])

    for row in rows:
        role = str(row.get("task_owner") or "")
        if role not in cycle_durations:
            continue
        try:
            start = datetime.fromisoformat(str(row["claimed_at"]))
            end_value = now if row.get("recovery_status") == "running" else datetime.fromisoformat(str(row["updated_at"]))
        except (KeyError, TypeError, ValueError):
            continue
        if end_value < start:
            continue
        duration = (end_value - start).total_seconds()
        cycle_id = next(
            (cycle_id for cycle_id, cycle_start in reversed(parsed_cycles) if cycle_start <= start),
            None,
        )
        intervals.append({
            "role": role,
            "display_label": _role_label(role),
            "task_id": row.get("task_id"),
            "task_title": row.get("task_title"),
            "start": start.isoformat(),
            "end": end_value.isoformat(),
            "duration_seconds": duration,
            "cycle_id": cycle_id,
        })
        if cycle_id is not None:
            cycle_durations[role][cycle_id] = cycle_durations[role].get(cycle_id, 0.0) + duration

    averages: dict[str, float | None] = {}
    for agent in agents:
        values = list(cycle_durations[agent].values())
        averages[agent] = (sum(values) / len(values)) if values else None
    timestamps = [datetime.fromisoformat(item[key]) for item in intervals for key in ("start", "end")]
    return {
        "intervals": sorted(intervals, key=lambda item: (item["start"], item["role"])),
        "range_start": min(timestamps).isoformat() if timestamps else None,
        "range_end": max(timestamps).isoformat() if timestamps else None,
    }, averages


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    if seconds < 3600:
        return f"{seconds / 60:.1f} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def _timeline_html(timeline: dict[str, Any], agents: list[dict[str, Any]]) -> str:
    intervals = timeline["intervals"]
    if not intervals:
        return '''<div class="timeline-controls">
          <label>时间轴缩放 <input id="timeline-zoom" type="range" min="1" max="8" step="0.5" value="1" disabled></label>
          <span>暂无可缩放时间范围</span>
        </div>
        <div class="timeline-viewport"><p class="empty">暂无任务执行时间记录</p></div>'''
    start = datetime.fromisoformat(timeline["range_start"])
    end = datetime.fromisoformat(timeline["range_end"])
    span = max((end - start).total_seconds(), 1.0)
    rows = []
    for agent in agents:
        role = agent["agent"]
        bars = []
        for item in intervals:
            if item["role"] != role:
                continue
            item_start = datetime.fromisoformat(item["start"])
            left = ((item_start - start).total_seconds() / span) * 100
            width = max((float(item["duration_seconds"]) / span) * 100, 0.25)
            title = f"任务 #{item['task_id']} · {_format_duration(float(item['duration_seconds']))} · {item['start']} 至 {item['end']}"
            bars.append(
                f'<span class="timeline-bar" style="left:{left:.4f}%;width:{width:.4f}%" title="{_escape(title)}"></span>'
            )
        rows.append(
            f'<div class="timeline-label">{_escape(agent["display_label"])}</div>'
            f'<div class="timeline-track">{"".join(bars)}</div>'
        )
    return f'''<div class="timeline-controls">
      <label>时间轴缩放 <input id="timeline-zoom" type="range" min="1" max="8" step="0.5" value="1"></label>
      <span>{_escape(timeline["range_start"])} 至 {_escape(timeline["range_end"])}</span>
    </div>
    <div class="timeline-viewport"><div class="timeline-canvas" id="timeline-canvas">
      <div class="timeline-grid">{"".join(rows)}</div>
    </div></div>
    <script>
      (() => {{
        const slider = document.getElementById('timeline-zoom');
        const canvas = document.getElementById('timeline-canvas');
        slider.addEventListener('input', () => {{ canvas.style.width = `${{Number(slider.value) * 100}}%`; }});
      }})();
    </script>'''


def _chart_svg(title: str, rows: list[dict[str, Any]], value_key: str, label_key: str) -> str:
    if not rows:
        return '<p class="empty">暂无记录</p>'
    width = 720
    bar_height = 28
    gap = 12
    left = 180
    top = 24
    chart_width = width - left - 32
    max_value = max(float(row.get(value_key) or 0) for row in rows) or 1.0
    height = top + len(rows) * (bar_height + gap) + 18
    bars = []
    for index, row in enumerate(rows):
        value = float(row.get(value_key) or 0)
        y = top + index * (bar_height + gap)
        bar_width = chart_width * (value / max_value)
        bars.append(
            f'<text x="0" y="{y + 19}" class="label">{_escape(row.get(label_key))}</text>'
            f'<rect x="{left}" y="{y}" width="{bar_width:.1f}" height="{bar_height}" rx="6"></rect>'
            f'<text x="{left + bar_width + 8:.1f}" y="{y + 19}" class="value">{_escape(int(value) if value.is_integer() else round(value, 2))}</text>'
        )
    return f"""
    <svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(title)}">
      <title>{_escape(title)}</title>
      <g>{''.join(bars)}</g>
    </svg>
    """


def build_snapshot(config: CompanyConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    sqlite_data = _sqlite_snapshot(config)
    tasks = sqlite_data["tasks"]
    approvals = sqlite_data["approvals"]
    resident_agents = [
        row["name"] for row in sqlite_data["roles"]
        if row.get("kind") == "agent" and row.get("status") == "resident"
    ]
    historical_task_agents = [
        str(task.get("owner")) for task in tasks
        if task.get("owner") and task.get("owner") not in resident_agents and task.get("owner") != "Chairman"
    ]
    agents = list(dict.fromkeys([*resident_agents, *historical_task_agents]))
    pending_approvals = [row for row in approvals if row.get("status") == "pending"]
    blocked_tasks = [row for row in tasks if row.get("status") == "blocked"]
    active_tasks = [row for row in tasks if row.get("status") in {"open", "in_progress", "blocked"}]
    recent_cycles = sqlite_data["cycles"]
    consecutive_empty_cycles = 0
    for cycle in recent_cycles:
        try:
            summary = json.loads(cycle.get("summary") or "{}")
        except json.JSONDecodeError:
            break
        if summary.get("processed") or summary.get("progressed") or summary.get("escalated"):
            break
        consecutive_empty_cycles += 1
    business_progress = "推进中" if active_tasks else ("停滞" if consecutive_empty_cycles >= 3 else "有风险")
    execution_health = _execution_health(sqlite_data["task_executions"])
    token_usage: dict[str, Any] = {agent: {"agent": agent, "display_label": _role_label(agent), "status_label": "未采集"} for agent in agents}
    workload: dict[str, Any] = {
        agent: {
            "agent": agent,
            "display_label": _role_label(agent),
            "status_label": "未采集",
            "task_outcomes": {
                "open": 0,
                "in_progress": 0,
                "blocked": 0,
                "done": 0,
                "cancelled": 0,
                "total": 0,
                "completion_rate": None,
                "retries": 0,
                "failures": 0,
            },
            "token_usage": {"status_label": "未采集"},
        }
        for agent in agents
    }
    if config.db_path.exists():
        with _connect_readonly(config.db_path) as conn:
            token_usage = _token_usage_snapshot(conn, agents)
            workload = _agent_workload(tasks, sqlite_data["task_executions"], agents, token_usage)
    execution_timeline, average_cycle_duration = _execution_timing(
        sqlite_data["task_executions"], sqlite_data["cycles"], agents
    )
    docs = {
        "roadmap": _read_doc(config.workspace / "docs" / "roadmap.md"),
        "kpis": _read_doc(config.workspace / "docs" / "kpis.md"),
        "architecture": _read_doc(config.workspace / "docs" / "architecture.md", max_lines=10),
        "strategy": _read_doc(config.workspace / "docs" / "strategy.md", max_lines=20),
        "org": _read_doc(config.workspace / "docs" / "org.md", max_lines=28),
        "raci": _read_doc(config.workspace / "docs" / "raci.md", max_lines=14),
        "cadence": _read_doc(config.workspace / "docs" / "cadence.md", max_lines=22),
        "local_beta_product": _read_doc(config.workspace / "docs" / "local-beta-product.md", max_lines=24),
        "versioning_records": _read_doc(config.workspace / "docs" / "versioning-and-records.md", max_lines=28),
        "constitution": _read_doc(config.workspace / "docs" / "constitution.md", max_lines=18),
    }
    git_head = _git(["rev-parse", "--short", "HEAD"], config.workspace)
    git_branch = _git(["branch", "--show-current"], config.workspace)
    git_status = _git(["status", "--short"], config.workspace)
    git_timestamp = _git(["log", "-1", "--format=%cI"], config.workspace)

    sources = [
        {
            "id": "sqlite",
            "label": "SQLite 运营账本",
            "path": str(config.db_path),
            "updated_at": sqlite_data["database"]["updated_at"],
            "available": sqlite_data["database"]["available"],
        },
        {
            "id": "git",
            "label": "Git 项目版本",
            "path": str(config.workspace / ".git"),
            "updated_at": git_timestamp,
            "available": git_head is not None,
        },
        {
            "id": "project_docs",
            "label": "项目文档",
            "path": str(config.workspace / "docs"),
            "updated_at": max((doc["updated_at"] for doc in docs.values() if doc["updated_at"]), default=None),
            "available": any(doc["available"] for doc in docs.values()),
        },
    ]

    return {
        "generated_at": _utcnow(),
        "product": config.product_name,
        "database": sqlite_data["database"],
        "sources": sources,
        "management": {
            "tasks": tasks,
            "task_counts_by_status": _counts(tasks, "status"),
            "task_counts_by_owner": _counts(tasks, "owner"),
            "display_labels": {
                "task_counts_by_status": "任务状态统计",
                "task_counts_by_owner": "负责人统计",
                "execution_health": "执行健康",
                "agent_workload": "Agent 工作负载",
                "token_usage": "Token 使用量",
            },
            "approvals": approvals,
            "cycles": sqlite_data["cycles"],
            "audit": sqlite_data["audit"],
            "execution_health": execution_health,
            "agent_workload": workload,
            "token_usage": token_usage,
            "strategic_phases": sqlite_data["strategic_phases"],
            "technical_health": "正常" if sqlite_data["database"]["available"] else "异常",
            "business_progress": business_progress,
            "consecutive_empty_cycles": consecutive_empty_cycles,
            "role_execution_timeline": execution_timeline,
            "average_execution_seconds_per_ceo_cycle": average_cycle_duration,
            "decisions": [row for row in approvals if row.get("status") in {"approved", "denied"}],
            "human_dependencies": [
                {
                    "approval_id": row.get("id"),
                    "requested_by": row.get("requested_by"),
                    "action_type": row.get("action_type"),
                    "summary": row.get("summary"),
                    "created_at": row.get("created_at"),
                }
                for row in pending_approvals
            ] + [
                {
                    "task_id": row.get("id"),
                    "owner": row.get("owner"),
                    "title": row.get("title"),
                    "blocked_reason": row.get("blocked_reason"),
                    "updated_at": row.get("updated_at"),
                }
                for row in blocked_tasks
            ],
        },
        "project": {
            "git": {
                "head": git_head,
                "branch": git_branch,
                "commit_time": git_timestamp,
                "dirty": bool(git_status),
                "status": git_status or "",
            },
            "roadmap": docs["roadmap"],
            "current_tasks": [row for row in tasks if row.get("status") in {"open", "in_progress", "blocked"}],
            "experiments": sqlite_data["experiments"],
            "artifact_evidence": _artifact_evidence(config),
            "test_validation": _test_status(config),
            "architecture": docs["architecture"],
        },
        "operations": {
            "launch_state": "pre_launch",
            "fields": [
                {
                    "label": "真实用户数",
                    "value": None,
                    "state": "placeholder",
                    "note": "产品未上线，暂无真实运营数据；不以 0 代替未知。",
                },
                {
                    "label": "激活率",
                    "value": None,
                    "state": "placeholder",
                    "note": "等待 beta 工作流上线并接入真实事件后展示。",
                },
                {
                    "label": "收入 / 毛利",
                    "value": None,
                    "state": "placeholder",
                    "note": "尚未发布定价或商业化结果，不能编造。",
                },
                {
                    "label": "客户支持队列",
                    "value": None,
                    "state": "placeholder",
                    "note": "未接入客户支持系统，当前仅展示占位状态。",
                },
            ],
            "kpi_definitions": docs["kpis"],
        },
        "company": {
            "static_sources": [
                "README.md",
                "docs/strategy.md",
                "docs/org.md",
                "docs/raci.md",
                "docs/cadence.md",
                "docs/local-beta-product.md",
                "docs/versioning-and-records.md",
                "docs/constitution.md",
            ],
            "mission": {
                "source": "docs/strategy.md",
                "lines": docs["strategy"]["lines"],
            },
            "operating_principles": {
                "source": "docs/constitution.md",
                "lines": docs["constitution"]["lines"],
            },
            "product": {
                "source": "docs/local-beta-product.md",
                "name": "织象 PixWeave",
                "lines": docs["local_beta_product"]["lines"],
                "status": "internal_local_beta",
            },
            "organization": {
                "source": "docs/org.md",
                "lines": docs["org"]["lines"],
            },
            "roles": [
                {**row, "source": "SQLite roles"}
                for row in sqlite_data["roles"]
                if row.get("status") == "resident"
            ],
            "historical_roles": [
                {**row, "source": "SQLite roles (historical compatibility)"}
                for row in sqlite_data["roles"]
                if row.get("status") == "historical"
            ],
            "on_demand_capabilities": [
                {"name": name, "mandate": mandate, "source": "agent_company.models"}
                for name, mandate in ON_DEMAND_CAPABILITIES.items()
            ],
            "raci": [
                {**row, "source": "SQLite raci"}
                for row in sqlite_data["raci"]
            ],
            "cadence": {
                "source": "docs/cadence.md",
                "lines": docs["cadence"]["lines"],
            },
            "governance": {
                "source": "docs/versioning-and-records.md",
                "lines": docs["versioning_records"]["lines"],
            },
        },
    }


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _json_response(payload: dict[str, Any], status: int = 200) -> Response:
    return Response(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _layout(title: str, active: str, content: str, snapshot: dict[str, Any]) -> str:
    nav = "\n".join(
        f'<a class="nav-link {"active" if path == active else ""}" href="{path}">{label}</a>'
        for path, label in PAGES.items()
    )
    sources = "".join(
        f"<li><span>{_escape(source['label'])}</span><code>{_escape(source['updated_at'] or '不可用')}</code></li>"
        for source in snapshot["sources"]
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_escape(title)} · {_escape(snapshot['product'])}</title>
  <style>{CSS}</style>
</head>
<body>
  <aside class="sidebar">
    <div class="brand">
      <span class="mark"></span>
      <div><strong>{_escape(snapshot['product'])}</strong><small>只读运营面板</small></div>
    </div>
    <nav>{nav}</nav>
    <a class="api" href="/api/status">状态接口</a>
  </aside>
  <main>
    <header class="topbar">
      <div>
        <p class="eyebrow">生成时间 {_escape(snapshot['generated_at'])}</p>
        <h1>{_escape(title)}</h1>
      </div>
      <div class="health">数据源：{"可读" if snapshot["database"]["available"] else "不可用"}</div>
    </header>
    {content}
    <section class="band provenance">
      <h2>来源与时间戳</h2>
      <ul>{sources}</ul>
      <p>本页只读取本地 SQLite、Git 和项目文件；未连接外部服务，未生成虚构指标。</p>
    </section>
  </main>
</body>
</html>"""


def _stat_cards(items: dict[str, int]) -> str:
    if not items:
        return '<p class="empty">暂无可读数据</p>'
    return "".join(f'<div class="stat"><span>{_escape(key)}</span><strong>{value}</strong></div>' for key, value in items.items())


def _metric_card(label: str, value: Any, detail: str = "") -> str:
    detail_html = f"<small>{_escape(detail)}</small>" if detail else ""
    return f'<div class="stat"><span>{_escape(label)}</span><strong>{_escape(value)}</strong>{detail_html}</div>'


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    if not rows:
        return '<p class="empty">暂无记录</p>'
    head = "".join(f"<th>{_escape(label)}</th>" for _, label in columns)
    body = ""
    for row in rows:
        cells = "".join(f"<td>{_escape(row.get(key))}</td>" for key, _ in columns)
        body += f"<tr>{cells}</tr>"
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _management(snapshot: dict[str, Any]) -> str:
    mgmt = snapshot["management"]
    dependencies = _table(mgmt["human_dependencies"], [
        ("approval_id", "审批"),
        ("task_id", "任务"),
        ("owner", "负责人"),
        ("action_type", "类型"),
        ("summary", "说明"),
        ("blocked_reason", "阻塞原因"),
    ])
    task_chart_data = [{"label": label, "value": count} for label, count in mgmt["task_counts_by_status"].items()]
    workload_rows = [
        {
            "label": data["display_label"],
            "value": int(data["task_outcomes"]["total"]),
            "completion_rate": 0 if data["task_outcomes"]["completion_rate"] is None else round(data["task_outcomes"]["completion_rate"] * 100, 1),
        }
        for data in mgmt["agent_workload"].values()
    ]
    token_rows = [
        {
            "label": data["display_label"],
            "value": int(data["token_usage"]["total_tokens"]),
        }
        for data in mgmt["agent_workload"].values()
        if data["token_usage"].get("status_label") == "已采集"
    ]
    average_duration_rows = [
        {
            "label": data["display_label"],
            "value": round(float(mgmt["average_execution_seconds_per_ceo_cycle"][agent]) / 60, 2),
        }
        for agent, data in mgmt["agent_workload"].items()
        if mgmt["average_execution_seconds_per_ceo_cycle"].get(agent) is not None
    ]
    timeline_agents = [
        {"agent": agent, "display_label": data["display_label"]}
        for agent, data in mgmt["agent_workload"].items()
    ]
    task_outcomes = _table(
        [
            {
                "agent": agent,
                "display_label": data["display_label"],
                "task_total": data["task_outcomes"]["total"],
                "completion_rate": "未采集" if data["task_outcomes"]["completion_rate"] is None else f"{round(data['task_outcomes']['completion_rate'] * 100, 1)}%",
                "retries": data["task_outcomes"]["retries"],
                "failures": data["task_outcomes"]["failures"],
                "open": data["task_outcomes"]["open"],
                "in_progress": data["task_outcomes"]["in_progress"],
                "blocked": data["task_outcomes"]["blocked"],
                "done": data["task_outcomes"]["done"],
                "cancelled": data["task_outcomes"]["cancelled"],
            }
            for agent, data in mgmt["agent_workload"].items()
        ],
        [
            ("agent", "ID"),
            ("display_label", "名称"),
            ("task_total", "任务总数"),
            ("completion_rate", "完成率"),
            ("retries", "重试"),
            ("failures", "失败"),
            ("open", "待处理"),
            ("in_progress", "进行中"),
            ("blocked", "阻塞"),
            ("done", "完成"),
            ("cancelled", "已取消"),
        ],
    )
    tasks_table = _table(
        [
            {
                **row,
                "status": _task_status_label(row.get("status")),
            }
            for row in mgmt["tasks"]
        ],
        [("id", "ID"), ("status", "状态"), ("owner", "负责人"), ("domain", "领域"), ("priority", "优先级"), ("title", "任务")],
    )
    executions_table = _table(
        [
            {
                **row,
                "task_status": _task_status_label(row.get("task_status")),
                "recovery_status": _recovery_status_label(row.get("recovery_status")),
            }
            for row in mgmt["execution_health"]["executions"]
        ],
        [("task_id", "任务"), ("task_status", "任务状态"), ("recovery_status", "恢复状态"), ("executor_id", "执行器"), ("backend", "后端"), ("lease_expires_at", "租约到期"), ("attempt_count", "尝试"), ("max_attempts", "上限"), ("checkpoint", "检查点"), ("next_action", "下一步"), ("last_error", "最后错误")],
    )
    workload_table = _table(
        [
            {
                "agent": agent,
                "display_label": data["display_label"],
                "status_label": data["status_label"],
                "total_tokens": "未采集" if data["token_usage"].get("status_label") == "未采集" else data["token_usage"].get("total_tokens"),
                "input_tokens": "未采集" if data["token_usage"].get("status_label") == "未采集" else data["token_usage"].get("input_tokens"),
                "output_tokens": "未采集" if data["token_usage"].get("status_label") == "未采集" else data["token_usage"].get("output_tokens"),
                "cache_tokens": "未采集" if data["token_usage"].get("status_label") == "未采集" else data["token_usage"].get("cache_tokens"),
                "reasoning_tokens": "未采集" if data["token_usage"].get("status_label") == "未采集" else data["token_usage"].get("reasoning_tokens"),
            }
            for agent, data in mgmt["agent_workload"].items()
        ],
        [
            ("agent", "ID"),
            ("display_label", "名称"),
            ("status_label", "采集状态"),
            ("input_tokens", "输入"),
            ("output_tokens", "输出"),
            ("cache_tokens", "缓存"),
            ("reasoning_tokens", "推理"),
            ("total_tokens", "总计"),
        ],
    )
    return f"""
    <section class="grid stats">
      {_metric_card("技术运行状态", mgmt["technical_health"])}
      {_metric_card("业务推进状态", mgmt["business_progress"])}
      {_metric_card("连续空周期", mgmt["consecutive_empty_cycles"])}
      {_metric_card("战略阶段", mgmt["strategic_phases"][0]["name"] if mgmt["strategic_phases"] else "未规划")}
    </section>
    <section class="grid stats">
      <div class="chart-card">
        <h3>任务状态图</h3>
        {_chart_svg("任务状态图", task_chart_data, "value", "label")}
      </div>
      <div class="chart-card">
        <h3>Agent 完成率图</h3>
        {_chart_svg("Agent 完成率图", workload_rows, "completion_rate", "label")}
      </div>
      <div class="chart-card">
        <h3>Token 使用量图</h3>
        {_chart_svg("Token 使用量图", token_rows, "value", "label") if token_rows else '<p class="empty">Token 使用量未采集</p>'}
      </div>
    </section>
    <section class="band">
      <h2>战略阶段与业务目标</h2>
      {_table(mgmt["strategic_phases"], [("id", "ID"), ("name", "阶段"), ("status", "状态"), ("objective", "业务目标"), ("success_metrics", "成功指标"), ("deadline", "截止时间"), ("dependencies", "依赖")])}
    </section>
    <section class="band">
      <h2>角色任务执行时间轴</h2>
      <p class="legend">横轴为时间，纵轴为角色；拖动滑块可放大时间轴，横向滚动查看细节。</p>
      {_timeline_html(mgmt["role_execution_timeline"], timeline_agents)}
    </section>
    <section class="band">
      <h2>每个 CEO 周期平均执行时长</h2>
      <p class="legend">按角色统计有执行记录的 CEO 周期内任务执行总时长平均值，单位：分钟。</p>
      {_chart_svg("每个 CEO 周期平均执行时长", average_duration_rows, "value", "label") if average_duration_rows else '<p class="empty">暂无可归属到 CEO 周期的执行记录</p>'}
    </section>
    <section class="band">
      <h2>执行健康</h2>
      <div class="grid stats">
        {_metric_card("过期租约", mgmt["execution_health"]["stale_count"])}
        {_metric_card("重试关注", mgmt["execution_health"]["retry_attention_count"])}
      </div>
      <div class="legend">显示所有已注册 agent；未采集的 token 以“未采集”显示，不以 0 代替。</div>
      {executions_table}
    </section>
    <section class="band">
      <h2>任务状态 / 负责人</h2>
      <div class="split">
        <div>{tasks_table}</div>
        <div class="owner-list">{_stat_cards(mgmt["task_counts_by_owner"])}</div>
      </div>
    </section>
    <section class="band"><h2>审批与人类依赖</h2>{dependencies}</section>
    <section class="band"><h2>周期</h2>{_table(mgmt["cycles"], [("id", "ID"), ("started_at", "开始"), ("finished_at", "结束"), ("summary", "摘要")])}</section>
    <section class="band"><h2>审计 / 决策</h2>{_table(mgmt["audit"], [("id", "ID"), ("ts", "时间"), ("actor", "角色"), ("action", "动作"), ("entity", "对象"), ("entity_id", "对象 ID")])}</section>
    <section class="band"><h2>Agent 工作负载</h2>{workload_table}</section>
    <section class="band"><h2>任务结果汇总</h2>{task_outcomes}</section>
    """


def _project(snapshot: dict[str, Any]) -> str:
    project = snapshot["project"]
    git = project["git"]
    roadmap = "".join(f"<li>{_escape(line)}</li>" for line in project["roadmap"]["lines"])
    artifacts = _table(project["artifact_evidence"], [("name", "文件"), ("bytes", "字节"), ("updated_at", "更新时间"), ("path", "路径")])
    validation = project["test_validation"]
    return f"""
    <section class="grid stats">
      <div class="stat"><span>Git 版本</span><strong>{_escape(git["head"] or "不可用")}</strong><small>{_escape(git["branch"] or "")}</small></div>
      <div class="stat"><span>工作区状态</span><strong>{"有未提交变更" if git["dirty"] else "干净"}</strong><small>{_escape(git["commit_time"] or "无提交时间")}</small></div>
      <div class="stat"><span>验证状态</span><strong>{_escape(validation["label"])}</strong><small>{_escape(validation["source"])}</small></div>
    </section>
    <section class="band"><h2>路线图</h2><ul class="roadmap">{roadmap or '<li>暂无路线图条目</li>'}</ul></section>
    <section class="band"><h2>当前任务</h2>{_table(project["current_tasks"], [("id", "ID"), ("status", "状态"), ("owner", "负责人"), ("domain", "领域"), ("title", "任务"), ("acceptance_criteria", "验收标准")])}</section>
    <section class="band"><h2>实验</h2>{_table(project["experiments"], [("id", "ID"), ("status", "状态"), ("owner", "负责人"), ("name", "实验"), ("metric", "指标"), ("result", "结果")])}</section>
    <section class="band"><h2>产物 / 构建证据</h2>{artifacts}</section>
    """


def _operations(snapshot: dict[str, Any]) -> str:
    fields = "".join(
        f"""<article class="placeholder">
          <span>{_escape(field['label'])}</span>
          <strong>尚未上线</strong>
          <p>{_escape(field['note'])}</p>
        </article>"""
        for field in snapshot["operations"]["fields"]
    )
    kpis = "".join(f"<li>{_escape(line)}</li>" for line in snapshot["operations"]["kpi_definitions"]["lines"])
    return f"""
    <section class="band launch">
      <h2>上线前占位</h2>
      <p>当前状态为上线前。所有未接入或未发生的运营字段显示为占位，不显示为 0。</p>
      <div class="placeholder-grid">{fields}</div>
    </section>
    <section class="band"><h2>KPI 定义来源</h2><ul class="roadmap">{kpis or '<li>暂无 KPI 定义</li>'}</ul></section>
    """


def _doc_list(section: dict[str, Any], fallback: str) -> str:
    items = section.get("lines") or [fallback]
    return "".join(f"<li>{_escape(line)}</li>" for line in items)


def _source_label(source: str) -> str:
    return f'<p class="source">来源: {_escape(source)}</p>'


def _company(snapshot: dict[str, Any]) -> str:
    company = snapshot["company"]
    mission = _doc_list(company["mission"], "使命说明缺失；请补充 docs/strategy.md。")
    principles = _doc_list(company["operating_principles"], "运营原则缺失；请补充 docs/constitution.md。")
    product = _doc_list(company["product"], "产品说明缺失；请补充 docs/local-beta-product.md。")
    org_lines = _doc_list(company["organization"], "组织说明缺失；请补充 docs/org.md。")
    cadence = _doc_list(
        company["cadence"],
        "CEO 每 30 分钟恢复运营；08:00、13:00、20:00 向 Chairman 汇报，详见 docs/cadence.md。",
    )
    governance = _doc_list(company["governance"], "治理说明缺失；请补充 docs/versioning-and-records.md。")
    roles = _table(company["roles"], [
        ("name", "角色"),
        ("kind", "类型"),
        ("mandate", "职责 / 授权边界"),
        ("source", "来源"),
    ])
    historical_roles = _table(company["historical_roles"], [
        ("name", "历史角色"),
        ("status", "状态"),
        ("source", "来源"),
    ])
    on_demand = _table(company["on_demand_capabilities"], [
        ("name", "按需能力"),
        ("mandate", "用途 / 边界"),
        ("source", "来源"),
    ])
    raci = _table(company["raci"], [
        ("domain", "领域"),
        ("responsible", "R"),
        ("accountable", "A"),
        ("consulted", "C"),
        ("informed", "I"),
        ("source", "来源"),
    ])
    sources = "".join(f"<li>{_escape(source)}</li>" for source in company["static_sources"])
    return f"""
    <section class="band">
      <h2>使命</h2>
      <ul class="roadmap">{mission}</ul>
      {_source_label(company["mission"]["source"])}
    </section>
    <section class="band">
      <h2>真实商业运营原则</h2>
      <ul class="roadmap">{principles}</ul>
      {_source_label(company["operating_principles"]["source"])}
    </section>
    <section class="band">
      <h2>当前产品 PixWeave</h2>
      <p class="lede">状态：内部本地 beta。({_escape(company["product"]["status"])}) 本区只汇总仓库文档描述，不声明外部上线、收入或客户结果。</p>
      <ul class="roadmap">{product}</ul>
      {_source_label(company["product"]["source"])}
    </section>
    <section class="band">
      <h2>组织图</h2>
      <div class="org-chart">
        <div class="org-node human">Chairman<br><small>唯一人类 / 保留决策</small></div>
        <div class="org-node">CEO<br><small>执行协调 / 决策流</small></div>
        <div class="org-row">
          <div class="org-node">Product Engineer<br><small>产品交付 / 质量证据</small></div>
          <div class="org-node">客户与营收（Customer &amp; Revenue）<br><small>客户 / GTM / 营收运营</small></div>
        </div>
      </div>
      <ul class="roadmap">{org_lines}</ul>
      {_source_label(company["organization"]["source"])}
    </section>
    <section class="band"><h2>SQLite 常驻角色与职责</h2>{roles}<p class="source">来源：SQLite roles</p></section>
    <section class="band"><h2>按需能力</h2>{on_demand}<p class="source">来源：agent_company.models</p></section>
    <section class="band"><h2>历史角色兼容记录</h2><p class="lede">仅为保留历史任务 owner 与账本关联，不属于当前常驻组织。</p>{historical_roles}</section>
    <section class="band"><h2>RACI / 协作关系</h2>{raci}<p class="source">来源：SQLite raci</p></section>
    <section class="band">
      <h2>CEO 30 分钟节奏与 08/13/20 汇报</h2>
      <ul class="roadmap">{cadence}</ul>
      {_source_label(company["cadence"]["source"])}
    </section>
    <section class="band">
      <h2>Chairman 保留决策 / 人类依赖</h2>
      <p class="lede">真实客户外联、定价承诺、付款与预算、合同和法律承诺、公开发布、生产部署及不可逆动作必须等待 Chairman 决策；人类阻塞项从 live approvals/tasks 在管理页展示。</p>
      {_source_label("docs/org.md")}
    </section>
    <section class="band">
      <h2>Codex 异步资源</h2>
      <p class="lede">Codex workers 是按需能力，条件是工作有边界、可审查、可验证；它们不是常驻角色、部门或决策者。</p>
      {_source_label("docs/org.md")}
    </section>
    <section class="band">
      <h2>证据 / 版本 / 归档治理</h2>
      <ul class="roadmap">{governance}</ul>
      {_source_label(company["governance"]["source"])}
    </section>
    <section class="band">
      <h2>静态说明来源</h2>
      <ul class="roadmap">{sources}</ul>
    </section>
    """


class DashboardApp:
    def __init__(self, config: CompanyConfig):
        self.config = config

    def render_path(self, path: str) -> Response:
        parsed = urlparse(path)
        route = parsed.path
        if route == "/":
            return Response(302, "text/plain; charset=utf-8", "/management")
        snapshot = build_snapshot(self.config)
        if route == "/healthz":
            return _json_response({
                "ok": snapshot["database"]["available"],
                "generated_at": snapshot["generated_at"],
                "database": snapshot["database"],
            }, 200 if snapshot["database"]["available"] else 503)
        if route == "/api/status":
            return _json_response(snapshot)
        if route == "/management":
            return Response(200, "text/html; charset=utf-8", _layout(PAGES[route], route, _management(snapshot), snapshot))
        if route == "/project":
            return Response(200, "text/html; charset=utf-8", _layout(PAGES[route], route, _project(snapshot), snapshot))
        if route == "/operations":
            return Response(200, "text/html; charset=utf-8", _layout(PAGES[route], route, _operations(snapshot), snapshot))
        if route == "/company":
            return Response(200, "text/html; charset=utf-8", _layout(PAGES[route], route, _company(snapshot), snapshot))
        return Response(404, "text/plain; charset=utf-8", "not found\n")


class DashboardHandler(BaseHTTPRequestHandler):
    app: DashboardApp

    def do_GET(self) -> None:
        response = self.app.render_path(self.path)
        if response.status == 302:
            self.send_response(302)
            self.send_header("Location", response.body)
            self.end_headers()
            return
        encoded = response.body.encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")


def serve(config: CompanyConfig, host: str = "0.0.0.0", port: int = 18080) -> None:
    app = DashboardApp(config)

    class BoundHandler(DashboardHandler):
        pass

    BoundHandler.app = app
    server = ThreadingHTTPServer((host, port), BoundHandler)
    print(f"dashboard listening on http://{host}:{port}", flush=True)
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only company operations dashboard")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default=os.environ.get("AGENT_COMPANY_DASHBOARD_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_COMPANY_DASHBOARD_PORT", "18080")))
    args = parser.parse_args(argv)
    serve(load_config(args.config), args.host, args.port)
    return 0


CSS = """
:root {
  color-scheme: dark;
  --bg: #08090b;
  --panel: #101215;
  --panel-2: #15181d;
  --line: #252a33;
  --text: #f3f5f7;
  --muted: #9aa3ad;
  --accent: #5dd6b4;
  --accent-2: #e7c66b;
  --danger: #ff7a90;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-height: 100vh;
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
a { color: inherit; text-decoration: none; }
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  padding: 22px 16px;
  border-right: 1px solid var(--line);
  background: #0b0d10;
}
.brand { display: flex; gap: 12px; align-items: center; margin-bottom: 28px; }
.brand strong { display: block; font-size: 15px; }
.brand small { display: block; color: var(--muted); margin-top: 2px; }
.mark { width: 28px; height: 28px; border-radius: 7px; background: linear-gradient(135deg, var(--accent), var(--accent-2)); }
nav { display: grid; gap: 6px; }
.nav-link, .api {
  display: block;
  padding: 10px 12px;
  border-radius: 7px;
  color: var(--muted);
}
.nav-link.active, .nav-link:hover, .api:hover { background: var(--panel-2); color: var(--text); }
.api { position: absolute; bottom: 18px; left: 16px; right: 16px; border: 1px solid var(--line); }
main { min-width: 0; padding: 28px; }
.topbar {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-start;
  margin-bottom: 22px;
}
.eyebrow { color: var(--muted); margin: 0 0 4px; font-size: 12px; }
h1 { margin: 0; font-size: 28px; letter-spacing: 0; }
h2 { margin: 0 0 14px; font-size: 16px; letter-spacing: 0; }
.chart-card {
  min-width: 0;
  padding: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.chart-card h3 { margin: 0 0 10px; font-size: 14px; color: var(--muted); font-weight: 600; }
.chart {
  width: 100%;
  height: auto;
  display: block;
}
.chart text { font: 12px ui-sans-serif, system-ui, sans-serif; fill: var(--text); }
.chart .label { fill: var(--muted); }
.chart rect { fill: var(--accent); }
.legend { color: var(--muted); margin: 8px 0 14px; }
.timeline-controls { display: flex; justify-content: space-between; gap: 16px; align-items: center; color: var(--muted); margin-bottom: 12px; }
.timeline-controls label { display: flex; align-items: center; gap: 10px; }
.timeline-controls input { width: min(260px, 35vw); }
.timeline-viewport { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
.timeline-canvas { min-width: 100%; padding: 10px 12px; transition: width 120ms ease; }
.timeline-grid { display: grid; grid-template-columns: 190px minmax(600px, 1fr); gap: 7px 12px; align-items: center; }
.timeline-label { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.timeline-track { position: relative; height: 24px; border-radius: 5px; background: repeating-linear-gradient(90deg, var(--panel-2) 0, var(--panel-2) calc(10% - 1px), var(--line) 10%); }
.timeline-bar { position: absolute; top: 4px; height: 16px; min-width: 3px; border-radius: 4px; background: var(--accent); cursor: help; }
.health { border: 1px solid var(--line); border-radius: 7px; padding: 8px 10px; color: var(--accent); white-space: nowrap; }
.grid { display: grid; gap: 12px; }
.stats { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); margin-bottom: 14px; }
.stat {
  min-height: 88px;
  padding: 14px;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.stat span, .placeholder span { display: block; color: var(--muted); margin-bottom: 6px; }
.stat strong { font-size: 24px; letter-spacing: 0; }
.stat small { display: block; color: var(--muted); margin-top: 4px; overflow-wrap: anywhere; }
.band {
  margin-top: 14px;
  padding: 18px;
  border-top: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0));
}
.split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(180px, 260px); gap: 14px; align-items: start; }
.owner-list { display: grid; gap: 10px; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { width: 100%; border-collapse: collapse; min-width: 760px; }
th, td { padding: 10px 12px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
th { color: var(--muted); font-weight: 600; background: var(--panel); }
td { color: #d9dee4; overflow-wrap: anywhere; }
tr:last-child td { border-bottom: 0; }
.empty { color: var(--muted); margin: 0; }
.roadmap { margin: 0; padding-left: 18px; color: #d9dee4; }
.placeholder-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; margin-top: 14px; }
.placeholder {
  padding: 14px;
  background: var(--panel);
  border: 1px dashed #3a414d;
  border-radius: 8px;
}
.placeholder strong { color: var(--accent-2); font-size: 18px; }
.placeholder p, .launch p, .provenance p, .lede, .source { color: var(--muted); margin: 8px 0 0; }
.provenance ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
.provenance li { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
.source { font-size: 12px; }
.org-chart { display: grid; gap: 10px; margin-bottom: 16px; }
.org-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; }
.org-row.compact { max-width: 520px; margin: 0 auto; }
.org-node {
  text-align: center;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
}
.org-node.human { border-color: var(--accent-2); }
.org-node.cto { border-color: var(--accent); }
.org-node small { color: var(--muted); }
code { color: var(--accent); overflow-wrap: anywhere; }
@media (max-width: 820px) {
  body { display: block; }
  .sidebar { position: relative; height: auto; border-right: 0; border-bottom: 1px solid var(--line); }
  .api { position: static; margin-top: 12px; }
  main { padding: 18px; }
  .topbar, .split, .provenance li { display: block; }
  .health { margin-top: 12px; display: inline-block; }
}
"""


if __name__ == "__main__":
    raise SystemExit(main())
