"""Compile versioned, least-privilege task context bundles."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .config import CompanyConfig
from .db import Store, utcnow

SCHEMA_VERSION = "agent-company-task-context/v1"
ROLE_SLUGS = {
    "CEO": "ceo",
    "Product Engineer": "product-engineer",
    "Customer & Revenue": "customer-revenue",
    "Company Platform Engineer": "company-platform",
    "Control & Reliability Reviewer": "control-review",
    "Finance & Risk Reviewer": "finance-risk",
    "Legal/Compliance Specialist": "legal-compliance",
    "Independent Quality Reviewer": "independent-quality",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _version(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return fallback


class ContextCompiler:
    def __init__(self, config: CompanyConfig, *, context_root: Path | None = None):
        self.config = config
        self.store = Store(config.db_path)
        self.store.init()
        self.context_root = context_root or config.workspace / "company_context"

    def _read(self, path: Path) -> str:
        if not path.is_file():
            raise ValueError(f"required context source missing: {path}")
        return path.read_text(encoding="utf-8").strip()

    def compile(
        self,
        task_id: int,
        *,
        generation: int,
        role: str,
        repository: dict[str, Any],
    ) -> dict[str, Any]:
        slug = ROLE_SLUGS.get(role)
        if slug is None:
            raise ValueError(f"role has no context policy: {role}")
        common = {
            "constitution": self._read(self.context_root / "common" / "constitution.md"),
            "governance": self._read(self.context_root / "common" / "governance.md"),
        }
        public_roles: dict[str, dict[str, str]] = {}
        role_versions: dict[str, str] = {}
        for candidate, candidate_slug in sorted(ROLE_SLUGS.items()):
            path = self.context_root / "roles" / candidate_slug / "public-charter.md"
            if not path.is_file():
                continue
            text = self._read(path)
            public_roles[candidate] = {"public_charter": text, "version": _version(text, f"{candidate_slug}/public")}
            role_versions[candidate] = public_roles[candidate]["version"]
        private = self._read(self.context_root / "roles" / slug / "private-playbook.md")
        with self.store.connect() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"task not found: {task_id}")
            if task["owner"] != role:
                raise ValueError(f"task is owned by {task['owner']}, not {role}")
            state = conn.execute("SELECT * FROM ceo_state_versions ORDER BY version DESC LIMIT 1").fetchone()
            directives = [dict(row) for row in conn.execute(
                "SELECT directive_version, directive_type, objective, constraints_json, priority, status FROM chairman_directives WHERE status IN ('pending','active') ORDER BY directive_version"
            )]
            for row in directives:
                row["constraints"] = json.loads(row.pop("constraints_json"))
            continuity = conn.execute("SELECT * FROM role_continuity WHERE role=?", (role,)).fetchone()
            project = conn.execute("SELECT * FROM project_history WHERE repository_id=?", (repository["id"],)).fetchone()
            handoffs = [dict(row) for row in conn.execute(
                "SELECT id, task_id, from_role, to_role, handoff_type, summary, artifact_refs_json, decision_needed, status, created_at FROM handoffs WHERE (to_role=? OR from_role=?) AND status IN ('offered','accepted','needs_clarification') ORDER BY id DESC LIMIT 20",
                (role, role),
            )]
            for row in handoffs:
                row["artifact_refs"] = json.loads(row.pop("artifact_refs_json"))
        history = {
            "role": role,
            "role_continuity": dict(continuity) if continuity else None,
            "project": dict(project) if project else None,
            "open_handoffs": handoffs,
        }
        for item in (history["role_continuity"], history["project"]):
            if item:
                for key in tuple(item):
                    if key.endswith("_json"):
                        item[key[:-5]] = json.loads(item.pop(key))
        company_version = "+".join(_version(text, name) for name, text in common.items())
        strategy_version = int(state["version"]) if state else 0
        directive_version = int(state["active_directive_version"]) if state else 0
        bundle: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "context_id": f"ctx-task-{task_id}-generation-{generation}",
            "generated_at": utcnow(),
            "company": common,
            "organization": {"public_roles": public_roles},
            "role": {
                "name": role,
                "private_playbook": private,
                "version": _version(private, f"{slug}/private"),
            },
            "strategy": {
                "version": strategy_version,
                "strategy": json.loads(state["strategy_json"]) if state else {},
                "assumptions": json.loads(state["assumptions_json"]) if state else [],
                "critical_path": json.loads(state["critical_path_json"]) if state else [],
                "directives": directives,
            },
            "task": dict(task),
            "repository": repository,
            "history": history,
            "governance": {
                "external_action_authorized": False,
                "customer_data_allowed": False,
                "data_classification": "internal",
                "rule_precedence": [
                    "chairman_directive", "constitution", "safety_legal_data", "strategy",
                    "role_charter", "task_contract", "repository_policy", "private_playbook", "history", "draft",
                ],
            },
            "provenance": {
                "company_context_version": company_version,
                "role_context_version": _version(private, f"{slug}/private"),
                "directive_version": directive_version,
                "strategy_version": strategy_version,
                "source_versions": {"public_roles": role_versions},
                "bundle_sha256": None,
            },
        }
        bundle["provenance"]["bundle_sha256"] = hashlib.sha256(_canonical(bundle).encode("utf-8")).hexdigest()
        with self.store.connect() as conn:
            now = utcnow()
            conn.execute("UPDATE task_contexts SET status='superseded', superseded_at=? WHERE task_id=? AND status='active'", (now, task_id))
            conn.execute(
                """INSERT INTO task_contexts(
                       task_id, generation, role, schema_version, company_context_version,
                       role_context_version, directive_version, strategy_version, bundle_sha256,
                       bundle_path, source_versions_json, status, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'active', ?)
                   ON CONFLICT(task_id, generation) DO UPDATE SET
                       role=excluded.role, schema_version=excluded.schema_version,
                       company_context_version=excluded.company_context_version,
                       role_context_version=excluded.role_context_version,
                       directive_version=excluded.directive_version, strategy_version=excluded.strategy_version,
                       bundle_sha256=excluded.bundle_sha256, source_versions_json=excluded.source_versions_json,
                       status='active', superseded_at=NULL""",
                (task_id, generation, role, SCHEMA_VERSION, company_version,
                 bundle["role"]["version"], directive_version, strategy_version,
                 bundle["provenance"]["bundle_sha256"], _canonical(bundle["provenance"]["source_versions"]), now),
            )
        return bundle

    def assert_current(self, task_id: int, generation: int, bundle_sha256: str) -> None:
        with self.store.connect() as conn:
            row = conn.execute(
                "SELECT * FROM task_contexts WHERE task_id=? AND generation=?",
                (task_id, generation),
            ).fetchone()
            state = conn.execute("SELECT version, active_directive_version FROM ceo_state_versions ORDER BY version DESC LIMIT 1").fetchone()
        if row is None or row["status"] != "active" or row["bundle_sha256"] != bundle_sha256:
            raise ValueError("task context is missing, superseded, or tampered")
        if state and (int(row["strategy_version"]) != int(state["version"]) or int(row["directive_version"]) != int(state["active_directive_version"])):
            raise ValueError("task context is stale after strategy or Chairman directive change")

    def materialize(self, workspace: Path, bundle: dict[str, Any]) -> dict[str, Any]:
        context_dir = workspace / ".agent-company"
        context_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "TASK_CONTEXT.json": json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            "COMPANY_CONTEXT.md": "\n\n".join(bundle["company"].values()) + "\n",
            "ORGANIZATION.md": "\n\n".join(
                row["public_charter"] for row in bundle["organization"]["public_roles"].values()
            ) + "\n",
            "ROLE_PRIVATE.md": bundle["role"]["private_playbook"] + "\n",
            "RELEVANT_HISTORY.md": "# Relevant History\n\n```json\n" + json.dumps(bundle["history"], ensure_ascii=False, indent=2, sort_keys=True) + "\n```\n",
        }
        paths: dict[str, str] = {}
        for name, content in files.items():
            path = context_dir / name
            path.write_text(content, encoding="utf-8")
            os.chmod(path, 0o444)
            paths[name] = str(path)
        manifest = {
            "schema_version": "agent-company-context-manifest/v1",
            "context_id": bundle["context_id"],
            "bundle_sha256": bundle["provenance"]["bundle_sha256"],
            "files": paths,
        }
        manifest_path = context_dir / "CONTEXT_MANIFEST.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(manifest_path, 0o444)
        task_id = int(bundle["task"]["id"])
        generation = int(bundle["context_id"].rsplit("-", 1)[1])
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE task_contexts SET bundle_path=? WHERE task_id=? AND generation=?",
                (str(context_dir / "TASK_CONTEXT.json"), task_id, generation),
            )
        return manifest
