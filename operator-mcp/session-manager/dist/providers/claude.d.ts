/**
 * Claude Agent SDK provider — wraps @anthropic-ai/claude-agent-sdk.
 *
 * Modeled after Paseo's ClaudeAgentSession but simplified:
 * - Single query pump loop reading SDK messages
 * - Translates SDK messages into AgentStreamEvent
 * - Supports multi-turn via query re-invocation
 */
import type { AgentSessionConfig, AgentStreamEvent, AgentUsage } from "../types.js";
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
 * Create a Claude agent session and start the query pump immediately.
 */
export declare function createClaudeSession(config: AgentSessionConfig, onEvent: (event: AgentStreamEvent) => void): ClaudeSessionHandle;
/**
 * Rebuild a dormant, resumable Claude handle on sidecar restart.
 *
 * The handle carries the persisted timeline (`persistedEvents`) as recovery
 * context and stays idle (no pump) until the first follow-up, at which point
 * `sendQuery` starts a fresh pump seeded with a continuation summary — the same
 * path `createClaudeSession` uses after its pump dies. This matches the
 * provider's deliberate choice NOT to use the SDK `resume` option (see
 * `buildClaudeOptions`), avoiding orphaned-`tool_result` 400s. `claudeSessionId`
 * is carried for reference only; it is not fed to the SDK.
 */
export declare function resumeClaudeSession(config: AgentSessionConfig, persistedEvents: AgentStreamEvent[], onEvent: (event: AgentStreamEvent) => void, claudeSessionId: string | null): ClaudeSessionHandle;
/**
 * Send a follow-up query to an existing session.
 */
export declare function sendClaudeQuery(handle: ClaudeSessionHandle, prompt: string, onEvent: (event: AgentStreamEvent) => void): void;
/**
 * Close a Claude session.
 */
export declare function closeClaudeSession(handle: ClaudeSessionHandle): Promise<void>;
