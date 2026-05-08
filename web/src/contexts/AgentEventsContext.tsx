/**
 * Global agent events context — captures operator channel events via SSE
 * regardless of which page is active.  Events persist in sessionStorage so
 * they survive in-app navigation (React Router unmount/remount cycles).
 *
 * Components like AgentChat read from this context instead of maintaining
 * their own ephemeral state.
 */

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import type { AgentChannelEvent } from '@/types/api';
import { SSEClient } from '@/lib/sse';

const SESSION_KEY = 'construct-agent-events-v1';
const MAX_EVENTS = 500; // Cap to avoid unbounded growth

interface AgentEventsContextValue {
  /** All agent channel events accumulated this session. */
  events: AgentChannelEvent[];
  /** Push a new event (used by WebSocket handlers that receive events directly). */
  pushEvent: (ev: AgentChannelEvent) => void;
  /** Clear all stored events (e.g. when starting a fresh orchestration). */
  clearEvents: () => void;
}

const AgentEventsContext = createContext<AgentEventsContextValue>({
  events: [],
  pushEvent: () => {},
  clearEvents: () => {},
});

export const useAgentEvents = () => useContext(AgentEventsContext);

function loadEvents(): AgentChannelEvent[] {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed as AgentChannelEvent[];
    }
  } catch {
    // Ignore corrupt storage
  }
  return [];
}

function saveEvents(events: AgentChannelEvent[]) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(events));
  } catch {
    // sessionStorage full — silently drop
  }
}

export function AgentEventsProvider({ children }: { children: ReactNode }) {
  const [events, setEvents] = useState<AgentChannelEvent[]>(loadEvents);
  const eventsRef = useRef(events);
  eventsRef.current = events;

  // Persist to sessionStorage on change
  useEffect(() => {
    saveEvents(events);
  }, [events]);

  const pushEvent = useCallback((ev: AgentChannelEvent) => {
    setEvents((prev) => {
      // Deduplicate by agentId + timestamp + type (SSE and WebSocket may both deliver the same event)
      const isDup = prev.length > 0 && prev.some(
        (existing) =>
          existing.agentId === ev.agentId &&
          existing.timestamp === ev.timestamp &&
          existing.type === ev.type,
      );
      if (isDup) return prev;

      const next = [...prev, ev];
      // Cap size
      return next.length > MAX_EVENTS ? next.slice(next.length - MAX_EVENTS) : next;
    });
  }, []);

  const clearEvents = useCallback(() => {
    setEvents([]);
    sessionStorage.removeItem(SESSION_KEY);
  }, []);

  // Global SSE listener — captures channel_event payloads even when not on the chat page
  useEffect(() => {
    const sse = new SSEClient();

    sse.onEvent = (raw) => {
      // The SSE stream carries many event types; we only care about channel_event
      if (raw.type !== 'channel_event') return;

      const payload = (raw as any).payload;
      if (!payload || !payload.type || !payload.agentId) return;

      const ev: AgentChannelEvent = {
        type: payload.type,
        agentId: payload.agentId,
        agentTitle: payload.agentTitle ?? '',
        content: payload.content ?? {},
        timestamp: payload.timestamp ?? new Date().toISOString(),
      };

      pushEvent(ev);
    };

    sse.connect();

    // Force-reconnect when the tab becomes visible after being hidden, or
    // when the browser regains network. Without this, a laptop coming
    // out of sleep can sit on a dead socket waiting for the next
    // exponential-backoff window (up to 30s) before retrying. The user
    // perceives this as an "intermittent dashboard drop".
    const onVisibility = () => {
      if (document.visibilityState === 'visible') sse.reconnectNow();
    };
    const onOnline = () => sse.reconnectNow();
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('online', onOnline);

    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('online', onOnline);
      sse.disconnect();
    };
  }, [pushEvent]);

  return (
    <AgentEventsContext.Provider value={{ events, pushEvent, clearEvents }}>
      {children}
    </AgentEventsContext.Provider>
  );
}

export default AgentEventsContext;
