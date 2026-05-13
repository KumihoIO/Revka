//! WebSocket agent chat handler.
//!
//! Connect: `ws://host:port/ws/chat?session_id=ID&name=My+Session`
//!
//! Protocol:
//! ```text
//! Server -> Client: {"type":"session_start","session_id":"...","name":"...","resumed":true,"message_count":42}
//! Client -> Server: {"type":"message","content":"Hello"}
//! Server -> Client: {"type":"chunk","content":"Hi! "}
//! Server -> Client: {"type":"tool_call","name":"shell","args":{...}}
//! Server -> Client: {"type":"tool_result","name":"shell","output":"..."}
//! Server -> Client: {"type":"done","full_response":"..."}
//! ```
//!
//! Query params:
//! - `session_id` — resume or create a session (default: new UUID)
//! - `name` — optional human-readable label for the session
//! - `token` — bearer auth token (alternative to Authorization header)

use super::AppState;
use axum::{
    extract::{
        Query, State, WebSocketUpgrade,
        ws::{Message, WebSocket},
    },
    http::{HeaderMap, header},
    response::IntoResponse,
};
use futures_util::{SinkExt, StreamExt};
use serde::Deserialize;
use std::sync::Arc;
use tracing::debug;

/// Optional connection parameters sent as the first WebSocket message.
///
/// If the first message after upgrade is `{"type":"connect",...}`, these
/// parameters are extracted and an acknowledgement is sent back. Old clients
/// that send `{"type":"message",...}` as the first frame still work — the
/// message is processed normally (backward-compatible).
#[derive(Debug, Deserialize)]
struct ConnectParams {
    #[serde(rename = "type")]
    msg_type: String,
    /// Client-chosen session ID for memory persistence
    #[serde(default)]
    session_id: Option<String>,
    /// Device name for device registry tracking
    #[serde(default)]
    device_name: Option<String>,
    /// Client capabilities
    #[serde(default)]
    capabilities: Vec<String>,
}

/// The sub-protocol we support for the chat WebSocket.
const WS_PROTOCOL: &str = "construct.v1";

/// Prefix used in `Sec-WebSocket-Protocol` to carry a bearer token.
const BEARER_SUBPROTO_PREFIX: &str = "bearer.";

#[derive(Deserialize)]
pub struct WsQuery {
    pub token: Option<String>,
    pub session_id: Option<String>,
    /// Optional human-readable name for the session.
    pub name: Option<String>,
}

/// Extract a bearer token from WebSocket-compatible sources.
///
/// Precedence (first non-empty wins):
/// 1. `Authorization: Bearer <token>` header
/// 2. `Sec-WebSocket-Protocol: bearer.<token>` subprotocol
/// 3. `?token=<token>` query parameter
///
/// Browsers cannot set custom headers on `new WebSocket(url)`, so the query
/// parameter and subprotocol paths are required for browser-based clients.
fn extract_ws_token<'a>(headers: &'a HeaderMap, query_token: Option<&'a str>) -> Option<&'a str> {
    // 1. Authorization header
    if let Some(t) = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|auth| auth.strip_prefix("Bearer "))
    {
        if !t.is_empty() {
            return Some(t);
        }
    }

    // 2. Sec-WebSocket-Protocol: bearer.<token>
    if let Some(t) = headers
        .get("sec-websocket-protocol")
        .and_then(|v| v.to_str().ok())
        .and_then(|protos| {
            protos
                .split(',')
                .map(|p| p.trim())
                .find_map(|p| p.strip_prefix(BEARER_SUBPROTO_PREFIX))
        })
    {
        if !t.is_empty() {
            return Some(t);
        }
    }

    // 3. ?token= query parameter
    if let Some(t) = query_token {
        if !t.is_empty() {
            return Some(t);
        }
    }

    None
}

/// GET /ws/chat — WebSocket upgrade for agent chat
pub async fn handle_ws_chat(
    State(state): State<AppState>,
    Query(params): Query<WsQuery>,
    headers: HeaderMap,
    ws: WebSocketUpgrade,
) -> impl IntoResponse {
    // Auth: check header, subprotocol, then query param (precedence order)
    if state.pairing.require_pairing() {
        let token = extract_ws_token(&headers, params.token.as_deref()).unwrap_or("");
        if !state.pairing.is_authenticated(token) {
            return (
                axum::http::StatusCode::UNAUTHORIZED,
                "Unauthorized — provide Authorization header, Sec-WebSocket-Protocol bearer, or ?token= query param",
            )
                .into_response();
        }
    }

    // Echo Sec-WebSocket-Protocol if the client requests our sub-protocol.
    let ws = if headers
        .get("sec-websocket-protocol")
        .and_then(|v| v.to_str().ok())
        .map_or(false, |protos| {
            protos.split(',').any(|p| p.trim() == WS_PROTOCOL)
        }) {
        ws.protocols([WS_PROTOCOL])
    } else {
        ws
    };

    // Audit: log WebSocket chat connection
    if let Some(ref logger) = state.audit_logger {
        let _ = logger.log_security_event("dashboard", "WebSocket chat session connected");
    }

    let session_id = params.session_id;
    let session_name = params.name;
    ws.on_upgrade(move |socket| handle_socket(socket, state, session_id, session_name))
        .into_response()
}

/// Gateway session key prefix to avoid collisions with channel sessions.
const GW_SESSION_PREFIX: &str = "gw_";

async fn handle_socket(
    socket: WebSocket,
    state: AppState,
    session_id: Option<String>,
    session_name: Option<String>,
) {
    let (mut sender, mut receiver) = socket.split();

    // Resolve session ID: use provided or generate a new UUID
    let session_id = session_id.unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    let session_key = format!("{GW_SESSION_PREFIX}{session_id}");

    // Hydrate agent from persisted session (if available)
    let mut resumed = false;
    let mut message_count: usize = 0;
    let mut effective_name: Option<String> = None;
    let mut persisted_messages: Vec<crate::providers::ChatMessage> = Vec::new();
    if let Some(ref backend) = state.session_backend {
        let messages = backend.load(&session_key);
        if !messages.is_empty() {
            message_count = messages.len();
            persisted_messages = messages;
            resumed = true;
        }
        // Set session name if provided (non-empty) on connect
        if let Some(ref name) = session_name {
            if !name.is_empty() {
                let _ = backend.set_session_name(&session_key, name);
                effective_name = Some(name.clone());
            }
        }
        // If no name was provided via query param, load the stored name
        if effective_name.is_none() {
            effective_name = backend.get_session_name(&session_key).unwrap_or(None);
        }
    }

    // Send session_start message to client
    let mut session_start = serde_json::json!({
        "type": "session_start",
        "session_id": session_id,
        "resumed": resumed,
        "message_count": message_count,
    });
    if let Some(ref name) = effective_name {
        session_start["name"] = serde_json::Value::String(name.clone());
    }
    let _ = sender
        .send(Message::Text(session_start.to_string().into()))
        .await;

    // ── Optional connect handshake ──────────────────────────────────
    // The first message may be a `{"type":"connect",...}` frame carrying
    // connection parameters.  If it is, we extract the params, send an
    // ack, and proceed to the normal message loop.  If the first message
    // is a regular `{"type":"message",...}` frame, we fall through and
    // process it immediately (backward-compatible).
    let mut first_msg_fallback: Option<String> = None;
    let mut agent: Option<crate::agent::Agent> = None;
    let mut agent_memory_session_id = session_id.clone();

    // Wait up to 5 seconds for the first client frame.  Listen-only
    // workflow run viewers may never send a message — the
    // timeout lets them fall through to the broadcast relay loop.
    match tokio::time::timeout(std::time::Duration::from_secs(5), receiver.next()).await {
        Ok(Some(first)) => {
            match first {
                Ok(Message::Text(text)) => {
                    if let Ok(cp) = serde_json::from_str::<ConnectParams>(&text) {
                        if cp.msg_type == "connect" {
                            debug!(
                                session_id = ?cp.session_id,
                                device_name = ?cp.device_name,
                                capabilities = ?cp.capabilities,
                                "WebSocket connect params received"
                            );
                            // Override session_id if provided in connect params
                            if let Some(sid) = &cp.session_id {
                                agent_memory_session_id = sid.clone();
                            }
                            let ack = serde_json::json!({
                                "type": "connected",
                                "message": "Connection established"
                            });
                            let _ = sender.send(Message::Text(ack.to_string().into())).await;
                        } else {
                            // Not a connect message — fall through to normal processing
                            first_msg_fallback = Some(text.to_string());
                        }
                    } else {
                        // Not parseable as ConnectParams — fall through
                        first_msg_fallback = Some(text.to_string());
                    }
                }
                Ok(Message::Close(frame)) => {
                    tracing::info!(session = %session_key, ?frame, "WebSocket chat closed during handshake");
                    return;
                }
                Err(error) => {
                    tracing::warn!(session = %session_key, %error, "WebSocket chat error during handshake");
                    return;
                }
                _ => {}
            }
        }
        Ok(None) => return, // Stream ended
        Err(_) => {
            // Timeout — no initial message received within 5s.  Proceed to
            // main loop so listen-only connections still receive broadcasts.
            debug!(session_id = %session_id, "No initial message within 5s — entering listen-only mode");
        }
    }

    // Subscribe to the broadcast channel early so we can relay operator channel
    // events (agent.started, agent.completed, etc.) even during the first turn.
    let mut broadcast_rx = state.event_tx.subscribe();

    // Process the first message if it was not a connect frame
    if let Some(ref text) = first_msg_fallback {
        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(text) {
            if parsed["type"].as_str() == Some("message") {
                let content = parsed["content"].as_str().unwrap_or("").to_string();
                if !content.is_empty() {
                    let page_ctx = parsed["page_context"].as_str();
                    let attachments = parse_attachments(&parsed);
                    // Persist user message
                    if let Some(ref backend) = state.session_backend {
                        if backend.is_session_archived(&session_key).unwrap_or(false) {
                            let _ = backend.unarchive_session(&session_key);
                        }
                        let user_msg = crate::providers::ChatMessage::user(&content);
                        let _ = backend.append(&session_key, &user_msg);
                    }
                    if !ensure_agent_for_session(
                        &state,
                        &mut sender,
                        &mut agent,
                        &agent_memory_session_id,
                        &persisted_messages,
                    )
                    .await
                    {
                        return;
                    }
                    let agent = agent.as_mut().expect("agent initialized");
                    process_chat_message(
                        &state,
                        agent,
                        &mut sender,
                        &mut receiver,
                        &content,
                        &session_key,
                        page_ctx,
                        &attachments,
                        &mut broadcast_rx,
                    )
                    .await;
                }
            } else {
                let unknown_type = parsed["type"].as_str().unwrap_or("unknown");
                let err = serde_json::json!({
                    "type": "error",
                    "message": format!(
                        "Unsupported message type \"{unknown_type}\". Send {{\"type\":\"message\",\"content\":\"your text\"}}"
                    )
                });
                let _ = sender.send(Message::Text(err.to_string().into())).await;
            }
        } else {
            let err = serde_json::json!({
                "type": "error",
                "message": "Invalid JSON. Send {\"type\":\"message\",\"content\":\"your text\"}"
            });
            let _ = sender.send(Message::Text(err.to_string().into())).await;
        }
    }

    // Periodic Ping keepalive. macOS/browser/proxy stacks close idle TCP
    // sockets aggressively; without server-initiated traffic the chat WS
    // drops every few minutes and the client reconnects (spawning a fresh
    // operator-mcp). Browsers auto-reply to server pings with pongs at the
    // protocol level, so no client-side change is needed.
    let mut ping_interval = tokio::time::interval(std::time::Duration::from_secs(30));
    ping_interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    // Skip the immediate first tick — interval fires once instantly otherwise.
    ping_interval.tick().await;

    loop {
        tokio::select! {
            // ── Branch 1: incoming WebSocket message from the client ──
            ws_msg = receiver.next() => {
                let msg = match ws_msg {
                    Some(Ok(Message::Text(text))) => text,
                    Some(Ok(Message::Close(frame))) => {
                        tracing::info!(session = %session_key, ?frame, "WebSocket chat closed");
                        break;
                    }
                    Some(Err(error)) => {
                        tracing::warn!(session = %session_key, %error, "WebSocket chat error");
                        break;
                    }
                    None => {
                        tracing::info!(session = %session_key, "WebSocket chat stream ended");
                        break;
                    }
                    _ => continue,
                };

                let parsed: serde_json::Value = match serde_json::from_str(&msg) {
                    Ok(v) => v,
                    Err(e) => {
                        let err = serde_json::json!({
                            "type": "error",
                            "message": format!("Invalid JSON: {}", e),
                            "code": "INVALID_JSON"
                        });
                        let _ = sender.send(Message::Text(err.to_string().into())).await;
                        continue;
                    }
                };

                let msg_type = parsed["type"].as_str().unwrap_or("");
                if msg_type == "stop" {
                    let stopped = serde_json::json!({
                        "type": "stopped",
                        "message": "No active Operator turn to stop."
                    });
                    let _ = sender.send(Message::Text(stopped.to_string().into())).await;
                    continue;
                }

                if msg_type != "message" {
                    let err = serde_json::json!({
                        "type": "error",
                        "message": format!(
                            "Unsupported message type \"{msg_type}\". Send {{\"type\":\"message\",\"content\":\"your text\"}}"
                        ),
                        "code": "UNKNOWN_MESSAGE_TYPE"
                    });
                    let _ = sender.send(Message::Text(err.to_string().into())).await;
                    continue;
                }

                let content = parsed["content"].as_str().unwrap_or("").to_string();
                if content.is_empty() {
                    let err = serde_json::json!({
                        "type": "error",
                        "message": "Message content cannot be empty",
                        "code": "EMPTY_CONTENT"
                    });
                    let _ = sender.send(Message::Text(err.to_string().into())).await;
                    continue;
                }

                // Acquire session lock to serialize concurrent turns
                let _session_guard = match state.session_queue.acquire(&session_key).await {
                    Ok(guard) => guard,
                    Err(e) => {
                        let err = serde_json::json!({
                            "type": "error",
                            "message": e.to_string(),
                            "code": "SESSION_BUSY"
                        });
                        let _ = sender.send(Message::Text(err.to_string().into())).await;
                        continue;
                    }
                };

                let page_ctx = parsed["page_context"].as_str();
                let attachments = parse_attachments(&parsed);

                // Persist user message
                if let Some(ref backend) = state.session_backend {
                    if backend.is_session_archived(&session_key).unwrap_or(false) {
                        let _ = backend.unarchive_session(&session_key);
                    }
                    let user_msg = crate::providers::ChatMessage::user(&content);
                    let _ = backend.append(&session_key, &user_msg);
                }

                if !ensure_agent_for_session(
                    &state,
                    &mut sender,
                    &mut agent,
                    &agent_memory_session_id,
                    &persisted_messages,
                )
                .await
                {
                    return;
                }
                let agent = agent.as_mut().expect("agent initialized");
                process_chat_message(&state, agent, &mut sender, &mut receiver, &content, &session_key, page_ctx, &attachments, &mut broadcast_rx).await;
            }

            // ── Branch 2: broadcast channel event from operator ──
            event = broadcast_rx.recv() => {
                match event {
                    Ok(ev) if ev["type"].as_str() == Some("channel_event") => {
                        let relay = serde_json::json!({
                            "type": "agent_event",
                            "event": ev["payload"],
                        });
                        let _ = sender.send(Message::Text(relay.to_string().into())).await;
                    }
                    Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
                    _ => {} // Skip non-channel events and lag errors
                }
            }

            // ── Branch 3: keepalive Ping ──
            _ = ping_interval.tick() => {
                if sender.send(Message::Ping(Vec::new().into())).await.is_err() {
                    tracing::warn!(session = %session_key, "WebSocket chat keepalive send failed");
                    break;
                }
            }
        }
    }
}

/// Lazily build the per-socket Agent only when the socket actually submits a
/// chat message. Dashboard pages also open listen-only WebSockets for live
/// workflow/operator events; constructing a full Agent for those sockets
/// spawns MCP stdio sidecars and can leak machine resources during reconnects.
async fn ensure_agent_for_session(
    state: &AppState,
    sender: &mut futures_util::stream::SplitSink<WebSocket, Message>,
    agent: &mut Option<crate::agent::Agent>,
    memory_session_id: &str,
    seed_messages: &[crate::providers::ChatMessage],
) -> bool {
    if agent.is_some() {
        return true;
    }

    let config = state.config.lock().clone();
    let mut new_agent =
        match crate::agent::Agent::from_config_with_mcp_registry(&config, state.mcp_registry())
            .await
        {
            Ok(agent) => agent,
            Err(e) => {
                tracing::error!(error = %e, "Agent initialization failed");
                let err = serde_json::json!({
                    "type": "error",
                    "message": format!("Failed to initialise agent: {e}"),
                    "code": "AGENT_INIT_FAILED"
                });
                let _ = sender.send(Message::Text(err.to_string().into())).await;
                let _ = sender
                    .send(Message::Close(Some(axum::extract::ws::CloseFrame {
                        code: 1011,
                        reason: axum::extract::ws::Utf8Bytes::from_static(
                            "Agent initialization failed",
                        ),
                    })))
                    .await;
                return false;
            }
        };
    new_agent.set_memory_session_id(Some(memory_session_id.to_string()));
    if !seed_messages.is_empty() {
        new_agent.seed_history(seed_messages);
    }
    *agent = Some(new_agent);
    true
}

/// Extract a `<tag>...</tag>` block from `page` by name, returning the
/// substring including both delimiters. Returns `None` when the open tag
/// is missing or no matching close tag follows it.
fn extract_xml_block(page: &str, tag: &str) -> Option<String> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let start = page.find(&open)?;
    let end = page[start..].find(&close)? + start + close.len();
    Some(page[start..end].to_string())
}

/// Extract the Architect's `<editor-state>...</editor-state>` block from a
/// `page_context` string, if present.  The Architect frontend embeds this
/// block on every chat turn so the LLM sees the current YAML and the
/// agent's runtime guard knows to hide workflow persistence tools.  Returns
/// `None` for non-Architect chats (no marker present) or malformed input
/// (open tag without a matching close tag).
fn architect_editor_state_block(page: &str) -> Option<String> {
    extract_xml_block(page, "editor-state")
}

/// Extract the Architect's `<architect-instructions>...</architect-instructions>`
/// block from a `page_context` string, if present.  The Architect frontend
/// embeds the system preface inside this block so it actually reaches the LLM
/// (operator-role chat messages stay client-side and never travel the WS).
/// Returns `None` when the block is absent.
fn architect_instructions_block(page: &str) -> Option<String> {
    extract_xml_block(page, "architect-instructions")
}

/// Build a context-aware system hint based on the dashboard page the user is viewing.
///
/// Returns `None` for unknown pages or the main chat — only Agent Pool and
/// Agent Teams pages get specialised instructions.
fn page_context_hint(page: &str) -> Option<&'static str> {
    match page {
        "agent_pool" => Some(concat!(
            "[Page context: The user is on the **Agent Pool** page.\n",
            "Available tools:\n",
            "- `construct-operator__save_agent_template` — Create/update an agent\n",
            "- `construct-operator__search_agent_pool` — Search agents by query\n",
            "- `construct-operator__list_agent_templates` — List all agents (returns kref, name, role, etc.)\n\n",
            "When creating agents, collect: name, role (coder/researcher/reviewer/specialist), ",
            "expertise areas, preferred model (codex/claude), identity, soul, tone, and optionally system_hint.\n",
            "Guide the user conversationally.\n\n",
            "IMPORTANT behavioral rules:\n",
            "- A tool returning empty content or no error means SUCCESS. Verify by calling list_agent_templates after.\n",
            "- NEVER say a tool is broken or file a bug report. If something seems off, retry or verify.\n",
            "- Do NOT ask the user to use the dashboard UI instead — YOU are the assistant, handle it.\n",
            "- After creating/updating, confirm success by listing agents to show the result.]\n\n",
        )),
        "agent_teams" => Some(concat!(
            "[Page context: The user is on the **Agent Teams** page.\n",
            "Available tools:\n",
            "- `construct-operator__create_team` — Create/update a team with members and edges\n",
            "- `construct-operator__list_agent_templates` — List all agents (returns kref for member_krefs)\n",
            "- `construct-operator__search_agent_pool` — Search agents by query\n",
            "- `construct-operator__list_teams` — List existing teams\n",
            "- `construct-operator__get_team` — Get team details with members and edges\n\n",
            "When creating teams: collect a name, description, and select member agents.\n",
            "Use the `kref` field from list_agent_templates for member_krefs — the system resolves names automatically.\n",
            "Define edges (SUPPORTS, DEPENDS_ON, REPORTS_TO) between members to express the team structure.\n\n",
            "IMPORTANT behavioral rules:\n",
            "- A tool returning empty content or no error means SUCCESS. Verify by calling list_teams after.\n",
            "- NEVER say a tool is broken or file a bug report. If something seems off, retry or verify.\n",
            "- Do NOT ask the user to use the dashboard UI instead — YOU are the assistant, handle it.\n",
            "- After creating a team, confirm success by calling list_teams or get_team to show the result.\n",
            "- member_krefs accepts agent names, partial krefs, or full krefs — the resolver handles matching.]\n\n",
        )),
        "skills" => Some(concat!(
            "[Page context: The user is on the **Skills Library** page.\n",
            "Skills are reusable behavioral procedures stored in CognitiveMemory/Skills.\n",
            "Available tools:\n",
            "- `construct-operator__save_skill` — Create/update a skill (if available)\n",
            "- `construct-operator__list_agent_templates` — List agents (skills may reference agents)\n",
            "- `construct-operator__search_clawhub` — Search ClawHub public marketplace for community skills\n",
            "- `construct-operator__browse_clawhub` — Browse trending skills on ClawHub\n",
            "- `construct-operator__install_from_clawhub` — Install a skill from ClawHub by slug\n\n",
            "A skill has: name, description, content (the procedure text), domain ",
            "(Memory/Creative/Privacy/Graph/Behavioral/Other), and tags.\n",
            "Guide the user through defining skills conversationally — help them articulate ",
            "the procedure, choose the right domain, and write clear content.\n",
            "When users want to find existing skills, search ClawHub first before creating from scratch.\n\n",
            "IMPORTANT behavioral rules:\n",
            "- A tool returning empty content or no error means SUCCESS. Verify after.\n",
            "- NEVER say a tool is broken or file a bug report.\n",
            "- Do NOT ask the user to use the dashboard UI instead — YOU are the assistant.]\n\n",
        )),
        "workflows" => Some(concat!(
            "[Page context: The user is on the **Workflows** page.\n",
            "Available tools: create_workflow, list_workflows, validate_workflow, run_workflow, ",
            "get_workflow_status, cancel_workflow, resume_workflow, dry_run_workflow, ",
            "recall_workflow_runs, get_workflow_run_detail, save_workflow_preset, list_workflow_presets ",
            "(all prefixed with `construct-operator__`).\n\n",
            "## Workflow schema (use this EXACTLY with create_workflow):\n",
            "```yaml\n",
            "workflow_def:\n",
            "  name: my-workflow          # kebab-case identifier\n",
            "  description: What it does\n",
            "  tags: [tag1, tag2]         # optional\n",
            "  inputs:                    # optional\n",
            "    - name: task\n",
            "      required: false\n",
            "      default: default value\n",
            "  steps:\n",
            "    - id: research_step\n",
            "      name: Research Phase\n",
            "      action: research       # research | code | review | deploy | test | build | notify | approve | summarize | task | human_input\n",
            "      description: Research the topic using ${inputs.task}\n",
            "      agent_hints: [researcher]  # hints for operator: coder | researcher | reviewer\n",
            "      depends_on: []\n",
            "    - id: code_step\n",
            "      name: Implementation\n",
            "      action: code\n",
            "      description: Implement based on ${research_step.output}\n",
            "      agent_hints: [coder]\n",
            "      depends_on: [research_step]\n",
            "    - id: review_step\n",
            "      name: Code Review\n",
            "      action: review\n",
            "      description: Review ${code_step.output}\n",
            "      agent_hints: [reviewer]\n",
            "      depends_on: [code_step]\n",
            "    - id: feedback_step\n",
            "      name: Get User Feedback\n",
            "      action: human_input\n",
            "      description: Please review the output and provide feedback\n",
            "      channel: dashboard       # dashboard | slack | discord\n",
            "      depends_on: [review_step]\n",
            "```\n",
            "The `action` field determines which agent type runs the step:\n",
            "  research → researcher (claude), code → coder (codex), review → reviewer (claude),\n",
            "  deploy/test/build → codex, notify/summarize → claude, task → generic claude,\n",
            "  human_input → pauses workflow and sends a prompt to a channel (dashboard/slack/discord), waits for human response.\n",
            "The `description` field is the agent's prompt — use ${step_id.output} and ${inputs.X} for interpolation.\n",
            "`agent_hints` are optional suggestions (operator auto-selects if omitted).\n",
            "For advanced use, add explicit `type` + config block (agent/shell/goto/output/human_approval).\n\n",
            "Rules:\n",
            "- create_workflow validates internally and returns {saved, path, valid, registered}. Trust it — do NOT call list_workflows or validate_workflow to verify.\n",
            "- One tool call is enough for creation. Keep it simple.\n",
            "- When the user says 'research agent', '3 agents', 'coder', etc., map to the right action.\n",
            "- When running a workflow, always provide the cwd parameter.\n",
            "- Do NOT ask the user to use the UI instead — handle it yourself.]\n\n",
        )),
        "canvas" => Some(concat!(
            "[Page context: The user is on the **Live Canvas** page.\n",
            "The canvas is YOUR primary output — render visual content IMMEDIATELY.\n\n",
            "Available tools:\n",
            "- `construct-operator__render_canvas` — Push content to the canvas (html, svg, markdown, text)\n",
            "- `construct-operator__clear_canvas` — Clear a canvas\n\n",
            "ALWAYS render to the canvas. The user opened this page to SEE visual output.\n",
            "Use it for:\n",
            "- Interactive HTML dashboards with charts, tables, and metrics\n",
            "- SVG diagrams, flowcharts, architecture maps, or data visualizations\n",
            "- Formatted reports, comparisons, or analyses\n",
            "- Any content that benefits from visual presentation\n\n",
            "CRITICAL rules:\n",
            "- ALWAYS call render_canvas — do NOT just describe what you would render.\n",
            "- For HTML: include ALL CSS inline. Use a dark theme (bg: #1a1a2e, text: #e2e8f0).\n",
            "  Include modern styling with gradients, rounded corners, and clean typography.\n",
            "- For SVG: provide complete <svg> with viewBox for responsive sizing.\n",
            "- For charts: use inline CSS/HTML tables or SVG — no external JS libraries.\n",
            "- Keep content self-contained — no external resources, CDNs, or imports.\n",
            "- Default canvas_id is 'default'. You can use separate canvas_ids for multiple views.\n",
            "- If the user asks a question, answer it AND render relevant visual content.\n",
            "- Iterate: if the user gives feedback, re-render with improvements.]\n\n",
        )),
        _ => None,
    }
}

/// Process a single chat message through the agent and send the response.
///
/// Uses [`Agent::turn_streamed`] so that intermediate text chunks, tool calls,
/// and tool results are forwarded to the WebSocket client in real time.
/// Maximum characters of inlined document text we'll embed per attachment.
/// 200 KB ≈ 50K tokens depending on the tokenizer — generous enough for
/// typical source files / specs, small enough to not blow the context
/// window when the user attaches several at once. Files larger than this
/// are truncated with a `[…truncated]` marker so the LLM sees what's
/// missing rather than silently losing data.
const MAX_INLINED_DOC_CHARS: usize = 200_000;

/// Build a leading text block describing the user's attachments for the
/// current turn. Returns an empty string when there are none. Image
/// attachments emit `[IMAGE:/path]` markers (picked up by
/// `multimodal::prepare_messages_for_provider` and converted to content
/// blocks for vision-capable providers). Non-image attachments are read
/// as UTF-8 and wrapped in named delimiters; binary files we can't
/// decode get a one-line description so the LLM at least knows they were
/// shared.
async fn build_attachment_prefix(metas: &[super::api_attachments::AttachmentMeta]) -> String {
    use std::fmt::Write as _;

    if metas.is_empty() {
        return String::new();
    }

    let mut out = String::new();
    for meta in metas {
        if meta.is_image() {
            // Existing image-marker pipeline handles base64 conversion +
            // size/mime validation at provider-prepare time.
            let _ = writeln!(out, "[IMAGE:{}]", meta.path.display());
            continue;
        }
        match tokio::fs::read(&meta.path).await {
            Ok(bytes) => match std::str::from_utf8(&bytes) {
                Ok(text) => {
                    let truncated;
                    let body: &str = if text.chars().count() > MAX_INLINED_DOC_CHARS {
                        truncated = format!(
                            "{}…\n[…truncated at {} chars]",
                            text.chars().take(MAX_INLINED_DOC_CHARS).collect::<String>(),
                            MAX_INLINED_DOC_CHARS
                        );
                        truncated.as_str()
                    } else {
                        text
                    };
                    let _ = writeln!(
                        out,
                        "[Attached file: {} ({} bytes, {})]\n{}\n[End of file: {}]",
                        meta.filename, meta.size, meta.mime, body, meta.filename
                    );
                }
                Err(_) => {
                    let _ = writeln!(
                        out,
                        "[Attached binary file: {} ({} bytes, {}) — content not inlined]",
                        meta.filename, meta.size, meta.mime
                    );
                }
            },
            Err(err) => {
                tracing::warn!(
                    err = %err,
                    file_id = %meta.file_id,
                    "failed to read attachment for inlining"
                );
                let _ = writeln!(
                    out,
                    "[Attached file unavailable: {} ({})]",
                    meta.filename, meta.mime
                );
            }
        }
    }
    if !out.is_empty() {
        out.push('\n');
    }
    out
}

/// Extract the optional `attachments: ["file_id", ...]` array from a parsed
/// WS message payload. Returns an empty Vec if the field is missing,
/// malformed, or contains non-string entries — never panics or rejects
/// the surrounding message.
fn parse_attachments(parsed: &serde_json::Value) -> Vec<String> {
    parsed["attachments"]
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .filter(|s| !s.is_empty())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default()
}

#[allow(clippy::too_many_arguments)]
async fn process_chat_message(
    state: &AppState,
    agent: &mut crate::agent::Agent,
    sender: &mut futures_util::stream::SplitSink<WebSocket, Message>,
    receiver: &mut futures_util::stream::SplitStream<WebSocket>,
    content: &str,
    session_key: &str,
    page_context: Option<&str>,
    attachments: &[String],
    broadcast_rx: &mut tokio::sync::broadcast::Receiver<serde_json::Value>,
) {
    use crate::agent::TurnEvent;

    let provider_label = state
        .config
        .lock()
        .default_provider
        .clone()
        .unwrap_or_else(|| "unknown".to_string());

    // Broadcast agent_start event
    let _ = state.event_tx.send(serde_json::json!({
        "type": "agent_start",
        "provider": provider_label,
        "model": state.model,
    }));

    // Set session state to running
    let turn_id = uuid::Uuid::new_v4().to_string();
    if let Some(ref backend) = state.session_backend {
        let _ = backend.set_session_state(session_key, "running", Some(&turn_id));
    }

    // Channel for streaming turn events from the agent.
    let (event_tx, mut event_rx) = tokio::sync::mpsc::channel::<TurnEvent>(64);

    // Run the streamed turn concurrently: the agent produces events
    // while we forward them to the WebSocket below.  We cannot move
    // `agent` into a spawned task (it is `&mut`), so we use a join
    // instead — `turn_streamed` writes to the channel and we drain it
    // from the other branch.
    // Resolve any attachment file_ids the client included on this message.
    // Images become `[IMAGE:/path]` markers — picked up by the existing
    // multimodal pipeline so vision-capable providers see them as content
    // blocks. Non-image files get inlined as text wrapped in delimiters
    // when they're UTF-8 readable; binary blobs we can't decode produce a
    // descriptive placeholder instead of failing the turn.
    let attachment_prefix = if attachments.is_empty() {
        String::new()
    } else {
        let workspace_dir = state.config.lock().workspace_dir.clone();
        // Uploads land in `<workspace>/attachments/<session_id>/...` keyed
        // on the bare session UUID — strip the gateway's `gw_` prefix from
        // `session_key` so the resolver looks in the right directory. The
        // earlier `rsplit(':')` was a no-op against the `gw_<uuid>` format
        // and silently dropped every attachment on the floor.
        let session_id = session_key
            .strip_prefix(GW_SESSION_PREFIX)
            .unwrap_or(session_key);
        let resolved =
            super::api_attachments::resolve_for_session(&workspace_dir, session_id, attachments)
                .await;
        build_attachment_prefix(&resolved).await
    };

    let content_with_attachments = if attachment_prefix.is_empty() {
        content.to_string()
    } else {
        format!("{attachment_prefix}{content}")
    };

    let content_owned = if let Some(hint) = page_context.and_then(page_context_hint) {
        format!("{hint}{content_with_attachments}")
    } else if let Some(architect_block) = page_context.and_then(architect_editor_state_block) {
        // Architect mode: embed the editor-state block so (a) the LLM sees
        // the editor's current YAML as documented in the Architect system
        // preface, and (b) the agent loop's runtime tool guard can detect
        // Architect mode via the `<editor-state>` substring and strip the
        // workflow persistence tools from the spec list. Regular Operator
        // chats never carry the marker, so this branch is a no-op for them.
        //
        // The Architect frontend also ships the system preface inside an
        // `<architect-instructions>` block in the same page_context. We
        // prepend it here so the LLM actually sees the rules on every turn
        // — they used to be pushed via `appendSystemMessage`, but
        // operator-role messages stay client-side and never travel the WS.
        let instructions_block = page_context
            .and_then(architect_instructions_block)
            .map(|b| format!("{b}\n\n"))
            .unwrap_or_default();
        format!("{instructions_block}{architect_block}\n\n{content_with_attachments}")
    } else {
        content_with_attachments
    };

    // Scope the tool-loop cost tracker so token usage reported mid-stream
    // (via StreamEvent::Usage) is recorded against the global CostTracker.
    // Without this scope, record_tool_loop_cost_usage is a no-op.
    let cost_tracking_context = state.cost_tracker.clone().map(|tracker| {
        let prices = Arc::new(state.config.lock().cost.prices.clone());
        crate::agent::cost::ToolLoopCostTrackingContext::new(tracker, prices)
    });
    let turn_fut = crate::agent::loop_::TOOL_LOOP_COST_TRACKING_CONTEXT
        .scope(cost_tracking_context, async {
            agent.turn_streamed(&content_owned, event_tx).await
        });

    // Drive the turn and relays in one select loop so the WebSocket can
    // receive a `stop` control frame while the agent is still working.
    // Keep sending protocol-level pings during the active turn as well as
    // during idle waits. Long provider/tool calls can otherwise produce no
    // outbound frames for minutes, which is enough for browser/proxy stacks
    // such as Cloudflare Tunnel to close the connection with client-side 1006.
    let mut ping_interval = tokio::time::interval(std::time::Duration::from_secs(30));
    ping_interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    ping_interval.tick().await;

    tokio::pin!(turn_fut);
    let result = loop {
        tokio::select! {
            result = &mut turn_fut => break Some(result),
            event = event_rx.recv() => {
                match event {
                    Some(event) => {
                        let ws_msg = match event {
                            TurnEvent::Chunk { delta } => {
                                serde_json::json!({ "type": "chunk", "content": delta })
                            }
                            TurnEvent::Thinking { delta } => {
                                serde_json::json!({ "type": "thinking", "content": delta })
                            }
                            TurnEvent::ToolCall { name, args } => {
                                serde_json::json!({ "type": "tool_call", "name": name, "args": args })
                            }
                            TurnEvent::ToolResult { name, output } => {
                                serde_json::json!({ "type": "tool_result", "name": name, "output": output })
                            }
                            TurnEvent::OperatorStatus { phase, detail } => {
                                serde_json::json!({ "type": "operator_status", "phase": phase, "detail": detail })
                            }
                        };
                        if sender.send(Message::Text(ws_msg.to_string().into())).await.is_err() {
                            tracing::warn!(session = %session_key, "WebSocket chat send failed during active turn");
                            break None;
                        }
                    }
                    None => {}
                }
            }
            bcast = broadcast_rx.recv() => {
                if let Ok(ev) = bcast {
                    if ev["type"].as_str() == Some("channel_event") {
                        let relay = serde_json::json!({
                            "type": "agent_event",
                            "event": ev["payload"],
                        });
                        if sender.send(Message::Text(relay.to_string().into())).await.is_err() {
                            tracing::warn!(session = %session_key, "WebSocket chat event relay failed during active turn");
                            break None;
                        }
                    }
                }
            }
            ws_msg = receiver.next() => {
                let text = match ws_msg {
                    Some(Ok(Message::Text(text))) => text,
                    Some(Ok(Message::Close(frame))) => {
                        tracing::info!(session = %session_key, ?frame, "WebSocket chat closed during active turn");
                        break None;
                    }
                    Some(Err(error)) => {
                        tracing::warn!(session = %session_key, %error, "WebSocket chat error during active turn");
                        break None;
                    }
                    None => {
                        tracing::info!(session = %session_key, "WebSocket chat stream ended during active turn");
                        break None;
                    }
                    _ => continue,
                };
                let parsed: serde_json::Value = match serde_json::from_str(&text) {
                    Ok(v) => v,
                    Err(_) => continue,
                };
                match parsed["type"].as_str().unwrap_or("") {
                    "stop" => break None,
                    "message" => {
                        let notice = serde_json::json!({
                            "type": "operator_status",
                            "phase": "queued",
                            "detail": "Current response is still running; the dashboard queues follow-up messages locally."
                        });
                        let _ = sender.send(Message::Text(notice.to_string().into())).await;
                    }
                    _ => {}
                }
            }
            _ = ping_interval.tick() => {
                if sender.send(Message::Ping(Vec::new().into())).await.is_err() {
                    tracing::warn!(session = %session_key, "WebSocket chat keepalive send failed during active turn");
                    break None;
                }
            }
        }
    };

    let Some(result) = result else {
        // Dropping `turn_fut` cancels the in-flight provider/tool future at the
        // next await point. Reset the persisted session state and tell the UI
        // to clear its streaming/progress state without treating this as an
        // agent error.
        if let Some(ref backend) = state.session_backend {
            let _ = backend.set_session_state(session_key, "idle", None);
        }
        let stopped = serde_json::json!({
            "type": "stopped",
            "message": "Stopped current Operator turn."
        });
        let _ = sender.send(Message::Text(stopped.to_string().into())).await;
        let _ = state.event_tx.send(serde_json::json!({
            "type": "agent_end",
            "provider": provider_label,
            "model": state.model,
        }));
        return;
    };

    match result {
        Ok(response) => {
            // Persist assistant response
            if let Some(ref backend) = state.session_backend {
                let assistant_msg = crate::providers::ChatMessage::assistant(&response);
                let _ = backend.append(session_key, &assistant_msg);
            }

            // Send chunk_reset so the client clears any accumulated draft
            // before the authoritative done message.
            let reset = serde_json::json!({ "type": "chunk_reset" });
            let _ = sender.send(Message::Text(reset.to_string().into())).await;

            let done = serde_json::json!({
                "type": "done",
                "full_response": response,
            });
            let _ = sender.send(Message::Text(done.to_string().into())).await;

            // Set session state to idle
            if let Some(ref backend) = state.session_backend {
                let _ = backend.set_session_state(session_key, "idle", None);
            }

            // Broadcast agent_end event
            let _ = state.event_tx.send(serde_json::json!({
                "type": "agent_end",
                "provider": provider_label,
                "model": state.model,
            }));
        }
        Err(e) => {
            // Set session state to error
            if let Some(ref backend) = state.session_backend {
                let _ = backend.set_session_state(session_key, "error", Some(&turn_id));
            }

            tracing::error!(error = %e, "Agent turn failed");
            let sanitized = crate::providers::sanitize_api_error(&e.to_string());
            let error_code = if sanitized.to_lowercase().contains("api key")
                || sanitized.to_lowercase().contains("authentication")
                || sanitized.to_lowercase().contains("unauthorized")
            {
                "AUTH_ERROR"
            } else if sanitized.to_lowercase().contains("provider")
                || sanitized.to_lowercase().contains("model")
            {
                "PROVIDER_ERROR"
            } else {
                "AGENT_ERROR"
            };
            let err = serde_json::json!({
                "type": "error",
                "message": sanitized,
                "code": error_code,
            });
            let _ = sender.send(Message::Text(err.to_string().into())).await;

            // Broadcast error event
            let _ = state.event_tx.send(serde_json::json!({
                "type": "error",
                "component": "ws_chat",
                "message": sanitized,
            }));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderMap;

    #[test]
    fn extract_ws_token_from_authorization_header() {
        let mut headers = HeaderMap::new();
        headers.insert("authorization", "Bearer zc_test123".parse().unwrap());
        assert_eq!(extract_ws_token(&headers, None), Some("zc_test123"));
    }

    #[test]
    fn extract_ws_token_from_subprotocol() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "sec-websocket-protocol",
            "construct.v1, bearer.zc_sub456".parse().unwrap(),
        );
        assert_eq!(extract_ws_token(&headers, None), Some("zc_sub456"));
    }

    #[test]
    fn extract_ws_token_from_query_param() {
        let headers = HeaderMap::new();
        assert_eq!(
            extract_ws_token(&headers, Some("zc_query789")),
            Some("zc_query789")
        );
    }

    #[test]
    fn extract_ws_token_precedence_header_over_subprotocol() {
        let mut headers = HeaderMap::new();
        headers.insert("authorization", "Bearer zc_header".parse().unwrap());
        headers.insert("sec-websocket-protocol", "bearer.zc_sub".parse().unwrap());
        assert_eq!(
            extract_ws_token(&headers, Some("zc_query")),
            Some("zc_header")
        );
    }

    #[test]
    fn extract_ws_token_precedence_subprotocol_over_query() {
        let mut headers = HeaderMap::new();
        headers.insert("sec-websocket-protocol", "bearer.zc_sub".parse().unwrap());
        assert_eq!(extract_ws_token(&headers, Some("zc_query")), Some("zc_sub"));
    }

    #[test]
    fn extract_ws_token_returns_none_when_empty() {
        let headers = HeaderMap::new();
        assert_eq!(extract_ws_token(&headers, None), None);
    }

    #[test]
    fn extract_ws_token_skips_empty_header_value() {
        let mut headers = HeaderMap::new();
        headers.insert("authorization", "Bearer ".parse().unwrap());
        assert_eq!(
            extract_ws_token(&headers, Some("zc_fallback")),
            Some("zc_fallback")
        );
    }

    #[test]
    fn extract_ws_token_skips_empty_query_param() {
        let headers = HeaderMap::new();
        assert_eq!(extract_ws_token(&headers, Some("")), None);
    }

    #[test]
    fn extract_ws_token_subprotocol_with_multiple_entries() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "sec-websocket-protocol",
            "construct.v1, bearer.zc_tok, other".parse().unwrap(),
        );
        assert_eq!(extract_ws_token(&headers, None), Some("zc_tok"));
    }

    #[test]
    fn architect_editor_state_block_extracts_marker_from_page_context() {
        // Mirrors what `web/src/construct/components/workflows/ArchitectPanel.tsx`
        // sends as `page_context` on every Architect chat turn.
        let page_context = "v2:workflow_editor:architect\n<editor-state>\n  <workflow_name>foo</workflow_name>\n  <current_yaml>\n    name: foo\n  </current_yaml>\n</editor-state>";
        let block = architect_editor_state_block(page_context).expect("marker present");
        assert!(block.starts_with("<editor-state>"));
        assert!(block.ends_with("</editor-state>"));
        assert!(block.contains("<workflow_name>foo</workflow_name>"));
    }

    #[test]
    fn architect_editor_state_block_returns_none_for_regular_chats() {
        assert!(architect_editor_state_block("agent_pool").is_none());
        assert!(architect_editor_state_block("").is_none());
        assert!(architect_editor_state_block("some random text").is_none());
        // Open without close is malformed and must not match.
        assert!(architect_editor_state_block("<editor-state>oops").is_none());
    }

    #[test]
    fn architect_instructions_block_extracts_preface_from_page_context() {
        // Mirrors the new `pageContext` envelope produced by `buildPageContext`
        // — the preface is wrapped in `<architect-instructions>` alongside
        // the existing `<editor-state>` block so it actually reaches the LLM.
        let page_context = "v2:workflow_editor:architect\n<architect-instructions>\n  You are the Architect.\n  CRITICAL: ...\n</architect-instructions>\n<editor-state>\n  <workflow_name>foo</workflow_name>\n</editor-state>";
        let block = architect_instructions_block(page_context).expect("marker present");
        assert!(block.starts_with("<architect-instructions>"));
        assert!(block.ends_with("</architect-instructions>"));
        assert!(block.contains("You are the Architect."));
    }

    #[test]
    fn architect_instructions_block_returns_none_when_absent() {
        let page_context = "v2:workflow_editor:architect\n<editor-state>\n</editor-state>";
        assert!(architect_instructions_block(page_context).is_none());
        assert!(architect_instructions_block("").is_none());
        assert!(architect_instructions_block("<architect-instructions>oops").is_none());
    }
}
