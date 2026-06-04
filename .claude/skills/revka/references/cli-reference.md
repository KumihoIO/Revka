# Revka CLI Reference

Complete command reference for the `revka` binary.

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
revka agent                                          # Interactive REPL
revka agent -m "Summarize today's logs"              # Single message
revka agent -p anthropic --model claude-sonnet-4-6   # Override provider/model
revka agent -t 0.3                                   # Set temperature
revka agent --peripheral nucleo-f401re:/dev/ttyACM0  # Attach hardware
```

**Key flags:**
- `-m <message>` — single message mode (no REPL)
- `-p <provider>` — override provider (openrouter, anthropic, openai, ollama)
- `--model <model>` — override model
- `-t <float>` — temperature (0.0–2.0)
- `--peripheral <name>:<port>` — attach hardware peripheral

The agent has access to 30+ tools gated by security policy: shell, file_read, file_write, file_edit, glob_search, content_search, browser, http_request, web_fetch, web_search, cron, delegate, git, and more. Memory persistence is provided via the Kumiho MCP (`kumiho_memory_engage`, `kumiho_memory_reflect`, `kumiho_memory_store`, …) and the Operator MCP (`revka-operator__memory_store`, `revka-operator__memory_search`). Max tool iterations defaults to 10.

---

## Onboarding

First-time setup or reconfiguration.

```bash
revka onboard                                 # Quick mode (default: openrouter)
revka onboard --provider anthropic            # Quick mode with specific provider
revka onboard                                 # Guided wizard (default)
revka onboard --memory sqlite                 # Set memory backend
revka onboard --force                         # Overwrite existing config
revka onboard --channels-only                 # Repair channels only
```

**Key flags:**
- `--provider <name>` — openrouter (default), anthropic, openai, ollama
- `--model <model>` — default model
- `--memory <backend>` — sqlite, markdown, lucid, none
- `--force` — overwrite existing config.toml
- `--channels-only` — only repair channel configuration
- `--reinit` — start fresh (backs up existing config)

Creates `~/.revka/config.toml` with `0600` permissions.

---

## Status & Diagnostics

```bash
revka status                    # System overview
revka doctor                    # Run all diagnostic checks
revka doctor models             # Probe model connectivity
revka doctor traces             # Query execution traces
```

---

## Memory

```bash
revka memory list                              # List all entries
revka memory list --category core --limit 10   # Filtered list
revka memory get "some-key"                    # Get specific entry
revka memory stats                             # Usage statistics
revka memory clear --key "prefix" --yes        # Delete entries (requires --yes)
```

**Key flags:**
- `--category <name>` — filter by category (core, daily, conversation, custom)
- `--limit <n>` — limit results
- `--key <prefix>` — key prefix for clear operations
- `--yes` — skip confirmation (required for clear)

---

## Cron

```bash
revka cron list                                                      # List all jobs
revka cron add '0 9 * * 1-5' 'Good morning' --tz America/New_York   # Recurring (cron expr)
revka cron add-at '2026-03-11T10:00:00Z' 'Remind me about meeting'  # One-time at specific time
revka cron add-every 3600000 'Check server health'                   # Interval in milliseconds
revka cron once 30m 'Follow up on that task'                         # Delay from now
revka cron pause <id>                                                # Pause job
revka cron resume <id>                                               # Resume job
revka cron remove <id>                                               # Delete job
```

**Subcommands:**
- `add <cron-expr> <command>` — standard cron expression (5-field)
- `add-at <iso-datetime> <command>` — fire once at exact time
- `add-every <ms> <command>` — repeating interval
- `once <duration> <command>` — delay from now (e.g., `30m`, `2h`, `1d`)

---

## Providers & Models

```bash
revka providers                                # List all 40+ supported providers
revka models list                              # Show cached model catalog
revka models refresh --all                     # Refresh catalogs from all providers
revka models set anthropic/claude-sonnet-4-6   # Set default model
revka models status                            # Current model info
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
revka gateway                                 # Start HTTP gateway (foreground)
revka gateway -p 8080 --host 127.0.0.1        # Custom port/host

revka daemon                                  # Gateway + channels + scheduler + heartbeat
revka daemon -p 8080 --host 0.0.0.0           # Custom bind
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
revka service install     # Install as system service
revka service start       # Start the service
revka service status      # Check service status
revka service stop        # Stop the service
revka service restart     # Restart the service
revka service uninstall   # Remove the service
```

**Logs:**
- macOS: `~/.revka/logs/daemon.stdout.log`
- Linux: `journalctl -u revka`

---

## Channels

Channels are configured in `config.toml` under `[channels]` and `[channels_config.*]`.

```bash
revka channels list       # List configured channels
revka channels doctor     # Check channel health
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
revka estop --level kill-all                              # Stop everything
revka estop --level network-kill                          # Block all network access
revka estop --level domain-block --domain "*.example.com" # Block specific domains
revka estop --level tool-freeze --tool shell              # Freeze specific tool
revka estop status                                        # Check estop state
revka estop resume --network                              # Resume (may require OTP)
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
revka hardware discover                              # Find USB devices
revka hardware introspect /dev/ttyACM0               # Probe device capabilities
revka peripheral list                                # List configured peripherals
revka peripheral add nucleo-f401re /dev/ttyACM0      # Add peripheral
revka peripheral flash-nucleo                        # Flash STM32 firmware
revka peripheral flash --port /dev/cu.usbmodem101    # Flash Arduino firmware
```

**Supported boards:** STM32 Nucleo-F401RE, Arduino Uno R4, Raspberry Pi GPIO, ESP32.

Attach to agent session: `revka agent --peripheral nucleo-f401re:/dev/ttyACM0`

---

## Skills

```bash
revka skills list         # List installed skills
revka skills install <path-or-url>  # Install a skill
revka skills audit        # Audit installed skills
revka skills remove <name>  # Remove a skill
```

---

## Shell Completions

```bash
revka completions zsh     # Generate Zsh completions
revka completions bash    # Generate Bash completions
revka completions fish    # Generate Fish completions
```

---

## Config File

Default location: `~/.revka/config.toml`

Config resolution order (first match wins):
1. `REVKA_CONFIG_DIR` environment variable
2. `REVKA_WORKSPACE` environment variable
3. `~/.revka/active_workspace.toml` marker file
4. `~/.revka/config.toml` (default)
