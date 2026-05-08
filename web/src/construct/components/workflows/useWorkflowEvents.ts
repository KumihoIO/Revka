/**
 * Editor-scoped SSE subscription.
 *
 * Subscribes to the gateway's `/api/events` stream (via the shared SSEClient,
 * which already handles Bearer auth + reconnect via fetch+ReadableStream),
 * filters for `workflow.revision.published` events that belong to the workflow
 * currently open in the editor, and ignores events originated by this very
 * tab (matched by `originating_session`).
 *
 * Intentionally a thin wrapper around SSEClient — we don't need the buffering
 * logic that the dashboard's `useSSE` provides, only a callback per matching
 * event.
 */

import { useEffect, useRef } from 'react';
import { SSEClient } from '@/lib/sse';
import type { SSEEvent } from '@/types/api';
import { getEditorSessionId } from './sessionId';

export interface WorkflowRevisionPublishedEvent {
  type: 'workflow.revision.published';
  workflow_kref: string;
  revision_kref: string;
  revision_number: number;
  name: string;
  published_at: string;
  /** Session id of the tab/Operator that triggered the save. May be null. */
  originating_session: string | null;
}

export interface UseWorkflowEventsOptions {
  /** kref of the workflow currently being edited (skip when null/undefined). */
  workflowKref: string | null | undefined;
  /** Called when a remote tab publishes a new revision for this workflow. */
  onRevisionPublished: (event: WorkflowRevisionPublishedEvent) => void;
}

function isRevisionPublishedEvent(
  event: SSEEvent,
): event is SSEEvent & WorkflowRevisionPublishedEvent {
  return (
    event.type === 'workflow.revision.published' &&
    typeof (event as { workflow_kref?: unknown }).workflow_kref === 'string' &&
    typeof (event as { revision_kref?: unknown }).revision_kref === 'string'
  );
}

export function useWorkflowEvents({
  workflowKref,
  onRevisionPublished,
}: UseWorkflowEventsOptions): void {
  // Keep callback in a ref so the effect doesn't re-subscribe on every render.
  const callbackRef = useRef(onRevisionPublished);
  callbackRef.current = onRevisionPublished;

  const krefRef = useRef(workflowKref);
  krefRef.current = workflowKref;

  useEffect(() => {
    // No workflow loaded yet — nothing to subscribe to.
    if (!workflowKref) return undefined;

    const sessionId = getEditorSessionId();
    const client = new SSEClient();

    client.onEvent = (event: SSEEvent) => {
      if (!isRevisionPublishedEvent(event)) return;
      if (event.workflow_kref !== krefRef.current) return;
      // Suppress self-originated events — the tab that saved already has
      // the new YAML in its in-memory state.
      if (event.originating_session && event.originating_session === sessionId) return;
      callbackRef.current(event);
    };

    client.connect();

    return () => {
      client.disconnect();
    };
  }, [workflowKref]);
}
