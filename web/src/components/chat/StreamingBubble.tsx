import { Bot } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTheme } from '@/revka/hooks/useTheme';

interface StreamingBubbleProps {
  content: string;
  thinking?: string;
  /** Use full markdown rendering. */
  richMarkdown?: boolean;
  compact?: boolean;
}

/** Live-streaming agent response bubble with optional thinking disclosure. */
export default function StreamingBubble({ content, thinking, richMarkdown, compact }: StreamingBubbleProps) {
  const { getSkinAsset } = useTheme();
  const operatorAvatar = getSkinAsset('operatorAvatar');
  const avatarSize = compact ? 'w-7 h-7 rounded-xl' : 'w-9 h-9 rounded-2xl';
  const iconSize = compact ? 'h-3.5 w-3.5' : 'h-4 w-4';
  const bubbleRadius = compact ? 'rounded-xl' : 'rounded-2xl';
  const bubblePadding = compact ? 'px-3 py-2' : 'px-4 py-3';
  const maxW = compact ? 'max-w-[85%]' : 'max-w-[75%]';

  return (
    <div className={`flex items-start gap-${compact ? '2.5' : '3'} animate-fade-in`}>
      <div
        className={`flex-shrink-0 ${avatarSize} flex items-center justify-center overflow-hidden border`}
        style={{ background: 'var(--pc-bg-elevated)', borderColor: 'var(--pc-border)' }}
      >
        {operatorAvatar ? (
          <img src={operatorAvatar} alt="" className="h-full w-full object-cover" draggable={false} />
        ) : (
          <Bot className={iconSize} style={{ color: 'var(--pc-accent)' }} />
        )}
      </div>
      <div
        className={`${bubbleRadius} ${bubblePadding} border ${maxW}`}
        style={{ background: 'var(--pc-bg-elevated)', borderColor: 'var(--pc-border)', color: 'var(--pc-text-primary)' }}
      >
        {thinking && (
          <details className="mb-2" open={!content}>
            <summary className="text-xs cursor-pointer select-none" style={{ color: 'var(--pc-text-muted)' }}>
              Thinking{!content && '...'}
            </summary>
            <pre
              className="text-xs mt-1 whitespace-pre-wrap break-words leading-relaxed overflow-auto max-h-60 p-2 rounded-lg"
              style={{ color: 'var(--pc-text-muted)', background: 'var(--pc-bg-surface)' }}
            >
              {thinking}
            </pre>
          </details>
        )}
        {content && (
          richMarkdown ? (
            <div className="text-sm break-words leading-relaxed chat-markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            </div>
          ) : (
            <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">{content}</p>
          )
        )}
      </div>
    </div>
  );
}
