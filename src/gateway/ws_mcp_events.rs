//! `GET /ws/mcp/events` — WebSocket proxy onto the in-process MCP server's
//! session-wide progress SSE stream (`GET /session/<id>/events`).
//!
//! The MCP server runs as a tokio task inside the same daemon process as the
//! gateway — this proxy still loopbacks through `~/.revka/mcp.json` so it
//! works without knowing the ephemeral port ahead of time.
//!
//! The V2 Code tab opens this WS while a CLI coding agent is running in the
//! PTY. Every `ProgressEvent` published by any in-flight `tools/call` on
//! that MCP session is forwarded here as a single JSON text frame, matching
//! the server's `ProgressEvent` serialization:
//!
//! ```json
//! { "token": 7, "progress": 4, "total": 10, "message": "...",
//!   "tool": "notion", "timestamp": "2026-04-17T10:20:33+00:00" }
//! ```
//!
//! ## Why a gateway proxy (not direct SSE from the browser)?
//!
//! - Keeps all external traffic funneled through the gateway — single auth
//!   surface, no CORS friction, consistent with `/ws/terminal`.
//! - The gateway independently verifies its own bearer (`?token=<rk_…>` /
//!   `Authorization:`) using `PairingGuard`; the MCP session token
//!   (`?mcp_token=<…>`) is used only to talk to the in-process MCP server.

use super::AppState;
use super::mcp_discovery::read_revka_mcp;
use axum::{
    extract::{
        ConnectInfo, Query, State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    http::{HeaderMap, StatusCode, header},
    response::IntoResponse,
};
use futures_util::{SinkExt, StreamExt, stream::BoxStream};
use serde::Deserialize;
use std::net::SocketAddr;
use std::time::Duration;

const WS_PROTOCOL: &str = "revka.v1";
const BEARER_SUBPROTO_PREFIX: &str = "bearer.";

fn extract_ws_token<'a>(headers: &'a HeaderMap, query_token: Option<&'a str>) -> Option<&'a str> {
    if let Some(t) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|auth| auth.strip_prefix("Bearer "))
    {
        if !t.is_empty() {
            return Some(t);
        }
    }
    if let Some(t) = headers
        .get("sec-websocket-protocol")
        .and_then(|v| v.to_str().ok())
        .and_then(|protos| {
            protos
                .split(',')
                .map(|p| p.trim())
                .find_map(|p| p.strip_prefix(BEARER_SUBPROTO_PREFIX))
        })
    {
        if !t.is_empty() {
            return Some(t);
        }
    }
    if let Some(t) = query_token {
        if !t.is_empty() {
            return Some(t);
        }
    }
    None
}

#[derive(Deserialize, Default)]
pub struct McpEventsQuery {
    /// Gateway bearer token (same as `/ws/terminal`).
    pub token: Option<String>,
    /// MCP daemon session id (returned by `POST /session`).
    pub session_id: Option<String>,
    /// MCP daemon bearer token (returned by `POST /session`).
    pub mcp_token: Option<String>,
}

/// Map the discovery URL (which usually ends in `/mcp`) to the session-events
/// URL for the given session id.
pub fn daemon_events_url_from_discovery(discovery_url: &str, session_id: &str) -> String {
    let trimmed = discovery_url.trim_end_matches('/');
    let base = trimmed.strip_suffix("/mcp").unwrap_or(trimmed);
    format!("{base}/session/{session_id}/events")
}

/// Abstraction over "open the session-events SSE and yield each event's data
/// payload as a String". Real impl uses `reqwest`; tests inject a scripted
/// mock.
#[async_trait::async_trait]
pub trait McpEventsSource: Send + Sync {
    async fn open(
        &self,
        url: &str,
        mcp_token: &str,
    ) -> Result<BoxStream<'static, Result<String, String>>, String>;
}

/// Default source — opens the SSE stream via `reqwest`, parses the server
/// frames, and yields each `data: …` line's payload as a String.
pub struct ReqwestEventsSource;

#[async_trait::async_trait]
impl McpEventsSource for ReqwestEventsSource {
    async fn open(
        &self,
        url: &str,
        mcp_token: &str,
    ) -> Result<BoxStream<'static, Result<String, String>>, String> {
        let client = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .build()
            .map_err(|e| e.to_string())?;
        let resp = client
            .get(url)
            .header(header::AUTHORIZATION, format!("Bearer {mcp_token}"))
            .header(header::ACCEPT, "text/event-stream")
            .send()
            .await
            .map_err(|e| e.to_string())?;
        if !resp.status().is_success() {
            return Err(format!("daemon responded {}", resp.status()));
        }
        // Convert reqwest's byte stream (Result<Bytes, reqwest::Error>) into
        // Result<Vec<u8>, String>, then run the SSE framer.
        let byte_stream = resp
            .bytes_stream()
            .map(|r| r.map(|b| b.to_vec()).map_err(|e| e.to_string()));
        Ok(parse_sse_stream(byte_stream).boxed())
    }
}

/// Parse a stream of byte chunks into a stream of `data:` payload Strings.
///
/// Simplified SSE parser: events are blank-line terminated, only the `data:`
/// field is surfaced (`event:`, `id:`, `:comment`, etc. are ignored).
/// Multiple `data:` lines within one event are joined with `\n` per spec.
pub fn parse_sse_stream<S>(
    byte_stream: S,
) -> impl futures_util::Stream<Item = Result<String, String>> + Send + 'static
where
    S: futures_util::Stream<Item = Result<Vec<u8>, String>> + Send + 'static,
{
    use futures_util::stream::unfold;

    struct St {
        inner: BoxStream<'static, Result<Vec<u8>, String>>,
        buffer: String,
        data_accum: String,
        pending: std::collections::VecDeque<String>,
        done: bool,
    }

    let state = St {
        inner: byte_stream.boxed(),
        buffer: String::new(),
        data_accum: String::new(),
        pending: std::collections::VecDeque::new(),
        done: false,
    };

    unfold(state, |mut st| async move {
        // Flush already-queued events first (one per yield).
        if let Some(next) = st.pending.pop_front() {
            return Some((Ok(next), st));
        }
        if st.done {
            // Drain any trailing data accumulated without a blank line.
            if !st.data_accum.is_empty() {
                let out = std::mem::take(&mut st.data_accum);
                return Some((Ok(out), st));
            }
            return None;
        }
        // Pull more bytes until at least one event is flushed or EOF.
        loop {
            match st.inner.next().await {
                None => {
                    st.done = true;
                    if !st.data_accum.is_empty() {
                        let out = std::mem::take(&mut st.data_accum);
                        return Some((Ok(out), st));
                    }
                    return None;
                }
                Some(Err(e)) => {
                    st.done = true;
                    return Some((Err(e), st));
                }
                Some(Ok(bytes)) => {
                    st.buffer.push_str(&String::from_utf8_lossy(&bytes));
                    while let Some(idx) = st.buffer.find('\n') {
                        let line = st.buffer[..idx].trim_end_matches('\r').to_string();
                        st.buffer.drain(..=idx);
                        if line.is_empty() {
                            if !st.data_accum.is_empty() {
                                st.pending.push_back(std::mem::take(&mut st.data_accum));
                            }
                            continue;
                        }
                        if let Some(rest) = line.strip_prefix("data:") {
                            let payload = rest.strip_prefix(' ').unwrap_or(rest);
                            if !st.data_accum.is_empty() {
                                st.data_accum.push('\n');
                            }
                            st.data_accum.push_str(payload);
                        }
                        // Other fields ignored (event:, id:, retry:, :comment).
                    }
                    if let Some(next) = st.pending.pop_front() {
                        return Some((Ok(next), st));
                    }
                    // Keep pulling more bytes.
                }
            }
        }
    })
}

/// GET /ws/mcp/events — WebSocket upgrade for session-wide MCP progress.
pub async fn handle_ws_mcp_events(
    State(state): State<AppState>,
    ConnectInfo(peer_addr): ConnectInfo<SocketAddr>,
    Query(params): Query<McpEventsQuery>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> axum::response::Response {
    // Defense-in-depth: reject cross-site WebSocket handshakes (#383).
    if !super::ws::check_ws_origin(&headers) {
        return (
            StatusCode::FORBIDDEN,
            "Forbidden — cross-origin WebSocket upgrade rejected",
        )
            .into_response();
    }

    // Rate-limited against bearer-token brute force (#384).
    if state.pairing.require_pairing() {
        let limiter = super::api::WsAuthLimiter::new(&state, Some(peer_addr), &headers);
        if let Err(retry_after) = limiter.check() {
            return (
                StatusCode::TOO_MANY_REQUESTS,
                [(header::RETRY_AFTER, retry_after.to_string())],
                "Too many auth attempts — try again later",
            )
                .into_response();
        }

        let token = extract_ws_token(&headers, params.token.as_deref()).unwrap_or("");
        if !state.pairing.is_authenticated(token) {
            limiter.record_failure();
            return (StatusCode::UNAUTHORIZED, "Unauthorized").into_response();
        }
    }

    let Some(session_id) = params.session_id.clone().filter(|s| !s.is_empty()) else {
        return (StatusCode::BAD_REQUEST, "missing session_id").into_response();
    };
    let Some(mcp_token) = params.mcp_token.clone().filter(|s| !s.is_empty()) else {
        return (StatusCode::BAD_REQUEST, "missing mcp_token").into_response();
    };

    let discovery = match read_revka_mcp() {
        Ok(d) => d,
        Err(_) => {
            return (StatusCode::SERVICE_UNAVAILABLE, "mcp daemon not discovered").into_response();
        }
    };
    let events_url = daemon_events_url_from_discovery(&discovery.url, &session_id);

    let ws = if headers
        .get("sec-websocket-protocol")
        .and_then(|v| v.to_str().ok())
        .is_some_and(|protos| protos.split(',').any(|p| p.trim() == WS_PROTOCOL))
    {
        ws.protocols([WS_PROTOCOL])
    } else {
        ws
    };

    ws.on_upgrade(move |socket| async move {
        run_proxy(socket, events_url, mcp_token, Box::new(ReqwestEventsSource)).await;
    })
    .into_response()
}

/// Pipe every SSE `data:` payload from `source` into the WebSocket as a text
/// frame. Returns when either side closes.
pub async fn run_proxy(
    mut ws: WebSocket,
    events_url: String,
    mcp_token: String,
    source: Box<dyn McpEventsSource>,
) {
    let mut stream = match source.open(&events_url, &mcp_token).await {
        Ok(s) => s,
        Err(e) => {
            let _ = ws
                .send(Message::Text(
                    serde_json::json!({ "error": "daemon-unreachable", "detail": e })
                        .to_string()
                        .into(),
                ))
                .await;
            let _ = ws.close().await;
            return;
        }
    };

    loop {
        tokio::select! {
            incoming = ws.recv() => {
                match incoming {
                    Some(Ok(Message::Close(_))) | None => break,
                    Some(Err(_)) => break,
                    _ => { /* ignore client->server frames; this channel is server-push */ }
                }
            }
            next = stream.next() => {
                match next {
                    Some(Ok(payload)) => {
                        if ws.send(Message::Text(payload.into())).await.is_err() {
                            break;
                        }
                    }
                    Some(Err(_)) | None => {
                        let _ = ws.close().await;
                        break;
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use futures_util::stream;

    #[test]
    fn builds_events_url_from_mcp_discovery() {
        assert_eq!(
            daemon_events_url_from_discovery("http://127.0.0.1:54500/mcp", "sid-1"),
            "http://127.0.0.1:54500/session/sid-1/events"
        );
        assert_eq!(
            daemon_events_url_from_discovery("http://127.0.0.1:54500/mcp/", "sid-2"),
            "http://127.0.0.1:54500/session/sid-2/events"
        );
        assert_eq!(
            daemon_events_url_from_discovery("http://127.0.0.1:54500", "sid-3"),
            "http://127.0.0.1:54500/session/sid-3/events"
        );
    }

    #[tokio::test]
    async fn sse_parser_extracts_data_frames() {
        let chunks: Vec<Result<Vec<u8>, String>> = vec![
            Ok(b"data: {\"a\":1}\n\n".to_vec()),
            Ok(b"data: {\"b\":2}\n\n".to_vec()),
        ];
        let byte_stream = stream::iter(chunks);
        let parsed = parse_sse_stream(byte_stream);
        futures_util::pin_mut!(parsed);
        let first = parsed.next().await.unwrap().unwrap();
        let second = parsed.next().await.unwrap().unwrap();
        assert_eq!(first, "{\"a\":1}");
        assert_eq!(second, "{\"b\":2}");
    }

    #[tokio::test]
    async fn sse_parser_joins_multi_data_lines() {
        let chunks: Vec<Result<Vec<u8>, String>> =
            vec![Ok(b"data: line1\ndata: line2\n\n".to_vec())];
        let byte_stream = stream::iter(chunks);
        let parsed = parse_sse_stream(byte_stream);
        futures_util::pin_mut!(parsed);
        let joined = parsed.next().await.unwrap().unwrap();
        assert_eq!(joined, "line1\nline2");
    }

    #[tokio::test]
    async fn sse_parser_ignores_non_data_fields() {
        let chunks: Vec<Result<Vec<u8>, String>> = vec![Ok(
            b": heartbeat\nevent: progress\ndata: {\"k\":\"v\"}\n\n".to_vec(),
        )];
        let byte_stream = stream::iter(chunks);
        let parsed = parse_sse_stream(byte_stream);
        futures_util::pin_mut!(parsed);
        let payload = parsed.next().await.unwrap().unwrap();
        assert_eq!(payload, "{\"k\":\"v\"}");
    }

    #[tokio::test]
    async fn sse_parser_handles_chunk_boundaries_midline() {
        let chunks: Vec<Result<Vec<u8>, String>> =
            vec![Ok(b"data: {\"tok".to_vec()), Ok(b"en\":42}\n\n".to_vec())];
        let byte_stream = stream::iter(chunks);
        let parsed = parse_sse_stream(byte_stream);
        futures_util::pin_mut!(parsed);
        let payload = parsed.next().await.unwrap().unwrap();
        assert_eq!(payload, "{\"token\":42}");
    }

    // ── Source mock used by the proxy handler ─────────────────────────────

    struct ScriptedSource(Vec<Result<String, String>>);

    #[async_trait::async_trait]
    impl McpEventsSource for ScriptedSource {
        async fn open(
            &self,
            _url: &str,
            _mcp_token: &str,
        ) -> Result<BoxStream<'static, Result<String, String>>, String> {
            let items = self.0.clone();
            Ok(stream::iter(items).boxed())
        }
    }

    #[tokio::test]
    async fn source_abstraction_is_mockable_and_yields_frames() {
        let source = ScriptedSource(vec![
            Ok(r#"{"token":1,"progress":1,"timestamp":"t1"}"#.into()),
            Ok(r#"{"token":1,"progress":2,"timestamp":"t2"}"#.into()),
        ]);
        let mut stream = source
            .open("http://example/session/x/events", "token")
            .await
            .expect("open ok");
        let first = stream.next().await.unwrap().unwrap();
        let second = stream.next().await.unwrap().unwrap();
        assert!(first.contains("\"progress\":1"));
        assert!(second.contains("\"progress\":2"));
        assert!(stream.next().await.is_none());
    }

    #[tokio::test]
    async fn source_open_error_surfaces_to_caller() {
        struct FailingSource;
        #[async_trait::async_trait]
        impl McpEventsSource for FailingSource {
            async fn open(
                &self,
                _url: &str,
                _mcp_token: &str,
            ) -> Result<BoxStream<'static, Result<String, String>>, String> {
                Err("connection refused".into())
            }
        }
        let source = FailingSource;
        let err = match source.open("http://x", "t").await {
            Ok(_) => panic!("expected error"),
            Err(e) => e,
        };
        assert!(err.contains("connection refused"));
    }
}
