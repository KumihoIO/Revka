"""Skill loader — reads orchestration skills from ~/.revka/skills/.

Skills are markdown instruction files that get injected into agent system
prompts based on the task context. The operator selects which skills to
load based on the orchestration pattern being used.
"""
from __future__ import annotations

import os
from typing import Any

from ._log import _log

_SKILLS_DIRS = [
    os.path.expanduser("~/.revka/skills"),
    os.path.expanduser("~/.revka/workspace/skills"),
]

# Skill name → filename mapping
_SKILL_FILES = {
    "operator-orchestrator": "operator-orchestrator.md",
    "operator-loop": "operator-loop.md",
    "operator-committee": "operator-committee.md",
    "operator-handoff": "operator-handoff.md",
    "operator-chat": "operator-chat.md",
    "kumiho-sdk": "kumiho-sdk.md",
    "construct-workflows": "construct-workflows.md",
}

_skill_cache: dict[str, str] = {}


def _find_skill_file(name: str) -> str | None:
    """Search skill directories for a matching file."""
    filename = _SKILL_FILES.get(name)
    candidates = [filename] if filename else [f"{name}.md"]
    for d in _SKILLS_DIRS:
        for c in candidates:
            path = os.path.join(d, c)
            if os.path.exists(path):
                return path
    return None


def load_skill(name: str) -> str | None:
    """Load a skill by name. Returns the markdown content or None."""
    if name in _skill_cache:
        return _skill_cache[name]

    path = _find_skill_file(name)
    if not path:
        return None

    try:
        with open(path, "r") as f:
            content = f.read()
        _skill_cache[name] = content
        return content
    except Exception as e:
        _log(f"Error loading skill {name}: {e}")
        return None


def list_skills() -> list[dict[str, Any]]:
    """List all available skills."""
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()

    for skills_dir in _SKILLS_DIRS:
        if not os.path.isdir(skills_dir):
            continue
        for filename in sorted(os.listdir(skills_dir)):
            if not filename.endswith(".md"):
                continue
            name = filename[:-3]  # strip .md
            if name in seen:
                continue
            seen.add(name)
            path = os.path.join(skills_dir, filename)
            title = name
            try:
                with open(path, "r") as f:
                    first_line = f.readline().strip()
                    if first_line.startswith("# "):
                        title = first_line[2:]
            except Exception:
                pass

            skills.append({
                "name": name,
                "title": title,
                "path": path,
            })

    return skills


def load_skills_for_pattern(pattern: str) -> str:
    """Load the appropriate skills for an orchestration pattern.

    Returns combined skill content as a single string for system prompt injection.
    """
    skill_names: list[str] = []

    if pattern == "team":
        skill_names = ["operator-orchestrator", "operator-chat"]
    elif pattern == "loop":
        skill_names = ["operator-loop", "operator-chat"]
    elif pattern == "committee":
        skill_names = ["operator-committee"]
    elif pattern == "handoff":
        skill_names = ["operator-handoff"]
    elif pattern == "chat":
        skill_names = ["operator-chat"]
    elif pattern == "kumiho":
        skill_names = ["kumiho-sdk"]
    else:
        # Load all for general orchestration
        skill_names = list(_SKILL_FILES.keys())

    parts: list[str] = []
    for name in skill_names:
        content = load_skill(name)
        if content:
            parts.append(content)

    return "\n\n---\n\n".join(parts)
