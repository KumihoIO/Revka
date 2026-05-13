use crate::auth::AuthService;
use crate::auth::openai_oauth::extract_account_id_from_jwt;
use crate::multimodal;
use crate::providers::ProviderRuntimeOptions;
use crate::providers::traits::{
    ChatMessage, ChatRequest, ChatResponse, Provider, ProviderCapabilities,
};
use async_trait::async_trait;
use futures_util::StreamExt;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::PathBuf;

const DEFAULT_CODEX_RESPONSES_URL: &str = "https://chatgpt.com/backend-api/codex/responses";
const CODEX_RESPONSES_URL_ENV: &str = "CONSTRUCT_CODEX_RESPONSES_URL";
const CODEX_BASE_URL_ENV: &str = "CONSTRUCT_CODEX_BASE_URL";
const DEFAULT_CODEX_INSTRUCTIONS: &str =
    "You are Construct, a concise and helpful coding assistant.";

pub struct OpenAiCodexProvider {
    auth: AuthService,
    auth_profile_override: Option<String>,
    responses_url: String,
    custom_endpoint: bool,
    gateway_api_key: Option<String>,
    reasoning_effort: Option<String>,
    client: Client,
}

#[derive(Debug, Serialize)]
struct ResponsesRequest {
    model: String,
    input: Vec<serde_json::Value>,
    instructions: String,
    store: bool,
    stream: bool,
    text: ResponsesTextOptions,
    reasoning: ResponsesReasoningOptions,
    include: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    tool_choice: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    parallel_tool_calls: Option<bool>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    tools: Vec<ResponsesTool>,
}

#[derive(Debug, Serialize)]
struct ResponsesTool {
    #[serde(rename = "type")]
    kind: String,
    name: String,
    description: String,
    parameters: serde_json::Value,
    strict: bool,
}

#[derive(Debug, Serialize)]
struct ResponsesTextOptions {
    verbosity: String,
}

#[derive(Debug, Serialize)]
struct ResponsesReasoningOptions {
    effort: String,
    summary: String,
}

#[derive(Debug, Deserialize)]
struct ResponsesResponse {
    #[serde(default)]
    output: Vec<ResponsesOutput>,
    #[serde(default)]
    output_text: Option<String>,
    #[serde(default)]
    usage: Option<ResponsesUsage>,
}

#[derive(Debug, Deserialize, Default)]
struct ResponsesUsage {
    #[serde(default)]
    input_tokens: Option<u64>,
    #[serde(default)]
    output_tokens: Option<u64>,
    #[serde(default)]
    input_tokens_details: Option<ResponsesUsageInputDetails>,
}

#[derive(Debug, Deserialize, Default)]
struct ResponsesUsageInputDetails {
    #[serde(default)]
    cached_tokens: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct ResponsesOutput {
    #[serde(rename = "type", default)]
    kind: Option<String>,
    #[serde(default)]
    content: Vec<ResponsesContent>,
    // function_call output fields
    #[serde(default)]
    name: Option<String>,
    #[serde(default)]
    arguments: Option<String>,
    #[serde(default)]
    call_id: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ResponsesContent {
    #[serde(rename = "type")]
    kind: Option<String>,
    text: Option<String>,
}

impl OpenAiCodexProvider {
    pub fn new(
        options: &ProviderRuntimeOptions,
        gateway_api_key: Option<&str>,
    ) -> anyhow::Result<Self> {
        let state_dir = options
            .construct_dir
            .clone()
            .unwrap_or_else(default_construct_dir);
        let auth = AuthService::new(&state_dir, options.secrets_encrypt);
        let responses_url = resolve_responses_url(options)?;

        Ok(Self {
            auth,
            auth_profile_override: options.auth_profile_override.clone(),
            custom_endpoint: !is_default_responses_url(&responses_url),
            responses_url,
            gateway_api_key: gateway_api_key.map(ToString::to_string),
            reasoning_effort: options.reasoning_effort.clone(),
            client: Client::builder()
                .connect_timeout(std::time::Duration::from_secs(10))
                .read_timeout(std::time::Duration::from_secs(300))
                .build()
                .unwrap_or_else(|_| Client::new()),
        })
    }
}

fn default_construct_dir() -> PathBuf {
    directories::UserDirs::new().map_or_else(
        || PathBuf::from(".construct"),
        |dirs| dirs.home_dir().join(".construct"),
    )
}

fn build_responses_url(base_or_endpoint: &str) -> anyhow::Result<String> {
    let candidate = base_or_endpoint.trim();
    if candidate.is_empty() {
        anyhow::bail!("OpenAI Codex endpoint override cannot be empty");
    }

    let mut parsed = reqwest::Url::parse(candidate)
        .map_err(|_| anyhow::anyhow!("OpenAI Codex endpoint override must be a valid URL"))?;

    match parsed.scheme() {
        "http" | "https" => {}
        _ => anyhow::bail!("OpenAI Codex endpoint override must use http:// or https://"),
    }

    let path = parsed.path().trim_end_matches('/');
    if !path.ends_with("/responses") {
        let with_suffix = if path.is_empty() || path == "/" {
            "/responses".to_string()
        } else {
            format!("{path}/responses")
        };
        parsed.set_path(&with_suffix);
    }

    parsed.set_query(None);
    parsed.set_fragment(None);

    Ok(parsed.to_string())
}

fn resolve_responses_url(options: &ProviderRuntimeOptions) -> anyhow::Result<String> {
    if let Some(endpoint) = std::env::var(CODEX_RESPONSES_URL_ENV)
        .ok()
        .and_then(|value| first_nonempty(Some(&value)))
    {
        return build_responses_url(&endpoint);
    }

    if let Some(base_url) = std::env::var(CODEX_BASE_URL_ENV)
        .ok()
        .and_then(|value| first_nonempty(Some(&value)))
    {
        return build_responses_url(&base_url);
    }

    if let Some(api_url) = options
        .provider_api_url
        .as_deref()
        .and_then(|value| first_nonempty(Some(value)))
    {
        return build_responses_url(&api_url);
    }

    Ok(DEFAULT_CODEX_RESPONSES_URL.to_string())
}

fn canonical_endpoint(url: &str) -> Option<(String, String, u16, String)> {
    let parsed = reqwest::Url::parse(url).ok()?;
    let host = parsed.host_str()?.to_ascii_lowercase();
    let port = parsed.port_or_known_default()?;
    let path = parsed.path().trim_end_matches('/').to_string();
    Some((parsed.scheme().to_ascii_lowercase(), host, port, path))
}

fn is_default_responses_url(url: &str) -> bool {
    canonical_endpoint(url) == canonical_endpoint(DEFAULT_CODEX_RESPONSES_URL)
}

fn first_nonempty(text: Option<&str>) -> Option<String> {
    text.and_then(|value| {
        let trimmed = value.trim();
        if trimmed.is_empty() {
            None
        } else {
            Some(trimmed.to_string())
        }
    })
}

fn resolve_instructions(system_prompt: Option<&str>) -> String {
    first_nonempty(system_prompt).unwrap_or_else(|| DEFAULT_CODEX_INSTRUCTIONS.to_string())
}

fn normalize_model_id(model: &str) -> &str {
    model.rsplit('/').next().unwrap_or(model)
}

fn build_responses_input(messages: &[ChatMessage]) -> (String, Vec<serde_json::Value>) {
    let mut system_parts: Vec<&str> = Vec::new();
    let mut input: Vec<serde_json::Value> = Vec::new();

    for msg in messages {
        match msg.role.as_str() {
            "system" => system_parts.push(&msg.content),
            "user" => {
                let (cleaned_text, image_refs) = multimodal::parse_image_markers(&msg.content);

                let mut content_items = Vec::new();

                if !cleaned_text.trim().is_empty() {
                    content_items.push(serde_json::json!({
                        "type": "input_text",
                        "text": cleaned_text,
                    }));
                }

                for image_ref in image_refs {
                    content_items.push(serde_json::json!({
                        "type": "input_image",
                        "image_url": image_ref,
                    }));
                }

                if content_items.is_empty() {
                    content_items.push(serde_json::json!({
                        "type": "input_text",
                        "text": "",
                    }));
                }

                input.push(serde_json::json!({
                    "role": "user",
                    "content": content_items,
                }));
            }
            "assistant" => {
                input.push(serde_json::json!({
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": msg.content,
                    }],
                }));
            }
            _ => {}
        }
    }

    let instructions = if system_parts.is_empty() {
        DEFAULT_CODEX_INSTRUCTIONS.to_string()
    } else {
        system_parts.join("\n\n")
    };

    (instructions, input)
}

fn clamp_reasoning_effort(model: &str, effort: &str) -> String {
    let id = normalize_model_id(model);
    // gpt-5-codex currently supports only low|medium|high.
    if id == "gpt-5-codex" {
        return match effort {
            "low" | "medium" | "high" => effort.to_string(),
            "minimal" => "low".to_string(),
            _ => "high".to_string(),
        };
    }
    if (id.starts_with("gpt-5.2") || id.starts_with("gpt-5.3")) && effort == "minimal" {
        return "low".to_string();
    }
    if id.starts_with("gpt-5-codex") && effort == "xhigh" {
        return "high".to_string();
    }
    if id == "gpt-5.1" && effort == "xhigh" {
        return "high".to_string();
    }
    if id == "gpt-5.1-codex-mini" {
        return if effort == "high" || effort == "xhigh" {
            "high".to_string()
        } else {
            "medium".to_string()
        };
    }
    effort.to_string()
}

fn resolve_reasoning_effort(model_id: &str, configured: Option<&str>) -> String {
    let raw = configured
        .map(ToString::to_string)
        .or_else(|| std::env::var("CONSTRUCT_CODEX_REASONING_EFFORT").ok())
        .and_then(|value| first_nonempty(Some(&value)))
        .unwrap_or_else(|| "xhigh".to_string())
        .to_ascii_lowercase();
    clamp_reasoning_effort(model_id, &raw)
}

fn nonempty_preserve(text: Option<&str>) -> Option<String> {
    text.and_then(|value| {
        if value.is_empty() {
            None
        } else {
            Some(value.to_string())
        }
    })
}

/// Extract both text and tool calls from a Responses API response.
fn extract_responses_text_and_tools(
    response: &ResponsesResponse,
) -> (Option<String>, Vec<crate::providers::ToolCall>) {
    let text = extract_responses_text(response);
    let mut tool_calls = Vec::new();

    for item in &response.output {
        if item.kind.as_deref() == Some("function_call") {
            if let (Some(name), Some(arguments)) = (&item.name, &item.arguments) {
                tool_calls.push(crate::providers::ToolCall {
                    id: item
                        .call_id
                        .clone()
                        .unwrap_or_else(|| format!("call_{}", uuid::Uuid::new_v4())),
                    name: name.clone(),
                    arguments: arguments.clone(),
                });
            }
        }
    }

    (text, tool_calls)
}

fn ensure_nonempty_response_output(
    text: &str,
    tool_calls: &[crate::providers::ToolCall],
) -> anyhow::Result<()> {
    if text.trim().is_empty() && tool_calls.is_empty() {
        anyhow::bail!(
            "OpenAI Codex Responses API returned empty text and no tool calls; treating as retryable empty completion"
        );
    }
    Ok(())
}

fn extract_responses_text(response: &ResponsesResponse) -> Option<String> {
    if let Some(text) = first_nonempty(response.output_text.as_deref()) {
        return Some(text);
    }

    for item in &response.output {
        for content in &item.content {
            if content.kind.as_deref() == Some("output_text") {
                if let Some(text) = first_nonempty(content.text.as_deref()) {
                    return Some(text);
                }
            }
        }
    }

    for item in &response.output {
        for content in &item.content {
            if let Some(text) = first_nonempty(content.text.as_deref()) {
                return Some(text);
            }
        }
    }

    None
}

fn extract_stream_event_text(event: &Value, saw_delta: bool) -> Option<String> {
    let event_type = event.get("type").and_then(Value::as_str);
    match event_type {
        Some("response.output_text.delta") => {
            nonempty_preserve(event.get("delta").and_then(Value::as_str))
        }
        Some("response.output_text.done") if !saw_delta => {
            nonempty_preserve(event.get("text").and_then(Value::as_str))
        }
        Some("response.completed" | "response.done") => event
            .get("response")
            .and_then(|value| serde_json::from_value::<ResponsesResponse>(value.clone()).ok())
            .and_then(|response| extract_responses_text(&response)),
        _ => None,
    }
}

fn parse_sse_text(body: &str) -> anyhow::Result<Option<String>> {
    let mut saw_delta = false;
    let mut delta_accumulator = String::new();
    let mut fallback_text = None;
    let mut buffer = body.to_string();

    let mut process_event = |event: Value| -> anyhow::Result<()> {
        if let Some(message) = extract_stream_error_message(&event) {
            return Err(anyhow::anyhow!("OpenAI Codex stream error: {message}"));
        }
        if let Some(text) = extract_stream_event_text(&event, saw_delta) {
            let event_type = event.get("type").and_then(Value::as_str);
            if event_type == Some("response.output_text.delta") {
                saw_delta = true;
                delta_accumulator.push_str(&text);
            } else if fallback_text.is_none() {
                fallback_text = Some(text);
            }
        }
        Ok(())
    };

    let mut process_chunk = |chunk: &str| -> anyhow::Result<()> {
        let data_lines: Vec<String> = chunk
            .lines()
            .filter_map(|line| line.strip_prefix("data:"))
            .map(|line| line.trim().to_string())
            .collect();
        if data_lines.is_empty() {
            return Ok(());
        }

        let joined = data_lines.join("\n");
        let trimmed = joined.trim();
        if trimmed.is_empty() || trimmed == "[DONE]" {
            return Ok(());
        }

        if let Ok(event) = serde_json::from_str::<Value>(trimmed) {
            return process_event(event);
        }

        for line in data_lines {
            let line = line.trim();
            if line.is_empty() || line == "[DONE]" {
                continue;
            }
            if let Ok(event) = serde_json::from_str::<Value>(line) {
                process_event(event)?;
            }
        }

        Ok(())
    };

    loop {
        let Some(idx) = buffer.find("\n\n") else {
            break;
        };

        let chunk = buffer[..idx].to_string();
        buffer = buffer[idx + 2..].to_string();
        process_chunk(&chunk)?;
    }

    if !buffer.trim().is_empty() {
        process_chunk(&buffer)?;
    }

    if saw_delta {
        return Ok(nonempty_preserve(Some(&delta_accumulator)));
    }

    Ok(fallback_text)
}

fn extract_stream_error_message(event: &Value) -> Option<String> {
    let event_type = event.get("type").and_then(Value::as_str);

    if event_type == Some("error") {
        return first_nonempty(
            event
                .get("message")
                .and_then(Value::as_str)
                .or_else(|| event.get("code").and_then(Value::as_str))
                .or_else(|| {
                    event
                        .get("error")
                        .and_then(|error| error.get("message"))
                        .and_then(Value::as_str)
                }),
        );
    }

    if event_type == Some("response.failed") {
        return first_nonempty(
            event
                .get("response")
                .and_then(|response| response.get("error"))
                .and_then(|error| error.get("message"))
                .and_then(Value::as_str),
        );
    }

    None
}

fn append_utf8_stream_chunk(
    body: &mut String,
    pending: &mut Vec<u8>,
    chunk: &[u8],
) -> anyhow::Result<()> {
    if pending.is_empty() {
        if let Ok(text) = std::str::from_utf8(chunk) {
            body.push_str(text);
            return Ok(());
        }
    }

    if !chunk.is_empty() {
        pending.extend_from_slice(chunk);
    }
    if pending.is_empty() {
        return Ok(());
    }

    match std::str::from_utf8(pending) {
        Ok(text) => {
            body.push_str(text);
            pending.clear();
            Ok(())
        }
        Err(err) => {
            let valid_up_to = err.valid_up_to();
            if valid_up_to > 0 {
                // SAFETY: `valid_up_to` always points to the end of a valid UTF-8 prefix.
                let prefix = std::str::from_utf8(&pending[..valid_up_to])
                    .expect("valid UTF-8 prefix from Utf8Error::valid_up_to");
                body.push_str(prefix);
                pending.drain(..valid_up_to);
            }

            if err.error_len().is_some() {
                return Err(anyhow::anyhow!(
                    "OpenAI Codex response contained invalid UTF-8: {err}"
                ));
            }

            // `error_len == None` means we have a valid prefix and an incomplete
            // multi-byte sequence at the end; keep it buffered until next chunk.
            Ok(())
        }
    }
}

fn decode_utf8_stream_chunks<'a, I>(chunks: I) -> anyhow::Result<String>
where
    I: IntoIterator<Item = &'a [u8]>,
{
    let mut body = String::new();
    let mut pending = Vec::new();

    for chunk in chunks {
        append_utf8_stream_chunk(&mut body, &mut pending, chunk)?;
    }

    if !pending.is_empty() {
        let err = std::str::from_utf8(&pending).expect_err("pending bytes should be invalid UTF-8");
        return Err(anyhow::anyhow!(
            "OpenAI Codex response ended with incomplete UTF-8: {err}"
        ));
    }

    Ok(body)
}

/// Read the response body incrementally via `bytes_stream()` to avoid
/// buffering the entire SSE payload in memory.  The previous implementation
/// used `response.text().await?` which holds the HTTP connection open until
/// every byte has arrived — on high-latency links the long-lived connection
/// often drops mid-read, producing the "error decoding response body" failure
/// reported in #3544.
async fn decode_responses_body(response: reqwest::Response) -> anyhow::Result<String> {
    let mut body = String::new();
    let mut pending_utf8 = Vec::new();
    let mut stream = response.bytes_stream();

    while let Some(chunk) = stream.next().await {
        let bytes = chunk
            .map_err(|err| anyhow::anyhow!("error reading OpenAI Codex response stream: {err}"))?;
        append_utf8_stream_chunk(&mut body, &mut pending_utf8, &bytes)?;
    }

    if !pending_utf8.is_empty() {
        let err = std::str::from_utf8(&pending_utf8)
            .expect_err("pending bytes should be invalid UTF-8 at end of stream");
        return Err(anyhow::anyhow!(
            "OpenAI Codex response ended with incomplete UTF-8: {err}"
        ));
    }

    if let Some(text) = parse_sse_text(&body)? {
        return Ok(text);
    }

    let body_trimmed = body.trim_start();
    let looks_like_sse = body_trimmed.starts_with("event:") || body_trimmed.starts_with("data:");
    if looks_like_sse {
        return Err(anyhow::anyhow!(
            "No response from OpenAI Codex stream payload: {}",
            super::sanitize_api_error(&body)
        ));
    }

    let parsed: ResponsesResponse = serde_json::from_str(&body).map_err(|err| {
        anyhow::anyhow!(
            "OpenAI Codex JSON parse failed: {err}. Payload: {}",
            super::sanitize_api_error(&body)
        )
    })?;
    extract_responses_text(&parsed).ok_or_else(|| anyhow::anyhow!("No response from OpenAI Codex"))
}

type ResponsesDecode = (
    String,
    Vec<crate::providers::ToolCall>,
    Option<crate::providers::traits::TokenUsage>,
);

fn parse_responses_body_with_tools(body: &str) -> anyhow::Result<ResponsesDecode> {
    // Try SSE streaming parse first — collect function_call events
    let mut tool_calls: Vec<crate::providers::ToolCall> = Vec::new();
    let mut text_result: Option<String> = None;
    let mut usage_result: Option<crate::providers::traits::TokenUsage> = None;

    // Parse the full SSE body looking for both text and function_call events
    let body_trimmed = body.trim_start();
    let looks_like_sse = body_trimmed.starts_with("event:") || body_trimmed.starts_with("data:");

    if looks_like_sse {
        // Parse SSE events to extract text deltas and function_call events.
        // The Responses API streams output items incrementally:
        //   response.output_item.added   → declares a new output item (text, function_call, reasoning)
        //   response.output_text.delta   → text content delta
        //   response.function_call_arguments.delta → function call arguments delta
        //   response.function_call_arguments.done  → function call complete
        //   response.output_item.done    → output item finalized
        //   response.completed           → response done (may have empty output array)
        let mut saw_delta = false;
        let mut delta_accumulator = String::new();

        // Track in-flight function calls by output_index
        struct PendingFunctionCall {
            name: String,
            call_id: String,
            arguments: String,
        }
        let mut pending_calls: std::collections::HashMap<u64, PendingFunctionCall> =
            std::collections::HashMap::new();

        for chunk in body.split("\n\n") {
            for line in chunk.lines() {
                if let Some(data) = line.strip_prefix("data:") {
                    let data = data.trim();
                    if data.is_empty() || data == "[DONE]" {
                        continue;
                    }
                    if let Ok(event) = serde_json::from_str::<Value>(data) {
                        let event_type = event.get("type").and_then(Value::as_str);
                        match event_type {
                            Some("response.output_text.delta") => {
                                if let Some(delta) = event.get("delta").and_then(Value::as_str) {
                                    saw_delta = true;
                                    delta_accumulator.push_str(delta);
                                }
                            }
                            // A new output item is being added — track function_call items
                            Some("response.output_item.added") => {
                                if let Some(item) = event.get("item") {
                                    if item.get("type").and_then(Value::as_str)
                                        == Some("function_call")
                                    {
                                        let output_index = event
                                            .get("output_index")
                                            .and_then(Value::as_u64)
                                            .unwrap_or(0);
                                        let name = item
                                            .get("name")
                                            .and_then(Value::as_str)
                                            .unwrap_or("")
                                            .to_string();
                                        let call_id = item
                                            .get("call_id")
                                            .and_then(Value::as_str)
                                            .unwrap_or("")
                                            .to_string();
                                        pending_calls.insert(
                                            output_index,
                                            PendingFunctionCall {
                                                name,
                                                call_id,
                                                arguments: String::new(),
                                            },
                                        );
                                    }
                                }
                            }
                            // Accumulate function call arguments
                            Some("response.function_call_arguments.delta") => {
                                let output_index = event
                                    .get("output_index")
                                    .and_then(Value::as_u64)
                                    .unwrap_or(0);
                                if let Some(delta) = event.get("delta").and_then(Value::as_str) {
                                    if let Some(pending) = pending_calls.get_mut(&output_index) {
                                        pending.arguments.push_str(delta);
                                    }
                                }
                            }
                            // Function call arguments complete — finalize the tool call
                            Some("response.function_call_arguments.done") => {
                                let output_index = event
                                    .get("output_index")
                                    .and_then(Value::as_u64)
                                    .unwrap_or(0);
                                // Use the full arguments from the done event if available
                                if let Some(args) = event.get("arguments").and_then(Value::as_str) {
                                    if let Some(pending) = pending_calls.get_mut(&output_index) {
                                        pending.arguments = args.to_string();
                                    }
                                }
                            }
                            Some("response.completed" | "response.done") => {
                                if let Some(resp_value) = event.get("response") {
                                    if let Ok(resp) = serde_json::from_value::<ResponsesResponse>(
                                        resp_value.clone(),
                                    ) {
                                        let (t, tc) = extract_responses_text_and_tools(&resp);
                                        if !tc.is_empty() {
                                            tool_calls = tc;
                                        }
                                        if text_result.is_none() {
                                            text_result = t;
                                        }
                                        if let Some(u) = token_usage_from_responses(&resp) {
                                            usage_result = Some(u);
                                        }
                                    }
                                }
                            }
                            _ => {}
                        }
                    }
                }
            }
        }

        // Collect pending function calls into tool_calls (if response.completed didn't provide them)
        if tool_calls.is_empty() && !pending_calls.is_empty() {
            let mut sorted: Vec<_> = pending_calls.into_iter().collect();
            sorted.sort_by_key(|(idx, _)| *idx);
            for (_, pending) in sorted {
                if !pending.name.is_empty() {
                    tool_calls.push(crate::providers::ToolCall {
                        id: if pending.call_id.is_empty() {
                            format!("call_{}", uuid::Uuid::new_v4())
                        } else {
                            pending.call_id
                        },
                        name: pending.name,
                        arguments: pending.arguments,
                    });
                }
            }
        }

        if saw_delta {
            text_result = Some(delta_accumulator);
        }

        if usage_result.is_none() {
            tracing::warn!(
                "OpenAI Codex SSE stream completed without a usage payload; cost tracking will record a zero-token request"
            );
        }

        let text = text_result.unwrap_or_default();
        ensure_nonempty_response_output(&text, &tool_calls)?;
        return Ok((text, tool_calls, usage_result));
    }

    // Non-SSE JSON response
    let parsed: ResponsesResponse = serde_json::from_str(&body).map_err(|err| {
        anyhow::anyhow!(
            "OpenAI Codex JSON parse failed: {err}. Payload: {}",
            super::sanitize_api_error(&body)
        )
    })?;
    let (text, tc) = extract_responses_text_and_tools(&parsed);
    let usage = token_usage_from_responses(&parsed);
    let text = text.unwrap_or_default();
    ensure_nonempty_response_output(&text, &tc)?;
    Ok((text, tc, usage))
}

/// Like `decode_responses_body` but also extracts function_call tool calls.
async fn decode_responses_body_with_tools(
    response: reqwest::Response,
) -> anyhow::Result<ResponsesDecode> {
    let mut body = String::new();
    let mut pending_utf8 = Vec::new();
    let mut stream = response.bytes_stream();

    while let Some(chunk) = stream.next().await {
        let bytes = chunk
            .map_err(|err| anyhow::anyhow!("error reading OpenAI Codex response stream: {err}"))?;
        append_utf8_stream_chunk(&mut body, &mut pending_utf8, &bytes)?;
    }

    if !pending_utf8.is_empty() {
        let err = std::str::from_utf8(&pending_utf8)
            .expect_err("pending bytes should be invalid UTF-8 at end of stream");
        return Err(anyhow::anyhow!(
            "OpenAI Codex response ended with incomplete UTF-8: {err}"
        ));
    }

    parse_responses_body_with_tools(&body)
}

fn token_usage_from_responses(
    resp: &ResponsesResponse,
) -> Option<crate::providers::traits::TokenUsage> {
    let u = resp.usage.as_ref()?;
    if u.input_tokens.is_none() && u.output_tokens.is_none() {
        return None;
    }
    Some(crate::providers::traits::TokenUsage {
        input_tokens: u.input_tokens,
        output_tokens: u.output_tokens,
        cached_input_tokens: u
            .input_tokens_details
            .as_ref()
            .and_then(|d| d.cached_tokens),
    })
}

impl OpenAiCodexProvider {
    async fn send_responses_request(
        &self,
        input: Vec<serde_json::Value>,
        instructions: String,
        model: &str,
    ) -> anyhow::Result<String> {
        let (text, _, _) = self
            .send_responses_request_inner(input, instructions, model, Vec::new())
            .await?;
        Ok(text)
    }

    async fn send_responses_request_inner(
        &self,
        input: Vec<serde_json::Value>,
        instructions: String,
        model: &str,
        tools: Vec<ResponsesTool>,
    ) -> anyhow::Result<(
        String,
        Vec<crate::providers::ToolCall>,
        Option<crate::providers::traits::TokenUsage>,
    )> {
        let use_gateway_api_key_auth = self.custom_endpoint && self.gateway_api_key.is_some();
        let profile = match self
            .auth
            .get_profile("openai-codex", self.auth_profile_override.as_deref())
            .await
        {
            Ok(profile) => profile,
            Err(err) if use_gateway_api_key_auth => {
                tracing::warn!(
                    error = %err,
                    "failed to load OpenAI Codex profile; continuing with custom endpoint API key mode"
                );
                None
            }
            Err(err) => return Err(err),
        };
        let oauth_access_token = match self
            .auth
            .get_valid_openai_access_token(self.auth_profile_override.as_deref())
            .await
        {
            Ok(token) => token,
            Err(err) if use_gateway_api_key_auth => {
                tracing::warn!(
                    error = %err,
                    "failed to refresh OpenAI token; continuing with custom endpoint API key mode"
                );
                None
            }
            Err(err) => return Err(err),
        };

        let account_id = profile.and_then(|profile| profile.account_id).or_else(|| {
            oauth_access_token
                .as_deref()
                .and_then(extract_account_id_from_jwt)
        });
        let access_token = if use_gateway_api_key_auth {
            oauth_access_token
        } else {
            Some(oauth_access_token.ok_or_else(|| {
                anyhow::anyhow!(
                    "OpenAI Codex auth profile not found. Run `construct auth login --provider openai-codex`."
                )
            })?)
        };
        let account_id = if use_gateway_api_key_auth {
            account_id
        } else {
            Some(account_id.ok_or_else(|| {
                anyhow::anyhow!(
                    "OpenAI Codex account id not found in auth profile/token. Run `construct auth login --provider openai-codex` again."
                )
            })?)
        };
        let normalized_model = normalize_model_id(model);

        let request = ResponsesRequest {
            model: normalized_model.to_string(),
            input,
            instructions,
            store: false,
            stream: true,
            text: ResponsesTextOptions {
                verbosity: "medium".to_string(),
            },
            reasoning: ResponsesReasoningOptions {
                effort: resolve_reasoning_effort(
                    normalized_model,
                    self.reasoning_effort.as_deref(),
                ),
                summary: "auto".to_string(),
            },
            include: vec!["reasoning.encrypted_content".to_string()],
            tool_choice: if tools.is_empty() {
                None
            } else {
                Some("auto".to_string())
            },
            parallel_tool_calls: if tools.is_empty() { None } else { Some(true) },
            tools,
        };

        let bearer_token = if use_gateway_api_key_auth {
            self.gateway_api_key.as_deref().unwrap_or_default()
        } else {
            access_token.as_deref().unwrap_or_default()
        };

        let mut request_builder = self
            .client
            .post(&self.responses_url)
            .header("Authorization", format!("Bearer {bearer_token}"))
            .header("OpenAI-Beta", "responses=experimental")
            .header("originator", "pi")
            .header("accept", "text/event-stream")
            .header("Content-Type", "application/json");

        if let Some(account_id) = account_id.as_deref() {
            request_builder = request_builder.header("chatgpt-account-id", account_id);
        }

        if use_gateway_api_key_auth {
            if let Some(access_token) = access_token.as_deref() {
                request_builder = request_builder.header("x-openai-access-token", access_token);
            }
            if let Some(account_id) = account_id.as_deref() {
                request_builder = request_builder.header("x-openai-account-id", account_id);
            }
        }

        tracing::info!(
            input_count = request.input.len(),
            tools_count = request.tools.len(),
            "Codex Responses API request"
        );

        let response = request_builder.json(&request).send().await?;

        if !response.status().is_success() {
            // Log the first few input items for debugging on error
            tracing::warn!(
                input_count = request.input.len(),
                tools_count = request.tools.len(),
                input_preview = %serde_json::to_string(&request.input.iter().take(3).collect::<Vec<_>>()).unwrap_or_default(),
                "Codex API request failed"
            );
            return Err(super::api_error("OpenAI Codex", response).await);
        }

        let result = decode_responses_body_with_tools(response).await;
        if let Ok((ref text, ref tool_calls, ref usage)) = result {
            tracing::info!(
                text_len = text.len(),
                tool_calls_count = tool_calls.len(),
                tool_names = %tool_calls.iter().map(|tc| tc.name.as_str()).collect::<Vec<_>>().join(", "),
                input_tokens = usage.as_ref().and_then(|u| u.input_tokens).unwrap_or(0),
                output_tokens = usage.as_ref().and_then(|u| u.output_tokens).unwrap_or(0),
                "Codex Responses API response"
            );
            if text.is_empty() && tool_calls.is_empty() {
                tracing::warn!(
                    "Codex Responses API returned empty text AND no tool calls — model produced no output"
                );
            }
        }
        if let Err(ref e) = result {
            tracing::error!(error = %e, "Codex Responses API decode failed");
        }
        result
    }
}

/// Convert a `ToolSpec` into the Codex Responses API `function` tool format.
fn tool_spec_to_responses_tool(spec: &crate::tools::ToolSpec) -> Option<ResponsesTool> {
    if spec.name.is_empty() {
        tracing::warn!("Skipping tool with empty name");
        return None;
    }
    Some(ResponsesTool {
        kind: "function".to_string(),
        name: spec.name.clone(),
        description: spec.description.clone(),
        parameters: spec.parameters.clone(),
        strict: false,
    })
}

/// Build Responses API input items from conversation history, including tool
/// call results (`role=tool` messages → `function_call_output` top-level items).
fn build_responses_input_with_tools(messages: &[ChatMessage]) -> (String, Vec<serde_json::Value>) {
    let mut system_parts: Vec<&str> = Vec::new();
    let mut input: Vec<serde_json::Value> = Vec::new();
    // Track emitted function_call call_ids so we can validate function_call_outputs.
    // The Responses API rejects any function_call_output whose call_id does not
    // match a preceding function_call — this can happen when context compression,
    // history trimming, or deferred tool activation drops an assistant message
    // that contained the original tool call.
    let mut emitted_call_ids: std::collections::HashSet<String> = std::collections::HashSet::new();

    // Pre-scan tool messages to collect call_ids that have matching outputs. The
    // Responses API also rejects any function_call whose call_id lacks a following
    // function_call_output (error: "No tool output found for function call X"). This
    // happens when tool results are missing from history — e.g. a channel agent
    // that crashed mid-turn, or persistence dropped the tool message. We skip
    // emitting function_call items that have no matching output to keep the
    // payload self-consistent.
    let mut outputs_present: std::collections::HashSet<String> = std::collections::HashSet::new();
    for msg in messages {
        if msg.role.as_str() == "tool" {
            if let Ok(parsed) = serde_json::from_str::<Value>(&msg.content) {
                if let Some(cid) = parsed.get("tool_call_id").and_then(Value::as_str) {
                    if !cid.is_empty() {
                        outputs_present.insert(cid.to_string());
                    }
                }
            }
        }
    }

    for msg in messages {
        match msg.role.as_str() {
            "system" => system_parts.push(&msg.content),
            "user" => {
                let (cleaned_text, image_refs) = multimodal::parse_image_markers(&msg.content);

                let mut content_items: Vec<serde_json::Value> = Vec::new();
                if !cleaned_text.trim().is_empty() {
                    content_items.push(serde_json::json!({
                        "type": "input_text",
                        "text": cleaned_text,
                    }));
                }
                for image_ref in image_refs {
                    content_items.push(serde_json::json!({
                        "type": "input_image",
                        "image_url": image_ref,
                    }));
                }
                if content_items.is_empty() {
                    content_items.push(serde_json::json!({
                        "type": "input_text",
                        "text": "",
                    }));
                }

                input.push(serde_json::json!({
                    "role": "user",
                    "content": content_items,
                }));
            }
            "assistant" => {
                // Check if the assistant message contains native tool calls
                // (stored as JSON with tool_calls array by build_native_assistant_history).
                if let Ok(parsed) = serde_json::from_str::<Value>(&msg.content) {
                    if let Some(tool_calls) = parsed.get("tool_calls").and_then(Value::as_array) {
                        // Emit the text part first (if any)
                        if let Some(text) = parsed.get("content").and_then(Value::as_str) {
                            if !text.is_empty() {
                                input.push(serde_json::json!({
                                    "role": "assistant",
                                    "content": [{
                                        "type": "output_text",
                                        "text": text,
                                    }],
                                }));
                            }
                        }
                        // Emit each tool call as a top-level function_call input item.
                        // Tool calls may be stored in OpenAI format:
                        //   {"id": "...", "function": {"name": "...", "arguments": "..."}}
                        // or flat format:
                        //   {"id": "...", "name": "...", "arguments": "..."}
                        for tc in tool_calls {
                            let call_id = tc
                                .get("id")
                                .or_else(|| tc.get("call_id"))
                                .and_then(Value::as_str)
                                .unwrap_or("");
                            let name = tc
                                .get("function")
                                .and_then(|f| f.get("name"))
                                .and_then(Value::as_str)
                                .or_else(|| tc.get("name").and_then(Value::as_str))
                                .unwrap_or("");
                            let arguments = tc
                                .get("function")
                                .and_then(|f| f.get("arguments"))
                                .and_then(Value::as_str)
                                .or_else(|| tc.get("arguments").and_then(Value::as_str))
                                .unwrap_or("{}");
                            // Skip tool calls with empty names — invalid for the API
                            if name.is_empty() {
                                tracing::warn!(
                                    call_id,
                                    "Skipping tool call with empty name in history"
                                );
                                continue;
                            }
                            // Skip function_calls whose output is missing from history.
                            // Responses API rejects orphan function_calls with
                            // "No tool output found for function call X".
                            if call_id.is_empty() || !outputs_present.contains(call_id) {
                                tracing::debug!(
                                    call_id,
                                    name,
                                    "Dropping orphaned function_call — no matching function_call_output in history"
                                );
                                continue;
                            }
                            emitted_call_ids.insert(call_id.to_string());
                            input.push(serde_json::json!({
                                "type": "function_call",
                                "call_id": call_id,
                                "name": name,
                                "arguments": arguments,
                            }));
                        }
                        continue;
                    }
                }

                input.push(serde_json::json!({
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": msg.content,
                    }],
                }));
            }
            "tool" => {
                // Tool result messages: content is JSON like {"tool_call_id": "...", "content": "..."}
                // Emit as top-level function_call_output items in the Responses API.
                if let Ok(parsed) = serde_json::from_str::<Value>(&msg.content) {
                    let call_id = parsed
                        .get("tool_call_id")
                        .and_then(Value::as_str)
                        .unwrap_or("");
                    // Skip orphaned tool results with no call_id
                    if call_id.is_empty() {
                        continue;
                    }
                    // Skip tool results whose function_call was dropped (by context
                    // compression, history trimming, or deferred-loading activation).
                    if !emitted_call_ids.contains(call_id) {
                        tracing::debug!(
                            call_id,
                            "Dropping orphaned function_call_output — no matching function_call in history"
                        );
                        continue;
                    }
                    let output = parsed
                        .get("content")
                        .and_then(Value::as_str)
                        .unwrap_or(&msg.content);
                    input.push(serde_json::json!({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    }));
                }
            }
            _ => {}
        }
    }

    let instructions = if system_parts.is_empty() {
        DEFAULT_CODEX_INSTRUCTIONS.to_string()
    } else {
        system_parts.join("\n\n")
    };

    (instructions, input)
}

#[async_trait]
impl Provider for OpenAiCodexProvider {
    fn capabilities(&self) -> ProviderCapabilities {
        ProviderCapabilities {
            native_tool_calling: true,
            vision: true,
            prompt_caching: false,
        }
    }

    async fn chat(
        &self,
        request: ChatRequest<'_>,
        model: &str,
        _temperature: f64,
    ) -> anyhow::Result<ChatResponse> {
        let config = crate::config::MultimodalConfig::default();
        let prepared =
            crate::multimodal::prepare_messages_for_provider(request.messages, &config).await?;

        let (instructions, input) = build_responses_input_with_tools(&prepared.messages);

        let tools: Vec<ResponsesTool> = request
            .tools
            .map(|specs| {
                specs
                    .iter()
                    .filter_map(tool_spec_to_responses_tool)
                    .collect()
            })
            .unwrap_or_default();

        let (text, tool_calls, usage) = self
            .send_responses_request_inner(input, instructions, model, tools)
            .await?;

        Ok(ChatResponse {
            text: if text.is_empty() { None } else { Some(text) },
            tool_calls,
            usage,
            reasoning_content: None,
        })
    }

    async fn chat_with_system(
        &self,
        system_prompt: Option<&str>,
        message: &str,
        model: &str,
        _temperature: f64,
    ) -> anyhow::Result<String> {
        let mut messages = Vec::new();
        if let Some(sys) = system_prompt {
            messages.push(ChatMessage::system(sys));
        }
        messages.push(ChatMessage::user(message));

        let config = crate::config::MultimodalConfig::default();
        let prepared = crate::multimodal::prepare_messages_for_provider(&messages, &config).await?;

        let (instructions, input) = build_responses_input(&prepared.messages);
        self.send_responses_request(input, instructions, model)
            .await
    }

    async fn chat_with_history(
        &self,
        messages: &[ChatMessage],
        model: &str,
        _temperature: f64,
    ) -> anyhow::Result<String> {
        let config = crate::config::MultimodalConfig::default();
        let prepared = crate::multimodal::prepare_messages_for_provider(messages, &config).await?;

        let (instructions, input) = build_responses_input(&prepared.messages);
        self.send_responses_request(input, instructions, model)
            .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Mutex, MutexGuard, OnceLock};

    fn env_lock() -> MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
            .lock()
            .expect("env lock poisoned")
    }

    struct EnvGuard {
        key: &'static str,
        original: Option<String>,
    }

    impl EnvGuard {
        fn set(key: &'static str, value: Option<&str>) -> Self {
            let original = std::env::var(key).ok();
            match value {
                // SAFETY: test-only, single-threaded test runner.
                Some(next) => unsafe { std::env::set_var(key, next) },
                // SAFETY: test-only, single-threaded test runner.
                None => unsafe { std::env::remove_var(key) },
            }
            Self { key, original }
        }
    }

    impl Drop for EnvGuard {
        fn drop(&mut self) {
            if let Some(original) = self.original.as_deref() {
                // SAFETY: test-only, single-threaded test runner.
                unsafe { std::env::set_var(self.key, original) };
            } else {
                // SAFETY: test-only, single-threaded test runner.
                unsafe { std::env::remove_var(self.key) };
            }
        }
    }

    #[test]
    fn extracts_output_text_first() {
        let response = ResponsesResponse {
            output: vec![],
            output_text: Some("hello".into()),
            usage: None,
        };
        assert_eq!(extract_responses_text(&response).as_deref(), Some("hello"));
    }

    #[test]
    fn extracts_nested_output_text() {
        let response = ResponsesResponse {
            output: vec![ResponsesOutput {
                kind: None,
                content: vec![ResponsesContent {
                    kind: Some("output_text".into()),
                    text: Some("nested".into()),
                }],
                name: None,
                arguments: None,
                call_id: None,
            }],
            output_text: None,
            usage: None,
        };
        assert_eq!(extract_responses_text(&response).as_deref(), Some("nested"));
    }

    #[test]
    fn default_state_dir_is_non_empty() {
        let path = default_construct_dir();
        assert!(!path.as_os_str().is_empty());
    }

    #[test]
    fn build_responses_url_appends_suffix_for_base_url() {
        assert_eq!(
            build_responses_url("https://api.tonsof.blue/v1").unwrap(),
            "https://api.tonsof.blue/v1/responses"
        );
    }

    #[test]
    fn build_responses_url_keeps_existing_responses_endpoint() {
        assert_eq!(
            build_responses_url("https://api.tonsof.blue/v1/responses").unwrap(),
            "https://api.tonsof.blue/v1/responses"
        );
    }

    #[test]
    fn resolve_responses_url_prefers_explicit_endpoint_env() {
        let _lock = env_lock();
        let _endpoint_guard = EnvGuard::set(
            CODEX_RESPONSES_URL_ENV,
            Some("https://env.example.com/v1/responses"),
        );
        let _base_guard = EnvGuard::set(CODEX_BASE_URL_ENV, Some("https://base.example.com/v1"));

        let options = ProviderRuntimeOptions::default();
        assert_eq!(
            resolve_responses_url(&options).unwrap(),
            "https://env.example.com/v1/responses"
        );
    }

    #[test]
    fn resolve_responses_url_uses_provider_api_url_override() {
        let _lock = env_lock();
        let _endpoint_guard = EnvGuard::set(CODEX_RESPONSES_URL_ENV, None);
        let _base_guard = EnvGuard::set(CODEX_BASE_URL_ENV, None);

        let options = ProviderRuntimeOptions {
            provider_api_url: Some("https://proxy.example.com/v1".to_string()),
            ..ProviderRuntimeOptions::default()
        };

        assert_eq!(
            resolve_responses_url(&options).unwrap(),
            "https://proxy.example.com/v1/responses"
        );
    }

    #[test]
    fn default_responses_url_detector_handles_equivalent_urls() {
        assert!(is_default_responses_url(DEFAULT_CODEX_RESPONSES_URL));
        assert!(is_default_responses_url(
            "https://chatgpt.com/backend-api/codex/responses/"
        ));
        assert!(!is_default_responses_url(
            "https://api.tonsof.blue/v1/responses"
        ));
    }

    #[test]
    fn constructor_enables_custom_endpoint_key_mode() {
        let options = ProviderRuntimeOptions {
            provider_api_url: Some("https://api.tonsof.blue/v1".to_string()),
            ..ProviderRuntimeOptions::default()
        };

        let provider = OpenAiCodexProvider::new(&options, Some("test-key")).unwrap();
        assert!(provider.custom_endpoint);
        assert_eq!(provider.gateway_api_key.as_deref(), Some("test-key"));
    }

    #[test]
    fn resolve_instructions_uses_default_when_missing() {
        assert_eq!(
            resolve_instructions(None),
            DEFAULT_CODEX_INSTRUCTIONS.to_string()
        );
    }

    #[test]
    fn resolve_instructions_uses_default_when_blank() {
        assert_eq!(
            resolve_instructions(Some("   ")),
            DEFAULT_CODEX_INSTRUCTIONS.to_string()
        );
    }

    #[test]
    fn resolve_instructions_uses_system_prompt_when_present() {
        assert_eq!(
            resolve_instructions(Some("Be strict")),
            "Be strict".to_string()
        );
    }

    #[test]
    fn clamp_reasoning_effort_adjusts_known_models() {
        assert_eq!(
            clamp_reasoning_effort("gpt-5-codex", "xhigh"),
            "high".to_string()
        );
        assert_eq!(
            clamp_reasoning_effort("gpt-5-codex", "minimal"),
            "low".to_string()
        );
        assert_eq!(
            clamp_reasoning_effort("gpt-5-codex", "medium"),
            "medium".to_string()
        );
        assert_eq!(
            clamp_reasoning_effort("gpt-5.3-codex", "minimal"),
            "low".to_string()
        );
        assert_eq!(
            clamp_reasoning_effort("gpt-5.1", "xhigh"),
            "high".to_string()
        );
        assert_eq!(
            clamp_reasoning_effort("gpt-5-codex", "xhigh"),
            "high".to_string()
        );
        assert_eq!(
            clamp_reasoning_effort("gpt-5.1-codex-mini", "low"),
            "medium".to_string()
        );
        assert_eq!(
            clamp_reasoning_effort("gpt-5.1-codex-mini", "xhigh"),
            "high".to_string()
        );
        assert_eq!(
            clamp_reasoning_effort("gpt-5.3-codex", "xhigh"),
            "xhigh".to_string()
        );
    }

    #[test]
    fn resolve_reasoning_effort_prefers_configured_override() {
        let _lock = env_lock();
        let _guard = EnvGuard::set("CONSTRUCT_CODEX_REASONING_EFFORT", Some("low"));
        assert_eq!(
            resolve_reasoning_effort("gpt-5-codex", Some("high")),
            "high".to_string()
        );
    }

    #[test]
    fn resolve_reasoning_effort_uses_legacy_env_when_unconfigured() {
        let _lock = env_lock();
        let _guard = EnvGuard::set("CONSTRUCT_CODEX_REASONING_EFFORT", Some("minimal"));
        assert_eq!(
            resolve_reasoning_effort("gpt-5-codex", None),
            "low".to_string()
        );
    }

    #[test]
    fn parse_sse_text_reads_output_text_delta() {
        let payload = r#"data: {"type":"response.created","response":{"id":"resp_123"}}

data: {"type":"response.output_text.delta","delta":"Hello"}
data: {"type":"response.output_text.delta","delta":" world"}
data: {"type":"response.completed","response":{"output_text":"Hello world"}}
data: [DONE]
"#;

        assert_eq!(
            parse_sse_text(payload).unwrap().as_deref(),
            Some("Hello world")
        );
    }

    #[test]
    fn parse_sse_text_falls_back_to_completed_response() {
        let payload = r#"data: {"type":"response.completed","response":{"output_text":"Done"}}
data: [DONE]
"#;

        assert_eq!(parse_sse_text(payload).unwrap().as_deref(), Some("Done"));
    }

    #[test]
    fn parse_responses_body_with_tools_rejects_empty_sse_completion() {
        let payload = r#"data: {"type":"response.created","response":{"id":"resp_empty"}}

data: {"type":"response.completed","response":{"output":[]}}
data: [DONE]
"#;

        let err = parse_responses_body_with_tools(payload).unwrap_err();
        assert!(
            err.to_string().contains("empty text and no tool calls"),
            "unexpected error: {err:#}"
        );
    }

    #[test]
    fn parse_responses_body_with_tools_accepts_text_sse_completion() {
        let payload = r#"data: {"type":"response.output_text.delta","delta":"Hello"}
data: {"type":"response.completed","response":{"output_text":"Hello","usage":{"input_tokens":10,"output_tokens":2}}}
data: [DONE]
"#;

        let (text, tool_calls, usage) = parse_responses_body_with_tools(payload).unwrap();
        assert_eq!(text, "Hello");
        assert!(tool_calls.is_empty());
        assert_eq!(usage.and_then(|u| u.output_tokens), Some(2));
    }

    #[test]
    fn parse_responses_body_with_tools_accepts_tool_call_without_text() {
        let payload = r#"data: {"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"call_1","name":"file_read"}}
data: {"type":"response.function_call_arguments.done","output_index":0,"arguments":"{\"path\":\"README.md\"}"}
data: {"type":"response.completed","response":{"output":[],"usage":{"input_tokens":10,"output_tokens":2}}}
data: [DONE]
"#;

        let (text, tool_calls, usage) = parse_responses_body_with_tools(payload).unwrap();
        assert!(text.is_empty());
        assert_eq!(tool_calls.len(), 1);
        assert_eq!(tool_calls[0].id, "call_1");
        assert_eq!(tool_calls[0].name, "file_read");
        assert_eq!(usage.and_then(|u| u.input_tokens), Some(10));
    }

    #[test]
    fn decode_utf8_stream_chunks_handles_multibyte_split_across_chunks() {
        let payload = "data: {\"type\":\"response.output_text.delta\",\"delta\":\"Hello 世\"}\n\ndata: [DONE]\n";
        let bytes = payload.as_bytes();
        let split_at = payload.find('世').unwrap() + 1;

        let decoded = decode_utf8_stream_chunks([&bytes[..split_at], &bytes[split_at..]]).unwrap();
        assert_eq!(decoded, payload);
        assert_eq!(
            parse_sse_text(&decoded).unwrap().as_deref(),
            Some("Hello 世")
        );
    }

    #[test]
    fn build_responses_input_maps_content_types_by_role() {
        let messages = vec![
            ChatMessage {
                role: "system".into(),
                content: "You are helpful.".into(),
            },
            ChatMessage {
                role: "user".into(),
                content: "Hi".into(),
            },
            ChatMessage {
                role: "assistant".into(),
                content: "Hello!".into(),
            },
            ChatMessage {
                role: "user".into(),
                content: "Thanks".into(),
            },
        ];
        let (instructions, input) = build_responses_input(&messages);
        assert_eq!(instructions, "You are helpful.");
        assert_eq!(input.len(), 3);

        assert_eq!(input[0]["role"], "user");
        assert_eq!(input[0]["content"][0]["type"], "input_text");
        assert_eq!(input[1]["role"], "assistant");
        assert_eq!(input[1]["content"][0]["type"], "output_text");
        assert_eq!(input[2]["role"], "user");
        assert_eq!(input[2]["content"][0]["type"], "input_text");
    }

    #[test]
    fn build_responses_input_uses_default_instructions_without_system() {
        let messages = vec![ChatMessage {
            role: "user".into(),
            content: "Hello".into(),
        }];
        let (instructions, input) = build_responses_input(&messages);
        assert_eq!(instructions, DEFAULT_CODEX_INSTRUCTIONS);
        assert_eq!(input.len(), 1);
    }

    #[test]
    fn build_responses_input_ignores_unknown_roles() {
        let messages = vec![
            ChatMessage {
                role: "tool".into(),
                content: "result".into(),
            },
            ChatMessage {
                role: "user".into(),
                content: "Go".into(),
            },
        ];
        let (instructions, input) = build_responses_input(&messages);
        assert_eq!(instructions, DEFAULT_CODEX_INSTRUCTIONS);
        assert_eq!(input.len(), 1);
        assert_eq!(input[0]["role"], "user");
    }

    #[test]
    fn build_responses_input_handles_image_markers() {
        let messages = vec![ChatMessage::user(
            "Describe this\n\n[IMAGE:data:image/png;base64,abc]",
        )];
        let (_, input) = build_responses_input(&messages);

        assert_eq!(input.len(), 1);
        assert_eq!(input[0]["role"], "user");
        let content = input[0]["content"].as_array().unwrap();
        assert_eq!(content.len(), 2);

        // First content = text
        assert_eq!(content[0]["type"], "input_text");
        assert!(
            content[0]["text"]
                .as_str()
                .unwrap()
                .contains("Describe this")
        );

        // Second content = image
        assert_eq!(content[1]["type"], "input_image");
        assert_eq!(content[1]["image_url"], "data:image/png;base64,abc");
    }

    #[test]
    fn build_responses_input_preserves_text_only_messages() {
        let messages = vec![ChatMessage::user("Hello without images")];
        let (_, input) = build_responses_input(&messages);

        assert_eq!(input.len(), 1);
        let content = input[0]["content"].as_array().unwrap();
        assert_eq!(content.len(), 1);

        assert_eq!(content[0]["type"], "input_text");
        assert_eq!(content[0]["text"], "Hello without images");
    }

    #[test]
    fn build_responses_input_handles_multiple_images() {
        let messages = vec![ChatMessage::user(
            "Compare these: [IMAGE:data:image/png;base64,img1] and [IMAGE:data:image/jpeg;base64,img2]",
        )];
        let (_, input) = build_responses_input(&messages);

        assert_eq!(input.len(), 1);
        let content = input[0]["content"].as_array().unwrap();
        assert_eq!(content.len(), 3); // text + 2 images

        assert_eq!(content[0]["type"], "input_text");
        assert_eq!(content[1]["type"], "input_image");
        assert_eq!(content[2]["type"], "input_image");
    }

    #[test]
    fn capabilities_includes_vision() {
        let options = ProviderRuntimeOptions {
            provider_api_url: None,
            construct_dir: None,
            secrets_encrypt: false,
            auth_profile_override: None,
            reasoning_enabled: None,
            reasoning_effort: None,
            provider_timeout_secs: None,
            extra_headers: std::collections::HashMap::new(),
            api_path: None,
            provider_max_tokens: None,
        };
        let provider =
            OpenAiCodexProvider::new(&options, None).expect("provider should initialize");
        let caps = provider.capabilities();

        assert!(caps.native_tool_calling);
        assert!(caps.vision);
    }

    #[test]
    fn build_responses_input_drops_orphaned_function_call_output() {
        // Simulate history where context compression dropped the assistant message
        // containing the function_call, but kept the tool result.
        let messages = vec![
            ChatMessage {
                role: "system".into(),
                content: "You are helpful.".into(),
            },
            ChatMessage {
                role: "user".into(),
                content: "Do something.".into(),
            },
            // No assistant message with tool_calls — it was compressed away.
            ChatMessage {
                role: "tool".into(),
                content: r#"{"tool_call_id": "call_orphaned", "content": "some result"}"#.into(),
            },
        ];
        let (_, input) = build_responses_input_with_tools(&messages);
        // The orphaned function_call_output should be dropped.
        assert!(
            !input.iter().any(
                |item| item.get("type").and_then(Value::as_str) == Some("function_call_output")
            ),
            "orphaned function_call_output should be filtered out"
        );
    }

    #[test]
    fn build_responses_input_keeps_matched_function_call_output() {
        let messages = vec![
            ChatMessage { role: "system".into(), content: "You are helpful.".into() },
            ChatMessage { role: "user".into(), content: "Do something.".into() },
            ChatMessage {
                role: "assistant".into(),
                content: r#"{"content": null, "tool_calls": [{"id": "call_good", "name": "tool_search", "arguments": "{}"}]}"#.into(),
            },
            ChatMessage {
                role: "tool".into(),
                content: r#"{"tool_call_id": "call_good", "content": "search results"}"#.into(),
            },
        ];
        let (_, input) = build_responses_input_with_tools(&messages);
        let has_call = input.iter().any(|item| {
            item.get("type").and_then(Value::as_str) == Some("function_call")
                && item.get("call_id").and_then(Value::as_str) == Some("call_good")
        });
        let has_output = input.iter().any(|item| {
            item.get("type").and_then(Value::as_str) == Some("function_call_output")
                && item.get("call_id").and_then(Value::as_str) == Some("call_good")
        });
        assert!(has_call, "function_call should be present");
        assert!(has_output, "matched function_call_output should be kept");
    }
}
