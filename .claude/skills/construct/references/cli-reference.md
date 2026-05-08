# Construct CLI Reference

Complete command reference for the `construct` binary.

## Table of Contents

1. [Agent](#agent)
2. [Onboarding](#onboarding)
3. [Status & Diagnostics](#status--diagnostics)
4. [Memory](#memory)
5. [Cron](#cron)
6. [Providers & Models](#providers--models)
7. [Gateway & Daemon](#gateway--daemon)
8. [Service Management](#service-management)
9. [Channels](#channels)
10. [Security & Emergency Stop](#security--emergency-stop)
11. [Hardware Peripherals](#hardware-peripherals)
12. [Skills](#skills)
13. [Shell Completions](#shell-completions)

---

## Agent

Interactive chat or single-message mode.

```bash
construct agent                                          # Interactive REPL
construct agent -m "Summarize today's logs"              # Single message
construct agent -p anthropic --model claude-sonnet-4-6   # Override provider/model
construct agent -t 0.3                                   # Set temperature
construct agent --peripheral nucleo-f401re:/dev/ttyACM0  # Attach hardware
```

**Key flags:**
- `-m <message>` — single message mode (no REPL)
- `-p <provider>` — override provider (openrouter, anthropic, openai, ollama)
- `--model <model>` — override model
- `-t <float>` — temperature (0.0–2.0)
- `--peripheral <name>:<port>` — attach hardware peripheral

The agent has access to 30+ tools gated by security policy: shell, file_read, file_write, file_edit, glob_search, content_search, browser, http_request, web_fetch, web_search, cron, delegate, git, and more. Memory persistence is provided via the Kumiho MCP (`kumiho_memory_engage`, `kumiho_memory_reflect`, `kumiho_memory_store`, …) and the Operator MCP (`construct-operator__memory_store`, `construct-operator__memory_search`). Max tool iterations defaults to 10.

---

## Onboarding

First-time setup or reconfiguration.

```bash
construct onboard                                 # Quick mode (default: openrouter)
construct onboard --provider anthropic            # Quick mode with specific provider
construct onboard                                 # Guided wizard (default)
construct onboard --memory sqlite                 # Set memory backend
construct onboard --force                         # Overwrite existing config
construct onboard --channels-only                 # Repair channels only
```

**Key flags:**
- `--provider <name>` — openrouter (default), anthropic, openai, ollama
- `--model <model>` — default model
- `--memory <backend>` — sqlite, markdown, lucid, none
- `--force` — overwrite existing config.toml
- `--channels-only` — only repair channel configuration
- `--reinit` — start fresh (backs up existing config)

Creates `~/.construct/config.toml` with `0600` permissions.

---

## Status & Diagnostics

```bash
construct status                    # System overview
construct doctor                    # Run all diagnostic checks
construct doctor models             # Probe model connectivity
construct doctor traces             # Query execution traces
```

---

## Memory

```bash
construct memory list                              # List all entries
construct memory list --category core --limit 10   # Filtered list
construct memory get "some-key"                    # Get specific entry
construct memory stats                             # Usage statistics
construct memory clear --key "prefix" --yes        # Delete entries (requires --yes)
```

**Key flags:**
- `--category <name>` — filter by category (core, daily, conversation, custom)
- `--limit <n>` — limit results
- `--key <prefix>` — key prefix for clear operations
- `--yes` — skip confirmation (required for clear)

---

## Cron

```bash
construct cron list                                                      # List all jobs
construct cron add '0 9 * * 1-5' 'Good morning' --tz America/New_York   # Recurring (cron expr)
construct cron add-at '2026-03-11T10:00:00Z' 'Remind me about meeting'  # One-time at specific time
construct cron add-every 3600000 'Check server health'                   # Interval in milliseconds
construct cron once 30m 'Follow up on that task'                         # Delay from now
construct cron pause <id>                                                # Pause job
construct cron resume <id>                                               # Resume job
construct cron remove <id>                                               # Delete job
```

**Subcommands:**
- `add <cron-expr> <command>` — standard cron expression (5-field)
- `add-at <iso-datetime> <command>` — fire once at exact time
- `add-every <ms> <command>` — repeating interval
- `once <duration> <command>` — delay from now (e.g., `30m`, `2h`, `1d`)

---

## Providers & Models

```bash
construct providers                                # List all 40+ supported providers
construct models list                              # Show cached model catalog
construct models refresh --all                     # Refresh catalogs from all providers
construct models set anthropic/claude-sonnet-4-6   # Set default model
construct models status                            # Current model info
```

Model routing in config.toml:
```toml
[[model_routes]]
hint = "reasoning"
provider = "openrouter"
model = "anthropic/claude-sonnet-4-6"
```

---

## Gateway & Daemon

```bash
construct gateway                                 # Start HTTP gateway (foreground)
construct gateway -p 8080 --host 127.0.0.1        # Custom port/host

construct daemon                                  # Gateway + channels + scheduler + heartbeat
construct daemon -p 8080 --host 0.0.0.0           # Custom bind
```

**Gateway defaults:**
- Port: 42617
- Host: 127.0.0.1
- Pairing required: true
- Public bind allowed: false

---

## Service Management

OS service lifecycle (systemd on Linux, launchd on macOS).

```bash
construct service install     # Install as system service
construct service start       # Start the service
construct service status      # Check service status
construct service stop        # Stop the service
construct service restart     # Restart the service
construct service uninstall   # Remove the service
```

**Logs:**
- macOS: `~/.construct/logs/daemon.stdout.log`
- Linux: `journalctl -u construct`

---

## Channels

Channels are configured in `config.toml` under `[channels]` and `[channels_config.*]`.

```bash
construct channels list       # List configured channels
construct channels doctor     # Check channel health
```

Supported channels (21 total): Telegram, Discord, Slack, WhatsApp (Meta), WATI, Linq (iMessage/RCS/SMS), Email (IMAP/SMTP), IRC, Matrix, Nostr, Signal, Nextcloud Talk, and more.

Channel config example (Telegram):
```toml
[channels]
telegram = true

[channels_config.telegram]
bot_token = "..."
allowed_users = [123456789]
```

---

## Security & Emergency Stop

```bash
construct estop --level kill-all                              # Stop everything
construct estop --level network-kill                          # Block all network access
construct estop --level domain-block --domain "*.example.com" # Block specific domains
construct estop --level tool-freeze --tool shell              # Freeze specific tool
construct estop status                                        # Check estop state
construct estop resume --network                              # Resume (may require OTP)
```

**Estop levels:**
- `kill-all` — nuclear option, stops all agent activity
- `network-kill` — blocks all outbound network
- `domain-block` — blocks specific domain patterns
- `tool-freeze` — freezes individual tools

Autonomy config in config.toml:
```toml
[autonomy]
level = "supervised"                           # read_only | supervised | full
workspace_only = true
allowed_commands = ["git", "cargo", "python"]
forbidden_paths = ["/etc", "/root", "~/.ssh"]
max_actions_per_hour = 20
max_cost_per_day_cents = 500
```

---

## Hardware Peripherals

```bash
construct hardware discover                              # Find USB devices
construct hardware introspect /dev/ttyACM0               # Probe device capabilities
construct peripheral list                                # List configured peripherals
construct peripheral add nucleo-f401re /dev/ttyACM0      # Add peripheral
construct peripheral flash-nucleo                        # Flash STM32 firmware
construct peripheral flash --port /dev/cu.usbmodem101    # Flash Arduino firmware
```

**Supported boards:** STM32 Nucleo-F401RE, Arduino Uno R4, Raspberry Pi GPIO, ESP32.

Attach to agent session: `construct agent --peripheral nucleo-f401re:/dev/ttyACM0`

---

## Skills

```bash
construct skills list         # List installed skills
construct skills install <path-or-url>  # Install a skill
construct skills audit        # Audit installed skills
construct skills remove <name>  # Remove a skill
```

---

## Shell Completions

```bash
construct completions zsh     # Generate Zsh completions
construct completions bash    # Generate Bash completions
construct completions fish    # Generate Fish completions
```

---

## Config File

Default location: `~/.construct/config.toml`

Config resolution order (first match wins):
1. `CONSTRUCT_CONFIG_DIR` environment variable
2. `CONSTRUCT_WORKSPACE` environment variable
3. `~/.construct/active_workspace.toml` marker file
4. `~/.construct/config.toml` (default)
