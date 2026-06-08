/**
 * useGcloudConfigs - module-level cache for local gcloud config metadata.
 *
 * The gateway returns metadata only: config names, active flag, account,
 * project, and regions. No token material is returned or cached here.
 */

import { useCallback, useEffect, useState } from 'react';
import { fetchGcloudConfigs } from '@/lib/api';
import type { GcloudConfigSummary } from '@/types/api';

let cache: GcloudConfigSummary[] | null = null;
let availableCache = true;
let errorCache: string | null = null;
let inflight: Promise<GcloudConfigSummary[]> | null = null;
const subscribers = new Set<() => void>();

function notify() {
  subscribers.forEach((fn) => fn());
}

function loadOnce(): Promise<GcloudConfigSummary[]> {
  if (cache) return Promise.resolve(cache);
  if (inflight) return inflight;
  inflight = fetchGcloudConfigs()
    .then((response) => {
      availableCache = response.available;
      errorCache = response.error;
      cache = response.configs ?? [];
      inflight = null;
      notify();
      return cache;
    })
    .catch((err) => {
      availableCache = false;
      errorCache = err instanceof Error ? err.message : 'Failed to load gcloud configs';
      cache = [];
      inflight = null;
      notify();
      throw err;
    });
  return inflight;
}

export interface GcloudConfigsState {
  configs: GcloudConfigSummary[];
  available: boolean;
  error: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
}

export function useGcloudConfigs(): GcloudConfigsState {
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
    configs: cache ?? [],
    available: availableCache,
    error: errorCache,
    loading,
    refresh,
  };
}
