# Prompt-Pack Expansion

PixWeave now provides a deterministic internal prompt-pack workflow for the roadmap's configurable prompt-pack milestone. A pack declares `prompt-pack/v1`, a semantic version, a format template, and a non-empty variable matrix. `python3 -m agent_company prompt-pack examples/prompt-pack.json` validates and expands every combination into an atomic JSON manifest.

Each rendered prompt has a stable identifier derived from pack name, version, and selected variables. The manifest records the source-pack fingerprint and its own checksum. Validation fails closed on missing or malformed placeholders, undefined or unused variables, empty values, and duplicate values.

This capability creates prompt metadata only. It does not generate an image, demonstrate visual quality, grant rights to input material, authorize customer-data processing, or authorize external publication or production deployment.
