"""Deterministic internal unit-economics sensitivity calculations."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any


class UnitEconomicsError(ValueError):
    """Raised when an internal cost scenario is invalid."""


def load_scenarios(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UnitEconomicsError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise UnitEconomicsError(f"{path}: top-level JSON value must be an object")
    return data


def calculate_scenarios(data: dict[str, Any]) -> dict[str, Any]:
    """Compute cost per attempt and accepted asset without setting a price."""
    if data.get("schema_version") != "unit-economics/v1":
        raise UnitEconomicsError("schema_version must be unit-economics/v1")
    currency = data.get("currency")
    if not isinstance(currency, str) or not currency.strip():
        raise UnitEconomicsError("currency must be a non-empty string")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise UnitEconomicsError("scenarios must be a non-empty list")

    names: set[str] = set()
    results: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise UnitEconomicsError(f"scenarios[{index}] must be an object")
        name = scenario.get("name")
        if not isinstance(name, str) or not name.strip():
            raise UnitEconomicsError(f"scenarios[{index}].name must be a non-empty string")
        if name in names:
            raise UnitEconomicsError(f"duplicate scenario name: {name}")
        names.add(name)

        inference = _nonnegative_decimal(scenario, "inference_cost_per_attempt", index)
        storage = _nonnegative_decimal(scenario, "storage_cost_per_attempt", index)
        qa_minutes = _nonnegative_decimal(scenario, "qa_minutes_per_attempt", index)
        qa_hourly = _nonnegative_decimal(scenario, "qa_hourly_cost", index)
        acceptance = _decimal(scenario, "acceptance_rate", index)
        if acceptance <= 0 or acceptance > 1:
            raise UnitEconomicsError(f"scenarios[{index}].acceptance_rate must be > 0 and <= 1")

        qa_cost = qa_minutes / Decimal("60") * qa_hourly
        attempt_cost = inference + storage + qa_cost
        accepted_cost = attempt_cost / acceptance
        results.append(
            {
                "name": name,
                "assumptions": {
                    "inference_cost_per_attempt": _number(inference),
                    "storage_cost_per_attempt": _number(storage),
                    "qa_minutes_per_attempt": _number(qa_minutes),
                    "qa_hourly_cost": _number(qa_hourly),
                    "acceptance_rate": _number(acceptance),
                },
                "qa_cost_per_attempt": _money(qa_cost),
                "cost_per_attempt": _money(attempt_cost),
                "cost_per_accepted_asset": _money(accepted_cost),
            }
        )

    return {
        "schema_version": "unit-economics-result/v1",
        "currency": currency,
        "pricing_authorized": False,
        "formula": "(inference_cost_per_attempt + storage_cost_per_attempt + (qa_minutes_per_attempt / 60 * qa_hourly_cost)) / acceptance_rate",
        "scenarios": results,
    }


def _decimal(scenario: dict[str, Any], key: str, index: int) -> Decimal:
    value = scenario.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise UnitEconomicsError(f"scenarios[{index}].{key} must be numeric")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise UnitEconomicsError(f"scenarios[{index}].{key} must be numeric") from exc
    if not number.is_finite():
        raise UnitEconomicsError(f"scenarios[{index}].{key} must be finite")
    return number


def _nonnegative_decimal(scenario: dict[str, Any], key: str, index: int) -> Decimal:
    number = _decimal(scenario, key, index)
    if number < 0:
        raise UnitEconomicsError(f"scenarios[{index}].{key} must be non-negative")
    return number


def _money(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _number(value: Decimal) -> float:
    return float(value)
