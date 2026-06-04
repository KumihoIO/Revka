//! Session state for the MCP daemon.
//!
//! Each connected client (one external CLI process) creates a session via
//! `POST /session`, receives a `{ session_id, token }` pair, and attaches
//! both headers on every JSON-RPC call. Sessions hold their own cwd and
//! some future-proof scaffolding (allowed-tools filter, created-at stamp).
//! Storage is a `tokio::sync::RwLock<HashMap<...>>` — Send+Sync, no deps.
//!
//! ## Session-wide progress broadcast (M4)
//!
//! In addition to the per-request SSE stream that `/mcp` tools/call uses to
//! ship progress events back to the caller, we also publish each event onto
//! a per-session `tokio::sync::broadcast` channel. Any subscriber holding
//! the session token can tap that stream via `GET /session/<id>/events` to
//! observe *every* Revka tool's progress for that session in real time —
//! which is how the V2 Code tab surfaces "what Revka is doing right now"
//! while an external CLI is mid tools/call.
//!
//! Broadcast capacity is small (64). Slow consumers simply miss frames
//! (broadcast::Receiver returns `Lagged`); progress events are advisory,
//! never load-bearing for correctness.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::{RwLock, broadcast};
use uuid::Uuid;

/// Capacity of the per-session broadcast channel. 64 is enough for a burst of
/// progress events without filling memory; slow subscribers will see `Lagged`
/// and simply skip frames (acceptable — progress is advisory, not a log).
const BROADCAST_CAPACITY: usize = 64;

/// Session-wide progress event published to any subscriber of
/// `/session/<id>/events`. Mirrors the per-request `notifications/progress`
/// payload with additional `tool` + `timestamp` fields so subscribers can
/// render "Notion — 4/10 at 10:20:33" without having to correlate tokens
/// back to the originating request.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgressEvent {
    pub token: u64,
    pub progress: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub tool: Option<String>,
    /// RFC3339 timestamp (UTC) — frontend renders this as "Ns ago".
    pub timestamp: String,
}

impl ProgressEvent {
    /// Convenience constructor using the current wall clock.
    pub fn new(
        token: u64,
        progress: u64,
        total: Option<u64>,
        message: Option<String>,
        tool: Option<String>,
    ) -> Self {
        Self {
            token,
            progress,
            total,
            message,
            tool,
            timestamp: Utc::now().to_rfc3339(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct SessionState {
    pub id: String,
    pub token: String,
    pub cwd: PathBuf,
    pub label: Option<String>,
    pub created_at: DateTime<Utc>,
    /// Sender half of the per-session progress broadcast. Clone to subscribe.
    pub events: broadcast::Sender<ProgressEvent>,
}

#[derive(Debug, Default)]
pub struct SessionStore {
    // session_id -> SessionState
    inner: RwLock<HashMap<String, SessionState>>,
}

impl SessionStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub async fn create(&self, cwd: PathBuf, label: Option<String>) -> SessionState {
        let id = Uuid::new_v4().to_string();
        let token = Uuid::new_v4().simple().to_string();
        let (events_tx, _) = broadcast::channel(BROADCAST_CAPACITY);
        let state = SessionState {
            id: id.clone(),
            token,
            cwd,
            label,
            created_at: Utc::now(),
            events: events_tx,
        };
        self.inner.write().await.insert(id, state.clone());
        state
    }

    /// Return the session iff the `(session_id, token)` pair matches one on file.
    pub async fn authenticate(&self, session_id: &str, token: &str) -> Option<SessionState> {
        let guard = self.inner.read().await;
        guard
            .get(session_id)
            .filter(|s| constant_time_eq(s.token.as_bytes(), token.as_bytes()))
            .cloned()
    }

    /// Look up a session's broadcast sender by id (no auth). Used by the
    /// `/session/<id>/events` handler after it has independently verified
    /// the bearer token via `authenticate`.
    pub async fn event_sender(&self, session_id: &str) -> Option<broadcast::Sender<ProgressEvent>> {
        let guard = self.inner.read().await;
        guard.get(session_id).map(|s| s.events.clone())
    }

    pub async fn len(&self) -> usize {
        self.inner.read().await.len()
    }
}

pub type SharedSessionStore = Arc<SessionStore>;

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff: u8 = 0;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn create_and_authenticate_happy_path() {
        let store = SessionStore::new();
        let sess = store
            .create(PathBuf::from("/tmp"), Some("test".into()))
            .await;
        assert_eq!(store.len().await, 1);
        let found = store.authenticate(&sess.id, &sess.token).await;
        assert!(found.is_some());
        assert_eq!(found.unwrap().cwd, PathBuf::from("/tmp"));
    }

    #[tokio::test]
    async fn authenticate_rejects_wrong_token() {
        let store = SessionStore::new();
        let sess = store.create(PathBuf::from("/tmp"), None).await;
        let found = store.authenticate(&sess.id, "not-the-token").await;
        assert!(found.is_none());
    }

    #[tokio::test]
    async fn authenticate_rejects_unknown_session() {
        let store = SessionStore::new();
        let _ = store.create(PathBuf::from("/tmp"), None).await;
        let found = store.authenticate("not-a-session-id", "anything").await;
        assert!(found.is_none());
    }

    #[tokio::test]
    async fn session_broadcast_delivers_published_event() {
        let store = SessionStore::new();
        let sess = store.create(PathBuf::from("/tmp"), None).await;

        // Subscribe BEFORE sending — broadcast drops messages with no live
        // receivers.
        let mut rx = sess.events.subscribe();

        let ev = ProgressEvent::new(7, 1, Some(3), Some("hello".into()), Some("notion".into()));
        sess.events.send(ev.clone()).expect("send ok");

        let got = rx.recv().await.expect("recv ok");
        assert_eq!(got.token, 7);
        assert_eq!(got.progress, 1);
        assert_eq!(got.total, Some(3));
        assert_eq!(got.message.as_deref(), Some("hello"));
        assert_eq!(got.tool.as_deref(), Some("notion"));
    }

    #[tokio::test]
    async fn event_sender_lookup_returns_same_channel() {
        let store = SessionStore::new();
        let sess = store.create(PathBuf::from("/tmp"), None).await;
        let tx = store.event_sender(&sess.id).await.expect("sender present");
        let mut rx = tx.subscribe();
        let ev = ProgressEvent::new(1, 1, None, None, None);
        sess.events.send(ev).expect("send ok");
        let got = rx.recv().await.expect("recv ok");
        assert_eq!(got.token, 1);
    }

    #[tokio::test]
    async fn event_sender_unknown_session_returns_none() {
        let store = SessionStore::new();
        assert!(store.event_sender("nope").await.is_none());
    }

    #[tokio::test]
    async fn broadcast_with_no_subscribers_is_not_an_error_to_caller() {
        // We surface this as "send may return Err, caller ignores it". This
        // test documents the expected shape without treating it as fatal.
        let store = SessionStore::new();
        let sess = store.create(PathBuf::from("/tmp"), None).await;
        let res = sess.events.send(ProgressEvent::new(0, 0, None, None, None));
        // No subscribers → send returns Err(SendError). That's fine; the
        // daemon's progress sink ignores this return value by design.
        assert!(res.is_err());
    }
}
