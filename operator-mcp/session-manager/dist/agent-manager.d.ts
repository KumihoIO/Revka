/**
 * Agent Manager — lifecycle management for SDK agent sessions.
 *
 * Central coordinator: creates sessions via providers, manages state transitions,
 * dispatches events through the event emitter, and exposes the REST API surface.
 */
import type { AgentSessionConfig, AgentSessionInfo, AgentStreamEvent } from "./types.js";
import { AgentEventEmitter } from "./event-emitter.js";
import type { PermissionHandler } from "./permission-handler.js";
export declare class AgentManager {
    private readonly permissions?;
    private sessions;
    readonly emitter: AgentEventEmitter;
    /**
     * @param permissions Shared permission handler used to gate tool calls from
     * spawned (non-trusted) Claude agents. When omitted, agents are not gated.
     */
    constructor(permissions?: PermissionHandler | undefined);
    /**
     * Create a new agent session.
     */
    createAgent(config: AgentSessionConfig): Promise<AgentSessionInfo>;
    /**
     * Send a follow-up prompt to an existing agent.
     */
    sendQuery(agentId: string, prompt: string): Promise<AgentSessionInfo>;
    /**
     * Close and cleanup an agent session.
     */
    closeAgent(agentId: string): Promise<void>;
    /**
     * Interrupt a running agent.
     */
    interruptAgent(agentId: string): Promise<void>;
    /**
     * Get info for a specific agent.
     */
    getAgent(agentId: string): AgentSessionInfo | null;
    /**
     * List all active agent sessions.
     */
    listAgents(): AgentSessionInfo[];
    /**
     * Get recent events for an agent (for activity/stream catchup).
     */
    getAgentEvents(agentId: string, since?: number): AgentStreamEvent[];
    /**
     * Resume persisted agent sessions on sidecar startup.
     * Only resumes Claude sessions that have a session ID (Codex cannot resume).
     */
    resumePersistedSessions(): Promise<number>;
    /**
     * Build the event handler that dispatches provider events into a managed
     * session: appends to the timeline, tracks status (+ persisted status),
     * accumulates usage, and rebroadcasts via the emitter. Shared by createAgent
     * and resumePersistedSessions so resumed sessions handle events identically.
     */
    private makeOnEvent;
    private getSessionInfo;
}
