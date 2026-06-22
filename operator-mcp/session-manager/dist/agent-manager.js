/**
 * Agent Manager — lifecycle management for SDK agent sessions.
 *
 * Central coordinator: creates sessions via providers, manages state transitions,
 * dispatches events through the event emitter, and exposes the REST API surface.
 */
import { randomUUID } from "node:crypto";
import { AgentEventEmitter } from "./event-emitter.js";
import { createClaudeSession, resumeClaudeSession, sendClaudeQuery, closeClaudeSession } from "./providers/claude.js";
import { createCodexSession, sendCodexQuery, closeCodexSession } from "./providers/codex.js";
import { saveAgentState, removeAgentState, updateAgentStatus, getResumableStates } from "./persistence.js";
const log = (msg) => process.stderr.write(`[session-mgr] ${msg}\n`);
export class AgentManager {
    permissions;
    sessions = new Map();
    emitter = new AgentEventEmitter();
    /**
     * @param permissions Shared permission handler used to gate tool calls from
     * spawned (non-trusted) Claude agents. When omitted, agents are not gated.
     */
    constructor(permissions) {
        this.permissions = permissions;
    }
    /**
     * Create a new agent session.
     */
    async createAgent(config) {
        const id = randomUUID();
        const createdAt = new Date().toISOString();
        log(`Creating ${config.agentType} agent ${id} in ${config.cwd}`);
        const session = {
            id,
            config,
            handle: null, // will be set below
            status: "initializing",
            createdAt,
            usage: {},
            events: [],
        };
        this.sessions.set(id, session);
        // Event handler — receives events from provider and dispatches
        const onEvent = this.makeOnEvent(id, session);
        // Create provider session
        try {
            if (config.agentType === "claude") {
                session.handle = createClaudeSession(config, onEvent, this.permissions ? { permissions: this.permissions, agentId: id } : undefined);
            }
            else if (config.agentType === "codex" ||
                config.agentType === "agy" ||
                config.agentType === "cursor" ||
                config.agentType === "opencode") {
                session.handle = createCodexSession(config, onEvent);
            }
            else {
                throw new Error(`Unsupported agent type: ${config.agentType}`);
            }
        }
        catch (err) {
            session.status = "error";
            const error = err instanceof Error ? err.message : String(err);
            log(`Failed to create agent ${id}: ${error}`);
            this.emitter.emit(id, { type: "status_changed", status: "error" });
            throw err;
        }
        const info = this.getSessionInfo(session);
        // Persist state to disk
        const claudeSessionId = session.config.agentType === "claude"
            ? session.handle.claudeSessionId ?? undefined
            : undefined;
        saveAgentState(info, claudeSessionId, session.events);
        return info;
    }
    /**
     * Send a follow-up prompt to an existing agent.
     */
    async sendQuery(agentId, prompt) {
        const session = this.sessions.get(agentId);
        if (!session)
            throw new Error(`Agent not found: ${agentId}`);
        if (session.status === "running")
            throw new Error("Agent is still running");
        if (session.status === "closed")
            throw new Error("Agent is closed");
        log(`Sending query to ${agentId} (${prompt.length} chars)`);
        if (session.config.agentType === "claude") {
            sendClaudeQuery(session.handle, prompt, (event) => {
                session.events.push(event);
                if (event.type === "status_changed")
                    session.status = event.status;
                if (event.type === "turn_completed" && event.usage) {
                    session.usage = {
                        inputTokens: (session.usage.inputTokens ?? 0) + (event.usage.inputTokens ?? 0),
                        outputTokens: (session.usage.outputTokens ?? 0) + (event.usage.outputTokens ?? 0),
                        totalCostUsd: (session.usage.totalCostUsd ?? 0) + (event.usage.totalCostUsd ?? 0),
                        model: event.usage.model ?? session.usage.model,
                        provider: event.usage.provider ?? session.usage.provider,
                    };
                }
                this.emitter.emit(agentId, event);
            });
        }
        else {
            sendCodexQuery(session.handle, prompt, (event) => {
                session.events.push(event);
                if (event.type === "status_changed")
                    session.status = event.status;
                if (event.type === "turn_completed" && event.usage) {
                    session.usage = {
                        inputTokens: (session.usage.inputTokens ?? 0) + (event.usage.inputTokens ?? 0),
                        outputTokens: (session.usage.outputTokens ?? 0) + (event.usage.outputTokens ?? 0),
                        totalCostUsd: (session.usage.totalCostUsd ?? 0) + (event.usage.totalCostUsd ?? 0),
                        model: event.usage.model ?? session.usage.model,
                        provider: event.usage.provider ?? session.usage.provider,
                    };
                }
                this.emitter.emit(agentId, event);
            });
        }
        session.status = "running";
        return this.getSessionInfo(session);
    }
    /**
     * Close and cleanup an agent session.
     */
    async closeAgent(agentId) {
        const session = this.sessions.get(agentId);
        if (!session)
            return;
        log(`Closing agent ${agentId}`);
        session.status = "closed";
        try {
            if (session.config.agentType === "claude") {
                await closeClaudeSession(session.handle);
            }
            else {
                await closeCodexSession(session.handle);
            }
        }
        catch (err) {
            log(`Error closing agent ${agentId}: ${err}`);
        }
        this.emitter.emit(agentId, { type: "session_closed", sessionId: agentId });
        this.emitter.removeAgent(agentId);
        removeAgentState(agentId);
    }
    /**
     * Interrupt a running agent.
     */
    async interruptAgent(agentId) {
        const session = this.sessions.get(agentId);
        if (!session || session.status !== "running")
            return;
        log(`Interrupting agent ${agentId}`);
        // For Claude: close the query, for Codex: kill the process
        if (session.config.agentType === "claude") {
            const handle = session.handle;
            try {
                await handle.query?.return?.(undefined);
            }
            catch { /* ignore */ }
        }
        else {
            const handle = session.handle;
            handle.process?.kill("SIGTERM");
        }
        session.status = "idle";
        this.emitter.emit(agentId, { type: "status_changed", status: "idle" });
    }
    /**
     * Get info for a specific agent.
     */
    getAgent(agentId) {
        const session = this.sessions.get(agentId);
        if (!session)
            return null;
        return this.getSessionInfo(session);
    }
    /**
     * List all active agent sessions.
     */
    listAgents() {
        return Array.from(this.sessions.values()).map((s) => this.getSessionInfo(s));
    }
    /**
     * Get recent events for an agent (for activity/stream catchup).
     */
    getAgentEvents(agentId, since) {
        const session = this.sessions.get(agentId);
        if (!session)
            return [];
        if (since !== undefined && since > 0) {
            return session.events.slice(since);
        }
        return session.events;
    }
    /**
     * Resume persisted agent sessions on sidecar startup.
     * Only resumes Claude sessions that have a session ID (Codex cannot resume).
     */
    async resumePersistedSessions() {
        const resumable = getResumableStates();
        let resumed = 0;
        for (const state of resumable) {
            try {
                log(`Resuming agent ${state.id} (${state.title}) from session ${state.sessionId}`);
                const config = {
                    cwd: state.cwd,
                    agentType: state.agentType,
                    prompt: "", // Resume doesn't need a prompt
                    title: state.title,
                    parentId: state.parentId,
                };
                // Re-create the session entry
                const session = {
                    id: state.id,
                    config,
                    handle: null,
                    status: "idle", // Resumed sessions start as idle
                    createdAt: state.createdAt,
                    usage: state.usage,
                    events: state.timelineTail ?? [],
                };
                this.sessions.set(state.id, session);
                // Rebuild the handle through the provider so the follow-up `sendQuery`
                // closure (with its continuation-summary fallback) is attached. A plain
                // object literal — as this used to be — has no `sendQuery`, so the first
                // follow-up threw `TypeError: sendQuery is not a function` (#450). The
                // handle stays idle; the persisted timeline seeds the continuation
                // context for the first follow-up.
                const onEvent = this.makeOnEvent(state.id, session);
                session.handle = resumeClaudeSession(config, session.events, onEvent, state.sessionId ?? null, 
                // Keep resumed agents permission-gated (#449) — without this a
                // sidecar restart would silently downgrade them to ungated.
                this.permissions ? { permissions: this.permissions, agentId: state.id } : undefined);
                resumed++;
                updateAgentStatus(state.id, "idle");
                log(`Resumed agent ${state.id} (idle, ready for queries)`);
            }
            catch (err) {
                log(`Failed to resume agent ${state.id}: ${err}`);
                removeAgentState(state.id);
            }
        }
        if (resumed > 0) {
            log(`Resumed ${resumed} agent(s) from previous session`);
        }
        return resumed;
    }
    /**
     * Build the event handler that dispatches provider events into a managed
     * session: appends to the timeline, tracks status (+ persisted status),
     * accumulates usage, and rebroadcasts via the emitter. Shared by createAgent
     * and resumePersistedSessions so resumed sessions handle events identically.
     */
    makeOnEvent(id, session) {
        return (event) => {
            session.events.push(event);
            if (event.type === "status_changed") {
                session.status = event.status;
                updateAgentStatus(id, event.status);
            }
            if (event.type === "turn_completed" && event.usage) {
                session.usage = {
                    inputTokens: (session.usage.inputTokens ?? 0) + (event.usage.inputTokens ?? 0),
                    outputTokens: (session.usage.outputTokens ?? 0) + (event.usage.outputTokens ?? 0),
                    totalCostUsd: (session.usage.totalCostUsd ?? 0) + (event.usage.totalCostUsd ?? 0),
                    model: event.usage.model ?? session.usage.model,
                    provider: event.usage.provider ?? session.usage.provider,
                };
            }
            this.emitter.emit(id, event);
        };
    }
    getSessionInfo(session) {
        return {
            id: session.id,
            provider: session.config.agentType,
            status: session.status,
            title: session.config.title ?? `${session.config.agentType}-agent`,
            cwd: session.config.cwd,
            createdAt: session.createdAt,
            parentId: session.config.parentId,
            claudeSessionId: session.config.agentType === "claude"
                ? session.handle.claudeSessionId ?? undefined
                : undefined,
            usage: session.usage,
        };
    }
}
//# sourceMappingURL=agent-manager.js.map