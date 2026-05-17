import { Handle, Position, useUpdateNodeInternals, type NodeTypes } from '@xyflow/react';
import { useEffect, useRef } from 'react';
import {
  gateBranchHandle,
  gateBranchLabel,
  gateBranchStyle,
  type ConditionalBranchDefinition,
  type StepRunInfo,
  type TaskNodeData,
} from '@/construct/components/workflows/yamlSync';
import { workflowActionTone, workflowStatusTone } from '../../lib/orchestration';

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
    ? 'var(--construct-status-danger)'
    : data.blocked
      ? 'var(--construct-status-warning)'
      : data.running
        ? 'var(--construct-signal-live)'
        : accent;
  const failureReason = stepFailureReason(data.runInfo);
  const branchSummary = data.type === 'conditional' ? conditionalRunSummary(data.runInfo) : '';

  return (
    <div
      ref={ref}
      className="rounded-[14px] border px-4 py-3 shadow-sm flex flex-col"
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
      style={{
        // The React Flow node entry sets `height: 140` so the MiniMap
        // can render rectangles before measurement; minHeight here keeps
        // the same baseline visually but lets the card grow with content.
        // useNodeAutoSize() above pushes the new measured dimensions back
        // to React Flow so handles + MiniMap update on resize.
        width: '100%',
        minHeight: 140,
        minWidth: 220,
        maxWidth: 280,
        borderColor: selected ? operationalAccent : 'color-mix(in srgb, var(--construct-border-soft) 75%, transparent)',
        background: selected
          ? `linear-gradient(135deg, color-mix(in srgb, ${operationalAccent} var(--construct-node-accent-selected), transparent), transparent 78%), var(--construct-bg-panel-strong)`
          : `linear-gradient(180deg, color-mix(in srgb, ${operationalAccent} var(--construct-node-accent-idle), transparent), transparent 42%), var(--construct-bg-panel-strong)`,
        boxShadow: selected
          ? `0 0 0 1px ${operationalAccent}, 0 0 28px color-mix(in srgb, ${operationalAccent} 26%, transparent)`
          : data.failing
            ? `0 0 18px color-mix(in srgb, var(--construct-status-danger) 22%, transparent)`
            : data.blocked
              ? `0 0 14px color-mix(in srgb, var(--construct-status-warning) 18%, transparent)`
              : 'var(--construct-shadow-panel)',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: operationalAccent, width: 9, height: 9 }} />

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
            {data.name || data.taskId}
          </div>
          <div className="mt-1 flex flex-wrap gap-1.5">
            <span
              className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
              style={{ background: 'color-mix(in srgb, var(--construct-bg-elevated) 85%, transparent)', color: operationalAccent }}
            >
              {data.type}
            </span>
            {data.runInfo ? (
              <span
                className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]"
                style={{ background: 'color-mix(in srgb, var(--construct-bg-elevated) 85%, transparent)', color: operationalAccent }}
              >
                {data.runInfo.status}
              </span>
            ) : null}
            {data.failing ? (
              <span className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ background: 'color-mix(in srgb, var(--construct-status-danger) 14%, transparent)', color: 'var(--construct-status-danger)' }}>
                failure path
              </span>
            ) : null}
            {data.blocked ? (
              <span className="rounded-md px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em]" style={{ background: 'color-mix(in srgb, var(--construct-status-warning) 14%, transparent)', color: 'var(--construct-status-warning)' }}>
                blocked
              </span>
            ) : null}
          </div>
        </div>
        {data.runInfo?.agent_type ? (
          <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--construct-signal-network-soft)', color: 'var(--construct-signal-network)' }}>
            {data.runInfo.agent_type}
          </span>
        ) : null}
      </div>

      {data.description ? (
        <p className="mt-2 line-clamp-2 text-[11px] leading-5" style={{ color: 'var(--construct-text-secondary)' }}>
          {data.description}
        </p>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-1.5">
        {data.agentHints.slice(0, 2).map((hint) => (
          <span key={hint} className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--construct-bg-elevated)', color: 'var(--construct-text-secondary)' }}>
            {hint}
          </span>
        ))}
        {data.skills.slice(0, 2).map((skill) => (
          <span key={skill} className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--construct-signal-live-soft)', color: 'var(--construct-signal-selected)' }}>
            {skill.replace(/^kref:\/\/.*\//, '').replace(/\.skilldef$/, '')}
          </span>
        ))}
        {data.runInfo?.transcript?.length ? (
          <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'var(--construct-signal-network-soft)', color: 'var(--construct-signal-network)' }}>
            {data.runInfo.transcript.length} rounds
          </span>
        ) : null}
        {data.runInfo?.skills?.length ? (
          <span className="rounded-md px-1.5 py-0.5 text-[10px] font-medium" style={{ background: 'color-mix(in srgb, var(--construct-status-warning) 18%, transparent)', color: 'var(--construct-status-warning)' }}>
            {data.runInfo.skills.length} skills
          </span>
        ) : null}
      </div>

      {branchSummary ? (
        <div className="mt-2 rounded-md px-2 py-1 text-[10px] leading-4" style={{ background: 'color-mix(in srgb, var(--construct-status-success) 12%, transparent)', color: 'var(--construct-text-secondary)' }}>
          {branchSummary}
        </div>
      ) : null}

      {failureReason ? (
        <div className="mt-2 rounded-md px-2 py-1 text-[10px] leading-4" style={{ background: 'color-mix(in srgb, var(--construct-status-danger) 12%, transparent)', color: 'var(--construct-status-danger)' }}>
          {failureReason}
        </div>
      ) : null}

      <Handle type="source" position={Position.Bottom} style={{ background: operationalAccent, width: 9, height: 9 }} />
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
  const accent = 'var(--construct-status-warning)';
  const ref = useNodeAutoSize(id);
  const branches = getGateBranches(data);
  const primaryCondition = branches.find((branch) => branch.condition.trim() !== 'default')?.condition;

  return (
    <div
      ref={ref}
      className="rounded-[14px] border px-4 py-3 shadow-sm flex flex-col"
      style={{
        // node.height = 96 hint for MiniMap; minHeight here keeps the
        // baseline and lets the gate card grow if labels push it taller.
        width: '100%',
        minHeight: 96,
        minWidth: 200,
        maxWidth: 250,
        borderColor: selected ? accent : 'color-mix(in srgb, var(--construct-border-soft) 75%, transparent)',
        background: selected
          ? `linear-gradient(135deg, color-mix(in srgb, ${accent} var(--construct-node-accent-selected), transparent), transparent 78%), var(--construct-bg-panel-strong)`
          : `linear-gradient(180deg, color-mix(in srgb, ${accent} var(--construct-node-accent-idle), transparent), transparent 42%), var(--construct-bg-panel-strong)`,
        boxShadow: selected ? `0 0 0 1px ${accent}, 0 0 24px color-mix(in srgb, ${accent} 22%, transparent)` : 'var(--construct-shadow-panel)',
      }}
    >
      <Handle type="target" position={Position.Top} style={{ background: accent, width: 9, height: 9 }} />
      <div className="flex items-center gap-2">
        <div className="h-4 w-4 rotate-45" style={{ background: accent, opacity: 0.85 }} />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold" style={{ color: 'var(--construct-text-primary)' }}>
            {data.name || data.taskId}
          </div>
          <div className="mt-1 text-[11px] font-mono" style={{ color: 'var(--construct-text-secondary)' }}>
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
      {branches.map((branch, index) => {
        const style = gateBranchStyle(branch, index);
        const left = `${((index + 1) / (branches.length + 1)) * 100}%`;
        return (
          <Handle
            key={gateBranchHandle(index)}
            type="source"
            position={Position.Bottom}
            id={gateBranchHandle(index)}
            style={{ left, background: style.stroke, width: 9, height: 9 }}
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
