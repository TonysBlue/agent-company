"""Argparse CLI for the company OS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backend import LocalBackend
from .beta_launch import evaluate_beta_launch_package_file
from .beta_session import capture_session_file, summarize_session_economics_file
from .config import load_config
from .feedback import capture_feedback_file, triage_feedback_file
from .ops import CompanyOS
from .unit_economics import calculate_scenarios, load_scenarios


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-company", description="AI-native company operating system MVP")
    parser.add_argument("--config", default=None, help="Path to INI config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize state")
    sub.add_parser("org-migrate", help="Apply the audited lean organization migration")
    sub.add_parser("status", help="Show company status")
    sub.add_parser("run-cycle", help="Run one operating cycle")
    sub.add_parser("worker-run", help="Run the persistent event worker until stopped")
    sub.add_parser("worker-step", help="Process at most one durable event")
    sub.add_parser("worker-status", help="Show event worker health and queue state")
    wake = sub.add_parser("worker-wake", help="Persist an explicit worker wake event")
    wake.add_argument("--reason", required=True)
    sub.add_parser("ceo-status", help="Show the persistent Hermes CEO control-plane status")
    directive = sub.add_parser("chairman-directive-ingest", help="Ingest a structured Chairman directive")
    directive.add_argument("--source-platform", required=True)
    directive.add_argument("--source-session-id", required=True)
    directive.add_argument("--source-message-id", required=True)
    directive.add_argument("--message", required=True, help="Transient raw message; only its SHA-256 is retained")
    directive.add_argument("--directive-type", required=True)
    directive.add_argument("--objective", required=True)
    directive.add_argument("--constraint", action="append", default=[])
    directive.add_argument("--priority", type=int, default=100)
    ceo_step = sub.add_parser("ceo-step", help="Process at most one event through the CEO-aware engine")
    ceo_step.add_argument("--fixture", type=Path, default=None)
    ceo_step.add_argument("--disable-external-delivery", action="store_true")
    runner = sub.add_parser("runner-run", help="Run one resident role execution worker")
    runner.add_argument("--executor-id", required=True)
    runner.add_argument("--owner", required=True)
    runner.add_argument("--capability", action="append", required=True)
    runner.add_argument("--poll-seconds", type=float, default=5.0)
    sub.add_parser("executor-list", help="List registered execution workers")
    register_executor = sub.add_parser("executor-register", help="Register or refresh an execution worker")
    register_executor.add_argument("--executor-id", required=True)
    register_executor.add_argument("--owner", required=True)
    register_executor.add_argument("--backend", required=True)
    register_executor.add_argument("--capability", action="append", required=True)
    register_executor.add_argument("--capacity", type=int, default=1)
    register_executor.add_argument("--process-id", type=int, default=None)
    register_executor.add_argument("--process-started-at", default=None)
    register_executor.add_argument("--session-ref", default=None)
    task_list = sub.add_parser("task-list", help="List active tasks")
    create = sub.add_parser("task-create", help="Create one reviewed backlog task")
    create.add_argument("--actor", required=True)
    create.add_argument("--owner", required=True)
    create.add_argument("--title", required=True)
    create.add_argument("--domain", required=True)
    create.add_argument("--priority", type=int, required=True)
    create.add_argument("--acceptance-criteria", required=True)
    claim = sub.add_parser("task-claim", help="Claim one open task for bounded execution")
    claim.add_argument("task_id", type=int)
    claim.add_argument("--actor", required=True)
    claim.add_argument("--executor-id", default=None)
    claim.add_argument("--backend", default=None)
    claim.add_argument("--process-id", type=int, default=None)
    claim.add_argument("--process-started-at", default=None)
    claim.add_argument("--session-ref", default=None)
    claim.add_argument("--lease-seconds", type=int, default=600)
    claim.add_argument("--max-attempts", type=int, default=3)
    claim.add_argument("--evidence-path", type=Path, action="append", default=[])
    claim.add_argument("--log-path", type=Path, action="append", default=[])
    heartbeat = sub.add_parser("task-heartbeat", help="Renew a task execution lease")
    heartbeat.add_argument("task_id", type=int)
    heartbeat.add_argument("--executor-id", required=True)
    heartbeat.add_argument("--lease-seconds", type=int, default=600)
    checkpoint = sub.add_parser("task-checkpoint", help="Record task execution checkpoint and next action")
    checkpoint.add_argument("task_id", type=int)
    checkpoint.add_argument("--executor-id", required=True)
    checkpoint.add_argument("--checkpoint", required=True)
    checkpoint.add_argument("--next-action", required=True)
    inspect = sub.add_parser("task-inspect", help="Inspect task execution state")
    inspect.add_argument("task_id", type=int)
    recover = sub.add_parser("task-recover", help="Recover or requeue a task execution")
    recover.add_argument("task_id", type=int)
    recover.add_argument("--actor", required=True)
    recover.add_argument("--reason", required=True)
    fail = sub.add_parser("task-fail", help="Record task execution failure")
    fail.add_argument("task_id", type=int)
    fail.add_argument("--executor-id", required=True)
    fail.add_argument("--error", required=True)
    fail.add_argument("--permanent", action="store_true")
    token_record = sub.add_parser("token-record", help="Record observed token usage")
    token_record.add_argument("--agent", required=True)
    token_record.add_argument("--task-id", type=int, default=None)
    token_record.add_argument("--execution-id", type=int, default=None)
    token_record.add_argument("--session", default=None)
    token_record.add_argument("--model", default=None)
    token_record.add_argument("--provider", default=None)
    token_record.add_argument("--input-tokens", type=int, required=True)
    token_record.add_argument("--output-tokens", type=int, required=True)
    token_record.add_argument("--cache-tokens", type=int, required=True)
    token_record.add_argument("--reasoning-tokens", type=int, required=True)
    token_record.add_argument("--total-tokens", type=int, required=True)
    token_record.add_argument("--cost", type=float, default=None)
    token_record.add_argument("--currency", default=None)
    token_record.add_argument("--source", required=True)
    token_record.add_argument("--timestamp", default=None)
    token_list = sub.add_parser("token-list", help="List observed token usage records")
    token_list.add_argument("--agent", default=None)
    token_list.add_argument("--limit", type=int, default=50)
    sub.add_parser("token-summary", help="Summarize token usage by registered agent")
    complete = sub.add_parser("task-complete", help="Complete a claimed task with reviewable evidence")
    complete.add_argument("task_id", type=int)
    complete.add_argument("--actor", required=True)
    complete.add_argument("--summary", required=True)
    complete.add_argument("--evidence", type=Path, action="append", required=True)
    cancel = sub.add_parser("task-cancel", help="Cancel obsolete or duplicate work with an audited reason")
    cancel.add_argument("task_id", type=int)
    cancel.add_argument("--actor", required=True)
    cancel.add_argument("--reason", required=True)
    sub.add_parser("chairman-inbox", help="List pending Chairman decisions")
    decide = sub.add_parser("decide", help="Record a Chairman decision")
    decide.add_argument("approval_id", type=int)
    decide.add_argument("decision", choices=["approve", "deny"])
    decide.add_argument("--rationale", default="Chairman decision recorded.")
    sub.add_parser("report", help="Print operating report")
    dashboard = sub.add_parser("dashboard", help="Run read-only operations dashboard")
    dashboard.add_argument("--host", default="0.0.0.0")
    dashboard.add_argument("--port", type=int, default=18080)
    beta_product = sub.add_parser("beta-product", help="Run local-only internal beta product interface")
    beta_product.add_argument("--host", default="127.0.0.1")
    beta_product.add_argument("--port", type=int, default=18112)
    sub.add_parser("demo", help="Run a demo cycle")
    sub.add_parser("validate", help="Validate state and governance")
    validate_brand = sub.add_parser("validate-brand-kit", help="Validate a brand-kit JSON file")
    validate_brand.add_argument("path", type=Path)
    campaign = sub.add_parser("campaign-manifest", help="Build a deterministic campaign manifest")
    campaign.add_argument("input", type=Path)
    campaign.add_argument("--output", type=Path, default=None)
    render = sub.add_parser("campaign-render", help="Render provenance-gated campaign drafts as SVG")
    render.add_argument("input", type=Path)
    render.add_argument("--output-dir", type=Path, default=None)
    render_verify = sub.add_parser("campaign-render-verify", help="Verify a campaign-render/v2 bundle")
    render_verify.add_argument("bundle_dir", type=Path)
    review = sub.add_parser("campaign-review", help="Record complete internal approve/reject decisions for a verified campaign render")
    review.add_argument("bundle_dir", type=Path)
    review.add_argument("decisions", type=Path)
    review.add_argument("--output", type=Path, default=None)
    prompt_pack = sub.add_parser("prompt-pack", help="Expand a deterministic versioned prompt pack")
    prompt_pack.add_argument("input", type=Path)
    prompt_pack.add_argument("--output", type=Path, default=None)
    economics = sub.add_parser("unit-economics", help="Calculate internal cost sensitivity scenarios")
    economics.add_argument("input", type=Path)
    product_shot = sub.add_parser("product-shot-workflow", help="Build a deterministic product-shot workflow manifest")
    product_shot.add_argument("input", type=Path)
    product_shot.add_argument("--output", type=Path, default=None)
    visual_qa = sub.add_parser("visual-qa-scorecard", help="Score explicit visual QA observations")
    visual_qa.add_argument("input", type=Path)
    visual_qa.add_argument("--output", type=Path, default=None)
    feedback = sub.add_parser("feedback-capture", help="Validate and retain a privacy-bounded feedback submission")
    feedback.add_argument("input", type=Path)
    feedback.add_argument("--output", type=Path, required=True)
    triage = sub.add_parser("feedback-triage", help="Bind an auditable triage decision to a feedback submission")
    triage.add_argument("submission", type=Path)
    triage.add_argument("decision", type=Path)
    triage.add_argument("--output", type=Path, required=True)
    beta = sub.add_parser("beta-launch-readiness", help="Evaluate an internal beta launch readiness package")
    beta.add_argument("input", type=Path)
    beta.add_argument("--output", type=Path, default=None)
    beta_session = sub.add_parser("beta-session-capture", help="Validate and retain a controlled-beta session record")
    beta_session.add_argument("input", type=Path)
    beta_session.add_argument("--output", type=Path, required=True)
    beta_economics = sub.add_parser("beta-session-economics", help="Summarize local synthetic beta-session evidence")
    beta_economics.add_argument("input", type=Path)
    beta_economics.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    osys = CompanyOS(load_config(args.config))
    try:
        if args.command == "init":
            osys.init()
            print("initialized")
        elif args.command == "org-migrate":
            print(json.dumps(osys.store.migrate_organization(), indent=2, sort_keys=True))
        elif args.command == "status":
            print(json.dumps(osys.status(), indent=2, sort_keys=True))
        elif args.command == "run-cycle":
            print(json.dumps(osys.run_cycle(), indent=2, sort_keys=True))
        elif args.command == "worker-run":
            from .event_engine import EventEngine

            EventEngine(osys.config).run()
        elif args.command == "worker-step":
            from .event_engine import EventEngine

            print(json.dumps(EventEngine(osys.config).step(), indent=2, sort_keys=True))
        elif args.command == "worker-status":
            from .event_engine import EventEngine

            print(json.dumps(EventEngine(osys.config).status(), indent=2, sort_keys=True))
        elif args.command == "worker-wake":
            from .event_engine import EventEngine

            print(json.dumps(EventEngine(osys.config).wake(args.reason), indent=2, sort_keys=True))
        elif args.command == "ceo-status":
            from .ceo_runtime import CEORuntime

            print(json.dumps(CEORuntime(osys.config).status(), indent=2, sort_keys=True))
        elif args.command == "chairman-directive-ingest":
            from .ceo_runtime import CEORuntime

            print(json.dumps(CEORuntime(osys.config).ingest_directive(
                source_platform=args.source_platform,
                source_session_id=args.source_session_id,
                source_message_id=args.source_message_id,
                message=args.message,
                directive_type=args.directive_type,
                objective=args.objective,
                constraints=args.constraint,
                priority=args.priority,
            ), indent=2, sort_keys=True))
        elif args.command == "ceo-step":
            from .ceo_runtime import CEORuntime, DisabledSender, FixtureReasoner
            from .event_engine import EventEngine

            reasoner = FixtureReasoner(args.fixture) if args.fixture else None
            sender = DisabledSender() if args.disable_external_delivery else None
            runtime = CEORuntime(
                osys.config,
                reasoner=reasoner,
                sender=sender,
                external_delivery_enabled=False if args.disable_external_delivery else None,
            )
            if args.fixture:
                runtime.init()
                with runtime.store.connect() as conn:
                    runtime.store.enqueue_event(
                        conn,
                        "ceo.fixture",
                        "fixture",
                        args.fixture.name,
                        {"fixture": str(args.fixture.resolve())},
                        priority=101,
                    )
            print(json.dumps(EventEngine(osys.config, ceo_runtime=runtime).step(), indent=2, sort_keys=True))
        elif args.command == "runner-run":
            from .runner import ExecutionRunner

            ExecutionRunner(
                osys.config, args.executor_id, args.owner, args.capability,
                poll_seconds=args.poll_seconds,
            ).run_forever()
        elif args.command == "executor-list":
            print(json.dumps(osys.executor_list(), indent=2, sort_keys=True))
        elif args.command == "executor-register":
            print(json.dumps(osys.register_executor(
                args.executor_id, args.owner, args.backend, args.capability,
                capacity=args.capacity, process_id=args.process_id,
                process_started_at=args.process_started_at, session_ref=args.session_ref,
            ), indent=2, sort_keys=True))
        elif args.command == "task-list":
            print(json.dumps(osys.task_list(), indent=2, sort_keys=True))
        elif args.command == "task-create":
            print(json.dumps(osys.create_task(
                args.actor,
                args.owner,
                args.title,
                args.domain,
                args.priority,
                args.acceptance_criteria,
            ), indent=2, sort_keys=True))
        elif args.command == "task-claim":
            print(json.dumps(osys.claim_task(
                args.task_id,
                args.actor,
                executor_id=args.executor_id,
                backend=args.backend,
                process_id=args.process_id,
                process_started_at=args.process_started_at,
                session_ref=args.session_ref,
                lease_seconds=args.lease_seconds,
                max_attempts=args.max_attempts,
                evidence_paths=args.evidence_path,
                log_paths=args.log_path,
            ), indent=2, sort_keys=True))
        elif args.command == "task-heartbeat":
            print(json.dumps(osys.heartbeat_task(args.task_id, args.executor_id, args.lease_seconds), indent=2, sort_keys=True))
        elif args.command == "task-checkpoint":
            print(json.dumps(osys.checkpoint_task(args.task_id, args.executor_id, args.checkpoint, args.next_action), indent=2, sort_keys=True))
        elif args.command == "task-inspect":
            print(json.dumps(osys.inspect_execution(args.task_id), indent=2, sort_keys=True))
        elif args.command == "task-recover":
            print(json.dumps(osys.recover_task(args.task_id, args.actor, args.reason), indent=2, sort_keys=True))
        elif args.command == "task-fail":
            print(json.dumps(osys.fail_task(args.task_id, args.executor_id, args.error, recoverable=not args.permanent), indent=2, sort_keys=True))
        elif args.command == "token-record":
            print(json.dumps(osys.record_token_usage(
                agent=args.agent,
                task_id=args.task_id,
                execution_id=args.execution_id,
                session=args.session,
                model=args.model,
                provider=args.provider,
                input_tokens=args.input_tokens,
                output_tokens=args.output_tokens,
                cache_tokens=args.cache_tokens,
                reasoning_tokens=args.reasoning_tokens,
                total_tokens=args.total_tokens,
                cost=args.cost,
                currency=args.currency,
                source=args.source,
                timestamp=args.timestamp,
            ), indent=2, sort_keys=True))
        elif args.command == "token-list":
            print(json.dumps(osys.list_token_usage(agent=args.agent, limit=args.limit), indent=2, sort_keys=True))
        elif args.command == "token-summary":
            print(json.dumps(osys.token_usage_summary(), indent=2, sort_keys=True, ensure_ascii=False))
        elif args.command == "task-complete":
            print(json.dumps(osys.complete_task(args.task_id, args.actor, args.summary, args.evidence), indent=2, sort_keys=True))
        elif args.command == "task-cancel":
            print(json.dumps(osys.cancel_task(args.task_id, args.actor, args.reason), indent=2, sort_keys=True))
        elif args.command == "chairman-inbox":
            print(json.dumps(osys.chairman_inbox(), indent=2, sort_keys=True))
        elif args.command == "decide":
            print(json.dumps(osys.decide(args.approval_id, args.decision, args.rationale), indent=2, sort_keys=True))
        elif args.command == "report":
            print(osys.report(), end="")
        elif args.command == "dashboard":
            from .dashboard import serve

            serve(osys.config, args.host, args.port)
        elif args.command == "beta-product":
            from .beta_product import serve

            serve(osys.config, args.host, args.port)
        elif args.command == "demo":
            print(json.dumps(osys.demo(), indent=2, sort_keys=True))
        elif args.command == "validate":
            errors = osys.validate()
            if errors:
                print(json.dumps({"ok": False, "errors": errors}, indent=2, sort_keys=True))
                return 1
            print(json.dumps({"ok": True, "errors": []}, indent=2, sort_keys=True))
        elif args.command == "validate-brand-kit":
            result = LocalBackend(osys.config).validate_brand_kit_file(args.path)
            print(json.dumps(result, indent=2, sort_keys=True))
            if not result["ok"]:
                return 1
        elif args.command == "campaign-manifest":
            result = LocalBackend(osys.config).generate_campaign_manifest_file(args.input, args.output)
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "campaign-render":
            result = LocalBackend(osys.config).render_campaign_file(args.input, args.output_dir)
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "campaign-render-verify":
            result = LocalBackend(osys.config).verify_campaign_render_bundle_dir(args.bundle_dir)
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "campaign-review":
            result = LocalBackend(osys.config).record_campaign_review_file(args.bundle_dir, args.decisions, args.output)
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "prompt-pack":
            result = LocalBackend(osys.config).generate_prompt_manifest_file(args.input, args.output)
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "unit-economics":
            result = calculate_scenarios(load_scenarios(args.input))
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "product-shot-workflow":
            result = LocalBackend(osys.config).generate_product_shot_workflow_file(args.input, args.output)
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "visual-qa-scorecard":
            result = LocalBackend(osys.config).generate_visual_qa_scorecard_file(args.input, args.output)
            print(json.dumps(result, indent=2, sort_keys=True))
        elif args.command == "feedback-capture":
            print(json.dumps(capture_feedback_file(args.input, args.output), indent=2, sort_keys=True))
        elif args.command == "feedback-triage":
            print(json.dumps(triage_feedback_file(args.submission, args.decision, args.output), indent=2, sort_keys=True))
        elif args.command == "beta-launch-readiness":
            print(json.dumps(evaluate_beta_launch_package_file(args.input, args.output), indent=2, sort_keys=True))
        elif args.command == "beta-session-capture":
            print(json.dumps(capture_session_file(args.input, args.output), indent=2, sort_keys=True))
        elif args.command == "beta-session-economics":
            print(json.dumps(summarize_session_economics_file(args.input, args.output), indent=2, sort_keys=True))
        else:
            raise AssertionError(args.command)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
