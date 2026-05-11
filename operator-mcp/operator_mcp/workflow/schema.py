"""Pydantic models for Construct declarative workflow DSL.

Workflows are defined in YAML with typed steps, variable interpolation,
conditional branching, parallel execution, and checkpoint support.

Step types:
  - agent: Spawn a Construct agent (claude/codex) with a prompt.
  - shell: Run a shell command.
  - conditional: Branch based on expressions over prior step outputs.
  - parallel: Run multiple sub-steps concurrently with join strategies.
  - goto: Jump to another step (loop support with max_iterations guard).
  - human_approval: Pause for human confirmation before proceeding.
  - output: Emit structured output from the workflow.
  - a2a: Send a task to an external A2A agent.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class StepType(str, Enum):
    AGENT = "agent"
    SHELL = "shell"
    PYTHON = "python"
    EMAIL = "email"
    IMAGE = "image"
    CONDITIONAL = "conditional"
    PARALLEL = "parallel"
    GOTO = "goto"
    HUMAN_APPROVAL = "human_approval"
    HUMAN_INPUT = "human_input"
    NOTIFY = "notify"
    OUTPUT = "output"
    A2A = "a2a"
    # Orchestration patterns (Wave 2) as step types
    MAP_REDUCE = "map_reduce"
    SUPERVISOR = "supervisor"
    GROUP_CHAT = "group_chat"
    HANDOFF = "handoff"
    RESOLVE = "resolve"
    FOR_EACH = "for_each"
    TAG = "tag"
    DEPRECATE = "deprecate"
    MANUS = "manus"


class JoinStrategy(str, Enum):
    ALL = "all"          # Wait for all branches
    ANY = "any"          # First success wins
    MAJORITY = "majority"  # >50% must succeed


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"       # human_approval or error
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Step definitions
# ---------------------------------------------------------------------------

class QualityCheckConfig(BaseModel):
    """Config for post-agent quality validation. When attached to an agent step,
    a lightweight validator scores the output after execution. If the score is
    below threshold, the step fails — triggering retry with quality feedback."""
    enabled: bool = False
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)  # Minimum score to pass
    criteria: list[str] = Field(default_factory=list)  # What to check, e.g. ["on_mandate", "depth", "language_ko"]
    model: str = "claude-haiku-4-5-20251001"    # Lightweight model for scoring


class AgentStepConfig(BaseModel):
    """Config for 'agent' step type."""
    agent_type: Literal["claude", "codex"] = "claude"
    role: str = "coder"
    prompt: str = ""
    model: str | None = None
    timeout: float = 300.0       # 5 min default — synthesis-style Claude steps blow past 120s under real load
    template: str | None = None  # Pool template name
    max_turns: int = 3           # Max LLM turns (low default = no tool loops, saves tokens)
    tools: Literal["all", "memory", "none"] = "none"  # MCP tool injection level
    output_fields: list[str] = Field(default_factory=list)  # Expected structured fields in ```json block
    quality_check: QualityCheckConfig | None = None
    # Auth profile binding (encrypted credential, resolved at runtime via the
    # gateway's auth-profiles resolve endpoint). Format: "<provider>:<profile_name>".
    # Agent steps see this only via the get_auth_token MCP tool — never injected
    # into the system prompt or any other context.
    auth: str | None = None


class ShellStepConfig(BaseModel):
    """Config for 'shell' step type."""
    command: str
    timeout: float = 60.0
    allow_failure: bool = False  # If True, non-zero exit doesn't fail the workflow
    # Auth profile binding — resolved at runtime; the decrypted token is passed
    # to the subprocess via the CONSTRUCT_AUTH_TOKEN env var (kind in
    # CONSTRUCT_AUTH_KIND). Format: "<provider>:<profile_name>".
    auth: str | None = None


class EmailStepConfig(BaseModel):
    """Config for 'email' step type — send an outbound email via SMTP.

    Reads SMTP credentials from ``[channels_config.email]`` in
    ``~/.construct/config.toml`` by default (the same section the email
    channel uses for its inbox/SMTP). Per-step overrides are supported
    for fan-out workflows that send through multiple senders.

    Click tracking: when ``track_clicks`` is true and ``track_kref`` is
    provided, every plain ``http(s)`` URL in ``body`` (and ``body_html``
    if present) is rewritten to::

        <track_base_url>/track/c/<encoded_kref>?u=<urlquoted-original>

    The same encoded kref is shared by all links in this email — one
    click event per send. The kref is encoded with the optional secret
    in ``track_secret_env`` (env var name) for tamper detection. Workflow
    authors who want per-link granularity should encode multiple krefs
    upstream and write the URLs by hand instead.

    Dry run: when ``dry_run: true`` the step renders the message and
    stores the rendered output in ``output_data`` but does NOT connect
    to SMTP. Critical for outreach previews — let the operator review
    50 personalized emails before sending one.
    """
    to: str | list[str]
    subject: str
    body: str
    body_html: str | None = None
    from_address: str | None = None
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    reply_to: str | None = None

    # Click tracking
    track_clicks: bool = False
    track_kref: str | None = None  # Required when track_clicks=true
    track_secret_env: str = "CLICK_TRACKING_SECRET"  # env var name for HMAC secret
    track_base_url: str | None = None  # Default: from config / env GATEWAY_URL

    # SMTP overrides — by default we read from
    # ~/.construct/config.toml [channels_config.email].
    smtp_host: str | None = None
    smtp_port: int | None = None  # default: 465 if smtp_tls, else 587
    smtp_tls: bool | None = None  # default: true
    smtp_username: str | None = None
    smtp_password_env: str | None = None  # env var name; default uses config password

    timeout: float = 30.0
    dry_run: bool = False  # Render & return without sending — for previews
    # Auth profile binding — when set, the decrypted token overrides
    # smtp_password for this step only. Format: "<provider>:<profile_name>".
    auth: str | None = None


class ImageStepConfig(BaseModel):
    """Config for 'image' step type — generate image(s) via codex CLI and
    register them as Kumiho artifacts + push to Live Canvas.

    First-class wrapper around the ``generate_image_codex`` operator-MCP
    tool. Plain ``agent`` steps don't have access to that tool (the
    subagent MCP intentionally excludes operator-tier tools to keep the
    surface area small), so workflows that need a deterministic image
    artifact pipeline use this step type instead of prose-prompting a
    ``codex`` agent and hoping it calls the right tool.

    Defaults are tuned for the common case (one PNG, push to canvas,
    register a Construct/Images artifact).
    """
    prompt: str
    output_path: str = ""              # filename only when register_artifact=true; relative-to-cwd path otherwise
    cwd: str | None = None             # default: ~/.construct/workspace
    count: int = Field(default=1, ge=1, le=5)
    output_pattern: str | None = None  # template with {n} when count > 1
    canvas: bool | str = True          # bool, or canvas_id string; canvas push is the point
    register_artifact: bool = True
    space: str | None = None           # default "Images" relative to harness; multi-segment OK
    item_name: str | None = None       # default: derived from output_path stem
    sandbox: Literal["read-only", "workspace-write", "danger-full-access"] | None = None
    timeout: float = 1200.0            # 20 min — codex image gen is slow; matches the operator-MCP tool_timeout default


class PythonStepConfig(BaseModel):
    """Config for 'python' step type — invoke a Python script with JSON I/O.

    Designed as a generic, reusable primitive: any custom transform / utility
    a workflow needs (kref encoding, lead-source parsers, scoring math, etc.)
    becomes a Python file that workflows reference by name. Avoids extending
    the workflow schema every time a one-off operation is needed.

    Specify exactly one of:
      - script: <path> — relative to workflow's cwd, an absolute path, OR the
        name of a builtin under operator_mcp/workflow/builtins/python_steps/
      - code: <inline source> — for one-offs where a separate file is overkill

    The script receives a JSON object on stdin:
      {
        "args": <step.args, with ${...} already interpolated>,
        "context": {
            "inputs": <workflow inputs>,
            "step_results": {<step_id>: <output_data dict>, ...},
            "run_id": <workflow run id>,
            "session_id": <session id, may be empty>,
        }
      }

    The script's stdout SHOULD be a JSON object — that becomes the step's
    output_data, interpolatable downstream as ${<step_id>.output_data.<key>}.
    Non-JSON stdout is captured as raw output but produces empty output_data.

    Sandbox: subprocess of the operator-mcp venv interpreter (so kumiho /
    httpx / etc. are importable from scripts). Inherits workflow cwd.
    Timeout enforced. Same policy gates as `shell:` apply.
    """
    script: str | None = None
    code: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    timeout: float = 60.0
    allow_failure: bool = False
    # Override the interpreter (default: operator-mcp's own venv python).
    # Useful if a script needs deps the operator-mcp venv lacks — point it
    # at a project-local venv instead.
    python: str | None = None
    # Auth profile binding — resolved at runtime; decrypted token passed via
    # CONSTRUCT_AUTH_TOKEN env var. Format: "<provider>:<profile_name>".
    auth: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "PythonStepConfig":
        if bool(self.script) == bool(self.code):
            raise ValueError(
                "python step requires exactly one of `script` (path/name) or "
                "`code` (inline source)"
            )
        return self


class ConditionalBranch(BaseModel):
    """A single branch in a conditional step."""
    condition: str  # Expression: "${step_id.status} == 'completed'" or "default"
    goto: str       # Step ID to jump to
    # Optional expression evaluated when this branch matches; result becomes
    # the conditional step's `output` so downstream steps can read
    # ``${gate.output}``. Evaluated by the same simpleeval-based evaluator as
    # ``condition`` — supports literals (``"'approved'"``), step refs
    # (``"review.status"``), arithmetic, and ternary
    # (``"score > 0.8 ? 'go' : 'stop'"``).
    value: str | None = None


class ConditionalStepConfig(BaseModel):
    """Config for 'conditional' step type."""
    branches: list[ConditionalBranch]


class ParallelStepConfig(BaseModel):
    """Config for 'parallel' step type."""
    steps: list[str] = Field(..., description="IDs of steps to execute in parallel.")
    join: JoinStrategy = JoinStrategy.ALL
    max_concurrency: int = Field(default=5, ge=1, le=10)

    @field_validator("steps")
    @classmethod
    def steps_must_be_non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError(
                "parallel.steps must list at least one step ID. "
                "If you don't need explicit grouping, drop the parallel wrapper "
                "entirely — sibling steps without depends_on run in parallel naturally."
            )
        # Duplicate child refs corrupt _exec_parallel accounting: results is a
        # dict keyed by step_id, so two refs to the same id produce one entry
        # while `total = len(cfg.steps)` counts both — giving a false-fail
        # `completed: 1, total: 2`. Reject at parse time.
        counts: dict[str, int] = {}
        for sid in v:
            counts[sid] = counts.get(sid, 0) + 1
        dups = [(sid, n) for sid, n in counts.items() if n > 1]
        if dups:
            sid, n = dups[0]
            raise ValueError(
                f"parallel.steps must not contain duplicate child references: "
                f"'{sid}' appears {n} times"
            )
        return v


class GotoStepConfig(BaseModel):
    """Config for 'goto' step type — loop construct."""
    target: str  # Step ID to jump to
    condition: str | None = None  # Optional guard expression
    max_iterations: int = Field(default=3, ge=1, le=20)


class HumanApprovalConfig(BaseModel):
    """Config for 'human_approval' step type."""
    message: str = "Workflow paused — approve to continue."
    timeout: float = 0  # 0 = hold indefinitely
    channel: str = "dashboard"  # "dashboard" | "discord" | "slack"
    channel_id: str = ""  # Override: specific Discord/Slack channel ID
    on_reject_goto: str = ""  # Step ID to jump back to on rejection (empty = cancel workflow)
    on_reject_max: int = Field(default=3, ge=1, le=10)  # Max rejection loops before hard cancel
    approve_keywords: list[str] = Field(default_factory=lambda: ["approve", "approved", "yes", "lgtm"])
    reject_keywords: list[str] = Field(default_factory=lambda: ["reject", "rejected", "no"])

    @field_validator("approve_keywords", mode="before")
    @classmethod
    def validate_approve_keywords(cls, v: list[str]) -> list[str]:
        v = [kw.lower() for kw in v]
        if not v:
            raise ValueError("approve_keywords must have at least one entry")
        return v

    @field_validator("reject_keywords", mode="before")
    @classmethod
    def validate_reject_keywords(cls, v: list[str]) -> list[str]:
        return [kw.lower() for kw in v]


class NotifyStepConfig(BaseModel):
    """Config for 'notify' step type — fire-and-forget notification.

    Unlike human_approval (pauses workflow waiting for response), notify
    pushes an event to one or more channels and continues immediately.
    Channel list is plural because users commonly want dashboard + discord
    (or similar) simultaneously.
    """
    channels: list[str] = Field(default_factory=lambda: ["dashboard"])  # e.g. ["dashboard", "discord"]
    channel_id: str = ""  # Override: specific Discord/Slack/Telegram channel/chat ID
    title: str = ""       # Optional notification title
    message: str = ""     # Notification body; supports ${...} interpolation


class HumanInputConfig(BaseModel):
    """Config for 'human_input' step type — pauses for freeform human response.

    Unlike human_approval (yes/no), this sends a prompt to a channel and waits
    for the human to reply with arbitrary text.  The response becomes the step's
    output, accessible via ``${step_id.output}`` in downstream steps.
    """
    message: str = "Input needed — please respond."
    channel: str = "dashboard"
    timeout: float = 3600.0  # 1 hour default


class OutputStepConfig(BaseModel):
    """Config for 'output' step type."""
    format: Literal["text", "json", "markdown"] = "text"
    template: str = ""  # Template with ${var} interpolation

    # Entity production — register output as a Kumiho entity that can trigger downstream workflows
    entity_name: str | None = None        # Item name (supports ${...} interpolation)
    entity_kind: str | None = None        # Item kind (e.g. "analysis-report")
    entity_tag: str = "ready"             # Tag to apply to the revision (triggers listeners)
    entity_space: str | None = None       # Space path (defaults to /Construct/WorkflowOutputs)
    entity_metadata: dict[str, str] = {}  # Key-value pairs stored on entity (supports ${...} interpolation)
                                          # Downstream triggers auto-map matching keys to workflow inputs


class ResolveStepConfig(BaseModel):
    """Config for 'resolve' step type — deterministic Kumiho entity lookup."""
    kind: str                                           # Entity kind to search for
    tag: str = "published"                              # Tag to match
    name_pattern: str = ""                              # Optional name filter (glob/regex)
    space: str = ""                                     # Optional space path filter
    mode: Literal["latest", "all"] = "latest"           # latest = single newest; all = list
    fields: list[str] = Field(default_factory=list)     # Specific metadata fields to extract (empty = all)
    fail_if_missing: bool = True                        # Fail step if no entity found


class TagStepConfig(BaseModel):
    """Config for 'tag' step type — re-tag an existing Kumiho entity revision."""
    item_kref: str                              # kref of the item (supports ${} interpolation)
    tag: str                                    # Tag to apply to the latest revision
    untag: str = ""                             # Optional: tag to remove first


class DeprecateStepConfig(BaseModel):
    """Config for 'deprecate' step type — deprecate a Kumiho item."""
    item_kref: str                              # kref of the item (supports ${} interpolation)
    reason: str = ""                            # Optional deprecation reason


class ForEachStepConfig(BaseModel):
    """Config for 'for_each' step type — sequential iteration over a range or list.

    Executes a sequence of sub-steps for each iteration. Each iteration runs
    sequentially (waiting for the previous to complete) so carry-forward data
    flows naturally from one iteration to the next.

    Variable injection:
      - ``${for_each.<variable>}``  — current iteration value (e.g. episode number)
      - ``${for_each.index}``       — zero-based iteration index
      - ``${for_each.iteration}``   — one-based iteration number
      - ``${for_each.total}``       — total number of iterations
      - ``${previous.<step_id>.output}``       — prior iteration step output
      - ``${previous.<step_id>.output_data.k}`` — prior iteration step data field

    Sub-step results are stored as ``<step_id>__iter_<N>`` in the workflow state,
    so downstream steps outside the loop can reference specific iterations.
    """
    range: str = ""                          # "1..8" or "1..${step.output_data.episode_count}"
    items: list[str] = Field(default_factory=list)  # Explicit item list (alternative to range)
    variable: str = "item"                   # Name of the iteration variable
    steps: list[str]                         # Step IDs to execute each iteration (in order)
    carry_forward: bool = True               # Make previous iteration outputs available
    fail_fast: bool = True                   # Stop on first iteration failure
    max_iterations: int = Field(default=20, ge=1, le=50)  # Safety cap

    @field_validator("steps")
    @classmethod
    def steps_must_be_unique(cls, v: list[str]) -> list[str]:
        # Same accounting hazard as ParallelStepConfig: duplicate child refs
        # would write to the same `<step_id>__iter_<N>` keys twice and the
        # second write clobbers the first. Reject at parse time.
        counts: dict[str, int] = {}
        for sid in v:
            counts[sid] = counts.get(sid, 0) + 1
        dups = [(sid, n) for sid, n in counts.items() if n > 1]
        if dups:
            sid, n = dups[0]
            raise ValueError(
                f"for_each.steps must not contain duplicate child references: "
                f"'{sid}' appears {n} times"
            )
        return v


class ManusStepConfig(BaseModel):
    """Config for 'manus' step type — delegate web research to Manus AI.

    Manus is a hosted general-purpose web agent. The step creates a Manus
    task with the configured prompt (optionally constrained by a JSON
    schema for structured output), then polls the task's message stream
    until the agent reaches a terminal state (``stopped`` or ``error``).
    The final assistant message — plus any structured-output payload — is
    returned as the step's result so downstream steps can consume it via
    ``${manus_step.output_data.*}``.

    Auth: the Manus API key is read from the env var named in
    ``[manus].api_key_env`` (default ``MANUS_API_KEY``). Construct never
    persists the key value — only the env var name — so workflow YAML
    stays safe to commit.

    Polling rationale: Manus exposes a streaming SSE channel and a
    cursor-based poll API. The first iteration uses polling because it's
    operationally simpler (single asyncio task, cancel just stops looping)
    and Manus tasks are long-lived enough that 5-second polls don't add
    meaningful latency. We can swap to SSE later without changing the
    public step contract.
    """
    prompt: str
    structured_output_schema: dict[str, Any] | None = None
    connectors: list[str] = Field(default_factory=list)
    enable_skills: list[str] = Field(default_factory=list)
    force_skills: list[str] = Field(default_factory=list)
    agent_profile: str | None = None
    locale: str | None = None
    project_id: str | None = None
    title: str | None = None
    timeout_seconds: int | None = None
    poll_interval_seconds: int | None = None
    allow_failure: bool = False


class A2AStepConfig(BaseModel):
    """Config for 'a2a' step type — call external A2A agent."""
    url: str  # A2A endpoint URL
    skill_id: str | None = None
    message: str = ""
    timeout: float = 300.0
    # Auth profile binding — resolved at runtime; decrypted token added to
    # outbound A2A request as `Authorization: Bearer <token>`. Format:
    # "<provider>:<profile_name>".
    auth: str | None = None


# -- Orchestration pattern configs -----------------------------------------

class MapReduceStepConfig(BaseModel):
    """Config for 'map_reduce' step type — fan-out / fan-in."""
    task: str  # Overall task description
    splits: list[str]  # Segments to map over (min 2)
    mapper: Literal["claude", "codex"] = "claude"
    reducer: Literal["claude", "codex"] = "claude"
    concurrency: int = Field(default=3, ge=1, le=10)
    timeout: float = 300.0


class SupervisorStepConfig(BaseModel):
    """Config for 'supervisor' step type — dynamic delegation loop."""
    task: str  # Task to decompose
    max_iterations: int = Field(default=5, ge=1, le=10)
    supervisor_type: Literal["claude", "codex"] = "claude"
    timeout: float = 300.0


class GroupChatStepConfig(BaseModel):
    """Config for 'group_chat' step type — moderated multi-agent discussion."""
    topic: str
    participants: list[str]  # Agent types or template names (min 2)
    moderator: Literal["claude", "codex"] = "claude"
    strategy: Literal["round_robin", "moderator_selected"] = "moderator_selected"
    max_rounds: int = Field(default=8, ge=2, le=20)
    timeout: float = 120.0


class HandoffStepConfig(BaseModel):
    """Config for 'handoff' step type — pass context from one agent to another."""
    from_step: str  # Step ID whose agent to hand off from
    to_agent_type: Literal["claude", "codex"] = "codex"
    reason: str = "Continuing the task"
    task: str = ""  # Specific task for receiver
    timeout: float = 300.0


# ---------------------------------------------------------------------------
# Action → executor mapping (editor actions → step type + agent defaults)
# ---------------------------------------------------------------------------

ACTION_DEFAULTS: dict[str, dict[str, str]] = {
    "research":  {"type": "agent", "role": "researcher",  "agent_type": "claude"},
    "code":      {"type": "agent", "role": "coder",       "agent_type": "codex"},
    "review":    {"type": "agent", "role": "reviewer",    "agent_type": "claude"},
    "deploy":    {"type": "agent", "role": "deployer",    "agent_type": "codex"},
    "test":      {"type": "agent", "role": "tester",      "agent_type": "codex"},
    "build":     {"type": "agent", "role": "builder",     "agent_type": "codex"},
    "notify":    {"type": "notify"},
    "approve":   {"type": "human_approval", "role": "",   "agent_type": ""},
    "summarize": {"type": "agent", "role": "summarizer",  "agent_type": "claude"},
    "task":      {"type": "agent", "role": "coder",       "agent_type": "claude"},
    "gate":        {"type": "conditional",  "role": "",      "agent_type": ""},
    "human_input": {"type": "human_input",  "role": "",      "agent_type": ""},
    "resolve":     {"type": "resolve"},
}


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------

class StepDef(BaseModel):
    """A single step in a declarative workflow.

    Accepts both executor format (type + config block) and editor format
    (action + agent_hints).  When ``type`` is omitted, it is inferred from
    ``action`` via ACTION_DEFAULTS.
    """
    id: str
    name: str = ""
    type: StepType = StepType.AGENT
    depends_on: list[str] = Field(default_factory=list)

    # Editor-compatible fields — influence agent selection & prompt
    action: str = ""
    agent_hints: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    assign: str = ""  # Pre-assigned agent template or ID
    description: str = ""

    # Type-specific configs — only one populated based on `type`
    agent: AgentStepConfig | None = None
    shell: ShellStepConfig | None = None
    python: PythonStepConfig | None = None
    email: EmailStepConfig | None = None
    image: ImageStepConfig | None = None
    conditional: ConditionalStepConfig | None = None
    parallel: ParallelStepConfig | None = None
    goto: GotoStepConfig | None = None
    human_approval: HumanApprovalConfig | None = None
    human_input: HumanInputConfig | None = None
    notify: NotifyStepConfig | None = None
    output: OutputStepConfig | None = None
    a2a: A2AStepConfig | None = None
    resolve: ResolveStepConfig | None = None
    for_each: ForEachStepConfig | None = None
    # Orchestration patterns
    map_reduce: MapReduceStepConfig | None = None
    supervisor: SupervisorStepConfig | None = None
    group_chat: GroupChatStepConfig | None = None
    handoff: HandoffStepConfig | None = None
    tag_step: TagStepConfig | None = None
    deprecate_step: DeprecateStepConfig | None = None
    manus: ManusStepConfig | None = None

    # Retry
    retry: int = Field(default=0, ge=0, le=5)
    retry_delay: float = Field(default=5.0, ge=0)

    # Step-level timeout override — pushed into the type-specific config
    # (agent/shell/a2a/group_chat) by a model validator. YAML convention is
    # `timeout: <seconds>` at the step level; this makes it authoritative.
    timeout: float | None = None

    @model_validator(mode="after")
    def _propagate_step_timeout(self) -> "StepDef":
        """Push step-level timeout into the per-type config."""
        if self.timeout is None:
            return self
        t = float(self.timeout)
        if self.agent is not None:
            self.agent.timeout = t
        if self.shell is not None:
            self.shell.timeout = t
        if self.python is not None:
            self.python.timeout = t
        if self.email is not None:
            self.email.timeout = t
        if self.image is not None:
            self.image.timeout = t
        if self.a2a is not None:
            self.a2a.timeout = t
        if self.group_chat is not None:
            self.group_chat.timeout = t
        return self

    @model_validator(mode="before")
    @classmethod
    def infer_type_from_action(cls, data: Any) -> Any:
        """Infer ``type`` from ``action`` or resolve action aliases.

        Handles two cases:
        1. ``type`` not set → infer from ``action`` via ACTION_DEFAULTS
        2. ``type`` set to an action alias (e.g. "notify") → expand to
           the real StepType (e.g. "agent") so Pydantic validation passes
        """
        if not isinstance(data, dict):
            return data

        raw_type = data.get("type", "")
        action = data.get("action", "")

        # Case 1: type not set — infer from action
        if not raw_type and action:
            defaults = ACTION_DEFAULTS.get(action.lower(), {})
            if defaults:
                data["type"] = defaults["type"]
            return data

        # Case 2: type is set but may be an action alias (e.g. "notify")
        if raw_type:
            valid_types = {e.value for e in StepType}
            if raw_type not in valid_types:
                defaults = ACTION_DEFAULTS.get(raw_type.lower(), {})
                if defaults:
                    if not action:
                        data["action"] = raw_type.lower()
                    data["type"] = defaults["type"]

        return data

    @model_validator(mode="before")
    @classmethod
    def bridge_legacy_conditional(cls, data: Any) -> Any:
        """Translate legacy flat conditional syntax to canonical
        ``conditional.branches``.

        The frontend editor (and many hand-written workflows) emit::

            type: conditional
            condition: "${X.status} == 'completed'"
            on_true: step_a
            on_false: step_b

        but the executor + validator only consume the nested form::

            type: conditional
            conditional:
              branches:
                - {condition: "...", goto: step_a}
                - {condition: "default", goto: step_b}

        Run before field-type validation so the dict-shaped flat fields
        become a populated ``ConditionalStepConfig`` and the legacy keys
        are dropped (they are not declared on ``StepDef`` and would be
        ignored, but we drop them explicitly to keep ``model_dump`` clean
        and avoid surprising future strict-mode rejections).

        No-op when ``conditional.branches`` is already provided — caller
        already gave canonical form, so we silently drop a stray top-level
        ``condition`` (mixed input) and leave branches alone.
        """
        if not isinstance(data, dict):
            return data
        # Only relevant for conditional steps. Be tolerant about how the
        # type is spelled (StepType.CONDITIONAL or the literal string).
        raw_type = data.get("type", "")
        type_str = raw_type.value if isinstance(raw_type, StepType) else str(raw_type)
        if type_str != StepType.CONDITIONAL.value:
            return data

        existing = data.get("conditional")
        # Already canonical form — drop legacy top-level keys and bail.
        if isinstance(existing, dict) and existing.get("branches"):
            for k in ("condition", "on_true", "on_false",
                      "on_true_value", "on_false_value"):
                data.pop(k, None)
            return data

        def _clean(v: Any) -> str:
            return v.strip() if isinstance(v, str) else ""

        cond = _clean(data.get("condition"))
        on_true = _clean(data.get("on_true"))
        on_false = _clean(data.get("on_false"))
        on_true_value = data.get("on_true_value")
        on_false_value = data.get("on_false_value")

        # Need a condition AND at least one target to translate. Otherwise
        # leave untouched so the validator can emit its clearer "missing
        # config / missing branches" error.
        if not cond or (not on_true and not on_false):
            return data

        branches: list[dict[str, Any]] = []
        if on_true:
            branch_t: dict[str, Any] = {"condition": cond, "goto": on_true}
            if isinstance(on_true_value, str) and on_true_value:
                branch_t["value"] = on_true_value
            branches.append(branch_t)
        if on_false:
            branch_f: dict[str, Any] = {"condition": "default", "goto": on_false}
            if isinstance(on_false_value, str) and on_false_value:
                branch_f["value"] = on_false_value
            branches.append(branch_f)

        data["conditional"] = {"branches": branches}
        for k in ("condition", "on_true", "on_false",
                  "on_true_value", "on_false_value"):
            data.pop(k, None)
        return data

    @field_validator("name", mode="before")
    @classmethod
    def default_name(cls, v: str, info: Any) -> str:
        if not v and info.data.get("id"):
            return info.data["id"]
        return v

    def get_config(self) -> BaseModel | None:
        """Return the type-specific config for this step."""
        return getattr(self, self.type.value, None)

    def resolve_agent_config(self) -> AgentStepConfig:
        """Return explicit agent config, or auto-construct from action + hints."""
        if self.agent is not None:
            # Wire assign → template if agent config has no template set
            if not self.agent.template and self.assign:
                self.agent.template = self.assign
            return self.agent
        defaults = ACTION_DEFAULTS.get(self.action.lower(), ACTION_DEFAULTS["task"])
        role = defaults["role"]
        agent_type = defaults["agent_type"]
        # Agent hints override defaults
        if "codex" in self.agent_hints or "coder" in self.agent_hints:
            agent_type = "codex"
        elif "claude" in self.agent_hints or "researcher" in self.agent_hints or "reviewer" in self.agent_hints:
            agent_type = "claude"
        # Explicit role hints
        for hint in self.agent_hints:
            if hint in ("coder", "researcher", "reviewer"):
                role = hint
                break
        prompt = self.description or f"Execute {self.action} task: {self.name}"
        return AgentStepConfig(
            agent_type=agent_type,  # type: ignore[arg-type]
            role=role,
            prompt=prompt,
            template=self.assign or None,  # Wire assign → pool template
        )


# ---------------------------------------------------------------------------
# Input / Output definitions
# ---------------------------------------------------------------------------

class InputDef(BaseModel):
    """Workflow input parameter."""
    name: str
    type: Literal["string", "number", "boolean", "list"] = "string"
    required: bool = True
    default: Any = None
    description: str = ""


class OutputDef(BaseModel):
    """Workflow output mapping."""
    name: str
    source: str  # e.g. "${final_review.output}"
    description: str = ""


# ---------------------------------------------------------------------------
# Trigger definition (event-driven workflow chaining)
# ---------------------------------------------------------------------------

class TriggerDef(BaseModel):
    """Declares an event or cron trigger that auto-launches this workflow."""
    on_kind: str = ""                     # Entity kind to watch (exact match); empty for cron
    on_tag: str = "ready"                 # Revision tag that triggers (exact match)
    on_name_pattern: str = ""             # Optional glob for entity name (empty = any)
    on_space: str = ""                    # Optional space path filter (prefix match); empty = any
    input_map: dict[str, str] = {}        # Maps workflow input name → template
                                          # e.g. {"report_kref": "${trigger.entity_kref}"}
    cron: str = ""                        # Cron expression for time-based triggers


# ---------------------------------------------------------------------------
# Workflow definition
# ---------------------------------------------------------------------------

class WorkflowDef(BaseModel):
    """Top-level declarative workflow definition."""
    name: str
    version: str = "1.0"
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    triggers: list[TriggerDef] = []       # Events that auto-launch this workflow

    inputs: list[InputDef] = Field(default_factory=list)
    outputs: list[OutputDef] = Field(default_factory=list)
    steps: list[StepDef]

    # Execution defaults
    default_cwd: str = ""
    default_timeout: float = 300.0
    max_total_time: float = 3600.0  # 1 hour safety cap
    checkpoint: bool = True

    @field_validator("steps")
    @classmethod
    def at_least_one_step(cls, v: list[StepDef]) -> list[StepDef]:
        if not v:
            raise ValueError("Workflow must have at least one step")
        # Duplicate step ids are always a bug: ``step_by_id`` returns the
        # first match while the frontend's `new Map(...)` round-trip keeps
        # the last, so the closure preview can disagree with the executor.
        # Reject at parse time rather than papering over it everywhere
        # downstream.
        seen: dict[str, int] = {}
        duplicates: list[str] = []
        for s in v:
            if s.id in seen:
                if s.id not in duplicates:
                    duplicates.append(s.id)
            else:
                seen[s.id] = 1
        if duplicates:
            raise ValueError(
                f"Duplicate step id(s): {', '.join(sorted(duplicates))}. "
                f"Each step.id must be unique within a workflow."
            )
        return v

    def step_by_id(self, step_id: str) -> StepDef | None:
        """Find a step by its ID."""
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def step_ids(self) -> list[str]:
        """All step IDs in definition order."""
        return [s.id for s in self.steps]


# ---------------------------------------------------------------------------
# Runtime state (used by executor, persisted to checkpoints)
# ---------------------------------------------------------------------------

class StepResult(BaseModel):
    """Result of executing a single step."""
    step_id: str
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    output: str = ""
    # input_data: the resolved/interpolated inputs at execution time, captured
    # by each `_exec_*` handler so the run-view UI can render the actual values
    # the step ran with (NOT the raw YAML config — that may contain ${...}
    # references that haven't been resolved yet). Persisted alongside
    # output_data so the dashboard can show full per-step detail.
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error: str = ""
    agent_id: str | None = None
    agent_type: str = ""  # "claude" or "codex" — which provider ran this step
    role: str = ""        # "coder", "researcher", "reviewer", etc.
    action: str = ""      # Original action from workflow definition
    files_touched: list[str] = Field(default_factory=list)
    duration_s: float = 0.0
    retries_used: int = 0


class WorkflowState(BaseModel):
    """Full runtime state of a workflow execution."""
    workflow_name: str
    run_id: str
    status: WorkflowStatus = WorkflowStatus.PENDING
    inputs: dict[str, Any] = Field(default_factory=dict)
    step_results: dict[str, StepResult] = Field(default_factory=dict)
    current_step: str | None = None
    iteration_counts: dict[str, int] = Field(default_factory=dict)  # For goto loops
    started_at: str | None = None
    completed_at: str | None = None
    error: str = ""
    checkpoint_path: str | None = None
    trigger_context: dict[str, str] = {}  # Set when launched by event listener
    # Kumiho kref pins so the dashboard DAG viewer can fetch the exact
    # workflow revision this run executed, regardless of later retags.
    # Empty strings mean "built-in / disk fallback" — name-matching is fine.
    workflow_item_kref: str = ""
    workflow_revision_kref: str = ""
    # Side-channel cache for conditional-step matched gotos. Keyed by
    # step id. Held off ``StepResult.output_data`` so the entry can't leak
    # into the simpleeval names dict or ``${gate.output_data.*}`` lookups.
    conditional_routes: dict[str, str] = Field(default_factory=dict)

    # Cancel signal — set by the cancel_workflow MCP tool. Distinct from
    # ``status: CANCELLED`` (the OUTCOME): ``cancel_requested`` is the
    # SIGNAL the executor reads at step boundaries / inside long-running
    # subprocess polls, then transitions the run to CANCELLED cleanly.
    # Excluded from persistence/checkpoint dumps because it's a transient
    # in-memory signal — once observed and the status flips to CANCELLED,
    # the flag's job is done.
    cancel_requested: bool = Field(default=False, exclude=True)
    # Live subprocess handles for the run, registered by step handlers
    # (_exec_shell, _exec_python). When cancel fires, the executor walks
    # this list and kills each process so subprocesses don't outlive
    # their parent run. Excluded from persistence: Process objects are
    # not serializable and only meaningful within the running executor.
    running_processes: list[Any] = Field(default_factory=list, exclude=True)
    # Run-to-step closure — set by the executor when ``target_step_id`` is
    # passed to ``execute_workflow``. Step handlers (currently
    # ``_exec_parallel``, ``_exec_for_each``) consult this to skip non-closure
    # children. Empty set means "no restriction" (normal full run). Excluded
    # from persistence: it's a transient input to the running executor and is
    # always re-derived from ``target_step_id`` on resume.
    run_to_closure: set[str] = Field(default_factory=set, exclude=True)
    # Persisted run-to-step target. Closure is derived from this so a paused
    # run-to-here resumed from checkpoint honours the same scoping it started
    # with. ``None`` means "no scoping" (normal full run).
    target_step_id: str | None = None
