/**
 * Permission Handler — policy-based auto-approve with channel escalation.
 *
 * Evaluates permission requests against a policy table:
 *   - Auto-approve: read-only ops, file edits in cwd (coder role), safe bash, MCP tools
 *   - Escalate: network access, destructive commands, out-of-cwd writes
 *
 * Escalated requests are emitted as events for the operator to forward
 * to gateway → channels → user. The user's response routes back through
 * respond_to_permission().
 */
import { randomUUID } from "node:crypto";
const log = (msg) => process.stderr.write(`[session-mgr:perm] ${msg}\n`);
// -- Policy rules ------------------------------------------------------------
const READ_ONLY_TOOLS = new Set([
    "Read", "Glob", "Grep", "ListMcpResourcesTool", "ReadMcpResourceTool",
    "WebFetch", "WebSearch", "ToolSearch",
    "cat", "head", "tail", "ls", "find", "grep", "rg",
]);
const SAFE_EDIT_TOOLS = new Set([
    "Edit", "Write", "NotebookEdit",
    "edit_file", "create_file", "write_file",
]);
const DESTRUCTIVE_BASH_PATTERNS = [
    /\brm\s+-rf?\b/i,
    /\bgit\s+push\s+--force\b/i,
    /\bgit\s+reset\s+--hard\b/i,
    /\bcurl\b/i,
    /\bwget\b/i,
    /\bssh\b/i,
    /\bnc\b/i,
    /\bnetcat\b/i,
    /\bsudo\b/i,
    /\bdocker\b/i,
    /\bkubectl\b/i,
    /\bnpm\s+publish\b/i,
    /\bcargo\s+publish\b/i,
];
function isReadOnly(tool) {
    return READ_ONLY_TOOLS.has(tool);
}
function isSafeEdit(tool, args, cwd) {
    if (!SAFE_EDIT_TOOLS.has(tool))
        return false;
    // Check that the file path is within the working directory
    const filePath = args.file_path ?? args.path ?? "";
    if (filePath && !filePath.startsWith(cwd)) {
        return false; // editing outside cwd — escalate
    }
    return true;
}
function isSafeBash(args) {
    const command = args.command ?? "";
    for (const pattern of DESTRUCTIVE_BASH_PATTERNS) {
        if (pattern.test(command))
            return false;
    }
    return true;
}
function isMcpTool(tool) {
    return tool.startsWith("mcp__");
}
// -- Permission Handler class ------------------------------------------------
export class PermissionHandler {
    pending = new Map();
    history = [];
    /**
     * Evaluate a permission request against the policy table.
     * Returns "approve" | "deny" | "escalate".
     */
    evaluate(tool, args, cwd, role) {
        // Read-only tools — always approve
        if (isReadOnly(tool))
            return "approve";
        // MCP tools — approve by default
        if (isMcpTool(tool))
            return "approve";
        // File edits in cwd — approve for coder role
        if (isSafeEdit(tool, args, cwd)) {
            if (role === "coder" || role === "researcher")
                return "approve";
            // Non-coder editing files — still approve but log
            log(`Non-coder (${role}) editing file: ${args.file_path ?? args.path ?? "?"}`);
            return "approve";
        }
        // Bash commands
        if (tool === "Bash" || tool === "execute_command") {
            if (isSafeBash(args))
                return "approve";
            log(`Potentially unsafe bash: ${(args.command ?? "").slice(0, 100)}`);
            return "escalate";
        }
        // Agent tool — approve
        if (tool === "Agent")
            return "approve";
        // Everything else — escalate
        return "escalate";
    }
    /**
     * Create a pending permission request (for escalated operations).
     * Returns a promise that resolves when the user responds.
     */
    createPendingRequest(agentId, agentTitle, tool, args, cwd, role, onEvent) {
        const request = {
            id: randomUUID(),
            agentId,
            agentTitle,
            tool,
            args,
            cwd,
            role,
            status: "pending",
            createdAt: new Date().toISOString(),
        };
        log(`Permission escalated: ${tool} from ${agentTitle} (${request.id})`);
        // Emit event for operator → gateway → channel
        onEvent({
            type: "timeline",
            item: {
                type: "tool_call",
                name: `permission:${tool}`,
                args: JSON.stringify(args),
                status: "running",
            },
        });
        return new Promise((resolve) => {
            this.pending.set(request.id, { request, resolve });
            this.history.push(request);
            // Auto-deny after 5 minutes if no response. unref() so a pending request
            // never keeps the process (or a test runner) alive on its own.
            const timer = setTimeout(() => {
                if (this.pending.has(request.id)) {
                    log(`Permission auto-denied (timeout): ${request.id}`);
                    this.respond(request.id, "deny", "auto-timeout");
                }
            }, 5 * 60 * 1000);
            timer.unref?.();
        });
    }
    /**
     * Respond to a pending permission request.
     */
    respond(requestId, action, by = "user") {
        const entry = this.pending.get(requestId);
        if (!entry)
            return false;
        entry.request.status = action === "approve" ? "approved" : "denied";
        entry.request.resolvedAt = new Date().toISOString();
        entry.request.resolvedBy = by;
        this.pending.delete(requestId);
        entry.resolve(action);
        log(`Permission ${action}: ${requestId} by ${by}`);
        return true;
    }
    /**
     * List all pending permission requests.
     */
    listPending() {
        return Array.from(this.pending.values()).map((e) => e.request);
    }
    /**
     * Get a specific permission request.
     */
    getRequest(requestId) {
        return this.pending.get(requestId)?.request
            ?? this.history.find((r) => r.id === requestId);
    }
    /**
     * Get recent permission history.
     */
    getHistory(limit = 50) {
        return this.history.slice(-limit);
    }
}
//# sourceMappingURL=permission-handler.js.map