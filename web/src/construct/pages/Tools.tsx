import { useEffect, useMemo, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  Package,
  RefreshCw,
  Search,
  Terminal,
  Wrench,
} from 'lucide-react';
import type { CliTool, ToolSpec } from '@/types/api';
import { getCliTools, getTools } from '@/lib/api';
import { useT } from '@/construct/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import Notice from '../components/ui/Notice';
import StateMessage from '../components/ui/StateMessage';

export default function Tools() {
  const { t, tpl } = useT();
  const [tools, setTools] = useState<ToolSpec[]>([]);
  const [cliTools, setCliTools] = useState<CliTool[]>([]);
  const [search, setSearch] = useState('');
  const [expandedTool, setExpandedTool] = useState<string | null>(null);
  const [agentSectionOpen, setAgentSectionOpen] = useState(true);
  const [cliSectionOpen, setCliSectionOpen] = useState(true);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);

  const load = () => {
    setLoading(true);
    Promise.all([getTools(), getCliTools()])
      .then(([t, c]) => {
        setTools(t);
        setCliTools(c);
      })
      .catch((err: Error) => setNotice({ tone: 'error', message: tpl('tools.failed_load', { message: err.message }) }))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const filteredAgent = useMemo(() => {
    const q = search.toLowerCase();
    return tools.filter((t) => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q));
  }, [tools, search]);

  const filteredCli = useMemo(() => {
    const q = search.toLowerCase();
    return cliTools.filter((t) => t.name.toLowerCase().includes(q) || t.category.toLowerCase().includes(q));
  }, [cliTools, search]);

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col gap-3">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}

      <PageHeader
        kicker={t('tools.kicker')}
        title={t('tools.title')}
        description={t('tools.description')}
        actions={(
          <button className="construct-button" onClick={load} disabled={loading}>
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
            {t('tools.reload')}
          </button>
        )}
      />

      <Panel className="p-3" variant="secondary">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: 'var(--construct-text-faint)' }} />
          <input
            type="text"
            className="construct-input w-full pl-9"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('tools.search_placeholder')}
          />
        </div>
      </Panel>

      {loading ? (
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateMessage tone="loading" title={t('tools.loading')} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col gap-4">
          <Panel className={`flex flex-col p-4 ${agentSectionOpen ? 'min-h-0 flex-1' : ''}`}>
            <button
              type="button"
              className="flex w-full items-center gap-2 text-left"
              onClick={() => setAgentSectionOpen((v) => !v)}
              aria-expanded={agentSectionOpen}
            >
              <Wrench className="h-4 w-4" style={{ color: 'var(--construct-signal-network)' }} />
              <span className="construct-kicker flex-1">{tpl('tools.agent_tools_count', { count: filteredAgent.length })}</span>
              <ChevronDown
                className="h-4 w-4"
                style={{
                  color: 'var(--construct-text-faint)',
                  transform: agentSectionOpen ? 'rotate(0deg)' : 'rotate(-90deg)',
                  transition: 'transform 0.2s ease',
                }}
              />
            </button>

            {agentSectionOpen ? (
              filteredAgent.length === 0 ? (
                <div className="mt-4">
                  <StateMessage
                    tone="empty"
                    compact
                    title={search ? t('tools.no_matches') : t('tools.no_agent_tools')}
                    description={search ? t('tools.try_different') : t('tools.agent_tools_empty_desc')}
                  />
                </div>
              ) : (
                <div className="mt-4 min-h-0 flex-1 overflow-y-auto pr-1">
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {filteredAgent.map((tool) => {
                    const isExpanded = expandedTool === tool.name;
                    return (
                      <div
                        key={tool.name}
                        className="overflow-hidden rounded-[12px] border"
                        style={{ borderColor: 'var(--construct-border-soft)', background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 70%, transparent)' }}
                      >
                        <button
                          type="button"
                          className="w-full p-3 text-left transition-colors"
                          onClick={() => setExpandedTool(isExpanded ? null : tool.name)}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex min-w-0 items-center gap-2">
                              <Package className="h-4 w-4 shrink-0" style={{ color: 'var(--construct-signal-network)' }} />
                              <h3 className="truncate text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{tool.name}</h3>
                            </div>
                            {isExpanded
                              ? <ChevronDown className="h-4 w-4 shrink-0" style={{ color: 'var(--construct-signal-network)' }} />
                              : <ChevronRight className="h-4 w-4 shrink-0" style={{ color: 'var(--construct-text-faint)' }} />}
                          </div>
                          <p className="mt-2 line-clamp-2 text-sm" style={{ color: 'var(--construct-text-secondary)' }}>
                            {tool.description}
                          </p>
                        </button>
                        {isExpanded && tool.parameters ? (
                          <div className="border-t p-3" style={{ borderColor: 'var(--construct-border-soft)' }}>
                            <div className="construct-kicker mb-2">{t('tools.parameter_schema')}</div>
                            <pre
                              className="max-h-64 overflow-auto rounded-[8px] p-3 font-mono text-xs"
                              style={{ background: 'var(--construct-bg-base)', color: 'var(--construct-text-secondary)' }}
                            >
                              {JSON.stringify(tool.parameters, null, 2)}
                            </pre>
                          </div>
                        ) : null}
                      </div>
                    );
                  })}
                  </div>
                </div>
              )
            ) : null}
          </Panel>

          <Panel className={`flex flex-col p-4 ${cliSectionOpen ? 'min-h-0 flex-1' : ''}`}>
            <button
              type="button"
              className="flex w-full items-center gap-2 text-left"
              onClick={() => setCliSectionOpen((v) => !v)}
              aria-expanded={cliSectionOpen}
            >
              <Terminal className="h-4 w-4" style={{ color: 'var(--construct-signal-selected)' }} />
              <span className="construct-kicker flex-1">{tpl('tools.cli_tools_count', { count: filteredCli.length })}</span>
              <ChevronDown
                className="h-4 w-4"
                style={{
                  color: 'var(--construct-text-faint)',
                  transform: cliSectionOpen ? 'rotate(0deg)' : 'rotate(-90deg)',
                  transition: 'transform 0.2s ease',
                }}
              />
            </button>

            {cliSectionOpen ? (
              filteredCli.length === 0 ? (
                <div className="mt-4">
                  <StateMessage
                    tone="empty"
                    compact
                    title={search ? t('tools.no_matches') : t('tools.no_cli_tools')}
                    description={search ? t('tools.try_different') : t('tools.cli_tools_empty_desc')}
                  />
                </div>
              ) : (
                <div className="mt-4 min-h-0 flex-1 overflow-auto">
                  <table className="construct-table w-full">
                    <thead>
                      <tr>
                        <th>{t('tools.col.name')}</th>
                        <th>{t('tools.col.path')}</th>
                        <th>{t('tools.col.version')}</th>
                        <th>{t('tools.col.category')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filteredCli.map((tool) => (
                        <tr key={tool.name}>
                          <td className="text-sm font-medium" style={{ color: 'var(--construct-text-primary)' }}>{tool.name}</td>
                          <td className="max-w-[260px] truncate font-mono text-xs" title={tool.path} style={{ color: 'var(--construct-text-faint)' }}>
                            {tool.path}
                          </td>
                          <td style={{ color: 'var(--construct-text-faint)' }}>{tool.version ?? '—'}</td>
                          <td>
                            <span
                              className="inline-flex items-center rounded-full border px-2.5 py-0.5 text-[10px] font-semibold capitalize"
                              style={{
                                borderColor: 'var(--construct-border-soft)',
                                color: 'var(--construct-text-secondary)',
                                background: 'color-mix(in srgb, var(--construct-signal-selected) 8%, transparent)',
                              }}
                            >
                              {tool.category}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )
            ) : null}
          </Panel>
        </div>
      )}
    </div>
  );
}
