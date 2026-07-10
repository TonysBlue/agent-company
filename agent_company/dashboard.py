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


PAGES = {
    "/management": "公司日常管理",
    "/project": "产品 / 项目状态",
    "/operations": "产品运营",
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
    }
    if not config.db_path.exists():
        empty["database"]["error"] = "database file not found"
        return empty
    try:
        with _connect_readonly(config.db_path) as conn:
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


def build_snapshot(config: CompanyConfig | None = None) -> dict[str, Any]:
    config = config or load_config()
    sqlite_data = _sqlite_snapshot(config)
    tasks = sqlite_data["tasks"]
    approvals = sqlite_data["approvals"]
    pending_approvals = [row for row in approvals if row.get("status") == "pending"]
    blocked_tasks = [row for row in tasks if row.get("status") == "blocked"]
    docs = {
        "roadmap": _read_doc(config.workspace / "docs" / "roadmap.md"),
        "kpis": _read_doc(config.workspace / "docs" / "kpis.md"),
        "architecture": _read_doc(config.workspace / "docs" / "architecture.md", max_lines=10),
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
            "approvals": approvals,
            "cycles": sqlite_data["cycles"],
            "audit": sqlite_data["audit"],
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
      <div><strong>{_escape(snapshot['product'])}</strong><small>只读运营仪表盘</small></div>
    </div>
    <nav>{nav}</nav>
    <a class="api" href="/api/status">JSON API</a>
  </aside>
  <main>
    <header class="topbar">
      <div>
        <p class="eyebrow">生成时间 {_escape(snapshot['generated_at'])}</p>
        <h1>{_escape(title)}</h1>
      </div>
      <div class="health">数据源: {"可读" if snapshot["database"]["available"] else "不可用"}</div>
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
    return f"""
    <section class="grid stats">
      {_stat_cards(mgmt["task_counts_by_status"])}
    </section>
    <section class="band">
      <h2>任务状态 / 负责人</h2>
      <div class="split">
        <div>{_table(mgmt["tasks"], [("id", "ID"), ("status", "状态"), ("owner", "负责人"), ("domain", "领域"), ("priority", "优先级"), ("title", "任务")])}</div>
        <div class="owner-list">{_stat_cards(mgmt["task_counts_by_owner"])}</div>
      </div>
    </section>
    <section class="band"><h2>审批与人类依赖</h2>{dependencies}</section>
    <section class="band"><h2>周期</h2>{_table(mgmt["cycles"], [("id", "ID"), ("started_at", "开始"), ("finished_at", "结束"), ("summary", "摘要")])}</section>
    <section class="band"><h2>审计 / 决策</h2>{_table(mgmt["audit"], [("id", "ID"), ("ts", "时间"), ("actor", "角色"), ("action", "动作"), ("entity", "对象"), ("entity_id", "对象 ID")])}</section>
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
      <p>当前状态为 pre_launch。所有未接入或未发生的运营字段显示为占位，不显示为 0。</p>
      <div class="placeholder-grid">{fields}</div>
    </section>
    <section class="band"><h2>KPI 定义来源</h2><ul class="roadmap">{kpis or '<li>暂无 KPI 定义</li>'}</ul></section>
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
.placeholder p, .launch p, .provenance p { color: var(--muted); margin: 8px 0 0; }
.provenance ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
.provenance li { display: flex; justify-content: space-between; gap: 12px; border-bottom: 1px solid var(--line); padding-bottom: 8px; }
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
