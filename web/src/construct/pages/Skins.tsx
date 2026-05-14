import { useCallback, useState, type DragEvent } from 'react';
import { Check, EyeOff, RefreshCw, Trash2, UploadCloud } from 'lucide-react';
import type { SkinModeName, SkinSummary } from '@/types/api';
import { skinAssetPath } from '@/lib/basePath';
import { useTheme } from '@/construct/hooks/useTheme';
import Panel from '../components/ui/Panel';
import PageHeader from '../components/ui/PageHeader';

function readError(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function ModePreview({ skin, mode }: { skin: SkinSummary; mode: SkinModeName }) {
  const modeDef = skin.manifest.modes[mode];
  const tokens = modeDef?.tokens ?? {};
  const assets = modeDef?.assets ?? {};
  const preview = modeDef?.preview ? skinAssetPath(skin.id, modeDef.preview) : assets.dashboardHero ? skinAssetPath(skin.id, assets.dashboardHero) : null;
  const bg = tokens['--construct-bg-base'] ?? (mode === 'light' ? '#f4f8f5' : '#05080a');
  const surface = tokens['--construct-bg-surface'] ?? (mode === 'light' ? '#ffffff' : '#0c1413');
  const text = tokens['--construct-text-primary'] ?? (mode === 'light' ? '#13201b' : '#e7f1eb');
  const accent = tokens['--construct-signal-live'] ?? (mode === 'light' ? '#3faf68' : '#7dff9b');

  return (
    <div className="overflow-hidden rounded-[12px] border" style={{ borderColor: 'var(--construct-border-soft)', background: bg, color: text }}>
      <div className="h-20 bg-cover bg-center" style={{ backgroundImage: preview ? `url("${preview.replace(/"/g, '%22')}")` : `linear-gradient(135deg, ${accent}33, transparent)` }} />
      <div className="space-y-2 p-3" style={{ background: surface }}>
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-semibold uppercase tracking-[0.14em]">{mode}</span>
          <span className="h-3 w-3 rounded-full" style={{ background: accent }} />
        </div>
        <div className="grid grid-cols-4 gap-1.5">
          {Object.entries(tokens).slice(0, 4).map(([name, value]) => (
            <span key={name} className="h-5 rounded-[6px] border" style={{ background: value, borderColor: 'rgba(0,0,0,0.08)' }} title={`${name}: ${value}`} />
          ))}
        </div>
      </div>
    </div>
  );
}

function SkinCard({ skin }: { skin: SkinSummary }) {
  const { activeSkinId, setSkin, deleteSkin } = useTheme();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = activeSkinId === skin.id;

  const onDelete = async () => {
    setBusy(true);
    setError(null);
    try {
      await deleteSkin(skin.id);
    } catch (err) {
      setError(readError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel className="p-4">
      <div className="flex flex-col gap-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="construct-kicker">{skin.id}</div>
            <h3 className="mt-1 truncate text-lg font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{skin.name}</h3>
            <p className="mt-1 text-xs" style={{ color: 'var(--construct-text-faint)' }}>v{skin.version}</p>
          </div>
          {active ? (
            <span className="construct-status-pill" style={{ color: 'var(--construct-signal-live)' }}>
              <Check className="h-3.5 w-3.5" />
              Active
            </span>
          ) : null}
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <ModePreview skin={skin} mode="light" />
          <ModePreview skin={skin} mode="dark" />
        </div>

        {error ? (
          <div className="rounded-[10px] border px-3 py-2 text-sm" style={{ borderColor: 'var(--construct-status-danger)', color: 'var(--construct-status-danger)' }}>
            {error}
          </div>
        ) : null}

        <div className="flex flex-wrap gap-2">
          <button type="button" className="construct-button text-sm" onClick={() => setSkin(active ? null : skin.id)} disabled={busy}>
            {active ? <EyeOff className="h-4 w-4" /> : <Check className="h-4 w-4" />}
            {active ? 'Deactivate' : 'Activate'}
          </button>
          <button type="button" className="construct-button text-sm" onClick={onDelete} disabled={busy}>
            <Trash2 className="h-4 w-4" />
            Delete
          </button>
        </div>
      </div>
    </Panel>
  );
}

export default function Skins() {
  const { installedSkins, skinsLoading, importSkinZip, refreshSkins } = useTheme();
  const [dragging, setDragging] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const importFile = useCallback(async (file: File | null | undefined) => {
    if (!file) return;
    setImporting(true);
    setError(null);
    try {
      await importSkinZip(file);
    } catch (err) {
      setError(readError(err));
    } finally {
      setImporting(false);
    }
  }, [importSkinZip]);

  const onDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault();
    setDragging(false);
    void importFile(event.dataTransfer.files[0]);
  };

  return (
    <div className="space-y-4">
      <PageHeader
        kicker="Appearance"
        title="Skins"
        actions={(
          <button type="button" className="construct-button text-sm" onClick={() => void refreshSkins()} disabled={skinsLoading}>
            <RefreshCw className={`h-4 w-4 ${skinsLoading ? 'animate-spin' : ''}`} />
            Refresh
          </button>
        )}
      />

      <Panel className="p-5">
        <label
          className="flex min-h-36 cursor-pointer flex-col items-center justify-center rounded-[12px] border border-dashed px-4 py-6 text-center transition"
          style={{
            borderColor: dragging ? 'var(--construct-border-strong)' : 'var(--construct-border-soft)',
            background: dragging ? 'var(--construct-signal-live-soft)' : 'var(--construct-bg-surface)',
          }}
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => event.preventDefault()}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <UploadCloud className="h-7 w-7" style={{ color: 'var(--construct-signal-network)' }} />
          <span className="mt-3 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{importing ? 'Importing ZIP...' : 'Upload skin ZIP'}</span>
          <span className="mt-1 text-xs" style={{ color: 'var(--construct-text-faint)' }}>construct-skin.json, tokens, and local image assets</span>
          <input
            className="sr-only"
            type="file"
            accept=".zip,application/zip"
            disabled={importing}
            onChange={(event) => {
              void importFile(event.currentTarget.files?.[0]);
              event.currentTarget.value = '';
            }}
          />
        </label>
        {error ? (
          <div className="mt-4 rounded-[10px] border px-3 py-2 text-sm" style={{ borderColor: 'var(--construct-status-danger)', color: 'var(--construct-status-danger)' }} aria-live="polite">
            {error}
          </div>
        ) : null}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        {installedSkins.map((skin) => (
          <SkinCard key={skin.id} skin={skin} />
        ))}
      </div>

      {!skinsLoading && installedSkins.length === 0 ? (
        <Panel className="p-5" variant="utility">
          <div className="text-sm" style={{ color: 'var(--construct-text-secondary)' }}>No skins installed.</div>
        </Panel>
      ) : null}
    </div>
  );
}
