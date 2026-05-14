use crate::cost::CostTracker;
use crate::cost::types::{BudgetCheck, CostRecordMetadata};
use std::sync::Arc;

// ── Cost tracking via task-local ──

/// Context for cost tracking within the tool call loop.
/// Scoped via `tokio::task_local!` at call sites (channels, gateway).
#[derive(Clone)]
pub(crate) struct ToolLoopCostTrackingContext {
    pub tracker: Arc<CostTracker>,
    pub source: String,
}

impl ToolLoopCostTrackingContext {
    pub(crate) fn new(tracker: Arc<CostTracker>, source: impl Into<String>) -> Self {
        Self {
            tracker,
            source: source.into(),
        }
    }
}

tokio::task_local! {
    pub(crate) static TOOL_LOOP_COST_TRACKING_CONTEXT: Option<ToolLoopCostTrackingContext>;
}

/// Record token usage from an LLM response via the task-local cost tracker.
/// Returns `(total_tokens, cost_usd)` on success, `None` when not scoped or no usage.
pub(crate) fn record_tool_loop_cost_usage(
    provider_name: &str,
    model: &str,
    usage: &crate::providers::traits::TokenUsage,
) -> Option<(u64, f64)> {
    let input_tokens = usage.input_tokens.unwrap_or(0);
    let output_tokens = usage.output_tokens.unwrap_or(0);
    let total_tokens = input_tokens.saturating_add(output_tokens);

    let ctx = TOOL_LOOP_COST_TRACKING_CONTEXT
        .try_with(Clone::clone)
        .ok()
        .flatten()?;

    if total_tokens == 0 {
        tracing::warn!(
            provider = provider_name,
            model,
            "Cost tracking received zero-token usage; recording request with zero tokens (provider may not be reporting usage)"
        );
    }
    let metadata = CostRecordMetadata {
        source: Some(ctx.source),
        provider: Some(provider_name.to_string()),
        ..Default::default()
    };

    match ctx.tracker.record_usage_from_tokens(
        provider_name,
        model,
        input_tokens,
        output_tokens,
        metadata,
    ) {
        Ok(cost_usage) => Some((cost_usage.total_tokens, cost_usage.cost_usd)),
        Err(error) => {
            tracing::warn!(
                provider = provider_name,
                model,
                "Failed to record cost tracking usage: {error}"
            );
            Some((total_tokens, 0.0))
        }
    }
}

/// Check budget before an LLM call. Returns `None` when no cost tracking
/// context is scoped (tests, delegate, CLI without cost config).
pub(crate) fn check_tool_loop_budget() -> Option<BudgetCheck> {
    TOOL_LOOP_COST_TRACKING_CONTEXT
        .try_with(Clone::clone)
        .ok()
        .flatten()
        .map(|ctx| {
            ctx.tracker
                .check_budget(0.0)
                .unwrap_or(BudgetCheck::Allowed)
        })
}
