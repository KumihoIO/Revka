import { AlertTriangle, CheckCircle2, PauseCircle, PlayCircle, XCircle } from 'lucide-react';
import { useTheme } from '@/construct/hooks/useTheme';
import type { SkinAssetSlot } from '@/types/api';

export default function StatusPill({ status }: { status: string }) {
  const { getSkinAsset } = useTheme();
  const normalized = status.toLowerCase();
  const config = (() => {
    switch (normalized) {
      case 'running':
        return { icon: PlayCircle, color: 'var(--construct-signal-live)', bg: 'var(--construct-signal-live-soft)', assetSlot: 'statusRunningBadge' as SkinAssetSlot };
      case 'completed':
      case 'success':
        return {
          icon: CheckCircle2,
          color: 'var(--construct-status-success)',
          bg: 'color-mix(in srgb, var(--construct-status-success) 12%, transparent)',
          assetSlot: 'statusSuccessBadge' as SkinAssetSlot,
        };
      case 'failed':
        return {
          icon: XCircle,
          color: 'var(--construct-status-danger)',
          bg: 'color-mix(in srgb, var(--construct-status-danger) 12%, transparent)',
          assetSlot: 'statusFailedBadge' as SkinAssetSlot,
        };
      case 'paused':
      case 'blocked':
      case 'pending':
        return {
          icon: PauseCircle,
          color: 'var(--construct-status-warning)',
          bg: 'color-mix(in srgb, var(--construct-status-warning) 12%, transparent)',
          assetSlot: 'statusPendingBadge' as SkinAssetSlot,
        };
      case 'skipped':
        return {
          icon: PauseCircle,
          color: 'var(--construct-status-idle)',
          bg: 'color-mix(in srgb, var(--construct-status-idle) 12%, transparent)',
          assetSlot: 'statusSkippedBadge' as SkinAssetSlot,
        };
      default:
        return {
          icon: AlertTriangle,
          color: 'var(--construct-text-muted)',
          bg: 'color-mix(in srgb, var(--construct-text-muted) 10%, transparent)',
          assetSlot: 'statusPendingBadge' as SkinAssetSlot,
        };
    }
  })();

  const Icon = config.icon;
  const statusAsset = getSkinAsset(config.assetSlot);

  return (
    <span
      className="construct-status-pill"
      style={{ color: config.color, background: config.bg, borderColor: 'transparent' }}
    >
      {statusAsset ? (
        <img src={statusAsset} alt="" className="construct-status-asset" draggable={false} />
      ) : (
        <Icon className="h-3.5 w-3.5" />
      )}
      {status}
    </span>
  );
}
