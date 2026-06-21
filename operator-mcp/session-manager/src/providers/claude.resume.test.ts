/**
 * Regression tests for #450 — resumed Claude sessions must expose `sendQuery`.
 *
 * Before the fix, `resumePersistedSessions()` hand-built the handle as a plain
 * object literal with no `sendQuery`, so the first follow-up threw
 * `TypeError: sendQuery is not a function`. These tests pin the rebuilt handle's
 * shape without spawning the SDK (they never invoke the follow-up, which would
 * start a real query pump).
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { resumeClaudeSession } from "./claude.js";
import type { AgentSessionConfig, AgentStreamEvent } from "../types.js";

const baseConfig: AgentSessionConfig = {
  cwd: process.cwd(),
  agentType: "claude",
  prompt: "", // resume has no live prompt
  title: "resumed-agent",
};

test("resumeClaudeSession returns a dormant handle with a sendQuery method", () => {
  const persisted: AgentStreamEvent[] = [
    { type: "timeline", item: { type: "user_message", text: "original task" } },
  ];

  const handle = resumeClaudeSession(baseConfig, persisted, () => {}, "sess-123");

  // The core #450 regression: a resumed handle MUST expose sendQuery. The old
  // object-literal handle did not, so the first follow-up threw a TypeError.
  assert.equal(
    typeof (handle as unknown as { sendQuery?: unknown }).sendQuery,
    "function",
    "resumed handle must have a sendQuery method",
  );
  // It stays dormant (no live pump) until the first follow-up arrives.
  assert.equal(handle.query, null);
  assert.equal(handle.input, null);
  // It carries the persisted session id and initializes recovery state — the
  // latter was also missing from the old literal.
  assert.equal(handle.claudeSessionId, "sess-123");
  assert.equal(handle.recoveryAttempts, 0);
  assert.equal(handle.closed, false);
});

test("resumeClaudeSession is silent on construction (no eager turn)", () => {
  const events: AgentStreamEvent[] = [];

  resumeClaudeSession(baseConfig, [], (e) => events.push(e), null);

  // A resumed handle must not emit or start a turn until a follow-up — unlike
  // createClaudeSession, which begins running immediately.
  assert.deepEqual(events, []);
});

test("resumeClaudeSession tolerates a null persisted session id", () => {
  const handle = resumeClaudeSession(baseConfig, [], () => {}, null);
  assert.equal(handle.claudeSessionId, null);
  assert.equal(typeof (handle as unknown as { sendQuery?: unknown }).sendQuery, "function");
});
