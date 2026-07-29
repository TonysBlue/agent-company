"""Frozen, local-only Phase D treatment helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
import zlib
from pathlib import Path


class PhaseDTreatmentError(ValueError):
    """Raised when a Phase D treatment contract or result violates its freeze."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhaseDTreatmentError(f"cannot load JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PhaseDTreatmentError(f"JSON input must be an object: {path}")
    return value


def verify_start_contracts(root: Path, manifest_path: Path) -> dict[str, dict[str, object]]:
    manifest = load_json(manifest_path)
    if manifest.get("schema_version") != "phase-d-start-freeze/v1":
        raise PhaseDTreatmentError("start freeze schema_version is invalid")
    entries = manifest.get("contracts")
    if not isinstance(entries, list) or len(entries) != 2:
        raise PhaseDTreatmentError("start freeze must bind exactly D1 and D2")
    contracts: dict[str, dict[str, object]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PhaseDTreatmentError("start contract entries must be objects")
        pilot = entry.get("pilot")
        relative = entry.get("path")
        expected = entry.get("sha256")
        if pilot not in {"d1", "d2"} or not isinstance(relative, str) or not isinstance(expected, str):
            raise PhaseDTreatmentError("start contract entry is invalid")
        path = root / relative
        if sha256_file(path) != expected:
            raise PhaseDTreatmentError(f"start contract hash mismatch: {pilot}")
        contracts[str(pilot)] = load_json(path)
    if set(contracts) != {"d1", "d2"}:
        raise PhaseDTreatmentError("start freeze must bind one D1 and one D2 contract")
    return contracts


def blind_assignment(scenario_id: str) -> dict[str, str]:
    digest = hashlib.sha256(f"phase-d-d1-v1:{scenario_id}".encode("ascii")).digest()
    if digest[0] % 2:
        return {"A": "candidate", "B": "comparator"}
    return {"A": "comparator", "B": "candidate"}


def evaluate_d2_results(results: list[dict[str, object]]) -> dict[str, object]:
    material = [
        result for result in results
        if result.get("seeded_fault") is True and str(result.get("severity", "")).lower() in {"critical", "high"}
    ]
    detected = [result for result in material if result.get("treatment_signal") == "detected"]
    false_passes = len(material) - len(detected)
    controls = [result for result in results if result.get("valid_control") is True]
    blocked = [result for result in controls if result.get("treatment_signal") == "blocked"]
    detection_rate = len(detected) / len(material) if material else 0.0
    false_block_rate = len(blocked) / len(controls) if controls else 0.0
    return {
        "critical_high_detection": {
            "detected": len(detected),
            "total": len(material),
            "rate": detection_rate,
        },
        "false_passes": false_passes,
        "false_blocks": {
            "blocked": len(blocked),
            "controls": len(controls),
            "rate": false_block_rate,
        },
        "thresholds_passed": (
            bool(material)
            and detection_rate == 1.0
            and false_passes == 0
            and bool(controls)
            and false_block_rate <= 0.10
        ),
    }


def synthetic_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return len(data).to_bytes(4, "big") + kind + data + crc.to_bytes(4, "big")

    scanline = b"\x00" + bytes(rgb) * width
    raw = scanline * height
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def d1_payload(scenario: dict[str, object], side: str) -> dict[str, object]:
    brand = scenario["brand"]
    message = scenario["message"]
    assert isinstance(brand, dict) and isinstance(message, dict)
    seed = int(scenario["seed"])
    color = ((seed * 17) % 192 + 32, (seed * 29) % 192 + 32, (seed * 43) % 192 + 32)
    width, height = (180, 320) if scenario.get("aspect_ratio") == "9:16" else (256, 256)
    source_id = f"phase-d-{scenario['id']}-source"
    payload: dict[str, object] = {
        "schema_version": "source-image-edit/v1",
        "brand_kit": {
            "schema_version": "brand-kit/v1",
            "brand_name": f"Phase D Synthetic {scenario['id']}",
            "brand_version": "1.0.0",
            "colors": {
                "primary": brand["palette"][0],
                "secondary": [brand["palette"][1]],
                "neutrals": ["#111827", "#F9FAFB"],
            },
            "typography": {"heading": "Inter", "body": "Noto Sans"},
            "logo": {
                "clearspace_px": int(brand["logo_clearance_percent"]),
                "allowed_placements": ["top-left"],
            },
            "forbidden_elements": list(scenario["constraints"]),
        },
        "source_image": {
            "source_id": source_id,
            "file_name": f"{scenario['id']}.png",
            "media_type": "image/png",
            "data_base64": base64.b64encode(synthetic_png(width, height, color)).decode("ascii"),
            "provenance": {
                "schema_version": "provenance/v1",
                "source_id": source_id,
                "parent_lineage": [],
                "source_category": "phase_d_synthetic_fixture",
                "origin": "agent-company Phase D D1 runner",
                "rights_basis": "company-created synthetic fixture",
                "rights_evidence_ref": "docs/assurance/pixweave/synthetic-scenarios-v1.json",
                "likeness_status": "no_real_person",
                "trademark_review_status": "no_third_party_mark",
                "data_classification": "synthetic",
                "retention_class": "phase_d_evidence",
                "policy_flags": ["synthetic_fixture", "internal_only"],
                "reviewer_ref": "phase-d-d1-contract",
                "review_decision": "approved_internal",
            },
        },
    }
    primary = str(message["primary"])
    secondary = str(message["secondary"])
    if side in {"candidate", "comparator"}:
        text = f"{primary} - {secondary}" if side == "candidate" else primary
        payload["operations"] = [
            {"id": "brand-message", "type": "branded_overlay", "text": text, "placement": "bottom"},
        ]
    else:
        raise PhaseDTreatmentError("D1 side must be candidate or comparator")
    return payload


def run_unittest_case(repository: Path, target: str, timeout_seconds: int) -> tuple[int | str, str]:
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", target, "-v"],
            cwd=repository,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
        )
        return completed.returncode, completed.stdout
    except subprocess.TimeoutExpired as exc:
        captured = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return "timeout", captured + f"\nPhase D treatment timed out after {timeout_seconds} seconds\n"
