#!/usr/bin/env python3
"""Compatibility entrypoint for the V4 blocked protocol verifier."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_company.phase_d_redesign import PhaseDRedesignError


def main() -> int:
    raise PhaseDRedesignError(
        "V3 dry run is superseded because it executes D1/D2 treatment workflows; "
        "use run_phase_d_redesign_v4_protocol.py"
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PhaseDRedesignError as exc:
        print(json.dumps({"error": str(exc), "stage": "Phase D V3 supersession"}, sort_keys=True))
        raise SystemExit(2)
