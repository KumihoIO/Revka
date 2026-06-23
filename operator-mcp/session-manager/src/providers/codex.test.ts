/**
 * #459 — spawn-time permission gating for CLI providers.
 *
 * codexSpawnRefusal decides whether an untrusted spawn of a permission-bypassing
 * CLI (codex/agy/cursor) must be refused. Trusted/unset spawns always proceed;
 * opencode has no bypass flag and is never refused.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { codexSpawnRefusal } from "./codex.js";

test("trusted or unset spawns are always allowed", () => {
  for (const agentType of ["codex", "agy", "cursor", "opencode"]) {
    assert.equal(codexSpawnRefusal(agentType, true), null);
    assert.equal(codexSpawnRefusal(agentType, undefined), null);
  }
});

test("untrusted permission-bypassing CLI spawns are refused", () => {
  for (const agentType of ["codex", "agy", "cursor"]) {
    const reason = codexSpawnRefusal(agentType, false);
    assert.ok(reason, `expected refusal for ${agentType}`);
    assert.ok(reason.includes(agentType));
  }
});

test("untrusted opencode is allowed (no bypass flag to gate)", () => {
  assert.equal(codexSpawnRefusal("opencode", false), null);
});
