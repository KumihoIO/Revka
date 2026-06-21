use super::types::{
    AgentStats, BudgetCheck, BudgetEnforcement, BudgetStatus, CostRecord, CostRecordMetadata,
    CostSummary, ModelStats, SourceStats, TokenUsage, UsagePeriod,
};
use crate::config::schema::{CostConfig, ModelPricing};
use anyhow::{Context, Result, anyhow};
use chrono::{Datelike, NaiveDate, Utc};
use parking_lot::{Mutex, MutexGuard};
use std::collections::HashMap;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, OnceLock};

/// Cost tracker for API usage monitoring and budget enforcement.
pub struct CostTracker {
    config: CostConfig,
    storage: Arc<Mutex<CostStorage>>,
    session_id: String,
    session_costs: Arc<Mutex<Vec<CostRecord>>>,
}

impl CostTracker {
    /// Create a new cost tracker.
    pub fn new(config: CostConfig, workspace_dir: &Path) -> Result<Self> {
        let storage_path = resolve_storage_path(workspace_dir)?;

        let storage = CostStorage::new(&storage_path).with_context(|| {
            format!("Failed to open cost storage at {}", storage_path.display())
        })?;

        Ok(Self {
            config,
            storage: Arc::new(Mutex::new(storage)),
            session_id: uuid::Uuid::new_v4().to_string(),
            session_costs: Arc::new(Mutex::new(Vec::new())),
        })
    }

    /// Get the session ID.
    pub fn session_id(&self) -> &str {
        &self.session_id
    }

    fn lock_storage(&self) -> MutexGuard<'_, CostStorage> {
        self.storage.lock()
    }

    fn lock_session_costs(&self) -> MutexGuard<'_, Vec<CostRecord>> {
        self.session_costs.lock()
    }

    /// Check if a request is within budget.
    pub fn check_budget(&self, estimated_cost_usd: f64) -> Result<BudgetCheck> {
        if !self.config.enabled {
            return Ok(BudgetCheck::Allowed);
        }

        if !estimated_cost_usd.is_finite() || estimated_cost_usd < 0.0 {
            return Err(anyhow!(
                "Estimated cost must be a finite, non-negative value"
            ));
        }

        let mut storage = self.lock_storage();
        let (daily_cost, monthly_cost) = storage.get_aggregated_costs()?;

        // `reserve_percent` carves a soft buffer off the top of each limit so a
        // request that would dip into the reserve is treated as exceeding the
        // effective limit. Reported `limit_usd` stays the configured value.
        let reserve_fraction = f64::from(self.config.enforcement.reserve_percent.min(100)) / 100.0;
        let daily_effective_limit = self.config.daily_limit_usd * (1.0 - reserve_fraction);
        let monthly_effective_limit = self.config.monthly_limit_usd * (1.0 - reserve_fraction);

        // Check daily limit
        let projected_daily = daily_cost + estimated_cost_usd;
        if projected_daily > daily_effective_limit {
            return Ok(BudgetCheck::Exceeded {
                current_usd: daily_cost,
                limit_usd: self.config.daily_limit_usd,
                period: UsagePeriod::Day,
            });
        }

        // Check monthly limit
        let projected_monthly = monthly_cost + estimated_cost_usd;
        if projected_monthly > monthly_effective_limit {
            return Ok(BudgetCheck::Exceeded {
                current_usd: monthly_cost,
                limit_usd: self.config.monthly_limit_usd,
                period: UsagePeriod::Month,
            });
        }

        // Check warning thresholds
        let warn_threshold = f64::from(self.config.warn_at_percent.min(100)) / 100.0;
        let daily_warn_threshold = self.config.daily_limit_usd * warn_threshold;
        let monthly_warn_threshold = self.config.monthly_limit_usd * warn_threshold;

        if projected_daily >= daily_warn_threshold {
            return Ok(BudgetCheck::Warning {
                current_usd: daily_cost,
                limit_usd: self.config.daily_limit_usd,
                period: UsagePeriod::Day,
            });
        }

        if projected_monthly >= monthly_warn_threshold {
            return Ok(BudgetCheck::Warning {
                current_usd: monthly_cost,
                limit_usd: self.config.monthly_limit_usd,
                period: UsagePeriod::Month,
            });
        }

        Ok(BudgetCheck::Allowed)
    }

    /// Map a budget check into an enforcement directive based on the configured
    /// `[cost.enforcement] mode` and `allow_override`.
    ///
    /// Only `BudgetCheck::Exceeded` is mode-sensitive; `Allowed`/`Warning`
    /// always `Proceed`. The previous behavior (always hard-block on exceed)
    /// now corresponds only to `mode = "block"`.
    pub fn resolve_enforcement(&self, check: &BudgetCheck) -> BudgetEnforcement {
        let (current_usd, limit_usd, period) = match check {
            BudgetCheck::Allowed | BudgetCheck::Warning { .. } => {
                return BudgetEnforcement::Proceed;
            }
            BudgetCheck::Exceeded {
                current_usd,
                limit_usd,
                period,
            } => (*current_usd, *limit_usd, *period),
        };

        // A per-request override bypasses enforcement entirely.
        if self.config.allow_override {
            return BudgetEnforcement::Warn {
                reason: format!(
                    "budget exceeded (${current_usd:.4} of ${limit_usd:.2} {period:?} limit) but allow_override is set; proceeding"
                ),
            };
        }

        let block = || BudgetEnforcement::Block {
            current_usd,
            limit_usd,
            period,
        };

        match self.config.enforcement.mode.as_str() {
            "warn" => BudgetEnforcement::Warn {
                reason: format!(
                    "budget exceeded (${current_usd:.4} of ${limit_usd:.2} {period:?} limit); enforcement mode is 'warn', proceeding"
                ),
            },
            "route_down" => match self.config.enforcement.route_down_model.as_deref() {
                Some(model) if !model.is_empty() => BudgetEnforcement::RouteDown {
                    model: model.to_string(),
                    reason: format!(
                        "budget exceeded (${current_usd:.4} of ${limit_usd:.2} {period:?} limit); routing down to '{model}'"
                    ),
                },
                _ => {
                    tracing::warn!(
                        "cost enforcement mode is 'route_down' but no route_down_model is configured; blocking"
                    );
                    block()
                }
            },
            // "block" and any unrecognized mode fall back to the safe hard-stop.
            other => {
                if other != "block" {
                    tracing::warn!(
                        "unknown cost enforcement mode '{other}'; defaulting to 'block'"
                    );
                }
                block()
            }
        }
    }

    /// Record a usage event.
    pub fn record_usage(&self, usage: TokenUsage) -> Result<()> {
        self.record_usage_with_metadata(usage, CostRecordMetadata::default())
    }

    /// Record token usage by looking up configured model pricing.
    pub fn record_usage_from_tokens(
        &self,
        provider_name: &str,
        model: &str,
        input_tokens: u64,
        output_tokens: u64,
        metadata: CostRecordMetadata,
    ) -> Result<TokenUsage> {
        let pricing = self.pricing_for(provider_name, model);
        let usage = TokenUsage::new(
            model,
            input_tokens,
            output_tokens,
            pricing.map_or(0.0, |entry| entry.input),
            pricing.map_or(0.0, |entry| entry.output),
        );

        if pricing.is_none() {
            tracing::debug!(
                provider = provider_name,
                model,
                "Cost tracking recorded token usage with zero pricing (no pricing entry found)"
            );
        }

        self.record_usage_with_metadata(usage.clone(), metadata)?;
        Ok(usage)
    }

    /// Record a usage event with origin metadata.
    pub fn record_usage_with_metadata(
        &self,
        usage: TokenUsage,
        metadata: CostRecordMetadata,
    ) -> Result<()> {
        if !self.config.enabled {
            return Ok(());
        }

        if !usage.cost_usd.is_finite() || usage.cost_usd < 0.0 {
            return Err(anyhow!(
                "Token usage cost must be a finite, non-negative value"
            ));
        }

        let record = CostRecord::new_with_metadata(&self.session_id, usage, metadata);

        // Persist first for durability guarantees.
        {
            let mut storage = self.lock_storage();
            storage.add_record(record.clone())?;
        }

        // Then update in-memory session snapshot.
        let mut session_costs = self.lock_session_costs();
        session_costs.push(record);

        Ok(())
    }

    fn pricing_for(&self, provider_name: &str, model: &str) -> Option<&ModelPricing> {
        self.config
            .prices
            .get(model)
            .or_else(|| self.config.prices.get(&format!("{provider_name}/{model}")))
            .or_else(|| {
                model
                    .rsplit_once('/')
                    .and_then(|(_, suffix)| self.config.prices.get(suffix))
            })
            .or_else(|| {
                let base = model
                    .rsplit_once('-')
                    .filter(|(_, tail)| tail.chars().all(|c| c.is_ascii_digit()))
                    .map_or(model, |(prefix, _)| prefix);

                self.config.prices.iter().find_map(|(key, entry)| {
                    let model_part = key.rsplit_once('/').map_or(key.as_str(), |(_, m)| m);
                    if model_part.starts_with(base) || base.starts_with(model_part) {
                        Some(entry)
                    } else {
                        None
                    }
                })
            })
    }

    /// Get the current cost summary.
    pub fn get_summary(&self) -> Result<CostSummary> {
        let (daily_cost, monthly_cost) = {
            let mut storage = self.lock_storage();
            storage.get_aggregated_costs()?
        };

        let session_costs = self.lock_session_costs();
        let session_cost: f64 = session_costs
            .iter()
            .map(|record| record.usage.cost_usd)
            .sum();
        let total_tokens: u64 = session_costs
            .iter()
            .map(|record| record.usage.total_tokens)
            .sum();
        let request_count = session_costs.len();
        let by_model = build_session_model_stats(&session_costs);
        let by_agent = build_session_agent_stats(&session_costs);
        let by_source = build_session_source_stats(&session_costs);
        let budget = self.budget_status(daily_cost, monthly_cost);

        Ok(CostSummary {
            session_cost_usd: session_cost,
            daily_cost_usd: daily_cost,
            monthly_cost_usd: monthly_cost,
            total_tokens,
            request_count,
            by_model,
            by_agent,
            by_source,
            budget,
        })
    }

    fn budget_status(&self, daily_cost: f64, monthly_cost: f64) -> BudgetStatus {
        if !self.config.enabled {
            return BudgetStatus::default();
        }

        // Report against the SAME effective (reserve-adjusted) limits that
        // check_budget enforces, so this status and the budget gate agree. The
        // operator agent calls get_budget_status() to decide whether to spend,
        // so the reported limit/remaining/state must reflect the threshold
        // enforcement actually trips at — not the raw configured limit (#453).
        let reserve_factor =
            1.0 - f64::from(self.config.enforcement.reserve_percent.min(100)) / 100.0;
        let daily_limit = self.config.daily_limit_usd.max(0.0) * reserve_factor;
        let monthly_limit = self.config.monthly_limit_usd.max(0.0) * reserve_factor;
        let warn_at_percent = self.config.warn_at_percent.min(100);
        let daily_percent = percent_used(daily_cost, daily_limit);
        let monthly_percent = percent_used(monthly_cost, monthly_limit);
        let warning_threshold = f64::from(warn_at_percent);
        let state = if daily_cost > daily_limit || monthly_cost > monthly_limit {
            "exceeded"
        } else if daily_percent >= warning_threshold || monthly_percent >= warning_threshold {
            "warning"
        } else {
            "ok"
        };

        BudgetStatus {
            enabled: true,
            daily_limit_usd: daily_limit,
            monthly_limit_usd: monthly_limit,
            warn_at_percent,
            daily_remaining_usd: (daily_limit - daily_cost).max(0.0),
            monthly_remaining_usd: (monthly_limit - monthly_cost).max(0.0),
            daily_percent,
            monthly_percent,
            state: state.to_string(),
        }
    }

    /// Get the daily cost for a specific date.
    pub fn get_daily_cost(&self, date: NaiveDate) -> Result<f64> {
        let storage = self.lock_storage();
        storage.get_cost_for_date(date)
    }

    /// Get the monthly cost for a specific month.
    pub fn get_monthly_cost(&self, year: i32, month: u32) -> Result<f64> {
        let storage = self.lock_storage();
        storage.get_cost_for_month(year, month)
    }
}

// ── Process-global singleton ────────────────────────────────────────
// Both the gateway and the channels supervisor share a single CostTracker
// so that budget enforcement is consistent across all paths.

static GLOBAL_COST_TRACKER: OnceLock<Option<Arc<CostTracker>>> = OnceLock::new();

impl CostTracker {
    /// Return the process-global `CostTracker`, creating it on first call.
    /// Subsequent calls (from gateway or channels, whichever starts second)
    /// receive the same `Arc`.  Returns `None` when cost tracking is disabled
    /// or initialisation fails.
    pub fn get_or_init_global(config: CostConfig, workspace_dir: &Path) -> Option<Arc<Self>> {
        GLOBAL_COST_TRACKER
            .get_or_init(|| {
                if !config.enabled {
                    return None;
                }
                match Self::new(config, workspace_dir) {
                    Ok(ct) => Some(Arc::new(ct)),
                    Err(e) => {
                        tracing::warn!("Failed to initialize global cost tracker: {e}");
                        None
                    }
                }
            })
            .clone()
    }
}

fn resolve_storage_path(workspace_dir: &Path) -> Result<PathBuf> {
    let storage_path = workspace_dir.join("state").join("costs.jsonl");
    let legacy_path = workspace_dir.join(".revka").join("costs.db");

    if !storage_path.exists() && legacy_path.exists() {
        if let Some(parent) = storage_path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("Failed to create directory {}", parent.display()))?;
        }

        if let Err(error) = fs::rename(&legacy_path, &storage_path) {
            tracing::warn!(
                "Failed to move legacy cost storage from {} to {}: {error}; falling back to copy",
                legacy_path.display(),
                storage_path.display()
            );
            fs::copy(&legacy_path, &storage_path).with_context(|| {
                format!(
                    "Failed to copy legacy cost storage from {} to {}",
                    legacy_path.display(),
                    storage_path.display()
                )
            })?;
        }
    }

    Ok(storage_path)
}

fn build_session_model_stats(session_costs: &[CostRecord]) -> HashMap<String, ModelStats> {
    let mut by_model: HashMap<String, ModelStats> = HashMap::new();

    for record in session_costs {
        add_model_stats(&mut by_model, record);
    }

    by_model
}

fn build_session_agent_stats(session_costs: &[CostRecord]) -> HashMap<String, AgentStats> {
    let mut by_agent: HashMap<String, AgentStats> = HashMap::new();

    for record in session_costs {
        let Some(agent_id) = record.metadata.agent_id.as_deref() else {
            continue;
        };
        if agent_id.is_empty() {
            continue;
        }

        let entry = by_agent
            .entry(agent_id.to_string())
            .or_insert_with(|| AgentStats {
                agent_id: agent_id.to_string(),
                agent_title: record.metadata.agent_title.clone(),
                source: record.metadata.source.clone(),
                cost_usd: 0.0,
                total_tokens: 0,
                request_count: 0,
                by_model: HashMap::new(),
            });

        if record.metadata.agent_title.is_some() {
            entry.agent_title = record.metadata.agent_title.clone();
        }
        if record.metadata.source.is_some() {
            entry.source = record.metadata.source.clone();
        }
        entry.cost_usd += record.usage.cost_usd;
        entry.total_tokens += record.usage.total_tokens;
        entry.request_count += 1;
        add_model_stats(&mut entry.by_model, record);
    }

    by_agent
}

fn build_session_source_stats(session_costs: &[CostRecord]) -> HashMap<String, SourceStats> {
    let mut by_source: HashMap<String, SourceStats> = HashMap::new();

    for record in session_costs {
        let source = record
            .metadata
            .source
            .as_deref()
            .filter(|source| !source.is_empty())
            .unwrap_or("runtime");
        let entry = by_source
            .entry(source.to_string())
            .or_insert_with(|| SourceStats {
                source: source.to_string(),
                cost_usd: 0.0,
                total_tokens: 0,
                request_count: 0,
            });
        entry.cost_usd += record.usage.cost_usd;
        entry.total_tokens += record.usage.total_tokens;
        entry.request_count += 1;
    }

    by_source
}

fn add_model_stats(by_model: &mut HashMap<String, ModelStats>, record: &CostRecord) {
    let entry = by_model
        .entry(record.usage.model.clone())
        .or_insert_with(|| ModelStats {
            model: record.usage.model.clone(),
            cost_usd: 0.0,
            total_tokens: 0,
            request_count: 0,
        });

    entry.cost_usd += record.usage.cost_usd;
    entry.total_tokens += record.usage.total_tokens;
    entry.request_count += 1;
}

fn percent_used(cost: f64, limit: f64) -> f64 {
    if limit <= 0.0 {
        if cost > 0.0 { 100.0 } else { 0.0 }
    } else {
        (cost / limit) * 100.0
    }
}

/// Persistent storage for cost records.
struct CostStorage {
    path: PathBuf,
    daily_cost_usd: f64,
    monthly_cost_usd: f64,
    cached_day: NaiveDate,
    cached_year: i32,
    cached_month: u32,
}

impl CostStorage {
    /// Create or open cost storage.
    fn new(path: &Path) -> Result<Self> {
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .with_context(|| format!("Failed to create directory {}", parent.display()))?;
        }

        let now = Utc::now();
        let mut storage = Self {
            path: path.to_path_buf(),
            daily_cost_usd: 0.0,
            monthly_cost_usd: 0.0,
            cached_day: now.date_naive(),
            cached_year: now.year(),
            cached_month: now.month(),
        };

        storage.rebuild_aggregates(
            storage.cached_day,
            storage.cached_year,
            storage.cached_month,
        )?;

        Ok(storage)
    }

    fn for_each_record<F>(&self, mut on_record: F) -> Result<()>
    where
        F: FnMut(CostRecord),
    {
        if !self.path.exists() {
            return Ok(());
        }

        let file = File::open(&self.path)
            .with_context(|| format!("Failed to read cost storage from {}", self.path.display()))?;
        let reader = BufReader::new(file);

        for (line_number, line) in reader.lines().enumerate() {
            let raw_line = line.with_context(|| {
                format!(
                    "Failed to read line {} from cost storage {}",
                    line_number + 1,
                    self.path.display()
                )
            })?;

            let trimmed = raw_line.trim();
            if trimmed.is_empty() {
                continue;
            }

            match serde_json::from_str::<CostRecord>(trimmed) {
                Ok(record) => on_record(record),
                Err(error) => {
                    tracing::warn!(
                        "Skipping malformed cost record at {}:{}: {error}",
                        self.path.display(),
                        line_number + 1
                    );
                }
            }
        }

        Ok(())
    }

    fn rebuild_aggregates(&mut self, day: NaiveDate, year: i32, month: u32) -> Result<()> {
        let mut daily_cost = 0.0;
        let mut monthly_cost = 0.0;

        self.for_each_record(|record| {
            let timestamp = record.usage.timestamp.naive_utc();

            if timestamp.date() == day {
                daily_cost += record.usage.cost_usd;
            }

            if timestamp.year() == year && timestamp.month() == month {
                monthly_cost += record.usage.cost_usd;
            }
        })?;

        self.daily_cost_usd = daily_cost;
        self.monthly_cost_usd = monthly_cost;
        self.cached_day = day;
        self.cached_year = year;
        self.cached_month = month;

        Ok(())
    }

    fn ensure_period_cache_current(&mut self) -> Result<()> {
        let now = Utc::now();
        let day = now.date_naive();
        let year = now.year();
        let month = now.month();

        if day != self.cached_day || year != self.cached_year || month != self.cached_month {
            self.rebuild_aggregates(day, year, month)?;
        }

        Ok(())
    }

    /// Add a new record.
    fn add_record(&mut self, record: CostRecord) -> Result<()> {
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .with_context(|| format!("Failed to open cost storage at {}", self.path.display()))?;

        writeln!(file, "{}", serde_json::to_string(&record)?)
            .with_context(|| format!("Failed to write cost record to {}", self.path.display()))?;
        file.sync_all()
            .with_context(|| format!("Failed to sync cost storage at {}", self.path.display()))?;

        self.ensure_period_cache_current()?;

        let timestamp = record.usage.timestamp.naive_utc();
        if timestamp.date() == self.cached_day {
            self.daily_cost_usd += record.usage.cost_usd;
        }
        if timestamp.year() == self.cached_year && timestamp.month() == self.cached_month {
            self.monthly_cost_usd += record.usage.cost_usd;
        }

        Ok(())
    }

    /// Get aggregated costs for current day and month.
    fn get_aggregated_costs(&mut self) -> Result<(f64, f64)> {
        self.ensure_period_cache_current()?;
        Ok((self.daily_cost_usd, self.monthly_cost_usd))
    }

    /// Get cost for a specific date.
    fn get_cost_for_date(&self, date: NaiveDate) -> Result<f64> {
        let mut cost = 0.0;

        self.for_each_record(|record| {
            if record.usage.timestamp.naive_utc().date() == date {
                cost += record.usage.cost_usd;
            }
        })?;

        Ok(cost)
    }

    /// Get cost for a specific month.
    fn get_cost_for_month(&self, year: i32, month: u32) -> Result<f64> {
        let mut cost = 0.0;

        self.for_each_record(|record| {
            let timestamp = record.usage.timestamp.naive_utc();
            if timestamp.year() == year && timestamp.month() == month {
                cost += record.usage.cost_usd;
            }
        })?;

        Ok(cost)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn enabled_config() -> CostConfig {
        CostConfig {
            enabled: true,
            ..Default::default()
        }
    }

    #[test]
    fn cost_tracker_initialization() {
        let tmp = TempDir::new().unwrap();
        let tracker = CostTracker::new(enabled_config(), tmp.path()).unwrap();
        assert!(!tracker.session_id().is_empty());
    }

    #[test]
    fn budget_check_when_disabled() {
        let tmp = TempDir::new().unwrap();
        let config = CostConfig {
            enabled: false,
            ..Default::default()
        };

        let tracker = CostTracker::new(config, tmp.path()).unwrap();
        let check = tracker.check_budget(1000.0).unwrap();
        assert!(matches!(check, BudgetCheck::Allowed));
    }

    #[test]
    fn record_usage_and_get_summary() {
        let tmp = TempDir::new().unwrap();
        let tracker = CostTracker::new(enabled_config(), tmp.path()).unwrap();

        let usage = TokenUsage::new("test/model", 1000, 500, 1.0, 2.0);
        tracker.record_usage(usage).unwrap();

        let summary = tracker.get_summary().unwrap();
        assert_eq!(summary.request_count, 1);
        assert!(summary.session_cost_usd > 0.0);
        assert_eq!(summary.by_model.len(), 1);
    }

    #[test]
    fn record_usage_from_tokens_uses_pricing_and_metadata() {
        let tmp = TempDir::new().unwrap();
        let mut config = enabled_config();
        config.prices = HashMap::from([(
            "openai-codex/gpt-5".to_string(),
            ModelPricing {
                input: 1.25,
                output: 10.0,
            },
        )]);
        let tracker = CostTracker::new(config, tmp.path()).unwrap();

        let usage = tracker
            .record_usage_from_tokens(
                "openai-codex",
                "gpt-5.5",
                1_000,
                250,
                CostRecordMetadata {
                    source: Some("sidecar".to_string()),
                    provider: Some("codex".to_string()),
                    agent_id: Some("agent-1".to_string()),
                    agent_title: Some("Budget worker".to_string()),
                },
            )
            .unwrap();

        assert_eq!(usage.total_tokens, 1_250);
        assert!(usage.cost_usd > 0.0);

        let summary = tracker.get_summary().unwrap();
        assert_eq!(summary.request_count, 1);
        assert!(summary.by_model.contains_key("gpt-5.5"));
        assert_eq!(summary.by_source["sidecar"].total_tokens, 1_250);
        assert_eq!(
            summary.by_agent["agent-1"].agent_title.as_deref(),
            Some("Budget worker")
        );
        assert_eq!(
            summary.by_agent["agent-1"].by_model["gpt-5.5"].request_count,
            1
        );
        assert_eq!(summary.budget.state, "ok");
    }

    #[test]
    fn budget_exceeded_daily_limit() {
        let tmp = TempDir::new().unwrap();
        let config = CostConfig {
            enabled: true,
            daily_limit_usd: 0.01, // Very low limit
            ..Default::default()
        };

        let tracker = CostTracker::new(config, tmp.path()).unwrap();

        // Record a usage that exceeds the limit
        let usage = TokenUsage::new("test/model", 10000, 5000, 1.0, 2.0); // ~0.02 USD
        tracker.record_usage(usage).unwrap();

        let check = tracker.check_budget(0.01).unwrap();
        assert!(matches!(check, BudgetCheck::Exceeded { .. }));
    }

    #[test]
    fn summary_by_model_is_session_scoped() {
        let tmp = TempDir::new().unwrap();
        let storage_path = resolve_storage_path(tmp.path()).unwrap();
        if let Some(parent) = storage_path.parent() {
            fs::create_dir_all(parent).unwrap();
        }

        let old_record = CostRecord::new(
            "old-session",
            TokenUsage::new("legacy/model", 500, 500, 1.0, 1.0),
        );
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(storage_path)
            .unwrap();
        writeln!(file, "{}", serde_json::to_string(&old_record).unwrap()).unwrap();
        file.sync_all().unwrap();

        let tracker = CostTracker::new(enabled_config(), tmp.path()).unwrap();
        tracker
            .record_usage(TokenUsage::new("session/model", 1000, 1000, 1.0, 1.0))
            .unwrap();

        let summary = tracker.get_summary().unwrap();
        assert_eq!(summary.by_model.len(), 1);
        assert!(summary.by_model.contains_key("session/model"));
        assert!(!summary.by_model.contains_key("legacy/model"));
    }

    #[test]
    fn malformed_lines_are_ignored_while_loading() {
        let tmp = TempDir::new().unwrap();
        let storage_path = resolve_storage_path(tmp.path()).unwrap();
        if let Some(parent) = storage_path.parent() {
            fs::create_dir_all(parent).unwrap();
        }

        let valid_usage = TokenUsage::new("test/model", 1000, 0, 1.0, 1.0);
        let valid_record = CostRecord::new("session-a", valid_usage.clone());

        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(storage_path)
            .unwrap();
        writeln!(file, "{}", serde_json::to_string(&valid_record).unwrap()).unwrap();
        writeln!(file, "not-a-json-line").unwrap();
        writeln!(file).unwrap();
        file.sync_all().unwrap();

        let tracker = CostTracker::new(enabled_config(), tmp.path()).unwrap();
        let today_cost = tracker.get_daily_cost(Utc::now().date_naive()).unwrap();
        assert!((today_cost - valid_usage.cost_usd).abs() < f64::EPSILON);
    }

    #[test]
    fn invalid_budget_estimate_is_rejected() {
        let tmp = TempDir::new().unwrap();
        let tracker = CostTracker::new(enabled_config(), tmp.path()).unwrap();

        let err = tracker.check_budget(f64::NAN).unwrap_err();
        assert!(
            err.to_string()
                .contains("Estimated cost must be a finite, non-negative value")
        );
    }

    use crate::config::schema::CostEnforcementConfig;
    use crate::cost::types::BudgetEnforcement;

    fn exceeded_config(enforcement: CostEnforcementConfig, allow_override: bool) -> CostConfig {
        CostConfig {
            enabled: true,
            daily_limit_usd: 0.01,
            allow_override,
            enforcement,
            ..Default::default()
        }
    }

    fn tracker_over_budget(config: CostConfig, tmp: &TempDir) -> CostTracker {
        let tracker = CostTracker::new(config, tmp.path()).unwrap();
        // ~0.02 USD, over the 0.01 daily limit.
        tracker
            .record_usage(TokenUsage::new("test/model", 10000, 5000, 1.0, 2.0))
            .unwrap();
        tracker
    }

    #[test]
    fn enforcement_proceeds_when_within_budget() {
        let tmp = TempDir::new().unwrap();
        let tracker = CostTracker::new(enabled_config(), tmp.path()).unwrap();
        let check = tracker.check_budget(0.0).unwrap();
        assert!(matches!(
            tracker.resolve_enforcement(&check),
            BudgetEnforcement::Proceed
        ));
    }

    #[test]
    fn enforcement_warn_mode_does_not_block() {
        let tmp = TempDir::new().unwrap();
        // Default enforcement mode is "warn".
        let tracker = tracker_over_budget(
            exceeded_config(CostEnforcementConfig::default(), false),
            &tmp,
        );
        let check = tracker.check_budget(0.01).unwrap();
        assert!(matches!(check, BudgetCheck::Exceeded { .. }));
        assert!(matches!(
            tracker.resolve_enforcement(&check),
            BudgetEnforcement::Warn { .. }
        ));
    }

    #[test]
    fn enforcement_block_mode_blocks() {
        let tmp = TempDir::new().unwrap();
        let enforcement = CostEnforcementConfig {
            mode: "block".to_string(),
            ..Default::default()
        };
        let tracker = tracker_over_budget(exceeded_config(enforcement, false), &tmp);
        let check = tracker.check_budget(0.01).unwrap();
        assert!(matches!(
            tracker.resolve_enforcement(&check),
            BudgetEnforcement::Block { .. }
        ));
    }

    #[test]
    fn enforcement_route_down_uses_configured_model() {
        let tmp = TempDir::new().unwrap();
        let enforcement = CostEnforcementConfig {
            mode: "route_down".to_string(),
            route_down_model: Some("cheap/model".to_string()),
            ..Default::default()
        };
        let tracker = tracker_over_budget(exceeded_config(enforcement, false), &tmp);
        let check = tracker.check_budget(0.01).unwrap();
        match tracker.resolve_enforcement(&check) {
            BudgetEnforcement::RouteDown { model, .. } => assert_eq!(model, "cheap/model"),
            other => panic!("expected RouteDown, got {other:?}"),
        }
    }

    #[test]
    fn enforcement_route_down_without_target_blocks() {
        let tmp = TempDir::new().unwrap();
        let enforcement = CostEnforcementConfig {
            mode: "route_down".to_string(),
            route_down_model: None,
            ..Default::default()
        };
        let tracker = tracker_over_budget(exceeded_config(enforcement, false), &tmp);
        let check = tracker.check_budget(0.01).unwrap();
        assert!(matches!(
            tracker.resolve_enforcement(&check),
            BudgetEnforcement::Block { .. }
        ));
    }

    #[test]
    fn enforcement_allow_override_bypasses_block_mode() {
        let tmp = TempDir::new().unwrap();
        let enforcement = CostEnforcementConfig {
            mode: "block".to_string(),
            ..Default::default()
        };
        let tracker = tracker_over_budget(exceeded_config(enforcement, true), &tmp);
        let check = tracker.check_budget(0.01).unwrap();
        assert!(matches!(
            tracker.resolve_enforcement(&check),
            BudgetEnforcement::Warn { .. }
        ));
    }

    #[test]
    fn reserve_percent_lowers_effective_limit() {
        let tmp = TempDir::new().unwrap();
        let config = CostConfig {
            enabled: true,
            daily_limit_usd: 1.0,
            monthly_limit_usd: 1000.0,
            warn_at_percent: 100,
            enforcement: CostEnforcementConfig {
                reserve_percent: 50,
                ..Default::default()
            },
            ..Default::default()
        };
        let tracker = CostTracker::new(config, tmp.path()).unwrap();
        // 0.6 USD recorded; effective daily limit is 1.0 * (1 - 0.5) = 0.5.
        tracker
            .record_usage(TokenUsage::new("test/model", 600_000, 0, 1.0, 0.0))
            .unwrap();
        let check = tracker.check_budget(0.0).unwrap();
        assert!(
            matches!(check, BudgetCheck::Exceeded { .. }),
            "spend past the reserved buffer should be treated as exceeded"
        );
    }

    #[test]
    fn budget_status_reflects_reserve_percent() {
        // #453 review: the reported status must agree with the enforcement gate.
        // With a 50% reserve on a $1.00/day limit, the gate exceeds at $0.50, so
        // get_summary().budget must also report "exceeded" against the effective
        // $0.50 limit — not "ok" with $0.40 remaining against the raw $1.00.
        let tmp = TempDir::new().unwrap();
        let config = CostConfig {
            enabled: true,
            daily_limit_usd: 1.0,
            monthly_limit_usd: 1000.0,
            warn_at_percent: 100,
            enforcement: CostEnforcementConfig {
                reserve_percent: 50,
                ..Default::default()
            },
            ..Default::default()
        };
        let tracker = CostTracker::new(config, tmp.path()).unwrap();

        // ~$0.60 spent — over the $0.50 effective floor, under the $1.00 raw limit.
        tracker
            .record_usage(TokenUsage::new("test/model", 600_000, 0, 1.0, 0.0))
            .unwrap();

        let budget = tracker.get_summary().unwrap().budget;
        assert_eq!(budget.state, "exceeded", "status must match the gate");
        assert!(
            (budget.daily_limit_usd - 0.5).abs() < 1e-9,
            "status must report the effective limit, got {}",
            budget.daily_limit_usd
        );
        assert_eq!(budget.daily_remaining_usd, 0.0);
    }
}
