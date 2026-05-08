//! Interactive approval workflow for supervised mode.
//!
//! Provides a pre-execution hook that prompts the user before tool calls,
//! with session-scoped "Always" allowlists and audit logging.

use crate::config::AutonomyConfig;
use crate::security::AutonomyLevel;
use crate::trust::{CorrectionType, TrustTracker};
use chrono::Utc;
use parking_lot::Mutex;
use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::io::{self, BufRead, Write};
use std::sync::Arc;

// ── Types ────────────────────────────────────────────────────────

/// A request to approve a tool call before execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalRequest {
    pub tool_name: String,
    pub arguments: serde_json::Value,
}

/// The user's response to an approval request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ApprovalResponse {
    /// Execute this one call.
    Yes,
    /// Deny this call.
    No,
    /// Execute and add tool to session-scoped allowlist.
    Always,
}

/// A single audit log entry for an approval decision.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ApprovalLogEntry {
    pub timestamp: String,
    pub tool_name: String,
    pub arguments_summary: String,
    pub decision: ApprovalResponse,
    pub channel: String,
}

// ── ApprovalManager ──────────────────────────────────────────────

/// Manages the approval workflow for tool calls.
///
/// - Checks config-level `auto_approve` / `always_ask` lists
/// - Maintains a session-scoped "always" allowlist
/// - Records an audit trail of all decisions
///
/// Two modes:
/// - **Interactive** (CLI): tools needing approval trigger a stdin prompt.
/// - **Non-interactive** (channels): tools needing approval are auto-denied
///   because there is no interactive operator to approve them. `auto_approve`
///   policy is still enforced, and `always_ask` / supervised-default tools are
///   denied rather than silently allowed.
pub struct ApprovalManager {
    /// Tools that never need approval (from config).
    auto_approve: HashSet<String>,
    /// Tools that always need approval, ignoring session allowlist.
    always_ask: HashSet<String>,
    /// Autonomy level from config.
    autonomy_level: AutonomyLevel,
    /// When `true`, tools that would require interactive approval are
    /// auto-denied instead. Used for channel-driven (non-CLI) runs.
    non_interactive: bool,
    /// Session-scoped allowlist built from "Always" responses.
    session_allowlist: Mutex<HashSet<String>>,
    /// Audit trail of approval decisions.
    audit_log: Mutex<Vec<ApprovalLogEntry>>,
    /// Optional per-domain trust tracker. When present, a domain in regression
    /// downgrades the effective autonomy level by one tier in `needs_approval`.
    /// Denied approvals are recorded as `UserOverride` corrections; callers
    /// can also report successes and other corrections via the public methods.
    trust: Option<Arc<Mutex<TrustTracker>>>,
}

impl ApprovalManager {
    /// Create an interactive (CLI) approval manager from autonomy config.
    pub fn from_config(config: &AutonomyConfig) -> Self {
        Self {
            auto_approve: config.auto_approve.iter().cloned().collect(),
            always_ask: config.always_ask.iter().cloned().collect(),
            autonomy_level: config.level,
            non_interactive: false,
            session_allowlist: Mutex::new(HashSet::new()),
            audit_log: Mutex::new(Vec::new()),
            trust: None,
        }
    }

    /// Create a non-interactive approval manager for channel-driven runs.
    ///
    /// Enforces the same `auto_approve` / `always_ask` / supervised policies
    /// as the CLI manager, but tools that would require interactive approval
    /// are auto-denied instead of prompting (since there is no operator).
    pub fn for_non_interactive(config: &AutonomyConfig) -> Self {
        Self {
            auto_approve: config.auto_approve.iter().cloned().collect(),
            always_ask: config.always_ask.iter().cloned().collect(),
            autonomy_level: config.level,
            non_interactive: true,
            session_allowlist: Mutex::new(HashSet::new()),
            audit_log: Mutex::new(Vec::new()),
            trust: None,
        }
    }

    /// Attach a trust tracker. Denials recorded as `UserOverride` corrections;
    /// regression on a tool's domain downgrades effective autonomy one tier.
    pub fn with_trust_tracker(mut self, tracker: Arc<Mutex<TrustTracker>>) -> Self {
        self.trust = Some(tracker);
        self
    }

    /// Current effective autonomy level for `domain`, applying trust regression
    /// downgrade when a tracker is attached. `domain` is typically the tool name.
    fn effective_autonomy(&self, domain: &str) -> AutonomyLevel {
        match &self.trust {
            Some(trust) => trust
                .lock()
                .effective_autonomy_level(domain, self.autonomy_level),
            None => self.autonomy_level,
        }
    }

    /// Record a successful tool execution against the trust tracker, if any.
    /// Safe no-op when no tracker is attached.
    pub fn record_tool_success(&self, tool_name: &str) {
        if let Some(trust) = &self.trust {
            trust.lock().record_success(tool_name);
        }
    }

    /// Record a correction (quality failure, SOP deviation, etc.) against the
    /// trust tracker, if any. Safe no-op when no tracker is attached.
    pub fn record_tool_correction(
        &self,
        tool_name: &str,
        correction_type: CorrectionType,
        description: &str,
    ) {
        if let Some(trust) = &self.trust {
            trust
                .lock()
                .record_correction(tool_name, correction_type, description);
        }
    }

    /// Returns `true` when this manager operates in non-interactive mode
    /// (i.e. for channel-driven runs where no operator can approve).
    pub fn is_non_interactive(&self) -> bool {
        self.non_interactive
    }

    /// Check whether a tool call requires interactive approval.
    ///
    /// Returns `true` if the call needs a prompt, `false` if it can proceed.
    pub fn needs_approval(&self, tool_name: &str) -> bool {
        let level = self.effective_autonomy(tool_name);

        // Full autonomy never prompts.
        if level == AutonomyLevel::Full {
            return false;
        }

        if level == AutonomyLevel::ReadOnly {
            // GLOBAL ReadOnly: the call is blocked downstream by
            // SecurityPolicy::can_act — no prompt needed.
            if self.autonomy_level == AutonomyLevel::ReadOnly {
                return false;
            }
            // Per-tool regression dropped us here while global autonomy is
            // Supervised or Full. SecurityPolicy::can_act only inspects the
            // global level and will let the call proceed, so without a
            // prompt the denied tool would execute silently. Force a prompt
            // (or auto-deny in non-interactive mode via the caller).
            return true;
        }

        // always_ask overrides everything.
        if self.always_ask.contains("*") || self.always_ask.contains(tool_name) {
            return true;
        }

        // Channel-driven shell execution is still guarded by the shell tool's
        // own command allowlist and risk policy. Skipping the outer approval
        // gate here lets low-risk allowlisted commands (e.g. `ls`) work in
        // non-interactive channels without silently allowing medium/high-risk
        // commands.
        if self.non_interactive && tool_name == "shell" {
            return false;
        }

        // MCP-namespaced tools (e.g. "kumiho-memory__kumiho_memory_engage")
        // are injected by admin-configured MCP servers. In non-interactive
        // mode, auto-approve them — they were explicitly provisioned and
        // there is no operator to prompt.
        if self.non_interactive && tool_name.contains("__") {
            return false;
        }

        // auto_approve skips the prompt.
        if self.auto_approve.contains("*") || self.auto_approve.contains(tool_name) {
            return false;
        }

        // Session allowlist (from prior "Always" responses).
        let allowlist = self.session_allowlist.lock();
        if allowlist.contains(tool_name) {
            return false;
        }

        // Default: supervised mode requires approval.
        true
    }

    /// Record an approval decision and update session state.
    pub fn record_decision(
        &self,
        tool_name: &str,
        args: &serde_json::Value,
        decision: ApprovalResponse,
        channel: &str,
    ) {
        // If "Always", add to session allowlist.
        if decision == ApprovalResponse::Always {
            let mut allowlist = self.session_allowlist.lock();
            allowlist.insert(tool_name.to_string());
        }

        // On denial, record a UserOverride correction against the tool's domain
        // so repeated rejections reduce effective autonomy for that tool.
        if decision == ApprovalResponse::No {
            if let Some(trust) = &self.trust {
                trust.lock().record_correction(
                    tool_name,
                    CorrectionType::UserOverride,
                    &format!("approval denied via {channel}"),
                );
            }
        }

        // Append to audit log.
        let summary = summarize_args(args);
        let entry = ApprovalLogEntry {
            timestamp: Utc::now().to_rfc3339(),
            tool_name: tool_name.to_string(),
            arguments_summary: summary,
            decision,
            channel: channel.to_string(),
        };
        let mut log = self.audit_log.lock();
        log.push(entry);
    }

    /// Get a snapshot of the audit log.
    pub fn audit_log(&self) -> Vec<ApprovalLogEntry> {
        self.audit_log.lock().clone()
    }

    /// Get the current session allowlist.
    pub fn session_allowlist(&self) -> HashSet<String> {
        self.session_allowlist.lock().clone()
    }

    /// Prompt the user on the CLI and return their decision.
    ///
    /// Only called for interactive (CLI) managers. Non-interactive managers
    /// auto-deny in the tool-call loop before reaching this point.
    pub fn prompt_cli(&self, request: &ApprovalRequest) -> ApprovalResponse {
        prompt_cli_interactive(request)
    }
}

// ── CLI prompt ───────────────────────────────────────────────────

/// Display the approval prompt and read user input from stdin.
fn prompt_cli_interactive(request: &ApprovalRequest) -> ApprovalResponse {
    let summary = summarize_args(&request.arguments);
    eprintln!();
    eprintln!("🔧 Agent wants to execute: {}", request.tool_name);
    eprintln!("   {summary}");
    eprint!("   [Y]es / [N]o / [A]lways for {}: ", request.tool_name);
    let _ = io::stderr().flush();

    let stdin = io::stdin();
    let mut line = String::new();
    if stdin.lock().read_line(&mut line).is_err() {
        return ApprovalResponse::No;
    }

    match line.trim().to_ascii_lowercase().as_str() {
        "y" | "yes" => ApprovalResponse::Yes,
        "a" | "always" => ApprovalResponse::Always,
        _ => ApprovalResponse::No,
    }
}

/// Produce a short human-readable summary of tool arguments.
fn summarize_args(args: &serde_json::Value) -> String {
    match args {
        serde_json::Value::Object(map) => {
            let parts: Vec<String> = map
                .iter()
                .map(|(k, v)| {
                    let val = match v {
                        serde_json::Value::String(s) => truncate_for_summary(s, 80),
                        other => {
                            let s = other.to_string();
                            truncate_for_summary(&s, 80)
                        }
                    };
                    format!("{k}: {val}")
                })
                .collect();
            parts.join(", ")
        }
        other => {
            let s = other.to_string();
            truncate_for_summary(&s, 120)
        }
    }
}

fn truncate_for_summary(input: &str, max_chars: usize) -> String {
    let mut chars = input.chars();
    let truncated: String = chars.by_ref().take(max_chars).collect();
    if chars.next().is_some() {
        format!("{truncated}…")
    } else {
        input.to_string()
    }
}

// ── Tests ────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::AutonomyConfig;

    fn supervised_config() -> AutonomyConfig {
        AutonomyConfig {
            level: AutonomyLevel::Supervised,
            auto_approve: vec!["file_read".into(), "weather".into()],
            always_ask: vec!["shell".into()],
            ..AutonomyConfig::default()
        }
    }

    fn full_config() -> AutonomyConfig {
        AutonomyConfig {
            level: AutonomyLevel::Full,
            ..AutonomyConfig::default()
        }
    }

    // ── needs_approval ───────────────────────────────────────

    #[test]
    fn auto_approve_tools_skip_prompt() {
        let mgr = ApprovalManager::from_config(&supervised_config());
        assert!(!mgr.needs_approval("file_read"));
        assert!(!mgr.needs_approval("weather"));
    }

    #[test]
    fn always_ask_tools_always_prompt() {
        let mgr = ApprovalManager::from_config(&supervised_config());
        assert!(mgr.needs_approval("shell"));
    }

    #[test]
    fn unknown_tool_needs_approval_in_supervised() {
        let mgr = ApprovalManager::from_config(&supervised_config());
        assert!(mgr.needs_approval("file_write"));
        assert!(mgr.needs_approval("http_request"));
    }

    #[test]
    fn full_autonomy_never_prompts() {
        let mgr = ApprovalManager::from_config(&full_config());
        assert!(!mgr.needs_approval("shell"));
        assert!(!mgr.needs_approval("file_write"));
        assert!(!mgr.needs_approval("anything"));
    }

    #[test]
    fn readonly_never_prompts() {
        let config = AutonomyConfig {
            level: AutonomyLevel::ReadOnly,
            ..AutonomyConfig::default()
        };
        let mgr = ApprovalManager::from_config(&config);
        assert!(!mgr.needs_approval("shell"));
    }

    // ── session allowlist ────────────────────────────────────

    #[test]
    fn always_response_adds_to_session_allowlist() {
        let mgr = ApprovalManager::from_config(&supervised_config());
        assert!(mgr.needs_approval("file_write"));

        mgr.record_decision(
            "file_write",
            &serde_json::json!({"path": "test.txt"}),
            ApprovalResponse::Always,
            "cli",
        );

        // Now file_write should be in session allowlist.
        assert!(!mgr.needs_approval("file_write"));
    }

    #[test]
    fn always_ask_overrides_session_allowlist() {
        let mgr = ApprovalManager::from_config(&supervised_config());

        // Even after "Always" for shell, it should still prompt.
        mgr.record_decision(
            "shell",
            &serde_json::json!({"command": "ls"}),
            ApprovalResponse::Always,
            "cli",
        );

        // shell is in always_ask, so it still needs approval.
        assert!(mgr.needs_approval("shell"));
    }

    #[test]
    fn yes_response_does_not_add_to_allowlist() {
        let mgr = ApprovalManager::from_config(&supervised_config());
        mgr.record_decision(
            "file_write",
            &serde_json::json!({}),
            ApprovalResponse::Yes,
            "cli",
        );
        assert!(mgr.needs_approval("file_write"));
    }

    // ── audit log ────────────────────────────────────────────

    // ── trust integration ────────────────────────────────────

    #[test]
    fn trust_regression_downgrades_full_to_supervised_approval() {
        use crate::trust::{CorrectionType, TrustConfig, TrustTracker};

        let config = AutonomyConfig {
            level: AutonomyLevel::Full,
            ..AutonomyConfig::default()
        };
        let trust = Arc::new(Mutex::new(TrustTracker::new(TrustConfig::default())));
        let mgr = ApprovalManager::from_config(&config).with_trust_tracker(Arc::clone(&trust));

        // With no corrections, Full autonomy skips approval for any tool.
        assert!(!mgr.needs_approval("shell"));

        // Drive the tool's domain into regression.
        for _ in 0..10 {
            trust.lock().record_correction(
                "shell",
                CorrectionType::UserOverride,
                "test regression",
            );
        }

        // Regression downgrades Full → Supervised, so unknown tool now prompts.
        assert!(mgr.needs_approval("shell"));
        // Other, unaffected tool still benefits from Full autonomy.
        assert!(!mgr.needs_approval("file_write"));
    }

    #[test]
    fn trust_regression_in_supervised_forces_prompt_not_silent_bypass() {
        // Regression: before this fix, seven denials in Supervised mode
        // downgraded the tool's per-tool level to ReadOnly. The ReadOnly
        // branch in needs_approval returned false under the old comment
        // "ReadOnly blocks everything — handled elsewhere", but "elsewhere"
        // (SecurityPolicy::can_act) only inspects GLOBAL autonomy. The
        // denied tool then executed without a prompt — the exact inverse of
        // the feature's intent.
        use crate::trust::{CorrectionType, TrustConfig, TrustTracker};

        let trust = Arc::new(Mutex::new(TrustTracker::new(TrustConfig::default())));
        let mgr = ApprovalManager::from_config(&supervised_config())
            .with_trust_tracker(Arc::clone(&trust));

        // Drive the tool's domain into regression. Default config:
        // initial_score=0.8, correction_penalty=0.05, threshold=0.5 →
        // 7 denials put us at 0.45, below threshold.
        for _ in 0..7 {
            trust
                .lock()
                .record_correction("file_write", CorrectionType::UserOverride, "test");
        }

        assert!(
            mgr.needs_approval("file_write"),
            "per-tool regression (Supervised→ReadOnly) must still prompt"
        );
        // Sanity: global ReadOnly still returns false (blocked downstream).
        let readonly_config = AutonomyConfig {
            level: AutonomyLevel::ReadOnly,
            ..AutonomyConfig::default()
        };
        let readonly_mgr =
            ApprovalManager::from_config(&readonly_config).with_trust_tracker(Arc::clone(&trust));
        assert!(!readonly_mgr.needs_approval("file_write"));
    }

    #[test]
    fn denial_records_user_override_correction_when_trust_attached() {
        use crate::trust::{TrustConfig, TrustTracker};

        let trust = Arc::new(Mutex::new(TrustTracker::new(TrustConfig::default())));
        let mgr = ApprovalManager::from_config(&supervised_config())
            .with_trust_tracker(Arc::clone(&trust));

        let before = trust.lock().get_score("file_write");
        mgr.record_decision(
            "file_write",
            &serde_json::json!({"path": "x"}),
            ApprovalResponse::No,
            "cli",
        );
        let after = trust.lock().get_score("file_write");
        assert!(after < before, "denial should reduce trust score");
    }

    #[test]
    fn audit_log_records_decisions() {
        let mgr = ApprovalManager::from_config(&supervised_config());

        mgr.record_decision(
            "shell",
            &serde_json::json!({"command": "rm -rf ./build/"}),
            ApprovalResponse::No,
            "cli",
        );
        mgr.record_decision(
            "file_write",
            &serde_json::json!({"path": "out.txt", "content": "hello"}),
            ApprovalResponse::Yes,
            "cli",
        );

        let log = mgr.audit_log();
        assert_eq!(log.len(), 2);
        assert_eq!(log[0].tool_name, "shell");
        assert_eq!(log[0].decision, ApprovalResponse::No);
        assert_eq!(log[1].tool_name, "file_write");
        assert_eq!(log[1].decision, ApprovalResponse::Yes);
    }

    #[test]
    fn audit_log_contains_timestamp_and_channel() {
        let mgr = ApprovalManager::from_config(&supervised_config());
        mgr.record_decision(
            "shell",
            &serde_json::json!({"command": "ls"}),
            ApprovalResponse::Yes,
            "telegram",
        );

        let log = mgr.audit_log();
        assert_eq!(log.len(), 1);
        assert!(!log[0].timestamp.is_empty());
        assert_eq!(log[0].channel, "telegram");
    }

    // ── summarize_args ───────────────────────────────────────

    #[test]
    fn summarize_args_object() {
        let args = serde_json::json!({"command": "ls -la", "cwd": "/tmp"});
        let summary = summarize_args(&args);
        assert!(summary.contains("command: ls -la"));
        assert!(summary.contains("cwd: /tmp"));
    }

    #[test]
    fn summarize_args_truncates_long_values() {
        let long_val = "x".repeat(200);
        let args = serde_json::json!({ "content": long_val });
        let summary = summarize_args(&args);
        assert!(summary.contains('…'));
        assert!(summary.len() < 200);
    }

    #[test]
    fn summarize_args_unicode_safe_truncation() {
        let long_val = "🦀".repeat(120);
        let args = serde_json::json!({ "content": long_val });
        let summary = summarize_args(&args);
        assert!(summary.contains("content:"));
        assert!(summary.contains('…'));
    }

    #[test]
    fn summarize_args_non_object() {
        let args = serde_json::json!("just a string");
        let summary = summarize_args(&args);
        assert!(summary.contains("just a string"));
    }

    // ── non-interactive (channel) mode ────────────────────────

    #[test]
    fn non_interactive_manager_reports_non_interactive() {
        let mgr = ApprovalManager::for_non_interactive(&supervised_config());
        assert!(mgr.is_non_interactive());
    }

    #[test]
    fn interactive_manager_reports_interactive() {
        let mgr = ApprovalManager::from_config(&supervised_config());
        assert!(!mgr.is_non_interactive());
    }

    #[test]
    fn non_interactive_auto_approve_tools_skip_approval() {
        let mgr = ApprovalManager::for_non_interactive(&supervised_config());
        // auto_approve tools (file_read, weather) should not need approval.
        assert!(!mgr.needs_approval("file_read"));
        assert!(!mgr.needs_approval("weather"));
    }

    #[test]
    fn non_interactive_shell_skips_outer_approval_by_default() {
        let mgr = ApprovalManager::for_non_interactive(&AutonomyConfig::default());
        assert!(!mgr.needs_approval("shell"));
    }

    #[test]
    fn non_interactive_always_ask_tools_need_approval() {
        let mgr = ApprovalManager::for_non_interactive(&supervised_config());
        // always_ask tools (shell) still report as needing approval,
        // so the tool-call loop will auto-deny them in non-interactive mode.
        assert!(mgr.needs_approval("shell"));
    }

    #[test]
    fn non_interactive_unknown_tools_need_approval_in_supervised() {
        let mgr = ApprovalManager::for_non_interactive(&supervised_config());
        // Unknown tools in supervised mode need approval (will be auto-denied
        // by the tool-call loop for non-interactive managers).
        assert!(mgr.needs_approval("file_write"));
        assert!(mgr.needs_approval("http_request"));
    }

    #[test]
    fn non_interactive_full_autonomy_never_needs_approval() {
        let mgr = ApprovalManager::for_non_interactive(&full_config());
        // Full autonomy means no approval needed, even in non-interactive mode.
        assert!(!mgr.needs_approval("shell"));
        assert!(!mgr.needs_approval("file_write"));
        assert!(!mgr.needs_approval("anything"));
    }

    #[test]
    fn non_interactive_readonly_never_needs_approval() {
        let config = AutonomyConfig {
            level: AutonomyLevel::ReadOnly,
            ..AutonomyConfig::default()
        };
        let mgr = ApprovalManager::for_non_interactive(&config);
        // ReadOnly blocks execution elsewhere; approval manager does not prompt.
        assert!(!mgr.needs_approval("shell"));
    }

    #[test]
    fn non_interactive_session_allowlist_still_works() {
        let mgr = ApprovalManager::for_non_interactive(&supervised_config());
        assert!(mgr.needs_approval("file_write"));

        // Simulate an "Always" decision (would come from a prior channel run
        // if the tool was auto-approved somehow, e.g. via config change).
        mgr.record_decision(
            "file_write",
            &serde_json::json!({"path": "test.txt"}),
            ApprovalResponse::Always,
            "telegram",
        );

        assert!(!mgr.needs_approval("file_write"));
    }

    #[test]
    fn non_interactive_always_ask_overrides_session_allowlist() {
        let mgr = ApprovalManager::for_non_interactive(&supervised_config());

        mgr.record_decision(
            "shell",
            &serde_json::json!({"command": "ls"}),
            ApprovalResponse::Always,
            "telegram",
        );

        // shell is in always_ask, so it still needs approval even after "Always".
        assert!(mgr.needs_approval("shell"));
    }

    // ── ApprovalResponse serde ───────────────────────────────

    #[test]
    fn approval_response_serde_roundtrip() {
        let json = serde_json::to_string(&ApprovalResponse::Always).unwrap();
        assert_eq!(json, "\"always\"");
        let parsed: ApprovalResponse = serde_json::from_str("\"no\"").unwrap();
        assert_eq!(parsed, ApprovalResponse::No);
    }

    // ── ApprovalRequest ──────────────────────────────────────

    #[test]
    fn approval_request_serde() {
        let req = ApprovalRequest {
            tool_name: "shell".into(),
            arguments: serde_json::json!({"command": "echo hi"}),
        };
        let json = serde_json::to_string(&req).unwrap();
        let parsed: ApprovalRequest = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed.tool_name, "shell");
    }

    // ── Regression: #4247 default approved tools in channels ──

    #[test]
    fn non_interactive_allows_default_auto_approve_tools() {
        let config = AutonomyConfig::default();
        let mgr = ApprovalManager::for_non_interactive(&config);

        for tool in &config.auto_approve {
            assert!(
                !mgr.needs_approval(tool),
                "default auto_approve tool '{tool}' should not need approval in non-interactive mode"
            );
        }
    }

    #[test]
    fn non_interactive_denies_unknown_tools() {
        let config = AutonomyConfig::default();
        let mgr = ApprovalManager::for_non_interactive(&config);
        assert!(
            mgr.needs_approval("some_unknown_tool"),
            "unknown tool should need approval"
        );
    }

    #[test]
    fn non_interactive_weather_is_auto_approved() {
        let config = AutonomyConfig::default();
        let mgr = ApprovalManager::for_non_interactive(&config);
        assert!(
            !mgr.needs_approval("weather"),
            "weather tool must not need approval — it is in the default auto_approve list"
        );
    }

    #[test]
    fn always_ask_overrides_auto_approve() {
        let mut config = AutonomyConfig::default();
        config.always_ask = vec!["weather".into()];
        let mgr = ApprovalManager::for_non_interactive(&config);
        assert!(
            mgr.needs_approval("weather"),
            "always_ask must override auto_approve"
        );
    }

    // ── MCP-namespaced tools in non-interactive mode ─────────

    #[test]
    fn non_interactive_auto_approves_mcp_tools() {
        let config = AutonomyConfig::default();
        let mgr = ApprovalManager::for_non_interactive(&config);
        assert!(
            !mgr.needs_approval("kumiho-memory__kumiho_memory_engage"),
            "MCP-namespaced tools should be auto-approved in non-interactive mode"
        );
        assert!(
            !mgr.needs_approval("operator-tools__create_agent"),
            "MCP-namespaced tools should be auto-approved in non-interactive mode"
        );
    }

    #[test]
    fn interactive_mcp_tools_still_need_approval() {
        let config = supervised_config();
        let mgr = ApprovalManager::from_config(&config);
        assert!(
            mgr.needs_approval("kumiho-memory__kumiho_memory_engage"),
            "MCP tools should still need approval in interactive supervised mode"
        );
    }

    #[test]
    fn non_interactive_always_ask_overrides_mcp_auto_approve() {
        let mut config = AutonomyConfig::default();
        config.always_ask = vec!["kumiho-memory__kumiho_memory_engage".into()];
        let mgr = ApprovalManager::for_non_interactive(&config);
        assert!(
            mgr.needs_approval("kumiho-memory__kumiho_memory_engage"),
            "always_ask should override MCP auto-approve"
        );
    }
}
