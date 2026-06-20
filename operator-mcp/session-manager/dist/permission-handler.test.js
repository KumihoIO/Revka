/**
 * Tests for the permission policy engine and the escalate → respond flow.
 * Run with: npm test  (compiles, then `node --test dist`).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { PermissionHandler } from "./permission-handler.js";
test("read-only and MCP tools auto-approve", () => {
    const p = new PermissionHandler();
    assert.equal(p.evaluate("Read", {}, "/cwd", "coder"), "approve");
    assert.equal(p.evaluate("Grep", {}, "/cwd", "coder"), "approve");
    assert.equal(p.evaluate("mcp__revka__foo", {}, "/cwd", "coder"), "approve");
});
test("safe bash approves; destructive/network bash escalates", () => {
    const p = new PermissionHandler();
    assert.equal(p.evaluate("Bash", { command: "ls -la" }, "/cwd", "coder"), "approve");
    assert.equal(p.evaluate("Bash", { command: "rm -rf /tmp/x" }, "/cwd", "coder"), "escalate");
    assert.equal(p.evaluate("Bash", { command: "curl https://evil.test" }, "/cwd", "coder"), "escalate");
});
test("out-of-cwd file write escalates; in-cwd write approves", () => {
    const p = new PermissionHandler();
    assert.equal(p.evaluate("Write", { file_path: "/cwd/a.txt" }, "/cwd", "coder"), "approve");
    assert.equal(p.evaluate("Write", { file_path: "/etc/passwd" }, "/cwd", "coder"), "escalate");
});
test("escalation creates a pending request and respond(deny) rejects it", async () => {
    const p = new PermissionHandler();
    const events = [];
    const decision = p.createPendingRequest("agent-1", "Agent One", "Bash", { command: "rm -rf x" }, "/cwd", "coder", (e) => events.push(e));
    const pending = p.listPending();
    assert.equal(pending.length, 1);
    assert.equal(pending[0].tool, "Bash");
    assert.equal(pending[0].agentId, "agent-1");
    assert.ok(events.some((e) => e.type === "timeline"), "an escalation event is emitted");
    assert.equal(p.respond(pending[0].id, "deny", "test"), true);
    assert.equal(await decision, "deny");
    assert.equal(p.listPending().length, 0);
});
test("respond(approve) resolves the pending request to approve", async () => {
    const p = new PermissionHandler();
    const decision = p.createPendingRequest("a", "A", "Bash", { command: "curl x" }, "/cwd", "coder", () => { });
    const id = p.listPending()[0].id;
    assert.equal(p.respond(id, "approve", "test"), true);
    assert.equal(await decision, "approve");
});
test("respond on an unknown id returns false", () => {
    const p = new PermissionHandler();
    assert.equal(p.respond("does-not-exist", "approve"), false);
});
//# sourceMappingURL=permission-handler.test.js.map