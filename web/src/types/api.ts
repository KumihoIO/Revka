export interface StatusResponse {
  provider: string | null;
  model: string;
  temperature: number;
  uptime_seconds: number;
  gateway_port: number;
  locale: string;
  memory_backend: string;
  paired: boolean;
  channels: Record<string, boolean>;
  health: HealthSnapshot;
}

export interface HealthSnapshot {
  pid: number;
  updated_at: string;
  uptime_seconds: number;
  components: Record<string, ComponentHealth>;
}

export interface ComponentHealth {
  status: string;
  updated_at: string;
  last_ok: string | null;
  last_error: string | null;
  restart_count: number;
}

export interface ToolSpec {
  name: string;
  description: string;
  parameters: any;
}

export interface CronJob {
  id: string;
  name: string | null;
  expression: string;
  command: string;
  prompt: string | null;
  job_type: string;
  schedule: unknown;
  enabled: boolean;
  delivery: unknown;
  delete_after_run: boolean;
  created_at: string;
  next_run: string;
  last_run: string | null;
  last_status: string | null;
  last_output: string | null;
}

export interface CronRun {
  id: number;
  job_id: string;
  started_at: string;
  finished_at: string;
  status: string;
  output: string | null;
  duration_ms: number | null;
}

export interface Integration {
  name: string;
  description: string;
  category: string;
  status: 'Available' | 'Active' | 'ComingSoon';
}

export interface DiagResult {
  severity: 'ok' | 'warn' | 'error';
  category: string;
  message: string;
}

export interface CostSummary {
  session_cost_usd: number;
  daily_cost_usd: number;
  monthly_cost_usd: number;
  total_tokens: number;
  request_count: number;
  by_model: Record<string, ModelStats>;
}

export interface ModelStats {
  model: string;
  cost_usd: number;
  total_tokens: number;
  request_count: number;
}

export interface AuditEvent {
  timestamp: string;
  event_id: string;
  event_type: string;
  actor?: {
    channel: string;
    user_id?: string;
    username?: string;
  };
  action?: {
    command?: string;
    risk_level?: string;
    approved: boolean;
    allowed: boolean;
  };
  result?: {
    success: boolean;
    exit_code?: number;
    duration_ms?: number;
    error?: string;
  };
  security: {
    policy_violation: boolean;
    rate_limit_remaining?: number;
    sandbox_backend?: string;
  };
  sequence: number;
  entry_hash: string;
}

export interface AuditResponse {
  events: AuditEvent[];
  count: number;
  audit_enabled: boolean;
}

export interface AuditVerifyResponse {
  verified: boolean;
  entry_count?: number;
  error?: string;
}

export interface CliTool {
  name: string;
  path: string;
  version: string | null;
  category: string;
}

export interface Session {
  id: string;
  channel: string;
  started_at: string;
  last_activity: string;
  status: 'active' | 'idle' | 'closed';
  message_count: number;
}

export interface ChannelDetail {
  name: string;
  type: string;
  enabled: boolean;
  status: 'active' | 'inactive' | 'error';
  message_count: number;
  last_message_at: string | null;
  health: 'healthy' | 'degraded' | 'down';
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export interface AgentDefinition {
  kref: string;
  name: string;
  item_name: string;
  deprecated: boolean;
  created_at: string;
  metadata: Record<string, string>;
  // Populated from latest revision metadata:
  identity: string;        // Who is this agent? (e.g., "Senior Rust engineer")
  soul: string;            // Core personality/philosophy
  expertise: string[];     // Focused areas (e.g., ["rust", "systems", "performance"])
  tone: string;            // Communication style (e.g., "concise and direct")
  role: string;            // "coder" | "reviewer" | "researcher"
  agent_type: string;      // "claude" | "codex"
  model: string;           // e.g. "claude-opus-4-6", "claude-sonnet-4-6", "gpt-5.4"
  system_hint: string;     // Extra prompt context
  revision_number: number; // Latest revision number
}

export interface AgentCreateRequest {
  name: string;
  identity: string;
  soul: string;
  expertise: string[];
  tone: string;
  role: string;
  agent_type: string;
  model?: string;
  system_hint?: string;
}

export interface AgentUpdateRequest extends AgentCreateRequest {
  kref: string;
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export interface SkillDefinition {
  kref: string;
  name: string;
  item_name: string;
  deprecated: boolean;
  created_at: string;
  description: string;
  content: string;
  domain: string;
  tags: string[];
  revision_number: number;
}

export interface SkillCreateRequest {
  name: string;
  description: string;
  content: string;
  domain: string;
  tags?: string[];
}

export interface SkillUpdateRequest extends SkillCreateRequest {
  kref: string;
}

// ── ClawHub Marketplace ──

export interface ClawHubSkill {
  slug: string;
  name?: string;
  displayName?: string;
  description: string;
  version: string;
  author?: string;
  downloads?: number;
  stars?: number;
  verified?: boolean;
  updatedAt?: string;
  skill_md?: string;
  tags?: string[];
}

export interface ClawHubSearchResult {
  score: number;
  slug: string;
  displayName?: string;
  name?: string;
  description: string;
  version: string;
  updatedAt?: string;
  downloads?: number;
  stars?: number;
  verified?: boolean;
}

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------

export interface TeamEdge {
  from_kref: string;
  to_kref: string;
  edge_type: 'REPORTS_TO' | 'SUPPORTS' | 'DEPENDS_ON';
}

export interface TeamMember {
  kref: string;
  name: string;
  role: string;
  agent_type: string;
  model?: string;
  identity: string;
  expertise: string[];
}

export interface TeamDefinition {
  kref: string;
  name: string;
  description: string;
  deprecated: boolean;
  created_at: string;
  members: TeamMember[];
  edges: TeamEdge[];
  /** Summary fields from bundle metadata (list view — avoids enrichment). */
  member_count?: number;
  member_names?: string[];
  edge_count?: number;
}

export interface TeamCreateRequest {
  name: string;
  description?: string;
  members: string[];  // agent krefs
  edges: TeamEdge[];
}

export interface TeamUpdateRequest extends TeamCreateRequest {
  kref: string;
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------

export interface WorkflowDefinition {
  kref: string;
  name: string;
  item_name: string;
  deprecated: boolean;
  created_at: string | null;
  description: string;
  definition: string;
  version: string;
  tags: string[];
  steps: number;
  revision_number: number;
  source?: string; // "builtin" | "builtin-modified" | "custom"
}

export interface WorkflowCreateRequest {
  name: string;
  description: string;
  definition: string;
  version?: string;
  tags?: string[];
}

export interface WorkflowUpdateRequest extends WorkflowCreateRequest {
  kref: string;
}

export interface WorkflowRunSummary {
  kref: string;
  run_id: string;
  workflow_name: string;
  status: string;
  started_at: string;
  completed_at: string;
  steps_completed: string;
  steps_total: string;
  error: string;
  workflow_item_kref?: string;
  workflow_revision_kref?: string;
}

export interface TranscriptEntry {
  speaker: string;
  content: string;
  round: number;
}

export interface WorkflowStepDetail {
  step_id: string;
  status: string;
  agent_id?: string;
  agent_type?: string;
  role?: string;
  template_name?: string;
  output_preview?: string;
  /** Absolute filesystem path to the step's full output artifact on disk */
  artifact_path?: string;
  skills?: string[];
  transcript?: TranscriptEntry[];
  /** Set when the step is a human_approval step waiting for a decision */
  output_data?: {
    awaiting_approval?: boolean;
    approval_message?: string;
    approve_keywords?: string[];
    reject_keywords?: string[];
  };
}

export interface WorkflowRunDetail extends WorkflowRunSummary {
  steps: WorkflowStepDetail[];
}

export interface WorkflowDashboard {
  definitions_count: number;
  definitions: WorkflowDefinition[];
  active_runs: number;
  recent_runs: WorkflowRunSummary[];
  total_runs: number;
}

// ---------------------------------------------------------------------------
// Architect (workflow revision) — mirrors operator-mcp `revise_workflow`.
// ---------------------------------------------------------------------------

/** Operation kinds the `revise_workflow` MCP tool understands. Mirrors
 *  `RevisionOpType` in operator-mcp/operator_mcp/tool_handlers/workflow_revisions.py. */
export type RevisionOpKind =
  | 'add_step'
  | 'edit_step'
  | 'delete_step'
  | 'reorder'
  | 'wire'
  | 'unwire'
  | 'insert_into_parallel'
  | 'extract_from_parallel'
  | 'rename_step';

/** A single revision operation. Fields are op-specific; the tool ignores
 *  fields irrelevant to the chosen `op`. Mirrors `RevisionOp` in
 *  operator-mcp. */
export interface RevisionOperation {
  op: RevisionOpKind;
  step_id?: string;
  new_id?: string;
  step_def?: Record<string, unknown>;
  target_step_id?: string;
  parallel_id?: string;
  position?: number;
  position_after?: string;
}

/** Reasons the tool can skip an individual operation. Mirrors
 *  `SkippedReason` in operator-mcp. */
export type SkippedReason =
  | 'step_not_found'
  | 'duplicate_id'
  | 'invalid_yaml'
  | 'missing_required_field'
  | 'cycle_detected'
  | 'unknown_step_type'
  | 'reference_broken'
  | 'invalid_position'
  | 'parallel_not_found'
  | 'validation_failed';

export interface SkippedItem {
  op_index: number;
  op: RevisionOpKind;
  reason: SkippedReason;
  details: string;
  target_step_id?: string | null;
}

export interface ReviseWorkflowResponse {
  success: boolean;
  new_revision_kref: string | null;
  applied_count: number;
  skipped_items: SkippedItem[];
  errors: string[];
}

/** Mirrors `RevisionSummary` in `src/gateway/api_architect.rs`. */
export interface RevisionSummary {
  /** Includes `?r=N` suffix. */
  kref: string;
  number: number;
  created_at: string | null;
  /** Includes `"published"` if currently published. */
  tags: string[];
  /** May include `"rationale": "..."` keys. Tolerate either presence or absence. */
  metadata: Record<string, string>;
}

export interface RevisionListResponse {
  revisions: RevisionSummary[];
}

// ---------------------------------------------------------------------------
// Asset Browser (Kumiho)
// ---------------------------------------------------------------------------

export interface KumihoProject {
  name: string;
  description: string;
  deprecated: boolean;
  created_at?: string | null;
  metadata?: Record<string, string>;
}

export interface KumihoSpace {
  path: string;
  name: string;
  type: 'root' | 'sub';
  created_at?: string | null;
  author?: string | null;
  metadata?: Record<string, string>;
}

export interface KumihoItem {
  kref: string;
  name: string;
  item_name: string;
  kind: string;
  deprecated: boolean;
  created_at?: string | null;
  author?: string | null;
  metadata?: Record<string, string>;
}

export interface KumihoRevision {
  kref: string;
  item_kref: string;
  number: number;
  latest: boolean;
  tags: string[];
  deprecated: boolean;
  published?: boolean;
  created_at?: string | null;
  author?: string | null;
  metadata?: Record<string, string>;
}

export interface KumihoArtifact {
  kref: string;
  name: string;
  location: string;
  revision_kref: string;
  item_kref: string;
  deprecated: boolean;
  created_at?: string | null;
  metadata?: Record<string, string>;
}

export interface KumihoEdge {
  source_kref: string;
  target_kref: string;
  edge_type: string;
  created_at?: string | null;
  metadata?: Record<string, string>;
}

export interface KumihoSearchResult {
  item: KumihoItem;
  score: number;
  matched_in?: string[];
}

// ---------------------------------------------------------------------------
// Memory Graph (Obsidian-style visualization)
// ---------------------------------------------------------------------------

export interface MemoryGraphNode {
  id: string;
  name: string;
  kind: string;
  space: string;
  created_at: string | null;
  title: string | null;
  summary: string | null;
  revision_kref: string | null;
}

export interface MemoryGraphEdge {
  source: string;
  target: string;
  edge_type: string;
  metadata?: Record<string, string>;
}

export interface MemoryGraphStats {
  total_items: number;
  total_edges: number;
  kinds: Record<string, number>;
}

export interface MemoryGraphResponse {
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
  spaces: string[];
  stats: MemoryGraphStats;
}

export interface SSEEvent {
  type: string;
  timestamp?: string;
  [key: string]: any;
}

export interface WsMessage {
  type:
    | 'message'
    | 'chunk'
    | 'chunk_reset'
    | 'thinking'
    | 'tool_call'
    | 'tool_result'
    | 'operator_status'
    | 'agent_event'
    | 'done'
    | 'error'
    | 'session_start'
    | 'connected';
  content?: string;
  full_response?: string;
  name?: string;
  args?: any;
  output?: string;
  message?: string;
  code?: string;
  session_id?: string;
  resumed?: boolean;
  message_count?: number;
  /** Operator orchestration status */
  phase?: string;
  detail?: string;
  /** Channel event payload from operator (for agent_event type) */
  event?: AgentChannelEvent;
}

/** Structured channel event relayed from the operator via the gateway. */
export interface AgentChannelEvent {
  type: 'agent.started' | 'agent.completed' | 'agent.error' | 'agent.tool_use' | 'agent.permission' | 'agent.thinking' | 'agent.message' | 'agent.chat';
  agentId: string;
  agentTitle: string;
  content: Record<string, any>;
  timestamp: string;
}

/** Row from GET /api/sessions/{id}/messages */
export interface SessionMessageRow {
  role: string;
  content: string;
}

export interface SessionMessagesResponse {
  session_id: string;
  messages: SessionMessageRow[];
  session_persistence: boolean;
}

/**
 * Metadata-only summary of an entry in the encrypted auth-profile store.
 *
 * Returned from `GET /api/auth/profiles`. **Token bytes are never included.**
 * Editor surfaces (workflow step "Auth" dropdown, lock icon) consume this
 * shape; the runtime fetches the actual decrypted token via the
 * service-token-gated `/api/auth/profiles/{id}/resolve` endpoint.
 */
export interface AuthProfileSummary {
  /** `<provider>:<profile_name>` — used as the value of the YAML `auth:` field. */
  id: string;
  provider: string;
  profile_name: string;
  /** "oauth" or "token". */
  kind: 'oauth' | 'token';
  account_id: string | null;
  workspace_id: string | null;
  /** Only populated for OAuth profiles. ISO 8601. */
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

