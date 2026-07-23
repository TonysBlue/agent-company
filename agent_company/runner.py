from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import CompanyConfig
from .ops import CompanyOS
from .product_registry import ProductRegistry
from .workspace_manager import WorkspaceManager


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
        registry_path = config.workspace / "config" / "repositories.json"
        if not registry_path.exists():
            registry_path = Path(__file__).resolve().parents[1] / "config" / "repositories.json"
        self.registry = ProductRegistry(registry_path)
        self.workspaces = WorkspaceManager(Path.home() / "agent-workspaces")

    def run_once(self) -> dict[str, Any]:
        tasks = [row for row in self.osys.task_list() if row["owner"] == self.owner and row["status"] == "open"]
        tasks = [row for row in tasks if self._matches(row)]
        if not tasks:
            self._register()
            return {"status": "idle"}
        task = tasks[0]
        repository = self.registry.for_role(self.owner, str(task["domain"]))
        workdir = self.workspaces.path_for(self.owner, int(task["id"]), repository.repository_id)
        evidence_dir = self.config.workspace / "evidence" / f"task-{task['id']}"
        log_path = self.config.workspace / "logs" / f"task-{task['id']}-{self.executor_id}.log"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._register()
        try:
            claim = self.osys.claim_task(
                int(task["id"]), self.owner, executor_id=self.executor_id,
                backend="local", process_id=None, session_ref=f"runner:{self.executor_id}",
                lease_seconds=1800, evidence_paths=[evidence_dir], log_paths=[log_path],
            )
        except ValueError as exc:
            if "already claimed" in str(exc) or "is not open" in str(exc):
                return {"status": "contended", "task_id": task["id"]}
            raise
        token = str(claim["fencing_token"])
        try:
            self._ensure_workspace(repository, workdir)
            process = self._launch(task, repository, workdir, evidence_dir, log_path)
        except Exception as exc:
            self.osys.fail_task(int(task["id"]), self.executor_id, f"workspace or launch failed: {exc}", True, token)
            return {"status": "failed", "task_id": task["id"], "returncode": None}
        self._attach_process(int(task["id"]), process)
        while process.poll() is None:
            self.osys.heartbeat_task(int(task["id"]), self.executor_id, 600, token)
            time.sleep(self.poll_seconds)
        if process.returncode != 0:
            self.osys.fail_task(int(task["id"]), self.executor_id, f"runner exited with code {process.returncode}", True, token)
            return {"status": "failed", "task_id": task["id"], "returncode": process.returncode}
        try:
            delivery = self._publish_delivery(repository, workdir)
        except Exception as exc:
            self.osys.fail_task(int(task["id"]), self.executor_id, f"delivery validation or push failed: {exc}", True, token)
            return {"status": "failed", "task_id": task["id"], "returncode": 0}
        evidence = sorted(evidence_dir.rglob("*"))
        evidence = [path for path in evidence if path.is_file()]
        delivery_evidence = evidence_dir / "delivery.json"
        delivery_evidence.write_text(json.dumps(delivery, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        evidence.append(delivery_evidence)
        if len(evidence) == 1:
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

    def _ensure_workspace(self, repository: Any, workdir: Path) -> None:
        workdir.parent.mkdir(parents=True, exist_ok=True)
        if workdir.exists():
            if not (workdir / ".git").exists():
                raise ValueError(f"workspace exists but is not a git checkout: {workdir}")
            origin = self._git(workdir, "remote", "get-url", "origin")
            branch = self._git(workdir, "branch", "--show-current")
            status = self._git(workdir, "status", "--porcelain")
            expected_branch = f"task/{workdir.name.split('-', 2)[1]}"
            if origin != repository.remote:
                raise ValueError(f"workspace origin mismatch: {origin}")
            if branch != expected_branch:
                raise ValueError(f"workspace branch mismatch: {branch}")
            if status:
                raise ValueError(f"workspace is dirty: {workdir}")
            return
        subprocess.run(
            ["git", "clone", "--origin", "origin", "--branch", repository.default_branch,
             "--single-branch", repository.remote, str(workdir)],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(workdir), "checkout", "-b", f"task/{workdir.name.split('-', 2)[1]}"],
            check=True, capture_output=True, text=True,
        )

    @staticmethod
    def _git(workdir: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(workdir), *args], check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def _register(self) -> None:
        self.osys.register_executor(self.executor_id, self.owner, "local", self.capabilities, 1, session_ref=f"runner:{self.executor_id}")

    def _launch(self, task: dict[str, Any], repository: Any, workdir: Path, evidence_dir: Path, log_path: Path):
        prompt = self._prompt(task, repository, workdir, evidence_dir)
        log = log_path.open("w", encoding="utf-8")
        codex_home = workdir / ".codex-home"
        self._prepare_codex_home(codex_home)
        command = [
            "bwrap", "--unshare-all", "--share-net", "--die-with-parent",
            "--ro-bind", "/", "/", "--tmpfs", "/home", "--dir", "/home/tony",
            "--ro-bind", "/home/tony/.npm-global", "/home/tony/.npm-global",
            "--bind", str(workdir), str(workdir), "--bind", str(evidence_dir), str(evidence_dir),
            "--proc", "/proc", "--dev", "/dev", "--setenv", "HOME", str(workdir),
            "--setenv", "CODEX_HOME", str(codex_home),
            "/usr/bin/node", "/home/tony/.npm-global/lib/node_modules/@openai/codex/bin/codex.js",
            "exec", "--ignore-user-config", "--dangerously-bypass-approvals-and-sandbox",
            "-C", str(workdir), "--color", "never", prompt,
        ]
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, cwd=workdir,
            env=self._runner_environment(),
        )
        log.close()
        return process

    def _publish_delivery(self, repository: Any, workdir: Path) -> dict[str, str]:
        origin = self._git(workdir, "remote", "get-url", "origin")
        branch = self._git(workdir, "branch", "--show-current")
        status = self._git(workdir, "status", "--porcelain")
        if origin != repository.remote:
            raise ValueError(f"workspace origin mismatch before push: {origin}")
        if not branch.startswith("task/"):
            raise ValueError(f"refusing to push non-task branch: {branch}")
        if status:
            raise ValueError("executor left uncommitted changes")
        base = self._git(workdir, "rev-parse", f"origin/{repository.default_branch}")
        head = self._git(workdir, "rev-parse", "HEAD")
        if base == head:
            raise ValueError("executor produced no delivery commit")
        subprocess.run(
            ["git", "-C", str(workdir), "push", "origin", f"HEAD:refs/heads/{branch}"],
            check=True, capture_output=True, text=True,
        )
        return {"repository": repository.repository_id, "branch": branch, "commit": head}

    @staticmethod
    def _prepare_codex_home(destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        for name in ("auth.json",):
            source = Path.home() / ".codex" / name
            if source.is_file():
                shutil.copy2(source, destination / name)

    @staticmethod
    def _runner_environment() -> dict[str, str]:
        return {
            "PATH": "/usr/bin:/bin:/home/tony/.npm-global/bin",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "SSL_CERT_FILE": "/etc/ssl/certs/ca-certificates.crt",
        }

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

    def _prompt(self, task: dict[str, Any], repository: Any, workdir: Path, evidence_dir: Path) -> str:
        return json.dumps({
            "role": self.owner,
            "task_id": task["id"],
            "title": task["title"],
            "acceptance_criteria": task["acceptance_criteria"],
            "target_repository": repository.repository_id,
            "repository_remote": repository.remote,
            "workspace": str(workdir),
            "canonical_test": repository.canonical_test,
            "evidence_dir": str(evidence_dir),
            "constraints": "Commit and push verified changes only to the target repository. Internal evidence only; no external contact, pricing, publication, payment, customer data, or legal commitments.",
        }, ensure_ascii=False)
