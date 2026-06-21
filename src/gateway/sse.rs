//! Server-Sent Events (SSE) stream for real-time event delivery.
//!
//! Wraps the broadcast channel in AppState to deliver events to web dashboard clients.

use super::AppState;
use axum::{
    extract::State,
    http::{HeaderMap, StatusCode, header},
    response::{
        IntoResponse,
        sse::{Event, KeepAlive, Sse},
    },
};
use std::convert::Infallible;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncSeekExt, BufReader, SeekFrom};
use tokio_stream::StreamExt;
use tokio_stream::wrappers::{BroadcastStream, ReceiverStream};

/// GET /api/events — SSE event stream
pub async fn handle_sse_events(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    // Auth check
    if state.pairing.require_pairing() {
        let token = headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|auth| auth.strip_prefix("Bearer "))
            .unwrap_or("");

        if !state.pairing.is_authenticated(token) {
            return (
                StatusCode::UNAUTHORIZED,
                "Unauthorized — provide Authorization: Bearer <token>",
            )
                .into_response();
        }
    }

    let rx = state.event_tx.subscribe();
    let stream = BroadcastStream::new(rx).filter_map(
        |result: Result<
            serde_json::Value,
            tokio_stream::wrappers::errors::BroadcastStreamRecvError,
        >| {
            match result {
                Ok(value) => Some(Ok::<_, Infallible>(
                    Event::default().data(value.to_string()),
                )),
                Err(_) => None, // Skip lagged messages
            }
        },
    );

    Sse::new(stream)
        .keep_alive(KeepAlive::default())
        .into_response()
}

/// GET /api/daemon/logs — tails the daemon stderr log file and streams lines as SSE events.
///
/// Emits an initial burst of the last ~200 lines, then polls every 500ms for appended bytes.
/// Handles log rotation/truncation by resetting to file start when the file shrinks.
pub async fn handle_api_daemon_logs(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if state.pairing.require_pairing() {
        let token = headers
            .get(header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok())
            .and_then(|auth| auth.strip_prefix("Bearer "))
            .unwrap_or("");

        if !state.pairing.is_authenticated(token) {
            return (
                StatusCode::UNAUTHORIZED,
                "Unauthorized — provide Authorization: Bearer <token>",
            )
                .into_response();
        }
    }

    let log_path = {
        let cfg = state.config.lock();
        cfg.config_path
            .parent()
            .map(|dir| dir.join("logs").join("daemon.stderr.log"))
    };

    let Some(log_path) = log_path else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            "Unable to resolve daemon log path",
        )
            .into_response();
    };

    let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Infallible>>(256);

    tokio::spawn(async move {
        const TAIL_BYTES: u64 = 64 * 1024; // initial burst — last ~64KB

        // Initial tail burst.
        let mut start_pos: u64 = match tokio::fs::File::open(&log_path).await {
            Ok(mut file) => {
                let size = file.metadata().await.map(|m| m.len()).unwrap_or(0);
                let seek_to = size.saturating_sub(TAIL_BYTES);
                if file.seek(SeekFrom::Start(seek_to)).await.is_ok() {
                    let mut reader = BufReader::new(file);
                    // If we seeked into the middle of a line, discard the partial prefix.
                    if seek_to > 0 {
                        let mut discard = String::new();
                        let _ = reader.read_line(&mut discard).await;
                    }
                    let mut line = String::new();
                    loop {
                        line.clear();
                        match reader.read_line(&mut line).await {
                            Ok(0) => break,
                            Ok(_) => {
                                let trimmed = line.trim_end_matches(['\n', '\r']).to_string();
                                if trimmed.is_empty() {
                                    continue;
                                }
                                let payload = serde_json::json!({
                                    "type": "log",
                                    "line": trimmed,
                                    "timestamp": chrono::Utc::now().to_rfc3339(),
                                });
                                let event = Event::default().data(payload.to_string());
                                if tx.send(Ok(event)).await.is_err() {
                                    return;
                                }
                            }
                            Err(_) => break,
                        }
                    }
                }
                size
            }
            Err(_) => {
                let payload = serde_json::json!({
                    "type": "log_unavailable",
                    "line": format!("daemon log not readable at {}", log_path.display()),
                    "timestamp": chrono::Utc::now().to_rfc3339(),
                });
                let _ = tx
                    .send(Ok(Event::default().data(payload.to_string())))
                    .await;
                0
            }
        };

        // Poll loop for appended bytes.
        let mut interval = tokio::time::interval(Duration::from_millis(500));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            interval.tick().await;

            let meta = match tokio::fs::metadata(&log_path).await {
                Ok(m) => m,
                Err(_) => continue, // log may not exist yet; keep polling
            };
            let size = meta.len();

            if size < start_pos {
                // Log was rotated or truncated — restart from beginning.
                start_pos = 0;
            }
            if size == start_pos {
                continue;
            }

            let mut file = match tokio::fs::File::open(&log_path).await {
                Ok(f) => f,
                Err(_) => continue,
            };
            if file.seek(SeekFrom::Start(start_pos)).await.is_err() {
                continue;
            }
            let mut reader = BufReader::new(file);
            let mut line = String::new();
            loop {
                line.clear();
                match reader.read_line(&mut line).await {
                    Ok(0) => break,
                    Ok(n) => {
                        start_pos = start_pos.saturating_add(n as u64);
                        let trimmed = line.trim_end_matches(['\n', '\r']).to_string();
                        if trimmed.is_empty() {
                            continue;
                        }
                        let payload = serde_json::json!({
                            "type": "log",
                            "line": trimmed,
                            "timestamp": chrono::Utc::now().to_rfc3339(),
                        });
                        let event = Event::default().data(payload.to_string());
                        if tx.send(Ok(event)).await.is_err() {
                            return;
                        }
                    }
                    Err(_) => break,
                }
            }
        }
    });

    Sse::new(ReceiverStream::new(rx))
        .keep_alive(KeepAlive::default())
        .into_response()
}

/// Broadcast observer that forwards events to the SSE broadcast channel.
pub struct BroadcastObserver {
    inner: std::sync::Arc<dyn crate::observability::Observer>,
    tx: tokio::sync::broadcast::Sender<serde_json::Value>,
}

impl BroadcastObserver {
    pub fn new(
        inner: std::sync::Arc<dyn crate::observability::Observer>,
        tx: tokio::sync::broadcast::Sender<serde_json::Value>,
    ) -> Self {
        Self { inner, tx }
    }

    pub fn inner(&self) -> &dyn crate::observability::Observer {
        self.inner.as_ref()
    }
}

impl crate::observability::Observer for BroadcastObserver {
    fn record_event(&self, event: &crate::observability::ObserverEvent) {
        // Forward to inner observer
        self.inner.record_event(event);

        // Broadcast to SSE subscribers
        let json = match event {
            crate::observability::ObserverEvent::LlmRequest {
                provider, model, ..
            } => serde_json::json!({
                "type": "llm_request",
                "provider": provider,
                "model": model,
                "timestamp": chrono::Utc::now().to_rfc3339(),
            }),
            crate::observability::ObserverEvent::ToolCall {
                tool,
                duration,
                success,
            } => serde_json::json!({
                "type": "tool_call",
                "tool": tool,
                "duration_ms": duration.as_millis(),
                "success": success,
                "timestamp": chrono::Utc::now().to_rfc3339(),
            }),
            crate::observability::ObserverEvent::ToolCallStart { tool, .. } => serde_json::json!({
                "type": "tool_call_start",
                "tool": tool,
                "timestamp": chrono::Utc::now().to_rfc3339(),
            }),
            crate::observability::ObserverEvent::Error { component, message } => {
                serde_json::json!({
                    "type": "error",
                    "component": component,
                    "message": message,
                    "timestamp": chrono::Utc::now().to_rfc3339(),
                })
            }
            crate::observability::ObserverEvent::AgentStart { provider, model } => {
                serde_json::json!({
                    "type": "agent_start",
                    "provider": provider,
                    "model": model,
                    "timestamp": chrono::Utc::now().to_rfc3339(),
                })
            }
            crate::observability::ObserverEvent::AgentEnd {
                provider,
                model,
                duration,
                tokens_used,
                cost_usd,
            } => serde_json::json!({
                "type": "agent_end",
                "provider": provider,
                "model": model,
                "duration_ms": duration.as_millis(),
                "tokens_used": tokens_used,
                "cost_usd": cost_usd,
                "timestamp": chrono::Utc::now().to_rfc3339(),
            }),
            _ => return, // Skip events we don't broadcast
        };

        let _ = self.tx.send(json);
    }

    fn record_metric(&self, metric: &crate::observability::traits::ObserverMetric) {
        self.inner.record_metric(metric);
    }

    fn flush(&self) {
        self.inner.flush();
    }

    fn name(&self) -> &str {
        "broadcast"
    }

    fn as_any(&self) -> &dyn std::any::Any {
        self
    }
}
