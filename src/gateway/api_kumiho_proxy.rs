//! Generic Kumiho API proxy — forwards `/api/kumiho/*` requests to the
//! upstream Kumiho FastAPI server, injecting the service token and remapping
//! auth errors so they don't trigger browser re-pairing.
//!
//! Only GET is exposed; write methods would need an idempotency story the
//! upstream doesn't currently provide (see `kumiho_client::send_no_retry`).
//! GETs go through `KumihoClient`'s retry helper indirectly via the same
//! `is_retryable_status` / `looks_like_html_body` helpers, so the proxy
//! produces the same clean JSON shape as typed routes on a CDN 5xx.

use super::AppState;
use super::api::require_auth;
use super::api_agents::build_kumiho_client;
use super::kumiho_client::{is_retryable_status, looks_like_html_body};
use axum::{
    Json,
    extract::{Query, State},
    http::{HeaderMap, HeaderValue, StatusCode, header},
    response::{IntoResponse, Response},
};
use std::collections::HashMap;
use std::time::{Duration, Instant};

/// Same end-to-end budget as `KumihoClient::TOTAL_BUDGET`. Duplicated here
/// (rather than re-exported) because the proxy reconstructs its own retry
/// loop instead of going through a typed `KumihoClient` method.
const PROXY_TOTAL_BUDGET: Duration = Duration::from_secs(15);
const PROXY_PER_ATTEMPT_TIMEOUT: Duration = Duration::from_secs(5);
const PROXY_MAX_ATTEMPTS: u32 = 3;
const PROXY_BACKOFF_MS: [u64; 2] = [500, 1500];

/// Build the unified 503 response the typed routes return on CDN 5xx / hung
/// upstream. Centralised here so the proxy can't leak a different shape.
fn upstream_unavailable(upstream_status: u16) -> Response {
    let mut resp = (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(serde_json::json!({
            "error": "Kumiho cloud temporarily unavailable",
            "error_code": "kumiho_upstream_unavailable",
            "upstream_status": upstream_status,
            "retry_after_seconds": 5,
        })),
    )
        .into_response();
    resp.headers_mut()
        .insert(header::RETRY_AFTER, HeaderValue::from_static("5"));
    resp
}

fn unreachable() -> Response {
    let mut resp = (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(serde_json::json!({
            "error": "Kumiho cloud unreachable",
            "error_code": "kumiho_unreachable",
            "retry_after_seconds": 10,
        })),
    )
        .into_response();
    resp.headers_mut()
        .insert(header::RETRY_AFTER, HeaderValue::from_static("10"));
    resp
}

/// GET /api/kumiho/{*path} — proxy any GET request to Kumiho API.
///
/// The browser sends `/api/kumiho/projects` and this handler forwards it
/// to `{kumiho_api_url}/api/v1/projects` with the service token header.
/// Query parameters are forwarded as-is.
///
/// On retryable 5xx (502/503/504/520/522/524), retries up to 3× with jittered
/// backoff inside a 15s wall-time budget — same policy as `KumihoClient`. On
/// any 5xx that escapes (incl. budget-exhausted or a plain 500), returns the
/// canonical `kumiho_upstream_unavailable` JSON shape with 503 + `Retry-After`.
pub async fn handle_kumiho_proxy(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Path(path): axum::extract::Path<String>,
    Query(params): Query<HashMap<String, String>>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let client = build_kumiho_client(&state);
    let base_url = {
        let config = state.config.lock();
        config.kumiho.api_url.clone()
    };
    let service_token = std::env::var("KUMIHO_SERVICE_TOKEN").unwrap_or_default();

    // Build the upstream URL
    let mut url = format!("{}/api/v1/{}", base_url.trim_end_matches('/'), path);
    if !params.is_empty() {
        let qs: Vec<String> = params
            .iter()
            .map(|(k, v)| format!("{}={}", urlencoding::encode(k), urlencoding::encode(v)))
            .collect();
        url = format!("{}?{}", url, qs.join("&"));
    }

    let deadline = Instant::now() + PROXY_TOTAL_BUDGET;
    let mut last_retryable_status: Option<u16> = None;

    for attempt in 1..=PROXY_MAX_ATTEMPTS {
        let now = Instant::now();
        if now >= deadline {
            break;
        }
        let attempt_cap = PROXY_PER_ATTEMPT_TIMEOUT.min(deadline.saturating_duration_since(now));

        let resp = client
            .client()
            .get(&url)
            .header("X-Kumiho-Token", &service_token)
            .timeout(attempt_cap)
            .send()
            .await;

        match resp {
            Ok(r) => {
                let status = r.status().as_u16();
                let content_type = r
                    .headers()
                    .get(reqwest::header::CONTENT_TYPE)
                    .and_then(|v| v.to_str().ok())
                    .map(str::to_owned);

                // Retryable 5xx — drop body (avoid leaking Cloudflare HTML),
                // log, back off, and retry within budget.
                if is_retryable_status(status) {
                    last_retryable_status = Some(status);
                    drop(r);
                    if attempt < PROXY_MAX_ATTEMPTS {
                        let delay_ms = PROXY_BACKOFF_MS[(attempt - 1) as usize];
                        let now2 = Instant::now();
                        let remaining = deadline.saturating_duration_since(now2);
                        if remaining <= Duration::from_millis(delay_ms) {
                            break;
                        }
                        tracing::warn!(
                            attempt = attempt,
                            max_attempts = PROXY_MAX_ATTEMPTS,
                            upstream_status = status,
                            path = %path,
                            "Kumiho proxy: retryable 5xx; retrying"
                        );
                        tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                        continue;
                    }
                    break;
                }

                let body = r.text().await.unwrap_or_default();

                // Remap 401/403 to 502 so browser doesn't clear pairing token
                let code = if status == 401 || status == 403 {
                    StatusCode::BAD_GATEWAY
                } else {
                    StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY)
                };

                if code.is_success() {
                    // Forward the JSON body as-is
                    return (
                        code,
                        [(axum::http::header::CONTENT_TYPE, "application/json")],
                        body,
                    )
                        .into_response();
                }

                // Non-retryable 5xx (500/501) or anything else: trim HTML
                // before propagating, and rewrite any 5xx to the canonical
                // 503 "temporarily unavailable" shape so the dashboard can
                // branch on `error_code`.
                if status >= 500 {
                    if looks_like_html_body(&body, content_type.as_deref()) {
                        tracing::warn!(
                            upstream_status = status,
                            path = %path,
                            body_preview = body.chars().take(256).collect::<String>(),
                            "Kumiho proxy: HTML 5xx body (trimming)"
                        );
                    } else {
                        tracing::warn!(
                            upstream_status = status,
                            path = %path,
                            body = %body,
                            "Kumiho proxy: non-retried 5xx"
                        );
                    }
                    return upstream_unavailable(status);
                }

                // 4xx — never HTML in normal Kumiho responses, but trim if
                // it slipped through (e.g. CDN-injected 4xx page).
                let safe_body = if looks_like_html_body(&body, content_type.as_deref()) {
                    "<HTML error page — see gateway logs>".to_string()
                } else {
                    body
                };
                return (
                    code,
                    Json(serde_json::json!({
                        "error": format!("Kumiho upstream: {safe_body}"),
                        "error_code": "kumiho_upstream_error",
                        "upstream_status": status,
                    })),
                )
                    .into_response();
            }
            Err(e) => {
                if attempt < PROXY_MAX_ATTEMPTS {
                    let delay_ms = PROXY_BACKOFF_MS[(attempt - 1) as usize];
                    let now2 = Instant::now();
                    let remaining = deadline.saturating_duration_since(now2);
                    if remaining <= Duration::from_millis(delay_ms) {
                        tracing::warn!(error = %e, path = %path, "Kumiho proxy: budget exhausted");
                        return unreachable();
                    }
                    tracing::warn!(
                        attempt = attempt,
                        max_attempts = PROXY_MAX_ATTEMPTS,
                        error = %e,
                        path = %path,
                        "Kumiho proxy: network error; retrying"
                    );
                    tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                    continue;
                }
                tracing::warn!(error = %e, path = %path, "Kumiho proxy: unreachable after retries");
                return unreachable();
            }
        }
    }

    // Budget exhausted on retryable status path.
    upstream_unavailable(last_retryable_status.unwrap_or(502))
}

#[cfg(test)]
mod tests {
    //! Verify the generic proxy never leaks Cloudflare HTML and surfaces the
    //! same `kumiho_upstream_unavailable` JSON shape as the typed routes.
    use super::*;
    use wiremock::matchers::{method, path as wm_path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    /// Helper: drive `handle_kumiho_proxy`'s retry/format logic against a
    /// mock by talking directly to the upstream URL it builds. We can't easily
    /// inject an `AppState` here, so this test exercises the proxy through a
    /// small helper that mirrors its body but takes the upstream URL directly.
    /// That keeps the assertion focused on the part Codex flagged: 5xx with
    /// HTML must become clean JSON, not `{ "error": "Kumiho upstream: <html>" }`.
    async fn proxy_get(upstream_base: &str, sub_path: &str) -> Response {
        // Mirror handle_kumiho_proxy without the AppState/auth dance.
        let url = format!(
            "{}/api/v1/{}",
            upstream_base.trim_end_matches('/'),
            sub_path
        );
        let http = reqwest::Client::new();

        let deadline = Instant::now() + PROXY_TOTAL_BUDGET;
        let mut last_retryable_status: Option<u16> = None;
        for attempt in 1..=PROXY_MAX_ATTEMPTS {
            let now = Instant::now();
            if now >= deadline {
                break;
            }
            let attempt_cap =
                PROXY_PER_ATTEMPT_TIMEOUT.min(deadline.saturating_duration_since(now));
            let r = match http.get(&url).timeout(attempt_cap).send().await {
                Ok(r) => r,
                Err(_) => return unreachable(),
            };
            let status = r.status().as_u16();
            let content_type = r
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .map(str::to_owned);
            if is_retryable_status(status) {
                last_retryable_status = Some(status);
                drop(r);
                if attempt < PROXY_MAX_ATTEMPTS {
                    let delay_ms = PROXY_BACKOFF_MS[(attempt - 1) as usize];
                    tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                    continue;
                }
                break;
            }
            let body = r.text().await.unwrap_or_default();
            if status >= 500 {
                let _ = looks_like_html_body(&body, content_type.as_deref());
                return upstream_unavailable(status);
            }
            let code = StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_GATEWAY);
            if code.is_success() {
                return (code, body).into_response();
            }
            return (code, body).into_response();
        }
        upstream_unavailable(last_retryable_status.unwrap_or(502))
    }

    #[tokio::test]
    async fn proxy_502_html_returns_clean_json_no_angle_brackets() {
        let server = MockServer::start().await;
        Mock::given(method("GET"))
            .and(wm_path("/api/v1/projects"))
            .respond_with(
                ResponseTemplate::new(502)
                    .insert_header("content-type", "text/html; charset=utf-8")
                    .set_body_string("<!DOCTYPE html><html><body>Bad Gateway</body></html>"),
            )
            .mount(&server)
            .await;

        let resp = proxy_get(&server.uri(), "projects").await;
        let (parts, body) = resp.into_parts();
        assert_eq!(parts.status, StatusCode::SERVICE_UNAVAILABLE);
        assert_eq!(
            parts
                .headers
                .get(header::RETRY_AFTER)
                .map(|v| v.to_str().unwrap()),
            Some("5"),
        );
        let bytes = axum::body::to_bytes(body, 64 * 1024).await.unwrap();
        let text = std::str::from_utf8(&bytes).unwrap();
        // Critical assertion: NO `<` characters from upstream HTML may appear
        // in the JSON body the dashboard ultimately renders.
        assert!(
            !text.contains('<'),
            "proxy leaked HTML angle brackets: {text}"
        );
        let parsed: serde_json::Value = serde_json::from_str(text).unwrap();
        assert_eq!(parsed["error_code"], "kumiho_upstream_unavailable");
        assert_eq!(parsed["upstream_status"], 502);
    }
}
