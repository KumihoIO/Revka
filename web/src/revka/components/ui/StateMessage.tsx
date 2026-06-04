import type { ReactNode } from 'react';
import { AlertTriangle, Inbox, LoaderCircle } from 'lucide-react';

export default function StateMessage({
  tone = 'empty',
  title,
  description,
  compact = false,
  action,
}: {
  tone?: 'loading' | 'empty' | 'error';
  title: string;
  description?: string;
  compact?: boolean;
  action?: ReactNode;
}) {
  const config = (() => {
    switch (tone) {
      case 'loading':
        return {
          icon: LoaderCircle,
          iconClassName: 'animate-spin',
          color: 'var(--revka-signal-network)',
          borderColor: 'color-mix(in srgb, var(--revka-signal-network) 22%, var(--revka-border-soft))',
          background: 'color-mix(in srgb, var(--revka-signal-network-soft) 38%, transparent)',
        };
      case 'error':
        return {
          icon: AlertTriangle,
          iconClassName: '',
          color: 'var(--revka-status-danger)',
          borderColor: 'color-mix(in srgb, var(--revka-status-danger) 24%, var(--revka-border-soft))',
          background: 'color-mix(in srgb, var(--revka-status-danger) 8%, transparent)',
        };
      default:
        return {
          icon: Inbox,
          iconClassName: '',
          color: 'var(--revka-signal-selected)',
          borderColor: 'color-mix(in srgb, var(--revka-signal-selected) 22%, var(--revka-border-soft))',
          background: 'linear-gradient(135deg, color-mix(in srgb, var(--revka-signal-selected) 5%, transparent), transparent 58%), color-mix(in srgb, var(--revka-bg-panel-strong) 92%, transparent)',
        };
    }
  })();

  const Icon = config.icon;

  return (
    <div
      className={`rounded-[14px] border ${compact ? 'p-3' : 'p-4'}`}
      style={{
        borderColor: config.borderColor,
        background: config.background,
      }}
    >
      <div className="flex items-start gap-3">
        <span
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-[12px] border"
          style={{
            borderColor: 'color-mix(in srgb, currentColor 18%, transparent)',
            color: config.color,
            background: 'color-mix(in srgb, currentColor 8%, transparent)',
          }}
        >
          <Icon className={`h-4 w-4 ${config.iconClassName}`.trim()} />
        </span>
        <div className="min-w-0">
          <div className="text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
            {title}
          </div>
          {description ? (
            <p className="mt-1 text-sm leading-6" style={{ color: 'var(--revka-text-secondary)' }}>
              {description}
            </p>
          ) : null}
          {action ? <div className="mt-3">{action}</div> : null}
        </div>
      </div>
    </div>
  );
}
