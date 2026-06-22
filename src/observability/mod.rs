pub mod log;
pub mod multi;
pub mod noop;
#[cfg(feature = "observability-otel")]
pub mod otel;
#[cfg(feature = "observability-prometheus")]
pub mod prometheus;
pub mod runtime_trace;
pub mod traits;
pub mod verbose;

#[allow(unused_imports)]
pub use self::log::LogObserver;
pub use self::multi::MultiObserver;
pub use noop::NoopObserver;
#[cfg(feature = "observability-otel")]
pub use otel::OtelObserver;
#[cfg(feature = "observability-prometheus")]
pub use prometheus::PrometheusObserver;
pub use traits::{Observer, ObserverEvent};
#[allow(unused_imports)]
pub use verbose::VerboseObserver;

use crate::config::ObservabilityConfig;
use std::sync::{Arc, OnceLock};

/// Factory: create the right observer from config.
///
/// `config.backend` may name a single backend (e.g. `"prometheus"`) or several
/// comma-separated backends (e.g. `"prometheus,otel"`). When more than one is
/// requested, each is built via the single-backend path and the results are
/// fanned out through a [`MultiObserver`]; zero or one backend keeps the
/// single-backend path unchanged for backward compatibility.
pub fn create_observer(config: &ObservabilityConfig) -> Box<dyn Observer> {
    let backends: Vec<&str> = config
        .backend
        .split(',')
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .collect();

    match backends.as_slice() {
        // No backend specified at all (empty string) — keep the historical
        // fall-back to noop via the single-backend path.
        [] => create_single_observer(&config.backend, config),
        [single] => create_single_observer(single, config),
        many => Box::new(MultiObserver::new(
            many.iter()
                .map(|b| create_single_observer(b, config))
                .collect(),
        )),
    }
}

/// Build exactly one backend from its name. Unknown names fall back to noop.
fn create_single_observer(
    backend: &str,
    // `config` is only read by the OTel arm; silence the unused warning when
    // that feature is compiled out.
    #[cfg_attr(not(feature = "observability-otel"), allow(unused_variables))]
    config: &ObservabilityConfig,
) -> Box<dyn Observer> {
    match backend {
        "log" => Box::new(LogObserver::new()),
        "verbose" => Box::new(VerboseObserver::new()),
        "prometheus" => {
            #[cfg(feature = "observability-prometheus")]
            {
                Box::new(PrometheusObserver::new())
            }
            #[cfg(not(feature = "observability-prometheus"))]
            {
                tracing::warn!(
                    "Prometheus backend requested but this build was compiled without `observability-prometheus`; falling back to noop."
                );
                Box::new(NoopObserver)
            }
        }
        "otel" | "opentelemetry" | "otlp" => {
            #[cfg(feature = "observability-otel")]
            match OtelObserver::new(
                config.otel_endpoint.as_deref(),
                config.otel_service_name.as_deref(),
            ) {
                Ok(obs) => {
                    tracing::info!(
                        endpoint = config
                            .otel_endpoint
                            .as_deref()
                            .unwrap_or("http://localhost:4318"),
                        "OpenTelemetry observer initialized"
                    );
                    Box::new(obs)
                }
                Err(e) => {
                    tracing::error!("Failed to create OTel observer: {e}. Falling back to noop.");
                    Box::new(NoopObserver)
                }
            }
            #[cfg(not(feature = "observability-otel"))]
            {
                tracing::warn!(
                    "OpenTelemetry backend requested but this build was compiled without `observability-otel`; falling back to noop."
                );
                Box::new(NoopObserver)
            }
        }
        "none" | "noop" => Box::new(NoopObserver),
        _ => {
            tracing::warn!("Unknown observability backend '{backend}', falling back to noop");
            Box::new(NoopObserver)
        }
    }
}

// ── Process-global singleton ────────────────────────────────────────
// Every entry point (gateway, agent, channels, daemon, interactive loop)
// shares ONE observer so that all telemetry — LLM request/token counters,
// tool-call counts/durations, agent durations, gauges — feeds the single
// registry that `GET /metrics` scrapes. Without this, each path built its
// own throwaway `PrometheusObserver` (a fresh `prometheus::Registry`) and
// the scraped surface stayed near-empty. Mirrors `CostTracker::get_or_init_global`.

static GLOBAL_OBSERVER: OnceLock<Arc<dyn Observer>> = OnceLock::new();

/// Return the process-global `Observer`, building it from `config` on first
/// call. Subsequent calls (from whichever entry point starts second) receive
/// the same `Arc`, ignoring their `config` — the first caller wins.
pub fn get_or_init_global(config: &ObservabilityConfig) -> Arc<dyn Observer> {
    GLOBAL_OBSERVER
        .get_or_init(|| Arc::from(create_observer(config)))
        .clone()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn factory_none_returns_noop() {
        let cfg = ObservabilityConfig {
            backend: "none".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&cfg).name(), "noop");
    }

    #[test]
    fn factory_noop_returns_noop() {
        let cfg = ObservabilityConfig {
            backend: "noop".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&cfg).name(), "noop");
    }

    #[test]
    fn factory_log_returns_log() {
        let cfg = ObservabilityConfig {
            backend: "log".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&cfg).name(), "log");
    }

    #[test]
    fn factory_verbose_returns_verbose() {
        let cfg = ObservabilityConfig {
            backend: "verbose".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&cfg).name(), "verbose");
    }

    #[test]
    fn factory_prometheus_returns_prometheus() {
        let cfg = ObservabilityConfig {
            backend: "prometheus".into(),
            ..ObservabilityConfig::default()
        };
        let expected = if cfg!(feature = "observability-prometheus") {
            "prometheus"
        } else {
            "noop"
        };
        assert_eq!(create_observer(&cfg).name(), expected);
    }

    #[test]
    fn factory_otel_returns_otel() {
        let cfg = ObservabilityConfig {
            backend: "otel".into(),
            otel_endpoint: Some("http://127.0.0.1:19999".into()),
            otel_service_name: Some("test".into()),
            ..ObservabilityConfig::default()
        };
        let expected = if cfg!(feature = "observability-otel") {
            "otel"
        } else {
            "noop"
        };
        assert_eq!(create_observer(&cfg).name(), expected);
    }

    #[test]
    fn factory_opentelemetry_alias() {
        let cfg = ObservabilityConfig {
            backend: "opentelemetry".into(),
            otel_endpoint: Some("http://127.0.0.1:19999".into()),
            otel_service_name: Some("test".into()),
            ..ObservabilityConfig::default()
        };
        let expected = if cfg!(feature = "observability-otel") {
            "otel"
        } else {
            "noop"
        };
        assert_eq!(create_observer(&cfg).name(), expected);
    }

    #[test]
    fn factory_otlp_alias() {
        let cfg = ObservabilityConfig {
            backend: "otlp".into(),
            otel_endpoint: Some("http://127.0.0.1:19999".into()),
            otel_service_name: Some("test".into()),
            ..ObservabilityConfig::default()
        };
        let expected = if cfg!(feature = "observability-otel") {
            "otel"
        } else {
            "noop"
        };
        assert_eq!(create_observer(&cfg).name(), expected);
    }

    #[test]
    fn factory_multiple_backends_returns_multi() {
        let cfg = ObservabilityConfig {
            backend: "log,verbose".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&cfg).name(), "multi");
    }

    #[test]
    fn factory_multiple_backends_trims_whitespace_and_ignores_empties() {
        // Stray whitespace and empty segments must not split a single backend
        // into a `MultiObserver`, nor inflate the backend count.
        let single = ObservabilityConfig {
            backend: " log , ".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&single).name(), "log");

        let multi = ObservabilityConfig {
            backend: " log , verbose ".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&multi).name(), "multi");
    }

    #[test]
    fn factory_unknown_falls_back_to_noop() {
        let cfg = ObservabilityConfig {
            backend: "xyzzy_unknown".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&cfg).name(), "noop");
    }

    #[test]
    fn factory_empty_string_falls_back_to_noop() {
        let cfg = ObservabilityConfig {
            backend: String::new(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&cfg).name(), "noop");
    }

    #[test]
    fn factory_garbage_falls_back_to_noop() {
        let cfg = ObservabilityConfig {
            backend: "xyzzy_garbage_123".into(),
            ..ObservabilityConfig::default()
        };
        assert_eq!(create_observer(&cfg).name(), "noop");
    }

    #[test]
    fn global_observer_is_a_shared_singleton() {
        // The first caller's config wins; every later caller — regardless of the
        // config it passes — gets the SAME `Arc`, so all telemetry feeds one
        // registry (#455). `GLOBAL_OBSERVER` is a process-global `OnceLock`
        // shared across the whole test binary, so whichever test touches it
        // first decides the backend — we therefore assert only the pointer
        // identity, which holds regardless of initialization order, and avoid
        // any backend-name assertion that would race other unit tests.
        let first = get_or_init_global(&ObservabilityConfig {
            backend: "log".into(),
            ..ObservabilityConfig::default()
        });
        let second = get_or_init_global(&ObservabilityConfig {
            backend: "verbose".into(),
            ..ObservabilityConfig::default()
        });
        assert!(
            Arc::ptr_eq(&first, &second),
            "expected the same global observer instance on every call"
        );
    }
}
