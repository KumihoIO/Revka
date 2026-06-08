//! Device management and pairing API handlers.

use super::{AppState, client_key_from_request};
use axum::{
    extract::{ConnectInfo, State},
    http::{HeaderMap, StatusCode, header},
    response::{IntoResponse, Json},
};
use chrono::{DateTime, Utc};
use parking_lot::Mutex;
use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use tracing::{debug, error, info, warn};

const DEVICE_METADATA_MAX_CHARS: usize = 120;
const DEVICE_NAME_HEADERS: &[&str] = &["X-Revka-Device-Name", "X-Device-Name"];
const DEVICE_TYPE_HEADERS: &[&str] = &["X-Revka-Device-Type", "X-Device-Type"];
const DEVICE_HARDWARE_HEADERS: &[&str] = &["X-Revka-Device-Hardware", "X-Device-Hardware"];

/// Metadata about a paired device.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceInfo {
    pub id: String,
    pub name: Option<String>,
    pub device_type: Option<String>,
    pub hardware: Option<String>,
    pub paired_at: DateTime<Utc>,
    pub last_seen: DateTime<Utc>,
    pub ip_address: Option<String>,
}

#[derive(Debug, Clone, Default)]
pub(crate) struct DeviceMetadata {
    pub name: Option<String>,
    pub device_type: Option<String>,
    pub hardware: Option<String>,
}

impl DeviceMetadata {
    pub(crate) fn from_headers_or_user_agent(headers: &HeaderMap) -> Self {
        let inferred = infer_device_metadata(headers);
        Self {
            name: first_metadata_header(headers, DEVICE_NAME_HEADERS)
                .or(inferred.name)
                .or_else(|| Some("API client".to_string())),
            device_type: first_metadata_header(headers, DEVICE_TYPE_HEADERS)
                .or(inferred.device_type)
                .or_else(|| Some("api-client".to_string())),
            hardware: first_metadata_header(headers, DEVICE_HARDWARE_HEADERS).or(inferred.hardware),
        }
    }

    fn from_submit_request(body: &SubmitPairingRequest, headers: &HeaderMap) -> Self {
        let fallback = Self::from_headers_or_user_agent(headers);
        Self {
            name: body
                .device_name
                .as_deref()
                .and_then(sanitize_metadata_value)
                .or(fallback.name),
            device_type: body
                .device_type
                .as_deref()
                .and_then(sanitize_metadata_value)
                .or(fallback.device_type),
            hardware: body
                .hardware
                .as_deref()
                .and_then(sanitize_metadata_value)
                .or(fallback.hardware),
        }
    }
}

#[derive(Debug, Deserialize)]
pub struct SubmitPairingRequest {
    code: String,
    device_name: Option<String>,
    device_type: Option<String>,
    hardware: Option<String>,
}

fn first_metadata_header(headers: &HeaderMap, names: &[&str]) -> Option<String> {
    names.iter().find_map(|name| {
        headers
            .get(*name)
            .and_then(|value| value.to_str().ok())
            .and_then(sanitize_metadata_value)
    })
}

fn sanitize_metadata_value(value: &str) -> Option<String> {
    let trimmed = value.trim();
    if trimmed.is_empty() {
        return None;
    }

    Some(trimmed.chars().take(DEVICE_METADATA_MAX_CHARS).collect())
}

fn infer_device_metadata(headers: &HeaderMap) -> DeviceMetadata {
    let Some(user_agent) = headers
        .get(header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
    else {
        return DeviceMetadata::default();
    };
    let lower = user_agent.to_ascii_lowercase();

    if lower.contains("revka-cli") || lower.starts_with("revka/") {
        return DeviceMetadata {
            name: Some("Revka CLI".to_string()),
            device_type: Some("cli".to_string()),
            hardware: None,
        };
    }

    if lower.contains("iphone") {
        return DeviceMetadata {
            name: Some("iPhone".to_string()),
            device_type: Some("mobile".to_string()),
            hardware: Some("iOS".to_string()),
        };
    }

    if lower.contains("ipad") {
        return DeviceMetadata {
            name: Some("iPad".to_string()),
            device_type: Some("tablet".to_string()),
            hardware: Some("iPadOS".to_string()),
        };
    }

    if lower.contains("android") {
        return DeviceMetadata {
            name: Some("Android device".to_string()),
            device_type: Some("mobile".to_string()),
            hardware: Some("Android".to_string()),
        };
    }

    if lower.contains("windows") {
        return DeviceMetadata {
            name: Some("Windows browser".to_string()),
            device_type: Some("browser".to_string()),
            hardware: Some("Windows".to_string()),
        };
    }

    if lower.contains("mac os") || lower.contains("macintosh") {
        return DeviceMetadata {
            name: Some("macOS browser".to_string()),
            device_type: Some("browser".to_string()),
            hardware: Some("macOS".to_string()),
        };
    }

    if lower.contains("linux") {
        return DeviceMetadata {
            name: Some("Linux browser".to_string()),
            device_type: Some("browser".to_string()),
            hardware: Some("Linux".to_string()),
        };
    }

    DeviceMetadata {
        name: Some("Browser client".to_string()),
        device_type: Some("browser".to_string()),
        hardware: None,
    }
}

/// Registry of paired devices backed by SQLite.
#[derive(Debug)]
pub struct DeviceRegistry {
    cache: Mutex<HashMap<String, DeviceInfo>>,
    db_path: PathBuf,
}

impl DeviceRegistry {
    /// Open (or create) the SQLite-backed device registry at
    /// `<workspace_dir>/devices.db`. Returns an error rather than panicking
    /// when the DB is locked or corrupt — a transient I/O failure at startup
    /// or on a request path must not take the gateway down.
    pub fn new(workspace_dir: &Path) -> anyhow::Result<Self> {
        use anyhow::Context;

        let db_path = workspace_dir.join("devices.db");
        let conn = Connection::open(&db_path)
            .with_context(|| format!("open device registry DB at {}", db_path.display()))?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS devices (
                token_hash TEXT PRIMARY KEY,
                id TEXT NOT NULL,
                name TEXT,
                device_type TEXT,
                hardware TEXT,
                paired_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                ip_address TEXT
            )",
        )
        .context("create devices table")?;
        ensure_device_registry_columns(&conn)?;

        let mut cache = HashMap::new();
        let mut stmt = conn
            .prepare("SELECT token_hash, id, name, device_type, hardware, paired_at, last_seen, ip_address FROM devices")
            .context("prepare device select")?;
        let rows = stmt
            .query_map([], |row| {
                let token_hash: String = row.get(0)?;
                let id: String = row.get(1)?;
                let name: Option<String> = row.get(2)?;
                let device_type: Option<String> = row.get(3)?;
                let hardware: Option<String> = row.get(4)?;
                let paired_at_str: String = row.get(5)?;
                let last_seen_str: String = row.get(6)?;
                let ip_address: Option<String> = row.get(7)?;
                let paired_at = DateTime::parse_from_rfc3339(&paired_at_str)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now());
                let last_seen = DateTime::parse_from_rfc3339(&last_seen_str)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or_else(|_| Utc::now());
                Ok((
                    token_hash,
                    DeviceInfo {
                        id,
                        name,
                        device_type,
                        hardware,
                        paired_at,
                        last_seen,
                        ip_address,
                    },
                ))
            })
            .context("query devices")?;
        for (hash, info) in rows.flatten() {
            cache.insert(hash, info);
        }

        Ok(Self {
            cache: Mutex::new(cache),
            db_path,
        })
    }

    fn open_db(&self) -> anyhow::Result<Connection> {
        use anyhow::Context;
        Connection::open(&self.db_path)
            .with_context(|| format!("open device registry DB at {}", self.db_path.display()))
    }

    pub fn register(&self, token_hash: String, info: DeviceInfo) -> anyhow::Result<()> {
        use anyhow::Context;
        let conn = self.open_db()?;
        let device_id = info.id.clone();
        conn.execute(
            "INSERT OR REPLACE INTO devices (token_hash, id, name, device_type, hardware, paired_at, last_seen, ip_address) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            rusqlite::params![
                token_hash,
                info.id,
                info.name,
                info.device_type,
                info.hardware,
                info.paired_at.to_rfc3339(),
                info.last_seen.to_rfc3339(),
                info.ip_address,
            ],
        )
        .context("insert device row")?;
        let hash_prefix: String = token_hash.chars().take(8).collect();
        self.cache.lock().insert(token_hash, info);
        info!(device_id = %device_id, token_hash_prefix = %hash_prefix, "device registered in SQLite");
        Ok(())
    }

    pub fn list(&self) -> Vec<DeviceInfo> {
        // Persistence failures here degrade to an empty list rather than
        // panicking: the caller is a request handler, not startup code.
        let conn = match self.open_db() {
            Ok(c) => c,
            Err(e) => {
                warn!(error = %e, "device registry list: open_db failed — returning empty list");
                return Vec::new();
            }
        };
        let mut stmt = match conn.prepare(
            "SELECT token_hash, id, name, device_type, hardware, paired_at, last_seen, ip_address FROM devices",
        ) {
            Ok(s) => s,
            Err(e) => {
                warn!(error = %e, "device registry list: prepare failed — returning empty list");
                return Vec::new();
            }
        };
        let rows = match stmt.query_map([], |row| {
            let id: String = row.get(1)?;
            let name: Option<String> = row.get(2)?;
            let device_type: Option<String> = row.get(3)?;
            let hardware: Option<String> = row.get(4)?;
            let paired_at_str: String = row.get(5)?;
            let last_seen_str: String = row.get(6)?;
            let ip_address: Option<String> = row.get(7)?;
            let paired_at = DateTime::parse_from_rfc3339(&paired_at_str)
                .map(|dt| dt.with_timezone(&Utc))
                .unwrap_or_else(|_| Utc::now());
            let last_seen = DateTime::parse_from_rfc3339(&last_seen_str)
                .map(|dt| dt.with_timezone(&Utc))
                .unwrap_or_else(|_| Utc::now());
            Ok(DeviceInfo {
                id,
                name,
                device_type,
                hardware,
                paired_at,
                last_seen,
                ip_address,
            })
        }) {
            Ok(r) => r,
            Err(e) => {
                warn!(error = %e, "device registry list: query_map failed — returning empty list");
                return Vec::new();
            }
        };
        rows.filter_map(|r| r.ok()).collect()
    }

    pub fn revoke(&self, device_id: &str) -> bool {
        let conn = match self.open_db() {
            Ok(c) => c,
            Err(e) => {
                warn!(error = %e, "device registry revoke: open_db failed");
                return false;
            }
        };
        let deleted = conn
            .execute(
                "DELETE FROM devices WHERE id = ?1",
                rusqlite::params![device_id],
            )
            .unwrap_or(0);
        if deleted > 0 {
            let mut cache = self.cache.lock();
            let key = cache
                .iter()
                .find(|(_, v)| v.id == device_id)
                .map(|(k, _)| k.clone());
            if let Some(key) = key {
                cache.remove(&key);
            }
            true
        } else {
            false
        }
    }

    pub fn update_last_seen(&self, token_hash: &str) {
        let now = Utc::now();
        if let Ok(conn) = self.open_db() {
            let _ = conn.execute(
                "UPDATE devices SET last_seen = ?1 WHERE token_hash = ?2",
                rusqlite::params![now.to_rfc3339(), token_hash],
            );
        }
        if let Some(device) = self.cache.lock().get_mut(token_hash) {
            device.last_seen = now;
        }
    }

    pub fn device_count(&self) -> usize {
        self.cache.lock().len()
    }

    /// Return the paired bearer-token hashes recorded for devices.
    ///
    /// The gateway accepts hashed tokens in `PairingGuard`, so this lets
    /// restart-time auth hydrate from the same device registry the dashboard
    /// pairing flow writes to.
    pub fn token_hashes(&self) -> Vec<String> {
        self.cache.lock().keys().cloned().collect()
    }
}

fn ensure_device_registry_columns(conn: &Connection) -> anyhow::Result<()> {
    use anyhow::Context;

    let mut stmt = conn
        .prepare("PRAGMA table_info(devices)")
        .context("prepare devices table info")?;
    let columns = stmt
        .query_map([], |row| row.get::<_, String>(1))
        .context("query devices table info")?;

    let mut has_hardware = false;
    for column in columns {
        if column.context("read devices column")? == "hardware" {
            has_hardware = true;
            break;
        }
    }

    if !has_hardware {
        conn.execute("ALTER TABLE devices ADD COLUMN hardware TEXT", [])
            .context("add devices.hardware column")?;
    }

    Ok(())
}

/// Store for pending pairing requests.
#[derive(Debug)]
pub struct PairingStore {
    pending: Mutex<Vec<PendingPairing>>,
    max_pending: usize,
}

#[derive(Debug, Clone, Serialize)]
struct PendingPairing {
    code: String,
    created_at: DateTime<Utc>,
    expires_at: DateTime<Utc>,
    client_ip: Option<String>,
    attempts: u32,
}

impl PairingStore {
    pub fn new(max_pending: usize) -> Self {
        Self {
            pending: Mutex::new(Vec::new()),
            max_pending,
        }
    }

    pub fn pending_count(&self) -> usize {
        let mut pending = self.pending.lock();
        pending.retain(|p| p.expires_at > Utc::now());
        pending.len()
    }
}

fn extract_bearer(headers: &HeaderMap) -> Option<&str> {
    headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|auth| auth.strip_prefix("Bearer "))
}

fn require_auth(state: &AppState, headers: &HeaderMap) -> Result<(), (StatusCode, &'static str)> {
    if state.pairing.require_pairing() {
        let token = extract_bearer(headers).unwrap_or("");
        if !state.pairing.is_authenticated(token) {
            return Err((StatusCode::UNAUTHORIZED, "Unauthorized"));
        }
    }
    Ok(())
}

/// POST /api/pairing/initiate — initiate a new pairing session
pub async fn initiate_pairing(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        warn!("initiate_pairing: unauthorized request");
        return e.into_response();
    }

    info!("initiate_pairing: generating new pairing code");
    match state.pairing.generate_new_pairing_code() {
        Some(code) => {
            let code_prefix: String = code.chars().take(2).collect();
            info!(code_prefix = %code_prefix, "initiate_pairing: code generated");
            Json(serde_json::json!({
                "pairing_code": code,
                "message": "New pairing code generated"
            }))
            .into_response()
        }
        None => {
            warn!("initiate_pairing: pairing disabled or unavailable");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                "Pairing is disabled or not available",
            )
                .into_response()
        }
    }
}

/// POST /api/pair — submit pairing code (for new device pairing)
pub async fn submit_pairing_enhanced(
    State(state): State<AppState>,
    ConnectInfo(peer_addr): ConnectInfo<SocketAddr>,
    headers: HeaderMap,
    Json(body): Json<SubmitPairingRequest>,
) -> impl IntoResponse {
    let code = body.code.trim();
    let metadata = DeviceMetadata::from_submit_request(&body, &headers);

    // Use the shared client-key helper. This ignores unsolicited
    // `X-Forwarded-For` values unless `trust_forwarded_headers` is set, so
    // attackers cannot bypass the per-client lockout by rotating the header.
    let client_id =
        client_key_from_request(Some(peer_addr), &headers, state.trust_forwarded_headers);

    info!(
        client_id = %client_id,
        code_len = code.len(),
        device_name = ?metadata.name,
        device_type = ?metadata.device_type,
        hardware = ?metadata.hardware,
        "submit_pairing_enhanced: received pair request"
    );

    match state.pairing.try_pair(code, &client_id).await {
        Ok(Some(token)) => {
            // Register the new device
            let token_hash = {
                use sha2::{Digest, Sha256};
                let hash = Sha256::digest(token.as_bytes());
                hex::encode(hash)
            };
            let hash_prefix: String = token_hash.chars().take(8).collect();
            info!(
                client_id = %client_id,
                token_hash_prefix = %hash_prefix,
                "submit_pairing_enhanced: pairing succeeded, registering device"
            );
            if let Some(ref registry) = state.device_registry {
                if let Err(e) = registry.register(
                    token_hash,
                    DeviceInfo {
                        id: uuid::Uuid::new_v4().to_string(),
                        name: metadata.name,
                        device_type: metadata.device_type,
                        hardware: metadata.hardware,
                        paired_at: Utc::now(),
                        last_seen: Utc::now(),
                        ip_address: Some(client_id.clone()),
                    },
                ) {
                    error!(
                        client_id = %client_id,
                        error = %e,
                        "submit_pairing_enhanced: device registry insert failed"
                    );
                    return (
                        StatusCode::INTERNAL_SERVER_ERROR,
                        "Pairing succeeded but device registration failed",
                    )
                        .into_response();
                }
            } else {
                debug!("submit_pairing_enhanced: no device_registry configured; skipping persist");
            }

            if let Err(e) = Box::pin(super::persist_pairing_tokens(
                state.config.clone(),
                &state.pairing,
            ))
            .await
            {
                error!(
                    error = %e,
                    "submit_pairing_enhanced: pairing succeeded but token persistence failed"
                );
                return Json(serde_json::json!({
                    "token": token,
                    "persisted": false,
                    "message": "Pairing successful for this process, but token persistence failed"
                }))
                .into_response();
            }

            Json(serde_json::json!({
                "token": token,
                "persisted": true,
                "message": "Pairing successful"
            }))
            .into_response()
        }
        Ok(None) => {
            warn!(client_id = %client_id, "submit_pairing_enhanced: invalid or expired code");
            (StatusCode::BAD_REQUEST, "Invalid or expired pairing code").into_response()
        }
        Err(lockout_secs) => {
            warn!(
                client_id = %client_id,
                lockout_secs,
                "submit_pairing_enhanced: client locked out"
            );
            (
                StatusCode::TOO_MANY_REQUESTS,
                format!("Too many attempts. Locked out for {lockout_secs}s"),
            )
                .into_response()
        }
    }
}

/// GET /api/devices — list paired devices
pub async fn list_devices(State(state): State<AppState>, headers: HeaderMap) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let devices = state
        .device_registry
        .as_ref()
        .map(|r| r.list())
        .unwrap_or_default();

    let count = devices.len();
    Json(serde_json::json!({
        "devices": devices,
        "count": count
    }))
    .into_response()
}

/// DELETE /api/devices/{id} — revoke a paired device
pub async fn revoke_device(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Path(device_id): axum::extract::Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    let revoked = state
        .device_registry
        .as_ref()
        .map(|r| r.revoke(&device_id))
        .unwrap_or(false);

    if revoked {
        Json(serde_json::json!({
            "message": "Device revoked",
            "device_id": device_id
        }))
        .into_response()
    } else {
        (StatusCode::NOT_FOUND, "Device not found").into_response()
    }
}

/// POST /api/devices/{id}/token/rotate — rotate a device's token
pub async fn rotate_token(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Path(device_id): axum::extract::Path<String>,
) -> impl IntoResponse {
    if let Err(e) = require_auth(&state, &headers) {
        return e.into_response();
    }

    // Generate a new pairing code for re-pairing
    match state.pairing.generate_new_pairing_code() {
        Some(code) => Json(serde_json::json!({
            "device_id": device_id,
            "pairing_code": code,
            "message": "Use this code to re-pair the device"
        }))
        .into_response(),
        None => (
            StatusCode::SERVICE_UNAVAILABLE,
            "Cannot generate new pairing code",
        )
            .into_response(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::{HeaderMap, HeaderValue};
    use tempfile::tempdir;

    #[test]
    fn metadata_headers_override_user_agent_inference() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "X-Revka-Device-Name",
            HeaderValue::from_static("Pixel field kit"),
        );
        headers.insert("X-Revka-Device-Type", HeaderValue::from_static("mobile"));
        headers.insert(
            "X-Revka-Device-Hardware",
            HeaderValue::from_static("Pixel 8 / Android"),
        );
        headers.insert(
            header::USER_AGENT,
            HeaderValue::from_static("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
        );

        let metadata = DeviceMetadata::from_headers_or_user_agent(&headers);

        assert_eq!(metadata.name.as_deref(), Some("Pixel field kit"));
        assert_eq!(metadata.device_type.as_deref(), Some("mobile"));
        assert_eq!(metadata.hardware.as_deref(), Some("Pixel 8 / Android"));
    }

    #[test]
    fn metadata_infers_mobile_from_user_agent() {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::USER_AGENT,
            HeaderValue::from_static("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"),
        );

        let metadata = DeviceMetadata::from_headers_or_user_agent(&headers);

        assert_eq!(metadata.name.as_deref(), Some("iPhone"));
        assert_eq!(metadata.device_type.as_deref(), Some("mobile"));
        assert_eq!(metadata.hardware.as_deref(), Some("iOS"));
    }

    #[test]
    fn registry_migrates_legacy_device_table_and_persists_hardware() {
        let dir = tempdir().expect("tempdir");
        let db_path = dir.path().join("devices.db");
        let conn = Connection::open(&db_path).expect("open legacy db");
        conn.execute_batch(
            "CREATE TABLE devices (
                token_hash TEXT PRIMARY KEY,
                id TEXT NOT NULL,
                name TEXT,
                device_type TEXT,
                paired_at TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                ip_address TEXT
            );",
        )
        .expect("create legacy devices table");

        let now = Utc::now().to_rfc3339();
        conn.execute(
            "INSERT INTO devices (token_hash, id, name, device_type, paired_at, last_seen, ip_address)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            rusqlite::params![
                "hash-a",
                "device-a",
                "Old client",
                "legacy-pair",
                now,
                now,
                "127.0.0.1",
            ],
        )
        .expect("insert legacy row");
        drop(conn);

        let registry = DeviceRegistry::new(dir.path()).expect("open migrated registry");
        let legacy = registry.list();
        assert_eq!(legacy.len(), 1);
        assert_eq!(legacy[0].hardware, None);

        registry
            .register(
                "hash-b".to_string(),
                DeviceInfo {
                    id: "device-b".to_string(),
                    name: Some("Field phone".to_string()),
                    device_type: Some("mobile".to_string()),
                    hardware: Some("Pixel 8 / Android".to_string()),
                    paired_at: Utc::now(),
                    last_seen: Utc::now(),
                    ip_address: Some("127.0.0.1".to_string()),
                },
            )
            .expect("register device with hardware");

        let devices = registry.list();
        let registered = devices
            .iter()
            .find(|device| device.id == "device-b")
            .expect("registered device");
        assert_eq!(registered.hardware.as_deref(), Some("Pixel 8 / Android"));
    }
}
