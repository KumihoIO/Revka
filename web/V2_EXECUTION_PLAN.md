# V2 UI Execution Plan

V2 is the intended replacement for V1 once parity and usability are strong enough. The current visual language is good enough to keep; the main work is structure, parity, and consistency.

## Goals

- Ship V2 as the primary Revka UI.
- Preserve the existing V2 color direction.
- Raise V2 to at least V1 parity on critical operator workflows.
- Replace row-heavy page composition with denser operational workspaces.
- Establish one consistent V2-native assistant pattern across pages.

## Feedback Translation

### Keep

- The current V2 palette and overall visual tone.

### Fix Immediately

- Missing explicit light theme support in the V2 shell.
- Oversized left-rail status area.
- Sidebar overflow/scroll behavior.
- Navigation order and IA mismatch.
- Row-heavy layouts across major pages.
- Missing task-detail parity on workflow runs.
- Missing structured config representation.
- Missing V2-native assistant surfaces on most pages.
- Canvas still feeling visually closer to legacy than V2.

## Execution Order

### Phase 1: Shell And IA

- Add an explicit V2 theme switcher with dark, light, and system modes.
- Compress the left-rail runtime status into a denser summary.
- Fix sidebar and shell overflow so navigation and content scroll independently.
- Reorder navigation to:
  - Orchestration: Dashboard, Chat, Workflows, Agents, Canvas, Teams
  - Operations: Assets, Skills, Integrations, Config, Cost
  - Inspection: Logs, Audit, Doctor
- Keep `Runs` and `Memory` reachable by route and deep links while V2 IA converges.

### Phase 2: Workflow Parity

- Rework `Runs` into a denser split workspace.
- Restore task-node detail tabs for summary, output, tools, and transcript.
- Converge `Runs` toward a workflow sub-surface rather than a forever top-level destination.

### Phase 3: Page Layout Normalization

- Replace long stacked row layouts on Agents, Workflows, Assets, and Teams.
- Standardize those pages around:
  - object index
  - focused workspace
  - contextual inspector/actions

### Phase 4: Config And Assistant Surfaces

- Expand Config into dual-mode:
  - structured grouped UI
  - raw TOML editor
- Introduce a reusable V2 assistant rail or dock pattern.
- Apply the assistant pattern to priority pages after Canvas.

### Phase 5: Canvas And Replacement Cutover

- Bring Canvas visuals and controls fully into the V2 design system.
- Remove remaining “parallel migration” framing in the shell once parity is sufficient.
- Promote V2 routes to replace V1 navigation and entry flow.

## First Implementation Slice

This pass will implement:

- repo-stored execution plan
- navigation reorder
- V2 theme switch exposure
- denser sidebar status cluster
- shell overflow fixes
- first runs-page parity improvement via tabbed task detail

## Validation

- `npm run build`
- V2 route smoke pass
- manual checks for sidebar overflow on short and tall viewports
- manual checks for dark/light/system theme switching
- manual checks for run detail tabs and step selection behavior
