//! Opt-in native Kumiho Rust SDK transport.
//!
//! This is intentionally a narrow read-only slice.  When the `kumiho-native`
//! feature is compiled and `REVKA_KUMIHO_NATIVE_SDK=1` is set, exact item and
//! revision reads try gRPC through the Rust SDK before the existing Python SDK
//! bridge and hosted FastAPI transports. Any native initialization or read
//! failure is returned to the caller so it can preserve the established
//! fallback chain.

use super::kumiho_client::{ItemResponse, RevisionResponse};
use std::sync::Arc;
use tokio::sync::OnceCell;

type NativeClientState = std::result::Result<kumiho::Client, String>;
const NATIVE_SDK_ENV: &str = "REVKA_KUMIHO_NATIVE_SDK";

#[derive(Clone)]
pub(super) struct NativeKumihoTransport {
    auth_token: String,
    tenant_hint: Option<String>,
    client: Option<Arc<OnceCell<NativeClientState>>>,
}

impl NativeKumihoTransport {
    pub(super) fn new(auth_token: String) -> Self {
        Self {
            auth_token,
            tenant_hint: non_empty_env("KUMIHO_TENANT_HINT"),
            client: env_flag(NATIVE_SDK_ENV).then(|| Arc::new(OnceCell::new())),
        }
    }

    async fn client(&self) -> Option<std::result::Result<&kumiho::Client, &str>> {
        let cell = self.client.as_ref()?;
        let state = cell
            .get_or_init(|| async {
                let mut builder = kumiho::Client::builder();
                if !self.auth_token.trim().is_empty() {
                    builder = builder.token(self.auth_token.clone());
                }
                if let Some(tenant_hint) = &self.tenant_hint {
                    builder = builder.tenant_hint(tenant_hint.clone());
                }
                builder.build().await.map_err(|error| error.to_string())
            })
            .await;

        Some(match state {
            Ok(client) => Ok(client),
            Err(error) => Err(error.as_str()),
        })
    }

    pub(super) async fn get_item_by_kref(
        &self,
        kref: &str,
    ) -> Option<std::result::Result<ItemResponse, String>> {
        let client = match self.client().await? {
            Ok(client) => client,
            Err(error) => return Some(Err(error.to_string())),
        };

        Some(
            client
                .get_item_by_kref(kref)
                .await
                .map(item_response_from_sdk)
                .map_err(|error| error.to_string()),
        )
    }

    pub(super) async fn get_revision(
        &self,
        kref: &str,
    ) -> Option<std::result::Result<RevisionResponse, String>> {
        let client = match self.client().await? {
            Ok(client) => client,
            Err(error) => return Some(Err(error.to_string())),
        };

        Some(
            client
                .get_revision(kref)
                .await
                .map(revision_response_from_sdk)
                .map_err(|error| error.to_string()),
        )
    }

    #[cfg(test)]
    pub(super) fn disabled_for_test() -> Self {
        Self {
            auth_token: String::new(),
            tenant_hint: None,
            client: None,
        }
    }

    #[cfg(test)]
    pub(super) fn failing_for_test(message: &str) -> Self {
        let cell = OnceCell::new();
        assert!(
            cell.set(Err(message.to_string())).is_ok(),
            "fresh native client cell must accept test failure"
        );
        Self {
            auth_token: String::new(),
            tenant_hint: None,
            client: Some(Arc::new(cell)),
        }
    }
}

fn non_empty_env(name: &str) -> Option<String> {
    std::env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
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

fn author_fields(
    author: String,
    username: String,
) -> (Option<String>, Option<String>, Option<String>) {
    let display = if username.is_empty() {
        author.clone()
    } else {
        username.clone()
    };
    (Some(author), Some(username), Some(display))
}

fn item_response_from_sdk(item: kumiho::Item) -> ItemResponse {
    item_response_from_parts(
        item.kref.uri().to_string(),
        item.name,
        item.item_name,
        item.kind,
        item.deprecated,
        item.created_at,
        item.author,
        item.username,
        item.metadata,
    )
}

#[allow(clippy::too_many_arguments)]
fn item_response_from_parts(
    kref: String,
    name: String,
    item_name: String,
    kind: String,
    deprecated: bool,
    created_at: Option<String>,
    author: String,
    username: String,
    metadata: std::collections::HashMap<String, String>,
) -> ItemResponse {
    let (author, username, author_display) = author_fields(author, username);
    ItemResponse {
        kref,
        name,
        item_name,
        kind,
        deprecated,
        created_at,
        author,
        username,
        author_display,
        metadata,
    }
}

fn revision_response_from_sdk(revision: kumiho::Revision) -> RevisionResponse {
    revision_response_from_parts(
        revision.kref.uri().to_string(),
        revision.item_kref.uri().to_string(),
        revision.number,
        revision.latest,
        revision.tags,
        revision.metadata,
        revision.deprecated,
        revision.created_at,
        revision.author,
        revision.username,
    )
}

#[allow(clippy::too_many_arguments)]
fn revision_response_from_parts(
    kref: String,
    item_kref: String,
    number: i32,
    latest: bool,
    tags: Vec<String>,
    metadata: std::collections::HashMap<String, String>,
    deprecated: bool,
    created_at: Option<String>,
    author: String,
    username: String,
) -> RevisionResponse {
    let (author, username, author_display) = author_fields(author, username);
    RevisionResponse {
        kref,
        item_kref,
        number,
        latest,
        tags,
        metadata,
        deprecated,
        created_at,
        author,
        username,
        author_display,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[tokio::test]
    async fn native_sdk_disabled_transport_skips_without_initializing() {
        let transport = NativeKumihoTransport::disabled_for_test();
        assert!(transport.client().await.is_none());
    }

    #[test]
    fn native_sdk_activation_requires_an_explicit_true_value() {
        for enabled in ["1", "true", "TRUE", " yes ", "on"] {
            assert!(
                flag_is_enabled(enabled),
                "{enabled:?} should enable native SDK"
            );
        }
        for disabled in ["", "0", "false", "no", "off", "enabled"] {
            assert!(
                !flag_is_enabled(disabled),
                "{disabled:?} should not enable native SDK"
            );
        }
    }

    #[tokio::test]
    async fn native_sdk_initialization_failure_is_cached_for_fallback() {
        let transport = NativeKumihoTransport::failing_for_test("native unavailable");
        let first = transport.client().await.expect("transport is enabled");
        let second = transport.client().await.expect("transport is enabled");
        let first_error = match first {
            Ok(_) => panic!("fixture must fail"),
            Err(error) => error,
        };
        let second_error = match second {
            Ok(_) => panic!("fixture must fail"),
            Err(error) => error,
        };
        assert_eq!(first_error, "native unavailable");
        assert_eq!(second_error, "native unavailable");
    }

    #[test]
    fn native_sdk_item_conversion_preserves_gateway_shape() {
        let response = item_response_from_parts(
            "kref://Revka/WorkflowRuns/run.workflow_run".into(),
            "run.workflow_run".into(),
            "run".into(),
            "workflow_run".into(),
            false,
            Some("2026-08-31T00:00:00Z".into()),
            "user-id".into(),
            "operator@example.com".into(),
            HashMap::from([("status".into(), "running".into())]),
        );

        assert_eq!(response.kref, "kref://Revka/WorkflowRuns/run.workflow_run");
        assert_eq!(response.item_name, "run");
        assert_eq!(response.author.as_deref(), Some("user-id"));
        assert_eq!(response.username.as_deref(), Some("operator@example.com"));
        assert_eq!(
            response.author_display.as_deref(),
            Some("operator@example.com")
        );
        assert_eq!(
            response.metadata.get("status").map(String::as_str),
            Some("running")
        );
    }

    #[test]
    fn native_sdk_revision_conversion_preserves_gateway_shape() {
        let response = revision_response_from_parts(
            "kref://Revka/WorkflowRuns/run.workflow_run?r=3".into(),
            "kref://Revka/WorkflowRuns/run.workflow_run".into(),
            3,
            true,
            vec!["latest".into(), "published".into()],
            HashMap::from([("status".into(), "completed".into())]),
            false,
            Some("2026-08-31T00:01:00Z".into()),
            "user-id".into(),
            String::new(),
        );

        assert_eq!(response.number, 3);
        assert!(response.latest);
        assert_eq!(response.author_display.as_deref(), Some("user-id"));
        assert_eq!(response.tags, ["latest", "published"]);
        assert_eq!(
            response.metadata.get("status").map(String::as_str),
            Some("completed")
        );
    }
}
