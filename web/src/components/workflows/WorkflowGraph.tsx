/**
 * WorkflowGraph — Read-only DAG visualization of a workflow definition.
 *
 * Parses the YAML definition into tasks, renders as a React Flow graph
 * with hierarchical layout, cycle detection, gate nodes, and task-type coloring.
 */

import { useMemo } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { parseWorkflowYaml, tasksToFlow, type TaskNodeData, type StepRunInfo } from './yamlSync';
import { taskNodeTypes } from './TaskNode';
import { gateNodeTypes } from './GateNode';
import { hasCycle, layoutNodes } from '../teams/graphHelpers';

const allNodeTypes = { ...taskNodeTypes, ...gateNodeTypes };

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface WorkflowGraphProps {
  definition: string;
  height?: string;
  /** Step results from a workflow run — overlays agent info on nodes */
  stepResults?: Record<string, StepRunInfo>;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

function WorkflowGraphInner({ definition, height = '400px', stepResults }: WorkflowGraphProps) {
  const { nodes, edges, taskCount, cycleWarning } = useMemo(() => {
    if (!definition.trim()) {
      return { nodes: [] as Node[], edges: [] as Edge[], taskCount: 0, cycleWarning: false };
    }

    const tasks = parseWorkflowYaml(definition);
    if (tasks.length === 0) {
      return { nodes: [] as Node[], edges: [] as Edge[], taskCount: 0, cycleWarning: false };
    }

    const { nodes: rawNodes, edges: flowEdges } = tasksToFlow(tasks);

    // Overlay run data if available
    if (stepResults) {
      for (const node of rawNodes) {
        const data = node.data as TaskNodeData;
        const info = stepResults[data.taskId];
        if (info) {
          data.runInfo = info;
        }
      }
    }

    const laidOut = layoutNodes(rawNodes, flowEdges);
    const isCyclic = hasCycle(rawNodes, flowEdges);

    return {
      nodes: laidOut,
      edges: flowEdges,
      taskCount: tasks.length,
      cycleWarning: isCyclic,
    };
  }, [definition, stepResults]);

  if (taskCount === 0) {
    return (
      <div
        className="flex items-center justify-center rounded-xl"
        style={{ height, background: 'var(--pc-bg-elevated)', border: '1px solid var(--pc-border)' }}
      >
        <p className="text-sm" style={{ color: 'var(--pc-text-muted)' }}>
          No tasks found in definition. Expected <code>tasks:</code> with <code>- id:</code> entries.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {/* Stats bar */}
      <div className="flex items-center gap-3 text-xs" style={{ color: 'var(--pc-text-muted)' }}>
        <span>{taskCount} task{taskCount !== 1 ? 's' : ''}</span>
        <span>{edges.length} edge{edges.length !== 1 ? 's' : ''}</span>
        {cycleWarning && (
          <span
            className="px-1.5 py-0.5 rounded-md text-[10px] font-medium"
            style={{
              background: 'color-mix(in srgb, var(--construct-status-danger) 16%, transparent)',
              color: 'var(--construct-status-danger)',
              border: '1px solid color-mix(in srgb, var(--construct-status-danger) 25%, transparent)',
            }}
          >
            cycle detected
          </span>
        )}
      </div>

      {/* Graph canvas */}
      <div
        className="rounded-xl overflow-hidden"
        style={{ height, border: '1px solid var(--pc-border)' }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={allNodeTypes}
          fitView
          fitViewOptions={{ padding: 0.3 }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnDrag
          zoomOnScroll
          minZoom={0.3}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          style={{ background: 'var(--pc-bg-base)' }}
        >
          <Background gap={20} size={1} color="rgba(255,255,255,0.03)" />
          <Controls
            showInteractive={false}
            style={{ background: 'var(--pc-bg-elevated)', borderColor: 'var(--pc-border)' }}
          />
          <MiniMap
            nodeColor={(node: Node<TaskNodeData>) => {
              const data = node.data as TaskNodeData;
              if (data?.type === 'conditional') return 'var(--construct-status-warning)';
              return 'var(--pc-accent)';
            }}
            style={{
              background: 'var(--pc-bg-elevated)',
              border: '1px solid var(--pc-border)',
              borderRadius: '0.75rem',
            }}
            maskColor="rgba(0,0,0,0.3)"
          />
        </ReactFlow>
      </div>
    </div>
  );
}

export default function WorkflowGraph(props: WorkflowGraphProps) {
  return (
    <ReactFlowProvider>
      <WorkflowGraphInner {...props} />
    </ReactFlowProvider>
  );
}
