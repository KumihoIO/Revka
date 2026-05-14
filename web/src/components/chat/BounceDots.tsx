import { Bot } from 'lucide-react';
import { useTheme } from '@/construct/hooks/useTheme';

interface BounceDotsProps {
  /** Compact sizing for modal panels. */
  compact?: boolean;
}

/** Typing indicator — three bouncing dots with agent avatar. */
export default function BounceDots({ compact }: BounceDotsProps) {
  const { getSkinAsset } = useTheme();
  const operatorAvatar = getSkinAsset('operatorAvatar');
  const avatarSize = compact ? 'w-7 h-7 rounded-xl' : 'w-9 h-9 rounded-2xl';
  const iconSize = compact ? 'h-3.5 w-3.5' : 'h-4 w-4';
  const bubbleRadius = compact ? 'rounded-xl' : 'rounded-2xl';
  const bubblePadding = compact ? 'px-3 py-2' : 'px-4 py-3';

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
        className={`${bubbleRadius} ${bubblePadding} border flex items-center gap-1.5`}
        style={{ background: 'var(--pc-bg-elevated)', borderColor: 'var(--pc-border)' }}
      >
        <span className="bounce-dot w-1.5 h-1.5 rounded-full" style={{ background: 'var(--pc-accent)' }} />
        <span className="bounce-dot w-1.5 h-1.5 rounded-full" style={{ background: 'var(--pc-accent)' }} />
        <span className="bounce-dot w-1.5 h-1.5 rounded-full" style={{ background: 'var(--pc-accent)' }} />
      </div>
    </div>
  );
}
