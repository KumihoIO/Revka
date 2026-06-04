import type { Node, Edge } from '@xyflow/react';

// ---------------------------------------------------------------------------
// Edge type styles
// ---------------------------------------------------------------------------

export type TeamEdgeType = 'REPORTS_TO' | 'SUPPORTS' | 'DEPENDS_ON';

export const EDGE_TYPES: TeamEdgeType[] = ['REPORTS_TO', 'SUPPORTS', 'DEPENDS_ON'];

const edgeStyleMap: Record<string, { stroke: string; strokeDasharray?: string; animated?: boolean }> = {
  REPORTS_TO: { stroke: '#a855f7' },
  SUPPORTS: { stroke: '#22c55e', strokeDasharray: '5 5' },
  DEPENDS_ON: { stroke: '#f97316', animated: true },
};

const defaultEdgeStyle: { stroke: string; strokeDasharray?: string; animated?: boolean } = { stroke: '#6b7280' };

/** Safe lookup — returns a fallback style for unknown edge types. */
export const getEdgeStyle = (type: string) => edgeStyleMap[type] ?? defaultEdgeStyle;

const edgeTypeLabels: Record<string, string> = {
  REPORTS_TO: 'Reports To',
  SUPPORTS: 'Supports',
  DEPENDS_ON: 'Depends On',
};

export const getEdgeLabel = (type: string) => edgeTypeLabels[type] ?? type.replace(/_/g, ' ');

const edgeTypeBadgeColors: Record<string, { bg: string; text: string }> = {
  REPORTS_TO: { bg: 'rgba(168, 85, 247, 0.15)', text: '#a855f7' },
  SUPPORTS: { bg: 'rgba(34, 197, 94, 0.15)', text: '#22c55e' },
  DEPENDS_ON: { bg: 'rgba(249, 115, 22, 0.15)', text: '#f97316' },
};

export const getEdgeBadgeColors = (type: string) =>
  edgeTypeBadgeColors[type] ?? { bg: 'rgba(107, 114, 128, 0.15)', text: '#6b7280' };

// ---------------------------------------------------------------------------
// Role → color helper (shared by AgentNode, TeamCard, TeamBuilder)
// ---------------------------------------------------------------------------

export function getRoleColor(role: string): string {
  return role === 'coder'
    ? 'var(--pc-accent)'
    : role === 'reviewer'
      ? '#a855f7'
      : '#22c55e';
}

// ---------------------------------------------------------------------------
// DAG cycle detection (topological sort)
// ---------------------------------------------------------------------------

export function hasCycle(nodes: Node[], edges: Edge[]): boolean {
  const nodeIds = new Set(nodes.map((n) => n.id));
  const adj = new Map<string, string[]>();
  const inDegree = new Map<string, number>();
  for (const id of nodeIds) {
    adj.set(id, []);
    inDegree.set(id, 0);
  }
  for (const e of edges) {
    const edgeData = e.data as Record<string, unknown> | undefined;
    if (edgeData?.synthetic) continue;
    adj.get(e.source)?.push(e.target);
    inDegree.set(e.target, (inDegree.get(e.target) ?? 0) + 1);
  }
  const queue: string[] = [];
  for (const [id, deg] of inDegree) {
    if (deg === 0) queue.push(id);
  }
  let visited = 0;
  while (queue.length > 0) {
    const cur = queue.shift()!;
    visited++;
    for (const next of adj.get(cur) ?? []) {
      const newDeg = (inDegree.get(next) ?? 1) - 1;
      inDegree.set(next, newDeg);
      if (newDeg === 0) queue.push(next);
    }
  }
  return visited < nodeIds.size;
}

export function getDisconnectedNodes(nodes: Node[], edges: Edge[]): string[] {
  const connected = new Set<string>();
  for (const e of edges) {
    connected.add(e.source);
    connected.add(e.target);
  }
  return nodes.filter((n) => !connected.has(n.id)).map((n) => (n.data as { label: string }).label);
}

// ---------------------------------------------------------------------------
// Simple tree layout
// ---------------------------------------------------------------------------

export function layoutNodes(nodes: Node[], edges: Edge[]): Node[] {
  if (nodes.length === 0) return nodes;

  // Build adjacency for top-down layout
  const children = new Map<string, string[]>();
  const parents = new Map<string, string[]>();
  for (const n of nodes) {
    children.set(n.id, []);
    parents.set(n.id, []);
  }
  for (const e of edges) {
    children.get(e.source)?.push(e.target);
    parents.get(e.target)?.push(e.source);
  }

  // Find roots (no parents)
  const roots = nodes.filter((n) => (parents.get(n.id) ?? []).length === 0);
  if (roots.length === 0) {
    // All in cycle — just line them up
    return nodes.map((n, i) => ({ ...n, position: { x: i * 250, y: 0 } }));
  }

  // BFS to assign levels (with visited guard to prevent infinite loops on cycles)
  const level = new Map<string, number>();
  const visited = new Set<string>();
  const queue: string[] = [];
  for (const r of roots) {
    level.set(r.id, 0);
    queue.push(r.id);
    visited.add(r.id);
  }
  while (queue.length > 0) {
    const cur = queue.shift()!;
    const curLevel = level.get(cur) ?? 0;
    for (const child of children.get(cur) ?? []) {
      if (!visited.has(child)) {
        visited.add(child);
        level.set(child, curLevel + 1);
        queue.push(child);
      }
    }
  }

  // Assign positions not yet assigned
  for (const n of nodes) {
    if (!level.has(n.id)) level.set(n.id, 0);
  }

  // Group by level
  const levels = new Map<number, string[]>();
  for (const [id, lvl] of level) {
    if (!levels.has(lvl)) levels.set(lvl, []);
    levels.get(lvl)!.push(id);
  }

  const baseXGap = 340;
  const yGap = 260;
  const positioned = new Map<string, { x: number; y: number }>();

  for (const [lvl, ids] of [...levels.entries()].sort((a, b) => a[0] - b[0])) {
    const xGap = baseXGap;
    const totalWidth = (ids.length - 1) * xGap;
    const startX = -totalWidth / 2;
    ids.forEach((id, i) => {
      positioned.set(id, { x: startX + i * xGap, y: lvl * yGap });
    });
  }

  return nodes.map((n) => ({
    ...n,
    position: positioned.get(n.id) ?? { x: 0, y: 0 },
  }));
}
