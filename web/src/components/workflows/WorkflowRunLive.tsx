/**
 * WorkflowRunLive — Real-time workflow execution view with DAG overlay.
 *
 * Connects to the WebSocket to receive agent events, maps them to workflow
 * steps via the agent title convention (`wf-{runId[:8]}-{stepId}`), and
 * overlays live status on the WorkflowGraph DAG.  Clicking a node shows
 * its events in a side panel.
 */

import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import {
  Bot,
  Brain,
  CheckCircle2,
  AlertTriangle,
  Wrench,
  MessageSquare,
  Radio,
  Clock,
  X,
  Activity,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import type { StepRunInfo } from './yamlSync';
import { parseWorkflowYaml, inferAgentFromTask } from './yamlSync';
import type { AgentChannelEvent, WsMessage } from '@/types/api';
import { WebSocketClient } from '@/lib/ws';
import { generateUUID } from '@/lib/uuid';
import { fetchWorkflowRun, fetchAgentActivity } from '@/lib/api';
import type { AgentActivity, AgentToolCall } from '@/lib/api';
import GroupChatTranscript from './GroupChatTranscript';
import ApprovalPanel from './ApprovalPanel';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WorkflowRunLiveProps {
  definition: string;
  runId: string;
  workflowName: string;
  /** Current run status from the REST API */
  runStatus?: string;
  onClose?: () => void;
}

// ---------------------------------------------------------------------------
// Event → Step mapping
// ---------------------------------------------------------------------------

/** Parse agent title to extract step ID.  Convention: `wf-{runId[:8]}-{stepId}` */
function extractStepId(
  agentTitle: string,
  runIdPrefix: string,
  agentId?: string,
  agentIdMap?: Map<string, string>,
): string | null {
  const prefix = `wf-${runIdPrefix}-`;
  if (agentTitle.startsWith(prefix)) {
    return agentTitle.slice(prefix.length);
  }
  // Fallback: reverse-lookup via agent_id from REST-hydrated step results
  if (agentId && agentIdMap?.has(agentId)) {
    return agentIdMap.get(agentId)!;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Step Event Panel (right sidebar)
// ---------------------------------------------------------------------------

function eventIcon(eventType: string) {
  switch (eventType) {
    case 'agent.started': return <Bot className="h-3 w-3" style={{ color: 'var(--pc-accent)' }} />;
    case 'agent.completed': return <CheckCircle2 className="h-3 w-3" style={{ color: 'var(--construct-status-success)' }} />;
    case 'agent.error': return <AlertTriangle className="h-3 w-3" style={{ color: 'var(--construct-status-danger)' }} />;
    case 'agent.tool_use': return <Wrench className="h-3 w-3" style={{ color: 'var(--pc-text-muted)' }} />;
    case 'agent.thinking': return <Brain className="h-3 w-3" style={{ color: 'var(--construct-signal-network)' }} />;
    case 'agent.message': return <MessageSquare className="h-3 w-3" style={{ color: 'var(--pc-accent)' }} />;
    default: return <Radio className="h-3 w-3" style={{ color: 'var(--pc-text-muted)' }} />;
  }
}

function eventTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}

function StepEventCard({ ev }: { ev: AgentChannelEvent }) {
  const [expanded, setExpanded] = useState(false);
  const isTool = ev.type === 'agent.tool_use';
  const isThinking = ev.type === 'agent.thinking';
  // Detail from event_consumer comes in ev.content.detail (not .args)
  const toolDetail = isTool ? (ev.content?.detail || ev.content?.args || '') : '';
  const hasDetail = isTool
    ? !!(ev.content?.output || (toolDetail && String(toolDetail).length > 40))
    : isThinking && !!ev.content?.text;

  return (
    <div
      className="rounded-lg border px-2.5 py-1.5 animate-fade-in"
      style={{ borderColor: 'var(--pc-border)', background: 'var(--pc-bg-elevated)' }}
    >
      <button
        type="button"
        onClick={() => hasDetail && setExpanded(!expanded)}
        className="flex w-full items-center gap-2 text-left"
      >
        <div className="shrink-0">{eventIcon(ev.type)}</div>
        <span className="flex-1 min-w-0 truncate text-[11px]" style={{ color: 'var(--pc-text-secondary)' }}>
          {isTool ? (
            <>
              <span style={{ color: 'var(--pc-text-primary)' }}>{ev.content?.tool ?? 'tool'}</span>
              {toolDetail && <span className="ml-1 opacity-70">{String(toolDetail).slice(0, 80)}</span>}
            </>
          ) : ev.type === 'agent.started' ? (
            <span style={{ color: 'var(--pc-accent)' }}>Agent spawned</span>
          ) : ev.type === 'agent.completed' ? (
            <span style={{ color: 'var(--construct-status-success)' }}>Completed</span>
          ) : ev.type === 'agent.error' ? (
            <span style={{ color: 'var(--construct-status-danger)' }}>{String(ev.content?.error ?? 'Error').slice(0, 80)}</span>
          ) : isThinking ? (
            <span style={{ color: 'var(--construct-signal-network)' }}>Thinking...</span>
          ) : (
            <span>{String(ev.content?.text ?? ev.type).slice(0, 80)}</span>
          )}
        </span>
        {hasDetail && (
          expanded
            ? <ChevronDown className="h-3 w-3 shrink-0" style={{ color: 'var(--pc-text-faint)' }} />
            : <ChevronRight className="h-3 w-3 shrink-0" style={{ color: 'var(--pc-text-faint)' }} />
        )}
        <span className="shrink-0 text-[10px]" style={{ color: 'var(--pc-text-faint)' }}>{eventTime(ev.timestamp)}</span>
      </button>
      {expanded && hasDetail && (
        <pre
          className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap text-[10px] leading-relaxed rounded p-2"
          style={{ color: 'var(--pc-text-secondary)', background: 'var(--pc-bg-base)' }}
        >
          {isTool
            ? (ev.content?.output || String(toolDetail))
            : ev.content?.text}
        </pre>
      )}
    </div>
  );
}

function RunLogToolCard({ entry }: { entry: AgentToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const name = entry.name || entry.kind || '';
  const status = entry.status || '';
  const hasResult = !!(entry.result || entry.error);

  // Extract readable detail from args
  let detail = '';
  try {
    const args = typeof entry.args === 'string' ? JSON.parse(entry.args || '{}') : (entry.args || {});
    if (name === 'Bash' || name === 'execute_command') detail = (args.command || '').split('\n')[0].slice(0, 100);
    else if (['Edit', 'Write', 'Read', 'edit_file', 'create_file', 'write_file'].includes(name)) {
      const fp = args.file_path || args.path || '';
      detail = fp.split('/').slice(-2).join('/');
    }
    else if (['WebSearch', 'web_search'].includes(name)) detail = args.query?.slice(0, 100) || '';
    else if (['WebFetch', 'web_fetch'].includes(name)) detail = args.url?.slice(0, 100) || '';
    else if (name === 'Grep') detail = args.pattern?.slice(0, 80) || '';
    else if (name === 'Glob') detail = args.pattern?.slice(0, 80) || '';
    else if (name === 'Agent') detail = args.description?.slice(0, 80) || '';
  } catch { /* ignore parse errors */ }

  const icon = name === 'Bash' || name === 'execute_command' ? '>'
    : ['WebSearch', 'web_search'].includes(name) ? '🔍'
    : ['WebFetch', 'web_fetch'].includes(name) ? '🌐'
    : ['Read', 'Grep', 'Glob'].includes(name) ? '📄'
    : ['Edit', 'Write', 'edit_file', 'create_file'].includes(name) ? '✏️'
    : '⚙️';

  const statusIcon = status === 'completed' ? '✓' : status === 'failed' ? '✗' : '⋯';
  const statusClr = status === 'completed'
    ? 'var(--construct-status-success)'
    : status === 'failed'
      ? 'var(--construct-status-danger)'
      : 'var(--construct-signal-live)';

  return (
    <div
      className="rounded border px-2 py-1.5"
      style={{ borderColor: 'var(--pc-border)', background: 'var(--pc-bg-elevated)' }}
    >
      <button
        type="button"
        onClick={() => hasResult && setExpanded(!expanded)}
        className="flex w-full items-center gap-1.5 text-left"
      >
        <span className="text-[10px] shrink-0">{icon}</span>
        <span className="flex-1 min-w-0 text-[11px] truncate" style={{ color: 'var(--pc-text-primary)' }}>
          <span className="font-medium">{name}</span>
          {detail && <span className="ml-1 opacity-60">{detail}</span>}
        </span>
        <span className="text-[10px] shrink-0" style={{ color: statusClr }}>{statusIcon}</span>
        {hasResult && (
          expanded
            ? <ChevronDown className="h-2.5 w-2.5 shrink-0" style={{ color: 'var(--pc-text-faint)' }} />
            : <ChevronRight className="h-2.5 w-2.5 shrink-0" style={{ color: 'var(--pc-text-faint)' }} />
        )}
      </button>
      {expanded && (
        <pre
          className="mt-1.5 max-h-48 overflow-auto whitespace-pre-wrap text-[10px] leading-relaxed rounded p-2"
          style={{ color: 'var(--pc-text-secondary)', background: 'var(--pc-bg-base)' }}
        >
          {entry.error ? `Error: ${entry.error}` : (typeof entry.result === 'string' ? entry.result.slice(0, 3000) : JSON.stringify(entry.result, null, 2)?.slice(0, 3000))}
        </pre>
      )}
    </div>
  );
}

function StepEventPanel({
  runId,
  stepId,
  events,
  stepInfo,
  onClose,
}: {
  runId: string;
  stepId: string;
  events: AgentChannelEvent[];
  stepInfo?: StepRunInfo;
  onClose: () => void;
}) {
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [activity, setActivity] = useState<AgentActivity | null>(null);
  const [activityLoading, setActivityLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<'live' | 'tools' | 'output'>('tools');

  // Fetch detailed activity from RunLog when agent_id is available
  const agentId = stepInfo?.agent_id;
  useEffect(() => {
    if (!agentId) return;
    setActivityLoading(true);
    fetchAgentActivity(agentId, 'summary', 50)
      .then((data) => setActivity(data))
      .catch(() => {})
      .finally(() => setActivityLoading(false));
  }, [agentId]);

  // Re-fetch activity when agent completes (to get final output)
  const isCompleted = stepInfo?.status === 'completed' || events.some((e) => e.type === 'agent.completed');
  useEffect(() => {
    if (!agentId || !isCompleted) return;
    fetchAgentActivity(agentId, 'summary', 50)
      .then((data) => setActivity(data))
      .catch(() => {});
  }, [agentId, isCompleted]);

  // Deduplicate: collapse consecutive events with same type+tool+timestamp
  const dedupedEvents = useMemo(() => {
    const result: AgentChannelEvent[] = [];
    for (const ev of events) {
      const prev = result[result.length - 1];
      if (prev
        && prev.type === ev.type
        && prev.timestamp === ev.timestamp
        && (prev.content?.tool || '') === (ev.content?.tool || '')
        && (prev.content?.detail || '') === (ev.content?.detail || '')) {
        continue;
      }
      if (ev.type === 'agent.completed' && result.some((e) => e.type === 'agent.completed')) {
        const idx = result.findIndex((e) => e.type === 'agent.completed');
        result[idx] = ev;
        continue;
      }
      result.push(ev);
    }
    return result;
  }, [events]);

  const completedEvent = dedupedEvents.find((e) => e.type === 'agent.completed');
  const errorEvent = dedupedEvents.find((e) => e.type === 'agent.error');
  const startedEvent = dedupedEvents.find((e) => e.type === 'agent.started');
  const toolCount = activity?.tool_call_count ?? dedupedEvents.filter((e) => e.type === 'agent.tool_use').length;

  const status = completedEvent ? 'completed'
    : errorEvent ? 'failed'
    : startedEvent ? 'running'
    : stepInfo?.status || 'pending';
  const statusColor = status === 'completed' ? 'var(--construct-status-success)'
    : status === 'failed' ? 'var(--construct-status-danger)'
    : status === 'running' ? 'var(--construct-signal-live)'
    : 'var(--pc-text-muted)';

  let duration = '';
  if (startedEvent && completedEvent) {
    const ms = new Date(completedEvent.timestamp).getTime() - new Date(startedEvent.timestamp).getTime();
    duration = ms < 60000 ? `${(ms / 1000).toFixed(1)}s` : `${(ms / 60000).toFixed(1)}m`;
  } else if (stepInfo?.duration_s) {
    const s = stepInfo.duration_s;
    duration = s < 60 ? `${s.toFixed(1)}s` : `${(s / 60).toFixed(1)}m`;
  }

  const hasTabs = !!(agentId && (activity || activityLoading));

  return (
    <div
      className="w-96 flex-shrink-0 border-l flex flex-col animate-fade-in"
      style={{ borderColor: 'var(--pc-border)', background: 'var(--pc-bg-surface)' }}
    >
      {/* Header */}
      <div className="p-3 border-b flex items-center justify-between" style={{ borderColor: 'var(--pc-border)' }}>
        <div className="min-w-0 flex-1">
          <h3 className="text-xs font-semibold truncate" style={{ color: 'var(--pc-text-primary)' }}>
            {stepId.replace(/_/g, ' ')}
          </h3>
          <div className="flex items-center gap-2 mt-1 text-[10px] flex-wrap" style={{ color: 'var(--pc-text-muted)' }}>
            {stepInfo?.agent_type && (
              <span style={{ color: 'var(--construct-signal-network)' }}>
                {stepInfo.agent_type}
              </span>
            )}
            {stepInfo?.role && <span>{stepInfo.role}</span>}
            {toolCount > 0 && <span>{toolCount} tools</span>}
            {activity?.error_count ? <span style={{ color: 'var(--construct-status-danger)' }}>{activity.error_count} errors</span> : null}
            {duration && (
              <span className="flex items-center gap-0.5">
                <Clock className="h-2.5 w-2.5" />
                {duration}
              </span>
            )}
            <span style={{ color: statusColor }}>{status}</span>
            {activity?.usage && activity.usage.total_cost_usd > 0 && (
              <span>${activity.usage.total_cost_usd.toFixed(3)}</span>
            )}
          </div>
          {/* Agent ID */}
          {stepInfo?.agent_id && (
            <div className="text-[9px] mt-0.5 font-mono truncate" style={{ color: 'var(--pc-text-faint)' }}>
              {stepInfo.agent_id.slice(0, 16)}
            </div>
          )}
          {/* Skills */}
          {stepInfo?.skills && stepInfo.skills.length > 0 && (
            <div className="flex gap-1 mt-1 flex-wrap">
              {stepInfo.skills.map((skill) => (
                <span
                  key={skill}
                  className="px-1 py-0.5 rounded text-[8px] font-medium inline-flex items-center gap-0.5"
                  style={{
                    background: status === 'running'
                      ? 'color-mix(in srgb, var(--construct-signal-live) 16%, transparent)'
                      : status === 'completed'
                        ? 'color-mix(in srgb, var(--construct-status-success) 16%, transparent)'
                        : 'var(--pc-accent-glow)',
                    color: status === 'running'
                      ? 'var(--construct-signal-live)'
                      : status === 'completed'
                        ? 'var(--construct-status-success)'
                        : 'var(--pc-accent-light)',
                  }}
                >
                  {status === 'running' && <span className="inline-block h-1 w-1 rounded-full" style={{ background: 'var(--construct-signal-live)', animation: 'pulse-dot 1.5s ease-in-out infinite' }} />}
                  {status === 'completed' && <span style={{ fontSize: '7px' }}>✓</span>}
                  {skill}
                </span>
              ))}
            </div>
          )}
        </div>
        <button onClick={onClose} className="p-1 rounded hover:bg-[var(--pc-hover)]">
          <X className="h-3.5 w-3.5" style={{ color: 'var(--pc-text-muted)' }} />
        </button>
      </div>

      {/* Tabs */}
      {hasTabs && (
        <div className="flex border-b text-[10px]" style={{ borderColor: 'var(--pc-border)' }}>
          {(['live', 'tools', 'output'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className="flex-1 py-1.5 text-center capitalize"
              style={{
                color: activeTab === tab ? 'var(--pc-accent)' : 'var(--pc-text-muted)',
                borderBottom: activeTab === tab ? '2px solid var(--pc-accent)' : '2px solid transparent',
              }}
            >
              {tab === 'live' ? 'Live Events' : tab === 'tools' ? 'Tool Calls' : 'Output'}
            </button>
          ))}
        </div>
      )}

      {/* Content */}
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto p-3">
        {!agentId && stepInfo?.action === 'group_chat' ? (
          /* Group chat step — show discussion transcript */
          <GroupChatTranscript
            transcript={stepInfo.transcript ?? []}
            status={status}
          />
        ) : !agentId && stepInfo?.awaiting_approval ? (
          /* Human approval step — show approval panel */
          <ApprovalPanel
            runId={runId}
            stepId={stepId}
            stepName={stepId.replace(/_/g, ' ')}
            message={stepInfo.approval_message ?? 'This step requires your approval before the workflow can continue.'}
            approveKeywords={stepInfo.approve_keywords}
            rejectKeywords={stepInfo.reject_keywords}
          />
        ) : !agentId ? (
          /* Non-agent step (output, notify, shell) — no WS events, show REST status */
          <div className="flex flex-col gap-3">
            <div
              className="rounded-lg border px-3 py-2"
              style={{ borderColor: 'var(--pc-border)', background: 'var(--pc-bg-elevated)' }}
            >
              <div className="flex items-center gap-2 text-[11px]">
                {status === 'completed' ? (
                  <CheckCircle2 className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--construct-status-success)' }} />
                ) : status === 'failed' ? (
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--construct-status-danger)' }} />
                ) : status === 'running' ? (
                  <Activity className="h-3.5 w-3.5 shrink-0 animate-spin" style={{ color: 'var(--construct-signal-live)' }} />
                ) : (
                  <Clock className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--pc-text-muted)' }} />
                )}
                <span className="font-medium" style={{ color: statusColor }}>
                  {status === 'pending' ? 'Waiting for dependencies...'
                    : status === 'running' ? 'Executing...'
                    : status === 'completed' ? 'Step completed'
                    : status === 'failed' ? 'Step failed'
                    : `Status: ${status}`}
                </span>
              </div>
            </div>
            <div className="text-[10px]" style={{ color: 'var(--pc-text-faint)' }}>
              This step runs without an agent — no tool call detail available.
            </div>
          </div>
        ) : activeTab === 'tools' ? (
          activityLoading ? (
            <div className="flex items-center justify-center h-32" style={{ color: 'var(--pc-text-muted)' }}>
              <p className="text-xs">Loading tool calls...</p>
            </div>
          ) : activity?.recent_tools && activity.recent_tools.length > 0 ? (
            <div className="flex flex-col gap-1">
              {activity.recent_tools.map((entry, i) => (
                <RunLogToolCard key={`${entry.name}-${entry.ts}-${i}`} entry={entry} />
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-32 text-center" style={{ color: 'var(--pc-text-muted)' }}>
              <p className="text-xs">{status === 'running' ? 'Agent is working...' : 'No tool calls recorded yet'}</p>
              {dedupedEvents.length > 0 && (
                <button
                  onClick={() => setActiveTab('live')}
                  className="mt-2 text-[10px] underline"
                  style={{ color: 'var(--pc-accent)' }}
                >
                  View live events →
                </button>
              )}
            </div>
          )
        ) : activeTab === 'output' ? (
          activity?.last_message ? (
            <pre
              className="whitespace-pre-wrap text-[11px] leading-relaxed"
              style={{ color: 'var(--pc-text-secondary)' }}
            >
              {activity.last_message}
            </pre>
          ) : (
            <div className="flex items-center justify-center h-32" style={{ color: 'var(--pc-text-muted)' }}>
              <p className="text-xs">{activityLoading ? 'Loading...' : status === 'running' ? 'Agent still working...' : 'No output captured'}</p>
            </div>
          )
        ) : (
          /* Live events tab */
          dedupedEvents.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-32 text-center" style={{ color: 'var(--pc-text-muted)' }}>
              <Activity className="h-6 w-6 mb-2" style={{ color: 'var(--pc-text-faint)' }} />
              <p className="text-xs">{status === 'running' ? 'Waiting for events...' : 'No live events captured'}</p>
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              {dedupedEvents.map((ev, i) => (
                <StepEventCard key={`${ev.type}-${ev.timestamp}-${i}`} ev={ev} />
              ))}
            </div>
          )
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Status summary bar
// ---------------------------------------------------------------------------

function RunStatusBar({
  stepResults,
  totalSteps,
  runStatus,
  elapsed,
}: {
  stepResults: Record<string, StepRunInfo>;
  totalSteps: number;
  runStatus?: string;
  elapsed: string;
}) {
  const completed = Object.values(stepResults).filter((s) => s.status === 'completed').length;
  const running = Object.values(stepResults).filter((s) => s.status === 'running').length;
  const failed = Object.values(stepResults).filter((s) => s.status === 'failed').length;

  const statusColor = runStatus === 'completed' ? 'var(--construct-status-success)'
    : runStatus === 'failed' ? 'var(--construct-status-danger)'
    : runStatus === 'running' ? 'var(--construct-signal-live)'
    : 'var(--pc-text-muted)';

  return (
    <div
      className="flex items-center gap-4 px-4 py-2.5 border-b text-xs"
      style={{ borderColor: 'var(--pc-border)', background: 'var(--pc-bg-surface)' }}
    >
      {/* Status badge */}
      <span
        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-semibold uppercase tracking-wider"
        style={{ background: `color-mix(in srgb, ${statusColor} 15%, transparent)`, color: statusColor }}
      >
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{
            background: statusColor,
            animation: runStatus === 'running' ? 'pulse-dot 1.5s ease-in-out infinite' : 'none',
          }}
        />
        {runStatus || 'unknown'}
      </span>

      {/* Step counts */}
      <div className="flex items-center gap-3" style={{ color: 'var(--pc-text-muted)' }}>
        {completed > 0 && <span style={{ color: 'var(--construct-status-success)' }}>{completed} done</span>}
        {running > 0 && <span style={{ color: 'var(--construct-signal-live)' }}>{running} running</span>}
        {failed > 0 && <span style={{ color: 'var(--construct-status-danger)' }}>{failed} failed</span>}
        <span>{Object.keys(stepResults).length}/{totalSteps} steps</span>
      </div>

      {/* Elapsed */}
      {elapsed && (
        <span className="flex items-center gap-1 ml-auto" style={{ color: 'var(--pc-text-faint)' }}>
          <Clock className="h-3 w-3" />
          {elapsed}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function WorkflowRunLive({
  definition,
  runId,
  workflowName,
  runStatus: initialStatus,
  onClose,
}: WorkflowRunLiveProps) {
  const runIdPrefix = runId.slice(0, 8);
  const [allEvents, setAllEvents] = useState<AgentChannelEvent[]>([]);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [runStatus, setRunStatus] = useState(initialStatus || 'running');
  const [startTime] = useState(Date.now());
  const [elapsed, setElapsed] = useState('');
  const wsRef = useRef<WebSocketClient | null>(null);
  const [initialStepResults, setInitialStepResults] = useState<Record<string, StepRunInfo>>({});

  // Elapsed timer
  useEffect(() => {
    if (runStatus !== 'running') return;
    const interval = setInterval(() => {
      const ms = Date.now() - startTime;
      const s = Math.floor(ms / 1000);
      const m = Math.floor(s / 60);
      setElapsed(m > 0 ? `${m}m ${s % 60}s` : `${s}s`);
    }, 1000);
    return () => clearInterval(interval);
  }, [runStatus, startTime]);

  // Hydrate step results from REST API — always fetch once on mount, then
  // poll every 5s while still running.  Stops polling once terminal.
  const runStatusRef = useRef(runStatus);
  runStatusRef.current = runStatus;

  useEffect(() => {
    let cancelled = false;
    let hasFetchedOnce = false;

    const isTerminal = (s: string) => s === 'completed' || s === 'failed';

    const poll = () => {
      // Always allow the first fetch (even if already terminal — need step data).
      // After that, stop polling once we reach a terminal state.
      if (hasFetchedOnce && isTerminal(runStatusRef.current)) return;
      fetchWorkflowRun(runId)
        .then((detail) => {
          if (cancelled) return;
          hasFetchedOnce = true;
          if (detail.steps && detail.steps.length > 0) {
            const results: Record<string, StepRunInfo> = {};
            for (const step of detail.steps) {
              results[step.step_id] = {
                status: (step.status as StepRunInfo['status']) || 'pending',
                agent_id: step.agent_id || undefined,
                agent_type: step.agent_type || undefined,
                role: step.role || undefined,
                template_name: step.template_name || undefined,
                skills: step.skills?.length ? step.skills : undefined,
                transcript: step.transcript?.length ? step.transcript : undefined,
                awaiting_approval: step.output_data?.awaiting_approval || undefined,
                approval_message: step.output_data?.approval_message || undefined,
                approve_keywords: step.output_data?.approve_keywords?.length ? step.output_data.approve_keywords : undefined,
                reject_keywords: step.output_data?.reject_keywords?.length ? step.output_data.reject_keywords : undefined,
              };
            }
            setInitialStepResults(results);
          }
          // Don't setRunStatus from REST — let step-inference derive it
          // from step results so "running" always takes priority over "failed".
        })
        .catch(() => {
          hasFetchedOnce = true; // don't block polling on fetch failure
        });
    };

    // Initial fetch
    poll();
    // Poll while running
    const interval = setInterval(() => {
      if (!cancelled) poll();
    }, 5000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [runId]);

  // Map agent_id → step_id from REST-hydrated step results so WS events
  // that arrive with a wrong/fallback agentTitle can still be routed.
  const agentIdMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const [stepId, info] of Object.entries(initialStepResults)) {
      if (info.agent_id) m.set(info.agent_id, stepId);
    }
    return m;
  }, [initialStepResults]);

  // Ref so the WS callback always sees the latest map without re-creating the socket
  const agentIdMapRef = useRef(agentIdMap);
  agentIdMapRef.current = agentIdMap;

  // WebSocket connection for agent events
  useEffect(() => {
    const sessionId = `wf-live-${generateUUID()}`;
    const ws = new WebSocketClient({ sessionId, autoReconnect: true });

    ws.onMessage = (msg: WsMessage) => {
      if (msg.type === 'agent_event' && msg.event) {
        const ev = msg.event as AgentChannelEvent;
        // Only collect events for this workflow run
        const stepId = extractStepId(ev.agentTitle, runIdPrefix, ev.agentId, agentIdMapRef.current);
        if (stepId) {
          setAllEvents((prev) => {
            const next = [...prev, ev];
            // Cap at 2000 events to prevent unbounded growth
            return next.length > 2000 ? next.slice(-2000) : next;
          });
        }
      }
    };

    // Send connect frame on open so the server enters its broadcast relay
    // loop.  Without this, the server blocks waiting for a first message
    // and never forwards agent events to this listen-only connection.
    ws.onOpen = () => {
      ws.sendConnect();
    };

    ws.connect();
    wsRef.current = ws;

    return () => {
      ws.disconnect();
      wsRef.current = null;
    };
  }, [runIdPrefix]);

  // Build step results: merge API-hydrated baseline with live WS events
  const stepResults = useMemo(() => {
    // Start with API-hydrated results as baseline
    const results: Record<string, StepRunInfo> = { ...initialStepResults };

    // Live events override API data (more up-to-date)
    for (const ev of allEvents) {
      const stepId = extractStepId(ev.agentTitle, runIdPrefix, ev.agentId, agentIdMap);
      if (!stepId) continue;

      if (!results[stepId]) {
        results[stepId] = { status: 'pending' };
      }

      const info = results[stepId];

      // Capture agent_id from ANY event that carries it, not just agent.started
      if (ev.agentId && !info.agent_id) {
        info.agent_id = ev.agentId;
      }

      switch (ev.type) {
        case 'agent.started':
          info.status = 'running';
          info.agent_id = ev.agentId;
          if (ev.content?.agent_type) info.agent_type = ev.content.agent_type;
          if (ev.content?.role) info.role = ev.content.role;
          break;
        case 'agent.completed':
          info.status = 'completed';
          if (ev.content?.duration_s) info.duration_s = ev.content.duration_s;
          break;
        case 'agent.error':
          info.status = 'failed';
          break;
      }
    }

    return results;
  }, [allEvents, runIdPrefix, initialStepResults, agentIdMap]);

  // Infer run status from step results.  Running takes priority over failed
  // so that parallel workflows don't show "failed" while other branches are
  // still executing.  Only show "failed" when nothing is still running.
  useEffect(() => {
    const steps = Object.values(stepResults);
    if (steps.length === 0) return;
    const hasRunning = steps.some((s) => s.status === 'running');
    const allDone = steps.every((s) => s.status === 'completed' || s.status === 'skipped');
    const hasFailed = steps.some((s) => s.status === 'failed');
    if (allDone && steps.length > 0) setRunStatus('completed');
    else if (hasRunning) setRunStatus('running');
    else if (hasFailed && !hasRunning) setRunStatus('failed');
  }, [stepResults]);

  // Events for selected step
  const selectedStepEvents = useMemo(() => {
    if (!selectedStep) return [];
    return allEvents.filter((ev) => extractStepId(ev.agentTitle, runIdPrefix, ev.agentId, agentIdMap) === selectedStep);
  }, [allEvents, selectedStep, runIdPrefix, agentIdMap]);

  // Handle node click from the graph
  const handleNodeClick = useCallback((_event: React.MouseEvent, stepId: string) => {
    setSelectedStep((prev) => (prev === stepId ? null : stepId));
  }, []);

  // Count steps: use the larger of YAML definition count and API step results
  const totalSteps = useMemo(() => {
    const yamlCount = (definition.match(/- id:/g) || []).length;
    const apiCount = Object.keys(stepResults).length;
    return Math.max(yamlCount, apiCount);
  }, [definition, stepResults]);

  return (
    <div className="flex flex-col h-full animate-fade-in">
      {/* Top bar */}
      <div
        className="flex items-center gap-3 px-4 py-2 border-b"
        style={{ borderColor: 'var(--pc-border)', background: 'var(--pc-bg-elevated)' }}
      >
        <Activity className="h-4 w-4" style={{ color: 'var(--pc-accent)' }} />
        <div className="flex-1 min-w-0">
          <span className="text-sm font-semibold" style={{ color: 'var(--pc-text-primary)' }}>
            {workflowName}
          </span>
          <span className="ml-2 text-[10px] font-mono" style={{ color: 'var(--pc-text-faint)' }}>
            {runId.slice(0, 8)}
          </span>
        </div>
        {onClose && (
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-[var(--pc-hover)]">
            <X className="h-4 w-4" style={{ color: 'var(--pc-text-muted)' }} />
          </button>
        )}
      </div>

      {/* Status bar */}
      <RunStatusBar
        stepResults={stepResults}
        totalSteps={totalSteps}
        runStatus={runStatus}
        elapsed={elapsed}
      />

      {/* Main content: Graph + optional side panel */}
      <div className="flex-1 flex min-h-0">
        {/* DAG */}
        <div className="flex-1 min-w-0">
          <WorkflowGraphLive
            definition={definition}
            workflowName={workflowName}
            stepResults={stepResults}
            selectedStep={selectedStep}
            onStepClick={handleNodeClick}
          />
        </div>

        {/* Step event panel */}
        {selectedStep && (
          <StepEventPanel
            runId={runId}
            stepId={selectedStep}
            events={selectedStepEvents}
            stepInfo={stepResults[selectedStep]}
            onClose={() => setSelectedStep(null)}
          />
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Interactive WorkflowGraph wrapper (supports node click)
// ---------------------------------------------------------------------------

import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { tasksToFlow, type TaskNodeData } from './yamlSync';
import { taskNodeTypes } from './TaskNode';
import { gateNodeTypes } from './GateNode';
import { layoutNodes } from '../teams/graphHelpers';

const allNodeTypes = { ...taskNodeTypes, ...gateNodeTypes };

function WorkflowGraphLive({
  definition,
  workflowName,
  stepResults,
  selectedStep,
  onStepClick,
}: {
  definition: string;
  workflowName: string;
  stepResults: Record<string, StepRunInfo>;
  selectedStep: string | null;
  onStepClick: (event: React.MouseEvent, stepId: string) => void;
}) {
  // Parse tasks once from YAML definition
  const parsedTasks = useMemo(() => parseWorkflowYaml(definition), [definition]);
  const { nodes, edges } = useMemo(() => {
    if (!definition.trim()) return { nodes: [] as Node[], edges: [] as Edge[] };

    const tasks = parsedTasks;
    if (tasks.length === 0) return { nodes: [] as Node[], edges: [] as Edge[] };

    const { nodes: rawNodes, edges: flowEdges } = tasksToFlow(tasks);

    // Build a task lookup for YAML-derived agent info
    const taskMap = new Map(tasks.map((t) => [t.id, t]));

    // Overlay run data + selected state
    for (const node of rawNodes) {
      const data = node.data as TaskNodeData;
      const info = stepResults[data.taskId];

      // Pre-populate from YAML definition so agent info is visible immediately
      const yamlTask = taskMap.get(data.taskId);
      if (yamlTask) {
        const inferred = inferAgentFromTask(yamlTask);
        const baseInfo: StepRunInfo = {
          status: 'pending',
          agent_type: inferred.agent_type,
          role: inferred.role,
          // StepRunInfo.action is the runtime overlay's free-text label —
          // for the YAML pre-fill we surface the canonical step type.
          action: yamlTask.type,
          skills: yamlTask.skills.length > 0 ? yamlTask.skills : undefined,
          template_name: yamlTask.assign || undefined,
        };
        // Merge: runtime data overrides YAML defaults
        data.runInfo = info ? { ...baseInfo, ...info } : baseInfo;
        // Ensure skills from YAML are always present even if runtime doesn't have them
        if (yamlTask.skills.length > 0 && !data.runInfo.skills?.length) {
          data.runInfo.skills = yamlTask.skills;
        }
        // Ensure pool agent name from YAML is present even if runtime hasn't set it yet
        if (yamlTask.assign && !data.runInfo.template_name) {
          data.runInfo.template_name = yamlTask.assign;
        }
      } else if (info) {
        data.runInfo = info;
      }

      // Mark selected node
      node.selected = data.taskId === selectedStep;
    }

    const laidOut = layoutNodes(rawNodes, flowEdges);

    // Restore saved positions from editor if available
    const savedKey = `wf-positions:${workflowName}`;
    try {
      const saved = JSON.parse(localStorage.getItem(savedKey) || '{}') as Record<string, { x: number; y: number }>;
      if (Object.keys(saved).length > 0) {
        for (const n of laidOut) {
          const s = saved[n.id];
          if (s) n.position = { x: s.x, y: s.y };
        }
      }
    } catch { /* ignore */ }

    // Color edges based on step status
    for (const edge of flowEdges) {
      const sourceStep = stepResults[edge.source];
      if (sourceStep?.status === 'completed') {
        edge.style = { stroke: 'var(--construct-status-success)', strokeWidth: 2 };
        edge.animated = false;
      } else if (sourceStep?.status === 'running') {
        edge.style = { stroke: 'var(--construct-signal-live)', strokeWidth: 2 };
        edge.animated = true;
      } else if (sourceStep?.status === 'failed') {
        edge.style = { stroke: 'var(--construct-status-danger)', strokeWidth: 2 };
        edge.animated = false;
      }
    }

    return { nodes: laidOut, edges: flowEdges };
  }, [parsedTasks, definition, stepResults, selectedStep]);

  return (
    <div className="h-full w-full" style={{ minHeight: 400 }}>
      <ReactFlowProvider>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={allNodeTypes}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable
          panOnDrag
          zoomOnScroll
          minZoom={0.3}
          maxZoom={2}
          onNodeClick={(_event, node) => {
            const data = node.data as TaskNodeData;
            onStepClick(_event as unknown as React.MouseEvent, data.taskId);
          }}
          proOptions={{ hideAttribution: true }}
          style={{ background: 'var(--pc-bg-base)' }}
        >
          <Background gap={20} size={1} color="rgba(255,255,255,0.03)" />
          <Controls
            showInteractive={false}
            style={{ background: 'var(--pc-bg-elevated)', borderColor: 'var(--pc-border)' }}
          />
          <MiniMap
            nodeColor={(node: Node) => {
              const data = node.data as TaskNodeData;
              if (data?.runInfo?.status === 'completed') return 'var(--construct-status-success)';
              if (data?.runInfo?.status === 'running') return 'var(--construct-signal-live)';
              if (data?.runInfo?.status === 'failed') return 'var(--construct-status-danger)';
              const stepType = data?.type?.toLowerCase() ?? '';
              if (stepType === 'conditional') return 'var(--construct-status-warning)';
              if (stepType.includes('research')) return 'var(--construct-signal-live)';
              if (stepType.includes('deploy')) return 'var(--construct-status-warning)';
              // All other decorative type accents collapse onto the network signal
              // — copper inside the Zion editor scope, neutral elsewhere.
              return 'var(--construct-signal-network)';
            }}
            nodeStrokeColor={(node: Node) => {
              const data = node.data as TaskNodeData;
              if (data?.runInfo?.status === 'completed') return 'var(--construct-status-success)';
              if (data?.runInfo?.status === 'running') return 'var(--construct-signal-live)';
              if (data?.runInfo?.status === 'failed') return 'var(--construct-status-danger)';
              return 'var(--construct-text-faint)';
            }}
            nodeStrokeWidth={2}
            nodeBorderRadius={8}
            pannable
            zoomable
            style={{
              background: 'var(--pc-bg-elevated)',
              border: '1px solid var(--construct-border-soft)',
              borderRadius: '0.75rem',
            }}
            maskColor="rgba(0,0,0,0.4)"
          />
        </ReactFlow>
      </ReactFlowProvider>
    </div>
  );
}
