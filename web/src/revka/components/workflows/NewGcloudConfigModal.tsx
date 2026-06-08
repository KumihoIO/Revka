/**
 * NewGcloudConfigModal - local Cloud SDK configuration creation for private
 * Cloud Run A2A workflow steps.
 *
 * This creates metadata in gcloud's config store. It does not collect or
 * return token material; runtime token minting happens through
 * `gcloud --configuration=<name> auth print-identity-token`.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Cloud, Loader2, X } from 'lucide-react';
import { ApiError, createGcloudConfig } from '@/lib/api';
import type { GcloudConfigSummary } from '@/types/api';
import { slugify } from './slugify';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: (name: string) => void | Promise<void>;
  defaultAccount?: string | null;
  defaultProject?: string | null;
  defaultRunRegion?: string | null;
  defaultComputeRegion?: string | null;
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
  color: 'var(--revka-text-faint)',
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  marginBottom: 6,
};

export default function NewGcloudConfigModal({
  open,
  onClose,
  onCreated,
  defaultAccount,
  defaultProject,
  defaultRunRegion,
  defaultComputeRegion,
}: Props) {
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState('');
  const [account, setAccount] = useState('');
  const [project, setProject] = useState('');
  const [runRegion, setRunRegion] = useState('');
  const [computeRegion, setComputeRegion] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setName('revka-cloud-run');
    setAccount(defaultAccount ?? '');
    setProject(defaultProject ?? '');
    setRunRegion(defaultRunRegion ?? '');
    setComputeRegion(defaultComputeRegion ?? defaultRunRegion ?? '');
    setSubmitting(false);
    setError(null);
    requestAnimationFrame(() => nameRef.current?.focus());
  }, [open, defaultAccount, defaultProject, defaultRunRegion, defaultComputeRegion]);

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
    () => Boolean(name.trim() && project.trim() && !submitting),
    [name, project, submitting],
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

    const configName = slugify(name, 'revka-cloud-run');

    try {
      const created: GcloudConfigSummary = await createGcloudConfig({
        name: configName,
        project: project.trim(),
        account: account.trim() || undefined,
        run_region: runRegion.trim() || undefined,
        compute_region: computeRegion.trim() || undefined,
      });
      await onCreated(created.name);
    } catch (err) {
      let message = 'Failed to create gcloud config';
      if (err instanceof ApiError) {
        if (err.status === 409) {
          message = 'A gcloud configuration with that name already exists';
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
      aria-label="Create gcloud config"
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
        className="revka-panel"
        data-variant="primary"
        style={{
          width: 'min(540px, calc(100vw - 32px))',
          maxHeight: 'calc(100vh - 24vh)',
          display: 'flex',
          flexDirection: 'column',
          borderRadius: 14,
          borderColor: 'var(--revka-border-strong)',
          boxShadow: '0 32px 80px rgba(0,0,0,0.48)',
          zIndex: MODAL_PANEL_Z,
          overflow: 'hidden',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div
          style={{
            padding: '14px 16px',
            borderBottom: '1px solid var(--revka-border-soft)',
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
            <Cloud size={15} />
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--revka-text-primary)' }}>
              New gcloud config
            </div>
            <div style={{ fontSize: 11, color: 'var(--revka-text-faint)' }}>
              Uses the local Cloud SDK credential store. No token bytes are saved to the workflow.
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
              color: 'var(--revka-text-faint)',
              opacity: submitting ? 0.5 : 1,
            }}
          >
            <X size={16} />
          </button>
        </div>

        <div
          style={{
            padding: '14px 16px',
            display: 'flex',
            flexDirection: 'column',
            gap: 12,
            overflowY: 'auto',
          }}
        >
          <div>
            <label style={labelStyle} htmlFor="new-gcloud-config-name">
              Config name *
            </label>
            <input
              id="new-gcloud-config-name"
              ref={nameRef}
              type="text"
              value={name}
              onChange={(e) => setName(slugify(e.target.value, 'revka-cloud-run'))}
              placeholder="revka-cloud-run"
              style={monoInputStyle}
              disabled={submitting}
              autoComplete="off"
            />
          </div>

          <div>
            <label style={labelStyle} htmlFor="new-gcloud-account">
              Account
            </label>
            <input
              id="new-gcloud-account"
              type="text"
              value={account}
              onChange={(e) => setAccount(e.target.value)}
              placeholder="support@example.com"
              style={monoInputStyle}
              disabled={submitting}
              autoComplete="off"
            />
          </div>

          <div>
            <label style={labelStyle} htmlFor="new-gcloud-project">
              Project *
            </label>
            <input
              id="new-gcloud-project"
              type="text"
              value={project}
              onChange={(e) => setProject(e.target.value)}
              placeholder="construct-498201"
              style={monoInputStyle}
              disabled={submitting}
              autoComplete="off"
            />
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
            <div>
              <label style={labelStyle} htmlFor="new-gcloud-run-region">
                Cloud Run region
              </label>
              <input
                id="new-gcloud-run-region"
                type="text"
                value={runRegion}
                onChange={(e) => setRunRegion(e.target.value)}
                placeholder="us-central1"
                style={monoInputStyle}
                disabled={submitting}
                autoComplete="off"
              />
            </div>
            <div>
              <label style={labelStyle} htmlFor="new-gcloud-compute-region">
                Compute region
              </label>
              <input
                id="new-gcloud-compute-region"
                type="text"
                value={computeRegion}
                onChange={(e) => setComputeRegion(e.target.value)}
                placeholder="us-central1"
                style={monoInputStyle}
                disabled={submitting}
                autoComplete="off"
              />
            </div>
          </div>

          {error && (
            <div
              role="alert"
              style={{
                padding: '8px 10px',
                borderRadius: 8,
                background: 'color-mix(in srgb, var(--revka-status-danger) 14%, transparent)',
                border: '1px solid var(--revka-status-danger)',
                color: 'var(--revka-status-danger)',
                fontSize: 11.5,
              }}
            >
              {error}
            </div>
          )}
        </div>

        <div
          style={{
            padding: '12px 16px',
            borderTop: '1px solid var(--revka-border-soft)',
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
            className="revka-button"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={!canSubmit}
            className="revka-button"
            data-variant="primary"
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            {submitting && <Loader2 size={13} className="animate-spin" />}
            {submitting ? 'Creating...' : 'Create config'}
          </button>
        </div>
      </form>
    </div>
  );

  return createPortal(content, document.body);
}
