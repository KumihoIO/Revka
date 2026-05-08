# Construct onboard wizard — English source bundle.
#
# Keys are kebab-case grouped by surface area. Args use {$name} placeholders.
# When you add a new key here, mirror it in i18n/ko/onboard.ftl (or whatever
# language bundle you're authoring) so missing strings don't silently fall
# back to English in production.
#
# This is the Phase 1 surface: banner, step headers, primary prompts at
# each step, provider/memory backend selection, and the final "Next steps"
# block. Per-provider deep instructions and per-channel walkthroughs are
# scheduled for a follow-up PR.

## ── Banner / welcome ─────────────────────────────────────────────

welcome-title = Welcome to the Construct.
welcome-subtitle = This wizard will configure your agent in under 60 seconds.

## ── Language picker (Step 0) ─────────────────────────────────────

step-language-prompt = Select your language / 언어를 선택하세요
step-language-saved = Language set to {$lang}.

## ── Step header (used at the top of every step) ──────────────────

step-header = Step {$num}/{$total}: {$title}

step-1-title = Workspace Setup
step-2-title = AI Provider & API Key
step-3-title = Channels (How You Talk to Construct)
step-4-title = Tunnel (Expose to Internet)
step-5-title = Tool Mode & Security
step-6-title = Hardware (Physical World)
step-7-title = Memory Configuration
step-8-title = Project Context (Personalize Your Agent)
step-9-title = Workspace Files

## ── Step 1: Workspace ────────────────────────────────────────────

workspace-default-location = Default location: {$path}
workspace-use-default = Use default workspace location?
workspace-enter-path = Enter workspace path
workspace-confirmed = ✓ Workspace: {$path}

## ── Step 2: Provider ─────────────────────────────────────────────

provider-select-tier = Select provider category
provider-tier-recommended = Recommended (Anthropic, OpenAI, Google)
provider-tier-fast = Fast / Low-cost (Groq, Cerebras, OpenRouter)
provider-tier-gateway = Gateway / Multi-provider (OpenRouter, LiteLLM)
provider-tier-specialized = Specialized (Cohere, Mistral, Together)
provider-tier-local = Local (Ollama, llama.cpp, LM Studio, vLLM, SGLang)
provider-tier-custom = Custom (OpenAI-compatible endpoint)

provider-select = Select your AI provider
provider-api-key-prompt = Paste your API key (or press Enter to skip)
provider-api-base-prompt = API base URL (e.g. http://localhost:1234 — where Construct sends LLM requests)
provider-api-key-optional = API key (or Enter to skip if not needed)
provider-model-name = Model name (e.g. llama3, gpt-4o, mistral)
provider-select-model = Select your default model
provider-enter-custom-model = Enter custom model ID

## ── Step 3: Channels ─────────────────────────────────────────────

channels-prompt = Connect a channel (or Done to continue)

## ── Step 4: Tunnel ───────────────────────────────────────────────

tunnel-select = Select tunnel provider

## ── Step 5: Tool mode & secrets ──────────────────────────────────

tool-mode-select = Select tool mode
secrets-encrypt = Enable encrypted secret storage?

## ── Step 6: Hardware ─────────────────────────────────────────────

hardware-prompt = How should Construct interact with the physical world?

## ── Step 7: Memory ───────────────────────────────────────────────

memory-select = Select memory backend
memory-kumiho-api-url = Kumiho API URL
memory-kumiho-token = Kumiho service token (KUMIHO_SERVICE_TOKEN)
memory-auto-save = Auto-save conversations to memory?

dreamstate-prompt = Set up nightly DreamState memory consolidation? (recommended)
dreamstate-time-prompt = What time should DreamState run?
dreamstate-cron-created = ✓ DreamState cron job created (schedule: {$time}, next run: {$next})
dreamstate-cron-failed = Could not register DreamState cron job: {$err}

## ── Step 8: Project context ──────────────────────────────────────

ctx-your-name = Your name
ctx-timezone = Your timezone
ctx-timezone-enter = Enter timezone (e.g. America/New_York)
ctx-agent-name = Agent name
ctx-comm-style = Communication style
ctx-comm-style-custom = Custom communication style

## ── Existing-config handling ─────────────────────────────────────

existing-config-found = Existing config found at {$path}. Select setup mode
existing-config-detected-force = ! Existing config detected at {$path}. Proceeding because --force was provided.
existing-config-overwrite-prompt = Existing config found at {$path}. Re-running onboarding will overwrite config.toml. Continue?

setup-mode-full = Full onboarding (overwrite config.toml)
setup-mode-update-provider = Update AI provider/model/API key only
setup-mode-cancel = Cancel

## ── Reinit flow (--reinit) ───────────────────────────────────────

reinit-banner = ⚠️  Reinitializing Construct configuration...
reinit-current-dir = Current config directory: {$path}
reinit-backup-target = This will back up your existing config to: {$path}
reinit-confirm = Continue? [y/N]
reinit-aborted = Aborted.
reinit-backup-ok = Backup created successfully.
reinit-fresh-start = Starting fresh initialization...

## ── Final "Next steps" block ─────────────────────────────────────

next-steps-header = Next steps:
next-step-chat = Start chatting with your agent:
next-step-chat-cmd = construct agent
next-step-gateway = Run the gateway (channels, dashboard):
next-step-gateway-cmd = construct gateway start
next-step-status = Check status anytime:
next-step-status-cmd = construct status
next-step-pairing-enabled = Pairing is enabled. A one-time pairing code will be displayed when the gateway starts.
next-step-dashboard = Dashboard: http://127.0.0.1:{$port}

## ── Errors / misc ────────────────────────────────────────────────

err-no-command = No command provided.
err-try-onboard = Try `construct onboard` to initialize your workspace.

# ════════════════════════════════════════════════════════════════════
# PHASE 2 — provider sub-flows, hardware/project/tunnel details, etc.
# ════════════════════════════════════════════════════════════════════

## ── Step 2: Custom provider sub-flow ─────────────────────────────

custom-provider-title = Custom Provider Setup
custom-provider-subtitle = — any OpenAI-compatible API
custom-provider-info-1 = Construct works with ANY API that speaks the OpenAI chat completions format.
custom-provider-info-2 = Examples: LiteLLM, LocalAI, vLLM, text-generation-webui, LM Studio, etc.
custom-provider-confirmed = ✓ Provider: {$provider} | Model: {$model}

## ── Step 2: Remote Ollama sub-flow ───────────────────────────────

ollama-use-remote = Use a remote Ollama endpoint (for example Ollama Cloud)?
ollama-remote-url-prompt = Remote Ollama endpoint URL
ollama-remote-configured = Remote endpoint configured: {$url}
ollama-normalized-base = Normalized endpoint to base URL (removed trailing /api).
ollama-cloud-suffix-hint = If you use cloud-only models, append {$suffix} to the model ID.
ollama-remote-key-prompt = API key for remote Ollama endpoint (or Enter to skip)
ollama-no-key-hint = No API key provided. Set {$env_var} later if required by your endpoint.
ollama-using-local = Using local Ollama at http://localhost:11434 (no API key needed).

## ── Step 2: llama.cpp / SGLang / vLLM / Osaurus sub-flows ────────

llamacpp-url-prompt = llama.cpp server endpoint URL
llamacpp-using = Using llama.cpp server endpoint: {$url}
llamacpp-key-info = No API key needed unless your llama.cpp server is started with --api-key.
llamacpp-key-prompt = API key for llama.cpp server (or Enter to skip)
local-server-no-key-hint = No API key provided. Set {$env_var} later only if your server requires authentication.

sglang-url-prompt = SGLang server endpoint URL
sglang-using = Using SGLang server endpoint: {$url}
sglang-key-info = No API key needed unless your SGLang server requires authentication.
sglang-key-prompt = API key for SGLang server (or Enter to skip)

vllm-url-prompt = vLLM server endpoint URL
vllm-using = Using vLLM server endpoint: {$url}
vllm-key-info = No API key needed unless your vLLM server requires authentication.
vllm-key-prompt = API key for vLLM server (or Enter to skip)

osaurus-url-prompt = Osaurus server endpoint URL
osaurus-using = Using Osaurus server endpoint: {$url}
osaurus-key-info = No API key needed unless your Osaurus server requires authentication.
osaurus-key-prompt = API key for Osaurus server (or Enter to skip)

## ── Step 2: Gemini OAuth + API key sub-flow ──────────────────────

gemini-cli-detected = Gemini CLI credentials detected! You can skip the API key.
gemini-cli-reuse-info = Construct will reuse your existing Gemini CLI authentication.
gemini-cli-confirm = Use existing Gemini CLI authentication?
gemini-cli-using = Using Gemini CLI OAuth tokens
gemini-key-url-info = Get your API key at: https://aistudio.google.com/app/apikey
gemini-key-prompt = Paste your Gemini API key
gemini-env-detected = GEMINI_API_KEY environment variable detected!
gemini-cli-fallback-info = Or run `gemini` CLI to authenticate (tokens will be reused).
gemini-key-prompt-optional = Paste your Gemini API key (or press Enter to skip)

## ── Step 2: Anthropic OAuth + API key sub-flow ───────────────────

anthropic-oauth-detected = ANTHROPIC_OAUTH_TOKEN environment variable detected!
anthropic-key-detected = ANTHROPIC_API_KEY environment variable detected!
anthropic-key-url-info = Get your API key at: {$url}
anthropic-setup-token-info = Or run `claude setup-token` to get an OAuth setup-token.
anthropic-key-prompt = Paste your API key or setup-token (or press Enter to skip)
anthropic-skipped = Skipped. Set {$env_oauth} or {$env_key} or edit config.toml later.

## ── Step 2: Qwen OAuth sub-flow ──────────────────────────────────

qwen-oauth-detected = QWEN_OAUTH_TOKEN environment variable detected!
qwen-oauth-creds-info = Qwen Code OAuth credentials are usually stored in ~/.qwen/oauth_creds.json.
qwen-oauth-run-cli = Run `qwen` once and complete OAuth login to populate cached credentials.
qwen-oauth-token-info = You can also set QWEN_OAUTH_TOKEN directly.
qwen-oauth-prompt = Paste your Qwen OAuth token (or press Enter to auto-detect cached OAuth)
qwen-oauth-skipped = Using OAuth auto-detection. Set {$env_oauth} and optional {$env_key} if needed.

## ── Step 2: Bedrock sub-flow ─────────────────────────────────────

bedrock-info-1 = Bedrock uses AWS credentials (not a single API key).
bedrock-info-2 = Set {$env_access} and {$env_secret} environment variables.
bedrock-region-info = Optionally set {$env_region} for the region (default: us-east-1).
bedrock-iam-url = Manage IAM credentials at: {$url}

## ── Step 2: Generic API key sub-flow ─────────────────────────────

provider-key-url-info = Get your API key at: {$url}
provider-key-config-info = You can also set it later via env var or config file.
provider-key-skipped = Skipped. Set {$env_var} or edit config.toml later.

## ── Step 2: Model selection ──────────────────────────────────────

model-needs-key-fallback = Remote Ollama live-model refresh needs an API key ({$env_var}); using curated models.
model-cache-found = Found cached models ({$count}) updated {$age} ago.
model-refresh-prompt = Refresh models from provider now?
model-fetch-prompt = Fetch latest models from provider now?
model-fetched-truncated = Fetched {$total} models. Showing first {$shown}.
model-fetched-all = Fetched {$count} live models.
model-no-models-returned = Provider returned no models; using curated list.
model-fetch-failed = Live fetch failed ({$err}); using cached/curated list.
model-cache-stale = Loaded stale cache from {$age} ago.
model-no-key-curated = No API key detected, so using curated model list.
model-tip-add-key = Tip: add an API key and rerun onboarding to fetch live models.
model-source-prompt = Model source

## ── Step 5: Tool mode info + Composio sub-flow ───────────────────

tool-mode-info-1 = Choose how Construct connects to external apps.
tool-mode-info-2 = You can always change this later in config.toml.
composio-title = Composio Setup
composio-subtitle = — 1000+ OAuth integrations (Gmail, Notion, GitHub, Slack, ...)
composio-key-url = Get your API key at: https://app.composio.dev/settings
composio-info = Construct uses Composio as a tool — your core agent stays local.
composio-key-prompt = Composio API key (or Enter to skip)
composio-skipped = Skipped — set composio.api_key in config.toml later
composio-confirmed = Composio: {$value} (1000+ OAuth tools available)

secrets-info-1 = Construct can encrypt API keys stored in config.toml.
secrets-info-2 = A local key file protects against plaintext exposure and accidental leaks.
secrets-status-encrypted = Secrets: {$value} — keys encrypted with local key file
secrets-status-plaintext = Secrets: {$value} — keys stored as plaintext (not recommended)

## ── Step 6: Hardware setup details ───────────────────────────────

hardware-info-1 = Construct can talk to physical hardware (LEDs, sensors, motors).
hardware-scanning = Scanning for connected devices...
hardware-no-devices = No connected devices detected.
hardware-enable-later = You can enable hardware later in config.toml under [hardware].
hardware-devices-found = {$count} device(s) found:

hardware-mode-native = 🚀 Native — direct GPIO on this Linux board (Raspberry Pi, Orange Pi, etc.)
hardware-mode-tethered = 🔌 Tethered — control an Arduino/ESP32/Nucleo plugged into USB
hardware-mode-debug-probe = 🔬 Debug Probe — flash/read MCUs via SWD/JTAG (probe-rs)
hardware-mode-software = ☁️  Software Only — no hardware access (default)

hardware-multiple-serial = Multiple serial devices found — select one
hardware-serial-port-prompt = Serial port path (e.g. /dev/ttyUSB0)
hardware-baud-rate-prompt = Serial baud rate
hardware-baud-default = 115200 (default, recommended)
hardware-baud-legacy = 9600 (legacy Arduino)
hardware-baud-custom = Custom
hardware-baud-custom-prompt = Custom baud rate
hardware-mcu-prompt = Target MCU chip (e.g. STM32F411CEUx, nRF52840_xxAA)
hardware-rag-prompt = Enable datasheet RAG? (index PDF schematics for AI pin lookups)
hardware-status-with-rag = Hardware: {$mode} | datasheets: {$rag}
hardware-status = Hardware: {$mode}

## ── Step 8: Project context details ──────────────────────────────

ctx-info-personalize = Let's personalize your agent. You can always update these later.
ctx-info-defaults = Press Enter to accept defaults.

ctx-tz-us-eastern = US Eastern (America/New_York)
ctx-tz-us-central = US Central (America/Chicago)
ctx-tz-us-mountain = US Mountain (America/Denver)
ctx-tz-us-pacific = US Pacific (America/Los_Angeles)
ctx-tz-eu-london = Europe/London
ctx-tz-eu-berlin = Europe/Berlin
ctx-tz-asia-tokyo = Asia/Tokyo
ctx-tz-asia-seoul = Asia/Seoul
ctx-tz-utc = UTC
ctx-tz-other = Other (enter manually)

ctx-style-direct = Direct — terse, no fluff
ctx-style-friendly = Friendly — warm, conversational
ctx-style-professional = Professional — formal, precise
ctx-style-expressive = Expressive — colorful, opinionated
ctx-style-technical = Technical — engineer-to-engineer, code-heavy
ctx-style-balanced = Balanced — middle of the road
ctx-style-custom = Custom — describe your own

## ── Step 4: Tunnel setup details ─────────────────────────────────

tunnel-info-1 = A tunnel exposes your gateway to the internet securely.
tunnel-info-2 = Skip this if you only use CLI or local channels.
tunnel-option-skip = Skip — local only (default)
tunnel-option-cloudflare = Cloudflare Tunnel — Zero Trust, free tier
tunnel-option-tailscale = Tailscale — private tailnet or public Funnel
tunnel-option-ngrok = ngrok — instant public URLs
tunnel-option-custom = Custom — bring your own (bore, frp, ssh, etc.)

cloudflare-token-info = Get your tunnel token from the Cloudflare Zero Trust dashboard.
cloudflare-token-prompt = Cloudflare tunnel token

tailscale-info = Tailscale must be installed and authenticated (tailscale up).
tailscale-funnel-prompt = Use Funnel (public internet)? No = tailnet only

ngrok-token-info = Get your auth token at https://dashboard.ngrok.com/get-started/your-authtoken
ngrok-token-prompt = ngrok auth token
ngrok-domain-prompt = Custom domain (optional, Enter to skip)

custom-tunnel-info-1 = Enter the command to start your tunnel.
custom-tunnel-info-2 = Use {"{port}"} and {"{host}"} as placeholders.
custom-tunnel-info-3 = Example: bore local {"{port}"} --to bore.pub
custom-tunnel-cmd-prompt = Start command

## ── Final "Next steps" detail action items ───────────────────────
# Three flavors based on auth model: keyless local servers, OAuth/device
# flow providers, and providers that need an env-var API key.

next-action-chat = Chat:
next-action-gateway = Gateway:
next-action-status = Status:
next-action-login = Login:
next-action-set-key = Set your API key:
next-action-or-edit = Or edit:

next-cmd-chat-hello = construct agent -m "Hello!"
next-cmd-gateway = construct gateway
next-cmd-status = construct status
next-cmd-login = construct auth login --provider {$provider}
next-cmd-export-key = export {$env_var}="sk-..."
next-cmd-config-toml = ~/.construct/config.toml

## ── Step 9: builtin workflows scaffolding ────────────────────────

workflows-available = {$count} built-in workflows available
workflows-destination = Destination: {$path}
workflows-wrote = Wrote {$count} new files
workflows-overwrote = Overwrote {$count} files
workflows-skipped = Skipped {$count} existing files (run with --force to overwrite)
workflows-summary = {$count} built-in workflows

# ════════════════════════════════════════════════════════════════════
# PHASE 3 — channel setup walkthroughs (all 16 channels)
# ════════════════════════════════════════════════════════════════════

## ── Channels: shared strings ──────────────────────────────────────

channels-info-1 = Channels let you talk to Construct from anywhere.
channels-info-2 = CLI is always available. Connect more channels now.
channels-summary = Channels: {$active}

# Shared transient/error states reused across most channel branches.
channel-skipped = Skipped
channel-testing = Testing connection...
channel-conn-failed-token = Connection failed — check your token and try again
channel-conn-failed-creds = Connection failed — check your credentials

## ── Telegram ──────────────────────────────────────────────────────

telegram-title = Telegram Setup
telegram-subtitle = talk to Construct from Telegram
telegram-step-1 = 1. Open Telegram and message @BotFather
telegram-step-2 = 2. Send /newbot and follow the prompts
telegram-step-3 = 3. Copy the bot token and paste it below
telegram-token-prompt = Bot token (from @BotFather)
telegram-connected = Connected as @{$bot_name}
telegram-allowlist-info-1 = Allowlist your own Telegram identity first (recommended for secure + fast setup).
telegram-allowlist-info-2 = Use your @username without '@' (example: yourname), or your numeric Telegram user ID.
telegram-allowlist-info-3 = Use '*' only for temporary open testing.
telegram-allowlist-prompt = Allowed Telegram identities (comma-separated: username without '@' and/or numeric user ID, '*' for all)
telegram-allowlist-warn = No users allowlisted — Telegram inbound messages will be denied until you add your username/user ID or '*'.

## ── Discord ───────────────────────────────────────────────────────

discord-title = Discord Setup
discord-subtitle = talk to Construct from Discord
discord-step-1 = 1. Go to https://discord.com/developers/applications
discord-step-2 = 2. Create a New Application → Bot → Copy token
discord-step-3 = 3. Enable MESSAGE CONTENT intent under Bot settings
discord-step-4 = 4. Invite bot to your server with messages permission
discord-token-prompt = Bot token
discord-connected = Connected as {$bot_name}
discord-guild-prompt = Server (guild) ID (optional, Enter to skip)
discord-allowlist-info-1 = Allowlist your own Discord user ID first (recommended).
discord-allowlist-info-2 = Get it in Discord: Settings → Advanced → Developer Mode (ON), then right-click your profile → Copy User ID.
discord-allowlist-info-3 = Use '*' only for temporary open testing.
discord-allowlist-prompt = Allowed Discord user IDs (comma-separated, recommended: your own ID, '*' for all)
discord-allowlist-warn = No users allowlisted — Discord inbound messages will be denied until you add IDs or '*'.

## ── Slack ─────────────────────────────────────────────────────────

slack-title = Slack Setup
slack-subtitle = talk to Construct from Slack
slack-step-1 = 1. Go to https://api.slack.com/apps → Create New App
slack-step-2 = 2. Add Bot Token Scopes: chat:write, channels:history
slack-step-3 = 3. Install to workspace and copy the Bot Token
slack-token-prompt = Bot token (xoxb-...)
slack-connected = Connected to workspace: {$team}
slack-error = Slack error: {$err}
slack-conn-failed = Connection failed — check your token
slack-app-token-prompt = App token (xapp-..., optional, Enter to skip)
slack-channel-prompt = Default channel ID (optional, Enter to skip for all accessible channels; '*' also means all)
slack-allowlist-info-1 = Allowlist your own Slack member ID first (recommended).
slack-allowlist-info-2 = Member IDs usually start with 'U' (open your Slack profile → More → Copy member ID).
slack-allowlist-info-3 = Use '*' only for temporary open testing.
slack-allowlist-prompt = Allowed Slack user IDs (comma-separated, recommended: your own member ID, '*' for all)
slack-allowlist-warn = No users allowlisted — Slack inbound messages will be denied until you add IDs or '*'.

## ── iMessage ──────────────────────────────────────────────────────

imessage-title = iMessage Setup
imessage-subtitle = macOS only, reads from Messages.app
imessage-macos-only = iMessage is only available on macOS.
imessage-info-1 = Construct reads your iMessage database and replies via AppleScript.
imessage-info-2 = You need to grant Full Disk Access to your terminal in System Settings.
imessage-contacts-prompt = Allowed contacts (comma-separated phone/email, or * for all)
imessage-configured = iMessage configured (contacts: {$contacts})

## ── Matrix ────────────────────────────────────────────────────────

matrix-title = Matrix Setup
matrix-subtitle = self-hosted, federated chat
matrix-info-1 = You need a Matrix account and an access token.
matrix-info-2 = Get a token via Element → Settings → Help & About → Access Token.
matrix-homeserver-prompt = Homeserver URL (e.g. https://matrix.org)
matrix-token-prompt = Access token
matrix-conn-verified = Connection verified
matrix-device-id-warn = Homeserver did not return device_id from whoami. If E2EE decryption fails, set channels.matrix.device_id manually in config.toml.
matrix-conn-failed = Connection failed — check homeserver URL and token
matrix-room-prompt = Room ID (e.g. !abc123:matrix.org)
matrix-allowlist-prompt = Allowed users (comma-separated @user:server, or * for all)
matrix-recovery-prompt = E2EE recovery key (or Enter to skip — see docs/security/matrix-e2ee-guide.md section 4G)

## ── Signal ────────────────────────────────────────────────────────

signal-title = Signal Setup
signal-subtitle = signal-cli daemon bridge
signal-step-1 = 1. Run signal-cli daemon with HTTP enabled (default port 8686).
signal-step-2 = 2. Ensure your Signal account is registered in signal-cli.
signal-step-3 = 3. Optionally scope to DMs only or to a specific group.
signal-url-prompt = signal-cli HTTP URL
signal-url-required = Skipped — HTTP URL required
signal-account-prompt = Account number (E.164, e.g. +1234567890)
signal-account-required = Skipped — account number required
signal-scope-all = All messages (DMs + groups)
signal-scope-dm = DM only
signal-scope-group = Specific group ID
signal-scope-prompt = Message scope
signal-group-prompt = Group ID
signal-group-required = Skipped — group ID required
signal-allowlist-prompt = Allowed sender numbers (comma-separated +1234567890, or * for all)
signal-ignore-attachments = Ignore attachment-only messages?
signal-ignore-stories = Ignore incoming stories?
signal-configured = Signal configured

## ── WhatsApp (Web + Cloud API) ───────────────────────────────────

whatsapp-title = WhatsApp Setup
whatsapp-mode-web = WhatsApp Web (QR / pair-code, no Meta Business API)
whatsapp-mode-cloud = WhatsApp Business Cloud API (webhook)
whatsapp-mode-prompt = Choose WhatsApp mode

# WhatsApp Web mode
whatsapp-web-feature-warn = The 'whatsapp-web' feature is not compiled in. WhatsApp Web will not work at runtime.
whatsapp-web-rebuild-info = Rebuild with: cargo build --features whatsapp-web
whatsapp-web-mode-label = Mode: WhatsApp Web
whatsapp-web-step-1 = 1. Build with --features whatsapp-web
whatsapp-web-step-2 = 2. Start channel/daemon and scan QR in WhatsApp → Linked Devices
whatsapp-web-step-3 = 3. Keep session_path persistent so relogin is not required
whatsapp-web-session-prompt = Session database path
whatsapp-web-session-required = Skipped — session path required
whatsapp-web-pair-phone-prompt = Pair phone (optional, digits only; leave empty to use QR flow)
whatsapp-web-pair-code-prompt = Custom pair code (optional, leave empty for auto-generated)
whatsapp-web-allowlist-prompt = Allowed phone numbers (comma-separated +1234567890, or * for all)
whatsapp-web-configured = WhatsApp Web configuration saved.

# WhatsApp Cloud API mode
whatsapp-cloud-mode-label = Mode: Business Cloud API
whatsapp-cloud-step-1 = 1. Go to developers.facebook.com and create a WhatsApp app
whatsapp-cloud-step-2 = 2. Add the WhatsApp product and get your phone number ID
whatsapp-cloud-step-3 = 3. Generate a temporary access token (System User)
whatsapp-cloud-step-4 = 4. Configure webhook URL to: https://your-domain/whatsapp
whatsapp-cloud-token-prompt = Access token (from Meta Developers)
whatsapp-cloud-phone-id-prompt = Phone number ID (from WhatsApp app settings)
whatsapp-cloud-phone-id-required = Skipped — phone number ID required
whatsapp-cloud-verify-token-prompt = Webhook verify token (create your own)
whatsapp-cloud-connected = Connected to WhatsApp API
whatsapp-cloud-conn-failed = Connection failed — check access token and phone number ID
whatsapp-cloud-allowlist-prompt = Allowed phone numbers (comma-separated +1234567890, or * for all)

## ── Linq ──────────────────────────────────────────────────────────

linq-title = Linq Setup
linq-subtitle = iMessage/RCS/SMS via Linq API
linq-step-1 = 1. Sign up at linqapp.com and get your Partner API token
linq-step-2 = 2. Note your Linq phone number (E.164 format)
linq-step-3 = 3. Configure webhook URL to: https://your-domain/linq
linq-token-prompt = API token (Linq Partner API token)
linq-phone-prompt = From phone number (E.164 format, e.g. +12223334444)
linq-phone-required = Skipped — phone number required
linq-connected = Connected to Linq API
linq-conn-failed = Connection failed — check API token
linq-allowlist-prompt = Allowed sender numbers (comma-separated +1234567890, or * for all)
linq-secret-prompt = Webhook signing secret (optional, press Enter to skip)

## ── IRC ───────────────────────────────────────────────────────────

irc-title = IRC Setup
irc-subtitle = IRC over TLS
irc-info-1 = IRC connects over TLS to any IRC server
irc-info-2 = Supports SASL PLAIN and NickServ authentication
irc-server-prompt = IRC server (hostname)
irc-port-prompt = Port
irc-port-invalid = Invalid port, using 6697
irc-nick-prompt = Bot nickname
irc-nick-required = Skipped — nickname required
irc-channels-prompt = Channels to join (comma-separated: #channel1,#channel2)
irc-allowlist-info-1 = Allowlist nicknames that can interact with the bot (case-insensitive).
irc-allowlist-info-2 = Use '*' to allow anyone (not recommended for production).
irc-allowlist-prompt = Allowed nicknames (comma-separated, or * for all)
irc-allowlist-empty = ⚠️  Empty allowlist — only you can interact. Add nicknames above.
irc-auth-info = Optional authentication (press Enter to skip each):
irc-server-pass-prompt = Server password (for bouncers like ZNC, leave empty if none)
irc-nickserv-pass-prompt = NickServ password (leave empty if none)
irc-sasl-pass-prompt = SASL PLAIN password (leave empty if none)
irc-tls-verify-prompt = Verify TLS certificate?
irc-configured = IRC configured as {$nick}@{$server}:{$port}

## ── Webhook ───────────────────────────────────────────────────────

webhook-title = Webhook Setup
webhook-subtitle = HTTP endpoint for custom integrations
webhook-port-prompt = Port
webhook-secret-prompt = Secret (optional, Enter to skip)
webhook-configured = Webhook on port {$port}

## ── Nextcloud Talk ───────────────────────────────────────────────

nctalk-title = Nextcloud Talk Setup
nctalk-subtitle = Talk webhook receive + OCS API send
nctalk-step-1 = 1. Configure your Nextcloud Talk bot app and app token.
nctalk-step-2 = 2. Set webhook URL to: https://<your-public-url>/nextcloud-talk
nctalk-step-3 = 3. Keep webhook_secret aligned with Nextcloud signature headers if enabled.
nctalk-base-url-prompt = Nextcloud base URL (e.g. https://cloud.example.com)
nctalk-base-url-required = Skipped — base URL required
nctalk-token-prompt = App token (Talk bot token)
nctalk-token-required = Skipped — app token required
nctalk-secret-prompt = Webhook secret (optional, Enter to skip)
nctalk-allowlist-prompt = Allowed Nextcloud actor IDs (comma-separated, or * for all)
nctalk-configured = Nextcloud Talk configured

## ── DingTalk ──────────────────────────────────────────────────────

dingtalk-title = DingTalk Setup
dingtalk-subtitle = DingTalk Stream Mode
dingtalk-step-1 = 1. Go to DingTalk developer console (open.dingtalk.com)
dingtalk-step-2 = 2. Create an app and enable the Stream Mode bot
dingtalk-step-3 = 3. Copy the Client ID (AppKey) and Client Secret (AppSecret)
dingtalk-client-id-prompt = Client ID (AppKey)
dingtalk-client-secret-prompt = Client Secret (AppSecret)
dingtalk-verified = DingTalk credentials verified
dingtalk-allowlist-prompt = Allowed staff IDs (comma-separated, '*' for all)

## ── QQ Official ───────────────────────────────────────────────────

qq-title = QQ Official Setup
qq-subtitle = Tencent QQ Bot SDK
qq-step-1 = 1. Go to QQ Bot developer console (q.qq.com)
qq-step-2 = 2. Create a bot application
qq-step-3 = 3. Copy the App ID and App Secret
qq-app-id-prompt = App ID
qq-app-secret-prompt = App Secret
qq-verified = QQ Bot credentials verified
qq-auth-failed = Auth error — check your credentials
qq-allowlist-prompt = Allowed user IDs (comma-separated, '*' for all)

## ── Lark / Feishu ────────────────────────────────────────────────

lark-title = {$provider} Setup
lark-subtitle = talk to Construct from {$provider}
lark-step-1 = 1. Go to {$provider} Open Platform ({$host})
lark-step-2 = 2. Create an app and enable 'Bot' capability
lark-step-3 = 3. Copy the App ID and App Secret
lark-app-id-prompt = App ID
lark-app-secret-prompt = App Secret
lark-app-secret-required = App Secret is required
lark-verified = {$provider} credentials verified
lark-receive-mode-prompt = Receive Mode
lark-receive-mode-ws = WebSocket (recommended, no public IP needed)
lark-receive-mode-webhook = Webhook (requires public HTTPS endpoint)
lark-verify-token-prompt = Verification Token (optional, for Webhook mode)
lark-verify-token-empty = Verification Token is empty — webhook authenticity checks are reduced.
lark-webhook-port-prompt = Webhook Port
lark-allowlist-prompt = Allowed user Open IDs (comma-separated, '*' for all)
lark-allowlist-warn = No users allowlisted — {$provider} inbound messages will be denied until you add Open IDs or '*'.

## ── Nostr ─────────────────────────────────────────────────────────

nostr-title = Nostr Setup
nostr-subtitle = private messages via NIP-04 & NIP-17
nostr-info-1 = Construct will listen for encrypted DMs on Nostr relays.
nostr-info-2 = You need a Nostr private key (hex or nsec) and at least one relay.
nostr-key-prompt = Private key (hex or nsec1...)
nostr-key-valid = Key valid — public key: {$pubkey}
nostr-key-invalid = Invalid private key — check format and try again
nostr-relays-prompt = Relay URLs (comma-separated, Enter for defaults)
nostr-allowlist-info-1 = Allowlist pubkeys that can message the bot (hex or npub).
nostr-allowlist-info-2 = Use '*' to allow anyone (not recommended for production).
nostr-allowlist-prompt = Allowed pubkeys (comma-separated, or * for all)
nostr-allowlist-warn = No pubkeys allowlisted — inbound messages will be denied until you add pubkeys or '*'.
nostr-configured = Nostr configured with {$relay_count} relay(s)
