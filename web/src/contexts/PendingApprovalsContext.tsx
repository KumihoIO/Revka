import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { SSEClient } from '@/lib/sse';

export interface PendingApproval {
  runId: string;
  stepId: string;
  workflowName: string;
  title: string;
  message: string;
  timestamp: string;
}

export interface ApprovalToast extends PendingApproval {
  /** Monotonic id for the toast stack so entries are unique per arrival. */
  toastId: number;
}

interface PendingApprovalsContextValue {
  pending: PendingApproval[];
  toasts: ApprovalToast[];
  dismissToast: (toastId: number) => void;
  /** Remove a pending approval by runId (use when the backend no longer has
   *  the run, e.g. daemon restart dropped the in-memory registry or the run
   *  was deleted). */
  dismiss: (runId: string) => void;
}

const SESSION_KEY = 'revka-pending-approvals-v1';

const PendingApprovalsContext = createContext<PendingApprovalsContextValue>({
  pending: [],
  toasts: [],
  dismissToast: () => {},
  dismiss: () => {},
});

export const usePendingApprovals = () => useContext(PendingApprovalsContext);

function loadPending(): PendingApproval[] {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed as PendingApproval[];
    }
  } catch {
    // ignore
  }
  return [];
}

function savePending(items: PendingApproval[]) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(items));
  } catch {
    // ignore
  }
}

export function PendingApprovalsProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingApproval[]>(loadPending);
  const [toasts, setToasts] = useState<ApprovalToast[]>([]);
  const toastIdRef = useRef(0);

  useEffect(() => {
    savePending(pending);
  }, [pending]);

  const dismissToast = useCallback((toastId: number) => {
    setToasts((prev) => prev.filter((t) => t.toastId !== toastId));
  }, []);

  const dismiss = useCallback((runId: string) => {
    if (!runId) return;
    setPending((prev) => prev.filter((p) => p.runId !== runId));
    setToasts((prev) => prev.filter((t) => t.runId !== runId));
  }, []);

  useEffect(() => {
    const sse = new SSEClient();

    sse.onEvent = (raw: any) => {
      if (raw.type === 'human_approval_request') {
        const entry: PendingApproval = {
          runId: String(raw.run_id ?? ''),
          stepId: String(raw.step_id ?? ''),
          workflowName: String(raw.workflow_name ?? ''),
          title: String(raw.title ?? 'Human approval needed'),
          message: String(raw.message ?? ''),
          timestamp: String(raw.timestamp ?? new Date().toISOString()),
        };
        if (!entry.runId) return;
        setPending((prev) => {
          if (prev.some((p) => p.runId === entry.runId && p.stepId === entry.stepId)) return prev;
          return [...prev, entry];
        });
        const toastId = ++toastIdRef.current;
        setToasts((prev) => [...prev, { ...entry, toastId }]);
        // Auto-dismiss toast after 8s; the persistent badge keeps it discoverable.
        setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.toastId !== toastId));
        }, 8000);
        return;
      }
      if (raw.type === 'human_approval_resolved') {
        const runId = String(raw.run_id ?? '');
        if (!runId) return;
        setPending((prev) => prev.filter((p) => p.runId !== runId));
        return;
      }
    };

    sse.connect();
    return () => sse.disconnect();
  }, []);

  const value = useMemo(
    () => ({ pending, toasts, dismissToast, dismiss }),
    [pending, toasts, dismissToast, dismiss],
  );

  return (
    <PendingApprovalsContext.Provider value={value}>
      {children}
    </PendingApprovalsContext.Provider>
  );
}

export default PendingApprovalsContext;
