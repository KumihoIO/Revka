import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
import { useSearchParams } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Bot,
  BookOpen,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Database,
  Edit3,
  Eye,
  FilePlus2,
  FileText,
  FolderOpen,
  GitBranch,
  Hash,
  Link2,
  Loader2,
  MapPinned,
  MessageSquare,
  Network,
  Package,
  Plus,
  RefreshCcw,
  Save,
  Search,
  Settings,
  Sparkles,
  Tag,
  Workflow,
  X,
} from 'lucide-react';
import type {
  KumihoAssetDependencyGraphResponse,
  KumihoAssetGraphNode,
  KumihoArtifact,
  KumihoBundleMemberDetail,
  KumihoEdge,
  KumihoItem,
  KumihoProject,
  KumihoRevision,
  KumihoSearchResult,
  KumihoSpace,
} from '@/types/api';
import {
  addAssetBundleMember,
  createAssetArtifact,
  createAssetBundle,
  createAssetEdge,
  createAssetItem,
  createAssetProject,
  createAssetRevision,
  createAssetSpace,
  fetchAssetBundleMembers,
  fetchAssetBundles,
  fetchAssetDependencyGraph,
  fetchArtifactBodyText,
  kumihoProxy,
  publishAssetRevision,
  removeAssetBundleMember,
  tagAssetRevision,
  toggleAssetArtifactDeprecation,
  toggleAssetItemDeprecation,
  toggleAssetRevisionDeprecation,
  untagAssetRevision,
  updateAssetArtifactContent,
} from '@/lib/api';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';
import ArtifactViewerModal from '../components/ui/ArtifactViewerModal';
import Modal from '../components/ui/Modal';
import { copyToClipboard } from '../lib/clipboard';
import { useT } from '@/revka/hooks/useT';

/* ------------------------------------------------------------------ */
/*  Kind metadata                                                      */
/* ------------------------------------------------------------------ */

type KindMeta = { icon: typeof Bot; color: string; bg: string; border: string };
type PathSegment = { name: string; path: string };
type AssetTab = 'items' | 'bundles';
type CreateAction =
  | 'project'
  | 'space'
  | 'subspace'
  | 'item'
  | 'bundle'
  | 'revision'
  | 'artifact'
  | 'edge'
  | 'tag'
  | 'bundle-member-add'
  | 'bundle-member-remove'
  | 'context-pack';

type ParsedKref = {
  project: string;
  spacePath: string;
  itemKref: string;
  revisionKref: string | null;
};

type ForceNode = KumihoAssetGraphNode & {
  id: string;
  graphDepth: number;
  graphDegree: number;
  val: number;
  x?: number;
  y?: number;
};

type ForceLink = {
  source: string | ForceNode;
  target: string | ForceNode;
  edge: KumihoEdge;
  curveOffset: number;
};

type LabelBox = {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
};

const KIND_MAP: Record<string, KindMeta> = {
  agent: { icon: Bot, color: '#22d3ee', bg: 'rgba(34, 211, 238, 0.1)', border: 'rgba(34, 211, 238, 0.25)' },
  skill: { icon: Sparkles, color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.1)', border: 'rgba(167, 139, 250, 0.25)' },
  conversation: { icon: MessageSquare, color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.1)', border: 'rgba(96, 165, 250, 0.25)' },
  decision: { icon: GitBranch, color: '#fbbf24', bg: 'rgba(251, 191, 36, 0.1)', border: 'rgba(251, 191, 36, 0.25)' },
  fact: { icon: BookOpen, color: '#34d399', bg: 'rgba(52, 211, 153, 0.1)', border: 'rgba(52, 211, 153, 0.25)' },
  bundle: { icon: Package, color: '#2dd4bf', bg: 'rgba(45, 212, 191, 0.1)', border: 'rgba(45, 212, 191, 0.25)' },
  config: { icon: Settings, color: '#a1a1aa', bg: 'rgba(161, 161, 170, 0.1)', border: 'rgba(161, 161, 170, 0.25)' },
  workflow: { icon: Workflow, color: '#fb923c', bg: 'rgba(251, 146, 60, 0.1)', border: 'rgba(251, 146, 60, 0.25)' },
  character: { icon: Bot, color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.1)', border: 'rgba(56, 189, 248, 0.25)' },
  'character-state': { icon: Bot, color: '#2dd4bf', bg: 'rgba(45, 212, 191, 0.1)', border: 'rgba(45, 212, 191, 0.25)' },
  storyline: { icon: GitBranch, color: '#f59e0b', bg: 'rgba(245, 158, 11, 0.1)', border: 'rgba(245, 158, 11, 0.25)' },
  'foreshadow-thread': { icon: Sparkles, color: '#c084fc', bg: 'rgba(192, 132, 252, 0.1)', border: 'rgba(192, 132, 252, 0.25)' },
  'canon-rule': { icon: Ban, color: '#f87171', bg: 'rgba(248, 113, 113, 0.1)', border: 'rgba(248, 113, 113, 0.25)' },
  'timeline-event': { icon: MapPinned, color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.1)', border: 'rgba(96, 165, 250, 0.25)' },
  'webnovel-episode': { icon: BookOpen, color: '#34d399', bg: 'rgba(52, 211, 153, 0.1)', border: 'rgba(52, 211, 153, 0.25)' },
  'canon-patch': { icon: Edit3, color: '#fb7185', bg: 'rgba(251, 113, 133, 0.1)', border: 'rgba(251, 113, 133, 0.25)' },
  'context-pack': { icon: Package, color: '#818cf8', bg: 'rgba(129, 140, 248, 0.1)', border: 'rgba(129, 140, 248, 0.25)' },
};

const DEFAULT_KIND: KindMeta = {
  icon: FileText,
  color: '#a1a1aa',
  bg: 'rgba(161, 161, 170, 0.1)',
  border: 'rgba(161, 161, 170, 0.25)',
};

const PROTECTED_BUNDLE_SUFFIXES = [
  'main-canon',
  'current-character-states',
  'active-storylines',
  'active-foreshadow',
];

const EDGE_TYPES = [
  'DEPENDS_ON',
  'DERIVED_FROM',
  'REFERENCES',
  'ADVANCES',
  'FORESHADOWS',
  'PAYOFF_TARGET',
  'UPDATES',
  'CONTRADICTS',
  'BLOCKS',
  'RELATED_TO',
  'RESOLVES',
];

const GRAPH_DEPTH_OPTIONS = [1, 2, 3] as const;
const GRAPH_NODE_LIMIT_OPTIONS = [80, 120, 160, 200] as const;

const GRAPH_EDGE_VISUALS: Record<string, { color: string; glow: string; label: string }> = {
  DEPENDS_ON: { color: '#f59e0b', glow: 'rgba(245,158,11,0.18)', label: 'depends' },
  DERIVED_FROM: { color: '#22d3ee', glow: 'rgba(34,211,238,0.18)', label: 'derived' },
  REFERENCES: { color: '#60a5fa', glow: 'rgba(96,165,250,0.18)', label: 'refs' },
  ADVANCES: { color: '#34d399', glow: 'rgba(52,211,153,0.18)', label: 'advances' },
  FORESHADOWS: { color: '#a78bfa', glow: 'rgba(167,139,250,0.18)', label: 'foreshadows' },
  PAYOFF_TARGET: { color: '#f472b6', glow: 'rgba(244,114,182,0.18)', label: 'payoff' },
  UPDATES: { color: '#2dd4bf', glow: 'rgba(45,212,191,0.18)', label: 'updates' },
  CONTRADICTS: { color: '#f87171', glow: 'rgba(248,113,113,0.22)', label: 'contradicts' },
  BLOCKS: { color: '#fb7185', glow: 'rgba(251,113,133,0.22)', label: 'blocks' },
  RELATED_TO: { color: '#a1a1aa', glow: 'rgba(161,161,170,0.14)', label: 'related' },
  RESOLVES: { color: '#84cc16', glow: 'rgba(132,204,22,0.18)', label: 'resolves' },
};

const DEFAULT_GRAPH_EDGE_VISUAL = { color: '#94a3b8', glow: 'rgba(148,163,184,0.14)', label: 'edge' };

const ITEM_KIND_TEMPLATES: Record<string, string> = {
  character: [
    '---',
    'character_id:',
    'display_name:',
    'role:',
    'first_seen_episode:',
    'core_traits:',
    'constraints:',
    '---',
    '',
    '## Canon Summary',
    '',
  ].join('\n'),
  'character-state': [
    '---',
    'character_id:',
    'timeline_position:',
    'current_location:',
    'current_goal:',
    'known_information:',
    'emotional_state:',
    'relationship_state:',
    'open_threads:',
    'last_updated_by_episode:',
    '---',
    '',
    '## Current State',
    '',
  ].join('\n'),
  storyline: [
    '---',
    'storyline_id:',
    'status: active',
    'current_pressure:',
    'next_pressure:',
    'payoff_target:',
    '---',
    '',
    '## Storyline',
    '',
  ].join('\n'),
  'foreshadow-thread': [
    '---',
    'thread_id:',
    'status: active',
    'allowed_use:',
    'forbidden_use:',
    'payoff_target:',
    '---',
    '',
    '## Foreshadow Thread',
    '',
  ].join('\n'),
  'canon-rule': [
    '---',
    'rule_id:',
    'severity: hard',
    'scope:',
    '---',
    '',
    '## Rule',
    '',
  ].join('\n'),
  'timeline-event': [
    '---',
    'event_id:',
    'timeline_position:',
    'episode:',
    'participants:',
    '---',
    '',
    '## Event',
    '',
  ].join('\n'),
  'webnovel-episode': [
    '---',
    'episode_number:',
    'volume:',
    'status: draft',
    'source_context_pack:',
    '---',
    '',
    '## Episode',
    '',
  ].join('\n'),
  'canon-patch': [
    '---',
    'patch_id:',
    'patch_status: candidate',
    'source_episode:',
    'source_context_pack:',
    '---',
    '',
    '## Proposed Revision Updates',
    '',
    '## Proposed Edges',
    '',
  ].join('\n'),
};

function getKindMeta(kind: string): KindMeta {
  return KIND_MAP[kind.toLowerCase()] ?? DEFAULT_KIND;
}

function revisionIsPublished(revision: KumihoRevision): boolean {
  return Boolean(revision.published || revision.tags?.includes('published'));
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return '--';
  try {
    return new Date(dateStr).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
  } catch {
    return dateStr;
  }
}

function formatTime(dateStr?: string | null): string {
  if (!dateStr) return '--';
  try {
    return new Date(dateStr).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return dateStr;
  }
}

function isUuidLike(value?: string | null): boolean {
  return Boolean(value && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value));
}

function readableAuthor(entity: {
  author?: string | null;
  username?: string | null;
  author_display?: string | null;
  metadata?: Record<string, string>;
}): string {
  const candidates = [
    entity.author_display,
    entity.username,
    entity.metadata?.username,
    entity.metadata?.updated_by,
    entity.metadata?.created_by,
    entity.author,
  ];
  return candidates.find((value) => value && !isUuidLike(value)) ?? '--';
}

function parseKref(kref?: string | null): ParsedKref | null {
  const normalized = kref?.startsWith('asset://') ? kref.slice('asset://'.length) : kref;
  if (!normalized?.startsWith('kref://')) return null;
  const selectorIndex = normalized.indexOf('?');
  const itemKref = selectorIndex >= 0 ? normalized.slice(0, selectorIndex) : normalized;
  const selector = selectorIndex >= 0 ? normalized.slice(selectorIndex + 1) : '';
  const selectorParams = new URLSearchParams(selector);
  const revisionKref = selectorParams.has('r') || selectorParams.has('t') ? normalized : null;
  const rest = itemKref.slice('kref://'.length);
  const parts = rest.split('/').filter(Boolean);
  if (parts.length < 2) return null;
  const project = parts[0] ?? '';
  if (!project) return null;
  const spaceParts = parts.slice(1, -1);
  const spacePath = `/${[project, ...spaceParts].join('/')}`;
  return { project, spacePath, itemKref, revisionKref };
}

function pathSegmentsFromSpacePath(spacePath?: string | null): PathSegment[] {
  if (!spacePath) return [];
  const parts = spacePath.split('/').filter(Boolean);
  return parts.map((part, index) => ({
    name: part,
    path: `/${parts.slice(0, index + 1).join('/')}`,
  }));
}

function bundleNameFromKref(kref?: string | null): string {
  if (!kref) return '';
  const base = kref.split('?')[0] ?? kref;
  const leaf = base.split('/').pop() ?? base;
  return leaf.split('.')[0] || leaf;
}

function bundleNameMatchesSuffix(name: string, suffix: string): boolean {
  return name === suffix || name.endsWith(`-${suffix}`);
}

function isProtectedBundleName(name: string): boolean {
  return PROTECTED_BUNDLE_SUFFIXES.some((suffix) => bundleNameMatchesSuffix(name, suffix));
}

function graphNodeLabel(node: Pick<KumihoAssetGraphNode, 'item_name' | 'kref'>): string {
  return node.item_name || bundleNameFromKref(node.kref) || node.kref;
}

function graphEdgeKey(edge: KumihoEdge): string {
  return `${edge.source_kref}\n${edge.edge_type}\n${edge.target_kref}`;
}

function graphEdgePairKey(edge: KumihoEdge): string {
  return `${edge.source_kref}\n${edge.target_kref}`;
}

function graphEdgeVisual(edgeType: string) {
  return GRAPH_EDGE_VISUALS[edgeType] ?? DEFAULT_GRAPH_EDGE_VISUAL;
}

function truncateLabel(label: string, maxLength: number): string {
  if (label.length <= maxLength) return label;
  return `${label.slice(0, Math.max(0, maxLength - 1))}...`;
}

function roundedRect(ctx: CanvasRenderingContext2D, x: number, y: number, width: number, height: number, radius: number) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

function labelBoxOverlaps(box: LabelBox, boxes: LabelBox[]): boolean {
  return boxes.some((other) => (
    box.x1 < other.x2
    && box.x2 > other.x1
    && box.y1 < other.y2
    && box.y2 > other.y1
  ));
}

function itemDisplayName(item?: KumihoItem | null): string {
  return item?.item_name || item?.name || item?.kref.split('/').pop()?.split('.')[0] || 'item';
}

function revisionLabel(revision?: KumihoRevision | null): string {
  if (!revision) return '--';
  return `r${revision.number}`;
}

function parseMetadataText(text: string): Record<string, string> {
  const metadata: Record<string, string> = {};
  const trimmed = text.trim();
  if (!trimmed) return metadata;
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      for (const [key, value] of Object.entries(parsed)) {
        metadata[key] = typeof value === 'string' ? value : JSON.stringify(value);
      }
      return metadata;
    }
  } catch {
    // Fall through to key/value line parsing.
  }
  for (const line of trimmed.split(/\r?\n/)) {
    const cleaned = line.trim();
    if (!cleaned || cleaned.startsWith('#')) continue;
    const [key, ...rest] = cleaned.includes('=')
      ? cleaned.split('=')
      : cleaned.split(':');
    if (!key?.trim()) continue;
    metadata[key.trim()] = rest.join(cleaned.includes('=') ? '=' : ':').trim();
  }
  return metadata;
}

/* ------------------------------------------------------------------ */
/*  Small shared components                                            */
/* ------------------------------------------------------------------ */

function CopyableKref({ kref }: { kref: string }) {
  const { t } = useT();
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        if (!(await copyToClipboard(kref))) return;
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
      className="truncate text-left text-xs font-mono"
      style={{ color: copied ? 'var(--revka-status-success)' : 'var(--revka-text-faint)' }}
      title={t('assets.copy_kref')}
    >
      {kref}
    </button>
  );
}

function TagChip({ label, tone }: { label: string; tone: string }) {
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase"
      style={{ color: tone, border: `1px solid color-mix(in srgb, ${tone} 20%, transparent)` }}
    >
      <Tag className="h-2.5 w-2.5" />
      {label}
    </span>
  );
}

const METADATA_PRIORITY = [
  'source_heading',
  'title',
  'summary',
  'role_summary',
  'status',
  'timeline_position',
  'current_location',
  'current_goal',
  'known_information',
  'open_threads',
  'embedding_text',
  '_search_text',
  'updated_by',
  'created_by',
  'tenant_id',
];

function sortedMetadataEntries(metadata?: Record<string, string>): [string, string][] {
  const entries = Object.entries(metadata ?? {});
  return entries.sort(([a], [b]) => {
    const aIndex = METADATA_PRIORITY.indexOf(a);
    const bIndex = METADATA_PRIORITY.indexOf(b);
    if (aIndex !== -1 || bIndex !== -1) {
      return (aIndex === -1 ? Number.MAX_SAFE_INTEGER : aIndex) - (bIndex === -1 ? Number.MAX_SAFE_INTEGER : bIndex);
    }
    return a.localeCompare(b);
  });
}

function metadataKeyLabel(key: string): string {
  return key.replace(/^_+/, '').replace(/_/g, ' ');
}

function metadataPreview(value: string): string {
  return value.length > 220 ? `${value.slice(0, 217)}...` : value;
}

function DetailStat({ label, value, tone }: { label: string; value: string | number; tone?: string }) {
  return (
    <div className="rounded-[8px] border px-3 py-2" style={{ borderColor: 'var(--revka-border-soft)', background: 'rgba(255,255,255,0.025)' }}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: 'var(--revka-text-faint)' }}>{label}</div>
      <div className="mt-1 truncate text-sm font-semibold" style={{ color: tone ?? 'var(--revka-text-primary)' }}>{value}</div>
    </div>
  );
}

function MetadataSection({ metadata, maxVisible = 8 }: { metadata?: Record<string, string>; maxVisible?: number }) {
  const entries = sortedMetadataEntries(metadata);
  const visible = entries.slice(0, maxVisible);
  const hidden = Math.max(0, entries.length - visible.length);

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>Metadata</div>
        <span className="rounded-full border px-2 py-0.5 text-[10px] font-semibold" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-muted)' }}>
          {entries.length} keys
        </span>
      </div>
      {visible.length > 0 ? (
        <div className="space-y-2">
          {visible.map(([key, value]) => (
            <div key={key} className="rounded-[8px] border p-2.5" style={{ borderColor: 'var(--revka-border-soft)', background: 'rgba(255,255,255,0.025)' }}>
              <div className="mb-1 truncate text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: 'var(--revka-signal-selected)' }}>
                {metadataKeyLabel(key)}
              </div>
              <div className="break-words text-xs leading-5" style={{ color: 'var(--revka-text-secondary)' }}>
                {metadataPreview(value)}
              </div>
            </div>
          ))}
          {hidden > 0 ? (
            <div className="text-[11px]" style={{ color: 'var(--revka-text-faint)' }}>
              +{hidden} more metadata keys in raw view
            </div>
          ) : null}
        </div>
      ) : (
        <div className="rounded-[8px] border px-3 py-4 text-xs" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}>
          No metadata on this node.
        </div>
      )}
      {entries.length > 0 ? (
        <details className="rounded-[8px] border" style={{ borderColor: 'var(--revka-border-soft)' }}>
          <summary className="cursor-pointer px-3 py-2 text-xs font-semibold" style={{ color: 'var(--revka-text-muted)' }}>Raw JSON</summary>
          <pre className="max-h-56 overflow-auto border-t p-3 text-[11px] leading-5" style={{ borderColor: 'var(--revka-border-soft)', background: 'var(--revka-bg-elevated)', color: 'var(--revka-text-secondary)' }}>
            {JSON.stringify(metadata ?? {}, null, 2)}
          </pre>
        </details>
      ) : null}
    </section>
  );
}

function ArtifactStrip({ artifacts }: { artifacts: KumihoArtifact[] }) {
  if (artifacts.length === 0) {
    return <div className="text-xs" style={{ color: 'var(--revka-text-faint)' }}>No artifacts attached.</div>;
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {artifacts.slice(0, 4).map((artifact) => (
        <span key={artifact.kref} className="max-w-full truncate rounded-full border px-2 py-1 text-[10px] font-semibold" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-secondary)' }} title={artifact.location || artifact.kref}>
          {artifact.name || bundleNameFromKref(artifact.kref)}
        </span>
      ))}
      {artifacts.length > 4 ? (
        <span className="rounded-full border px-2 py-1 text-[10px] font-semibold" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}>
          +{artifacts.length - 4}
        </span>
      ) : null}
    </div>
  );
}

function KrefBlock({ label, kref }: { label: string; kref: string }) {
  return (
    <div className="rounded-[8px] border p-2.5" style={{ borderColor: 'var(--revka-border-soft)', background: 'rgba(255,255,255,0.025)' }}>
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: 'var(--revka-text-faint)' }}>{label}</div>
      <CopyableKref kref={kref} />
    </div>
  );
}

function CollapsibleSection({
  title,
  count,
  defaultOpen = false,
  children,
}: {
  title: string;
  count: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border-t" style={{ borderColor: 'var(--revka-border-soft)' }}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-center justify-between gap-2 py-3 text-left"
      >
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--revka-text-secondary)' }}>
          {title}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-[11px]" style={{ color: 'var(--revka-text-faint)' }}>({count})</span>
          <ChevronRight
            className="h-3.5 w-3.5 transition-transform"
            style={{ color: 'var(--revka-text-faint)', transform: open ? 'rotate(90deg)' : undefined }}
          />
        </div>
      </button>
      {open ? <div className="pb-3">{children}</div> : null}
    </div>
  );
}

function BundleBrowser({
  bundles,
  members,
  childSpaces,
  currentSpacePath,
  selectedBundleKref,
  selectedBundleProtected,
  loadingBundles,
  loadingMembers,
  onNavigateSpace,
  onSelectBundle,
  onOpenMember,
  onCopyKref,
}: {
  bundles: KumihoItem[];
  members: KumihoBundleMemberDetail[];
  childSpaces: KumihoSpace[];
  currentSpacePath: string | null;
  selectedBundleKref: string | null;
  selectedBundleProtected: boolean;
  loadingBundles: boolean;
  loadingMembers: boolean;
  onNavigateSpace: (space: KumihoSpace) => void;
  onSelectBundle: (bundle: KumihoItem) => void;
  onOpenMember: (member: KumihoBundleMemberDetail) => void;
  onCopyKref: (kref: string) => void;
}) {
  return (
    <div className="grid min-h-full grid-cols-[minmax(14rem,0.38fr)_minmax(0,1fr)]">
      <div className="border-r" style={{ borderColor: 'var(--revka-border-soft)' }}>
        <div className="border-b px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.12em]" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}>
          Spaces
        </div>
        {childSpaces.length === 0 ? (
          <div className="border-b px-4 py-3 text-xs" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}>
            No child spaces
          </div>
        ) : childSpaces.map((space) => (
          <button
            key={space.path}
            type="button"
            onClick={() => onNavigateSpace(space)}
            className="flex w-full items-center gap-2 border-b px-4 py-3 text-left transition hover:brightness-125"
            style={{ borderColor: 'var(--revka-border-soft)' }}
          >
            <FolderOpen className="h-4 w-4 shrink-0" style={{ color: '#38bdf8' }} />
            <span className="min-w-0 flex-1 truncate text-sm font-medium" style={{ color: 'var(--revka-text-primary)' }}>
              {space.name}
            </span>
            <ChevronRight className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--revka-text-faint)' }} />
          </button>
        ))}
        <div className="border-b px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.12em]" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}>
          Bundles in {currentSpacePath ?? 'selected space'}
        </div>
        {loadingBundles ? (
          <div className="p-4"><StateMessage tone="loading" compact title="Loading bundles..." /></div>
        ) : bundles.length === 0 ? (
          <div className="p-4"><StateMessage compact title="No bundles" description="No Kumiho bundle items were found in this space." /></div>
        ) : bundles.map((bundle) => {
          const active = bundle.kref === selectedBundleKref;
          const protectedBundle = isProtectedBundleName(bundleNameFromKref(bundle.kref));
          return (
            <button
              key={bundle.kref}
              type="button"
              className="flex w-full items-center gap-2 border-b px-4 py-3 text-left transition"
              onClick={() => onSelectBundle(bundle)}
              style={{
                borderColor: 'var(--revka-border-soft)',
                background: active ? 'color-mix(in srgb, var(--revka-signal-selected) 14%, transparent)' : 'transparent',
              }}
            >
              <Package className="h-4 w-4 shrink-0" style={{ color: '#2dd4bf' }} />
              <span className="min-w-0 flex-1 truncate text-sm font-medium" style={{ color: 'var(--revka-text-primary)' }}>
                {itemDisplayName(bundle)}
              </span>
              {protectedBundle ? <AlertTriangle className="h-3.5 w-3.5" style={{ color: 'var(--revka-status-warning)' }} /> : null}
            </button>
          );
        })}
      </div>
      <div className="min-w-0">
        {!selectedBundleKref ? (
          <div className="p-4"><StateMessage compact title="Select a bundle" description="Bundle membership is item-level; locked revision manifests preserve exact reproducibility." /></div>
        ) : (
          <>
            <div className="border-b px-4 py-3" style={{ borderColor: 'var(--revka-border-soft)' }}>
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>
                    Bundle Members
                  </div>
                  <button type="button" className="mt-1 truncate text-left text-xs font-mono" style={{ color: 'var(--revka-text-faint)' }} onClick={() => onCopyKref(selectedBundleKref)}>
                    {selectedBundleKref}
                  </button>
                </div>
                {selectedBundleProtected ? (
                  <span className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-semibold uppercase" style={{ color: 'var(--revka-status-warning)', border: '1px solid color-mix(in srgb, var(--revka-status-warning) 24%, transparent)' }}>
                    <AlertTriangle className="h-3 w-3" />
                    Protected
                  </span>
                ) : null}
              </div>
            </div>
            {loadingMembers ? (
              <div className="p-4"><StateMessage tone="loading" compact title="Loading members..." /></div>
            ) : members.length === 0 ? (
              <div className="p-4"><StateMessage compact title="No members" description="This bundle has no item members." /></div>
            ) : (
              <div>
                {members.map((member) => {
                  const item = member.item;
                  const latest = member.latest_revision;
                  const current = member.current_revision;
                  const tags = current?.tags?.length ? current.tags : latest?.tags ?? [];
                  return (
                    <button
                      key={member.membership.item_kref}
                      type="button"
                      className="w-full border-b px-4 py-3 text-left transition hover:brightness-125"
                      onClick={() => onOpenMember(member)}
                      style={{ borderColor: 'var(--revka-border-soft)' }}
                    >
                      <div className="flex items-center gap-2">
                        <span className="truncate text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
                          {item ? itemDisplayName(item) : member.membership.item_kref}
                        </span>
                        {item ? (
                          <span className="rounded-full px-2 py-0.5 text-[10px] font-semibold" style={{ color: '#2dd4bf', background: 'rgba(45,212,191,0.1)' }}>
                            {item.kind}
                          </span>
                        ) : null}
                        {current ? <TagChip label="current" tone="var(--revka-status-success)" /> : null}
                      </div>
                      <div className="mt-1 truncate text-xs font-mono" style={{ color: 'var(--revka-text-faint)' }}>
                        {member.membership.item_kref}
                      </div>
                      <div className="mt-2 flex flex-wrap items-center gap-1.5">
                        <span className="text-[10px]" style={{ color: 'var(--revka-text-faint)' }}>
                          latest {latest ? revisionLabel(latest) : '--'} / current {current ? revisionLabel(current) : '--'}
                        </span>
                        {tags.slice(0, 8).map((tag) => <TagChip key={tag} label={tag} tone="var(--revka-text-faint)" />)}
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function CreateActionModal({
  action,
  project,
  spacePath,
  selectedItem,
  selectedRevision,
  selectedBundle,
  bundleMembers,
  onClose,
  onCreated,
}: {
  action: CreateAction;
  project: string | null;
  spacePath: string | null;
  selectedItem: KumihoItem | null;
  selectedRevision: KumihoRevision | null;
  selectedBundle: KumihoItem | null;
  bundleMembers: KumihoBundleMemberDetail[];
  onClose: () => void;
  onCreated: (target?: { item?: KumihoItem; revision?: KumihoRevision; bundle?: KumihoItem }) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [kind, setKind] = useState(selectedItem?.kind ?? 'character-state');
  const [metadataText, setMetadataText] = useState('');
  const [artifactName, setArtifactName] = useState(action === 'context-pack' ? 'CONTEXT_PACK.md' : 'content.md');
  const [location, setLocation] = useState('');
  const [content, setContent] = useState(ITEM_KIND_TEMPLATES[kind] ?? '');
  const [writeFile, setWriteFile] = useState(['item', 'artifact', 'context-pack'].includes(action));
  const [overwrite, setOverwrite] = useState(false);
  const [targetRevisionKref, setTargetRevisionKref] = useState(selectedRevision?.kref ?? '');
  const [targetItemKref, setTargetItemKref] = useState(selectedItem?.kref ?? '');
  const [tagValue, setTagValue] = useState('active');
  const [removeTag, setRemoveTag] = useState('');
  const [edgeType, setEdgeType] = useState('REFERENCES');
  const [targetEdgeKref, setTargetEdgeKref] = useState('');
  const [allowProtected, setAllowProtected] = useState(false);

  useEffect(() => {
    if (!content.trim() || Object.values(ITEM_KIND_TEMPLATES).includes(content)) {
      setContent(ITEM_KIND_TEMPLATES[kind] ?? '');
    }
  }, [kind]); // eslint-disable-line react-hooks/exhaustive-deps

  const title = {
    project: 'Create Project',
    space: 'Create Space',
    subspace: 'Create Subspace',
    item: 'Create Item',
    bundle: 'Create Bundle',
    revision: 'Create Revision',
    artifact: 'Attach or Link Artifact',
    edge: 'Create Edge',
    tag: 'Manage Revision Tags',
    'bundle-member-add': 'Add Item to Bundle',
    'bundle-member-remove': 'Remove Bundle Member',
    'context-pack': 'Create Context Pack from Bundle',
  }[action];

  const resolvedSpacePath = spacePath ?? (project ? `/${project}` : '');
  const protectedBundle = selectedBundle ? isProtectedBundleName(bundleNameFromKref(selectedBundle.kref)) : false;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const metadata = parseMetadataText(metadataText);
      if (action === 'project') {
        await createAssetProject({ name, description: metadata.description });
        onCreated();
        return;
      }
      if (action === 'space' || action === 'subspace') {
        await createAssetSpace({ parent_path: action === 'space' && project ? `/${project}` : resolvedSpacePath, name });
        onCreated();
        return;
      }
      if (action === 'item') {
        const item = await createAssetItem({ space_path: resolvedSpacePath, item_name: name, kind, metadata });
        let revision: KumihoRevision | undefined;
        const shouldCreateArtifact = writeFile || Boolean(location.trim());
        if (content.trim() || shouldCreateArtifact) {
          revision = await createAssetRevision({ item_kref: item.kref, metadata: { created_by: 'revka-asset-browser' } });
          if (shouldCreateArtifact) {
            await createAssetArtifact({
              revision_kref: revision.kref,
              name: artifactName,
              location,
              content,
              write_file: writeFile,
              overwrite,
              metadata: { mime: artifactName.endsWith('.md') ? 'text/markdown' : 'text/plain' },
            });
          }
        }
        onCreated({ item, revision });
        return;
      }
      if (action === 'bundle') {
        const bundle = await createAssetBundle({ space_path: resolvedSpacePath, bundle_name: name, metadata });
        onCreated({ bundle });
        return;
      }
      if (action === 'revision') {
        const revision = await createAssetRevision({ item_kref: targetItemKref || selectedItem?.kref || '', metadata });
        onCreated({ revision });
        return;
      }
      if (action === 'artifact') {
        await createAssetArtifact({
          revision_kref: targetRevisionKref || selectedRevision?.kref || '',
          name: artifactName,
          location,
          content,
          write_file: writeFile,
          overwrite,
          validate_exists: !writeFile,
          metadata,
        });
        onCreated();
        return;
      }
      if (action === 'edge') {
        await createAssetEdge({
          source_kref: selectedRevision?.kref || targetRevisionKref,
          target_kref: targetEdgeKref,
          edge_type: edgeType,
          metadata,
        });
        onCreated();
        return;
      }
      if (action === 'tag') {
        let revision: KumihoRevision | undefined;
        if (tagValue.trim()) revision = await tagAssetRevision(selectedRevision?.kref || targetRevisionKref, tagValue.trim());
        if (removeTag.trim()) revision = await untagAssetRevision(selectedRevision?.kref || targetRevisionKref, removeTag.trim());
        onCreated({ revision });
        return;
      }
      if (action === 'bundle-member-add') {
        await addAssetBundleMember({
          bundle_kref: selectedBundle?.kref || '',
          item_kref: targetItemKref || selectedItem?.kref || '',
          metadata,
          allow_protected: allowProtected,
        });
        onCreated();
        return;
      }
      if (action === 'bundle-member-remove') {
        await removeAssetBundleMember({
          bundle_kref: selectedBundle?.kref || '',
          item_kref: targetItemKref || selectedItem?.kref || '',
          allow_protected: allowProtected,
        });
        onCreated();
        return;
      }
      if (action === 'context-pack') {
        const packName = name || `${bundleNameFromKref(selectedBundle?.kref)}-context-pack`;
        const item = await createAssetItem({
          space_path: resolvedSpacePath,
          item_name: packName,
          kind: 'context-pack',
          metadata: {
            source_bundle: selectedBundle?.kref ?? '',
            created_by: 'revka-asset-browser',
          },
        });
        const revision = await createAssetRevision({ item_kref: item.kref, metadata: { source_bundle: selectedBundle?.kref ?? '' } });
        const manifest = [
          `# Kumiho Context Pack: ${packName}`,
          '',
          '## Source Bundle',
          selectedBundle?.kref ?? '',
          '',
          '## Locked Manifest Candidates',
          ...bundleMembers.map((member) => `- ${member.current_revision?.kref ?? member.latest_revision?.kref ?? member.membership.item_kref}`),
          '',
          '## Notes',
          'Bundle membership is item-level. Review and lock exact revisions before using this context pack for reproducible generation.',
        ].join('\n');
        await createAssetArtifact({
          revision_kref: revision.kref,
          name: artifactName || 'CONTEXT_PACK.md',
          location,
          content: manifest,
          write_file: writeFile,
          overwrite,
          metadata: { source_bundle: selectedBundle?.kref ?? '', mime: 'text/markdown' },
        });
        onCreated({ item, revision });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Asset creation failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={title} description="Kumiho-native authoring actions are prefilled from the current Asset Browser context." onClose={busy ? () => {} : onClose} size="2xl">
      <div className="grid gap-3 md:grid-cols-2">
        {action === 'project' ? (
          <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
            Project name
            <input className="revka-input mt-1" value={name} onChange={(event) => setName(event.target.value)} autoFocus />
          </label>
        ) : null}
        {['space', 'subspace', 'item', 'bundle', 'context-pack'].includes(action) ? (
          <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
            Name
            <input className="revka-input mt-1" value={name} onChange={(event) => setName(event.target.value)} autoFocus />
          </label>
        ) : null}
        {action === 'item' ? (
          <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
            Kind
            <input className="revka-input mt-1" value={kind} list="asset-kind-templates" onChange={(event) => setKind(event.target.value)} />
            <datalist id="asset-kind-templates">
              {Object.keys(ITEM_KIND_TEMPLATES).map((value) => <option key={value} value={value} />)}
            </datalist>
          </label>
        ) : null}
        {['revision', 'bundle-member-add', 'bundle-member-remove'].includes(action) ? (
          <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
            Item kref
            <input className="revka-input mt-1 font-mono text-xs" value={targetItemKref} onChange={(event) => setTargetItemKref(event.target.value)} />
          </label>
        ) : null}
        {['artifact', 'tag'].includes(action) ? (
          <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
            Revision kref
            <input className="revka-input mt-1 font-mono text-xs" value={targetRevisionKref} onChange={(event) => setTargetRevisionKref(event.target.value)} />
          </label>
        ) : null}
        {action === 'edge' ? (
          <>
            <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
              Edge type
              <input className="revka-input mt-1" value={edgeType} list="asset-edge-types" onChange={(event) => setEdgeType(event.target.value)} />
              <datalist id="asset-edge-types">{EDGE_TYPES.map((value) => <option key={value} value={value} />)}</datalist>
            </label>
            <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
              Target revision kref
              <input className="revka-input mt-1 font-mono text-xs" value={targetEdgeKref} onChange={(event) => setTargetEdgeKref(event.target.value)} />
            </label>
          </>
        ) : null}
        {action === 'tag' ? (
          <>
            <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
              Add tag
              <input className="revka-input mt-1" value={tagValue} onChange={(event) => setTagValue(event.target.value)} />
            </label>
            <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
              Remove tag
              <input className="revka-input mt-1" value={removeTag} onChange={(event) => setRemoveTag(event.target.value)} placeholder="current is blocked" />
            </label>
          </>
        ) : null}
        {['artifact', 'item', 'context-pack'].includes(action) ? (
          <>
            <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
              Artifact name
              <input className="revka-input mt-1" value={artifactName} onChange={(event) => setArtifactName(event.target.value)} />
            </label>
            <label className="text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
              Location / path reference
              <input
                className="revka-input mt-1 font-mono text-xs"
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                placeholder={writeFile ? 'Auto: Revka workspace/artifacts/kumiho/... when blank' : 'file:///G:/path/content.md'}
              />
            </label>
          </>
        ) : null}
      </div>
      {['item', 'artifact', 'context-pack'].includes(action) ? (
        <div className="mt-3 space-y-2">
          <div className="flex flex-wrap gap-3 text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={writeFile} onChange={(event) => setWriteFile(event.target.checked)} /> Create/write file under Revka workspace when location is blank</label>
            <label className="inline-flex items-center gap-2"><input type="checkbox" checked={overwrite} onChange={(event) => setOverwrite(event.target.checked)} /> Overwrite existing file</label>
          </div>
          {writeFile && !location.trim() ? (
            <div className="rounded-[8px] border px-3 py-2 text-xs" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}>
              The gateway will store this artifact under the configured Revka workspace at <span className="font-mono">artifacts/kumiho/&lt;project&gt;/&lt;space&gt;/&lt;item&gt;/r&lt;revision&gt;/&lt;artifact&gt;</span>.
            </div>
          ) : null}
          <textarea className="revka-input min-h-[14rem] w-full resize-y font-mono text-xs leading-5" value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} />
        </div>
      ) : null}
      {['item', 'bundle', 'revision', 'artifact', 'edge', 'project', 'bundle-member-add'].includes(action) ? (
        <label className="mt-3 block text-xs font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)' }}>
          Metadata JSON or key: value lines
          <textarea className="revka-input mt-1 min-h-[6rem] w-full resize-y font-mono text-xs leading-5" value={metadataText} onChange={(event) => setMetadataText(event.target.value)} spellCheck={false} />
        </label>
      ) : null}
      {protectedBundle && ['bundle-member-add', 'bundle-member-remove'].includes(action) ? (
        <label className="mt-3 flex items-center gap-2 rounded-[8px] border px-3 py-2 text-xs" style={{ borderColor: 'color-mix(in srgb, var(--revka-status-warning) 24%, transparent)', color: 'var(--revka-status-warning)' }}>
          <input type="checkbox" checked={allowProtected} onChange={(event) => setAllowProtected(event.target.checked)} />
          Confirm protected canon bundle mutation
        </label>
      ) : null}
      {error ? <div className="mt-3 text-sm" style={{ color: 'var(--revka-status-danger)' }}>{error}</div> : null}
      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="revka-button" onClick={onClose} disabled={busy}>Cancel</button>
        <button type="button" className="revka-button revka-button-primary" onClick={submit} disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Apply
        </button>
      </div>
    </Modal>
  );
}

function DependencyGraphModal({
  revision,
  onClose,
  onOpenKref,
}: {
  revision: KumihoRevision;
  onClose: () => void;
  onOpenKref: (kref: string) => void;
}) {
  const graphRef = useRef<any>(null);
  const labelBoxesRef = useRef<LabelBox[]>([]);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [graph, setGraph] = useState<KumihoAssetDependencyGraphResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [direction, setDirection] = useState('both');
  const [depth, setDepth] = useState(2);
  const [nodeLimit, setNodeLimit] = useState(120);
  const [edgeType, setEdgeType] = useState('all');
  const [selectedNode, setSelectedNode] = useState<KumihoAssetGraphNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<KumihoEdge | null>(null);
  const [hoveredNodeKref, setHoveredNodeKref] = useState<string | null>(null);
  const [hoveredEdgeKey, setHoveredEdgeKey] = useState<string | null>(null);
  const [dimensions, setDimensions] = useState({ width: 720, height: 460 });

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchAssetDependencyGraph({
      revision_kref: revision.kref,
      direction,
      depth,
      edge_type: edgeType === 'all' ? undefined : edgeType,
      node_limit: nodeLimit,
    })
      .then((data) => {
        setGraph(data);
        setSelectedNode(data.nodes.find((node) => node.kref === data.center_kref) ?? data.nodes[0] ?? null);
        setSelectedEdge(null);
        setHoveredNodeKref(null);
        setHoveredEdgeKey(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load dependency graph.'))
      .finally(() => setLoading(false));
  }, [depth, direction, edgeType, nodeLimit, revision.kref]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const update = () => setDimensions({ width: Math.max(320, node.clientWidth), height: Math.max(360, node.clientHeight) });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const graphData = useMemo(() => {
    if (!graph) return { nodes: [] as ForceNode[], links: [] as ForceLink[] };
    const nodeIds = new Set(graph.nodes.map((node) => node.kref));
    const baseLinks = graph.edges
      .filter((edge) => nodeIds.has(edge.source_kref) && nodeIds.has(edge.target_kref))
      .map((edge) => ({ source: edge.source_kref, target: edge.target_kref, edge }));
    const adjacency = new Map<string, string[]>();
    const degreeByKref = new Map<string, number>();
    for (const node of graph.nodes) {
      adjacency.set(node.kref, []);
      degreeByKref.set(node.kref, 0);
    }
    for (const link of baseLinks) {
      adjacency.get(link.edge.source_kref)?.push(link.edge.target_kref);
      adjacency.get(link.edge.target_kref)?.push(link.edge.source_kref);
      degreeByKref.set(link.edge.source_kref, (degreeByKref.get(link.edge.source_kref) ?? 0) + 1);
      degreeByKref.set(link.edge.target_kref, (degreeByKref.get(link.edge.target_kref) ?? 0) + 1);
    }

    const depthByKref = new Map<string, number>([[graph.center_kref, 0]]);
    const queue = [graph.center_kref];
    for (let index = 0; index < queue.length; index += 1) {
      const current = queue[index]!;
      const currentDepth = depthByKref.get(current) ?? 0;
      if (currentDepth >= graph.depth) continue;
      for (const neighbor of adjacency.get(current) ?? []) {
        if (depthByKref.has(neighbor)) continue;
        depthByKref.set(neighbor, currentDepth + 1);
        queue.push(neighbor);
      }
    }

    const rings = new Map<number, string[]>();
    for (const node of graph.nodes) {
      const ring = depthByKref.get(node.kref) ?? graph.depth;
      const bucket = rings.get(ring) ?? [];
      bucket.push(node.kref);
      rings.set(ring, bucket);
    }
    const ringPosition = new Map<string, { index: number; total: number }>();
    for (const krefs of rings.values()) {
      for (const [index, kref] of krefs.entries()) {
        ringPosition.set(kref, { index, total: Math.max(1, krefs.length) });
      }
    }

    const nodes = graph.nodes.map((node) => {
      const graphDepth = depthByKref.get(node.kref) ?? graph.depth;
      const degree = degreeByKref.get(node.kref) ?? 0;
      const position = ringPosition.get(node.kref);
      const angle = position ? (position.index / position.total) * Math.PI * 2 - Math.PI / 2 : 0;
      const radius = graphDepth === 0 ? 0 : 160 + graphDepth * 150;
      return {
        ...node,
        id: node.kref,
        graphDepth,
        graphDegree: degree,
        val: graphDepth === 0 ? 9 : Math.min(8, 3.5 + degree * 0.22),
        x: graphDepth === 0 ? 0 : Math.cos(angle) * radius,
        y: graphDepth === 0 ? 0 : Math.sin(angle) * radius,
      };
    });

    const pairTotals = new Map<string, number>();
    for (const link of baseLinks) {
      const key = graphEdgePairKey(link.edge);
      pairTotals.set(key, (pairTotals.get(key) ?? 0) + 1);
    }
    const pairIndexes = new Map<string, number>();
    const links = baseLinks.map((link) => {
      const key = graphEdgePairKey(link.edge);
      const index = pairIndexes.get(key) ?? 0;
      pairIndexes.set(key, index + 1);
      const total = pairTotals.get(key) ?? 1;
      return {
        ...link,
        curveOffset: total > 1 ? (index - (total - 1) / 2) * 18 : 0,
      };
    });

    return { nodes, links };
  }, [graph]);

  const selectedEdgeKey = selectedEdge ? graphEdgeKey(selectedEdge) : null;
  const edgeLegend = useMemo(() => {
    if (!graph) return [];
    const counts = new Map<string, number>();
    for (const edge of graph.edges) {
      counts.set(edge.edge_type, (counts.get(edge.edge_type) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6);
  }, [graph]);
  const nodeLegend = useMemo(() => {
    if (!graph) return [];
    const counts = new Map<string, number>();
    for (const node of graph.nodes) {
      const kind = node.kind || 'unknown';
      counts.set(kind, (counts.get(kind) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 6);
  }, [graph]);

  useEffect(() => {
    const api = graphRef.current;
    if (!api || graphData.nodes.length === 0) return;
    api.d3Force?.('charge')?.strength?.(-360);
    api.d3Force?.('collide')?.radius?.((node: ForceNode) => (node.graphDepth === 0 ? 34 : 24));
    api.d3Force?.('link')?.distance?.((link: ForceLink) => {
      const source = typeof link.source === 'object' ? link.source : null;
      const target = typeof link.target === 'object' ? link.target : null;
      const outerDepth = Math.max(source?.graphDepth ?? 1, target?.graphDepth ?? 1);
      return 120 + outerDepth * 50;
    })?.strength?.(0.35);
    api.d3ReheatSimulation?.();
    const timer = window.setTimeout(() => api.zoomToFit?.(450, 96), 650);
    return () => window.clearTimeout(timer);
  }, [graphData]);

  const paintNode = useCallback((node: ForceNode, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const x = node.x ?? 0;
    const y = node.y ?? 0;
    const isCenter = node.kref === revision.kref;
    const isSelected = node.kref === selectedNode?.kref;
    const isHovered = node.kref === hoveredNodeKref;
    const radius = isCenter ? 9 : Math.max(5, Math.min(8, 4 + node.graphDegree * 0.12));
    const meta = getKindMeta(node.kind ?? '');

    if (isCenter || isSelected || isHovered) {
      ctx.beginPath();
      ctx.arc(x, y, radius + (isCenter ? 10 : 7), 0, 2 * Math.PI);
      ctx.fillStyle = isCenter ? 'rgba(45,212,191,0.18)' : `${meta.color}26`;
      ctx.fill();
      ctx.beginPath();
      ctx.arc(x, y, radius + (isCenter ? 4 : 3), 0, 2 * Math.PI);
      ctx.strokeStyle = isCenter ? 'rgba(45,212,191,0.7)' : `${meta.color}80`;
      ctx.lineWidth = isCenter ? 1.8 : 1.2;
      ctx.stroke();
    }

    ctx.beginPath();
    ctx.arc(x, y, radius, 0, 2 * Math.PI);
    ctx.fillStyle = node.missing ? '#f59e0b' : meta.color;
    ctx.fill();
    ctx.lineWidth = isCenter || isSelected ? 2 : 1;
    ctx.strokeStyle = isCenter ? '#ffffff' : isSelected ? meta.color : 'rgba(255,255,255,0.38)';
    ctx.stroke();

    const forcedLabel = isCenter || isSelected || isHovered;
    const showLabel = forcedLabel
      || globalScale > 1.16
      || (globalScale > 0.82 && node.graphDepth <= 1 && node.graphDegree >= 2);
    if (!showLabel) return;

    const label = truncateLabel(graphNodeLabel(node), isCenter || isSelected || isHovered ? 34 : 24);
    const fontSize = Math.max(9, Math.min(12, 11 / globalScale));
    ctx.font = `${isCenter || isSelected ? '700' : '600'} ${fontSize}px Inter, system-ui, sans-serif`;
    const textWidth = ctx.measureText(label).width;
    const labelWidth = textWidth + 14;
    const labelHeight = fontSize + 8;
    const labelX = x + radius + 8;
    const labelY = y - labelHeight / 2;
    const labelBox = {
      x1: labelX - 4,
      y1: labelY - 3,
      x2: labelX + labelWidth + 4,
      y2: labelY + labelHeight + 3,
    };
    if (!forcedLabel && labelBoxOverlaps(labelBox, labelBoxesRef.current)) return;
    labelBoxesRef.current.push(labelBox);

    roundedRect(ctx, labelX, labelY, labelWidth, labelHeight, 7);
    ctx.fillStyle = isCenter || isSelected
      ? 'rgba(4, 17, 18, 0.92)'
      : 'rgba(7, 16, 18, 0.82)';
    ctx.fill();
    ctx.strokeStyle = isCenter || isSelected ? `${meta.color}88` : 'rgba(148,163,184,0.22)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = 'rgba(235, 245, 245, 0.94)';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, labelX + 7, y);
  }, [hoveredNodeKref, revision.kref, selectedNode?.kref]);

  const paintLink = useCallback((link: ForceLink, ctx: CanvasRenderingContext2D, globalScale: number) => {
    const source = link.source as ForceNode;
    const target = link.target as ForceNode;
    if (source.x == null || source.y == null || target.x == null || target.y == null) return;
    const edgeKey = graphEdgeKey(link.edge);
    const isActive = edgeKey === selectedEdgeKey || edgeKey === hoveredEdgeKey;
    const visual = graphEdgeVisual(link.edge.edge_type);
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(1, Math.hypot(dx, dy));
    const normalX = -dy / distance;
    const normalY = dx / distance;
    const controlX = (source.x + target.x) / 2 + normalX * link.curveOffset;
    const controlY = (source.y + target.y) / 2 + normalY * link.curveOffset;

    if (isActive) {
      ctx.beginPath();
      ctx.moveTo(source.x, source.y);
      ctx.quadraticCurveTo(controlX, controlY, target.x, target.y);
      ctx.strokeStyle = visual.glow;
      ctx.lineWidth = 7;
      ctx.stroke();
    }

    ctx.beginPath();
    ctx.moveTo(source.x, source.y);
    ctx.quadraticCurveTo(controlX, controlY, target.x, target.y);
    ctx.strokeStyle = visual.color;
    ctx.globalAlpha = isActive ? 0.92 : 0.5;
    ctx.lineWidth = isActive ? 2.3 : 1.2;
    ctx.stroke();
    ctx.globalAlpha = 1;

    const arrowT = 0.82;
    const arrowX = (1 - arrowT) ** 2 * source.x + 2 * (1 - arrowT) * arrowT * controlX + arrowT ** 2 * target.x;
    const arrowY = (1 - arrowT) ** 2 * source.y + 2 * (1 - arrowT) * arrowT * controlY + arrowT ** 2 * target.y;
    const tangentX = 2 * (1 - arrowT) * (controlX - source.x) + 2 * arrowT * (target.x - controlX);
    const tangentY = 2 * (1 - arrowT) * (controlY - source.y) + 2 * arrowT * (target.y - controlY);
    const angle = Math.atan2(tangentY, tangentX);
    const arrowSize = isActive ? 6 : 4.5;
    ctx.beginPath();
    ctx.moveTo(arrowX, arrowY);
    ctx.lineTo(arrowX - arrowSize * Math.cos(angle - 0.55), arrowY - arrowSize * Math.sin(angle - 0.55));
    ctx.lineTo(arrowX - arrowSize * Math.cos(angle + 0.55), arrowY - arrowSize * Math.sin(angle + 0.55));
    ctx.closePath();
    ctx.fillStyle = visual.color;
    ctx.globalAlpha = isActive ? 1 : 0.62;
    ctx.fill();
    ctx.globalAlpha = 1;

    const showEdgeLabel = isActive || (globalScale > 1.55 && graphData.links.length <= 30);
    if (!showEdgeLabel) return;
    const label = visual.label || link.edge.edge_type.toLowerCase();
    const fontSize = Math.max(8, Math.min(10, 9 / globalScale));
    ctx.font = `700 ${fontSize}px Inter, system-ui, sans-serif`;
    const labelWidth = ctx.measureText(label).width + 12;
    const labelHeight = fontSize + 7;
    const labelX = controlX - labelWidth / 2;
    const labelY = controlY - labelHeight / 2;
    roundedRect(ctx, labelX, labelY, labelWidth, labelHeight, 6);
    ctx.fillStyle = 'rgba(5, 14, 16, 0.88)';
    ctx.fill();
    ctx.strokeStyle = `${visual.color}88`;
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.fillStyle = 'rgba(235, 245, 245, 0.9)';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, controlX, controlY + 0.5);
  }, [graphData.links.length, hoveredEdgeKey, selectedEdgeKey]);

  return (
    <Modal title="Dependency Graph" description="A local revision-centered graph for dependency, provenance, and impact analysis." onClose={onClose} size="2xl">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <select className="revka-input h-9 py-0 text-xs" value={direction} onChange={(event) => setDirection(event.target.value)}>
          <option value="outgoing">Dependencies</option>
          <option value="incoming">Dependents</option>
          <option value="both">Both</option>
        </select>
        <div className="inline-flex h-9 overflow-hidden rounded-[8px] border" style={{ borderColor: 'var(--revka-border-soft)' }}>
          {GRAPH_DEPTH_OPTIONS.map((value) => (
            <button
              key={value}
              type="button"
              className="px-3 text-xs font-semibold transition"
              style={{
                color: depth === value ? 'var(--revka-text-primary)' : 'var(--revka-text-muted)',
                background: depth === value ? 'color-mix(in srgb, var(--revka-signal-selected) 18%, transparent)' : 'transparent',
                borderRight: value === GRAPH_DEPTH_OPTIONS[GRAPH_DEPTH_OPTIONS.length - 1] ? 'none' : '1px solid var(--revka-border-soft)',
              }}
              onClick={() => setDepth(value)}
            >
              Depth {value}
            </button>
          ))}
        </div>
        <select className="revka-input h-9 py-0 text-xs" value={nodeLimit} onChange={(event) => setNodeLimit(Number(event.target.value))}>
          {GRAPH_NODE_LIMIT_OPTIONS.map((value) => <option key={value} value={value}>Nodes {value}</option>)}
        </select>
        <select className="revka-input h-9 py-0 text-xs" value={edgeType} onChange={(event) => setEdgeType(event.target.value)}>
          <option value="all">All edge types</option>
          {EDGE_TYPES.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        <button type="button" className="revka-button h-9 px-3 text-xs" onClick={load} disabled={loading}>
          {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCcw className="h-3.5 w-3.5" />}
          Reload graph
        </button>
        <button type="button" className="revka-button h-9 px-3 text-xs" onClick={() => copyToClipboard(revision.kref)}>
          <Copy className="h-3.5 w-3.5" />
          Copy center kref
        </button>
      </div>
      <div className="grid min-h-[32rem] gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
        <div ref={containerRef} className="relative min-h-[30rem] overflow-hidden rounded-[12px] border" style={{ borderColor: 'var(--revka-border-soft)', background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)' }}>
          {loading ? <div className="absolute inset-0 z-10 flex items-center justify-center"><StateMessage tone="loading" title="Loading graph..." /></div> : null}
          {error ? <div className="absolute inset-0 z-10 flex items-center justify-center p-6"><StateMessage tone="error" title="Graph failed" description={error} /></div> : null}
          {!loading && !error && graphData.nodes.length > 0 ? (
            <ForceGraph2D
              ref={graphRef}
              graphData={graphData}
              width={dimensions.width}
              height={dimensions.height}
              backgroundColor="transparent"
              nodeRelSize={4}
              nodeVal={(node: ForceNode) => node.val}
              nodeLabel={(node: ForceNode) => {
                const parts = [graphNodeLabel(node), node.kind, `r${node.revision_number ?? '?'}`].filter(Boolean);
                return `${parts.join(' · ')}\n${node.kref}`;
              }}
              nodeCanvasObject={paintNode}
              nodePointerAreaPaint={(node: ForceNode, color: string, ctx: CanvasRenderingContext2D) => {
                const x = node.x ?? 0;
                const y = node.y ?? 0;
                ctx.beginPath();
                ctx.arc(x, y, 18, 0, 2 * Math.PI);
                ctx.fillStyle = color;
                ctx.fill();
              }}
              linkLabel={(link: ForceLink) => `${link.edge.edge_type}\n${link.edge.source_kref}\n-> ${link.edge.target_kref}`}
              linkCanvasObject={paintLink}
              linkCanvasObjectMode={() => 'replace'}
              onRenderFramePre={() => {
                labelBoxesRef.current = [];
              }}
              onNodeClick={(node: ForceNode) => {
                setSelectedNode(node);
                setSelectedEdge(null);
              }}
              onLinkClick={(link: ForceLink) => {
                setSelectedEdge(link.edge);
              }}
              onNodeHover={(node: ForceNode | null) => setHoveredNodeKref(node?.kref ?? null)}
              onLinkHover={(link: ForceLink | null) => setHoveredEdgeKey(link ? graphEdgeKey(link.edge) : null)}
              cooldownTicks={160}
              d3AlphaDecay={0.018}
              d3VelocityDecay={0.28}
              warmupTicks={40}
            />
          ) : null}
          {!loading && !error && graph ? (
            <div className="pointer-events-none absolute left-3 top-3 z-10 flex flex-wrap items-center gap-2 rounded-[10px] border px-3 py-2 text-[11px] font-medium" style={{ borderColor: 'var(--revka-border-soft)', background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 92%, transparent)', color: 'var(--revka-text-secondary)' }}>
              <span><strong style={{ color: 'var(--revka-text-primary)' }}>{graph.nodes.length}</strong> nodes</span>
              <span><strong style={{ color: 'var(--revka-text-primary)' }}>{graph.edges.length}</strong> edges</span>
              <span>depth {graph.depth}</span>
            </div>
          ) : null}
          {!loading && !error && graph?.truncated ? (
            <div className="pointer-events-none absolute right-3 top-3 z-10 rounded-[10px] border px-3 py-2 text-[11px] font-semibold" style={{ borderColor: 'color-mix(in srgb, var(--revka-status-warning) 35%, transparent)', background: 'rgba(67, 52, 18, 0.72)', color: 'var(--revka-status-warning)' }}>
              Truncated at {graph.node_limit}
            </div>
          ) : null}
          {!loading && !error && (nodeLegend.length > 0 || edgeLegend.length > 0) ? (
            <div className="pointer-events-none absolute bottom-3 left-3 z-10 flex max-w-[78%] flex-col gap-2 rounded-[10px] border px-3 py-2" style={{ borderColor: 'var(--revka-border-soft)', background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 92%, transparent)' }}>
              {nodeLegend.length > 0 ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: 'var(--revka-text-faint)' }}>Nodes</span>
                  {nodeLegend.map(([kind, count]) => {
                    const meta = getKindMeta(kind);
                    return (
                      <span key={kind} className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase" style={{ color: 'var(--revka-text-secondary)' }}>
                        <span className="h-2.5 w-2.5 rounded-full" style={{ background: meta.color, boxShadow: `0 0 10px ${meta.color}` }} />
                        {kind} {count}
                      </span>
                    );
                  })}
                </div>
              ) : null}
              {edgeLegend.length > 0 ? (
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[10px] font-semibold uppercase tracking-[0.08em]" style={{ color: 'var(--revka-text-faint)' }}>Edges</span>
                  {edgeLegend.map(([type, count]) => {
                    const visual = graphEdgeVisual(type);
                    return (
                      <span key={type} className="inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase" style={{ color: 'var(--revka-text-secondary)' }}>
                        <span className="h-0.5 w-5 rounded-full" style={{ background: visual.color, boxShadow: `0 0 10px ${visual.color}` }} />
                        {type} {count}
                      </span>
                    );
                  })}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
        <div className="min-h-0 overflow-y-auto rounded-[12px] border p-4" style={{ borderColor: 'var(--revka-border-soft)', background: 'linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.015))' }}>
          {selectedEdge ? (
            <div className="space-y-4">
              <div className="rounded-[10px] border p-3" style={{ borderColor: `${graphEdgeVisual(selectedEdge.edge_type).color}55`, background: graphEdgeVisual(selectedEdge.edge_type).glow }}>
                <div className="flex items-center justify-between gap-2">
                  <div>
                    <div className="text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>Relationship</div>
                    <div className="mt-2 text-lg font-semibold" style={{ color: 'var(--revka-text-primary)' }}>{graphEdgeVisual(selectedEdge.edge_type).label}</div>
                  </div>
                  <TagChip label={selectedEdge.edge_type} tone={graphEdgeVisual(selectedEdge.edge_type).color} />
                </div>
              </div>

              <div className="space-y-2">
                <KrefBlock label="Source" kref={selectedEdge.source_kref} />
                <div className="flex justify-center">
                  <div className="rounded-full border p-1.5" style={{ borderColor: 'var(--revka-border-soft)', color: graphEdgeVisual(selectedEdge.edge_type).color }}>
                    <ArrowRight className="h-4 w-4" />
                  </div>
                </div>
                <KrefBlock label="Target" kref={selectedEdge.target_kref} />
              </div>

              <MetadataSection metadata={selectedEdge.metadata} maxVisible={6} />
            </div>
          ) : selectedNode ? (
            <div className="space-y-4">
              <div className="rounded-[10px] border p-3" style={{ borderColor: `${getKindMeta(selectedNode.kind ?? '').color}55`, background: getKindMeta(selectedNode.kind ?? '').bg }}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>Revision Node</div>
                    <div className="mt-2 truncate text-lg font-semibold" style={{ color: 'var(--revka-text-primary)' }} title={graphNodeLabel(selectedNode)}>
                      {graphNodeLabel(selectedNode)}
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {selectedNode.kind ? <TagChip label={selectedNode.kind} tone={getKindMeta(selectedNode.kind).color} /> : null}
                      {selectedNode.revision_number ? <TagChip label={`r${selectedNode.revision_number}`} tone="var(--revka-text-faint)" /> : null}
                      {selectedNode.tags.slice(0, 3).map((tag) => <TagChip key={tag} label={tag} tone="var(--revka-text-faint)" />)}
                      {selectedNode.tags.length > 3 ? <TagChip label={`+${selectedNode.tags.length - 3}`} tone="var(--revka-text-faint)" /> : null}
                    </div>
                  </div>
                  <button type="button" className="revka-button h-8 px-2 text-xs" onClick={() => onOpenKref(selectedNode.kref)}>
                    <ArrowRight className="h-3.5 w-3.5" />
                    Open
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2">
                <DetailStat label="Artifacts" value={selectedNode.artifacts.length} tone={getKindMeta(selectedNode.kind ?? '').color} />
                <DetailStat label="Revision" value={selectedNode.revision_number ? `r${selectedNode.revision_number}` : '--'} />
                <DetailStat label="Incoming" value={selectedNode.incoming_edges.length} />
                <DetailStat label="Outgoing" value={selectedNode.outgoing_edges.length} />
              </div>

              <KrefBlock label="Revision kref" kref={selectedNode.kref} />

              <section className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>Artifacts</div>
                <ArtifactStrip artifacts={selectedNode.artifacts} />
              </section>

              <MetadataSection metadata={selectedNode.metadata} />
            </div>
          ) : (
            <StateMessage compact title="Select a node" description="Click a node or edge to inspect its Kumiho details." />
          )}
        </div>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function Assets() {
  const { t, tpl } = useT();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialParsedKref = parseKref(searchParams.get('kref'));
  const initialProject = searchParams.get('project') ?? initialParsedKref?.project ?? null;
  const initialSpacePath = searchParams.get('space') ?? initialParsedKref?.spacePath ?? (initialProject ? `/${initialProject}` : null);
  /* ---- state ---- */
  const [projects, setProjects] = useState<KumihoProject[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(initialProject);
  const [projectDropdownOpen, setProjectDropdownOpen] = useState(false);
  const [currentPath, setCurrentPath] = useState<PathSegment[]>(() => pathSegmentsFromSpacePath(initialSpacePath));
  const [childSpaces, setChildSpaces] = useState<KumihoSpace[]>([]);
  const [items, setItems] = useState<KumihoItem[]>([]);
  const [selectedItem, setSelectedItem] = useState<KumihoItem | null>(null);
  const [requestedItemKref, setRequestedItemKref] = useState<string | null>(searchParams.get('item') ?? initialParsedKref?.itemKref ?? null);
  const [revisions, setRevisions] = useState<KumihoRevision[]>([]);
  const [selectedRevision, setSelectedRevision] = useState<KumihoRevision | null>(null);
  const [requestedRevisionKref, setRequestedRevisionKref] = useState<string | null>(searchParams.get('revision') ?? initialParsedKref?.revisionKref ?? null);
  const [artifacts, setArtifacts] = useState<KumihoArtifact[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<KumihoArtifact | null>(null);
  const [viewerArtifact, setViewerArtifact] = useState<KumihoArtifact | null>(null);
  const [edges, setEdges] = useState<KumihoEdge[]>([]);
  const [searchQuery, setSearchQuery] = useState(searchParams.get('q') ?? '');
  const [searchResults, setSearchResults] = useState<KumihoSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [activeTab, setActiveTab] = useState<AssetTab>((searchParams.get('tab') === 'bundles' ? 'bundles' : 'items'));
  const [itemSort, setItemSort] = useState(searchParams.get('sort') ?? 'name');
  const [itemPage, setItemPage] = useState(Number(searchParams.get('page') ?? '1') || 1);
  const [bundles, setBundles] = useState<KumihoItem[]>([]);
  const [selectedBundleKref, setSelectedBundleKref] = useState<string | null>(searchParams.get('bundle'));
  const [bundleMembers, setBundleMembers] = useState<KumihoBundleMemberDetail[]>([]);
  const [loadingBundles, setLoadingBundles] = useState(false);
  const [loadingBundleMembers, setLoadingBundleMembers] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [reloading, setReloading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingContent, setLoadingContent] = useState(false);
  const [loadingRevisions, setLoadingRevisions] = useState(false);
  const [loadingRevisionDetail, setLoadingRevisionDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; message: string } | null>(null);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [editingArtifact, setEditingArtifact] = useState<KumihoArtifact | null>(null);
  const [artifactDraft, setArtifactDraft] = useState('');
  const [artifactDraftLoading, setArtifactDraftLoading] = useState(false);
  const [createMenuOpen, setCreateMenuOpen] = useState(false);
  const [createAction, setCreateAction] = useState<CreateAction | null>(null);
  const [graphOpen, setGraphOpen] = useState(false);
  const [expandedTreeNodes, setExpandedTreeNodes] = useState<string[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem('revka.assetBrowser.expanded') ?? '[]');
    } catch {
      return [];
    }
  });
  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dropdownRef = useRef<HTMLDivElement | null>(null);
  const createMenuRef = useRef<HTMLDivElement | null>(null);
  const currentSpacePath = currentPath[currentPath.length - 1]?.path ?? null;

  /* ---- effects ---- */

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setProjectDropdownOpen(false);
      }
      if (createMenuRef.current && !createMenuRef.current.contains(event.target as Node)) {
        setCreateMenuOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, []);

  useEffect(() => {
    setLoading(true);
    kumihoProxy<KumihoProject[]>('/projects')
      .then((data) => {
        const names = data.map((project) => project.name);
        setProjects(data);
        setSelectedProject((current) => {
          if (current && names.includes(current)) return current;
          if (initialProject && names.includes(initialProject)) return initialProject;
          return names[0] ?? null;
        });
        setError(null);
      })
      .catch((err) => {
        console.error('[Assets] Failed to load projects:', err);
        setError(t('assets.err.load'));
      })
      .finally(() => setLoading(false));
  }, [initialProject, reloadNonce, t]);

  useEffect(() => {
    return () => {
      if (searchTimeout.current) clearTimeout(searchTimeout.current);
    };
  }, []);

  useEffect(() => {
    const parsed = parseKref(searchParams.get('kref'));
    const nextProject = searchParams.get('project') ?? parsed?.project ?? selectedProject;
    const nextSpace = searchParams.get('space') ?? parsed?.spacePath;
    const nextItem = searchParams.get('item') ?? parsed?.itemKref ?? null;
    const nextRevision = searchParams.get('revision') ?? parsed?.revisionKref ?? null;
    if (nextProject && nextProject !== selectedProject) {
      setSelectedProject(nextProject);
    }
    if (nextSpace && nextSpace !== currentSpacePath) {
      setCurrentPath(pathSegmentsFromSpacePath(nextSpace));
    }
    if (nextItem && nextItem !== requestedItemKref) {
      setRequestedItemKref(nextItem);
    }
    if (nextRevision && nextRevision !== requestedRevisionKref) {
      setRequestedRevisionKref(nextRevision);
    }
    const nextBundle = searchParams.get('bundle');
    if (nextBundle !== selectedBundleKref) setSelectedBundleKref(nextBundle);
    const nextTab = searchParams.get('tab') === 'bundles' ? 'bundles' : 'items';
    if (nextTab !== activeTab) setActiveTab(nextTab);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  useEffect(() => {
    sessionStorage.setItem('revka.assetBrowser.expanded', JSON.stringify(expandedTreeNodes));
  }, [expandedTreeNodes]);

  useEffect(() => {
    if (!selectedProject) return;
    if (currentPath.length === 0 || currentPath[0]?.name !== selectedProject) {
      setCurrentPath(pathSegmentsFromSpacePath(`/${selectedProject}`));
    }
  }, [currentPath, selectedProject]);

  const showNotice = useCallback((tone: 'success' | 'error', message: string) => {
    setNotice({ tone, message });
    setTimeout(() => {
      setNotice((current) => (current?.message === message ? null : current));
    }, 4500);
  }, []);

  useEffect(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete('kref');
      if (selectedProject) next.set('project', selectedProject); else next.delete('project');
      if (currentSpacePath) next.set('space', currentSpacePath); else next.delete('space');
      if (selectedItem?.kref) next.set('item', selectedItem.kref); else next.delete('item');
      if (selectedRevision?.kref) next.set('revision', selectedRevision.kref); else next.delete('revision');
      if (activeTab !== 'items') next.set('tab', activeTab); else next.delete('tab');
      if (searchQuery.trim()) next.set('q', searchQuery); else next.delete('q');
      if (selectedBundleKref) next.set('bundle', selectedBundleKref); else next.delete('bundle');
      if (itemSort !== 'name') next.set('sort', itemSort); else next.delete('sort');
      if (itemPage > 1) next.set('page', String(itemPage)); else next.delete('page');
      return next;
    }, { replace: true });
  }, [
    activeTab,
    currentSpacePath,
    itemPage,
    itemSort,
    searchQuery,
    selectedBundleKref,
    selectedItem?.kref,
    selectedProject,
    selectedRevision?.kref,
    setSearchParams,
  ]);

  const refreshRevisionDetail = useCallback(async (
    revision: KumihoRevision,
    preferredArtifactKref?: string,
  ) => {
    setLoadingRevisionDetail(true);
    try {
      const [nextArtifacts, nextEdges] = await Promise.all([
        kumihoProxy<KumihoArtifact[]>('/artifacts', { revision_kref: revision.kref }).catch(() => []),
        kumihoProxy<KumihoEdge[]>('/edges', { kref: revision.kref, direction: 'both' }).catch(() => []),
      ]);
      setArtifacts(nextArtifacts);
      setSelectedArtifact(
        nextArtifacts.find((artifact) => artifact.kref === preferredArtifactKref) ?? nextArtifacts[0] ?? null,
      );
      setEdges(nextEdges);
    } finally {
      setLoadingRevisionDetail(false);
    }
  }, []);

  useEffect(() => {
    if (!currentSpacePath) return;
    setLoadingContent(true);

    Promise.all([
      kumihoProxy<KumihoSpace[]>('/spaces', { parent_path: currentSpacePath, recursive: false }).catch(() => []),
      kumihoProxy<KumihoItem[]>('/items', { space_path: currentSpacePath }).catch(() => []),
    ])
      .then(([nextSpaces, nextItems]) => {
        setChildSpaces(nextSpaces);
        setItems(nextItems);
        const requested = requestedItemKref
          ? nextItems.find((item) => item.kref === requestedItemKref)
          : null;
        if (requested) {
          setSelectedItem(requested);
        } else if (!requestedItemKref && selectedItem && !nextItems.some((item) => item.kref === selectedItem.kref)) {
          setSelectedItem(null);
          setRevisions([]);
          setSelectedRevision(null);
          setArtifacts([]);
          setSelectedArtifact(null);
          setEdges([]);
        }
      })
      .catch(() => {
        setChildSpaces([]);
        setItems([]);
        showNotice('error', 'Failed to reload the current Asset Browser view.');
      })
      .finally(() => setLoadingContent(false));
  }, [currentSpacePath, reloadNonce, requestedItemKref, selectedItem?.kref, showNotice]);

  useEffect(() => {
    if (!selectedItem) {
      setRevisions([]);
      setSelectedRevision(null);
      setArtifacts([]);
      setSelectedArtifact(null);
      setEdges([]);
      return;
    }

    setLoadingRevisions(true);
    setSelectedRevision(null);
    setArtifacts([]);
    setSelectedArtifact(null);
    setEdges([]);

    kumihoProxy<KumihoRevision[]>('/revisions', { item_kref: selectedItem.kref })
      .then(async (data) => {
        let sorted = [...data].sort((a, b) => b.number - a.number);
        let requested = requestedRevisionKref
          ? sorted.find((revision) => revision.kref === requestedRevisionKref)
          : null;
        if (!requested && requestedRevisionKref) {
          try {
            const resolved = await kumihoProxy<KumihoRevision>('/revisions/by-kref', {
              kref: requestedRevisionKref,
            });
            if (resolved.item_kref === selectedItem.kref) {
              sorted = [resolved, ...sorted.filter((revision) => revision.kref !== resolved.kref)]
                .sort((a, b) => b.number - a.number);
              requested = resolved;
            }
          } catch {
            // Fall back to latest if the requested tag selector cannot be resolved.
          }
        }
        setRevisions(sorted);
        setSelectedRevision(requested ?? sorted[0] ?? null);
      })
      .catch(() => setRevisions([]))
      .finally(() => setLoadingRevisions(false));
  }, [reloadNonce, requestedRevisionKref, selectedItem?.kref]);

  useEffect(() => {
    if (!selectedRevision) {
      setArtifacts([]);
      setSelectedArtifact(null);
      setEdges([]);
      return;
    }

    setSelectedArtifact(null);

    refreshRevisionDetail(selectedRevision).catch(() => {
      setArtifacts([]);
      setSelectedArtifact(null);
      setEdges([]);
      setLoadingRevisionDetail(false);
    });
  }, [refreshRevisionDetail, reloadNonce, selectedRevision?.kref]);

  useEffect(() => {
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    if (!searchQuery.trim()) {
      setSearchResults([]);
      setSearching(false);
      return;
    }

    setSearching(true);
    searchTimeout.current = setTimeout(async () => {
      try {
        const results = await kumihoProxy<KumihoSearchResult[]>('/items/fulltext-search', {
          query: searchQuery,
          context: selectedProject ?? undefined,
          include_revision_metadata: true,
        });
        setSearchResults(results);
      } catch (err) {
        console.error('[Assets] Search failed:', err);
        setSearchResults([]);
      } finally {
        setSearching(false);
      }
    }, 250);
  }, [reloadNonce, searchQuery, selectedProject]);

  useEffect(() => {
    if (!selectedProject) {
      setBundles([]);
      setSelectedBundleKref(null);
      setBundleMembers([]);
      return;
    }
    setLoadingBundles(true);
    fetchAssetBundles(selectedProject, currentSpacePath ?? `/${selectedProject}`)
      .then((nextBundles) => {
        setBundles(nextBundles);
        setSelectedBundleKref((current) => {
          if (current && nextBundles.some((bundle) => bundle.kref === current)) return current;
          return null;
        });
      })
      .catch((err) => {
        console.error('[Assets] Failed to load bundles:', err);
        setBundles([]);
        setSelectedBundleKref(null);
      })
      .finally(() => setLoadingBundles(false));
  }, [currentSpacePath, reloadNonce, selectedProject]);

  useEffect(() => {
    if (!selectedBundleKref) {
      setBundleMembers([]);
      return;
    }
    setLoadingBundleMembers(true);
    fetchAssetBundleMembers(selectedBundleKref)
      .then((response) => setBundleMembers(response.members))
      .catch((err) => {
        console.error('[Assets] Failed to load bundle members:', err);
        setBundleMembers([]);
      })
      .finally(() => setLoadingBundleMembers(false));
  }, [reloadNonce, selectedBundleKref]);

  /* ---- callbacks ---- */

  const navigateToSpace = useCallback((space: KumihoSpace) => {
    setExpandedTreeNodes((prev) => (prev.includes(currentSpacePath ?? '') ? prev : [...prev, currentSpacePath ?? ''].filter(Boolean)));
    setCurrentPath((prev) => [...prev, { name: space.name, path: space.path }]);
    setRequestedItemKref(null);
    setRequestedRevisionKref(null);
    setSelectedItem(null);
    setSelectedRevision(null);
    setSearchQuery('');
    setSearchResults([]);
    setSearching(false);
    setSelectedBundleKref(null);
    setBundleMembers([]);
  }, [currentSpacePath]);

  const navigateToBreadcrumb = useCallback((index: number) => {
    setCurrentPath((prev) => prev.slice(0, index + 1));
    setRequestedItemKref(null);
    setRequestedRevisionKref(null);
    setSelectedItem(null);
    setSelectedRevision(null);
    setSearchQuery('');
    setSearchResults([]);
    setSearching(false);
    setSelectedBundleKref(null);
    setBundleMembers([]);
  }, []);

  const handleSearchChange = useCallback((query: string) => {
    setSearchQuery(query);
    setItemPage(1);
  }, []);

  const handleProjectSelect = useCallback((project: string) => {
    if (project === selectedProject) {
      setProjectDropdownOpen(false);
      return;
    }
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    setProjectDropdownOpen(false);
    setSelectedProject(project);
    setCurrentPath(pathSegmentsFromSpacePath(`/${project}`));
    setRequestedItemKref(null);
    setRequestedRevisionKref(null);
    setSelectedItem(null);
    setSelectedRevision(null);
    setArtifacts([]);
    setSelectedArtifact(null);
    setEdges([]);
    setSearchQuery('');
    setSearchResults([]);
    setSelectedBundleKref(null);
    setActiveTab('items');
  }, [selectedProject]);

  const handleNavigateUp = useCallback(() => {
    setCurrentPath((prev) => (prev.length <= 1 ? prev : prev.slice(0, -1)));
    setRequestedItemKref(null);
    setRequestedRevisionKref(null);
    setSelectedItem(null);
    setSelectedRevision(null);
    setSearchQuery('');
    setSearchResults([]);
    setSearching(false);
    setSelectedBundleKref(null);
    setBundleMembers([]);
  }, []);

  const handleSelectItem = useCallback((item: KumihoItem) => {
    const parsed = parseKref(item.kref);
    if (parsed) {
      setSelectedProject(parsed.project);
      setCurrentPath(pathSegmentsFromSpacePath(parsed.spacePath));
    }
    setSelectedItem(item);
    setRequestedItemKref(item.kref);
    setRequestedRevisionKref(null);
    setActiveTab('items');
  }, []);

  const handleSelectRevision = useCallback((revision: KumihoRevision) => {
    setSelectedRevision(revision);
    setRequestedRevisionKref(revision.kref);
  }, []);

  const handleOpenKref = useCallback(async (kref: string) => {
    const parsed = parseKref(kref);
    if (!parsed) {
      showNotice('error', 'Invalid kref.');
      return;
    }
    setSelectedProject(parsed.project);
    setCurrentPath(pathSegmentsFromSpacePath(parsed.spacePath));
    setRequestedItemKref(parsed.itemKref);
    setRequestedRevisionKref(parsed.revisionKref);
    setActiveTab('items');
  }, [showNotice]);

  const handleReloadCurrentView = useCallback(() => {
    setReloading(true);
    setReloadNonce((value) => value + 1);
    window.setTimeout(() => setReloading(false), 700);
  }, []);

  const mergeItem = useCallback((updated: KumihoItem) => {
    setItems((prev) => prev.map((item) => (item.kref === updated.kref ? updated : item)));
    setSearchResults((prev) => prev.map((result) => (
      result.item.kref === updated.kref ? { ...result, item: updated } : result
    )));
    setSelectedItem((current) => (current?.kref === updated.kref ? updated : current));
  }, []);

  const mergeRevision = useCallback((updated: KumihoRevision, publishedExclusive = false) => {
    const normalized = {
      ...updated,
      published: revisionIsPublished(updated),
      tags: revisionIsPublished(updated) && !updated.tags.includes('published')
        ? [...updated.tags, 'published']
        : updated.tags,
    };
    setRevisions((prev) => prev.map((revision) => {
      if (revision.kref === normalized.kref) return normalized;
      if (!publishedExclusive) return revision;
      return {
        ...revision,
        published: false,
        tags: revision.tags.filter((tag) => tag !== 'published'),
      };
    }));
    setSelectedRevision((current) => (current?.kref === normalized.kref ? normalized : current));
  }, []);

  const mergeArtifact = useCallback((updated: KumihoArtifact) => {
    setArtifacts((prev) => prev.map((artifact) => (artifact.kref === updated.kref ? updated : artifact)));
    setSelectedArtifact((current) => (current?.kref === updated.kref ? updated : current));
  }, []);

  const handleToggleItemDeprecation = useCallback(async () => {
    if (!selectedItem) return;
    const next = !selectedItem.deprecated;
    setActionBusy('item-deprecate');
    try {
      const updated = await toggleAssetItemDeprecation(selectedItem.kref, next);
      mergeItem(updated);
      showNotice('success', next ? t('assets.toast.item_deprecated') : t('assets.toast.item_restored'));
    } catch (err) {
      showNotice('error', err instanceof Error ? err.message : t('assets.err.action'));
    } finally {
      setActionBusy(null);
    }
  }, [mergeItem, selectedItem, showNotice, t]);

  const handleToggleRevisionDeprecation = useCallback(async () => {
    if (!selectedRevision) return;
    const next = !selectedRevision.deprecated;
    setActionBusy('revision-deprecate');
    try {
      const updated = await toggleAssetRevisionDeprecation(selectedRevision.kref, next);
      mergeRevision(updated);
      showNotice('success', next ? t('assets.toast.revision_deprecated') : t('assets.toast.revision_restored'));
    } catch (err) {
      showNotice('error', err instanceof Error ? err.message : t('assets.err.action'));
    } finally {
      setActionBusy(null);
    }
  }, [mergeRevision, selectedRevision, showNotice, t]);

  const handlePublishRevision = useCallback(async () => {
    if (!selectedRevision) return;
    setActionBusy('revision-publish');
    try {
      const updated = await publishAssetRevision(selectedRevision.kref);
      mergeRevision(updated, true);
      showNotice('success', t('assets.toast.revision_published'));
    } catch (err) {
      showNotice('error', err instanceof Error ? err.message : t('assets.err.action'));
    } finally {
      setActionBusy(null);
    }
  }, [mergeRevision, selectedRevision, showNotice, t]);

  const handleToggleArtifactDeprecation = useCallback(async (artifact: KumihoArtifact) => {
    const next = !artifact.deprecated;
    setActionBusy(`artifact-deprecate:${artifact.kref}`);
    try {
      const updated = await toggleAssetArtifactDeprecation(artifact.kref, next);
      mergeArtifact(updated);
      showNotice('success', next ? t('assets.toast.artifact_deprecated') : t('assets.toast.artifact_restored'));
    } catch (err) {
      showNotice('error', err instanceof Error ? err.message : t('assets.err.action'));
    } finally {
      setActionBusy(null);
    }
  }, [mergeArtifact, showNotice, t]);

  const handleOpenArtifactEditor = useCallback(async (artifact: KumihoArtifact) => {
    setEditingArtifact(artifact);
    setArtifactDraft('');
    setArtifactDraftLoading(true);
    try {
      const text = await fetchArtifactBodyText(artifact.location);
      setArtifactDraft(text);
    } catch (err) {
      showNotice('error', err instanceof Error ? err.message : t('assets.err.load_artifact'));
      setEditingArtifact(null);
    } finally {
      setArtifactDraftLoading(false);
    }
  }, [showNotice, t]);

  const handleSaveArtifactDraft = useCallback(async () => {
    if (!editingArtifact || !selectedRevision) return;
    setActionBusy('artifact-save');
    try {
      const result = await updateAssetArtifactContent(
        editingArtifact.kref,
        selectedRevision.kref,
        artifactDraft,
      );
      if (result.created_revision) {
        const nextRevision = {
          ...result.revision,
          latest: true,
          published: revisionIsPublished(result.revision),
        };
        setRevisions((prev) => [
          nextRevision,
          ...prev
            .filter((revision) => revision.kref !== nextRevision.kref)
            .map((revision) => ({
              ...revision,
              latest: false,
              tags: revision.tags.filter((tag) => tag !== 'latest'),
            })),
        ].sort((a, b) => b.number - a.number));
        setSelectedRevision(nextRevision);
        await refreshRevisionDetail(nextRevision, result.artifact.kref);
        showNotice('success', t('assets.toast.artifact_saved_new_revision'));
      } else {
        await refreshRevisionDetail(selectedRevision, editingArtifact.kref);
        showNotice('success', t('assets.toast.artifact_saved'));
      }
      setEditingArtifact(null);
    } catch (err) {
      showNotice('error', err instanceof Error ? err.message : t('assets.err.action'));
    } finally {
      setActionBusy(null);
    }
  }, [artifactDraft, editingArtifact, refreshRevisionDetail, selectedRevision, showNotice, t]);

  /* ---- derived ---- */

  const isSearchActive = searchQuery.trim().length > 0;
  const sortedItems = useMemo(() => {
    const source = isSearchActive ? searchResults.map((result) => result.item) : items;
    return [...source].sort((a, b) => {
      if (itemSort === 'kind') return a.kind.localeCompare(b.kind) || itemDisplayName(a).localeCompare(itemDisplayName(b));
      if (itemSort === 'created') return String(b.created_at ?? '').localeCompare(String(a.created_at ?? ''));
      return itemDisplayName(a).localeCompare(itemDisplayName(b));
    });
  }, [isSearchActive, itemSort, items, searchResults]);
  const pageSize = 75;
  const pageCount = Math.max(1, Math.ceil(sortedItems.length / pageSize));
  const safePage = Math.min(Math.max(1, itemPage), pageCount);
  const visibleItems = sortedItems.slice((safePage - 1) * pageSize, safePage * pageSize);
  const selectedBundle = bundles.find((bundle) => bundle.kref === selectedBundleKref) ?? null;
  const selectedBundleProtected = selectedBundle ? isProtectedBundleName(bundleNameFromKref(selectedBundle.kref)) : false;
  const metadataEntries = Object.entries(
    selectedArtifact?.metadata ?? selectedRevision?.metadata ?? selectedItem?.metadata ?? {},
  );

  /* ---- render ---- */

  return (
    <>
    <div className="flex h-[calc(100vh-6rem)] flex-col gap-3">
      {/* Row 1 — Header + search */}
      <div className="flex items-start justify-between gap-4">
        <PageHeader kicker={t('assets.kicker')} title={t('assets.title')} />
        <div className="relative min-w-[14rem] max-w-[22rem] flex-1">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            style={{ color: 'var(--revka-text-faint)' }}
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder={t('assets.search_placeholder')}
            className="revka-input pl-10 pr-10"
          />
          {searching ? (
            <div
              className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin rounded-full border-2"
              style={{ borderColor: 'var(--revka-border-soft)', borderTopColor: 'var(--revka-signal-network)' }}
            />
          ) : null}
        </div>
      </div>

      {/* Row 2 — Toolbar: project selector + breadcrumb + up */}
      <div className="flex items-center gap-3">
        <div className="relative" ref={dropdownRef}>
          <button
            type="button"
            className="revka-button justify-between gap-2"
            onClick={() => setProjectDropdownOpen((prev) => !prev)}
          >
            <Database className="h-4 w-4" />
            <span className="truncate">{selectedProject ?? t('assets.project')}</span>
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          {projectDropdownOpen ? (
            <div
              className="absolute left-0 top-full z-20 mt-2 min-w-[14rem] rounded-[14px] border p-2"
              style={{ borderColor: 'var(--revka-border-soft)', background: 'var(--revka-bg-panel-strong)' }}
            >
              {projects.map((project) => (
                <button
                  key={project.name}
                  type="button"
                  className="w-full rounded-[10px] px-3 py-2 text-left text-sm transition"
                  onClick={() => handleProjectSelect(project.name)}
                  style={{
                    color: project.name === selectedProject ? 'var(--revka-text-primary)' : 'var(--revka-text-secondary)',
                    background: project.name === selectedProject ? 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 18%, transparent))' : 'transparent',
                  }}
                >
                  {project.name}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <div className="flex min-w-0 flex-1 items-center gap-1 text-sm">
          {currentPath.map((segment, index) => (
            <span key={segment.path} className="inline-flex items-center gap-1">
              {index > 0 && (
                <ChevronRight className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--revka-text-faint)' }} />
              )}
              <button
                type="button"
                onClick={() => navigateToBreadcrumb(index)}
                className="rounded px-1.5 py-0.5 hover:underline"
                style={{
                  color: index === currentPath.length - 1 ? 'var(--revka-text-primary)' : 'var(--revka-text-secondary)',
                }}
              >
                {segment.name}
              </button>
            </span>
          ))}
        </div>

        <button
          type="button"
          className="revka-button"
          onClick={handleReloadCurrentView}
          disabled={reloading}
          title="Reload current Asset Browser view"
        >
          {reloading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCcw className="h-4 w-4" />}
          Reload
        </button>

        <div className="relative" ref={createMenuRef}>
          <button
            type="button"
            className="revka-button revka-button-primary"
            onClick={() => setCreateMenuOpen((prev) => !prev)}
          >
            <Plus className="h-4 w-4" />
            Create
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          {createMenuOpen ? (
            <div
              className="absolute right-0 top-full z-20 mt-2 w-72 rounded-[14px] border p-2"
              style={{ borderColor: 'var(--revka-border-soft)', background: 'var(--revka-bg-panel-strong)' }}
            >
              {[
                ['project', 'Create Project'],
                ['space', 'Create Space'],
                ['subspace', 'Create Subspace'],
                ['item', 'Create Item in this Space'],
                ['bundle', 'Create Bundle'],
                ['revision', 'Create Revision'],
                ['artifact', 'Attach or Link Artifact'],
                ['edge', 'Create Edge from Revision'],
                ['tag', 'Tag Revision'],
                ['bundle-member-add', 'Add Item to Bundle'],
                ['bundle-member-remove', 'Remove Bundle Member'],
                ['context-pack', 'Create Context Pack from Bundle'],
              ].map(([action, label]) => (
                <button
                  key={action}
                  type="button"
                  className="flex w-full items-center gap-2 rounded-[10px] px-3 py-2 text-left text-sm transition hover:brightness-125"
                  onClick={() => {
                    setCreateAction(action as CreateAction);
                    setCreateMenuOpen(false);
                  }}
                  style={{ color: 'var(--revka-text-secondary)' }}
                >
                  <Plus className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>
          ) : null}
        </div>

        <button
          type="button"
          className="revka-button"
          onClick={handleNavigateUp}
          disabled={currentPath.length <= 1}
        >
          <ChevronLeft className="h-4 w-4" />
          {t('assets.up')}
        </button>
      </div>

      {/* Error banner */}
      {error ? (
        <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--revka-status-danger)' }}>
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      ) : null}
      {notice ? (
        <div
          className="flex items-center gap-2 rounded-[8px] border px-3 py-2 text-sm"
          style={{
            borderColor: notice.tone === 'success' ? 'color-mix(in srgb, var(--revka-status-success) 24%, transparent)' : 'color-mix(in srgb, var(--revka-status-danger) 24%, transparent)',
            color: notice.tone === 'success' ? 'var(--revka-status-success)' : 'var(--revka-status-danger)',
            background: notice.tone === 'success' ? 'color-mix(in srgb, var(--revka-status-success) 8%, transparent)' : 'color-mix(in srgb, var(--revka-status-danger) 8%, transparent)',
          }}
        >
          {notice.tone === 'success' ? <Check className="h-4 w-4 shrink-0" /> : <AlertTriangle className="h-4 w-4 shrink-0" />}
          {notice.message}
        </div>
      ) : null}

      {/* Row 3 — Master-detail split */}
      <div
        className="grid min-h-0 flex-1 gap-4"
        style={{
          gridTemplateColumns: selectedItem ? 'minmax(0,1fr) 32rem' : '1fr',
        }}
      >
        {/* ---- LEFT: Item table ---- */}
        <Panel className="flex flex-col overflow-hidden p-0">
          <div className="flex shrink-0 items-center justify-between gap-3 border-b px-4 py-3" style={{ borderColor: 'var(--revka-border-soft)' }}>
            <div className="inline-flex rounded-[8px] border p-0.5" style={{ borderColor: 'var(--revka-border-soft)', background: 'var(--revka-bg-elevated)' }}>
              {(['items', 'bundles'] as AssetTab[]).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className="rounded-[6px] px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.08em]"
                  onClick={() => setActiveTab(tab)}
                  style={{
                    background: activeTab === tab ? 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 18%, transparent))' : 'transparent',
                    color: activeTab === tab ? 'var(--revka-text-primary)' : 'var(--revka-text-faint)',
                  }}
                >
                  {tab === 'items' ? 'Items' : 'Bundles'}
                </button>
              ))}
            </div>
            {activeTab === 'items' ? (
              <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                <span>Sort</span>
                <select className="revka-input h-8 py-0 text-xs" value={itemSort} onChange={(event) => setItemSort(event.target.value)}>
                  <option value="name">Name</option>
                  <option value="kind">Kind</option>
                  <option value="created">Created</option>
                </select>
              </div>
            ) : (
              <div className="text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                {bundles.length} bundles in space
              </div>
            )}
          </div>
          {/* Table header. On narrow viewports the only meaningful header
              is NAME (kind chips and timestamps are self-describing inline),
              so we hide the rest at mobile widths to avoid the squashed
              "NAKINMED AUTHOR CREAT…" overlap from fixed-width labels. */}
          {activeTab === 'items' ? <div
            className="revka-assets-row shrink-0 border-b px-4 py-2.5"
            style={{
              borderColor: 'var(--revka-border-soft)',
              color: 'var(--revka-text-faint)',
            }}
          >
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em]">{t('assets.col.name')}</span>
            <span className="revka-assets-kind hidden text-[11px] font-semibold uppercase tracking-[0.14em] md:inline">{t('assets.col.kind')}</span>
            <span className="revka-assets-author hidden text-[11px] font-semibold uppercase tracking-[0.14em] md:inline">{t('assets.col.author')}</span>
            <span className="revka-assets-created hidden text-right text-[11px] font-semibold uppercase tracking-[0.14em] md:inline">{t('assets.col.created')}</span>
          </div> : null}

          {/* Table body */}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {activeTab === 'bundles' ? (
              <BundleBrowser
                bundles={bundles}
                members={bundleMembers}
                childSpaces={childSpaces}
                currentSpacePath={currentSpacePath}
                selectedBundleKref={selectedBundleKref}
                selectedBundleProtected={selectedBundleProtected}
                loadingBundles={loadingBundles}
                loadingMembers={loadingBundleMembers}
                onNavigateSpace={navigateToSpace}
                onSelectBundle={(bundle) => setSelectedBundleKref(bundle.kref)}
                onOpenMember={(member) => {
                  if (member.item) void handleOpenKref(member.item.kref);
                }}
                onCopyKref={(kref) => copyToClipboard(kref)}
              />
            ) : loading || loadingContent ? (
              <div className="p-4">
                <StateMessage
                  tone="loading"
                  compact
                  title={loading ? t('assets.loading.projects') : t('assets.loading.space')}
                />
              </div>
            ) : (
              <>
                {/* Folder rows */}
                {!isSearchActive &&
                  childSpaces.map((space) => (
                    <button
                      key={space.path}
                      type="button"
                      onClick={() => navigateToSpace(space)}
                      className="revka-assets-row w-full border-b px-4 py-2.5 text-left transition hover:brightness-125"
                      style={{
                        borderColor: 'var(--revka-border-soft)',
                        background: 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
                      }}
                    >
                      <div className="flex min-w-0 items-center gap-2.5">
                        <FolderOpen className="h-4 w-4 shrink-0" style={{ color: '#fbbf24' }} />
                        <span
                          className="truncate text-sm font-medium"
                          style={{ color: 'var(--revka-text-primary)' }}
                        >
                          {space.name}
                        </span>
                      </div>
                      <span className="revka-assets-kind text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                        {t('assets.folder')}
                      </span>
                      <span className="revka-assets-author truncate text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                        --
                      </span>
                      <span className="revka-assets-created text-right text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                        {formatTime(space.created_at)}
                      </span>
                    </button>
                  ))}

                {/* Search status */}
                {isSearchActive && searchResults.length > 0 ? (
                  <div
                    className="border-b px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.12em]"
                    style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}
                  >
                    {tpl('assets.search.result_count', { count: searchResults.length, query: searchQuery })}
                  </div>
                ) : null}

                {/* Item rows */}
                {visibleItems.map((item) => {
                  const meta = getKindMeta(item.kind);
                  const Icon = meta.icon;
                  const isActive = selectedItem?.kref === item.kref;
                  return (
                    <button
                      key={item.kref}
                      type="button"
                      onClick={() => handleSelectItem(item)}
                      className="revka-assets-row w-full border-b px-4 py-2.5 text-left transition"
                      style={{
                        borderColor: 'var(--revka-border-soft)',
                        background: isActive
                          ? 'color-mix(in srgb, var(--revka-signal-selected) 14%, var(--revka-bg-panel))'
                          : 'transparent',
                        opacity: item.deprecated ? 0.6 : 1,
                      }}
                    >
                      <div className="flex min-w-0 items-center gap-2.5">
                        <Icon className="h-4 w-4 shrink-0" style={{ color: meta.color }} />
                        <span
                          className="truncate text-sm font-medium"
                          style={{ color: 'var(--revka-text-primary)' }}
                        >
                          {item.item_name || item.name}
                        </span>
                      </div>
                      <span
                        className="revka-assets-kind inline-flex items-center justify-center rounded-full px-2 py-0.5 text-[10px] font-semibold capitalize"
                        style={{
                          background: meta.bg,
                          color: meta.color,
                          border: `1px solid ${meta.border}`,
                        }}
                      >
                        {item.kind}
                      </span>
                      <span
                        className="revka-assets-author truncate text-xs font-mono"
                        style={{ color: 'var(--revka-text-faint)' }}
                      >
                        {readableAuthor(item)}
                      </span>
                      <span className="revka-assets-created text-right text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                        {formatTime(item.created_at)}
                      </span>
                    </button>
                  );
                })}

                {sortedItems.length > pageSize ? (
                  <div className="flex items-center justify-between border-b px-4 py-2 text-xs" style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}>
                    <span>
                      Page {safePage} / {pageCount}
                    </span>
                    <div className="flex items-center gap-2">
                      <button type="button" className="revka-button h-8 px-2 text-xs" disabled={safePage <= 1} onClick={() => setItemPage((page) => Math.max(1, page - 1))}>
                        <ChevronLeft className="h-3.5 w-3.5" />
                        Prev
                      </button>
                      <button type="button" className="revka-button h-8 px-2 text-xs" disabled={safePage >= pageCount} onClick={() => setItemPage((page) => Math.min(pageCount, page + 1))}>
                        Next
                        <ChevronRight className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                ) : null}

                {/* Empty states */}
                {!isSearchActive && childSpaces.length === 0 && items.length === 0 ? (
                  <div className="p-4">
                    <StateMessage
                      compact
                      title={t('assets.empty.title')}
                      description={t('assets.empty.desc')}
                    />
                  </div>
                ) : null}
                {isSearchActive && searchResults.length === 0 && !searching ? (
                  <div className="p-4">
                    <StateMessage
                      compact
                      title={t('assets.search.empty_title')}
                      description={tpl('assets.search.empty_desc', { query: searchQuery })}
                    />
                  </div>
                ) : null}
              </>
            )}
          </div>
        </Panel>

        {/* ---- RIGHT: Inspector panel ---- */}
        {selectedItem ? (
          <div className="min-h-0 overflow-y-auto">
            <Panel className="p-4">
              {/* Header */}
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3
                    className="text-base font-semibold"
                    style={{ color: 'var(--revka-text-primary)' }}
                  >
                    {selectedItem.item_name || selectedItem.name}
                  </h3>
                  <div className="mt-1.5">
                    <CopyableKref kref={selectedItem.kref} />
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedItem(null);
                    setRequestedItemKref(null);
                    setRequestedRevisionKref(null);
                  }}
                  className="shrink-0 rounded-[10px] p-1.5 transition"
                  style={{ color: 'var(--revka-text-faint)' }}
                  title={t('assets.close_inspector')}
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              {/* Badges */}
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {(() => {
                  const meta = getKindMeta(selectedItem.kind);
                  return (
                    <span
                      className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-semibold capitalize"
                      style={{
                        background: meta.bg,
                        color: meta.color,
                        border: `1px solid ${meta.border}`,
                      }}
                    >
                      {selectedItem.kind}
                    </span>
                  );
                })()}
                <span
                  className="inline-flex items-center rounded-full px-2.5 py-1 text-[11px] font-semibold"
                  style={{
                    background: selectedItem.deprecated
                      ? 'rgba(245,158,11,0.12)'
                      : 'rgba(125,255,155,0.12)',
                    color: selectedItem.deprecated
                      ? 'var(--revka-status-warning)'
                      : 'var(--revka-status-success)',
                  }}
                >
                  {selectedItem.deprecated ? t('assets.deprecated') : t('assets.active')}
                </span>
                <span className="text-xs" style={{ color: 'var(--revka-text-faint)' }}>
                  {tpl(revisions.length === 1 ? 'assets.rev_count_one' : 'assets.rev_count', { count: revisions.length })}
                </span>
                <button
                  type="button"
                  className="revka-button ml-auto h-8 px-2 text-xs"
                  onClick={handleToggleItemDeprecation}
                  disabled={actionBusy === 'item-deprecate'}
                  title={selectedItem.deprecated ? t('assets.action.restore_item') : t('assets.action.deprecate_item')}
                >
                  {actionBusy === 'item-deprecate' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Ban className="h-3.5 w-3.5" />}
                  {selectedItem.deprecated ? t('assets.action.restore') : t('assets.action.deprecate')}
                </button>
              </div>

              {/* Author & date */}
              <div
                className="mt-3 flex items-center justify-between gap-2 text-xs"
                style={{ color: 'var(--revka-text-secondary)' }}
              >
                <span className="truncate font-mono">
                  {tpl('assets.by', { author: readableAuthor(selectedItem) })}
                </span>
                <span className="shrink-0">{formatDate(selectedItem.created_at)}</span>
              </div>

              {/* Sections */}
              <div className="mt-4">
                {/* REVISIONS */}
                <CollapsibleSection title={t('assets.section.revisions')} count={revisions.length} defaultOpen>
                  {loadingRevisions ? (
                    <StateMessage tone="loading" compact title={t('assets.loading.generic')} />
                  ) : revisions.length === 0 ? (
                    <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>
                      {t('assets.section.revisions_empty')}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {selectedRevision ? (
                        <div className="flex flex-wrap gap-2 rounded-[10px] border p-2" style={{ borderColor: 'var(--revka-border-soft)' }}>
                          <button
                            type="button"
                            className="revka-button h-8 px-2 text-xs"
                            onClick={handlePublishRevision}
                            disabled={revisionIsPublished(selectedRevision) || actionBusy === 'revision-publish'}
                            title={t('assets.action.publish_revision')}
                          >
                            {actionBusy === 'revision-publish' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Tag className="h-3.5 w-3.5" />}
                            {revisionIsPublished(selectedRevision) ? t('assets.action.published') : t('assets.action.publish')}
                          </button>
                          <button
                            type="button"
                            className="revka-button h-8 px-2 text-xs"
                            onClick={handleToggleRevisionDeprecation}
                            disabled={actionBusy === 'revision-deprecate'}
                            title={selectedRevision.deprecated ? t('assets.action.restore_revision') : t('assets.action.deprecate_revision')}
                          >
                            {actionBusy === 'revision-deprecate' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Ban className="h-3.5 w-3.5" />}
                            {selectedRevision.deprecated ? t('assets.action.restore') : t('assets.action.deprecate')}
                          </button>
                          <button
                            type="button"
                            className="revka-button h-8 px-2 text-xs"
                            onClick={() => setCreateAction('tag')}
                            title="Add or remove non-current revision tags"
                          >
                            <Tag className="h-3.5 w-3.5" />
                            Tags
                          </button>
                          <button
                            type="button"
                            className="revka-button h-8 px-2 text-xs"
                            onClick={() => setCreateAction('artifact')}
                            title="Attach artifact to this revision"
                          >
                            <FilePlus2 className="h-3.5 w-3.5" />
                            Artifact
                          </button>
                          <button
                            type="button"
                            className="revka-button h-8 px-2 text-xs"
                            onClick={() => setCreateAction('edge')}
                            title="Create an edge from this revision"
                          >
                            <Link2 className="h-3.5 w-3.5" />
                            Edge
                          </button>
                          <button
                            type="button"
                            className="revka-button h-8 px-2 text-xs"
                            onClick={() => setGraphOpen(true)}
                            title="View local dependency graph"
                          >
                            <Network className="h-3.5 w-3.5" />
                            Graph
                          </button>
                        </div>
                      ) : null}
                      <div className="max-h-[20rem] space-y-1.5 overflow-y-auto">
                        {revisions.map((revision) => {
                        const isRevActive = selectedRevision?.kref === revision.kref;
                        const published = revisionIsPublished(revision);
                        return (
                          <button
                            key={revision.kref}
                            type="button"
                            onClick={() => handleSelectRevision(revision)}
                            className="flex w-full items-center justify-between gap-2 rounded-[10px] px-3 py-2 text-left transition"
                            style={{
                              background: isRevActive
                                ? 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 18%, transparent))'
                                : 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
                              borderLeft: isRevActive
                                ? '2px solid var(--revka-signal-selected)'
                                : '2px solid transparent',
                            }}
                          >
                            <div className="flex items-center gap-2">
                              <Hash className="h-3 w-3" style={{ color: 'var(--revka-text-faint)' }} />
                              <span
                                className="text-sm font-semibold"
                                style={{ color: 'var(--revka-text-primary)' }}
                              >
                                r{revision.number}
                              </span>
                              <div className="flex gap-1">
                                {revision.latest ? (
                                  <TagChip label={t('assets.tag.latest')} tone="var(--revka-signal-live)" />
                                ) : null}
                                {published ? (
                                  <TagChip label={t('assets.tag.published')} tone="var(--revka-status-success)" />
                                ) : null}
                                {revision.deprecated ? (
                                  <TagChip label={t('assets.deprecated')} tone="var(--revka-status-warning)" />
                                ) : null}
                                {revision.tags
                                  .filter((tag) => tag !== 'latest' && tag !== 'published')
                                  .map((tag) => (
                                    <TagChip key={tag} label={tag} tone="var(--revka-text-faint)" />
                                  ))}
                              </div>
                            </div>
                            <span
                              className="shrink-0 text-xs"
                              style={{ color: 'var(--revka-text-faint)' }}
                            >
                              {formatTime(revision.created_at)}
                            </span>
                          </button>
                        );
                        })}
                      </div>
                    </div>
                  )}
                </CollapsibleSection>

                {/* ARTIFACTS */}
                <CollapsibleSection title={t('assets.section.artifacts')} count={artifacts.length}>
                  {loadingRevisionDetail ? (
                    <StateMessage tone="loading" compact title={t('assets.loading.generic')} />
                  ) : artifacts.length === 0 ? (
                    <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>
                      {t('assets.section.artifacts_empty')}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {artifacts.map((artifact) => (
                        <div
                          key={artifact.kref}
                          role="button"
                          tabIndex={0}
                          onClick={() => setSelectedArtifact(artifact)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              setSelectedArtifact(artifact);
                            }
                          }}
                          className="w-full rounded-[10px] px-3 py-2 text-left transition cursor-pointer"
                          style={{
                            background:
                              selectedArtifact?.kref === artifact.kref
                                ? 'var(--revka-signal-selected-soft, color-mix(in srgb, var(--revka-signal-selected) 18%, transparent))'
                                : 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
                            opacity: artifact.deprecated ? 0.62 : 1,
                          }}
                        >
                          <div className="flex items-center gap-2">
                            <Package className="h-3.5 w-3.5 shrink-0" style={{ color: '#2dd4bf' }} />
                            <span
                              className="truncate text-sm font-medium flex-1 min-w-0"
                              style={{ color: 'var(--revka-text-primary)' }}
                            >
                              {artifact.name}
                            </span>
                            {artifact.deprecated ? (
                              <span className="rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase" style={{ color: 'var(--revka-status-warning)', background: 'rgba(245,158,11,0.12)' }}>
                                {t('assets.deprecated')}
                              </span>
                            ) : null}
                            {artifact.location ? (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleOpenArtifactEditor(artifact);
                                }}
                                className="inline-flex items-center gap-1 rounded-[6px] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider shrink-0 transition"
                                style={{
                                  background: 'var(--revka-bg-elevated)',
                                  color: 'var(--revka-text-secondary)',
                                  border: '1px solid var(--revka-border-strong)',
                                }}
                                aria-label={`${t('assets.action.edit_artifact')} ${artifact.name}`}
                              >
                                <Edit3 className="h-3 w-3" />
                                {t('assets.action.edit')}
                              </button>
                            ) : null}
                            {artifact.location ? (
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setViewerArtifact(artifact);
                                }}
                                className="inline-flex items-center gap-1 rounded-[6px] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider shrink-0 transition"
                                style={{
                                  background: 'var(--revka-bg-elevated)',
                                  color: 'var(--revka-text-secondary)',
                                  border: '1px solid var(--revka-border-strong)',
                                }}
                                aria-label={`View ${artifact.name}`}
                              >
                                <Eye className="h-3 w-3" />
                                {t('assets.action.view')}
                              </button>
                            ) : null}
                            <button
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleToggleArtifactDeprecation(artifact);
                              }}
                              className="inline-flex items-center gap-1 rounded-[6px] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider shrink-0 transition"
                              style={{
                                background: 'var(--revka-bg-elevated)',
                                color: artifact.deprecated ? 'var(--revka-status-success)' : 'var(--revka-status-warning)',
                                border: '1px solid var(--revka-border-strong)',
                              }}
                              aria-label={`${artifact.deprecated ? t('assets.action.restore_artifact') : t('assets.action.deprecate_artifact')} ${artifact.name}`}
                            >
                              {actionBusy === `artifact-deprecate:${artifact.kref}` ? <Loader2 className="h-3 w-3 animate-spin" /> : <Ban className="h-3 w-3" />}
                              {artifact.deprecated ? t('assets.action.restore') : t('assets.action.deprecate')}
                              </button>
                          </div>
                          {artifact.location ? (
                            <div
                              className="mt-1 truncate pl-5.5 text-xs"
                              style={{ color: 'var(--revka-text-faint)', paddingLeft: '1.375rem' }}
                            >
                              <MapPinned className="mr-1 inline-block h-3 w-3" />
                              {artifact.location}
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  )}
                </CollapsibleSection>

                {/* EDGES */}
                <CollapsibleSection title={t('assets.section.edges')} count={edges.length}>
                  {edges.length === 0 ? (
                    <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>
                      {t('assets.section.edges_empty')}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {edges.map((edge, index) => (
                        <div
                          key={`${edge.source_kref}-${edge.target_kref}-${edge.edge_type}-${index}`}
                          className="rounded-[10px] px-3 py-2"
                          style={{
                            background:
                              'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
                          }}
                        >
                          <div className="flex items-center gap-1.5 text-xs">
                            <span
                              className="truncate font-mono"
                              style={{ color: 'var(--revka-text-faint)' }}
                            >
                              {edge.source_kref.split('/').pop()?.split('?')[0]}
                            </span>
                            <span
                              className="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-bold uppercase"
                              style={{
                                background: 'rgba(251,146,60,0.1)',
                                color: '#fb923c',
                              }}
                            >
                              {edge.edge_type}
                            </span>
                            <ArrowRight
                              className="h-3 w-3 shrink-0"
                              style={{ color: 'var(--revka-text-faint)' }}
                            />
                            <span
                              className="truncate font-mono"
                              style={{ color: 'var(--revka-text-faint)' }}
                            >
                              {edge.target_kref.split('/').pop()?.split('?')[0]}
                            </span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CollapsibleSection>

                {/* METADATA */}
                <CollapsibleSection
                  title={t('assets.section.metadata')}
                  count={metadataEntries.length}
                  defaultOpen={metadataEntries.length > 0}
                >
                  {metadataEntries.length === 0 ? (
                    <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>
                      {t('assets.section.metadata_empty')}
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {metadataEntries.map(([key, value]) => (
                        <div
                          key={key}
                          className="rounded-[10px] px-3 py-2"
                          style={{
                            background:
                              'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
                          }}
                        >
                          <div
                            className="text-[11px] font-semibold uppercase tracking-[0.1em]"
                            style={{ color: 'var(--revka-text-faint)' }}
                          >
                            {key}
                          </div>
                          <div
                            className="mt-1 break-all text-sm leading-5"
                            style={{ color: 'var(--revka-text-primary)' }}
                          >
                            {String(value)}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CollapsibleSection>
              </div>
            </Panel>
          </div>
        ) : null}
      </div>
    </div>
    {editingArtifact ? (
      <Modal
        title={tpl('assets.editor.title', { name: editingArtifact.name })}
        description={
          selectedRevision && revisionIsPublished(selectedRevision)
            ? t('assets.editor.published_desc')
            : t('assets.editor.mutable_desc')
        }
        onClose={() => {
          if (actionBusy !== 'artifact-save') setEditingArtifact(null);
        }}
        size="2xl"
      >
        <div className="mb-3 flex items-center gap-2 text-xs" style={{ color: 'var(--revka-text-faint)' }}>
          <FileText className="h-3.5 w-3.5" />
          <span className="truncate font-mono">{editingArtifact.kref}</span>
        </div>
        {artifactDraftLoading ? (
          <StateMessage tone="loading" compact title={t('assets.loading.generic')} />
        ) : (
          <textarea
            className="revka-input min-h-[24rem] w-full resize-y font-mono text-xs leading-5"
            value={artifactDraft}
            onChange={(event) => setArtifactDraft(event.target.value)}
            spellCheck={false}
          />
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="revka-button"
            onClick={() => setEditingArtifact(null)}
            disabled={actionBusy === 'artifact-save'}
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className="revka-button revka-button-primary"
            onClick={handleSaveArtifactDraft}
            disabled={artifactDraftLoading || actionBusy === 'artifact-save'}
          >
            {actionBusy === 'artifact-save' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            {t('assets.action.save_artifact')}
          </button>
        </div>
      </Modal>
    ) : null}
    {viewerArtifact ? (
      <ArtifactViewerModal
        artifact={viewerArtifact}
        onClose={() => setViewerArtifact(null)}
      />
    ) : null}
    {createAction ? (
      <CreateActionModal
        action={createAction}
        project={selectedProject}
        spacePath={currentSpacePath}
        selectedItem={selectedItem}
        selectedRevision={selectedRevision}
        selectedBundle={selectedBundle}
        bundleMembers={bundleMembers}
        onClose={() => setCreateAction(null)}
        onCreated={(target) => {
          setCreateAction(null);
          if (target?.item) {
            setRequestedItemKref(target.item.kref);
            setSelectedItem(target.item);
          }
          if (target?.revision) {
            setRequestedRevisionKref(target.revision.kref);
            setSelectedRevision(target.revision);
          }
          if (target?.bundle) {
            setSelectedBundleKref(target.bundle.kref);
            setActiveTab('bundles');
          }
          setReloadNonce((value) => value + 1);
          showNotice('success', 'Kumiho asset operation completed.');
        }}
      />
    ) : null}
    {graphOpen && selectedRevision ? (
      <DependencyGraphModal
        revision={selectedRevision}
        onClose={() => setGraphOpen(false)}
        onOpenKref={(kref) => {
          setGraphOpen(false);
          void handleOpenKref(kref);
        }}
      />
    ) : null}
    </>
  );
}
