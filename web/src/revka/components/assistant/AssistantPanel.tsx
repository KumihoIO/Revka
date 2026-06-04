import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronUp,
  Code2,
  Columns2,
  Copy,
  GitBranch,
  Loader2,
  ListPlus,
  MessageSquare,
  Paperclip,
  Plus,
  Rows2,
  Send,
  Settings,
  SplitSquareHorizontal,
  Square,
  Terminal,
  X,
} from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { generateUUID } from '@/lib/uuid';
import { useAgentChatSession } from '@/revka/hooks/useAgentChatSession';
import { useTheme } from '@/revka/hooks/useTheme';
import { useT, type Locale } from '@/revka/hooks/useT';
import { deleteSession, getSessionsWithArchiveState, renameSession } from '@/lib/api';
import { useV2Assistant } from './AssistantContext';
import { v2RouteMeta } from '../layout/revka-navigation';
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
import MarkdownMessage from './MarkdownMessage';
import SlashCommandMenu from './SlashCommandMenu';
import {
  matchCommands,
  parseInput,
  resolveCommand,
  type SlashCommandContext,
  type SlashThemeName,
} from './slashCommands';
import { copyToClipboard } from '@/revka/lib/clipboard';
import type { ActivityEvent, ChatMessage } from '@/components/chat/types';

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

const OPERATOR_MAIN_SESSION_ID = 'operator-main';
const ASSISTANT_TABS_STORAGE_KEY = 'revka_assistant_tabs_v1';
const ASSISTANT_ARCHIVED_SESSIONS_STORAGE_KEY = 'revka_assistant_archived_session_ids_v1';
const MAX_RESTORED_CHAT_TABS = 12;

interface PersistedAssistantTabs {
  tabs: AssistantTab[];
  activeTabId?: string;
}

function defaultAssistantTabs(): AssistantTab[] {
  return [
    { id: 'chat-main', type: 'chat', title: 'Chat', sessionId: OPERATOR_MAIN_SESSION_ID },
    { id: 'terminal-main', type: 'terminal', title: 'Terminal', sessionId: generateUUID() },
  ];
}

function fallbackChatTab(): AssistantTab {
  return { id: 'chat-main', type: 'chat', title: 'Chat', sessionId: generateUUID() };
}

function loadAssistantTabs(): PersistedAssistantTabs {
  try {
    const raw = localStorage.getItem(ASSISTANT_TABS_STORAGE_KEY);
    if (!raw) return { tabs: defaultAssistantTabs(), activeTabId: 'chat-main' };
    const parsed = JSON.parse(raw) as Partial<PersistedAssistantTabs>;
    const parsedTabs = Array.isArray(parsed.tabs)
      ? parsed.tabs.filter((tab): tab is AssistantTab =>
          !!tab
          && typeof tab.id === 'string'
          && typeof tab.title === 'string'
          && typeof tab.sessionId === 'string'
          && (tab.type === 'chat' || tab.type === 'terminal' || tab.type === 'code'),
        )
      : [];
    const activeTabId = parsedTabs.some((tab) => tab.id === parsed.activeTabId)
      ? parsed.activeTabId
      : parsedTabs[0]?.id;
    const chatTabs = parsedTabs.filter((tab) => tab.type === 'chat');
    const nonChatTabs = parsedTabs.filter((tab) => tab.type !== 'chat');
    const activeChat = activeTabId ? chatTabs.find((tab) => tab.id === activeTabId) : undefined;
    const boundedChatTabs = [
      ...(activeChat ? [activeChat] : []),
      ...chatTabs.filter((tab) => tab.id !== activeChat?.id),
    ].slice(0, MAX_RESTORED_CHAT_TABS);
    const tabs = [...boundedChatTabs, ...nonChatTabs];
    if (tabs.length === 0) return { tabs: defaultAssistantTabs(), activeTabId: 'chat-main' };
    return {
      tabs,
      activeTabId: tabs.some((tab) => tab.id === activeTabId) ? activeTabId : tabs[0]?.id,
    };
  } catch {
    return { tabs: defaultAssistantTabs(), activeTabId: 'chat-main' };
  }
}

function loadArchivedSessionIds(): Set<string> {
  try {
    const raw = localStorage.getItem(ASSISTANT_ARCHIVED_SESSIONS_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((id): id is string => typeof id === 'string' && id.length > 0));
  } catch {
    return new Set();
  }
}

function saveArchivedSessionIds(ids: Set<string>) {
  try {
    localStorage.setItem(ASSISTANT_ARCHIVED_SESSIONS_STORAGE_KEY, JSON.stringify([...ids]));
  } catch {
    // Private mode / quota failures should not break Operator chat.
  }
}

function rememberArchivedSessionId(sessionId: string) {
  const ids = loadArchivedSessionIds();
  ids.add(sessionId);
  saveArchivedSessionIds(ids);
}

function saveAssistantTabs(tabs: AssistantTab[], activeTabId: string | null) {
  try {
    const safeTabs = tabs.map((tab) => ({
      id: tab.id,
      type: tab.type,
      title: tab.title,
      sessionId: tab.sessionId,
      codeSession: tab.type === 'code' ? null : undefined,
      pageContextOverride: tab.pageContextOverride,
    }));
    localStorage.setItem(
      ASSISTANT_TABS_STORAGE_KEY,
      JSON.stringify({ tabs: safeTabs, activeTabId }),
    );
  } catch {
    // Private mode / quota failures should not break Operator chat.
  }
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
      style={{ borderColor: 'var(--revka-border-soft)', background: 'color-mix(in srgb, var(--revka-bg-surface) 95%, transparent)' }}
    >
      <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-xs">
        {/* Color Scheme */}
        <div>
          <label className="mb-1 block font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)', fontSize: '10px' }}>
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
                  color: config.colorScheme === key ? '#0c0c0c' : 'var(--revka-text-secondary)',
                  border: `1px solid ${config.colorScheme === key ? COLOR_SCHEMES[key].colors.primary : 'var(--revka-border-soft)'}`,
                }}
              >
                {COLOR_SCHEMES[key].label}
              </button>
            ))}
          </div>
        </div>

        {/* Font Size */}
        <div>
          <label className="mb-1 block font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)', fontSize: '10px' }}>
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
            style={{ color: 'var(--revka-signal-live)' }}
          />
        </div>

        {/* Cursor Blink */}
        <div className="flex items-center gap-2">
          <label className="font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)', fontSize: '10px' }}>
            Cursor Blink
          </label>
          <button
            type="button"
            onClick={() => updateConfig({ cursorBlink: !config.cursorBlink })}
            className="relative h-5 w-9 rounded-full transition-colors"
            style={{
              background: config.cursorBlink ? 'var(--revka-signal-live)' : 'var(--revka-border-strong)',
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
          <label className="mb-1 block font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)', fontSize: '10px' }}>
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
            style={{ color: 'var(--revka-signal-live)' }}
          />
        </div>

        {/* Panel Opacity — lets the page underneath show through, useful
            when chatting with Operator alongside a workflow editor or
            canvas. Capped at 0.5 so the chat stays readable; 1.0 is the
            default fully-opaque chrome. */}
        <div>
          <label className="mb-1 block font-semibold uppercase tracking-[0.1em]" style={{ color: 'var(--revka-text-faint)', fontSize: '10px' }}>
            Panel Opacity — {Math.round(config.panelOpacity * 100)}%
          </label>
          <input
            type="range"
            min={0.5}
            max={1.0}
            step={0.05}
            value={config.panelOpacity}
            onChange={(e) => updateConfig({ panelOpacity: Number(e.target.value) })}
            className="w-full accent-current"
            style={{ color: 'var(--revka-signal-live)' }}
          />
        </div>
      </div>
    </div>
  );
}

function messageRoleColor(role: ChatMessage['role'], colors: SchemeColors) {
  if (role === 'user') return colors.user;
  if (role === 'operator') return colors.secondary;
  return colors.primary;
}

function messageRoleGlow(role: ChatMessage['role'], colors: SchemeColors) {
  if (role === 'agent') return colors.glow;
  if (role === 'operator') return colors.glowSecondary;
  return 'none';
}

const ChatScrollback = memo(function ChatScrollback({
  messages,
  typing,
  activities,
  streamingContent,
  streamingThinking,
  copiedId,
  colors,
  fontSize,
  placeholder,
  copyMessage,
}: {
  messages: ChatMessage[];
  typing: boolean;
  activities: ActivityEvent[];
  streamingContent: string;
  streamingThinking: string;
  copiedId: string | null;
  colors: SchemeColors;
  fontSize: number;
  placeholder: string;
  copyMessage: (id: string, text: string) => void;
}) {
  if (messages.length === 0 && !typing) {
    return (
      <div className="flex h-full flex-col items-center justify-center text-center">
        <pre className="text-xs" style={{ color: 'var(--revka-text-faint)' }}>
{`┌──────────────────────────────┐
│  session ready · ask away    │
└──────────────────────────────┘`}
        </pre>
        <p className="mt-3 max-w-xs text-xs leading-5" style={{ color: 'var(--revka-text-muted)' }}>
          {placeholder}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {messages.map((msg) => {
        const prefix = msg.role === 'user' ? 'you' : msg.role === 'operator' ? 'sys' : 'op';
        const color = messageRoleColor(msg.role, colors);
        const glow = messageRoleGlow(msg.role, colors);
        const copied = copiedId === msg.id;
        const contentColor = msg.role === 'user' ? 'var(--revka-text-secondary)' : color;
        return (
          <div key={msg.id} className="group">
            {msg.activityLog && msg.activityLog.length > 0 && (
              <div className="mb-1 space-y-0.5">
                {msg.activityLog.map((evt) => (
                  <ActivityCard
                    key={evt.id}
                    event={evt}
                    accent={evt.kind === 'tool_result' ? 'var(--revka-status-success)' : colors.secondary}
                    fontSize={fontSize}
                  />
                ))}
              </div>
            )}
            <div className="flex min-w-0 items-start gap-1 break-words">
              <span className="shrink-0" style={{ color, textShadow: glow, fontWeight: 600 }}>{prefix} {'>'} </span>
              <div className="min-w-0 flex-1" style={{ color: contentColor, textShadow: glow }}>
                {msg.markdown && msg.role !== 'user' ? (
                  <MarkdownMessage content={msg.content} color={contentColor} textShadow={glow} />
                ) : (
                  <span className="whitespace-pre-wrap break-words">{msg.content}</span>
                )}
              </div>
            </div>
            <div className="mt-0.5 flex items-center justify-end gap-2 text-[10px]" style={{ color: 'var(--revka-text-faint)' }}>
              {msg.deliveryStatus === 'queued' && (
                <span
                  className="mr-auto inline-flex items-center rounded border px-1.5 py-0.5 uppercase tracking-[0.12em]"
                  style={{
                    borderColor: 'var(--revka-border-soft)',
                    color: 'var(--revka-text-faint)',
                  }}
                >
                  queued
                </span>
              )}
              {msg.deliveryStatus === 'sending' && (
                <span
                  className="mr-auto inline-flex items-center gap-1 rounded border px-1.5 py-0.5 uppercase tracking-[0.12em]"
                  style={{
                    borderColor: 'var(--revka-border-soft)',
                    color: colors.primary,
                    textShadow: colors.glow,
                  }}
                >
                  <Loader2 className="h-2.5 w-2.5 animate-spin" />
                  sending
                </span>
              )}
              <button
                type="button"
                onClick={() => copyMessage(msg.id, msg.content)}
                aria-label={copied ? 'Copied' : 'Copy message'}
                title={copied ? 'Copied' : 'Copy'}
                className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 opacity-50 transition-all hover:bg-white/5 hover:opacity-100 focus:opacity-100 focus:outline-none focus-visible:ring-1 focus-visible:ring-current group-hover:opacity-80"
                style={{ color: 'var(--revka-text-muted)' }}
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
                  ? 'var(--revka-status-success)'
                  : evt.kind === 'thinking'
                    ? 'var(--revka-text-faint)'
                    : colors.secondary
              }
              fontSize={fontSize}
            />
          ))}
        </div>
      )}

      {typing && (streamingContent || streamingThinking) && (
        <div className="flex min-w-0 items-start gap-1 break-words">
          <span className="shrink-0" style={{ color: colors.primary, textShadow: colors.glow, fontWeight: 600 }}>
            op {'>'}{' '}
          </span>
          <div className="min-w-0 flex-1">
            {streamingContent ? (
              <MarkdownMessage content={streamingContent} color={colors.primary} textShadow={colors.glow} />
            ) : (
              <span style={{ color: colors.primary, textShadow: colors.glow }}>…</span>
            )}
          </div>
        </div>
      )}

      {typing && !streamingContent && !streamingThinking && activities.length === 0 && (
        <div className="animate-pulse" style={{ color: colors.primary, textShadow: colors.glow }}>
          op {'>'} ▊
        </div>
      )}
    </div>
  );
});

/* ── ChatPane ─────────────────────────────────────── */

function ChatPane({
  tabId,
  sessionId,
  sessionName,
  pageContext,
  placeholder,
  config,
  colors,
  visible,
  onAddTab,
  onCloseActiveTab,
  onOpenNewTabMenu,
  onLiveStateChange,
}: {
  tabId: string;
  sessionId: string;
  sessionName: string;
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
  onLiveStateChange?: (tabId: string, live: boolean) => void;
}) {
  const { open } = useV2Assistant();
  const { setTheme } = useTheme();
  const { setLocale, t } = useT();
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
    queuedTurns,
    steerCurrentTurn,
    stopCurrentTurn,
    stopping,
    typing,
    uploadingCount,
  } = useAgentChatSession({
    sessionId,
    sessionName,
    draftKey: `revka-assistant:${sessionId}`,
    pageContext,
  });

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
  const [sendMode, setSendMode] = useState<'queue' | 'steer'>('queue');
  const [autoScroll, setAutoScroll] = useState(true);
  const autoScrollRef = useRef(true);

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

  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'auto') => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior,
    });
  }, []);

  const handleScroll = useCallback(() => {
    const node = scrollRef.current;
    if (!node) return;
    const nextAutoScroll = node.scrollHeight - node.scrollTop - node.clientHeight < 72;
    autoScrollRef.current = nextAutoScroll;
    setAutoScroll(nextAutoScroll);
  }, []);

  useLayoutEffect(() => {
    if (!visible) return;
    if (!autoScrollRef.current) return;
    const frame = requestAnimationFrame(() => {
      scrollToBottom('auto');
    });
    return () => cancelAnimationFrame(frame);
  }, [activities, messages.length, scrollToBottom, streamingContent, streamingThinking, typing, visible]);

  useEffect(() => {
    if (!open || !visible) return;
    autoScrollRef.current = true;
    setAutoScroll(true);
    const frame = requestAnimationFrame(() => scrollToBottom('auto'));
    return () => cancelAnimationFrame(frame);
  }, [open, scrollToBottom, visible]);

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

  const submitComposer = useCallback(() => {
    if (typing && sendMode === 'steer' && attachments.length === 0) {
      if (steerCurrentTurn()) return;
    }
    handleSend();
  }, [attachments.length, handleSend, sendMode, steerCurrentTurn, typing]);

  const hasLiveWork = typing || queuedTurns.length > 0 || stopping;

  useEffect(() => {
    onLiveStateChange?.(tabId, hasLiveWork);
    return () => onLiveStateChange?.(tabId, false);
  }, [hasLiveWork, onLiveStateChange, tabId]);

  return (
    <div
      className="min-h-0 flex-1 flex-col"
      style={{ display: visible ? 'flex' : 'none' }}
    >
      {typing && (
        <div className="h-[2px] overflow-hidden" style={{ background: 'var(--revka-bg-surface)' }}>
          <div
            className="h-full"
            style={{
              background: colors.primary,
              width: '40%',
              animation: 'revka-assistant-sweep 1.4s ease-in-out infinite alternate',
            }}
          />
        </div>
      )}

      {error && (
        <div
          className="border-b px-4 py-2 text-xs"
          style={{ borderColor: 'rgba(255,107,122,0.2)', background: 'rgba(255,107,122,0.06)', color: 'var(--revka-status-danger)' }}
        >
          <div className="flex items-center gap-2">
            <AlertCircle className="h-3 w-3 shrink-0" />
            <span>{error}</span>
          </div>
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="h-full overflow-y-auto overflow-x-hidden p-4 font-mono leading-6"
          style={{ fontSize: `${config.fontSize}px` }}
        >
          <ChatScrollback
            messages={messages}
            typing={typing}
            activities={activities}
            streamingContent={streamingContent}
            streamingThinking={streamingThinking}
            copiedId={copiedId}
            colors={colors}
            fontSize={config.fontSize}
            placeholder={placeholder}
            copyMessage={copyMessage}
          />
        </div>
        {!autoScroll && (
          <button
            type="button"
            onClick={() => {
              autoScrollRef.current = true;
              setAutoScroll(true);
              scrollToBottom('smooth');
            }}
            className="absolute bottom-3 right-4 z-10 rounded border px-2 py-1 font-mono text-[10px] uppercase shadow-lg transition-colors hover:bg-white/5 focus:outline-none focus-visible:ring-1 focus-visible:ring-current"
            style={{
              borderColor: 'var(--revka-border-soft)',
              background: 'var(--revka-bg-surface)',
              color: colors.primary,
              textShadow: colors.glow,
            }}
          >
            {t('agent.jump_to_latest')}
          </button>
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
        style={{ borderColor: 'var(--revka-border-soft)' }}
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
                  borderColor: 'var(--revka-border-soft)',
                  background: 'var(--revka-bg-surface)',
                  color: 'var(--revka-text-faint)',
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
            borderColor: dragHover ? colors.primary : 'var(--revka-border-soft)',
            background: dragHover ? 'color-mix(in srgb, var(--revka-bg-surface) 85%, transparent)' : 'transparent',
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
            style={{ color: 'var(--revka-text-muted)' }}
          >
            <Paperclip className="h-3.5 w-3.5" />
          </button>
          <span
            className="shrink-0 pb-[3px] font-mono text-sm font-semibold"
            style={{ color: colors.primary, textShadow: colors.glow }}
          >
            {'>'}<span className={config.cursorBlink ? 'revka-cursor-blink' : ''}>_</span>
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
                submitComposer();
              }
            }}
            onPaste={onPaste}
            placeholder={connected ? (typing ? (sendMode === 'steer' ? t('agent.placeholder_steer_next_step') : t('agent.placeholder_queue_next')) : t('agent.placeholder_message')) : t('agent.placeholder_connecting')}
            disabled={!connected}
            // `focus-visible:outline-none` overrides the global `:focus-visible`
            // ring set in index.css (2px accent outline) — without it Tailwind's
            // `outline-none` loses to the global selector and we end up with a
            // cyan halo around the composer on every keypress.
            className="min-h-[1.75rem] min-w-0 flex-1 resize-none bg-transparent font-mono outline-none focus:outline-none focus-visible:outline-none disabled:opacity-50"
            style={{
              color: 'var(--revka-text-primary)',
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
          {typing && (
            <div
              className="flex h-7 shrink-0 items-center overflow-hidden rounded border"
              style={{ borderColor: 'var(--revka-border-soft)' }}
              aria-label={t('agent.send_mode')}
            >
              <button
                type="button"
                onClick={() => setSendMode('queue')}
                title={t('agent.queue_after_current_response')}
                className="inline-flex h-full items-center gap-1 px-1.5 font-mono text-[10px] uppercase transition-colors hover:bg-white/5 focus:outline-none focus-visible:ring-1 focus-visible:ring-current"
                style={{
                  background: sendMode === 'queue' ? 'color-mix(in srgb, var(--revka-bg-surface) 88%, transparent)' : 'transparent',
                  color: sendMode === 'queue' ? colors.secondary : 'var(--revka-text-faint)',
                  textShadow: sendMode === 'queue' ? colors.glowSecondary : 'none',
                }}
              >
                <ListPlus className="h-3 w-3" />
                {t('agent.send_mode_queue')}
              </button>
              <button
                type="button"
                onClick={() => setSendMode('steer')}
                title={attachments.length > 0 ? t('agent.steer_text_only') : t('agent.steer_next_step')}
                disabled={attachments.length > 0 || uploadingCount > 0}
                className="inline-flex h-full items-center gap-1 border-l px-1.5 font-mono text-[10px] uppercase transition-colors hover:bg-white/5 focus:outline-none focus-visible:ring-1 focus-visible:ring-current disabled:cursor-not-allowed disabled:opacity-35"
                style={{
                  borderColor: 'var(--revka-border-soft)',
                  background: sendMode === 'steer' ? 'color-mix(in srgb, var(--revka-bg-surface) 88%, transparent)' : 'transparent',
                  color: sendMode === 'steer' ? colors.primary : 'var(--revka-text-faint)',
                  textShadow: sendMode === 'steer' ? colors.glow : 'none',
                }}
              >
                <GitBranch className="h-3 w-3" />
                {t('agent.send_mode_steer')}
              </button>
            </div>
          )}
          {typing && (
            <button
              type="button"
              onClick={stopCurrentTurn}
              disabled={!connected || stopping}
              aria-label={stopping ? 'Stopping Operator' : 'Stop Operator'}
              title={stopping ? 'Stopping…' : 'Stop current response'}
              className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded transition-all hover:bg-white/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-current disabled:cursor-not-allowed disabled:opacity-30"
              style={{
                color: stopping ? 'var(--revka-text-faint)' : 'var(--revka-status-danger)',
              }}
            >
              {stopping ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Square className="h-3.5 w-3.5" />}
            </button>
          )}
          <button
            type="button"
            onClick={submitComposer}
            disabled={!connected || (!input.trim() && attachments.length === 0) || uploadingCount > 0}
            aria-label={typing && sendMode === 'steer' && attachments.length === 0 ? t('agent.steer_next_step') : typing ? t('agent.queue_message') : t('agent.send_message')}
            title={
              !connected
                ? t('agent.disconnected')
                : typing
                  ? sendMode === 'steer' && attachments.length === 0
                    ? t('agent.steer_next_step')
                    : t('agent.queue_after_current_response')
                  : t('agent.send_enter')
            }
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded transition-all hover:bg-white/5 focus:outline-none focus-visible:ring-2 focus-visible:ring-current disabled:cursor-not-allowed disabled:opacity-30"
            style={{
              color:
                (input.trim() || attachments.length > 0) && connected && uploadingCount === 0 && !typing
                  ? colors.primary
                  : (input.trim() || attachments.length > 0) && connected && uploadingCount === 0 && typing
                    ? colors.secondary
                    : 'var(--revka-text-faint)',
              textShadow:
                (input.trim() || attachments.length > 0) && connected && uploadingCount === 0 && !typing
                  ? colors.glow
                  : (input.trim() || attachments.length > 0) && connected && uploadingCount === 0 && typing
                    ? colors.glowSecondary
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
              background: 'color-mix(in srgb, var(--revka-bg-base) 70%, transparent)',
              color: colors.primary,
              textShadow: colors.glow,
            }}
          >
            drop files to attach
          </div>
        )}
        <div className="mt-2 flex items-center gap-3">
          <span className="flex shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--revka-text-faint)' }}>
            <span
              className="inline-block h-1.5 w-1.5 rounded-full"
              style={{ background: connected ? 'var(--revka-status-success)' : 'var(--revka-status-danger)' }}
            />
            {connected ? 'live' : 'offline'}
          </span>
          {/* In-flight notice — sits between the live/offline indicator
              and the pageContext crumb. Clarifies why the send button is
              disabled while the previous turn is still streaming. */}
          {typing && (
            <span
              className="flex shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
              style={{ color: colors.primary, textShadow: colors.glow }}
            >
              <Loader2 className="h-3 w-3 animate-spin" />
              Operator is responding…
            </span>
          )}
          {queuedTurns.length > 0 && (
            <span
              className="flex shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
              style={{ color: colors.secondary, textShadow: colors.glowSecondary }}
            >
              {queuedTurns.length} queued
            </span>
          )}
          <span
            className="min-w-0 flex-1 truncate text-[10px]"
            style={{ color: 'var(--revka-text-faint)' }}
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
        background: 'var(--revka-bg-panel-strong)',
        borderColor: 'var(--revka-border-strong)',
        minWidth: '10rem',
      }}
    >
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-white/5"
        style={{ color: 'var(--revka-text-secondary)' }}
        onClick={() => { onSelect('chat'); onClose(); }}
      >
        <MessageSquare className="h-3.5 w-3.5" />
        New Chat
      </button>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-white/5"
        style={{ color: 'var(--revka-text-secondary)' }}
        onClick={() => { onSelect('terminal'); onClose(); }}
      >
        <Terminal className="h-3.5 w-3.5" />
        New Terminal
      </button>
      <button
        type="button"
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-white/5"
        style={{ color: 'var(--revka-text-secondary)' }}
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

  const initialTabsRef = useRef<PersistedAssistantTabs | null>(null);
  if (initialTabsRef.current === null) {
    initialTabsRef.current = loadAssistantTabs();
  }
  const [tabs, setTabs] = useState<AssistantTab[]>(() => initialTabsRef.current!.tabs);
  const [activeTabId, setActiveTabId] = useState(() => initialTabsRef.current!.activeTabId ?? 'chat-main');
  const [showNewTabMenu, setShowNewTabMenu] = useState(false);
  const newTabBtnRef = useRef<HTMLButtonElement>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [hasOpened, setHasOpened] = useState(open);
  // Split-pane state — when splitTabId is non-null and BOTH the active
  // tab and the split tab are chats, the panel renders them side-by-side
  // (vertical) or stacked (horizontal). Code/terminal tabs ignore the
  // split and render normally; switching to one auto-closes the split.
  const [splitTabId, setSplitTabId] = useState<string | null>(null);
  const [splitDirection, setSplitDirection] = useState<'horizontal' | 'vertical'>('vertical');
  const [liveChatTabIds, setLiveChatTabIds] = useState<Set<string>>(() => new Set());
  // Inline tab rename: double-click a chat tab to edit, Enter/blur saves, Escape cancels.
  const [editingTabId, setEditingTabId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');

  useEffect(() => {
    saveAssistantTabs(tabs, activeTabId);
  }, [tabs, activeTabId]);

  useEffect(() => {
    if (tabs.length > 0 && tabs.some((tab) => tab.id === activeTabId)) return;
    setActiveTabId(tabs[0]?.id ?? 'chat-main');
  }, [tabs, activeTabId]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const { sessions, archivedSessionIds } = await getSessionsWithArchiveState();
        if (cancelled) return;
        const archivedSessionIdsSet = new Set([
          ...archivedSessionIds,
          ...loadArchivedSessionIds(),
        ]);
        const dashboardSessions = sessions
          .filter((session) => session.channel === 'dashboard' || session.channel === 'gateway')
          .filter((session) => !archivedSessionIdsSet.has(session.id))
          .filter((session) => session.message_count > 0)
          .sort((a, b) => new Date(b.last_activity).getTime() - new Date(a.last_activity).getTime())
          .slice(0, MAX_RESTORED_CHAT_TABS);
        const sessionIdsWithMessages = new Set(dashboardSessions.map((session) => session.id));

        setTabs((prev) => {
          const activeTabs = prev.filter(
            (tab) =>
              tab.type !== 'chat'
              || (
                !archivedSessionIdsSet.has(tab.sessionId)
                && (
                  tab.id === activeTabId
                  || tab.sessionId === OPERATOR_MAIN_SESSION_ID
                  || sessionIdsWithMessages.has(tab.sessionId)
                )
              ),
          );
          const seenSessionIds = new Set(
            activeTabs.filter((tab) => tab.type === 'chat').map((tab) => tab.sessionId),
          );
          const restored = dashboardSessions
            .filter((session) => !seenSessionIds.has(session.id))
            .map((session, index): AssistantTab => ({
              id: `chat-${session.id}`,
              type: 'chat',
              title: session.name || (index === 0 ? 'Chat' : `Chat ${index + 1}`),
              sessionId: session.id,
            }));
          const next = restored.length > 0 ? [...activeTabs, ...restored] : activeTabs;
          return next.length > 0 ? next : [fallbackChatTab()];
        });
      } catch {
        // Session continuity is best-effort; the active tab still connects by
        // stable session id and loads its own transcript.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') closeAssistant(); };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, closeAssistant]);

  useEffect(() => {
    if (open) setHasOpened(true);
  }, [open]);

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

  const commitRename = useCallback(async (tab: AssistantTab) => {
    const trimmed = editingTitle.trim();
    setEditingTabId(null);
    // No-op if unchanged or empty (treat empty as cancel).
    if (!trimmed || trimmed === tab.title) {
      setEditingTitle('');
      return;
    }
    // Optimistic local update.
    const previousTitle = tab.title;
    updateTab(tab.id, { title: trimmed });
    setEditingTitle('');
    // Persist to backend only for chat tabs with a real session id. The
    // gateway handler accepts the bare session id and prefixes `gw_` itself.
    if (tab.type === 'chat' && tab.sessionId) {
      try {
        await renameSession(tab.sessionId, trimmed);
      } catch (err) {
        // Revert local title on failure so the UI doesn't lie about persistence.
        updateTab(tab.id, { title: previousTitle });
        console.error('Rename failed:', err);
      }
    }
  }, [editingTitle, updateTab]);

  const cancelRename = useCallback(() => {
    setEditingTabId(null);
    setEditingTitle('');
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
    const closing = tabs.find((t) => t.id === tabId);
    if (closing?.type === 'chat') {
      rememberArchivedSessionId(closing.sessionId);
      void deleteSession(closing.sessionId).catch(() => {
        // The tab is intentionally closed locally even if the persisted
        // transcript was already gone or the gateway is temporarily offline.
      });
    }
    setTabs((prev) => {
      const remaining = prev.filter((t) => t.id !== tabId);
      if (remaining.length === 0) {
        return [fallbackChatTab()];
      }
      return remaining;
    });
    setActiveTabId((prev) => {
      if (prev !== tabId) return prev;
      const idx = tabs.findIndex((t) => t.id === tabId);
      const remaining = tabs.filter((t) => t.id !== tabId);
      if (remaining.length === 0) return 'chat-main';
      return remaining[Math.min(idx, remaining.length - 1)]!.id;
    });
    // Closing either side of an active split clears the split.
    setSplitTabId((prev) => (prev === tabId ? null : prev));
    setLiveChatTabIds((prev) => {
      if (!prev.has(tabId)) return prev;
      const next = new Set(prev);
      next.delete(tabId);
      return next;
    });
  }, [tabs]);

  // Split: spawn a fresh chat tab paired with the active tab. If a split
  // is already active, the button just toggles direction (more useful
  // than re-spawning yet another tab).
  const splitChat = useCallback(() => {
    if (splitTabId) {
      setSplitDirection((d) => (d === 'vertical' ? 'horizontal' : 'vertical'));
      return;
    }
    const id = generateUUID();
    setTabs((prev) => {
      const count = prev.filter((t) => t.type === 'chat').length;
      const newTab: AssistantTab = {
        id,
        type: 'chat',
        title: `Chat ${count + 1}`,
        sessionId: generateUUID(),
      };
      return [...prev, newTab];
    });
    setSplitTabId(id);
  }, [splitTabId]);

  const closeSplit = useCallback(() => {
    setSplitTabId(null);
  }, []);

  const setChatTabLive = useCallback((tabId: string, live: boolean) => {
    setLiveChatTabIds((prev) => {
      if (prev.has(tabId) === live) return prev;
      const next = new Set(prev);
      if (live) next.add(tabId);
      else next.delete(tabId);
      return next;
    });
  }, []);

  // Split is only meaningful when the active tab is a chat. Switching
  // to a terminal/code tab implicitly hides the split (we keep the
  // splitTabId so it restores when the user comes back), but the active
  // chat must also exist as a chat for the split to render.
  const activeTab = tabs.find((t) => t.id === activeTabId) ?? null;
  const splitTab = splitTabId ? (tabs.find((t) => t.id === splitTabId) ?? null) : null;
  const isSplitVisible =
    activeTab?.type === 'chat' &&
    splitTab?.type === 'chat' &&
    activeTab.id !== splitTab.id;

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
          // Multiply the open/closed opacity transition by the
          // user-configured panelOpacity so the see-through setting
          // applies once the panel has finished animating in.
          opacity: open ? config.panelOpacity : 0,
          transform: open ? 'translateY(0)' : 'translateY(-0.75rem)',
          transition: 'height 300ms ease-out, max-height 300ms ease-out, opacity 200ms ease-out, transform 300ms ease-out',
          borderColor: open ? 'var(--revka-border-strong)' : 'transparent',
          background: 'var(--revka-bg-base)',
          boxShadow: open ? 'var(--revka-shadow-overlay)' : 'none',
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
          style={{ borderColor: 'var(--revka-border-soft)', background: 'var(--revka-bg-surface)' }}
        >
          <div
            className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto px-2 py-1 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {tabs.map((tab) => {
              const isActive = activeTabId === tab.id;
              const isEditing = editingTabId === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  className="group flex shrink-0 items-center gap-1.5 px-2.5 py-1.5 font-mono text-[11px] transition-colors"
                  onClick={() => setActiveTabId(tab.id)}
                  onDoubleClick={(e) => {
                    if (tab.type !== 'chat') return;
                    e.stopPropagation();
                    setEditingTabId(tab.id);
                    setEditingTitle(tab.title);
                  }}
                  title={tab.type === 'chat' ? 'Double-click to rename' : undefined}
                  style={{
                    background: isActive ? colors.primary + '18' : 'transparent',
                    color: isActive ? colors.primary : 'var(--revka-text-muted)',
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
                  {isEditing ? (
                    <input
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      onBlur={() => commitRename(tab)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') {
                          e.preventDefault();
                          commitRename(tab);
                        } else if (e.key === 'Escape') {
                          e.preventDefault();
                          cancelRename();
                        }
                      }}
                      autoFocus
                      maxLength={64}
                      style={{
                        background: 'transparent',
                        border: 'none',
                        outline: 'none',
                        color: 'inherit',
                        font: 'inherit',
                        width: 'min(180px, 100%)',
                        padding: 0,
                      }}
                    />
                  ) : (
                    <>{tab.title}</>
                  )}
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
                style={{ color: 'var(--revka-text-faint)' }}
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
            {/* Split chat button — only shown when the active tab is a
                chat. First click splits the active chat with a fresh
                chat tab; subsequent clicks toggle the split direction
                (vertical ↔ horizontal). The X next to it un-splits. */}
            {activeTab?.type === 'chat' && (
              <>
                <button
                  type="button"
                  className="p-1.5 transition-colors hover:bg-white/5"
                  onClick={splitChat}
                  style={{
                    color: isSplitVisible ? colors.primary : 'var(--revka-text-faint)',
                  }}
                  title={
                    isSplitVisible
                      ? `Switch split: ${splitDirection === 'vertical' ? 'side-by-side' : 'stacked'} → ${splitDirection === 'vertical' ? 'stacked' : 'side-by-side'}`
                      : 'Split chat (new chat tab side-by-side)'
                  }
                >
                  {isSplitVisible
                    ? splitDirection === 'vertical'
                      ? <Columns2 className="h-3.5 w-3.5" />
                      : <Rows2 className="h-3.5 w-3.5" />
                    : <SplitSquareHorizontal className="h-3.5 w-3.5" />}
                </button>
                {isSplitVisible && (
                  <button
                    type="button"
                    className="p-1.5 transition-colors hover:bg-white/5"
                    onClick={closeSplit}
                    style={{ color: 'var(--revka-text-faint)' }}
                    title="Close split"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                )}
              </>
            )}

            <button
              type="button"
              className="p-1.5 transition-colors hover:bg-white/5"
              onClick={() => setShowConfig((prev) => !prev)}
              style={{ color: showConfig ? colors.primary : 'var(--revka-text-faint)' }}
              title="Settings"
            >
              <Settings className="h-3.5 w-3.5" />
            </button>

            <button
              type="button"
              className="p-1.5 transition-colors hover:bg-white/5"
              onClick={closeAssistant}
              style={{ color: 'var(--revka-text-faint)' }}
              title="Dismiss (Esc)"
            >
              <ChevronUp className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* config panel (inline) */}
        {showConfig && <ConfigPanel config={config} updateConfig={updateConfig} />}

        {/* pane content */}
        {hasOpened && (
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

            {/* Chat tabs are mounted only when visible or doing live work.
                That preserves in-flight streams across tab switches without
                keeping every restored idle chat tab connected in the
                background. When split is active, the active and split chats
                are wrapped in a flex container with the configured direction
                (row = side-by-side, column = stacked). */}
            {isSplitVisible ? (
              <div
                className="flex min-h-0 flex-1"
                style={{
                  flexDirection: splitDirection === 'vertical' ? 'row' : 'column',
                }}
              >
                {tabs
                  .filter((t) => t.type === 'chat')
                  .map((tab) => {
                    const isInSplit = tab.id === activeTabId || tab.id === splitTabId;
                    const shouldMount = (open && isInSplit) || liveChatTabIds.has(tab.id);
                    if (!shouldMount) return null;
                    return (
                      <div
                        key={tab.id}
                        className="flex min-h-0 min-w-0"
                        style={{
                          flex: isInSplit ? '1 1 50%' : '0 0 0',
                          // Splitter divider between the two visible
                          // panes (only on the second one in the flow).
                          borderLeft:
                            isInSplit && splitDirection === 'vertical' && tab.id === splitTabId
                              ? '1px solid var(--revka-border-soft)'
                              : undefined,
                          borderTop:
                            isInSplit && splitDirection === 'horizontal' && tab.id === splitTabId
                              ? '1px solid var(--revka-border-soft)'
                              : undefined,
                        }}
                      >
                        <ChatPane
                          tabId={tab.id}
                          sessionId={tab.sessionId}
                          sessionName={tab.title}
                          pageContext={tab.pageContextOverride ?? pageContext}
                          placeholder={placeholder}
                          config={config}
                          colors={colors}
                          visible={open && isInSplit}
                          onAddTab={addTab}
                          onCloseActiveTab={() => closeTab(tab.id)}
                          onOpenNewTabMenu={() => setShowNewTabMenu(true)}
                          onLiveStateChange={setChatTabLive}
                        />
                      </div>
                    );
                  })}
              </div>
            ) : (
              tabs.filter((t) => t.type === 'chat').map((tab) => {
                const isActiveChat = activeTabId === tab.id;
                const shouldMount = (open && isActiveChat) || liveChatTabIds.has(tab.id);
                if (!shouldMount) return null;
                return (
                  <ChatPane
                    key={tab.id}
                    tabId={tab.id}
                    sessionId={tab.sessionId}
                    sessionName={tab.title}
                    pageContext={tab.pageContextOverride ?? pageContext}
                    placeholder={placeholder}
                    config={config}
                    colors={colors}
                    visible={open && isActiveChat}
                    onAddTab={addTab}
                    onCloseActiveTab={() => closeTab(tab.id)}
                    onOpenNewTabMenu={() => setShowNewTabMenu(true)}
                    onLiveStateChange={setChatTabLive}
                  />
                );
              })
            )}
          </div>
        )}
      </div>

      <style>{`
        @keyframes revka-assistant-sweep {
          from { transform: translateX(-10%); }
          to   { transform: translateX(160%); }
        }
        @keyframes revka-cursor-blink-anim {
          0%, 49% { opacity: 1; }
          50%, 100% { opacity: 0; }
        }
        .revka-cursor-blink {
          animation: revka-cursor-blink-anim 1s step-end infinite;
        }
      `}</style>
    </>
  );
}
