import { AlertTriangle, CheckCircle2, History, PauseCircle, PlayCircle, XCircle } from 'lucide-react';
import { useTheme } from '@/revka/hooks/useTheme';
import type { SkinAssetSlot } from '@/types/api';

export default function StatusPill({ status }: { status: string }) {
  const { getSkinAsset } = useTheme();
  const normalized = status.toLowerCase();
  const config = (() => {
    switch (normalized) {
      case 'running':
        return { icon: PlayCircle, color: 'var(--revka-signal-live)', bg: 'var(--revka-signal-live-soft)', assetSlot: 'statusRunningBadge' as SkinAssetSlot };
      case 'completed':
      case 'success':
        return {
          icon: CheckCircle2,
          color: 'var(--revka-status-success)',
          bg: 'color-mix(in srgb, var(--revka-status-success) 8%, transparent)',
          assetSlot: 'statusSuccessBadge' as SkinAssetSlot,
        };
      case 'failed':
        return {
          icon: XCircle,
          color: 'var(--revka-status-danger)',
          bg: 'color-mix(in srgb, var(--revka-status-danger) 12%, transparent)',
          assetSlot: 'statusFailedBadge' as SkinAssetSlot,
        };
      case 'paused':
      case 'blocked':
      case 'pending':
        return {
          icon: PauseCircle,
          color: 'var(--revka-status-warning)',
          bg: 'color-mix(in srgb, var(--revka-status-warning) 12%, transparent)',
          assetSlot: 'statusPendingBadge' as SkinAssetSlot,
        };
      case 'skipped':
        return {
          icon: PauseCircle,
          color: 'var(--revka-status-idle)',
          bg: 'color-mix(in srgb, var(--revka-status-idle) 12%, transparent)',
          assetSlot: 'statusSkippedBadge' as SkinAssetSlot,
        };
      case 'stale':
        // Live progress is unknown (e.g. checkpoint wiped by a redeploy);
        // distinct from the generic unknown fallback so operators can tell
        // "cold cache — open the run for its real status" at a glance.
        return {
          icon: History,
          color: 'var(--revka-status-idle)',
          bg: 'color-mix(in srgb, var(--revka-status-idle) 14%, transparent)',
          assetSlot: 'statusSkippedBadge' as SkinAssetSlot,
        };
      default:
        return {
          icon: AlertTriangle,
          color: 'var(--revka-text-muted)',
          bg: 'color-mix(in srgb, var(--revka-text-muted) 10%, transparent)',
          assetSlot: 'statusPendingBadge' as SkinAssetSlot,
        };
    }
  })();

  const Icon = config.icon;
  const statusAsset = getSkinAsset(config.assetSlot);

  return (
    <span
      className="revka-status-pill"
      style={{ color: config.color, background: config.bg, borderColor: 'transparent' }}
    >
      {statusAsset ? (
        <img src={statusAsset} alt="" className="revka-status-asset" draggable={false} />
      ) : (
        <Icon className="h-3.5 w-3.5" />
      )}
      {status}
    </span>
  );
}
