/**
 * Google Agents CLI provider - runs ADK/A2A agents via `agents-cli run`.
 *
 * `agents-cli` is a lifecycle CLI, not a coding-agent SDK. The documented
 * non-interactive prompt path is `agents-cli run MESSAGE`, so this provider
 * treats each turn as a bounded subprocess execution and emits text output as
 * timeline content when the process exits.
 */
import { spawn } from "node:child_process";
const log = (msg) => process.stderr.write(`[session-mgr:google-agents] ${msg}\n`);
/**
 * Create a Google Agents CLI session via subprocess.
 */
export function createGoogleAgentsSession(config, onEvent) {
    const runPrompt = (handle, prompt, emit) => {
        const turnId = `turn-${++handle.turnSeq}`;
        emit({ type: "turn_started", turnId });
        emit({ type: "status_changed", status: "running" });
        handle.stdout = "";
        handle.stderr = "";
        handle.usage = {
            model: config.model,
            provider: "google_agents",
        };
        const args = ["run", prompt];
        log(`Spawning agents-cli: run ... (${prompt.length} chars)`);
        const proc = spawn("agents-cli", args, {
            cwd: config.cwd,
            stdio: ["ignore", "pipe", "pipe"],
            env: { ...process.env, ...(config.env ?? {}) },
        });
        handle.process = proc;
        proc.stdout?.on("data", (chunk) => {
            handle.stdout += chunk.toString("utf-8");
        });
        proc.stderr?.on("data", (chunk) => {
            handle.stderr += chunk.toString("utf-8");
        });
        proc.on("close", (code) => {
            handle.process = null;
            if (handle.closed)
                return;
            if (code === 0) {
                const text = handle.stdout.trim();
                if (text) {
                    emit({ type: "timeline", item: { type: "assistant_message", text } });
                }
                emit({ type: "turn_completed", turnId, usage: handle.usage });
                emit({ type: "status_changed", status: "idle" });
            }
            else {
                const error = handle.stderr.slice(-500) || `Process exited with code ${code}`;
                emit({
                    type: "turn_failed",
                    turnId,
                    error,
                    exitCode: code,
                    stderrTail: handle.stderr.slice(-2000),
                });
                emit({ type: "status_changed", status: "error" });
            }
        });
        proc.on("error", (err) => {
            handle.process = null;
            if (handle.closed)
                return;
            emit({
                type: "turn_failed",
                turnId,
                error: err.message,
                exitCode: null,
                stderrTail: handle.stderr.slice(-2000),
            });
            emit({ type: "status_changed", status: "error" });
        });
    };
    let handle;
    handle = {
        id: config.title ?? "google-agents-session",
        process: null,
        closed: false,
        turnSeq: 0,
        stdout: "",
        stderr: "",
        usage: {},
        sendQuery(prompt, emit) {
            if (handle.closed)
                throw new Error("Session is closed");
            runPrompt(handle, prompt, emit);
        },
    };
    runPrompt(handle, config.prompt, onEvent);
    return handle;
}
/**
 * Send a follow-up query to an existing Google Agents CLI session.
 */
export function sendGoogleAgentsQuery(handle, prompt, onEvent) {
    handle.sendQuery(prompt, onEvent);
}
/**
 * Close a Google Agents CLI session.
 */
export async function closeGoogleAgentsSession(handle) {
    handle.closed = true;
    if (handle.process) {
        handle.process.kill("SIGTERM");
        handle.process = null;
    }
}
//# sourceMappingURL=google-agents.js.map