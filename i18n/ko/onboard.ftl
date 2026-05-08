# Construct onboard 마법사 — 한국어 번역.
#
# 키 이름은 i18n/en/onboard.ftl 영문 원본과 동일합니다. {$name} 자리표시자도
# 그대로 두세요. 영문에 키가 추가되면 이 파일에도 추가해야 합니다 — 누락 키는
# 자동으로 영문으로 폴백되어 사용자에게 영어 문장이 섞여 보일 수 있습니다.
#
# 톤 가이드:
# - 본문은 -ㅂ니다체로, 안내문은 짧고 직접적으로.
# - "Construct"는 영문 그대로 (제품명).
# - "에이전트", "워크스페이스", "프로바이더" 같은 용어는 한국어 IT 업계
#   표기를 따름. 외래어가 더 자연스러우면 그대로 사용.
# - 명령어/경로/URL/ENV 변수명은 절대 번역하지 마세요.

## ── 배너 / 환영 메시지 ──────────────────────────────────────────

welcome-title = Construct에 오신 것을 환영합니다.
welcome-subtitle = 이 마법사가 60초 안에 에이전트 설정을 끝냅니다.

## ── 언어 선택 (Step 0) ─────────────────────────────────────────

step-language-prompt = Select your language / 언어를 선택하세요
step-language-saved = 언어를 {$lang}(으)로 설정했습니다.

## ── 단계 헤더 ───────────────────────────────────────────────────

step-header = {$total}단계 중 {$num}단계: {$title}

step-1-title = 워크스페이스 설정
step-2-title = AI 프로바이더 및 API 키
step-3-title = 채널 (Construct와의 대화 통로)
step-4-title = 터널 (외부 노출)
step-5-title = 도구 모드 및 보안
step-6-title = 하드웨어 (물리 세계 연결)
step-7-title = 메모리 구성
step-8-title = 프로젝트 컨텍스트 (에이전트 개인화)
step-9-title = 워크스페이스 파일

## ── 1단계: 워크스페이스 ────────────────────────────────────────

workspace-default-location = 기본 위치: {$path}
workspace-use-default = 기본 워크스페이스 위치를 사용할까요?
workspace-enter-path = 워크스페이스 경로를 입력하세요
workspace-confirmed = ✓ 워크스페이스: {$path}

## ── 2단계: 프로바이더 ──────────────────────────────────────────

provider-select-tier = 프로바이더 카테고리를 선택하세요
provider-tier-recommended = 추천 (Anthropic, OpenAI, Google) — 일반 사용에 가장 안정적
provider-tier-fast = 빠른 / 저비용 (Groq, Cerebras, OpenRouter) — 응답 속도와 비용 우선
provider-tier-gateway = 게이트웨이 / 멀티 프로바이더 (OpenRouter, LiteLLM) — 한 키로 여러 모델
provider-tier-specialized = 전문 (Cohere, Mistral, Together) — 특정 영역 강점
provider-tier-local = 로컬 (Ollama, llama.cpp, LM Studio, vLLM, SGLang) — 인터넷 없이 자체 호스팅
provider-tier-custom = 커스텀 (OpenAI 호환 엔드포인트) — 직접 운영하는 API

provider-select = AI 프로바이더를 선택하세요
provider-api-key-prompt = API 키를 붙여넣으세요 (없으면 Enter로 건너뛰기)
provider-api-base-prompt = API 엔드포인트 URL (예: http://localhost:1234 — Construct가 LLM 요청을 보낼 주소)
provider-api-key-optional = API 키 (필요 없으면 Enter)
provider-model-name = 모델 이름 (예: llama3, gpt-4o, mistral)
provider-select-model = 기본 모델을 선택하세요
provider-enter-custom-model = 커스텀 모델 ID를 입력하세요

## ── 3단계: 채널 ─────────────────────────────────────────────────

channels-prompt = 연결할 채널을 선택하세요 (완료하려면 Done)

## ── 4단계: 터널 ─────────────────────────────────────────────────

tunnel-select = 터널 프로바이더를 선택하세요

## ── 5단계: 도구 모드 및 시크릿 ─────────────────────────────────

tool-mode-select = 도구 모드를 선택하세요
secrets-encrypt = 암호화된 시크릿 저장소를 사용할까요? (API 키를 평문 대신 암호화해 저장)

## ── 6단계: 하드웨어 ────────────────────────────────────────────

hardware-prompt = Construct가 어떻게 물리 세계와 상호작용할까요? (시리얼 장치, 없음 등)

## ── 7단계: 메모리 ──────────────────────────────────────────────

memory-select = 메모리 백엔드를 선택하세요
memory-kumiho-api-url = Kumiho API URL (기본값을 그대로 두면 api.kumiho.cloud)
memory-kumiho-token = Kumiho 서비스 토큰 (KUMIHO_SERVICE_TOKEN — 대시보드 → Service Tokens에서 발급)
memory-auto-save = 대화를 메모리에 자동 저장할까요?

dreamstate-prompt = 야간 DreamState 메모리 통합을 설정할까요? (권장 — 매일 밤 자동으로 메모리를 정리·연결합니다)
dreamstate-time-prompt = DreamState를 몇 시에 실행할까요?
dreamstate-cron-created = ✓ DreamState 크론 작업 등록 완료 (스케줄: {$time}, 다음 실행: {$next})
dreamstate-cron-failed = DreamState 크론 작업 등록에 실패했습니다: {$err}

## ── 8단계: 프로젝트 컨텍스트 ───────────────────────────────────

ctx-your-name = 사용자 이름
ctx-timezone = 타임존
ctx-timezone-enter = 타임존을 입력하세요 (예: Asia/Seoul)
ctx-agent-name = 에이전트 이름
ctx-comm-style = 커뮤니케이션 스타일
ctx-comm-style-custom = 커스텀 커뮤니케이션 스타일

## ── 기존 설정 처리 ────────────────────────────────────────────

existing-config-found = {$path}에 기존 설정이 있습니다. 진행 모드를 선택하세요
existing-config-detected-force = ! {$path}에 기존 설정이 감지되었습니다. --force 플래그로 강제 진행합니다.
existing-config-overwrite-prompt = {$path}에 기존 설정이 있습니다. 다시 온보딩하면 config.toml을 덮어씁니다. 계속할까요?

setup-mode-full = 전체 온보딩 (config.toml 덮어쓰기)
setup-mode-update-provider = AI 프로바이더 / 모델 / API 키만 업데이트
setup-mode-cancel = 취소

## ── --reinit 플로우 ────────────────────────────────────────────

reinit-banner = ⚠️  Construct 설정을 초기화합니다...
reinit-current-dir = 현재 설정 디렉토리: {$path}
reinit-backup-target = 기존 설정을 다음 위치에 백업합니다: {$path}
reinit-confirm = 계속할까요? [y/N]
reinit-aborted = 중단되었습니다.
reinit-backup-ok = 백업이 정상적으로 생성되었습니다.
reinit-fresh-start = 새 설정으로 초기화를 시작합니다...

## ── 마무리 "다음 단계" 블록 ────────────────────────────────────

next-steps-header = 다음 단계:
next-step-chat = 에이전트와 대화 시작:
next-step-chat-cmd = construct agent
next-step-gateway = 게이트웨이 실행 (채널, 대시보드):
next-step-gateway-cmd = construct gateway start
next-step-status = 상태 확인:
next-step-status-cmd = construct status
next-step-pairing-enabled = 페어링이 활성화되었습니다. 게이트웨이가 시작되면 일회성 페어링 코드가 표시됩니다.
next-step-dashboard = 대시보드: http://127.0.0.1:{$port}

## ── 에러 / 기타 ────────────────────────────────────────────────

err-no-command = 명령어가 입력되지 않았습니다.
err-try-onboard = 워크스페이스를 초기화하려면 `construct onboard`를 실행하세요.

# ════════════════════════════════════════════════════════════════════
# PHASE 2 — 프로바이더 서브플로우, 하드웨어/프로젝트/터널 상세, 등
# ════════════════════════════════════════════════════════════════════

## ── 2단계: 커스텀 프로바이더 서브플로우 ──────────────────────────

custom-provider-title = 커스텀 프로바이더 설정
custom-provider-subtitle = — OpenAI 호환 API라면 무엇이든
custom-provider-info-1 = OpenAI chat completions 형식을 지원하는 API라면 모두 연결할 수 있습니다.
custom-provider-info-2 = 예: LiteLLM, LocalAI, vLLM, text-generation-webui, LM Studio 등.
custom-provider-confirmed = ✓ 프로바이더: {$provider} | 모델: {$model}

## ── 2단계: 원격 Ollama 서브플로우 ────────────────────────────────

ollama-use-remote = 원격 Ollama 엔드포인트를 사용하시겠습니까? (예: Ollama Cloud)
ollama-remote-url-prompt = 원격 Ollama 엔드포인트 URL
ollama-remote-configured = 원격 엔드포인트 설정됨: {$url}
ollama-normalized-base = 엔드포인트를 베이스 URL로 정규화했습니다 (끝부분의 /api 제거).
ollama-cloud-suffix-hint = 클라우드 전용 모델을 사용하면 모델 ID 뒤에 {$suffix}를 붙이세요.
ollama-remote-key-prompt = 원격 Ollama 엔드포인트 API 키 (없으면 Enter)
ollama-no-key-hint = API 키 미설정. 엔드포인트가 인증을 요구하면 나중에 {$env_var}를 설정하세요.
ollama-using-local = 로컬 Ollama 사용 (http://localhost:11434, API 키 불필요).

## ── 2단계: llama.cpp / SGLang / vLLM / Osaurus 서브플로우 ─────────

llamacpp-url-prompt = llama.cpp 서버 엔드포인트 URL
llamacpp-using = llama.cpp 서버 엔드포인트 사용: {$url}
llamacpp-key-info = llama.cpp 서버를 --api-key로 실행한 경우에만 키가 필요합니다.
llamacpp-key-prompt = llama.cpp 서버 API 키 (없으면 Enter)
local-server-no-key-hint = API 키 미설정. 서버가 인증을 요구할 때만 나중에 {$env_var}를 설정하세요.

sglang-url-prompt = SGLang 서버 엔드포인트 URL
sglang-using = SGLang 서버 엔드포인트 사용: {$url}
sglang-key-info = SGLang 서버가 인증을 요구하는 경우에만 키가 필요합니다.
sglang-key-prompt = SGLang 서버 API 키 (없으면 Enter)

vllm-url-prompt = vLLM 서버 엔드포인트 URL
vllm-using = vLLM 서버 엔드포인트 사용: {$url}
vllm-key-info = vLLM 서버가 인증을 요구하는 경우에만 키가 필요합니다.
vllm-key-prompt = vLLM 서버 API 키 (없으면 Enter)

osaurus-url-prompt = Osaurus 서버 엔드포인트 URL
osaurus-using = Osaurus 서버 엔드포인트 사용: {$url}
osaurus-key-info = Osaurus 서버가 인증을 요구하는 경우에만 키가 필요합니다.
osaurus-key-prompt = Osaurus 서버 API 키 (없으면 Enter)

## ── 2단계: Gemini OAuth + API 키 서브플로우 ──────────────────────

gemini-cli-detected = Gemini CLI 자격증명이 감지되었습니다! API 키를 건너뛸 수 있습니다.
gemini-cli-reuse-info = Construct가 기존 Gemini CLI 인증을 재사용합니다.
gemini-cli-confirm = 기존 Gemini CLI 인증을 사용하시겠습니까?
gemini-cli-using = Gemini CLI OAuth 토큰 사용
gemini-key-url-info = API 키 발급: https://aistudio.google.com/app/apikey
gemini-key-prompt = Gemini API 키를 붙여넣으세요
gemini-env-detected = GEMINI_API_KEY 환경변수가 감지되었습니다!
gemini-cli-fallback-info = 또는 `gemini` CLI를 실행해 인증하면 토큰이 재사용됩니다.
gemini-key-prompt-optional = Gemini API 키를 붙여넣으세요 (없으면 Enter로 건너뛰기)

## ── 2단계: Anthropic OAuth + API 키 서브플로우 ───────────────────

anthropic-oauth-detected = ANTHROPIC_OAUTH_TOKEN 환경변수가 감지되었습니다!
anthropic-key-detected = ANTHROPIC_API_KEY 환경변수가 감지되었습니다!
anthropic-key-url-info = API 키 발급: {$url}
anthropic-setup-token-info = 또는 `claude setup-token`을 실행하면 OAuth setup-token을 받을 수 있습니다.
anthropic-key-prompt = API 키 또는 setup-token을 붙여넣으세요 (없으면 Enter로 건너뛰기)
anthropic-skipped = 건너뜀. 나중에 {$env_oauth} 또는 {$env_key}를 설정하거나 config.toml을 편집하세요.

## ── 2단계: Qwen OAuth 서브플로우 ─────────────────────────────────

qwen-oauth-detected = QWEN_OAUTH_TOKEN 환경변수가 감지되었습니다!
qwen-oauth-creds-info = Qwen Code OAuth 자격증명은 보통 ~/.qwen/oauth_creds.json에 저장됩니다.
qwen-oauth-run-cli = `qwen`을 한 번 실행해 OAuth 로그인을 마치면 캐시된 자격증명이 만들어집니다.
qwen-oauth-token-info = QWEN_OAUTH_TOKEN을 직접 설정해도 됩니다.
qwen-oauth-prompt = Qwen OAuth 토큰을 붙여넣으세요 (없으면 Enter로 캐시된 OAuth 자동 감지)
qwen-oauth-skipped = OAuth 자동 감지를 사용합니다. 필요시 {$env_oauth}와 선택적 {$env_key}를 설정하세요.

## ── 2단계: Bedrock 서브플로우 ────────────────────────────────────

bedrock-info-1 = Bedrock은 단일 API 키가 아니라 AWS 자격증명을 사용합니다.
bedrock-info-2 = {$env_access}와 {$env_secret} 환경변수를 설정하세요.
bedrock-region-info = 선택적으로 리전을 위해 {$env_region}을 설정하세요 (기본: us-east-1).
bedrock-iam-url = IAM 자격증명 관리: {$url}

## ── 2단계: 일반 API 키 서브플로우 ────────────────────────────────

provider-key-url-info = API 키 발급: {$url}
provider-key-config-info = 환경변수나 config 파일을 통해 나중에 설정해도 됩니다.
provider-key-skipped = 건너뜀. 나중에 {$env_var}를 설정하거나 config.toml을 편집하세요.

## ── 2단계: 모델 선택 ──────────────────────────────────────────────

model-needs-key-fallback = 원격 Ollama 라이브 모델 새로고침에는 API 키({$env_var})가 필요합니다. 큐레이션 모델 목록을 사용합니다.
model-cache-found = 캐시된 모델 ({$count}개)을 찾았습니다. {$age} 전에 갱신됨.
model-refresh-prompt = 지금 프로바이더에서 모델을 새로고침하시겠습니까?
model-fetch-prompt = 지금 프로바이더에서 최신 모델을 가져오시겠습니까?
model-fetched-truncated = 모델 {$total}개를 가져왔습니다. 처음 {$shown}개 표시.
model-fetched-all = 라이브 모델 {$count}개를 가져왔습니다.
model-no-models-returned = 프로바이더가 모델을 반환하지 않아 큐레이션 목록을 사용합니다.
model-fetch-failed = 라이브 가져오기 실패 ({$err}). 캐시 또는 큐레이션 목록을 사용합니다.
model-cache-stale = {$age} 전 만료된 캐시를 로드했습니다.
model-no-key-curated = API 키가 감지되지 않아 큐레이션된 모델 목록을 사용합니다.
model-tip-add-key = 팁: API 키를 추가하고 온보딩을 다시 실행하면 라이브 모델을 가져올 수 있습니다.
model-source-prompt = 모델 소스

## ── 5단계: 도구 모드 안내 + Composio 서브플로우 ─────────────────

tool-mode-info-1 = Construct가 외부 앱과 어떻게 연결될지 선택하세요.
tool-mode-info-2 = config.toml에서 언제든 변경할 수 있습니다.
composio-title = Composio 설정
composio-subtitle = — 1000개 이상의 OAuth 통합 (Gmail, Notion, GitHub, Slack 등)
composio-key-url = API 키 발급: https://app.composio.dev/settings
composio-info = Construct는 Composio를 도구로 사용합니다 — 핵심 에이전트는 로컬에 유지됩니다.
composio-key-prompt = Composio API 키 (없으면 Enter)
composio-skipped = 건너뜀 — 나중에 config.toml에서 composio.api_key를 설정하세요
composio-confirmed = Composio: {$value} (1000개 이상의 OAuth 도구 사용 가능)

secrets-info-1 = Construct는 config.toml에 저장된 API 키를 암호화할 수 있습니다.
secrets-info-2 = 로컬 키 파일이 평문 노출과 우발적 유출을 막습니다.
secrets-status-encrypted = 시크릿: {$value} — 로컬 키 파일로 암호화됨
secrets-status-plaintext = 시크릿: {$value} — 평문으로 저장됨 (권장하지 않음)

## ── 6단계: 하드웨어 설정 상세 ────────────────────────────────────

hardware-info-1 = Construct는 물리 하드웨어(LED, 센서, 모터 등)와 통신할 수 있습니다.
hardware-scanning = 연결된 장치 검색 중...
hardware-no-devices = 연결된 장치가 감지되지 않았습니다.
hardware-enable-later = config.toml의 [hardware] 섹션에서 나중에 활성화할 수 있습니다.
hardware-devices-found = {$count}개 장치 발견:

hardware-mode-native = 🚀 네이티브 — 이 Linux 보드의 GPIO 직접 제어 (Raspberry Pi, Orange Pi 등)
hardware-mode-tethered = 🔌 테더드 — USB로 연결된 Arduino/ESP32/Nucleo 제어
hardware-mode-debug-probe = 🔬 디버그 프로브 — SWD/JTAG로 MCU 플래시/읽기 (probe-rs)
hardware-mode-software = ☁️  소프트웨어 전용 — 하드웨어 접근 없음 (기본)

hardware-multiple-serial = 시리얼 장치가 여러 개 발견되었습니다 — 하나를 선택하세요
hardware-serial-port-prompt = 시리얼 포트 경로 (예: /dev/ttyUSB0)
hardware-baud-rate-prompt = 시리얼 baud rate
hardware-baud-default = 115200 (기본, 권장)
hardware-baud-legacy = 9600 (구형 Arduino)
hardware-baud-custom = 직접 입력
hardware-baud-custom-prompt = 커스텀 baud rate
hardware-mcu-prompt = 대상 MCU 칩 (예: STM32F411CEUx, nRF52840_xxAA)
hardware-rag-prompt = 데이터시트 RAG를 활성화할까요? (PDF 회로도를 색인하여 AI가 핀 정보를 조회)
hardware-status-with-rag = 하드웨어: {$mode} | 데이터시트: {$rag}
hardware-status = 하드웨어: {$mode}

## ── 8단계: 프로젝트 컨텍스트 상세 ───────────────────────────────

ctx-info-personalize = 에이전트를 개인화해봅시다. 언제든 나중에 변경할 수 있습니다.
ctx-info-defaults = Enter를 눌러 기본값을 사용할 수 있습니다.

ctx-tz-us-eastern = 미국 동부 (America/New_York)
ctx-tz-us-central = 미국 중부 (America/Chicago)
ctx-tz-us-mountain = 미국 산악 (America/Denver)
ctx-tz-us-pacific = 미국 태평양 (America/Los_Angeles)
ctx-tz-eu-london = 유럽/런던
ctx-tz-eu-berlin = 유럽/베를린
ctx-tz-asia-tokyo = 아시아/도쿄
ctx-tz-asia-seoul = 아시아/서울
ctx-tz-utc = UTC
ctx-tz-other = 기타 (직접 입력)

ctx-style-direct = 직접적 — 간결하고 군더더기 없음
ctx-style-friendly = 친근함 — 따뜻하고 대화적
ctx-style-professional = 전문적 — 격식 있고 정확함
ctx-style-expressive = 표현적 — 다채롭고 의견을 가짐
ctx-style-technical = 기술적 — 엔지니어 대 엔지니어, 코드 중심
ctx-style-balanced = 균형 — 중간 톤
ctx-style-custom = 커스텀 — 직접 묘사

## ── 4단계: 터널 설정 상세 ───────────────────────────────────────

tunnel-info-1 = 터널은 게이트웨이를 안전하게 인터넷에 노출시킵니다.
tunnel-info-2 = CLI나 로컬 채널만 사용한다면 건너뛰어도 됩니다.
tunnel-option-skip = 건너뛰기 — 로컬 전용 (기본)
tunnel-option-cloudflare = Cloudflare Tunnel — Zero Trust, 무료 티어
tunnel-option-tailscale = Tailscale — 프라이빗 tailnet 또는 공개 Funnel
tunnel-option-ngrok = ngrok — 즉시 공개 URL 발급
tunnel-option-custom = 커스텀 — 직접 가져오기 (bore, frp, ssh 등)

cloudflare-token-info = Cloudflare Zero Trust 대시보드에서 터널 토큰을 발급받으세요.
cloudflare-token-prompt = Cloudflare 터널 토큰

tailscale-info = Tailscale이 설치되고 인증되어 있어야 합니다 (tailscale up).
tailscale-funnel-prompt = Funnel(공개 인터넷)을 사용할까요? 아니오 = tailnet만 사용

ngrok-token-info = 인증 토큰 발급: https://dashboard.ngrok.com/get-started/your-authtoken
ngrok-token-prompt = ngrok 인증 토큰
ngrok-domain-prompt = 커스텀 도메인 (선택, 없으면 Enter로 건너뛰기)

custom-tunnel-info-1 = 터널을 시작할 명령어를 입력하세요.
custom-tunnel-info-2 = {"{port}"}와 {"{host}"}를 자리표시자로 사용하세요.
custom-tunnel-info-3 = 예: bore local {"{port}"} --to bore.pub
custom-tunnel-cmd-prompt = 시작 명령어

## ── 마무리 "다음 단계" 액션 항목 ────────────────────────────────
# 인증 모델별 세 가지 — 키 없는 로컬 서버, OAuth/디바이스 플로우 프로바이더,
# 환경변수 API 키가 필요한 프로바이더.

next-action-chat = 대화:
next-action-gateway = 게이트웨이:
next-action-status = 상태:
next-action-login = 로그인:
next-action-set-key = API 키 설정:
next-action-or-edit = 또는 편집:

next-cmd-chat-hello = construct agent -m "Hello!"
next-cmd-gateway = construct gateway
next-cmd-status = construct status
next-cmd-login = construct auth login --provider {$provider}
next-cmd-export-key = export {$env_var}="sk-..."
next-cmd-config-toml = ~/.construct/config.toml

## ── 9단계: 내장 워크플로 스캐폴딩 ──────────────────────────────

workflows-available = 내장 워크플로 {$count}개 사용 가능
workflows-destination = 대상 경로: {$path}
workflows-wrote = 새 파일 {$count}개 작성됨
workflows-overwrote = 파일 {$count}개 덮어쓰기됨
workflows-skipped = 기존 파일 {$count}개 건너뜀 (덮어쓰려면 --force 사용)
workflows-summary = 내장 워크플로 {$count}개

# ════════════════════════════════════════════════════════════════════
# PHASE 3 — 채널 설정 워크스루 (16개 채널 전체)
# ════════════════════════════════════════════════════════════════════

## ── 채널: 공통 문자열 ─────────────────────────────────────────────

channels-info-1 = 채널을 통해 어디서든 Construct와 대화할 수 있습니다.
channels-info-2 = CLI는 항상 사용 가능합니다. 추가 채널을 지금 연결해보세요.
channels-summary = 채널: {$active}

# 거의 모든 채널 분기에서 재사용되는 임시/에러 메시지.
channel-skipped = 건너뜀
channel-testing = 연결 테스트 중...
channel-conn-failed-token = 연결 실패 — 토큰을 확인하고 다시 시도하세요
channel-conn-failed-creds = 연결 실패 — 자격증명을 확인하세요

## ── Telegram ──────────────────────────────────────────────────────

telegram-title = Telegram 설정
telegram-subtitle = Telegram에서 Construct와 대화하기
telegram-step-1 = 1. Telegram을 열고 @BotFather에게 메시지를 보내세요
telegram-step-2 = 2. /newbot을 보내고 안내를 따르세요
telegram-step-3 = 3. 봇 토큰을 복사해서 아래에 붙여넣으세요
telegram-token-prompt = 봇 토큰 (@BotFather에서 발급)
telegram-connected = @{$bot_name}으로 연결됨
telegram-allowlist-info-1 = 본인 Telegram ID를 먼저 허용 목록에 추가하세요 (안전하고 빠른 설정 권장).
telegram-allowlist-info-2 = '@'를 뺀 사용자명(예: yourname)이나 숫자 사용자 ID를 사용하세요.
telegram-allowlist-info-3 = 임시 공개 테스트가 아니면 '*'는 사용하지 마세요.
telegram-allowlist-prompt = 허용 Telegram ID (쉼표 구분: '@'를 뺀 사용자명 또는 숫자 사용자 ID, 모두 허용은 '*')
telegram-allowlist-warn = 허용 목록이 비어 있어 Telegram 수신 메시지가 거부됩니다. 사용자명/사용자 ID를 추가하거나 '*'를 입력하세요.

## ── Discord ───────────────────────────────────────────────────────

discord-title = Discord 설정
discord-subtitle = Discord에서 Construct와 대화하기
discord-step-1 = 1. https://discord.com/developers/applications 로 이동
discord-step-2 = 2. New Application → Bot → 토큰 복사
discord-step-3 = 3. Bot 설정에서 MESSAGE CONTENT intent 활성화
discord-step-4 = 4. 메시지 권한과 함께 봇을 서버에 초대
discord-token-prompt = 봇 토큰
discord-connected = {$bot_name}으로 연결됨
discord-guild-prompt = 서버(guild) ID (선택, 없으면 Enter로 건너뛰기)
discord-allowlist-info-1 = 본인 Discord 사용자 ID를 먼저 허용 목록에 추가하세요 (권장).
discord-allowlist-info-2 = Discord에서: 설정 → 고급 → 개발자 모드 ON, 그 다음 본인 프로필 우클릭 → 사용자 ID 복사.
discord-allowlist-info-3 = 임시 공개 테스트가 아니면 '*'는 사용하지 마세요.
discord-allowlist-prompt = 허용 Discord 사용자 ID (쉼표 구분, 본인 ID 권장, 모두 허용은 '*')
discord-allowlist-warn = 허용 목록이 비어 있어 Discord 수신 메시지가 거부됩니다. ID를 추가하거나 '*'를 입력하세요.

## ── Slack ─────────────────────────────────────────────────────────

slack-title = Slack 설정
slack-subtitle = Slack에서 Construct와 대화하기
slack-step-1 = 1. https://api.slack.com/apps → Create New App
slack-step-2 = 2. Bot Token Scopes 추가: chat:write, channels:history
slack-step-3 = 3. 워크스페이스에 설치하고 Bot Token 복사
slack-token-prompt = Bot 토큰 (xoxb-...)
slack-connected = 워크스페이스에 연결됨: {$team}
slack-error = Slack 오류: {$err}
slack-conn-failed = 연결 실패 — 토큰을 확인하세요
slack-app-token-prompt = App 토큰 (xapp-..., 선택, 없으면 Enter)
slack-channel-prompt = 기본 채널 ID (선택, 모든 접근 가능 채널을 쓰려면 Enter; '*'도 모두 허용)
slack-allowlist-info-1 = 본인 Slack 멤버 ID를 먼저 허용 목록에 추가하세요 (권장).
slack-allowlist-info-2 = 멤버 ID는 보통 'U'로 시작합니다 (Slack 프로필 → 더보기 → 멤버 ID 복사).
slack-allowlist-info-3 = 임시 공개 테스트가 아니면 '*'는 사용하지 마세요.
slack-allowlist-prompt = 허용 Slack 사용자 ID (쉼표 구분, 본인 멤버 ID 권장, 모두 허용은 '*')
slack-allowlist-warn = 허용 목록이 비어 있어 Slack 수신 메시지가 거부됩니다. ID를 추가하거나 '*'를 입력하세요.

## ── iMessage ──────────────────────────────────────────────────────

imessage-title = iMessage 설정
imessage-subtitle = macOS 전용, Messages.app에서 읽기
imessage-macos-only = iMessage는 macOS에서만 사용 가능합니다.
imessage-info-1 = Construct는 iMessage 데이터베이스를 읽고 AppleScript로 답장합니다.
imessage-info-2 = 시스템 설정에서 터미널에 Full Disk Access 권한을 부여해야 합니다.
imessage-contacts-prompt = 허용 연락처 (쉼표 구분 전화번호/이메일, 모두 허용은 *)
imessage-configured = iMessage 설정 완료 (연락처: {$contacts})

## ── Matrix ────────────────────────────────────────────────────────

matrix-title = Matrix 설정
matrix-subtitle = 셀프호스팅 가능한 페더레이션 채팅
matrix-info-1 = Matrix 계정과 액세스 토큰이 필요합니다.
matrix-info-2 = Element → 설정 → Help & About → Access Token에서 토큰을 받을 수 있습니다.
matrix-homeserver-prompt = Homeserver URL (예: https://matrix.org)
matrix-token-prompt = 액세스 토큰
matrix-conn-verified = 연결 확인됨
matrix-device-id-warn = Homeserver의 whoami 응답에 device_id가 없습니다. E2EE 복호화에 실패하면 config.toml의 channels.matrix.device_id를 수동 설정하세요.
matrix-conn-failed = 연결 실패 — Homeserver URL과 토큰을 확인하세요
matrix-room-prompt = 방 ID (예: !abc123:matrix.org)
matrix-allowlist-prompt = 허용 사용자 (쉼표 구분 @user:server, 모두 허용은 *)
matrix-recovery-prompt = E2EE 복구 키 (없으면 Enter — 자세한 내용은 docs/security/matrix-e2ee-guide.md 4G 절 참조)

## ── Signal ────────────────────────────────────────────────────────

signal-title = Signal 설정
signal-subtitle = signal-cli 데몬 브리지
signal-step-1 = 1. signal-cli 데몬을 HTTP 활성 상태로 실행하세요 (기본 포트 8686).
signal-step-2 = 2. signal-cli에 Signal 계정이 등록되어 있어야 합니다.
signal-step-3 = 3. 선택적으로 DM 전용 또는 특정 그룹으로 범위를 제한할 수 있습니다.
signal-url-prompt = signal-cli HTTP URL
signal-url-required = 건너뜀 — HTTP URL이 필요합니다
signal-account-prompt = 계정 번호 (E.164 형식, 예: +1234567890)
signal-account-required = 건너뜀 — 계정 번호가 필요합니다
signal-scope-all = 모든 메시지 (DM + 그룹)
signal-scope-dm = DM만
signal-scope-group = 특정 그룹 ID
signal-scope-prompt = 메시지 범위
signal-group-prompt = 그룹 ID
signal-group-required = 건너뜀 — 그룹 ID가 필요합니다
signal-allowlist-prompt = 허용 발신 번호 (쉼표 구분 +1234567890, 모두 허용은 *)
signal-ignore-attachments = 첨부 전용 메시지를 무시할까요?
signal-ignore-stories = 수신 스토리를 무시할까요?
signal-configured = Signal 설정 완료

## ── WhatsApp (Web + Cloud API) ───────────────────────────────────

whatsapp-title = WhatsApp 설정
whatsapp-mode-web = WhatsApp Web (QR / 페어 코드, Meta Business API 미사용)
whatsapp-mode-cloud = WhatsApp Business Cloud API (웹훅)
whatsapp-mode-prompt = WhatsApp 모드를 선택하세요

# WhatsApp Web 모드
whatsapp-web-feature-warn = 'whatsapp-web' 기능이 컴파일되어 있지 않아 런타임에서 동작하지 않습니다.
whatsapp-web-rebuild-info = 다시 빌드하려면: cargo build --features whatsapp-web
whatsapp-web-mode-label = 모드: WhatsApp Web
whatsapp-web-step-1 = 1. --features whatsapp-web로 빌드하세요
whatsapp-web-step-2 = 2. 채널/데몬을 시작하고 WhatsApp → 연결된 기기에서 QR 스캔
whatsapp-web-step-3 = 3. 재로그인을 피하려면 session_path를 영속적으로 유지하세요
whatsapp-web-session-prompt = 세션 데이터베이스 경로
whatsapp-web-session-required = 건너뜀 — 세션 경로가 필요합니다
whatsapp-web-pair-phone-prompt = 페어링 전화번호 (선택, 숫자만; 비우면 QR 플로우 사용)
whatsapp-web-pair-code-prompt = 커스텀 페어 코드 (선택, 비우면 자동 생성)
whatsapp-web-allowlist-prompt = 허용 전화번호 (쉼표 구분 +1234567890, 모두 허용은 *)
whatsapp-web-configured = WhatsApp Web 설정이 저장되었습니다.

# WhatsApp Cloud API 모드
whatsapp-cloud-mode-label = 모드: Business Cloud API
whatsapp-cloud-step-1 = 1. developers.facebook.com에서 WhatsApp 앱 생성
whatsapp-cloud-step-2 = 2. WhatsApp 제품 추가 및 phone number ID 확인
whatsapp-cloud-step-3 = 3. 임시 액세스 토큰 발급 (System User)
whatsapp-cloud-step-4 = 4. 웹훅 URL을 https://your-domain/whatsapp 으로 설정
whatsapp-cloud-token-prompt = 액세스 토큰 (Meta Developers에서 발급)
whatsapp-cloud-phone-id-prompt = Phone number ID (WhatsApp 앱 설정에서)
whatsapp-cloud-phone-id-required = 건너뜀 — phone number ID가 필요합니다
whatsapp-cloud-verify-token-prompt = 웹훅 verify 토큰 (직접 만들어 입력)
whatsapp-cloud-connected = WhatsApp API에 연결됨
whatsapp-cloud-conn-failed = 연결 실패 — 액세스 토큰과 phone number ID를 확인하세요
whatsapp-cloud-allowlist-prompt = 허용 전화번호 (쉼표 구분 +1234567890, 모두 허용은 *)

## ── Linq ──────────────────────────────────────────────────────────

linq-title = Linq 설정
linq-subtitle = Linq API를 통한 iMessage/RCS/SMS
linq-step-1 = 1. linqapp.com에 가입하고 Partner API 토큰 발급
linq-step-2 = 2. Linq 전화번호(E.164 형식) 확인
linq-step-3 = 3. 웹훅 URL을 https://your-domain/linq 으로 설정
linq-token-prompt = API 토큰 (Linq Partner API 토큰)
linq-phone-prompt = 발신 전화번호 (E.164 형식, 예: +12223334444)
linq-phone-required = 건너뜀 — 전화번호가 필요합니다
linq-connected = Linq API에 연결됨
linq-conn-failed = 연결 실패 — API 토큰을 확인하세요
linq-allowlist-prompt = 허용 발신 번호 (쉼표 구분 +1234567890, 모두 허용은 *)
linq-secret-prompt = 웹훅 서명 시크릿 (선택, 없으면 Enter)

## ── IRC ───────────────────────────────────────────────────────────

irc-title = IRC 설정
irc-subtitle = TLS를 통한 IRC
irc-info-1 = IRC는 모든 IRC 서버에 TLS로 연결합니다
irc-info-2 = SASL PLAIN과 NickServ 인증을 지원합니다
irc-server-prompt = IRC 서버 (호스트명)
irc-port-prompt = 포트
irc-port-invalid = 유효하지 않은 포트, 6697 사용
irc-nick-prompt = 봇 닉네임
irc-nick-required = 건너뜀 — 닉네임이 필요합니다
irc-channels-prompt = 참여할 채널 (쉼표 구분: #channel1,#channel2)
irc-allowlist-info-1 = 봇과 상호작용할 닉네임을 허용 목록에 추가하세요 (대소문자 구분 안 함).
irc-allowlist-info-2 = '*'는 모두 허용 (운영 환경에는 권장하지 않음).
irc-allowlist-prompt = 허용 닉네임 (쉼표 구분, 모두 허용은 *)
irc-allowlist-empty = ⚠️  허용 목록이 비어 있습니다 — 본인만 상호작용 가능. 위에 닉네임을 추가하세요.
irc-auth-info = 선택적 인증 (각 항목은 Enter로 건너뛸 수 있습니다):
irc-server-pass-prompt = 서버 비밀번호 (ZNC 같은 바운서용, 없으면 비워두기)
irc-nickserv-pass-prompt = NickServ 비밀번호 (없으면 비워두기)
irc-sasl-pass-prompt = SASL PLAIN 비밀번호 (없으면 비워두기)
irc-tls-verify-prompt = TLS 인증서를 검증할까요?
irc-configured = IRC 설정 완료: {$nick}@{$server}:{$port}

## ── Webhook ───────────────────────────────────────────────────────

webhook-title = Webhook 설정
webhook-subtitle = 커스텀 통합용 HTTP 엔드포인트
webhook-port-prompt = 포트
webhook-secret-prompt = 시크릿 (선택, 없으면 Enter)
webhook-configured = 포트 {$port}에서 Webhook 동작

## ── Nextcloud Talk ───────────────────────────────────────────────

nctalk-title = Nextcloud Talk 설정
nctalk-subtitle = Talk 웹훅 수신 + OCS API 송신
nctalk-step-1 = 1. Nextcloud Talk 봇 앱과 앱 토큰을 설정하세요.
nctalk-step-2 = 2. 웹훅 URL을 https://<your-public-url>/nextcloud-talk 으로 설정
nctalk-step-3 = 3. 활성화한 경우 webhook_secret을 Nextcloud 서명 헤더와 일치시키세요.
nctalk-base-url-prompt = Nextcloud 베이스 URL (예: https://cloud.example.com)
nctalk-base-url-required = 건너뜀 — 베이스 URL이 필요합니다
nctalk-token-prompt = 앱 토큰 (Talk 봇 토큰)
nctalk-token-required = 건너뜀 — 앱 토큰이 필요합니다
nctalk-secret-prompt = 웹훅 시크릿 (선택, 없으면 Enter)
nctalk-allowlist-prompt = 허용 Nextcloud 액터 ID (쉼표 구분, 모두 허용은 *)
nctalk-configured = Nextcloud Talk 설정 완료

## ── DingTalk ──────────────────────────────────────────────────────

dingtalk-title = DingTalk 설정
dingtalk-subtitle = DingTalk Stream Mode
dingtalk-step-1 = 1. DingTalk 개발자 콘솔(open.dingtalk.com)로 이동
dingtalk-step-2 = 2. 앱을 만들고 Stream Mode 봇 활성화
dingtalk-step-3 = 3. Client ID(AppKey)와 Client Secret(AppSecret) 복사
dingtalk-client-id-prompt = Client ID (AppKey)
dingtalk-client-secret-prompt = Client Secret (AppSecret)
dingtalk-verified = DingTalk 자격증명 확인됨
dingtalk-allowlist-prompt = 허용 직원 ID (쉼표 구분, 모두 허용은 '*')

## ── QQ Official ───────────────────────────────────────────────────

qq-title = QQ Official 설정
qq-subtitle = Tencent QQ Bot SDK
qq-step-1 = 1. QQ Bot 개발자 콘솔(q.qq.com)로 이동
qq-step-2 = 2. 봇 애플리케이션 생성
qq-step-3 = 3. App ID와 App Secret 복사
qq-app-id-prompt = App ID
qq-app-secret-prompt = App Secret
qq-verified = QQ Bot 자격증명 확인됨
qq-auth-failed = 인증 오류 — 자격증명을 확인하세요
qq-allowlist-prompt = 허용 사용자 ID (쉼표 구분, 모두 허용은 '*')

## ── Lark / Feishu ────────────────────────────────────────────────

lark-title = {$provider} 설정
lark-subtitle = {$provider}에서 Construct와 대화하기
lark-step-1 = 1. {$provider} Open Platform({$host})로 이동
lark-step-2 = 2. 앱을 만들고 'Bot' 기능 활성화
lark-step-3 = 3. App ID와 App Secret 복사
lark-app-id-prompt = App ID
lark-app-secret-prompt = App Secret
lark-app-secret-required = App Secret이 필요합니다
lark-verified = {$provider} 자격증명 확인됨
lark-receive-mode-prompt = 수신 모드
lark-receive-mode-ws = WebSocket (권장, 공인 IP 불필요)
lark-receive-mode-webhook = Webhook (공개 HTTPS 엔드포인트 필요)
lark-verify-token-prompt = Verification 토큰 (선택, Webhook 모드용)
lark-verify-token-empty = Verification 토큰이 비어 있어 웹훅 인증 검사가 약화됩니다.
lark-webhook-port-prompt = Webhook 포트
lark-allowlist-prompt = 허용 Open ID (쉼표 구분, 모두 허용은 '*')
lark-allowlist-warn = 허용 목록이 비어 있어 {$provider} 수신 메시지가 거부됩니다. Open ID를 추가하거나 '*'를 입력하세요.

## ── Nostr ─────────────────────────────────────────────────────────

nostr-title = Nostr 설정
nostr-subtitle = NIP-04 및 NIP-17을 통한 비공개 메시지
nostr-info-1 = Construct가 Nostr 릴레이에서 암호화된 DM을 수신합니다.
nostr-info-2 = Nostr 개인키(hex 또는 nsec)와 최소 한 개의 릴레이가 필요합니다.
nostr-key-prompt = 개인키 (hex 또는 nsec1...)
nostr-key-valid = 키 유효 — 공개키: {$pubkey}
nostr-key-invalid = 유효하지 않은 개인키 — 형식을 확인하고 다시 시도하세요
nostr-relays-prompt = 릴레이 URL (쉼표 구분, 기본값을 쓰려면 Enter)
nostr-allowlist-info-1 = 봇에 메시지를 보낼 수 있는 pubkey를 허용 목록에 추가하세요 (hex 또는 npub).
nostr-allowlist-info-2 = '*'는 모두 허용 (운영 환경에는 권장하지 않음).
nostr-allowlist-prompt = 허용 pubkey (쉼표 구분, 모두 허용은 *)
nostr-allowlist-warn = 허용 목록이 비어 있어 수신 메시지가 거부됩니다. pubkey를 추가하거나 '*'를 입력하세요.
nostr-configured = Nostr 설정 완료 ({$relay_count}개 릴레이)
