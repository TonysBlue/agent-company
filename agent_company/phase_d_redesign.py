"""Fail-closed tooling for the corrected Phase D D1/D2 design."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import shutil
import sqlite3
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


class PhaseDRedesignError(ValueError):
    """Raised when corrected Phase D inputs or evidence violate their freeze."""


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PhaseDRedesignError(f"cannot load JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PhaseDRedesignError(f"JSON input must be an object: {path}")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _validate_independent_approval(
    root: Path,
    freeze_path: Path,
    approval_path: Path,
    documents: dict[str, dict[str, Any]],
    author_principals: set[str],
) -> dict[str, Any]:
    approval = load_json(approval_path)
    if approval.get("schema_version") != "phase-d-redesign-independent-approval/v2":
        raise PhaseDRedesignError("independent approval schema_version is invalid")
    if approval.get("decision") != "approve":
        raise PhaseDRedesignError("independent approval decision is not approve")
    reviewer = approval.get("reviewer_principal")
    if not isinstance(reviewer, str) or not reviewer or reviewer in author_principals:
        raise PhaseDRedesignError("independent approval reviewer is not independent")
    if approval.get("reviewed_freeze_sha256") != sha256_file(freeze_path):
        raise PhaseDRedesignError("independent approval does not bind the corrected freeze")
    expected_hashes = {
        str(value["path"]): str(value["sha256"])
        for value in documents.values()
    }
    if approval.get("reviewed_document_sha256") != expected_hashes:
        raise PhaseDRedesignError("independent approval does not bind every corrected document")
    unresolved = approval.get("unresolved_findings")
    if not isinstance(unresolved, list) or any(
        isinstance(item, dict) and str(item.get("severity", "")).lower() in {"critical", "high"}
        for item in unresolved
    ):
        raise PhaseDRedesignError("independent approval has unresolved Critical/High findings")
    return approval


def verify_corrected_freeze(
    root: Path,
    freeze_path: Path,
    *,
    require_execution_approval: bool = False,
) -> dict[str, Any]:
    """Verify every corrected input and keep execution blocked absent independent approval."""
    freeze = load_json(freeze_path)
    if freeze.get("schema_version") != "phase-d-redesign-freeze/v2":
        raise PhaseDRedesignError("corrected freeze schema_version is invalid")
    entries = freeze.get("documents")
    if not isinstance(entries, list) or not entries:
        raise PhaseDRedesignError("corrected freeze must bind documents")
    documents: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PhaseDRedesignError("corrected freeze document entry must be an object")
        kind = entry.get("kind")
        relative = entry.get("path")
        expected = entry.get("sha256")
        if not all(isinstance(value, str) and value for value in (kind, relative, expected)):
            raise PhaseDRedesignError("corrected freeze document entry is invalid")
        if kind in documents:
            raise PhaseDRedesignError(f"corrected freeze repeats document kind: {kind}")
        path = root / str(relative)
        if sha256_file(path) != expected:
            raise PhaseDRedesignError(f"corrected freeze hash mismatch: {kind}")
        documents[str(kind)] = {**entry, "content": load_json(path)}

    required = {
        "independent_findings",
        "ceo_start_proposal",
        "supersession_record",
        "d1_contract",
        "d1_scenario_bank",
        "d2_contract",
        "d2_mutation_bank",
    }
    if not required.issubset(documents):
        missing = ", ".join(sorted(required - set(documents)))
        raise PhaseDRedesignError(f"corrected freeze is missing required documents: {missing}")

    findings = documents["independent_findings"]["content"]
    proposal = documents["ceo_start_proposal"]["content"]
    d1_contract = documents["d1_contract"]["content"]
    d2_contract = documents["d2_contract"]["content"]
    if findings.get("reviewed_head") != "6626411" or findings.get("prior_treatment_conclusions_invalid") is not True:
        raise PhaseDRedesignError("independent findings do not invalidate the reviewed D1/D2 conclusions")
    if proposal.get("current_decision") != "do_not_start" or proposal.get("effective_authorization") is not False:
        raise PhaseDRedesignError("CEO start proposal must remain non-authorizing before review")
    for pilot, contract in (("D1", d1_contract), ("D2", d2_contract)):
        if contract.get("status") != "blocked_pending_independent_approval":
            raise PhaseDRedesignError(f"{pilot} corrected contract is not blocked")
        if contract.get("treatment_execution_authorized") is not False:
            raise PhaseDRedesignError(f"{pilot} corrected contract incorrectly authorizes execution")

    gate = freeze.get("execution_gate")
    if not isinstance(gate, dict) or not isinstance(gate.get("independent_approval_path"), str):
        raise PhaseDRedesignError("corrected freeze execution gate is invalid")
    approval_path = root / str(gate["independent_approval_path"])
    approval: dict[str, Any] | None = None
    if approval_path.exists():
        authors = freeze.get("author_principals")
        if not isinstance(authors, list) or not all(isinstance(item, str) for item in authors):
            raise PhaseDRedesignError("corrected freeze author principals are invalid")
        approval = _validate_independent_approval(
            root,
            freeze_path,
            approval_path,
            documents,
            set(authors),
        )
    execution_authorized = approval is not None
    if require_execution_approval and not execution_authorized:
        raise PhaseDRedesignError("corrected D1/D2 execution requires independent approval")
    return {
        "status": "independently_approved" if execution_authorized else "blocked_pending_independent_approval",
        "execution_authorized": execution_authorized,
        "documents": {kind: entry["content"] for kind, entry in documents.items()},
        "document_sha256": {str(entry["path"]): str(entry["sha256"]) for entry in documents.values()},
        "approval": approval,
    }


def validate_scenario_bank(root: Path, bank_path: Path) -> dict[str, Any]:
    bank = load_json(bank_path)
    if bank.get("schema_version") != "phase-d-d1-scenario-bank/v2":
        raise PhaseDRedesignError("D1 scenario bank schema_version is invalid")
    scenarios = bank.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) < 6:
        raise PhaseDRedesignError("D1 requires at least six recognizable source scenarios")
    seen_ids: set[str] = set()
    seen_categories: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise PhaseDRedesignError("D1 scenarios must be objects")
        scenario_id = scenario.get("id")
        category = scenario.get("product_category")
        features = scenario.get("recognizable_features")
        if not isinstance(scenario_id, str) or scenario_id in seen_ids:
            raise PhaseDRedesignError("D1 scenario ids must be unique strings")
        if not isinstance(category, str) or category in seen_categories:
            raise PhaseDRedesignError("D1 product categories must be unique strings")
        if not isinstance(features, list) or len(features) < 3 or not all(isinstance(item, str) for item in features):
            raise PhaseDRedesignError(f"D1 scenario is not recognizably specified: {scenario_id}")
        source_path = scenario.get("source_path")
        expected_hash = scenario.get("source_sha256")
        if not isinstance(source_path, str) or not isinstance(expected_hash, str):
            raise PhaseDRedesignError(f"D1 source binding is invalid: {scenario_id}")
        source = root / source_path
        if sha256_file(source) != expected_hash:
            raise PhaseDRedesignError(f"D1 source hash mismatch: {scenario_id}")
        if source.stat().st_size > int(bank.get("maximum_source_bytes", 0)):
            raise PhaseDRedesignError(f"D1 source exceeds the frozen byte ceiling: {scenario_id}")
        for required in ("brief", "messages", "channel", "aspect_ratio"):
            if required not in scenario:
                raise PhaseDRedesignError(f"D1 scenario omits {required}: {scenario_id}")
        seen_ids.add(scenario_id)
        seen_categories.add(category)
    return bank


def build_d1_run_specs(
    scenario: dict[str, Any],
    contract: dict[str, Any],
    source_bytes: bytes,
) -> dict[str, dict[str, Any]]:
    """Build paired run specs whose sole difference is assurance workflow."""
    parity = contract.get("paired_input_parity")
    workflows = contract.get("workflows")
    if not isinstance(parity, dict) or not isinstance(workflows, dict):
        raise PhaseDRedesignError("D1 paired-input contract is invalid")
    if sha256_bytes(source_bytes) != scenario.get("source_sha256"):
        raise PhaseDRedesignError("D1 source bytes do not match the frozen scenario")
    common = {
        "scenario_id": scenario["id"],
        "source_bytes": bytes(source_bytes),
        "source_sha256": sha256_bytes(source_bytes),
        "brief": copy.deepcopy(scenario["brief"]),
        "messages": copy.deepcopy(scenario["messages"]),
        "attempt_budget": int(parity["attempts_per_option"]),
        "model": str(parity["model"]),
        "tool": str(parity["tool"]),
        "timeout_seconds": int(parity["timeout_seconds"]),
        "evidence_budget_bytes": int(parity["evidence_budget_bytes_per_option"]),
    }
    return {
        "candidate": {**copy.deepcopy(common), "assurance_workflow": copy.deepcopy(workflows["candidate"])},
        "comparator": {**copy.deepcopy(common), "assurance_workflow": copy.deepcopy(workflows["comparator"])},
    }


def _escape_xml(value: object) -> str:
    text = str(value)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _bounded_lines(text: str, *, maximum_characters: int = 96, width: int = 32) -> list[str]:
    if len(text) > maximum_characters:
        raise PhaseDRedesignError("D1 message exceeds the bounded artifact text ceiling")
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if len(candidate) <= width:
            current = candidate
        else:
            if not current or len(lines) == 2:
                raise PhaseDRedesignError("D1 message cannot fit the bounded artifact")
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def render_bounded_artifact(scenario: dict[str, Any], source_bytes: bytes) -> bytes:
    """Render a deterministic 512px SVG with an enforced message safe area."""
    if sha256_bytes(source_bytes) != scenario.get("source_sha256"):
        raise PhaseDRedesignError("D1 render source does not match the frozen scenario")
    messages = scenario.get("messages")
    if not isinstance(messages, dict):
        raise PhaseDRedesignError("D1 scenario messages are invalid")
    headline = str(messages.get("headline", ""))
    support = str(messages.get("support", ""))
    lines = _bounded_lines(f"{headline} - {support}")
    source_media_type = str(scenario.get("source_media_type", "image/svg+xml"))
    source_b64 = base64.b64encode(source_bytes).decode("ascii")
    text_elements = []
    for index, line in enumerate(lines):
        text_elements.append(
            f'<text x="32" y="{414 + index * 30}" font-family="sans-serif" font-size="24" '
            f'font-weight="700" fill="#ffffff" data-max-width="448">{_escape_xml(line)}</text>'
        )
    title = _escape_xml(scenario.get("product_category", "synthetic product"))
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512" '
        'data-safe-area="0,0,512,512">\n'
        f'  <title>Phase D synthetic {title}</title>\n'
        f'  <image x="0" y="0" width="512" height="384" preserveAspectRatio="xMidYMid meet" '
        f'href="data:{source_media_type};base64,{source_b64}"/>\n'
        '  <rect x="0" y="384" width="512" height="128" fill="#111827"/>\n'
        f'  {"".join(text_elements)}\n'
        '</svg>\n'
    )
    artifact = svg.encode("utf-8")
    validation = validate_bounded_svg(artifact)
    if not validation["bounded"]:
        raise PhaseDRedesignError("D1 renderer produced an overflowing artifact")
    return artifact


def validate_bounded_svg(artifact: bytes) -> dict[str, Any]:
    try:
        root = ET.fromstring(artifact)
    except ET.ParseError as exc:
        raise PhaseDRedesignError(f"D1 artifact is not valid SVG: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise PhaseDRedesignError("D1 artifact root is not SVG")
    try:
        width = float(root.attrib["width"])
        height = float(root.attrib["height"])
        view_box = [float(item) for item in root.attrib["viewBox"].split()]
    except (KeyError, ValueError) as exc:
        raise PhaseDRedesignError("D1 artifact bounds are invalid") from exc
    overflow_reasons: list[str] = []
    if width != 512 or height != 512 or view_box != [0.0, 0.0, 512.0, 512.0]:
        overflow_reasons.append("canvas_or_viewbox_mismatch")
    for element in root.iter():
        kind = element.tag.rsplit("}", 1)[-1]
        if kind in {"rect", "image"}:
            try:
                x = float(element.attrib.get("x", "0"))
                y = float(element.attrib.get("y", "0"))
                item_width = float(element.attrib["width"])
                item_height = float(element.attrib["height"])
            except (KeyError, ValueError):
                overflow_reasons.append(f"invalid_{kind}_bounds")
                continue
            if x < 0 or y < 0 or x + item_width > 512 or y + item_height > 512:
                overflow_reasons.append(f"{kind}_overflow")
        if kind == "text":
            try:
                x = float(element.attrib["x"])
                y = float(element.attrib["y"])
                font_size = float(element.attrib["font-size"])
                maximum_width = float(element.attrib["data-max-width"])
            except (KeyError, ValueError):
                overflow_reasons.append("invalid_text_bounds")
                continue
            estimated_width = len(element.text or "") * font_size * 0.58
            if x < 0 or y - font_size < 0 or y > 512 or estimated_width > maximum_width or x + maximum_width > 512:
                overflow_reasons.append("text_overflow")
    return {
        "bounded": not overflow_reasons,
        "overflow": bool(overflow_reasons),
        "canvas": {"width": int(width), "height": int(height)},
        "overflow_reasons": sorted(set(overflow_reasons)),
    }


def build_rater_form(scenario: dict[str, Any]) -> dict[str, Any]:
    hard_gates = [
        {
            "id": "source_product_integrity",
            "question": "Is the source product recognizable and free of material geometry loss or invention?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "message_and_brief_accuracy",
            "question": "Are every required message and brief constraint accurate, legible, and unsupported claims absent?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "brand_and_rights_safety",
            "question": "Does the option obey the brand rules and avoid people, third-party marks, or rights ambiguity?",
            "allowed": ["pass", "fail", "uncertain"],
        },
        {
            "id": "technical_delivery_integrity",
            "question": "Is the option complete, readable, within the canvas, and free of clipping or overflow?",
            "allowed": ["pass", "fail", "uncertain"],
        },
    ]
    dimensions = [
        (
            "product_recognizability",
            "1: product identity is lost or materially distorted",
            "3: product is recognizable with minor ambiguity",
            "5: product is immediately recognizable and faithfully preserved",
        ),
        (
            "brief_and_message_fidelity",
            "1: key brief or message requirements are missing or wrong",
            "3: core requirements are present with minor weakness",
            "5: every requirement is accurate, clear, and well integrated",
        ),
        (
            "visual_hierarchy_and_legibility",
            "1: hierarchy is confusing or required text is unreadable",
            "3: hierarchy and text are usable with visible weaknesses",
            "5: attention and reading order are immediate at intended size",
        ),
        (
            "brand_coherence",
            "1: visual language conflicts with the stated brand",
            "3: broadly consistent with some generic or uneven choices",
            "5: distinctive, internally consistent, and on-brief",
        ),
        (
            "craft_and_delivery_readiness",
            "1: obvious artifacts, clipping, overflow, or unfinished details",
            "3: deliverable with minor polish issues",
            "5: clean, bounded, polished, and channel-ready",
        ),
    ]
    return {
        "schema_version": "phase-d-d1-rater-form/v2",
        "scenario_id": scenario["id"],
        "instructions": {
            "task": "Evaluate A and B independently, then state a comparative preference.",
            "independence": "Do not seek system identity or custody mapping. Record any accidental disclosure.",
            "hard_gate_rule": "Complete all four gates for each option before dimension scores or preference.",
            "abstention_rule": "Use abstain when evidence is insufficient; use tie only when both are judgeable and equivalent.",
        },
        "scenario": {
            "product_category": scenario["product_category"],
            "recognizable_features": copy.deepcopy(scenario["recognizable_features"]),
            "channel": scenario["channel"],
            "aspect_ratio": scenario["aspect_ratio"],
            "brief": copy.deepcopy(scenario["brief"]),
            "messages": copy.deepcopy(scenario["messages"]),
        },
        "hard_gates": {"per_option": hard_gates, "options": ["A", "B"]},
        "anchored_dimensions": [
            {"id": item[0], "scale": [1, 2, 3, 4, 5], "anchors": {"1": item[1], "3": item[2], "5": item[3]}}
            for item in dimensions
        ],
        "response_template": {
            "hard_gate_results": {"A": {}, "B": {}},
            "dimension_scores": {"A": {}, "B": {}},
            "preference": {"value": None, "allowed": ["A", "B", "tie", "abstain"]},
            "confidence": {"value": None, "scale": [1, 2, 3, 4, 5]},
            "rationale": "",
            "elapsed_minutes": None,
            "protocol_violations": [],
        },
    }


def write_delivery_bundle(
    destination: Path,
    scenario: dict[str, Any],
    option_a: bytes,
    option_b: bytes,
    rater_form: dict[str, Any],
) -> dict[str, Any]:
    """Create a rater-only bundle with no custody or generated-tree references."""
    lowered_parts = {part.lower() for part in destination.parts}
    if lowered_parts & {"custody", "mapping", "generated", "candidate", "comparator"}:
        raise PhaseDRedesignError("D1 rater delivery destination leaks custody or generation identity")
    destination.mkdir(parents=True, exist_ok=False)
    brief = {
        "schema_version": "phase-d-d1-rater-brief/v2",
        "scenario_id": scenario["id"],
        "product_category": scenario["product_category"],
        "recognizable_features": scenario["recognizable_features"],
        "channel": scenario["channel"],
        "aspect_ratio": scenario["aspect_ratio"],
        "brief": scenario["brief"],
        "messages": scenario["messages"],
    }
    files: dict[str, bytes] = {
        "brief.json": (json.dumps(brief, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        "option-A.svg": bytes(option_a),
        "option-B.svg": bytes(option_b),
        "rater-form.json": (json.dumps(rater_form, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    records = []
    for relative, content in sorted(files.items()):
        if len(content) > 1024 * 1024:
            raise PhaseDRedesignError(f"D1 delivery artifact exceeds one MiB: {relative}")
        path = destination / relative
        path.write_bytes(content)
        records.append({"path": relative, "bytes": len(content), "sha256": sha256_bytes(content)})
    bundle_hash = sha256_bytes(canonical_json(records).encode("ascii"))
    manifest = {
        "schema_version": "phase-d-d1-delivery-bundle/v2",
        "scenario_id": scenario["id"],
        "files": records,
        "bundle_sha256": bundle_hash,
        "identity_disclosure": False,
    }
    serialized = canonical_json(manifest).lower()
    if any(term in serialized for term in ("candidate", "comparator", "custody", "mapping", "generated/")):
        raise PhaseDRedesignError("D1 delivery manifest leaks option identity or custody paths")
    write_json(destination / "bundle-manifest.json", manifest)
    return manifest


def create_harness_fixture(destination: Path) -> None:
    """Create a synthetic DB/repository fixture for harness tests and dry runs."""
    destination.mkdir(parents=True, exist_ok=False)
    database = destination / "company.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT NOT NULL, title TEXT NOT NULL);
            CREATE TABLE approvals(id INTEGER PRIMARY KEY, status TEXT NOT NULL, decision TEXT);
            CREATE TABLE audit_log(id INTEGER PRIMARY KEY, action TEXT NOT NULL, details TEXT NOT NULL);
            CREATE TABLE operational_counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
            CREATE TABLE assurance_execution_bindings(id INTEGER PRIMARY KEY, binding_sha256 TEXT NOT NULL);
            INSERT INTO tasks VALUES(1, 'running', 'synthetic canary task');
            INSERT INTO approvals VALUES(1, 'pending', NULL);
            INSERT INTO audit_log VALUES(1, 'fixture_created', '{}');
            INSERT INTO operational_counters VALUES('worker_heartbeats', 7);
            INSERT INTO assurance_execution_bindings VALUES(1, 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
            """
        )
    write_json(destination / "repo" / "contracts" / "freeze.json", {"id": "synthetic-freeze", "status": "frozen"})
    write_json(destination / "repo" / "evidence" / "baseline.json", {"id": "synthetic-baseline", "observed": True})


def _install_database_controls(database: Path, controls: list[str]) -> None:
    statements = {
        "deny_direct_completion": """
            CREATE TRIGGER phase_d_deny_direct_completion
            BEFORE UPDATE OF status ON tasks
            WHEN NEW.status='completed' AND OLD.status!='completed'
            BEGIN SELECT RAISE(ABORT, 'direct completion denied'); END;
        """,
        "protect_approval_records": """
            CREATE TRIGGER phase_d_approvals_immutable_update BEFORE UPDATE ON approvals
            BEGIN SELECT RAISE(ABORT, 'approval mutation denied'); END;
            CREATE TRIGGER phase_d_approvals_immutable_delete BEFORE DELETE ON approvals
            BEGIN SELECT RAISE(ABORT, 'approval deletion denied'); END;
        """,
        "protect_audit_records": """
            CREATE TRIGGER phase_d_audit_immutable_update BEFORE UPDATE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit mutation denied'); END;
            CREATE TRIGGER phase_d_audit_immutable_delete BEFORE DELETE ON audit_log
            BEGIN SELECT RAISE(ABORT, 'audit deletion denied'); END;
        """,
        "protect_execution_bindings": """
            CREATE TRIGGER phase_d_bindings_immutable_update BEFORE UPDATE ON assurance_execution_bindings
            BEGIN SELECT RAISE(ABORT, 'binding mutation denied'); END;
            CREATE TRIGGER phase_d_bindings_immutable_delete BEFORE DELETE ON assurance_execution_bindings
            BEGIN SELECT RAISE(ABORT, 'binding deletion denied'); END;
        """,
    }
    unknown = set(controls) - set(statements)
    if unknown:
        raise PhaseDRedesignError(f"unknown D2 database controls: {', '.join(sorted(unknown))}")
    with sqlite3.connect(database) as conn:
        for control in controls:
            conn.executescript(statements[control])


def _database_snapshot(database: Path) -> dict[str, Any]:
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        triggers = [
            {"name": row[0], "table": row[1], "sql": row[2]}
            for row in conn.execute(
                "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
            )
        ]
        rows = {
            table: [dict(row) for row in conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            for table in tables
        }
    return {"file_sha256": sha256_file(database), "tables": rows, "triggers": triggers}


def _tree_snapshot(fixture: Path) -> dict[str, Any]:
    database = _database_snapshot(fixture / "company.sqlite3")
    repo_root = fixture / "repo"
    repo_files = {
        path.relative_to(repo_root).as_posix(): {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
        for path in sorted(repo_root.rglob("*"))
        if path.is_file()
    }
    state = {"database": database, "repository": repo_files}
    return {**state, "state_sha256": sha256_bytes(canonical_json(state).encode("ascii"))}


def _copy_fixture(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, copy_function=shutil.copy2)


def _restore_fixture(source: Path, destination: Path) -> None:
    source_files = {
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file()
    }
    for path in sorted((item for item in destination.rglob("*") if item.is_file()), reverse=True):
        if path.relative_to(destination) not in source_files:
            path.unlink()
    for relative in sorted(source_files):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)


def _apply_mutation(fixture: Path, mutation: dict[str, Any], protected_paths: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    kind = mutation.get("kind")
    event: dict[str, Any] = {"kind": kind, "attempted": True}
    if kind == "sql":
        statement = mutation.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            raise PhaseDRedesignError("D2 SQL mutation is invalid")
        try:
            with sqlite3.connect(fixture / "company.sqlite3") as conn:
                conn.execute(statement)
            event.update({"applied": True, "error": None})
            observation = {"outcome": "allowed", "mechanism": "database_commit", "detail": "mutation committed in isolated copy"}
        except sqlite3.DatabaseError as exc:
            event.update({"applied": False, "error": str(exc)})
            observation = {"outcome": "denied", "mechanism": "sqlite_control", "detail": str(exc)}
        return event, observation
    if kind in {"repo_write", "repo_delete"}:
        relative = mutation.get("path")
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise PhaseDRedesignError("D2 repository mutation path is invalid")
        target = fixture / "repo" / relative
        if relative in protected_paths:
            event.update({
                "applied": False,
                "path": relative,
                "error": "protected repository path mutation denied",
            })
            observation = {
                "outcome": "denied",
                "mechanism": "repository_integrity_guard",
                "detail": "protected path mutation denied in isolated copy",
            }
            return event, observation
        if kind == "repo_write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(mutation.get("content", "")), encoding="utf-8")
        elif target.exists():
            target.unlink()
        event.update({"applied": True, "path": relative, "error": None})
        observation = {"outcome": "allowed", "mechanism": "repository_write", "detail": "mutation allowed in isolated copy"}
        return event, observation
    raise PhaseDRedesignError(f"unsupported D2 mutation kind: {kind}")


def _select_noninterference(snapshot: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    tables = snapshot["database"]["tables"]
    repo = snapshot["repository"]
    protected_tables = case.get("protected_tables", [])
    protected_paths = case.get("protected_repo_paths", [])
    if not isinstance(protected_tables, list) or not isinstance(protected_paths, list):
        raise PhaseDRedesignError("D2 noninterference protections are invalid")
    return {
        "tables": {str(name): tables.get(str(name)) for name in protected_tables},
        "repository": {str(path): repo.get(str(path)) for path in protected_paths},
    }


def _execute_mutation_side(
    frozen_fixture: Path,
    case: dict[str, Any],
    workflow: dict[str, Any],
    side: str,
    evidence_root: Path,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"phase-d-d2-{side}-") as temporary:
        temporary_root = Path(temporary)
        isolated = temporary_root / "isolated"
        _copy_fixture(frozen_fixture, isolated)
        controls = workflow.get("database_controls", [])
        protected_paths = workflow.get("protected_repo_paths", [])
        if not isinstance(controls, list) or not isinstance(protected_paths, list):
            raise PhaseDRedesignError("D2 workflow controls are invalid")
        _install_database_controls(isolated / "company.sqlite3", [str(item) for item in controls])
        rollback_source = temporary_root / "rollback-source"
        _copy_fixture(isolated, rollback_source)
        before = _tree_snapshot(isolated)
        protected_before = _select_noninterference(before, case)
        mutation_event, observation = _apply_mutation(
            isolated,
            copy.deepcopy(case["mutation"]),
            {str(item) for item in protected_paths},
        )
        observed = _tree_snapshot(isolated)
        protected_observed = _select_noninterference(observed, case)
        noninterference = {
            "passed": protected_before == protected_observed,
            "protected_before_sha256": sha256_bytes(canonical_json(protected_before).encode("ascii")),
            "protected_observed_sha256": sha256_bytes(canonical_json(protected_observed).encode("ascii")),
            "protected_tables": case.get("protected_tables", []),
            "protected_repo_paths": case.get("protected_repo_paths", []),
        }
        _restore_fixture(rollback_source, isolated)
        after = _tree_snapshot(isolated)
        rollback = {
            "completed": before["state_sha256"] == after["state_sha256"],
            "method": "restore_frozen_pre_mutation_copy",
            "before_state_sha256": before["state_sha256"],
            "after_state_sha256": after["state_sha256"],
        }
        audit_rows = observed["database"]["tables"].get("audit_log", [])
        event = {
            "case_id": case["id"],
            "workflow_id": workflow["id"],
            "side": side,
            "mutation": mutation_event,
            "observation": observation,
        }
        audit_event_evidence = {
            "event": event,
            "event_sha256": sha256_bytes(canonical_json(event).encode("ascii")),
            "observed_audit_rows": audit_rows,
            "observed_audit_rows_sha256": sha256_bytes(canonical_json(audit_rows).encode("ascii")),
        }
        record = {
            "workflow": copy.deepcopy(workflow),
            "before_snapshot": before,
            "mutation": mutation_event,
            "observation": observation,
            "rollback": rollback,
            "after_snapshot": after,
            "audit_event_evidence": audit_event_evidence,
            "noninterference": noninterference,
        }
        if not rollback["completed"]:
            raise PhaseDRedesignError(f"D2 rollback failed: {case['id']} {side}")
        if not noninterference["passed"]:
            raise PhaseDRedesignError(f"D2 noninterference failed: {case['id']} {side}")
        destination = evidence_root / str(case["id"]) / side
        artifacts = {
            "before-snapshot.json": before,
            "mutation.json": mutation_event,
            "observation.json": observation,
            "rollback.json": rollback,
            "after-snapshot.json": after,
            "audit-event-evidence.json": audit_event_evidence,
            "noninterference.json": noninterference,
        }
        for name, value in artifacts.items():
            write_json(destination / name, value)
        return record


def execute_mutation_pair(
    frozen_fixture: Path,
    case: dict[str, Any],
    baseline_workflow: dict[str, Any],
    treatment_workflow: dict[str, Any],
    evidence_root: Path,
) -> dict[str, Any]:
    """Run one fault against separate baseline and treatment copies, then restore both."""
    if baseline_workflow.get("id") == treatment_workflow.get("id") or canonical_json(baseline_workflow) == canonical_json(treatment_workflow):
        raise PhaseDRedesignError("D2 baseline and treatment workflows must be distinct")
    if not (frozen_fixture / "company.sqlite3").is_file() or not (frozen_fixture / "repo").is_dir():
        raise PhaseDRedesignError("D2 frozen fixture must include a database and repository copy")
    return {
        "case_id": case["id"],
        "seeded_fault": bool(case.get("seeded_fault")),
        "valid_control": bool(case.get("valid_control")),
        "baseline": _execute_mutation_side(
            frozen_fixture, case, baseline_workflow, "baseline", evidence_root
        ),
        "treatment": _execute_mutation_side(
            frozen_fixture, case, treatment_workflow, "treatment", evidence_root
        ),
    }


def derive_d2_observation_thresholds(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive comparison requirements from baseline observations, never constants."""
    if not pairs:
        raise PhaseDRedesignError("D2 threshold derivation requires paired observations")
    baseline_escapes = sorted(
        str(pair["case_id"])
        for pair in pairs
        if pair.get("seeded_fault") is True and pair["baseline"]["observation"]["outcome"] == "allowed"
    )
    baseline_allowed_controls = sorted(
        str(pair["case_id"])
        for pair in pairs
        if pair.get("valid_control") is True and pair["baseline"]["observation"]["outcome"] == "allowed"
    )
    treatment_denied = {
        str(pair["case_id"])
        for pair in pairs
        if pair["treatment"]["observation"]["outcome"] == "denied"
    }
    treatment_allowed = {
        str(pair["case_id"])
        for pair in pairs
        if pair["treatment"]["observation"]["outcome"] == "allowed"
    }
    return {
        "threshold_source": "paired_baseline_observations",
        "observed_baseline_escape_ids": baseline_escapes,
        "required_treatment_denial_ids": baseline_escapes,
        "observed_baseline_allowed_control_ids": baseline_allowed_controls,
        "required_treatment_allow_ids": baseline_allowed_controls,
        "treatment_denied_required_ids": sorted(set(baseline_escapes) & treatment_denied),
        "treatment_allowed_required_control_ids": sorted(set(baseline_allowed_controls) & treatment_allowed),
        "observation_derived_comparison_passed": (
            bool(baseline_escapes)
            and set(baseline_escapes).issubset(treatment_denied)
            and set(baseline_allowed_controls).issubset(treatment_allowed)
        ),
    }


def _artifact_manifest(output: Path) -> dict[str, Any]:
    records = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "evidence-manifest.json":
            records.append({
                "path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return {
        "schema_version": "phase-d-redesign-evidence-manifest/v1",
        "status": "dry_run_complete_treatments_blocked",
        "corrected_treatments_executed": False,
        "artifacts": records,
        "external_actions": ["git_push"],
    }


def run_redesign_dry_run(root: Path, output: Path) -> dict[str, Any]:
    """Validate corrected tooling on synthetic canaries without executing treatments."""
    freeze = root / "docs" / "assurance" / "phase-d" / "redesign" / "corrected-freeze-v2.json"
    verification = verify_corrected_freeze(root, freeze)
    if verification["execution_authorized"]:
        raise PhaseDRedesignError("redesign dry run must not run after treatment authorization")
    if output.exists():
        raise PhaseDRedesignError("redesign dry-run output must not already exist")
    output.mkdir(parents=True)

    documents = verification["documents"]
    bank_path = root / documents["d1_contract"]["scenario_bank"]
    scenario_bank = validate_scenario_bank(root, bank_path)
    d1_generated_root = output / "dry-run" / "d1" / "working"
    d1_delivery_root = output / "dry-run" / "d1" / "rater-delivery"
    custody_mappings = []
    delivery_records = []
    for scenario in scenario_bank["scenarios"]:
        source = (root / scenario["source_path"]).read_bytes()
        specs = build_d1_run_specs(scenario, documents["d1_contract"], source)
        candidate = copy.deepcopy(specs["candidate"])
        comparator = copy.deepcopy(specs["comparator"])
        candidate_workflow = candidate.pop("assurance_workflow")
        comparator_workflow = comparator.pop("assurance_workflow")
        if candidate != comparator or candidate_workflow == comparator_workflow:
            raise PhaseDRedesignError(f"D1 dry-run parity failed: {scenario['id']}")
        artifact = render_bounded_artifact(scenario, source)
        for side in ("candidate", "comparator"):
            working = d1_generated_root / str(scenario["id"]) / side
            working.mkdir(parents=True)
            (working / "artifact.svg").write_bytes(artifact)
            write_json(working / "run-spec.json", {
                **{key: value for key, value in specs[side].items() if key != "source_bytes"},
                "source_bytes_sha256": sha256_bytes(specs[side]["source_bytes"]),
                "artifact_sha256": sha256_bytes(artifact),
                "artifact_validation": validate_bounded_svg(artifact),
                "dry_run_only": True,
            })
        assignment = {"A": "candidate", "B": "comparator"}
        delivery = d1_delivery_root / str(scenario["id"])
        manifest = write_delivery_bundle(
            delivery,
            scenario,
            artifact,
            artifact,
            build_rater_form(scenario),
        )
        custody_mappings.append({
            "scenario_id": scenario["id"],
            "assignment": assignment,
            "candidate_artifact_sha256": sha256_bytes(artifact),
            "comparator_artifact_sha256": sha256_bytes(artifact),
            "delivery_bundle_sha256": manifest["bundle_sha256"],
            "dry_run_only": True,
        })
        delivery_records.append({
            "scenario_id": scenario["id"],
            "bundle_sha256": manifest["bundle_sha256"],
            "delivery_path": delivery.relative_to(output).as_posix(),
        })
    write_json(output / "custody" / "d1" / "custody-mapping.json", {
        "schema_version": "phase-d-d1-dry-run-custody/v2",
        "access": "not_for_raters",
        "treatment_execution": False,
        "mappings": custody_mappings,
    })
    d1_result = {
        "schema_version": "phase-d-d1-redesign-dry-run/v1",
        "status": "dry_run_complete_treatment_blocked",
        "scenario_count": len(delivery_records),
        "paired_input_parity_verified": True,
        "bounded_nonoverflow_artifacts_verified": True,
        "delivery_bundles": delivery_records,
        "ratings_collected": 0,
        "treatment_execution": False,
    }
    write_json(output / "dry-run" / "d1" / "result.json", d1_result)

    mutation_bank = documents["d2_mutation_bank"]
    cases = mutation_bank.get("cases")
    dry_run_ids = mutation_bank.get("dry_run_subset")
    if not isinstance(cases, list) or not isinstance(dry_run_ids, list):
        raise PhaseDRedesignError("D2 mutation bank dry-run subset is invalid")
    by_id = {str(case["id"]): case for case in cases if isinstance(case, dict)}
    selected = []
    for case_id in dry_run_ids:
        if case_id not in by_id:
            raise PhaseDRedesignError(f"D2 dry-run case is missing: {case_id}")
        selected.append(by_id[str(case_id)])
    fixture = output / "dry-run" / "d2" / "frozen-fixture"
    create_harness_fixture(fixture)
    fixture_before = _tree_snapshot(fixture)
    baseline = documents["d2_contract"]["workflows"]["baseline"]
    treatment = documents["d2_contract"]["workflows"]["treatment"]
    pairs = [
        execute_mutation_pair(
            fixture,
            case,
            baseline,
            treatment,
            output / "dry-run" / "d2" / "cases",
        )
        for case in selected
    ]
    fixture_after = _tree_snapshot(fixture)
    if fixture_before["state_sha256"] != fixture_after["state_sha256"]:
        raise PhaseDRedesignError("D2 dry run changed the frozen source fixture")
    comparison = derive_d2_observation_thresholds(pairs)
    d2_result = {
        "schema_version": "phase-d-d2-redesign-dry-run/v1",
        "status": "dry_run_complete_treatment_blocked",
        "canary_count": len(pairs),
        "canary_ids": [pair["case_id"] for pair in pairs],
        "threshold_source": comparison["threshold_source"],
        "comparison": comparison,
        "frozen_fixture_unchanged": True,
        "treatment_execution": False,
    }
    write_json(output / "dry-run" / "d2" / "result.json", d2_result)

    result = {
        "schema_version": "phase-d-redesign-dry-run/v1",
        "status": "dry_run_complete_treatments_blocked",
        "corrected_treatments_executed": False,
        "independent_approval_present": False,
        "d1": d1_result,
        "d2": d2_result,
        "forbidden_actions_observed": [],
    }
    write_json(output / "dry-run-result.json", result)
    write_json(output / "evidence-manifest.json", _artifact_manifest(output))
    return result
