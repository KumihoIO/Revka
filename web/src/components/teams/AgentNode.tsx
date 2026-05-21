import { Handle, Position, type NodeTypes } from '@xyflow/react';
import { getRoleColor } from './graphHelpers';
import AgentAvatar from '@/construct/components/ui/AgentAvatar';

export interface AgentNodeData {
  label: string;
  role: string;
  agentType: string;
  identity: string;
  kref: string;
  avatarUrl?: string | null;
  [key: string]: unknown;
}

function cssUrl(value: string): string {
  return `url("${value.replace(/"/g, '%22')}")`;
}

function AgentNode({ data }: { data: AgentNodeData }) {
  const borderColor = getRoleColor(data.role);
  const hasAvatar = typeof data.avatarUrl === 'string' && data.avatarUrl.length > 0;

  return (
    <div
      className="px-4 py-3 rounded-xl shadow-lg"
      style={{
        backgroundColor: 'var(--pc-bg-elevated)',
        backgroundImage: hasAvatar
          ? [
              'linear-gradient(90deg, var(--pc-bg-elevated) 0%, color-mix(in srgb, var(--pc-bg-elevated) 84%, transparent) 54%, color-mix(in srgb, var(--pc-bg-elevated) 34%, transparent) 100%)',
              cssUrl(data.avatarUrl as string),
            ].join(', ')
          : undefined,
        backgroundRepeat: 'no-repeat',
        backgroundPosition: hasAvatar ? '0 0, right -16px center' : undefined,
        backgroundSize: hasAvatar ? '100% 100%, 58% auto' : undefined,
        border: `2px solid ${borderColor}`,
        minWidth: 180,
        minHeight: 124,
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: borderColor }} />
      <div className="flex items-center gap-2">
        <AgentAvatar src={data.avatarUrl} alt={data.label} size={28} radius={8} />
        <div className="text-sm font-bold" style={{ color: 'var(--pc-text-primary)' }}>
          {data.label}
        </div>
      </div>
      <div
        className="text-xs mt-1 line-clamp-2"
        style={{ color: 'var(--pc-text-muted)', maxWidth: 200 }}
      >
        {data.identity}
      </div>
      <div className="flex gap-1 mt-2">
        <span
          className="px-1.5 py-0.5 rounded text-[10px] font-medium"
          style={{ background: borderColor + '22', color: borderColor }}
        >
          {data.role}
        </span>
        <span
          className="px-1.5 py-0.5 rounded text-[10px] font-medium"
          style={{
            background: 'var(--pc-hover)',
            color: 'var(--pc-text-secondary)',
          }}
        >
          {data.agentType}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: borderColor }} />
    </div>
  );
}

export const nodeTypes: NodeTypes = {
  agentNode: AgentNode,
};

export default AgentNode;
