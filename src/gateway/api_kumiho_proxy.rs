//! Generic Kumiho API proxy.
//!
//! This route intentionally does not implement Kumiho transport policy itself.
//! Auth is checked at the Construct gateway boundary, then all Kumiho network
//! access goes through [`super::kumiho_client::KumihoClient`].

use super::AppState;
use super::api::require_auth;
use super::kumiho_client::{RawKumihoResponse, build_kumiho_client, kumiho_error_to_response};
use axum::{
    extract::{Query, State},
    http::{HeaderMap, HeaderName, HeaderValue, header},
    response::{IntoResponse, Response},
};
use std::collections::HashMap;

fn raw_response_to_http(raw: RawKumihoResponse) -> Response {
    let mut response = (raw.status, raw.body).into_response();
    let headers = response.headers_mut();
    headers.insert(
        header::CONTENT_TYPE,
        HeaderValue::from_static("application/json"),
    );
    if let Some(cache_state) = raw.cache_state {
        headers.insert(
            HeaderName::from_static("x-construct-cache"),
            HeaderValue::from_static(cache_state),
        );
    }
    if let Some(transport) = raw.transport {
        headers.insert(
            HeaderName::from_static("x-construct-kumiho-transport"),
            HeaderValue::from_static(transport),
        );
    }
    response
}

/// GET /api/kumiho/{*path} — proxy a Kumiho JSON read endpoint.
///
/// Query parameters are forwarded as-is. The underlying client handles local
/// SDK bridge, hosted FastAPI fallback, retries, account-scoped cache keys,
/// stale fallback, and error response normalization.
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
    match client.get_raw(&path, &params).await {
        Ok(raw) => raw_response_to_http(raw),
        Err(err) => kumiho_error_to_response(err),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::StatusCode;

    #[test]
    fn raw_response_to_http_sets_transport_and_cache_headers() {
        let response = raw_response_to_http(RawKumihoResponse {
            status: StatusCode::OK,
            body: "{}".to_string(),
            transport: Some("sdk-bridge"),
            cache_state: Some("hit"),
        });

        assert_eq!(response.status(), StatusCode::OK);
        assert_eq!(
            response
                .headers()
                .get("x-construct-kumiho-transport")
                .and_then(|v| v.to_str().ok()),
            Some("sdk-bridge"),
        );
        assert_eq!(
            response
                .headers()
                .get("x-construct-cache")
                .and_then(|v| v.to_str().ok()),
            Some("hit"),
        );
    }
}
