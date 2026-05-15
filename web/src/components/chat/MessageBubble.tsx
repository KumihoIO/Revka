import { useState, useCallback } from 'react';
import { Bot, User, Copy, Check } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatMessage } from './types';
import { operatorPhaseColor } from './chat-utils';
import { ThinkingTrace, OperatorTrace } from './TraceDisclosure';
import { useTheme } from '@/construct/hooks/useTheme';

// ---------------------------------------------------------------------------
// Operator badge — compact inline status for orchestration events
// ---------------------------------------------------------------------------

function OperatorBadge({ msg }: { msg: ChatMessage }) {
  return (
    <div className="animate-fade-in mx-auto w-fit max-w-[85%]">
      <div
        className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs"
        style={{
          background: 'var(--pc-bg-elevated)',
          border: `1px solid ${operatorPhaseColor(msg.operatorPhase ?? 'working')}33`,
          color: operatorPhaseColor(msg.operatorPhase ?? 'working'),
        }}
      >
        <span>{msg.activityLog ? `${msg.activityLog.length} operator steps` : msg.content}</span>
      </div>
      {msg.activityLog && msg.activityLog.length > 0 && (
        <details className="mt-1 px-3 text-xs" style={{ color: 'var(--pc-text-muted)' }}>
          <summary className="cursor-pointer select-none">Show activity log</summary>
          <div className="mt-1 space-y-0.5 rounded-lg p-2" style={{ background: 'var(--pc-bg-elevated)' }}>
            {msg.activityLog.map((evt) => (
              <div key={evt.id} className="py-0.5">{evt.label}</div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Simple code-block-aware renderer for compact (ChatPanel) mode
// ---------------------------------------------------------------------------

function renderPlainContent(text: string) {
  const parts = text.split(/(```[\s\S]*?```)/g);
  return parts.map((part, i) => {
    if (part.startsWith('```') && part.endsWith('```')) {
      const inner = part.slice(3, -3);
      const newline = inner.indexOf('\n');
      const code = newline >= 0 ? inner.slice(newline + 1) : inner;
      return (
        <pre
          key={i}
          className="text-xs my-2 p-2.5 rounded-lg overflow-x-auto"
          style={{ background: 'var(--pc-bg-surface)', color: 'var(--pc-text-secondary)' }}
        >
          <code>{code}</code>
        </pre>
      );
    }
    const inlineParts = part.split(/(`[^`]+`)/g);
    return (
      <span key={i}>
        {inlineParts.map((seg, j) => {
          if (seg.startsWith('`') && seg.endsWith('`')) {
            return (
              <code
                key={j}
                className="text-xs px-1 py-0.5 rounded"
                style={{ background: 'var(--pc-bg-surface)', color: 'var(--pc-accent-light)' }}
              >
                {seg.slice(1, -1)}
              </code>
            );
          }
          return <span key={j}>{seg}</span>;
        })}
      </span>
    );
  });
}

// ---------------------------------------------------------------------------
// Copy helper
// ---------------------------------------------------------------------------

function fallbackCopy(text: string): boolean {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  try {
    document.execCommand('copy');
    return true;
  } catch {
    return false;
  } finally {
    document.body.removeChild(textarea);
  }
}

// ---------------------------------------------------------------------------
// MessageBubble — renders a single chat message (user, agent, or operator)
// ---------------------------------------------------------------------------

interface MessageBubbleProps {
  msg: ChatMessage;
  /** Show full markdown rendering (AgentChat) vs simple code blocks (ChatPanel). */
  richMarkdown?: boolean;
  /** Show copy button on hover. */
  copyable?: boolean;
  /** Compact sizing for modal panels. */
  compact?: boolean;
  /** Animation delay for staggered entrance. */
  animationDelay?: number;
}

export default function MessageBubble({ msg, richMarkdown = false, copyable = false, compact = false, animationDelay }: MessageBubbleProps) {
  const { getSkinAsset } = useTheme();
  const operatorAvatar = getSkinAsset('operatorAvatar');
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    const onSuccess = () => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(msg.content).then(onSuccess).catch(() => {
        fallbackCopy(msg.content) && onSuccess();
      });
    } else {
      fallbackCopy(msg.content) && onSuccess();
    }
  }, [msg.content]);

  if (msg.role === 'operator') {
    return <OperatorBadge msg={msg} />;
  }

  const isUser = msg.role === 'user';
  const avatarSize = compact ? 'w-7 h-7 rounded-xl' : 'w-9 h-9 rounded-2xl';
  const iconSize = compact ? 'h-3.5 w-3.5' : 'h-4 w-4';
  const bubbleRadius = compact ? 'rounded-xl' : 'rounded-2xl';
  const bubblePadding = compact ? 'px-3 py-2' : 'px-4 py-3';
  const maxW = compact ? 'max-w-[85%]' : 'max-w-[75%]';

  return (
    <div
      className={`group flex items-start gap-${compact ? '2.5' : '3'} ${
        isUser ? 'flex-row-reverse' : ''
      } ${!compact ? (isUser ? 'animate-slide-in-right' : 'animate-slide-in-left') : ''}`}
      style={animationDelay ? { animationDelay: `${animationDelay}ms` } : undefined}
    >
      {/* Avatar */}
      <div
        className={`flex-shrink-0 ${avatarSize} flex items-center justify-center overflow-hidden border`}
        style={{
          background: isUser ? 'var(--pc-accent)' : 'var(--pc-bg-elevated)',
          borderColor: isUser ? 'var(--pc-accent)' : 'var(--pc-border)',
        }}
      >
        {isUser
          ? <User className={`${iconSize} text-white`} />
          : operatorAvatar
            ? <img src={operatorAvatar} alt="" className="h-full w-full object-cover" draggable={false} />
            : <Bot className={iconSize} style={{ color: 'var(--pc-accent)' }} />
        }
      </div>

      {/* Bubble */}
      <div className={`relative ${maxW}`}>
        <div
          className={`${bubbleRadius} ${bubblePadding} border`}
          style={
            isUser
              ? { background: 'var(--pc-accent-glow)', borderColor: 'var(--pc-accent-dim)', color: 'var(--pc-text-primary)' }
              : { background: 'var(--pc-bg-elevated)', borderColor: 'var(--pc-border)', color: 'var(--pc-text-primary)' }
          }
        >
          {/* Thinking trace */}
          {msg.thinking && <ThinkingTrace thinking={msg.thinking} />}

          {/* Content */}
          {msg.markdown && richMarkdown ? (
            <div className="text-sm break-words leading-relaxed chat-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
            </div>
          ) : msg.role === 'agent' ? (
            <div className="text-sm leading-relaxed whitespace-pre-wrap break-words">
              {renderPlainContent(msg.content)}
            </div>
          ) : (
            <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">{msg.content}</p>
          )}

          {/* Operator activity trace */}
          {msg.activityLog && msg.activityLog.length > 0 && (
            <OperatorTrace events={msg.activityLog} />
          )}

          {/* Timestamp */}
          <p
            className="text-[10px] mt-1.5"
            style={{ color: isUser ? 'var(--pc-accent-light)' : 'var(--pc-text-faint)' }}
          >
            {msg.timestamp.toLocaleTimeString()}
          </p>
        </div>

        {/* Copy button */}
        {copyable && !isUser && (
          <button
            onClick={handleCopy}
            aria-label="Copy message"
            className={`absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-all p-1.5 ${bubbleRadius}`}
            style={{
              background: 'var(--pc-bg-elevated)',
              border: '1px solid var(--pc-border)',
              color: 'var(--pc-text-muted)',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--pc-text-primary)'; e.currentTarget.style.borderColor = 'var(--pc-accent-dim)'; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--pc-text-muted)'; e.currentTarget.style.borderColor = 'var(--pc-border)'; }}
          >
            {copied
              ? <Check className="h-3 w-3" style={{ color: '#34d399' }} />
              : <Copy className="h-3 w-3" />
            }
          </button>
        )}
      </div>
    </div>
  );
}
