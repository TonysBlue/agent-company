"""Versioned prompt-pack validation and deterministic expansion."""

from __future__ import annotations

import itertools
import re
from typing import Any

from .brandkit import stable_sha256

PROMPT_PACK_SCHEMA_VERSION = "prompt-pack/v1"
PROMPT_MANIFEST_SCHEMA_VERSION = "prompt-manifest/v1"
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
VARIABLE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


class PromptPackError(ValueError):
    """Raised when a prompt pack cannot be expanded safely."""


def validate_prompt_pack(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != PROMPT_PACK_SCHEMA_VERSION:
        errors.append(f"schema_version must be {PROMPT_PACK_SCHEMA_VERSION}")
    name = data.get("name")
    if not isinstance(name, str) or not NAME.fullmatch(name):
        errors.append("name must be a valid non-empty identifier")
    version = data.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        errors.append("version must use MAJOR.MINOR.PATCH")
    template = data.get("template")
    if not isinstance(template, str) or not template.strip():
        errors.append("template must be a non-empty string")
        template = ""
    variables = data.get("variables")
    if not isinstance(variables, dict) or not variables:
        errors.append("variables must be a non-empty object")
        variables = {}
    else:
        for key, values in variables.items():
            label = f"variables.{key}"
            if not isinstance(key, str) or not VARIABLE.fullmatch(key):
                errors.append(f"{label} has an invalid variable name")
                continue
            if not isinstance(values, list) or not values:
                errors.append(f"{label} must be a non-empty string list")
            elif not all(isinstance(value, str) and value.strip() for value in values):
                errors.append(f"{label} must contain non-empty strings")
            elif len(values) != len(set(values)):
                errors.append(f"{label} must not contain duplicate values")
    placeholders = set(PLACEHOLDER.findall(template))
    variable_names = set(variables)
    for missing in sorted(placeholders - variable_names):
        errors.append(f"template variable {{{missing}}} is not defined")
    for unused in sorted(variable_names - placeholders):
        errors.append(f"variables.{unused} is not used by template")
    # Reject stray braces so expansion never silently preserves malformed placeholders.
    if PLACEHOLDER.sub("", template).count("{") or PLACEHOLDER.sub("", template).count("}"):
        errors.append("template contains an invalid placeholder")
    return errors


def build_prompt_manifest(data: dict[str, Any]) -> dict[str, Any]:
    errors = validate_prompt_pack(data)
    if errors:
        raise PromptPackError("; ".join(errors))
    variable_names = sorted(data["variables"])
    prompts = []
    for values in itertools.product(*(sorted(data["variables"][name]) for name in variable_names)):
        variables = dict(zip(variable_names, values))
        prompt = data["template"].format_map(variables)
        identity = {
            "pack_name": data["name"],
            "pack_version": data["version"],
            "variables": variables,
        }
        prompts.append({"id": stable_sha256(identity)[:16], "prompt": prompt, "variables": variables})
    manifest = {
        "schema_version": PROMPT_MANIFEST_SCHEMA_VERSION,
        "pack": {
            "name": data["name"],
            "version": data["version"],
            "fingerprint_sha256": stable_sha256(data),
        },
        "prompt_count": len(prompts),
        "prompts": prompts,
    }
    manifest["manifest_sha256"] = stable_sha256(manifest)
    return manifest
