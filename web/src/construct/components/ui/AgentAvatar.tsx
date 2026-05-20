import { useEffect, useState } from 'react';
import { Bot, Users } from 'lucide-react';

export default function AgentAvatar({
  src,
  alt = 'Agent',
  size = 28,
  radius = 8,
  iconSize,
  kind = 'agent',
  className,
  style,
}: {
  src?: string | null;
  alt?: string;
  size?: number;
  radius?: number;
  iconSize?: number;
  kind?: 'agent' | 'team';
  className?: string;
  style?: React.CSSProperties;
}) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [src]);

  const baseStyle: React.CSSProperties = {
    width: size,
    height: size,
    minWidth: size,
    borderRadius: radius,
    border: '1px solid var(--construct-border-soft)',
    boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.06)',
    ...style,
  };

  if (src && !failed) {
    return (
      <img
        className={className}
        src={src}
        alt={alt}
        onError={() => setFailed(true)}
        style={{
          ...baseStyle,
          display: 'block',
          objectFit: 'cover',
          background: 'var(--construct-bg-elevated)',
        }}
      />
    );
  }

  const FallbackIcon = kind === 'team' ? Users : Bot;

  return (
    <span
      className={className}
      aria-hidden="true"
      style={{
        ...baseStyle,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'color-mix(in srgb, var(--construct-signal-network-soft) 72%, var(--construct-bg-elevated))',
        color: 'var(--construct-signal-network)',
      }}
    >
      <FallbackIcon size={iconSize ?? Math.max(12, Math.round(size * 0.5))} />
    </span>
  );
}
