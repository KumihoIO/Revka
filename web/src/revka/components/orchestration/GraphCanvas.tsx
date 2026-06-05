import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  type Edge,
  type EdgeTypes,
  type Node,
  type NodeMouseHandler,
  type NodeTypes,
} from '@xyflow/react';
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
  onlyRenderVisibleElements?: boolean;
  showMiniMap?: boolean;
  emptyState?: string;
  overlay?: ReactNode;
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
  onlyRenderVisibleElements = false,
  showMiniMap = true,
  emptyState = 'No graph data available.',
  overlay,
}: GraphCanvasProps<TNode>) {
  const sizeStyle = fill ? { flex: '1 1 0%', minHeight: 0 } : { height };

  if (nodes.length === 0) {
    return (
      <div
        className="revka-graph revka-graph-empty flex items-center justify-center rounded-[8px] border border-dashed p-6 text-sm"
        style={{ ...sizeStyle, minHeight: fill ? 0 : height, borderColor: 'var(--revka-border-strong)', color: 'var(--revka-text-secondary)' }}
      >
        {emptyState}
      </div>
    );
  }

  return (
    <div
      className="revka-graph overflow-hidden rounded-[8px] border"
      style={{ ...sizeStyle, borderColor: 'var(--revka-border-soft)' }}
    >
      {overlay ? (
        <div className="revka-graph-overlay">
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
        onlyRenderVisibleElements={onlyRenderVisibleElements}
        minZoom={0.35}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        style={{ background: 'transparent' }}
      >
        <Background gap={24} size={1} color="var(--revka-grid-line)" />
        <Controls
          showInteractive={false}
          style={{
            background: 'var(--revka-bg-panel-strong)',
            borderColor: 'var(--revka-border-soft)',
            borderRadius: '8px',
          }}
        />
        {showMiniMap ? (
          <MiniMap
            position="bottom-right"
            pannable
            zoomable
            nodeColor={minimapColor}
            style={{
              background: 'var(--revka-bg-panel-strong)',
              border: '1px solid var(--revka-border-soft)',
              borderRadius: '8px',
              width: 220,
              height: 150,
            }}
            maskColor="color-mix(in srgb, var(--revka-bg-base) 54%, transparent)"
          />
        ) : null}
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
