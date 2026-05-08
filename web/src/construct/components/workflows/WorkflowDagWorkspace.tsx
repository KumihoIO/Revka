import { useMemo } from 'react';
import type { ReactNode } from 'react';
import type { Edge, Node, NodeMouseHandler } from '@xyflow/react';
import { parseWorkflowYaml, tasksToFlow, type StepRunInfo, type TaskDefinition, type TaskNodeData } from '@/construct/components/workflows/yamlSync';
import { layoutNodes } from '@/construct/lib/graphHelpers';
import GraphCanvas from '../orchestration/GraphCanvas';
import { workflowEdgeTypesV2 } from '../orchestration/WorkflowEdge';
import { workflowNodeTypesV2 } from '../orchestration/WorkflowNode';
import { buildWorkflowEdgeStyle, resolveCssVar, workflowActionTone } from '../../lib/orchestration';

export default function WorkflowDagWorkspace({
  definition,
  stepResults,
  onSelectTask,
  selectedTaskId,
  height,
  fill,
  overlay,
  hiddenTaskIds,
  blockedTaskIds,
  failingTaskIds,
  runningTaskIds,
}: {
  definition: string;
  stepResults?: Record<string, StepRunInfo>;
  onSelectTask: (task: TaskDefinition | null) => void;
  selectedTaskId?: string | null;
  height?: string;
  fill?: boolean;
  overlay?: ReactNode;
  hiddenTaskIds?: string[];
  blockedTaskIds?: string[];
  failingTaskIds?: string[];
  runningTaskIds?: string[];
}) {
  const { nodes, edges, tasks } = useMemo(() => {
    const parsed = parseWorkflowYaml(definition);
    const flow = tasksToFlow(parsed);
    const tasksById = new Map(parsed.map((task) => [task.id, task]));
    const hidden = new Set(hiddenTaskIds ?? []);
    const blocked = new Set(blockedTaskIds ?? []);
    const failing = new Set(failingTaskIds ?? []);
    const running = new Set(runningTaskIds ?? []);
    if (stepResults) {
      for (const node of flow.nodes) {
        const data = node.data as TaskNodeData;
        const runInfo = stepResults[data.taskId];
        if (runInfo) {
          data.runInfo = runInfo;
        }
        (data as TaskNodeData & { blocked?: boolean; failing?: boolean; running?: boolean }).blocked = blocked.has(data.taskId);
        (data as TaskNodeData & { blocked?: boolean; failing?: boolean; running?: boolean }).failing = failing.has(data.taskId);
        (data as TaskNodeData & { blocked?: boolean; failing?: boolean; running?: boolean }).running = running.has(data.taskId);
      }
    }
    return {
      tasks: parsed,
      nodes: layoutNodes(flow.nodes, flow.edges).map((node) => ({
        ...node,
        selected: node.id === selectedTaskId,
        hidden: hidden.has(node.id),
      })) as Node<TaskNodeData>[],
      edges: flow.edges.map((edge) => ({
        ...edge,
        ...buildWorkflowEdgeStyle({ edge, tasksById, stepResults, selectedTaskId }),
        hidden: hidden.has(edge.source) || hidden.has(edge.target),
      })) as Edge[],
    };
  }, [blockedTaskIds, definition, failingTaskIds, hiddenTaskIds, runningTaskIds, selectedTaskId, stepResults]);

  const handleNodeClick: NodeMouseHandler<Node<TaskNodeData>> = (_, node) => {
    const taskId = node.data.taskId;
    onSelectTask(tasks.find((task) => task.id === taskId) ?? null);
  };

  return (
    <GraphCanvas
      nodes={nodes}
      edges={edges}
      nodeTypes={workflowNodeTypesV2}
      edgeTypes={workflowEdgeTypesV2}
      onNodeClick={handleNodeClick}
      height={height}
      fill={fill}
      overlay={overlay}
      autoFocusNodeId={selectedTaskId}
      minimapColor={(node) => {
        const data = node.data as TaskNodeData;
        return resolveCssVar(workflowActionTone(data.type));
      }}
      emptyState="Select a workflow to inspect its DAG."
    />
  );
}
