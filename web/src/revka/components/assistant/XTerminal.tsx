import { useCallback, useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import { apiFetch } from '@/lib/api';
import { getToken } from '@/lib/auth';
import { apiOrigin, basePath } from '@/lib/basePath';
import { isTauri } from '@/lib/tauri';
import type { AssistantConfig, SchemeColors } from './assistantConfig';

/* ── API-mode endpoints ───────────────────────────── */

const ENDPOINTS: Record<string, string> = {
  status: '/api/status',
  cost: '/api/cost',
  agents: '/api/agents',
  config: '/api/config',
  sessions: '/api/sessions',
  health: '/api/health',
  integrations: '/api/integrations',
};

const HELP_TEXT = [
  '',
  '\x1b[36m  Available commands:\x1b[0m',
  '    status        runtime status',
  '    cost          cost summary',
  '    agents        list agents',
  '    config        show config',
  '    sessions      active sessions',
  '    health        health snapshot',
  '    integrations  list integrations',
  '    clear         clear terminal',
  '    help          this message',
  '',
].join('\r\n');

const BANNER = [
  '',
  '\x1b[36m  ╔═══════════════════════════════════════╗\x1b[0m',
  '\x1b[36m  ║\x1b[0m  Revka Terminal v2.0                \x1b[36m║\x1b[0m',
  '\x1b[36m  ║\x1b[0m  Type \x1b[32m"help"\x1b[0m for available commands   \x1b[36m║\x1b[0m',
  '\x1b[36m  ╚═══════════════════════════════════════╝\x1b[0m',
  '',
].join('\r\n');

/* ── xterm theme from color scheme ────────────────── */

function buildXtermTheme(colors: SchemeColors): Record<string, string> {
  return {
    background: '#0b0d10',
    foreground: colors.primary,
    cursor: colors.cursorColor,
    cursorAccent: '#0b0d10',
    selectionBackground: 'rgba(111,131,214,0.18)',
    selectionForeground: '#ffffff',
    black: '#1a1a1a',
    red: '#ff6b7a',
    green: '#2fbf75',
    yellow: '#ffc857',
    blue: '#8fa8c8',
    magenta: '#7c8ddd',
    cyan: '#8fa8c8',
    white: '#e6e6e6',
    brightBlack: '#4a4a4a',
    brightRed: '#ff8a97',
    brightGreen: '#55d18f',
    brightYellow: '#ffd980',
    brightBlue: '#a4bad6',
    brightMagenta: '#95a2e6',
    brightCyan: '#a4bad6',
    brightWhite: '#ffffff',
  };
}

/* ── WebSocket URL helper ─────────────────────────── */

function getTerminalWsUrl(sessionId: string, cols: number, rows: number): string {
  let base: string;
  if (isTauri() && apiOrigin) {
    base = apiOrigin.replace(/^http/, 'ws');
  } else {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    base = `${protocol}//${window.location.host}`;
  }
  const params = new URLSearchParams();
  const token = getToken();
  if (token) params.set('token', token);
  params.set('session_id', sessionId);
  // Pre-size the PTY so the spawned shell's winsize is right on first spawn.
  // Matters for prompts that wrap and for TUIs (without this the shell sees
  // 80×24 and readline wraps a step behind reality).
  if (cols > 0) params.set('cols', String(cols));
  if (rows > 0) params.set('rows', String(rows));
  return `${base}${basePath}/ws/terminal?${params.toString()}`;
}

/* ── Component ────────────────────────────────────── */

interface XTerminalProps {
  sessionId: string;
  config: AssistantConfig;
  colors: SchemeColors;
  visible: boolean;
}

export default function XTerminal({ sessionId, config, colors, visible }: XTerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const lineBufferRef = useRef('');
  const [mode, setMode] = useState<'connecting' | 'pty' | 'api'>('connecting');
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const busyRef = useRef(false);

  const writePrompt = useCallback(() => {
    termRef.current?.write('\x1b[32m❯\x1b[0m ');
  }, []);

  const runApiCommand = useCallback(async (cmd: string) => {
    const term = termRef.current;
    if (!term) return;

    const trimmed = cmd.trim().toLowerCase();
    if (!trimmed) { writePrompt(); return; }
    if (trimmed === 'clear') { term.clear(); writePrompt(); return; }
    if (trimmed === 'help') { term.write(HELP_TEXT); writePrompt(); return; }

    const ep = ENDPOINTS[trimmed];
    if (!ep) {
      term.write(`\x1b[31m  unknown command: ${trimmed}\x1b[0m\r\n`);
      term.write('  type \x1b[32m"help"\x1b[0m to see available commands\r\n');
      writePrompt();
      return;
    }

    busyRef.current = true;
    term.write('\x1b[33m  fetching…\x1b[0m');
    try {
      const data = await apiFetch(ep);
      const json = JSON.stringify(data, null, 2);
      // Clear the "fetching…" line and write result
      term.write('\r\x1b[K');
      const lines = json.split('\n');
      for (const line of lines) {
        term.write(`  \x1b[32m${line}\x1b[0m\r\n`);
      }
    } catch (err) {
      term.write('\r\x1b[K');
      term.write(`\x1b[31m  error: ${err instanceof Error ? err.message : String(err)}\x1b[0m\r\n`);
    } finally {
      busyRef.current = false;
      writePrompt();
    }
  }, [writePrompt]);

  // Create terminal instance
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

    // Fit synchronously so term.cols/rows reflect the container before the WS
    // URL is built — the gateway reads these from the query string to openpty
    // at the right size on spawn.
    try { fitAddon.fit(); } catch { /* container may not be visible yet */ }

    termRef.current = term;
    fitAddonRef.current = fitAddon;

    // Write banner
    term.write(BANNER);

    // Attempt WebSocket connection for PTY mode
    let ws: WebSocket | null = null;
    let wsTimedOut = false;

    try {
      const wsUrl = getTerminalWsUrl(sessionId, term.cols, term.rows);
      ws = new WebSocket(wsUrl);
      ws.binaryType = 'arraybuffer';
      wsRef.current = ws;

      const timeout = setTimeout(() => {
        wsTimedOut = true;
        if (ws && ws.readyState !== WebSocket.OPEN) {
          ws.close();
        }
      }, 3000);

      ws.onopen = () => {
        clearTimeout(timeout);
        if (wsTimedOut) return;
        setMode('pty');
        term.write('\x1b[36m  Connected to PTY terminal.\x1b[0m\r\n\r\n');
        // Send initial resize
        const msg = JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows });
        ws!.send(msg);
      };

      ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
          term.write(event.data);
        } else {
          term.write(new Uint8Array(event.data as ArrayBuffer));
        }
      };

      ws.onerror = () => {
        clearTimeout(timeout);
        if (modeRef.current !== 'pty') {
          setMode('api');
          term.write('\x1b[33m  PTY not available — running in API command mode.\x1b[0m\r\n\r\n');
          writePrompt();
        }
      };

      ws.onclose = () => {
        clearTimeout(timeout);
        if (modeRef.current === 'pty') {
          term.write('\r\n\x1b[33m  Terminal session ended.\x1b[0m\r\n');
          setMode('api');
          writePrompt();
        } else if (modeRef.current === 'connecting') {
          setMode('api');
          term.write('\x1b[33m  PTY not available — running in API command mode.\x1b[0m\r\n\r\n');
          writePrompt();
        }
      };
    } catch {
      setMode('api');
      term.write('\x1b[33m  PTY not available — running in API command mode.\x1b[0m\r\n\r\n');
      writePrompt();
    }

    // Handle user input
    term.onData((data) => {
      // PTY mode — forward everything to WebSocket
      if (modeRef.current === 'pty' && wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(data);
        return;
      }

      // API mode — local line editing
      if (modeRef.current !== 'api' || busyRef.current) return;

      for (const ch of data) {
        if (ch === '\r') {
          // Enter
          term.write('\r\n');
          const cmd = lineBufferRef.current;
          lineBufferRef.current = '';
          runApiCommand(cmd);
        } else if (ch === '\u007f' || ch === '\b') {
          // Backspace
          if (lineBufferRef.current.length > 0) {
            lineBufferRef.current = lineBufferRef.current.slice(0, -1);
            term.write('\b \b');
          }
        } else if (ch === '\u0003') {
          // Ctrl+C
          term.write('^C\r\n');
          lineBufferRef.current = '';
          writePrompt();
        } else if (ch === '\u000c') {
          // Ctrl+L
          term.clear();
          writePrompt();
        } else if (ch >= ' ') {
          lineBufferRef.current += ch;
          term.write(ch);
        }
      }
    });

    // Handle resize
    term.onResize(({ cols, rows }) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    });

    // ResizeObserver for container changes
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
  }, [sessionId]);

  // Update theme when colors change
  useEffect(() => {
    if (!termRef.current) return;
    termRef.current.options.theme = buildXtermTheme(colors);
  }, [colors]);

  // Update cursor blink
  useEffect(() => {
    if (!termRef.current) return;
    termRef.current.options.cursorBlink = config.cursorBlink;
  }, [config.cursorBlink]);

  // Update font size
  useEffect(() => {
    if (!termRef.current) return;
    termRef.current.options.fontSize = config.fontSize;
    try { fitAddonRef.current?.fit(); } catch { /* ignore */ }
  }, [config.fontSize]);

  // Re-fit when visibility changes
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
      style={{
        display: visible ? 'block' : 'none',
        padding: '4px 0',
      }}
    />
  );
}
