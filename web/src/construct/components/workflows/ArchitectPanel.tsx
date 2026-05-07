/**
 * Architect — editor-scoped chat panel.
 *
 * Slides in from the right of the workflow editor. Reuses
 * `useAgentChatSession` (the same WebSocket-streaming hook the dashboard
 * AssistantPanel uses) so the chat surface, tool-call cards, slash menu,
 * and history persistence all behave identically.
 *
 * Architecture (per the architectural realignment):
 *
 *   - Architect generates YAML in memory and pipes it into the editor's
 *     `definition` state via the `onYamlProposed` callback.
 *   - The existing yamlSync flow re-parses → DAG canvas re-renders →
 *     YAML pane updates.
 *   - Save is user-driven — toolbar Save creates the Kumiho revision when
 *     the user decides.
 *   - When base_yaml is non-empty, Architect MERGES (extends with new
 *     steps), it does not overwrite.
 *
 * Architect must NEVER call `create_workflow` (disk-only) or
 * `revise_workflow` / `register_workflow` (Kumiho-persisting). The system
 * preface enforces this.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Check, Copy, Loader2, Send, Wand2, X } from 'lucide-react';
import {
  useAgentChatSession,
  type ToolResultEvent,
} from '@/construct/hooks/useAgentChatSession';
import {
  matchCommands,
  parseInput,
  resolveCommand,
  type SlashCommandContext,
  type SlashThemeName,
} from '@/construct/components/assistant/slashCommands';
import SlashCommandMenu from '@/construct/components/assistant/SlashCommandMenu';
import ActivityCard from '@/construct/components/assistant/ActivityCard';
import { useTheme } from '@/construct/hooks/useTheme';
import { useT, type Locale } from '@/construct/hooks/useT';
import { copyToClipboard } from '@/construct/lib/clipboard';

interface ArchitectPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** kref of the workflow currently open in the editor, or null when
   *  the user hasn't saved the workflow yet. Informational only — the
   *  Architect no longer requires a kref to operate. */
  workflowKref: string | null;
  /** Display name — surfaced in the header badge and the context preface.
   *  Null when the workflow has no name yet. */
  workflowName: string | null;
  /** The editor's current YAML (the `definition` string). Sent in
   *  pageContext on each chat turn so Architect can use it as `base_yaml`
   *  in `propose_workflow_yaml` calls. */
  currentYaml: string;
  /** Called when a `propose_workflow_yaml` tool result arrives with
   *  valid YAML. The parent updates the editor's `definition` state and
   *  re-parses to nodes/edges. */
  onYamlProposed: (yaml: string, summary: string) => void;
  /** When set on first open, pre-fill the chat input with this text.
   *  The user reviews and can edit before sending — we never auto-send. */
  initialPrompt?: string;
}

/** Stable session id. Persisted in sessionStorage so reopening the panel
 *  within the same tab continues the same chat thread. Falls back to a
 *  per-tab id when no kref is available (fresh canvas), so the user can
 *  still chat with Architect before saving. */
function architectSessionIdFor(workflowKref: string | null): string {
  const key = workflowKref
    ? `construct.architect.session_id:${workflowKref}`
    : 'construct.architect.session_id:new';
  try {
    const existing = sessionStorage.getItem(key);
    if (existing) return existing;
    const fresh =
      typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `arch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    sessionStorage.setItem(key, fresh);
    return fresh;
  } catch {
    return `arch-${workflowKref ?? 'new'}`;
  }
}

/** Build the system-style preface that primes every new Architect chat.
 *  Front-loads the chat-invisibility constraint: the editor canvas updates
 *  ONLY from `propose_workflow_yaml` tool results, never from chat-rendered
 *  YAML. The persistence tools (`create_workflow`, `revise_workflow`,
 *  `register_workflow`, …) are stripped at runtime by the Architect tool
 *  guard; we still mention them here so the LLM doesn't waste tokens
 *  hallucinating calls. */
function buildContextPreface(workflowName: string): string {
  return [
    'You are the Architect for the Construct workflow editor.',
    '',
    'CRITICAL: The user CANNOT see workflows you write in chat. They only see what you submit via `propose_workflow_yaml`. A YAML code block in your chat reply is invisible to the editor — it does NOT render on the canvas, and the user has no way to save it.',
    '',
    'Your ONE proposal tool: `propose_workflow_yaml(proposed_yaml=<YAML>, intent_summary=<one line>, base_yaml=<editor\'s current YAML, or empty>)`.',
    '',
    'DO NOT:',
    '- Print the YAML in chat instead of calling propose_workflow_yaml',
    '- Call create_workflow, revise_workflow, register_workflow, save_workflow_yaml, save_workflow_preset (they\'re not available; the editor handles persistence)',
    '',
    'Process:',
    '1. If the user describes a workflow, design it from available primitives.',
    '2. Optionally call get_workflow_metadata first to check available step types, agents, skills, auth profiles.',
    '3. Construct the complete YAML.',
    '4. **If base_yaml is non-empty, EXTEND it.** Treat existing steps as fixed. Add new steps after them. Do NOT remove or modify existing steps unless the user explicitly asks.',
    '5. Call propose_workflow_yaml(...) with your YAML. The tool validates and the editor receives the proposal.',
    '6. After the call returns, summarize what you proposed in a single short paragraph in chat. The summary is for the user to read — but the actual workflow goes through the tool, not the chat.',
    '',
    'If propose_workflow_yaml returns valid: false, read the errors and call it again with a fixed YAML. Don\'t print the broken YAML in chat — the user can\'t fix it from there.',
    '',
    'Workflow context:',
    `- Current name: ${workflowName || '(unnamed)'}`,
    '- The editor\'s current YAML state will be in your message context as the editor-state block.',
  ].join('\n');
}

/** Compose the `pageContext` string sent on every chat turn. Includes the
 *  editor's current YAML so Architect always sees the latest state — and
 *  can pass it as `base_yaml` when calling `propose_workflow_yaml`. */
function buildPageContext(
  workflowName: string,
  currentYaml: string,
): string {
  const indented = currentYaml
    .split('\n')
    .map((line) => `  ${line}`)
    .join('\n');
  return [
    'v2:workflow_editor:architect',
    '<editor-state>',
    `  <workflow_name>${workflowName || '(unnamed)'}</workflow_name>`,
    '  <current_yaml>',
    indented || '    (empty)',
    '  </current_yaml>',
    '</editor-state>',
  ].join('\n');
}

/** The live chat surface. */
function ArchitectChatSurface({
  open,
  onOpenChange,
  workflowKref,
  workflowName,
  currentYaml,
  onYamlProposed,
  initialPrompt,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workflowKref: string | null;
  workflowName: string;
  currentYaml: string;
  onYamlProposed: (yaml: string, summary: string) => void;
  initialPrompt?: string;
}) {
  const sessionId = useMemo(() => architectSessionIdFor(workflowKref), [workflowKref]);
  // pageContext recomputes on every YAML change so the next send carries
  // the latest editor state. The hook reads pageContext via closure on
  // each handleSend, so this is cheap — no WS reconnect.
  const pageContext = useMemo(
    () => buildPageContext(workflowName, currentYaml),
    [workflowName, currentYaml],
  );
  const draftKey = workflowKref
    ? `construct-architect:${workflowKref}`
    : 'construct-architect:new';
  const { setTheme } = useTheme();
  const { setLocale } = useT();

  // Track the last propose_workflow_yaml result we've already piped into
  // the editor — without this, a re-render could re-fire onYamlProposed
  // for the same proposal.
  const lastProcessedResultId = useRef<string | null>(null);

  const handleToolResult = useCallback(
    (evt: ToolResultEvent) => {
      if (evt.name !== 'propose_workflow_yaml') return;
      if (lastProcessedResultId.current === evt.id) return;
      lastProcessedResultId.current = evt.id;

      let parsed: {
        yaml?: string;
        summary?: string;
        valid?: boolean;
      } | null = null;
      try {
        parsed = JSON.parse(evt.output);
      } catch {
        // Some servers stringify with extra wrapper text; try a best-effort
        // brace-match. If that fails, just bail — the activity feed already
        // showed the raw output.
        const start = evt.output.indexOf('{');
        const end = evt.output.lastIndexOf('}');
        if (start >= 0 && end > start) {
          try {
            parsed = JSON.parse(evt.output.slice(start, end + 1));
          } catch {
            parsed = null;
          }
        }
      }
      if (!parsed) return;
      if (parsed.valid && typeof parsed.yaml === 'string' && parsed.yaml.trim()) {
        onYamlProposed(parsed.yaml, parsed.summary ?? '');
      }
      // Validation failures already surface in the activity feed via the
      // tool_result card — no extra UI needed here. The LLM should re-roll.
    },
    [onYamlProposed],
  );

  const {
    activities,
    appendSystemMessage,
    clearMessages,
    connected,
    error,
    handleSend,
    handleTextareaChange,
    input,
    inputRef,
    messages,
    setInput,
    streamingContent,
    streamingThinking,
    submitMessage,
    typing,
  } = useAgentChatSession({
    sessionId,
    draftKey,
    pageContext,
    onToolResult: handleToolResult,
  });

  // Inject the context preface as a synthetic operator-role message the
  // first time we observe an empty scrollback for this session.
  const prefacedRef = useRef<string | null>(null);
  useEffect(() => {
    if (prefacedRef.current === sessionId) return;
    if (!open) return;
    if (messages.length > 0) {
      prefacedRef.current = sessionId;
      return;
    }
    appendSystemMessage(buildContextPreface(workflowName));
    prefacedRef.current = sessionId;
  }, [open, sessionId, messages.length, appendSystemMessage, workflowName]);

  // Pre-fill the input on first open if `initialPrompt` was supplied.
  // We only do this when the input is empty so a user's existing draft
  // isn't clobbered.
  const prefilledRef = useRef<string | null>(null);
  useEffect(() => {
    if (!open) return;
    if (!initialPrompt) return;
    if (prefilledRef.current === sessionId) return;
    if (input.trim().length === 0) {
      setInput(initialPrompt);
    }
    prefilledRef.current = sessionId;
  }, [open, initialPrompt, sessionId, input, setInput]);

  // ── Slash menu plumbing ────────────────────────────────────────────
  const composerRef = useRef<HTMLDivElement>(null);
  const [slashSelectedIndex, setSlashSelectedIndex] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  const slashMatches = useMemo(() => {
    if (slashDismissed) return [];
    const trimmed = input.trimStart();
    if (!trimmed.startsWith('/')) return [];
    if (trimmed.includes(' ') || trimmed.includes('\n')) return [];
    return matchCommands(trimmed, 'workflow_editor');
  }, [input, slashDismissed]);

  useEffect(() => {
    if (slashSelectedIndex >= slashMatches.length) {
      setSlashSelectedIndex(slashMatches.length === 0 ? 0 : slashMatches.length - 1);
    }
  }, [slashMatches.length, slashSelectedIndex]);

  useEffect(() => {
    if (slashDismissed && !input.startsWith('/')) setSlashDismissed(false);
  }, [input, slashDismissed]);

  const slashCtx = useMemo<SlashCommandContext>(
    () => ({
      clearMessages,
      appendSystemMessage,
      openFilePicker: () => {},
      addTab: () => {},
      openNewTabMenu: () => {},
      closeActiveTab: () => onOpenChange(false),
      setLang: (code: string) => {
        setLocale(code as Locale);
      },
      setTheme: (theme: SlashThemeName) => {
        setTheme(theme);
      },
      submitMessage,
      workflowKref: workflowKref ?? undefined,
      workflowName,
    }),
    [
      clearMessages,
      appendSystemMessage,
      onOpenChange,
      setLocale,
      setTheme,
      submitMessage,
      workflowKref,
      workflowName,
    ],
  );

  const runSlashFromInput = useCallback((): boolean => {
    const parsed = parseInput(input);
    if (!parsed) return false;
    const cmd = resolveCommand(parsed.name);
    if (!cmd) return false;
    setInput('');
    setSlashSelectedIndex(0);
    setSlashDismissed(false);
    if (inputRef.current) inputRef.current.style.height = 'auto';
    try {
      void cmd.handler(slashCtx, parsed.args);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      appendSystemMessage(`Command "/${cmd.name}" failed: ${msg}`);
    }
    return true;
  }, [input, slashCtx, setInput, inputRef, appendSystemMessage]);

  const pickSlashCommand = useCallback(
    (index: number) => {
      const cmd = slashMatches[index];
      if (!cmd) return;
      if (cmd.args) {
        setInput(`/${cmd.name} `);
        setSlashSelectedIndex(0);
        inputRef.current?.focus();
      } else {
        setInput('');
        setSlashSelectedIndex(0);
        if (inputRef.current) inputRef.current.style.height = 'auto';
        try {
          void cmd.handler(slashCtx, '');
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          appendSystemMessage(`Command "/${cmd.name}" failed: ${msg}`);
        }
      }
    },
    [slashMatches, slashCtx, setInput, inputRef, appendSystemMessage],
  );

  // Auto-scroll to the bottom on new content.
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [activities, messages, streamingContent, typing]);

  // Focus the textarea when the panel opens.
  useEffect(() => {
    if (!open) return;
    const id = setTimeout(() => inputRef.current?.focus(), 220);
    return () => clearTimeout(id);
  }, [open, inputRef]);

  const copyMessage = useCallback(async (id: string, text: string) => {
    if (!(await copyToClipboard(text))) return;
    setCopiedId(id);
    setTimeout(() => setCopiedId((curr) => (curr === id ? null : curr)), 1200);
  }, []);

  return (
    <>
      {/* Connection status pill */}
      <div
        className="flex items-center justify-end border-b px-3 py-1"
        style={{
          borderColor: 'var(--construct-border-soft)',
          background: 'var(--construct-bg-surface)',
        }}
      >
        <span
          className="flex shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
          style={{ color: 'var(--construct-text-faint)' }}
        >
          <span
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{
              background: connected
                ? 'var(--construct-status-success)'
                : 'var(--construct-status-danger)',
            }}
          />
          {connected ? 'live' : 'offline'}
        </span>
      </div>

      {/* Typing sweep */}
      {typing && (
        <div className="h-[2px] overflow-hidden" style={{ background: 'var(--construct-bg-surface)' }}>
          <div
            className="h-full"
            style={{
              background: 'var(--construct-signal-network)',
              width: '40%',
              animation: 'construct-architect-sweep 1.4s ease-in-out infinite alternate',
            }}
          />
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div
          className="border-b px-3 py-2 text-xs"
          style={{
            borderColor: 'color-mix(in srgb, var(--construct-status-danger) 32%, transparent)',
            background: 'color-mix(in srgb, var(--construct-status-danger) 10%, transparent)',
            color: 'var(--construct-status-danger)',
          }}
        >
          {error}
        </div>
      )}

      {/* Scrollback */}
      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-3 py-3 font-mono leading-6"
        style={{ fontSize: 13 }}
      >
        {messages.length === 0 && !typing ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <pre className="text-xs" style={{ color: 'var(--construct-text-faint)' }}>
{`┌──────────────────────────────┐
│  architect ready · describe  │
│  the workflow you want       │
└──────────────────────────────┘`}
            </pre>
            <p
              className="mt-3 max-w-xs text-xs leading-5"
              style={{ color: 'var(--construct-text-muted)' }}
            >
              Try /architect add a python step that prints hello
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {messages.map((msg) => {
              const prefix = msg.role === 'user' ? 'you' : msg.role === 'operator' ? 'sys' : 'architect';
              const color =
                msg.role === 'user'
                  ? 'var(--construct-text-secondary)'
                  : msg.role === 'operator'
                    ? 'var(--construct-signal-network)'
                    : 'var(--construct-signal-live)';
              const copied = copiedId === msg.id;
              return (
                <div key={msg.id} className="group">
                  {msg.activityLog && msg.activityLog.length > 0 && (
                    <div className="mb-1 space-y-0.5">
                      {msg.activityLog.map((evt) => (
                        <ActivityCard
                          key={evt.id}
                          event={evt}
                          accent={
                            evt.kind === 'tool_result'
                              ? 'var(--construct-status-success)'
                              : 'var(--construct-signal-network)'
                          }
                          fontSize={13}
                        />
                      ))}
                    </div>
                  )}
                  <div className="whitespace-pre-wrap break-words">
                    <span style={{ color, fontWeight: 600 }}>{prefix} {'>'} </span>
                    <span style={{ color }}>{msg.content}</span>
                  </div>
                  <div
                    className="mt-0.5 flex items-center justify-end gap-2 text-[10px]"
                    style={{ color: 'var(--construct-text-faint)' }}
                  >
                    <button
                      type="button"
                      onClick={() => copyMessage(msg.id, msg.content)}
                      aria-label={copied ? 'Copied' : 'Copy message'}
                      title={copied ? 'Copied' : 'Copy'}
                      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 opacity-50 transition-all hover:bg-white/5 hover:opacity-100 group-hover:opacity-80"
                      style={{ color: 'var(--construct-text-muted)' }}
                    >
                      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                      <span>{copied ? 'copied' : 'copy'}</span>
                    </button>
                  </div>
                </div>
              );
            })}

            {typing && activities.length > 0 && (
              <div className="space-y-0.5">
                {activities.map((evt) => (
                  <ActivityCard
                    key={evt.id}
                    event={evt}
                    accent={
                      evt.kind === 'tool_result'
                        ? 'var(--construct-status-success)'
                        : evt.kind === 'thinking'
                          ? 'var(--construct-text-faint)'
                          : 'var(--construct-signal-network)'
                    }
                    fontSize={13}
                  />
                ))}
              </div>
            )}

            {typing && (streamingContent || streamingThinking) && (
              <div className="whitespace-pre-wrap break-words">
                <span
                  style={{ color: 'var(--construct-signal-live)', fontWeight: 600 }}
                >
                  architect {'>'}{' '}
                </span>
                <span style={{ color: 'var(--construct-signal-live)' }}>
                  {streamingContent || '…'}
                </span>
              </div>
            )}

            {typing && !streamingContent && !streamingThinking && activities.length === 0 && (
              <div
                className="animate-pulse"
                style={{ color: 'var(--construct-signal-live)' }}
              >
                architect {'>'} <Loader2 className="inline h-3 w-3 animate-spin" />
              </div>
            )}
          </div>
        )}
      </div>

      {/* Composer */}
      <div
        ref={composerRef}
        className="relative border-t px-3 py-2"
        style={{ borderColor: 'var(--construct-border-soft)' }}
      >
        <div
          className="flex items-end gap-2 rounded-md border px-2 py-1.5"
          style={{
            borderColor: 'var(--construct-border-soft)',
            color: 'var(--construct-signal-network)',
          }}
        >
          <span
            className="shrink-0 pb-[3px] font-mono text-sm font-semibold"
            style={{ color: 'var(--construct-signal-network)' }}
          >
            {'>'}<span className="construct-cursor-blink">_</span>
          </span>
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            onChange={handleTextareaChange}
            onKeyDown={(e) => {
              const menuOpen = slashMatches.length > 0;
              if (menuOpen) {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setSlashSelectedIndex((i) => (i + 1) % slashMatches.length);
                  return;
                }
                if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setSlashSelectedIndex(
                    (i) => (i - 1 + slashMatches.length) % slashMatches.length,
                  );
                  return;
                }
                if (e.key === 'Tab') {
                  e.preventDefault();
                  pickSlashCommand(slashSelectedIndex);
                  return;
                }
                if (e.key === 'Escape') {
                  e.preventDefault();
                  setSlashDismissed(true);
                  return;
                }
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  pickSlashCommand(slashSelectedIndex);
                  return;
                }
              }
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (runSlashFromInput()) return;
                handleSend();
              }
            }}
            placeholder={
              connected
                ? 'Describe a change… (try /architect)'
                : 'connecting…'
            }
            disabled={!connected}
            className="min-h-[1.75rem] min-w-0 flex-1 resize-none bg-transparent font-mono outline-none focus:outline-none focus-visible:outline-none disabled:opacity-50"
            style={{
              color: 'var(--construct-text-primary)',
              caretColor: 'var(--construct-signal-network)',
              maxHeight: '6rem',
              fontSize: 16,
            }}
          />
          <button
            type="button"
            onClick={() => handleSend()}
            disabled={!connected || !input.trim()}
            aria-label="Send message"
            title={connected ? 'Send (Enter)' : 'Disconnected'}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded transition-all hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-30"
            style={{
              color: input.trim() && connected
                ? 'var(--construct-signal-network)'
                : 'var(--construct-text-faint)',
            }}
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </div>

        <SlashCommandMenu
          anchorRef={composerRef}
          matches={slashMatches}
          selectedIndex={slashSelectedIndex}
          onPick={pickSlashCommand}
        />
      </div>
    </>
  );
}

export default function ArchitectPanel({
  open,
  onOpenChange,
  workflowKref,
  workflowName,
  currentYaml,
  onYamlProposed,
  initialPrompt,
}: ArchitectPanelProps) {
  // Esc closes the panel.
  useEffect(() => {
    if (!open) return undefined;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onOpenChange(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onOpenChange]);

  const displayName = workflowName ?? 'workflow';

  return (
    <>
      {/* Scrim */}
      {open && (
        <div
          className="fixed inset-0 z-[80]"
          style={{ background: 'rgba(0,0,0,0.18)' }}
          onClick={() => onOpenChange(false)}
        />
      )}

      <aside
        className="fixed right-0 top-0 z-[90] flex h-full flex-col border-l"
        style={{
          width: 480,
          maxWidth: '100vw',
          transform: open ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 280ms ease-out',
          background: 'var(--construct-bg-base)',
          borderColor: 'var(--construct-border-strong)',
          boxShadow: open ? 'var(--construct-shadow-overlay)' : 'none',
          pointerEvents: open ? 'auto' : 'none',
        }}
        aria-hidden={!open}
      >
        {/* Header */}
        <div
          className="flex items-center gap-2 border-b px-3 py-2"
          style={{
            borderColor: 'var(--construct-border-soft)',
            background: 'var(--construct-bg-surface)',
          }}
        >
          <Wand2
            size={14}
            style={{ color: 'var(--construct-signal-network)' }}
            aria-hidden
          />
          <span
            className="text-[12px] font-semibold uppercase tracking-[0.14em]"
            style={{ color: 'var(--construct-text-primary)' }}
          >
            Architect
          </span>
          <span
            className="ml-1 truncate rounded px-2 py-0.5 text-[11px]"
            style={{
              background: 'var(--pc-bg-input)',
              color: 'var(--construct-text-muted)',
              maxWidth: 220,
            }}
            title={workflowKref ? `${displayName} · ${workflowKref}` : displayName}
          >
            {displayName}
          </span>
          <div className="flex-1" />
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close Architect"
            title="Close (Esc)"
            className="ml-1 rounded p-1 transition-colors hover:bg-white/5"
            style={{ color: 'var(--construct-text-faint)' }}
          >
            <X size={14} />
          </button>
        </div>

        <ArchitectChatSurface
          open={open}
          onOpenChange={onOpenChange}
          workflowKref={workflowKref}
          workflowName={displayName}
          currentYaml={currentYaml}
          onYamlProposed={onYamlProposed}
          initialPrompt={initialPrompt}
        />

        {/* Footer attribution */}
        <div
          className="border-t px-3 py-1.5 text-center text-[10px] uppercase tracking-[0.16em]"
          style={{
            borderColor: 'var(--construct-border-soft)',
            color: 'var(--pc-text-faint, var(--construct-text-faint))',
            background: 'var(--construct-bg-surface)',
          }}
        >
          Powered by Operator
        </div>
      </aside>

      <style>{`
        @keyframes construct-architect-sweep {
          from { transform: translateX(-10%); }
          to   { transform: translateX(160%); }
        }
      `}</style>
    </>
  );
}
