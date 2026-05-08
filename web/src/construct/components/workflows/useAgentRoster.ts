/**
 * useAgentRoster — module-level cache for the editor's agent roster.
 *
 * Multiple consumers (canvas-anchored AgentPicker, side-panel field button,
 * editor mount-time prefetch) all share one in-flight fetch and one cached
 * result for the editor session. Lets the picker open instantly after first
 * paint without re-hitting GET /api/agents on every node click.
 */

import { useEffect, useState, useCallback } from 'react';
import { fetchAgents } from '@/lib/api';
import type { AgentDefinition } from '@/types/api';

let cache: AgentDefinition[] | null = null;
let inflight: Promise<AgentDefinition[]> | null = null;
const subscribers = new Set<() => void>();

function notify() {
  subscribers.forEach((fn) => fn());
}

function loadOnce(): Promise<AgentDefinition[]> {
  if (cache) return Promise.resolve(cache);
  if (inflight) return inflight;
  inflight = fetchAgents(false, 1, 100)
    .then((res) => {
      cache = res.agents;
      inflight = null;
      notify();
      return cache;
    })
    .catch((err) => {
      inflight = null;
      cache = [];
      notify();
      throw err;
    });
  return inflight;
}

export interface AgentRoster {
  agents: AgentDefinition[];
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useAgentRoster(): AgentRoster {
  const [, setTick] = useState(0);
  const [loading, setLoading] = useState<boolean>(cache === null);

  useEffect(() => {
    const sub = () => setTick((n) => n + 1);
    subscribers.add(sub);
    if (cache === null) {
      setLoading(true);
      loadOnce()
        .catch(() => {})
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
    return () => {
      subscribers.delete(sub);
    };
  }, []);

  const refresh = useCallback(async () => {
    cache = null;
    inflight = null;
    setLoading(true);
    try {
      await loadOnce();
    } finally {
      setLoading(false);
    }
  }, []);

  return {
    agents: cache ?? [],
    loading,
    refresh,
  };
}
