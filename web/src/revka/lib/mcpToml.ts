// Lightweight TOML read/write helpers scoped to the [mcp] block.
//
// We intentionally do NOT bring in a full TOML library. The Revka config
// parser elsewhere in Config already runs on a bespoke reader; we mirror
// its tolerant style here but keep MCP-specific logic isolated so it can be
// unit-tested in dev via the round-trip guard at the bottom of this file.

export type McpTransport = 'stdio' | 'http' | 'sse';

export interface McpServerEntry {
  name: string;
  transport: McpTransport;
  // stdio
  command?: string;
  args?: string[];
  env?: Record<string, string>;
  // http / sse
  url?: string;
  headers?: Record<string, string>;
  timeout_ms?: number;
}

export interface McpConfig {
  enabled: boolean;
  deferred_loading: boolean;
  servers: McpServerEntry[];
}

// ---------------------------------------------------------------------------
// scalar helpers
// ---------------------------------------------------------------------------

function stripInlineComment(raw: string): string {
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < raw.length; i += 1) {
    const c = raw[i];
    if (c === "'" && !inDouble) inSingle = !inSingle;
    else if (c === '"' && !inSingle) inDouble = !inDouble;
    else if (c === '#' && !inSingle && !inDouble) return raw.slice(0, i).trimEnd();
  }
  return raw;
}

function parseScalar(raw: string): string | number | boolean {
  const s = raw.trim();
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (/^-?\d+$/.test(s)) return parseInt(s, 10);
  if (/^-?\d+\.\d+$/.test(s)) return parseFloat(s);
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) {
    return s.slice(1, -1);
  }
  return s;
}

function parseArrayOfStrings(raw: string): string[] {
  const s = raw.trim();
  if (!s.startsWith('[') || !s.endsWith(']')) return [];
  const inner = s.slice(1, -1).trim();
  if (!inner) return [];
  return inner
    .split(',')
    .map((part) => {
      const t = part.trim();
      if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
        return t.slice(1, -1);
      }
      return t;
    })
    .filter((s) => s.length > 0);
}

function parseInlineTable(raw: string): Record<string, string> {
  // Accepts: { KEY = "val", OTHER = "v2" } — values coerced to string.
  const s = raw.trim();
  if (!s.startsWith('{') || !s.endsWith('}')) return {};
  const inner = s.slice(1, -1).trim();
  if (!inner) return {};
  const out: Record<string, string> = {};
  // Split on commas that are NOT inside quotes.
  const parts: string[] = [];
  let current = '';
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < inner.length; i += 1) {
    const c = inner[i]!;
    if (c === "'" && !inDouble) inSingle = !inSingle;
    else if (c === '"' && !inSingle) inDouble = !inDouble;
    if (c === ',' && !inSingle && !inDouble) {
      parts.push(current);
      current = '';
    } else {
      current += c;
    }
  }
  if (current.trim()) parts.push(current);
  for (const part of parts) {
    const eq = part.indexOf('=');
    if (eq === -1) continue;
    const key = part.slice(0, eq).trim();
    const val = part.slice(eq + 1).trim();
    const parsed = parseScalar(val);
    out[key] = typeof parsed === 'string' ? parsed : String(parsed);
  }
  return out;
}

function serializeString(value: string): string {
  return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

function serializeStringArray(values: string[]): string {
  if (values.length === 0) return '[]';
  return `[${values.map(serializeString).join(', ')}]`;
}

function serializeInlineTable(record: Record<string, string>): string {
  const entries = Object.entries(record).filter(([k]) => k.length > 0);
  if (entries.length === 0) return '{}';
  return `{ ${entries.map(([k, v]) => `${k} = ${serializeString(v)}`).join(', ')} }`;
}

// ---------------------------------------------------------------------------
// read: extract [mcp] + [[mcp.servers]] from the raw TOML string
// ---------------------------------------------------------------------------

export function parseMcpBlock(toml: string): McpConfig {
  const lines = toml.split('\n');
  const config: McpConfig = { enabled: false, deferred_loading: false, servers: [] };
  let mode: 'none' | 'mcp' | 'server' = 'none';
  let current: Partial<McpServerEntry> | null = null;

  const commit = () => {
    if (current) {
      const transport = (current.transport ?? 'stdio') as McpTransport;
      config.servers.push({ ...current, name: current.name ?? '', transport });
    }
    current = null;
  };

  for (let i = 0; i < lines.length; i += 1) {
    const rawLine = lines[i]!;
    const trimmed = rawLine.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    if (trimmed === '[[mcp.servers]]') {
      commit();
      mode = 'server';
      current = {};
      continue;
    }
    const sectionMatch = trimmed.match(/^\[([^\]]+)\]$/);
    if (sectionMatch) {
      commit();
      const name = sectionMatch[1]!.trim();
      mode = name === 'mcp' ? 'mcp' : 'none';
      continue;
    }
    if (mode === 'none') continue;

    const kvMatch = trimmed.match(/^([A-Za-z0-9_\-.]+)\s*=\s*(.+)$/);
    if (!kvMatch) continue;
    const key = kvMatch[1]!;
    let rawVal = stripInlineComment(kvMatch[2]!);

    // multi-line array support
    if (rawVal.trim().startsWith('[') && !rawVal.trim().endsWith(']')) {
      let accumulated = rawVal.trim();
      for (let j = i + 1; j < lines.length; j += 1) {
        i = j;
        const cont = stripInlineComment(lines[j]!).trim();
        accumulated += ` ${cont}`;
        if (cont.endsWith(']')) break;
      }
      rawVal = accumulated;
    }

    if (mode === 'mcp') {
      if (key === 'enabled') {
        const v = parseScalar(rawVal);
        config.enabled = v === true;
      } else if (key === 'deferred_loading') {
        const v = parseScalar(rawVal);
        config.deferred_loading = v === true;
      }
      continue;
    }
    if (mode === 'server' && current) {
      switch (key) {
        case 'name':
        case 'command':
        case 'url':
        case 'transport': {
          const v = parseScalar(rawVal);
          (current as Record<string, unknown>)[key] = typeof v === 'string' ? v : String(v);
          break;
        }
        case 'args': {
          current.args = parseArrayOfStrings(rawVal);
          break;
        }
        case 'timeout_ms': {
          const v = parseScalar(rawVal);
          if (typeof v === 'number') current.timeout_ms = v;
          break;
        }
        case 'env': {
          current.env = parseInlineTable(rawVal);
          break;
        }
        case 'headers': {
          current.headers = parseInlineTable(rawVal);
          break;
        }
        default:
          break;
      }
    }
  }
  commit();
  return config;
}

// ---------------------------------------------------------------------------
// write: replace the [mcp] block (and all [[mcp.servers]]) with a fresh one
// ---------------------------------------------------------------------------

function stripMcpRegion(toml: string): { before: string; after: string } {
  // Identify the span of the current mcp region: the first line that is
  // either `[mcp]` or `[[mcp.servers]]` through to (but not including) the
  // next top-level section header that is NOT under mcp.*.
  const lines = toml.split('\n');
  let start = -1;
  let end = lines.length;
  for (let i = 0; i < lines.length; i += 1) {
    const t = lines[i]!.trim();
    if (t === '[mcp]' || t === '[[mcp.servers]]') {
      if (start === -1) start = i;
    }
  }
  if (start === -1) {
    return { before: toml, after: '' };
  }
  for (let i = start + 1; i < lines.length; i += 1) {
    const t = lines[i]!.trim();
    const sec = t.match(/^\[\[?([^\]]+)\]?\]$/);
    if (sec) {
      const name = sec[1]!.trim();
      if (name !== 'mcp' && !name.startsWith('mcp.')) {
        end = i;
        break;
      }
    }
  }
  // Trim a single trailing blank line if present so we don't accumulate.
  let stopBefore = start;
  while (stopBefore > 0 && lines[stopBefore - 1]!.trim() === '') {
    stopBefore -= 1;
  }
  const before = lines.slice(0, stopBefore).join('\n');
  const after = lines.slice(end).join('\n');
  return { before, after };
}

function renderMcpRegion(config: McpConfig): string {
  const out: string[] = [];
  out.push('[mcp]');
  out.push(`enabled = ${config.enabled ? 'true' : 'false'}`);
  out.push(`deferred_loading = ${config.deferred_loading ? 'true' : 'false'}`);
  for (const server of config.servers) {
    out.push('');
    out.push('[[mcp.servers]]');
    out.push(`name = ${serializeString(server.name)}`);
    out.push(`transport = ${serializeString(server.transport)}`);
    if (server.transport === 'stdio') {
      if (server.command !== undefined) {
        out.push(`command = ${serializeString(server.command)}`);
      }
      if (server.args && server.args.length > 0) {
        out.push(`args = ${serializeStringArray(server.args)}`);
      }
      if (server.env && Object.keys(server.env).length > 0) {
        out.push(`env = ${serializeInlineTable(server.env)}`);
      }
    } else {
      if (server.url !== undefined) {
        out.push(`url = ${serializeString(server.url)}`);
      }
      if (server.headers && Object.keys(server.headers).length > 0) {
        out.push(`headers = ${serializeInlineTable(server.headers)}`);
      }
      if (typeof server.timeout_ms === 'number') {
        out.push(`timeout_ms = ${server.timeout_ms}`);
      }
    }
  }
  return out.join('\n');
}

export function patchMcpBlock(toml: string, config: McpConfig): string {
  const { before, after } = stripMcpRegion(toml);
  const region = renderMcpRegion(config);
  const parts: string[] = [];
  if (before.trim().length > 0) {
    parts.push(before.replace(/\s+$/, ''));
    parts.push('');
  }
  parts.push(region);
  if (after.trim().length > 0) {
    parts.push('');
    parts.push(after.replace(/^\s+/, ''));
  }
  let result = parts.join('\n');
  if (!result.endsWith('\n')) result += '\n';
  return result;
}

// ---------------------------------------------------------------------------
// validation
// ---------------------------------------------------------------------------

export interface McpServerErrors {
  name?: string;
  command?: string;
  url?: string;
}

export function validateServers(servers: McpServerEntry[]): Record<number, McpServerErrors> {
  const errs: Record<number, McpServerErrors> = {};
  const seen = new Map<string, number>();
  servers.forEach((server, idx) => {
    const e: McpServerErrors = {};
    const name = server.name.trim();
    if (!name) {
      e.name = 'Name is required';
    } else if (seen.has(name)) {
      e.name = `Duplicate name (also used by entry ${seen.get(name)! + 1})`;
    } else {
      seen.set(name, idx);
    }
    if (server.transport === 'stdio') {
      if (!server.command || !server.command.trim()) {
        e.command = 'Command is required for stdio transport';
      }
    } else {
      if (!server.url || !server.url.trim()) {
        e.url = 'URL is required for http/sse transport';
      }
    }
    if (e.name || e.command || e.url) errs[idx] = e;
  });
  return errs;
}

export function hasErrors(errs: Record<number, McpServerErrors>): boolean {
  return Object.keys(errs).length > 0;
}

// ---------------------------------------------------------------------------
// dev-only round-trip sanity guard
// ---------------------------------------------------------------------------

// Run a tiny round-trip check at module import in development builds so any
// regression in patch/parse is caught at the dev console immediately. Guarded
// to avoid any cost in production bundles.
if (typeof import.meta !== 'undefined' && (import.meta as { env?: { DEV?: boolean } }).env?.DEV) {
  try {
    const sample: McpConfig = {
      enabled: true,
      deferred_loading: false,
      servers: [
        {
          name: 'memory',
          transport: 'stdio',
          command: '/usr/local/bin/mcp-memory',
          args: ['--db', '/tmp/mem.sqlite'],
          env: { LOG: 'info' },
        },
        {
          name: 'web',
          transport: 'http',
          url: 'http://localhost:9000/mcp',
          headers: { Authorization: 'Bearer abc' },
          timeout_ms: 30000,
        },
      ],
    };
    const base = '[default]\nname = "x"\n\n[mcp]\nenabled = false\ndeferred_loading = false\n\n[after]\nk = 1\n';
    const patched = patchMcpBlock(base, sample);
    const parsed = parseMcpBlock(patched);
    if (parsed.servers.length !== 2) {
      // eslint-disable-next-line no-console
      console.warn('[mcpToml] round-trip server count mismatch', parsed);
    }
    if (!patched.includes('[after]')) {
      // eslint-disable-next-line no-console
      console.warn('[mcpToml] round-trip lost trailing section');
    }
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[mcpToml] round-trip check threw', err);
  }
}
