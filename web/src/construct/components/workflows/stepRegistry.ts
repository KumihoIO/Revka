/**
 * stepRegistry.ts — Single source of truth for the workflow editor step palette.
 *
 * Step types mirror the authoritative `StepType` enum in
 * `operator-mcp/operator_mcp/workflow/schema.py`. When adding a new step type
 * to the executor, add it here too with an icon and category.
 */

import {
  ArrowRightLeft,
  Archive,
  Bell,
  Bot,
  CheckCircle,
  Crown,
  FileCode,
  GitBranch,
  Keyboard,
  LayoutGrid,
  Mail,
  Network,
  Repeat,
  RotateCw,
  Search,
  Send,
  Split,
  Image,
  Tag,
  Terminal,
  Users,
  type LucideIcon,
} from 'lucide-react';

export type StepCategory = 'execution' | 'control' | 'coordination' | 'human' | 'memory';

export interface StepTypeDef {
  /** Matches `StepType` enum value in schema.py */
  type: string;
  /** Human-readable label for UI */
  label: string;
  category: StepCategory;
  icon: LucideIcon;
  /** Single-line description (~80 chars) */
  description: string;
  /** Aliases / friendly verbs to fuzzy-match against in cmdk */
  searchTags: string[];
}

export const CATEGORY_META: Record<StepCategory, { label: string; description: string }> = {
  execution: {
    label: 'Execution',
    description: 'Run agents, code, shells, and emit outputs.',
  },
  control: {
    label: 'Control',
    description: 'Branch, loop, and orchestrate flow.',
  },
  coordination: {
    label: 'Coordination',
    description: 'Multi-agent collaboration and delegation.',
  },
  human: {
    label: 'Human',
    description: 'Pause for input, approval, or notifications.',
  },
  memory: {
    label: 'Memory',
    description: 'Resolve, tag, and deprecate Kumiho entities.',
  },
};

export const STEP_TYPES: StepTypeDef[] = [
  // ── Execution ──────────────────────────────────────────────────────────
  {
    type: 'agent',
    label: 'Agent',
    category: 'execution',
    icon: Bot,
    description: 'Run an LLM agent (Claude, Codex) with a prompt and skills.',
    searchTags: ['research', 'code', 'review', 'test', 'build', 'deploy', 'summarize', 'task', 'llm', 'claude', 'codex'],
  },
  {
    type: 'shell',
    label: 'Shell',
    category: 'execution',
    icon: Terminal,
    description: 'Execute a shell command and capture stdout/stderr.',
    searchTags: ['bash', 'command', 'exec', 'cli'],
  },
  {
    type: 'python',
    label: 'Python',
    category: 'execution',
    icon: FileCode,
    description: 'Run a Python script or inline code with JSON in/out.',
    searchTags: ['script', 'code', 'py', 'subprocess'],
  },
  {
    type: 'output',
    label: 'Output',
    category: 'execution',
    icon: Send,
    description: 'Format and publish a result, optionally as a Kumiho entity.',
    searchTags: ['publish', 'emit', 'result', 'render', 'template'],
  },
  {
    type: 'email',
    label: 'Email',
    category: 'execution',
    icon: Mail,
    description: 'Send an outbound email via SMTP with optional click tracking.',
    searchTags: ['smtp', 'send', 'mail', 'message', 'outreach'],
  },
  {
    type: 'image',
    label: 'Image',
    category: 'execution',
    icon: Image,
    description: 'Generate an image via codex and push it to canvas + Kumiho artifact.',
    searchTags: ['codex', 'png', 'render', 'visual', 'canvas', 'artwork', 'illustration'],
  },

  // ── Control ────────────────────────────────────────────────────────────
  {
    type: 'conditional',
    label: 'Conditional',
    category: 'control',
    icon: GitBranch,
    description: 'Branch on an expression — true/false handles route flow.',
    searchTags: ['if', 'else', 'gate', 'branch', 'condition'],
  },
  {
    type: 'parallel',
    label: 'Parallel',
    category: 'control',
    icon: Split,
    description: 'Fan out to multiple branches and join with all/any/majority.',
    searchTags: ['fork', 'fanout', 'concurrent', 'split'],
  },
  {
    type: 'for_each',
    label: 'For Each',
    category: 'control',
    icon: Repeat,
    description: 'Loop sub-steps over a range or list, sequentially.',
    searchTags: ['loop', 'iterate', 'range', 'foreach', 'iteration'],
  },
  {
    type: 'goto',
    label: 'Goto',
    category: 'control',
    icon: RotateCw,
    description: 'Jump back to an earlier step, optionally guarded by condition.',
    searchTags: ['loop', 'jump', 'iterate', 'rerun'],
  },
  {
    type: 'map_reduce',
    label: 'Map / Reduce',
    category: 'control',
    icon: LayoutGrid,
    description: 'Split a task across mappers and combine with a reducer.',
    searchTags: ['fan-out', 'reduce', 'aggregate', 'split'],
  },

  // ── Coordination ───────────────────────────────────────────────────────
  {
    type: 'supervisor',
    label: 'Supervisor',
    category: 'coordination',
    icon: Crown,
    description: 'Decompose a task and delegate to sub-agents.',
    searchTags: ['orchestrator', 'manager', 'delegate'],
  },
  {
    type: 'group_chat',
    label: 'Group Chat',
    category: 'coordination',
    icon: Users,
    description: 'Multiple agents discuss a topic with a moderator.',
    searchTags: ['conversation', 'discussion', 'multi-agent', 'debate'],
  },
  {
    type: 'handoff',
    label: 'Handoff',
    category: 'coordination',
    icon: ArrowRightLeft,
    description: 'Transfer execution to a different agent type with context.',
    searchTags: ['transfer', 'delegate', 'pass'],
  },
  {
    type: 'a2a',
    label: 'A2A Call',
    category: 'coordination',
    icon: Network,
    description: 'Call a remote agent over the Agent-to-Agent protocol.',
    searchTags: ['remote', 'protocol', 'agent', 'http'],
  },

  // ── Human ──────────────────────────────────────────────────────────────
  {
    type: 'human_input',
    label: 'Human Input',
    category: 'human',
    icon: Keyboard,
    description: 'Pause and collect free-text input from a human via channel.',
    searchTags: ['input', 'prompt', 'ask', 'wait'],
  },
  {
    type: 'human_approval',
    label: 'Human Approval',
    category: 'human',
    icon: CheckCircle,
    description: 'Pause until a human approves or rejects via dashboard/Slack/Discord.',
    searchTags: ['approve', 'review', 'gate', 'confirm', 'sign-off'],
  },
  {
    type: 'notify',
    label: 'Notify',
    category: 'human',
    icon: Bell,
    description: 'Broadcast a message to one or more channels (no wait).',
    searchTags: ['alert', 'message', 'broadcast', 'send'],
  },

  // ── Memory ─────────────────────────────────────────────────────────────
  {
    type: 'resolve',
    label: 'Resolve',
    category: 'memory',
    icon: Search,
    description: 'Look up a Kumiho entity by kind/tag/name and load its fields.',
    searchTags: ['lookup', 'fetch', 'find', 'kumiho', 'entity'],
  },
  {
    type: 'tag',
    label: 'Tag',
    category: 'memory',
    icon: Tag,
    description: 'Apply a tag to a Kumiho entity revision (and optionally untag).',
    searchTags: ['label', 'mark', 'kumiho', 'revision'],
  },
  {
    type: 'deprecate',
    label: 'Deprecate',
    category: 'memory',
    icon: Archive,
    description: 'Mark a Kumiho item as deprecated with an optional reason.',
    searchTags: ['archive', 'retire', 'kumiho', 'remove'],
  },
];

export const STEP_TYPES_BY_TYPE: Record<string, StepTypeDef> = Object.fromEntries(
  STEP_TYPES.map((s) => [s.type, s]),
);

export const STEP_TYPES_BY_CATEGORY: Record<StepCategory, StepTypeDef[]> = {
  execution: STEP_TYPES.filter((s) => s.category === 'execution'),
  control: STEP_TYPES.filter((s) => s.category === 'control'),
  coordination: STEP_TYPES.filter((s) => s.category === 'coordination'),
  human: STEP_TYPES.filter((s) => s.category === 'human'),
  memory: STEP_TYPES.filter((s) => s.category === 'memory'),
};

export const CATEGORY_ORDER: StepCategory[] = ['execution', 'control', 'coordination', 'human', 'memory'];
