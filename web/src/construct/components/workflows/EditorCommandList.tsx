/**
 * EditorCommandList — empty-canvas overlay for the workflow editor.
 *
 * Shows the Construct lockup and three primary actions with keyboard shortcuts:
 *   ⌘K — Add Step
 *   ⌘I — Import YAML
 *   ⌘G — Generate from prompt (opens Architect with a pre-filled input)
 */

import { Plus, FileUp, Sparkles } from 'lucide-react';
import { appAssetPath } from '@/lib/basePath';

interface Props {
  onAddStep: () => void;
  onImportYaml: () => void;
  /** Open the Architect panel with a pre-filled "describe the workflow…"
   *  prompt. When omitted, the row is disabled (legacy behavior). */
  onGenerate?: () => void;
  /** Mac vs non-mac shortcut prefix; default detects from navigator. */
  modKey?: string;
}

const isMac = typeof navigator !== 'undefined' && /mac/i.test(navigator.platform);

function ShortcutChip({ keys }: { keys: string[] }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      {keys.map((k) => (
        <kbd
          key={k}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            minWidth: 22,
            height: 22,
            padding: '0 6px',
            borderRadius: 6,
            border: '1px solid var(--construct-border-soft)',
            background: 'var(--pc-bg-input)',
            color: 'var(--construct-text-secondary)',
            fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          {k}
        </kbd>
      ))}
    </span>
  );
}

export default function EditorCommandList({
  onAddStep,
  onImportYaml,
  onGenerate,
  modKey,
}: Props) {
  const mod = modKey ?? (isMac ? '⌘' : 'Ctrl');
  const generateDisabled = !onGenerate;

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        pointerEvents: 'none',
      }}
    >
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 18,
          maxWidth: 360,
          padding: '24px 28px',
          pointerEvents: 'auto',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 10 }}>
          <img
            src={appAssetPath('construct-trans.png')}
            alt="Construct"
            style={{
              height: 56,
              width: 56,
              borderRadius: 14,
              objectFit: 'cover',
              opacity: 0.85,
            }}
            onError={(e) => {
              (e.currentTarget as HTMLImageElement).style.display = 'none';
            }}
          />
          <div className="construct-kicker" style={{ letterSpacing: '0.24em' }}>
            Workflow Editor
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, width: '100%' }}>
          <button
            type="button"
            onClick={onAddStep}
            style={commandRowStyle()}
          >
            <span style={iconWrapStyle()}>
              <Plus size={14} />
            </span>
            <span style={{ flex: 1, textAlign: 'left' }}>Add Step</span>
            <ShortcutChip keys={[mod, 'K']} />
          </button>

          <button
            type="button"
            onClick={onImportYaml}
            style={commandRowStyle()}
          >
            <span style={iconWrapStyle()}>
              <FileUp size={14} />
            </span>
            <span style={{ flex: 1, textAlign: 'left' }}>Import YAML</span>
            <ShortcutChip keys={[mod, 'I']} />
          </button>

          <button
            type="button"
            onClick={onGenerate}
            disabled={generateDisabled}
            title={generateDisabled ? 'Coming with Operator copilot (P2)' : 'Open Architect with a prompt'}
            style={
              generateDisabled
                ? { ...commandRowStyle(), opacity: 0.5, cursor: 'not-allowed' }
                : commandRowStyle()
            }
          >
            <span style={iconWrapStyle()}>
              <Sparkles size={14} />
            </span>
            <span style={{ flex: 1, textAlign: 'left' }}>Generate from prompt</span>
            {generateDisabled ? (
              <span
                style={{
                  fontSize: 10,
                  color: 'var(--construct-text-faint)',
                  letterSpacing: '0.12em',
                  textTransform: 'uppercase',
                }}
              >
                soon
              </span>
            ) : (
              <ShortcutChip keys={[mod, 'G']} />
            )}
          </button>
        </div>

        <p
          style={{
            fontSize: 11,
            color: 'var(--construct-text-faint)',
            textAlign: 'center',
            lineHeight: 1.5,
          }}
        >
          Right-click the canvas for more actions, or drag a noodle from a node into empty space to insert and wire a step.
        </p>
      </div>
    </div>
  );
}

function commandRowStyle(): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: 12,
    padding: '10px 12px',
    borderRadius: 10,
    border: '1px solid var(--construct-border-soft)',
    background: 'color-mix(in srgb, var(--construct-bg-panel-strong) 78%, transparent)',
    color: 'var(--construct-text-secondary)',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'border-color 0.15s ease, color 0.15s ease, background 0.15s ease',
  };
}

function iconWrapStyle(): React.CSSProperties {
  return {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 22,
    height: 22,
    borderRadius: 6,
    background: 'var(--pc-accent-glow)',
    color: 'var(--pc-accent)',
    flexShrink: 0,
  };
}
