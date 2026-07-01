/**
 * Tests for agent.mcp_servers round-tripping through the Workflow Editor's
 * YAML<->flow pipeline (parseWorkflowYaml, tasksToFlow, flowToTasks,
 * tasksToYaml). Companion to operator-mcp's PR #559, which added the
 * `agent.mcp_servers: [name, ...]` step field on the Python execution side —
 * this only matters if the editor round-trips it without silently dropping
 * it, which is the exact bug pattern `findIgnoredConfigBlocks` was added
 * for previously (see yamlSync.test.ts), but for a field inside a typed
 * `agent:` block rather than a whole unrecognized `config:` block.
 *
 * Run: npx tsx --test src/revka/components/workflows/__tests__/yamlSync.mcpServers.test.ts
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { flowToTasks, parseWorkflowYaml, tasksToFlow, tasksToYaml, type TaskDefinition } from '../yamlSync';

test('parseWorkflowYaml reads agent.mcp_servers into TaskDefinition.mcp_servers', () => {
  const yaml = `
steps:
  - id: render
    type: agent
    agent:
      prompt: "Render the video."
      mcp_servers: [OpenCrab, LocalTool]
`;
  const tasks = parseWorkflowYaml(yaml);
  assert.deepEqual(tasks[0]!.mcp_servers, ['OpenCrab', 'LocalTool']);
});

test('parseWorkflowYaml leaves mcp_servers undefined when absent (no other agent fields need it)', () => {
  const yaml = `
steps:
  - id: render
    type: agent
    agent:
      prompt: "Render the video."
`;
  const tasks = parseWorkflowYaml(yaml);
  assert.equal(tasks[0]!.mcp_servers, undefined);
});

test('tasksToFlow carries mcp_servers into TaskNodeData.mcpServers', () => {
  const task: TaskDefinition = {
    id: 'render',
    name: 'render',
    description: '',
    type: 'agent',
    agent_hints: [],
    skills: [],
    depends_on: [],
    prompt: 'Render the video.',
    mcp_servers: ['OpenCrab'],
  };
  const { nodes } = tasksToFlow([task]);
  assert.deepEqual(nodes[0]!.data.mcpServers, ['OpenCrab']);
});

test('tasksToFlow defaults mcpServers to [] when the task has none', () => {
  const task: TaskDefinition = {
    id: 'render',
    name: 'render',
    description: '',
    type: 'agent',
    agent_hints: [],
    skills: [],
    depends_on: [],
    prompt: 'Render the video.',
  };
  const { nodes } = tasksToFlow([task]);
  assert.deepEqual(nodes[0]!.data.mcpServers, []);
});

test('flowToTasks carries mcpServers back into TaskDefinition.mcp_servers', () => {
  const yaml = `
steps:
  - id: render
    type: agent
    agent:
      prompt: "Render the video."
      mcp_servers: [OpenCrab]
`;
  const { nodes, edges } = tasksToFlow(parseWorkflowYaml(yaml));
  const tasks = flowToTasks(nodes, edges);
  assert.deepEqual(tasks[0]!.mcp_servers, ['OpenCrab']);
});

test('tasksToYaml emits mcp_servers under the agent: block', () => {
  const task: TaskDefinition = {
    id: 'render',
    name: 'render',
    description: '',
    type: 'agent',
    agent_hints: [],
    skills: [],
    depends_on: [],
    prompt: 'Render the video.',
    mcp_servers: ['OpenCrab', 'LocalTool'],
  };
  const emitted = tasksToYaml([task]);
  assert.match(emitted, /mcp_servers: \[OpenCrab, LocalTool\]/);
});

test('tasksToYaml still opens an agent: block for a step with ONLY mcp_servers set', () => {
  // Regression guard: tasksToYaml's agent-block-open condition previously
  // checked agent_type/role/prompt/template/auth/agent_required_tools only.
  // A step whose sole agent field is mcp_servers must not be silently
  // dropped by that gate.
  const task: TaskDefinition = {
    id: 'render',
    name: 'render',
    description: '',
    type: 'agent',
    agent_hints: [],
    skills: [],
    depends_on: [],
    mcp_servers: ['OpenCrab'],
  };
  const emitted = tasksToYaml([task]);
  assert.match(emitted, /agent:\n(\s+.*\n)*\s+mcp_servers: \[OpenCrab\]/);
});

test('full round trip: YAML -> tasks -> flow -> tasks -> YAML preserves mcp_servers', () => {
  const yaml = `
steps:
  - id: render
    type: agent
    agent:
      agent_type: claude
      prompt: "Render the video."
      mcp_servers: [OpenCrab]
`;
  const firstPass = parseWorkflowYaml(yaml);
  const { nodes, edges } = tasksToFlow(firstPass);
  const roundTripped = flowToTasks(nodes, edges);
  const reEmitted = tasksToYaml(roundTripped);
  const secondPass = parseWorkflowYaml(reEmitted);
  assert.deepEqual(secondPass[0]!.mcp_servers, ['OpenCrab']);
});
