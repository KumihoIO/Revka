import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { AlertCircle, Play, X as XIcon } from 'lucide-react';
import { getToken } from '@/lib/auth';
import { apiOrigin, basePath } from '@/lib/basePath';
import { isTauri } from '@/lib/tauri';
import type { AssistantConfig, SchemeColors } from './assistantConfig';

/* ── types ─────────────────────────────────────────── */

export type CodeTool = 'claude' | 'codex' | 'opencode' | 'gemini' | 'operator';

export interface CodeSession {
  sessionId: string;
  token: string;
  cwd: string;
  toolKey: CodeTool;
}

interface ToolOption {
  key: CodeTool;
  label: string;
}

const TOOL_OPTIONS: ToolOption[] = [
  { key: 'claude', label: 'Claude Code' },
  { key: 'codex', label: 'Codex' },
  { key: 'opencode', label: 'OpenCode' },
  { key: 'gemini', label: 'Gemini CLI' },
  { key: 'operator', label: 'Construct Operator' },
];

const RECENT_REPOS_KEY = 'construct-code-recent-repos';
const RECENT_REPOS_CAP = 10;

export function toolLabel(key: CodeTool): string {
  return TOOL_OPTIONS.find((t) => t.key === key)?.label ?? key;
}

export function basename(p: string): string {
  if (!p) return '';
  const trimmed = p.replace(/\/+$/, '');
  const idx = trimmed.lastIndexOf('/');
  return idx >= 0 ? trimmed.slice(idx + 1) : trimmed;
}

/* ── local storage helpers ─────────────────────────── */

function readRecentRepos(): string[] {
  try {
    const raw = localStorage.getItem(RECENT_REPOS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x): x is string => typeof x === 'string').slice(0, RECENT_REPOS_CAP);
  } catch {
    return [];
  }
}

function pushRecentRepo(path: string) {
  try {
    const current = readRecentRepos();
    const next = [path, ...current.filter((p) => p !== path)].slice(0, RECENT_REPOS_CAP);
    localStorage.setItem(RECENT_REPOS_KEY, JSON.stringify(next));
  } catch {
    /* ignore */
  }
}

/* ── MCP daemon URL resolution ─────────────────────── */

/**
 * Shape returned by `GET /api/mcp/discovery`. Either the daemon is reachable
 * (in which case `url` is the MCP endpoint and `health` is the full health
 * snapshot), or it isn't and we surface a machine-readable `reason`.
 */
type McpDiscoveryResponse =
  | { available: true; url: string; health: unknown }
  | { available: false; reason: string };

/** Dev-only override: if set, skip the gateway proxy and talk to the daemon
 *  directly — matches the pre-reverse-proxy behavior for local hacking. */
function mcpOverrideBaseUrl(): string | null {
  const env = (import.meta.env.VITE_CONSTRUCT_MCP_URL as string | undefined)?.trim();
  return env ? env.replace(/\/+$/, '') : null;
}

async function fetchMcpDiscovery(signal: AbortSignal): Promise<McpDiscoveryResponse> {
  const headers: Record<string, string> = {};
  const auth = getToken();
  if (auth) headers['Authorization'] = `Bearer ${auth}`;
  const res = await fetch(`${basePath}/api/mcp/discovery`, { signal, headers });
  if (!res.ok) {
    return { available: false, reason: `discovery http ${res.status}` };
  }
  return (await res.json()) as McpDiscoveryResponse;
}

/* ── xterm theme ───────────────────────────────────── */

function buildXtermTheme(colors: SchemeColors): Record<string, string> {
  return {
    background: '#0c0c0c',
    foreground: colors.primary,
    cursor: colors.cursorColor,
    cursorAccent: '#0c0c0c',
    selectionBackground: 'rgba(125,255,155,0.18)',
    selectionForeground: '#ffffff',
    black: '#1a1a1a',
    red: '#ff6b7a',
    green: '#7dff9b',
    yellow: '#ffc857',
    blue: '#72d8ff',
    magenta: '#a78bfa',
    cyan: '#72d8ff',
    white: '#e6e6e6',
    brightBlack: '#4a4a4a',
    brightRed: '#ff8a97',
    brightGreen: '#9dffb3',
    brightYellow: '#ffd980',
    brightBlue: '#8be4ff',
    brightMagenta: '#c4a8fc',
    brightCyan: '#8be4ff',
    brightWhite: '#ffffff',
  };
}

/* ── WS URL builder ────────────────────────────────── */

function wsBaseOrigin(): string {
  if (isTauri() && apiOrigin) return apiOrigin.replace(/^http/, 'ws');
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}`;
}

function buildCodeWsUrl(session: CodeSession, cols: number, rows: number): string {
  const params = new URLSearchParams();
  const auth = getToken();
  if (auth) params.set('token', auth);
  params.set('tool', session.toolKey);
  params.set('cwd', session.cwd);
  params.set('mcp_session', session.sessionId);
  params.set('mcp_token', session.token);
  // Pre-size the PTY so the child's first layout matches xterm. Without this
  // the gateway opens 80×24, the TUI paints, then a resize message arrives and
  // everything repaints — Claude Code's boxes collide mid-redraw.
  if (cols > 0) params.set('cols', String(cols));
  if (rows > 0) params.set('rows', String(rows));
  return `${wsBaseOrigin()}${basePath}/ws/terminal?${params.toString()}`;
}

function buildMcpEventsWsUrl(session: CodeSession): string {
  const params = new URLSearchParams();
  const auth = getToken();
  if (auth) params.set('token', auth);
  params.set('session_id', session.sessionId);
  params.set('mcp_token', session.token);
  return `${wsBaseOrigin()}${basePath}/ws/mcp/events?${params.toString()}`;
}

/* ── Progress event shape + card projection ────────── */

/**
 * Wire format emitted by the gateway's /ws/mcp/events proxy — mirrors the
 * daemon's `ProgressEvent` Rust struct exactly. See `src/mcp_server/session.rs`.
 */
export interface McpProgressEvent {
  token: number;
  progress: number;
  total?: number | null;
  message?: string | null;
  tool?: string | null;
  timestamp: string; // RFC3339
}

export interface StatusCardProps {
  /** Tool name, e.g. "notion". Falls back to "tool #<token>" if omitted. */
  title: string;
  /** Either "4 / 10" if total known, or a plain progress count / message. */
  progressLine: string;
  /** Fraction 0-1 if total known, else null (render as pulse, no bar). */
  ratio: number | null;
  /** Event timestamp in ms since epoch, used to compute "Ns ago". */
  timestampMs: number;
  /** Stable id for React keying — `${token}-${timestampMs}`. */
  id: string;
}

/**
 * Pure function: derive the render props for a status card from a raw event.
 * Exported for unit tests and keeps all shaping logic in one place.
 */
export function eventToCardProps(ev: McpProgressEvent): StatusCardProps {
  const title = ev.tool && ev.tool.trim() ? ev.tool : `tool #${ev.token}`;
  const total = ev.total ?? null;
  let progressLine: string;
  let ratio: number | null = null;
  if (total && total > 0) {
    progressLine = `${ev.progress} / ${total}`;
    ratio = Math.max(0, Math.min(1, ev.progress / total));
  } else if (ev.message && ev.message.trim()) {
    progressLine = ev.message;
  } else {
    progressLine = `step ${ev.progress}`;
  }
  const ts = Date.parse(ev.timestamp);
  const timestampMs = Number.isFinite(ts) ? ts : Date.now();
  return {
    title,
    progressLine,
    ratio,
    timestampMs,
    id: `${ev.token}-${timestampMs}`,
  };
}

function formatRelative(ms: number, now: number): string {
  const diff = Math.max(0, Math.floor((now - ms) / 1000));
  if (diff < 1) return 'now';
  if (diff < 60) return `${diff}s ago`;
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m}m ago`;
  return `${Math.floor(m / 60)}h ago`;
}

const STATUS_CARD_CAP = 30;
const STATUS_FADE_AFTER_MS = 20_000;
const EVENTS_WS_MAX_RETRIES = 3;

/* ── useMcpEvents — subscribes to /ws/mcp/events with reconnect ───── */

type EventsStatus = 'connecting' | 'open' | 'offline';

function useMcpEvents(session: CodeSession | null): {
  events: McpProgressEvent[];
  status: EventsStatus;
} {
  const [events, setEvents] = useState<McpProgressEvent[]>([]);
  const [status, setStatus] = useState<EventsStatus>('connecting');
  const wsRef = useRef<WebSocket | null>(null);
  const retryTimerRef = useRef<number | null>(null);
  const retriesRef = useRef(0);
  const cancelledRef = useRef(false);

  useEffect(() => {
    if (!session) return;
    cancelledRef.current = false;
    retriesRef.current = 0;

    const connect = () => {
      if (cancelledRef.current) return;
      setStatus('connecting');
      let ws: WebSocket;
      try {
        ws = new WebSocket(buildMcpEventsWsUrl(session));
      } catch {
        scheduleRetry();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        if (cancelledRef.current) return;
        retriesRef.current = 0;
        setStatus('open');
      };
      ws.onmessage = (event) => {
        if (typeof event.data !== 'string') return;
        try {
          const parsed = JSON.parse(event.data) as McpProgressEvent & {
            error?: string;
          };
          if ('error' in parsed && parsed.error) {
            // Daemon unreachable frame sent by gateway proxy — treat as offline.
            setStatus('offline');
            return;
          }
          if (typeof parsed.token !== 'number' || typeof parsed.progress !== 'number') {
            return;
          }
          setEvents((prev) => {
            const next = [...prev, parsed];
            return next.length > STATUS_CARD_CAP
              ? next.slice(next.length - STATUS_CARD_CAP)
              : next;
          });
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onerror = () => {
        /* onclose will fire next and handle retry */
      };
      ws.onclose = () => {
        if (cancelledRef.current) return;
        scheduleRetry();
      };
    };

    const scheduleRetry = () => {
      if (cancelledRef.current) return;
      if (retriesRef.current >= EVENTS_WS_MAX_RETRIES) {
        setStatus('offline');
        return;
      }
      const attempt = retriesRef.current;
      retriesRef.current = attempt + 1;
      // Backoff: 500ms, 1500ms, 3500ms.
      const delay = 500 + attempt * 1000 + attempt * attempt * 500;
      retryTimerRef.current = window.setTimeout(connect, delay);
    };

    connect();
    return () => {
      cancelledRef.current = true;
      if (retryTimerRef.current !== null) {
        window.clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      const ws = wsRef.current;
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.onclose = null;
        try { ws.close(); } catch { /* ignore */ }
      }
      wsRef.current = null;
    };
  }, [session]);

  return { events, status };
}

/* ── McpStatusStrip — inline progress cards above xterm ─────────── */

function McpStatusStrip({
  events,
  status,
  colors,
}: {
  events: McpProgressEvent[];
  status: EventsStatus;
  colors: SchemeColors;
}) {
  // Ticking "now" so relative timestamps refresh every second without
  // touching the event list.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const cards = useMemo(() => events.map(eventToCardProps), [events]);

  const stripStyle: React.CSSProperties = {
    borderColor: 'var(--construct-border-soft)',
    background: 'color-mix(in srgb, var(--construct-bg-surface) 92%, transparent)',
    minHeight: 64,
    maxHeight: 96,
  };

  if (status === 'offline') {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 border-b px-3 py-2 text-[11px]"
        style={stripStyle}
      >
        <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: 'var(--construct-text-faint)' }} />
        <span style={{ color: 'var(--construct-text-faint)' }}>
          progress stream offline
        </span>
      </div>
    );
  }

  if (cards.length === 0) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 border-b px-3 py-2 text-[11px]"
        style={stripStyle}
      >
        <span
          className="inline-block h-1.5 w-1.5 rounded-full animate-pulse"
          style={{ background: status === 'open' ? colors.primary : 'var(--construct-text-faint)' }}
        />
        <span style={{ color: 'var(--construct-text-muted)' }}>
          waiting for Construct tool activity
        </span>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="flex gap-2 overflow-x-auto border-b px-3 py-2"
      style={stripStyle}
    >
      {cards.map((card) => {
        const ageMs = now - card.timestampMs;
        const faded = ageMs > STATUS_FADE_AFTER_MS;
        return (
          <div
            key={card.id}
            className="shrink-0 rounded border px-2 py-1.5"
            style={{
              minWidth: 150,
              maxWidth: 240,
              borderColor: faded ? 'var(--construct-border-soft)' : 'color-mix(in srgb, ' + colors.primary + ' 40%, var(--construct-border-soft))',
              background: 'var(--construct-bg-surface)',
              opacity: faded ? 0.55 : 1,
              transition: 'opacity 600ms ease',
            }}
          >
            <div
              className="truncate text-[10px] font-semibold uppercase tracking-[0.1em]"
              style={{
                color: faded ? 'var(--construct-text-faint)' : colors.primary,
                textShadow: faded ? 'none' : colors.glow,
              }}
              title={card.title}
            >
              {card.title}
            </div>
            <div
              className="truncate text-[11px]"
              style={{ color: faded ? 'var(--construct-text-faint)' : 'var(--construct-text-primary)' }}
              title={card.progressLine}
            >
              {card.progressLine}
            </div>
            {card.ratio !== null && (
              <div
                className="mt-1 h-1 w-full overflow-hidden rounded"
                style={{ background: 'var(--construct-border-soft)' }}
              >
                <div
                  className="h-full"
                  style={{
                    width: `${Math.round(card.ratio * 100)}%`,
                    background: faded ? 'var(--construct-text-faint)' : colors.primary,
                    transition: 'width 300ms ease',
                  }}
                />
              </div>
            )}
            <div
              className="mt-1 text-[10px]"
              style={{ color: 'var(--construct-text-faint)' }}
            >
              {formatRelative(card.timestampMs, now)}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── CodePty — xterm view for a live session ──────── */

function CodePty({
  session,
  config,
  colors,
  visible,
}: {
  session: CodeSession;
  config: AssistantConfig;
  colors: SchemeColors;
  visible: boolean;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const term = new Terminal({
      cursorBlink: config.cursorBlink,
      cursorStyle: 'bar',
      fontSize: config.fontSize,
      fontFamily: 'JetBrains Mono, SF Mono, Menlo, Monaco, Consolas, monospace',
      lineHeight: 1.2,
      scrollback: 10000,
      allowProposedApi: true,
      theme: buildXtermTheme(colors),
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    // Fit synchronously so term.cols/rows reflect the actual container before
    // we open the WS — the gateway reads cols/rows from the query string to
    // openpty at the right size on the first try.
    try { fitAddon.fit(); } catch { /* container may be hidden */ }

    termRef.current = term;
    fitAddonRef.current = fitAddon;

    term.write(`\x1b[36m  Launching ${toolLabel(session.toolKey)} in ${session.cwd}…\x1b[0m\r\n\r\n`);

    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(buildCodeWsUrl(session, term.cols, term.rows));
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      ws.onopen = () => {
        ws!.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      };
      ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
          term.write(event.data);
        } else {
          term.write(new Uint8Array(event.data as ArrayBuffer));
        }
      };
      ws.onerror = () => {
        term.write('\r\n\x1b[31m  WebSocket error.\x1b[0m\r\n');
      };
      ws.onclose = () => {
        term.write('\r\n\x1b[33m  Session ended.\x1b[0m\r\n');
      };
    } catch (err) {
      term.write(`\x1b[31m  Failed to open WebSocket: ${err instanceof Error ? err.message : String(err)}\x1b[0m\r\n`);
    }

    term.onData((data) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(data);
      }
    });

    term.onResize(({ cols, rows }) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    });

    const resizeObserver = new ResizeObserver(() => {
      try { fitAddon.fit(); } catch { /* ignore */ }
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      if (ws) { ws.onopen = null; ws.onmessage = null; ws.onclose = null; ws.onerror = null; ws.close(); }
      term.dispose();
      termRef.current = null;
      fitAddonRef.current = null;
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session.sessionId]);

  useEffect(() => {
    if (!termRef.current) return;
    termRef.current.options.theme = buildXtermTheme(colors);
  }, [colors]);

  useEffect(() => {
    if (!termRef.current) return;
    termRef.current.options.cursorBlink = config.cursorBlink;
  }, [config.cursorBlink]);

  useEffect(() => {
    if (!termRef.current) return;
    termRef.current.options.fontSize = config.fontSize;
    try { fitAddonRef.current?.fit(); } catch { /* ignore */ }
  }, [config.fontSize]);

  useEffect(() => {
    if (visible) {
      requestAnimationFrame(() => {
        try { fitAddonRef.current?.fit(); } catch { /* ignore */ }
        termRef.current?.focus();
      });
    }
  }, [visible]);

  return (
    <div
      ref={containerRef}
      className="min-h-0 flex-1"
      style={{ display: visible ? 'block' : 'none', padding: '4px 0' }}
    />
  );
}

/* ── CodeRunning — the live-session view (strip + xterm) ───────── */

function CodeRunning({
  session,
  config,
  colors,
  visible,
  onSessionEnd,
}: {
  session: CodeSession;
  config: AssistantConfig;
  colors: SchemeColors;
  visible: boolean;
  onSessionEnd: () => void;
}) {
  const { events, status } = useMcpEvents(session);
  return (
    <div className="flex min-h-0 flex-1 flex-col" style={{ display: visible ? 'flex' : 'none' }}>
      <div
        className="flex items-center gap-2 border-b px-3 py-1.5 text-[11px]"
        style={{ borderColor: 'var(--construct-border-soft)', background: 'var(--construct-bg-surface)' }}
      >
        <span style={{ color: colors.primary, textShadow: colors.glow, fontWeight: 600 }}>
          {toolLabel(session.toolKey)}
        </span>
        <span style={{ color: 'var(--construct-text-faint)' }}>·</span>
        <span
          className="truncate"
          style={{ color: 'var(--construct-text-muted)', maxWidth: '40ch' }}
          title={session.cwd}
        >
          {session.cwd}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          className="flex items-center gap-1 rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.1em] transition-colors hover:bg-white/5"
          onClick={onSessionEnd}
          style={{ color: 'var(--construct-text-secondary)', border: '1px solid var(--construct-border-soft)' }}
          title="End session"
        >
          <XIcon className="h-3 w-3" />
          End session
        </button>
      </div>
      <McpStatusStrip events={events} status={status} colors={colors} />
      <CodePty session={session} config={config} colors={colors} visible={visible} />
    </div>
  );
}

/* ── CodeTab ─────────────────────────────────────── */

export interface CodeTabProps {
  tabId: string;
  config: AssistantConfig;
  colors: SchemeColors;
  visible: boolean;
  session: CodeSession | null;
  onSessionStart: (session: CodeSession, toolLabel: string, cwd: string) => void;
  onSessionEnd: () => void;
  onDelegateToChat: (pageContext: string, title: string) => void;
}

export default function CodeTab({
  config,
  colors,
  visible,
  session,
  onSessionStart,
  onSessionEnd,
  onDelegateToChat,
}: CodeTabProps) {
  const [tool, setTool] = useState<CodeTool>('claude');
  const [cwd, setCwd] = useState('');
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recentRepos, setRecentRepos] = useState<string[]>(() => readRecentRepos());
  const [daemonStatus, setDaemonStatus] = useState<'checking' | 'ready' | 'missing'>('checking');
  const [daemonReason, setDaemonReason] = useState<string | null>(null);

  // Daemon probe. In normal mode the gateway reverse-proxies `/api/mcp/*`
  // to the in-process MCP server, so we only need to know whether the daemon
  // is up — not its URL. In dev override mode we still hit the override host
  // directly (same as before), preserving the single-shot dev loop.
  useEffect(() => {
    if (session) return; // only probe in setup state
    let cancelled = false;
    const controller = new AbortController();
    (async () => {
      try {
        const override = mcpOverrideBaseUrl();
        if (override) {
          const res = await fetch(`${override}/health`, { signal: controller.signal });
          if (cancelled) return;
          setDaemonStatus(res.ok ? 'ready' : 'missing');
          setDaemonReason(res.ok ? null : `health http ${res.status}`);
          return;
        }
        const d = await fetchMcpDiscovery(controller.signal);
        if (cancelled) return;
        if (d.available) {
          setDaemonStatus('ready');
          setDaemonReason(null);
        } else {
          setDaemonStatus('missing');
          setDaemonReason(d.reason);
        }
      } catch {
        if (!cancelled) {
          setDaemonStatus('missing');
          setDaemonReason('discovery request failed');
        }
      }
    })();
    return () => { cancelled = true; controller.abort(); };
  }, [session]);

  const startSession = useCallback(async () => {
    setError(null);

    if (tool === 'operator') {
      // Construct Operator → delegate to a Chat tab, do not spawn a PTY.
      onDelegateToChat('v2:code:operator', 'Operator');
      return;
    }

    const trimmedCwd = cwd.trim();
    if (!trimmedCwd) {
      setError('Repository path is required.');
      return;
    }

    setStarting(true);
    try {
      // Normal mode: POST to the gateway reverse-proxy so the browser stays
      // same-origin (no CORS, no ERR_CONNECTION_REFUSED on the ephemeral MCP
      // port). Dev override still goes direct for local hacking.
      const override = mcpOverrideBaseUrl();
      const url = override ? `${override}/session` : `${basePath}/api/mcp/session`;
      const headers: Record<string, string> = { 'Content-Type': 'application/json' };
      if (!override) {
        const auth = getToken();
        if (auth) headers['Authorization'] = `Bearer ${auth}`;
      }
      const res = await fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify({ cwd: trimmedCwd, label: `${toolLabel(tool)} · ${basename(trimmedCwd)}` }),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`Session request failed (${res.status})${text ? `: ${text}` : ''}`);
      }
      const data = await res.json() as { session_id: string; token: string; cwd: string };
      const newSession: CodeSession = {
        sessionId: data.session_id,
        token: data.token,
        cwd: data.cwd || trimmedCwd,
        toolKey: tool,
      };
      pushRecentRepo(newSession.cwd);
      setRecentRepos(readRecentRepos());
      onSessionStart(newSession, toolLabel(tool), newSession.cwd);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  }, [tool, cwd, onSessionStart, onDelegateToChat]);

  const statusText = useMemo(() => {
    if (daemonStatus === 'checking') return 'Construct MCP: checking…';
    if (daemonStatus === 'ready') return 'Construct MCP: ready';
    const suffix = daemonReason ? ` (${daemonReason})` : '';
    return `Construct MCP: not running — start construct-mcp first${suffix}`;
  }, [daemonStatus, daemonReason]);

  const statusColor = useMemo(() => {
    if (daemonStatus === 'ready') return 'var(--construct-status-success)';
    if (daemonStatus === 'missing') return 'var(--construct-status-danger)';
    return 'var(--construct-text-faint)';
  }, [daemonStatus]);

  // Running state
  if (session) {
    return <CodeRunning
      session={session}
      config={config}
      colors={colors}
      visible={visible}
      onSessionEnd={onSessionEnd}
    />;
  }

  // Setup state
  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-y-auto p-4"
      style={{ display: visible ? 'flex' : 'none' }}
    >
      <div className="mx-auto w-full max-w-md space-y-4">
        <div>
          <h3
            className="text-xs font-semibold uppercase tracking-[0.14em]"
            style={{ color: colors.primary, textShadow: colors.glow }}
          >
            Launch Code Session
          </h3>
          <p className="mt-1 text-[11px]" style={{ color: 'var(--construct-text-muted)' }}>
            Start a CLI coding agent in a terminal, wired to the shared Construct MCP daemon.
          </p>
        </div>

        {/* Daemon status */}
        <div
          className="flex items-center gap-2 rounded border px-3 py-2 text-[11px]"
          style={{
            borderColor: 'var(--construct-border-soft)',
            background: 'color-mix(in srgb, var(--construct-bg-surface) 95%, transparent)',
          }}
        >
          <span
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: statusColor }}
          />
          <span style={{ color: 'var(--construct-text-secondary)' }}>{statusText}</span>
        </div>

        {/* Tool dropdown */}
        <div>
          <label
            className="mb-1 block font-semibold uppercase tracking-[0.1em]"
            style={{ color: 'var(--construct-text-faint)', fontSize: '10px' }}
          >
            Tool
          </label>
          <select
            value={tool}
            onChange={(e) => setTool(e.target.value as CodeTool)}
            className="w-full rounded border bg-transparent px-2 py-1.5 text-xs outline-none"
            style={{
              borderColor: 'var(--construct-border-soft)',
              color: 'var(--construct-text-primary)',
              background: 'var(--construct-bg-surface)',
            }}
          >
            {TOOL_OPTIONS.map((opt) => (
              <option key={opt.key} value={opt.key} style={{ background: 'var(--construct-bg-panel-strong)' }}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>

        {/* Repo root */}
        {tool !== 'operator' && (
          <div>
            <label
              className="mb-1 block font-semibold uppercase tracking-[0.1em]"
              style={{ color: 'var(--construct-text-faint)', fontSize: '10px' }}
            >
              Repository Root
            </label>
            <input
              type="text"
              value={cwd}
              onChange={(e) => setCwd(e.target.value)}
              placeholder="/path/to/repo"
              className="w-full rounded border bg-transparent px-2 py-1.5 font-mono text-xs outline-none"
              style={{
                borderColor: 'var(--construct-border-soft)',
                color: 'var(--construct-text-primary)',
                background: 'var(--construct-bg-surface)',
              }}
            />
            {recentRepos.length > 0 && (
              <div className="mt-2">
                <label
                  className="mb-1 block font-semibold uppercase tracking-[0.1em]"
                  style={{ color: 'var(--construct-text-faint)', fontSize: '10px' }}
                >
                  Recent
                </label>
                <select
                  value=""
                  onChange={(e) => {
                    if (e.target.value) setCwd(e.target.value);
                  }}
                  className="w-full rounded border bg-transparent px-2 py-1.5 font-mono text-xs outline-none"
                  style={{
                    borderColor: 'var(--construct-border-soft)',
                    color: 'var(--construct-text-primary)',
                    background: 'var(--construct-bg-surface)',
                  }}
                >
                  <option value="" style={{ background: 'var(--construct-bg-panel-strong)' }}>
                    Select recent…
                  </option>
                  {recentRepos.map((p) => (
                    <option key={p} value={p} style={{ background: 'var(--construct-bg-panel-strong)' }}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>
        )}

        {tool === 'operator' && (
          <p className="text-[11px]" style={{ color: 'var(--construct-text-muted)' }}>
            Construct Operator runs as a chat conversation, not a PTY. Starting it will open a Chat
            tab with the Operator context.
          </p>
        )}

        {error && (
          <div
            className="flex items-start gap-2 rounded border px-3 py-2 text-[11px]"
            style={{
              borderColor: 'rgba(255,107,122,0.3)',
              background: 'rgba(255,107,122,0.06)',
              color: 'var(--construct-status-danger)',
            }}
          >
            <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <button
          type="button"
          onClick={startSession}
          disabled={starting}
          className="flex w-full items-center justify-center gap-1.5 rounded px-3 py-2 text-xs font-semibold uppercase tracking-[0.12em] transition-colors disabled:opacity-50"
          style={{
            background: colors.primary + '22',
            color: colors.primary,
            textShadow: colors.glow,
            border: `1px solid ${colors.primary}`,
          }}
        >
          <Play className="h-3 w-3" />
          {starting ? 'Starting…' : 'Start Session'}
        </button>
      </div>
    </div>
  );
}
