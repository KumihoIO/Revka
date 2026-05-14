/**
 * Codex CLI provider — spawns codex as subprocess.
 *
 * Unlike Claude which has a proper SDK, Codex is driven via CLI subprocess.
 * This is a thin wrapper that captures stdout/stderr and emits timeline events.
 */

import { spawn, type ChildProcess } from "node:child_process";
import type { AgentSessionConfig, AgentStreamEvent, AgentUsage } from "../types.js";

const log = (msg: string) => process.stderr.write(`[session-mgr:codex] ${msg}\n`);

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

function asNumber(value: unknown): number | undefined {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return undefined;
}

function asString(value: unknown): string | undefined {
  if (typeof value === "string" && value.trim()) return value.trim();
  return undefined;
}

function usageFromCandidate(candidate: any): AgentUsage | undefined {
  if (!candidate || typeof candidate !== "object") return undefined;
  const inputTokens = asNumber(
    candidate.inputTokens ??
      candidate.input_tokens ??
      candidate.prompt_tokens ??
      candidate.promptTokens,
  );
  const outputTokens = asNumber(
    candidate.outputTokens ??
      candidate.output_tokens ??
      candidate.completion_tokens ??
      candidate.completionTokens,
  );
  const totalCostUsd = asNumber(
    candidate.totalCostUsd ??
      candidate.total_cost_usd ??
      candidate.costUsd ??
      candidate.cost_usd,
  );
  if (inputTokens === undefined && outputTokens === undefined && totalCostUsd === undefined) {
    return undefined;
  }
  const model = asString(
    candidate.model ??
      candidate.model_name ??
      candidate.modelName ??
      candidate.info?.model,
  );
  const provider = asString(
    candidate.provider ??
      candidate.provider_name ??
      candidate.providerName ??
      candidate.info?.provider,
  );
  return { inputTokens, outputTokens, totalCostUsd, model, provider };
}

function withEventMetadata(usage: AgentUsage, event: any): AgentUsage {
  return {
    ...usage,
    model:
      usage.model ??
      asString(event?.model ?? event?.model_name ?? event?.modelName) ??
      asString(event?.response?.model ?? event?.data?.model ?? event?.info?.model),
    provider:
      usage.provider ??
      asString(event?.provider ?? event?.provider_name ?? event?.providerName) ??
      asString(event?.response?.provider ?? event?.data?.provider ?? event?.info?.provider),
  };
}

function extractUsage(event: any): AgentUsage | undefined {
  const candidates = [
    event?.usage,
    event?.token_usage,
    event?.tokenUsage,
    event?.total_token_usage,
    event?.totalTokenUsage,
    event?.info?.usage,
    event?.info?.token_usage,
    event?.info?.total_token_usage,
    event?.event?.usage,
    event?.response?.usage,
    event?.data?.usage,
  ];
  for (const candidate of candidates) {
    const usage = usageFromCandidate(candidate);
    if (usage) return withEventMetadata(usage, event);
  }
  const usage = usageFromCandidate(event);
  return usage ? withEventMetadata(usage, event) : undefined;
}

function mergeUsage(current: AgentUsage, next: AgentUsage): AgentUsage {
  return {
    inputTokens: next.inputTokens ?? current.inputTokens,
    outputTokens: next.outputTokens ?? current.outputTokens,
    totalCostUsd: next.totalCostUsd ?? current.totalCostUsd,
    model: next.model ?? current.model,
    provider: next.provider ?? current.provider,
  };
}

function extractTimelineText(event: any): string {
  const type = String(event?.type ?? event?.event?.type ?? "");
  const candidates = [
    event?.message,
    event?.text,
    event?.delta,
    event?.content,
    event?.item?.text,
    event?.event?.message,
    event?.event?.text,
    event?.data?.message,
    event?.data?.text,
  ];
  const text = candidates.find((value) => typeof value === "string" && value.length > 0);
  if (!text) return "";
  if (type && !/message|delta|output|response/i.test(type)) return "";
  return text;
}

function hasUsage(usage: AgentUsage): boolean {
  return usage.inputTokens !== undefined || usage.outputTokens !== undefined || usage.totalCostUsd !== undefined;
}

/**
 * Create a Codex agent session via subprocess.
 */
export function createCodexSession(
  config: AgentSessionConfig,
  onEvent: (event: AgentStreamEvent) => void,
): CodexSessionHandle {
  const handle: CodexSessionHandle = {
    id: config.title ?? "codex-session",
    process: null,
    closed: false,
    turnSeq: 0,
    stdout: "",
    stderr: "",
    usage: {},
    jsonBuffer: "",
  };

  const runPrompt = (prompt: string) => {
    const turnId = `turn-${++handle.turnSeq}`;
    onEvent({ type: "turn_started", turnId });
    onEvent({ type: "status_changed", status: "running" });

    handle.stdout = "";
    handle.stderr = "";

    handle.usage = {
      model: config.model,
      provider: "codex",
    };
    handle.jsonBuffer = "";

    const args = ["exec", "--json", "--full-auto", "--skip-git-repo-check"];
    if (config.model) {
      args.push("--model", config.model);
    }
    args.push(prompt);
    log(`Spawning codex: ${args.slice(0, 4).join(" ")}... (${prompt.length} chars)`);

    const proc = spawn("codex", args, {
      cwd: config.cwd,
      stdio: ["ignore", "pipe", "pipe"],
      env: { ...process.env, ...(config.env ?? {}) },
    });
    handle.process = proc;

    proc.stdout?.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf-8");
      handle.stdout += text;
      handle.jsonBuffer += text;
      const lines = handle.jsonBuffer.split(/\r?\n/);
      handle.jsonBuffer = lines.pop() ?? "";
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) continue;
        try {
          const event = JSON.parse(trimmed);
          const usage = extractUsage(event);
          if (usage) {
            handle.usage = mergeUsage(handle.usage, {
              ...usage,
              model: usage.model ?? config.model,
              provider: usage.provider ?? "codex",
            });
          }
          const timelineText = extractTimelineText(event);
          if (timelineText) {
            onEvent({ type: "timeline", item: { type: "assistant_message", text: timelineText } });
          }
        } catch {
          onEvent({ type: "timeline", item: { type: "assistant_message", text: line } });
        }
      }
    });

    proc.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString("utf-8");
      handle.stderr += text;
    });

    proc.on("close", (code) => {
      handle.process = null;
      if (handle.closed) return;

      if (code === 0) {
        const tail = handle.jsonBuffer.trim();
        if (tail) {
          try {
            const event = JSON.parse(tail);
            const usage = extractUsage(event);
            if (usage) {
              handle.usage = mergeUsage(handle.usage, {
                ...usage,
                model: usage.model ?? config.model,
                provider: usage.provider ?? "codex",
              });
            }
            const timelineText = extractTimelineText(event);
            if (timelineText) {
              onEvent({ type: "timeline", item: { type: "assistant_message", text: timelineText } });
            }
          } catch {
            onEvent({ type: "timeline", item: { type: "assistant_message", text: tail } });
          }
        }
        handle.jsonBuffer = "";
        onEvent({
          type: "turn_completed",
          turnId,
          usage: hasUsage(handle.usage) ? handle.usage : undefined,
        });
        onEvent({ type: "status_changed", status: "idle" });
      } else {
        const error = handle.stderr.slice(-500) || `Process exited with code ${code}`;
        onEvent({ type: "turn_failed", turnId, error });
        onEvent({ type: "status_changed", status: "error" });
      }
    });

    proc.on("error", (err) => {
      handle.process = null;
      if (handle.closed) return;
      onEvent({ type: "turn_failed", turnId, error: err.message });
      onEvent({ type: "status_changed", status: "error" });
    });
  };

  // Start the first turn
  runPrompt(config.prompt);

  // Attach follow-up method
  (handle as any).sendQuery = (prompt: string) => {
    if (handle.closed) throw new Error("Session is closed");
    runPrompt(prompt);
  };

  return handle;
}

/**
 * Send a follow-up query to an existing Codex session.
 */
export function sendCodexQuery(handle: CodexSessionHandle, prompt: string, onEvent: (event: AgentStreamEvent) => void): void {
  (handle as any).sendQuery(prompt);
}

/**
 * Close a Codex session.
 */
export async function closeCodexSession(handle: CodexSessionHandle): Promise<void> {
  handle.closed = true;
  if (handle.process) {
    handle.process.kill("SIGTERM");
    handle.process = null;
  }
}
