"""Domain constants and seed data."""

from __future__ import annotations

ROLES = {
    "Chairman": "Only human. Owns mission, capital allocation, reserved decisions, and final external control.",
    "CEO": "Agent. Coordinates company execution and is the only agent that may write Chairman inbox/outbox files.",
    "CPO": "Agent. Owns product requirements, customer workflows, and roadmap proposals.",
    "CTO": "Agent. Leads a compact engineering group and owns architecture, delivery quality, validation, and release readiness.",
    "Product Engineer": "Agent reporting to CTO. Builds the end-to-end product across frontend, backend, and integrations; optimizes for working vertical slices.",
    "AI Platform & Quality Engineer": "Agent reporting to CTO. Owns model integration, evaluation, reliability, security checks, test automation, and release evidence.",
    "CRO": "Agent. Owns GTM, pipeline design, pricing experiments, and revenue operations.",
    "COO": "Agent. Owns cadence, metrics hygiene, operating process, and risk register.",
    "CFO": "Agent. Owns unit economics, runway model, budgets, and spend approval requests.",
    "Counsel": "Agent. Flags legal/compliance risk but never provides legal autonomy or binds the company.",
}

RACI = {
    "strategy": ("CEO", "Chairman", "CPO,CRO,CFO,COO", "All agents"),
    "roadmap": ("CPO", "CEO", "CTO,CRO", "Chairman"),
    "engineering": ("CTO", "CEO", "CPO", "Chairman"),
    "pricing": ("CRO", "Chairman", "CFO,CEO", "All agents"),
    "spend": ("CFO", "Chairman", "CEO", "All agents"),
    "legal": ("Counsel", "Chairman", "CEO", "All agents"),
    "cadence": ("COO", "CEO", "All agents", "Chairman"),
}

RESERVED_KEYWORDS = {
    "external_publish": ("publish", "public", "launch externally"),
    "external_spend": ("spend", "ad buy", "paid campaign", "vendor payment"),
    "legal_commitment": ("legal", "contract", "terms", "commitment"),
    "contract_signature": ("sign", "signature", "agreement"),
    "production_deploy": ("deploy production", "release to users", "production"),
    "data_export": ("export customer", "download user data", "data export"),
    "pricing_change": ("price", "pricing", "discount"),
}

SEED_TASKS = [
    ("CPO", "Draft configurable AI image generation/editing product requirements", "product", 90),
    ("CTO", "Build deterministic local prompt-to-artifact backend prototype", "engineering", 85),
    ("CRO", "Design first ICP, offer, and GTM experiment backlog", "gtm", 80),
    ("CFO", "Create unit economics baseline for image generation/editing product", "finance", 75),
    ("COO", "Set weekly operating cadence and KPI review loop", "operations", 70),
    ("Counsel", "Review reserved-action policy and disclaimers for human control", "risk", 65),
]
