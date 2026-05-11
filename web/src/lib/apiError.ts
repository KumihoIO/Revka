// Pure-data helpers for the `apiFetch` error path. Lives in its own module so
// the gateway-error logic stays unit-testable under `tsx --test` without
// pulling in DOM-only siblings (basePath / window references).

/// Thrown when the gateway returns a non-2xx response. `.body` carries the
/// parsed JSON error payload when the server sent one (otherwise null); use
/// it to render structured error details (validation errors, etc.) in the UI.
/// `.errorCode` mirrors the gateway's `error_code` field on structured JSON
/// errors (e.g. "kumiho_upstream_unavailable") so call sites can branch
/// without string-matching the human message.
export class ApiError extends Error {
  public readonly status: number;
  public readonly body: unknown;
  public readonly errorCode: string | null;

  constructor(status: number, message: string, body: unknown, errorCode: string | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    // Best-effort: if the caller didn't pass an explicit code, pull it out of
    // a structured JSON body so existing callers transparently get the field.
    // The gateway is inconsistent — most routes emit `error_code`, but a few
    // older ones (`api_auth_profiles`, the operator workflow cancel path) emit
    // bare `code`. Prefer `error_code`, fall back to `code`.
    if (errorCode === null && body && typeof body === 'object') {
      const bag = body as { error_code?: unknown; code?: unknown };
      const ec = bag.error_code ?? bag.code;
      this.errorCode = typeof ec === 'string' ? ec : null;
    } else {
      this.errorCode = errorCode;
    }
  }
}

/// Heuristic: does this response look like an HTML error page (e.g.
/// Cloudflare's 502 splash) that snuck through despite the gateway's
/// retry+trim layer? If so, refuse to render it as the user-visible message.
export function isHtmlErrorBody(text: string, contentType: string | null): boolean {
  if (contentType && contentType.toLowerCase().startsWith('text/html')) {
    return true;
  }
  const head = text.trimStart().slice(0, 16).toLowerCase();
  return head.startsWith('<!doctype') || head.startsWith('<html');
}

/// Build the `ApiError` payload from a non-2xx response's raw text + headers.
/// Pulled out of `apiFetch` so it can be unit-tested without a `fetch`/DOM
/// shim.
export function buildApiError(
  status: number,
  statusText: string,
  text: string,
  contentType: string | null,
): ApiError {
  let parsedBody: unknown = null;
  if (text) {
    try {
      parsedBody = JSON.parse(text);
    } catch {
      parsedBody = text;
    }
  }
  const structuredMessage =
    parsedBody && typeof parsedBody === 'object' && 'error' in parsedBody
      ? String((parsedBody as { error: unknown }).error)
      : null;
  const htmlFallback = isHtmlErrorBody(text, contentType)
    ? `Service temporarily unavailable (HTTP ${status})`
    : null;
  const message =
    structuredMessage ||
    htmlFallback ||
    (text && typeof parsedBody === 'string' ? text : null) ||
    statusText ||
    `API ${status}`;
  return new ApiError(status, `API ${status}: ${message}`, parsedBody);
}
