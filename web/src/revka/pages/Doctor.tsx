import { useState, type ReactNode } from 'react';
import { AlertTriangle, CheckCircle, Loader2, Play, Stethoscope, XCircle } from 'lucide-react';
import type { DiagResult } from '@/types/api';
import { runDoctor } from '@/lib/api';
import { useT } from '@/revka/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';

function severityIcon(severity: DiagResult['severity']) {
  switch (severity) {
    case 'ok': return <CheckCircle className="h-4 w-4" style={{ color: 'var(--revka-status-success)' }} />;
    case 'warn': return <AlertTriangle className="h-4 w-4" style={{ color: 'var(--revka-status-warning)' }} />;
    case 'error': return <XCircle className="h-4 w-4" style={{ color: 'var(--revka-status-danger)' }} />;
  }
}

export default function Doctor() {
  const { t, tpl } = useT();
  const [results, setResults] = useState<DiagResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleRun = async () => {
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      setResults(await runDoctor());
    } catch (err) {
      setError(err instanceof Error ? err.message : t('doctor.run_failed'));
    } finally {
      setLoading(false);
    }
  };

  const okCount = results?.filter((r) => r.severity === 'ok').length ?? 0;
  const warnCount = results?.filter((r) => r.severity === 'warn').length ?? 0;
  const errorCount = results?.filter((r) => r.severity === 'error').length ?? 0;
  const grouped = results?.reduce<Record<string, DiagResult[]>>((acc, item) => {
    const key = item.category;
    if (!acc[key]) acc[key] = [];
    acc[key].push(item);
    return acc;
  }, {}) ?? {};

  return (
    <div className="space-y-6">
      <PageHeader
        kicker={t('doctor.kicker')}
        title={t('doctor.title')}
        description={t('doctor.description')}
        actions={(
          <button className="revka-button" onClick={handleRun} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
            {loading ? t('doctor.running') : t('doctor.run')}
          </button>
        )}
      />

      {error ? <Panel className="p-5"><StateMessage tone="error" title={t('doctor.failed_title')} description={error} /></Panel> : null}

      {loading ? (
        <Panel className="p-5">
          <StateMessage tone="loading" title={t('doctor.loading_title')} description={t('doctor.loading_desc')} />
        </Panel>
      ) : results ? (
        <>
          <div className="grid gap-4 lg:grid-cols-[22rem_minmax(0,1fr)]">
            <div className="space-y-4">
              <Panel className="p-4" variant="secondary">
                <div className="revka-kicker">{t('doctor.health_summary')}</div>
                <div className="mt-4 grid gap-3">
                  <HealthMetric label={t('doctor.passing')} value={`${okCount}`} tone="var(--revka-status-success)" icon={<CheckCircle className="h-4 w-4" style={{ color: 'var(--revka-status-success)' }} />} />
                  <HealthMetric label={t('doctor.warnings')} value={`${warnCount}`} tone="var(--revka-status-warning)" icon={<AlertTriangle className="h-4 w-4" style={{ color: 'var(--revka-status-warning)' }} />} />
                  <HealthMetric label={t('doctor.failures')} value={`${errorCount}`} tone="var(--revka-status-danger)" icon={<XCircle className="h-4 w-4" style={{ color: 'var(--revka-status-danger)' }} />} />
                </div>
              </Panel>

              <Panel className="p-4" variant="secondary">
                <div className="revka-kicker">{t('doctor.run_context')}</div>
                <div className="mt-3 space-y-3 text-sm leading-6" style={{ color: 'var(--revka-text-secondary)' }}>
                  <p>{t('doctor.context_p1')}</p>
                  <p>{t('doctor.context_p2')}</p>
                </div>
              </Panel>
            </div>

            <div className="space-y-4">
              <Panel className="p-4" variant="utility">
                <div className="revka-kicker">{t('doctor.subsystem_boards')}</div>
                <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-2">
                  {Object.entries(grouped).sort(([a], [b]) => a.localeCompare(b)).map(([category, items]) => {
                    const ok = items.filter((item) => item.severity === 'ok').length;
                    const warn = items.filter((item) => item.severity === 'warn').length;
                    const fail = items.filter((item) => item.severity === 'error').length;
                    return (
                      <div key={category} className="rounded-[16px] border p-4" style={{ borderColor: 'var(--revka-border-soft)', background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)' }}>
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="revka-kicker">{category}</div>
                            <div className="mt-2 text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>{tpl('doctor.checks', { count: items.length })}</div>
                          </div>
                          <div className="flex gap-2 text-[10px] font-semibold uppercase tracking-[0.12em]">
                            <span style={{ color: 'var(--revka-status-success)' }}>{ok} {t('doctor.ok')}</span>
                            <span style={{ color: 'var(--revka-status-warning)' }}>{warn} {t('doctor.warn')}</span>
                            <span style={{ color: 'var(--revka-status-danger)' }}>{fail} {t('doctor.fail')}</span>
                          </div>
                        </div>
                        <div className="mt-4 space-y-2">
                          {items.map((result, index) => (
                            <div key={`${category}-${index}`} className="rounded-[12px] border p-3" style={{ borderColor: result.severity === 'ok' ? 'color-mix(in srgb, var(--revka-status-success) 24%, transparent)' : result.severity === 'warn' ? 'color-mix(in srgb, var(--revka-status-warning) 24%, transparent)' : 'color-mix(in srgb, var(--revka-status-danger) 24%, transparent)' }}>
                              <div className="flex items-start gap-3">
                                {severityIcon(result.severity)}
                                <div className="min-w-0">
                                  <div className="text-sm" style={{ color: 'var(--revka-text-primary)' }}>{result.message}</div>
                                  <div className="mt-1 text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--revka-text-faint)' }}>{result.severity}</div>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </Panel>
            </div>
          </div>
        </>
      ) : (
        <Panel className="p-5">
          <StateMessage title={t('doctor.system_diag_title')} description={t('doctor.system_diag_desc')} action={<Stethoscope className="h-5 w-5" style={{ color: 'var(--revka-signal-network)' }} />} />
        </Panel>
      )}
    </div>
  );
}

function HealthMetric({
  label,
  value,
  tone,
  icon,
}: {
  label: string;
  value: string;
  tone: string;
  icon: ReactNode;
}) {
  return (
    <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--revka-border-soft)', background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 88%, transparent)' }}>
      <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--revka-text-faint)' }}>
        {icon}
        {label}
      </div>
      <div className="mt-2 text-2xl font-semibold" style={{ color: tone }}>{value}</div>
    </div>
  );
}
