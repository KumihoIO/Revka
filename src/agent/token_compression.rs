use std::collections::{BTreeMap, HashMap};
use std::fmt::Write;

use serde_json::Value;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompressionStats {
    pub axis: &'static str,
    pub content_type: &'static str,
    pub original_chars: usize,
    pub compressed_chars: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompressedText {
    pub text: String,
    pub stats: Option<CompressionStats>,
}

impl CompressedText {
    fn unchanged(input: &str) -> Self {
        Self {
            text: input.to_string(),
            stats: None,
        }
    }
}

const ERROR_KEYWORDS: &[&str] = &[
    "error",
    "failed",
    "failure",
    "panic",
    "exception",
    "traceback",
    "assertion",
    "denied",
    "timeout",
    "warning",
    "warn",
    "fatal",
    "not found",
    "cannot",
    "could not",
];

const OPERATOR_JSON_TOOLS: &[&str] = &[
    "create_agent",
    "wait_for_agent",
    "get_agent_activity",
    "spawn_team",
    "get_team",
    "list_agents",
    "list_teams",
    "record_agent_outcome",
    "resolve_agent_outcome",
    "resolve_outcome",
    "get_outcome_lineage",
];

pub fn should_preserve_json_tool_output(tool_name: &str) -> bool {
    let bare_tool_name = tool_name.rsplit("__").next().unwrap_or(tool_name);
    OPERATOR_JSON_TOOLS
        .iter()
        .any(|name| bare_tool_name.eq_ignore_ascii_case(name))
}

pub fn compress_input(input: &str, max_chars: usize) -> CompressedText {
    compress_by_content("input", None, input, max_chars)
}

pub fn compress_transcript_message(role: &str, content: &str, max_chars: usize) -> String {
    if max_chars == 0 || content.len() <= max_chars {
        return content.to_string();
    }
    let compressed = compress_by_content("input", Some(role), content, max_chars);
    compressed.text
}

pub fn compress_tool_output(
    tool_name: &str,
    output: &str,
    max_chars: usize,
    command: Option<&str>,
) -> CompressedText {
    if should_preserve_json_tool_output(tool_name) {
        return CompressedText::unchanged(output);
    }
    let axis = if tool_name.eq_ignore_ascii_case("shell") {
        "cli_output"
    } else if tool_name.eq_ignore_ascii_case("semantic_code_search")
        || tool_name.eq_ignore_ascii_case("content_search")
        || tool_name.eq_ignore_ascii_case("glob_search")
    {
        "code_search"
    } else {
        "tool_output"
    };
    compress_by_content(axis, command, output, max_chars)
}

pub fn compact_tool_specs(
    specs: &mut [crate::tools::ToolSpec],
    config: &crate::agent::context_compressor::ContextCompressionConfig,
) -> Option<CompressionStats> {
    if !config.compact_tool_schemas {
        return None;
    }

    let before = estimate_tool_spec_chars(specs);
    for spec in specs.iter_mut() {
        compact_tool_spec(
            spec,
            config.tool_description_max_chars,
            config.schema_description_max_chars,
        );
    }
    let after = estimate_tool_spec_chars(specs);

    (after < before).then_some(CompressionStats {
        axis: "base_context",
        content_type: "tool_schema",
        original_chars: before,
        compressed_chars: after,
    })
}

pub fn compact_tool_spec(
    spec: &mut crate::tools::ToolSpec,
    tool_description_max_chars: usize,
    schema_description_max_chars: usize,
) {
    spec.description = compact_inline(&spec.description, tool_description_max_chars);
    compact_schema_value(&mut spec.parameters, schema_description_max_chars);
}

pub fn compact_inline(input: &str, max_chars: usize) -> String {
    let mut out = String::with_capacity(input.len().min(max_chars.max(1)));
    let mut last_was_space = false;
    for ch in input.trim().chars() {
        if ch.is_whitespace() {
            if !last_was_space {
                out.push(' ');
                last_was_space = true;
            }
        } else {
            out.push(ch);
            last_was_space = false;
        }
    }

    match max_chars {
        0 => String::new(),
        max if out.len() <= max => out,
        max if max <= 3 => out[..floor_char_boundary(&out, max)].to_string(),
        max => {
            let end = floor_char_boundary(&out, max - 3);
            format!("{}...", &out[..end])
        }
    }
}

fn estimate_tool_spec_chars(specs: &[crate::tools::ToolSpec]) -> usize {
    specs
        .iter()
        .map(|spec| serde_json::to_string(spec).map_or(0, |s| s.len()))
        .sum()
}

fn compact_schema_value(value: &mut Value, description_max_chars: usize) {
    match value {
        Value::Object(map) => {
            for key in [
                "$schema",
                "$id",
                "$defs",
                "definitions",
                "$comment",
                "examples",
                "example",
                "markdownDescription",
            ] {
                map.remove(key);
            }

            let remove_description =
                if let Some(Value::String(description)) = map.get_mut("description") {
                    *description = compact_inline(description, description_max_chars);
                    description.is_empty()
                } else {
                    false
                };
            if remove_description {
                map.remove("description");
            }

            for child in map.values_mut() {
                compact_schema_value(child, description_max_chars);
            }
        }
        Value::Array(items) => {
            for item in items {
                compact_schema_value(item, description_max_chars);
            }
        }
        _ => {}
    }
}

fn compress_by_content(
    axis: &'static str,
    hint: Option<&str>,
    input: &str,
    max_chars: usize,
) -> CompressedText {
    if max_chars == 0 || input.len() <= max_chars {
        return CompressedText::unchanged(input);
    }

    let trimmed = input.trim();
    if trimmed.is_empty() {
        return CompressedText::unchanged(input);
    }

    let (content_type, mut compressed) =
        if let Ok(json) = serde_json::from_str::<serde_json::Value>(trimmed) {
            ("json", compress_json(&json, max_chars))
        } else if looks_like_diff(trimmed) {
            ("diff", compress_diff(trimmed, max_chars))
        } else if looks_like_search_output(hint, trimmed) {
            ("search", compress_search_output(trimmed, max_chars))
        } else if looks_like_code(hint, trimmed) {
            ("code", compress_code(trimmed, max_chars))
        } else if looks_like_log(hint, trimmed) {
            ("log", compress_log(trimmed, max_chars))
        } else {
            ("text", compress_plain_text(trimmed, max_chars))
        };

    if compressed.len() > max_chars {
        compressed = truncate_with_marker(&compressed, max_chars);
    }

    if compressed.len() >= input.len() {
        return CompressedText::unchanged(input);
    }

    let mut out = String::new();
    let original_tokens = estimate_tokens(input);
    let compressed_tokens = estimate_tokens(&compressed);
    let saved = original_tokens.saturating_sub(compressed_tokens);
    let _ = writeln!(
        out,
        "[Revka token compression: axis={axis}, type={content_type}, chars {}->{}, est_tokens {}->{}, saved~{}]",
        input.len(),
        compressed.len(),
        original_tokens,
        compressed_tokens,
        saved
    );
    out.push_str(&compressed);

    if out.len() > max_chars {
        out = truncate_with_marker(&out, max_chars);
    }

    CompressedText {
        stats: Some(CompressionStats {
            axis,
            content_type,
            original_chars: input.len(),
            compressed_chars: out.len(),
        }),
        text: out,
    }
}

fn estimate_tokens(text: &str) -> usize {
    text.len().div_ceil(4)
}

fn looks_like_diff(text: &str) -> bool {
    text.starts_with("diff --git")
        || text.lines().take(20).any(|line| {
            line.starts_with("@@ ")
                || line.starts_with("+++ ")
                || line.starts_with("--- ")
                || line.starts_with("Index: ")
        })
}

fn looks_like_search_output(hint: Option<&str>, text: &str) -> bool {
    hint.is_some_and(|h| {
        let h = h.to_ascii_lowercase();
        h.contains("search") || h.contains("grep") || h.contains("rg ")
    }) || text
        .lines()
        .take(40)
        .filter(|line| parse_search_line(line).is_some())
        .count()
        >= 3
}

fn looks_like_code(hint: Option<&str>, text: &str) -> bool {
    if hint.is_some_and(|h| h.eq_ignore_ascii_case("user")) {
        let code_fence_count = text.matches("```").count();
        if code_fence_count >= 2 {
            return true;
        }
    }

    let mut code_lines = 0usize;
    let mut total = 0usize;
    for line in text.lines().take(80) {
        let trimmed = line.trim_start();
        if trimmed.is_empty() {
            continue;
        }
        total += 1;
        if trimmed.starts_with("use ")
            || trimmed.starts_with("mod ")
            || trimmed.starts_with("pub ")
            || trimmed.starts_with("fn ")
            || trimmed.starts_with("impl ")
            || trimmed.starts_with("struct ")
            || trimmed.starts_with("enum ")
            || trimmed.starts_with("trait ")
            || trimmed.starts_with("class ")
            || trimmed.starts_with("def ")
            || trimmed.starts_with("import ")
            || trimmed.starts_with("from ")
            || trimmed.starts_with("const ")
            || trimmed.starts_with("let ")
        {
            code_lines += 1;
        }
    }
    total >= 8 && code_lines >= 3
}

fn looks_like_log(hint: Option<&str>, text: &str) -> bool {
    if hint.is_some_and(|h| {
        let h = h.to_ascii_lowercase();
        h.contains("test") || h.contains("build") || h.contains("cargo") || h.contains("npm")
    }) {
        return true;
    }

    let lines: Vec<&str> = text.lines().take(120).collect();
    if lines.len() < 12 {
        return false;
    }
    let noisy = lines
        .iter()
        .filter(|line| {
            let lower = line.to_ascii_lowercase();
            lower.contains("compil")
                || lower.contains("running ")
                || lower.contains("finished")
                || lower.contains("test ")
                || lower.contains("warning")
                || lower.contains("error")
        })
        .count();
    noisy >= 4
}

fn compress_json(value: &serde_json::Value, max_chars: usize) -> String {
    let mut out = String::new();
    let _ = writeln!(out, "JSON summary:");
    describe_json(value, "$", 0, &mut out);
    append_json_samples(value, &mut out);
    truncate_with_marker(&out, max_chars)
}

fn describe_json(value: &serde_json::Value, path: &str, depth: usize, out: &mut String) {
    if depth > 3 || out.len() > 12_000 {
        return;
    }
    match value {
        serde_json::Value::Object(map) => {
            let keys: Vec<&str> = map.keys().take(40).map(String::as_str).collect();
            let _ = writeln!(
                out,
                "- {path}: object keys={} [{}]",
                map.len(),
                keys.join(", ")
            );
            for (key, child) in map.iter().take(12) {
                let child_path = format!("{path}.{key}");
                match child {
                    serde_json::Value::Array(items) => {
                        let _ = writeln!(out, "  - {child_path}: array len={}", items.len());
                    }
                    serde_json::Value::Object(_) => {
                        describe_json(child, &child_path, depth + 1, out);
                    }
                    _ => {
                        let scalar = json_scalar_preview(child);
                        let _ = writeln!(out, "  - {child_path}: {scalar}");
                    }
                }
            }
        }
        serde_json::Value::Array(items) => {
            let _ = writeln!(out, "- {path}: array len={}", items.len());
            if let Some(first) = items.first() {
                describe_json(first, &format!("{path}[0]"), depth + 1, out);
            }
        }
        _ => {
            let _ = writeln!(out, "- {path}: {}", json_scalar_preview(value));
        }
    }
}

fn json_scalar_preview(value: &serde_json::Value) -> String {
    match value {
        serde_json::Value::String(s) => format!("string({}) {:?}", s.len(), short(s, 120)),
        serde_json::Value::Number(n) => format!("number({n})"),
        serde_json::Value::Bool(b) => format!("bool({b})"),
        serde_json::Value::Null => "null".to_string(),
        serde_json::Value::Array(items) => format!("array len={}", items.len()),
        serde_json::Value::Object(map) => format!("object keys={}", map.len()),
    }
}

fn append_json_samples(value: &serde_json::Value, out: &mut String) {
    match value {
        serde_json::Value::Array(items) => {
            let _ = writeln!(out, "\nSamples:");
            for (idx, item) in items.iter().take(2).enumerate() {
                let _ = writeln!(out, "- first[{idx}]: {}", short(&item.to_string(), 500));
            }
            if items.len() > 2
                && let Some(last) = items.last()
            {
                let _ = writeln!(out, "- last: {}", short(&last.to_string(), 500));
            }
        }
        serde_json::Value::Object(map) => {
            let _ = writeln!(out, "\nTop-level preview:");
            for (key, item) in map.iter().take(8) {
                let _ = writeln!(out, "- {key}: {}", short(&item.to_string(), 400));
            }
        }
        _ => {}
    }
}

fn compress_diff(text: &str, max_chars: usize) -> String {
    let mut out = String::new();
    let mut files = Vec::new();
    let mut additions = 0usize;
    let mut deletions = 0usize;
    let mut hunks = 0usize;
    let mut important = Vec::new();

    for line in text.lines() {
        if let Some(path) = line.strip_prefix("diff --git ") {
            files.push(path.to_string());
            important.push(line.to_string());
        } else if line.starts_with("@@ ") {
            hunks += 1;
            important.push(line.to_string());
        } else if line.starts_with('+') && !line.starts_with("+++") {
            additions += 1;
            if is_code_signal_line(line) {
                important.push(line.to_string());
            }
        } else if line.starts_with('-') && !line.starts_with("---") {
            deletions += 1;
            if is_code_signal_line(line) {
                important.push(line.to_string());
            }
        } else if line.starts_with("+++ ") || line.starts_with("--- ") {
            important.push(line.to_string());
        }
        if important.len() >= 120 {
            break;
        }
    }

    let _ = writeln!(
        out,
        "Diff summary: files={}, hunks={}, +{}, -{}",
        files.len(),
        hunks,
        additions,
        deletions
    );
    for path in files.iter().take(20) {
        let _ = writeln!(out, "- {path}");
    }
    out.push_str("\nSignal lines:\n");
    for line in important.iter().take(120) {
        let _ = writeln!(out, "{line}");
    }
    truncate_with_marker(&out, max_chars)
}

fn compress_search_output(text: &str, max_chars: usize) -> String {
    let mut by_file: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut unmatched = Vec::new();
    for line in text.lines() {
        if let Some((path, _line_no)) = parse_search_line(line) {
            by_file
                .entry(path.to_string())
                .or_default()
                .push(line.to_string());
        } else if !line.trim().is_empty() && unmatched.len() < 20 {
            unmatched.push(line.to_string());
        }
    }

    let total_matches: usize = by_file.values().map(Vec::len).sum();
    let mut out = String::new();
    let _ = writeln!(
        out,
        "Search summary: {} matches across {} files",
        total_matches,
        by_file.len()
    );
    for (path, lines) in by_file.iter().take(40) {
        let _ = writeln!(out, "\n## {path} ({} hits)", lines.len());
        for line in lines.iter().take(3) {
            let _ = writeln!(out, "{}", short(line, 300));
        }
        if lines.len() > 3 {
            let _ = writeln!(out, "... {} more hits in {path}", lines.len() - 3);
        }
    }
    if !unmatched.is_empty() {
        out.push_str("\nOther lines:\n");
        for line in unmatched {
            let _ = writeln!(out, "{}", short(&line, 240));
        }
    }
    truncate_with_marker(&out, max_chars)
}

fn parse_search_line(line: &str) -> Option<(&str, usize)> {
    let colon_1 = line.find(':')?;
    let after_path = &line[colon_1 + 1..];
    let colon_2 = after_path.find(':')?;
    let line_no = after_path[..colon_2].parse::<usize>().ok()?;
    Some((&line[..colon_1], line_no))
}

fn compress_code(text: &str, max_chars: usize) -> String {
    let mut out = String::new();
    let mut kept = 0usize;
    let mut total = 0usize;
    for (idx, line) in text.lines().enumerate() {
        total += 1;
        let trimmed = line.trim_start();
        if is_code_signal_line(trimmed)
            || trimmed.starts_with("use ")
            || trimmed.starts_with("import ")
            || trimmed.starts_with("from ")
            || trimmed.starts_with("#[")
            || trimmed.starts_with("//!")
            || trimmed.starts_with("///")
        {
            let _ = writeln!(out, "{}: {}", idx + 1, short(line, 400));
            kept += 1;
        }
        if kept >= 180 || out.len() > max_chars {
            break;
        }
    }

    if kept == 0 {
        return compress_plain_text(text, max_chars);
    }

    let mut prefixed = format!("Code skeleton: kept {kept} signal lines from {total} lines\n");
    prefixed.push_str(&out);
    truncate_with_marker(&prefixed, max_chars)
}

fn is_code_signal_line(line: &str) -> bool {
    let trimmed = line.trim_start_matches(['+', '-']).trim_start();
    trimmed.starts_with("pub ")
        || trimmed.starts_with("fn ")
        || trimmed.starts_with("async fn ")
        || trimmed.starts_with("impl ")
        || trimmed.starts_with("struct ")
        || trimmed.starts_with("enum ")
        || trimmed.starts_with("trait ")
        || trimmed.starts_with("class ")
        || trimmed.starts_with("def ")
        || trimmed.starts_with("function ")
        || trimmed.starts_with("const ")
        || trimmed.starts_with("export ")
        || trimmed.starts_with("interface ")
        || trimmed.starts_with("type ")
        || trimmed.contains(" test")
        || trimmed.contains("assert")
}

fn compress_log(text: &str, max_chars: usize) -> String {
    let mut counts: HashMap<&str, usize> = HashMap::new();
    let mut order = Vec::new();
    let mut signal = Vec::new();

    for line in text.lines() {
        let normalized = normalize_log_line(line);
        if !counts.contains_key(normalized) {
            order.push(normalized);
        }
        *counts.entry(normalized).or_insert(0) += 1;

        if is_error_signal(line) && signal.len() < 120 {
            signal.push(line.to_string());
        }
    }

    let mut out = String::new();
    let _ = writeln!(out, "Log summary: {} lines", text.lines().count());
    if !signal.is_empty() {
        out.push_str("\nErrors/warnings/failures:\n");
        for line in &signal {
            let _ = writeln!(out, "{}", short(line, 500));
        }
    }

    out.push_str("\nRepeated/noisy lines:\n");
    let mut repeated: Vec<_> = order
        .into_iter()
        .filter_map(|line| counts.get(line).map(|count| (*count, line)))
        .collect();
    repeated.sort_by_key(|entry| std::cmp::Reverse(entry.0));
    for (count, line) in repeated.into_iter().take(30) {
        if count > 1 {
            let _ = writeln!(out, "- x{count}: {}", short(line, 220));
        }
    }

    out.push_str("\nTail:\n");
    for line in tail_lines(text, 40) {
        let _ = writeln!(out, "{}", short(line, 400));
    }
    truncate_with_marker(&out, max_chars)
}

fn normalize_log_line(line: &str) -> &str {
    line.trim()
}

fn is_error_signal(line: &str) -> bool {
    let lower = line.to_ascii_lowercase();
    ERROR_KEYWORDS.iter().any(|keyword| lower.contains(keyword))
}

fn compress_plain_text(text: &str, max_chars: usize) -> String {
    let mut out = String::new();
    out.push_str("Text summary by extraction:\n");

    let mut seen = HashMap::<&str, usize>::new();
    let mut signal_count = 0usize;
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        *seen.entry(trimmed).or_insert(0) += 1;
        if (is_error_signal(trimmed) || trimmed.len() > 80) && signal_count < 80 {
            let _ = writeln!(out, "- {}", short(trimmed, 500));
            signal_count += 1;
        }
        if out.len() > max_chars / 2 {
            break;
        }
    }

    if signal_count == 0 {
        return truncate_with_marker(text, max_chars);
    }

    out.push_str("\nTail:\n");
    for line in tail_lines(text, 30) {
        let _ = writeln!(out, "{}", short(line, 400));
    }
    truncate_with_marker(&out, max_chars)
}

fn tail_lines(text: &str, n: usize) -> Vec<&str> {
    let lines: Vec<&str> = text.lines().collect();
    let start = lines.len().saturating_sub(n);
    lines[start..].to_vec()
}

fn short(s: &str, max: usize) -> String {
    if s.len() <= max {
        return s.to_string();
    }
    let end = floor_char_boundary(s, max);
    format!("{}...", &s[..end])
}

fn truncate_with_marker(input: &str, max_chars: usize) -> String {
    if max_chars == 0 || input.len() <= max_chars {
        return input.to_string();
    }
    if max_chars <= 32 {
        return input[..floor_char_boundary(input, max_chars)].to_string();
    }

    let marker = "\n\n[... compressed output truncated ...]\n\n";
    let available = max_chars.saturating_sub(marker.len());
    let head_len = available * 2 / 3;
    let tail_len = available.saturating_sub(head_len);
    let head_end = floor_char_boundary(input, head_len);
    let tail_start_raw = input.len().saturating_sub(tail_len);
    let tail_start = ceil_char_boundary(input, tail_start_raw);

    if head_end >= tail_start {
        return input[..floor_char_boundary(input, max_chars)].to_string();
    }

    format!("{}{}{}", &input[..head_end], marker, &input[tail_start..])
}

fn floor_char_boundary(s: &str, i: usize) -> usize {
    if i >= s.len() {
        return s.len();
    }
    let mut pos = i;
    while pos > 0 && !s.is_char_boundary(pos) {
        pos -= 1;
    }
    pos
}

fn ceil_char_boundary(s: &str, i: usize) -> usize {
    if i >= s.len() {
        return s.len();
    }
    let mut pos = i;
    while pos < s.len() && !s.is_char_boundary(pos) {
        pos += 1;
    }
    pos
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn json_is_summarized_under_budget() {
        let input = serde_json::json!({
            "items": (0..200).map(|i| serde_json::json!({"id": i, "value": "x".repeat(100)})).collect::<Vec<_>>(),
            "status": "ok"
        })
        .to_string();

        let out = compress_input(&input, 1200);
        assert!(out.stats.is_some());
        assert!(out.text.len() <= 1200);
        assert!(out.text.contains("JSON summary"));
        assert!(out.text.contains("items"));
    }

    #[test]
    fn logs_keep_error_signal() {
        let input = format!(
            "{}\nerror: failed to compile src/main.rs\n{}",
            "Compiling crate".repeat(500),
            "Finished".repeat(500)
        );
        let out = compress_tool_output("shell", &input, 900, Some("cargo build"));
        assert!(out.stats.is_some());
        assert!(out.text.contains("failed to compile"));
        assert!(out.text.len() <= 900);
    }

    #[test]
    fn search_output_groups_by_file() {
        let mut input = String::new();
        for i in 1..200 {
            let _ = writeln!(input, "src/main.rs:{i}:match line {i}");
        }
        let out = compress_tool_output("content_search", &input, 1000, Some("rg match"));
        assert!(out.stats.is_some());
        assert!(out.text.contains("Search summary"));
        assert!(out.text.contains("src/main.rs"));
        assert!(out.text.len() <= 1000);
    }

    #[test]
    fn operator_json_tools_are_preserved() {
        let input =
            serde_json::json!({"agent_id":"a1","last_message":"x".repeat(20_000)}).to_string();
        let out = compress_tool_output("wait_for_agent", &input, 1000, None);
        assert!(out.stats.is_none());
        assert_eq!(out.text, input);
    }

    #[test]
    fn utf8_truncation_is_safe() {
        let input = "한글".repeat(10_000);
        let out = compress_input(&input, 777);
        assert!(out.text.len() <= 777);
        assert!(std::str::from_utf8(out.text.as_bytes()).is_ok());
    }

    #[test]
    fn tool_specs_are_compacted_without_losing_contract() {
        let mut specs = vec![crate::tools::ToolSpec {
            name: "very_long_tool".into(),
            description: "Use this tool when ".repeat(80),
            parameters: serde_json::json!({
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "A very detailed explanation. ".repeat(80),
                        "examples": ["one", "two"]
                    }
                },
                "required": ["query"]
            }),
        }];

        let before = serde_json::to_string(&specs).unwrap().len();
        let stats = compact_tool_specs(
            &mut specs,
            &crate::agent::context_compressor::ContextCompressionConfig::default(),
        );
        let after = serde_json::to_string(&specs).unwrap().len();

        assert!(stats.is_some());
        assert!(after < before);
        assert_eq!(specs[0].parameters["type"], "object");
        assert_eq!(
            specs[0].parameters["required"],
            serde_json::json!(["query"])
        );
        assert!(specs[0].parameters.get("$schema").is_none());
        assert!(
            specs[0].parameters["properties"]["query"]["description"]
                .as_str()
                .unwrap()
                .len()
                <= 120
        );
    }
}
