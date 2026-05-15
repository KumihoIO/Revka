import type {
  StatusResponse,
  ToolSpec,
  CronJob,
  CronRun,
  Integration,
  DiagResult,
  CostSummary,
  CliTool,
  HealthSnapshot,
  Session,
  ChannelDetail,
  SessionMessagesResponse,
  AgentDefinition,
  AgentCreateRequest,
  AgentUpdateRequest,
  SkillDefinition,
  SkillCreateRequest,
  SkillUpdateRequest,
  TeamDefinition,
  TeamCreateRequest,
  TeamUpdateRequest,
  AuditResponse,
  AuditVerifyResponse,
  ClawHubSkill,
  ClawHubSearchResult,
  WorkflowDefinition,
  WorkflowCreateRequest,
  WorkflowUpdateRequest,
  WorkflowRunSummary,
  WorkflowRunDetail,
  WorkflowDashboard,
  MemoryGraphResponse,
  AuthProfileSummary,
  RevisionOperation,
  ReviseWorkflowResponse,
  RevisionListResponse,
  KumihoArtifact,
  KumihoItem,
  KumihoRevision,
  SkinSummary,
} from '../types/api';
import { clearToken, getToken, setToken } from './auth';
import { apiOrigin, basePath } from './basePath';
import {
  EDITOR_SESSION_HEADER,
  getEditorSessionId,
} from '../construct/components/workflows/sessionId';

// ---------------------------------------------------------------------------
// Base fetch wrapper
// ---------------------------------------------------------------------------

export class UnauthorizedError extends Error {
  constructor() {
    super('Unauthorized');
    this.name = 'UnauthorizedError';
  }
}

// `ApiError` + error-message construction live in a sibling module so they
// stay unit-testable without DOM dependencies. Re-exported here for back-
// compat with the existing `import { ApiError } from './api'` call sites.
export { ApiError, buildApiError, isHtmlErrorBody } from './apiError';

import { buildApiError as _buildApiError } from './apiError';

export async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const token = getToken();
  const headers = new Headers(options.headers);

  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  if (
    options.body &&
    typeof options.body === 'string' &&
    !headers.has('Content-Type')
  ) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${apiOrigin}${basePath}${path}`, { ...options, headers });

  if (response.status === 401) {
    clearToken();
    window.dispatchEvent(new Event('construct-unauthorized'));
    throw new UnauthorizedError();
  }

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    const contentType = response.headers.get('content-type');
    throw _buildApiError(response.status, response.statusText, text, contentType);
  }

  // Some endpoints may return 204 No Content
  if (response.status === 204) {
    return undefined as unknown as T;
  }

  return response.json() as Promise<T>;
}

function unwrapField<T>(value: T | Record<string, T>, key: string): T {
  if (value !== null && typeof value === 'object' && !Array.isArray(value) && key in value) {
    const unwrapped = (value as Record<string, T | undefined>)[key];
    if (unwrapped !== undefined) {
      return unwrapped;
    }
  }
  return value as T;
}

// ---------------------------------------------------------------------------
// Pairing
// ---------------------------------------------------------------------------

export async function pair(code: string): Promise<{ token: string }> {
  const response = await fetch(`${basePath}/pair`, {
    method: 'POST',
    headers: { 'X-Pairing-Code': code },
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Pairing failed (${response.status}): ${text || response.statusText}`);
  }

  const data = (await response.json()) as { token: string };
  setToken(data.token);
  return data;
}

export async function getAdminPairCode(): Promise<{ pairing_code: string | null; pairing_required: boolean }> {
  // Use the public /pair/code endpoint which works in Docker and remote environments
  // (no localhost restriction). Falls back to the admin endpoint for backward compat.
  const publicResp = await fetch(`${basePath}/pair/code`);
  if (publicResp.ok) {
    return publicResp.json() as Promise<{ pairing_code: string | null; pairing_required: boolean }>;
  }

  const response = await fetch('/admin/paircode');
  if (!response.ok) {
    throw new Error(`Failed to fetch pairing code (${response.status})`);
  }
  return response.json() as Promise<{ pairing_code: string | null; pairing_required: boolean }>;
}

// ---------------------------------------------------------------------------
// Public health (no auth required)
// ---------------------------------------------------------------------------

export async function getPublicHealth(): Promise<{ require_pairing: boolean; paired: boolean }> {
  const response = await fetch(`${basePath}/health`);
  if (!response.ok) {
    throw new Error(`Health check failed (${response.status})`);
  }
  return response.json() as Promise<{ require_pairing: boolean; paired: boolean }>;
}

// ---------------------------------------------------------------------------
// Status / Health
// ---------------------------------------------------------------------------

export function getStatus(): Promise<StatusResponse> {
  return apiFetch<StatusResponse>('/api/status');
}

export function getHealth(): Promise<HealthSnapshot> {
  return apiFetch<HealthSnapshot | { health: HealthSnapshot }>('/api/health').then((data) =>
    unwrapField(data, 'health'),
  );
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

export function getConfig(): Promise<string> {
  return apiFetch<string | { format?: string; content: string }>('/api/config').then((data) =>
    typeof data === 'string' ? data : data.content,
  );
}

export function putConfig(toml: string): Promise<void> {
  return apiFetch<void>('/api/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/toml' },
    body: toml,
  });
}

// ---------------------------------------------------------------------------
// UI Skins
// ---------------------------------------------------------------------------

export function getSkins(): Promise<SkinSummary[]> {
  return apiFetch<SkinSummary[] | { skins: SkinSummary[] }>('/api/skins').then((data) =>
    unwrapField(data, 'skins'),
  );
}

export function importSkinZip(file: File): Promise<SkinSummary> {
  return apiFetch<SkinSummary | { skin: SkinSummary }>('/api/skins/import', {
    method: 'POST',
    headers: { 'Content-Type': file.type || 'application/zip' },
    body: file,
  }).then((data) => unwrapField(data, 'skin'));
}

export function deleteSkin(id: string): Promise<void> {
  return apiFetch<void>(`/api/skins/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

export function getTools(): Promise<ToolSpec[]> {
  return apiFetch<ToolSpec[] | { tools: ToolSpec[] }>('/api/tools').then((data) =>
    unwrapField(data, 'tools'),
  );
}

// ---------------------------------------------------------------------------
// Cron
// ---------------------------------------------------------------------------

export function getCronJobs(): Promise<CronJob[]> {
  return apiFetch<CronJob[] | { jobs: CronJob[] }>('/api/cron').then((data) =>
    unwrapField(data, 'jobs'),
  );
}

export function addCronJob(body: {
  name?: string;
  command: string;
  schedule: string;
  enabled?: boolean;
}): Promise<CronJob> {
  return apiFetch<CronJob | { status: string; job: CronJob }>('/api/cron', {
    method: 'POST',
    body: JSON.stringify(body),
  }).then((data) => (typeof (data as { job?: CronJob }).job === 'object' ? (data as { job: CronJob }).job : (data as CronJob)));
}

export function deleteCronJob(id: string): Promise<void> {
  return apiFetch<void>(`/api/cron/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}
export function patchCronJob(
  id: string,
  patch: { name?: string; schedule?: string; command?: string; enabled?: boolean },
): Promise<CronJob> {
  return apiFetch<CronJob | { status: string; job: CronJob }>(
    `/api/cron/${encodeURIComponent(id)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(patch),
    },
  ).then((data) => (typeof (data as { job?: CronJob }).job === 'object' ? (data as { job: CronJob }).job : (data as CronJob)));
}


export function getCronRuns(
  jobId: string,
  limit: number = 20,
): Promise<CronRun[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  return apiFetch<CronRun[] | { runs: CronRun[] }>(
    `/api/cron/${encodeURIComponent(jobId)}/runs?${params}`,
  ).then((data) => unwrapField(data, 'runs'));
}

export interface CronSettings {
  enabled: boolean;
  catch_up_on_startup: boolean;
  max_run_history: number;
}

export function getCronSettings(): Promise<CronSettings> {
  return apiFetch<CronSettings>('/api/cron/settings');
}

export function patchCronSettings(
  patch: Partial<CronSettings>,
): Promise<CronSettings> {
  return apiFetch<CronSettings & { status: string }>('/api/cron/settings', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

// ---------------------------------------------------------------------------
// Integrations
// ---------------------------------------------------------------------------

export function getIntegrations(): Promise<Integration[]> {
  return apiFetch<Integration[] | { integrations: Integration[] }>('/api/integrations').then(
    (data) => unwrapField(data, 'integrations'),
  );
}

// ---------------------------------------------------------------------------
// MCP server — "Test" handshake from the Config editor.
// ---------------------------------------------------------------------------

export interface McpServerTestRequest {
  name: string;
  transport: 'stdio' | 'http' | 'sse';
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  url?: string;
  headers?: Record<string, string>;
  timeout_ms?: number;
}

export interface McpServerTestResult {
  ok: boolean;
  tool_count?: number;
  tools?: string[];
  latency_ms: number;
  error?: string;
}

export function testMcpServer(req: McpServerTestRequest): Promise<McpServerTestResult> {
  return apiFetch<McpServerTestResult>('/api/mcp/servers/test', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

// ---------------------------------------------------------------------------
// Doctor / Diagnostics
// ---------------------------------------------------------------------------

export function runDoctor(): Promise<DiagResult[]> {
  return apiFetch<DiagResult[] | { results: DiagResult[]; summary?: unknown }>('/api/doctor', {
    method: 'POST',
    body: JSON.stringify({}),
  }).then((data) => (Array.isArray(data) ? data : data.results));
}

// Old memory CRUD (getMemory, storeMemory, deleteMemory) removed — use Kumiho.

// ---------------------------------------------------------------------------
// Cost
// ---------------------------------------------------------------------------

export function getCost(): Promise<CostSummary> {
  return apiFetch<CostSummary | { cost: CostSummary }>('/api/cost').then((data) =>
    unwrapField(data, 'cost'),
  );
}

// ---------------------------------------------------------------------------
// Audit Trail
// ---------------------------------------------------------------------------

export function getAuditEvents(params?: {
  limit?: number;
  event_type?: string;
  since?: string;
}): Promise<AuditResponse> {
  const query = new URLSearchParams();
  if (params?.limit) query.set('limit', String(params.limit));
  if (params?.event_type) query.set('event_type', params.event_type);
  if (params?.since) query.set('since', params.since);
  const qs = query.toString();
  return apiFetch<AuditResponse>(`/api/audit${qs ? `?${qs}` : ''}`);
}

export function verifyAuditChain(): Promise<AuditVerifyResponse> {
  return apiFetch<AuditVerifyResponse>('/api/audit/verify');
}

// ---------------------------------------------------------------------------
// Nodes
// ---------------------------------------------------------------------------

export interface NodeInfo {
  node_id: string;
  capabilities: Array<{ name: string; description: string }>;
  capability_count: number;
}

export interface NodesResponse {
  nodes: NodeInfo[];
  count: number;
}

export function getNodes(): Promise<NodesResponse> {
  return apiFetch<NodesResponse>('/api/nodes');
}

export function invokeNode(
  nodeId: string,
  capability: string,
  args?: Record<string, unknown>,
): Promise<{ call_id: string; success: boolean; output: string; error?: string }> {
  return apiFetch(`/api/nodes/${encodeURIComponent(nodeId)}/invoke`, {
    method: 'POST',
    body: JSON.stringify({ capability, args: args ?? {} }),
  });
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export interface SessionsResponse {
  sessions: Session[];
  archived_session_ids?: string[];
}

export function getSessionsWithArchiveState(): Promise<{
  sessions: Session[];
  archivedSessionIds: string[];
}> {
  return apiFetch<Session[] | SessionsResponse>('/api/sessions').then((data) => {
    if (Array.isArray(data)) {
      return { sessions: data, archivedSessionIds: [] };
    }
    return {
      sessions: data.sessions,
      archivedSessionIds: data.archived_session_ids ?? [],
    };
  });
}

export function getSessions(): Promise<Session[]> {
  return getSessionsWithArchiveState().then((data) => data.sessions);
}

export function getSession(id: string): Promise<Session> {
  return apiFetch<Session>(`/api/sessions/${encodeURIComponent(id)}`);
}

/** Load persisted dashboard WebSocket chat transcript for the dashboard Agent Chat. */
export function getSessionMessages(id: string): Promise<SessionMessagesResponse> {
  return apiFetch<SessionMessagesResponse>(
    `/api/sessions/${encodeURIComponent(id)}/messages`,
  );
}

/** Remove an operator chat session from the active gateway transcript list. */
export function deleteSession(id: string): Promise<{
  deleted: boolean;
  session_id: string;
  archived?: boolean;
  archived_at?: string;
}> {
  return apiFetch<{
    deleted: boolean;
    session_id: string;
    archived?: boolean;
    archived_at?: string;
  }>(
    `/api/sessions/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
  );
}

/** Rename an operator chat session. Persists across daemon restarts. */
export function renameSession(id: string, name: string): Promise<{ session_id: string; name: string }> {
  return apiFetch<{ session_id: string; name: string }>(
    `/api/sessions/${encodeURIComponent(id)}`,
    {
      method: 'PUT',
      body: JSON.stringify({ name }),
    },
  );
}

/** Server response from `POST /api/sessions/{id}/attachments`. */
export interface AttachmentUploadResponse {
  file_id: string;
  filename: string;
  size: number;
  mime: string;
  session_id: string;
  created_at: string;
}

/**
 * Upload a single file to the session's attachment store. Returns the
 * server-issued metadata; the `file_id` field is what gets passed back
 * via the WS `message` payload's `attachments: [...]` array.
 *
 * The gateway caps individual files at 25 MiB. Errors surface as
 * standard `apiFetch` rejections (4xx/5xx → thrown).
 */
export function uploadAttachment(
  sessionId: string,
  file: File,
): Promise<AttachmentUploadResponse> {
  const form = new FormData();
  form.append('file', file, file.name);
  // Don't set Content-Type — the browser provides the multipart boundary.
  return apiFetch<AttachmentUploadResponse>(
    `/api/sessions/${encodeURIComponent(sessionId)}/attachments`,
    { method: 'POST', body: form },
  );
}

// ---------------------------------------------------------------------------
// Channels (detailed)
// ---------------------------------------------------------------------------

export function getChannels(): Promise<ChannelDetail[]> {
  return apiFetch<ChannelDetail[] | { channels: ChannelDetail[] }>('/api/channels').then((data) =>
    unwrapField(data, 'channels'),
  );
}

// ---------------------------------------------------------------------------
// CLI Tools
// ---------------------------------------------------------------------------

export function getCliTools(): Promise<CliTool[]> {
  return apiFetch<CliTool[] | { cli_tools: CliTool[] }>('/api/cli-tools').then((data) =>
    unwrapField(data, 'cli_tools'),
  );
}

// ---------------------------------------------------------------------------
// Agents
// ---------------------------------------------------------------------------

export interface AgentsPage {
  agents: AgentDefinition[];
  total_count: number;
  page: number;
  per_page: number;
}

export async function fetchAgents(
  includeDisabled = false,
  page = 1,
  perPage = 9,
): Promise<AgentsPage> {
  const params = new URLSearchParams();
  if (includeDisabled) params.set('include_deprecated', 'true');
  params.set('page', String(page));
  params.set('per_page', String(perPage));
  return apiFetch<AgentsPage>(`/api/agents?${params}`);
}

export async function createAgent(agent: AgentCreateRequest): Promise<AgentDefinition> {
  return apiFetch<AgentDefinition | { agent: AgentDefinition }>('/api/agents', {
    method: 'POST',
    body: JSON.stringify(agent),
  }).then((data) => unwrapField(data, 'agent'));
}

export async function updateAgent(agent: AgentUpdateRequest): Promise<AgentDefinition> {
  const path = agent.kref.replace(/^kref:\/\//, '');
  return apiFetch<AgentDefinition | { agent: AgentDefinition }>(`/api/agents/${path}`, {
    method: 'PUT',
    body: JSON.stringify(agent),
  }).then((data) => unwrapField(data, 'agent'));
}

export async function toggleAgentDeprecation(kref: string, deprecated: boolean): Promise<AgentDefinition> {
  return apiFetch<AgentDefinition | { agent: AgentDefinition }>('/api/agents/deprecate', {
    method: 'POST',
    body: JSON.stringify({ kref, deprecated }),
  }).then((data) => unwrapField(data, 'agent'));
}

export async function deleteAgent(kref: string): Promise<void> {
  const path = kref.replace(/^kref:\/\//, '');
  return apiFetch<void>(`/api/agents/${path}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Auth profiles (workflow step credential dropdown)
// ---------------------------------------------------------------------------

/** Metadata-only listing — the gateway never returns token bytes here. */
export async function fetchAuthProfiles(): Promise<AuthProfileSummary[]> {
  const data = await apiFetch<{ profiles: AuthProfileSummary[] }>('/api/auth/profiles');
  return data.profiles ?? [];
}

export interface CreateAuthProfileRequest {
  provider: string;
  profile_name: string;
  /** Raw bearer / API key. Encrypted on the gateway before persist. */
  token: string;
  account_id?: string;
  /** "token" by default; OAuth flows must go through /config. */
  kind?: 'token' | 'api_key';
}

/** POST a new static-token auth profile. Returns metadata only — the
 *  response never includes the token bytes. */
export async function createAuthProfile(
  body: CreateAuthProfileRequest,
): Promise<AuthProfileSummary> {
  return apiFetch<AuthProfileSummary | { profile: AuthProfileSummary }>(
    '/api/auth/profiles',
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  ).then((data) => unwrapField(data, 'profile'));
}

/** DELETE a stored auth profile by id. 204 on success, throws ApiError(404)
 *  if the id doesn't exist. */
export async function deleteAuthProfile(id: string): Promise<void> {
  return apiFetch<void>(`/api/auth/profiles/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

// ---------------------------------------------------------------------------
// Skills
// ---------------------------------------------------------------------------

export interface SkillsPage {
  skills: SkillDefinition[];
  total_count: number;
  page: number;
  per_page: number;
}

export async function fetchSkills(
  includeDisabled = false,
  page = 1,
  perPage = 9,
): Promise<SkillsPage> {
  const params = new URLSearchParams();
  if (includeDisabled) params.set('include_deprecated', 'true');
  params.set('page', String(page));
  params.set('per_page', String(perPage));
  return apiFetch<SkillsPage>(`/api/skills?${params}`);
}

export async function fetchSkillDetail(kref: string): Promise<SkillDefinition> {
  const path = kref.replace('kref://', '');
  return apiFetch<{ skill: SkillDefinition }>(`/api/skills/${path}`).then((data) => data.skill);
}

export async function searchSkills(query: string): Promise<SkillDefinition[]> {
  if (!query || query.length < 2) return [];
  const params = new URLSearchParams({ q: query });
  return apiFetch<SkillDefinition[] | { skills: SkillDefinition[] }>(`/api/skills?${params}`).then(
    (data) => unwrapField(data, 'skills'),
  );
}

export async function createSkill(skill: SkillCreateRequest): Promise<SkillDefinition> {
  return apiFetch<SkillDefinition | { skill: SkillDefinition }>('/api/skills', {
    method: 'POST',
    body: JSON.stringify(skill),
  }).then((data) => unwrapField(data, 'skill'));
}

export async function updateSkill(skill: SkillUpdateRequest): Promise<SkillDefinition> {
  const path = skill.kref.replace(/^kref:\/\//, '');
  return apiFetch<SkillDefinition | { skill: SkillDefinition }>(`/api/skills/${path}`, {
    method: 'PUT',
    body: JSON.stringify(skill),
  }).then((data) => unwrapField(data, 'skill'));
}

export async function toggleSkillDeprecation(kref: string, deprecated: boolean): Promise<SkillDefinition> {
  return apiFetch<SkillDefinition | { skill: SkillDefinition }>('/api/skills/deprecate', {
    method: 'POST',
    body: JSON.stringify({ kref, deprecated }),
  }).then((data) => unwrapField(data, 'skill'));
}

export async function deleteSkill(kref: string): Promise<void> {
  const path = kref.replace(/^kref:\/\//, '');
  return apiFetch<void>(`/api/skills/${path}`, { method: 'DELETE' });
}

// ── ClawHub Marketplace ──

function normalizeClawHubResults(items: any[]): ClawHubSearchResult[] {
  return items.map((r) => {
    // Upstream may nest data under `skill`, `latestVersion`, `stats` — flatten it
    const s = r.skill ?? r;
    const lv = r.latestVersion ?? s.latestVersion ?? {};
    const stats = s.stats ?? r.stats ?? {};
    return {
      score: r.score ?? 0,
      slug: s.slug ?? r.slug ?? '',
      displayName: s.displayName ?? s.name ?? r.displayName ?? r.name,
      name: s.name ?? s.displayName ?? r.name ?? r.displayName,
      description: s.description ?? s.summary ?? r.description ?? r.summary ?? '',
      version: lv.version ?? s.version ?? r.version ?? '',
      downloads: stats.downloads ?? stats.installsAllTime ?? r.downloads,
      stars: stats.stars ?? r.stars,
      verified: s.verified ?? r.verified,
      updatedAt: s.updatedAt ?? r.updatedAt,
    };
  });
}

export async function searchClawHub(query: string, limit = 20): Promise<ClawHubSearchResult[]> {
  const params = new URLSearchParams({ q: query, limit: String(limit) });
  return apiFetch<any>(`/api/clawhub/search?${params}`).then((data) => {
    // ClawHub returns different shapes — normalize
    const raw = Array.isArray(data) ? data : data?.results ?? data?.items ?? [];
    return normalizeClawHubResults(raw);
  });
}

export async function fetchClawHubTrending(limit = 20): Promise<ClawHubSearchResult[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  return apiFetch<any>(`/api/clawhub/trending?${params}`).then((data) => {
    const raw = Array.isArray(data) ? data : data?.results ?? data?.items ?? data?.skills ?? [];
    return normalizeClawHubResults(raw);
  });
}

export async function fetchClawHubSkillDetail(slug: string): Promise<ClawHubSkill> {
  return apiFetch<any>(`/api/clawhub/skills/${encodeURIComponent(slug)}`).then((data) => {
    // The upstream returns nested { skill, owner, latestVersion, ... } — flatten it
    const s = data.skill ?? data;
    const owner = data.owner;
    const stats = s.stats ?? {};
    const lv = data.latestVersion ?? {};
    return {
      slug: s.slug ?? slug,
      name: s.name ?? s.displayName,
      displayName: s.displayName ?? s.name,
      description: s.summary ?? s.description ?? '',
      version: lv.version ?? s.version ?? '',
      author: owner?.displayName ?? owner?.handle ?? undefined,
      downloads: stats.downloads ?? stats.installsAllTime ?? undefined,
      stars: stats.stars ?? undefined,
      verified: s.verified ?? undefined,
      updatedAt: s.updatedAt ? new Date(s.updatedAt).toISOString() : undefined,
      skill_md: data.skill_md ?? undefined,
      tags: s.tags ? Object.keys(s.tags) : undefined,
    } as ClawHubSkill;
  });
}

export async function installClawHubSkill(slug: string): Promise<{ installed: boolean; name: string; kref: string }> {
  return apiFetch<{ installed: boolean; name: string; kref: string }>(`/api/clawhub/install/${encodeURIComponent(slug)}`, {
    method: 'POST',
  });
}

// ---------------------------------------------------------------------------
// Workflows
// ---------------------------------------------------------------------------

export async function fetchWorkflows(includeDisabled = false): Promise<WorkflowDefinition[]> {
  const params = new URLSearchParams();
  if (includeDisabled) params.set('include_deprecated', 'true');
  return apiFetch<WorkflowDefinition[] | { workflows: WorkflowDefinition[] }>(
    `/api/workflows?${params}`
  ).then((data) => unwrapField(data, 'workflows'));
}

export async function createWorkflow(workflow: WorkflowCreateRequest): Promise<WorkflowDefinition> {
  return apiFetch<WorkflowDefinition | { workflow: WorkflowDefinition }>('/api/workflows', {
    method: 'POST',
    body: JSON.stringify(workflow),
    headers: { [EDITOR_SESSION_HEADER]: getEditorSessionId() },
  }).then((data) => unwrapField(data, 'workflow'));
}

export async function updateWorkflow(workflow: WorkflowUpdateRequest): Promise<WorkflowDefinition> {
  const path = workflow.kref.replace(/^kref:\/\//, '');
  return apiFetch<WorkflowDefinition | { workflow: WorkflowDefinition }>(`/api/workflows/${path}`, {
    method: 'PUT',
    body: JSON.stringify(workflow),
    headers: { [EDITOR_SESSION_HEADER]: getEditorSessionId() },
  }).then((data) => unwrapField(data, 'workflow'));
}

export async function toggleWorkflowDeprecation(kref: string, deprecated: boolean): Promise<WorkflowDefinition> {
  return apiFetch<WorkflowDefinition | { workflow: WorkflowDefinition }>('/api/workflows/deprecate', {
    method: 'POST',
    body: JSON.stringify({ kref, deprecated }),
  }).then((data) => unwrapField(data, 'workflow'));
}

export async function deleteWorkflow(kref: string): Promise<void> {
  const path = kref.replace(/^kref:\/\//, '');
  return apiFetch<void>(`/api/workflows/${path}`, { method: 'DELETE' });
}

export async function fetchWorkflowByRevisionKref(
  revisionKref: string,
): Promise<WorkflowDefinition> {
  // Encode the whole kref — the `?r=N` suffix would otherwise be parsed as a
  // query string and dropped by the server's path extractor.
  const path = encodeURIComponent(revisionKref.replace(/^kref:\/\//, ''));
  return apiFetch<WorkflowDefinition | { workflow: WorkflowDefinition }>(
    `/api/workflows/revisions/${path}`
  ).then((data) => unwrapField(data, 'workflow'));
}

export async function fetchWorkflowRuns(
  limit = 20,
  workflow?: string,
): Promise<WorkflowRunSummary[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (workflow) params.set('workflow', workflow);
  return apiFetch<WorkflowRunSummary[] | { runs: WorkflowRunSummary[] }>(
    `/api/workflows/runs?${params}`
  ).then((data) => unwrapField(data, 'runs'));
}

export async function deleteWorkflowRun(runId: string): Promise<void> {
  return apiFetch<void>(`/api/workflows/runs/${encodeURIComponent(runId)}`, { method: 'DELETE' });
}

export async function fetchWorkflowRun(runId: string): Promise<WorkflowRunDetail> {
  return apiFetch<WorkflowRunDetail | { run: WorkflowRunDetail }>(
    `/api/workflows/runs/${encodeURIComponent(runId)}`
  ).then((data) => unwrapField(data, 'run'));
}

export async function approveWorkflowRun(
  runId: string,
  approved: boolean,
  feedback?: string,
): Promise<{ status: string; message: string }> {
  return apiFetch<{ status: string; message: string }>(
    `/api/workflows/runs/${encodeURIComponent(runId)}/approve`,
    {
      method: 'POST',
      body: JSON.stringify({ approved, feedback }),
    },
  );
}

export async function retryWorkflowRun(
  runId: string,
  cwd?: string,
): Promise<{ status: string; message?: string; run_id?: string }> {
  return apiFetch<{ status: string; message?: string; run_id?: string }>(
    `/api/workflows/runs/${encodeURIComponent(runId)}/retry`,
    {
      method: 'POST',
      body: JSON.stringify(cwd ? { cwd } : {}),
    },
  );
}

export interface CancelWorkflowRunResponse {
  cancelled: boolean;
  run_id?: string;
  status?: string;
  reason?: string;
  steps_completed?: number;
}

/**
 * Cancel a running workflow. The gateway maps the cancel signal to HTTP
 * status codes:
 *   - 200 → run cancelled (cancelled=true)
 *   - 404 → run not found / already finished
 *   - 409 → run is already in a terminal state
 *
 * Both 404 and 409 throw via apiFetch; callers catch and inspect via the
 * thrown Error's message to render an inline notice.
 */
export async function cancelWorkflowRun(
  runId: string,
): Promise<CancelWorkflowRunResponse> {
  return apiFetch<CancelWorkflowRunResponse>(
    `/api/workflows/runs/${encodeURIComponent(runId)}/cancel`,
    { method: 'POST', body: JSON.stringify({}) },
  );
}

export async function runWorkflow(
  name: string,
  inputs?: Record<string, unknown>,
  cwd?: string,
  options?: { targetStepId?: string },
): Promise<{ status: string; workflow: string; run_id: string }> {
  const body: Record<string, unknown> = { inputs: inputs ?? {} };
  if (cwd) body.cwd = cwd;
  if (options?.targetStepId) body.target_step_id = options.targetStepId;
  return apiFetch<{ status: string; workflow: string; run_id: string }>(
    `/api/workflows/run/${encodeURIComponent(name)}`,
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  );
}

// ---------------------------------------------------------------------------
// Architect (workflow revision)
// ---------------------------------------------------------------------------

/** POST /api/architect/revise — gateway forwards to operator-mcp's
 *  `revise_workflow` MCP tool. The Architect chat panel doesn't call this
 *  directly in Stage B.1 (the Operator does it via tool-call inside the
 *  agent loop), but the wrapper is here so Stage B.2's revision-history
 *  strip + revert path can consume it without re-deriving the contract. */
export async function reviseWorkflow(body: {
  workflow_kref: string;
  operations: RevisionOperation[];
  rationale?: string;
}): Promise<ReviseWorkflowResponse> {
  return apiFetch<ReviseWorkflowResponse>('/api/architect/revise', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** GET /api/architect/revisions?workflow_kref=... — list Kumiho revisions
 *  for a workflow item. Returns a thin summary (kref, number, created_at,
 *  tags, metadata). Used by the editor's revision history strip. */
export async function listRevisions(
  workflowKref: string,
): Promise<RevisionListResponse> {
  const params = new URLSearchParams({ workflow_kref: workflowKref });
  return apiFetch<RevisionListResponse>(`/api/architect/revisions?${params}`);
}

/** POST /api/architect/republish — re-tag the chosen revision as
 *  `published`. The gateway emits `workflow.revision.republished` on
 *  success, which the editor's SSE subscription auto-applies. */
export async function republishRevision(
  revisionKref: string,
): Promise<{ ok: boolean; revision_kref: string }> {
  return apiFetch<{ ok: boolean; revision_kref: string }>(
    '/api/architect/republish',
    {
      method: 'POST',
      body: JSON.stringify({ revision_kref: revisionKref }),
    },
  );
}

export interface ArchitectValidatorError {
  message?: string;
  path?: string;
  step_id?: string;
  field?: string;
  severity?: string;
}

export interface ArchitectValidateYamlResponse {
  valid: boolean;
  yaml: string;
  summary: string;
  errors: ArchitectValidatorError[];
  warnings: ArchitectValidatorError[];
  added_step_ids: string[];
  modified_step_ids: string[];
  removed_step_ids: string[];
}

/** POST /api/architect/validate_yaml — routes a YAML proposal through the
 *  same `propose_workflow_yaml` Operator tool the LLM uses. Used by the
 *  editor's chat-fallback path when Architect dumps YAML in chat instead
 *  of calling the tool. The endpoint is auth-gated (require_auth), so the
 *  raw fetch call previously used here returned 401; this helper threads
 *  the bearer token via apiFetch. */
export async function validateArchitectYaml(body: {
  yaml: string;
  base_yaml?: string;
  intent_summary?: string;
}): Promise<ArchitectValidateYamlResponse> {
  return apiFetch<ArchitectValidateYamlResponse>(
    '/api/architect/validate_yaml',
    {
      method: 'POST',
      body: JSON.stringify(body),
    },
  );
}

export interface AgentActivity {
  agent_id: string;
  view: string;
  title?: string;
  agent_type?: string;
  total_events?: number;
  tool_call_count?: number;
  error_count?: number;
  last_message?: string;
  recent_tools?: AgentToolCall[];
  usage?: { input_tokens: number; output_tokens: number; total_cost_usd: number };
  total?: number;
  entries?: AgentToolCall[];
}

export interface AgentToolCall {
  kind: string;
  ts?: string;
  name?: string;
  args?: string;
  result?: string;
  status?: string;
  error?: string;
  text?: string;
  command?: string;
  [key: string]: unknown;
}

export async function fetchAgentActivity(
  agentId: string,
  view: 'summary' | 'tool_calls' | 'messages' | 'errors' | 'full' = 'summary',
  limit = 100,
): Promise<AgentActivity> {
  return apiFetch<AgentActivity>(
    `/api/workflows/agent-activity/${encodeURIComponent(agentId)}?view=${view}&limit=${limit}`
  );
}

export async function fetchWorkflowDashboard(): Promise<WorkflowDashboard> {
  return apiFetch<WorkflowDashboard | { dashboard: WorkflowDashboard }>(
    '/api/workflows/dashboard'
  ).then((data) => unwrapField(data, 'dashboard'));
}

// ---------------------------------------------------------------------------
// Teams
// ---------------------------------------------------------------------------

export interface TeamsPage {
  teams: TeamDefinition[];
  total_count: number;
  page: number;
  per_page: number;
}

export async function fetchTeams(
  includeDisabled = false,
  page = 1,
  perPage = 9,
): Promise<TeamsPage> {
  const params = new URLSearchParams();
  if (includeDisabled) params.set('include_deprecated', 'true');
  params.set('page', String(page));
  params.set('per_page', String(perPage));
  return apiFetch<TeamsPage>(`/api/teams?${params}`);
}

export async function fetchTeam(kref: string): Promise<TeamDefinition> {
  const path = kref.replace(/^kref:\/\//, '');
  return apiFetch<TeamDefinition | { team: TeamDefinition }>(
    `/api/teams/${path}`
  ).then((data) => unwrapField(data, 'team'));
}

export async function createTeam(team: TeamCreateRequest): Promise<TeamDefinition> {
  return apiFetch<TeamDefinition | { team: TeamDefinition }>('/api/teams', {
    method: 'POST',
    body: JSON.stringify(team),
  }).then((data) => unwrapField(data, 'team'));
}

export async function updateTeam(team: TeamUpdateRequest): Promise<TeamDefinition> {
  const path = team.kref.replace(/^kref:\/\//, '');
  return apiFetch<TeamDefinition | { team: TeamDefinition }>(`/api/teams/${path}`, {
    method: 'PUT',
    body: JSON.stringify(team),
  }).then((data) => unwrapField(data, 'team'));
}

export async function toggleTeamDeprecation(kref: string, deprecated: boolean): Promise<void> {
  return apiFetch<void>('/api/teams/deprecate', {
    method: 'POST',
    body: JSON.stringify({ kref, deprecated }),
  });
}

export async function deleteTeam(kref: string): Promise<void> {
  // Strip kref:// prefix — the wildcard route expects the bare path, handler re-adds the prefix
  const path = kref.replace(/^kref:\/\//, '');
  return apiFetch<void>(`/api/teams/${path}`, { method: 'DELETE' });
}

// ---------------------------------------------------------------------------
// Memory Graph
// ---------------------------------------------------------------------------

export async function fetchMemoryGraph(params?: {
  project?: string;
  limit?: number;
  kinds?: string;
  space?: string;
  sort?: string;
  search?: string;
}): Promise<MemoryGraphResponse> {
  const qs = new URLSearchParams();
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        qs.set(key, String(value));
      }
    }
  }
  const query = qs.toString();
  return apiFetch<MemoryGraphResponse>(`/api/memory/graph${query ? `?${query}` : ''}`);
}

// ---------------------------------------------------------------------------
// Kumiho proxy — calls Kumiho via Construct gateway (/api/kumiho/*)
// ---------------------------------------------------------------------------

export async function kumihoProxy<T>(
  path: string,
  params?: Record<string, string | boolean | number | undefined>,
): Promise<T> {
  const qs = new URLSearchParams();
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        qs.set(key, String(value));
      }
    }
  }
  const qsStr = qs.toString();
  const cleanPath = path.startsWith('/') ? path.slice(1) : path;
  return apiFetch<T>(`/api/kumiho/${cleanPath}${qsStr ? `?${qsStr}` : ''}`);
}

export async function toggleAssetItemDeprecation(kref: string, deprecated: boolean): Promise<KumihoItem> {
  return apiFetch<{ item: KumihoItem }>('/api/assets/items/deprecate', {
    method: 'POST',
    body: JSON.stringify({ kref, deprecated }),
  }).then((data) => data.item);
}

export async function toggleAssetRevisionDeprecation(kref: string, deprecated: boolean): Promise<KumihoRevision> {
  return apiFetch<{ revision: KumihoRevision }>('/api/assets/revisions/deprecate', {
    method: 'POST',
    body: JSON.stringify({ kref, deprecated }),
  }).then((data) => data.revision);
}

export async function publishAssetRevision(kref: string): Promise<KumihoRevision> {
  return apiFetch<{ revision: KumihoRevision }>('/api/assets/revisions/publish', {
    method: 'POST',
    body: JSON.stringify({ kref }),
  }).then((data) => data.revision);
}

export async function toggleAssetArtifactDeprecation(kref: string, deprecated: boolean): Promise<KumihoArtifact> {
  return apiFetch<{ artifact: KumihoArtifact }>('/api/assets/artifacts/deprecate', {
    method: 'POST',
    body: JSON.stringify({ kref, deprecated }),
  }).then((data) => data.artifact);
}

export interface UpdateAssetArtifactContentResponse {
  revision: KumihoRevision;
  artifact: KumihoArtifact;
  created_revision: boolean;
  copied_artifacts: number;
}

export async function updateAssetArtifactContent(
  artifactKref: string,
  revisionKref: string,
  content: string,
): Promise<UpdateAssetArtifactContentResponse> {
  return apiFetch<UpdateAssetArtifactContentResponse>('/api/assets/artifacts/content', {
    method: 'PUT',
    body: JSON.stringify({
      artifact_kref: artifactKref,
      revision_kref: revisionKref,
      content,
    }),
  });
}

export async function fetchArtifactBodyText(location: string): Promise<string> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set('Authorization', `Bearer ${token}`);

  const response = await fetch(
    `${apiOrigin}${basePath}/api/artifact-body?location=${encodeURIComponent(location)}`,
    { headers },
  );
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    const contentType = response.headers.get('content-type');
    throw _buildApiError(response.status, response.statusText, text, contentType);
  }
  return response.text();
}

// ---------------------------------------------------------------------------
// Kumiho direct API helper — calls Kumiho FastAPI directly (legacy)
// ---------------------------------------------------------------------------

let _kumihoBaseUrl: string | null = null;

export async function getKumihoBaseUrl(): Promise<string> {
  if (_kumihoBaseUrl) return _kumihoBaseUrl;
  _kumihoBaseUrl = 'http://localhost:8000'; // default
  try {
    const configText = await getConfig();
    const match = configText?.match(/api_url\s*=\s*"([^"]+)"/);
    if (match?.[1]) _kumihoBaseUrl = match[1];
  } catch {
    /* use default */
  }
  return _kumihoBaseUrl!;
}

export async function kumihoFetch<T>(
  path: string,
  params?: Record<string, string | boolean | number | undefined>,
  options?: RequestInit,
): Promise<T> {
  const baseUrl = await getKumihoBaseUrl();
  const qs = new URLSearchParams();
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== null && value !== '') {
        qs.set(key, String(value));
      }
    }
  }
  const qsStr = qs.toString();
  const url = `${baseUrl}/api/v1${path}${qsStr ? `?${qsStr}` : ''}`;
  const resp = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!resp.ok) {
    const body = await resp.text().catch(() => '');
    throw new Error(`Kumiho API error ${resp.status}: ${body}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
}
