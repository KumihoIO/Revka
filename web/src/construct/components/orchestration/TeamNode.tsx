import { Handle, Position, type NodeTypes } from '@xyflow/react';
import type { AgentNodeData } from '@/components/teams/AgentNode';
import { getRoleColor } from '@/construct/lib/graphHelpers';
import AgentAvatar from '@/construct/components/ui/AgentAvatar';

function cssUrl(value: string): string {
  return `url("${value.replace(/"/g, '%22')}")`;
}

function TeamNode({ data, selected }: { data: AgentNodeData; selected?: boolean }) {
  const accent = getRoleColor(data.role);
  const accentLayer = selected
    ? `linear-gradient(135deg, color-mix(in srgb, ${accent} var(--construct-node-accent-selected), transparent), transparent 78%)`
    : `linear-gradient(180deg, color-mix(in srgb, ${accent} var(--construct-node-accent-idle), transparent), transparent 42%)`;
  const avatarBackground = typeof data.avatarUrl === 'string' && data.avatarUrl
    ? [
        'linear-gradient(90deg, var(--construct-bg-panel-strong) 0%, color-mix(in srgb, var(--construct-bg-panel-strong) 86%, transparent) 52%, color-mix(in srgb, var(--construct-bg-panel-strong) 36%, transparent) 100%)',
        accentLayer,
        cssUrl(data.avatarUrl),
      ].join(', ')
    : accentLayer;

  return (
    <div
      className="rounded-[14px] border px-4 py-3 shadow-sm"
      style={{
        minWidth: 200,
        maxWidth: 240,
        minHeight: 132,
        borderColor: selected ? accent : 'color-mix(in srgb, var(--construct-border-soft) 75%, transparent)',
        backgroundColor: 'var(--construct-bg-panel-strong)',
        backgroundImage: avatarBackground,
        backgroundRepeat: 'no-repeat',
        backgroundPosition: data.avatarUrl ? '0 0, 0 0, right -18px center' : undefined,
        backgroundSize: data.avatarUrl ? '100% 100%, 100% 100%, 58% auto' : undefined,
        boxShadow: selected ? `0 0 0 1px ${accent}, 0 0 26px color-mix(in srgb, ${accent} 24%, transparent)` : 'var(--construct-shadow-panel)',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: accent, width: 9, height: 9 }} />
      <div className="flex items-center gap-2">
        <AgentAvatar src={data.avatarUrl} alt={data.label} size={28} radius={8} />
        <div className="truncate text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
          {data.label}
        </div>
      </div>
      <p className="mt-2 line-clamp-2 text-[11px] leading-5" style={{ color: 'var(--construct-text-secondary)' }}>
        {data.identity}
      </p>
      <div className="mt-3 flex flex-wrap gap-1.5">
        <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'color-mix(in srgb, var(--construct-bg-elevated) 85%, transparent)', color: accent }}>
          {data.role}
        </span>
        <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--construct-signal-network-soft)', color: 'var(--construct-signal-network)' }}>
          {data.agentType}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: accent, width: 9, height: 9 }} />
    </div>
  );
}

export const teamNodeTypesV2: NodeTypes = {
  agentNode: TeamNode,
};
