"""Skill outcome tracking — closes the loop on agent self-improvement.

Step 3 of the self-improving agent plan. Whenever an agent uses a skill,
``record_skill_outcome`` should fire afterwards with ``success=True/False``
and an optional summary / error. Outcomes accumulate at::

    <memory_project>/Skills/<skill_slug>/Outcomes/

(``<memory_project>`` is the user-cognitive namespace, default
``CognitiveMemory``.)

``get_skill_effectiveness`` then computes a recency-weighted success rate
from the most recent outcomes, exposing it for the prompt builder
(``SkillsSection`` on the Rust side) to rerank skills before injection.
That is the substrate Hermes Agent calls "skill self-improvement during
use" — Revka gets the same loop without the SQLite + DSPy stack
because the outcomes already live in the Kumiho graph with edges back
to the skill they exercised.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .._log import _log
from ..revka_config import memory_project

try:
    from kumiho.mcp_server import (
        tool_get_revision_by_tag,
        tool_memory_store,
        tool_search_items,
    )

    _HAS_KUMIHO = True
except ImportError:
    _HAS_KUMIHO = False


def _unavailable() -> dict[str, Any]:
    return {
        "error": "kumiho package not available — install via `revka sidecars install`",
    }


def _resolve_skill_name(skill_name: str | None, skill_kref: str | None) -> str | None:
    """Pull the slug out of a kref if no explicit name is given.

    krefs look like::

        kref://CognitiveMemory/Skills/operator-orchestrator.skill?r=3

    (Legacy items use the ``.skilldef`` suffix instead — both forms are
    handled the same way because we split on ``.`` and take the prefix.)

    We want ``operator-orchestrator``.
    """
    if skill_name and skill_name.strip():
        return skill_name.strip()
    if not skill_kref:
        return None
    s = skill_kref.split("?", 1)[0]  # drop revision query
    s = s.rstrip("/")
    last = s.rsplit("/", 1)[-1]
    # last looks like "operator-orchestrator.skill" (or, for legacy
    # items, "operator-orchestrator.skilldef").
    return last.split(".", 1)[0] if "." in last else last


def _outcomes_space(skill_name: str) -> str:
    """Resolve the storage space for a skill's outcomes.

    Uses the configured ``memory_project`` so deployments that rebrand
    keep all skill outcomes under the right namespace.
    """
    safe = (skill_name or "unknown").replace("/", "-")
    return f"/{memory_project()}/Skills/{safe}/Outcomes"


def _resolve_to_concrete_revision_kref(skill_kref: str) -> str:
    """Resolve a tag-pointer kref (``?t=published``) to a concrete
    revision kref (``?r=N``) so per-revision outcome bucketing on the
    Rust side can attribute each outcome to a specific revision.

    Step 6f-C of the kumiho-versioned skill plan.  Without this the
    daemon's ``classify_outcomes_per_revision`` drops every outcome
    whose ``skill_kref`` metadata is a tag-pointer (because a tag
    points at "whichever revision is current right now" and that
    moves out from under us as new revisions land), and
    ``regression_candidates`` returns an empty list — the
    auto-rollback infrastructure is in place but never fires.

    Behaviour:

    - Krefs without ``?t=`` (no query, ``?r=N``, ``?as_of=…``) are
      returned unchanged — they're already concrete.
    - Krefs with ``?t=<tag>`` are resolved via Kumiho's
      ``get_revision_by_tag`` and the returned revision's kref is
      substituted.
    - On any resolver failure (Kumiho unreachable, tag not found,
      malformed response) we log + return the original kref.  This
      keeps ``record_skill_outcome`` resilient: a recording attempt
      never fails because the resolver hiccupped, even if that
      single outcome ends up unattributed.
    """
    if not skill_kref or "?t=" not in skill_kref:
        return skill_kref

    base, query = skill_kref.split("?", 1)
    tag = None
    for part in query.split("&"):
        if part.startswith("t="):
            tag = part[2:]
            break
    if not tag:
        return skill_kref

    try:
        result = tool_get_revision_by_tag(base, tag)
    except Exception as e:  # noqa: BLE001
        _log(f"resolve_skill_kref: kumiho call failed for {skill_kref!r}: {e}")
        return skill_kref

    if not isinstance(result, dict) or "error" in result:
        _log(
            "resolve_skill_kref: kumiho returned error for "
            f"{skill_kref!r}: {result!r}"
        )
        return skill_kref

    revision = result.get("revision", result)
    resolved = revision.get("kref") if isinstance(revision, dict) else None
    if not resolved:
        _log(
            "resolve_skill_kref: kumiho response missing revision.kref for "
            f"{skill_kref!r}: {result!r}"
        )
        return skill_kref

    return resolved


# ---------------------------------------------------------------------------
# record_skill_outcome
# ---------------------------------------------------------------------------


async def tool_record_skill_outcome_op(args: dict[str, Any]) -> dict[str, Any]:
    """Record a single skill use outcome.

    Required: either ``skill_name`` or ``skill_kref``, plus ``success`` (bool).

    Stores under ``<memory_project>/Skills/<skill>/Outcomes/`` with the
    skill's kref recorded as ``source_revision_krefs`` so the graph traces
    each outcome back to the exact skill revision that produced it.
    """
    if not _HAS_KUMIHO:
        return _unavailable()

    skill_name = _resolve_skill_name(args.get("skill_name"), args.get("skill_kref"))
    if not skill_name:
        return {"error": "skill_name or skill_kref is required"}

    success = bool(args.get("success", False))
    summary = (args.get("summary") or "").strip()
    error = (args.get("error") or "").strip()
    agent_id = args.get("agent_id", "")
    session_id = args.get("session_id", "")
    duration_ms = args.get("duration_ms")
    skill_kref_input = args.get("skill_kref", "")
    # Step 6f-C: resolve tag-pointer krefs (?t=published) to concrete
    # revision krefs so the daemon's per-revision regression detector
    # can attribute each outcome to a specific revision.  No-op when
    # the caller already passed a concrete kref or omitted skill_kref.
    skill_kref = _resolve_to_concrete_revision_kref(skill_kref_input)

    kind = "success" if success else "failure"
    when = datetime.now(timezone.utc).strftime("%b %d %H:%M UTC")
    # The title's leading marker survives slugification as the FIRST token of
    # the resulting item_name (`ok-…` or `fail-…`). _outcome_is_success uses
    # that prefix as a reliable classifier, since neither tool_search_items
    # nor tool_fulltext_search currently surface our custom revision tags.
    marker = "[OK]" if success else "[FAIL]"
    title = f"{marker} {skill_name} on {when}"
    # Prefer summary, fall back to error message, then to a marker so the
    # underlying tool_memory_store call (which requires user_text or
    # assistant_text) does not reject the request.
    body = summary or error or kind

    tags = ["skill-outcome", kind, f"skill:{skill_name}"]
    if agent_id:
        tags.append(f"agent:{agent_id}")
    if session_id:
        tags.append(f"session:{session_id}")

    metadata: dict[str, Any] = {
        "skill_name": skill_name,
        "success": "true" if success else "false",
        "kind": kind,
    }
    if agent_id:
        metadata["agent_id"] = agent_id
    if session_id:
        metadata["session_id"] = session_id
    if duration_ms is not None:
        metadata["duration_ms"] = str(duration_ms)
    if error:
        metadata["error"] = error
    if skill_kref:
        metadata["skill_kref"] = skill_kref
        # Preserve the original pointer the caller passed so audit
        # trails can tell whether this outcome was recorded against a
        # tag-pointer (legacy) or a concrete revision kref (post-6f-C).
        if skill_kref_input and skill_kref_input != skill_kref:
            metadata["skill_kref_input"] = skill_kref_input

    space_path = _outcomes_space(skill_name)

    try:
        result = await asyncio.to_thread(
            tool_memory_store,
            project=memory_project(),
            space_path=space_path,
            memory_type=kind,  # 'success' or 'failure'
            memory_item_kind="skill_outcome",
            title=title,
            summary=body,
            assistant_text=body,
            tags=tags,
            source_revision_krefs=[skill_kref] if skill_kref else [],
            metadata=metadata,
            edge_type="DERIVED_FROM",
            stack_revisions=False,  # outcomes are append-only
        )
    except Exception as e:  # noqa: BLE001
        _log(f"record_skill_outcome failed: {e}")
        return {"error": f"store failed: {e}"}

    if isinstance(result, dict) and "error" in result:
        return result

    kref = (
        result.get("revision_kref")
        or result.get("item_kref")
        or result.get("kref")
        if isinstance(result, dict)
        else None
    )

    return {
        "kref": kref,
        "skill_name": skill_name,
        "kind": kind,
        "success": success,
        "space_path": space_path,
    }


# ---------------------------------------------------------------------------
# get_skill_effectiveness
# ---------------------------------------------------------------------------


def _outcome_is_success(item: dict[str, Any]) -> bool | None:
    """Return True/False for a skill_outcome item, or None if undeterminable.

    Detection priority:
    1. ``item_name`` slug prefix — ``ok-…`` (success) or ``fail-…`` (failure).
       Set deterministically by record_skill_outcome via the ``[OK]`` /
       ``[FAIL]`` title marker. This is the primary classifier because
       Kumiho's item-level search response doesn't currently expose
       revision-level tags / memory_type.
    2. Revision metadata fallbacks for items that DO carry them (e.g.
       fetched via tool_get_revision rather than tool_search_items).
    """
    item_name = (item.get("item_name") or item.get("name") or "").lower()
    if item_name.startswith("ok-"):
        return True
    if item_name.startswith("fail-"):
        return False

    tags = item.get("tags") or []
    if "success" in tags:
        return True
    if "failure" in tags:
        return False
    mt = (item.get("memory_type") or "").lower()
    if mt == "success":
        return True
    if mt == "failure":
        return False
    meta = item.get("metadata") or {}
    s = str(meta.get("success", "")).lower()
    if s == "true":
        return True
    if s == "false":
        return False
    return None


async def tool_get_skill_effectiveness_op(args: dict[str, Any]) -> dict[str, Any]:
    """Compute rolling success rate for a skill from its recent outcomes.

    Required: ``skill_name`` or ``skill_kref``.
    Optional: ``limit`` (default 50, max 500), ``window_days`` (only count
    outcomes newer than this; default unlimited).

    Returns::

        {
          skill_name, total, successes, failures,
          rate,                 # successes / total, or None if total == 0
          recent: [...],        # newest-first, capped at 10
          space_path
        }
    """
    if not _HAS_KUMIHO:
        return _unavailable()

    skill_name = _resolve_skill_name(args.get("skill_name"), args.get("skill_kref"))
    if not skill_name:
        return {"error": "skill_name or skill_kref is required"}

    raw_limit = args.get("limit", 50)
    try:
        limit = max(1, min(int(raw_limit), 500))
    except (TypeError, ValueError):
        limit = 50

    space_path = _outcomes_space(skill_name)
    context = space_path.lstrip("/")

    try:
        raw = await asyncio.to_thread(
            tool_search_items,
            context_filter=context,
            kind_filter="skill_outcome",
            include_metadata=True,
        )
    except Exception as e:  # noqa: BLE001
        _log(f"get_skill_effectiveness failed: {e}")
        return {"error": f"search failed: {e}"}

    items = []
    if isinstance(raw, dict):
        items = raw.get("items") or []

    # Sort newest first by created_at when present.
    try:
        items.sort(key=lambda it: it.get("created_at") or "", reverse=True)
    except Exception:  # noqa: BLE001
        pass
    items = items[:limit]

    successes = 0
    failures = 0
    undetermined = 0
    for it in items:
        match _outcome_is_success(it):
            case True:
                successes += 1
            case False:
                failures += 1
            case _:
                undetermined += 1

    total = successes + failures
    rate = (successes / total) if total > 0 else None

    return {
        "skill_name": skill_name,
        "total": total,
        "successes": successes,
        "failures": failures,
        "undetermined": undetermined,
        "rate": rate,
        "recent": items[:10],
        "space_path": space_path,
    }
