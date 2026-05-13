import { useState, useEffect, useRef, useCallback, useContext } from 'react';
import type { WsMessage, AgentChannelEvent } from '@/types/api';
import { WebSocketClient } from '@/lib/ws';
import { generateUUID } from '@/lib/uuid';
import { DraftContext } from '@/construct/hooks/useDraft';
import { t } from '@/lib/i18n';
import { getSessionMessages, uploadAttachment, type AttachmentUploadResponse } from '@/lib/api';
import {
  loadChatHistory,
  mapServerMessagesToPersisted,
  persistedToUiMessages,
  saveChatHistory,
  uiMessagesToPersisted,
} from '@/lib/chatHistoryStorage';
import type { ActivityEvent, ChatMessage } from '@/components/chat/types';
import { operatorPhaseIcon, isTransientPhase, friendlyToolLabel } from '@/components/chat/chat-utils';
import { copyToClipboard } from '@/construct/lib/clipboard';

/** Tool-result event surfaced to consumers (e.g. ArchitectChatSurface
 *  needs to react to `propose_workflow_yaml` results). The hook still
 *  threads the same event into the activities feed for rendering — this
 *  callback is purely a side-channel for consumers that need the raw
 *  output. Fired once per `tool_result` WS message. */
export interface ToolResultEvent {
  /** Stable id minted at receive time, useful for de-dup in effects. */
  id: string;
  /** MCP tool name. */
  name: string;
  /** Raw `output` field from the WS message — typically a JSON string
   *  but may be plain text. Consumers JSON.parse if they expect it. */
  output: string;
}

interface UseAgentChatSessionOptions {
  sessionId: string;
  sessionName?: string;
  draftKey: string;
  pageContext?: string;
  onUserMessage?: (content: string) => void;
  /** Fired once per `tool_result` WebSocket message. Lives alongside the
   *  existing activity-feed integration; consumers that don't need raw
   *  tool results can ignore it. */
  onToolResult?: (event: ToolResultEvent) => void;
}

/** Server-issued attachment metadata plus an optional data-URL thumbnail
 *  used only by the chip strip. The data URL is generated client-side at
 *  add time (FileReader.readAsDataURL) so we never re-fetch the file. */
export interface StagedAttachment extends AttachmentUploadResponse {
  /** Client-only data URL preview for image attachments. Undefined for docs. */
  previewUrl?: string;
}

interface QueuedTurn {
  id: string;
  content: string;
  attachments: StagedAttachment[];
}

export function useAgentChatSession({
  sessionId,
  sessionName,
  draftKey,
  pageContext,
  onUserMessage,
  onToolResult,
}: UseAgentChatSessionOptions) {
  const { getDraft, setDraft, clearDraft: clearDraftStore } = useContext(DraftContext);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [historyReady, setHistoryReady] = useState(false);
  const [input, setInput] = useState(() => getDraft(draftKey));
  const [typing, setTyping] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activities, setActivities] = useState<ActivityEvent[]>([]);
  const [streamingContent, setStreamingContent] = useState('');
  const [streamingThinking, setStreamingThinking] = useState('');
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [agentEvents, setAgentEvents] = useState<AgentChannelEvent[]>([]);
  const [queuedTurns, setQueuedTurns] = useState<QueuedTurn[]>([]);
  const [stopping, setStopping] = useState(false);
  // Staged attachments waiting to ship with the next user message. Each
  // entry has the server-issued metadata plus an optional client-only
  // `previewUrl` (data URL for image thumbnails) so the chip strip can
  // render without re-downloading. Cleared after a successful send.
  const [attachments, setAttachments] = useState<StagedAttachment[]>([]);
  // Number of in-flight `uploadAttachment` requests. The send button
  // stays disabled while > 0 so a user can't fire a turn that drops
  // half the files they tried to attach.
  const [uploadingCount, setUploadingCount] = useState(0);

  const wsRef = useRef<WebSocketClient | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const pendingContentRef = useRef('');
  const pendingThinkingRef = useRef('');
  const capturedThinkingRef = useRef('');
  const activitiesRef = useRef<ActivityEvent[]>([]);
  const typingRef = useRef(false);
  const sendingQueuedRef = useRef(false);
  const inFlightTurnsRef = useRef<Map<string, QueuedTurn>>(new Map());
  const onUserMessageRef = useRef(onUserMessage);
  onUserMessageRef.current = onUserMessage;
  const onToolResultRef = useRef(onToolResult);
  onToolResultRef.current = onToolResult;
  const draftKeyRef = useRef(draftKey);
  draftKeyRef.current = draftKey;

  useEffect(() => {
    typingRef.current = typing;
  }, [typing]);

  const markSendingTurnsSent = useCallback(() => {
    inFlightTurnsRef.current.clear();
    setMessages((prev) => prev.map((message) =>
      message.deliveryStatus === 'sending' ? { ...message, deliveryStatus: 'sent' } : message,
    ));
  }, []);

  const requeueLatestSendingTurn = useCallback(() => {
    const inFlightTurns = Array.from(inFlightTurnsRef.current.values());
    const latest = inFlightTurns[inFlightTurns.length - 1];
    if (!latest) return false;
    inFlightTurnsRef.current.delete(latest.id);
    setQueuedTurns((prev) =>
      prev.some((turn) => turn.id === latest.id) ? prev : [latest, ...prev],
    );
    setMessages((prev) => prev.map((message) =>
      message.id === latest.id ? { ...message, deliveryStatus: 'queued' } : message,
    ));
    return true;
  }, []);

  // Reset input when session changes (one-way: store → state)
  useEffect(() => {
    setInput(getDraft(draftKey));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Persist input to draft store (one-way: state → store, no re-render)
  useEffect(() => {
    setDraft(draftKeyRef.current, input);
  }, [input, setDraft]);

  useEffect(() => {
    let cancelled = false;
    setMessages([]);
    setActivities([]);
    setAgentEvents([]);
    setQueuedTurns([]);
    sendingQueuedRef.current = false;
    inFlightTurnsRef.current.clear();
    setStopping(false);
    activitiesRef.current = [];
    setStreamingContent('');
    setStreamingThinking('');
    setTyping(false);
    setError(null);
    setHistoryReady(false);

    (async () => {
      try {
        const res = await getSessionMessages(sessionId);
        if (cancelled) return;
        if (res.session_persistence && res.messages.length > 0) {
          setMessages(persistedToUiMessages(mapServerMessagesToPersisted(res.messages)));
        } else if (!res.session_persistence) {
          const ls = loadChatHistory(sessionId);
          setMessages(ls.length ? persistedToUiMessages(ls) : []);
        }
      } catch {
        if (!cancelled) {
          const ls = loadChatHistory(sessionId);
          setMessages(ls.length ? persistedToUiMessages(ls) : []);
        }
      } finally {
        if (!cancelled) setHistoryReady(true);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    if (!historyReady) return;
    saveChatHistory(sessionId, uiMessagesToPersisted(
      messages.filter((m): m is ChatMessage & { role: 'user' | 'agent' } => m.role !== 'operator'),
    ));
  }, [historyReady, messages, sessionId]);

  useEffect(() => {
    let cancelled = false;
    let ws: WebSocketClient | null = null;

    const connectTimer = setTimeout(() => {
      if (cancelled) return;
      ws = new WebSocketClient({ sessionId, name: sessionName });

      ws.onOpen = () => {
        if (cancelled) return;
        setConnected(true);
        setError(null);
      };

      // Reset all in-flight turn state when the socket drops. Without this,
      // a mid-turn disconnect strands the UI: the "Operator is responding…"
      // pill stays visible, the send button stays disabled, and any partial
      // streaming/activity state persists until the user reloads or runs
      // `/clear`. Mirror what `done`/`error` handlers reset.
      const resetInFlightState = () => {
        setTyping(false);
        inFlightTurnsRef.current.clear();
        pendingContentRef.current = '';
        pendingThinkingRef.current = '';
        capturedThinkingRef.current = '';
        setStreamingContent('');
        setStreamingThinking('');
        activitiesRef.current = [];
        setActivities([]);
        setStopping(false);
      };

      ws.onClose = (ev: CloseEvent) => {
        if (cancelled) return;
        setConnected(false);
        resetInFlightState();
        if (ev.code !== 1000 && ev.code !== 1001) {
          const reason = ev.code === 1006
            ? 'Connection interrupted; reconnecting...'
            : `Connection closed unexpectedly (code: ${ev.code}). Reconnecting...`;
          setError(reason);
        }
      };

      ws.onError = () => {
        if (cancelled) return;
        resetInFlightState();
        setError(t('agent.connection_error'));
      };

      ws.onMessage = (msg: WsMessage) => {
        if (cancelled) return;
        switch (msg.type) {
          case 'session_start':
          case 'connected':
            break;

          case 'thinking': {
            markSendingTurnsSent();
            setTyping(true);
            pendingThinkingRef.current += msg.content ?? '';
            setStreamingThinking(pendingThinkingRef.current);
            const previous = activitiesRef.current;
            const last = previous[previous.length - 1];
            const nextActivities = last?.kind === 'thinking'
              ? [
                  ...previous.slice(0, -1),
                  { ...last, label: 'Reasoning...', detail: (last.detail ?? '') + (msg.content ?? '') },
                ]
              : [
                  ...previous,
                  { id: generateUUID(), kind: 'thinking' as const, label: 'Reasoning...', timestamp: new Date() },
                ];
            activitiesRef.current = nextActivities;
            setActivities(nextActivities);
            break;
          }

          case 'chunk':
            markSendingTurnsSent();
            setTyping(true);
            pendingContentRef.current += msg.content ?? '';
            setStreamingContent(pendingContentRef.current);
            break;

          case 'chunk_reset':
            capturedThinkingRef.current = pendingThinkingRef.current;
            pendingContentRef.current = '';
            pendingThinkingRef.current = '';
            setStreamingContent('');
            setStreamingThinking('');
            break;

          case 'message':
          case 'done': {
            const content = msg.full_response ?? msg.content ?? pendingContentRef.current;
            const thinking = capturedThinkingRef.current || pendingThinkingRef.current || undefined;
            const persistedActivities = activitiesRef.current.filter((activity) =>
              activity.kind !== 'thinking' || !!activity.detail,
            );

            activitiesRef.current = [];
            setActivities([]);

            if (content) {
              setMessages((prev) => [
                ...prev,
                {
                  id: generateUUID(),
                  role: 'agent',
                  content,
                  thinking,
                  markdown: true,
                  timestamp: new Date(),
                  activityLog: persistedActivities.length > 0 ? persistedActivities : undefined,
                },
              ]);
            } else if (persistedActivities.length > 0) {
              setMessages((prev) => [
                ...prev,
                {
                  id: generateUUID(),
                  role: 'operator',
                  content: persistedActivities.map((activity) => activity.label).join('\n'),
                  operatorPhase: 'completed',
                  timestamp: new Date(),
                  activityLog: persistedActivities,
                },
              ]);
            }

            pendingContentRef.current = '';
            pendingThinkingRef.current = '';
            capturedThinkingRef.current = '';
            setStreamingContent('');
            setStreamingThinking('');
            markSendingTurnsSent();
            setTyping(false);
            setStopping(false);
            break;
          }

          case 'tool_call': {
            markSendingTurnsSent();
            setTyping(true);
            const toolName = msg.name ?? 'tool';
            const nextActivities = [
              ...activitiesRef.current,
              {
                id: generateUUID(),
                kind: 'tool_call' as const,
                label: friendlyToolLabel(toolName),
                detail: msg.args ? (typeof msg.args === 'string' ? msg.args : JSON.stringify(msg.args, null, 2)) : undefined,
                toolName,
                status: 'running' as const,
                timestamp: new Date(),
              },
            ];
            activitiesRef.current = nextActivities;
            setActivities(nextActivities);
            break;
          }

          case 'tool_result': {
            markSendingTurnsSent();
            const toolName = msg.name ?? 'tool';
            const output = msg.output && msg.output.length > 500 ? `${msg.output.slice(0, 500)}...` : msg.output;
            const previous = activitiesRef.current;
            const pendingIndex = [...previous]
              .reverse()
              .findIndex((activity) =>
                activity.kind === 'tool_call'
                && activity.toolName === toolName
                && activity.status !== 'done',
              );
            const actualIndex = pendingIndex >= 0 ? previous.length - 1 - pendingIndex : -1;
            const nextActivities = actualIndex >= 0
              ? previous.map((activity, index) => {
                  if (index !== actualIndex) return activity;
                  const input = activity.detail?.trim();
                  const detail = [
                    input ? `Input\n${input}` : '',
                    output ? `Output\n${output}` : '',
                  ].filter(Boolean).join('\n\n');
                  return {
                    ...activity,
                    kind: 'tool_result' as const,
                    label: `${friendlyToolLabel(toolName)} - done`,
                    detail: detail || undefined,
                    status: 'done' as const,
                    timestamp: new Date(),
                  };
                })
              : [
                  ...previous,
                  {
                    id: generateUUID(),
                    kind: 'tool_result' as const,
                    label: `${friendlyToolLabel(toolName)} - done`,
                    detail: output,
                    toolName,
                    status: 'done' as const,
                    timestamp: new Date(),
                  },
                ];
            activitiesRef.current = nextActivities;
            setActivities(nextActivities);
            // Side-channel for consumers that need the raw tool output
            // (e.g. Architect's propose_workflow_yaml result → editor).
            if (onToolResultRef.current && msg.name) {
              onToolResultRef.current({
                id: generateUUID(),
                name: msg.name,
                output: msg.output ?? '',
              });
            }
            break;
          }

          case 'stopped': {
            const persistedActivities = activitiesRef.current;
            activitiesRef.current = [];
            setActivities([]);
            pendingContentRef.current = '';
            pendingThinkingRef.current = '';
            capturedThinkingRef.current = '';
            setStreamingContent('');
            setStreamingThinking('');
            markSendingTurnsSent();
            setTyping(false);
            setStopping(false);
            setMessages((prev) => [
              ...prev,
              {
                id: generateUUID(),
                role: 'operator',
                content: msg.message ?? 'Stopped current Operator turn.',
                operatorPhase: 'stopped',
                timestamp: new Date(),
                activityLog: persistedActivities.length > 0 ? persistedActivities : undefined,
              },
            ]);
            break;
          }

          case 'operator_status': {
            const phase = msg.phase ?? 'working';
            const detail = msg.detail ?? '';
            if (phase === 'queued') {
              requeueLatestSendingTurn();
            } else {
              markSendingTurnsSent();
            }
            setTyping(true);
            const activity = {
                id: generateUUID(),
                kind: 'operator' as const,
                label: `${operatorPhaseIcon(phase)} ${detail}`,
                detail: detail || undefined,
                timestamp: new Date(),
              };
            const currentActivities = activitiesRef.current;
            const lastActivity = currentActivities[currentActivities.length - 1];
            const nextActivities =
              isTransientPhase(phase) && lastActivity?.kind === 'operator'
                ? [...currentActivities.slice(0, -1), { ...activity, id: lastActivity.id }]
                : [...currentActivities, activity];
            activitiesRef.current = nextActivities;
            setActivities(nextActivities);

            if (!isTransientPhase(phase)) {
              setMessages((prev) => [
                ...prev,
                {
                  id: generateUUID(),
                  role: 'operator',
                  content: `${operatorPhaseIcon(phase)} ${detail}`,
                  operatorPhase: phase,
                  timestamp: new Date(),
                },
              ]);
            }
            break;
          }

          case 'agent_event': {
            const ev = msg.event as AgentChannelEvent | undefined;
            console.log('[construct] agent_event received:', ev?.type, ev?.agentTitle, ev);
            if (ev) {
              setAgentEvents((prev) => [...prev, ev]);
            }
            break;
          }

          case 'error':
            setMessages((prev) => [
              ...prev,
              {
                id: generateUUID(),
                role: 'agent',
                content: `${t('agent.error_prefix')} ${msg.message ?? t('agent.unknown_error')}`,
                timestamp: new Date(),
              },
            ]);
            if (msg.code === 'AGENT_INIT_FAILED' || msg.code === 'AUTH_ERROR' || msg.code === 'PROVIDER_ERROR') {
              setError(`Configuration error: ${msg.message}. Please check your provider settings (API key, model, etc.).`);
            } else if (msg.code === 'INVALID_JSON' || msg.code === 'UNKNOWN_MESSAGE_TYPE' || msg.code === 'EMPTY_CONTENT') {
              setError(`Message error: ${msg.message}`);
            }
            setTyping(false);
            pendingContentRef.current = '';
            pendingThinkingRef.current = '';
            capturedThinkingRef.current = '';
            setStreamingContent('');
            setStreamingThinking('');
            activitiesRef.current = [];
            setActivities([]);
            markSendingTurnsSent();
            setStopping(false);
            break;
        }
      };

      ws.connect();
      wsRef.current = ws;
    }, 50);

    return () => {
      cancelled = true;
      clearTimeout(connectTimer);
      if (ws) ws.disconnect();
      wsRef.current = null;
    };
    // pageContext intentionally NOT in the dependency array. The context is
    // sent per-message via wsRef.current.sendMessage(text, pageContext) using
    // handleSend's closure, so the WS itself only needs to reconnect when the
    // session changes. Re-connecting on every route change drops the in-flight
    // Operator request and clears the activity feed mid-tool-call.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markSendingTurnsSent, requeueLatestSendingTurn, sessionId, sessionName]);

  const sendTurn = useCallback((turn: QueuedTurn, fromQueue: boolean): boolean => {
    if (!wsRef.current?.connected) return false;
    try {
      wsRef.current.sendMessage(turn.content, pageContext, turn.attachments.map((a) => a.file_id));
      inFlightTurnsRef.current.set(turn.id, turn);
      onUserMessageRef.current?.(turn.content);
      if (fromQueue) {
        setMessages((prev) => prev.map((message) =>
          message.id === turn.id ? { ...message, deliveryStatus: 'sending' } : message,
        ));
      }
      setTyping(true);
      pendingContentRef.current = '';
      pendingThinkingRef.current = '';
      capturedThinkingRef.current = '';
      activitiesRef.current = [];
      setActivities([]);
    } catch {
      setError(t('agent.send_error'));
      return false;
    }
    return true;
  }, [pageContext]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    // A turn can be (a) text only, (b) attachments only with no text, or
    // (c) text + attachments. Reject empty-empty.
    if ((!trimmed && attachments.length === 0) || !wsRef.current?.connected) return false;
    if (uploadingCount > 0) return false; // wait for in-flight uploads

    // Render the user bubble with attachment chips inlined into the
    // message content so they appear in the scrollback. Plain text
    // (`content`) keeps the user's actual prompt; the trailing
    // `[Attached: name (size)]` lines are cosmetic so the user can
    // see what they shared without expanding the message bubble. The
    // server-side resolver handles real inlining for the LLM.
    const cosmeticAttach =
      attachments.length > 0
        ? '\n' + attachments.map((a) => `[Attached: ${a.filename} (${a.size}b)]`).join('\n')
        : '';
    const userContent = trimmed + cosmeticAttach;
    const turn: QueuedTurn = {
      id: generateUUID(),
      content: trimmed,
      attachments,
    };

    if (typingRef.current) {
      setQueuedTurns((prev) => [...prev, turn]);
      setMessages((prev) => [
        ...prev,
        {
          id: turn.id,
          role: 'user',
          content: userContent,
          deliveryStatus: 'queued',
          timestamp: new Date(),
        },
      ]);
    } else {
      setMessages((prev) => [
        ...prev,
        {
          id: turn.id,
          role: 'user',
          content: userContent,
          deliveryStatus: 'sending',
          timestamp: new Date(),
        },
      ]);
      if (!sendTurn(turn, false)) return false;
    }

    setInput('');
    setAttachments([]);
    clearDraftStore(draftKeyRef.current);
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      inputRef.current.focus();
    }
    return true;
  }, [attachments, clearDraftStore, input, sendTurn, uploadingCount]);

  useEffect(() => {
    if (typing || !connected || queuedTurns.length === 0 || sendingQueuedRef.current) return;
    const next = queuedTurns[0];
    if (!next) return;
    const rest = queuedTurns.slice(1);
    sendingQueuedRef.current = true;
    setQueuedTurns(rest);
    if (!sendTurn(next, true)) {
      setQueuedTurns((prev) => [next, ...prev]);
    }
    queueMicrotask(() => {
      sendingQueuedRef.current = false;
    });
  }, [connected, queuedTurns, sendTurn, typing]);

  const stopCurrentTurn = useCallback(() => {
    if (!typingRef.current || !wsRef.current?.connected || stopping) return false;
    try {
      wsRef.current.sendStop();
      setStopping(true);
      return true;
    } catch {
      setError(t('agent.send_error'));
      return false;
    }
  }, [stopping]);

  /** Send an arbitrary text turn without going through the textarea. Used
   *  by slash commands like `/architect` whose handler synthesizes a
   *  user-visible prompt rather than echoing the literal command. The
   *  message bubble is rendered in scrollback exactly the same way
   *  `handleSend` would render it. Attachments are NOT consumed — this
   *  path is purely for synthetic, no-attachment turns. */
  const submitMessage = useCallback(
    (text: string): boolean => {
      const trimmed = text.trim();
      if (!trimmed || !wsRef.current?.connected) return false;
      const turn: QueuedTurn = { id: generateUUID(), content: trimmed, attachments: [] };

      if (typingRef.current) {
        setQueuedTurns((prev) => [...prev, turn]);
        setMessages((prev) => [
          ...prev,
          {
            id: turn.id,
            role: 'user',
            content: trimmed,
            deliveryStatus: 'queued',
            timestamp: new Date(),
          },
        ]);
        return true;
      }

      setMessages((prev) => [
        ...prev,
        {
          id: turn.id,
          role: 'user',
          content: trimmed,
          deliveryStatus: 'sending',
          timestamp: new Date(),
        },
      ]);

      return sendTurn(turn, false);
    },
    [sendTurn],
  );

  /** Upload a file to the session's attachment store and stage it for
   *  the next send. For images we also generate a client-side data URL
   *  preview so the chip strip can show a thumbnail. */
  const addAttachment = useCallback(
    async (file: File): Promise<StagedAttachment | null> => {
      setUploadingCount((c) => c + 1);
      try {
        const meta = await uploadAttachment(sessionId, file);
        let previewUrl: string | undefined;
        if (meta.mime.startsWith('image/')) {
          previewUrl = await new Promise<string | undefined>((resolve) => {
            const reader = new FileReader();
            reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : undefined);
            reader.onerror = () => resolve(undefined);
            reader.readAsDataURL(file);
          });
        }
        const staged: StagedAttachment = { ...meta, previewUrl };
        setAttachments((prev) => [...prev, staged]);
        return staged;
      } catch (err) {
        // Surface the upload error on the same banner the connection
        // errors use so users see *some* feedback. Don't drop already-
        // staged attachments on a single failure.
        const msg = err instanceof Error ? err.message : 'upload failed';
        setError(`Attachment upload failed: ${msg}`);
        return null;
      } finally {
        setUploadingCount((c) => Math.max(0, c - 1));
      }
    },
    [sessionId],
  );

  /** Remove a staged attachment by file_id (the × on a chip). */
  const removeAttachment = useCallback((fileId: string) => {
    setAttachments((prev) => prev.filter((a) => a.file_id !== fileId));
  }, []);

  /** Clear all staged attachments without sending. */
  const clearAttachments = useCallback(() => {
    setAttachments([]);
  }, []);

  const handleTextareaChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`;
  }, []);

  /** Wipe all rendered messages + activity state without disturbing the
   *  WebSocket session. Used by the `/clear` slash command. The session
   *  is still alive and can receive further turns; this only clears what
   *  the user sees. */
  const clearMessages = useCallback(() => {
    setMessages([]);
    setActivities([]);
    activitiesRef.current = [];
    setStreamingContent('');
    setStreamingThinking('');
    pendingContentRef.current = '';
    pendingThinkingRef.current = '';
    capturedThinkingRef.current = '';
    setTyping(false);
    setError(null);
  }, []);

  /** Inject a synthetic operator-role message into the scrollback. Used
   *  by `/help` and other client-side commands that want to surface
   *  output inline rather than via a modal. */
  const appendSystemMessage = useCallback((content: string) => {
    setMessages((prev) => [
      ...prev,
      {
        id: generateUUID(),
        role: 'operator',
        content,
        operatorPhase: 'completed',
        timestamp: new Date(),
      },
    ]);
  }, []);

  const copyMessage = useCallback(async (messageId: string, content: string) => {
    if (!(await copyToClipboard(content))) return;
    setCopiedId(messageId);
    setTimeout(() => setCopiedId((prev) => (prev === messageId ? null : prev)), 2000);
  }, []);

  return {
    activities,
    addAttachment,
    agentEvents,
    appendSystemMessage,
    attachments,
    clearAttachments,
    clearMessages,
    connected,
    copiedId,
    copyMessage,
    error,
    handleSend,
    handleTextareaChange,
    input,
    inputRef,
    messages,
    removeAttachment,
    setInput,
    streamingContent,
    streamingThinking,
    queuedTurns,
    stopCurrentTurn,
    stopping,
    submitMessage,
    typing,
    uploadingCount,
  };
}
