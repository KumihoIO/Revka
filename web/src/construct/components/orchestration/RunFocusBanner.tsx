import { Activity, ArrowDownRight } from 'lucide-react';
import type { WorkflowRunDetail } from '@/types/api';
import { useT } from '@/construct/hooks/useT';
import { expandedStepCount, loopProgressLabel } from '@/construct/lib/workflowProgress';
import StatusPill from '../ui/StatusPill';
import { formatLocalDateTime } from '../../lib/datetime';

export default function RunFocusBanner({
  run,
  active = false,
  label = 'Selected Run',
}: {
  run: WorkflowRunDetail | null;
  active?: boolean;
  label?: string;
}) {
  const { tpl } = useT();
  if (!run) return null;
  const expanded = expandedStepCount(run);
  const loopLabel = loopProgressLabel(run, tpl);

  return (
    <div
      className={`construct-run-focus-banner ${active ? 'is-active' : ''}`}
      aria-live="polite"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="construct-kicker">{label}</div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <div className="inline-flex items-center gap-2 text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
              <Activity className="h-4 w-4" style={{ color: 'var(--construct-signal-network)' }} />
              <span className="truncate">{run.workflow_name}</span>
            </div>
            <span className="text-xs font-mono" style={{ color: 'var(--construct-text-faint)' }}>
              {run.run_id.slice(0, 8)}
            </span>
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
            <span>{run.steps_completed || '0'} / {run.steps_total || '?'} steps</span>
            {expanded !== null ? (
              <>
                <span aria-hidden="true">•</span>
                <span>{tpl('runs.stats.expanded_steps', { count: expanded })}</span>
              </>
            ) : null}
            {loopLabel ? (
              <>
                <span aria-hidden="true">•</span>
                <span className="font-mono">{loopLabel}</span>
              </>
            ) : null}
            <span aria-hidden="true">•</span>
            <span>{formatLocalDateTime(run.started_at) || 'start time unavailable'}</span>
            {active ? (
              <>
                <span aria-hidden="true">•</span>
                <span className="inline-flex items-center gap-1 font-semibold" style={{ color: 'var(--construct-signal-live)' }}>
                  <ArrowDownRight className="h-3.5 w-3.5" />
                  Workspace updated below
                </span>
              </>
            ) : null}
          </div>
        </div>
        <StatusPill status={run.status} />
      </div>
    </div>
  );
}
