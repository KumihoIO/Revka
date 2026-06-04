# Revka Commands Reference

This reference is derived from the current CLI surface (`revka --help`).

Last verified: **May 8, 2026**.

The `revka` binary also embeds the React/TypeScript web dashboard served by
`revka gateway` / `revka daemon` at `http://127.0.0.1:42617`. Most
operational surface is available in both CLI and dashboard; this document
covers the CLI surface only.

## Top-Level Commands

| Command | Purpose |
|---|---|
| `onboard` | Initialize workspace/config quickly or interactively |
| `agent` | Run interactive chat or single-message mode |
| `gateway` | Start/manage the gateway server (web dashboard + webhooks + WebSockets) |
| `acp` | Start ACP (Agent Control Protocol) server over stdio |
| `daemon` | Start supervised runtime (gateway + channels + heartbeat + cron scheduler) |
| `service` | Manage OS service lifecycle (launchd/systemd/OpenRC) |
| `doctor` | Run diagnostics and freshness checks |
| `status` | Print current configuration and system summary |
| `estop` | Engage/resume emergency stop levels and inspect estop state |
| `cron` | Manage scheduled tasks |
| `models` | Refresh and inspect provider model catalogs |
| `providers` | List provider IDs, aliases, and active provider |
| `channel` | Manage channels and channel health checks |
| `integrations` | Inspect integration details |
| `skills` | List/install/remove/audit skills |
| `workflows` | List or sync bundled workflow templates |
| `migrate` | Import from external runtimes (currently OpenClaw) |
| `auth` | Manage provider subscription authentication profiles (OAuth, token-based) |
| `memory` | Manage agent memory entries (list, get, stats, clear) |
| `config` | Export machine-readable config schema |
| `install` | Install Revka sidecars and related components |
| `update` | Check for and apply updates (6-phase pipeline with rollback) |
| `self-test` | Run diagnostic self-tests to verify the installation |
| `completions` | Generate shell completion scripts to stdout |
| `hardware` | Discover and introspect USB hardware |
| `peripheral` | Configure and flash peripherals |
| `desktop` | Launch or install the companion desktop app (Tauri shell) |
| `plugin` | Manage WASM plugins (only when built with `plugins-wasm` feature) |

## Command Groups

### `onboard`

- `revka onboard`
- `revka onboard --channels-only`
- `revka onboard --force`
- `revka onboard --reinit`
- `revka onboard --api-key <KEY> --provider <ID> --memory <kumiho|none>`
- `revka onboard --api-key <KEY> --provider <ID> --model <MODEL_ID> --memory <kumiho|none>`
- `revka onboard --api-key <KEY> --provider <ID> --model <MODEL_ID> --memory <kumiho|none> --force`

`onboard` safety behavior:

- If `config.toml` already exists, onboarding offers two modes:
  - Full onboarding (overwrite `config.toml`)
  - Provider-only update (update provider/model/API key while preserving existing channels, tunnel, memory, hooks, and other settings)
- In non-interactive environments, existing `config.toml` causes a safe refusal unless `--force` is passed.
- Use `revka onboard --channels-only` when you only need to rotate channel tokens/allowlists.
- Use `revka onboard --reinit` to start fresh. This backs up your existing config directory with a timestamp suffix and creates a new configuration from scratch.

### `agent`

- `revka agent`
- `revka agent -m "Hello"`
- `revka agent --provider <ID> --model <MODEL> --temperature <0.0-2.0>`
- `revka agent --peripheral <board:path>`

Tip:

- In interactive chat, you can ask for route changes in natural language (for example “conversation uses kimi, coding uses gpt-5.3-codex”); the assistant can persist this via tool `model_routing_config`.

### `acp`

- `revka acp`
- `revka acp --max-sessions <N>`
- `revka acp --session-timeout <SECONDS>`

Start the ACP (Agent Control Protocol) server for IDE and tool integration.

- Uses JSON-RPC 2.0 over stdin/stdout
- Supports methods: `initialize`, `session/new`, `session/prompt`, `session/stop`
- Streams agent reasoning, tool calls, and content in real-time as notifications
- Default max sessions: 10
- Default session timeout: 3600 seconds (1 hour)

### `gateway` / `daemon`

- `revka gateway` / `revka gateway start [--host <HOST>] [--port <PORT>]`
- `revka gateway restart [--host <HOST>] [--port <PORT>]`
- `revka gateway get-paircode [--new]`
- `revka daemon [--host <HOST>] [--port <PORT>]`

Notes:

- `gateway` hosts the embedded React web dashboard at `http://<host>:<port>/`
  (default `127.0.0.1:42617`), plus REST API, SSE (`/api/events`), and
  WebSocket endpoints (`/ws/chat`, `/ws/canvas/{id}`, `/ws/nodes`).
- `/ws/chat` accepts `{"type":"message","content":"..."}` to start a turn,
  `{"type":"steer","content":"..."}` while a turn is active, and
  `{"type":"stop"}` to cancel the active turn.
- `daemon` runs gateway + all configured channels + heartbeat + cron scheduler
  together. Use `revka service install` + `revka service start` to keep
  it resident on boot.
- Pairing: `revka gateway get-paircode` prints the current device pair code
  (or `--new` to rotate).

### `estop`

- `revka estop` (engage `kill-all`)
- `revka estop --level network-kill`
- `revka estop --level domain-block --domain "*.chase.com" [--domain "*.paypal.com"]`
- `revka estop --level tool-freeze --tool shell [--tool browser]`
- `revka estop status`
- `revka estop resume`
- `revka estop resume --network`
- `revka estop resume --domain "*.chase.com"`
- `revka estop resume --tool shell`
- `revka estop resume --otp <123456>`

Notes:

- `estop` commands require `[security.estop].enabled = true`.
- When `[security.estop].require_otp_to_resume = true`, `resume` requires OTP validation.
- OTP prompt appears automatically if `--otp` is omitted.

### `service`

- `revka service install`
- `revka service start`
- `revka service stop`
- `revka service restart`
- `revka service status`
- `revka service logs [-n <LINES>] [--follow]`
- `revka service uninstall`

### `cron`

- `revka cron list`
- `revka cron add <expr> [--tz <IANA_TZ>] <command>`
- `revka cron add-at <rfc3339_timestamp> <command>`
- `revka cron add-every <every_ms> <command>`
- `revka cron once <delay> <command>`
- `revka cron remove <id>`
- `revka cron pause <id>`
- `revka cron resume <id>`
- `revka cron update <id> [--expression <EXPR>] [--tz <IANA_TZ>] [--command <CMD>]`

Notes:

- Mutating schedule/cron actions require `cron.enabled = true`.
- Shell command payloads for schedule creation (`create` / `add` / `once`) are validated by security command policy before job persistence.
- **Timezone semantics** — `cron add` accepts an IANA timezone via `--tz` (e.g. `--tz America/Los_Angeles`, `--tz Asia/Seoul`, `--tz UTC`). When `--tz` is omitted the default is **UTC** — the cron expression is interpreted against UTC wall-clock, not the daemon host's local timezone. The runtime validates `--tz` strings against the IANA tz database via `chrono-tz`; non-IANA values are rejected at job-add time. Cron round-trip semantics (per-job `tz`) are exercised by `src/cron/types.rs::tests::cron_with_tz_*`.
- `add-at` / `add-every` / `once` do **not** accept `--tz`. `add-at` takes an RFC 3339 timestamp (which embeds its own offset); `add-every` and `once` schedule from the moment of registration and are timezone-agnostic by construction.

### `models`

- `revka models refresh`
- `revka models refresh --provider <ID>`
- `revka models refresh --all`
- `revka models refresh --force`
- `revka models list [--provider <ID>]`
- `revka models set <MODEL_ID>`
- `revka models status`

`models refresh` currently supports live catalog refresh for provider IDs: `openrouter`, `openai`, `anthropic`, `groq`, `mistral`, `deepseek`, `xai`, `together-ai`, `gemini`, `ollama`, `llamacpp`, `sglang`, `vllm`, `astrai`, `venice`, `fireworks`, `cohere`, `moonshot`, `glm`, `zai`, `qwen`, and `nvidia`.

- `models list` prints the currently cached model catalog for the resolved provider.
- `models set` writes `default_model` to `~/.revka/config.toml`.
- `models status` prints the active model configuration and cache freshness.

### `doctor`

- `revka doctor`
- `revka doctor models [--provider <ID>] [--use-cache]`
- `revka doctor traces [--limit <N>] [--event <TYPE>] [--contains <TEXT>]`
- `revka doctor traces --id <TRACE_ID>`

`doctor traces` reads runtime tool/model diagnostics from `observability.runtime_trace_path`.

### `channel`

- `revka channel list`
- `revka channel start`
- `revka channel doctor`
- `revka channel bind-telegram <IDENTITY>`
- `revka channel add <type> <json>`
- `revka channel remove <name>`
- `revka channel send <message> --channel-id <NAME> --recipient <TARGET>`

Runtime in-chat commands (Telegram/Discord while channel server is running):

- `/models`
- `/models <provider>`
- `/model`
- `/model <model-id>`
- `/new`

Channel runtime also watches `config.toml` and hot-applies updates to:
- `default_provider`
- `default_model`
- `default_temperature`
- `api_key` / `api_url` (for the default provider)
- `reliability.*` provider retry settings

`add/remove` currently route you back to managed setup/manual config paths (not full declarative mutators yet).

### `integrations`

- `revka integrations info <name>`

### `skills`

- `revka skills list`
- `revka skills audit <source_or_name>`
- `revka skills install <source>`
- `revka skills remove <name>`
- `revka skills test [<name>] [--verbose]`

`<source>` accepts git remotes (`https://...`, `http://...`, `ssh://...`, and `git@host:owner/repo.git`) or a local filesystem path.

`skills install` always runs a built-in static security audit before the skill is accepted. The audit blocks:
- symlinks inside the skill package
- script-like files (`.sh`, `.bash`, `.zsh`, `.ps1`, `.bat`, `.cmd`)
- high-risk command snippets (for example pipe-to-shell payloads)
- markdown links that escape the skill root, point to remote markdown, or target script files

Use `skills audit` to manually validate a candidate skill directory (or an installed skill by name) before sharing it.

Skill manifests (`SKILL.toml`) support `prompts` and `[[tools]]`; both are injected into the agent system prompt at runtime, so the model can follow skill instructions without manually reading skill files.

### `workflows`

- `revka workflows list`
- `revka workflows sync [--force]`

`sync` seeds the bundled workflow YAMLs into the active workspace under
`operator_mcp/workflow/builtins/`.

### `migrate`

- `revka migrate openclaw [--source <path>] [--dry-run]`

### `auth`

Manage provider subscription authentication profiles (OAuth for `openai-codex`,
`gemini`, Anthropic subscription setup tokens, etc.).

- `revka auth login --provider <openai-codex|gemini> [--profile <name>] [--device-code] [--import <PATH>]`
- `revka auth paste-redirect --provider openai-codex [--profile <name>] [--input <URL_OR_CODE>]`
- `revka auth paste-token --provider anthropic [--profile <name>] [--token <VALUE>] [--auth-kind <authorization|api-key>]`
- `revka auth setup-token --provider anthropic [--profile <name>]` (interactive alias of `paste-token`)
- `revka auth refresh --provider openai-codex [--profile <name>]`
- `revka auth use --provider <ID> --profile <name>`
- `revka auth logout --provider <ID> [--profile <name>]`
- `revka auth list`
- `revka auth status`

Notes:

- `--import` is currently supported for `openai-codex` only and defaults to
  `~/.codex/auth.json` when path is omitted.
- `use` sets the active profile for subsequent requests.
- `status` reports the active profile per provider and token expiry info when
  available.

### `memory`

Inspect and manage agent memory entries.

- `revka memory stats`
- `revka memory list [--category <name>] [--session <id>] [--limit <N>] [--offset <N>]`
- `revka memory get <KEY>`
- `revka memory clear [--key <KEY>] [--category <CATEGORY>] [--yes]`

Notes:

- `get` and `clear --key` support prefix match against the memory key.
- `clear` with no `--key`/`--category` wipes all entries (requires `--yes` to
  skip confirmation).
- Applies to the local memory backend configured under `[memory]`; for the
  Kumiho graph memory browser, use the `Assets` / `Memory` views on the web
  dashboard or the `kumiho` proxy under `/api/kumiho/*`.

### `config`

- `revka config schema`

`config schema` prints a JSON Schema (draft 2020-12) for the full `config.toml` contract to stdout.

### `install`

- `revka install --sidecars-only`
- `revka install --sidecars-only --skip-kumiho --skip-operator`
- `revka install --sidecars-only --dry-run`
- `revka install --sidecars-only --with-session-manager`
- `revka install --sidecars-only --python <PYTHON>`
- `revka install --sidecars-only --from-source <REPO_PATH>`

Notes:

- `--sidecars-only` is currently required; the broader repo installer still
  lives outside this subcommand.
- `--with-session-manager` is optional and changes the spawned-agent auth/cost
  path because it uses the Claude Agent SDK.

### `completions`

- `revka completions bash`
- `revka completions fish`
- `revka completions zsh`
- `revka completions powershell`
- `revka completions elvish`

`completions` is stdout-only by design so scripts can be sourced directly without log/warning contamination.

### `hardware`

- `revka hardware discover`
- `revka hardware introspect <path>`
- `revka hardware info [--chip <chip_name>]`

### `peripheral`

- `revka peripheral list`
- `revka peripheral add <board> <path>`
- `revka peripheral flash [--port <serial_port>]`
- `revka peripheral setup-uno-q [--host <ip_or_host>]`
- `revka peripheral flash-nucleo`

### `update`

- `revka update` — download and install the latest release
- `revka update --check` — only check for updates, do not install
- `revka update --force` — install without confirmation prompt
- `revka update --version <X.Y.Z>` — install a specific version

The updater runs a 6-phase pipeline: preflight, download, backup, validate,
swap, and smoke test. Automatic rollback on failure.

### `self-test`

- `revka self-test` — run the full suite (includes network: gateway health, memory round-trip)
- `revka self-test --quick` — skip network checks for offline validation

### `desktop`

- `revka desktop` — launch the Revka companion desktop app (Tauri shell
  that points at the local gateway at `http://127.0.0.1:42617/_app/`)
- `revka desktop --install` — download and install the pre-built companion
  app for your platform

### `plugin`

Only available when built with the `plugins-wasm` Cargo feature.

- `revka plugin list`
- `revka plugin install <source>` (directory or URL)
- `revka plugin remove <name>`
- `revka plugin info <name>`

## Validation Tip

To verify docs against your current binary quickly:

```bash
revka --help
revka <command> --help
```
