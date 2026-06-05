import { ChevronDown, Pencil, Play, Plus, Power, RefreshCw, Trash2, Workflow } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useT } from '@/revka/hooks/useT';
import { parseWorkflowYaml, type TaskDefinition } from '@/revka/components/workflows/yamlSync';
import WorkflowEditor from '@/revka/components/workflows/WorkflowEditor';
import type { WorkflowCreateRequest, WorkflowDefinition, WorkflowRunSummary, WorkflowUpdateRequest } from '@/types/api';
import { ApiError, createWorkflow, deleteWorkflow, fetchWorkflowByRevisionKref, fetchWorkflowRuns, fetchWorkflows, runWorkflow, toggleWorkflowDeprecation, updateWorkflow } from '@/lib/api';
import {
  SelectedTaskCard,
  WorkflowMetadataCard,
} from '../components/orchestration/InspectorCards';
import Panel from '../components/ui/Panel';
import Notice from '../components/ui/Notice';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';
import WorkflowDagWorkspace from '../components/workflows/WorkflowDagWorkspace';

export default function Workflows() {
  const { t, tpl } = useT();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [definitions, setDefinitions] = useState<WorkflowDefinition[]>([]);
  const [runs, setRuns] = useState<WorkflowRunSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [definitionLoading, setDefinitionLoading] = useState(false);
  const [definitionError, setDefinitionError] = useState<string | null>(null);
  const [selectedWorkflowKref, setSelectedWorkflowKref] = useState<string | null>(null);
  const [selectedTask, setSelectedTask] = useState<TaskDefinition | null>(null);
  const [editorMode, setEditorMode] = useState<'create' | 'edit' | 'duplicate' | null>(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);
  const [workflowDropdownOpen, setWorkflowDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const load = async () => {
    setLoading(true);
    return Promise.all([
      fetchWorkflows(true, false),
      fetchWorkflowRuns(40),
    ])
      .then(([workflowDefinitions, workflowRuns]) => {
        setDefinitions(workflowDefinitions);
        setRuns(workflowRuns);
        const requestedWorkflow = searchParams.get('workflow');
        const matchedWorkflow = requestedWorkflow
          ? workflowDefinitions.find((workflow) => workflow.kref === requestedWorkflow || workflow.name.toLowerCase() === requestedWorkflow.toLowerCase())
          : null;
        setSelectedWorkflowKref((current) => current ?? matchedWorkflow?.kref ?? workflowDefinitions[0]?.kref ?? null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  };

  const applySavedWorkflow = (workflow: WorkflowDefinition) => {
    setDefinitions((current) => {
      const index = current.findIndex((entry) => entry.kref === workflow.kref);
      if (index === -1) return [workflow, ...current];

      const next = [...current];
      next[index] = { ...next[index], ...workflow };
      return next;
    });
  };

  const refreshInBackground = () => {
    void load();
  };

  useEffect(() => {
    load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Close dropdown on outside click
  useEffect(() => {
    if (!workflowDropdownOpen) return;
    const handler = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as HTMLElement)) {
        setWorkflowDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [workflowDropdownOpen]);

  const selectedWorkflow = useMemo(
    () => definitions.find((workflow) => workflow.kref === selectedWorkflowKref) ?? definitions[0] ?? null,
    [definitions, selectedWorkflowKref],
  );

  const buildRunWorkspaceHref = useCallback((runId?: string | null, nodeId?: string | null): string => {
    const next = new URLSearchParams();
    if (selectedWorkflow?.name) {
      next.set('workflow', selectedWorkflow.name);
    } else if (selectedWorkflow?.kref) {
      next.set('workflow', selectedWorkflow.kref);
    }
    if (runId) next.set('run', runId);
    if (nodeId) next.set('node', nodeId);

    const requestedPathMode = searchParams.get('path');
    if (requestedPathMode === 'all' || requestedPathMode === 'failed' || requestedPathMode === 'blocked') {
      next.set('path', requestedPathMode);
    }

    const query = next.toString();
    return query ? `/runs?${query}` : '/runs';
  }, [searchParams, selectedWorkflow?.kref, selectedWorkflow?.name]);

  useEffect(() => {
    if (searchParams.get('tab') !== 'runs' || !selectedWorkflow) return;
    navigate(buildRunWorkspaceHref(
      searchParams.get('run'),
      searchParams.get('node') ?? selectedTask?.id,
    ), { replace: true });
  }, [buildRunWorkspaceHref, navigate, searchParams, selectedTask?.id, selectedWorkflow]);

  useEffect(() => {
    if (!selectedWorkflow || selectedWorkflow.definition) {
      setDefinitionLoading(false);
      setDefinitionError(null);
      return;
    }
    if (!selectedWorkflow.kref.startsWith('kref://') || selectedWorkflow.revision_number <= 0) {
      setDefinitionLoading(false);
      return;
    }

    let cancelled = false;
    setDefinitionLoading(true);
    setDefinitionError(null);

    fetchWorkflowByRevisionKref(`${selectedWorkflow.kref}?r=${selectedWorkflow.revision_number}`)
      .then((workflow) => {
        if (cancelled) return;
        setDefinitions((current) =>
          current.map((entry) => entry.kref === selectedWorkflow.kref ? { ...entry, ...workflow } : entry),
        );
      })
      .catch((err) => {
        if (!cancelled) setDefinitionError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setDefinitionLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedWorkflow?.definition, selectedWorkflow?.kref, selectedWorkflow?.revision_number]);

  const selectedRuns = useMemo(() => {
    if (!selectedWorkflow) return [];
    return runs.filter((run) => run.workflow_name.toLowerCase() === selectedWorkflow.name.toLowerCase()).slice(0, 20);
  }, [runs, selectedWorkflow]);

  const activeWorkflowDefinition = selectedWorkflow?.definition ?? '';
  const activeDefinitionLoading = definitionLoading;

  const selectedWorkflowTasks = useMemo(() => {
    if (!selectedWorkflow) return [];
    return selectedWorkflow.definition ? parseWorkflowYaml(selectedWorkflow.definition) : [];
  }, [selectedWorkflow]);

  useEffect(() => {
    const requestedNode = searchParams.get('node');
    if (!requestedNode) {
      setSelectedTask(null);
      return;
    }
    setSelectedTask(selectedWorkflowTasks.find((task) => task.id === requestedNode) ?? null);
  }, [searchParams, selectedWorkflowTasks]);

  useEffect(() => {
    if (!selectedWorkflow?.kref) return;
    if (searchParams.get('tab') === 'runs') return;
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set('workflow', selectedWorkflow.kref);
      next.delete('tab');
      next.delete('run');
      if (selectedTask?.id) {
        next.set('node', selectedTask.id);
      } else {
        next.delete('node');
      }
      return next;
    }, { replace: true });
  }, [searchParams, selectedTask?.id, selectedWorkflow?.kref, setSearchParams]);

  /* ---- Error formatting ---- */

  // Turn an ApiError carrying a `{ errors: [...] }` validation payload into a
  // human-readable, multi-line message. Falls back to the plain message for
  // non-validation errors.
  const formatWorkflowError = (err: unknown, fallback: string): string => {
    if (err instanceof ApiError && err.body && typeof err.body === 'object') {
      const body = err.body as { error?: string; errors?: unknown };
      if (Array.isArray(body.errors) && body.errors.length > 0) {
        const lines = body.errors.slice(0, 8).map((e) => {
          if (e && typeof e === 'object') {
            const entry = e as { message?: string; step_id?: string; field?: string };
            const prefix = entry.step_id
              ? `[${entry.step_id}] `
              : entry.field
                ? `[${entry.field}] `
                : '';
            return `• ${prefix}${entry.message ?? JSON.stringify(e)}`;
          }
          return `• ${String(e)}`;
        });
        const header = body.error ?? 'Validation failed';
        return [header, ...lines].join('\n');
      }
      if (body.error) return String(body.error);
    }
    if (err instanceof Error) return err.message;
    return fallback;
  };

  /* ---- CRUD handlers ---- */

  const handleSaveWorkflow = async (values: WorkflowFormValues): Promise<void> => {
    setSaving(true);
    setError(null);
    try {
      if (editorMode === 'edit' && selectedWorkflow) {
        const request: WorkflowUpdateRequest = {
          kref: selectedWorkflow.kref,
          name: values.name,
          description: values.description,
          version: values.version,
          tags: values.tags,
          definition: values.definition,
        };
        const updated = await updateWorkflow(request);
        applySavedWorkflow(updated);
        setSelectedWorkflowKref(updated.kref);
        setNotice({ tone: 'success', message: tpl('workflows.toast.updated', { name: updated.name }) });
        refreshInBackground();
      } else {
        const request: WorkflowCreateRequest = {
          name: values.name,
          description: values.description,
          version: values.version,
          tags: values.tags,
          definition: values.definition,
        };
        const created = await createWorkflow(request);
        applySavedWorkflow(created);
        setSelectedWorkflowKref(created.kref);
        setNotice({ tone: 'success', message: tpl('workflows.toast.created', { name: created.name }) });
        refreshInBackground();
      }
      setEditorMode(null);
    } catch (err) {
      const message = formatWorkflowError(err, t('workflows.save_failure'));
      setError(message);
      setNotice({ tone: 'error', message });
      // Re-throw so the editor (rendered as a fixed-overlay above the page-
      // level notice) can surface the message inline. Without this, clicks on
      // Save appear to do nothing because the page notice is hidden behind
      // the editor.
      throw new Error(message);
    } finally {
      setSaving(false);
    }
  };

  const handleToggleDeprecation = async () => {
    if (!selectedWorkflow || selectedWorkflow.source === 'builtin') return;
    try {
      await toggleWorkflowDeprecation(selectedWorkflow.kref, !selectedWorkflow.deprecated);
      await load();
      setNotice({
        tone: 'success',
        message: tpl(selectedWorkflow.deprecated ? 'workflows.toast.reenabled' : 'workflows.toast.deprecated', { name: selectedWorkflow.name }),
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : t('workflows.status_failure'));
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('workflows.status_failure_dot') });
    }
  };

  const handleRunWorkflow = async () => {
    if (!selectedWorkflow || running) return;
    setRunning(true);
    try {
      const response = await runWorkflow(selectedWorkflow.name);
      setNotice({
        tone: 'success',
        message: tpl('workflows.toast.run_started', {
          name: selectedWorkflow.name,
          runId: response.run_id.slice(0, 8),
        }),
      });
      navigate(buildRunWorkspaceHref(response.run_id, null));
    } catch (err) {
      const message = formatWorkflowError(err, t('workflows.run_failure'));
      setNotice({ tone: 'error', message });
    } finally {
      setRunning(false);
    }
  };

  const handleDeleteWorkflow = async () => {
    if (!selectedWorkflow || selectedWorkflow.source === 'builtin') return;
    setDeleting(true);
    try {
      const workflowName = selectedWorkflow.name;
      await deleteWorkflow(selectedWorkflow.kref);
      setSelectedTask(null);
      setSelectedWorkflowKref(null);
      await load();
      setNotice({ tone: 'success', message: tpl('workflows.toast.deleted', { name: workflowName }) });
    } catch (err) {
      setError(err instanceof Error ? err.message : t('workflows.delete_failure'));
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('workflows.delete_failure_dot') });
    } finally {
      setDeleting(false);
    }
  };

  /* ---- render ---- */

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col gap-3">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}

      <PageHeader
        kicker={t('workflows.kicker')}
        title={t('workflows.title')}
        actions={
          <>
            <button className="revka-button" data-variant="primary" onClick={() => setEditorMode('create')}>
              <Plus className="h-4 w-4" /> {t('workflows.create')}
            </button>
            <button className="revka-button" onClick={() => void load()} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} /> {t('common.refresh')}
            </button>
          </>
        }
      />

      {error ? (
        <div className="text-sm" style={{ color: 'var(--revka-status-danger)' }}>{error}</div>
      ) : null}

      {/* Toolbar: workflow selector + tabs + actions */}
      {selectedWorkflow ? (
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          {/* Workflow dropdown selector */}
          <div ref={dropdownRef} className="relative">
            <button
              type="button"
              className="revka-button flex items-center gap-2"
              onClick={() => setWorkflowDropdownOpen((prev) => !prev)}
            >
              <Workflow className="h-4 w-4" style={{ color: 'var(--revka-signal-network)' }} />
              <span className="max-w-[18rem] truncate text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
                {selectedWorkflow.name}
              </span>
              <ChevronDown className="h-3.5 w-3.5" style={{ color: 'var(--revka-text-faint)' }} />
            </button>

            {workflowDropdownOpen ? (
              <div
                className="absolute left-0 top-full z-50 mt-1 max-h-[24rem] w-[22rem] overflow-y-auto rounded-[12px] border shadow-lg"
                style={{ borderColor: 'var(--revka-border-strong)', background: 'var(--revka-bg-panel-strong)' }}
              >
                {definitions.map((workflow) => {
                  const isActive = workflow.kref === selectedWorkflow.kref;
                  return (
                    <button
                      key={workflow.kref}
                      type="button"
                      className="w-full border-b px-4 py-2.5 text-left transition last:border-b-0"
                      style={{
                        borderColor: 'var(--revka-border-soft)',
                        background: isActive
                          ? 'color-mix(in srgb, var(--revka-signal-selected) 14%, var(--revka-bg-panel))'
                          : 'transparent',
                      }}
                      onClick={() => {
                        setSelectedWorkflowKref(workflow.kref);
                        setSelectedTask(null);
                        setWorkflowDropdownOpen(false);
                      }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-medium" style={{ color: 'var(--revka-text-primary)' }}>
                          {workflow.name}
                        </span>
                        <span
                          className="shrink-0 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase"
                          style={{
                            background: workflow.deprecated
                              ? 'color-mix(in srgb, var(--revka-status-danger) 12%, transparent)'
                              : 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 14%, transparent))',
                            color: workflow.deprecated ? 'var(--revka-status-danger)' : 'var(--revka-signal-selected)',
                          }}
                        >
                          {workflow.deprecated ? t('workflows.status.off') : t('workflows.status.ready')}
                        </span>
                      </div>
                      <div className="mt-0.5 text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                        {tpl('workflows.workflow_info_short', { version: workflow.version, steps: workflow.steps })}
                      </div>
                    </button>
                  );
                })}
              </div>
            ) : null}
          </div>

          {/* Workflow info */}
          <span className="text-xs" style={{ color: 'var(--revka-text-faint)' }}>
            {tpl('workflows.workflow_info', { version: selectedWorkflow.version, steps: selectedWorkflow.steps, runs: selectedRuns.length })}
          </span>

          <div className="flex-1" />

          {/* Tabs */}
          <div className="revka-tab-strip" role="tablist">
            {(['definition', 'runs'] as const).map((id) => (
              <button
                key={id}
                type="button"
                className="revka-tab-button"
                data-active={String(id === 'definition')}
                aria-selected={id === 'definition'}
                onClick={() => {
                  if (id === 'runs') {
                    navigate(buildRunWorkspaceHref(selectedRuns[0]?.run_id ?? null, selectedTask?.id));
                    return;
                  }
                }}
              >
                {id === 'definition' ? t('workflows.tab.definition') : t('workflows.tab.runs')}
              </button>
            ))}
          </div>

          {/* Actions */}
          <button
            className="revka-button"
            data-variant="primary"
            onClick={handleRunWorkflow}
            disabled={running || selectedWorkflow.deprecated}
            title={t('common.execute')}
          >
            {running ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
            <span className="text-xs">{t('common.execute')}</span>
          </button>
          <button
            className="revka-button"
            onClick={() => selectedWorkflow && setEditorMode(selectedWorkflow.source === 'builtin' ? 'duplicate' : 'edit')}
            title={selectedWorkflow.source === 'builtin' ? t('common.duplicate') : t('common.edit')}
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            className="revka-button"
            onClick={handleToggleDeprecation}
            disabled={selectedWorkflow.source === 'builtin'}
            title={selectedWorkflow.deprecated ? t('common.reenable') : t('common.deprecate')}
          >
            <Power className="h-3.5 w-3.5" />
          </button>
          <button
            className="revka-button"
            onClick={handleDeleteWorkflow}
            disabled={selectedWorkflow.source === 'builtin' || deleting}
            title={t('common.delete')}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ) : null}

      {/* Content: DAG + optional inspector */}
      {!selectedWorkflow ? (
        <Panel className="flex min-h-0 flex-1 items-center justify-center p-5">
          <StateMessage
            tone={loading ? 'loading' : 'empty'}
            title={loading ? t('workflows.loading') : t('workflows.empty_title')}
            description={t('workflows.empty_desc')}
          />
        </Panel>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-4 lg:flex-row">
          {/* DAG canvas — fills remaining space */}
          <div className="flex min-h-0 min-w-0 flex-1 flex-col">
            {!activeWorkflowDefinition ? (
              <Panel className="flex flex-1 items-center justify-center" variant="secondary">
                <StateMessage
                  tone={activeDefinitionLoading ? 'loading' : 'empty'}
                  title={activeDefinitionLoading ? t('workflows.loading') : t('workflows.definition_unavailable_title')}
                  description={definitionError ?? t('workflows.definition_unavailable_desc')}
                />
              </Panel>
            ) : (
              <WorkflowDagWorkspace
                definition={activeWorkflowDefinition}
                onSelectTask={setSelectedTask}
                selectedTaskId={selectedTask?.id}
                fill
              />
            )}
          </div>

          {/* Inspector panel — contextual right sidebar (stacks below on mobile) */}
          {selectedTask ? (
            <div className="min-h-0 w-full shrink-0 space-y-3 overflow-y-auto lg:w-[22rem]">
              <WorkflowMetadataCard workflow={selectedWorkflow} />
              <SelectedTaskCard
                task={selectedTask}
                footer={
                  <div className="flex flex-wrap gap-3 text-xs">
                    <Link
                      to={buildRunWorkspaceHref(selectedRuns[0]?.run_id ?? null, selectedTask.id)}
                      style={{ color: 'var(--revka-signal-network)' }}
                    >
                      {t('workflows.open_node_in_runs')}
                    </Link>
                  </div>
                }
                emptyText={t('workflows.select_dag_node')}
              />
            </div>
          ) : null}
        </div>
      )}

      {editorMode ? (
        <div
          className="fixed inset-0 z-50"
          style={{ background: 'var(--revka-bg-base, var(--pc-bg-base))' }}
        >
          <WorkflowEditor
            mode={editorMode}
            workflow={
              editorMode === 'create'
                ? null
                : editorMode === 'duplicate' && selectedWorkflow
                  ? { ...selectedWorkflow, name: `${selectedWorkflow.name} ${t('workflows.copy_suffix')}` }
                  : selectedWorkflow
            }
            saving={saving}
            onCancel={() => setEditorMode(null)}
            onSave={handleSaveWorkflow}
            containerClassName="flex h-screen w-screen flex-col animate-fade-in"
          />
        </div>
      ) : null}
    </div>
  );
}

interface WorkflowFormValues {
  name: string;
  description: string;
  version: string;
  tags: string[];
  definition: string;
}
