/**
 * ApprovalPanel — shown when a workflow step is paused awaiting human approval.
 *
 * Renders inside the workflow run page when a step has status === 'pending' and
 * StepRunInfo.awaiting_approval === true.
 */

import { useState } from 'react';
import { CheckCircle2, XCircle, Loader2, AlertTriangle, Clock } from 'lucide-react';
import { approveWorkflowRun } from '@/lib/api';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ApprovalPanelProps {
  runId: string;
  stepId: string;
  stepName: string;
  message: string;
  approveKeywords?: string[];
  rejectKeywords?: string[];
  onResolved?: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

type PanelState = 'idle' | 'confirming_reject' | 'loading' | 'approved' | 'rejected' | 'error';

export default function ApprovalPanel({
  runId,
  stepId,
  stepName,
  message,
  approveKeywords,
  rejectKeywords,
  onResolved,
}: ApprovalPanelProps) {
  const [panelState, setPanelState] = useState<PanelState>('idle');
  const [feedback, setFeedback] = useState('');
  const [errorMsg, setErrorMsg] = useState('');

  const hasApproveKeywords = approveKeywords && approveKeywords.length > 0;
  const hasRejectKeywords = rejectKeywords && rejectKeywords.length > 0;

  async function submit(approved: boolean, fb: string) {
    setPanelState('loading');
    setErrorMsg('');
    try {
      await approveWorkflowRun(runId, approved, fb || undefined);
      setPanelState(approved ? 'approved' : 'rejected');
      onResolved?.();
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setPanelState('error');
    }
  }

  function handleApprove() {
    void submit(true, '');
  }

  function handleRejectConfirm() {
    void submit(false, feedback);
  }

  // ── Resolved states ──────────────────────────────────────────────────────

  if (panelState === 'approved') {
    return (
      <div
        className="rounded-xl border-2 px-4 py-3 flex items-center gap-3"
        style={{ borderColor: '#34d399', background: 'rgba(52,211,153,0.08)' }}
      >
        <CheckCircle2 className="h-5 w-5 shrink-0" style={{ color: '#34d399' }} />
        <div>
          <p className="text-sm font-semibold" style={{ color: '#34d399' }}>Approved</p>
          <p className="text-[11px] mt-0.5" style={{ color: 'var(--pc-text-muted)' }}>
            Workflow is resuming.
          </p>
        </div>
      </div>
    );
  }

  if (panelState === 'rejected') {
    return (
      <div
        className="rounded-xl border-2 px-4 py-3 flex items-center gap-3"
        style={{ borderColor: '#f87171', background: 'rgba(248,113,113,0.08)' }}
      >
        <XCircle className="h-5 w-5 shrink-0" style={{ color: '#f87171' }} />
        <div>
          <p className="text-sm font-semibold" style={{ color: '#f87171' }}>Rejected</p>
          <p className="text-[11px] mt-0.5" style={{ color: 'var(--pc-text-muted)' }}>
            The workflow step has been rejected.
          </p>
        </div>
      </div>
    );
  }

  // ── Active approval card ─────────────────────────────────────────────────

  return (
    <div
      className="rounded-xl border-2 px-4 py-3 flex flex-col gap-3 animate-fade-in"
      style={{ borderColor: '#f59e0b', background: 'rgba(245,158,11,0.07)' }}
    >
      {/* Header */}
      <div className="flex items-start gap-2">
        <Clock className="h-4 w-4 shrink-0 mt-0.5" style={{ color: '#f59e0b' }} />
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: '#f59e0b' }}>
            Awaiting Approval
          </p>
          <p className="text-sm font-medium mt-0.5 truncate" style={{ color: 'var(--pc-text-primary)' }}>
            {stepName}
          </p>
        </div>
      </div>

      {/* Message */}
      {message && (
        <p className="text-[12px] leading-relaxed" style={{ color: 'var(--pc-text-secondary)' }}>
          {message}
        </p>
      )}

      {/* Keyword hints */}
      {(hasApproveKeywords || hasRejectKeywords) && (
        <div className="flex flex-col gap-1">
          {hasApproveKeywords && (
            <p className="text-[10px]" style={{ color: 'var(--pc-text-faint)' }}>
              <span style={{ color: '#34d399' }}>Approve keywords:</span>{' '}
              {approveKeywords!.join(', ')}
            </p>
          )}
          {hasRejectKeywords && (
            <p className="text-[10px]" style={{ color: 'var(--pc-text-faint)' }}>
              <span style={{ color: '#f87171' }}>Reject keywords:</span>{' '}
              {rejectKeywords!.join(', ')}
            </p>
          )}
        </div>
      )}

      {/* Error state */}
      {panelState === 'error' && errorMsg && (
        <div className="flex items-center gap-2 rounded-lg px-3 py-2" style={{ background: 'rgba(248,113,113,0.12)' }}>
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" style={{ color: '#f87171' }} />
          <p className="text-[11px]" style={{ color: '#f87171' }}>{errorMsg}</p>
        </div>
      )}

      {/* Reject feedback textarea */}
      {panelState === 'confirming_reject' && (
        <div className="flex flex-col gap-2">
          <label className="text-[11px]" style={{ color: 'var(--pc-text-muted)' }}>
            Rejection feedback (required)
          </label>
          <textarea
            className="w-full rounded-lg border px-3 py-2 text-[12px] resize-none focus:outline-none focus:ring-1"
            style={{
              borderColor: 'var(--pc-border)',
              background: 'var(--pc-bg-base)',
              color: 'var(--pc-text-primary)',
              minHeight: '72px',
            }}
            placeholder="Explain why you're rejecting this step..."
            value={feedback}
            onChange={(e) => setFeedback(e.target.value)}
            autoFocus
          />
        </div>
      )}

      {/* Action buttons */}
      <div className="flex items-center gap-2">
        {panelState === 'loading' ? (
          <div className="flex items-center gap-2" style={{ color: 'var(--pc-text-muted)' }}>
            <Loader2 className="h-4 w-4 animate-spin" />
            <span className="text-[12px]">Processing...</span>
          </div>
        ) : panelState === 'confirming_reject' ? (
          <>
            <button
              type="button"
              disabled={!feedback.trim()}
              onClick={handleRejectConfirm}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
              style={{ background: 'rgba(248,113,113,0.18)', color: '#f87171' }}
            >
              <XCircle className="h-3.5 w-3.5" />
              Confirm Reject
            </button>
            <button
              type="button"
              onClick={() => { setPanelState('idle'); setFeedback(''); }}
              className="px-3 py-1.5 rounded-lg text-[12px]"
              style={{ color: 'var(--pc-text-muted)', background: 'var(--pc-bg-elevated)' }}
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={handleApprove}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium"
              style={{ background: 'rgba(52,211,153,0.18)', color: '#34d399' }}
            >
              <CheckCircle2 className="h-3.5 w-3.5" />
              Approve
            </button>
            <button
              type="button"
              onClick={() => setPanelState('confirming_reject')}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-medium"
              style={{ background: 'rgba(248,113,113,0.18)', color: '#f87171' }}
            >
              <XCircle className="h-3.5 w-3.5" />
              Reject
            </button>
          </>
        )}
        <span className="ml-auto text-[10px] font-mono" style={{ color: 'var(--pc-text-faint)' }}>
          {stepId}
        </span>
      </div>
    </div>
  );
}
