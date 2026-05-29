"""Shared helpers for workflow agent structured output contracts."""
from __future__ import annotations

import re
from typing import Any


OUTPUT_FIELD_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

RESERVED_OUTPUT_DATA_FIELDS = {
    "template_name",
    "agent_type",
    "role",
    "managed_agent_id",
    "sidecar_id",
    "artifact_path",
    "exit_code",
    "stderr_tail",
    "skills",
    "structured_output_missing",
}


def is_valid_output_field_name(field: str) -> bool:
    """Return true when an output field is addressable as output_data.<field>."""
    return bool(OUTPUT_FIELD_NAME_RE.fullmatch(field))


def structured_output_instruction(fields: list[str]) -> str:
    """Return the prompt suffix for required agent structured output fields."""
    if not fields:
        return ""

    lines = [
        "",
        "STRUCTURED OUTPUT REQUIRED",
        "",
        "At the end of your response, include a final YAML block exactly like this:",
        "",
        "FINAL_OUTPUT:",
    ]
    for field in fields:
        lines.append(f"  {field}: <value>")
    lines.extend([
        "",
        "Rules:",
        "- Include every required field.",
        "- Use valid YAML scalar, list, or object values.",
        "- Do not put any text after FINAL_OUTPUT.",
    ])
    return "\n".join(lines)


def missing_required_output_fields(
    required_fields: list[str],
    output_data: dict[str, Any],
) -> list[str]:
    """Return required structured output fields absent from output_data."""
    return [field for field in required_fields if field not in output_data]
