# Visual QA Scorecard

`visual-qa-scorecard` computes a deterministic scorecard from explicitly supplied
observations for edit fidelity and brand consistency. The CLI accepts numeric values
from `0` to `100`, records observer and method references, and writes an atomic JSON
result with a stable checksum.

Scoring is deterministic:

- `composite_score = edit_fidelity * 0.6 + brand_consistency * 0.4`
- `pass` requires composite score `>= 85` and each measurement `>= 70`
- `fail` covers lower non-stop scores
- `stop` applies when any measurement is below `50` or an observation severity is
  `stop` or `critical`

This scorecard does not measure images directly and does not claim actual image
quality. It only scores the explicit measured observations supplied by a reviewer,
test harness, or other controlled observation source.

Example:

```bash
python3.11 -m agent_company.cli visual-qa-scorecard examples/visual-qa-scorecard.json
```
