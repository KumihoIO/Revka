/**
 * AuthProfilePicker — cmdk popover for binding an encrypted auth profile
 * to a workflow step.
 *
 * Style mirrors AgentPicker (anchored or centered popover, ESC to close,
 * grouped item list, footer link). Items are grouped by provider; OAuth
 * profiles within 24h of expiry get a warning chip, already-expired
 * profiles a danger chip. Token bytes never leave the gateway — selecting
 * a profile only writes its `id` (e.g. `gmail:work`) to step.auth.
 */

import { Command } from 'cmdk';
import { Lock, Search, AlertTriangle } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { AuthProfileSummary } from '@/types/api';
import { useAuthProfiles } from './useAuthProfiles';
import { providerLabel } from './providerLabels';
import NewAuthProfileModal from './NewAuthProfileModal';

// Re-export for legacy import sites (StepConfigPanel etc.).
export { providerLabel } from './providerLabels';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Current task.auth — used to highlight + show "Clear" button. */
  value?: string;
  /** Selected profile id, or null to clear. */
  onSelect: (id: string | null) => void;
  /** Bounding rect of the trigger element. */
  anchorRect?: DOMRect | null;
}

const POPOVER_WIDTH = 360;
const POPOVER_MAX_HEIGHT = 400;
const ANCHOR_GAP = 8;
const NEAR_EXPIRY_HOURS = 24;
const PICKER_BACKDROP_Z = 9000;
const PICKER_PANEL_Z = 9001;

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

interface ExpiryChip {
  tone: 'warning' | 'danger';
  label: string;
}

function expiryChip(p: AuthProfileSummary): ExpiryChip | null {
  if (p.kind !== 'oauth' || !p.expires_at) return null;
  const expMs = Date.parse(p.expires_at);
  if (isNaN(expMs)) return null;
  const now = Date.now();
  if (expMs <= now) {
    return { tone: 'danger', label: 'expired' };
  }
  if (expMs - now <= NEAR_EXPIRY_HOURS * 3_600_000) {
    return { tone: 'warning', label: 'expires soon' };
  }
  return null;
}

export default function AuthProfilePicker({
  open,
  onOpenChange,
  value,
  onSelect,
  anchorRect,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const { profiles, loading, refresh } = useAuthProfiles();
  const [search, setSearch] = useState('');
  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    if (open) {
      setSearch('');
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

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

  const popoverStyle = useMemo<React.CSSProperties>(() => {
    if (anchorRect) return computeAnchoredStyle(anchorRect);
    return centeredStyle();
  }, [anchorRect]);

  // Group profiles by provider for the cmdk Group sections.
  const grouped = useMemo(() => {
    const byProvider = new Map<string, AuthProfileSummary[]>();
    for (const p of profiles) {
      const list = byProvider.get(p.provider) ?? [];
      list.push(p);
      byProvider.set(p.provider, list);
    }
    return Array.from(byProvider.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [profiles]);

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  const handlePick = (id: string | null) => {
    onSelect(id);
    onOpenChange(false);
  };

  const content = (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Choose auth profile"
      onClick={(e) => {
        if (e.target === e.currentTarget) onOpenChange(false);
      }}
      style={{ position: 'fixed', inset: 0, zIndex: PICKER_BACKDROP_Z, background: 'transparent' }}
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
        <Command label="Choose auth profile" loop shouldFilter>
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
            <Command.Input
              ref={inputRef}
              value={search}
              onValueChange={setSearch}
              placeholder="Search auth profiles…"
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

          <Command.List style={{ flex: 1, maxHeight: 280, overflowY: 'auto', padding: '6px' }}>
            <Command.Empty
              style={{
                padding: '20px 16px',
                textAlign: 'center',
                fontSize: 12,
                color: 'var(--construct-text-faint)',
              }}
            >
              {loading ? 'Loading auth profiles…' : 'No auth profiles match'}
            </Command.Empty>

            {grouped.map(([provider, list]) => (
              <Command.Group
                key={provider}
                heading={providerLabel(provider)}
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  textTransform: 'uppercase',
                  letterSpacing: '0.08em',
                  color: 'var(--construct-text-faint)',
                }}
              >
                {list.map((p) => {
                  const isSelected = value === p.id;
                  const chip = expiryChip(p);
                  return (
                    <Command.Item
                      key={p.id}
                      value={`${p.provider} ${p.profile_name} ${p.id} ${p.account_id ?? ''}`}
                      keywords={[p.kind, p.provider, p.account_id ?? '']}
                      onSelect={() => handlePick(p.id)}
                      asChild
                    >
                      {/*
                        asChild routes cmdk's Item through Radix Slot, which
                        merges cmdk's wired onClick (calls onSelect) onto
                        this button. Do NOT add another onClick here — Slot
                        would chain both, firing handlePick twice.
                      */}
                      <button
                        type="button"
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
                          <Lock size={13} />
                        </span>
                        <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0, flex: 1 }}>
                          <span
                            style={{
                              fontSize: 12.5,
                              fontWeight: 600,
                              fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            {p.profile_name}
                          </span>
                          {p.account_id && (
                            <span
                              style={{
                                fontSize: 10.5,
                                color: 'var(--construct-text-faint)',
                                overflow: 'hidden',
                                textOverflow: 'ellipsis',
                                whiteSpace: 'nowrap',
                              }}
                            >
                              as {p.account_id}
                            </span>
                          )}
                        </span>
                        <span
                          style={{
                            fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
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
                          {p.kind}
                        </span>
                        {chip && (
                          <span
                            title={
                              chip.tone === 'danger'
                                ? `Expired at ${p.expires_at}`
                                : `Expires at ${p.expires_at}`
                            }
                            style={{
                              display: 'inline-flex',
                              alignItems: 'center',
                              gap: 3,
                              fontSize: 9.5,
                              padding: '2px 6px',
                              borderRadius: 4,
                              background:
                                chip.tone === 'danger'
                                  ? 'color-mix(in srgb, var(--construct-status-danger) 18%, transparent)'
                                  : 'color-mix(in srgb, var(--construct-status-warning) 18%, transparent)',
                              color:
                                chip.tone === 'danger'
                                  ? 'var(--construct-status-danger)'
                                  : 'var(--construct-status-warning)',
                              flexShrink: 0,
                              textTransform: 'uppercase',
                              letterSpacing: '0.04em',
                            }}
                          >
                            <AlertTriangle size={9} />
                            {chip.label}
                          </span>
                        )}
                      </button>
                    </Command.Item>
                  );
                })}
              </Command.Group>
            ))}
          </Command.List>

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
                background: 'color-mix(in srgb, var(--construct-status-warning) 14%, transparent)',
                color: 'var(--construct-status-warning)',
                cursor: 'pointer',
              }}
            >
              Clear auth profile
            </button>
          )}

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
              {profiles.length} profile{profiles.length === 1 ? '' : 's'}
            </span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
              <a
                href="/config"
                target="_blank"
                rel="noreferrer"
                style={{
                  color: 'var(--construct-text-faint)',
                  textDecoration: 'none',
                  fontWeight: 500,
                }}
              >
                Open config →
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
                + New auth profile
              </button>
            </span>
          </div>
        </Command>
      </div>

      <style>{`
        [cmdk-item][data-selected='true'] {
          background: var(--pc-accent-glow);
          box-shadow: inset 2px 0 0 var(--pc-accent);
        }
        [cmdk-item]:hover {
          background: var(--pc-hover);
        }
        [cmdk-group-heading] {
          padding: 6px 10px 4px;
        }
      `}</style>
    </div>
  );

  // Portal to body so position:fixed escapes any ancestor with
  // transform/filter/will-change that would otherwise become the
  // containing block and clip the popover behind the side panel.
  return (
    <>
      {createPortal(content, document.body)}
      <NewAuthProfileModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={async (id) => {
          await refresh();
          setCreateOpen(false);
          handlePick(id);
        }}
      />
    </>
  );
}
