# Construct 명령어 참조

이 문서는 현재 CLI 표면(`construct --help`)에서 그대로 추린 참조입니다.

마지막 검증: **2026년 4월 21일**.

`construct` 바이너리에는 `construct gateway` / `construct daemon`이 `http://127.0.0.1:42617`에 띄우는 React/TypeScript 웹 대시보드가 함께 들어 있습니다. 운영 표면 대부분은 CLI와 대시보드 양쪽에서 가능하지만, 이 문서는 CLI 쪽만 다룹니다.

<!-- TODO screenshot: 대시보드 최상위 명령 메뉴/내비게이션 -->
![대시보드 최상위 명령 메뉴/내비게이션](../../../../assets/reference/commands-reference-01-dashboard-menu.png)

## 최상위 명령

| 명령 | 용도 |
|---|---|
| `onboard` | 워크스페이스/설정을 빠르게 또는 안내형으로 초기화 |
| `agent` | 대화형 채팅 또는 단발성 메시지 모드 실행 |
| `gateway` | 게이트웨이 서버 시작/관리 (웹 대시보드 + 웹훅 + WebSocket) |
| `acp` | stdio 위에서 ACP(Agent Control Protocol) 서버 실행 |
| `daemon` | 슈퍼바이즈드 런타임 시작 (게이트웨이 + 채널 + 하트비트 + 크론 스케줄러) |
| `service` | OS 서비스 라이프사이클 관리 (launchd/systemd/OpenRC) |
| `doctor` | 진단 및 신선도 점검 |
| `status` | 현재 설정과 시스템 요약 출력 |
| `estop` | 비상 정지 단계 발동/해제, estop 상태 확인 |
| `cron` | 예약 작업 관리 |
| `models` | 프로바이더 모델 카탈로그 갱신/조회 |
| `providers` | 프로바이더 ID, 별칭, 활성 프로바이더 목록 |
| `channel` | 채널 관리 및 채널 헬스 체크 |
| `integrations` | 통합 정보 조회 |
| `skills` | 스킬 목록/설치/제거/감사 |
| `migrate` | 외부 런타임에서 가져오기 (현재 OpenClaw 지원) |
| `auth` | 프로바이더 구독 인증 프로필 관리 (OAuth, 토큰 기반) |
| `memory` | 에이전트 메모리 항목 관리 (목록·조회·통계·삭제) |
| `config` | 머신 판독 가능한 설정 스키마 내보내기 |
| `update` | 업데이트 확인 및 적용 (롤백 가능한 6단계 파이프라인) |
| `self-test` | 설치 검증용 자가 진단 실행 |
| `completions` | 셸 자동완성 스크립트를 stdout으로 출력 |
| `hardware` | USB 하드웨어 탐지 및 분석 |
| `peripheral` | 주변 장치 구성 및 펌웨어 플래싱 |
| `desktop` | 데스크톱 컴패니언 앱(Tauri 셸) 실행/설치 |
| `plugin` | WASM 플러그인 관리 (`plugins-wasm` 피처로 빌드된 경우만) |

## 명령 그룹

### `onboard`

- `construct onboard`
- `construct onboard --channels-only`
- `construct onboard --force`
- `construct onboard --reinit`
- `construct onboard --api-key <KEY> --provider <ID> --memory <sqlite|lucid|markdown|none>`
- `construct onboard --api-key <KEY> --provider <ID> --model <MODEL_ID> --memory <sqlite|lucid|markdown|none>`
- `construct onboard --api-key <KEY> --provider <ID> --model <MODEL_ID> --memory <sqlite|lucid|markdown|none> --force`

`onboard`의 안전 동작:

- `config.toml`이 이미 있으면 두 가지 모드를 제시합니다.
  - 풀 온보딩 (`config.toml` 덮어쓰기)
  - 프로바이더만 갱신 (채널·터널·메모리·훅 등 기존 설정은 유지하면서 프로바이더/모델/API 키만 변경)
- 비대화형 환경에서 `config.toml`이 이미 존재하면 안전하게 거부합니다. `--force`를 줘야 진행됩니다.
- 채널 토큰/허용 목록만 갈아끼우려면 `construct onboard --channels-only`를 쓰세요.
- 처음부터 다시 시작하려면 `construct onboard --reinit`. 기존 설정 디렉터리를 타임스탬프 접미사와 함께 백업한 뒤 새 설정을 만듭니다.

### `agent`

- `construct agent`
- `construct agent -m "Hello"`
- `construct agent --provider <ID> --model <MODEL> --temperature <0.0-2.0>`
- `construct agent --peripheral <board:path>`

팁:

- 대화형 채팅에서 자연어로 라우팅 변경을 요청할 수 있습니다 (예: "대화는 kimi, 코딩은 gpt-5.3-codex"). 어시스턴트가 `model_routing_config` 도구로 영속화합니다.

### `acp`

- `construct acp`
- `construct acp --max-sessions <N>`
- `construct acp --session-timeout <SECONDS>`

IDE 및 도구 통합용 ACP(Agent Control Protocol) 서버를 띄웁니다.

- stdin/stdout 위에서 JSON-RPC 2.0 사용
- 지원 메서드: `initialize`, `session/new`, `session/prompt`, `session/stop`
- 에이전트의 추론·도구 호출·콘텐츠를 알림 형태로 실시간 스트리밍
- 기본 최대 세션: 10
- 기본 세션 타임아웃: 3600초 (1시간)

<!-- TODO screenshot: localhost:42617의 게이트웨이가 서비스하는 임베디드 Construct 대시보드를 띄운 브라우저 -->
![localhost:42617의 게이트웨이가 서비스하는 임베디드 Construct 대시보드를 띄운 브라우저](../../../../assets/reference/commands-reference-02-dashboard-browser.png)

### `gateway` / `daemon`

- `construct gateway` / `construct gateway start [--host <HOST>] [--port <PORT>]`
- `construct gateway restart [--host <HOST>] [--port <PORT>]`
- `construct gateway get-paircode [--new]`
- `construct daemon [--host <HOST>] [--port <PORT>]`

메모:

- `gateway`는 임베디드 React 웹 대시보드를 `http://<host>:<port>/`(기본 `127.0.0.1:42617`)에 띄우고, REST API·SSE(`/api/events`)·WebSocket 엔드포인트(`/ws/chat`, `/ws/canvas/{id}`, `/ws/nodes`)도 제공합니다.
- `/ws/chat`은 턴 시작에 `{"type":"message","content":"..."}`, 활성 턴
  조정에 `{"type":"steer","content":"..."}`, 활성 턴 취소에
  `{"type":"stop"}`을 받습니다.
- `daemon`은 게이트웨이와 설정된 모든 채널·하트비트·크론 스케줄러를 함께 굴립니다. 부팅 후에도 살려 두려면 `construct service install` + `construct service start`를 쓰세요.
- 페어링: `construct gateway get-paircode`가 현재 디바이스 페어 코드를 출력합니다 (`--new`로 회전).

### `estop`

- `construct estop` (`kill-all` 발동)
- `construct estop --level network-kill`
- `construct estop --level domain-block --domain "*.chase.com" [--domain "*.paypal.com"]`
- `construct estop --level tool-freeze --tool shell [--tool browser]`
- `construct estop status`
- `construct estop resume`
- `construct estop resume --network`
- `construct estop resume --domain "*.chase.com"`
- `construct estop resume --tool shell`
- `construct estop resume --otp <123456>`

메모:

- `estop` 명령은 `[security.estop].enabled = true`가 필요합니다.
- `[security.estop].require_otp_to_resume = true`인 경우 `resume`은 OTP 검증을 요구합니다.
- `--otp`를 생략하면 OTP 입력 프롬프트가 자동으로 뜹니다.

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

메모:

- 스케줄/크론을 변경하는 동작은 `cron.enabled = true`가 필요합니다.
- 스케줄 생성(`create` / `add` / `once`)에 들어가는 셸 명령 페이로드는 작업이 영속되기 전에 보안 정책으로 검증됩니다.

### `models`

- `construct models refresh`
- `construct models refresh --provider <ID>`
- `construct models refresh --all`
- `construct models refresh --force`
- `construct models list [--provider <ID>]`
- `construct models set <MODEL_ID>`
- `construct models status`

`models refresh`가 현재 라이브 카탈로그 갱신을 지원하는 프로바이더 ID: `openrouter`, `openai`, `anthropic`, `groq`, `mistral`, `deepseek`, `xai`, `together-ai`, `gemini`, `ollama`, `llamacpp`, `sglang`, `vllm`, `astrai`, `venice`, `fireworks`, `cohere`, `moonshot`, `glm`, `zai`, `qwen`, `nvidia`.

- `models list`는 결정된 프로바이더의 캐시된 모델 카탈로그를 출력합니다.
- `models set`은 `~/.construct/config.toml`에 `default_model`을 기록합니다.
- `models status`는 활성 모델 설정과 캐시 신선도를 출력합니다.

<!-- TODO screenshot: `construct doctor` 진단 결과를 보기 좋게 출력한 터미널 -->
![construct doctor 진단 결과를 보기 좋게 출력한 터미널](../../../../assets/reference/commands-reference-03-doctor-output.png)

### `doctor`

- `construct doctor`
- `construct doctor models [--provider <ID>] [--use-cache]`
- `construct doctor traces [--limit <N>] [--event <TYPE>] [--contains <TEXT>]`
- `construct doctor traces --id <TRACE_ID>`

`doctor traces`는 `observability.runtime_trace_path`에서 런타임 도구/모델 진단 정보를 읽습니다.

### `channel`

- `construct channel list`
- `construct channel start`
- `construct channel doctor`
- `construct channel bind-telegram <IDENTITY>`
- `construct channel add <type> <json>`
- `construct channel remove <name>`

채널 서버가 동작 중일 때 인앱 명령(Telegram/Discord):

- `/models`
- `/models <provider>`
- `/model`
- `/model <model-id>`
- `/new`

채널 런타임은 `config.toml`도 감시하면서 다음 항목을 핫 적용합니다.

- `default_provider`
- `default_model`
- `default_temperature`
- 기본 프로바이더의 `api_key` / `api_url`
- `reliability.*` 프로바이더 재시도 설정

`add/remove`는 현재 매니지드 셋업/수동 설정 경로로 안내합니다 (전체 선언적 변경 지원은 아직 아닙니다).

### `integrations`

- `construct integrations info <name>`

### `skills`

- `construct skills list`
- `construct skills audit <source_or_name>`
- `construct skills install <source>`
- `construct skills remove <name>`

`<source>`는 git 원격(`https://...`, `http://...`, `ssh://...`, `git@host:owner/repo.git`) 또는 로컬 파일시스템 경로를 받습니다.

`skills install`은 스킬을 받아들이기 전에 항상 내장 정적 보안 감사를 실행합니다. 차단 항목:

- 스킬 패키지 안의 심볼릭 링크
- 스크립트성 파일 (`.sh`, `.bash`, `.zsh`, `.ps1`, `.bat`, `.cmd`)
- 위험도 높은 명령 스니펫 (예: pipe-to-shell 형태)
- 스킬 루트를 벗어나거나, 원격 마크다운을 가리키거나, 스크립트 파일을 타깃으로 하는 마크다운 링크

후보 스킬 디렉터리(또는 이미 설치된 스킬을 이름으로) 검증을 수동으로 돌리려면 `skills audit`을 사용하세요.

스킬 매니페스트(`SKILL.toml`)는 `prompts`와 `[[tools]]`를 지원합니다. 둘 다 런타임에 에이전트 시스템 프롬프트로 주입되므로, 모델이 스킬 파일을 직접 읽지 않아도 스킬 지시를 따를 수 있습니다.

### `migrate`

- `construct migrate openclaw [--source <path>] [--dry-run]`

### `auth`

프로바이더 구독 인증 프로필 관리 (예: `openai-codex` / `gemini`의 OAuth, Anthropic 구독 셋업 토큰).

- `construct auth login --provider <openai-codex|gemini> [--profile <name>] [--device-code] [--import <PATH>]`
- `construct auth paste-redirect --provider openai-codex [--profile <name>] [--input <URL_OR_CODE>]`
- `construct auth paste-token --provider anthropic [--profile <name>] [--token <VALUE>] [--auth-kind <authorization|api-key>]`
- `construct auth setup-token --provider anthropic [--profile <name>]` (`paste-token`의 대화형 별칭)
- `construct auth refresh --provider openai-codex [--profile <name>]`
- `construct auth use --provider <ID> --profile <name>`
- `construct auth logout --provider <ID> [--profile <name>]`
- `construct auth list`
- `construct auth status`

메모:

- `--import`는 현재 `openai-codex`에서만 지원되며, 경로를 생략하면 `~/.codex/auth.json`을 기본값으로 씁니다.
- `use`는 이후 요청에 사용할 활성 프로필을 지정합니다.
- `status`는 프로바이더별 활성 프로필과, 가능한 경우 토큰 만료 정보를 출력합니다.

### `memory`

에이전트 메모리 항목을 점검하고 관리합니다.

- `construct memory stats`
- `construct memory list [--category <name>] [--session <id>] [--limit <N>] [--offset <N>]`
- `construct memory get <KEY>`
- `construct memory clear [--key <KEY>] [--category <CATEGORY>] [--yes]`

메모:

- `get`과 `clear --key`는 메모리 키에 대해 접두사 매칭을 지원합니다.
- `--key`/`--category` 없이 `clear`를 호출하면 모든 항목을 비웁니다 (확인 없이 진행하려면 `--yes` 필요).
- `[memory]` 아래 설정된 로컬 메모리 백엔드를 대상으로 합니다. Kumiho 그래프 메모리 브라우저는 웹 대시보드의 `Assets` / `Memory` 화면이나 `kumiho` 프록시(`/api/kumiho/*`)를 사용하세요.

### `config`

- `construct config schema`

`config schema`는 전체 `config.toml` 계약에 대한 JSON Schema(draft 2020-12)를 stdout으로 출력합니다.

### `completions`

- `construct completions bash`
- `construct completions fish`
- `construct completions zsh`
- `construct completions powershell`
- `construct completions elvish`

`completions`는 의도적으로 stdout 전용입니다. 로그/경고가 섞이지 않으므로 스크립트를 곧바로 source할 수 있습니다.

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

- `construct update` — 최신 릴리스를 받아 설치
- `construct update --check` — 업데이트 확인만, 설치하지 않음
- `construct update --force` — 확인 프롬프트 없이 설치
- `construct update --version <X.Y.Z>` — 특정 버전 설치

업데이터는 6단계 파이프라인(preflight, download, backup, validate, swap, smoke test)으로 동작하며, 실패 시 자동 롤백됩니다.

### `self-test`

- `construct self-test` — 풀 스위트 (네트워크 점검 포함: 게이트웨이 헬스, 메모리 라운드 트립)
- `construct self-test --quick` — 오프라인 검증을 위해 네트워크 점검 생략

### `desktop`

- `construct desktop` — Construct 컴패니언 데스크톱 앱 실행 (로컬 게이트웨이 `http://127.0.0.1:42617/_app/`을 가리키는 Tauri 셸)
- `construct desktop --install` — 플랫폼에 맞는 사전 빌드 컴패니언 앱을 받아 설치

### `plugin`

`plugins-wasm` Cargo 피처로 빌드된 경우에만 사용할 수 있습니다.

- `construct plugin list`
- `construct plugin install <source>` (디렉터리 또는 URL)
- `construct plugin remove <name>`
- `construct plugin info <name>`

## 검증 팁

문서를 현재 바이너리와 빠르게 대조하려면:

```bash
construct --help
construct <command> --help
```
