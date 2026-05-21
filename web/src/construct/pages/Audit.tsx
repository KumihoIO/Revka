import { useCallback, useEffect, useState } from 'react';
import { CheckCircle, RefreshCw, ShieldAlert, ShieldCheck, XCircle } from 'lucide-react';
import type { AuditEvent, AuditVerifyResponse } from '@/types/api';
import { getAuditEvents, verifyAuditChain } from '@/lib/api';
import { useT } from '@/construct/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';

function eventTypeColor(eventType: string): string {
  switch (eventType) {
    case 'auth_failure':
    case 'policy_violation':
      return 'var(--construct-status-danger)';
    case 'security_event':
      return 'var(--construct-status-warning)';
    case 'auth_success':
      return 'var(--construct-status-success)';
    default:
      return 'var(--construct-text-secondary)';
  }
}

export default function Audit() {
  const { t, tpl } = useT();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [chainStatus, setChainStatus] = useState<AuditVerifyResponse | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [typeFilter, setTypeFilter] = useState('');
  const [auditEnabled, setAuditEnabled] = useState(true);

  const formatRelative = useCallback((iso: string): string => {
    try {
      const diff = Date.now() - new Date(iso).getTime();
      const seconds = Math.floor(diff / 1000);
      if (seconds < 60) return tpl('audit.time.seconds_ago', { n: seconds });
      const minutes = Math.floor(seconds / 60);
      if (minutes < 60) return tpl('audit.time.minutes_ago', { n: minutes });
      const hours = Math.floor(minutes / 60);
      if (hours < 24) return tpl('audit.time.hours_ago', { n: hours });
      return tpl('audit.time.days_ago', { n: Math.floor(hours / 24) });
    } catch {
      return iso;
    }
  }, [tpl]);

  const eventTypeLabel = useCallback((eventType: string): string => {
    const key = `audit.event.${eventType}`;
    const translated = t(key);
    if (translated !== key) return translated;
    return eventType.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase());
  }, [t]);

  const loadEvents = useCallback(() => {
    setLoading(true);
    getAuditEvents({ limit: 100, event_type: typeFilter || undefined })
      .then((data) => {
        setEvents(data.events);
        setAuditEnabled(data.audit_enabled);
      })
      .catch(() => setEvents([]))
      .finally(() => setLoading(false));
  }, [typeFilter]);

  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  const handleVerify = () => {
    setVerifying(true);
    verifyAuditChain()
      .then(setChainStatus)
      .catch(() => setChainStatus({ verified: false, error: 'Verification request failed' }))
      .finally(() => setVerifying(false));
  };

  if (!auditEnabled) {
    return (
      <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3">
        <PageHeader kicker={t('audit.kicker')} title={t('audit.title')} description={t('audit.description')} />
        <Panel className="p-5">
          <StateMessage title={t('audit.disabled_title')} description={t('audit.disabled_desc')} />
        </Panel>
      </div>
    );
  }

  return (
    <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3 lg:h-[calc(100vh-6rem)]">
      <PageHeader
        kicker={t('audit.kicker')}
        title={t('audit.title')}
        description={t('audit.description')}
        actions={(
          <>
            <button className="construct-button" onClick={loadEvents}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {t('audit.refresh')}
            </button>
            <button className="construct-button" onClick={handleVerify} disabled={verifying}>
              <ShieldCheck className={`h-4 w-4 ${verifying ? 'animate-spin' : ''}`} />
              {t('audit.verify_chain')}
            </button>
          </>
        )}
      />

      <div className="grid gap-4 grid-cols-1 lg:min-h-0 lg:flex-1 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <Panel className="p-4" variant="secondary">
          <div className="construct-kicker">{t('audit.filters')}</div>
          <div className="mt-3 space-y-3">
            <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className="construct-input">
              <option value="">{t('audit.all_event_types')}</option>
              <option value="command_execution">{t('audit.event.command_execution')}</option>
              <option value="file_access">{t('audit.event.file_access')}</option>
              <option value="config_change">{t('audit.event.config_change')}</option>
              <option value="auth_success">{t('audit.event.auth_success')}</option>
              <option value="auth_failure">{t('audit.event.auth_failure')}</option>
              <option value="policy_violation">{t('audit.event.policy_violation')}</option>
              <option value="security_event">{t('audit.event.security_event')}</option>
            </select>
            {chainStatus ? (
              <div className="rounded-[12px] border p-3 text-sm" style={{ borderColor: chainStatus.verified ? 'color-mix(in srgb, var(--construct-status-success) 24%, transparent)' : 'color-mix(in srgb, var(--construct-status-danger) 24%, transparent)' }}>
                <div className="flex items-start gap-2">
                  {chainStatus.verified ? (
                    <CheckCircle className="mt-0.5 h-4 w-4" style={{ color: 'var(--construct-status-success)' }} />
                  ) : (
                    <XCircle className="mt-0.5 h-4 w-4" style={{ color: 'var(--construct-status-danger)' }} />
                  )}
                  <div>
                    <div style={{ color: chainStatus.verified ? 'var(--construct-status-success)' : 'var(--construct-status-danger)' }}>
                      {chainStatus.verified ? t('audit.chain_verified') : t('audit.chain_invalid')}
                    </div>
                    <div className="mt-1 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
                      {chainStatus.verified ? tpl('audit.entries_verified', { count: chainStatus.entry_count ?? 0 }) : chainStatus.error}
                    </div>
                  </div>
                </div>
              </div>
            ) : null}
          </div>
        </Panel>

        <Panel className="p-5 lg:overflow-y-auto">
          <div className="construct-kicker">{t('audit.stream_title')}</div>
          <h3 className="mt-2 text-lg font-semibold">{t('audit.stream_subtitle')}</h3>
          <div className="mt-5 space-y-3">
            {loading ? (
              <StateMessage tone="loading" title={t('audit.loading')} />
            ) : events.length === 0 ? (
              <StateMessage title={t('audit.empty_title')} description={t('audit.empty_desc')} />
            ) : (
              events.map((event) => (
                <div key={event.event_id} className="rounded-[14px] border p-4" style={{ borderColor: 'var(--construct-border-soft)' }}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <span className="rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: eventTypeColor(event.event_type), background: `color-mix(in srgb, ${eventTypeColor(event.event_type)} 12%, transparent)` }}>
                        {eventTypeLabel(event.event_type)}
                      </span>
                      {event.actor ? (
                        <span className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
                          {event.actor.channel}{event.actor.username ? ` · ${event.actor.username}` : ''}
                        </span>
                      ) : null}
                    </div>
                    <span className="text-xs" style={{ color: 'var(--construct-text-faint)' }}>{formatRelative(event.timestamp)}</span>
                  </div>
                  <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4 text-xs">
                    <div>
                      <div className="construct-kicker">{t('audit.col.sequence')}</div>
                      <div className="mt-1" style={{ color: 'var(--construct-text-primary)' }}>{event.sequence}</div>
                    </div>
                    <div>
                      <div className="construct-kicker">{t('audit.col.action')}</div>
                      <div className="mt-1" style={{ color: 'var(--construct-text-primary)' }}>{event.action?.command || '—'}</div>
                    </div>
                    <div>
                      <div className="construct-kicker">{t('audit.col.result')}</div>
                      <div className="mt-1" style={{ color: event.result?.success ? 'var(--construct-status-success)' : 'var(--construct-text-secondary)' }}>
                        {event.result?.success ? t('audit.result_success') : event.result?.error || t('audit.result_recorded')}
                      </div>
                    </div>
                    <div>
                      <div className="construct-kicker">{t('audit.col.hash')}</div>
                      <div className="mt-1 font-mono" style={{ color: 'var(--construct-text-faint)' }}>{event.entry_hash.slice(0, 16)}</div>
                    </div>
                  </div>
                  {event.security.policy_violation ? (
                    <div className="mt-3 inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--construct-status-danger)', background: 'color-mix(in srgb, var(--construct-status-danger) 10%, transparent)' }}>
                      <ShieldAlert className="h-3 w-3" />
                      {t('audit.policy_violation_badge')}
                    </div>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
