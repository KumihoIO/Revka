import { useState, useRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { ShieldAlert } from 'lucide-react';
import { usePendingApprovals } from '@/contexts/PendingApprovalsContext';

export default function ApprovalBadge() {
  const { pending } = usePendingApprovals();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  if (pending.length === 0) return null;

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        className="revka-button relative px-2 py-1 text-xs"
        style={{
          background: 'var(--revka-signal-live-soft)',
          color: 'var(--revka-signal-live)',
          borderColor: 'var(--revka-signal-live)',
        }}
        onClick={() => setOpen((v) => !v)}
        aria-label={`${pending.length} pending approval${pending.length === 1 ? '' : 's'}`}
        title={`${pending.length} pending approval${pending.length === 1 ? '' : 's'}`}
      >
        <ShieldAlert className="h-4 w-4" />
        <span className="ml-1 font-semibold tabular-nums">{pending.length}</span>
      </button>
      {open ? (
        <div
          className="revka-panel absolute right-0 z-40 mt-2 w-80 p-2 shadow-lg"
          style={{ background: 'var(--revka-surface-raised, var(--revka-surface))' }}
        >
          <div className="revka-kicker mb-1 px-1 text-[10px]">Pending approvals</div>
          <ul className="max-h-80 space-y-1 overflow-auto">
            {pending.map((p) => (
              <li key={`${p.runId}:${p.stepId}`}>
                <Link
                  to={`/runs?run=${encodeURIComponent(p.runId)}`}
                  className="flex flex-col rounded px-2 py-1.5 text-xs hover:bg-[var(--revka-surface-hover,rgba(255,255,255,0.04))]"
                  onClick={() => setOpen(false)}
                >
                  <span className="font-medium truncate">
                    {p.workflowName || p.title || 'Workflow'}
                  </span>
                  <span className="truncate" style={{ color: 'var(--revka-text-secondary)' }}>
                    {p.message || `Step ${p.stepId}`}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
