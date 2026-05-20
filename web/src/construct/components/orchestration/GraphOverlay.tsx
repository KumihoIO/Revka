import type { ReactNode } from 'react';
import { useTheme } from '@/construct/hooks/useTheme';
import type { SkinAssetSlot } from '@/types/api';

export function OperatorSection({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="construct-operator-card">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]" style={{ color: 'var(--construct-text-faint)' }}>
        {title}
      </div>
      {children}
    </div>
  );
}

export function OperatorQuickFocusButton({
  label,
  hint,
  onClick,
}: {
  label: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="construct-signal-chip inline-flex items-center gap-2 font-semibold transition-colors"
      style={{
        borderColor: 'var(--construct-border-soft)',
        background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 92%, transparent)',
        color: 'var(--construct-text-primary)',
      }}
    >
      <span>{label}</span>
      <span className="construct-kbd">{hint}</span>
    </button>
  );
}

export function OperatorSignalChip({
  label,
  tone,
  onClick,
}: {
  label: string;
  tone: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="construct-signal-chip text-left"
      style={{
        borderColor: `color-mix(in srgb, ${tone} 28%, transparent)`,
        background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 92%, transparent)',
        color: tone,
      }}
    >
      {label}
    </button>
  );
}

export function OperatorCountChip({
  label,
  value,
  tone,
  compact = false,
  assetSlot,
}: {
  label: string;
  value: number;
  tone: string;
  compact?: boolean;
  assetSlot?: SkinAssetSlot;
}) {
  const { getSkinAsset } = useTheme();
  const asset = assetSlot ? getSkinAsset(assetSlot) : null;

  return (
    <div
      className={compact ? 'rounded-[12px] border px-3 py-2' : 'construct-signal-chip'}
      style={{
        borderColor: `color-mix(in srgb, ${tone} 30%, var(--construct-border-soft))`,
        background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 92%, transparent)',
        backdropFilter: compact ? undefined : 'blur(10px)',
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ color: 'var(--construct-text-faint)' }}>
          {label}
        </div>
        {asset ? <img src={asset} alt="" className="construct-status-asset" draggable={false} /> : null}
      </div>
      <div className={compact ? 'mt-1 text-lg font-semibold' : 'mt-1 text-base font-semibold'} style={{ color: tone }}>
        {value}
      </div>
    </div>
  );
}

export function OperatorLegendChip({ label, tone }: { label: string; tone: string }) {
  return (
    <span
      className="inline-flex items-center gap-2 rounded-[999px] border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]"
      style={{
        borderColor: `color-mix(in srgb, ${tone} 25%, var(--construct-border-soft))`,
        background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 94%, transparent)',
        color: 'var(--construct-text-secondary)',
      }}
    >
      <span className="h-2 w-2 rounded-full" style={{ background: tone }} />
      {label}
    </span>
  );
}
