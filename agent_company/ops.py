"""Company operating cycle."""

from __future__ import annotations

import json
from pathlib import Path

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
        return {
            "product": self.config.product_name,
            "open_tasks": open_tasks,
            "in_progress_tasks": in_progress,
            "active_tasks": open_tasks + in_progress + blocked,
            "blocked_tasks": blocked,
            "pending_approvals": approvals,
            "cycles": cycles,
            "disclaimer": DISCLAIMER,
        }

    def run_cycle(self) -> dict[str, object]:
        self.init()
        with self.store.connect() as conn:
            started = utcnow()
            cur = conn.execute("INSERT INTO cycles(started_at, summary) VALUES (?, ?)", (started, "running"))
            cycle_id = cur.lastrowid
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
                conn.execute(
                    "UPDATE tasks SET status='in_progress', updated_at=? WHERE id=?",
                    (utcnow(), task["id"]),
                )
                self.store.audit(conn, "CEO", "dispatch_task", "task", task["id"], {"owner": task["owner"]})
                progressed.append(task["id"])
            self._ensure_backlog(conn)
            self._record_metrics(conn)
            summary = {"progressed": progressed, "escalated": escalated, "processed": len(tasks)}
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

    def claim_task(self, task_id: int, actor: str) -> dict[str, object]:
        self.init()
        with self.store.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"task not found: {task_id}")
            if task["owner"] != actor:
                raise ValueError(f"task {task_id} is owned by {task['owner']}, not {actor}")
            if task["status"] != "open":
                raise ValueError(f"task {task_id} is not open: {task['status']}")
            conn.execute("UPDATE tasks SET status='in_progress',updated_at=? WHERE id=?", (utcnow(), task_id))
            self.store.audit(conn, actor, "claim_task", "task", task_id, {"title": task["title"]})
            return {"task_id": task_id, "status": "in_progress", "owner": actor}

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
            self.store.audit(conn, actor, "complete_task", "task", task_id, result)
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
            self.store.audit(conn, actor, "cancel_task", "task", task_id, result)
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
        required_tables = {"audit_log", "roles", "raci", "tasks", "approvals", "metrics", "experiments", "cycles"}
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
