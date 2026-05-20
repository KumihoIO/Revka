import type { ReactNode } from 'react';

export default function Panel({
  children,
  className = '',
  variant = 'primary',
  skinSlot,
}: {
  children: ReactNode;
  className?: string;
  variant?: 'primary' | 'secondary' | 'utility';
  skinSlot?: 'riskRail' | 'agentRail' | 'commandBand' | 'recentRuns' | 'runCard' | 'stepCard' | 'timeline' | 'metric';
}) {
  return (
    <section className={`construct-panel ${className}`.trim()} data-variant={variant} data-skin-slot={skinSlot}>
      {children}
    </section>
  );
}
