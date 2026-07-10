"""Governance and approval policy."""

from __future__ import annotations

import re

from .config import CompanyConfig
from .models import RESERVED_KEYWORDS


DISCLAIMER = (
    "This system is an agentic operating aid. It does not have legal autonomy, "
    "does not bind the company, and requires Chairman control for external, "
    "regulated, financial, legal, or irreversible actions."
)


def classify_reserved_action(text: str, config: CompanyConfig) -> str | None:
    lowered = text.lower()
    for action in config.reserved_actions:
        if _contains_phrase(lowered, action.replace("_", " ")):
            return action
        for keyword in RESERVED_KEYWORDS.get(action, ()):
            if _contains_phrase(lowered, keyword):
                return action
    return None


def requires_chairman(action_type: str | None) -> bool:
    return action_type is not None


def _contains_phrase(text: str, phrase: str) -> bool:
    escaped = re.escape(phrase.lower())
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None
