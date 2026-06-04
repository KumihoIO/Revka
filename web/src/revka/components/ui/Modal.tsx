import type { ReactNode } from 'react';

type ModalSize = 'md' | 'lg' | 'xl' | '2xl';

const SIZE_CLASS: Record<ModalSize, string> = {
  md: 'max-w-3xl',
  lg: 'max-w-4xl',
  xl: 'max-w-5xl',
  '2xl': 'max-w-6xl',
};

export default function Modal({
  title,
  description,
  children,
  onClose,
  size = 'md',
}: {
  title: string;
  description?: string;
  children: ReactNode;
  onClose: () => void;
  size?: ModalSize;
}) {
  return (
    <div
      className="fixed inset-0 z-[120] flex items-center justify-center p-4"
      style={{ background: 'rgba(5, 8, 10, 0.64)', backdropFilter: 'blur(10px)' }}
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      role="dialog"
      aria-modal="true"
    >
      <div className={`revka-panel revka-modal-shell w-full ${SIZE_CLASS[size]} p-5`}>
        <div className="mb-4 shrink-0">
          <div className="revka-kicker">Action Surface</div>
          <h3 className="mt-2 text-lg font-semibold" style={{ color: 'var(--revka-text-primary)' }}>{title}</h3>
          {description ? (
            <p className="mt-2 text-sm" style={{ color: 'var(--revka-text-secondary)' }}>{description}</p>
          ) : null}
        </div>
        <div className="revka-modal-body">
          {children}
        </div>
      </div>
    </div>
  );
}
