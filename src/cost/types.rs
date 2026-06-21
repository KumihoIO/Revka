use serde::{Deserialize, Serialize};

/// Token usage information from a single API call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TokenUsage {
    /// Model identifier (e.g., "anthropic/claude-sonnet-4-20250514")
    pub model: String,
    /// Input/prompt tokens
    pub input_tokens: u64,
    /// Output/completion tokens
    pub output_tokens: u64,
    /// Total tokens
    pub total_tokens: u64,
    /// Calculated cost in USD
    pub cost_usd: f64,
    /// Timestamp of the request
    pub timestamp: chrono::DateTime<chrono::Utc>,
}

impl TokenUsage {
    fn sanitize_price(value: f64) -> f64 {
        if value.is_finite() && value > 0.0 {
            value
        } else {
            0.0
        }
    }

    /// Create a new token usage record.
    pub fn new(
        model: impl Into<String>,
        input_tokens: u64,
        output_tokens: u64,
        input_price_per_million: f64,
        output_price_per_million: f64,
    ) -> Self {
        let model = model.into();
        let input_price_per_million = Self::sanitize_price(input_price_per_million);
        let output_price_per_million = Self::sanitize_price(output_price_per_million);
        let total_tokens = input_tokens.saturating_add(output_tokens);

        // Calculate cost: (tokens / 1M) * price_per_million
        let input_cost = (input_tokens as f64 / 1_000_000.0) * input_price_per_million;
        let output_cost = (output_tokens as f64 / 1_000_000.0) * output_price_per_million;
        let cost_usd = input_cost + output_cost;

        Self {
            model,
            input_tokens,
            output_tokens,
            total_tokens,
            cost_usd,
            timestamp: chrono::Utc::now(),
        }
    }

    /// Get the total cost.
    pub fn cost(&self) -> f64 {
        self.cost_usd
    }
}

/// Time period for cost aggregation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum UsagePeriod {
    Session,
    Day,
    Month,
}

/// A single cost record for persistent storage.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CostRecord {
    /// Unique identifier
    pub id: String,
    /// Token usage details
    pub usage: TokenUsage,
    /// Session identifier (for grouping)
    pub session_id: String,
    /// Optional origin metadata for unified runtime + sidecar accounting.
    #[serde(default)]
    pub metadata: CostRecordMetadata,
}

/// Optional metadata attached to a cost record.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CostRecordMetadata {
    /// Runtime surface that produced the usage, e.g. `gateway`, `channel`, `sidecar`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    /// Provider family when known, e.g. `openai-codex`, `anthropic`, `claude`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    /// Sidecar/operator agent id when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_id: Option<String>,
    /// Human-readable sidecar/operator agent title when known.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_title: Option<String>,
}

impl CostRecord {
    /// Create a new cost record.
    pub fn new(session_id: impl Into<String>, usage: TokenUsage) -> Self {
        Self::new_with_metadata(session_id, usage, CostRecordMetadata::default())
    }

    /// Create a new cost record with origin metadata.
    pub fn new_with_metadata(
        session_id: impl Into<String>,
        usage: TokenUsage,
        metadata: CostRecordMetadata,
    ) -> Self {
        Self {
            id: uuid::Uuid::new_v4().to_string(),
            usage,
            session_id: session_id.into(),
            metadata,
        }
    }
}

/// Budget enforcement result.
#[derive(Debug, Clone)]
pub enum BudgetCheck {
    /// Within budget, request can proceed
    Allowed,
    /// Warning threshold exceeded but request can proceed
    Warning {
        current_usd: f64,
        limit_usd: f64,
        period: UsagePeriod,
    },
    /// Budget exceeded, request blocked
    Exceeded {
        current_usd: f64,
        limit_usd: f64,
        period: UsagePeriod,
    },
}

/// Action the agent loop should take after a budget check, derived from the
/// configured `[cost.enforcement] mode` (and `allow_override`).
#[derive(Debug, Clone)]
pub enum BudgetEnforcement {
    /// Within budget (or only a warning) — proceed normally.
    Proceed,
    /// Budget exceeded but configured `mode` (or `allow_override`) permits the
    /// call to continue. Carries a human-readable reason for logging.
    Warn { reason: String },
    /// Budget exceeded and `mode = "route_down"` — continue, but downgrade the
    /// model to the configured `route_down_model`.
    RouteDown { model: String, reason: String },
    /// Budget exceeded and `mode = "block"` (or `route_down` without a target) —
    /// hard-stop the call. Carries the overage details for the error message.
    Block {
        current_usd: f64,
        limit_usd: f64,
        period: UsagePeriod,
    },
}

/// Cost summary for reporting.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CostSummary {
    /// Total cost for the session
    pub session_cost_usd: f64,
    /// Total cost for the day
    pub daily_cost_usd: f64,
    /// Total cost for the month
    pub monthly_cost_usd: f64,
    /// Total tokens used
    pub total_tokens: u64,
    /// Number of requests
    pub request_count: usize,
    /// Breakdown by model
    pub by_model: std::collections::HashMap<String, ModelStats>,
    /// Breakdown by sidecar/operator agent id.
    #[serde(default)]
    pub by_agent: std::collections::HashMap<String, AgentStats>,
    /// Breakdown by runtime source.
    #[serde(default)]
    pub by_source: std::collections::HashMap<String, SourceStats>,
    /// Current configured budget status.
    #[serde(default)]
    pub budget: BudgetStatus,
}

/// Statistics for a specific model.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelStats {
    /// Model name
    pub model: String,
    /// Total cost for this model
    pub cost_usd: f64,
    /// Total tokens for this model
    pub total_tokens: u64,
    /// Number of requests for this model
    pub request_count: usize,
}

/// Statistics for a sidecar/operator agent.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AgentStats {
    /// Agent id.
    pub agent_id: String,
    /// Latest known human-readable title.
    pub agent_title: Option<String>,
    /// Runtime source, e.g. `sidecar`.
    pub source: Option<String>,
    /// Total cost for this agent.
    pub cost_usd: f64,
    /// Total tokens for this agent.
    pub total_tokens: u64,
    /// Number of requests for this agent.
    pub request_count: usize,
    /// Breakdown by model for this agent.
    pub by_model: std::collections::HashMap<String, ModelStats>,
}

/// Statistics for a runtime source.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SourceStats {
    /// Runtime source.
    pub source: String,
    /// Total cost for this source.
    pub cost_usd: f64,
    /// Total tokens for this source.
    pub total_tokens: u64,
    /// Number of requests for this source.
    pub request_count: usize,
}

/// Configured budget and current utilization.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BudgetStatus {
    /// Whether cost tracking is enabled.
    pub enabled: bool,
    /// Daily spending limit in USD.
    pub daily_limit_usd: f64,
    /// Monthly spending limit in USD.
    pub monthly_limit_usd: f64,
    /// Warning threshold percentage.
    pub warn_at_percent: u8,
    /// Remaining daily budget in USD.
    pub daily_remaining_usd: f64,
    /// Remaining monthly budget in USD.
    pub monthly_remaining_usd: f64,
    /// Daily budget utilization percentage.
    pub daily_percent: f64,
    /// Monthly budget utilization percentage.
    pub monthly_percent: f64,
    /// `ok`, `warning`, `exceeded`, or `disabled`.
    pub state: String,
}

impl Default for BudgetStatus {
    fn default() -> Self {
        Self {
            enabled: false,
            daily_limit_usd: 0.0,
            monthly_limit_usd: 0.0,
            warn_at_percent: 0,
            daily_remaining_usd: 0.0,
            monthly_remaining_usd: 0.0,
            daily_percent: 0.0,
            monthly_percent: 0.0,
            state: "disabled".to_string(),
        }
    }
}

impl Default for CostSummary {
    fn default() -> Self {
        Self {
            session_cost_usd: 0.0,
            daily_cost_usd: 0.0,
            monthly_cost_usd: 0.0,
            total_tokens: 0,
            request_count: 0,
            by_model: std::collections::HashMap::new(),
            by_agent: std::collections::HashMap::new(),
            by_source: std::collections::HashMap::new(),
            budget: BudgetStatus::default(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn token_usage_calculation() {
        let usage = TokenUsage::new("test/model", 1000, 500, 3.0, 15.0);

        // Expected: (1000/1M)*3 + (500/1M)*15 = 0.003 + 0.0075 = 0.0105
        assert!((usage.cost_usd - 0.0105).abs() < 0.0001);
        assert_eq!(usage.input_tokens, 1000);
        assert_eq!(usage.output_tokens, 500);
        assert_eq!(usage.total_tokens, 1500);
    }

    #[test]
    fn token_usage_zero_tokens() {
        let usage = TokenUsage::new("test/model", 0, 0, 3.0, 15.0);
        assert!(usage.cost_usd.abs() < f64::EPSILON);
        assert_eq!(usage.total_tokens, 0);
    }

    #[test]
    fn token_usage_negative_or_non_finite_prices_are_clamped() {
        let usage = TokenUsage::new("test/model", 1000, 1000, -3.0, f64::NAN);
        assert!(usage.cost_usd.abs() < f64::EPSILON);
        assert_eq!(usage.total_tokens, 2000);
    }

    #[test]
    fn cost_record_creation() {
        let usage = TokenUsage::new("test/model", 100, 50, 1.0, 2.0);
        let record = CostRecord::new("session-123", usage);

        assert_eq!(record.session_id, "session-123");
        assert!(!record.id.is_empty());
        assert_eq!(record.usage.model, "test/model");
        assert!(record.metadata.source.is_none());
    }
}
