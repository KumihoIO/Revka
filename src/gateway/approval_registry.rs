use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::{Arc, OnceLock, RwLock};

/// Process-global approval registry shared between the gateway (which registers
/// pending approvals) and the channel listeners (Discord/Slack/Telegram) which
/// handle keyword replies.
///
/// Both components run in the same process when started via `revka daemon`.
/// This singleton is created on first access and lives for the process lifetime.
static GLOBAL_REGISTRY: OnceLock<Arc<ApprovalRegistry>> = OnceLock::new();

/// Return the process-global `ApprovalRegistry`, creating it on first call.
pub fn global() -> Arc<ApprovalRegistry> {
    Arc::clone(GLOBAL_REGISTRY.get_or_init(|| Arc::new(ApprovalRegistry::new())))
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PendingApproval {
    pub run_id: String,
    pub step_id: String,
    pub workflow_name: String,
    pub approve_keywords: Vec<String>,
    pub reject_keywords: Vec<String>,
    pub cwd: String,
    pub created_at: DateTime<Utc>,

    // Channel scoping — populated AFTER the approval prompt is sent so we can
    // restrict keyword matching to the thread / reply that belongs to this
    // specific approval. Without these the bot matches any message in the
    // configured notification channel, which conflates parallel approvals
    // and triggers on unrelated chatter.
    pub discord_channel_id: Option<String>,
    pub discord_thread_id: Option<String>,
    pub discord_prompt_message_id: Option<String>,

    pub slack_channel_id: Option<String>,
    pub slack_thread_ts: Option<String>,

    pub telegram_chat_id: Option<String>,
    pub telegram_prompt_message_id: Option<i64>,
}

impl PendingApproval {
    pub fn new(
        run_id: String,
        step_id: String,
        workflow_name: String,
        approve_keywords: Vec<String>,
        reject_keywords: Vec<String>,
        cwd: String,
    ) -> Self {
        Self {
            run_id,
            step_id,
            workflow_name,
            approve_keywords,
            reject_keywords,
            cwd,
            created_at: Utc::now(),
            discord_channel_id: None,
            discord_thread_id: None,
            discord_prompt_message_id: None,
            slack_channel_id: None,
            slack_thread_ts: None,
            telegram_chat_id: None,
            telegram_prompt_message_id: None,
        }
    }
}

/// Thread-safe registry of pending workflow approvals.
///
/// When a workflow hits a human_approval step, the operator pushes an event
/// to the gateway. The gateway registers the pending approval here.
/// When a user responds (via Discord/Slack/Telegram reply or dashboard REST),
/// the gateway looks up the approval, atomically claims it, and calls
/// resume_workflow.
pub struct ApprovalRegistry {
    pending: RwLock<HashMap<String, PendingApproval>>, // keyed by run_id
}

impl ApprovalRegistry {
    pub fn new() -> Self {
        Self {
            pending: RwLock::new(HashMap::new()),
        }
    }

    /// Register a new pending approval. Channel thread/reply IDs are attached
    /// later via the `attach_*` methods once the channel adapter has sent the
    /// prompt and received the message/thread identifiers back.
    pub fn register(&self, approval: PendingApproval) {
        let mut map = self.pending.write().unwrap();
        map.insert(approval.run_id.clone(), approval);
    }

    /// Attach Discord thread + prompt message IDs to an existing pending approval.
    pub fn attach_discord(
        &self,
        run_id: &str,
        channel_id: Option<String>,
        thread_id: Option<String>,
        prompt_message_id: Option<String>,
    ) {
        let mut map = self.pending.write().unwrap();
        if let Some(a) = map.get_mut(run_id) {
            if channel_id.is_some() {
                a.discord_channel_id = channel_id;
            }
            a.discord_thread_id = thread_id;
            a.discord_prompt_message_id = prompt_message_id;
        }
    }

    /// Attach Slack channel + thread_ts to an existing pending approval.
    pub fn attach_slack(
        &self,
        run_id: &str,
        channel_id: Option<String>,
        thread_ts: Option<String>,
    ) {
        let mut map = self.pending.write().unwrap();
        if let Some(a) = map.get_mut(run_id) {
            a.slack_channel_id = channel_id;
            a.slack_thread_ts = thread_ts;
        }
    }

    /// Attach Telegram chat + prompt message_id to an existing pending approval.
    pub fn attach_telegram(
        &self,
        run_id: &str,
        chat_id: Option<String>,
        prompt_message_id: Option<i64>,
    ) {
        let mut map = self.pending.write().unwrap();
        if let Some(a) = map.get_mut(run_id) {
            a.telegram_chat_id = chat_id;
            a.telegram_prompt_message_id = prompt_message_id;
        }
    }

    /// Atomically claim a pending approval. Returns Some if the approval
    /// existed and hadn't been claimed yet. Returns None if already claimed
    /// or not found. This prevents race conditions between channel adapters
    /// and the dashboard.
    pub fn try_claim(&self, run_id: &str) -> Option<PendingApproval> {
        let mut map = self.pending.write().unwrap();
        map.remove(run_id)
    }

    /// Match a Discord message to a pending approval. Only matches when the
    /// message is either in the thread that was created for the approval OR
    /// is a reply to the original prompt message. This prevents unrelated
    /// chatter in the notification channel from triggering approvals and
    /// cleanly disambiguates parallel approvals.
    ///
    /// Pass `None` for `thread_id` if the incoming message is in the root
    /// channel (not a thread). Pass `None` for `reply_to_message_id` if the
    /// incoming message is not a reply.
    pub fn match_discord_keyword(
        &self,
        channel_id: &str,
        thread_id: Option<&str>,
        reply_to_message_id: Option<&str>,
        message: &str,
    ) -> Option<(String, bool, String)> {
        let map = self.pending.read().unwrap();
        let msg_lower = message.trim().to_lowercase();

        for (run_id, approval) in map.iter() {
            let in_expected_thread = match (&approval.discord_thread_id, thread_id) {
                (Some(want), Some(got)) => want == got,
                _ => false,
            };
            let is_reply_to_prompt =
                match (&approval.discord_prompt_message_id, reply_to_message_id) {
                    (Some(want), Some(got)) => want == got,
                    _ => false,
                };
            // Back-compat: if we never captured thread/message IDs (e.g. send
            // failed or thread creation was denied), fall back to matching by
            // channel only. This preserves existing behavior for unscoped
            // deployments but is strictly worse disambiguation.
            let fallback_channel_only = approval.discord_thread_id.is_none()
                && approval.discord_prompt_message_id.is_none()
                && approval
                    .discord_channel_id
                    .as_ref()
                    .map(|id| id == channel_id)
                    .unwrap_or(false);

            if !(in_expected_thread || is_reply_to_prompt || fallback_channel_only) {
                continue;
            }

            if let Some(res) = match_keywords(
                &msg_lower,
                message,
                &approval.approve_keywords,
                &approval.reject_keywords,
            ) {
                return Some((run_id.clone(), res.0, res.1));
            }
        }

        None
    }

    /// Match a Slack message to a pending approval. Requires the incoming
    /// message to carry `thread_ts` equal to the approval's captured ts.
    pub fn match_slack_keyword(
        &self,
        channel_id: &str,
        thread_ts: Option<&str>,
        message: &str,
    ) -> Option<(String, bool, String)> {
        let map = self.pending.read().unwrap();
        let msg_lower = message.trim().to_lowercase();

        for (run_id, approval) in map.iter() {
            let channel_match = approval
                .slack_channel_id
                .as_ref()
                .map(|id| id == channel_id)
                .unwrap_or(false);
            if !channel_match {
                continue;
            }
            let thread_match = match (&approval.slack_thread_ts, thread_ts) {
                (Some(want), Some(got)) => want == got,
                _ => false,
            };
            if !thread_match {
                continue;
            }
            if let Some(res) = match_keywords(
                &msg_lower,
                message,
                &approval.approve_keywords,
                &approval.reject_keywords,
            ) {
                return Some((run_id.clone(), res.0, res.1));
            }
        }

        None
    }

    /// Match a Telegram message to a pending approval. Requires the incoming
    /// message to be a reply to the approval's prompt message.
    pub fn match_telegram_keyword(
        &self,
        chat_id: &str,
        reply_to_message_id: Option<i64>,
        message: &str,
    ) -> Option<(String, bool, String)> {
        let map = self.pending.read().unwrap();
        let msg_lower = message.trim().to_lowercase();

        for (run_id, approval) in map.iter() {
            let chat_match = approval
                .telegram_chat_id
                .as_ref()
                .map(|id| id == chat_id)
                .unwrap_or(false);
            if !chat_match {
                continue;
            }
            let reply_match = match (approval.telegram_prompt_message_id, reply_to_message_id) {
                (Some(want), Some(got)) => want == got,
                _ => false,
            };
            if !reply_match {
                continue;
            }
            if let Some(res) = match_keywords(
                &msg_lower,
                message,
                &approval.approve_keywords,
                &approval.reject_keywords,
            ) {
                return Some((run_id.clone(), res.0, res.1));
            }
        }

        None
    }

    /// Remove a pending approval (cleanup after resolution).
    pub fn remove(&self, run_id: &str) {
        let mut map = self.pending.write().unwrap();
        map.remove(run_id);
    }

    /// List all pending approvals (for debugging/status).
    pub fn list_pending(&self) -> Vec<PendingApproval> {
        let map = self.pending.read().unwrap();
        map.values().cloned().collect()
    }
}

/// Check a normalized message against approve/reject keyword lists. Returns
/// `(is_approve, feedback)` on match.
fn match_keywords(
    msg_lower: &str,
    original: &str,
    approve_keywords: &[String],
    reject_keywords: &[String],
) -> Option<(bool, String)> {
    for kw in approve_keywords {
        if msg_lower == kw || msg_lower.starts_with(&format!("{} ", kw)) {
            return Some((true, String::new()));
        }
    }
    for kw in reject_keywords {
        if msg_lower == kw {
            return Some((false, String::new()));
        }
        if msg_lower.starts_with(&format!("{} ", kw)) {
            let feedback = original.trim()[kw.len()..].trim().to_string();
            return Some((false, feedback));
        }
    }
    None
}
