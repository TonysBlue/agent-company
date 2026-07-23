"""Domain constants and seed data."""

from __future__ import annotations

ROLES = {
    "Chairman": "Only human. Owns mission, capital allocation, reserved decisions, and final external control.",
    "CEO": "Resident agent. Owns product priorities, company operations, and Chairman decision flow.",
    "Product Engineer": "Resident agent. Builds and validates end-to-end product increments across frontend, backend, AI integration, reliability, and release evidence.",
    "Customer & Revenue": "Resident agent. Owns customer discovery preparation, GTM, pipeline, and revenue operations within Chairman approval boundaries.",
}

ON_DEMAND_CAPABILITIES = {
    "Company Platform Engineer": "Develops and maintains the Agent Company control plane, runtime, ledger, runners, workspace isolation, and management dashboard.",
    "Control & Reliability Reviewer": "Independently verifies control-plane state machines, migrations, governance boundaries, recovery, and release evidence.",
    "Finance & Risk Reviewer": "Reviews unit economics, budgets, financial controls, and material operating risk when requested.",
    "Legal/Compliance Specialist": "Flags legal and compliance issues when requested; does not provide legal autonomy or bind the company.",
    "Independent Quality Reviewer": "Provides independent product, evidence, security, reliability, and release-quality review when requested.",
    "Codex workers": "Execute bounded, reviewable, verifiable work asynchronously; they are not standing roles or decision-makers.",
}

RACI = {
    "strategy": ("CEO", "Chairman", "Product Engineer,Customer & Revenue", "All resident agents"),
    "product_priority": ("CEO", "CEO", "Product Engineer,Customer & Revenue", "Chairman"),
    "company_platform": ("Company Platform Engineer", "CEO", "Control & Reliability Reviewer", "Chairman"),
    "company_platform_review": ("Control & Reliability Reviewer", "CEO", "Company Platform Engineer", "Chairman"),
    "product_delivery": ("Product Engineer", "CEO", "Independent Quality Reviewer", "Chairman,Customer & Revenue"),
    "customer_revenue": ("Customer & Revenue", "CEO", "Product Engineer", "Chairman"),
    "customer_outreach": ("Customer & Revenue", "Chairman", "CEO", "Product Engineer"),
    "pricing": ("Customer & Revenue", "Chairman", "CEO,Finance & Risk Reviewer", "Product Engineer"),
    "spend": ("CEO", "Chairman", "Finance & Risk Reviewer", "Product Engineer,Customer & Revenue"),
    "legal": ("Legal/Compliance Specialist", "Chairman", "CEO", "Product Engineer,Customer & Revenue"),
    "public_release": ("Customer & Revenue", "Chairman", "CEO,Product Engineer", "Independent Quality Reviewer"),
    "production_deploy": ("Product Engineer", "Chairman", "CEO,Independent Quality Reviewer", "Customer & Revenue"),
    "irreversible_action": ("CEO", "Chairman", "Finance & Risk Reviewer,Legal/Compliance Specialist", "All resident agents"),
    "cadence": ("CEO", "CEO", "Product Engineer,Customer & Revenue", "Chairman"),
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
    ("Product Engineer", "Deliver the highest-priority reviewable product increment", "product", 90),
    ("Customer & Revenue", "Prepare the highest-priority evidence-backed commercial action", "gtm", 80),
]
