//! HTTP client for the Kumiho FastAPI REST API.
//!
//! Wraps `reqwest` calls to the Kumiho service, providing typed methods for
//! item CRUD, revisions, search, and space management.  Used by the agent
//! management API routes (`/api/agents`) and skill management routes
//! (`/api/skills`).

use crate::config::Config;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Build a `KumihoClient` from the top-level `Config`.
///
/// Reads `kumiho.api_url` for the base URL and `KUMIHO_SERVICE_TOKEN` env var
/// for the service token. Used by CLI commands (`construct memory`,
/// `construct migrate openclaw`) that need a Kumiho client without an
/// `AppState`.
pub fn build_client_from_config(config: &Config) -> KumihoClient {
    let base_url = config.kumiho.api_url.clone();
    let service_token = std::env::var("KUMIHO_SERVICE_TOKEN").unwrap_or_default();
    KumihoClient::new(base_url, service_token)
}

/// Convert a human-readable name to a kref-safe slug (lowercase, hyphens, no spaces).
pub fn slugify(name: &str) -> String {
    name.trim()
        .to_lowercase()
        .chars()
        .map(|c| {
            if c.is_alphanumeric() || c == '-' {
                c
            } else {
                '-'
            }
        })
        .collect::<String>()
        .split('-')
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("-")
}

/// Kumiho FastAPI client.
#[derive(Clone)]
pub struct KumihoClient {
    client: Client,
    base_url: String,
    service_token: String,
}

// ── Response types (match Kumiho FastAPI JSON) ──────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ItemResponse {
    pub kref: String,
    pub name: String,
    pub item_name: String,
    pub kind: String,
    #[serde(default)]
    pub deprecated: bool,
    pub created_at: Option<String>,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RevisionResponse {
    pub kref: String,
    pub item_kref: String,
    pub number: i32,
    #[serde(default)]
    pub latest: bool,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
    #[serde(default)]
    pub deprecated: bool,
    pub created_at: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BatchRevisionsResponse {
    pub revisions: Vec<RevisionResponse>,
    pub not_found: Vec<String>,
    pub requested_count: i32,
    pub found_count: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub item: ItemResponse,
    #[serde(default)]
    pub score: f64,
}

// ── Bundle response types ────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BundleMemberInfo {
    pub item_kref: String,
    pub added_at: Option<String>,
    pub added_by: Option<String>,
    pub added_in_revision: Option<i32>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BundleMembersResponse {
    pub members: Vec<BundleMemberInfo>,
    pub total_count: Option<i32>,
}

// ── Artifact response types ────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ArtifactResponse {
    pub kref: String,
    pub name: String,
    pub location: String,
    pub revision_kref: String,
    pub item_kref: Option<String>,
    #[serde(default)]
    pub deprecated: bool,
    pub created_at: Option<String>,
    #[serde(default)]
    pub metadata: HashMap<String, String>,
}

// ── Edge response types ─────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EdgeResponse {
    pub source_kref: String,
    pub target_kref: String,
    pub edge_type: String,
    pub created_at: Option<String>,
    #[serde(default)]
    pub metadata: Option<HashMap<String, String>>,
}

// ── Space response types ───────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpaceResponse {
    pub path: String,
    pub name: String,
    pub parent_path: Option<String>,
    pub created_at: Option<String>,
}

// ── Error type ──────────────────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub enum KumihoError {
    #[error("Kumiho service unreachable: {0}")]
    Unreachable(#[from] reqwest::Error),

    #[error("Kumiho returned {status}: {body}")]
    Api { status: u16, body: String },

    #[error("Unexpected response: {0}")]
    Decode(String),
}

pub type Result<T> = std::result::Result<T, KumihoError>;

// ── Request body types ──────────────────────────────────────────────────

#[derive(Serialize)]
struct CreateProjectBody {
    name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    description: Option<String>,
}

#[derive(Serialize)]
struct CreateSpaceBody {
    parent_path: String,
    name: String,
}

#[derive(Serialize)]
struct CreateItemBody {
    space_path: String,
    item_name: String,
    kind: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

#[derive(Serialize)]
struct CreateRevisionBody {
    item_kref: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

#[derive(Serialize)]
struct CreateBundleBody {
    space_path: String,
    bundle_name: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

#[derive(Serialize)]
struct BundleMemberBody {
    bundle_kref: String,
    item_kref: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    metadata: Option<HashMap<String, String>>,
}

#[derive(Serialize)]
struct RemoveBundleMemberBody {
    bundle_kref: String,
    item_kref: String,
}

#[derive(Serialize)]
struct CreateEdgeBody {
    source_revision_kref: String,
    target_revision_kref: String,
    edge_type: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

#[derive(Serialize)]
struct CreateArtifactBody {
    revision_kref: String,
    name: String,
    location: String,
    #[serde(skip_serializing_if = "HashMap::is_empty")]
    metadata: HashMap<String, String>,
}

impl KumihoClient {
    /// Create a new Kumiho client.
    ///
    /// `service_token` is sent as `X-Kumiho-Token` on every request.
    pub fn new(base_url: String, service_token: String) -> Self {
        let client = Client::builder()
            .timeout(std::time::Duration::from_secs(20))
            .connect_timeout(std::time::Duration::from_secs(5))
            .pool_max_idle_per_host(32)
            .build()
            .unwrap_or_else(|_| Client::new());
        Self {
            client,
            base_url: base_url.trim_end_matches('/').to_string(),
            service_token,
        }
    }

    /// Access the inner HTTP client (for proxy use).
    pub fn client(&self) -> &Client {
        &self.client
    }

    // ── Helpers ─────────────────────────────────────────────────────

    fn url(&self, path: &str) -> String {
        format!("{}/api/v1{}", self.base_url, path)
    }

    async fn check_response(&self, resp: reqwest::Response) -> Result<reqwest::Response> {
        let status = resp.status();
        if status.is_success() {
            Ok(resp)
        } else {
            let code = status.as_u16();
            let body = resp.text().await.unwrap_or_default();
            Err(KumihoError::Api { status: code, body })
        }
    }

    // ── Project management ─────────────────────────────────────────

    /// Ensure a project exists (idempotent).  Ignores 409 Conflict (already exists).
    pub async fn ensure_project(&self, project_name: &str) -> Result<()> {
        let body = CreateProjectBody {
            name: project_name.to_string(),
            description: None,
        };

        let resp = self
            .client
            .post(self.url("/projects"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let status = resp.status().as_u16();
        if resp.status().is_success() || status == 409 {
            Ok(())
        } else {
            let text = resp.text().await.unwrap_or_default();
            Err(KumihoError::Api { status, body: text })
        }
    }

    // ── Space management ────────────────────────────────────────────

    /// Ensure a space exists (idempotent).  Ignores 409 Conflict (already exists).
    pub async fn ensure_space(&self, project: &str, space_name: &str) -> Result<()> {
        let body = CreateSpaceBody {
            parent_path: format!("/{project}"),
            name: space_name.to_string(),
        };

        let resp = self
            .client
            .post(self.url("/spaces"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let status = resp.status().as_u16();
        // 409 = already exists — that's fine
        if resp.status().is_success() || status == 409 {
            Ok(())
        } else {
            let text = resp.text().await.unwrap_or_default();
            Err(KumihoError::Api { status, body: text })
        }
    }

    /// Ensure a nested space exists under a parent (idempotent).
    pub async fn ensure_child_space(
        &self,
        _project: &str,
        parent_path: &str,
        space_name: &str,
    ) -> Result<()> {
        let body = CreateSpaceBody {
            parent_path: parent_path.to_string(),
            name: space_name.to_string(),
        };

        let resp = self
            .client
            .post(self.url("/spaces"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let status = resp.status().as_u16();
        if resp.status().is_success() || status == 409 {
            Ok(())
        } else {
            let text = resp.text().await.unwrap_or_default();
            Err(KumihoError::Api { status, body: text })
        }
    }

    /// List spaces under a parent path (optionally recursive).
    pub async fn list_spaces(
        &self,
        parent_path: &str,
        recursive: bool,
    ) -> Result<Vec<SpaceResponse>> {
        let resp = self
            .client
            .get(self.url("/spaces"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[
                ("parent_path", parent_path),
                ("recursive", if recursive { "true" } else { "false" }),
            ])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<SpaceResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    // ── Item CRUD ───────────────────────────────────────────────────

    /// List items in a space.
    pub async fn list_items(
        &self,
        space_path: &str,
        include_deprecated: bool,
    ) -> Result<Vec<ItemResponse>> {
        self.list_items_paged(space_path, include_deprecated, 100, 0)
            .await
    }

    /// List items with explicit pagination.
    pub async fn list_items_paged(
        &self,
        space_path: &str,
        include_deprecated: bool,
        limit: u32,
        offset: u32,
    ) -> Result<Vec<ItemResponse>> {
        let resp = self
            .client
            .get(self.url("/items"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[
                ("space_path", space_path),
                (
                    "include_deprecated",
                    if include_deprecated { "true" } else { "false" },
                ),
                ("limit", &limit.to_string()),
                ("offset", &offset.to_string()),
            ])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<ItemResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List items in a space filtered by name substring.
    ///
    /// Uses the `name_filter` query parameter to reduce result size,
    /// staying under Kumiho's gRPC message limit for large spaces.
    pub async fn list_items_filtered(
        &self,
        space_path: &str,
        name_filter: &str,
        include_deprecated: bool,
    ) -> Result<Vec<ItemResponse>> {
        let resp = self
            .client
            .get(self.url("/items"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[
                ("space_path", space_path),
                ("name_filter", name_filter),
                (
                    "include_deprecated",
                    if include_deprecated { "true" } else { "false" },
                ),
            ])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<ItemResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Create an item.
    pub async fn create_item(
        &self,
        space_path: &str,
        item_name: &str,
        kind: &str,
        metadata: HashMap<String, String>,
    ) -> Result<ItemResponse> {
        let body = CreateItemBody {
            space_path: space_path.to_string(),
            item_name: item_name.to_string(),
            kind: kind.to_string(),
            metadata,
        };

        let resp = self
            .client
            .post(self.url("/items"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Deprecate or restore an item.
    pub async fn deprecate_item(&self, kref: &str, deprecated: bool) -> Result<ItemResponse> {
        let resp = self
            .client
            .post(self.url("/items/deprecate"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[
                ("kref", kref),
                ("deprecated", if deprecated { "true" } else { "false" }),
            ])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Delete an item (force).
    pub async fn delete_item(&self, kref: &str) -> Result<()> {
        let resp = self
            .client
            .delete(self.url("/items/by-kref"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("kref", kref), ("force", "true")])
            .send()
            .await?;

        let _ = self.check_response(resp).await?;
        Ok(())
    }

    /// Full-text search across items.
    pub async fn search_items(
        &self,
        query: &str,
        context: &str,
        kind: &str,
        include_deprecated: bool,
    ) -> Result<Vec<SearchResult>> {
        let resp = self
            .client
            .get(self.url("/items/fulltext-search"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[
                ("query", query),
                ("context", context),
                ("kind", kind),
                (
                    "include_deprecated",
                    if include_deprecated { "true" } else { "false" },
                ),
            ])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<SearchResult>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    // ── Revisions ───────────────────────────────────────────────────

    /// Create a new revision on an item.
    pub async fn create_revision(
        &self,
        item_kref: &str,
        metadata: HashMap<String, String>,
    ) -> Result<RevisionResponse> {
        let body = CreateRevisionBody {
            item_kref: item_kref.to_string(),
            metadata,
        };

        let resp = self
            .client
            .post(self.url("/revisions"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List all revisions for an item, ordered by number.
    ///
    /// Backed by `GET /api/v1/revisions?item_kref=...` on Kumiho. Used by the
    /// editor's revision-history strip (Architect feature).
    pub async fn list_item_revisions(&self, item_kref: &str) -> Result<Vec<RevisionResponse>> {
        let resp = self
            .client
            .get(self.url("/revisions"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("item_kref", item_kref)])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<RevisionResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Tag a revision (e.g. "published").
    pub async fn tag_revision(&self, revision_kref: &str, tag: &str) -> Result<()> {
        let resp = self
            .client
            .post(self.url("/revisions/tags"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("kref", revision_kref)])
            .json(&serde_json::json!({ "tag": tag }))
            .send()
            .await?;

        let _ = self.check_response(resp).await?;
        Ok(())
    }

    /// Get a revision by tag (e.g. "published").
    pub async fn get_revision_by_tag(
        &self,
        item_kref: &str,
        tag: &str,
    ) -> Result<RevisionResponse> {
        let resp = self
            .client
            .get(self.url("/revisions/by-kref"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("kref", item_kref), ("t", tag)])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get a specific revision by its own revision_kref (e.g. "…?r=5").
    /// The Kumiho server's `/revisions/by-kref` endpoint parses the `?r=N`
    /// suffix out of the kref and returns that exact revision's metadata.
    pub async fn get_revision(&self, revision_kref: &str) -> Result<RevisionResponse> {
        let resp = self
            .client
            .get(self.url("/revisions/by-kref"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("kref", revision_kref)])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get the latest revision for an item.
    pub async fn get_latest_revision(&self, item_kref: &str) -> Result<RevisionResponse> {
        let resp = self
            .client
            .get(self.url("/revisions/latest"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("item_kref", item_kref)])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<RevisionResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get the published revision, falling back to latest.
    pub async fn get_published_or_latest(&self, item_kref: &str) -> Result<RevisionResponse> {
        match self.get_revision_by_tag(item_kref, "published").await {
            Ok(rev) => Ok(rev),
            Err(_) => self.get_latest_revision(item_kref).await,
        }
    }

    /// Batch fetch revisions for multiple items by tag in a single HTTP call.
    ///
    /// Returns a map of item_kref → RevisionResponse for items that were found.
    pub async fn batch_get_revisions(
        &self,
        item_krefs: &[String],
        tag: &str,
    ) -> Result<HashMap<String, RevisionResponse>> {
        if item_krefs.is_empty() {
            return Ok(HashMap::new());
        }

        let body = serde_json::json!({
            "item_krefs": item_krefs,
            "tag": tag,
            "allow_partial": true,
        });

        let resp = self
            .client
            .post(self.url("/revisions/batch"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        let batch: BatchRevisionsResponse = resp
            .json()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))?;

        let mut map = HashMap::with_capacity(batch.revisions.len());
        for rev in batch.revisions {
            map.insert(rev.item_kref.clone(), rev);
        }
        Ok(map)
    }

    // ── Skill convenience methods ──────────────────────────────────

    /// List skills in the given project's Skills space.
    pub async fn list_skills(
        &self,
        project: &str,
        include_deprecated: bool,
    ) -> Result<Vec<ItemResponse>> {
        let space_path = format!("/{project}/Skills");
        self.list_items(&space_path, include_deprecated).await
    }

    /// Search skills by query within the given project.
    pub async fn search_skills(
        &self,
        query: &str,
        project: &str,
        include_deprecated: bool,
    ) -> Result<Vec<SearchResult>> {
        self.search_items(query, project, "skill", include_deprecated)
            .await
    }

    /// Create a new skill item + first revision in the given project.
    pub async fn create_skill(
        &self,
        project: &str,
        name: &str,
        metadata: HashMap<String, String>,
    ) -> Result<(ItemResponse, RevisionResponse)> {
        self.ensure_space(project, "Skills").await.ok();
        let space_path = format!("/{project}/Skills");
        let item = self
            .create_item(&space_path, name, "skill", HashMap::new())
            .await?;
        let revision = self.create_revision(&item.kref, metadata).await?;
        Ok((item, revision))
    }

    /// Deprecate or restore a skill.
    pub async fn deprecate_skill(&self, kref: &str, deprecated: bool) -> Result<ItemResponse> {
        self.deprecate_item(kref, deprecated).await
    }

    // ── Bundle methods ─────────────────────────────────────────────

    /// Create a bundle.
    pub async fn create_bundle(
        &self,
        space_path: &str,
        bundle_name: &str,
        metadata: HashMap<String, String>,
    ) -> Result<ItemResponse> {
        let body = CreateBundleBody {
            space_path: space_path.to_string(),
            bundle_name: bundle_name.to_string(),
            metadata,
        };

        let resp = self
            .client
            .post(self.url("/bundles"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get a bundle by kref.
    pub async fn get_bundle(&self, kref: &str) -> Result<ItemResponse> {
        let resp = self
            .client
            .get(self.url("/bundles/by-kref"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("kref", kref)])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ItemResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Delete a bundle (force).
    pub async fn delete_bundle(&self, kref: &str) -> Result<()> {
        let resp = self
            .client
            .delete(self.url("/bundles/by-kref"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("kref", kref), ("force", "true")])
            .send()
            .await?;

        let _ = self.check_response(resp).await?;
        Ok(())
    }

    /// Add a member to a bundle.
    pub async fn add_bundle_member(
        &self,
        bundle_kref: &str,
        item_kref: &str,
        metadata: HashMap<String, String>,
    ) -> Result<serde_json::Value> {
        let body = BundleMemberBody {
            bundle_kref: bundle_kref.to_string(),
            item_kref: item_kref.to_string(),
            metadata: if metadata.is_empty() {
                None
            } else {
                Some(metadata)
            },
        };

        let resp = self
            .client
            .post(self.url("/bundles/members/add"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<serde_json::Value>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Remove a member from a bundle.
    pub async fn remove_bundle_member(
        &self,
        bundle_kref: &str,
        item_kref: &str,
    ) -> Result<serde_json::Value> {
        let body = RemoveBundleMemberBody {
            bundle_kref: bundle_kref.to_string(),
            item_kref: item_kref.to_string(),
        };

        let resp = self
            .client
            .post(self.url("/bundles/members/remove"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<serde_json::Value>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List members of a bundle.
    pub async fn list_bundle_members(&self, bundle_kref: &str) -> Result<BundleMembersResponse> {
        let resp = self
            .client
            .get(self.url("/bundles/members"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("bundle_kref", bundle_kref)])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<BundleMembersResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    // ── Edge methods ───────────────────────────────────────────────

    /// Create an edge between two revisions.
    pub async fn create_edge(
        &self,
        source_kref: &str,
        target_kref: &str,
        edge_type: &str,
        metadata: HashMap<String, String>,
    ) -> Result<EdgeResponse> {
        let body = CreateEdgeBody {
            source_revision_kref: source_kref.to_string(),
            target_revision_kref: target_kref.to_string(),
            edge_type: edge_type.to_string(),
            metadata,
        };

        let resp = self
            .client
            .post(self.url("/edges"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<EdgeResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List edges for a revision.
    ///
    /// `direction`: 0 = outgoing, 1 = incoming, 2 = both.
    pub async fn list_edges(
        &self,
        revision_kref: &str,
        edge_type: Option<&str>,
        direction: Option<&str>,
    ) -> Result<Vec<EdgeResponse>> {
        // Map string directions to numeric values expected by Kumiho API
        let dir_num = direction.map(|d| match d {
            "outgoing" | "out" => "0",
            "incoming" | "in" => "1",
            "both" => "2",
            other => other, // pass through if already numeric
        });

        let mut query_params: Vec<(&str, &str)> = vec![("kref", revision_kref)];
        if let Some(et) = edge_type {
            query_params.push(("edge_type", et));
        }
        if let Some(dir) = dir_num.as_deref() {
            query_params.push(("direction", dir));
        }

        let resp = self
            .client
            .get(self.url("/edges"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&query_params)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<EdgeResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Delete an edge.
    pub async fn delete_edge(
        &self,
        source_kref: &str,
        target_kref: &str,
        edge_type: &str,
    ) -> Result<()> {
        let resp = self
            .client
            .delete(self.url("/edges"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[
                ("source_kref", source_kref),
                ("target_kref", target_kref),
                ("edge_type", edge_type),
            ])
            .send()
            .await?;

        let _ = self.check_response(resp).await?;
        Ok(())
    }

    // ── Artifact methods ──────────────────────────────────────────

    /// Create an artifact associated with a revision.
    pub async fn create_artifact(
        &self,
        revision_kref: &str,
        name: &str,
        location: &str,
        metadata: HashMap<String, String>,
    ) -> Result<ArtifactResponse> {
        let body = CreateArtifactBody {
            revision_kref: revision_kref.to_string(),
            name: name.to_string(),
            location: location.to_string(),
            metadata,
        };

        let resp = self
            .client
            .post(self.url("/artifacts"))
            .header("X-Kumiho-Token", &self.service_token)
            .json(&body)
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ArtifactResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// List artifacts for a revision.
    pub async fn get_artifacts(&self, revision_kref: &str) -> Result<Vec<ArtifactResponse>> {
        let resp = self
            .client
            .get(self.url("/artifacts"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("revision_kref", revision_kref)])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<Vec<ArtifactResponse>>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    /// Get a specific artifact by revision kref and name.
    pub async fn get_artifact_by_name(
        &self,
        revision_kref: &str,
        name: &str,
    ) -> Result<ArtifactResponse> {
        let resp = self
            .client
            .get(self.url("/artifacts/by-kref"))
            .header("X-Kumiho-Token", &self.service_token)
            .query(&[("revision_kref", revision_kref), ("name", name)])
            .send()
            .await?;

        let resp = self.check_response(resp).await?;
        resp.json::<ArtifactResponse>()
            .await
            .map_err(|e| KumihoError::Decode(e.to_string()))
    }

    // ── Team convenience methods ───────────────────────────────────

    /// List teams in the given `<project>/Teams` space.
    pub async fn list_teams_in(
        &self,
        space_path: &str,
        include_deprecated: bool,
    ) -> Result<Vec<ItemResponse>> {
        self.list_items(space_path, include_deprecated).await
    }

    /// Deprecate or restore a team.
    pub async fn deprecate_team(&self, kref: &str, deprecated: bool) -> Result<()> {
        self.deprecate_item(kref, deprecated).await?;
        Ok(())
    }
}
