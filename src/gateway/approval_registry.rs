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

/// Per-channel scoping for a pending approval — populated AFTER the approval
/// prompt is sent so we can restrict keyword matching to the thread / reply
/// that belongs to this specific approval. Without this the bot matches any
/// message in the configured notification channel, which conflates parallel
/// approvals and triggers on unrelated chatter.
///
/// The fields are interpreted per platform:
/// - `channel_id`: the platform channel/conversation the prompt was sent to
///   (Discord channel, Slack channel, Telegram chat).
/// - `thread_id`: the platform thread anchor (Discord thread, Slack `thread_ts`).
/// - `prompt_message_id`: the prompt's message id (Discord prompt message for
///   reply matching, Telegram prompt message id stored as a string).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ApprovalScope {
    pub channel_id: Option<String>,
    pub thread_id: Option<String>,
    pub prompt_message_id: Option<String>,
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

    /// Per-channel reply scoping, keyed by channel slug (e.g. "discord",
    /// "slack", "telegram"). Populated by `attach` once the channel adapter
    /// has sent the prompt and reported the platform identifiers back.
    pub scopes: HashMap<String, ApprovalScope>,
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
            scopes: HashMap::new(),
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
    /// later via [`ApprovalRegistry::attach`] once the channel adapter has sent
    /// the prompt and received the message/thread identifiers back.
    pub fn register(&self, approval: PendingApproval) {
        let mut map = self.pending.write().unwrap();
        map.insert(approval.run_id.clone(), approval);
    }

    /// Attach per-channel reply scoping to an existing pending approval, keyed
    /// by channel slug (e.g. "discord", "slack", "telegram"). Called after the
    /// channel adapter has sent the prompt and reported the platform message /
    /// thread identifiers back.
    pub fn attach(&self, run_id: &str, channel: &str, scope: ApprovalScope) {
        let mut map = self.pending.write().unwrap();
        if let Some(a) = map.get_mut(run_id) {
            a.scopes.insert(channel.to_string(), scope);
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
            let Some(scope) = approval.scopes.get("discord") else {
                continue;
            };
            let in_expected_thread = match (&scope.thread_id, thread_id) {
                (Some(want), Some(got)) => want == got,
                _ => false,
            };
            let is_reply_to_prompt = match (&scope.prompt_message_id, reply_to_message_id) {
                (Some(want), Some(got)) => want == got,
                _ => false,
            };
            // Back-compat: if we never captured thread/message IDs (e.g. send
            // failed or thread creation was denied), fall back to matching by
            // channel only. This preserves existing behavior for unscoped
            // deployments but is strictly worse disambiguation.
            let fallback_channel_only = scope.thread_id.is_none()
                && scope.prompt_message_id.is_none()
                && scope
                    .channel_id
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
            let Some(scope) = approval.scopes.get("slack") else {
                continue;
            };
            let channel_match = scope
                .channel_id
                .as_ref()
                .map(|id| id == channel_id)
                .unwrap_or(false);
            if !channel_match {
                continue;
            }
            let thread_match = match (&scope.thread_id, thread_ts) {
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
            let Some(scope) = approval.scopes.get("telegram") else {
                continue;
            };
            let chat_match = scope
                .channel_id
                .as_ref()
                .map(|id| id == chat_id)
                .unwrap_or(false);
            if !chat_match {
                continue;
            }
            let want_prompt = scope
                .prompt_message_id
                .as_ref()
                .and_then(|s| s.parse::<i64>().ok());
            let reply_match = match (want_prompt, reply_to_message_id) {
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

    /// Match a bare approve/reject keyword to a pending approval on ANY channel,
    /// scoped to `(channel, channel_id)`. Channel-agnostic and reply-free: the
    /// per-platform matchers above handle precise reply/thread matching (which
    /// disambiguates parallel approvals), while this fallback lets a plain
    /// "approve" resume a run on any channel — but ONLY when exactly one approval
    /// is pending for that chat/channel, so multiple concurrent approvals still
    /// require an explicit reply/thread to disambiguate.
    ///
    /// `channel` is the channel slug (e.g. "telegram", "discord", "mattermost");
    /// `channel_id` is the incoming message's chat/channel, compared against the
    /// scope's `channel_id` captured when the prompt was sent.
    pub fn match_any_channel_keyword(
        &self,
        channel: &str,
        channel_id: &str,
        message: &str,
    ) -> Option<(String, bool, String)> {
        let map = self.pending.read().unwrap();
        if map.is_empty() {
            return None;
        }
        let msg_lower = message.trim().to_lowercase();

        // Collect REAL keyword matches scoped to (channel, channel_id). Two
        // pendings in the same chat are only ambiguous when BOTH match the same
        // keyword — disjoint keyword lists disambiguate themselves. So filter by
        // keyword first, then bail only if more than one pending actually
        // matched the incoming text.
        let mut hit: Option<(String, bool, String)> = None;
        for (run_id, approval) in map.iter() {
            let scoped = approval
                .scopes
                .get(channel)
                .and_then(|s| s.channel_id.as_deref())
                == Some(channel_id);
            if !scoped {
                continue;
            }
            if let Some((is_approve, feedback)) = match_keywords(
                &msg_lower,
                message,
                &approval.approve_keywords,
                &approval.reject_keywords,
            ) {
                if hit.is_some() {
                    // More than one pending matched the keyword → ambiguous;
                    // require an explicit reply/thread to disambiguate.
                    return None;
                }
                hit = Some((run_id.clone(), is_approve, feedback));
            }
        }
        hit
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
        if msg_lower == kw
            || msg_lower
                .strip_prefix(kw.as_str())
                .is_some_and(|rest| rest.starts_with(' '))
        {
            return Some((true, String::new()));
        }
    }
    for kw in reject_keywords {
        if msg_lower == kw {
            return Some((false, String::new()));
        }
        if let Some(rest_lower) = msg_lower.strip_prefix(kw.as_str()) {
            if rest_lower.starts_with(' ') {
                // Preserve the user's original casing when the keyword end lands
                // on a char boundary in the original (always true for ASCII
                // keywords); otherwise fall back to the lowercased remainder so
                // a case-folding byte-length change can never slice inside a
                // multibyte char and panic (which, under the registry read
                // lock, would poison it and brick all approvals).
                let trimmed = original.trim();
                let feedback = if trimmed.is_char_boundary(kw.len()) {
                    trimmed[kw.len()..].trim().to_string()
                } else {
                    rest_lower.trim().to_string()
                };
                return Some((false, feedback));
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approval(run_id: &str) -> PendingApproval {
        PendingApproval::new(
            run_id.into(),
            "approve".into(),
            "wf".into(),
            vec!["approve".into(), "yes".into()],
            vec!["reject".into()],
            "/tmp".into(),
        )
    }

    fn approval_with_keywords(
        run_id: &str,
        approve: Vec<String>,
        reject: Vec<String>,
    ) -> PendingApproval {
        PendingApproval::new(
            run_id.into(),
            "approve".into(),
            "wf".into(),
            approve,
            reject,
            "/tmp".into(),
        )
    }

    fn scope(chat: &str) -> ApprovalScope {
        ApprovalScope {
            channel_id: Some(chat.into()),
            thread_id: None,
            prompt_message_id: None,
        }
    }

    #[test]
    fn bare_keyword_resumes_single_pending_on_any_channel() {
        for ch in ["telegram", "discord", "slack", "mattermost", "signal"] {
            let reg = ApprovalRegistry::new();
            reg.register(approval("r1"));
            reg.attach("r1", ch, scope("100"));
            let m = reg.match_any_channel_keyword(ch, "100", "approve");
            assert_eq!(
                m.map(|(id, ok, _)| (id, ok)),
                Some(("r1".into(), true)),
                "channel {ch}"
            );
        }
    }

    #[test]
    fn bare_reject_carries_feedback() {
        let reg = ApprovalRegistry::new();
        reg.register(approval("r1"));
        reg.attach("r1", "discord", scope("c1"));
        let m = reg.match_any_channel_keyword("discord", "c1", "reject not safe");
        assert_eq!(m, Some(("r1".into(), false, "not safe".into())));
    }

    #[test]
    fn reject_feedback_survives_case_folding_byte_change() {
        // Kelvin sign 'K' (U+212A, 3 bytes) lowercases to ASCII 'k' (1 byte).
        // Slicing the original by the lowercased keyword's byte length would land
        // inside the multibyte char and panic (poisoning the registry lock). The
        // matcher must instead fall back to the lowercased remainder.
        let reg = ApprovalRegistry::new();
        reg.register(approval_with_keywords(
            "r1",
            vec!["approve".into()],
            vec!["k".into()],
        ));
        reg.attach("r1", "telegram", scope("100"));
        let m = reg.match_any_channel_keyword("telegram", "100", "\u{212A} bad");
        assert_eq!(m, Some(("r1".into(), false, "bad".into())));
    }

    #[test]
    fn ambiguous_when_multiple_pending_in_same_chat() {
        let reg = ApprovalRegistry::new();
        reg.register(approval("r1"));
        reg.attach("r1", "slack", scope("c1"));
        reg.register(approval("r2"));
        reg.attach("r2", "slack", scope("c1"));
        // Two pending in the same chat → ambiguous → no match (an explicit
        // reply/thread via the per-platform matcher disambiguates instead).
        assert!(
            reg.match_any_channel_keyword("slack", "c1", "approve")
                .is_none()
        );
    }

    #[test]
    fn no_match_for_other_chat_or_channel() {
        let reg = ApprovalRegistry::new();
        reg.register(approval("r1"));
        reg.attach("r1", "telegram", scope("100"));
        assert!(
            reg.match_any_channel_keyword("telegram", "999", "approve")
                .is_none()
        );
        assert!(
            reg.match_any_channel_keyword("discord", "100", "approve")
                .is_none()
        );
    }

    #[test]
    fn no_match_for_non_keyword_message() {
        let reg = ApprovalRegistry::new();
        reg.register(approval("r1"));
        reg.attach("r1", "telegram", scope("100"));
        assert!(
            reg.match_any_channel_keyword("telegram", "100", "hello there")
                .is_none()
        );
    }

    #[test]
    fn two_pending_disjoint_keywords_disambiguate() {
        let reg = ApprovalRegistry::new();
        // Two pendings in the same chat, but with DISJOINT keyword lists. A
        // keyword unique to one of them is unambiguous and resumes that run.
        reg.register(approval_with_keywords(
            "r1",
            vec!["alpha".into()],
            vec!["nope".into()],
        ));
        reg.attach("r1", "slack", scope("c1"));
        reg.register(approval_with_keywords(
            "r2",
            vec!["bravo".into()],
            vec!["deny".into()],
        ));
        reg.attach("r2", "slack", scope("c1"));

        let m = reg.match_any_channel_keyword("slack", "c1", "alpha");
        assert_eq!(m.map(|(id, ok, _)| (id, ok)), Some(("r1".into(), true)));

        let m = reg.match_any_channel_keyword("slack", "c1", "bravo");
        assert_eq!(m.map(|(id, ok, _)| (id, ok)), Some(("r2".into(), true)));
    }

    #[test]
    fn is_empty_registry_returns_none() {
        let reg = ApprovalRegistry::new();
        assert!(
            reg.match_any_channel_keyword("telegram", "100", "approve")
                .is_none()
        );
    }

    #[test]
    fn keyword_with_trailing_text_strip_prefix() {
        let reg = ApprovalRegistry::new();
        reg.register(approval("r1"));
        reg.attach("r1", "discord", scope("c1"));
        let m = reg.match_any_channel_keyword("discord", "c1", "yes please");
        assert_eq!(m.map(|(id, ok, _)| (id, ok)), Some(("r1".into(), true)));
    }
}
