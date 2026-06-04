use crate::config::Config;
use crate::gateway::kumiho_client::{ItemResponse, KumihoError, build_client_from_config, slugify};
use crate::memory::MemoryCategory;
use anyhow::{Context, Result, bail};
use chrono::Utc;
use directories::UserDirs;
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

const OPENCLAW_SPACE_NAME: &str = "OpenClawImport";
const MEMORY_ITEM_KIND: &str = "memory";

#[derive(Debug, Clone)]
struct SourceEntry {
    key: String,
    content: String,
    category: MemoryCategory,
}

#[derive(Debug, Default)]
struct MigrationStats {
    from_sqlite: usize,
    from_markdown: usize,
    imported: usize,
    skipped_unchanged: usize,
    renamed_conflicts: usize,
}

pub async fn handle_command(command: crate::MigrateCommands, config: &Config) -> Result<()> {
    match command {
        crate::MigrateCommands::Openclaw { source, dry_run } => {
            migrate_openclaw_memory(config, source, dry_run).await
        }
    }
}

async fn migrate_openclaw_memory(
    config: &Config,
    source_workspace: Option<PathBuf>,
    dry_run: bool,
) -> Result<()> {
    let source_workspace = resolve_openclaw_workspace(source_workspace)?;
    if !source_workspace.exists() {
        bail!(
            "OpenClaw workspace not found at {}. Pass --source <path> if needed.",
            source_workspace.display()
        );
    }

    if paths_equal(&source_workspace, &config.workspace_dir) {
        bail!("Source workspace matches current Revka workspace; refusing self-migration");
    }

    let mut stats = MigrationStats::default();
    let entries = collect_source_entries(&source_workspace, &mut stats)?;

    if entries.is_empty() {
        println!(
            "No importable memory found in {}",
            source_workspace.display()
        );
        println!("Checked for: memory/brain.db, MEMORY.md, memory/*.md");
        return Ok(());
    }

    let project = config.kumiho.memory_project.clone();
    let space_path = format!("/{project}/{OPENCLAW_SPACE_NAME}");

    if dry_run {
        println!("🔎 Dry run: OpenClaw migration preview");
        println!("  Source: {}", source_workspace.display());
        println!(
            "  Target: Kumiho {} (space {})",
            config.kumiho.api_url, space_path
        );
        println!("  Candidates: {}", entries.len());
        println!("    - from sqlite:   {}", stats.from_sqlite);
        println!("    - from markdown: {}", stats.from_markdown);
        println!();
        println!("Run without --dry-run to import these entries.");
        return Ok(());
    }

    if let Some(backup_dir) = backup_target_memory(&config.workspace_dir)? {
        println!("🛟 Backup created: {}", backup_dir.display());
    }

    let client = build_client_from_config(config);

    // Ensure project + space exist.
    client
        .ensure_project(&project)
        .await
        .map_err(kumiho_err_ctx("ensure project"))?;
    client
        .ensure_child_space(&project, &format!("/{project}"), OPENCLAW_SPACE_NAME)
        .await
        .map_err(kumiho_err_ctx("ensure space"))?;

    // Pre-fetch existing items for dedup.
    let existing_items: Vec<ItemResponse> = client
        .list_items(&space_path, false)
        .await
        .map_err(kumiho_err_ctx("list existing items"))?;
    let mut existing_by_slug: HashMap<String, ItemResponse> = existing_items
        .into_iter()
        .map(|item| (item.item_name.clone(), item))
        .collect();

    for (idx, entry) in entries.into_iter().enumerate() {
        let key_raw = if entry.key.trim().is_empty() {
            format!("openclaw_{idx}")
        } else {
            entry.key.clone()
        };
        let mut slug = slugify(&key_raw);
        if slug.is_empty() {
            slug = format!("openclaw-{idx}");
        }

        // If an item with this slug exists, compare its latest revision's content.
        if let Some(existing) = existing_by_slug.get(&slug) {
            let latest = client.get_latest_revision(&existing.kref).await.ok();
            let existing_content = latest
                .as_ref()
                .and_then(|rev| rev.metadata.get("content"))
                .map(std::string::String::as_str)
                .unwrap_or_default();

            if existing_content.trim() == entry.content.trim() {
                stats.skipped_unchanged += 1;
                continue;
            }

            slug = next_available_slug(&slug, &existing_by_slug);
            stats.renamed_conflicts += 1;
        }

        let item = client
            .create_item(&space_path, &slug, MEMORY_ITEM_KIND, HashMap::new())
            .await
            .map_err(kumiho_err_ctx("create item"))?;

        let metadata = revision_metadata(&entry, &key_raw);
        client
            .create_revision(&item.kref, metadata)
            .await
            .map_err(kumiho_err_ctx("create revision"))?;

        existing_by_slug.insert(slug, item);
        stats.imported += 1;
    }

    println!("✅ OpenClaw memory migration complete");
    println!("  Source: {}", source_workspace.display());
    println!(
        "  Target: Kumiho {} (space {})",
        config.kumiho.api_url, space_path
    );
    println!("  Imported:          {}", stats.imported);
    println!("  Skipped unchanged: {}", stats.skipped_unchanged);
    println!("  Renamed conflicts: {}", stats.renamed_conflicts);
    println!("  Source sqlite rows:{}", stats.from_sqlite);
    println!("  Source markdown:   {}", stats.from_markdown);

    Ok(())
}

fn revision_metadata(entry: &SourceEntry, original_key: &str) -> HashMap<String, String> {
    let mut meta = HashMap::new();
    meta.insert("content".into(), entry.content.clone());
    meta.insert("category".into(), entry.category.to_string());
    meta.insert("key".into(), original_key.to_string());
    meta.insert("migrated_from".into(), "openclaw".into());
    meta.insert("imported_at".into(), Utc::now().to_rfc3339());
    meta
}

fn next_available_slug(base: &str, existing: &HashMap<String, ItemResponse>) -> String {
    for i in 2..=10_000 {
        let candidate = format!("{base}-{i}");
        if !existing.contains_key(&candidate) {
            return candidate;
        }
    }
    format!("{base}-{}", Utc::now().timestamp_millis())
}

fn kumiho_err_ctx(action: &'static str) -> impl FnOnce(KumihoError) -> anyhow::Error {
    move |e| match e {
        KumihoError::Unreachable(err) => anyhow::anyhow!(
            "Kumiho service unreachable while attempting to {action}: {err}. \
             Check that Kumiho is running and that `kumiho.api_url` points to it."
        ),
        other => anyhow::anyhow!("Kumiho error while attempting to {action}: {other}"),
    }
}

fn collect_source_entries(
    source_workspace: &Path,
    stats: &mut MigrationStats,
) -> Result<Vec<SourceEntry>> {
    let mut entries = Vec::new();

    let sqlite_path = source_workspace.join("memory").join("brain.db");
    let sqlite_entries = read_openclaw_sqlite_entries(&sqlite_path)?;
    stats.from_sqlite = sqlite_entries.len();
    entries.extend(sqlite_entries);

    let markdown_entries = read_openclaw_markdown_entries(source_workspace)?;
    stats.from_markdown = markdown_entries.len();
    entries.extend(markdown_entries);

    // De-dup exact duplicates to make re-runs deterministic.
    let mut seen = HashSet::new();
    entries.retain(|entry| {
        let sig = format!("{}\u{0}{}\u{0}{}", entry.key, entry.content, entry.category);
        seen.insert(sig)
    });

    Ok(entries)
}

fn read_openclaw_sqlite_entries(db_path: &Path) -> Result<Vec<SourceEntry>> {
    if !db_path.exists() {
        return Ok(Vec::new());
    }

    let conn = Connection::open_with_flags(db_path, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .with_context(|| format!("Failed to open source db {}", db_path.display()))?;

    let table_exists: Option<String> = conn
        .query_row(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories' LIMIT 1",
            [],
            |row| row.get(0),
        )
        .optional()?;

    if table_exists.is_none() {
        return Ok(Vec::new());
    }

    let columns = table_columns(&conn, "memories")?;
    let key_expr = pick_column_expr(&columns, &["key", "id", "name"], "CAST(rowid AS TEXT)");
    let Some(content_expr) =
        pick_optional_column_expr(&columns, &["content", "value", "text", "memory"])
    else {
        bail!("OpenClaw memories table found but no content-like column was detected");
    };
    let category_expr = pick_column_expr(&columns, &["category", "kind", "type"], "'core'");

    let sql = format!(
        "SELECT {key_expr} AS key, {content_expr} AS content, {category_expr} AS category FROM memories"
    );

    let mut stmt = conn.prepare(&sql)?;
    let mut rows = stmt.query([])?;

    let mut entries = Vec::new();
    let mut idx = 0_usize;

    while let Some(row) = rows.next()? {
        let key: String = row
            .get(0)
            .unwrap_or_else(|_| format!("openclaw_sqlite_{idx}"));
        let content: String = row.get(1).unwrap_or_default();
        let category_raw: String = row.get(2).unwrap_or_else(|_| "core".to_string());

        if content.trim().is_empty() {
            continue;
        }

        entries.push(SourceEntry {
            key: normalize_key(&key, idx),
            content: content.trim().to_string(),
            category: parse_category(&category_raw),
        });

        idx += 1;
    }

    Ok(entries)
}

fn read_openclaw_markdown_entries(source_workspace: &Path) -> Result<Vec<SourceEntry>> {
    let mut all = Vec::new();

    let core_path = source_workspace.join("MEMORY.md");
    if core_path.exists() {
        let content = fs::read_to_string(&core_path)?;
        all.extend(parse_markdown_file(
            &core_path,
            &content,
            MemoryCategory::Core,
            "openclaw_core",
        ));
    }

    let daily_dir = source_workspace.join("memory");
    if daily_dir.exists() {
        for file in fs::read_dir(&daily_dir)? {
            let file = file?;
            let path = file.path();
            if path.extension().and_then(|ext| ext.to_str()) != Some("md") {
                continue;
            }
            let content = fs::read_to_string(&path)?;
            let stem = path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or("openclaw_daily");
            all.extend(parse_markdown_file(
                &path,
                &content,
                MemoryCategory::Daily,
                stem,
            ));
        }
    }

    Ok(all)
}

#[allow(clippy::needless_pass_by_value)]
fn parse_markdown_file(
    _path: &Path,
    content: &str,
    default_category: MemoryCategory,
    stem: &str,
) -> Vec<SourceEntry> {
    let mut entries = Vec::new();

    for (idx, raw_line) in content.lines().enumerate() {
        let trimmed = raw_line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }

        let line = trimmed.strip_prefix("- ").unwrap_or(trimmed);
        let (key, text) = match parse_structured_memory_line(line) {
            Some((k, v)) => (normalize_key(k, idx), v.trim().to_string()),
            None => (
                format!("openclaw_{stem}_{}", idx + 1),
                line.trim().to_string(),
            ),
        };

        if text.is_empty() {
            continue;
        }

        entries.push(SourceEntry {
            key,
            content: text,
            category: default_category.clone(),
        });
    }

    entries
}

fn parse_structured_memory_line(line: &str) -> Option<(&str, &str)> {
    if !line.starts_with("**") {
        return None;
    }

    let rest = line.strip_prefix("**")?;
    let key_end = rest.find("**:")?;
    let key = rest.get(..key_end)?.trim();
    let value = rest.get(key_end + 3..)?.trim();

    if key.is_empty() || value.is_empty() {
        return None;
    }

    Some((key, value))
}

fn parse_category(raw: &str) -> MemoryCategory {
    match raw.trim().to_ascii_lowercase().as_str() {
        "core" | "" => MemoryCategory::Core,
        "daily" => MemoryCategory::Daily,
        "conversation" => MemoryCategory::Conversation,
        other => MemoryCategory::Custom(other.to_string()),
    }
}

fn normalize_key(key: &str, fallback_idx: usize) -> String {
    let trimmed = key.trim();
    if trimmed.is_empty() {
        return format!("openclaw_{fallback_idx}");
    }
    trimmed.to_string()
}

fn table_columns(conn: &Connection, table: &str) -> Result<Vec<String>> {
    let pragma = format!("PRAGMA table_info({table})");
    let mut stmt = conn.prepare(&pragma)?;
    let rows = stmt.query_map([], |row| row.get::<_, String>(1))?;

    let mut cols = Vec::new();
    for col in rows {
        cols.push(col?.to_ascii_lowercase());
    }

    Ok(cols)
}

fn pick_optional_column_expr(columns: &[String], candidates: &[&str]) -> Option<String> {
    candidates
        .iter()
        .find(|candidate| columns.iter().any(|c| c == *candidate))
        .map(std::string::ToString::to_string)
}

fn pick_column_expr(columns: &[String], candidates: &[&str], fallback: &str) -> String {
    pick_optional_column_expr(columns, candidates).unwrap_or_else(|| fallback.to_string())
}

fn resolve_openclaw_workspace(source: Option<PathBuf>) -> Result<PathBuf> {
    if let Some(src) = source {
        return Ok(src);
    }

    let home = UserDirs::new()
        .map(|u| u.home_dir().to_path_buf())
        .context("Could not find home directory")?;

    Ok(home.join(".openclaw").join("workspace"))
}

fn paths_equal(a: &Path, b: &Path) -> bool {
    match (fs::canonicalize(a), fs::canonicalize(b)) {
        (Ok(a), Ok(b)) => a == b,
        _ => a == b,
    }
}

fn backup_target_memory(workspace_dir: &Path) -> Result<Option<PathBuf>> {
    let timestamp = chrono::Local::now().format("%Y%m%d-%H%M%S").to_string();
    let backup_root = workspace_dir
        .join("memory")
        .join("migrations")
        .join(format!("openclaw-{timestamp}"));

    let mut copied_any = false;
    fs::create_dir_all(&backup_root)?;

    let files_to_copy = [
        workspace_dir.join("memory").join("brain.db"),
        workspace_dir.join("MEMORY.md"),
    ];

    for source in files_to_copy {
        if source.exists() {
            let Some(name) = source.file_name() else {
                continue;
            };
            fs::copy(&source, backup_root.join(name))?;
            copied_any = true;
        }
    }

    let daily_dir = workspace_dir.join("memory");
    if daily_dir.exists() {
        let daily_backup = backup_root.join("daily");
        for file in fs::read_dir(&daily_dir)? {
            let file = file?;
            let path = file.path();
            if path.extension().and_then(|ext| ext.to_str()) != Some("md") {
                continue;
            }
            fs::create_dir_all(&daily_backup)?;
            let Some(name) = path.file_name() else {
                continue;
            };
            fs::copy(&path, daily_backup.join(name))?;
            copied_any = true;
        }
    }

    if copied_any {
        Ok(Some(backup_root))
    } else {
        let _ = fs::remove_dir_all(&backup_root);
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rusqlite::params;
    use tempfile::TempDir;

    #[test]
    fn parse_structured_markdown_line() {
        let line = "**user_pref**: likes Rust";
        let parsed = parse_structured_memory_line(line).unwrap();
        assert_eq!(parsed.0, "user_pref");
        assert_eq!(parsed.1, "likes Rust");
    }

    #[test]
    fn parse_unstructured_markdown_generates_key() {
        let entries = parse_markdown_file(
            Path::new("/tmp/MEMORY.md"),
            "- plain note",
            MemoryCategory::Core,
            "core",
        );
        assert_eq!(entries.len(), 1);
        assert!(entries[0].key.starts_with("openclaw_core_"));
        assert_eq!(entries[0].content, "plain note");
    }

    #[test]
    fn sqlite_reader_supports_legacy_value_column() {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("brain.db");
        let conn = Connection::open(&db_path).unwrap();

        conn.execute_batch("CREATE TABLE memories (key TEXT, value TEXT, type TEXT);")
            .unwrap();
        conn.execute(
            "INSERT INTO memories (key, value, type) VALUES (?1, ?2, ?3)",
            params!["legacy_key", "legacy_value", "daily"],
        )
        .unwrap();

        let rows = read_openclaw_sqlite_entries(&db_path).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].key, "legacy_key");
        assert_eq!(rows[0].content, "legacy_value");
        assert_eq!(rows[0].category, MemoryCategory::Daily);
    }

    #[test]
    fn parse_category_handles_all_variants() {
        assert_eq!(parse_category("core"), MemoryCategory::Core);
        assert_eq!(parse_category("daily"), MemoryCategory::Daily);
        assert_eq!(parse_category("conversation"), MemoryCategory::Conversation);
        assert_eq!(parse_category(""), MemoryCategory::Core);
        assert_eq!(
            parse_category("custom_type"),
            MemoryCategory::Custom("custom_type".to_string())
        );
    }

    #[test]
    fn parse_category_case_insensitive() {
        assert_eq!(parse_category("CORE"), MemoryCategory::Core);
        assert_eq!(parse_category("Daily"), MemoryCategory::Daily);
        assert_eq!(parse_category("CONVERSATION"), MemoryCategory::Conversation);
    }

    #[test]
    fn normalize_key_handles_empty_string() {
        let key = normalize_key("", 42);
        assert_eq!(key, "openclaw_42");
    }

    #[test]
    fn normalize_key_trims_whitespace() {
        let key = normalize_key("  my_key  ", 0);
        assert_eq!(key, "my_key");
    }

    #[test]
    fn parse_structured_markdown_rejects_empty_key() {
        assert!(parse_structured_memory_line("****:value").is_none());
    }

    #[test]
    fn parse_structured_markdown_rejects_empty_value() {
        assert!(parse_structured_memory_line("**key**:").is_none());
    }

    #[test]
    fn parse_structured_markdown_rejects_no_stars() {
        assert!(parse_structured_memory_line("key: value").is_none());
    }

    #[test]
    fn migration_skips_empty_content() {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("brain.db");
        let conn = Connection::open(&db_path).unwrap();

        conn.execute_batch("CREATE TABLE memories (key TEXT, content TEXT, category TEXT);")
            .unwrap();
        conn.execute(
            "INSERT INTO memories (key, content, category) VALUES (?1, ?2, ?3)",
            params!["empty_key", "   ", "core"],
        )
        .unwrap();

        let rows = read_openclaw_sqlite_entries(&db_path).unwrap();
        assert_eq!(
            rows.len(),
            0,
            "entries with empty/whitespace content must be skipped"
        );
    }

    #[test]
    fn backup_creates_timestamped_directory() {
        let tmp = TempDir::new().unwrap();
        let mem_dir = tmp.path().join("memory");
        std::fs::create_dir_all(&mem_dir).unwrap();

        let db_path = mem_dir.join("brain.db");
        std::fs::write(&db_path, "fake db content").unwrap();

        let result = backup_target_memory(tmp.path()).unwrap();
        assert!(
            result.is_some(),
            "backup should be created when files exist"
        );

        let backup_dir = result.unwrap();
        assert!(backup_dir.exists());
        assert!(
            backup_dir.to_string_lossy().contains("openclaw-"),
            "backup dir must contain openclaw- prefix"
        );
    }

    #[test]
    fn backup_returns_none_when_no_files() {
        let tmp = TempDir::new().unwrap();
        let result = backup_target_memory(tmp.path()).unwrap();
        assert!(
            result.is_none(),
            "backup should return None when no files to backup"
        );
    }

    #[test]
    fn next_available_slug_increments_suffix() {
        let mut existing: HashMap<String, ItemResponse> = HashMap::new();
        existing.insert("key".into(), dummy_item("key"));
        existing.insert("key-2".into(), dummy_item("key-2"));

        let next = next_available_slug("key", &existing);
        assert_eq!(next, "key-3");
    }

    fn dummy_item(name: &str) -> ItemResponse {
        ItemResponse {
            kref: format!("kref://dummy/{name}"),
            name: name.into(),
            item_name: name.into(),
            kind: MEMORY_ITEM_KIND.into(),
            deprecated: false,
            created_at: None,
            author: None,
            username: None,
            author_display: None,
            metadata: HashMap::new(),
        }
    }
}
