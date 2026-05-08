import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { SlashCommand } from './slashCommands';

interface SlashCommandMenuProps {
  /** Element the menu attaches to. The menu opens *above* this element
   *  (composer is at the bottom of the panel) so it doesn't run off
   *  the viewport. */
  anchorRef: React.RefObject<HTMLElement | null>;
  /** Filtered list of commands to display. ChatPane filters via
   *  `matchCommands(query)` and passes the result here so re-rendering
   *  on every keystroke stays cheap. Empty list collapses the menu. */
  matches: SlashCommand[];
  /** Currently highlighted index. ChatPane manages this so the textarea's
   *  ArrowUp/Down/Tab/Enter handlers can move and select without the
   *  menu needing its own keyboard listener stack. */
  selectedIndex: number;
  /** Click selects the command at the given index — same path as
   *  Enter would take. */
  onPick: (index: number) => void;
}

/**
 * Slash-command picker rendered as a portal so it floats above the
 * chat panel's stacking contexts the same way `NewTabMenu` does. Opens
 * upward from the anchor (composer) — typical chat-app convention.
 *
 * Keyboard navigation is *not* owned here: ChatPane's textarea handles
 * ArrowUp/Down/Tab/Enter/Esc and tells us which row is selected. That
 * keeps the focus on the textarea (so typing keeps working) instead of
 * having us steal it onto a menu element.
 */
export default function SlashCommandMenu({
  anchorRef,
  matches,
  selectedIndex,
  onPick,
}: SlashCommandMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ bottom: number; left: number; width: number } | null>(null);

  // Position the menu above the anchor. `bottom: viewportHeight - rect.top`
  // pins the menu's bottom edge to just above the anchor; recompute on
  // scroll/resize so it tracks if the panel reflows. Use the anchor's
  // width so the menu visually aligns with the composer rather than
  // floating loose to the left.
  useLayoutEffect(() => {
    const update = () => {
      const rect = anchorRef.current?.getBoundingClientRect();
      if (!rect) return;
      setPos({
        bottom: window.innerHeight - rect.top + 6,
        left: rect.left,
        width: rect.width,
      });
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [anchorRef]);

  // When the highlighted row scrolls out of view (because the menu has
  // many entries and a max-height), scroll it back into view. Only
  // affects long lists — short ones never scroll at all.
  useEffect(() => {
    const el = menuRef.current?.querySelector(`[data-slash-index="${selectedIndex}"]`);
    el?.scrollIntoView({ block: 'nearest' });
  }, [selectedIndex]);

  if (matches.length === 0 || !pos) return null;

  return createPortal(
    <div
      ref={menuRef}
      className="fixed z-[200] overflow-y-auto rounded-[8px] border py-1 shadow-lg"
      style={{
        bottom: pos.bottom,
        left: pos.left,
        width: pos.width,
        maxHeight: '40vh',
        background: 'var(--construct-bg-panel-strong)',
        borderColor: 'var(--construct-border-strong)',
      }}
      role="listbox"
      aria-label="Slash commands"
    >
      {matches.map((cmd, i) => {
        const active = i === selectedIndex;
        return (
          <button
            key={cmd.name}
            type="button"
            data-slash-index={i}
            role="option"
            aria-selected={active}
            // Use mousedown rather than click so we fire before the
            // textarea's blur fires; otherwise the input loses focus
            // before we can re-focus and apply state.
            onMouseDown={(e) => {
              e.preventDefault();
              onPick(i);
            }}
            className="flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-xs transition-colors"
            style={{
              background: active ? 'color-mix(in srgb, var(--construct-signal-live) 12%, transparent)' : 'transparent',
              color: active ? 'var(--construct-text-primary)' : 'var(--construct-text-secondary)',
            }}
          >
            <span className="flex min-w-0 items-baseline gap-2">
              <span className="font-mono font-semibold" style={{ color: active ? 'var(--construct-signal-live)' : 'var(--construct-text-primary)' }}>
                /{cmd.name}
              </span>
              {cmd.args ? (
                <span className="font-mono text-[10px]" style={{ color: 'var(--construct-text-faint)' }}>
                  {cmd.args}
                </span>
              ) : null}
              {cmd.aliases && cmd.aliases.length > 0 ? (
                <span className="font-mono text-[10px]" style={{ color: 'var(--construct-text-faint)' }}>
                  · {cmd.aliases.map((a) => `/${a}`).join(' ')}
                </span>
              ) : null}
            </span>
            <span className="truncate text-[11px]" style={{ color: 'var(--construct-text-muted)' }}>
              {cmd.description}
            </span>
          </button>
        );
      })}
    </div>,
    document.body,
  );
}
