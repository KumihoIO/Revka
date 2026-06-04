//! OpenAI-family tool layer (GPT, Codex, o-series).
//!
//! OpenAI models use JSON function-calling format.  They need explicit examples
//! showing the exact JSON structure, parameter types, and expected responses.

/// Tool-calling guidance for OpenAI-family models.
pub const TOOL_LAYER: &str = r#"

=== TOOL USAGE ===

You have revka-operator tools available. Each tool accepts a JSON object with
named parameters. Below is every tool with its parameters and usage examples.

--- Agent Lifecycle ---

create_agent — Spawn a child agent.
  Required: cwd (string), title (string), initial_prompt (string)
  Optional: agent_type ("claude" or "codex"), template (string), model (string)

  Example — spawn from template:
  ```
  create_agent({
    "cwd": "/path/to/project",
    "title": "Security audit",
    "template": "rust-security-auditor",
    "initial_prompt": "Audit unsafe blocks in src/buffer.rs for memory safety."
  })
  ```

  Example — spawn from scratch:
  ```
  create_agent({
    "cwd": "/path/to/project",
    "title": "Refactor DB layer",
    "agent_type": "codex",
    "initial_prompt": "Refactor src/db.rs to use connection pooling. Run tests."
  })
  ```

  Example — delegate Google ADK lifecycle work to a coding agent:
  ```
  create_agent({
    "cwd": "/path/to/adk-project",
    "title": "Deploy ADK agent",
    "agent_type": "codex",
    "initial_prompt": "Use google_agents_cli to deploy this ADK project and report the result."
  })
  ```

wait_for_agent — Wait for a child agent to finish.
  Required: agent_id (string)

  ```
  wait_for_agent({"agent_id": "<id-from-create>"})
  ```
  Returns: { "agent_id": "...", "status": "completed"|"error"|"running", "last_message": "..." }
  If status is "running", call wait_for_agent again — the agent is still working.

send_agent_prompt — Send follow-up work to an idle agent.
  Required: agent_id (string), prompt (string)

  ```
  send_agent_prompt({
    "agent_id": "<id>",
    "prompt": "Now add integration tests for the refactored code."
  })
  ```

get_agent_activity — Check what an agent has done.
  Required: agent_id (string)

  ```
  get_agent_activity({"agent_id": "<id>"})
  ```

list_agents — See all running agents.
  No parameters.
  ```
  list_agents({})
  ```

--- Agent Pool ---

search_agent_pool — Find matching agent templates.
  Required: query (string)

  ```
  search_agent_pool({"query": "rust security reviewer"})
  ```
  Returns: { "matches": [{"name": "...", "role": "...", "description": "..."}], "count": N }

save_agent_template — Save an agent template to the pool.
  Required: name, agent_type, role, capabilities (array), description
  Optional: identity, soul, tone, model, system_hint

  ```
  save_agent_template({
    "name": "rust-security-auditor",
    "agent_type": "codex",
    "role": "reviewer",
    "capabilities": ["rust", "security", "unsafe-code"],
    "description": "Reviews Rust code for memory safety and security issues",
    "identity": "Senior security engineer specializing in Rust",
    "soul": "Methodical. Never skips edge cases.",
    "tone": "Direct. Flags issues with severity levels.",
    "model": "claude-opus-4-6"
  })
  ```

list_agent_templates — Browse all templates. No parameters.
  ```
  list_agent_templates({})
  ```

--- Teams ---

spawn_team — Deploy a pre-built team.
  Required: team_kref (string), task (string), cwd (string)

  ```
  spawn_team({
    "team_kref": "kref://Revka/Teams/security-audit",
    "task": "Full security audit on the payments module.",
    "cwd": "/path/to/project"
  })
  ```
  Returns: { "agents": [{"agent_id": "...", "title": "...", "role": "..."}] }

search_teams — Find teams by name/description.
  Required: query (string)
  ```
  search_teams({"query": "security audit"})
  ```

list_teams — List all teams. No parameters.
get_team — Get team details. Required: kref (string).
create_team — Create a new team. Required: name, description, member_krefs, edges.

--- Goals ---

create_goal — Required: name, description. Optional: status, priority, parent_kref.
get_goals — Optional: status, priority (filters).
update_goal — Required: kref. Optional: status, priority, description.

--- Skills ---

capture_skill — Required: name, domain, description, procedure, learned_from.

--- Trust ---

record_agent_outcome — Required: agent_id, template_name, outcome, task_summary.
get_agent_trust — Required: template_name.

--- Budget ---

get_budget_status — No parameters. Returns session/daily/monthly spend.

--- Google Agents CLI ---

google_agents_cli — Run agents-cli lifecycle commands. Params: command (array), prompt (for run), working_directory, timeout.

--- ClawHub ---

search_clawhub — Required: query. Optional: limit.
browse_clawhub — Optional: limit.
install_from_clawhub — Required: slug.

--- Nodes ---

list_nodes — No parameters.
invoke_node — Required: node_id, capability. Optional: args (object).

--- Session ---

get_session_history — Optional: session_id, agent_id, limit, list_sessions (bool).
archive_session — Required: title, summary, outcome. Optional: session_id.

=== ERROR HANDLING ===
If a tool call returns an error:
  - "Agent not found" — the agent_id is incorrect or the agent was cleaned up
  - "Agent is still running" — call wait_for_agent instead of send_agent_prompt
  - "Agent limit reached" — too many concurrent agents; wait for some to finish
  - "Template not found" — the template name doesn't exist in the pool
  - "Directory does not exist" — check the cwd path

When an agent errors, use get_agent_activity to see what went wrong, then either
retry with a corrected prompt via send_agent_prompt, or spawn a new agent.
"#;
