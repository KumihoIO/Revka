import type { ReactNode } from 'react';

export default function PageHeader({
  kicker,
  description,
  actions,
}: {
  kicker?: string;
  /** Deprecated: page title now lives in the app Header. Kept for call-site
   *  compatibility; silently ignored. */
  title?: string;
  description?: string;
  actions?: ReactNode;
}) {
  if (!kicker && !description && !actions) return null;
  return (
    <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
      <div>
        {kicker ? <div className="revka-kicker">{kicker}</div> : null}
        {description ? (
          <p className="mt-2 max-w-3xl text-sm" style={{ color: 'var(--revka-text-secondary)' }}>
            {description}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
