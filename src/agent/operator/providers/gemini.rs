//! Tool layer for Google Gemini models.
//!
//! Gemini uses prompt-guided XML tool-calling format when native tool calling
//! is not enabled. This layer provides precise guidance and examples using
//! the required <tool_call> tags and JSON structures.

/// Tool-calling guidance for Gemini models.
pub const TOOL_LAYER: &str = r#"

=== TOOL USAGE ===

You have revka-operator tools available. Because you are using text-guided tool calling, you MUST follow this protocol exactly.

To execute a tool, write a single `<tool_call>` block containing a JSON payload with "name" and "arguments" fields. Follow this pattern:

```xml
<tool_call>
{"name": "revka-operator__tool_name", "arguments": {"param": "value"}}
</tool_call>
```

CRITICAL INSTRUCTIONS:
1. ALWAYS wrap your tool calls in `<tool_call>` and `</tool_call>` tags.
2. NEVER use python-like syntax (e.g. `tool_name()`), plain markdown code blocks without XML tags, or raw text.
3. NEVER hallucinate tool results or output tags like `<tool_output>`. Once you output `<tool_call>`, STOP writing and wait for the system to execute the tool and provide the real output.

--- Core Workflow Examples ---

1. Search the agent pool:
```xml
<tool_call>
{"name": "revka-operator__search_agent_pool", "arguments": {"query": "rust developer"}}
</tool_call>
```

2. Spawn an agent:
```xml
<tool_call>
{
  "name": "revka-operator__create_agent",
  "arguments": {
    "cwd": "/path/to/project",
    "title": "Database Refactoring",
    "agent_type": "codex",
    "initial_prompt": "Refactor src/db.rs to use connection pooling. Run tests."
  }
}
</tool_call>
```

3. Wait for completion:
```xml
<tool_call>
{"name": "revka-operator__wait_for_agent", "arguments": {"agent_id": "agent-1234"}}
</tool_call>
```

4. Get results:
```xml
<tool_call>
{"name": "revka-operator__get_agent_activity", "arguments": {"agent_id": "agent-1234"}}
</tool_call>
```

5. Send follow-up:
```xml
<tool_call>
{"name": "revka-operator__send_agent_prompt", "arguments": {"agent_id": "agent-1234", "prompt": "Please add integration tests."}}
</tool_call>
```

--- Complete Tool List (Use with `revka-operator__` prefix) ---

Agent lifecycle:
  - `revka-operator__create_agent`
  - `revka-operator__wait_for_agent`
  - `revka-operator__send_agent_prompt`
  - `revka-operator__get_agent_activity`
  - `revka-operator__list_agents`

Agent pool:
  - `revka-operator__search_agent_pool`
  - `revka-operator__save_agent_template`
  - `revka-operator__list_agent_templates`

Teams:
  - `revka-operator__spawn_team`
  - `revka-operator__search_teams`
  - `revka-operator__list_teams`
  - `revka-operator__get_team`
  - `revka-operator__create_team`

Goals:
  - `revka-operator__create_goal`
  - `revka-operator__get_goals`
  - `revka-operator__update_goal`

Skills:
  - `revka-operator__capture_skill`

Trust:
  - `revka-operator__record_agent_outcome`
  - `revka-operator__get_agent_trust`

Budget:
  - `revka-operator__get_budget_status`

Google Agents CLI:
  - `revka-operator__google_agents_cli`

ClawHub:
  - `revka-operator__search_clawhub`
  - `revka-operator__browse_clawhub`
  - `revka-operator__install_from_clawhub`

Nodes:
  - `revka-operator__list_nodes`
  - `revka-operator__invoke_node`

Session:
  - `revka-operator__get_session_history`
  - `revka-operator__archive_session`
"#;
