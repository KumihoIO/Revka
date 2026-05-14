/**
 * Codex CLI provider — spawns codex as subprocess.
 *
 * Unlike Claude which has a proper SDK, Codex is driven via CLI subprocess.
 * This is a thin wrapper that captures stdout/stderr and emits timeline events.
 */
import { type ChildProcess } from "node:child_process";
import type { AgentSessionConfig, AgentStreamEvent, AgentUsage } from "../types.js";
export interface CodexSessionHandle {
    id: string;
    process: ChildProcess | null;
    closed: boolean;
    turnSeq: number;
    stdout: string;
    stderr: string;
    usage: AgentUsage;
    jsonBuffer: string;
}
/**
 * Create a Codex agent session via subprocess.
 */
export declare function createCodexSession(config: AgentSessionConfig, onEvent: (event: AgentStreamEvent) => void): CodexSessionHandle;
/**
 * Send a follow-up query to an existing Codex session.
 */
export declare function sendCodexQuery(handle: CodexSessionHandle, prompt: string, onEvent: (event: AgentStreamEvent) => void): void;
/**
 * Close a Codex session.
 */
export declare function closeCodexSession(handle: CodexSessionHandle): Promise<void>;
