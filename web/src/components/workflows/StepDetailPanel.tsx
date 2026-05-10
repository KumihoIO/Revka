/**
 * StepDetailPanel — type-aware detail view for non-agent workflow steps.
 *
 * Replaces the legacy "no tool detail available" dead end. Renders inputs
 * (what was queried/run) and outputs (what came back) for each non-agent
 * step type so users can diagnose silent failures — e.g. a `resolve` that
 * reported "completed" but actually didn't match anything.
 *
 * Data comes from the per-step `input_data` and `output_data` blobs the
 * executor persists (PR #220), exposed by the gateway as raw JSON. Each
 * sub-renderer reads the keys it cares about with light type guards.
 *
 * Agent-step rendering is NOT handled here — those go through the existing
 * RunLog tool-call panel in WorkflowRunLive.
 */
import { useState } from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  Activity,
  Clock,
  AlertCircle,
  ChevronDown,
  ChevronRight,
} from 'lucide-react';
import type { StepRunInfo } from './yamlSync';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Json = Record<string, unknown>;

function asString(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined;
}
function asNumber(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}
function asBool(v: unknown): boolean | undefined {
  return typeof v === 'boolean' ? v : undefined;
}
function asArray(v: unknown): unknown[] | undefined {
  return Array.isArray(v) ? v : undefined;
}

function statusColorFor(status: string): string {
  if (status === 'completed') return 'var(--construct-status-success)';
  if (status === 'failed') return 'var(--construct-status-danger)';
  if (status === 'running') return 'var(--construct-signal-live)';
  return 'var(--pc-text-muted)';
}

// ---------------------------------------------------------------------------
// Shared primitives — match TaskNode/RunLog visual style
// ---------------------------------------------------------------------------

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="text-[9px] uppercase tracking-wider font-semibold mb-1"
      style={{ color: 'var(--pc-text-faint)' }}
    >
      {children}
    </div>
  );
}

function FieldRow({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-baseline gap-2 text-[11px] py-0.5">
      <span className="shrink-0 w-20" style={{ color: 'var(--pc-text-muted)' }}>{label}</span>
      <span
        className={`flex-1 min-w-0 break-words ${mono ? 'font-mono' : ''}`}
        style={{ color: 'var(--pc-text-primary)' }}
      >
        {value}
      </span>
    </div>
  );
}

function Card({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="rounded-lg border px-3 py-2"
      style={{ borderColor: 'var(--pc-border)', background: 'var(--pc-bg-elevated)' }}
    >
      {children}
    </div>
  );
}

function CodeBlock({ text, maxHeight = '12rem' }: { text: string; maxHeight?: string }) {
  return (
    <pre
      className="whitespace-pre-wrap break-words text-[10px] leading-relaxed rounded p-2 overflow-auto"
      style={{
        color: 'var(--pc-text-secondary)',
        background: 'var(--pc-bg-base)',
        maxHeight,
      }}
    >
      {text}
    </pre>
  );
}

function CollapsibleBlock({
  label,
  text,
  defaultOpen = false,
}: {
  label: string;
  text: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (!text) return null;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 text-[10px] py-0.5"
        style={{ color: 'var(--pc-text-muted)' }}
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        <span className="uppercase tracking-wider">{label}</span>
      </button>
      {open && <CodeBlock text={text} />}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const color = statusColorFor(status);
  const Icon =
    status === 'completed' ? CheckCircle2
    : status === 'failed' ? AlertTriangle
    : status === 'running' ? Activity
    : Clock;
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      <Icon
        className={`h-3.5 w-3.5 shrink-0 ${status === 'running' ? 'animate-spin' : ''}`}
        style={{ color }}
      />
      <span className="font-medium" style={{ color }}>
        {status === 'pending' ? 'Waiting for dependencies'
          : status === 'running' ? 'Executing'
          : status === 'completed' ? 'Step completed'
          : status === 'failed' ? 'Step failed'
          : status === 'skipped' ? 'Skipped'
          : status}
      </span>
    </div>
  );
}

function ExitCodeBadge({ code }: { code: number }) {
  const ok = code === 0;
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold"
      style={{
        background: `color-mix(in srgb, ${ok ? 'var(--construct-status-success)' : 'var(--construct-status-danger)'} 16%, transparent)`,
        color: ok ? 'var(--construct-status-success)' : 'var(--construct-status-danger)',
      }}
    >
      exit {code}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Per-type renderers
// ---------------------------------------------------------------------------

function ResolvePanel({ input, output, status }: { input: Json; output: Json; status: string }) {
  const kind = asString(input.kind);
  const tag = asString(input.tag);
  const namePattern = asString(input.name_pattern);
  const space = asString(input.space);
  const mode = asString(input.mode);
  const failIfMissing = asBool(input.fail_if_missing);

  const matchedKref = asString(output.matched_kref);
  const matchedName = asString(output.matched_name);
  const hasMatch = !!matchedKref;
  const stepDone = status === 'completed';

  return (
    <>
      <Card>
        <SectionLabel>Searched</SectionLabel>
        {kind && <FieldRow label="kind" value={kind} mono />}
        {tag && <FieldRow label="tag" value={tag} mono />}
        {namePattern && <FieldRow label="name" value={namePattern} mono />}
        {space && <FieldRow label="space" value={space} mono />}
        {mode && <FieldRow label="mode" value={mode} mono />}
      </Card>
      <Card>
        <SectionLabel>Result</SectionLabel>
        {hasMatch ? (
          <div className="flex items-start gap-1.5 text-[11px]">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5" style={{ color: 'var(--construct-status-success)' }} />
            <div className="flex-1 min-w-0">
              <div style={{ color: 'var(--construct-status-success)' }} className="font-medium">
                Found {matchedName ?? '(unnamed)'}
              </div>
              {matchedKref && (
                <div className="font-mono text-[10px] break-all mt-0.5" style={{ color: 'var(--pc-text-secondary)' }}>
                  {matchedKref}
                </div>
              )}
            </div>
          </div>
        ) : stepDone ? (
          // Distinguishable: step succeeded but nothing matched (fail_if_missing=false).
          // This is the silent-failure case the user reported.
          <div
            className="flex items-start gap-1.5 text-[11px] rounded p-2"
            style={{
              background: 'color-mix(in srgb, var(--construct-status-warning) 14%, transparent)',
            }}
          >
            <AlertCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" style={{ color: 'var(--construct-status-warning)' }} />
            <div className="flex-1 min-w-0">
              <div style={{ color: 'var(--construct-status-warning)' }} className="font-medium">
                No match found
              </div>
              <div className="text-[10px] mt-0.5" style={{ color: 'var(--pc-text-secondary)' }}>
                Step succeeded because <span className="font-mono">fail_if_missing={String(failIfMissing ?? false)}</span>.
                Downstream steps may receive an empty kref.
              </div>
            </div>
          </div>
        ) : (
          <div className="text-[11px]" style={{ color: 'var(--pc-text-muted)' }}>—</div>
        )}
      </Card>
    </>
  );
}

function ShellPanel({ input, output, error }: { input: Json; output: Json; error?: string }) {
  const command = asString(input.command) ?? '';
  const cwd = asString(input.cwd);
  const timeout = asNumber(input.timeout_secs);
  const allowFailure = asBool(input.allow_failure);
  const exitCode = asNumber(output.exit_code);
  const stdout = asString(output.stdout) ?? '';
  const stderr = asString(output.stderr) ?? '';
  const stdoutTruncated = asBool(output.stdout_truncated);
  const stderrTruncated = asBool(output.stderr_truncated);

  return (
    <>
      <Card>
        <SectionLabel>Command</SectionLabel>
        <CodeBlock text={command || '(empty)'} maxHeight="6rem" />
        <div className="flex items-center gap-3 mt-2 text-[10px]" style={{ color: 'var(--pc-text-muted)' }}>
          {typeof exitCode === 'number' && <ExitCodeBadge code={exitCode} />}
          {timeout != null && <span>timeout {timeout}s</span>}
          {cwd && <span className="font-mono truncate">cwd: {cwd}</span>}
          {allowFailure && <span>allow_failure</span>}
        </div>
      </Card>
      {(stdout || stderr || error) && (
        <Card>
          <SectionLabel>Output</SectionLabel>
          <div className="flex flex-col gap-1.5">
            {stdout && (
              <CollapsibleBlock
                label={stdoutTruncated ? 'stdout (truncated — show full output below)' : 'stdout'}
                text={stdout}
                defaultOpen
              />
            )}
            {stderr && (
              <CollapsibleBlock
                label={stderrTruncated ? 'stderr (truncated)' : 'stderr'}
                text={stderr}
                defaultOpen={!!error}
              />
            )}
            {error && (
              <div className="text-[10px] rounded p-2"
                style={{ background: 'var(--pc-bg-base)', color: 'var(--construct-status-danger)' }}>
                {error}
              </div>
            )}
            {(stdoutTruncated || stderrTruncated) && (
              <div className="text-[10px]" style={{ color: 'var(--pc-text-faint)' }}>
                Output truncated for persistence — full content is in the daemon logs.
              </div>
            )}
          </div>
        </Card>
      )}
    </>
  );
}

function PythonPanel({ input, output, error }: { input: Json; output: Json; error?: string }) {
  const scriptPath = asString(input.script_path);
  const codePreview = asString(input.code_preview);
  const codeLength = asNumber(input.code_length);
  const args = asArray(input.args);
  const timeout = asNumber(input.timeout_secs);
  const allowFailure = asBool(input.allow_failure);
  const exitCode = asNumber(output.exit_code);
  const stdout = asString(output.stdout) ?? '';
  const stderr = asString(output.stderr) ?? '';
  const stdoutTruncated = asBool(output.stdout_truncated);
  const stderrTruncated = asBool(output.stderr_truncated);

  return (
    <>
      <Card>
        <SectionLabel>{scriptPath ? 'Script' : 'Code'}</SectionLabel>
        {scriptPath ? (
          <FieldRow label="path" value={scriptPath} mono />
        ) : (
          <CodeBlock text={codePreview ?? '(no code preview)'} maxHeight="8rem" />
        )}
        {args && args.length > 0 && (
          <FieldRow label="args" value={args.map((a) => String(a)).join(' ')} mono />
        )}
        <div className="flex items-center gap-3 mt-2 text-[10px]" style={{ color: 'var(--pc-text-muted)' }}>
          {typeof exitCode === 'number' && <ExitCodeBadge code={exitCode} />}
          {codeLength != null && <span>{codeLength} chars</span>}
          {timeout != null && <span>timeout {timeout}s</span>}
          {allowFailure && <span>allow_failure</span>}
        </div>
      </Card>
      {(stdout || stderr || error) && (
        <Card>
          <SectionLabel>Output</SectionLabel>
          <div className="flex flex-col gap-1.5">
            {stdout && (
              <CollapsibleBlock
                label={stdoutTruncated ? 'stdout (truncated)' : 'stdout'}
                text={stdout}
                defaultOpen
              />
            )}
            {stderr && (
              <CollapsibleBlock
                label={stderrTruncated ? 'stderr (truncated)' : 'stderr'}
                text={stderr}
                defaultOpen={!!error}
              />
            )}
            {error && (
              <div className="text-[10px] rounded p-2"
                style={{ background: 'var(--pc-bg-base)', color: 'var(--construct-status-danger)' }}>
                {error}
              </div>
            )}
            {(stdoutTruncated || stderrTruncated) && (
              <div className="text-[10px]" style={{ color: 'var(--pc-text-faint)' }}>
                Output truncated for persistence — full content is in the daemon logs.
              </div>
            )}
          </div>
        </Card>
      )}
    </>
  );
}

function OutputPanel({ input, output }: { input: Json; output: Json }) {
  const format = asString(input.format);
  const templatePreview = asString(input.template_preview);
  const templateLength = asNumber(input.template_length);
  const entityKind = asString(input.entity_kind);
  const entityTag = asString(input.entity_tag);
  const entitySpace = asString(input.entity_space);
  const entityName = asString(input.entity_name);
  const entityRegistered = asBool(output.entity_registered);
  const entityKref = asString(output.entity_kref);

  return (
    <>
      <Card>
        <SectionLabel>Output</SectionLabel>
        {format && <FieldRow label="format" value={format} mono />}
        {entityKind && <FieldRow label="kind" value={entityKind} mono />}
        {entityTag && <FieldRow label="tag" value={entityTag} mono />}
        {entitySpace && <FieldRow label="space" value={entitySpace} mono />}
        {entityName && <FieldRow label="name" value={entityName} mono />}
        {templateLength != null && (
          <FieldRow label="template" value={`${templateLength} chars`} />
        )}
        {templatePreview && (
          <div className="mt-1.5">
            <CollapsibleBlock label="template preview" text={templatePreview} />
          </div>
        )}
      </Card>
      <Card>
        <SectionLabel>Result</SectionLabel>
        {entityRegistered ? (
          <div className="flex items-start gap-1.5 text-[11px]">
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5" style={{ color: 'var(--construct-status-success)' }} />
            <div className="flex-1 min-w-0">
              <div style={{ color: 'var(--construct-status-success)' }} className="font-medium">Entity registered</div>
              {entityKref && (
                <div className="font-mono text-[10px] break-all mt-0.5" style={{ color: 'var(--pc-text-secondary)' }}>
                  {entityKref}
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="text-[11px]" style={{ color: 'var(--pc-text-secondary)' }}>
            Rendered output (no entity persisted)
          </div>
        )}
      </Card>
    </>
  );
}

function NotifyPanel({ input, output }: { input: Json; output: Json }) {
  const title = asString(input.title);
  const message = asString(input.message);
  const channels = asArray(input.channels);
  const channelId = asString(input.channel_id);
  const delivered = asBool(output.delivered);

  return (
    <Card>
      <SectionLabel>Notification</SectionLabel>
      {title && <FieldRow label="title" value={title} />}
      {message && (
        <div className="mt-1.5">
          <SectionLabel>Message</SectionLabel>
          <CodeBlock text={message} maxHeight="6rem" />
        </div>
      )}
      <div className="mt-1.5">
        {channels && channels.length > 0 && (
          <FieldRow label="channels" value={channels.map(String).join(', ')} mono />
        )}
        {channelId && <FieldRow label="channel" value={channelId} mono />}
        {typeof delivered === 'boolean' && (
          <FieldRow
            label="status"
            value={
              <span style={{ color: delivered ? 'var(--construct-status-success)' : 'var(--pc-text-muted)' }}>
                {delivered ? 'Delivered' : 'Not delivered'}
              </span>
            }
          />
        )}
      </div>
    </Card>
  );
}

function EmailPanel({ input, output }: { input: Json; output: Json }) {
  const to = asArray(input.to) ?? (asString(input.to) ? [asString(input.to)] : []);
  const cc = asArray(input.cc);
  const bcc = asArray(input.bcc);
  const subject = asString(input.subject);
  const from = asString(input.from);
  const bodyPreview = asString(input.body_preview);
  const bodyLength = asNumber(input.body_length);
  const dryRun = asBool(input.dry_run);
  const delivered = asBool(output.delivered);

  return (
    <>
      <Card>
        <SectionLabel>Email</SectionLabel>
        {from && <FieldRow label="from" value={from} mono />}
        {to.length > 0 && <FieldRow label="to" value={to.map(String).join(', ')} mono />}
        {cc && cc.length > 0 && <FieldRow label="cc" value={cc.map(String).join(', ')} mono />}
        {bcc && bcc.length > 0 && <FieldRow label="bcc" value={bcc.map(String).join(', ')} mono />}
        {subject && <FieldRow label="subject" value={subject} />}
        {bodyLength != null && <FieldRow label="body" value={`${bodyLength} chars`} />}
        <div className="mt-1.5">
          <span
            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold"
            style={{
              background: dryRun
                ? 'color-mix(in srgb, var(--construct-status-warning) 16%, transparent)'
                : delivered
                  ? 'color-mix(in srgb, var(--construct-status-success) 16%, transparent)'
                  : 'color-mix(in srgb, var(--construct-status-danger) 16%, transparent)',
              color: dryRun
                ? 'var(--construct-status-warning)'
                : delivered
                  ? 'var(--construct-status-success)'
                  : 'var(--construct-status-danger)',
            }}
          >
            {dryRun ? 'Dry-run (not delivered)' : delivered ? 'Delivered' : 'Not delivered'}
          </span>
        </div>
      </Card>
      {bodyPreview && (
        <Card>
          <SectionLabel>Body preview</SectionLabel>
          <CodeBlock text={bodyPreview} />
        </Card>
      )}
    </>
  );
}

function ImagePanel({ input, output }: { input: Json; output: Json }) {
  const prompt = asString(input.prompt);
  const count = asNumber(input.count);
  const model = asString(input.model);
  const dryRun = asBool(input.dry_run);
  const imagesGenerated = asNumber(output.images_generated);
  const artifactKrefs = asArray(output.artifact_krefs);

  return (
    <>
      <Card>
        <SectionLabel>Image generation</SectionLabel>
        {model && <FieldRow label="model" value={model} mono />}
        {count != null && <FieldRow label="count" value={String(count)} />}
        {dryRun && <FieldRow label="dry_run" value="yes" />}
        {prompt && (
          <div className="mt-1.5">
            <SectionLabel>Prompt</SectionLabel>
            <CodeBlock text={prompt} maxHeight="6rem" />
          </div>
        )}
      </Card>
      <Card>
        <SectionLabel>Result</SectionLabel>
        {imagesGenerated != null && (
          <FieldRow label="generated" value={String(imagesGenerated)} />
        )}
        {artifactKrefs && artifactKrefs.length > 0 ? (
          <div className="mt-1">
            <SectionLabel>Artifact krefs</SectionLabel>
            {artifactKrefs.map((k, i) => (
              <div key={i} className="font-mono text-[10px] break-all" style={{ color: 'var(--pc-text-secondary)' }}>
                {String(k)}
              </div>
            ))}
          </div>
        ) : null}
      </Card>
    </>
  );
}

function ConditionalPanel({ input, output }: { input: Json; output: Json }) {
  const branchCount = asNumber(input.branch_count);
  const matchedIndex = asNumber(input.matched_branch_index);
  const matchedCondition = asString(input.matched_condition);
  const matchedValueExpr = asString(input.matched_value_expr);
  const matchedGoto = asString(output.matched_goto);
  const emittedValue = output.value;

  return (
    <Card>
      <SectionLabel>Conditional</SectionLabel>
      {branchCount != null && <FieldRow label="branches" value={String(branchCount)} />}
      {matchedIndex != null ? (
        <>
          <FieldRow
            label="matched"
            value={
              <span style={{ color: 'var(--construct-status-success)' }}>
                branch {matchedIndex} {matchedCondition ? `— ${matchedCondition}` : ''}
              </span>
            }
            mono
          />
          {matchedGoto && <FieldRow label="goto" value={matchedGoto} mono />}
          {matchedValueExpr && <FieldRow label="value_expr" value={matchedValueExpr} mono />}
          {emittedValue !== undefined && (
            <FieldRow
              label="emitted"
              value={typeof emittedValue === 'string' ? emittedValue : JSON.stringify(emittedValue)}
              mono
            />
          )}
        </>
      ) : (
        <div className="text-[11px]" style={{ color: 'var(--pc-text-muted)' }}>
          No branch matched (default fall-through).
        </div>
      )}
    </Card>
  );
}

function ForEachPanel({ input, output }: { input: Json; output: Json }) {
  const variable = asString(input.variable);
  const itemsCount = asNumber(input.items_count);
  const itemsPreview = asArray(input.items_preview);
  const iterationsCompleted = asNumber(output.iterations_completed);
  const cancelledAfter = asNumber(output.cancelled_after_iteration);

  return (
    <Card>
      <SectionLabel>For-each</SectionLabel>
      {variable && <FieldRow label="variable" value={variable} mono />}
      {itemsCount != null && <FieldRow label="items" value={String(itemsCount)} />}
      {itemsPreview && itemsPreview.length > 0 && (
        <div className="mt-1">
          <SectionLabel>First items</SectionLabel>
          <CodeBlock text={itemsPreview.map((i) => String(i)).join('\n')} maxHeight="5rem" />
        </div>
      )}
      <div className="mt-1.5">
        {iterationsCompleted != null && (
          <FieldRow label="completed" value={`${iterationsCompleted}${itemsCount != null ? ` / ${itemsCount}` : ''}`} />
        )}
        {cancelledAfter != null && (
          <FieldRow
            label="cancelled"
            value={
              <span style={{ color: 'var(--construct-status-warning)' }}>
                after iteration {cancelledAfter}
              </span>
            }
          />
        )}
      </div>
    </Card>
  );
}

function GotoPanel({ input }: { input: Json }) {
  const target = asString(input.target);
  const maxIterations = asNumber(input.max_iterations);
  const currentIteration = asNumber(input.current_iteration);
  const condition = asString(input.condition);

  return (
    <Card>
      <SectionLabel>Goto</SectionLabel>
      {target && <FieldRow label="target" value={target} mono />}
      {currentIteration != null && (
        <FieldRow
          label="iteration"
          value={`${currentIteration}${maxIterations != null ? ` / ${maxIterations}` : ''}`}
        />
      )}
      {condition && <FieldRow label="condition" value={condition} mono />}
    </Card>
  );
}

function TagPanel({ input, output }: { input: Json; output: Json }) {
  const kref = asString(input.kref);
  const tag = asString(input.tag);
  const previousTag = asString(input.previous_tag) ?? asString(output.previous_tag);
  const tagged = asBool(output.tagged);

  return (
    <Card>
      <SectionLabel>Tag</SectionLabel>
      {kref && <FieldRow label="kref" value={kref} mono />}
      {tag && <FieldRow label="tag" value={tag} mono />}
      {previousTag && <FieldRow label="previous" value={previousTag} mono />}
      {typeof tagged === 'boolean' && (
        <FieldRow
          label="status"
          value={
            <span style={{ color: tagged ? 'var(--construct-status-success)' : 'var(--pc-text-muted)' }}>
              {tagged ? 'Tagged' : 'Not tagged'}
            </span>
          }
        />
      )}
    </Card>
  );
}

function DeprecatePanel({ input, output }: { input: Json; output: Json }) {
  const kref = asString(input.kref);
  const reason = asString(input.reason);
  const deprecatedAt = asString(output.deprecated_at);

  return (
    <Card>
      <SectionLabel>Deprecate</SectionLabel>
      {kref && <FieldRow label="kref" value={kref} mono />}
      {reason && <FieldRow label="reason" value={reason} />}
      {deprecatedAt && <FieldRow label="at" value={deprecatedAt} mono />}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Main switch
// ---------------------------------------------------------------------------

export interface StepDetailPanelProps {
  /** Step type from the YAML — `shell`, `resolve`, `output`, etc. */
  stepType: string;
  /** Run-time step info — input_data, output_data, output_preview, error, status. */
  stepInfo: StepRunInfo;
}

/** Truncation banner: shown when memory.py marked the persisted blob as truncated. */
function TruncationBanner() {
  return (
    <div
      className="rounded-lg border px-2.5 py-1.5 flex items-start gap-1.5 text-[10px]"
      style={{
        borderColor: 'var(--construct-status-warning)',
        background: 'color-mix(in srgb, var(--construct-status-warning) 12%, transparent)',
        color: 'var(--construct-status-warning)',
      }}
    >
      <AlertTriangle className="h-3 w-3 shrink-0 mt-0.5" />
      <span>
        Some fields truncated for persistence — full content is available in the daemon logs.
      </span>
    </div>
  );
}

export default function StepDetailPanel({ stepType, stepInfo }: StepDetailPanelProps) {
  const input: Json = stepInfo.input_data ?? {};
  const output: Json = stepInfo.output_data ?? {};
  const error = stepInfo.error;
  const status = stepInfo.status;
  const outputPreview = stepInfo.output_preview;

  const hasInput = Object.keys(input).length > 0;
  const hasOutput = Object.keys(output).length > 0;
  const truncated =
    asBool(input._truncated) === true || asBool(output._truncated) === true;

  // Pre-PR-#220 runs have neither input_data nor output_data captured.
  // Show a small explanatory notice instead of an empty panel.
  if (!hasInput && !hasOutput && !error && !outputPreview) {
    return (
      <div className="flex flex-col gap-3">
        <Card>
          <StatusBadge status={status} />
        </Card>
        <div
          className="rounded-lg border px-2.5 py-2 text-[10px]"
          style={{
            borderColor: 'var(--pc-border)',
            background: 'var(--pc-bg-elevated)',
            color: 'var(--pc-text-faint)',
          }}
        >
          Detail capture not available for this run (pre-#220). Newer runs of this
          workflow will show inputs and outputs here.
        </div>
      </div>
    );
  }

  const t = (stepType || '').toLowerCase();

  let body: React.ReactNode;
  switch (t) {
    case 'resolve':
      body = <ResolvePanel input={input} output={output} status={status} />;
      break;
    case 'shell':
      body = <ShellPanel input={input} output={output} error={error} />;
      break;
    case 'python':
      body = <PythonPanel input={input} output={output} error={error} />;
      break;
    case 'output':
      body = <OutputPanel input={input} output={output} />;
      break;
    case 'notify':
      body = <NotifyPanel input={input} output={output} />;
      break;
    case 'email':
      body = <EmailPanel input={input} output={output} />;
      break;
    case 'image':
      body = <ImagePanel input={input} output={output} />;
      break;
    case 'conditional':
      body = <ConditionalPanel input={input} output={output} />;
      break;
    case 'for_each':
      body = <ForEachPanel input={input} output={output} />;
      break;
    case 'goto':
      body = <GotoPanel input={input} />;
      break;
    case 'tag':
      body = <TagPanel input={input} output={output} />;
      break;
    case 'deprecate':
      body = <DeprecatePanel input={input} output={output} />;
      break;
    default:
      // Unknown / non-mapped step type — fall back to a generic JSON view so
      // the user still gets some signal instead of a dead end.
      body = (
        <>
          {hasInput && (
            <Card>
              <SectionLabel>Inputs</SectionLabel>
              <CodeBlock text={JSON.stringify(input, null, 2)} />
            </Card>
          )}
          {hasOutput && (
            <Card>
              <SectionLabel>Outputs</SectionLabel>
              <CodeBlock text={JSON.stringify(output, null, 2)} />
            </Card>
          )}
        </>
      );
  }

  return (
    <div className="flex flex-col gap-3">
      <Card>
        <StatusBadge status={status} />
      </Card>
      {truncated && <TruncationBanner />}
      {body}
      {error && t !== 'shell' && t !== 'python' && (
        <Card>
          <SectionLabel>Error</SectionLabel>
          <div className="text-[10px] rounded p-2"
            style={{ background: 'var(--pc-bg-base)', color: 'var(--construct-status-danger)' }}>
            {error}
          </div>
        </Card>
      )}
      {outputPreview && t !== 'shell' && t !== 'python' && t !== 'output' && (
        <Card>
          <SectionLabel>Output preview</SectionLabel>
          <CodeBlock text={outputPreview} />
        </Card>
      )}
    </div>
  );
}
