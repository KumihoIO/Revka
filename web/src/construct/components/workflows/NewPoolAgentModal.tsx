/**
 * NewPoolAgentModal — inline pool-agent creation surface, opened from the
 * AgentPicker footer.
 *
 * Reuses `POST /api/agents` (handler at `src/gateway/api_agents.rs`). On
 * success, the parent picker invalidates its agent-roster cache, refreshes,
 * and auto-selects the new agent's `item_name`.
 *
 * Form fields match the existing Agents page (name / agent_type / role /
 * model / system_hint / identity) so the back-end schema stays single-shape.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Bot, Loader2, X } from 'lucide-react';
import { createAgent } from '@/lib/api';
import { ApiError } from '@/lib/api';
import type { AgentDefinition, AgentCreateRequest } from '@/types/api';
import { slugify } from './slugify';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Called on successful POST with the created agent. */
  onCreated: (agent: AgentDefinition) => void | Promise<void>;
}

const MODAL_BACKDROP_Z = 9099;
const MODAL_PANEL_Z = 9100;

const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: 'coder', label: 'Coder' },
  { value: 'researcher', label: 'Researcher' },
  { value: 'reviewer', label: 'Reviewer' },
];

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '8px 10px',
  borderRadius: 8,
  border: '1px solid var(--pc-border)',
  background: 'var(--pc-bg-input)',
  color: 'var(--pc-text-primary)',
  fontSize: 12.5,
  outline: 'none',
};

const monoInputStyle: React.CSSProperties = {
  ...inputStyle,
  fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 10.5,
  fontWeight: 600,
  color: 'var(--construct-text-faint)',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  marginBottom: 6,
};

export default function NewPoolAgentModal({ open, onClose, onCreated }: Props) {
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState('');
  // Track whether the user has manually edited the slug — once they have,
  // stop auto-deriving it from `name`.
  const [slugTouched, setSlugTouched] = useState(false);
  const [slug, setSlug] = useState('');
  const [agentType, setAgentType] = useState<'claude' | 'codex'>('claude');
  const [role, setRole] = useState('coder');
  const [model, setModel] = useState('');
  const [identity, setIdentity] = useState('');
  const [systemHint, setSystemHint] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset state on open.
  useEffect(() => {
    if (!open) return;
    setName('');
    setSlug('');
    setSlugTouched(false);
    setAgentType('claude');
    setRole('coder');
    setModel('');
    setIdentity('');
    setSystemHint('');
    setSubmitting(false);
    setError(null);
    requestAnimationFrame(() => nameRef.current?.focus());
  }, [open]);

  // Auto-slugify name → slug until the user types into slug.
  useEffect(() => {
    if (slugTouched) return;
    setSlug(name ? slugify(name, 'agent') : '');
  }, [name, slugTouched]);

  // Esc closes (unless mid-request).
  useEffect(() => {
    if (!open) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        if (!submitting) onClose();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose, submitting]);

  const canSubmit = useMemo(
    () => Boolean(name.trim() && slug.trim() && role.trim() && !submitting),
    [name, slug, role, submitting],
  );

  if (!open) return null;
  if (typeof document === 'undefined') return null;

  const handleBackdropClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget && !submitting) {
      onClose();
    }
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const body: AgentCreateRequest = {
        // The backend `name` is the human display name; the gateway derives
        // `item_name` from it on the server. Pass slugified name to keep
        // both fields tied to the user's intent.
        name: slug.trim(),
        identity: identity.trim() || name.trim(),
        soul: '',
        expertise: [],
        tone: '',
        role: role.trim(),
        agent_type: agentType,
        model: model.trim() || undefined,
        system_hint: systemHint.trim() || undefined,
      };
      const created = await createAgent(body);
      await onCreated(created);
    } catch (err) {
      let message = 'Failed to create agent';
      if (err instanceof ApiError) {
        message = err.message.replace(/^API \d+: /, '') || message;
      } else if (err instanceof Error) {
        message = err.message;
      }
      setError(message);
      setSubmitting(false);
    }
  };

  const content = (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Create pool agent"
      onClick={handleBackdropClick}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: MODAL_BACKDROP_Z,
        background: 'rgba(0, 0, 0, 0.48)',
        backdropFilter: 'blur(6px)',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        paddingTop: '12vh',
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="construct-panel"
        data-variant="primary"
        style={{
          width: 'min(560px, calc(100vw - 32px))',
          maxHeight: 'calc(100vh - 24vh)',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 14,
          borderColor: 'var(--construct-border-strong)',
          boxShadow: '0 32px 80px rgba(0,0,0,0.48)',
          zIndex: MODAL_PANEL_Z,
          overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div
          style={{
            padding: '14px 16px',
            borderBottom: '1px solid var(--construct-border-soft)',
            display: 'flex',
            alignItems: 'center',
            gap: 10,
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
            }}
          >
            <Bot size={15} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--construct-text-primary)' }}>
              New pool agent
            </div>
            <div style={{ fontSize: 11, color: 'var(--construct-text-faint)' }}>
              Create a reusable agent for workflow steps
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              if (!submitting) onClose();
            }}
            disabled={submitting}
            aria-label="Close"
            style={{
              background: 'transparent',
              border: 0,
              padding: 4,
              cursor: submitting ? 'not-allowed' : 'pointer',
              color: 'var(--construct-text-faint)',
              opacity: submitting ? 0.5 : 1,
            }}
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div
          style={{
            padding: '14px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            overflowY: 'auto',
          }}
        >
          {/* Name */}
          <div>
            <label style={labelStyle} htmlFor="new-agent-name">
              Name *
            </label>
            <input
              id="new-agent-name"
              ref={nameRef}
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Senior Rust reviewer"
              style={inputStyle}
              disabled={submitting}
            />
          </div>

          {/* Slug */}
          <div>
            <label style={labelStyle} htmlFor="new-agent-slug">
              Item name (slug) *
            </label>
            <input
              id="new-agent-slug"
              type="text"
              value={slug}
              onChange={(e) => {
                setSlug(slugify(e.target.value, 'agent'));
                setSlugTouched(true);
              }}
              placeholder="senior-rust-reviewer"
              style={monoInputStyle}
              disabled={submitting}
            />
          </div>

          {/* Agent type — segmented control */}
          <div>
            <label style={labelStyle}>Agent type</label>
            <div
              role="radiogroup"
              aria-label="Agent type"
              style={{
                display: 'inline-flex',
                borderRadius: 8,
                border: '1px solid var(--pc-border)',
                background: 'var(--pc-bg-input)',
                padding: 2,
              }}
            >
              {(['claude', 'codex'] as const).map((opt) => {
                const selected = agentType === opt;
                return (
                  <button
                    key={opt}
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setAgentType(opt)}
                    disabled={submitting}
                    style={{
                      padding: '5px 14px',
                      fontSize: 11.5,
                      fontWeight: 600,
                      borderRadius: 6,
                      border: 0,
                      cursor: submitting ? 'not-allowed' : 'pointer',
                      background: selected ? 'var(--pc-accent-glow)' : 'transparent',
                      color: selected ? 'var(--pc-accent)' : 'var(--construct-text-secondary)',
                      textTransform: 'capitalize',
                    }}
                  >
                    {opt}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Role */}
          <div>
            <label style={labelStyle} htmlFor="new-agent-role">
              Role
            </label>
            <input
              id="new-agent-role"
              type="text"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              list="new-agent-role-options"
              placeholder="coder"
              style={inputStyle}
              disabled={submitting}
            />
            <datalist id="new-agent-role-options">
              {ROLE_OPTIONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </datalist>
          </div>

          {/* Model override */}
          <div>
            <label style={labelStyle} htmlFor="new-agent-model">
              Model override
            </label>
            <input
              id="new-agent-model"
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="claude-opus-4-7 (optional)"
              style={monoInputStyle}
              disabled={submitting}
            />
          </div>

          {/* Prompt template / system_hint */}
          <div>
            <label style={labelStyle} htmlFor="new-agent-prompt">
              Prompt template
            </label>
            <textarea
              id="new-agent-prompt"
              value={systemHint}
              onChange={(e) => setSystemHint(e.target.value)}
              placeholder="System prompt or guidance for this agent…"
              rows={5}
              style={{
                ...monoInputStyle,
                resize: 'vertical',
                minHeight: 80,
              }}
              disabled={submitting}
            />
          </div>

          {/* Description / notes (identity in API terms) */}
          <div>
            <label style={labelStyle} htmlFor="new-agent-identity">
              Description / notes
            </label>
            <textarea
              id="new-agent-identity"
              value={identity}
              onChange={(e) => setIdentity(e.target.value)}
              placeholder="Senior engineer focused on Rust systems work…"
              rows={2}
              style={{ ...inputStyle, resize: 'vertical', minHeight: 50 }}
              disabled={submitting}
            />
          </div>

          {error && (
            <div
              role="alert"
              style={{
                padding: '8px 10px',
                borderRadius: 8,
                background: 'color-mix(in srgb, var(--construct-status-danger) 14%, transparent)',
                border: '1px solid var(--construct-status-danger)',
                color: 'var(--construct-status-danger)',
                fontSize: 11.5,
              }}
            >
              {error}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '12px 16px',
            borderTop: '1px solid var(--construct-border-soft)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: 8,
          }}
        >
          <button
            type="button"
            onClick={() => {
              if (!submitting) onClose();
            }}
            disabled={submitting}
            className="construct-button"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="construct-button"
            data-variant="primary"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            {submitting && <Loader2 size={13} className="animate-spin" />}
            {submitting ? 'Creating…' : 'Create agent'}
          </button>
        </div>
      </form>
    </div>
  );

  return createPortal(content, document.body);
}
