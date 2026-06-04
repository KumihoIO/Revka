import { createContext, useContext, useCallback, useRef, useMemo } from 'react';

/**
 * In-memory draft store that survives component unmounts but not page reloads.
 * Keyed by an arbitrary string (e.g. route path or conversation id).
 */

export interface DraftContextType {
  getDraft: (key: string) => string;
  setDraft: (key: string, value: string) => void;
  clearDraft: (key: string) => void;
}

export const DraftContext = createContext<DraftContextType>({
  getDraft: () => '',
  setDraft: () => {},
  clearDraft: () => {},
});

export function useDraftStore(): DraftContextType {
  const store = useRef<Map<string, string>>(new Map());

  const getDraft = useCallback((key: string): string => {
    return store.current.get(key) ?? '';
  }, []);

  const setDraft = useCallback((key: string, value: string): void => {
    store.current.set(key, value);
  }, []);

  const clearDraft = useCallback((key: string): void => {
    store.current.delete(key);
  }, []);

  return useMemo(() => ({ getDraft, setDraft, clearDraft }), [getDraft, setDraft, clearDraft]);
}

export function useDraft(key: string) {
  const { getDraft, setDraft, clearDraft } = useContext(DraftContext);
  const saveDraft = useCallback((value: string) => setDraft(key, value), [key, setDraft]);
  const clear = useCallback(() => clearDraft(key), [key, clearDraft]);
  return useMemo(() => ({
    draft: getDraft(key),
    saveDraft,
    clearDraft: clear,
  }), [getDraft, key, saveDraft, clear]);
}
