import type { ReactNode } from 'react';
import { Activity, DollarSign, Radio, ShieldCheck, Users } from 'lucide-react';
import type { AuditVerifyResponse, ChannelDetail, CostSummary, Session, WorkflowRunSummary } from '@/types/api';
import Panel from '../ui/Panel';
import StatusPill from '../ui/StatusPill';

interface DashboardMetricStripProps {
  definitionsCount?: number;
  activeRuns?: number;
  totalRuns?: number;
  error?: string | null;
}

export function DashboardMetricStrip({
  definitionsCount,
  activeRuns,
  totalRuns,
  error,
}: DashboardMetricStripProps) {
  if (error) {
    return (
      <p className="mt-4 text-sm" style={{ color: 'var(--revka-status-danger)' }}>
        Failed to load workflow dashboard: {error}
      </p>
    );
  }

  return (
    <div className="mt-5 grid gap-4 md:grid-cols-3">
      <DashboardStat label="Definitions" value={definitionsCount ?? '...'} />
      <DashboardStat label="Active Runs" value={activeRuns ?? '...'} />
      <DashboardStat label="Total Runs" value={totalRuns ?? '...'} />
    </div>
  );
}

function DashboardStat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="revka-dashboard-stat">
      <div className="revka-kicker">{label}</div>
      <div className="revka-metric-value mt-2">{value}</div>
    </div>
  );
}

interface CommandBandCardProps {
  selectedRunStatus?: string | null;
  audit: AuditVerifyResponse | null;
  provider?: string | null;
  model?: string | null;
}

export function CommandBandCard({
  selectedRunStatus,
  audit,
  provider,
  model,
}: CommandBandCardProps) {
  return (
    <Panel className="p-4" skinSlot="commandBand">
      <div className="revka-kicker">Command Band</div>
      <div className="mt-3 grid gap-2 text-sm">
        <StatusPill status={selectedRunStatus ?? 'running'} />
        <span className="revka-status-pill">
          <ShieldCheck className="h-3.5 w-3.5" />
          {audit?.verified ? 'Trust verified' : 'Trust check pending'}
        </span>
        <div className="rounded-[8px] border p-3" style={{ borderColor: 'var(--revka-border-soft)' }}>
          <div className="revka-kicker">Runtime</div>
          <div className="mt-2 text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
            {provider ?? 'Unknown provider'}
          </div>
          <div className="mt-1 text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
            {model || 'No model reported'}
          </div>
        </div>
      </div>
    </Panel>
  );
}

interface AgentRailCardProps {
  sessions: Session[];
  channels: ChannelDetail[];
  activeSessionCount: number;
  activeChannelCount: number;
}

export function AgentRailCard({
  sessions,
  channels,
  activeSessionCount,
  activeChannelCount,
}: AgentRailCardProps) {
  return (
    <Panel className="p-4" variant="secondary" skinSlot="agentRail">
      <div className="revka-kicker">Agent Rail</div>
      <div className="mt-3 space-y-3">
        <MiniMetricCard
          icon={<Users className="h-4 w-4" style={{ color: 'var(--revka-signal-live)' }} />}
          label="Sessions"
          value={activeSessionCount}
          detail={`${sessions.length} tracked conversations`}
        />
        <MiniMetricCard
          icon={<Radio className="h-4 w-4" style={{ color: 'var(--revka-signal-network)' }} />}
          label="Channels"
          value={activeChannelCount}
          detail={`${channels.length} integrated surfaces`}
        />
        <Panel className="p-3" variant="utility">
          <div className="revka-kicker">Recent session activity</div>
          <div className="mt-2 space-y-2">
            {sessions.slice(0, 3).map((session) => (
              <div key={session.id} className="flex items-center justify-between gap-2 text-xs">
                <span className="truncate" style={{ color: 'var(--revka-text-primary)' }}>{session.channel}</span>
                <StatusPill status={session.status} />
              </div>
            ))}
            {sessions.length === 0 ? (
              <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>No active sessions.</div>
            ) : null}
          </div>
        </Panel>
      </div>
    </Panel>
  );
}

interface RiskRailCardProps {
  audit: AuditVerifyResponse | null;
  cost: CostSummary | null;
  degradedComponentCount: number;
}

export function RiskRailCard({
  audit,
  cost,
  degradedComponentCount,
}: RiskRailCardProps) {
  return (
    <Panel className="p-4" variant="secondary" skinSlot="riskRail">
      <div className="revka-kicker">Risk Rail</div>
      <div className="mt-3 space-y-3">
        <MiniMetricCard
          icon={<ShieldCheck className="h-4 w-4" style={{ color: audit?.verified ? 'var(--revka-status-success)' : 'var(--revka-status-warning)' }} />}
          label="Audit chain"
          value={audit?.verified ? 'Verified' : 'Pending verification'}
        />
        <MiniMetricCard
          icon={<DollarSign className="h-4 w-4" style={{ color: 'var(--revka-signal-network)' }} />}
          label="Spend"
          value={`$${cost?.daily_cost_usd?.toFixed(2) ?? '...'}`}
          detail={`daily / $${cost?.monthly_cost_usd?.toFixed(2) ?? '...'} monthly`}
        />
        <MiniMetricCard
          icon={<Activity className="h-4 w-4" style={{ color: degradedComponentCount > 0 ? 'var(--revka-status-warning)' : 'var(--revka-status-success)' }} />}
          label="Component health"
          value={degradedComponentCount > 0 ? `${degradedComponentCount} degraded` : 'All healthy'}
        />
      </div>
    </Panel>
  );
}

interface RecentRunsRailCardProps {
  runs: WorkflowRunSummary[];
  onSelectRun: (runId: string) => void;
  selectedRunId?: string | null;
  footer?: ReactNode;
}

export function RecentRunsRailCard({ runs, onSelectRun, selectedRunId, footer }: RecentRunsRailCardProps) {
  return (
    <Panel className="p-4" variant="utility" skinSlot="recentRuns">
      <div className="flex items-center gap-2">
        <Activity className="h-4 w-4" style={{ color: 'var(--revka-signal-network)' }} />
        <span className="text-sm font-medium">Recent runs</span>
      </div>
      {/* Cap the run-button list at ~24rem so the card stays a predictable
          size even when 4 runs come back. Without this cap the card grew
          to ~400px and overflowed the right rail's viewport allotment,
          making the dashboard layout jitter as runs arrived. The list
          scrolls internally once it overflows. */}
      <div className="mt-3 max-h-[24rem] space-y-2 overflow-y-auto pr-1">
        {runs.slice(0, 4).map((run) => (
          <button
            key={run.run_id}
            type="button"
            onClick={() => onSelectRun(run.run_id)}
            data-active={run.run_id === selectedRunId}
            className="revka-run-selection-card"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium">{run.workflow_name}</span>
              <StatusPill status={run.status} />
            </div>
            {run.run_id === selectedRunId ? (
              <div className="mt-2 text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--revka-signal-selected)' }}>
                Active selection
              </div>
            ) : null}
            <div className="mt-2 text-xs font-mono" style={{ color: 'var(--revka-text-faint)' }}>
              {run.run_id.slice(0, 8)}
            </div>
          </button>
        ))}
        {runs.length === 0 ? (
          <div className="text-sm" style={{ color: 'var(--revka-text-faint)' }}>Loading recent runs…</div>
        ) : null}
      </div>
      {footer ? <div className="mt-4">{footer}</div> : null}
    </Panel>
  );
}

interface MiniMetricCardProps {
  icon: ReactNode;
  label: string;
  value: ReactNode;
  detail?: ReactNode;
}

function MiniMetricCard({ icon, label, value, detail }: MiniMetricCardProps) {
  return (
    <div className="revka-mini-metric-card rounded-[8px] border p-3" style={{ borderColor: 'var(--revka-border-soft)' }}>
      <div className="flex items-center gap-2">
        {icon}
        <span className="text-sm font-medium" style={{ color: 'var(--revka-text-primary)' }}>{label}</span>
      </div>
      <div className="mt-2 text-2xl font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
        {value}
      </div>
      {detail ? (
        <div className="mt-1 text-xs" style={{ color: 'var(--revka-text-secondary)' }}>
          {detail}
        </div>
      ) : null}
    </div>
  );
}
