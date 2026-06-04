# Revka 프로바이더 참조

이 문서는 프로바이더 ID, 별칭, 자격 환경 변수의 매핑을 정리합니다.

마지막 검증: **2026년 4월 21일**.

## 프로바이더 목록 보기

```bash
revka providers
```

## 자격 결정 순서

런타임 결정 순서는 다음과 같습니다.

1. 설정/CLI에서 들어온 명시적 자격
2. 프로바이더별 환경 변수
3. 일반 폴백 환경 변수: `REVKA_API_KEY` → `API_KEY`

탄력적 폴백 체인(`reliability.fallback_providers`)에서는 각 폴백 프로바이더가 자격을 독립적으로 결정합니다. 주 프로바이더의 명시적 자격은 폴백 프로바이더로 재사용되지 않습니다.

## 프로바이더 카탈로그

| 정식 ID | 별칭 | 로컬 | 프로바이더별 환경 변수 |
|---|---|---:|---|
| `openrouter` | — | 아니오 | `OPENROUTER_API_KEY` |
| `anthropic` | — | 아니오 | `ANTHROPIC_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` |
| `openai` | — | 아니오 | `OPENAI_API_KEY` |
| `ollama` | — | 예 | `OLLAMA_API_KEY` (선택) |
| `gemini` | `google`, `google-gemini` | 아니오 | `GEMINI_API_KEY`, `GOOGLE_API_KEY` |
| `venice` | — | 아니오 | `VENICE_API_KEY` |
| `vercel` | `vercel-ai` | 아니오 | `VERCEL_API_KEY` |
| `cloudflare` | `cloudflare-ai` | 아니오 | `CLOUDFLARE_API_KEY` |
| `moonshot` | `kimi` | 아니오 | `MOONSHOT_API_KEY` |
| `kimi-code` | `kimi_coding`, `kimi_for_coding` | 아니오 | `KIMI_CODE_API_KEY`, `MOONSHOT_API_KEY` |
| `synthetic` | — | 아니오 | `SYNTHETIC_API_KEY` |
| `opencode` | `opencode-zen` | 아니오 | `OPENCODE_API_KEY` |
| `opencode-go` | — | 아니오 | `OPENCODE_GO_API_KEY` |
| `zai` | `z.ai` | 아니오 | `ZAI_API_KEY` |
| `glm` | `zhipu` | 아니오 | `GLM_API_KEY` |
| `minimax` | `minimax-intl`, `minimax-io`, `minimax-global`, `minimax-cn`, `minimaxi`, `minimax-oauth`, `minimax-oauth-cn`, `minimax-portal`, `minimax-portal-cn` | 아니오 | `MINIMAX_OAUTH_TOKEN`, `MINIMAX_API_KEY` |
| `bedrock` | `aws-bedrock` | 아니오 | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (선택: `AWS_REGION`) |
| `qianfan` | `baidu` | 아니오 | `QIANFAN_API_KEY` |
| `doubao` | `volcengine`, `ark`, `doubao-cn` | 아니오 | `ARK_API_KEY`, `DOUBAO_API_KEY` |
| `qwen` | `dashscope`, `qwen-intl`, `dashscope-intl`, `qwen-us`, `dashscope-us`, `qwen-code`, `qwen-oauth`, `qwen_oauth` | 아니오 | `QWEN_OAUTH_TOKEN`, `DASHSCOPE_API_KEY` |
| `groq` | — | 아니오 | `GROQ_API_KEY` |
| `mistral` | — | 아니오 | `MISTRAL_API_KEY` |
| `xai` | `grok` | 아니오 | `XAI_API_KEY` |
| `deepseek` | — | 아니오 | `DEEPSEEK_API_KEY` |
| `together` | `together-ai` | 아니오 | `TOGETHER_API_KEY` |
| `fireworks` | `fireworks-ai` | 아니오 | `FIREWORKS_API_KEY` |
| `novita` | — | 아니오 | `NOVITA_API_KEY` |
| `perplexity` | — | 아니오 | `PERPLEXITY_API_KEY` |
| `cohere` | — | 아니오 | `COHERE_API_KEY` |
| `copilot` | `github-copilot` | 아니오 | (GitHub 토큰을 설정/`API_KEY` 폴백으로 전달) |
| `lmstudio` | `lm-studio` | 예 | (선택; 기본 로컬) |
| `llamacpp` | `llama.cpp` | 예 | `LLAMACPP_API_KEY` (서버 인증이 켜진 경우만) |
| `sglang` | — | 예 | `SGLANG_API_KEY` (선택) |
| `vllm` | — | 예 | `VLLM_API_KEY` (선택) |
| `osaurus` | — | 예 | `OSAURUS_API_KEY` (선택; 기본값 `"osaurus"`) |
| `nvidia` | `nvidia-nim`, `build.nvidia.com` | 아니오 | `NVIDIA_API_KEY` |
| `avian` | — | 아니오 | `AVIAN_API_KEY` |
| `claude-code` | — | 아니오 | (로컬 Claude Code CLI 구독/세션 사용) |
| `openai-codex` | `codex` | 아니오 | `revka auth login --provider openai-codex` OAuth (`~/.revka/auth/`에 캐시되거나 `~/.codex/auth.json`에서 가져옴) |
| `gemini-cli` | — | 아니오 | `~/.gemini/oauth_creds.json` 기반 OAuth, 또는 `GEMINI_API_KEY` 폴백 |
| `copilot` | `github-copilot` | 아니오 | 설정/`API_KEY` 폴백을 통해 GitHub 토큰 |
| `azure_openai` | `azure-openai`, `azure` | 아니오 | `AZURE_OPENAI_API_KEY` (엔드포인트/디플로이먼트 설정 필요) |
| `telnyx` | — | 아니오 | `TELNYX_API_KEY` |
| `kilocli` | — | 아니오 | (현재 엔드포인트는 `revka providers` 출력 참고) |

### Vercel AI Gateway 메모

- 프로바이더 ID: `vercel` (별칭: `vercel-ai`)
- 베이스 API URL: `https://ai-gateway.vercel.sh/v1`
- 인증: `VERCEL_API_KEY`
- Vercel AI Gateway 사용은 프로젝트 배포가 필요 없습니다.
- `DEPLOYMENT_NOT_FOUND`가 보이면 프로바이더가 `https://api.vercel.ai`가 아니라 위 게이트웨이 엔드포인트를 가리키는지 확인하세요.

### Gemini 메모

- 프로바이더 ID: `gemini` (별칭: `google`, `google-gemini`)
- 인증은 `GEMINI_API_KEY`, `GOOGLE_API_KEY`, 또는 Gemini CLI OAuth 캐시(`~/.gemini/oauth_creds.json`)에서 올 수 있습니다.
- API 키 요청은 `generativelanguage.googleapis.com/v1beta`를 사용합니다.
- Gemini CLI OAuth 요청은 `cloudcode-pa.googleapis.com/v1internal`에 Code Assist 요청 봉투 의미론으로 보냅니다.
- Thinking 모델(예: `gemini-3-pro-preview`)을 지원합니다 — 내부 추론 부분은 응답에서 자동 필터링됩니다.

### Ollama Vision 메모

- 프로바이더 ID: `ollama`
- 비전 입력은 사용자 메시지의 이미지 마커 `[IMAGE:<source>]`로 지원됩니다.
- 멀티모달 정규화 후, Revka는 이미지 페이로드를 Ollama 네이티브 `messages[].images` 필드로 보냅니다.
- 비전 비지원 프로바이더가 선택됐을 때는 이미지를 조용히 무시하지 않고 구조화된 capability 에러를 반환합니다.

### Ollama 클라우드 라우팅 메모

- `:cloud` 모델 접미사는 원격 Ollama 엔드포인트에서만 사용하세요.
- 원격 엔드포인트는 `api_url`에 설정해야 합니다 (예: `https://ollama.com`).
- Revka는 `api_url` 끝의 `/api`를 자동 정규화합니다.
- `default_model`이 `:cloud`로 끝나는데 `api_url`이 로컬이거나 비어 있으면, 설정 검증이 조기에 의미 있는 에러로 실패합니다.
- 로컬 Ollama 모델 디스커버리는 의도적으로 `:cloud` 항목을 제외해, 로컬 모드에서 클라우드 전용 모델이 선택되는 것을 막습니다.

### llama.cpp 서버 메모

- 프로바이더 ID: `llamacpp` (별칭: `llama.cpp`)
- 기본 엔드포인트: `http://localhost:8080/v1`
- API 키는 기본적으로 선택 사항입니다. `llama-server`를 `--api-key`로 실행한 경우에만 `LLAMACPP_API_KEY`를 설정하세요.
- 모델 디스커버리: `revka models refresh --provider llamacpp`

### SGLang 서버 메모

- 프로바이더 ID: `sglang`
- 기본 엔드포인트: `http://localhost:30000/v1`
- API 키는 기본적으로 선택 사항입니다. 서버가 인증을 요구할 때만 `SGLANG_API_KEY`를 설정하세요.
- 도구 호출은 SGLang을 `--tool-call-parser`(예: `hermes`, `llama3`, `qwen25`)로 실행해야 합니다.
- 모델 디스커버리: `revka models refresh --provider sglang`

### vLLM 서버 메모

- 프로바이더 ID: `vllm`
- 기본 엔드포인트: `http://localhost:8000/v1`
- API 키는 기본적으로 선택 사항입니다. 서버가 인증을 요구할 때만 `VLLM_API_KEY`를 설정하세요.
- 모델 디스커버리: `revka models refresh --provider vllm`

### Osaurus 서버 메모

- 프로바이더 ID: `osaurus`
- 기본 엔드포인트: `http://localhost:1337/v1`
- API 키 기본값은 `"osaurus"`이지만 선택 사항입니다. `OSAURUS_API_KEY`로 덮어쓰거나 비워 두면 키 없이 접근됩니다.
- 모델 디스커버리: `revka models refresh --provider osaurus`
- [Osaurus](https://github.com/dinoki-ai/osaurus)는 macOS(Apple Silicon) 통합 AI 엣지 런타임으로, 로컬 MLX 추론과 클라우드 프로바이더 프록싱을 한 엔드포인트에서 결합합니다.
- 여러 API 포맷을 동시에 지원합니다: OpenAI 호환(`/v1/chat/completions`), Anthropic(`/messages`), Ollama(`/chat`), Open Responses(`/v1/responses`).
- 빌트인 MCP(Model Context Protocol) 지원으로 도구·컨텍스트 서버 연결 가능.
- 로컬 모델은 MLX로 실행됩니다 (Llama, Qwen, Gemma, GLM, Phi, Nemotron 등). 클라우드 모델은 투명하게 프록시됩니다.

### Bedrock 메모

- 프로바이더 ID: `bedrock` (별칭: `aws-bedrock`)
- API: [Converse API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_Converse.html)
- 인증: AWS AKSK (단일 API 키 아님). `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` 환경 변수를 설정하세요.
- 선택: 임시/STS 자격을 위한 `AWS_SESSION_TOKEN`, `AWS_REGION` 또는 `AWS_DEFAULT_REGION` (기본 `us-east-1`).
- 기본 온보딩 모델: `anthropic.claude-sonnet-4-5-20250929-v1:0`
- 네이티브 도구 호출과 프롬프트 캐싱(`cachePoint`) 지원.
- 크로스 리전 추론 프로파일 지원 (예: `us.anthropic.claude-*`).
- 모델 ID는 Bedrock 포맷을 사용합니다: `anthropic.claude-sonnet-4-6`, `anthropic.claude-opus-4-6-v1` 등.

### Ollama Reasoning 토글

`config.toml`에서 Ollama의 reasoning/thinking 동작을 제어할 수 있습니다.

```toml
[runtime]
reasoning_enabled = false
```

동작:

- `false`: Ollama `/api/chat` 요청에 `think: false`를 보냅니다.
- `true`: `think: true`를 보냅니다.
- 미설정: `think`를 생략하고 Ollama/모델 기본값을 따릅니다.

### Kimi Code 메모

- 프로바이더 ID: `kimi-code`
- 엔드포인트: `https://api.kimi.com/coding/v1`
- 기본 온보딩 모델: `kimi-for-coding` (대안: `kimi-k2.5`)
- 런타임이 호환성을 위해 `User-Agent: KimiCLI/0.77`을 자동으로 추가합니다.

### NVIDIA NIM 메모

- 정식 프로바이더 ID: `nvidia`
- 별칭: `nvidia-nim`, `build.nvidia.com`
- 베이스 API URL: `https://integrate.api.nvidia.com/v1`
- 모델 디스커버리: `revka models refresh --provider nvidia`

권장 시작 모델 ID (2026년 2월 18일 NVIDIA API 카탈로그 기준):

- `meta/llama-3.3-70b-instruct`
- `deepseek-ai/deepseek-v3.2`
- `nvidia/llama-3.3-nemotron-super-49b-v1.5`
- `nvidia/llama-3.1-nemotron-ultra-253b-v1`

### Claude Code / Codex / Gemini CLI 구독 프로바이더

이 프로바이더들은 직접 API 키 대신 해당 벤더의 네이티브 CLI에서 인증된 구독을 Revka가 재사용하게 합니다.

- `claude-code` — 로컬 Claude Code CLI 자격/세션 재사용.
- `openai-codex` (별칭 `codex`) — `revka auth login --provider openai-codex [--device-code]`로 OAuth, 또는 `--import <path>`로 `~/.codex/auth.json` 재사용.
- `gemini-cli` — `~/.gemini/oauth_creds.json`에 캐시된 Gemini CLI OAuth 자격 사용.

활성 프로필 관리는 `revka auth use --provider <id> --profile <name>`으로 합니다. `revka auth status`는 활성 프로필과 토큰 만료 정보를 출력합니다.

## 커스텀 엔드포인트

- OpenAI 호환 엔드포인트:

```toml
default_provider = "custom:https://your-api.example.com"
```

- Anthropic 호환 엔드포인트:

```toml
default_provider = "anthropic-custom:https://your-api.example.com"
```

## MiniMax OAuth 설정 (config.toml)

설정에서 MiniMax 프로바이더와 OAuth 플레이스홀더를 지정합니다.

```toml
default_provider = "minimax-oauth"
api_key = "minimax-oauth"
```

그런 다음 환경 변수로 자격 중 하나를 제공합니다.

- `MINIMAX_OAUTH_TOKEN` (권장, 직접 액세스 토큰)
- `MINIMAX_API_KEY` (레거시/정적 토큰)
- `MINIMAX_OAUTH_REFRESH_TOKEN` (시작 시 액세스 토큰 자동 갱신)

선택:

- `MINIMAX_OAUTH_REGION=global` 또는 `cn` (프로바이더 별칭에 따라 기본값)
- 기본 OAuth 클라이언트 ID를 덮어쓰려면 `MINIMAX_OAUTH_CLIENT_ID`

채널 호환 메모:

- MiniMax 기반 채널 대화의 경우, 런타임이 히스토리를 정규화해 `user`/`assistant` 턴 순서를 유효하게 유지합니다.
- 채널별 전달 가이드(예: Telegram 첨부 마커)는 끝에 붙는 `system` 턴으로 추가되지 않고, 선두 시스템 프롬프트에 병합됩니다.

## Qwen Code OAuth 설정 (config.toml)

설정에서 Qwen Code OAuth 모드를 지정합니다.

```toml
default_provider = "qwen-code"
api_key = "qwen-oauth"
```

`qwen-code`의 자격 결정 순서:

1. 명시적 `api_key` 값 (플레이스홀더 `qwen-oauth`가 아닌 경우)
2. `QWEN_OAUTH_TOKEN`
3. `~/.qwen/oauth_creds.json` (Qwen Code 캐시 OAuth 자격 재사용)
4. (선택) `QWEN_OAUTH_REFRESH_TOKEN` 또는 캐시된 리프레시 토큰을 통한 갱신
5. OAuth 플레이스홀더를 쓰지 않는 경우 폴백으로 `DASHSCOPE_API_KEY` 사용 가능

선택적 엔드포인트 오버라이드:

- `QWEN_OAUTH_RESOURCE_URL` (필요 시 `https://.../v1`로 정규화)
- 미설정이면 캐시된 OAuth 자격의 `resource_url`이 있으면 사용

## 모델 라우팅 (`hint:<name>`)

`[[model_routes]]`로 힌트 기반 라우팅을 구성할 수 있습니다.

```toml
[[model_routes]]
hint = "reasoning"
provider = "openrouter"
model = "anthropic/claude-opus-4-20250514"

[[model_routes]]
hint = "fast"
provider = "groq"
model = "llama-3.3-70b-versatile"
```

그 후 도구나 통합 경로에서 힌트 모델 이름으로 호출합니다:

```text
hint:reasoning
```

## 임베딩 라우팅 (`hint:<name>`)

`[[embedding_routes]]`로 동일한 힌트 패턴을 적용할 수 있습니다. `[memory].embedding_model`을 `hint:<name>` 값으로 설정해 라우팅을 활성화하세요.

```toml
[memory]
embedding_model = "hint:semantic"

[[embedding_routes]]
hint = "semantic"
provider = "openai"
model = "text-embedding-3-small"
dimensions = 1536

[[embedding_routes]]
hint = "archive"
provider = "custom:https://embed.example.com/v1"
model = "your-embedding-model-id"
dimensions = 1024
```

지원 임베딩 프로바이더:

- `none`
- `openai`
- `custom:<url>` (OpenAI 호환 임베딩 엔드포인트)

라우트별 키 오버라이드 (선택):

```toml
[[embedding_routes]]
hint = "semantic"
provider = "openai"
model = "text-embedding-3-small"
api_key = "sk-route-specific"
```

## 모델 안전 업그레이드

안정적인 힌트를 유지하고, 프로바이더가 모델 ID를 폐기할 때 라우트 타깃만 갱신하세요.

권장 흐름:

1. 호출 지점은 그대로 유지하세요 (`hint:reasoning`, `hint:semantic`).
2. `[[model_routes]]` 또는 `[[embedding_routes]]` 아래 타깃 모델만 변경하세요.
3. 다음을 실행합니다:
   - `revka doctor`
   - `revka status`
4. 롤아웃 전에 대표 흐름 하나를 스모크 테스트하세요 (채팅 + 메모리 회상).

이 방식은 모델 ID가 업그레이드돼도 통합과 프롬프트가 바뀌지 않아 깨지는 일이 적습니다.
