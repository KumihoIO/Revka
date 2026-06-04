#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub enum MemoryBackendKind {
    Kumiho,
    None,
    Unknown,
}

#[allow(clippy::struct_excessive_bools)]
#[derive(Debug, Clone, Copy, Eq, PartialEq)]
pub struct MemoryBackendProfile {
    pub key: &'static str,
    pub label: &'static str,
    pub auto_save_default: bool,
    pub uses_sqlite_hygiene: bool,
    pub sqlite_based: bool,
    pub optional_dependency: bool,
}

const KUMIHO_PROFILE: MemoryBackendProfile = MemoryBackendProfile {
    key: "kumiho",
    label: "Kumiho — graph-native cognitive memory (recommended)",
    auto_save_default: true,
    uses_sqlite_hygiene: false,
    sqlite_based: false,
    optional_dependency: false,
};

const NONE_PROFILE: MemoryBackendProfile = MemoryBackendProfile {
    key: "none",
    label: "None — disable persistent memory",
    auto_save_default: false,
    uses_sqlite_hygiene: false,
    sqlite_based: false,
    optional_dependency: false,
};

const CUSTOM_PROFILE: MemoryBackendProfile = MemoryBackendProfile {
    key: "custom",
    label: "Custom backend — extension point",
    auto_save_default: true,
    uses_sqlite_hygiene: false,
    sqlite_based: false,
    optional_dependency: false,
};

/// Revka: Kumiho (via MCP) is the recommended backend, None for stateless.
const SELECTABLE_MEMORY_BACKENDS: [MemoryBackendProfile; 2] = [KUMIHO_PROFILE, NONE_PROFILE];

pub fn selectable_memory_backends() -> &'static [MemoryBackendProfile] {
    &SELECTABLE_MEMORY_BACKENDS
}

/// Revka: Kumiho is the default persistent memory backend.
pub fn default_memory_backend_key() -> &'static str {
    KUMIHO_PROFILE.key
}

pub fn classify_memory_backend(backend: &str) -> MemoryBackendKind {
    match backend {
        "kumiho" => MemoryBackendKind::Kumiho,
        "none" => MemoryBackendKind::None,
        _ => MemoryBackendKind::Unknown,
    }
}

pub fn memory_backend_profile(backend: &str) -> MemoryBackendProfile {
    match classify_memory_backend(backend) {
        MemoryBackendKind::Kumiho => KUMIHO_PROFILE,
        MemoryBackendKind::None => NONE_PROFILE,
        MemoryBackendKind::Unknown => CUSTOM_PROFILE,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn classify_known_backends() {
        assert_eq!(classify_memory_backend("kumiho"), MemoryBackendKind::Kumiho);
        assert_eq!(classify_memory_backend("none"), MemoryBackendKind::None);
    }

    #[test]
    fn classify_removed_backends_are_unknown() {
        for name in ["sqlite", "lucid", "markdown", "qdrant"] {
            assert_eq!(
                classify_memory_backend(name),
                MemoryBackendKind::Unknown,
                "'{name}' should be treated as Unknown after removal"
            );
        }
    }

    #[test]
    fn classify_unknown_backend() {
        assert_eq!(classify_memory_backend("redis"), MemoryBackendKind::Unknown);
    }

    #[test]
    fn default_backend_is_kumiho() {
        assert_eq!(default_memory_backend_key(), "kumiho");
    }

    #[test]
    fn selectable_backends_are_kumiho_and_none() {
        let backends = selectable_memory_backends();
        assert_eq!(backends.len(), 2);
        assert_eq!(backends[0].key, "kumiho");
        assert_eq!(backends[1].key, "none");
    }

    #[test]
    fn unknown_profile_preserves_extensibility_defaults() {
        let profile = memory_backend_profile("custom-memory");
        assert_eq!(profile.key, "custom");
        assert!(profile.auto_save_default);
        assert!(!profile.uses_sqlite_hygiene);
    }
}
