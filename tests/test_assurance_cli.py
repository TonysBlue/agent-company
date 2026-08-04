from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from agent_company.assurance import AssuranceKernel
from agent_company.cli import main as cli_main
from agent_company.config import load_config
from agent_company.db import Store
from agent_company.integrity import signature as integrity_signature


class AssuranceCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            "[paths]\ndatabase=data/company.sqlite3\nartifacts=data/artifacts\nlogs=logs\n",
            encoding="utf-8",
        )
        os.chdir(self.root)

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> dict:
        code, payload = self.run_cli_with_code(*args)
        self.assertEqual(code, 0)
        return payload

    def run_cli_with_code(self, *args: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main([*args])
        return code, json.loads(output.getvalue())

    def insert_legacy_artifact(self) -> dict[str, object]:
        config = load_config()
        kernel = AssuranceKernel(config)
        kernel.init()
        payload = {
            "schema_version": "assurance-artifact/v1",
            "artifact_id": "assurance-bootstrap-goal",
            "kind": "goal_contract",
            "version": 1,
            "status": "draft",
            "initiative_id": "development-assurance-bootstrap",
            "profile": "control-plane-reliability",
            "risk_class": "C2",
            "owner_principal": "principal-platform",
            "repository_id": "agent-company",
            "content": {
                "outcome": "Establish a non-blocking shadow assurance kernel",
                "non_goals": ["enforce task gates", "modify PixWeave"],
            },
        }
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        digest = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        registered_at = "2026-07-24T03:59:59+00:00"
        approved_at = "2026-07-24T03:59:59+00:00"
        with Store(config.db_path).connect() as conn:
            conn.executemany(
                """INSERT INTO assurance_principals(
                       principal_id,actor,authority,status,created_at
                   ) VALUES (?,?,?,'active','2026-07-24T00:00:00+00:00')""",
                [
                    ("principal-platform", "Company Platform Engineer", "implementer"),
                    ("principal-ceo", "CEO", "executive"),
                ],
            )
            conn.execute(
                """INSERT INTO assurance_initiatives(
                       initiative_id,profile,risk_class,title,owner_principal,status,
                       mode,created_at,updated_at
                   ) VALUES (?,?,?,?,?,'discovery','shadow',?,?)""",
                (
                    payload["initiative_id"], payload["profile"], payload["risk_class"],
                    "Legacy Phase C", payload["owner_principal"], registered_at,
                    registered_at,
                ),
            )
            conn.execute(
                """INSERT INTO assurance_artifacts(
                       artifact_id,initiative_id,kind,version,status,profile,risk_class,
                       owner_principal,repository_id,content_json,content_sha256,
                       approved_by_principal,approved_at,created_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["artifact_id"], payload["initiative_id"], payload["kind"],
                    payload["version"], "approved", payload["profile"],
                    payload["risk_class"], payload["owner_principal"],
                    payload["repository_id"], canonical, digest, "principal-ceo",
                    approved_at, registered_at,
                ),
            )
            conn.executemany(
                """INSERT INTO audit_log(
                       ts,actor,action,entity,entity_id,details
                   ) VALUES (?,?,?,?,?,?)""",
                [
                    (
                        registered_at, "Company Platform Engineer",
                        "assurance_artifact_registered", "assurance_artifact",
                        "assurance-bootstrap-goal:v1", json.dumps({
                            "kind": "goal_contract", "mode": "shadow",
                            "principal_id": "principal-platform", "sha256": digest,
                        }, sort_keys=True),
                    ),
                    (
                        approved_at, "CEO", "assurance_artifact_approved",
                        "assurance_artifact", "assurance-bootstrap-goal:v1", json.dumps({
                            "mode": "shadow", "principal_id": "principal-ceo",
                        }, sort_keys=True),
                    ),
                ],
            )
        return {"payload": payload, "canonical": canonical, "sha256": digest}

    def operational_snapshot(self) -> dict[str, list[dict[str, object]]]:
        with Store(load_config().db_path).connect_readonly() as conn:
            tables = [
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
                if not row["name"].startswith(("assurance_", "trusted_eval_", "sqlite_"))
            ]
            return {
                table: [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                for table in tables
            }

    def test_shadow_commands_init_classify_list_and_integrity(self) -> None:
        Store(load_config().db_path).init()
        initialized = self.run_cli("assurance-init")
        self.assertEqual(initialized["mode"], "shadow")
        with Store(load_config().db_path).connect_readonly() as conn:
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='trusted_eval_contracts'"
            ).fetchone())
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trusted_eval_%'"
            ).fetchone()[0], 10)
            phase_c_tables = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertTrue({
                "assurance_artifact_registrations", "assurance_artifact_approvals",
                "assurance_artifact_lifecycle", "trusted_eval_runs",
                "trusted_eval_manifests", "trusted_eval_quarantines",
                "trusted_eval_contracts", "trusted_eval_evaluator_credentials",
                "assurance_task_bindings",
                "assurance_pilot_config", "assurance_execution_bindings",
                "assurance_claim_bindings", "assurance_pilot_claim_history",
                "assurance_completion_bindings",
            } <= phase_c_tables)
            phase_c_triggers = {
                row["name"] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                )
            }
            self.assertTrue({
                "assurance_artifact_registrations_immutable_update",
                "assurance_artifact_registrations_immutable_delete",
                "assurance_artifact_approvals_immutable_update",
                "assurance_artifact_approvals_immutable_delete",
                "assurance_artifact_lifecycle_immutable_update",
                "assurance_artifact_lifecycle_immutable_delete",
                "assurance_artifact_status_transition_guard",
                "assurance_execution_bindings_immutable_update",
                "assurance_execution_bindings_immutable_delete",
                "assurance_claim_bindings_immutable_update",
                "assurance_claim_bindings_immutable_delete",
                "assurance_pilot_claim_history_immutable_update",
                "assurance_pilot_claim_history_immutable_delete",
                "assurance_task_bindings_claimed_immutable_update",
                "assurance_task_bindings_claimed_immutable_delete",
                "assurance_completion_bindings_insert_guard",
                "assurance_completion_bindings_immutable_update",
                "assurance_completion_bindings_immutable_delete",
                "tasks_bound_pilot_completion_guard",
                "trusted_eval_runs_immutable_update",
                "trusted_eval_runs_immutable_delete",
                "trusted_eval_manifests_immutable_update",
                "trusted_eval_manifests_immutable_delete",
                "trusted_eval_contracts_immutable_update",
                "trusted_eval_contracts_immutable_delete",
                "trusted_eval_quarantines_append_only",
                "trusted_eval_quarantines_no_delete",
                "trusted_eval_evaluator_credentials_immutable_update",
                "trusted_eval_evaluator_credentials_immutable_delete",
            } <= phase_c_triggers)
        credential = "test-cli-ceo-credential"
        os.environ["ASSURANCE_CREDENTIAL_PRINCIPAL_CEO"] = credential
        with Store(load_config().db_path).connect() as conn:
            conn.execute(
                """INSERT INTO assurance_principals(
                       principal_id,actor,authority,credential_sha256,status,created_at
                   ) VALUES ('principal-ceo','CEO','executive',?,'active','2026-07-24T00:00:00+00:00')""",
                (hashlib.sha256(credential.encode()).hexdigest(),),
            )
        result = self.run_cli(
            "assurance-classify", "--actor", "CEO", "--principal-id", "principal-ceo",
            "--title", "Assurance bootstrap", "--persistent-schema",
        )
        self.assertEqual(result["risk_class"], "C2")
        self.assertEqual(result["mode"], "shadow")
        listing = self.run_cli("assurance-list")
        self.assertEqual(listing, [])
        integrity = self.run_cli("assurance-integrity")
        self.assertEqual(integrity["status"], "ok")
        self.assertEqual(Store(load_config().db_path).fetch_one("SELECT COUNT(*) AS c FROM tasks")["c"], 2)

    def test_assurance_init_migrates_reconciled_legacy_artifact_idempotently(self) -> None:
        Store(load_config().db_path).init()
        self.insert_legacy_artifact()
        before = self.operational_snapshot()

        first = self.run_cli("assurance-init")
        config = load_config()
        config.chairman_inbox.mkdir(parents=True)
        config.chairman_outbox.mkdir(parents=True)
        validation = self.run_cli("validate")
        second = self.run_cli("assurance-init")

        self.assertEqual(first["migration"]["status"], "ok")
        self.assertEqual(first["migration"]["anchors_backfilled"], {
            "approvals": 1, "lifecycle": 2, "registrations": 1,
        })
        self.assertEqual(first["integrity"]["status"], "ok")
        self.assertEqual(validation, {"errors": [], "ok": True})
        self.assertEqual(second["migration"]["anchors_backfilled"], {
            "approvals": 0, "lifecycle": 0, "registrations": 0,
        })
        self.assertEqual(second["integrity"]["status"], "ok")
        self.assertEqual(self.operational_snapshot(), before)
        with Store(load_config().db_path).connect_readonly() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_registrations"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_approvals"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_lifecycle"
            ).fetchone()[0], 2)

    def test_assurance_init_rejects_tampered_legacy_artifact_without_partial_anchors(self) -> None:
        Store(load_config().db_path).init()
        legacy = self.insert_legacy_artifact()
        payload = dict(legacy["payload"])
        payload["content"] = {
            "outcome": "Tampered mutable content",
            "non_goals": ["deploy"],
        }
        rewritten = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        )
        rewritten_sha256 = hashlib.sha256(rewritten.encode("ascii")).hexdigest()
        with Store(load_config().db_path).connect() as conn:
            conn.execute(
                """UPDATE assurance_artifacts
                   SET content_json=?,content_sha256=?
                   WHERE artifact_id='assurance-bootstrap-goal'""",
                (rewritten, rewritten_sha256),
            )
        before = self.operational_snapshot()

        code, result = self.run_cli_with_code("assurance-init")

        self.assertEqual(code, 1)
        self.assertEqual(result["migration"]["status"], "integrity_conflict")
        self.assertEqual(result["migration"]["conflicts"], [{
            "artifact_id": "assurance-bootstrap-goal",
            "reason": "content hash does not match approved legacy manifest", "version": 1,
        }])
        self.assertEqual(result["integrity"]["status"], "integrity_conflict")
        self.assertEqual(self.operational_snapshot(), before)
        with Store(load_config().db_path).connect_readonly() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_registrations"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_approvals"
            ).fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_lifecycle"
            ).fetchone()[0], 0)

        with Store(load_config().db_path).connect() as conn:
            conn.execute(
                """UPDATE assurance_artifacts
                   SET content_json=?,content_sha256=?
                   WHERE artifact_id='assurance-bootstrap-goal'""",
                (legacy["canonical"], "0" * 64),
            )

        code, result = self.run_cli_with_code("assurance-init")

        self.assertEqual(code, 1)
        self.assertEqual(result["migration"]["conflicts"], [{
            "artifact_id": "assurance-bootstrap-goal",
            "reason": "content hash does not match approved legacy manifest", "version": 1,
        }])
        with Store(load_config().db_path).connect_readonly() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_registrations"
            ).fetchone()[0], 0)

    def test_assurance_init_rejects_legacy_approval_metadata_mismatch(self) -> None:
        Store(load_config().db_path).init()
        self.insert_legacy_artifact()
        with Store(load_config().db_path).connect() as conn:
            conn.execute(
                """UPDATE assurance_artifacts SET approved_at='2026-07-24T04:00:00+00:00'
                   WHERE artifact_id='assurance-bootstrap-goal'"""
            )

        code, result = self.run_cli_with_code("assurance-init")

        self.assertEqual(code, 1)
        self.assertEqual(result["migration"]["conflicts"], [{
            "artifact_id": "assurance-bootstrap-goal",
            "reason": "approval metadata does not match approved legacy manifest",
            "version": 1,
        }])
        with Store(load_config().db_path).connect_readonly() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_registrations"
            ).fetchone()[0], 0)

    def test_assurance_init_rejects_legacy_audit_evidence_mismatch(self) -> None:
        Store(load_config().db_path).init()
        legacy = self.insert_legacy_artifact()
        with Store(load_config().db_path).connect() as conn:
            audit = conn.execute(
                """SELECT id,details FROM audit_log
                   WHERE action='assurance_artifact_registered'
                     AND entity_id='assurance-bootstrap-goal:v1'"""
            ).fetchone()
            details = json.loads(audit["details"])
            details["sha256"] = "0" * 64
            conn.execute(
                "UPDATE audit_log SET details=? WHERE id=?",
                (json.dumps(details, sort_keys=True), audit["id"]),
            )

        code, result = self.run_cli_with_code("assurance-init")

        self.assertEqual(code, 1)
        self.assertEqual(result["migration"]["conflicts"], [{
            "artifact_id": "assurance-bootstrap-goal",
            "reason": "registration audit evidence mismatch", "version": 1,
        }])
        with Store(load_config().db_path).connect_readonly() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_registrations"
            ).fetchone()[0], 0)

        with Store(load_config().db_path).connect() as conn:
            audit = conn.execute(
                """SELECT id,details FROM audit_log
                   WHERE action='assurance_artifact_registered'
                     AND entity_id='assurance-bootstrap-goal:v1'"""
            ).fetchone()
            details = json.loads(audit["details"])
            details["sha256"] = legacy["sha256"]
            conn.execute(
                "UPDATE audit_log SET details=? WHERE id=?",
                (json.dumps(details, sort_keys=True), audit["id"]),
            )

        migrated = self.run_cli("assurance-init")
        self.assertEqual(migrated["migration"]["anchors_backfilled"], {
            "approvals": 1, "lifecycle": 2, "registrations": 1,
        })
        with Store(load_config().db_path).connect() as conn:
            conn.execute(
                """UPDATE audit_log SET actor='tampered-actor'
                   WHERE action='assurance_artifact_approved'
                     AND entity_id='assurance-bootstrap-goal:v1'"""
            )

        code, result = self.run_cli_with_code("assurance-init")

        self.assertEqual(code, 1)
        self.assertEqual(result["migration"]["conflicts"], [{
            "artifact_id": "assurance-bootstrap-goal",
            "reason": "approval audit evidence mismatch", "version": 1,
        }])
        with Store(load_config().db_path).connect_readonly() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_registrations"
            ).fetchone()[0], 1)

    def test_assurance_init_does_not_mutate_partial_anchors_before_audit_conflict(self) -> None:
        Store(load_config().db_path).init()
        legacy = self.insert_legacy_artifact()
        config = load_config()
        registration = {
            "artifact_id": "assurance-bootstrap-goal",
            "version": 1,
            "content_sha256": legacy["sha256"],
            "created_at": "2026-07-24T03:59:59+00:00",
        }
        approval = {
            "artifact_id": "assurance-bootstrap-goal",
            "version": 1,
            "content_sha256": legacy["sha256"],
            "approved_by_principal": "principal-ceo",
            "approved_at": "2026-07-24T03:59:59+00:00",
        }
        with Store(config.db_path).connect() as conn:
            conn.execute(
                """INSERT INTO assurance_artifact_registrations(
                       artifact_id,version,content_sha256,created_at,integrity_signature
                   ) VALUES (?,?,?,?,?)""",
                (*registration.values(), integrity_signature(
                    config.db_path, "artifact-registration", registration,
                )),
            )
            conn.execute(
                """INSERT INTO assurance_artifact_approvals(
                       artifact_id,version,content_sha256,approved_by_principal,
                       approved_at,integrity_signature
                   ) VALUES (?,?,?,?,?,?)""",
                (*approval.values(), integrity_signature(
                    config.db_path, "artifact-approval", approval,
                )),
            )
            conn.execute(
                """UPDATE audit_log SET actor='tampered-actor'
                   WHERE action='assurance_artifact_approved'
                     AND entity_id='assurance-bootstrap-goal:v1'"""
            )

        code, result = self.run_cli_with_code("assurance-init")

        self.assertEqual(code, 1)
        self.assertEqual(result["migration"]["conflicts"], [{
            "artifact_id": "assurance-bootstrap-goal",
            "reason": "approval audit evidence mismatch", "version": 1,
        }])
        with Store(config.db_path).connect_readonly() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_registrations"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_approvals"
            ).fetchone()[0], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM assurance_artifact_lifecycle"
            ).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
