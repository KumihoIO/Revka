/**
 * yamlSync.ts — Bidirectional sync between workflow task graph and YAML.
 *
 * Task YAML schema:
 *   steps:
 *     - id: step-1
 *       name: Greeting Task
 *       description: Send a greeting to the user
 *       type: agent
 *       agent_hints: [coder, researcher]
 *       skills: [code-review, rust-analysis]
 *       depends_on: step-0
 *       params: { ... }
 *
 * NOTE: legacy YAML may contain `action: <friendly verb>` (e.g.
 * `action: research`). On parse we map it through ACTION_TO_TYPE to a
 * canonical `type` and drop the `action` field. The emitter only writes
 * `type:` going forward.
 */

import type { Node, Edge } from '@xyflow/react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface TaskDefinition {
  id: string;
  name: string;
  description: string;
  /** Canonical step type (matches StepType in operator schema). Legacy YAML
   *  may carry `action:` instead — the parser maps it through ACTION_TO_TYPE
   *  and drops the `action` field, so callers should always read `type`. */
  type: string;
  agent_hints: string[];
  skills: string[];
  depends_on: string[];
  params?: Record<string, string>;
  /** Pre-assigned pool agent template name */
  assign?: string;
  /** Pool persona binding for agent steps — resolves `AgentStepConfig.template`
   *  at dispatch. Architect's persona-discovery flow writes this; round-trips
   *  through `agent.template:` in YAML. */
  template?: string;
  /** When true, executor skips the step and passes inputs straight through as output_data */
  disabled?: boolean;
  /** Gate-only fields */
  condition?: string;
  on_true?: string;
  on_false?: string;
  /** Optional value expressions for the true/false branches. When the gate
   *  matches a branch, the runtime evaluates this expression with the same
   *  simpleeval-based evaluator used for `condition` and emits the result
   *  on the conditional step's `output` — downstream steps reading
   *  `${gate.output}` see the matched branch's value. */
  on_true_value?: string;
  on_false_value?: string;
  /** Human-input channel */
  channel?: 'dashboard' | 'slack' | 'discord';
  /** Notify channels (multi-select) */
  channels?: string[];
  /** Parallel step children (parsed from `parallel.steps`) */
  parallel_steps?: string[];
  /** Parallel join strategy */
  parallel_join?: 'all' | 'any' | 'majority';
  /** Agent step: agent_type */
  agent_type?: 'claude' | 'codex';
  /** Agent step: role */
  role?: string;
  /** Agent step: prompt (template) */
  prompt?: string;
  /** Agent step: timeout */
  timeout?: number;
  /** Goto step: target */
  goto_target?: string;
  /** Goto step: max iterations */
  goto_max_iterations?: number;
  /** Group chat: topic */
  group_chat_topic?: string;
  /** Group chat: participants */
  group_chat_participants?: string[];
  /** Group chat: max rounds */
  group_chat_max_rounds?: number;
  /** Supervisor: task */
  supervisor_task?: string;
  /** Supervisor: max iterations */
  supervisor_max_iterations?: number;
  /** Shell: command */
  shell_command?: string;
  /** Output: format */
  output_format?: string;
  /** Output: Kumiho entity fields */
  entity_name?: string;
  entity_kind?: string;
  entity_tag?: string;
  entity_space?: string;
  entity_metadata?: Record<string, string>;
  /** Handoff: from_step */
  handoff_from?: string;
  /** Handoff: to agent type */
  handoff_to?: 'claude' | 'codex';
  /** Handoff: reason */
  handoff_reason?: string;
  // --- Step common: retry ---
  retry?: number;
  retry_delay?: number;
  // --- Agent: model override ---
  model?: string;
  // --- Shell: extra fields ---
  shell_timeout?: number;
  shell_allow_failure?: boolean;
  // --- Goto: condition guard ---
  goto_condition?: string;
  // --- Parallel: max concurrency ---
  parallel_max_concurrency?: number;
  // --- Human Input: message + timeout ---
  human_input_message?: string;
  human_input_timeout?: number;
  // --- Human Approval: message + timeout ---
  human_approval_message?: string;
  human_approval_timeout?: number;
  human_approval_channel?: string;
  human_approval_channel_id?: string;
  // --- Output: template ---
  output_template?: string;
  // --- A2A: full config ---
  a2a_url?: string;
  a2a_skill_id?: string;
  a2a_message?: string;
  a2a_timeout?: number;
  // --- MapReduce: full config ---
  map_reduce_task?: string;
  map_reduce_splits?: string[];
  map_reduce_mapper?: string;
  map_reduce_reducer?: string;
  map_reduce_concurrency?: number;
  map_reduce_timeout?: number;
  // --- Supervisor: extra fields ---
  supervisor_type?: string;
  supervisor_timeout?: number;
  // --- GroupChat: extra fields ---
  group_chat_moderator?: string;
  group_chat_strategy?: string;
  group_chat_timeout?: number;
  // --- Handoff: extra fields ---
  handoff_task?: string;
  handoff_timeout?: number;
  // --- Resolve: Kumiho entity lookup ---
  resolve_kind?: string;
  resolve_tag?: string;
  resolve_name_pattern?: string;
  resolve_space?: string;
  resolve_mode?: string;        // "latest" | "all"
  resolve_fields?: string[];
  resolve_fail_if_missing?: boolean;
  // --- ForEach: sequential loop ---
  for_each_steps?: string[];
  for_each_range?: string;
  for_each_items?: string[];
  for_each_variable?: string;
  for_each_carry_forward?: boolean;
  for_each_fail_fast?: boolean;
  for_each_max_iterations?: number;
  // --- Notify: first-class message/title ---
  notify_message?: string;
  notify_title?: string;
  // --- Python step: reusable JSON-IO subprocess (kref encoding, lead parsers, etc.) ---
  python_script?: string;        // path / builtin filename — XOR with python_code
  python_code?: string;          // inline source — XOR with python_script
  python_args?: string;          // JSON object string passed to script as args
  python_timeout?: number;
  python_allow_failure?: boolean;
  // --- Email step: outbound SMTP send + optional click-tracking link rewrite ---
  email_to?: string;             // single addr or comma-separated list
  email_subject?: string;
  email_body?: string;
  email_body_html?: string;
  email_from?: string;
  email_cc?: string;             // comma-separated list
  email_bcc?: string;
  email_reply_to?: string;
  email_track_clicks?: boolean;
  email_track_kref?: string;
  email_track_base_url?: string;
  email_smtp_host?: string;      // override; default reads from config.toml
  email_dry_run?: boolean;
  email_timeout?: number;
  // --- Image step (see operator_mcp/workflow/schema.py::ImageStepConfig) ---
  image_prompt?: string;
  image_count?: number;
  image_canvas?: boolean | string;
  image_register_artifact?: boolean;
  image_space?: string;
  image_item_name?: string;
  image_output_path?: string;
  image_output_pattern?: string;
  image_sandbox?: string;
  image_cwd?: string;
  image_timeout?: number;
  // --- Tag step: re-tag an existing Kumiho entity revision ---
  tag_item_kref?: string;        // kref of the item (supports ${...} interpolation)
  tag_value?: string;            // tag to apply to the latest revision
  tag_untag?: string;            // optional: tag to remove first
  // --- Deprecate step: deprecate a Kumiho item ---
  deprecate_item_kref?: string;  // kref of the item
  deprecate_reason?: string;     // optional deprecation reason
  /**
   * Encrypted auth-profile binding for agent / shell / python / email / a2a
   * steps. Format: `<provider>:<profile_name>`. Resolved at runtime via the
   * gateway's auth-profile resolve endpoint — token bytes never appear in
   * YAML, list responses, or agent system prompts.
   */
  auth?: string;
}

/** Step result from a workflow run — overlaid on nodes when viewing runs */
export interface StepRunInfo {
  status: 'pending' | 'running' | 'completed' | 'failed' | 'skipped';
  agent_id?: string;
  agent_type?: string;  // "claude" | "codex"
  role?: string;        // "coder" | "researcher" | "reviewer"
  template_name?: string; // agent pool template used
  action?: string;
  duration_s?: number;
  trust_score?: number; // 0.0–1.0 from Construct/AgentTrust
  skills?: string[];    // Skills assigned to this step
  transcript?: { speaker: string; content: string; round: number }[]; // group_chat discussion
  /** Set when the step is a human_approval step awaiting a dashboard/Discord decision */
  awaiting_approval?: boolean;
  approval_message?: string;
  approve_keywords?: string[];
  reject_keywords?: string[];
  /** Per-step interpolated inputs (PR #220), shape varies by step type. */
  input_data?: Record<string, unknown>;
  /** Per-step output blob (PR #220), shape varies by step type. */
  output_data?: Record<string, unknown>;
  /** Truncated preview of the step's primary text output. */
  output_preview?: string;
  /** Error message when the step failed. */
  error?: string;
}

/** Infer agent_type and role from type + hints (mirrors Python ACTION_DEFAULTS).
 *  Keys here are legacy `action` verbs and canonical step types alike — the
 *  fallback handles both cleanly. */
const ACTION_AGENT_MAP: Record<string, { agent_type: string; role: string }> = {
  research:  { agent_type: 'claude', role: 'researcher' },
  code:      { agent_type: 'codex',  role: 'coder' },
  review:    { agent_type: 'claude', role: 'reviewer' },
  deploy:    { agent_type: 'codex',  role: 'deployer' },
  test:      { agent_type: 'codex',  role: 'tester' },
  build:     { agent_type: 'codex',  role: 'builder' },
  notify:    { agent_type: 'claude', role: 'notifier' },
  summarize: { agent_type: 'claude', role: 'summarizer' },
  task:      { agent_type: 'claude', role: 'coder' },
  agent:     { agent_type: 'claude', role: 'coder' },
};

export function inferAgentFromTask(task: TaskDefinition): { agent_type: string; role: string } {
  const defaults = ACTION_AGENT_MAP[(task.type || '').toLowerCase()] ?? { agent_type: 'claude', role: 'coder' };
  let { agent_type, role } = defaults;
  // Agent hints override
  if (task.agent_hints.includes('codex') || task.agent_hints.includes('coder')) agent_type = 'codex';
  else if (task.agent_hints.includes('claude') || task.agent_hints.includes('researcher') || task.agent_hints.includes('reviewer')) agent_type = 'claude';
  for (const hint of task.agent_hints) {
    if (['coder', 'researcher', 'reviewer'].includes(hint)) { role = hint; break; }
  }
  return { agent_type, role };
}

export interface TaskNodeData {
  label: string;
  taskId: string;
  name: string;
  description: string;
  /** Canonical step type — see TaskDefinition.type. */
  type: string;
  agentHints: string[];
  skills: string[];
  /** Pre-assigned pool agent template name */
  assign: string;
  /** Pool persona binding (`agent.template`) — set by Architect's persona
   *  discovery or by a hand-edited YAML. Distinct from `assign`, which is
   *  only written by the AgentPicker side-panel UI. */
  template: string;
  /** When true, executor skips the step and passes inputs straight through as output_data */
  disabled?: boolean;
  paramCount: number;
  dependencyCount: number;
  /** Gate-only: condition expression */
  condition: string;
  /** Gate-only: optional value expression emitted on `output` when the
   *  true branch matches. See TaskDefinition.on_true_value. */
  onTrueValue: string;
  /** Gate-only: optional value expression emitted on `output` when the
   *  false branch matches. See TaskDefinition.on_false_value. */
  onFalseValue: string;
  /** Human-input channel */
  channel: string;
  /** Notify channels (multi-select) */
  channels: string[];
  /** Executor step type fields */
  agentType: string;
  role: string;
  prompt: string;
  timeout: number;
  parallelJoin: string;
  gotoTarget: string;
  gotoMaxIterations: number;
  groupChatTopic: string;
  groupChatParticipants: string[];
  groupChatMaxRounds: number;
  supervisorTask: string;
  supervisorMaxIterations: number;
  shellCommand: string;
  outputFormat: string;
  entityName: string;
  entityKind: string;
  entityTag: string;
  entitySpace: string;
  entityMetadata: Record<string, string>;
  handoffFrom: string;
  handoffTo: string;
  handoffReason: string;
  // Step common
  retry: number;
  retryDelay: number;
  // Agent
  model: string;
  // Shell
  shellTimeout: number;
  shellAllowFailure: boolean;
  // Goto
  gotoCondition: string;
  // Parallel
  parallelMaxConcurrency: number;
  // Human Input
  humanInputMessage: string;
  humanInputTimeout: number;
  // Human Approval
  humanApprovalMessage: string;
  humanApprovalTimeout: number;
  humanApprovalChannel: string;
  humanApprovalChannelId: string;
  // Output
  outputTemplate: string;
  // A2A
  a2aUrl: string;
  a2aSkillId: string;
  a2aMessage: string;
  a2aTimeout: number;
  // MapReduce
  mapReduceTask: string;
  mapReduceSplits: string[];
  mapReduceMapper: string;
  mapReduceReducer: string;
  mapReduceConcurrency: number;
  mapReduceTimeout: number;
  // Supervisor
  supervisorType: string;
  supervisorTimeout: number;
  // GroupChat
  groupChatModerator: string;
  groupChatStrategy: string;
  groupChatTimeout: number;
  // Handoff
  handoffTask: string;
  handoffTimeout: number;
  // Resolve
  resolveKind: string;
  resolveTag: string;
  resolveNamePattern: string;
  resolveSpace: string;
  resolveMode: string;
  resolveFields: string[];
  resolveFailIfMissing: boolean;
  // ForEach
  forEachSteps: string[];
  forEachRange: string;
  forEachItems: string[];
  forEachVariable: string;
  forEachCarryForward: boolean;
  forEachFailFast: boolean;
  forEachMaxIterations: number;
  // Notify — first-class message/title
  notifyMessage: string;
  notifyTitle: string;
  // Python step
  pythonScript: string;
  pythonCode: string;
  pythonArgs: string;
  pythonTimeout: number;
  pythonAllowFailure: boolean;
  // Email step
  emailTo: string;
  emailSubject: string;
  emailBody: string;
  emailBodyHtml: string;
  emailFrom: string;
  emailCc: string;
  emailBcc: string;
  emailReplyTo: string;
  emailTrackClicks: boolean;
  emailTrackKref: string;
  emailTrackBaseUrl: string;
  emailSmtpHost: string;
  emailDryRun: boolean;
  emailTimeout: number;
  // Image step
  imagePrompt: string;
  imageCount: number;
  imageCanvas: boolean;
  imageRegisterArtifact: boolean;
  imageSpace: string;
  imageItemName: string;
  imageOutputPath: string;
  imageOutputPattern: string;
  imageSandbox: string;
  imageCwd: string;
  imageTimeout: number;
  // Tag step
  tagItemKref: string;
  tagValue: string;
  tagUntag: string;
  // Deprecate step
  deprecateItemKref: string;
  deprecateReason: string;
  /** Encrypted auth-profile id (e.g. `gmail:work`) — resolved at runtime. */
  auth?: string;
  /** Run-mode overlay — populated when viewing a workflow run */
  runInfo?: StepRunInfo;
  /** P1.2 transient flag — set briefly after a remote SSE update touched
   *  this step so the node can pulse a highlight. Cleared after ~1.2s. */
  justUpdated?: boolean;
  [key: string]: unknown;
}

export interface TriggerDef {
  onKind: string;
  onTag: string;
  onNamePattern: string;
  inputMap: Record<string, string>;
}

export interface InputDef {
  name: string;
  type: 'string' | 'number' | 'boolean' | 'list';
  required: boolean;
  default: string;
  description: string;
}

export interface OutputDef {
  name: string;
  source: string;
  description: string;
}

export interface WorkflowMeta {
  name: string;
  version: string;
  description: string;
  tags: string[];
  triggers: TriggerDef[];
  inputs: InputDef[];
  outputs: OutputDef[];
  defaultCwd: string;
  defaultTimeout: number;
  maxTotalTime: number;
  checkpoint: boolean;
}

/** Legacy type kept for WorkflowGraph read-only viewer */
export interface StepNodeData {
  label: string;
  stepId: string;
  type: string;
  agent: string;
  paramCount: number;
  dependencyCount: number;
  [key: string]: unknown;
}

export type ParsedStep = TaskDefinition;

/** Map editor action / friendly verb to canonical executor step type.
 *  Hoisted above the parser so YAML containing legacy `action:` can be
 *  canonicalized at parse time. Self-mapping entries make the lookup safe
 *  to use against either an action verb or a canonical type. */
export const ACTION_TO_TYPE: Record<string, string> = {
  research: 'agent', code: 'agent', review: 'agent', deploy: 'agent',
  test: 'agent', build: 'agent', notify: 'notify', summarize: 'agent',
  task: 'agent', approve: 'human_approval', gate: 'conditional',
  human_input: 'human_input',
  // Executor types map to themselves
  agent: 'agent', parallel: 'parallel', shell: 'shell', goto: 'goto',
  output: 'output', conditional: 'conditional', group_chat: 'group_chat',
  supervisor: 'supervisor', map_reduce: 'map_reduce', handoff: 'handoff',
  a2a: 'a2a', resolve: 'resolve', for_each: 'for_each',
  human_approval: 'human_approval',
  // New step types — see operator_mcp/workflow/schema.py
  python: 'python', email: 'email',
  tag: 'tag', deprecate: 'deprecate',
};

/** Resolve legacy `action:` verb or `type:` value to a canonical step type. */
function canonicalizeType(raw: string): string {
  return ACTION_TO_TYPE[raw] ?? raw;
}

// ---------------------------------------------------------------------------
// YAML → Tasks parser (lightweight, no external YAML lib)
// ---------------------------------------------------------------------------

export function parseWorkflowYaml(yaml: string): TaskDefinition[] {
  const tasks: TaskDefinition[] = [];
  const lines = yaml.split('\n');

  let inSteps = false;
  let current: Partial<TaskDefinition> | null = null;
  let inParams = false;
  let inArrayField: 'agent_hints' | 'skills' | 'depends_on' | 'channels' | null = null;
  let paramCount = 0;

  for (const line of lines) {
    const trimmed = line.trim();

    // Detect top-level `steps:` key
    if (/^steps\s*:/.test(trimmed)) {
      inSteps = true;
      continue;
    }

    if (!inSteps) continue;

    // End of steps section — new top-level key
    if (
      trimmed &&
      !trimmed.startsWith('-') &&
      !trimmed.startsWith('#') &&
      !line.startsWith(' ') &&
      !line.startsWith('\t')
    ) {
      break;
    }

    // New step entry
    if (trimmed.startsWith('- id:') || trimmed.startsWith('-  id:')) {
      if (current?.id) {
        tasks.push(finalizeTask(current, paramCount));
      }
      current = { id: trimmed.replace(/^-\s*id:\s*/, '').trim().replace(/^["']|["']$/g, '') };
      inParams = false;
      inArrayField = null;
      paramCount = 0;
      continue;
    }

    if (!current) continue;

    // Step-level fields
    const fieldMatch = trimmed.match(/^(\w[\w_]*):\s*(.*)/);
    if (fieldMatch) {
      const [, key, rawValue] = fieldMatch;
      const value = (rawValue ?? '').trim().replace(/^["']|["']$/g, '');

      inParams = false;
      inArrayField = null;

      if (key === 'name') {
        current.name = value;
      } else if (key === 'description' || key === 'desc') {
        current.description = value;
      } else if (key === 'type' || key === 'action' || key === 'task') {
        // `type:` wins over legacy `action:` if both appear on the same step.
        // Canonicalize both through ACTION_TO_TYPE so callers always see a
        // valid step type — the `action` field is dropped going forward.
        if (key === 'type' || !current.type) {
          current.type = canonicalizeType(value);
        }
      } else if (key === 'condition') {
        current.condition = value;
      } else if (key === 'on_true' || key === 'onTrue') {
        current.on_true = value;
      } else if (key === 'on_false' || key === 'onFalse') {
        current.on_false = value;
      } else if (key === 'on_true_value' || key === 'onTrueValue') {
        current.on_true_value = value;
      } else if (key === 'on_false_value' || key === 'onFalseValue') {
        current.on_false_value = value;
      } else if (key === 'channel') {
        current.channel = value as TaskDefinition['channel'];
      } else if (key === 'agent_hints' || key === 'agentHints') {
        if (value.startsWith('[')) {
          current.agent_hints = parseInlineArray(value);
        } else if (value) {
          current.agent_hints = [value];
        } else {
          inArrayField = 'agent_hints';
          current.agent_hints = current.agent_hints || [];
        }
      } else if (key === 'skills') {
        if (value.startsWith('[')) {
          current.skills = parseInlineArray(value);
        } else if (value) {
          current.skills = [value];
        } else {
          inArrayField = 'skills';
          current.skills = current.skills || [];
        }
      } else if (key === 'depends_on' || key === 'dependsOn' || key === 'after') {
        if (value.startsWith('[')) {
          current.depends_on = parseInlineArray(value);
        } else if (value) {
          current.depends_on = [value];
        } else {
          inArrayField = 'depends_on';
          current.depends_on = current.depends_on || [];
        }
      } else if (key === 'channels') {
        if (value.startsWith('[')) {
          current.channels = parseInlineArray(value);
        } else if (value) {
          current.channels = [value];
        } else {
          inArrayField = 'channels';
          current.channels = current.channels || [];
        }
      } else if (key === 'retry') {
        current.retry = parseInt(value) || 0;
      } else if (key === 'retry_delay' || key === 'retryDelay') {
        current.retry_delay = parseFloat(value) || 5;
      } else if (key === 'disabled') {
        current.disabled = value.toLowerCase() === 'true';
      } else if (key === 'assign') {
        // Top-level assign: only. Nested `agent.template:` is captured
        // separately by extractStepBlockData → data.template, so the two
        // YAML keys preserve the AgentPicker (assign) vs persona (template)
        // distinction across save/reload.
        current.assign = value;
      } else if (key === 'params' || key === 'parameters' || key === 'config') {
        inParams = true;
        if (value.startsWith('{')) {
          try {
            paramCount = Object.keys(JSON.parse(value)).length;
          } catch {
            paramCount = 1;
          }
          inParams = false;
        }
      }
      continue;
    }

    // Param entries
    if (inParams && trimmed.includes(':')) {
      paramCount++;
    }

    // Array items for multi-line arrays
    if (trimmed.startsWith('- ') && inArrayField && current[inArrayField] !== undefined) {
      const item = trimmed.slice(2).trim().replace(/^["']|["']$/g, '');
      if (item) {
        (current[inArrayField] as string[]).push(item);
      }
    }
  }

  // Push last task
  if (current?.id) {
    tasks.push(finalizeTask(current, paramCount));
  }

  // Extract nested block data from raw YAML (the line-by-line parser can't handle nested blocks)
  const parallelChildren = extractParallelChildren(yaml);
  const blockData = extractStepBlockData(yaml);
  for (const task of tasks) {
    const children = parallelChildren.get(task.id);
    if (children) task.parallel_steps = children;
    const extra = blockData.get(task.id);
    if (extra) Object.assign(task, extra);
  }

  return tasks;
}

export function parseWorkflowMeta(yaml: string): WorkflowMeta {
  const meta: WorkflowMeta = {
    name: '', version: '1.0', description: '', tags: [],
    triggers: [], inputs: [], outputs: [],
    defaultCwd: '', defaultTimeout: 300, maxTotalTime: 3600, checkpoint: true,
  };

  const lines = yaml.split('\n');
  let i = 0;

  while (i < lines.length) {
    const line = lines[i]!;
    const trimmed = line.trim();

    if (/^steps\s*:/.test(trimmed)) break;

    const m = trimmed.match(/^(\w[\w_]*):\s*(.*)/);
    if (m) {
      const key = m[1]!;
      const val = (m[2] ?? '').trim().replace(/^["']|["']$/g, '');

      if (key === 'name') { meta.name = val; }
      else if (key === 'version') { meta.version = val; }
      else if (key === 'description') {
        if (val.startsWith('>') || val.startsWith('|')) {
          let desc = '';
          i++;
          while (i < lines.length && lines[i]!.match(/^\s+\S/)) {
            desc += (desc ? ' ' : '') + lines[i]!.trim();
            i++;
          }
          meta.description = desc;
          continue;
        } else { meta.description = val; }
      }
      else if (key === 'tags') {
        if (val.startsWith('[')) {
          meta.tags = val.slice(1, -1).split(',').map(s => s.trim().replace(/["']/g, '')).filter(Boolean);
        }
      }
      else if (key === 'default_cwd') { meta.defaultCwd = val; }
      else if (key === 'default_timeout') { meta.defaultTimeout = parseFloat(val) || 300; }
      else if (key === 'max_total_time') { meta.maxTotalTime = parseFloat(val) || 3600; }
      else if (key === 'checkpoint') { meta.checkpoint = val !== 'false'; }
      else if (key === 'triggers') {
        i++;
        while (i < lines.length) {
          const tl = lines[i]!;
          const tt = tl.trim();
          if (tt.startsWith('- on_kind:') || tt.startsWith('- cron:')) {
            const trigger: TriggerDef = { onKind: '', onTag: 'ready', onNamePattern: '', inputMap: {} };
            if (tt.startsWith('- on_kind:')) {
              trigger.onKind = tt.replace(/^-\s*on_kind:\s*/, '').replace(/["']/g, '').trim();
            } else if (tt.startsWith('- cron:')) {
              trigger.inputMap.__cron = tt.replace(/^-\s*cron:\s*/, '').replace(/["']/g, '').trim();
            }
            i++;
            while (i < lines.length) {
              const il = lines[i]!.trim();
              if (!il || il.startsWith('- ') || !lines[i]!.match(/^\s/)) break;
              if (il.startsWith('#')) { i++; continue; }
              const tm = il.match(/^(\w[\w_]*):\s*(.*)/);
              if (tm) {
                const tk = tm[1]!;
                const tv = (tm[2] ?? '').trim().replace(/^["']|["']$/g, '');
                if (tk === 'on_tag') trigger.onTag = tv;
                else if (tk === 'on_name_pattern') trigger.onNamePattern = tv;
                else if (tk === 'on_kind') trigger.onKind = tv;
                else if (tk === 'cron') trigger.inputMap.__cron = tv;
                else if (tk === 'input_map') {
                  i++;
                  while (i < lines.length) {
                    const ml = lines[i]!.trim();
                    if (!ml || !lines[i]!.match(/^\s{4,}/)) break;
                    if (ml.startsWith('#')) { i++; continue; }
                    const mkv = ml.match(/^(\w[\w_]*):\s*(.*)/);
                    if (mkv) trigger.inputMap[mkv[1]!] = (mkv[2] ?? '').trim().replace(/^["']|["']$/g, '');
                    i++;
                  }
                  continue;
                }
              }
              i++;
            }
            meta.triggers.push(trigger);
            continue;
          }
          if (tt && !tl.match(/^\s/) && !tt.startsWith('#')) break;
          if (!tt) { i++; continue; }
          i++;
        }
        continue;
      }
      else if (key === 'inputs') {
        i++;
        while (i < lines.length) {
          const il = lines[i]!;
          const it = il.trim();
          if (it.startsWith('- name:')) {
            const input: InputDef = {
              name: it.replace(/^-\s*name:\s*/, '').replace(/["']/g, '').trim(),
              type: 'string', required: true, default: '', description: '',
            };
            i++;
            while (i < lines.length) {
              const fl = lines[i]!.trim();
              if (!fl || fl.startsWith('- ') || !lines[i]!.match(/^\s/)) break;
              if (fl.startsWith('#')) { i++; continue; }
              const fm = fl.match(/^(\w[\w_]*):\s*(.*)/);
              if (fm) {
                const fk = fm[1]!;
                const fv = (fm[2] ?? '').trim().replace(/^["']|["']$/g, '');
                if (fk === 'type') input.type = fv as InputDef['type'];
                else if (fk === 'required') input.required = fv !== 'false';
                else if (fk === 'default') input.default = fv;
                else if (fk === 'description') input.description = fv;
              }
              i++;
            }
            meta.inputs.push(input);
            continue;
          }
          if (it && !il.match(/^\s/) && !it.startsWith('#')) break;
          i++;
        }
        continue;
      }
      else if (key === 'outputs') {
        i++;
        while (i < lines.length) {
          const ol = lines[i]!;
          const ot = ol.trim();
          if (ot.startsWith('- name:')) {
            const output: OutputDef = {
              name: ot.replace(/^-\s*name:\s*/, '').replace(/["']/g, '').trim(),
              source: '', description: '',
            };
            i++;
            while (i < lines.length) {
              const fl = lines[i]!.trim();
              if (!fl || fl.startsWith('- ') || !lines[i]!.match(/^\s/)) break;
              if (fl.startsWith('#')) { i++; continue; }
              const fm = fl.match(/^(\w[\w_]*):\s*(.*)/);
              if (fm) {
                const fk = fm[1]!;
                const fv = (fm[2] ?? '').trim().replace(/^["']|["']$/g, '');
                if (fk === 'source') output.source = fv;
                else if (fk === 'description') output.description = fv;
              }
              i++;
            }
            meta.outputs.push(output);
            continue;
          }
          if (ot && !ol.match(/^\s/) && !ot.startsWith('#')) break;
          i++;
        }
        continue;
      }
    }
    i++;
  }

  return meta;
}

/** Extract parallel.steps arrays from step blocks that have type: parallel. */
function extractParallelChildren(yaml: string): Map<string, string[]> {
  const result = new Map<string, string[]>();
  // Split YAML into per-step blocks (each starts with `- id:`)
  const stepBlocks = yaml.split(/(?=^\s*- id:)/m);
  for (const block of stepBlocks) {
    const idMatch = block.match(/-\s*id:\s*(\S+)/);
    if (!idMatch) continue;
    const stepId = idMatch[1]!.replace(/^["']|["']$/g, '');
    if (!block.match(/type:\s*parallel/)) continue;
    const stepsMatch = block.match(/steps:\s*\[([^\]]+)\]/);
    if (!stepsMatch) continue;
    const children = stepsMatch[1]!
      .split(',')
      .map((s) => s.trim().replace(/^["']|["']$/g, ''))
      .filter(Boolean);
    if (children.length > 0) result.set(stepId, children);
  }
  return result;
}

/** Extract nested block fields (agent, goto, group_chat, supervisor, shell, output, handoff) from YAML. */
function extractStepBlockData(yaml: string): Map<string, Partial<TaskDefinition>> {
  const result = new Map<string, Partial<TaskDefinition>>();
  const stepBlocks = yaml.split(/(?=^\s*- id:)/m);

  for (const block of stepBlocks) {
    const idMatch = block.match(/-\s*id:\s*(\S+)/);
    if (!idMatch) continue;
    const stepId = idMatch[1]!.replace(/^["']|["']$/g, '');
    const data: Partial<TaskDefinition> = {};

    // Top-level `assign:` — AgentPicker pool-agent binding. Distinct from
    // `agent.template:` (Architect persona binding) which is captured below.
    const assign = block.match(/^\s*assign:\s*(\S+)/m);
    if (assign) data.assign = assign[1]!.replace(/^["']|["']$/g, '');

    // Agent block: agent_type, role, prompt, timeout
    if (block.match(/\bagent\s*:/m)) {
      const agentType = block.match(/agent_type:\s*(\S+)/);
      const role = block.match(/\brole:\s*(\S+)/);
      const timeout = block.match(/timeout:\s*(\d+)/);
      const template = block.match(/\btemplate:\s*(\S+)/);
      if (agentType) data.agent_type = agentType[1]!.replace(/["']/g, '') as 'claude' | 'codex';
      if (role) data.role = role[1]!.replace(/["']/g, '');
      if (timeout) data.timeout = parseInt(timeout[1]!);
      if (template) data.template = template[1]!.replace(/["']/g, '');
      // Extract prompt (may be multi-line with |)
      const promptMatch = block.match(/prompt:\s*\|?\s*\n([\s\S]*?)(?=\n\s{6}\w|\n\s{4}\w|\n\s{2}-|\n\w|$)/);
      if (promptMatch) {
        data.prompt = promptMatch[1]!.split('\n').map(l => l.replace(/^\s{8}/, '')).join('\n').trim();
      } else {
        const singlePrompt = block.match(/prompt:\s*["']?(.+?)["']?\s*$/m);
        if (singlePrompt) data.prompt = singlePrompt[1]!;
      }
      const agentModel = block.match(/\bmodel:\s*(\S+)/);
      if (agentModel) data.model = agentModel[1]!.replace(/["']/g, '');
      const auth = block.match(/^\s{6}auth:\s*(.+)$/m);
      if (auth) data.auth = auth[1]!.trim().replace(/^["']|["']$/g, '');
    }

    // Parallel block: join, max_concurrency
    if (block.match(/type:\s*parallel/)) {
      const join = block.match(/join:\s*(\S+)/);
      if (join) data.parallel_join = join[1]!.replace(/["']/g, '') as TaskDefinition['parallel_join'];
      const conc = block.match(/max_concurrency:\s*(\d+)/);
      if (conc) data.parallel_max_concurrency = parseInt(conc[1]!);
    }

    // Goto block: target, max_iterations, condition
    if (block.match(/type:\s*goto/)) {
      const target = block.match(/target:\s*(\S+)/);
      const maxIter = block.match(/max_iterations:\s*(\d+)/);
      const gotoCond = block.match(/condition:\s*(.+)/);
      if (target) data.goto_target = target[1]!.replace(/["']/g, '');
      if (maxIter) data.goto_max_iterations = parseInt(maxIter[1]!);
      if (gotoCond) data.goto_condition = gotoCond[1]!.trim().replace(/^["']|["']$/g, '');
    }

    // Group chat block
    if (block.match(/type:\s*group_chat/)) {
      const topic = block.match(/topic:\s*(.+)/);
      const maxRounds = block.match(/max_rounds:\s*(\d+)/);
      if (topic) data.group_chat_topic = topic[1]!.trim().replace(/^["']|["']$/g, '');
      if (maxRounds) data.group_chat_max_rounds = parseInt(maxRounds[1]!);
      const participants = block.match(/participants:\s*\[([^\]]+)\]/);
      if (participants) {
        data.group_chat_participants = participants[1]!.split(',').map(s => s.trim().replace(/["']/g, '')).filter(Boolean);
      }
      const mod = block.match(/moderator:\s*(\S+)/);
      if (mod) data.group_chat_moderator = mod[1]!.replace(/["']/g, '');
      const strat = block.match(/strategy:\s*(\S+)/);
      if (strat) data.group_chat_strategy = strat[1]!.replace(/["']/g, '');
      const gcTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (gcTimeout) data.group_chat_timeout = parseFloat(gcTimeout[1]!);
    }

    // Supervisor block
    if (block.match(/type:\s*supervisor/)) {
      const task = block.match(/\btask:\s*(.+)/);
      const maxIter = block.match(/max_iterations:\s*(\d+)/);
      if (task) data.supervisor_task = task[1]!.trim().replace(/^["']|["']$/g, '');
      if (maxIter) data.supervisor_max_iterations = parseInt(maxIter[1]!);
      const supType = block.match(/supervisor_type:\s*(\S+)/);
      if (supType) data.supervisor_type = supType[1]!.replace(/["']/g, '');
      const supTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (supTimeout) data.supervisor_timeout = parseFloat(supTimeout[1]!);
    }

    // Shell block
    if (block.match(/type:\s*shell/)) {
      const cmd = block.match(/command:\s*(.+)/);
      if (cmd) data.shell_command = cmd[1]!.trim().replace(/^["']|["']$/g, '');
      const shellTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (shellTimeout) data.shell_timeout = parseFloat(shellTimeout[1]!);
      const allowFail = block.match(/allow_failure:\s*(true|false)/i);
      if (allowFail) data.shell_allow_failure = allowFail[1]!.toLowerCase() === 'true';
      const auth = block.match(/^\s{6}auth:\s*(.+)$/m);
      if (auth) data.auth = auth[1]!.trim().replace(/^["']|["']$/g, '');
    }

    // Python block — script XOR code; args is a JSON object string
    if (block.match(/type:\s*python/)) {
      const script = block.match(/script:\s*(.+)/);
      if (script) data.python_script = script[1]!.trim().replace(/^["']|["']$/g, '');
      // Inline code: YAML | block scalar; permissive parser — captures
      // everything indented under `code: |` until the next sibling key.
      const codeBlock = block.match(/code:\s*\|\s*\n([\s\S]*?)(?=\n\s{6}\w|\n\s{4}\w|\n\s{2}-|\n\w|$)/);
      if (codeBlock) {
        data.python_code = codeBlock[1]!.split('\n').map(l => l.replace(/^\s{8}/, '')).join('\n').trim();
      } else {
        const inlineCode = block.match(/code:\s*["'](.+?)["']\s*$/m);
        if (inlineCode) data.python_code = inlineCode[1]!;
      }
      // args is a JSON object on a single line (most common in builtins/)
      const args = block.match(/args:\s*(\{[^\n]*\})/);
      if (args) data.python_args = args[1]!;
      const pyTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (pyTimeout) data.python_timeout = parseFloat(pyTimeout[1]!);
      const pyAllowFail = block.match(/allow_failure:\s*(true|false)/i);
      if (pyAllowFail) data.python_allow_failure = pyAllowFail[1]!.toLowerCase() === 'true';
      const auth = block.match(/^\s{6}auth:\s*(.+)$/m);
      if (auth) data.auth = auth[1]!.trim().replace(/^["']|["']$/g, '');
    }

    // Email block — many fields; only the first three are required.
    if (block.match(/type:\s*email/)) {
      const to = block.match(/\bto:\s*(.+)/);
      if (to) data.email_to = to[1]!.trim().replace(/^["']|["']$/g, '');
      const subject = block.match(/subject:\s*(.+)/);
      if (subject) data.email_subject = subject[1]!.trim().replace(/^["']|["']$/g, '');
      // body / body_html may be multi-line block scalars
      const bodyBlock = block.match(/\bbody:\s*\|\s*\n([\s\S]*?)(?=\n\s{6}\w|\n\s{4}\w|\n\s{2}-|\n\w|$)/);
      if (bodyBlock) {
        data.email_body = bodyBlock[1]!.split('\n').map(l => l.replace(/^\s{8}/, '')).join('\n').trim();
      } else {
        const inlineBody = block.match(/\bbody:\s*["'](.+?)["']\s*$/m);
        if (inlineBody) data.email_body = inlineBody[1]!;
      }
      const htmlBlock = block.match(/body_html:\s*\|\s*\n([\s\S]*?)(?=\n\s{6}\w|\n\s{4}\w|\n\s{2}-|\n\w|$)/);
      if (htmlBlock) {
        data.email_body_html = htmlBlock[1]!.split('\n').map(l => l.replace(/^\s{8}/, '')).join('\n').trim();
      }
      const fromAddr = block.match(/from_address:\s*(.+)/);
      if (fromAddr) data.email_from = fromAddr[1]!.trim().replace(/^["']|["']$/g, '');
      const cc = block.match(/cc:\s*\[([^\]]*)\]/);
      if (cc) data.email_cc = cc[1]!.split(',').map(s => s.trim().replace(/["']/g, '')).filter(Boolean).join(', ');
      const bcc = block.match(/bcc:\s*\[([^\]]*)\]/);
      if (bcc) data.email_bcc = bcc[1]!.split(',').map(s => s.trim().replace(/["']/g, '')).filter(Boolean).join(', ');
      const replyTo = block.match(/reply_to:\s*(.+)/);
      if (replyTo) data.email_reply_to = replyTo[1]!.trim().replace(/^["']|["']$/g, '');
      const trackClicks = block.match(/track_clicks:\s*(true|false)/i);
      if (trackClicks) data.email_track_clicks = trackClicks[1]!.toLowerCase() === 'true';
      const trackKref = block.match(/track_kref:\s*(.+)/);
      if (trackKref) data.email_track_kref = trackKref[1]!.trim().replace(/^["']|["']$/g, '');
      const trackBase = block.match(/track_base_url:\s*(.+)/);
      if (trackBase) data.email_track_base_url = trackBase[1]!.trim().replace(/^["']|["']$/g, '');
      const smtpHost = block.match(/smtp_host:\s*(.+)/);
      if (smtpHost) data.email_smtp_host = smtpHost[1]!.trim().replace(/^["']|["']$/g, '');
      const dryRun = block.match(/dry_run:\s*(true|false)/i);
      if (dryRun) data.email_dry_run = dryRun[1]!.toLowerCase() === 'true';
      const emailTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (emailTimeout) data.email_timeout = parseFloat(emailTimeout[1]!);
      const auth = block.match(/^\s{6}auth:\s*(.+)$/m);
      if (auth) data.auth = auth[1]!.trim().replace(/^["']|["']$/g, '');
    }

    // Image block
    if (block.match(/type:\s*image/)) {
      const promptBlock = block.match(/prompt:\s*\|\s*\n([\s\S]*?)(?=\n\s{6}\w|\n\s{4}\w|\n\s{2}-|\n\w|$)/);
      if (promptBlock) {
        data.image_prompt = promptBlock[1]!.split('\n').map(l => l.replace(/^\s{8}/, '')).join('\n').trim();
      } else {
        const inlinePrompt = block.match(/prompt:\s*["']?(.+?)["']?\s*$/m);
        if (inlinePrompt) data.image_prompt = inlinePrompt[1]!.trim().replace(/^["']|["']$/g, '');
      }
      const imgCount = block.match(/^\s{6}count:\s*(\d+)/m);
      if (imgCount) data.image_count = parseInt(imgCount[1]!);
      const imgCanvas = block.match(/^\s{6}canvas:\s*(true|false)/im);
      if (imgCanvas) data.image_canvas = imgCanvas[1]!.toLowerCase() === 'true';
      const imgRegister = block.match(/register_artifact:\s*(true|false)/i);
      if (imgRegister) data.image_register_artifact = imgRegister[1]!.toLowerCase() === 'true';
      const imgSpace = block.match(/^\s{6}space:\s*(.+)/m);
      if (imgSpace) data.image_space = imgSpace[1]!.trim().replace(/^["']|["']$/g, '');
      const imgItemName = block.match(/item_name:\s*(.+)/);
      if (imgItemName) data.image_item_name = imgItemName[1]!.trim().replace(/^["']|["']$/g, '');
      const imgOutPath = block.match(/output_path:\s*(.+)/);
      if (imgOutPath) data.image_output_path = imgOutPath[1]!.trim().replace(/^["']|["']$/g, '');
      const imgOutPattern = block.match(/output_pattern:\s*(.+)/);
      if (imgOutPattern) data.image_output_pattern = imgOutPattern[1]!.trim().replace(/^["']|["']$/g, '');
      const imgSandbox = block.match(/sandbox:\s*(read-only|workspace-write|danger-full-access)/);
      if (imgSandbox) data.image_sandbox = imgSandbox[1]!;
      const imgCwd = block.match(/^\s{6}cwd:\s*(.+)/m);
      if (imgCwd) data.image_cwd = imgCwd[1]!.trim().replace(/^["']|["']$/g, '');
      const imgTimeout = block.match(/^\s{6}timeout:\s*(\d+(?:\.\d+)?)/m);
      if (imgTimeout) data.image_timeout = parseFloat(imgTimeout[1]!);
    }

    // Output block
    if (block.match(/type:\s*output/)) {
      const fmt = block.match(/format:\s*(\S+)/);
      if (fmt) data.output_format = fmt[1]!.replace(/["']/g, '');
      // Template (may be multi-line with |)
      const tplMatch = block.match(/template:\s*\|?\s*\n([\s\S]*?)(?=\n\s{6}\w|\n\s{4}\w|\n\s{2}-|\n\w|$)/);
      if (tplMatch) {
        data.output_template = tplMatch[1]!.split('\n').map(l => l.replace(/^\s{8}/, '')).join('\n').trim();
      } else {
        const singleTpl = block.match(/template:\s*["']?(.+?)["']?\s*$/m);
        if (singleTpl && !singleTpl[1]!.startsWith('|')) data.output_template = singleTpl[1]!;
      }
      const eName = block.match(/entity_name:\s*(.+)/);
      if (eName) data.entity_name = eName[1]!.trim().replace(/^["']|["']$/g, '');
      const eKind = block.match(/entity_kind:\s*(.+)/);
      if (eKind) data.entity_kind = eKind[1]!.trim().replace(/^["']|["']$/g, '');
      const eTag = block.match(/entity_tag:\s*(.+)/);
      if (eTag) data.entity_tag = eTag[1]!.trim().replace(/^["']|["']$/g, '');
      const eSpace = block.match(/entity_space:\s*(.+)/);
      if (eSpace) data.entity_space = eSpace[1]!.trim().replace(/^["']|["']$/g, '');
      // Parse entity_metadata as key-value pairs
      const metaMatch = block.match(/entity_metadata:\s*\n((?:\s+\S+:\s*.+\n?)*)/);
      if (metaMatch) {
        const meta: Record<string, string> = {};
        for (const mLine of metaMatch[1]!.split('\n')) {
          const kv = mLine.trim().match(/^(\S+):\s*(.+)/);
          if (kv) meta[kv[1]!] = kv[2]!.replace(/^["']|["']$/g, '');
        }
        if (Object.keys(meta).length > 0) data.entity_metadata = meta;
      }
    }

    // Notify block — parse first-class notify.channels, notify.message, notify.title
    {
      const notifyMatch = block.match(/^\s{4}notify:\s*\n((?:\s{5,}.*\n?)*)/m);
      if (notifyMatch) {
        const nb = notifyMatch[1]!;
        const nChannels = nb.match(/^\s+channels:\s*\[([^\]]+)\]/m);
        if (nChannels) {
          data.channels = dedupChannels(parseInlineArray(nChannels[1]!));
        }
        const nMsgMulti = nb.match(/^\s+message:\s*\|\s*\n([\s\S]*?)(?=\n\s{0,6}\S|\n$|$)/m);
        if (nMsgMulti) {
          const raw = nMsgMulti[1]!;
          const msgLines = raw.split('\n');
          const indents = msgLines
            .filter((l) => l.trim().length > 0)
            .map((l) => (l.match(/^(\s*)/)?.[1]?.length ?? 0));
          const minIndent = indents.length ? Math.min(...indents) : 0;
          data.notify_message = msgLines.map((l) => l.slice(minIndent)).join('\n').trimEnd();
        } else {
          const nMsgSingle = nb.match(/^\s+message:\s*(?!\|)["']?(.+?)["']?\s*$/m);
          if (nMsgSingle) data.notify_message = nMsgSingle[1]!;
        }
        const nTitle = nb.match(/^\s+title:\s*["']?(.+?)["']?\s*$/m);
        if (nTitle) data.notify_title = nTitle[1]!;
      }
    }

    // Handoff block
    if (block.match(/type:\s*handoff/)) {
      const from = block.match(/from_step:\s*(\S+)/);
      const to = block.match(/to_agent_type:\s*(\S+)/);
      const reason = block.match(/reason:\s*(.+)/);
      if (from) data.handoff_from = from[1]!.replace(/["']/g, '');
      if (to) data.handoff_to = to[1]!.replace(/["']/g, '') as 'claude' | 'codex';
      if (reason) data.handoff_reason = reason[1]!.trim().replace(/^["']|["']$/g, '');
      const htask = block.match(/\btask:\s*(.+)/);
      if (htask) data.handoff_task = htask[1]!.trim().replace(/^["']|["']$/g, '');
      const hTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (hTimeout) data.handoff_timeout = parseFloat(hTimeout[1]!);
    }

    // Human Input block
    if (block.match(/\bhuman_input\s*:/m) || block.match(/type:\s*human_input/)) {
      const hiMsg = block.match(/message:\s*\|?\s*\n([\s\S]*?)(?=\n\s{6}\w|\n\s{4}\w|\n\s{2}-|\n\w|$)/);
      if (hiMsg) {
        data.human_input_message = hiMsg[1]!.split('\n').map(l => l.replace(/^\s{8}/, '')).join('\n').trim();
      } else {
        const hiMsgSingle = block.match(/message:\s*["']?(.+?)["']?\s*$/m);
        if (hiMsgSingle) data.human_input_message = hiMsgSingle[1]!;
      }
      const hiTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (hiTimeout) data.human_input_timeout = parseFloat(hiTimeout[1]!);
    }

    // Human Approval block
    if (block.match(/\bhuman_approval\s*:/m) || block.match(/type:\s*human_approval/)) {
      // Message (may be multi-line with |)
      const haMsgMulti = block.match(/message:\s*\|?\s*\n([\s\S]*?)(?=\n\s{6}\w|\n\s{4}\w|\n\s{2}-|\n\w|$)/);
      if (haMsgMulti) {
        data.human_approval_message = haMsgMulti[1]!.split('\n').map(l => l.replace(/^\s{8}/, '')).join('\n').trim();
      } else {
        const haMsg = block.match(/message:\s*["']?(.+?)["']?\s*$/m);
        if (haMsg) data.human_approval_message = haMsg[1]!;
      }
      const haTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (haTimeout) data.human_approval_timeout = parseFloat(haTimeout[1]!);
      const haChannel = block.match(/channel:\s*(\S+)/);
      if (haChannel) data.human_approval_channel = haChannel[1]!.replace(/["']/g, '');
      const haChannelId = block.match(/channel_id:\s*(\S+)/);
      if (haChannelId) data.human_approval_channel_id = haChannelId[1]!.replace(/["']/g, '');
    }

    // A2A block
    if (block.match(/type:\s*a2a/) || block.match(/\ba2a\s*:/m)) {
      const a2aUrl = block.match(/url:\s*(\S+)/);
      if (a2aUrl) data.a2a_url = a2aUrl[1]!.replace(/["']/g, '');
      const a2aSkill = block.match(/skill_id:\s*(\S+)/);
      if (a2aSkill) data.a2a_skill_id = a2aSkill[1]!.replace(/["']/g, '');
      const a2aMsg = block.match(/message:\s*["']?(.+?)["']?\s*$/m);
      if (a2aMsg) data.a2a_message = a2aMsg[1]!;
      const a2aTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (a2aTimeout) data.a2a_timeout = parseFloat(a2aTimeout[1]!);
      const auth = block.match(/^\s{6}auth:\s*(.+)$/m);
      if (auth) data.auth = auth[1]!.trim().replace(/^["']|["']$/g, '');
    }

    // MapReduce block
    if (block.match(/type:\s*map_reduce/) || block.match(/\bmap_reduce\s*:/m)) {
      const mrTask = block.match(/\btask:\s*(.+)/);
      if (mrTask) data.map_reduce_task = mrTask[1]!.trim().replace(/^["']|["']$/g, '');
      const mrMapper = block.match(/mapper:\s*(\S+)/);
      if (mrMapper) data.map_reduce_mapper = mrMapper[1]!.replace(/["']/g, '');
      const mrReducer = block.match(/reducer:\s*(\S+)/);
      if (mrReducer) data.map_reduce_reducer = mrReducer[1]!.replace(/["']/g, '');
      const mrConc = block.match(/concurrency:\s*(\d+)/);
      if (mrConc) data.map_reduce_concurrency = parseInt(mrConc[1]!);
      const mrTimeout = block.match(/timeout:\s*(\d+(?:\.\d+)?)/);
      if (mrTimeout) data.map_reduce_timeout = parseFloat(mrTimeout[1]!);
      const mrSplits = block.match(/splits:\s*\[([^\]]+)\]/);
      if (mrSplits) data.map_reduce_splits = mrSplits[1]!.split(',').map(s => s.trim().replace(/["']/g, '')).filter(Boolean);
    }

    // ForEach block
    if (block.match(/type:\s*for_each/)) {
      // Steps — inline [a, b, c] or multi-line YAML list
      const feStepsInline = block.match(/\bsteps:\s*\[([^\]]+)\]/);
      if (feStepsInline) {
        data.for_each_steps = feStepsInline[1]!.split(',').map(s => s.trim().replace(/["']/g, '')).filter(Boolean);
      } else {
        // Multi-line: steps:\n        - foo\n        - bar
        const feStepsMulti = block.match(/\bsteps:\s*\n((?:\s+- .+\n?)*)/);
        if (feStepsMulti) {
          data.for_each_steps = feStepsMulti[1]!
            .split('\n')
            .map(l => l.trim().replace(/^- /, '').trim().replace(/["']/g, ''))
            .filter(Boolean);
        }
      }
      const feRange = block.match(/range:\s*["']?([^"'\n]+)["']?/);
      if (feRange) data.for_each_range = feRange[1]!.trim();
      const feVar = block.match(/variable:\s*(\S+)/);
      if (feVar) data.for_each_variable = feVar[1]!.replace(/["']/g, '');
      const feCf = block.match(/carry_forward:\s*(true|false)/i);
      if (feCf) data.for_each_carry_forward = feCf[1]!.toLowerCase() === 'true';
      const feFf = block.match(/fail_fast:\s*(true|false)/i);
      if (feFf) data.for_each_fail_fast = feFf[1]!.toLowerCase() === 'true';
      const feMax = block.match(/max_iterations:\s*(\d+)/);
      if (feMax) data.for_each_max_iterations = parseInt(feMax[1]!);
      // Items — inline or multi-line
      const feItemsInline = block.match(/\bitems:\s*\[([^\]]+)\]/);
      if (feItemsInline) {
        data.for_each_items = feItemsInline[1]!.split(',').map(s => s.trim().replace(/["']/g, '')).filter(Boolean);
      } else {
        const feItemsMulti = block.match(/\bitems:\s*\n((?:\s+- .+\n?)*)/);
        if (feItemsMulti) {
          data.for_each_items = feItemsMulti[1]!
            .split('\n')
            .map(l => l.trim().replace(/^- /, '').trim().replace(/["']/g, ''))
            .filter(Boolean);
        }
      }
    }

    // Tag step block — `tag_step:` with item_kref / tag / untag
    const tagStepMatch = block.match(/^\s{4}tag_step:\s*\n((?:\s{5,}.*\n?)*)/m);
    if (tagStepMatch) {
      const tb = tagStepMatch[1]!;
      const itemM = tb.match(/^\s+item_kref:\s*"?([^"\n]+)"?\s*$/m);
      const tagM = tb.match(/^\s+tag:\s*"?([^"\n]+)"?\s*$/m);
      const untagM = tb.match(/^\s+untag:\s*"?([^"\n]+)"?\s*$/m);
      if (itemM) data.tag_item_kref = itemM[1]!.trim();
      if (tagM) data.tag_value = tagM[1]!.trim();
      if (untagM) data.tag_untag = untagM[1]!.trim();
    }

    // Deprecate step block — `deprecate_step:` with item_kref / reason
    const deprecateStepMatch = block.match(/^\s{4}deprecate_step:\s*\n((?:\s{5,}.*\n?)*)/m);
    if (deprecateStepMatch) {
      const db = deprecateStepMatch[1]!;
      const itemM = db.match(/^\s+item_kref:\s*"?([^"\n]+)"?\s*$/m);
      const reasonM = db.match(/^\s+reason:\s*"?([^"\n]+)"?\s*$/m);
      if (itemM) data.deprecate_item_kref = itemM[1]!.trim();
      if (reasonM) data.deprecate_reason = reasonM[1]!.trim();
    }

    // Conditional block — canonical form: `conditional: { branches: [...] }`.
    // Maps the first branch's condition/goto/value into legacy flat fields
    // (condition / on_true / on_true_value); maps the default branch into
    // on_false / on_false_value. Anything beyond two branches is dropped on
    // the editor side — the editor's gate node has only true/false handles.
    if (block.match(/type:\s*conditional/)) {
      const condBlockMatch = block.match(/^\s{4}conditional:\s*\n((?:\s{5,}.*\n?)*)/m);
      if (condBlockMatch) {
        const cb = condBlockMatch[1]!;
        // Split on `- condition:` markers under branches:
        const branchSection = cb.match(/^\s+branches:\s*\n((?:\s+.*\n?)*)/m);
        if (branchSection) {
          const bs = branchSection[1]!;
          // Each branch starts with `- condition: ...` then has goto/value lines
          const branchChunks = bs.split(/(?=^\s*-\s*condition:)/m).filter(c => c.trim());
          let trueAssigned = false;
          let falseAssigned = false;
          for (const chunk of branchChunks) {
            const cMatch = chunk.match(/-\s*condition:\s*"?([^"\n]+?)"?\s*$/m);
            const gMatch = chunk.match(/^\s+goto:\s*"?([^"\n]+?)"?\s*$/m);
            const vMatch = chunk.match(/^\s+value:\s*"?([^"\n]+?)"?\s*$/m);
            if (!cMatch || !gMatch) continue;
            const cText = cMatch[1]!.trim();
            const gText = gMatch[1]!.trim();
            const vText = vMatch ? vMatch[1]!.trim() : '';
            if (cText === 'default') {
              if (!falseAssigned) {
                data.on_false = gText;
                if (vText) data.on_false_value = vText;
                falseAssigned = true;
              }
            } else if (!trueAssigned) {
              data.condition = cText;
              data.on_true = gText;
              if (vText) data.on_true_value = vText;
              trueAssigned = true;
            }
          }
        }
      }
    }

    // Resolve block — capture all lines indented deeper than `resolve:` (5+ spaces)
    const resolveMatch = block.match(/^\s{4}resolve:\s*\n((?:\s{5,}.*\n?)*)/m);
    if (resolveMatch) {
      const rb = resolveMatch[1]!;
      const kindM = rb.match(/^\s+kind:\s*"?([^"\n]+)"?\s*$/m);
      const tagM = rb.match(/^\s+tag:\s*"?([^"\n]+)"?\s*$/m);
      const namePatM = rb.match(/^\s+name_pattern:\s*"?([^"\n]+)"?\s*$/m);
      const spaceM = rb.match(/^\s+space:\s*"?([^"\n]+)"?\s*$/m);
      const modeM = rb.match(/^\s+mode:\s*"?([^"\n]+)"?\s*$/m);
      const fieldsM = rb.match(/^\s+fields:\s*\[([^\]]*)\]/m);
      const failM = rb.match(/^\s+fail_if_missing:\s*(true|false)/m);

      if (kindM) data.resolve_kind = kindM[1]!.trim();
      if (tagM) data.resolve_tag = tagM[1]!.trim();
      if (namePatM) data.resolve_name_pattern = namePatM[1]!.trim();
      if (spaceM) data.resolve_space = spaceM[1]!.trim();
      if (modeM) data.resolve_mode = modeM[1]!.trim();
      if (fieldsM) {
        data.resolve_fields = fieldsM[1]!.split(',').map(s => s.trim().replace(/"/g, '')).filter(Boolean);
      }
      if (failM) data.resolve_fail_if_missing = failM[1] === 'true';
    }

    if (Object.keys(data).length > 0) result.set(stepId, data);
  }

  return result;
}

function parseInlineArray(value: string): string[] {
  return value
    .replace(/[\[\]]/g, '')
    .split(',')
    .map((s) => s.trim().replace(/^["']|["']$/g, ''))
    .filter(Boolean);
}

function dedupChannels(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const v of values) {
    const key = v.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(v);
  }
  return out;
}

function finalizeTask(partial: Partial<TaskDefinition>, paramCount: number): TaskDefinition {
  return {
    id: partial.id!,
    name: partial.name || partial.id!,
    description: partial.description || '',
    type: partial.type || 'agent',
    agent_hints: partial.agent_hints || [],
    skills: partial.skills || [],
    depends_on: partial.depends_on || [],
    params: paramCount > 0 ? ({ _count: String(paramCount) } as Record<string, string>) : undefined,
    condition: partial.condition,
    on_true: partial.on_true,
    on_false: partial.on_false,
    on_true_value: partial.on_true_value,
    on_false_value: partial.on_false_value,
    channel: partial.channel,
    channels: partial.channels,
    parallel_steps: partial.parallel_steps,
    for_each_steps: partial.for_each_steps,
    for_each_range: partial.for_each_range,
    for_each_items: partial.for_each_items,
    for_each_variable: partial.for_each_variable,
    for_each_carry_forward: partial.for_each_carry_forward,
    for_each_fail_fast: partial.for_each_fail_fast,
    for_each_max_iterations: partial.for_each_max_iterations,
    disabled: partial.disabled,
    tag_item_kref: partial.tag_item_kref,
    tag_value: partial.tag_value,
    tag_untag: partial.tag_untag,
    deprecate_item_kref: partial.deprecate_item_kref,
    deprecate_reason: partial.deprecate_reason,
  };
}

// ---------------------------------------------------------------------------
// Tasks → React Flow nodes & edges
// ---------------------------------------------------------------------------

export const GATE_EDGE_STYLES = {
  true: { stroke: 'var(--construct-status-success)', strokeWidth: 2 },
  false: { stroke: 'var(--construct-status-danger)', strokeWidth: 2 },
  default: { stroke: 'var(--construct-status-warning)', strokeWidth: 2 },
} as const;

export function tasksToFlow(tasks: TaskDefinition[]): { nodes: Node<TaskNodeData>[]; edges: Edge[] } {
  const isGate = (t: TaskDefinition) => t.type === 'conditional';

  const nodes: Node<TaskNodeData>[] = tasks.map((task, i) => ({
    id: task.id,
    type: isGate(task) ? 'gateNode' : 'taskNode',
    position: { x: 0, y: i * 150 },
    width: isGate(task) ? 220 : 280,
    data: {
      label: task.name || task.id,
      taskId: task.id,
      name: task.name || task.id,
      description: task.description,
      type: task.type,
      agentHints: task.agent_hints,
      skills: task.skills,
      assign: task.assign || '',
      template: task.template || '',
      paramCount: task.params ? Object.keys(task.params).length : 0,
      dependencyCount: task.depends_on.length,
      condition: task.condition || '',
      onTrueValue: task.on_true_value || '',
      onFalseValue: task.on_false_value || '',
      channel: task.channel || '',
      channels: task.channels || [],
      agentType: task.agent_type || '',
      role: task.role || '',
      prompt: task.prompt || '',
      timeout: task.timeout || 300,
      parallelJoin: task.parallel_join || 'all',
      gotoTarget: task.goto_target || '',
      gotoMaxIterations: task.goto_max_iterations || 3,
      groupChatTopic: task.group_chat_topic || '',
      groupChatParticipants: task.group_chat_participants || [],
      groupChatMaxRounds: task.group_chat_max_rounds || 8,
      supervisorTask: task.supervisor_task || '',
      supervisorMaxIterations: task.supervisor_max_iterations || 5,
      shellCommand: task.shell_command || '',
      outputFormat: task.output_format || 'markdown',
      entityName: task.entity_name || '',
      entityKind: task.entity_kind || '',
      entityTag: task.entity_tag || '',
      entitySpace: task.entity_space || '',
      entityMetadata: task.entity_metadata || {},
      handoffFrom: task.handoff_from || '',
      handoffTo: task.handoff_to || '',
      handoffReason: task.handoff_reason || '',
      retry: task.retry || 0,
      retryDelay: task.retry_delay || 5,
      model: task.model || '',
      shellTimeout: task.shell_timeout || 60,
      shellAllowFailure: task.shell_allow_failure || false,
      gotoCondition: task.goto_condition || '',
      parallelMaxConcurrency: task.parallel_max_concurrency || 5,
      humanInputMessage: task.human_input_message || '',
      humanInputTimeout: task.human_input_timeout || 3600,
      humanApprovalMessage: task.human_approval_message || '',
      humanApprovalTimeout: task.human_approval_timeout || 3600,
      humanApprovalChannel: task.human_approval_channel || 'dashboard',
      humanApprovalChannelId: task.human_approval_channel_id || '',
      outputTemplate: task.output_template || '',
      a2aUrl: task.a2a_url || '',
      a2aSkillId: task.a2a_skill_id || '',
      a2aMessage: task.a2a_message || '',
      a2aTimeout: task.a2a_timeout || 300,
      mapReduceTask: task.map_reduce_task || '',
      mapReduceSplits: task.map_reduce_splits || [],
      mapReduceMapper: task.map_reduce_mapper || 'claude',
      mapReduceReducer: task.map_reduce_reducer || 'claude',
      mapReduceConcurrency: task.map_reduce_concurrency || 3,
      mapReduceTimeout: task.map_reduce_timeout || 300,
      supervisorType: task.supervisor_type || 'claude',
      supervisorTimeout: task.supervisor_timeout || 300,
      groupChatModerator: task.group_chat_moderator || 'claude',
      groupChatStrategy: task.group_chat_strategy || 'moderator_selected',
      groupChatTimeout: task.group_chat_timeout || 120,
      handoffTask: task.handoff_task || '',
      handoffTimeout: task.handoff_timeout || 300,
      resolveKind: task.resolve_kind ?? '',
      resolveTag: task.resolve_tag ?? 'published',
      resolveNamePattern: task.resolve_name_pattern ?? '',
      resolveSpace: task.resolve_space ?? '',
      resolveMode: task.resolve_mode ?? 'latest',
      resolveFields: task.resolve_fields ?? [],
      resolveFailIfMissing: task.resolve_fail_if_missing ?? true,
      forEachSteps: task.for_each_steps || [],
      forEachRange: task.for_each_range || '',
      forEachItems: task.for_each_items || [],
      forEachVariable: task.for_each_variable || 'item',
      forEachCarryForward: task.for_each_carry_forward ?? true,
      forEachFailFast: task.for_each_fail_fast ?? true,
      forEachMaxIterations: task.for_each_max_iterations || 20,
      notifyMessage: task.notify_message || '',
      notifyTitle: task.notify_title || '',
      pythonScript: task.python_script || '',
      pythonCode: task.python_code || '',
      pythonArgs: task.python_args || '',
      pythonTimeout: task.python_timeout || 60,
      pythonAllowFailure: task.python_allow_failure || false,
      emailTo: task.email_to || '',
      emailSubject: task.email_subject || '',
      emailBody: task.email_body || '',
      emailBodyHtml: task.email_body_html || '',
      emailFrom: task.email_from || '',
      emailCc: task.email_cc || '',
      emailBcc: task.email_bcc || '',
      emailReplyTo: task.email_reply_to || '',
      emailTrackClicks: task.email_track_clicks || false,
      emailTrackKref: task.email_track_kref || '',
      emailTrackBaseUrl: task.email_track_base_url || '',
      emailSmtpHost: task.email_smtp_host || '',
      emailDryRun: task.email_dry_run || false,
      emailTimeout: task.email_timeout || 30,
      imagePrompt: task.image_prompt || '',
      imageCount: task.image_count ?? 1,
      imageCanvas: task.image_canvas !== false,
      imageRegisterArtifact: task.image_register_artifact !== false,
      imageSpace: task.image_space || '',
      imageItemName: task.image_item_name || '',
      imageOutputPath: task.image_output_path || '',
      imageOutputPattern: task.image_output_pattern || '',
      imageSandbox: task.image_sandbox || '',
      imageCwd: task.image_cwd || '',
      imageTimeout: task.image_timeout || 1200,
      tagItemKref: task.tag_item_kref || '',
      tagValue: task.tag_value || '',
      tagUntag: task.tag_untag || '',
      deprecateItemKref: task.deprecate_item_kref || '',
      deprecateReason: task.deprecate_reason || '',
      auth: task.auth || '',
      disabled: task.disabled ?? false,
    },
  }));

  const edges: Edge[] = [];
  const nodeIds = new Set(tasks.map((t) => t.id));

  // Track edges already added so we never emit duplicates. Keyed on
  // `${source}->${target}` so a single edge is never drawn twice even
  // when multiple inference passes (depends_on, parallel.steps,
  // ${ref.output} interpolation) all want to add it.
  const seenEdges = new Set<string>();
  const pushEdge = (edge: Edge): void => {
    const key = `${edge.source}->${edge.target}`;
    if (seenEdges.has(key)) return;
    seenEdges.add(key);
    edges.push(edge);
  };

  // Build a map of parallel step → children for edge rewriting
  const parallelChildrenMap = new Map<string, string[]>();
  for (const task of tasks) {
    if (task.parallel_steps && task.parallel_steps.length > 0) {
      const validChildren = task.parallel_steps.filter((c) => nodeIds.has(c));
      if (validChildren.length > 0) parallelChildrenMap.set(task.id, validChildren);
    }
  }

  // Build a map of for_each step → children for edge rewriting
  const forEachChildrenMap = new Map<string, string[]>();
  for (const task of tasks) {
    if (task.for_each_steps && task.for_each_steps.length > 0) {
      const validChildren = task.for_each_steps.filter((c) => nodeIds.has(c));
      if (validChildren.length > 0) forEachChildrenMap.set(task.id, validChildren);
    }
  }

  // Normal dependency edges (with parallel/for_each fan-out rewriting)
  for (const task of tasks) {
    for (const dep of task.depends_on) {
      // If dep is a parallel step, replace with edges from each parallel child
      const children = parallelChildrenMap.get(dep);
      if (children) {
        for (const child of children) {
          pushEdge({
            id: `${child}->${task.id}`,
            source: child,
            target: task.id,
            type: 'default',
            animated: true,
            selectable: true,
            interactionWidth: 20,
            style: GATE_EDGE_STYLES.default,
          });
        }
      // If dep is a for_each step, edge from last child (the loop output)
      } else if (forEachChildrenMap.has(dep)) {
        const feChildren = forEachChildrenMap.get(dep)!;
        const lastChild = feChildren[feChildren.length - 1]!;
        pushEdge({
          id: `${lastChild}->${task.id}`,
          source: lastChild,
          target: task.id,
          type: 'default',
          animated: true,
          selectable: true,
          interactionWidth: 20,
          style: GATE_EDGE_STYLES.default,
        });
      } else if (nodeIds.has(dep)) {
        pushEdge({
          id: `${dep}->${task.id}`,
          source: dep,
          target: task.id,
          type: 'default',
          animated: true,
          selectable: true,
          interactionWidth: 20,
          style: GATE_EDGE_STYLES.default,
        });
      }
    }
  }

  // Add edges from parallel parent to its children (synthetic — visual only)
  for (const [parentId, children] of parallelChildrenMap) {
    for (const child of children) {
      pushEdge({
        id: `par:${parentId}->${child}`,
        source: parentId,
        target: child,
        type: 'default',
        animated: true,
        selectable: true,
        interactionWidth: 20,
        style: GATE_EDGE_STYLES.default,
        data: { synthetic: true },
      });
    }
  }

  // Add edges from for_each parent to first sub-step, then chain sub-steps sequentially.
  // These are SYNTHETIC edges for visualization only — marked with data.synthetic
  // so flowToTasks can exclude them from depends_on reconstruction.
  for (const [parentId, children] of forEachChildrenMap) {
    for (let ci = 0; ci < children.length; ci++) {
      const child = children[ci]!;
      if (ci === 0) {
        // Parent → first child
        pushEdge({
          id: `fe:${parentId}->${child}`,
          source: parentId,
          target: child,
          type: 'default',
          animated: true,
          selectable: true,
          interactionWidth: 20,
          style: { stroke: 'var(--construct-signal-live)', strokeWidth: 2 },
          data: { synthetic: true },
        });
      } else {
        // Chain: previous child → this child (unless already has depends_on edges)
        const prev = children[ci - 1]!;
        pushEdge({
          id: `fe:${prev}->${child}`,
          source: prev,
          target: child,
          type: 'default',
          animated: true,
          selectable: true,
          interactionWidth: 20,
          style: { stroke: 'var(--construct-signal-live)', strokeWidth: 2 },
          data: { synthetic: true },
        });
      }
    }
  }

  // Gate branch edges (on_true / on_false)
  for (const task of tasks) {
    if (!isGate(task)) continue;
    if (task.on_true && nodeIds.has(task.on_true)) {
      pushEdge({
        id: `${task.id}->true->${task.on_true}`,
        source: task.id,
        sourceHandle: 'true',
        target: task.on_true,
        type: 'default',
        animated: true,
        selectable: true,
        interactionWidth: 20,
        style: GATE_EDGE_STYLES.true,
        label: 'true',
        labelStyle: { fill: 'var(--construct-status-success)', fontSize: 10, fontWeight: 600 },
      });
    }
    if (task.on_false && nodeIds.has(task.on_false)) {
      pushEdge({
        id: `${task.id}->false->${task.on_false}`,
        source: task.id,
        sourceHandle: 'false',
        target: task.on_false,
        type: 'default',
        animated: true,
        selectable: true,
        interactionWidth: 20,
        style: GATE_EDGE_STYLES.false,
        label: 'false',
        labelStyle: { fill: 'var(--construct-status-danger)', fontSize: 10, fontWeight: 600 },
      });
    }
  }

  // Inferred dependency edges from `${step_id.<field>}` interpolations in
  // text fields. Architect-generated YAML (and many hand-written workflows)
  // express deps via prompt/template references rather than explicit
  // depends_on. We mirror the depends_on direction (source = referenced
  // step, target = referencing step) so the Set-based dedup naturally
  // suppresses doubles when both depends_on and interpolation point at
  // the same source. ${input.X} / ${trigger.X} / ${env.X} are skipped —
  // those are workflow-scope references, not step references.
  for (const task of tasks) {
    const refs = collectStepRefs(task, nodeIds);
    for (const ref of refs) {
      if (ref === task.id) continue; // ignore self-references
      pushEdge({
        id: `ref:${ref}->${task.id}`,
        source: ref,
        target: task.id,
        type: 'default',
        animated: true,
        selectable: true,
        interactionWidth: 20,
        style: GATE_EDGE_STYLES.default,
        data: { inferred: 'interpolation' },
      });
    }
  }

  return { nodes, edges };
}

/** Text fields on a TaskDefinition that may contain `${step_id.<field>}`
 *  interpolations referencing other steps. Limited to the obvious
 *  text-bearing fields LLMs and humans write — we don't traverse params or
 *  arbitrary nested dicts (those rarely carry inter-step refs and would
 *  produce false positives from JSON-ish payloads). */
const INTERPOLATION_TEXT_FIELDS: ReadonlyArray<keyof TaskDefinition> = [
  'prompt',
  'shell_command',
  'python_code',
  'python_args',
  'python_script',
  'email_body',
  'email_body_html',
  'email_subject',
  'email_to',
  'email_cc',
  'email_bcc',
  'image_prompt',
  'output_template',
  'condition',
  'goto_condition',
  'human_input_message',
  'human_approval_message',
  'notify_message',
  'notify_title',
  'group_chat_topic',
  'supervisor_task',
  'a2a_message',
  'map_reduce_task',
  'handoff_reason',
];

/** IDs that look like step refs but are actually workflow-scope sources. */
const NON_STEP_REF_IDS = new Set(['input', 'trigger', 'env', 'inputs', 'outputs', 'context']);

/** Match `${step_id}` and `${step_id.field}` (and `${step_id.field.subfield}`).
 *  step_id matches the YAML id rule: starts with a letter or underscore,
 *  then letters/digits/underscores/hyphens. We don't try to handle nested
 *  expressions or pipes — LLMs use simple references. */
const STEP_REF_REGEX = /\$\{([a-zA-Z_][a-zA-Z0-9_-]*)(?:\.[a-zA-Z_][a-zA-Z0-9_.-]*)?\}/g;

function collectStepRefs(task: TaskDefinition, nodeIds: Set<string>): Set<string> {
  const refs = new Set<string>();
  for (const field of INTERPOLATION_TEXT_FIELDS) {
    const value = task[field];
    if (typeof value !== 'string' || !value) continue;
    STEP_REF_REGEX.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = STEP_REF_REGEX.exec(value)) !== null) {
      const id = m[1]!;
      if (NON_STEP_REF_IDS.has(id)) continue;
      if (!nodeIds.has(id)) continue;
      refs.add(id);
    }
  }
  return refs;
}

/** Legacy adapter for the read-only WorkflowGraph viewer */
export function stepsToFlow(steps: TaskDefinition[]): { nodes: Node<StepNodeData>[]; edges: Edge[] } {
  const nodes: Node<StepNodeData>[] = steps.map((step, i) => ({
    id: step.id,
    type: 'stepNode',
    position: { x: 0, y: i * 150 },
    width: 280,
    data: {
      label: step.id,
      stepId: step.id,
      type: step.type,
      agent: step.agent_hints?.[0] || '',
      paramCount: step.params ? Object.keys(step.params).length : 0,
      dependencyCount: step.depends_on.length,
    },
  }));

  const edges: Edge[] = [];
  const nodeIds = new Set(steps.map((s) => s.id));

  for (const step of steps) {
    for (const dep of step.depends_on) {
      if (nodeIds.has(dep)) {
        edges.push({
          id: `${dep}->${step.id}`,
          source: dep,
          target: step.id,
          type: 'default',
          animated: true,
          style: { stroke: 'var(--construct-status-warning)', strokeWidth: 2 },
          label: 'depends on',
          labelStyle: { fill: 'var(--construct-status-warning)', fontSize: 10 },
        });
      }
    }
  }

  return { nodes, edges };
}

// ---------------------------------------------------------------------------
// React Flow graph → YAML (serialize)
// ---------------------------------------------------------------------------

export function flowToTasks(nodes: Node<TaskNodeData>[], edges: Edge[]): TaskDefinition[] {
  // Regular dependency edges (no sourceHandle or sourceHandle is not true/false)
  const depsMap = new Map<string, string[]>();
  // Gate branch edges
  const trueBranch = new Map<string, string>();  // gateId → target
  const falseBranch = new Map<string, string>(); // gateId → target
  // Parallel children: parallel_node_id → [child_task_ids in edge order]
  const parallelChildren = new Map<string, string[]>();

  // Identify parallel-parent nodes so we can treat their outgoing edges as
  // child-membership (→ parallel.steps) rather than dependency edges.
  const parallelNodeIds = new Set(
    nodes.filter((n) => n.data.type === 'parallel').map((n) => n.id),
  );
  // Map node.id → task.id (task ids are what the YAML `parallel.steps` list
  // references; React Flow node ids may differ when tasks are renamed).
  const nodeIdToTaskId = new Map(nodes.map((n) => [n.id, n.data.taskId]));

  for (const edge of edges) {
    // Edges originating from a parallel node represent child membership and
    // must be captured regardless of the `synthetic` flag — synthetic edges
    // are created by flowFromTasks when loading a YAML that already has
    // `parallel.steps`, and we still need to round-trip that list back out.
    if (parallelNodeIds.has(edge.source)) {
      const childTaskId = nodeIdToTaskId.get(edge.target);
      if (childTaskId) {
        const children = parallelChildren.get(edge.source) || [];
        if (!children.includes(childTaskId)) children.push(childTaskId);
        parallelChildren.set(edge.source, children);
      }
      continue;
    }
    // Skip other synthetic edges (for_each chain) — these are visual only
    if ((edge.data as Record<string, unknown>)?.synthetic) continue;
    if (edge.sourceHandle === 'true') {
      trueBranch.set(edge.source, edge.target);
    } else if (edge.sourceHandle === 'false') {
      falseBranch.set(edge.source, edge.target);
    } else {
      const deps = depsMap.get(edge.target) || [];
      deps.push(edge.source);
      depsMap.set(edge.target, deps);
    }
  }

  return nodes.map((node) => {
    const d = node.data;
    const st = d.type || 'agent';
    const base: TaskDefinition = {
      id: d.taskId,
      name: d.name,
      description: d.description,
      type: st,
      agent_hints: d.agentHints,
      skills: d.skills,
      depends_on: depsMap.get(node.id) || [],
      condition: st === 'conditional' ? d.condition : undefined,
      on_true: trueBranch.get(node.id),
      on_false: falseBranch.get(node.id),
      on_true_value: st === 'conditional' && d.onTrueValue ? d.onTrueValue : undefined,
      on_false_value: st === 'conditional' && d.onFalseValue ? d.onFalseValue : undefined,
      channel: st === 'human_input' && d.channel
        ? d.channel as TaskDefinition['channel']
        : undefined,
      channels: st === 'notify' && d.channels.length > 0
        ? dedupChannels(d.channels)
        : undefined,
      notify_message: st === 'notify' && d.notifyMessage ? d.notifyMessage : undefined,
      notify_title: st === 'notify' && d.notifyTitle ? d.notifyTitle : undefined,
      retry: d.retry > 0 ? d.retry : undefined,
      retry_delay: d.retryDelay !== 5 ? d.retryDelay : undefined,
      disabled: d.disabled === true ? true : undefined,
      // Auth profile binding only emitted on the step types that consume it.
      auth: ['agent', 'shell', 'python', 'email', 'a2a'].includes(st) && d.auth
        ? d.auth
        : undefined,
    };
    // Pass through executor-specific fields
    if (st === 'agent') {
      if (d.agentType) base.agent_type = d.agentType as 'claude' | 'codex';
      if (d.role) base.role = d.role;
      if (d.prompt) base.prompt = d.prompt;
      if (d.timeout && d.timeout !== 300) base.timeout = d.timeout;
      if (d.assign) base.assign = d.assign;
      if (d.template) base.template = d.template;
      if (d.model) base.model = d.model;
    }
    if (st === 'parallel') {
      base.parallel_join = (d.parallelJoin || 'all') as TaskDefinition['parallel_join'];
      base.parallel_max_concurrency = d.parallelMaxConcurrency || 5;
      // Children are derived from canvas edges (synthetic edges from a loaded
      // YAML are included in `parallelChildren`, so round-trip is preserved).
      const childrenFromEdges = parallelChildren.get(node.id);
      if (childrenFromEdges && childrenFromEdges.length > 0) {
        base.parallel_steps = childrenFromEdges;
      }
    }
    if (st === 'goto') {
      if (d.gotoTarget) base.goto_target = d.gotoTarget;
      if (d.gotoMaxIterations) base.goto_max_iterations = d.gotoMaxIterations;
      if (d.gotoCondition) base.goto_condition = d.gotoCondition;
    }
    if (st === 'group_chat') {
      if (d.groupChatTopic) base.group_chat_topic = d.groupChatTopic;
      if (d.groupChatParticipants.length > 0) base.group_chat_participants = d.groupChatParticipants;
      if (d.groupChatMaxRounds) base.group_chat_max_rounds = d.groupChatMaxRounds;
      if (d.groupChatModerator !== 'claude') base.group_chat_moderator = d.groupChatModerator;
      if (d.groupChatStrategy !== 'moderator_selected') base.group_chat_strategy = d.groupChatStrategy;
      if (d.groupChatTimeout !== 120) base.group_chat_timeout = d.groupChatTimeout;
    }
    if (st === 'supervisor') {
      if (d.supervisorTask) base.supervisor_task = d.supervisorTask;
      if (d.supervisorMaxIterations) base.supervisor_max_iterations = d.supervisorMaxIterations;
      if (d.supervisorType !== 'claude') base.supervisor_type = d.supervisorType;
      if (d.supervisorTimeout !== 300) base.supervisor_timeout = d.supervisorTimeout;
    }
    if (st === 'shell') {
      if (d.shellCommand) base.shell_command = d.shellCommand;
      if (d.shellTimeout && d.shellTimeout !== 60) base.shell_timeout = d.shellTimeout;
      if (d.shellAllowFailure) base.shell_allow_failure = true;
    }
    if (st === 'python') {
      if (d.pythonScript) base.python_script = d.pythonScript;
      if (d.pythonCode) base.python_code = d.pythonCode;
      if (d.pythonArgs) base.python_args = d.pythonArgs;
      if (d.pythonTimeout && d.pythonTimeout !== 60) base.python_timeout = d.pythonTimeout;
      if (d.pythonAllowFailure) base.python_allow_failure = true;
    }
    if (st === 'email') {
      if (d.emailTo) base.email_to = d.emailTo;
      if (d.emailSubject) base.email_subject = d.emailSubject;
      if (d.emailBody) base.email_body = d.emailBody;
      if (d.emailBodyHtml) base.email_body_html = d.emailBodyHtml;
      if (d.emailFrom) base.email_from = d.emailFrom;
      if (d.emailCc) base.email_cc = d.emailCc;
      if (d.emailBcc) base.email_bcc = d.emailBcc;
      if (d.emailReplyTo) base.email_reply_to = d.emailReplyTo;
      if (d.emailTrackClicks) base.email_track_clicks = true;
      if (d.emailTrackKref) base.email_track_kref = d.emailTrackKref;
      if (d.emailTrackBaseUrl) base.email_track_base_url = d.emailTrackBaseUrl;
      if (d.emailSmtpHost) base.email_smtp_host = d.emailSmtpHost;
      if (d.emailDryRun) base.email_dry_run = true;
      if (d.emailTimeout && d.emailTimeout !== 30) base.email_timeout = d.emailTimeout;
    }
    if (st === 'image') {
      if (d.imagePrompt) base.image_prompt = d.imagePrompt;
      if (d.imageCount && d.imageCount !== 1) base.image_count = d.imageCount;
      if (d.imageCanvas === false) base.image_canvas = false;
      if (d.imageRegisterArtifact === false) base.image_register_artifact = false;
      if (d.imageSpace) base.image_space = d.imageSpace;
      if (d.imageItemName) base.image_item_name = d.imageItemName;
      if (d.imageOutputPath) base.image_output_path = d.imageOutputPath;
      if (d.imageOutputPattern) base.image_output_pattern = d.imageOutputPattern;
      if (d.imageSandbox) base.image_sandbox = d.imageSandbox;
      if (d.imageCwd) base.image_cwd = d.imageCwd;
      if (d.imageTimeout && d.imageTimeout !== 1200) base.image_timeout = d.imageTimeout;
    }
    if (st === 'output') {
      if (d.outputFormat) base.output_format = d.outputFormat;
      if (d.outputTemplate) base.output_template = d.outputTemplate;
      if (d.entityName) base.entity_name = d.entityName;
      if (d.entityKind) base.entity_kind = d.entityKind;
      if (d.entityTag) base.entity_tag = d.entityTag;
      if (d.entitySpace) base.entity_space = d.entitySpace;
      if (Object.keys(d.entityMetadata).length > 0) base.entity_metadata = d.entityMetadata;
    }
    if (st === 'handoff') {
      if (d.handoffFrom) base.handoff_from = d.handoffFrom;
      if (d.handoffTo) base.handoff_to = d.handoffTo as 'claude' | 'codex';
      if (d.handoffReason) base.handoff_reason = d.handoffReason;
      if (d.handoffTask) base.handoff_task = d.handoffTask;
      if (d.handoffTimeout !== 300) base.handoff_timeout = d.handoffTimeout;
    }
    if (st === 'human_input') {
      if (d.humanInputMessage) base.human_input_message = d.humanInputMessage;
      if (d.humanInputTimeout && d.humanInputTimeout !== 3600) base.human_input_timeout = d.humanInputTimeout;
    }
    if (st === 'human_approval') {
      if (d.humanApprovalMessage) base.human_approval_message = d.humanApprovalMessage;
      if (d.humanApprovalTimeout && d.humanApprovalTimeout !== 3600) base.human_approval_timeout = d.humanApprovalTimeout;
      if (d.humanApprovalChannel && d.humanApprovalChannel !== 'dashboard') base.human_approval_channel = d.humanApprovalChannel;
      if (d.humanApprovalChannelId) base.human_approval_channel_id = d.humanApprovalChannelId;
    }
    if (st === 'a2a') {
      if (d.a2aUrl) base.a2a_url = d.a2aUrl;
      if (d.a2aSkillId) base.a2a_skill_id = d.a2aSkillId;
      if (d.a2aMessage) base.a2a_message = d.a2aMessage;
      if (d.a2aTimeout && d.a2aTimeout !== 300) base.a2a_timeout = d.a2aTimeout;
    }
    if (st === 'map_reduce') {
      if (d.mapReduceTask) base.map_reduce_task = d.mapReduceTask;
      if (d.mapReduceSplits.length > 0) base.map_reduce_splits = d.mapReduceSplits;
      if (d.mapReduceMapper !== 'claude') base.map_reduce_mapper = d.mapReduceMapper;
      if (d.mapReduceReducer !== 'claude') base.map_reduce_reducer = d.mapReduceReducer;
      if (d.mapReduceConcurrency !== 3) base.map_reduce_concurrency = d.mapReduceConcurrency;
      if (d.mapReduceTimeout !== 300) base.map_reduce_timeout = d.mapReduceTimeout;
    }
    if (st === 'resolve') {
      if (d.resolveKind) base.resolve_kind = d.resolveKind;
      if (d.resolveTag) base.resolve_tag = d.resolveTag;
      if (d.resolveNamePattern) base.resolve_name_pattern = d.resolveNamePattern;
      if (d.resolveSpace) base.resolve_space = d.resolveSpace;
      if (d.resolveMode) base.resolve_mode = d.resolveMode;
      if (d.resolveFields?.length) base.resolve_fields = d.resolveFields;
      if (d.resolveFailIfMissing === false) base.resolve_fail_if_missing = false;
    }
    if (st === 'for_each') {
      if (d.forEachSteps.length > 0) base.for_each_steps = d.forEachSteps;
      if (d.forEachRange) base.for_each_range = d.forEachRange;
      if (d.forEachItems.length > 0) base.for_each_items = d.forEachItems;
      if (d.forEachVariable && d.forEachVariable !== 'item') base.for_each_variable = d.forEachVariable;
      if (!d.forEachCarryForward) base.for_each_carry_forward = false;
      if (!d.forEachFailFast) base.for_each_fail_fast = false;
      if (d.forEachMaxIterations && d.forEachMaxIterations !== 20) base.for_each_max_iterations = d.forEachMaxIterations;
    }
    if (st === 'tag') {
      if (d.tagItemKref) base.tag_item_kref = d.tagItemKref;
      if (d.tagValue) base.tag_value = d.tagValue;
      if (d.tagUntag) base.tag_untag = d.tagUntag;
    }
    if (st === 'deprecate') {
      if (d.deprecateItemKref) base.deprecate_item_kref = d.deprecateItemKref;
      if (d.deprecateReason) base.deprecate_reason = d.deprecateReason;
    }
    return base;
  });
}

export function tasksToYaml(tasks: TaskDefinition[], meta?: Partial<WorkflowMeta>): string {
  const lines: string[] = [];

  if (meta?.name) lines.push(`name: ${meta.name}`);
  if (meta?.version) lines.push(`version: "${meta.version}"`);
  if (meta?.description) lines.push(`description: ${yamlEscape(meta.description)}`);
  if (meta?.tags && meta.tags.length > 0) lines.push(`tags: [${meta.tags.join(', ')}]`);
  // Triggers
  if (meta?.triggers && meta.triggers.length > 0) {
    lines.push('');
    lines.push('triggers:');
    for (const t of meta.triggers) {
      if (t.inputMap.__cron) {
        lines.push(`  - cron: ${yamlEscape(t.inputMap.__cron)}`);
      } else {
        lines.push(`  - on_kind: ${yamlEscape(t.onKind)}`);
      }
      if (t.onTag && t.onTag !== 'ready') lines.push(`    on_tag: ${yamlEscape(t.onTag)}`);
      if (t.onNamePattern) lines.push(`    on_name_pattern: ${yamlEscape(t.onNamePattern)}`);
      const mapEntries = Object.entries(t.inputMap).filter(([k]) => k !== '__cron');
      if (mapEntries.length > 0) {
        lines.push('    input_map:');
        for (const [mk, mv] of mapEntries) {
          lines.push(`      ${mk}: ${yamlEscape(mv)}`);
        }
      }
    }
  }
  // Inputs
  if (meta?.inputs && meta.inputs.length > 0) {
    lines.push('');
    lines.push('inputs:');
    for (const inp of meta.inputs) {
      lines.push(`  - name: ${inp.name}`);
      if (inp.type !== 'string') lines.push(`    type: ${inp.type}`);
      if (!inp.required) lines.push(`    required: false`);
      if (inp.default) lines.push(`    default: ${yamlEscape(inp.default)}`);
      if (inp.description) lines.push(`    description: ${yamlEscape(inp.description)}`);
    }
  }
  // Outputs
  if (meta?.outputs && meta.outputs.length > 0) {
    lines.push('');
    lines.push('outputs:');
    for (const out of meta.outputs) {
      lines.push(`  - name: ${out.name}`);
      lines.push(`    source: ${yamlEscape(out.source)}`);
      if (out.description) lines.push(`    description: ${yamlEscape(out.description)}`);
    }
  }
  // Execution defaults (only emit non-defaults)
  if (meta?.defaultCwd) lines.push(`default_cwd: ${yamlEscape(meta.defaultCwd)}`);
  if (meta?.defaultTimeout && meta.defaultTimeout !== 300) lines.push(`default_timeout: ${meta.defaultTimeout}`);
  if (meta?.maxTotalTime && meta.maxTotalTime !== 3600) lines.push(`max_total_time: ${meta.maxTotalTime}`);
  if (meta?.checkpoint === false) lines.push(`checkpoint: false`);
  if (lines.length > 0) lines.push('');

  lines.push('steps:');

  for (const task of tasks) {
    lines.push(`  - id: ${task.id}`);
    if (task.name && task.name !== task.id) {
      lines.push(`    name: ${yamlEscape(task.name)}`);
    }
    // Canonical step type — `action:` is no longer emitted (legacy YAML
    // with `action:` is still parsed and migrated to `type` on load).
    const stepType = task.type || 'agent';
    lines.push(`    type: ${stepType}`);
    if (task.description) {
      lines.push(`    description: ${yamlEscape(task.description)}`);
    }
    if (task.retry && task.retry > 0) lines.push(`    retry: ${task.retry}`);
    if (task.retry_delay && task.retry_delay !== 5) lines.push(`    retry_delay: ${task.retry_delay}`);
    if (task.disabled === true) lines.push(`    disabled: true`);
    // Conditional steps emit canonical `conditional.branches` form.
    // Legacy flat `condition`/`on_true`/`on_false` keys are no longer
    // emitted — the backend bridges them on load for forward compat,
    // but new saves must round-trip canonical so the YAML lines up
    // with what the validator + executor consume directly.
    if (stepType === 'conditional') {
      const branches: string[] = [];
      if (task.on_true && task.condition) {
        branches.push(`      - condition: ${yamlEscape(task.condition)}`);
        branches.push(`        goto: ${task.on_true}`);
        if (task.on_true_value) {
          branches.push(`        value: ${yamlEscape(task.on_true_value)}`);
        }
      }
      if (task.on_false) {
        branches.push(`      - condition: "default"`);
        branches.push(`        goto: ${task.on_false}`);
        if (task.on_false_value) {
          branches.push(`        value: ${yamlEscape(task.on_false_value)}`);
        }
      }
      if (branches.length > 0) {
        lines.push(`    conditional:`);
        lines.push(`      branches:`);
        lines.push(...branches);
      }
    }
    if (stepType === 'human_input' && task.channel) {
      lines.push(`    channel: ${task.channel}`);
    }
    if (stepType === 'notify' && task.channels && task.channels.length > 0) {
      lines.push(`    notify:`);
      lines.push(`      channels: [${dedupChannels(task.channels).join(', ')}]`);
      const notifyMessage = task.notify_message || '';
      if (notifyMessage) {
        if (notifyMessage.includes('\n')) {
          lines.push(`      message: |`);
          for (const ml of notifyMessage.split('\n')) lines.push(`        ${ml}`);
        } else {
          lines.push(`      message: ${yamlEscape(notifyMessage)}`);
        }
      }
      const notifyTitle = task.notify_title || '';
      if (notifyTitle) lines.push(`      title: ${yamlEscape(notifyTitle)}`);
    }
    // Executor-specific nested blocks
    if (stepType === 'agent' && (task.agent_type || task.role || task.prompt || task.template || task.auth)) {
      lines.push(`    agent:`);
      if (task.agent_type) lines.push(`      agent_type: ${task.agent_type}`);
      if (task.role) lines.push(`      role: ${task.role}`);
      // Persona binding ONLY from task.template. task.assign emits separately
      // as a top-level `assign:` key so the two round-trip independently.
      if (task.template) lines.push(`      template: ${task.template}`);
      if (task.prompt) {
        if (task.prompt.includes('\n')) {
          lines.push(`      prompt: |`);
          for (const pl of task.prompt.split('\n')) {
            lines.push(`        ${pl}`);
          }
        } else {
          lines.push(`      prompt: ${yamlEscape(task.prompt)}`);
        }
      }
      if (task.timeout && task.timeout !== 300) lines.push(`      timeout: ${task.timeout}`);
      if (task.model) lines.push(`      model: ${task.model}`);
      if (task.auth) lines.push(`      auth: ${yamlEscape(task.auth)}`);
    }
    if (stepType === 'parallel') {
      lines.push(`    parallel:`);
      if (task.parallel_steps && task.parallel_steps.length > 0) {
        lines.push(`      steps: [${task.parallel_steps.join(', ')}]`);
      }
      lines.push(`      join: ${task.parallel_join || 'all'}`);
      lines.push(`      max_concurrency: ${task.parallel_max_concurrency || 5}`);
    }
    if (stepType === 'goto') {
      lines.push(`    goto:`);
      if (task.goto_target) lines.push(`      target: ${task.goto_target}`);
      if (task.goto_max_iterations) lines.push(`      max_iterations: ${task.goto_max_iterations}`);
      if (task.goto_condition) lines.push(`      condition: ${yamlEscape(task.goto_condition)}`);
    }
    if (stepType === 'group_chat') {
      lines.push(`    group_chat:`);
      if (task.group_chat_topic) lines.push(`      topic: ${yamlEscape(task.group_chat_topic)}`);
      if (task.group_chat_participants && task.group_chat_participants.length > 0) {
        lines.push(`      participants: [${task.group_chat_participants.join(', ')}]`);
      }
      if (task.group_chat_max_rounds) lines.push(`      max_rounds: ${task.group_chat_max_rounds}`);
      if (task.group_chat_moderator && task.group_chat_moderator !== 'claude') lines.push(`      moderator: ${task.group_chat_moderator}`);
      if (task.group_chat_strategy && task.group_chat_strategy !== 'moderator_selected') lines.push(`      strategy: ${task.group_chat_strategy}`);
      if (task.group_chat_timeout && task.group_chat_timeout !== 120) lines.push(`      timeout: ${task.group_chat_timeout}`);
    }
    if (stepType === 'supervisor') {
      lines.push(`    supervisor:`);
      if (task.supervisor_task) lines.push(`      task: ${yamlEscape(task.supervisor_task)}`);
      if (task.supervisor_max_iterations) lines.push(`      max_iterations: ${task.supervisor_max_iterations}`);
      if (task.supervisor_type && task.supervisor_type !== 'claude') lines.push(`      supervisor_type: ${task.supervisor_type}`);
      if (task.supervisor_timeout && task.supervisor_timeout !== 300) lines.push(`      timeout: ${task.supervisor_timeout}`);
    }
    if (stepType === 'shell') {
      lines.push(`    shell:`);
      if (task.shell_command) lines.push(`      command: ${yamlEscape(task.shell_command)}`);
      if (task.shell_timeout && task.shell_timeout !== 60) lines.push(`      timeout: ${task.shell_timeout}`);
      if (task.shell_allow_failure) lines.push(`      allow_failure: true`);
      if (task.auth) lines.push(`      auth: ${yamlEscape(task.auth)}`);
    }
    if (stepType === 'python') {
      lines.push(`    python:`);
      if (task.python_script) lines.push(`      script: ${yamlEscape(task.python_script)}`);
      if (task.python_code) {
        if (task.python_code.includes('\n')) {
          lines.push(`      code: |`);
          for (const cl of task.python_code.split('\n')) {
            lines.push(`        ${cl}`);
          }
        } else {
          lines.push(`      code: ${yamlEscape(task.python_code)}`);
        }
      }
      if (task.python_args) lines.push(`      args: ${task.python_args}`);
      if (task.python_timeout && task.python_timeout !== 60) lines.push(`      timeout: ${task.python_timeout}`);
      if (task.python_allow_failure) lines.push(`      allow_failure: true`);
      if (task.auth) lines.push(`      auth: ${yamlEscape(task.auth)}`);
    }
    if (stepType === 'email') {
      lines.push(`    email:`);
      if (task.email_to) lines.push(`      to: ${yamlEscape(task.email_to)}`);
      if (task.email_subject) lines.push(`      subject: ${yamlEscape(task.email_subject)}`);
      if (task.email_body) {
        if (task.email_body.includes('\n')) {
          lines.push(`      body: |`);
          for (const bl of task.email_body.split('\n')) {
            lines.push(`        ${bl}`);
          }
        } else {
          lines.push(`      body: ${yamlEscape(task.email_body)}`);
        }
      }
      if (task.email_body_html) {
        lines.push(`      body_html: |`);
        for (const hl of task.email_body_html.split('\n')) {
          lines.push(`        ${hl}`);
        }
      }
      if (task.email_from) lines.push(`      from_address: ${yamlEscape(task.email_from)}`);
      if (task.email_cc) {
        const ccs = task.email_cc.split(',').map(s => s.trim()).filter(Boolean);
        if (ccs.length > 0) lines.push(`      cc: [${ccs.map(yamlEscape).join(', ')}]`);
      }
      if (task.email_bcc) {
        const bccs = task.email_bcc.split(',').map(s => s.trim()).filter(Boolean);
        if (bccs.length > 0) lines.push(`      bcc: [${bccs.map(yamlEscape).join(', ')}]`);
      }
      if (task.email_reply_to) lines.push(`      reply_to: ${yamlEscape(task.email_reply_to)}`);
      if (task.email_track_clicks) lines.push(`      track_clicks: true`);
      if (task.email_track_kref) lines.push(`      track_kref: ${yamlEscape(task.email_track_kref)}`);
      if (task.email_track_base_url) lines.push(`      track_base_url: ${yamlEscape(task.email_track_base_url)}`);
      if (task.email_smtp_host) lines.push(`      smtp_host: ${yamlEscape(task.email_smtp_host)}`);
      if (task.email_dry_run) lines.push(`      dry_run: true`);
      if (task.email_timeout && task.email_timeout !== 30) lines.push(`      timeout: ${task.email_timeout}`);
      if (task.auth) lines.push(`      auth: ${yamlEscape(task.auth)}`);
    }
    if (stepType === 'image') {
      lines.push(`    image:`);
      if (task.image_prompt) {
        if (task.image_prompt.includes('\n')) {
          lines.push(`      prompt: |`);
          for (const pl of task.image_prompt.split('\n')) lines.push(`        ${pl}`);
        } else {
          lines.push(`      prompt: ${yamlEscape(task.image_prompt)}`);
        }
      }
      if (task.image_count && task.image_count !== 1) lines.push(`      count: ${task.image_count}`);
      if (task.image_canvas === false) lines.push(`      canvas: false`);
      if (task.image_register_artifact === false) lines.push(`      register_artifact: false`);
      if (task.image_space) lines.push(`      space: ${yamlEscape(task.image_space)}`);
      if (task.image_item_name) lines.push(`      item_name: ${yamlEscape(task.image_item_name)}`);
      if (task.image_output_path) lines.push(`      output_path: ${yamlEscape(task.image_output_path)}`);
      if (task.image_output_pattern) lines.push(`      output_pattern: ${yamlEscape(task.image_output_pattern)}`);
      if (task.image_sandbox) lines.push(`      sandbox: ${task.image_sandbox}`);
      if (task.image_cwd) lines.push(`      cwd: ${yamlEscape(task.image_cwd)}`);
      if (task.image_timeout && task.image_timeout !== 1200) lines.push(`      timeout: ${task.image_timeout}`);
    }
    if (stepType === 'output') {
      lines.push(`    output:`);
      if (task.output_format) lines.push(`      format: ${task.output_format}`);
      if (task.output_template) {
        if (task.output_template.includes('\n')) {
          lines.push(`      template: |`);
          for (const tplLine of task.output_template.split('\n')) {
            lines.push(`        ${tplLine}`);
          }
        } else {
          lines.push(`      template: ${yamlEscape(task.output_template)}`);
        }
      }
      if (task.entity_name) lines.push(`      entity_name: ${yamlEscape(task.entity_name)}`);
      if (task.entity_kind) lines.push(`      entity_kind: ${yamlEscape(task.entity_kind)}`);
      if (task.entity_tag) lines.push(`      entity_tag: ${yamlEscape(task.entity_tag)}`);
      if (task.entity_space) lines.push(`      entity_space: ${yamlEscape(task.entity_space)}`);
      if (task.entity_metadata && Object.keys(task.entity_metadata).length > 0) {
        lines.push(`      entity_metadata:`);
        for (const [mk, mv] of Object.entries(task.entity_metadata)) {
          lines.push(`        ${mk}: "${String(mv).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`);
        }
      }
    }
    if (stepType === 'handoff') {
      lines.push(`    handoff:`);
      if (task.handoff_from) lines.push(`      from_step: ${task.handoff_from}`);
      if (task.handoff_to) lines.push(`      to_agent_type: ${task.handoff_to}`);
      if (task.handoff_reason) lines.push(`      reason: ${yamlEscape(task.handoff_reason)}`);
      if (task.handoff_task) lines.push(`      task: ${yamlEscape(task.handoff_task)}`);
      if (task.handoff_timeout && task.handoff_timeout !== 300) lines.push(`      timeout: ${task.handoff_timeout}`);
    }
    if (stepType === 'human_input') {
      lines.push(`    human_input:`);
      if (task.human_input_message) {
        if (task.human_input_message.includes('\n')) {
          lines.push(`      message: |`);
          for (const msgLine of task.human_input_message.split('\n')) {
            lines.push(`        ${msgLine}`);
          }
        } else {
          lines.push(`      message: ${yamlEscape(task.human_input_message)}`);
        }
      }
      if (task.channel) lines.push(`      channel: ${task.channel}`);
      if (task.human_input_timeout && task.human_input_timeout !== 3600) lines.push(`      timeout: ${task.human_input_timeout}`);
    }
    if (stepType === 'human_approval') {
      lines.push(`    human_approval:`);
      if (task.human_approval_channel && task.human_approval_channel !== 'dashboard') lines.push(`      channel: ${task.human_approval_channel}`);
      if (task.human_approval_channel_id) lines.push(`      channel_id: "${task.human_approval_channel_id}"`);
      if (task.human_approval_message) {
        if (task.human_approval_message.includes('\n')) {
          lines.push(`      message: |`);
          for (const msgLine of task.human_approval_message.split('\n')) {
            lines.push(`        ${msgLine}`);
          }
        } else {
          lines.push(`      message: ${yamlEscape(task.human_approval_message)}`);
        }
      }
      if (task.human_approval_timeout && task.human_approval_timeout !== 3600) lines.push(`      timeout: ${task.human_approval_timeout}`);
    }
    if (stepType === 'a2a') {
      lines.push(`    a2a:`);
      if (task.a2a_url) lines.push(`      url: ${task.a2a_url}`);
      if (task.a2a_skill_id) lines.push(`      skill_id: ${task.a2a_skill_id}`);
      if (task.a2a_message) lines.push(`      message: ${yamlEscape(task.a2a_message)}`);
      if (task.a2a_timeout && task.a2a_timeout !== 300) lines.push(`      timeout: ${task.a2a_timeout}`);
      if (task.auth) lines.push(`      auth: ${yamlEscape(task.auth)}`);
    }
    if (stepType === 'map_reduce') {
      lines.push(`    map_reduce:`);
      if (task.map_reduce_task) lines.push(`      task: ${yamlEscape(task.map_reduce_task)}`);
      if (task.map_reduce_splits && task.map_reduce_splits.length > 0) {
        lines.push(`      splits: [${task.map_reduce_splits.map(s => yamlEscape(s)).join(', ')}]`);
      }
      if (task.map_reduce_mapper && task.map_reduce_mapper !== 'claude') lines.push(`      mapper: ${task.map_reduce_mapper}`);
      if (task.map_reduce_reducer && task.map_reduce_reducer !== 'claude') lines.push(`      reducer: ${task.map_reduce_reducer}`);
      if (task.map_reduce_concurrency && task.map_reduce_concurrency !== 3) lines.push(`      concurrency: ${task.map_reduce_concurrency}`);
      if (task.map_reduce_timeout && task.map_reduce_timeout !== 300) lines.push(`      timeout: ${task.map_reduce_timeout}`);
    }
    if (stepType === 'resolve') {
      lines.push(`    resolve:`);
      lines.push(`      kind: "${task.resolve_kind || ''}"`);
      lines.push(`      tag: "${task.resolve_tag || 'published'}"`);
      lines.push(`      name_pattern: "${task.resolve_name_pattern || ''}"`);
      lines.push(`      space: "${task.resolve_space || ''}"`);
      lines.push(`      mode: "${task.resolve_mode || 'latest'}"`);
      if (task.resolve_fields?.length) {
        lines.push(`      fields: [${task.resolve_fields.map(f => `"${f}"`).join(', ')}]`);
      } else {
        lines.push(`      fields: []`);
      }
      lines.push(`      fail_if_missing: ${task.resolve_fail_if_missing !== false ? 'true' : 'false'}`);
    }
    if (stepType === 'for_each' && task.for_each_steps && task.for_each_steps.length > 0) {
      lines.push(`    for_each:`);
      if (task.for_each_range) lines.push(`      range: "${task.for_each_range}"`);
      if (task.for_each_items && task.for_each_items.length > 0) {
        lines.push(`      items: [${task.for_each_items.map(s => `"${s}"`).join(', ')}]`);
      }
      if (task.for_each_variable && task.for_each_variable !== 'item') lines.push(`      variable: ${task.for_each_variable}`);
      lines.push(`      steps: [${task.for_each_steps.join(', ')}]`);
      if (task.for_each_carry_forward === false) lines.push(`      carry_forward: false`);
      if (task.for_each_fail_fast === false) lines.push(`      fail_fast: false`);
      if (task.for_each_max_iterations && task.for_each_max_iterations !== 20) lines.push(`      max_iterations: ${task.for_each_max_iterations}`);
    }
    if (stepType === 'tag') {
      lines.push(`    tag_step:`);
      if (task.tag_item_kref) lines.push(`      item_kref: ${yamlEscape(task.tag_item_kref)}`);
      if (task.tag_value) lines.push(`      tag: ${yamlEscape(task.tag_value)}`);
      if (task.tag_untag) lines.push(`      untag: ${yamlEscape(task.tag_untag)}`);
    }
    if (stepType === 'deprecate') {
      lines.push(`    deprecate_step:`);
      if (task.deprecate_item_kref) lines.push(`      item_kref: ${yamlEscape(task.deprecate_item_kref)}`);
      if (task.deprecate_reason) lines.push(`      reason: ${yamlEscape(task.deprecate_reason)}`);
    }
    if (task.agent_hints.length > 0) {
      lines.push(`    agent_hints: [${task.agent_hints.join(', ')}]`);
    }
    if (task.skills.length > 0) {
      lines.push(`    skills: [${task.skills.join(', ')}]`);
    }
    // AgentPicker pool-agent binding. Applies to agent steps too — emitted
    // alongside `agent.template:` so the two keys round-trip independently.
    if (task.assign) {
      lines.push(`    assign: ${task.assign}`);
    }
    if (task.depends_on.length > 0) {
      lines.push(`    depends_on: [${task.depends_on.join(', ')}]`);
    }
  }

  return lines.join('\n') + '\n';
}

function yamlEscape(value: string): string {
  if (/[:#\[\]{}&*!|>'"%@`]/.test(value) || value.includes('\n')) {
    return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
  }
  return value;
}
