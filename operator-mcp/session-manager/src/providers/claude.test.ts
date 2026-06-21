/**
 * Tests for the Claude provider's pure translation/recovery helpers.
 * Run with: npm test  (compiles, then `node --test dist`).
 *
 * These cover the highest-risk pure units called out in the maturity review:
 * SDK message translation, context-corruption detection, and the continuation
 * summary used to recover a session after a tool_use_id mismatch.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  translateMessage,
  buildContinuationSummary,
  isToolIdMismatchError,
  createToolCallStreamState,
} from "./claude.js";
import type { AgentStreamEvent } from "../types.js";

// -- isToolIdMismatchError ---------------------------------------------------

test("isToolIdMismatchError detects the unexpected tool_use_id variants", () => {
  assert.equal(isToolIdMismatchError(new Error("unexpected `tool_use_id`")), true);
  assert.equal(isToolIdMismatchError(new Error("unexpected tool_use_id found")), true);
  assert.equal(
    isToolIdMismatchError(new Error("tool_result without a corresponding `tool_use` block")),
    true,
  );
  assert.equal(
    isToolIdMismatchError(new Error("400 Bad Request: tool_result references missing tool_use")),
    true,
  );
});

test("isToolIdMismatchError inspects the cause and error fields", () => {
  const withCause = new Error("request failed");
  (withCause as any).cause = "unexpected `tool_use_id`";
  assert.equal(isToolIdMismatchError(withCause), true);

  const withErrorField = new Error("api error");
  (withErrorField as any).error = { message: "unexpected tool_use_id" };
  assert.equal(isToolIdMismatchError(withErrorField), true);
});

test("isToolIdMismatchError returns false for unrelated errors and non-errors", () => {
  assert.equal(isToolIdMismatchError(new Error("network timeout")), false);
  assert.equal(isToolIdMismatchError("just a string"), false);
  assert.equal(isToolIdMismatchError(undefined), false);
  assert.equal(isToolIdMismatchError({ random: "object" }), false);
});

// -- translateMessage --------------------------------------------------------

test("translateMessage extracts assistant text", () => {
  const state = createToolCallStreamState();
  const events = translateMessage(
    { type: "assistant", message: { content: [{ type: "text", text: "hello" }] } },
    "turn-1",
    state,
  );
  assert.deepEqual(events, [
    { type: "timeline", item: { type: "assistant_message", text: "hello" } },
  ]);
});

test("translateMessage emits nothing for an empty assistant message", () => {
  const state = createToolCallStreamState();
  const events = translateMessage(
    { type: "assistant", message: { content: [] } },
    "turn-1",
    state,
  );
  assert.deepEqual(events, []);
});

test("translateMessage emits a user_message timeline item", () => {
  const state = createToolCallStreamState();
  const events = translateMessage(
    { type: "user", message: { content: [{ type: "text", text: "do the thing" }] } },
    "turn-1",
    state,
  );
  assert.deepEqual(events, [
    { type: "timeline", item: { type: "user_message", text: "do the thing" } },
  ]);
});

test("translateMessage resolves tool_result names from the id→name map", () => {
  const state = createToolCallStreamState();
  state.idToName.set("tid-1", "Read");
  const events = translateMessage(
    {
      type: "user",
      message: {
        content: [
          { type: "tool_result", tool_use_id: "tid-1", content: "file contents", is_error: false },
        ],
      },
    },
    "turn-1",
    state,
  );
  assert.equal(events.length, 1);
  const item = (events[0] as any).item;
  assert.equal(item.type, "tool_call");
  assert.equal(item.name, "Read");
  assert.equal(item.status, "completed");
  assert.equal(item.result, "file contents");
});

test("translateMessage marks failed tool_result blocks as failed", () => {
  const state = createToolCallStreamState();
  state.idToName.set("tid-err", "Bash");
  const events = translateMessage(
    {
      type: "user",
      message: { content: [{ type: "tool_result", tool_use_id: "tid-err", content: "boom", is_error: true }] },
    },
    "turn-1",
    state,
  );
  const item = (events[0] as any).item;
  assert.equal(item.status, "failed");
  assert.equal(item.error, "boom");
});

test("translateMessage maps a successful result to turn_completed", () => {
  const state = createToolCallStreamState();
  const events = translateMessage(
    { type: "result", subtype: "success", usage: { input_tokens: 10, output_tokens: 5 } },
    "turn-7",
    state,
  );
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "turn_completed");
  assert.equal((events[0] as any).turnId, "turn-7");
  assert.equal((events[0] as any).usage.inputTokens, 10);
  assert.equal((events[0] as any).usage.outputTokens, 5);
});

test("translateMessage maps a failed result to turn_failed with stderr tail", () => {
  const state = createToolCallStreamState();
  const events = translateMessage(
    { type: "result", subtype: "error", error: "exploded" },
    "turn-7",
    state,
    "tail of stderr",
  );
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "turn_failed");
  assert.equal((events[0] as any).error, "exploded");
  assert.equal((events[0] as any).stderrTail, "tail of stderr");
});

test("translateMessage captures the session id from a system init message", () => {
  const state = createToolCallStreamState();
  const events = translateMessage(
    { type: "system", subtype: "init", session_id: "sess-abc" },
    "turn-1",
    state,
  );
  assert.deepEqual(events, [
    { type: "session_started", sessionId: "sess-abc", provider: "claude" },
  ]);
});

test("translateMessage assembles streamed tool_use args across stream_event blocks", () => {
  const state = createToolCallStreamState();
  // content_block_start registers the pending tool call
  translateMessage(
    { type: "stream_event", event: { type: "content_block_start", index: 0, content_block: { type: "tool_use", id: "t1", name: "Edit" } } },
    "turn-1",
    state,
  );
  // input_json_delta chunks accumulate the args
  translateMessage(
    { type: "stream_event", event: { type: "content_block_delta", index: 0, delta: { type: "input_json_delta", partial_json: '{"file_path":' } } },
    "turn-1",
    state,
  );
  translateMessage(
    { type: "stream_event", event: { type: "content_block_delta", index: 0, delta: { type: "input_json_delta", partial_json: '"/tmp/x"}' } } },
    "turn-1",
    state,
  );
  // content_block_stop emits the fully-assembled tool_call
  const events = translateMessage(
    { type: "stream_event", event: { type: "content_block_stop", index: 0 } },
    "turn-1",
    state,
  );
  assert.equal(events.length, 1);
  const item = (events[0] as any).item;
  assert.equal(item.type, "tool_call");
  assert.equal(item.name, "Edit");
  assert.equal(item.status, "running");
  assert.equal(item.args, '{"file_path":"/tmp/x"}');
  // id→name was recorded so a later tool_result can resolve the name
  assert.equal(state.idToName.get("t1"), "Edit");
});

// -- buildContinuationSummary ------------------------------------------------

test("buildContinuationSummary includes the original prompt and a recovery preamble", () => {
  const summary = buildContinuationSummary([], "Build me a website");
  assert.match(summary, /previous session was interrupted/);
  assert.match(summary, /## Original User Request/);
  assert.match(summary, /Build me a website/);
  assert.match(summary, /## Instructions/);
});

test("buildContinuationSummary truncates very long original prompts", () => {
  const longPrompt = "x".repeat(5000);
  const summary = buildContinuationSummary([], longPrompt);
  assert.match(summary, /\.\.\.\(truncated\)/);
  assert.ok(!summary.includes("x".repeat(4001)), "prompt body is capped at 4000 chars");
});

test("buildContinuationSummary folds in messages and completed tool calls", () => {
  const events: AgentStreamEvent[] = [
    { type: "timeline", item: { type: "user_message", text: "follow-up question" } },
    { type: "timeline", item: { type: "assistant_message", text: "working on it" } },
    { type: "timeline", item: { type: "tool_call", name: "Read", status: "completed", result: "ok" } },
    // A running tool call must NOT be summarized as completed
    { type: "timeline", item: { type: "tool_call", name: "Bash", status: "running" } },
  ];
  const summary = buildContinuationSummary(events, "original");
  assert.match(summary, /## Follow-up User Messages/);
  assert.match(summary, /follow-up question/);
  assert.match(summary, /## Recent Assistant Messages/);
  assert.match(summary, /working on it/);
  assert.match(summary, /## Completed Tool Calls/);
  assert.match(summary, /- Read: ok/);
  assert.ok(!summary.includes("- Bash"), "running tool calls are excluded from completed list");
});
