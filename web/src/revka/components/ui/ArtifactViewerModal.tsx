import { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { AlertCircle, Check, Copy, ExternalLink, FileText, Image as ImageIcon, Film, Package } from 'lucide-react';
import Modal from './Modal';
import type { KumihoArtifact } from '../../../types/api';
import { getToken } from '../../../lib/auth';
import { apiOrigin, basePath } from '../../../lib/basePath';

type Kind = 'text' | 'markdown' | 'image' | 'video' | 'binary';

const MARKDOWN_EXTS = new Set(['md', 'markdown', 'mdx']);
const TEXT_EXTS = new Set([
  'txt', 'log', 'json', 'yaml', 'yml', 'toml', 'ini', 'csv', 'tsv',
  'xml', 'html', 'css', 'js', 'ts', 'tsx', 'jsx', 'py', 'rs', 'go',
  'sh', 'bash', 'zsh', 'sql', 'env',
]);
const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif']);
const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'm4v', 'ogv']);

function extOf(location: string): string {
  const clean = (location.split('?')[0] ?? '').split('#')[0] ?? '';
  const dot = clean.lastIndexOf('.');
  if (dot < 0) return '';
  return clean.slice(dot + 1).toLowerCase();
}

function detectKind(location: string): Kind {
  const ext = extOf(location);
  if (MARKDOWN_EXTS.has(ext)) return 'markdown';
  if (TEXT_EXTS.has(ext)) return 'text';
  if (IMAGE_EXTS.has(ext)) return 'image';
  if (VIDEO_EXTS.has(ext)) return 'video';
  return 'binary';
}

function iconFor(kind: Kind) {
  switch (kind) {
    case 'markdown':
    case 'text':
      return FileText;
    case 'image':
      return ImageIcon;
    case 'video':
      return Film;
    default:
      return Package;
  }
}

function bodyUrl(location: string): string {
  const encoded = encodeURIComponent(location);
  return `${apiOrigin}${basePath}/api/artifact-body?location=${encoded}`;
}

async function fetchBody(location: string): Promise<Response> {
  const token = getToken();
  const headers = new Headers();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(bodyUrl(location), { headers });
  return res;
}

export default function ArtifactViewerModal({
  artifact,
  onClose,
}: {
  artifact: KumihoArtifact;
  onClose: () => void;
}) {
  const kind = useMemo(() => detectKind(artifact.location), [artifact.location]);
  const Icon = iconFor(kind);

  return (
    <Modal
      title={artifact.name || 'Artifact'}
      description={artifact.location}
      onClose={onClose}
      size="2xl"
    >
      <div className="flex items-center gap-2 mb-3 text-xs" style={{ color: 'var(--revka-text-faint)' }}>
        <Icon className="h-3.5 w-3.5" />
        <span className="uppercase tracking-wider">{kind}</span>
        <span>·</span>
        <span className="font-mono truncate">{artifact.kref}</span>
      </div>
      <ArtifactBody artifact={artifact} kind={kind} />
      <ArtifactFooter artifact={artifact} />
    </Modal>
  );
}

function ArtifactBody({ artifact, kind }: { artifact: KumihoArtifact; kind: Kind }) {
  if (kind === 'markdown' || kind === 'text') {
    return <TextReader artifact={artifact} asMarkdown={kind === 'markdown'} />;
  }
  if (kind === 'image') {
    return <ImageViewer artifact={artifact} />;
  }
  if (kind === 'video') {
    return <VideoViewer artifact={artifact} />;
  }
  return <BinaryFallback artifact={artifact} />;
}

function TextReader({ artifact, asMarkdown }: { artifact: KumihoArtifact; asMarkdown: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setText(null);
    setError(null);
    fetchBody(artifact.location)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.text().catch(() => '');
          throw new Error(body || `HTTP ${res.status}`);
        }
        return res.text();
      })
      .then((t) => {
        if (!cancelled) setText(t);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [artifact.location]);

  if (error) {
    return <ErrorPanel message={error} />;
  }
  if (text === null) {
    return <LoadingPanel />;
  }

  if (asMarkdown) {
    return (
      <div
        className="rounded-[12px] p-4 overflow-auto max-h-[60vh] text-sm chat-markdown"
        style={{
          background: 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
          color: 'var(--revka-text-primary)',
        }}
      >
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
      </div>
    );
  }

  return (
    <pre
      className="rounded-[12px] p-4 overflow-auto max-h-[60vh] text-xs font-mono whitespace-pre-wrap break-words"
      style={{
        background: 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
        color: 'var(--revka-text-primary)',
      }}
    >
      {text}
    </pre>
  );
}

function ImageViewer({ artifact }: { artifact: KumihoArtifact }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setSrc(null);
    setError(null);
    fetchBody(artifact.location)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.text().catch(() => '');
          throw new Error(body || `HTTP ${res.status}`);
        }
        return res.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.location]);

  if (error) return <ErrorPanel message={error} />;
  if (!src) return <LoadingPanel />;

  return (
    <div
      className="rounded-[12px] p-3 overflow-auto max-h-[60vh] flex items-center justify-center"
      style={{ background: 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)' }}
    >
      <img
        src={src}
        alt={artifact.name}
        className="max-w-full max-h-[56vh] object-contain"
        style={{ imageRendering: 'auto' }}
      />
    </div>
  );
}

function VideoViewer({ artifact }: { artifact: KumihoArtifact }) {
  const [src, setSrc] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [codecError, setCodecError] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    setSrc(null);
    setError(null);
    setCodecError(false);
    fetchBody(artifact.location)
      .then(async (res) => {
        if (!res.ok) {
          const body = await res.text().catch(() => '');
          throw new Error(body || `HTTP ${res.status}`);
        }
        return res.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact.location]);

  if (error) return <ErrorPanel message={error} />;
  if (!src) return <LoadingPanel />;

  if (codecError) {
    return (
      <div className="space-y-3">
        <div
          className="rounded-[12px] p-4 flex items-start gap-3 text-sm"
          style={{
            background: 'color-mix(in srgb, var(--revka-status-warning) 10%, transparent)',
            color: 'var(--revka-text-primary)',
            border: '1px solid color-mix(in srgb, var(--revka-status-warning) 30%, transparent)',
          }}
        >
          <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" style={{ color: 'var(--revka-status-warning)' }} />
          <div className="space-y-1">
            <div className="font-medium">Browser can't decode this video</div>
            <div className="text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
              Web video viewers support MP4 (H.264/H.265), WebM (VP8/VP9/AV1), and Ogg.
              Other codecs (ProRes, DNxHD, MXF, etc.) need an external player.
            </div>
          </div>
        </div>
        <PathActions artifact={artifact} />
      </div>
    );
  }

  return (
    <div
      className="rounded-[12px] p-3 overflow-hidden max-h-[60vh] flex items-center justify-center"
      style={{ background: 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)' }}
    >
      <video
        ref={videoRef}
        src={src}
        controls
        className="max-w-full max-h-[56vh]"
        onError={() => setCodecError(true)}
      >
        Your browser does not support the video tag.
      </video>
    </div>
  );
}

function BinaryFallback({ artifact }: { artifact: KumihoArtifact }) {
  return (
    <div className="space-y-3">
      <div
        className="rounded-[12px] p-4 flex items-start gap-3 text-sm"
        style={{
          background: 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
          color: 'var(--revka-text-secondary)',
        }}
      >
        <Package className="h-4 w-4 shrink-0 mt-0.5" style={{ color: 'var(--revka-text-faint)' }} />
        <div>
          No inline preview for <span className="font-mono">.{extOf(artifact.location) || 'bin'}</span> files.
          Open externally or copy the path below.
        </div>
      </div>
      <PathActions artifact={artifact} />
    </div>
  );
}

function PathActions({ artifact }: { artifact: KumihoArtifact }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(artifact.location);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      // no-op — user will see no change; silent fallback is fine
    }
  };

  const handleOpenExternal = () => {
    const url = bodyUrl(artifact.location);
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  return (
    <div
      className="rounded-[12px] p-3 space-y-2"
      style={{ background: 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)' }}
    >
      <div
        className="text-xs font-mono break-all"
        style={{ color: 'var(--revka-text-secondary)' }}
      >
        {artifact.location}
      </div>
      <div className="flex gap-2">
        <button
          type="button"
          onClick={handleCopy}
          className="inline-flex items-center gap-1.5 rounded-[8px] px-2.5 py-1.5 text-xs font-medium transition"
          style={{
            background: 'var(--revka-bg-elevated)',
            color: 'var(--revka-text-primary)',
            border: '1px solid var(--revka-border-strong)',
          }}
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
          {copied ? 'Copied' : 'Copy path'}
        </button>
        <button
          type="button"
          onClick={handleOpenExternal}
          className="inline-flex items-center gap-1.5 rounded-[8px] px-2.5 py-1.5 text-xs font-medium transition"
          style={{
            background: 'var(--revka-bg-elevated)',
            color: 'var(--revka-text-primary)',
            border: '1px solid var(--revka-border-strong)',
          }}
        >
          <ExternalLink className="h-3.5 w-3.5" />
          Open externally
        </button>
      </div>
    </div>
  );
}

function ArtifactFooter({ artifact }: { artifact: KumihoArtifact }) {
  const created = artifact.created_at ? new Date(artifact.created_at) : null;
  const createdStr = created && !Number.isNaN(created.getTime()) ? created.toLocaleString() : null;
  return (
    <div className="mt-4 pt-3 border-t text-xs flex items-center gap-3 flex-wrap"
      style={{
        borderColor: 'var(--revka-border)',
        color: 'var(--revka-text-faint)',
      }}
    >
      {createdStr ? <span>Created {createdStr}</span> : null}
      {artifact.deprecated ? <span style={{ color: 'var(--revka-status-warning)' }}>Deprecated</span> : null}
    </div>
  );
}

function LoadingPanel() {
  return (
    <div
      className="rounded-[12px] p-6 text-center text-sm"
      style={{
        background: 'color-mix(in srgb, var(--revka-bg-elevated) 50%, transparent)',
        color: 'var(--revka-text-faint)',
      }}
    >
      Loading…
    </div>
  );
}

function ErrorPanel({ message }: { message: string }) {
  return (
    <div
      className="rounded-[12px] p-4 flex items-start gap-3 text-sm"
      style={{
        background: 'color-mix(in srgb, var(--revka-status-error) 10%, transparent)',
        color: 'var(--revka-text-primary)',
        border: '1px solid color-mix(in srgb, var(--revka-status-error) 30%, transparent)',
      }}
    >
      <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" style={{ color: 'var(--revka-status-error)' }} />
      <div className="break-words">{message}</div>
    </div>
  );
}
