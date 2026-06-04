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
import { Lock, Search, AlertTriangle, Trash2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ApiError, deleteAuthProfile } from '@/lib/api';
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
  /** Optional provider slug (e.g. ``manus``) to filter the picker list AND
   *  pre-fill in the New-auth-profile modal. When set, the picker only
   *  shows profiles whose provider matches and the modal opens with the
   *  provider field pre-populated. */
  providerFilter?: string;
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
  providerFilter,
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

  // Group profiles by provider for the cmdk Group sections. When the
  // caller supplies a providerFilter we narrow to that provider only —
  // step types like Manus are single-provider and shouldn't show e.g.
  // Slack/GitHub profiles alongside the relevant ones.
  const grouped = useMemo(() => {
    const byProvider = new Map<string, AuthProfileSummary[]>();
    for (const p of profiles) {
      if (providerFilter && p.provider !== providerFilter) continue;
      const list = byProvider.get(p.provider) ?? [];
      list.push(p);
      byProvider.set(p.provider, list);
    }
    return Array.from(byProvider.entries()).sort((a, b) => a[0].localeCompare(b[0]));
  }, [profiles, providerFilter]);

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  const handlePick = (id: string | null) => {
    onSelect(id);
    onOpenChange(false);
  };

  // Per-row delete: call DELETE /api/auth/profiles/{id} then refresh the
  // cache. Browser-native confirm() keeps the picker minimal — a custom
  // confirm UI would be a bigger surface change than the delete button
  // itself, and the action is destructive enough to justify the prompt.
  const handleDelete = async (p: AuthProfileSummary) => {
    const ok = window.confirm(
      `Delete auth profile "${p.provider}:${p.profile_name}"? ` +
        `Workflow steps bound to this profile will fail until rebound.`,
    );
    if (!ok) return;
    try {
      await deleteAuthProfile(p.id);
      // If the deleted profile was the current selection, clear it.
      if (value === p.id) onSelect(null);
      await refresh();
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message.replace(/^API \d+: /, '')
          : err instanceof Error
            ? err.message
            : 'Delete failed';
      window.alert(`Failed to delete auth profile: ${msg}`);
    }
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
        className="revka-panel"
        data-variant="primary"
        style={{
          ...popoverStyle,
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 12,
          borderColor: 'var(--revka-border-strong)',
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
              borderBottom: '1px solid var(--revka-border-soft)',
            }}
          >
            <Search size={14} style={{ color: 'var(--revka-text-faint)' }} />
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
                color: 'var(--revka-text-primary)',
                fontSize: 13,
              }}
            />
            <kbd
              className="revka-kbd"
              style={{
                fontSize: 10,
                padding: '2px 6px',
                borderRadius: 6,
                color: 'var(--revka-text-faint)',
                background: 'var(--pc-bg-input)',
                border: '1px solid var(--revka-border-soft)',
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
                color: 'var(--revka-text-faint)',
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
                  color: 'var(--revka-text-faint)',
                }}
              >
                {list.map((p) => {
                  const isSelected = value === p.id;
                  const chip = expiryChip(p);
                  return (
                    <div
                      key={p.id}
                      style={{ position: 'relative' }}
                      onPointerEnter={(e) => {
                        const btn = (e.currentTarget as HTMLElement).querySelector<HTMLElement>(
                          '[data-auth-row-delete]',
                        );
                        if (btn) btn.style.opacity = '1';
                      }}
                      onPointerLeave={(e) => {
                        const btn = (e.currentTarget as HTMLElement).querySelector<HTMLElement>(
                          '[data-auth-row-delete]',
                        );
                        if (btn) btn.style.opacity = '0';
                      }}
                    >
                    <Command.Item
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
                          paddingRight: 34,
                          borderRadius: 8,
                          cursor: 'pointer',
                          color: 'var(--revka-text-primary)',
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
                              ? 'var(--revka-signal-network-soft)'
                              : 'color-mix(in srgb, var(--pc-accent-glow) 60%, transparent)',
                            color: isSelected
                              ? 'var(--revka-signal-network)'
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
                                color: 'var(--revka-text-faint)',
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
                            color: 'var(--revka-text-faint)',
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
                                  ? 'color-mix(in srgb, var(--revka-status-danger) 18%, transparent)'
                                  : 'color-mix(in srgb, var(--revka-status-warning) 18%, transparent)',
                              color:
                                chip.tone === 'danger'
                                  ? 'var(--revka-status-danger)'
                                  : 'var(--revka-status-warning)',
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
                    {/*
                      Per-row delete affordance — sibling of the cmdk Item
                      button so the click never bubbles into cmdk's
                      "select" path. Reveal on hover; hidden by default to
                      avoid noise.
                    */}
                    <button
                      type="button"
                      data-auth-row-delete
                      title="Delete auth profile"
                      aria-label={`Delete ${p.provider}:${p.profile_name}`}
                      onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        void handleDelete(p);
                      }}
                      style={{
                        position: 'absolute',
                        top: '50%',
                        right: 8,
                        transform: 'translateY(-50%)',
                        opacity: 0,
                        transition: 'opacity 80ms ease',
                        background: 'transparent',
                        border: 0,
                        padding: 4,
                        borderRadius: 6,
                        cursor: 'pointer',
                        color: 'var(--revka-status-danger)',
                      }}
                    >
                      <Trash2 size={12} />
                    </button>
                    </div>
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
                border: '1px solid var(--revka-status-warning)',
                background: 'color-mix(in srgb, var(--revka-status-warning) 14%, transparent)',
                color: 'var(--revka-status-warning)',
                cursor: 'pointer',
              }}
            >
              Clear auth profile
            </button>
          )}

          <div
            style={{
              padding: '8px 12px',
              borderTop: '1px solid var(--revka-border-soft)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              fontSize: 11,
              color: 'var(--revka-text-faint)',
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
                  color: 'var(--revka-text-faint)',
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
        defaultProvider={providerFilter}
        onCreated={async (id) => {
          await refresh();
          setCreateOpen(false);
          handlePick(id);
        }}
      />
    </>
  );
}
