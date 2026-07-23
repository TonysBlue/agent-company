"""Repository registry for the company control plane."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepositorySpec:
    repository_id: str
    kind: str
    remote: str
    local_path: Path
    default_branch: str
    canonical_test: str
    allowed_roles: tuple[str, ...]


class ProductRegistry:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != "agent-company-repositories/v1":
            raise ValueError("unsupported repository registry schema")
        rows = data.get("repositories")
        if not isinstance(rows, list) or not rows:
            raise ValueError("repository registry must contain repositories")
        ids = [row.get("id") for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("repository ids must be unique")
        self._specs = {
            row["id"]: RepositorySpec(
                repository_id=row["id"],
                kind=row["kind"],
                remote=row["remote"],
                local_path=Path(row["local_path"]),
                default_branch=row["default_branch"],
                canonical_test=row["canonical_test"],
                allowed_roles=tuple(row["allowed_roles"]),
            )
            for row in rows
        }
        self._role_defaults = dict(data.get("role_defaults", {}))
        self._domain_defaults = dict(data.get("domain_defaults", {}))

    def get(self, repository_id: str, *, role: str) -> RepositorySpec:
        try:
            spec = self._specs[repository_id]
        except KeyError as exc:
            raise ValueError(f"unknown repository: {repository_id}") from exc
        if role not in spec.allowed_roles:
            raise ValueError(f"role {role} is not allowed to write repository {repository_id}")
        return spec

    def for_role(self, role: str, domain: str) -> RepositorySpec:
        repository_id = self._domain_defaults.get(domain) or self._role_defaults.get(role)
        if not repository_id:
            raise ValueError(f"no repository policy for role={role} domain={domain}")
        return self.get(repository_id, role=role)

    def list(self) -> list[RepositorySpec]:
        return list(self._specs.values())
