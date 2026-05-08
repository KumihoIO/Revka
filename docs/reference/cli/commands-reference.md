# Construct Commands Reference

This reference is derived from the current CLI surface (`construct --help`).

Last verified: **April 21, 2026**.

The `construct` binary also embeds the React/TypeScript web dashboard served by
`construct gateway` / `construct daemon` at `http://127.0.0.1:42617`. Most
operational surface is available in both CLI and dashboard; this document
covers the CLI surface only.

<!-- TODO screenshot: dashboard top-level commands menu/navigation -->
![Dashboard top-level commands menu/navigation](../../assets/reference/commands-reference-01-dashboard-menu.png)

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
| `migrate` | Import from external runtimes (currently OpenClaw) |
| `auth` | Manage provider subscription authentication profiles (OAuth, token-based) |
| `memory` | Manage agent memory entries (list, get, stats, clear) |
| `config` | Export machine-readable config schema |
| `update` | Check for and apply updates (6-phase pipeline with rollback) |
| `self-test` | Run diagnostic self-tests to verify the installation |
| `completions` | Generate shell completion scripts to stdout |
| `hardware` | Discover and introspect USB hardware |
| `peripheral` | Configure and flash peripherals |
| `desktop` | Launch or install the companion desktop app (Tauri shell) |
| `plugin` | Manage WASM plugins (only when built with `plugins-wasm` feature) |

## Command Groups

### `onboard`

- `construct onboard`
- `construct onboard --channels-only`
- `construct onboard --force`
- `construct onboard --reinit`
- `construct onboard --api-key <KEY> --provider <ID> --memory <sqlite|lucid|markdown|none>`
- `construct onboard --api-key <KEY> --provider <ID> --model <MODEL_ID> --memory <sqlite|lucid|markdown|none>`
- `construct onboard --api-key <KEY> --provider <ID> --model <MODEL_ID> --memory <sqlite|lucid|markdown|none> --force`

`onboard` safety behavior:

- If `config.toml` already exists, onboarding offers two modes:
  - Full onboarding (overwrite `config.toml`)
  - Provider-only update (update provider/model/API key while preserving existing channels, tunnel, memory, hooks, and other settings)
- In non-interactive environments, existing `config.toml` causes a safe refusal unless `--force` is passed.
- Use `construct onboard --channels-only` when you only need to rotate channel tokens/allowlists.
- Use `construct onboard --reinit` to start fresh. This backs up your existing config directory with a timestamp suffix and creates a new configuration from scratch.

### `agent`

- `construct agent`
- `construct agent -m "Hello"`
- `construct agent --provider <ID> --model <MODEL> --temperature <0.0-2.0>`
- `construct agent --peripheral <board:path>`

Tip:

- In interactive chat, you can ask for route changes in natural language (for example “conversation uses kimi, coding uses gpt-5.3-codex”); the assistant can persist this via tool `model_routing_config`.

### `acp`

- `construct acp`
- `construct acp --max-sessions <N>`
- `construct acp --session-timeout <SECONDS>`

Start the ACP (Agent Control Protocol) server for IDE and tool integration.

- Uses JSON-RPC 2.0 over stdin/stdout
- Supports methods: `initialize`, `session/new`, `session/prompt`, `session/stop`
- Streams agent reasoning, tool calls, and content in real-time as notifications
- Default max sessions: 10
- Default session timeout: 3600 seconds (1 hour)

<!-- TODO screenshot: browser showing the embedded Construct dashboard served by the gateway at localhost:42617 -->
![Browser showing the embedded Construct dashboard served by the gateway at localhost:42617](../../assets/reference/commands-reference-02-dashboard-browser.png)

### `gateway` / `daemon`

- `construct gateway` / `construct gateway start [--host <HOST>] [--port <PORT>]`
- `construct gateway restart [--host <HOST>] [--port <PORT>]`
- `construct gateway get-paircode [--new]`
- `construct daemon [--host <HOST>] [--port <PORT>]`

Notes:

- `gateway` hosts the embedded React web dashboard at `http://<host>:<port>/`
  (default `127.0.0.1:42617`), plus REST API, SSE (`/api/events`), and
  WebSocket endpoints (`/ws/chat`, `/ws/canvas/{id}`, `/ws/nodes`).
- `daemon` runs gateway + all configured channels + heartbeat + cron scheduler
  together. Use `construct service install` + `construct service start` to keep
  it resident on boot.
- Pairing: `construct gateway get-paircode` prints the current device pair code
  (or `--new` to rotate).

### `estop`

- `construct estop` (engage `kill-all`)
- `construct estop --level network-kill`
- `construct estop --level domain-block --domain "*.chase.com" [--domain "*.paypal.com"]`
- `construct estop --level tool-freeze --tool shell [--tool browser]`
- `construct estop status`
- `construct estop resume`
- `construct estop resume --network`
- `construct estop resume --domain "*.chase.com"`
- `construct estop resume --tool shell`
- `construct estop resume --otp <123456>`

Notes:

- `estop` commands require `[security.estop].enabled = true`.
- When `[security.estop].require_otp_to_resume = true`, `resume` requires OTP validation.
- OTP prompt appears automatically if `--otp` is omitted.

### `service`

- `construct service install`
- `construct service start`
- `construct service stop`
- `construct service restart`
- `construct service status`
- `construct service uninstall`

### `cron`

- `construct cron list`
- `construct cron add <expr> [--tz <IANA_TZ>] <command>`
- `construct cron add-at <rfc3339_timestamp> <command>`
- `construct cron add-every <every_ms> <command>`
- `construct cron once <delay> <command>`
- `construct cron remove <id>`
- `construct cron pause <id>`
- `construct cron resume <id>`

Notes:

- Mutating schedule/cron actions require `cron.enabled = true`.
- Shell command payloads for schedule creation (`create` / `add` / `once`) are validated by security command policy before job persistence.
- **Timezone semantics** — `cron add` accepts an IANA timezone via `--tz` (e.g. `--tz America/Los_Angeles`, `--tz Asia/Seoul`, `--tz UTC`). When `--tz` is omitted the default is **UTC** — the cron expression is interpreted against UTC wall-clock, not the daemon host's local timezone. The runtime validates `--tz` strings against the IANA tz database via `chrono-tz`; non-IANA values are rejected at job-add time. Cron round-trip semantics (per-job `tz`) are exercised by `src/cron/types.rs::tests::cron_with_tz_*`.
- `add-at` / `add-every` / `once` do **not** accept `--tz`. `add-at` takes an RFC 3339 timestamp (which embeds its own offset); `add-every` and `once` schedule from the moment of registration and are timezone-agnostic by construction.

### `models`

- `construct models refresh`
- `construct models refresh --provider <ID>`
- `construct models refresh --all`
- `construct models refresh --force`
- `construct models list [--provider <ID>]`
- `construct models set <MODEL_ID>`
- `construct models status`

`models refresh` currently supports live catalog refresh for provider IDs: `openrouter`, `openai`, `anthropic`, `groq`, `mistral`, `deepseek`, `xai`, `together-ai`, `gemini`, `ollama`, `llamacpp`, `sglang`, `vllm`, `astrai`, `venice`, `fireworks`, `cohere`, `moonshot`, `glm`, `zai`, `qwen`, and `nvidia`.

- `models list` prints the currently cached model catalog for the resolved provider.
- `models set` writes `default_model` to `~/.construct/config.toml`.
- `models status` prints the active model configuration and cache freshness.

<!-- TODO screenshot: terminal showing formatted `construct doctor` diagnostics output -->
![Terminal showing formatted construct doctor diagnostics output](../../assets/reference/commands-reference-03-doctor-output.png)

### `doctor`

- `construct doctor`
- `construct doctor models [--provider <ID>] [--use-cache]`
- `construct doctor traces [--limit <N>] [--event <TYPE>] [--contains <TEXT>]`
- `construct doctor traces --id <TRACE_ID>`

`doctor traces` reads runtime tool/model diagnostics from `observability.runtime_trace_path`.

### `channel`

- `construct channel list`
- `construct channel start`
- `construct channel doctor`
- `construct channel bind-telegram <IDENTITY>`
- `construct channel add <type> <json>`
- `construct channel remove <name>`

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

- `construct integrations info <name>`

### `skills`

- `construct skills list`
- `construct skills audit <source_or_name>`
- `construct skills install <source>`
- `construct skills remove <name>`

`<source>` accepts git remotes (`https://...`, `http://...`, `ssh://...`, and `git@host:owner/repo.git`) or a local filesystem path.

`skills install` always runs a built-in static security audit before the skill is accepted. The audit blocks:
- symlinks inside the skill package
- script-like files (`.sh`, `.bash`, `.zsh`, `.ps1`, `.bat`, `.cmd`)
- high-risk command snippets (for example pipe-to-shell payloads)
- markdown links that escape the skill root, point to remote markdown, or target script files

Use `skills audit` to manually validate a candidate skill directory (or an installed skill by name) before sharing it.

Skill manifests (`SKILL.toml`) support `prompts` and `[[tools]]`; both are injected into the agent system prompt at runtime, so the model can follow skill instructions without manually reading skill files.

### `migrate`

- `construct migrate openclaw [--source <path>] [--dry-run]`

### `auth`

Manage provider subscription authentication profiles (OAuth for `openai-codex`,
`gemini`, Anthropic subscription setup tokens, etc.).

- `construct auth login --provider <openai-codex|gemini> [--profile <name>] [--device-code] [--import <PATH>]`
- `construct auth paste-redirect --provider openai-codex [--profile <name>] [--input <URL_OR_CODE>]`
- `construct auth paste-token --provider anthropic [--profile <name>] [--token <VALUE>] [--auth-kind <authorization|api-key>]`
- `construct auth setup-token --provider anthropic [--profile <name>]` (interactive alias of `paste-token`)
- `construct auth refresh --provider openai-codex [--profile <name>]`
- `construct auth use --provider <ID> --profile <name>`
- `construct auth logout --provider <ID> [--profile <name>]`
- `construct auth list`
- `construct auth status`

Notes:

- `--import` is currently supported for `openai-codex` only and defaults to
  `~/.codex/auth.json` when path is omitted.
- `use` sets the active profile for subsequent requests.
- `status` reports the active profile per provider and token expiry info when
  available.

### `memory`

Inspect and manage agent memory entries.

- `construct memory stats`
- `construct memory list [--category <name>] [--session <id>] [--limit <N>] [--offset <N>]`
- `construct memory get <KEY>`
- `construct memory clear [--key <KEY>] [--category <CATEGORY>] [--yes]`

Notes:

- `get` and `clear --key` support prefix match against the memory key.
- `clear` with no `--key`/`--category` wipes all entries (requires `--yes` to
  skip confirmation).
- Applies to the local memory backend configured under `[memory]`; for the
  Kumiho graph memory browser, use the `Assets` / `Memory` views on the web
  dashboard or the `kumiho` proxy under `/api/kumiho/*`.

### `config`

- `construct config schema`

`config schema` prints a JSON Schema (draft 2020-12) for the full `config.toml` contract to stdout.

### `completions`

- `construct completions bash`
- `construct completions fish`
- `construct completions zsh`
- `construct completions powershell`
- `construct completions elvish`

`completions` is stdout-only by design so scripts can be sourced directly without log/warning contamination.

### `hardware`

- `construct hardware discover`
- `construct hardware introspect <path>`
- `construct hardware info [--chip <chip_name>]`

### `peripheral`

- `construct peripheral list`
- `construct peripheral add <board> <path>`
- `construct peripheral flash [--port <serial_port>]`
- `construct peripheral setup-uno-q [--host <ip_or_host>]`
- `construct peripheral flash-nucleo`

### `update`

- `construct update` — download and install the latest release
- `construct update --check` — only check for updates, do not install
- `construct update --force` — install without confirmation prompt
- `construct update --version <X.Y.Z>` — install a specific version

The updater runs a 6-phase pipeline: preflight, download, backup, validate,
swap, and smoke test. Automatic rollback on failure.

### `self-test`

- `construct self-test` — run the full suite (includes network: gateway health, memory round-trip)
- `construct self-test --quick` — skip network checks for offline validation

### `desktop`

- `construct desktop` — launch the Construct companion desktop app (Tauri shell
  that points at the local gateway at `http://127.0.0.1:42617/_app/`)
- `construct desktop --install` — download and install the pre-built companion
  app for your platform

### `plugin`

Only available when built with the `plugins-wasm` Cargo feature.

- `construct plugin list`
- `construct plugin install <source>` (directory or URL)
- `construct plugin remove <name>`
- `construct plugin info <name>`

## Validation Tip

To verify docs against your current binary quickly:

```bash
construct --help
construct <command> --help
```
