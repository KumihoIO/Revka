import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import {
  Check,
  ChevronDown,
  Copy,
  Search,
  SlidersHorizontal,
  X,
} from 'lucide-react';
import { fetchMemoryGraph, kumihoProxy } from '@/lib/api';
import type { KumihoRevision, MemoryGraphNode, MemoryGraphResponse } from '@/types/api';
import Panel from '../components/ui/Panel';
import StateMessage from '../components/ui/StateMessage';
import { copyToClipboard } from '../lib/clipboard';
import { useT } from '@/revka/hooks/useT';
import { useTheme } from '@/revka/hooks/useTheme';

type ForceNode = MemoryGraphNode & {
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  val: number;
  __highlight?: boolean;
};

type ForceLink = {
  source: string | ForceNode;
  target: string | ForceNode;
  edgeType: string;
};

type ForceGraphData = {
  nodes: ForceNode[];
  links: ForceLink[];
};

type BundleMemberDetail = {
  itemKref: string;
  name: string;
  kind: string;
  summary: string | null;
};

type NodeDetail = {
  name: string;
  kind: string;
  kref: string;
  space: string;
  created_at: string | null;
  title: string | null;
  summary: string | null;
  metadata: Record<string, string>;
  bundleMembers: BundleMemberDetail[] | null;
};

const KIND_COLORS: Record<string, string> = {
  conversation: 'var(--revka-signal-network)',
  decision: 'var(--revka-status-warning)',
  fact: 'var(--revka-signal-selected)',
  preference: 'var(--revka-signal-selected)',
  correction: 'var(--revka-status-danger)',
  skill: 'var(--revka-signal-network)',
  summary: 'var(--revka-signal-selected)',
  bundle: 'var(--revka-signal-live)',
  reflection: 'var(--revka-signal-selected)',
  action: 'var(--revka-status-warning)',
  error: 'var(--revka-status-danger)',
  instruction: 'var(--revka-status-warning)',
  plan: 'var(--revka-signal-selected)',
  implementation: 'var(--revka-signal-live)',
  architecture: 'var(--revka-signal-network)',
  synthesis: 'var(--revka-signal-selected)',
  space: 'var(--revka-status-idle)',
  document: 'var(--revka-signal-network)',
  skilldef: 'var(--revka-signal-selected)',
};

const EDGE_COLORS: Record<string, string> = {
  DEPENDS_ON: 'var(--revka-status-warning)',
  DERIVED_FROM: 'var(--revka-signal-selected)',
  REFERENCED: 'var(--revka-signal-network)',
  CONTAINS: 'var(--revka-signal-live)',
  CREATED_FROM: 'var(--revka-signal-selected)',
  BELONGS_TO: 'var(--revka-signal-network)',
  SUPERSEDES: 'var(--revka-status-danger)',
  MEMBER: 'var(--revka-status-warning)',
  USED_TEMPLATE: 'var(--revka-status-warning)',
};

const DEFAULT_NODE_COLOR = 'var(--revka-text-muted)';
const DEFAULT_EDGE_COLOR = 'var(--revka-text-faint)';

function resolveCssColor(color: string): string {
  if (typeof window === 'undefined') return color;
  const match = color.match(/^var\((--[^,)]+)/);
  if (!match) return color;
  return getComputedStyle(document.documentElement).getPropertyValue(match[1]!).trim() || color;
}

function ensureKrefScheme(id: string): string {
  return id.startsWith('kref://') ? id : `kref://${id}`;
}

function useRelativeTime() {
  const { t, tpl } = useT();
  return useCallback((dateStr: string | null | undefined): string => {
    if (!dateStr) return '';
    const now = Date.now();
    const then = new Date(dateStr).getTime();
    const diff = now - then;
    const seconds = Math.floor(diff / 1000);
    if (seconds < 60) return t('memory.relative.just_now');
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return tpl('memory.relative.minutes', { count: minutes });
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return tpl('memory.relative.hours', { count: hours });
    const days = Math.floor(hours / 24);
    if (days < 7) return tpl('memory.relative.days', { count: days });
    return new Date(dateStr).toLocaleDateString();
  }, [t, tpl]);
}

function KindChips({
  kinds,
  activeKinds,
  onToggle,
}: {
  kinds: string[];
  activeKinds: Set<string>;
  onToggle: (kind: string) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {kinds.map((kind) => {
        const active = activeKinds.size === 0 || activeKinds.has(kind);
        const color = KIND_COLORS[kind] || DEFAULT_NODE_COLOR;
        return (
          <button
            key={kind}
            type="button"
            onClick={() => onToggle(kind)}
            className="rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]"
            style={{
              background: active ? `color-mix(in srgb, ${color} 16%, transparent)` : 'transparent',
              color: active ? color : 'var(--revka-text-faint)',
              borderColor: active ? `color-mix(in srgb, ${color} 30%, transparent)` : 'var(--revka-border-soft)',
              opacity: active ? 1 : 0.55,
            }}
          >
            {kind}
          </button>
        );
      })}
    </div>
  );
}

function NodeDetailRail({
  detail,
  loading,
  onClose,
  onMemberClick,
}: {
  detail: NodeDetail;
  loading: boolean;
  onClose: () => void;
  onMemberClick: (member: BundleMemberDetail) => void;
}) {
  const { t } = useT();
  const relativeTime = useRelativeTime();
  const [copied, setCopied] = useState(false);
  const color = KIND_COLORS[detail.kind] || DEFAULT_NODE_COLOR;

  const handleCopy = async () => {
    const text = detail.kref.startsWith('kref://') ? detail.kref : `kref://${detail.kref}`;
    if (!(await copyToClipboard(text))) return;
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <Panel className="p-4" variant="secondary">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="revka-kicker">{t('memory.selected_memory')}</div>
          <div className="mt-2 flex items-center gap-2">
            <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
            <span className="text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color }}>
              {detail.kind}
            </span>
          </div>
          <h3 className="mt-2 break-words text-lg font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
            {detail.title || detail.name}
          </h3>
        </div>
        <button type="button" className="revka-button" onClick={onClose}>
          <X className="h-4 w-4" />
        </button>
      </div>

      {loading ? (
        <div className="mt-4">
          <StateMessage compact tone="loading" title={t('memory.loading_detail')} />
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {detail.summary ? (
            <p className="text-sm leading-6" style={{ color: 'var(--revka-text-secondary)' }}>
              {detail.summary}
            </p>
          ) : null}

          <div className="flex flex-wrap gap-2 text-xs" style={{ color: 'var(--revka-text-faint)' }}>
            {detail.space ? <span className="rounded-full border px-2 py-1" style={{ borderColor: 'var(--revka-border-soft)' }}>{detail.space}</span> : null}
            {detail.created_at ? <span>{relativeTime(detail.created_at)}</span> : null}
          </div>

          <div className="flex items-center gap-2">
            <code className="flex-1 truncate rounded-[10px] border px-3 py-2 text-[11px]" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}>
              {detail.kref}
            </code>
            <button type="button" className="revka-button" onClick={handleCopy}>
              {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
            </button>
          </div>

          {Object.keys(detail.metadata).length > 0 ? (
            <div>
              <div className="revka-kicker">{t('memory.detail.metadata')}</div>
              <div className="mt-2 space-y-2">
                {Object.entries(detail.metadata)
                  .filter(([key]) => key !== 'title' && key !== 'summary')
                  .map(([key, value]) => (
                    <div key={key} className="rounded-[12px] border p-3 text-xs" style={{ borderColor: 'var(--revka-border-soft)' }}>
                      <div className="font-semibold" style={{ color: 'var(--revka-text-secondary)' }}>{key}</div>
                      <div className="mt-1 break-all" style={{ color: 'var(--revka-text-faint)' }}>
                        {value.length > 200 ? `${value.slice(0, 200)}...` : value}
                      </div>
                    </div>
                  ))}
              </div>
            </div>
          ) : null}

          {detail.bundleMembers && detail.bundleMembers.length > 0 ? (
            <div>
              <div className="revka-kicker">{t('memory.detail.bundle_members')}</div>
              <div className="mt-2 space-y-2">
                {detail.bundleMembers.map((member) => (
                  <button
                    key={member.itemKref}
                    type="button"
                    onClick={() => onMemberClick(member)}
                    className="block w-full rounded-[12px] border p-3 text-left transition-colors"
                    style={{ borderColor: 'var(--revka-border-soft)' }}
                  >
                    <div className="text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>{member.name}</div>
                    <div className="mt-1 text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: KIND_COLORS[member.kind] || DEFAULT_NODE_COLOR }}>
                      {member.kind}
                    </div>
                    {member.summary ? (
                      <div className="mt-2 line-clamp-2 text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
                        {member.summary}
                      </div>
                    ) : null}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </Panel>
  );
}

export default function Memory() {
  const { t, tpl } = useT();
  const { resolvedTheme } = useTheme();
  const [graphResponse, setGraphResponse] = useState<MemoryGraphResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeKinds, setActiveKinds] = useState<Set<string>>(new Set());
  const [selectedSpace, setSelectedSpace] = useState('');
  const [nodeLimit, setNodeLimit] = useState(150);
  const [debouncedLimit, setDebouncedLimit] = useState(150);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchHighlight, setSearchHighlight] = useState<Set<string>>(new Set());
  const containerRef = useRef<HTMLDivElement | null>(null);
  const graphRef = useRef<any>(null);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [nodeDetail, setNodeDetail] = useState<NodeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  const labelPalette = useMemo(() => {
    if (typeof window === 'undefined') {
      return { primary: '#ffffff', muted: 'rgba(255,255,255,0.6)', dim: 'rgba(255,255,255,0.1)' };
    }
    const styles = getComputedStyle(document.documentElement);
    const primary = styles.getPropertyValue('--revka-text-primary').trim() || (resolvedTheme === 'light' ? '#18181b' : '#ffffff');
    const muted = styles.getPropertyValue('--revka-text-secondary').trim() || (resolvedTheme === 'light' ? '#52525b' : 'rgba(255,255,255,0.6)');
    const dim = resolvedTheme === 'light' ? 'rgba(24,24,27,0.15)' : 'rgba(255,255,255,0.1)';
    return { primary, muted, dim };
  }, [resolvedTheme]);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedLimit(nodeLimit), 400);
    return () => clearTimeout(id);
  }, [nodeLimit]);

  const fetchGraph = useCallback(async () => {
    setLoading(true);
    setError(null);
    const maxRetries = 2;
    for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
      try {
        const params: Record<string, string | number> = { limit: debouncedLimit };
        if (activeKinds.size > 0) params.kinds = Array.from(activeKinds).join(',');
        if (selectedSpace) params.space = selectedSpace;
        const data = await fetchMemoryGraph(params);
        setGraphResponse(data);
        setLoading(false);
        return;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        const retryable = /API (408|502|503|504)/.test(message);
        if (retryable && attempt < maxRetries) {
          await new Promise((resolve) => setTimeout(resolve, (attempt + 1) * 2000));
          continue;
        }
        setError(message || t('memory.err.load'));
      }
    }
    setLoading(false);
  }, [activeKinds, debouncedLimit, selectedSpace, t]);

  useEffect(() => {
    fetchGraph();
  }, [fetchGraph]);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const update = () => setDimensions({ width: element.clientWidth, height: element.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!searchQuery.trim() || !graphResponse) {
      setSearchHighlight(new Set());
      return;
    }
    const query = searchQuery.toLowerCase();
    const matches = new Set<string>();
    for (const node of graphResponse.nodes) {
      if (
        node.name.toLowerCase().includes(query) ||
        node.title?.toLowerCase().includes(query) ||
        node.kind.toLowerCase().includes(query) ||
        node.summary?.toLowerCase().includes(query)
      ) {
        matches.add(node.id);
      }
    }
    setSearchHighlight(matches);
  }, [graphResponse, searchQuery]);

  const graphData = useMemo<ForceGraphData>(() => {
    if (!graphResponse) return { nodes: [], links: [] };
    const nodes: ForceNode[] = graphResponse.nodes.map((node) => ({
      ...node,
      val: searchHighlight.has(node.id) ? 3 : 1,
      __highlight: searchHighlight.has(node.id),
    }));
    const nodeIds = new Set(nodes.map((node) => node.id));
    const links: ForceLink[] = graphResponse.edges
      .filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target))
      .map((edge) => ({
        source: edge.source,
        target: edge.target,
        edgeType: edge.edge_type,
      }));
    return { nodes, links };
  }, [graphResponse, searchHighlight]);

  const availableKinds = useMemo(() => {
    if (!graphResponse) return [];
    return Object.keys(graphResponse.stats.kinds).sort();
  }, [graphResponse]);

  const handleKindToggle = useCallback((kind: string) => {
    setActiveKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }, []);

  const fetchNodeDetail = useCallback(async (node: MemoryGraphNode) => {
    setDetailLoading(true);
    const detail: NodeDetail = {
      name: node.name,
      kind: node.kind,
      kref: node.id,
      space: node.space,
      created_at: node.created_at,
      title: node.title,
      summary: node.summary,
      metadata: {},
      bundleMembers: null,
    };
    try {
      const kref = ensureKrefScheme(node.id);
      const revisions = await kumihoProxy<KumihoRevision[]>('/revisions', { item_kref: kref });
      const published = revisions?.find((revision) => revision.tags?.includes('published'));
      const latest = published ?? revisions?.find((revision) => revision.latest) ?? revisions?.[0];
      if (latest) {
        detail.metadata = latest.metadata ?? {};
        detail.title = latest.metadata?.title || detail.title;
        detail.summary = latest.metadata?.summary || detail.summary;
      }

      if (node.kind === 'bundle') {
        try {
          const response = await kumihoProxy<{ members: { item_kref: string }[] }>('/bundles/members', { bundle_kref: kref });
          detail.bundleMembers = (response?.members ?? []).map((member) => {
            const stripped = member.item_kref.replace(/^kref:\/\//, '');
            const parts = stripped.split('/');
            const last = parts.pop() || '';
            const dotIdx = last.indexOf('.');
            const name = dotIdx > 0 ? last.substring(0, dotIdx) : last;
            const kind = dotIdx > 0 ? last.substring(dotIdx + 1) : 'unknown';
            return { itemKref: member.item_kref, name, kind, summary: null };
          });
        } catch {
          detail.bundleMembers = null;
        }
      }
    } catch {
      // Show partial detail if Kumiho lookups fail.
    }
    setNodeDetail(detail);
    setDetailLoading(false);
  }, []);

  const handleNodeClick = useCallback((node: ForceNode) => {
    setSelectedNodeId(node.id);
    fetchNodeDetail(node);
  }, [fetchNodeDetail]);

  const handleMemberClick = useCallback((member: BundleMemberDetail) => {
    const found = graphResponse?.nodes.find((node) => node.id === member.itemKref.replace(/^kref:\/\//, ''));
    if (!found) return;
    setSelectedNodeId(found.id);
    fetchNodeDetail(found);
  }, [fetchNodeDetail, graphResponse]);

  const paintNode = useCallback((node: ForceNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const color = resolveCssColor(KIND_COLORS[node.kind] || DEFAULT_NODE_COLOR);
    const isSelected = node.id === selectedNodeId;
    const isHighlighted = !!node.__highlight;
    const isSpace = node.kind === 'space';
    const radius = isSelected ? 7 : isHighlighted ? 6 : isSpace ? 5 : 4;
    const searchActive = searchHighlight.size > 0;
    const dimmed = searchActive && !isHighlighted && !isSelected;

    if (isSelected) {
      ctx.beginPath();
      ctx.arc(x, y, radius + 5, 0, 2 * Math.PI);
      ctx.fillStyle = `${color}20`;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, radius + 2.5, 0, 2 * Math.PI);
      ctx.strokeStyle = `${color}50`;
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    if (isHighlighted && !isSelected) {
      ctx.beginPath();
      ctx.arc(x, y, radius + 3, 0, 2 * Math.PI);
      ctx.strokeStyle = labelPalette.primary;
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.6;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    ctx.beginPath();
    if (isSpace) {
      const r = radius * 1.2;
      ctx.moveTo(x, y - r);
      ctx.lineTo(x + r, y);
      ctx.lineTo(x, y + r);
      ctx.lineTo(x - r, y);
      ctx.closePath();
    } else {
      ctx.arc(x, y, radius, 0, 2 * Math.PI);
    }
    ctx.fillStyle = color;
    ctx.globalAlpha = dimmed ? 0.15 : 1;
    ctx.fill();
    ctx.globalAlpha = 1;

    if (globalScale > 0.8 || isSelected || isHighlighted) {
      const fontSize = Math.max(10 / globalScale, 3);
      ctx.font = `${isSelected ? 'bold ' : ''}${fontSize}px Inter, system-ui, sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = dimmed ? labelPalette.dim : isSelected ? labelPalette.primary : labelPalette.muted;
      const label = node.name.length > 22 ? `${node.name.slice(0, 20)}...` : node.name;
      ctx.fillText(label, x, y + radius + 2);
    }
  }, [searchHighlight, selectedNodeId, labelPalette]);

  const paintLink = useCallback((link: ForceLink, ctx: CanvasRenderingContext2D) => {
    const source = link.source as ForceNode;
    const target = link.target as ForceNode;
    if (typeof source !== 'object' || typeof target !== 'object') return;
    if (source.x == null || source.y == null || target.x == null || target.y == null) return;
    const color = resolveCssColor(EDGE_COLORS[link.edgeType] || DEFAULT_EDGE_COLOR);
    const searchActive = searchHighlight.size > 0;
    const sourceHighlighted = searchHighlight.has(source.id) || source.id === selectedNodeId;
    const targetHighlighted = searchHighlight.has(target.id) || target.id === selectedNodeId;
    const dimmed = searchActive && !sourceHighlighted && !targetHighlighted;
    ctx.beginPath();
    ctx.moveTo(source.x, source.y);
    ctx.lineTo(target.x, target.y);
    ctx.strokeStyle = color;
    ctx.globalAlpha = dimmed ? 0.05 : 0.4;
    ctx.lineWidth = dimmed ? 0.5 : 1.5;
    ctx.stroke();
    ctx.globalAlpha = 1;
  }, [searchHighlight, selectedNodeId]);

  return (
    <div className="flex h-[calc(100vh-6rem)] flex-col gap-3">
      <div className="grid min-h-0 flex-1 gap-4 md:grid-cols-[16rem_minmax(0,1fr)_minmax(22rem,26rem)]">
        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
          <Panel className="p-4">
            <div className="revka-kicker">{t('memory.controls.title')}</div>
            <div className="mt-3 space-y-4">
              <div>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: 'var(--revka-text-faint)' }} />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(event) => setSearchQuery(event.target.value)}
                    placeholder={t('memory.controls.search_placeholder')}
                    className="revka-input pl-10 pr-10"
                  />
                  {searchQuery ? (
                    <button type="button" className="absolute right-2 top-1/2 -translate-y-1/2 revka-button px-2 py-2" onClick={() => setSearchQuery('')}>
                      <X className="h-3.5 w-3.5" />
                    </button>
                  ) : null}
                </div>
              </div>

              {graphResponse && graphResponse.spaces.length > 1 ? (
                <div className="relative">
                  <select
                    value={selectedSpace}
                    onChange={(event) => setSelectedSpace(event.target.value)}
                    className="revka-input appearance-none pr-10"
                  >
                    <option value="">{t('memory.controls.all_spaces')}</option>
                    {graphResponse.spaces.map((space) => (
                      <option key={space} value={`/${space}`}>
                        {space.split('/').pop()}
                      </option>
                    ))}
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: 'var(--revka-text-faint)' }} />
                </div>
              ) : null}

              <div>
                <div className="mb-2 inline-flex items-center gap-2 text-sm" style={{ color: 'var(--revka-text-secondary)' }}>
                  <SlidersHorizontal className="h-4 w-4" />
                  {t('memory.controls.node_limit')}
                </div>
                <input
                  type="range"
                  min={25}
                  max={500}
                  step={25}
                  value={nodeLimit}
                  onChange={(event) => setNodeLimit(Number(event.target.value))}
                  className="w-full accent-[var(--revka-signal-selected)]"
                />
                <div className="mt-2 text-xs font-mono" style={{ color: 'var(--revka-text-faint)' }}>{nodeLimit}</div>
              </div>
            </div>
          </Panel>

          <Panel className="p-4" variant="secondary">
            <div className="revka-kicker">{t('memory.filters.title')}</div>
            <div className="mt-3">
              {availableKinds.length > 0 ? (
                <KindChips kinds={availableKinds} activeKinds={activeKinds} onToggle={handleKindToggle} />
              ) : (
                <StateMessage compact title={t('memory.filters.empty_title')} description={t('memory.filters.empty_desc')} />
              )}
            </div>
          </Panel>
        </div>

        <Panel className="flex min-h-0 flex-col p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="revka-kicker">{t('memory.graph.kicker')}</div>
              <h3 className="mt-2 text-lg font-semibold">{t('memory.graph.heading')}</h3>
            </div>
            {graphResponse ? (
              <div className="flex flex-wrap items-center gap-3 text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                <span><strong style={{ color: 'var(--revka-text-primary)' }}>{graphResponse.stats.total_items}</strong> {t('memory.graph.items')}</span>
                <span><strong style={{ color: 'var(--revka-text-primary)' }}>{graphResponse.stats.total_edges}</strong> {t('memory.graph.edges')}</span>
                <span><strong style={{ color: 'var(--revka-text-primary)' }}>{graphData.nodes.length}</strong> {t('memory.graph.shown')}</span>
                {searchHighlight.size > 0 ? (
                  <span style={{ color: 'var(--revka-status-warning)' }}>
                    {tpl(searchHighlight.size === 1 ? 'memory.graph.match' : 'memory.graph.matches', { count: searchHighlight.size })}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>

          <div
            ref={containerRef}
            className="relative mt-5 min-h-0 flex-1 overflow-hidden rounded-[18px] border"
            style={{
              borderColor: 'var(--revka-border-soft)',
              background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)',
            }}
          >
            {loading ? (
              <div className="absolute inset-0 flex items-center justify-center p-6">
                <StateMessage tone="loading" title={t('memory.graph.loading_title')} description={t('memory.graph.loading_desc')} />
              </div>
            ) : null}

            {!loading && error ? (
              <div className="absolute inset-0 flex items-center justify-center p-6">
                <StateMessage tone="error" title={t('memory.graph.error_title')} description={error} action={<button className="revka-button" onClick={fetchGraph}>{t('memory.graph.retry')}</button>} />
              </div>
            ) : null}

            {!loading && !error && graphData.nodes.length === 0 ? (
              <div className="absolute inset-0 flex items-center justify-center p-6">
                <StateMessage title={t('memory.graph.empty_title')} description={t('memory.graph.empty_desc')} />
              </div>
            ) : null}

            {!loading && !error && dimensions.width > 0 && dimensions.height > 0 && graphData.nodes.length > 0 ? (
              <ForceGraph2D
                ref={graphRef}
                graphData={graphData}
                width={dimensions.width}
                height={dimensions.height}
                backgroundColor="transparent"
                nodeRelSize={4}
                nodeCanvasObject={paintNode}
                nodePointerAreaPaint={(node: ForceNode, color: string, ctx: CanvasRenderingContext2D) => {
                  const x = node.x ?? 0;
                  const y = node.y ?? 0;
                  ctx.beginPath();
                  ctx.arc(x, y, 10, 0, 2 * Math.PI);
                  ctx.fillStyle = color;
                  ctx.fill();
                }}
                linkCanvasObject={paintLink}
                linkCanvasObjectMode={() => 'replace'}
                onNodeClick={handleNodeClick}
                cooldownTicks={200}
                d3AlphaDecay={0.015}
                d3VelocityDecay={0.25}
                warmupTicks={50}
              />
            ) : null}

            {!loading && !error && graphData.nodes.length > 0 ? (
              <div className="absolute bottom-3 left-3 z-10 flex max-w-[70%] flex-wrap items-center gap-x-3 gap-y-1 rounded-[12px] border px-3 py-2" style={{ borderColor: 'var(--revka-border-soft)', background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)' }}>
                {Object.entries(KIND_COLORS)
                  .filter(([kind]) => graphResponse?.stats.kinds[kind])
                  .map(([kind, color]) => (
                    <span key={kind} className="inline-flex items-center gap-1 text-[10px]" style={{ color: 'var(--revka-text-secondary)' }}>
                      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                      {kind}
                    </span>
                  ))}
              </div>
            ) : null}
          </div>
        </Panel>

        <div className="flex min-h-0 flex-col gap-4 overflow-y-auto pr-1">
          {nodeDetail ? (
            <NodeDetailRail
              detail={nodeDetail}
              loading={detailLoading}
              onClose={() => {
                setNodeDetail(null);
                setSelectedNodeId(null);
              }}
              onMemberClick={handleMemberClick}
            />
          ) : (
            <Panel className="p-5" variant="secondary">
              <StateMessage
                title={t('memory.none_selected_title')}
                description={t('memory.none_selected_desc')}
              />
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}
