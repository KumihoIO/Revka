"""A2A Agent Registry — tracks published agent cards and discovered external agents."""
from __future__ import annotations

from typing import Any

from .._log import _log
from .a2a_card import AgentCardBuilder


class A2ARegistry:
    """Registry of A2A agent cards — both local (Revka templates) and external."""

    def __init__(self, *, base_url: str = "http://localhost:8000"):
        self._local_cards: dict[str, dict[str, Any]] = {}
        self._external_cards: dict[str, dict[str, Any]] = {}
        self._builder = AgentCardBuilder(base_url=base_url)

    # -- Local template registration --

    def register_template(self, template: dict[str, Any]) -> None:
        """Register a local Revka template as an A2A agent."""
        name = template.get("name", "")
        if not name:
            return
        card = self._builder.from_template(template)
        self._local_cards[name] = card
        _log(f"a2a_registry: registered local agent '{name}'")

    def unregister(self, template_name: str) -> None:
        """Remove a local template from the registry."""
        self._local_cards.pop(template_name, None)
        _log(f"a2a_registry: unregistered '{template_name}'")

    def get_card(self, template_name: str) -> dict[str, Any] | None:
        """Get A2A card for a specific template."""
        return self._local_cards.get(template_name)

    def get_composite_card(self) -> dict[str, Any]:
        """Get composite A2A card representing all local templates."""
        # Rebuild from current POOL state
        from ..agent_state import POOL
        templates = [
            {
                "name": t.name,
                "agent_type": t.agent_type,
                "role": t.role,
                "capabilities": t.capabilities,
                "description": t.description,
                "identity": t.identity,
            }
            for t in POOL.list_all()
        ]
        return self._builder.composite_card(templates)

    # -- External agent discovery --

    def register_external(self, url: str, card: dict[str, Any]) -> None:
        """Cache a discovered external A2A agent card."""
        self._external_cards[url] = card
        _log(f"a2a_registry: discovered external agent at '{url}': {card.get('name', '?')}")

    def get_external(self, url: str) -> dict[str, Any] | None:
        """Get cached external agent card."""
        return self._external_cards.get(url)

    def list_external(self) -> list[dict[str, Any]]:
        """List all discovered external agents."""
        result = []
        for url, card in self._external_cards.items():
            result.append({
                "url": url,
                "name": card.get("name", "unknown"),
                "description": card.get("description", ""),
                "skills": [s.get("name", "") for s in card.get("skills", [])],
                "capabilities": card.get("capabilities", {}),
            })
        return result

    # -- Unified search (local + external) --

    def search(self, query: str) -> list[dict[str, Any]]:
        """Search both local and external agents by query string."""
        query_lower = query.lower()
        matches = []

        for name, card in self._local_cards.items():
            if self._matches(card, query_lower):
                matches.append({**card, "source": "local", "template_name": name})

        for url, card in self._external_cards.items():
            if self._matches(card, query_lower):
                matches.append({**card, "source": "a2a", "url": url})

        return matches

    @staticmethod
    def _matches(card: dict[str, Any], query: str) -> bool:
        """Check if a card matches a search query."""
        searchable = " ".join([
            card.get("name", ""),
            card.get("description", ""),
            " ".join(
                s.get("name", "") + " " + s.get("description", "") + " " + " ".join(s.get("tags", []))
                for s in card.get("skills", [])
            ),
        ]).lower()
        return all(term in searchable for term in query.split())


# -- Module-level singleton --
_registry: A2ARegistry | None = None


def get_registry(base_url: str = "http://localhost:8000") -> A2ARegistry:
    """Get or create the global A2A registry."""
    global _registry
    if _registry is None:
        _registry = A2ARegistry(base_url=base_url)
    return _registry
