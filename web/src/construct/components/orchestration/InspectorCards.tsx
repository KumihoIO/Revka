import type { ReactNode } from 'react';
import { Eye } from 'lucide-react';
import { Link } from 'react-router-dom';
import type { TaskDefinition } from '@/construct/components/workflows/yamlSync';
import type { TeamDefinition, TeamMember, WorkflowDefinition, WorkflowRunDetail, WorkflowRunSummary, WorkflowStepDetail } from '@/types/api';
import { parseWorkflowMeta } from '@/construct/components/workflows/yamlSync';
import Panel from '../ui/Panel';
import StatusPill from '../ui/StatusPill';
import { formatLocalDateTime } from '../../lib/datetime';

interface WorkflowMetadataCardProps {
  workflow: WorkflowDefinition | null;
}

export function WorkflowMetadataCard({ workflow }: WorkflowMetadataCardProps) {
  const meta = workflow ? parseWorkflowMeta(workflow.definition) : null;

  return (
    <Panel className="p-4" variant="utility">
      <div className="construct-kicker">Workflow Metadata</div>
      {workflow ? (
        <div className="mt-3 space-y-2 text-sm">
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Version</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{workflow.version}</span></div>
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Steps</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{workflow.steps}</span></div>
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Tags</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{workflow.tags.join(', ') || 'None'}</span></div>
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Triggers</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{meta?.triggers.length ?? 0}</span></div>
        </div>
      ) : (
        <div className="mt-3 text-sm" style={{ color: 'var(--construct-text-faint)' }}>No workflow selected.</div>
      )}
    </Panel>
  );
}

interface SelectedTaskCardProps {
  task: TaskDefinition | null;
  step?: WorkflowStepDetail | null;
  title?: string;
  emptyText: string;
  footer?: ReactNode;
  /** Open the step's full output artifact in a viewer modal. */
  onViewArtifact?: (step: WorkflowStepDetail) => void;
}

export function SelectedTaskCard({ task, step, title = 'Selected Node', emptyText, footer, onViewArtifact }: SelectedTaskCardProps) {
  return (
    <Panel className="p-4" variant="secondary">
      <div className="construct-kicker">{title}</div>
      {task ? (
        <div className="mt-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
              {task.name || task.id}
            </div>
            {step ? <StatusPill status={step.status} /> : null}
          </div>
          <div className="text-xs uppercase tracking-[0.12em]" style={{ color: 'var(--construct-text-faint)' }}>
            {task.type}
          </div>
          <p className="text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>
            {task.description || 'No description provided.'}
          </p>
          {!step ? (
            <div className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
              Depends on: {task.depends_on.join(', ') || 'none'}
            </div>
          ) : null}
          {step?.output_preview ? (
            <div className="rounded-[12px] border p-3 text-xs leading-6" style={{ borderColor: 'var(--construct-border-soft)', color: 'var(--construct-text-secondary)' }}>
              <div className="mb-2 flex items-center justify-between gap-2">
                <div className="text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--construct-text-faint)' }}>
                  Output preview
                </div>
                {step.artifact_path && onViewArtifact ? (
                  <button
                    type="button"
                    onClick={() => onViewArtifact(step)}
                    className="inline-flex items-center gap-1 rounded-[6px] px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider transition"
                    style={{
                      background: 'var(--construct-bg-elevated)',
                      color: 'var(--construct-text-secondary)',
                      border: '1px solid var(--construct-border-strong)',
                    }}
                  >
                    <Eye className="h-3 w-3" />
                    View full
                  </button>
                ) : null}
              </div>
              <pre className="whitespace-pre-wrap" style={{ fontFamily: 'var(--pc-font-mono)' }}>{step.output_preview}</pre>
            </div>
          ) : null}
          {step ? (
            <div className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
              Agent: {step.agent_type || 'n/a'} {step.role ? `/ ${step.role}` : ''}
            </div>
          ) : null}
          {step?.skills?.length ? (
            <div className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
              Skills: {step.skills.join(', ')}
            </div>
          ) : null}
          {step?.agent_id ? (
            <div className="text-xs font-mono" style={{ color: 'var(--construct-text-faint)' }}>
              Agent ID: {step.agent_id}
            </div>
          ) : null}
          {footer ? <div className="pt-1">{footer}</div> : null}
        </div>
      ) : (
        <div className="mt-3 text-sm" style={{ color: 'var(--construct-text-faint)' }}>{emptyText}</div>
      )}
    </Panel>
  );
}

interface RunSummaryCardProps {
  run: WorkflowRunDetail | null;
  workflowHref?: string;
}

export function RunSummaryCard({ run, workflowHref }: RunSummaryCardProps) {
  return (
    <Panel className="p-4" variant="utility">
      <div className="construct-kicker">Run Summary</div>
      {run ? (
        <div className="mt-3 space-y-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <span style={{ color: 'var(--construct-text-faint)' }}>Status</span>
            <StatusPill status={run.status} />
          </div>
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Started</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{formatLocalDateTime(run.started_at) || '-'}</span></div>
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Completed</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{formatLocalDateTime(run.completed_at) || '-'}</span></div>
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Steps</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{run.steps_completed || '0'} / {run.steps_total || '?'}</span></div>
          {run.error ? (
            <div className="rounded-[12px] border p-3 text-xs" style={{ borderColor: 'color-mix(in srgb, var(--construct-status-danger) 28%, transparent)', color: 'var(--construct-status-danger)' }}>
              {run.error}
            </div>
          ) : null}
          {workflowHref ? (
            <Link
              to={workflowHref}
              className="inline-flex items-center gap-2 text-sm"
              style={{ color: 'var(--construct-signal-network)' }}
            >
              Open workflow definition
            </Link>
          ) : null}
        </div>
      ) : (
        <div className="mt-3 text-sm" style={{ color: 'var(--construct-text-faint)' }}>No run selected.</div>
      )}
    </Panel>
  );
}

interface RecentRunsCardProps {
  runs: WorkflowRunSummary[];
  workflowKref?: string | null;
  emptyText: string;
}

export function RecentRunsCard({ runs, workflowKref, emptyText }: RecentRunsCardProps) {
  return (
    <Panel className="p-4" variant="utility">
      <div className="construct-kicker">Recent Runs</div>
      <div className="mt-3 space-y-2">
        {runs.map((run) => (
          <Link
            key={run.run_id}
            to={`/runs?run=${encodeURIComponent(run.run_id)}&workflow=${encodeURIComponent(workflowKref ?? '')}`}
            className="block rounded-[12px] border p-3 transition-colors hover:bg-[var(--construct-signal-live-soft)]"
            style={{ borderColor: 'var(--construct-border-soft)' }}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="truncate text-sm font-medium" style={{ color: 'var(--construct-text-primary)' }}>
                {run.run_id.slice(0, 8)}
              </div>
              <StatusPill status={run.status} />
            </div>
            <div className="mt-2 text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
              {run.steps_completed || '0'} / {run.steps_total || '?'} steps
            </div>
          </Link>
        ))}
        {runs.length === 0 ? (
          <div className="text-sm" style={{ color: 'var(--construct-text-faint)' }}>{emptyText}</div>
        ) : null}
      </div>
    </Panel>
  );
}

interface TeamSummaryCardProps {
  team: TeamDefinition | null;
}

export function TeamSummaryCard({ team }: TeamSummaryCardProps) {
  return (
    <Panel className="p-4" variant="utility">
      <div className="construct-kicker">Team Summary</div>
      {team ? (
        <div className="mt-3 space-y-2 text-sm">
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Members</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{team.members.length || team.member_count || 0}</span></div>
          <div><span style={{ color: 'var(--construct-text-faint)' }}>Edges</span>: <span style={{ color: 'var(--construct-text-primary)' }}>{team.edges.length || team.edge_count || 0}</span></div>
          <p style={{ color: 'var(--construct-text-secondary)' }}>{team.description || 'No description.'}</p>
        </div>
      ) : (
        <div className="mt-3 text-sm" style={{ color: 'var(--construct-text-faint)' }}>No team selected.</div>
      )}
    </Panel>
  );
}

interface SelectedMemberCardProps {
  member: TeamMember | null;
  footer?: ReactNode;
}

export function SelectedMemberCard({ member, footer }: SelectedMemberCardProps) {
  return (
    <Panel className="p-4" variant="secondary">
      <div className="construct-kicker">Selected Member</div>
      {member ? (
        <div className="mt-3 space-y-2">
          <div className="text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>{member.name}</div>
          <div className="text-xs uppercase tracking-[0.12em]" style={{ color: 'var(--construct-text-faint)' }}>
            {member.role} / {member.agent_type}
          </div>
          <p className="text-sm leading-6" style={{ color: 'var(--construct-text-secondary)' }}>
            {member.identity}
          </p>
          <div className="text-xs" style={{ color: 'var(--construct-text-secondary)' }}>
            Expertise: {member.expertise.join(', ') || 'None'}
          </div>
          {footer ? <div className="pt-1">{footer}</div> : null}
        </div>
      ) : (
        <div className="mt-3 text-sm" style={{ color: 'var(--construct-text-faint)' }}>
          Select a team member in the topology graph to inspect details.
        </div>
      )}
    </Panel>
  );
}
