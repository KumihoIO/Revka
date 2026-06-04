import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, ArrowDown, Filter, Pause, Play } from 'lucide-react';
import type { SSEEvent } from '@/types/api';
import { SSEClient } from '@/lib/sse';
import { apiOrigin, basePath } from '@/lib/basePath';
import { useT } from '@/revka/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';

function formatTimestamp(ts?: string): string {
  if (!ts) return new Date().toLocaleTimeString();
  return new Date(ts).toLocaleTimeString();
}

function eventTypeStyle(type: string): { color: string; bg: string; border: string } {
  switch (type.toLowerCase()) {
    case 'error':
      return { color: 'var(--revka-status-danger)', bg: 'color-mix(in srgb, var(--revka-status-danger) 8%, transparent)', border: 'color-mix(in srgb, var(--revka-status-danger) 18%, transparent)' };
    case 'warn':
    case 'warning':
      return { color: 'var(--revka-status-warning)', bg: 'color-mix(in srgb, var(--revka-status-warning) 8%, transparent)', border: 'color-mix(in srgb, var(--revka-status-warning) 18%, transparent)' };
    case 'tool_call':
    case 'tool_result':
    case 'tool_call_start':
      return { color: 'var(--revka-signal-selected)', bg: 'color-mix(in srgb, var(--revka-signal-selected) 8%, transparent)', border: 'color-mix(in srgb, var(--revka-signal-selected) 18%, transparent)' };
    case 'llm_request':
      return { color: 'var(--revka-signal-network)', bg: 'color-mix(in srgb, var(--revka-signal-network) 8%, transparent)', border: 'color-mix(in srgb, var(--revka-signal-network) 18%, transparent)' };
    case 'agent_start':
    case 'agent_end':
      return { color: 'var(--revka-signal-selected)', bg: 'color-mix(in srgb, var(--revka-signal-selected) 8%, transparent)', border: 'color-mix(in srgb, var(--revka-signal-selected) 18%, transparent)' };
    case 'message':
    case 'chat':
      return { color: 'var(--revka-signal-selected)', bg: 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 18%, transparent))', border: 'color-mix(in srgb, var(--revka-signal-selected) 28%, transparent)' };
    case 'log':
      return { color: 'var(--revka-text-secondary)', bg: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)', border: 'var(--revka-border-soft)' };
    case 'log_unavailable':
      return { color: 'var(--revka-status-danger)', bg: 'color-mix(in srgb, var(--revka-status-danger) 8%, transparent)', border: 'color-mix(in srgb, var(--revka-status-danger) 18%, transparent)' };
    default:
      return { color: 'var(--revka-text-secondary)', bg: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)', border: 'var(--revka-border-soft)' };
  }
}

interface LogEntry {
  id: string;
  event: SSEEvent;
}

export default function Logs() {
  const { t, tpl } = useT();
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(false);
  const [autoScroll, setAutoScroll] = useState(true);
  const [typeFilters, setTypeFilters] = useState<Set<string>>(new Set());
  const containerRef = useRef<HTMLDivElement | null>(null);
  const pausedRef = useRef(false);
  const entryIdRef = useRef(0);

  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  useEffect(() => {
    const client = new SSEClient({ path: `${apiOrigin}${basePath}/api/daemon/logs` });
    client.onConnect = () => setConnected(true);
    client.onError = () => setConnected(false);
    client.onEvent = (event: SSEEvent) => {
      if (pausedRef.current) return;
      entryIdRef.current += 1;
      const entry: LogEntry = { id: `log-${entryIdRef.current}`, event };
      setEntries((prev) => {
        const next = [...prev, entry];
        return next.length > 500 ? next.slice(-500) : next;
      });
    };
    client.connect();
    return () => client.disconnect();
  }, []);

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [entries, autoScroll]);

  const handleScroll = useCallback(() => {
    if (!containerRef.current) return;
    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 50);
  }, []);

  const allTypes = Array.from(new Set(entries.map((entry) => entry.event.type))).sort();
  const filteredEntries = typeFilters.size === 0 ? entries : entries.filter((entry) => typeFilters.has(entry.event.type));

  return (
    <div className="space-y-6">
      <PageHeader
        kicker={t('logs.kicker')}
        title={t('logs.title')}
        description={t('logs.description')}
        actions={(
          <>
            <button className="revka-button" onClick={() => setPaused((current) => !current)}>
              {paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
              {paused ? t('logs.resume') : t('logs.pause')}
            </button>
            {!autoScroll ? (
              <button className="revka-button" onClick={() => {
                if (containerRef.current) containerRef.current.scrollTop = containerRef.current.scrollHeight;
                setAutoScroll(true);
              }}>
                <ArrowDown className="h-4 w-4" />
                {t('logs.jump_to_bottom')}
              </button>
            ) : null}
          </>
        )}
      />

      <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="space-y-4">
          <Panel className="p-4">
            <div className="revka-kicker">{t('logs.stream_status')}</div>
            <div className="mt-3 inline-flex items-center gap-2 rounded-full px-3 py-1.5 text-xs font-semibold" style={{ color: connected ? 'var(--revka-signal-selected)' : 'var(--revka-status-danger)', background: connected ? 'color-mix(in srgb, var(--revka-signal-selected) 8%, transparent)' : 'color-mix(in srgb, var(--revka-status-danger) 8%, transparent)' }}>
              <span className="revka-dot" style={{ background: connected ? 'var(--revka-signal-selected)' : 'var(--revka-status-danger)' }} />
              {connected ? t('logs.connected') : t('logs.disconnected')}
            </div>
            <div className="mt-3 text-sm" style={{ color: 'var(--revka-text-secondary)' }}>
              {tpl('logs.visible_lines', { count: filteredEntries.length })}
            </div>
          </Panel>

          <Panel className="p-4" variant="secondary">
            <div className="flex items-center gap-2">
              <Filter className="h-4 w-4" style={{ color: 'var(--revka-text-faint)' }} />
              <div className="revka-kicker">{t('logs.event_filters')}</div>
            </div>
            <div className="mt-3 space-y-2">
              {allTypes.length === 0 ? (
                <StateMessage compact title={t('logs.no_event_types')} description={t('logs.no_event_types_desc')} />
              ) : (
                allTypes.map((type) => (
                  <label key={type} className="flex items-center gap-2 text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
                    <input type="checkbox" checked={typeFilters.has(type)} onChange={() => setTypeFilters((prev) => {
                      const next = new Set(prev);
                      if (next.has(type)) next.delete(type); else next.add(type);
                      return next;
                    })} />
                    {type}
                  </label>
                ))
              )}
              {typeFilters.size > 0 ? (
                <button className="revka-button mt-2" onClick={() => setTypeFilters(new Set())}>{t('logs.clear_filters')}</button>
              ) : null}
            </div>
          </Panel>
        </div>

        <Panel className="p-5">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4" style={{ color: 'var(--revka-signal-network)' }} />
            <div className="revka-kicker">{t('logs.live_event_feed')}</div>
          </div>
          <div
            ref={containerRef}
            onScroll={handleScroll}
            className="mt-4 h-[42rem] overflow-y-auto rounded-[16px] border p-3"
            style={{ borderColor: 'var(--revka-border-soft)', background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)' }}
          >
            {filteredEntries.length === 0 ? (
              <StateMessage title={t('logs.no_events')} description={t('logs.no_events_desc')} />
            ) : (
              <div className="space-y-2">
                {filteredEntries.map((entry) => {
                  const style = eventTypeStyle(entry.event.type);
                  const preview = typeof entry.event.line === 'string'
                    ? entry.event.line
                    : typeof entry.event.message === 'string'
                      ? entry.event.message
                      : typeof entry.event.content === 'string'
                        ? entry.event.content
                        : JSON.stringify(entry.event);
                  return (
                    <div key={entry.id} className="rounded-[12px] border p-3" style={{ borderColor: style.border, background: style.bg }}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: style.color }}>
                          {entry.event.type}
                        </span>
                        <span className="text-[11px]" style={{ color: 'var(--revka-text-faint)' }}>{formatTimestamp(entry.event.timestamp)}</span>
                      </div>
                      <pre className="mt-2 whitespace-pre-wrap break-words text-xs" style={{ color: 'var(--revka-text-secondary)', fontFamily: 'var(--pc-font-mono)' }}>
                        {preview}
                      </pre>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
