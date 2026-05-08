# Construct 구성 참조 (운영자 중심)

자주 보는 구성 섹션과 기본값을 한 번에 훑을 수 있도록 정리했습니다.

마지막 검증: **2026년 4월 21일**.

전체 머신 판독 가능 스키마(모든 키와 모든 기본값)는 다음으로 받으세요:

```bash
construct config schema > schema.json
```

시작 시 설정 파일 경로 결정 순서:

1. `CONSTRUCT_WORKSPACE` 오버라이드 (설정된 경우)
2. 영속된 `~/.construct/active_workspace.toml` 마커 (있는 경우)
3. 기본값 `~/.construct/config.toml`

Construct는 시작 시 결정된 설정을 `INFO` 레벨로 기록합니다.

- `Config loaded` — 필드: `path`, `workspace`, `source`, `initialized`

스키마 내보내기 명령:

- `construct config schema` — JSON Schema (draft 2020-12)을 stdout으로 출력

## 핵심 키

| 키 | 기본값 | 메모 |
|---|---|---|
| `default_provider` | `openrouter` | 프로바이더 ID 또는 별칭 |
| `default_model` | `anthropic/claude-sonnet-4-6` | 선택된 프로바이더로 라우팅되는 모델 |
| `default_temperature` | `0.7` | 모델 temperature |

<!-- TODO screenshot: editor showing the [observability] section of config.toml -->
![config.toml의 [observability] 섹션을 보여 주는 에디터](../../../../assets/reference/config-reference-02-observability-section.png)

## `[observability]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `backend` | `none` | 옵저버빌리티 백엔드: `none`, `noop`, `log`, `prometheus`, `otel`, `opentelemetry`, `otlp` |
| `otel_endpoint` | `http://localhost:4318` | 백엔드가 `otel`일 때 사용하는 OTLP HTTP 엔드포인트 |
| `otel_service_name` | `construct` | OTLP 콜렉터에 보낼 서비스 이름 |
| `runtime_trace_mode` | `none` | 런타임 트레이스 저장 모드: `none`, `rolling`, `full` |
| `runtime_trace_path` | `state/runtime-trace.jsonl` | 런타임 트레이스 JSONL 경로 (절대 경로가 아니면 워크스페이스 기준) |
| `runtime_trace_max_entries` | `200` | `runtime_trace_mode = "rolling"`일 때 보관할 최대 이벤트 수 |

메모:

- `backend = "otel"`은 블로킹 익스포터 클라이언트로 OTLP HTTP 익스포트를 사용해, Tokio 외부 컨텍스트에서도 안전하게 스팬과 메트릭을 내보냅니다.
- `opentelemetry`와 `otlp`는 `otel` 백엔드의 별칭입니다.
- 런타임 트레이스는 도구 호출 실패와 망가진 모델 도구 페이로드를 디버깅하기 위한 용도입니다. 모델 출력 텍스트가 들어갈 수 있으니 공유 호스트에서는 기본적으로 끄세요.
- 런타임 트레이스 조회:
  - `construct doctor traces --limit 20`
  - `construct doctor traces --event tool_call_result --contains "error"`
  - `construct doctor traces --id <trace-id>`

예시:

```toml
[observability]
backend = "otel"
otel_endpoint = "http://localhost:4318"
otel_service_name = "construct"
runtime_trace_mode = "rolling"
runtime_trace_path = "state/runtime-trace.jsonl"
runtime_trace_max_entries = 200
```

<!-- TODO screenshot: terminal setting CONSTRUCT_PROVIDER env var and running construct with the overridden provider -->
![CONSTRUCT_PROVIDER 환경 변수를 설정한 뒤 오버라이드된 프로바이더로 construct를 실행하는 터미널](../../../../assets/reference/config-reference-01-env-override-terminal.png)

## 환경 변수 기반 프로바이더 오버라이드

프로바이더는 환경 변수로도 제어할 수 있습니다. 우선순위:

1. `CONSTRUCT_PROVIDER` (명시적 오버라이드, 비어 있지 않으면 항상 우선)
2. `PROVIDER` (레거시 폴백, 설정 프로바이더가 비어 있거나 여전히 `openrouter`인 경우에만 적용)
3. `config.toml`의 `default_provider`

컨테이너 운영 시 참고:

- `config.toml`이 `custom:https://.../v1` 같은 명시적 커스텀 프로바이더를 지정하면, Docker/컨테이너 환경의 기본 `PROVIDER=openrouter`로 더 이상 덮어 써지지 않습니다.
- 의도적으로 런타임 환경 변수가 비기본 프로바이더 설정을 오버라이드하길 원할 때는 `CONSTRUCT_PROVIDER`를 쓰세요.

## `[agent]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `compact_context` | `true` | true면 bootstrap_max_chars=6000, rag_chunk_limit=2. 13B 이하 모델에 권장 |
| `max_tool_iterations` | `10` | CLI·게이트웨이·채널을 통틀어 한 사용자 메시지당 도구 호출 루프 최대 회수 |
| `max_history_messages` | `50` | 세션당 보관할 대화 히스토리 최대 메시지 수 |
| `parallel_tools` | `false` | 한 번의 이터레이션 안에서 도구 병렬 실행 활성화 |
| `tool_dispatcher` | `auto` | 도구 디스패치 전략 |
| `tool_call_dedup_exempt` | `[]` | 같은 턴 안 중복 호출 억제에서 제외할 도구 이름 |
| `tool_filter_groups` | `[]` | 턴별 MCP 도구 스키마 필터 그룹 (아래 참고) |

메모:

- `max_tool_iterations = 0`은 안전 기본값 `10`으로 폴백됩니다.
- 채널 메시지가 이 값을 넘으면 런타임이 다음을 반환합니다: `Agent exceeded maximum tool iterations (<value>)`.
- CLI·게이트웨이·채널의 도구 루프에서 승인 게이팅이 필요 없는 독립 도구 호출이 여러 개 있으면 기본적으로 동시에 실행되며, 결과 순서는 안정적입니다.
- `parallel_tools`는 `Agent::turn()` API 표면에 적용됩니다. CLI/게이트웨이/채널 핸들러의 런타임 루프는 게이트하지 않습니다.
- `tool_call_dedup_exempt`는 정확한 도구 이름의 배열을 받습니다. 여기에 등록된 도구는 같은 턴 안에서 동일한 인자로 여러 번 호출돼도 dedup 검사를 건너뜁니다. 예: `tool_call_dedup_exempt = ["browser"]`.

### `tool_filter_groups`

매 턴 LLM에 보내는 MCP 도구 스키마를 제한해 토큰 오버헤드를 줄입니다. 빌트인(비-MCP) 도구는 항상 그대로 통과합니다.

각 항목은 다음 필드를 가진 테이블입니다.

| 필드 | 타입 | 용도 |
|---|---|---|
| `mode` | `"always"` \| `"dynamic"` | `always`: 무조건 포함. `dynamic`: 사용자 메시지에 키워드가 있을 때만 포함. |
| `tools` | `[string]` | 도구 이름 패턴. 단일 `*` 와일드카드 지원 (접두/접미/중간), 예: `"mcp_vikunja_*"`. |
| `keywords` | `[string]` | (`dynamic` 전용) 마지막 사용자 메시지에 대한 대소문자 무시 부분 일치. |

`tool_filter_groups`가 비어 있으면 기능이 꺼져 있고 모든 도구가 그대로 통과합니다 (하위 호환 기본값).

예시:

```toml
[agent]
# Vikunja 태스크 관리 MCP 도구는 항상 사용 가능
[[agent.tool_filter_groups]]
mode = "always"
tools = ["mcp_vikunja_*"]

# 브라우저 MCP 도구는 사용자 메시지가 브라우징을 언급할 때만 포함
[[agent.tool_filter_groups]]
mode = "dynamic"
tools = ["mcp_browser_*"]
keywords = ["browse", "navigate", "open url", "screenshot"]
```

## `[pacing]`

느린 로컬 LLM 워크로드(Ollama, llama.cpp, vLLM)를 위한 페이싱 컨트롤. 모든 키가 선택 사항이며, 빠지면 기존 동작을 그대로 유지합니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `step_timeout_secs` | _없음_ | 한 단계 LLM 추론 턴 최대 시간(초). 정말로 멈춘 모델은 잡되 전체 작업 루프는 죽이지 않음 |
| `loop_detection_min_elapsed_secs` | _없음_ | 루프 감지가 활성화되기 전까지의 최소 경과 초. 짧게 끝나는 작업은 강한 루프 보호를 받고, 긴 작업은 유예 시간을 받음 |
| `loop_ignore_tools` | `[]` | 출력 동일성 기반 루프 감지에서 제외할 도구. `browser_screenshot`처럼 구조적으로 루프처럼 보이는 워크플로에 유용 |
| `message_timeout_scale_max` | `4` | 하드코딩된 타임아웃 스케일 상한 오버라이드. 채널 메시지 타임아웃 예산 = `message_timeout_secs * min(max_tool_iterations, message_timeout_scale_max)` |

메모:

- 이 설정들은 로컬/느린 LLM 배포를 위한 것입니다. 클라우드 프로바이더 사용자는 보통 필요하지 않습니다.
- `step_timeout_secs`는 전체 채널 메시지 타임아웃 예산과 독립적으로 동작합니다. 단계 타임아웃으로 중단돼도 전체 예산을 소모하지 않으며, 루프만 멈춥니다.
- `loop_detection_min_elapsed_secs`는 작업 자체가 아니라 루프 감지 카운팅을 지연시킵니다. 짧은 작업에는 루프 보호가 그대로 살아 있습니다 (기본 동작).
- `loop_ignore_tools`는 명시된 도구에 한해 도구 출력 기반 루프 감지만 끕니다. 다른 안전 기능(최대 이터레이션, 전체 타임아웃)은 그대로 살아 있습니다.
- `message_timeout_scale_max`는 1 이상이어야 합니다. `max_tool_iterations`보다 크게 잡아도 추가 효과는 없습니다 (수식이 `min()`을 씁니다).
- 느린 로컬 Ollama 배포 예시:

```toml
[pacing]
step_timeout_secs = 120
loop_detection_min_elapsed_secs = 60
loop_ignore_tools = ["browser_screenshot", "browser_navigate"]
message_timeout_scale_max = 8
```

## `[security.otp]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | 민감한 작업/도메인에 대한 OTP 게이팅 활성화 |
| `method` | `totp` | OTP 방식 (`totp`, `pairing`, `cli-prompt`) |
| `token_ttl_secs` | `30` | TOTP 시간 윈도우(초) |
| `cache_valid_secs` | `300` | 최근 검증된 OTP 코드의 캐시 윈도우 |
| `gated_actions` | `["shell","file_write","browser_open","browser"]` | OTP로 보호되는 도구 액션 |
| `gated_domains` | `[]` | OTP가 필요한 명시적 도메인 패턴 (`*.example.com`, `login.example.com`) |
| `gated_domain_categories` | `[]` | 도메인 프리셋 카테고리 (`banking`, `medical`, `government`, `identity_providers`) |

메모:

- 도메인 패턴은 와일드카드 `*`를 지원합니다.
- 카테고리 프리셋은 검증 시점에 정제된 도메인 집합으로 확장됩니다.
- 잘못된 도메인 글롭이나 알려지지 않은 카테고리는 시작 시 즉시 실패합니다.
- `enabled = true`이고 OTP 시크릿이 없으면 Construct가 한 번 생성하고 등록 URI를 한 번 출력합니다.

예시:

```toml
[security.otp]
enabled = true
method = "totp"
token_ttl_secs = 30
cache_valid_secs = 300
gated_actions = ["shell", "browser_open"]
gated_domains = ["*.chase.com", "accounts.google.com"]
gated_domain_categories = ["banking"]
```

## `[security.estop]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | 비상 정지 상태 머신과 CLI 활성화 |
| `state_file` | `~/.construct/estop-state.json` | estop 상태 영속 경로 |
| `require_otp_to_resume` | `true` | resume 작업 전에 OTP 검증 요구 |

메모:

- estop 상태는 원자적으로 영속되고 시작 시 다시 로드됩니다.
- 손상되거나 읽을 수 없는 estop 상태는 fail-closed `kill_all`로 폴백합니다.
- 발동은 `construct estop`, 단계 해제는 `construct estop resume`으로 합니다.

## `[agents.<name>]`

위임용 서브 에이전트 설정. `[agents]` 아래 각 키가 주 에이전트가 위임할 수 있는 이름 있는 서브 에이전트를 정의합니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `provider` | _필수_ | 프로바이더 이름 (예: `"ollama"`, `"openrouter"`, `"anthropic"`) |
| `model` | _필수_ | 서브 에이전트가 사용할 모델 |
| `system_prompt` | 미설정 | 서브 에이전트용 시스템 프롬프트 오버라이드 (선택) |
| `api_key` | 미설정 | API 키 오버라이드 (선택, `secrets.encrypt = true`일 때 암호화 저장) |
| `temperature` | 미설정 | 서브 에이전트용 temperature 오버라이드 |
| `max_depth` | `3` | 중첩 위임 최대 깊이 |
| `agentic` | `false` | 서브 에이전트의 멀티 턴 도구 호출 루프 모드 활성화 |
| `allowed_tools` | `[]` | agentic 모드용 도구 허용 목록 |
| `max_iterations` | `10` | agentic 모드 최대 도구 호출 이터레이션 |
| `timeout_secs` | `120` | 비-agentic 프로바이더 호출 타임아웃(초, 1–3600) |
| `agentic_timeout_secs` | `300` | agentic 서브 에이전트 루프 타임아웃(초, 1–3600) |
| `skills_directory` | 미설정 | 스킬 디렉터리(워크스페이스 기준 경로). 스코프된 스킬 로딩에 사용 |

메모:

- `agentic = false`는 단일 프롬프트→응답 위임 동작을 그대로 유지합니다.
- `agentic = true`는 `allowed_tools`에 일치하는 항목이 최소 하나 있어야 합니다.
- `delegate` 도구는 재진입 위임 루프를 막기 위해 서브 에이전트 허용 목록에서 제외됩니다.
- 서브 에이전트는 다음을 포함한 풍부한 시스템 프롬프트를 받습니다: 도구 섹션(허용 도구와 파라미터), 스킬 섹션(스코프 또는 기본 디렉터리), 워크스페이스 경로, 현재 날짜·시각, 안전 제약, `shell`이 포함됐을 때의 셸 정책.
- `skills_directory`가 비어 있으면 서브 에이전트는 기본 워크스페이스 `skills/` 디렉터리에서 스킬을 로드합니다. 설정되면 해당 디렉터리(워크스페이스 루트 기준)에서만 로드해, 에이전트별 스코프된 스킬셋을 만들 수 있습니다.

```toml
[agents.researcher]
provider = "openrouter"
model = "anthropic/claude-sonnet-4-6"
system_prompt = "You are a research assistant."
max_depth = 2
agentic = true
allowed_tools = ["web_search", "http_request", "file_read"]
max_iterations = 8
agentic_timeout_secs = 600

[agents.coder]
provider = "ollama"
model = "qwen2.5-coder:32b"
temperature = 0.2
timeout_secs = 60

[agents.code_reviewer]
provider = "anthropic"
model = "claude-opus-4-5"
system_prompt = "You are an expert code reviewer focused on security and performance."
agentic = true
allowed_tools = ["file_read", "shell"]
skills_directory = "skills/code-review"
```

## `[runtime]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `reasoning_enabled` | 미설정 (`None`) | 명시적 컨트롤을 지원하는 프로바이더에 대한 글로벌 reasoning/thinking 오버라이드 |

메모:

- `reasoning_enabled = false`는 지원 프로바이더(현재 `ollama`, 요청 필드 `think: false`)에서 서버 측 reasoning을 명시적으로 끕니다.
- `reasoning_enabled = true`는 지원 프로바이더에서 reasoning을 명시적으로 요청합니다 (`ollama`의 `think: true`).
- 미설정이면 프로바이더 기본값을 따릅니다.

## `[skills]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `open_skills_enabled` | `false` | 커뮤니티 `open-skills` 리포지토리 로딩/싱크 옵트인 |
| `open_skills_dir` | 미설정 | `open-skills` 로컬 경로 (활성화 시 기본값은 `$HOME/open-skills`) |
| `prompt_injection_mode` | `full` | 스킬 프롬프트 상세도: `full` (인라인 지시/도구 포함) 또는 `compact` (이름/설명/위치만) |

메모:

- 보안 우선 기본값: Construct는 `open_skills_enabled = true`가 아니면 `open-skills`를 클론하거나 동기화하지 **않습니다**.
- 환경 변수 오버라이드:
  - `CONSTRUCT_OPEN_SKILLS_ENABLED`는 `1/0`, `true/false`, `yes/no`, `on/off`를 받습니다.
  - `CONSTRUCT_OPEN_SKILLS_DIR`는 비어 있지 않으면 리포지토리 경로를 오버라이드합니다.
  - `CONSTRUCT_SKILLS_PROMPT_MODE`는 `full` 또는 `compact`를 받습니다.
- 활성화 플래그 우선순위: `CONSTRUCT_OPEN_SKILLS_ENABLED` → `config.toml`의 `skills.open_skills_enabled` → 기본 `false`.
- 컨텍스트가 작은 로컬 모델에서는 `prompt_injection_mode = "compact"`를 권장합니다. 시작 프롬프트 크기를 줄이면서도 스킬 파일은 필요할 때 사용 가능합니다.
- 스킬 로딩과 `construct skills install`은 모두 정적 보안 감사를 적용합니다. 심볼릭 링크, 스크립트성 파일, 위험도 높은 셸 페이로드, 안전하지 않은 마크다운 링크 트래버설을 포함한 스킬은 거부됩니다.

## `[composio]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | Composio 매니지드 OAuth 도구 활성화 |
| `api_key` | 미설정 | `composio` 도구가 사용할 Composio API 키 |
| `entity_id` | `default` | connect/execute 호출 시 보낼 기본 `user_id` |

메모:

- 하위 호환: 레거시 `enable = true`는 `enabled = true`의 별칭으로 인정됩니다.
- `enabled = false`이거나 `api_key`가 없으면 `composio` 도구가 등록되지 않습니다.
- Construct는 Composio v3 도구를 `toolkit_versions=latest`로 요청하고, 도구 실행 시 `version="latest"`를 사용해 기본 도구 리비전이 오래되는 것을 방지합니다.
- 일반적인 흐름: `connect` 호출 → 브라우저 OAuth 완료 → 원하는 도구 동작에 대해 `execute` 실행.
- Composio가 connected-account 참조 누락 에러를 반환하면 `list_accounts`(필요 시 `app` 함께)를 호출해 받은 `connected_account_id`를 `execute`에 전달하세요.

## `[cost]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | 비용 추적 활성화 |
| `daily_limit_usd` | `10.00` | 일일 지출 한도 (USD) |
| `monthly_limit_usd` | `100.00` | 월간 지출 한도 (USD) |
| `warn_at_percent` | `80` | 한도 대비 이 비율(%)에 도달하면 경고 |
| `allow_override` | `false` | `--override` 플래그로 예산 초과 허용 |

메모:

- `enabled = true`이면 런타임이 요청별 비용 추정치를 추적하고 일일/월간 한도를 적용합니다.
- `warn_at_percent` 임계점에서 경고가 발생하지만 요청은 계속 진행됩니다.
- 한도에 도달하면 요청이 거부되며, `allow_override = true` + `--override` 플래그가 있을 때만 통과됩니다.

## `[identity]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `format` | `openclaw` | 아이덴티티 포맷: `"openclaw"` (기본) 또는 `"aieos"` |
| `aieos_path` | 미설정 | AIEOS JSON 파일 경로 (워크스페이스 기준) |
| `aieos_inline` | 미설정 | 인라인 AIEOS JSON (파일 경로 대안) |

메모:

- `format = "aieos"`로 설정하고 `aieos_path` 또는 `aieos_inline` 중 하나를 채워 AIEOS / OpenClaw 아이덴티티 문서를 로드하세요.
- `aieos_path`와 `aieos_inline`은 한쪽만 설정해야 하며, 둘 다 있으면 `aieos_path`가 우선합니다.

## `[multimodal]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `max_images` | `4` | 요청당 허용되는 최대 이미지 마커 수 |
| `max_image_size_mb` | `5` | base64 인코딩 전 이미지 한 장 크기 한도 |
| `allow_remote_fetch` | `false` | 마커의 `http(s)` 이미지 URL을 가져오는 것을 허용 |

메모:

- 런타임은 사용자 메시지의 이미지 마커 문법 `[IMAGE:<source>]`를 받습니다.
- 지원 소스:
  - 로컬 파일 경로 (예: `[IMAGE:/tmp/screenshot.png]`)
  - 데이터 URI (예: `[IMAGE:data:image/png;base64,...]`)
  - `allow_remote_fetch = true`일 때만 원격 URL
- 허용 MIME 타입: `image/png`, `image/jpeg`, `image/webp`, `image/gif`, `image/bmp`.
- 활성 프로바이더가 비전을 지원하지 않으면, 이미지를 조용히 떨구지 않고 구조화된 capability 에러(`capability=vision`)로 실패합니다.

## `[browser]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | `browser_open` 도구 활성화 (스크래핑 없이 시스템 브라우저로 URL 열기) |
| `allowed_domains` | `[]` | `browser_open` 허용 도메인 (정확/서브도메인 매칭, 또는 `"*"`로 모든 공용 도메인) |
| `session_name` | 미설정 | 브라우저 세션 이름 (에이전트 브라우저 자동화용) |
| `backend` | `agent_browser` | 브라우저 자동화 백엔드: `"agent_browser"`, `"rust_native"`, `"computer_use"`, `"auto"` |
| `native_headless` | `true` | rust-native 백엔드의 헤드리스 모드 |
| `native_webdriver_url` | `http://127.0.0.1:9515` | rust-native 백엔드의 WebDriver 엔드포인트 URL |
| `native_chrome_path` | 미설정 | rust-native 백엔드용 Chrome/Chromium 실행 경로 (선택) |

### `[browser.computer_use]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `endpoint` | `http://127.0.0.1:8787/v1/actions` | computer-use 액션용 사이드카 엔드포인트 (OS 레벨 마우스/키보드/스크린샷) |
| `api_key` | 미설정 | computer-use 사이드카용 베어러 토큰 (암호화 저장) |
| `timeout_ms` | `15000` | 액션별 요청 타임아웃 (ms) |
| `allow_remote_endpoint` | `false` | 원격/공용 엔드포인트 허용 |
| `window_allowlist` | `[]` | 사이드카 정책으로 전달할 창 제목/프로세스 허용 목록 |
| `max_coordinate_x` | 미설정 | 좌표 기반 액션의 X축 경계 (선택) |
| `max_coordinate_y` | 미설정 | 좌표 기반 액션의 Y축 경계 (선택) |

메모:

- `backend = "computer_use"`이면 에이전트가 브라우저 액션을 `computer_use.endpoint`의 사이드카에 위임합니다.
- `allow_remote_endpoint = false` (기본)는 우발적인 공용 노출을 막기 위해 루프백이 아닌 엔드포인트를 거부합니다.
- 사이드카가 상호작용할 OS 창을 제한하려면 `window_allowlist`를 사용하세요.

## `[http_request]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | API 호출용 `http_request` 도구 활성화 |
| `allowed_domains` | `[]` | HTTP 요청 허용 도메인 (정확/서브도메인 매칭, 또는 `"*"`) |
| `max_response_size` | `1000000` | 최대 응답 크기 (바이트, 기본 1 MB) |
| `timeout_secs` | `30` | 요청 타임아웃 (초) |

메모:

- 기본 deny: `allowed_domains`가 비어 있으면 모든 HTTP 요청이 거부됩니다.
- 정확한 도메인 또는 서브도메인 매칭(예: `"api.example.com"`, `"example.com"`)을 쓰거나, `"*"`로 모든 공용 도메인을 허용하세요.
- 로컬/사설 타깃은 `"*"`로 설정해도 여전히 차단됩니다.

## `[google_workspace]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | `google_workspace` 도구 활성화 |
| `credentials_path` | 미설정 | Google 서비스 계정 또는 OAuth 자격 JSON 경로 |
| `default_account` | 미설정 | `gws`에 `--account`로 전달할 기본 Google 계정 |
| `allowed_services` | (빌트인 목록) | 에이전트가 접근할 수 있는 서비스: `drive`, `gmail`, `calendar`, `sheets`, `docs`, `slides`, `tasks`, `people`, `chat`, `classroom`, `forms`, `keep`, `meet`, `events` |
| `rate_limit_per_minute` | `60` | 분당 최대 `gws` 호출 |
| `timeout_secs` | `30` | 호출별 실행 타임아웃 (초) |
| `audit_log` | `false` | 모든 `gws` 호출에 대해 `INFO` 로그 라인 출력 |

### `[[google_workspace.allowed_operations]]`

이 배열이 비어 있지 않으면 정확한 일치만 통과합니다. 항목이 일치하려면 `service`, `resource`, `sub_resource`, `method`가 모두 맞아야 합니다. 배열이 비어 있으면 (기본) `allowed_services` 안의 모든 조합이 가능합니다.

| 키 | 필수 | 용도 |
|---|---|---|
| `service` | 예 | 서비스 식별자 (`allowed_services`의 항목과 일치해야 함) |
| `resource` | 예 | 최상위 리소스 이름 (Gmail은 `users`, Drive는 `files`, Calendar는 `events`) |
| `sub_resource` | 아니오 | 4세그먼트 gws 명령용 서브 리소스. Gmail은 `gws gmail users <sub_resource> <method>`로 호출하므로 Gmail 항목은 런타임에 일치하려면 `sub_resource`가 필요합니다. Drive·Calendar 등 대부분은 3세그먼트 명령이라 생략합니다. |
| `methods` | 예 | 해당 리소스/서브 리소스에서 허용되는 메서드 이름 (하나 이상) |

Gmail은 모든 작업에 `gws gmail users <sub_resource> <method>`를 씁니다. `sub_resource` 없는 Gmail 항목은 런타임에서 절대 일치하지 않습니다. Drive·Calendar는 3세그먼트라 `sub_resource`를 생략합니다.

```toml
[google_workspace]
enabled = true
default_account = "owner@company.com"
allowed_services = ["gmail"]
audit_log = true

[[google_workspace.allowed_operations]]
service = "gmail"
resource = "users"
sub_resource = "messages"
methods = ["list", "get"]

[[google_workspace.allowed_operations]]
service = "gmail"
resource = "users"
sub_resource = "drafts"
methods = ["list", "get", "create", "update"]
```

메모:

- `gws`가 설치되고 인증돼 있어야 합니다 (`gws auth login`). 설치: `npm install -g @googleworkspace/cli`.
- `credentials_path`는 호출 전에 `GOOGLE_APPLICATION_CREDENTIALS`를 설정합니다.
- `allowed_services`가 비어 있거나 생략되면 빌트인 목록을 기본값으로 씁니다.
- 검증은 동일한 `(service, resource)` 페어 중복과 단일 항목 안의 메서드 중복을 거부합니다.
- 전체 정책 모델과 검증된 워크플로 예시는 `docs/superpowers/specs/2026-03-19-google-workspace-operation-allowlist.md`를 보세요.

## `[gateway]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `host` | `127.0.0.1` | 바인드 주소 |
| `port` | `42617` | 게이트웨이 리슨 포트 |
| `require_pairing` | `true` | 베어러 인증 전에 페어링 요구 |
| `allow_public_bind` | `false` | 우발적 공용 노출 차단 |
| `path_prefix` | _(없음)_ | 리버스 프록시 배포용 URL 경로 접두사 (예: `"/construct"`) |

리버스 프록시 뒤에서 Construct를 서브 경로로 매핑할 때는 `path_prefix`를 그 경로로 설정하세요 (예: `"/construct"`). 모든 게이트웨이 라우트가 이 접두사 아래에서 서비스됩니다. 값은 `/`로 시작해야 하고 `/`로 끝나면 안 됩니다.

## `[autonomy]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `level` | `supervised` | `read_only`, `supervised`, `full` |
| `workspace_only` | `true` | 명시적으로 끄지 않으면 절대 경로 입력을 거부 |
| `allowed_commands` | _셸 실행 시 필수_ | 실행 파일 이름·명시 경로·`"*"`의 허용 목록 |
| `forbidden_paths` | 빌트인 보호 목록 | 명시적 경로 거부 목록 (시스템 경로 + 민감한 dotdir이 기본) |
| `allowed_roots` | `[]` | canonicalize 후 워크스페이스 외부에서 허용할 추가 루트 |
| `max_actions_per_hour` | `20` | 정책당 시간당 액션 예산 |
| `max_cost_per_day_cents` | `500` | 정책당 일일 지출 가드레일 |
| `require_approval_for_medium_risk` | `true` | 중간 위험 명령에 대한 승인 게이트 |
| `block_high_risk_commands` | `true` | 고위험 명령은 강제 차단 |
| `auto_approve` | `[]` | 항상 자동 승인되는 도구 동작 |
| `always_ask` | `[]` | 항상 승인이 필요한 도구 동작 |

메모:

- `level = "full"`은 셸 실행에 대한 중간 위험 승인 게이팅을 건너뜁니다 (다른 가드레일은 그대로 적용).
- 워크스페이스 외부 접근은 `workspace_only = false`라도 `allowed_roots`가 필요합니다.
- `allowed_roots`는 절대 경로, `~/...`, 워크스페이스 상대 경로를 지원합니다.
- `allowed_commands` 항목은 명령 이름(예: `"git"`), 명시적 실행 경로(예: `"/usr/bin/antigravity"`), 또는 모든 명령/경로를 허용하는 `"*"`(위험 게이트는 그대로 적용)일 수 있습니다.
- 셸 구분자/연산자 파싱은 인용 인식형입니다. 인용 안의 `;` 같은 문자는 리터럴로 처리되며 명령 구분자가 아닙니다.
- 인용되지 않은 셸 체이닝/연산자(`;`, `|`, `&&`, `||`, 백그라운드 체이닝, 리다이렉트)는 정책 검사로 그대로 적용됩니다.

```toml
[autonomy]
workspace_only = false
forbidden_paths = ["/etc", "/root", "/proc", "/sys", "~/.ssh", "~/.gnupg", "~/.aws"]
allowed_roots = ["~/Desktop/projects", "/opt/shared-repo"]
```

## `[memory]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `backend` | `sqlite` | `sqlite`, `lucid`, `markdown`, `none` |
| `auto_save` | `true` | 사용자가 명시한 입력만 영속 (어시스턴트 출력은 제외) |
| `embedding_provider` | `none` | `none`, `openai`, 커스텀 엔드포인트 |
| `embedding_model` | `text-embedding-3-small` | 임베딩 모델 ID 또는 `hint:<name>` 라우트 |
| `embedding_dimensions` | `1536` | 선택된 임베딩 모델의 예상 벡터 크기 |
| `vector_weight` | `0.7` | 하이브리드 랭킹의 벡터 가중치 |
| `keyword_weight` | `0.3` | 하이브리드 랭킹의 키워드 가중치 |

메모:

- 메모리 컨텍스트 주입은 레거시 `assistant_resp*` auto-save 키를 무시해, 옛 모델 출력 요약이 사실로 취급되는 것을 막습니다.

## `[[model_routes]]` 와 `[[embedding_routes]]`

라우트 힌트를 사용하면 통합 측은 안정적인 이름을 유지하면서 모델 ID는 진화시킬 수 있습니다.

### `[[model_routes]]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `hint` | _필수_ | 작업 힌트 이름 (예: `"reasoning"`, `"fast"`, `"code"`, `"summarize"`) |
| `provider` | _필수_ | 라우팅 대상 프로바이더 (알려진 프로바이더 이름과 일치해야 함) |
| `model` | _필수_ | 해당 프로바이더에서 사용할 모델 |
| `api_key` | 미설정 | 이 라우트 프로바이더의 API 키 오버라이드 (선택) |

### `[[embedding_routes]]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `hint` | _필수_ | 라우트 힌트 이름 (예: `"semantic"`, `"archive"`, `"faq"`) |
| `provider` | _필수_ | 임베딩 프로바이더 (`"none"`, `"openai"`, `"custom:<url>"`) |
| `model` | _필수_ | 해당 프로바이더에서 사용할 임베딩 모델 |
| `dimensions` | 미설정 | 이 라우트의 임베딩 차원 오버라이드 (선택) |
| `api_key` | 미설정 | 이 라우트 프로바이더의 API 키 오버라이드 (선택) |

```toml
[memory]
embedding_model = "hint:semantic"

[[model_routes]]
hint = "reasoning"
provider = "openrouter"
model = "provider/model-id"

[[embedding_routes]]
hint = "semantic"
provider = "openai"
model = "text-embedding-3-small"
dimensions = 1536
```

업그레이드 전략:

1. 힌트는 안정적으로 유지하세요 (`hint:reasoning`, `hint:semantic`).
2. 라우트 항목의 `model = "...new-version..."`만 갱신하세요.
3. 재시작/롤아웃 전에 `construct doctor`로 검증하세요.

자연어 설정 경로:

- 일반 에이전트 채팅 중에 평문으로 라우트 재배선을 요청할 수 있습니다.
- 런타임은 도구 `model_routing_config`로 이런 갱신(기본값, 시나리오, 위임 서브 에이전트)을 영속화하므로, 직접 TOML을 만질 필요가 없습니다.

요청 예:

- `Set conversation to provider kimi, model moonshot-v1-8k.`
- `Set coding to provider openai, model gpt-5.3-codex, and auto-route when message contains code blocks.`
- `Create a coder sub-agent using openai/gpt-5.3-codex with tools file_read,file_write,shell.`

## `[query_classification]`

자동 모델 힌트 라우팅 — 사용자 메시지를 콘텐츠 패턴으로 매핑해 `[[model_routes]]` 힌트로 라우팅합니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | 자동 쿼리 분류 활성화 |
| `rules` | `[]` | 분류 규칙 (우선순위 순으로 평가) |

`rules`의 각 규칙:

| 키 | 기본값 | 용도 |
|---|---|---|
| `hint` | _필수_ | `[[model_routes]]` 힌트 값과 일치해야 함 |
| `keywords` | `[]` | 대소문자 무시 부분 일치 |
| `patterns` | `[]` | 대소문자 구분 리터럴 일치 (코드 펜스, `"fn "` 같은 키워드용) |
| `min_length` | 미설정 | 메시지 길이 ≥ N자일 때만 매칭 |
| `max_length` | 미설정 | 메시지 길이 ≤ N자일 때만 매칭 |
| `priority` | `0` | 높은 우선순위 규칙이 먼저 평가됨 |

```toml
[query_classification]
enabled = true

[[query_classification.rules]]
hint = "reasoning"
keywords = ["explain", "analyze", "why"]
min_length = 200
priority = 10

[[query_classification.rules]]
hint = "fast"
keywords = ["hi", "hello", "thanks"]
max_length = 50
priority = 5
```

## `[channels_config]`

채널 최상위 옵션은 `channels_config` 아래에 설정합니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `message_timeout_secs` | `300` | 채널 메시지 처리의 기본 타임아웃 (초). 런타임은 도구 루프 깊이로 이 값을 스케일합니다 (최대 4배, `[pacing].message_timeout_scale_max`로 조정 가능) |

예시:

- `[channels_config.telegram]`
- `[channels_config.discord]`
- `[channels_config.whatsapp]`
- `[channels_config.linq]`
- `[channels_config.nextcloud_talk]`
- `[channels_config.email]`
- `[channels_config.nostr]`

메모:

- 기본값 `300s`는 클라우드 API보다 느린 온디바이스 LLM(Ollama)에 맞춰져 있습니다.
- 런타임 타임아웃 예산 = `message_timeout_secs * scale`. `scale = min(max_tool_iterations, cap)`이며 최소 1입니다. 기본 캡은 `4`이고 `[pacing].message_timeout_scale_max`로 오버라이드합니다.
- 이 스케일링은 첫 LLM 턴이 느리거나 재시도되는 경우에도 이후 도구 루프 턴을 끝낼 수 있게 해 거짓 타임아웃을 방지합니다.
- 클라우드 API(OpenAI, Anthropic 등)를 쓰면 `60` 이하로 줄여도 됩니다.
- `30` 미만 값은 즉시 타임아웃 회전을 막기 위해 `30`으로 클램프됩니다.
- 타임아웃 발생 시 사용자에게 다음이 전달됩니다: `⚠️ Request timed out while waiting for the model. Please try again.`
- Telegram 전용 인터럽트 동작은 `channels_config.telegram.interrupt_on_new_message`(기본 `false`)로 제어합니다. 켜져 있으면 같은 채팅의 같은 발신자가 보낸 새 메시지가 진행 중인 요청을 취소하고, 인터럽트된 사용자 컨텍스트는 보존됩니다.
- `construct channel start` 실행 중 `default_provider`, `default_model`, `default_temperature`, `api_key`, `api_url`, `reliability.*` 변경은 다음 인바운드 메시지에 핫 적용됩니다.

### `[channels_config.nostr]`

| 키 | 기본값 | 용도 |
|---|---|---|
| `private_key` | _필수_ | Nostr 개인키 (hex 또는 `nsec1…` bech32). `secrets.encrypt = true`일 때 저장 시 암호화 |
| `relays` | 메모 참고 | 릴레이 WebSocket URL 목록. 기본값은 `relay.damus.io`, `nos.lol`, `relay.primal.net`, `relay.snort.social` |
| `allowed_pubkeys` | `[]` (모두 거부) | 발신자 허용 목록 (hex 또는 `npub1…`). `"*"`로 모든 발신자 허용 |

메모:

- NIP-04(레거시 암호화 DM)와 NIP-17(기프트랩 사설 메시지)을 모두 지원합니다. 답신은 발신자 프로토콜을 자동으로 미러링합니다.
- `private_key`는 가치가 높은 비밀입니다. 운영에서는 기본값 `secrets.encrypt = true`를 유지하세요.

자세한 채널 매트릭스와 허용 목록 동작은 [channels-reference.md](channels-reference.md) *(영문)* 를 보세요.

### `[channels_config.whatsapp]`

WhatsApp은 한 설정 테이블 아래 두 가지 백엔드를 지원합니다.

Cloud API 모드 (Meta 웹훅):

| 키 | 필수 | 용도 |
|---|---|---|
| `access_token` | 예 | Meta Cloud API 베어러 토큰 |
| `phone_number_id` | 예 | Meta 전화번호 ID |
| `verify_token` | 예 | 웹훅 검증 토큰 |
| `app_secret` | 선택 | 웹훅 서명 검증(`X-Hub-Signature-256`) 활성화 |
| `allowed_numbers` | 권장 | 허용 인바운드 번호 (`[]` = 전부 거부, `"*"` = 전부 허용) |

WhatsApp Web 모드 (네이티브 클라이언트):

| 키 | 필수 | 용도 |
|---|---|---|
| `session_path` | 예 | 영속 SQLite 세션 경로 |
| `pair_phone` | 선택 | 페어 코드 흐름 전화번호 (숫자만) |
| `pair_code` | 선택 | 사용자 지정 페어 코드 (없으면 자동 생성) |
| `allowed_numbers` | 권장 | 허용 인바운드 번호 (`[]` = 전부 거부, `"*"` = 전부 허용) |

메모:

- WhatsApp Web은 빌드 플래그 `whatsapp-web`이 필요합니다.
- Cloud와 Web 필드가 둘 다 있으면 하위 호환을 위해 Cloud 모드가 이깁니다.

### `[channels_config.linq]`

iMessage·RCS·SMS용 Linq Partner V3 API 통합.

| 키 | 필수 | 용도 |
|---|---|---|
| `api_token` | 예 | Linq Partner API 베어러 토큰 |
| `from_phone` | 예 | 발신 전화번호 (E.164) |
| `signing_secret` | 선택 | HMAC-SHA256 서명 검증용 웹훅 서명 시크릿 |
| `allowed_senders` | 권장 | 허용 인바운드 전화번호 (`[]` = 전부 거부, `"*"` = 전부 허용) |

메모:

- 웹훅 엔드포인트는 `POST /linq`.
- `CONSTRUCT_LINQ_SIGNING_SECRET`이 설정돼 있으면 `signing_secret`을 오버라이드합니다.
- 서명은 `X-Webhook-Signature`와 `X-Webhook-Timestamp` 헤더를 씁니다. 오래된 타임스탬프(>300s)는 거부됩니다.
- 전체 설정 예시는 [channels-reference.md](channels-reference.md) *(영문)* 를 보세요.

### `[channels_config.nextcloud_talk]`

네이티브 Nextcloud Talk 봇 통합 (웹훅 수신 + OCS 송신 API).

| 키 | 필수 | 용도 |
|---|---|---|
| `base_url` | 예 | Nextcloud 베이스 URL (예: `https://cloud.example.com`) |
| `app_token` | 예 | OCS 베어러 인증에 사용할 봇 앱 토큰 |
| `webhook_secret` | 선택 | 웹훅 서명 검증 활성화 |
| `allowed_users` | 권장 | 허용 Nextcloud 액터 ID (`[]` = 전부 거부, `"*"` = 전부 허용) |
| `bot_name` | 선택 | Nextcloud Talk에서 봇 표시 이름 (예: `"construct"`). 봇 자신의 메시지를 걸러 피드백 루프를 막는 데 사용 |

메모:

- 웹훅 엔드포인트는 `POST /nextcloud-talk`.
- `CONSTRUCT_NEXTCLOUD_TALK_WEBHOOK_SECRET`이 설정돼 있으면 `webhook_secret`을 오버라이드합니다.
- 셋업과 트러블슈팅은 [nextcloud-talk-setup.md](../../setup-guides/nextcloud-talk-setup.md) *(영문)* 를 참고하세요.

## `[hardware]`

물리 세계 접근(STM32, 프로브, 시리얼)을 위한 하드웨어 위저드 설정.

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | 하드웨어 접근 활성화 여부 |
| `transport` | `none` | 트랜스포트 모드: `"none"`, `"native"`, `"serial"`, `"probe"` |
| `serial_port` | 미설정 | 시리얼 포트 경로 (예: `"/dev/ttyACM0"`) |
| `baud_rate` | `115200` | 시리얼 보레이트 |
| `probe_target` | 미설정 | 프로브 타깃 칩 (예: `"STM32F401RE"`) |
| `workspace_datasheets` | `false` | 워크스페이스 데이터시트 RAG 활성화 (PDF 회로도를 인덱스해 AI 핀 룩업에 사용) |

메모:

- USB-시리얼 연결에는 `transport = "serial"`과 `serial_port`를 사용하세요.
- 디버그 프로브 플래싱(예: ST-Link)에는 `transport = "probe"`와 `probe_target`을 사용하세요.
- 프로토콜 디테일은 [hardware-peripherals-design.md](../../hardware/hardware-peripherals-design.md) *(영문)* 를 참고하세요.

## `[peripherals]`

상위 레벨의 주변 보드 설정. 활성화되면 보드가 에이전트 도구가 됩니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | 주변 보드 지원 활성화 (보드가 에이전트 도구로) |
| `boards` | `[]` | 보드 설정 목록 |
| `datasheet_dir` | 미설정 | RAG 검색용 데이터시트 디렉터리 (워크스페이스 기준) |

`boards`의 각 항목:

| 키 | 기본값 | 용도 |
|---|---|---|
| `board` | _필수_ | 보드 타입: `"nucleo-f401re"`, `"rpi-gpio"`, `"esp32"` 등 |
| `transport` | `serial` | 트랜스포트: `"serial"`, `"native"`, `"websocket"` |
| `path` | 미설정 | 시리얼 경로: `"/dev/ttyACM0"`, `"/dev/ttyUSB0"` |
| `baud` | `115200` | 시리얼 보레이트 |

```toml
[peripherals]
enabled = true
datasheet_dir = "docs/datasheets"

[[peripherals.boards]]
board = "nucleo-f401re"
transport = "serial"
path = "/dev/ttyACM0"
baud = 115200

[[peripherals.boards]]
board = "rpi-gpio"
transport = "native"
```

메모:

- 보드 이름의 `.md`/`.txt` 데이터시트(예: `nucleo-f401re.md`, `rpi-gpio.md`)를 `datasheet_dir`에 두면 RAG가 검색합니다.
- 보드 프로토콜과 펌웨어 메모는 [hardware-peripherals-design.md](../../hardware/hardware-peripherals-design.md) *(영문)* 를 참고하세요.

## `[kumiho]`

Kumiho는 Construct의 정식 영속 그래프 메모리 백엔드입니다. 런타임은 모든 비내부 에이전트에 Kumiho MCP 서버와 세션 부트스트랩 시스템 프롬프트를 자동 주입합니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `true` | 비내부 에이전트에 Kumiho 메모리 주입 활성화 |
| `mcp_path` | `~/.construct/kumiho/run_kumiho_mcp.py` | MCP 러너 스크립트의 절대 경로 |
| `space_prefix` | `Construct` | 메모리를 스코프하는 프로젝트/스페이스 접두사 (예: `Construct/AgentPool/`) |
| `api_url` | `https://api.kumiho.cloud` | 에이전트 관리 프록시가 사용하는 Kumiho FastAPI REST API 베이스 URL |
| `memory_project` | (기본값) | 사용자 메모리·세션·압축용 프로젝트 |
| `harness_project` | (기본값) | 스킬·운영 데이터·ClawHub 인스톨용 프로젝트 |

메모:

- Kumiho를 설치하지 않은 배포에서는 `enabled = false`로 끄세요.
- `api_url`은 대시보드/API 프록시 `GET /api/kumiho/{*path}`에서 사용됩니다.
- `space_prefix` 아래 Construct가 사용하는 네임스페이스: `AgentPool`, `Plans`, `Sessions`, `Goals`, `AgentTrust`, `ClawHub`, `Teams`, `CognitiveMemory/Skills`.

## `[operator]`

Operator는 14개의 단계 타입과 4개 이상의 오케스트레이션 패턴으로 선언적 YAML 워크플로를 굴리는 Python MCP 서버입니다. 모든 비내부 에이전트에 자동 주입됩니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `true` | 비내부 에이전트에 Operator 주입 활성화 |
| `mcp_path` | `~/.construct/operator_mcp/run_operator_mcp.py` | MCP 러너 스크립트의 절대 경로 |
| `max_tool_iterations` | (operator 오버라이드) | operator가 활성화된 세션에 대해 `agent.max_tool_iterations` 오버라이드 (operator 작업은 본질적으로 다단계) |

메모:

- 워크플로 체크포인트는 `~/.construct/workflow_checkpoints/`에 기록됩니다.
- 에이전트별 RunLog JSONL 감사 트레일은 `~/.construct/operator_mcp/runlogs/`에 기록됩니다.
- 현재 지원하는 단계 타입: `agent`, `shell`, `output`, `notify`, `a2a`, `conditional`, `parallel`, `goto`, `human_approval`, `human_input`, `map_reduce`, `supervisor`, `group_chat`, `handoff`.

## `[clawhub]`

ClawHub 스킬/템플릿 마켓플레이스 통합.

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `true` | ClawHub 통합 활성화 |
| `api_token` | 미설정 | ClawHub API 토큰 (`clh_…`) — 게시할 때만 필요 |
| `api_url` | `https://clawhub.ai` | ClawHub API 베이스 URL |

메모:

- 익명 브라우징과 설치는 토큰 없이도 동작합니다.
- 대시보드는 `Skills` 화면에 ClawHub를 띄웁니다. REST 엔드포인트: `GET /api/clawhub/search`, `/trending`, `/skills/{slug}`, `POST /api/clawhub/install/{slug}`.

## `[trust]`

도메인/도구에 대한 트러스트 점수 (회귀 감지). 에이전트 템플릿 트러스트 점수는 Kumiho의 `Construct/AgentTrust/`에 저장되며, 이 설정 섹션과는 별개입니다.

| 키 | 기본값 | 용도 |
|---|---|---|
| `initial_score` | `0.8` | 새 도메인의 초기 트러스트 점수 |
| `decay_half_life_days` | `30` | 트러스트 감쇠 반감기 (일) |
| `regression_threshold` | `0.5` | 이 값 미만이면 회귀로 표시 |
| `correction_penalty` | `0.05` | 정정 이벤트당 점수 패널티 |
| `success_boost` | `0.01` | 성공 이벤트당 점수 부스트 |

## `[verifiable_intent]`

커머스 도구 호출에 대한 Verifiable Intent (VI) 자격 검증.

| 키 | 기본값 | 용도 |
|---|---|---|
| `enabled` | `false` | 커머스 호출에 VI 자격 검증 활성화 |
| `strictness` | `strict` | 제약 평가 방식: `strict` (알 수 없는 제약 타입에서 fail-closed) 또는 `permissive` (경고만 남기고 건너뜀) |

## 보안 관련 기본값

- 채널 허용 목록은 deny-by-default (`[]`은 전부 거부)
- 게이트웨이는 기본적으로 페어링 요구
- 공용 바인드는 기본 비활성화
- Kumiho와 Operator MCP 서버는 기본 활성화. 사이드카를 띄우지 않는 배포에서는 명시적으로 끄세요.

## 검증 명령

설정 편집 후:

```bash
construct status
construct doctor
construct channel doctor
construct service restart
```

## 관련 문서

- [channels-reference.md](channels-reference.md) *(영문)*
- [providers-reference.md](providers-reference.md) *(영문)*
- [operations-runbook.md](../../ops/operations-runbook.md) *(영문)*
- [troubleshooting.md](../../ops/troubleshooting.md)
