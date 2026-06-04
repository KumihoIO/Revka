import { FileText, Image as ImageIcon, X } from 'lucide-react';
import type { StagedAttachment } from '@/revka/hooks/useAgentChatSession';

interface AttachmentChipProps {
  attachment: StagedAttachment;
  onRemove: (fileId: string) => void;
  /** Optional accent color for the leading icon (defaults to muted). */
  accent?: string;
}

/**
 * Compact preview chip for a single staged attachment in the chat
 * composer. Renders one of three layouts:
 *   - Image with a 24×24 thumbnail (when `previewUrl` is set)
 *   - Image without preview (e.g. SVG that didn't decode to a data URL)
 *   - Document — text icon plus filename and humanized size
 *
 * Always exposes an × button to remove the attachment from the staged
 * list before sending.
 */
export default function AttachmentChip({ attachment, onRemove, accent }: AttachmentChipProps) {
  const isImage = attachment.mime.startsWith('image/');
  const accentColor = accent ?? 'var(--revka-text-muted)';

  return (
    <div
      className="group inline-flex max-w-[220px] items-center gap-1.5 rounded-md border px-1.5 py-1 text-[11px] transition-colors"
      style={{
        borderColor: 'var(--revka-border-soft)',
        background: 'var(--revka-bg-surface)',
        color: 'var(--revka-text-secondary)',
      }}
      title={`${attachment.filename} · ${humanizeBytes(attachment.size)} · ${attachment.mime}`}
    >
      {isImage && attachment.previewUrl ? (
        <img
          src={attachment.previewUrl}
          alt={attachment.filename}
          className="h-6 w-6 shrink-0 rounded-sm object-cover"
        />
      ) : isImage ? (
        <ImageIcon className="h-3.5 w-3.5 shrink-0" style={{ color: accentColor }} />
      ) : (
        <FileText className="h-3.5 w-3.5 shrink-0" style={{ color: accentColor }} />
      )}
      <span className="min-w-0 flex-1 truncate font-mono">{attachment.filename}</span>
      <span className="shrink-0 text-[10px]" style={{ color: 'var(--revka-text-faint)' }}>
        {humanizeBytes(attachment.size)}
      </span>
      <button
        type="button"
        onClick={() => onRemove(attachment.file_id)}
        aria-label={`Remove ${attachment.filename}`}
        title="Remove"
        className="ml-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded transition-colors hover:bg-white/10 focus:outline-none focus-visible:ring-1 focus-visible:ring-current"
        style={{ color: 'var(--revka-text-faint)' }}
      >
        <X className="h-2.5 w-2.5" />
      </button>
    </div>
  );
}

/** Render a byte count compactly (e.g. `12 KB`, `2.4 MB`). */
function humanizeBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
