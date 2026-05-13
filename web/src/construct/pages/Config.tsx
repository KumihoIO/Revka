import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Check, ChevronDown, ChevronRight, Eye, EyeOff, Plus, RefreshCw, Save, Settings, Trash2, X as XIcon } from 'lucide-react';
import { getConfig, putConfig, testMcpServer, type McpServerTestResult } from '@/lib/api';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import Notice from '../components/ui/Notice';
import StateMessage from '../components/ui/StateMessage';
import {
  hasErrors as mcpHasErrors,
  parseMcpBlock,
  patchMcpBlock,
  validateServers,
  type McpConfig,
  type McpServerEntry,
  type McpServerErrors,
  type McpTransport,
} from '../lib/mcpToml';
import { useT } from '@/construct/hooks/useT';

interface ParsedConfig {
  api_key?: string;
  api_url?: string;
  default_provider?: string;
  default_model?: string;
  default_temperature?: number;
  provider_timeout_secs?: number;
  provider_max_tokens?: number;
  agent?: {
    max_tool_iterations?: number;
    max_context_tokens?: number;
    max_history_messages?: number;
    parallel_tools?: boolean;
    context_compression?: { enabled?: boolean };
    thinking?: { default_level?: string };
  };
  kumiho?: {
    enabled?: boolean;
    api_url?: string;
    memory_project?: string;
    harness_project?: string;
    memory_retrieval_limit?: number;
  };
  operator?: {
    enabled?: boolean;
    max_tool_iterations?: number;
  };
  memory?: {
    backend?: string;
    auto_save?: boolean;
    hygiene_enabled?: boolean;
    snapshot_enabled?: boolean;
  };
  gateway?: {
    port?: number;
    host?: string;
    require_pairing?: boolean;
    allow_public_bind?: boolean;
    session_persistence?: boolean;
  };
  autonomy?: {
    level?: string;
    workspace_only?: boolean;
    max_actions_per_hour?: number;
  };
  cost?: {
    enabled?: boolean;
    daily_limit_usd?: number;
    monthly_limit_usd?: number;
    warn_at_percent?: number;
  };
  security?: {
    audit?: { enabled?: boolean };
    sandbox?: { backend?: string };
  };
  channels_config?: {
    cli?: boolean;
    telegram?: unknown;
    discord?: unknown;
    slack?: unknown;
    matrix?: unknown;
    email?: unknown;
    signal?: unknown;
    whatsapp?: unknown;
    irc?: unknown;
    message_timeout_secs?: number;
    ack_reactions?: boolean;
    show_tool_calls?: boolean;
  };
}

type ConfigSectionKey = 'provider' | 'agent' | 'memory' | 'runtime' | 'security' | 'channels' | 'mcp';

type TFn = (key: string) => string;
type TplFn = (key: string, vars: Record<string, string | number>) => string;

function findUnquotedHash(s: string): number {
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < s.length; i += 1) {
    const c = s[i];
    if (c === "'" && !inDouble) inSingle = !inSingle;
    else if (c === '"' && !inSingle) inDouble = !inDouble;
    else if (c === '#' && !inSingle && !inDouble) return i;
  }
  return -1;
}

function parseTOMLValue(raw: string): string | number | boolean | string[] {
  const s = raw.trim();
  if (s === 'true') return true;
  if (s === 'false') return false;
  if (/^-?\d+$/.test(s)) return parseInt(s, 10);
  if (/^-?\d+\.\d+$/.test(s)) return parseFloat(s);
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) return s.slice(1, -1);
  if (s.startsWith('[') && s.endsWith(']')) {
    const inner = s.slice(1, -1).trim();
    if (!inner) return [];
    return inner.split(',').map((part) => {
      const trimmed = part.trim();
      if ((trimmed.startsWith('"') && trimmed.endsWith('"')) || (trimmed.startsWith("'") && trimmed.endsWith("'"))) {
        return trimmed.slice(1, -1);
      }
      return trimmed;
    });
  }
  return s;
}

function parseTOML(toml: string): ParsedConfig {
  const result: Record<string, Record<string, unknown>> = { '': {} };
  let currentSection = '';
  const lines = toml.split('\n');

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const trimmed = lines[lineIndex]!.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    const sectionMatch = trimmed.match(/^\[([^\]]+)\]$/);
    if (sectionMatch) {
      currentSection = sectionMatch[1]!.trim();
      if (!result[currentSection]) result[currentSection] = {};
      continue;
    }

    const kvMatch = trimmed.match(/^([A-Za-z0-9_\-.]+)\s*=\s*(.+)$/);
    if (!kvMatch) continue;
    const key = kvMatch[1]!;
    let rawVal = kvMatch[2]!;
    const hashIdx = findUnquotedHash(rawVal);
    if (hashIdx !== -1) rawVal = rawVal.slice(0, hashIdx).trimEnd();

    const trimmedRaw = rawVal.trim();
    if (trimmedRaw.startsWith('[') && !trimmedRaw.includes(']')) {
      let accumulated = trimmedRaw;
      for (let j = lineIndex + 1; j < lines.length; j += 1) {
        lineIndex = j;
        const contLine = lines[j]!.trim();
        accumulated += ` ${contLine}`;
        if (contLine.endsWith(']')) break;
      }
      rawVal = accumulated;
    }

    if (!result[currentSection]) result[currentSection] = {};
    result[currentSection]![key] = parseTOMLValue(rawVal);
  }

  const config: Record<string, unknown> = {};
  Object.assign(config, result[''] ?? {});

  for (const [section, values] of Object.entries(result)) {
    if (section === '') continue;
    const parts = section.split('.');
    let target: Record<string, unknown> = config;
    for (let i = 0; i < parts.length; i += 1) {
      const part = parts[i]!;
      if (i === parts.length - 1) {
        if (!target[part] || typeof target[part] !== 'object') target[part] = {};
        Object.assign(target[part] as Record<string, unknown>, values);
      } else {
        if (!target[part] || typeof target[part] !== 'object') target[part] = {};
        target = target[part] as Record<string, unknown>;
      }
    }
  }

  return config as ParsedConfig;
}

function colorScalar(v: string): string {
  const trimmed = v.trim();
  if (trimmed === 'true' || trimmed === 'false') return `<span style="color:#7dfb9b">${v}</span>`;
  if (/^-?\d[\d_]*(\.[\d_]*)?([eE][+-]?\d+)?$/.test(trimmed)) return `<span style="color:#ffd166">${v}</span>`;
  if (trimmed.startsWith('"') || trimmed.startsWith("'")) return `<span style="color:#9be7a7">${v}</span>`;
  if (trimmed.startsWith('[') || trimmed.startsWith('{')) return `<span style="color:#d7e4dc">${v}</span>`;
  return v;
}

function colorValue(v: string): string {
  const trimmed = v.trim();
  const commentIdx = findUnquotedHash(trimmed);
  if (commentIdx !== -1) {
    const valueCore = trimmed.slice(0, commentIdx).trimEnd();
    const comment = `<span style="color:#6b7280;font-style:italic">${trimmed.slice(commentIdx)}</span>`;
    const leading = v.slice(0, v.indexOf(trimmed));
    return `${leading}${colorScalar(valueCore)} ${comment}`;
  }
  return colorScalar(v);
}

function highlightToml(raw: string): string {
  const result: string[] = [];
  for (const line of raw.split('\n')) {
    const escaped = line.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    if (/^\s*\[/.test(escaped)) {
      result.push(`<span style="color:#72d8ff;font-weight:600">${escaped}</span>`);
      continue;
    }
    if (/^\s*#/.test(escaped)) {
      result.push(`<span style="color:#6b7280;font-style:italic">${escaped}</span>`);
      continue;
    }
    const kvMatch = escaped.match(/^(\s*)([A-Za-z0-9_\-.]+)(\s*=\s*)(.*)$/);
    if (kvMatch) {
      const [, indent, key, eq, rawValue] = kvMatch;
      result.push(`${indent}<span style="color:#a78bfa">${key}</span><span style="color:#71717a">${eq}</span>${colorValue(rawValue ?? '')}`);
      continue;
    }
    result.push(escaped);
  }
  return `${result.join('\n')}\n`;
}

function formatTomlScalar(value: string | number | boolean): string {
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') return String(value);
  return JSON.stringify(value);
}

function upsertTomlField(raw: string, sectionPath: string | null, key: string, value: string | number | boolean): string {
  const lines = raw.split('\n');
  const newLine = `${key} = ${formatTomlScalar(value)}`;

  if (!sectionPath) {
    const firstSectionIndex = lines.findIndex((line) => line.trim().startsWith('['));
    const searchEnd = firstSectionIndex === -1 ? lines.length : firstSectionIndex;
    for (let i = 0; i < searchEnd; i += 1) {
      if (new RegExp(`^\\s*${key}\\s*=`).test(lines[i] ?? '')) {
        lines[i] = newLine;
        return lines.join('\n');
      }
    }
    lines.splice(searchEnd, 0, newLine);
    return lines.join('\n');
  }

  const sectionHeader = `[${sectionPath}]`;
  const sectionStart = lines.findIndex((line) => line.trim() === sectionHeader);

  if (sectionStart === -1) {
    const prefix = raw.endsWith('\n') || raw.length === 0 ? '' : '\n';
    return `${raw}${prefix}${raw.trim() ? '\n' : ''}${sectionHeader}\n${newLine}\n`;
  }

  let sectionEnd = lines.length;
  for (let i = sectionStart + 1; i < lines.length; i += 1) {
    if (lines[i]?.trim().startsWith('[')) {
      sectionEnd = i;
      break;
    }
  }

  for (let i = sectionStart + 1; i < sectionEnd; i += 1) {
    if (new RegExp(`^\\s*${key}\\s*=`).test(lines[i] ?? '')) {
      lines[i] = newLine;
      return lines.join('\n');
    }
  }

  lines.splice(sectionEnd, 0, newLine);
  return lines.join('\n');
}

function countDefined(values: unknown[]) {
  return values.filter((value) => value !== undefined && value !== '').length;
}

function countDefinedDeep(value: unknown): number {
  if (value === undefined || value === null || value === '') return 0;
  if (Array.isArray(value)) return value.length > 0 ? 1 : 0;
  if (typeof value === 'object') {
    return Object.values(value as Record<string, unknown>).reduce<number>((acc, v) => acc + countDefinedDeep(v), 0);
  }
  return 1;
}

// Top-level TOML sections already handled by named renderers (provider/agent/memory/runtime/security/channels/mcp).
// Anything else in config.toml is discovered dynamically and rendered generically.
const KNOWN_TOML_SECTIONS = new Set([
  'agent',
  'memory',
  'kumiho',
  'gateway',
  'autonomy',
  'security',
  'cost',
  'channels_config',
  'mcp',
  'operator',
]);

interface DiscoveredSection {
  id: string;
  path: string;
  data: Record<string, unknown>;
}

function discoverExtraSections(parsed: ParsedConfig | null): DiscoveredSection[] {
  if (!parsed) return [];
  const out: DiscoveredSection[] = [];
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (KNOWN_TOML_SECTIONS.has(key)) continue;
    if (value === null || typeof value !== 'object' || Array.isArray(value)) continue;
    out.push({ id: `ext:${key}`, path: key, data: value as Record<string, unknown> });
  }
  return out.sort((a, b) => a.path.localeCompare(b.path));
}

function buildConfigSections(
  parsedConfig: ParsedConfig | null,
  mcpServerCount: number,
  mcpEnabled: boolean,
  t: TFn,
  extraSections: DiscoveredSection[],
) {
  const known = [
    {
      id: 'provider' as const,
      title: t('config.section.provider'),
      description: t('config.section.provider_desc'),
      populatedCount: countDefined([
        parsedConfig?.default_provider,
        parsedConfig?.default_model,
        parsedConfig?.default_temperature,
        parsedConfig?.provider_timeout_secs,
        parsedConfig?.provider_max_tokens,
      ]),
      paths: ['default_provider', 'default_model', 'default_temperature', 'provider_timeout_secs', 'provider_max_tokens'],
    },
    {
      id: 'agent' as const,
      title: t('config.section.agent'),
      description: t('config.section.agent_desc'),
      populatedCount: countDefined([
        parsedConfig?.agent?.max_tool_iterations,
        parsedConfig?.agent?.max_context_tokens,
        parsedConfig?.agent?.max_history_messages,
        parsedConfig?.agent?.parallel_tools,
        parsedConfig?.agent?.thinking?.default_level,
      ]),
      paths: ['agent.max_tool_iterations', 'agent.max_context_tokens', 'agent.max_history_messages', 'agent.parallel_tools', 'agent.thinking.default_level'],
    },
    {
      id: 'memory' as const,
      title: t('config.section.memory'),
      description: t('config.section.memory_desc'),
      populatedCount: countDefined([
        parsedConfig?.memory?.backend,
        parsedConfig?.memory?.auto_save,
        parsedConfig?.memory?.snapshot_enabled,
        parsedConfig?.kumiho?.enabled,
        parsedConfig?.kumiho?.memory_project,
        parsedConfig?.kumiho?.memory_retrieval_limit,
      ]),
      paths: ['memory.backend', 'memory.auto_save', 'memory.snapshot_enabled', 'kumiho.enabled', 'kumiho.memory_project', 'kumiho.harness_project', 'kumiho.memory_retrieval_limit'],
    },
    {
      id: 'runtime' as const,
      title: t('config.section.runtime'),
      description: t('config.section.runtime_desc'),
      populatedCount: countDefined([
        parsedConfig?.gateway?.host,
        parsedConfig?.gateway?.port,
        parsedConfig?.gateway?.require_pairing,
        parsedConfig?.autonomy?.level,
        parsedConfig?.autonomy?.max_actions_per_hour,
      ]),
      paths: ['gateway.host', 'gateway.port', 'gateway.require_pairing', 'gateway.session_persistence', 'autonomy.level', 'autonomy.max_actions_per_hour'],
    },
    {
      id: 'security' as const,
      title: t('config.section.security'),
      description: t('config.section.security_desc'),
      populatedCount: countDefined([
        parsedConfig?.security?.audit?.enabled,
        parsedConfig?.security?.sandbox?.backend,
        parsedConfig?.cost?.daily_limit_usd,
        parsedConfig?.cost?.monthly_limit_usd,
        parsedConfig?.cost?.warn_at_percent,
      ]),
      paths: ['security.audit.enabled', 'security.sandbox.backend', 'cost.daily_limit_usd', 'cost.monthly_limit_usd', 'cost.warn_at_percent'],
    },
    {
      id: 'channels' as const,
      title: t('config.section.channels'),
      description: t('config.section.channels_desc'),
      populatedCount: countDefined([
        parsedConfig?.channels_config?.cli,
        parsedConfig?.channels_config?.ack_reactions,
        parsedConfig?.channels_config?.show_tool_calls,
        parsedConfig?.channels_config?.message_timeout_secs,
      ]),
      paths: ['channels_config.cli', 'channels_config.ack_reactions', 'channels_config.show_tool_calls', 'channels_config.message_timeout_secs'],
    },
    {
      id: 'mcp' as string,
      title: t('config.section.mcp'),
      description: t('config.section.mcp_desc'),
      populatedCount: (mcpEnabled ? 1 : 0) + mcpServerCount,
      paths: ['mcp.enabled', 'mcp.deferred_loading', 'mcp.servers[]'],
    },
  ];

  const extras = extraSections.map((section) => ({
    id: section.id,
    title: `[${section.path}]`,
    description: t('config.section.extra_desc'),
    populatedCount: countDefinedDeep(section.data),
    paths: [section.path],
  }));

  return [...known, ...extras];
}

export default function Config() {
  const { t, tpl } = useT();
  const [rawConfig, setRawConfig] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showPreview, setShowPreview] = useState(true);
  const [workspaceTab, setWorkspaceTab] = useState<'structured' | 'source'>('structured');
  const [activeSection, setActiveSection] = useState<string>('provider');
  const [notice, setNotice] = useState<{ tone: 'success' | 'error' | 'info'; message: string } | null>(null);

  const tRef = useRef(t);
  tRef.current = t;

  const loadConfig = useCallback(async () => {
    setLoading(true);
    try {
      const config = await getConfig();
      setRawConfig(config);
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : tRef.current('config.err.load') });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);

  // Defer heavy parsing/highlighting so fast typing in the source textarea
  // doesn't repaint the syntax-highlighted preview on every keystroke.
  const deferredRawConfig = useDeferredValue(rawConfig);

  const parsedConfig = useMemo(() => {
    try {
      return parseTOML(deferredRawConfig);
    } catch {
      return null;
    }
  }, [deferredRawConfig]);

  const mcpConfig = useMemo<McpConfig>(() => {
    try {
      return parseMcpBlock(deferredRawConfig);
    } catch {
      return { enabled: false, deferred_loading: false, servers: [] };
    }
  }, [deferredRawConfig]);

  const mcpErrors = useMemo(() => validateServers(mcpConfig.servers), [mcpConfig.servers]);

  const highlightedToml = useMemo(() => highlightToml(deferredRawConfig), [deferredRawConfig]);
  const discoveredSections = useMemo(() => discoverExtraSections(parsedConfig), [parsedConfig]);
  const sectionItems = useMemo(
    () => buildConfigSections(parsedConfig, mcpConfig.servers.length, mcpConfig.enabled, t, discoveredSections),
    [parsedConfig, mcpConfig.servers.length, mcpConfig.enabled, t, discoveredSections],
  );
  const activeSectionItem = sectionItems.find((section) => section.id === activeSection) ?? sectionItems[0];

  const saveConfig = async () => {
    if (mcpHasErrors(mcpErrors)) {
      setNotice({ tone: 'error', message: t('config.err.mcp_errors') });
      setActiveSection('mcp');
      return;
    }
    setSaving(true);
    try {
      await putConfig(rawConfig);
      setNotice({ tone: 'success', message: t('config.toast.saved') });
    } catch (err) {
      setNotice({ tone: 'error', message: err instanceof Error ? err.message : t('config.err.save') });
    } finally {
      setSaving(false);
    }
  };

  const updateField = (sectionPath: string | null, key: string, value: string | number | boolean) => {
    setRawConfig((current) => upsertTomlField(current, sectionPath, key, value));
  };

  const applyMcpConfig = (next: McpConfig) => {
    setRawConfig((current) => patchMcpBlock(current, next));
  };

  return (
    <div className="flex min-h-[calc(100vh-6rem)] flex-col gap-3 md:h-[calc(100vh-6rem)]">
      {notice ? <Notice tone={notice.tone} message={notice.message} onDismiss={() => setNotice(null)} /> : null}
      <PageHeader
        kicker={t('config.kicker')}
        title={t('config.page_title')}
        actions={(
          <>
            <button className="construct-button" onClick={() => void loadConfig()}>
              <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {t('config.reload')}
            </button>
            <button className="construct-button" data-variant="primary" onClick={saveConfig} disabled={saving || loading}>
              <Save className="h-4 w-4" />
              {saving ? t('config.saving_button') : t('config.save_button')}
            </button>
          </>
        )}
      />

      {loading ? (
        <div className="flex min-h-0 flex-1 items-center justify-center">
          <StateMessage tone="loading" title={t('config.loading_title')} />
        </div>
      ) : (
        <div className="grid gap-4 grid-cols-1 md:min-h-0 md:flex-1 md:grid-cols-[18rem_minmax(0,1fr)] md:[grid-template-rows:minmax(0,1fr)] lg:grid-cols-[18rem_minmax(0,1fr)_24rem]">
          <div className="flex flex-col gap-4 md:overflow-y-auto md:min-h-0">
            <Panel className="shrink-0 p-4" variant="secondary">
              <div className="construct-kicker">{t('config.map')}</div>
              <div className="mt-3 space-y-2">
                {sectionItems.map((section) => (
                  <button
                    key={section.id}
                    type="button"
                    className="construct-selection-card text-left"
                    data-active={String(activeSection === section.id)}
                    data-accent="workflow"
                    onClick={() => {
                      setActiveSection(section.id);
                      setWorkspaceTab('structured');
                    }}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-sm font-medium">{section.title}</span>
                      <span className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>{section.populatedCount}</span>
                    </div>
                    <div className="mt-2 text-xs leading-5" style={{ color: 'var(--construct-text-secondary)' }}>{section.description}</div>
                  </button>
                ))}
              </div>
            </Panel>

          </div>

          <Panel className="flex min-h-0 flex-col p-5">
            <div className="flex shrink-0 items-center justify-between gap-3">
              <div>
                <div className="construct-kicker">{t('config.workspace')}</div>
                <h3 className="mt-2 text-lg font-semibold">
                  {workspaceTab === 'structured' ? activeSectionItem?.title ?? t('config.workspace.structured_heading') : t('config.workspace.source_heading')}
                </h3>
                {workspaceTab === 'structured' && activeSectionItem ? (
                  <p className="mt-2 max-w-2xl text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>
                    {activeSectionItem.description}
                  </p>
                ) : null}
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <div className="construct-tab-strip" role="tablist" aria-label={t('config.tabs_aria')}>
                  <button
                    type="button"
                    className="construct-tab-button"
                    data-active={String(workspaceTab === 'structured')}
                    aria-selected={workspaceTab === 'structured'}
                    onClick={() => setWorkspaceTab('structured')}
                  >
                    {t('config.tab.structured')}
                  </button>
                  <button
                    type="button"
                    className="construct-tab-button"
                    data-active={String(workspaceTab === 'source')}
                    aria-selected={workspaceTab === 'source'}
                    onClick={() => setWorkspaceTab('source')}
                  >
                    {t('config.tab.source')}
                  </button>
                </div>
                <button className="construct-button" onClick={() => setShowPreview((current) => !current)}>
                  {showPreview ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  {showPreview ? t('config.hide_preview') : t('config.show_preview')}
                </button>
              </div>
            </div>

            {workspaceTab === 'structured' ? (
              parsedConfig ? (
                <div className="mt-5 min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
                  {renderConfigSection(activeSection, parsedConfig, updateField, mcpConfig, mcpErrors, applyMcpConfig, discoveredSections, t, tpl)}

                  {activeSectionItem?.paths?.length ? (
                    <Panel className="p-4" variant="secondary">
                      <div className="construct-kicker">{t('config.mapped_paths')}</div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {activeSectionItem.paths.map((path) => (
                          <span key={path} className="rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ borderColor: 'var(--construct-border-soft)', color: 'var(--construct-text-secondary)' }}>
                            {path}
                          </span>
                        ))}
                      </div>
                    </Panel>
                  ) : null}
                </div>
              ) : (
                <div className="mt-5">
                  <StateMessage tone="error" title={t('config.structured.error_title')} description={t('config.structured.error_desc')} />
                </div>
              )
            ) : (
              <div className={`mt-5 grid min-h-0 flex-1 gap-4 ${showPreview ? 'xl:grid-cols-2' : 'grid-cols-1'}`}>
                <textarea
                  value={rawConfig}
                  onChange={(event) => setRawConfig(event.target.value)}
                  className="construct-input h-full min-h-0 w-full resize-none"
                  style={{ fontFamily: 'var(--pc-font-mono)', lineHeight: 1.65 }}
                  spellCheck={false}
                />

                {showPreview ? (
                  <div className="min-h-0 overflow-auto rounded-[16px] border p-4" style={{ borderColor: 'var(--construct-border-soft)', background: '#09110d' }}>
                    <pre
                      className="whitespace-pre-wrap break-words text-xs leading-7"
                      style={{ color: 'var(--construct-text-secondary)', fontFamily: 'var(--pc-font-mono)' }}
                      dangerouslySetInnerHTML={{ __html: highlightedToml }}
                    />
                  </div>
                ) : null}
              </div>
            )}
          </Panel>

          <div className="flex flex-col gap-4 lg:min-h-0 lg:overflow-hidden">
            <Panel className="flex min-h-0 flex-1 flex-col p-4" variant="secondary">
              <div className="inline-flex shrink-0 items-center gap-2">
                <Settings className="h-4 w-4" style={{ color: 'var(--construct-signal-network)' }} />
                <div className="construct-kicker">{t('config.summary.title')}</div>
              </div>
              {parsedConfig ? (
                <div className="mt-4 min-h-0 flex-1 space-y-4 overflow-y-auto pr-1 text-sm">
                  <ConfigGroup title={t('config.section.provider')}>
                    <ConfigRow label={t('config.summary.api_url')} value={parsedConfig.api_url} />
                    <ConfigRow label={t('config.summary.provider')} value={parsedConfig.default_provider} />
                    <ConfigRow label={t('config.summary.model')} value={parsedConfig.default_model} />
                    <ConfigRow label={t('config.summary.temperature')} value={parsedConfig.default_temperature} />
                    <ConfigRow label={t('config.summary.timeout')} value={parsedConfig.provider_timeout_secs} />
                    <ConfigRow label={t('config.summary.max_tokens')} value={parsedConfig.provider_max_tokens} />
                  </ConfigGroup>

                  <ConfigGroup title={t('config.summary.runtime')}>
                    <ConfigRow label={t('config.summary.gateway')} value={parsedConfig.gateway?.host && parsedConfig.gateway?.port ? `${parsedConfig.gateway.host}:${parsedConfig.gateway.port}` : undefined} />
                    <ConfigRow label={t('config.summary.pairing_required')} value={parsedConfig.gateway?.require_pairing} />
                    <ConfigRow label={t('config.summary.session_persistence')} value={parsedConfig.gateway?.session_persistence} />
                    <ConfigRow label={t('config.summary.autonomy_level')} value={parsedConfig.autonomy?.level} />
                    <ConfigRow label={t('config.summary.workspace_only')} value={parsedConfig.autonomy?.workspace_only} />
                    <ConfigRow label={t('config.summary.max_actions_hour')} value={parsedConfig.autonomy?.max_actions_per_hour} />
                  </ConfigGroup>

                  <ConfigGroup title={t('config.summary.memory')}>
                    <ConfigRow label={t('config.summary.backend')} value={parsedConfig.memory?.backend} />
                    <ConfigRow label={t('config.summary.auto_save')} value={parsedConfig.memory?.auto_save} />
                    <ConfigRow label={t('config.summary.hygiene')} value={parsedConfig.memory?.hygiene_enabled} />
                    <ConfigRow label={t('config.summary.snapshots')} value={parsedConfig.memory?.snapshot_enabled} />
                    <ConfigRow label={t('config.summary.kumiho_enabled')} value={parsedConfig.kumiho?.enabled} />
                    <ConfigRow label={t('config.summary.memory_project')} value={parsedConfig.kumiho?.memory_project} />
                    <ConfigRow label={t('config.summary.retrieval_limit')} value={parsedConfig.kumiho?.memory_retrieval_limit ?? 3} />
                  </ConfigGroup>

                  <ConfigGroup title={t('config.summary.safety_cost')}>
                    <ConfigRow label={t('config.summary.audit_enabled')} value={parsedConfig.security?.audit?.enabled} />
                    <ConfigRow label={t('config.summary.sandbox_backend')} value={parsedConfig.security?.sandbox?.backend} />
                    <ConfigRow label={t('config.summary.daily_limit')} value={parsedConfig.cost?.daily_limit_usd} />
                    <ConfigRow label={t('config.summary.monthly_limit')} value={parsedConfig.cost?.monthly_limit_usd} />
                    <ConfigRow label={t('config.summary.warn_percent')} value={parsedConfig.cost?.warn_at_percent} />
                  </ConfigGroup>

                  <ConfigGroup title={t('config.summary.channels')}>
                    <ConfigRow label={t('config.summary.cli')} value={parsedConfig.channels_config?.cli} />
                    <ConfigRow label={t('config.summary.telegram')} value={!!parsedConfig.channels_config?.telegram} />
                    <ConfigRow label={t('config.summary.discord')} value={!!parsedConfig.channels_config?.discord} />
                    <ConfigRow label={t('config.summary.slack')} value={!!parsedConfig.channels_config?.slack} />
                    <ConfigRow label={t('config.summary.matrix')} value={!!parsedConfig.channels_config?.matrix} />
                    <ConfigRow label={t('config.summary.signal')} value={!!parsedConfig.channels_config?.signal} />
                    <ConfigRow label={t('config.summary.whatsapp')} value={!!parsedConfig.channels_config?.whatsapp} />
                  </ConfigGroup>
                </div>
              ) : (
                <div className="mt-4">
                  <StateMessage tone="error" compact title={t('config.summary.error_title')} description={t('config.summary.error_desc')} />
                </div>
              )}
            </Panel>

            <Panel className="flex max-h-[22rem] shrink-0 flex-col p-4" variant="utility">
              <div className="construct-kicker shrink-0">{t('config.source_health')}</div>
              <div className="mt-3 min-h-0 flex-1 space-y-3 overflow-y-auto pr-1 text-sm">
                <ConfigRow label={t('config.row.workspace_mode')} value={workspaceTab === 'structured' ? t('config.mode.structured') : t('config.mode.source')} />
                <ConfigRow label={t('config.row.preview')} value={showPreview} />
                <ConfigRow label={t('config.row.sections')} value={sectionItems.length} />
                <ConfigRow label={t('config.row.parse_status')} value={parsedConfig ? t('config.row.parse_valid') : t('config.row.parse_needs')} />
              </div>
            </Panel>
          </div>
        </div>
      )}
    </div>
  );
}

function renderConfigSection(
  activeSection: string,
  parsedConfig: ParsedConfig,
  updateField: (sectionPath: string | null, key: string, value: string | number | boolean) => void,
  mcpConfig: McpConfig,
  mcpErrors: Record<number, McpServerErrors>,
  applyMcpConfig: (next: McpConfig) => void,
  discoveredSections: DiscoveredSection[],
  t: TFn,
  tpl: TplFn,
) {
  if (activeSection.startsWith('ext:')) {
    const match = discoveredSections.find((section) => section.id === activeSection);
    if (match) {
      return (
        <GenericSection
          path={match.path}
          data={match.data}
          updateField={updateField}
          t={t}
        />
      );
    }
    return null;
  }
  switch (activeSection as ConfigSectionKey) {
    case 'provider':
      return (
        <ConfigSectionCard title={t('config.section.provider')}>
          <EditableField label={t('config.provider.default_provider')}>
            <input className="construct-input" value={parsedConfig.default_provider ?? ''} onChange={(event) => updateField(null, 'default_provider', event.target.value)} />
          </EditableField>
          <EditableField label={t('config.provider.default_model')}>
            <input className="construct-input" value={parsedConfig.default_model ?? ''} onChange={(event) => updateField(null, 'default_model', event.target.value)} />
          </EditableField>
          <EditableField label={t('config.provider.temperature')}>
            <input className="construct-input" type="number" step="0.1" value={parsedConfig.default_temperature ?? 0.7} onChange={(event) => updateField(null, 'default_temperature', Number(event.target.value))} />
          </EditableField>
          <EditableField label={t('config.provider.timeout')}>
            <input className="construct-input" type="number" value={parsedConfig.provider_timeout_secs ?? 120} onChange={(event) => updateField(null, 'provider_timeout_secs', Number(event.target.value))} />
          </EditableField>
          <EditableField label={t('config.provider.max_tokens')}>
            <input className="construct-input" type="number" value={parsedConfig.provider_max_tokens ?? 8192} onChange={(event) => updateField(null, 'provider_max_tokens', Number(event.target.value))} />
          </EditableField>
        </ConfigSectionCard>
      );
    case 'agent':
      return (
        <ConfigSectionCard title={t('config.section.agent')}>
          <EditableField label={t('config.agent.max_tool_iterations')}>
            <input className="construct-input" type="number" value={parsedConfig.agent?.max_tool_iterations ?? 10} onChange={(event) => updateField('agent', 'max_tool_iterations', Number(event.target.value))} />
          </EditableField>
          <EditableField label={t('config.agent.max_context_tokens')}>
            <input className="construct-input" type="number" value={parsedConfig.agent?.max_context_tokens ?? 32000} onChange={(event) => updateField('agent', 'max_context_tokens', Number(event.target.value))} />
          </EditableField>
          <EditableField label={t('config.agent.max_history_messages')}>
            <input className="construct-input" type="number" value={parsedConfig.agent?.max_history_messages ?? 40} onChange={(event) => updateField('agent', 'max_history_messages', Number(event.target.value))} />
          </EditableField>
          <EditableToggle label={t('config.agent.parallel_tools')} checked={parsedConfig.agent?.parallel_tools ?? false} onChange={(checked) => updateField('agent', 'parallel_tools', checked)} t={t} />
          <EditableField label={t('config.agent.thinking_level')}>
            <input className="construct-input" value={parsedConfig.agent?.thinking?.default_level ?? 'medium'} onChange={(event) => updateField('agent.thinking', 'default_level', event.target.value)} />
          </EditableField>
        </ConfigSectionCard>
      );
    case 'memory':
      return (
        <ConfigSectionCard title={t('config.section.memory')}>
          <EditableToggle label={t('config.memory.kumiho_enabled')} checked={parsedConfig.kumiho?.enabled ?? false} onChange={(checked) => updateField('kumiho', 'enabled', checked)} t={t} />
          <EditableField label={t('config.memory.memory_project')}>
            <input className="construct-input" value={parsedConfig.kumiho?.memory_project ?? ''} onChange={(event) => updateField('kumiho', 'memory_project', event.target.value)} />
          </EditableField>
          <EditableField label={t('config.memory.harness_project')}>
            <input className="construct-input" value={parsedConfig.kumiho?.harness_project ?? ''} onChange={(event) => updateField('kumiho', 'harness_project', event.target.value)} />
          </EditableField>
          <EditableField label={t('config.memory.retrieval_limit')}>
            <input className="construct-input" type="number" min={1} max={50} value={parsedConfig.kumiho?.memory_retrieval_limit ?? 3} onChange={(event) => updateField('kumiho', 'memory_retrieval_limit', Number(event.target.value))} />
          </EditableField>
          <EditableField label={t('config.memory.backend')}>
            <input className="construct-input" value={parsedConfig.memory?.backend ?? ''} onChange={(event) => updateField('memory', 'backend', event.target.value)} />
          </EditableField>
          <EditableToggle label={t('config.memory.auto_save')} checked={parsedConfig.memory?.auto_save ?? false} onChange={(checked) => updateField('memory', 'auto_save', checked)} t={t} />
          <EditableToggle label={t('config.memory.snapshots')} checked={parsedConfig.memory?.snapshot_enabled ?? false} onChange={(checked) => updateField('memory', 'snapshot_enabled', checked)} t={t} />
        </ConfigSectionCard>
      );
    case 'runtime':
      return (
        <ConfigSectionCard title={t('config.section.runtime')}>
          <EditableField label={t('config.runtime.gateway_host')}>
            <input className="construct-input" value={parsedConfig.gateway?.host ?? ''} onChange={(event) => updateField('gateway', 'host', event.target.value)} />
          </EditableField>
          <EditableField label={t('config.runtime.gateway_port')}>
            <input className="construct-input" type="number" value={parsedConfig.gateway?.port ?? 42617} onChange={(event) => updateField('gateway', 'port', Number(event.target.value))} />
          </EditableField>
          <EditableToggle label={t('config.runtime.require_pairing')} checked={parsedConfig.gateway?.require_pairing ?? false} onChange={(checked) => updateField('gateway', 'require_pairing', checked)} t={t} />
          <EditableToggle label={t('config.runtime.session_persistence')} checked={parsedConfig.gateway?.session_persistence ?? false} onChange={(checked) => updateField('gateway', 'session_persistence', checked)} t={t} />
          <EditableField label={t('config.runtime.autonomy_level')}>
            <input className="construct-input" value={parsedConfig.autonomy?.level ?? ''} onChange={(event) => updateField('autonomy', 'level', event.target.value)} />
          </EditableField>
          <EditableField label={t('config.runtime.max_actions_hour')}>
            <input className="construct-input" type="number" value={parsedConfig.autonomy?.max_actions_per_hour ?? 0} onChange={(event) => updateField('autonomy', 'max_actions_per_hour', Number(event.target.value))} />
          </EditableField>
        </ConfigSectionCard>
      );
    case 'security':
      return (
        <ConfigSectionCard title={t('config.section.security')}>
          <EditableToggle label={t('config.security.audit_enabled')} checked={parsedConfig.security?.audit?.enabled ?? false} onChange={(checked) => updateField('security.audit', 'enabled', checked)} t={t} />
          <EditableField label={t('config.security.sandbox_backend')}>
            <input className="construct-input" value={parsedConfig.security?.sandbox?.backend ?? ''} onChange={(event) => updateField('security.sandbox', 'backend', event.target.value)} />
          </EditableField>
          <EditableField label={t('config.security.daily_limit')}>
            <input className="construct-input" type="number" step="0.01" value={parsedConfig.cost?.daily_limit_usd ?? 0} onChange={(event) => updateField('cost', 'daily_limit_usd', Number(event.target.value))} />
          </EditableField>
          <EditableField label={t('config.security.monthly_limit')}>
            <input className="construct-input" type="number" step="0.01" value={parsedConfig.cost?.monthly_limit_usd ?? 0} onChange={(event) => updateField('cost', 'monthly_limit_usd', Number(event.target.value))} />
          </EditableField>
          <EditableField label={t('config.security.warn_percent')}>
            <input className="construct-input" type="number" value={parsedConfig.cost?.warn_at_percent ?? 80} onChange={(event) => updateField('cost', 'warn_at_percent', Number(event.target.value))} />
          </EditableField>
        </ConfigSectionCard>
      );
    case 'channels':
      return (
        <ConfigSectionCard title={t('config.section.channels')}>
          <EditableToggle label={t('config.channels.cli')} checked={parsedConfig.channels_config?.cli ?? false} onChange={(checked) => updateField('channels_config', 'cli', checked)} t={t} />
          <EditableToggle label={t('config.channels.ack_reactions')} checked={parsedConfig.channels_config?.ack_reactions ?? false} onChange={(checked) => updateField('channels_config', 'ack_reactions', checked)} t={t} />
          <EditableToggle label={t('config.channels.show_tool_calls')} checked={parsedConfig.channels_config?.show_tool_calls ?? false} onChange={(checked) => updateField('channels_config', 'show_tool_calls', checked)} t={t} />
          <EditableField label={t('config.channels.message_timeout')}>
            <input className="construct-input" type="number" value={parsedConfig.channels_config?.message_timeout_secs ?? 60} onChange={(event) => updateField('channels_config', 'message_timeout_secs', Number(event.target.value))} />
          </EditableField>
        </ConfigSectionCard>
      );
    case 'mcp':
      return <McpSection config={mcpConfig} errors={mcpErrors} onChange={applyMcpConfig} t={t} tpl={tpl} />;
    default:
      return null;
  }
}

function ConfigSectionCard({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <Panel className="p-4" variant="utility">
      <div className="construct-kicker">{title}</div>
      <div className="mt-4 space-y-4">{children}</div>
    </Panel>
  );
}

function EditableField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <label className="grid gap-2 text-sm">
      <span style={{ color: 'var(--construct-text-secondary)' }}>{label}</span>
      {children}
    </label>
  );
}

function EditableToggle({
  label,
  checked,
  onChange,
  t,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  t: TFn;
}) {
  return (
    <button
      type="button"
      className="flex items-center justify-between rounded-[12px] border px-3 py-3 text-left"
      style={{ borderColor: 'var(--construct-border-soft)', background: 'var(--construct-bg-surface)' }}
      onClick={() => onChange(!checked)}
    >
      <span className="text-sm" style={{ color: 'var(--construct-text-primary)' }}>{label}</span>
      <span
        className="rounded-full px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]"
        style={{
          background: checked ? 'var(--construct-signal-live-soft)' : 'rgba(100,116,139,0.14)',
          color: checked ? 'var(--construct-status-success)' : 'var(--construct-text-faint)',
        }}
      >
        {checked ? t('config.toggle.enabled') : t('config.toggle.disabled')}
      </span>
    </button>
  );
}

function ConfigGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div>
      <div className="construct-kicker">{title}</div>
      <div className="mt-2 space-y-2">{children}</div>
    </div>
  );
}

function ConfigRow({
  label,
  value,
}: {
  label: string;
  value: string | number | boolean | undefined;
}) {
  const { t } = useT();
  return (
    <div className="rounded-[12px] border p-3" style={{ borderColor: 'var(--construct-border-soft)' }}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>
        {label}
      </div>
      <div className="mt-1 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
        {value === undefined ? '—' : typeof value === 'boolean' ? (value ? t('config.row.enabled') : t('config.row.disabled')) : String(value)}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// MCP section — structured editor for [mcp] + [[mcp.servers]]
// ---------------------------------------------------------------------------

function McpSection({
  config,
  errors,
  onChange,
  t,
  tpl,
}: {
  config: McpConfig;
  errors: Record<number, McpServerErrors>;
  onChange: (next: McpConfig) => void;
  t: TFn;
  tpl: TplFn;
}) {
  const setEnabled = (value: boolean) => onChange({ ...config, enabled: value });
  const setDeferred = (value: boolean) => onChange({ ...config, deferred_loading: value });

  const updateServer = (idx: number, patch: Partial<McpServerEntry>) => {
    const next = config.servers.slice();
    next[idx] = { ...next[idx]!, ...patch } as McpServerEntry;
    onChange({ ...config, servers: next });
  };

  const deleteServer = (idx: number) => {
    const server = config.servers[idx];
    const label = server?.name?.trim() ? server.name : tpl('config.mcp.entry_label', { index: idx + 1 });
    const ok = typeof window !== 'undefined' ? window.confirm(tpl('config.mcp.confirm_remove', { label })) : true;
    if (!ok) return;
    const next = config.servers.filter((_, i) => i !== idx);
    onChange({ ...config, servers: next });
  };

  const addServer = () => {
    const next: McpServerEntry[] = [
      ...config.servers,
      { name: '', transport: 'stdio', command: '', args: [] },
    ];
    onChange({ ...config, servers: next });
  };

  const defaultExpanded = config.servers.length <= 3;

  const serverCountMessage = config.servers.length === 0
    ? t('config.mcp.no_servers')
    : tpl(config.servers.length === 1 ? 'config.mcp.server_count_one' : 'config.mcp.server_count', { count: config.servers.length });

  return (
    <div className="space-y-4">
      <ConfigSectionCard title={t('config.mcp.title')}>
        <EditableToggle
          label={t('config.mcp.enabled')}
          checked={config.enabled}
          onChange={setEnabled}
          t={t}
        />
        <EditableToggle
          label={t('config.mcp.deferred')}
          checked={config.deferred_loading}
          onChange={setDeferred}
          t={t}
        />
      </ConfigSectionCard>

      <Panel className="p-4" variant="utility">
        <div className="flex items-center justify-between">
          <div>
            <div className="construct-kicker">{t('config.mcp.servers')}</div>
            <p className="mt-1 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
              {serverCountMessage}
            </p>
          </div>
          <button type="button" className="construct-button" data-variant="primary" onClick={addServer}>
            <Plus className="h-4 w-4" />
            {t('config.mcp.add_server')}
          </button>
        </div>

        <div className="mt-4 space-y-3">
          {config.servers.map((server, idx) => (
            <McpServerCard
              key={`mcp-server-${idx}`}
              index={idx}
              server={server}
              errors={errors[idx]}
              defaultExpanded={defaultExpanded}
              onUpdate={(patch) => updateServer(idx, patch)}
              onDelete={() => deleteServer(idx)}
              t={t}
              tpl={tpl}
            />
          ))}
        </div>
      </Panel>
    </div>
  );
}

type TestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'done'; result: McpServerTestResult };

function McpServerCard({
  index,
  server,
  errors,
  defaultExpanded,
  onUpdate,
  onDelete,
  t,
  tpl,
}: {
  index: number;
  server: McpServerEntry;
  errors?: McpServerErrors;
  defaultExpanded: boolean;
  onUpdate: (patch: Partial<McpServerEntry>) => void;
  onDelete: () => void;
  t: TFn;
  tpl: TplFn;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [testState, setTestState] = useState<TestState>({ kind: 'idle' });
  const hasError = !!(errors?.name || errors?.command || errors?.url);
  const title = server.name.trim() || tpl('config.mcp.server_default', { index: index + 1 });
  const subtitle =
    server.transport === 'stdio'
      ? (server.command?.trim() ? server.command : t('config.mcp.stdio_empty'))
      : (server.url?.trim() ? server.url : tpl('config.mcp.transport_empty', { transport: server.transport }));

  // Clicking Test posts the current draft (not saved disk state) so the user
  // can iterate on a config before committing it to construct.toml.
  const runTest = async () => {
    setTestState({ kind: 'testing' });
    try {
      const result = await testMcpServer({
        name: server.name,
        transport: server.transport,
        command: server.command,
        args: server.args,
        env: server.env,
        url: server.url,
        headers: server.headers,
        timeout_ms: server.timeout_ms,
      });
      setTestState({ kind: 'done', result });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      setTestState({ kind: 'done', result: { ok: false, latency_ms: 0, error: msg } });
    }
  };

  const testDisabled = hasError || testState.kind === 'testing';

  return (
    <div
      className="rounded-[14px] border"
      style={{
        borderColor: hasError ? 'var(--construct-status-error, #f87171)' : 'var(--construct-border-soft)',
        background: 'var(--construct-bg-surface)',
      }}
    >
      <div className="flex items-center justify-between px-4 py-3">
        <button
          type="button"
          className="flex flex-1 items-center gap-3 text-left"
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" style={{ color: 'var(--construct-text-secondary)' }} />
          ) : (
            <ChevronRight className="h-4 w-4" style={{ color: 'var(--construct-text-secondary)' }} />
          )}
          <div className="min-w-0">
            <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{title}</div>
            <div className="truncate text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
              {server.transport.toUpperCase()} · {subtitle}
            </div>
          </div>
        </button>
        <div className="flex items-center gap-2">
          <TestResultBadge state={testState} t={t} tpl={tpl} />
          <button
            type="button"
            className="construct-button"
            onClick={(e) => { e.stopPropagation(); void runTest(); }}
            disabled={testDisabled}
            title={hasError ? t('config.mcp.test_blocked') : t('config.mcp.test_tooltip')}
            style={testDisabled ? { opacity: 0.5, cursor: 'not-allowed' } : undefined}
          >
            {testState.kind === 'testing' ? t('config.mcp.testing') : t('config.mcp.test')}
          </button>
          <button
            type="button"
            className="construct-button"
            onClick={(e) => { e.stopPropagation(); onDelete(); }}
            aria-label={tpl('config.mcp.delete_label', { label: title })}
          >
            <Trash2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {expanded ? (
        <div className="space-y-4 border-t px-4 py-4" style={{ borderColor: 'var(--construct-border-soft)' }}>
          <EditableField label={t('config.mcp.field_name')}>
            <input
              className="construct-input"
              value={server.name}
              placeholder={t('config.mcp.field_name_placeholder')}
              onChange={(e) => onUpdate({ name: e.target.value })}
            />
            {errors?.name ? <FieldError message={errors.name} /> : null}
          </EditableField>

          <EditableField label={t('config.mcp.field_transport')}>
            <select
              className="construct-input"
              value={server.transport}
              onChange={(e) => onUpdate({ transport: e.target.value as McpTransport })}
            >
              <option value="stdio">stdio</option>
              <option value="http">http</option>
              <option value="sse">sse</option>
            </select>
          </EditableField>

          {server.transport === 'stdio' ? (
            <>
              <EditableField label={t('config.mcp.field_command')}>
                <input
                  className="construct-input"
                  value={server.command ?? ''}
                  placeholder={t('config.mcp.field_command_placeholder')}
                  onChange={(e) => onUpdate({ command: e.target.value })}
                />
                {errors?.command ? <FieldError message={errors.command} /> : null}
              </EditableField>
              <StringArrayEditor
                label={t('config.mcp.field_args')}
                values={server.args ?? []}
                placeholder={t('config.mcp.field_args_placeholder')}
                onChange={(next) => onUpdate({ args: next })}
                tpl={tpl}
              />
              <KeyValueEditor
                label={t('config.mcp.field_env')}
                entries={server.env ?? {}}
                keyPlaceholder={t('config.mcp.field_env_key')}
                valuePlaceholder={t('config.mcp.field_env_value')}
                onChange={(next) => onUpdate({ env: next })}
                tpl={tpl}
              />
            </>
          ) : (
            <>
              <EditableField label={t('config.mcp.field_url')}>
                <input
                  className="construct-input"
                  value={server.url ?? ''}
                  placeholder={t('config.mcp.field_url_placeholder')}
                  onChange={(e) => onUpdate({ url: e.target.value })}
                />
                {errors?.url ? <FieldError message={errors.url} /> : null}
              </EditableField>
              <KeyValueEditor
                label={t('config.mcp.field_headers')}
                entries={server.headers ?? {}}
                keyPlaceholder={t('config.mcp.field_headers_key')}
                valuePlaceholder={t('config.mcp.field_headers_value')}
                onChange={(next) => onUpdate({ headers: next })}
                tpl={tpl}
              />
              <EditableField label={t('config.mcp.field_timeout')}>
                <input
                  className="construct-input"
                  type="number"
                  value={server.timeout_ms ?? ''}
                  placeholder={t('config.mcp.field_timeout_placeholder')}
                  onChange={(e) => {
                    const raw = e.target.value;
                    if (raw === '') {
                      onUpdate({ timeout_ms: undefined });
                    } else {
                      const n = Number(raw);
                      if (Number.isFinite(n)) onUpdate({ timeout_ms: n });
                    }
                  }}
                />
              </EditableField>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}

function TestResultBadge({ state, t, tpl }: { state: TestState; t: TFn; tpl: TplFn }) {
  if (state.kind === 'idle') return null;
  if (state.kind === 'testing') {
    return (
      <span
        className="inline-flex items-center gap-1 text-xs"
        style={{ color: 'var(--construct-text-secondary)' }}
      >
        <RefreshCw className="h-3.5 w-3.5 animate-spin" />
        {t('config.mcp.connecting')}
      </span>
    );
  }
  const { result } = state;
  if (result.ok) {
    const toolCount = result.tool_count ?? 0;
    return (
      <span
        className="inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs"
        style={{
          borderColor: 'var(--construct-status-ok, #22c55e)',
          color: 'var(--construct-status-ok, #22c55e)',
        }}
        title={
          (result.tools && result.tools.length > 0)
            ? tpl('config.mcp.tools_list', { list: result.tools.join(', ') })
            : undefined
        }
      >
        <Check className="h-3.5 w-3.5" />
        {tpl(toolCount === 1 ? 'config.mcp.tool_count_one' : 'config.mcp.tool_count', { count: toolCount, latency: result.latency_ms })}
      </span>
    );
  }
  return (
    <span
      className="inline-flex max-w-xs items-center gap-1 truncate rounded border px-2 py-0.5 text-xs"
      style={{
        borderColor: 'var(--construct-status-error, #f87171)',
        color: 'var(--construct-status-error, #f87171)',
      }}
      title={result.error ?? t('config.mcp.handshake_failed')}
    >
      <XIcon className="h-3.5 w-3.5" />
      {result.error ?? t('config.mcp.handshake_failed')}
    </span>
  );
}

function FieldError({ message }: { message: string }) {
  return (
    <span className="text-xs" style={{ color: 'var(--construct-status-error, #f87171)' }}>
      {message}
    </span>
  );
}

function StringArrayEditor({
  label,
  values,
  placeholder,
  onChange,
  tpl,
}: {
  label: string;
  values: string[];
  placeholder?: string;
  onChange: (next: string[]) => void;
  tpl: TplFn;
}) {
  const update = (idx: number, v: string) => {
    const next = values.slice();
    next[idx] = v;
    onChange(next);
  };
  const remove = (idx: number) => onChange(values.filter((_, i) => i !== idx));
  const add = () => onChange([...values, '']);
  return (
    <div className="grid gap-2 text-sm">
      <span style={{ color: 'var(--construct-text-secondary)' }}>{label}</span>
      <div className="space-y-2">
        {values.map((v, idx) => (
          <div key={`${label}-${idx}`} className="flex items-center gap-2">
            <input
              className="construct-input flex-1"
              value={v}
              placeholder={placeholder}
              onChange={(e) => update(idx, e.target.value)}
            />
            <button
              type="button"
              className="construct-button"
              onClick={() => remove(idx)}
              aria-label={tpl('config.mcp.remove_entry', { label, index: idx + 1 })}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
        <button type="button" className="construct-button" onClick={add}>
          <Plus className="h-4 w-4" />
          {tpl('config.mcp.add_entry', { label })}
        </button>
      </div>
    </div>
  );
}

function KeyValueEditor({
  label,
  entries,
  keyPlaceholder,
  valuePlaceholder,
  onChange,
  tpl,
}: {
  label: string;
  entries: Record<string, string>;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  onChange: (next: Record<string, string>) => void;
  tpl: TplFn;
}) {
  // Represent as ordered pairs for stable editing.
  const pairs = Object.entries(entries);
  const commit = (next: Array<[string, string]>) => {
    const out: Record<string, string> = {};
    for (const [k, v] of next) {
      if (k.length === 0) continue;
      out[k] = v;
    }
    onChange(out);
  };
  const updateKey = (idx: number, key: string) => {
    const next = pairs.slice();
    next[idx] = [key, next[idx]![1]];
    commit(next);
  };
  const updateValue = (idx: number, value: string) => {
    const next = pairs.slice();
    next[idx] = [next[idx]![0], value];
    commit(next);
  };
  const remove = (idx: number) => commit(pairs.filter((_, i) => i !== idx));
  const add = () => {
    // Use a placeholder key to keep the row visible even before the user types.
    const nextKey = `new_key_${pairs.length + 1}`;
    commit([...pairs, [nextKey, '']]);
  };
  return (
    <div className="grid gap-2 text-sm">
      <span style={{ color: 'var(--construct-text-secondary)' }}>{label}</span>
      <div className="space-y-2">
        {pairs.map(([k, v], idx) => (
          <div key={`${label}-${idx}`} className="flex items-center gap-2">
            <input
              className="construct-input flex-1"
              value={k}
              placeholder={keyPlaceholder}
              onChange={(e) => updateKey(idx, e.target.value)}
            />
            <input
              className="construct-input flex-1"
              value={v}
              placeholder={valuePlaceholder}
              onChange={(e) => updateValue(idx, e.target.value)}
            />
            <button
              type="button"
              className="construct-button"
              onClick={() => remove(idx)}
              aria-label={tpl('config.mcp.remove_entry', { label, index: idx + 1 })}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        ))}
        <button type="button" className="construct-button" onClick={add}>
          <Plus className="h-4 w-4" />
          {tpl('config.mcp.add_entry', { label })}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Generic section renderer — fallback for any TOML section that doesn't have
// a hand-crafted editor. Renders scalars as inputs/toggles, nested tables as
// collapsible sub-panels. Arrays are read-only for now since upsertTomlField
// doesn't handle arrays.
// ---------------------------------------------------------------------------

type UpdateFn = (sectionPath: string | null, key: string, value: string | number | boolean) => void;

function GenericSection({
  path,
  data,
  updateField,
  t,
}: {
  path: string;
  data: Record<string, unknown>;
  updateField: UpdateFn;
  t: TFn;
}) {
  return (
    <ConfigSectionCard title={`[${path}]`}>
      <GenericFields pathPrefix={path} data={data} updateField={updateField} t={t} />
    </ConfigSectionCard>
  );
}

function GenericFields({
  pathPrefix,
  data,
  updateField,
  t,
}: {
  pathPrefix: string;
  data: Record<string, unknown>;
  updateField: UpdateFn;
  t: TFn;
}) {
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return (
      <div className="text-xs" style={{ color: 'var(--construct-text-faint)' }}>
        {t('config.generic.empty')}
      </div>
    );
  }
  return (
    <>
      {entries.map(([key, value]) => (
        <GenericField
          key={key}
          pathPrefix={pathPrefix}
          fieldKey={key}
          value={value}
          updateField={updateField}
          t={t}
        />
      ))}
    </>
  );
}

function GenericField({
  pathPrefix,
  fieldKey,
  value,
  updateField,
  t,
}: {
  pathPrefix: string;
  fieldKey: string;
  value: unknown;
  updateField: UpdateFn;
  t: TFn;
}) {
  if (typeof value === 'boolean') {
    return (
      <EditableToggle
        label={fieldKey}
        checked={value}
        onChange={(checked) => updateField(pathPrefix, fieldKey, checked)}
        t={t}
      />
    );
  }
  if (typeof value === 'number') {
    return (
      <EditableField label={fieldKey}>
        <input
          className="construct-input"
          type="number"
          value={value}
          onChange={(event) => {
            const next = Number(event.target.value);
            if (Number.isFinite(next)) updateField(pathPrefix, fieldKey, next);
          }}
        />
      </EditableField>
    );
  }
  if (typeof value === 'string') {
    return (
      <EditableField label={fieldKey}>
        <input
          className="construct-input"
          value={value}
          onChange={(event) => updateField(pathPrefix, fieldKey, event.target.value)}
        />
      </EditableField>
    );
  }
  if (Array.isArray(value)) {
    return (
      <EditableField label={fieldKey}>
        <div
          className="rounded-[10px] border px-3 py-2 font-mono text-xs"
          style={{ borderColor: 'var(--construct-border-soft)', color: 'var(--construct-text-secondary)' }}
        >
          {value.length === 0 ? t('config.generic.empty_array') : JSON.stringify(value)}
        </div>
        <span className="text-[10px]" style={{ color: 'var(--construct-text-faint)' }}>
          {t('config.generic.array_readonly')}
        </span>
      </EditableField>
    );
  }
  if (value !== null && typeof value === 'object') {
    return (
      <Panel className="p-3" variant="utility">
        <div className="construct-kicker">{`${pathPrefix}.${fieldKey}`}</div>
        <div className="mt-3 space-y-3">
          <GenericFields
            pathPrefix={`${pathPrefix}.${fieldKey}`}
            data={value as Record<string, unknown>}
            updateField={updateField}
            t={t}
          />
        </div>
      </Panel>
    );
  }
  return null;
}
