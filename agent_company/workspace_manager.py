"""Role- and task-scoped workspace paths."""

from __future__ import annotations

import re
from pathlib import Path


ROLE_SLUGS = {
    "Company Platform Engineer": "company-platform",
    "Control & Reliability Reviewer": "control-review",
    "Product Engineer": "product-engineer",
    "Customer & Revenue": "customer-revenue",
    "CEO": "ceo",
}


class WorkspaceManager:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def path_for(self, role: str, task_id: int, repository_id: str) -> Path:
        if task_id <= 0:
            raise ValueError("task_id must be positive")
        role_slug = ROLE_SLUGS.get(role)
        if role_slug is None:
            role_slug = self._slug(role)
        repo_slug = self._slug(repository_id)
        return self.root / role_slug / f"task-{task_id}-{repo_slug}"

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
        if not slug:
            raise ValueError("workspace identifier must not be empty")
        return slug
