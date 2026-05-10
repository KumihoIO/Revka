"""Workflow executor — runs a validated WorkflowDef step by step.

Handles:
  - Topological ordering via depends_on
  - Variable interpolation: ${step_id.output}, ${inputs.field}, ${loop.iteration}
  - Parallel step execution with join strategies
  - Goto loops with iteration guards
  - Checkpoint persistence (save/resume)
  - Per-step retry with delay
  - Human approval pauses
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .._log import _log
from ..agent_subprocess import compose_agent_prompt
from ..failure_classification import classified_error, VALIDATION_ERROR
from .auth_resolver import AuthResolveError, resolve_auth_profile
from .schema import (
    JoinStrategy,
    StepDef,
    StepResult,
    StepType,
    WorkflowDef,
    WorkflowState,
    WorkflowStatus,
    AgentStepConfig,
    QualityCheckConfig,
    ShellStepConfig,
    PythonStepConfig,
    EmailStepConfig,
    ImageStepConfig,
    A2AStepConfig,
    GotoStepConfig,
    OutputStepConfig,
    MapReduceStepConfig,
    SupervisorStepConfig,
    GroupChatStepConfig,
    HandoffStepConfig,
    ResolveStepConfig,
    ForEachStepConfig,
    TagStepConfig,
    DeprecateStepConfig,
)
from .validator import validate_workflow

# ---------------------------------------------------------------------------
# Variable interpolation
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\$\{([^}]+)\}")


def interpolate(template: str, state: WorkflowState) -> str:
    """Replace ${...} references with values from workflow state.

    Supported namespaces:
      - ${inputs.field}          — workflow input parameters
      - ${trigger.entity_kref}   — trigger context (event-driven launches)
      - ${trigger.entity_name}   — trigger context fields
      - ${step_id.output}        — step's text output
      - ${step_id.status}        — step's status string
      - ${step_id.output_data.k} — step's structured output field
      - ${step_id.files}         — comma-separated files list
      - ${loop.iteration}        — current goto iteration count
      - ${for_each.<variable>}   — current for_each iteration value
      - ${for_each.index}        — zero-based iteration index
      - ${for_each.iteration}    — one-based iteration number
      - ${for_each.total}        — total iteration count
      - ${previous.<step>.output} — previous iteration step output
      - ${rejection.feedback}     — human reviewer's feedback on rejection
      - ${rejection.count}       — number of rejection loops so far
      - ${env.VAR}               — environment variable
      - ${run_id}                — workflow run ID
    """
    def _resolve(match: re.Match) -> str:
        ref = match.group(1)
        parts = ref.split(".", 1)
        ns = parts[0]
        field = parts[1] if len(parts) > 1 else ""

        if ns == "inputs":
            return str(state.inputs.get(field, ""))
        if ns == "trigger":
            return state.trigger_context.get(field, "")
        if ns == "loop":
            if field == "iteration":
                return str(max(state.iteration_counts.values(), default=0))
            return ""
        if ns == "for_each":
            # for_each context is injected into inputs by _exec_for_each
            fe_ctx = state.inputs.get("__for_each__", {})
            if field in fe_ctx:
                return str(fe_ctx[field])
            return ""
        if ns == "previous":
            # Previous iteration step results: ${previous.step_id.output}
            prev_map = state.inputs.get("__previous__", {})
            prev_parts = field.split(".", 1)
            prev_step = prev_parts[0]
            prev_field = prev_parts[1] if len(prev_parts) > 1 else "output"
            sr_prev = prev_map.get(prev_step)
            if isinstance(sr_prev, dict):
                if prev_field == "output":
                    return str(sr_prev.get("output", ""))
                if prev_field.startswith("output_data."):
                    key = prev_field[len("output_data."):]
                    return str(sr_prev.get("output_data", {}).get(key, ""))
                return str(sr_prev.get(prev_field, ""))
            return ""
        if ns == "rejection":
            # Revision loop context: ${rejection.feedback}, ${rejection.count}
            if field == "feedback":
                return str(state.inputs.get("__rejection_feedback__", ""))
            if field == "count":
                return str(state.inputs.get("__rejection_count__", 0))
            return ""
        if ns == "env":
            return os.environ.get(field, "")
        if ns == "run_id":
            return state.run_id

        # Step reference
        sr = state.step_results.get(ns)
        if not sr:
            return match.group(0)  # Leave unresolved

        if not field or field == "output":
            return sr.output
        if field == "status":
            return sr.status
        if field == "error":
            return sr.error
        if field == "files":
            return ", ".join(sr.files_touched)
        if field.startswith("output_data."):
            key = field[len("output_data."):]
            # Defense in depth: refuse dunder / leading-underscore lookups
            # so agent-supplied JSON keys like `__class__` (or stray
            # internal sentinels) can't be exfiltrated via interpolation.
            if key.startswith("_"):
                return match.group(0)
            return str(sr.output_data.get(key, ""))
        if field == "agent_id":
            return sr.agent_id or ""

        return match.group(0)

    return _VAR_RE.sub(_resolve, template)


# ---------------------------------------------------------------------------
# Checkpoint persistence
# ---------------------------------------------------------------------------

_CHECKPOINT_DIR = os.path.expanduser("~/.construct/workflow_checkpoints")


def _save_checkpoint(state: WorkflowState) -> str:
    """Save workflow state to disk. Returns checkpoint path."""
    os.makedirs(_CHECKPOINT_DIR, exist_ok=True)
    path = os.path.join(_CHECKPOINT_DIR, f"{state.run_id}.json")
    with open(path, "w") as f:
        json.dump(state.model_dump(), f, indent=2, default=str)
    state.checkpoint_path = path
    return path


def load_checkpoint(run_id: str) -> WorkflowState | None:
    """Load a checkpoint from disk."""
    path = os.path.join(_CHECKPOINT_DIR, f"{run_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r") as f:
        data = json.load(f)
    return WorkflowState(**data)


def _cleanup_checkpoint(run_id: str) -> None:
    """Remove a checkpoint file."""
    path = os.path.join(_CHECKPOINT_DIR, f"{run_id}.json")
    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------
#
# Backed by simpleeval — a sandboxed AST-walking evaluator that's safe by
# default (no imports, no dunder access, configurable function/operator
# allowlist). The wrapper below:
#
#   1. Translates workflow-language operators (`&&`, `||`, `?:`, `contains`)
#      to Python equivalents simpleeval understands natively.
#   2. Builds a `names` dict mirroring `${X.field}` interpolation so step
#      results, inputs, trigger context, etc. are accessible as bare
#      identifiers in the expression (e.g. `review.status == 'approved'`).
#   3. Falls back to `interpolate()` for any leftover `${...}` references —
#      preserves the legacy form for users who still write
#      `${review.status} == 'approved'`.
#   4. Treats parse / name-resolution failures as `False` and logs a warning
#      so a misspelled identifier doesn't crash the workflow.

# Identifier used by the contains-RHS bare-word quoting heuristic.
_BARE_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Word boundary `contains` for the token scanner.
_CONTAINS_RE = re.compile(r"\bcontains\b")


def _translate_outside_strings(expr: str, replacements: list[tuple[str, str]]) -> str:
    """Apply literal substring replacements only to regions of ``expr``
    that are NOT inside a quoted string literal.

    Walks the expression character-by-character tracking ``'`` / ``"``
    string state with backslash-escape handling. ``replacements`` is a list
    of ``(needle, replacement)`` tuples applied in order at each position.
    Naïve longest-match: each tuple is tried in the order given, so put
    longer needles first if they share a prefix.
    """
    out: list[str] = []
    i = 0
    n = len(expr)
    quote: str | None = None  # current open quote char, or None
    while i < n:
        ch = expr[i]
        if quote is not None:
            # Inside a string literal — copy verbatim, honoring backslash escapes
            # so an escaped quote (e.g. `\'`) doesn't close the literal.
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(expr[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        # Outside string — check for a quote opening first.
        if ch == "'" or ch == '"':
            quote = ch
            out.append(ch)
            i += 1
            continue
        # Try each replacement at this position.
        matched = False
        for needle, repl in replacements:
            if expr.startswith(needle, i):
                out.append(repl)
                i += len(needle)
                matched = True
                break
        if not matched:
            out.append(ch)
            i += 1
    return "".join(out)


def _split_top_level(expr: str, sep: str) -> tuple[str, str] | None:
    """Find the first ``sep`` occurrence outside strings/parens/brackets.

    Returns ``(lhs, rhs)`` with the separator stripped, or None if not
    found. Used for ternary splitting where we need the top-level ``?``
    and ``:`` not nested inside parens or string literals.
    """
    n = len(expr)
    i = 0
    quote: str | None = None
    depth = 0
    sep_len = len(sep)
    while i < n:
        ch = expr[i]
        if quote is not None:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "'" or ch == '"':
            quote = ch
            i += 1
            continue
        if ch in "([{":
            depth += 1
            i += 1
            continue
        if ch in ")]}":
            depth -= 1
            i += 1
            continue
        if depth == 0 and expr.startswith(sep, i):
            return expr[:i], expr[i + sep_len:]
        i += 1
    return None


def _preprocess_expr(expr: str) -> str:
    """Translate workflow-language operators to Python operators.

    `&&` → ` and `, `||` → ` or `, leading `!` → ` not `, ternary `? :` →
    `if/else`. Idempotent for plain Python: a clean Python expression
    survives the rewrite untouched (Python doesn't use `&&`/`||`/`?:`).
    `contains` is handled via simpleeval's `in` operator at the names-dict
    layer (so ``a contains b`` is rewritten to ``b in a`` here).

    String literals are preserved verbatim — replacements happen only
    outside quoted regions, so ``x == 'foo&&bar'`` keeps its literal.
    """
    # NOTE: `!` must be tried only when not followed by `=`. The token
    # scanner only does literal matches, so we handle `!=` by putting it
    # first as an identity replacement (consumes both chars before `!`
    # alone gets a chance to fire).
    replacements: list[tuple[str, str]] = [
        ("&&", " and "),
        ("||", " or "),
        ("!=", "!="),       # identity — consume so the next rule skips it
        ("!", " not "),
        # `contains` handled separately below (needs word-boundary check).
    ]
    out = _translate_outside_strings(expr, replacements)

    # ``a contains b`` → ``b in a`` (workflow-language sugar). Word-boundary
    # match so ``contains_x`` identifiers aren't touched, and skipping any
    # match inside a string literal. Method calls like ``foo.contains(bar)``
    # still hit the regex but we guard by checking the char immediately
    # before the match isn't ``.`` (attribute access).
    out = _rewrite_contains_outside_strings(out)

    # Ternary: ``cond ? a : b`` → ``(a) if (cond) else (b)``. Use top-level
    # split to avoid matching `?`/`:` inside strings or parens.
    q_split = _split_top_level(out, "?")
    if q_split is not None:
        cond, rest = q_split
        c_split = _split_top_level(rest, ":")
        if c_split is not None:
            a, b = c_split
            cond_s, a_s, b_s = cond.strip(), a.strip(), b.strip()
            # Avoid matching nested ternaries (the second `?`); single-level.
            if cond_s and a_s and b_s and "?" not in a_s:
                out = f"({a_s}) if ({cond_s}) else ({b_s})"

    return out


def _rewrite_contains_outside_strings(expr: str) -> str:
    """Rewrite ``LHS contains RHS`` → ``(RHS) in (LHS)`` when ``contains``
    appears outside string literals AND isn't an attribute/method
    (i.e. preceded by ``.``). ``foo.contains(bar)`` is left intact.

    Splits on the first qualifying ``contains`` only — matches the prior
    behavior. Bare-word RHS gets quoted to mimic the pre-simpleeval
    evaluator's quote-stripping comparison.
    """
    n = len(expr)
    i = 0
    quote: str | None = None
    while i < n:
        ch = expr[i]
        if quote is not None:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "'" or ch == '"':
            quote = ch
            i += 1
            continue
        m = _CONTAINS_RE.match(expr, i)
        if m:
            # Reject method-call form: previous non-space char is `.`
            j = i - 1
            while j >= 0 and expr[j].isspace():
                j -= 1
            if j >= 0 and expr[j] == ".":
                i = m.end()
                continue
            lhs = expr[:i].strip()
            rhs = expr[m.end():].strip()
            if _BARE_IDENT_RE.fullmatch(rhs):
                rhs = repr(rhs)
            return f"({rhs}) in ({lhs})"
        i += 1
    return expr


def _safe_keys(d: dict[str, Any]) -> dict[str, Any]:
    """Strip dunder / leading-underscore keys from a dict.

    Defense in depth: even though simpleeval's DISALLOW_PREFIXES blocks
    dunder *attribute* access, top-level name lookup and dict-key lookup
    are not filtered. An agent JSON output containing ``__class__`` would
    otherwise be reachable as ``step.output_data.__class__`` (via
    EvalWithCompoundTypes' subscript access). We exclude any key that's
    not a valid bare identifier without leading underscore.
    """
    return {k: v for k, v in d.items() if isinstance(k, str) and k and not k.startswith("_")}


def _build_eval_names(state: WorkflowState) -> dict[str, Any]:
    """Build a `names` dict for simpleeval mirroring interpolation namespaces.

    EvalWithCompoundTypes resolves dotted access on dicts via attribute
    syntax — `review.status` looks up `names['review']['status']`. This
    means users can write expressions naturally without `${...}` syntax.

    All sub-dicts are passed through ``_safe_keys`` so dunder/private
    keys never become accessible via the evaluator.
    """
    names: dict[str, Any] = {
        "inputs": _safe_keys(state.inputs),
        "trigger": _safe_keys(state.trigger_context),
        "run_id": state.run_id,
    }

    # Step results — flatten so `step.output`, `step.status`, and
    # `step.output_data.field` all work via dotted access.
    for sid, sr in state.step_results.items():
        if sid.startswith("_"):
            continue
        names[sid] = {
            "output": sr.output,
            "status": sr.status,
            "error": sr.error,
            "files": list(sr.files_touched),
            "output_data": _safe_keys(sr.output_data),
            "agent_id": sr.agent_id or "",
        }

    # Loop / for_each / previous / rejection scopes — mirrors interpolate()
    fe_ctx = state.inputs.get("__for_each__")
    if isinstance(fe_ctx, dict):
        names["for_each"] = _safe_keys(fe_ctx)
    prev_map = state.inputs.get("__previous__")
    if isinstance(prev_map, dict):
        names["previous"] = _safe_keys(prev_map)
    names["rejection"] = {
        "feedback": state.inputs.get("__rejection_feedback__", ""),
        "count": state.inputs.get("__rejection_count__", 0),
    }
    names["loop"] = {
        "iteration": max(state.iteration_counts.values(), default=0),
    }
    # `env` intentionally NOT filtered — env vars commonly contain underscores
    # but never dunders, and existing workflows reference ${env.VAR} freely.
    names["env"] = dict(os.environ)
    return names


def _interpolate_for_expr(expr: str, state: WorkflowState) -> str:
    """Like `interpolate` but quotes string substitutions for safe injection
    into an expression context.

    Legacy workflows wrote ``${review.status} == 'completed'`` expecting the
    interpolator to spit out ``completed == 'completed'``. With simpleeval
    that bare ``completed`` would be a NameNotDefined. Wrap each substituted
    value: numeric / bool literals stay bare, everything else gets repr'd
    (single-quoted, with embedded quotes escaped).
    """
    def _quote(value: str) -> str:
        # Numeric literal? Leave bare so arithmetic comparisons work.
        try:
            float(value)
            return value
        except (ValueError, TypeError):
            pass
        if value in ("True", "False", "None"):
            return value
        # String literal — repr produces a Python-safe single-quoted form
        # that simpleeval parses cleanly even when the value contains quotes.
        return repr(value)

    def _sub(match: re.Match) -> str:
        # Reuse interpolate() to resolve a single ${...} expression by
        # passing it through with no surrounding text.
        resolved = interpolate(match.group(0), state)
        # If interpolate returned the placeholder unchanged (unresolved
        # reference), don't quote it — let simpleeval surface the error.
        if resolved == match.group(0):
            return resolved
        return _quote(resolved)

    return _VAR_RE.sub(_sub, expr)


def _eval_expression(expr: str, state: WorkflowState) -> Any:
    """Evaluate an expression and return the raw result.

    Pipeline: preprocess workflow-syntax sugar → interpolate any leftover
    ${...} (legacy form, with auto-quoting) → simpleeval. Used by both
    `_eval_condition` and branch-value evaluation.
    """
    from simpleeval import (
        EvalWithCompoundTypes,
        FeatureNotAvailable,
        FunctionNotDefined,
        InvalidExpression,
        NameNotDefined,
    )

    pre = _preprocess_expr(expr)
    # Resolve any remaining ${...} via the legacy interpolator. New-style
    # expressions don't need this; we keep it for backward compat with
    # workflows that wrote `${review.status} == 'approved'`.
    if "${" in pre:
        pre = _interpolate_for_expr(pre, state)

    names = _build_eval_names(state)
    # Whitelisted safe functions for workflow expressions. Kept tiny on
    # purpose — these handle the 99% case (case-insensitive matching,
    # length checks, type coercion) without exposing arbitrary Python.
    safe_functions: dict[str, Any] = {
        "lower": lambda s: str(s).lower() if s is not None else "",
        "upper": lambda s: str(s).upper() if s is not None else "",
        "len": len,
        "str": str,
        "int": int,
        "float": float,
        "bool": bool,
    }
    evaluator = EvalWithCompoundTypes(names=names, functions=safe_functions)
    # Cap exponentiation hard. simpleeval's module-level safe_power defaults
    # to 4_000_000 which lets `2**3999999` chew CPU/RAM. Workflow conditionals
    # never need huge exponents — 1000 is generous. We override the Pow
    # operator on the evaluator instance so simpleeval.MAX_POWER stays
    # untouched (other code paths importing simpleeval are unaffected).
    import ast as _ast

    def _bounded_power(a: Any, b: Any, _cap: int = 1000) -> Any:
        if abs(a) > _cap or abs(b) > _cap:
            raise InvalidExpression(
                f"exponent {a}**{b} exceeds workflow safety cap ({_cap})"
            )
        return a ** b

    evaluator.operators[_ast.Pow] = _bounded_power
    try:
        return evaluator.eval(pre)
    except (
        NameNotDefined,
        InvalidExpression,
        FunctionNotDefined,
        FeatureNotAvailable,
        SyntaxError,
        TypeError,
        ValueError,
        AttributeError,
        KeyError,
        IndexError,
    ) as exc:
        _log(f"workflow: expression eval failed ({type(exc).__name__}): "
             f"{expr!r} → {pre!r}: {exc}")
        raise


def _eval_condition(expr: str, state: WorkflowState) -> bool:
    """Evaluate a branch condition. Returns False on any failure (logged)."""
    if not expr or expr.strip().lower() == "default":
        return True
    try:
        return bool(_eval_expression(expr, state))
    except Exception:
        # _eval_expression already logged; swallow so the executor moves on
        # to the next branch (typically a `default` fallback).
        return False


def _eval_branch_value(expr: str | None, state: WorkflowState) -> str:
    """Evaluate a branch's `value` field, returning a string. Empty on
    missing/failure (logged)."""
    if not expr:
        return ""
    try:
        result = _eval_expression(expr, state)
    except Exception:
        return ""
    if result is None:
        return ""
    if isinstance(result, bool):
        return "true" if result else "false"
    return str(result)


# ---------------------------------------------------------------------------
# Skill pre-resolution
# ---------------------------------------------------------------------------

async def _resolve_skills_inline(skill_refs: list[str]) -> str:
    """Pre-resolve skill krefs into inline content.

    Fetches skill content from Kumiho or local files so agents get the full
    text in their prompt instead of opaque kref URIs they'd waste turns
    trying to fetch via MCP tools.
    """
    parts: list[str] = []
    for ref in skill_refs:
        content = None
        if ref.startswith("kref://"):
            # Try Kumiho resolve — returns file path on disk
            try:
                from ..operator_mcp import KUMIHO_SDK
                if KUMIHO_SDK._available:
                    file_path = await KUMIHO_SDK.resolve_kref(ref)
                    if file_path and os.path.exists(file_path):
                        with open(file_path, "r") as f:
                            content = f.read()
            except Exception as e:
                _log(f"skill pre-resolve failed for {ref}: {e}")
        if not content:
            # Fallback: try as local skill name
            from ..skill_loader import load_skill
            # Strip both the canonical `.skill` suffix and the legacy
            # `.skilldef` suffix so refs created before the kind rename
            # still resolve to a local skill name.
            name = (
                ref.rsplit("/", 1)[-1]
                .replace(".skilldef", "")
                .replace(".skill", "")
                .replace(".md", "")
            )
            content = load_skill(name)
        if content:
            # Truncate very large skills to save tokens
            if len(content) > 8000:
                content = content[:8000] + "\n\n[... truncated for token efficiency ...]"
            parts.append(content)
        else:
            _log(f"skill pre-resolve: could not resolve {ref}, skipping")
    if parts:
        return "\n## Reference Skills\n\n" + "\n\n---\n\n".join(parts)
    return ""


# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

async def _quality_score(
    output: str,
    criteria: list[str],
    step_id: str,
    model: str = "claude-haiku-4-5-20251001",
    prompt_hint: str = "",
) -> tuple[float, str]:
    """Score agent output quality using a lightweight model.

    Returns (score: 0.0-1.0, feedback: str).
    """
    criteria_text = "\n".join(f"- {c}" for c in criteria) if criteria else "- Output is substantive and on-topic\n- Output meets the requested format\n- Output is not generic filler"

    scoring_prompt = f"""Score the following agent output on a scale of 0.0 to 1.0.

Criteria to evaluate:
{criteria_text}

Original task hint (first 500 chars of prompt):
{prompt_hint}

Agent output to score:
{output[:8000]}

Respond with ONLY a JSON object:
{{"score": 0.85, "feedback": "Brief explanation of score"}}"""

    try:
        import anthropic
        client = anthropic.AsyncAnthropic()
        response = await client.messages.create(
            model=model,
            max_tokens=200,
            messages=[{"role": "user", "content": scoring_prompt}],
        )
        text = response.content[0].text.strip()

        # Strip markdown fences if present (LLMs sometimes wrap JSON)
        import json as _json
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.startswith("```")]
            text = "\n".join(lines).strip()

        parsed = _json.loads(text)
        score = float(parsed.get("score", 0.5))
        feedback = str(parsed.get("feedback", ""))
        return (max(0.0, min(1.0, score)), feedback)
    except Exception as exc:
        _log(f"quality_score for '{step_id}': scoring failed: {exc}")
        return (1.0, f"Scoring unavailable: {exc}")  # Pass through on error


# ---------------------------------------------------------------------------
# Step executors
# ---------------------------------------------------------------------------

async def _exec_agent(step: StepDef, state: WorkflowState, cwd: str) -> StepResult:
    """Execute an agent step."""
    from ..patterns.refinement import _spawn_and_wait, _get_agent_output

    cfg = step.resolve_agent_config()
    prompt = interpolate(cfg.prompt, state)

    # Pre-resolve skill krefs into inline content so agents don't waste turns
    # trying to fetch them via MCP tools.
    skill_context = ""
    if step.skills:
        skill_context = await _resolve_skills_inline(step.skills)

    full_prompt = compose_agent_prompt(
        step.id, cfg.role, skill_context, [], prompt,
    )

    # Determine MCP injection level from step config
    include_memory = cfg.tools in ("all", "memory")
    include_operator = cfg.tools == "all"

    # Auth profile binding: surfaced to the agent via the get_auth_token MCP
    # tool, NOT pre-injected into the system prompt or any agent context.
    # We propagate the profile id (and the gateway service token) via env
    # so subagent_mcp.get_auth_token can resolve the credential when (and
    # only when) the agent actually calls the tool.
    agent_env_extra: dict[str, str] = {}
    if cfg.auth:
        agent_env_extra["CONSTRUCT_AUTH_PROFILE_ID"] = cfg.auth
        # Forward the local service token if the operator-mcp process has
        # access to one — keeps the agent subprocess isolated from the file.
        try:
            from .auth_resolver import _service_token  # type: ignore[attr-defined]
            tok = _service_token()
            if tok:
                agent_env_extra["CONSTRUCT_SERVICE_TOKEN"] = tok
        except Exception:  # noqa: BLE001
            pass

    agent, output = await _spawn_and_wait(
        cfg.agent_type, f"wf-{state.run_id[:8]}-{step.id}",
        cwd, full_prompt,
        model=cfg.model, timeout=cfg.timeout,
        max_turns=cfg.max_turns,
        include_memory=include_memory,
        include_operator=include_operator,
        env_extra=agent_env_extra or None,
    )

    agent_output, files = _get_agent_output(agent.id)
    effective = agent_output or output

    # Use sidecar_id as agent_id — it matches the RunLog filename and WS events.
    # Fall back to Python-side agent.id if sidecar wasn't used.
    effective_agent_id = getattr(agent, "_sidecar_id", "") or agent.id

    # Guard: agent completed but returned empty output — treat as failure.
    # This catches rate-limited or timed-out agents that silently produce nothing.
    agent_succeeded = agent.status in ("completed", "idle")
    if agent_succeeded and not effective.strip():
        _log(f"agent step '{step.id}': agent completed but returned empty output — marking as failed")
        agent_succeeded = False

    # Persist full agent output to disk immediately.  The in-memory output
    # can be lost on daemon restart and the Kumiho metadata only stores a
    # 400-char preview.  Writing to disk + artifact means recovery and
    # downstream steps can always read the full text.
    artifact_path = ""
    if effective.strip():
        try:
            art_dir = os.path.expanduser(
                f"~/.construct/artifacts/{state.workflow_name}/{state.run_id}"
            )
            os.makedirs(art_dir, exist_ok=True)
            artifact_path = os.path.join(art_dir, f"{step.id}.md")
            with open(artifact_path, "w", encoding="utf-8") as f:
                f.write(effective)
        except Exception as e:
            _log(f"agent step '{step.id}': failed to write artifact: {e}")
            artifact_path = ""

    result = StepResult(
        step_id=step.id,
        status="completed" if agent_succeeded else "failed",
        output=effective[:50000],
        agent_id=effective_agent_id,
        agent_type=cfg.agent_type,
        role=cfg.role,
        action=step.action,
        files_touched=files,
        error=(effective[:2000] if agent.status == "error"
               else "Agent returned empty output" if not agent_succeeded and not effective.strip()
               else ""),
    )
    # Store template name for Kumiho pool cross-referencing
    result.output_data["template_name"] = cfg.template or ""
    result.output_data["agent_type"] = cfg.agent_type
    result.output_data["role"] = cfg.role
    if artifact_path:
        result.output_data["artifact_path"] = artifact_path
    # Store skills so they persist to Kumiho metadata for live/historical views
    if step.skills:
        result.output_data["skills"] = step.skills

    # If the agent output contains structured data, merge keys into output_data
    # for downstream access via ${step_id.output_data.field}.
    # Supports two formats:
    #   1. Entire output is JSON object → parse directly
    #   2. Markdown with a fenced ```json block → extract and parse the block
    stripped = effective.strip()
    parsed_json: dict | None = None

    # Format 1: entire output is a JSON object
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                parsed_json = obj
        except (json.JSONDecodeError, ValueError):
            pass

    # Format 2: markdown with fenced ```json block (extract LAST one)
    if parsed_json is None:
        json_blocks = re.findall(r'```json\s*\n(.*?)\n\s*```', effective, re.DOTALL)
        if json_blocks:
            try:
                obj = json.loads(json_blocks[-1])  # Use the last JSON block
                if isinstance(obj, dict):
                    parsed_json = obj
            except (json.JSONDecodeError, ValueError):
                pass

    if parsed_json is not None:
        for k, v in parsed_json.items():
            if k not in result.output_data:  # Don't clobber executor fields
                result.output_data[k] = v
        _log(f"agent step '{step.id}': extracted {len(parsed_json)} structured fields into output_data")

    # Quality check: if configured, run a lightweight validator on the output.
    # A score below threshold marks the step as failed, triggering retry.
    qc = cfg.quality_check
    if qc and qc.enabled and result.status == "completed" and effective.strip():
        try:
            score, feedback = await _quality_score(
                output=effective,
                criteria=qc.criteria,
                step_id=step.id,
                model=qc.model,
                prompt_hint=cfg.prompt[:500] if cfg.prompt else "",
            )
            result.output_data["quality_score"] = score
            result.output_data["quality_feedback"] = feedback
            if score < qc.threshold:
                _log(
                    f"agent step '{step.id}': quality score {score:.2f} below "
                    f"threshold {qc.threshold:.2f}, marking as failed for retry"
                )
                result.status = "failed"
                result.error = (
                    f"Quality score {score:.2f} below threshold {qc.threshold:.2f}. "
                    f"Feedback: {feedback}"
                )
        except Exception as exc:
            _log(f"agent step '{step.id}': quality check failed: {exc} — passing through")
            # Don't block the pipeline if the quality check itself fails

    return result


async def _resolve_step_auth(
    step: StepDef,
    auth: str | None,
) -> tuple[dict[str, Any] | None, StepResult | None]:
    """Resolve a step's optional auth profile.

    Returns ``(resolved, None)`` on success (or when no auth was bound),
    or ``(None, StepResult(failed))`` with structured ``auth_resolve_failed``
    error if the profile is missing/expired/unreachable.
    """
    if not auth:
        return None, None
    try:
        resolved = await resolve_auth_profile(auth)
        return resolved, None
    except AuthResolveError as exc:
        return None, StepResult(
            step_id=step.id,
            status="failed",
            error=f"auth_resolve_failed: {exc.code} — {exc}",
            output_data={
                "auth_resolve_failed": True,
                "auth_resolve_code": exc.code,
            },
        )


def _proc_alive(proc: Any) -> bool:
    """Return True if the subprocess is still running (best-effort)."""
    if proc is None:
        return False
    try:
        return proc.returncode is None  # asyncio.subprocess.Process
    except AttributeError:
        try:
            return proc.poll() is None  # subprocess.Popen
        except Exception:
            return False


def _kill_proc(proc: Any) -> None:
    """Best-effort kill of a subprocess and its entire process group.

    On POSIX, child processes are spawned with ``start_new_session=True``
    so they become process-group leaders. We send SIGTERM to the group,
    wait briefly, then escalate to SIGKILL — this catches grandchildren
    spawned by patterns like ``bash -c "long & other"`` that ``proc.kill()``
    would otherwise leak.

    On Windows, process groups don't translate cleanly; fall back to
    ``proc.kill()`` (the existing single-child kill).
    """
    if proc is None:
        return
    if not _proc_alive(proc):
        return

    if os.name == "posix":
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            # Already exited or unable to query — best-effort proc.kill below.
            pgid = None
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            # Brief grace period for graceful exit, then escalate.
            deadline = time.monotonic() + 0.5
            while time.monotonic() < deadline:
                if not _proc_alive(proc):
                    return
                time.sleep(0.05)
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            return

    # Windows fallback (or POSIX path where pgid lookup failed).
    try:
        if hasattr(proc, "kill"):
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        elif hasattr(proc, "poll") and proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def _track_proc(state: WorkflowState, proc: Any) -> None:
    state.running_processes.append(proc)


def _untrack_proc(state: WorkflowState, proc: Any) -> None:
    try:
        state.running_processes.remove(proc)
    except ValueError:
        pass


async def _exec_shell(step: StepDef, state: WorkflowState, cwd: str) -> StepResult:
    """Execute a shell command step.

    Cooperative cancellation: while the subprocess runs, polls
    ``state.cancel_requested`` every 250ms and kills the process if set.
    Also kills the subprocess on timeout (previous versions left it
    orphaned).
    """
    cfg: ShellStepConfig = step.shell  # type: ignore
    command = interpolate(cfg.command, state)

    # Capture interpolated inputs for run-view UI BEFORE auth resolution so
    # even an auth_resolve_failed result records what we tried to run.
    # Command may contain interpolated secrets — _redact_for_persistence in
    # memory.py masks obvious secret patterns at persist time.
    input_data: dict[str, Any] = {
        "command": command,
        "timeout_secs": cfg.timeout,
        "allow_failure": cfg.allow_failure,
        "cwd": cwd,
    }

    auth_resolved, auth_err = await _resolve_step_auth(step, cfg.auth)
    if auth_err is not None:
        auth_err.input_data = input_data
        return auth_err

    # Inherit current env, then layer the auth token on top so the subprocess
    # sees it without us having to know everything that was already set.
    subproc_env = os.environ.copy()
    if auth_resolved:
        subproc_env["CONSTRUCT_AUTH_TOKEN"] = auth_resolved["token"]
        subproc_env["CONSTRUCT_AUTH_KIND"] = auth_resolved.get("kind", "token")

    proc = None
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            env=subproc_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # New session so the child becomes a process-group leader;
            # _kill_proc kills the whole group on cancel/timeout to avoid
            # leaking grandchildren spawned by patterns like
            # ``bash -c "long & other"``.
            start_new_session=(os.name == "posix"),
        )
        _track_proc(state, proc)

        # Run communicate() but poll the cancel flag every 250ms so a
        # mid-step cancel kills the subprocess promptly.
        comm_task = asyncio.create_task(proc.communicate())
        deadline = time.monotonic() + cfg.timeout
        cancelled_mid_step = False
        timed_out = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(comm_task),
                    timeout=min(0.25, remaining),
                )
                break
            except asyncio.TimeoutError:
                if state.cancel_requested:
                    cancelled_mid_step = True
                    break
                continue

        if cancelled_mid_step or timed_out:
            _kill_proc(proc)
            try:
                await asyncio.wait_for(comm_task, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
            if cancelled_mid_step:
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    error="Cancelled by user",
                    input_data=input_data,
                )
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Shell command timed out after {cfg.timeout}s",
                input_data=input_data,
            )

        stdout_raw = stdout.decode("utf-8", errors="replace")
        stderr_raw = stderr.decode("utf-8", errors="replace")
        output = stdout_raw[:4000]
        err = stderr_raw[:2000]

        success = proc.returncode == 0 or cfg.allow_failure
        return StepResult(
            step_id=step.id,
            status="completed" if success else "failed",
            output=output,
            error=err if proc.returncode != 0 else "",
            input_data=input_data,
            output_data={
                "exit_code": proc.returncode,
                "stdout_truncated": len(stdout_raw) > 4000,
                "stderr_truncated": len(stderr_raw) > 2000,
            },
        )
    except Exception as exc:
        _kill_proc(proc)
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(exc)[:2000],
            input_data=input_data,
        )
    finally:
        _untrack_proc(state, proc)


# ---------------------------------------------------------------------------
# Python step — generic JSON-IO subprocess for reusable scripts
# ---------------------------------------------------------------------------

# Where builtin python step scripts live. Workflows can reference these by
# bare filename (e.g. `script: kref_encode.py`) and the executor will resolve
# from this dir if the path doesn't exist relative to the workflow's cwd.
_BUILTIN_PYTHON_STEPS_DIR = os.path.join(
    os.path.dirname(__file__), "builtins", "python_steps"
)


def _operator_mcp_venv_python() -> str:
    """Default interpreter for python steps — operator-mcp's own venv.

    Falls back to the current interpreter if the venv hasn't been
    materialized (e.g. running tests outside `construct install`).
    """
    from ..mcp_injection import _venv_python  # type: ignore[attr-defined]
    home = os.path.expanduser("~")
    venv_root = os.path.join(home, ".construct", "operator_mcp", "venv")
    return _venv_python(venv_root)


def _interpolate_args(value: Any, state: WorkflowState) -> Any:
    """Recursively interpolate ${...} references in dict/list args.

    Strings get the same interpolate() pass shell/agent steps use; lists
    and dicts are walked. Anything else (numbers, bools, None) passes
    through unchanged.
    """
    if isinstance(value, str):
        return interpolate(value, state)
    if isinstance(value, dict):
        return {k: _interpolate_args(v, state) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_args(v, state) for v in value]
    return value


async def _exec_python(step: StepDef, state: WorkflowState, cwd: str) -> StepResult:
    """Execute a Python script step with JSON I/O contract.

    See PythonStepConfig docstring for the protocol. Briefly: payload on
    stdin (args + workflow context), JSON object on stdout becomes the
    step's output_data. Stderr is captured for diagnostics but doesn't
    appear in interpolation.
    """
    cfg: PythonStepConfig = step.python  # type: ignore

    # Capture interpolated inputs for run-view UI. We don't store the full
    # stdin context here — that's reconstructible from inputs + step_results.
    interpolated_args = _interpolate_args(cfg.args, state)
    code_preview = ""
    code_length = 0
    if cfg.code:
        code_length = len(cfg.code)
        code_preview = cfg.code[:500]
    input_data: dict[str, Any] = {
        "script_path": cfg.script or "",
        "code_preview": code_preview,
        "code_length": code_length,
        "args": interpolated_args,
        "timeout_secs": cfg.timeout,
        "allow_failure": cfg.allow_failure,
    }

    # Resolve script path. Order: explicit absolute → relative to workflow cwd
    # → bare name in builtins dir. Inline `code:` skips this entirely.
    script_path: str | None = None
    if cfg.script:
        candidate = cfg.script
        if os.path.isabs(candidate) and os.path.exists(candidate):
            script_path = candidate
        else:
            cwd_path = os.path.join(cwd, candidate)
            builtin_path = os.path.join(_BUILTIN_PYTHON_STEPS_DIR, candidate)
            if os.path.exists(cwd_path):
                script_path = cwd_path
            elif os.path.exists(builtin_path):
                script_path = builtin_path
            else:
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    error=(
                        f"python step script not found: '{cfg.script}' "
                        f"(tried cwd={cwd_path}, builtins={builtin_path})"
                    ),
                    input_data=input_data,
                )

    # Build the JSON payload the script reads from stdin.
    payload = {
        "args": interpolated_args,
        "context": {
            "inputs": state.inputs,
            "step_results": {
                sid: dict(r.output_data or {})
                for sid, r in state.step_results.items()
            },
            "run_id": state.run_id,
            "session_id": getattr(state, "session_id", "") or "",
        },
    }

    python_exe = cfg.python or _operator_mcp_venv_python()
    if script_path:
        cmd = [python_exe, script_path]
    else:
        cmd = [python_exe, "-c", cfg.code or ""]

    # Auth profile binding: resolved at runtime; passed to the subprocess via
    # env vars so the script can read os.environ["CONSTRUCT_AUTH_TOKEN"]
    # without the credential ever appearing in YAML, args, or stdin.
    auth_resolved, auth_err = await _resolve_step_auth(step, cfg.auth)
    if auth_err is not None:
        auth_err.input_data = input_data
        return auth_err
    subproc_env = os.environ.copy()
    if auth_resolved:
        subproc_env["CONSTRUCT_AUTH_TOKEN"] = auth_resolved["token"]
        subproc_env["CONSTRUCT_AUTH_KIND"] = auth_resolved.get("kind", "token")

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=subproc_env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # New session so the child becomes a process-group leader;
            # _kill_proc kills the whole group on cancel/timeout to avoid
            # leaking grandchildren spawned by user code (e.g. subprocess.Popen).
            start_new_session=(os.name == "posix"),
        )
        _track_proc(state, proc)
        # Poll cancel flag every 250ms while waiting for the subprocess.
        comm_task = asyncio.create_task(
            proc.communicate(input=json.dumps(payload).encode("utf-8"))
        )
        deadline = time.monotonic() + cfg.timeout
        cancelled_mid_step = False
        timed_out = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                stdout, stderr = await asyncio.wait_for(
                    asyncio.shield(comm_task),
                    timeout=min(0.25, remaining),
                )
                break
            except asyncio.TimeoutError:
                if state.cancel_requested:
                    cancelled_mid_step = True
                    break
                continue
        if cancelled_mid_step or timed_out:
            _kill_proc(proc)
            try:
                await asyncio.wait_for(comm_task, timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
            if cancelled_mid_step:
                return StepResult(
                    step_id=step.id,
                    status="failed",
                    error="Cancelled by user",
                    input_data=input_data,
                )
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"Python step timed out after {cfg.timeout}s",
                input_data=input_data,
            )

        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")
        rc = proc.returncode or 0

        success = rc == 0 or cfg.allow_failure
        if not success:
            return StepResult(
                step_id=step.id,
                status="failed",
                output=stdout_text[:4000],
                error=(stderr_text[:2000] or f"exited with code {rc}"),
                input_data=input_data,
                output_data={
                    "exit_code": rc,
                    "stdout_truncated": len(stdout_text) > 4000,
                    "stderr_truncated": len(stderr_text) > 2000,
                },
            )

        # Parse stdout as JSON for output_data. A non-JSON stdout is allowed
        # (the script just printed something) — we keep it as raw output but
        # leave output_data minimal so downstream interpolation sees nothing
        # surprising.
        output_data: dict[str, Any] = {
            "exit_code": rc,
            "stdout_truncated": len(stdout_text) > 4000,
            "stderr_truncated": len(stderr_text) > 1000,
        }
        stdout_stripped = stdout_text.strip()
        if stdout_stripped:
            try:
                parsed = json.loads(stdout_stripped)
                if isinstance(parsed, dict):
                    output_data.update(parsed)
                else:
                    output_data["result"] = parsed
            except json.JSONDecodeError:
                # Non-JSON stdout — fine, just don't merge into output_data.
                pass

        return StepResult(
            step_id=step.id,
            status="completed",
            output=stdout_text[:4000],
            error=stderr_text[:1000] if stderr_text else "",
            input_data=input_data,
            output_data=output_data,
        )
    except Exception as exc:
        _kill_proc(proc)
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(exc)[:2000],
            input_data=input_data,
        )
    finally:
        _untrack_proc(state, proc)


# ---------------------------------------------------------------------------
# Email step — outbound SMTP send with optional click-tracking link rewrite
# ---------------------------------------------------------------------------


def _load_email_config_from_toml() -> dict[str, Any]:
    """Read [channels_config.email] from ~/.construct/config.toml.

    Returns an empty dict on any read error so callers fall back to
    explicit per-step config or surface a clear "no SMTP host" error.
    """
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return {}
    path = os.path.expanduser("~/.construct/config.toml")
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (FileNotFoundError, Exception):
        return {}
    channels = data.get("channels_config") or data.get("channels") or {}
    if isinstance(channels, dict):
        email_cfg = channels.get("email") or {}
        if isinstance(email_cfg, dict):
            return email_cfg
    return {}


def _build_mime(
    *,
    to: list[str],
    subject: str,
    body: str,
    body_html: str | None,
    from_address: str,
    cc: list[str],
    bcc: list[str],
    reply_to: str | None,
):
    """Compose a MIME message. multipart/alternative when HTML is present.

    Forces quoted-printable encoding for the text bodies — the default
    for utf-8 is base64, which is correct on the wire but unreadable in
    raw form. Quoted-printable keeps ASCII URLs etc. legible so click
    handlers, log inspectors, and dry-run previews can scan the rendered
    content without decoding it first.
    """
    from email.charset import QP, Charset
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    qp_charset = Charset("utf-8")
    qp_charset.body_encoding = QP

    if body_html:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(body, "plain", qp_charset))
        msg.attach(MIMEText(body_html, "html", qp_charset))
    else:
        msg = MIMEText(body, "plain", qp_charset)

    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to
    return msg


async def _exec_email(step: StepDef, state: WorkflowState) -> StepResult:
    """Execute an email send step.

    Resolves SMTP creds from per-step overrides → ~/.construct/config.toml
    [channels_config.email] section. Optionally rewrites links for click
    tracking. Honors ``dry_run`` for preview workflows.
    """
    cfg: EmailStepConfig = step.email  # type: ignore

    # Interpolate every user-provided string field. We can't run the whole
    # config through interpolate at once (it has list/bool fields), so each
    # template-bearing string is interpolated individually.
    subject = interpolate(cfg.subject, state)
    body = interpolate(cfg.body, state)
    body_html = interpolate(cfg.body_html, state) if cfg.body_html else None
    track_kref = interpolate(cfg.track_kref, state) if cfg.track_kref else None
    track_base_url = (
        interpolate(cfg.track_base_url, state) if cfg.track_base_url else None
    )

    # Recipients can be a single string or a list — normalize to a list.
    raw_to = cfg.to
    if isinstance(raw_to, str):
        to_list = [interpolate(raw_to, state)]
    else:
        to_list = [interpolate(addr, state) for addr in raw_to]
    cc_list = [interpolate(addr, state) for addr in cfg.cc]
    bcc_list = [interpolate(addr, state) for addr in cfg.bcc]

    # Capture interpolated inputs for run-view UI. body_preview is capped to
    # avoid bloating Kumiho metadata for marketing emails with HTML payloads.
    input_data: dict[str, Any] = {
        "to": to_list,
        "cc": cc_list,
        "bcc": bcc_list,
        "subject": subject,
        "from": cfg.from_address or "",
        "body_preview": (body or "")[:500],
        "body_length": len(body or ""),
        "dry_run": cfg.dry_run,
    }

    auth_resolved, auth_err = await _resolve_step_auth(step, cfg.auth)
    if auth_err is not None:
        auth_err.input_data = input_data
        return auth_err

    # Click-tracking link rewrite — same encoded kref shared across body
    # and body_html. Avoids double-rewrites by gating on track_clicks +
    # track_kref both being present.
    if cfg.track_clicks:
        if not track_kref:
            return StepResult(
                step_id=step.id,
                status="failed",
                error="track_clicks=true requires track_kref",
                input_data=input_data,
            )
        try:
            from ..tracking import encode_kref, rewrite_links_with_tracker
        except Exception as exc:  # noqa: BLE001
            return StepResult(
                step_id=step.id,
                status="failed",
                error=f"tracking module not available: {exc}",
                input_data=input_data,
            )
        secret = os.environ.get(cfg.track_secret_env, "") or None
        encoded = encode_kref(track_kref, secret)
        base = track_base_url or os.environ.get("GATEWAY_URL", "")
        if not base:
            return StepResult(
                step_id=step.id,
                status="failed",
                error=(
                    "track_clicks=true requires track_base_url or "
                    "GATEWAY_URL env var"
                ),
                input_data=input_data,
            )
        body = rewrite_links_with_tracker(body, encoded_kref=encoded, base_url=base)
        if body_html:
            body_html = rewrite_links_with_tracker(
                body_html, encoded_kref=encoded, base_url=base
            )
        tracking_info = {"encoded_kref": encoded, "tracked_kref": track_kref}
    else:
        tracking_info = {}

    # Resolve SMTP config: per-step overrides win, then config.toml.
    file_cfg = _load_email_config_from_toml()
    smtp_host = cfg.smtp_host or file_cfg.get("smtp_host", "")
    smtp_tls = (
        cfg.smtp_tls if cfg.smtp_tls is not None else file_cfg.get("smtp_tls", True)
    )
    default_port = 465 if smtp_tls else 587
    smtp_port = cfg.smtp_port or int(file_cfg.get("smtp_port", default_port) or default_port)
    smtp_username = cfg.smtp_username or file_cfg.get("username", "")
    from_address = cfg.from_address or file_cfg.get("from_address", smtp_username)

    # Password resolution order: bound auth profile (decrypted at runtime)
    # → env override (per-step) → config.toml. The auth profile path is the
    # most secure — the SMTP password never lives on disk in plaintext or in
    # the workflow YAML.
    if auth_resolved:
        smtp_password = auth_resolved["token"]
    elif cfg.smtp_password_env:
        smtp_password = os.environ.get(cfg.smtp_password_env, "")
    else:
        smtp_password = file_cfg.get("password", "")

    # Build the rendered message regardless of dry_run — useful for preview.
    msg = _build_mime(
        to=to_list,
        subject=subject,
        body=body,
        body_html=body_html,
        from_address=from_address,
        cc=cc_list,
        bcc=bcc_list,
        reply_to=interpolate(cfg.reply_to, state) if cfg.reply_to else None,
    )
    rendered = msg.as_string()

    output_data: dict[str, Any] = {
        "to": to_list,
        "cc": cc_list,
        "bcc": bcc_list,
        "subject": subject,
        "from": from_address,
        "rendered_size": len(rendered),
        **tracking_info,
    }

    if cfg.dry_run:
        # Preview mode: render but don't send. Outreach campaigns run dry
        # first so the operator can review every personalized email
        # before any actually leave the building.
        output_data["dry_run"] = True
        output_data["rendered"] = rendered
        output_data["delivered"] = False
        return StepResult(
            step_id=step.id,
            status="completed",
            output=f"DRY RUN: would send '{subject}' to {to_list}",
            input_data=input_data,
            output_data=output_data,
        )

    if not smtp_host:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=(
                "no SMTP host configured — set [channels_config.email].smtp_host "
                "in ~/.construct/config.toml or pass smtp_host on the step"
            ),
            input_data=input_data,
        )

    # Send via stdlib smtplib in a thread (it's blocking). asyncio.to_thread
    # plus asyncio.wait_for gives us the timeout enforcement the contract
    # promises without rewriting smtplib.
    import smtplib

    def _send_blocking() -> None:
        all_recipients = list(to_list) + list(cc_list) + list(bcc_list)
        if smtp_tls:
            with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=cfg.timeout) as smtp:
                if smtp_username:
                    smtp.login(smtp_username, smtp_password)
                smtp.sendmail(from_address, all_recipients, rendered)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=cfg.timeout) as smtp:
                smtp.starttls()
                if smtp_username:
                    smtp.login(smtp_username, smtp_password)
                smtp.sendmail(from_address, all_recipients, rendered)

    try:
        await asyncio.wait_for(asyncio.to_thread(_send_blocking), timeout=cfg.timeout)
    except asyncio.TimeoutError:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"Email send timed out after {cfg.timeout}s",
            input_data=input_data,
            output_data={**output_data, "delivered": False},
        )
    except Exception as exc:
        # Don't echo the raw exception to step output — smtplib can include
        # server-returned text in some edge cases. Log the full repr for
        # operator inspection; surface a generic message to the workflow.
        _log(f"SMTP send: {exc!r}")
        return StepResult(
            step_id=step.id,
            status="failed",
            error="SMTP send failed (see logs)",
            input_data=input_data,
            output_data={**output_data, "delivered": False},
        )

    output_data["sent"] = True
    output_data["delivered"] = True
    return StepResult(
        step_id=step.id,
        status="completed",
        output=f"Sent '{subject}' to {to_list}",
        input_data=input_data,
        output_data=output_data,
    )


async def _exec_image(step: StepDef, state: WorkflowState, cwd: str) -> StepResult:
    """Execute an 'image' step — call generate_image_codex directly.

    Bypasses the agent layer entirely. Plain ``codex`` agent steps don't
    have access to the ``generate_image_codex`` MCP tool (the subagent
    MCP server intentionally excludes operator-tier tools), which is why
    prose-prompting an agent to "generate an image and show on canvas"
    silently produces no canvas frame and no Kumiho artifact. Calling
    the tool directly removes the LLM round-trip and the
    "agent-decides-whether-to-call-the-tool" failure mode.
    """
    cfg: ImageStepConfig = step.image  # type: ignore

    prompt = interpolate(cfg.prompt, state)

    # Capture interpolated inputs for run-view UI. dry_run is included so the
    # frontend can flag preview runs even though ImageStepConfig has no
    # explicit dry_run field — the executor never dry-runs image steps today,
    # so this always reads False but the field is reserved.
    input_data: dict[str, Any] = {
        "prompt": prompt,
        "count": cfg.count,
        "model": cfg.sandbox or "",  # ImageStepConfig has no model field; surface sandbox/policy hint instead
        "dry_run": False,
    }

    if not prompt.strip():
        return StepResult(
            step_id=step.id,
            status="failed",
            error="image step requires a non-empty prompt after interpolation",
            input_data=input_data,
        )

    # Default the filename to the step id so authors don't have to
    # repeat themselves. The tool then derives item_name from this stem.
    output_path = (cfg.output_path or "").strip()
    if not output_path:
        output_path = f"{step.id}.png"

    # Default item_name from the step id when register_artifact=true and
    # the author didn't pin one. Without this, two image steps in the
    # same workflow could collide on the default-derived item name from
    # output_path alone if they happen to share a stem.
    item_name = cfg.item_name
    if cfg.register_artifact and not item_name:
        item_name = step.id

    args: dict[str, Any] = {
        "prompt": prompt,
        "output_path": output_path,
        "count": cfg.count,
        "register_artifact": cfg.register_artifact,
        "canvas": cfg.canvas,
    }
    if cfg.cwd:
        args["cwd"] = interpolate(cfg.cwd, state)
    elif cwd:
        args["cwd"] = cwd
    if cfg.output_pattern:
        args["output_pattern"] = cfg.output_pattern
    if cfg.space:
        args["space"] = cfg.space
    if item_name:
        args["item_name"] = item_name
    if cfg.sandbox:
        args["sandbox"] = cfg.sandbox

    try:
        from ..gateway_client import ConstructGatewayClient
        from ..tool_handlers import codex_image
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"image step dependencies unavailable: {exc}",
        )

    gw = ConstructGatewayClient()
    try:
        response = await asyncio.wait_for(
            codex_image.tool_generate_image_codex(args, gw),
            timeout=cfg.timeout,
        )
    except asyncio.TimeoutError:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"image step timed out after {cfg.timeout}s",
            input_data=input_data,
        )
    except Exception as exc:  # noqa: BLE001
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"image step failed: {exc}",
            input_data=input_data,
        )

    if not isinstance(response, dict):
        return StepResult(
            step_id=step.id,
            status="failed",
            error=f"image tool returned unexpected payload type: {type(response).__name__}",
            input_data=input_data,
        )

    files = response.get("files") or []
    urls = response.get("urls") or []
    artifact = response.get("artifact") if isinstance(response.get("artifact"), dict) else {}
    canvas_info = response.get("canvas") if isinstance(response.get("canvas"), dict) else {}

    output_data: dict[str, Any] = {
        "files": files,
        "urls": urls,
        "requested": response.get("requested", cfg.count),
        "generated": response.get("generated", len(files)),
        "images_generated": response.get("generated", len(files)),
        "artifact_krefs": [],
    }
    if artifact:
        output_data["item_kref"] = artifact.get("item_kref", "")
        output_data["revision_kref"] = artifact.get("revision_kref", "")
        output_data["artifact_krefs"] = artifact.get("artifact_krefs", [])
    if canvas_info:
        output_data["canvas_id"] = canvas_info.get("canvas_id", "")
        output_data["canvas_frame_id"] = canvas_info.get("frame_id", "")

    err = response.get("error")
    if err and not files:
        # Hard failure — no PNG produced.
        return StepResult(
            step_id=step.id,
            status="failed",
            output=err,
            input_data=input_data,
            output_data=output_data,
            error=err,
            files_touched=files,
        )

    summary_parts = [f"generated {len(files)}/{cfg.count} image(s)"]
    if canvas_info.get("frame_id"):
        summary_parts.append(f"canvas={canvas_info['frame_id']}")
    if artifact.get("revision_kref"):
        summary_parts.append(f"kref={artifact['revision_kref']}")

    return StepResult(
        step_id=step.id,
        status="completed",
        output="; ".join(summary_parts),
        input_data=input_data,
        output_data=output_data,
        files_touched=list(files),
    )


async def _exec_output(step: StepDef, state: WorkflowState) -> StepResult:
    """Execute an output step — render template with interpolation."""
    cfg: OutputStepConfig = step.output  # type: ignore
    rendered = interpolate(cfg.template, state)

    input_data: dict[str, Any] = {
        "format": cfg.format,
        "template_preview": (cfg.template or "")[:500],
        "template_length": len(cfg.template or ""),
        "entity_kind": cfg.entity_kind or "",
        "entity_tag": cfg.entity_tag,
        "entity_space": cfg.entity_space or "",
        "entity_name": interpolate(cfg.entity_name, state) if cfg.entity_name else "",
    }

    result = StepResult(
        step_id=step.id,
        status="completed",
        output=rendered[:6000],
        input_data=input_data,
        output_data={"format": cfg.format, "entity_registered": False},
    )

    # Entity production — register output as a Kumiho entity
    if cfg.entity_name and cfg.entity_kind:
        from operator_mcp.workflow.memory import publish_workflow_entity
        entity_name = interpolate(cfg.entity_name, state)

        # Interpolate entity_metadata values so ${inputs.*} and ${step.output} resolve
        resolved_metadata: dict[str, str] | None = None
        if cfg.entity_metadata:
            resolved_metadata = {
                k: interpolate(v, state) for k, v in cfg.entity_metadata.items()
            }
            # Warn if most metadata values resolved to empty — likely upstream failure
            non_system = {k: v for k, v in resolved_metadata.items()
                         if k not in ("source_workflow", "source_run_id", "source_step")}
            empty_count = sum(1 for v in non_system.values() if not v.strip())
            if non_system and empty_count > len(non_system) // 2:
                _log(
                    f"output step '{step.id}': WARNING — {empty_count}/{len(non_system)} "
                    f"entity metadata fields are empty: {[k for k, v in non_system.items() if not v.strip()]}"
                )

        entity_result = await publish_workflow_entity(
            entity_name=entity_name,
            entity_kind=cfg.entity_kind,
            entity_tag=cfg.entity_tag,
            entity_space=cfg.entity_space,
            entity_metadata=resolved_metadata,
            content=rendered,
            content_format=cfg.format,
            workflow_name=state.workflow_name,
            run_id=state.run_id,
            step_id=step.id,
        )
        if entity_result:
            result.output_data["entity_kref"] = entity_result["item_kref"]
            result.output_data["entity_revision_kref"] = entity_result["revision_kref"]
            result.output_data["entity_name"] = entity_name
            result.output_data["entity_kind"] = cfg.entity_kind
            result.output_data["entity_tag"] = cfg.entity_tag
            result.output_data["entity_registered"] = True

    return result


async def _exec_resolve(step: StepDef, state: WorkflowState) -> StepResult:
    """Resolve a Kumiho entity by kind+tag — deterministic, no LLM."""
    cfg: ResolveStepConfig = step.resolve or ResolveStepConfig(kind="")
    if not cfg.kind:
        return StepResult(step_id=step.id, status="failed", error="resolve step requires 'kind'")

    # Capture interpolated query parameters for run-view UI.
    resolved_kind = interpolate(cfg.kind, state)
    resolved_tag = interpolate(cfg.tag, state)
    resolved_name_pattern = interpolate(cfg.name_pattern, state) if cfg.name_pattern else ""
    resolved_space = interpolate(cfg.space, state) if cfg.space else ""
    input_data: dict[str, Any] = {
        "kind": resolved_kind,
        "tag": resolved_tag,
        "name_pattern": resolved_name_pattern,
        "space": resolved_space,
        "mode": cfg.mode,
        "fail_if_missing": cfg.fail_if_missing,
    }

    try:
        from operator_mcp.workflow.memory import resolve_entity
        entity = await resolve_entity(
            kind=resolved_kind,
            tag=resolved_tag,
            name_pattern=resolved_name_pattern,
            space=resolved_space,
            mode=cfg.mode,
        )
    except Exception as exc:
        if cfg.fail_if_missing:
            return StepResult(
                step_id=step.id, status="failed",
                error=f"resolve failed: {exc}",
                input_data=input_data,
            )
        entity = None

    if entity is None and cfg.fail_if_missing:
        return StepResult(
            step_id=step.id, status="failed",
            error=f"No entity found for kind={cfg.kind!r} tag={cfg.tag!r}",
            input_data=input_data,
        )

    output_data: dict[str, Any] = {}
    if entity is None:
        output_data["found"] = False
    elif cfg.mode == "latest":
        output_data["found"] = True
        matched_kref = entity.get("item_kref") or entity.get("kref", "")
        matched_name = entity.get("name", "")
        output_data["item_kref"] = matched_kref
        output_data["revision_kref"] = entity.get("kref", "")
        output_data["name"] = matched_name
        # Convenience fields for the run-view UI — denormalized so the
        # frontend can render "matched: <name> (<kref>)" without poking at
        # the rest of the metadata blob.
        output_data["matched_kref"] = matched_kref
        output_data["matched_name"] = matched_name
        # Extract metadata
        meta = entity.get("metadata", {})
        if cfg.fields:
            for f in cfg.fields:
                output_data[f] = meta.get(f, "")
        else:
            output_data["metadata"] = meta
            # Also flatten top-level metadata keys for easy interpolation
            for k, v in meta.items():
                if k not in output_data:
                    output_data[k] = v
        # Auto-load artifact content from disk if available.
        # Agents in max_turns=1 mode can't fetch kref content, so we
        # inline the artifact text as output_data["artifact_content"].
        art_path = meta.get("artifact_path", "")
        if art_path and os.path.isfile(art_path):
            try:
                with open(art_path, "r", encoding="utf-8") as fh:
                    output_data["artifact_content"] = fh.read()
                output_data["artifact_path"] = art_path
            except Exception as exc:
                _log(f"resolve: failed to read artifact {art_path}: {exc}")
    else:  # mode == "all"
        entities = entity if isinstance(entity, list) else [entity]
        output_data["found"] = True
        output_data["count"] = len(entities)
        output_data["entities"] = entities
        # Build a formatted summary for agent prompts
        lines = []
        for ent in entities:
            ent_name = ent.get("name", "unknown")
            ent_kref = ent.get("item_kref", ent.get("kref", ""))
            meta = ent.get("metadata", {})
            meta_str = ", ".join(f"{k}={v}" for k, v in meta.items()
                                if k not in ("source_workflow", "source_run_id", "source_step",
                                             "content_preview", "content_length", "artifact_path"))
            lines.append(f"- {ent_name} (kref: {ent_kref}) [{meta_str}]")
        output_data["summary"] = "\n".join(lines)
        # Also expose individual entity krefs as a comma-separated list for iteration
        output_data["item_krefs"] = ",".join(
            ent.get("item_kref", ent.get("kref", "")) for ent in entities
        )

    summary = f"Resolved {cfg.kind}:{cfg.tag}"
    if output_data.get("found"):
        summary += f" → {output_data.get('name', output_data.get('item_kref', ''))}"
    else:
        summary += " → not found"

    return StepResult(
        step_id=step.id,
        status="completed",
        output=summary,
        input_data=input_data,
        output_data=output_data,
        action=step.action or "resolve",
    )


async def _exec_tag(step: StepDef, state: WorkflowState) -> StepResult:
    """Re-tag an existing Kumiho entity revision."""
    cfg: TagStepConfig = step.tag_step or TagStepConfig(item_kref="", tag="")
    if not cfg.item_kref:
        return StepResult(step_id=step.id, status="failed", error="tag step requires 'item_kref'")
    if not cfg.tag:
        return StepResult(step_id=step.id, status="failed", error="tag step requires 'tag'")

    item_kref = interpolate(cfg.item_kref, state)
    new_tag = interpolate(cfg.tag, state)
    old_tag = interpolate(cfg.untag, state) if cfg.untag else ""

    input_data: dict[str, Any] = {
        "kref": item_kref,
        "tag": new_tag,
        "previous_tag": old_tag,
    }

    try:
        from operator_mcp.workflow.memory import tag_entity
        result = await tag_entity(
            item_kref=item_kref,
            tag=new_tag,
            untag=old_tag,
        )
    except Exception as exc:
        return StepResult(
            step_id=step.id, status="failed",
            error=f"tag failed: {exc}",
            input_data=input_data,
        )

    output_data: dict[str, Any] = dict(result or {})
    output_data["tagged"] = True
    if old_tag:
        output_data["previous_tag"] = old_tag

    return StepResult(
        step_id=step.id,
        status="completed",
        output=f"Tagged {item_kref}: {old_tag + ' → ' if old_tag else ''}{new_tag}",
        input_data=input_data,
        output_data=output_data,
    )


async def _exec_deprecate(step: StepDef, state: WorkflowState) -> StepResult:
    """Deprecate a Kumiho item."""
    cfg: DeprecateStepConfig = step.deprecate_step or DeprecateStepConfig(item_kref="")
    if not cfg.item_kref:
        return StepResult(step_id=step.id, status="failed", error="deprecate step requires 'item_kref'")

    item_kref = interpolate(cfg.item_kref, state)
    reason = interpolate(cfg.reason, state) if cfg.reason else ""

    input_data: dict[str, Any] = {
        "kref": item_kref,
        "reason": reason,
    }

    try:
        from operator_mcp.workflow.memory import deprecate_entity
        result = await deprecate_entity(item_kref=item_kref, reason=reason)
    except Exception as exc:
        return StepResult(
            step_id=step.id, status="failed",
            error=f"deprecate failed: {exc}",
            input_data=input_data,
        )

    output_data: dict[str, Any] = dict(result or {})
    output_data["deprecated_at"] = datetime.now(timezone.utc).isoformat()

    return StepResult(
        step_id=step.id,
        status="completed",
        output=f"Deprecated {item_kref}" + (f" ({reason})" if reason else ""),
        input_data=input_data,
        output_data=output_data,
    )


async def _exec_for_each(
    step: StepDef,
    state: WorkflowState,
    cwd: str,
    wf: WorkflowDef,
) -> StepResult:
    """Execute a for_each step — sequential iteration over a range or list.

    For each iteration:
      1. Inject for_each context (variable, index, total) into state.inputs
      2. Inject previous iteration results as ``__previous__``
      3. Execute each sub-step sequentially
      4. Store sub-step results as ``<step_id>__iter_<N>`` in workflow state
      5. On completion, also store the latest iteration results under the
         original step IDs so downstream steps can reference them directly
    """
    cfg: ForEachStepConfig = step.for_each  # type: ignore
    if not cfg:
        return StepResult(step_id=step.id, status="failed", error="for_each config missing")

    # Run-to-step: ``compute_ancestor_closure`` pulls every body step into
    # scope when the wrapper is in scope, so this guard is normally a no-op.
    # Keep it as a defensive check: if closure construction has been bypassed
    # and partial bodies leak in, we'd rather skip the loop than execute a
    # fragmentary iteration.
    if state.run_to_closure:
        if not all(sub_id in state.run_to_closure for sub_id in cfg.steps):
            _log(
                f"workflow: run_to skipping for_each '{step.id}' — "
                f"body steps not all in closure (defensive)"
            )
            return StepResult(
                step_id=step.id, status="skipped",
                error="for_each skipped: run-to-step closure excludes body",
            )

    # Resolve iteration values from range or items list.
    # If range resolves to an empty string, fall through to items.
    values: list[str] = []
    range_str = ""
    if cfg.range:
        range_str = interpolate(cfg.range, state).strip()

    if range_str:
        # Support both ".." (1..8) and "-" (101-109) as range separators
        sep = None
        if ".." in range_str:
            sep = ".."
        elif "-" in range_str:
            # Handle negative start: skip leading minus for detection
            rest = range_str.lstrip().lstrip("-")
            if "-" in rest:
                sep = "-"
        if sep:
            try:
                lo, hi = range_str.split(sep, 1) if sep == ".." else range_str.rsplit("-", 1)
                lo_i, hi_i = int(lo.strip()), int(hi.strip())
                if lo_i > hi_i:
                    return StepResult(
                        step_id=step.id, status="failed",
                        error=f"Invalid range: '{range_str}' — start ({lo_i}) > end ({hi_i}). Use ascending ranges only.",
                    )
                values = [str(i) for i in range(lo_i, hi_i + 1)]
            except ValueError:
                return StepResult(
                    step_id=step.id, status="failed",
                    error=f"Invalid range: '{range_str}' (expected 'N..M' or 'N-M' where N and M are integers)",
                )
        else:
            # Single value (no separator found)
            values = [range_str]
    elif cfg.items:
        values = [interpolate(item, state) for item in cfg.items]
    elif cfg.range:
        # Range expression was set but resolved to empty
        return StepResult(
            step_id=step.id, status="failed",
            error=f"for_each range resolved to empty (expression: '{cfg.range}'). "
                  f"Provide a valid range via inputs or add 'items' as fallback.",
        )
    else:
        return StepResult(step_id=step.id, status="failed", error="for_each needs 'range' or 'items'")

    # Build the run-view input_data once values are resolved. Items_preview
    # is capped to 5 entries because for_each runs over potentially huge
    # ranges (1..1000) and we don't want to persist the entire list.
    base_input_data: dict[str, Any] = {
        "variable": cfg.variable,
        "items_count": len(values),
        "items_preview": values[:5],
    }

    if not values:
        return StepResult(
            step_id=step.id, status="completed",
            output="for_each: 0 iterations (empty range)",
            input_data=base_input_data,
            output_data={"iterations_completed": 0, "completed": 0, "total": 0},
        )

    # Safety cap
    if len(values) > cfg.max_iterations:
        return StepResult(
            step_id=step.id, status="failed",
            error=f"for_each range has {len(values)} items but max_iterations={cfg.max_iterations}. Increase max_iterations if intentional.",
        )

    total = len(values)
    _log(f"for_each '{step.id}': {total} iterations, sub-steps={cfg.steps}, variable={cfg.variable}")

    # Validate sub-steps exist
    for sub_id in cfg.steps:
        if not wf.step_by_id(sub_id):
            return StepResult(
                step_id=step.id, status="failed",
                error=f"for_each sub-step '{sub_id}' not found in workflow",
            )

    # Save original inputs so we can restore after loop
    original_for_each = state.inputs.get("__for_each__")
    original_previous = state.inputs.get("__previous__")

    # Resume support: check if we're resuming from a paused state.
    # __for_each_resume__ stores {step_id, iteration, sub_step} so we can
    # skip already-completed iterations and sub-steps.
    resume_ctx = state.inputs.pop("__for_each_resume__", None)
    resume_iter: int = 0          # 1-based iteration to resume from (0 = fresh start)
    resume_sub_idx: int = 0       # index into cfg.steps to resume from
    if isinstance(resume_ctx, dict) and resume_ctx.get("step_id") == step.id:
        resume_iter = resume_ctx.get("iteration", 0)
        resume_sub_name = resume_ctx.get("sub_step", "")
        if resume_sub_name in cfg.steps:
            resume_sub_idx = cfg.steps.index(resume_sub_name)
        _log(f"for_each '{step.id}': resuming from iteration {resume_iter}, sub-step '{resume_sub_name}'")

    iteration_summaries: list[str] = []
    previous_results: dict[str, dict] = {}  # step_id -> result dict from prior iteration
    completed_iterations = 0
    cancelled_mid_loop = False

    for idx, value in enumerate(values):
        iter_num = idx + 1

        # Cooperative cancel between iterations. Without this check, a long
        # for_each over agent-only sub-steps wouldn't notice cancel until the
        # entire loop returned. Partial results in state.step_results are
        # preserved (we just break, not clear).
        if state.cancel_requested:
            cancelled_mid_loop = True
            _log(
                f"for_each '{step.id}': cancel observed before iteration "
                f"{iter_num}/{total}; breaking with {completed_iterations} "
                f"completed"
            )
            break

        # If resuming, skip fully completed iterations
        if resume_iter and iter_num < resume_iter:
            # Reconstruct previous_results from stored iteration results
            if cfg.carry_forward:
                for sid in cfg.steps:
                    iter_key = f"{sid}__iter_{iter_num}"
                    sr = state.step_results.get(iter_key)
                    if sr:
                        previous_results[sid] = sr.model_dump()
            iteration_summaries.append(f"iter {iter_num} ({cfg.variable}={value}): completed (prior run)")
            completed_iterations += 1
            continue

        _log(f"for_each '{step.id}': iteration {iter_num}/{total} ({cfg.variable}={value})")

        # Inject for_each context
        state.inputs["__for_each__"] = {
            cfg.variable: value,
            "index": idx,
            "iteration": iter_num,
            "total": total,
        }

        # Inject previous iteration results (or clear if carry_forward=False)
        if cfg.carry_forward and previous_results:
            state.inputs["__previous__"] = previous_results
        else:
            state.inputs.pop("__previous__", None)

        # Execute sub-steps sequentially within this iteration
        current_results: dict[str, dict] = {}
        iteration_failed = False

        for sub_step_idx, sub_id in enumerate(cfg.steps):
            sub_step = wf.step_by_id(sub_id)
            if not sub_step:
                continue

            # If resuming within this iteration, skip already-completed sub-steps
            if resume_iter and iter_num == resume_iter and sub_step_idx < resume_sub_idx:
                iter_key = f"{sub_id}__iter_{iter_num}"
                sr = state.step_results.get(iter_key)
                if sr and sr.status == "completed":
                    current_results[sub_id] = sr.model_dump()
                    continue

            # Check sub-step's depends_on within the iteration context
            # Dependencies refer to other sub-steps in the same iteration
            deps_ok = True
            for dep in sub_step.depends_on:
                if dep in cfg.steps:
                    # Internal dependency — check this iteration's results
                    iter_key = f"{dep}__iter_{iter_num}"
                    dep_result = state.step_results.get(iter_key)
                    if not dep_result or dep_result.status != "completed":
                        deps_ok = False
                        break
                else:
                    # External dependency — check global results
                    dep_result = state.step_results.get(dep)
                    if not dep_result or dep_result.status != "completed":
                        deps_ok = False
                        break

            if not deps_ok:
                iter_key = f"{sub_id}__iter_{iter_num}"
                state.step_results[iter_key] = StepResult(
                    step_id=sub_id, status="skipped",
                    error="Dependencies not satisfied within iteration",
                )
                iteration_failed = True
                break

            # Temporarily set the sub-step result under its original ID
            # so interpolation within the iteration works naturally
            # (e.g. one sub-step referencing another via ${sub_step_id.output})
            state.current_step = sub_id
            result = await _execute_step_with_retry(sub_step, state, cwd, wf)

            # Store under iteration-qualified key for history
            iter_key = f"{sub_id}__iter_{iter_num}"
            state.step_results[iter_key] = result
            # Also store under original key so intra-iteration refs work
            state.step_results[sub_id] = result
            current_results[sub_id] = result.model_dump()

            # Handle human approval / input pause within for_each
            if result.status == "pending":
                _log(f"for_each '{step.id}': sub-step '{sub_id}' requires human action, pausing at iteration {iter_num}")
                # Save resume context so we can pick up from the NEXT sub-step
                # when the workflow is resumed (the pending step will be marked
                # completed by tool_resume_workflow before re-entering).
                # If this is the last sub-step, advance to next iteration.
                next_sub_idx = sub_step_idx + 1
                if next_sub_idx < len(cfg.steps):
                    # Resume from the next sub-step in the same iteration
                    state.inputs["__for_each_resume__"] = {
                        "step_id": step.id,
                        "iteration": iter_num,
                        "sub_step": cfg.steps[next_sub_idx],
                    }
                else:
                    # Last sub-step — resume from the start of the NEXT iteration
                    state.inputs["__for_each_resume__"] = {
                        "step_id": step.id,
                        "iteration": iter_num + 1,
                        "sub_step": cfg.steps[0] if cfg.steps else "",
                    }

                # Checkpoint so resume can find our state
                state.status = WorkflowStatus.PAUSED
                state.error = f"Awaiting human action in for_each '{step.id}' iteration {iter_num}"
                if wf.checkpoint:
                    _save_checkpoint(state)
                ACTIVE_WORKFLOWS[state.run_id] = state

                # Restore original inputs (except resume context, which must survive)
                if original_for_each is not None:
                    state.inputs["__for_each__"] = original_for_each
                if original_previous is not None:
                    state.inputs["__previous__"] = original_previous

                return StepResult(
                    step_id=step.id,
                    status="pending",
                    output=f"Paused at iteration {iter_num}/{total}, sub-step '{sub_id}'",
                    input_data=base_input_data,
                    output_data={
                        "awaiting_approval": True,
                        "paused_iteration": iter_num,
                        "paused_sub_step": sub_id,
                        "iterations_completed": completed_iterations,
                    },
                )

            if result.status == "failed":
                _log(f"for_each '{step.id}': sub-step '{sub_id}' failed at iteration {iter_num}")
                iteration_failed = True
                break

        if iteration_failed:
            # If cancel landed mid-iteration (e.g. main loop killed our shell
            # subprocess after cancel_requested flipped), treat as cancel
            # rather than a real failure so output_data flags partial completion.
            if state.cancel_requested:
                cancelled_mid_loop = True
                iteration_summaries.append(
                    f"iter {iter_num} ({cfg.variable}={value}): CANCELLED"
                )
                _log(
                    f"for_each '{step.id}': cancel observed during iteration "
                    f"{iter_num}; breaking with {completed_iterations} completed"
                )
                break
            iteration_summaries.append(f"iter {iter_num} ({cfg.variable}={value}): FAILED")
            if cfg.fail_fast:
                break
        else:
            iteration_summaries.append(f"iter {iter_num} ({cfg.variable}={value}): completed")
            completed_iterations += 1

        # Clear resume context after completing the resumed iteration
        if resume_iter and iter_num == resume_iter:
            resume_iter = 0
            resume_sub_idx = 0

        # Carry forward: snapshot this iteration's results for next iteration
        previous_results = current_results

        # Checkpoint + persist after each iteration so UI sees progress
        if wf.checkpoint:
            _save_checkpoint(state)
        try:
            from .memory import persist_workflow_run
            step_dicts = {k: v.model_dump() for k, v in state.step_results.items()}
            await persist_workflow_run(
                workflow_name=state.workflow_name,
                run_id=state.run_id,
                status="running",
                inputs=state.inputs,
                step_results=step_dicts,
                started_at=state.started_at,
                steps_total=len(wf.steps),
                workflow_item_kref=state.workflow_item_kref,
                workflow_revision_kref=state.workflow_revision_kref,
            )
        except Exception:
            pass  # Non-fatal

    # Restore original inputs
    if original_for_each is not None:
        state.inputs["__for_each__"] = original_for_each
    else:
        state.inputs.pop("__for_each__", None)
    if original_previous is not None:
        state.inputs["__previous__"] = original_previous
    else:
        state.inputs.pop("__previous__", None)

    summary = (
        f"for_each '{step.id}': {completed_iterations}/{total} iterations completed"
        + (" (cancelled)" if cancelled_mid_loop else "")
        + "\n"
        + "\n".join(iteration_summaries)
    )
    _log(summary)

    if cancelled_mid_loop:
        status = "failed"
    else:
        status = "completed" if completed_iterations == total else "failed"

    output_data: dict[str, Any] = {
        "completed": completed_iterations,
        "total": total,
        "iterations_completed": completed_iterations,
        "iterations": iteration_summaries,
    }
    if cancelled_mid_loop:
        output_data["cancelled_after_iteration"] = completed_iterations

    if cancelled_mid_loop:
        error = "Cancelled by user"
    elif status == "completed":
        error = ""
    else:
        error = f"{total - completed_iterations} iteration(s) failed"

    return StepResult(
        step_id=step.id,
        status=status,
        output=summary,
        input_data=base_input_data,
        output_data=output_data,
        error=error,
    )


async def _exec_a2a(step: StepDef, state: WorkflowState) -> StepResult:
    """Execute an A2A step — send task to external A2A agent via outbound client."""
    cfg: A2AStepConfig = step.a2a  # type: ignore
    message = interpolate(cfg.message, state)

    auth_resolved, auth_err = await _resolve_step_auth(step, cfg.auth)
    if auth_err is not None:
        return auth_err
    auth_token = auth_resolved["token"] if auth_resolved else None

    try:
        from ..a2a.a2a_client import get_client, A2AClientError
        client = get_client(timeout=cfg.timeout)

        task = await client.send_task(
            cfg.url,
            message=message,
            skill_id=cfg.skill_id,
            auth_token=auth_token,
        )

        task_id = task.get("id", "")
        status = task.get("status", {})
        state_val = status.get("state", "unknown")

        # Poll until complete if not already terminal
        if state_val not in ("completed", "failed", "canceled"):
            task = await client.poll_until_complete(
                cfg.url, task_id,
                poll_interval=5.0,
                max_polls=int(cfg.timeout / 5),
            )
            status = task.get("status", {})
            state_val = status.get("state", "unknown")

        # Extract output text from artifacts
        output_text = ""
        for artifact in task.get("artifacts", []):
            for part in artifact.get("parts", []):
                if part.get("type") == "text":
                    output_text += part.get("text", "") + "\n"

        return StepResult(
            step_id=step.id,
            status="completed" if state_val == "completed" else "failed",
            output=output_text[:6000] or json.dumps(task, default=str)[:4000],
            output_data=task,
            error=status.get("message", "") if state_val == "failed" else "",
        )
    except Exception as exc:
        return StepResult(
            step_id=step.id,
            status="failed",
            error=str(exc)[:2000],
        )


# ---------------------------------------------------------------------------
# Orchestration pattern step executors
# ---------------------------------------------------------------------------

async def _exec_map_reduce(step: StepDef, state: WorkflowState, cwd: str) -> StepResult:
    """Execute a map_reduce pattern step."""
    cfg: MapReduceStepConfig = step.map_reduce  # type: ignore
    task = interpolate(cfg.task, state)
    splits = [interpolate(s, state) for s in cfg.splits]

    try:
        from ..patterns.map_reduce import tool_map_reduce
        result = await tool_map_reduce({
            "task": task,
            "splits": splits,
            "mapper": cfg.mapper,
            "reducer": cfg.reducer,
            "cwd": cwd,
            "concurrency": cfg.concurrency,
            "timeout": cfg.timeout,
        })
        status = result.get("status", "")
        reducer = result.get("reducer", {})
        output = reducer.get("output", "") if isinstance(reducer, dict) else ""
        return StepResult(
            step_id=step.id,
            status="completed" if status == "completed" else "failed",
            output=output[:6000] or json.dumps(result, default=str)[:4000],
            output_data=result,
            error=result.get("error", "") if status != "completed" else "",
        )
    except Exception as exc:
        return StepResult(step_id=step.id, status="failed", error=str(exc)[:2000])


async def _exec_supervisor(step: StepDef, state: WorkflowState, cwd: str) -> StepResult:
    """Execute a supervisor delegation pattern step."""
    cfg: SupervisorStepConfig = step.supervisor  # type: ignore
    task = interpolate(cfg.task, state)

    try:
        from ..patterns.supervisor import tool_supervisor_run
        result = await tool_supervisor_run({
            "task": task,
            "cwd": cwd,
            "max_iterations": cfg.max_iterations,
            "supervisor_type": cfg.supervisor_type,
            "timeout": cfg.timeout,
        })
        status = result.get("status", "")
        summary = result.get("final_summary", "")
        return StepResult(
            step_id=step.id,
            status="completed" if status == "completed" else "failed",
            output=summary[:6000] or json.dumps(result, default=str)[:4000],
            output_data=result,
        )
    except Exception as exc:
        return StepResult(step_id=step.id, status="failed", error=str(exc)[:2000])


async def _exec_group_chat(step: StepDef, state: WorkflowState, cwd: str) -> StepResult:
    """Execute a group chat discussion step."""
    cfg: GroupChatStepConfig = step.group_chat  # type: ignore
    topic = interpolate(cfg.topic, state)

    # Callback to stream intermediate transcript into workflow state
    def _on_turn(transcript: list[dict[str, str]]) -> None:
        state.step_results[step.id] = StepResult(
            step_id=step.id,
            status="running",
            output=f"Discussion in progress... ({len(transcript)} messages)",
            output_data={"transcript": transcript, "topic": topic},
        )

    try:
        from ..patterns.group_chat import tool_group_chat
        result = await tool_group_chat({
            "topic": topic,
            "participants": cfg.participants,
            "moderator": cfg.moderator,
            "strategy": cfg.strategy,
            "max_rounds": cfg.max_rounds,
            "cwd": cwd,
            "timeout": cfg.timeout,
        }, on_turn=_on_turn)
        summary = result.get("summary", "")
        conclusion = result.get("conclusion", "")
        output = f"Summary: {summary}\nConclusion: {conclusion}"
        return StepResult(
            step_id=step.id,
            status="completed",
            output=output[:6000],
            output_data=result,
        )
    except Exception as exc:
        return StepResult(step_id=step.id, status="failed", error=str(exc)[:2000])


async def _exec_handoff(step: StepDef, state: WorkflowState, cwd: str) -> StepResult:
    """Execute a handoff step — transfer context from a prior agent to a new one."""
    cfg: HandoffStepConfig = step.handoff  # type: ignore
    reason = interpolate(cfg.reason, state)
    task = interpolate(cfg.task, state) if cfg.task else ""

    # Resolve the source agent ID from the referenced step
    from_step_result = state.step_results.get(cfg.from_step)
    if not from_step_result or not from_step_result.agent_id:
        return StepResult(
            step_id=step.id, status="failed",
            error=f"Handoff source step '{cfg.from_step}' has no agent_id",
        )

    try:
        from ..patterns.handoff import tool_handoff_agent
        result = await tool_handoff_agent({
            "from_agent_id": from_step_result.agent_id,
            "to_agent_type": cfg.to_agent_type,
            "reason": reason,
            "task": task,
            "cwd": cwd,
            "timeout": cfg.timeout,
        })

        to_status = result.get("to_agent_status", "error")
        output = result.get("to_agent_output", "")
        return StepResult(
            step_id=step.id,
            status="completed" if to_status in ("completed", "idle") else "failed",
            output=output[:6000],
            output_data=result,
            agent_id=result.get("to_agent_id"),
            files_touched=result.get("to_agent_files", []),
        )
    except Exception as exc:
        return StepResult(step_id=step.id, status="failed", error=str(exc)[:2000])


# ---------------------------------------------------------------------------
# Active workflow registry (for status checks and cancellation)
# ---------------------------------------------------------------------------

ACTIVE_WORKFLOWS: dict[str, WorkflowState] = {}


# ---------------------------------------------------------------------------
# Dry-run: plan without executing
# ---------------------------------------------------------------------------

def dry_run_workflow(wf: WorkflowDef, inputs: dict[str, Any]) -> dict[str, Any]:
    """Validate and plan a workflow without executing anything.

    Returns execution plan with step order, estimated agent count,
    parallel groups, and variable resolution preview.
    """
    vr = validate_workflow(wf)
    if not vr.valid:
        return {
            "valid": False,
            "errors": [e.to_dict() for e in vr.errors],
            "warnings": [w.to_dict() for w in vr.warnings],
        }

    # Build execution plan
    plan_steps: list[dict[str, Any]] = []
    agent_count = 0
    shell_count = 0
    parallel_groups: list[list[str]] = []

    for step_id in vr.execution_order:
        step = wf.step_by_id(step_id)
        if not step:
            continue

        entry: dict[str, Any] = {
            "id": step.id,
            "name": step.name,
            "type": step.type.value,
            "depends_on": step.depends_on,
        }

        if step.type == StepType.AGENT:
            cfg = step.agent
            if cfg:
                entry["agent_type"] = cfg.agent_type
                entry["role"] = cfg.role
                entry["timeout"] = cfg.timeout
                if cfg.template:
                    entry["template"] = cfg.template
            agent_count += 1

        elif step.type == StepType.SHELL:
            cfg = step.shell
            if cfg:
                entry["command_preview"] = cfg.command[:100]
                entry["timeout"] = cfg.timeout
            shell_count += 1

        elif step.type == StepType.PYTHON:
            cfg = step.python
            if cfg:
                if cfg.script:
                    entry["script"] = cfg.script
                else:
                    entry["code_preview"] = (cfg.code or "")[:100]
                entry["timeout"] = cfg.timeout

        elif step.type == StepType.EMAIL:
            cfg = step.email
            if cfg:
                entry["to_preview"] = cfg.to if isinstance(cfg.to, str) else f"{len(cfg.to)} recipients"
                entry["subject_preview"] = cfg.subject[:80]
                entry["track_clicks"] = cfg.track_clicks
                entry["dry_run"] = cfg.dry_run

        elif step.type == StepType.IMAGE:
            cfg = step.image
            if cfg:
                entry["prompt_preview"] = cfg.prompt[:80]
                entry["count"] = cfg.count
                entry["canvas"] = bool(cfg.canvas)
                entry["register_artifact"] = cfg.register_artifact
                entry["timeout"] = cfg.timeout

        elif step.type == StepType.PARALLEL:
            cfg = step.parallel
            if cfg:
                entry["sub_steps"] = cfg.steps
                entry["join"] = cfg.join.value
                parallel_groups.append(cfg.steps)
                # Count agents in parallel sub-steps
                for sub_id in cfg.steps:
                    sub = wf.step_by_id(sub_id)
                    if sub and sub.type == StepType.AGENT:
                        agent_count += 1

        elif step.type == StepType.CONDITIONAL:
            cfg = step.conditional
            if cfg:
                entry["branches"] = [
                    {"condition": b.condition, "goto": b.goto}
                    for b in cfg.branches
                ]

        elif step.type == StepType.GOTO:
            cfg = step.goto
            if cfg:
                entry["target"] = cfg.target
                entry["max_iterations"] = cfg.max_iterations

        elif step.type == StepType.A2A:
            cfg = step.a2a
            if cfg:
                entry["url"] = cfg.url
                entry["skill_id"] = cfg.skill_id
            agent_count += 1  # External agent

        elif step.type == StepType.MAP_REDUCE:
            cfg = step.map_reduce
            if cfg:
                entry["splits_count"] = len(cfg.splits)
                entry["mapper"] = cfg.mapper
                entry["reducer"] = cfg.reducer
                entry["concurrency"] = cfg.concurrency
            agent_count += len(cfg.splits) + 1 if cfg else 2  # mappers + reducer

        elif step.type == StepType.SUPERVISOR:
            cfg = step.supervisor
            if cfg:
                entry["max_iterations"] = cfg.max_iterations
                entry["supervisor_type"] = cfg.supervisor_type
            agent_count += (cfg.max_iterations if cfg else 5) + 1  # specialists + supervisor

        elif step.type == StepType.GROUP_CHAT:
            cfg = step.group_chat
            if cfg:
                entry["participants"] = cfg.participants
                entry["strategy"] = cfg.strategy
                entry["max_rounds"] = cfg.max_rounds
            agent_count += (cfg.max_rounds if cfg else 8) + 1  # turns + moderator

        elif step.type == StepType.HANDOFF:
            cfg = step.handoff
            if cfg:
                entry["from_step"] = cfg.from_step
                entry["to_agent_type"] = cfg.to_agent_type
            agent_count += 1

        if step.retry > 0:
            entry["retry"] = step.retry

        plan_steps.append(entry)

    # Estimate cost range (rough: $0.01-0.05 per agent call)
    estimated_cost_low = agent_count * 0.01
    estimated_cost_high = agent_count * 0.05

    return {
        "valid": True,
        "workflow": wf.name,
        "version": wf.version,
        "description": wf.description,
        "execution_order": vr.execution_order,
        "plan": plan_steps,
        "summary": {
            "total_steps": len(wf.steps),
            "agent_steps": agent_count,
            "shell_steps": shell_count,
            "parallel_groups": len(parallel_groups),
            "has_conditionals": any(s.type == StepType.CONDITIONAL for s in wf.steps),
            "has_loops": any(s.type == StepType.GOTO for s in wf.steps),
            "has_human_approval": any(s.type == StepType.HUMAN_APPROVAL for s in wf.steps),
            "max_total_time": wf.max_total_time,
            "estimated_cost_usd": f"${estimated_cost_low:.2f}–${estimated_cost_high:.2f}",
        },
        "inputs_required": [
            {"name": i.name, "type": i.type, "required": i.required, "default": i.default}
            for i in wf.inputs
        ],
        "inputs_provided": list(inputs.keys()),
        "warnings": [w.to_dict() for w in vr.warnings],
    }


# ---------------------------------------------------------------------------
# Cost guard — check budget before and during execution
# ---------------------------------------------------------------------------

def _check_cost_guard(
    max_cost_usd: float | None = None,
) -> str | None:
    """Check if budget allows workflow execution. Returns error string or None."""
    if max_cost_usd is None:
        return None
    try:
        from ..cost_tracker import CostTracker
        from ..operator_mcp import COST_TRACKER
        exceeded = COST_TRACKER.check_budget(
            max_session_usd=max_cost_usd,
        )
        if exceeded:
            return (
                f"Budget exceeded: {exceeded['exceeded']} limit "
                f"${exceeded['limit_usd']:.2f}, actual ${exceeded['actual_usd']:.2f}"
            )
        return None
    except Exception:
        return None  # Don't block on cost tracker errors


# ---------------------------------------------------------------------------
# Run-to-step ancestor closure
# ---------------------------------------------------------------------------

def compute_ancestor_closure(wf: WorkflowDef, target_step_id: str) -> set[str]:
    """Return the transitive ancestor closure of ``target_step_id`` (inclusive).

    Walks ``depends_on`` edges via BFS, applying these wrapper rules so a
    "run to here" never silently no-ops or runs against a fragmentary state:

      - **Parallel/for_each child → wrapper**: a body step implicitly depends
        on its wrapper. Reaching the child pulls the wrapper in (and the
        wrapper's own depends_on chain). Sibling children are NOT pulled in
        — the user picked a single branch.
      - **Downstream consumer → wrapper → all children**: when a step in
        closure depends_on a wrapper directly (or via the implicit child
        rule recursively), that wrapper's complete child list is pulled
        in. Otherwise the join sees zero children and the run reports a
        false-green "0 successful out of 0 expected" (target downstream of
        a parallel scenario from the codex review).

    Returns an empty set when the target id doesn't exist in ``wf`` — callers
    must check this explicitly (``execute_workflow`` does and fails the run).

    The returned set always includes ``target_step_id`` itself when the target
    is valid, even when the target has no ancestors at all.
    """
    closure: set[str] = set()
    if not wf.step_by_id(target_step_id):
        return closure

    # Build reverse map: child_id -> wrappers that own it. Used for the
    # implicit "child depends on wrapper" rule.
    parent_wrappers: dict[str, set[str]] = {}
    for s in wf.steps:
        if s.type == StepType.PARALLEL and s.parallel:
            for child_id in s.parallel.steps:
                parent_wrappers.setdefault(child_id, set()).add(s.id)
        elif s.type == StepType.FOR_EACH and s.for_each:
            for child_id in s.for_each.steps:
                parent_wrappers.setdefault(child_id, set()).add(s.id)

    # Track wrappers that were pulled in *because something explicitly
    # depends_on them* (not just because we reached one of their children
    # via the implicit child→wrapper rule). Only these wrappers expand
    # their child list — a target that IS a wrapper child shouldn't drag
    # in its siblings.
    consumed_wrappers: set[str] = set()

    # BFS up the dependency DAG.
    queue: list[str] = [target_step_id]
    while queue:
        sid = queue.pop()
        if sid in closure:
            continue
        closure.add(sid)
        step = wf.step_by_id(sid)
        if not step:
            continue
        for dep in step.depends_on:
            dep_step = wf.step_by_id(dep)
            if dep_step and dep_step.type in (StepType.PARALLEL, StepType.FOR_EACH):
                # Reached this wrapper via an explicit consumer dependency
                # — record so the post-pass expands its children.
                consumed_wrappers.add(dep)
            if dep not in closure:
                queue.append(dep)
        for wrapper in parent_wrappers.get(sid, ()):
            if wrapper not in closure:
                queue.append(wrapper)

    # Post-pass: for each wrapper reached via an explicit consumer, pull in
    # every body step. Re-feed through the BFS so any new ancestors of those
    # children come along too.
    expand_queue: list[str] = []
    for wrapper_id in consumed_wrappers:
        wstep = wf.step_by_id(wrapper_id)
        if not wstep:
            continue
        if wstep.type == StepType.PARALLEL and wstep.parallel:
            for child_id in wstep.parallel.steps:
                if child_id not in closure:
                    expand_queue.append(child_id)
        elif wstep.type == StepType.FOR_EACH and wstep.for_each:
            for child_id in wstep.for_each.steps:
                if child_id not in closure:
                    expand_queue.append(child_id)

    while expand_queue:
        sid = expand_queue.pop()
        if sid in closure:
            continue
        closure.add(sid)
        step = wf.step_by_id(sid)
        if not step:
            continue
        for dep in step.depends_on:
            dep_step = wf.step_by_id(dep)
            if dep_step and dep_step.type in (StepType.PARALLEL, StepType.FOR_EACH):
                # If a child has its own depends_on chain that goes through
                # another wrapper, that wrapper too must expand fully.
                if dep not in consumed_wrappers:
                    consumed_wrappers.add(dep)
                    if dep_step.type == StepType.PARALLEL and dep_step.parallel:
                        for cid in dep_step.parallel.steps:
                            if cid not in closure:
                                expand_queue.append(cid)
                    elif dep_step.type == StepType.FOR_EACH and dep_step.for_each:
                        for cid in dep_step.for_each.steps:
                            if cid not in closure:
                                expand_queue.append(cid)
            if dep not in closure:
                expand_queue.append(dep)
        for wrapper in parent_wrappers.get(sid, ()):
            if wrapper not in closure:
                expand_queue.append(wrapper)
    return closure


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

async def execute_workflow(
    wf: WorkflowDef,
    inputs: dict[str, Any],
    cwd: str,
    *,
    run_id: str | None = None,
    resume_state: WorkflowState | None = None,
    max_cost_usd: float | None = None,
    trigger_context: dict[str, str] | None = None,
    workflow_item_kref: str = "",
    workflow_revision_kref: str = "",
    target_step_id: str | None = None,
) -> WorkflowState:
    """Execute a workflow definition.

    Args:
        wf: Validated workflow definition.
        inputs: Input parameters matching wf.inputs.
        cwd: Working directory for agent/shell steps.
        run_id: Optional run ID (generated if not provided).
        resume_state: Optional state to resume from checkpoint.
        max_cost_usd: Optional cost cap — abort if session cost exceeds this.
        target_step_id: Optional step id for the "run to here" feature. When
            set, only steps in the transitive ancestor closure of this step
            (plus the step itself) are executed; the loop terminates as soon
            as the target completes — descendants are not run, all ancestors
            re-run fresh.

    Returns:
        Final WorkflowState with all step results.
    """
    # Validate first
    vr = validate_workflow(wf)
    if not vr.valid:
        state = WorkflowState(
            workflow_name=wf.name,
            run_id=run_id or str(uuid.uuid4()),
            status=WorkflowStatus.FAILED,
            error=f"Validation failed: {vr.errors[0].message}",
            workflow_item_kref=workflow_item_kref,
            workflow_revision_kref=workflow_revision_kref,
        )
        return state

    # Run-to-step: hard-fail unknown target ids here so the executor never
    # silently runs the entire workflow when the gateway/poller passes a
    # stale or typo'd step id (an empty closure used to fall through to
    # full-run mode — a "run 3 steps, burned 25" footgun).
    effective_target_step_id: str | None = (
        target_step_id if target_step_id else (resume_state.target_step_id if resume_state else None)
    )
    if effective_target_step_id and not wf.step_by_id(effective_target_step_id):
        return WorkflowState(
            workflow_name=wf.name,
            run_id=run_id or str(uuid.uuid4()),
            status=WorkflowStatus.FAILED,
            error=f"unknown_target_step: '{effective_target_step_id}'",
            workflow_item_kref=workflow_item_kref,
            workflow_revision_kref=workflow_revision_kref,
        )

    # Propagate the workflow-level default_timeout to any step config that
    # has a `timeout` field but didn't set one explicitly. Without this the
    # workflow-level setting is dead config and per-step timeouts silently
    # fall back to their schema default (300s).
    for s in wf.steps:
        cfg = s.get_config()
        if cfg is None:
            continue
        if "timeout" in type(cfg).model_fields and "timeout" not in cfg.model_fields_set:
            cfg.timeout = wf.default_timeout

    # Pre-flight cost check
    cost_err = _check_cost_guard(max_cost_usd)
    if cost_err:
        return WorkflowState(
            workflow_name=wf.name,
            run_id=run_id or str(uuid.uuid4()),
            status=WorkflowStatus.FAILED,
            error=f"Cost guard: {cost_err}",
            workflow_item_kref=workflow_item_kref,
            workflow_revision_kref=workflow_revision_kref,
        )

    # Initialize or resume state
    if resume_state:
        state = resume_state
        state.status = WorkflowStatus.RUNNING
        # Resumed runs keep their originally pinned krefs — do not let a fresh
        # resolution overwrite them. Only fill if the stored state is empty.
        if not state.workflow_item_kref and workflow_item_kref:
            state.workflow_item_kref = workflow_item_kref
        if not state.workflow_revision_kref and workflow_revision_kref:
            state.workflow_revision_kref = workflow_revision_kref
        # If the caller passed an explicit target_step_id (e.g. recovery
        # propagating the persisted value), refresh state so the closure is
        # rebuilt from it. Otherwise fall back to whatever the persisted
        # state recorded — the persisted value IS the source of truth across
        # resume.
        if target_step_id:
            state.target_step_id = target_step_id
    else:
        # Merge declared input defaults with caller-provided values.
        # Caller values win; defaults fill in anything not explicitly passed.
        merged_inputs: dict[str, Any] = {
            d.name: d.default for d in wf.inputs if d.default is not None
        }
        merged_inputs.update(inputs or {})
        state = WorkflowState(
            workflow_name=wf.name,
            run_id=run_id or str(uuid.uuid4()),
            status=WorkflowStatus.RUNNING,
            inputs=merged_inputs,
            started_at=datetime.now(timezone.utc).isoformat(),
            trigger_context=trigger_context or {},
            workflow_item_kref=workflow_item_kref,
            workflow_revision_kref=workflow_revision_kref,
            target_step_id=target_step_id or None,
        )

    # Claim a per-run file lock BEFORE registering in ACTIVE_WORKFLOWS.
    # This prevents duplicate execution across operator processes.
    _run_lock_fd = None
    if not resume_state:  # recovery already holds its own lock
        from .recovery import _acquire_run_lock
        _run_lock_fd = _acquire_run_lock(state.run_id)
        if _run_lock_fd is None:
            _log(f"workflow: run={state.run_id[:8]} already claimed by another process, skipping")
            state.status = WorkflowStatus.CANCELLED
            state.error = "Duplicate execution prevented by run lock"
            return state

    ACTIVE_WORKFLOWS[state.run_id] = state

    effective_cwd = cwd or wf.default_cwd or "/tmp"
    execution_order = vr.execution_order
    start_time = time.monotonic()

    _log(f"workflow: starting '{wf.name}' run={state.run_id[:8]} steps={len(wf.steps)}")

    # Persist a "running" entry so the UI sees the run immediately
    if not resume_state:
        try:
            from .memory import persist_workflow_run
            await persist_workflow_run(
                workflow_name=state.workflow_name,
                run_id=state.run_id,
                status="running",
                inputs=state.inputs,
                step_results={},
                started_at=state.started_at,
                steps_total=len(wf.steps),
                workflow_item_kref=state.workflow_item_kref,
                workflow_revision_kref=state.workflow_revision_kref,
            )
        except Exception as exc:
            _log(f"workflow: failed to persist initial run entry (non-fatal): {exc}")

    # Control-flow step types that must run alone (they jump/pause/branch)
    _CONTROL_FLOW_TYPES = frozenset({
        StepType.CONDITIONAL, StepType.GOTO,
        StepType.HUMAN_APPROVAL, StepType.HUMAN_INPUT,
        StepType.FOR_EACH,
    })

    try:
        # Collect for_each sub-step IDs — these are executed internally by
        # _exec_for_each and must be excluded from the main execution loop.
        # Also transitively exclude parallel sub-steps nested inside for_each.
        _for_each_owned: set[str] = set()
        for s in wf.steps:
            if s.type == StepType.FOR_EACH and s.for_each:
                for sub_id in s.for_each.steps:
                    _for_each_owned.add(sub_id)
                    sub_step = wf.step_by_id(sub_id)
                    if sub_step and sub_step.type == StepType.PARALLEL and sub_step.parallel:
                        _for_each_owned.update(sub_step.parallel.steps)

        # Run-to-step closure — set of step ids permitted to run when the
        # caller pinned a target. Empty means "no restriction". Mirrored onto
        # state.run_to_closure so step handlers (parallel, for_each) can read
        # it without having to thread an extra parameter through every
        # dispatch path.
        #
        # Source of truth is ``state.target_step_id`` (persisted across
        # checkpoint+resume). The kwarg-passed ``target_step_id`` was already
        # written into state above; reading from state here means a recovered
        # run honours its original target even when the resumed
        # ``execute_workflow`` call doesn't repeat the kwarg.
        run_to_closure: set[str] = set()
        active_target = state.target_step_id
        if active_target:
            run_to_closure = compute_ancestor_closure(wf, active_target)
            _log(
                f"workflow: run_to target='{active_target}' "
                f"closure={sorted(run_to_closure)}"
            )
        state.run_to_closure = run_to_closure

        # Collect all step IDs into a set for tracking
        remaining = set(execution_order) - _for_each_owned
        if run_to_closure:
            remaining &= run_to_closure

        while remaining:
            # Time guard
            elapsed = time.monotonic() - start_time
            if elapsed > wf.max_total_time:
                state.status = WorkflowStatus.FAILED
                state.error = f"Exceeded max_total_time ({wf.max_total_time}s)"
                break

            # Mid-execution cost guard
            cost_err = _check_cost_guard(max_cost_usd)
            if cost_err:
                state.status = WorkflowStatus.FAILED
                state.error = f"Cost guard (mid-run): {cost_err}"
                break

            # Cancellation check — react to either an externally-set
            # CANCELLED status (legacy direct flip) or a cancel_requested
            # signal from the cancel_workflow MCP tool. The signal path is
            # the canonical one: the executor processes it cleanly, kills
            # any owned subprocesses, and transitions to CANCELLED.
            if state.cancel_requested or state.status == WorkflowStatus.CANCELLED:
                if state.cancel_requested:
                    _log(f"workflow: cancel_requested observed for run={state.run_id[:8]}")
                state.status = WorkflowStatus.CANCELLED
                if not state.error:
                    state.error = "Cancelled by user"
                # Kill any subprocesses owned by this run (shell/python steps).
                # Step handlers also poll cancel_requested independently, but
                # the explicit kill here covers any handler that hasn't yet
                # noticed (e.g. parallel batch where one step finishes fast).
                for p in list(state.running_processes):
                    _kill_proc(p)
                break

            # Find all ready steps: deps satisfied and not yet completed
            ready: list[str] = []
            for step_id in execution_order:
                if step_id not in remaining:
                    continue
                existing = state.step_results.get(step_id)
                if existing and existing.status in ("completed", "skipped"):
                    remaining.discard(step_id)
                    continue
                step = wf.step_by_id(step_id)
                if not step:
                    remaining.discard(step_id)
                    continue
                # In run-to-step mode, deps that are excluded from the
                # closure are treated as already-satisfied (they aren't going
                # to run, so don't block their dependents).
                deps_ok = all(
                    (run_to_closure and dep not in run_to_closure)
                    or state.step_results.get(dep, StepResult(step_id=dep)).status == "completed"
                    for dep in step.depends_on
                )
                if deps_ok:
                    ready.append(step_id)

            if not ready:
                # No steps are ready but some remain — deps can't be satisfied
                for sid in list(remaining):
                    state.step_results[sid] = StepResult(
                        step_id=sid, status="skipped",
                        error="Dependencies not satisfied",
                    )
                remaining.clear()
                break

            # Separate control-flow steps (must run alone) from parallelisable ones
            parallel_batch: list[str] = []
            control_step: str | None = None
            for sid in ready:
                step = wf.step_by_id(sid)
                if step and step.type in _CONTROL_FLOW_TYPES:
                    control_step = sid
                    break
                parallel_batch.append(sid)

            if control_step:
                # Run the control-flow step alone
                step = wf.step_by_id(control_step)
                assert step is not None
                state.current_step = control_step
                _log(f"workflow: executing step '{control_step}' ({step.type.value})")

                result = await _execute_step_with_retry(step, state, effective_cwd, wf)
                state.step_results[control_step] = result
                remaining.discard(control_step)

                # Handle control flow
                if step.type == StepType.CONDITIONAL:
                    next_step = _resolve_conditional(step, state)
                    if next_step == "end":
                        break
                    # Run-to-step: log when every branch points outside the
                    # closure. The conditional's match still records onto
                    # state.conditional_routes for downstream interpolation,
                    # but no goto-style jump happens here so this is purely
                    # diagnostic.
                    if (
                        run_to_closure
                        and isinstance(next_step, str)
                        and next_step not in run_to_closure
                        and next_step != "end"
                    ):
                        _log(
                            f"workflow: run_to conditional '{control_step}' "
                            f"matched goto='{next_step}' outside closure (no-op)"
                        )

                elif step.type == StepType.GOTO:
                    cfg_goto: GotoStepConfig = step.goto  # type: ignore
                    count = state.iteration_counts.get(control_step, 0) + 1
                    state.iteration_counts[control_step] = count
                    # Update the StepResult input_data with the now-incremented
                    # iteration count so the run-view reflects the iteration
                    # this dispatch represents (1, 2, 3...) rather than the
                    # pre-increment value captured inside _exec_goto.
                    if isinstance(result.input_data, dict):
                        result.input_data["current_iteration"] = count
                    if count <= cfg_goto.max_iterations:
                        should_goto = True
                        if cfg_goto.condition:
                            should_goto = _eval_condition(cfg_goto.condition, state)
                        # Run-to-step mode: if the goto target is outside the
                        # closure, treat the jump as a no-op (a partial loop
                        # back-edge would either re-run already-completed
                        # ancestors or jump into territory we never planned
                        # to execute). Log so the user can debug.
                        if (
                            should_goto
                            and run_to_closure
                            and cfg_goto.target not in run_to_closure
                        ):
                            _log(
                                f"workflow: run_to skipping goto '{control_step}' -> "
                                f"'{cfg_goto.target}' (target outside closure)"
                            )
                            should_goto = False
                        if should_goto and cfg_goto.target in execution_order:
                            target_idx = execution_order.index(cfg_goto.target)
                            for clear_idx in range(target_idx, len(execution_order)):
                                clear_sid = execution_order[clear_idx]
                                if clear_sid == control_step:
                                    break
                                state.step_results.pop(clear_sid, None)
                                remaining.add(clear_sid)
                            _log(f"workflow: goto '{control_step}' -> '{cfg_goto.target}' (iteration {count}/{cfg_goto.max_iterations})")

                elif step.type in (StepType.HUMAN_APPROVAL, StepType.HUMAN_INPUT):
                    if result.status != "completed":
                        label = "approval" if step.type == StepType.HUMAN_APPROVAL else "input"
                        state.status = WorkflowStatus.PAUSED
                        state.error = f"Awaiting human {label}"
                        if wf.checkpoint:
                            _save_checkpoint(state)
                        ACTIVE_WORKFLOWS[state.run_id] = state
                        return state

                elif step.type == StepType.FOR_EACH:
                    if result.status == "pending":
                        # for_each paused internally (e.g. human_approval inside the loop).
                        # State is already PAUSED and checkpointed by _exec_for_each.
                        ACTIVE_WORKFLOWS[state.run_id] = state
                        return state

                if result.status == "failed" and step.type not in (
                    StepType.CONDITIONAL, StepType.GOTO, StepType.OUTPUT
                ):
                    # If the failure was the cooperative cancel signal,
                    # transition to CANCELLED rather than FAILED. The
                    # main-loop top-of-iteration check handles this on the
                    # next pass too, but we'd already break out as FAILED
                    # without this guard.
                    if state.cancel_requested:
                        state.status = WorkflowStatus.CANCELLED
                        if not state.error:
                            state.error = "Cancelled by user"
                    else:
                        state.status = WorkflowStatus.FAILED
                        state.error = f"Step '{control_step}' failed: {result.error[:500]}"
                    break

            else:
                # Run all ready non-control-flow steps in parallel
                batch_ids = parallel_batch
                _log(f"workflow: executing {len(batch_ids)} step(s) in parallel: {batch_ids}")

                async def _run_one(sid: str) -> tuple[str, StepResult]:
                    s = wf.step_by_id(sid)
                    assert s is not None
                    r = await _execute_step_with_retry(s, state, effective_cwd, wf)
                    # Persist this step immediately so recovery can see it
                    # even if the executor is killed before the batch finishes.
                    state.step_results[sid] = r
                    try:
                        from .memory import persist_workflow_run
                        step_dicts = {
                            k: v.model_dump() for k, v in state.step_results.items()
                        }
                        await persist_workflow_run(
                            workflow_name=state.workflow_name,
                            run_id=state.run_id,
                            status="running",
                            inputs=state.inputs,
                            step_results=step_dicts,
                            started_at=state.started_at,
                            steps_total=len(wf.steps),
                            workflow_item_kref=state.workflow_item_kref,
                            workflow_revision_kref=state.workflow_revision_kref,
                        )
                    except Exception as exc:
                        _log(f"workflow: per-step persist failed for '{sid}': {exc}")
                    return sid, r

                results = await asyncio.gather(
                    *[_run_one(sid) for sid in batch_ids],
                    return_exceptions=True,
                )

                failed_step: str | None = None
                for item in results:
                    if isinstance(item, BaseException):
                        _log(f"workflow: parallel step raised: {item}")
                        continue
                    sid, result = item
                    state.step_results[sid] = result
                    remaining.discard(sid)
                    if result.status == "failed":
                        step = wf.step_by_id(sid)
                        if step and step.type not in (
                            StepType.CONDITIONAL, StepType.GOTO, StepType.OUTPUT
                        ):
                            failed_step = sid

                if failed_step:
                    # Mid-step cancel: parallel step handlers return
                    # status="failed" with "Cancelled by user". Don't let
                    # that mask the cancel — surface the correct terminal
                    # state.
                    if state.cancel_requested:
                        state.status = WorkflowStatus.CANCELLED
                        if not state.error:
                            state.error = "Cancelled by user"
                    else:
                        fr = state.step_results[failed_step]
                        state.status = WorkflowStatus.FAILED
                        state.error = f"Step '{failed_step}' failed: {fr.error[:500]}"
                    break

            # Run-to-step: stop the loop as soon as the pinned target has
            # completed. We don't walk descendants in this mode — the run is
            # done. Place this BEFORE the post-wave checkpoint so the next
            # wave never starts spinning up steps we don't intend to run.
            if active_target and active_target in state.step_results:
                tgt_result = state.step_results[active_target]
                if tgt_result.status in ("completed", "skipped"):
                    _log(
                        f"workflow: run_to target '{active_target}' reached "
                        f"({tgt_result.status}); terminating"
                    )
                    remaining.clear()

            # Checkpoint + incremental persist after each wave
            if wf.checkpoint:
                _save_checkpoint(state)
            try:
                from .memory import persist_workflow_run
                step_dicts = {
                    sid: sr.model_dump() for sid, sr in state.step_results.items()
                }
                _log(f"workflow: incremental persist {len(step_dicts)} step(s) for run={state.run_id[:8]}")
                await persist_workflow_run(
                    workflow_name=state.workflow_name,
                    run_id=state.run_id,
                    status="running",
                    inputs=state.inputs,
                    step_results=step_dicts,
                    started_at=state.started_at,
                    steps_total=len(wf.steps),
                    workflow_item_kref=state.workflow_item_kref,
                    workflow_revision_kref=state.workflow_revision_kref,
                )
            except Exception as exc:
                _log(f"workflow: incremental persist failed: {exc}")

        # Finalize
        if state.status == WorkflowStatus.RUNNING:
            state.status = WorkflowStatus.COMPLETED
        state.completed_at = datetime.now(timezone.utc).isoformat()
        state.current_step = None

        if wf.checkpoint:
            _save_checkpoint(state)

        # Persist to Kumiho memory (best-effort, fire-and-forget)
        if state.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED):
            try:
                from .memory import persist_workflow_run, link_agents_to_run
                step_dicts = {
                    sid: sr.model_dump() for sid, sr in state.step_results.items()
                }
                run_kref = await persist_workflow_run(
                    workflow_name=state.workflow_name,
                    run_id=state.run_id,
                    status=state.status.value,
                    inputs=state.inputs,
                    step_results=step_dicts,
                    started_at=state.started_at,
                    completed_at=state.completed_at,
                    error=state.error,
                    workflow_item_kref=state.workflow_item_kref,
                    workflow_revision_kref=state.workflow_revision_kref,
                )
                if run_kref:
                    await link_agents_to_run(run_kref, step_dicts)
            except Exception as mem_exc:
                _log(f"workflow: memory persist failed (non-fatal): {mem_exc}")

    except Exception as exc:
        state.status = WorkflowStatus.FAILED
        state.error = f"Executor error: {str(exc)[:1000]}"
        _log(f"workflow: executor error: {exc}")
    finally:
        # Clean up terminal workflows so they don't linger in the active
        # registry (which feeds the dashboard "active runs" count).
        # Only keep truly active states (running/paused) in the dict.
        if state.status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        ):
            ACTIVE_WORKFLOWS.pop(state.run_id, None)
        else:
            ACTIVE_WORKFLOWS[state.run_id] = state

        # Release per-run file lock so recovery can claim this run
        # if we crash after this point.
        if _run_lock_fd is not None:
            try:
                from .recovery import _release_run_lock
                _release_run_lock(_run_lock_fd, state.run_id)
            except Exception:
                pass

    _log(f"workflow: '{wf.name}' run={state.run_id[:8]} → {state.status.value}")
    return state


# ---------------------------------------------------------------------------
# Step dispatch with retry
# ---------------------------------------------------------------------------

async def _execute_step_with_retry(
    step: StepDef,
    state: WorkflowState,
    cwd: str,
    wf: WorkflowDef,
) -> StepResult:
    """Execute a step, retrying on failure up to step.retry times."""
    last_result: StepResult | None = None

    for attempt in range(step.retry + 1):
        t0 = time.monotonic()
        result = await _dispatch_step(step, state, cwd, wf)
        result.duration_s = round(time.monotonic() - t0, 2)
        result.retries_used = attempt

        if result.status == "completed" or step.type in (
            StepType.CONDITIONAL, StepType.GOTO,
            StepType.HUMAN_APPROVAL, StepType.HUMAN_INPUT,
            StepType.FOR_EACH,
        ):
            return result

        last_result = result
        if attempt < step.retry:
            _log(f"workflow: step '{step.id}' failed (attempt {attempt+1}), retrying...")
            await asyncio.sleep(step.retry_delay)

    return last_result or StepResult(step_id=step.id, status="failed", error="No result")


async def _dispatch_step(
    step: StepDef,
    state: WorkflowState,
    cwd: str,
    wf: WorkflowDef,
) -> StepResult:
    """Route a step to its type-specific executor."""
    try:
        if step.type == StepType.AGENT:
            return await _exec_agent(step, state, cwd)
        elif step.type == StepType.SHELL:
            return await _exec_shell(step, state, cwd)
        elif step.type == StepType.PYTHON:
            return await _exec_python(step, state, cwd)
        elif step.type == StepType.EMAIL:
            return await _exec_email(step, state)
        elif step.type == StepType.IMAGE:
            return await _exec_image(step, state, cwd)
        elif step.type == StepType.OUTPUT:
            return await _exec_output(step, state)
        elif step.type == StepType.A2A:
            return await _exec_a2a(step, state)
        elif step.type == StepType.PARALLEL:
            return await _exec_parallel(step, state, cwd, wf)
        elif step.type == StepType.CONDITIONAL:
            return _exec_conditional(step, state)
        elif step.type == StepType.GOTO:
            return _exec_goto(step, state)
        elif step.type == StepType.HUMAN_APPROVAL:
            return await _exec_human_approval(step, state)
        elif step.type == StepType.HUMAN_INPUT:
            return await _exec_human_input(step, state)
        elif step.type == StepType.NOTIFY:
            return await _exec_notify(step, state)
        # Orchestration patterns
        elif step.type == StepType.MAP_REDUCE:
            return await _exec_map_reduce(step, state, cwd)
        elif step.type == StepType.SUPERVISOR:
            return await _exec_supervisor(step, state, cwd)
        elif step.type == StepType.GROUP_CHAT:
            return await _exec_group_chat(step, state, cwd)
        elif step.type == StepType.HANDOFF:
            return await _exec_handoff(step, state, cwd)
        elif step.type == StepType.RESOLVE:
            return await _exec_resolve(step, state)
        elif step.type == StepType.FOR_EACH:
            return await _exec_for_each(step, state, cwd, wf)
        elif step.type == StepType.TAG:
            return await _exec_tag(step, state)
        elif step.type == StepType.DEPRECATE:
            return await _exec_deprecate(step, state)
        else:
            return StepResult(
                step_id=step.id, status="failed",
                error=f"Unknown step type: {step.type}",
            )
    except Exception as exc:
        return StepResult(
            step_id=step.id, status="failed",
            error=f"Step execution error: {str(exc)[:2000]}",
        )


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------

async def _exec_parallel(
    step: StepDef,
    state: WorkflowState,
    cwd: str,
    wf: WorkflowDef,
) -> StepResult:
    """Execute parallel sub-steps with join strategy."""
    from .schema import ParallelStepConfig
    cfg: ParallelStepConfig = step.parallel  # type: ignore

    # Run-to-step: skip children outside the closure. Sibling children that
    # aren't ancestors of the target should NOT run when the user picks one
    # branch via "run to here". The join is computed against the filtered
    # set so a `join: all` parallel with one selected child still passes.
    #
    # If ALL listed children are in the closure (typical for a target
    # downstream of the parallel — compute_ancestor_closure pulls them all
    # back in), don't filter at all. Filtering an empty subset would give us
    # an empty `sub_ids` and the join would falsely report "0/0 success".
    if state.run_to_closure:
        in_closure = [s for s in cfg.steps if s in state.run_to_closure]
        if len(in_closure) == len(cfg.steps) or not in_closure:
            # Either everything is in scope (run normally) or nothing is
            # (would mean the parallel itself isn't in closure — but the
            # main loop wouldn't have dispatched us in that case). Either
            # way, run the canonical list.
            sub_ids = list(cfg.steps)
        else:
            sub_ids = in_closure
            _log(
                f"workflow: run_to parallel '{step.id}' filtered "
                f"{len(cfg.steps) - len(sub_ids)} non-closure children"
            )
    else:
        sub_ids = list(cfg.steps)

    semaphore = asyncio.Semaphore(cfg.max_concurrency)
    results: dict[str, StepResult] = {}

    async def run_sub(sub_id: str) -> None:
        sub_step = wf.step_by_id(sub_id)
        if not sub_step:
            results[sub_id] = StepResult(
                step_id=sub_id, status="failed", error=f"Step '{sub_id}' not found",
            )
            return
        async with semaphore:
            r = await _dispatch_step(sub_step, state, cwd, wf)
            results[sub_id] = r
            state.step_results[sub_id] = r

    tasks = [asyncio.create_task(run_sub(sid)) for sid in sub_ids]
    await asyncio.gather(*tasks, return_exceptions=True)

    # Apply join strategy — total reflects the filtered set so a
    # run-to-step partial parallel doesn't always fail `join: all`.
    # ParallelStepConfig.steps rejects duplicate child refs at parse time,
    # so `total == len(set(sub_ids))` is guaranteed and the dict-keyed
    # `results` map can never under-count.
    completed = [r for r in results.values() if r.status == "completed"]
    total = len(sub_ids)

    if cfg.join == JoinStrategy.ALL:
        success = len(completed) == total
    elif cfg.join == JoinStrategy.ANY:
        success = len(completed) > 0
    elif cfg.join == JoinStrategy.MAJORITY:
        success = len(completed) > total / 2
    else:
        success = len(completed) == total

    combined_output = "\n---\n".join(
        f"[{r.step_id}]: {r.output[:1000]}" for r in results.values()
    )

    return StepResult(
        step_id=step.id,
        status="completed" if success else "failed",
        output=combined_output[:6000],
        output_data={
            "sub_results": {sid: r.status for sid, r in results.items()},
            "completed": len(completed),
            "total": total,
        },
        files_touched=[f for r in results.values() for f in r.files_touched],
    )


# ---------------------------------------------------------------------------
# Control flow helpers
# ---------------------------------------------------------------------------

def _exec_goto(step: StepDef, state: WorkflowState) -> StepResult:
    """Capture goto step inputs for the run-view UI.

    The actual jump still happens in the wave loop at executor.py:2847 —
    this handler only records what the step is configured to do plus the
    current iteration count so the dashboard can render a useful detail
    panel ("goto refine, iteration 2/3") instead of "Step completed".
    """
    cfg: GotoStepConfig = step.goto or GotoStepConfig(target="")  # type: ignore
    current_iter = state.iteration_counts.get(step.id, 0)
    input_data: dict[str, Any] = {
        "target": cfg.target,
        "max_iterations": cfg.max_iterations,
        "current_iteration": current_iter,
        "condition": cfg.condition or "",
    }
    return StepResult(
        step_id=step.id,
        status="completed",
        input_data=input_data,
    )


def _exec_conditional(step: StepDef, state: WorkflowState) -> StepResult:
    """Resolve which branch matches and emit its `value` (if any) as output.

    Branch resolution happens here so the matched goto + emitted value are
    visible in the StepResult — downstream steps reading ``${gate.output}``
    see the matched branch's value, and the wave loop reads the cached
    goto from ``state.conditional_routes`` via _resolve_conditional.
    """
    from .schema import ConditionalStepConfig
    cfg: ConditionalStepConfig = step.conditional  # type: ignore
    if not cfg:
        return StepResult(step_id=step.id, status="completed")

    matched_goto: str | None = None
    matched_idx: int = -1
    matched_condition: str = ""
    matched_value_expr: str | None = None
    output: str = ""
    for idx, branch in enumerate(cfg.branches):
        if _eval_condition(branch.condition, state):
            matched_goto = branch.goto
            matched_idx = idx
            matched_condition = branch.condition
            matched_value_expr = branch.value
            output = _eval_branch_value(branch.value, state)
            break

    # Cache the matched goto on the workflow state, NOT on output_data.
    # Stashing it in output_data would expose it via ${gate.output_data.*}
    # interpolation and as a name in the simpleeval evaluator.
    if matched_goto is not None:
        state.conditional_routes[step.id] = matched_goto
    else:
        state.conditional_routes.pop(step.id, None)

    input_data: dict[str, Any] = {
        "branch_count": len(cfg.branches),
        "matched_branch_index": matched_idx,
        "matched_condition": matched_condition,
        "matched_value_expr": matched_value_expr,
    }
    output_data: dict[str, Any] = {}
    if matched_goto is not None:
        output_data["matched_goto"] = matched_goto

    return StepResult(
        step_id=step.id,
        status="completed",
        output=output,
        input_data=input_data,
        output_data=output_data,
    )


def _resolve_conditional(step: StepDef, state: WorkflowState) -> str | None:
    """Return the goto target chosen by `_exec_conditional`.

    Reads the cached match from ``state.conditional_routes`` so we don't
    re-evaluate (which would also re-trigger any side-effecting access to
    state). Falls back to fresh evaluation if the cache entry is missing.
    """
    cached = state.conditional_routes.get(step.id)
    if isinstance(cached, str):
        return cached

    from .schema import ConditionalStepConfig
    cfg: ConditionalStepConfig = step.conditional  # type: ignore
    if not cfg:
        return None
    for branch in cfg.branches:
        if _eval_condition(branch.condition, state):
            return branch.goto
    return None


async def _exec_human_approval(step: StepDef, state: WorkflowState) -> StepResult:
    """Handle human approval — always pauses workflow for external approval.

    The workflow resumes when the user calls resume_workflow with approval.
    Pushes a notification to the configured channel (dashboard/discord/slack).
    """
    from .schema import HumanApprovalConfig
    cfg: HumanApprovalConfig = step.human_approval or HumanApprovalConfig()  # type: ignore
    message = interpolate(cfg.message, state)

    # Push approval request to the configured channel via gateway
    try:
        from ..gateway_client import ConstructGatewayClient
        gw = ConstructGatewayClient()
        if gw._available:
            channels = [cfg.channel] if cfg.channel else ["dashboard"]
            event: dict[str, Any] = {
                "type": "human_approval_request",
                "channels": channels,
                "content": {
                    "title": f"Approval Required: {step.name or step.id}",
                    "message": message,
                },
                "run_id": state.run_id,
                "step_id": step.id,
                "timeout": cfg.timeout,
                "workflow_name": state.workflow_name,
                "approve_keywords": cfg.approve_keywords,
                "reject_keywords": cfg.reject_keywords,
            }
            if cfg.channel_id:
                event["channel_id"] = interpolate(cfg.channel_id, state)
            await gw.push_channel_event(event)
            _log(f"_exec_human_approval: pushed to channels={channels}")
    except Exception as exc:
        _log(f"_exec_human_approval: channel push failed (non-fatal): {exc}")

    return StepResult(
        step_id=step.id,
        status="pending",  # Triggers PAUSED state in executor
        output=message,
        output_data={
            "awaiting_approval": True,
            "channel": cfg.channel,
            "approve_keywords": cfg.approve_keywords,
            "reject_keywords": cfg.reject_keywords,
        },
    )


async def _exec_notify(step: StepDef, state: WorkflowState) -> StepResult:
    """Fire-and-forget notification step.

    Pushes a workflow_notification event to one or more channels
    (dashboard/discord/slack/telegram) and returns immediately. Channel push
    failures are logged but never fail the workflow — notifications are
    best-effort.
    """
    from .schema import NotifyStepConfig
    cfg: NotifyStepConfig = step.notify or NotifyStepConfig()  # type: ignore
    message = interpolate(cfg.message, state)
    title = interpolate(cfg.title, state)
    channels = cfg.channels or ["dashboard"]

    input_data: dict[str, Any] = {
        "title": title,
        "message": message,
        "channels": channels,
    }
    if cfg.channel_id:
        input_data["channel_id"] = interpolate(cfg.channel_id, state)

    try:
        from ..gateway_client import ConstructGatewayClient
        gw = ConstructGatewayClient()
        if gw._available:
            event: dict[str, Any] = {
                "type": "workflow_notification",
                "channels": channels,
                "content": {
                    "title": title or f"Workflow: {state.workflow_name}",
                    "message": message,
                },
                "run_id": state.run_id,
                "step_id": step.id,
                "workflow_name": state.workflow_name,
            }
            if cfg.channel_id:
                event["channel_id"] = interpolate(cfg.channel_id, state)
            await gw.push_channel_event(event)
            _log(f"_exec_notify: pushed to channels={channels}")
    except Exception as exc:
        _log(f"_exec_notify: channel push failed (non-fatal): {exc}")

    return StepResult(
        step_id=step.id,
        status="completed",
        output=message,
        input_data=input_data,
        output_data={"channels": channels},
    )


async def _exec_human_input(step: StepDef, state: WorkflowState) -> StepResult:
    """Handle human input — pauses workflow and sends a prompt to a channel.

    Unlike human_approval (yes/no), this collects freeform text.  The
    response is piped into step output for downstream interpolation.
    """
    from .schema import HumanInputConfig

    cfg: HumanInputConfig = step.human_input or HumanInputConfig()  # type: ignore
    message = interpolate(cfg.message, state)

    # Push prompt to the requested channel via gateway
    try:
        from ..gateway_client import ConstructGatewayClient
        gw = ConstructGatewayClient()
        if gw._available:
            await gw.push_channel_event({
                "type": "human_input_request",
                "run_id": state.run_id,
                "step_id": step.id,
                "channel": cfg.channel,
                "message": message,
                "timeout": cfg.timeout,
            })
    except Exception as exc:
        _log(f"_exec_human_input: channel push failed (non-fatal): {exc}")

    return StepResult(
        step_id=step.id,
        status="pending",  # Triggers PAUSED state in executor
        output=message,
        action=step.action,
        output_data={
            "awaiting_input": True,
            "channel": cfg.channel,
            "timeout": cfg.timeout,
        },
    )
