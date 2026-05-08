import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { History, Monitor, RefreshCw, Trash2 } from 'lucide-react';
import { getToken } from '@/lib/auth';
import { apiOrigin, basePath } from '@/lib/basePath';
import { isTauri } from '@/lib/tauri';
import { apiFetch } from '@/lib/api';
import { useTheme } from '@/construct/hooks/useTheme';
import { useT } from '@/construct/hooks/useT';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';

interface CanvasFrame {
  frame_id: string;
  content_type: string;
  content: string;
  timestamp: string;
}

interface WsCanvasMessage {
  type: string;
  canvas_id: string;
  frame?: CanvasFrame;
}

export default function Canvas() {
  const { t, tpl } = useT();
  const [canvasId, setCanvasId] = useState('default');
  const [canvasIdInput, setCanvasIdInput] = useState('default');
  const [currentFrame, setCurrentFrame] = useState<CanvasFrame | null>(null);
  const [history, setHistory] = useState<CanvasFrame[]>([]);
  const [connected, setConnected] = useState(false);
  const [showHistory, setShowHistory] = useState(true);
  const [canvasList, setCanvasList] = useState<string[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const wsRef = useRef<WebSocket | null>(null);
  const { resolvedTheme } = useTheme();

  const getWsUrl = useCallback((id: string) => {
    const token = getToken();
    const params = token ? `?token=${encodeURIComponent(token)}` : '';
    let base: string;
    if (isTauri() && apiOrigin) {
      base = apiOrigin.replace(/^http/, 'ws');
    } else {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
      base = `${proto}//${location.host}`;
    }
    return `${base}${basePath || ''}/ws/canvas/${encodeURIComponent(id)}${params}`;
  }, []);

  const connectWs = useCallback((id: string) => {
    wsRef.current?.close();

    const token = getToken();
    const protocols = token ? ['construct.v1', `bearer.${token}`] : ['construct.v1'];
    const ws = new WebSocket(getWsUrl(id), protocols);

    ws.onopen = () => setConnected(true);
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setConnected(false);
    ws.onmessage = (event) => {
      try {
        const msg: WsCanvasMessage = JSON.parse(event.data);
        if (msg.type !== 'frame' || !msg.frame) return;
        const frame = msg.frame;
        if (frame.content_type === 'clear') {
          setCurrentFrame(null);
          setHistory([]);
          return;
        }
        setCurrentFrame(frame);
        setHistory((prev) => {
          if (prev.length > 0 && prev[prev.length - 1]?.frame_id === frame.frame_id) {
            return prev;
          }
          return [...prev.slice(-49), frame];
        });
      } catch {
        // Ignore malformed websocket payloads.
      }
    };

    wsRef.current = ws;
  }, [getWsUrl]);

  useEffect(() => {
    connectWs(canvasId);
    return () => {
      wsRef.current?.close();
    };
  }, [canvasId, connectWs]);

  useEffect(() => {
    const fetchList = async () => {
      try {
        const data = await apiFetch<{ canvases: string[] }>('/api/canvas');
        setCanvasList(data.canvases || []);
      } catch {
        setCanvasList([]);
      } finally {
        setLoadingList(false);
      }
    };

    fetchList();
    const interval = setInterval(fetchList, 5000);
    return () => clearInterval(interval);
  }, []);

  const iframeSrcDoc = useMemo(() => {
    if (!currentFrame || currentFrame.content_type === 'eval') return undefined;
    const viewportBg = resolvedTheme === 'light' ? '#f6fbf7' : '#08110d';
    const viewportText = resolvedTheme === 'light' ? '#173127' : '#dbece1';

    if (currentFrame.content_type === 'svg') {
      return `<!DOCTYPE html><html><head><style>body{margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;background:${viewportBg};color:${viewportText};}</style></head><body>${currentFrame.content}</body></html>`;
    }

    if (currentFrame.content_type === 'markdown') {
      const escaped = currentFrame.content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      return `<!DOCTYPE html><html><head><style>body{margin:1rem;font-family:system-ui,sans-serif;color:${viewportText};background:${viewportBg};line-height:1.6;}pre{white-space:pre-wrap;word-wrap:break-word;}</style></head><body><pre>${escaped}</pre></body></html>`;
    }

    if (currentFrame.content_type === 'text') {
      const escaped = currentFrame.content
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
      return `<!DOCTYPE html><html><head><style>body{margin:1rem;font-family:monospace;color:${viewportText};background:${viewportBg};white-space:pre-wrap;}</style></head><body>${escaped}</body></html>`;
    }

    return currentFrame.content;
  }, [currentFrame, resolvedTheme]);

  const handleSwitchCanvas = () => {
    const nextCanvasId = canvasIdInput.trim();
    if (!nextCanvasId) return;
    setCanvasId(nextCanvasId);
    setCurrentFrame(null);
    setHistory([]);
  };

  const handleClear = async () => {
    try {
      await apiFetch(`/api/canvas/${encodeURIComponent(canvasId)}`, { method: 'DELETE' });
      setCurrentFrame(null);
      setHistory([]);
    } catch {
      // Ignore clear failures for now; reconnection state still reflects websocket health.
    }
  };

  const viewportBackground = resolvedTheme === 'light'
    ? 'linear-gradient(180deg, rgba(255,255,255,0.65), rgba(240,247,242,0.92))'
    : 'linear-gradient(180deg, rgba(8,17,13,0.96), rgba(5,10,9,0.98))';

  const viewportGrid = resolvedTheme === 'light'
    ? 'rgba(63, 175, 104, 0.06)'
    : 'rgba(125, 255, 155, 0.05)';

  const currentFrameTime = currentFrame ? new Date(currentFrame.timestamp).toLocaleString() : '--';

  return (
    <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3 lg:h-[calc(100vh-6rem)]">
      <PageHeader
        kicker={t('canvas.kicker')}
        title={t('canvas.title')}
        actions={(
          <button className="construct-button" onClick={() => connectWs(canvasId)}>
            <RefreshCw className="h-4 w-4" />
            {t('canvas.reconnect')}
          </button>
        )}
      />

      <div className="grid gap-4 grid-cols-1 lg:min-h-0 lg:flex-1 lg:grid-cols-[20rem_minmax(0,1fr)_18rem]">
        <div className="flex flex-col gap-4 lg:overflow-y-auto lg:min-h-0">
          <Panel className="p-4">
            <div className="construct-kicker">{t('canvas.control')}</div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span
                className="construct-status-pill"
                style={{
                  color: connected ? 'var(--construct-status-success)' : 'var(--construct-status-danger)',
                  background: connected ? 'rgba(125,255,155,0.12)' : 'rgba(255,107,122,0.12)',
                  borderColor: 'transparent',
                }}
              >
                <span className="construct-dot" style={{ background: connected ? 'var(--construct-status-success)' : 'var(--construct-status-danger)' }} />
                {connected ? t('canvas.connected') : t('canvas.disconnected')}
              </span>
              <span className="construct-status-pill" style={{ color: 'var(--construct-text-secondary)' }}>
                <Monitor className="h-3.5 w-3.5" />
                {canvasId}
              </span>
            </div>

            <div className="mt-4 space-y-3">
              <input
                type="text"
                value={canvasIdInput}
                onChange={(event) => setCanvasIdInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') handleSwitchCanvas();
                }}
                placeholder={t('canvas.canvas_id_placeholder')}
                className="construct-input"
              />
              <div className="grid gap-2 sm:grid-cols-2">
                <button className="construct-button" data-variant="primary" onClick={handleSwitchCanvas}>
                  {t('canvas.switch')}
                </button>
                <button className="construct-button" onClick={handleClear}>
                  <Trash2 className="h-4 w-4" />
                  {t('canvas.clear')}
                </button>
              </div>
            </div>
          </Panel>

          <Panel className="p-4" variant="secondary">
            <div className="construct-kicker">{t('canvas.available')}</div>
            <div className="mt-3 space-y-2">
              {loadingList ? (
                <StateMessage
                  tone="loading"
                  compact
                  title={t('canvas.loading_list')}
                  description={t('canvas.loading_list_desc')}
                />
              ) : canvasList.length === 0 ? (
                <StateMessage
                  compact
                  title={t('canvas.no_active_title')}
                  description={t('canvas.no_active_desc')}
                />
              ) : (
                canvasList.map((id) => (
                  <button
                    key={id}
                    type="button"
                    data-active={id === canvasId}
                    data-accent="workflow"
                    className="construct-selection-card"
                    onClick={() => {
                      setCanvasIdInput(id);
                      setCanvasId(id);
                      setCurrentFrame(null);
                      setHistory([]);
                    }}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-medium" style={{ color: 'var(--construct-text-primary)' }}>{id}</span>
                      {id === canvasId ? (
                        <span className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-signal-live)' }}>
                          {t('canvas.active')}
                        </span>
                      ) : null}
                    </div>
                  </button>
                ))
              )}
            </div>
          </Panel>
        </div>

        <Panel className="p-5 lg:overflow-y-auto">
          <div className="construct-kicker">{t('canvas.viewport')}</div>
          <h3 className="mt-2 text-lg font-semibold">{t('canvas.live_output')}</h3>

          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <Panel className="p-3" variant="utility">
              <div className="construct-kicker">{t('canvas.canvas_label')}</div>
              <div className="mt-2 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{canvasId}</div>
            </Panel>
            <Panel className="p-3" variant="utility">
              <div className="construct-kicker">{t('canvas.connection')}</div>
              <div className="mt-2 text-sm font-semibold" style={{ color: connected ? 'var(--construct-status-success)' : 'var(--construct-status-danger)' }}>
                {connected ? t('canvas.live') : t('canvas.offline')}
              </div>
            </Panel>
            <Panel className="p-3" variant="utility">
              <div className="construct-kicker">{t('canvas.frame_type')}</div>
              <div className="mt-2 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
                {currentFrame?.content_type ?? '--'}
              </div>
            </Panel>
            <Panel className="p-3" variant="utility">
              <div className="construct-kicker">{t('canvas.last_update')}</div>
              <div className="mt-2 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
                {currentFrameTime}
              </div>
            </Panel>
          </div>

          <div
            className="mt-5 overflow-hidden rounded-[18px] border"
            style={{
              borderColor: 'var(--construct-border-soft)',
              background: viewportBackground,
              minHeight: '48rem',
              position: 'relative',
            }}
          >
            <div
              aria-hidden="true"
              style={{
                position: 'absolute',
                inset: 0,
                pointerEvents: 'none',
                backgroundImage: `linear-gradient(${viewportGrid} 1px, transparent 1px), linear-gradient(90deg, ${viewportGrid} 1px, transparent 1px)`,
                backgroundSize: '24px 24px',
                maskImage: 'linear-gradient(180deg, rgba(255,255,255,0.35), transparent 85%)',
              }}
            />
            <div
              className="flex items-center justify-between gap-3 border-b px-4 py-3"
              style={{ borderColor: 'var(--construct-border-soft)', position: 'relative', zIndex: 1, background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 86%, transparent)' }}
            >
              <div className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: 'var(--construct-status-danger)' }} />
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: 'var(--construct-status-warning)' }} />
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: 'var(--construct-status-success)' }} />
              </div>
              <div className="text-xs font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>
                {currentFrame ? `${currentFrame.content_type} ${t('canvas.frame_suffix')}` : t('canvas.waiting_label')}
              </div>
            </div>
            {currentFrame ? (
              <iframe
                sandbox="allow-scripts"
                srcDoc={iframeSrcDoc}
                className="h-[calc(48rem-3.25rem)] w-full border-0"
                title={`Canvas: ${canvasId}`}
                style={{ background: 'transparent', position: 'relative', zIndex: 1 }}
              />
            ) : (
              <div className="flex h-[calc(48rem-3.25rem)] items-center justify-center p-6" style={{ position: 'relative', zIndex: 1 }}>
                <StateMessage
                  tone={connected ? 'empty' : 'error'}
                  title={connected ? tpl('canvas.waiting_for_content', { id: canvasId }) : t('canvas.disconnected_title')}
                  description={connected
                    ? t('canvas.waiting_for_content_desc')
                    : t('canvas.disconnected_desc')}
                  action={!connected ? (
                    <button className="construct-button" onClick={() => connectWs(canvasId)}>
                      <RefreshCw className="h-4 w-4" />
                      {t('canvas.reconnect_button')}
                    </button>
                  ) : undefined}
                />
              </div>
            )}
          </div>

          {currentFrame ? (
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <Panel className="p-3" variant="utility">
                <div className="construct-kicker">{t('canvas.frame_id')}</div>
                <div className="mt-2 text-sm font-mono" style={{ color: 'var(--construct-text-primary)' }}>
                  {currentFrame.frame_id}
                </div>
              </Panel>
              <Panel className="p-3" variant="utility">
                <div className="construct-kicker">{t('canvas.guidance')}</div>
                <div className="mt-2 text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>
                  {t('canvas.guidance_desc')}
                </div>
              </Panel>
            </div>
          ) : null}
        </Panel>

        <Panel className="p-4 lg:overflow-y-auto" variant="utility">
          <div className="flex items-center justify-between gap-2">
            <div className="construct-kicker">{t('canvas.history')}</div>
            <button className="construct-button" onClick={() => setShowHistory((current) => !current)}>
              <History className="h-4 w-4" />
              {showHistory ? t('canvas.hide') : t('canvas.show')}
            </button>
          </div>

          <div className="mt-3 space-y-2">
            {!showHistory ? (
              <StateMessage
                compact
                title={t('canvas.history_collapsed_title')}
                description={t('canvas.history_collapsed_desc')}
              />
            ) : history.length === 0 ? (
              <StateMessage
                compact
                title={t('canvas.no_frames_title')}
                description={t('canvas.no_frames_desc')}
              />
            ) : (
              [...history].reverse().map((frame) => (
                <button
                  key={frame.frame_id}
                  type="button"
                  data-active={currentFrame?.frame_id === frame.frame_id}
                  data-accent="team"
                  className="construct-selection-card"
                  onClick={() => setCurrentFrame(frame)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--construct-signal-network)' }}>
                      {frame.content_type}
                    </span>
                    <span className="text-[11px]" style={{ color: 'var(--construct-text-faint)' }}>
                      {new Date(frame.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="mt-2 text-xs font-mono" style={{ color: 'var(--construct-text-faint)' }}>
                    {frame.frame_id.slice(0, 8)}
                  </div>
                  <div className="mt-2 line-clamp-2 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
                    {frame.content.slice(0, 96)}
                    {frame.content.length > 96 ? '…' : ''}
                  </div>
                </button>
              ))
            )}
          </div>
        </Panel>
      </div>

    </div>
  );
}
