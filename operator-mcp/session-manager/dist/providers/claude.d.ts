/**
 * Claude Agent SDK provider — wraps @anthropic-ai/claude-agent-sdk.
 *
 * Modeled after Paseo's ClaudeAgentSession but simplified:
 * - Single query pump loop reading SDK messages
 * - Translates SDK messages into AgentStreamEvent
 * - Supports multi-turn via query re-invocation
 */
import type { AgentSessionConfig, AgentStreamEvent, AgentUsage } from "../types.js";
/**
 * Detects if an error is a tool_use_id mismatch (orphaned tool_result after context truncation).
 */
export declare function isToolIdMismatchError(err: unknown): boolean;
/**
 * Build a continuation summary from session events for recovery after context corruption.
 */
export declare function buildContinuationSummary(events: AgentStreamEvent[], originalPrompt?: string): string;
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
 * Per-session state for tracking in-flight tool calls across stream events.
 * Accumulates input_json_delta chunks so the emitted tool_call has full args.
 */
interface ToolCallStreamState {
    /** content_block index → pending tool call info */
    pending: Map<number, {
        id: string;
        name: string;
        inputChunks: string[];
    }>;
    /** tool_use_id → tool name, for resolving tool_result blocks */
    idToName: Map<string, string>;
}
export declare function createToolCallStreamState(): ToolCallStreamState;
/**
 * Translate a raw SDK message into zero or more AgentStreamEvents.
 */
export declare function translateMessage(message: any, turnId: string, state: ToolCallStreamState, stderrTail?: string): AgentStreamEvent[];
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
export {};
