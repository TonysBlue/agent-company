# Controlled Beta Data Rights, Privacy, and Consent Check

Status: internal risk draft; not legal advice
Owner: Legal/Compliance Specialist (on demand)
Scope: preparation for Chairman-approved controlled Beta only

## Risk checklist

- Accept only participants able to consent and assets with documented provenance and an asserted rights basis; exclude minors, biometrics, sensitive personal data, regulated/high-risk imagery, and unclear third-party rights.
- Minimize collection to a participant pseudonym, consent evidence, asset-rights attestation, session/issue identifiers, metrics, and optional contact details. Never commit raw customer content or contact data to Git.
- Restrict records by role, log access, encrypt protected storage and backups, and test deletion and recovery before real data is accepted.
- Do not publish, train on, sell, repurpose, or transfer participant assets or outputs. No production deployment or external processor is authorized without separate review and Chairman approval.
- Provide a withdrawal/deletion route. Preserve only the minimum audit record where an approved obligation requires it; document conflicts and decisions.
- Pause immediately for suspected consent, rights, privacy, access, deletion, or exposure failures; notify the CEO, on-demand Legal/Compliance Specialist, Product Engineer, and Chairman through the internal incident process.

## Consent text draft

> PixWeave is conducting a limited product-evaluation session. Participation is voluntary. We will process the rights-cleared assets you choose to provide, your workflow instructions, generated outputs, operational measurements, and your optional feedback solely to run and evaluate this session and improve the product. We will not publish your assets or outputs, use them for model training, or contact you beyond the agreed session purpose without separate permission. Access is limited to authorized operators. You may decline questions or stop the session and request deletion through the stated internal contact, subject to any specifically disclosed retention obligation. Please confirm that you are authorized to provide the assets and that they do not contain prohibited sensitive data or third-party material lacking permission.

The consent record must include protocol/text version, timestamp, participant pseudonym, affirmative consent status, rights attestation, approved purpose, data categories, retention class, withdrawal route, facilitator, and Chairman approval reference. Silence and prechecked boxes are not consent. Missing consent or rights evidence stops the session.

## Retention and deletion

- Rejected intake and unconsented content: do not retain; securely remove immediately and record only a non-sensitive rejection event.
- Session assets and generated artifacts: default deletion 30 days after session closure unless the Chairman approves a shorter documented evaluation need; no indefinite retention.
- Structured session metrics, consent proof, rights attestation, and incident evidence: retain 12 months after phase closure for audit, then review and delete unless a documented obligation or active incident hold applies.
- Optional contact details: retain only for the explicitly consented purpose and delete at withdrawal or 30 days after final approved follow-up, whichever comes first.
- Backups follow the same expiry intent and must age out within the tested backup lifecycle. Deletion records include scope, request/expiry date, operator, completion date, and exceptions without retaining deleted content.

## Chairman decision points

Approval is required before: recruiting or contacting external participants; creating accounts; processing real customer/contact/assets data; selecting production storage or external subprocessors; changing retention periods; publishing outputs; using data for training; setting pricing/payment/compensation; contract or legal commitments; production deployment; or resuming after a critical rights/privacy incident.

Before approval, the on-demand Legal/Compliance Specialist should flag applicable jurisdiction, entity/controller roles, participant notice language, processor terms, cross-border handling, deletion feasibility, incident route, and whether professional legal advice is required. Internal synthetic fixtures may be used meanwhile but do not count as customer evidence.
