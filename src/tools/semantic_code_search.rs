use super::traits::{Tool, ToolResult};
use crate::security::SecurityPolicy;
use async_trait::async_trait;
use serde_json::json;
use std::collections::VecDeque;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

const DEFAULT_TOP_K: usize = 8;
const MAX_TOP_K: usize = 25;
const SEARCH_TIMEOUT_SECS: u64 = 30;
const OUTPUT_MAX_CHARS: usize = 16_000;
const BUILTIN_FALLBACK_MAX_FILES: usize = 5_000;
const BUILTIN_FALLBACK_MAX_FILE_BYTES: u64 = 1_000_000;

pub struct SemanticCodeSearchTool {
    security: Arc<SecurityPolicy>,
}

impl SemanticCodeSearchTool {
    pub fn new(security: Arc<SecurityPolicy>) -> Self {
        Self { security }
    }
}

#[async_trait]
impl Tool for SemanticCodeSearchTool {
    fn name(&self) -> &str {
        "semantic_code_search"
    }

    fn description(&self) -> &str {
        "Find relevant code snippets with a token-efficient search adapter. Uses Semble when installed, then ripgrep, then a built-in literal scan."
    }

    fn parameters_schema(&self) -> serde_json::Value {
        json!({
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language or symbol query, e.g. 'context compression for tool outputs'."
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search, relative to workspace. Defaults to '.'.",
                    "default": "."
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum number of snippets/results to return. Defaults to 8, max 25.",
                    "default": DEFAULT_TOP_K
                },
                "backend": {
                    "type": "string",
                    "description": "Search backend: 'auto', 'semble', or 'literal'. 'auto' prefers Semble; literal uses ripgrep or the built-in scanner.",
                    "enum": ["auto", "semble", "literal"],
                    "default": "auto"
                }
            },
            "required": ["query"]
        })
    }

    async fn execute(&self, args: serde_json::Value) -> anyhow::Result<ToolResult> {
        let query = args
            .get("query")
            .and_then(serde_json::Value::as_str)
            .map(str::trim)
            .filter(|query| !query.is_empty())
            .ok_or_else(|| anyhow::anyhow!("Missing 'query' parameter"))?;
        let path = args
            .get("path")
            .and_then(serde_json::Value::as_str)
            .unwrap_or(".");
        let backend = args
            .get("backend")
            .and_then(serde_json::Value::as_str)
            .unwrap_or("auto");
        if !matches!(backend, "auto" | "semble" | "literal") {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some("Invalid backend. Use 'auto', 'semble', or 'literal'.".into()),
            });
        }
        let top_k = args
            .get("top_k")
            .and_then(serde_json::Value::as_u64)
            .and_then(|v| usize::try_from(v).ok())
            .unwrap_or(DEFAULT_TOP_K)
            .clamp(1, MAX_TOP_K);

        if self.security.is_rate_limited() {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some("Rate limit exceeded: too many actions in the last hour".into()),
            });
        }

        let resolved_path = match resolve_allowed_search_path(&self.security, path) {
            Ok(path) => path,
            Err(error) => {
                return Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some(error),
                });
            }
        };

        if !self.security.record_action() {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some("Rate limit exceeded: action budget exhausted".into()),
            });
        }

        let (raw_output, command_hint) = if backend != "literal" && which::which("semble").is_ok() {
            (
                run_semble(query, &resolved_path, top_k).await?,
                Some("semble search"),
            )
        } else if backend == "semble" {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(
                    "Semble is not installed. Install with `pip install semble` or `uv tool install semble`, or use backend='literal'.".into(),
                ),
            });
        } else {
            (
                run_literal_fallback(query, &resolved_path, top_k).await?,
                Some("rg"),
            )
        };

        let compressed = crate::agent::token_compression::compress_tool_output(
            "semantic_code_search",
            &raw_output,
            OUTPUT_MAX_CHARS,
            command_hint,
        );

        Ok(ToolResult {
            success: true,
            output: compressed.text,
            error: None,
        })
    }
}

fn resolve_allowed_search_path(security: &SecurityPolicy, path: &str) -> Result<PathBuf, String> {
    if Path::new(path).is_absolute() && !security.is_under_allowed_root(path) {
        return Err("Absolute paths are not allowed. Use a relative path.".into());
    }
    if path.contains("../") || path.contains("..\\") || path == ".." {
        return Err("Path traversal ('..') is not allowed.".into());
    }
    if !security.is_path_allowed(path) {
        return Err(format!("Path '{path}' is not allowed by security policy."));
    }
    let resolved = security.resolve_tool_path(path);
    let canonical = std::fs::canonicalize(&resolved)
        .map_err(|error| format!("Cannot resolve path '{path}': {error}"))?;
    if !security.is_resolved_path_allowed(&canonical) {
        return Err(format!(
            "Resolved path for '{path}' is outside the allowed workspace."
        ));
    }
    Ok(canonical)
}

async fn run_semble(query: &str, path: &Path, top_k: usize) -> anyhow::Result<String> {
    let mut cmd = tokio::process::Command::new("semble");
    cmd.arg("search")
        .arg(query)
        .arg(path)
        .arg("--top-k")
        .arg(top_k.to_string())
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    run_command(cmd, "semble search").await
}

async fn run_literal_fallback(query: &str, path: &Path, top_k: usize) -> anyhow::Result<String> {
    let terms = query_terms(query);
    if terms.is_empty() {
        return Ok("No searchable terms found in query.".to_string());
    }

    if which::which("rg").is_err() {
        return run_builtin_literal_fallback(query, path, top_k).await;
    }

    let pattern = terms
        .iter()
        .map(|term| regex::escape(term))
        .collect::<Vec<_>>()
        .join("|");
    let max_count = (top_k * 6).to_string();

    let mut cmd = tokio::process::Command::new("rg");
    cmd.arg("--line-number")
        .arg("--with-filename")
        .arg("--no-heading")
        .arg("--ignore-case")
        .arg("--max-count")
        .arg(max_count)
        .arg("--")
        .arg(pattern)
        .arg(path)
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    let raw = run_command(cmd, "rg fallback").await?;
    if raw.trim().is_empty() {
        Ok(format!(
            "No literal fallback matches for query terms: {}",
            terms.join(", ")
        ))
    } else {
        let mut out = String::new();
        out.push_str("[semantic_code_search fallback: Semble unavailable; literal rg results]\n");
        out.push_str(&raw);
        Ok(out)
    }
}

async fn run_builtin_literal_fallback(
    query: &str,
    path: &Path,
    top_k: usize,
) -> anyhow::Result<String> {
    let query = query.to_string();
    let path = path.to_path_buf();
    tokio::task::spawn_blocking(move || scan_literal_builtin(&query, &path, top_k))
        .await
        .map_err(|error| anyhow::anyhow!("built-in search task failed: {error}"))?
}

fn scan_literal_builtin(query: &str, path: &Path, top_k: usize) -> anyhow::Result<String> {
    let terms = query_terms(query);
    if terms.is_empty() {
        return Ok("No searchable terms found in query.".to_string());
    }

    let hit_limit = top_k.saturating_mul(6).max(1);
    let mut hits = Vec::new();
    let mut files_scanned = 0usize;

    if path.is_file() {
        scan_file_for_terms(
            path,
            path.parent().unwrap_or(path),
            &terms,
            hit_limit,
            &mut hits,
        );
        files_scanned = 1;
    } else {
        let search_root = path.to_path_buf();
        let mut queue = VecDeque::from([path.to_path_buf()]);
        while let Some(dir) = queue.pop_front() {
            let Ok(entries) = fs::read_dir(&dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let path = entry.path();
                let Ok(file_type) = entry.file_type() else {
                    continue;
                };
                if file_type.is_dir() {
                    if !should_skip_search_dir(&path) {
                        queue.push_back(path);
                    }
                    continue;
                }
                if !file_type.is_file() || should_skip_search_file(&path) {
                    continue;
                }
                files_scanned += 1;
                scan_file_for_terms(&path, &search_root, &terms, hit_limit, &mut hits);
                if files_scanned >= BUILTIN_FALLBACK_MAX_FILES || hits.len() >= hit_limit {
                    break;
                }
            }
            if files_scanned >= BUILTIN_FALLBACK_MAX_FILES || hits.len() >= hit_limit {
                break;
            }
        }
    }

    let mut out = String::new();
    out.push_str(
        "[semantic_code_search fallback: Semble and ripgrep unavailable; built-in literal scan]\n",
    );
    out.push_str(&format!(
        "[query_terms: {}; files_scanned: {}; hits: {}]\n",
        terms.join(", "),
        files_scanned,
        hits.len()
    ));
    if hits.is_empty() {
        out.push_str("No built-in literal fallback matches found.");
    } else {
        out.push_str(&hits.join("\n"));
    }
    Ok(out)
}

fn scan_file_for_terms(
    path: &Path,
    root: &Path,
    terms: &[String],
    hit_limit: usize,
    hits: &mut Vec<String>,
) {
    if hits.len() >= hit_limit {
        return;
    }
    let Ok(metadata) = fs::metadata(path) else {
        return;
    };
    if metadata.len() > BUILTIN_FALLBACK_MAX_FILE_BYTES {
        return;
    }
    let Ok(content) = fs::read_to_string(path) else {
        return;
    };
    if content.contains('\0') {
        return;
    }

    let display = path
        .strip_prefix(root)
        .ok()
        .filter(|relative| !relative.as_os_str().is_empty())
        .unwrap_or(path)
        .display()
        .to_string();
    for (index, line) in content.lines().enumerate() {
        let lower = line.to_ascii_lowercase();
        if terms.iter().any(|term| lower.contains(term)) {
            hits.push(format!("{}:{}:{}", display, index + 1, line.trim()));
            if hits.len() >= hit_limit {
                break;
            }
        }
    }
}

fn should_skip_search_dir(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    matches!(
        name.to_ascii_lowercase().as_str(),
        ".git"
            | ".hg"
            | ".svn"
            | "target"
            | "node_modules"
            | ".next"
            | "dist"
            | "build"
            | ".venv"
            | "venv"
            | "__pycache__"
    )
}

fn should_skip_search_file(path: &Path) -> bool {
    let Some(ext) = path.extension().and_then(|ext| ext.to_str()) else {
        return false;
    };
    matches!(
        ext.to_ascii_lowercase().as_str(),
        "png"
            | "jpg"
            | "jpeg"
            | "gif"
            | "webp"
            | "ico"
            | "pdf"
            | "zip"
            | "gz"
            | "tar"
            | "7z"
            | "exe"
            | "dll"
            | "so"
            | "dylib"
            | "pdb"
            | "lock"
    )
}

async fn run_command(mut cmd: tokio::process::Command, label: &str) -> anyhow::Result<String> {
    let output =
        match tokio::time::timeout(Duration::from_secs(SEARCH_TIMEOUT_SECS), cmd.output()).await {
            Ok(Ok(output)) => output,
            Ok(Err(error)) => anyhow::bail!("{label} failed to start: {error}"),
            Err(_) => anyhow::bail!("{label} timed out after {SEARCH_TIMEOUT_SECS}s"),
        };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if output.status.success() || output.status.code() == Some(1) {
        Ok(stdout.to_string())
    } else {
        anyhow::bail!("{label} failed: {}", stderr.trim())
    }
}

fn query_terms(query: &str) -> Vec<String> {
    let mut terms = Vec::new();
    for raw in query.split(|ch: char| !ch.is_ascii_alphanumeric() && ch != '_' && ch != '-') {
        let term = raw.trim();
        if term.len() < 3 {
            continue;
        }
        let lower = term.to_ascii_lowercase();
        if matches!(
            lower.as_str(),
            "the" | "and" | "for" | "with" | "where" | "how" | "what" | "does" | "this"
        ) {
            continue;
        }
        if !terms.contains(&lower) {
            terms.push(lower);
        }
        if terms.len() >= 8 {
            break;
        }
    }
    terms
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::security::{AutonomyLevel, SecurityPolicy};
    use tempfile::TempDir;

    fn test_security(workspace: PathBuf) -> Arc<SecurityPolicy> {
        Arc::new(SecurityPolicy {
            autonomy: AutonomyLevel::Supervised,
            workspace_dir: workspace,
            ..SecurityPolicy::default()
        })
    }

    #[test]
    fn schema_has_expected_fields() {
        let tool = SemanticCodeSearchTool::new(test_security(std::env::temp_dir()));
        let schema = tool.parameters_schema();
        assert!(schema["properties"]["query"].is_object());
        assert!(schema["properties"]["backend"].is_object());
    }

    #[test]
    fn query_terms_drop_stop_words() {
        let terms = query_terms("where does context compression handle tool output?");
        assert!(terms.contains(&"context".to_string()));
        assert!(terms.contains(&"compression".to_string()));
        assert!(!terms.contains(&"where".to_string()));
    }

    #[test]
    fn builtin_literal_fallback_finds_text_without_external_tools() {
        let dir = TempDir::new().unwrap();
        let src_dir = dir.path().join("src");
        std::fs::create_dir_all(&src_dir).unwrap();
        std::fs::write(
            src_dir.join("lib.rs"),
            "fn compress_tool_output() {\n    // context compression keeps signal lines\n}\n",
        )
        .unwrap();

        let out = scan_literal_builtin("context compression", dir.path(), 4).unwrap();

        assert!(out.contains("built-in literal scan"));
        assert!(out.contains("lib.rs"));
        assert!(out.contains("context compression keeps signal lines"));
    }

    #[tokio::test]
    async fn missing_query_is_error() {
        let dir = TempDir::new().unwrap();
        let tool = SemanticCodeSearchTool::new(test_security(dir.path().to_path_buf()));
        let result = tool.execute(json!({})).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn invalid_backend_returns_tool_error() {
        let dir = TempDir::new().unwrap();
        let tool = SemanticCodeSearchTool::new(test_security(dir.path().to_path_buf()));
        let result = tool
            .execute(json!({"query": "context compression", "backend": "vector"}))
            .await
            .unwrap();

        assert!(!result.success);
        assert_eq!(
            result.error.as_deref(),
            Some("Invalid backend. Use 'auto', 'semble', or 'literal'.")
        );
    }
}
