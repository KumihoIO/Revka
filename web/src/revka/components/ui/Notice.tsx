import { CheckCircle2, AlertTriangle, Info } from 'lucide-react';

export default function Notice({
  tone,
  message,
  onDismiss,
}: {
  tone: 'success' | 'error' | 'info';
  message: string;
  onDismiss?: () => void;
}) {
  const config = tone === 'success'
    ? { icon: CheckCircle2, color: 'var(--revka-status-success)', border: 'color-mix(in srgb, var(--revka-status-success) 26%, transparent)' }
    : tone === 'error'
      ? { icon: AlertTriangle, color: 'var(--revka-status-danger)', border: 'color-mix(in srgb, var(--revka-status-danger) 26%, transparent)' }
      : { icon: Info, color: 'var(--revka-signal-network)', border: 'color-mix(in srgb, var(--revka-signal-network) 26%, transparent)' };

  const Icon = config.icon;

  return (
    <div
      className="revka-panel revka-notice flex items-start justify-between gap-3 p-3"
      style={{
        borderColor: config.border,
        background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 94%, transparent)',
      }}
    >
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 h-4 w-4 shrink-0" style={{ color: config.color }} />
        <div
          className="text-sm whitespace-pre-wrap"
          style={{ color: 'var(--revka-text-primary)' }}
        >
          {message}
        </div>
      </div>
      {onDismiss ? (
        <button
          type="button"
          className="text-xs font-semibold uppercase tracking-[0.12em]"
          style={{ color: 'var(--revka-text-faint)' }}
          onClick={onDismiss}
        >
          Close
        </button>
      ) : null}
    </div>
  );
}
