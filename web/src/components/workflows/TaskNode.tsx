import { Handle, Position, type NodeTypes } from '@xyflow/react';
import { Bot, Lock } from 'lucide-react';
import type { TaskNodeData } from '@/construct/components/workflows/yamlSync';
import { emitOpenAgentPicker } from '@/construct/components/workflows/stepEvents';

// Action → token mapping. Maps semantic intent to Construct CSS vars.
// Categories:
//   - "live"     → green/success/active work (research, deploy ops, build, completion)
//   - "network"  → blue/info/communication (notify, group_chat, agents, messaging)
//   - "warning"  → amber (gate, conditional, decision points)
//   - "danger"   → red (failure, errors)
//   - "accent"   → primary brand (code/edit operations)
//   - "muted"    → neutral (shell, plumbing, generic task)
type ActionTone = 'live' | 'network' | 'warning' | 'danger' | 'accent' | 'muted';

const ACTION_TONES: Record<string, ActionTone> = {
  code: 'accent',
  review: 'network',
  research: 'live',
  deploy: 'warning',
  test: 'network',
  build: 'warning',
  notify: 'network',
  approve: 'network',
  summarize: 'live',
  greet: 'network',
  human_input: 'warning',
  task: 'muted',
  // Executor step types
  agent: 'network',
  parallel: 'network',
  shell: 'muted',
  goto: 'warning',
  output: 'live',
  conditional: 'warning',
  group_chat: 'network',
  supervisor: 'warning',
  map_reduce: 'network',
  handoff: 'network',
  a2a: 'live',
  resolve: 'network',
  for_each: 'live',
};

function getActionTone(action: string): ActionTone {
  const key = action.toLowerCase().replace(/[^a-z]/g, '');
  for (const [prefix, tone] of Object.entries(ACTION_TONES)) {
    if (key.startsWith(prefix) || key.includes(prefix)) return tone;
  }
  return 'muted';
}

/** CSS var for the solid line/fill color associated with a tone. */
function toneColorVar(tone: ActionTone): string {
  switch (tone) {
    case 'live': return 'var(--construct-signal-live)';
    case 'network': return 'var(--construct-signal-network)';
    case 'warning': return 'var(--construct-status-warning)';
    case 'danger': return 'var(--construct-status-danger)';
    case 'accent': return 'var(--pc-accent)';
    case 'muted': return 'var(--construct-status-idle)';
  }
}

/** CSS var (or color-mix expression) for the soft tint matching the tone. */
function toneSoftVar(tone: ActionTone): string {
  switch (tone) {
    case 'live': return 'var(--construct-signal-live-soft)';
    case 'network': return 'var(--construct-signal-network-soft)';
    case 'warning': return 'color-mix(in srgb, var(--construct-status-warning) 16%, transparent)';
    case 'danger': return 'color-mix(in srgb, var(--construct-status-danger) 16%, transparent)';
    case 'accent': return 'var(--pc-accent-glow)';
    case 'muted': return 'color-mix(in srgb, var(--construct-status-idle) 16%, transparent)';
  }
}

// Agent hint tones
const HINT_TONES: Record<string, ActionTone> = {
  coder: 'accent',
  researcher: 'live',
  reviewer: 'network',
};

// Run status → tone
const STATUS_TONES: Record<string, ActionTone> = {
  completed: 'live',
  running: 'warning',
  failed: 'danger',
  pending: 'muted',
  skipped: 'muted',
};

// Agent type tones
const AGENT_TYPE_TONES: Record<string, ActionTone> = {
  claude: 'network',
  codex: 'warning',
};

function TaskNode({ id, data, selected }: { id: string; data: TaskNodeData; selected?: boolean }) {
  const tone = getActionTone(data.type);
  const color = toneColorVar(tone);
  const soft = toneSoftVar(tone);
  const isAgentStep = (data.type || 'agent') === 'agent';

  const openAgentPicker = (event: React.MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    const rect = event.currentTarget.getBoundingClientRect();
    emitOpenAgentPicker({ taskId: id, anchorRect: rect });
  };

  return (
    <div
      className={`px-4 py-3 rounded-xl shadow-lg transition-all${data.justUpdated ? ' step-updated-pulse' : ''}`}
      style={{
        position: 'relative',
        background: selected
          ? `linear-gradient(135deg, ${soft} 0%, ${soft} 40%, var(--construct-bg-panel-strong) 100%)`
          : `linear-gradient(135deg, ${soft} 0%, var(--construct-bg-elevated) 50%, var(--construct-bg-surface) 100%)`,
        border: `2px solid ${selected ? color : 'var(--construct-border-strong)'}`,
        minWidth: 220,
        maxWidth: 280,
        boxShadow: selected
          ? `0 0 20px ${soft}, inset 0 1px 0 ${soft}`
          : `0 4px 12px rgba(0, 0, 0, 0.3), inset 0 1px 0 var(--construct-border-soft)`,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: color, width: 10, height: 10 }} />

      {/* Task name */}
      <div
        className="text-sm font-bold truncate"
        style={{ color: selected ? 'var(--construct-signal-selected)' : 'var(--pc-text-primary)' }}
      >
        {data.name || data.taskId}
      </div>

      {/* Type badge */}
      <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
        <span
          className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider"
          style={{ background: soft, color }}
        >
          {data.type}
        </span>
        {data.paramCount > 0 && (
          <span
            className="px-1.5 py-0.5 rounded text-[10px] font-medium"
            style={{ background: 'var(--pc-hover)', color: 'var(--pc-text-secondary)' }}
          >
            {data.paramCount} param{data.paramCount !== 1 ? 's' : ''}
          </span>
        )}
      </div>

      {/* Description preview */}
      {data.description && (
        <div
          className="text-[11px] mt-1.5 line-clamp-2"
          style={{ color: 'var(--pc-text-muted)', lineHeight: '1.3' }}
        >
          {data.description}
        </div>
      )}

      {/* Assigned pool agent — clickable for agent steps to open AgentPicker */}
      {data.assign && !data.runInfo && (
        <button
          type="button"
          onClick={isAgentStep ? openAgentPicker : undefined}
          onMouseDown={(e) => e.stopPropagation()}
          className="mt-1.5 px-2 py-0.5 rounded-md text-[10px] font-semibold truncate inline-flex items-center gap-1"
          style={{
            background: 'var(--construct-signal-network-soft)',
            color: 'var(--construct-signal-network)',
            border: '1px solid var(--construct-border-strong)',
            maxWidth: '100%',
            cursor: isAgentStep ? 'pointer' : 'default',
            font: 'inherit',
          }}
          title={`Assigned: ${data.assign}${isAgentStep ? ' — click to change' : ''}`}
        >
          <span style={{ fontSize: '8px' }}>●</span>
          {data.assign}
        </button>
      )}

      {/* Persona pill — agent.template binding from Architect persona discovery
          or hand-edited YAML. Distinct from the green "Assigned" button above
          (set via AgentPicker side-panel) but still actionable. */}
      {!data.assign && data.template && isAgentStep && !data.runInfo && (
        <button
          type="button"
          onClick={openAgentPicker}
          onMouseDown={(e) => e.stopPropagation()}
          className="mt-1.5 px-2 py-0.5 rounded-md text-[10px] font-semibold truncate inline-flex items-center gap-1"
          style={{
            background: 'transparent',
            color: 'var(--construct-text-secondary)',
            border: '1px solid var(--construct-border-strong)',
            maxWidth: '100%',
            cursor: 'pointer',
            font: 'inherit',
          }}
          title={`Pool persona "${data.template}" — click to change`}
        >
          <Bot size={10} />
          Persona · {data.template}
        </button>
      )}

      {/* Default-agent / unassigned pill — agent steps without a specific pool-agent binding.
          When agent_type is set the workflow runs fine on the default agent of that kind, so we
          show a neutral "Default · {Type}" chip rather than an alarming warning. The button still
          opens the picker for users who want to bind a specific pool agent. */}
      {!data.assign && !data.template && isAgentStep && !data.runInfo && (() => {
        const agentTypeLabel = data.agentType
          ? data.agentType.charAt(0).toUpperCase() + data.agentType.slice(1)
          : '';
        const isDefault = !!data.agentType;
        return (
          <button
            type="button"
            onClick={openAgentPicker}
            onMouseDown={(e) => e.stopPropagation()}
            className="mt-1.5 px-2 py-0.5 rounded-md text-[10px] font-medium truncate inline-flex items-center gap-1"
            style={{
              background: isDefault
                ? 'transparent'
                : 'color-mix(in srgb, var(--construct-status-warning) 16%, transparent)',
              color: isDefault
                ? 'var(--construct-text-muted)'
                : 'var(--construct-status-warning)',
              border: isDefault
                ? '1px solid var(--construct-border-soft)'
                : '1px solid var(--construct-status-warning)',
              maxWidth: '100%',
              cursor: 'pointer',
              font: 'inherit',
            }}
            title={
              isDefault
                ? `Default ${data.agentType} agent — click to assign a specific pool agent`
                : 'No pool agent assigned — click to choose'
            }
          >
            <Bot size={10} />
            {isDefault ? `Default · ${agentTypeLabel}` : 'Unassigned'}
          </button>
        );
      })()}

      {/* Agent hints */}
      {data.agentHints.length > 0 && (
        <div className="flex gap-1 mt-1.5 flex-wrap">
          {data.agentHints.map((hint) => {
            const hintTone = HINT_TONES[hint] || 'muted';
            return (
              <span
                key={hint}
                className="px-1.5 py-0.5 rounded text-[9px] font-medium"
                style={{
                  background: toneSoftVar(hintTone),
                  color: toneColorVar(hintTone),
                }}
              >
                {hint}
              </span>
            );
          })}
        </div>
      )}

      {/* Auth profile binding — small lock icon when an encrypted credential is bound */}
      {data.auth && (
        <div
          className="flex items-center gap-1 mt-1"
          title={`Auth: ${data.auth}`}
          style={{ color: 'var(--construct-text-faint)' }}
        >
          <Lock size={12} />
        </div>
      )}

      {/* Skills */}
      {data.skills.length > 0 && (
        <div className="flex gap-1 mt-1 flex-wrap">
          {data.skills.slice(0, 3).map((skill) => {
            // Show just the skill name, not the full kref path
            const short = skill.replace(/^kref:\/\/.*\//, '').replace(/\.skilldef$/, '');
            return (
              <span
                key={skill}
                className="px-1.5 py-0.5 rounded text-[9px] font-medium truncate"
                style={{ background: 'var(--pc-accent-glow)', color: 'var(--pc-accent-light)', maxWidth: '100%' }}
                title={skill}
              >
                {short}
              </span>
            );
          })}
          {data.skills.length > 3 && (
            <span
              className="px-1.5 py-0.5 rounded text-[9px] font-medium"
              style={{ background: 'var(--pc-hover)', color: 'var(--pc-text-faint)' }}
            >
              +{data.skills.length - 3}
            </span>
          )}
        </div>
      )}

      {/* Run info overlay — shown when viewing a workflow run */}
      {data.runInfo && (
        <div className="mt-2 pt-2" style={{ borderTop: '1px solid var(--construct-border-soft)' }}>
          <div className="flex items-center gap-1.5 flex-wrap">
            {/* Status badge */}
            {(() => {
              const statusTone = STATUS_TONES[data.runInfo.status] || 'muted';
              return (
                <span
                  className="px-1.5 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wider"
                  style={{
                    background: toneSoftVar(statusTone),
                    color: toneColorVar(statusTone),
                  }}
                >
                  {data.runInfo.status}
                </span>
              );
            })()}
            {/* Agent type badge */}
            {data.runInfo.agent_type && (() => {
              const atTone = AGENT_TYPE_TONES[data.runInfo.agent_type] || 'muted';
              return (
                <span
                  className="px-1.5 py-0.5 rounded text-[9px] font-medium"
                  style={{
                    background: toneSoftVar(atTone),
                    color: toneColorVar(atTone),
                  }}
                >
                  {data.runInfo!.agent_type}
                </span>
              );
            })()}
            {/* Role badge */}
            {data.runInfo.role && (() => {
              const roleTone = HINT_TONES[data.runInfo.role] || 'muted';
              return (
                <span
                  className="px-1.5 py-0.5 rounded text-[9px] font-medium"
                  style={{
                    background: toneSoftVar(roleTone),
                    color: toneColorVar(roleTone),
                  }}
                >
                  {data.runInfo!.role}
                </span>
              );
            })()}
          </div>
          {/* Pool agent (assigned template) — prominent badge */}
          {data.runInfo.template_name && (
            <div
              className="mt-1.5 px-2 py-0.5 rounded-md text-[10px] font-semibold truncate inline-flex items-center gap-1"
              style={{
                background: 'var(--construct-signal-network-soft)',
                color: 'var(--construct-signal-network)',
                border: '1px solid var(--construct-border-strong)',
                maxWidth: '100%',
              }}
              title={`Pool Agent: ${data.runInfo.template_name}`}
            >
              <span style={{ fontSize: '8px' }}>●</span>
              {data.runInfo.template_name}
            </div>
          )}
          {/* Agent ID — shown when agent is running or completed */}
          {data.runInfo.agent_id && (
            <div
              className="text-[8px] mt-1 font-mono truncate"
              style={{ color: 'var(--pc-text-faint)', letterSpacing: '0.02em' }}
              title={data.runInfo.agent_id}
            >
              id:{data.runInfo.agent_id.slice(0, 12)}
            </div>
          )}
          {/* Skills — shown with active indicator during execution */}
          {data.runInfo.skills && data.runInfo.skills.length > 0 && (
            <div className="flex gap-1 mt-1.5 flex-wrap">
              {data.runInfo.skills.slice(0, 3).map((skill) => (
                <span
                  key={skill}
                  className="px-1.5 py-0.5 rounded text-[8px] font-medium inline-flex items-center gap-0.5"
                  style={{
                    background: data.runInfo!.status === 'running'
                      ? 'var(--construct-signal-network-soft)'
                      : data.runInfo!.status === 'completed'
                        ? 'var(--construct-signal-live-soft)'
                        : 'var(--pc-accent-glow)',
                    color: data.runInfo!.status === 'running'
                      ? 'var(--construct-signal-network)'
                      : data.runInfo!.status === 'completed'
                        ? 'var(--construct-signal-live)'
                        : 'var(--pc-accent-light)',
                  }}
                >
                  {data.runInfo!.status === 'running' && (
                    <span
                      className="inline-block h-1 w-1 rounded-full"
                      style={{
                        background: 'var(--construct-signal-network)',
                        animation: 'pulse-dot 1.5s ease-in-out infinite',
                      }}
                    />
                  )}
                  {data.runInfo!.status === 'completed' && (
                    <span style={{ fontSize: '7px' }}>✓</span>
                  )}
                  {skill}
                </span>
              ))}
              {data.runInfo.skills.length > 3 && (
                <span
                  className="px-1.5 py-0.5 rounded text-[8px] font-medium"
                  style={{ background: 'var(--pc-hover)', color: 'var(--pc-text-faint)' }}
                >
                  +{data.runInfo.skills.length - 3}
                </span>
              )}
            </div>
          )}
          {/* Duration + Trust score */}
          <div className="flex items-center gap-2 mt-1">
            {data.runInfo.duration_s != null && data.runInfo.duration_s > 0 && (
              <span className="text-[9px]" style={{ color: 'var(--pc-text-faint)' }}>
                {data.runInfo.duration_s < 60
                  ? `${data.runInfo.duration_s.toFixed(1)}s`
                  : `${(data.runInfo.duration_s / 60).toFixed(1)}m`}
              </span>
            )}
            {data.runInfo.trust_score != null && (
              <span
                className="text-[9px] font-medium"
                style={{
                  color: data.runInfo.trust_score >= 0.8 ? 'var(--construct-status-success)'
                    : data.runInfo.trust_score >= 0.5 ? 'var(--construct-status-warning)'
                    : 'var(--construct-status-danger)',
                }}
              >
                trust {(data.runInfo.trust_score * 100).toFixed(0)}%
              </span>
            )}
          </div>
        </div>
      )}

      <Handle type="source" position={Position.Bottom} style={{ background: color, width: 10, height: 10 }} />
    </div>
  );
}

export const taskNodeTypes: NodeTypes = {
  taskNode: TaskNode,
};

export default TaskNode;
