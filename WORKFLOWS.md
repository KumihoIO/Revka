# Revka Declarative Workflows — HOW-TO Guide

## Overview

Revka workflows are YAML files that define multi-step, multi-agent pipelines.
The operator executes them deterministically: resolve data, spawn agents, branch
on conditions, publish entities, and chain into downstream workflows — all without
manual orchestration.

```
YAML definition → Operator validates → Executor runs steps → Entities published → Downstream triggered
```

---

## Quick Start

### 1. Where workflows live

| Priority | Path | Purpose |
|----------|------|---------|
| 3 (highest) | `.revka/workflows/` | Project-local overrides |
| 2 | `~/.revka/workflows/` | User-global workflows |
| 1 (lowest) | `operator_mcp/workflow/builtins/` | Shipped defaults |

Later sources override earlier ones. The operator also checks **Kumiho**
(`Revka/Workflows` space) as a final fallback when a workflow isn't found on disk.

### 2. Minimal workflow

```yaml
name: hello-world
version: "1.0"
description: A simple two-step workflow.

steps:
  - id: greet
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Say hello in three languages."

  - id: summary
    type: output
    depends_on: [greet]
    output:
      format: text
      template: "Agent said: ${greet.output}"
```

### 3. Running a workflow

- **Operator CLI**: Ask the AI assistant to run a workflow (e.g. "run quantum-soul-arc-room")
- **API**: `POST /api/workflows/run/{name}` with optional `{"inputs": {...}, "cwd": "..."}`
- **Cron**: Add a `triggers:` block — Revka auto-registers the schedule on save
- **Event chain**: A previous workflow's output entity triggers this one automatically

### 4. Dashboard editor and run controls

The dashboard keeps workflow definitions and workflow run instances separate:

- **Definition tab** (`/workflows`) edits, duplicates, deprecates, deletes, and starts workflow definitions.
- **Runs tab** (`/workflows`, `/runs`) selects run instances and offers run-scoped controls only: stop an active run, retry a failed run, or delete the selected run record.
- The YAML drawer is a graph editor surface. YAML text edits must be applied to the graph before Save; Save serializes the graph back to YAML and the gateway validates it before creating a Kumiho revision.
- The run viewer pins a run to the workflow revision it executed when that revision is available, so later definition edits do not change the displayed run graph.
- Completed conditional nodes show the matched branch, goto target, branch value, and emitted output in the graph and step inspector.
- Failed nodes show the best available failure detail from the executor error, structured `output_data`, stderr preview, or captured inputs.

---

## Anatomy of a Workflow

```yaml
name: my-workflow              # Unique identifier (becomes the slug)
version: "1.0"                 # Semantic version
description: What this does.
tags: [domain, category]

triggers:                      # Optional — auto-launch conditions
  - cron: "0 9 * * 1"         # Time-based (cron expression)
  - on_kind: "report"         # Event-based (entity kind + tag)
    on_tag: "ready"
    on_name_pattern: "daily-*" # Optional glob on entity name
    on_space: "Revka/Reports" # Optional space prefix filter
    input_map:
      report_kref: "${trigger.entity_kref}"

inputs:                        # Typed parameters
  - name: topic
    type: string               # string | number | boolean | list
    required: true
    default: ""
    description: The topic to research.

outputs:                       # Named outputs for callers
  - name: result
    source: "${final_step.output}"

steps:                         # At least one step required
  - id: step_1
    type: agent
    ...
```

---

## Step Types

### `agent` — Spawn an LLM agent

```yaml
- id: research
  type: agent
  depends_on: []
  agent:
    agent_type: claude         # claude or codex
    role: researcher           # coder, researcher, reviewer, etc.
    prompt: |
      Research ${inputs.topic} and summarize findings.
    output_fields: [summary, score] # When set, these structured fields are required
    model: null                # Optional model override
    timeout: 300               # Seconds (default 300)
    template: my-template      # Optional agent pool template
  skills:
    - "kref://CognitiveMemory/Skills/some-skill.skilldef"
  retry: 1                    # Retry once on failure
  retry_delay: 10             # Wait 10s between retries
```

The `action` field provides shorthand: `action: research` auto-sets
`type: agent`, `role: researcher`, `agent_type: claude` via `ACTION_DEFAULTS`.

#### Agent structured output

Agent steps can expose structured fields through `output_data`.

When `agent.output_fields` is set, Revka treats it as a required structured
output contract:

1. The executor appends structured-output instructions to the agent prompt.
2. The agent must return every declared field.
3. Missing fields fail the step with `structured_output_missing`.

```yaml
- id: final-canon-auditor
  type: agent
  agent:
    role: reviewer
    prompt: "Review canon consistency."
    output_fields: [verdict, production_ready]
```

Recommended final format:

```yaml
FINAL_OUTPUT:
  verdict: NEEDS_CHANGES
  production_ready: false
```

Supported structured output formats:

- Full JSON object: `{"verdict":"APPROVED","production_ready":true}`
- Final fenced `json` block
- Final `FINAL_OUTPUT:` YAML block

The parsed fields are merged into `output_data`, so downstream steps can use
`${final-canon-auditor.output_data.production_ready}` or expression aliases such
as `final_canon_auditor.output_data.production_ready`. Agent outputs without
`output_fields` remain backward compatible: valid JSON, fenced JSON, and
`FINAL_OUTPUT` are parsed when present, but missing fields do not fail the step.

#### Agent output artifacts and dependency handoff

Agent steps persist their full text output to disk and expose the path as
`${agent_step.output_data.artifact_path}`. Use this path when a downstream
agent needs exact prior context without inlining the full upstream output into
its prompt.

When you connect an agent step to another agent in the dashboard editor, the
target prompt is auto-populated with a small dependency handoff block that
points at `${source.output_data.artifact_path}`. The downstream agent should
read that file only when it needs the full context.

For step types that do not produce `output_data.artifact_path`, keep using
`${step.output}` or a specific `${step.output_data.field}`. Explicit
`${step.output}` interpolation is still supported, but it should be reserved
for short values because it inlines the upstream text into the next prompt.

```yaml
- id: draft
  type: agent
  agent:
    prompt: "Write the full draft."

- id: review
  type: agent
  depends_on: [draft]
  agent:
    prompt: |
      Dependency handoff from draft:
      - artifact_path: ${draft.output_data.artifact_path}

      Read artifact_path for the complete draft, then review it.
```

### `shell` — Run a shell command

```yaml
- id: build
  type: shell
  shell:
    command: "cd ${inputs.project_dir} && npm run build"
    timeout: 60
    allow_failure: false       # true = non-zero exit doesn't fail workflow
```

### `python` — Run a Python script or inline Python

```yaml
- id: transform
  type: python
  python:
    script: "scripts/transform.py"   # Or use `code:` for inline Python
    args:
      topic: "${inputs.topic}"
    timeout: 60
```

### `email` — Send outbound email via SMTP

```yaml
- id: outreach
  type: email
  email:
    to: "user@example.com"
    subject: "Revka report"
    body: "${report.output}"
    dry_run: true
```

### `notify` — Send a notification without pausing the workflow

```yaml
- id: heads_up
  type: notify
  notify:
    channels: [dashboard, slack]
    title: "Workflow update"
    message: "Run ${run_id} completed."
```

### `resolve` — Deterministic Kumiho entity lookup (no LLM)

```yaml
- id: resolve_cursor
  type: resolve
  resolve:
    kind: "qs-episode-final"   # Entity kind (exact match)
    tag: "published"           # Revision tag (exact match)
    name_pattern: ""           # Optional glob filter on entity name
    space: ""                  # Space path filter (default: Revka/WorkflowOutputs)
    mode: latest               # latest = single newest | all = list
    metadata_source: revision  # revision | item | artifact
    fields: [part, episode_number, arc_name]  # Metadata fields to extract (empty = all)
    fail_if_missing: false     # false = don't fail if nothing found
```

**Output data** (accessible via `${resolve_cursor.output_data.*}`):

| Field | Value |
|-------|-------|
| `found` | `true` or `false` |
| `item_kref` | Kumiho item kref |
| `revision_kref` | Kumiho revision kref |
| `name` | Entity name |
| `metadata_source` | Metadata level used for extracted fields |
| `<field>` | Each field from `fields` list, or all metadata if `fields` is empty |

`metadata_source` defaults to `revision`. Use `item` to read metadata written
for trigger auto-mapping, or `artifact` to read metadata attached to the
published output artifact.

### `conditional` — Branch on expressions

```yaml
- id: gate
  type: conditional
  depends_on: [review]
  conditional:
    branches:
      - condition: "${review.output} contains APPROVED"
        goto: publish
      - condition: "${review.status} == 'failed'"
        goto: fix
      - condition: default      # Catch-all
        goto: fix
```

Supported operators: `==`, `!=`, `contains`, `>`, `<`, `>=`, `<=`.
Use `"end"` as goto target to terminate the workflow.

The run viewer records the matched branch on completed conditionals:

| Field | Meaning |
|-------|---------|
| `output_data.matched_branch_index` | Zero-based branch index |
| `output_data.matched_branch_label` | `branch N` or `default` |
| `output_data.matched_goto` | Target step chosen by this gate |
| `output_data.matched_condition` | Condition string that matched |
| `output_data.matched_value_expr` | Optional branch `value` expression |
| `output_data.matched_output` | Value emitted as `${gate.output}` |

### `parallel` — Run steps concurrently

```yaml
- id: fan_out
  type: parallel
  parallel:
    steps: [step_a, step_b, step_c]
    join: all                  # all | any | majority
    max_concurrency: 5         # 1-10
```

| Join strategy | Behavior |
|---------------|----------|
| `all` | Wait for every branch; fail if any fails |
| `any` | First success wins; cancel the rest |
| `majority` | >50% must succeed |

### `goto` — Loop with guard

```yaml
- id: retry_loop
  type: goto
  depends_on: [check_quality]
  goto:
    target: improve            # Step ID to jump back to
    condition: "${check_quality.output} contains NEEDS_WORK"
    max_iterations: 3          # Safety cap (1-20)
```

### `output` — Emit result and optionally publish entity

```yaml
- id: report
  type: output
  depends_on: [analyze]
  output:
    format: markdown           # text | json | markdown
    template: |
      # Analysis Report
      ${analyze.output}

    # Optional: publish as Kumiho entity (triggers downstream workflows)
    entity_name: "analysis-${inputs.topic}"
    entity_kind: "analysis-report"
    entity_tag: "ready"
    entity_space: "Revka/WorkflowOutputs"   # Default space
    metadata_target: item       # item | revision | artifact
    entity_metadata:
      topic: "${inputs.topic}"
      summary: "${analyze.output}"
```

When `entity_name` and `entity_kind` are both set, the executor:
1. Creates a Kumiho item in `entity_space`
2. Creates a revision with the rendered template as content
3. Tags the revision with `entity_tag`
4. Fires a `revision.tagged` event — which can trigger downstream workflows

`entity_metadata` is written to `metadata_target`. The default `item` preserves
downstream trigger auto-mapping. Choose `revision` when resolve steps should
read it without setting `metadata_source`, or `artifact` when metadata belongs
to the attached output artifact.

**Output data** includes `entity_kref` and `entity_revision_kref` for downstream reference.

### `human_approval` — Pause for yes/no

```yaml
- id: approve
  type: human_approval
  human_approval:
    message: "Deploy to production?"
    timeout: 3600              # 1 hour
```

### `human_input` — Pause for freeform text

```yaml
- id: ask_user
  type: human_input
  human_input:
    message: "What changes do you want?"
    channel: dashboard
    timeout: 3600
```

Response becomes `${ask_user.output}` for downstream steps.

### `a2a` — Call external A2A agent

```yaml
- id: external
  type: a2a
  a2a:
    url: "https://agent.example.com/a2a"
    skill_id: "analyze-data"
    message: "Analyze: ${inputs.data}"
    timeout: 300
```

### Orchestration patterns

| Type | Purpose |
|------|---------|
| `map_reduce` | Fan-out over splits, then reduce |
| `supervisor` | Dynamic delegation loop |
| `group_chat` | Moderated multi-agent discussion |
| `handoff` | Pass context from one agent to another |

---

## Variable Interpolation

Most user-authored string fields in steps support `${...}` interpolation.
Variables resolve at execution time from the current workflow state. This
includes prompts, shell commands, Python args, notify messages, output
templates, entity publish fields, resolve `kind`/`tag`/`name_pattern`/`space`,
conditions, branch values, goto guards, and email fields.

There are two expression forms:

| Form | Result type | Use when |
|------|-------------|----------|
| `${namespace.path}` | Always text | You need a direct value lookup |
| `${{ expression }}` | Typed when it is the whole value, text inside larger strings | You need functions, arithmetic, comparisons, or fallback logic |

Examples:

```yaml
agent:
  prompt: |
    Topic: ${inputs.topic}
    Prior summary: ${resolve_prior.output_data.summary}

resolve:
  kind: "report"
  tag: "${inputs.release_tag}"
  name_pattern: "daily-${{ lower(inputs.team) }}-*"
  space: "Revka/${{ lower(inputs.team) }}/Reports"

compute:
  outputs:
    next_episode: "${{ int(resolve_cursor.output_data.episode_number) + 1 }}"
    publish_ready: "${{ review.output_data.score >= 0.8 }}"
```

### Namespaces

```
${inputs.name}                    Workflow input parameter
${trigger.entity_kref}            Trigger entity kref
${trigger.entity_name}            Trigger entity name
${trigger.entity_kind}            Trigger entity kind
${trigger.tag}                    Trigger tag
${trigger.revision_kref}          Trigger revision kref
${trigger.metadata.key}           Trigger entity metadata field

${step_id.output}                 Step's text output
${step_id.status}                 completed | failed | running | skipped
${step_id.error}                  Error message (if failed)
${step_id.output_data.key}        Structured output field
${step_id.files}                  Comma-separated files touched
${step_id.agent_id}               Agent ID (for agent steps)

${loop.iteration}                 Current goto loop count
${env.VAR}                        Environment variable
${run_id}                         Workflow run UUID
```

`inputs` is the canonical workflow input namespace. Some older UI hints may
show `${input.name}`; use `${inputs.name}` in workflow YAML.

### Expression placeholders

`${{ ... }}` uses the same safe expression evaluator used by conditional
steps. It can read the namespaces above without wrapping each lookup in
`${...}`:

```yaml
condition: "review.output_data.score >= 0.8"
value: "approved:${{ format(review.output_data.score, '.2f') }}"
space: "Revka/${{ lower(inputs.team) }}/WorkflowOutputs"
```

Supported expression features:

| Feature | Examples |
|---------|----------|
| Comparisons | `a == b`, `a != b`, `score >= 0.8` |
| Boolean logic | `ok and not blocked`, `status == 'done' or retry_count > 0` |
| Membership | `'approve' in lower(review.output)`, `review.output contains 'APPROVED'` |
| Arithmetic | `int(count) + 1`, `price * quantity` |
| String helpers | `lower(x)`, `upper(x)`, `str(x)`, `format(score, '.2f')`, `pad(n, 3)` |
| Type helpers | `int(x)`, `float(x)`, `bool(x)`, `len(x)` |
| Lists/ranges | `range(1, int(inputs.count) + 1)` |
| Equality helper | `eq(a, b)` |

When a string is exactly one expression placeholder, the typed value is
preserved where the step supports typed values. Inside longer strings, the
expression result is converted to text.

### Missing values

Unresolved `${step.output_data.key}` returns `""` if the step has not run,
the key is absent, or a resolve step returned `found: false`. Other unresolved
`${...}` references remain literal so they are visible in run diagnostics.
Use `fail_if_missing: false` on first-run resolve steps and write prompts to
handle empty resolved fields.

---

## Triggers and Workflow Chaining

### Cron triggers

```yaml
triggers:
  - cron: "0 9 * * 1"           # Every Monday 9am
```

When a workflow with a cron trigger is saved to Kumiho (via the UI), Revka
auto-registers it as a scheduled job. The cron scheduler calls
`POST /api/workflows/run/{name}` directly at the scheduled time.

> **Note:** Cron-only triggers don't need `on_kind`/`on_tag`. Those fields are
> only required for entity-based triggers.

### Entity triggers

```yaml
triggers:
  - on_kind: "qs-arc-plan"      # Watch for this entity kind
    on_tag: "ready"             # When tagged with this
    on_name_pattern: "qs-*"     # Optional glob on entity name
    on_space: "Revka/WorkflowOutputs/QuantumSoul" # Optional space prefix
    input_map:                  # Map trigger data → workflow inputs
      arc_kref: "${trigger.entity_kref}"
      arc_name: "${trigger.metadata.arc_name}"
```

The event listener watches for `revision.tagged` events. When an output step
publishes an entity matching a trigger rule, the downstream workflow launches
automatically.

Entity trigger filters are cumulative:

| Field | Match behavior |
|-------|----------------|
| `on_kind` | Entity kind exact match. Required for entity triggers. |
| `on_tag` | Revision tag exact match. Defaults to `ready` when omitted. |
| `on_name_pattern` | Optional glob against entity name, for example `daily-*`. |
| `on_space` | Optional space path prefix, for example `Revka/Reports`. |

**Auto-mapping**: If a trigger's entity metadata keys match required input
names on the downstream workflow, they're mapped automatically — no explicit
`input_map` needed.

### Chaining example

```
quantum-soul-arc-room
  └─ output step publishes: kind=qs-arc-plan, tag=ready
       └─ event listener matches trigger on quantum-soul-episode-room
            └─ quantum-soul-episode-room launches with arc context
                 └─ output step publishes: kind=qs-episode-final, tag=published
                      └─ next arc-room run resolves this as cursor
```

---

## Multi-Run Continuity Pattern

This is the key pattern for workflows that build on previous runs.

### The problem

A workflow runs weekly. Each run must know what happened in previous runs
(last episode written, last arc planned, etc.) without hardcoding state.

### The solution: resolve + seed inputs + entity publishing

```yaml
inputs:
  - name: arc_name
    default: "awakening-arc-1"       # Seed for first run
    description: Auto-resolved on subsequent runs

steps:
  # 1. Try to find previous output (empty on first run)
  - id: resolve_prior
    type: resolve
    resolve:
      kind: "qs-arc-plan"
      tag: "ready"
      fail_if_missing: false         # Don't fail if nothing exists yet

  # 2. Agent uses resolved data OR seed inputs
  - id: plan
    type: agent
    depends_on: [resolve_prior]
    agent:
      prompt: |
        ## Auto-resolved from last run (empty on first run)
        Previous arc: ${resolve_prior.output_data.arc_name}
        Episode range: ${resolve_prior.output_data.episode_range}
        Continuity: ${resolve_prior.output_data.continuity_context}

        ## Seed inputs (use when auto-resolved is empty)
        Arc name: ${inputs.arc_name}

        Use auto-resolved values when available; fall back to seeds on first run.

  # 3. Publish entity for next run to find
  - id: output
    type: output
    depends_on: [plan]
    output:
      template: "${plan.output}"
      entity_name: "qs-arc-${inputs.arc_name}"
      entity_kind: "qs-arc-plan"
      entity_tag: "ready"
      entity_metadata:
        arc_name: "${inputs.arc_name}"
        episode_range: "1-8"
        continuity_context: "${plan.output}"
```

**First run**: `resolve_prior.output_data.found = false`, all fields empty.
Agent uses seed inputs. Output publishes entity.

**Second run**: `resolve_prior` finds the entity from run 1. Agent uses
resolved continuity. Output publishes new entity (next iteration).

### Key rules

1. Always use `fail_if_missing: false` on resolve steps that may be empty
2. Put sensible defaults in `inputs` for the very first run
3. Structure prompts with both resolved and seed sections
4. Store everything the next run needs in `entity_metadata`
5. Align `output.metadata_target` with `resolve.metadata_source` when reading
   entity metadata back in a later run
6. Make sure the output step's `entity_kind` + `entity_tag` match what the
   resolve step searches for

---

## Saving and Artifact Persistence

When you save a workflow from the UI:

1. **API receives** the YAML definition via `PUT /api/workflows/{kref}`
2. **Kumiho revision** created with the definition in metadata
3. **YAML written to disk** at `~/.revka/workflows/{slug}.r{N}.yaml`
4. **Kumiho artifact** registered pointing to the file: `file:///.../{slug}.r{N}.yaml`
5. **Revision tagged** as `published` (after artifact is attached)
6. **Cron jobs synced** if the workflow has cron triggers

The artifact is what makes `resolve_kref` work — when the operator needs to
load a Kumiho-managed workflow, it resolves the kref to the file on disk.

**Revision files** (`.r{N}.yaml`) are not picked up by directory scanning.
Only the base file (`workflow-name.yaml`) is discovered by the loader's
filesystem scan. Revision files are accessed exclusively through kref resolution.

---

## Complete Example: Quantum Soul Arc Room

This is a production workflow with 11 steps across 5 phases:

```
Phase 0: Resolve     ─ resolve_cursor + resolve_last_arc (parallel, no LLM)
Phase 1: Specialists ─ 6 agents in parallel (world, science, character, structure, persona, hooks)
Phase 2: Synthesis   ─ arc_editor synthesizes all 6 memos
Phase 3: Queue       ─ episode_queue builds operational writing queue
Phase 4: Output      ─ arc_packet publishes qs-arc-plan entity → triggers episode room
```

### Phase 0: Resolve prior state

```yaml
steps:
  - id: resolve_cursor
    type: resolve
    resolve:
      kind: "qs-episode-final"
      tag: "published"
      fields: [part, episode_number, episode_goal, arc_name]
      fail_if_missing: false

  - id: resolve_last_arc
    type: resolve
    resolve:
      kind: "qs-arc-plan"
      tag: "ready"
      fields: [part, arc_name, episode_range, arc_goal, continuity_context]
      fail_if_missing: false
```

Two parallel resolve steps. No LLM calls. Each returns metadata from the
latest matching Kumiho entity, or `found: false` if none exist.

### Phase 1: Parallel specialist agents

All 6 agents depend on both resolve steps and run in parallel.
Each prompt follows the dual-source pattern:

```yaml
  - id: arc_world
    type: agent
    depends_on: [resolve_cursor, resolve_last_arc]
    agent:
      agent_type: claude
      role: world-builder
      template: quantum-soul-world-builder
      prompt: |
        ## Series cursor (auto-resolved — empty on first run)
        Last episode number: ${resolve_cursor.output_data.episode_number}
        Part: ${resolve_cursor.output_data.part}

        ## Previous arc plan (auto-resolved — empty on first run)
        Episode range: ${resolve_last_arc.output_data.episode_range}
        Arc goal: ${resolve_last_arc.output_data.arc_goal}

        ## Seed inputs (use when auto-resolved values above are empty)
        Part: ${inputs.part}
        Arc name: ${inputs.arc_name}
        Episode range: ${inputs.episode_range}

        Use the auto-resolved values when available; fall back to seed inputs on first run.

        Output in markdown with exactly these sections:
        1. Setting / Institutional Pressure Across The Arc
        ...
```

### Phase 2-3: Synthesis chain

```yaml
  - id: arc_editor
    depends_on: [arc_world, arc_science, arc_character, arc_structure, arc_persona, arc_hooks]
    agent:
      prompt: |
        World memo:    ${arc_world.output}
        Science memo:  ${arc_science.output}
        ...
        Synthesize into one canonical arc mandate.

  - id: episode_queue
    depends_on: [arc_editor]
    agent:
      prompt: |
        ${arc_editor.output}
        Convert into an operational writing queue.
```

### Phase 4: Entity output

```yaml
  - id: arc_packet
    type: output
    depends_on: [arc_editor, episode_queue]
    output:
      format: markdown
      template: |
        # Quantum Soul Arc Plan
        ${arc_editor.output}
        ## Episode Queue
        ${episode_queue.output}
      entity_name: "qs-arc-${inputs.arc_name}"
      entity_kind: "qs-arc-plan"
      entity_tag: "ready"
      entity_space: "Revka/WorkflowOutputs"
      metadata_target: revision
      entity_metadata:
        part: "${resolve_cursor.output_data.part}"
        arc_name: "${resolve_cursor.output_data.arc_name}"
        episode_range: "${inputs.episode_range}"
        last_episode_number: "${resolve_cursor.output_data.episode_number}"
        last_episode_kref: "${resolve_cursor.output_data.revision_kref}"
        last_arc_kref: "${resolve_last_arc.output_data.revision_kref}"
        continuity_context: "${arc_editor.output}"
        episode_queue: "${episode_queue.output}"
```

**Critical details:**
- `entity_kind: "qs-arc-plan"` must match `resolve_last_arc`'s `kind` field
- `entity_tag: "ready"` must match `resolve_last_arc`'s `tag` field
- `metadata_target: revision` matches resolve's default `metadata_source`
- `entity_metadata` stores everything the next run needs to pick up continuity
- The entity_name uses `${inputs.arc_name}` (always has a value) not resolved data (may be empty)

---

## Validation

The validator runs 6 passes before execution:

1. **Duplicate step IDs** — no two steps share an ID
2. **Dependency references** — all `depends_on` point to existing steps
3. **Cycle detection** — topological sort fails on cycles
4. **Step config** — type-specific checks (e.g., agent config exists, shell has command)
5. **Variable references** — warns if `${step_id.*}` references unknown steps
6. **Trigger validation** — checks trigger fields, warns on unmapped required inputs

Agent steps with `depends_on` must reference each dependency in `agent.prompt`.
Prefer `${upstream.output_data.artifact_path}` when the upstream agent produced
a full output artifact. Use `${upstream.output}` only for short inline handoffs
or for step types that do not produce an artifact path.

To validate without executing, ask the operator to dry-run a workflow. The
operator's `dry_run_workflow` tool parses the YAML, runs all 6 passes, and
reports errors/warnings without starting execution.

---

## Retry and Checkpoints

### Retry

```yaml
- id: flaky_step
  type: agent
  retry: 2              # Retry up to 2 times after first attempt
  retry_delay: 10       # Wait 10 seconds between retries
```

Only retries on step failure. Completion and validation errors are not retried.

### Checkpoints

```yaml
checkpoint: true         # Default: true (set at workflow level)
```

When enabled, the executor saves state to `~/.revka/workflow_checkpoints/{run_id}.json`
after each step completes and on workflow pause (human approval). Checkpoints
support explicit user actions such as approval resume and failed-run retry.
Operator startup does **not** automatically resume interrupted workflow runs:
stale in-progress runs are marked failed, and the user must press Retry to
launch the retry path.

### Run stop and delete

Stopping a run sends `POST /api/workflows/runs/{run_id}/cancel` to the gateway,
which forwards `cancel_workflow` to the Operator MCP server. The executor stops
at the next boundary and kills owned shell/python subprocesses where possible.
Deleting a run calls `DELETE /api/workflows/runs/{run_id}` and removes the
WorkflowRuns Kumiho item plus best-effort local checkpoint/artifact files. It
does not delete or deprecate the workflow definition.

Definitions use separate endpoints:

- `DELETE /api/workflows/{kref}` deletes a workflow definition.
- `POST /api/workflows/deprecate` toggles definition availability.
- Run stop, retry, and delete endpoints act only on a run instance.

---

## Action Shorthand

The `action` field maps editor-friendly names to step types and agent defaults:

| Action | Type | Role | Agent |
|--------|------|------|-------|
| `research` | agent | researcher | claude |
| `code` | agent | coder | codex |
| `review` | agent | reviewer | claude |
| `test` | agent | tester | codex |
| `build` | agent | builder | codex |
| `deploy` | agent | deployer | codex |
| `notify` | notify | — | — |
| `summarize` | agent | summarizer | claude |
| `task` | agent | coder | claude |
| `approve` | human_approval | — | — |
| `gate` | conditional | — | — |
| `human_input` | human_input | — | — |
| `resolve` | resolve | — | — |

Override with `agent_hints`:

```yaml
- id: my_step
  action: research
  agent_hints: [codex]    # Override: use codex instead of claude
```

---

## Common Patterns

### Pattern 1: Linear pipeline

```yaml
steps:
  - id: gather
    type: agent
    agent: { agent_type: claude, role: researcher, prompt: "..." }

  - id: process
    type: agent
    depends_on: [gather]
    agent:
      agent_type: codex
      role: coder
      prompt: |
        Read the upstream artifact before processing:
        ${gather.output_data.artifact_path}

  - id: report
    type: output
    depends_on: [process]
    output: { format: text, template: "${process.output}" }
```

### Pattern 2: Parallel fan-out + synthesis

```yaml
steps:
  - id: analyst_a
    type: agent
    agent: { prompt: "Analyze from angle A..." }

  - id: analyst_b
    type: agent
    agent: { prompt: "Analyze from angle B..." }

  - id: synthesize
    type: agent
    depends_on: [analyst_a, analyst_b]
    agent:
      prompt: |
        Angle A artifact: ${analyst_a.output_data.artifact_path}
        Angle B artifact: ${analyst_b.output_data.artifact_path}
        Read both artifacts for the full analysis.
        Synthesize into one recommendation.
```

### Pattern 3: Review loop with conditional

```yaml
steps:
  - id: implement
    type: agent
    agent: { agent_type: codex, role: coder, prompt: "Implement ${inputs.feature}" }

  - id: review
    type: agent
    depends_on: [implement]
    agent:
      role: reviewer
      prompt: "Review the implementation artifact: ${implement.output_data.artifact_path}"

  - id: check
    type: conditional
    depends_on: [review]
    conditional:
      branches:
        - condition: "${review.output} contains APPROVED"
          goto: done
        - condition: default
          goto: implement    # Loop back

  - id: done
    type: output
    depends_on: [review]
    output: { template: "${implement.output}" }
```

### Pattern 4: Entity chain (workflow A → workflow B)

**Workflow A** (producer):
```yaml
steps:
  - id: result
    type: output
    output:
      entity_name: "my-result"
      entity_kind: "analysis"
      entity_tag: "ready"
      entity_metadata:
        summary: "${analyze.output}"
```

**Workflow B** (consumer):
```yaml
triggers:
  - on_kind: "analysis"
    on_tag: "ready"
    input_map:
      analysis_kref: "${trigger.entity_kref}"

steps:
  - id: use_result
    type: agent
    agent:
      prompt: "The analysis kref is: ${inputs.analysis_kref}"
```

### Pattern 5: Resolve + fallback for multi-run

```yaml
inputs:
  - name: seed
    default: "initial value"

steps:
  - id: prior
    type: resolve
    resolve:
      kind: "my-output"
      tag: "ready"
      fail_if_missing: false

  - id: work
    type: agent
    depends_on: [prior]
    agent:
      prompt: |
        ## Resolved (empty on first run)
        Previous: ${prior.output_data.value}

        ## Seed (use when resolved is empty)
        Default: ${inputs.seed}

  - id: publish
    type: output
    depends_on: [work]
    output:
      entity_name: "my-output-latest"
      entity_kind: "my-output"
      entity_tag: "ready"
      entity_metadata:
        value: "${work.output}"
```

---

## Troubleshooting

### Workflow not found

```
workflow_loader: 'my-workflow' not found in Kumiho
```

Check: Is the YAML in `~/.revka/workflows/` or registered in Kumiho with an artifact?
The operator checks disk first, then resolves via `kref://Revka/Workflows/my-workflow.workflow`.

### Validation errors on load

```
workflow_loader: skipping 'my-workflow.r3' (...): N validation errors
```

Files matching `*.r{N}.yaml` are revision artifacts, not standalone workflows.
They're accessed via kref resolution, not directory scanning. This warning is
harmless — they're filtered out by the loader.

### Entity not found by resolve step

Check that:
1. The `kind` in your resolve config matches the `entity_kind` in the producing output step
2. The `tag` matches the `entity_tag`
3. The entity was published to the expected space (default: `Revka/WorkflowOutputs`)
4. The producing workflow actually completed successfully

### Artifact not created (403 error)

```
Failed to create artifact: Revision not found or is published.
```

This happens if the revision is tagged as `published` before the artifact is attached.
Revka v2026.4.21+ fixes this by attaching artifacts before publishing.

### Interpolation produces empty string

Unresolved `${step.output_data.key}` returns `""` if:
- The step hasn't run yet (check `depends_on`)
- The step's `output_data` doesn't contain that key
- A resolve step returned `found: false`

This is expected for first-run patterns — design prompts to handle empty values.
