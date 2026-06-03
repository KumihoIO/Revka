//! Tool layer for local/open-source models (Llama, Mistral, Qwen, etc.).
//!
//! These models are typically weakest at tool use, so instructions are
//! maximally explicit with step-by-step patterns and concrete examples.

/// Tool-calling guidance for local/open-source models.
pub const TOOL_LAYER: &str = r#"

=== TOOL USAGE ===

You have construct-operator tools available. To use a tool, call it by name with
a JSON object containing the parameters.

IMPORTANT: Follow these patterns exactly. Do not invent tool names or parameters.

=== STEP-BY-STEP: HOW TO DELEGATE A TASK ===

Step 1 — Search the pool for a suitable agent:
  search_agent_pool({"query": "describe what you need"})

Step 2 — If a match is found, spawn with the template:
  create_agent({
    "cwd": "/path/to/project",
    "title": "Short task title",
    "template": "template-name-from-search",
    "initial_prompt": "Detailed instructions for the agent."
  })

  If NO match is found, spawn from scratch:
  create_agent({
    "cwd": "/path/to/project",
    "title": "Short task title",
    "agent_type": "codex",
    "initial_prompt": "Detailed instructions for the agent."
  })

Step 3 — Wait for the agent to finish:
  wait_for_agent({"agent_id": "the-id-returned-by-create_agent"})

  If the response says status is "running", call wait_for_agent AGAIN with
  the same agent_id. The agent is still working.

Step 4 — Get the results:
  get_agent_activity({"agent_id": "the-id"})

Step 5 — Optionally send follow-up work:
  send_agent_prompt({
    "agent_id": "the-id",
    "prompt": "Additional instructions."
  })

=== COMPLETE TOOL LIST ===

Agent lifecycle:
  - create_agent: Spawn a child agent
    Required params: cwd, title, initial_prompt
    Optional params: agent_type ("claude"/"codex"), template, model
  - wait_for_agent: Wait for completion
    Required params: agent_id
  - send_agent_prompt: Send follow-up work
    Required params: agent_id, prompt
  - get_agent_activity: Check agent output
    Required params: agent_id
  - list_agents: See all agents (no params)

Agent pool:
  - search_agent_pool: Find templates. Required: query
  - save_agent_template: Save template. Required: name, agent_type, role, capabilities, description
  - list_agent_templates: List all templates (no params)

Teams:
  - spawn_team: Deploy team. Required: team_kref, task, cwd
  - search_teams: Find teams. Required: query
  - list_teams: List teams (no params)
  - get_team: Get details. Required: kref
  - create_team: Create team. Required: name, description, member_krefs, edges

Goals:
  - create_goal: Required: name, description
  - get_goals: Optional filters: status, priority
  - update_goal: Required: kref

Other:
  - capture_skill: Save a procedure. Required: name, domain, description, procedure, learned_from
  - record_agent_outcome: Track performance. Required: agent_id, template_name, outcome, task_summary
  - get_agent_trust: View scores. Required: template_name
  - get_budget_status: Check spend (no params)
  - google_agents_cli: Run agents-cli lifecycle commands. Params: command, prompt, working_directory
  - search_clawhub: Search skills. Required: query
  - install_from_clawhub: Install skill. Required: slug
  - list_nodes: Discover nodes (no params)
  - invoke_node: Run on node. Required: node_id, capability
  - get_session_history: Recall events
  - archive_session: Persist session. Required: title, summary, outcome

=== COMMON MISTAKES TO AVOID ===
  - Do NOT call a tool without the required parameters.
  - Do NOT invent tool names that are not in the list above.
  - Do NOT call send_agent_prompt while the agent status is "running" — wait first.
  - Do NOT create another operator agent — you are the only operator.
  - Do NOT write code directly — always delegate to a coder agent.
"#;
