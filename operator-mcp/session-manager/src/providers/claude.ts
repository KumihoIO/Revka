/**
 * Claude Agent SDK provider — wraps @anthropic-ai/claude-agent-sdk.
 *
 * Modeled after Paseo's ClaudeAgentSession but simplified:
 * - Single query pump loop reading SDK messages
 * - Translates SDK messages into AgentStreamEvent
 * - Supports multi-turn via query re-invocation
 */

import { query as claudeQuery, type Options as ClaudeOptions } from "@anthropic-ai/claude-agent-sdk";
import type { AgentSessionConfig, AgentStreamEvent, AgentUsage, McpServerConfig, TimelineItem } from "../types.js";

type SDKMessage = Awaited<ReturnType<typeof claudeQuery>> extends AsyncGenerator<infer T> ? T : never;

const log = (msg: string) => process.stderr.write(`[session-mgr:claude] ${msg}\n`);

/**
 * Detects if an error is a tool_use_id mismatch (orphaned tool_result after context truncation).
 */
function isToolIdMismatchError(err: unknown): boolean {
  // Check the error message, its cause chain, and stringified form
  const parts: string[] = [];
  if (err instanceof Error) {
    parts.push(err.message);
    if ((err as any).cause) parts.push(String((err as any).cause));
    if ((err as any).error) parts.push(JSON.stringify((err as any).error));
  }
  parts.push(String(err));
  const full = parts.join(" ");
  return (
    full.includes("unexpected `tool_use_id`") ||
    full.includes("unexpected tool_use_id") ||
    (full.includes("tool_result") && full.includes("corresponding `tool_use`")) ||
    (full.includes("400") && full.includes("tool_result") && full.includes("tool_use"))
  );
}

/**
 * Build a continuation summary from session events for recovery after context corruption.
 */
function buildContinuationSummary(events: AgentStreamEvent[], originalPrompt?: string): string {
  const parts: string[] = [
    "IMPORTANT: Your previous session was interrupted due to a context window issue.",
    "Here is a summary of what you accomplished so far. Continue from where you left off.\n",
  ];

  // Include original user request so the agent remembers its task
  if (originalPrompt) {
    parts.push("## Original User Request");
    // Truncate very long prompts but keep enough for full task context
    parts.push(originalPrompt.length > 4000 ? originalPrompt.slice(0, 4000) + "\n...(truncated)" : originalPrompt);
    parts.push("");
  }

  // Collect user messages, assistant messages, and completed tool calls
  const userMessages: string[] = [];
  const assistantMessages: string[] = [];
  const completedTools: string[] = [];

  for (const event of events) {
    if (event.type === "timeline") {
      const item = event.item;
      if (item.type === "user_message" && item.text) {
        userMessages.push(item.text);
      } else if (item.type === "assistant_message" && item.text) {
        assistantMessages.push(item.text);
      } else if (item.type === "tool_call" && item.status === "completed" && item.name) {
        const summary = item.result ? item.result.slice(0, 500) : "(completed)";
        completedTools.push(`- ${item.name}: ${summary}`);
      }
    }
  }

  // Include follow-up user messages (beyond the original prompt)
  if (userMessages.length > 0) {
    const recent = userMessages.slice(-3);
    parts.push("## Follow-up User Messages");
    parts.push(recent.join("\n\n"));
    parts.push("");
  }

  if (assistantMessages.length > 0) {
    // Keep last few assistant messages for context
    const recent = assistantMessages.slice(-3);
    parts.push("## Recent Assistant Messages");
    parts.push(recent.join("\n\n"));
  }

  if (completedTools.length > 0) {
    // Keep last 10 tool completions
    const recent = completedTools.slice(-10);
    parts.push("\n## Completed Tool Calls");
    parts.push(recent.join("\n"));
  }

  parts.push("\n## Instructions");
  parts.push("Continue your work from where you left off. Do not repeat already-completed steps.");
  parts.push("Your primary task is described in the 'Original User Request' section above — do NOT ignore it.");

  return parts.join("\n");
}

/**
 * Find the first user-message text in a timeline, used to recover the original
 * prompt for a resumed session (whose live config.prompt is empty).
 */
function firstUserMessageText(events: AgentStreamEvent[]): string | undefined {
  for (const event of events) {
    if (event.type === "timeline" && event.item.type === "user_message" && event.item.text) {
      return event.item.text;
    }
  }
  return undefined;
}

export interface ClaudeSessionHandle {
  id: string;
  claudeSessionId: string | null;
  query: AsyncGenerator<any> | null;
  input: { push(msg: any): void; iterable: AsyncIterable<any> } | null;
  closed: boolean;
  turnSeq: number;
  usage: AgentUsage;
  recoveryAttempts: number;
  stderr: string;
}

/**
 * Creates a reusable async message input channel (producer/consumer pair).
 * The `push()` side queues user messages; the `iterable` side yields them
 * into the Claude SDK query pump.
 */
function createAsyncInput<T>(): { push: (item: T) => void; iterable: AsyncIterable<T> } {
  const queue: T[] = [];
  let resolve: (() => void) | null = null;

  return {
    push(item: T) {
      queue.push(item);
      resolve?.();
    },
    iterable: {
      [Symbol.asyncIterator]() {
        return {
          async next(): Promise<IteratorResult<T>> {
            while (queue.length === 0) {
              await new Promise<void>((r) => { resolve = r; });
              resolve = null;
            }
            return { value: queue.shift()!, done: false };
          },
        };
      },
    },
  };
}

/**
 * Build a properly formatted SDK stream-json input message.
 *
 * The Claude Agent SDK's `streamInput()` writes whatever the async iterable
 * yields directly to the CLI's stdin.  The CLI expects the full envelope:
 *   {type, session_id, message: {role, content: [{type, text}]}, parent_tool_use_id}
 * NOT the bare {role, content} that the SDK's own Session.send() accepts.
 */
function buildUserMessage(text: string): Record<string, any> {
  return {
    type: "user",
    session_id: "",
    message: { role: "user", content: [{ type: "text", text }] },
    parent_tool_use_id: null,
  };
}

/**
 * Normalize Revka McpServerConfig to Claude SDK format.
 */
function normalizeMcpServers(
  servers: Record<string, McpServerConfig>,
): Record<string, { type: string; command?: string; args?: string[]; env?: Record<string, string>; url?: string }> {
  const result: Record<string, any> = {};
  for (const [name, config] of Object.entries(servers)) {
    if (config.type === "stdio") {
      result[name] = {
        type: "stdio",
        command: config.command,
        args: config.args,
        env: config.env,
      };
    } else if (config.type === "http") {
      result[name] = {
        type: "http",
        url: config.url,
        headers: config.headers,
      };
    }
  }
  return result;
}

/**
 * Build Claude SDK options from a Revka session config.
 */
function buildClaudeOptions(config: AgentSessionConfig, onStderr?: (data: string) => void): ClaudeOptions {
  const opts: ClaudeOptions = {
    cwd: config.cwd,
    permissionMode: "bypassPermissions" as any,
    includePartialMessages: true,
    env: {
      ...process.env,
      MCP_TIMEOUT: "600000",
      MCP_TOOL_TIMEOUT: "600000",
      ...(config.env ?? {}),
    },
    stderr: (data: string) => {
      onStderr?.(data);
      log(`stderr: ${data.trim()}`);
    },
  };

  if (config.systemPrompt) {
    opts.systemPrompt = {
      type: "preset",
      preset: "claude_code",
      append: config.systemPrompt,
    } as any;
  }

  if (config.mcpServers) {
    opts.mcpServers = normalizeMcpServers(config.mcpServers) as any;
  }

  if (config.model) {
    opts.model = config.model;
  }

  // Do NOT use resume — it loads conversation history from disk which can have
  // orphaned tool_result blocks after compaction, causing 400 errors.
  // We manage our own continuation context via buildContinuationSummary().

  // Don't persist sessions to disk — we manage state ourselves via the sidecar.
  // This prevents stale session files from causing resume corruption.
  opts.persistSession = false;

  // Enable auto-compaction via Settings to prevent context overflow.
  // autoCompactWindow controls the token threshold at which the CLI auto-compacts.
  // Lower values = more aggressive compaction = fewer 400 errors from tool_use_id mismatch.
  opts.settings = {
    ...(typeof opts.settings === "object" ? opts.settings : {}),
    autoCompactWindow: 80_000,
  } as any;

  // Limit max turns to prevent runaway agents
  if (config.maxTurns) {
    (opts as any).maxTurns = config.maxTurns;
  }

  return opts;
}

/**
 * Per-session state for tracking in-flight tool calls across stream events.
 * Accumulates input_json_delta chunks so the emitted tool_call has full args.
 */
interface ToolCallStreamState {
  /** content_block index → pending tool call info */
  pending: Map<number, { id: string; name: string; inputChunks: string[] }>;
  /** tool_use_id → tool name, for resolving tool_result blocks */
  idToName: Map<string, string>;
}

function createToolCallStreamState(): ToolCallStreamState {
  return { pending: new Map(), idToName: new Map() };
}

/**
 * Translate a raw SDK message into zero or more AgentStreamEvents.
 */
function translateMessage(
  message: any,
  turnId: string,
  state: ToolCallStreamState,
  stderrTail = "",
): AgentStreamEvent[] {
  const events: AgentStreamEvent[] = [];

  switch (message.type) {
    case "assistant": {
      // Extract text content only; tool_use blocks are emitted via stream_event
      // content_block_start → delta → stop to capture full args.
      const text = typeof message.message === "string"
        ? message.message
        : (message.message?.content ?? [])
            .filter((b: any) => b.type === "text" && b.text)
            .map((b: any) => b.text)
            .join("") ?? "";
      if (text) {
        events.push({ type: "timeline", item: { type: "assistant_message", text } });
      }
      break;
    }
    case "user": {
      const uContent = typeof message.message === "string"
        ? [{ type: "text", text: message.message }]
        : (message.message?.content ?? []);
      const uTextParts: string[] = [];
      for (const block of uContent) {
        if (block.type === "text" && block.text) {
          uTextParts.push(block.text);
        } else if (block.type === "tool_result") {
          // Tool call completion — resolve name from id→name map
          const toolName = state.idToName.get(block.tool_use_id) ?? "unknown";
          const resultContent = typeof block.content === "string"
            ? block.content
            : Array.isArray(block.content)
              ? block.content.map((c: any) => c.text ?? "").join("")
              : "";
          const item: TimelineItem = {
            type: "tool_call",
            name: toolName,
            status: block.is_error ? "failed" : "completed",
            result: resultContent ? resultContent.slice(0, 2000) : undefined,
            error: block.is_error ? resultContent : undefined,
          };
          events.push({ type: "timeline", item });
        }
      }
      const uText = uTextParts.join("");
      if (uText) {
        events.push({ type: "timeline", item: { type: "user_message", text: uText } });
      }
      break;
    }
    case "result": {
      if (message.subtype === "success") {
        events.push({ type: "turn_completed", turnId, usage: extractUsage(message) });
      } else {
        const error = message.error ?? message.message ?? "Unknown error";
        events.push({ type: "turn_failed", turnId, error: String(error), stderrTail });
      }
      break;
    }
    case "tool_use": {
      // Fallback: top-level tool_use message (not seen in current SDK, but kept for safety)
      const item: TimelineItem = {
        type: "tool_call",
        name: message.tool_name ?? message.name ?? "unknown",
        args: JSON.stringify(message.tool_input ?? message.input ?? {}),
        status: "running",
      };
      events.push({ type: "timeline", item });
      break;
    }
    case "tool_result": {
      // Fallback: top-level tool_result message (not seen in current SDK, but kept for safety)
      const item: TimelineItem = {
        type: "tool_call",
        name: message.tool_name ?? "unknown",
        status: message.is_error ? "failed" : "completed",
        result: message.content ? String(message.content).slice(0, 2000) : undefined,
        error: message.is_error ? String(message.content) : undefined,
      };
      events.push({ type: "timeline", item });
      break;
    }
    case "stream_event": {
      const eventType = message.event?.type;
      if (eventType === "content_block_delta") {
        const delta = message.event?.delta;
        if (delta?.type === "thinking_delta" && delta.thinking) {
          events.push({ type: "timeline", item: { type: "reasoning", text: delta.thinking } });
        } else if (delta?.type === "input_json_delta" && delta.partial_json) {
          // Accumulate tool call args
          const idx = message.event?.index ?? 0;
          const pending = state.pending.get(idx);
          if (pending) {
            pending.inputChunks.push(delta.partial_json);
          }
        }
      } else if (eventType === "content_block_start") {
        const block = message.event?.content_block;
        if (block?.type === "tool_use" && block.name) {
          const idx = message.event?.index ?? 0;
          const id = block.id ?? "";
          state.pending.set(idx, { id, name: block.name, inputChunks: [] });
          if (id) state.idToName.set(id, block.name);
        }
      } else if (eventType === "content_block_stop") {
        // Emit complete tool_call with fully assembled args
        const idx = message.event?.index ?? 0;
        const pending = state.pending.get(idx);
        if (pending) {
          let args = "{}";
          try {
            const assembled = pending.inputChunks.join("");
            if (assembled) {
              JSON.parse(assembled); // validate
              args = assembled;
            }
          } catch { /* keep default {} */ }
          const item: TimelineItem = {
            type: "tool_call",
            name: pending.name,
            args,
            status: "running",
          };
          events.push({ type: "timeline", item });
          state.pending.delete(idx);
        }
      }
      break;
    }
    case "system": {
      if (message.subtype === "init" && message.session_id) {
        // Capture session ID for resume
        events.push({ type: "session_started", sessionId: message.session_id, provider: "claude" });
      }
      break;
    }
  }

  return events;
}

function extractUsage(message: any): AgentUsage | undefined {
  const usage = message.usage ?? message.result?.usage;
  if (!usage) return undefined;
  return {
    inputTokens: usage.input_tokens,
    outputTokens: usage.output_tokens,
    totalCostUsd: usage.total_cost_usd,
    model: usage.model ?? usage.model_name ?? message.model ?? message.result?.model,
    provider: usage.provider ?? message.provider ?? "claude",
  };
}

const MAX_RECOVERY_ATTEMPTS = 2;

/**
 * Internal: build a Claude session handle with its query pump and follow-up
 * `sendQuery` closure attached. Shared by `createClaudeSession` (starts a turn
 * immediately) and `resumeClaudeSession` (leaves the handle dormant until the
 * first follow-up).
 */
function buildClaudeHandle(
  config: AgentSessionConfig,
  onEvent: (event: AgentStreamEvent) => void,
  opts: { startImmediately: boolean; seedEvents?: AgentStreamEvent[]; resumedSessionId?: string | null },
): ClaudeSessionHandle {
  const handle: ClaudeSessionHandle = {
    id: config.title ?? "claude-session",
    claudeSessionId: opts.resumedSessionId ?? null,
    query: null,
    input: null,
    closed: false,
    turnSeq: 0,
    usage: {},
    recoveryAttempts: 0,
    stderr: "",
  };

  // Accumulate all events across pump restarts for recovery context. For a
  // resumed session this is seeded with the persisted timeline so the first
  // continuation summary carries the prior conversation.
  const accumulatedEvents: AgentStreamEvent[] = opts.seedEvents ? [...opts.seedEvents] : [];
  // Original user prompt so recovery can re-inject it. On resume config.prompt
  // is empty, so fall back to the first user message in the persisted timeline.
  const originalPrompt = config.prompt || firstUserMessageText(accumulatedEvents);

  const startPump = (prompt: string) => {
    const input = createAsyncInput<any>();
    handle.input = input;

    // Push initial user message
    input.push(buildUserMessage(prompt));

    handle.stderr = "";
    const options = buildClaudeOptions(config, (data) => {
      handle.stderr += data;
      if (handle.stderr.length > 8000) {
        handle.stderr = handle.stderr.slice(-8000);
      }
    });
    const q = claudeQuery({ prompt: input.iterable, options });
    handle.query = q;

    const turnId = `turn-${++handle.turnSeq}`;
    onEvent({ type: "turn_started", turnId });

    // Per-pump state for accumulating streamed tool call args
    const streamState = createToolCallStreamState();

    // Run the pump in background
    (async () => {
      try {
        for await (const message of q) {
          if (handle.closed) break;

          // Capture session ID
          if (message.type === "system" && message.subtype === "init" && message.session_id) {
            handle.claudeSessionId = message.session_id;
          }

          const events = translateMessage(message, turnId, streamState, handle.stderr.slice(-2000));
          for (const event of events) {
            if (event.type === "turn_completed" && event.usage) {
              event.usage = {
                ...event.usage,
                model: event.usage.model ?? config.model,
                provider: event.usage.provider ?? "claude",
              };
            }
            accumulatedEvents.push(event);
            // Accumulate usage
            if (event.type === "turn_completed" && event.usage) {
              handle.usage = {
                inputTokens: (handle.usage.inputTokens ?? 0) + (event.usage.inputTokens ?? 0),
                outputTokens: (handle.usage.outputTokens ?? 0) + (event.usage.outputTokens ?? 0),
                totalCostUsd: (handle.usage.totalCostUsd ?? 0) + (event.usage.totalCostUsd ?? 0),
                model: event.usage.model ?? handle.usage.model,
                provider: event.usage.provider ?? handle.usage.provider,
              };
            }
            onEvent(event);
          }

          // Result means this turn is done
          if (message.type === "result") {
            // Check for tool_id mismatch error in failed results
            if (message.subtype !== "success") {
              const errMsg = (message as any).error ?? (message as any).message ?? "";
              if (isToolIdMismatchError(errMsg)) {
                throw new Error(String(errMsg));
              }
            }
            onEvent({ type: "status_changed", status: "idle" });
            break;
          }
        }
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);

        // Recovery: tool_use_id mismatch means context window corruption
        if (isToolIdMismatchError(err) && handle.recoveryAttempts < MAX_RECOVERY_ATTEMPTS) {
          handle.recoveryAttempts++;
          log(`Context corruption detected (attempt ${handle.recoveryAttempts}/${MAX_RECOVERY_ATTEMPTS}), recovering with fresh session...`);

          // Collect what we know from accumulated events + original prompt
          const allEvents = accumulatedEvents;
          const continuationPrompt = buildContinuationSummary(allEvents, originalPrompt);

          // Close the corrupted query
          try { await q.return?.(undefined); } catch { /* ignore */ }
          handle.query = null;
          handle.input = null;
          handle.claudeSessionId = null; // Force fresh session, no resume

          onEvent({
            type: "timeline",
            item: {
              type: "assistant_message",
              text: `[System: Context window recovered — resuming with accumulated context (recovery ${handle.recoveryAttempts}/${MAX_RECOVERY_ATTEMPTS})]`,
            },
          });

          // Restart with fresh session + continuation summary
          startPump(continuationPrompt);
          return;
        }

        log(`Query pump error: ${error}`);
        onEvent({
          type: "turn_failed",
          turnId,
          error,
          stderrTail: handle.stderr.slice(-2000),
        });
        onEvent({ type: "status_changed", status: "error" });
      }
    })();
  };

  // Attach follow-up method — pushes into existing input stream if pump is alive,
  // otherwise starts a fresh pump with continuation context. For a resumed
  // (dormant) handle the pump is never started below, so the first follow-up
  // takes the "start fresh with continuation summary" branch — exactly resume.
  (handle as any).sendQuery = (prompt: string) => {
    if (handle.closed) throw new Error("Session is closed");
    onEvent({ type: "status_changed", status: "running" });

    if (handle.input && handle.query) {
      // Pump still alive — push directly into input stream for multi-turn
      const turnId = `turn-${++handle.turnSeq}`;
      onEvent({ type: "turn_started", turnId });
      handle.input.push(buildUserMessage(prompt));
    } else {
      // Pump died or was cleaned up — start fresh with continuation summary
      const continuationPrompt = buildContinuationSummary(accumulatedEvents, originalPrompt);
      startPump(continuationPrompt + "\n\n## New User Message\n" + prompt);
    }
  };

  // A fresh session starts its first turn now; a resumed handle stays idle until
  // the first follow-up arrives via sendQuery.
  if (opts.startImmediately) {
    onEvent({ type: "status_changed", status: "running" });
    startPump(config.prompt);
  }

  return handle;
}

/**
 * Create a Claude agent session and start the query pump immediately.
 */
export function createClaudeSession(
  config: AgentSessionConfig,
  onEvent: (event: AgentStreamEvent) => void,
): ClaudeSessionHandle {
  return buildClaudeHandle(config, onEvent, { startImmediately: true });
}

/**
 * Rebuild a dormant, resumable Claude handle on sidecar restart.
 *
 * The handle carries the persisted timeline (`persistedEvents`) as recovery
 * context and stays idle (no pump) until the first follow-up, at which point
 * `sendQuery` starts a fresh pump seeded with a continuation summary — the same
 * path `createClaudeSession` uses after its pump dies. This matches the
 * provider's deliberate choice NOT to use the SDK `resume` option (see
 * `buildClaudeOptions`), avoiding orphaned-`tool_result` 400s. `claudeSessionId`
 * is carried for reference only; it is not fed to the SDK.
 */
export function resumeClaudeSession(
  config: AgentSessionConfig,
  persistedEvents: AgentStreamEvent[],
  onEvent: (event: AgentStreamEvent) => void,
  claudeSessionId: string | null,
): ClaudeSessionHandle {
  return buildClaudeHandle(config, onEvent, {
    startImmediately: false,
    seedEvents: persistedEvents,
    resumedSessionId: claudeSessionId,
  });
}

/**
 * Send a follow-up query to an existing session.
 */
export function sendClaudeQuery(handle: ClaudeSessionHandle, prompt: string, onEvent: (event: AgentStreamEvent) => void): void {
  (handle as any).sendQuery(prompt);
}

/**
 * Close a Claude session.
 */
export async function closeClaudeSession(handle: ClaudeSessionHandle): Promise<void> {
  handle.closed = true;
  try {
    await handle.query?.return?.(undefined);
  } catch {
    // ignore
  }
  handle.query = null;
  handle.input = null;
}
