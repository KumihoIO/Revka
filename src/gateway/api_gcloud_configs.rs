//! REST API handlers for local gcloud configuration profiles.
//!
//! These endpoints expose metadata-only gcloud config management for the
//! workflow editor's private Cloud Run A2A fields. A gcloud configuration is
//! not a secret itself; it references credentials in the local Cloud SDK
//! credential store. Responses therefore include display metadata only and
//! never include access tokens, refresh tokens, or identity tokens.

use super::AppState;
use super::api::require_auth;
use axum::{
    extract::State,
    http::{HeaderMap, StatusCode},
    response::{IntoResponse, Json},
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::process::Output;
use std::time::Duration;
use tokio::process::Command;

const GCLOUD_TIMEOUT_SECS: u64 = 20;
const GCLOUD_LIST_FORMAT: &str = "json(name,is_active,properties.core.account,properties.core.project,properties.run.region,properties.compute.region)";

#[derive(Serialize, Clone, Debug, PartialEq, Eq)]
pub struct GcloudConfigSummary {
    pub name: String,
    pub is_active: bool,
    pub account: Option<String>,
    pub project: Option<String>,
    pub run_region: Option<String>,
    pub compute_region: Option<String>,
}

#[derive(Serialize)]
pub struct ListGcloudConfigsResponse {
    pub available: bool,
    pub configs: Vec<GcloudConfigSummary>,
    pub error: Option<String>,
}

#[derive(Deserialize)]
pub struct CreateGcloudConfigBody {
    pub name: String,
    pub project: String,
    #[serde(default)]
    pub account: Option<String>,
    #[serde(default)]
    pub run_region: Option<String>,
    #[serde(default)]
    pub compute_region: Option<String>,
}

pub async fn handle_list_gcloud_configs(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    match list_gcloud_configs().await {
        Ok(configs) => Json(ListGcloudConfigsResponse {
            available: true,
            configs,
            error: None,
        })
        .into_response(),
        Err(GcloudCommandError::NotFound) => Json(ListGcloudConfigsResponse {
            available: false,
            configs: vec![],
            error: Some("gcloud was not found in PATH".to_string()),
        })
        .into_response(),
        Err(err) => (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({
                "error": "Failed to list gcloud configurations",
                "code": "gcloud_configs_list_failed",
                "detail": err.public_message(),
            })),
        )
            .into_response(),
    }
}

pub async fn handle_create_gcloud_config(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<CreateGcloudConfigBody>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let name = match validate_config_name(&body.name) {
        Ok(name) => name,
        Err(message) => return bad_request(message, "gcloud_config_invalid_name"),
    };
    let project = match validate_property_value("project", &body.project, true) {
        Ok(project) => project,
        Err(message) => return bad_request(message, "gcloud_config_invalid_project"),
    };
    let account = match validate_optional_property("account", body.account.as_deref()) {
        Ok(account) => account,
        Err(message) => return bad_request(message, "gcloud_config_invalid_account"),
    };
    let run_region = match validate_optional_property("run_region", body.run_region.as_deref()) {
        Ok(region) => region,
        Err(message) => return bad_request(message, "gcloud_config_invalid_run_region"),
    };
    let compute_region =
        match validate_optional_property("compute_region", body.compute_region.as_deref()) {
            Ok(region) => region,
            Err(message) => return bad_request(message, "gcloud_config_invalid_compute_region"),
        };

    let existing = match list_gcloud_configs().await {
        Ok(configs) => configs,
        Err(GcloudCommandError::NotFound) => {
            return (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(serde_json::json!({
                    "error": "gcloud was not found in PATH",
                    "code": "gcloud_not_found"
                })),
            )
                .into_response();
        }
        Err(err) => {
            return (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({
                    "error": "Failed to inspect existing gcloud configurations",
                    "code": "gcloud_configs_list_failed",
                    "detail": err.public_message(),
                })),
            )
                .into_response();
        }
    };

    if existing.iter().any(|config| config.name == name) {
        return (
            StatusCode::CONFLICT,
            Json(serde_json::json!({
                "error": format!("gcloud configuration already exists: {name}"),
                "code": "gcloud_config_already_exists"
            })),
        )
            .into_response();
    }

    let create_args = vec![
        "config".to_string(),
        "configurations".to_string(),
        "create".to_string(),
        name.clone(),
        "--no-activate".to_string(),
        "--quiet".to_string(),
    ];
    if let Err(err) = run_gcloud_checked(create_args).await {
        return gcloud_create_error("Failed to create gcloud configuration", err);
    }

    let mut set_ops: Vec<(&str, String)> = vec![("core/project", project.clone())];
    if let Some(value) = account.clone() {
        set_ops.push(("core/account", value));
    }
    if let Some(value) = run_region.clone() {
        set_ops.push(("run/region", value));
    }
    if let Some(value) = compute_region.clone() {
        set_ops.push(("compute/region", value));
    }

    for (key, value) in set_ops {
        let args = vec![
            format!("--configuration={name}"),
            "config".to_string(),
            "set".to_string(),
            key.to_string(),
            value,
            "--quiet".to_string(),
        ];
        if let Err(err) = run_gcloud_checked(args).await {
            return gcloud_create_error("Failed to set gcloud configuration property", err);
        }
    }

    let created = list_gcloud_configs()
        .await
        .ok()
        .and_then(|configs| configs.into_iter().find(|config| config.name == name))
        .unwrap_or(GcloudConfigSummary {
            name,
            is_active: false,
            account,
            project: Some(project),
            run_region,
            compute_region,
        });

    (StatusCode::CREATED, Json(created)).into_response()
}

fn bad_request(message: String, code: &'static str) -> axum::response::Response {
    (
        StatusCode::BAD_REQUEST,
        Json(serde_json::json!({
            "error": message,
            "code": code
        })),
    )
        .into_response()
}

fn gcloud_create_error(message: &'static str, err: GcloudCommandError) -> axum::response::Response {
    let status = match &err {
        GcloudCommandError::NotFound => StatusCode::SERVICE_UNAVAILABLE,
        GcloudCommandError::TimedOut | GcloudCommandError::Failed(_) => {
            StatusCode::INTERNAL_SERVER_ERROR
        }
    };
    (
        status,
        Json(serde_json::json!({
            "error": message,
            "code": "gcloud_config_create_failed",
            "detail": err.public_message(),
        })),
    )
        .into_response()
}

async fn list_gcloud_configs() -> Result<Vec<GcloudConfigSummary>, GcloudCommandError> {
    let args = vec![
        "config".to_string(),
        "configurations".to_string(),
        "list".to_string(),
        format!("--format={GCLOUD_LIST_FORMAT}"),
    ];
    let output = run_gcloud_checked(args).await?;
    let parsed: Value = serde_json::from_slice(&output.stdout).map_err(|err| {
        GcloudCommandError::Failed(format!("failed to parse gcloud config list output: {err}"))
    })?;
    Ok(parse_gcloud_config_list(&parsed))
}

async fn run_gcloud_checked(args: Vec<String>) -> Result<Output, GcloudCommandError> {
    let mut command = Command::new("gcloud");
    command
        .args(args)
        .env("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")
        .env("CLOUDSDK_CORE_LOG_HTTP", "false")
        .stdin(std::process::Stdio::null());

    let output = match tokio::time::timeout(
        Duration::from_secs(GCLOUD_TIMEOUT_SECS),
        command.output(),
    )
    .await
    {
        Ok(Ok(output)) => output,
        Ok(Err(err)) if err.kind() == std::io::ErrorKind::NotFound => {
            return Err(GcloudCommandError::NotFound);
        }
        Ok(Err(err)) => {
            return Err(GcloudCommandError::Failed(format!(
                "failed to execute gcloud: {err}"
            )));
        }
        Err(_) => return Err(GcloudCommandError::TimedOut),
    };

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        return Err(GcloudCommandError::Failed(if stderr.is_empty() {
            stdout
        } else {
            stderr
        }));
    }

    Ok(output)
}

fn parse_gcloud_config_list(value: &Value) -> Vec<GcloudConfigSummary> {
    let Some(items) = value.as_array() else {
        return vec![];
    };
    let mut configs: Vec<GcloudConfigSummary> = items
        .iter()
        .filter_map(|item| {
            let name = string_at(item, &["name"])?;
            Some(GcloudConfigSummary {
                name,
                is_active: item
                    .get("is_active")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                account: string_at(item, &["properties", "core", "account"]),
                project: string_at(item, &["properties", "core", "project"]),
                run_region: string_at(item, &["properties", "run", "region"]),
                compute_region: string_at(item, &["properties", "compute", "region"]),
            })
        })
        .collect();
    configs.sort_by(|a, b| {
        b.is_active
            .cmp(&a.is_active)
            .then_with(|| a.name.cmp(&b.name))
    });
    configs
}

fn string_at(value: &Value, path: &[&str]) -> Option<String> {
    let mut cursor = value;
    for key in path {
        cursor = cursor.get(*key)?;
    }
    cursor
        .as_str()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

fn validate_config_name(value: &str) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return Err("configuration name is required".to_string());
    }
    if trimmed.len() > 64 {
        return Err("configuration name must be 64 characters or fewer".to_string());
    }
    let mut chars = trimmed.chars();
    let Some(first) = chars.next() else {
        return Err("configuration name is required".to_string());
    };
    if !first.is_ascii_alphanumeric() {
        return Err("configuration name must start with an ASCII letter or number".to_string());
    }
    if !chars.all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '-' | '_' | '.')) {
        return Err(
            "configuration name may contain only ASCII letters, numbers, '-', '_', and '.'"
                .to_string(),
        );
    }
    Ok(trimmed.to_string())
}

fn validate_optional_property(label: &str, value: Option<&str>) -> Result<Option<String>, String> {
    match value {
        Some(value) if value.trim().is_empty() => Ok(None),
        Some(value) => validate_property_value(label, value, false).map(Some),
        None => Ok(None),
    }
}

fn validate_property_value(label: &str, value: &str, required: bool) -> Result<String, String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        if required {
            return Err(format!("{label} is required"));
        }
        return Ok(String::new());
    }
    if trimmed.len() > 256 {
        return Err(format!("{label} must be 256 characters or fewer"));
    }
    if trimmed.chars().any(|ch| ch.is_control()) {
        return Err(format!("{label} may not contain control characters"));
    }
    Ok(trimmed.to_string())
}

#[derive(Debug)]
enum GcloudCommandError {
    NotFound,
    TimedOut,
    Failed(String),
}

impl GcloudCommandError {
    fn public_message(&self) -> String {
        match self {
            GcloudCommandError::NotFound => "gcloud was not found in PATH".to_string(),
            GcloudCommandError::TimedOut => {
                format!("gcloud command timed out after {GCLOUD_TIMEOUT_SECS}s")
            }
            GcloudCommandError::Failed(message) => truncate(message, 500),
        }
    }
}

fn truncate(value: &str, max: usize) -> String {
    if value.chars().count() <= max {
        return value.to_string();
    }
    format!("{}...", value.chars().take(max).collect::<String>())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_config_name_accepts_safe_names() {
        assert_eq!(
            validate_config_name("revka-track3").unwrap(),
            "revka-track3"
        );
        assert_eq!(validate_config_name("team.prod_1").unwrap(), "team.prod_1");
    }

    #[test]
    fn validate_config_name_rejects_shell_like_or_path_names() {
        assert!(validate_config_name("-prod").is_err());
        assert!(validate_config_name("team/prod").is_err());
        assert!(validate_config_name("team prod").is_err());
        assert!(validate_config_name("prod;rm").is_err());
    }

    #[test]
    fn parse_gcloud_config_list_extracts_metadata() {
        let raw = serde_json::json!([
            {
                "name": "default",
                "is_active": true,
                "properties": {
                    "core": {
                        "account": "support@example.com",
                        "project": "construct-498201"
                    },
                    "run": { "region": "us-central1" },
                    "compute": { "region": "us-central1" }
                }
            }
        ]);

        assert_eq!(
            parse_gcloud_config_list(&raw),
            vec![GcloudConfigSummary {
                name: "default".to_string(),
                is_active: true,
                account: Some("support@example.com".to_string()),
                project: Some("construct-498201".to_string()),
                run_region: Some("us-central1".to_string()),
                compute_region: Some("us-central1".to_string()),
            }]
        );
    }
}
