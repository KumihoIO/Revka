import { useMemo } from 'react';
import { MarkerType, type Edge, type Node, type NodeMouseHandler } from '@xyflow/react';
import type { TeamDefinition } from '@/types/api';
import { getEdgeLabel, getEdgeStyle, layoutNodes } from '@/revka/lib/graphHelpers';
import type { AgentNodeData } from '@/components/teams/AgentNode';
import GraphCanvas from '../orchestration/GraphCanvas';
import { teamNodeTypesV2 } from '../orchestration/TeamNode';

export default function TeamTopologyPanel({
  team,
  onSelectMember,
  selectedMemberKref,
}: {
  team: TeamDefinition | null;
  onSelectMember: (memberKref: string | null) => void;
  selectedMemberKref?: string | null;
}) {
  const { nodes, edges } = useMemo(() => {
    if (!team) {
      return { nodes: [] as Node<AgentNodeData>[], edges: [] as Edge[] };
    }

    const baseNodes: Node<AgentNodeData>[] = team.members.map((member) => ({
      id: member.kref,
      type: 'agentNode',
      position: { x: 0, y: 0 },
      selected: member.kref === selectedMemberKref,
      data: {
        label: member.name,
        role: member.role,
        agentType: member.agent_type,
        identity: member.identity,
        kref: member.kref,
        avatarUrl: member.avatar_url,
      },
    }));

    const baseEdges: Edge[] = team.edges.map((edge) => {
      const style = getEdgeStyle(edge.edge_type);
      return {
        id: `${edge.from_kref}-${edge.to_kref}-${edge.edge_type}`,
        source: edge.from_kref,
        target: edge.to_kref,
        label: getEdgeLabel(edge.edge_type),
        type: 'default',
        style: {
          stroke: style.stroke,
          strokeWidth: 2,
          strokeDasharray: style.strokeDasharray,
        },
        animated: style.animated ?? false,
        markerEnd: { type: MarkerType.ArrowClosed, color: style.stroke },
      };
    });

    return {
      nodes: layoutNodes(baseNodes, baseEdges) as Node<AgentNodeData>[],
      edges: baseEdges,
    };
  }, [selectedMemberKref, team]);

  const handleNodeClick: NodeMouseHandler<Node<AgentNodeData>> = (_, node) => {
    onSelectMember(node.id);
  };

  return (
    <GraphCanvas
      nodes={nodes}
      edges={edges}
      nodeTypes={teamNodeTypesV2}
      onNodeClick={handleNodeClick}
      minimapColor={(node) => {
        const data = node.data as AgentNodeData;
        if (data.role === 'reviewer') return '#6575d3';
        if (data.role === 'coder') return '#2fbf75';
        return '#4f75a8';
      }}
      emptyState="Select a team to inspect its topology."
    />
  );
}
