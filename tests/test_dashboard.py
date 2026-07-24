from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_company.config import load_config
from agent_company.dashboard import DashboardApp, build_snapshot
from agent_company.db import Store


class DashboardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd = Path.cwd()
        self.root = Path(self.tmp.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "sample.ini").write_text(
            """
[company]
product_name = Test PixWeave

[paths]
database = data/company.sqlite3
chairman_inbox = data/chairman/inbox
chairman_outbox = data/chairman/outbox
artifacts = data/artifacts
logs = logs
""".strip()
            + "\n",
            encoding="utf-8",
        )
        os.chdir(self.root)
        self.config = load_config()
        self.store = Store(self.config.db_path)
        self.store.init()
        self._seed_operating_data()

    def tearDown(self) -> None:
        os.chdir(self.old_cwd)
        self.tmp.cleanup()

    def _seed_operating_data(self) -> None:
        now = "2026-07-11T01:02:03+00:00"
        with self.store.connect() as conn:
            conn.execute("DELETE FROM tasks")
            conn.execute("DELETE FROM approvals")
            conn.execute("DELETE FROM cycles")
            conn.execute("DELETE FROM experiments")
            conn.execute("DELETE FROM audit_log")
            conn.execute(
                """INSERT INTO tasks(
                       id, created_at, updated_at, owner, title, domain, status,
                       priority, blocked_reason, result, acceptance_criteria
                   ) VALUES (1, ?, ?, 'CTO', 'Build dashboard', 'engineering',
                   'in_progress', 90, NULL, NULL, 'Tests and curl evidence pass.')""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO tasks(
                       id, created_at, updated_at, owner, title, domain, status,
                       priority, blocked_reason, result, acceptance_criteria
                   ) VALUES (2, ?, ?, 'CRO', 'Publish launch pricing', 'gtm',
                   'blocked', 80, 'Pending Chairman approval #1', NULL,
                   'Chairman decision recorded before external action.')""",
                (now, now),
            )
            conn.execute(
                """INSERT INTO approvals(
                       id, created_at, requested_by, action_type, summary, status
                   ) VALUES (1, ?, 'CRO', 'pricing_change',
                   'Task 2 requires Chairman decision before continuing: Publish launch pricing',
                   'pending')""",
                (now,),
            )
            conn.execute(
                "INSERT INTO cycles(id, started_at, finished_at, summary) VALUES (1, ?, ?, ?)",
                (now, now, json.dumps({"processed": 2, "progressed": [1], "escalated": [2]})),
            )
            conn.execute(
                """INSERT INTO experiments(
                       id, created_at, owner, name, hypothesis, metric, status, result
                   ) VALUES (1, ?, 'CRO', 'Message test', 'ICP responds to control',
                   'rubric_score_difference', 'draft', NULL)""",
                (now,),
            )
            conn.execute(
                """INSERT INTO audit_log(
                       id, ts, actor, action, entity, entity_id, details
                   ) VALUES (1, ?, 'CEO', 'run_cycle', 'cycle', '1', ?)""",
                (now, json.dumps({"processed": 2})),
            )
        artifact = self.config.artifacts_dir / "build-evidence.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text('{"schema_version":"test/v1"}\n', encoding="utf-8")

    def test_snapshot_uses_live_sources_and_no_fabricated_operations_metrics(self) -> None:
        snapshot = build_snapshot(self.config)

        self.assertEqual(snapshot["management"]["task_counts_by_status"]["blocked"], 1)
        self.assertEqual(snapshot["management"]["task_counts_by_owner"]["CTO"], 1)
        self.assertEqual(snapshot["management"]["human_dependencies"][0]["approval_id"], 1)
        self.assertEqual(snapshot["project"]["experiments"][0]["name"], "Message test")
        self.assertIn("git", snapshot["project"])
        self.assertEqual(snapshot["operations"]["launch_state"], "internal_beta")
        self.assertIsNone(snapshot["operations"]["funnel"][0]["value"])
        self.assertIn("context_governance", snapshot["management"])
        self.assertIn("active_contexts", snapshot["management"]["context_governance"])
        serialized = json.dumps(snapshot["management"]["context_governance"])
        self.assertNotIn("verified_facts_json", serialized)
        self.assertNotIn("open_items_json", serialized)
        self.assertNotIn("artifact_refs_json", serialized)
        self.assertNotIn("bundle_path", serialized)
        self.assertNotIn("fencing_token", serialized)
        self.assertEqual(snapshot["operations"]["funnel"][0]["state"], "placeholder")
        sources = {source["id"] for source in snapshot["sources"]}
        self.assertIn("sqlite", sources)
        self.assertIn("git", sources)

    def test_dashboard_reflects_event_driven_ceo_and_self_serve_subscription_model(self) -> None:
        now = "2026-07-11T01:02:03+00:00"
        with self.store.connect() as conn:
            conn.execute(
                """UPDATE event_worker_state SET status='waiting', worker_id='worker-1',
                           process_id=123, heartbeat_at=?, events_processed=9 WHERE singleton=1""",
                (now,),
            )
            conn.execute(
                """INSERT INTO chairman_directives(
                       directive_version, schema_version, created_at, source_platform,
                       source_session_id, source_message_id, source_message_sha256,
                       directive_type, objective, constraints_json, priority, status
                   ) VALUES (1, 'chairman-directive/v1', ?, 'weixin', 's1', 'm1', ?,
                             'strategy', '标准化自助订阅，不依赖商业谈判', '[\"订阅制\"]', 100, 'pending')""",
                (now, "a" * 64),
            )
            conn.execute(
                """INSERT INTO execution_events(
                       created_at, available_at, event_type, entity_type, entity_id,
                       payload, status, priority
                   ) VALUES (?, ?, 'ceo.business_stall_review', 'strategic_phase', '1',
                             '{}', 'pending', 70)""",
                (now, "2026-07-12T01:02:03+00:00"),
            )
            conn.execute(
                """INSERT INTO audit_log(ts, actor, action, entity, entity_id, details)
                   VALUES (?, 'Customer & Revenue', 'prepare_subscription_positioning',
                           'task', '3', '{}')""",
                ("2026-07-11T01:03:03+00:00",),
            )

        snapshot = build_snapshot(self.config)
        management = snapshot["management"]
        self.assertEqual(management["operating_model"], "事件驱动 7×24")
        self.assertEqual(management["worker"]["status"], "waiting")
        self.assertEqual(management["ceo_runtime"]["logical_ceo_count"], 1)
        self.assertEqual(management["chairman_directives"][0]["objective"], "标准化自助订阅，不依赖商业谈判")
        self.assertEqual(management["pending_events"][0]["event_type"], "ceo.business_stall_review")
        activity = management["role_activity_timeline"]
        self.assertTrue(any(item["role"] == "Customer & Revenue" for item in activity))
        self.assertTrue(any(item["source"] == "审计" for item in activity))

        body = DashboardApp(self.config).render_path("/management").body
        self.assertIn("唯一逻辑 CEO", body)
        self.assertIn("Agent 上下文治理", body)
        self.assertIn("角色连续性记忆", body)
        self.assertIn("跨角色结构化交接", body)
        self.assertIn("事件 Worker", body)
        self.assertIn("待处理事件", body)
        self.assertIn("董事长战略指令", body)
        self.assertIn("角色活动时间线", body)
        self.assertIn("Customer &amp; Revenue", body)
        self.assertNotIn("每个 CEO 周期平均执行时长", body)
        self.assertNotIn("Agent 完成率图", body)

        company = DashboardApp(self.config).render_path("/company").body
        self.assertIn("标准化自助订阅", company)
        self.assertIn("个人、小公司和企业", company)
        self.assertNotIn("CEO 30 分钟节奏", company)

        operations = DashboardApp(self.config).render_path("/operations").body
        self.assertIn("自助订阅漏斗", operations)
        self.assertIn("套餐与单位经济", operations)
        self.assertNotIn("上线前占位", operations)

    def test_dashboard_pages_are_separate_and_chinese_labeled(self) -> None:
        app = DashboardApp(self.config)

        management = app.render_path("/management")
        project = app.render_path("/project")
        operations = app.render_path("/operations")
        company = app.render_path("/company")

        self.assertIn("公司日常管理", management.body)
        self.assertIn('href="/company"', management.body)
        self.assertIn("任务状态", management.body)
        self.assertIn("项目状态", project.body)
        self.assertIn("Git 版本", project.body)
        self.assertIn("产品运营", operations.body)
        self.assertIn("内部受控 Beta", operations.body)
        self.assertEqual(company.status, 200)
        self.assertIn("公司介绍", company.body)
        self.assertIn("使命", company.body)
        self.assertIn("真实商业运营原则", company.body)
        self.assertIn("PixWeave", company.body)
        self.assertIn("组织图", company.body)
        self.assertNotIn("CEO 30 分钟节奏", company.body)
        self.assertIn("事件驱动 7×24", company.body)
        self.assertIn("Chairman 保留决策", company.body)
        self.assertIn("Codex 异步资源", company.body)
        self.assertIn("证据 / 版本 / 归档治理", company.body)
        self.assertNotEqual(management.body, project.body)
        self.assertNotEqual(project.body, operations.body)
        self.assertNotEqual(company.body, management.body)

    def test_company_page_derives_roles_and_raci_from_live_sqlite(self) -> None:
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO roles(name, kind, mandate) VALUES (?, ?, ?)",
                ("Live Test Role", "agent", "Live-only mandate from SQLite."),
            )
            conn.execute(
                "INSERT INTO raci(domain, responsible, accountable, consulted, informed) VALUES (?, ?, ?, ?, ?)",
                ("live-domain", "Live Test Role", "CEO", "CTO", "Chairman"),
            )
        app = DashboardApp(self.config)

        company = app.render_path("/company")

        self.assertIn("Live Test Role", company.body)
        self.assertIn("Live-only mandate from SQLite.", company.body)
        self.assertIn("live-domain", company.body)
        self.assertIn("CEO", company.body)
        self.assertIn("来源：SQLite roles", company.body)
        self.assertIn("来源：SQLite raci", company.body)
        self.assertIn("来源: docs/strategy.md", company.body)
        self.assertIn("来源: docs/org.md", company.body)
        self.assertIn("来源: docs/cadence.md", company.body)
        self.assertIn("来源: docs/versioning-and-records.md", company.body)

    def test_json_endpoints_and_health(self) -> None:
        app = DashboardApp(self.config)

        health = app.render_path("/healthz")
        api = app.render_path("/api/status")

        self.assertEqual(health.content_type, "application/json; charset=utf-8")
        self.assertEqual(json.loads(health.body)["ok"], True)
        payload = json.loads(api.body)
        self.assertEqual(payload["product"], "Test PixWeave")
        self.assertIn("management", payload)
        self.assertIn("project", payload)
        self.assertIn("operations", payload)
        self.assertIn("company", payload)
        self.assertEqual(payload["company"]["roles"][0]["source"], "SQLite roles")
        self.assertEqual(payload["company"]["raci"][0]["source"], "SQLite raci")
        self.assertIn("docs/strategy.md", payload["company"]["static_sources"])

    def test_missing_database_is_reported_without_creating_it(self) -> None:
        missing_config = load_config()
        missing_config.db_path.unlink()

        snapshot = build_snapshot(missing_config)

        self.assertFalse(missing_config.db_path.exists())
        self.assertEqual(snapshot["database"]["available"], False)
        self.assertEqual(snapshot["management"]["tasks"], [])

    def test_dashboard_aggregates_all_agents_and_token_usage_without_fabricating_zeroes(self) -> None:
        with self.store.connect() as conn:
            conn.execute(
                """INSERT INTO token_usage(
                       ts, agent, task_id, execution_id, session, model, provider,
                       input_tokens, output_tokens, cache_tokens, reasoning_tokens,
                       total_tokens, cost, currency, source, created_at
                   ) VALUES (?, 'Product Engineer', 1, NULL, 's-1', 'gpt-test', 'openai',
                   100, 25, 10, 5, 140, 0.12, 'USD', 'observed-test', ?)""",
                ("2026-07-11T00:00:00+00:00", "2026-07-11T00:00:00+00:00"),
            )
        snapshot = build_snapshot(self.config)

        workload = snapshot["management"]["agent_workload"]
        self.assertIn("CEO", workload)
        self.assertEqual(workload["CEO"]["status_label"], "未采集")
        self.assertEqual(workload["CTO"]["task_outcomes"]["in_progress"], 1)
        self.assertEqual(workload["Product Engineer"]["token_usage"]["total_tokens"], 140)
        self.assertEqual(workload["CTO"]["display_label"], "首席技术官（CTO）")

        management = DashboardApp(self.config).render_path("/management").body
        self.assertIn("<svg", management)
        self.assertIn("当前任务状态", management)
        self.assertIn("Agent 工作负载", management)
        self.assertIn("Token 使用量", management)
        self.assertIn("未采集", management)
        self.assertIn("首席执行官（CEO）", management)
        self.assertIn('aria-label="Token 使用量图"', management)
        self.assertIn("完成率", management)
        self.assertIn("失败", management)

    def test_dashboard_visible_copy_is_simplified_chinese_with_canonical_api_keys(self) -> None:
        api = json.loads(DashboardApp(self.config).render_path("/api/status").body)
        self.assertIn("task_counts_by_status", api["management"])
        self.assertIn("display_labels", api["management"])
        self.assertEqual(api["management"]["display_labels"]["task_counts_by_status"], "任务状态统计")

        for path in ["/management", "/project", "/operations", "/company"]:
            body = DashboardApp(self.config).render_path(path).body
            self.assertIn('lang="zh-CN"', body)
            self.assertNotIn(">JSON API<", body)
            self.assertNotIn("Live SQLite", body)
            self.assertNotIn("pre_launch", body)

    def test_dashboard_shows_zoomable_role_execution_timeline_and_cycle_average_duration(self) -> None:
        with self.store.connect() as conn:
            conn.execute("DELETE FROM task_executions")
            conn.execute(
                """INSERT INTO task_executions(
                       task_id, executor_id, backend, claimed_at, heartbeat_at,
                       lease_expires_at, attempt_count, max_attempts, evidence_paths,
                       log_paths, recovery_status, created_at, updated_at
                   ) VALUES (1, 'timeline-exec', 'codex',
                       '2026-07-11T01:02:10+00:00', '2026-07-11T01:02:30+00:00',
                       '2026-07-11T01:12:10+00:00', 0, 3, '[]', '[]',
                       'completed', '2026-07-11T01:02:10+00:00',
                       '2026-07-11T01:03:10+00:00')"""
            )

        snapshot = build_snapshot(self.config)
        timeline = snapshot["management"]["role_execution_timeline"]
        self.assertEqual(timeline["intervals"][0]["role"], "CTO")
        self.assertEqual(timeline["intervals"][0]["duration_seconds"], 60.0)
        averages = snapshot["management"]["average_execution_seconds_per_ceo_cycle"]
        self.assertEqual(averages["CTO"], 60.0)
        self.assertIsNone(averages["CEO"])

        management = DashboardApp(self.config).render_path("/management").body
        self.assertIn("角色任务执行时间轴", management)
        self.assertNotIn("每个 CEO 周期平均执行时长", management)
        self.assertIn("角色活动时间线", management)
        self.assertIn('type="range"', management)
        self.assertIn("timeline-viewport", management)
        self.assertIn("首席技术官（CTO）", management)


if __name__ == "__main__":
    unittest.main()
