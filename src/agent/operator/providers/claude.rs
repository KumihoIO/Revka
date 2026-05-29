//! Claude-specific tool layer.
//!
//! Claude natively understands MCP tool schemas via the SDK, so it needs
//! minimal guidance.  A few examples help with complex multi-step flows.

/// Tool-calling guidance for Claude models (Opus, Sonnet, Haiku).
pub const TOOL_LAYER: &str = r#"

=== TOOL USAGE ===

You have construct-operator MCP tools available. Call them directly by name.

Quick reference with examples:

  # Search the pool before spawning
  search_agent_pool(query="rust security reviewer")

  # Spawn from a template
  create_agent(
    cwd="/path/to/project",
    title="Security audit",
    template="rust-security-auditor",
    initial_prompt="Audit unsafe blocks in src/buffer.rs for memory safety."
  )

  # Spawn from scratch (no template)
  create_agent(
    cwd="/path/to/project",
    title="Refactor DB layer",
    agent_type="codex",
    initial_prompt="Refactor src/db.rs to use connection pooling. Run tests."
  )
  # Use agent_type="google_agents" for Google Agents CLI / ADK projects.

  # Wait for completion, then collect results
  wait_for_agent(agent_id="<id>")
  get_agent_activity(agent_id="<id>")

  # Send follow-up work to an idle agent
  send_agent_prompt(agent_id="<id>", prompt="Now add integration tests.")

  # Run Google Agents CLI lifecycle commands
  google_agents_cli(
    command=["deploy", "--no-wait"],
    working_directory="/path/to/adk-project"
  )

  # Deploy a pre-built team
  spawn_team(
    team_kref="kref://Construct/Teams/security-audit",
    task="Full security audit on the payments module.",
    cwd="/path/to/project"
  )

All operator tools are always available. Use them whenever delegation is appropriate.
"#;
