export const GOOGLE_AGENTOPS_REQUIRED_TOOLS = [
  'google_agents_cli',
  'a2a_discover',
  'a2a_send_task',
  'a2a_get_remote_task',
] as const;

const GOOGLE_AGENTOPS_TOOL_SET = new Set<string>(GOOGLE_AGENTOPS_REQUIRED_TOOLS);

export type AgentToolsMode = 'all' | 'memory' | 'google_agentops' | 'none';

export function dedupeToolNames(tools: readonly string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const tool of tools) {
    const name = String(tool || '').trim();
    if (!name || seen.has(name)) continue;
    seen.add(name);
    result.push(name);
  }
  return result;
}

export function expandGoogleAgentOpsRequiredTools(tools: readonly string[]): string[] {
  const normalized = dedupeToolNames(tools);
  if (!normalized.includes('google_agents_cli')) {
    return normalized;
  }
  return dedupeToolNames([...normalized, ...GOOGLE_AGENTOPS_REQUIRED_TOOLS]);
}

export function hasGoogleAgentOpsBundle(tools: readonly string[]): boolean {
  const names = new Set(dedupeToolNames(tools));
  return GOOGLE_AGENTOPS_REQUIRED_TOOLS.every((tool) => names.has(tool));
}

export function requiresGoogleAgentOpsToolMode(tools: readonly string[]): boolean {
  return dedupeToolNames(tools).some((tool) => GOOGLE_AGENTOPS_TOOL_SET.has(tool));
}
