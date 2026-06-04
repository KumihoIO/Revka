"""A2A Agent Card builder — maps Revka agent templates to A2A AgentCard format.

Follows the Google A2A protocol specification (a2a-protocol.org).
Agent cards are JSON metadata documents that describe identity, capabilities,
skills, endpoints, and authentication requirements.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .._log import _log


# ---------------------------------------------------------------------------
# A2A data structures (lightweight, no external dependency)
# ---------------------------------------------------------------------------

def _build_skill(
    *,
    skill_id: str,
    name: str,
    description: str,
    tags: list[str] | None = None,
    examples: list[str] | None = None,
    input_modes: list[str] | None = None,
    output_modes: list[str] | None = None,
) -> dict[str, Any]:
    """Build an A2A skill object."""
    skill: dict[str, Any] = {
        "id": skill_id,
        "name": name,
        "description": description,
    }
    if tags:
        skill["tags"] = tags
    if examples:
        skill["examples"] = examples
    if input_modes:
        skill["inputModes"] = input_modes
    if output_modes:
        skill["outputModes"] = output_modes
    return skill


# ---------------------------------------------------------------------------
# Agent Card Builder
# ---------------------------------------------------------------------------

class AgentCardBuilder:
    """Builds A2A-compliant agent cards from Revka agent templates."""

    def __init__(self, *, base_url: str = "http://localhost:8000", version: str = "1.0.0"):
        self.base_url = base_url.rstrip("/")
        self.version = version

    def from_template(self, template: dict[str, Any]) -> dict[str, Any]:
        """Convert a Revka AgentTemplate dict to an A2A AgentCard.

        Args:
            template: Dict with keys: name, agent_type, role, capabilities,
                      description, identity, soul, tone, model, system_hint.

        Returns:
            A2A AgentCard dict.
        """
        name = template.get("name", "revka-agent")
        description = template.get("description", "")
        role = template.get("role", "coder")
        capabilities = template.get("capabilities", [])
        if isinstance(capabilities, str):
            capabilities = [c.strip() for c in capabilities.split(",") if c.strip()]

        # Build skill from template capabilities
        skill_tags = [role]
        if isinstance(capabilities, list):
            skill_tags.extend(str(c) for c in capabilities[:10])

        skill = _build_skill(
            skill_id=f"revka-{name}",
            name=name,
            description=description or f"Revka {role} agent",
            tags=skill_tags,
            examples=[f"Use this agent for {role} tasks"],
            input_modes=["text/plain"],
            output_modes=["text/plain", "application/json"],
        )

        card: dict[str, Any] = {
            "name": name,
            "description": description or f"Revka {role} agent: {name}",
            "url": f"{self.base_url}/a2a",
            "version": self.version,
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "capabilities": {
                "streaming": True,
                "pushNotifications": False,
            },
            "skills": [skill],
            "provider": {
                "organization": "Revka",
                "url": self.base_url,
            },
        }

        # Add identity/soul as provider metadata
        identity = template.get("identity") or template.get("soul")
        if identity:
            card["description"] = f"{description} — {identity}"

        return card

    def composite_card(self, templates: list[dict[str, Any]]) -> dict[str, Any]:
        """Build a composite A2A card with skills from all templates.

        This represents the Revka instance as a single A2A agent
        with multiple skills, one per template.
        """
        skills = []
        for tmpl in templates:
            name = tmpl.get("name", "unknown")
            role = tmpl.get("role", "coder")
            desc = tmpl.get("description", f"Revka {role}")
            caps = tmpl.get("capabilities", [])
            if isinstance(caps, str):
                caps = [c.strip() for c in caps.split(",") if c.strip()]

            tags = [role]
            if isinstance(caps, list):
                tags.extend(str(c) for c in caps[:5])

            skills.append(_build_skill(
                skill_id=f"revka-{name}",
                name=name,
                description=desc,
                tags=tags,
                input_modes=["text/plain"],
                output_modes=["text/plain", "application/json"],
            ))

        return {
            "name": "Revka Operator",
            "description": "Multi-agent orchestration platform with graph-native memory. "
                           "Supports code generation, review, research, testing, and architecture tasks.",
            "url": f"{self.base_url}/a2a",
            "version": self.version,
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "capabilities": {
                "streaming": True,
                "pushNotifications": False,
            },
            "skills": skills,
            "provider": {
                "organization": "Revka / KumihoIO",
                "url": self.base_url,
            },
        }
