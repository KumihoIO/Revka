import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeTypes,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
} from '@xyflow/react';
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import '@xyflow/react/dist/style.css';

type GraphCanvasProps<TNode extends Node = Node> = {
  nodes: TNode[];
  edges: Edge[];
  nodeTypes: NodeTypes;
  edgeTypes?: EdgeTypes;
  onNodeClick?: NodeMouseHandler<TNode>;
  minimapColor?: (node: TNode) => string;
  /** Fixed height string (e.g. '30rem'). Ignored when `fill` is true. */
  height?: string;
  /** When true, the canvas fills its flex parent (flex: 1 + min-h-0). */
  fill?: boolean;
  emptyState?: string;
  overlay?: ReactNode;
  autoFocusNodeId?: string | null;
};

function GraphCanvasInner<TNode extends Node = Node>({
  nodes,
  edges,
  nodeTypes,
  edgeTypes,
  onNodeClick,
  minimapColor,
  height = '30rem',
  fill,
  emptyState = 'No graph data available.',
  overlay,
  autoFocusNodeId,
}: GraphCanvasProps<TNode>) {
  const reactFlow = useReactFlow<TNode>();

  useEffect(() => {
    if (!autoFocusNodeId) return;
    const node = nodes.find((candidate) => candidate.id === autoFocusNodeId);
    if (!node || node.hidden) return;

    const width = node.width ?? 240;
    const nodeHeight = node.height ?? 120;
    const zoom = Math.max(reactFlow.getZoom?.() ?? 1, 0.85);
    void reactFlow.setCenter(
      node.position.x + width / 2,
      node.position.y + nodeHeight / 2,
      { zoom, duration: 240 },
    );
  }, [autoFocusNodeId, nodes, reactFlow]);

  const sizeStyle = fill ? { flex: '1 1 0%', minHeight: 0 } : { height };

  if (nodes.length === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-[16px] border border-dashed p-6 text-sm"
        style={{ ...sizeStyle, minHeight: fill ? 0 : height, borderColor: 'var(--construct-border-strong)', color: 'var(--construct-text-secondary)' }}
      >
        {emptyState}
      </div>
    );
  }

  return (
    <div
      className="construct-graph overflow-hidden rounded-[16px] border"
      style={{ ...sizeStyle, borderColor: 'var(--construct-border-soft)' }}
    >
      {overlay ? (
        <div className="construct-graph-overlay">
          {overlay}
        </div>
      ) : null}
      <ReactFlow<TNode>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        onNodeClick={onNodeClick}
        nodesDraggable={false}
        nodesConnectable={false}
        panOnDrag
        elementsSelectable
        minZoom={0.35}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        style={{ background: 'transparent' }}
      >
        <Background gap={24} size={1} color="var(--construct-grid-line)" />
        <Controls
          showInteractive={false}
          style={{
            background: 'var(--construct-bg-panel-strong)',
            borderColor: 'var(--construct-border-soft)',
            borderRadius: '12px',
          }}
        />
        <MiniMap
          position="bottom-right"
          pannable
          zoomable
          nodeColor={minimapColor}
          style={{
            background: 'var(--construct-bg-panel-strong)',
            border: '1px solid var(--construct-border-soft)',
            borderRadius: '12px',
            width: 220,
            height: 150,
          }}
          maskColor="rgba(0, 0, 0, 0.24)"
        />
      </ReactFlow>
    </div>
  );
}

export default function GraphCanvas<TNode extends Node = Node>(props: GraphCanvasProps<TNode>) {
  return (
    <ReactFlowProvider>
      <GraphCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
