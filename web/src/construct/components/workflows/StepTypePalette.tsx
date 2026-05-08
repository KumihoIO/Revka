/**
 * StepTypePalette — cmdk-powered command palette for choosing a workflow step type.
 *
 * Triggered by ⌘K, the toolbar + button, the empty-state command list, the
 * canvas right-click menu, and noodle-drops. The palette is a pure chooser:
 * on selection it emits `construct:add-step`, the editor canvas is the only
 * writer of canvas state.
 */

import { Command } from 'cmdk';
import { Search } from 'lucide-react';
import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  CATEGORY_META,
  CATEGORY_ORDER,
  STEP_TYPES_BY_CATEGORY,
} from './stepRegistry';
import { emitAddStep, type AddStepDetail } from './stepEvents';

const PICKER_BACKDROP_Z = 9000;
const PICKER_PANEL_Z = 9001;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context?: Pick<AddStepDetail, 'position' | 'source' | 'target' | 'presetSkill'>;
  /** Override label of the action button — default "Add Step" */
  actionLabel?: string;
  /** Custom emit handler — when provided the palette calls this instead of
   * dispatching the global add-step event. Used by the side panel's
   * "Change type" flow. */
  onSelect?: (type: string) => void;
}

export default function StepTypePalette({
  open,
  onOpenChange,
  context,
  actionLabel,
  onSelect,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  // Autofocus the search input when opening.
  useEffect(() => {
    if (open) {
      // Defer to allow cmdk to mount.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Close on Escape (cmdk doesn't bind Escape on the dialog wrapper itself).
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        onOpenChange(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onOpenChange]);

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  const handlePick = (type: string) => {
    if (onSelect) {
      onSelect(type);
    } else {
      emitAddStep({ type, ...(context ?? {}) });
    }
    onOpenChange(false);
  };

  const content = (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={actionLabel ?? 'Add Step'}
      onClick={(e) => {
        if (e.target === e.currentTarget) onOpenChange(false);
      }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: PICKER_BACKDROP_Z,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: '14vh',
        background: 'color-mix(in srgb, var(--construct-bg-base) 70%, transparent)',
        backdropFilter: 'blur(2px)',
      }}
    >
      <div
        className="construct-panel"
        data-variant="primary"
        style={{
          width: 'min(640px, 92vw)',
          maxHeight: '70vh',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 12,
          borderColor: 'var(--construct-border-strong)',
          boxShadow: '0 24px 64px rgba(0,0,0,0.36)',
          overflow: 'hidden',
          zIndex: PICKER_PANEL_Z,
        }}
      >
        <Command
          label={actionLabel ?? 'Add Step'}
          loop
          // Custom filter so searchTags participate in fuzzy match.
          shouldFilter={true}
        >
          {/* Search input */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 10,
              padding: '12px 14px',
              borderBottom: '1px solid var(--construct-border-soft)',
            }}
          >
            <Search size={16} style={{ color: 'var(--construct-text-faint)' }} />
            <Command.Input
              ref={inputRef}
              placeholder="Search steps, skills, channels..."
              style={{
                flex: 1,
                background: 'transparent',
                border: 0,
                outline: 'none',
                color: 'var(--construct-text-primary)',
                fontSize: 14,
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
          <Command.List
            style={{
              maxHeight: '54vh',
              overflowY: 'auto',
              padding: '6px',
            }}
          >
            <Command.Empty
              style={{
                padding: '24px 16px',
                textAlign: 'center',
                fontSize: 13,
                color: 'var(--construct-text-faint)',
              }}
            >
              No steps match
            </Command.Empty>

            {CATEGORY_ORDER.map((category) => {
              const meta = CATEGORY_META[category];
              const items = STEP_TYPES_BY_CATEGORY[category];
              return (
                <Command.Group
                  key={category}
                  heading={
                    <div className="construct-kicker" style={{ padding: '8px 10px 4px', letterSpacing: '0.16em' }}>
                      {meta.label}
                    </div>
                  }
                >
                  {items.map((step) => {
                    const Icon = step.icon;
                    return (
                      <Command.Item
                        key={step.type}
                        value={`${step.label} ${step.type} ${step.description} ${step.searchTags.join(' ')}`}
                        keywords={[step.type, ...step.searchTags]}
                        onSelect={() => handlePick(step.type)}
                        asChild
                      >
                        {/*
                          asChild routes cmdk's Item through Radix Slot, which
                          merges cmdk's wired onClick (calls onSelect) onto
                          this button. Do NOT add another onClick here — Slot
                          would chain both, firing handlePick twice and
                          spawning duplicate steps.
                        */}
                        <button
                          type="button"
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 12,
                            padding: '10px 12px',
                            borderRadius: 10,
                            cursor: 'pointer',
                            color: 'var(--construct-text-primary)',
                            width: '100%',
                            textAlign: 'left',
                            background: 'transparent',
                            border: 0,
                            font: 'inherit',
                          }}
                        >
                          <span
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              justifyContent: 'center',
                              width: 28,
                              height: 28,
                              borderRadius: 8,
                              background: 'color-mix(in srgb, var(--pc-accent-glow) 60%, transparent)',
                              color: 'var(--pc-accent)',
                              flexShrink: 0,
                            }}
                          >
                            <Icon size={16} />
                          </span>
                          <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                            <span style={{ fontSize: 13, fontWeight: 600 }}>{step.label}</span>
                            <span
                              style={{
                                fontSize: 11.5,
                                color: 'var(--construct-text-secondary)',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              {step.description}
                            </span>
                          </span>
                          <span
                            style={{
                              fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
                              fontSize: 10,
                              color: 'var(--construct-text-faint)',
                              flexShrink: 0,
                            }}
                          >
                            {step.type}
                          </span>
                        </button>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              );
            })}
          </Command.List>
        </Command>
      </div>

      <style>{`
        [cmdk-item][data-selected='true'] {
          background: var(--pc-accent-glow);
          box-shadow: inset 2px 0 0 var(--pc-accent);
        }
        [cmdk-item][data-selected='true'] span:first-child {
          background: color-mix(in srgb, var(--pc-accent-glow) 90%, transparent);
        }
        [cmdk-item]:hover {
          background: var(--pc-hover);
        }
        [cmdk-group-heading] {
          padding: 0;
        }
      `}</style>
    </div>
  );

  // Portal to body so position:fixed escapes any ancestor with
  // transform/filter/will-change that would otherwise become the
  // containing block and clip the palette behind the side panel.
  return createPortal(content, document.body);
}
