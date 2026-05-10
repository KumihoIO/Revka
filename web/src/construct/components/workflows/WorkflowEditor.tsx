/**
 * WorkflowEditor — P0 redesign on Construct dashboard tokens.
 *
 * Replaces the legacy `web/src/components/workflows/WorkflowEditor.tsx`.
 * Reuses the same data layer (yamlSync) so the YAML schema and the rest of
 * the dashboard (Dashboard, Workflows page DAG view) keep working.
 *
 * Surfaces:
 *   - Toolbar `+ Add Step` button → opens StepTypePalette
 *   - ⌘K / Ctrl+K → opens StepTypePalette
 *   - Right-click on empty canvas → context menu
 *   - Drop a noodle on empty canvas → opens palette in "source" mode
 *   - Empty canvas → EditorCommandList overlay
 *
 * Side panel: StepConfigPanel (replacement for legacy TaskSidePanel).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  Code,
  Crosshair,
  LayoutGrid,
  Plus,
  Radio,
  Wand2,
  X,
  Zap,
} from 'lucide-react';
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import type { WorkflowDefinition } from '@/types/api';
import { taskNodeTypes } from '@/components/workflows/TaskNode';
import { gateNodeTypes } from '@/components/workflows/GateNode';
import {
  GATE_EDGE_STYLES,
  flowToTasks,
  parseWorkflowMeta,
  parseWorkflowYaml,
  tasksToFlow,
  tasksToYaml,
  type InputDef,
  type TaskNodeData,
  type WorkflowMeta,
} from '@/components/workflows/yamlSync';
import { hasCycle, layoutNodes } from '@/components/teams/graphHelpers';

import Panel from '@/construct/components/ui/Panel';
import EditorCommandList from './EditorCommandList';
import StepConfigPanel from './StepConfigPanel';
import StepTypePalette from './StepTypePalette';
import AgentPicker from './AgentPicker';
import ArchitectPanel from './ArchitectPanel';
import RevisionHistoryStrip from './RevisionHistoryStrip';
import { useAgentRoster } from './useAgentRoster';
import {
  ADD_STEP_EVENT,
  OPEN_AGENT_PICKER_EVENT,
  emitOpenAgentPicker,
  type AddStepDetail,
  type OpenAgentPickerDetail,
} from './stepEvents';
import {
  useWorkflowEvents,
  type WorkflowRevisionPublishedEvent,
} from './useWorkflowEvents';
import { fetchWorkflowByRevisionKref } from '@/lib/api';
import '@/construct/styles/editor-chrome.css';

const allNodeTypes = { ...taskNodeTypes, ...gateNodeTypes };

interface WorkflowFormData {
  name: string;
  description: string;
  definition: string;
  version: string;
  tags: string[];
}

interface WorkflowEditorProps {
  workflow: WorkflowDefinition | null;
  // Returns a promise that rejects with an Error whose `.message` is a
  // human-readable summary (multi-line ok). The editor surfaces the message
  // inline so server-side validation errors are visible while the editor
  // overlay is open.
  onSave: (data: WorkflowFormData) => Promise<void>;
  onCancel: () => void;
  saving: boolean;
  mode?: 'create' | 'edit' | 'duplicate';
  containerClassName?: string;
}

export default function WorkflowEditor(props: WorkflowEditorProps) {
  return (
    <ReactFlowProvider>
      <WorkflowEditorInner {...props} />
    </ReactFlowProvider>
  );
}

// ---------------------------------------------------------------------------
// Edge auto-insert helpers
// ---------------------------------------------------------------------------

// Map step type → the TaskNodeData field that holds the user-facing primary
// text (the prompt / command / template / message). When the user wires an
// edge into one of these step types we auto-append `${source.output}` so the
// canvas action implies the matching `${ref}` interpolation in the target's
// text — without this, the edge alone yields a `depends_on` that PR #182's
// validator rejects as unused. Step types not in this map (parallel, goto,
// tag, deprecate, human_approval, human_input, for_each, …) have no obvious
// text field; the edge alone is enough.
const STEP_PRIMARY_TEXT_FIELD: Record<string, keyof TaskNodeData> = {
  agent: 'prompt',
  shell: 'shellCommand',
  output: 'outputTemplate',
  conditional: 'condition',
  notify: 'notifyMessage',
  email: 'emailBody',
  python: 'pythonCode',
};

// All TaskNodeData text fields that may carry `${step.<field>}` references —
// mirrors INTERPOLATION_TEXT_FIELDS in yamlSync.ts (camelCase here, snake in
// the TaskDefinition shape). Used to detect whether the target already
// references the source so we don't pollute with a duplicate.
const TASK_INTERPOLATION_FIELDS: ReadonlyArray<keyof TaskNodeData> = [
  'prompt',
  'shellCommand',
  'pythonCode',
  'pythonArgs',
  'pythonScript',
  'emailBody',
  'emailBodyHtml',
  'emailSubject',
  'emailTo',
  'emailCc',
  'emailBcc',
  'imagePrompt',
  'outputTemplate',
  'condition',
  'gotoCondition',
  'humanInputMessage',
  'humanApprovalMessage',
  'notifyMessage',
  'notifyTitle',
  'groupChatTopic',
  'supervisorTask',
  'a2aMessage',
  'mapReduceTask',
  'handoffReason',
];

// Same shape as yamlSync's STEP_REF_REGEX. Inlined so we don't import a
// non-exported regex (and so this stays a frontend-only change).
const EDITOR_STEP_REF_REGEX = /\$\{([a-zA-Z_][a-zA-Z0-9_-]*)(?:\.[a-zA-Z_][a-zA-Z0-9_.-]*)?\}/g;

function targetAlreadyReferencesSource(data: TaskNodeData, sourceId: string): boolean {
  for (const field of TASK_INTERPOLATION_FIELDS) {
    const value = data[field];
    if (typeof value !== 'string' || !value) continue;
    EDITOR_STEP_REF_REGEX.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = EDITOR_STEP_REF_REGEX.exec(value)) !== null) {
      if (m[1] === sourceId) return true;
    }
  }
  return false;
}

// ---------------------------------------------------------------------------
// Time helpers
// ---------------------------------------------------------------------------

// Render a recent timestamp (ISO/RFC3339 string) as "just now", "Ns ago",
// "Nm ago", "Nh ago" or fall back to the raw string. Used by the conflict
// banner and the "Operator edited" pill — both surface remote events that
// happened seconds-to-hours ago, never further out.
function formatRelative(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const seconds = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return `${hours}h ago`;
}

// ---------------------------------------------------------------------------
// Default node data — mirrors legacy editor (must include every TaskNodeData
// field or React Flow will see undefined).
// ---------------------------------------------------------------------------

function defaultNodeData(id: string, overrides?: Partial<TaskNodeData>): TaskNodeData {
  return {
    label: id,
    taskId: id,
    name: id,
    description: '',
    type: 'agent',
    agentHints: [],
    skills: [],
    assign: '',
    template: '',
    paramCount: 0,
    dependencyCount: 0,
    condition: '',
    onTrueValue: '',
    onFalseValue: '',
    channel: '',
    channels: [],
    agentType: '',
    role: '',
    prompt: '',
    timeout: 300,
    parallelJoin: 'all',
    gotoTarget: '',
    gotoMaxIterations: 3,
    groupChatTopic: '',
    groupChatParticipants: [],
    groupChatMaxRounds: 8,
    supervisorTask: '',
    supervisorMaxIterations: 5,
    shellCommand: '',
    outputFormat: 'markdown',
    entityName: '',
    entityKind: '',
    entityTag: '',
    entitySpace: '',
    entityMetadata: {},
    handoffFrom: '',
    handoffTo: '',
    handoffReason: '',
    retry: 0,
    retryDelay: 5,
    model: '',
    shellTimeout: 60,
    shellAllowFailure: false,
    gotoCondition: '',
    parallelMaxConcurrency: 5,
    humanInputMessage: '',
    humanInputTimeout: 3600,
    humanApprovalMessage: '',
    humanApprovalTimeout: 3600,
    humanApprovalChannel: 'dashboard',
    humanApprovalChannelId: '',
    outputTemplate: '',
    a2aUrl: '',
    a2aSkillId: '',
    a2aMessage: '',
    a2aTimeout: 300,
    resolveKind: '',
    resolveTag: 'published',
    resolveNamePattern: '',
    resolveSpace: '',
    resolveMode: 'latest',
    resolveFields: [],
    resolveFailIfMissing: true,
    mapReduceTask: '',
    mapReduceSplits: [],
    mapReduceMapper: 'claude',
    mapReduceReducer: 'claude',
    mapReduceConcurrency: 3,
    mapReduceTimeout: 300,
    supervisorType: 'claude',
    supervisorTimeout: 300,
    groupChatModerator: 'claude',
    groupChatStrategy: 'moderator_selected',
    groupChatTimeout: 120,
    handoffTask: '',
    handoffTimeout: 300,
    forEachSteps: [],
    forEachRange: '',
    forEachItems: [],
    forEachVariable: 'item',
    forEachCarryForward: true,
    forEachFailFast: true,
    forEachMaxIterations: 20,
    notifyMessage: '',
    notifyTitle: '',
    pythonScript: '',
    pythonCode: '',
    pythonArgs: '',
    pythonTimeout: 60,
    pythonAllowFailure: false,
    emailTo: '',
    emailSubject: '',
    emailBody: '',
    emailBodyHtml: '',
    emailFrom: '',
    emailCc: '',
    emailBcc: '',
    emailReplyTo: '',
    emailTrackClicks: false,
    emailTrackKref: '',
    emailTrackBaseUrl: '',
    emailSmtpHost: '',
    emailDryRun: false,
    emailTimeout: 30,
    imagePrompt: '',
    imageCount: 1,
    imageCanvas: true,
    imageRegisterArtifact: true,
    imageSpace: '',
    imageItemName: '',
    imageOutputPath: '',
    imageOutputPattern: '',
    imageSandbox: '',
    imageCwd: '',
    imageTimeout: 1200,
    tagItemKref: '',
    tagValue: '',
    tagUntag: '',
    deprecateItemKref: '',
    deprecateReason: '',
    ...overrides,
  };
}

// Build initial node data overrides for a given step type. `type` is the
// canonical executor identifier (matches StepType in operator schema) and is
// the only step-kind field stored on the node going forward.
function defaultsForType(type: string): Partial<TaskNodeData> {
  return { type };
}

// ---------------------------------------------------------------------------
// Right-click context menu
// ---------------------------------------------------------------------------

interface ContextMenuState {
  screenX: number;
  screenY: number;
  flowX: number;
  flowY: number;
}

// ---------------------------------------------------------------------------
// Inner editor (uses ReactFlow context)
// ---------------------------------------------------------------------------

function WorkflowEditorInner({
  workflow,
  onSave,
  onCancel,
  saving,
  mode,
  containerClassName,
}: WorkflowEditorProps) {
  const resolvedMode: 'create' | 'edit' | 'duplicate' = mode ?? (workflow ? 'edit' : 'create');
  const isMac = typeof navigator !== 'undefined' && /mac/i.test(navigator.platform);

  // ── Workflow-level state ────────────────────────────────────────────────
  const [name, setName] = useState(workflow?.name ?? '');
  const [description, setDescription] = useState(workflow?.description ?? '');
  const [tags, setTags] = useState<string[]>(workflow?.tags ? [...workflow.tags] : []);
  const [tagInput, setTagInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [yamlText, setYamlText] = useState(workflow?.definition ?? '');

  const [workflowMeta, setWorkflowMeta] = useState<WorkflowMeta>({
    name: '',
    version: '1.0',
    description: '',
    tags: [],
    triggers: [],
    inputs: [],
    outputs: [],
    defaultCwd: '',
    defaultTimeout: 300,
    maxTotalTime: 3600,
    checkpoint: true,
  });

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  // Architect chat panel — toggled by ⌘J or the Wand2 toolbar button.
  const [architectPanelOpen, setArchitectPanelOpen] = useState(false);
  // When the empty-state "Generate from prompt" row opens the panel, this
  // pre-fills the chat input. Cleared after the panel closes.
  const [architectInitialPrompt, setArchitectInitialPrompt] = useState<string | undefined>(undefined);
  const [paletteContext, setPaletteContext] = useState<
    Pick<AddStepDetail, 'position' | 'source' | 'target'> | undefined
  >(undefined);
  const [changeTypeFor, setChangeTypeFor] = useState<string | null>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);

  // Agent picker state — driven by `construct:open-agent-picker` events from
  // canvas badges (and our own auto-open after creating an agent step).
  const [agentPickerState, setAgentPickerState] = useState<{
    taskId: string;
    anchorRect: DOMRect | null;
  } | null>(null);

  // Prime the agent roster cache so the picker opens instantly on first click.
  // Roster is also read below to enrich `assign` writes with agentType/role.
  const { agents: poolAgents } = useAgentRoster();

  const taskIdCounter = useRef(0);
  const connectingFrom = useRef<{ nodeId: string; handleType: string; handleId: string | null } | null>(null);
  const connectionMade = useRef(false);
  const { screenToFlowPosition, fitView } = useReactFlow();
  const canvasRef = useRef<HTMLDivElement>(null);

  // Parse initial workflow definition.
  const { initialNodes, initialEdges } = useMemo(() => {
    if (!workflow?.definition) return { initialNodes: [] as Node[], initialEdges: [] as Edge[] };
    const tasks = parseWorkflowYaml(workflow.definition);
    const meta = parseWorkflowMeta(workflow.definition);
    setWorkflowMeta(meta);
    const { nodes: rawNodes, edges } = tasksToFlow(tasks);
    const laidOut = layoutNodes(rawNodes, edges);
    const savedKey = `wf-positions:${workflow.name}`;
    try {
      const saved = JSON.parse(localStorage.getItem(savedKey) || '{}') as Record<
        string,
        { x: number; y: number }
      >;
      if (Object.keys(saved).length > 0) {
        for (const n of laidOut) {
          const s = saved[n.id];
          if (s) n.position = { x: s.x, y: s.y };
        }
      }
    } catch {
      /* ignore */
    }
    taskIdCounter.current = tasks.length;
    return { initialNodes: laidOut, initialEdges: edges };
  }, [workflow]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  const selectedNode = useMemo(
    () => (selectedNodeId ? nodes.find((n) => n.id === selectedNodeId) ?? null : null),
    [selectedNodeId, nodes],
  );

  // ── DAG context for ${...} expression autocomplete in textareas ─────────
  // Step IDs come from the live xyflow nodes; workflow inputs from the
  // parsed workflowMeta; trigger fields are common defaults plus any keys
  // surfaced by the workflow's declared trigger inputMap.
  const dagContext = useMemo(() => {
    const stepIds = nodes
      .map((n) => (n.data as TaskNodeData).taskId)
      .filter((id): id is string => Boolean(id));
    const workflowInputs = workflowMeta.inputs.map((i) => i.name).filter(Boolean);
    const defaultTriggerFields = ['entity_kref', 'kind', 'tag', 'name', 'metadata'];
    const triggerInputKeys = new Set<string>();
    for (const t of workflowMeta.triggers) {
      for (const key of Object.keys(t.inputMap || {})) {
        if (key && !key.startsWith('__')) triggerInputKeys.add(key);
      }
    }
    const triggerFields = Array.from(
      new Set([...defaultTriggerFields, ...triggerInputKeys]),
    );
    return { stepIds, workflowInputs, triggerFields };
  }, [nodes, workflowMeta.inputs, workflowMeta.triggers]);

  // ── Position helpers ────────────────────────────────────────────────────
  const getViewportCenter = useCallback(() => {
    const el = canvasRef.current;
    if (!el) return { x: 200, y: 200 };
    const rect = el.getBoundingClientRect();
    return screenToFlowPosition({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
  }, [screenToFlowPosition]);

  // ── Persist node positions ──────────────────────────────────────────────
  const onNodeDragStop = useCallback(() => {
    const savedKey = `wf-positions:${name}`;
    const positions: Record<string, { x: number; y: number }> = {};
    for (const n of nodes) positions[n.id] = n.position;
    try {
      localStorage.setItem(savedKey, JSON.stringify(positions));
    } catch {
      /* ignore */
    }
  }, [nodes, name]);

  // ── Add a new step (called via construct:add-step event) ────────────────
  const insertStep = useCallback(
    (detail: AddStepDetail) => {
      const id = `step-${++taskIdCounter.current}`;
      const isGate = detail.type === 'conditional';
      const position =
        detail.position ??
        (() => {
          const c = getViewportCenter();
          return { x: c.x - 110, y: c.y - 40 };
        })();

      const data = defaultNodeData(id, defaultsForType(detail.type));
      if (detail.presetSkill && !data.skills.includes(detail.presetSkill)) {
        data.skills = [...data.skills, detail.presetSkill];
      }

      const newNode: Node<TaskNodeData> = {
        id,
        type: isGate ? 'gateNode' : 'taskNode',
        position,
        data,
      };

      // If a source was provided, also create an edge from source → new node.
      // If a target was provided (reverse drop), create an edge new node → target.
      let newEdge: Edge | null = null;
      if (detail.source?.taskId) {
        const handle = detail.source.handle ?? null;
        const isBranch = handle === 'true' || handle === 'false';
        const edgeStyle = isBranch ? GATE_EDGE_STYLES[handle] : GATE_EDGE_STYLES.default;
        const edgeColor = edgeStyle.stroke;
        newEdge = {
          id: `${detail.source.taskId}->${handle ? handle + '->' : ''}${id}`,
          source: detail.source.taskId,
          target: id,
          sourceHandle: handle,
          type: 'default',
          animated: true,
          selectable: true,
          interactionWidth: 20,
          style: edgeStyle,
          markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
          ...(isBranch
            ? { label: handle, labelStyle: { fill: edgeColor, fontSize: 10, fontWeight: 600 } }
            : {}),
        };
      } else if (detail.target?.taskId) {
        const edgeStyle = GATE_EDGE_STYLES.default;
        const edgeColor = edgeStyle.stroke;
        newEdge = {
          id: `${id}->${detail.target.taskId}`,
          source: id,
          target: detail.target.taskId,
          sourceHandle: null,
          type: 'default',
          animated: true,
          selectable: true,
          interactionWidth: 20,
          style: edgeStyle,
          markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
        };
      }

      setNodes((nds) => {
        if (newEdge && !newEdge.sourceHandle && newEdge.target === id) {
          // Forward drop: bump dependency count for the new node.
          newNode.data = { ...newNode.data, dependencyCount: 1 };
        }
        // Reverse drop: bump dependency count on the original target.
        if (newEdge && newEdge.source === id && newEdge.target !== id) {
          return nds
            .map((n) =>
              n.id === newEdge!.target
                ? {
                    ...n,
                    data: {
                      ...n.data,
                      dependencyCount: (n.data as TaskNodeData).dependencyCount + 1,
                    },
                  }
                : n,
            )
            .concat(newNode);
        }
        return [...nds, newNode];
      });
      if (newEdge) setEdges((eds) => [...eds, newEdge!]);
      setSelectedNodeId(id);

      // Auto-open agent picker for new agent steps. Wait one frame for xyflow
      // to mount the node, then try to anchor the picker to the new badge.
      // If the badge isn't in the DOM yet, the editor's listener falls back
      // to a centered popover (anchorRect: null).
      if (detail.type === 'agent') {
        requestAnimationFrame(() => {
          const nodeEl = document.querySelector(
            `.react-flow__node[data-id="${id}"] button[title^="No pool agent"], ` +
              `.react-flow__node[data-id="${id}"] button[title^="Assigned"]`,
          ) as HTMLElement | null;
          const rect = nodeEl?.getBoundingClientRect() ?? null;
          if (rect) {
            emitOpenAgentPicker({ taskId: id, anchorRect: rect });
          } else {
            // Fallback — surface a centered picker by setting state directly.
            setAgentPickerState({ taskId: id, anchorRect: null });
          }
        });
      }
    },
    [getViewportCenter, setNodes, setEdges],
  );

  // ── Subscribe to global add-step events ─────────────────────────────────
  useEffect(() => {
    const handler = (event: Event) => {
      const ce = event as CustomEvent<AddStepDetail>;
      if (!ce.detail) return;
      insertStep(ce.detail);
    };
    window.addEventListener(ADD_STEP_EVENT, handler as EventListener);
    return () => window.removeEventListener(ADD_STEP_EVENT, handler as EventListener);
  }, [insertStep]);

  // ── Subscribe to global open-agent-picker events ───────────────────────
  useEffect(() => {
    const handler = (event: Event) => {
      const ce = event as CustomEvent<OpenAgentPickerDetail>;
      if (!ce.detail) return;
      setAgentPickerState({ taskId: ce.detail.taskId, anchorRect: ce.detail.anchorRect });
    };
    window.addEventListener(OPEN_AGENT_PICKER_EVENT, handler as EventListener);
    return () => window.removeEventListener(OPEN_AGENT_PICKER_EVENT, handler as EventListener);
  }, []);

  // ── Real-time updates (P1.2) ────────────────────────────────────────────
  // `lastSyncedYaml` is the round-tripped YAML the editor was last hydrated
  // from — either the prop on mount or a remote revision applied via SSE.
  // Comparing the current graph's YAML against it tells us whether the user
  // has unsaved local edits (the `dirty` flag below).
  //
  // We normalize the baseline through a parse → serialize pass so formatting
  // differences (key ordering, whitespace) don't make the editor look dirty
  // immediately on mount.
  const initialYamlRef = useRef<string>('');
  const [lastSyncedYaml, setLastSyncedYaml] = useState<string>('');
  // Hydrate the baseline once per workflow load.
  useEffect(() => {
    if (!workflow?.definition) {
      initialYamlRef.current = '';
      setLastSyncedYaml('');
      return;
    }
    try {
      const parsed = parseWorkflowYaml(workflow.definition);
      const meta = parseWorkflowMeta(workflow.definition);
      const normalized = tasksToYaml(parsed, {
        ...meta,
        name: workflow.name,
        description: workflow.description,
      });
      initialYamlRef.current = normalized;
      setLastSyncedYaml(normalized);
    } catch {
      initialYamlRef.current = workflow.definition;
      setLastSyncedYaml(workflow.definition);
    }
  }, [workflow?.kref, workflow?.definition, workflow?.name, workflow?.description]);
  const [pendingRemoteUpdate, setPendingRemoteUpdate] =
    useState<WorkflowRevisionPublishedEvent | null>(null);
  const [remotePill, setRemotePill] = useState<{
    publishedAt: string;
    expiresAt: number;
  } | null>(null);

  // Auto-dismiss the pill after 4s.
  useEffect(() => {
    if (!remotePill) return undefined;
    const remaining = Math.max(0, remotePill.expiresAt - Date.now());
    const timer = setTimeout(() => setRemotePill(null), remaining);
    return () => clearTimeout(timer);
  }, [remotePill]);

  // Forward ref so the ⌘I keydown effect (registered before openYamlPanel
  // is declared) can dispatch to the latest callback.
  const openYamlPanelRef = useRef<() => void>(() => {});

  // ── ⌘K / ⌘I / ⌘J shortcuts ──────────────────────────────────────────────
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const inField =
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable);
      // ⌘J should toggle the Architect panel even from inside the editor's
      // own input fields — but never from inside the panel's own composer
      // (otherwise pressing ⌘J inside the textarea would close the panel
      // mid-typing). The panel renders into a portal-like fixed aside so
      // we filter by an ancestor data-attribute.
      const mod = isMac ? event.metaKey : event.ctrlKey;
      const isJ = mod && event.key.toLowerCase() === 'j';
      if (isJ) {
        event.preventDefault();
        setArchitectPanelOpen((prev) => !prev);
        return;
      }

      if (inField) return;

      if (mod && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteContext(undefined);
        setChangeTypeFor(null);
        setPaletteOpen(true);
      } else if (mod && event.key.toLowerCase() === 'i') {
        event.preventDefault();
        openYamlPanelRef.current();
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [isMac]);

  // ── React Flow handlers ─────────────────────────────────────────────────
  const onConnect = useCallback(
    (connection: Connection) => {
      connectionMade.current = true;
      if (!connection.source || !connection.target) return;
      const exists = edges.some(
        (e) =>
          e.source === connection.source &&
          e.target === connection.target &&
          e.sourceHandle === (connection.sourceHandle ?? null),
      );
      if (exists) return;
      const branch = connection.sourceHandle as 'true' | 'false' | null;
      const isBranch = branch === 'true' || branch === 'false';
      const edgeStyle = isBranch ? GATE_EDGE_STYLES[branch] : GATE_EDGE_STYLES.default;
      const edgeColor = edgeStyle.stroke;

      const newEdge: Edge = {
        id: `${connection.source}->${branch ? branch + '->' : ''}${connection.target}`,
        source: connection.source,
        sourceHandle: connection.sourceHandle,
        target: connection.target,
        type: 'default',
        animated: true,
        selectable: true,
        interactionWidth: 20,
        style: edgeStyle,
        markerEnd: { type: MarkerType.ArrowClosed, color: edgeColor },
        ...(isBranch
          ? { label: branch, labelStyle: { fill: edgeColor, fontSize: 10, fontWeight: 600 } }
          : {}),
      };
      setEdges((eds) => [...eds, newEdge]);

      // Auto-insert `${source.output}` into the target's primary text field so
      // the wired edge implies the matching interpolation. Skip when source ===
      // target (self-loop), when the edge is a conditional branch (true/false
      // handles route control flow, not data), when the target type has no
      // primary text field, or when the target already references this source
      // somewhere.
      const sourceId = connection.source;
      const targetId = connection.target;
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id !== targetId) return n;
          const data = n.data as TaskNodeData;
          let nextData: TaskNodeData = data;
          if (!isBranch) {
            nextData = { ...nextData, dependencyCount: nextData.dependencyCount + 1 };
          }
          if (sourceId !== targetId && !isBranch) {
            const primaryField = STEP_PRIMARY_TEXT_FIELD[nextData.type];
            if (primaryField && !targetAlreadyReferencesSource(nextData, sourceId)) {
              const current = (nextData[primaryField] as string | undefined) ?? '';
              const ref = `\${${sourceId}.output}`;
              const updated = current.length === 0 ? ref : `${current}\n\n${ref}`;
              nextData = { ...nextData, [primaryField]: updated };
            }
          }
          return nextData === data ? n : { ...n, data: nextData };
        }),
      );
    },
    [edges, setEdges, setNodes],
  );

  const onConnectStart = useCallback(
    (
      _: unknown,
      params: { nodeId: string | null; handleType: string | null; handleId: string | null },
    ) => {
      connectionMade.current = false;
      if (params.nodeId && params.handleType) {
        connectingFrom.current = {
          nodeId: params.nodeId,
          handleType: params.handleType,
          handleId: params.handleId || null,
        };
      }
    },
    [],
  );

  // Drop a noodle into empty space → open the palette with source context.
  const onConnectEnd = useCallback(
    (event: MouseEvent | TouchEvent) => {
      const from = connectingFrom.current;
      connectingFrom.current = null;
      if (!from) return;
      if (connectionMade.current) return;
      const target = (event as MouseEvent).target as HTMLElement;
      if (target?.closest('.react-flow__node') || target?.closest('.react-flow__handle')) return;

      const touch = 'changedTouches' in event ? (event as TouchEvent).changedTouches?.[0] : null;
      const clientX = touch ? touch.clientX : (event as MouseEvent).clientX;
      const clientY = touch ? touch.clientY : (event as MouseEvent).clientY;
      const position = screenToFlowPosition({ x: clientX, y: clientY });

      // Forward: source-handle drop → new node is wired AS A DOWNSTREAM
      // dependency of the dragged-from node (source → new).
      // Reverse: target-handle drop → new node is wired AS THE UPSTREAM
      // dependency of the dragged-from node (new → target).
      if (from.handleType === 'source') {
        setPaletteContext({
          position,
          source: { taskId: from.nodeId, handle: from.handleId as 'true' | 'false' | null },
        });
      } else if (from.handleType === 'target') {
        setPaletteContext({
          position,
          target: { taskId: from.nodeId },
        });
      } else {
        return;
      }
      setChangeTypeFor(null);
      setPaletteOpen(true);
    },
    [screenToFlowPosition],
  );

  const onEdgesDelete = useCallback(
    (deletedEdges: Edge[]) => {
      const targetCounts = new Map<string, number>();
      for (const e of deletedEdges) targetCounts.set(e.target, (targetCounts.get(e.target) || 0) + 1);
      setNodes((nds) =>
        nds.map((n) => {
          const dec = targetCounts.get(n.id);
          if (!dec) return n;
          return {
            ...n,
            data: {
              ...n.data,
              dependencyCount: Math.max(0, (n.data as TaskNodeData).dependencyCount - dec),
            },
          };
        }),
      );
    },
    [setNodes],
  );

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNodeId(node.id);
  }, []);

  const onPaneClick = useCallback(() => {
    setSelectedNodeId(null);
    setContextMenu(null);
  }, []);

  // Right-click on canvas pane.
  const onPaneContextMenu = useCallback(
    (event: React.MouseEvent | MouseEvent) => {
      event.preventDefault();
      const flowPos = screenToFlowPosition({
        x: (event as MouseEvent).clientX,
        y: (event as MouseEvent).clientY,
      });
      setContextMenu({
        screenX: (event as MouseEvent).clientX,
        screenY: (event as MouseEvent).clientY,
        flowX: flowPos.x,
        flowY: flowPos.y,
      });
    },
    [screenToFlowPosition],
  );

  // ── Side panel updates ──────────────────────────────────────────────────
  const handleNodeUpdate = useCallback(
    (nodeId: string, updates: Partial<TaskNodeData>) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId ? { ...n, data: { ...n.data, ...updates } } : n,
        ),
      );
    },
    [setNodes],
  );

  // Atomic step-id rename: keeps node.id, data.taskId, and edge endpoints in
  // lockstep so depends_on round-trips correctly.
  //
  // Known gap (P1.5a, intentional): does NOT rewrite `${old_id.output}`
  // references buried inside other steps' fields (prompts, conditions, etc.).
  // Users editing a Step ID see only the dependency wires move; if they had
  // typed `${test-agent.output}` into a downstream prompt, that string keeps
  // the old name. Surfacing those references in the panel is P1.5b/c work.
  const handleRenameStep = useCallback(
    (oldId: string, newId: string) => {
      if (oldId === newId) return;
      setNodes((nds) =>
        nds.map((n) =>
          n.id === oldId
            ? { ...n, id: newId, data: { ...n.data, taskId: newId, label: n.data.label === oldId ? newId : n.data.label } }
            : n,
        ),
      );
      setEdges((eds) =>
        eds.map((e) => {
          const next = { ...e };
          let touched = false;
          if (e.source === oldId) { next.source = newId; touched = true; }
          if (e.target === oldId) { next.target = newId; touched = true; }
          if (touched) {
            const handle = e.sourceHandle ? `${e.sourceHandle}->` : '';
            next.id = `${next.source}->${handle}${next.target}`;
          }
          return next;
        }),
      );
      if (selectedNodeId === oldId) setSelectedNodeId(newId);
    },
    [setNodes, setEdges, selectedNodeId],
  );

  const handleNodeDelete = useCallback(
    (nodeId: string) => {
      setNodes((nds) => nds.filter((n) => n.id !== nodeId));
      setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId));
      if (selectedNodeId === nodeId) setSelectedNodeId(null);
    },
    [setNodes, setEdges, selectedNodeId],
  );

  // Switch a node's type — keeps name/description/skills, drops type-specific
  // fields by re-creating data from defaults.
  const handleChangeType = useCallback(
    (nodeId: string, newType: string) => {
      setNodes((nds) =>
        nds.map((n) => {
          if (n.id !== nodeId) return n;
          const old = n.data as TaskNodeData;
          const fresh = defaultNodeData(old.taskId, {
            ...defaultsForType(newType),
            taskId: old.taskId,
            label: old.label,
            name: old.name,
            description: old.description,
            agentHints: old.agentHints,
            skills: old.skills,
            assign: old.assign,
            template: old.template,
            paramCount: old.paramCount,
            dependencyCount: old.dependencyCount,
            retry: old.retry,
            retryDelay: old.retryDelay,
            channels: old.channels,
            channel: old.channel,
          });
          return {
            ...n,
            type: newType === 'conditional' ? 'gateNode' : 'taskNode',
            data: fresh,
          };
        }),
      );
    },
    [setNodes],
  );

  // ── Toolbar actions ─────────────────────────────────────────────────────
  const openPalette = useCallback((position?: { x: number; y: number }) => {
    setPaletteContext(position ? { position } : undefined);
    setChangeTypeFor(null);
    setPaletteOpen(true);
  }, []);

  const handleLayout = useCallback(() => {
    setNodes((nds) => layoutNodes([...nds], edges));
    requestAnimationFrame(() => {
      try {
        fitView({ padding: 0.2, duration: 240 });
      } catch {
        /* ignore */
      }
    });
  }, [edges, setNodes, fitView]);

  const handleFitView = useCallback(() => {
    try {
      fitView({ padding: 0.2, duration: 240 });
    } catch {
      /* ignore */
    }
  }, [fitView]);

  const handleYamlImport = useCallback(() => {
    try {
      const tasks = parseWorkflowYaml(yamlText);
      if (tasks.length === 0) {
        setError('No tasks found in YAML. Ensure the YAML has a "steps:" section.');
        return;
      }
      const meta = parseWorkflowMeta(yamlText);
      setWorkflowMeta(meta);
      const { nodes: rawNodes, edges: newEdges } = tasksToFlow(tasks);
      const laidOut = layoutNodes(rawNodes, newEdges);
      setNodes(laidOut);
      setEdges(newEdges);
      taskIdCounter.current = tasks.length;
      setShowAdvanced(false);
      setError(null);
    } catch {
      setError('Failed to parse YAML. Check syntax.');
    }
  }, [yamlText, setNodes, setEdges]);

  // Open YAML drawer (used by EditorCommandList row + ⌘I shortcut).
  const openYamlPanel = useCallback(() => {
    const tasks = flowToTasks(nodes as Node<TaskNodeData>[], edges);
    setYamlText(tasksToYaml(tasks, { ...workflowMeta, name, description }));
    setShowAdvanced(true);
  }, [nodes, edges, workflowMeta, name, description]);
  // Keep the ⌘I keydown effect's ref pointed at the latest closure.
  openYamlPanelRef.current = openYamlPanel;

  // ── Tag input ───────────────────────────────────────────────────────────
  const handleTagKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      const value = tagInput.trim().toLowerCase();
      if (value && !tags.includes(value)) setTags((t) => [...t, value]);
      setTagInput('');
    }
    if (e.key === 'Backspace' && tagInput === '' && tags.length > 0) {
      setTags((t) => t.slice(0, -1));
    }
  };

  // ── Compute current YAML for dirty detection ────────────────────────────
  // Cheap to recompute (the editor already does this on Save / YAML toggle);
  // re-running it as a memo only when nodes/edges/meta change avoids stale-
  // dirty bugs.
  const currentYaml = useMemo(() => {
    const tasks = flowToTasks(nodes as Node<TaskNodeData>[], edges);
    return tasksToYaml(tasks, { ...workflowMeta, name, description });
  }, [nodes, edges, workflowMeta, name, description]);

  const dirty = useMemo(() => {
    // No baseline yet (create mode with no edits) — never dirty.
    if (!lastSyncedYaml && !initialYamlRef.current) return false;
    return currentYaml !== (lastSyncedYaml || initialYamlRef.current);
  }, [currentYaml, lastSyncedYaml]);

  // ── Apply a remote revision to the canvas ────────────────────────────────
  // Fetches the new YAML, replaces the in-memory graph, briefly highlights
  // changed step IDs, and surfaces the toolbar pill. Used by the SSE handler
  // (auto-apply path) and the conflict banner's "Apply" button.
  const applyRemoteRevision = useCallback(
    async (event: WorkflowRevisionPublishedEvent) => {
      try {
        const remote = await fetchWorkflowByRevisionKref(event.revision_kref);
        const newDefinition = remote.definition ?? '';
        const newTasks = parseWorkflowYaml(newDefinition);
        const newMeta = parseWorkflowMeta(newDefinition);
        const { nodes: rawNodes, edges: newEdges } = tasksToFlow(newTasks);
        const laidOut = layoutNodes(rawNodes, newEdges);

        // Compute changed step IDs by comparing serialized step blobs.
        const oldTasks = flowToTasks(nodes as Node<TaskNodeData>[], edges);
        const oldById = new Map(oldTasks.map((t) => [t.id, JSON.stringify(t)]));
        const changedIds = new Set<string>();
        for (const t of newTasks) {
          const prev = oldById.get(t.id);
          if (prev === undefined || prev !== JSON.stringify(t)) {
            changedIds.add(t.id);
          }
        }

        // Mark changed nodes; clear after 1.2s.
        const markedNodes = laidOut.map((n) =>
          changedIds.has(n.id)
            ? { ...n, data: { ...(n.data as TaskNodeData), justUpdated: true } }
            : n,
        );

        setNodes(markedNodes);
        setEdges(newEdges);
        setWorkflowMeta(newMeta);
        setName(remote.name ?? event.name);
        setDescription(remote.description ?? '');
        // Normalize the baseline through the same pipeline the dirty check
        // uses (parse → tasksToYaml) so a clean apply doesn't immediately
        // register as "dirty" because of formatting differences.
        const normalized = tasksToYaml(newTasks, {
          ...newMeta,
          name: remote.name ?? event.name,
          description: remote.description ?? '',
        });
        setLastSyncedYaml(normalized);
        initialYamlRef.current = normalized;
        taskIdCounter.current = newTasks.length;
        setPendingRemoteUpdate(null);
        setRemotePill({
          publishedAt: event.published_at,
          expiresAt: Date.now() + 4000,
        });

        if (changedIds.size > 0) {
          setTimeout(() => {
            setNodes((nds) =>
              nds.map((n) => {
                const data = n.data as TaskNodeData & { justUpdated?: boolean };
                if (!data.justUpdated) return n;
                const { justUpdated: _drop, ...rest } = data;
                void _drop;
                return { ...n, data: rest as TaskNodeData };
              }),
            );
          }, 1200);
        }
      } catch (err) {
        // Don't blow up the editor — surface a soft warning. The user can
        // refresh manually if the auto-apply fails.
        setWarning(
          `Couldn't apply remote update: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [nodes, edges, setNodes, setEdges],
  );

  // Stable refs so the SSE callback doesn't re-subscribe on every state change.
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const applyRef = useRef(applyRemoteRevision);
  applyRef.current = applyRemoteRevision;

  useWorkflowEvents({
    workflowKref: workflow?.kref ?? null,
    onRevisionPublished: useCallback((event) => {
      if (dirtyRef.current) {
        // Queue behind a conflict banner — user picks Apply / Keep mine.
        setPendingRemoteUpdate(event);
      } else {
        void applyRef.current(event);
      }
    }, []),
  });

  // ── Architect → editor pipe ─────────────────────────────────────────────
  // When Architect proposes a YAML via `propose_workflow_yaml`, swap the
  // canvas to the proposal. The user explicitly invoked Architect with the
  // merge instruction in the system preface, so overwriting the in-memory
  // graph is the expected behavior. Save remains user-driven — the toolbar
  // Save button is still the only path that creates a Kumiho revision.
  const handleYamlProposed = useCallback(
    (newYaml: string, summary: string) => {
      try {
        const newTasks = parseWorkflowYaml(newYaml);
        const newMeta = parseWorkflowMeta(newYaml);
        const { nodes: rawNodes, edges: newEdges } = tasksToFlow(newTasks);
        const laidOut = layoutNodes(rawNodes, newEdges);
        setNodes(laidOut);
        setEdges(newEdges);
        setWorkflowMeta(newMeta);
        if (newMeta.name) setName(newMeta.name);
        if (newMeta.description) setDescription(newMeta.description);
        taskIdCounter.current = newTasks.length;
        setError(null);
        // Reuse the remote-update pill UX — same affordance, different
        // origin. Auto-dismisses after 4s via the existing effect.
        setRemotePill({
          publishedAt: summary || new Date().toISOString(),
          expiresAt: Date.now() + 4000,
        });
      } catch (err) {
        setWarning(
          `Couldn't apply Architect proposal: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    },
    [setNodes, setEdges],
  );

  // ── Save ────────────────────────────────────────────────────────────────
  // Awaits the parent's onSave and surfaces any rejection as an inline error.
  // Without this, server-side validation failures (e.g. shell step missing
  // command) only set page-level state hidden behind this fixed-overlay editor
  // — clicks would appear to do nothing.
  const handleSave = useCallback(async () => {
    setError(null);
    setWarning(null);
    if (!name.trim()) return setError('Workflow name is required.');
    if (!description.trim()) return setError('Workflow description is required.');
    if (nodes.length === 0) return setError('Add at least one step to the workflow.');
    if (hasCycle(nodes, edges)) return setError('Cannot save: workflow has cycles.');

    const tasks = flowToTasks(nodes as Node<TaskNodeData>[], edges);
    const definition = tasksToYaml(tasks, {
      ...workflowMeta,
      name: name.trim(),
      description: description.trim(),
    });
    try {
      await onSave({
        name: name.trim(),
        description: description.trim(),
        definition,
        version: workflowMeta.version || '',
        tags,
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Save failed.';
      setError(message);
    }
  }, [name, description, tags, nodes, edges, workflowMeta, onSave]);

  // ── Sync YAML when toggling drawer ──────────────────────────────────────
  useEffect(() => {
    if (showAdvanced) {
      const tasks = flowToTasks(nodes as Node<TaskNodeData>[], edges);
      setYamlText(tasksToYaml(tasks, { ...workflowMeta, name, description }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showAdvanced]);

  // ── Render ──────────────────────────────────────────────────────────────
  const isEmpty = nodes.length === 0;
  const cycleDetected = hasCycle(nodes, edges);

  return (
    <div
      className={`editor-chrome ${containerClassName ?? 'flex h-[calc(100vh-3.5rem)] flex-col'}`}
      style={{ background: 'var(--construct-bg-base, var(--pc-bg-base))' }}
    >
      {/* Top bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          padding: '10px 20px',
          borderBottom: '1px solid var(--construct-border-soft)',
          background: 'var(--construct-bg-surface)',
        }}
      >
        <button
          type="button"
          onClick={onCancel}
          title="Back"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: 32,
            height: 32,
            borderRadius: 8,
            border: '1px solid var(--construct-border-soft)',
            background: 'transparent',
            color: 'var(--construct-text-secondary)',
            cursor: 'pointer',
          }}
        >
          <ArrowLeft size={16} />
        </button>

        <div style={{ display: 'flex', flex: 1, minWidth: 0, gap: 12, alignItems: 'center' }}>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Workflow name"
            disabled={resolvedMode === 'edit'}
            style={{
              background: 'transparent',
              border: 0,
              outline: 'none',
              fontSize: 16,
              fontWeight: 700,
              color: 'var(--construct-text-primary)',
              minWidth: 180,
              maxWidth: 320,
            }}
          />
          <input
            type="text"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Description"
            style={{
              background: 'transparent',
              border: 0,
              outline: 'none',
              fontSize: 13,
              color: 'var(--construct-text-secondary)',
              flex: 1,
              minWidth: 0,
            }}
          />
          {remotePill ? (
            <span
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '3px 8px',
                borderRadius: 999,
                border: '1px solid var(--construct-border-soft)',
                background: 'var(--construct-signal-network-soft)',
                color: 'var(--construct-signal-network)',
                fontSize: 11,
                fontWeight: 600,
                whiteSpace: 'nowrap',
              }}
              title={`Updated at ${remotePill.publishedAt}`}
            >
              <span
                aria-hidden
                style={{
                  width: 6,
                  height: 6,
                  borderRadius: '50%',
                  background: 'var(--construct-signal-network)',
                }}
              />
              <Radio size={11} />
              Operator edited · {formatRelative(remotePill.publishedAt)}
            </span>
          ) : null}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {workflow && (
            <span
              style={{
                fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
                fontSize: 11,
                padding: '2px 8px',
                borderRadius: 6,
                color: 'var(--construct-text-faint)',
                background: 'var(--pc-bg-input)',
              }}
            >
              rev {workflow.revision_number}
            </span>
          )}

          {/* Tag input */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              padding: '4px 8px',
              borderRadius: 8,
              border: '1px solid var(--pc-border)',
              background: 'var(--pc-bg-input)',
              minWidth: 120,
              maxWidth: 240,
              flexWrap: 'wrap',
            }}
          >
            {tags.map((tag) => (
              <span
                key={tag}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 2,
                  padding: '2px 6px',
                  borderRadius: 4,
                  fontSize: 10,
                  fontWeight: 500,
                  background: 'var(--pc-accent-glow)',
                  color: 'var(--pc-accent-light)',
                }}
              >
                {tag}
                <button
                  type="button"
                  onClick={() => setTags((t) => t.filter((x) => x !== tag))}
                  style={{ background: 'transparent', border: 0, color: 'inherit', cursor: 'pointer', padding: 0 }}
                >
                  <X size={9} />
                </button>
              </span>
            ))}
            <input
              type="text"
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={handleTagKeyDown}
              placeholder={tags.length === 0 ? 'Tags…' : ''}
              style={{
                flex: 1,
                minWidth: 40,
                background: 'transparent',
                border: 0,
                outline: 'none',
                fontSize: 11,
                color: 'var(--pc-text-primary)',
              }}
            />
          </div>

          <button type="button" onClick={onCancel} className="construct-button" style={{ padding: '6px 12px', fontSize: 12 }}>
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="construct-button"
            data-variant="primary"
            style={{ padding: '6px 14px', fontSize: 12, fontWeight: 600 }}
          >
            {saving ? 'Saving…' : resolvedMode === 'edit' ? 'Update' : resolvedMode === 'duplicate' ? 'Create Copy' : 'Save'}
          </button>
        </div>
      </div>

      {/* Error / Warning */}
      {error && (
        <div
          style={{
            margin: '8px 20px 0',
            padding: '8px 12px',
            borderRadius: 10,
            border: '1px solid color-mix(in srgb, var(--construct-status-danger) 32%, transparent)',
            background: 'color-mix(in srgb, var(--construct-status-danger) 10%, transparent)',
            color: 'var(--construct-status-danger)',
            fontSize: 13,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <AlertTriangle size={14} />
            {error}
          </span>
          <button
            onClick={() => setError(null)}
            style={{ background: 'transparent', border: 0, color: 'inherit', cursor: 'pointer' }}
          >
            <X size={14} />
          </button>
        </div>
      )}
      {warning && (
        <div
          style={{
            margin: '8px 20px 0',
            padding: '8px 12px',
            borderRadius: 10,
            border: '1px solid color-mix(in srgb, var(--construct-status-warning) 32%, transparent)',
            background: 'color-mix(in srgb, var(--construct-status-warning) 10%, transparent)',
            color: 'var(--construct-status-warning)',
            fontSize: 13,
          }}
        >
          <AlertTriangle size={14} style={{ display: 'inline', marginRight: 6 }} />
          {warning}
        </div>
      )}

      {pendingRemoteUpdate ? (
        <div
          className="construct-panel"
          data-variant="utility"
          style={{
            margin: '8px 20px 0',
            padding: '10px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          <Radio size={14} style={{ color: 'var(--construct-signal-network)' }} />
          <span
            style={{
              flex: 1,
              fontSize: 13,
              color: 'var(--construct-text-primary)',
              minWidth: 0,
            }}
          >
            Operator updated this workflow
            {pendingRemoteUpdate.published_at
              ? ` ${formatRelative(pendingRemoteUpdate.published_at)}`
              : ''}
            {' — your edits aren\'t saved yet.'}
          </span>
          <button
            type="button"
            className="construct-button"
            data-variant="primary"
            onClick={() => {
              if (pendingRemoteUpdate) void applyRemoteRevision(pendingRemoteUpdate);
            }}
            style={{ padding: '4px 12px', fontSize: 12, fontWeight: 600 }}
          >
            Apply
          </button>
          <button
            type="button"
            className="construct-button"
            onClick={() => setPendingRemoteUpdate(null)}
            style={{ padding: '4px 12px', fontSize: 12 }}
          >
            Keep mine
          </button>
        </div>
      ) : null}

      {/* Body grid */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) 22rem',
          gap: 12,
          padding: 12,
          flex: 1,
          minHeight: 0,
        }}
      >
        {/* Canvas panel */}
        <Panel className="overflow-hidden">
          <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
            {/* Toolbar */}
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '10px 12px',
                borderBottom: '1px solid var(--construct-border-soft)',
              }}
            >
              <div className="construct-kicker">Graph</div>
              <span style={{ fontSize: 11, color: 'var(--construct-text-faint)', marginLeft: 6 }}>
                {nodes.length} step{nodes.length === 1 ? '' : 's'} · {edges.length} edge
                {edges.length === 1 ? '' : 's'}
                {cycleDetected ? (
                  <>
                    {' · '}
                    <span style={{ color: 'var(--construct-status-danger)' }}>cycle detected</span>
                  </>
                ) : null}
              </span>
              <div style={{ flex: 1 }} />
              <button
                type="button"
                onClick={() => openPalette()}
                className="construct-button"
                data-variant="primary"
                title={`Add Step (${isMac ? '⌘' : 'Ctrl'}+K)`}
                style={{ padding: '6px 12px', fontSize: 12, fontWeight: 600 }}
              >
                <Plus size={14} />
                Add Step
              </button>
              <button
                type="button"
                onClick={handleLayout}
                className="construct-button"
                title="Auto-layout"
                style={{ padding: '6px 10px' }}
              >
                <LayoutGrid size={14} />
              </button>
              <button
                type="button"
                onClick={handleFitView}
                className="construct-button"
                title="Fit to view"
                style={{ padding: '6px 10px' }}
              >
                <Crosshair size={14} />
              </button>
              <button
                type="button"
                onClick={() => setShowAdvanced((s) => !s)}
                className="construct-button"
                data-variant={showAdvanced ? 'primary' : undefined}
                title="Toggle YAML"
                style={{ padding: '6px 10px' }}
              >
                <Code size={14} />
              </button>
              <button
                type="button"
                onClick={() => setArchitectPanelOpen((prev) => !prev)}
                className="construct-button"
                data-variant={architectPanelOpen ? 'primary' : undefined}
                title={
                  workflow?.kref
                    ? `Architect (${isMac ? '⌘' : 'Ctrl'}+J)`
                    : 'Architect — save the workflow first to start revising'
                }
                style={{ padding: '6px 10px' }}
              >
                <Wand2 size={14} />
              </button>
            </div>

            {/* Revision history strip — only meaningful for saved workflows
                (architect/revisions endpoint requires a kref). Same gate as
                the Architect button. */}
            {workflow?.kref ? (
              <RevisionHistoryStrip workflowKref={workflow.kref} />
            ) : null}

            {/* Canvas + side YAML */}
            <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
              {showAdvanced && (
                <div
                  style={{
                    width: 320,
                    flexShrink: 0,
                    borderRight: '1px solid var(--construct-border-soft)',
                    display: 'flex',
                    flexDirection: 'column',
                    background: 'var(--construct-bg-surface)',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      padding: '8px 12px',
                      borderBottom: '1px solid var(--construct-border-soft)',
                    }}
                  >
                    <span className="construct-kicker">YAML</span>
                    <button
                      type="button"
                      onClick={handleYamlImport}
                      className="construct-button"
                      data-variant="primary"
                      style={{ padding: '4px 10px', fontSize: 11 }}
                    >
                      <Zap size={12} />
                      Import
                    </button>
                  </div>
                  <textarea
                    value={yamlText}
                    onChange={(e) => setYamlText(e.target.value)}
                    spellCheck={false}
                    style={{
                      flex: 1,
                      padding: 10,
                      background: 'transparent',
                      color: 'var(--pc-text-primary)',
                      border: 0,
                      outline: 'none',
                      resize: 'none',
                      fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
                      fontSize: 11.5,
                    }}
                  />
                </div>
              )}

              <div
                ref={canvasRef}
                style={{ flex: 1, position: 'relative', background: 'var(--construct-bg-surface)' }}
                onContextMenu={(e) => {
                  // Only handle when right-clicking on the empty pane.
                  const target = e.target as HTMLElement;
                  if (target.closest('.react-flow__node') || target.closest('.react-flow__handle')) return;
                  onPaneContextMenu(e);
                }}
              >
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onConnect={onConnect}
                  onConnectStart={onConnectStart}
                  onConnectEnd={onConnectEnd}
                  onEdgesDelete={onEdgesDelete}
                  onNodeClick={onNodeClick}
                  onNodeDragStop={onNodeDragStop}
                  onPaneClick={onPaneClick}
                  nodeTypes={allNodeTypes}
                  fitView
                  elementsSelectable
                  edgesFocusable
                  deleteKeyCode={['Backspace', 'Delete']}
                  style={{ background: 'transparent' }}
                  defaultEdgeOptions={{
                    type: 'default',
                    animated: true,
                    selectable: true,
                    style: GATE_EDGE_STYLES.default,
                    markerEnd: { type: MarkerType.ArrowClosed, color: GATE_EDGE_STYLES.default.stroke },
                    interactionWidth: 20,
                  }}
                >
                  <Background color="var(--construct-grid-line, var(--pc-border))" gap={20} size={1} />
                  <Controls
                    showInteractive={false}
                    style={{
                      background: 'var(--construct-bg-panel-strong)',
                      borderColor: 'var(--construct-border-soft)',
                      borderRadius: 12,
                      overflow: 'hidden',
                    }}
                  />
                  {nodes.length > 0 && nodes.length <= 40 && (
                    <MiniMap
                      position="bottom-right"
                      pannable
                      zoomable
                      style={{
                        background: 'var(--construct-bg-panel-strong)',
                        border: '1px solid var(--construct-border-soft)',
                        borderRadius: 12,
                        width: 200,
                        height: 140,
                      }}
                      maskColor="rgba(0,0,0,0.32)"
                      nodeColor={() => 'var(--pc-accent)'}
                    />
                  )}
                </ReactFlow>

                {/* Empty state */}
                {isEmpty && (
                  <EditorCommandList
                    onAddStep={() => openPalette()}
                    onImportYaml={openYamlPanel}
                    onGenerate={() => {
                      setArchitectInitialPrompt('Describe the workflow you want to build…');
                      setArchitectPanelOpen(true);
                    }}
                  />
                )}

                {/* Right-click context menu */}
                {contextMenu && (
                  <ContextMenu
                    state={contextMenu}
                    canPaste={false}
                    onClose={() => setContextMenu(null)}
                    onAddStep={() => {
                      const ctx = contextMenu;
                      setContextMenu(null);
                      openPalette({ x: ctx.flowX, y: ctx.flowY });
                    }}
                    onAutoLayout={() => {
                      setContextMenu(null);
                      handleLayout();
                    }}
                    onFitToView={() => {
                      setContextMenu(null);
                      handleFitView();
                    }}
                    isMac={isMac}
                  />
                )}
              </div>
            </div>
          </div>
        </Panel>

        {/* Side panel */}
        {selectedNode ? (
          <StepConfigPanel
            node={selectedNode as Node<TaskNodeData>}
            existingTaskIds={nodes.map((n) => (n.data as TaskNodeData).taskId)}
            onUpdate={handleNodeUpdate}
            onRenameStep={handleRenameStep}
            onDelete={handleNodeDelete}
            onChangeType={() => {
              setChangeTypeFor(selectedNode.id);
              setPaletteContext(undefined);
              setPaletteOpen(true);
            }}
            dagContext={dagContext}
          />
        ) : (
          <WorkflowSettingsPanel meta={workflowMeta} setMeta={setWorkflowMeta} />
        )}
      </div>

      {/* Palette */}
      <StepTypePalette
        open={paletteOpen}
        onOpenChange={(o) => {
          setPaletteOpen(o);
          if (!o) setChangeTypeFor(null);
        }}
        context={paletteContext}
        onSelect={
          changeTypeFor
            ? (type) => {
                handleChangeType(changeTypeFor, type);
              }
            : undefined
        }
      />

      {/* Architect — editor-scoped chat panel. Always mounted; works on
          a fresh canvas (no kref required) because Architect now produces
          YAML in memory instead of persisting Kumiho revisions. */}
      <ArchitectPanel
        open={architectPanelOpen}
        onOpenChange={(o) => {
          setArchitectPanelOpen(o);
          if (!o) setArchitectInitialPrompt(undefined);
        }}
        workflowKref={workflow?.kref ?? null}
        workflowName={workflow?.name ?? name ?? null}
        currentYaml={currentYaml}
        onYamlProposed={handleYamlProposed}
        initialPrompt={architectInitialPrompt}
      />

      {/* Shared agent picker — single mount for the entire editor.
          Opened by canvas badge clicks, auto-open after creating a new
          agent step, AND the side panel "Choose agent…" button (which
          dispatches OPEN_AGENT_PICKER_EVENT). Single source of truth so
          two pickers can never be open simultaneously. */}
      <AgentPicker
        open={agentPickerState !== null}
        onOpenChange={(o) => {
          if (!o) setAgentPickerState(null);
        }}
        value={
          agentPickerState
            ? (nodes.find((n) => n.id === agentPickerState.taskId)?.data as TaskNodeData | undefined)?.assign
            : undefined
        }
        anchorRect={agentPickerState?.anchorRect ?? null}
        onSelect={(name) => {
          if (!agentPickerState) return;
          if (name === null) {
            handleNodeUpdate(agentPickerState.taskId, { assign: '' });
            return;
          }
          // Enrich with agentType + role from the picked roster entry,
          // matching the behaviour the side-panel mount used to have.
          const picked = poolAgents.find((a) => a.item_name === name);
          const current = nodes.find((n) => n.id === agentPickerState.taskId)?.data as
            | TaskNodeData
            | undefined;
          handleNodeUpdate(agentPickerState.taskId, {
            assign: name,
            agentType: picked?.agent_type || current?.agentType || 'claude',
            role: picked?.role || current?.role || 'coder',
          });
        }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Right-click context menu
// ---------------------------------------------------------------------------

function ContextMenu({
  state,
  canPaste,
  onClose,
  onAddStep,
  onAutoLayout,
  onFitToView,
  isMac,
}: {
  state: ContextMenuState;
  canPaste: boolean;
  onClose: () => void;
  onAddStep: () => void;
  onAutoLayout: () => void;
  onFitToView: () => void;
  isMac: boolean;
}) {
  // Click outside closes the menu.
  useEffect(() => {
    const handler = () => onClose();
    window.addEventListener('mousedown', handler);
    return () => window.removeEventListener('mousedown', handler);
  }, [onClose]);

  return (
    <div
      onMouseDown={(e) => e.stopPropagation()}
      style={{
        position: 'fixed',
        left: state.screenX,
        top: state.screenY,
        zIndex: 50,
        minWidth: 200,
        padding: 4,
        borderRadius: 10,
        border: '1px solid var(--construct-border-strong)',
        background: 'var(--construct-bg-panel-strong)',
        boxShadow: '0 12px 32px rgba(0,0,0,0.32)',
      }}
    >
      <ContextMenuItem onClick={onAddStep} label="Add Step" shortcut={`${isMac ? '⌘' : 'Ctrl'} K`} />
      <ContextMenuItem onClick={() => {}} label="Paste" disabled={!canPaste} />
      <ContextMenuSeparator />
      <ContextMenuItem onClick={onAutoLayout} label="Auto-layout" />
      <ContextMenuItem onClick={onFitToView} label="Fit to View" />
    </div>
  );
}

function ContextMenuItem({
  onClick,
  label,
  shortcut,
  disabled,
}: {
  onClick: () => void;
  label: string;
  shortcut?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '6px 10px',
        borderRadius: 8,
        border: 0,
        background: 'transparent',
        color: disabled ? 'var(--construct-text-faint)' : 'var(--construct-text-primary)',
        fontSize: 12,
        textAlign: 'left',
        cursor: disabled ? 'not-allowed' : 'pointer',
      }}
      onMouseEnter={(e) => {
        if (!disabled) e.currentTarget.style.background = 'var(--pc-hover)';
      }}
      onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
    >
      <span style={{ flex: 1 }}>{label}</span>
      {shortcut ? (
        <span
          style={{
            fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)',
            fontSize: 10,
            color: 'var(--construct-text-faint)',
          }}
        >
          {shortcut}
        </span>
      ) : null}
    </button>
  );
}

function ContextMenuSeparator() {
  return <div style={{ height: 1, margin: '4px 6px', background: 'var(--construct-border-soft)' }} />;
}

// ---------------------------------------------------------------------------
// Workflow settings panel (when no node is selected)
// ---------------------------------------------------------------------------

function WorkflowSettingsPanel({
  meta,
  setMeta,
}: {
  meta: WorkflowMeta;
  setMeta: React.Dispatch<React.SetStateAction<WorkflowMeta>>;
}) {
  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: 10,
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.08em',
    color: 'var(--pc-text-faint)',
    marginBottom: 4,
  };
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

  return (
    <Panel variant="primary" className="overflow-hidden">
      <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--construct-border-soft)' }}>
          <div className="construct-kicker">Workflow Settings</div>
        </div>
        <div style={{ overflowY: 'auto', padding: 16, display: 'flex', flexDirection: 'column', gap: 14 }}>
          <div>
            <label style={labelStyle}>Version</label>
            <input
              type="text"
              value={meta.version}
              onChange={(e) => setMeta((m) => ({ ...m, version: e.target.value }))}
              style={inputStyle}
            />
          </div>

          <div style={{ display: 'flex', gap: 8 }}>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Step Timeout (s)</label>
              <input
                type="number"
                value={meta.defaultTimeout}
                onChange={(e) => setMeta((m) => ({ ...m, defaultTimeout: parseInt(e.target.value) || 300 }))}
                style={inputStyle}
              />
            </div>
            <div style={{ flex: 1 }}>
              <label style={labelStyle}>Max Total (s)</label>
              <input
                type="number"
                value={meta.maxTotalTime}
                onChange={(e) => setMeta((m) => ({ ...m, maxTotalTime: parseInt(e.target.value) || 3600 }))}
                style={inputStyle}
              />
            </div>
          </div>

          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={meta.checkpoint}
              onChange={(e) => setMeta((m) => ({ ...m, checkpoint: e.target.checked }))}
              style={{ accentColor: 'var(--pc-accent)' }}
            />
            <span style={{ fontSize: 12, color: 'var(--pc-text-muted)' }}>Enable checkpoints</span>
          </label>

          {/* Triggers */}
          <SectionGroup
            kicker="Triggers"
            count={meta.triggers.length}
            addSlot={
              <div style={{ display: 'flex', gap: 4 }}>
                <button
                  type="button"
                  onClick={() =>
                    setMeta((m) => ({
                      ...m,
                      triggers: [
                        ...m.triggers,
                        { onKind: '', onTag: '', onNamePattern: '', inputMap: { __cron: '' } },
                      ],
                    }))
                  }
                  className="construct-button"
                  style={{ padding: '2px 8px', fontSize: 10 }}
                  title="Run on a cron schedule"
                >
                  + Cron
                </button>
                <button
                  type="button"
                  onClick={() =>
                    setMeta((m) => ({
                      ...m,
                      triggers: [
                        ...m.triggers,
                        { onKind: '', onTag: 'ready', onNamePattern: '', inputMap: {} },
                      ],
                    }))
                  }
                  className="construct-button"
                  style={{ padding: '2px 8px', fontSize: 10 }}
                  title="Run when an entity matches"
                >
                  + Entity
                </button>
              </div>
            }
          >
            {meta.triggers.map((trigger, ti) => (
              <div
                key={ti}
                style={{
                  padding: 8,
                  borderRadius: 8,
                  border: '1px solid var(--pc-border)',
                  background: 'var(--pc-bg-base)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: 10, color: 'var(--pc-text-faint)' }}>
                    {trigger.inputMap.__cron ? 'Cron' : 'Entity'}
                  </span>
                  <button
                    onClick={() =>
                      setMeta((m) => ({ ...m, triggers: m.triggers.filter((_, i) => i !== ti) }))
                    }
                    style={{
                      background: 'transparent',
                      border: 0,
                      color: 'var(--construct-status-danger)',
                      fontSize: 10,
                      cursor: 'pointer',
                    }}
                  >
                    Remove
                  </button>
                </div>
                {trigger.inputMap.__cron ? (
                  <input
                    type="text"
                    value={trigger.inputMap.__cron}
                    onChange={(e) =>
                      setMeta((m) => {
                        const triggers = [...m.triggers];
                        triggers[ti] = {
                          ...triggers[ti]!,
                          inputMap: { ...triggers[ti]!.inputMap, __cron: e.target.value },
                        };
                        return { ...m, triggers };
                      })
                    }
                    placeholder="0 9 * * 1 (cron)"
                    style={{ ...inputStyle, fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)' }}
                  />
                ) : (
                  <div style={{ display: 'flex', gap: 6 }}>
                    <input
                      type="text"
                      value={trigger.onKind}
                      onChange={(e) =>
                        setMeta((m) => {
                          const triggers = [...m.triggers];
                          triggers[ti] = { ...triggers[ti]!, onKind: e.target.value };
                          return { ...m, triggers };
                        })
                      }
                      placeholder="Entity kind"
                      style={{ ...inputStyle, flex: 1, fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)' }}
                    />
                    <input
                      type="text"
                      value={trigger.onTag}
                      onChange={(e) =>
                        setMeta((m) => {
                          const triggers = [...m.triggers];
                          triggers[ti] = { ...triggers[ti]!, onTag: e.target.value };
                          return { ...m, triggers };
                        })
                      }
                      placeholder="Tag"
                      style={{ ...inputStyle, width: 80, fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)' }}
                    />
                  </div>
                )}
              </div>
            ))}
          </SectionGroup>

          {/* Inputs */}
          <SectionGroup
            kicker="Inputs"
            count={meta.inputs.length}
            onAdd={() =>
              setMeta((m) => ({
                ...m,
                inputs: [
                  ...m.inputs,
                  { name: '', type: 'string', required: true, default: '', description: '' },
                ],
              }))
            }
          >
            {meta.inputs.map((input, ii) => (
              <div
                key={ii}
                style={{
                  padding: 8,
                  borderRadius: 8,
                  border: '1px solid var(--pc-border)',
                  background: 'var(--pc-bg-base)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <input
                    type="text"
                    value={input.name}
                    onChange={(e) =>
                      setMeta((m) => {
                        const inputs = [...m.inputs];
                        inputs[ii] = { ...inputs[ii]!, name: e.target.value };
                        return { ...m, inputs };
                      })
                    }
                    placeholder="Param name"
                    style={{ ...inputStyle, flex: 1, fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)' }}
                  />
                  <select
                    value={input.type}
                    onChange={(e) =>
                      setMeta((m) => {
                        const inputs = [...m.inputs];
                        inputs[ii] = { ...inputs[ii]!, type: e.target.value as InputDef['type'] };
                        return { ...m, inputs };
                      })
                    }
                    style={{ ...inputStyle, width: 90 }}
                  >
                    <option value="string">string</option>
                    <option value="number">number</option>
                    <option value="boolean">boolean</option>
                    <option value="list">list</option>
                  </select>
                  <button
                    onClick={() => setMeta((m) => ({ ...m, inputs: m.inputs.filter((_, i) => i !== ii) }))}
                    style={{
                      background: 'transparent',
                      border: 0,
                      color: 'var(--construct-status-danger)',
                      cursor: 'pointer',
                    }}
                  >
                    ×
                  </button>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={input.required}
                      onChange={(e) =>
                        setMeta((m) => {
                          const inputs = [...m.inputs];
                          inputs[ii] = { ...inputs[ii]!, required: e.target.checked };
                          return { ...m, inputs };
                        })
                      }
                      style={{ accentColor: 'var(--pc-accent)' }}
                    />
                    <span style={{ fontSize: 10, color: 'var(--pc-text-muted)' }}>Required</span>
                  </label>
                  <input
                    type="text"
                    value={input.default}
                    onChange={(e) =>
                      setMeta((m) => {
                        const inputs = [...m.inputs];
                        inputs[ii] = { ...inputs[ii]!, default: e.target.value };
                        return { ...m, inputs };
                      })
                    }
                    placeholder="Default"
                    style={{ ...inputStyle, flex: 1 }}
                  />
                </div>
                <input
                  type="text"
                  value={input.description}
                  onChange={(e) =>
                    setMeta((m) => {
                      const inputs = [...m.inputs];
                      inputs[ii] = { ...inputs[ii]!, description: e.target.value };
                      return { ...m, inputs };
                    })
                  }
                  placeholder="Description"
                  style={inputStyle}
                />
              </div>
            ))}
          </SectionGroup>

          {/* Outputs */}
          <SectionGroup
            kicker="Outputs"
            count={meta.outputs.length}
            onAdd={() =>
              setMeta((m) => ({
                ...m,
                outputs: [...m.outputs, { name: '', source: '', description: '' }],
              }))
            }
          >
            {meta.outputs.map((output, oi) => (
              <div
                key={oi}
                style={{
                  padding: 8,
                  borderRadius: 8,
                  border: '1px solid var(--pc-border)',
                  background: 'var(--pc-bg-base)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                <div style={{ display: 'flex', gap: 6 }}>
                  <input
                    type="text"
                    value={output.name}
                    onChange={(e) =>
                      setMeta((m) => {
                        const outputs = [...m.outputs];
                        outputs[oi] = { ...outputs[oi]!, name: e.target.value };
                        return { ...m, outputs };
                      })
                    }
                    placeholder="Output name"
                    style={{ ...inputStyle, flex: 1, fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)' }}
                  />
                  <button
                    onClick={() => setMeta((m) => ({ ...m, outputs: m.outputs.filter((_, i) => i !== oi) }))}
                    style={{
                      background: 'transparent',
                      border: 0,
                      color: 'var(--construct-status-danger)',
                      cursor: 'pointer',
                    }}
                  >
                    ×
                  </button>
                </div>
                <input
                  type="text"
                  value={output.source}
                  onChange={(e) =>
                    setMeta((m) => {
                      const outputs = [...m.outputs];
                      outputs[oi] = { ...outputs[oi]!, source: e.target.value };
                      return { ...m, outputs };
                    })
                  }
                  placeholder="${step_id.output}"
                  style={{ ...inputStyle, fontFamily: 'var(--pc-font-mono, ui-monospace, monospace)' }}
                />
              </div>
            ))}
          </SectionGroup>
        </div>
      </div>
    </Panel>
  );
}

function SectionGroup({
  kicker,
  count,
  onAdd,
  addSlot,
  children,
}: {
  kicker: string;
  count: number;
  onAdd?: () => void;
  addSlot?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span className="construct-kicker">
          {kicker} ({count})
        </span>
        {addSlot ?? (
          onAdd ? (
            <button
              type="button"
              onClick={onAdd}
              className="construct-button"
              style={{ padding: '2px 8px', fontSize: 10 }}
            >
              + Add
            </button>
          ) : null
        )}
      </div>
      {children}
    </div>
  );
}

