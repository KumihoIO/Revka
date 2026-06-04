//! Thin wrapper that adds `start` + `finish` progress notifications around an
//! underlying tool's `execute()` body.
//!
//! This lets the MCP daemon emit `notifications/progress` events for a
//! handful of long-running integration tools (Notion search, Google Drive
//! list, etc.) **without modifying those tool implementations**. The wrapper
//! delegates name/description/schema unchanged so the MCP surface sees the
//! same tool spec as before; only the in-flight progress stream is richer.
//!
//! The wrapper uses a 2-event model (start → complete) because Revka's
//! integration tools run as a single HTTP request under the hood — we don't
//! have paginated internals to tick against. That's still more useful than
//! silence: CLIs can show a spinner/toast the moment the call kicks off.

use crate::tools::progress::{ProgressHandle, ProgressSink};
use crate::tools::traits::{Tool, ToolResult};
use async_trait::async_trait;
use std::sync::Arc;

/// Wrap a `Tool` so that `execute_with_progress` emits bookend events.
pub struct ProgressEnvelope {
    inner: Arc<dyn Tool>,
    start_message: String,
    finish_message: String,
}

impl ProgressEnvelope {
    pub fn new(inner: Arc<dyn Tool>, start_message: &str, finish_message: &str) -> Self {
        Self {
            inner,
            start_message: start_message.to_string(),
            finish_message: finish_message.to_string(),
        }
    }

    /// Convenience: wrap into an `Arc<dyn Tool>` suitable for the registry.
    pub fn into_arc(self) -> Arc<dyn Tool> {
        Arc::new(self)
    }
}

#[async_trait]
impl Tool for ProgressEnvelope {
    fn name(&self) -> &str {
        self.inner.name()
    }

    fn description(&self) -> &str {
        self.inner.description()
    }

    fn parameters_schema(&self) -> serde_json::Value {
        self.inner.parameters_schema()
    }

    async fn execute(&self, args: serde_json::Value) -> anyhow::Result<ToolResult> {
        self.inner.execute(args).await
    }

    async fn execute_with_progress(
        &self,
        args: serde_json::Value,
        sink: &dyn ProgressSink,
    ) -> anyhow::Result<ToolResult> {
        let handle = ProgressHandle::new(sink, Some(2));
        handle.update(1, Some(&self.start_message));
        let result = self.inner.execute(args).await;
        handle.update(2, Some(&self.finish_message));
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::progress::ProgressToken;
    use std::sync::Mutex;

    #[derive(Default)]
    struct RecordingSink {
        events: Mutex<Vec<(u64, Option<String>)>>,
    }
    impl ProgressSink for RecordingSink {
        fn notify(
            &self,
            _t: ProgressToken,
            progress: u64,
            _total: Option<u64>,
            message: Option<&str>,
        ) {
            self.events
                .lock()
                .unwrap()
                .push((progress, message.map(str::to_string)));
        }
    }

    struct StubTool;
    #[async_trait]
    impl Tool for StubTool {
        fn name(&self) -> &str {
            "stub"
        }
        fn description(&self) -> &str {
            "stub"
        }
        fn parameters_schema(&self) -> serde_json::Value {
            serde_json::json!({ "type": "object" })
        }
        async fn execute(&self, _args: serde_json::Value) -> anyhow::Result<ToolResult> {
            Ok(ToolResult {
                success: true,
                output: "inner".into(),
                error: None,
            })
        }
    }

    #[tokio::test]
    async fn envelope_emits_bookend_progress() {
        let sink = RecordingSink::default();
        let wrapped = ProgressEnvelope::new(Arc::new(StubTool), "starting", "done");
        let r = wrapped
            .execute_with_progress(serde_json::json!({}), &sink)
            .await
            .unwrap();
        assert_eq!(r.output, "inner");
        let events = sink.events.lock().unwrap();
        assert_eq!(events.len(), 2);
        assert_eq!(events[0], (1, Some("starting".into())));
        assert_eq!(events[1], (2, Some("done".into())));
    }

    #[tokio::test]
    async fn envelope_forwards_spec_and_name() {
        let wrapped = ProgressEnvelope::new(Arc::new(StubTool), "a", "b");
        assert_eq!(wrapped.name(), "stub");
        assert!(!wrapped.parameters_schema().is_null());
    }
}
