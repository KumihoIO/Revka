import { useEffect, useMemo, useState } from 'react';
import { DollarSign, Hash, Layers, TrendingUp } from 'lucide-react';
import type { CostSummary } from '@/types/api';
import { getCost } from '@/lib/api';
import { useT } from '@/construct/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';

function formatUSD(value: number): string {
  return `$${value.toFixed(4)}`;
}

export default function Cost() {
  const { t, tpl } = useT();
  const [cost, setCost] = useState<CostSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedModelName, setSelectedModelName] = useState<string | null>(null);

  useEffect(() => {
    getCost().then(setCost).catch((err) => setError(err.message)).finally(() => setLoading(false));
  }, []);

  const models = useMemo(() => cost ? Object.values(cost.by_model).sort((a, b) => b.cost_usd - a.cost_usd) : [], [cost]);
  const selectedModel = useMemo(
    () => models.find((model) => model.model === selectedModelName) ?? models[0] ?? null,
    [models, selectedModelName],
  );

  useEffect(() => {
    if (!models.find((model) => model.model === selectedModelName)) {
      setSelectedModelName(models[0]?.model ?? null);
    }
  }, [models, selectedModelName]);

  return (
    <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3 md:h-[calc(100vh-6rem)]">
      <PageHeader
        kicker={t('cost.kicker')}
        title={t('cost.title')}
      />

      {error ? (
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateMessage tone="error" title={t('cost.error_title')} description={error} />
        </div>
      ) : loading || !cost ? (
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateMessage tone="loading" title={t('cost.loading_title')} />
        </div>
      ) : (
        <div className="grid gap-4 grid-cols-1 md:min-h-0 md:flex-1 md:grid-cols-[18rem_minmax(0,1fr)] lg:grid-cols-[18rem_minmax(0,1fr)_22rem]">
          <div className="flex flex-col gap-4 md:overflow-y-auto md:min-h-0">
            <Panel className="p-4" variant="secondary">
              <div className="construct-kicker">{t('cost.spend_posture')}</div>
              <div className="mt-4 space-y-3">
                {[
                  { icon: DollarSign, tone: 'var(--construct-signal-live)', label: t('cost.session_cost'), value: formatUSD(cost.session_cost_usd) },
                  { icon: TrendingUp, tone: 'var(--construct-status-success)', label: t('cost.daily_cost'), value: formatUSD(cost.daily_cost_usd) },
                  { icon: Layers, tone: 'var(--construct-signal-selected)', label: t('cost.monthly_cost'), value: formatUSD(cost.monthly_cost_usd) },
                  { icon: Hash, tone: 'var(--construct-status-warning)', label: t('cost.requests'), value: cost.request_count.toLocaleString() },
                ].map(({ icon: Icon, tone, label, value }) => (
                  <div key={label} className="rounded-[14px] border p-3" style={{ borderColor: 'var(--construct-border-soft)', background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 88%, transparent)' }}>
                    <div className="flex items-center gap-3">
                      <div className="rounded-[12px] p-2" style={{ background: `color-mix(in srgb, ${tone} 12%, transparent)`, color: tone }}>
                        <Icon className="h-4 w-4" />
                      </div>
                      <div className="min-w-0">
                        <div className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>{label}</div>
                        <div className="mt-1 font-mono text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{value}</div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </Panel>

            <Panel className="p-4" variant="secondary">
              <div className="construct-kicker">{t('cost.token_efficiency')}</div>
              <div className="mt-3 space-y-3 text-sm">
                <DetailMetric label={t('cost.total_tokens')} value={cost.total_tokens.toLocaleString()} />
                <DetailMetric label={t('cost.avg_tokens')} value={cost.request_count > 0 ? Math.round(cost.total_tokens / cost.request_count).toLocaleString() : '0'} />
                <DetailMetric label={t('cost.cost_per_1k')} value={cost.total_tokens > 0 ? formatUSD((cost.monthly_cost_usd / cost.total_tokens) * 1000) : '$0.0000'} />
              </div>
            </Panel>
          </div>

          <Panel className="p-5 md:overflow-y-auto">
            <div className="construct-kicker">{t('cost.model_workspace')}</div>
            <h3 className="mt-2 text-lg font-semibold">{t('cost.spend_by_model')}</h3>
            <div className="mt-5 space-y-3">
              {models.length === 0 ? (
                <StateMessage title={t('cost.no_model_data')} description={t('cost.no_model_data_desc')} />
              ) : (
                models.map((model) => {
                  const share = cost.monthly_cost_usd > 0 ? (model.cost_usd / cost.monthly_cost_usd) * 100 : 0;
                  return (
                    <button
                      key={model.model}
                      type="button"
                      className="construct-selection-card text-left"
                      data-active={String(selectedModel?.model === model.model)}
                      data-accent="workflow"
                      onClick={() => setSelectedModelName(model.model)}
                    >
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{model.model}</div>
                        <div className="font-mono text-sm" style={{ color: 'var(--construct-text-secondary)' }}>{formatUSD(model.cost_usd)}</div>
                      </div>
                      <div className="mt-2 grid gap-2 text-xs md:grid-cols-3" style={{ color: 'var(--construct-text-secondary)' }}>
                        <div>{tpl('cost.tokens_label', { count: model.total_tokens.toLocaleString() })}</div>
                        <div>{tpl('cost.requests_label', { count: model.request_count.toLocaleString() })}</div>
                        <div>{tpl('cost.share_label', { share: share.toFixed(1) })}</div>
                      </div>
                      <div className="mt-3 h-2 overflow-hidden rounded-full" style={{ background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 86%, transparent)' }}>
                        <div style={{ width: `${Math.max(share, 2)}%`, background: 'var(--construct-signal-selected)', height: '100%' }} />
                      </div>
                    </button>
                  );
                })
              )}
            </div>
          </Panel>

          <div className="flex flex-col gap-4 lg:overflow-y-auto lg:min-h-0">
            <Panel className="p-4" variant="utility">
              <div className="construct-kicker">{t('cost.selected_model')}</div>
              {!selectedModel ? (
                <div className="mt-4">
                  <StateMessage compact title={t('cost.no_model_selected')} description={t('cost.no_model_selected_desc')} />
                </div>
              ) : (
                <div className="mt-3 space-y-3">
                  <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{selectedModel.model}</div>
                  <DetailMetric label={t('cost.monthly_spend')} value={formatUSD(selectedModel.cost_usd)} />
                  <DetailMetric label={t('cost.requests')} value={selectedModel.request_count.toLocaleString()} />
                  <DetailMetric label={t('cost.tokens')} value={selectedModel.total_tokens.toLocaleString()} />
                  <DetailMetric label={t('cost.avg_tokens')} value={selectedModel.request_count > 0 ? Math.round(selectedModel.total_tokens / selectedModel.request_count).toLocaleString() : '0'} />
                  <DetailMetric label={t('cost.cost_per_1k')} value={selectedModel.total_tokens > 0 ? formatUSD((selectedModel.cost_usd / selectedModel.total_tokens) * 1000) : '$0.0000'} />
                  <DetailMetric label={t('cost.share_monthly')} value={cost.monthly_cost_usd > 0 ? `${((selectedModel.cost_usd / cost.monthly_cost_usd) * 100).toFixed(1)}%` : '0.0%'} />
                </div>
              )}
            </Panel>

            <Panel className="p-4" variant="secondary">
              <div className="construct-kicker">{t('cost.interpretation')}</div>
              <div className="mt-3 space-y-3 text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>
                <p>{t('cost.interp_p1')}</p>
                <p>{t('cost.interp_p2')}</p>
              </div>
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}

function DetailMetric({
  label,
  value,
}: {
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-[12px] border p-3" style={{ borderColor: 'var(--construct-border-soft)' }}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>{label}</div>
      <div className="mt-1 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{value}</div>
    </div>
  );
}
