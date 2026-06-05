import { Handle, Position, useUpdateNodeInternals, type NodeTypes } from '@xyflow/react';
import { useEffect, useRef, type CSSProperties } from 'react';
import {
  gateBranchHandle,
  gateBranchLabel,
  gateBranchStyle,
  type ConditionalBranchDefinition,
  type StepRunInfo,
  type TaskNodeData,
} from '@/revka/components/workflows/yamlSync';
import AgentAvatar from '@/revka/components/ui/AgentAvatar';
import { workflowActionTone, workflowStatusTone } from '../../lib/orchestration';

function cssUrl(value: string): string {
  return `url("${value.replace(/"/g, '%22')}")`;
}

// Tells React Flow to re-measure this node and re-anchor handles + MiniMap
// rectangles whenever the rendered card resizes. Cards grow past their
// initial `node.height` hint when content (description, chips, run badges)
// pushes them taller, and without this hook the bottom Handle stays glued
// to the original 140px hint and the MiniMap shows stale rectangles.
function useNodeAutoSize(id: string) {
  const updateNodeInternals = useUpdateNodeInternals();
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(() => updateNodeInternals(id));
    observer.observe(el);
    return () => observer.disconnect();
  }, [id, updateNodeInternals]);

  return ref;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function textValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function compactText(value: string, max = 110): string {
  const oneLine = value.replace(/\s+/g, ' ').trim();
  return oneLine.length > max ? `${oneLine.slice(0, max - 1)}…` : oneLine;
}

function stepFailureReason(runInfo?: StepRunInfo): string {
  if (!runInfo || runInfo.status !== 'failed') return '';
  const output = asRecord(runInfo.output_data);
  const input = asRecord(runInfo.input_data);
  const candidates = [
    runInfo.error,
    output.error,
    output.entity_error,
    output.entity_artifact_error,
    output.entity_tag_error,
    output.register_output_error,
    output.structured_output_error,
    asRecord(output.error_message).content,
    output.stderr,
    output.stderr_preview,
    input.command ? `command: ${input.command}` : '',
  ];
  for (const candidate of candidates) {
    const text = compactText(textValue(candidate));
    if (text) return text;
  }
  return '';
}

function conditionalRunSummary(runInfo?: StepRunInfo): string {
  if (!runInfo || runInfo.status !== 'completed') return '';
  const input = asRecord(runInfo.input_data);
  const output = asRecord(runInfo.output_data);
  const index = textValue(output.matched_branch_index ?? input.matched_branch_index);
  const goto = textValue(output.matched_goto);
  const condition = textValue(output.matched_condition ?? input.matched_condition);
  const emitted = textValue(output.matched_output ?? runInfo.output_preview);
  const parts: string[] = [];
  const numericIndex = Number(index);
  const branchLabel = textValue(output.matched_branch_label);
  if (branchLabel) parts.push(branchLabel);
  else if (Number.isFinite(numericIndex) && numericIndex >= 0) parts.push(`branch ${numericIndex + 1}`);
  if (goto) parts.push(`to ${goto}`);
  if (condition) parts.push(`if ${condition}`);
  if (emitted) parts.push(`out ${emitted}`);
  return compactText(parts.join(' · '), 120);
}

function WorkflowNode({
  id,
  data,
  selected,
}: {
  id: string;
  data: TaskNodeData & { blocked?: boolean; failing?: boolean; running?: boolean };
  selected?: boolean;
}) {
  const ref = useNodeAutoSize(id);
  const accent = data.runInfo ? workflowStatusTone(data.runInfo.status) : workflowActionTone(data.type);
  const operationalAccent = data.failing
    ? 'var(--revka-status-danger)'
    : data.blocked
      ? 'var(--revka-status-warning)'
      : data.running
        ? 'var(--revka-signal-live)'
        : accent;
  const failureReason = stepFailureReason(data.runInfo);
  const branchSummary = data.type === 'conditional' ? conditionalRunSummary(data.runInfo) : '';
  const poolAgentLabel = data.runInfo?.template_name || data.assign || data.template;
  const nodeState = data.failing
    ? 'danger'
    : data.blocked
      ? 'warning'
      : data.running
        ? 'running'
        : data.runInfo?.status ?? 'idle';
  const accentLayer = selected
    ? `linear-gradient(135deg, color-mix(in srgb, ${operationalAccent} var(--revka-node-accent-selected), transparent), transparent 78%)`
    : `linear-gradient(180deg, color-mix(in srgb, ${operationalAccent} var(--revka-node-accent-idle), transparent), transparent 42%)`;
  const hasAvatarBackground = Boolean(data.agentAvatarUrl);
  const nodeBackgroundImage = hasAvatarBackground
    ? [
        'linear-gradient(90deg, var(--revka-bg-panel-strong) 0%, color-mix(in srgb, var(--revka-bg-panel-strong) 88%, transparent) 50%, color-mix(in srgb, var(--revka-bg-panel-strong) 32%, transparent) 100%)',
        accentLayer,
        cssUrl(data.agentAvatarUrl!),
      ].join(', ')
    : accentLayer;
  const nodeStyle = {
    '--revka-node-accent': operationalAccent,
    // The React Flow node entry sets `height: 140` so the MiniMap
    // can render rectangles before measurement; minHeight here keeps
    // the same baseline visually but lets the card grow with content.
    // useNodeAutoSize() above pushes the new measured dimensions back
    // to React Flow so handles + MiniMap update on resize.
    width: '100%',
    minHeight: 140,
    minWidth: 220,
    maxWidth: 280,
    borderColor: selected ? operationalAccent : 'color-mix(in srgb, var(--revka-border-soft) 82%, transparent)',
    backgroundColor: 'var(--revka-bg-panel-strong)',
    backgroundImage: nodeBackgroundImage,
    backgroundRepeat: 'no-repeat',
    backgroundPosition: hasAvatarBackground ? '0 0, 0 0, right -22px center' : undefined,
    backgroundSize: hasAvatarBackground ? '100% 100%, 100% 100%, 56% auto' : undefined,
    boxShadow: selected
      ? `0 0 0 1px color-mix(in srgb, ${operationalAccent} 70%, var(--revka-border-strong)), 0 16px 32px color-mix(in srgb, ${operationalAccent} 12%, transparent)`
      : data.failing
        ? `0 14px 28px color-mix(in srgb, var(--revka-status-danger) 10%, transparent)`
        : data.blocked
          ? `0 14px 26px color-mix(in srgb, var(--revka-status-warning) 8%, transparent)`
          : 'var(--revka-shadow-panel)',
  } as CSSProperties;
  const handleStyle = {
    width: 10,
    height: 10,
    zIndex: 2,
  } as CSSProperties;

  return (
    <div
      ref={ref}
      className="revka-workflow-node rounded-[8px] border px-4 py-3 shadow-sm flex flex-col"
      data-selected={selected ? 'true' : 'false'}
      data-state={nodeState}
      title={[
        data.name || data.taskId,
        `Type: ${data.type}`,
        data.runInfo?.status ? `Status: ${data.runInfo.status}` : null,
        data.runInfo?.agent_type ? `Agent: ${data.runInfo.agent_type}${data.runInfo.role ? ` / ${data.runInfo.role}` : ''}` : null,
        branchSummary ? `Resolved: ${branchSummary}` : null,
        failureReason ? `Failure: ${failureReason}` : null,
        data.blocked ? 'Blocked by upstream failure' : null,
        data.failing ? 'On failure path' : null,
        data.runInfo?.skills?.length ? `Skills: ${data.runInfo.skills.join(', ')}` : null,
      ].filter(Boolean).join('\n')}
      style={nodeStyle}
    >
      <Handle className="revka-workflow-handle" type="target" position={Position.Top} style={handleStyle} />

      <div className="relative z-[1] flex min-h-full flex-col">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
              {data.name || data.taskId}
            </div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              <span
                className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
                style={{ background: 'color-mix(in srgb, var(--revka-bg-elevated) 80%, transparent)', color: operationalAccent }}
              >
                {data.type}
              </span>
              {data.runInfo ? (
                <span
                  className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
                  style={{ background: 'color-mix(in srgb, var(--revka-bg-elevated) 80%, transparent)', color: operationalAccent }}
                >
                  {data.runInfo.status}
                </span>
              ) : null}
              {data.failing ? (
                <span className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ background: 'color-mix(in srgb, var(--revka-status-danger) 12%, transparent)', color: 'var(--revka-status-danger)' }}>
                  failure path
                </span>
              ) : null}
              {data.blocked ? (
                <span className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ background: 'color-mix(in srgb, var(--revka-status-warning) 12%, transparent)', color: 'var(--revka-status-warning)' }}>
                  blocked
                </span>
              ) : null}
            </div>
          </div>
          {poolAgentLabel ? (
            <span
              className="inline-flex max-w-[116px] items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium"
              style={{ background: 'var(--revka-signal-network-soft)', color: 'var(--revka-signal-network)' }}
              title={data.agentDisplayName || poolAgentLabel}
            >
              <AgentAvatar
                src={data.agentAvatarUrl}
                alt={data.agentDisplayName || poolAgentLabel}
                size={16}
                radius={4}
                iconSize={10}
              />
              <span className="truncate">{poolAgentLabel}</span>
            </span>
          ) : data.runInfo?.agent_type ? (
            <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--revka-signal-network-soft)', color: 'var(--revka-signal-network)' }}>
              {data.runInfo.agent_type}
            </span>
          ) : null}
        </div>

        {data.description ? (
          <p className="mt-2 line-clamp-2 text-[11px] leading-5" style={{ color: 'var(--revka-text-secondary)' }}>
            {data.description}
          </p>
        ) : null}

        <div className="mt-3 flex flex-wrap gap-1.5">
          {data.agentHints.slice(0, 2).map((hint) => (
            <span key={hint} className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--revka-bg-elevated)', color: 'var(--revka-text-secondary)' }}>
              {hint}
            </span>
          ))}
          {data.skills.slice(0, 2).map((skill) => (
            <span key={skill} className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--revka-signal-live-soft)', color: 'var(--revka-signal-selected)' }}>
              {skill.replace(/^kref:\/\/.*\//, '').replace(/\.skilldef$/, '')}
            </span>
          ))}
          {data.runInfo?.transcript?.length ? (
            <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--revka-signal-network-soft)', color: 'var(--revka-signal-network)' }}>
              {data.runInfo.transcript.length} rounds
            </span>
          ) : null}
          {data.runInfo?.skills?.length ? (
            <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'color-mix(in srgb, var(--revka-status-warning) 14%, transparent)', color: 'var(--revka-status-warning)' }}>
              {data.runInfo.skills.length} skills
            </span>
          ) : null}
        </div>

        {branchSummary ? (
          <div className="mt-2 rounded-md px-2 py-1 text-[10px] leading-4" style={{ background: 'color-mix(in srgb, var(--revka-status-success) 10%, transparent)', color: 'var(--revka-text-secondary)' }}>
            {branchSummary}
          </div>
        ) : null}

        {failureReason ? (
          <div className="mt-2 rounded-md px-2 py-1 text-[10px] leading-4" style={{ background: 'color-mix(in srgb, var(--revka-status-danger) 10%, transparent)', color: 'var(--revka-status-danger)' }}>
            {failureReason}
          </div>
        ) : null}
      </div>

      <Handle className="revka-workflow-handle" type="source" position={Position.Bottom} style={handleStyle} />
    </div>
  );
}

function getGateBranches(data: TaskNodeData): ConditionalBranchDefinition[] {
  if (data.conditionalBranches?.length > 0) return data.conditionalBranches;
  const branches: ConditionalBranchDefinition[] = [];
  if (data.condition || data.onTrueValue) {
    branches.push({ condition: data.condition || '', goto: '', value: data.onTrueValue || undefined });
  }
  if (data.onFalseValue) {
    branches.push({ condition: 'default', goto: '', value: data.onFalseValue || undefined });
  }
  return branches.length > 0 ? branches : [
    { condition: data.condition || '', goto: '', value: data.onTrueValue || undefined },
    { condition: 'default', goto: '', value: data.onFalseValue || undefined },
  ];
}

function GateNodeV2({ id, data, selected }: { id: string; data: TaskNodeData; selected?: boolean }) {
  const accent = 'var(--revka-status-warning)';
  const ref = useNodeAutoSize(id);
  const branches = getGateBranches(data);
  const primaryCondition = branches.find((branch) => branch.condition.trim() !== 'default')?.condition;
  const gateStyle = {
    '--revka-node-accent': accent,
    // node.height = 96 hint for MiniMap; minHeight here keeps the
    // baseline and lets the gate card grow if labels push it taller.
    width: '100%',
    minHeight: 96,
    minWidth: 200,
    maxWidth: 250,
    borderColor: selected ? accent : 'color-mix(in srgb, var(--revka-border-soft) 82%, transparent)',
    background: selected
      ? `linear-gradient(135deg, color-mix(in srgb, ${accent} var(--revka-node-accent-selected), transparent), transparent 78%), var(--revka-bg-panel-strong)`
      : `linear-gradient(180deg, color-mix(in srgb, ${accent} var(--revka-node-accent-idle), transparent), transparent 42%), var(--revka-bg-panel-strong)`,
    boxShadow: selected
      ? `0 0 0 1px color-mix(in srgb, ${accent} 70%, var(--revka-border-strong)), 0 16px 30px color-mix(in srgb, ${accent} 12%, transparent)`
      : 'var(--revka-shadow-panel)',
  } as CSSProperties;
  const handleStyle = {
    width: 10,
    height: 10,
    zIndex: 2,
  } as CSSProperties;

  return (
    <div
      ref={ref}
      className="revka-workflow-node rounded-[8px] border px-4 py-3 shadow-sm flex flex-col"
      data-selected={selected ? 'true' : 'false'}
      data-state="gate"
      style={gateStyle}
    >
      <Handle className="revka-workflow-handle" type="target" position={Position.Top} style={handleStyle} />
      <div className="relative z-[1] flex min-h-full flex-col">
        <div className="flex items-center gap-2">
          <div className="h-4 w-4 rotate-45 border" style={{ borderColor: accent, background: 'color-mix(in srgb, var(--revka-bg-panel-strong) 82%, transparent)' }} />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold" style={{ color: 'var(--revka-text-primary)' }}>
              {data.name || data.taskId}
            </div>
            <div className="mt-1 text-[11px] font-mono" style={{ color: 'var(--revka-text-secondary)' }}>
              {primaryCondition || data.condition || 'No condition set'}
            </div>
          </div>
        </div>
        <div className="mt-3 flex flex-col gap-1.5 text-[10px] font-semibold uppercase tracking-[0.12em]">
          {branches.map((branch, index) => {
            const style = gateBranchStyle(branch, index);
            return (
              <div key={gateBranchHandle(index)} className="flex items-center justify-between gap-2">
                <span className="truncate" style={{ color: style.stroke }}>
                  {gateBranchLabel(branch, index)}
                </span>
                {branch.value ? (
                  <span className="truncate font-mono normal-case tracking-normal" style={{ color: style.stroke }}>
                    {branch.value}
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
      {branches.map((branch, index) => {
        const style = gateBranchStyle(branch, index);
        const left = `${((index + 1) / (branches.length + 1)) * 100}%`;
        return (
          <Handle
            key={gateBranchHandle(index)}
            className="revka-workflow-handle"
            type="source"
            position={Position.Bottom}
            id={gateBranchHandle(index)}
            style={{ ...handleStyle, left, '--revka-node-accent': style.stroke } as CSSProperties}
          />
        );
      })}
    </div>
  );
}

export const workflowNodeTypesV2: NodeTypes = {
  taskNode: WorkflowNode,
  gateNode: GateNodeV2,
};
