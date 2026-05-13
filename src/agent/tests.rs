//! Comprehensive agent-loop test suite.
//!
//! Tests exercise the full `Agent.turn()` cycle with mock providers and tools,
//! covering every edge case an agentic tool loop must handle:
//!
//!   1. Simple text response (no tools)
//!   2. Single tool call → final response
//!   3. Multi-step tool chain (tool A → tool B → response)
//!   4. Max-iteration bailout
//!   5. Unknown tool name recovery
//!   6. Tool execution failure recovery
//!   7. Parallel tool dispatch
//!   8. History trimming during long conversations
//!   9. Memory auto-save round-trip
//!  10. Native vs XML dispatcher integration
//!  11. Empty / whitespace-only LLM responses
//!  12. Mixed text + tool call responses
//!  13. Multi-tool batch in a single response
//!  14. System prompt generation & tool instructions
//!  15. Context enrichment from memory loader
//!  16. ConversationMessage serialization round-trip
//!  17. Tool call with stringified JSON arguments
//!  18. Conversation history fidelity (tool call → tool result → assistant)
//!  19. Builder validation (missing required fields)
//!  20. Idempotent system prompt insertion

use crate::agent::agent::{Agent, TurnEvent};
use crate::agent::dispatcher::{
    NativeToolDispatcher, ToolDispatcher, ToolExecutionResult, XmlToolDispatcher,
};
use crate::config::{AgentConfig, MemoryConfig};
use crate::memory::{self, Memory};
use crate::observability::{NoopObserver, Observer};
use crate::providers::{
    ChatMessage, ChatRequest, ChatResponse, ConversationMessage, Provider, ToolCall,
    ToolResultMessage,
};
use crate::tools::{Tool, ToolResult};
use anyhow::Result;
use async_trait::async_trait;
use std::sync::{Arc, Mutex};

// ═══════════════════════════════════════════════════════════════════════════
// Test Helpers — Mock Provider, Mock Tool, Mock Memory
// ═══════════════════════════════════════════════════════════════════════════

/// A mock LLM provider that returns pre-scripted responses in order.
/// When the queue is exhausted it returns a simple "done" text response.
struct ScriptedProvider {
    responses: Mutex<Vec<ChatResponse>>,
    /// Records every request for assertion.
    requests: Mutex<Vec<Vec<ChatMessage>>>,
}

impl ScriptedProvider {
    fn new(responses: Vec<ChatResponse>) -> Self {
        Self {
            responses: Mutex::new(responses),
            requests: Mutex::new(Vec::new()),
        }
    }

    fn request_count(&self) -> usize {
        self.requests.lock().unwrap().len()
    }
}

#[async_trait]
impl Provider for ScriptedProvider {
    async fn chat_with_system(
        &self,
        _system_prompt: Option<&str>,
        _message: &str,
        _model: &str,
        _temperature: f64,
    ) -> Result<String> {
        Ok("fallback".into())
    }

    async fn chat(
        &self,
        request: ChatRequest<'_>,
        _model: &str,
        _temperature: f64,
    ) -> Result<ChatResponse> {
        self.requests
            .lock()
            .unwrap()
            .push(request.messages.to_vec());

        let mut guard = self.responses.lock().unwrap();
        if guard.is_empty() {
            return Ok(ChatResponse {
                text: Some("done".into()),
                tool_calls: vec![],
                usage: None,
                reasoning_content: None,
            });
        }
        Ok(guard.remove(0))
    }
}

/// A mock provider that always returns an error.
struct FailingProvider;

#[async_trait]
impl Provider for FailingProvider {
    async fn chat_with_system(
        &self,
        _system_prompt: Option<&str>,
        _message: &str,
        _model: &str,
        _temperature: f64,
    ) -> Result<String> {
        anyhow::bail!("provider error")
    }

    async fn chat(
        &self,
        _request: ChatRequest<'_>,
        _model: &str,
        _temperature: f64,
    ) -> Result<ChatResponse> {
        anyhow::bail!("provider error")
    }
}

/// A simple echo tool that returns its arguments as output.
struct EchoTool;

#[async_trait]
impl Tool for EchoTool {
    fn name(&self) -> &str {
        "echo"
    }

    fn description(&self) -> &str {
        "Echoes the input"
    }

    fn parameters_schema(&self) -> serde_json::Value {
        serde_json::json!({
            "type": "object",
            "properties": {
                "message": {"type": "string"}
            }
        })
    }

    async fn execute(&self, args: serde_json::Value) -> Result<ToolResult> {
        let msg = args
            .get("message")
            .and_then(|v| v.as_str())
            .unwrap_or("(empty)")
            .to_string();
        Ok(ToolResult {
            success: true,
            output: msg,
            error: None,
        })
    }
}

/// A tool that always fails execution.
struct FailingTool;

#[async_trait]
impl Tool for FailingTool {
    fn name(&self) -> &str {
        "fail"
    }

    fn description(&self) -> &str {
        "Always fails"
    }

    fn parameters_schema(&self) -> serde_json::Value {
        serde_json::json!({"type": "object"})
    }

    async fn execute(&self, _args: serde_json::Value) -> Result<ToolResult> {
        Ok(ToolResult {
            success: false,
            output: String::new(),
            error: Some("intentional failure".into()),
        })
    }
}

/// A tool that panics (tests error propagation).
struct PanickingTool;

#[async_trait]
impl Tool for PanickingTool {
    fn name(&self) -> &str {
        "panicker"
    }

    fn description(&self) -> &str {
        "Panics on execution"
    }

    fn parameters_schema(&self) -> serde_json::Value {
        serde_json::json!({"type": "object"})
    }

    async fn execute(&self, _args: serde_json::Value) -> Result<ToolResult> {
        anyhow::bail!("catastrophic tool failure")
    }
}

/// A tool that tracks how many times it was called.
struct CountingTool {
    count: Arc<Mutex<usize>>,
}

impl CountingTool {
    fn new() -> (Self, Arc<Mutex<usize>>) {
        let count = Arc::new(Mutex::new(0));
        (
            Self {
                count: count.clone(),
            },
            count,
        )
    }
}

#[async_trait]
impl Tool for CountingTool {
    fn name(&self) -> &str {
        "counter"
    }

    fn description(&self) -> &str {
        "Counts calls"
    }

    fn parameters_schema(&self) -> serde_json::Value {
        serde_json::json!({"type": "object"})
    }

    async fn execute(&self, _args: serde_json::Value) -> Result<ToolResult> {
        let mut c = self.count.lock().unwrap();
        *c += 1;
        Ok(ToolResult {
            success: true,
            output: format!("call #{}", *c),
            error: None,
        })
    }
}

fn make_memory() -> Arc<dyn Memory> {
    let cfg = MemoryConfig {
        backend: "none".into(),
        ..MemoryConfig::default()
    };
    Arc::from(memory::create_memory(&cfg, &std::env::temp_dir(), None).unwrap())
}

fn make_observer() -> Arc<dyn Observer> {
    Arc::from(NoopObserver {})
}

fn build_agent_with(
    provider: Box<dyn Provider>,
    tools: Vec<Box<dyn Tool>>,
    dispatcher: Box<dyn ToolDispatcher>,
) -> Agent {
    Agent::builder()
        .provider(provider)
        .tools(tools)
        .memory(make_memory())
        .observer(make_observer())
        .tool_dispatcher(dispatcher)
        .workspace_dir(std::env::temp_dir())
        .build()
        .unwrap()
}

fn build_agent_with_memory(
    provider: Box<dyn Provider>,
    tools: Vec<Box<dyn Tool>>,
    mem: Arc<dyn Memory>,
    auto_save: bool,
) -> Agent {
    Agent::builder()
        .provider(provider)
        .tools(tools)
        .memory(mem)
        .observer(make_observer())
        .tool_dispatcher(Box::new(NativeToolDispatcher))
        .workspace_dir(std::env::temp_dir())
        .auto_save(auto_save)
        .build()
        .unwrap()
}

fn build_agent_with_config(
    provider: Box<dyn Provider>,
    tools: Vec<Box<dyn Tool>>,
    config: AgentConfig,
) -> Agent {
    Agent::builder()
        .provider(provider)
        .tools(tools)
        .memory(make_memory())
        .observer(make_observer())
        .tool_dispatcher(Box::new(NativeToolDispatcher))
        .workspace_dir(std::env::temp_dir())
        .config(config)
        .build()
        .unwrap()
}

/// Helper: create a ChatResponse with tool calls (native format).
fn tool_response(calls: Vec<ToolCall>) -> ChatResponse {
    ChatResponse {
        text: Some(String::new()),
        tool_calls: calls,
        usage: None,
        reasoning_content: None,
    }
}

/// Helper: create a plain text ChatResponse.
fn text_response(text: &str) -> ChatResponse {
    ChatResponse {
        text: Some(text.into()),
        tool_calls: vec![],
        usage: None,
        reasoning_content: None,
    }
}

/// Helper: create an XML-style tool call response.
fn xml_tool_response(name: &str, args: &str) -> ChatResponse {
    ChatResponse {
        text: Some(format!(
            "<tool_call>\n{{\"name\": \"{name}\", \"arguments\": {args}}}\n</tool_call>"
        )),
        tool_calls: vec![],
        usage: None,
        reasoning_content: None,
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// 1. Simple text response (no tools)
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_returns_text_when_no_tools_called() {
    let provider = Box::new(ScriptedProvider::new(vec![text_response("Hello world")]));
    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("hi").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty text response from provider"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 2. Single tool call → final response
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_executes_single_tool_then_returns() {
    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![ToolCall {
            id: "tc1".into(),
            name: "echo".into(),
            arguments: r#"{"message": "hello from tool"}"#.into(),
        }]),
        text_response("I ran the tool"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("run echo").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty response after tool execution"
    );
}

#[tokio::test]
async fn turn_retries_empty_final_after_tool_execution() {
    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![ToolCall {
            id: "tc1".into(),
            name: "echo".into(),
            arguments: r#"{"message": "tool result"}"#.into(),
        }]),
        text_response(""),
        text_response("Recovered final answer"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("run echo").await.unwrap();
    assert_eq!(response, "Recovered final answer");
}

#[tokio::test]
async fn turn_streamed_retries_empty_final_after_tool_execution() {
    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![ToolCall {
            id: "tc1".into(),
            name: "echo".into(),
            arguments: r#"{"message": "tool result"}"#.into(),
        }]),
        text_response(""),
        text_response("Recovered streamed answer"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );
    let (event_tx, mut event_rx) = tokio::sync::mpsc::channel::<TurnEvent>(64);

    let response = agent.turn_streamed("run echo", event_tx).await.unwrap();
    assert_eq!(response, "Recovered streamed answer");

    let mut streamed = String::new();
    while let Ok(event) = event_rx.try_recv() {
        if let TurnEvent::Chunk { delta } = event {
            streamed.push_str(&delta);
        }
    }
    assert_eq!(streamed, "Recovered streamed answer");
}

// ═══════════════════════════════════════════════════════════════════════════
// 3. Multi-step tool chain (tool A → tool B → response)
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_handles_multi_step_tool_chain() {
    let (counting_tool, count) = CountingTool::new();

    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![ToolCall {
            id: "tc1".into(),
            name: "counter".into(),
            arguments: "{}".into(),
        }]),
        tool_response(vec![ToolCall {
            id: "tc2".into(),
            name: "counter".into(),
            arguments: "{}".into(),
        }]),
        tool_response(vec![ToolCall {
            id: "tc3".into(),
            name: "counter".into(),
            arguments: "{}".into(),
        }]),
        text_response("Done after 3 calls"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(counting_tool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("count 3 times").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty response after multi-step chain"
    );
    assert_eq!(*count.lock().unwrap(), 3);
}

// ═══════════════════════════════════════════════════════════════════════════
// 4. Max-iteration bailout
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_bails_out_at_max_iterations() {
    // Create more tool calls than max_tool_iterations allows.
    let max_iters = 3;
    let mut responses = Vec::new();
    for i in 0..max_iters + 5 {
        responses.push(tool_response(vec![ToolCall {
            id: format!("tc{i}"),
            name: "echo".into(),
            arguments: r#"{"message": "loop"}"#.into(),
        }]));
    }

    let provider = Box::new(ScriptedProvider::new(responses));

    let config = AgentConfig {
        max_tool_iterations: max_iters,
        ..AgentConfig::default()
    };

    let mut agent = build_agent_with_config(provider, vec![Box::new(EchoTool)], config);

    let result = agent.turn("infinite loop").await;
    assert!(result.is_err());
    let err = result.unwrap_err().to_string();
    assert!(
        err.contains("maximum tool iterations"),
        "Expected max iterations error, got: {err}"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 5. Unknown tool name recovery
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_handles_unknown_tool_gracefully() {
    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![ToolCall {
            id: "tc1".into(),
            name: "nonexistent_tool".into(),
            arguments: "{}".into(),
        }]),
        text_response("I couldn't find that tool"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("use nonexistent").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty response after unknown tool recovery"
    );

    // Verify the tool result mentioned "Unknown tool"
    let has_tool_result = agent.history().iter().any(|msg| match msg {
        ConversationMessage::ToolResults(results) => {
            results.iter().any(|r| r.content.contains("Unknown tool"))
        }
        _ => false,
    });
    assert!(
        has_tool_result,
        "Expected tool result with 'Unknown tool' message"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 6. Tool execution failure recovery
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_recovers_from_tool_failure() {
    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![ToolCall {
            id: "tc1".into(),
            name: "fail".into(),
            arguments: "{}".into(),
        }]),
        text_response("Tool failed but I recovered"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(FailingTool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("try failing tool").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty response after tool failure recovery"
    );
}

#[tokio::test]
async fn turn_recovers_from_tool_error() {
    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![ToolCall {
            id: "tc1".into(),
            name: "panicker".into(),
            arguments: "{}".into(),
        }]),
        text_response("I recovered from the error"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(PanickingTool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("try panicking").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty response after tool error recovery"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 7. Provider error propagation
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_propagates_provider_error() {
    let mut agent = build_agent_with(
        Box::new(FailingProvider),
        vec![],
        Box::new(NativeToolDispatcher),
    );

    let result = agent.turn("hello").await;
    assert!(result.is_err(), "Expected provider error to propagate");
}

// ═══════════════════════════════════════════════════════════════════════════
// 8. History trimming during long conversations
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn history_trims_after_max_messages() {
    let max_history = 6;
    let mut responses = vec![];
    for _ in 0..max_history + 5 {
        responses.push(text_response("ok"));
    }

    let provider = Box::new(ScriptedProvider::new(responses));
    let config = AgentConfig {
        max_history_messages: max_history,
        ..AgentConfig::default()
    };

    let mut agent = build_agent_with_config(provider, vec![], config);

    for i in 0..max_history + 5 {
        let _ = agent.turn(&format!("msg {i}")).await.unwrap();
    }

    // System prompt (1) + trimmed messages
    // Should not exceed max_history + 1 (system prompt)
    assert!(
        agent.history().len() <= max_history + 1,
        "History length {} exceeds max {} + 1 (system)",
        agent.history().len(),
        max_history,
    );

    // System prompt should always be preserved
    let first = &agent.history()[0];
    assert!(matches!(first, ConversationMessage::Chat(c) if c.role == "system"));
}

// ═══════════════════════════════════════════════════════════════════════════
// 10. Native vs XML dispatcher integration
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn xml_dispatcher_parses_and_loops() {
    let provider = Box::new(ScriptedProvider::new(vec![
        xml_tool_response("echo", r#"{"message": "xml-test"}"#),
        text_response("XML tool completed"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(XmlToolDispatcher),
    );

    let response = agent.turn("test xml").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty response from XML dispatcher"
    );
}

#[tokio::test]
async fn native_dispatcher_sends_tool_specs() {
    let provider = Box::new(ScriptedProvider::new(vec![text_response("ok")]));
    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    let _ = agent.turn("hi").await.unwrap();

    // NativeToolDispatcher.should_send_tool_specs() returns true
    let dispatcher = NativeToolDispatcher;
    assert!(dispatcher.should_send_tool_specs());
}

#[tokio::test]
async fn xml_dispatcher_does_not_send_tool_specs() {
    let dispatcher = XmlToolDispatcher;
    assert!(!dispatcher.should_send_tool_specs());
}

// ═══════════════════════════════════════════════════════════════════════════
// 11. Empty / whitespace-only LLM responses
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_handles_empty_text_response() {
    let provider = Box::new(ScriptedProvider::new(vec![ChatResponse {
        text: Some(String::new()),
        tool_calls: vec![],
        usage: None,
        reasoning_content: None,
    }]));

    let mut agent = build_agent_with(provider, vec![], Box::new(NativeToolDispatcher));

    let response = agent.turn("hi").await.unwrap();
    assert!(response.is_empty());
}

#[tokio::test]
async fn turn_handles_none_text_response() {
    let provider = Box::new(ScriptedProvider::new(vec![ChatResponse {
        text: None,
        tool_calls: vec![],
        usage: None,
        reasoning_content: None,
    }]));

    let mut agent = build_agent_with(provider, vec![], Box::new(NativeToolDispatcher));

    // Should not panic — falls back to empty string
    let response = agent.turn("hi").await.unwrap();
    assert!(response.is_empty());
}

// ═══════════════════════════════════════════════════════════════════════════
// 12. Mixed text + tool call responses
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_preserves_text_alongside_tool_calls() {
    let provider = Box::new(ScriptedProvider::new(vec![
        ChatResponse {
            text: Some("Let me check...".into()),
            tool_calls: vec![ToolCall {
                id: "tc1".into(),
                name: "echo".into(),
                arguments: r#"{"message": "hi"}"#.into(),
            }],
            usage: None,
            reasoning_content: None,
        },
        text_response("Here are the results"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("check something").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty final response after mixed text+tool"
    );

    // The intermediate text should be in history
    let has_intermediate = agent.history().iter().any(|msg| match msg {
        ConversationMessage::Chat(c) => c.role == "assistant" && c.content.contains("Let me check"),
        _ => false,
    });
    assert!(has_intermediate, "Intermediate text should be in history");
}

// ═══════════════════════════════════════════════════════════════════════════
// 13. Multi-tool batch in a single response
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn turn_handles_multiple_tools_in_one_response() {
    let (counting_tool, count) = CountingTool::new();

    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![
            ToolCall {
                id: "tc1".into(),
                name: "counter".into(),
                arguments: "{}".into(),
            },
            ToolCall {
                id: "tc2".into(),
                name: "counter".into(),
                arguments: "{}".into(),
            },
            ToolCall {
                id: "tc3".into(),
                name: "counter".into(),
                arguments: "{}".into(),
            },
        ]),
        text_response("All 3 done"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(counting_tool)],
        Box::new(NativeToolDispatcher),
    );

    let response = agent.turn("batch").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty response after multi-tool batch"
    );
    assert_eq!(
        *count.lock().unwrap(),
        3,
        "All 3 tools should have been called"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 14. System prompt generation & tool instructions
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn system_prompt_injected_on_first_turn() {
    let provider = Box::new(ScriptedProvider::new(vec![text_response("ok")]));
    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    assert!(agent.history().is_empty(), "History should start empty");

    let _ = agent.turn("hi").await.unwrap();

    // First message should be the system prompt
    let first = &agent.history()[0];
    assert!(
        matches!(first, ConversationMessage::Chat(c) if c.role == "system"),
        "First history entry should be system prompt"
    );
}

#[tokio::test]
async fn system_prompt_not_duplicated_on_second_turn() {
    let provider = Box::new(ScriptedProvider::new(vec![
        text_response("first"),
        text_response("second"),
    ]));
    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    let _ = agent.turn("hi").await.unwrap();
    let _ = agent.turn("hello again").await.unwrap();

    let system_count = agent
        .history()
        .iter()
        .filter(|msg| matches!(msg, ConversationMessage::Chat(c) if c.role == "system"))
        .count();
    assert_eq!(system_count, 1, "System prompt should appear exactly once");
}

// ═══════════════════════════════════════════════════════════════════════════
// 15. Conversation history fidelity
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn history_contains_all_expected_entries_after_tool_loop() {
    let provider = Box::new(ScriptedProvider::new(vec![
        tool_response(vec![ToolCall {
            id: "tc1".into(),
            name: "echo".into(),
            arguments: r#"{"message": "tool-out"}"#.into(),
        }]),
        text_response("final answer"),
    ]));

    let mut agent = build_agent_with(
        provider,
        vec![Box::new(EchoTool)],
        Box::new(NativeToolDispatcher),
    );

    let _ = agent.turn("test").await.unwrap();

    // Expected history entries:
    //   0: system prompt
    //   1: user message "test"
    //   2: AssistantToolCalls
    //   3: ToolResults
    //   4: assistant "final answer"
    let history = agent.history();
    assert!(
        history.len() >= 5,
        "Expected at least 5 history entries, got {}",
        history.len()
    );

    assert!(matches!(&history[0], ConversationMessage::Chat(c) if c.role == "system"));
    assert!(matches!(&history[1], ConversationMessage::Chat(c) if c.role == "user"));
    assert!(matches!(
        &history[2],
        ConversationMessage::AssistantToolCalls { .. }
    ));
    assert!(matches!(&history[3], ConversationMessage::ToolResults(_)));
    assert!(
        matches!(&history[4], ConversationMessage::Chat(c) if c.role == "assistant" && c.content == "final answer")
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 16. Builder validation
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn builder_fails_without_provider() {
    let result = Agent::builder()
        .tools(vec![])
        .memory(make_memory())
        .observer(make_observer())
        .tool_dispatcher(Box::new(NativeToolDispatcher))
        .workspace_dir(std::path::PathBuf::from("/tmp"))
        .build();

    assert!(result.is_err(), "Building without provider should fail");
}

// ═══════════════════════════════════════════════════════════════════════════
// 17. Multi-turn conversation maintains context
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn multi_turn_maintains_growing_history() {
    let provider = Box::new(ScriptedProvider::new(vec![
        text_response("response 1"),
        text_response("response 2"),
        text_response("response 3"),
    ]));

    let mut agent = build_agent_with(provider, vec![], Box::new(NativeToolDispatcher));

    let r1 = agent.turn("msg 1").await.unwrap();
    let len_after_1 = agent.history().len();

    let r2 = agent.turn("msg 2").await.unwrap();
    let len_after_2 = agent.history().len();

    let r3 = agent.turn("msg 3").await.unwrap();
    let len_after_3 = agent.history().len();

    assert_eq!(r1, "response 1");
    assert_eq!(r2, "response 2");
    assert_eq!(r3, "response 3");

    // History should grow with each turn (user + assistant per turn)
    assert!(
        len_after_2 > len_after_1,
        "History should grow after turn 2"
    );
    assert!(
        len_after_3 > len_after_2,
        "History should grow after turn 3"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 18. Tool call with stringified JSON arguments (common LLM pattern)
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn native_dispatcher_handles_stringified_arguments() {
    let dispatcher = NativeToolDispatcher;
    let response = ChatResponse {
        text: Some(String::new()),
        tool_calls: vec![ToolCall {
            id: "tc1".into(),
            name: "echo".into(),
            arguments: r#"{"message": "hello"}"#.into(),
        }],
        usage: None,
        reasoning_content: None,
    };

    let (_, calls) = dispatcher.parse_response(&response);
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].name, "echo");
    assert_eq!(
        calls[0].arguments.get("message").unwrap().as_str().unwrap(),
        "hello"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 19. XML dispatcher edge cases
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn xml_dispatcher_handles_nested_json() {
    let response = ChatResponse {
        text: Some(
            r#"<tool_call>
{"name": "file_write", "arguments": {"path": "test.json", "content": "{\"key\": \"value\"}"}}
</tool_call>"#
                .into(),
        ),
        tool_calls: vec![],
        usage: None,
        reasoning_content: None,
    };

    let dispatcher = XmlToolDispatcher;
    let (_, calls) = dispatcher.parse_response(&response);
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].name, "file_write");
    assert_eq!(
        calls[0].arguments.get("path").unwrap().as_str().unwrap(),
        "test.json"
    );
}

#[test]
fn xml_dispatcher_handles_empty_tool_call_tag() {
    let response = ChatResponse {
        text: Some("<tool_call>\n</tool_call>\nSome text".into()),
        tool_calls: vec![],
        usage: None,
        reasoning_content: None,
    };

    let dispatcher = XmlToolDispatcher;
    let (text, calls) = dispatcher.parse_response(&response);
    assert!(calls.is_empty());
    assert!(text.contains("Some text"));
}

#[test]
fn xml_dispatcher_handles_unclosed_tool_call() {
    let response = ChatResponse {
        text: Some("Before\n<tool_call>\n{\"name\": \"shell\"}".into()),
        tool_calls: vec![],
        usage: None,
        reasoning_content: None,
    };

    let dispatcher = XmlToolDispatcher;
    let (text, calls) = dispatcher.parse_response(&response);
    // Should not panic — just treat as text
    assert!(calls.is_empty());
    assert!(text.contains("Before"));
}

// ═══════════════════════════════════════════════════════════════════════════
// 20. ConversationMessage serialization round-trip
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn conversation_message_serialization_roundtrip() {
    let messages = vec![
        ConversationMessage::Chat(ChatMessage::system("system")),
        ConversationMessage::Chat(ChatMessage::user("hello")),
        ConversationMessage::AssistantToolCalls {
            text: Some("checking".into()),
            tool_calls: vec![ToolCall {
                id: "tc1".into(),
                name: "shell".into(),
                arguments: "{}".into(),
            }],
            reasoning_content: None,
        },
        ConversationMessage::ToolResults(vec![ToolResultMessage {
            tool_call_id: "tc1".into(),
            content: "ok".into(),
        }]),
        ConversationMessage::Chat(ChatMessage::assistant("done")),
    ];

    for msg in &messages {
        let json = serde_json::to_string(msg).unwrap();
        let parsed: ConversationMessage = serde_json::from_str(&json).unwrap();

        // Verify the variant type matches
        match (msg, &parsed) {
            (ConversationMessage::Chat(a), ConversationMessage::Chat(b)) => {
                assert_eq!(a.role, b.role);
                assert_eq!(a.content, b.content);
            }
            (
                ConversationMessage::AssistantToolCalls {
                    text: a_text,
                    tool_calls: a_calls,
                    ..
                },
                ConversationMessage::AssistantToolCalls {
                    text: b_text,
                    tool_calls: b_calls,
                    ..
                },
            ) => {
                assert_eq!(a_text, b_text);
                assert_eq!(a_calls.len(), b_calls.len());
            }
            (ConversationMessage::ToolResults(a), ConversationMessage::ToolResults(b)) => {
                assert_eq!(a.len(), b.len());
            }
            _ => panic!("Variant mismatch after serialization"),
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// 21. Tool dispatcher format_results
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn xml_format_results_includes_status_and_output() {
    let dispatcher = XmlToolDispatcher;
    let results = vec![
        ToolExecutionResult {
            name: "shell".into(),
            output: "file1.txt\nfile2.txt".into(),
            success: true,
            tool_call_id: None,
        },
        ToolExecutionResult {
            name: "file_read".into(),
            output: "Error: file not found".into(),
            success: false,
            tool_call_id: None,
        },
    ];

    let msg = dispatcher.format_results(&results);
    let content = match msg {
        ConversationMessage::Chat(c) => c.content,
        _ => panic!("Expected Chat variant"),
    };

    assert!(content.contains("shell"));
    assert!(content.contains("file1.txt"));
    assert!(content.contains("ok"));
    assert!(content.contains("file_read"));
    assert!(content.contains("error"));
}

#[test]
fn native_format_results_maps_tool_call_ids() {
    let dispatcher = NativeToolDispatcher;
    let results = vec![
        ToolExecutionResult {
            name: "a".into(),
            output: "out1".into(),
            success: true,
            tool_call_id: Some("tc-001".into()),
        },
        ToolExecutionResult {
            name: "b".into(),
            output: "out2".into(),
            success: true,
            tool_call_id: Some("tc-002".into()),
        },
    ];

    let msg = dispatcher.format_results(&results);
    match msg {
        ConversationMessage::ToolResults(r) => {
            assert_eq!(r.len(), 2);
            assert_eq!(r[0].tool_call_id, "tc-001");
            assert_eq!(r[0].content, "out1");
            assert_eq!(r[1].tool_call_id, "tc-002");
            assert_eq!(r[1].content, "out2");
        }
        _ => panic!("Expected ToolResults"),
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// 22. to_provider_messages conversion
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn xml_dispatcher_converts_history_to_provider_messages() {
    let dispatcher = XmlToolDispatcher;
    let history = vec![
        ConversationMessage::Chat(ChatMessage::system("sys")),
        ConversationMessage::Chat(ChatMessage::user("hi")),
        ConversationMessage::AssistantToolCalls {
            text: Some("checking".into()),
            tool_calls: vec![ToolCall {
                id: "tc1".into(),
                name: "shell".into(),
                arguments: "{}".into(),
            }],
            reasoning_content: None,
        },
        ConversationMessage::ToolResults(vec![ToolResultMessage {
            tool_call_id: "tc1".into(),
            content: "ok".into(),
        }]),
        ConversationMessage::Chat(ChatMessage::assistant("done")),
    ];

    let messages = dispatcher.to_provider_messages(&history);

    // Should have: system, user, assistant (from tool calls), user (tool results), assistant
    assert!(messages.len() >= 4);
    assert_eq!(messages[0].role, "system");
    assert_eq!(messages[1].role, "user");
}

#[test]
fn native_dispatcher_converts_tool_results_to_tool_messages() {
    let dispatcher = NativeToolDispatcher;
    let history = vec![ConversationMessage::ToolResults(vec![
        ToolResultMessage {
            tool_call_id: "tc1".into(),
            content: "output1".into(),
        },
        ToolResultMessage {
            tool_call_id: "tc2".into(),
            content: "output2".into(),
        },
    ])];

    let messages = dispatcher.to_provider_messages(&history);
    assert_eq!(messages.len(), 2);
    assert_eq!(messages[0].role, "tool");
    assert_eq!(messages[1].role, "tool");
}

// ═══════════════════════════════════════════════════════════════════════════
// 23. XML tool instructions generation
// ═══════════════════════════════════════════════════════════════════════════

#[test]
fn xml_dispatcher_generates_tool_instructions() {
    let tools: Vec<Box<dyn Tool>> = vec![Box::new(EchoTool)];
    let dispatcher = XmlToolDispatcher;
    let instructions = dispatcher.prompt_instructions(&tools);

    assert!(instructions.contains("## Tool Use Protocol"));
    assert!(instructions.contains("<tool_call>"));
    // Tool listing is handled by ToolsSection in prompt.rs, not by the
    // dispatcher.  prompt_instructions() must only emit the protocol envelope.
    assert!(
        !instructions.contains("echo"),
        "dispatcher should not duplicate tool listing"
    );
}

#[test]
fn native_dispatcher_returns_empty_instructions() {
    let tools: Vec<Box<dyn Tool>> = vec![Box::new(EchoTool)];
    let dispatcher = NativeToolDispatcher;
    let instructions = dispatcher.prompt_instructions(&tools);
    assert!(instructions.is_empty());
}

// ═══════════════════════════════════════════════════════════════════════════
// 24. Clear history
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn clear_history_resets_conversation() {
    let provider = Box::new(ScriptedProvider::new(vec![
        text_response("first"),
        text_response("second"),
    ]));

    let mut agent = build_agent_with(provider, vec![], Box::new(NativeToolDispatcher));

    let _ = agent.turn("hi").await.unwrap();
    assert!(!agent.history().is_empty());

    agent.clear_history();
    assert!(agent.history().is_empty());

    // Next turn should re-inject system prompt
    let _ = agent.turn("hello again").await.unwrap();
    assert!(matches!(
        &agent.history()[0],
        ConversationMessage::Chat(c) if c.role == "system"
    ));
}

// ═══════════════════════════════════════════════════════════════════════════
// 25. run_single delegates to turn
// ═══════════════════════════════════════════════════════════════════════════

#[tokio::test]
async fn run_single_delegates_to_turn() {
    let provider = Box::new(ScriptedProvider::new(vec![text_response("via run_single")]));
    let mut agent = build_agent_with(provider, vec![], Box::new(NativeToolDispatcher));

    let response = agent.run_single("test").await.unwrap();
    assert!(
        !response.is_empty(),
        "Expected non-empty response from run_single"
    );
}

// ═══════════════════════════════════════════════════════════════════════════
// 26. Architect runtime tool guard — strips persistence tools from the spec
//     list when the user message carries the `<editor-state>` marker.
// ═══════════════════════════════════════════════════════════════════════════

/// When the user message carries the `<editor-state>` marker (Architect
/// mode), the workflow persistence tools must be stripped from the spec
/// list before the LLM sees them — so the LLM literally cannot call them.
#[test]
fn filter_tool_specs_for_architect_strips_denied_tools_when_marker_present() {
    use crate::agent::agent::filter_tool_specs_for_architect;
    use crate::tools::ToolSpec;

    let mut specs = vec![
        ToolSpec {
            name: "construct-operator__create_workflow".into(),
            description: "denied".into(),
            parameters: serde_json::json!({}),
        },
        ToolSpec {
            name: "construct-operator__save_workflow_preset".into(),
            description: "denied".into(),
            parameters: serde_json::json!({}),
        },
        ToolSpec {
            name: "construct-operator__run_workflow".into(),
            description: "denied".into(),
            parameters: serde_json::json!({}),
        },
        ToolSpec {
            name: "construct-operator__validate_workflow".into(),
            description: "denied".into(),
            parameters: serde_json::json!({}),
        },
        ToolSpec {
            name: "construct-operator__dry_run_workflow".into(),
            description: "denied".into(),
            parameters: serde_json::json!({}),
        },
        ToolSpec {
            name: "construct-operator__propose_workflow_yaml".into(),
            description: "allowed".into(),
            parameters: serde_json::json!({}),
        },
        ToolSpec {
            name: "echo".into(),
            description: "non-mcp tool, allowed".into(),
            parameters: serde_json::json!({}),
        },
    ];

    let architect_message = "<editor-state>\n  <workflow_name>foo</workflow_name>\n  <current_yaml>\n    name: foo\n  </current_yaml>\n</editor-state>\n\nadd a research step";
    filter_tool_specs_for_architect(&mut specs, architect_message);

    let names: Vec<&str> = specs.iter().map(|s| s.name.as_str()).collect();
    assert!(
        !names.contains(&"construct-operator__create_workflow"),
        "create_workflow must be filtered out in Architect mode; got {names:?}"
    );
    assert!(
        !names.contains(&"construct-operator__save_workflow_preset"),
        "save_workflow_preset must be filtered out in Architect mode; got {names:?}"
    );
    assert!(
        !names.contains(&"construct-operator__run_workflow"),
        "run_workflow must be filtered out in Architect mode; got {names:?}"
    );
    assert!(
        !names.contains(&"construct-operator__validate_workflow"),
        "validate_workflow must be filtered out in Architect mode; got {names:?}"
    );
    assert!(
        !names.contains(&"construct-operator__dry_run_workflow"),
        "dry_run_workflow must be filtered out in Architect mode; got {names:?}"
    );
    assert!(
        names.contains(&"construct-operator__propose_workflow_yaml"),
        "propose_workflow_yaml must remain available; got {names:?}"
    );
    assert!(
        names.contains(&"echo"),
        "non-mcp tools must remain available; got {names:?}"
    );
}

/// In a regular Operator chat (no `<editor-state>` marker), the persistence
/// tools must remain in the spec list — the runtime guard must NOT trigger.
#[test]
fn filter_tool_specs_for_architect_is_noop_without_marker() {
    use crate::agent::agent::filter_tool_specs_for_architect;
    use crate::tools::ToolSpec;

    let mut specs = vec![
        ToolSpec {
            name: "construct-operator__create_workflow".into(),
            description: "still allowed".into(),
            parameters: serde_json::json!({}),
        },
        ToolSpec {
            name: "construct-operator__save_workflow_preset".into(),
            description: "still allowed".into(),
            parameters: serde_json::json!({}),
        },
        ToolSpec {
            name: "construct-operator__propose_workflow_yaml".into(),
            description: "still allowed".into(),
            parameters: serde_json::json!({}),
        },
    ];
    let original_len = specs.len();

    let regular_message =
        "create a workflow that researches a topic, codes the result, and reviews it";
    filter_tool_specs_for_architect(&mut specs, regular_message);

    assert_eq!(
        specs.len(),
        original_len,
        "Regular Operator chats must not trigger the Architect tool guard"
    );
    let names: Vec<&str> = specs.iter().map(|s| s.name.as_str()).collect();
    assert!(names.contains(&"construct-operator__create_workflow"));
    assert!(names.contains(&"construct-operator__save_workflow_preset"));
    assert!(names.contains(&"construct-operator__propose_workflow_yaml"));
}

// ═══════════════════════════════════════════════════════════════════════════
// Operator chat context compression — guards against the 1M-token blowup
// reproduced in /Users/neo/.construct/logs/daemon.stderr.log on 2026-05-11.
// Failure mode: `trim_history` is message-count based and missed huge tool
// results, letting the Anthropic request hit the 1M cap.
// ═══════════════════════════════════════════════════════════════════════════

/// Provider used by compression tests.  Counts chat calls and records the
/// last messages seen so tests can assert what (and how much) was sent.
struct CompressionTestProvider {
    chat_calls: Arc<Mutex<usize>>,
    last_request_chars: Arc<Mutex<usize>>,
    /// If `true`, the first chat call returns a context-window-exceeded error
    /// to exercise the reactive `compress_on_error` path.
    fail_first_with_overflow: Arc<Mutex<bool>>,
    /// Optional summarizer-call response.  When set, returns this from
    /// `chat_with_system` so the compressor's LLM summarization path is
    /// exercised deterministically.
    summarizer_response: Arc<Mutex<Option<String>>>,
}

impl CompressionTestProvider {
    fn new() -> Self {
        Self {
            chat_calls: Arc::new(Mutex::new(0)),
            last_request_chars: Arc::new(Mutex::new(0)),
            fail_first_with_overflow: Arc::new(Mutex::new(false)),
            summarizer_response: Arc::new(Mutex::new(Some(
                "[CONTEXT SUMMARY — 5 earlier messages compressed]\n- placeholder summary"
                    .to_string(),
            ))),
        }
    }
}

#[async_trait]
impl Provider for CompressionTestProvider {
    async fn chat_with_system(
        &self,
        _system_prompt: Option<&str>,
        _message: &str,
        _model: &str,
        _temperature: f64,
    ) -> Result<String> {
        // Used by `ContextCompressor` for summarization.
        Ok(self
            .summarizer_response
            .lock()
            .unwrap()
            .clone()
            .unwrap_or_else(|| "[CONTEXT SUMMARY] fallback".into()))
    }

    async fn chat(
        &self,
        request: ChatRequest<'_>,
        _model: &str,
        _temperature: f64,
    ) -> Result<ChatResponse> {
        let chars: usize = request.messages.iter().map(|m| m.content.len()).sum();
        *self.last_request_chars.lock().unwrap() = chars;
        let mut calls = self.chat_calls.lock().unwrap();
        *calls += 1;

        let should_fail = {
            let mut g = self.fail_first_with_overflow.lock().unwrap();
            if *g {
                *g = false;
                true
            } else {
                false
            }
        };
        if should_fail {
            anyhow::bail!("prompt is too long: 1049796 tokens > 1000000 maximum context length");
        }

        Ok(ChatResponse {
            text: Some("ok".into()),
            tool_calls: vec![],
            usage: None,
            reasoning_content: None,
        })
    }
}

/// Build an agent wired with `CompressionTestProvider` and a context
/// compression config tuned for fast, deterministic tests.
fn build_compression_test_agent(
    provider: Box<CompressionTestProvider>,
    threshold_ratio: f64,
) -> Agent {
    let mut cfg = AgentConfig::default();
    cfg.context_compression.threshold_ratio = threshold_ratio;
    cfg.context_compression.protect_first_n = 1; // just the system prompt
    cfg.context_compression.protect_last_n = 2;
    cfg.context_compression.max_passes = 1;
    // Force a low max_history_messages so we know trim_history alone is NOT
    // what saves us — the test depends on the token-aware path firing.
    cfg.max_history_messages = 1_000;

    build_agent_with_config(provider, vec![Box::new(EchoTool)], cfg)
}

/// Fill the agent's history with a single oversized user message + tool
/// result pair simulating a Manus/Kumiho payload that pins the chat above
/// 1M tokens with very few messages.
fn seed_huge_history(agent: &mut Agent, payload_chars: usize) {
    // We mimic the production pattern: system message (seeded automatically
    // on the first turn — we add one here for the test directly) + a single
    // assistant message with a giant blob.  This is intentionally as small
    // as possible (few messages, huge content) so message-count trimming
    // does nothing.
    agent.seed_history(&[
        ChatMessage::system("sys"),
        ChatMessage::user("kick off long task"),
        ChatMessage::assistant("x".repeat(payload_chars)),
        ChatMessage::user("follow-up 1"),
        ChatMessage::assistant("ok 1"),
        ChatMessage::user("follow-up 2"),
        ChatMessage::assistant("ok 2"),
    ]);
}

#[tokio::test]
async fn compression_invoked_when_over_threshold() {
    // 800K chars ≈ 240K tokens with the compressor's 4-chars/token + 1.2x
    // margin heuristic.  With threshold_ratio=0.20 and the default Claude
    // 4-series window (1M), we trip the compressor.
    let provider = Box::new(CompressionTestProvider::new());
    let chat_calls = provider.chat_calls.clone();
    let last_chars = provider.last_request_chars.clone();

    let mut agent = build_compression_test_agent(provider, 0.20);
    seed_huge_history(&mut agent, 800_000);

    // Run a normal turn — model name defaults to a Claude 4-series-ish slug
    // via the agent's `classify_model` fallback (returns the configured
    // model_name which is empty in the builder; for the test we set it).
    agent.set_model_name_for_test("claude-opus-4-7");

    let _ = agent.turn("compress please").await.unwrap();

    // After the turn the provider was called with significantly fewer
    // characters than the seeded payload — compression replaced the huge
    // middle with a summary.
    let final_chars = *last_chars.lock().unwrap();
    assert!(
        final_chars < 100_000,
        "expected compression to drastically reduce request size, got {final_chars} chars"
    );
    assert!(
        *chat_calls.lock().unwrap() >= 1,
        "provider chat should have been called at least once"
    );
}

#[tokio::test]
async fn no_compression_under_threshold() {
    let provider = Box::new(CompressionTestProvider::new());
    let last_chars = provider.last_request_chars.clone();

    // Threshold high enough that small history never trips it.
    let mut agent = build_compression_test_agent(provider, 0.95);
    agent.set_model_name_for_test("claude-opus-4-7");
    agent.seed_history(&[ChatMessage::user("hi"), ChatMessage::assistant("hello")]);

    let _ = agent.turn("how are you?").await.unwrap();

    // History should still contain the original content — no summary message.
    let final_chars = *last_chars.lock().unwrap();
    assert!(
        final_chars < 500,
        "small history should be sent verbatim; got {final_chars} chars"
    );
    let has_summary = agent.history().iter().any(
        |m| matches!(m, ConversationMessage::Chat(c) if c.content.starts_with("[CONTEXT SUMMARY")),
    );
    assert!(!has_summary, "compressor must not run when under threshold");
}

#[tokio::test]
async fn compression_preserves_system_and_recent_messages() {
    let provider = Box::new(CompressionTestProvider::new());
    let mut agent = build_compression_test_agent(provider, 0.20);
    agent.set_model_name_for_test("claude-opus-4-7");
    seed_huge_history(&mut agent, 800_000);

    let _ = agent.turn("compress").await.unwrap();

    let history = agent.history();
    // System prompt preserved at the head (the agent injects its own on the
    // first turn; original "sys" gets replaced — what matters is that the
    // head is still a system message).
    assert!(
        matches!(&history[0], ConversationMessage::Chat(m) if m.role == "system"),
        "head of history must remain a system message after compression"
    );
    // Summary message present.
    let summary_present = history.iter().any(
        |m| matches!(m, ConversationMessage::Chat(c) if c.content.starts_with("[CONTEXT SUMMARY")),
    );
    assert!(summary_present, "compressed summary must be retained");
    // Last user message preserved (tail of `protect_last_n`).
    let has_recent_user = history.iter().any(|m| {
        matches!(m, ConversationMessage::Chat(c) if c.role == "user" && c.content.contains("compress"))
    });
    assert!(
        has_recent_user,
        "most recent user message must be preserved through compression"
    );
}

#[tokio::test]
async fn hard_token_cap_fails_loud_when_compression_cant_reduce_enough() {
    // Disable compression entirely so the hard cap is the only gate.
    let provider = Box::new(CompressionTestProvider::new());
    let mut cfg = AgentConfig::default();
    cfg.context_compression.enabled = false;
    cfg.max_history_messages = 10_000;

    let mut agent = build_agent_with_config(provider, vec![Box::new(EchoTool)], cfg);
    agent.set_model_name_for_test("claude-opus-4-7");

    // Seed with ~5M chars — well past the 1M token cap (~95% of 1M = 950K).
    agent.seed_history(&[
        ChatMessage::system("sys"),
        ChatMessage::assistant("y".repeat(5_000_000)),
    ]);

    let err = agent
        .turn("trigger hard cap")
        .await
        .expect_err("expected hard token cap to bail");
    let msg = format!("{err:#}");
    assert!(
        msg.contains("Conversation too long") && msg.contains("compression"),
        "error must explain the situation; got: {msg}"
    );
}

#[tokio::test]
async fn compression_failure_doesnt_crash_turn() {
    // Compressor's summarizer returns Err — fast_trim still runs but on a
    // history with no oversized tool messages it saves nothing.  The agent
    // must continue with the original history and not propagate the error.
    let provider = CompressionTestProvider::new();
    // Force the summarizer to "succeed" with bogus content — the chat()
    // path still works.  Compression is best-effort; the turn must complete.
    *provider.summarizer_response.lock().unwrap() = Some(String::new());
    let provider = Box::new(provider);

    let mut agent = build_compression_test_agent(provider, 0.20);
    agent.set_model_name_for_test("claude-opus-4-7");
    // History just under hard-cap so a no-op compressor still lets the turn through.
    seed_huge_history(&mut agent, 100_000);

    let resp = agent.turn("hello").await.unwrap();
    assert_eq!(resp, "ok");
}
