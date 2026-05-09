# Construct Declarative Workflows — HOW-TO Guide

## Overview

Construct workflows are YAML files that define multi-step, multi-agent pipelines.
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
| 3 (highest) | `.construct/workflows/` | Project-local overrides |
| 2 | `~/.construct/workflows/` | User-global workflows |
| 1 (lowest) | `operator_mcp/workflow/builtins/` | Shipped defaults |

Later sources override earlier ones. The operator also checks **Kumiho**
(`Construct/Workflows` space) as a final fallback when a workflow isn't found on disk.

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
- **Cron**: Add a `triggers:` block — Construct auto-registers the schedule on save
- **Event chain**: A previous workflow's output entity triggers this one automatically

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

> **JSON auto-parse:** If an agent returns valid JSON, its keys are automatically
> merged into `output_data`. This means `${agent_step.output_data.any_key}` works
> without any extra configuration — just have the agent return a JSON object.

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
    subject: "Construct report"
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
    space: ""                  # Space path filter (default: Construct/WorkflowOutputs)
    mode: latest               # latest = single newest | all = list
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
| `<field>` | Each field from `fields` list, or all metadata if `fields` is empty |

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
    entity_space: "Construct/WorkflowOutputs"   # Default space
    entity_metadata:
      topic: "${inputs.topic}"
      summary: "${analyze.output}"
```

When `entity_name` and `entity_kind` are both set, the executor:
1. Creates a Kumiho item in `entity_space`
2. Creates a revision with the rendered template as content
3. Tags the revision with `entity_tag`
4. Fires a `revision.tagged` event — which can trigger downstream workflows

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

All string fields in steps support `${...}` interpolation. Variables resolve
at execution time from the current workflow state.

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

**Unresolved variables** remain as literal `${...}` strings (empty string for
missing output_data keys).

---

## Triggers and Workflow Chaining

### Cron triggers

```yaml
triggers:
  - cron: "0 9 * * 1"           # Every Monday 9am
```

When a workflow with a cron trigger is saved to Kumiho (via the UI), Construct
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
    input_map:                  # Map trigger data → workflow inputs
      arc_kref: "${trigger.entity_kref}"
      arc_name: "${trigger.metadata.arc_name}"
```

The event listener watches for `revision.tagged` events in
`/Construct/WorkflowOutputs`. When an output step publishes an entity matching
a trigger rule, the downstream workflow launches automatically.

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
5. Make sure the output step's `entity_kind` + `entity_tag` match what the
   resolve step searches for

---

## Saving and Artifact Persistence

When you save a workflow from the UI:

1. **API receives** the YAML definition via `PUT /api/workflows/{kref}`
2. **Kumiho revision** created with the definition in metadata
3. **YAML written to disk** at `~/.construct/workflows/{slug}.r{N}.yaml`
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
      entity_space: "Construct/WorkflowOutputs"
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

When enabled, the executor saves state to `~/.construct/workflow_checkpoints/{run_id}.json`
after each step completes and on workflow pause (human approval). This allows
resuming a workflow from where it left off after a crash or restart.

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
    agent: { agent_type: codex, role: coder, prompt: "Using: ${gather.output}" }

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
        Angle A: ${analyst_a.output}
        Angle B: ${analyst_b.output}
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
    agent: { role: reviewer, prompt: "Review: ${implement.output}" }

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

Check: Is the YAML in `~/.construct/workflows/` or registered in Kumiho with an artifact?
The operator checks disk first, then resolves via `kref://Construct/Workflows/my-workflow.workflow`.

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
3. The entity was published to the expected space (default: `Construct/WorkflowOutputs`)
4. The producing workflow actually completed successfully

### Artifact not created (403 error)

```
Failed to create artifact: Revision not found or is published.
```

This happens if the revision is tagged as `published` before the artifact is attached.
Construct v2026.4.21+ fixes this by attaching artifacts before publishing.

### Interpolation produces empty string

Unresolved `${step.output_data.key}` returns `""` if:
- The step hasn't run yet (check `depends_on`)
- The step's `output_data` doesn't contain that key
- A resolve step returned `found: false`

This is expected for first-run patterns — design prompts to handle empty values.
