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
