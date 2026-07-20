import { useState, type ReactElement } from 'react';
import { Brain, Wrench, Zap, Bot, ChevronDown, ChevronRight, GitCommitHorizontal } from 'lucide-react';
import type { ActivityEvent } from './types';

const ICON_MAP: Record<ActivityEvent['kind'], ReactElement> = {
  thinking: <Brain className="h-3.5 w-3.5" />,
  tool_call: <Wrench className="h-3.5 w-3.5" />,
  tool_result: <Zap className="h-3.5 w-3.5" />,
  operator: <Bot className="h-3.5 w-3.5" />,
  code_changes: <GitCommitHorizontal className="h-3.5 w-3.5" />,
};

const COLOR_MAP: Record<ActivityEvent['kind'], string> = {
  thinking: 'var(--pc-accent)',
  tool_call: '#f59e0b',
  tool_result: '#10b981',
  operator: '#8b5cf6',
  code_changes: '#10b981',
};

interface ActivityIndicatorProps {
  event: ActivityEvent;
  isLatest: boolean;
  /** Use compact sizing for modal panels. */
  compact?: boolean;
}

export default function ActivityIndicator({ event, isLatest, compact }: ActivityIndicatorProps) {
  const [expanded, setExpanded] = useState(false);
  const color = COLOR_MAP[event.kind];
  const iconSize = compact ? 'w-5 h-5' : 'w-6 h-6';

  return (
    <div
      className="flex items-start gap-2.5 text-xs"
      style={{
        animation: isLatest ? 'fadeIn 0.3s ease-out' : undefined,
        opacity: isLatest ? 1 : 0.6,
      }}
    >
      <div
        className={`flex-shrink-0 ${iconSize} rounded-lg flex items-center justify-center mt-0.5`}
        style={{
          color,
          background: `color-mix(in srgb, ${color} 12%, transparent)`,
          animation: isLatest ? 'pulse-dot 2s ease-in-out infinite' : undefined,
        }}
      >
        {ICON_MAP[event.kind]}
      </div>

      <div className="flex-1 min-w-0">
        <button
          className="flex items-center gap-1.5 cursor-pointer hover:underline"
          style={{ color: 'var(--pc-text-secondary)', background: 'none', border: 'none', padding: 0, font: 'inherit' }}
          onClick={() => event.detail && setExpanded(!expanded)}
          aria-expanded={event.detail ? expanded : undefined}
        >
          {isLatest && (
            <span
              className="inline-block w-1.5 h-1.5 rounded-full flex-shrink-0"
              style={{
                background: color,
                animation: 'pulse-dot 1.5s ease-in-out infinite',
                boxShadow: `0 0 6px ${color}`,
              }}
            />
          )}
          <span className="truncate">{event.label}</span>
          {event.detail && (
            expanded
              ? <ChevronDown className="h-3 w-3 flex-shrink-0" style={{ color: 'var(--pc-text-muted)' }} />
              : <ChevronRight className="h-3 w-3 flex-shrink-0" style={{ color: 'var(--pc-text-muted)' }} />
          )}
        </button>
        {expanded && event.detail && (
          <pre
            className="mt-1.5 text-[11px] p-2 rounded-lg overflow-x-auto"
            style={{
              background: 'var(--pc-bg-surface)',
              color: 'var(--pc-text-muted)',
              maxHeight: compact ? '80px' : '120px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-all',
            }}
          >
            {event.detail}
          </pre>
        )}
      </div>
    </div>
  );
}
