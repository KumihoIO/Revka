/**
 * Session Persistence — saves/restores agent state to disk.
 *
 * Agent state files live in ~/.construct/operator_mcp/agents/{agent_id}.json.
 * On sidecar startup, persisted sessions with status "running" or "idle"
 * are eligible for resume via the Claude SDK's `resume` option.
 */
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
const log = (msg) => process.stderr.write(`[session-mgr:persist] ${msg}\n`);
const AGENTS_DIR = join(process.env.HOME ?? "~", ".construct/operator_mcp/agents");
/**
 * Ensure the agents state directory exists.
 */
function ensureDir() {
    if (!existsSync(AGENTS_DIR)) {
        mkdirSync(AGENTS_DIR, { recursive: true });
    }
}
/**
 * Save agent state to disk.
 */
export function saveAgentState(info, sessionId, events) {
    ensureDir();
    const state = {
        id: info.id,
        title: info.title,
        cwd: info.cwd,
        agentType: info.provider,
        sessionId: sessionId ?? info.claudeSessionId,
        status: info.status,
        parentId: info.parentId,
        usage: info.usage ?? {},
        timelineTail: (events ?? []).slice(-20),
        createdAt: info.createdAt,
        lastActivity: new Date().toISOString(),
    };
    const filePath = join(AGENTS_DIR, `${info.id}.json`);
    try {
        writeFileSync(filePath, JSON.stringify(state, null, 2), "utf-8");
    }
    catch (err) {
        log(`Failed to save state for ${info.id}: ${err}`);
    }
}
/**
 * Remove agent state file (on close/cleanup).
 */
export function removeAgentState(agentId) {
    const filePath = join(AGENTS_DIR, `${agentId}.json`);
    try {
        if (existsSync(filePath)) {
            unlinkSync(filePath);
        }
    }
    catch (err) {
        log(`Failed to remove state for ${agentId}: ${err}`);
    }
}
/**
 * Load all persisted agent states.
 */
export function loadAllAgentStates() {
    ensureDir();
    const states = [];
    try {
        for (const file of readdirSync(AGENTS_DIR)) {
            if (!file.endsWith(".json"))
                continue;
            try {
                const content = readFileSync(join(AGENTS_DIR, file), "utf-8");
                const state = JSON.parse(content);
                states.push(state);
            }
            catch (err) {
                log(`Failed to parse state file ${file}: ${err}`);
            }
        }
    }
    catch (err) {
        log(`Failed to read agents directory: ${err}`);
    }
    return states;
}
/**
 * Get resumable agent states (those that were running or idle when sidecar stopped).
 */
export function getResumableStates() {
    return loadAllAgentStates().filter((s) => ["running", "idle"].includes(s.status) && s.agentType === "claude" && s.sessionId);
}
/**
 * Update just the status field in a persisted state.
 */
export function updateAgentStatus(agentId, status) {
    const filePath = join(AGENTS_DIR, `${agentId}.json`);
    try {
        if (!existsSync(filePath))
            return;
        const content = readFileSync(filePath, "utf-8");
        const state = JSON.parse(content);
        state.status = status;
        state.lastActivity = new Date().toISOString();
        writeFileSync(filePath, JSON.stringify(state, null, 2), "utf-8");
    }
    catch (err) {
        log(`Failed to update status for ${agentId}: ${err}`);
    }
}
//# sourceMappingURL=persistence.js.map