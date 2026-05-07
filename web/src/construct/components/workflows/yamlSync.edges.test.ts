/**
 * Edge inference tests for tasksToFlow().
 *
 * These exercise the three sources tasksToFlow consults when building
 * the Reactflow edge list:
 *   1. depends_on (explicit)
 *   2. parallel.steps (parent → children)
 *   3. ${step_id.<field>} interpolations in text fields
 *
 * Plus the dedup contract: if two passes both want to add the same
 * (source, target) edge, only one is emitted.
 *
 * Run via:  npx tsx --test src/construct/components/workflows/yamlSync.edges.test.ts
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { flowToTasks, parseWorkflowYaml, tasksToFlow, tasksToYaml } from './yamlSync';
import type { TaskNodeData } from './yamlSync';
import type { Node } from '@xyflow/react';

function edgePairs(edges: { source: string; target: string }[]): Set<string> {
  return new Set(edges.map((e) => `${e.source}->${e.target}`));
}

test('parallel.steps emits parent → child edges', () => {
  const yaml = `
steps:
  - id: parallel_research
    type: parallel
    parallel:
      steps: [research_a, research_b]
      join: all
  - id: research_a
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Research A"
  - id: research_b
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Research B"
`;
  const tasks = parseWorkflowYaml(yaml);
  const { edges } = tasksToFlow(tasks);
  const pairs = edgePairs(edges);
  assert.ok(
    pairs.has('parallel_research->research_a'),
    'expected parallel_research → research_a edge',
  );
  assert.ok(
    pairs.has('parallel_research->research_b'),
    'expected parallel_research → research_b edge',
  );
});

test('${step.output} interpolation in prompt emits a referenced → referencing edge', () => {
  const yaml = `
steps:
  - id: research_construct
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Research Construct"
  - id: research_simai
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Research SimAI"
  - id: synthesize
    type: agent
    agent:
      agent_type: claude
      role: summarizer
      prompt: |
        Construct: \${research_construct.output}
        SimAI: \${research_simai.output}
`;
  const tasks = parseWorkflowYaml(yaml);
  const { edges } = tasksToFlow(tasks);
  const pairs = edgePairs(edges);
  assert.ok(
    pairs.has('research_construct->synthesize'),
    'expected research_construct → synthesize edge',
  );
  assert.ok(
    pairs.has('research_simai->synthesize'),
    'expected research_simai → synthesize edge',
  );
});

test('${step.output} in output template emits an edge', () => {
  const yaml = `
steps:
  - id: synthesize
    type: agent
    agent:
      agent_type: claude
      role: summarizer
      prompt: "Summarize."
  - id: final_output
    type: output
    output:
      format: markdown
      template: |
        # Report
        \${synthesize.output}
`;
  const tasks = parseWorkflowYaml(yaml);
  const { edges } = tasksToFlow(tasks);
  const pairs = edgePairs(edges);
  assert.ok(
    pairs.has('synthesize->final_output'),
    'expected synthesize → final_output edge from template interpolation',
  );
});

test('explicit depends_on and ${step.output} for the same source dedup to one edge', () => {
  const yaml = `
steps:
  - id: step_a
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Do A."
  - id: step_b
    type: agent
    depends_on: [step_a]
    agent:
      agent_type: claude
      role: summarizer
      prompt: "Use \${step_a.output}"
`;
  const tasks = parseWorkflowYaml(yaml);
  const { edges } = tasksToFlow(tasks);
  const matches = edges.filter((e) => e.source === 'step_a' && e.target === 'step_b');
  assert.equal(matches.length, 1, `expected exactly one step_a → step_b edge, got ${matches.length}`);
});

test('${input.X} / ${trigger.X} / ${env.X} are skipped', () => {
  const yaml = `
steps:
  - id: only_step
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "User said \${input.user_query}, env \${env.OPENAI_KEY}, fired by \${trigger.payload}"
`;
  const tasks = parseWorkflowYaml(yaml);
  const { edges } = tasksToFlow(tasks);
  // The only_step shouldn't reference itself or any non-existent step.
  assert.equal(edges.length, 0, 'expected zero edges for input/trigger/env-only references');
});

test('full architect example: 5 steps, expected edge set', () => {
  const yaml = `
steps:
  - id: parallel_research
    type: parallel
    parallel:
      steps: [research_construct, research_simai]
      join: all
  - id: research_construct
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Research Construct."
  - id: research_simai
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Research SimAI."
  - id: synthesize_report
    type: agent
    agent:
      agent_type: claude
      role: summarizer
      prompt: |
        Construct research:
        \${research_construct.output}
        SimAI research:
        \${research_simai.output}
  - id: final_output
    type: output
    output:
      format: markdown
      template: |
        # Construct vs SimAI Research Report
        \${synthesize_report.output}
`;
  const tasks = parseWorkflowYaml(yaml);
  const { edges } = tasksToFlow(tasks);
  const pairs = edgePairs(edges);
  // Note: edges follow the depends_on direction convention
  // (source = referenced/parent, target = referencing/child).
  const expected = [
    'parallel_research->research_construct',
    'parallel_research->research_simai',
    'research_construct->synthesize_report',
    'research_simai->synthesize_report',
    'synthesize_report->final_output',
  ];
  for (const p of expected) {
    assert.ok(pairs.has(p), `missing expected edge ${p}; got ${[...pairs].join(', ')}`);
  }
  assert.equal(edges.length, expected.length, `expected ${expected.length} edges, got ${edges.length}`);
});

test('agent.template round-trips through parse → flow → emit', () => {
  const yaml = `
steps:
  - id: research_step
    type: agent
    agent:
      agent_type: claude
      role: researcher
      template: construct-vs-simai-researcher
      prompt: "Research the topic."
`;
  const tasks = parseWorkflowYaml(yaml);
  // Parse routes agent.template → TaskDefinition.template (NOT assign).
  assert.equal(tasks[0]!.template, 'construct-vs-simai-researcher');
  assert.equal(tasks[0]!.assign, undefined);

  // tasksToFlow surfaces it on TaskNodeData.template for the chip.
  const { nodes } = tasksToFlow(tasks);
  const data = nodes[0]!.data as TaskNodeData;
  assert.equal(data.template, 'construct-vs-simai-researcher');
  assert.equal(data.assign, '');

  // flowToTasks → tasksToYaml emits agent.template back into YAML.
  const roundTripped = tasksToYaml(flowToTasks(nodes as Node<TaskNodeData>[], []));
  assert.match(
    roundTripped,
    /agent:[\s\S]*?template: construct-vs-simai-researcher/,
    'expected agent.template to be re-emitted on round-trip',
  );
});
