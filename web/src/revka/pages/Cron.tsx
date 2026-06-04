import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertCircle,
  CheckCircle,
  ChevronDown,
  ChevronRight,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from 'lucide-react';
import type { CronJob, CronRun } from '@/types/api';
import {
  addCronJob,
  deleteCronJob,
  getCronJobs,
  getCronRuns,
  getCronSettings,
  patchCronJob,
  patchCronSettings,
  type CronSettings,
} from '@/lib/api';
import { useT } from '@/revka/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import Modal from '../components/ui/Modal';
import Notice from '../components/ui/Notice';
import StateMessage from '../components/ui/StateMessage';

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString();
}

function formatDuration(ms: number | null): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms}ms`;
  const secs = ms / 1000;
  if (secs < 60) return `${secs.toFixed(1)}s`;
  return `${(secs / 60).toFixed(1)}m`;
}

function statusIcon(status: string | null) {
  if (!status) return null;
  switch (status.toLowerCase()) {
    case 'ok':
    case 'success':
      return <CheckCircle className="h-4 w-4" style={{ color: 'var(--revka-status-success)' }} />;
    case 'error':
    case 'failed':
      return <XCircle className="h-4 w-4" style={{ color: 'var(--revka-status-danger)' }} />;
    default:
      return <AlertCircle className="h-4 w-4" style={{ color: 'var(--revka-status-warning)' }} />;
  }
}

function RunHistoryPanel({ jobId }: { jobId: string }) {
  const { t, tpl } = useT();
  const [runs, setRuns] = useState<CronRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchRuns = useCallback(() => {
    setLoading(true);
    setError(null);
    getCronRuns(jobId, 20)
      .then(setRuns)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [jobId]);

  useEffect(() => { fetchRuns(); }, [fetchRuns]);

  if (loading) {
    return <StateMessage compact tone="loading" title={t('cron.history.loading')} />;
  }
  if (error) {
    return (
      <StateMessage
        compact
        tone="error"
        title={t('cron.history.error_title')}
        description={error}
        action={(
          <button className="revka-button" onClick={fetchRuns}>
            <RefreshCw className="h-3.5 w-3.5" />
            {t('cron.history.retry')}
          </button>
        )}
      />
    );
  }
  if (runs.length === 0) {
    return (
      <StateMessage
        compact
        title={t('cron.history.no_runs_title')}
        description={t('cron.history.no_runs_desc')}
        action={(
          <button className="revka-button" onClick={fetchRuns}>
            <RefreshCw className="h-3.5 w-3.5" />
            {t('cron.history.refresh')}
          </button>
        )}
      />
    );
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <div className="revka-kicker">{tpl('cron.history.recent', { count: runs.length })}</div>
        <button className="revka-button" onClick={fetchRuns} title={t('cron.history.refresh_title')}>
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="max-h-60 space-y-1.5 overflow-y-auto">
        {runs.map((run) => (
          <div
            key={run.id}
            className="rounded-[10px] border px-3 py-2 text-xs"
            style={{
              background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)',
              borderColor: 'var(--revka-border-soft)',
            }}
          >
            <div className="mb-1 flex items-center justify-between">
              <div className="flex items-center gap-2">
                {statusIcon(run.status)}
                <span style={{ color: 'var(--revka-text-secondary)' }}>{run.status}</span>
              </div>
              <span style={{ color: 'var(--revka-text-faint)' }}>{formatDuration(run.duration_ms)}</span>
            </div>
            <div className="flex items-center gap-3" style={{ color: 'var(--revka-text-faint)' }}>
              <span>{formatDate(run.started_at)}</span>
            </div>
            {run.output ? (
              <pre
                className="mt-1.5 max-h-24 overflow-x-auto whitespace-pre-wrap break-words rounded-[8px] p-2 font-mono text-xs"
                style={{ background: 'var(--revka-bg-base)', color: 'var(--revka-text-secondary)' }}
              >
                {run.output}
              </pre>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Cron() {
  const { t, tpl } = useT();
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [expandedJob, setExpandedJob] = useState<string | null>(null);
  const [settings, setSettings] = useState<CronSettings | null>(null);
  const [togglingCatchUp, setTogglingCatchUp] = useState(false);

  const [modalJob, setModalJob] = useState<CronJob | 'add' | null>(null);
  const [formName, setFormName] = useState('');
  const [formSchedule, setFormSchedule] = useState('');
  const [formCommand, setFormCommand] = useState('');
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const isEditing = modalJob !== null && modalJob !== 'add';

  const fetchJobs = useCallback(() => {
    setLoading(true);
    getCronJobs()
      .then(setJobs)
      .catch((err: Error) => setNotice({ tone: 'error', message: tpl('cron.err.load', { message: err.message }) }))
      .finally(() => setLoading(false));
  }, [tpl]);

  const fetchSettings = useCallback(() => {
    getCronSettings().then(setSettings).catch(() => {});
  }, []);

  useEffect(() => {
    fetchJobs();
    fetchSettings();
  }, [fetchJobs, fetchSettings]);

  const toggleCatchUp = async () => {
    if (!settings) return;
    setTogglingCatchUp(true);
    try {
      const updated = await patchCronSettings({ catch_up_on_startup: !settings.catch_up_on_startup });
      setSettings(updated);
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('cron.err.settings') });
    } finally {
      setTogglingCatchUp(false);
    }
  };

  const openAddModal = () => {
    setFormName('');
    setFormSchedule('');
    setFormCommand('');
    setFormError(null);
    setModalJob('add');
  };

  const openEditModal = (job: CronJob) => {
    setFormName(job.name ?? '');
    setFormSchedule(job.expression);
    setFormCommand(job.prompt ?? job.command);
    setFormError(null);
    setModalJob(job);
  };

  const closeModal = () => {
    setModalJob(null);
    setFormError(null);
  };

  const handleSubmit = async () => {
    if (!formSchedule.trim() || !formCommand.trim()) {
      setFormError(t('cron.err.form_required'));
      return;
    }
    setSubmitting(true);
    setFormError(null);
    try {
      if (isEditing) {
        const updated = await patchCronJob((modalJob as CronJob).id, {
          name: formName.trim() || undefined,
          schedule: formSchedule.trim(),
          command: formCommand.trim(),
        });
        setJobs((prev) => prev.map((j) => (j.id === updated.id ? updated : j)));
        setNotice({ tone: 'success', message: tpl('cron.toast.updated', { name: updated.name ?? updated.id.slice(0, 8) }) });
      } else {
        const job = await addCronJob({
          name: formName.trim() || undefined,
          schedule: formSchedule.trim(),
          command: formCommand.trim(),
        });
        setJobs((prev) => [...prev, job]);
        setNotice({ tone: 'success', message: tpl('cron.toast.added', { name: job.name ?? job.id.slice(0, 8) }) });
      }
      closeModal();
    } catch (err) {
      setFormError(err instanceof Error ? err.message : t('cron.err.save'));
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: string) => {
    try {
      await deleteCronJob(id);
      setJobs((prev) => prev.filter((j) => j.id !== id));
      setNotice({ tone: 'success', message: t('cron.toast.deleted') });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('cron.err.delete') });
    } finally {
      setConfirmDelete(null);
    }
  };

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col gap-3">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}

      <PageHeader
        kicker={t('cron.kicker')}
        title={t('cron.title')}
        description={t('cron.description')}
        actions={(
          <>
            <button className="revka-button" onClick={fetchJobs} disabled={loading}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {t('cron.reload')}
            </button>
            <button className="revka-button" data-variant="primary" onClick={openAddModal}>
              <Plus className="h-4 w-4" />
              {t('cron.add_job')}
            </button>
          </>
        )}
      />

      {settings ? (
        <Panel className="p-4" variant="secondary">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="revka-kicker">{t('cron.startup_behaviour')}</div>
              <div className="mt-2 text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
                {t('cron.catch_up_title')}
              </div>
              <p className="mt-1 text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
                {t('cron.catch_up_desc')}
              </p>
            </div>
            <button
              type="button"
              onClick={toggleCatchUp}
              disabled={togglingCatchUp}
              className="relative inline-flex h-6 w-11 items-center rounded-full transition-colors"
              style={{
                background: settings.catch_up_on_startup ? 'var(--revka-signal-selected)' : 'var(--revka-border-strong)',
                opacity: togglingCatchUp ? 0.6 : 1,
              }}
              aria-pressed={settings.catch_up_on_startup}
            >
              <span
                className="inline-block h-4 w-4 rounded-full bg-white transition-transform"
                style={{ transform: settings.catch_up_on_startup ? 'translateX(1.5rem)' : 'translateX(0.25rem)' }}
              />
            </button>
          </div>
        </Panel>
      ) : null}

      {loading ? (
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateMessage tone="loading" title={t('cron.loading_jobs')} />
        </div>
      ) : jobs.length === 0 ? (
        <Panel className="p-6">
          <StateMessage
            tone="empty"
            title={t('cron.no_jobs_title')}
            description={t('cron.no_jobs_desc')}
            action={(
              <button className="revka-button" data-variant="primary" onClick={openAddModal}>
                <Plus className="h-4 w-4" />
                {t('cron.add_job')}
              </button>
            )}
          />
        </Panel>
      ) : (
        <Panel className="min-h-0 flex-1 overflow-auto p-0">
          <table className="revka-table w-full">
            <thead>
              <tr>
                <th className="text-left">{t('cron.col.id')}</th>
                <th className="text-left">{t('cron.col.name')}</th>
                <th className="text-left">{t('cron.col.command')}</th>
                <th className="text-left">{t('cron.col.next_run')}</th>
                <th className="text-left">{t('cron.col.last_status')}</th>
                <th className="text-left">{t('cron.col.enabled')}</th>
                <th className="text-right">{t('cron.col.actions')}</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <React.Fragment key={job.id}>
                  <tr>
                    <td className="font-mono text-xs">
                      <button
                        className="inline-flex items-center gap-1"
                        style={{ color: 'var(--revka-text-secondary)' }}
                        onClick={() => setExpandedJob((prev) => (prev === job.id ? null : job.id))}
                        title={t('cron.toggle_history')}
                      >
                        {expandedJob === job.id
                          ? <ChevronDown className="h-3.5 w-3.5" />
                          : <ChevronRight className="h-3.5 w-3.5" />}
                        {job.id.slice(0, 8)}
                      </button>
                    </td>
                    <td className="text-sm font-medium" style={{ color: 'var(--revka-text-primary)' }}>
                      {job.name ?? '—'}
                    </td>
                    <td className="max-w-[240px] truncate font-mono text-xs" style={{ color: 'var(--revka-text-secondary)' }} title={job.prompt ?? job.command}>
                      {job.prompt ?? job.command}
                    </td>
                    <td className="text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                      {formatDate(job.next_run)}
                    </td>
                    <td>
                      <div className="flex items-center gap-1.5">
                        {statusIcon(job.last_status)}
                        <span className="text-xs capitalize" style={{ color: 'var(--revka-text-secondary)' }}>
                          {job.last_status ?? '—'}
                        </span>
                      </div>
                    </td>
                    <td>
                      <span
                        className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
                        style={job.enabled
                          ? { color: 'var(--revka-signal-selected)', borderColor: 'color-mix(in srgb, var(--revka-signal-selected) 28%, transparent)', background: 'color-mix(in srgb, var(--revka-signal-selected) 10%, transparent)' }
                          : { color: 'var(--revka-text-faint)', borderColor: 'var(--revka-border-soft)', background: 'transparent' }}
                      >
                        {job.enabled ? t('cron.enabled_badge') : t('cron.disabled_badge')}
                      </span>
                    </td>
                    <td className="text-right">
                      <div className="inline-flex items-center justify-end gap-1">
                        <button
                          className="revka-button"
                          onClick={() => openEditModal(job)}
                          title={t('cron.edit')}
                        >
                          <Pencil className="h-4 w-4" />
                        </button>
                        {confirmDelete === job.id ? (
                          <div className="inline-flex items-center gap-2">
                            <span className="text-xs" style={{ color: 'var(--revka-status-danger)' }}>{t('cron.confirm')}</span>
                            <button
                              className="text-xs font-semibold"
                              style={{ color: 'var(--revka-status-danger)' }}
                              onClick={() => handleDelete(job.id)}
                            >{t('cron.yes')}</button>
                            <button
                              className="text-xs font-semibold"
                              style={{ color: 'var(--revka-text-secondary)' }}
                              onClick={() => setConfirmDelete(null)}
                            >{t('cron.no')}</button>
                          </div>
                        ) : (
                          <button
                            className="revka-button"
                            onClick={() => setConfirmDelete(job.id)}
                            title={t('cron.delete')}
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                  {expandedJob === job.id ? (
                    <tr>
                      <td colSpan={7} style={{ background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 60%, transparent)' }}>
                        <div className="px-4 py-3">
                          <RunHistoryPanel jobId={job.id} />
                        </div>
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {modalJob !== null ? (
        <Modal
          title={isEditing ? t('cron.modal.edit_title') : t('cron.modal.add_title')}
          description={t('cron.modal.description')}
          onClose={closeModal}
        >
          <div className="space-y-4">
            {formError ? <Notice tone="error" message={formError} /> : null}
            <div>
              <div className="revka-kicker">{t('cron.form.name')} <span style={{ color: 'var(--revka-text-faint)' }}>{t('cron.form.optional')}</span></div>
              <input
                className="revka-input mt-2 w-full"
                value={formName}
                onChange={(e) => setFormName(e.target.value)}
                placeholder={t('cron.form.name_placeholder')}
              />
            </div>
            <div>
              <div className="revka-kicker">{t('cron.form.schedule')} <span style={{ color: 'var(--revka-status-danger)' }}>*</span></div>
              <input
                className="revka-input mt-2 w-full font-mono text-sm"
                value={formSchedule}
                onChange={(e) => setFormSchedule(e.target.value)}
                placeholder="0 0 * * *"
              />
            </div>
            <div>
              <div className="revka-kicker">{t('cron.form.command')} <span style={{ color: 'var(--revka-status-danger)' }}>*</span></div>
              <textarea
                className="revka-input mt-2 w-full resize-y font-mono text-sm"
                rows={4}
                value={formCommand}
                onChange={(e) => setFormCommand(e.target.value)}
                placeholder={t('cron.form.command_placeholder')}
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button className="revka-button" onClick={closeModal}>{t('cron.form.cancel')}</button>
              <button
                className="revka-button"
                data-variant="primary"
                onClick={handleSubmit}
                disabled={submitting}
              >
                {submitting ? (isEditing ? t('cron.form.saving') : t('cron.form.adding')) : (isEditing ? t('cron.form.save') : t('cron.add_job'))}
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
