/**
 * Tests for `buildApiError` — the helper that turns a non-2xx HTTP response
 * into a user-facing `ApiError`. The whole point of this layer is to keep
 * upstream HTML splash pages (Cloudflare 502, nginx 504, ...) out of the
 * dashboard. Test that:
 *
 *   1. HTML bodies never leak into the rendered message.
 *   2. Structured gateway errors (with `error` + `error_code`) round-trip
 *      cleanly, with `errorCode` exposed on the thrown error.
 *   3. Plain 4xx JSON errors keep their previous behaviour.
 *
 * Run: npx tsx --test src/lib/__tests__/api.test.ts
 */

// Side-effect import installs window/localStorage shims and a tsx loader
// hook that lets `../api` (which pulls in `basePath.ts`) load in Node.
// Must remain the FIRST import; ESM evaluates side-effect imports in
// declaration order.
import './setup.mjs';

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { ApiError, buildApiError, isHtmlErrorBody } from '../apiError';

test('502 with HTML body produces a clean message — no markup', () => {
  const html =
    '<!DOCTYPE html><html><head><title>Bad gateway</title></head>' +
    '<body><h1>502 Bad Gateway</h1>cloudflare</body></html>';
  const err = buildApiError(502, 'Bad Gateway', html, 'text/html; charset=UTF-8');

  assert.ok(err instanceof ApiError);
  assert.equal(err.status, 502);
  assert.ok(!err.message.includes('<html'), `message leaked html: ${err.message}`);
  assert.ok(!err.message.toLowerCase().includes('<!doctype'), `message leaked doctype: ${err.message}`);
  assert.match(err.message, /Service temporarily unavailable/);
  // The HTML body is stored verbatim on `.body` only because JSON.parse failed.
  // Callers that need it can still inspect it; the message stays clean.
  assert.equal(typeof err.body, 'string');
});

test('503 with structured gateway error exposes error_code', () => {
  const payload = {
    error: 'Kumiho cloud temporarily unavailable',
    error_code: 'kumiho_upstream_unavailable',
    upstream_status: 502,
    attempts: 3,
    retry_after_seconds: 5,
  };
  const err = buildApiError(
    503,
    'Service Unavailable',
    JSON.stringify(payload),
    'application/json',
  );

  assert.equal(err.status, 503);
  assert.equal(err.message, 'API 503: Kumiho cloud temporarily unavailable');
  assert.equal(err.errorCode, 'kumiho_upstream_unavailable');
  assert.deepEqual(err.body, payload);
});

test('400 with simple JSON error keeps current behaviour', () => {
  const payload = { error: 'bad input' };
  const err = buildApiError(
    400,
    'Bad Request',
    JSON.stringify(payload),
    'application/json',
  );

  assert.equal(err.status, 400);
  assert.equal(err.message, 'API 400: bad input');
  assert.equal(err.errorCode, null);
  assert.deepEqual(err.body, payload);
});

test('errorCode falls back to bare `code` when no `error_code`', () => {
  // Some gateway routes (api_auth_profiles rate limit, workflow cancel) emit
  // `code` without the `error_` prefix. The client must still expose it on
  // `.errorCode` so call sites can branch without string-matching messages.
  const payload = { error: 'rate limited', code: 'auth_profile_rate_limited' };
  const err = buildApiError(
    429,
    'Too Many Requests',
    JSON.stringify(payload),
    'application/json',
  );

  assert.equal(err.errorCode, 'auth_profile_rate_limited');
});

test('error_code wins over code when both are present', () => {
  // A defensive case: if a route ever sets both, prefer the canonical
  // `error_code` so we don't accidentally regress against the standard shape.
  const payload = { error: 'x', error_code: 'canonical', code: 'legacy' };
  const err = buildApiError(503, 'Service Unavailable', JSON.stringify(payload), 'application/json');
  assert.equal(err.errorCode, 'canonical');
});

test('isHtmlErrorBody detects content-type and body shapes', () => {
  assert.equal(isHtmlErrorBody('<!DOCTYPE html><html>', null), true);
  assert.equal(isHtmlErrorBody('   <html>', null), true);
  assert.equal(isHtmlErrorBody('{"ok":true}', 'text/html; charset=utf-8'), true);
  assert.equal(isHtmlErrorBody('{"error":"x"}', 'application/json'), false);
  assert.equal(isHtmlErrorBody('plain', null), false);
});

test('renameSession calls PUT /api/sessions/{id} with name body + Bearer auth', async () => {
  // Capture fetch and dynamically import `../api` (so the loader hook in
  // setup.mjs has had a chance to patch basePath.ts).
  let captured: { url: string; init: RequestInit } | null = null;
  const originalFetch = (globalThis as any).fetch;
  (globalThis as any).fetch = async (url: string, init: RequestInit) => {
    captured = { url, init };
    return {
      ok: true,
      status: 200,
      statusText: 'OK',
      headers: { get: () => 'application/json' },
      text: async () => '',
      json: async () => ({ session_id: 'sess-1', name: 'My Chat' }),
    };
  };

  try {
    const { renameSession } = await import('../api');
    const { setToken, clearToken } = await import('../auth');
    setToken('test-token-abc');

    const result = await renameSession('sess-1', 'My Chat');
    assert.deepEqual(result, { session_id: 'sess-1', name: 'My Chat' });

    assert.ok(captured, 'fetch was not called');
    const cap = captured as { url: string; init: RequestInit };
    assert.match(cap.url, /\/api\/sessions\/sess-1$/);
    assert.equal(cap.init.method, 'PUT');
    assert.equal(cap.init.body, JSON.stringify({ name: 'My Chat' }));

    const headers = cap.init.headers as Headers;
    assert.equal(headers.get('Authorization'), 'Bearer test-token-abc');
    assert.equal(headers.get('Content-Type'), 'application/json');

    clearToken();
  } finally {
    (globalThis as any).fetch = originalFetch;
  }
});
