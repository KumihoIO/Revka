//! Tool layer for Google Gemini models.
//!
//! Gemini uses a function-calling format similar to OpenAI.  This layer
//! provides JSON examples adapted for Gemini's conventions.

/// Tool-calling guidance for Gemini models.
pub const TOOL_LAYER: &str = r#"

=== TOOL USAGE ===

You have revka-operator tools available. Call each tool by name with a JSON
object containing the parameters.

--- Core Workflow ---

1. Search the agent pool:
   search_agent_pool({"query": "what you need"})

2. Spawn an agent:
   create_agent({
     "cwd": "/path/to/project",
     "title": "Task title",
     "template": "template-name",
     "initial_prompt": "Detailed instructions."
   })
   Or without template:
   create_agent({
     "cwd": "/path/to/project",
     "title": "Task title",
     "agent_type": "codex",
     "initial_prompt": "Detailed instructions."
   })
   For Google ADK lifecycle work, spawn claude/codex and instruct it to call
   `google_agents_cli`; agents-cli is a tool for coding agents, not a provider.

3. Wait for completion:
   wait_for_agent({"agent_id": "<id>"})
   If status is "running", call wait_for_agent again.

4. Get results:
   get_agent_activity({"agent_id": "<id>"})

5. Send follow-up (only when agent is idle):
   send_agent_prompt({"agent_id": "<id>", "prompt": "Next steps."})

--- Teams ---

Deploy a team:
   spawn_team({
     "team_kref": "kref://Revka/Teams/team-name",
     "task": "Task description.",
     "cwd": "/path/to/project"
   })

--- All Tools ---

Agent lifecycle: create_agent, wait_for_agent, send_agent_prompt,
  get_agent_activity, list_agents
Agent pool: search_agent_pool, save_agent_template, list_agent_templates
Teams: spawn_team, search_teams, list_teams, get_team, create_team
Goals: create_goal, get_goals, update_goal
Skills: capture_skill
Trust: record_agent_outcome, get_agent_trust
Budget: get_budget_status
Google Agents CLI: google_agents_cli
ClawHub: search_clawhub, browse_clawhub, install_from_clawhub
Nodes: list_nodes, invoke_node
Session: get_session_history, archive_session

--- Error Handling ---

If a tool returns an error, read the message carefully:
  - "Agent not found" — wrong agent_id
  - "Agent is still running" — wait first, then send follow-up
  - "Agent limit reached" — wait for existing agents to finish
  - "Template not found" — check the name with search_agent_pool
"#;
