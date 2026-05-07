/**
 * ExpressionTextarea — drop-in <textarea> with DAG-aware ${...} autocomplete.
 *
 * When the user types `${`, a popover opens anchored to the caret with
 * suggestions for step IDs (with `.output`, `.status`, `.error` suffixes),
 * workflow inputs (`${input.X}`), trigger fields (`${trigger.X}`), and a
 * generic `${env.<name>}` template.
 *
 * Caret pixel position is computed via a hidden mirror <div> that copies the
 * textarea's computed style + content up to the caret; the popover anchors to
 * the bottom-right of that point and is portaled to document.body so it
 * escapes side-panel clipping (same pattern as P1.5a's pickers).
 *
 * Insert behavior: replaces the active `${<fragment>` from the most recent
 * `${` up to the caret with the chosen `insert` string. The closing `}` is
 * intentionally NOT auto-added so users can chain
 * (`${foo.output}.${bar.output}`); they type the `}` themselves.
 *
 * Tokens-only — no hex literals.
 */

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';

const POPOVER_Z = 9100;
const MAX_SUGGESTIONS = 12;

// Popover sizing / margin constants used both in the inline style below and
// in the viewport-clamp logic that keeps the popover on-screen when the
// caret is near a viewport edge.
const POPOVER_MIN_W = 240;
const POPOVER_MAX_W = 360;
const POPOVER_MAX_H = 280;
const POPOVER_VIEWPORT_MARGIN = 8;

type Group = 'Steps' | 'Inputs' | 'Trigger' | 'Env';

interface Suggestion {
  insert: string;
  label: string;
  group: Group;
}

interface Props {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  rows?: number;
  style?: React.CSSProperties;
  /** All step IDs available in the current workflow DAG. */
  stepIds: string[];
  /** Workflow-level inputs (declared in the `inputs:` block). */
  workflowInputs?: string[];
  /** Trigger fields available in the trigger context. */
  triggerFields?: string[];
}

// ---------------------------------------------------------------------------
// Suggestion sources
// ---------------------------------------------------------------------------

function buildSuggestions(
  stepIds: string[],
  workflowInputs: string[],
  triggerFields: string[],
): Suggestion[] {
  const items: Suggestion[] = [];

  // Steps: bare id + .output/.status/.error
  for (const id of stepIds) {
    if (!id) continue;
    items.push({ insert: `\${${id}}`, label: `\${${id}}`, group: 'Steps' });
    items.push({
      insert: `\${${id}.output}`,
      label: `\${${id}.output}`,
      group: 'Steps',
    });
    items.push({
      insert: `\${${id}.status}`,
      label: `\${${id}.status}`,
      group: 'Steps',
    });
    items.push({
      insert: `\${${id}.error}`,
      label: `\${${id}.error}`,
      group: 'Steps',
    });
  }

  // Workflow inputs: ${input.X}
  for (const name of workflowInputs) {
    if (!name) continue;
    items.push({
      insert: `\${input.${name}}`,
      label: `\${input.${name}}`,
      group: 'Inputs',
    });
  }

  // Trigger fields: ${trigger.X}
  for (const field of triggerFields) {
    if (!field) continue;
    items.push({
      insert: `\${trigger.${field}}`,
      label: `\${trigger.${field}}`,
      group: 'Trigger',
    });
  }

  // Generic env template
  items.push({
    insert: `\${env.<name>}`,
    label: `\${env.<name>}`,
    group: 'Env',
  });

  return items;
}

/** Substring-based filter against the lowercased fragment (the chars typed
 *  after the most recent `${`). Empty fragment matches all. */
function filterSuggestions(items: Suggestion[], fragment: string): Suggestion[] {
  if (!fragment) return items.slice(0, MAX_SUGGESTIONS);
  const needle = fragment.toLowerCase();
  // Score: prefix match on the part after `${` beats inner match.
  const scored = items
    .map((s) => {
      // Drop the `${` from the label for matching against the fragment.
      const inner = s.label.replace(/^\$\{/, '').toLowerCase();
      let score = -1;
      if (inner.startsWith(needle)) score = 2;
      else if (inner.includes(needle)) score = 1;
      return { s, score };
    })
    .filter((x) => x.score >= 0)
    .sort((a, b) => b.score - a.score);
  return scored.slice(0, MAX_SUGGESTIONS).map((x) => x.s);
}

// ---------------------------------------------------------------------------
// Caret-position via hidden mirror div
// ---------------------------------------------------------------------------

/** Computed style props that affect text layout — copied verbatim from the
 *  textarea onto the mirror so caret math matches across themes/fonts. */
const MIRROR_STYLE_PROPS = [
  'boxSizing',
  'width',
  'height',
  'overflowX',
  'overflowY',
  'borderTopWidth',
  'borderRightWidth',
  'borderBottomWidth',
  'borderLeftWidth',
  'borderStyle',
  'paddingTop',
  'paddingRight',
  'paddingBottom',
  'paddingLeft',
  'fontStyle',
  'fontVariant',
  'fontWeight',
  'fontStretch',
  'fontSize',
  'fontSizeAdjust',
  'lineHeight',
  'fontFamily',
  'textAlign',
  'textTransform',
  'textIndent',
  'textDecoration',
  'letterSpacing',
  'wordSpacing',
  'tabSize',
  'whiteSpace',
  'wordBreak',
  'overflowWrap',
] as const;

interface CaretPos {
  /** Page x of the caret (relative to viewport). */
  left: number;
  /** Page y of the caret bottom (relative to viewport). */
  bottom: number;
}

/** Compute caret pixel coordinates by rendering a mirror div with the
 *  textarea's content up to the caret + a sentinel <span>, reading the
 *  span's bounding rect, and adjusting for the textarea's own rect/scroll. */
function computeCaretPosition(
  textarea: HTMLTextAreaElement,
  caretIndex: number,
): CaretPos {
  const doc = textarea.ownerDocument;
  const mirror = doc.createElement('div');
  const computed = window.getComputedStyle(textarea);

  for (const prop of MIRROR_STYLE_PROPS) {
    (mirror.style as any)[prop] = (computed as any)[prop] ?? '';
  }
  mirror.style.position = 'absolute';
  mirror.style.visibility = 'hidden';
  mirror.style.top = '0';
  mirror.style.left = '0';
  mirror.style.whiteSpace = 'pre-wrap';
  mirror.style.wordWrap = 'break-word';

  const before = textarea.value.substring(0, caretIndex);
  mirror.textContent = before;

  const sentinel = doc.createElement('span');
  // Zero-width sentinel — gives us a measurable rect at the caret.
  sentinel.textContent = '​';
  mirror.appendChild(sentinel);

  doc.body.appendChild(mirror);
  const sentinelRect = sentinel.getBoundingClientRect();
  const mirrorRect = mirror.getBoundingClientRect();
  doc.body.removeChild(mirror);

  const taRect = textarea.getBoundingClientRect();
  // sentinelRect is positioned relative to the mirror at (0,0); adjust into
  // textarea-local coords, then add textarea's viewport position. Subtract
  // textarea scroll so the popover follows scrolled content correctly.
  const localX = sentinelRect.left - mirrorRect.left;
  const localBottom = sentinelRect.bottom - mirrorRect.top;

  return {
    left: taRect.left + localX - textarea.scrollLeft,
    bottom: taRect.top + localBottom - textarea.scrollTop,
  };
}

interface ClampedPos {
  left: number;
  top: number;
}

/** Clamp a caret-anchored popover position so it stays inside the viewport.
 *  When there's not enough room below the caret to fit the popover, flip it
 *  above the caret instead. Estimates with the popover's MAX dimensions so we
 *  never clip even if the rendered popover hits the upper bound. */
export function clampPopoverPosition(
  caret: CaretPos,
  viewport: { width: number; height: number },
): ClampedPos {
  const { width: vw, height: vh } = viewport;
  const clampedLeft = Math.max(
    POPOVER_VIEWPORT_MARGIN,
    Math.min(caret.left, vw - POPOVER_MAX_W - POPOVER_VIEWPORT_MARGIN),
  );
  const wantBottom = caret.bottom + 4;
  const flipUp = wantBottom + POPOVER_MAX_H + POPOVER_VIEWPORT_MARGIN > vh;
  const clampedTop = flipUp
    ? Math.max(POPOVER_VIEWPORT_MARGIN, caret.bottom - 4 - POPOVER_MAX_H)
    : Math.min(wantBottom, vh - POPOVER_MAX_H - POPOVER_VIEWPORT_MARGIN);
  return { left: clampedLeft, top: clampedTop };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/** Find the most-recent unclosed `${` strictly before `caretIndex`. Returns
 *  the index of the `$` in `${`, or -1 if no active fragment. An intervening
 *  `}` after the `${` cancels it (closed expression). */
function findActiveDollarBrace(value: string, caretIndex: number): number {
  // Walk backwards from caret to find `${` not followed by `}` before caret.
  for (let i = caretIndex - 2; i >= 0; i--) {
    const ch = value[i];
    if (ch === '}') return -1; // caret is past a closed expression
    if (ch === '$' && value[i + 1] === '{') {
      return i;
    }
  }
  return -1;
}

export default function ExpressionTextarea({
  value,
  onChange,
  placeholder,
  rows,
  style,
  stepIds,
  workflowInputs = [],
  triggerFields = [],
}: Props) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [open, setOpen] = useState(false);
  const [fragment, setFragment] = useState('');
  /** Index of the `$` of the active `${` so we can replace from there. */
  const dollarIndexRef = useRef<number>(-1);
  const [activeIndex, setActiveIndex] = useState(0);
  const [caretPos, setCaretPos] = useState<CaretPos>({ left: 0, bottom: 0 });
  /** Viewport size, tracked so clampPopoverPosition recomputes on resize. */
  const [viewport, setViewport] = useState<{ width: number; height: number }>(
    () =>
      typeof window !== 'undefined'
        ? { width: window.innerWidth, height: window.innerHeight }
        : { width: 0, height: 0 },
  );

  const allSuggestions = useMemo(
    () => buildSuggestions(stepIds, workflowInputs, triggerFields),
    [stepIds, workflowInputs, triggerFields],
  );

  const filtered = useMemo(
    () => filterSuggestions(allSuggestions, fragment),
    [allSuggestions, fragment],
  );

  // Reset selection when the filter changes.
  useEffect(() => {
    setActiveIndex(0);
  }, [fragment, open]);

  /** Re-evaluate whether the popover should be open based on the current
   *  caret position. Updates fragment + caret pixel pos. */
  const refreshFromCaret = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    const caret = ta.selectionStart ?? 0;
    const dollarIdx = findActiveDollarBrace(ta.value, caret);
    if (dollarIdx < 0) {
      dollarIndexRef.current = -1;
      setOpen(false);
      return;
    }
    // Fragment is the text between `${` and the caret.
    const frag = ta.value.substring(dollarIdx + 2, caret);
    // Bail if the fragment contains characters that clearly aren't part of a
    // simple identifier path (e.g. whitespace, newline). Keeps the popover
    // from following the user out of the expression.
    if (/[\s\n\r]/.test(frag)) {
      dollarIndexRef.current = -1;
      setOpen(false);
      return;
    }
    dollarIndexRef.current = dollarIdx;
    setFragment(frag);
    setCaretPos(computeCaretPosition(ta, caret));
    setOpen(true);
  }, []);

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange(e.target.value);
      // Schedule a refresh after React applies the new value so caret math
      // sees the up-to-date textarea content.
      requestAnimationFrame(() => refreshFromCaret());
    },
    [onChange, refreshFromCaret],
  );

  /** Insert the chosen suggestion: replace `${<fragment>` with the suggestion
   *  string, place caret immediately after the inserted text. */
  const insertSuggestion = useCallback(
    (suggestion: Suggestion) => {
      const ta = textareaRef.current;
      if (!ta) return;
      const dollarIdx = dollarIndexRef.current;
      if (dollarIdx < 0) return;
      const caret = ta.selectionStart ?? 0;
      const before = ta.value.substring(0, dollarIdx);
      const after = ta.value.substring(caret);
      const next = before + suggestion.insert + after;
      const nextCaret = before.length + suggestion.insert.length;
      onChange(next);
      // Restore caret on the next frame after React re-renders the value.
      requestAnimationFrame(() => {
        const t = textareaRef.current;
        if (!t) return;
        t.setSelectionRange(nextCaret, nextCaret);
        t.focus();
      });
      setOpen(false);
    },
    [onChange],
  );

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (!open || filtered.length === 0) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % filtered.length);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + filtered.length) % filtered.length);
      } else if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        const choice = filtered[activeIndex];
        if (choice) insertSuggestion(choice);
      } else if (e.key === 'Escape') {
        e.preventDefault();
        setOpen(false);
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        // Caret moved; close — refresh on next tick will reopen if still in `${`.
        setOpen(false);
      }
    },
    [open, filtered, activeIndex, insertSuggestion],
  );

  const handleSelect = useCallback(() => {
    // Selection / arrow / mouse caret moves — re-evaluate.
    refreshFromCaret();
  }, [refreshFromCaret]);

  const handleBlur = useCallback(() => {
    // Popover items use `onMouseDown={(e) => e.preventDefault()}` to keep
    // focus on the textarea, so blur during a click is already prevented —
    // close synchronously here.
    setOpen(false);
  }, []);

  // Reposition popover + refresh viewport size when window resizes / scrolls
  // while open. Both feed into clampPopoverPosition so the popover stays on
  // screen even when the caret is near the right/bottom viewport edge.
  useLayoutEffect(() => {
    if (!open) return;
    const onScrollOrResize = () => {
      const ta = textareaRef.current;
      if (!ta) return;
      const caret = ta.selectionStart ?? 0;
      setCaretPos(computeCaretPosition(ta, caret));
      setViewport({ width: window.innerWidth, height: window.innerHeight });
    };
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
    };
  }, [open]);

  // Clamped popover position — recomputes when caret moves or viewport changes.
  const clampedPos = useMemo(
    () => clampPopoverPosition(caretPos, viewport),
    [caretPos, viewport],
  );

  // Group filtered suggestions for rendering, preserving filter order.
  const grouped = useMemo(() => {
    const order: Group[] = ['Steps', 'Inputs', 'Trigger', 'Env'];
    const buckets = new Map<Group, Suggestion[]>();
    for (const s of filtered) {
      const arr = buckets.get(s.group) ?? [];
      arr.push(s);
      buckets.set(s.group, arr);
    }
    return order
      .map((g) => ({ group: g, items: buckets.get(g) ?? [] }))
      .filter((b) => b.items.length > 0);
  }, [filtered]);

  // Map a flat (group,item) coordinate to the activeIndex over the filtered
  // list so keyboard nav lines up with rendered item order.
  const flatIndexOf = useCallback(
    (suggestion: Suggestion): number => filtered.indexOf(suggestion),
    [filtered],
  );

  const popover =
    open && filtered.length > 0 && typeof document !== 'undefined'
      ? createPortal(
          <div
            // Mousedown inside the popover should not blur the textarea — but
            // since the textarea's blur handler defers, we also stop propagation
            // here for safety.
            onMouseDown={(e) => e.preventDefault()}
            style={{
              position: 'fixed',
              left: clampedPos.left,
              top: clampedPos.top,
              zIndex: POPOVER_Z,
              minWidth: POPOVER_MIN_W,
              maxWidth: POPOVER_MAX_W,
              maxHeight: POPOVER_MAX_H,
              overflowY: 'auto',
              background: 'var(--pc-bg-elevated)',
              border: '1px solid var(--pc-border)',
              borderRadius: 10,
              boxShadow: '0 12px 32px rgba(0,0,0,0.32)',
              padding: 4,
              fontSize: 12,
              color: 'var(--pc-text-primary)',
            }}
          >
            {grouped.map(({ group, items }) => (
              <div key={group} style={{ marginBottom: 2 }}>
                <div
                  style={{
                    fontSize: 9,
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    letterSpacing: '0.12em',
                    color: 'var(--pc-text-faint)',
                    padding: '6px 8px 2px',
                  }}
                >
                  {group}
                </div>
                {items.map((s) => {
                  const idx = flatIndexOf(s);
                  const isActive = idx === activeIndex;
                  return (
                    <div
                      key={s.label}
                      role="option"
                      aria-selected={isActive}
                      onMouseEnter={() => setActiveIndex(idx)}
                      onMouseDown={(e) => {
                        // Prevent blur before click fires.
                        e.preventDefault();
                        insertSuggestion(s);
                      }}
                      style={{
                        padding: '6px 8px',
                        borderRadius: 6,
                        cursor: 'pointer',
                        fontFamily:
                          'var(--pc-font-mono, ui-monospace, monospace)',
                        background: isActive
                          ? 'var(--pc-accent-glow)'
                          : 'transparent',
                        boxShadow: isActive
                          ? 'inset 2px 0 0 var(--pc-accent)'
                          : 'none',
                        color: isActive
                          ? 'var(--pc-text-primary)'
                          : 'var(--pc-text-secondary)',
                      }}
                    >
                      {s.label}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onSelect={handleSelect}
        onClick={handleSelect}
        onBlur={handleBlur}
        placeholder={placeholder}
        rows={rows}
        style={style}
      />
      {popover}
    </>
  );
}
