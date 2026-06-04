import type { ReactNode } from 'react';
import Panel from '../ui/Panel';
import StateMessage from '../ui/StateMessage';

interface SelectionListPanelProps<T> {
  title: string;
  items: T[];
  error?: string | null;
  loading?: boolean;
  loadingText: string;
  emptyTitle?: string;
  emptyDescription?: string;
  renderItem: (item: T) => ReactNode;
}

export default function SelectionListPanel<T>({
  title,
  items,
  error,
  loading,
  loadingText,
  emptyTitle,
  emptyDescription,
  renderItem,
}: SelectionListPanelProps<T>) {
  return (
    <Panel className="p-3" variant="secondary">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="revka-kicker">{title}</div>
          <p className="mt-2 text-sm leading-6" style={{ color: 'var(--revka-text-secondary)' }}>
            Use the index as the entry point into the active workspace, not as a wall of unrelated rows.
          </p>
        </div>
        <span
          className="rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em]"
          style={{ borderColor: 'var(--revka-border-soft)', color: 'var(--revka-text-secondary)' }}
        >
          {items.length}
        </span>
      </div>
      <div className="mt-3 max-h-[26rem] space-y-2 overflow-y-auto pr-1">
        {items.map((item) => renderItem(item))}
        {loading && items.length === 0 ? (
          <StateMessage
            tone="loading"
            compact
            title={loadingText}
            description="The index is being populated from the live orchestration API."
          />
        ) : null}
        {!loading && items.length === 0 && !error ? (
          <StateMessage
            tone="empty"
            compact
            title={emptyTitle ?? 'Nothing to show yet'}
            description={emptyDescription ?? 'No records matched the current workspace selection.'}
          />
        ) : null}
        {error ? <StateMessage tone="error" compact title="Unable to load index" description={error} /> : null}
      </div>
    </Panel>
  );
}
