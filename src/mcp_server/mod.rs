//! Revka MCP server — exposes Revka's built-in tools over the MCP
//! Streamable HTTP transport (JSON-RPC 2.0 + SSE) so external CLIs
//! (Claude Code, Codex, OpenCode, Gemini CLI) can share one local backend.
//!
//! The MCP server runs as an **in-process tokio task inside the main revka
//! daemon** (see `src/gateway/mod.rs::run_gateway`). External clients discover
//! it through `~/.revka/mcp.json`, written by the gateway as soon as the
//! MCP task finishes binding to its ephemeral port.
//!
//! This module contains the router, session store, tool registry, and
//! `ProgressSink` that publishes `notifications/progress` events onto the
//! originating SSE stream.
//!
//! Design notes:
//! - One daemon, many sessions; sessions are in-memory only.
//! - Auth is a `session_id` + `bearer token` pair; no persistence.
//! - MCP keeps its own ephemeral port and its own auth pair — it does NOT
//!   share the gateway's listener or pairing model.
//! - Tools are enumerated once at startup via [`registry::build_tools_with_runtime`]
//!   (config-aware + wired to live runtime handles) so every tool the gateway
//!   can run is also exposed to MCP clients. [`registry::build_default_tools`]
//!   and [`registry::build_tools_with_config`] are retained as the degraded
//!   fallback and the test entry point.

pub mod progress;
pub mod progress_wrap;
pub mod registry;
pub mod runtime;
pub mod server;
pub mod session;
pub mod skills_tools;

pub use runtime::RuntimeHandles;
pub use server::{McpServerHandle, run_daemon, serve_on};
pub use session::ProgressEvent;
