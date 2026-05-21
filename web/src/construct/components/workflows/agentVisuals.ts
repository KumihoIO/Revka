import type { AgentDefinition } from '@/types/api';
import type { TaskNodeData } from './yamlSync';

type AgentVisual = {
  avatarUrl?: string | null;
  displayName?: string | null;
};

function key(value?: string | null): string {
  return (value ?? '').trim().toLowerCase();
}

function agentIndex(agents: AgentDefinition[]): Map<string, AgentVisual> {
  const index = new Map<string, AgentVisual>();
  for (const agent of agents) {
    const visual: AgentVisual = {
      avatarUrl: agent.avatar_url,
      displayName: agent.name || agent.item_name,
    };
    for (const candidate of [agent.item_name, agent.name, agent.kref]) {
      const normalized = key(candidate);
      if (normalized && !index.has(normalized)) {
        index.set(normalized, visual);
      }
    }
  }
  return index;
}

function resolveVisual(data: TaskNodeData, index: Map<string, AgentVisual>): AgentVisual | null {
  for (const candidate of [
    data.runInfo?.template_name,
    data.assign,
    data.template,
  ]) {
    const visual = index.get(key(candidate));
    if (visual) return visual;
  }
  return null;
}

export function withAgentVisuals<T extends { data: TaskNodeData }>(
  nodes: T[],
  agents: AgentDefinition[],
): T[] {
  if (agents.length === 0) return nodes;
  const index = agentIndex(agents);
  return nodes.map((node) => {
    const visual = resolveVisual(node.data, index);
    const avatarUrl = visual?.avatarUrl ?? undefined;
    const displayName = visual?.displayName ?? undefined;
    if (
      node.data.agentAvatarUrl === avatarUrl
      && node.data.agentDisplayName === displayName
    ) {
      return node;
    }
    return {
      ...node,
      data: {
        ...node.data,
        agentAvatarUrl: avatarUrl,
        agentDisplayName: displayName,
      },
    };
  });
}
