"""Permanent fail-closed tombstone for superseded Phase D treatment helpers."""


class PhaseDTreatmentError(ValueError):
    """Raised when superseded treatment code is invoked."""


BLOCKED_REASON = (
    "legacy Phase D treatment helpers are permanently disabled; no D1/D2 treatment, "
    "evaluation, payload generation, subprocess execution, or evidence output is available"
)
