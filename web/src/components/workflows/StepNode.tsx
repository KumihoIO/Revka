import { Handle, Position, type NodeTypes } from '@xyflow/react';
import type { StepNodeData } from './yamlSync';

// ---------------------------------------------------------------------------
// Action → token mapping. Mirrors the convention in TaskNode.tsx so the
// canvas adapts to the active Construct theme (and the Zion copper scope).
// ---------------------------------------------------------------------------

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
};

function getActionTone(action: string): ActionTone {
  const key = action.toLowerCase().replace(/[^a-z]/g, '');
  for (const [prefix, tone] of Object.entries(ACTION_TONES)) {
    if (key.startsWith(prefix) || key.includes(prefix)) return tone;
  }
  return 'muted';
}

function toneColorVar(tone: ActionTone): string {
  switch (tone) {
    case 'live': return 'var(--construct-signal-live)';
    case 'network': return 'var(--construct-signal-network)';
    case 'warning': return 'var(--construct-status-warning)';
    case 'danger': return 'var(--construct-status-danger)';
    case 'accent': return 'var(--pc-accent)';
    case 'muted': return 'var(--construct-text-faint)';
  }
}

function toneSoftVar(tone: ActionTone): string {
  switch (tone) {
    case 'live': return 'color-mix(in srgb, var(--construct-signal-live) 16%, transparent)';
    case 'network': return 'color-mix(in srgb, var(--construct-signal-network) 16%, transparent)';
    case 'warning': return 'color-mix(in srgb, var(--construct-status-warning) 16%, transparent)';
    case 'danger': return 'color-mix(in srgb, var(--construct-status-danger) 16%, transparent)';
    case 'accent': return 'var(--pc-accent-glow)';
    case 'muted': return 'color-mix(in srgb, var(--construct-text-faint) 16%, transparent)';
  }
}

// ---------------------------------------------------------------------------
// StepNode component
// ---------------------------------------------------------------------------

function StepNode({ data }: { data: StepNodeData }) {
  const tone = getActionTone(data.type);
  const color = toneColorVar(tone);
  const soft = toneSoftVar(tone);

  return (
    <div
      className="px-4 py-3 rounded-xl shadow-lg"
      style={{
        position: 'relative',
        background: 'var(--pc-bg-elevated)',
        border: `2px solid ${color}`,
        minWidth: 200,
        maxWidth: 260,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: color }} />

      {/* Step ID */}
      <div className="text-sm font-bold" style={{ color: 'var(--pc-text-primary)' }}>
        {data.label}
      </div>

      {/* Action */}
      <div className="flex items-center gap-1.5 mt-1.5">
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

      {/* Agent assignment */}
      {data.agent && (
        <div
          className="text-[11px] mt-1.5 truncate"
          style={{ color: 'var(--pc-text-muted)', maxWidth: 220 }}
        >
          agent: {data.agent.replace(/^kref:\/\//, '').split('/').pop()}
        </div>
      )}

      {/* Dependency indicator */}
      {data.dependencyCount > 0 && (
        <div className="text-[10px] mt-1" style={{ color: 'var(--pc-text-faint)' }}>
          {data.dependencyCount} dep{data.dependencyCount !== 1 ? 's' : ''}
        </div>
      )}

      <Handle type="source" position={Position.Bottom} style={{ background: color }} />
    </div>
  );
}

export const nodeTypes: NodeTypes = {
  stepNode: StepNode,
};

export default StepNode;
