//! Register discovered harness skills with Kumiho — without moving files.
//!
//! Each [`DiscoveredHarnessSkill`] becomes a Kumiho `item → revision → artifact`
//! in `<memory_project>/Skills`, exactly like a native Revka skill
//! ([`crate::skills::registration`]), except:
//!
//! - The artifact is named `"SKILL.md"` (the operator-mcp loader's preferred
//!   name) and its `location` points at the **original** harness file in place
//!   — nothing is copied or rewritten on the user's disk.
//! - There is no `SKILL.toml` to read or rewrite (harness dirs are never
//!   touched). Idempotency is tracked in an on-disk ledger under `~/.revka/state`
//!   keyed by the canonical source path, with a Kumiho existence check as a
//!   fallback when the ledger is missing.
//!
//! The Operator discovers these on demand via `kumiho_memory_engage` /
//! `list_skills` and loads each body via `load_skill`, which reads the file from
//! its recorded location. No Operator wiring is required.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

use super::harness_scan::DiscoveredHarnessSkill;
use super::harness_uri::to_file_uri;
use super::registration::{PUBLISHED_TAG, SKILL_ITEM_KIND};
use crate::gateway::kumiho_client::KumihoClient;

/// Artifact name the operator-mcp skill loader looks for first
/// (`_SKILL_ARTIFACT_NAME` in `tool_handlers/skills.py`).
const SKILL_ARTIFACT_NAME: &str = "SKILL.md";

/// Outcome of registering one harness skill.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HarnessRegistration {
    /// Ledger hit with a matching content hash — no Kumiho calls were made.
    Unchanged { slug: String, item_kref: String },
    /// A new skill item was created.
    Registered {
        slug: String,
        item_kref: String,
        revision_kref: String,
    },
    /// An existing item gained a new published revision (content changed, or the
    /// item was adopted because it already existed in Kumiho).
    Updated {
        slug: String,
        item_kref: String,
        revision_kref: String,
    },
}

/// Aggregate result of an import batch.
#[derive(Debug, Clone, Default)]
pub struct HarnessImportReport {
    pub registered: usize,
    pub updated: usize,
    pub unchanged: usize,
    /// Per-skill failures (source path, error) — a batch is best-effort.
    pub failed: Vec<(PathBuf, String)>,
    pub item_krefs: Vec<String>,
}

// ── Ledger ─────────────────────────────────────────────────────────────────

/// On-disk idempotency record, keyed by canonical source path.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Ledger {
    #[serde(default)]
    pub entries: HashMap<String, LedgerEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LedgerEntry {
    pub slug: String,
    pub item_kref: String,
    pub content_hash: String,
    pub source_path: String,
    pub registered_at: String,
}

impl Ledger {
    /// Load the ledger; a missing file yields an empty ledger and a corrupt one
    /// is logged and treated as empty (the Kumiho existence check recovers).
    pub fn load(path: &Path) -> Self {
        match std::fs::read_to_string(path) {
            Ok(raw) => serde_json::from_str(&raw).unwrap_or_else(|e| {
                tracing::warn!(
                    error = %e,
                    path = %path.display(),
                    "harness import ledger is corrupt; starting fresh"
                );
                Ledger::default()
            }),
            Err(_) => Ledger::default(),
        }
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("creating {}", parent.display()))?;
        }
        let raw = serde_json::to_string_pretty(self).context("serializing harness ledger")?;
        std::fs::write(path, raw).with_context(|| format!("writing {}", path.display()))?;
        Ok(())
    }
}

/// Default ledger location: `~/.revka/state/harness_skills.json`.
///
/// Resolves `~/.revka` the same way as `config::schema::default_config_dir`
/// (`$HOME` first, then the OS home directory).
pub fn default_ledger_path() -> PathBuf {
    revka_state_dir().join("harness_skills.json")
}

fn revka_state_dir() -> PathBuf {
    let home = std::env::var("HOME")
        .ok()
        .filter(|h| !h.is_empty())
        .map(PathBuf::from)
        .or_else(|| directories::UserDirs::new().map(|u| u.home_dir().to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));
    home.join(".revka").join("state")
}

// ── Decision logic (pure) ───────────────────────────────────────────────────

/// What the ledger alone says to do with a skill (before any Kumiho lookup).
#[derive(Debug, Clone, PartialEq, Eq)]
enum LedgerDecision {
    /// Already registered with the same content under the right project.
    Unchanged { slug: String, item_kref: String },
    /// Known item, but content changed — publish a new revision.
    Update { item_kref: String },
    /// Not in the ledger (or project mismatch) — look up / create in Kumiho.
    Resolve,
}

/// Decide an action from the ledger only. An entry counts only when its
/// `item_kref` belongs to the configured `memory_project` (guards against a
/// `memory_project` change between runs).
fn ledger_decision(
    ledger: &Ledger,
    key: &str,
    skill: &DiscoveredHarnessSkill,
    memory_project: &str,
) -> LedgerDecision {
    let prefix = format!("kref://{memory_project}/");
    match ledger.entries.get(key) {
        Some(entry) if entry.item_kref.starts_with(&prefix) => {
            if entry.content_hash == skill.content_hash {
                LedgerDecision::Unchanged {
                    slug: entry.slug.clone(),
                    item_kref: entry.item_kref.clone(),
                }
            } else {
                LedgerDecision::Update {
                    item_kref: entry.item_kref.clone(),
                }
            }
        }
        _ => LedgerDecision::Resolve,
    }
}

// ── Metadata builders (pure) ────────────────────────────────────────────────

/// Item metadata — merged FIRST by the operator's `_skill_summary`. `description`
/// and `domain` are required for discovery.
fn item_metadata(skill: &DiscoveredHarnessSkill) -> HashMap<String, String> {
    let mut m = HashMap::new();
    m.insert("description".into(), skill.description.clone());
    m.insert("domain".into(), skill.harness_tool.as_str().to_string());
    m.insert("title".into(), skill.name.clone());
    m.insert("source".into(), "revka-harness-import".to_string());
    m.insert(
        "harness_kind".into(),
        skill.harness_kind.as_str().to_string(),
    );
    m.insert(
        "harness_tool".into(),
        skill.harness_tool.as_str().to_string(),
    );
    m.insert(
        "source_path".into(),
        skill.source_path.to_string_lossy().to_string(),
    );
    m
}

/// Revision metadata — merged SECOND (wins on clash), so an Update revision
/// carries current description/domain plus the change-detection hash.
fn revision_metadata(skill: &DiscoveredHarnessSkill) -> HashMap<String, String> {
    let mut m = item_metadata(skill);
    m.insert("content_hash".into(), skill.content_hash.clone());
    m.insert("artifact_name".into(), SKILL_ARTIFACT_NAME.to_string());
    m.insert("created_at".into(), chrono::Utc::now().to_rfc3339());
    if let Some(globs) = &skill.globs {
        m.insert("globs".into(), globs.clone());
    }
    m
}

// ── Registration ────────────────────────────────────────────────────────────

/// Register a single discovered harness skill with Kumiho, updating `ledger`
/// in memory on success (the caller persists it).
///
/// Idempotent: an unchanged skill returns [`HarnessRegistration::Unchanged`]
/// with zero Kumiho calls. The ledger entry is only written after every Kumiho
/// call for the skill has succeeded, so a partial failure leaves a clean state
/// for the next run to retry.
pub async fn register_harness_skill(
    client: &KumihoClient,
    memory_project: &str,
    skill: &DiscoveredHarnessSkill,
    ledger: &mut Ledger,
) -> Result<HarnessRegistration> {
    let key = skill.source_path.to_string_lossy().to_string();
    let space_path = format!("/{memory_project}/Skills");

    // Cheap path: ledger says nothing changed.
    let decision = ledger_decision(ledger, &key, skill, memory_project);
    if let LedgerDecision::Unchanged { slug, item_kref } = &decision {
        return Ok(HarnessRegistration::Unchanged {
            slug: slug.clone(),
            item_kref: item_kref.clone(),
        });
    }

    client
        .ensure_project(memory_project)
        .await
        .with_context(|| format!("ensure_project({memory_project})"))?;
    client
        .ensure_space(memory_project, "Skills")
        .await
        .with_context(|| format!("ensure_space({memory_project}/Skills)"))?;

    // Resolve the target item: known from the ledger, found in Kumiho (adopt),
    // or freshly created.
    let (item_kref, is_update) = match decision {
        LedgerDecision::Update { item_kref } => (item_kref, true),
        LedgerDecision::Unchanged { .. } => unreachable!("handled above"),
        LedgerDecision::Resolve => match find_existing_item(client, &space_path, skill).await? {
            Some(item_kref) => (item_kref, true),
            None => {
                let item = client
                    .create_item(
                        &space_path,
                        &skill.slug,
                        SKILL_ITEM_KIND,
                        item_metadata(skill),
                    )
                    .await
                    .with_context(|| format!("create_item({space_path}/{})", skill.slug))?;
                (item.kref, false)
            }
        },
    };

    let revision = client
        .create_revision(&item_kref, revision_metadata(skill))
        .await
        .with_context(|| format!("create_revision({item_kref})"))?;

    let location = to_file_uri(&skill.source_path);
    client
        .create_artifact(
            &revision.kref,
            SKILL_ARTIFACT_NAME,
            &location,
            HashMap::new(),
        )
        .await
        .with_context(|| format!("create_artifact({} -> {location})", revision.kref))?;

    client
        .tag_revision(&revision.kref, PUBLISHED_TAG)
        .await
        .with_context(|| format!("tag_revision({}, {PUBLISHED_TAG})", revision.kref))?;

    // All Kumiho calls succeeded — record in the ledger.
    ledger.entries.insert(
        key.clone(),
        LedgerEntry {
            slug: skill.slug.clone(),
            item_kref: item_kref.clone(),
            content_hash: skill.content_hash.clone(),
            source_path: key,
            registered_at: chrono::Utc::now().to_rfc3339(),
        },
    );

    Ok(if is_update {
        HarnessRegistration::Updated {
            slug: skill.slug.clone(),
            item_kref,
            revision_kref: revision.kref,
        }
    } else {
        HarnessRegistration::Registered {
            slug: skill.slug.clone(),
            item_kref,
            revision_kref: revision.kref,
        }
    })
}

/// Find a pre-existing Kumiho item for this skill — by slug (item name), then by
/// artifact location (the same file imported earlier under a different slug).
async fn find_existing_item(
    client: &KumihoClient,
    space_path: &str,
    skill: &DiscoveredHarnessSkill,
) -> Result<Option<String>> {
    let items = client
        .list_items_filtered(space_path, &skill.slug, false)
        .await
        .with_context(|| format!("list_items_filtered({space_path}, {})", skill.slug))?;
    if let Some(item) = items
        .iter()
        .find(|i| i.item_name == skill.slug || i.name == skill.slug)
    {
        return Ok(Some(item.kref.clone()));
    }

    // Secondary: a previous import of the same file under a different slug.
    let location = to_file_uri(&skill.source_path);
    if let Ok(artifacts) = client.get_artifacts_by_location(&location).await {
        if let Some(item_kref) = artifacts.iter().find_map(|a| a.item_kref.clone()) {
            return Ok(Some(item_kref));
        }
    }
    Ok(None)
}

/// Register a batch of discovered skills, persisting the ledger after each
/// change. Best-effort: a per-skill failure is recorded and the batch continues.
pub async fn import_harness_skills(
    client: &KumihoClient,
    memory_project: &str,
    skills: &[DiscoveredHarnessSkill],
    ledger_path: &Path,
) -> Result<HarnessImportReport> {
    let mut ledger = Ledger::load(ledger_path);
    let mut report = HarnessImportReport::default();

    for skill in skills {
        match register_harness_skill(client, memory_project, skill, &mut ledger).await {
            Ok(HarnessRegistration::Unchanged { .. }) => report.unchanged += 1,
            Ok(HarnessRegistration::Registered { item_kref, .. }) => {
                report.registered += 1;
                report.item_krefs.push(item_kref);
                if let Err(e) = ledger.save(ledger_path) {
                    tracing::warn!(error = %e, "failed to persist harness ledger");
                }
            }
            Ok(HarnessRegistration::Updated { item_kref, .. }) => {
                report.updated += 1;
                report.item_krefs.push(item_kref);
                if let Err(e) = ledger.save(ledger_path) {
                    tracing::warn!(error = %e, "failed to persist harness ledger");
                }
            }
            Err(e) => report
                .failed
                .push((skill.source_path.clone(), format!("{e:#}"))),
        }
    }

    Ok(report)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::skills::harness_scan::{HarnessKind, HarnessTool};

    fn skill(slug: &str, hash: &str) -> DiscoveredHarnessSkill {
        DiscoveredHarnessSkill {
            name: "demo".into(),
            slug: slug.into(),
            description: "demo skill".into(),
            harness_tool: HarnessTool::Claude,
            harness_kind: HarnessKind::Skill,
            source_path: PathBuf::from("/tmp/demo/.claude/skills/demo/SKILL.md"),
            content_hash: hash.into(),
            globs: None,
        }
    }

    fn ledger_with(key: &str, slug: &str, item_kref: &str, hash: &str) -> Ledger {
        let mut l = Ledger::default();
        l.entries.insert(
            key.into(),
            LedgerEntry {
                slug: slug.into(),
                item_kref: item_kref.into(),
                content_hash: hash.into(),
                source_path: key.into(),
                registered_at: "2026-06-26T00:00:00Z".into(),
            },
        );
        l
    }

    #[test]
    fn ledger_round_trips_json() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state/harness_skills.json");
        let l = ledger_with(
            "/tmp/x/AGENTS.md",
            "harness-codex-x",
            "kref://CognitiveMemory/Skills/harness-codex-x.skill",
            "abc",
        );
        l.save(&path).unwrap();
        let back = Ledger::load(&path);
        assert_eq!(back.entries.len(), 1);
        assert_eq!(back.entries["/tmp/x/AGENTS.md"].slug, "harness-codex-x");
    }

    #[test]
    fn corrupt_ledger_loads_as_empty() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("harness_skills.json");
        std::fs::write(&path, "{ not json").unwrap();
        assert!(Ledger::load(&path).entries.is_empty());
    }

    #[test]
    fn decision_unchanged_when_hash_matches() {
        let s = skill("harness-claude-demo", "h1");
        let key = s.source_path.to_string_lossy().to_string();
        let l = ledger_with(
            &key,
            "harness-claude-demo",
            "kref://CognitiveMemory/Skills/harness-claude-demo.skill",
            "h1",
        );
        assert_eq!(
            ledger_decision(&l, &key, &s, "CognitiveMemory"),
            LedgerDecision::Unchanged {
                slug: "harness-claude-demo".into(),
                item_kref: "kref://CognitiveMemory/Skills/harness-claude-demo.skill".into(),
            }
        );
    }

    #[test]
    fn decision_update_when_hash_changes() {
        let s = skill("harness-claude-demo", "h2");
        let key = s.source_path.to_string_lossy().to_string();
        let l = ledger_with(
            &key,
            "harness-claude-demo",
            "kref://CognitiveMemory/Skills/harness-claude-demo.skill",
            "h1",
        );
        assert_eq!(
            ledger_decision(&l, &key, &s, "CognitiveMemory"),
            LedgerDecision::Update {
                item_kref: "kref://CognitiveMemory/Skills/harness-claude-demo.skill".into(),
            }
        );
    }

    #[test]
    fn decision_resolve_when_absent() {
        let s = skill("harness-claude-demo", "h1");
        let l = Ledger::default();
        assert_eq!(
            ledger_decision(&l, "missing", &s, "CognitiveMemory"),
            LedgerDecision::Resolve
        );
    }

    #[test]
    fn decision_resolve_on_project_mismatch() {
        // Entry exists but points at a different project → re-resolve.
        let s = skill("harness-claude-demo", "h1");
        let key = s.source_path.to_string_lossy().to_string();
        let l = ledger_with(
            &key,
            "harness-claude-demo",
            "kref://OldProject/Skills/harness-claude-demo.skill",
            "h1",
        );
        assert_eq!(
            ledger_decision(&l, &key, &s, "CognitiveMemory"),
            LedgerDecision::Resolve
        );
    }

    #[tokio::test]
    async fn register_unchanged_short_circuits_without_network() {
        // Dead endpoint: if the Unchanged path touched the client this would error.
        let client = KumihoClient::new("http://127.0.0.1:1".into(), "test".into());
        let s = skill("harness-claude-demo", "h1");
        let key = s.source_path.to_string_lossy().to_string();
        let mut l = ledger_with(
            &key,
            "harness-claude-demo",
            "kref://CognitiveMemory/Skills/harness-claude-demo.skill",
            "h1",
        );
        let reg = register_harness_skill(&client, "CognitiveMemory", &s, &mut l)
            .await
            .expect("unchanged path must not hit the network");
        assert!(matches!(reg, HarnessRegistration::Unchanged { .. }));
    }

    #[test]
    fn metadata_maps_have_required_operator_keys() {
        let s = skill("harness-claude-demo", "h1");
        let item = item_metadata(&s);
        for key in [
            "description",
            "domain",
            "title",
            "source",
            "harness_kind",
            "harness_tool",
            "source_path",
        ] {
            assert!(item.contains_key(key), "item metadata missing {key}");
        }
        let rev = revision_metadata(&s);
        for key in [
            "description",
            "domain",
            "content_hash",
            "artifact_name",
            "created_at",
        ] {
            assert!(rev.contains_key(key), "revision metadata missing {key}");
        }
        assert_eq!(rev["artifact_name"], "SKILL.md");
        assert_eq!(item["domain"], "claude");
    }

    #[test]
    fn default_ledger_path_is_under_revka_state() {
        let p = default_ledger_path();
        assert!(p.ends_with("harness_skills.json"));
        assert!(
            p.to_string_lossy()
                .replace('\\', "/")
                .contains(".revka/state")
        );
    }
}
