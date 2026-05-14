"""Group Chat Pattern — moderated multi-agent discussion.

Multiple agents discuss in a shared chat room with turn-taking strategies.
A moderator agent selects speakers, synthesizes consensus, and ends the session.

Usage:
    group_chat(topic="Architecture decision for auth refactor",
               participants=["researcher-claude", "architect-claude", "security-claude"],
               strategy="moderator_selected", max_rounds=10)
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable

from .._log import _log
from ..agent_state import AGENTS, ManagedAgent, POOL
from ..agent_subprocess import compose_agent_prompt
from ..failure_classification import classified_error, VALIDATION_ERROR
from .refinement import _spawn_and_wait, _wait_for_agent


# ---------------------------------------------------------------------------
# Turn-taking strategies
# ---------------------------------------------------------------------------

ROUND_ROBIN = "round_robin"
MODERATOR_SELECTED = "moderator_selected"
VALID_STRATEGIES = {ROUND_ROBIN, MODERATOR_SELECTED}


def _pool_template(name: str):
    for tmpl in POOL.list_all():
        if tmpl.name.lower() == name.lower():
            return tmpl
    return None


def _resolve_agent_descriptor(value: Any, fallback_name: str) -> dict[str, str]:
    if isinstance(value, dict):
        name = str(value.get("name") or fallback_name)
        agent_type = str(value.get("agent_type") or "claude")
        role = str(value.get("role") or "participant")
        description = str(value.get("description") or f"Agent type: {agent_type}")
        return {"name": name, "agent_type": agent_type, "role": role, "description": description}

    hint = str(value or "claude")
    tmpl = _pool_template(hint)
    if tmpl:
        description_parts = [tmpl.description]
        if tmpl.identity:
            description_parts.append(tmpl.identity)
        if tmpl.capabilities:
            description_parts.append("Capabilities: " + ", ".join(tmpl.capabilities))
        return {
            "name": tmpl.name,
            "agent_type": tmpl.agent_type,
            "role": tmpl.role,
            "description": "\n".join(part for part in description_parts if part),
        }

    agent_type = hint if hint in ("claude", "codex") else "claude"
    return {
        "name": fallback_name if hint in ("claude", "codex") else hint,
        "agent_type": agent_type,
        "role": "participant",
        "description": f"Agent type: {agent_type}",
    }


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_MODERATOR_PROMPT = """\
You are moderating a group discussion.

## Topic
{topic}

## Participants
{participant_list}

## Your role
- Open the discussion by framing the topic clearly.
- After each participant speaks, select the next speaker by writing:
  NEXT_SPEAKER: <participant_name>
- Choose speakers based on who has the most relevant expertise for the current sub-topic.
- After {max_rounds} total speaking turns (or when consensus is reached), synthesize the discussion:
  1. Write SUMMARY: followed by a concise summary of key points and agreements.
  2. Write CONSENSUS: YES if all participants aligned, NO if disagreements remain.
  3. Write CONCLUSION: followed by the actionable outcome.

## Current discussion
{transcript}

## What just happened
{last_message}

Select the next speaker or synthesize if the discussion is complete.
"""

_PARTICIPANT_PROMPT = """\
You are {name}, participating in a group discussion.

## Topic
{topic}

## Your role and expertise
{role_description}

## Discussion so far
{transcript}

## Most recent message
{last_message}

## Instructions
- Share your analysis, opinion, or expertise on the current sub-topic.
- Reference specific points from other participants.
- Be concise (2-4 paragraphs max).
- If you agree with the consensus, say so explicitly.
"""

_SYNTHESIS_PROMPT = """\
You are the moderator. The discussion on "{topic}" has concluded after {rounds} rounds.

## Full transcript
{transcript}

## Instructions
Produce a final synthesis:
1. SUMMARY: Key points and arguments from each participant.
2. CONSENSUS: YES or NO — did participants reach agreement?
3. CONCLUSION: The actionable outcome or decision.
4. OPEN_QUESTIONS: Any unresolved items for follow-up.
"""


# ---------------------------------------------------------------------------
# Core group chat engine
# ---------------------------------------------------------------------------

async def tool_group_chat(
    args: dict[str, Any],
    on_turn: Callable[[list[dict[str, str]]], None] | None = None,
) -> dict[str, Any]:
    """Run a moderated multi-agent group chat discussion.

    Args:
        args: Tool arguments dict.
        on_turn: Optional callback fired after each turn with the full transcript so far.
            Used by the workflow executor to stream intermediate results.
    """
    topic = args.get("topic", "")
    participants = args.get("participants", [])
    moderator_type = args.get("moderator", "claude")
    strategy = args.get("strategy", MODERATOR_SELECTED)
    max_rounds = min(args.get("max_rounds", 8), 20)
    cwd = args.get("cwd", "/tmp")
    model = args.get("model")
    timeout = args.get("timeout", 120.0)

    if not topic:
        return classified_error("topic is required", code="missing_topic", category=VALIDATION_ERROR)
    if len(participants) < 2:
        return classified_error(
            "At least 2 participants required",
            code="insufficient_participants", category=VALIDATION_ERROR,
        )
    if strategy not in VALID_STRATEGIES:
        return classified_error(
            f"Invalid strategy: {strategy}. Use: {', '.join(VALID_STRATEGIES)}",
            code="invalid_strategy", category=VALIDATION_ERROR,
        )

    # Normalize participant names. Strings may be direct agent types
    # ("claude"/"codex") or pool template names selected in the dashboard.
    participant_descriptors = [
        _resolve_agent_descriptor(p, f"participant-{i+1}-{p}")
        for i, p in enumerate(participants)
    ]
    participant_names = [p["name"] for p in participant_descriptors]
    participant_types = [p["agent_type"] for p in participant_descriptors]
    participant_roles = [p["role"] for p in participant_descriptors]
    participant_descriptions = [p["description"] for p in participant_descriptors]

    moderator_descriptor = _resolve_agent_descriptor(moderator_type, "Moderator")
    moderator_type = moderator_descriptor["agent_type"]
    moderator_name = moderator_descriptor["name"]

    participant_list_str = "\n".join(
        f"- {name} ({ptype})" for name, ptype in zip(participant_names, participant_types)
    )

    transcript: list[dict[str, str]] = []
    rounds_completed = 0
    consensus = "unknown"
    summary = ""
    conclusion = ""

    _log(f"group_chat: starting '{topic}' with {len(participants)} participants, strategy={strategy}")

    # -- Run the discussion --
    speaker_order: list[int] = []

    for round_num in range(1, max_rounds + 1):
        rounds_completed = round_num
        transcript_text = _format_transcript(transcript)

        if strategy == ROUND_ROBIN:
            # Simple round-robin: cycle through participants
            speaker_idx = (round_num - 1) % len(participants)
        else:
            # Moderator-selected: ask moderator who speaks next
            if round_num == 1:
                # First round: moderator opens
                speaker_idx = -1  # moderator speaks
            else:
                speaker_idx = await _ask_moderator_for_next(
                    moderator_type, topic, participant_names, participant_list_str,
                    transcript_text, transcript[-1] if transcript else {},
                    max_rounds, cwd, model, timeout,
                    moderator_name, moderator_descriptor["role"], moderator_descriptor["description"],
                )
                if speaker_idx is None:
                    # Moderator signaled end of discussion
                    break

        if speaker_idx == -1:
            # Moderator opening statement
            last_msg = transcript[-1].get("content", "") if transcript else ""
            mod_prompt = _MODERATOR_PROMPT.format(
                topic=topic,
                participant_list=participant_list_str,
                max_rounds=max_rounds,
                transcript=transcript_text[:4000],
                last_message=last_msg[:2000],
            )
            mod_agent, mod_output = await _spawn_and_wait(
                moderator_type, f"moderator-round{round_num}", cwd,
                compose_agent_prompt(
                    moderator_name,
                    moderator_descriptor["role"],
                    moderator_descriptor["description"],
                    [],
                    mod_prompt,
                ),
                model=model, timeout=timeout,
            )
            transcript.append({"speaker": "Moderator", "content": mod_output[:3000], "round": round_num})
            if on_turn:
                on_turn(transcript)

            # Check if moderator is synthesizing (SUMMARY: present)
            if "SUMMARY:" in mod_output:
                summary, consensus, conclusion = _parse_synthesis(mod_output)
                break

            # Extract next speaker from moderator output
            next_name = _extract_next_speaker(mod_output, participant_names)
            if next_name:
                try:
                    speaker_idx = participant_names.index(next_name)
                except ValueError:
                    speaker_idx = 0
            else:
                speaker_idx = 0
        else:
            speaker_idx = max(0, min(speaker_idx, len(participants) - 1))

        speaker_order.append(speaker_idx)

        # Participant speaks
        name = participant_names[speaker_idx]
        ptype = participant_types[speaker_idx]
        prole = participant_roles[speaker_idx]
        pdesc = participant_descriptions[speaker_idx]
        last_msg = transcript[-1].get("content", "") if transcript else f"Discussion topic: {topic}"

        p_prompt = _PARTICIPANT_PROMPT.format(
            name=name,
            topic=topic,
            role_description=pdesc,
            transcript=transcript_text[:4000],
            last_message=last_msg[:2000],
        )
        p_agent, p_output = await _spawn_and_wait(
            ptype, f"{name}-round{round_num}", cwd,
            compose_agent_prompt(name, prole, pdesc, [], p_prompt),
            model=model, timeout=timeout,
        )
        transcript.append({"speaker": name, "content": p_output[:3000], "round": round_num})
        if on_turn:
            on_turn(transcript)

        _log(f"group_chat: round {round_num} — {name} spoke ({len(p_output)} chars)")

    # -- Final synthesis if not already done --
    if not summary:
        transcript_text = _format_transcript(transcript)
        synth_prompt = _SYNTHESIS_PROMPT.format(
            topic=topic,
            rounds=rounds_completed,
            transcript=transcript_text[:8000],
        )
        synth_agent, synth_output = await _spawn_and_wait(
            moderator_type, "moderator-synthesis", cwd,
            compose_agent_prompt(
                moderator_name,
                moderator_descriptor["role"],
                moderator_descriptor["description"],
                [],
                synth_prompt,
            ),
            model=model, timeout=timeout,
        )
        summary, consensus, conclusion = _parse_synthesis(synth_output)
        transcript.append({
            "speaker": "Moderator (synthesis)",
            "content": synth_output[:3000],
            "round": rounds_completed + 1,
        })
        if on_turn:
            on_turn(transcript)

    result: dict[str, Any] = {
        "topic": topic,
        "participants": participant_names,
        "strategy": strategy,
        "total_rounds": rounds_completed,
        "transcript": transcript,
        "summary": summary,
        "consensus": consensus,
        "conclusion": conclusion,
        "speaker_order": speaker_order,
    }

    _log(f"group_chat: completed — {rounds_completed} rounds, consensus={consensus}")
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_transcript(transcript: list[dict[str, str]]) -> str:
    """Format transcript for injection into prompts."""
    lines = []
    for entry in transcript:
        speaker = entry.get("speaker", "Unknown")
        content = entry.get("content", "")
        lines.append(f"**{speaker}:** {content[:1500]}")
    return "\n\n".join(lines)


def _extract_next_speaker(text: str, names: list[str]) -> str | None:
    """Extract NEXT_SPEAKER: <name> from moderator output."""
    import re
    match = re.search(r"NEXT_SPEAKER:\s*(.+)", text, re.IGNORECASE)
    if match:
        requested = match.group(1).strip()
        # Fuzzy match against participant names
        for name in names:
            if name.lower() in requested.lower() or requested.lower() in name.lower():
                return name
        # Try partial match
        for name in names:
            if any(word in requested.lower() for word in name.lower().split("-")):
                return name
    return None


def _parse_synthesis(text: str) -> tuple[str, str, str]:
    """Parse SUMMARY:, CONSENSUS:, CONCLUSION: from synthesis output."""
    import re
    summary = ""
    consensus = "unknown"
    conclusion = ""

    s_match = re.search(r"SUMMARY:\s*(.+?)(?=CONSENSUS:|CONCLUSION:|OPEN_QUESTIONS:|$)", text, re.DOTALL | re.IGNORECASE)
    if s_match:
        summary = s_match.group(1).strip()[:2000]

    c_match = re.search(r"CONSENSUS:\s*(YES|NO)", text, re.IGNORECASE)
    if c_match:
        consensus = c_match.group(1).upper()

    cl_match = re.search(r"CONCLUSION:\s*(.+?)(?=OPEN_QUESTIONS:|$)", text, re.DOTALL | re.IGNORECASE)
    if cl_match:
        conclusion = cl_match.group(1).strip()[:2000]

    return summary, consensus, conclusion


async def _ask_moderator_for_next(
    moderator_type: str,
    topic: str,
    names: list[str],
    participant_list: str,
    transcript: str,
    last_entry: dict,
    max_rounds: int,
    cwd: str,
    model: str | None,
    timeout: float,
    moderator_name: str = "moderator",
    moderator_role: str = "researcher",
    moderator_description: str = "",
) -> int | None:
    """Ask moderator to select next speaker. Returns index or None to end."""
    last_msg = last_entry.get("content", "") if last_entry else ""

    prompt = _MODERATOR_PROMPT.format(
        topic=topic,
        participant_list=participant_list,
        max_rounds=max_rounds,
        transcript=transcript[:4000],
        last_message=last_msg[:2000],
    )
    mod_agent, output = await _spawn_and_wait(
        moderator_type, "moderator-select", cwd,
        compose_agent_prompt(moderator_name, moderator_role, moderator_description, [], prompt),
        model=model, timeout=timeout,
    )

    # Check for synthesis signal
    if "SUMMARY:" in output:
        return None

    name = _extract_next_speaker(output, names)
    if name:
        try:
            return names.index(name)
        except ValueError:
            return 0
    return 0
