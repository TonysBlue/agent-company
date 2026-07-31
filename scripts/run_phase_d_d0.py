#!/usr/bin/env python3
"""Permanent fail-closed tombstone for the superseded Phase D D0 runner."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_company.phase_d_d0 import D0Error


BLOCKED_REASON = (
    "D0 runner tombstone: D0 is superseded and permanently disabled; execution, "
    "subprocess probes, evidence writes, and authorization output are unavailable"
)


def main() -> int:
    raise D0Error(BLOCKED_REASON)


if __name__ == "__main__":
    raise SystemExit(2)
