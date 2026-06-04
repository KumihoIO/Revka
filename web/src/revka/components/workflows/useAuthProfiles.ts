/**
 * useAuthProfiles — module-level cache for the editor's auth-profile roster.
 *
 * Mirrors useAgentRoster: shared cache + inflight dedupe so multiple consumers
 * (the side-panel "Auth" picker, the per-step lock-icon tooltip, and any
 * future canvas surface) only hit `GET /api/auth/profiles` once per editor
 * session.
 *
 * The endpoint returns metadata only — no token bytes — so it's safe to keep
 * the result in module memory for the editor's lifetime.
 */

import { useEffect, useState, useCallback } from 'react';
import { fetchAuthProfiles } from '@/lib/api';
import type { AuthProfileSummary } from '@/types/api';

let cache: AuthProfileSummary[] | null = null;
let inflight: Promise<AuthProfileSummary[]> | null = null;
const subscribers = new Set<() => void>();

function notify() {
  subscribers.forEach((fn) => fn());
}

function loadOnce(): Promise<AuthProfileSummary[]> {
  if (cache) return Promise.resolve(cache);
  if (inflight) return inflight;
  inflight = fetchAuthProfiles()
    .then((profiles) => {
      cache = profiles;
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

export interface AuthProfilesState {
  profiles: AuthProfileSummary[];
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useAuthProfiles(): AuthProfilesState {
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
    profiles: cache ?? [],
    loading,
    refresh,
  };
}
