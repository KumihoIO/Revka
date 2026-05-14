import { useEffect, useMemo, useState } from 'react';
import { Check, Clock, Puzzle, RadioTower, Zap } from 'lucide-react';
import type { Integration } from '@/types/api';
import { getIntegrations } from '@/lib/api';
import { useT } from '@/construct/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';

export default function Integrations() {
  const { t, tpl } = useT();
  const [integrations, setIntegrations] = useState<Integration[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeCategory, setActiveCategory] = useState('all');
  const [selectedIntegrationName, setSelectedIntegrationName] = useState<string | null>(null);

  const statusBadge = (status: Integration['status']) => {
    switch (status) {
      case 'Active':
        return { icon: Check, label: t('integrations.badge.active'), color: 'var(--construct-status-success)', border: 'color-mix(in srgb, var(--construct-status-success) 24%, transparent)', bg: 'color-mix(in srgb, var(--construct-status-success) 10%, transparent)' };
      case 'Available':
        return { icon: Zap, label: t('integrations.badge.available'), color: 'var(--construct-signal-network)', border: 'color-mix(in srgb, var(--construct-signal-network) 24%, transparent)', bg: 'color-mix(in srgb, var(--construct-signal-network) 10%, transparent)' };
      case 'ComingSoon':
        return { icon: Clock, label: t('integrations.badge.coming_soon'), color: 'var(--construct-text-faint)', border: 'var(--construct-border-soft)', bg: 'transparent' };
      default:
        return { icon: Clock, label: status, color: 'var(--construct-text-faint)', border: 'var(--construct-border-soft)', bg: 'transparent' };
    }
  };

  useEffect(() => {
    getIntegrations().then(setIntegrations).catch((err) => setError(err.message)).finally(() => setLoading(false));
  }, []);

  const categories = useMemo(() => ['all', ...Array.from(new Set(integrations.map((i) => i.category))).sort()], [integrations]);
  const filtered = activeCategory === 'all' ? integrations : integrations.filter((i) => i.category === activeCategory);
  const selectedIntegration = useMemo(
    () => filtered.find((integration) => integration.name === selectedIntegrationName) ?? filtered[0] ?? null,
    [filtered, selectedIntegrationName],
  );
  const summary = useMemo(() => ({
    active: integrations.filter((item) => item.status === 'Active').length,
    available: integrations.filter((item) => item.status === 'Available').length,
    comingSoon: integrations.filter((item) => item.status === 'ComingSoon').length,
  }), [integrations]);

  useEffect(() => {
    if (!filtered.find((integration) => integration.name === selectedIntegrationName)) {
      setSelectedIntegrationName(filtered[0]?.name ?? null);
    }
  }, [filtered, selectedIntegrationName]);

  return (
    <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3 lg:h-[calc(100vh-6rem)]">
      <PageHeader
        kicker={t('integrations.kicker')}
        title={t('integrations.title')}
      />
      {error ? <div className="text-sm" style={{ color: 'var(--construct-status-danger)' }}>{error}</div> : null}
      {loading ? (
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateMessage tone="loading" title={t('integrations.loading')} />
        </div>
      ) : (
        <div className="grid gap-4 grid-cols-1 lg:min-h-0 lg:flex-1 lg:grid-cols-[18rem_minmax(0,1.15fr)_minmax(18rem,0.85fr)]">
          <div className="flex flex-col gap-4 lg:overflow-y-auto lg:min-h-0">
            <Panel className="p-4" variant="secondary">
              <div className="construct-kicker">{t('integrations.coverage')}</div>
              <div className="mt-4 grid gap-3 sm:grid-cols-3 xl:grid-cols-1">
                <MetricPill label={t('integrations.metric.active')} value={summary.active} tone="var(--construct-status-success)" />
                <MetricPill label={t('integrations.metric.available')} value={summary.available} tone="var(--construct-signal-network)" />
                <MetricPill label={t('integrations.metric.coming_soon')} value={summary.comingSoon} tone="var(--construct-text-faint)" />
              </div>
            </Panel>

            <Panel className="p-4" variant="secondary">
              <div className="construct-kicker">{t('integrations.categories')}</div>
              <div className="mt-3 space-y-2">
                {categories.map((category) => {
                  const count = category === 'all'
                    ? integrations.length
                    : integrations.filter((integration) => integration.category === category).length;
                  return (
                    <button
                      key={category}
                      type="button"
                      className="w-full rounded-[14px] border px-3 py-3 text-left transition"
                      onClick={() => setActiveCategory(category)}
                      style={{
                        borderColor: activeCategory === category ? 'var(--construct-border-strong)' : 'var(--construct-border-soft)',
                        background: activeCategory === category
                          ? 'color-mix(in srgb, var(--construct-signal-selected) 14%, var(--construct-bg-panel-strong))'
                          : 'color-mix(in srgb, var(--construct-bg-panel-strong) 94%, transparent)',
                      }}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium capitalize">{category === 'all' ? t('integrations.category_all') : category}</span>
                        <span className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>{count}</span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </Panel>
          </div>

          <Panel className="p-5 lg:overflow-y-auto">
            <div className="construct-kicker">{t('integrations.workspace')}</div>
            <h3 className="mt-2 text-lg font-semibold">{activeCategory === 'all' ? t('integrations.all_integrations') : tpl('integrations.category_integrations', { category: activeCategory })}</h3>

            <div className="mt-5 grid gap-3 grid-cols-1 md:grid-cols-2">
              {filtered.length === 0 ? (
                <StateMessage title={t('integrations.empty_title')} description={t('integrations.empty_desc')} />
              ) : (
                filtered.map((integration) => {
                  const badge = statusBadge(integration.status);
                  const BadgeIcon = badge.icon;
                  return (
                    <button
                      key={integration.name}
                      type="button"
                      className="rounded-[16px] border p-4 text-left transition"
                      onClick={() => setSelectedIntegrationName(integration.name)}
                      style={{
                        borderColor: selectedIntegration?.name === integration.name ? 'var(--construct-border-strong)' : 'var(--construct-border-soft)',
                        background: selectedIntegration?.name === integration.name
                          ? 'color-mix(in srgb, var(--construct-signal-selected) 14%, var(--construct-bg-panel-strong))'
                          : 'color-mix(in srgb, var(--construct-bg-panel-strong) 94%, transparent)',
                      }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          <Puzzle className="h-4 w-4 shrink-0" style={{ color: 'var(--construct-signal-network)' }} />
                          <h4 className="truncate text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{integration.name}</h4>
                        </div>
                        <span className="shrink-0 inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: badge.color, borderColor: badge.border, background: badge.bg }}>
                          <BadgeIcon className="h-3 w-3" />
                          {badge.label}
                        </span>
                      </div>
                      <p className="mt-2 line-clamp-2 text-xs leading-5" style={{ color: 'var(--construct-text-secondary)' }}>{integration.description}</p>
                      <div className="mt-2 text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>
                        {integration.category}
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </Panel>

          <div className="flex flex-col gap-4 lg:overflow-y-auto lg:min-h-0">
            <Panel className="p-4" variant="utility">
              <div className="construct-kicker">{t('integrations.selected')}</div>
              {!selectedIntegration ? (
                <div className="mt-4">
                  <StateMessage compact title={t('integrations.no_selected_title')} description={t('integrations.no_selected_desc')} />
                </div>
              ) : (
                <>
                  <div className="mt-3 flex items-center gap-2">
                    <RadioTower className="h-4 w-4" style={{ color: 'var(--construct-signal-network)' }} />
                    <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{selectedIntegration.name}</div>
                  </div>
                  <p className="mt-3 text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>{selectedIntegration.description}</p>
                  <div className="mt-4 space-y-3">
                    <DetailRow label={t('integrations.detail.category')} value={selectedIntegration.category} />
                    <DetailRow label={t('integrations.detail.status')} value={selectedIntegration.status === 'ComingSoon' ? t('integrations.badge.coming_soon') : selectedIntegration.status === 'Active' ? t('integrations.badge.active') : t('integrations.badge.available')} />
                    <DetailRow
                      label={t('integrations.detail.operational_readiness')}
                      value={
                        selectedIntegration.status === 'Active'
                          ? t('integrations.readiness.active')
                          : selectedIntegration.status === 'Available'
                            ? t('integrations.readiness.available')
                            : t('integrations.readiness.coming_soon')
                      }
                    />
                  </div>
                </>
              )}
            </Panel>

            <Panel className="p-4" variant="secondary">
              <div className="construct-kicker">{t('integrations.legend')}</div>
              <div className="mt-3 space-y-3 text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>
                <p>{t('integrations.legend.p1')}</p>
                <p>{t('integrations.legend.p2')}</p>
                <p>{t('integrations.legend.p3')}</p>
              </div>
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}

function MetricPill({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <div className="rounded-[14px] border p-3" style={{ borderColor: 'var(--construct-border-soft)', background: 'var(--construct-bg-surface)' }}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>{label}</div>
      <div className="mt-2 text-xl font-semibold" style={{ color: tone }}>{value}</div>
    </div>
  );
}

function DetailRow({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-[12px] border p-3" style={{ borderColor: 'var(--construct-border-soft)' }}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>{label}</div>
      <div className="mt-1 text-sm leading-6" style={{ color: 'var(--construct-text-primary)' }}>{value}</div>
    </div>
  );
}
