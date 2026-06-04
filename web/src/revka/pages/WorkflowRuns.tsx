import { Eye, Pause, RefreshCw, Trash2, Wrench, MessageSquareText, RotateCcw } from 'lucide-react';
import type { ReactNode } from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { TaskDefinition } from '@/revka/components/workflows/yamlSync';
import { parseWorkflowYaml } from '@/revka/components/workflows/yamlSync';
import type { KumihoArtifact, WorkflowRunDetail, WorkflowRunSummary, WorkflowDefinition, WorkflowStepDetail } from '@/types/api';
import type { AgentActivity, AgentToolCall } from '@/lib/api';
import { cancelWorkflowRun, deleteWorkflowRun, fetchAgentActivity, fetchWorkflowByRevisionKref, fetchWorkflowRun, fetchWorkflowRuns, fetchWorkflows, retryWorkflowRun } from '@/lib/api';
import ApprovalPanel from '@/components/workflows/ApprovalPanel';
import { usePendingApprovals } from '@/contexts/PendingApprovalsContext';
import {
  OperatorCountChip,
  OperatorLegendChip,
  OperatorQuickFocusButton,
  OperatorSection,
  OperatorSignalChip,
} from '../components/orchestration/GraphOverlay';
import RunFocusBanner from '../components/orchestration/RunFocusBanner';
import Panel from '../components/ui/Panel';
import Notice from '../components/ui/Notice';
import PageHeader from '../components/ui/PageHeader';
import StatusPill from '../components/ui/StatusPill';
import StateMessage from '../components/ui/StateMessage';
import WorkflowDagWorkspace from '../components/workflows/WorkflowDagWorkspace';
import ArtifactViewerModal from '../components/ui/ArtifactViewerModal';
import { deriveBlockedTaskIds, deriveDependencyChainIds, toStepRunInfo } from '../lib/orchestration';
import { formatLocalDateTime } from '../lib/datetime';
import { expandedStepCount, loopProgressLabel } from '../lib/workflowProgress';
import { useT } from '@/revka/hooks/useT';

function isMissingRunError(err: unknown): boolean {
  return err instanceof Error && /\bAPI 404\b/.test(err.message);
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function detailText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function compactDetail(value: string, max = 220): string {
  const oneLine = value.replace(/\s+/g, ' ').trim();
  return oneLine.length > max ? `${oneLine.slice(0, max - 1)}…` : oneLine;
}

function hasStructuredData(value: unknown): boolean {
  const record = asRecord(value);
  return Object.keys(record).length > 0;
}

function jsonPreview(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return detailText(value);
  }
}

function stepFailureReason(step?: WorkflowStepDetail | null): string {
  if (!step || step.status !== 'failed') return '';
  const input = asRecord(step.input_data);
  const output = asRecord(step.output_data);
  const candidates = [
    step.error,
    output.error,
    output.entity_error,
    output.entity_artifact_error,
    output.entity_tag_error,
    output.register_output_error,
    output.structured_output_error,
    output.stderr,
    output.stderr_preview,
    asRecord(output.error_message).content,
    input.command ? `command: ${input.command}` : '',
    input.code_preview ? `python: ${input.code_preview}` : '',
  ];
  for (const candidate of candidates) {
    const text = compactDetail(detailText(candidate));
    if (text) return text;
  }
  return '';
}

function RunProgressMeta({ run, className = '' }: { run: WorkflowRunSummary; className?: string }) {
  const { tpl } = useT();
  const expanded = expandedStepCount(run);
  const loopLabel = loopProgressLabel(run, tpl);

  return (
    <div className={className} style={{ color: 'var(--revka-text-faint)' }}>
      <span>{tpl('runs.stats.steps_fraction', { completed: run.steps_completed || '0', total: run.steps_total || '?' })}</span>
      {expanded !== null ? <span>{tpl('runs.stats.expanded_steps', { count: expanded })}</span> : null}
      {loopLabel ? <span className="font-mono">{loopLabel}</span> : null}
    </div>
  );
}

interface ConditionalResolutionDetail {
  label: string;
  goto: string;
  condition: string;
  valueExpr: string;
  output: string;
}

function conditionalResolution(step?: WorkflowStepDetail | null): ConditionalResolutionDetail | null {
  if (!step || step.status !== 'completed') return null;
  const input = asRecord(step.input_data);
  const output = asRecord(step.output_data);
  const rawIndex = output.matched_branch_index ?? input.matched_branch_index;
  const index = typeof rawIndex === 'number' ? rawIndex : Number(detailText(rawIndex));
  const explicitLabel = detailText(output.matched_branch_label);
  const label = explicitLabel
    || (Number.isFinite(index) && index >= 0 ? `Branch ${index + 1}` : 'No branch matched');
  const emitted = detailText(output.matched_output ?? step.output_preview);
  return {
    label,
    goto: detailText(output.matched_goto),
    condition: detailText(output.matched_condition ?? input.matched_condition),
    valueExpr: detailText(output.matched_value_expr ?? input.matched_value_expr),
    output: emitted,
  };
}

export default function WorkflowRuns() {
  const { t, tpl } = useT();
  const { dismiss: dismissPendingApproval } = usePendingApprovals();
  const [searchParams, setSearchParams] = useSearchParams();
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [selectedRun, setSelectedRun] = useState<WorkflowRunDetail | null>(null);
  const [pinnedDefinition, setPinnedDefinition] = useState<WorkflowDefinition | null>(null);
  const [selectedTask, setSelectedTask] = useState<TaskDefinition | null>(null);
  const [selectedActivity, setSelectedActivity] = useState<AgentActivity | null>(null);
  const [activityLoading, setActivityLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [pathMode, setPathMode] = useState<'all' | 'failed' | 'blocked'>('all');
  const [detailTab, setDetailTab] = useState<'summary' | 'output' | 'tools' | 'transcript'>('summary');
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);
  const [viewerArtifact, setViewerArtifact] = useState<KumihoArtifact | null>(null);
  const [shouldScrollToWorkspace, setShouldScrollToWorkspace] = useState(false);
  const workspaceRef = useRef<HTMLDivElement | null>(null);

  /* ---- data loading ---- */

  const load = async () => {
    setLoading(true);
    return Promise.all([
      fetchWorkflowRuns(25),
      fetchWorkflows(true, false),
    ])
      .then(async ([workflowRuns, workflowDefinitions]) => {
        const requestedRun = searchParams.get('run');
        const requestedWorkflow = searchParams.get('workflow');

        // If a specific run is requested but not in the top-25 window, fetch it
        // directly and prepend so notification deep-links always resolve.
        let mergedRuns = workflowRuns;
        let requestedRunMissing = false;
        if (requestedRun && !workflowRuns.some((run) => run.run_id === requestedRun)) {
          const detail = await fetchWorkflowRun(requestedRun).catch((err: unknown) => {
            if (isMissingRunError(err)) {
              // Stale pending-approval entry (daemon restart cleared the
              // in-memory registry, or the run was deleted). Evict it from the
              // notification store and drop `?run=` from the URL so the page
              // falls back to the first available run.
              dismissPendingApproval(requestedRun);
              requestedRunMissing = true;
              return null;
            }
            throw err;
          });
          if (detail) {
            const { steps: _steps, ...summary } = detail;
            mergedRuns = [summary as WorkflowRunSummary, ...workflowRuns];
          }
        }

        if (requestedRunMissing) {
          setSearchParams((current) => {
            const next = new URLSearchParams(current);
            next.delete('run');
            return next;
          }, { replace: true });
        }

        setRuns(mergedRuns);
        setDefinitions(workflowDefinitions);

        const scopedRuns = requestedWorkflow
          ? mergedRuns.filter((run) => run.workflow_name.toLowerCase() === requestedWorkflow.toLowerCase())
          : mergedRuns;
        // `?run=` is authoritative — always honor it on (re)load so clicking an
        // approval notification navigates to the correct run even when another
        // run is already selected. If the requested run 404'd, fall back to the
        // first run we do have.
        const effectiveRequested = requestedRunMissing ? null : requestedRun;
        setSelectedRunId(effectiveRequested ?? scopedRuns[0]?.run_id ?? mergedRuns[0]?.run_id ?? null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // React to `?run=` changing after the initial load (e.g. clicking the
  // approval notification while already on this page).
  useEffect(() => {
    const requestedRun = searchParams.get('run');
    if (!requestedRun || requestedRun === selectedRunId) return;
    setSelectedRunId(requestedRun);
    if (!runs.some((run) => run.run_id === requestedRun)) {
      void fetchWorkflowRun(requestedRun)
        .then((detail) => {
          const { steps: _steps, ...summary } = detail;
          setRuns((prev) =>
            prev.some((run) => run.run_id === requestedRun)
              ? prev
              : [summary as WorkflowRunSummary, ...prev],
          );
        })
        .catch((err: unknown) => {
          if (isMissingRunError(err)) {
            dismissPendingApproval(requestedRun);
            setSearchParams((current) => {
              const next = new URLSearchParams(current);
              next.delete('run');
              return next;
            }, { replace: true });
          }
          /* other errors handled by the detail fetch effect */
        });
    }
  }, [searchParams, runs, selectedRunId]);

  useEffect(() => {
    if (!selectedRunId) return;
    const runId = selectedRunId;
    setPinnedDefinition(null);
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let lastPinnedKref: string | null = null;
    const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled']);
    const POLL_INTERVAL_MS = 4000;
    const scheduleNext = (delay: number) => {
      timer = setTimeout(poll, delay);
    };
    const poll = () => {
      fetchWorkflowRun(runId)
        .then((run) => {
          if (cancelled) return;
          setSelectedRun(run);
          // The pinned-definition kref is stable for a given run, so only
          // fetch it once (or when it actually changes between polls).
          if (run.workflow_revision_kref && run.workflow_revision_kref !== lastPinnedKref) {
            lastPinnedKref = run.workflow_revision_kref;
            fetchWorkflowByRevisionKref(run.workflow_revision_kref)
              .then((def) => {
                if (!cancelled) setPinnedDefinition(def);
              })
              .catch(() => {
                if (!cancelled) setPinnedDefinition(null);
              });
          }
          // Keep polling while the run is still in flight. Treat any
          // unrecognized status as terminal so we don't loop forever.
          if (run.status === 'running' || run.status === 'pending') {
            scheduleNext(POLL_INTERVAL_MS);
          } else if (!TERMINAL_STATUSES.has(run.status)) {
            return;
          }
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          if (isMissingRunError(err)) {
            // Backend lost the run (daemon restart or deletion). Clean up the
            // stale notification + URL state and pick a different run.
            dismissPendingApproval(runId);
            setSelectedRun(null);
            setSelectedRunId(null);
            setSearchParams((current) => {
              const next = new URLSearchParams(current);
              next.delete('run');
              return next;
            }, { replace: true });
            return;
          }
          setError(err instanceof Error ? err.message : String(err));
        });
    };
    poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedRunId]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!shouldScrollToWorkspace || !selectedRun) return;
    workspaceRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    workspaceRef.current?.focus({ preventScroll: true });
    setShouldScrollToWorkspace(false);
  }, [selectedRun, shouldScrollToWorkspace]);

  /* ---- derived state ---- */

  const selectedDefinition = useMemo(() => {
    if (!selectedRun) return null;
    // Prefer the revision-pinned definition if available — it mirrors the exact
    // YAML the run executed, so the DAG won't drift when the workflow is edited.
    if (pinnedDefinition) return pinnedDefinition;
    const workflowName = selectedRun.workflow_name.toLowerCase();
    return definitions.find((definition) => definition.name.toLowerCase() === workflowName) ?? null;
  }, [definitions, pinnedDefinition, selectedRun]);

  const displayedRuns = useMemo(() => {
    const requestedWorkflow = searchParams.get('workflow');
    if (!requestedWorkflow) return runs;
    const lower = requestedWorkflow.toLowerCase();
    return runs.filter((run) => run.workflow_name.toLowerCase() === lower);
  }, [runs, searchParams]);

  const selectedDefinitionTasks = useMemo(
    () => (selectedDefinition ? parseWorkflowYaml(selectedDefinition.definition) : []),
    [selectedDefinition],
  );

  useEffect(() => {
    const requestedNode = searchParams.get('node');
    if (!requestedNode) {
      setSelectedTask(null);
      return;
    }
    setSelectedTask(selectedDefinitionTasks.find((task) => task.id === requestedNode) ?? null);
  }, [searchParams, selectedDefinitionTasks]);

  useEffect(() => {
    const requestedPathMode = searchParams.get('path');
    if (requestedPathMode === 'failed' || requestedPathMode === 'blocked' || requestedPathMode === 'all') {
      setPathMode(requestedPathMode);
    }
  }, [searchParams]);

  useEffect(() => {
    setDetailTab('summary');
  }, [selectedRunId, selectedTask?.id]);

  const stepResults = useMemo(() => {
    if (!selectedRun) return {};
    return Object.fromEntries(selectedRun.steps.map((step) => [step.step_id, toStepRunInfo(step)]));
  }, [selectedRun]);

  const selectedStep = useMemo(
    () => (selectedTask && selectedRun ? selectedRun.steps.find((step) => step.step_id === selectedTask.id) ?? null : null),
    [selectedRun, selectedTask],
  );
  const selectedFailureReason = useMemo(() => stepFailureReason(selectedStep), [selectedStep]);
  const selectedConditionalResolution = useMemo(
    () => (selectedTask?.type === 'conditional' ? conditionalResolution(selectedStep) : null),
    [selectedStep, selectedTask?.type],
  );

  const pendingApprovalStep = useMemo(
    () => selectedRun?.steps.find((step) => step.output_data?.awaiting_approval === true) ?? null,
    [selectedRun],
  );

  const runStepCounts = useMemo(() => {
    const counts = { pending: 0, running: 0, completed: 0, failed: 0, skipped: 0 };
    if (!selectedRun) return counts;
    for (const step of selectedRun.steps) {
      const normalized = toStepRunInfo(step).status;
      counts[normalized] += 1;
    }
    return counts;
  }, [selectedRun]);

  const blockedTaskIds = useMemo(
    () => deriveBlockedTaskIds({ tasks: selectedDefinitionTasks, stepResults }),
    [selectedDefinitionTasks, stepResults],
  );

  const failingSteps = useMemo(
    () => selectedRun?.steps.filter((step) => toStepRunInfo(step).status === 'failed') ?? [],
    [selectedRun],
  );

  const runningSteps = useMemo(
    () => selectedRun?.steps.filter((step) => toStepRunInfo(step).status === 'running') ?? [],
    [selectedRun],
  );

  const blockedTasks = useMemo(
    () => selectedDefinitionTasks.filter((task) => blockedTaskIds.includes(task.id)),
    [blockedTaskIds, selectedDefinitionTasks],
  );

  const riskAndActiveTasks = useMemo(
    () => [
      ...failingSteps
        .map((step) => selectedDefinitionTasks.find((task) => task.id === step.step_id) ?? null)
        .filter((task): task is TaskDefinition => task !== null),
      ...runningSteps
        .map((step) => selectedDefinitionTasks.find((task) => task.id === step.step_id) ?? null)
        .filter((task): task is TaskDefinition => task !== null)
        .filter((task) => !failingSteps.some((step) => step.step_id === task.id)),
      ...blockedTasks.filter((task) => !failingSteps.some((step) => step.step_id === task.id)),
    ],
    [blockedTasks, failingSteps, runningSteps, selectedDefinitionTasks],
  );

  const failedChainIds = useMemo(
    () => deriveDependencyChainIds({ startTaskIds: failingSteps.map((step) => step.step_id), tasks: selectedDefinitionTasks }),
    [failingSteps, selectedDefinitionTasks],
  );

  const blockedChainIds = useMemo(
    () => deriveDependencyChainIds({ startTaskIds: blockedTaskIds, tasks: selectedDefinitionTasks }),
    [blockedTaskIds, selectedDefinitionTasks],
  );

  const hiddenTaskIds = useMemo(() => {
    if (pathMode === 'all') return [];
    const visible = new Set(pathMode === 'failed' ? failedChainIds : blockedChainIds);
    return selectedDefinitionTasks.map((task) => task.id).filter((taskId) => !visible.has(taskId));
  }, [blockedChainIds, failedChainIds, pathMode, selectedDefinitionTasks]);

  /* ---- agent activity for selected step ---- */

  useEffect(() => {
    const agentId = selectedStep?.agent_id;
    if (!agentId) {
      setSelectedActivity(null);
      setActivityLoading(false);
      return;
    }
    setActivityLoading(true);
    fetchAgentActivity(agentId, 'summary', 50)
      .then((activity) => setSelectedActivity(activity))
      .catch(() => setSelectedActivity(null))
      .finally(() => setActivityLoading(false));
  }, [selectedStep?.agent_id]);

  /* ---- keyboard nav ---- */

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (riskAndActiveTasks.length === 0) return;
      const currentIndex = riskAndActiveTasks.findIndex((task) => task.id === selectedTask?.id);
      if (event.key === 'j') {
        event.preventDefault();
        setSelectedTask(riskAndActiveTasks[currentIndex >= 0 ? (currentIndex + 1) % riskAndActiveTasks.length : 0] ?? null);
      }
      if (event.key === 'k') {
        event.preventDefault();
        setSelectedTask(riskAndActiveTasks[currentIndex >= 0 ? (currentIndex - 1 + riskAndActiveTasks.length) % riskAndActiveTasks.length : riskAndActiveTasks.length - 1] ?? null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [riskAndActiveTasks, selectedTask?.id]);

  /* ---- URL sync ---- */

  useEffect(() => {
    if (!selectedRun) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('run', selectedRun.run_id);
      next.set('workflow', selectedRun.workflow_name);
      next.set('path', pathMode);
      if (selectedTask?.id) {
        next.set('node', selectedTask.id);
      } else {
        next.delete('node');
      }
      return next;
    }, { replace: true });
  }, [pathMode, selectedRun?.run_id, selectedRun?.workflow_name, selectedTask?.id, setSearchParams]);

  /* ---- handlers ---- */

  const handleRetryRun = async () => {
    if (!selectedRun) return;
    setRetrying(true);
    try {
      const runLabel = selectedRun.run_id.slice(0, 8);
      await retryWorkflowRun(selectedRun.run_id);
      setNotice({ tone: 'success', message: tpl('runs.toast.retry_started', { id: runLabel }) });
      // Refetch to show new step states.
      const fresh = await fetchWorkflowRun(selectedRun.run_id).catch(() => null);
      if (fresh) setSelectedRun(fresh);
      await load();
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('runs.err.retry') });
    } finally {
      setRetrying(false);
    }
  };

  const handleCancelRun = async () => {
    if (!selectedRun || cancelling) return;
    setCancelling(true);
    try {
      const runLabel = selectedRun.run_id.slice(0, 8);
      await cancelWorkflowRun(selectedRun.run_id);
      setNotice({ tone: 'success', message: tpl('runs.toast.stop_requested', { id: runLabel }) });
      const fresh = await fetchWorkflowRun(selectedRun.run_id).catch(() => null);
      if (fresh) setSelectedRun(fresh);
      await load();
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('runs.err.stop') });
    } finally {
      setCancelling(false);
    }
  };

  const handleDeleteRun = async () => {
    if (!selectedRun) return;
    setDeleting(true);
    try {
      const runLabel = selectedRun.run_id.slice(0, 8);
      await deleteWorkflowRun(selectedRun.run_id);
      setSelectedTask(null);
      setSelectedRun(null);
      setSelectedRunId(null);
      await load();
      setNotice({ tone: 'success', message: tpl('runs.toast.deleted', { id: runLabel }) });
    } catch (err) {
      setError(err instanceof Error ? err.message : t('runs.err.delete'));
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('runs.err.delete') });
    } finally {
      setDeleting(false);
    }
  };

  const focusTaskById = (taskId: string) => {
    setSelectedTask(selectedDefinitionTasks.find((task) => task.id === taskId) ?? null);
  };

  const focusPreviousSignal = () => {
    const currentIndex = riskAndActiveTasks.findIndex((task) => task.id === selectedTask?.id);
    setSelectedTask(riskAndActiveTasks[currentIndex >= 0 ? (currentIndex - 1 + riskAndActiveTasks.length) % riskAndActiveTasks.length : riskAndActiveTasks.length - 1] ?? null);
  };

  const focusNextSignal = () => {
    const currentIndex = riskAndActiveTasks.findIndex((task) => task.id === selectedTask?.id);
    setSelectedTask(riskAndActiveTasks[currentIndex >= 0 ? (currentIndex + 1) % riskAndActiveTasks.length : 0] ?? null);
  };

  /* ---- render ---- */

  const tabLabels: Record<'summary' | 'output' | 'tools' | 'transcript', string> = {
    summary: t('runs.tab.summary'),
    output: t('runs.tab.output'),
    tools: t('runs.tab.tools'),
    transcript: t('runs.tab.transcript'),
  };

  return (
    <>
    <div className="flex h-[calc(100vh-6rem)] flex-col gap-3">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}

      {/* Row 1 — Header */}
      <PageHeader
        kicker={t('runs.kicker')}
        title={t('runs.title')}
        actions={
          <button className="revka-button" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> {t('runs.refresh')}
          </button>
        }
      />

      {/* Row 2 — Three-column: run index | DAG canvas | step inspector */}
      <div className="grid min-h-0 flex-1 gap-4" style={{ gridTemplateColumns: '16rem minmax(0,1fr) 24rem' }}>
        {/* ---- LEFT: Run index ---- */}
        <Panel className="flex flex-col overflow-hidden p-0" variant="secondary">
          <div className="shrink-0 border-b px-4 py-2.5" style={{ borderColor: 'var(--revka-border-soft)' }}>
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--revka-text-faint)' }}>
                {t('runs.index.title')}
              </span>
              <span className="text-[11px]" style={{ color: 'var(--revka-text-faint)' }}>
                {displayedRuns.length}
              </span>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {displayedRuns.length === 0 && !error ? (
              <div className="p-3">
                <StateMessage compact title={t('runs.empty.title')} description={t('runs.empty.desc')} />
              </div>
            ) : null}
            {error ? (
              <div className="p-3">
                <StateMessage tone="error" compact title={t('runs.error.title')} description={error} />
              </div>
            ) : null}
            {displayedRuns.map((run) => {
              const isActive = run.run_id === selectedRunId;
              return (
                <button
                  key={run.run_id}
                  type="button"
                  onClick={() => {
                    setSelectedRunId(run.run_id);
                    setSelectedTask(null);
                    setShouldScrollToWorkspace(true);
                  }}
                  className="w-full border-b px-4 py-3 text-left transition"
                  style={{
                    borderColor: 'var(--revka-border-soft)',
                    background: isActive
                      ? 'color-mix(in srgb, var(--revka-signal-selected) 14%, var(--revka-bg-panel))'
                      : 'transparent',
                  }}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium" style={{ color: 'var(--revka-text-primary)' }}>
                      {run.workflow_name}
                    </span>
                    <StatusPill status={run.status} />
                  </div>
                  <RunProgressMeta className="mt-1 flex flex-wrap items-center gap-3 text-xs" run={run} />
                  <div className="mt-1 flex items-center gap-3 text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                    <span className="font-mono">{run.run_id.slice(0, 8)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </Panel>

        {/* ---- CENTER: DAG workspace ---- */}
        <div ref={workspaceRef} tabIndex={-1} className="flex min-h-0 flex-col gap-3 outline-none">
          {/* Workspace header bar */}
          {selectedRun ? (
            <div className="flex shrink-0 flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                <span className="text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
                  {selectedRun.workflow_name} / {selectedRun.run_id.slice(0, 8)}
                </span>
                <StatusPill status={selectedRun.status} />
                {selectedDefinition ? (
                  <Link
                    to={`/workflows?workflow=${encodeURIComponent(selectedDefinition.kref)}${selectedTask ? `&node=${encodeURIComponent(selectedTask.id)}` : ''}`}
                    className="text-xs"
                    style={{ color: 'var(--revka-signal-network)' }}
                  >
                    {t('runs.open_definition')}
                  </Link>
                ) : null}
              </div>
              <div className="flex items-center gap-2">
                {selectedRun.status === 'failed' ? (
                  <button
                    className="revka-button"
                    onClick={handleRetryRun}
                    disabled={retrying}
                    title={t('runs.action.retry_tooltip')}
                    style={{
                      background: 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 18%, transparent))',
                      color: 'var(--revka-signal-selected)',
                      borderColor: 'var(--revka-signal-selected)',
                    }}
                  >
                    <RotateCcw className={`h-3.5 w-3.5 ${retrying ? 'animate-spin' : ''}`} />
                    <span className="ml-1 text-xs">{retrying ? t('runs.action.retrying') : t('runs.action.retry_failed')}</span>
                  </button>
                ) : null}
                {selectedRun.status === 'running' || selectedRun.status === 'pending' || selectedRun.status === 'paused' ? (
                  <button
                    className="revka-button"
                    onClick={handleCancelRun}
                    disabled={cancelling}
                    title={t('runs.action.stop_tooltip')}
                  >
                    {cancelling ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Pause className="h-3.5 w-3.5" />}
                    <span className="ml-1 text-xs">{cancelling ? t('runs.action.stopping') : t('runs.action.stop')}</span>
                  </button>
                ) : null}
                <button
                  className="revka-button"
                  onClick={handleDeleteRun}
                  disabled={deleting}
                  title={t('runs.action.delete_tooltip')}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          ) : null}

          <RunFocusBanner run={selectedRun} active={shouldScrollToWorkspace} label={t('runs.banner.label')} />

          {/* DAG canvas */}
          <div className="min-h-0 flex-1">
            {selectedRun && selectedDefinition ? (
              <WorkflowDagWorkspace
                definition={selectedDefinition.definition}
                stepResults={stepResults}
                onSelectTask={setSelectedTask}
                selectedTaskId={selectedTask?.id}
                hiddenTaskIds={hiddenTaskIds}
                blockedTaskIds={blockedTaskIds}
                failingTaskIds={failingSteps.map((step) => step.step_id)}
                runningTaskIds={runningSteps.map((step) => step.step_id)}
                overlay={
                  <div className="space-y-2">
                    <OperatorSection title={t('runs.overlay.path_mode')}>
                      <PathLegend />
                      <div className="flex flex-wrap gap-2">
                        <PathModeButton label={t('runs.overlay.path.all')} active={pathMode === 'all'} onClick={() => setPathMode('all')} />
                        <PathModeButton label={t('runs.overlay.path.failed')} active={pathMode === 'failed'} onClick={() => setPathMode('failed')} />
                        <PathModeButton label={t('runs.overlay.path.blocked')} active={pathMode === 'blocked'} onClick={() => setPathMode('blocked')} />
                      </div>
                    </OperatorSection>
                    {riskAndActiveTasks.length > 0 ? (
                      <OperatorSection title={t('runs.overlay.signals')}>
                        <div className="flex flex-wrap items-center gap-2">
                          <OperatorQuickFocusButton label={t('runs.overlay.prev')} hint="K" onClick={focusPreviousSignal} />
                          <OperatorQuickFocusButton label={t('runs.overlay.next')} hint="J" onClick={focusNextSignal} />
                          <span className="text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>
                            {tpl('runs.overlay.signal_count', { count: riskAndActiveTasks.length })}
                          </span>
                        </div>
                      </OperatorSection>
                    ) : null}
                    <OperatorSection title={t('runs.overlay.posture')}>
                      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                        <OperatorCountChip label={t('runs.overlay.running')} value={runStepCounts.running} tone="var(--revka-signal-live)" />
                        <OperatorCountChip label={t('runs.overlay.failed')} value={runStepCounts.failed} tone="var(--revka-status-danger)" />
                        <OperatorCountChip label={t('runs.overlay.done')} value={runStepCounts.completed} tone="var(--revka-signal-selected)" />
                        <OperatorCountChip label={t('runs.overlay.pending')} value={runStepCounts.pending} tone="var(--revka-text-faint)" />
                        <OperatorCountChip label={t('runs.overlay.skipped')} value={runStepCounts.skipped} tone="var(--revka-status-idle)" />
                      </div>
                    </OperatorSection>
                    {failingSteps.length > 0 || runningSteps.length > 0 || blockedTasks.length > 0 ? (
                      <OperatorSection title={t('runs.overlay.hotspots')}>
                        <div className="flex flex-wrap gap-2">
                          {failingSteps.slice(0, 3).map((step) => (
                            <OperatorSignalChip key={step.step_id} label={tpl('runs.overlay.fail_label', { id: step.step_id })} tone="var(--revka-status-danger)" onClick={() => focusTaskById(step.step_id)} />
                          ))}
                          {runningSteps.slice(0, 2).map((step) => (
                            <OperatorSignalChip key={step.step_id} label={tpl('runs.overlay.run_label', { id: step.step_id })} tone="var(--revka-signal-live)" onClick={() => focusTaskById(step.step_id)} />
                          ))}
                          {blockedTasks.slice(0, 3).map((task) => (
                            <OperatorSignalChip key={task.id} label={tpl('runs.overlay.block_label', { id: task.id })} tone="var(--revka-status-warning)" onClick={() => focusTaskById(task.id)} />
                          ))}
                        </div>
                      </OperatorSection>
                    ) : null}
                  </div>
                }
              />
            ) : (
              <Panel className="flex h-full items-center justify-center" variant="secondary">
                <StateMessage title={t('runs.none_selected.title')} description={t('runs.none_selected.desc')} />
              </Panel>
            )}
          </div>
        </div>

        {/* ---- RIGHT: Step inspector ---- */}
        <div className="min-h-0 overflow-y-auto">
          {/* Run summary strip */}
          {selectedRun ? (
            <Panel className="mb-3 p-3" variant="utility">
              <div className="flex items-center justify-between gap-2 text-xs">
                <StatusPill status={selectedRun.status} />
                <RunProgressMeta className="flex flex-wrap items-center justify-end gap-2" run={selectedRun} />
              </div>
              {selectedRun.started_at ? (
                <div className="mt-2 text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                  {tpl('runs.started_at', { time: formatLocalDateTime(selectedRun.started_at) })}
                </div>
              ) : null}
              {selectedRun.error ? (
                <div className="mt-2 rounded-[10px] border p-2 text-xs" style={{ borderColor: 'color-mix(in srgb, var(--revka-status-danger) 28%, transparent)', color: 'var(--revka-status-danger)' }}>
                  {selectedRun.error}
                </div>
              ) : null}
            </Panel>
          ) : null}

          {/* Approval card — shown when a step is awaiting human approval */}
          {selectedRun && pendingApprovalStep ? (
            <div className="mb-3">
              <ApprovalPanel
                runId={selectedRun.run_id}
                stepId={pendingApprovalStep.step_id}
                stepName={pendingApprovalStep.step_id}
                message={typeof pendingApprovalStep.output_data?.approval_message === 'string' ? pendingApprovalStep.output_data.approval_message : ''}
                approveKeywords={Array.isArray(pendingApprovalStep.output_data?.approve_keywords) ? (pendingApprovalStep.output_data!.approve_keywords as unknown[]).map(String) : undefined}
                rejectKeywords={Array.isArray(pendingApprovalStep.output_data?.reject_keywords) ? (pendingApprovalStep.output_data!.reject_keywords as unknown[]).map(String) : undefined}
                onResolved={() => {
                  void fetchWorkflowRun(selectedRun.run_id).then(setSelectedRun).catch(() => {});
                  void load();
                }}
              />
            </div>
          ) : null}

          {/* Step detail tabs */}
          <Panel className="p-3" variant="secondary">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--revka-text-faint)' }}>
                {selectedTask ? selectedTask.name || selectedTask.id : t('runs.inspector.title')}
              </span>
              <div className="revka-tab-strip" role="tablist">
                {(['summary', 'output', 'tools', 'transcript'] as const).map((id) => (
                  <button
                    key={id}
                    type="button"
                    role="tab"
                    aria-selected={detailTab === id}
                    className="revka-tab-button"
                    data-active={String(detailTab === id)}
                    onClick={() => setDetailTab(id)}
                  >
                    {tabLabels[id]}
                  </button>
                ))}
              </div>
            </div>

            <div className="mt-3">
              {detailTab === 'summary' ? (
                !selectedTask ? (
                  <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.select_node')}</div>
                ) : (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>{selectedTask.name || selectedTask.id}</span>
                      {selectedStep ? <StatusPill status={selectedStep.status} /> : null}
                    </div>
                    <div className="text-xs uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>{selectedTask.type}</div>
                    <p className="text-sm leading-6" style={{ color: 'var(--revka-text-secondary)' }}>{selectedTask.description || t('runs.detail.no_description')}</p>
                    <div className="text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
                      {tpl('runs.detail.depends_on', { list: selectedTask.depends_on.join(', ') || t('runs.detail.depends_none') })}
                    </div>
                    {selectedStep ? (
                      <>
                        <div className="text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
                          {tpl('runs.detail.agent', { type: selectedStep.agent_type || t('runs.detail.agent_na'), role: selectedStep.role ? ` / ${selectedStep.role}` : '' })}
                        </div>
                        {selectedStep.skills?.length ? (
                          <div className="text-xs" style={{ color: 'var(--revka-text-secondary)' }}>{tpl('runs.detail.skills', { list: selectedStep.skills.join(', ') })}</div>
                        ) : null}
                        {selectedStep.output_preview ? (
                          <div className="rounded-[10px] border p-2 text-xs leading-6" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-secondary)' }}>
                            {selectedStep.output_preview}
                          </div>
                        ) : null}
                        {selectedConditionalResolution ? (
                          <RunDetailCard title="Resolved Branch" tone="success">
                            <div className="space-y-1">
                              <div>{selectedConditionalResolution.label}{selectedConditionalResolution.goto ? ` -> ${selectedConditionalResolution.goto}` : ''}</div>
                              {selectedConditionalResolution.condition ? <div>Condition: {selectedConditionalResolution.condition}</div> : null}
                              {selectedConditionalResolution.valueExpr ? <div>Value: {selectedConditionalResolution.valueExpr}</div> : null}
                              {selectedConditionalResolution.output ? <div>Output: {selectedConditionalResolution.output}</div> : null}
                            </div>
                          </RunDetailCard>
                        ) : null}
                        {selectedFailureReason ? (
                          <RunDetailCard title="Failure Detail" tone="danger">
                            {selectedFailureReason}
                          </RunDetailCard>
                        ) : null}
                      </>
                    ) : null}
                    {selectedTask && selectedRun ? (
                      <div className="flex flex-wrap gap-3 pt-1 text-xs">
                        <Link to={`/dashboard?run=${encodeURIComponent(selectedRun.run_id)}&node=${encodeURIComponent(selectedTask.id)}`} style={{ color: 'var(--revka-signal-network)' }}>
                          {t('runs.detail.dashboard_link')}
                        </Link>
                        {selectedDefinition ? (
                          <Link to={`/workflows?workflow=${encodeURIComponent(selectedDefinition.kref)}&node=${encodeURIComponent(selectedTask.id)}`} style={{ color: 'var(--revka-signal-network)' }}>
                            {t('runs.detail.definition_link')}
                          </Link>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                )
              ) : null}

              {detailTab === 'output' ? (
                !selectedStep ? (
                  <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.select_step')}</div>
                ) : (
                  <div className="space-y-3">
                    {selectedStep.input_data && hasStructuredData(selectedStep.input_data) ? (
                      <JsonDetailCard title="Resolved Inputs" value={selectedStep.input_data} />
                    ) : null}
                    {selectedStep.output_data && hasStructuredData(selectedStep.output_data) ? (
                      <JsonDetailCard title="Structured Output" value={selectedStep.output_data} />
                    ) : null}
                    {selectedStep.output_preview ? (
                      <div className="rounded-[10px] border p-3" style={{ borderColor: 'var(--revka-border-soft)' }}>
                        <div className="mb-2 flex items-center justify-between gap-2">
                          <div className="text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.step_output')}</div>
                          {selectedStep.artifact_path ? (
                            <button
                              type="button"
                              onClick={() => setViewerArtifact({
                                kref: `step:${selectedStep.step_id}`,
                                name: selectedStep.step_id,
                                location: selectedStep.artifact_path ?? '',
                                revision_kref: '',
                                item_kref: '',
                                deprecated: false,
                              })}
                              className="inline-flex items-center gap-1 rounded-[6px] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider transition"
                              style={{
                                background: 'var(--revka-bg-elevated)',
                                color: 'var(--revka-text-secondary)',
                                border: '1px solid var(--revka-border-strong)',
                              }}
                            >
                              <Eye className="h-3 w-3" />
                              View full
                            </button>
                          ) : null}
                        </div>
                        <pre className="whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--revka-text-secondary)', fontFamily: 'var(--pc-font-mono)' }}>{selectedStep.output_preview}</pre>
                      </div>
                    ) : null}
                    {selectedActivity?.last_message ? (
                      <div className="rounded-[10px] border p-3" style={{ borderColor: 'var(--revka-border-soft)' }}>
                        <div className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>
                          <MessageSquareText className="h-3 w-3" /> {t('runs.detail.agent_output')}
                        </div>
                        <pre className="whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--revka-text-secondary)', fontFamily: 'var(--pc-font-mono)' }}>{selectedActivity.last_message}</pre>
                      </div>
                    ) : null}
                    {!selectedStep.output_preview && !selectedActivity?.last_message && !hasStructuredData(selectedStep.input_data) && !hasStructuredData(selectedStep.output_data) ? (
                      <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.no_output')}</div>
                    ) : null}
                  </div>
                )
              ) : null}

              {detailTab === 'tools' ? (
                !selectedStep ? (
                  <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.select_step')}</div>
                ) : activityLoading ? (
                  <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.loading')}</div>
                ) : selectedActivity ? (
                  <div className="space-y-2">
                    <div className="grid gap-2 grid-cols-2 text-xs">
                      <div className="rounded-[10px] border p-2" style={{ borderColor: 'var(--revka-border-soft)' }}>
                        <div className="text-[10px] uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.tools_calls')}</div>
                        <div className="mt-1 text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>{selectedActivity.tool_call_count ?? selectedActivity.recent_tools?.length ?? 0}</div>
                      </div>
                      <div className="rounded-[10px] border p-2" style={{ borderColor: 'var(--revka-border-soft)' }}>
                        <div className="text-[10px] uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.tools_errors')}</div>
                        <div className="mt-1 text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>{selectedActivity.error_count ?? 0}</div>
                      </div>
                    </div>
                    {(selectedActivity.recent_tools ?? []).slice(0, 8).map((tool, index) => (
                      <ToolCallCard key={`${tool.name ?? tool.kind}-${tool.ts ?? index}`} tool={tool} />
                    ))}
                    {(selectedActivity.recent_tools?.length ?? 0) === 0 ? (
                      <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.no_tools')}</div>
                    ) : null}
                  </div>
                ) : (
                  <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.no_activity')}</div>
                )
              ) : null}

              {detailTab === 'transcript' ? (
                !selectedStep ? (
                  <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.select_step')}</div>
                ) : selectedStep.transcript?.length ? (
                  <div className="space-y-2">
                    {selectedStep.transcript.map((entry, index) => (
                      <div key={`${entry.round}-${entry.speaker}-${index}`} className="rounded-[10px] border p-2" style={{ borderColor: 'var(--revka-border-soft)' }}>
                        <div className="flex items-center justify-between gap-2 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>
                          <span>{entry.speaker}</span>
                          <span>R{entry.round}</span>
                        </div>
                        <pre className="mt-1 whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--revka-text-secondary)', fontFamily: 'var(--pc-font-mono)' }}>{entry.content}</pre>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>{t('runs.detail.no_transcript')}</div>
                )
              ) : null}
            </div>
          </Panel>

          {/* Step timeline */}
          {selectedRun ? (
            <Panel className="mt-3 p-3" variant="utility">
              <span className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--revka-text-faint)' }}>
                {tpl('runs.timeline', { count: selectedRun.steps.length })}
              </span>
              <div className="mt-2 space-y-1">
                {selectedRun.steps.map((step) => {
                  const failureReason = stepFailureReason(step);
                  const branch = selectedDefinitionTasks.find((task) => task.id === step.step_id)?.type === 'conditional'
                    ? conditionalResolution(step)
                    : null;
                  return (
                  <button
                    key={step.step_id}
                    type="button"
                    onClick={() => setSelectedTask(selectedDefinitionTasks.find((task) => task.id === step.step_id) ?? null)}
                    className="flex w-full items-center justify-between gap-2 rounded-[10px] px-3 py-2 text-left transition"
                    style={{
                      background: selectedTask?.id === step.step_id
                        ? 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 18%, transparent))'
                        : 'transparent',
                    }}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm" style={{ color: 'var(--revka-text-primary)' }}>{step.step_id}</span>
                      {failureReason ? (
                        <span className="mt-0.5 block truncate text-[11px]" style={{ color: 'var(--revka-status-danger)' }}>{failureReason}</span>
                      ) : branch ? (
                        <span className="mt-0.5 block truncate text-[11px]" style={{ color: 'var(--revka-text-secondary)' }}>
                          {branch.label}{branch.goto ? ` -> ${branch.goto}` : ''}{branch.output ? ` · ${compactDetail(branch.output, 90)}` : ''}
                        </span>
                      ) : null}
                    </span>
                    <StatusPill status={step.status} />
                  </button>
                  );
                })}
              </div>
            </Panel>
          ) : null}
        </div>
      </div>
    </div>
    {viewerArtifact ? (
      <ArtifactViewerModal
        artifact={viewerArtifact}
        onClose={() => setViewerArtifact(null)}
      />
    ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Helper components                                                  */
/* ------------------------------------------------------------------ */

function PathModeButton({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-[12px] border px-3 py-2 text-xs font-semibold transition-colors"
      style={{
        borderColor: active ? 'var(--revka-border-strong)' : 'var(--revka-border-soft)',
        background: active ? 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 18%, transparent))' : 'color-mix(in srgb, var(--revka-bg-panel-strong) 92%, transparent)',
        color: active ? 'var(--revka-text-primary)' : 'var(--revka-text-secondary)',
      }}
    >
      {label}
    </button>
  );
}

function PathLegend() {
  const { t } = useT();
  return (
    <div className="flex flex-wrap gap-2">
      <OperatorLegendChip label={t('runs.overlay.legend_running')} tone="var(--revka-signal-live)" />
      <OperatorLegendChip label={t('runs.overlay.legend_failure')} tone="var(--revka-status-danger)" />
      <OperatorLegendChip label={t('runs.overlay.legend_blocked')} tone="var(--revka-status-warning)" />
      <OperatorLegendChip label={t('runs.overlay.legend_skipped')} tone="var(--revka-status-idle)" />
      <OperatorLegendChip label={t('runs.overlay.legend_gate')} tone="var(--revka-signal-network)" />
    </div>
  );
}

function RunDetailCard({
  title,
  tone = 'neutral',
  children,
}: {
  title: string;
  tone?: 'neutral' | 'success' | 'danger';
  children: ReactNode;
}) {
  const toneColor = tone === 'danger'
    ? 'var(--revka-status-danger)'
    : tone === 'success'
      ? 'var(--revka-signal-selected)'
      : 'var(--revka-text-faint)';
  const background = tone === 'neutral'
    ? 'transparent'
    : `color-mix(in srgb, ${toneColor} 10%, transparent)`;
  return (
    <div
      className="rounded-[10px] border p-2 text-xs leading-6"
      style={{
        borderColor: tone === 'neutral'
          ? 'var(--revka-border-soft)'
          : `color-mix(in srgb, ${toneColor} 34%, transparent)`,
        background,
        color: tone === 'danger' ? 'var(--revka-status-danger)' : 'var(--revka-text-secondary)',
      }}
    >
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: toneColor }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function JsonDetailCard({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="rounded-[10px] border p-3" style={{ borderColor: 'var(--revka-border-soft)' }}>
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>
        {title}
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap text-xs leading-6" style={{ color: 'var(--revka-text-secondary)', fontFamily: 'var(--pc-font-mono)' }}>
        {jsonPreview(value)}
      </pre>
    </div>
  );
}

function ToolCallCard({ tool }: { tool: AgentToolCall }) {
  const { t } = useT();
  const detail = (() => {
    try {
      const args = typeof tool.args === 'string' ? JSON.parse(tool.args || '{}') : (tool.args || {});
      if (tool.name === 'Bash' || tool.name === 'execute_command') return args.command || tool.command || '';
      if (tool.name === 'WebSearch' || tool.name === 'web_search') return args.query || '';
      if (tool.name === 'WebFetch' || tool.name === 'web_fetch') return args.url || '';
      if (tool.name === 'Read' || tool.name === 'Write' || tool.name === 'Edit') return args.file_path || args.path || '';
      return '';
    } catch {
      return '';
    }
  })();

  const statusColor = tool.status === 'failed'
    ? 'var(--revka-status-danger)'
    : tool.status === 'completed'
      ? 'var(--revka-signal-selected)'
      : 'var(--revka-status-warning)';

  return (
    <div className="rounded-[10px] border p-2" style={{ borderColor: 'var(--revka-border-soft)' }}>
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Wrench className="h-3 w-3 shrink-0" style={{ color: 'var(--revka-signal-network)' }} />
          <span className="truncate text-sm" style={{ color: 'var(--revka-text-primary)' }}>{tool.name || tool.kind || t('runs.detail.tool_default')}</span>
        </div>
        <span className="text-[10px] font-semibold uppercase" style={{ color: statusColor }}>{tool.status || t('runs.detail.tool_status_ok')}</span>
      </div>
      {detail ? <div className="mt-1 truncate text-xs" style={{ color: 'var(--revka-text-secondary)' }}>{detail}</div> : null}
      {tool.error ? <div className="mt-1 text-xs" style={{ color: 'var(--revka-status-danger)' }}>{tool.error}</div> : null}
    </div>
  );
}
