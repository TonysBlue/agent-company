from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.assurance import AssuranceError, AssuranceKernel
from agent_company.assurance_credentials import CredentialManager
from agent_company.config import load_config
from agent_company.db import Store


class AssuranceCredentialTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[paths]\ndatabase=data/company.sqlite3\nartifacts=data/artifacts\nlogs=logs\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        Store(self.config.db_path).init()
        AssuranceKernel(self.config).init()
        self.manager = CredentialManager(self.config)

    def tearDown(self) -> None:
        os.chdir(self.old)
        self.tmp.cleanup()

    def test_bootstrap_creates_owner_only_secret_and_principal_file_without_returning_secret(self) -> None:
        bootstrap = self.manager.bootstrap()
        self.assertEqual(bootstrap["status"], "created")
        bootstrap_file = self.root / "data" / "assurance-bootstrap.secret"
        self.assertEqual(bootstrap_file.stat().st_mode & 0o777, 0o600)

        registered = self.manager.provision(
            "principal-evaluator", "Trusted Evaluator", "operator",
            bootstrap_secret=bootstrap_file.read_text(encoding="utf-8").strip(),
        )
        credential_file = Path(registered["credential_file"])
        self.assertEqual(credential_file.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("credential", registered)
        listed = self.manager.list_principals()
        self.assertNotIn("credential", str(listed).lower())
        self.assertNotIn(credential_file.read_text(encoding="utf-8").strip(), str(listed))

    def test_rotation_invalidates_old_credential_and_revocation_blocks_use(self) -> None:
        self.manager.bootstrap()
        bootstrap_file = self.root / "data" / "assurance-bootstrap.secret"
        secret = bootstrap_file.read_text(encoding="utf-8").strip()
        provisioned = self.manager.provision(
            "principal-ceo", "CEO", "executive", bootstrap_secret=secret,
        )
        path = Path(provisioned["credential_file"])
        old = path.read_text(encoding="utf-8").strip()
        rotated = self.manager.rotate("principal-ceo", bootstrap_secret=secret)
        new = path.read_text(encoding="utf-8").strip()
        self.assertNotEqual(old, new)
        self.assertNotIn("credential", rotated)
        os.environ["ASSURANCE_CREDENTIAL_PRINCIPAL_CEO"] = old
        with self.assertRaisesRegex(AssuranceError, "unauthenticated"):
            AssuranceKernel(self.config).create_initiative(
                "old-token", "Old token", "control-plane-reliability", "C2",
                actor="CEO", principal_id="principal-ceo",
            )
        os.environ["ASSURANCE_CREDENTIAL_PRINCIPAL_CEO"] = new
        AssuranceKernel(self.config).create_initiative(
            "new-token", "New token", "control-plane-reliability", "C2",
            actor="CEO", principal_id="principal-ceo",
        )
        self.manager.revoke("principal-ceo", bootstrap_secret=secret)
        self.assertFalse(path.exists())
        with self.assertRaisesRegex(AssuranceError, "unauthenticated"):
            AssuranceKernel(self.config).create_initiative(
                "revoked", "Revoked", "control-plane-reliability", "C2",
                actor="CEO", principal_id="principal-ceo",
            )
        os.environ.pop("ASSURANCE_CREDENTIAL_PRINCIPAL_CEO", None)

    def test_bootstrap_and_credential_files_reject_unsafe_permissions(self) -> None:
        bootstrap = self.manager.bootstrap()
        path = Path(bootstrap["bootstrap_file"])
        os.chmod(path, 0o644)
        with self.assertRaisesRegex(ValueError, "0600"):
            self.manager.provision(
                "principal-platform", "Company Platform Engineer", "implementer",
                bootstrap_secret=path.read_text(encoding="utf-8").strip(),
            )
        path.unlink()
        captured = self.root / "captured-bootstrap"
        captured.write_text("secret\n", encoding="utf-8")
        captured.chmod(0o600)
        path.symlink_to(captured)
        with self.assertRaisesRegex(ValueError, "regular"):
            self.manager.provision(
                "principal-platform", "Company Platform Engineer", "implementer",
                bootstrap_secret="secret",
            )
        temporary = self.root / "data" / "assurance-credentials" / "principal-platform.credential.tmp"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.symlink_to(captured)
        with self.assertRaisesRegex(ValueError, "temporary"):
            CredentialManager._atomic_secret(temporary.with_suffix(""), "new")


if __name__ == "__main__":
    unittest.main()
