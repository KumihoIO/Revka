//! Per-session actor queue for serializing concurrent access.
//!
//! Each session gets at most one concurrent turn. Additional requests queue up
//! (bounded by `max_queue_depth`) and proceed in FIFO order. This prevents
//! SQLite history corruption from overlapping writes and ensures consistent
//! session state transitions.

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Duration;

use tokio::sync::{Mutex, OwnedSemaphorePermit, Semaphore};
use tokio::time::Instant;

/// Default per-session lock acquisition timeout in seconds. Set high (5 min)
/// because Operator turns can run long — web search, sub-agent delegation,
/// and workflow building routinely take minutes. Override via the
/// `REVKA_GATEWAY_SESSION_LOCK_TIMEOUT_SECS` env var.
pub const SESSION_LOCK_TIMEOUT_SECS_DEFAULT: u64 = 300;

/// Read the per-session lock timeout from
/// `REVKA_GATEWAY_SESSION_LOCK_TIMEOUT_SECS` at runtime, falling back to
/// [`SESSION_LOCK_TIMEOUT_SECS_DEFAULT`]. Invalid (non-numeric or zero)
/// values log a warning and use the default.
pub fn session_lock_timeout_secs() -> u64 {
    const VAR: &str = "REVKA_GATEWAY_SESSION_LOCK_TIMEOUT_SECS";
    match std::env::var(VAR) {
        Ok(raw) => match raw.parse::<u64>() {
            Ok(n) if n > 0 => n,
            _ => {
                tracing::warn!(
                    target: "gateway",
                    env_var = VAR,
                    value = %raw,
                    default = SESSION_LOCK_TIMEOUT_SECS_DEFAULT,
                    "invalid session lock timeout — falling back to default"
                );
                SESSION_LOCK_TIMEOUT_SECS_DEFAULT
            }
        },
        Err(_) => SESSION_LOCK_TIMEOUT_SECS_DEFAULT,
    }
}

/// Per-session serialization queue.
pub struct SessionActorQueue {
    slots: Mutex<HashMap<String, Arc<SessionSlot>>>,
    max_queue_depth: usize,
    lock_timeout: Duration,
    idle_ttl: Duration,
}

struct SessionSlot {
    semaphore: Arc<Semaphore>,
    last_active: Mutex<Instant>,
    pending: AtomicUsize,
}

/// RAII guard that releases the session permit on drop.
pub struct SessionGuard {
    slot: Arc<SessionSlot>,
    _permit: OwnedSemaphorePermit,
}

impl Drop for SessionGuard {
    fn drop(&mut self) {
        self.slot.pending.fetch_sub(1, Ordering::Relaxed);
    }
}

/// Errors from the session queue.
#[derive(Debug)]
pub enum SessionQueueError {
    /// Too many requests queued for this session.
    QueueFull { session_id: String, depth: usize },
    /// Timed out waiting for the session lock. The `timeout_secs` is included
    /// in the user-facing message so operators know which knob they hit.
    Timeout {
        session_id: String,
        timeout_secs: u64,
    },
}

impl std::fmt::Display for SessionQueueError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::QueueFull { session_id, depth } => {
                write!(
                    f,
                    "Session {session_id} queue full ({depth} pending requests)"
                )
            }
            Self::Timeout { timeout_secs, .. } => {
                write!(
                    f,
                    "Previous message is still being processed — please wait, or retry once it completes (timeout: {timeout_secs}s)"
                )
            }
        }
    }
}

impl std::error::Error for SessionQueueError {}

impl SessionActorQueue {
    /// Create a new queue with the given limits.
    pub fn new(max_queue_depth: usize, lock_timeout_secs: u64, idle_ttl_secs: u64) -> Self {
        Self {
            slots: Mutex::new(HashMap::new()),
            max_queue_depth,
            lock_timeout: Duration::from_secs(lock_timeout_secs),
            idle_ttl: Duration::from_secs(idle_ttl_secs),
        }
    }

    /// Acquire exclusive access to a session. Blocks until the session is free
    /// or the timeout expires. Returns a guard that releases on drop.
    pub async fn acquire(&self, session_id: &str) -> Result<SessionGuard, SessionQueueError> {
        // Reserve our slot under the `slots` lock: get-or-insert the slot AND
        // bump `pending` before releasing the lock. This makes the slot
        // observably in-flight (`pending > 0`) the instant it becomes visible
        // to `evict_idle`, closing the window where a concurrent eviction could
        // drop a freshly-inserted slot out from under us and hand a later
        // acquirer a different semaphore for the same session.
        let slot = {
            let mut slots = self.slots.lock().await;
            let slot = slots
                .entry(session_id.to_string())
                .or_insert_with(|| {
                    Arc::new(SessionSlot {
                        semaphore: Arc::new(Semaphore::new(1)),
                        last_active: Mutex::new(Instant::now()),
                        pending: AtomicUsize::new(0),
                    })
                })
                .clone();

            // Check queue depth and reserve a pending slot while still holding
            // the `slots` lock so eviction can't race the reservation.
            let current = slot.pending.fetch_add(1, Ordering::Relaxed);
            if current >= self.max_queue_depth {
                slot.pending.fetch_sub(1, Ordering::Relaxed);
                return Err(SessionQueueError::QueueFull {
                    session_id: session_id.to_string(),
                    depth: current,
                });
            }
            slot
        };

        // Acquire owned permit with timeout
        let sem = slot.semaphore.clone();
        match tokio::time::timeout(self.lock_timeout, sem.acquire_owned()).await {
            Ok(Ok(permit)) => {
                *slot.last_active.lock().await = Instant::now();
                Ok(SessionGuard {
                    slot,
                    _permit: permit,
                })
            }
            Ok(Err(_)) | Err(_) => {
                slot.pending.fetch_sub(1, Ordering::Relaxed);
                Err(SessionQueueError::Timeout {
                    session_id: session_id.to_string(),
                    timeout_secs: self.lock_timeout.as_secs(),
                })
            }
        }
    }

    /// Get the number of pending requests for a session.
    pub async fn queue_depth(&self, session_id: &str) -> usize {
        let slots = self.slots.lock().await;
        slots
            .get(session_id)
            .map(|s| s.pending.load(Ordering::Relaxed))
            .unwrap_or(0)
    }

    /// Remove idle session slots that haven't been accessed within the TTL.
    pub async fn evict_idle(&self) -> usize {
        let mut slots = self.slots.lock().await;
        let now = Instant::now();
        let before = slots.len();
        let ttl = self.idle_ttl;

        let mut to_remove = Vec::new();
        for (key, slot) in slots.iter() {
            let last = *slot.last_active.lock().await;
            if now.duration_since(last) > ttl && slot.pending.load(Ordering::Relaxed) == 0 {
                to_remove.push(key.clone());
            }
        }
        for key in &to_remove {
            slots.remove(key);
        }

        before - slots.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn serializes_same_session() {
        let queue = SessionActorQueue::new(8, 5, 600);

        // Acquire and release, then re-acquire should work
        let guard1 = queue.acquire("s1").await.unwrap();
        drop(guard1);
        let _guard2 = queue.acquire("s1").await.unwrap();
    }

    #[tokio::test]
    async fn parallel_different_sessions() {
        let queue = SessionActorQueue::new(8, 5, 600);
        let _guard1 = queue.acquire("s1").await.unwrap();
        let _guard2 = queue.acquire("s2").await.unwrap();
        // Both acquired simultaneously — different sessions don't block each other
    }

    #[tokio::test]
    async fn queue_depth_limit() {
        let queue = Arc::new(SessionActorQueue::new(2, 30, 600));

        // Hold the session lock (pending=1)
        let guard = queue.acquire("s1").await.unwrap();

        // Queue one more (pending=2, will block waiting for permit)
        let queue_clone = queue.clone();
        let handle = tokio::spawn(async move { queue_clone.acquire("s1").await });

        // Give the spawned task time to register
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Third request should be rejected (pending=2 >= max=2)
        let result = queue.acquire("s1").await;
        assert!(matches!(result, Err(SessionQueueError::QueueFull { .. })));

        drop(guard);
        let _ = handle.await;
    }

    #[tokio::test]
    async fn timeout_returns_error() {
        let queue = SessionActorQueue::new(8, 1, 600);
        let _guard = queue.acquire("s1").await.unwrap();

        let start = Instant::now();
        let result = queue.acquire("s1").await;
        assert!(matches!(result, Err(SessionQueueError::Timeout { .. })));
        assert!(start.elapsed() >= Duration::from_millis(900));
    }

    #[tokio::test]
    async fn timeout_error_message_mentions_timeout_value() {
        let queue = SessionActorQueue::new(8, 1, 600);
        let _guard = queue.acquire("s1").await.unwrap();
        let msg = match queue.acquire("s1").await {
            Err(e) => e.to_string(),
            Ok(_) => panic!("expected timeout error"),
        };
        // No leaked session id; includes timeout value and guidance.
        assert!(!msg.contains("s1"), "session id leaked: {msg}");
        assert!(msg.contains("timeout: 1s"), "missing timeout value: {msg}");
        assert!(
            msg.contains("still being processed"),
            "missing guidance: {msg}"
        );
    }

    #[tokio::test]
    async fn idle_eviction() {
        let queue = SessionActorQueue::new(8, 5, 0); // 0s TTL
        {
            let _guard = queue.acquire("s1").await.unwrap();
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
        let evicted = queue.evict_idle().await;
        assert_eq!(evicted, 1);
    }

    #[tokio::test]
    async fn evict_skips_slot_with_in_flight_acquire() {
        // A slot held by a live guard must never be evicted, even with a 0s
        // TTL: its `pending` count is non-zero. This guards the lock-ordering
        // fix — the reservation is visible to `evict_idle` before the `slots`
        // lock is released.
        let queue = SessionActorQueue::new(8, 5, 0); // 0s TTL
        let guard = queue.acquire("s1").await.unwrap();
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert_eq!(queue.evict_idle().await, 0, "in-flight slot was evicted");
        drop(guard);
    }

    #[tokio::test]
    async fn concurrent_acquirers_share_one_semaphore_under_eviction() {
        // Hammer acquire/evict for the same key with a 0s TTL. If eviction ever
        // races a reservation and re-creates the slot under a fresh semaphore,
        // two acquirers could hold the lock at once and `serialized` would
        // exceed 1. The lock-ordering fix must keep every acquire mutually
        // exclusive regardless of eviction.
        // Queue depth comfortably above the worker count so acquires queue
        // rather than getting rejected with QueueFull — the invariant under
        // test is mutual exclusion, not the depth limit.
        let queue = Arc::new(SessionActorQueue::new(64, 5, 0)); // 0s TTL
        let in_critical = Arc::new(AtomicUsize::new(0));
        let max_seen = Arc::new(AtomicUsize::new(0));

        let mut handles = Vec::new();
        for _ in 0..16 {
            let q = queue.clone();
            let in_critical = in_critical.clone();
            let max_seen = max_seen.clone();
            handles.push(tokio::spawn(async move {
                for _ in 0..25 {
                    let guard = q.acquire("s1").await.unwrap();
                    let n = in_critical.fetch_add(1, Ordering::SeqCst) + 1;
                    max_seen.fetch_max(n, Ordering::SeqCst);
                    tokio::task::yield_now().await;
                    in_critical.fetch_sub(1, Ordering::SeqCst);
                    drop(guard);
                }
            }));
        }
        // Sweep concurrently with the acquirers.
        let sweeper = {
            let q = queue.clone();
            tokio::spawn(async move {
                for _ in 0..200 {
                    q.evict_idle().await;
                    tokio::task::yield_now().await;
                }
            })
        };

        for h in handles {
            h.await.unwrap();
        }
        sweeper.await.unwrap();

        assert_eq!(
            max_seen.load(Ordering::SeqCst),
            1,
            "serialization broken: multiple acquirers entered the critical section concurrently"
        );
    }

    #[test]
    fn lock_timeout_env_override_and_default() {
        // Single test owns the env var to avoid racing other tests in
        // parallel cargo runs. Covers: unset → default, valid → parsed,
        // invalid string → default, zero → default.
        // SAFETY: test-only, env var is namespaced and used only here.
        unsafe { std::env::remove_var("REVKA_GATEWAY_SESSION_LOCK_TIMEOUT_SECS") };
        assert_eq!(
            session_lock_timeout_secs(),
            SESSION_LOCK_TIMEOUT_SECS_DEFAULT
        );

        unsafe { std::env::set_var("REVKA_GATEWAY_SESSION_LOCK_TIMEOUT_SECS", "120") };
        assert_eq!(session_lock_timeout_secs(), 120);

        unsafe {
            std::env::set_var("REVKA_GATEWAY_SESSION_LOCK_TIMEOUT_SECS", "not-a-number");
        };
        assert_eq!(
            session_lock_timeout_secs(),
            SESSION_LOCK_TIMEOUT_SECS_DEFAULT
        );

        unsafe { std::env::set_var("REVKA_GATEWAY_SESSION_LOCK_TIMEOUT_SECS", "0") };
        assert_eq!(
            session_lock_timeout_secs(),
            SESSION_LOCK_TIMEOUT_SECS_DEFAULT
        );

        unsafe { std::env::remove_var("REVKA_GATEWAY_SESSION_LOCK_TIMEOUT_SECS") };
    }

    #[tokio::test]
    async fn queue_depth_reports_correctly() {
        let queue = SessionActorQueue::new(8, 30, 600);
        assert_eq!(queue.queue_depth("s1").await, 0);

        let guard = queue.acquire("s1").await.unwrap();
        assert_eq!(queue.queue_depth("s1").await, 1);

        drop(guard);
        assert_eq!(queue.queue_depth("s1").await, 0);
    }
}
