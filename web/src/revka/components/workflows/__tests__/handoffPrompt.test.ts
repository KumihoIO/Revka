import test from 'node:test';
import assert from 'node:assert/strict';

import { buildAutoInsertedDependencyReference } from '../handoffPrompt';

test('agent dependency handoff auto-insert references artifact path instead of inline output', () => {
  const snippet = buildAutoInsertedDependencyReference('draft', 'agent', 'agent');

  assert.match(snippet, /\$\{draft\.output_data\.artifact_path\}/);
  assert.match(snippet, /Read artifact_path/);
  assert.doesNotMatch(snippet, /\$\{draft\.output\}/);
});

test('non-agent target dependency auto-insert keeps inline output reference', () => {
  assert.equal(
    buildAutoInsertedDependencyReference('draft', 'output', 'agent'),
    '${draft.output}',
  );
});

test('agent target keeps inline output when source has no full-output artifact', () => {
  assert.equal(
    buildAutoInsertedDependencyReference('build', 'agent', 'shell'),
    '${build.output}',
  );
});
