import type { CSSProperties } from 'react';
import type { Edge } from '@xyflow/react';
import {
  tasksToFlow,
  type StepRunInfo,
  type TaskDefinition,
} from '@/revka/components/workflows/yamlSync';
import type { WorkflowStepDetail } from '@/types/api';

export function normalizeWorkflowStepStatus(status: string): StepRunInfo['status'] {
  switch (status) {
    case 'success':
      return 'completed';
    case 'error':
      return 'failed';
    case 'queued':
    case 'waiting':
    case 'blocked':
    case 'approval_required':
    case 'paused':
      return 'pending';
    case 'retrying':
      return 'running';
    case 'cancelled':
      return 'skipped';
    default:
      return (status as StepRunInfo['status']) ?? 'pending';
  }
}

export function toStepRunInfo(step: WorkflowStepDetail): StepRunInfo {
  return {
    status: normalizeWorkflowStepStatus(step.status),
    agent_id: step.agent_id,
    agent_type: step.agent_type,
    role: step.role,
    template_name: step.template_name,
    skills: step.skills,
    transcript: step.transcript,
    error: step.error,
    output_preview: step.output_preview,
    input_data: step.input_data,
    output_data: step.output_data,
  };
}

/**
 * Per-action color map. All tones are drawn from the Matrix palette tokens
 * so theme switches (dark/oled/light) stay coherent.
 *
 * Groupings:
 *   signal-live (green)    — execution/compute primitives
 *   signal-network (cyan)  — agents, comms, orchestration
 *   signal-selected        — review / inspection
 *   status-warning (amber) — human-in-the-loop / gated flows
 *   status-idle (slate)    — neutral flow-control
 */
const SIGNAL_LIVE = 'var(--revka-signal-live)';
const SIGNAL_NETWORK = 'var(--revka-signal-network)';
const SIGNAL_SELECTED = 'var(--revka-signal-selected)';
const STATUS_WARNING = 'var(--revka-status-warning)';
const STATUS_IDLE = 'var(--revka-status-idle)';

const ACTION_TONES: Record<string, string> = {
  // Execution / compute
  code: SIGNAL_LIVE,
  build: SIGNAL_LIVE,
  test: SIGNAL_LIVE,
  research: SIGNAL_LIVE,
  for_each: SIGNAL_LIVE,
  shell: SIGNAL_LIVE,

  // Agent / network / orchestration
  agent: SIGNAL_NETWORK,
  deploy: SIGNAL_NETWORK,
  notify: SIGNAL_NETWORK,
  a2a: SIGNAL_NETWORK,
  handoff: SIGNAL_NETWORK,
  group_chat: SIGNAL_NETWORK,
  map_reduce: SIGNAL_NETWORK,
  supervisor: SIGNAL_NETWORK,

  // Review / inspection / output
  review: SIGNAL_SELECTED,
  summarize: SIGNAL_SELECTED,
  resolve: SIGNAL_SELECTED,
  output: SIGNAL_SELECTED,

  // Human-in-the-loop / gated
  human_input: STATUS_WARNING,
  human_approval: STATUS_WARNING,
  approve: STATUS_WARNING,
  gate: STATUS_WARNING,
  conditional: STATUS_WARNING,
  goto: STATUS_WARNING,

  // Neutral flow control
  parallel: STATUS_IDLE,
  task: STATUS_IDLE,
};

// React Flow's MiniMap renders node fills as an SVG `fill` attribute which
// does not reliably resolve `var(--...)` tokens. Resolve CSS variables to
// computed colors before handing them to the minimap.
export function resolveCssVar(token: string): string {
  if (typeof window === 'undefined') return token;
  const match = token.match(/^var\((--[\w-]+)\)$/);
  const name = match?.[1];
  if (!name) return token;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value || token;
}

export function workflowActionTone(action: string): string {
  const normalized = action.toLowerCase();
  // Exact match first
  if (ACTION_TONES[normalized]) return ACTION_TONES[normalized];
  // Substring match fallback
  for (const [key, color] of Object.entries(ACTION_TONES)) {
    if (normalized.includes(key)) return color;
  }
  return 'var(--revka-signal-live)';
}

export function workflowStatusTone(status?: string): string {
  switch (status) {
    case 'running':
      return 'var(--revka-signal-live)';
    case 'completed':
      return 'var(--revka-signal-selected)';
    case 'failed':
      return 'var(--revka-status-danger)';
    case 'skipped':
      return 'var(--revka-status-idle)';
    case 'pending':
    default:
      return 'var(--revka-text-faint)';
  }
}

function buildIncomingTaskEdgeMap(tasks: TaskDefinition[]): Map<string, string[]> {
  const taskMap = new Map(tasks.map((task) => [task.id, task]));
  const incomingByTarget = new Map<string, string[]>();
  for (const edge of tasksToFlow(tasks).edges) {
    if (!taskMap.has(edge.source) || !taskMap.has(edge.target)) continue;
    const incoming = incomingByTarget.get(edge.target) ?? [];
    if (!incoming.includes(edge.source)) incoming.push(edge.source);
    incomingByTarget.set(edge.target, incoming);
  }
  return incomingByTarget;
}

export function buildWorkflowEdgeStyle({
  edge,
  tasksById,
  stepResults,
  selectedTaskId,
}: {
  edge: Edge;
  tasksById: Map<string, TaskDefinition>;
  stepResults?: Record<string, StepRunInfo>;
  selectedTaskId?: string | null;
}): Pick<Edge, 'animated' | 'style' | 'label' | 'labelStyle'> {
  const sourceStatus = stepResults?.[edge.source]?.status;
  const targetStatus = stepResults?.[edge.target]?.status;
  const runTone = workflowStatusTone(targetStatus ?? sourceStatus);
  const accent = stepResults ? runTone : 'var(--revka-signal-live)';
  void tasksById;
  const isFocused = selectedTaskId && (edge.source === selectedTaskId || edge.target === selectedTaskId);
  const label = edge.sourceHandle === 'true'
    ? 'TRUE'
    : edge.sourceHandle === 'false'
      ? 'FALSE'
      : typeof edge.label === 'string'
        ? edge.label
        : undefined;

  return {
    animated: sourceStatus === 'running' || targetStatus === 'running',
    label,
    labelStyle: label ? {
      ...(edge.labelStyle ?? {}),
      fill: edge.sourceHandle === 'false'
        ? 'var(--revka-status-danger)'
        : (edge.labelStyle as CSSProperties | undefined)?.fill ?? 'var(--revka-status-success)',
      fontSize: 10,
      fontWeight: 700,
    } : undefined,
    style: {
      stroke: isFocused ? accent : `color-mix(in srgb, ${accent} 72%, var(--revka-border-soft))`,
      strokeWidth: targetStatus === 'failed' ? 2.8 : isFocused ? 2.6 : 1.8,
      strokeDasharray: targetStatus === 'skipped' ? '5 4' : undefined,
      opacity: targetStatus === 'failed' ? 1 : isFocused ? 1 : 0.72,
      filter: targetStatus === 'failed'
        ? `drop-shadow(0 0 8px color-mix(in srgb, ${accent} 42%, transparent))`
        : undefined,
    } satisfies CSSProperties,
  };
}

export function deriveBlockedTaskIds({
  tasks,
  stepResults,
}: {
  tasks: TaskDefinition[];
  stepResults: Record<string, StepRunInfo>;
}): string[] {
  const blocked = new Set<string>();
  const incomingByTarget = buildIncomingTaskEdgeMap(tasks);
  const failed = new Set(
    Object.entries(stepResults)
      .filter(([, result]) => result.status === 'failed')
      .map(([taskId]) => taskId),
  );

  let changed = true;
  while (changed) {
    changed = false;
    for (const task of tasks) {
      if (blocked.has(task.id) || failed.has(task.id)) continue;
      const status = stepResults[task.id]?.status;
      if (status !== 'pending') continue;
      const isBlocked = (incomingByTarget.get(task.id) ?? [])
        .some((dependencyId) => failed.has(dependencyId) || blocked.has(dependencyId));
      if (isBlocked) {
        blocked.add(task.id);
        changed = true;
      }
    }
  }

  return [...blocked];
}

export function deriveDependencyChainIds({
  startTaskIds,
  tasks,
}: {
  startTaskIds: string[];
  tasks: TaskDefinition[];
}): string[] {
  const incomingByTarget = buildIncomingTaskEdgeMap(tasks);
  const visited = new Set<string>();
  const stack = [...startTaskIds];

  while (stack.length > 0) {
    const taskId = stack.pop();
    if (!taskId || visited.has(taskId)) continue;
    visited.add(taskId);
    for (const dependencyId of incomingByTarget.get(taskId) ?? []) {
      stack.push(dependencyId);
    }
  }

  return [...visited];
}
