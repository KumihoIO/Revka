/**
 * Google Agents CLI provider - runs ADK/A2A agents via `agents-cli run`.
 *
 * `agents-cli` is a lifecycle CLI, not a coding-agent SDK. The documented
 * non-interactive prompt path is `agents-cli run MESSAGE`, so this provider
 * treats each turn as a bounded subprocess execution and emits text output as
 * timeline content when the process exits.
 */
import { type ChildProcess } from "node:child_process";
import type { AgentSessionConfig, AgentStreamEvent, AgentUsage } from "../types.js";
export interface GoogleAgentsSessionHandle {
    id: string;
    process: ChildProcess | null;
    closed: boolean;
    turnSeq: number;
    stdout: string;
    stderr: string;
    usage: AgentUsage;
    sendQuery: (prompt: string, onEvent: (event: AgentStreamEvent) => void) => void;
}
/**
 * Create a Google Agents CLI session via subprocess.
 */
export declare function createGoogleAgentsSession(config: AgentSessionConfig, onEvent: (event: AgentStreamEvent) => void): GoogleAgentsSessionHandle;
/**
 * Send a follow-up query to an existing Google Agents CLI session.
 */
export declare function sendGoogleAgentsQuery(handle: GoogleAgentsSessionHandle, prompt: string, onEvent: (event: AgentStreamEvent) => void): void;
/**
 * Close a Google Agents CLI session.
 */
export declare function closeGoogleAgentsSession(handle: GoogleAgentsSessionHandle): Promise<void>;
