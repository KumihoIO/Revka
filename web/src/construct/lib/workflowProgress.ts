import type { WorkflowRunSummary } from '@/types/api';

export type WorkflowProgressRun = Pick<
  WorkflowRunSummary,
  | 'steps_completed'
  | 'expanded_steps_completed'
  | 'current_iteration'
  | 'current_loop_total'
  | 'current_step_instance'
>;

function parseCount(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function expandedStepCount(run: WorkflowProgressRun): number | null {
  const expanded = parseCount(run.expanded_steps_completed);
  if (expanded === null) return null;
  const topLevel = parseCount(run.steps_completed) ?? 0;
  return expanded > topLevel ? expanded : null;
}

export function loopProgressLabel(
  run: WorkflowProgressRun,
  tpl: (key: string, vars?: Record<string, string | number>) => string,
): string | null {
  const iteration = run.current_iteration ?? '';
  const step = run.current_step_instance ?? '';
  if (!iteration || !step) return null;

  const total = run.current_loop_total ?? '';
  if (total) {
    return tpl('runs.stats.loop_fraction', { iteration, total, step });
  }
  return tpl('runs.stats.loop_iteration', { iteration, step });
}
