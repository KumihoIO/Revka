/**
 * SSE filter hook for revision-history events.
 *
 * The editor's existing `useWorkflowEvents` hook is narrowly scoped to
 * `workflow.revision.published` (the auto-apply path). The revision history
 * strip needs to react to the *same* event AND to `workflow.revision.republished`
 * so it can re-fetch its capsule list when an earlier revision is re-tagged
 * as published from any tab.
 *
 * Intentionally a separate, thin subscription rather than extending
 * `useWorkflowEvents`'s public shape — keeps the auto-apply path's contract
 * unchanged. We pay one extra SSEClient connection per editor mount, which
 * is negligible (the gateway multiplexes both ends).
 */

import { useEffect, useRef } from 'react';
import { SSEClient } from '@/lib/sse';
import type { SSEEvent } from '@/types/api';

interface RevisionPublishedEvent {
  type: 'workflow.revision.published';
  workflow_kref: string;
  revision_kref?: string;
}

interface RevisionRepublishedEvent {
  type: 'workflow.revision.republished';
  revision_kref: string;
}

type RevisionEvent = RevisionPublishedEvent | RevisionRepublishedEvent;

function matchesPublished(
  event: SSEEvent,
  kref: string,
): event is SSEEvent & RevisionPublishedEvent {
  if (event.type !== 'workflow.revision.published') return false;
  const wf = (event as { workflow_kref?: unknown }).workflow_kref;
  return typeof wf === 'string' && wf === kref;
}

function matchesRepublished(
  event: SSEEvent,
  kref: string,
): event is SSEEvent & RevisionRepublishedEvent {
  if (event.type !== 'workflow.revision.republished') return false;
  const rev = (event as { revision_kref?: unknown }).revision_kref;
  if (typeof rev !== 'string') return false;
  // A revision kref looks like `kref://Project/Workflows/foo?r=3`. Strip
  // the `?r=N` suffix and compare against the workflow kref. (Without the
  // suffix the prefix match would be exact; with it, we trim before
  // comparing.)
  const baseRev = rev.split('?', 1)[0];
  const baseKref = kref.split('?', 1)[0];
  return baseRev === baseKref;
}

export interface UseRevisionEventsOptions {
  /** kref of the workflow currently in the editor. Skip when null. */
  workflowKref: string | null | undefined;
  /** Called when the strip should re-fetch its revision list. */
  onChange: (event: RevisionEvent) => void;
}

/** Subscribe to revision lifecycle events for the given workflow kref. */
export function useRevisionEvents({
  workflowKref,
  onChange,
}: UseRevisionEventsOptions): void {
  // Stable ref so the effect doesn't re-subscribe per render.
  const callbackRef = useRef(onChange);
  callbackRef.current = onChange;

  useEffect(() => {
    if (!workflowKref) return undefined;

    const client = new SSEClient();

    client.onEvent = (event: SSEEvent) => {
      if (matchesPublished(event, workflowKref)) {
        callbackRef.current(event);
        return;
      }
      if (matchesRepublished(event, workflowKref)) {
        callbackRef.current(event);
      }
    };

    client.connect();

    return () => {
      client.disconnect();
    };
  }, [workflowKref]);
}
