pub mod backend;
pub mod chunker;
pub mod cli;
pub mod decay;
pub mod embeddings;
pub mod none;
pub mod response_cache;
pub mod traits;
pub mod vector;

#[cfg(test)]
pub mod test_memory;

#[allow(unused_imports)]
pub use backend::{
    MemoryBackendKind, MemoryBackendProfile, classify_memory_backend, default_memory_backend_key,
    memory_backend_profile, selectable_memory_backends,
};
pub use none::NoneMemory;
pub use response_cache::ResponseCache;
pub use traits::Memory;
#[allow(unused_imports)]
pub use traits::{ExportFilter, MemoryCategory, MemoryEntry, ProceduralMessage};

use crate::config::{EmbeddingRouteConfig, MemoryConfig, StorageProviderConfig};
use std::path::Path;

pub fn effective_memory_backend_name(
    memory_backend: &str,
    storage_provider: Option<&StorageProviderConfig>,
) -> String {
    if let Some(override_provider) = storage_provider
        .map(|cfg| cfg.provider.trim())
        .filter(|provider| !provider.is_empty())
    {
        return override_provider.to_ascii_lowercase();
    }

    memory_backend.trim().to_ascii_lowercase()
}

/// Legacy auto-save key used for model-authored assistant summaries.
/// These entries are treated as untrusted context and should not be re-injected.
pub fn is_assistant_autosave_key(key: &str) -> bool {
    let normalized = key.trim().to_ascii_lowercase();
    normalized == "assistant_resp" || normalized.starts_with("assistant_resp_")
}

/// Filter known synthetic autosave noise patterns that should not be
/// persisted as user conversation memories.
pub fn should_skip_autosave_content(content: &str) -> bool {
    let normalized = content.trim();
    if normalized.is_empty() {
        return true;
    }

    let lowered = normalized.to_ascii_lowercase();
    lowered.starts_with("[cron:")
        || lowered.starts_with("[heartbeat task")
        || lowered.starts_with("[distilled_")
        || lowered.contains("distilled_index_sig:")
}

/// Factory: create the right memory backend from config.
///
/// Persistent memory in Revka is handled exclusively by Kumiho MCP (injected
/// at the agent level). The runtime `Memory` trait binding is therefore always
/// `NoneMemory` — in-session, non-persistent. Any non-Kumiho backend name is
/// rejected with an error directing users to Kumiho.
pub fn create_memory(
    config: &MemoryConfig,
    workspace_dir: &Path,
    api_key: Option<&str>,
) -> anyhow::Result<Box<dyn Memory>> {
    create_memory_with_storage_and_routes(config, &[], None, workspace_dir, api_key)
}

/// Factory: create memory with optional storage-provider override.
pub fn create_memory_with_storage(
    config: &MemoryConfig,
    storage_provider: Option<&StorageProviderConfig>,
    workspace_dir: &Path,
    api_key: Option<&str>,
) -> anyhow::Result<Box<dyn Memory>> {
    create_memory_with_storage_and_routes(config, &[], storage_provider, workspace_dir, api_key)
}

/// Factory: create memory with optional storage-provider override and embedding routes.
pub fn create_memory_with_storage_and_routes(
    config: &MemoryConfig,
    _embedding_routes: &[EmbeddingRouteConfig],
    storage_provider: Option<&StorageProviderConfig>,
    _workspace_dir: &Path,
    _api_key: Option<&str>,
) -> anyhow::Result<Box<dyn Memory>> {
    let backend_name = effective_memory_backend_name(&config.backend, storage_provider);

    match classify_memory_backend(&backend_name) {
        MemoryBackendKind::Kumiho | MemoryBackendKind::None => Ok(Box::new(NoneMemory::new())),
        MemoryBackendKind::Unknown => {
            anyhow::bail!(
                "Memory backend '{backend_name}' is not supported in Revka. \
                 Use 'kumiho' (default) for persistent memory via Kumiho MCP, or 'none' \
                 to disable persistence."
            )
        }
    }
}

/// Factory: create an optional response cache from config.
pub fn create_response_cache(config: &MemoryConfig, workspace_dir: &Path) -> Option<ResponseCache> {
    if !config.response_cache_enabled {
        return None;
    }

    match ResponseCache::new(
        workspace_dir,
        config.response_cache_ttl_minutes,
        config.response_cache_max_entries,
    ) {
        Ok(cache) => {
            tracing::info!(
                "💾 Response cache enabled (TTL: {}min, max: {} entries)",
                config.response_cache_ttl_minutes,
                config.response_cache_max_entries
            );
            Some(cache)
        }
        Err(e) => {
            tracing::warn!("Response cache disabled due to error: {e}");
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::StorageProviderConfig;
    use tempfile::TempDir;

    #[test]
    fn assistant_autosave_key_detection_matches_legacy_patterns() {
        assert!(is_assistant_autosave_key("assistant_resp"));
        assert!(is_assistant_autosave_key("assistant_resp_1234"));
        assert!(is_assistant_autosave_key("ASSISTANT_RESP_abcd"));
        assert!(!is_assistant_autosave_key("assistant_response"));
        assert!(!is_assistant_autosave_key("user_msg_1234"));
    }

    #[test]
    fn autosave_content_filter_drops_cron_and_distilled_noise() {
        assert!(should_skip_autosave_content("[cron:auto] patrol check"));
        assert!(should_skip_autosave_content(
            "[DISTILLED_MEMORY_CHUNK 1/2] DISTILLED_INDEX_SIG:abc123"
        ));
        assert!(should_skip_autosave_content(
            "[Heartbeat Task | decision] Should I run tasks?"
        ));
        assert!(should_skip_autosave_content(
            "[Heartbeat Task | high] Execute scheduled patrol"
        ));
        assert!(!should_skip_autosave_content(
            "User prefers concise answers."
        ));
    }

    #[test]
    fn factory_kumiho_uses_noop_memory() {
        let tmp = TempDir::new().unwrap();
        let cfg = MemoryConfig {
            backend: "kumiho".into(),
            ..MemoryConfig::default()
        };
        let mem = create_memory(&cfg, tmp.path(), None).unwrap();
        assert_eq!(mem.name(), "none");
    }

    #[test]
    fn factory_none_uses_noop_memory() {
        let tmp = TempDir::new().unwrap();
        let cfg = MemoryConfig {
            backend: "none".into(),
            ..MemoryConfig::default()
        };
        let mem = create_memory(&cfg, tmp.path(), None).unwrap();
        assert_eq!(mem.name(), "none");
    }

    #[test]
    fn factory_removed_backends_are_rejected() {
        let tmp = TempDir::new().unwrap();
        for name in ["sqlite", "lucid", "markdown", "qdrant", "redis"] {
            let cfg = MemoryConfig {
                backend: name.into(),
                ..MemoryConfig::default()
            };
            assert!(
                create_memory(&cfg, tmp.path(), None).is_err(),
                "backend '{name}' must be rejected in Revka"
            );
        }
    }

    #[test]
    fn effective_backend_name_prefers_storage_override() {
        let storage = StorageProviderConfig {
            provider: "custom".into(),
            ..StorageProviderConfig::default()
        };

        assert_eq!(
            effective_memory_backend_name("kumiho", Some(&storage)),
            "custom"
        );
    }
}
