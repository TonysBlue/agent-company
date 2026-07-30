#!/usr/bin/env python3
"""Run or reproduce V4 non-treatment Phase D protocol evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_company.phase_d_redesign import (
    PhaseDRedesignError,
    run_redesign_dry_run,
    verify_redesign_evidence,
)


FREEZE = ROOT / "docs" / "assurance" / "phase-d" / "redesign" / "corrected-freeze-v4.json"
EVIDENCE = ROOT / "evidence" / "phase-d" / "redesign-v4" / "protocol-handoff"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--development-overlay",
        action="store_true",
        help="verify the reviewed baseline Git object while local TDD changes remain uncommitted",
    )
    args = parser.parse_args(argv)
    if args.verify:
        result = verify_redesign_evidence(
            ROOT,
            args.output,
            freeze_path=FREEZE,
            require_immutable_head=not args.development_overlay,
        )
    else:
        result = run_redesign_dry_run(
            ROOT,
            args.output,
            freeze_path=FREEZE,
            allow_development_overlay=args.development_overlay,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PhaseDRedesignError as exc:
        print(json.dumps({"error": str(exc), "stage": "Phase D V4 protocol"}, sort_keys=True))
        raise SystemExit(2)
