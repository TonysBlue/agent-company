#!/usr/bin/env python3
"""Permanent fail-closed tombstone for the legacy Phase D treatment runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_company.phase_d_treatments import BLOCKED_REASON, PhaseDTreatmentError


def main() -> int:
    raise PhaseDTreatmentError(f"tombstone: {BLOCKED_REASON}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PhaseDTreatmentError as exc:
        print(json.dumps({"error": str(exc), "stage": "Phase D treatment"}, sort_keys=True))
        raise SystemExit(2)
