//! Curated starter model catalogs shared by onboarding and runtime tools.
//!
//! Providers with a live model-list endpoint still prefer that endpoint during
//! onboarding. These entries are the offline fallback and the source of each
//! provider's default model, so the first entry in every catalog is intentional.

use super::{canonical_china_provider_name, is_qwen_oauth_alias};

/// Revka's default model when no provider-specific catalog exists.
pub(crate) const GLOBAL_DEFAULT_MODEL: &str = "anthropic/claude-sonnet-5";

/// A model ID and its user-facing onboarding label.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct ModelCatalogEntry {
    pub(crate) id: &'static str,
    pub(crate) label: &'static str,
}

const fn model(id: &'static str, label: &'static str) -> ModelCatalogEntry {
    ModelCatalogEntry { id, label }
}

const OPENROUTER_MODELS: &[ModelCatalogEntry] = &[
    model(
        "anthropic/claude-sonnet-5",
        "Claude Sonnet 5 (balanced, recommended)",
    ),
    model("openai/gpt-5.6-sol", "GPT-5.6 Sol (flagship reasoning)"),
    model(
        "google/gemini-3.7-flash",
        "Gemini 3.7 Flash (coding and agents)",
    ),
    model("x-ai/grok-4.6", "Grok 4.6 (reasoning and coding)"),
    model(
        "deepseek/deepseek-v4-pro-0813",
        "DeepSeek V4 Pro (reasoning and value)",
    ),
    model("z-ai/glm-5.3", "GLM-5.3 (long-context agents)"),
];

const ANTHROPIC_MODELS: &[ModelCatalogEntry] = &[
    model("claude-sonnet-5", "Claude Sonnet 5 (balanced, recommended)"),
    model("claude-opus-5", "Claude Opus 5 (complex agentic work)"),
    model("claude-fable-5", "Claude Fable 5 (long-running agents)"),
    model(
        "claude-haiku-4-5-20251001",
        "Claude Haiku 4.5 (fastest, cheapest)",
    ),
];

const OPENAI_MODELS: &[ModelCatalogEntry] = &[
    model("gpt-5.6-sol", "GPT-5.6 Sol (flagship, recommended)"),
    model("gpt-5.6-terra", "GPT-5.6 Terra (balanced cost)"),
    model("gpt-5.6-luna", "GPT-5.6 Luna (lowest cost)"),
];

const VENICE_MODELS: &[ModelCatalogEntry] = &[
    model("z-ai-glm-5-3", "GLM-5.3 via Venice (recommended)"),
    model("claude-sonnet-5", "Claude Sonnet 5 via Venice"),
    model("deepseek-v4-pro-0813", "DeepSeek V4 Pro via Venice"),
    model("grok-4-6", "Grok 4.6 via Venice"),
    model("kimi-k3", "Kimi K3 via Venice"),
];

const GROQ_MODELS: &[ModelCatalogEntry] = &[
    model("openai/gpt-oss-120b", "GPT-OSS 120B (recommended)"),
    model("openai/gpt-oss-20b", "GPT-OSS 20B (lower latency and cost)"),
];

const MISTRAL_MODELS: &[ModelCatalogEntry] = &[
    model("mistral-medium-3-5", "Mistral Medium 3.5 (recommended)"),
    model("mistral-small-2603", "Mistral Small 2603 (fast)"),
    model("mistral-large-2512", "Mistral Large 2512 (flagship)"),
];

const DEEPSEEK_MODELS: &[ModelCatalogEntry] = &[
    model("deepseek-v4-flash", "DeepSeek V4 Flash (recommended)"),
    model("deepseek-v4-pro", "DeepSeek V4 Pro (maximum quality)"),
    model(
        "deepseek-v4-flash-vision-exp",
        "DeepSeek V4 Flash Vision Experimental",
    ),
];

const XAI_MODELS: &[ModelCatalogEntry] = &[model(
    "grok-4.6",
    "Grok 4.6 (recommended for code and agents)",
)];

const PERPLEXITY_MODELS: &[ModelCatalogEntry] = &[
    model("sonar-pro", "Sonar Pro (flagship web-grounded model)"),
    model(
        "sonar-reasoning-pro",
        "Sonar Reasoning Pro (multi-step reasoning)",
    ),
    model(
        "sonar-deep-research",
        "Sonar Deep Research (long-form research)",
    ),
    model("sonar", "Sonar (fast search)"),
];

const FIREWORKS_MODELS: &[ModelCatalogEntry] = &[
    model("accounts/fireworks/models/glm-5p3", "GLM-5.3 (recommended)"),
    model(
        "accounts/fireworks/models/glm-5p3-flash",
        "GLM-5.3 Flash (faster)",
    ),
    model("accounts/fireworks/models/kimi-k3", "Kimi K3"),
    model("accounts/fireworks/models/minimax-m3", "MiniMax M3"),
    model(
        "accounts/fireworks/models/deepseek-v4-flash-0731",
        "DeepSeek V4 Flash 0731",
    ),
];

const NOVITA_MODELS: &[ModelCatalogEntry] = &[
    model("minimax/minimax-m3", "MiniMax M3 (recommended)"),
    model("moonshotai/kimi-k3", "Kimi K3"),
];

const TOGETHER_MODELS: &[ModelCatalogEntry] = &[
    model("zai-org/GLM-5.1", "GLM-5.1 (recommended for coding)"),
    model("moonshotai/Kimi-K2.5", "Kimi K2.5 (chat and coding)"),
    model("openai/gpt-oss-120b", "GPT-OSS 120B"),
    model("deepseek-ai/DeepSeek-V4-Pro", "DeepSeek V4 Pro"),
    model("moonshotai/Kimi-K2.6", "Kimi K2.6"),
];

const COHERE_MODELS: &[ModelCatalogEntry] = &[
    model(
        "command-a-plus-05-2026",
        "Command A Plus (recommended enterprise model)",
    ),
    model(
        "command-a-reasoning-08-2025",
        "Command A Reasoning (agentic reasoning)",
    ),
    model("command-r-08-2024", "Command R (fast baseline)"),
];

const KIMI_CODE_MODELS: &[ModelCatalogEntry] = &[
    model(
        "kimi-for-coding",
        "Kimi for Coding (official coding-agent model)",
    ),
    model("kimi-k2.5", "Kimi K2.5 (general coding endpoint)"),
];

const MOONSHOT_MODELS: &[ModelCatalogEntry] = &[
    model("kimi-k2.5", "Kimi K2.5 (recommended)"),
    model("kimi-k2-thinking", "Kimi K2 Thinking (deep reasoning)"),
    model("kimi-k2-0905-preview", "Kimi K2 0905 Preview (coding)"),
];

const GLM_MODELS: &[ModelCatalogEntry] = &[
    model("glm-5.3", "GLM-5.3 (recommended)"),
    model("glm-5.2", "GLM-5.2 (previous generation)"),
    model("glm-5.1", "GLM-5.1 (stable fallback)"),
];

const MINIMAX_MODELS: &[ModelCatalogEntry] = &[
    model("MiniMax-M3", "MiniMax M3 (recommended)"),
    model("MiniMax-M2.7", "MiniMax M2.7"),
    model("MiniMax-M2.7-highspeed", "MiniMax M2.7 High-Speed"),
    model("MiniMax-M2.5", "MiniMax M2.5"),
    model("MiniMax-M2.5-highspeed", "MiniMax M2.5 High-Speed"),
];

const QWEN_MODELS: &[ModelCatalogEntry] = &[
    model("qwen3.7-plus", "Qwen 3.7 Plus (balanced, recommended)"),
    model("qwen3.8-max", "Qwen 3.8 Max (highest quality)"),
    model("qwen3.8-flash", "Qwen 3.8 Flash (fast and efficient)"),
    model("qwen3.7-max", "Qwen 3.7 Max (stable flagship)"),
    model("qwen3.6-flash", "Qwen 3.6 Flash (stable fast model)"),
];

const QWEN_CODE_MODELS: &[ModelCatalogEntry] = &[
    model("qwen3.7-plus", "Qwen 3.7 Plus (Coding Plan recommended)"),
    model("qwen3.6-plus", "Qwen 3.6 Plus (Coding Plan)"),
    model("qwen3.5-plus", "Qwen 3.5 Plus (Coding Plan)"),
    model("qwen3-coder-next", "Qwen3 Coder Next (coding specialist)"),
    model("qwen3-coder-plus", "Qwen3 Coder Plus (1M context)"),
    model("qwen3-max-2026-01-23", "Qwen3 Max 2026-01-23"),
];

const NVIDIA_MODELS: &[ModelCatalogEntry] = &[
    model(
        "deepseek-ai/deepseek-v4-pro-0813",
        "DeepSeek V4 Pro 0813 (recommended)",
    ),
    model(
        "deepseek-ai/deepseek-v4-flash-0731",
        "DeepSeek V4 Flash 0731",
    ),
    model("minimaxai/minimax-m3", "MiniMax M3"),
    model("moonshotai/kimi-k3", "Kimi K3"),
    model("moonshotai/kimi-k2.6", "Kimi K2.6"),
];

// Astrai's public model endpoint is not currently reliable. Keep its last
// verified starter list until it can be refreshed without guessing IDs.
const ASTRAI_MODELS: &[ModelCatalogEntry] = &[
    model(
        "anthropic/claude-sonnet-4.6",
        "Claude Sonnet 4.6 (verified Astrai default)",
    ),
    model("openai/gpt-5.2", "GPT-5.2"),
    model("deepseek/deepseek-v3.2", "DeepSeek V3.2"),
    model("z-ai/glm-5", "GLM-5"),
];

const AVIAN_MODELS: &[ModelCatalogEntry] = &[
    model(
        "deepseek/deepseek-v4-flash",
        "DeepSeek V4 Flash (recommended)",
    ),
    model("deepseek/deepseek-v4-pro-0813", "DeepSeek V4 Pro 0813"),
    model("moonshotai/kimi-k2.6", "Kimi K2.6"),
    model("z-ai/glm-5.2", "GLM-5.2"),
];

const OLLAMA_MODELS: &[ModelCatalogEntry] = &[
    model("qwen3.5", "Qwen 3.5 (recommended local model)"),
    model("llama3.2", "Llama 3.2"),
    model("gemma3", "Gemma 3"),
    model("mistral", "Mistral"),
];

const LLAMACPP_MODELS: &[ModelCatalogEntry] = &[
    model(
        "ggml-org/gpt-oss-20b-GGUF",
        "GPT-OSS 20B GGUF (llama.cpp server example)",
    ),
    model(
        "bartowski/Llama-3.3-70B-Instruct-GGUF",
        "Llama 3.3 70B GGUF",
    ),
    model(
        "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF",
        "Qwen2.5 Coder 7B GGUF",
    ),
];

const LOCAL_SERVER_MODELS: &[ModelCatalogEntry] = &[
    model(
        "meta-llama/Llama-3.1-8B-Instruct",
        "Llama 3.1 8B Instruct (example deployment)",
    ),
    model(
        "meta-llama/Llama-3.1-70B-Instruct",
        "Llama 3.1 70B Instruct (example deployment)",
    ),
    model(
        "Qwen/Qwen2.5-Coder-7B-Instruct",
        "Qwen2.5 Coder 7B (example deployment)",
    ),
];

const OSAURUS_MODELS: &[ModelCatalogEntry] = &[
    model("qwen3-30b-a3b-8bit", "Qwen3 30B A3B (local, balanced)"),
    model("gemma-3n-e4b-it-lm-4bit", "Gemma 3N E4B (local, efficient)"),
    model(
        "phi-4-mini-reasoning-mlx-4bit",
        "Phi-4 Mini Reasoning (local)",
    ),
];

const BEDROCK_MODELS: &[ModelCatalogEntry] = &[
    model("anthropic.claude-sonnet-5", "Claude Sonnet 5 (recommended)"),
    model("anthropic.claude-sonnet-4-6", "Claude Sonnet 4.6"),
    model("anthropic.claude-opus-4-6-v1", "Claude Opus 4.6"),
    model(
        "anthropic.claude-haiku-4-5-20251001-v1:0",
        "Claude Haiku 4.5",
    ),
];

const GEMINI_MODELS: &[ModelCatalogEntry] = &[
    model("gemini-3.7-flash", "Gemini 3.7 Flash (recommended)"),
    model("gemini-3.6-flash", "Gemini 3.6 Flash (previous stable)"),
    model("gemini-3.5-flash", "Gemini 3.5 Flash (stable)"),
    model("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
    model("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
    model("gemini-3.1-flash-lite", "Gemini 3.1 Flash-Lite"),
];

/// Normalize provider aliases for catalog lookup.
pub(crate) fn canonical_provider_name(provider_name: &str) -> &str {
    let provider_name = provider_name.trim();

    // This check must precede the broader China-provider normalization, which
    // intentionally maps every Qwen alias to `qwen`.
    if is_qwen_oauth_alias(provider_name) {
        return "qwen-code";
    }

    if let Some(canonical) = canonical_china_provider_name(provider_name) {
        return canonical;
    }

    match provider_name {
        "grok" => "xai",
        "together" => "together-ai",
        "fireworks-ai" => "fireworks",
        "google" | "google-gemini" => "gemini",
        "github-copilot" => "copilot",
        "openai_codex" | "codex" => "openai-codex",
        "kimi_coding" | "kimi_for_coding" => "kimi-code",
        "nvidia-nim" | "build.nvidia.com" => "nvidia",
        "aws-bedrock" => "bedrock",
        "llama.cpp" => "llamacpp",
        "azure-openai" | "azure" => "azure_openai",
        "lm-studio" => "lmstudio",
        "opencode-zen" => "opencode",
        "vercel-ai" => "vercel",
        "cloudflare-ai" => "cloudflare",
        "silicon-flow" => "siliconflow",
        "lite-llm" => "litellm",
        "deep-infra" => "deepinfra",
        "hf" => "huggingface",
        "ai21-labs" => "ai21",
        "kilo" => "kilocli",
        _ => provider_name,
    }
}

/// Return the curated offline starter list for a provider.
pub(crate) fn curated_models_for_provider(provider_name: &str) -> &'static [ModelCatalogEntry] {
    match canonical_provider_name(provider_name) {
        "openrouter" => OPENROUTER_MODELS,
        "anthropic" => ANTHROPIC_MODELS,
        "openai" | "openai-codex" => OPENAI_MODELS,
        "venice" => VENICE_MODELS,
        "groq" => GROQ_MODELS,
        "mistral" => MISTRAL_MODELS,
        "deepseek" => DEEPSEEK_MODELS,
        "xai" => XAI_MODELS,
        "perplexity" => PERPLEXITY_MODELS,
        "fireworks" => FIREWORKS_MODELS,
        "novita" => NOVITA_MODELS,
        "together-ai" => TOGETHER_MODELS,
        "cohere" => COHERE_MODELS,
        "kimi-code" => KIMI_CODE_MODELS,
        "moonshot" => MOONSHOT_MODELS,
        "glm" | "zai" => GLM_MODELS,
        "minimax" => MINIMAX_MODELS,
        "qwen" => QWEN_MODELS,
        "qwen-code" => QWEN_CODE_MODELS,
        "nvidia" => NVIDIA_MODELS,
        "astrai" => ASTRAI_MODELS,
        "avian" => AVIAN_MODELS,
        "ollama" => OLLAMA_MODELS,
        "llamacpp" => LLAMACPP_MODELS,
        "sglang" | "vllm" => LOCAL_SERVER_MODELS,
        "osaurus" => OSAURUS_MODELS,
        "bedrock" => BEDROCK_MODELS,
        "gemini" => GEMINI_MODELS,
        _ => &[],
    }
}

/// Return the provider default. Catalog-backed providers use their first entry.
pub(crate) fn default_model_for_provider(provider_name: &str) -> &'static str {
    match canonical_provider_name(provider_name) {
        // These servers expose an operator-selected model under an arbitrary ID.
        "sglang" | "vllm" | "osaurus" | "opencode-go" => "default",
        canonical => curated_models_for_provider(canonical)
            .first()
            .map_or(GLOBAL_DEFAULT_MODEL, |entry| entry.id),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    const CATALOG_PROVIDERS: &[&str] = &[
        "openrouter",
        "anthropic",
        "openai",
        "openai-codex",
        "venice",
        "groq",
        "mistral",
        "deepseek",
        "xai",
        "perplexity",
        "fireworks",
        "novita",
        "together-ai",
        "cohere",
        "kimi-code",
        "moonshot",
        "glm",
        "zai",
        "minimax",
        "qwen",
        "qwen-code",
        "nvidia",
        "astrai",
        "avian",
        "ollama",
        "llamacpp",
        "sglang",
        "vllm",
        "osaurus",
        "bedrock",
        "gemini",
    ];

    #[test]
    fn catalog_defaults_are_first_and_ids_are_unique() {
        for provider in CATALOG_PROVIDERS {
            let catalog = curated_models_for_provider(provider);
            assert!(!catalog.is_empty(), "missing catalog for {provider}");

            if !matches!(*provider, "sglang" | "vllm" | "osaurus") {
                assert_eq!(default_model_for_provider(provider), catalog[0].id);
            }

            let mut ids = HashSet::new();
            for entry in catalog {
                assert!(
                    ids.insert(entry.id),
                    "duplicate {} model: {}",
                    provider,
                    entry.id
                );
                assert!(!entry.label.trim().is_empty());
            }
        }
    }

    #[test]
    fn current_frontier_defaults_are_selected() {
        assert_eq!(
            default_model_for_provider("openrouter"),
            GLOBAL_DEFAULT_MODEL
        );
        assert_eq!(default_model_for_provider("anthropic"), "claude-sonnet-5");
        assert_eq!(default_model_for_provider("openai"), "gpt-5.6-sol");
        assert_eq!(default_model_for_provider("codex"), "gpt-5.6-sol");
        assert_eq!(default_model_for_provider("gemini"), "gemini-3.7-flash");
        assert_eq!(default_model_for_provider("grok"), "grok-4.6");
        assert_eq!(default_model_for_provider("zai"), "glm-5.3");
        assert_eq!(default_model_for_provider("minimax"), "MiniMax-M3");
    }

    #[test]
    fn aliases_share_catalogs() {
        for (canonical, alias) in [
            ("openai-codex", "codex"),
            ("xai", "grok"),
            ("together-ai", "together"),
            ("fireworks", "fireworks-ai"),
            ("gemini", "google"),
            ("qwen", "dashscope-us"),
            ("qwen-code", "qwen-oauth"),
            ("nvidia", "nvidia-nim"),
            ("bedrock", "aws-bedrock"),
            ("llamacpp", "llama.cpp"),
        ] {
            assert_eq!(
                curated_models_for_provider(canonical),
                curated_models_for_provider(alias)
            );
        }
    }

    #[test]
    fn retired_defaults_are_not_reintroduced() {
        let groq_ids: Vec<_> = curated_models_for_provider("groq")
            .iter()
            .map(|entry| entry.id)
            .collect();
        assert!(!groq_ids.contains(&"llama-3.3-70b-versatile"));

        let gemini_ids: Vec<_> = curated_models_for_provider("gemini")
            .iter()
            .map(|entry| entry.id)
            .collect();
        assert!(!gemini_ids.contains(&"gemini-2.5-pro"));

        let codex_ids: Vec<_> = curated_models_for_provider("openai-codex")
            .iter()
            .map(|entry| entry.id)
            .collect();
        assert!(!codex_ids.contains(&"gpt-5.2-codex"));
    }

    #[test]
    fn unknown_provider_has_no_catalog_and_uses_global_default() {
        assert!(curated_models_for_provider("unknown-provider").is_empty());
        assert_eq!(
            default_model_for_provider("unknown-provider"),
            GLOBAL_DEFAULT_MODEL
        );
    }
}
