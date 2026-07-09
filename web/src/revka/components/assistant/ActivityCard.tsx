import { memo, useState } from 'react';
import { ChevronRight, Copy, Check, GitCommitHorizontal } from 'lucide-react';
import type { ActivityEvent } from '@/components/chat/types';
import type { CodeChangeFile, CodeChangesPayload } from '@/types/api';
import { copyToClipboard } from '@/revka/lib/clipboard';
import { t } from '@/lib/i18n';

interface ActivityCardProps {
  event: ActivityEvent;
  /** Optional accent color for the leading rail. Defaults to muted. */
  accent?: string;
  /** Optional fixed font size to inherit from the chat config. */
  fontSize?: number;
}

function looksLikeDiff(detail: string): boolean {
  const lines = detail.split(/\r?\n/);
  const hasDiffHeader = lines.some((line) =>
    line.startsWith('diff --git')
    || line.startsWith('@@')
    || line.startsWith('--- ')
    || line.startsWith('+++ ')
    || line.startsWith('*** Begin Patch')
    || line.startsWith('*** Update File:')
    || line.startsWith('*** Add File:')
    || line.startsWith('*** Delete File:'),
  );
  const hasAddition = lines.some((line) =>
    line.startsWith('+') && !line.startsWith('+++'),
  );
  const hasDeletion = lines.some((line) =>
    line.startsWith('-') && !line.startsWith('---'),
  );
  return hasDiffHeader || (hasAddition && hasDeletion);
}

function DiffDetail({ detail, fontSize }: { detail: string; fontSize: string }) {
  return (
    <div
      className="overflow-auto rounded-[4px] border font-mono"
      style={{
        borderColor: 'var(--revka-border-soft)',
        background: 'var(--revka-bg-base)',
        fontSize,
        lineHeight: 1.45,
        maxHeight: '28rem',
        textShadow: 'none',
      }}
    >
      {detail.split(/\r?\n/).map((line, index) => {
        const isAddition = line.startsWith('+') && !line.startsWith('+++');
        const isDeletion = line.startsWith('-') && !line.startsWith('---');
        const isHeader =
          line.startsWith('diff --git')
          || line.startsWith('@@')
          || line.startsWith('--- ')
          || line.startsWith('+++ ')
          || line.startsWith('*** ');
        return (
          <div
            key={`${index}-${line.slice(0, 16)}`}
            className="grid min-w-max grid-cols-[2.5rem_1fr]"
            style={{
              background: isAddition
                ? 'color-mix(in srgb, var(--revka-status-success) 12%, transparent)'
                : isDeletion
                  ? 'color-mix(in srgb, var(--revka-status-danger) 12%, transparent)'
                  : isHeader
                    ? 'color-mix(in srgb, var(--revka-bg-surface) 75%, transparent)'
                    : 'transparent',
              color: isAddition
                ? 'var(--revka-status-success)'
                : isDeletion
                  ? 'var(--revka-status-danger)'
                  : isHeader
                    ? 'var(--revka-text-primary)'
                    : 'var(--revka-text-secondary)',
            }}
          >
            <span
              className="select-none border-r px-2 text-right"
              style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-faint)' }}
            >
              {isAddition ? '+' : isDeletion ? '-' : ''}
            </span>
            <span className="whitespace-pre px-2">{line}</span>
          </div>
        );
      })}
    </div>
  );
}

function fileStatusGlyph(status: CodeChangeFile['status']): string {
  if (status === 'added') return 'A';
  if (status === 'deleted') return 'D';
  if (status === 'binary') return 'B';
  return 'M';
}

function fileStatusColor(status: CodeChangeFile['status']): string {
  if (status === 'added') return 'var(--revka-status-success)';
  if (status === 'deleted') return 'var(--revka-status-danger)';
  if (status === 'binary') return 'var(--revka-text-faint)';
  return 'var(--revka-text-secondary)';
}

/** One file inside the changes card: status + path + stats header, with a
 *  collapsible unified diff underneath (when a patch is available). */
function FileChangeSection({
  file,
  fontSize,
  defaultOpen,
}: {
  file: CodeChangeFile;
  fontSize: string;
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const hasPatch = !!file.patch;
  return (
    <div className="rounded-[4px] border" style={{ borderColor: 'var(--revka-border-soft)' }}>
      <button
        type="button"
        onClick={() => hasPatch && setOpen((prev) => !prev)}
        disabled={!hasPatch}
        className="flex w-full items-center gap-2 px-2 py-1 text-left font-mono"
        style={{ cursor: hasPatch ? 'pointer' : 'default', fontSize }}
      >
        {hasPatch ? (
          <ChevronRight
            className="h-3 w-3 shrink-0 transition-transform"
            style={{
              color: 'var(--revka-text-faint)',
              transform: open ? 'rotate(90deg)' : 'rotate(0deg)',
            }}
          />
        ) : (
          <span className="inline-block h-3 w-3 shrink-0" />
        )}
        <span className="shrink-0 font-semibold" style={{ color: fileStatusColor(file.status) }}>
          {fileStatusGlyph(file.status)}
        </span>
        <span className="min-w-0 flex-1 truncate" style={{ color: 'var(--revka-text-secondary)' }} title={file.path}>
          {file.path}
        </span>
        {(file.insertions ?? null) !== null && (
          <span className="shrink-0" style={{ color: 'var(--revka-status-success)' }}>+{file.insertions}</span>
        )}
        {(file.deletions ?? null) !== null && (
          <span className="shrink-0" style={{ color: 'var(--revka-status-danger)' }}>−{file.deletions}</span>
        )}
        {file.truncated && (
          <span className="shrink-0 uppercase" style={{ color: 'var(--revka-text-faint)', fontSize: '9px' }}>
            {t('agent.code_changes_truncated')}
          </span>
        )}
      </button>
      {hasPatch && open && <DiffDetail detail={file.patch ?? ''} fontSize={fontSize} />}
    </div>
  );
}

/** Structured "what this turn changed" card body: per-file diff sections
 *  plus a commit marker when HEAD moved during the turn. */
function CodeChangesDetail({ changes, fontSize }: { changes: CodeChangesPayload; fontSize: string }) {
  return (
    <div className="space-y-1">
      {changes.committed && (
        <div
          className="flex items-center gap-1.5 font-mono"
          style={{ color: 'var(--revka-text-muted)', fontSize }}
        >
          <GitCommitHorizontal className="h-3 w-3 shrink-0" />
          <span>
            {t('agent.code_changes_committed')}
            {changes.head_before && changes.head_after
              ? ` ${changes.head_before.slice(0, 8)} → ${changes.head_after.slice(0, 8)}`
              : ''}
          </span>
        </div>
      )}
      {changes.files.map((file, index) => (
        <FileChangeSection
          key={`${index}:${file.path}`}
          file={file}
          fontSize={fontSize}
          defaultOpen={changes.files.length === 1}
        />
      ))}
      {changes.truncated && (
        <div style={{ color: 'var(--revka-text-faint)', fontSize }}>
          {t('agent.code_changes_payload_truncated')}
        </div>
      )}
    </div>
  );
}

/**
 * Collapsible card for an Operator activity (tool call, tool result,
 * thinking trace, or status phase).
 *
 * Header is always visible — kind icon, label, and a chevron when there's
 * a detail body to expand. Click anywhere on the header to toggle. Body
 * is rendered only when expanded; for tool calls and results the detail
 * is usually JSON or a long string, so it's monospace + pre-wrap and
 * gets its own copy button.
 *
 * Empty-detail activities (e.g., a `thinking` start with no payload yet)
 * collapse into a header-only line that doesn't accept clicks.
 */
function ActivityCard({ event, accent, fontSize }: ActivityCardProps) {
  const [expanded, setExpanded] = useState(event.kind === 'thinking' || event.kind === 'code_changes');
  const [copied, setCopied] = useState(false);

  const hasCodeChanges = event.kind === 'code_changes' && !!event.codeChanges;
  const hasDetail = hasCodeChanges || (!!event.detail && event.detail.trim().length > 0);
  const fs = fontSize ? `${fontSize - 1}px` : undefined;
  const detailFs = fontSize ? `${fontSize - 2}px` : '11px';

  const onCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    const text = event.detail
      ?? (event.codeChanges ? JSON.stringify(event.codeChanges, null, 2) : '');
    if (!text) return;
    if (!(await copyToClipboard(text))) return;
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  const kindGlyph =
    event.kind === 'tool_call'
      ? '▸' // ▸
      : event.kind === 'tool_result'
        ? '▾' // ▾
        : event.kind === 'thinking'
          ? '…' // …
          : event.kind === 'code_changes'
            ? '±' // ±
            : '•'; // •

  const accentColor = accent ?? 'var(--revka-text-faint)';

  return (
    <div
      className="my-0.5 rounded-[4px] border-l-2"
      style={{
        borderLeftColor: accentColor,
        background: expanded ? 'color-mix(in srgb, var(--revka-bg-surface) 60%, transparent)' : 'transparent',
      }}
    >
      <button
        type="button"
        onClick={() => hasDetail && setExpanded((prev) => !prev)}
        disabled={!hasDetail}
        className="flex w-full items-center gap-2 px-2 py-1 text-left transition-colors"
        style={{
          cursor: hasDetail ? 'pointer' : 'default',
          color: 'var(--revka-text-muted)',
          fontSize: fs,
        }}
      >
        {hasDetail ? (
          <ChevronRight
            className="h-3 w-3 shrink-0 transition-transform"
            style={{
              color: 'var(--revka-text-faint)',
              transform: expanded ? 'rotate(90deg)' : 'rotate(0deg)',
            }}
          />
        ) : (
          <span className="inline-block h-3 w-3 shrink-0 text-center" style={{ color: 'var(--revka-text-faint)' }}>
            {kindGlyph}
          </span>
        )}
        <span className="truncate font-mono">
          <span style={{ color: 'var(--revka-text-faint)' }}>sys {'>'} </span>
          {event.label}
        </span>
      </button>

      {hasDetail && expanded && (
        <div
          className="relative border-t px-3 py-2"
          style={{
            borderColor: 'var(--revka-border-soft)',
            background: 'var(--revka-bg-base)',
          }}
        >
          {hasCodeChanges && event.codeChanges ? (
            <CodeChangesDetail changes={event.codeChanges} fontSize={detailFs} />
          ) : looksLikeDiff(event.detail ?? '') ? (
            <DiffDetail detail={event.detail ?? ''} fontSize={detailFs} />
          ) : (
            <pre
              className="overflow-x-auto whitespace-pre-wrap break-words font-mono"
              style={{
                color: 'var(--revka-text-secondary)',
                fontSize: detailFs,
                lineHeight: 1.5,
                maxHeight: '20rem',
                overflowY: 'auto',
              }}
            >
              {event.detail}
            </pre>
          )}
          <button
            type="button"
            onClick={onCopy}
            aria-label={copied ? 'Copied' : 'Copy detail'}
            title={copied ? 'Copied' : 'Copy'}
            className="absolute right-1 top-1 inline-flex h-5 w-5 items-center justify-center rounded transition-colors hover:bg-white/5 focus:outline-none focus-visible:ring-1 focus-visible:ring-current"
            style={{ color: 'var(--revka-text-faint)' }}
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          </button>
        </div>
      )}
    </div>
  );
}

export default memo(ActivityCard);
