//! Skill meta-tools exposed by the in-process MCP server.
//!
//! Instead of exposing every user skill as a standalone MCP tool (which could
//! easily balloon to 50+ entries), this module provides three compact
//! meta-tools that let external CLIs discover and invoke skills on demand:
//!
//! - `skills_list`     → no args; returns a JSON array of skill summaries.
//! - `skills_describe` → `{ skill_id }`; returns the full skill body + metadata.
//! - `skills_execute`  → `{ skill_id, arguments? }`; dispatches to the skill's
//!                       named `[[tools]]` entry OR returns the body for
//!                       markdown-only skills for the calling model to follow.
//!
//! All three are backed by the shared `crate::skills::load_skills_*` helpers
//! (the same code path `ReadSkillTool` uses) so they stay consistent with
//! the CLI-side view and don't duplicate the on-disk skill lookup.
//!
//! # Testability
//!
//! The tools accept a `SkillSource` so unit tests can hand in a tmp dir or a
//! mocked executor without needing to reach into the real `~/.revka` tree.

use crate::security::SecurityPolicy;
use crate::skills::Skill;
use crate::tools::traits::{Tool, ToolResult};
use async_trait::async_trait;
use serde_json::{Value, json};
use std::path::PathBuf;
use std::sync::Arc;

/// Abstraction over the skill store so we can inject a mock in tests.
///
/// The real implementation walks `workspace_dir/skills/` (and optionally the
/// open-skills mirror); tests can substitute a pre-built `Vec<Skill>`.
pub trait SkillSource: Send + Sync {
    fn load(&self) -> Vec<Skill>;
}

/// Production skill source: reads from disk using the same entry points as
/// `ReadSkillTool` + the gateway's `skills_to_prompt` path.
pub struct DiskSkillSource {
    workspace_dir: PathBuf,
    open_skills_enabled: bool,
    open_skills_dir: Option<String>,
}

impl DiskSkillSource {
    pub fn new(
        workspace_dir: PathBuf,
        open_skills_enabled: bool,
        open_skills_dir: Option<String>,
    ) -> Self {
        Self {
            workspace_dir,
            open_skills_enabled,
            open_skills_dir,
        }
    }
}

impl SkillSource for DiskSkillSource {
    fn load(&self) -> Vec<Skill> {
        crate::skills::load_skills_with_open_skills_settings(
            &self.workspace_dir,
            self.open_skills_enabled,
            self.open_skills_dir.as_deref(),
        )
    }
}

fn summarize_skill(skill: &Skill) -> Value {
    let location = skill
        .location
        .as_ref()
        .map(|p| p.to_string_lossy().into_owned());
    json!({
        "id": skill.name,
        "name": skill.name,
        "description": skill.description,
        "version": skill.version,
        "author": skill.author,
        "tags": skill.tags,
        "location": location,
        "tool_count": skill.tools.len(),
    })
}

fn find_skill<'a>(skills: &'a [Skill], id: &str) -> Option<&'a Skill> {
    skills
        .iter()
        .find(|s| s.name.eq_ignore_ascii_case(id.trim()))
}

// ── skills_list ──────────────────────────────────────────────────────────

/// List every skill visible to the daemon.
pub struct SkillsListTool {
    source: Arc<dyn SkillSource>,
}

impl SkillsListTool {
    pub fn new(source: Arc<dyn SkillSource>) -> Self {
        Self { source }
    }
}

#[async_trait]
impl Tool for SkillsListTool {
    fn name(&self) -> &str {
        "skills_list"
    }

    fn description(&self) -> &str {
        "List all Revka skills available to the local daemon. Returns a JSON array of { id, name, description, version, tags, tool_count, location } objects."
    }

    fn parameters_schema(&self) -> Value {
        json!({ "type": "object", "properties": {}, "additionalProperties": false })
    }

    async fn execute(&self, _args: Value) -> anyhow::Result<ToolResult> {
        let skills = self.source.load();
        let payload: Vec<Value> = skills.iter().map(summarize_skill).collect();
        Ok(ToolResult {
            success: true,
            output: serde_json::to_string_pretty(&payload)?,
            error: None,
        })
    }
}

// ── skills_describe ──────────────────────────────────────────────────────

/// Return the full markdown/manifest body of a single skill.
pub struct SkillsDescribeTool {
    source: Arc<dyn SkillSource>,
}

impl SkillsDescribeTool {
    pub fn new(source: Arc<dyn SkillSource>) -> Self {
        Self { source }
    }
}

#[async_trait]
impl Tool for SkillsDescribeTool {
    fn name(&self) -> &str {
        "skills_describe"
    }

    fn description(&self) -> &str {
        "Return the full body (markdown / SKILL.toml) plus metadata for a single Revka skill by id."
    }

    fn parameters_schema(&self) -> Value {
        json!({
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "Exact skill name/id as returned by skills_list."
                }
            },
            "required": ["skill_id"]
        })
    }

    async fn execute(&self, args: Value) -> anyhow::Result<ToolResult> {
        let id = args
            .get("skill_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty());
        let Some(id) = id else {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some("skills_describe requires `skill_id`".into()),
            });
        };

        let skills = self.source.load();
        let Some(skill) = find_skill(&skills, id) else {
            let mut names: Vec<&str> = skills.iter().map(|s| s.name.as_str()).collect();
            names.sort_unstable();
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(format!(
                    "Unknown skill '{id}'. Available: {}",
                    if names.is_empty() {
                        "none".into()
                    } else {
                        names.join(", ")
                    }
                )),
            });
        };

        let body = if let Some(loc) = &skill.location {
            tokio::fs::read_to_string(loc).await.unwrap_or_default()
        } else {
            String::new()
        };

        let payload = json!({
            "id": skill.name,
            "description": skill.description,
            "version": skill.version,
            "author": skill.author,
            "tags": skill.tags,
            "tools": skill.tools.iter().map(|t| json!({
                "name": t.name,
                "description": t.description,
                "kind": t.kind,
            })).collect::<Vec<_>>(),
            "body": body,
            "location": skill.location.as_ref().map(|p| p.to_string_lossy().into_owned()),
        });

        Ok(ToolResult {
            success: true,
            output: serde_json::to_string_pretty(&payload)?,
            error: None,
        })
    }
}

// ── skills_execute ───────────────────────────────────────────────────────

/// Executor abstraction — produced by tests to capture calls.
#[async_trait]
pub trait SkillExecutor: Send + Sync {
    async fn run(
        &self,
        skill: &Skill,
        sub_tool: Option<&str>,
        arguments: Value,
    ) -> anyhow::Result<ToolResult>;
}

/// Default executor: delegates to the same `SkillShellTool` / `SkillHttpTool`
/// wrappers the agent loop uses. For markdown-only skills (no `[[tools]]`),
/// returns the skill body so the calling model can follow the instructions.
pub struct DefaultSkillExecutor {
    security: Arc<SecurityPolicy>,
}

impl DefaultSkillExecutor {
    pub fn new(security: Arc<SecurityPolicy>) -> Self {
        Self { security }
    }
}

#[async_trait]
impl SkillExecutor for DefaultSkillExecutor {
    async fn run(
        &self,
        skill: &Skill,
        sub_tool: Option<&str>,
        arguments: Value,
    ) -> anyhow::Result<ToolResult> {
        // Markdown-only skill: return body verbatim.
        if skill.tools.is_empty() {
            let body = if let Some(loc) = &skill.location {
                tokio::fs::read_to_string(loc).await.unwrap_or_default()
            } else {
                String::new()
            };
            return Ok(ToolResult {
                success: true,
                output: body,
                error: None,
            });
        }

        // Select the sub-tool: explicit arg wins; otherwise the first entry.
        let tool = if let Some(name) = sub_tool {
            skill.tools.iter().find(|t| t.name == name)
        } else {
            skill.tools.first()
        };

        let Some(tool) = tool else {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(format!(
                    "Skill '{}' has no [[tools]] entry matching '{}'",
                    skill.name,
                    sub_tool.unwrap_or("(first)")
                )),
            });
        };

        match tool.kind.as_str() {
            "shell" | "script" => {
                let t = crate::tools::skill_tool::SkillShellTool::new(
                    &skill.name,
                    tool,
                    self.security.clone(),
                );
                t.execute(arguments).await
            }
            "http" => {
                let t = crate::tools::skill_http::SkillHttpTool::new(&skill.name, tool);
                t.execute(arguments).await
            }
            other => Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(format!("Unsupported skill tool kind: {other}")),
            }),
        }
    }
}

/// MCP tool: `skills_execute`.
pub struct SkillsExecuteTool {
    source: Arc<dyn SkillSource>,
    executor: Arc<dyn SkillExecutor>,
}

impl SkillsExecuteTool {
    pub fn new(source: Arc<dyn SkillSource>, executor: Arc<dyn SkillExecutor>) -> Self {
        Self { source, executor }
    }
}

#[async_trait]
impl Tool for SkillsExecuteTool {
    fn name(&self) -> &str {
        "skills_execute"
    }

    fn description(&self) -> &str {
        "Execute a Revka skill by id. For markdown skills this returns the skill body; for skills with [[tools]] entries it invokes the named sub-tool (or the first one) with the supplied `arguments` object."
    }

    fn parameters_schema(&self) -> Value {
        json!({
            "type": "object",
            "properties": {
                "skill_id": { "type": "string", "description": "Skill id/name." },
                "tool": { "type": "string", "description": "Optional sub-tool name within the skill's [[tools]]." },
                "arguments": { "type": "object", "description": "Arguments for the skill sub-tool." }
            },
            "required": ["skill_id"]
        })
    }

    async fn execute(&self, args: Value) -> anyhow::Result<ToolResult> {
        let id = args
            .get("skill_id")
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty());
        let Some(id) = id else {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some("skills_execute requires `skill_id`".into()),
            });
        };
        let sub = args.get("tool").and_then(Value::as_str);
        let arguments = args.get("arguments").cloned().unwrap_or_else(|| json!({}));

        let skills = self.source.load();
        let Some(skill) = find_skill(&skills, id) else {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(format!("Unknown skill '{id}'")),
            });
        };

        self.executor.run(skill, sub, arguments).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::sync::Mutex;

    struct StaticSource(Vec<Skill>);
    impl SkillSource for StaticSource {
        fn load(&self) -> Vec<Skill> {
            self.0.clone()
        }
    }

    fn skill(name: &str) -> Skill {
        Skill {
            name: name.to_string(),
            description: format!("desc-{name}"),
            version: "0.1.0".into(),
            author: None,
            tags: vec!["t1".into()],
            tools: vec![],
            prompts: vec![],
            location: None,
        }
    }

    #[tokio::test]
    async fn skills_list_returns_store_contents() {
        let source: Arc<dyn SkillSource> =
            Arc::new(StaticSource(vec![skill("alpha"), skill("beta")]));
        let tool = SkillsListTool::new(source);
        let res = tool.execute(json!({})).await.unwrap();
        assert!(res.success);
        let v: Value = serde_json::from_str(&res.output).unwrap();
        let arr = v.as_array().unwrap();
        assert_eq!(arr.len(), 2);
        assert_eq!(arr[0]["id"], "alpha");
        assert_eq!(arr[1]["id"], "beta");
    }

    #[tokio::test]
    async fn skills_list_empty_returns_empty_array() {
        let source: Arc<dyn SkillSource> = Arc::new(StaticSource(vec![]));
        let tool = SkillsListTool::new(source);
        let res = tool.execute(json!({})).await.unwrap();
        assert!(res.success);
        assert_eq!(res.output.trim(), "[]");
    }

    #[tokio::test]
    async fn skills_describe_unknown_skill_errors_with_available_list() {
        let source: Arc<dyn SkillSource> = Arc::new(StaticSource(vec![skill("alpha")]));
        let tool = SkillsDescribeTool::new(source);
        let res = tool.execute(json!({ "skill_id": "zeta" })).await.unwrap();
        assert!(!res.success);
        assert!(res.error.as_deref().unwrap().contains("alpha"));
    }

    struct RecordingExecutor {
        calls: Mutex<Vec<(String, Option<String>, Value)>>,
        response: String,
    }
    #[async_trait]
    impl SkillExecutor for RecordingExecutor {
        async fn run(
            &self,
            skill: &Skill,
            sub: Option<&str>,
            arguments: Value,
        ) -> anyhow::Result<ToolResult> {
            self.calls.lock().unwrap().push((
                skill.name.clone(),
                sub.map(str::to_string),
                arguments,
            ));
            Ok(ToolResult {
                success: true,
                output: self.response.clone(),
                error: None,
            })
        }
    }

    #[tokio::test]
    async fn skills_execute_dispatches_to_executor_with_arguments() {
        let source: Arc<dyn SkillSource> = Arc::new(StaticSource(vec![skill("deploy")]));
        let exec = Arc::new(RecordingExecutor {
            calls: Mutex::new(Vec::new()),
            response: "shipped!".into(),
        });
        let tool = SkillsExecuteTool::new(source, exec.clone());
        let res = tool
            .execute(json!({
                "skill_id": "deploy",
                "tool": "run",
                "arguments": { "env": "prod" }
            }))
            .await
            .unwrap();
        assert!(res.success);
        assert_eq!(res.output, "shipped!");
        let calls = exec.calls.lock().unwrap();
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].0, "deploy");
        assert_eq!(calls[0].1.as_deref(), Some("run"));
        assert_eq!(calls[0].2["env"], "prod");
    }

    #[tokio::test]
    async fn skills_execute_markdown_skill_returns_body() {
        let tmp = tempfile::TempDir::new().unwrap();
        let skill_path = tmp.path().join("DEPLOY.md");
        std::fs::write(&skill_path, "# Deploy\nmarkdown body").unwrap();
        let mut s = skill("deploy");
        s.location = Some(skill_path);
        let source: Arc<dyn SkillSource> = Arc::new(StaticSource(vec![s]));
        let executor = Arc::new(DefaultSkillExecutor::new(Arc::new(
            SecurityPolicy::default(),
        )));
        let tool = SkillsExecuteTool::new(source, executor);
        let res = tool.execute(json!({ "skill_id": "deploy" })).await.unwrap();
        assert!(res.success);
        assert!(res.output.contains("markdown body"));
    }

    #[tokio::test]
    async fn disk_skill_source_reads_from_workspace_skills_dir() {
        let tmp = tempfile::TempDir::new().unwrap();
        let skill_dir = tmp.path().join("skills/widget");
        std::fs::create_dir_all(&skill_dir).unwrap();
        std::fs::write(skill_dir.join("SKILL.md"), "# Widget\nbody").unwrap();
        let source = DiskSkillSource::new(tmp.path().to_path_buf(), false, None);
        let loaded = source.load();
        assert!(loaded.iter().any(|s| s.name == "widget"));
    }

    // Suppress unused-import warnings in non-test builds.
    #[allow(dead_code)]
    fn _path_ref() -> PathBuf {
        PathBuf::new()
    }
}
