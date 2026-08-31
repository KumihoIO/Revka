//! Opt-in deterministic Kumiho Rust SDK recall.
//!
//! The native provider intentionally implements only the summarized,
//! non-graph read path.  Any unsupported request or native failure is returned
//! as an error so [`super::provider::HybridMemoryProvider`] can delegate to the
//! Python `kumiho-memory` reference implementation.

use std::collections::{BTreeMap, HashSet};
use std::sync::Arc;
use std::time::Instant;

use anyhow::{Context, Result, anyhow, bail};
use async_trait::async_trait;
use serde_json::{Value, json};
use tokio::sync::OnceCell;

use crate::config::McpServerConfig;

use super::provider::{
    ConsolidateOutcome, ConsolidateRequest, EngageRequest, MemoryContext, MemoryProvider,
    MemoryRecord, RecallMode, RecallOutcome, RecallRequest, ReflectOutcome, ReflectRequest,
};

const NATIVE_MEMORY_ENV: &str = "REVKA_KUMIHO_NATIVE_MEMORY";
const KUMIHO_MCP_SERVER: &str = "kumiho-memory";
const DEFAULT_MEMORY_PROJECT: &str = "CognitiveMemory";
const MEMORY_ITEM_KIND: &str = "conversation";
const DEFAULT_LIMIT: usize = 5;
const MAX_NATIVE_LIMIT: usize = 100;

const EVIDENCE_LEVELS: [&str; 4] = ["official", "corroborated", "single_source", "unverified"];

#[derive(Debug, Clone, PartialEq)]
struct NativeSearchCall {
    query: String,
    context: String,
    min_score: f32,
    page_size: i32,
    include_revision_metadata: bool,
}

#[derive(Debug, Clone)]
struct ResolvedSearchHit {
    item_kref: String,
    revision_kref: String,
    space: String,
    score: f64,
    tags: Vec<String>,
    metadata: BTreeMap<String, String>,
    created_at: Option<String>,
}

#[async_trait]
trait NativeRecallSource: Send + Sync {
    async fn search_resolved(&self, call: NativeSearchCall) -> Result<Vec<ResolvedSearchHit>>;
}

type NativeClientState = std::result::Result<kumiho::Client, String>;

struct SdkRecallSource {
    auth_token: Option<String>,
    tenant_hint: Option<String>,
    local_endpoint: Option<String>,
    client: OnceCell<NativeClientState>,
}

impl SdkRecallSource {
    fn new(
        auth_token: Option<String>,
        tenant_hint: Option<String>,
        local_endpoint: Option<String>,
    ) -> Self {
        Self {
            auth_token,
            tenant_hint,
            local_endpoint,
            client: OnceCell::new(),
        }
    }

    async fn client(&self) -> Result<&kumiho::Client> {
        let state = self
            .client
            .get_or_init(|| async {
                let mut builder = kumiho::Client::builder();
                if let Some(endpoint) = &self.local_endpoint {
                    if !is_loopback_endpoint(endpoint) {
                        return Err(
                            "KUMIHO_LOCAL_SERVER_ENDPOINT must use a loopback host".to_owned()
                        );
                    }
                    builder = builder.endpoint(endpoint.clone()).use_discovery(false);
                }
                if let Some(token) = &self.auth_token {
                    builder = builder.token(token.clone());
                }
                if let Some(tenant_hint) = &self.tenant_hint {
                    builder = builder.tenant_hint(tenant_hint.clone());
                }
                builder.build().await.map_err(|error| error.to_string())
            })
            .await;
        state
            .as_ref()
            .map_err(|error| anyhow!(error.clone()))
            .context("failed to initialize Kumiho Rust SDK client")
    }
}

#[async_trait]
impl NativeRecallSource for SdkRecallSource {
    async fn search_resolved(&self, call: NativeSearchCall) -> Result<Vec<ResolvedSearchHit>> {
        let page = self
            .client()
            .await?
            .search(
                &call.query,
                &call.context,
                MEMORY_ITEM_KIND,
                false,
                call.include_revision_metadata,
                false,
                call.min_score,
                Some(call.page_size),
                None,
            )
            .await
            .with_context(|| format!("native Kumiho search failed in `{}`", call.context))?;

        let matched_count = page.items.len();
        let mut first_resolution_error = None;
        let mut resolved = Vec::with_capacity(matched_count);

        for search_result in page.items {
            let item = search_result.item;
            let revision = match item.get_revision_by_tag("published").await {
                Ok(Some(revision)) => Some(revision),
                Ok(None) => match item.get_revision_by_tag("latest").await {
                    Ok(revision) => revision,
                    Err(error) => {
                        first_resolution_error.get_or_insert_with(|| error.to_string());
                        None
                    }
                },
                Err(error) => {
                    first_resolution_error.get_or_insert_with(|| error.to_string());
                    None
                }
            };

            let Some(revision) = revision else {
                continue;
            };
            resolved.push(ResolvedSearchHit {
                item_kref: item.kref.uri().to_owned(),
                revision_kref: revision.kref.uri().to_owned(),
                space: item.space(),
                score: f64::from(search_result.score),
                tags: revision.tags,
                metadata: revision.metadata.into_iter().collect(),
                created_at: revision.created_at,
            });
        }

        if matched_count > 0 && resolved.is_empty() {
            if let Some(error) = first_resolution_error {
                bail!("native Kumiho revision resolution failed: {error}");
            }
        }

        Ok(resolved)
    }
}

/// Deterministic summarized-memory reader backed by the Kumiho Rust SDK.
#[derive(Clone)]
pub struct NativeKumihoProvider {
    source: Arc<dyn NativeRecallSource>,
    project: String,
}

impl NativeKumihoProvider {
    pub(crate) fn from_mcp_configs(configs: &[McpServerConfig]) -> Option<Self> {
        if !env_flag(NATIVE_MEMORY_ENV) {
            return None;
        }

        let mcp_config = configs
            .iter()
            .find(|config| config.name == KUMIHO_MCP_SERVER);
        let auth_token = configured_auth_token(mcp_config);
        let tenant_hint = config_or_process_env(mcp_config, "KUMIHO_TENANT_HINT");
        let local_endpoint = config_or_process_env(mcp_config, "KUMIHO_LOCAL_SERVER_ENDPOINT");
        let project = config_or_process_env(mcp_config, "KUMIHO_MEMORY_PROJECT")
            .unwrap_or_else(|| DEFAULT_MEMORY_PROJECT.to_owned());
        let source = Arc::new(SdkRecallSource::new(
            auth_token,
            tenant_hint,
            local_endpoint,
        ));
        Some(Self { source, project })
    }

    #[cfg(test)]
    fn with_source(source: Arc<dyn NativeRecallSource>, project: impl Into<String>) -> Self {
        Self {
            source,
            project: project.into(),
        }
    }

    async fn read(
        &self,
        query: &str,
        limit: Option<usize>,
        min_score: Option<f64>,
        space_paths: Option<&[String]>,
        memory_types: Option<&[String]>,
        graph_augmented: Option<bool>,
        recall_mode: Option<RecallMode>,
        extra: &BTreeMap<String, Value>,
    ) -> Result<RecallOutcome> {
        if graph_augmented.unwrap_or(false) {
            bail!("native recall does not yet support graph-augmented requests");
        }
        if recall_mode == Some(RecallMode::Full) {
            bail!("native recall does not load full memory artifacts");
        }
        if !extra.is_empty() {
            bail!("native recall does not recognize additive request fields");
        }
        if query.trim().is_empty() {
            bail!("native recall requires a non-empty query");
        }

        let limit = limit.unwrap_or(DEFAULT_LIMIT);
        if limit > MAX_NATIVE_LIMIT {
            bail!("native recall limit {limit} exceeds the canary ceiling {MAX_NATIVE_LIMIT}");
        }
        if limit == 0 {
            return Ok(RecallOutcome {
                recall_mode: Some("summarized".to_owned()),
                ..RecallOutcome::default()
            });
        }

        let min_score = min_score.unwrap_or(0.0);
        if !min_score.is_finite() || !(0.0..=1.0).contains(&min_score) {
            bail!("native recall min_score must be a finite value from 0 to 1");
        }

        let started = Instant::now();
        let contexts = normalized_contexts(&self.project, space_paths);
        let page_size = i32::try_from(limit.saturating_mul(2))
            .expect("native limit ceiling keeps page size within i32");
        let mut candidates = Vec::new();

        for context in &contexts {
            let deep_call = NativeSearchCall {
                query: query.to_owned(),
                context: context.clone(),
                // Python applies the caller threshold after its ranking
                // stack. Fetch the same candidate pool here, then filter
                // after native evidence weighting below.
                min_score: 0.0,
                page_size,
                include_revision_metadata: true,
            };
            let mut hits = self.source.search_resolved(deep_call.clone()).await?;
            if hits.is_empty() {
                let mut shallow_call = deep_call;
                shallow_call.include_revision_metadata = false;
                hits = self.source.search_resolved(shallow_call).await?;
            }
            candidates.extend(hits);
        }

        if candidates.is_empty() {
            bail!("native search produced no resolved memories");
        }

        candidates.sort_by(|left, right| {
            right
                .score
                .total_cmp(&left.score)
                .then_with(|| left.item_kref.cmp(&right.item_kref))
                .then_with(|| left.revision_kref.cmp(&right.revision_kref))
        });

        let allowed_types = normalized_memory_types(memory_types);
        let mut seen_items = HashSet::new();
        let mut results = Vec::with_capacity(candidates.len());
        for candidate in candidates {
            if !matches_memory_type(&candidate.metadata, allowed_types.as_ref()) {
                continue;
            }
            if !seen_items.insert(candidate.item_kref.clone()) {
                continue;
            }
            results.push(memory_record(candidate));
        }

        if results.is_empty() {
            bail!("native search results did not pass the requested memory filters");
        }

        apply_evidence_weights(&mut results);
        results.retain(|memory| memory.score.is_some_and(|score| score >= min_score));
        results.truncate(limit);
        let count = results.len();
        tracing::debug!(
            provider = "kumiho-rust",
            count,
            elapsed_ms = started.elapsed().as_millis(),
            "native Kumiho summarized recall completed"
        );

        Ok(RecallOutcome {
            results,
            count,
            recall_mode: Some("summarized".to_owned()),
            ..RecallOutcome::default()
        })
    }
}

#[async_trait]
impl MemoryProvider for NativeKumihoProvider {
    async fn engage(&self, request: EngageRequest) -> Result<MemoryContext> {
        let outcome = self
            .read(
                &request.query,
                request.limit,
                request.min_score,
                request.space_paths.as_deref(),
                request.memory_types.as_deref(),
                request.graph_augmented,
                request.recall_mode,
                &request.extra,
            )
            .await?;
        let context = compose_summarized_context(&outcome.results);
        let source_krefs = outcome
            .results
            .iter()
            .filter_map(|memory| memory.kref.clone())
            .collect();
        let approx_tokens = context.chars().count() / 4;
        Ok(MemoryContext {
            context,
            results: outcome.results,
            source_krefs,
            count: outcome.count,
            recall_mode: outcome.recall_mode,
            approx_tokens: Some(approx_tokens),
            ..MemoryContext::default()
        })
    }

    async fn reflect(&self, _request: ReflectRequest) -> Result<ReflectOutcome> {
        bail!("native Kumiho cognitive writes are intentionally unsupported")
    }

    async fn recall(&self, request: RecallRequest) -> Result<RecallOutcome> {
        self.read(
            &request.query,
            request.limit,
            request.min_score,
            request.space_paths.as_deref(),
            request.memory_types.as_deref(),
            request.graph_augmented,
            request.recall_mode,
            &request.extra,
        )
        .await
    }

    async fn consolidate(&self, _request: ConsolidateRequest) -> Result<ConsolidateOutcome> {
        bail!("native Kumiho cognitive writes are intentionally unsupported")
    }
}

fn configured_auth_token(config: Option<&McpServerConfig>) -> Option<String> {
    if let Some(config) = config {
        let token_was_configured = config.env.contains_key("KUMIHO_SERVICE_TOKEN")
            || config.env.contains_key("KUMIHO_AUTH_TOKEN");
        if token_was_configured {
            // Match Revka onboarding precedence. Explicit empty values are
            // not forwarded by this provider (CE routing is pinned separately
            // by `local_endpoint`).
            return config
                .env
                .get("KUMIHO_SERVICE_TOKEN")
                .and_then(|value| non_empty(value))
                .or_else(|| {
                    config
                        .env
                        .get("KUMIHO_AUTH_TOKEN")
                        .and_then(|value| non_empty(value))
                });
        }
    }
    non_empty_env("KUMIHO_SERVICE_TOKEN").or_else(|| non_empty_env("KUMIHO_AUTH_TOKEN"))
}

fn config_or_process_env(config: Option<&McpServerConfig>, name: &str) -> Option<String> {
    config
        .and_then(|config| config.env.get(name))
        .and_then(|value| non_empty(value))
        .or_else(|| non_empty_env(name))
}

fn non_empty(value: &str) -> Option<String> {
    let value = value.trim();
    (!value.is_empty()).then(|| value.to_owned())
}

fn non_empty_env(name: &str) -> Option<String> {
    std::env::var(name).ok().and_then(|value| non_empty(&value))
}

fn env_flag(name: &str) -> bool {
    std::env::var(name).is_ok_and(|value| flag_is_enabled(&value))
}

fn flag_is_enabled(value: &str) -> bool {
    matches!(
        value.trim().to_ascii_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn is_loopback_endpoint(endpoint: &str) -> bool {
    let endpoint = endpoint.trim();
    let authority = endpoint
        .split_once("://")
        .map_or(endpoint, |(_, remainder)| remainder)
        .split('/')
        .next()
        .unwrap_or_default();
    if authority.contains('@') {
        return false;
    }
    let host = if let Some(bracketed) = authority.strip_prefix('[') {
        bracketed
            .split_once(']')
            .map(|(host, _)| host)
            .unwrap_or_default()
    } else if authority.matches(':').count() == 1 {
        authority
            .split_once(':')
            .map(|(host, _)| host)
            .unwrap_or_default()
    } else {
        authority
    };

    host.eq_ignore_ascii_case("localhost")
        || host
            .parse::<std::net::IpAddr>()
            .is_ok_and(|address| address.is_loopback())
}

fn normalized_contexts(project: &str, space_paths: Option<&[String]>) -> Vec<String> {
    let mut contexts = Vec::new();
    let mut seen = HashSet::new();
    for path in space_paths.unwrap_or(&[]) {
        let path = path.trim().trim_matches('/');
        let context = if path.is_empty() {
            project.to_owned()
        } else if path == project || path.starts_with(&format!("{project}/")) {
            path.to_owned()
        } else {
            format!("{project}/{path}")
        };
        if seen.insert(context.clone()) {
            contexts.push(context);
        }
    }
    if contexts.is_empty() {
        contexts.push(project.to_owned());
    }
    contexts
}

fn normalized_memory_types(memory_types: Option<&[String]>) -> Option<HashSet<String>> {
    let normalized: HashSet<String> = memory_types
        .unwrap_or(&[])
        .iter()
        .map(|value| value.trim().to_ascii_lowercase())
        .filter(|value| !value.is_empty())
        .collect();
    (!normalized.is_empty()).then_some(normalized)
}

fn matches_memory_type(
    metadata: &BTreeMap<String, String>,
    allowed: Option<&HashSet<String>>,
) -> bool {
    let Some(allowed) = allowed else {
        return true;
    };
    metadata
        .get("memory_type")
        .or_else(|| metadata.get("type"))
        .is_some_and(|memory_type| allowed.contains(&memory_type.trim().to_ascii_lowercase()))
}

fn memory_record(hit: ResolvedSearchHit) -> MemoryRecord {
    let memory_type = hit
        .metadata
        .get("memory_type")
        .or_else(|| hit.metadata.get("type"))
        .cloned()
        .filter(|value| !value.is_empty());
    let mut extra = BTreeMap::new();
    let space = hit
        .metadata
        .get("space")
        .cloned()
        .filter(|value| !value.is_empty())
        .unwrap_or(hit.space);
    extra.insert("space".to_owned(), Value::String(space));
    extra.insert("tags".to_owned(), json!(hit.tags));

    for key in [
        "evidence_level",
        "source",
        "facts",
        "event_date_confidence",
        "valid_from",
        "valid_to",
        "grounding_stale",
        "grounding_stale_reason",
    ] {
        if let Some(value) = hit.metadata.get(key) {
            extra.insert(key.to_owned(), metadata_value(value));
        }
    }

    MemoryRecord {
        kref: Some(hit.revision_kref),
        item_kref: Some(hit.item_kref),
        title: hit
            .metadata
            .get("title")
            .cloned()
            .filter(|value| !value.is_empty()),
        summary: hit
            .metadata
            .get("summary")
            .cloned()
            .filter(|value| !value.is_empty()),
        score: Some(hit.score),
        created_at: hit.created_at,
        event_date: hit
            .metadata
            .get("event_date")
            .cloned()
            .filter(|value| !value.is_empty()),
        memory_type,
        extra,
        ..MemoryRecord::default()
    }
}

fn metadata_value(value: &str) -> Value {
    serde_json::from_str(value).unwrap_or_else(|_| Value::String(value.to_owned()))
}

fn evidence_level(memory: &MemoryRecord) -> Option<&str> {
    if let Some(level) = memory.extra.get("evidence_level").and_then(Value::as_str) {
        if EVIDENCE_LEVELS.contains(&level) {
            return Some(level);
        }
    }
    memory
        .extra
        .get("tags")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .filter_map(|tag| tag.strip_prefix("evidence:"))
        .find(|level| EVIDENCE_LEVELS.contains(level))
}

fn evidence_weight(level: Option<&str>) -> f64 {
    match level {
        Some("official") => 0.15,
        Some("corroborated") => 0.08,
        Some("unverified") => -0.10,
        _ => 0.0,
    }
}

fn apply_evidence_weights(memories: &mut [MemoryRecord]) {
    if !memories
        .iter()
        .any(|memory| evidence_level(memory).is_some())
    {
        return;
    }
    for memory in memories.iter_mut() {
        if let Some(base_score) = memory.score {
            let level = evidence_level(memory).map(str::to_owned);
            memory
                .extra
                .insert("base_score".to_owned(), json!(base_score));
            memory.score = Some(base_score + evidence_weight(level.as_deref()));
        }
    }
    memories.sort_by(|left, right| {
        right
            .score
            .unwrap_or(f64::NEG_INFINITY)
            .total_cmp(&left.score.unwrap_or(f64::NEG_INFINITY))
            .then_with(|| left.item_kref.cmp(&right.item_kref))
    });
}

fn evidence_badge(memory: &MemoryRecord) -> String {
    match evidence_level(memory) {
        Some(level) if level != "single_source" => format!("[{level}] "),
        _ => String::new(),
    }
}

fn compose_summarized_context(memories: &[MemoryRecord]) -> String {
    memories
        .iter()
        .filter_map(|memory| {
            let summary = memory.summary.as_deref().unwrap_or("");
            if summary.is_empty() {
                return None;
            }
            let badge = evidence_badge(memory);
            let date = memory
                .event_date
                .as_deref()
                .filter(|date| !date.is_empty())
                .map(|date| format!("[{date}] "))
                .unwrap_or_default();
            let main = memory
                .title
                .as_deref()
                .filter(|title| !title.is_empty())
                .map_or_else(
                    || format!("{badge}{date}{summary}"),
                    |title| format!("{badge}{date}{title}: {summary}"),
                );
            let facts = memory
                .extra
                .get("facts")
                .and_then(format_facts)
                .filter(|facts| !facts.is_empty())
                .map(|facts| format!("\nFacts: {facts}"))
                .unwrap_or_default();
            Some(format!("{main}{facts}"))
        })
        .collect::<Vec<_>>()
        .join("\n\n")
}

fn format_facts(value: &Value) -> Option<String> {
    match value {
        Value::String(value) => Some(value.clone()),
        Value::Array(values) => Some(
            values
                .iter()
                .map(|value| {
                    value
                        .get("claim")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                        .unwrap_or_else(|| value.to_string())
                })
                .collect::<Vec<_>>()
                .join("; "),
        ),
        Value::Null => None,
        value => Some(value.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use std::sync::Mutex;

    use super::*;

    #[derive(Default)]
    struct FakeRecallSource {
        responses: BTreeMap<(String, bool), Vec<ResolvedSearchHit>>,
        calls: Mutex<Vec<NativeSearchCall>>,
    }

    impl FakeRecallSource {
        fn with_response(
            mut self,
            context: &str,
            deep: bool,
            hits: Vec<ResolvedSearchHit>,
        ) -> Self {
            self.responses.insert((context.to_owned(), deep), hits);
            self
        }

        fn calls(&self) -> Vec<NativeSearchCall> {
            self.calls.lock().expect("calls lock poisoned").clone()
        }
    }

    #[async_trait]
    impl NativeRecallSource for FakeRecallSource {
        async fn search_resolved(&self, call: NativeSearchCall) -> Result<Vec<ResolvedSearchHit>> {
            self.calls
                .lock()
                .expect("calls lock poisoned")
                .push(call.clone());
            Ok(self
                .responses
                .get(&(call.context, call.include_revision_metadata))
                .cloned()
                .unwrap_or_default())
        }
    }

    fn hit(
        item: &str,
        revision: &str,
        score: f64,
        memory_type: &str,
        title: &str,
        summary: &str,
        evidence: &str,
    ) -> ResolvedSearchHit {
        let mut metadata = BTreeMap::from([
            ("memory_type".to_owned(), memory_type.to_owned()),
            ("title".to_owned(), title.to_owned()),
            ("summary".to_owned(), summary.to_owned()),
            ("event_date".to_owned(), "2026-08-30".to_owned()),
        ]);
        if !evidence.is_empty() {
            metadata.insert("evidence_level".to_owned(), evidence.to_owned());
        }
        ResolvedSearchHit {
            item_kref: item.to_owned(),
            revision_kref: revision.to_owned(),
            space: "people".to_owned(),
            score,
            tags: vec!["published".to_owned()],
            metadata,
            created_at: Some("2026-08-31T00:00:00Z".to_owned()),
        }
    }

    #[tokio::test]
    async fn summarized_recall_retries_shallow_per_explicit_space_and_never_widens() {
        let alpha = hit(
            "kref://CognitiveMemory/people/alice/alpha.conversation",
            "kref://CognitiveMemory/people/alice/alpha.conversation?r=2",
            0.60,
            "decision",
            "Alpha",
            "Use the Rust SDK first.",
            "official",
        );
        let alpha_duplicate = ResolvedSearchHit {
            score: 0.58,
            ..alpha.clone()
        };
        let beta = hit(
            "kref://CognitiveMemory/work/beta.conversation",
            "kref://CognitiveMemory/work/beta.conversation?r=1",
            0.65,
            "decision",
            "Beta",
            "Keep Python writes.",
            "unverified",
        );
        let ignored = hit(
            "kref://CognitiveMemory/work/fact.conversation",
            "kref://CognitiveMemory/work/fact.conversation?r=1",
            0.99,
            "fact",
            "Wrong type",
            "Must be filtered.",
            "official",
        );
        let source = Arc::new(
            FakeRecallSource::default()
                .with_response("CognitiveMemory/people/alice", false, vec![alpha])
                .with_response(
                    "CognitiveMemory/work",
                    false,
                    vec![ignored, beta, alpha_duplicate],
                ),
        );
        let provider = NativeKumihoProvider::with_source(source.clone(), "CognitiveMemory");
        let mut request = EngageRequest::new("memory architecture");
        request.limit = Some(3);
        request.min_score = Some(0.2);
        request.space_paths = Some(vec![
            "people/alice".to_owned(),
            "CognitiveMemory/work".to_owned(),
        ]);
        request.memory_types = Some(vec!["decision".to_owned()]);
        request.recall_mode = Some(RecallMode::Summarized);

        let context = provider.engage(request).await.unwrap();

        assert_eq!(context.count, 2);
        assert_eq!(
            context.results[0].item_kref.as_deref(),
            Some("kref://CognitiveMemory/people/alice/alpha.conversation")
        );
        assert_eq!(context.results[0].score, Some(0.75));
        assert_eq!(context.results[1].score, Some(0.55));
        assert_eq!(
            context.context,
            "[official] [2026-08-30] Alpha: Use the Rust SDK first.\n\n[unverified] [2026-08-30] Beta: Keep Python writes."
        );
        assert_eq!(
            context.approx_tokens,
            Some(context.context.chars().count() / 4)
        );

        let calls = source.calls();
        assert_eq!(calls.len(), 4);
        assert_eq!(calls[0].context, "CognitiveMemory/people/alice");
        assert!(calls[0].include_revision_metadata);
        assert_eq!(calls[1].context, "CognitiveMemory/people/alice");
        assert!(!calls[1].include_revision_metadata);
        assert_eq!(calls[2].context, "CognitiveMemory/work");
        assert!(calls[2].include_revision_metadata);
        assert_eq!(calls[3].context, "CognitiveMemory/work");
        assert!(!calls[3].include_revision_metadata);
        assert!(calls.iter().all(|call| call.context != "CognitiveMemory"));
    }

    #[tokio::test]
    async fn deep_hits_skip_shallow_retry() {
        let source = Arc::new(FakeRecallSource::default().with_response(
            "CognitiveMemory",
            true,
            vec![hit(
                "kref://CognitiveMemory/a.conversation",
                "kref://CognitiveMemory/a.conversation?r=1",
                0.8,
                "summary",
                "A",
                "Summary",
                "",
            )],
        ));
        let provider = NativeKumihoProvider::with_source(source.clone(), "CognitiveMemory");

        let outcome = provider.recall(RecallRequest::new("query")).await.unwrap();

        assert_eq!(outcome.count, 1);
        assert_eq!(source.calls().len(), 1);
        assert!(source.calls()[0].include_revision_metadata);
    }

    #[tokio::test]
    async fn unsupported_read_shapes_fail_before_search_for_python_fallback() {
        let source = Arc::new(FakeRecallSource::default());
        let provider = NativeKumihoProvider::with_source(source.clone(), "CognitiveMemory");

        let mut full = RecallRequest::new("query");
        full.recall_mode = Some(RecallMode::Full);
        assert!(provider.recall(full).await.is_err());

        let mut graph = RecallRequest::new("query");
        graph.graph_augmented = Some(true);
        assert!(provider.recall(graph).await.is_err());

        let mut future = RecallRequest::new("query");
        future.extra.insert("future_filter".to_owned(), json!(true));
        assert!(provider.recall(future).await.is_err());

        let mut invalid_score = RecallRequest::new("query");
        invalid_score.min_score = Some(f64::NAN);
        assert!(provider.recall(invalid_score).await.is_err());

        assert!(source.calls().is_empty());
    }

    #[tokio::test]
    async fn healthy_empty_search_requests_python_fallback() {
        let source = Arc::new(FakeRecallSource::default());
        let provider = NativeKumihoProvider::with_source(source.clone(), "CognitiveMemory");

        let error = provider
            .recall(RecallRequest::new("missing"))
            .await
            .unwrap_err();

        assert!(error.to_string().contains("no resolved memories"));
        assert_eq!(source.calls().len(), 2);
    }

    #[tokio::test]
    async fn explicit_min_score_is_applied_after_evidence_weighting() {
        let source = Arc::new(FakeRecallSource::default().with_response(
            "CognitiveMemory",
            true,
            vec![hit(
                "kref://CognitiveMemory/a.conversation",
                "kref://CognitiveMemory/a.conversation?r=1",
                0.60,
                "decision",
                "A",
                "Official memory",
                "official",
            )],
        ));
        let provider = NativeKumihoProvider::with_source(source.clone(), "CognitiveMemory");
        let mut request = RecallRequest::new("query");
        request.min_score = Some(0.70);

        let outcome = provider.recall(request).await.unwrap();

        assert_eq!(outcome.count, 1);
        assert_eq!(outcome.results[0].score, Some(0.75));
        assert_eq!(source.calls()[0].min_score, 0.0);
    }

    #[tokio::test]
    async fn evidence_weighting_can_promote_a_candidate_across_the_result_limit() {
        let source = Arc::new(FakeRecallSource::default().with_response(
            "CognitiveMemory",
            true,
            vec![
                hit(
                    "kref://CognitiveMemory/unverified.conversation",
                    "kref://CognitiveMemory/unverified.conversation?r=1",
                    0.70,
                    "decision",
                    "Unverified",
                    "Higher base score",
                    "unverified",
                ),
                hit(
                    "kref://CognitiveMemory/official.conversation",
                    "kref://CognitiveMemory/official.conversation?r=1",
                    0.65,
                    "decision",
                    "Official",
                    "Higher weighted score",
                    "official",
                ),
            ],
        ));
        let provider = NativeKumihoProvider::with_source(source, "CognitiveMemory");
        let mut request = RecallRequest::new("query");
        request.limit = Some(1);

        let outcome = provider.recall(request).await.unwrap();

        assert_eq!(outcome.count, 1);
        assert_eq!(
            outcome.results[0].item_kref.as_deref(),
            Some("kref://CognitiveMemory/official.conversation")
        );
        assert_eq!(outcome.results[0].score, Some(0.80));
    }

    #[test]
    fn facts_context_matches_python_summarized_shape() {
        let mut memory = MemoryRecord {
            title: Some("Profile".to_owned()),
            summary: Some("Known details".to_owned()),
            ..MemoryRecord::default()
        };
        memory.extra.insert(
            "facts".to_owned(),
            json!([{"claim": "Uses Rust"}, {"claim": "Keeps Python writes"}]),
        );

        assert_eq!(
            compose_summarized_context(&[memory]),
            "Profile: Known details\nFacts: Uses Rust; Keeps Python writes"
        );
    }

    #[test]
    fn empty_optional_metadata_is_omitted_from_python_shaped_context() {
        let memory = memory_record(hit(
            "kref://CognitiveMemory/a.conversation",
            "kref://CognitiveMemory/a.conversation?r=1",
            0.8,
            "",
            "",
            "Summary only",
            "",
        ));

        assert_eq!(memory.title, None);
        assert_eq!(memory.event_date.as_deref(), Some("2026-08-30"));
        assert_eq!(memory.memory_type, None);
        assert_eq!(
            compose_summarized_context(&[memory]),
            "[2026-08-30] Summary only"
        );

        let without_date = MemoryRecord {
            summary: Some("No date".to_owned()),
            event_date: Some(String::new()),
            ..MemoryRecord::default()
        };
        assert_eq!(compose_summarized_context(&[without_date]), "No date");
    }

    #[test]
    fn native_memory_activation_requires_explicit_true_value() {
        for enabled in ["1", "true", "TRUE", " yes ", "on"] {
            assert!(flag_is_enabled(enabled));
        }
        for disabled in ["", "0", "false", "no", "off", "enabled"] {
            assert!(!flag_is_enabled(disabled));
        }
    }

    #[test]
    fn local_endpoint_guard_matches_sdk_loopback_contract() {
        for endpoint in [
            "localhost:8080",
            "http://LOCALHOST:8080",
            "127.0.0.1:8080",
            "http://127.0.0.2:8080",
            "http://[::1]:8080",
            "::1",
        ] {
            assert!(is_loopback_endpoint(endpoint), "rejected {endpoint}");
        }
        for endpoint in [
            "",
            "kumiho.example.com:443",
            "http://192.168.1.4:8080",
            "http://localhost:8080@kumiho.example.com",
        ] {
            assert!(!is_loopback_endpoint(endpoint), "accepted {endpoint}");
        }
    }

    #[test]
    fn configured_auth_uses_revka_service_token_precedence() {
        let config: McpServerConfig = serde_json::from_value(json!({
            "name": "kumiho-memory",
            "command": "python",
            "env": {
                "KUMIHO_SERVICE_TOKEN": "service-token",
                "KUMIHO_AUTH_TOKEN": "stale-auth-token"
            }
        }))
        .unwrap();
        assert_eq!(
            configured_auth_token(Some(&config)).as_deref(),
            Some("service-token")
        );

        let auth_fallback: McpServerConfig = serde_json::from_value(json!({
            "name": "kumiho-memory",
            "command": "python",
            "env": {
                "KUMIHO_SERVICE_TOKEN": "",
                "KUMIHO_AUTH_TOKEN": "auth-token"
            }
        }))
        .unwrap();
        assert_eq!(
            configured_auth_token(Some(&auth_fallback)).as_deref(),
            Some("auth-token")
        );

        let ce_config: McpServerConfig = serde_json::from_value(json!({
            "name": "kumiho-memory",
            "command": "python",
            "env": {
                "KUMIHO_SERVICE_TOKEN": "",
                "KUMIHO_AUTH_TOKEN": ""
            }
        }))
        .unwrap();
        assert_eq!(configured_auth_token(Some(&ce_config)), None);
    }
}
