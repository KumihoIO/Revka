// Shared chat types used by AgentChat (full-page) and ChatPanel (modal).

export interface ChatMessage {
  id: string;
  role: 'user' | 'agent' | 'operator';
  content: string;
  thinking?: string;
  markdown?: boolean;
  timestamp: Date;
  operatorPhase?: string;
  deliveryStatus?: 'queued' | 'sending' | 'sent';
  activityLog?: ActivityEvent[];
}

export interface ActivityEvent {
  id: string;
  kind: 'thinking' | 'tool_call' | 'tool_result' | 'operator';
  label: string;
  detail?: string;
  toolName?: string;
  status?: 'running' | 'done';
  timestamp: Date;
}

export type ActivityKind = ActivityEvent['kind'];

/** Discriminated target for workspace panes. */
export type TabTarget =
  | { type: 'chat'; sessionId: string }
  | { type: 'coordination' }
  | { type: 'agent'; agentId: string; agentTitle: string };

export interface ChatTab {
  id: string;
  /** @deprecated Use target.sessionId for chat tabs. Kept for v1 compat. */
  sessionId: string;
  title: string;
  createdAt: string;
  /** Pane target — defaults to { type: 'chat', sessionId } when absent (v1 compat). */
  target?: TabTarget;
}
