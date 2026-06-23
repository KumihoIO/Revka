/**
 * Codex CLI provider — spawns codex as subprocess.
 *
 * Unlike Claude which has a proper SDK, Codex is driven via CLI subprocess.
 * This is a thin wrapper that captures stdout/stderr and emits timeline events.
 */
import { type ChildProcess } from "node:child_process";
import type { AgentSessionConfig, AgentStreamEvent, AgentUsage } from "../types.js";
/**
 * #459: reason to refuse an untrusted permission-bypassing CLI spawn, or null.
 *
 * An explicitly-untrusted spawn (config.trusted === false) of a CLI we would
 * otherwise launch with permissions bypassed is refused rather than run
 * ungated — there is no headless-safe sandbox/approval flag to downgrade to.
 * Trusted or unset (default) spawns always proceed; opencode is never refused.
 */
export declare function codexSpawnRefusal(agentType: string, trusted: boolean | undefined): string | null;
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
