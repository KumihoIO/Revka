/**
 * RevisionHistoryStrip — slim horizontal strip rendered above the React
 * Flow canvas in WorkflowEditor.
 *
 * Lists Kumiho revisions for the open workflow as small capsules. The
 * currently-published revision is rendered with an accent fill + "live"
 * dot. Older revisions are outlined / muted; clicking one opens a confirm
 * modal that calls POST /api/architect/republish on confirm.
 *
 * SSE: subscribes via `useRevisionEvents` to both
 * `workflow.revision.published` (a new revision was created) and
 * `workflow.revision.republished` (an existing revision was re-tagged) so
 * the strip's view always tracks the gateway's truth without a polling
 * loop. The editor's existing `useWorkflowEvents` handles auto-applying
 * the YAML — this strip only redraws.
 *
 * Frontend only. No new dependencies. Tokens only.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import { History, Loader2, X } from 'lucide-react';
import { ApiError, listRevisions, republishRevision } from '@/lib/api';
import type { RevisionSummary } from '@/types/api';
import { useRevisionEvents } from './useRevisionEvents';

interface RevisionHistoryStripProps {
  workflowKref: string;
  /** Called when the user successfully republishes a revision. The editor
   *  re-fetches via SSE auto-apply; the strip re-fetches itself too. */
  onRepublished?: (newPublishedKref: string) => void;
}

const MODAL_BACKDROP_Z = 9099;
const MODAL_PANEL_Z = 9100;

// ── Helpers ──────────────────────────────────────────────────────────────

function isCurrentlyPublished(rev: RevisionSummary): boolean {
  return rev.tags.includes('published');
}

function rationaleOf(rev: RevisionSummary): string | null {
  // Prefer metadata.rationale (in case future code stores it there);
  // fall back to scanning tags for a `rationale:<text>` entry, which is
  // how Stage A.1's `revise_workflow` actually persists it today.
  const fromMeta = rev.metadata?.rationale;
  if (typeof fromMeta === 'string' && fromMeta.trim().length > 0) {
    return fromMeta.trim();
  }
  for (const tag of rev.tags) {
    if (tag.startsWith('rationale:')) {
      const value = tag.slice('rationale:'.length).trim();
      return value.length > 0 ? value : null;
    }
  }
  return null;
}

/** Compact relative-time formatter ("just now", "2m ago", "1h ago",
 *  "3d ago"). For very old timestamps (>30d) falls back to a localized
 *  date string so the strip doesn't show implausible "92d ago". */
function relativeTime(iso: string | null): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '—';
  const deltaMs = Date.now() - t;
  if (deltaMs < 0) return 'just now';
  const sec = Math.round(deltaMs / 1000);
  if (sec < 45) return 'just now';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  try {
    return new Date(t).toLocaleDateString();
  } catch {
    return `${day}d ago`;
  }
}

function fullTimestamp(iso: string | null): string {
  if (!iso) return 'unknown time';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  try {
    return new Date(t).toLocaleString();
  } catch {
    return iso;
  }
}

// ── Confirm-revert modal ─────────────────────────────────────────────────

interface RevertModalProps {
  open: boolean;
  revision: RevisionSummary | null;
  onCancel: () => void;
  onConfirm: () => void | Promise<void>;
  submitting: boolean;
  error: string | null;
}

function RevertConfirmModal({
  open,
  revision,
  onCancel,
  onConfirm,
  submitting,
  error,
}: RevertModalProps) {
  // Esc closes (unless mid-request).
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!submitting) onCancel();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, submitting, onCancel]);

  if (!open || !revision) return null;
  if (typeof document === 'undefined') return null;

  const rationale = rationaleOf(revision);

  const handleBackdropClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget && !submitting) onCancel();
  };

  const content = (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Revert to revision r${revision.number}`}
      onClick={handleBackdropClick}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: MODAL_BACKDROP_Z,
        background: 'rgba(0, 0, 0, 0.48)',
        backdropFilter: 'blur(6px)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: '14vh',
      }}
    >
      <div
        className="construct-panel"
        data-variant="primary"
        onClick={(e) => e.stopPropagation()}
        style={{
          width: 'min(460px, calc(100vw - 32px))',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 14,
          borderColor: 'var(--construct-border-strong)',
          boxShadow: '0 32px 80px rgba(0,0,0,0.48)',
          zIndex: MODAL_PANEL_Z,
          overflow: 'hidden',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '14px 16px',
            borderBottom: '1px solid var(--construct-border-soft)',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 28,
              height: 28,
              borderRadius: 8,
              background:
                'color-mix(in srgb, var(--construct-status-warning) 28%, transparent)',
              color: 'var(--construct-status-warning)',
            }}
          >
            <History size={15} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontSize: 13,
                fontWeight: 700,
                color: 'var(--construct-text-primary)',
              }}
            >
              Revert to revision r{revision.number}?
            </div>
            {rationale ? (
              <div
                style={{
                  fontSize: 11,
                  color: 'var(--construct-text-faint)',
                  marginTop: 2,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
                title={rationale}
              >
                {rationale}
              </div>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => {
              if (!submitting) onCancel();
            }}
            disabled={submitting}
            aria-label="Close"
            style={{
              background: 'transparent',
              border: 0,
              padding: 4,
              cursor: submitting ? 'not-allowed' : 'pointer',
              color: 'var(--construct-text-faint)',
              opacity: submitting ? 0.5 : 1,
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div
          style={{
            padding: '14px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 10,
          }}
        >
          <p
            style={{
              fontSize: 12.5,
              lineHeight: 1.5,
              color: 'var(--construct-text-secondary)',
              margin: 0,
            }}
          >
            This will create a new published revision with revision r
            {revision.number}'s content. Other tabs viewing this workflow
            will auto-update.
          </p>

          {error ? (
            <div
              role="alert"
              style={{
                padding: '8px 10px',
                borderRadius: 8,
                background:
                  'color-mix(in srgb, var(--construct-status-danger) 14%, transparent)',
                border: '1px solid var(--construct-status-danger)',
                color: 'var(--construct-status-danger)',
                fontSize: 11.5,
              }}
            >
              {error}
            </div>
          ) : null}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '12px 16px',
            borderTop: '1px solid var(--construct-border-soft)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: 8,
          }}
        >
          <button
            type="button"
            onClick={() => {
              if (!submitting) onCancel();
            }}
            disabled={submitting}
            className="construct-button"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => {
              void onConfirm();
            }}
            disabled={submitting}
            className="construct-button"
            data-variant="primary"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              background:
                'color-mix(in srgb, var(--construct-status-warning) 80%, transparent)',
              borderColor: 'var(--construct-status-warning)',
              color: 'var(--construct-text-primary)',
            }}
          >
            {submitting && <Loader2 size={13} className="animate-spin" />}
            {submitting ? 'Reverting…' : 'Revert'}
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(content, document.body);
}

// ── Strip ────────────────────────────────────────────────────────────────

export default function RevisionHistoryStrip({
  workflowKref,
  onRepublished,
}: RevisionHistoryStripProps) {
  const [revisions, setRevisions] = useState<RevisionSummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [pickedRevision, setPickedRevision] = useState<RevisionSummary | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Track in-flight fetches so a fast burst of SSE events doesn't dogpile
  // the gateway. We always honor the most recent trigger by re-checking
  // after the in-flight fetch settles.
  const inFlightRef = useRef<Promise<void> | null>(null);
  const pendingRef = useRef(false);

  const fetchRevisions = useCallback(async (): Promise<void> => {
    if (inFlightRef.current) {
      pendingRef.current = true;
      return inFlightRef.current;
    }

    const run = (async () => {
      try {
        const res = await listRevisions(workflowKref);
        setRevisions(res.revisions ?? []);
        setLoadError(null);
      } catch (err) {
        const msg =
          err instanceof ApiError
            ? err.message.replace(/^API \d+: /, '') || 'Failed to load revisions'
            : err instanceof Error
              ? err.message
              : 'Failed to load revisions';
        setLoadError(msg);
      }
    })();

    inFlightRef.current = run;
    try {
      await run;
    } finally {
      inFlightRef.current = null;
      if (pendingRef.current) {
        pendingRef.current = false;
        // Coalesced re-fetch — fire-and-forget; errors are surfaced via state.
        void fetchRevisions();
      }
    }
  }, [workflowKref]);

  // Initial fetch + reset when the open workflow changes.
  useEffect(() => {
    setRevisions([]);
    setLoadError(null);
    void fetchRevisions();
  }, [fetchRevisions]);

  // SSE — re-fetch when a revision event fires for this workflow.
  useRevisionEvents({
    workflowKref,
    onChange: () => {
      void fetchRevisions();
    },
  });

  const handleCapsuleClick = useCallback((rev: RevisionSummary) => {
    if (isCurrentlyPublished(rev)) return; // no-op for the live one
    setSubmitError(null);
    setPickedRevision(rev);
  }, []);

  const handleCancel = useCallback(() => {
    if (submitting) return;
    setPickedRevision(null);
    setSubmitError(null);
  }, [submitting]);

  const handleConfirm = useCallback(async () => {
    if (!pickedRevision) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const result = await republishRevision(pickedRevision.kref);
      setPickedRevision(null);
      // Re-fetch immediately — don't wait for the SSE round-trip. The SSE
      // event will trigger another (coalesced) fetch shortly, which is a
      // harmless no-op against current state.
      void fetchRevisions();
      onRepublished?.(result.revision_kref);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message.replace(/^API \d+: /, '') || 'Republish failed'
          : err instanceof Error
            ? err.message
            : 'Republish failed';
      // eslint-disable-next-line no-console
      console.error('[RevisionHistoryStrip] republish failed:', err);
      setSubmitError(msg);
    } finally {
      setSubmitting(false);
    }
  }, [pickedRevision, fetchRevisions, onRepublished]);

  // Sort newest first. The gateway already returns in created-at desc but
  // we sort defensively in case the upstream order changes.
  const sortedRevisions = useMemo(() => {
    const copy = [...revisions];
    copy.sort((a, b) => b.number - a.number);
    return copy;
  }, [revisions]);

  return (
    <>
      <div
        role="region"
        aria-label="Workflow revisions"
        style={{
          position: 'relative',
          height: 36,
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          padding: '0 12px',
          borderTop: '1px solid var(--pc-border)',
          borderBottom: '1px solid var(--pc-border)',
          background: 'var(--pc-bg-elevated)',
          flexShrink: 0,
        }}
      >
        <span className="construct-kicker" style={{ flexShrink: 0 }}>
          Revisions
        </span>

        <div
          className="construct-rev-strip-scroller"
          style={{
            flex: 1,
            minWidth: 0,
            display: 'flex',
            alignItems: 'center',
            gap: 6,
            overflowX: 'auto',
            overflowY: 'hidden',
            scrollbarWidth: 'none',
          }}
        >
          {loadError ? (
            <span
              style={{
                fontSize: 11,
                color: 'var(--construct-status-danger)',
              }}
              title={loadError}
            >
              {loadError}
            </span>
          ) : sortedRevisions.length === 0 ? (
            <span
              style={{
                fontSize: 11,
                color: 'var(--construct-text-faint)',
                fontStyle: 'italic',
              }}
            >
              No revisions yet
            </span>
          ) : (
            sortedRevisions.map((rev) => {
              const live = isCurrentlyPublished(rev);
              const rationale = rationaleOf(rev);
              const tooltip = [
                `r${rev.number}`,
                fullTimestamp(rev.created_at),
                rationale ? `— ${rationale}` : null,
              ]
                .filter(Boolean)
                .join(' · ');
              return (
                <button
                  key={rev.kref}
                  type="button"
                  onClick={() => handleCapsuleClick(rev)}
                  disabled={live}
                  className="construct-rev-capsule"
                  data-live={live ? 'true' : 'false'}
                  title={tooltip}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    height: 22,
                    padding: '0 8px',
                    borderRadius: 999,
                    fontSize: 10.5,
                    fontFamily:
                      'var(--pc-font-mono, ui-monospace, monospace)',
                    fontWeight: 600,
                    flexShrink: 0,
                    cursor: live ? 'default' : 'pointer',
                    transition: 'transform 120ms ease, background 120ms ease',
                    border: live
                      ? '1px solid var(--pc-accent)'
                      : '1px solid var(--pc-border)',
                    background: live
                      ? 'var(--pc-accent-glow)'
                      : 'transparent',
                    color: live
                      ? 'var(--pc-accent)'
                      : 'var(--construct-text-muted)',
                    animation: 'construct-rev-capsule-in 240ms ease-out both',
                  }}
                >
                  {live ? (
                    <span
                      aria-hidden
                      style={{
                        display: 'inline-block',
                        width: 6,
                        height: 6,
                        borderRadius: 999,
                        background: 'var(--construct-status-success)',
                        boxShadow:
                          '0 0 6px color-mix(in srgb, var(--construct-status-success) 60%, transparent)',
                      }}
                    />
                  ) : null}
                  <span>r{rev.number}</span>
                  <span
                    style={{
                      color: live
                        ? 'var(--pc-accent)'
                        : 'var(--construct-text-faint)',
                      fontWeight: 500,
                    }}
                  >
                    {relativeTime(rev.created_at)}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </div>

      <RevertConfirmModal
        open={pickedRevision !== null}
        revision={pickedRevision}
        onCancel={handleCancel}
        onConfirm={handleConfirm}
        submitting={submitting}
        error={submitError}
      />

      <style>{`
        .construct-rev-strip-scroller::-webkit-scrollbar {
          height: 0;
          background: transparent;
        }
        .construct-rev-strip-scroller:hover {
          scrollbar-width: thin;
        }
        .construct-rev-strip-scroller:hover::-webkit-scrollbar {
          height: 6px;
        }
        .construct-rev-strip-scroller:hover::-webkit-scrollbar-thumb {
          background: var(--pc-border);
          border-radius: 3px;
        }
        .construct-rev-capsule:not(:disabled):hover {
          transform: translateY(-1px);
          background: color-mix(in srgb, var(--pc-accent-glow) 35%, transparent);
          color: var(--construct-text-primary);
        }
        @keyframes construct-rev-capsule-in {
          from { opacity: 0; transform: translateY(2px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </>
  );
}
