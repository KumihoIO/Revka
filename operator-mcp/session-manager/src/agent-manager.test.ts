/**
 * Tests for the AgentManager resume path.
 * Run with: npm test  (compiles, then `node --test dist`).
 *
 * resumePersistedSessions() rebuilds in-memory session state from the JSON
 * files persistence.ts wrote to ~/.revka/operator_mcp/agents. The maturity
 * review flagged this resume path as a likely home for an unnoticed TypeError,
 * so we exercise it end-to-end against a real (temporary) state directory.
 *
 * persistence.ts captures the agents dir from process.env.HOME at module load,
 * so we point HOME at a temp dir BEFORE importing the modules under test and
 * use a dynamic import to bind the constant to that dir.
 */
import { test, before, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let tmpHome: string;
let agentsDir: string;
let AgentManager: typeof import("./agent-manager.js").AgentManager;

before(async () => {
  tmpHome = mkdtempSync(join(tmpdir(), "revka-sm-test-"));
  process.env.HOME = tmpHome;
  agentsDir = join(tmpHome, ".revka/operator_mcp/agents");
  mkdirSync(agentsDir, { recursive: true });
  // Import AFTER HOME is set so persistence.ts binds to the temp dir.
  ({ AgentManager } = await import("./agent-manager.js"));
});

after(() => {
  rmSync(tmpHome, { recursive: true, force: true });
});

function writeState(id: string, state: Record<string, unknown>): void {
  writeFileSync(join(agentsDir, `${id}.json`), JSON.stringify(state, null, 2), "utf-8");
}

test("resumePersistedSessions restores an idle Claude session with a session id", async () => {
  writeState("agent-resume", {
    id: "agent-resume",
    title: "Resumable",
    cwd: "/work",
    agentType: "claude",
    sessionId: "sess-xyz",
    status: "idle",
    usage: { inputTokens: 3 },
    timelineTail: [],
    createdAt: "2026-01-01T00:00:00.000Z",
    lastActivity: "2026-01-01T00:00:00.000Z",
  });

  const mgr = new AgentManager();
  const resumed = await mgr.resumePersistedSessions();
  assert.equal(resumed, 1);

  const info = mgr.getAgent("agent-resume");
  assert.ok(info, "the resumed agent is listed");
  assert.equal(info!.status, "idle");
  assert.equal(info!.provider, "claude");
  assert.equal(info!.claudeSessionId, "sess-xyz");
  assert.equal(info!.title, "Resumable");
});

test("resumePersistedSessions skips Claude sessions without a session id", async () => {
  // No sessionId → getResumableStates() filters it out.
  writeState("agent-nosession", {
    id: "agent-nosession",
    title: "No Session",
    cwd: "/work",
    agentType: "claude",
    status: "idle",
    usage: {},
    timelineTail: [],
    createdAt: "2026-01-01T00:00:00.000Z",
    lastActivity: "2026-01-01T00:00:00.000Z",
  });

  const mgr = new AgentManager();
  await mgr.resumePersistedSessions();
  assert.equal(mgr.getAgent("agent-nosession"), null);
});

test("resumePersistedSessions skips Codex sessions (cannot resume)", async () => {
  writeState("agent-codex", {
    id: "agent-codex",
    title: "Codex",
    cwd: "/work",
    agentType: "codex",
    sessionId: "sess-codex",
    status: "idle",
    usage: {},
    timelineTail: [],
    createdAt: "2026-01-01T00:00:00.000Z",
    lastActivity: "2026-01-01T00:00:00.000Z",
  });

  const mgr = new AgentManager();
  await mgr.resumePersistedSessions();
  assert.equal(mgr.getAgent("agent-codex"), null);
});
