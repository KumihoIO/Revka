import { Handle, Position, type NodeTypes } from '@xyflow/react';
import {
  gateBranchHandle,
  gateBranchLabel,
  gateBranchStyle,
  type ConditionalBranchDefinition,
  type TaskNodeData,
} from '@/construct/components/workflows/yamlSync';

const GATE_COLOR = 'var(--construct-status-warning)';
const GATE_SOFT = 'color-mix(in srgb, var(--construct-status-warning) 16%, transparent)';

function getBranches(data: TaskNodeData): ConditionalBranchDefinition[] {
  if (data.conditionalBranches?.length > 0) return data.conditionalBranches;
  const branches: ConditionalBranchDefinition[] = [];
  if (data.condition || data.onTrueValue) {
    branches.push({ condition: data.condition || '', goto: '', value: data.onTrueValue || undefined });
  }
  if (data.onFalseValue) {
    branches.push({ condition: 'default', goto: '', value: data.onFalseValue });
  }
  return branches.length > 0 ? branches : [
    { condition: data.condition || '', goto: '', value: data.onTrueValue || undefined },
    { condition: 'default', goto: '', value: data.onFalseValue || undefined },
  ];
}

function GateNode({ data, selected }: { data: TaskNodeData; selected?: boolean }) {
  const branches = getBranches(data);
  const primaryCondition = branches.find((branch) => branch.condition.trim() !== 'default')?.condition;

  return (
    <div
      className="px-4 py-3 rounded-xl shadow-lg transition-all"
      style={{
        position: 'relative',
        background: selected
          ? `linear-gradient(135deg, ${GATE_SOFT} 0%, ${GATE_SOFT} 40%, var(--construct-bg-panel-strong) 100%)`
          : `linear-gradient(135deg, ${GATE_SOFT} 0%, var(--construct-bg-elevated) 50%, var(--construct-bg-surface) 100%)`,
        border: `2px solid ${selected ? GATE_COLOR : 'var(--construct-border-strong)'}`,
        minWidth: 200,
        maxWidth: 260,
        boxShadow: selected
          ? `0 0 20px ${GATE_SOFT}, inset 0 1px 0 ${GATE_SOFT}`
          : `0 4px 12px rgba(0, 0, 0, 0.3), inset 0 1px 0 var(--construct-border-soft)`,
      }}
    >
      {/* Input handle */}
      <Handle
        type="target"
        position={Position.Top}
        style={{ background: GATE_COLOR, width: 10, height: 10 }}
      />

      {/* Header with diamond icon */}
      <div className="flex items-center gap-2">
        <div
          className="w-5 h-5 flex items-center justify-center flex-shrink-0"
          style={{ color: GATE_COLOR }}
        >
          <svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
            <path d="M8 1L15 8L8 15L1 8Z" />
          </svg>
        </div>
        <div
          className="text-sm font-bold truncate"
          style={{ color: selected ? 'var(--construct-signal-selected)' : 'var(--pc-text-primary)' }}
        >
          {data.name || data.taskId}
        </div>
      </div>

      {/* Gate badge */}
      <div className="flex items-center gap-1.5 mt-1.5">
        <span
          className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wider"
          style={{ background: GATE_SOFT, color: GATE_COLOR }}
        >
          {branches.length > 2 ? `${branches.length} branches` : 'if / else'}
        </span>
      </div>

      {/* Condition */}
      {primaryCondition ? (
        <div
          className="text-[11px] mt-1.5 font-mono truncate"
          style={{ color: 'var(--pc-text-muted)', lineHeight: '1.3' }}
        >
          {primaryCondition}
        </div>
      ) : (
        <div className="text-[10px] mt-1.5 italic" style={{ color: 'var(--pc-text-faint)' }}>
          no condition set
        </div>
      )}

      {/* Description */}
      {data.description && (
        <div
          className="text-[10px] mt-1 line-clamp-1"
          style={{ color: 'var(--pc-text-faint)' }}
        >
          {data.description}
        </div>
      )}

      {/* Branch labels + handles */}
      <div className="flex flex-col gap-1.5 mt-2.5">
        {branches.map((branch, index) => {
          const style = gateBranchStyle(branch, index);
          const label = gateBranchLabel(branch, index);
          return (
            <div key={gateBranchHandle(index)} className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-1 min-w-0">
                <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: style.stroke }} />
                <span className="text-[9px] font-semibold uppercase truncate" style={{ color: style.stroke }}>
                  {label}
                </span>
              </div>
              {branch.value && (
                <span
                  className="text-[9px] font-mono px-1 rounded truncate max-w-[120px]"
                  style={{
                    background: 'color-mix(in srgb, var(--construct-status-warning) 14%, transparent)',
                    color: style.stroke,
                  }}
                  title={`emits: ${branch.value}`}
                >
                  = {branch.value}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {branches.map((branch, index) => {
        const style = gateBranchStyle(branch, index);
        const left = `${((index + 1) / (branches.length + 1)) * 100}%`;
        return (
          <Handle
            key={gateBranchHandle(index)}
            type="source"
            position={Position.Bottom}
            id={gateBranchHandle(index)}
            style={{ background: style.stroke, width: 10, height: 10, left }}
          />
        );
      })}
    </div>
  );
}

export const gateNodeTypes: NodeTypes = {
  gateNode: GateNode,
};

export default GateNode;
