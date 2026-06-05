//! UI skin package API.
//!
//! Skins are local ZIP packages containing a root `revka-skin.json`
//! manifest plus image assets under `assets/`. The gateway validates and
//! stores them under the configured workspace directory.

use std::{
    collections::{BTreeMap, HashSet},
    io::{Cursor, Read},
    path::{Component, Path, PathBuf},
};

use axum::{
    body::Bytes,
    extract::{Path as AxumPath, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Json, Response},
};
use serde::{Deserialize, Serialize};
use zip::ZipArchive;

use super::{AppState, api::require_auth};

pub const SKIN_ZIP_MAX_BODY: usize = 25 * 1024 * 1024;
const SKIN_MAX_EXTRACTED_BYTES: u64 = 50 * 1024 * 1024;
const SKIN_MAX_FILE_COUNT: usize = 128;
const MANIFEST_FILE: &str = "revka-skin.json";
const STORED_MANIFEST_FILE: &str = "manifest.json";
const ASSET_DIR: &str = "assets";
const BUILTIN_REVKA_SKIN_ID: &str = "revka";
const BUILTIN_MATRIX_SKIN_ID: &str = "matrix";

const ASSET_SLOTS: &[&str] = &[
    "brandLogo",
    "operatorAvatar",
    "dashboardHero",
    "shellTexture",
    "panelDecoration",
    "pageBackdrop",
    "sidebarBackdrop",
    "headerBackdrop",
    "dashboardShowcase",
    "dashboardAccent",
    "graphBackdrop",
    "metricDecoration",
    "runCardDecoration",
    "stepCardDecoration",
    "timelineDecoration",
    "riskRailDecoration",
    "agentRailDecoration",
    "commandBandDecoration",
    "recentRunsDecoration",
    "statusRunningBadge",
    "statusSuccessBadge",
    "statusFailedBadge",
    "statusPendingBadge",
    "statusSkippedBadge",
];

const PACKAGE_EXTENSIONS: &[&str] = &["json", "png", "jpg", "jpeg", "webp"];
const ASSET_EXTENSIONS: &[&str] = &["png", "jpg", "jpeg", "webp"];

const MATRIX_COMMON_TOKENS: &[(&str, &str)] = &[
    ("--revka-radius-sm", "10px"),
    ("--revka-radius-md", "14px"),
    ("--revka-radius-lg", "18px"),
    ("--revka-shell-width", "18rem"),
];

const REVKA_COMMON_TOKENS: &[(&str, &str)] = &[
    ("--revka-radius-sm", "6px"),
    ("--revka-radius-md", "8px"),
    ("--revka-radius-lg", "10px"),
    ("--revka-shell-width", "18rem"),
];

const REVKA_DARK_TOKENS: &[(&str, &str)] = &[
    ("--revka-bg-base", "#0b0d10"),
    ("--revka-bg-shell", "#101318"),
    ("--revka-bg-surface", "#13161d"),
    ("--revka-bg-elevated", "#191f27"),
    ("--revka-bg-panel", "rgba(19, 22, 29, 0.94)"),
    ("--revka-bg-panel-strong", "rgba(22, 27, 35, 0.98)"),
    ("--revka-bg-input", "#10141a"),
    ("--revka-hover-surface", "rgba(100, 116, 139, 0.08)"),
    ("--revka-border-soft", "rgba(148, 163, 184, 0.13)"),
    ("--revka-border-strong", "rgba(148, 163, 184, 0.22)"),
    ("--revka-border-neutral", "rgba(226, 232, 240, 0.08)"),
    ("--revka-text-primary", "#f7f8fc"),
    ("--revka-text-secondary", "#c8ceda"),
    ("--revka-text-muted", "#929bad"),
    ("--revka-text-faint", "#687386"),
    ("--revka-signal-live", "#8fa8c8"),
    ("--revka-signal-live-soft", "rgba(143, 168, 200, 0.10)"),
    ("--revka-signal-network", "#6f83d6"),
    ("--revka-signal-network-soft", "rgba(111, 131, 214, 0.09)"),
    ("--revka-signal-selected", "#7c8ddd"),
    ("--revka-signal-selected-soft", "rgba(124, 141, 221, 0.10)"),
    ("--revka-signal-warn", "#f6b44b"),
    ("--revka-signal-danger", "#f87171"),
    ("--revka-status-success", "#2fbf75"),
    ("--revka-status-ok", "#2fbf75"),
    ("--revka-status-warning", "#f6b44b"),
    ("--revka-status-danger", "#f87171"),
    ("--revka-status-error", "#f87171"),
    ("--revka-status-blocked", "#f59e0b"),
    ("--revka-status-idle", "#7b8797"),
    ("--revka-shadow-panel", "0 14px 34px rgba(0, 0, 0, 0.28)"),
    ("--revka-shadow-overlay", "0 24px 60px rgba(0, 0, 0, 0.40)"),
    ("--revka-grid-line", "rgba(148, 163, 184, 0.042)"),
    ("--revka-node-accent-idle", "7%"),
    ("--revka-node-accent-selected", "18%"),
];

const REVKA_LIGHT_TOKENS: &[(&str, &str)] = &[
    ("--revka-bg-base", "#eef1f5"),
    ("--revka-bg-shell", "#e5eaf0"),
    ("--revka-bg-surface", "#f7f9fb"),
    ("--revka-bg-elevated", "#e9edf3"),
    ("--revka-bg-panel", "rgba(247, 249, 251, 0.94)"),
    ("--revka-bg-panel-strong", "rgba(242, 245, 249, 0.98)"),
    ("--revka-bg-input", "#eef2f6"),
    ("--revka-hover-surface", "rgba(73, 86, 141, 0.06)"),
    ("--revka-border-soft", "rgba(45, 55, 72, 0.12)"),
    ("--revka-border-strong", "rgba(71, 85, 105, 0.18)"),
    ("--revka-border-neutral", "rgba(30, 41, 59, 0.10)"),
    ("--revka-text-primary", "#111827"),
    ("--revka-text-secondary", "#334155"),
    ("--revka-text-muted", "#64748b"),
    ("--revka-text-faint", "#94a3b8"),
    ("--revka-signal-live", "#4f75a8"),
    ("--revka-signal-live-soft", "rgba(79, 117, 168, 0.08)"),
    ("--revka-signal-network", "#596cc6"),
    ("--revka-signal-network-soft", "rgba(89, 108, 198, 0.08)"),
    ("--revka-signal-selected", "#6575d3"),
    ("--revka-signal-selected-soft", "rgba(101, 117, 211, 0.08)"),
    ("--revka-signal-warn", "#b86f00"),
    ("--revka-signal-danger", "#dc3f58"),
    ("--revka-status-success", "#1f9463"),
    ("--revka-status-ok", "#1f9463"),
    ("--revka-status-warning", "#b86f00"),
    ("--revka-status-danger", "#dc3f58"),
    ("--revka-status-error", "#dc3f58"),
    ("--revka-status-blocked", "#a36300"),
    ("--revka-status-idle", "#7a8797"),
    ("--revka-shadow-panel", "0 10px 24px rgba(15, 23, 42, 0.08)"),
    (
        "--revka-shadow-overlay",
        "0 18px 42px rgba(15, 23, 42, 0.14)",
    ),
    ("--revka-grid-line", "rgba(71, 85, 105, 0.04)"),
    ("--revka-node-accent-idle", "12%"),
    ("--revka-node-accent-selected", "24%"),
];

const MATRIX_DARK_TOKENS: &[(&str, &str)] = &[
    ("--revka-bg-base", "#05080a"),
    ("--revka-bg-shell", "#08100f"),
    ("--revka-bg-surface", "#0c1413"),
    ("--revka-bg-elevated", "#111a18"),
    ("--revka-bg-panel", "rgba(12, 20, 19, 0.88)"),
    ("--revka-bg-panel-strong", "rgba(15, 24, 22, 0.96)"),
    ("--revka-bg-input", "#0f1716"),
    ("--revka-border-soft", "rgba(125, 255, 155, 0.08)"),
    ("--revka-border-strong", "rgba(125, 255, 155, 0.18)"),
    ("--revka-border-neutral", "rgba(255, 255, 255, 0.08)"),
    ("--revka-text-primary", "#e7f1eb"),
    ("--revka-text-secondary", "#afc0b7"),
    ("--revka-text-muted", "#73877e"),
    ("--revka-text-faint", "#53645d"),
    ("--revka-signal-live", "#7dff9b"),
    ("--revka-signal-live-soft", "rgba(125, 255, 155, 0.16)"),
    ("--revka-signal-network", "#72d8ff"),
    ("--revka-signal-network-soft", "rgba(114, 216, 255, 0.14)"),
    ("--revka-signal-selected", "#b8ffca"),
    ("--revka-status-success", "#7dff9b"),
    ("--revka-status-warning", "#ffcc66"),
    ("--revka-status-danger", "#ff6b7a"),
    ("--revka-status-blocked", "#f59e0b"),
    ("--revka-status-idle", "#64748b"),
    ("--revka-shadow-panel", "0 16px 34px rgba(0, 0, 0, 0.34)"),
    ("--revka-shadow-overlay", "0 24px 60px rgba(0, 0, 0, 0.44)"),
    ("--revka-grid-line", "rgba(125, 255, 155, 0.05)"),
    ("--revka-node-accent-idle", "8%"),
    ("--revka-node-accent-selected", "18%"),
];

const MATRIX_LIGHT_TOKENS: &[(&str, &str)] = &[
    ("--revka-bg-base", "#f4f8f5"),
    ("--revka-bg-shell", "#eef4f0"),
    ("--revka-bg-surface", "#ffffff"),
    ("--revka-bg-elevated", "#f8fbf8"),
    ("--revka-bg-panel", "rgba(255, 255, 255, 0.92)"),
    ("--revka-bg-panel-strong", "rgba(255, 255, 255, 0.98)"),
    ("--revka-bg-input", "#f2f6f3"),
    ("--revka-border-soft", "rgba(63, 175, 104, 0.12)"),
    ("--revka-border-strong", "rgba(63, 175, 104, 0.24)"),
    ("--revka-border-neutral", "rgba(15, 23, 42, 0.10)"),
    ("--revka-text-primary", "#13201b"),
    ("--revka-text-secondary", "#33463e"),
    ("--revka-text-muted", "#60746b"),
    ("--revka-text-faint", "#819289"),
    ("--revka-signal-live", "#3faf68"),
    ("--revka-signal-live-soft", "rgba(63, 175, 104, 0.14)"),
    ("--revka-signal-network", "#4da3d9"),
    ("--revka-signal-network-soft", "rgba(77, 163, 217, 0.14)"),
    ("--revka-signal-selected", "#2c8f57"),
    ("--revka-status-success", "#2f9e5a"),
    ("--revka-status-warning", "#c98a18"),
    ("--revka-status-danger", "#d25563"),
    ("--revka-status-blocked", "#b7791f"),
    ("--revka-status-idle", "#7a8797"),
    ("--revka-shadow-panel", "0 10px 24px rgba(15, 23, 42, 0.08)"),
    (
        "--revka-shadow-overlay",
        "0 18px 40px rgba(15, 23, 42, 0.12)",
    ),
    ("--revka-grid-line", "rgba(63, 175, 104, 0.05)"),
    ("--revka-node-accent-idle", "22%"),
    ("--revka-node-accent-selected", "38%"),
];

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct SkinManifest {
    pub schema_version: u32,
    pub id: String,
    pub name: String,
    pub version: String,
    pub modes: SkinModes,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SkinModes {
    #[serde(default)]
    pub light: Option<SkinMode>,
    #[serde(default)]
    pub dark: Option<SkinMode>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SkinMode {
    #[serde(default)]
    pub tokens: BTreeMap<String, String>,
    #[serde(default)]
    pub assets: BTreeMap<String, String>,
    #[serde(default)]
    pub preview: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct SkinSummary {
    id: String,
    name: String,
    version: String,
    manifest: SkinManifest,
    asset_base_path: String,
    builtin: bool,
}

struct ParsedSkinPackage {
    manifest: SkinManifest,
    assets: Vec<(PathBuf, Vec<u8>)>,
}

pub async fn handle_list_skins(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let root = skins_root(&state.config.lock().workspace_dir);
    let mut skins = builtin_skin_summaries();
    let mut seen_ids: HashSet<String> = skins.iter().map(|skin| skin.id.clone()).collect();
    let mut dir = match tokio::fs::read_dir(&root).await {
        Ok(dir) => dir,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
            return Json(serde_json::json!({ "skins": skins })).into_response();
        }
        Err(err) => {
            tracing::error!(err = %err, root = %root.display(), "failed to list skins");
            return json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "failed to list skin storage",
            );
        }
    };

    loop {
        match dir.next_entry().await {
            Ok(Some(entry)) => {
                let manifest_path = entry.path().join(STORED_MANIFEST_FILE);
                let Ok(bytes) = tokio::fs::read(&manifest_path).await else {
                    continue;
                };
                match serde_json::from_slice::<SkinManifest>(&bytes) {
                    Ok(manifest) => {
                        if !seen_ids.insert(manifest.id.clone()) {
                            tracing::warn!(
                                id = %manifest.id,
                                path = %manifest_path.display(),
                                "skipping stored skin because id is reserved or duplicated"
                            );
                            continue;
                        }
                        skins.push(skin_summary(manifest));
                    }
                    Err(err) => tracing::warn!(
                        err = %err,
                        path = %manifest_path.display(),
                        "skipping invalid stored skin manifest"
                    ),
                }
            }
            Ok(None) => break,
            Err(err) => {
                tracing::warn!(err = %err, "failed to read skin directory entry");
                break;
            }
        }
    }

    skins.sort_by(|a, b| a.name.cmp(&b.name).then_with(|| a.id.cmp(&b.id)));
    Json(serde_json::json!({ "skins": skins })).into_response()
}

pub async fn handle_import_skin(
    State(state): State<AppState>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    if body.is_empty() {
        return json_error(StatusCode::BAD_REQUEST, "empty ZIP body");
    }
    if body.len() > SKIN_ZIP_MAX_BODY {
        return json_error(
            StatusCode::PAYLOAD_TOO_LARGE,
            "skin ZIP exceeds 25 MiB limit",
        );
    }

    let parsed = match parse_skin_package(&body) {
        Ok(parsed) => parsed,
        Err(err) => return json_error(StatusCode::BAD_REQUEST, &err),
    };
    if is_builtin_skin_id(&parsed.manifest.id) {
        return json_error(
            StatusCode::BAD_REQUEST,
            "skin id is reserved for a built-in skin",
        );
    }

    let workspace_dir = state.config.lock().workspace_dir.clone();
    let root = skins_root(&workspace_dir);
    if let Err(err) = tokio::fs::create_dir_all(&root).await {
        tracing::error!(err = %err, root = %root.display(), "failed to create skin root");
        return json_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "failed to create skin storage",
        );
    }

    let canonical_root = match root.canonicalize() {
        Ok(path) => path,
        Err(err) => {
            tracing::error!(err = %err, root = %root.display(), "failed to canonicalize skin root");
            return json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "failed to prepare skin storage",
            );
        }
    };
    let skin_dir = canonical_root.join(&parsed.manifest.id);

    if skin_dir.exists() {
        match skin_dir.canonicalize() {
            Ok(canonical) if canonical.starts_with(&canonical_root) => {
                if let Err(err) = tokio::fs::remove_dir_all(&canonical).await {
                    tracing::error!(err = %err, dir = %canonical.display(), "failed to replace skin");
                    return json_error(StatusCode::INTERNAL_SERVER_ERROR, "failed to replace skin");
                }
            }
            _ => return json_error(StatusCode::BAD_REQUEST, "skin storage path escaped root"),
        }
    }

    if let Err(err) = tokio::fs::create_dir_all(skin_dir.join(ASSET_DIR)).await {
        tracing::error!(err = %err, dir = %skin_dir.display(), "failed to create skin directory");
        return json_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "failed to create skin directory",
        );
    }

    let manifest_json = match serde_json::to_vec_pretty(&parsed.manifest) {
        Ok(json) => json,
        Err(err) => {
            tracing::error!(err = %err, "failed to serialize skin manifest");
            let _ = tokio::fs::remove_dir_all(&skin_dir).await;
            return json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "failed to serialize skin manifest",
            );
        }
    };

    if let Err(err) = tokio::fs::write(skin_dir.join(STORED_MANIFEST_FILE), manifest_json).await {
        tracing::error!(err = %err, "failed to persist skin manifest");
        let _ = tokio::fs::remove_dir_all(&skin_dir).await;
        return json_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "failed to persist skin manifest",
        );
    }

    for (rel, bytes) in parsed.assets {
        let dst = skin_dir.join(rel);
        if let Some(parent) = dst.parent()
            && let Err(err) = tokio::fs::create_dir_all(parent).await
        {
            tracing::error!(err = %err, dir = %parent.display(), "failed to create asset dir");
            let _ = tokio::fs::remove_dir_all(&skin_dir).await;
            return json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "failed to persist skin assets",
            );
        }
        if let Err(err) = tokio::fs::write(&dst, bytes).await {
            tracing::error!(err = %err, path = %dst.display(), "failed to write skin asset");
            let _ = tokio::fs::remove_dir_all(&skin_dir).await;
            return json_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "failed to persist skin assets",
            );
        }
    }

    Json(serde_json::json!({ "skin": skin_summary(parsed.manifest) })).into_response()
}

pub async fn handle_delete_skin(
    State(state): State<AppState>,
    AxumPath(id): AxumPath<String>,
    headers: HeaderMap,
) -> Response {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }
    if !valid_skin_id(&id) {
        return json_error(StatusCode::BAD_REQUEST, "invalid skin id");
    }
    if is_builtin_skin_id(&id) {
        return StatusCode::NO_CONTENT.into_response();
    }

    let root = skins_root(&state.config.lock().workspace_dir);
    let Err(err) = tokio::fs::create_dir_all(&root).await else {
        return delete_skin_dir(&root, &id).await;
    };
    tracing::error!(err = %err, root = %root.display(), "failed to create skin root for delete");
    json_error(
        StatusCode::INTERNAL_SERVER_ERROR,
        "failed to prepare skin storage",
    )
}

async fn delete_skin_dir(root: &Path, id: &str) -> Response {
    let canonical_root = match root.canonicalize() {
        Ok(path) => path,
        Err(_) => return StatusCode::NO_CONTENT.into_response(),
    };
    let target = canonical_root.join(id);
    if !target.exists() {
        return StatusCode::NO_CONTENT.into_response();
    }
    let canonical_target = match target.canonicalize() {
        Ok(path) if path.starts_with(&canonical_root) => path,
        _ => return json_error(StatusCode::BAD_REQUEST, "skin storage path escaped root"),
    };
    match tokio::fs::remove_dir_all(&canonical_target).await {
        Ok(()) => StatusCode::NO_CONTENT.into_response(),
        Err(err) => {
            tracing::error!(err = %err, dir = %canonical_target.display(), "failed to delete skin");
            json_error(StatusCode::INTERNAL_SERVER_ERROR, "failed to delete skin")
        }
    }
}

pub async fn handle_skin_asset(
    State(state): State<AppState>,
    AxumPath((id, path)): AxumPath<(String, String)>,
) -> Response {
    if !valid_skin_id(&id) {
        return (StatusCode::BAD_REQUEST, "invalid skin id").into_response();
    }

    let root = skins_root(&state.config.lock().workspace_dir)
        .join(&id)
        .join(ASSET_DIR);
    let resolved = match safe_resolve_existing_file(&root, &path) {
        Ok(path) => path,
        Err("not found") => return (StatusCode::NOT_FOUND, "not found").into_response(),
        Err(e) => return (StatusCode::BAD_REQUEST, format!("invalid path: {e}")).into_response(),
    };

    match tokio::fs::read(&resolved).await {
        Ok(bytes) => {
            let mime = mime_guess::from_path(&resolved)
                .first_or_octet_stream()
                .to_string();
            (
                StatusCode::OK,
                [
                    (header::CONTENT_TYPE, mime),
                    (header::X_CONTENT_TYPE_OPTIONS, "nosniff".to_string()),
                    (header::CACHE_CONTROL, "private, max-age=300".to_string()),
                ],
                bytes,
            )
                .into_response()
        }
        Err(_) => (StatusCode::NOT_FOUND, "not found").into_response(),
    }
}

fn skins_root(workspace_dir: &Path) -> PathBuf {
    workspace_dir.join("skins")
}

fn skin_summary(manifest: SkinManifest) -> SkinSummary {
    SkinSummary {
        id: manifest.id.clone(),
        name: manifest.name.clone(),
        version: manifest.version.clone(),
        asset_base_path: format!("/api/skins/{}/assets", manifest.id),
        builtin: false,
        manifest,
    }
}

fn builtin_skin_summaries() -> Vec<SkinSummary> {
    vec![builtin_revka_skin_summary(), builtin_matrix_skin_summary()]
}

fn builtin_revka_skin_summary() -> SkinSummary {
    let manifest = builtin_revka_manifest();
    SkinSummary {
        id: manifest.id.clone(),
        name: manifest.name.clone(),
        version: manifest.version.clone(),
        asset_base_path: format!("/api/skins/{}/assets", manifest.id),
        builtin: true,
        manifest,
    }
}

fn builtin_matrix_skin_summary() -> SkinSummary {
    let manifest = builtin_matrix_manifest();
    SkinSummary {
        id: manifest.id.clone(),
        name: manifest.name.clone(),
        version: manifest.version.clone(),
        asset_base_path: format!("/api/skins/{}/assets", manifest.id),
        builtin: true,
        manifest,
    }
}

fn builtin_revka_manifest() -> SkinManifest {
    SkinManifest {
        schema_version: 1,
        id: BUILTIN_REVKA_SKIN_ID.to_string(),
        name: "Revka Default".to_string(),
        version: "1.0.0".to_string(),
        modes: SkinModes {
            light: Some(builtin_skin_mode(REVKA_COMMON_TOKENS, REVKA_LIGHT_TOKENS)),
            dark: Some(builtin_skin_mode(REVKA_COMMON_TOKENS, REVKA_DARK_TOKENS)),
        },
    }
}

fn builtin_matrix_manifest() -> SkinManifest {
    SkinManifest {
        schema_version: 1,
        id: BUILTIN_MATRIX_SKIN_ID.to_string(),
        name: "Matrix".to_string(),
        version: "1.0.0".to_string(),
        modes: SkinModes {
            light: Some(builtin_skin_mode(MATRIX_COMMON_TOKENS, MATRIX_LIGHT_TOKENS)),
            dark: Some(builtin_skin_mode(MATRIX_COMMON_TOKENS, MATRIX_DARK_TOKENS)),
        },
    }
}

fn builtin_skin_mode(common_tokens: &[(&str, &str)], mode_tokens: &[(&str, &str)]) -> SkinMode {
    let tokens = common_tokens
        .iter()
        .chain(mode_tokens.iter())
        .map(|(name, value)| ((*name).to_string(), (*value).to_string()))
        .collect();
    SkinMode {
        tokens,
        assets: BTreeMap::new(),
        preview: None,
    }
}

fn is_builtin_skin_id(id: &str) -> bool {
    id == BUILTIN_REVKA_SKIN_ID || id == BUILTIN_MATRIX_SKIN_ID
}

fn parse_skin_package(bytes: &[u8]) -> Result<ParsedSkinPackage, String> {
    let cursor = Cursor::new(bytes);
    let mut archive =
        ZipArchive::new(cursor).map_err(|_| "upload is not a valid ZIP".to_string())?;
    let mut manifest: Option<SkinManifest> = None;
    let mut assets = Vec::new();
    let mut seen_entries = HashSet::new();
    let mut extracted_bytes = 0u64;
    let mut file_count = 0usize;

    for i in 0..archive.len() {
        let mut file = archive
            .by_index(i)
            .map_err(|_| "failed to read ZIP entry".to_string())?;

        if file.is_dir() {
            continue;
        }

        if file
            .unix_mode()
            .is_some_and(|mode| mode & 0o170_000 == 0o120_000)
        {
            return Err("ZIP symlinks are not allowed".to_string());
        }

        file_count += 1;
        if file_count > SKIN_MAX_FILE_COUNT {
            return Err(format!("skin ZIP exceeds {SKIN_MAX_FILE_COUNT} file limit"));
        }

        let enclosed = file
            .enclosed_name()
            .ok_or_else(|| "ZIP entry escapes skin root".to_string())?
            .to_path_buf();
        validate_package_path(&enclosed)?;
        let normalized = normalize_path_for_manifest(&enclosed)?;
        if !seen_entries.insert(normalized.clone()) {
            return Err(format!("duplicate ZIP entry: {normalized}"));
        }

        extracted_bytes = extracted_bytes
            .checked_add(file.size())
            .ok_or_else(|| "skin ZIP size overflow".to_string())?;
        if extracted_bytes > SKIN_MAX_EXTRACTED_BYTES {
            return Err("skin ZIP extracted contents exceed 50 MiB limit".to_string());
        }

        let mut content = Vec::with_capacity(file.size().min(1024 * 1024) as usize);
        file.read_to_end(&mut content)
            .map_err(|_| "failed to read ZIP entry".to_string())?;
        if content.len() as u64 != file.size() {
            return Err(format!("ZIP entry size mismatch: {normalized}"));
        }

        if normalized == MANIFEST_FILE {
            if manifest.is_some() {
                return Err("duplicate revka-skin.json manifest".to_string());
            }
            let raw = String::from_utf8(content)
                .map_err(|_| "revka-skin.json must be UTF-8 JSON".to_string())?;
            manifest = Some(validate_manifest(&raw)?);
            continue;
        }

        if enclosed
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name == MANIFEST_FILE)
        {
            return Err("revka-skin.json is only allowed at ZIP root".to_string());
        }

        let asset_path = validate_asset_file_path(&enclosed)?;
        assets.push((asset_path, content));
    }

    let manifest = manifest.ok_or_else(|| "missing root revka-skin.json".to_string())?;
    let available_assets = assets
        .iter()
        .filter_map(|(path, _)| normalize_path_for_manifest(path).ok())
        .collect::<HashSet<_>>();
    validate_manifest_asset_references(&manifest, &available_assets)?;

    Ok(ParsedSkinPackage { manifest, assets })
}

fn validate_package_path(path: &Path) -> Result<(), String> {
    let normalized = normalize_path_for_manifest(path)?;
    if normalized.contains('\\') {
        return Err("backslash path separators are not allowed".to_string());
    }
    let ext = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !PACKAGE_EXTENSIONS.contains(&ext.as_str()) {
        return Err(format!(
            "unsupported file extension in ZIP entry: {normalized}"
        ));
    }
    Ok(())
}

fn validate_asset_file_path(path: &Path) -> Result<PathBuf, String> {
    let mut components = path.components();
    match components.next() {
        Some(Component::Normal(name)) if name == ASSET_DIR => {}
        _ => return Err("skin assets must be stored under assets/".to_string()),
    }
    if components.clone().next().is_none() {
        return Err("asset path must include a file name".to_string());
    }

    let normalized = normalize_path_for_manifest(path)?;
    validate_asset_reference(&normalized)?;
    Ok(path.to_path_buf())
}

fn validate_manifest(raw: &str) -> Result<SkinManifest, String> {
    let value: serde_json::Value =
        serde_json::from_str(raw).map_err(|err| format!("invalid manifest JSON: {err}"))?;

    for required in ["schemaVersion", "id", "name", "version", "modes"] {
        if value.get(required).is_none() {
            return Err(format!("manifest missing required field `{required}`"));
        }
    }

    let modes = value
        .get("modes")
        .and_then(|modes| modes.as_object())
        .ok_or_else(|| "manifest modes must be an object".to_string())?;
    for key in modes.keys() {
        if key != "light" && key != "dark" {
            return Err(format!("unsupported skin mode `{key}`"));
        }
    }

    let manifest: SkinManifest =
        serde_json::from_value(value).map_err(|err| format!("invalid manifest shape: {err}"))?;
    if manifest.schema_version != 1 {
        return Err("schemaVersion must be 1".to_string());
    }
    if !valid_skin_id(&manifest.id) {
        return Err("skin id must use lowercase letters, numbers, '-' or '_'".to_string());
    }
    if manifest.name.trim().is_empty() {
        return Err("skin name must not be empty".to_string());
    }
    if manifest.version.trim().is_empty() {
        return Err("skin version must not be empty".to_string());
    }
    if manifest.modes.light.is_none() && manifest.modes.dark.is_none() {
        return Err("skin must define at least one of modes.light or modes.dark".to_string());
    }

    for (mode_name, mode) in [
        ("light", manifest.modes.light.as_ref()),
        ("dark", manifest.modes.dark.as_ref()),
    ] {
        if let Some(mode) = mode {
            for (token, value) in &mode.tokens {
                validate_token(mode_name, token, value)?;
            }
            for (slot, path) in &mode.assets {
                validate_asset_slot(slot)?;
                validate_asset_reference(path)?;
            }
            if let Some(preview) = mode.preview.as_deref() {
                validate_asset_reference(preview)?;
            }
        }
    }

    Ok(manifest)
}

fn validate_manifest_asset_references(
    manifest: &SkinManifest,
    available_assets: &HashSet<String>,
) -> Result<(), String> {
    for mode in [manifest.modes.light.as_ref(), manifest.modes.dark.as_ref()]
        .into_iter()
        .flatten()
    {
        for path in mode.assets.values() {
            if !available_assets.contains(path) {
                return Err(format!("manifest references missing asset `{path}`"));
            }
        }
        if let Some(preview) = mode.preview.as_deref()
            && !available_assets.contains(preview)
        {
            return Err(format!(
                "manifest references missing preview asset `{preview}`"
            ));
        }
    }
    Ok(())
}

fn validate_token(mode: &str, name: &str, value: &str) -> Result<(), String> {
    if !name.starts_with("--revka-") {
        return Err(format!(
            "{mode} token `{name}` must use the --revka-* namespace"
        ));
    }
    if name.starts_with("--pc-") {
        return Err(format!(
            "{mode} token `{name}` is not authorable; --pc-* is generated by compatibility bridge"
        ));
    }

    let lower_name = name.to_ascii_lowercase();
    let lower_value = value.to_ascii_lowercase();
    if lower_name.contains("shadow")
        || lower_name.contains("background-image")
        || lower_value.contains("url(")
        || lower_value.contains("javascript:")
        || lower_value.contains("expression(")
        || lower_value.contains("var(")
        || value.contains(['<', '>', ';'])
    {
        return Err(format!("{mode} token `{name}` uses a forbidden CSS value"));
    }

    if is_color_value(value) || is_bounded_length_value(value) {
        return Ok(());
    }

    Err(format!(
        "{mode} token `{name}` must be a hex/rgb/hsl color or bounded CSS length"
    ))
}

fn validate_asset_slot(slot: &str) -> Result<(), String> {
    if ASSET_SLOTS.contains(&slot) {
        Ok(())
    } else {
        Err(format!("unsupported asset slot `{slot}`"))
    }
}

fn validate_asset_reference(path: &str) -> Result<(), String> {
    if path.is_empty() {
        return Err("asset path must not be empty".to_string());
    }
    if path.starts_with("http://") || path.starts_with("https://") || path.starts_with("//") {
        return Err(format!("remote asset URLs are not allowed: {path}"));
    }
    if path.starts_with('/') || path.starts_with('\\') || path.contains('\\') {
        return Err(format!("asset path must be relative under assets/: {path}"));
    }
    if path.split('/').any(|part| part == "..") {
        return Err(format!("asset path traversal is not allowed: {path}"));
    }
    if !path.starts_with("assets/") {
        return Err(format!("asset path must start with assets/: {path}"));
    }

    let ext = Path::new(path)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    if !ASSET_EXTENSIONS.contains(&ext.as_str()) {
        return Err(format!("unsupported asset extension: {path}"));
    }
    Ok(())
}

fn safe_resolve_existing_file(root: &Path, rel: &str) -> Result<PathBuf, &'static str> {
    validate_relative_path(rel)?;
    let canonical_root = root.canonicalize().map_err(|_| "not found")?;
    let canonical = canonical_root
        .join(rel)
        .canonicalize()
        .map_err(|_| "not found")?;
    if !canonical.starts_with(&canonical_root) {
        return Err("path escapes skin asset root");
    }
    if !canonical.is_file() {
        return Err("not found");
    }
    Ok(canonical)
}

fn validate_relative_path(path: &str) -> Result<(), &'static str> {
    if path.is_empty() || path.starts_with('/') || path.starts_with('\\') || path.contains('\\') {
        return Err("invalid relative path");
    }
    for part in path.split('/') {
        if part == ".." || part.is_empty() {
            return Err("path traversal not allowed");
        }
    }
    Ok(())
}

fn normalize_path_for_manifest(path: &Path) -> Result<String, String> {
    let mut parts = Vec::new();
    for component in path.components() {
        match component {
            Component::Normal(part) => parts.push(
                part.to_str()
                    .ok_or_else(|| "ZIP path must be UTF-8".to_string())?
                    .to_string(),
            ),
            Component::CurDir => {}
            Component::ParentDir => return Err("ZIP path traversal is not allowed".to_string()),
            Component::Prefix(_) | Component::RootDir => {
                return Err("absolute ZIP paths are not allowed".to_string());
            }
        }
    }
    if parts.is_empty() {
        return Err("empty ZIP path is not allowed".to_string());
    }
    Ok(parts.join("/"))
}

fn valid_skin_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-' || b == b'_')
}

fn is_color_value(value: &str) -> bool {
    let trimmed = value.trim();
    if let Some(hex) = trimmed.strip_prefix('#') {
        return matches!(hex.len(), 3 | 4 | 6 | 8) && hex.chars().all(|c| c.is_ascii_hexdigit());
    }

    for prefix in ["rgb(", "rgba(", "hsl(", "hsla("] {
        if let Some(inner) = trimmed
            .strip_prefix(prefix)
            .and_then(|value| value.strip_suffix(')'))
        {
            return !inner.is_empty()
                && inner.len() <= 96
                && inner.chars().all(|c| {
                    c.is_ascii_digit()
                        || c.is_ascii_whitespace()
                        || matches!(c, ',' | '.' | '%' | '-' | '+' | '/')
                        || matches!(c, 'd' | 'e' | 'g' | 'r' | 'a')
                });
        }
    }

    false
}

fn is_bounded_length_value(value: &str) -> bool {
    let trimmed = value.trim();
    if trimmed == "0" {
        return true;
    }
    let Some(unit) = ["px", "rem", "em", "%"]
        .iter()
        .find(|unit| trimmed.ends_with(**unit))
    else {
        return false;
    };
    let number = &trimmed[..trimmed.len() - unit.len()];
    let Ok(parsed) = number.parse::<f32>() else {
        return false;
    };
    parsed.is_finite()
        && parsed >= 0.0
        && if *unit == "%" {
            parsed <= 100.0
        } else {
            parsed <= 256.0
        }
}

fn json_error(status: StatusCode, message: &str) -> Response {
    (status, Json(serde_json::json!({ "error": message }))).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manifest_rejects_pc_token_authoring() {
        let raw = r##"{
            "schemaVersion": 1,
            "id": "rabbit_garden",
            "name": "Rabbit Garden",
            "version": "1.0.0",
            "modes": {
                "light": {
                    "tokens": {
                        "--pc-accent": "#ffffff"
                    }
                }
            }
        }"##;

        let err = validate_manifest(raw).expect_err("pc token should be rejected");
        assert!(err.contains("--revka-*"));
    }

    #[test]
    fn manifest_accepts_revka_tokens_and_asset_slots() {
        let raw = r##"{
            "schemaVersion": 1,
            "id": "noir_rose",
            "name": "Noir Rose",
            "version": "1.0.0",
            "modes": {
                "dark": {
                    "tokens": {
                        "--revka-bg-base": "#080506",
                        "--revka-radius-md": "14px"
                    },
                    "assets": {
                        "brandLogo": "assets/logo.webp",
                        "dashboardShowcase": "assets/showcase.webp",
                        "statusSuccessBadge": "assets/success.webp"
                    },
                    "preview": "assets/preview.png"
                }
            }
        }"##;

        let manifest = validate_manifest(raw).expect("valid manifest");
        assert_eq!(manifest.id, "noir_rose");
    }

    #[test]
    fn builtin_revka_skin_is_available() {
        let summary = builtin_revka_skin_summary();
        let dark = summary
            .manifest
            .modes
            .dark
            .as_ref()
            .expect("revka dark mode");
        let light = summary
            .manifest
            .modes
            .light
            .as_ref()
            .expect("revka light mode");

        assert_eq!(summary.id, BUILTIN_REVKA_SKIN_ID);
        assert!(summary.builtin);
        assert_eq!(
            dark.tokens.get("--revka-bg-base"),
            Some(&"#0b0d10".to_string())
        );
        assert_eq!(
            light.tokens.get("--revka-signal-live"),
            Some(&"#4f75a8".to_string())
        );
        assert_eq!(
            light.tokens.get("--revka-radius-md"),
            Some(&"8px".to_string())
        );
    }

    #[test]
    fn builtin_matrix_skin_is_available() {
        let summary = builtin_matrix_skin_summary();
        let dark = summary
            .manifest
            .modes
            .dark
            .as_ref()
            .expect("matrix dark mode");
        let light = summary
            .manifest
            .modes
            .light
            .as_ref()
            .expect("matrix light mode");

        assert_eq!(summary.id, BUILTIN_MATRIX_SKIN_ID);
        assert!(summary.builtin);
        assert_eq!(
            dark.tokens.get("--revka-bg-base"),
            Some(&"#05080a".to_string())
        );
        assert_eq!(
            light.tokens.get("--revka-signal-live"),
            Some(&"#3faf68".to_string())
        );
    }

    #[test]
    fn builtin_skin_ids_are_reserved() {
        assert!(is_builtin_skin_id("revka"));
        assert!(is_builtin_skin_id("matrix"));
        assert!(!is_builtin_skin_id("uploaded"));
    }

    #[test]
    fn imported_skin_summary_is_not_builtin() {
        let manifest = SkinManifest {
            schema_version: 1,
            id: "uploaded".to_string(),
            name: "Uploaded".to_string(),
            version: "1.0.0".to_string(),
            modes: SkinModes {
                light: None,
                dark: Some(SkinMode::default()),
            },
        };

        let summary = skin_summary(manifest);
        assert!(!summary.builtin);
    }

    #[test]
    fn asset_reference_rejects_svg_and_remote_urls() {
        assert!(validate_asset_reference("assets/logo.svg").is_err());
        assert!(validate_asset_reference("https://example.com/logo.png").is_err());
        assert!(validate_asset_reference("../logo.png").is_err());
        assert!(validate_asset_reference("assets/logo.png").is_ok());
    }
}
