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

import YAML from 'js-yaml';
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
// YAML → Tasks parser (uses js-yaml for structured parsing)
// ---------------------------------------------------------------------------
//
// Reads `steps:` from the parsed YAML doc and walks each step structurally,
// pulling step-level fields plus nested blocks (`agent.*`, `shell.*`,
// `python.*`, `output.*`, `resolve.*`, `parallel.*`, `for_each.*`, `goto.*`,
// `conditional.branches`, `notify.*`, `tag_step.*`, `deprecate_step.*`, …).
//
// Canonical `conditional.branches` is also flattened into the legacy
// `condition` / `on_true` / `on_false` / `on_true_value` / `on_false_value`
// fields so the editor's existing edge builder + side panel work without
// changes (PR #216, #217).

type YAMLValue = unknown;
type YAMLObj = Record<string, YAMLValue>;

const isObj = (v: YAMLValue): v is YAMLObj =>
  typeof v === 'object' && v !== null && !Array.isArray(v);

const asStr = (v: YAMLValue): string | undefined => {
  if (v === null || v === undefined) return undefined;
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return undefined;
};

const asNum = (v: YAMLValue): number | undefined => {
  if (typeof v === 'number') return v;
  if (typeof v === 'string' && v.trim() !== '') {
    const n = Number(v);
    return Number.isFinite(n) ? n : undefined;
  }
  return undefined;
};

const asBool = (v: YAMLValue): boolean | undefined => {
  if (typeof v === 'boolean') return v;
  if (typeof v === 'string') {
    const s = v.toLowerCase();
    if (s === 'true') return true;
    if (s === 'false') return false;
  }
  return undefined;
};

const asStrArr = (v: YAMLValue): string[] | undefined => {
  if (!Array.isArray(v)) return undefined;
  const out = v.map((x) => asStr(x)).filter((s): s is string => !!s && s.length > 0);
  return out;
};

export function parseWorkflowYaml(yamlText: string): TaskDefinition[] {
  const doc = YAML.load(yamlText);
  if (!isObj(doc)) return [];
  const stepsRaw = doc.steps;
  if (!Array.isArray(stepsRaw)) return [];

  const tasks: TaskDefinition[] = [];
  for (const raw of stepsRaw) {
    if (!isObj(raw)) continue;
    const task = parseStep(raw);
    if (task) tasks.push(task);
  }
  return tasks;
}

function parseStep(s: YAMLObj): TaskDefinition | null {
  const id = asStr(s.id);
  if (!id) return null;

  // type/action canonicalization — `type:` wins, `action:` is legacy.
  const rawType = asStr(s.type) ?? asStr(s.action) ?? asStr(s.task) ?? 'agent';
  const type = canonicalizeType(rawType);

  // depends_on accepts string | string[]
  let depends_on: string[] = [];
  const depRaw = s.depends_on ?? s.dependsOn ?? s.after;
  if (Array.isArray(depRaw)) depends_on = asStrArr(depRaw) ?? [];
  else { const single = asStr(depRaw); if (single) depends_on = [single]; }

  // agent_hints / skills / channels — accept array | scalar
  const hintsRaw = s.agent_hints ?? s.agentHints;
  let agent_hints: string[] = [];
  if (Array.isArray(hintsRaw)) agent_hints = asStrArr(hintsRaw) ?? [];
  else { const single = asStr(hintsRaw); if (single) agent_hints = [single]; }

  let skills: string[] = [];
  if (Array.isArray(s.skills)) skills = asStrArr(s.skills) ?? [];
  else { const single = asStr(s.skills); if (single) skills = [single]; }

  // channels — top-level (legacy) and nested under `notify:` (canonical)
  let channels: string[] | undefined;
  if (Array.isArray(s.channels)) channels = asStrArr(s.channels);
  else { const single = asStr(s.channels); if (single) channels = [single]; }

  // params — preserve count for the badge
  let paramCount = 0;
  const paramsRaw = s.params ?? s.parameters ?? s.config;
  if (isObj(paramsRaw)) paramCount = Object.keys(paramsRaw).length;

  const t: TaskDefinition = {
    id,
    name: asStr(s.name) ?? id,
    description: asStr(s.description) ?? asStr(s.desc) ?? '',
    type,
    agent_hints,
    skills,
    depends_on,
    params: paramCount > 0 ? ({ _count: String(paramCount) } as Record<string, string>) : undefined,
    assign: asStr(s.assign),
    disabled: asBool(s.disabled),
    retry: asNum(s.retry),
    retry_delay: asNum(s.retry_delay) ?? asNum(s.retryDelay),
    channel: asStr(s.channel) as TaskDefinition['channel'] | undefined,
    channels,
    // Legacy flat conditional fields — overwritten below by canonical
    // `conditional.branches` if present.
    condition: asStr(s.condition),
    on_true: asStr(s.on_true) ?? asStr(s.onTrue),
    on_false: asStr(s.on_false) ?? asStr(s.onFalse),
    on_true_value: asStr(s.on_true_value) ?? asStr(s.onTrueValue),
    on_false_value: asStr(s.on_false_value) ?? asStr(s.onFalseValue),
  };

  // Nested blocks --------------------------------------------------------
  const agent = isObj(s.agent) ? s.agent : undefined;
  if (agent) {
    t.agent_type = (asStr(agent.agent_type) as 'claude' | 'codex' | undefined);
    t.role = asStr(agent.role);
    t.template = asStr(agent.template);
    t.prompt = asStr(agent.prompt);
    t.timeout = asNum(agent.timeout);
    t.model = asStr(agent.model);
    if (asStr(agent.auth)) t.auth = asStr(agent.auth);
  }

  const parallel = isObj(s.parallel) ? s.parallel : undefined;
  if (parallel) {
    const ps = asStrArr(parallel.steps);
    if (ps && ps.length) t.parallel_steps = ps;
    t.parallel_join = asStr(parallel.join) as TaskDefinition['parallel_join'] | undefined;
    t.parallel_max_concurrency = asNum(parallel.max_concurrency);
  }

  const goto = isObj(s.goto) ? s.goto : undefined;
  if (goto) {
    t.goto_target = asStr(goto.target);
    t.goto_max_iterations = asNum(goto.max_iterations);
    t.goto_condition = asStr(goto.condition);
  }

  const groupChat = isObj(s.group_chat) ? s.group_chat : undefined;
  if (groupChat) {
    t.group_chat_topic = asStr(groupChat.topic);
    t.group_chat_max_rounds = asNum(groupChat.max_rounds);
    t.group_chat_participants = asStrArr(groupChat.participants);
    t.group_chat_moderator = asStr(groupChat.moderator);
    t.group_chat_strategy = asStr(groupChat.strategy);
    t.group_chat_timeout = asNum(groupChat.timeout);
  }

  const supervisor = isObj(s.supervisor) ? s.supervisor : undefined;
  if (supervisor) {
    t.supervisor_task = asStr(supervisor.task);
    t.supervisor_max_iterations = asNum(supervisor.max_iterations);
    t.supervisor_type = asStr(supervisor.supervisor_type);
    t.supervisor_timeout = asNum(supervisor.timeout);
  }

  const shell = isObj(s.shell) ? s.shell : undefined;
  if (shell) {
    t.shell_command = asStr(shell.command);
    t.shell_timeout = asNum(shell.timeout);
    t.shell_allow_failure = asBool(shell.allow_failure);
    if (asStr(shell.auth)) t.auth = asStr(shell.auth);
  }

  const python = isObj(s.python) ? s.python : undefined;
  if (python) {
    t.python_script = asStr(python.script);
    t.python_code = asStr(python.code);
    // `args` may be either an inline JSON string or a parsed object/array.
    const pyArgs = python.args;
    if (typeof pyArgs === 'string') t.python_args = pyArgs;
    else if (pyArgs !== undefined && pyArgs !== null) {
      try { t.python_args = JSON.stringify(pyArgs); } catch { /* ignore */ }
    }
    t.python_timeout = asNum(python.timeout);
    t.python_allow_failure = asBool(python.allow_failure);
    if (asStr(python.auth)) t.auth = asStr(python.auth);
  }

  const email = isObj(s.email) ? s.email : undefined;
  if (email) {
    t.email_to = asStr(email.to);
    t.email_subject = asStr(email.subject);
    t.email_body = asStr(email.body);
    t.email_body_html = asStr(email.body_html);
    t.email_from = asStr(email.from_address);
    const cc = asStrArr(email.cc);
    if (cc) t.email_cc = cc.join(', ');
    else { const ccs = asStr(email.cc); if (ccs) t.email_cc = ccs; }
    const bcc = asStrArr(email.bcc);
    if (bcc) t.email_bcc = bcc.join(', ');
    else { const bccs = asStr(email.bcc); if (bccs) t.email_bcc = bccs; }
    t.email_reply_to = asStr(email.reply_to);
    t.email_track_clicks = asBool(email.track_clicks);
    t.email_track_kref = asStr(email.track_kref);
    t.email_track_base_url = asStr(email.track_base_url);
    t.email_smtp_host = asStr(email.smtp_host);
    t.email_dry_run = asBool(email.dry_run);
    t.email_timeout = asNum(email.timeout);
    if (asStr(email.auth)) t.auth = asStr(email.auth);
  }

  const image = isObj(s.image) ? s.image : undefined;
  if (image) {
    t.image_prompt = asStr(image.prompt);
    t.image_count = asNum(image.count);
    const canvas = image.canvas;
    if (typeof canvas === 'boolean') t.image_canvas = canvas;
    else if (typeof canvas === 'string') t.image_canvas = canvas;
    t.image_register_artifact = asBool(image.register_artifact);
    t.image_space = asStr(image.space);
    t.image_item_name = asStr(image.item_name);
    t.image_output_path = asStr(image.output_path);
    t.image_output_pattern = asStr(image.output_pattern);
    t.image_sandbox = asStr(image.sandbox);
    t.image_cwd = asStr(image.cwd);
    t.image_timeout = asNum(image.timeout);
  }

  const output = isObj(s.output) ? s.output : undefined;
  if (output) {
    t.output_format = asStr(output.format);
    t.output_template = asStr(output.template);
    t.entity_name = asStr(output.entity_name);
    t.entity_kind = asStr(output.entity_kind);
    t.entity_tag = asStr(output.entity_tag);
    t.entity_space = asStr(output.entity_space);
    if (isObj(output.entity_metadata)) {
      const meta: Record<string, string> = {};
      for (const [k, v] of Object.entries(output.entity_metadata)) {
        const sv = asStr(v);
        if (sv !== undefined) meta[k] = sv;
      }
      if (Object.keys(meta).length) t.entity_metadata = meta;
    }
  }

  const notify = isObj(s.notify) ? s.notify : undefined;
  if (notify) {
    const ch = asStrArr(notify.channels);
    if (ch && ch.length) t.channels = dedupChannels(ch);
    t.notify_message = asStr(notify.message);
    t.notify_title = asStr(notify.title);
  }

  const handoff = isObj(s.handoff) ? s.handoff : undefined;
  if (handoff) {
    t.handoff_from = asStr(handoff.from_step);
    t.handoff_to = asStr(handoff.to_agent_type) as 'claude' | 'codex' | undefined;
    t.handoff_reason = asStr(handoff.reason);
    t.handoff_task = asStr(handoff.task);
    t.handoff_timeout = asNum(handoff.timeout);
  }

  const humanInput = isObj(s.human_input) ? s.human_input : undefined;
  if (humanInput) {
    t.human_input_message = asStr(humanInput.message);
    t.human_input_timeout = asNum(humanInput.timeout);
    if (asStr(humanInput.channel)) t.channel = asStr(humanInput.channel) as TaskDefinition['channel'];
  }

  const humanApproval = isObj(s.human_approval) ? s.human_approval : undefined;
  if (humanApproval) {
    t.human_approval_message = asStr(humanApproval.message);
    t.human_approval_timeout = asNum(humanApproval.timeout);
    t.human_approval_channel = asStr(humanApproval.channel);
    t.human_approval_channel_id = asStr(humanApproval.channel_id);
  }

  const a2a = isObj(s.a2a) ? s.a2a : undefined;
  if (a2a) {
    t.a2a_url = asStr(a2a.url);
    t.a2a_skill_id = asStr(a2a.skill_id);
    t.a2a_message = asStr(a2a.message);
    t.a2a_timeout = asNum(a2a.timeout);
    if (asStr(a2a.auth)) t.auth = asStr(a2a.auth);
  }

  const mapReduce = isObj(s.map_reduce) ? s.map_reduce : undefined;
  if (mapReduce) {
    t.map_reduce_task = asStr(mapReduce.task);
    t.map_reduce_mapper = asStr(mapReduce.mapper);
    t.map_reduce_reducer = asStr(mapReduce.reducer);
    t.map_reduce_concurrency = asNum(mapReduce.concurrency);
    t.map_reduce_timeout = asNum(mapReduce.timeout);
    t.map_reduce_splits = asStrArr(mapReduce.splits);
  }

  const forEach = isObj(s.for_each) ? s.for_each : undefined;
  if (forEach) {
    t.for_each_steps = asStrArr(forEach.steps);
    t.for_each_range = asStr(forEach.range);
    t.for_each_items = asStrArr(forEach.items);
    t.for_each_variable = asStr(forEach.variable);
    t.for_each_carry_forward = asBool(forEach.carry_forward);
    t.for_each_fail_fast = asBool(forEach.fail_fast);
    t.for_each_max_iterations = asNum(forEach.max_iterations);
  }

  const resolve = isObj(s.resolve) ? s.resolve : undefined;
  if (resolve) {
    t.resolve_kind = asStr(resolve.kind);
    t.resolve_tag = asStr(resolve.tag);
    t.resolve_name_pattern = asStr(resolve.name_pattern);
    t.resolve_space = asStr(resolve.space);
    t.resolve_mode = asStr(resolve.mode);
    t.resolve_fields = asStrArr(resolve.fields);
    t.resolve_fail_if_missing = asBool(resolve.fail_if_missing);
  }

  const tagStep = isObj(s.tag_step) ? s.tag_step : undefined;
  if (tagStep) {
    t.tag_item_kref = asStr(tagStep.item_kref);
    t.tag_value = asStr(tagStep.tag);
    t.tag_untag = asStr(tagStep.untag);
  }

  const deprecateStep = isObj(s.deprecate_step) ? s.deprecate_step : undefined;
  if (deprecateStep) {
    t.deprecate_item_kref = asStr(deprecateStep.item_kref);
    t.deprecate_reason = asStr(deprecateStep.reason);
  }

  // Conditional canonical form — `conditional.branches: [{condition, goto, value?}, ...]`.
  // Map first non-default branch → flat `condition` / `on_true` / `on_true_value`.
  // Map the `default` branch (or the second, fallback) → `on_false` / `on_false_value`.
  // The editor's gate node has only true/false handles so beyond two branches
  // we drop the rest — same constraint as the previous parser.
  const conditional = isObj(s.conditional) ? s.conditional : undefined;
  if (conditional && Array.isArray(conditional.branches)) {
    let trueAssigned = false;
    let falseAssigned = false;
    for (const br of conditional.branches) {
      if (!isObj(br)) continue;
      const cText = asStr(br.condition);
      const gText = asStr(br.goto);
      const vText = asStr(br.value);
      if (!cText || !gText) continue;
      if (cText === 'default') {
        if (!falseAssigned) {
          t.on_false = gText;
          if (vText) t.on_false_value = vText;
          falseAssigned = true;
        }
      } else if (!trueAssigned) {
        t.condition = cText;
        t.on_true = gText;
        if (vText) t.on_true_value = vText;
        trueAssigned = true;
      } else if (!falseAssigned) {
        // No explicit default — second branch becomes the false branch.
        t.on_false = gText;
        if (vText) t.on_false_value = vText;
        falseAssigned = true;
      }
    }
  }

  // Auth — top-level fallback (legacy YAML occasionally lifts `auth:` to step level)
  if (!t.auth) {
    const topAuth = asStr(s.auth);
    if (topAuth) t.auth = topAuth;
  }

  return t;
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
