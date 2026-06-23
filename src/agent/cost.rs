use crate::cost::CostTracker;
use crate::cost::types::{BudgetCheck, BudgetEnforcement, CostRecordMetadata};
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

/// Check budget before an LLM call and resolve the configured enforcement
/// directive (`warn`/`block`/`route_down`, honoring `allow_override`).
/// Returns `None` when no cost tracking context is scoped (tests, delegate,
/// CLI without cost config), in which case the call should proceed.
///
/// The check is predictive: it estimates the cost of the request about to be
/// made from `input_tokens` (an estimate of the prepared request) plus
/// `output_reserve_tokens` (an allowance for the generation), so the request
/// that would breach the limit is blocked *before* it is sent rather than after
/// (#456). The estimate degrades to `0.0` — i.e. the prior reactive behavior —
/// when the active model has no pricing entry.
pub(crate) fn check_tool_loop_budget(
    provider_name: &str,
    model: &str,
    input_tokens: u64,
    output_reserve_tokens: u64,
) -> Option<BudgetEnforcement> {
    TOOL_LOOP_COST_TRACKING_CONTEXT
        .try_with(Clone::clone)
        .ok()
        .flatten()
        .map(|ctx| {
            let estimated_cost_usd = ctx.tracker.estimate_request_cost(
                provider_name,
                model,
                input_tokens,
                output_reserve_tokens,
            );
            let check = ctx
                .tracker
                .check_budget(estimated_cost_usd)
                .unwrap_or(BudgetCheck::Allowed);
            ctx.tracker.resolve_enforcement(&check)
        })
}
