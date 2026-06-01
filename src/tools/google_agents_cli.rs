use super::traits::{Tool, ToolResult};
use crate::config::GoogleAgentsCliConfig;
use crate::security::SecurityPolicy;
use crate::security::policy::ToolOperation;
use async_trait::async_trait;
use serde_json::json;
use std::sync::Arc;
use std::time::Duration;
use tokio::process::Command;

/// Environment variables safe to pass through to the `agents-cli` subprocess.
const SAFE_ENV_VARS: &[&str] = &[
    "PATH",
    "HOME",
    "TERM",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "USER",
    "SHELL",
    "TMPDIR",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_GENAI_USE_VERTEXAI",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "GEMINI_ENTERPRISE_APP_ID",
];

const ALLOWED_TOP_LEVEL_COMMANDS: &[&str] = &[
    "cmd-info",
    "create",
    "data-ingestion",
    "deploy",
    "eval",
    "infra",
    "info",
    "install",
    "lint",
    "login",
    "playground",
    "publish",
    "run",
    "scaffold",
    "setup",
    "update",
];

/// Runs Google Agents CLI (`agents-cli`) lifecycle commands.
///
/// `agents-cli` is not a coding-agent replacement for Claude Code or Codex.
/// It is Google's ADK / Agent Platform lifecycle CLI for scaffolding,
/// evaluating, running, deploying, publishing, and observing ADK/A2A agents.
/// This tool keeps Construct's execution surface explicit by spawning only the
/// `agents-cli` binary with argv tokens, never a shell.
pub struct GoogleAgentsCliTool {
    security: Arc<SecurityPolicy>,
    config: GoogleAgentsCliConfig,
}

impl GoogleAgentsCliTool {
    pub fn new(security: Arc<SecurityPolicy>, config: GoogleAgentsCliConfig) -> Self {
        Self { security, config }
    }
}

#[async_trait]
impl Tool for GoogleAgentsCliTool {
    fn name(&self) -> &str {
        "google_agents_cli"
    }

    fn description(&self) -> &str {
        "Run Google Agents CLI (agents-cli) lifecycle commands for ADK/A2A agents: run, scaffold, install, lint, eval, deploy, publish, infra, login --status, and info. Use for Google Agent Platform workflows, not as a generic shell."
    }

    fn parameters_schema(&self) -> serde_json::Value {
        json!({
            "type": "object",
            "properties": {
                "command": {
                    "type": "array",
                    "items": { "type": "string" },
                    "description": "Arguments after `agents-cli`, e.g. [\"run\"], [\"deploy\", \"--no-wait\"], [\"eval\", \"run\"], or [\"publish\", \"gemini-enterprise\", \"--list\"]. If omitted with `prompt`, defaults to [\"run\"]."
                },
                "prompt": {
                    "type": "string",
                    "description": "Prompt appended to `agents-cli run`. Use this instead of embedding long ADK project prompts in command tokens."
                },
                "working_directory": {
                    "type": "string",
                    "description": "Working directory within the workspace (must be inside workspace_dir). For most commands this should be an agents-cli project root."
                },
                "allow_interactive": {
                    "type": "boolean",
                    "description": "Allow interactive flags such as --interactive/-i. Defaults to false because Construct tools run non-interactively."
                }
            }
        })
    }

    async fn execute(&self, args: serde_json::Value) -> anyhow::Result<ToolResult> {
        if self.security.is_rate_limited() {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some("Rate limit exceeded: too many actions in the last hour".into()),
            });
        }

        if let Err(error) = self
            .security
            .enforce_tool_operation(ToolOperation::Act, "google_agents_cli")
        {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(error),
            });
        }

        let prompt = args.get("prompt").and_then(|v| v.as_str());
        let mut cli_args = match normalize_command(&args) {
            Ok(command) => command,
            Err(error) => {
                return Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some(error),
                });
            }
        };

        if cli_args.is_empty() {
            if prompt.is_some() {
                cli_args.push("run".to_string());
            } else {
                return Err(anyhow::anyhow!(
                    "Missing 'command' parameter (or provide 'prompt' to default to agents-cli run)"
                ));
            }
        }

        let allow_interactive = args
            .get("allow_interactive")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        if let Err(error) = validate_command(&cli_args, allow_interactive) {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(error),
            });
        }

        if let Some(text) = prompt {
            if text.contains('\0') {
                return Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some("prompt contains a NUL byte".into()),
                });
            }
            if cli_args.first().is_some_and(|cmd| cmd == "run") {
                cli_args.push(text.to_string());
            } else {
                return Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some("'prompt' is only valid with `command = [\"run\", ...]`".into()),
                });
            }
        }

        let work_dir = if let Some(wd) = args.get("working_directory").and_then(|v| v.as_str()) {
            match validate_working_directory(wd, &self.security.workspace_dir) {
                Ok(path) => path,
                Err(error) => {
                    return Ok(ToolResult {
                        success: false,
                        output: String::new(),
                        error: Some(error.to_string()),
                    });
                }
            }
        } else {
            self.security.workspace_dir.clone()
        };

        if !self.security.record_action() {
            return Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some("Rate limit exceeded: action budget exhausted".into()),
            });
        }

        let agents_cli_bin = if cfg!(target_os = "windows") {
            "agents-cli.exe"
        } else {
            "agents-cli"
        };
        let mut cmd = Command::new(agents_cli_bin);
        cmd.args(&cli_args);

        cmd.env_clear();
        for var in SAFE_ENV_VARS {
            if let Ok(val) = std::env::var(var) {
                cmd.env(var, val);
            }
        }
        for var in &self.config.env_passthrough {
            let trimmed = var.trim();
            if !trimmed.is_empty() {
                if let Ok(val) = std::env::var(trimmed) {
                    cmd.env(trimmed, val);
                }
            }
        }

        cmd.current_dir(&work_dir);
        let timeout = Duration::from_secs(self.config.timeout_secs);
        cmd.kill_on_drop(true);

        let result = tokio::time::timeout(timeout, cmd.output()).await;

        match result {
            Ok(Ok(output)) => {
                let mut stdout = String::from_utf8_lossy(&output.stdout).to_string();
                let mut stderr = String::from_utf8_lossy(&output.stderr).to_string();
                truncate_to_bytes(
                    &mut stdout,
                    self.config.max_output_bytes,
                    "\n... [output truncated]",
                );
                truncate_to_bytes(
                    &mut stderr,
                    self.config.max_output_bytes,
                    "\n... [stderr truncated]",
                );

                Ok(ToolResult {
                    success: output.status.success(),
                    output: stdout,
                    error: if stderr.is_empty() {
                        None
                    } else {
                        Some(stderr)
                    },
                })
            }
            Ok(Err(e)) => {
                let err_msg = e.to_string();
                let msg = if err_msg.contains("No such file or directory")
                    || err_msg.contains("not found")
                    || err_msg.contains("cannot find")
                {
                    "Google Agents CLI ('agents-cli') not found in PATH. Install with: uvx google-agents-cli setup".into()
                } else {
                    format!("Failed to execute agents-cli: {e}")
                };
                Ok(ToolResult {
                    success: false,
                    output: String::new(),
                    error: Some(msg),
                })
            }
            Err(_) => Ok(ToolResult {
                success: false,
                output: String::new(),
                error: Some(format!(
                    "Google Agents CLI timed out after {}s and was killed",
                    self.config.timeout_secs
                )),
            }),
        }
    }
}

fn normalize_command(args: &serde_json::Value) -> Result<Vec<String>, String> {
    match args.get("command") {
        None | Some(serde_json::Value::Null) => Ok(Vec::new()),
        Some(serde_json::Value::String(command)) => Ok(vec![command.to_owned()]),
        Some(serde_json::Value::Array(command)) => {
            let mut parts = Vec::with_capacity(command.len());
            for part in command {
                let Some(text) = part.as_str() else {
                    return Err("agents-cli command tokens must be strings".into());
                };
                parts.push(text.to_owned());
            }
            Ok(parts)
        }
        Some(_) => Err("agents-cli command must be a string or array of strings".into()),
    }
}

fn truncate_to_bytes(text: &mut String, max_bytes: usize, marker: &str) {
    if text.len() <= max_bytes {
        return;
    }
    let mut b = max_bytes.min(text.len());
    while b > 0 && !text.is_char_boundary(b) {
        b -= 1;
    }
    text.truncate(b);
    text.push_str(marker);
}

fn validate_working_directory(
    wd: &str,
    workspace: &std::path::Path,
) -> anyhow::Result<std::path::PathBuf> {
    let wd_path = std::path::PathBuf::from(wd);
    let candidate = if wd_path.is_absolute() {
        wd_path
    } else {
        workspace.join(wd_path)
    };
    let canonical_wd = match candidate.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            return Err(anyhow::anyhow!(
                "working_directory '{}' does not exist or is not accessible",
                wd
            ));
        }
    };
    let canonical_ws = match workspace.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            return Err(anyhow::anyhow!(
                "workspace directory '{}' does not exist or is not accessible",
                workspace.display()
            ));
        }
    };
    if !canonical_wd.starts_with(&canonical_ws) {
        return Err(anyhow::anyhow!(
            "working_directory '{}' is outside the workspace '{}'",
            wd,
            workspace.display()
        ));
    }
    Ok(canonical_wd)
}

fn validate_command(args: &[String], allow_interactive: bool) -> Result<(), String> {
    let Some(first) = args.first().map(|s| s.trim()) else {
        return Err("agents-cli command must not be empty".into());
    };
    if first.is_empty() {
        return Err("agents-cli command must not start with an empty token".into());
    }
    if !ALLOWED_TOP_LEVEL_COMMANDS.contains(&first) {
        return Err(format!(
            "Unsupported agents-cli command '{}'. Allowed commands: {}",
            first,
            ALLOWED_TOP_LEVEL_COMMANDS.join(", ")
        ));
    }

    for arg in args {
        if arg.is_empty() {
            return Err("agents-cli command contains an empty token".into());
        }
        if arg.trim() != arg {
            return Err(
                "agents-cli command tokens must not include leading or trailing whitespace".into(),
            );
        }
        if arg.contains('\0') {
            return Err("agents-cli command contains a NUL byte".into());
        }
        if !allow_interactive && (arg == "-i" || arg == "--interactive") {
            return Err("Interactive agents-cli flags are disabled by default".into());
        }
    }

    if first == "login"
        && !args.iter().any(|arg| arg == "--status" || arg == "status")
        && !allow_interactive
    {
        return Err(
            "Use `agents-cli login --status`; interactive login must be done outside Construct"
                .into(),
        );
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::GoogleAgentsCliConfig;
    use crate::security::{AutonomyLevel, SecurityPolicy};

    fn test_config() -> GoogleAgentsCliConfig {
        GoogleAgentsCliConfig::default()
    }

    fn test_security(autonomy: AutonomyLevel) -> Arc<SecurityPolicy> {
        Arc::new(SecurityPolicy {
            autonomy,
            workspace_dir: std::env::temp_dir(),
            ..SecurityPolicy::default()
        })
    }

    #[test]
    fn google_agents_cli_tool_name() {
        let tool =
            GoogleAgentsCliTool::new(test_security(AutonomyLevel::Supervised), test_config());
        assert_eq!(tool.name(), "google_agents_cli");
    }

    #[test]
    fn google_agents_cli_tool_schema_has_command_and_prompt() {
        let tool =
            GoogleAgentsCliTool::new(test_security(AutonomyLevel::Supervised), test_config());
        let schema = tool.parameters_schema();
        assert!(schema["properties"]["command"].is_object());
        assert!(schema["properties"]["prompt"].is_object());
    }

    #[tokio::test]
    async fn google_agents_cli_blocks_rate_limited() {
        let security = Arc::new(SecurityPolicy {
            autonomy: AutonomyLevel::Supervised,
            workspace_dir: std::env::temp_dir(),
            max_actions_per_hour: 0,
            ..SecurityPolicy::default()
        });
        let tool = GoogleAgentsCliTool::new(security, test_config());
        let result = tool
            .execute(json!({ "command": ["login", "--status"] }))
            .await
            .unwrap();
        assert!(!result.success);
        assert!(result.error.unwrap().contains("Rate limit"));
    }

    #[tokio::test]
    async fn google_agents_cli_blocks_readonly() {
        let tool = GoogleAgentsCliTool::new(test_security(AutonomyLevel::ReadOnly), test_config());
        let result = tool
            .execute(json!({ "command": ["login", "--status"] }))
            .await
            .unwrap();
        assert!(!result.success);
        assert!(result.error.unwrap().contains("read-only mode"));
    }

    #[tokio::test]
    async fn google_agents_cli_missing_command_and_prompt() {
        let tool =
            GoogleAgentsCliTool::new(test_security(AutonomyLevel::Supervised), test_config());
        let err = tool.execute(json!({})).await.unwrap_err();
        assert!(err.to_string().contains("Missing 'command'"));
    }

    #[tokio::test]
    async fn google_agents_cli_rejects_interactive_login() {
        let tool =
            GoogleAgentsCliTool::new(test_security(AutonomyLevel::Supervised), test_config());
        let result = tool
            .execute(json!({ "command": ["login", "--interactive"] }))
            .await
            .unwrap();
        assert!(!result.success);
        assert!(result.error.unwrap().contains("Interactive"));
    }

    #[tokio::test]
    async fn google_agents_cli_rejects_non_string_command_tokens() {
        let tool =
            GoogleAgentsCliTool::new(test_security(AutonomyLevel::Supervised), test_config());
        let result = tool
            .execute(json!({ "command": ["run", 1] }))
            .await
            .unwrap();
        assert!(!result.success);
        assert!(result.error.unwrap().contains("tokens must be strings"));
    }

    #[tokio::test]
    async fn google_agents_cli_rejects_path_outside_workspace() {
        let tmp = tempfile::tempdir().unwrap();
        let outside = tempfile::tempdir().unwrap();
        let security = Arc::new(SecurityPolicy {
            autonomy: AutonomyLevel::Full,
            workspace_dir: tmp.path().to_path_buf(),
            ..SecurityPolicy::default()
        });
        let tool = GoogleAgentsCliTool::new(security, test_config());
        let result = tool
            .execute(json!({
                "command": ["login", "--status"],
                "working_directory": outside.path().to_string_lossy()
            }))
            .await
            .unwrap();
        assert!(!result.success);
        assert!(result.error.unwrap().contains("outside the workspace"));
    }

    #[tokio::test]
    async fn google_agents_cli_accepts_relative_path_inside_workspace() {
        let tmp = tempfile::tempdir().unwrap();
        let child = tmp.path().join("agent-project");
        std::fs::create_dir(&child).unwrap();
        let security = Arc::new(SecurityPolicy {
            autonomy: AutonomyLevel::Full,
            workspace_dir: tmp.path().to_path_buf(),
            ..SecurityPolicy::default()
        });
        let tool = GoogleAgentsCliTool::new(security, test_config());
        let result = tool
            .execute(json!({
                "command": ["login", "--status"],
                "working_directory": "agent-project"
            }))
            .await
            .unwrap();
        assert!(
            !result
                .error
                .unwrap_or_default()
                .contains("outside the workspace")
        );
    }

    #[test]
    fn google_agents_cli_env_passthrough_defaults() {
        let config = GoogleAgentsCliConfig::default();
        assert!(config.env_passthrough.is_empty());
    }

    #[test]
    fn google_agents_cli_safe_env_includes_enterprise_publish_id() {
        assert!(SAFE_ENV_VARS.contains(&"GEMINI_ENTERPRISE_APP_ID"));
    }

    #[test]
    fn google_agents_cli_default_config_values() {
        let config = GoogleAgentsCliConfig::default();
        assert!(!config.enabled);
        assert_eq!(config.timeout_secs, 600);
        assert_eq!(config.max_output_bytes, 2_097_152);
    }

    #[test]
    fn google_agents_cli_accepts_string_command_shape() {
        let command = normalize_command(&json!({ "command": "info" })).unwrap();
        assert_eq!(command, vec!["info"]);
    }

    #[test]
    fn google_agents_cli_rejects_command_object_shape() {
        let error = normalize_command(&json!({ "command": { "name": "info" } })).unwrap_err();
        assert!(error.contains("string or array"));
    }

    #[test]
    fn google_agents_cli_truncates_stderr_without_splitting_utf8() {
        let mut stderr = "éééé".to_string();
        truncate_to_bytes(&mut stderr, 5, "\n... [stderr truncated]");
        assert_eq!(stderr, "éé\n... [stderr truncated]");
    }

    #[test]
    fn google_agents_cli_accepts_current_info_command() {
        assert!(validate_command(&["info".to_string()], false).is_ok());
    }
}
