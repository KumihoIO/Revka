use std::sync::Arc;

use anyhow::Result;
use tracing::{info, warn};

use super::engine::now_iso8601;
use super::types::{SopRun, SopRunStatus, SopStepResult};
use crate::memory::traits::{Memory, MemoryCategory};

const SOP_CATEGORY: &str = "sop";

/// Persists SOP execution runs and step results to the Memory backend.
///
/// Storage keys:
/// - `sop_run_{run_id}` — full `SopRun` JSON (created on start, updated on complete)
/// - `sop_step_{run_id}_{step_number}` — `SopStepResult` JSON (one per step)
pub struct SopAuditLogger {
    memory: Arc<dyn Memory>,
}

impl SopAuditLogger {
    pub fn new(memory: Arc<dyn Memory>) -> Self {
        Self { memory }
    }

    /// Log the start of a new SOP run.
    pub async fn log_run_start(&self, run: &SopRun) -> Result<()> {
        let key = run_key(&run.run_id);
        let content = serde_json::to_string_pretty(run)?;
        self.memory.store(&key, &content, category(), None).await?;
        info!(
            "SOP audit: run {} started for '{}'",
            run.run_id, run.sop_name
        );
        Ok(())
    }

    /// Log a step result.
    pub async fn log_step_result(&self, run_id: &str, result: &SopStepResult) -> Result<()> {
        let key = step_key(run_id, result.step_number);
        let content = serde_json::to_string_pretty(result)?;
        self.memory.store(&key, &content, category(), None).await?;
        Ok(())
    }

    /// Log run completion (updates the run record with final state).
    pub async fn log_run_complete(&self, run: &SopRun) -> Result<()> {
        let key = run_key(&run.run_id);
        let content = serde_json::to_string_pretty(run)?;
        self.memory.store(&key, &content, category(), None).await?;
        info!(
            "SOP audit: run {} finished with status {}",
            run.run_id, run.status
        );
        Ok(())
    }

    /// Log an operator approval event for a specific step.
    pub async fn log_approval(&self, run: &SopRun, step_number: u32) -> Result<()> {
        let key = format!("sop_approval_{}_{step_number}", run.run_id);
        let content = serde_json::to_string_pretty(run)?;
        self.memory.store(&key, &content, category(), None).await?;
        info!(
            "SOP audit: run {} step {step_number} approved by operator",
            run.run_id
        );
        Ok(())
    }

    /// Log a timeout-based auto-approval event for a specific step.
    pub async fn log_timeout_auto_approve(&self, run: &SopRun, step_number: u32) -> Result<()> {
        let key = format!("sop_timeout_approve_{}_{step_number}", run.run_id);
        let content = serde_json::to_string_pretty(run)?;
        self.memory.store(&key, &content, category(), None).await?;
        info!(
            "SOP audit: run {} step {step_number} auto-approved after timeout",
            run.run_id
        );
        Ok(())
    }

    /// Log that a timed-out approval gate was **held** for a human — the
    /// fail-safe default: the run was not auto-executed and still awaits
    /// explicit approval.
    pub async fn log_timeout_held(&self, run: &SopRun, step_number: u32) -> Result<()> {
        let key = format!("sop_timeout_held_{}_{step_number}", run.run_id);
        let content = serde_json::to_string_pretty(run)?;
        self.memory.store(&key, &content, category(), None).await?;
        info!(
            "SOP audit: run {} step {step_number} held for human approval after timeout",
            run.run_id
        );
        Ok(())
    }

    /// Retrieve a stored run by ID (if it exists in memory).
    pub async fn get_run(&self, run_id: &str) -> Result<Option<SopRun>> {
        let key = run_key(run_id);
        match self.memory.get(&key).await? {
            Some(entry) => {
                let run: SopRun = serde_json::from_str(&entry.content).map_err(|e| {
                    warn!("SOP audit: failed to parse run {run_id}: {e}");
                    e
                })?;
                Ok(Some(run))
            }
            None => Ok(None),
        }
    }

    /// List all stored SOP run keys.
    pub async fn list_runs(&self) -> Result<Vec<String>> {
        let entries = self.memory.list(Some(&category()), None).await?;
        let run_keys: Vec<String> = entries
            .into_iter()
            .filter(|e| e.key.starts_with("sop_run_"))
            .map(|e| e.key)
            .collect();
        Ok(run_keys)
    }

    /// Mark interrupted (non-terminal) runs as `Failed` on daemon startup.
    ///
    /// Mirrors the Python operator's recovery contract
    /// (`operator-mcp/operator_mcp/workflow/recovery.py`): after a restart any
    /// run still in a non-terminal state is marked `Failed` — runs are **not**
    /// auto-resumed; retry is an explicit user action. Deterministic
    /// `PausedCheckpoint` runs are intentionally left untouched (their resume is
    /// handled separately from the persisted checkpoint state — see #391), so
    /// this sweeps only the non-deterministic in-flight states (`Pending`,
    /// `Running`, `WaitingApproval`). Returns the number of runs marked.
    ///
    /// Best-effort and idempotent: terminal runs are skipped, so a second call
    /// marks nothing.
    pub async fn mark_stale_runs(&self) -> Result<usize> {
        let mut marked = 0usize;
        for key in self.list_runs().await? {
            let run_id = key.strip_prefix("sop_run_").unwrap_or(key.as_str());
            let Some(mut run) = self.get_run(run_id).await? else {
                continue;
            };
            let stale = matches!(
                run.status,
                SopRunStatus::Pending | SopRunStatus::Running | SopRunStatus::WaitingApproval
            );
            if !stale {
                continue;
            }
            run.status = SopRunStatus::Failed;
            if run.completed_at.is_none() {
                run.completed_at = Some(now_iso8601());
            }
            let content = serde_json::to_string_pretty(&run)?;
            self.memory.store(&key, &content, category(), None).await?;
            warn!(
                "SOP recovery: run {} ('{}') was interrupted by a daemon restart — marked Failed \
                 (retry is a manual action)",
                run.run_id, run.sop_name
            );
            marked += 1;
        }
        if marked > 0 {
            info!("SOP recovery: marked {marked} interrupted run(s) Failed on startup");
        }
        Ok(marked)
    }
}

fn run_key(run_id: &str) -> String {
    format!("sop_run_{run_id}")
}

fn step_key(run_id: &str, step_number: u32) -> String {
    format!("sop_step_{run_id}_{step_number}")
}

fn category() -> MemoryCategory {
    MemoryCategory::Custom(SOP_CATEGORY.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sop::types::{SopEvent, SopRunStatus, SopStepStatus, SopTriggerSource};

    fn test_run() -> SopRun {
        SopRun {
            run_id: "run-test-001".into(),
            sop_name: "test-sop".into(),
            trigger_event: SopEvent {
                source: SopTriggerSource::Manual,
                topic: None,
                payload: None,
                timestamp: "2026-02-19T12:00:00Z".into(),
            },
            status: SopRunStatus::Running,
            current_step: 1,
            total_steps: 3,
            started_at: "2026-02-19T12:00:00Z".into(),
            completed_at: None,
            step_results: Vec::new(),
            waiting_since: None,
            llm_calls_saved: 0,
        }
    }

    fn test_step_result(n: u32) -> SopStepResult {
        SopStepResult {
            step_number: n,
            status: SopStepStatus::Completed,
            output: format!("Step {n} completed"),
            started_at: "2026-02-19T12:00:00Z".into(),
            completed_at: Some("2026-02-19T12:00:05Z".into()),
        }
    }

    #[tokio::test]
    async fn audit_roundtrip() {
        let memory: Arc<dyn Memory> = Arc::new(crate::memory::test_memory::TestMemory::new());

        let logger = SopAuditLogger::new(memory);

        // Log run start
        let run = test_run();
        logger.log_run_start(&run).await.unwrap();

        // Log step result
        let step = test_step_result(1);
        logger.log_step_result(&run.run_id, &step).await.unwrap();

        // Log run complete
        let mut completed_run = run.clone();
        completed_run.status = SopRunStatus::Completed;
        completed_run.completed_at = Some("2026-02-19T12:05:00Z".into());
        completed_run.step_results = vec![step];
        logger.log_run_complete(&completed_run).await.unwrap();

        // Retrieve
        let retrieved = logger.get_run("run-test-001").await.unwrap().unwrap();
        assert_eq!(retrieved.run_id, "run-test-001");
        assert_eq!(retrieved.status, SopRunStatus::Completed);
        assert_eq!(retrieved.step_results.len(), 1);

        // List runs
        let keys = logger.list_runs().await.unwrap();
        assert!(keys.contains(&"sop_run_run-test-001".to_string()));
    }

    #[tokio::test]
    async fn log_approval_persists_entry() {
        let memory: Arc<dyn Memory> = Arc::new(crate::memory::test_memory::TestMemory::new());

        let logger = SopAuditLogger::new(memory.clone());
        let run = test_run();
        logger.log_approval(&run, 1).await.unwrap();

        let entries = memory.list(Some(&category()), None).await.unwrap();
        let approval_keys: Vec<_> = entries
            .iter()
            .filter(|e| e.key.starts_with("sop_approval_"))
            .collect();
        assert_eq!(approval_keys.len(), 1);
        assert!(approval_keys[0].key.contains("run-test-001"));
    }

    #[tokio::test]
    async fn log_timeout_auto_approve_persists_entry() {
        let memory: Arc<dyn Memory> = Arc::new(crate::memory::test_memory::TestMemory::new());

        let logger = SopAuditLogger::new(memory.clone());
        let run = test_run();
        logger.log_timeout_auto_approve(&run, 1).await.unwrap();

        let entries = memory.list(Some(&category()), None).await.unwrap();
        let timeout_keys: Vec<_> = entries
            .iter()
            .filter(|e| e.key.starts_with("sop_timeout_approve_"))
            .collect();
        assert_eq!(timeout_keys.len(), 1);
        assert!(timeout_keys[0].key.contains("run-test-001"));
    }

    #[tokio::test]
    async fn log_timeout_held_persists_entry() {
        let memory: Arc<dyn Memory> = Arc::new(crate::memory::test_memory::TestMemory::new());

        let logger = SopAuditLogger::new(memory.clone());
        let run = test_run();
        logger.log_timeout_held(&run, 1).await.unwrap();

        let entries = memory.list(Some(&category()), None).await.unwrap();
        let held_keys: Vec<_> = entries
            .iter()
            .filter(|e| e.key.starts_with("sop_timeout_held_"))
            .collect();
        assert_eq!(held_keys.len(), 1);
        assert!(held_keys[0].key.contains("run-test-001"));
    }

    #[tokio::test]
    async fn mark_stale_runs_fails_non_terminal_and_leaves_terminal() {
        let memory: Arc<dyn Memory> = Arc::new(crate::memory::test_memory::TestMemory::new());
        let logger = SopAuditLogger::new(memory);

        let persist = |id: &str, status: SopRunStatus| {
            let mut r = test_run();
            r.run_id = id.into();
            r.status = status;
            r
        };
        // Non-terminal in-flight states (must be marked Failed):
        logger
            .log_run_start(&persist("run-running", SopRunStatus::Running))
            .await
            .unwrap();
        logger
            .log_run_start(&persist("run-waiting", SopRunStatus::WaitingApproval))
            .await
            .unwrap();
        // Terminal (must be left as-is):
        logger
            .log_run_complete(&persist("run-completed", SopRunStatus::Completed))
            .await
            .unwrap();
        // Deterministic checkpoint (left for #391's resume, NOT failed):
        logger
            .log_run_start(&persist("run-paused", SopRunStatus::PausedCheckpoint))
            .await
            .unwrap();

        let marked = logger.mark_stale_runs().await.unwrap();
        assert_eq!(marked, 2, "only Running + WaitingApproval should be marked");

        async fn status_of(logger: &SopAuditLogger, id: &str) -> SopRunStatus {
            logger.get_run(id).await.unwrap().unwrap().status
        }
        assert_eq!(
            status_of(&logger, "run-running").await,
            SopRunStatus::Failed
        );
        assert_eq!(
            status_of(&logger, "run-waiting").await,
            SopRunStatus::Failed
        );
        assert_eq!(
            status_of(&logger, "run-completed").await,
            SopRunStatus::Completed
        );
        // Deterministic checkpoint left for #391's resume, NOT failed.
        assert_eq!(
            status_of(&logger, "run-paused").await,
            SopRunStatus::PausedCheckpoint
        );

        // Idempotent: a second sweep marks nothing.
        assert_eq!(logger.mark_stale_runs().await.unwrap(), 0);
    }

    #[tokio::test]
    async fn get_nonexistent_run_returns_none() {
        let memory: Arc<dyn Memory> = Arc::new(crate::memory::test_memory::TestMemory::new());

        let logger = SopAuditLogger::new(memory);
        let result = logger.get_run("nonexistent").await.unwrap();
        assert!(result.is_none());
    }
}
