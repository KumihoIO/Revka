/**
 * Shared types for the Revka Session Manager sidecar.
 * Modeled after Paseo's agent-sdk-types but simplified for Revka.
 */

// -- MCP server configuration -----------------------------------------------

export interface McpStdioServerConfig {
  type: "stdio";
  command: string;
  args?: string[];
  env?: Record<string, string>;
}

export interface McpHttpServerConfig {
  type: "http";
  url: string;
  headers?: Record<string, string>;
}

export type McpServerConfig = McpStdioServerConfig | McpHttpServerConfig;

// -- Agent session types -----------------------------------------------------

export type AgentProvider = "claude" | "codex";

export type AgentStatus = "initializing" | "running" | "idle" | "error" | "closed";

export interface AgentUsage {
  inputTokens?: number;
  outputTokens?: number;
  totalCostUsd?: number;
  model?: string;
  provider?: string;
}

export interface AgentSessionConfig {
  cwd: string;
  agentType: AgentProvider;
  prompt: string;
  systemPrompt?: string;
  model?: string;
  maxTurns?: number;
  maxBudgetUsd?: number;
  mcpServers?: Record<string, McpServerConfig>;
  parentId?: string;
  env?: Record<string, string>;
  title?: string;
}

export interface AgentSessionInfo {
  id: string;
  provider: AgentProvider;
  status: AgentStatus;
  title: string;
  cwd: string;
  createdAt: string;
  parentId?: string;
  claudeSessionId?: string;
  usage?: AgentUsage;
}

// -- Stream events -----------------------------------------------------------

export type AgentStreamEvent =
  | { type: "turn_started"; turnId: string }
  | { type: "turn_completed"; turnId: string; usage?: AgentUsage }
  | { type: "turn_failed"; turnId: string; error: string; exitCode?: number | null; stderrTail?: string }
  | { type: "timeline"; item: TimelineItem }
  | { type: "status_changed"; status: AgentStatus }
  | { type: "session_started"; sessionId: string; provider: AgentProvider }
  | { type: "session_closed"; sessionId: string };

export type TimelineItem =
  | { type: "user_message"; text: string }
  | { type: "assistant_message"; text: string }
  | { type: "reasoning"; text: string }
  | { type: "tool_call"; name: string; args?: string; status: "running" | "completed" | "failed"; result?: string; error?: string }
  | { type: "error"; message: string };

// -- Chat rooms (inter-agent communication) -----------------------------------

export interface ChatRoom {
  id: string;
  name: string;
  purpose: string;
  messages: ChatMessage[];
  createdAt: string;
}

export interface ChatMessage {
  id: string;
  senderId: string;
  senderName: string;
  content: string;
  mentions: string[];
  replyTo?: string;
  timestamp: string;
}

export interface ChatRoomInfo {
  id: string;
  name: string;
  purpose: string;
  messageCount: number;
  createdAt: string;
}

// -- Event envelope (broadcast to operator/gateway) -------------------------

export interface AgentEvent {
  agentId: string;
  event: AgentStreamEvent;
  seq: number;
  timestamp: string;
}
