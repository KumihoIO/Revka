/**
 * AgentPicker — popover for choosing a pool agent.
 *
 * Used in two surfaces:
 *   1. Anchored — opened from the canvas TaskNode badge or the side-panel
 *      field button. `anchorRect` positions the popover ~8px below the
 *      anchor's bottom, right-aligned to the anchor's right edge, clamped
 *      to viewport.
 *   2. Centered — when `anchorRect` is null/undefined, renders as a small
 *      centered modal (used as the auto-open fallback after creating a
 *      new agent step).
 *
 * Implementation note: previous attempts (#127, #128, #129) tried to make
 * cmdk's <Command.Item> fire a mouse-click handler reliably under React 19
 * + portal and none worked in the real browser. This file uses plain JSX
 * (vanilla <input>, <ul>, <button>) with native onClick handlers. cmdk is
 * intentionally NOT imported.
 *
 * Shares the editor-session agent roster via useAgentRoster() so the second
 * open never re-hits the API.
 */

import { Bot, Search } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { AgentDefinition } from '@/types/api';
import { useAgentRoster } from './useAgentRoster';
import NewPoolAgentModal from './NewPoolAgentModal';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Current task.assign — passed in to highlight + show "Clear" button. */
  value?: string;
  /** Callback with the selected pool-agent item_name, or null to clear. */
  onSelect: (name: string | null) => void;
  /**
   * Bounding rect of the trigger element. When provided, popover is
   * positioned anchored to it. When null/undefined, renders centered.
   */
  anchorRect?: DOMRect | null;
}

const POPOVER_WIDTH = 340;
const POPOVER_MAX_HEIGHT = 360;
const ANCHOR_GAP = 8;
const PICKER_BACKDROP_Z = 9000;
const PICKER_PANEL_Z = 9001;
const MAX_VISIBLE = 50;

function computeAnchoredStyle(rect: DOMRect): React.CSSProperties {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const top = Math.min(rect.bottom + ANCHOR_GAP, vh - POPOVER_MAX_HEIGHT - 8);
  const right = Math.max(8, vw - rect.right);
  return {
    position: 'fixed',
    top: Math.max(8, top),
    right,
    width: POPOVER_WIDTH,
    maxHeight: POPOVER_MAX_HEIGHT,
  };
}

function centeredStyle(): React.CSSProperties {
  return {
    position: 'fixed',
    top: '20vh',
    left: '50%',
    transform: 'translateX(-50%)',
    width: POPOVER_WIDTH,
    maxHeight: POPOVER_MAX_HEIGHT,
  };
}

function matchesQuery(agent: AgentDefinition, q: string): boolean {
  if (!q) return true;
  const haystack = [
    agent.item_name,
    agent.name,
    agent.role,
    agent.agent_type,
    (agent.expertise ?? []).join(' '),
  ]
    .join(' ')
    .toLowerCase();
  return haystack.includes(q.toLowerCase());
}

export default function AgentPicker({
  open,
  onOpenChange,
  value,
  onSelect,
  anchorRect,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { agents, loading, refresh } = useAgentRoster();
  const [search, setSearch] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);

  // Autofocus + reset state on open.
  useEffect(() => {
    if (open) {
      setSearch('');
      setActiveIndex(0);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  const filtered = useMemo(() => {
    return agents.filter((a) => matchesQuery(a, search)).slice(0, MAX_VISIBLE);
  }, [agents, search]);

  // Keep activeIndex in range as filtered list shrinks.
  useEffect(() => {
    if (activeIndex >= filtered.length) {
      setActiveIndex(filtered.length === 0 ? 0 : filtered.length - 1);
    }
  }, [filtered.length, activeIndex]);

  const popoverStyle = useMemo<React.CSSProperties>(() => {
    if (anchorRect) return computeAnchoredStyle(anchorRect);
    return centeredStyle();
  }, [anchorRect]);

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  const closeAndReset = () => {
    setSearch('');
    setActiveIndex(0);
    onOpenChange(false);
  };

  const handlePick = (name: string | null) => {
    onSelect(name);
    closeAndReset();
  };

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (filtered.length === 0) return;
      setActiveIndex((i) => (i + 1) % filtered.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (filtered.length === 0) return;
      setActiveIndex((i) => (i - 1 + filtered.length) % filtered.length);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      const target = filtered[activeIndex];
      if (target) handlePick(target.item_name);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      closeAndReset();
    }
  };

  const emptyMessage = loading
    ? 'Loading agents…'
    : agents.length === 0
      ? 'No pool agents — click + New pool agent'
      : 'No agents match';

  const content = (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Choose pool agent"
      onClick={(e) => {
        if (e.target === e.currentTarget) closeAndReset();
      }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: PICKER_BACKDROP_Z,
        background: 'transparent',
      }}
    >
      <div
        className="construct-panel"
        data-variant="primary"
        style={{
          ...popoverStyle,
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 12,
          borderColor: 'var(--construct-border-strong)',
          boxShadow: '0 24px 64px rgba(0,0,0,0.36)',
          overflow: 'hidden',
          zIndex: PICKER_PANEL_Z,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Search input */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            padding: '10px 12px',
            borderBottom: '1px solid var(--construct-border-soft)',
          }}
        >
          <Search size={14} style={{ color: 'var(--construct-text-faint)' }} />
          <input
            ref={inputRef}
            type="text"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={handleInputKeyDown}
            placeholder="Search pool agents…"
            style={{
              flex: 1,
              background: 'transparent',
              border: 0,
              outline: 'none',
              color: 'var(--construct-text-primary)',
              fontSize: 13,
            }}
          />
          <kbd
            className="construct-kbd"
            style={{
              fontSize: 10,
              padding: '2px 6px',
              borderRadius: 6,
              color: 'var(--construct-text-faint)',
              background: 'var(--pc-bg-input)',
              border: '1px solid var(--construct-border-soft)',
            }}
          >
            ESC
          </kbd>
        </div>

        {/* List body */}
        <div
          style={{
            flex: 1,
            maxHeight: 240,
            overflowY: 'auto',
            padding: '6px',
          }}
        >
          {filtered.length === 0 ? (
            <div
              style={{
                padding: '20px 16px',
                textAlign: 'center',
                fontSize: 12,
                color: 'var(--construct-text-faint)',
              }}
            >
              {emptyMessage}
            </div>
          ) : (
            <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
              {filtered.map((agent, i) => {
                const isSelected = value === agent.item_name;
                const isActive = i === activeIndex;
                return (
                  <li key={agent.kref} style={{ margin: 0, padding: 0 }}>
                    <button
                      type="button"
                      data-active={isActive}
                      onMouseEnter={() => setActiveIndex(i)}
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        handlePick(agent.item_name);
                      }}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        padding: '8px 10px',
                        borderRadius: 8,
                        cursor: 'pointer',
                        color: 'var(--construct-text-primary)',
                        width: '100%',
                        textAlign: 'left',
                        background: isActive ? 'var(--pc-accent-glow)' : 'transparent',
                        boxShadow: isActive
                          ? 'inset 2px 0 0 var(--pc-accent)'
                          : 'none',
                        border: 0,
                        font: 'inherit',
                      }}
                    >
                      <span
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          width: 24,
                          height: 24,
                          borderRadius: 6,
                          background: isSelected
                            ? 'var(--construct-signal-network-soft)'
                            : 'color-mix(in srgb, var(--pc-accent-glow) 60%, transparent)',
                          color: isSelected
                            ? 'var(--construct-signal-network)'
                            : 'var(--pc-accent)',
                          flexShrink: 0,
                        }}
                      >
                        <Bot size={13} />
                      </span>
                      <span
                        style={{
                          display: 'flex',
                          flexDirection: 'column',
                          minWidth: 0,
                          flex: 1,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 12.5,
                            fontWeight: 600,
                            fontFamily:
                              'var(--pc-font-mono, ui-monospace, monospace)',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {agent.item_name}
                        </span>
                        {agent.identity && (
                          <span
                            style={{
                              fontSize: 10.5,
                              color: 'var(--construct-text-secondary)',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {agent.identity}
                          </span>
                        )}
                      </span>
                      <span
                        style={{
                          fontFamily:
                            'var(--pc-font-mono, ui-monospace, monospace)',
                          fontSize: 9.5,
                          padding: '2px 6px',
                          borderRadius: 4,
                          background: 'var(--pc-hover)',
                          color: 'var(--construct-text-faint)',
                          flexShrink: 0,
                          textTransform: 'uppercase',
                          letterSpacing: '0.04em',
                        }}
                      >
                        {agent.agent_type}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        {/* Clear assignment */}
        {value && (
          <button
            type="button"
            onClick={() => handlePick(null)}
            style={{
              margin: '0 8px 8px',
              padding: '6px 10px',
              fontSize: 11,
              fontWeight: 600,
              borderRadius: 6,
              border: '1px solid var(--construct-status-warning)',
              background:
                'color-mix(in srgb, var(--construct-status-warning) 14%, transparent)',
              color: 'var(--construct-status-warning)',
              cursor: 'pointer',
            }}
          >
            Clear assignment
          </button>
        )}

        {/* Footer */}
        <div
          style={{
            padding: '8px 12px',
            borderTop: '1px solid var(--construct-border-soft)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            fontSize: 11,
            color: 'var(--construct-text-faint)',
          }}
        >
          <span>
            {filtered.length === agents.length
              ? `${agents.length} agent${agents.length === 1 ? '' : 's'}`
              : `${filtered.length} of ${agents.length}`}
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
            <a
              href="/agents"
              target="_blank"
              rel="noreferrer"
              style={{
                color: 'var(--construct-text-faint)',
                textDecoration: 'none',
                fontWeight: 500,
              }}
            >
              Open agents page →
            </a>
            <button
              type="button"
              onClick={() => setCreateOpen(true)}
              style={{
                background: 'transparent',
                border: 0,
                padding: 0,
                cursor: 'pointer',
                color: 'var(--pc-accent)',
                fontWeight: 600,
                fontSize: 11,
              }}
            >
              + New pool agent
            </button>
          </span>
        </div>
      </div>
    </div>
  );

  // Portal to body so position:fixed escapes any ancestor with
  // transform/filter/will-change that would otherwise become the
  // containing block and clip the popover behind the side panel.
  return (
    <>
      {createPortal(content, document.body)}
      <NewPoolAgentModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={async (agent) => {
          await refresh();
          setCreateOpen(false);
          handlePick(agent.item_name);
        }}
      />
    </>
  );
}
