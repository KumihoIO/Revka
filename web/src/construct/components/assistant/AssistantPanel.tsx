import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  Code2,
  Copy,
  Loader2,
  MessageSquare,
  Paperclip,
  Plus,
  Send,
  Settings,
  Terminal,
  X,
} from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { generateUUID } from '@/lib/uuid';
import { useAgentChatSession } from '@/construct/hooks/useAgentChatSession';
import { useTheme } from '@/construct/hooks/useTheme';
import { useT, type Locale } from '@/construct/hooks/useT';
import { useV2Assistant } from './AssistantContext';
import { v2RouteMeta } from '../layout/construct-navigation';
import {
  COLOR_SCHEMES,
  SCHEME_KEYS,
  useAssistantConfig,
  type AssistantConfig,
  type SchemeColors,
} from './assistantConfig';
import XTerminal from './XTerminal';
import CodeTab, { basename, type CodeSession, toolLabel } from './CodeTab';
import ActivityCard from './ActivityCard';
import AttachmentChip from './AttachmentChip';
import SlashCommandMenu from './SlashCommandMenu';
import {
  matchCommands,
  parseInput,
  resolveCommand,
  type SlashCommandContext,
  type SlashThemeName,
} from './slashCommands';
import { copyToClipboard } from '@/construct/lib/clipboard';

/* ── types ─────────────────────────────────────────── */

type TabType = 'chat' | 'terminal' | 'code';

interface AssistantTab {
  id: string;
  type: TabType;
  title: string;
  sessionId: string;
  /** For code tabs: null until the user starts a session. */
  codeSession?: CodeSession | null;
  /** Override the pageContext used by chat tabs (e.g. 'v2:code:operator'). */
  pageContextOverride?: string;
}

/* ── helpers ───────────────────────────────────────── */

function routeContext(pathname: string) {
  return pathname.replace(/^\//, '');
}

/* ── ConfigPanel ──────────────────────────────────── */

function ConfigPanel({
  config,
  updateConfig,
}: {
  config: AssistantConfig;
  updateConfig: (partial: Partial<AssistantConfig>) => void;
}) {
  return (
    <div
      className="border-b px-4 py-3"
      style={{ borderColor: 'var(--construct-border-soft)', background: 'color-mix(in srgb, var(--construct-bg-surface) 95%, transparent)' }}
    >
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
        {/* Color Scheme */}
        <div>
          <label className="mb-1 block font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--construct-text-faint)', fontSize: '10px' }}>
            Color Scheme
          </label>
          <div className="flex gap-1.5">
            {SCHEME_KEYS.map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => updateConfig({ colorScheme: key })}
                className="rounded px-2 py-1 text-[10px] font-semibold uppercase tracking-wider transition-colors"
                style={{
                  background: config.colorScheme === key ? COLOR_SCHEMES[key].colors.primary : 'transparent',
                  color: config.colorScheme === key ? '#0c0c0c' : 'var(--construct-text-secondary)',
                  border: `1px solid ${config.colorScheme === key ? COLOR_SCHEMES[key].colors.primary : 'var(--construct-border-soft)'}`,
                }}
              >
                {COLOR_SCHEMES[key].label}
              </button>
            ))}
          </div>
        </div>

        {/* Font Size */}
        <div>
          <label className="mb-1 block font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--construct-text-faint)', fontSize: '10px' }}>
            Font Size — {config.fontSize}px
          </label>
          <input
            type="range"
            min={10}
            max={20}
            step={1}
            value={config.fontSize}
            onChange={(e) => updateConfig({ fontSize: Number(e.target.value) })}
            className="w-full accent-current"
            style={{ color: 'var(--construct-signal-live)' }}
          />
        </div>

        {/* Cursor Blink */}
        <div className="flex items-center gap-2">
          <label className="font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--construct-text-faint)', fontSize: '10px' }}>
            Cursor Blink
          </label>
          <button
            type="button"
            onClick={() => updateConfig({ cursorBlink: !config.cursorBlink })}
            className="relative h-5 w-9 rounded-full transition-colors"
            style={{
              background: config.cursorBlink ? 'var(--construct-signal-live)' : 'var(--construct-border-strong)',
            }}
          >
            <span
              className="absolute top-0.5 block h-4 w-4 rounded-full bg-white shadow transition-transform"
              style={{ transform: config.cursorBlink ? 'translateX(18px)' : 'translateX(2px)' }}
            />
          </button>
        </div>

        {/* Panel Height */}
        <div>
          <label className="mb-1 block font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--construct-text-faint)', fontSize: '10px' }}>
            Panel Height — {config.panelHeightPercent}%
          </label>
          <input
            type="range"
            min={25}
            max={90}
            step={5}
            value={config.panelHeightPercent}
            onChange={(e) => updateConfig({ panelHeightPercent: Number(e.target.value) })}
            className="w-full accent-current"
            style={{ color: 'var(--construct-signal-live)' }}
          />
        </div>
      </div>
    </div>
  );
}

/* ── ChatPane ─────────────────────────────────────── */

function ChatPane({
  sessionId,
  pageContext,
  placeholder,
  config,
  colors,
  visible,
  onAddTab,
  onCloseActiveTab,
  onOpenNewTabMenu,
}: {
  sessionId: string;
  pageContext: string;
  placeholder: string;
  config: AssistantConfig;
  colors: SchemeColors;
  /** When false the pane is `display:none` but stays mounted, so the
   *  WebSocket stream keeps producing typing/chunk/done events into the
   *  hook's state. Switching back instantly shows the in-flight progress
   *  instead of unmounting + remounting + losing every event in between. */
  visible: boolean;
  onAddTab: (type: TabType) => void;
  onCloseActiveTab: () => void;
  onOpenNewTabMenu: () => void;
}) {
  const { open } = useV2Assistant();
  const { setTheme } = useTheme();
  const { setLocale } = useT();
  const {
    activities,
    addAttachment,
    appendSystemMessage,
    attachments,
    clearMessages,
    connected,
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
    typing,
    uploadingCount,
  } = useAgentChatSession({ sessionId, draftKey: `construct-assistant:${pageContext}`, pageContext });

  const scrollRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerRef = useRef<HTMLDivElement>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [dragHover, setDragHover] = useState(false);
  // Index highlighted in the slash menu — driven by ArrowUp/Down from the
  // textarea so the input can keep focus while we navigate the popover.
  const [slashSelectedIndex, setSlashSelectedIndex] = useState(0);
  // After Esc, suppress the menu until the user changes the input again.
  // Otherwise pressing Esc would just flicker — matchCommands would keep
  // returning the same list on every render.
  const [slashDismissed, setSlashDismissed] = useState(false);

  // Concurrently upload a list of files (e.g. multi-select from the
  // file picker, or multiple drag-drop items). Errors on individual
  // uploads surface via the hook's `error` banner; one failure
  // doesn't cancel the rest.
  const handleFileList = useCallback(
    async (files: FileList | File[]) => {
      const arr = Array.from(files);
      await Promise.all(arr.map((f) => addAttachment(f)));
    },
    [addAttachment],
  );

  const onPickFiles = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  // ── Slash command plumbing ───────────────────────────────────────
  // Menu visibility: only while the user is typing the *name* — input
  // starts with `/`, no space (args mode), no newline (multi-line draft).
  const slashMatches = useMemo(() => {
    if (slashDismissed) return [];
    const trimmed = input.trimStart();
    if (!trimmed.startsWith('/')) return [];
    if (trimmed.includes(' ') || trimmed.includes('\n')) return [];
    return matchCommands(trimmed);
  }, [input, slashDismissed]);

  // Clamp the highlighted index whenever the match list shrinks.
  useEffect(() => {
    if (slashSelectedIndex >= slashMatches.length) {
      setSlashSelectedIndex(slashMatches.length === 0 ? 0 : slashMatches.length - 1);
    }
  }, [slashMatches.length, slashSelectedIndex]);

  // Re-arm the menu the moment the user starts typing again after Esc.
  useEffect(() => {
    if (slashDismissed && !input.startsWith('/')) setSlashDismissed(false);
  }, [input, slashDismissed]);

  const slashCtx = useMemo<SlashCommandContext>(
    () => ({
      clearMessages,
      appendSystemMessage,
      openFilePicker: () => fileInputRef.current?.click(),
      addTab: onAddTab,
      openNewTabMenu: onOpenNewTabMenu,
      closeActiveTab: onCloseActiveTab,
      setLang: (code: string) => {
        setLocale(code as Locale);
      },
      setTheme: (theme: SlashThemeName) => {
        setTheme(theme);
      },
    }),
    [clearMessages, appendSystemMessage, onAddTab, onOpenNewTabMenu, onCloseActiveTab, setLocale, setTheme],
  );

  /** Resolve and run a typed slash invocation (called from Enter when
   *  the input parses as `/<known-name> [args]`). Returns true if a
   *  command was executed; false if the input wasn't a recognized
   *  command and should fall through to `handleSend`. */
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

  /** Pick a command from the menu (click or Enter while menu is open).
   *  If the command takes args, prefill `/<name> ` so the user can type
   *  them; otherwise execute immediately. */
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

  const onPaste = useCallback(
    (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
      // Capture image blobs from the clipboard (e.g. screenshots). Text
      // pastes flow through normally — we only intercept when there's
      // actual file content.
      const items = Array.from(e.clipboardData?.items ?? []);
      const files: File[] = items
        .filter((it) => it.kind === 'file')
        .map((it) => it.getAsFile())
        .filter((f): f is File => f !== null);
      if (files.length > 0) {
        e.preventDefault();
        void handleFileList(files);
      }
    },
    [handleFileList],
  );

  const onDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    if (e.dataTransfer?.types?.includes('Files')) setDragHover(true);
  }, []);

  const onDragLeave = useCallback((e: React.DragEvent) => {
    // Only clear if we've actually left the composer (not just bubbled
    // through a child) — relatedTarget on `null` means leaving the
    // window; our containment check filters that out too.
    if (e.currentTarget === e.target) setDragHover(false);
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragHover(false);
      const files = e.dataTransfer?.files;
      if (files && files.length > 0) void handleFileList(files);
    },
    [handleFileList],
  );

  const copyMessage = useCallback(async (id: string, text: string) => {
    if (!(await copyToClipboard(text))) return;
    setCopiedId(id);
    setTimeout(() => setCopiedId((curr) => (curr === id ? null : curr)), 1200);
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [activities, messages, streamingContent, typing]);

  // Focus the message input each time the panel opens *and* this pane
  // is the visible one. The 320ms delay matches the panel's 300ms
  // slide-down transition so the textarea is on screen when the cursor
  // lands. Without the visible guard, every mounted (but hidden) chat
  // tab would race to grab focus when the panel opens.
  useEffect(() => {
    if (!open || !visible) return;
    const id = setTimeout(() => inputRef.current?.focus(), 320);
    return () => clearTimeout(id);
  }, [open, visible, inputRef]);

  const roleColor = useCallback(
    (role: string) => {
      if (role === 'user') return colors.user;
      if (role === 'operator') return colors.secondary;
      return colors.primary;
    },
    [colors],
  );

  const roleGlow = useCallback(
    (role: string) => {
      if (role === 'agent') return colors.glow;
      if (role === 'operator') return colors.glowSecondary;
      return 'none';
    },
    [colors],
  );

  return (
    <div
      className="min-h-0 flex-1 flex-col"
      style={{ display: visible ? 'flex' : 'none' }}
    >
      {typing && (
        <div className="h-[2px] overflow-hidden" style={{ background: 'var(--construct-bg-surface)' }}>
          <div
            className="h-full"
            style={{
              background: colors.primary,
              width: '40%',
              animation: 'construct-assistant-sweep 1.4s ease-in-out infinite alternate',
            }}
          />
        </div>
      )}

      {error && (
        <div
          className="border-b px-4 py-2 text-xs"
          style={{ borderColor: 'rgba(255,107,122,0.2)', background: 'rgba(255,107,122,0.06)', color: 'var(--construct-status-danger)' }}
        >
          <div className="flex items-center gap-2">
            <AlertCircle className="h-3 w-3 shrink-0" />
            <span>{error}</span>
          </div>
        </div>
      )}

      <div
        ref={scrollRef}
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden p-4 font-mono leading-6"
        style={{ fontSize: `${config.fontSize}px` }}
      >
        {messages.length === 0 && !typing ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <pre className="text-xs" style={{ color: 'var(--construct-text-faint)' }}>
{`┌──────────────────────────────┐
│  session ready · ask away    │
└──────────────────────────────┘`}
            </pre>
            <p className="mt-3 max-w-xs text-xs leading-5" style={{ color: 'var(--construct-text-muted)' }}>
              {placeholder}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {messages.map((msg) => {
              const prefix = msg.role === 'user' ? 'you' : msg.role === 'operator' ? 'sys' : 'op';
              const color = roleColor(msg.role);
              const glow = roleGlow(msg.role);
              const copied = copiedId === msg.id;
              return (
                <div key={msg.id} className="group">
                  {/* Persisted activity log (collapsed by default) — shown ABOVE the
                      finalized agent reply so the user sees "what was done" before
                      "what was said". Empty for plain messages. */}
                  {msg.activityLog && msg.activityLog.length > 0 && (
                    <div className="mb-1 space-y-0.5">
                      {msg.activityLog.map((evt) => (
                        <ActivityCard
                          key={evt.id}
                          event={evt}
                          accent={evt.kind === 'tool_result' ? 'var(--construct-status-success)' : colors.secondary}
                          fontSize={config.fontSize}
                        />
                      ))}
                    </div>
                  )}
                  <div className="whitespace-pre-wrap break-words">
                    <span style={{ color, textShadow: glow, fontWeight: 600 }}>{prefix} {'>'} </span>
                    <span style={{ color: msg.role === 'user' ? 'var(--construct-text-secondary)' : color, textShadow: glow }}>
                      {msg.content}
                    </span>
                  </div>
                  {/* Footer row: timestamp on the left, copy-to-clipboard on the right.
                      Lives BELOW the message text per design — easier to reach with
                      thumb on mobile and doesn't overlap content on long messages. */}
                  <div className="mt-0.5 flex items-center justify-end gap-2 text-[10px]" style={{ color: 'var(--construct-text-faint)' }}>
                    <button
                      type="button"
                      onClick={() => copyMessage(msg.id, msg.content)}
                      aria-label={copied ? 'Copied' : 'Copy message'}
                      title={copied ? 'Copied' : 'Copy'}
                      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 opacity-50 transition-all hover:bg-white/5 hover:opacity-100 focus:opacity-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-current group-hover:opacity-80"
                      style={{ color: 'var(--construct-text-muted)' }}
                    >
                      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                      <span>{copied ? 'copied' : 'copy'}</span>
                    </button>
                  </div>
                </div>
              );
            })}

            {/* In-flight activities — render as collapsible cards so users can drill
                into each tool call's input/output. Replaces the truncate-3 strip. */}
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
                          : colors.secondary
                    }
                    fontSize={config.fontSize}
                  />
                ))}
              </div>
            )}

            {typing && (streamingContent || streamingThinking) && (
              <div className="whitespace-pre-wrap break-words">
                <span style={{ color: colors.primary, textShadow: colors.glow, fontWeight: 600 }}>
                  op {'>'}{' '}
                </span>
                <span style={{ color: colors.primary, textShadow: colors.glow }}>
                  {streamingContent || '…'}
                </span>
              </div>
            )}

            {typing && !streamingContent && !streamingThinking && activities.length === 0 && (
              <div className="animate-pulse" style={{ color: colors.primary, textShadow: colors.glow }}>
                op {'>'} ▊
              </div>
            )}
          </div>
        )}
      </div>

      {/* Composer — :focus-within ring on the container instead of suppressing
          textarea outline globally, so keyboard nav still has an accessible
          focus indicator. The Send button beside the textarea makes the
          action discoverable on touch devices that lack an Enter key.
          Drag-drop and paste handlers on the wrapper accept file uploads;
          the dotted-border overlay shows up while a drag is in flight. */}
      <div
        ref={composerRef}
        className="relative border-t px-4 py-3"
        style={{ borderColor: 'var(--construct-border-soft)' }}
        onDragEnter={onDragEnter}
        onDragOver={(e) => {
          e.preventDefault();
          if (e.dataTransfer?.types?.includes('Files')) setDragHover(true);
        }}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {/* Hidden file input — opened by the paperclip button. Multiple +
            no `accept` filter; the server validates size, and the image vs.
            document handling is decided by the response MIME, not the
            picker filter. */}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            const files = e.target.files;
            if (files && files.length > 0) void handleFileList(files);
            // Reset value so picking the same file twice in a row still fires onChange.
            e.target.value = '';
          }}
        />

        {/* Chip strip — staged attachments waiting to ship with the next
            send. Empty = strip is hidden. */}
        {(attachments.length > 0 || uploadingCount > 0) && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attachments.map((att) => (
              <AttachmentChip
                key={att.file_id}
                attachment={att}
                onRemove={removeAttachment}
                accent={colors.secondary}
              />
            ))}
            {uploadingCount > 0 && (
              <span
                className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[10px]"
                style={{
                  borderColor: 'var(--construct-border-soft)',
                  background: 'var(--construct-bg-surface)',
                  color: 'var(--construct-text-faint)',
                }}
              >
                <Loader2 className="h-3 w-3 animate-spin" />
                uploading {uploadingCount}…
              </span>
            )}
          </div>
        )}

        <div
          className="flex items-end gap-2 rounded-md border px-2 py-1.5 transition-colors"
          style={{
            // No focus-within border highlight — users found it noisy.
            // Border only changes on drag-hover (visible feedback while
            // a file is being dragged in) and otherwise stays at the
            // muted soft border throughout focus + typing.
            borderColor: dragHover ? colors.primary : 'var(--construct-border-soft)',
            background: dragHover ? 'color-mix(in srgb, var(--construct-bg-surface) 85%, transparent)' : 'transparent',
            color: colors.primary,
          }}
        >
          <button
            type="button"
            onClick={onPickFiles}
            disabled={!connected}
            aria-label="Attach files"
            title="Attach files"
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded transition-all hover:bg-white/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-current disabled:cursor-not-allowed disabled:opacity-30"
            style={{ color: 'var(--construct-text-muted)' }}
          >
            <Paperclip className="h-3.5 w-3.5" />
          </button>
          <span
            className="shrink-0 pb-[3px] font-mono text-sm font-semibold"
            style={{ color: colors.primary, textShadow: colors.glow }}
          >
            {'>'}<span className={config.cursorBlink ? 'construct-cursor-blink' : ''}>_</span>
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
                  setSlashSelectedIndex((i) => (i - 1 + slashMatches.length) % slashMatches.length);
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
            onPaste={onPaste}
            placeholder={connected ? 'message…' : 'connecting…'}
            disabled={!connected}
            // `focus-visible:outline-none` overrides the global `:focus-visible`
            // ring set in index.css (2px accent outline) — without it Tailwind's
            // `outline-none` loses to the global selector and we end up with a
            // cyan halo around the composer on every keypress.
            className="min-h-[1.75rem] min-w-0 flex-1 resize-none bg-transparent font-mono outline-none focus:outline-none focus-visible:outline-none disabled:opacity-50"
            style={{
              color: 'var(--construct-text-primary)',
              caretColor: colors.cursorColor,
              maxHeight: '6rem',
              // 16px floor specifically for the textarea so iOS Safari
              // doesn't autozoom on focus. The user's font-size preference
              // still applies to message scrollback above; only the input
              // is clamped. Below 16px on form controls is the autozoom
              // trigger across mobile WebKit.
              fontSize: `${Math.max(16, config.fontSize)}px`,
            }}
          />
          <button
            type="button"
            onClick={() => handleSend()}
            disabled={!connected || (!input.trim() && attachments.length === 0) || uploadingCount > 0}
            aria-label="Send message"
            title={connected ? 'Send (Enter)' : 'Disconnected'}
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded transition-all hover:bg-white/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-current disabled:cursor-not-allowed disabled:opacity-30"
            style={{
              color:
                (input.trim() || attachments.length > 0) && connected && uploadingCount === 0
                  ? colors.primary
                  : 'var(--construct-text-faint)',
              textShadow:
                (input.trim() || attachments.length > 0) && connected && uploadingCount === 0
                  ? colors.glow
                  : 'none',
            }}
          >
            <Send className="h-3.5 w-3.5" />
          </button>
        </div>

        {/* Drag-hover overlay — only visible while a file drag is over
            the composer. Click-through pointer-events-none so it doesn't
            steal focus from the textarea underneath. */}
        {dragHover && (
          <div
            className="pointer-events-none absolute inset-2 flex items-center justify-center rounded-md border-2 border-dashed text-xs"
            style={{
              borderColor: colors.primary,
              background: 'color-mix(in srgb, var(--construct-bg-base) 70%, transparent)',
              color: colors.primary,
              textShadow: colors.glow,
            }}
          >
            drop files to attach
          </div>
        )}
        <div className="mt-2 flex items-center gap-3">
          <span className="flex shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--construct-text-faint)' }}>
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: connected ? 'var(--construct-status-success)' : 'var(--construct-status-danger)' }}
            />
            {connected ? 'live' : 'offline'}
          </span>
          <span
            className="min-w-0 flex-1 truncate text-[10px]"
            style={{ color: 'var(--construct-text-faint)' }}
            title={pageContext}
          >
            {pageContext}
          </span>
        </div>

        <SlashCommandMenu
          anchorRef={composerRef}
          matches={slashMatches}
          selectedIndex={slashSelectedIndex}
          onPick={pickSlashCommand}
        />
      </div>
    </div>
  );
}

/* ── NewTabMenu ───────────────────────────────────── */

function NewTabMenu({
  anchorRef,
  onSelect,
  onClose,
}: {
  anchorRef: React.RefObject<HTMLElement | null>;
  onSelect: (type: TabType) => void;
  onClose: () => void;
}) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  // Compute position from the trigger button's bounding rect and render
  // via portal so the menu escapes the assistant panel's stacking context
  // — `position: absolute` inside the panel was being clipped by the
  // chat pane's `overflow-hidden`. Recomputed on scroll/resize so the
  // menu tracks if the page reflows underneath it. The anchor button is
  // small and the menu is short-lived, so a `getBoundingClientRect` per
  // event is cheap.
  useLayoutEffect(() => {
    const update = () => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) return;
      setPos({ top: rect.bottom + 4, left: rect.left });
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [anchorRef]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const target = e.target as Node;
      if (menuRef.current && menuRef.current.contains(target)) return;
      if (anchorRef.current && anchorRef.current.contains(target)) return;
      onClose();
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [anchorRef, onClose]);

  if (!pos) return null;

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-[200] rounded-[8px] border py-1 shadow-lg"
      style={{
        top: pos.top,
        left: pos.left,
        background: 'var(--construct-bg-panel-strong)',
        borderColor: 'var(--construct-border-strong)',
        minWidth: '10rem',
      }}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-white/5"
        style={{ color: 'var(--construct-text-secondary)' }}
        onClick={() => { onSelect('chat'); onClose(); }}
      >
        <MessageSquare className="h-3.5 w-3.5" />
        New Chat
      </button>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-white/5"
        style={{ color: 'var(--construct-text-secondary)' }}
        onClick={() => { onSelect('terminal'); onClose(); }}
      >
        <Terminal className="h-3.5 w-3.5" />
        New Terminal
      </button>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-white/5"
        style={{ color: 'var(--construct-text-secondary)' }}
        onClick={() => { onSelect('code'); onClose(); }}
      >
        <Code2 className="h-3.5 w-3.5" />
        New Code
      </button>
    </div>,
    document.body,
  );
}

/* ── AssistantPanel ─────────────────────────────── */

export default function AssistantPanel() {
  const location = useLocation();
  const { open, closeAssistant, pageContextOverride, placeholderOverride } = useV2Assistant();
  const { config, colors, updateConfig } = useAssistantConfig();
  const routeMeta = v2RouteMeta[location.pathname];
  const pageContext = pageContextOverride ?? `v2:${routeContext(location.pathname) || 'dashboard'}`;
  const placeholder = placeholderOverride ?? `Ask about ${routeMeta?.title?.toLowerCase() ?? 'this workspace'}.`;

  const [tabs, setTabs] = useState<AssistantTab[]>(() => [
    { id: 'chat-main', type: 'chat', title: 'Chat', sessionId: generateUUID() },
    { id: 'terminal-main', type: 'terminal', title: 'Terminal', sessionId: generateUUID() },
  ]);
  const [activeTabId, setActiveTabId] = useState('chat-main');
  const [showNewTabMenu, setShowNewTabMenu] = useState(false);
  const newTabBtnRef = useRef<HTMLButtonElement>(null);
  const [showConfig, setShowConfig] = useState(false);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') closeAssistant(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, closeAssistant]);

  const addTab = useCallback((type: TabType) => {
    const id = generateUUID();
    setTabs((prev) => {
      const count = prev.filter((t) => t.type === type).length;
      let title: string;
      if (type === 'chat') title = `Chat ${count + 1}`;
      else if (type === 'terminal') title = `Terminal ${count + 1}`;
      else title = 'Code';
      const newTab: AssistantTab = {
        id,
        type,
        title,
        sessionId: generateUUID(),
        codeSession: type === 'code' ? null : undefined,
      };
      return [...prev, newTab];
    });
    setActiveTabId(id);
  }, []);

  const updateTab = useCallback((tabId: string, patch: Partial<AssistantTab>) => {
    setTabs((prev) => prev.map((t) => (t.id === tabId ? { ...t, ...patch } : t)));
  }, []);

  const handleCodeSessionStart = useCallback(
    (tabId: string) => (session: CodeSession, _label: string, resolvedCwd: string) => {
      updateTab(tabId, {
        codeSession: session,
        title: `${toolLabel(session.toolKey)} · ${basename(resolvedCwd)}`,
      });
    },
    [updateTab],
  );

  const handleCodeSessionEnd = useCallback(
    (tabId: string) => () => {
      updateTab(tabId, { codeSession: null, title: 'Code' });
    },
    [updateTab],
  );

  const handleCodeDelegateToChat = useCallback(
    (tabId: string) => (pageCtx: string, title: string) => {
      // Convert the code tab in place into a chat tab with the Operator context.
      updateTab(tabId, {
        type: 'chat',
        title,
        sessionId: generateUUID(),
        codeSession: undefined,
        pageContextOverride: pageCtx,
      });
    },
    [updateTab],
  );

  const closeTab = useCallback((tabId: string) => {
    setTabs((prev) => {
      const remaining = prev.filter((t) => t.id !== tabId);
      if (remaining.length === 0) {
        // Always keep at least one tab
        const fallback: AssistantTab = { id: generateUUID(), type: 'chat', title: 'Chat', sessionId: generateUUID() };
        return [fallback];
      }
      return remaining;
    });
    setActiveTabId((prev) => {
      if (prev !== tabId) return prev;
      const idx = tabs.findIndex((t) => t.id === tabId);
      const remaining = tabs.filter((t) => t.id !== tabId);
      if (remaining.length === 0) return prev; // will be replaced by new tab
      return remaining[Math.min(idx, remaining.length - 1)]!.id;
    });
  }, [tabs]);

  const panelHeight = `${config.panelHeightPercent}vh`;

  return (
    <>
      {open && (
        <div
          className="absolute inset-0 z-[50]"
          style={{ background: 'rgba(0,0,0,0.25)' }}
          onClick={closeAssistant}
        />
      )}

      <div
        className="absolute inset-x-0 top-0 z-[60] flex min-w-0 flex-col overflow-hidden border-b border-l border-r"
        style={{
          height: open ? panelHeight : '0px',
          maxHeight: open ? '90vh' : '0px',
          opacity: open ? 1 : 0,
          transform: open ? 'translateY(0)' : 'translateY(-0.75rem)',
          transition: 'height 300ms ease-out, max-height 300ms ease-out, opacity 200ms ease-out, transform 300ms ease-out',
          borderColor: open ? 'var(--construct-border-strong)' : 'transparent',
          background: 'var(--construct-bg-base)',
          boxShadow: open ? 'var(--construct-shadow-overlay)' : 'none',
          pointerEvents: open ? 'auto' : 'none',
          borderRadius: '0 0 14px 14px',
        }}
      >
        {/* scanlines */}
        <div
          className="pointer-events-none absolute inset-0 z-[5]"
          style={{
            background: 'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(125,255,155,0.012) 2px, rgba(125,255,155,0.012) 4px)',
            mixBlendMode: 'overlay',
          }}
        />

        {/* tab bar — split into a horizontally-scrollable tab strip + a fixed
            right-side action cluster (settings/close) so the actions never get
            pushed off-screen on narrow viewports. The tab strip itself scrolls
            via overflow-x-auto with hidden scrollbar styling, and the new-tab
            "+" button stays inline with the tabs (it's part of the tab cluster
            conceptually, not a global action). */}
        <div
          className="relative z-20 flex items-center border-b"
          style={{ borderColor: 'var(--construct-border-soft)', background: 'var(--construct-bg-surface)' }}
        >
          <div
            className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto px-2 py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {tabs.map((tab) => {
              const isActive = activeTabId === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  className="group flex shrink-0 items-center gap-1.5 px-2.5 py-1.5 font-mono text-[11px] transition-colors"
                  onClick={() => setActiveTabId(tab.id)}
                  style={{
                    background: isActive ? colors.primary + '18' : 'transparent',
                    color: isActive ? colors.primary : 'var(--construct-text-muted)',
                    textShadow: isActive ? colors.glow : 'none',
                    borderBottom: isActive ? `2px solid ${colors.primary}` : '2px solid transparent',
                  }}
                >
                  {tab.type === 'terminal' ? (
                    <Terminal className="h-3 w-3" />
                  ) : tab.type === 'code' ? (
                    <Code2 className="h-3 w-3" />
                  ) : (
                    <MessageSquare className="h-3 w-3" />
                  )}
                  {tab.title}
                  {tabs.length > 1 && (
                    <span
                      className="ml-0.5 p-0.5 opacity-0 transition-opacity hover:bg-white/10 group-hover:opacity-100"
                      onClick={(e) => { e.stopPropagation(); closeTab(tab.id); }}
                      role="button"
                      tabIndex={-1}
                    >
                      <X className="h-2.5 w-2.5" />
                    </span>
                  )}
                </button>
              );
            })}

            <div className="shrink-0">
              <button
                ref={newTabBtnRef}
                type="button"
                className="flex items-center gap-0.5 p-1.5 transition-colors hover:bg-white/5"
                onClick={() => setShowNewTabMenu((prev) => !prev)}
                style={{ color: 'var(--construct-text-faint)' }}
                title="New tab"
              >
                <Plus className="h-3 w-3" />
                <ChevronDown className="h-2.5 w-2.5" />
              </button>
              {showNewTabMenu && (
                <NewTabMenu
                  anchorRef={newTabBtnRef}
                  onSelect={addTab}
                  onClose={() => setShowNewTabMenu(false)}
                />
              )}
            </div>
          </div>

          <div className="flex shrink-0 items-center px-1">
            <button
              type="button"
              className="p-1.5 transition-colors hover:bg-white/5"
              onClick={() => setShowConfig((prev) => !prev)}
              style={{ color: showConfig ? colors.primary : 'var(--construct-text-faint)' }}
              title="Settings"
            >
              <Settings className="h-3.5 w-3.5" />
            </button>

            <button
              type="button"
              className="p-1.5 transition-colors hover:bg-white/5"
              onClick={closeAssistant}
              style={{ color: 'var(--construct-text-faint)' }}
              title="Dismiss (Esc)"
            >
              <ChevronUp className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* config panel (inline) */}
        {showConfig && <ConfigPanel config={config} updateConfig={updateConfig} />}

        {/* pane content */}
        {open && (
          <div className="relative z-10 flex min-h-0 flex-1 flex-col">
            {/* Terminal tabs — all rendered, visibility toggled */}
            {tabs.filter((t) => t.type === 'terminal').map((tab) => (
              <XTerminal
                key={tab.id}
                sessionId={tab.sessionId}
                config={config}
                colors={colors}
                visible={activeTabId === tab.id}
              />
            ))}

            {/* Code tabs — all rendered, visibility toggled so xterm state persists. */}
            {tabs.filter((t) => t.type === 'code').map((tab) => (
              <CodeTab
                key={tab.id}
                tabId={tab.id}
                config={config}
                colors={colors}
                visible={activeTabId === tab.id}
                session={tab.codeSession ?? null}
                onSessionStart={handleCodeSessionStart(tab.id)}
                onSessionEnd={handleCodeSessionEnd(tab.id)}
                onDelegateToChat={handleCodeDelegateToChat(tab.id)}
              />
            ))}

            {/* Chat tabs — all rendered, visibility toggled so the
                WebSocket stream and in-flight typing/streaming state
                survive tab switches. Otherwise asking a question, swapping
                to a terminal/code tab, and switching back would unmount
                the pane mid-turn — losing every event between unmount
                and remount, so the user sees a blank pane until the next
                history fetch (i.e. the next tab swap). */}
            {tabs.filter((t) => t.type === 'chat').map((tab) => (
              <ChatPane
                key={tab.id}
                sessionId={tab.sessionId}
                pageContext={tab.pageContextOverride ?? pageContext}
                placeholder={placeholder}
                config={config}
                colors={colors}
                visible={activeTabId === tab.id}
                onAddTab={addTab}
                onCloseActiveTab={() => closeTab(tab.id)}
                onOpenNewTabMenu={() => setShowNewTabMenu(true)}
              />
            ))}
          </div>
        )}
      </div>

      <style>{`
        @keyframes construct-assistant-sweep {
          from { transform: translateX(-10%); }
          to   { transform: translateX(160%); }
        }
        @keyframes construct-cursor-blink-anim {
          0%, 49% { opacity: 1; }
          50%, 100% { opacity: 0; }
        }
        .construct-cursor-blink {
          animation: construct-cursor-blink-anim 1s step-end infinite;
        }
      `}</style>
    </>
  );
}
