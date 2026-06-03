import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  GOOGLE_AGENTOPS_REQUIRED_TOOLS,
  dedupeToolNames,
  expandGoogleAgentOpsRequiredTools,
  hasGoogleAgentOpsBundle,
  requiresGoogleAgentOpsToolMode,
} from '../agentToolPresets';

test('google_agents_cli expands to the Google AgentOps companion tools', () => {
  assert.deepEqual(
    expandGoogleAgentOpsRequiredTools(['capture_skill', 'google_agents_cli']),
    ['capture_skill', ...GOOGLE_AGENTOPS_REQUIRED_TOOLS],
  );
});

test('Google AgentOps expansion preserves explicit order and removes duplicates', () => {
  assert.deepEqual(
    expandGoogleAgentOpsRequiredTools([
      'a2a_send_task',
      'google_agents_cli',
      'a2a_send_task',
      'a2a_discover',
    ]),
    [
      'a2a_send_task',
      'google_agents_cli',
      'a2a_discover',
      'a2a_get_remote_task',
    ],
  );
});

test('Google AgentOps tools require a narrowed MCP surface', () => {
  assert.equal(requiresGoogleAgentOpsToolMode(['capture_skill']), false);
  assert.equal(requiresGoogleAgentOpsToolMode(['a2a_discover']), true);
  assert.equal(requiresGoogleAgentOpsToolMode(['google_agents_cli']), true);
  assert.equal(hasGoogleAgentOpsBundle(['google_agents_cli']), false);
  assert.equal(hasGoogleAgentOpsBundle(GOOGLE_AGENTOPS_REQUIRED_TOOLS), true);
});

test('tool-name normalization trims blanks and keeps first occurrence', () => {
  assert.deepEqual(dedupeToolNames([' tag_revision ', '', 'tag_revision', 'capture_skill']), [
    'tag_revision',
    'capture_skill',
  ]);
});
