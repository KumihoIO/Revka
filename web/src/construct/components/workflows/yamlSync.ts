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

export interface ConditionalBranchDefinition {
  condition: string;
  goto: string;
  value?: string;
}

export interface WorkflowNodePosition {
  x: number;
  y: number;
}

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
  /** Layout-only editor/viewer position. Runtime ignores this field. */
  position?: WorkflowNodePosition;
  params?: Record<string, string>;
  /** When true, executor skips the step and passes inputs straight through as output_data */
  disabled?: boolean;
  /** Pre-assigned pool agent template name */
  assign?: string;
  /** Pool persona binding for agent steps — resolves `AgentStepConfig.template`
   *  at dispatch. Architect's persona-discovery flow writes this; round-trips
   *  through `agent.template:` in YAML. */
  template?: string;
  /** Gate-only fields */
  condition?: string;
  on_true?: string;
  on_false?: string;
  /** Canonical conditional.branches entries, preserved in order. */
  conditional_branches?: ConditionalBranchDefinition[];
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
  /** Agent step: max LLM turns */
  agent_max_turns?: number;
  /** Agent step: MCP tool injection level */
  agent_tools?: 'all' | 'memory' | 'none';
  /** Agent step: required MCP tools that must be visible before launch */
  agent_required_tools?: string[];
  /** Agent step: expected JSON output fields */
  agent_output_fields?: string[];
  /** Agent step: quality-check block */
  agent_quality_enabled?: boolean;
  agent_quality_threshold?: number;
  agent_quality_criteria?: string[];
  agent_quality_model?: string;
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
  /** Supervisor: available specialist template names */
  supervisor_templates?: string[];
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
  metadata_target?: string;
  artifact_summary_model?: string;
  /** Handoff: from_step */
  handoff_from?: string;
  /** Handoff: to agent type or template name */
  handoff_to?: string;
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
  human_approval_on_reject_goto?: string;
  human_approval_on_reject_max?: number;
  human_approval_approve_keywords?: string[];
  human_approval_reject_keywords?: string[];
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
  resolve_artifact_name?: string;
  resolve_mode?: string;        // "latest" | "all"
  resolve_fields?: string[];
  resolve_metadata_source?: string;
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
  notify_channel_id?: string;
  // --- Python step (see operator_mcp/workflow/schema.py::PythonStepConfig) ---
  python_script?: string;
  python_code?: string;
  python_args?: string;
  python_interpreter?: string;
  python_timeout?: number;
  python_allow_failure?: boolean;
  // --- Compute step (see operator_mcp/workflow/schema.py::ComputeStepConfig) ---
  compute_outputs?: Record<string, string>;
  // --- Email step (see operator_mcp/workflow/schema.py::EmailStepConfig) ---
  email_to?: string;
  email_subject?: string;
  email_body?: string;
  email_body_html?: string;
  email_from?: string;
  email_cc?: string;
  email_bcc?: string;
  email_reply_to?: string;
  email_track_clicks?: boolean;
  email_track_kref?: string;
  email_track_secret_env?: string;
  email_track_base_url?: string;
  email_smtp_host?: string;
  email_smtp_port?: number;
  email_smtp_tls?: boolean;
  email_smtp_username?: string;
  email_smtp_password_env?: string;
  email_dry_run?: boolean;
  email_timeout?: number;
  // --- Image step (see operator_mcp/workflow/schema.py::ImageStepConfig) ---
  image_prompt?: string;
  image_count?: number;
  image_canvas?: boolean | string;
  image_canvas_target?: string;
  image_register_artifact?: boolean;
  image_space?: string;
  image_item_name?: string;
  image_output_path?: string;
  image_output_pattern?: string;
  image_input_images?: string[];
  image_sandbox?: string;
  image_cwd?: string;
  image_timeout?: number;
  // --- Tag step: re-tag an existing Kumiho entity revision ---
  tag_item_kref?: string;
  tag_value?: string;
  tag_untag?: string;
  // --- Deprecate step: deprecate a Kumiho item ---
  deprecate_item_kref?: string;
  deprecate_reason?: string;
  // --- Manus step: delegate web research to Manus AI ---
  manus_prompt?: string;
  manus_structured_output_schema?: string;  // JSON string round-trip
  manus_connectors?: string[];
  manus_enable_skills?: string[];
  manus_force_skills?: string[];
  manus_agent_profile?: string;
  manus_locale?: string;
  manus_project_id?: string;
  manus_title?: string;
  manus_timeout_seconds?: number;
  manus_poll_interval_seconds?: number;
  manus_allow_failure?: boolean;
  /**
   * Manus auth-profile id (e.g. ``manus:work``) — when set, the runtime
   * resolves the bound token via the gateway's auth-profile resolve
   * endpoint instead of reading the ``MANUS_API_KEY`` env var. Lives on
   * the Manus step config as ``credentials_ref`` in YAML so workflows
   * stay safe to commit (only the id is persisted; never the token).
   */
  manus_credentials_ref?: string;
  /**
   * Manus register_output — when present, the Manus step auto-publishes
   * its result as a Kumiho entity and downloads attachments to an
   * entity-anchored disk path. See `ManusRegisterOutputConfig` in
   * operator-mcp/operator_mcp/workflow/schema.py for the on-disk layout
   * and Kumiho publish semantics.
   *
   * `manus_register_enabled` is the canonical on/off flag — when true the
   * emit path writes a `register_output:` block (regardless of whether
   * entity_name/kind are set; empty fields fail-fast in the runtime so
   * users see the error). The other fields hold the per-field values that
   * survive a toggle-off + toggle-on cycle.
   */
  manus_register_enabled?: boolean;
  manus_register_entity_name?: string;
  manus_register_entity_kind?: string;
  manus_register_entity_tag?: string;
  manus_register_entity_space?: string;
  manus_register_attachments?: boolean;
  manus_register_content_source?: 'message' | 'structured';
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
  error?: string;
  output_preview?: string;
  input_data?: Record<string, unknown>;
  output_data?: Record<string, unknown>;
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
  /** When true, executor skips the step and passes inputs straight through as output_data */
  disabled?: boolean;
  /** Pre-assigned pool agent template name */
  assign: string;
  /** Pool persona binding (`agent.template`) — set by Architect's persona
   *  discovery or by a hand-edited YAML. Distinct from `assign`, which is
   *  only written by the AgentPicker side-panel UI. */
  template: string;
  /** UI-only: resolved from the pool-agent roster, never serialized to YAML. */
  agentAvatarUrl?: string;
  agentDisplayName?: string;
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
  /** Gate-only: canonical conditional.branches entries, preserved in order. */
  conditionalBranches: ConditionalBranchDefinition[];
  /** Human-input channel */
  channel: string;
  /** Notify channels (multi-select) */
  channels: string[];
  /** Executor step type fields */
  agentType: string;
  role: string;
  prompt: string;
  timeout: number;
  agentMaxTurns: number;
  agentTools: 'all' | 'memory' | 'none';
  agentRequiredTools: string[];
  agentOutputFields: string[];
  agentQualityEnabled: boolean;
  agentQualityThreshold: number;
  agentQualityCriteria: string[];
  agentQualityModel: string;
  parallelJoin: string;
  gotoTarget: string;
  gotoMaxIterations: number;
  groupChatTopic: string;
  groupChatParticipants: string[];
  groupChatMaxRounds: number;
  supervisorTask: string;
  supervisorMaxIterations: number;
  supervisorTemplates: string[];
  shellCommand: string;
  outputFormat: string;
  entityName: string;
  entityKind: string;
  entityTag: string;
  entitySpace: string;
  entityMetadata: Record<string, string>;
  entityMetadataTarget: string;
  artifactSummaryModel: string;
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
  humanApprovalOnRejectGoto: string;
  humanApprovalOnRejectMax: number;
  humanApprovalApproveKeywords: string[];
  humanApprovalRejectKeywords: string[];
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
  resolveArtifactName: string;
  resolveMode: string;
  resolveFields: string[];
  resolveMetadataSource: string;
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
  notifyChannelId: string;
  // Python step
  pythonScript: string;
  pythonCode: string;
  pythonArgs: string;
  pythonInterpreter: string;
  pythonTimeout: number;
  pythonAllowFailure: boolean;
  // Compute step
  computeOutputs: Record<string, string>;
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
  emailTrackSecretEnv: string;
  emailTrackBaseUrl: string;
  emailSmtpHost: string;
  emailSmtpPort: number;
  emailSmtpTls: boolean;
  emailSmtpUsername: string;
  emailSmtpPasswordEnv: string;
  emailDryRun: boolean;
  emailTimeout: number;
  // Image step
  imagePrompt: string;
  imageCount: number;
  imageCanvas: boolean;
  imageCanvasTarget: string;
  imageRegisterArtifact: boolean;
  imageSpace: string;
  imageItemName: string;
  imageOutputPath: string;
  imageOutputPattern: string;
  imageInputImages: string[];
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
  // Manus step
  manusPrompt: string;
  manusStructuredOutputSchema: string;
  manusConnectors: string[];
  manusEnableSkills: string[];
  manusForceSkills: string[];
  manusAgentProfile: string;
  manusLocale: string;
  manusProjectId: string;
  manusTitle: string;
  manusTimeoutSeconds: number;
  manusPollIntervalSeconds: number;
  manusAllowFailure: boolean;
  manusCredentialsRef: string;
  // Manus register_output — Kumiho entity auto-publish + attachment download.
  // `manusRegisterEnabled` is the canonical on/off flag for the UI checkbox
  // and gates emission of the `register_output:` YAML block. The other
  // fields hold per-field values so toggling off → on restores user input.
  manusRegisterEnabled: boolean;
  manusRegisterEntityName: string;
  manusRegisterEntityKind: string;
  manusRegisterEntityTag: string;
  manusRegisterEntitySpace: string;
  manusRegisterAttachments: boolean;
  manusRegisterContentSource: 'message' | 'structured';
  /** Encrypted auth-profile id (e.g. `gmail:work`) — resolved at runtime. */
  auth?: string;
  /** Run-mode overlay — populated when viewing a workflow run */
  runInfo?: StepRunInfo;
  [key: string]: unknown;
}

export interface TriggerDef {
  onKind: string;
  onTag: string;
  onNamePattern: string;
  onSpace: string;
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
  python: 'python', compute: 'compute', email: 'email',
  tag: 'tag', deprecate: 'deprecate',
  manus: 'manus',
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

const asPosition = (v: YAMLValue): WorkflowNodePosition | undefined => {
  if (!isObj(v)) return undefined;
  const x = asNum(v.x);
  const y = asNum(v.y);
  if (x === undefined || y === undefined) return undefined;
  if (!Number.isFinite(x) || !Number.isFinite(y)) return undefined;
  return { x, y };
};

const roundPositionCoordinate = (value: number): number => {
  if (!Number.isFinite(value)) return 0;
  const rounded = Math.round(value * 100) / 100;
  return Object.is(rounded, -0) ? 0 : rounded;
};

const normalizeNodePosition = (position: Node['position']): WorkflowNodePosition => ({
  x: roundPositionCoordinate(position?.x ?? 0),
  y: roundPositionCoordinate(position?.y ?? 0),
});

function normalizeConditionalBranch(branch: ConditionalBranchDefinition): ConditionalBranchDefinition {
  const goto = branch.goto.trim();
  const condition = branch.condition.trim() || (goto ? 'default' : '');
  return {
    condition,
    goto,
    value: branch.value?.trim() || undefined,
  };
}

export function hasPersistedTaskPositions(tasks: TaskDefinition[]): boolean {
  return tasks.some((task) => task.position !== undefined);
}

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
    position: asPosition(s.position),
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
    t.agent_max_turns = asNum(agent.max_turns);
    const tools = asStr(agent.tools);
    if (tools === 'all' || tools === 'memory' || tools === 'none') t.agent_tools = tools;
    t.agent_required_tools = asStrArr(agent.required_tools);
    t.agent_output_fields = asStrArr(agent.output_fields);
    const quality = isObj(agent.quality_check) ? agent.quality_check : undefined;
    if (quality) {
      t.agent_quality_enabled = asBool(quality.enabled);
      t.agent_quality_threshold = asNum(quality.threshold);
      t.agent_quality_criteria = asStrArr(quality.criteria);
      t.agent_quality_model = asStr(quality.model);
    }
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
    t.supervisor_templates = asStrArr(supervisor.templates);
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
    t.python_interpreter = asStr(python.python);
    t.python_timeout = asNum(python.timeout);
    t.python_allow_failure = asBool(python.allow_failure);
    if (asStr(python.auth)) t.auth = asStr(python.auth);
  }

  const compute = isObj(s.compute) ? s.compute : undefined;
  if (compute && isObj(compute.outputs)) {
    const outputs: Record<string, string> = {};
    for (const [k, v] of Object.entries(compute.outputs)) {
      const sv = asStr(v);
      if (sv !== undefined) outputs[k] = sv;
    }
    if (Object.keys(outputs).length > 0) t.compute_outputs = outputs;
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
    t.email_track_secret_env = asStr(email.track_secret_env);
    t.email_track_base_url = asStr(email.track_base_url);
    t.email_smtp_host = asStr(email.smtp_host);
    t.email_smtp_port = asNum(email.smtp_port);
    t.email_smtp_tls = asBool(email.smtp_tls);
    t.email_smtp_username = asStr(email.smtp_username);
    t.email_smtp_password_env = asStr(email.smtp_password_env);
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
    else if (typeof canvas === 'string') {
      t.image_canvas = canvas;
      t.image_canvas_target = canvas;
    }
    t.image_register_artifact = asBool(image.register_artifact);
    t.image_space = asStr(image.space);
    t.image_item_name = asStr(image.item_name);
    t.image_output_path = asStr(image.output_path);
    t.image_output_pattern = asStr(image.output_pattern);
    const inputImages = asStrArr(image.input_images);
    if (inputImages) t.image_input_images = inputImages;
    else {
      const inputImage = asStr(image.input_images);
      if (inputImage) t.image_input_images = [inputImage];
    }
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
    t.metadata_target = asStr(output.metadata_target);
    t.artifact_summary_model = asStr(output.artifact_summary_model);
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
    t.notify_channel_id = asStr(notify.channel_id);
    t.notify_message = asStr(notify.message);
    t.notify_title = asStr(notify.title);
  }

  const handoff = isObj(s.handoff) ? s.handoff : undefined;
  if (handoff) {
    t.handoff_from = asStr(handoff.from_step);
    t.handoff_to = asStr(handoff.to_agent_type);
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
    t.human_approval_on_reject_goto = asStr(humanApproval.on_reject_goto);
    t.human_approval_on_reject_max = asNum(humanApproval.on_reject_max);
    t.human_approval_approve_keywords = asStrArr(humanApproval.approve_keywords);
    t.human_approval_reject_keywords = asStrArr(humanApproval.reject_keywords);
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
    t.resolve_artifact_name = asStr(resolve.artifact_name);
    t.resolve_mode = asStr(resolve.mode);
    t.resolve_fields = asStrArr(resolve.fields);
    t.resolve_metadata_source = asStr(resolve.metadata_source);
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

  const manus = isObj(s.manus) ? s.manus : undefined;
  if (manus) {
    t.manus_prompt = asStr(manus.prompt);
    // structured_output_schema is an object on the wire; we round-trip
    // through a JSON string so the editor textarea can show it verbatim.
    if (manus.structured_output_schema !== undefined &&
        manus.structured_output_schema !== null) {
      try {
        t.manus_structured_output_schema = JSON.stringify(
          manus.structured_output_schema,
          null,
          2,
        );
      } catch { /* ignore — leave undefined */ }
    }
    t.manus_connectors = asStrArr(manus.connectors);
    t.manus_enable_skills = asStrArr(manus.enable_skills);
    t.manus_force_skills = asStrArr(manus.force_skills);
    t.manus_agent_profile = asStr(manus.agent_profile);
    t.manus_locale = asStr(manus.locale);
    t.manus_project_id = asStr(manus.project_id);
    t.manus_title = asStr(manus.title);
    t.manus_timeout_seconds = asNum(manus.timeout_seconds);
    t.manus_poll_interval_seconds = asNum(manus.poll_interval_seconds);
    t.manus_allow_failure = asBool(manus.allow_failure);
    t.manus_credentials_ref = asStr(manus.credentials_ref);

    // register_output — nested block, optional. Round-trip every field so
    // re-emitted YAML matches the input. Presence of the block flips the
    // canonical `manus_register_enabled` flag on; the per-field values
    // round-trip independently.
    const ro = isObj(manus.register_output) ? manus.register_output : undefined;
    if (ro) {
      t.manus_register_enabled = true;
      t.manus_register_entity_name = asStr(ro.entity_name);
      t.manus_register_entity_kind = asStr(ro.entity_kind);
      const tag = asStr(ro.entity_tag);
      if (tag) t.manus_register_entity_tag = tag;
      const space = asStr(ro.entity_space);
      if (space) t.manus_register_entity_space = space;
      // `register_attachments` defaults to true on the wire; only round-trip
      // the explicit `false` case so a re-emit stays clean for the common path.
      if (ro.register_attachments === false) {
        t.manus_register_attachments = false;
      } else if (ro.register_attachments === true) {
        t.manus_register_attachments = true;
      }
      const cs = asStr(ro.content_source);
      if (cs === 'structured' || cs === 'message') {
        t.manus_register_content_source = cs;
      }
    }
  }

  // Conditional canonical form — `conditional.branches: [{condition, goto, value?}, ...]`.
  // Preserve every branch for the editor, while also populating the legacy
  // flat fields used by older true/false gate surfaces.
  const conditional = isObj(s.conditional) ? s.conditional : undefined;
  if (conditional && Array.isArray(conditional.branches)) {
    const branches: ConditionalBranchDefinition[] = [];
    for (const br of conditional.branches) {
      if (!isObj(br)) continue;
      const cText = asStr(br.condition);
      const gText = asStr(br.goto);
      const vText = asStr(br.value);
      if (!gText) continue;
      branches.push(normalizeConditionalBranch({
        condition: cText ?? '',
        goto: gText,
        value: vText || undefined,
      }));
    }
    if (branches.length > 0) {
      t.conditional_branches = branches;
      Object.assign(t, flattenConditionalBranches(branches));
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
            const trigger: TriggerDef = { onKind: '', onTag: 'ready', onNamePattern: '', onSpace: '', inputMap: {} };
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
                else if (tk === 'on_space') trigger.onSpace = tv;
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
            if (input.name) meta.inputs.push(input);
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
  default: { stroke: 'var(--construct-signal-selected)', strokeWidth: 2 },
} as const;

export function gateBranchHandle(index: number): string {
  return `branch-${index}`;
}

export function gateBranchIndex(handle: string | null | undefined): number | null {
  if (!handle) return null;
  if (handle === 'true') return 0;
  if (handle === 'false') return 1;
  const match = /^branch-(\d+)$/.exec(handle);
  return match ? Number(match[1]) : null;
}

export function isGateBranchHandle(handle: string | null | undefined): boolean {
  return handle === 'true' || handle === 'false' || gateBranchIndex(handle) !== null;
}

export function gateBranchLabel(branch: ConditionalBranchDefinition, index: number): string {
  if (branch.condition.trim() === 'default') return 'default';
  if (index === 0) return 'true';
  return `case ${index + 1}`;
}

export function gateBranchStyle(branch: ConditionalBranchDefinition, index: number): typeof GATE_EDGE_STYLES[keyof typeof GATE_EDGE_STYLES] {
  if (branch.condition.trim() === 'default') return GATE_EDGE_STYLES.false;
  if (index === 0) return GATE_EDGE_STYLES.true;
  return GATE_EDGE_STYLES.default;
}

export function gateEdgeStyleForHandle(handle: string | null | undefined): typeof GATE_EDGE_STYLES[keyof typeof GATE_EDGE_STYLES] {
  if (handle === 'true') return GATE_EDGE_STYLES.true;
  if (handle === 'false') return GATE_EDGE_STYLES.false;
  return gateBranchIndex(handle) !== null ? GATE_EDGE_STYLES.default : GATE_EDGE_STYLES.default;
}

function normalizeConditionalBranches(task: Pick<TaskDefinition, 'condition' | 'on_true' | 'on_false' | 'on_true_value' | 'on_false_value' | 'conditional_branches'>): ConditionalBranchDefinition[] {
  if (task.conditional_branches && task.conditional_branches.length > 0) {
    return task.conditional_branches
      .map(normalizeConditionalBranch)
      .filter((branch) => branch.goto);
  }

  const branches: ConditionalBranchDefinition[] = [];
  if (task.condition && task.on_true) {
    branches.push(normalizeConditionalBranch({
      condition: task.condition,
      goto: task.on_true,
      value: task.on_true_value || undefined,
    }));
  }
  if (task.on_false) {
    branches.push(normalizeConditionalBranch({
      condition: 'default',
      goto: task.on_false,
      value: task.on_false_value || undefined,
    }));
  }
  return branches;
}

function flattenConditionalBranches(branches: ConditionalBranchDefinition[]): {
  condition?: string;
  on_true?: string;
  on_false?: string;
  on_true_value?: string;
  on_false_value?: string;
} {
  const firstCase = branches.find((branch) => branch.condition !== 'default');
  const fallback = branches.find((branch) => branch.condition === 'default')
    ?? branches.find((branch) => branch !== firstCase);

  return {
    condition: firstCase?.condition,
    on_true: firstCase?.goto,
    on_false: fallback?.goto,
    on_true_value: firstCase?.value,
    on_false_value: fallback?.value,
  };
}

export function tasksToFlow(tasks: TaskDefinition[]): { nodes: Node<TaskNodeData>[]; edges: Edge[] } {
  const isGate = (t: TaskDefinition) => t.type === 'conditional';

  const nodes: Node<TaskNodeData>[] = tasks.map((task, i) => ({
    id: task.id,
    type: isGate(task) ? 'gateNode' : 'taskNode',
    position: task.position ?? { x: 0, y: i * 150 },
    width: isGate(task) ? 220 : 280,
    // Initial height hint — required for the MiniMap to render rectangles
    // before nodes are measured. The actual rendered card uses minHeight
    // and grows past this; WorkflowNode.tsx calls useUpdateNodeInternals
    // via a ResizeObserver so React Flow re-anchors handles + redraws
    // the MiniMap to the measured dimensions whenever the card resizes.
    height: isGate(task) ? Math.max(96, 86 + normalizeConditionalBranches(task).length * 24) : 140,
    data: {
      label: task.name || task.id,
      taskId: task.id,
      name: task.name || task.id,
      description: task.description,
      type: task.type,
      agentHints: task.agent_hints,
      skills: task.skills,
      disabled: task.disabled ?? false,
      assign: task.assign || '',
      template: task.template || '',
      paramCount: task.params ? Object.keys(task.params).length : 0,
      dependencyCount: task.depends_on.length,
      condition: task.condition || '',
      onTrueValue: task.on_true_value || '',
      onFalseValue: task.on_false_value || '',
      conditionalBranches: normalizeConditionalBranches(task),
      channel: task.channel || '',
      channels: task.channels || [],
      agentType: task.agent_type || '',
      role: task.role || '',
      prompt: task.prompt || '',
      timeout: task.timeout || 300,
      agentMaxTurns: task.agent_max_turns ?? 3,
      agentTools: task.agent_tools ?? 'none',
      agentRequiredTools: task.agent_required_tools || [],
      agentOutputFields: task.agent_output_fields || [],
      agentQualityEnabled: task.agent_quality_enabled ?? false,
      agentQualityThreshold: task.agent_quality_threshold ?? 0.7,
      agentQualityCriteria: task.agent_quality_criteria || [],
      agentQualityModel: task.agent_quality_model || 'claude-haiku-4-5-20251001',
      parallelJoin: task.parallel_join || 'all',
      gotoTarget: task.goto_target || '',
      gotoMaxIterations: task.goto_max_iterations || 3,
      groupChatTopic: task.group_chat_topic || '',
      groupChatParticipants: task.group_chat_participants || [],
      groupChatMaxRounds: task.group_chat_max_rounds || 8,
      supervisorTask: task.supervisor_task || '',
      supervisorMaxIterations: task.supervisor_max_iterations || 5,
      supervisorTemplates: task.supervisor_templates || [],
      shellCommand: task.shell_command || '',
      outputFormat: task.output_format || 'markdown',
      entityName: task.entity_name || '',
      entityKind: task.entity_kind || '',
      entityTag: task.entity_tag || '',
      entitySpace: task.entity_space || '',
      entityMetadata: task.entity_metadata || {},
      entityMetadataTarget: task.metadata_target || 'item',
      artifactSummaryModel: task.artifact_summary_model || '',
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
      humanApprovalOnRejectGoto: task.human_approval_on_reject_goto || '',
      humanApprovalOnRejectMax: task.human_approval_on_reject_max || 3,
      humanApprovalApproveKeywords: task.human_approval_approve_keywords || ['approve', 'approved', 'yes', 'lgtm'],
      humanApprovalRejectKeywords: task.human_approval_reject_keywords || ['reject', 'rejected', 'no'],
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
      resolveArtifactName: task.resolve_artifact_name ?? '',
      resolveMode: task.resolve_mode ?? 'latest',
      resolveFields: task.resolve_fields ?? [],
      resolveMetadataSource: task.resolve_metadata_source ?? 'revision',
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
      notifyChannelId: task.notify_channel_id || '',
      pythonScript: task.python_script || '',
      pythonCode: task.python_code || '',
      pythonArgs: task.python_args || '',
      pythonInterpreter: task.python_interpreter || '',
      pythonTimeout: task.python_timeout || 60,
      pythonAllowFailure: task.python_allow_failure || false,
      computeOutputs: task.compute_outputs || {},
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
      emailTrackSecretEnv: task.email_track_secret_env || '',
      emailTrackBaseUrl: task.email_track_base_url || '',
      emailSmtpHost: task.email_smtp_host || '',
      emailSmtpPort: task.email_smtp_port || 0,
      emailSmtpTls: task.email_smtp_tls ?? true,
      emailSmtpUsername: task.email_smtp_username || '',
      emailSmtpPasswordEnv: task.email_smtp_password_env || '',
      emailDryRun: task.email_dry_run || false,
      emailTimeout: task.email_timeout || 30,
      imagePrompt: task.image_prompt || '',
      imageCount: task.image_count ?? 1,
      imageCanvas: task.image_canvas !== false,
      imageCanvasTarget: task.image_canvas_target || (typeof task.image_canvas === 'string' ? task.image_canvas : ''),
      imageRegisterArtifact: task.image_register_artifact !== false,
      imageSpace: task.image_space || '',
      imageItemName: task.image_item_name || '',
      imageOutputPath: task.image_output_path || '',
      imageOutputPattern: task.image_output_pattern || '',
      imageInputImages: task.image_input_images || [],
      imageSandbox: task.image_sandbox || '',
      imageCwd: task.image_cwd || '',
      imageTimeout: task.image_timeout || 1200,
      tagItemKref: task.tag_item_kref || '',
      tagValue: task.tag_value || '',
      tagUntag: task.tag_untag || '',
      deprecateItemKref: task.deprecate_item_kref || '',
      deprecateReason: task.deprecate_reason || '',
      manusPrompt: task.manus_prompt || '',
      manusStructuredOutputSchema: task.manus_structured_output_schema || '',
      manusConnectors: task.manus_connectors || [],
      manusEnableSkills: task.manus_enable_skills || [],
      manusForceSkills: task.manus_force_skills || [],
      manusAgentProfile: task.manus_agent_profile || '',
      manusLocale: task.manus_locale || '',
      manusProjectId: task.manus_project_id || '',
      manusTitle: task.manus_title || '',
      manusTimeoutSeconds: task.manus_timeout_seconds ?? 600,
      manusPollIntervalSeconds: task.manus_poll_interval_seconds ?? 5,
      manusAllowFailure: task.manus_allow_failure || false,
      manusCredentialsRef: task.manus_credentials_ref || '',
      manusRegisterEnabled: task.manus_register_enabled === true,
      manusRegisterEntityName: task.manus_register_entity_name || '',
      manusRegisterEntityKind: task.manus_register_entity_kind || '',
      manusRegisterEntityTag: task.manus_register_entity_tag || '',
      manusRegisterEntitySpace: task.manus_register_entity_space || '',
      // Default true on the wire — preserve unless explicitly false.
      manusRegisterAttachments: task.manus_register_attachments !== false,
      manusRegisterContentSource: task.manus_register_content_source || 'message',
      auth: task.auth || '',
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
    const key = `${edge.source}:${edge.sourceHandle ?? ''}->${edge.target}`;
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
  const forEachChildParentMap = new Map<string, string>();
  for (const task of tasks) {
    if (task.for_each_steps && task.for_each_steps.length > 0) {
      const validChildren = task.for_each_steps.filter((c) => nodeIds.has(c));
      if (validChildren.length > 0) {
        forEachChildrenMap.set(task.id, validChildren);
        for (const child of validChildren) {
          forEachChildParentMap.set(child, task.id);
        }
      }
    }
  }

  // Normal dependency edges (with parallel/for_each fan-out rewriting)
  for (const task of tasks) {
    for (const dep of task.depends_on) {
      // A for_each wrapper already owns its body steps through for_each.steps.
      // Treat an accidental body depends_on: [wrapper] as membership, not a
      // real dependency, or save/reload can create backend circular validation.
      if (forEachChildParentMap.get(task.id) === dep) continue;
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

  // Gate branch edges (canonical conditional.branches, including multi-branch gates)
  for (const task of tasks) {
    if (!isGate(task)) continue;
    const branches = normalizeConditionalBranches(task);
    branches.forEach((branch, index) => {
      if (!nodeIds.has(branch.goto)) return;
      const handle = gateBranchHandle(index);
      const style = gateBranchStyle(branch, index);
      const label = gateBranchLabel(branch, index);
      pushEdge({
        id: `${task.id}->${handle}->${branch.goto}`,
        source: task.id,
        sourceHandle: handle,
        target: branch.goto,
        type: 'default',
        animated: true,
        selectable: true,
        interactionWidth: 20,
        style,
        label,
        labelStyle: { fill: style.stroke, fontSize: 10, fontWeight: 600 },
      });
    });
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
      if (forEachChildParentMap.get(task.id) === ref) continue;
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
  'entity_name',
  'entity_kind',
  'entity_tag',
  'entity_space',
  'resolve_kind',
  'resolve_tag',
  'resolve_name_pattern',
  'resolve_space',
  'resolve_artifact_name',
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
  'manus_prompt',
  'manus_title',
];

/** IDs that look like step refs but are actually workflow-scope sources. */
const NON_STEP_REF_IDS = new Set([
  'input',
  'inputs',
  'trigger',
  'env',
  'outputs',
  'context',
  'loop',
  'for_each',
  'previous',
  'rejection',
  'run_id',
]);

/** Match `${step_id}` and `${step_id.field}` (and `${step_id.field.subfield}`).
 *  step_id matches the YAML id rule: starts with a letter or underscore,
 *  then letters/digits/underscores/hyphens. We don't try to handle nested
 *  expressions or pipes — LLMs use simple references. */
const STEP_REF_REGEX = /\$\{(?!\{)([a-zA-Z_][a-zA-Z0-9_-]*)(?:\.[a-zA-Z_][a-zA-Z0-9_.-]*)?\}/g;
const EXPR_TEMPLATE_REGEX = /\$\{\{\s*([\s\S]*?)\s*\}\}/g;

function isExprIdentStart(ch: string): boolean {
  return /[A-Za-z_]/.test(ch);
}

function isExprIdentPart(ch: string): boolean {
  return /[A-Za-z0-9_-]/.test(ch);
}

function extractExpressionRootRefs(body: string): string[] {
  const refs = new Set<string>();
  let quote: string | null = null;
  for (let i = 0; i < body.length;) {
    const ch = body[i]!;
    if (quote) {
      if (ch === '\\') {
        i += 2;
        continue;
      }
      if (ch === quote) quote = null;
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      i += 1;
      continue;
    }
    if (!isExprIdentStart(ch)) {
      i += 1;
      continue;
    }
    const start = i;
    i += 1;
    while (i < body.length && isExprIdentPart(body[i]!)) i += 1;
    const ident = body.slice(start, i);
    const prev = start > 0 ? body[start - 1]! : '';
    if (prev && /[A-Za-z0-9_.]/.test(prev)) continue;
    let j = i;
    while (j < body.length && /\s/.test(body[j]!)) j += 1;
    if (body[j] === '.') refs.add(ident);
  }
  return [...refs];
}

function addStepRefsFromText(value: string, nodeIds: Set<string>, refs: Set<string>, aliasToId: Map<string, string>): void {
  STEP_REF_REGEX.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = STEP_REF_REGEX.exec(value)) !== null) {
    const rawId = m[1]!;
    const id = aliasToId.get(rawId) ?? rawId;
    if (NON_STEP_REF_IDS.has(rawId)) continue;
    if (!nodeIds.has(id)) continue;
    refs.add(id);
  }

  EXPR_TEMPLATE_REGEX.lastIndex = 0;
  let expr: RegExpExecArray | null;
  while ((expr = EXPR_TEMPLATE_REGEX.exec(value)) !== null) {
    const body = expr[1] ?? '';
    for (const rawId of extractExpressionRootRefs(body)) {
      const id = aliasToId.get(rawId) ?? rawId;
      if (NON_STEP_REF_IDS.has(rawId)) continue;
      if (!nodeIds.has(id)) continue;
      refs.add(id);
    }
  }
}

function collectStepRefs(task: TaskDefinition, nodeIds: Set<string>): Set<string> {
  const refs = new Set<string>();
  const aliasToId = new Map<string, string>();
  for (const id of nodeIds) {
    const alias = id.replace(/-/g, '_');
    if (alias !== id && !nodeIds.has(alias)) aliasToId.set(alias, id);
  }
  for (const field of INTERPOLATION_TEXT_FIELDS) {
    const value = task[field];
    if (typeof value !== 'string' || !value) continue;
    addStepRefsFromText(value, nodeIds, refs, aliasToId);
  }
  if (task.compute_outputs) {
    for (const value of Object.values(task.compute_outputs)) {
      if (typeof value === 'string' && value) {
        addStepRefsFromText(value, nodeIds, refs, aliasToId);
      }
    }
  }
  if (task.entity_metadata) {
    for (const value of Object.values(task.entity_metadata)) {
      if (typeof value === 'string' && value) {
        addStepRefsFromText(value, nodeIds, refs, aliasToId);
      }
    }
  }
  return refs;
}

// ---------------------------------------------------------------------------
// Run-to-step ancestor closure (preview helper)
// ---------------------------------------------------------------------------

/**
 * Compute the transitive ancestor closure of `targetId` (inclusive) over a
 * task list. Used by the StepConfigPanel "Run to here" popover to preview
 * which steps would execute. The backend re-derives this authoritatively
 * before scheduling so a stale frontend list cannot mis-target the run.
 *
 * Walks `depends_on` edges via BFS. Treats `parallel.steps` as descendants
 * of the parallel wrapper — i.e. selecting a parallel child also pulls in
 * the wrapper. Mirrors `compute_ancestor_closure` in operator-mcp's
 * executor.py; keep them in sync when the rules change.
 *
 * Returns the closure in topological-ish order: each step appears AFTER its
 * ancestors so the popover lists the run order naturally. The target is
 * always last.
 */
export function computeAncestorClosure(tasks: TaskDefinition[], targetId: string): string[] {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  if (!byId.has(targetId)) return [];

  // Reverse map: child_id -> wrapper(s) that own it (parallel + for_each).
  const parentWrappers = new Map<string, Set<string>>();
  for (const t of tasks) {
    if (t.type === 'parallel' && t.parallel_steps) {
      for (const child of t.parallel_steps) {
        if (!parentWrappers.has(child)) parentWrappers.set(child, new Set());
        parentWrappers.get(child)!.add(t.id);
      }
    }
    if (t.type === 'for_each' && t.for_each_steps) {
      for (const child of t.for_each_steps) {
        if (!parentWrappers.has(child)) parentWrappers.set(child, new Set());
        parentWrappers.get(child)!.add(t.id);
      }
    }
  }

  // Track wrappers reached via explicit depends_on (a downstream consumer)
  // vs only via the implicit child→wrapper rule. Only consumed wrappers
  // expand their full child list; otherwise targeting one child of a
  // parallel would silently drag in all siblings.
  const consumedWrappers = new Set<string>();

  // BFS up.
  const closure = new Set<string>();
  const queue: string[] = [targetId];
  while (queue.length > 0) {
    const sid = queue.pop()!;
    if (closure.has(sid)) continue;
    closure.add(sid);
    const step = byId.get(sid);
    if (!step) continue;
    for (const dep of step.depends_on) {
      const depStep = byId.get(dep);
      if (depStep && (depStep.type === 'parallel' || depStep.type === 'for_each')) {
        consumedWrappers.add(dep);
      }
      if (!closure.has(dep) && byId.has(dep)) queue.push(dep);
    }
    const wrappers = parentWrappers.get(sid);
    if (wrappers) {
      for (const w of wrappers) {
        if (!closure.has(w)) queue.push(w);
      }
    }
  }

  // Post-pass: pull every child of consumed wrappers into closure. Mirrors
  // operator-mcp executor.compute_ancestor_closure — without this, a target
  // downstream of a parallel falsely runs zero children (false-green).
  const expandQueue: string[] = [];
  for (const wid of consumedWrappers) {
    const w = byId.get(wid);
    if (!w) continue;
    if (w.type === 'parallel' && w.parallel_steps) {
      for (const cid of w.parallel_steps) {
        if (!closure.has(cid)) expandQueue.push(cid);
      }
    }
    if (w.type === 'for_each' && w.for_each_steps) {
      for (const cid of w.for_each_steps) {
        if (!closure.has(cid)) expandQueue.push(cid);
      }
    }
  }

  while (expandQueue.length > 0) {
    const sid = expandQueue.pop()!;
    if (closure.has(sid)) continue;
    closure.add(sid);
    const step = byId.get(sid);
    if (!step) continue;
    for (const dep of step.depends_on) {
      const depStep = byId.get(dep);
      if (depStep && (depStep.type === 'parallel' || depStep.type === 'for_each')) {
        if (!consumedWrappers.has(dep)) {
          consumedWrappers.add(dep);
          if (depStep.type === 'parallel' && depStep.parallel_steps) {
            for (const cid of depStep.parallel_steps) {
              if (!closure.has(cid)) expandQueue.push(cid);
            }
          }
          if (depStep.type === 'for_each' && depStep.for_each_steps) {
            for (const cid of depStep.for_each_steps) {
              if (!closure.has(cid)) expandQueue.push(cid);
            }
          }
        }
      }
      if (!closure.has(dep) && byId.has(dep)) expandQueue.push(dep);
    }
    const wrappers = parentWrappers.get(sid);
    if (wrappers) {
      for (const w of wrappers) {
        if (!closure.has(w)) expandQueue.push(w);
      }
    }
  }

  // Order by tasks-list position so the popover renders predictably. Task
  // list order is roughly definition order; not strictly topo, but for the
  // typical author flow it's the order users see in the editor.
  return tasks.filter((t) => closure.has(t.id)).map((t) => t.id);
}

// ---------------------------------------------------------------------------
// React Flow graph → YAML (serialize)
// ---------------------------------------------------------------------------

export function flowToTasks(nodes: Node<TaskNodeData>[], edges: Edge[]): TaskDefinition[] {
  // Regular dependency edges (no sourceHandle or sourceHandle is not a gate branch)
  const depsMap = new Map<string, string[]>();
  // Gate branch edges
  const trueBranch = new Map<string, string>();  // gateId → target
  const falseBranch = new Map<string, string>(); // gateId → target
  const branchTargets = new Map<string, Map<number, string>>(); // gateId → branch index → target
  // Parallel children: parallel_node_id → [child_task_ids in edge order]
  const parallelChildren = new Map<string, string[]>();

  const parallelNodeIds = new Set(
    nodes.filter((n) => n.data.type === 'parallel').map((n) => n.id),
  );
  const nodeIdToTaskId = new Map(nodes.map((n) => [n.id, n.data.taskId]));
  const forEachChildParent = new Map<string, string>();
  for (const node of nodes) {
    if (node.data.type !== 'for_each') continue;
    for (const childTaskId of node.data.forEachSteps ?? []) {
      forEachChildParent.set(childTaskId, node.id);
    }
  }

  for (const edge of edges) {
    const edgeData = edge.data as Record<string, unknown> | undefined;
    // Interpolation edges are inferred from text fields and regenerated on
    // load. Persisting them as depends_on mutates hand-authored YAML and can
    // reintroduce deleted cycle edges on save/reopen.
    if (edgeData?.inferred === 'interpolation') continue;

    // Edges from a parallel parent define child membership (→ parallel.steps).
    // Process before the synthetic-skip so round-tripping a YAML that already
    // has `parallel.steps` preserves the list.
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
    if (edgeData?.synthetic) continue;
    const branchIndex = gateBranchIndex(edge.sourceHandle as string | null | undefined);
    if (branchIndex !== null) {
      const branches = branchTargets.get(edge.source) ?? new Map<number, string>();
      branches.set(branchIndex, edge.target);
      branchTargets.set(edge.source, branches);
    } else if (edge.sourceHandle === 'true') {
      trueBranch.set(edge.source, edge.target);
    } else if (edge.sourceHandle === 'false') {
      falseBranch.set(edge.source, edge.target);
    } else {
      const targetTaskId = nodeIdToTaskId.get(edge.target) ?? edge.target;
      if (forEachChildParent.get(targetTaskId) === edge.source) continue;
      const deps = depsMap.get(edge.target) || [];
      deps.push(edge.source);
      depsMap.set(edge.target, deps);
    }
  }

  return nodes.map((node) => {
    const d = node.data;
    const st = d.type || 'agent';
    const conditionalBranches = st === 'conditional'
      ? (() => {
          const branches = (d.conditionalBranches && d.conditionalBranches.length > 0
            ? d.conditionalBranches
            : [
                ...(d.condition
                  ? [{ condition: d.condition, goto: '', value: d.onTrueValue || undefined }]
                  : []),
                ...(d.onFalseValue
                  ? [{ condition: 'default', goto: '', value: d.onFalseValue }]
                  : []),
              ]).map((branch) => ({ ...branch }));

          const indexedTargets = branchTargets.get(node.id);
          if (indexedTargets) {
            for (const [index, target] of indexedTargets) {
              if (branches[index]) branches[index] = { ...branches[index]!, goto: target };
            }
          }

          const trueTarget = trueBranch.get(node.id);
          if (trueTarget) {
            const idx = branches.findIndex((branch) => branch.condition !== 'default');
            if (idx >= 0) branches[idx] = { ...branches[idx]!, goto: trueTarget };
            else branches.unshift({ condition: d.condition || 'true', goto: trueTarget, value: d.onTrueValue || undefined });
          }

          const falseTarget = falseBranch.get(node.id);
          if (falseTarget) {
            const idx = branches.findIndex((branch) => branch.condition === 'default');
            if (idx >= 0) branches[idx] = { ...branches[idx]!, goto: falseTarget };
            else branches.push({ condition: 'default', goto: falseTarget, value: d.onFalseValue || undefined });
          }

          return branches
            .map(normalizeConditionalBranch)
            .filter((branch) => branch.goto);
        })()
      : [];
    const flatBranches = flattenConditionalBranches(conditionalBranches);
    const base: TaskDefinition = {
      id: d.taskId,
      name: d.name,
      description: d.description,
      type: st,
      agent_hints: d.agentHints,
      skills: d.skills,
      depends_on: depsMap.get(node.id) || [],
      position: normalizeNodePosition(node.position),
      condition: st === 'conditional' ? flatBranches.condition : undefined,
      on_true: st === 'conditional' ? flatBranches.on_true : undefined,
      on_false: st === 'conditional' ? flatBranches.on_false : undefined,
      conditional_branches: st === 'conditional' && conditionalBranches.length > 0
        ? conditionalBranches
        : undefined,
      on_true_value: st === 'conditional' ? flatBranches.on_true_value : undefined,
      on_false_value: st === 'conditional' ? flatBranches.on_false_value : undefined,
      channel: st === 'human_input' && d.channel
        ? d.channel as TaskDefinition['channel']
        : undefined,
      channels: st === 'notify' && d.channels.length > 0
        ? dedupChannels(d.channels)
        : undefined,
      notify_message: st === 'notify' && d.notifyMessage ? d.notifyMessage : undefined,
      notify_title: st === 'notify' && d.notifyTitle ? d.notifyTitle : undefined,
      notify_channel_id: st === 'notify' && d.notifyChannelId ? d.notifyChannelId : undefined,
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
      if (d.agentMaxTurns && d.agentMaxTurns !== 3) base.agent_max_turns = d.agentMaxTurns;
      if (d.agentTools && d.agentTools !== 'none') base.agent_tools = d.agentTools;
      if (d.agentRequiredTools?.length) base.agent_required_tools = d.agentRequiredTools;
      if (d.agentOutputFields?.length) base.agent_output_fields = d.agentOutputFields;
      if (d.agentQualityEnabled) {
        base.agent_quality_enabled = true;
        base.agent_quality_threshold = d.agentQualityThreshold || 0.7;
        if (d.agentQualityCriteria?.length) base.agent_quality_criteria = d.agentQualityCriteria;
        if (d.agentQualityModel) base.agent_quality_model = d.agentQualityModel;
      }
    }
    if (st === 'parallel') {
      base.parallel_join = (d.parallelJoin || 'all') as TaskDefinition['parallel_join'];
      base.parallel_max_concurrency = d.parallelMaxConcurrency || 5;
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
      if (d.supervisorTemplates?.length) base.supervisor_templates = d.supervisorTemplates;
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
      if (d.pythonInterpreter) base.python_interpreter = d.pythonInterpreter;
      if (d.pythonTimeout && d.pythonTimeout !== 60) base.python_timeout = d.pythonTimeout;
      if (d.pythonAllowFailure) base.python_allow_failure = true;
    }
    if (st === 'compute') {
      if (Object.keys(d.computeOutputs).length > 0) base.compute_outputs = d.computeOutputs;
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
      if (d.emailTrackSecretEnv) base.email_track_secret_env = d.emailTrackSecretEnv;
      if (d.emailTrackBaseUrl) base.email_track_base_url = d.emailTrackBaseUrl;
      if (d.emailSmtpHost) base.email_smtp_host = d.emailSmtpHost;
      if (d.emailSmtpPort) base.email_smtp_port = d.emailSmtpPort;
      if (d.emailSmtpTls === false) base.email_smtp_tls = false;
      if (d.emailSmtpUsername) base.email_smtp_username = d.emailSmtpUsername;
      if (d.emailSmtpPasswordEnv) base.email_smtp_password_env = d.emailSmtpPasswordEnv;
      if (d.emailDryRun) base.email_dry_run = true;
      if (d.emailTimeout && d.emailTimeout !== 30) base.email_timeout = d.emailTimeout;
    }
    if (st === 'image') {
      if (d.imagePrompt) base.image_prompt = d.imagePrompt;
      if (d.imageCount && d.imageCount !== 1) base.image_count = d.imageCount;
      if (d.imageCanvasTarget) base.image_canvas = d.imageCanvasTarget;
      if (d.imageCanvas === false) base.image_canvas = false;
      if (d.imageRegisterArtifact === false) base.image_register_artifact = false;
      if (d.imageSpace) base.image_space = d.imageSpace;
      if (d.imageItemName) base.image_item_name = d.imageItemName;
      if (d.imageOutputPath) base.image_output_path = d.imageOutputPath;
      if (d.imageOutputPattern) base.image_output_pattern = d.imageOutputPattern;
      if (Array.isArray(d.imageInputImages) && d.imageInputImages.length > 0) {
        base.image_input_images = d.imageInputImages;
      }
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
      if (d.entityMetadataTarget && d.entityMetadataTarget !== 'item') {
        base.metadata_target = d.entityMetadataTarget;
      }
      if (d.artifactSummaryModel) base.artifact_summary_model = d.artifactSummaryModel;
    }
    if (st === 'handoff') {
      if (d.handoffFrom) base.handoff_from = d.handoffFrom;
      if (d.handoffTo) base.handoff_to = d.handoffTo;
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
      if (d.humanApprovalOnRejectGoto) base.human_approval_on_reject_goto = d.humanApprovalOnRejectGoto;
      if (d.humanApprovalOnRejectMax && d.humanApprovalOnRejectMax !== 3) base.human_approval_on_reject_max = d.humanApprovalOnRejectMax;
      if (d.humanApprovalApproveKeywords?.length) base.human_approval_approve_keywords = d.humanApprovalApproveKeywords;
      if (d.humanApprovalRejectKeywords?.length) base.human_approval_reject_keywords = d.humanApprovalRejectKeywords;
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
      if (d.resolveArtifactName) base.resolve_artifact_name = d.resolveArtifactName;
      if (d.resolveMode) base.resolve_mode = d.resolveMode;
      if (d.resolveFields?.length) base.resolve_fields = d.resolveFields;
      if (d.resolveMetadataSource && d.resolveMetadataSource !== 'revision') {
        base.resolve_metadata_source = d.resolveMetadataSource;
      }
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
    if (st === 'manus') {
      if (d.manusPrompt) base.manus_prompt = d.manusPrompt;
      if (d.manusStructuredOutputSchema) base.manus_structured_output_schema = d.manusStructuredOutputSchema;
      if (d.manusConnectors?.length) base.manus_connectors = d.manusConnectors;
      if (d.manusEnableSkills?.length) base.manus_enable_skills = d.manusEnableSkills;
      if (d.manusForceSkills?.length) base.manus_force_skills = d.manusForceSkills;
      if (d.manusAgentProfile) base.manus_agent_profile = d.manusAgentProfile;
      if (d.manusLocale) base.manus_locale = d.manusLocale;
      if (d.manusProjectId) base.manus_project_id = d.manusProjectId;
      if (d.manusTitle) base.manus_title = d.manusTitle;
      if (d.manusTimeoutSeconds && d.manusTimeoutSeconds !== 600) base.manus_timeout_seconds = d.manusTimeoutSeconds;
      if (d.manusPollIntervalSeconds && d.manusPollIntervalSeconds !== 5) base.manus_poll_interval_seconds = d.manusPollIntervalSeconds;
      if (d.manusAllowFailure) base.manus_allow_failure = true;
      if (d.manusCredentialsRef) base.manus_credentials_ref = d.manusCredentialsRef;
      // register_output round-trip: emission is gated by the canonical
      // `manusRegisterEnabled` flag — the user-facing checkbox. Empty
      // entity_name / entity_kind still emit (the runtime fail-fasts at
      // registration time with register_output_error so users see the
      // error and know to fill them in). Empty optional fields (space,
      // tag default, etc.) are omitted as before.
      if (d.manusRegisterEnabled === true) {
        base.manus_register_enabled = true;
        base.manus_register_entity_name = d.manusRegisterEntityName || '';
        base.manus_register_entity_kind = d.manusRegisterEntityKind || '';
        if (d.manusRegisterEntityTag && d.manusRegisterEntityTag !== 'published') {
          base.manus_register_entity_tag = d.manusRegisterEntityTag;
        }
        if (d.manusRegisterEntitySpace) {
          base.manus_register_entity_space = d.manusRegisterEntitySpace;
        }
        if (d.manusRegisterAttachments === false) {
          base.manus_register_attachments = false;
        }
        if (d.manusRegisterContentSource && d.manusRegisterContentSource !== 'message') {
          base.manus_register_content_source = d.manusRegisterContentSource;
        }
      }
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
      if (t.onSpace) lines.push(`    on_space: ${yamlEscape(t.onSpace)}`);
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
  const workflowInputs = (meta?.inputs ?? []).filter((inp) => inp.name.trim());
  if (workflowInputs.length > 0) {
    lines.push('');
    lines.push('inputs:');
    for (const inp of workflowInputs) {
      lines.push(`  - name: ${inp.name.trim()}`);
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
    if (task.position) {
      lines.push(`    position:`);
      lines.push(`      x: ${roundPositionCoordinate(task.position.x)}`);
      lines.push(`      y: ${roundPositionCoordinate(task.position.y)}`);
    }
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
      for (const branch of normalizeConditionalBranches(task)) {
        if (!branch.condition || !branch.goto) continue;
        branches.push(`      - condition: ${yamlEscape(branch.condition)}`);
        branches.push(`        goto: ${branch.goto}`);
        if (branch.value) {
          branches.push(`        value: ${yamlEscape(branch.value)}`);
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
    if (
      stepType === 'notify'
      && ((task.channels && task.channels.length > 0) || task.notify_message || task.notify_title || task.notify_channel_id)
    ) {
      lines.push(`    notify:`);
      lines.push(`      channels: [${dedupChannels(task.channels || ['dashboard']).join(', ')}]`);
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
      if (task.notify_channel_id) lines.push(`      channel_id: ${yamlEscape(task.notify_channel_id)}`);
    }
    // Executor-specific nested blocks
    if (
      stepType === 'agent'
      && (
        task.agent_type
        || task.role
        || task.prompt
        || task.template
        || task.auth
        || task.agent_required_tools?.length
      )
    ) {
      lines.push(`    agent:`);
      if (task.agent_type) lines.push(`      agent_type: ${task.agent_type}`);
      if (task.role) lines.push(`      role: ${task.role}`);
      // Persona binding ONLY from task.template (set by Architect's persona
      // discovery or hand-edited YAML). task.assign — the AgentPicker pool-agent
      // binding — emits separately as a top-level `assign:` key below so the
      // round-trip preserves the AgentPicker (green chip) vs Persona distinction.
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
      if (task.agent_max_turns && task.agent_max_turns !== 3) lines.push(`      max_turns: ${task.agent_max_turns}`);
      if (task.agent_tools && task.agent_tools !== 'none') lines.push(`      tools: ${task.agent_tools}`);
      if (task.agent_required_tools?.length) {
        lines.push(`      required_tools: [${task.agent_required_tools.map(yamlEscape).join(', ')}]`);
      }
      if (task.agent_output_fields?.length) {
        lines.push(`      output_fields: [${task.agent_output_fields.map(yamlEscape).join(', ')}]`);
      }
      if (task.agent_quality_enabled) {
        lines.push(`      quality_check:`);
        lines.push(`        enabled: true`);
        if (task.agent_quality_threshold !== undefined && task.agent_quality_threshold !== 0.7) {
          lines.push(`        threshold: ${task.agent_quality_threshold}`);
        }
        if (task.agent_quality_criteria?.length) {
          lines.push(`        criteria: [${task.agent_quality_criteria.map(yamlEscape).join(', ')}]`);
        }
        if (task.agent_quality_model) lines.push(`        model: ${task.agent_quality_model}`);
      }
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
      if (task.supervisor_templates?.length) {
        lines.push(`      templates: [${task.supervisor_templates.map(yamlEscape).join(', ')}]`);
      }
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
          for (const cl of task.python_code.split('\n')) lines.push(`        ${cl}`);
        } else {
          lines.push(`      code: ${yamlEscape(task.python_code)}`);
        }
      }
      if (task.python_args) lines.push(`      args: ${task.python_args}`);
      if (task.python_interpreter) lines.push(`      python: ${yamlEscape(task.python_interpreter)}`);
      if (task.python_timeout && task.python_timeout !== 60) lines.push(`      timeout: ${task.python_timeout}`);
      if (task.python_allow_failure) lines.push(`      allow_failure: true`);
      if (task.auth) lines.push(`      auth: ${yamlEscape(task.auth)}`);
    }
    if (stepType === 'compute') {
      lines.push(`    compute:`);
      lines.push(`      outputs:`);
      for (const [key, value] of Object.entries(task.compute_outputs || {})) {
        lines.push(`        ${key}: ${yamlEscape(String(value))}`);
      }
    }
    if (stepType === 'email') {
      lines.push(`    email:`);
      if (task.email_to) lines.push(`      to: ${yamlEscape(task.email_to)}`);
      if (task.email_subject) lines.push(`      subject: ${yamlEscape(task.email_subject)}`);
      if (task.email_body) {
        if (task.email_body.includes('\n')) {
          lines.push(`      body: |`);
          for (const bl of task.email_body.split('\n')) lines.push(`        ${bl}`);
        } else {
          lines.push(`      body: ${yamlEscape(task.email_body)}`);
        }
      }
      if (task.email_body_html) {
        if (task.email_body_html.includes('\n')) {
          lines.push(`      body_html: |`);
          for (const hl of task.email_body_html.split('\n')) lines.push(`        ${hl}`);
        } else {
          lines.push(`      body_html: ${yamlEscape(task.email_body_html)}`);
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
      if (task.email_track_secret_env) lines.push(`      track_secret_env: ${yamlEscape(task.email_track_secret_env)}`);
      if (task.email_track_base_url) lines.push(`      track_base_url: ${yamlEscape(task.email_track_base_url)}`);
      if (task.email_smtp_host) lines.push(`      smtp_host: ${yamlEscape(task.email_smtp_host)}`);
      if (task.email_smtp_port) lines.push(`      smtp_port: ${task.email_smtp_port}`);
      if (task.email_smtp_tls === false) lines.push(`      smtp_tls: false`);
      if (task.email_smtp_username) lines.push(`      smtp_username: ${yamlEscape(task.email_smtp_username)}`);
      if (task.email_smtp_password_env) lines.push(`      smtp_password_env: ${yamlEscape(task.email_smtp_password_env)}`);
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
      if (typeof task.image_canvas === 'string' && task.image_canvas) lines.push(`      canvas: ${yamlEscape(task.image_canvas)}`);
      else if (task.image_canvas_target) lines.push(`      canvas: ${yamlEscape(task.image_canvas_target)}`);
      else if (task.image_canvas === false) lines.push(`      canvas: false`);
      if (task.image_register_artifact === false) lines.push(`      register_artifact: false`);
      if (task.image_space) lines.push(`      space: ${yamlEscape(task.image_space)}`);
      if (task.image_item_name) lines.push(`      item_name: ${yamlEscape(task.image_item_name)}`);
      if (task.image_output_path) lines.push(`      output_path: ${yamlEscape(task.image_output_path)}`);
      if (task.image_output_pattern) lines.push(`      output_pattern: ${yamlEscape(task.image_output_pattern)}`);
      if (task.image_input_images && task.image_input_images.length > 0) {
        lines.push(`      input_images: [${task.image_input_images.map(yamlEscape).join(', ')}]`);
      }
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
      if (task.artifact_summary_model) {
        lines.push(`      artifact_summary_model: ${yamlEscape(task.artifact_summary_model)}`);
      }
      if (task.metadata_target && task.metadata_target !== 'item') {
        lines.push(`      metadata_target: ${yamlEscape(task.metadata_target)}`);
      }
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
      if (task.human_approval_on_reject_goto) lines.push(`      on_reject_goto: ${task.human_approval_on_reject_goto}`);
      if (task.human_approval_on_reject_max && task.human_approval_on_reject_max !== 3) {
        lines.push(`      on_reject_max: ${task.human_approval_on_reject_max}`);
      }
      if (task.human_approval_approve_keywords?.length) {
        lines.push(`      approve_keywords: [${task.human_approval_approve_keywords.map(yamlEscape).join(', ')}]`);
      }
      if (task.human_approval_reject_keywords?.length) {
        lines.push(`      reject_keywords: [${task.human_approval_reject_keywords.map(yamlEscape).join(', ')}]`);
      }
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
      if (task.resolve_artifact_name) {
        lines.push(`      artifact_name: ${yamlEscape(task.resolve_artifact_name)}`);
      }
      lines.push(`      mode: "${task.resolve_mode || 'latest'}"`);
      if (task.resolve_metadata_source && task.resolve_metadata_source !== 'revision') {
        lines.push(`      metadata_source: ${yamlEscape(task.resolve_metadata_source)}`);
      }
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
    if (stepType === 'manus') {
      lines.push(`    manus:`);
      if (task.manus_prompt) {
        if (task.manus_prompt.includes('\n')) {
          lines.push(`      prompt: |`);
          for (const pl of task.manus_prompt.split('\n')) lines.push(`        ${pl}`);
        } else {
          lines.push(`      prompt: ${yamlEscape(task.manus_prompt)}`);
        }
      }
      if (task.manus_structured_output_schema) {
        // The textarea holds a JSON string. Try to parse and re-emit as
        // a YAML inline map so the file stays valid YAML; fall back to
        // a quoted string if parsing fails (the executor handles both
        // — pydantic accepts either, and a malformed JSON schema would
        // fail the validator anyway).
        try {
          const parsed = JSON.parse(task.manus_structured_output_schema);
          lines.push(`      structured_output_schema: ${JSON.stringify(parsed)}`);
        } catch {
          lines.push(`      structured_output_schema: ${yamlEscape(task.manus_structured_output_schema)}`);
        }
      }
      if (task.manus_connectors?.length) {
        lines.push(`      connectors: [${task.manus_connectors.map(yamlEscape).join(', ')}]`);
      }
      if (task.manus_enable_skills?.length) {
        lines.push(`      enable_skills: [${task.manus_enable_skills.map(yamlEscape).join(', ')}]`);
      }
      if (task.manus_force_skills?.length) {
        lines.push(`      force_skills: [${task.manus_force_skills.map(yamlEscape).join(', ')}]`);
      }
      if (task.manus_agent_profile) lines.push(`      agent_profile: ${yamlEscape(task.manus_agent_profile)}`);
      if (task.manus_locale) lines.push(`      locale: ${yamlEscape(task.manus_locale)}`);
      if (task.manus_project_id) lines.push(`      project_id: ${yamlEscape(task.manus_project_id)}`);
      if (task.manus_title) lines.push(`      title: ${yamlEscape(task.manus_title)}`);
      if (task.manus_timeout_seconds && task.manus_timeout_seconds !== 600) {
        lines.push(`      timeout_seconds: ${task.manus_timeout_seconds}`);
      }
      if (task.manus_poll_interval_seconds && task.manus_poll_interval_seconds !== 5) {
        lines.push(`      poll_interval_seconds: ${task.manus_poll_interval_seconds}`);
      }
      if (task.manus_allow_failure) lines.push(`      allow_failure: true`);
      if (task.manus_credentials_ref) lines.push(`      credentials_ref: ${yamlEscape(task.manus_credentials_ref)}`);
      // register_output — emission is gated by the canonical
      // `manus_register_enabled` flag. Empty entity_name / entity_kind
      // still emit (the runtime fail-fasts so the user sees the error);
      // empty optional fields are omitted.
      if (task.manus_register_enabled === true) {
        lines.push(`      register_output:`);
        lines.push(`        entity_name: ${yamlEscape(task.manus_register_entity_name || '')}`);
        lines.push(`        entity_kind: ${yamlEscape(task.manus_register_entity_kind || '')}`);
        if (task.manus_register_entity_tag && task.manus_register_entity_tag !== 'published') {
          lines.push(`        entity_tag: ${yamlEscape(task.manus_register_entity_tag)}`);
        }
        if (task.manus_register_entity_space) {
          lines.push(`        entity_space: ${yamlEscape(task.manus_register_entity_space)}`);
        }
        if (task.manus_register_attachments === false) {
          lines.push(`        register_attachments: false`);
        }
        if (task.manus_register_content_source && task.manus_register_content_source !== 'message') {
          lines.push(`        content_source: ${yamlEscape(task.manus_register_content_source)}`);
        }
      }
    }
    if (task.agent_hints.length > 0) {
      lines.push(`    agent_hints: [${task.agent_hints.join(', ')}]`);
    }
    if (task.skills.length > 0) {
      lines.push(`    skills: [${task.skills.join(', ')}]`);
    }
    // Top-level `assign:` is the AgentPicker's pool-agent binding. It applies
    // to agent steps too — emitted alongside `agent.template:` so the two
    // round-trip independently.
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
