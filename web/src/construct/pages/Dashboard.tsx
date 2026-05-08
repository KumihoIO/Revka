import { RefreshCw, Workflow } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useT } from '@/construct/hooks/useT';
import { parseWorkflowYaml, type TaskDefinition } from '@/components/workflows/yamlSync';
import type {
  AuditVerifyResponse,
  ChannelDetail,
  CostSummary,
  Session,
  StatusResponse,
  WorkflowDashboard,
  WorkflowDefinition,
  WorkflowRunDetail,
} from '@/types/api';
import {
  fetchWorkflowDashboard,
  fetchWorkflowRun,
  getChannels,
  getCost,
  getSessions,
  getStatus,
  verifyAuditChain,
} from '@/lib/api';
import {
  AgentRailCard,
  CommandBandCard,
  DashboardMetricStrip,
  RecentRunsRailCard,
  RiskRailCard,
} from '../components/orchestration/DashboardCards';
import {
  OperatorCountChip,
  OperatorQuickFocusButton,
  OperatorSection,
  OperatorSignalChip,
} from '../components/orchestration/GraphOverlay';
import { RunSummaryCard, SelectedTaskCard } from '../components/orchestration/InspectorCards';
import RunFocusBanner from '../components/orchestration/RunFocusBanner';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import WorkflowDagWorkspace from '../components/workflows/WorkflowDagWorkspace';
import { deriveBlockedTaskIds, toStepRunInfo } from '../lib/orchestration';

export default function Dashboard() {
  const { t, tpl } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<WorkflowDashboard | null>(null);
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [cost, setCost] = useState<CostSummary | null>(null);
  const [audit, setAudit] = useState<AuditVerifyResponse | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [channels, setChannels] = useState<ChannelDetail[]>([]);
  const [selectedRun, setSelectedRun] = useState<WorkflowRunDetail | null>(null);
  const [selectedTask, setSelectedTask] = useState<TaskDefinition | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shouldScrollToWorkspace, setShouldScrollToWorkspace] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const workspaceRef = useRef<HTMLDivElement | null>(null);

  const loadDashboard = () => {
    setRefreshing(true);
    return Promise.all([
      fetchWorkflowDashboard(),
      getStatus(),
      getCost(),
      verifyAuditChain(),
      getSessions(),
      getChannels(),
    ])
      .then(([dashboard, runtimeStatus, runtimeCost, auditVerify, activeSessions, channelList]) => {
        setData(dashboard);
        setStatus(runtimeStatus);
        setCost(runtimeCost);
        setAudit(auditVerify);
        setSessions(activeSessions);
        setChannels(channelList);

        const requestedRun = searchParams.get('run');
        const nextRunId = requestedRun ?? dashboard.recent_runs[0]?.run_id ?? null;
        if (nextRunId) {
          return fetchWorkflowRun(nextRunId).then(setSelectedRun);
        }
        return null;
      })
      .catch((err) => setError(err.message))
      .finally(() => setRefreshing(false));
  };

  useEffect(() => {
    loadDashboard();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Audit-chain check is owned by Header at a 60s cadence; Dashboard has
  // to refresh on the same beat or the trust badge here drifts out of
  // sync with the one in the header. Polling just `verifyAuditChain` is
  // cheap (one HEAD-equivalent on the audit log file); we don't reload
  // the rest of the dashboard. Also refresh on window focus so coming
  // back to a tab after a long idle catches up immediately.
  useEffect(() => {
    let cancelled = false;
    const refreshAudit = () => {
      verifyAuditChain()
        .then((res) => {
          if (!cancelled) setAudit(res);
        })
        .catch((err) => {
          if (!cancelled) {
            setAudit({ verified: false, error: err instanceof Error ? err.message : String(err) });
          }
        });
    };
    const id = window.setInterval(refreshAudit, 60_000);
    const onFocus = () => refreshAudit();
    window.addEventListener('focus', onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(id);
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  useEffect(() => {
    if (!shouldScrollToWorkspace || !selectedRun) return;
    workspaceRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    workspaceRef.current?.focus({ preventScroll: true });
    setShouldScrollToWorkspace(false);
  }, [selectedRun, shouldScrollToWorkspace]);

  const selectedDefinition = useMemo(() => {
    if (!data || !selectedRun) return null;
    const workflowName = selectedRun.workflow_name.toLowerCase();
    return data.definitions.find((definition: WorkflowDefinition) => definition.name.toLowerCase() === workflowName) ?? null;
  }, [data, selectedRun]);

  const selectedDefinitionTasks = useMemo(
    () => (selectedDefinition ? parseWorkflowYaml(selectedDefinition.definition) : []),
    [selectedDefinition],
  );

  const stepResults = useMemo(() => {
    if (!selectedRun) return {};
    return Object.fromEntries(selectedRun.steps.map((step) => [step.step_id, toStepRunInfo(step)]));
  }, [selectedRun]);

  const selectedStep = useMemo(
    () => (selectedTask && selectedRun ? selectedRun.steps.find((step) => step.step_id === selectedTask.id) ?? null : null),
    [selectedRun, selectedTask],
  );

  const runStepCounts = useMemo(() => {
    const counts = {
      pending: 0,
      running: 0,
      completed: 0,
      failed: 0,
      skipped: 0,
    };
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

  const signalTasks = useMemo(
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

  useEffect(() => {
    if (!selectedRun) return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('run', selectedRun.run_id);
      if (selectedTask?.id) {
        next.set('node', selectedTask.id);
      } else {
        next.delete('node');
      }
      return next;
    }, { replace: true });
  }, [selectedRun?.run_id, selectedTask?.id, setSearchParams]);

  useEffect(() => {
    const requestedNode = searchParams.get('node');
    if (!requestedNode) {
      setSelectedTask(null);
      return;
    }
    setSelectedTask(selectedDefinitionTasks.find((task) => task.id === requestedNode) ?? null);
  }, [searchParams, selectedDefinitionTasks]);

  const activeSessionCount = useMemo(
    () => sessions.filter((session) => session.status === 'active').length,
    [sessions],
  );

  const activeChannelCount = useMemo(
    () => channels.filter((channel) => channel.status === 'active').length,
    [channels],
  );

  const degradedComponentCount = useMemo(
    () => Object.values(status?.health.components ?? {}).filter((component) => component.status !== 'healthy' && component.status !== 'ok').length,
    [status?.health.components],
  );

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)) return;
      if (signalTasks.length === 0) return;

      const currentIndex = signalTasks.findIndex((task) => task.id === selectedTask?.id);

      if (event.key === 'j') {
        event.preventDefault();
        const nextIndex = currentIndex >= 0
          ? (currentIndex + 1) % signalTasks.length
          : 0;
        setSelectedTask(signalTasks[nextIndex] ?? null);
      }

      if (event.key === 'k') {
        event.preventDefault();
        const nextIndex = currentIndex >= 0
          ? (currentIndex - 1 + signalTasks.length) % signalTasks.length
          : signalTasks.length - 1;
        setSelectedTask(signalTasks[nextIndex] ?? null);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedTask?.id, signalTasks]);

  const focusTaskById = (taskId: string) => {
    setSelectedTask(selectedDefinitionTasks.find((task) => task.id === taskId) ?? null);
  };

  const focusPreviousSignal = () => {
    const currentIndex = signalTasks.findIndex((task) => task.id === selectedTask?.id);
    const nextIndex = currentIndex >= 0
      ? (currentIndex - 1 + signalTasks.length) % signalTasks.length
      : signalTasks.length - 1;
    setSelectedTask(signalTasks[nextIndex] ?? null);
  };

  const focusNextSignal = () => {
    const currentIndex = signalTasks.findIndex((task) => task.id === selectedTask?.id);
    const nextIndex = currentIndex >= 0
      ? (currentIndex + 1) % signalTasks.length
      : 0;
    setSelectedTask(signalTasks[nextIndex] ?? null);
  };

  return (
    <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3 lg:h-[calc(100vh-6rem)]">
      <PageHeader
        kicker={t('dashboard.kicker')}
        title={t('nav.dashboard')}
        actions={(
          <button
            type="button"
            className="construct-button"
            onClick={() => void loadDashboard()}
            disabled={refreshing}
          >
            <RefreshCw className={`h-4 w-4 ${refreshing ? 'animate-spin' : ''}`} />
            {t('common.refresh')}
          </button>
        )}
      />

      {/* Two-column layout — workflow workspace on the left, single
          stacked rail on the right with the operator's reading order:
          Risk → Agent → Command → Recent Runs. The rail is wider than
          the previous 22rem (now 24rem) and is explicitly height-capped
          via `max-h` so the inner overflow-y-auto reliably engages even
          when the document body itself is scrollable — that was the
          truncation bug in the earlier attempt: the rail just kept
          extending below the viewport instead of giving us a scrollbar. */}
      <div className="grid gap-4 grid-cols-1 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_24rem] lg:[grid-template-rows:minmax(0,1fr)]">
        <Panel className="flex flex-col p-5 lg:min-h-0">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="construct-kicker">{t('dashboard.live_orchestration_pane')}</div>
              <h3 className="mt-2 text-lg font-semibold">
                {selectedRun ? `${selectedRun.workflow_name} / ${selectedRun.run_id.slice(0, 8)}` : t('dashboard.active_workflow_posture')}
              </h3>
            </div>
            <Workflow className="h-5 w-5" style={{ color: 'var(--construct-signal-network)' }} />
          </div>

          <DashboardMetricStrip
            definitionsCount={data?.definitions_count}
            activeRuns={data?.active_runs}
            totalRuns={data?.total_runs}
            error={error}
          />

          {selectedDefinition ? (
            <div className="mt-3">
              <Link
                to={`/workflows?workflow=${encodeURIComponent(selectedDefinition.kref)}${selectedTask ? `&node=${encodeURIComponent(selectedTask.id)}` : ''}`}
                className="inline-flex items-center gap-2 text-sm"
                style={{ color: 'var(--construct-signal-network)' }}
              >
                {t('dashboard.open_workflow_definition')}
              </Link>
            </div>
          ) : null}

          <RunFocusBanner run={selectedRun} active={shouldScrollToWorkspace} label={t('dashboard.selected_run')} />

          <div ref={workspaceRef} tabIndex={-1} className="mt-6 grid gap-4 outline-none lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_22rem] lg:[grid-template-rows:minmax(0,1fr)]">
            <div className="flex min-h-0 flex-col">
            <WorkflowDagWorkspace
              fill
              definition={selectedDefinition?.definition ?? ''}
              stepResults={stepResults}
              onSelectTask={setSelectedTask}
              selectedTaskId={selectedTask?.id}
              blockedTaskIds={blockedTaskIds}
              failingTaskIds={failingSteps.map((step) => step.step_id)}
              runningTaskIds={runningSteps.map((step) => step.step_id)}
              overlay={selectedRun ? (
                <div className="space-y-2">
                  <OperatorSection title={t('dashboard.run_posture')}>
                    <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                      <OperatorCountChip label={t('dashboard.count.running')} value={runStepCounts.running} tone="var(--construct-signal-live)" compact />
                      <OperatorCountChip label={t('dashboard.count.failed')} value={runStepCounts.failed} tone="var(--construct-status-danger)" compact />
                      <OperatorCountChip label={t('dashboard.count.completed')} value={runStepCounts.completed} tone="var(--construct-status-success)" compact />
                      <OperatorCountChip label={t('dashboard.count.pending')} value={runStepCounts.pending} tone="var(--construct-text-faint)" compact />
                      <OperatorCountChip label={t('dashboard.count.skipped')} value={runStepCounts.skipped} tone="var(--construct-status-idle)" compact />
                    </div>
                  </OperatorSection>
                  {signalTasks.length > 0 ? (
                    <OperatorSection title={t('dashboard.signal_focus')}>
                      <div className="flex flex-wrap items-center gap-2">
                        <OperatorQuickFocusButton label={t('dashboard.prev_signal')} hint="K" onClick={focusPreviousSignal} />
                        <OperatorQuickFocusButton label={t('dashboard.next_signal')} hint="J" onClick={focusNextSignal} />
                        <span className="text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--construct-text-faint)' }}>
                          {tpl('dashboard.intervention_points', { count: signalTasks.length })}
                        </span>
                      </div>
                    </OperatorSection>
                  ) : null}
                  {failingSteps.length > 0 || runningSteps.length > 0 || blockedTasks.length > 0 ? (
                    <OperatorSection title={t('dashboard.hotspots')}>
                      <div className="flex flex-wrap gap-2">
                        {failingSteps.slice(0, 3).map((step) => (
                          <OperatorSignalChip
                            key={step.step_id}
                            label={tpl('dashboard.label.failure', { id: step.step_id })}
                            tone="var(--construct-status-danger)"
                            onClick={() => focusTaskById(step.step_id)}
                          />
                        ))}
                        {runningSteps.slice(0, 2).map((step) => (
                          <OperatorSignalChip
                            key={step.step_id}
                            label={tpl('dashboard.label.running', { id: step.step_id })}
                            tone="var(--construct-signal-live)"
                            onClick={() => focusTaskById(step.step_id)}
                          />
                        ))}
                        {blockedTasks.slice(0, 3).map((task) => (
                          <OperatorSignalChip
                            key={task.id}
                            label={tpl('dashboard.label.blocked', { id: task.id })}
                            tone="var(--construct-status-warning)"
                            onClick={() => focusTaskById(task.id)}
                          />
                        ))}
                      </div>
                    </OperatorSection>
                  ) : null}
                </div>
              ) : undefined}
            />
            </div>

            <div className="space-y-4 lg:min-h-0 lg:overflow-y-auto lg:pr-1">
              <RunSummaryCard
                run={selectedRun}
                workflowHref={selectedDefinition ? `/workflows?workflow=${encodeURIComponent(selectedDefinition.kref)}${selectedTask ? `&node=${encodeURIComponent(selectedTask.id)}` : ''}` : undefined}
              />

              {selectedRun ? (
                <div className="px-1">
                  <Link
                    to={`/runs?run=${encodeURIComponent(selectedRun.run_id)}&workflow=${encodeURIComponent(selectedRun.workflow_name)}${selectedTask ? `&node=${encodeURIComponent(selectedTask.id)}` : ''}`}
                    className="inline-flex items-center gap-2 text-sm"
                    style={{ color: 'var(--construct-signal-network)' }}
                  >
                    {t('dashboard.open_full_run_workspace')}
                  </Link>
                </div>
              ) : null}

              <SelectedTaskCard
                title={t('dashboard.selected_step')}
                task={selectedTask}
                step={selectedStep}
                footer={selectedTask && selectedRun ? (
                  <div className="flex flex-wrap gap-3 text-xs">
                    <Link
                      to={`/runs?run=${encodeURIComponent(selectedRun.run_id)}&workflow=${encodeURIComponent(selectedRun.workflow_name)}&node=${encodeURIComponent(selectedTask.id)}`}
                      style={{ color: 'var(--construct-signal-network)' }}
                    >
                      {t('dashboard.inspect_node_in_runs')}
                    </Link>
                    {selectedDefinition ? (
                      <Link
                        to={`/workflows?workflow=${encodeURIComponent(selectedDefinition.kref)}&node=${encodeURIComponent(selectedTask.id)}`}
                        style={{ color: 'var(--construct-signal-network)' }}
                      >
                        {t('dashboard.open_definition_node')}
                      </Link>
                    ) : null}
                  </div>
                ) : undefined}
                emptyText={t('dashboard.select_dag_node')}
              />

              <Panel className="p-4" variant="utility">
                <div className="construct-kicker">{t('dashboard.priority_timeline')}</div>
                <div className="mt-3 space-y-2">
                  {selectedRun?.steps.map((step) => (
                    <button
                      key={step.step_id}
                      type="button"
                      onClick={() => focusTaskById(step.step_id)}
                      className="block w-full rounded-[12px] border p-3 text-left"
                      style={{ borderColor: selectedTask?.id === step.step_id ? 'var(--construct-border-strong)' : 'var(--construct-border-soft)' }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium" style={{ color: 'var(--construct-text-primary)' }}>
                          {step.step_id}
                        </span>
                        <span
                          className="text-[10px] font-semibold uppercase tracking-[0.14em]"
                          style={{
                            color: toStepRunInfo(step).status === 'failed'
                              ? 'var(--construct-status-danger)'
                              : toStepRunInfo(step).status === 'running'
                                ? 'var(--construct-signal-live)'
                                : blockedTaskIds.includes(step.step_id)
                                  ? 'var(--construct-status-warning)'
                                  : 'var(--construct-text-faint)',
                          }}
                        >
                          {blockedTaskIds.includes(step.step_id) && toStepRunInfo(step).status === 'pending'
                            ? t('dashboard.status.blocked')
                            : toStepRunInfo(step).status}
                        </span>
                      </div>
                      {step.output_preview ? (
                        <div className="mt-2 line-clamp-2 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
                          {step.output_preview}
                        </div>
                      ) : null}
                    </button>
                  )) ?? <div className="text-sm" style={{ color: 'var(--construct-text-faint)' }}>{t('dashboard.no_run_selected')}</div>}
                </div>
              </Panel>
            </div>
          </div>
        </Panel>

        {/* Right rail — single column, 4 cards, reading order is the
            operator's posture sequence. Three things keep this stable:
            (1) `lg:max-h-[calc(100vh-9rem)]` constrains rail height so
            overflow-y-auto can engage. (2) `[&>*]:shrink-0` on direct
            children — without it, flex-shrink defaults to 1 and the
            cards get squeezed to fit the rail. Combined with
            `.construct-panel { overflow: hidden }` (from theme.css),
            squeezed cards silently clip their inner content (Risk Rail
            losing Component health, Agent Rail losing session activity).
            (3) The `pr-1` reserves space for the scrollbar so it
            doesn't overlap card borders. */}
        <div className="flex flex-col gap-4 lg:max-h-[calc(100vh-9rem)] lg:min-h-0 lg:overflow-y-auto lg:pr-1 [&>*]:shrink-0">
          <RiskRailCard
            audit={audit}
            cost={cost}
            degradedComponentCount={degradedComponentCount}
          />
          <AgentRailCard
            sessions={sessions}
            channels={channels}
            activeSessionCount={activeSessionCount}
            activeChannelCount={activeChannelCount}
          />
          <CommandBandCard
            selectedRunStatus={selectedRun?.status}
            audit={audit}
            provider={status?.provider}
            model={status?.model}
          />
          <RecentRunsRailCard
            runs={data?.recent_runs ?? []}
            selectedRunId={selectedRun?.run_id}
            onSelectRun={(runId) => {
              setSelectedTask(null);
              setShouldScrollToWorkspace(true);
              fetchWorkflowRun(runId).then(setSelectedRun).catch((err) => setError(err.message));
            }}
            footer={(
              <Link to="/runs" className="inline-flex items-center gap-2 text-sm" style={{ color: 'var(--construct-signal-network)' }}>
                {t('dashboard.open_all_runs')}
              </Link>
            )}
          />
        </div>
      </div>
    </div>
  );
}
