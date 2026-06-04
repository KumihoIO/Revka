//! Progress-notification hook for long-running tools.
//!
//! This module provides an additive, no-op-by-default surface that tools can
//! use to emit progress events during execution. Callers that care about
//! progress (e.g. a future MCP server wrapping Revka tools and forwarding
//! `notifications/progress`) can supply a real [`ProgressSink`]; everyone else
//! gets a [`NoopProgressSink`] and pays no cost.
//!
//! # Design
//!
//! The [`Tool`](crate::tools::Tool) trait gained a new default method,
//! [`Tool::execute_with_progress`], that takes a `&dyn ProgressSink` alongside
//! the existing `args`. Its default body simply calls
//! [`Tool::execute`](crate::tools::Tool::execute) and drops the sink, so the
//! ~93 existing tool implementations compile and behave exactly as before.
//! Tool authors that want to emit progress opt in by overriding
//! `execute_with_progress`.
//!
//! # MCP alignment
//!
//! The [`ProgressSink::notify`] shape (`progress_token`, `progress`,
//! `total: Option<u64>`, `message: Option<&str>`) mirrors the MCP
//! `notifications/progress` payload so that a future adapter can forward
//! events verbatim.

use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};

/// Opaque token identifying a single progress stream.
///
/// Tools receive (or allocate) a token and tag every [`ProgressSink::notify`]
/// call with it; MCP callers use it to correlate notifications with the
/// originating request. The representation is intentionally simple (`u64`)
/// so the hot path stays alloc-free.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct ProgressToken(pub u64);

impl ProgressToken {
    /// Raw token value — useful when serializing into an MCP payload.
    pub const fn value(self) -> u64 {
        self.0
    }
}

/// Sink that receives progress emissions from a running tool.
///
/// Implementations must be cheap to clone/share (`Send + Sync`) because the
/// same sink may be handed to many tool invocations concurrently. All methods
/// take `&self`; internal synchronization is the sink's responsibility.
pub trait ProgressSink: Send + Sync {
    /// Allocate a fresh progress token for a new operation.
    ///
    /// The default implementation hands out monotonically increasing tokens
    /// starting at 0; real sinks (e.g. MCP) will typically override this to
    /// mint tokens that match an inbound request ID.
    fn new_token(&self) -> ProgressToken {
        // Fall back to a process-local counter. Real sinks should override.
        static COUNTER: AtomicU64 = AtomicU64::new(0);
        ProgressToken(COUNTER.fetch_add(1, Ordering::Relaxed))
    }

    /// Emit a progress event.
    ///
    /// - `token`: correlates this event with an operation.
    /// - `progress`: current work completed (monotonically non-decreasing).
    /// - `total`: optional total units of work, if known up front.
    /// - `message`: optional human-readable status string.
    fn notify(
        &self,
        token: ProgressToken,
        progress: u64,
        total: Option<u64>,
        message: Option<&str>,
    );
}

/// Zero-cost sink used whenever a caller does not care about progress.
///
/// `notify` is a no-op and will be inlined away by the optimizer.
#[derive(Debug, Default, Clone, Copy)]
pub struct NoopProgressSink;

impl ProgressSink for NoopProgressSink {
    #[inline]
    fn notify(
        &self,
        _token: ProgressToken,
        _progress: u64,
        _total: Option<u64>,
        _message: Option<&str>,
    ) {
        // Intentionally empty.
    }
}

/// Process-wide default no-op sink, handed out when no sink is supplied.
///
/// Returned as `&'static dyn ProgressSink` so callers can pass it without
/// allocating.
pub fn noop_sink() -> &'static dyn ProgressSink {
    static SINK: NoopProgressSink = NoopProgressSink;
    &SINK
}

/// Ergonomic scoped handle: binds a [`ProgressToken`] to a [`ProgressSink`]
/// so tool code can emit without repeating the token argument.
///
/// Revka one at the top of a long-running tool's `execute_with_progress`
/// and call [`ProgressHandle::update`] as work proceeds.
pub struct ProgressHandle<'a> {
    sink: &'a dyn ProgressSink,
    token: ProgressToken,
    total: Option<u64>,
}

impl<'a> ProgressHandle<'a> {
    /// Create a handle for a new operation on `sink` with an optional known total.
    pub fn new(sink: &'a dyn ProgressSink, total: Option<u64>) -> Self {
        let token = sink.new_token();
        Self { sink, token, total }
    }

    /// Create a handle reusing an existing token (e.g. one supplied by the caller).
    pub fn with_token(
        sink: &'a dyn ProgressSink,
        token: ProgressToken,
        total: Option<u64>,
    ) -> Self {
        Self { sink, token, total }
    }

    /// Token backing this handle.
    pub fn token(&self) -> ProgressToken {
        self.token
    }

    /// Emit a progress update.
    pub fn update(&self, progress: u64, message: Option<&str>) {
        self.sink.notify(self.token, progress, self.total, message);
    }
}

/// Shared/owned sink alias, for callers that need to stash one in a struct.
pub type SharedProgressSink = Arc<dyn ProgressSink>;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::Tool;
    use crate::tools::traits::ToolResult;
    use async_trait::async_trait;
    use std::sync::Mutex;

    /// Recording sink used to assert that emissions really flow through the
    /// new trait entry point.
    #[derive(Default)]
    struct RecordingSink {
        events: Mutex<Vec<(u64, u64, Option<u64>, Option<String>)>>,
    }

    impl ProgressSink for RecordingSink {
        fn notify(
            &self,
            token: ProgressToken,
            progress: u64,
            total: Option<u64>,
            message: Option<&str>,
        ) {
            self.events.lock().unwrap().push((
                token.value(),
                progress,
                total,
                message.map(str::to_owned),
            ));
        }
    }

    #[test]
    fn noop_sink_swallows_emissions() {
        let sink = NoopProgressSink;
        // Should be safe to call arbitrarily many times without side effects.
        let t = sink.new_token();
        sink.notify(t, 0, Some(10), Some("starting"));
        sink.notify(t, 5, Some(10), None);
        sink.notify(t, 10, Some(10), Some("done"));

        // noop_sink() helper returns a usable static sink too.
        let s = noop_sink();
        s.notify(ProgressToken(42), 1, None, None);
    }

    /// Tool that overrides `execute_with_progress` and emits a couple of
    /// events. Used to prove the new entry point wires through to the sink.
    struct ProgressyTool;

    #[async_trait]
    impl Tool for ProgressyTool {
        fn name(&self) -> &str {
            "progressy"
        }
        fn description(&self) -> &str {
            "emits progress for tests"
        }
        fn parameters_schema(&self) -> serde_json::Value {
            serde_json::json!({ "type": "object" })
        }
        async fn execute(&self, _args: serde_json::Value) -> anyhow::Result<ToolResult> {
            // Non-progress path returns the same thing so behaviour is identical
            // when a caller skips the progress entry point.
            Ok(ToolResult {
                success: true,
                output: "ok".into(),
                error: None,
            })
        }
        async fn execute_with_progress(
            &self,
            args: serde_json::Value,
            sink: &dyn ProgressSink,
        ) -> anyhow::Result<ToolResult> {
            let handle = ProgressHandle::new(sink, Some(2));
            handle.update(1, Some("halfway"));
            handle.update(2, Some("finished"));
            self.execute(args).await
        }
    }

    #[tokio::test]
    async fn overridden_execute_with_progress_forwards_to_sink() {
        let sink = RecordingSink::default();
        let tool = ProgressyTool;

        let result = tool
            .execute_with_progress(serde_json::json!({}), &sink)
            .await
            .unwrap();

        assert!(result.success);
        let events = sink.events.lock().unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].1, 1);
        assert_eq!(events[0].2, Some(2));
        assert_eq!(events[0].3.as_deref(), Some("halfway"));
        assert_eq!(events[1].1, 2);
        assert_eq!(events[1].3.as_deref(), Some("finished"));
    }

    /// Tool that does NOT override `execute_with_progress`. The default impl
    /// must fall through to `execute` and never touch the sink.
    struct LegacyTool;

    #[async_trait]
    impl Tool for LegacyTool {
        fn name(&self) -> &str {
            "legacy"
        }
        fn description(&self) -> &str {
            "legacy tool, no progress"
        }
        fn parameters_schema(&self) -> serde_json::Value {
            serde_json::json!({ "type": "object" })
        }
        async fn execute(&self, _args: serde_json::Value) -> anyhow::Result<ToolResult> {
            Ok(ToolResult {
                success: true,
                output: "legacy-ok".into(),
                error: None,
            })
        }
    }

    #[tokio::test]
    async fn default_execute_with_progress_ignores_sink() {
        let sink = RecordingSink::default();
        let tool = LegacyTool;

        let result = tool
            .execute_with_progress(serde_json::json!({}), &sink)
            .await
            .unwrap();

        assert!(result.success);
        assert_eq!(result.output, "legacy-ok");
        assert!(sink.events.lock().unwrap().is_empty());
    }
}
