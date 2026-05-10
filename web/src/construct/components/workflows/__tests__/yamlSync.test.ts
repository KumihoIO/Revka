/**
 * Tests for parseWorkflowYaml — the YAML→tasks pipeline that powers the
 * editor's "Import YAML" path. Mirrors the parallel test file under
 * `web/src/components/workflows/__tests__/yamlSync.test.ts`; both yamlSync
 * modules are kept in sync, so both get covered by symmetric tests.
 *
 * Run: npx tsx --test src/construct/components/workflows/__tests__/yamlSync.test.ts
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { parseWorkflowYaml, tasksToFlow, flowToTasks, tasksToYaml } from '../yamlSync';

test('canonical conditional.branches populates flat fields + edges', () => {
  const yaml = `
steps:
  - id: gate-1
    type: conditional
    conditional:
      branches:
        - condition: "\${research.score} > 0.5"
          goto: publish
          value: "high"
        - condition: "default"
          goto: archive
          value: "low"
  - id: publish
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Publish"
  - id: archive
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Archive"
`;
  const tasks = parseWorkflowYaml(yaml);
  const gate = tasks.find((t) => t.id === 'gate-1');
  assert.ok(gate, 'gate-1 task exists');
  assert.equal(gate!.condition, '${research.score} > 0.5');
  assert.equal(gate!.on_true, 'publish');
  assert.equal(gate!.on_false, 'archive');
  assert.equal(gate!.on_true_value, 'high');
  assert.equal(gate!.on_false_value, 'low');

  const { edges } = tasksToFlow(tasks);
  const pairs = new Set(edges.map((e) => `${e.source}->${e.target}`));
  assert.ok(pairs.has('gate-1->publish'), 'gate→true edge present');
  assert.ok(pairs.has('gate-1->archive'), 'gate→false edge present');
});

test('legacy flat condition + on_true + on_false also populates fields', () => {
  const yaml = `
steps:
  - id: gate
    type: conditional
    condition: "\${score} > 0.5"
    on_true: pub
    on_false: arc
  - id: pub
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Publish"
  - id: arc
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Archive"
`;
  const tasks = parseWorkflowYaml(yaml);
  const gate = tasks.find((t) => t.id === 'gate')!;
  assert.equal(gate.condition, '${score} > 0.5');
  assert.equal(gate.on_true, 'pub');
  assert.equal(gate.on_false, 'arc');

  const { edges } = tasksToFlow(tasks);
  const pairs = new Set(edges.map((e) => `${e.source}->${e.target}`));
  assert.ok(pairs.has('gate->pub'));
  assert.ok(pairs.has('gate->arc'));
});

test('multi-line prompt: | scalar is preserved intact', () => {
  const yaml = `
steps:
  - id: writer
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: |
        Line one.
        Line two.
        Line three with \${ref.output}
`;
  const tasks = parseWorkflowYaml(yaml);
  const writer = tasks[0]!;
  assert.equal(
    writer.prompt,
    'Line one.\nLine two.\nLine three with ${ref.output}\n',
  );
});

test('hyphenated step IDs survive parse and edge build', () => {
  const yaml = `
steps:
  - id: zeroclaw-resolve
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Resolve"
  - id: zeroclaw-publish
    type: agent
    depends_on: [zeroclaw-resolve]
    agent:
      agent_type: claude
      role: coder
      prompt: "Publish from \${zeroclaw-resolve.output}"
`;
  const tasks = parseWorkflowYaml(yaml);
  assert.equal(tasks.length, 2);
  assert.equal(tasks[0]!.id, 'zeroclaw-resolve');
  assert.equal(tasks[1]!.id, 'zeroclaw-publish');
  assert.deepEqual(tasks[1]!.depends_on, ['zeroclaw-resolve']);

  const { edges } = tasksToFlow(tasks);
  const pairs = new Set(edges.map((e) => `${e.source}->${e.target}`));
  assert.ok(pairs.has('zeroclaw-resolve->zeroclaw-publish'));
});

test('4-space and mixed indentation parse identically', () => {
  const yaml = `
steps:
    - id: a
      type: agent
      agent:
          agent_type: claude
          role: coder
          prompt: "Hello"
    - id: b
      type: agent
      depends_on: [a]
      agent:
          agent_type: claude
          role: coder
          prompt: "World"
`;
  const tasks = parseWorkflowYaml(yaml);
  assert.equal(tasks.length, 2);
  assert.equal(tasks[0]!.prompt, 'Hello');
  assert.equal(tasks[1]!.prompt, 'World');
  assert.deepEqual(tasks[1]!.depends_on, ['a']);
});

test('deeply nested parallel containing for_each containing agent', () => {
  const yaml = `
steps:
  - id: par
    type: parallel
    parallel:
      steps: [loop_a, loop_b]
      join: all
  - id: loop_a
    type: for_each
    for_each:
      items: ["x", "y"]
      variable: item
      steps: [worker_a]
  - id: loop_b
    type: for_each
    for_each:
      items: ["1", "2"]
      variable: item
      steps: [worker_b]
  - id: worker_a
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Work A on \${item}"
  - id: worker_b
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Work B on \${item}"
`;
  const tasks = parseWorkflowYaml(yaml);
  assert.equal(tasks.length, 5);
  const par = tasks.find((t) => t.id === 'par')!;
  assert.deepEqual(par.parallel_steps, ['loop_a', 'loop_b']);
  const loopA = tasks.find((t) => t.id === 'loop_a')!;
  assert.deepEqual(loopA.for_each_items, ['x', 'y']);
  assert.deepEqual(loopA.for_each_steps, ['worker_a']);
  const workerA = tasks.find((t) => t.id === 'worker_a')!;
  assert.equal(workerA.prompt, 'Work A on ${item}');
});

test('round-trip: parse then emit then parse yields equivalent tasks', () => {
  const yaml = `
steps:
  - id: a
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Hello"
  - id: b
    type: agent
    depends_on: [a]
    agent:
      agent_type: claude
      role: coder
      prompt: "Use \${a.output}"
`;
  const tasks1 = parseWorkflowYaml(yaml);
  const { nodes, edges } = tasksToFlow(tasks1);
  const round = flowToTasks(nodes, edges);
  const yaml2 = tasksToYaml(round);
  const tasks2 = parseWorkflowYaml(yaml2);
  assert.equal(tasks2.length, tasks1.length);
  assert.equal(tasks2[0]!.id, tasks1[0]!.id);
  assert.equal(tasks2[0]!.prompt, tasks1[0]!.prompt);
  assert.equal(tasks2[1]!.id, tasks1[1]!.id);
  assert.deepEqual(tasks2[1]!.depends_on, ['a']);
});

test('malformed YAML throws an Error with a useful message', () => {
  const bad = `
steps:
  - id: a
    type: agent
    agent:
      prompt: "unterminated string
`;
  assert.throws(
    () => parseWorkflowYaml(bad),
    (err) => {
      assert.ok(err instanceof Error, 'is Error');
      assert.ok(err.message.length > 0, 'has message');
      return true;
    },
  );
});

test('full multi-step workflow: nodes populated and edges reconstruct', () => {
  const yaml = `
name: blog-writer
version: "1.0"
description: Multi-stage blog writer.

steps:
  - id: research-topic
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: |
        Research the topic deeply.
        Produce \${research-topic.output}.

  - id: score-gate
    type: conditional
    depends_on: [research-topic]
    conditional:
      branches:
        - condition: "\${research-topic.score} >= 0.6"
          goto: parallel-drafts
          value: "ok"
        - condition: "default"
          goto: abort
          value: "low"

  - id: parallel-drafts
    type: parallel
    parallel:
      steps: [draft-short, draft-long]
      join: all

  - id: draft-short
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Short draft"

  - id: draft-long
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Long draft"

  - id: review-loop
    type: for_each
    depends_on: [parallel-drafts]
    for_each:
      items: ["short", "long"]
      variable: variant
      steps: [reviewer]

  - id: reviewer
    type: agent
    agent:
      agent_type: claude
      role: reviewer
      prompt: "Review \${variant}"

  - id: final-output
    type: output
    depends_on: [review-loop]
    output:
      format: markdown
      template: |
        # Final
        \${draft-short.output}
        \${draft-long.output}

  - id: abort
    type: agent
    agent:
      agent_type: claude
      role: coder
      prompt: "Abort"
`;
  const tasks = parseWorkflowYaml(yaml);
  assert.equal(tasks.length, 9);

  const gate = tasks.find((t) => t.id === 'score-gate')!;
  assert.equal(gate.condition, '${research-topic.score} >= 0.6');
  assert.equal(gate.on_true, 'parallel-drafts');
  assert.equal(gate.on_false, 'abort');

  assert.equal(tasks.find((t) => t.id === 'parallel-drafts')!.type, 'parallel');
  assert.deepEqual(
    tasks.find((t) => t.id === 'parallel-drafts')!.parallel_steps,
    ['draft-short', 'draft-long'],
  );
  assert.equal(tasks.find((t) => t.id === 'review-loop')!.type, 'for_each');
  assert.deepEqual(
    tasks.find((t) => t.id === 'review-loop')!.for_each_steps,
    ['reviewer'],
  );

  const { edges } = tasksToFlow(tasks);
  const pairs = new Set(edges.map((e) => `${e.source}->${e.target}`));
  assert.ok(pairs.has('research-topic->score-gate'), 'depends_on edge');
  assert.ok(pairs.has('score-gate->parallel-drafts'), 'gate true edge');
  assert.ok(pairs.has('score-gate->abort'), 'gate false edge');
  assert.ok(pairs.has('parallel-drafts->draft-short'), 'parallel→child');
  assert.ok(pairs.has('parallel-drafts->draft-long'), 'parallel→child');
  assert.ok(pairs.has('draft-short->final-output'), 'output template ref');
  assert.ok(pairs.has('draft-long->final-output'), 'output template ref');
});
