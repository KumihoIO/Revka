/**
 * StepConfigPanel — right-rail node inspector for the workflow editor.
 *
 * Replaces legacy TaskSidePanel. Ported field-by-field, re-skinned to
 * Revka design tokens. All inputs use --pc-bg-input / --pc-border /
 * --pc-text-primary; section accents use --revka-status-* tokens.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Crosshair, Link2, Link2Off, Loader2, Lock, Plus, Search, Sparkles, Trash2, X } from 'lucide-react';
import type { Node } from '@xyflow/react';
import { type ConditionalBranchDefinition, type TaskNodeData } from '@/revka/components/workflows/yamlSync';
import type { SkillDefinition } from '@/types/api';
import { fetchSkills, getChannels } from '@/lib/api';
import Panel from '@/revka/components/ui/Panel';
import { STEP_TYPES_BY_TYPE } from './stepRegistry';
import AuthProfilePicker from './AuthProfilePicker';
import NewGcloudConfigModal from './NewGcloudConfigModal';
import { providerLabel } from './providerLabels';
import ExpressionTextarea from './ExpressionTextarea';
import { emitOpenAgentPicker } from './stepEvents';
import { useAuthProfiles } from './useAuthProfiles';
import { useGcloudConfigs } from './useGcloudConfigs';
import { slugify as slugifyShared, uniqueSlug } from './slugify';
import {
  GOOGLE_AGENTOPS_REQUIRED_TOOLS,
  type AgentToolsMode,
  expandGoogleAgentOpsRequiredTools,
  hasGoogleAgentOpsBundle,
  requiresGoogleAgentOpsToolMode,
} from './agentToolPresets';

/** Step types that surface the encrypted auth-profile dropdown. */
const AUTH_ELIGIBLE_STEP_TYPES = new Set(['agent', 'shell', 'python', 'email', 'a2a']);

const AGENT_HINT_OPTIONS = ['coder', 'researcher', 'reviewer'];
const BUILTIN_AGENT_OPTIONS = ['claude', 'codex', 'agy', 'agent', 'opencode'] as const;

function parseListInput(value: string): string[] {
  return value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function useCommaListDraft(value: string[], resetKey: string) {
  const valueText = value.join(', ');
  const [draft, setDraft] = useState(valueText);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (!editing) {
      setDraft(valueText);
    }
  }, [editing, valueText]);

  useEffect(() => {
    setEditing(false);
    setDraft(valueText);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  const commit = useCallback(() => {
    const parsed = parseListInput(draft);
    setDraft(parsed.join(', '));
    setEditing(false);
    return parsed;
  }, [draft]);

  return {
    draft,
    setDraft,
    startEditing: () => setEditing(true),
    commit,
  };
}

function defaultConditionalBranches(data: TaskNodeData): ConditionalBranchDefinition[] {
  if (data.conditionalBranches?.length > 0) {
    return data.conditionalBranches.map((branch) => ({ ...branch }));
  }

  const branches: ConditionalBranchDefinition[] = [];
  if (data.condition || data.onTrueValue) {
    branches.push({
      condition: data.condition || '',
      goto: '',
      value: data.onTrueValue || undefined,
    });
  }
  if (data.onFalseValue) {
    branches.push({
      condition: 'default',
      goto: '',
      value: data.onFalseValue,
    });
  }
  return branches.length > 0 ? branches : [
    { condition: '', goto: '', value: undefined },
    { condition: 'default', goto: '', value: undefined },
  ];
}

function conditionalBranchUpdates(branches: ConditionalBranchDefinition[]): Partial<TaskNodeData> {
  const normalized = branches.map((branch) => ({
    condition: branch.condition,
    goto: branch.goto,
    value: branch.value || undefined,
  }));
  const firstCase = normalized.find((branch) => branch.condition.trim() !== 'default');
  const fallback = normalized.find((branch) => branch.condition.trim() === 'default')
    ?? normalized.find((branch) => branch !== firstCase);

  return {
    conditionalBranches: normalized,
    condition: firstCase?.condition ?? '',
    onTrueValue: firstCase?.value ?? '',
    onFalseValue: fallback?.value ?? '',
  };
}

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
// Shared style helpers — all colors via --pc-* / --revka-* tokens
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
  /** Tooltip / aria text explaining WHY the button is disabled — surfaced
   *  on hover so users understand they need to save first. */
  runToHereDisabledReason?: string;
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
  runToHereDisabledReason,
  computeRunToHereClosure,
}: Props) {
  const dagStepIds = dagContext?.stepIds ?? [];
  const dagInputs = dagContext?.workflowInputs ?? [];
  const dagTriggerFields = dagContext?.triggerFields ?? [];
  const data = node.data;
  const stepType = data.type ?? 'agent';
  const typeDef = STEP_TYPES_BY_TYPE[stepType];
  const conditionalBranches = useMemo(
    () => defaultConditionalBranches(data),
    [data],
  );
  const conditionalTargetOptions = useMemo(
    () => existingTaskIds.filter((taskId) => taskId !== data.taskId),
    [existingTaskIds, data.taskId],
  );
  const updateConditionalBranches = useCallback(
    (branches: ConditionalBranchDefinition[]) => {
      onUpdate(node.id, conditionalBranchUpdates(branches));
    },
    [node.id, onUpdate],
  );

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
  const agentRequiredToolsDraftState = useCommaListDraft(data.agentRequiredTools || [], node.id);
  const agentOutputFieldsDraftState = useCommaListDraft(data.agentOutputFields || [], node.id);
  const agentQualityCriteriaDraftState = useCommaListDraft(data.agentQualityCriteria || [], node.id);
  const approvalApproveKeywordsDraftState = useCommaListDraft(data.humanApprovalApproveKeywords || [], node.id);
  const approvalRejectKeywordsDraftState = useCommaListDraft(data.humanApprovalRejectKeywords || [], node.id);
  const mapReduceSplitsDraftState = useCommaListDraft(data.mapReduceSplits || [], node.id);
  const kumihoSeedBundlesDraftState = useCommaListDraft(data.kumihoSeedBundles || [], node.id);
  const kumihoSeedKrefsDraftState = useCommaListDraft(data.kumihoSeedKrefs || [], node.id);
  const kumihoSeedQueriesDraftState = useCommaListDraft(data.kumihoSeedQueries || [], node.id);
  const kumihoTraversalEdgeTypesDraftState = useCommaListDraft(data.kumihoTraversalEdgeTypes || [], node.id);
  const kumihoIncludeKindsDraftState = useCommaListDraft(data.kumihoFiltersIncludeKinds || [], node.id);
  const kumihoExcludeTagsDraftState = useCommaListDraft(data.kumihoFiltersExcludeTags || [], node.id);
  const kumihoTagPreferenceDraftState = useCommaListDraft(data.kumihoLockTagPreference || [], node.id);
  const kumihoNewRevisionTagsDraftState = useCommaListDraft(data.kumihoNewRevisionTags || [], node.id);
  const kumihoPatchTagsRemoveDraftState = useCommaListDraft(data.kumihoPatchTagsRemove || [], node.id);
  const kumihoPatchTagsAddDraftState = useCommaListDraft(data.kumihoPatchTagsAdd || [], node.id);

  const handleNameChange = useCallback(
    (nextName: string) => {
      onUpdate(node.id, { name: nextName, label: nextName });
    },
    [node.id, onUpdate],
  );

  const commitNameLinkedId = useCallback(
    (nextName: string) => {
      if (idLinkedToName) {
        const nextId = uniqueTaskId(slugify(nextName), otherTaskIds);
        if (nextId !== data.taskId) onRenameStep(data.taskId, nextId);
      }
    },
    [idLinkedToName, data.taskId, otherTaskIds, onRenameStep],
  );

  const handleAgentOutputFieldsChange = useCallback(
    (nextDraft: string) => {
      agentOutputFieldsDraftState.setDraft(nextDraft);
      onUpdate(node.id, { agentOutputFields: parseListInput(nextDraft) });
    },
    [agentOutputFieldsDraftState, node.id, onUpdate],
  );

  const commitAgentOutputFieldsDraft = useCallback(() => {
    onUpdate(node.id, { agentOutputFields: agentOutputFieldsDraftState.commit() });
  }, [agentOutputFieldsDraftState, node.id, onUpdate]);

  const handleAgentRequiredToolsChange = useCallback(
    (nextDraft: string) => {
      const parsed = parseListInput(nextDraft);
      const expanded = expandGoogleAgentOpsRequiredTools(parsed);
      const unchanged = expanded.length === parsed.length
        && expanded.every((tool, index) => tool === parsed[index]);
      agentRequiredToolsDraftState.setDraft(
        unchanged ? nextDraft : expanded.join(', '),
      );
      onUpdate(node.id, {
        agentRequiredTools: expanded,
        ...(requiresGoogleAgentOpsToolMode(expanded) && !['all', 'google_agentops'].includes(data.agentTools || 'none')
          ? { agentTools: 'google_agentops' as AgentToolsMode }
          : {}),
      });
    },
    [agentRequiredToolsDraftState, data.agentTools, node.id, onUpdate],
  );

  const commitAgentRequiredToolsDraft = useCallback(() => {
    const committed = agentRequiredToolsDraftState.commit();
    const expanded = expandGoogleAgentOpsRequiredTools(committed);
    if (expanded.join(', ') !== committed.join(', ')) {
      agentRequiredToolsDraftState.setDraft(expanded.join(', '));
    }
    onUpdate(node.id, {
      agentRequiredTools: expanded,
      ...(requiresGoogleAgentOpsToolMode(expanded) && !['all', 'google_agentops'].includes(data.agentTools || 'none')
        ? { agentTools: 'google_agentops' as AgentToolsMode }
        : {}),
    });
  }, [agentRequiredToolsDraftState, data.agentTools, node.id, onUpdate]);

  const addGoogleAgentOpsToolBundle = useCallback(() => {
    const expanded = expandGoogleAgentOpsRequiredTools([
      ...(data.agentRequiredTools || []),
      'google_agents_cli',
    ]);
    agentRequiredToolsDraftState.setDraft(expanded.join(', '));
    onUpdate(node.id, {
      agentRequiredTools: expanded,
      agentTools: 'google_agentops',
    });
  }, [agentRequiredToolsDraftState, data.agentRequiredTools, node.id, onUpdate]);

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
  const [channelOptions, setChannelOptions] = useState<{ value: string; label: string }[]>([
    { value: 'dashboard', label: 'Dashboard' },
  ]);

  // Pool-agent picker — single shared mount lives in WorkflowEditor. The
  // "Choose agent…" button below dispatches OPEN_AGENT_PICKER_EVENT instead
  // of mounting its own picker, so two AgentPickers can never both be open.

  // Auth-profile picker — bound encrypted credential for external API calls.
  const { profiles: authProfiles } = useAuthProfiles();
  const {
    configs: gcloudConfigs,
    loading: gcloudConfigsLoading,
    available: gcloudConfigsAvailable,
    error: gcloudConfigsError,
    refresh: refreshGcloudConfigs,
  } = useGcloudConfigs();
  const [authPickerOpen, setAuthPickerOpen] = useState(false);
  const [authAnchorRect, setAuthAnchorRect] = useState<DOMRect | null>(null);
  const [gcloudConfigCreateOpen, setGcloudConfigCreateOpen] = useState(false);

  // Separate picker for the Manus step's ``credentials_ref`` field. Manus
  // doesn't go through the generic ``data.auth`` channel — it has its own
  // dedicated slot so the env-var fallback path stays explicit and the
  // run-view records which credential was used.
  const [manusPickerOpen, setManusPickerOpen] = useState(false);
  const [manusAnchorRect, setManusAnchorRect] = useState<DOMRect | null>(null);

  // Reset the auth picker when the user clicks a different node — without
  // this, opening the picker on node A and then clicking node B before
  // selecting leaves the picker mounted with a stale anchor (same class as
  // the AgentPicker double-mount issue).
  useEffect(() => {
    setAuthPickerOpen(false);
    setAuthAnchorRect(null);
    setManusPickerOpen(false);
    setManusAnchorRect(null);
    setGcloudConfigCreateOpen(false);
  }, [node.id]);
  const showAuthField = AUTH_ELIGIBLE_STEP_TYPES.has(stepType);
  const selectedAuthProfile = useMemo(
    () => authProfiles.find((p) => p.id === data.auth) ?? null,
    [authProfiles, data.auth],
  );
  const selectedManusProfile = useMemo(
    () => authProfiles.find((p) => p.id === data.manusCredentialsRef) ?? null,
    [authProfiles, data.manusCredentialsRef],
  );
  const selectedGcloudConfig = useMemo(
    () => gcloudConfigs.find((config) => config.name === data.a2aCloudRunConfig) ?? null,
    [gcloudConfigs, data.a2aCloudRunConfig],
  );
  const defaultGcloudConfig = useMemo(
    () => selectedGcloudConfig ?? gcloudConfigs.find((config) => config.is_active) ?? gcloudConfigs[0] ?? null,
    [gcloudConfigs, selectedGcloudConfig],
  );

  // Channels: load for human / notify steps. Values are canonical slugs (what the
  // executor matches); labels are display names.
  useEffect(() => {
    if (stepType !== 'human_input' && stepType !== 'notify' && stepType !== 'human_approval') return;
    getChannels()
      .then((channels) => {
        const byValue = new Map<string, { value: string; label: string }>();
        byValue.set('dashboard', { value: 'dashboard', label: 'Dashboard' });
        for (const ch of channels) {
          if (!ch.enabled || ch.status !== 'active') continue;
          const value = ch.id ?? ch.name;
          byValue.set(value, { value, label: ch.display_name ?? ch.name });
        }
        setChannelOptions(Array.from(byValue.values()));
      })
      .catch(() => setChannelOptions([{ value: 'dashboard', label: 'Dashboard' }]));
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

  const openPoolAgentPicker = useCallback(
    (
      target:
        | 'assign'
        | 'groupChatParticipant'
        | 'groupChatModerator'
        | 'supervisorAgent'
        | 'supervisorTemplate'
        | 'handoffTo'
        | 'a2aSkill'
        | 'mapReduceMapper'
        | 'mapReduceReducer',
      event: React.MouseEvent<HTMLElement>,
      participantIndex?: number,
    ) => {
      emitOpenAgentPicker({
        taskId: node.id,
        anchorRect: event.currentTarget.getBoundingClientRect(),
        target,
        participantIndex,
      });
    },
    [node.id],
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

  const skillSection = stepType !== 'conditional' &&
    stepType !== 'compute' &&
    stepType !== 'kumiho_context' &&
    stepType !== 'kumiho_bundle_update' &&
    stepType !== 'kumiho_patch_apply' &&
    stepType !== 'human_input' &&
    stepType !== 'notify' &&
    stepType !== 'tag' &&
    stepType !== 'deprecate' ? (
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
          type="button"
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
                    type="button"
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
    ) : null;

  const TypeIcon = typeDef?.icon;
  const googleAgentOpsBundleActive = hasGoogleAgentOpsBundle(data.agentRequiredTools || []);

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
            borderBottom: '1px solid var(--revka-border-soft)',
            position: 'relative',
          }}
        >
          <div className="revka-kicker">Step Details</div>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            {onRunToHere ? (
              <button
                type="button"
                onClick={() => setRunToHereOpen((v) => !v)}
                disabled={runToHereDisabled}
                title={
                  runToHereDisabled && runToHereDisabledReason
                    ? runToHereDisabledReason
                    : 'Run every ancestor of this step plus the step itself, then stop'
                }
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  padding: '4px 8px',
                  borderRadius: 8,
                  border: '1px solid var(--revka-border-soft)',
                  background: 'transparent',
                  color: runToHereDisabled
                    ? 'var(--pc-text-faint)'
                    : 'var(--revka-signal-selected)',
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
                border: '1px solid var(--revka-border-soft)',
                background: 'transparent',
                color: 'var(--revka-status-danger)',
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
                border: '1px solid var(--revka-border-soft)',
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
                            ? 'var(--revka-signal-selected)'
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
                    border: '1px solid var(--revka-border-soft)',
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
                    border: '1px solid var(--revka-signal-selected)',
                    background: 'var(--revka-signal-selected)',
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
          {/* Step ID — editable; syncs from Name after edits while linked */}
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
                    color: 'var(--revka-text-faint)',
                    background: 'color-mix(in srgb, var(--revka-text-faint) 12%, transparent)',
                    border: '1px solid var(--revka-border-soft)',
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
                      color: 'var(--revka-status-warning)',
                      background: 'color-mix(in srgb, var(--revka-status-warning) 14%, transparent)',
                      border: '1px solid color-mix(in srgb, var(--revka-status-warning) 36%, transparent)',
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
              onBlur={(e) => commitNameLinkedId(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  (e.currentTarget as HTMLInputElement).blur();
                }
              }}
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
                className="revka-button"
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
                  background: 'color-mix(in srgb, var(--revka-status-warning) 18%, transparent)',
                  color: 'var(--revka-status-warning)',
                  border: '1px solid color-mix(in srgb, var(--revka-status-warning) 36%, transparent)',
                }}
              >
                Conditional Gate
              </span>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {conditionalBranches.map((branch, index) => (
                  <div
                    key={`branch-${index}`}
                    style={{
                      display: 'flex',
                      flexDirection: 'column',
                      gap: 6,
                      padding: 10,
                      borderRadius: 8,
                      border: '1px solid var(--pc-border)',
                      background: 'var(--pc-bg-panel)',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                      <span style={{ ...sectionTitleStyle, color: 'var(--pc-text-muted)' }}>
                        Branch {index + 1}
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          if (conditionalBranches.length <= 1) {
                            updateConditionalBranches([{ condition: '', goto: '', value: undefined }]);
                            return;
                          }
                          updateConditionalBranches(conditionalBranches.filter((_, i) => i !== index));
                        }}
                        className="revka-icon-button"
                        title="Remove branch"
                        aria-label="Remove branch"
                        style={{ width: 24, height: 24 }}
                      >
                        <Trash2 size={12} />
                      </button>
                    </div>
                    <label style={labelStyle}>Condition</label>
                    <textarea
                      value={branch.condition}
                      onChange={(e) => {
                        const next = conditionalBranches.map((item, i) =>
                          i === index ? { ...item, condition: e.target.value } : item,
                        );
                        updateConditionalBranches(next);
                      }}
                      placeholder={index === conditionalBranches.length - 1 ? 'default' : "e.g. review.status == 'passed'"}
                      rows={2}
                      style={monoInputStyle}
                    />
                    <label style={labelStyle}>Target Step</label>
                    <select
                      value={branch.goto}
                      onChange={(e) => {
                        const next = conditionalBranches.map((item, i) =>
                          i === index ? { ...item, goto: e.target.value } : item,
                        );
                        updateConditionalBranches(next);
                      }}
                      style={inputStyle}
                    >
                      <option value="">Select target...</option>
                      {conditionalTargetOptions.map((taskId) => (
                        <option key={taskId} value={taskId}>{taskId}</option>
                      ))}
                    </select>
                    <label style={labelStyle}>Output Value (optional)</label>
                    <input
                      type="text"
                      value={branch.value || ''}
                      onChange={(e) => {
                        const next = conditionalBranches.map((item, i) =>
                          i === index ? { ...item, value: e.target.value || undefined } : item,
                        );
                        updateConditionalBranches(next);
                      }}
                      placeholder="e.g. 'approved' or review.output_data.score"
                      style={monoInputStyle}
                    />
                  </div>
                ))}
              </div>
              <button
                type="button"
                className="revka-button"
                onClick={() => updateConditionalBranches([
                  ...conditionalBranches,
                  { condition: '', goto: '', value: undefined },
                ])}
                style={{ justifyContent: 'center' }}
              >
                + Branch
              </button>
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

          {skillSection}

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
                    openPoolAgentPicker('assign', e);
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
                    <option value="agy">agy (Antigravity)</option>
                    <option value="agent">agent (Cursor)</option>
                    <option value="opencode">opencode (OpenCode)</option>
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

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <label style={labelStyle}>Max Turns</label>
                  <input
                    type="number"
                    min={1}
                    max={200}
                    value={data.agentMaxTurns ?? 3}
                    onChange={(e) => onUpdate(node.id, { agentMaxTurns: parseInt(e.target.value) || 3 })}
                    style={inputStyle}
                  />
                </div>
                <div>
                  <label style={labelStyle}>Tools</label>
                  <select
                    value={data.agentTools || 'none'}
                    onChange={(e) => onUpdate(node.id, { agentTools: e.target.value as AgentToolsMode })}
                    style={inputStyle}
                  >
                    <option value="none">none</option>
                    <option value="memory">memory</option>
                    <option value="google_agentops">google_agentops</option>
                    <option value="all">all</option>
                  </select>
                </div>
              </div>

              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <label style={labelStyle}>Required Tools</label>
                  <button
                    type="button"
                    onClick={addGoogleAgentOpsToolBundle}
                    title="Add Google Agents CLI and A2A companion tools"
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      gap: 4,
                      padding: '3px 8px',
                      borderRadius: 8,
                      border: googleAgentOpsBundleActive
                        ? '1px solid var(--revka-signal-selected)'
                        : '1px solid var(--pc-accent-dim)',
                      background: googleAgentOpsBundleActive
                        ? 'color-mix(in srgb, var(--revka-signal-selected) 18%, transparent)'
                        : 'transparent',
                      color: googleAgentOpsBundleActive
                        ? 'var(--revka-signal-selected)'
                        : 'var(--pc-accent-light)',
                      fontSize: 10,
                      fontWeight: 700,
                      cursor: 'pointer',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    <Sparkles size={11} />
                    Google AgentOps
                  </button>
                </div>
                {googleAgentOpsBundleActive && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginBottom: 6 }}>
                    {GOOGLE_AGENTOPS_REQUIRED_TOOLS.map((tool) => (
                      <span
                        key={tool}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          padding: '2px 6px',
                          borderRadius: 6,
                          border: '1px solid var(--revka-border-soft)',
                          background: 'var(--pc-bg-input)',
                          color: 'var(--pc-text-faint)',
                          fontSize: 9.5,
                          fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
                        }}
                      >
                        {tool}
                      </span>
                    ))}
                  </div>
                )}
                <input
                  type="text"
                  value={agentRequiredToolsDraftState.draft}
                  onFocus={agentRequiredToolsDraftState.startEditing}
                  onChange={(e) => handleAgentRequiredToolsChange(e.target.value)}
                  onBlur={commitAgentRequiredToolsDraft}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      (e.currentTarget as HTMLInputElement).blur();
                    }
                  }}
                  placeholder="capture_skill, tag_revision"
                  style={monoInputStyle}
                />
              </div>

              <div>
                <label style={labelStyle}>Required Structured Output Fields</label>
                <input
                  type="text"
                  value={agentOutputFieldsDraftState.draft}
                  onFocus={agentOutputFieldsDraftState.startEditing}
                  onChange={(e) => handleAgentOutputFieldsChange(e.target.value)}
                  onBlur={commitAgentOutputFieldsDraft}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      (e.currentTarget as HTMLInputElement).blur();
                    }
                  }}
                  placeholder="verdict, production_ready"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>
                  Revka appends final-output instructions and fails this step if any named field is missing.
                </p>
                <pre style={{ ...helperStyle(), fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap' }}>
{`FINAL_OUTPUT:
  verdict: ...
  production_ready: ...`}
                </pre>
              </div>

              <div style={{ paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <Checkbox
                  checked={data.agentQualityEnabled || false}
                  onChange={(v) => onUpdate(node.id, { agentQualityEnabled: v })}
                  label="Quality check"
                />
                {data.agentQualityEnabled && (
                  <div style={{ marginTop: 8, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                      <div>
                        <label style={labelStyle}>Threshold</label>
                        <input
                          type="number"
                          min={0}
                          max={1}
                          step={0.05}
                          value={data.agentQualityThreshold ?? 0.7}
                          onChange={(e) => onUpdate(node.id, { agentQualityThreshold: parseFloat(e.target.value) || 0.7 })}
                          style={inputStyle}
                        />
                      </div>
                      <div>
                        <label style={labelStyle}>Validator Model</label>
                        <input
                          type="text"
                          value={data.agentQualityModel || ''}
                          onChange={(e) => onUpdate(node.id, { agentQualityModel: e.target.value })}
                          placeholder="claude-haiku-4-5-20251001"
                          style={monoInputStyle}
                        />
                      </div>
                    </div>
                    <div>
                      <label style={labelStyle}>Criteria</label>
                      <input
                        type="text"
                        value={agentQualityCriteriaDraftState.draft}
                        onFocus={agentQualityCriteriaDraftState.startEditing}
                        onChange={(e) => {
                          agentQualityCriteriaDraftState.setDraft(e.target.value);
                          onUpdate(node.id, { agentQualityCriteria: parseListInput(e.target.value) });
                        }}
                        onBlur={() => onUpdate(node.id, { agentQualityCriteria: agentQualityCriteriaDraftState.commit() })}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            (e.currentTarget as HTMLInputElement).blur();
                          }
                        }}
                        placeholder="on_mandate, depth, language_ko"
                        style={monoInputStyle}
                      />
                    </div>
                  </div>
                )}
              </div>

              <div>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginBottom: 4 }}>
                  <label style={{ ...labelStyle, marginBottom: 0 }}>Prompt</label>
                  <Checkbox
                    checked={data.compression || false}
                    onChange={(v) => onUpdate(node.id, { compression: v })}
                    label="Compress Output Handoff"
                    title="Compresses this step's completed output before later steps use it. It does not reduce this step's input prompt; artifacts/files stay unchanged."
                  />
                </div>
                <ExpressionTextarea
                  value={data.prompt}
                  onChange={(next) => onUpdate(node.id, { prompt: next })}
                  placeholder="Agent prompt template (prefer ${step_id.output_data.artifact_path} for full upstream output)"
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
              <div style={{ ...sectionTitleStyle, color: 'var(--revka-signal-selected)' }}>ForEach Config</div>
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
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {(data.groupChatParticipants || []).map((participant, index) => (
                    <div key={`${participant}-${index}`} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <button
                        type="button"
                        onClick={(e) => openPoolAgentPicker('groupChatParticipant', e, index)}
                        style={{ ...monoInputStyle, flex: 1, textAlign: 'left', cursor: 'pointer' }}
                        title="Click to replace with a pool agent"
                      >
                        {participant}
                      </button>
                      <button
                        type="button"
                        onClick={() => onUpdate(node.id, {
                          groupChatParticipants: (data.groupChatParticipants || []).filter((_, i) => i !== index),
                        })}
                        style={{
                          padding: '5px 8px',
                          borderRadius: 6,
                          border: '1px solid var(--revka-border-soft)',
                          background: 'transparent',
                          color: 'var(--revka-status-danger)',
                          cursor: 'pointer',
                        }}
                        title="Remove participant"
                      >
                        ×
                      </button>
                    </div>
                  ))}
                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {BUILTIN_AGENT_OPTIONS.map((agentType) => (
                      <button
                        key={agentType}
                        type="button"
                        onClick={() => onUpdate(node.id, {
                          groupChatParticipants: [...(data.groupChatParticipants || []), agentType],
                        })}
                        style={{
                          padding: '4px 8px',
                          borderRadius: 6,
                          border: '1px solid var(--revka-border-soft)',
                          background: 'transparent',
                          color: 'var(--pc-text-primary)',
                          fontSize: 11,
                          cursor: 'pointer',
                        }}
                      >
                        + {agentType}
                      </button>
                    ))}
                    <button
                      type="button"
                      onClick={(e) => openPoolAgentPicker('groupChatParticipant', e)}
                      style={{
                        padding: '4px 8px',
                        borderRadius: 6,
                        border: '1px solid var(--pc-accent-dim)',
                        background: 'var(--pc-accent-glow)',
                        color: 'var(--pc-accent-light)',
                        fontSize: 11,
                        cursor: 'pointer',
                      }}
                    >
                      + Pool agent
                    </button>
                  </div>
                </div>
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
                  <button
                    type="button"
                    onClick={(e) => openPoolAgentPicker('groupChatModerator', e)}
                    style={{ ...monoInputStyle, textAlign: 'left', cursor: 'pointer' }}
                    title="Pick a moderator pool agent"
                  >
                    {data.groupChatModerator || 'claude'}
                  </button>
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    {BUILTIN_AGENT_OPTIONS.map((agentType) => (
                      <button
                        key={agentType}
                        type="button"
                        onClick={() => onUpdate(node.id, { groupChatModerator: agentType })}
                        style={{
                          padding: '2px 6px',
                          borderRadius: 6,
                          border: '1px solid var(--revka-border-soft)',
                          background: (data.groupChatModerator || 'claude') === agentType ? 'var(--pc-accent-glow)' : 'transparent',
                          color: 'var(--pc-text-primary)',
                          fontSize: 10,
                          cursor: 'pointer',
                        }}
                      >
                        {agentType}
                      </button>
                    ))}
                  </div>
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
                  <label style={labelStyle}>Supervisor Agent</label>
                  <button
                    type="button"
                    onClick={(e) => openPoolAgentPicker('supervisorAgent', e)}
                    style={{ ...monoInputStyle, textAlign: 'left', cursor: 'pointer' }}
                    title="Pick the supervisor controller from the pool"
                  >
                    {data.supervisorType || 'claude'}
                  </button>
                  <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                    {BUILTIN_AGENT_OPTIONS.map((agentType) => (
                      <button
                        key={agentType}
                        type="button"
                        onClick={() => onUpdate(node.id, { supervisorType: agentType })}
                        style={{
                          padding: '2px 6px',
                          borderRadius: 6,
                          border: '1px solid var(--revka-border-soft)',
                          background: (data.supervisorType || 'claude') === agentType ? 'var(--pc-accent-glow)' : 'transparent',
                          color: 'var(--pc-text-primary)',
                          fontSize: 10,
                          cursor: 'pointer',
                        }}
                      >
                        {agentType}
                      </button>
                    ))}
                  </div>
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
              <div>
                <label style={labelStyle}>Specialist Pool</label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {(data.supervisorTemplates || []).map((template, index) => (
                    <div key={`${template}-${index}`} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <input type="text" readOnly value={template} style={{ ...monoInputStyle, flex: 1 }} />
                      <button
                        type="button"
                        onClick={() => onUpdate(node.id, {
                          supervisorTemplates: (data.supervisorTemplates || []).filter((_, i) => i !== index),
                        })}
                        style={{
                          padding: '5px 8px',
                          borderRadius: 6,
                          border: '1px solid var(--revka-border-soft)',
                          background: 'transparent',
                          color: 'var(--revka-status-danger)',
                          cursor: 'pointer',
                        }}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={(e) => openPoolAgentPicker('supervisorTemplate', e)}
                    style={{
                      alignSelf: 'flex-start',
                      padding: '4px 8px',
                      borderRadius: 6,
                      border: '1px solid var(--pc-accent-dim)',
                      background: 'var(--pc-accent-glow)',
                      color: 'var(--pc-accent-light)',
                      fontSize: 11,
                      cursor: 'pointer',
                    }}
                  >
                    + Pool agent
                  </button>
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
              <div>
                <label style={labelStyle}>Python Interpreter</label>
                <input
                  type="text"
                  value={data.pythonInterpreter || ''}
                  onChange={(e) => onUpdate(node.id, { pythonInterpreter: e.target.value })}
                  placeholder="optional: /path/to/.venv/bin/python"
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

          {/* ── Compute ── */}
          {stepType === 'compute' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Compute Outputs</div>
              {Object.entries(data.computeOutputs || {}).map(([key, value], index) => (
                <div
                  key={`${key}-${index}`}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'minmax(92px, 0.45fr) minmax(0, 1fr) auto',
                    gap: 6,
                    alignItems: 'start',
                  }}
                >
                  <div>
                    <label style={labelStyle}>Key</label>
                    <input
                      type="text"
                      value={key}
                      onChange={(e) => {
                        const nextKey = e.target.value.trim();
                        const next = { ...(data.computeOutputs || {}) };
                        delete next[key];
                        if (nextKey) next[nextKey] = value;
                        onUpdate(node.id, { computeOutputs: next });
                      }}
                      placeholder="start"
                      spellCheck={false}
                      style={monoInputStyle}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Value</label>
                    <ExpressionTextarea
                      value={value}
                      onChange={(nextValue) => onUpdate(node.id, {
                        computeOutputs: {
                          ...(data.computeOutputs || {}),
                          [key]: nextValue,
                        },
                      })}
                      rows={2}
                      placeholder="${{ int(inputs.end) + 1 }}"
                      style={monoInputStyle}
                      stepIds={dagStepIds}
                      workflowInputs={dagInputs}
                      triggerFields={dagTriggerFields}
                    />
                  </div>
                  <button
                    type="button"
                    className="revka-icon-button"
                    title="Remove output"
                    aria-label="Remove output"
                    onClick={() => {
                      const next = { ...(data.computeOutputs || {}) };
                      delete next[key];
                      onUpdate(node.id, { computeOutputs: next });
                    }}
                    style={{ width: 28, height: 28, marginTop: 18 }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
              <button
                type="button"
                className="revka-button"
                onClick={() => {
                  const current = data.computeOutputs || {};
                  let key = 'value';
                  let suffix = 2;
                  while (Object.prototype.hasOwnProperty.call(current, key)) {
                    key = `value_${suffix}`;
                    suffix += 1;
                  }
                  onUpdate(node.id, {
                    computeOutputs: {
                      ...current,
                      [key]: '${{ 1 }}',
                    },
                  });
                }}
                style={{ justifyContent: 'center' }}
              >
                + Output
              </button>
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
                <label style={labelStyle}>Body HTML</label>
                <ExpressionTextarea
                  value={data.emailBodyHtml || ''}
                  onChange={(next) => onUpdate(node.id, { emailBodyHtml: next })}
                  rows={5}
                  placeholder="<p>Optional HTML body</p>"
                  style={monoInputStyle}
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
              <div>
                <label style={labelStyle}>Reply-To</label>
                <input
                  type="text"
                  value={data.emailReplyTo || ''}
                  onChange={(e) => onUpdate(node.id, { emailReplyTo: e.target.value })}
                  placeholder="optional reply-to address"
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
                      <label style={labelStyle}>Track secret env</label>
                      <input
                        type="text"
                        value={data.emailTrackSecretEnv || ''}
                        onChange={(e) => onUpdate(node.id, { emailTrackSecretEnv: e.target.value })}
                        placeholder="CLICK_TRACKING_SECRET"
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
              <div style={{ paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <div style={{ ...sectionTitleStyle, marginBottom: 8 }}>SMTP Overrides</div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                  <div>
                    <label style={labelStyle}>SMTP Host</label>
                    <input
                      type="text"
                      value={data.emailSmtpHost || ''}
                      onChange={(e) => onUpdate(node.id, { emailSmtpHost: e.target.value })}
                      placeholder="smtp.example.com"
                      style={monoInputStyle}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>SMTP Port</label>
                    <input
                      type="number"
                      min={1}
                      value={data.emailSmtpPort || ''}
                      onChange={(e) => onUpdate(node.id, { emailSmtpPort: parseInt(e.target.value) || 0 })}
                      placeholder="465"
                      style={inputStyle}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>SMTP Username</label>
                    <input
                      type="text"
                      value={data.emailSmtpUsername || ''}
                      onChange={(e) => onUpdate(node.id, { emailSmtpUsername: e.target.value })}
                      placeholder="optional username override"
                      style={inputStyle}
                    />
                  </div>
                  <div>
                    <label style={labelStyle}>Password Env</label>
                    <input
                      type="text"
                      value={data.emailSmtpPasswordEnv || ''}
                      onChange={(e) => onUpdate(node.id, { emailSmtpPasswordEnv: e.target.value })}
                      placeholder="SMTP_PASSWORD"
                      style={monoInputStyle}
                    />
                  </div>
                </div>
                <div style={{ marginTop: 8 }}>
                  <Checkbox
                    checked={data.emailSmtpTls ?? true}
                    onChange={(v) => onUpdate(node.id, { emailSmtpTls: v })}
                    label="Use TLS"
                  />
                </div>
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
                <label style={labelStyle}>Working Directory</label>
                <input
                  type="text"
                  value={data.imageCwd || ''}
                  onChange={(e) => onUpdate(node.id, { imageCwd: e.target.value })}
                  placeholder="default: ~/.revka/workspace"
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
              <div>
                <label style={labelStyle}>Input image paths</label>
                <textarea
                  value={(data.imageInputImages || []).join('\n')}
                  onChange={(e) => onUpdate(node.id, {
                    imageInputImages: e.target.value
                      .split(/\r?\n|,/)
                      .map((item) => item.trim())
                      .filter(Boolean),
                  })}
                  placeholder="/workspace/reference.png"
                  style={{ ...monoInputStyle, minHeight: 72, resize: 'vertical' }}
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
              {data.imageCanvas !== false && (
                <div>
                  <label style={labelStyle}>Canvas ID</label>
                  <input
                    type="text"
                    value={data.imageCanvasTarget || ''}
                    onChange={(e) => onUpdate(node.id, { imageCanvasTarget: e.target.value })}
                    placeholder="optional canvas id; blank uses default"
                    style={monoInputStyle}
                  />
                </div>
              )}
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
                    <label style={labelStyle}>Metadata Target</label>
                    <select
                      value={data.entityMetadataTarget || 'item'}
                      onChange={(e) => onUpdate(node.id, { entityMetadataTarget: e.target.value })}
                      style={inputStyle}
                    >
                      <option value="item">item</option>
                      <option value="revision">revision</option>
                      <option value="artifact">artifact</option>
                    </select>
                    <p style={helperStyle()}>Item keeps trigger auto-mapping; revision is the resolve default.</p>
                  </div>
                  <div>
                    <label style={labelStyle}>Artifact Summary Model</label>
                    <input
                      type="text"
                      value={data.artifactSummaryModel || ''}
                      onChange={(e) => onUpdate(node.id, { artifactSummaryModel: e.target.value })}
                      placeholder="claude-haiku-4-5-20251001"
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
                <label style={labelStyle}>Receiving Agent</label>
                <button
                  type="button"
                  onClick={(e) => openPoolAgentPicker('handoffTo', e)}
                  style={{ ...monoInputStyle, textAlign: 'left', cursor: 'pointer' }}
                  title="Pick the receiving agent from the pool"
                >
                  {data.handoffTo || 'codex'}
                </button>
                <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
                  {BUILTIN_AGENT_OPTIONS.map((agentType) => (
                    <button
                      key={agentType}
                      type="button"
                      onClick={() => onUpdate(node.id, { handoffTo: agentType })}
                      style={{
                        padding: '2px 6px',
                        borderRadius: 6,
                        border: '1px solid var(--revka-border-soft)',
                        background: (data.handoffTo || 'codex') === agentType ? 'var(--pc-accent-glow)' : 'transparent',
                        color: 'var(--pc-text-primary)',
                        fontSize: 10,
                        cursor: 'pointer',
                      }}
                    >
                      {agentType}
                    </button>
                  ))}
                </div>
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
              <div style={{ ...sectionTitleStyle, color: 'var(--revka-status-warning)' }}>Human Input Config</div>
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
                    {channelOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
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
              <div style={{ ...sectionTitleStyle, color: 'var(--revka-status-warning)' }}>Human Approval Config</div>
              <div>
                <label style={labelStyle}>Channel</label>
                <select
                  value={data.humanApprovalChannel}
                  onChange={(e) => onUpdate(node.id, { humanApprovalChannel: e.target.value })}
                  style={inputStyle}
                >
                  {channelOptions.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
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
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <label style={labelStyle}>On Reject Goto</label>
                  <input
                    type="text"
                    value={data.humanApprovalOnRejectGoto}
                    onChange={(e) => onUpdate(node.id, { humanApprovalOnRejectGoto: e.target.value })}
                    placeholder="optional step id"
                    style={monoInputStyle}
                  />
                </div>
                <div>
                  <label style={labelStyle}>On Reject Max</label>
                  <input
                    type="number"
                    min={1}
                    max={10}
                    value={data.humanApprovalOnRejectMax ?? 3}
                    onChange={(e) => onUpdate(node.id, { humanApprovalOnRejectMax: parseInt(e.target.value) || 3 })}
                    style={inputStyle}
                  />
                </div>
              </div>
              <div>
                <label style={labelStyle}>Approve Keywords</label>
                <input
                  type="text"
                  value={approvalApproveKeywordsDraftState.draft}
                  onFocus={approvalApproveKeywordsDraftState.startEditing}
                  onChange={(e) => {
                    approvalApproveKeywordsDraftState.setDraft(e.target.value);
                    onUpdate(node.id, { humanApprovalApproveKeywords: parseListInput(e.target.value) });
                  }}
                  onBlur={() =>
                    onUpdate(node.id, { humanApprovalApproveKeywords: approvalApproveKeywordsDraftState.commit() })
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      (e.currentTarget as HTMLInputElement).blur();
                    }
                  }}
                  placeholder="approve, approved, yes, lgtm"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Reject Keywords</label>
                <input
                  type="text"
                  value={approvalRejectKeywordsDraftState.draft}
                  onFocus={approvalRejectKeywordsDraftState.startEditing}
                  onChange={(e) => {
                    approvalRejectKeywordsDraftState.setDraft(e.target.value);
                    onUpdate(node.id, { humanApprovalRejectKeywords: parseListInput(e.target.value) });
                  }}
                  onBlur={() =>
                    onUpdate(node.id, { humanApprovalRejectKeywords: approvalRejectKeywordsDraftState.commit() })
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      (e.currentTarget as HTMLInputElement).blur();
                    }
                  }}
                  placeholder="reject, rejected, no"
                  style={monoInputStyle}
                />
              </div>
            </div>
          )}

          {/* ── A2A ── */}
          {stepType === 'manus' && (
            <div style={sectionShellStyle}>
              <div style={{ ...sectionTitleStyle, color: 'var(--revka-signal-network)' }}>Manus Config</div>
              <div>
                <label style={labelStyle}>Prompt</label>
                <ExpressionTextarea
                  value={data.manusPrompt || ''}
                  onChange={(next) => onUpdate(node.id, { manusPrompt: next })}
                  rows={5}
                  placeholder="Research the top 5 startups in Seoul working on robotics — return names + one-line descriptions."
                  style={inputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
                <p style={helperStyle()}>Free-text task for the Manus web agent. Supports ${'${...}'} interpolation.</p>
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Agent profile</label>
                  <input
                    type="text"
                    value={data.manusAgentProfile || ''}
                    onChange={(e) => onUpdate(node.id, { manusAgentProfile: e.target.value })}
                    placeholder="manus-1.6"
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Locale</label>
                  <input
                    type="text"
                    value={data.manusLocale || ''}
                    onChange={(e) => onUpdate(node.id, { manusLocale: e.target.value })}
                    placeholder="auto"
                    style={inputStyle}
                  />
                </div>
              </div>
              <div>
                <label style={labelStyle}>Connectors</label>
                <input
                  type="text"
                  defaultValue={(data.manusConnectors || []).join(', ')}
                  onBlur={(e) =>
                    onUpdate(node.id, {
                      manusConnectors: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
                    })
                  }
                  placeholder="gmail, drive, slack"
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Comma-separated connector ids enabled for this task.</p>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <label style={labelStyle}>Enable Skills</label>
                  <input
                    type="text"
                    defaultValue={(data.manusEnableSkills || []).join(', ')}
                    onBlur={(e) =>
                      onUpdate(node.id, { manusEnableSkills: parseListInput(e.target.value) })
                    }
                    placeholder="skill ids"
                    style={monoInputStyle}
                  />
                </div>
                <div>
                  <label style={labelStyle}>Force Skills</label>
                  <input
                    type="text"
                    defaultValue={(data.manusForceSkills || []).join(', ')}
                    onBlur={(e) =>
                      onUpdate(node.id, { manusForceSkills: parseListInput(e.target.value) })
                    }
                    placeholder="required skill ids"
                    style={monoInputStyle}
                  />
                </div>
              </div>
              <div>
                <label style={labelStyle}>Title (optional)</label>
                <input
                  type="text"
                  value={data.manusTitle || ''}
                  onChange={(e) => onUpdate(node.id, { manusTitle: e.target.value })}
                  placeholder="Robotics startup scan — Seoul"
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Project ID</label>
                <input
                  type="text"
                  value={data.manusProjectId || ''}
                  onChange={(e) => onUpdate(node.id, { manusProjectId: e.target.value })}
                  placeholder="optional Manus project id"
                  style={monoInputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Timeout (s)</label>
                  <input
                    type="number"
                    min={30}
                    value={data.manusTimeoutSeconds ?? 600}
                    onChange={(e) => onUpdate(node.id, { manusTimeoutSeconds: parseInt(e.target.value) || 600 })}
                    style={inputStyle}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Poll interval (s)</label>
                  <input
                    type="number"
                    min={1}
                    value={data.manusPollIntervalSeconds ?? 5}
                    onChange={(e) => onUpdate(node.id, { manusPollIntervalSeconds: parseInt(e.target.value) || 5 })}
                    style={inputStyle}
                  />
                </div>
              </div>
              <div>
                <label style={labelStyle}>Structured output schema (JSON, optional)</label>
                <textarea
                  value={data.manusStructuredOutputSchema || ''}
                  onChange={(e) => onUpdate(node.id, { manusStructuredOutputSchema: e.target.value })}
                  rows={5}
                  placeholder='{"type": "object", "properties": {"companies": {"type": "array"}}}'
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>When set, Manus returns a value matching this schema; available as ${'${step.output_data.structured_output}'}.</p>
              </div>
              <div style={{ paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <Checkbox
                  checked={data.manusAllowFailure || false}
                  onChange={(v) => onUpdate(node.id, { manusAllowFailure: v })}
                  label="Allow failure (continue workflow on Manus error)"
                />
              </div>
              {/* Credential binding — picks a stored Manus auth profile so
                  the runtime resolves the API key at execution time instead
                  of relying on the MANUS_API_KEY env var. */}
              <div style={{ paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <label style={labelStyle}>Manus credential</label>
                <button
                  type="button"
                  onClick={(e) => {
                    setManusAnchorRect(e.currentTarget.getBoundingClientRect());
                    setManusPickerOpen(true);
                  }}
                  style={{
                    ...inputStyle,
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    textAlign: 'left',
                    cursor: 'pointer',
                    color: data.manusCredentialsRef ? 'var(--pc-text-primary)' : 'var(--pc-text-faint)',
                  }}
                >
                  <Lock size={12} style={{ color: 'var(--revka-text-faint)', flexShrink: 0 }} />
                  <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {selectedManusProfile
                      ? `${providerLabel(selectedManusProfile.provider)} · ${selectedManusProfile.profile_name}`
                      : data.manusCredentialsRef || 'None — falls back to MANUS_API_KEY env var'}
                  </span>
                </button>
                {data.manusCredentialsRef && (
                  <button
                    type="button"
                    onClick={() => onUpdate(node.id, { manusCredentialsRef: '' })}
                    style={{
                      marginTop: 6,
                      padding: '4px 10px',
                      fontSize: 11,
                      fontWeight: 600,
                      borderRadius: 6,
                      border: '1px solid var(--revka-status-warning)',
                      background: 'color-mix(in srgb, var(--revka-status-warning) 14%, transparent)',
                      color: 'var(--revka-status-warning)',
                      cursor: 'pointer',
                    }}
                  >
                    Clear
                  </button>
                )}
                <p style={helperStyle()}>
                  Optional. Encrypted Manus API key from the auth-profile store.
                  When set, the runtime resolves it at execution time and skips
                  the MANUS_API_KEY env var.
                </p>
                <AuthProfilePicker
                  open={manusPickerOpen}
                  onOpenChange={setManusPickerOpen}
                  value={data.manusCredentialsRef}
                  anchorRect={manusAnchorRect}
                  providerFilter="manus"
                  onSelect={(id) => onUpdate(node.id, { manusCredentialsRef: id ?? '' })}
                />
              </div>

              {/* register_output — toggle that auto-publishes the Manus result
                  as a Kumiho entity + downloads attachments to an
                  entity-anchored path. Toggle is off by default; flipping it
                  on reveals the per-field inputs that mirror the output-step
                  Kumiho Entity panel. Toggling off keeps the previously-entered
                  values on the node so re-enabling restores user input. */}
              <div style={{ paddingTop: 8, borderTop: '1px solid var(--pc-border)' }}>
                <Checkbox
                  checked={data.manusRegisterEnabled === true}
                  onChange={(v) => onUpdate(node.id, { manusRegisterEnabled: v })}
                  label="Register output as Kumiho entity"
                />
              </div>
              {data.manusRegisterEnabled && (
                <div style={{ paddingTop: 8 }}>
                  <div style={{ ...sectionTitleStyle, color: 'var(--pc-accent-light)', marginBottom: 8 }}>
                    Kumiho Entity
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div>
                      <label style={labelStyle}>Entity Name</label>
                      <input
                        type="text"
                        value={data.manusRegisterEntityName || ''}
                        onChange={(e) => onUpdate(node.id, { manusRegisterEntityName: e.target.value })}
                        placeholder="e.g. report-${inputs.topic}"
                        style={monoInputStyle}
                      />
                    </div>
                    <div style={{ display: 'flex', gap: 8 }}>
                      <div style={{ flex: 1 }}>
                        <label style={labelStyle}>Kind</label>
                        <input
                          type="text"
                          value={data.manusRegisterEntityKind || ''}
                          onChange={(e) => onUpdate(node.id, { manusRegisterEntityKind: e.target.value })}
                          placeholder="e.g. research-report"
                          style={monoInputStyle}
                        />
                      </div>
                      <div style={{ width: 96 }}>
                        <label style={labelStyle}>Tag</label>
                        <input
                          type="text"
                          value={data.manusRegisterEntityTag || ''}
                          onChange={(e) => onUpdate(node.id, { manusRegisterEntityTag: e.target.value })}
                          placeholder="published"
                          style={monoInputStyle}
                        />
                      </div>
                    </div>
                    <div>
                      <label style={labelStyle}>Space</label>
                      <input
                        type="text"
                        value={data.manusRegisterEntitySpace || ''}
                        onChange={(e) => onUpdate(node.id, { manusRegisterEntitySpace: e.target.value })}
                        placeholder="Revka/WorkflowOutputs/Research"
                        style={monoInputStyle}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Content source</label>
                      <select
                        value={data.manusRegisterContentSource || 'message'}
                        onChange={(e) => onUpdate(node.id, {
                          manusRegisterContentSource: e.target.value as 'message' | 'structured',
                        })}
                        style={inputStyle}
                      >
                        <option value="message">message (assistant text)</option>
                        <option value="structured">structured (structured_output JSON)</option>
                      </select>
                    </div>
                    <Checkbox
                      checked={data.manusRegisterAttachments ?? true}
                      onChange={(v) => onUpdate(node.id, { manusRegisterAttachments: v })}
                      label="Download attachments to entity_dir/attachments/"
                    />
                    <p style={helperStyle()}>
                      Content is written to
                      <code> ~/.revka/artifacts/&lt;space&gt;/&lt;kind&gt;/&lt;name&gt;/content.md</code>.
                      Each attachment lands under the same dir's <code>attachments/</code>.
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {stepType === 'a2a' && (
            <div style={sectionShellStyle}>
              <div style={{ ...sectionTitleStyle, color: 'var(--revka-signal-network)' }}>A2A Config</div>
              <div>
                <label style={labelStyle}>Endpoint URL</label>
                <ExpressionTextarea
                  value={data.a2aUrl}
                  onChange={(next) => onUpdate(node.id, { a2aUrl: next })}
                  placeholder="https://agent.example.com/a2a"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Skill ID</label>
                <div style={{ display: 'flex', gap: 6 }}>
                  <input
                    type="text"
                    value={data.a2aSkillId}
                    onChange={(e) => onUpdate(node.id, { a2aSkillId: e.target.value })}
                    placeholder="Optional skill ID"
                    style={{ ...monoInputStyle, flex: 1 }}
                  />
                  <button
                    type="button"
                    onClick={(e) => openPoolAgentPicker('a2aSkill', e)}
                    style={{
                      padding: '6px 8px',
                      borderRadius: 8,
                      border: '1px solid var(--pc-accent-dim)',
                      background: 'var(--pc-accent-glow)',
                      color: 'var(--pc-accent-light)',
                      fontSize: 11,
                      cursor: 'pointer',
                    }}
                    title="Use a pool-agent template name as the A2A skill id"
                  >
                    Pool
                  </button>
                </div>
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
              <div>
                <label style={labelStyle}>Cloud Run IAM</label>
                <select
                  value={data.a2aCloudRunAuth || ''}
                  onChange={(e) => onUpdate(node.id, { a2aCloudRunAuth: e.target.value as '' | 'gcloud' })}
                  style={inputStyle}
                >
                  <option value="">none</option>
                  <option value="gcloud">gcloud</option>
                </select>
              </div>
              {data.a2aCloudRunAuth === 'gcloud' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  <div>
                    <label style={labelStyle}>Gcloud Config</label>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <select
                        value={data.a2aCloudRunConfig || ''}
                        onChange={(e) => onUpdate(node.id, { a2aCloudRunConfig: e.target.value })}
                        style={{ ...inputStyle, flex: 1 }}
                        disabled={!gcloudConfigsAvailable}
                      >
                        <option value="">
                          {gcloudConfigsLoading ? 'loading...' : 'active gcloud config'}
                        </option>
                        {gcloudConfigs.map((config) => (
                          <option key={config.name} value={config.name}>
                            {config.name}
                            {config.project ? ` - ${config.project}` : ''}
                            {config.is_active ? ' (active)' : ''}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => setGcloudConfigCreateOpen(true)}
                        title="Create gcloud config"
                        style={{
                          padding: '6px 8px',
                          borderRadius: 8,
                          border: '1px solid var(--pc-accent-dim)',
                          background: 'var(--pc-accent-glow)',
                          color: 'var(--pc-accent-light)',
                          fontSize: 11,
                          cursor: 'pointer',
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: 5,
                        }}
                      >
                        <Plus size={12} />
                        New
                      </button>
                    </div>
                    <p style={helperStyle()}>
                      {gcloudConfigsError
                        ? gcloudConfigsError
                        : selectedGcloudConfig
                          ? `${selectedGcloudConfig.account || 'account unset'} / ${selectedGcloudConfig.project || 'project unset'}`
                          : 'Uses the active Cloud SDK config unless a profile is selected.'}
                    </p>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 112px', gap: 8 }}>
                    <div>
                      <label style={labelStyle}>Cloud Run Audience</label>
                      <ExpressionTextarea
                        value={data.a2aCloudRunAudience || ''}
                        onChange={(next) => onUpdate(node.id, { a2aCloudRunAudience: next })}
                        placeholder="Defaults to endpoint origin"
                        rows={1}
                        style={monoInputStyle}
                        stepIds={dagStepIds}
                        workflowInputs={dagInputs}
                        triggerFields={dagTriggerFields}
                      />
                    </div>
                    <div>
                      <label style={labelStyle}>Auth Timeout</label>
                      <input
                        type="number"
                        min={1}
                        max={120}
                        value={data.a2aCloudRunAuthTimeout || 20}
                        onChange={(e) => onUpdate(node.id, { a2aCloudRunAuthTimeout: parseInt(e.target.value) || 20 })}
                        style={inputStyle}
                      />
                    </div>
                  </div>
                  <NewGcloudConfigModal
                    open={gcloudConfigCreateOpen}
                    onClose={() => setGcloudConfigCreateOpen(false)}
                    defaultAccount={defaultGcloudConfig?.account}
                    defaultProject={defaultGcloudConfig?.project}
                    defaultRunRegion={defaultGcloudConfig?.run_region}
                    defaultComputeRegion={defaultGcloudConfig?.compute_region}
                    onCreated={async (name) => {
                      await refreshGcloudConfigs();
                      setGcloudConfigCreateOpen(false);
                      onUpdate(node.id, {
                        a2aCloudRunAuth: 'gcloud',
                        a2aCloudRunConfig: name,
                      });
                    }}
                  />
                </div>
              )}
            </div>
          )}

          {/* ── Resolve ── */}
          {stepType === 'resolve' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Resolve Config</div>
              <div>
                <label style={labelStyle}>Entity Kind</label>
                <ExpressionTextarea
                  value={data.resolveKind || ''}
                  onChange={(next) => onUpdate(node.id, { resolveKind: next })}
                  placeholder="e.g. qs-episode-draft"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Tag</label>
                <ExpressionTextarea
                  value={data.resolveTag || 'published'}
                  onChange={(next) => onUpdate(node.id, { resolveTag: next })}
                  placeholder="published"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Name Pattern</label>
                <ExpressionTextarea
                  value={data.resolveNamePattern || ''}
                  onChange={(next) => onUpdate(node.id, { resolveNamePattern: next })}
                  placeholder="e.g. qs-episode-*"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Space</label>
                <ExpressionTextarea
                  value={data.resolveSpace || ''}
                  onChange={(next) => onUpdate(node.id, { resolveSpace: next })}
                  placeholder="e.g. Revka/${inputs.team}/WorkflowOutputs"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Artifact Name</label>
                <ExpressionTextarea
                  value={data.resolveArtifactName || ''}
                  onChange={(next) => onUpdate(node.id, { resolveArtifactName: next })}
                  placeholder="SKILL.md"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
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
                <label style={labelStyle}>Metadata Source</label>
                <select
                  value={data.resolveMetadataSource || 'revision'}
                  onChange={(e) => onUpdate(node.id, { resolveMetadataSource: e.target.value })}
                  style={inputStyle}
                >
                  <option value="revision">revision</option>
                  <option value="item">item</option>
                  <option value="artifact">artifact</option>
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

          {/* ── Kumiho Context ── */}
          {stepType === 'kumiho_context' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Kumiho Context Config</div>
              <div>
                <label style={labelStyle}>Project</label>
                <ExpressionTextarea
                  value={data.kumihoProject || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoProject: next })}
                  placeholder="StoryProject"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Mode</label>
                <select
                  value={data.kumihoMode || 'graph_augmented_context'}
                  onChange={(e) => onUpdate(node.id, { kumihoMode: e.target.value })}
                  style={inputStyle}
                >
                  <option value="graph_augmented_context">graph_augmented_context</option>
                  <option value="bundle_context">bundle_context</option>
                  <option value="semantic_context">semantic_context</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>Seed Bundles</label>
                <textarea
                  value={kumihoSeedBundlesDraftState.draft}
                  onFocus={kumihoSeedBundlesDraftState.startEditing}
                  onChange={(e) => kumihoSeedBundlesDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoSeedBundles: kumihoSeedBundlesDraftState.commit() })}
                  placeholder="series-main-canon, series-active-storylines"
                  rows={2}
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Seed Krefs</label>
                <textarea
                  value={kumihoSeedKrefsDraftState.draft}
                  onFocus={kumihoSeedKrefsDraftState.startEditing}
                  onChange={(e) => kumihoSeedKrefsDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoSeedKrefs: kumihoSeedKrefsDraftState.commit() })}
                  placeholder="${latest-production-episode.output_data.kref}"
                  rows={2}
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Seed Queries</label>
                <textarea
                  value={kumihoSeedQueriesDraftState.draft}
                  onFocus={kumihoSeedQueriesDraftState.startEditing}
                  onChange={(e) => kumihoSeedQueriesDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoSeedQueries: kumihoSeedQueriesDraftState.commit() })}
                  placeholder="${inputs.episode_goal}, ${inputs.must_include}"
                  rows={3}
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Traversal Depth</label>
                <input
                  type="number"
                  min={0}
                  max={3}
                  value={data.kumihoTraversalMaxDepth ?? 2}
                  onChange={(e) => onUpdate(node.id, { kumihoTraversalMaxDepth: Math.min(3, Math.max(0, parseInt(e.target.value) || 0)) })}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Traversal Direction</label>
                <select
                  value={data.kumihoTraversalDirection || 'both'}
                  onChange={(e) => onUpdate(node.id, { kumihoTraversalDirection: e.target.value })}
                  style={inputStyle}
                >
                  <option value="both">both</option>
                  <option value="out">out</option>
                  <option value="in">in</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>Edge Types</label>
                <input
                  type="text"
                  value={kumihoTraversalEdgeTypesDraftState.draft}
                  onFocus={kumihoTraversalEdgeTypesDraftState.startEditing}
                  onChange={(e) => kumihoTraversalEdgeTypesDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoTraversalEdgeTypes: kumihoTraversalEdgeTypesDraftState.commit() })}
                  placeholder="DEPENDS_ON, REFERENCES, ADVANCES, UPDATES, CONTRADICTS"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Include Kinds</label>
                <textarea
                  value={kumihoIncludeKindsDraftState.draft}
                  onFocus={kumihoIncludeKindsDraftState.startEditing}
                  onChange={(e) => kumihoIncludeKindsDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoFiltersIncludeKinds: kumihoIncludeKindsDraftState.commit() })}
                  placeholder="canon-rule, character-state, storyline, webnovel-episode"
                  rows={2}
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Exclude Tags</label>
                <input
                  type="text"
                  value={kumihoExcludeTagsDraftState.draft}
                  onFocus={kumihoExcludeTagsDraftState.startEditing}
                  onChange={(e) => kumihoExcludeTagsDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoFiltersExcludeTags: kumihoExcludeTagsDraftState.commit() })}
                  placeholder="deprecated"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Max Items</label>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={data.kumihoFiltersMaxItems ?? 50}
                  onChange={(e) => onUpdate(node.id, { kumihoFiltersMaxItems: Math.min(100, Math.max(1, parseInt(e.target.value) || 50)) })}
                  style={inputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Ranking Method</label>
                <select
                  value={data.kumihoRankingMethod || 'hybrid'}
                  onChange={(e) => onUpdate(node.id, { kumihoRankingMethod: e.target.value })}
                  style={inputStyle}
                >
                  <option value="hybrid">hybrid</option>
                  <option value="graph">graph</option>
                  <option value="semantic">semantic</option>
                  <option value="none">none</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>Semantic Query</label>
                <ExpressionTextarea
                  value={data.kumihoRankingSemanticQuery || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoRankingSemanticQuery: next })}
                  placeholder="${inputs.episode_goal} ${inputs.must_include}"
                  rows={2}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Tag Preference</label>
                <input
                  type="text"
                  value={kumihoTagPreferenceDraftState.draft}
                  onFocus={kumihoTagPreferenceDraftState.startEditing}
                  onChange={(e) => kumihoTagPreferenceDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoLockTagPreference: kumihoTagPreferenceDraftState.commit() })}
                  placeholder="current, active, production-ready, ready, published, latest"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Output Format</label>
                <input
                  type="text"
                  value={data.kumihoOutputFormat || 'context_pack'}
                  onChange={(e) => onUpdate(node.id, { kumihoOutputFormat: e.target.value })}
                  placeholder="episode_context_pack"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Max Artifact Chars</label>
                <input
                  type="number"
                  min={0}
                  value={data.kumihoOutputMaxArtifactCharsPerItem ?? 3000}
                  onChange={(e) => onUpdate(node.id, { kumihoOutputMaxArtifactCharsPerItem: Math.max(0, parseInt(e.target.value) || 0) })}
                  style={inputStyle}
                />
              </div>
              <Checkbox
                checked={data.kumihoOutputIncludeArtifactSummaries ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoOutputIncludeArtifactSummaries: v })}
                label="Artifact summaries"
              />
              <Checkbox
                checked={data.kumihoOutputIncludeArtifactContent ?? false}
                onChange={(v) => onUpdate(node.id, { kumihoOutputIncludeArtifactContent: v })}
                label="Artifact content"
              />
              <Checkbox
                checked={data.kumihoOutputIncludeEdgeMap ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoOutputIncludeEdgeMap: v })}
                label="Edge map"
              />
              <Checkbox
                checked={data.kumihoOutputIncludeConflictWarnings ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoOutputIncludeConflictWarnings: v })}
                label="Conflict warnings"
              />
              <Checkbox
                checked={data.kumihoOutputIncludeMissingContext ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoOutputIncludeMissingContext: v })}
                label="Missing context"
              />
            </div>
          )}

          {/* ── Kumiho Bundle Update ── */}
          {stepType === 'kumiho_bundle_update' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Kumiho Bundle Update Config</div>
              <div>
                <label style={labelStyle}>Project</label>
                <ExpressionTextarea
                  value={data.kumihoProject || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoProject: next })}
                  placeholder="StoryProject"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Mode</label>
                <select
                  value={data.kumihoMode || 'add_members'}
                  onChange={(e) => onUpdate(node.id, { kumihoMode: e.target.value })}
                  style={inputStyle}
                >
                  <option value="add_members">add_members</option>
                  <option value="remove_members">remove_members</option>
                  <option value="replace_members">replace_members</option>
                  <option value="mixed">mixed</option>
                </select>
              </div>
              <Checkbox
                checked={data.kumihoCreateIfMissing ?? false}
                onChange={(v) => onUpdate(node.id, { kumihoCreateIfMissing: v })}
                label="Create missing bundles"
              />
              <Checkbox
                checked={data.kumihoIdempotent ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoIdempotent: v })}
                label="Idempotent"
              />
              <Checkbox
                checked={data.kumihoFailIfMissingBundle ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoFailIfMissingBundle: v })}
                label="Fail if bundle missing"
              />
              <Checkbox
                checked={data.kumihoFailIfMissingItem ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoFailIfMissingItem: v })}
                label="Fail if item missing"
              />
              <Checkbox
                checked={data.kumihoAllowProtected ?? false}
                onChange={(v) => onUpdate(node.id, { kumihoAllowProtected: v })}
                label="Allow protected bundles"
              />
              <div>
                <label style={labelStyle}>Updates JSON</label>
                <textarea
                  key={`kumiho-updates-${node.id}`}
                  defaultValue={JSON.stringify(data.kumihoUpdates || [], null, 2)}
                  onBlur={(e) => {
                    try {
                      const parsed = JSON.parse(e.target.value || '[]');
                      onUpdate(node.id, { kumihoUpdates: Array.isArray(parsed) ? parsed : [] });
                    } catch {
                      // Keep the previous structured value; YAML editor remains available for advanced edits.
                    }
                  }}
                  rows={8}
                  style={monoInputStyle}
                />
                <p style={helperStyle()}>Array of bundle update objects. Advanced YAML fields are preserved through kumiho_config.</p>
              </div>
            </div>
          )}

          {/* ── Kumiho Patch Apply ── */}
          {stepType === 'kumiho_patch_apply' && (
            <div style={sectionShellStyle}>
              <div style={sectionTitleStyle}>Kumiho Patch Apply Config</div>
              <div>
                <label style={labelStyle}>Project</label>
                <ExpressionTextarea
                  value={data.kumihoProject || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoProject: next })}
                  placeholder="StoryProject"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Patch Kref</label>
                <ExpressionTextarea
                  value={data.kumihoPatchKref || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoPatchKref: next })}
                  placeholder="${canon-patch-loader.output_data.kref}"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <Checkbox
                checked={data.kumihoDryRun ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoDryRun: v })}
                label="Dry run"
              />
              <Checkbox
                checked={data.kumihoApprovalRequired ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoApprovalRequired: v })}
                label="Approval required"
              />
              <div>
                <label style={labelStyle}>Approved Expression</label>
                <ExpressionTextarea
                  value={String(data.kumihoApprovalApproved ?? false)}
                  onChange={(next) => onUpdate(node.id, { kumihoApprovalApproved: next })}
                  placeholder="${patch-approval.output_data.approved}"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Approved By</label>
                <ExpressionTextarea
                  value={data.kumihoApprovalApprovedBy || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoApprovalApprovedBy: next })}
                  placeholder="${patch-approval.output_data.approved_by}"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Approval Note</label>
                <ExpressionTextarea
                  value={data.kumihoApprovalNote || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoApprovalNote: next })}
                  placeholder="${patch-approval.output_data.note}"
                  rows={2}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <Checkbox
                checked={data.kumihoApplyCreateRevisions ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoApplyCreateRevisions: v })}
                label="Create revisions"
              />
              <Checkbox
                checked={data.kumihoApplyCreateEdges ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoApplyCreateEdges: v })}
                label="Create edges"
              />
              <Checkbox
                checked={data.kumihoApplyUpdateTags ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoApplyUpdateTags: v })}
                label="Update tags"
              />
              <Checkbox
                checked={data.kumihoApplyUntagPreviousCurrent ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoApplyUntagPreviousCurrent: v })}
                label="Untag previous current"
              />
              <Checkbox
                checked={data.kumihoApplyUpdateBundles ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoApplyUpdateBundles: v })}
                label="Update bundles"
              />
              <Checkbox
                checked={data.kumihoApplySaveApplyReport ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoApplySaveApplyReport: v })}
                label="Save apply report"
              />
              <div>
                <label style={labelStyle}>New Revision Tags</label>
                <input
                  type="text"
                  value={kumihoNewRevisionTagsDraftState.draft}
                  onFocus={kumihoNewRevisionTagsDraftState.startEditing}
                  onChange={(e) => kumihoNewRevisionTagsDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoNewRevisionTags: kumihoNewRevisionTagsDraftState.commit() })}
                  placeholder="current, approved"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Patch Tags Remove</label>
                <input
                  type="text"
                  value={kumihoPatchTagsRemoveDraftState.draft}
                  onFocus={kumihoPatchTagsRemoveDraftState.startEditing}
                  onChange={(e) => kumihoPatchTagsRemoveDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoPatchTagsRemove: kumihoPatchTagsRemoveDraftState.commit() })}
                  placeholder="candidate"
                  style={monoInputStyle}
                />
              </div>
              <div>
                <label style={labelStyle}>Patch Tags Add</label>
                <input
                  type="text"
                  value={kumihoPatchTagsAddDraftState.draft}
                  onFocus={kumihoPatchTagsAddDraftState.startEditing}
                  onChange={(e) => kumihoPatchTagsAddDraftState.setDraft(e.target.value)}
                  onBlur={() => onUpdate(node.id, { kumihoPatchTagsAdd: kumihoPatchTagsAddDraftState.commit() })}
                  placeholder="applied"
                  style={monoInputStyle}
                />
              </div>
              {([
                ['Pending Patch Bundle', 'kumihoPendingPatchBundle', 'pending-canon-patches'],
                ['Applied Patch Bundle', 'kumihoAppliedPatchBundle', 'applied-canon-patches'],
                ['Current State Bundle', 'kumihoCurrentStateBundle', 'current-character-states'],
                ['Active Storyline Bundle', 'kumihoActiveStorylineBundle', 'active-storylines'],
                ['Active Foreshadow Bundle', 'kumihoActiveForeshadowBundle', 'active-foreshadow'],
                ['Timeline Bundle', 'kumihoTimelineBundle', 'timeline-events'],
              ] as Array<[string, keyof TaskNodeData, string]>).map(([label, key, placeholder]) => (
                <div key={String(key)}>
                  <label style={labelStyle}>{label}</label>
                  <input
                    type="text"
                    value={String(data[key] || '')}
                    onChange={(e) => onUpdate(node.id, { [key]: e.target.value } as Partial<TaskNodeData>)}
                    placeholder={placeholder}
                    style={monoInputStyle}
                  />
                </div>
              ))}
              <Checkbox
                checked={data.kumihoRequireEvidenceLocator ?? true}
                onChange={(v) => onUpdate(node.id, { kumihoRequireEvidenceLocator: v })}
                label="Require evidence_locator"
              />
              <div>
                <label style={labelStyle}>Source Episode Kref</label>
                <ExpressionTextarea
                  value={data.kumihoSourceEpisodeKref || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoSourceEpisodeKref: next })}
                  placeholder="${episode-loader.output_data.kref}"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
              <div>
                <label style={labelStyle}>Source Context Pack Kref</label>
                <ExpressionTextarea
                  value={data.kumihoSourceContextPackKref || ''}
                  onChange={(next) => onUpdate(node.id, { kumihoSourceContextPackKref: next })}
                  placeholder="${context-pack-loader.output_data.kref}"
                  rows={1}
                  style={monoInputStyle}
                  stepIds={dagStepIds}
                  workflowInputs={dagInputs}
                  triggerFields={dagTriggerFields}
                />
              </div>
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
                  value={mapReduceSplitsDraftState.draft}
                  onFocus={mapReduceSplitsDraftState.startEditing}
                  onChange={(e) => {
                    mapReduceSplitsDraftState.setDraft(e.target.value);
                    onUpdate(node.id, { mapReduceSplits: parseListInput(e.target.value) });
                  }}
                  onBlur={() => onUpdate(node.id, { mapReduceSplits: mapReduceSplitsDraftState.commit() })}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      (e.currentTarget as HTMLInputElement).blur();
                    }
                  }}
                  placeholder="segment1, segment2, segment3"
                  style={inputStyle}
                />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Mapper</label>
                  <button
                    type="button"
                    onClick={(e) => openPoolAgentPicker('mapReduceMapper', e)}
                    style={{ ...monoInputStyle, textAlign: 'left', cursor: 'pointer' }}
                  >
                    {data.mapReduceMapper || 'claude'}
                  </button>
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Reducer</label>
                  <button
                    type="button"
                    onClick={(e) => openPoolAgentPicker('mapReduceReducer', e)}
                    style={{ ...monoInputStyle, textAlign: 'left', cursor: 'pointer' }}
                  >
                    {data.mapReduceReducer || 'claude'}
                  </button>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6 }}>
                {BUILTIN_AGENT_OPTIONS.map((agentType) => (
                  <button
                    key={`mapper-${agentType}`}
                    type="button"
                    onClick={() => onUpdate(node.id, { mapReduceMapper: agentType })}
                    style={{
                      padding: '2px 6px',
                      borderRadius: 6,
                      border: '1px solid var(--revka-border-soft)',
                      background: (data.mapReduceMapper || 'claude') === agentType ? 'var(--pc-accent-glow)' : 'transparent',
                      color: 'var(--pc-text-primary)',
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    mapper {agentType}
                  </button>
                ))}
                {BUILTIN_AGENT_OPTIONS.map((agentType) => (
                  <button
                    key={`reducer-${agentType}`}
                    type="button"
                    onClick={() => onUpdate(node.id, { mapReduceReducer: agentType })}
                    style={{
                      padding: '2px 6px',
                      borderRadius: 6,
                      border: '1px solid var(--revka-border-soft)',
                      background: (data.mapReduceReducer || 'claude') === agentType ? 'var(--pc-accent-glow)' : 'transparent',
                      color: 'var(--pc-text-primary)',
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    reducer {agentType}
                  </button>
                ))}
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
                  {channelOptions.map((opt) => {
                    const active = (data.channels ?? []).includes(opt.value);
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        onClick={() => toggleChannel(opt.value)}
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
                        {opt.label}
                      </button>
                    );
                  })}
                </div>
                <p style={helperStyle()}>Select one or more channels to broadcast to.</p>
              </div>
              <div>
                <label style={labelStyle}>Channel ID Override</label>
                <input
                  type="text"
                  value={data.notifyChannelId ?? ''}
                  onChange={(e) => onUpdate(node.id, { notifyChannelId: e.target.value })}
                  placeholder="optional channel/thread/chat id"
                  style={monoInputStyle}
                />
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
                <Lock size={12} style={{ color: 'var(--revka-text-faint)', flexShrink: 0 }} />
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
                    border: '1px solid var(--revka-status-warning)',
                    background: 'color-mix(in srgb, var(--revka-status-warning) 14%, transparent)',
                    color: 'var(--revka-status-warning)',
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
            stepType !== 'kumiho_context' &&
            stepType !== 'kumiho_bundle_update' &&
            stepType !== 'kumiho_patch_apply' &&
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
  title,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  title?: string;
}) {
  return (
    <label
      title={title}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', userSelect: 'none' }}
    >
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
