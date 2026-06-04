/**
 * Tests for parseWorkflowYaml — the YAML→tasks pipeline that powers the
 * editor's "Import YAML" path. Mirrors the parallel test file under
 * `web/src/components/workflows/__tests__/yamlSync.test.ts`; both yamlSync
 * modules are kept in sync, so both get covered by symmetric tests.
 *
 * Run: npx tsx --test src/revka/components/workflows/__tests__/yamlSync.test.ts
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  findOutputDataRefs,
  flowToTasks,
  hasPersistedTaskPositions,
  parseWorkflowMeta,
  parseWorkflowYaml,
  tasksToFlow,
  tasksToYaml,
  validateAgentOutputContracts,
  type TaskDefinition,
} from '../yamlSync';

test('workflow step positions round-trip through YAML and flow nodes', () => {
  const yaml = `
steps:
  - id: a
    type: agent
    position:
      x: 123.45
      y: -67.89
    agent:
      prompt: "A"
  - id: b
    type: shell
    depends_on: [a]
    position:
      x: 444
      y: 222.125
    shell:
      command: "echo b"
`;

  const tasks = parseWorkflowYaml(yaml);
  assert.equal(hasPersistedTaskPositions(tasks), true);
  assert.deepEqual(tasks[0]!.position, { x: 123.45, y: -67.89 });

  const { nodes, edges } = tasksToFlow(tasks);
  assert.deepEqual(nodes.find((node) => node.id === 'a')!.position, { x: 123.45, y: -67.89 });
  assert.deepEqual(nodes.find((node) => node.id === 'b')!.position, { x: 444, y: 222.125 });

  nodes[0]!.position = { x: 300.333, y: 400.666 };
  const roundTrippedTasks = flowToTasks(nodes, edges);
  assert.deepEqual(roundTrippedTasks[0]!.position, { x: 300.33, y: 400.67 });

  const emitted = tasksToYaml(roundTrippedTasks);
  assert.match(emitted, /position:\n      x: 300\.33\n      y: 400\.67/);
  assert.deepEqual(parseWorkflowYaml(emitted)[0]!.position, { x: 300.33, y: 400.67 });
});

test('workflows without YAML positions are detectable for auto-layout fallback', () => {
  const tasks = parseWorkflowYaml(`
steps:
  - id: a
    type: agent
    agent:
      prompt: "A"
`);
  assert.equal(hasPersistedTaskPositions(tasks), false);
});

test('workflow inputs with blank names are omitted from emitted and parsed metadata', () => {
  const tasks: TaskDefinition[] = [{
    id: 'a',
    name: 'a',
    description: '',
    type: 'agent',
    agent_hints: [],
    skills: [],
    depends_on: [],
    prompt: 'A',
  }];

  const emitted = tasksToYaml(tasks, {
    name: 'blank-inputs',
    version: '1.0',
    description: '',
    tags: [],
    triggers: [],
    inputs: [
      { name: '', type: 'string', required: true, default: 'stale', description: 'stale' },
      { name: 'topic', type: 'string', required: true, default: '', description: 'Topic' },
    ],
    outputs: [],
    defaultCwd: '',
    defaultTimeout: 300,
    maxTotalTime: 3600,
    checkpoint: true,
  });

  assert.doesNotMatch(emitted, /- name:\s*\n/);
  assert.match(emitted, /name: topic/);
  assert.deepEqual(parseWorkflowMeta(emitted).inputs.map((input) => input.name), ['topic']);
});

test('entity trigger name and space filters round-trip through metadata YAML', () => {
  const yaml = `
name: triggered-workflow
version: "1.0"
triggers:
  - on_kind: report
    on_tag: ready
    on_name_pattern: "daily-*"
    on_space: "Revka/Reports"
    input_map:
      report_kref: "\${trigger.entity_kref}"
steps:
  - id: a
    type: agent
    agent:
      prompt: "A"
`;

  const meta = parseWorkflowMeta(yaml);
  assert.equal(meta.triggers.length, 1);
  assert.deepEqual(meta.triggers[0], {
    onKind: 'report',
    onTag: 'ready',
    onNamePattern: 'daily-*',
    onSpace: 'Revka/Reports',
    inputMap: { report_kref: '${trigger.entity_kref}' },
  });

  const emitted = tasksToYaml(parseWorkflowYaml(yaml), meta);
  assert.match(emitted, /on_name_pattern: "daily-\*"/);
  assert.match(emitted, /on_space: Revka\/Reports/);
  assert.equal(parseWorkflowMeta(emitted).triggers[0]!.onSpace, 'Revka/Reports');
});

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
  assert.deepEqual(gate!.conditional_branches, [
    { condition: '${research.score} > 0.5', goto: 'publish', value: 'high' },
    { condition: 'default', goto: 'archive', value: 'low' },
  ]);

  const { edges } = tasksToFlow(tasks);
  const pairs = new Set(edges.map((e) => `${e.source}->${e.target}`));
  assert.ok(pairs.has('gate-1->publish'), 'gate→true edge present');
  assert.ok(pairs.has('gate-1->archive'), 'gate→false edge present');
});

test('multi-branch conditional branches survive parse flow and YAML emit', () => {
  const yaml = `
steps:
  - id: gate
    type: conditional
    conditional:
      branches:
        - condition: "\${score} >= 0.8"
          goto: publish
          value: "high"
        - condition: "\${score} >= 0.5"
          goto: revise
          value: "medium"
        - condition: "default"
          goto: archive
          value: "low"
  - id: publish
    type: agent
    agent:
      prompt: "Publish"
  - id: revise
    type: agent
    agent:
      prompt: "Revise"
  - id: archive
    type: agent
    agent:
      prompt: "Archive"
`;
  const tasks = parseWorkflowYaml(yaml);
  const gate = tasks.find((t) => t.id === 'gate')!;
  assert.deepEqual(gate.conditional_branches, [
    { condition: '${score} >= 0.8', goto: 'publish', value: 'high' },
    { condition: '${score} >= 0.5', goto: 'revise', value: 'medium' },
    { condition: 'default', goto: 'archive', value: 'low' },
  ]);

  const { nodes, edges } = tasksToFlow(tasks);
  const branchEdges = edges
    .filter((edge) => edge.source === 'gate')
    .map((edge) => `${edge.sourceHandle}->${edge.target}`);
  assert.deepEqual(branchEdges, [
    'branch-0->publish',
    'branch-1->revise',
    'branch-2->archive',
  ]);
  assert.equal(nodes.find((node) => node.id === 'gate')!.data.conditionalBranches.length, 3);

  const roundTrippedTasks = flowToTasks(nodes, edges);
  const roundTrippedGate = roundTrippedTasks.find((t) => t.id === 'gate')!;
  assert.deepEqual(roundTrippedGate.conditional_branches, gate.conditional_branches);

  const emitted = tasksToYaml(roundTrippedTasks);
  assert.match(emitted, /condition: "\$\{score\} >= 0\.8"/);
  assert.match(emitted, /condition: "\$\{score\} >= 0\.5"/);
  assert.match(emitted, /condition: default/);
  const reparsed = parseWorkflowYaml(emitted).find((t) => t.id === 'gate')!;
  assert.deepEqual(reparsed.conditional_branches, gate.conditional_branches);
});

test('conditional branch with blank condition persists as default when it has a target', () => {
  const tasks: TaskDefinition[] = [
    {
      id: 'gate',
      name: 'gate',
      description: '',
      type: 'conditional',
      agent_hints: [],
      skills: [],
      depends_on: [],
      conditional_branches: [{ condition: '', goto: 'done' }],
    },
    {
      id: 'done',
      name: 'done',
      description: '',
      type: 'output',
      agent_hints: [],
      skills: [],
      depends_on: [],
    },
  ];

  const emitted = tasksToYaml(tasks);
  assert.match(emitted, /condition: default/);

  const reparsed = parseWorkflowYaml(emitted).find((t) => t.id === 'gate')!;
  assert.deepEqual(reparsed.conditional_branches, [
    { condition: 'default', goto: 'done', value: undefined },
  ]);
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

test('coordination steps round-trip pool agent assignments through UI flow fields', () => {
  const yaml = `
steps:
  - id: chat
    type: group_chat
    group_chat:
      topic: "Review the launch plan"
      participants: [reviewer-template, codex]
      moderator: lead-template
      strategy: round_robin
      max_rounds: 4
      timeout: 90
  - id: supervise
    type: supervisor
    supervisor:
      task: "Coordinate implementation"
      supervisor_type: lead-template
      templates: [reviewer-template, coder-template]
      max_iterations: 4
      timeout: 240
  - id: pass
    type: handoff
    handoff:
      from_step: chat
      to_agent_type: coder-template
      reason: "Implementation is ready"
      task: "Apply the accepted plan"
      timeout: 180
  - id: remote
    type: a2a
    a2a:
      url: "https://agent.example.com"
      skill_id: remote-template
      message: "Run the external check"
      timeout: 120
  - id: reduce
    type: map_reduce
    map_reduce:
      task: "Summarize modules"
      splits: ["api", "web"]
      mapper: researcher-template
      reducer: synth-template
      concurrency: 2
      timeout: 200
`;
  const tasks1 = parseWorkflowYaml(yaml);
  assert.equal(tasks1.length, 5);

  const chat1 = tasks1.find((t) => t.id === 'chat')!;
  assert.deepEqual(chat1.group_chat_participants, ['reviewer-template', 'codex']);
  assert.equal(chat1.group_chat_moderator, 'lead-template');

  const sup1 = tasks1.find((t) => t.id === 'supervise')!;
  assert.equal(sup1.supervisor_type, 'lead-template');
  assert.deepEqual(sup1.supervisor_templates, ['reviewer-template', 'coder-template']);

  const handoff1 = tasks1.find((t) => t.id === 'pass')!;
  assert.equal(handoff1.handoff_to, 'coder-template');

  const mr1 = tasks1.find((t) => t.id === 'reduce')!;
  assert.equal(mr1.map_reduce_mapper, 'researcher-template');
  assert.equal(mr1.map_reduce_reducer, 'synth-template');

  const { nodes, edges } = tasksToFlow(tasks1);
  assert.equal(nodes.find((n) => n.id === 'chat')!.data.groupChatModerator, 'lead-template');
  assert.deepEqual(
    nodes.find((n) => n.id === 'supervise')!.data.supervisorTemplates,
    ['reviewer-template', 'coder-template'],
  );
  assert.equal(nodes.find((n) => n.id === 'pass')!.data.handoffTo, 'coder-template');
  assert.equal(nodes.find((n) => n.id === 'remote')!.data.a2aSkillId, 'remote-template');
  assert.equal(nodes.find((n) => n.id === 'reduce')!.data.mapReduceMapper, 'researcher-template');
  assert.equal(nodes.find((n) => n.id === 'reduce')!.data.mapReduceReducer, 'synth-template');

  const yaml2 = tasksToYaml(flowToTasks(nodes, edges));
  const tasks2 = parseWorkflowYaml(yaml2);
  assert.deepEqual(tasks2.find((t) => t.id === 'chat')!.group_chat_participants, ['reviewer-template', 'codex']);
  assert.equal(tasks2.find((t) => t.id === 'chat')!.group_chat_moderator, 'lead-template');
  assert.equal(tasks2.find((t) => t.id === 'supervise')!.supervisor_type, 'lead-template');
  assert.deepEqual(tasks2.find((t) => t.id === 'supervise')!.supervisor_templates, ['reviewer-template', 'coder-template']);
  assert.equal(tasks2.find((t) => t.id === 'pass')!.handoff_to, 'coder-template');
  assert.equal(tasks2.find((t) => t.id === 'remote')!.a2a_skill_id, 'remote-template');
  assert.equal(tasks2.find((t) => t.id === 'reduce')!.map_reduce_mapper, 'researcher-template');
  assert.equal(tasks2.find((t) => t.id === 'reduce')!.map_reduce_reducer, 'synth-template');
});

test('step config parity fields round-trip through parse, flow, emit, parse', () => {
  const yaml = `
steps:
  - id: agent-full
    type: agent
    agent:
      agent_type: codex
      role: coder
      prompt: "Produce JSON"
      max_turns: 6
      tools: memory
      required_tools: [capture_skill, tag_revision]
      output_fields: [summary, score]
      quality_check:
        enabled: true
        threshold: 0.85
        criteria: [accurate, concise]
        model: judge-model
  - id: approval
    type: human_approval
    human_approval:
      channel: discord
      channel_id: "ops-room"
      message: "Approve?"
      timeout: 7200
      on_reject_goto: fix
      on_reject_max: 2
      approve_keywords: [ship, approve]
      reject_keywords: [block, reject]
  - id: notify
    type: notify
    notify:
      channels: [dashboard, slack]
      channel_id: "alerts"
      title: "Done"
      message: "Workflow completed"
  - id: python
    type: python
    python:
      python: "/opt/python/bin/python3"
      code: "print('ok')"
      timeout: 45
  - id: email
    type: email
    email:
      to: "ops@example.com"
      subject: "Report"
      body: "Plain text"
      body_html: "<b>Report</b>"
      reply_to: "reply@example.com"
      track_secret_env: "TRACKING_SECRET"
      smtp_host: "smtp.example.com"
      smtp_port: 2525
      smtp_tls: false
      smtp_username: "smtp-user"
      smtp_password_env: "SMTP_PASS"
  - id: image
    type: image
    image:
      prompt: "Generate dashboard concept"
      canvas: launch-board
      cwd: "/tmp/work"
      input_images: ["/tmp/ref.png"]
  - id: manus
    type: manus
    manus:
      prompt: "Research competitors"
      enable_skills: [browse, code]
      force_skills: [browse]
      project_id: "project-42"
`;
  const tasks1 = parseWorkflowYaml(yaml);
  const { nodes, edges } = tasksToFlow(tasks1);
  const yaml2 = tasksToYaml(flowToTasks(nodes, edges));
  const tasks2 = parseWorkflowYaml(yaml2);

  const agent = tasks2.find((t) => t.id === 'agent-full')!;
  assert.equal(agent.agent_max_turns, 6);
  assert.equal(agent.agent_tools, 'memory');
  assert.deepEqual(agent.agent_required_tools, ['capture_skill', 'tag_revision']);
  assert.deepEqual(agent.agent_output_fields, ['summary', 'score']);
  assert.equal(agent.agent_quality_enabled, true);
  assert.equal(agent.agent_quality_threshold, 0.85);
  assert.deepEqual(agent.agent_quality_criteria, ['accurate', 'concise']);
  assert.equal(agent.agent_quality_model, 'judge-model');

  const agentopsYaml = `
steps:
  - id: agentops
    type: agent
    agent:
      tools: google_agentops
      required_tools: [google_agents_cli, a2a_discover]
      prompt: "Deploy ADK"
`;
  const agentops = parseWorkflowYaml(tasksToYaml(parseWorkflowYaml(agentopsYaml)))[0]!;
  assert.equal(agentops.agent_tools, 'google_agentops');
  assert.deepEqual(agentops.agent_required_tools, ['google_agents_cli', 'a2a_discover']);

  const approval = tasks2.find((t) => t.id === 'approval')!;
  assert.equal(approval.human_approval_channel, 'discord');
  assert.equal(approval.human_approval_channel_id, 'ops-room');
  assert.equal(approval.human_approval_on_reject_goto, 'fix');
  assert.equal(approval.human_approval_on_reject_max, 2);
  assert.deepEqual(approval.human_approval_approve_keywords, ['ship', 'approve']);
  assert.deepEqual(approval.human_approval_reject_keywords, ['block', 'reject']);

  const notify = tasks2.find((t) => t.id === 'notify')!;
  assert.deepEqual(notify.channels, ['dashboard', 'slack']);
  assert.equal(notify.notify_channel_id, 'alerts');

  const python = tasks2.find((t) => t.id === 'python')!;
  assert.equal(python.python_interpreter, '/opt/python/bin/python3');

  const email = tasks2.find((t) => t.id === 'email')!;
  assert.equal(email.email_body_html, '<b>Report</b>');
  assert.equal(email.email_reply_to, 'reply@example.com');
  assert.equal(email.email_track_secret_env, 'TRACKING_SECRET');
  assert.equal(email.email_smtp_port, 2525);
  assert.equal(email.email_smtp_tls, false);
  assert.equal(email.email_smtp_username, 'smtp-user');
  assert.equal(email.email_smtp_password_env, 'SMTP_PASS');

  const image = tasks2.find((t) => t.id === 'image')!;
  assert.equal(image.image_canvas, 'launch-board');
  assert.equal(image.image_canvas_target, 'launch-board');
  assert.equal(image.image_cwd, '/tmp/work');
  assert.deepEqual(image.image_input_images, ['/tmp/ref.png']);

  const manus = tasks2.find((t) => t.id === 'manus')!;
  assert.deepEqual(manus.manus_enable_skills, ['browse', 'code']);
  assert.deepEqual(manus.manus_force_skills, ['browse']);
  assert.equal(manus.manus_project_id, 'project-42');
});

test('findOutputDataRefs extracts template and expression output_data references', () => {
  const refs = findOutputDataRefs(
    "${final-canon-auditor.output_data.production_ready} ${{ final_canon_auditor.output_data.verdict == 'APPROVED' }}",
  );

  assert.deepEqual(refs, [
    { stepId: 'final-canon-auditor', field: 'production_ready' },
    { stepId: 'final_canon_auditor', field: 'verdict' },
  ]);
});

test('agent output contract warning fires for undeclared downstream output_data field', () => {
  const tasks = parseWorkflowYaml(`
steps:
  - id: final-canon-auditor
    type: agent
    agent:
      prompt: "Review canon"
      output_fields: [verdict]
  - id: production-route-gate
    type: agent
    depends_on: [final-canon-auditor]
    agent:
      prompt: "Route on \${{ final_canon_auditor.output_data.production_ready == true }}"
`);

  const warnings = validateAgentOutputContracts(tasks);

  assert.equal(warnings.length, 1);
  const warning = warnings[0];
  assert.ok(warning);
  assert.equal(warning.sourceStepId, 'final-canon-auditor');
  assert.equal(warning.consumerStepId, 'production-route-gate');
  assert.equal(warning.field, 'production_ready');
});

test('agent output contract warning is skipped for declared and built-in fields', () => {
  const tasks = parseWorkflowYaml(`
steps:
  - id: review
    type: agent
    agent:
      prompt: "Review"
      output_fields: [production_ready]
  - id: gate
    type: agent
    depends_on: [review]
    agent:
      prompt: "Use \${review.output_data.production_ready} and \${review.output_data.artifact_path}"
`);

  assert.deepEqual(validateAgentOutputContracts(tasks), []);
});

test('manus step: full-fields round-trip preserves all manus_* fields and schema-as-object', () => {
  const yaml = `
steps:
  - id: research
    type: manus
    manus:
      prompt: "Investigate widget market"
      structured_output_schema:
        type: object
        properties:
          summary:
            type: string
          score:
            type: number
        required: [summary]
      connectors: [google_drive, slack]
      enable_skills: [browse, code]
      force_skills: [browse]
      agent_profile: deep_research
      locale: en-US
      project_id: proj-123
      title: "Widget market scan"
      timeout_seconds: 1200
      poll_interval_seconds: 10
      allow_failure: true
`;
  const tasks1 = parseWorkflowYaml(yaml);
  assert.equal(tasks1.length, 1);
  const t1 = tasks1[0]!;
  assert.equal(t1.type, 'manus');
  assert.equal(t1.manus_prompt, 'Investigate widget market');
  // structured_output_schema parses to a JSON-string round-trip of the object.
  assert.ok(t1.manus_structured_output_schema, 'schema string present');
  const schema1 = JSON.parse(t1.manus_structured_output_schema!);
  assert.equal(schema1.type, 'object');
  assert.deepEqual(schema1.required, ['summary']);
  assert.equal(schema1.properties.summary.type, 'string');
  assert.equal(schema1.properties.score.type, 'number');
  assert.deepEqual(t1.manus_connectors, ['google_drive', 'slack']);
  assert.deepEqual(t1.manus_enable_skills, ['browse', 'code']);
  assert.deepEqual(t1.manus_force_skills, ['browse']);
  assert.equal(t1.manus_agent_profile, 'deep_research');
  assert.equal(t1.manus_locale, 'en-US');
  assert.equal(t1.manus_project_id, 'proj-123');
  assert.equal(t1.manus_title, 'Widget market scan');
  assert.equal(t1.manus_timeout_seconds, 1200);
  assert.equal(t1.manus_poll_interval_seconds, 10);
  assert.equal(t1.manus_allow_failure, true);

  // Re-emit and re-parse — structural equivalence with the original parse.
  const yaml2 = tasksToYaml(tasks1);
  const tasks2 = parseWorkflowYaml(yaml2);
  assert.equal(tasks2.length, 1);
  const t2 = tasks2[0]!;
  assert.equal(t2.type, 'manus');
  assert.equal(t2.manus_prompt, t1.manus_prompt);
  assert.deepEqual(t2.manus_connectors, t1.manus_connectors);
  assert.deepEqual(t2.manus_enable_skills, t1.manus_enable_skills);
  assert.deepEqual(t2.manus_force_skills, t1.manus_force_skills);
  assert.equal(t2.manus_agent_profile, t1.manus_agent_profile);
  assert.equal(t2.manus_locale, t1.manus_locale);
  assert.equal(t2.manus_project_id, t1.manus_project_id);
  assert.equal(t2.manus_title, t1.manus_title);
  assert.equal(t2.manus_timeout_seconds, t1.manus_timeout_seconds);
  assert.equal(t2.manus_poll_interval_seconds, t1.manus_poll_interval_seconds);
  assert.equal(t2.manus_allow_failure, t1.manus_allow_failure);

  // structured_output_schema must round-trip as a JSON object (not a stringified blob).
  assert.ok(t2.manus_structured_output_schema, 'schema string present after round-trip');
  const schema2 = JSON.parse(t2.manus_structured_output_schema!);
  assert.deepEqual(schema2, schema1);
  // The emitted YAML must contain the schema as an inline JSON object, not a quoted string.
  assert.ok(
    /structured_output_schema:\s*\{/.test(yaml2),
    'emitted schema is an inline object, not a quoted string',
  );
});

test('manus step: minimal-fields case round-trips cleanly with only prompt set', () => {
  const yaml = `
steps:
  - id: quick
    type: manus
    manus:
      prompt: "Quick check"
`;
  const tasks1 = parseWorkflowYaml(yaml);
  assert.equal(tasks1.length, 1);
  const t1 = tasks1[0]!;
  assert.equal(t1.type, 'manus');
  assert.equal(t1.manus_prompt, 'Quick check');
  assert.equal(t1.manus_structured_output_schema, undefined);
  assert.equal(t1.manus_agent_profile, undefined);
  assert.equal(t1.manus_locale, undefined);

  const yaml2 = tasksToYaml(tasks1);
  const tasks2 = parseWorkflowYaml(yaml2);
  assert.equal(tasks2.length, 1);
  const t2 = tasks2[0]!;
  assert.equal(t2.type, 'manus');
  assert.equal(t2.manus_prompt, 'Quick check');
  assert.equal(t2.manus_structured_output_schema, undefined);
  assert.equal(t2.manus_agent_profile, undefined);
  assert.equal(t2.manus_locale, undefined);
  assert.equal(t2.manus_allow_failure, undefined);
});

test('manus step with credentials_ref round-trips cleanly', () => {
  const yaml = `
steps:
  - id: research
    type: manus
    manus:
      prompt: "Find competitors"
      credentials_ref: "manus:work"
`;
  const tasks1 = parseWorkflowYaml(yaml);
  assert.equal(tasks1.length, 1);
  assert.equal(tasks1[0]!.manus_credentials_ref, 'manus:work');

  // Re-emit + re-parse — credentials_ref survives the round-trip.
  const yaml2 = tasksToYaml(tasks1);
  assert.ok(/credentials_ref:\s*['"]?manus:work['"]?/.test(yaml2),
    'emitted YAML contains credentials_ref under the manus block');
  const tasks2 = parseWorkflowYaml(yaml2);
  assert.equal(tasks2[0]!.manus_credentials_ref, 'manus:work');
});

test('manus step without credentials_ref omits the field in emitted YAML', () => {
  const yaml = `
steps:
  - id: research
    type: manus
    manus:
      prompt: "Find competitors"
`;
  const tasks1 = parseWorkflowYaml(yaml);
  assert.equal(tasks1[0]!.manus_credentials_ref, undefined);

  // Emitted YAML must NOT contain a credentials_ref line — empty values
  // should be skipped, not serialized as ``credentials_ref: ""``.
  const yaml2 = tasksToYaml(tasks1);
  assert.ok(!/credentials_ref/.test(yaml2),
    'emitted YAML omits credentials_ref when unset');
  const tasks2 = parseWorkflowYaml(yaml2);
  assert.equal(tasks2[0]!.manus_credentials_ref, undefined);
});

test('manus step register_output: full-fields round-trip', () => {
  const yaml = `
steps:
  - id: research
    type: manus
    manus:
      prompt: "Investigate"
      register_output:
        entity_name: "report-\${inputs.topic}"
        entity_kind: "research-report"
        entity_tag: "ready"
        entity_space: "Revka/WorkflowOutputs/Research"
        register_attachments: false
        content_source: "structured"
`;
  const tasks1 = parseWorkflowYaml(yaml);
  const t1 = tasks1[0]!;
  assert.equal(t1.manus_register_enabled, true);
  assert.equal(t1.manus_register_entity_name, 'report-${inputs.topic}');
  assert.equal(t1.manus_register_entity_kind, 'research-report');
  assert.equal(t1.manus_register_entity_tag, 'ready');
  assert.equal(t1.manus_register_entity_space, 'Revka/WorkflowOutputs/Research');
  assert.equal(t1.manus_register_attachments, false);
  assert.equal(t1.manus_register_content_source, 'structured');

  const yaml2 = tasksToYaml(tasks1);
  assert.ok(/register_output:/.test(yaml2), 'emitted YAML contains register_output block');
  const tasks2 = parseWorkflowYaml(yaml2);
  const t2 = tasks2[0]!;
  assert.equal(t2.manus_register_enabled, true);
  assert.equal(t2.manus_register_entity_name, t1.manus_register_entity_name);
  assert.equal(t2.manus_register_entity_kind, t1.manus_register_entity_kind);
  assert.equal(t2.manus_register_entity_tag, t1.manus_register_entity_tag);
  assert.equal(t2.manus_register_entity_space, t1.manus_register_entity_space);
  assert.equal(t2.manus_register_attachments, t1.manus_register_attachments);
  assert.equal(t2.manus_register_content_source, t1.manus_register_content_source);
});

test('manus step register_output: omitted when register_output block absent in YAML', () => {
  // Without register_output in the YAML, the emitted YAML should not
  // contain a register_output block — even after a round-trip through tasks.
  const yaml = `
steps:
  - id: r
    type: manus
    manus:
      prompt: "x"
`;
  const tasks = parseWorkflowYaml(yaml);
  // The canonical enabled flag should be unset when no block is present.
  assert.notEqual(tasks[0]!.manus_register_enabled, true);
  const out = tasksToYaml(tasks);
  assert.ok(!/register_output/.test(out),
    'emitted YAML omits register_output when unconfigured');
});

test('manus step register_output: enabled flag round-trips through tasksToFlow / flowToTasks', () => {
  // Mirrors the UI checkbox flow: parse → tasksToFlow → flowToTasks →
  // tasksToYaml. The canonical `manus_register_enabled` flag must
  // survive that round-trip so the editor's checkbox state is preserved.
  const yaml = `
steps:
  - id: research
    type: manus
    manus:
      prompt: "Investigate"
      register_output:
        entity_name: "report"
        entity_kind: "research-report"
`;
  const tasks1 = parseWorkflowYaml(yaml);
  const { nodes, edges } = tasksToFlow(tasks1);
  // Node data carries the canonical UI flag.
  const n = nodes[0]!;
  assert.equal(n.data.manusRegisterEnabled, true);

  const tasks2 = flowToTasks(nodes, edges);
  assert.equal(tasks2[0]!.manus_register_enabled, true);
  const yaml2 = tasksToYaml(tasks2);
  assert.ok(/register_output:/.test(yaml2), 'enabled flag round-trip emits register_output block');
});

test('manus step register_output: enabled with empty entity_name/kind still emits block', () => {
  // The checkbox is on but the user hasn't filled in entity_name/kind
  // yet. The emit path should still emit the block (with empty strings)
  // so the runtime fail-fasts at registration time with register_output_error
  // — that's the right behavior so users see the error and know to fill
  // in the fields rather than silently dropping the block.
  const tasks: TaskDefinition[] = [{
    id: 'r',
    type: 'manus',
    name: 'r',
    description: '',
    agent_hints: [],
    skills: [],
    depends_on: [],
    inputs: [],
    outputs: [],
    on_complete: '',
    on_fail: '',
    timeout_minutes: 0,
    retry_count: 0,
    manus_prompt: 'x',
    manus_register_enabled: true,
    manus_register_entity_name: '',
    manus_register_entity_kind: '',
  } as unknown as TaskDefinition];
  const out = tasksToYaml(tasks);
  assert.ok(/register_output:/.test(out),
    'emit block even when entity_name/kind are empty (runtime fail-fast)');
  // yamlEscape('') returns '' so the lines render as `entity_name:` / `entity_kind:`
  // — both keys are present so the runtime fail-fast path sees the empty values.
  assert.ok(/entity_name:/.test(out), 'entity_name key present in block');
  assert.ok(/entity_kind:/.test(out), 'entity_kind key present in block');
});

test('output metadata target and resolve metadata source round-trip', () => {
  const yaml = `
steps:
  - id: publish
    type: output
    output:
      format: markdown
      template: "body"
      entity_name: "report"
      entity_kind: "Report"
      artifact_summary_model: "claude-haiku-4-5-20251001"
      metadata_target: revision
      entity_metadata:
        topic: "Q1"
  - id: resolve
    type: resolve
    depends_on: [publish]
    resolve:
      kind: "Report"
      tag: "ready"
      mode: "latest"
      artifact_name: "content.md"
      metadata_source: artifact
      fields: [topic]
`;

  const tasks1 = parseWorkflowYaml(yaml);
  assert.equal(tasks1[0]!.metadata_target, 'revision');
  assert.equal(tasks1[0]!.artifact_summary_model, 'claude-haiku-4-5-20251001');
  assert.equal(tasks1[1]!.resolve_artifact_name, 'content.md');
  assert.equal(tasks1[1]!.resolve_metadata_source, 'artifact');

  const { nodes, edges } = tasksToFlow(tasks1);
  assert.equal(nodes[0]!.data.entityMetadataTarget, 'revision');
  assert.equal(nodes[0]!.data.artifactSummaryModel, 'claude-haiku-4-5-20251001');
  assert.equal(nodes[1]!.data.resolveArtifactName, 'content.md');
  assert.equal(nodes[1]!.data.resolveMetadataSource, 'artifact');

  const tasks2 = flowToTasks(nodes, edges);
  assert.equal(tasks2[0]!.metadata_target, 'revision');
  assert.equal(tasks2[0]!.artifact_summary_model, 'claude-haiku-4-5-20251001');
  assert.equal(tasks2[1]!.resolve_artifact_name, 'content.md');
  assert.equal(tasks2[1]!.resolve_metadata_source, 'artifact');

  const yaml2 = tasksToYaml(tasks2);
  assert.match(yaml2, /artifact_summary_model:\s+claude-haiku-4-5-20251001/);
  assert.match(yaml2, /metadata_target:\s+revision/);
  assert.match(yaml2, /artifact_name:\s+content\.md/);
  assert.match(yaml2, /metadata_source:\s+artifact/);

  const tasks3 = parseWorkflowYaml(yaml2);
  assert.equal(tasks3[0]!.metadata_target, 'revision');
  assert.equal(tasks3[0]!.artifact_summary_model, 'claude-haiku-4-5-20251001');
  assert.equal(tasks3[1]!.resolve_artifact_name, 'content.md');
  assert.equal(tasks3[1]!.resolve_metadata_source, 'artifact');
});

test('kumiho_context step round-trips through YAML and flow fields', () => {
  const yaml = `
steps:
  - id: latest-production-episode
    type: resolve
    resolve:
      kind: webnovel-episode
      tag: production-ready
      fail_if_missing: false
  - id: episode-context
    name: Episode Context
    type: kumiho_context
    kumiho:
      project: StoryProject
      mode: graph_augmented_context
      seed:
        bundles:
          - series-main-canon
          - series-active-storylines
        krefs:
          - "\${latest-production-episode.output_data.kref}"
        queries:
          - "\${inputs.episode_goal}"
        items:
          - kind: character-state
            name_pattern: protagonist
            tag: current
      traversal:
        max_depth: 2
        direction: both
        edge_types:
          - DEPENDS_ON
          - REFERENCES
          - BLOCKS
      filters:
        include_kinds:
          - canon-rule
          - character-state
          - storyline
        exclude_tags: [deprecated]
        max_items: 50
      ranking:
        method: hybrid
        semantic_query: "\${inputs.episode_goal} \${inputs.must_include}"
      lock:
        revisions: true
        tag_preference: [current, active, production-ready, ready, published, latest]
      output:
        format: episode_context_pack
        include_artifact_summaries: true
        include_artifact_content: false
        max_artifact_chars_per_item: 3000
        include_edge_map: true
        include_conflict_warnings: true
        include_missing_context: true
    depends_on: [latest-production-episode]
`;

  const tasks1 = parseWorkflowYaml(yaml);
  const ctx1 = tasks1[1]!;
  assert.equal(ctx1.type, 'kumiho_context');
  assert.equal(ctx1.kumiho_project, 'StoryProject');
  assert.equal(ctx1.kumiho_mode, 'graph_augmented_context');
  assert.deepEqual(ctx1.kumiho_seed_bundles, ['series-main-canon', 'series-active-storylines']);
  assert.deepEqual(ctx1.kumiho_traversal_edge_types, ['DEPENDS_ON', 'REFERENCES', 'BLOCKS']);
  assert.equal(ctx1.kumiho_output_format, 'episode_context_pack');
  assert.equal(ctx1.kumiho_seed_items?.length, 1);

  const { nodes, edges } = tasksToFlow(tasks1);
  const ctxNode = nodes.find((n) => n.id === 'episode-context')!;
  assert.equal(ctxNode.data.kumihoProject, 'StoryProject');
  assert.deepEqual(ctxNode.data.kumihoFiltersIncludeKinds, ['canon-rule', 'character-state', 'storyline']);
  assert.ok(edges.some((e) => e.source === 'latest-production-episode' && e.target === 'episode-context'));

  const tasks2 = flowToTasks(nodes, edges);
  const ctx2 = tasks2.find((t) => t.id === 'episode-context')!;
  assert.equal(ctx2.kumiho_project, 'StoryProject');
  assert.equal(ctx2.kumiho_output_format, 'episode_context_pack');
  assert.equal((ctx2.kumiho_config?.seed as any).items.length, 1);

  const yaml2 = tasksToYaml(tasks2);
  assert.match(yaml2, /type:\s+kumiho_context/);
  assert.match(yaml2, /project:\s+StoryProject/);
  assert.match(yaml2, /format:\s+episode_context_pack/);

  const tasks3 = parseWorkflowYaml(yaml2);
  const ctx3 = tasks3.find((t) => t.id === 'episode-context')!;
  assert.equal(ctx3.kumiho_project, 'StoryProject');
  assert.deepEqual(ctx3.kumiho_lock_tag_preference, ['current', 'active', 'production-ready', 'ready', 'published', 'latest']);
  assert.equal(ctx3.kumiho_output_include_conflict_warnings, true);
});

test('kumiho mutation steps round-trip through YAML and flow fields', () => {
  const yaml = `
steps:
  - id: emit-final-episode
    type: output
    output:
      template: "episode"
  - id: update-output-bundles
    type: kumiho_bundle_update
    kumiho:
      project: StoryProject
      mode: add_members
      create_if_missing: true
      idempotent: true
      updates:
        - bundle: series-production-episodes
          add:
            - item_kref: "\${emit-final-episode.output_data.item_kref}"
              reason: Production-ready episode
  - id: patch-loader
    type: resolve
    resolve:
      kind: canon-patch
      tag: candidate
      fail_if_missing: false
  - id: approval
    type: human_approval
    human_approval:
      message: "Approve patch?"
  - id: apply-canon-patch
    type: kumiho_patch_apply
    kumiho:
      project: StoryProject
      patch_kref: "\${patch-loader.output_data.kref}"
      dry_run: false
      approval:
        required: true
        approved: "\${approval.output_data.approved}"
        approved_by: "\${approval.output_data.approved_by}"
        approval_note: "\${approval.output_data.note}"
      apply:
        create_revisions: true
        create_edges: true
        update_tags: true
        untag_previous_current: true
        update_bundles: true
        save_apply_report: true
      tag_policy:
        new_revision_tags: [current, approved]
        patch_tags:
          remove: [candidate]
          add: [applied]
      bundle_policy:
        pending_patch_bundle: series-pending-canon-patches
        applied_patch_bundle: series-applied-canon-patches
        current_state_bundle: series-current-character-states
      evidence:
        require_evidence_locator: true
        source_episode_kref: "\${emit-final-episode.output_data.revision_kref}"
`;

  const tasks1 = parseWorkflowYaml(yaml);
  const bundle = tasks1.find((t) => t.id === 'update-output-bundles')!;
  const patch = tasks1.find((t) => t.id === 'apply-canon-patch')!;
  assert.equal(bundle.type, 'kumiho_bundle_update');
  assert.equal(bundle.kumiho_project, 'StoryProject');
  assert.equal(bundle.kumiho_mode, 'add_members');
  assert.equal(bundle.kumiho_create_if_missing, true);
  assert.equal((bundle.kumiho_updates?.[0] as any).bundle, 'series-production-episodes');
  assert.equal(patch.type, 'kumiho_patch_apply');
  assert.equal(patch.kumiho_patch_kref, '${patch-loader.output_data.kref}');
  assert.equal(patch.kumiho_dry_run, false);
  assert.equal(patch.kumiho_approval_approved, '${approval.output_data.approved}');
  assert.deepEqual(patch.kumiho_new_revision_tags, ['current', 'approved']);
  assert.equal(patch.kumiho_pending_patch_bundle, 'series-pending-canon-patches');

  const { nodes, edges } = tasksToFlow(tasks1);
  const bundleNode = nodes.find((n) => n.id === 'update-output-bundles')!;
  const patchNode = nodes.find((n) => n.id === 'apply-canon-patch')!;
  assert.equal(bundleNode.data.kumihoCreateIfMissing, true);
  assert.equal(patchNode.data.kumihoDryRun, false);
  assert.ok(edges.some((e) => e.source === 'emit-final-episode' && e.target === 'update-output-bundles'));
  assert.ok(edges.some((e) => e.source === 'patch-loader' && e.target === 'apply-canon-patch'));
  assert.ok(edges.some((e) => e.source === 'approval' && e.target === 'apply-canon-patch'));

  const tasks2 = flowToTasks(nodes, edges);
  const yaml2 = tasksToYaml(tasks2);
  assert.match(yaml2, /type:\s+kumiho_bundle_update/);
  assert.match(yaml2, /type:\s+kumiho_patch_apply/);
  assert.match(yaml2, /create_if_missing:\s+true/);
  assert.match(yaml2, /patch_kref:\s+\$\{patch-loader\.output_data\.kref\}/);
  assert.match(yaml2, /pending_patch_bundle:\s+series-pending-canon-patches/);

  const reparsed = parseWorkflowYaml(yaml2);
  assert.equal(reparsed.find((t) => t.id === 'update-output-bundles')!.kumiho_mode, 'add_members');
  assert.equal(reparsed.find((t) => t.id === 'apply-canon-patch')!.kumiho_apply_update_bundles, true);
});

test('step compression flag round-trips through YAML and flow nodes', () => {
  const yaml = `
steps:
  - id: generate
    type: agent
    compression: true
    agent:
      prompt: "Generate a large report"
  - id: consume
    type: output
    depends_on: [generate]
    output:
      template: "\${generate.output}"
`;

  const tasks1 = parseWorkflowYaml(yaml);
  assert.equal(tasks1[0]!.compression, true);

  const { nodes, edges } = tasksToFlow(tasks1);
  assert.equal(nodes[0]!.data.compression, true);

  const tasks2 = flowToTasks(nodes, edges);
  assert.equal(tasks2[0]!.compression, true);

  const yaml2 = tasksToYaml(tasks2);
  assert.match(yaml2, /compression:\s+true/);

  const tasks3 = parseWorkflowYaml(yaml2);
  assert.equal(tasks3[0]!.compression, true);
});
