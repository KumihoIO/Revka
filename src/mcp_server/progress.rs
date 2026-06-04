//! `ProgressSink` implementation that publishes MCP `notifications/progress`
//! JSON-RPC messages onto a per-request channel. The HTTP handler wires the
//! receiver end into an SSE stream that ships events back to the client.
//!
//! Shape of the emitted JSON-RPC message follows the MCP spec:
//! ```json
//! {
//!   "jsonrpc": "2.0",
//!   "method": "notifications/progress",
//!   "params": {
//!     "progressToken": <u64>,
//!     "progress": <u64>,
//!     "total": <u64 | null>,
//!     "message": "<optional string>"
//!   }
//! }
//! ```
//!
//! ## Dual-fanout (M4)
//!
//! In addition to the per-request SSE channel, `McpProgressSink` can also
//! publish a `ProgressEvent` onto the session's shared broadcast channel
//! (see `session::SessionState::events`). That second fanout is what feeds
//! the V2 Code tab's inline status-card strip: the external CLI drives the
//! request, but the Revka UI reads the same events via the session-wide
//! stream regardless of who is talking to the daemon.
//!
//! Broadcast send errors (no live receivers) are intentionally ignored —
//! progress events are advisory and must never block a tool invocation.

use crate::mcp_server::session::ProgressEvent;
use crate::tools::progress::{ProgressSink, ProgressToken};
use serde_json::{Value, json};
use std::sync::atomic::{AtomicU64, Ordering};
use tokio::sync::broadcast;
use tokio::sync::mpsc::UnboundedSender;

/// Sink used for a single in-flight `tools/call` JSON-RPC request.
///
/// `tx` is the SSE stream's outbound channel. If `requested_token` is `Some`,
/// the client supplied a `progressToken` in the request's `_meta`; we reuse
/// it so the client can correlate events. Otherwise we mint one.
///
/// `session_events` is the session-wide broadcast sender (see [`super::session`]).
/// `tool_name` is stamped onto each `ProgressEvent` so session subscribers can
/// show which Revka tool is doing work (e.g. `notion`, `jira`, …).
pub struct McpProgressSink {
    tx: UnboundedSender<Value>,
    requested_token: Option<u64>,
    counter: AtomicU64,
    session_events: Option<broadcast::Sender<ProgressEvent>>,
    tool_name: Option<String>,
}

impl McpProgressSink {
    /// Per-request-only sink (no session broadcast). Kept for backwards
    /// compatibility with existing tests; production code should prefer
    /// [`Self::with_session`].
    pub fn new(tx: UnboundedSender<Value>, requested_token: Option<u64>) -> Self {
        Self {
            tx,
            requested_token,
            counter: AtomicU64::new(0),
            session_events: None,
            tool_name: None,
        }
    }

    /// Full-fat sink that fans out to both the per-request SSE stream and
    /// the session-wide broadcast channel.
    pub fn with_session(
        tx: UnboundedSender<Value>,
        requested_token: Option<u64>,
        session_events: broadcast::Sender<ProgressEvent>,
        tool_name: impl Into<String>,
    ) -> Self {
        Self {
            tx,
            requested_token,
            counter: AtomicU64::new(0),
            session_events: Some(session_events),
            tool_name: Some(tool_name.into()),
        }
    }
}

impl ProgressSink for McpProgressSink {
    fn new_token(&self) -> ProgressToken {
        if let Some(t) = self.requested_token {
            return ProgressToken(t);
        }
        ProgressToken(self.counter.fetch_add(1, Ordering::Relaxed))
    }

    fn notify(
        &self,
        token: ProgressToken,
        progress: u64,
        total: Option<u64>,
        message: Option<&str>,
    ) {
        let mut params = json!({
            "progressToken": token.value(),
            "progress": progress,
        });
        if let Some(total) = total {
            params["total"] = json!(total);
        }
        if let Some(msg) = message {
            params["message"] = json!(msg);
        }
        let envelope = json!({
            "jsonrpc": "2.0",
            "method": "notifications/progress",
            "params": params,
        });
        // Per-request SSE fanout — ignore send errors (client disconnected).
        let _ = self.tx.send(envelope);

        // Session-wide broadcast fanout — ignore errors (no subscribers is
        // the common, healthy case when no UI is attached).
        if let Some(bus) = &self.session_events {
            let ev = ProgressEvent::new(
                token.value(),
                progress,
                total,
                message.map(str::to_string),
                self.tool_name.clone(),
            );
            let _ = bus.send(ev);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    #[tokio::test]
    async fn emits_progress_envelope_with_requested_token() {
        let (tx, mut rx) = mpsc::unbounded_channel();
        let sink = McpProgressSink::new(tx, Some(42));
        let token = sink.new_token();
        assert_eq!(token.value(), 42);
        sink.notify(token, 1, Some(3), Some("one of three"));
        let evt = rx.recv().await.unwrap();
        assert_eq!(evt["method"], "notifications/progress");
        assert_eq!(evt["params"]["progressToken"], 42);
        assert_eq!(evt["params"]["progress"], 1);
        assert_eq!(evt["params"]["total"], 3);
        assert_eq!(evt["params"]["message"], "one of three");
    }

    #[tokio::test]
    async fn mints_token_when_none_supplied() {
        let (tx, _rx) = mpsc::unbounded_channel();
        let sink = McpProgressSink::new(tx, None);
        let a = sink.new_token();
        let b = sink.new_token();
        assert_ne!(a.value(), b.value());
    }

    #[tokio::test]
    async fn dual_fanout_publishes_to_session_broadcast() {
        let (tx, _rx) = mpsc::unbounded_channel();
        let (bus_tx, mut bus_rx) = broadcast::channel(8);
        let sink = McpProgressSink::with_session(tx, Some(9), bus_tx, "notion");
        sink.notify(sink.new_token(), 2, Some(5), Some("doing a thing"));
        let ev = bus_rx.recv().await.unwrap();
        assert_eq!(ev.token, 9);
        assert_eq!(ev.progress, 2);
        assert_eq!(ev.total, Some(5));
        assert_eq!(ev.message.as_deref(), Some("doing a thing"));
        assert_eq!(ev.tool.as_deref(), Some("notion"));
        assert!(!ev.timestamp.is_empty());
    }

    #[tokio::test]
    async fn dual_fanout_swallows_no_subscriber_error() {
        let (tx, _rx) = mpsc::unbounded_channel();
        let (bus_tx, bus_rx) = broadcast::channel(8);
        drop(bus_rx); // no live receivers
        let sink = McpProgressSink::with_session(tx, None, bus_tx, "notion");
        // Must not panic / error visibly.
        sink.notify(sink.new_token(), 1, None, None);
    }
}
