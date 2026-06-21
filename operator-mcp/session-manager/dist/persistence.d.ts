/**
 * Session Persistence — saves/restores agent state to disk.
 *
 * Agent state files live in ~/.revka/operator_mcp/agents/{agent_id}.json.
 * On sidecar startup, persisted sessions with status "running" or "idle" are
 * eligible for resume. Continuation context is rebuilt from the persisted
 * timeline (a continuation summary), NOT the Claude SDK's `resume` option —
 * see providers/claude.ts for why SDK resume is deliberately avoided.
 */
import type { AgentProvider, AgentSessionInfo, AgentUsage } from "./types.js";
export interface PersistedAgentState {
    id: string;
    title: string;
    cwd: string;
    agentType: AgentProvider;
    sessionId?: string;
    status: string;
    parentId?: string;
    usage: AgentUsage;
    timelineTail: any[];
    createdAt: string;
    lastActivity: string;
}
/**
 * Save agent state to disk.
 */
export declare function saveAgentState(info: AgentSessionInfo, sessionId?: string, events?: any[]): void;
/**
 * Remove agent state file (on close/cleanup).
 */
export declare function removeAgentState(agentId: string): void;
/**
 * Load all persisted agent states.
 */
export declare function loadAllAgentStates(): PersistedAgentState[];
/**
 * Get resumable agent states (those that were running or idle when sidecar stopped).
 */
export declare function getResumableStates(): PersistedAgentState[];
/**
 * Update just the status field in a persisted state.
 */
export declare function updateAgentStatus(agentId: string, status: string): void;
