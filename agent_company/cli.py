"""Argparse CLI for the company OS."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .backend import LocalBackend
from .config import load_config
from .ops import CompanyOS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-company", description="AI-native company operating system MVP")
    parser.add_argument("--config", default=None, help="Path to INI config")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Initialize state")
    sub.add_parser("status", help="Show company status")
    sub.add_parser("run-cycle", help="Run one operating cycle")
    sub.add_parser("chairman-inbox", help="List pending Chairman decisions")
    decide = sub.add_parser("decide", help="Record a Chairman decision")
    decide.add_argument("approval_id", type=int)
    decide.add_argument("decision", choices=["approve", "deny"])
    decide.add_argument("--rationale", default="Chairman decision recorded.")
    sub.add_parser("report", help="Print operating report")
    sub.add_parser("demo", help="Run a demo cycle")
    sub.add_parser("validate", help="Validate state and governance")
    validate_brand = sub.add_parser("validate-brand-kit", help="Validate a brand-kit JSON file")
    validate_brand.add_argument("path", type=Path)
    campaign = sub.add_parser("campaign-manifest", help="Build a deterministic campaign manifest")
    campaign.add_argument("input", type=Path)
    campaign.add_argument("--output", type=Path, default=None)
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
        elif args.command == "chairman-inbox":
            print(json.dumps(osys.chairman_inbox(), indent=2, sort_keys=True))
        elif args.command == "decide":
            print(json.dumps(osys.decide(args.approval_id, args.decision, args.rationale), indent=2, sort_keys=True))
        elif args.command == "report":
            print(osys.report(), end="")
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
        else:
            raise AssertionError(args.command)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
