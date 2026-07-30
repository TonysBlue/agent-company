#!/usr/bin/env python3
"""Compatibility entrypoint for V4 blocked, non-treatment protocol checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_company.phase_d_redesign import PhaseDRedesignError, run_redesign_dry_run


def main() -> int:
    raise PhaseDRedesignError(
        "legacy dry-run output is superseded; use run_phase_d_redesign_v4_protocol.py "
        "with an explicit empty output directory"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PhaseDRedesignError as exc:
        print(json.dumps({"error": str(exc), "stage": "Phase D redesign dry run"}, sort_keys=True))
        raise SystemExit(2)
