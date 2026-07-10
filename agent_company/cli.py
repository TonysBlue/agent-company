"""Argparse CLI for the company OS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backend import LocalBackend
from .config import load_config
from .ops import CompanyOS
from .unit_economics import calculate_scenarios, load_scenarios


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-company", description="AI-native company operating system MVP")
    parser.add_argument("--config", default=None, help="Path to INI config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize state")
    sub.add_parser("status", help="Show company status")
    sub.add_parser("run-cycle", help="Run one operating cycle")
    sub.add_parser("task-list", help="List active tasks")
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    osys = CompanyOS(load_config(args.config))
    try:
        if args.command == "init":
            osys.init()
            print("initialized")
        elif args.command == "status":
            print(json.dumps(osys.status(), indent=2, sort_keys=True))
        elif args.command == "run-cycle":
            print(json.dumps(osys.run_cycle(), indent=2, sort_keys=True))
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
            print(json.dumps(osys.claim_task(args.task_id, args.actor), indent=2, sort_keys=True))
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
        else:
            raise AssertionError(args.command)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
