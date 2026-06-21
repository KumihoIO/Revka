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

/// Action the tool loop should take when the budget is exceeded, derived from
/// the configured `enforcement.mode`.
pub(crate) enum BudgetDecision {
    /// Within budget (or warning only) — proceed normally.
    Proceed,
    /// Budget exceeded; hard-block with this overage detail.
    Block {
        current_usd: f64,
        limit_usd: f64,
        period: crate::cost::types::UsagePeriod,
    },
    /// Budget exceeded; downgrade to a cheaper model and continue.
    RouteDown { model: String },
}

/// Resolve the budget gate for the current LLM call, applying the configured
/// `enforcement.mode`. `active_model` is the model the loop is about to use,
/// used to avoid re-routing when already on the route-down target.
///
/// Returns `None` when no cost tracking context is scoped.
pub(crate) fn decide_tool_loop_budget(active_model: &str) -> Option<BudgetDecision> {
    let ctx = TOOL_LOOP_COST_TRACKING_CONTEXT
        .try_with(Clone::clone)
        .ok()
        .flatten()?;

    let check = ctx
        .tracker
        .check_budget(0.0)
        .unwrap_or(BudgetCheck::Allowed);

    let BudgetCheck::Exceeded {
        current_usd,
        limit_usd,
        period,
    } = check
    else {
        return Some(BudgetDecision::Proceed);
    };

    let block = BudgetDecision::Block {
        current_usd,
        limit_usd,
        period,
    };

    match ctx.tracker.enforcement_mode() {
        // Warn: annotate the overage but let the call proceed.
        "warn" => {
            tracing::warn!(
                current_usd,
                limit_usd,
                ?period,
                "Budget exceeded; proceeding because enforcement mode is \"warn\""
            );
            Some(BudgetDecision::Proceed)
        }
        // Route down: switch to the cheaper model when one is configured and we
        // are not already using it; otherwise fall back to blocking.
        "route_down" => match ctx.tracker.route_down_model() {
            Some(target) if target != active_model => {
                tracing::warn!(
                    current_usd,
                    limit_usd,
                    ?period,
                    target,
                    "Budget exceeded; routing down to cheaper model"
                );
                Some(BudgetDecision::RouteDown {
                    model: target.to_string(),
                })
            }
            _ => Some(block),
        },
        // Block (and any unknown mode): keep the hard limit.
        _ => Some(block),
    }
}
