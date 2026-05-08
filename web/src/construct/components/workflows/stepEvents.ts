/**
 * stepEvents.ts — Typed CustomEvent bus for opening the step palette and
 * inserting nodes into the canvas. Every chooser surface emits this event;
 * the editor canvas listens once and is the only writer of canvas state.
 */

export interface AddStepDetail {
  /** StepType value from stepRegistry (e.g. "agent", "shell", "tag") */
  type: string;
  /** Optional canvas (flow) coordinates. Defaults to viewport center. */
  position?: { x: number; y: number };
  /** When emitted from a noodle-drop, the source node + handle to wire from. */
  source?: { taskId: string; handle?: 'true' | 'false' | null };
  /** When emitted from a reverse-noodle-drop (target handle dragged onto
   *  empty canvas), the target node id to wire INTO. The new node becomes
   *  the upstream source: `new node → target node`. */
  target?: { taskId: string };
  /** Optional preset skill name to assign to the new node. */
  presetSkill?: string;
}

export const ADD_STEP_EVENT = 'construct:add-step';

/** Emit a typed CustomEvent that the editor listens for to insert a step. */
export function emitAddStep(detail: AddStepDetail): void {
  window.dispatchEvent(new CustomEvent<AddStepDetail>(ADD_STEP_EVENT, { detail }));
}

// ---------------------------------------------------------------------------
// Open agent-picker — fired by the canvas TaskNode badge (and any other
// surface that wants the shared AgentPicker anchored to itself). The editor
// owns the picker mount and is the single listener.
// ---------------------------------------------------------------------------

export interface OpenAgentPickerDetail {
  /** Node id of the step to assign. The editor uses this to write `assign`. */
  taskId: string;
  /** Bounding rect of the trigger element — used to anchor the popover. */
  anchorRect: DOMRect;
}

export const OPEN_AGENT_PICKER_EVENT = 'construct:open-agent-picker';

export function emitOpenAgentPicker(detail: OpenAgentPickerDetail): void {
  window.dispatchEvent(
    new CustomEvent<OpenAgentPickerDetail>(OPEN_AGENT_PICKER_EVENT, { detail }),
  );
}

declare global {
  interface WindowEventMap {
    'construct:add-step': CustomEvent<AddStepDetail>;
    'construct:open-agent-picker': CustomEvent<OpenAgentPickerDetail>;
  }
}
