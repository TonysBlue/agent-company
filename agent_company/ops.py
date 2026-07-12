"""Company operating cycle."""

from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

from .config import CompanyConfig
from .db import Store, utcnow
from .governance import DISCLAIMER, classify_reserved_action


class CompanyOS:
    def __init__(self, config: CompanyConfig):
        self.config = config
        self.store = Store(config.db_path)

    def init(self) -> None:
        for path in [self.config.chairman_inbox, self.config.chairman_outbox, self.config.artifacts_dir, self.config.logs_dir]:
            path.mkdir(parents=True, exist_ok=True)
        self.store.init()

    def status(self) -> dict[str, object]:
        self.init()
        open_tasks = self.store.fetch_one("SELECT COUNT(*) AS c FROM tasks WHERE status='open'")["c"]
        in_progress = self.store.fetch_one("SELECT COUNT(*) AS c FROM tasks WHERE status='in_progress'")["c"]
        blocked = self.store.fetch_one("SELECT COUNT(*) AS c FROM tasks WHERE status='blocked'")["c"]
        approvals = self.store.fetch_one("SELECT COUNT(*) AS c FROM approvals WHERE status='pending'")["c"]
        cycles = self.store.fetch_one("SELECT COUNT(*) AS c FROM cycles")["c"]
        active_phase = self.store.fetch_one("SELECT * FROM strategic_phases WHERE status='active' ORDER BY id DESC LIMIT 1")
        recent = self.store.fetch_all("SELECT summary FROM cycles ORDER BY id DESC LIMIT 12")
        consecutive_empty = 0
        for row in recent:
            try:
                summary = json.loads(row["summary"])
            except (TypeError, json.JSONDecodeError):
                break
            if int(summary.get("processed", 0)) or summary.get("progressed") or summary.get("escalated"):
                break
            consecutive_empty += 1
        active_count = open_tasks + in_progress + blocked
        business_progress = "advancing" if active_count else ("stalled" if consecutive_empty >= 3 else "at_risk")
        return {
            "product": self.config.product_name,
            "open_tasks": open_tasks,
            "in_progress_tasks": in_progress,
            "active_tasks": active_count,
            "blocked_tasks": blocked,
            "pending_approvals": approvals,
            "cycles": cycles,
            "technical_health": "healthy",
            "business_progress": business_progress,
            "consecutive_empty_cycles": consecutive_empty,
            "active_strategic_phase": dict(active_phase) if active_phase else None,
            "disclaimer": DISCLAIMER,
        }

    def run_cycle(self) -> dict[str, object]:
        self.init()
        with self.store.connect() as conn:
            started = utcnow()
            cur = conn.execute("INSERT INTO cycles(started_at, summary) VALUES (?, ?)", (started, "running"))
            cycle_id = cur.lastrowid
            recovery = self._recover_stale_executions(conn, "CEO", "cycle lease inspection")
            recovered = [item["task_id"] for item in recovery if item["status"] == "open"]
            exhausted = [item["task_id"] for item in recovery if item["status"] == "blocked"]
            if recovery:
                tasks = []
            else:
                tasks = list(
                    conn.execute(
                        "SELECT * FROM tasks WHERE status='open' ORDER BY priority DESC, id ASC LIMIT ?",
                        (self.config.cycle_task_limit,),
                    )
                )
            progressed: list[int] = []
            escalated: list[int] = []
            for task in tasks:
                reserved = classify_reserved_action(f"{task['title']} {task['domain']}", self.config)
                if reserved and self._has_approved_action(conn, task["id"], reserved):
                    reserved = None
                if reserved:
                    approval_id = self._create_approval(
                        conn,
                        requested_by=task["owner"],
                        action_type=reserved,
                        summary=f"Task {task['id']} requires Chairman decision before continuing: {task['title']}",
                    )
                    conn.execute(
                        "UPDATE tasks SET status='blocked', updated_at=?, blocked_reason=? WHERE id=?",
                        (utcnow(), f"Pending Chairman approval #{approval_id}", task["id"]),
                    )
                    escalated.append(task["id"])
                    continue
                details = self._claim_task_execution(
                    conn,
                    task,
                    actor="CEO",
                    executor_id=f"cycle-{cycle_id}-task-{task['id']}",
                    backend="cycle",
                    lease_seconds=600,
                )
                self.store.audit(conn, "CEO", "dispatch_task", "task", task["id"], {"owner": task["owner"]})
                self.store.audit(conn, "CEO", "claim_task_execution", "task_execution", task["id"], details)
                progressed.append(task["id"])
            planned_phase_id = self._ensure_strategic_horizon(conn)
            self._ensure_backlog(conn)
            self._record_metrics(conn)
            summary = {
                "progressed": progressed,
                "escalated": escalated,
                "recovered": recovered,
                "recovery_exhausted": exhausted,
                "processed": len(tasks),
                "planned_phase_id": planned_phase_id,
            }
            conn.execute("UPDATE cycles SET finished_at=?, summary=? WHERE id=?", (utcnow(), json.dumps(summary, sort_keys=True), cycle_id))
            self.store.audit(conn, "CEO", "run_cycle", "cycle", cycle_id, summary)
            return {"cycle_id": cycle_id, **summary}

    def _perform_task(self, backend, task) -> dict[str, str]:
        title = task["title"]
        domain = task["domain"]
        if domain in {"product", "engineering"}:
            return backend.generate(f"{self.config.product_name}: {title}", mode="edit" if "editing" in title.lower() else "generate")
        return {
            "summary": f"{task['owner']} produced an internal {domain} operating artifact for {self.config.product_name}.",
            "next": "Review in weekly operating cadence.",
        }

    def _spawn_followups(self, conn, task, result: dict[str, str]) -> None:
        followups = {
            "product": ("CTO", "Validate prototype workflow against product requirements", "engineering", 60),
            "engineering": ("CPO", "Prepare internal beta workflow checklist", "product", 58),
            "gtm": ("CRO", "Draft landing-page copy for Chairman review before public launch", "gtm", 55),
            "finance": ("CFO", "Refine gross margin assumptions with usage scenarios", "finance", 50),
            "operations": ("COO", "Update KPI dashboard definitions", "operations", 48),
            "risk": ("Counsel", "Maintain human-control compliance checklist", "risk", 45),
        }
        if task["domain"] not in followups:
            return
        owner, title, domain, priority = followups[task["domain"]]
        # Completed recurring titles are not useful new work; roadmap replenishment
        # below creates distinct, measurable tasks instead.
        exists = conn.execute("SELECT 1 FROM tasks WHERE title=?", (title,)).fetchone()
        if exists:
            return
        now = utcnow()
        conn.execute(
            """INSERT INTO tasks(
                   created_at, updated_at, owner, title, domain, status, priority,
                   acceptance_criteria
               ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
            (
                now,
                now,
                owner,
                title,
                domain,
                priority,
                "Produce a reviewable artifact, verify it against the parent task result, and attach the evidence path before completion.",
            ),
        )

    def _ensure_strategic_horizon(self, conn) -> int | None:
        """Activate one reviewed commercial phase before the active queue is exhausted."""
        active = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('open', 'in_progress', 'blocked')"
        ).fetchone()[0]
        low_water = max(2, self.config.cycle_task_limit // 2)
        if active > low_water:
            return None
        existing = conn.execute(
            "SELECT id FROM strategic_phases WHERE phase_key='controlled-beta-validation-v1'"
        ).fetchone()
        if existing:
            return existing["id"]

        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        now = now_dt.isoformat()
        deadline = (now_dt + timedelta(days=30)).isoformat()
        metrics = [
            "完成至少 5 次经同意的受控 Beta 使用会话",
            "获得可审计的满意度、任务成功率和操作时长证据",
            "形成基于实测 Token 与人工复核成本的单位经济结论",
        ]
        dependencies = [
            "真实客户外联、公开发布、定价、收费、合同与法律承诺均须董事长批准",
            "外部测试者账号和数据处理方式须在启用前完成风险评审",
        ]
        evidence = ["会话记录", "反馈与缺陷台账", "质量评分", "Token 与成本台账", "阶段复盘"]
        cur = conn.execute(
            """INSERT INTO strategic_phases(
                   phase_key, name, objective, success_metrics, deadline, dependencies,
                   evidence_requirements, status, created_at, activated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                "controlled-beta-validation-v1",
                "受控 Beta 客户验证与单位经济阶段",
                "以受控 Beta 客户验证推动真实产品使用、客户满意度、产品质量和商业可行性进步。",
                json.dumps(metrics, ensure_ascii=False),
                deadline,
                json.dumps(dependencies, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                now,
                now,
            ),
        )
        phase_id = int(cur.lastrowid)
        tasks = [
            ("CPO", "定义受控 Beta 客户验证协议与成功指标", "product", 98,
             "形成版本化协议，明确目标客户、核心场景、任务成功率、满意度、质量与停止阈值。",
             "确保客户验证可产生可比较的需求、满意度与产品质量证据。"),
            ("Product Engineer", "完善受控 Beta 会话与反馈闭环", "engineering", 96,
             "实现本地受控会话、同意记录、反馈捕获、问题关联和可验证测试，不进行公开发布。",
             "让真实使用反馈能进入产品改进闭环并缩短问题解决时间。"),
            ("AI Platform & Quality Engineer", "自动采集任务时长 Token 与质量证据", "engineering", 95,
             "真实执行自动写入任务时长和模型 Token 台账；未知值保持未采集；测试覆盖失败路径。",
             "建立产品质量、效率和成本优化所需的真实经营数据。"),
            ("CRO", "建立经董事长批准的首批验证客户候选与外联方案", "gtm", 94,
             "完成候选客户画像、价值假设、招募材料、审批依赖和衡量方法；批准前不发送外联。",
             "形成可执行且受治理的客户获取路径，验证需求而非只做内部建设。"),
            ("CFO", "建立受控 Beta 单位经济与利润敏感性模型", "finance", 92,
             "使用实测或明确标记待采集的 Token、复核和支持成本，给出盈亏平衡与利润敏感性。",
             "为未来定价和资源投入提供可审计的利润决策依据。"),
            ("COO", "建立 Beta 客户问题 SLA 与满意度改进机制", "operations", 90,
             "定义反馈分级、负责人、响应期限、复盘、复发预防和满意度回访闭环。",
             "持续提高客户问题解决速度、满意度和留存可能性。"),
            ("Counsel", "完成受控 Beta 数据权利隐私与同意检查", "risk", 89,
             "形成非法律意见的风险清单、同意文本草案、数据保留规则和董事长决策点。",
             "在客户验证前降低数据、图片权利和隐私风险。"),
            ("CTO", "制定并验证受控 Beta 可靠性与恢复门槛", "engineering", 91,
             "定义访问控制、备份恢复、可观测性和故障演练门槛，附可重复验证证据。",
             "保证客户使用期间产品可靠、问题可定位且数据可恢复。"),
        ]
        for owner, title, domain, priority, criteria, outcome in tasks:
            conn.execute(
                """INSERT INTO tasks(
                       created_at, updated_at, owner, title, domain, status, priority,
                       acceptance_criteria, strategic_phase_id, business_outcome)
                   VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?)""",
                (now, now, owner, title, domain, priority, criteria, phase_id, outcome),
            )
        details = {
            "phase_key": "controlled-beta-validation-v1",
            "objective": "controlled beta customer validation and unit economics",
            "task_count": len(tasks),
            "trigger_active_tasks": active,
            "low_water_mark": low_water,
            "deadline": deadline,
        }
        self.store.audit(conn, "CEO", "activate_strategic_phase", "strategic_phase", phase_id, details)
        return phase_id

    def _ensure_backlog(self, conn) -> None:
        """Keep a bounded, finite roadmap backlog with explicit done criteria.

        Exhausting the reviewed roadmap is a valid operating state.  The scheduler
        must not manufacture numbered repetitions merely to keep the queue non-empty;
        new work should come from evidence, a decision, or a reviewed roadmap change.
        """
        active = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('open', 'in_progress', 'blocked')"
        ).fetchone()[0]
        target = max(self.config.cycle_task_limit, 6)
        now = utcnow()
        if conn.execute("SELECT COUNT(*) FROM experiments").fetchone()[0] == 0:
            cur = conn.execute(
                """INSERT INTO experiments(
                       created_at, owner, name, hypothesis, metric, status, result
                   ) VALUES (?, 'CRO', ?, ?, ?, 'draft', NULL)""",
                (
                    now,
                    "Internal positioning evidence test",
                    "Commercial teams with frequent campaign variants respond more strongly to controllability and repeatability than generic image generation speed.",
                    "rubric_score_difference",
                ),
            )
            self.store.audit(
                conn,
                "CEO",
                "create_experiment",
                "experiment",
                cur.lastrowid,
                {"status": "draft", "external_action": False},
            )
        if active >= target:
            return
        candidates = [
            ("CPO", "Define brand-kit schema and inheritance rules", "product", 84,
             "Schema covers colors, typography, logo constraints, forbidden elements, and versioning with two examples."),
            ("CTO", "Implement brand-kit validation for local artifacts", "engineering", 82,
             "Validator rejects invalid palettes and missing brand-kit versions; unit tests cover valid and invalid inputs."),
            ("CPO", "Specify batch campaign variation workflow", "product", 80,
             "Requirements define inputs, variation matrix, review states, retry behavior, and export manifest."),
            ("CTO", "Add batch manifest generation to local backend", "engineering", 78,
             "One command creates a deterministic multi-variant manifest; tests prove repeatability and input validation."),
            ("CRO", "Draft internal ICP interview guide and evidence rubric", "gtm", 74,
             "Guide has 10 non-leading questions and a rubric for pain frequency, urgency, workflow volume, and willingness to pay; no outreach is sent."),
            ("CFO", "Build inference cost sensitivity model", "finance", 72,
             "Model documents assumptions and computes low/base/high cost per accepted asset and gross-margin break-even points."),
            ("COO", "Define edit-fidelity and brand-consistency scorecards", "operations", 70,
             "Scorecards include formulas, sampling rules, baseline protocol, owners, and alert thresholds."),
            ("Counsel", "Draft internal image provenance and IP risk checklist", "risk", 68,
             "Checklist covers source rights, likeness, trademarks, provenance retention, review escalation, and deletion handling; marked non-legal advice."),
            ("CPO", "Design reusable product-shot workflow template", "product", 66,
             "Template defines required inputs, controls, edit stages, acceptance checks, and three representative scenarios."),
            ("CTO", "Add artifact provenance fields and schema version", "engineering", 64,
             "Artifacts record schema version, model/backend, seed, parent artifact, timestamps, and policy flags; migration tests pass."),
            ("CPO", "Define campaign approval states and role permissions", "product", 62,
             "Specification defines creator, reviewer, and approver permissions plus transitions, rejection reasons, and an audit example."),
            ("CTO", "Implement deterministic brand consistency scoring", "engineering", 61,
             "Scorer evaluates palette and required metadata, returns explainable violations, and has repeatability tests."),
            ("CRO", "Create internal positioning message test matrix", "gtm", 60,
             "Matrix compares three evidence-based value propositions for two ICP segments with hypotheses and success metrics; no external distribution occurs."),
            ("CFO", "Model beta packaging scenarios without setting prices", "finance", 59,
             "Internal model compares usage limits and cost envelopes for three packaging scenarios without approving or publishing a price."),
            ("COO", "Create baseline QA sampling protocol", "operations", 58,
             "Protocol defines sample size, stratification, review cadence, defect taxonomy, and stop thresholds."),
            ("Counsel", "Define internal generated-image incident workflow", "risk", 57,
             "Workflow covers intake, preservation, severity, escalation, deletion holds, and Chairman-controlled external response; marked non-legal advice."),
            ("CPO", "Specify social creative resize and adaptation workflow", "product", 56,
             "Requirements cover source asset, channel variants, safe zones, copy constraints, review states, and deterministic manifest output."),
            ("CTO", "Add structured artifact policy flags", "engineering", 54,
             "Artifacts support documented policy flag codes and review status; tests cover serialization and invalid values."),
            ("CRO", "Design internal customer discovery evidence repository", "gtm", 53,
             "Schema stores anonymized interview evidence, segment, pain, frequency, confidence, and consent status without collecting real customer data."),
            ("COO", "Define experiment lifecycle and stopping rules", "operations", 52,
             "Playbook defines draft, approved, running, analyzed, and stopped states with metric, guardrail, owner, and stopping criteria."),
            ("CTO", "Implement configurable prompt-pack expansion", "engineering", 76,
             "One CLI command validates a versioned prompt pack, deterministically expands its variable matrix into uniquely identified prompts, writes an atomic manifest, and has tests for repeatability and fail-closed input validation."),
        ]
        for owner, title, domain, priority, criteria in candidates:
            if active >= target:
                break
            exists = conn.execute("SELECT 1 FROM tasks WHERE title=?", (title,)).fetchone()
            if exists:
                continue
            conn.execute(
                """INSERT INTO tasks(
                       created_at, updated_at, owner, title, domain, status,
                       priority, acceptance_criteria
                   ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
                (now, now, owner, title, domain, priority, criteria),
            )
            active += 1

        # Do not synthesize recurring/numbered work after these reviewed candidates
        # are exhausted.  An empty queue is preferable to false operating progress.

    def _has_approved_action(self, conn, task_id: int, action_type: str) -> bool:
        summary_prefix = f"Task {task_id} requires Chairman decision before continuing:"
        row = conn.execute(
            """
            SELECT 1 FROM approvals
            WHERE action_type=? AND status='approved' AND summary LIKE ?
            ORDER BY id DESC LIMIT 1
            """,
            (action_type, f"{summary_prefix}%"),
        ).fetchone()
        return row is not None

    def _create_approval(self, conn, requested_by: str, action_type: str, summary: str) -> int:
        now = utcnow()
        cur = conn.execute(
            "INSERT INTO approvals(created_at, requested_by, action_type, summary, status) VALUES (?, ?, ?, ?, 'pending')",
            (now, requested_by, action_type, summary),
        )
        approval_id = cur.lastrowid
        inbox_file = self.config.chairman_inbox / f"approval-{approval_id}.json"
        payload = {
            "approval_id": approval_id,
            "requested_by": requested_by,
            "action_type": action_type,
            "summary": summary,
            "allowed_decisions": ["approve", "deny"],
            "disclaimer": DISCLAIMER,
        }
        inbox_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        conn.execute("UPDATE approvals SET inbox_file=? WHERE id=?", (str(inbox_file), approval_id))
        self.store.audit(conn, "CEO", "request_chairman_decision", "approval", approval_id, payload)
        return approval_id

    def _record_metrics(self, conn) -> None:
        rows = [
            ("tasks_open", conn.execute("SELECT COUNT(*) FROM tasks WHERE status='open'").fetchone()[0], "count", "system"),
            ("tasks_done", conn.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0], "count", "system"),
            ("approvals_pending", conn.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0], "count", "system"),
        ]
        now = utcnow()
        conn.executemany("INSERT INTO metrics(ts, name, value, unit, source) VALUES (?, ?, ?, ?, ?)", [(now, *row) for row in rows])

    def task_list(self) -> list[dict[str, object]]:
        self.init()
        rows = self.store.fetch_all(
            "SELECT * FROM tasks WHERE status IN ('open','in_progress','blocked') ORDER BY priority DESC,id"
        )
        return [dict(row) for row in rows]

    def create_task(
        self,
        actor: str,
        owner: str,
        title: str,
        domain: str,
        priority: int,
        acceptance_criteria: str,
    ) -> dict[str, object]:
        """Add reviewed, finite work without bypassing the operating audit trail."""
        self.init()
        if actor != "CEO":
            raise ValueError("only CEO may create reviewed backlog tasks")
        if not title.strip() or not domain.strip() or not acceptance_criteria.strip():
            raise ValueError("title, domain, and acceptance criteria must not be empty")
        if priority < 1 or priority > 100:
            raise ValueError("priority must be between 1 and 100")
        with self.store.connect() as conn:
            role = conn.execute("SELECT kind FROM roles WHERE name=?", (owner,)).fetchone()
            if role is None or role["kind"] != "agent":
                raise ValueError(f"owner must be a registered agent: {owner}")
            if conn.execute("SELECT 1 FROM tasks WHERE title=?", (title.strip(),)).fetchone():
                raise ValueError(f"task title already exists: {title.strip()}")
            now = utcnow()
            cur = conn.execute(
                """INSERT INTO tasks(
                       created_at, updated_at, owner, title, domain, status,
                       priority, acceptance_criteria
                   ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)""",
                (now, now, owner, title.strip(), domain.strip(), priority, acceptance_criteria.strip()),
            )
            task_id = cur.lastrowid
            details = {
                "owner": owner,
                "title": title.strip(),
                "domain": domain.strip(),
                "priority": priority,
                "acceptance_criteria": acceptance_criteria.strip(),
            }
            self.store.audit(conn, actor, "create_task", "task", task_id, details)
            return {"task_id": task_id, "status": "open", **details}

    def claim_task(
        self,
        task_id: int,
        actor: str,
        executor_id: str | None = None,
        backend: str | None = None,
        process_id: int | None = None,
        process_started_at: str | None = None,
        session_ref: str | None = None,
        lease_seconds: int = 600,
        max_attempts: int = 3,
        evidence_paths: list[Path] | None = None,
        log_paths: list[Path] | None = None,
    ) -> dict[str, object]:
        self.init()
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        executor_id = (executor_id or f"{actor.lower()}-{os.getpid()}").strip()
        backend = (backend or self.config.backend or "local").strip()
        if not executor_id:
            raise ValueError("executor_id must not be empty")
        if not backend:
            raise ValueError("backend must not be empty")
        if backend == "codex" and not self.config.codex_enabled:
            raise ValueError("Codex backend selected without codex_enabled=true")
        evidence_json = json.dumps([str(path.expanduser()) for path in evidence_paths or []], sort_keys=True)
        log_json = json.dumps([str(path.expanduser()) for path in log_paths or []], sort_keys=True)
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"task not found: {task_id}")
            if task["owner"] != actor:
                raise ValueError(f"task {task_id} is owned by {task['owner']}, not {actor}")
            existing = conn.execute(
                "SELECT * FROM task_executions WHERE task_id=? AND recovery_status IN ('running','failed')",
                (task_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError(f"task {task_id} is already claimed by {existing['executor_id']}")
            if task["status"] != "open":
                raise ValueError(f"task {task_id} is not open: {task['status']}")
            details = self._claim_task_execution(
                conn,
                task,
                actor=actor,
                executor_id=executor_id,
                backend=backend,
                process_id=process_id,
                process_started_at=process_started_at,
                session_ref=session_ref,
                lease_seconds=lease_seconds,
                max_attempts=max_attempts,
                evidence_json=evidence_json,
                log_json=log_json,
            )
            self.store.audit(conn, actor, "claim_task", "task", task_id, {"title": task["title"]})
            self.store.audit(conn, actor, "claim_task_execution", "task_execution", task_id, details)
            return {"task_id": task_id, "status": "in_progress", "owner": actor, **details}

    def heartbeat_task(self, task_id: int, executor_id: str, lease_seconds: int = 600) -> dict[str, object]:
        self.init()
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        with self.store.connect() as conn:
            task, execution = self._active_execution(conn, task_id, executor_id)
            now = utcnow()
            lease_expires = _iso_add(now, lease_seconds)
            conn.execute(
                "UPDATE task_executions SET heartbeat_at=?, lease_expires_at=?, recovery_status='running', updated_at=? WHERE task_id=?",
                (now, lease_expires, now, task_id),
            )
            updated = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
            details = self._execution_details(updated)
            self.store.audit(conn, task["owner"], "heartbeat_task_execution", "task_execution", task_id, details)
            return {"task_id": task_id, **details}

    def checkpoint_task(self, task_id: int, executor_id: str, checkpoint: str, next_action: str) -> dict[str, object]:
        self.init()
        if not checkpoint.strip() or not next_action.strip():
            raise ValueError("checkpoint and next_action must not be empty")
        with self.store.connect() as conn:
            task, _ = self._active_execution(conn, task_id, executor_id)
            now = utcnow()
            conn.execute(
                "UPDATE task_executions SET checkpoint=?, next_action=?, heartbeat_at=?, lease_expires_at=?, recovery_status='running', updated_at=? WHERE task_id=?",
                (checkpoint.strip(), next_action.strip(), now, _iso_add(now, 600), now, task_id),
            )
            updated = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
            details = self._execution_details(updated)
            self.store.audit(conn, task["owner"], "checkpoint_task_execution", "task_execution", task_id, details)
            return {"task_id": task_id, **details}

    def fail_task(self, task_id: int, executor_id: str, error: str, recoverable: bool = True) -> dict[str, object]:
        self.init()
        if not error.strip():
            raise ValueError("error must not be empty")
        with self.store.connect() as conn:
            task, _ = self._active_execution(conn, task_id, executor_id)
            now = utcnow()
            status = "failed" if recoverable else "exhausted"
            task_status = "in_progress" if recoverable else "blocked"
            blocked_reason = None if recoverable else f"Task execution failed permanently: {error.strip()}"
            conn.execute(
                "UPDATE task_executions SET last_error=?, recovery_status=?, heartbeat_at=?, updated_at=? WHERE task_id=?",
                (error.strip(), status, now, now, task_id),
            )
            conn.execute(
                "UPDATE tasks SET status=?, updated_at=?, blocked_reason=? WHERE id=?",
                (task_status, now, blocked_reason, task_id),
            )
            updated = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
            details = self._execution_details(updated)
            self.store.audit(conn, task["owner"], "fail_task_execution", "task_execution", task_id, details)
            return {"task_id": task_id, "status": task_status, **details}

    def inspect_execution(self, task_id: int) -> dict[str, object]:
        self.init()
        with self.store.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"task not found: {task_id}")
            execution = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
            details = self._execution_details(execution) if execution else None
            return {
                "task": dict(task),
                "execution": details,
                "process": self._process_status(details),
            }

    def recover_task(self, task_id: int, actor: str, reason: str) -> dict[str, object]:
        self.init()
        if actor != "CEO":
            raise ValueError("only CEO may recover task executions")
        if not reason.strip():
            raise ValueError("reason must not be empty")
        with self.store.connect() as conn:
            result = self._recover_execution(conn, task_id, actor, reason.strip(), require_stale=False)
            if result is None:
                raise ValueError(f"task {task_id} has no recoverable execution")
            return result

    def complete_task(self, task_id: int, actor: str, summary: str, evidence: list[Path]) -> dict[str, object]:
        self.init()
        if not summary.strip():
            raise ValueError("summary must not be empty")
        resolved = [path.expanduser().resolve() for path in evidence]
        missing = [str(path) for path in resolved if not path.is_file()]
        if missing:
            raise ValueError(f"evidence files do not exist: {missing}")
        with self.store.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"task not found: {task_id}")
            if task["owner"] != actor:
                raise ValueError(f"task {task_id} is owned by {task['owner']}, not {actor}")
            if task["status"] != "in_progress":
                raise ValueError(f"task {task_id} is not in_progress: {task['status']}")
            result = {"summary": summary.strip(), "evidence": [str(path) for path in resolved]}
            conn.execute(
                "UPDATE tasks SET status='done',updated_at=?,result=? WHERE id=?",
                (utcnow(), json.dumps(result, sort_keys=True), task_id),
            )
            conn.execute(
                "UPDATE task_executions SET recovery_status='completed', evidence_paths=?, updated_at=? WHERE task_id=?",
                (json.dumps(result["evidence"], sort_keys=True), utcnow(), task_id),
            )
            self.store.audit(conn, actor, "complete_task", "task", task_id, result)
            execution = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
            if execution is not None:
                self.store.audit(conn, actor, "complete_task_execution", "task_execution", task_id, self._execution_details(execution))
            self._spawn_followups(conn, task, result)
            self._ensure_backlog(conn)
            return {"task_id": task_id, "status": "done", **result}

    def cancel_task(self, task_id: int, actor: str, reason: str) -> dict[str, object]:
        """Close obsolete work without misrepresenting it as completed."""
        self.init()
        if not reason.strip():
            raise ValueError("reason must not be empty")
        with self.store.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"task not found: {task_id}")
            if actor not in {"CEO", task["owner"]}:
                raise ValueError(f"task {task_id} may only be cancelled by CEO or {task['owner']}")
            if task["status"] not in {"open", "in_progress"}:
                raise ValueError(f"task {task_id} cannot be cancelled from status: {task['status']}")
            result = {"reason": reason.strip(), "completed": False}
            conn.execute(
                "UPDATE tasks SET status='cancelled',updated_at=?,blocked_reason=NULL,result=? WHERE id=?",
                (utcnow(), json.dumps(result, sort_keys=True), task_id),
            )
            conn.execute(
                "UPDATE task_executions SET recovery_status='cancelled', updated_at=? WHERE task_id=?",
                (utcnow(), task_id),
            )
            self.store.audit(conn, actor, "cancel_task", "task", task_id, result)
            execution = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
            if execution is not None:
                self.store.audit(conn, actor, "cancel_task_execution", "task_execution", task_id, self._execution_details(execution))
            return {"task_id": task_id, "status": "cancelled", **result}

    def chairman_inbox(self) -> list[dict[str, object]]:
        self.init()
        rows = self.store.fetch_all("SELECT * FROM approvals WHERE status='pending' ORDER BY id")
        return [dict(row) for row in rows]

    def decide(self, approval_id: int, decision: str, rationale: str) -> dict[str, object]:
        if decision not in {"approve", "deny"}:
            raise ValueError("decision must be approve or deny")
        self.init()
        with self.store.connect() as conn:
            row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if row is None:
                raise ValueError(f"approval not found: {approval_id}")
            if row["status"] != "pending":
                raise ValueError(f"approval already decided: {approval_id}")
            outbox_file = self.config.chairman_outbox / f"decision-{approval_id}.json"
            payload = {
                "approval_id": approval_id,
                "decision": decision,
                "rationale": rationale,
                "decided_by": "Chairman",
                "decided_at": utcnow(),
            }
            outbox_file.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            conn.execute(
                "UPDATE approvals SET status=?, decision=?, rationale=?, decided_at=?, outbox_file=? WHERE id=?",
                ("approved" if decision == "approve" else "denied", decision, rationale, payload["decided_at"], str(outbox_file), approval_id),
            )
            task_rows = conn.execute("SELECT id, blocked_reason FROM tasks WHERE status='blocked'").fetchall()
            for task in task_rows:
                if f"#{approval_id}" in (task["blocked_reason"] or ""):
                    new_status = "open" if decision == "approve" else "done"
                    result = None if decision == "approve" else json.dumps({"summary": "Chairman denied reserved action; task closed without external action."})
                    conn.execute("UPDATE tasks SET status=?, updated_at=?, blocked_reason=NULL, result=? WHERE id=?", (new_status, utcnow(), result, task["id"]))
            self.store.audit(conn, "Chairman", "decide", "approval", approval_id, payload)
            return payload

    def report(self) -> str:
        self.init()
        status = self.status()
        rows = self.store.fetch_all("SELECT name, value, unit, ts FROM metrics ORDER BY id DESC LIMIT 9")
        approvals = self.chairman_inbox()
        lines = [
            f"# Operating Report: {self.config.product_name}",
            "",
            DISCLAIMER,
            "",
            f"- Open tasks: {status['open_tasks']}",
            f"- In-progress tasks: {status['in_progress_tasks']}",
            f"- Blocked tasks: {status['blocked_tasks']}",
            f"- Pending Chairman approvals: {status['pending_approvals']}",
            f"- Completed cycles: {status['cycles']}",
            "",
            "## Recent Metrics",
        ]
        lines.extend(f"- {row['ts']} {row['name']}={row['value']} {row['unit']}" for row in rows)
        lines.append("")
        lines.append("## Chairman Queue")
        if approvals:
            lines.extend(f"- #{row['id']} {row['action_type']}: {row['summary']}" for row in approvals)
        else:
            lines.append("- No pending approvals.")
        return "\n".join(lines) + "\n"

    def demo(self) -> dict[str, object]:
        self.init()
        before = self.status()
        cycle = self.run_cycle()
        after = self.status()
        return {"before": before, "cycle": cycle, "after": after}

    def validate(self) -> list[str]:
        self.init()
        errors: list[str] = []
        required_tables = {"audit_log", "roles", "raci", "tasks", "approvals", "metrics", "experiments", "cycles", "task_executions", "token_usage", "strategic_phases"}
        rows = self.store.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
        present = {row["name"] for row in rows}
        missing = required_tables - present
        if missing:
            errors.append(f"Missing tables: {sorted(missing)}")
        chairman = self.store.fetch_one("SELECT kind FROM roles WHERE name='Chairman'")
        if chairman is None or chairman["kind"] != "human":
            errors.append("Chairman must be the only human role")
        humans = self.store.fetch_all("SELECT name FROM roles WHERE kind='human'")
        if [row["name"] for row in humans] != ["Chairman"]:
            errors.append("Non-Chairman human roles are not allowed")
        if not self.config.chairman_inbox.exists() or not self.config.chairman_outbox.exists():
            errors.append("Chairman inbox/outbox paths missing")
        if self.config.backend == "codex" and not self.config.codex_enabled:
            errors.append("Codex backend selected without codex_enabled=true")
        return errors

    def record_token_usage(
        self,
        agent: str,
        input_tokens: int,
        output_tokens: int,
        cache_tokens: int,
        reasoning_tokens: int,
        total_tokens: int,
        source: str,
        task_id: int | None = None,
        execution_id: int | None = None,
        session: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        cost: float | None = None,
        currency: str | None = None,
        timestamp: str | None = None,
    ) -> dict[str, object]:
        self.init()
        values = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_tokens": cache_tokens,
            "reasoning_tokens": reasoning_tokens,
            "total_tokens": total_tokens,
        }
        for name, value in values.items():
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        component_total = input_tokens + output_tokens + cache_tokens + reasoning_tokens
        if total_tokens != component_total:
            raise ValueError("total_tokens must equal input_tokens + output_tokens + cache_tokens + reasoning_tokens")
        if not source.strip():
            raise ValueError("source is required; token usage must come from observed records")
        if cost is not None and cost < 0:
            raise ValueError("cost must be nonnegative")
        if cost is not None and not (currency or "").strip():
            raise ValueError("currency is required when cost is recorded")
        if currency is not None:
            currency = currency.strip().upper()
            if len(currency) != 3 or not currency.isalpha():
                raise ValueError("currency must be a 3-letter code")
        ts = timestamp or utcnow()
        _parse_iso8601(ts, "timestamp")

        with self.store.connect() as conn:
            role = conn.execute("SELECT kind FROM roles WHERE name=?", (agent,)).fetchone()
            if role is None or role["kind"] != "agent":
                raise ValueError("agent must be a registered agent role")
            if task_id is not None and conn.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
                raise ValueError(f"task not found: {task_id}")
            if execution_id is not None and conn.execute("SELECT 1 FROM task_executions WHERE id=?", (execution_id,)).fetchone() is None:
                raise ValueError(f"execution not found: {execution_id}")
            cur = conn.execute(
                """INSERT INTO token_usage(
                       ts, agent, task_id, execution_id, session, model, provider,
                       input_tokens, output_tokens, cache_tokens, reasoning_tokens,
                       total_tokens, cost, currency, source, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts,
                    agent,
                    task_id,
                    execution_id,
                    _blank_to_none(session),
                    _blank_to_none(model),
                    _blank_to_none(provider),
                    input_tokens,
                    output_tokens,
                    cache_tokens,
                    reasoning_tokens,
                    total_tokens,
                    cost,
                    currency,
                    source.strip(),
                    utcnow(),
                ),
            )
            record = dict(conn.execute("SELECT * FROM token_usage WHERE id=?", (cur.lastrowid,)).fetchone())
            self.store.audit(conn, agent, "record_token_usage", "token_usage", record["id"], {"record_id": record["id"], "agent": agent, "source": source.strip()})
            return record

    def list_token_usage(self, agent: str | None = None, limit: int = 50) -> list[dict[str, object]]:
        self.init()
        if limit <= 0:
            raise ValueError("limit must be positive")
        query = "SELECT * FROM token_usage"
        params: list[object] = []
        if agent:
            query += " WHERE agent=?"
            params.append(agent)
        query += " ORDER BY ts DESC, id DESC LIMIT ?"
        params.append(limit)
        return [dict(row) for row in self.store.fetch_all(query, params)]

    def token_usage_summary(self) -> dict[str, object]:
        self.init()
        with self.store.connect() as conn:
            agents = [row["name"] for row in conn.execute("SELECT name FROM roles WHERE kind='agent' ORDER BY name ASC")]
            rows = conn.execute(
                """SELECT agent,
                          COUNT(*) AS record_count,
                          SUM(input_tokens) AS input_tokens,
                          SUM(output_tokens) AS output_tokens,
                          SUM(cache_tokens) AS cache_tokens,
                          SUM(reasoning_tokens) AS reasoning_tokens,
                          SUM(total_tokens) AS total_tokens,
                          SUM(cost) AS cost,
                          MAX(currency) AS currency
                   FROM token_usage
                   GROUP BY agent"""
            ).fetchall()
        by_agent = {row["agent"]: dict(row) for row in rows}
        summary: dict[str, object] = {}
        for agent in agents:
            row = by_agent.get(agent)
            if row is None:
                summary[agent] = {
                    "agent": agent,
                    "display_label": agent,
                    "status_label": "未采集",
                    "record_count": 0,
                    "input_tokens": None,
                    "output_tokens": None,
                    "cache_tokens": None,
                    "reasoning_tokens": None,
                    "total_tokens": None,
                    "cost": None,
                    "currency": None,
                }
            else:
                summary[agent] = {
                    "agent": agent,
                    "display_label": agent,
                    "status_label": "已采集",
                    "record_count": int(row["record_count"] or 0),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "cache_tokens": int(row["cache_tokens"] or 0),
                    "reasoning_tokens": int(row["reasoning_tokens"] or 0),
                    "total_tokens": int(row["total_tokens"] or 0),
                    "cost": row["cost"],
                    "currency": row["currency"],
                }
        return {"agents": summary}

    def _active_execution(self, conn, task_id: int, executor_id: str):
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        execution = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
        if execution is None:
            raise ValueError(f"task {task_id} has no execution state")
        if execution["executor_id"] != executor_id:
            raise ValueError(f"task {task_id} is claimed by {execution['executor_id']}, not {executor_id}")
        if execution["recovery_status"] not in {"running", "failed"}:
            raise ValueError(f"task {task_id} execution is not active: {execution['recovery_status']}")
        return task, execution

    def _claim_task_execution(
        self,
        conn,
        task,
        actor: str,
        executor_id: str,
        backend: str,
        process_id: int | None = None,
        process_started_at: str | None = None,
        session_ref: str | None = None,
        lease_seconds: int = 600,
        max_attempts: int = 3,
        evidence_json: str = "[]",
        log_json: str = "[]",
    ) -> dict[str, object]:
        now = utcnow()
        task_id = int(task["id"])
        lease_expires = _iso_add(now, lease_seconds)
        updated = conn.execute(
            "UPDATE tasks SET status='in_progress', updated_at=? WHERE id=? AND status='open'",
            (now, task_id),
        ).rowcount
        if updated != 1:
            raise ValueError(f"task {task_id} is already claimed")
        conn.execute(
            """INSERT INTO task_executions(
                   task_id, executor_id, backend, process_id, process_started_at,
                   session_ref, claimed_at, heartbeat_at, lease_expires_at,
                   attempt_count, max_attempts, evidence_paths, log_paths,
                   recovery_status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'running', ?, ?)
               ON CONFLICT(task_id) DO UPDATE SET
                   executor_id=excluded.executor_id,
                   backend=excluded.backend,
                   process_id=excluded.process_id,
                   process_started_at=excluded.process_started_at,
                   session_ref=excluded.session_ref,
                   claimed_at=excluded.claimed_at,
                   heartbeat_at=excluded.heartbeat_at,
                   lease_expires_at=excluded.lease_expires_at,
                   max_attempts=excluded.max_attempts,
                   evidence_paths=excluded.evidence_paths,
                   log_paths=excluded.log_paths,
                   last_error=NULL,
                   recovery_status='running',
                   updated_at=excluded.updated_at
               WHERE task_executions.recovery_status IN ('requeued','exhausted')""",
            (
                task_id,
                executor_id,
                backend,
                process_id,
                process_started_at,
                session_ref,
                now,
                now,
                lease_expires,
                max_attempts,
                evidence_json,
                log_json,
                now,
                now,
            ),
        )
        execution = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
        if execution is None or execution["executor_id"] != executor_id or execution["recovery_status"] != "running":
            raise ValueError(f"task {task_id} is already claimed")
        return self._execution_details(execution)

    def _recover_stale_executions(self, conn, actor: str, reason: str) -> list[dict[str, object]]:
        rows = conn.execute(
            "SELECT task_id FROM task_executions WHERE recovery_status IN ('running','failed') ORDER BY updated_at ASC, task_id ASC"
        ).fetchall()
        recovered: list[dict[str, object]] = []
        for row in rows:
            result = self._recover_execution(conn, row["task_id"], actor, reason, require_stale=True)
            if result is not None:
                recovered.append(result)
        return recovered

    def _recover_execution(self, conn, task_id: int, actor: str, reason: str, require_stale: bool) -> dict[str, object] | None:
        task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        execution = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
        if task is None or execution is None or execution["recovery_status"] not in {"running", "failed"}:
            return None
        details = self._execution_details(execution)
        stale = self._execution_needs_recovery(details)
        if not stale:
            if not require_stale:
                return None
            now = utcnow()
            conn.execute(
                "UPDATE task_executions SET lease_expires_at=?, updated_at=? WHERE task_id=?",
                (_iso_add(now, 600), now, task_id),
            )
            updated = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
            self.store.audit(
                conn,
                actor,
                "renew_task_execution",
                "task_execution",
                task_id,
                {**self._execution_details(updated), "reason": "lease still valid"},
            )
            return None
        now = utcnow()
        next_attempt = int(execution["attempt_count"]) + 1
        max_attempts = int(execution["max_attempts"])
        exhausted = next_attempt >= max_attempts
        if exhausted:
            blocked_reason = f"Task execution retry attempts exhausted after {next_attempt}/{max_attempts}: {reason}"
            conn.execute(
                "UPDATE tasks SET status='blocked', updated_at=?, blocked_reason=? WHERE id=?",
                (now, blocked_reason, task_id),
            )
            conn.execute(
                "UPDATE task_executions SET attempt_count=?, recovery_status='exhausted', last_error=?, updated_at=? WHERE task_id=?",
                (next_attempt, reason, now, task_id),
            )
            status = "blocked"
        else:
            conn.execute(
                "UPDATE tasks SET status='open', updated_at=?, blocked_reason=NULL WHERE id=?",
                (now, task_id),
            )
            conn.execute(
                "UPDATE task_executions SET attempt_count=?, recovery_status='requeued', last_error=?, updated_at=? WHERE task_id=?",
                (next_attempt, reason, now, task_id),
            )
            status = "open"
        updated = conn.execute("SELECT * FROM task_executions WHERE task_id=?", (task_id,)).fetchone()
        result = {"task_id": task_id, "status": status, **self._execution_details(updated)}
        self.store.audit(conn, actor, "recover_task_execution", "task_execution", task_id, {**result, "reason": reason, "stale": stale})
        return result

    def _execution_needs_recovery(self, execution: dict[str, object]) -> bool:
        if execution["recovery_status"] == "failed":
            return True
        if self._execution_lease_expired(execution):
            return True
        if execution["backend"] == "local":
            if execution.get("process_id") is None or not execution.get("process_started_at"):
                return True
            return self._process_status(execution)["alive"] is not True
        return False

    def _execution_lease_expired(self, execution: dict[str, object]) -> bool:
        lease = str(execution["lease_expires_at"])
        try:
            expires = datetime.fromisoformat(lease)
        except ValueError:
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= datetime.now(timezone.utc)

    def _execution_details(self, execution) -> dict[str, object]:
        def loads(value: str | None) -> list[str]:
            if not value:
                return []
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return []
            return parsed if isinstance(parsed, list) else []

        return {
            "executor_id": execution["executor_id"],
            "backend": execution["backend"],
            "process_id": execution["process_id"],
            "process_started_at": execution["process_started_at"],
            "session_ref": execution["session_ref"],
            "claimed_at": execution["claimed_at"],
            "heartbeat_at": execution["heartbeat_at"],
            "lease_expires_at": execution["lease_expires_at"],
            "attempt_count": execution["attempt_count"],
            "max_attempts": execution["max_attempts"],
            "checkpoint": execution["checkpoint"],
            "next_action": execution["next_action"],
            "evidence_paths": loads(execution["evidence_paths"]),
            "log_paths": loads(execution["log_paths"]),
            "last_error": execution["last_error"],
            "recovery_status": execution["recovery_status"],
        }

    def _process_status(self, execution: dict[str, object] | None) -> dict[str, object]:
        if not execution or execution.get("process_id") is None:
            return {"alive": None, "reason": "no process recorded"}
        pid = int(execution["process_id"])
        expected_start = execution.get("process_started_at")
        if expected_start:
            actual_start = _process_start_identity(pid)
            if actual_start is None:
                return {"alive": False, "reason": "process not found"}
            if actual_start != expected_start:
                return {"alive": False, "reason": "process identity mismatch", "actual_start_identity": actual_start}
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return {"alive": False, "reason": "process not found"}
        except PermissionError:
            return {"alive": True, "reason": "permission denied but process exists"}
        return {"alive": True, "reason": "process exists"}


def _iso_add(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


def _parse_iso8601(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _process_start_identity(pid: int) -> str | None:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        stat = stat_path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None
    try:
        after_comm = stat.rsplit(")", 1)[1].strip()
        fields = after_comm.split()
        return fields[19]
    except (IndexError, ValueError):
        return None
