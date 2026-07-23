from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import CompanyConfig
from .ops import CompanyOS


class ExecutionRunner:
    """Resident, single-capacity worker for one company role."""

    def __init__(
        self,
        config: CompanyConfig,
        executor_id: str,
        owner: str,
        capabilities: list[str],
        poll_seconds: float = 5.0,
    ) -> None:
        self.config = config
        self.executor_id = executor_id
        self.owner = owner
        self.capabilities = capabilities
        self.poll_seconds = poll_seconds
        self.osys = CompanyOS(config)

    def run_once(self) -> dict[str, Any]:
        tasks = [row for row in self.osys.task_list() if row["owner"] == self.owner and row["status"] == "open"]
        tasks = [row for row in tasks if self._matches(row)]
        if not tasks:
            self._register()
            return {"status": "idle"}
        task = tasks[0]
        evidence_dir = self.config.workspace / "evidence" / f"task-{task['id']}"
        log_path = self.config.workspace / "logs" / f"task-{task['id']}-{self.executor_id}.log"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._register()
        claim = self.osys.claim_task(
            int(task["id"]), self.owner, executor_id=self.executor_id,
            backend="local", process_id=None, session_ref=f"runner:{self.executor_id}",
            lease_seconds=1800, evidence_paths=[evidence_dir], log_paths=[log_path],
        )
        token = str(claim["fencing_token"])
        process = self._launch(task, evidence_dir, log_path)
        self._attach_process(int(task["id"]), process)
        while process.poll() is None:
            self.osys.heartbeat_task(int(task["id"]), self.executor_id, 600, token)
            time.sleep(self.poll_seconds)
        if process.returncode != 0:
            self.osys.fail_task(int(task["id"]), self.executor_id, f"runner exited with code {process.returncode}", True, token)
            return {"status": "failed", "task_id": task["id"], "returncode": process.returncode}
        evidence = sorted(evidence_dir.rglob("*"))
        evidence = [path for path in evidence if path.is_file()]
        if not evidence:
            self.osys.fail_task(int(task["id"]), self.executor_id, "runner exited without reviewable evidence", True, token)
            return {"status": "failed", "task_id": task["id"], "returncode": 0}
        result = self.osys.complete_task(
            int(task["id"]), self.owner,
            "Runner verified reviewable evidence for the bounded task.", evidence,
            fencing_token=token,
        )
        return {"status": "done", "task_id": task["id"], "result": result}

    def run_forever(self) -> None:
        while True:
            result = self.run_once()
            if result["status"] == "idle":
                time.sleep(self.poll_seconds)

    def _matches(self, task: dict[str, Any]) -> bool:
        return str(task["domain"]) in set(self.capabilities) or str(task["domain"]) in {"commercial", "customer", "gtm"} and "commercial" in self.capabilities

    def _register(self) -> None:
        self.osys.register_executor(self.executor_id, self.owner, "local", self.capabilities, 1, session_ref=f"runner:{self.executor_id}")

    def _launch(self, task: dict[str, Any], evidence_dir: Path, log_path: Path):
        prompt = self._prompt(task, evidence_dir)
        log = log_path.open("w", encoding="utf-8")
        process = subprocess.Popen(
            ["codex", "exec", "-C", str(self.config.workspace), "--dangerously-bypass-approvals-and-sandbox", "--color", "never", prompt],
            stdout=log, stderr=subprocess.STDOUT, cwd=self.config.workspace,
            env={**os.environ, "CODEX_SANDBOX": "danger-full-access"},
        )
        log.close()
        return process

    def _attach_process(self, task_id: int, process: Any) -> None:
        with self.osys.store.connect() as conn:
            conn.execute(
                "UPDATE task_executions SET process_id=?, process_started_at=? WHERE task_id=? AND executor_id=?",
                (int(process.pid), self._process_identity(int(process.pid)), task_id, self.executor_id),
            )

    def _process_identity(self, pid: int) -> str | None:
        path = Path(f"/proc/{pid}/stat")
        try:
            fields = path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()
            return fields[19]
        except (FileNotFoundError, IndexError, ValueError):
            return None

    def _prompt(self, task: dict[str, Any], evidence_dir: Path) -> str:
        return json.dumps({"role": self.owner, "task_id": task["id"], "title": task["title"], "acceptance_criteria": task["acceptance_criteria"], "evidence_dir": str(evidence_dir), "constraints": "Internal evidence only; no external contact, pricing, publication, payment, customer data, or legal commitments."}, ensure_ascii=False)
