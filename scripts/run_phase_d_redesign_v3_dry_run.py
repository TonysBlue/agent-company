#!/usr/bin/env python3
"""Produce blocked, synthetic-only Phase D redesign V3 dry-run evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_company.phase_d_redesign import PhaseDRedesignError, run_redesign_dry_run


def main() -> int:
    freeze = ROOT / "docs" / "assurance" / "phase-d" / "redesign" / "corrected-freeze-v3.json"
    output = ROOT / "evidence" / "phase-d" / "redesign-v3"
    result = run_redesign_dry_run(ROOT, output, freeze_path=freeze)
    print(json.dumps({
        "status": result["status"],
        "corrected_treatments_executed": result["corrected_treatments_executed"],
        "d1_scenarios": result["d1"]["scenario_count"],
        "d2_canaries": result["d2"]["canary_count"],
        "d2_thresholds_passed_on_synthetic_dry_run": result["d2"]["thresholds_passed"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PhaseDRedesignError as exc:
        print(json.dumps({"error": str(exc), "stage": "Phase D redesign V3 dry run"}, sort_keys=True))
        raise SystemExit(2)
