import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
  type EdgeTypes,
} from '@xyflow/react';

function WorkflowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  label,
  labelStyle,
}: EdgeProps) {
  const [path, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  });

  return (
    <>
      <BaseEdge
        id={`${id}-track`}
        path={path}
        style={{
          stroke: 'var(--revka-border-soft)',
          strokeWidth: 4,
          opacity: 0.42,
          fill: 'none',
        }}
      />
      <BaseEdge id={id} path={path} style={style} />
      {label ? (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan absolute -translate-x-1/2 -translate-y-1/2 rounded-md border px-1.5 py-0.5 text-[10px] font-semibold tracking-[0.08em]"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              borderColor: 'var(--revka-border-strong)',
              background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 96%, transparent)',
              color: 'var(--revka-text-primary)',
              boxShadow: '0 8px 18px rgba(0, 0, 0, 0.10)',
              ...(labelStyle ?? {}),
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export const workflowEdgeTypesV2: EdgeTypes = {
  default: WorkflowEdge,
};
