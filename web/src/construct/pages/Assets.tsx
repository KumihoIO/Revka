import { useCallback, useEffect, useRef, useState } from 'react';
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
  Database,
  Edit3,
  Eye,
  FileText,
  FolderOpen,
  GitBranch,
  Hash,
  Loader2,
  MapPinned,
  MessageSquare,
  Package,
  Save,
  Search,
  Settings,
  Sparkles,
  Tag,
  Workflow,
  X,
} from 'lucide-react';
import type {
  KumihoArtifact,
  KumihoEdge,
  KumihoItem,
  KumihoProject,
  KumihoRevision,
  KumihoSearchResult,
  KumihoSpace,
} from '@/types/api';
import {
  fetchArtifactBodyText,
  kumihoProxy,
  publishAssetRevision,
  toggleAssetArtifactDeprecation,
  toggleAssetItemDeprecation,
  toggleAssetRevisionDeprecation,
  updateAssetArtifactContent,
} from '@/lib/api';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';
import StateMessage from '../components/ui/StateMessage';
import ArtifactViewerModal from '../components/ui/ArtifactViewerModal';
import Modal from '../components/ui/Modal';
import { copyToClipboard } from '../lib/clipboard';
import { useT } from '@/construct/hooks/useT';

/* ------------------------------------------------------------------ */
/*  Kind metadata                                                      */
/* ------------------------------------------------------------------ */

type KindMeta = { icon: typeof Bot; color: string; bg: string; border: string };
type PathSegment = { name: string; path: string };

const KIND_MAP: Record<string, KindMeta> = {
  agent: { icon: Bot, color: '#22d3ee', bg: 'rgba(34, 211, 238, 0.1)', border: 'rgba(34, 211, 238, 0.25)' },
  skill: { icon: Sparkles, color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.1)', border: 'rgba(167, 139, 250, 0.25)' },
  conversation: { icon: MessageSquare, color: '#60a5fa', bg: 'rgba(96, 165, 250, 0.1)', border: 'rgba(96, 165, 250, 0.25)' },
  decision: { icon: GitBranch, color: '#fbbf24', bg: 'rgba(251, 191, 36, 0.1)', border: 'rgba(251, 191, 36, 0.25)' },
  fact: { icon: BookOpen, color: '#34d399', bg: 'rgba(52, 211, 153, 0.1)', border: 'rgba(52, 211, 153, 0.25)' },
  bundle: { icon: Package, color: '#2dd4bf', bg: 'rgba(45, 212, 191, 0.1)', border: 'rgba(45, 212, 191, 0.25)' },
  config: { icon: Settings, color: '#a1a1aa', bg: 'rgba(161, 161, 170, 0.1)', border: 'rgba(161, 161, 170, 0.25)' },
  workflow: { icon: Workflow, color: '#fb923c', bg: 'rgba(251, 146, 60, 0.1)', border: 'rgba(251, 146, 60, 0.25)' },
};

const DEFAULT_KIND: KindMeta = {
  icon: FileText,
  color: '#a1a1aa',
  bg: 'rgba(161, 161, 170, 0.1)',
  border: 'rgba(161, 161, 170, 0.25)',
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
      style={{ color: copied ? 'var(--construct-status-success)' : 'var(--construct-text-faint)' }}
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
    <div className="border-t" style={{ borderColor: 'var(--construct-border-soft)' }}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full items-center justify-between gap-2 py-3 text-left"
      >
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-secondary)' }}>
          {title}
        </span>
        <div className="flex items-center gap-2">
          <span className="text-[11px]" style={{ color: 'var(--construct-text-faint)' }}>({count})</span>
          <ChevronRight
            className="h-3.5 w-3.5 transition-transform"
            style={{ color: 'var(--construct-text-faint)', transform: open ? 'rotate(90deg)' : undefined }}
          />
        </div>
      </button>
      {open ? <div className="pb-3">{children}</div> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export default function Assets() {
  const { t, tpl } = useT();
  /* ---- state ---- */
  const [projects, setProjects] = useState<KumihoProject[]>([]);
  const [selectedProject, setSelectedProject] = useState<string | null>(null);
  const [projectDropdownOpen, setProjectDropdownOpen] = useState(false);
  const [currentPath, setCurrentPath] = useState<PathSegment[]>([]);
  const [childSpaces, setChildSpaces] = useState<KumihoSpace[]>([]);
  const [items, setItems] = useState<KumihoItem[]>([]);
  const [selectedItem, setSelectedItem] = useState<KumihoItem | null>(null);
  const [revisions, setRevisions] = useState<KumihoRevision[]>([]);
  const [selectedRevision, setSelectedRevision] = useState<KumihoRevision | null>(null);
  const [artifacts, setArtifacts] = useState<KumihoArtifact[]>([]);
  const [selectedArtifact, setSelectedArtifact] = useState<KumihoArtifact | null>(null);
  const [viewerArtifact, setViewerArtifact] = useState<KumihoArtifact | null>(null);
  const [edges, setEdges] = useState<KumihoEdge[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<KumihoSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
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
  const searchTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const dropdownRef = useRef<HTMLDivElement | null>(null);

  /* ---- effects ---- */

  useEffect(() => {
    function handleClick(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setProjectDropdownOpen(false);
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
          return names[0] ?? null;
        });
        setError(null);
      })
      .catch((err) => {
        console.error('[Assets] Failed to load projects:', err);
        setError(t('assets.err.load'));
      })
      .finally(() => setLoading(false));
  }, [t]);

  useEffect(() => {
    return () => {
      if (searchTimeout.current) clearTimeout(searchTimeout.current);
    };
  }, []);

  useEffect(() => {
    if (!selectedProject) return;
    setCurrentPath([{ name: selectedProject, path: `/${selectedProject}` }]);
    setChildSpaces([]);
    setItems([]);
    setSelectedItem(null);
    setRevisions([]);
    setSelectedRevision(null);
    setArtifacts([]);
    setSelectedArtifact(null);
    setEdges([]);
    setSearchQuery('');
    setSearchResults([]);
  }, [selectedProject]);

  const currentSpacePath = currentPath[currentPath.length - 1]?.path ?? null;

  const showNotice = useCallback((tone: 'success' | 'error', message: string) => {
    setNotice({ tone, message });
    setTimeout(() => {
      setNotice((current) => (current?.message === message ? null : current));
    }, 4500);
  }, []);

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
    setSelectedItem(null);
    setRevisions([]);
    setSelectedRevision(null);
    setArtifacts([]);
    setSelectedArtifact(null);
    setEdges([]);

    Promise.all([
      kumihoProxy<KumihoSpace[]>('/spaces', { parent_path: currentSpacePath, recursive: false }).catch(() => []),
      kumihoProxy<KumihoItem[]>('/items', { space_path: currentSpacePath }).catch(() => []),
    ])
      .then(([nextSpaces, nextItems]) => {
        setChildSpaces(nextSpaces);
        setItems(nextItems);
      })
      .catch(() => {
        setChildSpaces([]);
        setItems([]);
      })
      .finally(() => setLoadingContent(false));
  }, [currentSpacePath]);

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
      .then((data) => {
        const sorted = [...data].sort((a, b) => b.number - a.number);
        setRevisions(sorted);
        setSelectedRevision(sorted[0] ?? null);
      })
      .catch(() => setRevisions([]))
      .finally(() => setLoadingRevisions(false));
  }, [selectedItem?.kref]);

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
  }, [refreshRevisionDetail, selectedRevision?.kref]);

  /* ---- callbacks ---- */

  const navigateToSpace = useCallback((space: KumihoSpace) => {
    setCurrentPath((prev) => [...prev, { name: space.name, path: space.path }]);
    setSearchQuery('');
    setSearchResults([]);
    setSearching(false);
  }, []);

  const navigateToBreadcrumb = useCallback((index: number) => {
    setCurrentPath((prev) => prev.slice(0, index + 1));
    setSearchQuery('');
    setSearchResults([]);
    setSearching(false);
  }, []);

  const handleSearchChange = useCallback((query: string) => {
    setSearchQuery(query);
    if (searchTimeout.current) clearTimeout(searchTimeout.current);

    if (!query.trim()) {
      setSearchResults([]);
      setSearching(false);
      return;
    }

    setSearching(true);
    searchTimeout.current = setTimeout(async () => {
      try {
        const results = await kumihoProxy<KumihoSearchResult[]>('/items/fulltext-search', {
          query,
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
  }, [selectedProject]);

  const handleProjectSelect = useCallback((project: string) => {
    if (project === selectedProject) {
      setProjectDropdownOpen(false);
      return;
    }
    if (searchTimeout.current) clearTimeout(searchTimeout.current);
    setProjectDropdownOpen(false);
    setSelectedProject(project);
  }, [selectedProject]);

  const handleNavigateUp = useCallback(() => {
    setCurrentPath((prev) => (prev.length <= 1 ? prev : prev.slice(0, -1)));
    setSearchQuery('');
    setSearchResults([]);
    setSearching(false);
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
  const visibleItems = isSearchActive ? searchResults.map((result) => result.item) : items;
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
            style={{ color: 'var(--construct-text-faint)' }}
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => handleSearchChange(e.target.value)}
            placeholder={t('assets.search_placeholder')}
            className="construct-input pl-10 pr-10"
          />
          {searching ? (
            <div
              className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin rounded-full border-2"
              style={{ borderColor: 'var(--construct-border-soft)', borderTopColor: 'var(--construct-signal-network)' }}
            />
          ) : null}
        </div>
      </div>

      {/* Row 2 — Toolbar: project selector + breadcrumb + up */}
      <div className="flex items-center gap-3">
        <div className="relative" ref={dropdownRef}>
          <button
            type="button"
            className="construct-button justify-between gap-2"
            onClick={() => setProjectDropdownOpen((prev) => !prev)}
          >
            <Database className="h-4 w-4" />
            <span className="truncate">{selectedProject ?? t('assets.project')}</span>
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          {projectDropdownOpen ? (
            <div
              className="absolute left-0 top-full z-20 mt-2 min-w-[14rem] rounded-[14px] border p-2"
              style={{ borderColor: 'var(--construct-border-soft)', background: 'var(--construct-bg-panel-strong)' }}
            >
              {projects.map((project) => (
                <button
                  key={project.name}
                  type="button"
                  className="w-full rounded-[10px] px-3 py-2 text-left text-sm transition"
                  onClick={() => handleProjectSelect(project.name)}
                  style={{
                    color: project.name === selectedProject ? 'var(--construct-text-primary)' : 'var(--construct-text-secondary)',
                    background: project.name === selectedProject ? 'var(--construct-signal-live-soft)' : 'transparent',
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
                <ChevronRight className="h-3.5 w-3.5 shrink-0" style={{ color: 'var(--construct-text-faint)' }} />
              )}
              <button
                type="button"
                onClick={() => navigateToBreadcrumb(index)}
                className="rounded px-1.5 py-0.5 hover:underline"
                style={{
                  color: index === currentPath.length - 1 ? 'var(--construct-text-primary)' : 'var(--construct-text-secondary)',
                }}
              >
                {segment.name}
              </button>
            </span>
          ))}
        </div>

        <button
          type="button"
          className="construct-button"
          onClick={handleNavigateUp}
          disabled={currentPath.length <= 1}
        >
          <ChevronLeft className="h-4 w-4" />
          {t('assets.up')}
        </button>
      </div>

      {/* Error banner */}
      {error ? (
        <div className="flex items-center gap-2 text-sm" style={{ color: 'var(--construct-status-danger)' }}>
          <AlertTriangle className="h-4 w-4 shrink-0" />
          {error}
        </div>
      ) : null}
      {notice ? (
        <div
          className="flex items-center gap-2 rounded-[8px] border px-3 py-2 text-sm"
          style={{
            borderColor: notice.tone === 'success' ? 'rgba(125,255,155,0.24)' : 'rgba(255,107,122,0.24)',
            color: notice.tone === 'success' ? 'var(--construct-status-success)' : 'var(--construct-status-danger)',
            background: notice.tone === 'success' ? 'rgba(125,255,155,0.08)' : 'rgba(255,107,122,0.08)',
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
          {/* Table header. On narrow viewports the only meaningful header
              is NAME (kind chips and timestamps are self-describing inline),
              so we hide the rest at mobile widths to avoid the squashed
              "NAKINMED AUTHOR CREAT…" overlap from fixed-width labels. */}
          <div
            className="construct-assets-row shrink-0 border-b px-4 py-2.5"
            style={{
              borderColor: 'var(--construct-border-soft)',
              color: 'var(--construct-text-faint)',
            }}
          >
            <span className="text-[11px] font-semibold uppercase tracking-[0.14em]">{t('assets.col.name')}</span>
            <span className="construct-assets-kind hidden text-[11px] font-semibold uppercase tracking-[0.14em] md:inline">{t('assets.col.kind')}</span>
            <span className="construct-assets-author hidden text-[11px] font-semibold uppercase tracking-[0.14em] md:inline">{t('assets.col.author')}</span>
            <span className="construct-assets-created hidden text-right text-[11px] font-semibold uppercase tracking-[0.14em] md:inline">{t('assets.col.created')}</span>
          </div>

          {/* Table body */}
          <div className="min-h-0 flex-1 overflow-y-auto">
            {loading || loadingContent ? (
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
                      className="construct-assets-row w-full border-b px-4 py-2.5 text-left transition hover:brightness-125"
                      style={{
                        borderColor: 'var(--construct-border-soft)',
                        background: 'color-mix(in srgb, var(--construct-bg-elevated) 50%, transparent)',
                      }}
                    >
                      <div className="flex min-w-0 items-center gap-2.5">
                        <FolderOpen className="h-4 w-4 shrink-0" style={{ color: '#fbbf24' }} />
                        <span
                          className="truncate text-sm font-medium"
                          style={{ color: 'var(--construct-text-primary)' }}
                        >
                          {space.name}
                        </span>
                      </div>
                      <span className="construct-assets-kind text-xs" style={{ color: 'var(--construct-text-faint)' }}>
                        {t('assets.folder')}
                      </span>
                      <span className="construct-assets-author truncate text-xs" style={{ color: 'var(--construct-text-faint)' }}>
                        --
                      </span>
                      <span className="construct-assets-created text-right text-xs" style={{ color: 'var(--construct-text-faint)' }}>
                        {formatTime(space.created_at)}
                      </span>
                    </button>
                  ))}

                {/* Search status */}
                {isSearchActive && searchResults.length > 0 ? (
                  <div
                    className="border-b px-4 py-2 text-[11px] font-semibold uppercase tracking-[0.12em]"
                    style={{ borderColor: 'var(--construct-border-soft)', color: 'var(--construct-text-faint)' }}
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
                      onClick={() => setSelectedItem(item)}
                      className="construct-assets-row w-full border-b px-4 py-2.5 text-left transition"
                      style={{
                        borderColor: 'var(--construct-border-soft)',
                        background: isActive
                          ? 'color-mix(in srgb, var(--construct-signal-live-soft) 80%, var(--construct-bg-panel))'
                          : 'transparent',
                        opacity: item.deprecated ? 0.6 : 1,
                      }}
                    >
                      <div className="flex min-w-0 items-center gap-2.5">
                        <Icon className="h-4 w-4 shrink-0" style={{ color: meta.color }} />
                        <span
                          className="truncate text-sm font-medium"
                          style={{ color: 'var(--construct-text-primary)' }}
                        >
                          {item.item_name || item.name}
                        </span>
                      </div>
                      <span
                        className="construct-assets-kind inline-flex items-center justify-center rounded-full px-2 py-0.5 text-[10px] font-semibold capitalize"
                        style={{
                          background: meta.bg,
                          color: meta.color,
                          border: `1px solid ${meta.border}`,
                        }}
                      >
                        {item.kind}
                      </span>
                      <span
                        className="construct-assets-author truncate text-xs font-mono"
                        style={{ color: 'var(--construct-text-faint)' }}
                      >
                        {readableAuthor(item)}
                      </span>
                      <span className="construct-assets-created text-right text-xs" style={{ color: 'var(--construct-text-faint)' }}>
                        {formatTime(item.created_at)}
                      </span>
                    </button>
                  );
                })}

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
                    style={{ color: 'var(--construct-text-primary)' }}
                  >
                    {selectedItem.item_name || selectedItem.name}
                  </h3>
                  <div className="mt-1.5">
                    <CopyableKref kref={selectedItem.kref} />
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setSelectedItem(null)}
                  className="shrink-0 rounded-[10px] p-1.5 transition"
                  style={{ color: 'var(--construct-text-faint)' }}
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
                      ? 'var(--construct-status-warning)'
                      : 'var(--construct-status-success)',
                  }}
                >
                  {selectedItem.deprecated ? t('assets.deprecated') : t('assets.active')}
                </span>
                <span className="text-xs" style={{ color: 'var(--construct-text-faint)' }}>
                  {tpl(revisions.length === 1 ? 'assets.rev_count_one' : 'assets.rev_count', { count: revisions.length })}
                </span>
                <button
                  type="button"
                  className="construct-button ml-auto h-8 px-2 text-xs"
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
                style={{ color: 'var(--construct-text-secondary)' }}
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
                    <div className="text-sm" style={{ color: 'var(--construct-text-faint)' }}>
                      {t('assets.section.revisions_empty')}
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {selectedRevision ? (
                        <div className="flex flex-wrap gap-2 rounded-[10px] border p-2" style={{ borderColor: 'var(--construct-border-soft)' }}>
                          <button
                            type="button"
                            className="construct-button h-8 px-2 text-xs"
                            onClick={handlePublishRevision}
                            disabled={revisionIsPublished(selectedRevision) || actionBusy === 'revision-publish'}
                            title={t('assets.action.publish_revision')}
                          >
                            {actionBusy === 'revision-publish' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Tag className="h-3.5 w-3.5" />}
                            {revisionIsPublished(selectedRevision) ? t('assets.action.published') : t('assets.action.publish')}
                          </button>
                          <button
                            type="button"
                            className="construct-button h-8 px-2 text-xs"
                            onClick={handleToggleRevisionDeprecation}
                            disabled={actionBusy === 'revision-deprecate'}
                            title={selectedRevision.deprecated ? t('assets.action.restore_revision') : t('assets.action.deprecate_revision')}
                          >
                            {actionBusy === 'revision-deprecate' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Ban className="h-3.5 w-3.5" />}
                            {selectedRevision.deprecated ? t('assets.action.restore') : t('assets.action.deprecate')}
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
                            onClick={() => setSelectedRevision(revision)}
                            className="flex w-full items-center justify-between gap-2 rounded-[10px] px-3 py-2 text-left transition"
                            style={{
                              background: isRevActive
                                ? 'var(--construct-signal-live-soft)'
                                : 'color-mix(in srgb, var(--construct-bg-elevated) 50%, transparent)',
                              borderLeft: isRevActive
                                ? '2px solid var(--construct-signal-live)'
                                : '2px solid transparent',
                            }}
                          >
                            <div className="flex items-center gap-2">
                              <Hash className="h-3 w-3" style={{ color: 'var(--construct-text-faint)' }} />
                              <span
                                className="text-sm font-semibold"
                                style={{ color: 'var(--construct-text-primary)' }}
                              >
                                r{revision.number}
                              </span>
                              <div className="flex gap-1">
                                {revision.latest ? (
                                  <TagChip label={t('assets.tag.latest')} tone="var(--construct-signal-live)" />
                                ) : null}
                                {published ? (
                                  <TagChip label={t('assets.tag.published')} tone="var(--construct-status-success)" />
                                ) : null}
                                {revision.deprecated ? (
                                  <TagChip label={t('assets.deprecated')} tone="var(--construct-status-warning)" />
                                ) : null}
                                {revision.tags
                                  .filter((tag) => tag !== 'latest' && tag !== 'published')
                                  .map((tag) => (
                                    <TagChip key={tag} label={tag} tone="var(--construct-text-faint)" />
                                  ))}
                              </div>
                            </div>
                            <span
                              className="shrink-0 text-xs"
                              style={{ color: 'var(--construct-text-faint)' }}
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
                    <div className="text-sm" style={{ color: 'var(--construct-text-faint)' }}>
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
                                ? 'var(--construct-signal-live-soft)'
                                : 'color-mix(in srgb, var(--construct-bg-elevated) 50%, transparent)',
                            opacity: artifact.deprecated ? 0.62 : 1,
                          }}
                        >
                          <div className="flex items-center gap-2">
                            <Package className="h-3.5 w-3.5 shrink-0" style={{ color: '#2dd4bf' }} />
                            <span
                              className="truncate text-sm font-medium flex-1 min-w-0"
                              style={{ color: 'var(--construct-text-primary)' }}
                            >
                              {artifact.name}
                            </span>
                            {artifact.deprecated ? (
                              <span className="rounded-full px-1.5 py-0.5 text-[9px] font-semibold uppercase" style={{ color: 'var(--construct-status-warning)', background: 'rgba(245,158,11,0.12)' }}>
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
                                  background: 'var(--construct-bg-elevated)',
                                  color: 'var(--construct-text-secondary)',
                                  border: '1px solid var(--construct-border-strong)',
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
                                  background: 'var(--construct-bg-elevated)',
                                  color: 'var(--construct-text-secondary)',
                                  border: '1px solid var(--construct-border-strong)',
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
                                background: 'var(--construct-bg-elevated)',
                                color: artifact.deprecated ? 'var(--construct-status-success)' : 'var(--construct-status-warning)',
                                border: '1px solid var(--construct-border-strong)',
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
                              style={{ color: 'var(--construct-text-faint)', paddingLeft: '1.375rem' }}
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
                    <div className="text-sm" style={{ color: 'var(--construct-text-faint)' }}>
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
                              'color-mix(in srgb, var(--construct-bg-elevated) 50%, transparent)',
                          }}
                        >
                          <div className="flex items-center gap-1.5 text-xs">
                            <span
                              className="truncate font-mono"
                              style={{ color: 'var(--construct-text-faint)' }}
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
                              style={{ color: 'var(--construct-text-faint)' }}
                            />
                            <span
                              className="truncate font-mono"
                              style={{ color: 'var(--construct-text-faint)' }}
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
                    <div className="text-sm" style={{ color: 'var(--construct-text-faint)' }}>
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
                              'color-mix(in srgb, var(--construct-bg-elevated) 50%, transparent)',
                          }}
                        >
                          <div
                            className="text-[11px] font-semibold uppercase tracking-[0.1em]"
                            style={{ color: 'var(--construct-text-faint)' }}
                          >
                            {key}
                          </div>
                          <div
                            className="mt-1 break-all text-sm leading-5"
                            style={{ color: 'var(--construct-text-primary)' }}
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
        <div className="mb-3 flex items-center gap-2 text-xs" style={{ color: 'var(--construct-text-faint)' }}>
          <FileText className="h-3.5 w-3.5" />
          <span className="truncate font-mono">{editingArtifact.kref}</span>
        </div>
        {artifactDraftLoading ? (
          <StateMessage tone="loading" compact title={t('assets.loading.generic')} />
        ) : (
          <textarea
            className="construct-input min-h-[24rem] w-full resize-y font-mono text-xs leading-5"
            value={artifactDraft}
            onChange={(event) => setArtifactDraft(event.target.value)}
            spellCheck={false}
          />
        )}
        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            className="construct-button"
            onClick={() => setEditingArtifact(null)}
            disabled={actionBusy === 'artifact-save'}
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            className="construct-button construct-button-primary"
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
    </>
  );
}
