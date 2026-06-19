/**
 * Claude Agent SDK provider — wraps @anthropic-ai/claude-agent-sdk.
 *
 * Modeled after Paseo's ClaudeAgentSession but simplified:
 * - Single query pump loop reading SDK messages
 * - Translates SDK messages into AgentStreamEvent
 * - Supports multi-turn via query re-invocation
 */
import type { AgentSessionConfig, AgentStreamEvent, AgentUsage } from "../types.js";
import type { PermissionHandler } from "../permission-handler.js";
export interface ClaudeSessionHandle {
    id: string;
    claudeSessionId: string | null;
    query: AsyncGenerator<any> | null;
    input: {
        push(msg: any): void;
        iterable: AsyncIterable<any>;
    } | null;
    closed: boolean;
    turnSeq: number;
    usage: AgentUsage;
    recoveryAttempts: number;
    stderr: string;
}
/**
 * Create a Claude agent session and start the query pump.
 */
export interface ClaudePermissionContext {
    permissions: PermissionHandler;
    agentId: string;
}
export declare function createClaudeSession(config: AgentSessionConfig, onEvent: (event: AgentStreamEvent) => void, perm?: ClaudePermissionContext): ClaudeSessionHandle;
/**
 * Send a follow-up query to an existing session.
 */
export declare function sendClaudeQuery(handle: ClaudeSessionHandle, prompt: string, onEvent: (event: AgentStreamEvent) => void): void;
/**
 * Close a Claude session.
 */
export declare function closeClaudeSession(handle: ClaudeSessionHandle): Promise<void>;
