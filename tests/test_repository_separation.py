from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_company.product_registry import ProductRegistry
from agent_company.workspace_manager import WorkspaceManager


class RepositorySeparationTest(unittest.TestCase):
    def test_registry_resolves_company_and_product_repositories(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = ProductRegistry(root / "config" / "repositories.json")

        company = registry.for_role("Company Platform Engineer", "platform")
        product = registry.for_role("Product Engineer", "product")

        self.assertEqual(company.repository_id, "agent-company")
        self.assertEqual(company.local_path, Path("/home/tony/agent-company"))
        self.assertEqual(product.repository_id, "pixweave")
        self.assertEqual(product.local_path, Path("/home/tony/products/pixweave"))
        self.assertEqual(product.remote, "git@github.com:TonysBlue/PixWeave.git")

    def test_workspace_paths_are_role_and_task_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = WorkspaceManager(Path(tmp))
            path = manager.path_for("Company Platform Engineer", 42, "agent-company")
            other = manager.path_for("Product Engineer", 42, "pixweave")

            self.assertEqual(path, Path(tmp) / "company-platform" / "task-42-agent-company")
            self.assertEqual(other, Path(tmp) / "product-engineer" / "task-42-pixweave")
            self.assertNotEqual(path, other)

    def test_customer_role_cannot_target_company_source_repository(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = ProductRegistry(root / "config" / "repositories.json")
        with self.assertRaisesRegex(ValueError, "not allowed"):
            registry.get("agent-company", role="Customer & Revenue")


if __name__ == "__main__":
    unittest.main()
