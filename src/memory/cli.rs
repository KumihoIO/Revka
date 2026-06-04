//! `revka memory` CLI commands, backed by Kumiho MCP.
//!
//! Persistent memory in Revka is stored in Kumiho as item → revision pairs,
//! with small content stashed in the revision's metadata (`content` key). The
//! CLI targets a dedicated `Memory` space under the configured memory project
//! so CLI-managed entries stay separate from memories captured via
//! `kumiho_memory_reflect` and friends.

use crate::config::Config;
use crate::gateway::kumiho_client::{
    ItemResponse, KumihoClient, KumihoError, RevisionResponse, build_client_from_config, slugify,
};
use anyhow::{Result, bail};
use std::collections::HashMap;
use std::io::{self, Write};

/// Subspace name (under the configured `kumiho.memory_project`) where CLI-managed
/// entries live. Keeps them separate from agent-captured memories.
pub const CLI_SPACE_NAME: &str = "Memory";
const MEMORY_ITEM_KIND: &str = "memory";
const CONTENT_PREVIEW_LEN: usize = 120;

/// Handle `revka memory <subcommand>` CLI commands.
pub async fn handle_command(command: crate::MemoryCommands, config: &Config) -> Result<()> {
    let ctx = CliContext::from_config(config);

    match command {
        crate::MemoryCommands::List {
            category,
            session,
            limit,
            offset,
        } => list_entries(&ctx, category.as_deref(), session.as_deref(), limit, offset).await,
        crate::MemoryCommands::Get { key } => get_entry(&ctx, &key).await,
        crate::MemoryCommands::Stats => show_stats(&ctx).await,
        crate::MemoryCommands::Clear { key, category, yes } => {
            clear_entries(&ctx, key.as_deref(), category.as_deref(), yes).await
        }
    }
}

struct CliContext {
    client: KumihoClient,
    project: String,
    api_url: String,
}

impl CliContext {
    fn from_config(config: &Config) -> Self {
        Self {
            client: build_client_from_config(config),
            project: config.kumiho.memory_project.clone(),
            api_url: config.kumiho.api_url.clone(),
        }
    }

    fn space_path(&self) -> String {
        format!("/{}/{CLI_SPACE_NAME}", self.project)
    }

    async fn ensure_space(&self) -> Result<()> {
        self.client
            .ensure_project(&self.project)
            .await
            .map_err(|e| kumiho_err(e, "ensure project"))?;
        self.client
            .ensure_child_space(&self.project, &format!("/{}", self.project), CLI_SPACE_NAME)
            .await
            .map_err(|e| kumiho_err(e, "ensure space"))?;
        Ok(())
    }
}

async fn list_entries(
    ctx: &CliContext,
    category_filter: Option<&str>,
    session_filter: Option<&str>,
    limit: usize,
    offset: usize,
) -> Result<()> {
    ctx.ensure_space().await?;
    let space = ctx.space_path();

    // Fetch a superset so we can filter client-side, capped sensibly.
    let fetch_limit =
        u32::try_from(limit.saturating_add(offset).saturating_mul(2).max(50)).unwrap_or(u32::MAX);
    let items = ctx
        .client
        .list_items_paged(&space, false, fetch_limit, 0)
        .await
        .map_err(|e| kumiho_err(e, "list items"))?;

    if items.is_empty() {
        println!("No memory entries in {space}.");
        return Ok(());
    }

    let mut rows: Vec<(ItemResponse, RevisionResponse)> = Vec::with_capacity(items.len());
    for item in items {
        match ctx.client.get_latest_revision(&item.kref).await {
            Ok(rev) => rows.push((item, rev)),
            Err(KumihoError::Api { status: 404, .. }) => continue,
            Err(e) => return Err(kumiho_err(e, "fetch revision")),
        }
    }

    let filtered: Vec<_> = rows
        .into_iter()
        .filter(|(_, rev)| {
            category_filter
                .map(|c| rev.metadata.get("category").map(String::as_str) == Some(c))
                .unwrap_or(true)
        })
        .filter(|(_, rev)| {
            session_filter
                .map(|s| rev.metadata.get("session_id").map(String::as_str) == Some(s))
                .unwrap_or(true)
        })
        .skip(offset)
        .take(limit)
        .collect();

    if filtered.is_empty() {
        println!("No memory entries matched the given filters.");
        return Ok(());
    }

    for (item, rev) in &filtered {
        let key = rev
            .metadata
            .get("key")
            .cloned()
            .unwrap_or_else(|| item.item_name.clone());
        let category = rev
            .metadata
            .get("category")
            .map(String::as_str)
            .unwrap_or("core");
        let content = rev
            .metadata
            .get("content")
            .map(String::as_str)
            .unwrap_or("");
        println!(
            "{key}\t[{category}]\t{}",
            truncate(content, CONTENT_PREVIEW_LEN)
        );
    }

    println!();
    println!(
        "{} entr{} shown.",
        filtered.len(),
        if filtered.len() == 1 { "y" } else { "ies" }
    );
    Ok(())
}

async fn get_entry(ctx: &CliContext, key: &str) -> Result<()> {
    ctx.ensure_space().await?;
    let space = ctx.space_path();
    let slug = slugify(key);
    if slug.is_empty() {
        bail!("Key '{key}' could not be slugified to a valid identifier");
    }

    let items = ctx
        .client
        .list_items_filtered(&space, &slug, false)
        .await
        .map_err(|e| kumiho_err(e, "search for entry"))?;

    let item = items
        .into_iter()
        .find(|i| i.item_name == slug)
        .ok_or_else(|| anyhow::anyhow!("No memory entry found for key '{key}' (slug: {slug})"))?;

    let rev = ctx
        .client
        .get_latest_revision(&item.kref)
        .await
        .map_err(|e| kumiho_err(e, "fetch revision"))?;

    let content = rev
        .metadata
        .get("content")
        .map(String::as_str)
        .unwrap_or("");
    let category = rev
        .metadata
        .get("category")
        .map(String::as_str)
        .unwrap_or("core");
    let original_key = rev
        .metadata
        .get("key")
        .cloned()
        .unwrap_or_else(|| slug.clone());

    println!("Key:      {original_key}");
    println!("Slug:     {slug}");
    println!("Category: {category}");
    if let Some(session) = rev.metadata.get("session_id") {
        println!("Session:  {session}");
    }
    if let Some(origin) = rev.metadata.get("migrated_from") {
        println!("Origin:   {origin}");
    }
    if let Some(imported) = rev.metadata.get("imported_at") {
        println!("Imported: {imported}");
    }
    if let Some(created) = rev.created_at.as_deref() {
        println!("Created:  {created}");
    }
    println!("Kref:     {}", item.kref);
    println!();
    println!("{content}");

    Ok(())
}

async fn show_stats(ctx: &CliContext) -> Result<()> {
    println!("Kumiho endpoint: {}", ctx.api_url);
    println!("Memory project: {}", ctx.project);

    let root = format!("/{}", ctx.project);
    let spaces = match ctx.client.list_spaces(&root, true).await {
        Ok(spaces) => spaces,
        Err(KumihoError::Unreachable(err)) => {
            bail!(
                "Kumiho service unreachable at {}: {err}. \
                 Check that Kumiho is running and that `kumiho.api_url` points to it.",
                ctx.api_url
            );
        }
        Err(e) => return Err(kumiho_err(e, "list spaces")),
    };

    let mut total_items = 0usize;
    let mut rows: Vec<(String, usize)> = Vec::new();

    // Include the project root itself.
    let mut space_paths: Vec<String> = vec![root.clone()];
    space_paths.extend(spaces.into_iter().map(|s| s.path));

    for path in &space_paths {
        let count = ctx
            .client
            .list_items_paged(path, false, 500, 0)
            .await
            .map(|items| items.len())
            .unwrap_or(0);
        total_items += count;
        rows.push((path.clone(), count));
    }

    println!("Spaces:         {}", space_paths.len());
    println!("Total items:    {}", total_items);
    println!();
    println!("Per-space counts:");
    for (path, count) in &rows {
        println!("  {path:<40} {count}");
    }

    Ok(())
}

async fn clear_entries(
    ctx: &CliContext,
    key_filter: Option<&str>,
    category_filter: Option<&str>,
    skip_confirm: bool,
) -> Result<()> {
    ctx.ensure_space().await?;
    let space = ctx.space_path();

    // Collect items to delete, applying filters.
    let items = ctx
        .client
        .list_items_paged(&space, false, 500, 0)
        .await
        .map_err(|e| kumiho_err(e, "list items"))?;

    let mut targets: Vec<ItemResponse> = Vec::new();
    for item in items {
        if let Some(prefix) = key_filter {
            let slug_prefix = slugify(prefix);
            if !item.item_name.starts_with(&slug_prefix) {
                continue;
            }
        }
        if let Some(cat) = category_filter {
            let rev = ctx.client.get_latest_revision(&item.kref).await.ok();
            let matches = rev
                .as_ref()
                .and_then(|r| r.metadata.get("category"))
                .map(String::as_str)
                == Some(cat);
            if !matches {
                continue;
            }
        }
        targets.push(item);
    }

    if targets.is_empty() {
        println!("No entries matched the given filter — nothing to clear.");
        return Ok(());
    }

    if !skip_confirm {
        print!(
            "About to delete {} entr{} from {space}. Continue? [y/N] ",
            targets.len(),
            if targets.len() == 1 { "y" } else { "ies" }
        );
        io::stdout().flush().ok();
        let mut input = String::new();
        io::stdin().read_line(&mut input)?;
        if !matches!(input.trim().to_ascii_lowercase().as_str(), "y" | "yes") {
            println!("Cancelled.");
            return Ok(());
        }
    }

    let mut deleted = 0usize;
    for item in targets {
        match ctx.client.delete_item(&item.kref).await {
            Ok(()) => deleted += 1,
            Err(e) => {
                eprintln!("Failed to delete {}: {}", item.item_name, e);
            }
        }
    }

    println!(
        "Deleted {deleted} entr{}.",
        if deleted == 1 { "y" } else { "ies" }
    );
    Ok(())
}

fn truncate(s: &str, max_chars: usize) -> String {
    let single_line: String = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if single_line.chars().count() <= max_chars {
        single_line
    } else {
        let truncated: String = single_line.chars().take(max_chars).collect();
        format!("{truncated}…")
    }
}

fn kumiho_err(e: KumihoError, action: &'static str) -> anyhow::Error {
    match e {
        KumihoError::Unreachable(err) => anyhow::anyhow!(
            "Kumiho service unreachable while attempting to {action}: {err}. \
             Check that Kumiho is running and that `kumiho.api_url` points to it."
        ),
        KumihoError::Api { status, body } => {
            anyhow::anyhow!("Kumiho returned {status} while attempting to {action}: {body}")
        }
        KumihoError::UpstreamUnavailable { status, attempts } => anyhow::anyhow!(
            "Kumiho upstream temporarily unavailable (HTTP {status} after {attempts} attempts) \
             while attempting to {action}. Try again shortly."
        ),
        KumihoError::Decode(msg) => anyhow::anyhow!(
            "Kumiho returned an unexpected response while attempting to {action}: {msg}"
        ),
    }
}

/// Revka a revision metadata map for a CLI-stored memory entry.
///
/// Kept as a free helper so other callers (e.g. future `revka memory store`
/// subcommand) can build revisions with the same schema the list/get commands
/// expect.
#[allow(dead_code)]
pub fn cli_revision_metadata(
    key: &str,
    content: &str,
    category: &str,
    session_id: Option<&str>,
) -> HashMap<String, String> {
    let mut meta = HashMap::new();
    meta.insert("key".into(), key.to_string());
    meta.insert("content".into(), content.to_string());
    meta.insert("category".into(), category.to_string());
    if let Some(session) = session_id {
        meta.insert("session_id".into(), session.to_string());
    }
    meta.insert("kind".into(), MEMORY_ITEM_KIND.to_string());
    meta
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_shortens_long_strings() {
        let long = "a".repeat(200);
        let t = truncate(&long, 50);
        assert_eq!(t.chars().count(), 51); // 50 chars + ellipsis
        assert!(t.ends_with('…'));
    }

    #[test]
    fn truncate_passes_through_short_strings() {
        let t = truncate("short", 50);
        assert_eq!(t, "short");
    }

    #[test]
    fn truncate_collapses_whitespace() {
        let t = truncate("a  b\nc\td", 50);
        assert_eq!(t, "a b c d");
    }

    #[test]
    fn cli_revision_metadata_contains_expected_keys() {
        let meta = cli_revision_metadata("my_key", "hello", "core", Some("sess-1"));
        assert_eq!(meta.get("key").map(String::as_str), Some("my_key"));
        assert_eq!(meta.get("content").map(String::as_str), Some("hello"));
        assert_eq!(meta.get("category").map(String::as_str), Some("core"));
        assert_eq!(meta.get("session_id").map(String::as_str), Some("sess-1"));
        assert_eq!(meta.get("kind").map(String::as_str), Some(MEMORY_ITEM_KIND));
    }

    #[test]
    fn cli_revision_metadata_omits_session_when_absent() {
        let meta = cli_revision_metadata("k", "v", "daily", None);
        assert!(!meta.contains_key("session_id"));
    }
}
