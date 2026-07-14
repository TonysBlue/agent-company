# Product Risk Control Review

Date: 2026-07-11
Owner: CEO; Legal/Compliance Specialist invoked on demand
Scope: internal operational risk review only; not legal advice and not authorization for production use, customer-data processing, external publication, or a legal commitment

## Evidence reviewed

- `agent_company/backend.py` creates deterministic JSON concept metadata from a prompt but does not create an image.
- `agent_company/brandkit.py` validates brand constraints and creates deterministic campaign manifests.
- `docs/versioning-and-records.md` prohibits committing customer personal data and requires protected handling before such data is introduced.
- `docs/constitution.md` reserves customer-data, legal, production, publication, pricing, financial, and irreversible actions for Chairman approval.

## Control gap: no enforceable source-rights and likeness gate

The current artifact and campaign schemas do not require source provenance, rights basis, consent status, trademark review, retention class, or a reviewer decision. The generated edit plan says to preserve subject identity, but this is descriptive metadata rather than an enforceable control. A prompt or future source asset involving a person, trademark, copyrighted work, confidential material, or customer data can therefore enter the internal workflow without a structured stop signal.

This gap is material before any real source asset or customer data is processed. Existing determinism, checksums, brand validation, and human-controlled publishing do not establish that an input may lawfully or safely be used.

## Bounded mitigation

Before enabling real-source ingestion, add a versioned provenance record to every source asset and derived artifact with, at minimum:

- stable source identifier and parent lineage;
- source category and origin;
- asserted rights basis and evidence reference;
- person/likeness presence and consent status;
- trademark/brand presence and review status;
- customer-data and confidentiality classification;
- retention/deletion class;
- policy flags and reviewer decision (`pending`, `approved_internal`, or `rejected`).

Fail closed: missing or invalid provenance keeps the asset at `pending` and blocks generation, editing, export, and publication. Evidence references must point to restricted records rather than embedding contracts, identity documents, personal data, credentials, or payment information in Git or ordinary artifacts.

## Escalation criteria

Escalate to the CEO immediately and do not process the asset when any of the following applies:

- rights basis is unknown, disputed, expired, or inconsistent with the requested use;
- a real person's face, voice-equivalent identity cue, biometric data, or sensitive personal data is present without verified scope-appropriate consent;
- a third-party trademark, character, copyrighted work, confidential source, or customer asset lacks documented permission;
- deletion, legal hold, jurisdiction, age, or data-residency requirements are unclear;
- policy flags conflict, provenance lineage breaks, or review evidence cannot be verified.

The CEO must seek Chairman approval before real customer outreach or data handling, pricing commitments, payment or budgets, external publication, production release, legal commitment, or another reserved action. An on-demand Legal/Compliance Specialist should recommend qualified external legal review where ownership, consent, privacy, or jurisdiction cannot be resolved internally.

## Acceptance signal

`provenance_gate_escape_rate = artifacts accepted for internal use with missing/invalid required provenance / artifacts accepted for internal use`.

Owner: Product Engineer. Cadence: every artifact validation and CI regression run, independently reviewed on demand and escalated to the CEO or Legal/Compliance Specialist as required. Data source: versioned provenance records, policy flags, and artifact review decisions. Required threshold: `0`; any escape is a stop event and CEO escalation.
