// Shared utilities for chat components.

import type { ChatTab, TabTarget } from './types';

/** Resolve the effective target from a ChatTab (v1 compat: missing target defaults to chat). */
export function resolveTarget(tab: ChatTab): TabTarget {
  return tab.target ?? { type: 'chat', sessionId: tab.sessionId };
}

/** Map tool names to user-friendly labels. */
export function friendlyToolLabel(name: string): string {
  const map: Record<string, string> = {
    save_agent: 'Creating agent',
    save_agent_template: 'Saving agent template',
    create_team: 'Building team',
    search_agents: 'Searching agents',
    search_agent_pool: 'Searching agent pool',
    search_teams: 'Searching teams',
    list_agent_templates: 'Listing agents',
    list_teams: 'Listing teams',
    get_team: 'Fetching team details',
    save_plan: 'Saving plan',
    save_skill: 'Saving skill',
    set_agent_trust: 'Setting trust level',
    set_agent_goal: 'Setting agent goal',
    shell: 'Running command',
    search_clawhub: 'Searching ClawHub',
    browse_clawhub: 'Browsing ClawHub',
    install_from_clawhub: 'Installing from ClawHub',
    render_canvas: 'Rendering to canvas',
    clear_canvas: 'Clearing canvas',
  };
  return map[name] || name.replace(/_/g, ' ');
}

/** Map operator phases to emoji icons. */
export function operatorPhaseIcon(phase: string): string {
  switch (phase) {
    case 'spawning': return '\u{1f916}';
    case 'waiting': return '\u23f3';
    case 'delegating': return '\u{1f4e8}';
    case 'collecting': case 'collected': return '\u{1f4cb}';
    case 'checking': case 'listing': case 'searching': return '\u{1f50d}';
    case 'saving': return '\u{1f4be}';
    case 'steering': return '\u{1f3af}';
    case 'finalizing': return '\u23f3';
    case 'spawned': case 'completed': return '\u2705';
    case 'failed': return '\u274c';
    case 'blocked': return '\u{1f6d1}';
    case 'running': return '\u{1f3c3}';
    default: return '\u2699\ufe0f';
  }
}

/** Map operator phases to CSS color values. */
export function operatorPhaseColor(phase: string): string {
  switch (phase) {
    case 'completed': case 'spawned': case 'collected': return 'var(--color-status-success, #34d399)';
    case 'failed': return 'var(--color-status-error, #f87171)';
    case 'blocked': return 'var(--color-status-warning, #fbbf24)';
    case 'running': return 'var(--pc-accent)';
    default: return 'var(--pc-accent)';
  }
}

/** Transient phases that replace in-place (don't clutter history). */
export function isTransientPhase(phase?: string): boolean {
  return !!phase && !['completed', 'spawned', 'collected', 'failed', 'blocked'].includes(phase);
}
