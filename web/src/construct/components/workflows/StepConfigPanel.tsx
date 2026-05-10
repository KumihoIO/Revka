/**
 * StepConfigPanel — right-rail node inspector for the workflow editor.
 *
 * Replaces legacy TaskSidePanel. Ported field-by-field, re-skinned to
 * Construct design tokens. All inputs use --pc-bg-input / --pc-border /
 * --pc-text-primary; section accents use --construct-status-* tokens.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Crosshair, Link2, Link2Off, Loader2, Lock, Search, Sparkles, Trash2, X } from 'lucide-react';
import type { Node } from '@xyflow/react';
import { type TaskNodeData } from '@/components/workflows/yamlSync';
import type { SkillDefinition } from '@/types/api';
import { fetchSkills, getChannels } from '@/lib/api';
import Panel from '@/construct/components/ui/Panel';
import { STEP_TYPES_BY_TYPE } from './stepRegistry';
import AuthProfilePicker from './AuthProfilePicker';
import { providerLabel } from './providerLabels';
import ExpressionTextarea from './ExpressionTextarea';
import { emitOpenAgentPicker } from './stepEvents';
import { useAuthProfiles } from './useAuthProfiles';
import { slugify as slugifyShared, uniqueSlug } from './slugify';

/** Step types that surface the encrypted auth-profile dropdown. */
const AUTH_ELIGIBLE_STEP_TYPES = new Set(['agent', 'shell', 'python', 'email', 'a2a']);

const AGENT_HINT_OPTIONS = ['coder', 'researcher', 'reviewer'];

// ---------------------------------------------------------------------------
// Step ID helpers — Name → slug-id link
// ---------------------------------------------------------------------------

/** ASCII-only step-id slug. See `./slugify` for the shared implementation —
 *  re-exported here so existing import sites keep compiling. */
export function slugify(input: string): string {
  return slugifyShared(input, 'step');
}

/** Append `-2`, `-3`, … until a slug doesn't collide with `existing`. */
export function uniqueTaskId(slug: string, existing: Iterable<string>): string {
  return uniqueSlug(slug, existing);
}

// ---------------------------------------------------------------------------
// Shared style helpers — all colors via --pc-* / --construct-* tokens
// ---------------------------------------------------------------------------

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: '6px 8px',
  borderRadius: 8,
  border: '1px solid var(--pc-border)',
  background: 'var(--pc-bg-input)',
  color: 'var(--pc-text-primary)',
  fontSize: 12,
  outline: 'none',
};

const monoInputStyle: React.CSSProperties = {
  ...inputStyle,
  fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  fontSize: 10,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.08em',
  color: 'var(--pc-text-faint)',
  marginBottom: 4,
};

const sectionShellStyle: React.CSSProperties = {
  padding: 12,
  borderRadius: 10,
  border: '1px solid var(--pc-border)',
  background: 'var(--pc-bg-base)',
  display: 'flex',
  flexDirection: 'column',
  gap: 10,
};

const sectionTitleStyle: React.CSSProperties = {
  fontSize: 10,
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.12em',
  color: 'var(--pc-text-faint)',
};

function helperStyle(): React.CSSProperties {
  return { fontSize: 10, color: 'var(--pc-text-faint)', marginTop: 2 };
}

/** DAG context surfaced to ExpressionTextarea for ${...} autocomplete. */
export interface DagContext {
  stepIds: string[];
  workflowInputs: string[];
  triggerFields: string[];
}

interface Props {
  node: Node<TaskNodeData>;
  /** All current task IDs in the editor — used to resolve slug collisions
   *  when the Name → Step ID link rewrites the id. Includes the active node. */
  existingTaskIds: string[];
  onUpdate: (nodeId: string, updates: Partial<TaskNodeData>) => void;
  /** Atomic step-id rename: updates node.id, data.taskId, and edge endpoints
   *  in lockstep so depends_on round-trips correctly. */
  onRenameStep: (oldId: string, newId: string) => void;
  onDelete: (nodeId: string) => void;
  /** Open the type-change palette */
  onChangeType: () => void;
  /** Available references for ${...} autocomplete in expression textareas. */
  dagContext?: DagContext;
  /** "Run to here" — launch a partial workflow run that executes the
   *  selected step's ancestor closure (plus the step itself). Owner is
   *  responsible for hitting the runWorkflow API and setting `targetStepId`.
   *  Receives the closure as the editor sees it for the popover preview. */
  onRunToHere?: (taskId: string, closureTaskIds: string[]) => void;
  /** Disable the "Run to here" button (e.g. while a run is in flight or
   *  the editor has unsaved changes). */
  runToHereDisabled?: boolean;
  /** Compute the ancestor closure of a task id for the popover preview.
   *  The owner injects this so the panel doesn't need direct access to
   *  the task list. Returns ids in editor order, target last. */
  computeRunToHereClosure?: (taskId: string) => string[];
}

export default function StepConfigPanel({
  node,
  existingTaskIds,
  onUpdate,
  onRenameStep,
  onDelete,
  onChangeType,
  dagContext,
  onRunToHere,
  runToHereDisabled = false,
  computeRunToHereClosure,
}: Props) {
  const dagStepIds = dagContext?.stepIds ?? [];
  const dagInputs = dagContext?.workflowInputs ?? [];
  const dagTriggerFields = dagContext?.triggerFields ?? [];
  const data = node.data;
  const stepType = data.type ?? 'agent';
  const typeDef = STEP_TYPES_BY_TYPE[stepType];

  // ── Name → Step ID slug-link state ──────────────────────────────────────
  // Compute initial linked state on mount: a step is "linked" if its current
  // id matches what slugify(name) would produce. Editor-only state, never
  // persisted to YAML — re-derived on every load.
  const [idLinkedToName, setIdLinkedToName] = useState<boolean>(
    () => slugify(data.name || '') === data.taskId,
  );
  // If the selected node changes, re-derive the linked state for the new node.
  useEffect(() => {
    setIdLinkedToName(slugify(data.name || '') === data.taskId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id]);

  // Pool of existing IDs the new slug must not collide with — exclude the
  // active node's own id so editing in place doesn't fight with itself.
  const otherTaskIds = useMemo(
    () => existingTaskIds.filter((id) => id !== data.taskId),
    [existingTaskIds, data.taskId],
  );

  const handleNameChange = useCallback(
    (nextName: string) => {
      onUpdate(node.id, { name: nextName, label: nextName });
      if (idLinkedToName) {
        const nextId = uniqueTaskId(slugify(nextName), otherTaskIds);
        if (nextId !== data.taskId) onRenameStep(data.taskId, nextId);
      }
    },
    [idLinkedToName, node.id, data.taskId, otherTaskIds, onUpdate, onRenameStep],
  );

  // Local draft so typing intermediate states (uppercase, spaces) doesn't
  // aggressively reformat under the cursor. Commits to the canvas on blur.
  const [taskIdDraft, setTaskIdDraft] = useState<string>(data.taskId);
  useEffect(() => {
    setTaskIdDraft(data.taskId);
  }, [data.taskId]);

  const handleTaskIdInputChange = useCallback((next: string) => {
    setTaskIdDraft(next);
    // Manual touch breaks the slug-link immediately, even before commit.
    setIdLinkedToName(false);
  }, []);

  const commitTaskIdDraft = useCallback(() => {
    const cleaned = slugify(taskIdDraft);
    if (cleaned === data.taskId) {
      // Slug normalized back to current id — no rename, but keep the draft
      // visually aligned with the stored value.
      setTaskIdDraft(data.taskId);
      return;
    }
    const unique = uniqueTaskId(cleaned, otherTaskIds);
    onRenameStep(data.taskId, unique);
  }, [taskIdDraft, data.taskId, otherTaskIds, onRenameStep]);

  const handleRelinkId = useCallback(() => {
    const slug = uniqueTaskId(slugify(data.name || ''), otherTaskIds);
    if (slug !== data.taskId) onRenameStep(data.taskId, slug);
    setIdLinkedToName(true);
  }, [data.name, data.taskId, otherTaskIds, onRenameStep]);

  const [skillSearch, setSkillSearch] = useState('');
  const [showSkillPicker, setShowSkillPicker] = useState(false);
  const [allSkills, setAllSkills] = useState<SkillDefinition[]>([]);
  const [skillLoading, setSkillLoading] = useState(false);
  const [channelOptions, setChannelOptions] = useState<string[]>(['dashboard']);

  // Pool-agent picker — single shared mount lives in WorkflowEditor. The
  // "Choose agent…" button below dispatches OPEN_AGENT_PICKER_EVENT instead
  // of mounting its own picker, so two AgentPickers can never both be open.

  // Auth-profile picker — bound encrypted credential for external API calls.
  const { profiles: authProfiles } = useAuthProfiles();
  const [authPickerOpen, setAuthPickerOpen] = useState(false);
  const [authAnchorRect, setAuthAnchorRect] = useState<DOMRect | null>(null);

  // Reset the auth picker when the user clicks a different node — without
  // this, opening the picker on node A and then clicking node B before
  // selecting leaves the picker mounted with a stale anchor (same class as
  // the AgentPicker double-mount issue).
  useEffect(() => {
    setAuthPickerOpen(false);
    setAuthAnchorRect(null);
  }, [node.id]);
  const showAuthField = AUTH_ELIGIBLE_STEP_TYPES.has(stepType);
  const selectedAuthProfile = useMemo(
    () => authProfiles.find((p) => p.id === data.auth) ?? null,
    [authProfiles, data.auth],
  );

  // Channels: load for human / notify steps
  useEffect(() => {
    if (stepType !== 'human_input' && stepType !== 'notify' && stepType !== 'human_approval') return;
    getChannels()
      .then((channels) => {
        const active = channels.filter((ch) => ch.enabled && ch.status === 'active').map((ch) => ch.name);
        setChannelOptions(Array.from(new Set(['dashboard', ...active])));
      })
      .catch(() => setChannelOptions(['dashboard']));
  }, [stepType]);

  // Skills: load when picker opens
  useEffect(() => {
    if (!showSkillPicker || allSkills.length > 0) return;
    setSkillLoading(true);
    fetchSkills(false, 1, 50)
      .then((res) => setAllSkills(res.skills))
      .catch(() => setAllSkills([]))
      .finally(() => setSkillLoading(false));
  }, [showSkillPicker, allSkills.length]);

  const skillSearchResults = useMemo(() => {
    const assigned = new Set(data.skills);
    const available = allSkills.filter((s) => !assigned.has(s.name));
    if (!skillSearch) return available;
    const q = skillSearch.toLowerCase();
    return available.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        (s.description && s.description.toLowerCase().includes(q)) ||
        (s.domain && s.domain.toLowerCase().includes(q)),
    );
  }, [skillSearch, data.skills, allSkills]);

  const toggleHint = useCallback(
    (hint: string) => {
      const next = data.agentHints.includes(hint)
        ? data.agentHints.filter((h) => h !== hint)
        : [...data.agentHints, hint];
      onUpdate(node.id, { agentHints: next });
    },
    [node.id, data.agentHints, onUpdate],
  );

  const addSkill = useCallback(
    (name: string) => {
      if (!data.skills.includes(name)) onUpdate(node.id, { skills: [...data.skills, name] });
    },
    [node.id, data.skills, onUpdate],
  );

  const removeSkill = useCallback(
    (name: string) => {
      onUpdate(node.id, { skills: data.skills.filter((s) => s !== name) });
    },
    [node.id, data.skills, onUpdate],
  );

  const toggleChannel = useCallback(
    (ch: string) => {
      const current = data.channels ?? [];
      const next = current.includes(ch) ? current.filter((c) => c !== ch) : [...current, ch];
      onUpdate(node.id, { channels: next });
    },
    [node.id, data.channels, onUpdate],
  );

  // ── Run-to-here popover ────────────────────────────────────────────────
  // Opens a confirmation popover listing the ancestor closure that would
  // execute. Closure preview is purely best-effort — the backend
  // re-derives it authoritatively before scheduling.
  const [runToHereOpen, setRunToHereOpen] = useState(false);
  const runToHereClosureIds = useMemo<string[]>(
    () =>
      runToHereOpen && computeRunToHereClosure ? computeRunToHereClosure(data.taskId) : [],
    [runToHereOpen, computeRunToHereClosure, data.taskId],
  );
  // Reset whenever the user picks a different node so a stale popover
  // can't fire against the wrong target.
  useEffect(() => {
    setRunToHereOpen(false);
  }, [node.id]);

  const handleRunToHereConfirm = useCallback(() => {
    if (!onRunToHere) return;
    onRunToHere(data.taskId, runToHereClosureIds);
    setRunToHereOpen(false);
  }, [onRunToHere, data.taskId, runToHereClosureIds]);

  const TypeIcon = typeDef?.icon;

  return (
    <Panel variant="primary" className="overflow-hidden">
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        {/* Header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '14px 16px',
            borderBottom: '1px solid var(--construct-border-soft)',
            position: 'relative',
          }}
        >
          <div className="construct-kicker">Step Details</div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {onRunToHere ? (
              <button
                type="button"
                onClick={() => setRunToHereOpen((v) => !v)}
                disabled={runToHereDisabled}
                title="Run every ancestor of this step plus the step itself, then stop"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '4px 8px',
                  borderRadius: 8,
                  border: '1px solid var(--construct-border-soft)',
                  background: 'transparent',
                  color: runToHereDisabled
                    ? 'var(--pc-text-faint)'
                    : 'var(--construct-status-success)',
                  fontSize: 11,
                  cursor: runToHereDisabled ? 'not-allowed' : 'pointer',
                  opacity: runToHereDisabled ? 0.6 : 1,
                }}
              >
                <Crosshair size={12} />
                Run to here
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => onDelete(node.id)}
              title="Delete step"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 4,
                padding: '4px 8px',
                borderRadius: 8,
                border: '1px solid var(--construct-border-soft)',
                background: 'transparent',
                color: 'var(--construct-status-danger)',
                fontSize: 11,
                cursor: 'pointer',
              }}
            >
              <Trash2 size={12} />
              Delete
            </button>
          </div>
          {runToHereOpen && onRunToHere ? (
            <div
              role="dialog"
              aria-label="Run to here confirmation"
              style={{
                position: 'absolute',
                top: 'calc(100% + 4px)',
                right: 12,
                zIndex: 50,
                width: 280,
                padding: 12,
                borderRadius: 10,
                border: '1px solid var(--construct-border-soft)',
                background: 'var(--pc-bg-base)',
                boxShadow: '0 8px 24px rgba(0, 0, 0, 0.25)',
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
              }}
            >
              <div style={{ fontSize: 11, color: 'var(--pc-text-primary)' }}>
                {runToHereClosureIds.length <= 1
                  ? 'Just this one step will run.'
                  : `This will run ${runToHereClosureIds.length} steps in order:`}
              </div>
              {runToHereClosureIds.length > 1 ? (
                <ol
                  style={{
                    margin: 0,
                    padding: '0 0 0 18px',
                    fontSize: 11,
                    color: 'var(--pc-text-faint)',
                    maxHeight: 160,
                    overflowY: 'auto',
                    fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
                  }}
                >
                  {runToHereClosureIds.map((sid) => (
                    <li
                      key={sid}
                      style={{
                        color:
                          sid === data.taskId
                            ? 'var(--construct-status-success)'
                            : 'var(--pc-text-faint)',
                        fontWeight: sid === data.taskId ? 600 : 400,
                      }}
                    >
                      {sid}
                      {sid === data.taskId ? '  ← target' : ''}
                    </li>
                  ))}
                </ol>
              ) : null}
              <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                <button
                  type="button"
                  onClick={() => setRunToHereOpen(false)}
                  style={{
                    padding: '4px 10px',
                    borderRadius: 6,
                    border: '1px solid var(--construct-border-soft)',
                    background: 'transparent',
                    color: 'var(--pc-text-primary)',
                    fontSize: 11,
                    cursor: 'pointer',
                  }}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleRunToHereConfirm}
                  style={{
                    padding: '4px 10px',
                    borderRadius: 6,
                    border: '1px solid var(--construct-status-success)',
                    background: 'var(--construct-status-success)',
                    color: 'var(--pc-bg-base)',
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  Run
                </button>
              </div>
            </div>
          ) : null}
        </div>

        <div style={{ overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          {/* Step ID — editable; auto-slugifies from Name while linked */}
          <div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
                marginBottom: 4,
              }}
            >
              <label style={{ ...labelStyle, marginBottom: 0 }}>Step ID</label>
              {idLinkedToName ? (
                <span
                  title="Step ID auto-derives from Name. Edit it manually to break the link."
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '2px 6px',
                    borderRadius: 999,
                    fontSize: 9.5,
                    fontWeight: 600,
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    color: 'var(--construct-text-faint)',
                    background: 'color-mix(in srgb, var(--construct-text-faint) 12%, transparent)',
                    border: '1px solid var(--construct-border-soft)',
                  }}
                >
                  <Link2 size={10} />
                  linked
                </span>
              ) : (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <span
                    title="Step ID was edited manually — Name changes no longer touch it."
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 4,
                      padding: '2px 6px',
                      borderRadius: 999,
                      fontSize: 9.5,
                      fontWeight: 600,
                      textTransform: 'uppercase',
                      letterSpacing: '0.06em',
                      color: 'var(--construct-status-warning)',
                      background: 'color-mix(in srgb, var(--construct-status-warning) 14%, transparent)',
                      border: '1px solid color-mix(in srgb, var(--construct-status-warning) 36%, transparent)',
                    }}
                  >
                    <Link2Off size={10} />
                    manual
                  </span>
                  <button
                    type="button"
                    onClick={handleRelinkId}
                    title="Reset Step ID to slugify(Name) and re-link"
                    style={{
                      padding: '2px 8px',
                      fontSize: 10,
                      fontWeight: 600,
                      borderRadius: 6,
                      border: '1px solid var(--pc-accent-dim)',
                      background: 'transparent',
                      color: 'var(--pc-accent-light)',
                      cursor: 'pointer',
                    }}
                  >
                    Re-link
                  </button>
                </span>
              )}
            </div>
            <input
              type="text"
              value={taskIdDraft}
              onChange={(e) => handleTaskIdInputChange(e.target.value)}
              onBlur={commitTaskIdDraft}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  (e.currentTarget as HTMLInputElement).blur();
                }
              }}
              spellCheck={false}
              style={monoInputStyle}
            />
          </div>

          {/* Name */}
          <div>
            <label style={labelStyle}>Name</label>
            <input
              type="text"
              value={data.name}
              onChange={(e) => handleNameChange(e.target.value)}
              style={inputStyle}
            />
          </div>

          {/* Type chip + Change Type */}
          <div>
            <label style={labelStyle}>Type</label>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  padding: '6px 10px',
                  borderRadius: 999,
                  background: 'var(--pc-accent-glow)',
                  color: 'var(--pc-accent)',
                  border: '1px solid var(--pc-accent-dim)',
                  fontSize: 12,
                  fontWeight: 600,
                  flex: 1,
                  minWidth: 0,
                }}
              >
                {TypeIcon ? <TypeIcon size={12} /> : null}
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {typeDef?.label ?? stepType}
                </span>
              </span>
              <button
                type="button"
                onClick={onChangeType}
                className="construct-button"
                style={{ padding: '6px 10px', fontSize: 11 }}
              >
                Change
              </button>
            </div>
            <p
              style={{
                fontSize: 11,
                fontStyle: 'italic',
                color: 'var(--pc-text-faint)',
                marginTop: 4,
              }}
            >
              What kind of step this is — determines how it runs.
            </p>
          </div>

          {/* Conditional gate badge + condition */}
          {stepType === 'conditional' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <span
                style={{
                  display: 'inline-flex',
                  alignSelf: 'flex-start',
                  alignItems: 'center',
                  gap: 6,
                  padding: '4px 10px',
                  borderRadius: 999,
                  fontSize: 11,
                  fontWeight: 600,
                  background: 'color-mix(in srgb, var(--construct-status-warning) 18%, transparent)',
                  color: 'var(--construct-status-warning)',
                  border: '1px solid color-mix(in srgb, var(--construct-status-warning) 36%, transparent)',
                }}
              >
                If / Else Gate
              </span>
              <label style={labelStyle}>Condition</label>
              <textarea
                value={data.condition || ''}
                onChange={(e) => onUpdate(node.id, { condition: e.target.value })}
                placeholder="e.g. review.status == 'passed'"
                rows={2}
                style={monoInputStyle}
              />
              <p style={helperStyle()}>Wire the green (true) and red (false) handles to branch targets.</p>

              {/* Optional branch values — emitted on the gate's `output` so
                  downstream steps can read `${gate.output}`. Multi-branch
                  workflows should edit YAML directly. */}
              <label style={labelStyle}>True branch value (optional)</label>
              <input
                type="text"
                value={data.onTrueValue || ''}
                onChange={(e) => onUpdate(node.id, { onTrueValue: e.target.value })}
                placeholder="e.g. 'approved' or review.output_data.score"
                style={monoInputStyle}
              />
              <label style={labelStyle}>False branch value (optional)</label>
              <input
                type="text"
                value={data.onFalseValue || ''}
                onChange={(e) => onUpdate(node.id, { onFalseValue: e.target.value })}
                placeholder="e.g. 'rejected'"
                style={monoInputStyle}
              />
              <p style={helperStyle()}>
                When the matching branch fires, this expression's result becomes the
                gate's <code>output</code>. Same syntax as <code>condition</code> —
                literals, name refs, ternary all work.
              </p>
            </div>
          )}

          {/* Description */}
          <div>
            <label style={labelStyle}>Description</label>
            <textarea
              value={data.description}
              onChange={(e) => onUpdate(node.id, { description: e.target.value })}
              placeholder={stepType === 'conditional' ? 'What this gate checks…' : 'What this step does…'}
              rows={3}
              style={inputStyle}
            />
          </div>

          {/* Retry */}
          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Retry</label>
              <input
                type="number"
                min={0}
                max={5}
                value={data.retry}
                onChange={(e) => onUpdate(node.id, { retry: parseInt(e.target.value) || 0 })}
                style={inputStyle}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Retry Delay (s)</label>
              <input
                type="number"
                min={0}
                step={1}
                value={data.retryDelay}
                onChange={(e) => onUpdate(node.id, { retryDelay: parseFloat(e.target.value) || 5 })}
                style={inputStyle}
              />
            </div>
          </div>

          {/* ── Agent ── */}
          {stepType === 'agent' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Agent Config</div>

              {/* Pool Agent — dispatches OPEN_AGENT_PICKER_EVENT so the
                  single editor-level AgentPicker mount opens anchored here. */}
              <div>
                <label style={labelStyle}>Pool Agent</label>
                <button
                  type="button"
                  onClick={(e) => {
                    emitOpenAgentPicker({
                      taskId: node.id,
                      anchorRect: e.currentTarget.getBoundingClientRect(),
                    });
                  }}
                  style={{
                    ...monoInputStyle,
                    textAlign: 'left',
                    cursor: 'pointer',
                    color: data.assign
                      ? 'var(--pc-text-primary)'
                      : 'var(--pc-text-faint)',
                  }}
                >
                  {data.assign || 'Choose agent…'}
                </button>
                {data.assign && (
                  <div
                    style={{
                      marginTop: 4,
                      padding: '2px 8px',
                      borderRadius: 6,
                      fontSize: 10,
                      fontWeight: 600,
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 4,
                      background: 'var(--pc-accent-glow)',
                      color: 'var(--pc-accent-light)',
                      border: '1px solid var(--pc-accent-dim)',
                    }}
                  >
                    <span>●</span>
                    {data.assign}
                  </div>
                )}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <label style={labelStyle}>Type</label>
                  <select
                    value={data.agentType || 'claude'}
                    onChange={(e) => onUpdate(node.id, { agentType: e.target.value })}
                    style={inputStyle}
                  >
                    <option value="claude">claude</option>
                    <option value="codex">codex</option>
                  </select>
                </div>
                <div>
                  <label style={labelStyle}>Role</label>
                  <input
                    type="text"
                    value={data.role || ''}
                    onChange={(e) => onUpdate(node.id, { role: e.target.value })}
                    placeholder="coder"
                    style={inputStyle}
                  />
                </div>
              </div>

              <div>
                <label style={labelStyle}>Timeout (sec)</label>
                <input
                  type="number"
                  min={10}
                  max={3600}
                  value={data.timeout || 300}
                  onChange={(e) => onUpdate(node.id, { timeout: parseInt(e.target.value) || 300 })}
                  style={{ ...inputStyle, width: 100 }}
                />
              </div>

              <div>
                <label style={labelStyle}>Model Override</label>
                <input
                  type="text"
                  value={data.model}
                  onChange={(e) => onUpdate(node.id, { model: e.target.value })}
                  placeholder="e.g. claude-sonnet-4-5-20250514"
                  style={monoInputStyle}
                />
              </div>

              <div>
                <label style={labelStyle}>Prompt</label>
                <ExpressionTextarea
                  value={data.prompt}
                  onChange={(next) => onUpdate(node.id, { prompt: next })}
                  placeholder="Agent prompt template (supports ${step_id.output} interpolation)"
                  rows={6}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
            </div>
          )}

          {/* ── Parallel ── */}
          {stepType === 'parallel' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Parallel Config</div>
              <div>
                <label style={labelStyle}>Join Strategy</label>
                <select
                  value={data.parallelJoin || 'all'}
                  onChange={(e) => onUpdate(node.id, { parallelJoin: e.target.value })}
                  style={inputStyle}
                >
                  <option value="all">all — wait for every branch</option>
                  <option value="any">any — first success wins</option>
                  <option value="majority">majority — &gt;50% must succeed</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>Max Concurrency</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={data.parallelMaxConcurrency}
                  onChange={(e) =>
                    onUpdate(node.id, { parallelMaxConcurrency: parseInt(e.target.value) || 5 })
                  }
                  style={inputStyle}
                />
              </div>
              <p style={helperStyle()}>Children are wired by connecting nodes on the canvas.</p>
            </div>
          )}

          {/* ── ForEach ── */}
          {stepType === 'for_each' && (
            <div style={sectionShellStyle}>
              <div style={{ ...sectionTitleStyle, color: 'var(--construct-status-success)' }}>ForEach Config</div>
              <div>
                <label style={labelStyle}>Range</label>
                <input
                  type="text"
                  value={data.forEachRange || ''}
                  onChange={(e) => onUpdate(node.id, { forEachRange: e.target.value })}
                  placeholder="e.g. 1..8 or ${resolve_arc.output_data.episode_range}"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Numeric range (N..M or N-M). Supports interpolation.</p>
              </div>
              <div>
                <label style={labelStyle}>Items (alternative to range)</label>
                <input
                  type="text"
                  defaultValue={(data.forEachItems || []).join(', ')}
                  onBlur={(e) =>
                    onUpdate(node.id, {
                      forEachItems: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="item1, item2, item3"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Comma-separated values. Used when range is empty.</p>
              </div>
              <div>
                <label style={labelStyle}>Loop Variable</label>
                <input
                  type="text"
                  value={data.forEachVariable || 'item'}
                  onChange={(e) => onUpdate(node.id, { forEachVariable: e.target.value })}
                  placeholder="item"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>
                  Access via {`\${for_each.${data.forEachVariable || 'item'}}`}, {'${for_each.index}'},{' '}
                  {'${for_each.iteration}'}.
                </p>
              </div>
              <div>
                <label style={labelStyle}>Sub-steps</label>
                <input
                  type="text"
                  defaultValue={(data.forEachSteps || []).join(', ')}
                  onBlur={(e) =>
                    onUpdate(node.id, {
                      forEachSteps: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="step_a, step_b, step_c"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Comma-separated step IDs executed sequentially per iteration.</p>
              </div>
              <div>
                <label style={labelStyle}>Max Iterations</label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={data.forEachMaxIterations || 20}
                  onChange={(e) => onUpdate(node.id, { forEachMaxIterations: parseInt(e.target.value) || 20 })}
                  style={{ ...inputStyle, width: 100 }}
                />
              </div>
              <div style={{ display: 'flex', gap: 16 }}>
                <Checkbox
                  checked={data.forEachCarryForward ?? true}
                  onChange={(v) => onUpdate(node.id, { forEachCarryForward: v })}
                  label="Carry forward"
                />
                <Checkbox
                  checked={data.forEachFailFast ?? true}
                  onChange={(v) => onUpdate(node.id, { forEachFailFast: v })}
                  label="Fail fast"
                />
              </div>
            </div>
          )}

          {/* ── Goto ── */}
          {stepType === 'goto' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Loop Config</div>
              <div>
                <label style={labelStyle}>Target Step</label>
                <input
                  type="text"
                  value={data.gotoTarget || ''}
                  onChange={(e) => onUpdate(node.id, { gotoTarget: e.target.value })}
                  placeholder="step-id to loop back to"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Max Iterations</label>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={data.gotoMaxIterations || 3}
                  onChange={(e) => onUpdate(node.id, { gotoMaxIterations: parseInt(e.target.value) || 3 })}
                  style={{ ...inputStyle, width: 100 }}
                />
              </div>
              <div>
                <label style={labelStyle}>Condition Guard</label>
                <input
                  type="text"
                  value={data.gotoCondition}
                  onChange={(e) => onUpdate(node.id, { gotoCondition: e.target.value })}
                  placeholder="Optional: only goto if expression is truthy"
                  style={monoInputStyle}
                />
              </div>
            </div>
          )}

          {/* ── Group Chat ── */}
          {stepType === 'group_chat' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Group Chat Config</div>
              <div>
                <label style={labelStyle}>Topic</label>
                <input
                  type="text"
                  value={data.groupChatTopic || ''}
                  onChange={(e) => onUpdate(node.id, { groupChatTopic: e.target.value })}
                  placeholder="Discussion topic"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Participants</label>
                <input
                  type="text"
                  value={(data.groupChatParticipants || []).join(', ')}
                  onChange={(e) =>
                    onUpdate(node.id, {
                      groupChatParticipants: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="claude, codex"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Comma-separated agent types or template names.</p>
              </div>
              <div>
                <label style={labelStyle}>Max Rounds</label>
                <input
                  type="number"
                  min={2}
                  max={20}
                  value={data.groupChatMaxRounds || 8}
                  onChange={(e) => onUpdate(node.id, { groupChatMaxRounds: parseInt(e.target.value) || 8 })}
                  style={{ ...inputStyle, width: 100 }}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Moderator</label>
                  <select
                    value={data.groupChatModerator}
                    onChange={(e) => onUpdate(node.id, { groupChatModerator: e.target.value })}
                    style={inputStyle}
                  >
                    <option value="claude">Claude</option>
                    <option value="codex">Codex</option>
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Strategy</label>
                  <select
                    value={data.groupChatStrategy}
                    onChange={(e) => onUpdate(node.id, { groupChatStrategy: e.target.value })}
                    style={inputStyle}
                  >
                    <option value="moderator_selected">Moderator Selected</option>
                    <option value="round_robin">Round Robin</option>
                  </select>
                </div>
              </div>
              <div>
                <label style={labelStyle}>Timeout (s)</label>
                <input
                  type="number"
                  min={1}
                  value={data.groupChatTimeout}
                  onChange={(e) => onUpdate(node.id, { groupChatTimeout: parseInt(e.target.value) || 120 })}
                  style={inputStyle}
                />
              </div>
            </div>
          )}

          {/* ── Supervisor ── */}
          {stepType === 'supervisor' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Supervisor Config</div>
              <div>
                <label style={labelStyle}>Task</label>
                <textarea
                  value={data.supervisorTask || ''}
                  onChange={(e) => onUpdate(node.id, { supervisorTask: e.target.value })}
                  placeholder="Task to decompose and delegate"
                  rows={2}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Max Iterations</label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={data.supervisorMaxIterations || 5}
                  onChange={(e) => onUpdate(node.id, { supervisorMaxIterations: parseInt(e.target.value) || 5 })}
                  style={{ ...inputStyle, width: 100 }}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Agent Type</label>
                  <select
                    value={data.supervisorType}
                    onChange={(e) => onUpdate(node.id, { supervisorType: e.target.value })}
                    style={inputStyle}
                  >
                    <option value="claude">Claude</option>
                    <option value="codex">Codex</option>
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Timeout (s)</label>
                  <input
                    type="number"
                    min={1}
                    value={data.supervisorTimeout}
                    onChange={(e) => onUpdate(node.id, { supervisorTimeout: parseInt(e.target.value) || 300 })}
                    style={inputStyle}
                  />
                </div>
              </div>
            </div>
          )}

          {/* ── Shell ── */}
          {stepType === 'shell' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Shell Config</div>
              <div>
                <label style={labelStyle}>Command</label>
                <input
                  type="text"
                  value={data.shellCommand || ''}
                  onChange={(e) => onUpdate(node.id, { shellCommand: e.target.value })}
                  placeholder="e.g. npm run build"
                  style={monoInputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Timeout (s)</label>
                  <input
                    type="number"
                    min={1}
                    value={data.shellTimeout}
                    onChange={(e) => onUpdate(node.id, { shellTimeout: parseInt(e.target.value) || 60 })}
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', paddingBottom: 4 }}>
                  <Checkbox
                    checked={data.shellAllowFailure}
                    onChange={(v) => onUpdate(node.id, { shellAllowFailure: v })}
                    label="Allow failure"
                  />
                </div>
              </div>
            </div>
          )}

          {/* ── Python ── */}
          {stepType === 'python' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Python Config</div>
              <div>
                <label style={labelStyle}>Script (path or builtin) — XOR with Code</label>
                <input
                  type="text"
                  value={data.pythonScript || ''}
                  onChange={(e) => onUpdate(node.id, { pythonScript: e.target.value })}
                  placeholder="e.g. kref_encode.py"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Code (inline) — XOR with Script</label>
                <textarea
                  value={data.pythonCode || ''}
                  onChange={(e) => onUpdate(node.id, { pythonCode: e.target.value })}
                  placeholder="import json, sys&#10;json.dump({'ok': True}, sys.stdout)"
                  rows={4}
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Args (JSON object)</label>
                <input
                  type="text"
                  value={data.pythonArgs || ''}
                  onChange={(e) => onUpdate(node.id, { pythonArgs: e.target.value })}
                  placeholder='{"op": "encode", "kref": "${trigger.entity_kref}"}'
                  style={monoInputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Timeout (s)</label>
                  <input
                    type="number"
                    min={1}
                    value={data.pythonTimeout || 60}
                    onChange={(e) => onUpdate(node.id, { pythonTimeout: parseInt(e.target.value) || 60 })}
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', paddingBottom: 4 }}>
                  <Checkbox
                    checked={data.pythonAllowFailure || false}
                    onChange={(v) => onUpdate(node.id, { pythonAllowFailure: v })}
                    label="Allow failure"
                  />
                </div>
              </div>
            </div>
          )}

          {/* ── Email ── */}
          {stepType === 'email' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Email Config</div>
              <div>
                <label style={labelStyle}>To</label>
                <input
                  type="text"
                  value={data.emailTo || ''}
                  onChange={(e) => onUpdate(node.id, { emailTo: e.target.value })}
                  placeholder="lead@example.com or ${steps.lead.output_data.email}"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Subject</label>
                <input
                  type="text"
                  value={data.emailSubject || ''}
                  onChange={(e) => onUpdate(node.id, { emailSubject: e.target.value })}
                  placeholder="Hi ${steps.lead.output_data.first_name}"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Body (plain text)</label>
                <ExpressionTextarea
                  value={data.emailBody || ''}
                  onChange={(next) => onUpdate(node.id, { emailBody: next })}
                  rows={5}
                  placeholder="Hi there,&#10;&#10;Saw you're working on…"
                  style={inputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>From (override)</label>
                <input
                  type="text"
                  value={data.emailFrom || ''}
                  onChange={(e) => onUpdate(node.id, { emailFrom: e.target.value })}
                  placeholder="default: from config.toml"
                  style={inputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>CC</label>
                  <input
                    type="text"
                    value={data.emailCc || ''}
                    onChange={(e) => onUpdate(node.id, { emailCc: e.target.value })}
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>BCC</label>
                  <input
                    type="text"
                    value={data.emailBcc || ''}
                    onChange={(e) => onUpdate(node.id, { emailBcc: e.target.value })}
                    style={inputStyle}
                  />
                </div>
              </div>
              <div style={{ paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <Checkbox
                  checked={data.emailTrackClicks || false}
                  onChange={(v) => onUpdate(node.id, { emailTrackClicks: v })}
                  label="Track clicks (rewrite URLs)"
                />
                {data.emailTrackClicks && (
                  <div style={{ paddingLeft: 24, marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div>
                      <label style={labelStyle}>Track kref (required)</label>
                      <input
                        type="text"
                        value={data.emailTrackKref || ''}
                        onChange={(e) => onUpdate(node.id, { emailTrackKref: e.target.value })}
                        placeholder="${trigger.entity_kref}"
                        style={monoInputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Track base URL</label>
                      <input
                        type="text"
                        value={data.emailTrackBaseUrl || ''}
                        onChange={(e) => onUpdate(node.id, { emailTrackBaseUrl: e.target.value })}
                        placeholder="https://gateway.example.com"
                        style={monoInputStyle}
                      />
                    </div>
                  </div>
                )}
              </div>
              <div style={{ display: 'flex', gap: 8, paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Timeout (s)</label>
                  <input
                    type="number"
                    min={1}
                    value={data.emailTimeout || 30}
                    onChange={(e) => onUpdate(node.id, { emailTimeout: parseInt(e.target.value) || 30 })}
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', paddingBottom: 4 }}>
                  <Checkbox
                    checked={data.emailDryRun || false}
                    onChange={(v) => onUpdate(node.id, { emailDryRun: v })}
                    label="Dry run"
                  />
                </div>
              </div>
            </div>
          )}

          {/* ── Image ── */}
          {stepType === 'image' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Image Config</div>
              <div>
                <label style={labelStyle}>Prompt</label>
                <ExpressionTextarea
                  value={data.imagePrompt || ''}
                  onChange={(next) => onUpdate(node.id, { imagePrompt: next })}
                  rows={5}
                  placeholder="Architectural panel of Seoul Station 2040, golden hour…"
                  style={inputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Count (1–5)</label>
                  <input
                    type="number"
                    min={1}
                    max={5}
                    value={data.imageCount ?? 1}
                    onChange={(e) => onUpdate(node.id, { imageCount: Math.max(1, Math.min(5, parseInt(e.target.value) || 1)) })}
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Timeout (s)</label>
                  <input
                    type="number"
                    min={1}
                    value={data.imageTimeout ?? 1200}
                    onChange={(e) => onUpdate(node.id, { imageTimeout: parseInt(e.target.value) || 1200 })}
                    style={inputStyle}
                  />
                </div>
              </div>
              <div>
                <label style={labelStyle}>Item name (override)</label>
                <input
                  type="text"
                  value={data.imageItemName || ''}
                  onChange={(e) => onUpdate(node.id, { imageItemName: e.target.value })}
                  placeholder="default: derived from step id"
                  style={inputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Space</label>
                  <input
                    type="text"
                    value={data.imageSpace || ''}
                    onChange={(e) => onUpdate(node.id, { imageSpace: e.target.value })}
                    placeholder="default: Images (under harness)"
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Sandbox</label>
                  <select
                    value={data.imageSandbox || ''}
                    onChange={(e) => onUpdate(node.id, { imageSandbox: e.target.value })}
                    style={inputStyle}
                  >
                    <option value="">auto (platform default)</option>
                    <option value="read-only">read-only</option>
                    <option value="workspace-write">workspace-write</option>
                    <option value="danger-full-access">danger-full-access</option>
                  </select>
                </div>
              </div>
              <div>
                <label style={labelStyle}>Output filename (override)</label>
                <input
                  type="text"
                  value={data.imageOutputPath || ''}
                  onChange={(e) => onUpdate(node.id, { imageOutputPath: e.target.value })}
                  placeholder="default: <step_id>.png"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Output pattern (count &gt; 1)</label>
                <input
                  type="text"
                  value={data.imageOutputPattern || ''}
                  onChange={(e) => onUpdate(node.id, { imageOutputPattern: e.target.value })}
                  placeholder="e.g. panel-{n}.png"
                  style={monoInputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 16, paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <Checkbox
                  checked={data.imageCanvas !== false}
                  onChange={(v) => onUpdate(node.id, { imageCanvas: v })}
                  label="Push to canvas"
                />
                <Checkbox
                  checked={data.imageRegisterArtifact !== false}
                  onChange={(v) => onUpdate(node.id, { imageRegisterArtifact: v })}
                  label="Register Kumiho artifact"
                />
              </div>
            </div>
          )}

          {/* ── Output ── */}
          {stepType === 'output' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Output Config</div>
              <div>
                <label style={labelStyle}>Format</label>
                <select
                  value={data.outputFormat || 'markdown'}
                  onChange={(e) => onUpdate(node.id, { outputFormat: e.target.value })}
                  style={inputStyle}
                >
                  <option value="markdown">markdown</option>
                  <option value="json">json</option>
                  <option value="text">text</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>Template</label>
                <ExpressionTextarea
                  value={data.outputTemplate}
                  onChange={(next) => onUpdate(node.id, { outputTemplate: next })}
                  placeholder="Output template with ${step_id.output} interpolation"
                  rows={6}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>

              <div style={{ paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <div style={{ ...sectionTitleStyle, color: 'var(--pc-accent-light)', marginBottom: 8 }}>
                  Kumiho Entity
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div>
                    <label style={labelStyle}>Entity Name</label>
                    <input
                      type="text"
                      value={data.entityName || ''}
                      onChange={(e) => onUpdate(node.id, { entityName: e.target.value })}
                      placeholder="e.g. ep-${inputs.episode}-draft"
                      style={monoInputStyle}
                    />
                  </div>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <div style={{ flex: 1 }}>
                      <label style={labelStyle}>Kind</label>
                      <input
                        type="text"
                        value={data.entityKind || ''}
                        onChange={(e) => onUpdate(node.id, { entityKind: e.target.value })}
                        placeholder="e.g. qs-episode-draft"
                        style={monoInputStyle}
                      />
                    </div>
                    <div style={{ width: 96 }}>
                      <label style={labelStyle}>Tag</label>
                      <input
                        type="text"
                        value={data.entityTag || ''}
                        onChange={(e) => onUpdate(node.id, { entityTag: e.target.value })}
                        placeholder="ready"
                        style={monoInputStyle}
                      />
                    </div>
                  </div>
                  <div>
                    <label style={labelStyle}>Space</label>
                    <input
                      type="text"
                      value={data.entitySpace || ''}
                      onChange={(e) => onUpdate(node.id, { entitySpace: e.target.value })}
                      placeholder="CognitiveMemory/creative/..."
                      style={monoInputStyle}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Metadata</label>
                    {Object.entries(data.entityMetadata || {}).map(([mk, mv]) => (
                      <div key={mk} style={{ display: 'flex', gap: 4, alignItems: 'center', marginBottom: 4 }}>
                        <input type="text" readOnly value={mk} style={{ ...monoInputStyle, width: 120, opacity: 0.7 }} />
                        <span style={{ fontSize: 11, color: 'var(--pc-text-faint)' }}>=</span>
                        <input
                          type="text"
                          value={String(mv ?? '')}
                          onChange={(e) =>
                            onUpdate(node.id, { entityMetadata: { ...data.entityMetadata, [mk]: e.target.value } })
                          }
                          style={{ ...monoInputStyle, flex: 1 }}
                        />
                        <button
                          onClick={() => {
                            const updated = { ...data.entityMetadata };
                            delete updated[mk];
                            onUpdate(node.id, { entityMetadata: updated });
                          }}
                          style={{ background: 'transparent', border: 0, color: 'var(--pc-text-faint)', cursor: 'pointer' }}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                    <button
                      onClick={() => {
                        const key = window.prompt('Metadata key:');
                        if (key) onUpdate(node.id, { entityMetadata: { ...(data.entityMetadata || {}), [key]: '' } });
                      }}
                      style={{
                        fontSize: 10,
                        padding: '4px 8px',
                        borderRadius: 6,
                        border: '1px solid var(--pc-accent-dim)',
                        background: 'var(--pc-accent-glow)',
                        color: 'var(--pc-accent-light)',
                        cursor: 'pointer',
                      }}
                    >
                      + Add metadata
                    </button>
                  </div>
                  <p style={helperStyle()}>Publishes output as a Kumiho entity for downstream triggers.</p>
                </div>
              </div>
            </div>
          )}

          {/* ── Handoff ── */}
          {stepType === 'handoff' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Handoff Config</div>
              <div>
                <label style={labelStyle}>From Step</label>
                <input
                  type="text"
                  value={data.handoffFrom || ''}
                  onChange={(e) => onUpdate(node.id, { handoffFrom: e.target.value })}
                  placeholder="step-id"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>To Agent Type</label>
                <select
                  value={data.handoffTo || 'codex'}
                  onChange={(e) => onUpdate(node.id, { handoffTo: e.target.value })}
                  style={inputStyle}
                >
                  <option value="claude">claude</option>
                  <option value="codex">codex</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>Reason</label>
                <input
                  type="text"
                  value={data.handoffReason || ''}
                  onChange={(e) => onUpdate(node.id, { handoffReason: e.target.value })}
                  placeholder="Continuing the task"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Task</label>
                <textarea
                  value={data.handoffTask}
                  onChange={(e) => onUpdate(node.id, { handoffTask: e.target.value })}
                  placeholder="Specific task for the receiving agent"
                  rows={2}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Timeout (s)</label>
                <input
                  type="number"
                  min={1}
                  value={data.handoffTimeout}
                  onChange={(e) => onUpdate(node.id, { handoffTimeout: parseInt(e.target.value) || 300 })}
                  style={inputStyle}
                />
              </div>
            </div>
          )}

          {/* ── Human Input ── */}
          {stepType === 'human_input' && (
            <div style={sectionShellStyle}>
              <div style={{ ...sectionTitleStyle, color: 'var(--construct-status-warning)' }}>Human Input Config</div>
              <div>
                <label style={labelStyle}>Message</label>
                <textarea
                  value={data.humanInputMessage}
                  onChange={(e) => onUpdate(node.id, { humanInputMessage: e.target.value })}
                  placeholder="Prompt sent to the channel"
                  rows={4}
                  style={inputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Channel</label>
                  <select
                    value={data.channel}
                    onChange={(e) => onUpdate(node.id, { channel: e.target.value })}
                    style={inputStyle}
                  >
                    <option value="dashboard">Dashboard</option>
                    <option value="discord">Discord</option>
                    <option value="slack">Slack</option>
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Timeout (s)</label>
                  <input
                    type="number"
                    min={60}
                    value={data.humanInputTimeout}
                    onChange={(e) => onUpdate(node.id, { humanInputTimeout: parseInt(e.target.value) || 3600 })}
                    style={inputStyle}
                  />
                </div>
              </div>
            </div>
          )}

          {/* ── Human Approval ── */}
          {stepType === 'human_approval' && (
            <div style={sectionShellStyle}>
              <div style={{ ...sectionTitleStyle, color: 'var(--construct-status-warning)' }}>Human Approval Config</div>
              <div>
                <label style={labelStyle}>Channel</label>
                <select
                  value={data.humanApprovalChannel}
                  onChange={(e) => onUpdate(node.id, { humanApprovalChannel: e.target.value })}
                  style={inputStyle}
                >
                  {channelOptions.map((ch) => (
                    <option key={ch} value={ch}>
                      {ch}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label style={labelStyle}>Channel ID Override</label>
                <input
                  type="text"
                  value={data.humanApprovalChannelId}
                  onChange={(e) => onUpdate(node.id, { humanApprovalChannelId: e.target.value })}
                  placeholder="optional channel/thread override"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Approval Message</label>
                <textarea
                  value={data.humanApprovalMessage}
                  onChange={(e) => onUpdate(node.id, { humanApprovalMessage: e.target.value })}
                  placeholder="Message shown when requesting approval"
                  rows={3}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Timeout (s)</label>
                <input
                  type="number"
                  min={60}
                  value={data.humanApprovalTimeout}
                  onChange={(e) => onUpdate(node.id, { humanApprovalTimeout: parseInt(e.target.value) || 3600 })}
                  style={inputStyle}
                />
              </div>
            </div>
          )}

          {/* ── A2A ── */}
          {stepType === 'a2a' && (
            <div style={sectionShellStyle}>
              <div style={{ ...sectionTitleStyle, color: 'var(--construct-signal-network)' }}>A2A Config</div>
              <div>
                <label style={labelStyle}>Endpoint URL</label>
                <input
                  type="text"
                  value={data.a2aUrl}
                  onChange={(e) => onUpdate(node.id, { a2aUrl: e.target.value })}
                  placeholder="https://agent.example.com/a2a"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Skill ID</label>
                <input
                  type="text"
                  value={data.a2aSkillId}
                  onChange={(e) => onUpdate(node.id, { a2aSkillId: e.target.value })}
                  placeholder="Optional skill ID"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Message</label>
                <textarea
                  value={data.a2aMessage}
                  onChange={(e) => onUpdate(node.id, { a2aMessage: e.target.value })}
                  rows={3}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Timeout (s)</label>
                <input
                  type="number"
                  min={1}
                  value={data.a2aTimeout}
                  onChange={(e) => onUpdate(node.id, { a2aTimeout: parseInt(e.target.value) || 300 })}
                  style={inputStyle}
                />
              </div>
            </div>
          )}

          {/* ── Resolve ── */}
          {stepType === 'resolve' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Resolve Config</div>
              <div>
                <label style={labelStyle}>Entity Kind</label>
                <input
                  type="text"
                  value={data.resolveKind || ''}
                  onChange={(e) => onUpdate(node.id, { resolveKind: e.target.value })}
                  placeholder="e.g. qs-episode-draft"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Tag</label>
                <input
                  type="text"
                  value={data.resolveTag || 'published'}
                  onChange={(e) => onUpdate(node.id, { resolveTag: e.target.value })}
                  placeholder="published"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Name Pattern</label>
                <input
                  type="text"
                  value={data.resolveNamePattern || ''}
                  onChange={(e) => onUpdate(node.id, { resolveNamePattern: e.target.value })}
                  placeholder="e.g. qs-episode-*"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Space</label>
                <input
                  type="text"
                  value={data.resolveSpace || ''}
                  onChange={(e) => onUpdate(node.id, { resolveSpace: e.target.value })}
                  placeholder="e.g. Construct/WorkflowOutputs"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Mode</label>
                <select
                  value={data.resolveMode || 'latest'}
                  onChange={(e) => onUpdate(node.id, { resolveMode: e.target.value })}
                  style={inputStyle}
                >
                  <option value="latest">latest</option>
                  <option value="all">all</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>Fields</label>
                <input
                  type="text"
                  defaultValue={(data.resolveFields || []).join(', ')}
                  onBlur={(e) =>
                    onUpdate(node.id, {
                      resolveFields: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="part, episode_number, episode_goal"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Comma-separated metadata fields to extract.</p>
              </div>
              <Checkbox
                checked={data.resolveFailIfMissing ?? true}
                onChange={(v) => onUpdate(node.id, { resolveFailIfMissing: v })}
                label="Fail if missing"
              />
            </div>
          )}

          {/* ── MapReduce ── */}
          {stepType === 'map_reduce' && (
            <div style={sectionShellStyle}>
              <div style={{ ...sectionTitleStyle, color: 'var(--pc-accent)' }}>MapReduce Config</div>
              <div>
                <label style={labelStyle}>Task</label>
                <textarea
                  value={data.mapReduceTask}
                  onChange={(e) => onUpdate(node.id, { mapReduceTask: e.target.value })}
                  placeholder="Overall task description"
                  rows={2}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Splits (comma-separated)</label>
                <input
                  type="text"
                  value={data.mapReduceSplits.join(', ')}
                  onChange={(e) =>
                    onUpdate(node.id, {
                      mapReduceSplits: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="segment1, segment2, segment3"
                  style={inputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Mapper</label>
                  <select
                    value={data.mapReduceMapper}
                    onChange={(e) => onUpdate(node.id, { mapReduceMapper: e.target.value })}
                    style={inputStyle}
                  >
                    <option value="claude">Claude</option>
                    <option value="codex">Codex</option>
                  </select>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Reducer</label>
                  <select
                    value={data.mapReduceReducer}
                    onChange={(e) => onUpdate(node.id, { mapReduceReducer: e.target.value })}
                    style={inputStyle}
                  >
                    <option value="claude">Claude</option>
                    <option value="codex">Codex</option>
                  </select>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Concurrency</label>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={data.mapReduceConcurrency}
                    onChange={(e) => onUpdate(node.id, { mapReduceConcurrency: parseInt(e.target.value) || 3 })}
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Timeout (s)</label>
                  <input
                    type="number"
                    min={1}
                    value={data.mapReduceTimeout}
                    onChange={(e) => onUpdate(node.id, { mapReduceTimeout: parseInt(e.target.value) || 300 })}
                    style={inputStyle}
                  />
                </div>
              </div>
            </div>
          )}

          {/* ── Notify ── */}
          {stepType === 'notify' && (
            <>
              <div>
                <label style={labelStyle}>Channels</label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {channelOptions.map((ch) => {
                    const active = (data.channels ?? []).includes(ch);
                    return (
                      <button
                        key={ch}
                        type="button"
                        onClick={() => toggleChannel(ch)}
                        style={{
                          padding: '4px 10px',
                          borderRadius: 8,
                          fontSize: 11,
                          fontWeight: 600,
                          background: active ? 'var(--pc-accent-glow)' : 'var(--pc-bg-input)',
                          color: active ? 'var(--pc-accent-light)' : 'var(--pc-text-muted)',
                          border: `1px solid ${active ? 'var(--pc-accent-dim)' : 'var(--pc-border)'}`,
                          cursor: 'pointer',
                        }}
                      >
                        {ch}
                      </button>
                    );
                  })}
                </div>
                <p style={helperStyle()}>Select one or more channels to broadcast to.</p>
              </div>
              <div>
                <label style={labelStyle}>Notify Title</label>
                <input
                  type="text"
                  value={data.notifyTitle ?? ''}
                  onChange={(e) => onUpdate(node.id, { notifyTitle: e.target.value })}
                  placeholder="Header shown above the message"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Notify Message</label>
                <ExpressionTextarea
                  value={data.notifyMessage ?? ''}
                  onChange={(next) => onUpdate(node.id, { notifyMessage: next })}
                  placeholder="Body — supports ${step_id.output} templating"
                  rows={6}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
            </>
          )}

          {/* ── Tag (NEW) ── */}
          {stepType === 'tag' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Tag Config</div>
              <div>
                <label style={labelStyle}>Item kref</label>
                <input
                  type="text"
                  value={data.tagItemKref || ''}
                  onChange={(e) => onUpdate(node.id, { tagItemKref: e.target.value })}
                  placeholder="${trigger.entity_kref}"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Supports {'${...}'} interpolation.</p>
              </div>
              <div>
                <label style={labelStyle}>Tag</label>
                <input
                  type="text"
                  value={data.tagValue || ''}
                  onChange={(e) => onUpdate(node.id, { tagValue: e.target.value })}
                  placeholder="published"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Untag (optional)</label>
                <input
                  type="text"
                  value={data.tagUntag || ''}
                  onChange={(e) => onUpdate(node.id, { tagUntag: e.target.value })}
                  placeholder="draft"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Tag to remove before applying the new one.</p>
              </div>
            </div>
          )}

          {/* ── Deprecate (NEW) ── */}
          {stepType === 'deprecate' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Deprecate Config</div>
              <div>
                <label style={labelStyle}>Item kref</label>
                <input
                  type="text"
                  value={data.deprecateItemKref || ''}
                  onChange={(e) => onUpdate(node.id, { deprecateItemKref: e.target.value })}
                  placeholder="${trigger.entity_kref}"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Supports {'${...}'} interpolation.</p>
              </div>
              <div>
                <label style={labelStyle}>Reason</label>
                <textarea
                  value={data.deprecateReason || ''}
                  onChange={(e) => onUpdate(node.id, { deprecateReason: e.target.value })}
                  placeholder="Why this item is being deprecated"
                  rows={3}
                  style={inputStyle}
                />
              </div>
            </div>
          )}

          {/* Auth profile binding (encrypted credential for external API calls) */}
          {showAuthField && (
            <div>
              <label style={labelStyle}>Auth profile</label>
              <button
                type="button"
                onClick={(e) => {
                  setAuthAnchorRect(e.currentTarget.getBoundingClientRect());
                  setAuthPickerOpen(true);
                }}
                style={{
                  ...inputStyle,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  textAlign: 'left',
                  cursor: 'pointer',
                  color: data.auth ? 'var(--pc-text-primary)' : 'var(--pc-text-faint)',
                }}
              >
                <Lock size={12} style={{ color: 'var(--construct-text-faint)', flexShrink: 0 }} />
                <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {selectedAuthProfile
                    ? `${providerLabel(selectedAuthProfile.provider)} · ${selectedAuthProfile.profile_name}`
                    : data.auth || 'None'}
                </span>
              </button>
              {data.auth && (
                <button
                  type="button"
                  onClick={() => onUpdate(node.id, { auth: '' })}
                  style={{
                    marginTop: 6,
                    padding: '4px 10px',
                    fontSize: 11,
                    fontWeight: 600,
                    borderRadius: 6,
                    border: '1px solid var(--construct-status-warning)',
                    background: 'color-mix(in srgb, var(--construct-status-warning) 14%, transparent)',
                    color: 'var(--construct-status-warning)',
                    cursor: 'pointer',
                  }}
                >
                  Clear
                </button>
              )}
              <p style={helperStyle()}>
                Optional. Bound credential for external API calls.
              </p>
              <AuthProfilePicker
                open={authPickerOpen}
                onOpenChange={setAuthPickerOpen}
                value={data.auth}
                anchorRect={authAnchorRect}
                onSelect={(id) => onUpdate(node.id, { auth: id ?? '' })}
              />
            </div>
          )}

          {/* Agent Hints (most types) */}
          {stepType !== 'conditional' &&
            stepType !== 'human_input' &&
            stepType !== 'notify' &&
            stepType !== 'tag' &&
            stepType !== 'deprecate' && (
              <div>
                <label style={labelStyle}>Agent Hints</label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {AGENT_HINT_OPTIONS.map((hint) => {
                    const active = data.agentHints.includes(hint);
                    return (
                      <button
                        key={hint}
                        type="button"
                        onClick={() => toggleHint(hint)}
                        style={{
                          padding: '4px 10px',
                          borderRadius: 8,
                          fontSize: 11,
                          fontWeight: 600,
                          background: active ? 'var(--pc-accent-glow)' : 'var(--pc-bg-input)',
                          color: active ? 'var(--pc-accent-light)' : 'var(--pc-text-muted)',
                          border: `1px solid ${active ? 'var(--pc-accent-dim)' : 'var(--pc-border)'}`,
                          cursor: 'pointer',
                        }}
                      >
                        {hint}
                      </button>
                    );
                  })}
                </div>
                <p style={helperStyle()}>Suggestions for the operator — final assignment is automatic.</p>
              </div>
            )}

          {/* Skills */}
          {stepType !== 'conditional' &&
            stepType !== 'human_input' &&
            stepType !== 'notify' &&
            stepType !== 'tag' &&
            stepType !== 'deprecate' && (
              <div>
                <label style={labelStyle}>Skills</label>
                {data.skills.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                    {data.skills.map((skill) => (
                      <span
                        key={skill}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 4,
                          padding: '2px 8px',
                          borderRadius: 6,
                          fontSize: 10,
                          fontWeight: 500,
                          background: 'var(--pc-accent-glow)',
                          color: 'var(--pc-accent-light)',
                          border: '1px solid var(--pc-accent-dim)',
                        }}
                      >
                        {skill}
                        <button
                          type="button"
                          onClick={() => removeSkill(skill)}
                          style={{ background: 'transparent', border: 0, color: 'inherit', cursor: 'pointer', padding: 0 }}
                        >
                          <X size={10} />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
                <button
                  onClick={() => setShowSkillPicker(!showSkillPicker)}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    padding: '4px 10px',
                    borderRadius: 8,
                    border: '1px solid var(--pc-accent-dim)',
                    background: showSkillPicker ? 'var(--pc-accent-glow)' : 'transparent',
                    color: 'var(--pc-accent-light)',
                    fontSize: 11,
                    fontWeight: 500,
                    cursor: 'pointer',
                  }}
                >
                  <Sparkles size={12} />
                  {showSkillPicker ? 'Hide skill picker' : 'Add skills'}
                </button>
                {showSkillPicker && (
                  <div
                    style={{
                      marginTop: 8,
                      borderRadius: 10,
                      border: '1px solid var(--pc-border)',
                      background: 'var(--pc-bg-input)',
                      overflow: 'hidden',
                    }}
                  >
                    <div style={{ position: 'relative', padding: 8 }}>
                      <Search
                        size={12}
                        style={{
                          position: 'absolute',
                          left: 16,
                          top: '50%',
                          transform: 'translateY(-50%)',
                          color: 'var(--pc-text-faint)',
                        }}
                      />
                      <input
                        type="text"
                        value={skillSearch}
                        onChange={(e) => setSkillSearch(e.target.value)}
                        placeholder="Search skills…"
                        style={{ ...inputStyle, paddingLeft: 26 }}
                      />
                    </div>
                    <div style={{ maxHeight: 144, overflowY: 'auto' }}>
                      {skillLoading ? (
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            gap: 6,
                            padding: 12,
                            fontSize: 10,
                            color: 'var(--pc-text-faint)',
                          }}
                        >
                          <Loader2 size={11} className="animate-spin" /> Loading skills…
                        </div>
                      ) : skillSearchResults.length === 0 ? (
                        <p style={{ textAlign: 'center', padding: 12, fontSize: 10, color: 'var(--pc-text-faint)' }}>
                          {allSkills.length === 0 ? 'No skills available' : 'No matching skills'}
                        </p>
                      ) : (
                        skillSearchResults.slice(0, 20).map((skill) => (
                          <button
                            key={skill.kref}
                            onClick={() => addSkill(skill.name)}
                            style={{
                              display: 'block',
                              width: '100%',
                              textAlign: 'left',
                              padding: '6px 12px',
                              fontSize: 11,
                              border: 0,
                              background: 'transparent',
                              color: 'var(--pc-text-secondary)',
                              cursor: 'pointer',
                            }}
                            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--pc-hover)')}
                            onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                          >
                            <div style={{ fontWeight: 500, color: 'var(--pc-text-primary)' }}>{skill.name}</div>
                            {skill.description && (
                              <div
                                style={{
                                  fontSize: 10,
                                  color: 'var(--pc-text-faint)',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                }}
                              >
                                {skill.description}
                              </div>
                            )}
                          </button>
                        ))
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}

          {/* Dependencies (read-only) */}
          {data.dependencyCount > 0 && (
            <div>
              <label style={labelStyle}>Dependencies</label>
              <div style={{ fontSize: 11, color: 'var(--pc-text-muted)' }}>
                {data.dependencyCount} incoming {data.dependencyCount === 1 ? 'dependency' : 'dependencies'}
              </div>
              <p style={helperStyle()}>Managed by connecting nodes on the canvas.</p>
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------------------
// Local helpers
// ---------------------------------------------------------------------------

function Checkbox({
  checked,
  onChange,
  label,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', userSelect: 'none' }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{ accentColor: 'var(--pc-accent)' }}
      />
      <span style={{ fontSize: 11, color: 'var(--pc-text-muted)' }}>{label}</span>
    </label>
  );
}
