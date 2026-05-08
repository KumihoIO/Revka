/**
 * NewAuthProfileModal — inline static-token / API-key auth-profile creation
 * surface, opened from the AuthProfilePicker footer.
 *
 * Posts to `POST /api/auth/profiles` (handled by
 * `src/gateway/api_auth_profiles.rs::handle_create_auth_profile`). The token
 * is encrypted on the backend via `SecretStore`; this modal never echoes it
 * back. Only static-token / API-key flows are supported here — OAuth is
 * gated to the existing /config flow.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Eye, EyeOff, Loader2, Lock, X } from 'lucide-react';
import { ApiError, createAuthProfile } from '@/lib/api';
import type { AuthProfileSummary } from '@/types/api';
import { PROVIDER_LABELS } from './providerLabels';
import { slugify } from './slugify';

interface Props {
  open: boolean;
  onClose: () => void;
  /** Called on successful POST with the new profile id (e.g. `openai:work`). */
  onCreated: (id: string) => void | Promise<void>;
}

const MODAL_BACKDROP_Z = 9099;
const MODAL_PANEL_Z = 9100;

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

export default function NewAuthProfileModal({ open, onClose, onCreated }: Props) {
  const providerRef = useRef<HTMLInputElement>(null);
  const [provider, setProvider] = useState('');
  const [profileName, setProfileName] = useState('');
  const [profileNameTouched, setProfileNameTouched] = useState(false);
  const [token, setToken] = useState('');
  const [accountId, setAccountId] = useState('');
  const [revealToken, setRevealToken] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setProvider('');
    setProfileName('');
    setProfileNameTouched(false);
    setToken('');
    setAccountId('');
    setRevealToken(false);
    setSubmitting(false);
    setError(null);
    requestAnimationFrame(() => providerRef.current?.focus());
  }, [open]);

  // Auto-default profile name to "default" once provider is set, until the
  // user explicitly edits it.
  useEffect(() => {
    if (profileNameTouched) return;
    if (provider.trim() && !profileName) {
      setProfileName('default');
    }
  }, [provider, profileName, profileNameTouched]);

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
    () =>
      Boolean(
        provider.trim() && profileName.trim() && token && !submitting,
      ),
    [provider, profileName, token, submitting],
  );

  const providerOptions = useMemo(() => Object.entries(PROVIDER_LABELS), []);

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

    const slugProvider = slugify(provider, 'provider');
    const slugProfileName = slugify(profileName, 'default');

    try {
      const created: AuthProfileSummary = await createAuthProfile({
        provider: slugProvider,
        profile_name: slugProfileName,
        token,
        account_id: accountId.trim() || undefined,
        kind: 'token',
      });
      await onCreated(created.id);
    } catch (err) {
      let message = 'Failed to create auth profile';
      if (err instanceof ApiError) {
        if (err.status === 409) {
          message = 'A profile with that provider+name already exists';
        } else {
          message = err.message.replace(/^API \d+: /, '') || message;
        }
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
      aria-label="Create auth profile"
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
          width: 'min(540px, calc(100vw - 32px))',
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
            <Lock size={15} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--construct-text-primary)' }}>
              New auth profile
            </div>
            <div style={{ fontSize: 11, color: 'var(--construct-text-faint)' }}>
              Static token only — for OAuth providers, use the gateway&apos;s /config flow.
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
          {/* Provider */}
          <div>
            <label style={labelStyle} htmlFor="new-auth-provider">
              Provider *
            </label>
            <input
              id="new-auth-provider"
              ref={providerRef}
              type="text"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              list="new-auth-provider-options"
              placeholder="openai, anthropic, github…"
              style={monoInputStyle}
              disabled={submitting}
              autoComplete="off"
            />
            <datalist id="new-auth-provider-options">
              {providerOptions.map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </datalist>
          </div>

          {/* Profile name */}
          <div>
            <label style={labelStyle} htmlFor="new-auth-profile-name">
              Profile name *
            </label>
            <input
              id="new-auth-profile-name"
              type="text"
              value={profileName}
              onChange={(e) => {
                setProfileName(slugify(e.target.value, 'default'));
                setProfileNameTouched(true);
              }}
              placeholder="default"
              style={monoInputStyle}
              disabled={submitting}
              autoComplete="off"
            />
          </div>

          {/* Kind — display only */}
          <div>
            <label style={labelStyle}>Kind</label>
            <div
              style={{
                padding: '6px 10px',
                borderRadius: 8,
                border: '1px solid var(--pc-border)',
                background: 'var(--pc-bg-input)',
                color: 'var(--construct-text-secondary)',
                fontSize: 12,
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
              }}
            >
              API key / token
            </div>
          </div>

          {/* Token */}
          <div>
            <label style={labelStyle} htmlFor="new-auth-token">
              Token *
            </label>
            <div style={{ position: 'relative' }}>
              <input
                id="new-auth-token"
                type={revealToken ? 'text' : 'password'}
                value={token}
                onChange={(e) => setToken(e.target.value)}
                placeholder="sk-… / ghp_… / xoxb-…"
                style={{ ...monoInputStyle, paddingRight: 36 }}
                disabled={submitting}
                autoComplete="off"
                spellCheck={false}
              />
              <button
                type="button"
                onClick={() => setRevealToken((v) => !v)}
                aria-label={revealToken ? 'Hide token' : 'Show token'}
                disabled={submitting}
                style={{
                  position: 'absolute',
                  top: '50%',
                  right: 6,
                  transform: 'translateY(-50%)',
                  background: 'transparent',
                  border: 0,
                  padding: 4,
                  cursor: submitting ? 'not-allowed' : 'pointer',
                  color: 'var(--construct-text-faint)',
                }}
              >
                {revealToken ? <EyeOff size={14} /> : <Eye size={14} />}
              </button>
            </div>
          </div>

          {/* Account ID */}
          <div>
            <label style={labelStyle} htmlFor="new-auth-account-id">
              Account ID
            </label>
            <input
              id="new-auth-account-id"
              type="text"
              value={accountId}
              onChange={(e) => setAccountId(e.target.value)}
              placeholder="email or id, for your reference"
              style={inputStyle}
              disabled={submitting}
              autoComplete="off"
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
            {submitting ? 'Creating…' : 'Create profile'}
          </button>
        </div>
      </form>
    </div>
  );

  return createPortal(content, document.body);
}
