"""Configuration loading for the company OS."""

from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_RESERVED_ACTIONS = [
    "external_publish",
    "external_spend",
    "legal_commitment",
    "contract_signature",
    "production_deploy",
    "data_export",
    "pricing_change",
]


@dataclass(frozen=True)
class CompanyConfig:
    workspace: Path
    db_path: Path
    chairman_inbox: Path
    chairman_outbox: Path
    artifacts_dir: Path
    logs_dir: Path
    product_name: str
    cycle_task_limit: int
    backend: str
    codex_enabled: bool
    reserved_actions: tuple[str, ...] = field(default_factory=lambda: tuple(DEFAULT_RESERVED_ACTIONS))
    ceo_hermes_timeout_seconds: int = 120
    ceo_external_delivery_enabled: bool = True


def load_config(path: str | Path | None = None) -> CompanyConfig:
    workspace = Path.cwd().resolve()
    config_path = Path(path).resolve() if path else workspace / "config" / "sample.ini"
    parser = configparser.ConfigParser()
    if config_path.exists():
        parser.read(config_path)

    def get(section: str, key: str, fallback: str) -> str:
        return parser.get(section, key, fallback=fallback)

    def get_bool(section: str, key: str, fallback: bool) -> bool:
        return parser.getboolean(section, key, fallback=fallback)

    db_path = workspace / get("paths", "database", "data/company.sqlite3")
    inbox = workspace / get("paths", "chairman_inbox", "data/chairman/inbox")
    outbox = workspace / get("paths", "chairman_outbox", "data/chairman/outbox")
    artifacts = workspace / get("paths", "artifacts", "data/artifacts")
    logs = workspace / get("paths", "logs", "logs")
    reserved = get("governance", "reserved_actions", ",".join(DEFAULT_RESERVED_ACTIONS))
    reserved_actions = tuple(item.strip() for item in reserved.split(",") if item.strip())
    return CompanyConfig(
        workspace=workspace,
        db_path=db_path,
        chairman_inbox=inbox,
        chairman_outbox=outbox,
        artifacts_dir=artifacts,
        logs_dir=logs,
        product_name=get("company", "product_name", "织象 PixWeave"),
        cycle_task_limit=int(get("company", "cycle_task_limit", "2")),
        backend=get("backend", "name", "local"),
        codex_enabled=get_bool("backend", "codex_enabled", False),
        reserved_actions=reserved_actions,
        ceo_hermes_timeout_seconds=int(get("ceo_runtime", "hermes_timeout_seconds", "120")),
        ceo_external_delivery_enabled=get_bool("ceo_runtime", "external_delivery_enabled", True),
    )
