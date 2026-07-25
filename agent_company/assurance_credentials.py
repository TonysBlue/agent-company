"""Local principal credential lifecycle without secret disclosure."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path
from typing import Any

from .assurance import AssuranceError, AssuranceKernel
from .config import CompanyConfig
from .db import Store, utcnow


class CredentialManager:
    def __init__(self, config: CompanyConfig):
        self.config = config
        self.store = Store(config.db_path)
        self.kernel = AssuranceKernel(config)
        self.data_dir = config.workspace / "data"
        self.credential_dir = self.data_dir / "assurance-credentials"
        self.bootstrap_file = self.data_dir / "assurance-bootstrap.secret"

    @staticmethod
    def _require_0600(path: Path) -> None:
        if path.stat().st_mode & 0o777 != 0o600:
            raise ValueError(f"credential file must use 0600 permissions: {path}")

    @staticmethod
    def _atomic_secret(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value + "\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)

    def bootstrap(self) -> dict[str, str]:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.bootstrap_file.exists():
            self._require_0600(self.bootstrap_file)
            status = "existing"
        else:
            self._atomic_secret(self.bootstrap_file, secrets.token_urlsafe(48))
            status = "created"
        return {"status": status, "bootstrap_file": str(self.bootstrap_file)}

    def _authenticate_bootstrap(self, supplied: str) -> None:
        if not self.bootstrap_file.exists():
            raise AssuranceError("assurance bootstrap is not initialized")
        self._require_0600(self.bootstrap_file)
        import hmac
        if not hmac.compare_digest(self.bootstrap_file.read_text(encoding="utf-8").strip(), supplied):
            raise AssuranceError("invalid assurance bootstrap credential")

    def _credential_file(self, principal_id: str) -> Path:
        if not principal_id or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for char in principal_id):
            raise ValueError("principal id contains unsupported characters")
        return self.credential_dir / f"{principal_id}.credential"

    def provision(
        self, principal_id: str, actor: str, authority: str, *, bootstrap_secret: str,
    ) -> dict[str, str]:
        self._authenticate_bootstrap(bootstrap_secret)
        credential = self.kernel.register_principal(
            principal_id, actor, authority, bootstrap_secret=bootstrap_secret,
        )
        path = self._credential_file(principal_id)
        self._atomic_secret(path, credential)
        return {"principal_id": principal_id, "actor": actor, "authority": authority,
                "status": "active", "credential_file": str(path)}

    def rotate(self, principal_id: str, *, bootstrap_secret: str) -> dict[str, str]:
        self._authenticate_bootstrap(bootstrap_secret)
        self.kernel.init()
        with self.store.connect_readonly() as conn:
            row = conn.execute(
                "SELECT actor,authority,status FROM assurance_principals WHERE principal_id=?", (principal_id,)
            ).fetchone()
        if row is None or row["status"] != "active":
            raise ValueError("active principal not found")
        return self.provision(
            principal_id, row["actor"], row["authority"], bootstrap_secret=bootstrap_secret,
        )

    def revoke(self, principal_id: str, *, bootstrap_secret: str) -> dict[str, str]:
        self._authenticate_bootstrap(bootstrap_secret)
        self.kernel.init()
        with self.store.connect() as conn:
            if conn.execute(
                "UPDATE assurance_principals SET status='revoked',credential_sha256=NULL WHERE principal_id=?",
                (principal_id,),
            ).rowcount != 1:
                raise ValueError("principal not found")
        self._credential_file(principal_id).unlink(missing_ok=True)
        return {"principal_id": principal_id, "status": "revoked"}

    def list_principals(self) -> list[dict[str, Any]]:
        self.kernel.init()
        with self.store.connect_readonly() as conn:
            rows = conn.execute(
                "SELECT principal_id,actor,authority,status,created_at FROM assurance_principals ORDER BY principal_id"
            ).fetchall()
        return [dict(row) for row in rows]
