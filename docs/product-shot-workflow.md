# Product-Shot Workflow Manifest

`product-shot-workflow` builds a deterministic `product-shot-manifest/v1` artifact
from a versioned workflow definition and at least three scenario inputs. The command
requires source provenance for every scenario source and fails closed when provenance is
missing, malformed, mismatched, pending, or rejected.

The manifest records explicit controls, ordered stages, acceptance checks, stable
scenario IDs, source lineage, and a checksum. It does not generate images, inspect
images, measure image quality, or claim that the described output is visually correct.
`approved_internal` provenance authorizes only this internal metadata operation; all
reserved actions still require Chairman control.

Example:

```bash
python3.11 -m agent_company.cli product-shot-workflow examples/product-shot-workflow.json
```
