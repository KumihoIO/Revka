/**
 * Per-tab session id used by the editor to:
 *
 *   1. Stamp outgoing `POST/PUT /api/workflows` requests with a
 *      `X-Construct-Session` header so the gateway can echo it back
 *      on its `workflow.revision.published` SSE event.
 *   2. Suppress incoming events whose `originating_session` matches —
 *      the tab that triggered the save shouldn't react to its own broadcast.
 *
 * Stored in sessionStorage so it's stable across reloads of the same tab
 * but unique per-tab. Falls back to an in-memory id when sessionStorage
 * is unavailable (private browsing, etc.).
 */

const STORAGE_KEY = 'construct.editor.session_id';

let memoryFallback: string | null = null;

function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // Fallback: timestamp + random — unique enough for our use.
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function getEditorSessionId(): string {
  try {
    const existing = sessionStorage.getItem(STORAGE_KEY);
    if (existing) return existing;
    const fresh = generateId();
    sessionStorage.setItem(STORAGE_KEY, fresh);
    return fresh;
  } catch {
    if (!memoryFallback) memoryFallback = generateId();
    return memoryFallback;
  }
}

/** Header name to use on both client and server sides. */
export const EDITOR_SESSION_HEADER = 'X-Construct-Session';
