# 채널 참조

이 문서는 Revka의 채널 설정에 대한 정식 참조입니다.

암호화된 Matrix 방은 별도 런북도 함께 보세요.

- [Matrix E2EE 가이드](../../security/matrix-e2ee-guide.md) *(영문)*

## 빠른 길찾기

- 채널별 전체 설정 참조: [4. 채널별 설정 예시](#4-채널별-설정-예시)로 이동.
- 답신 없음 진단 흐름: [6. 트러블슈팅 체크리스트](#6-트러블슈팅-체크리스트)로 이동.
- Matrix 암호화 방 도움: [Matrix E2EE 가이드](../../security/matrix-e2ee-guide.md) *(영문)*.
- Nextcloud Talk 봇 셋업: [Nextcloud Talk 셋업](../../setup-guides/nextcloud-talk-setup.md) *(영문)*.
- 배포/네트워크 가정 (폴링 vs 웹훅): [네트워크 배포](../../ops/network-deployment.md) *(영문)*.

## FAQ: Matrix 셋업은 통과했는데 답신이 없어요

가장 자주 보는 증상입니다 (#499 이슈와 같은 부류). 다음을 순서대로 점검하세요.

1. **허용 목록 불일치**: `allowed_users`에 발신자가 없거나 비어 있음.
2. **잘못된 방 타깃**: 봇이 설정된 `room_id` / 별칭 대상 방에 합류하지 않음.
3. **토큰/계정 불일치**: 토큰은 유효하지만 다른 Matrix 계정 소속.
4. **E2EE 디바이스 아이덴티티 누락**: `whoami`가 `device_id`를 반환하지 않고, 설정에도 없음.
5. **키 공유/신뢰 누락**: 방 키가 봇 디바이스에 공유되지 않아 암호화 이벤트를 복호화할 수 없음.
6. **런타임 상태 stale**: 설정은 바뀌었는데 `revka daemon`을 재시작하지 않음.

---

## 1. 설정 네임스페이스

모든 채널 설정은 `~/.revka/config.toml`의 `channels_config` 아래에 들어갑니다.

```toml
[channels_config]
cli = true
```

각 채널은 하위 테이블(예: `[channels_config.telegram]`)을 만들면 활성화됩니다.

## 인앱 런타임 모델 전환 (Telegram / Discord)

`revka channel start` (또는 데몬 모드)로 실행 중일 때, Telegram과 Discord는 발신자 단위 런타임 전환을 지원합니다.

- `/models` — 사용 가능한 프로바이더와 현재 선택 표시
- `/models <provider>` — 현재 발신자 세션에 대해 프로바이더 전환
- `/model` — 현재 모델과 캐시된 모델 ID 표시 (있다면)
- `/model <model-id>` — 현재 발신자 세션에 대해 모델 전환
- `/new` — 대화 히스토리를 비우고 새 세션 시작

메모:

- 프로바이더나 모델 전환은 모델 간 컨텍스트 오염을 막기 위해 해당 발신자의 인메모리 대화 히스토리만 비웁니다.
- `/new`는 프로바이더/모델은 그대로 두고 대화 히스토리만 비웁니다.
- 모델 캐시 미리보기는 `revka models refresh --provider <ID>`에서 옵니다.
- 위 명령은 런타임 채팅 명령이지 CLI 서브커맨드가 아닙니다.

## 인바운드 이미지 마커 프로토콜

Revka는 인라인 메시지 마커로 멀티모달 입력을 받습니다.

- 문법: `[IMAGE:<source>]`
- `<source>`는 다음 중 하나:
  - 로컬 파일 경로
  - 데이터 URI (`data:image/...;base64,...`)
  - 원격 URL — `[multimodal].allow_remote_fetch = true`일 때만

운영 메모:

- 마커 파싱은 프로바이더 호출 전에 user 역할 메시지에 적용됩니다.
- 프로바이더 capability는 런타임에 강제됩니다. 선택된 프로바이더가 비전을 지원하지 않으면, 구조화된 capability 에러(`capability=vision`)로 실패합니다.
- Linq 웹훅의 `image/*` MIME 타입 `media` 파트는 자동으로 위 마커 형식으로 변환됩니다.

## 채널 매트릭스

### 빌드 피처 토글 (`channel-matrix`, `channel-lark`)

Matrix와 Lark 지원은 컴파일 타임에 제어됩니다.

- 기본 빌드는 가벼우며(`default = []`) Matrix/Lark가 들어 있지 않습니다.
- 하드웨어 지원만 켜는 일반적인 로컬 점검:

```bash
cargo check --features hardware
```

- 필요할 때 Matrix를 명시적으로 활성화:

```bash
cargo check --features hardware,channel-matrix
```

- 필요할 때 Lark를 명시적으로 활성화:

```bash
cargo check --features hardware,channel-lark
```

`[channels_config.matrix]`, `[channels_config.lark]`, `[channels_config.feishu]`가 있는데 해당 피처가 컴파일되지 않은 빌드에서는 `revka channel list`, `revka channel doctor`, `revka channel start`가 그 채널을 의도적으로 건너뛰었다고 보고합니다.

---

## 2. 전송 모드 한눈에

| 채널 | 수신 모드 | 공용 인바운드 포트 필요? |
|---|---|---|
| CLI | 로컬 stdin/stdout | 아니오 |
| Telegram | 폴링 | 아니오 |
| Discord | 게이트웨이/WebSocket | 아니오 |
| Slack | Events API | 아니오 (토큰 기반 채널 흐름) |
| Mattermost | 폴링 | 아니오 |
| Matrix | sync API (E2EE 지원) | 아니오 |
| Signal | signal-cli HTTP 브리지 | 아니오 (로컬 브리지 엔드포인트) |
| WhatsApp | 웹훅 (Cloud API) 또는 WebSocket (Web 모드) | Cloud API: 예 (공용 HTTPS 콜백), Web 모드: 아니오 |
| Nextcloud Talk | 웹훅 (`/nextcloud-talk`) | 예 (공용 HTTPS 콜백) |
| Webhook | 게이트웨이 엔드포인트 (`/webhook`) | 일반적으로 예 |
| Email | IMAP 폴링 + SMTP 송신 | 아니오 |
| IRC | IRC 소켓 | 아니오 |
| Lark | WebSocket (기본) 또는 웹훅 | 웹훅 모드만 |
| Feishu | WebSocket (기본) 또는 웹훅 | 웹훅 모드만 |
| DingTalk | 스트림 모드 | 아니오 |
| QQ | 봇 게이트웨이 | 아니오 |
| Linq | 웹훅 (`/linq`) | 예 (공용 HTTPS 콜백) |
| iMessage | 로컬 통합 | 아니오 |
| Nostr | 릴레이 WebSocket (NIP-04 / NIP-17) | 아니오 |

---

## 3. 허용 목록 동작

인바운드 발신자 허용 목록을 가진 채널의 동작:

- 빈 허용 목록: 모든 인바운드 메시지 거부.
- `"*"`: 모든 인바운드 발신자 허용 (임시 검증용으로만 사용).
- 명시 목록: 명시된 발신자만 허용.

채널별 필드 이름:

- `allowed_users` (Telegram/Discord/Slack/Mattermost/Matrix/IRC/Lark/Feishu/DingTalk/QQ/Nextcloud Talk)
- `allowed_from` (Signal)
- `allowed_numbers` (WhatsApp)
- `allowed_senders` (Email/Linq)
- `allowed_contacts` (iMessage)
- `allowed_pubkeys` (Nostr)

---

## 4. 채널별 설정 예시

### 4.1 Telegram

```toml
[channels_config.telegram]
bot_token = "123456:telegram-token"
allowed_users = ["*"]
stream_mode = "off"               # 선택: off | partial
draft_update_interval_ms = 1000   # 선택: partial 스트리밍의 편집 스로틀
mention_only = false              # 선택: 그룹에서 @멘션 요구
interrupt_on_new_message = false  # 선택: 같은 발신자/같은 채팅의 진행 중 요청 취소
```

Telegram 메모:

- `interrupt_on_new_message = true`이면 인터럽트된 사용자 턴을 대화 히스토리에 보존하고 가장 최근 메시지로 생성을 다시 시작합니다.
- 인터럽트 범위는 엄격합니다 (같은 채팅의 같은 발신자). 다른 채팅의 메시지는 독립적으로 처리됩니다.

### 4.2 Discord

```toml
[channels_config.discord]
bot_token = "discord-bot-token"
guild_id = "123456789012345678"   # 선택
allowed_users = ["*"]
listen_to_bots = false
mention_only = false
stream_mode = "multi_message"     # 선택: off | partial | multi_message (위저드 기본 multi_message)
draft_update_interval_ms = 1000   # 선택: partial 스트리밍의 편집 스로틀
multi_message_delay_ms = 800      # 선택: multi_message 모드의 단락 송신 간격
```

Discord 메모:

- `stream_mode = "partial"`은 편집 가능한 초안 메시지를 보내고, LLM이 응답을 스트리밍하는 동안 토큰 단위로 갱신한 뒤 전체 텍스트로 마무리합니다.
- `stream_mode = "multi_message"`는 응답을 단락 경계(`\n\n`)에서 잘라 별도 메시지로 점진 전달합니다. 각 단락은 완성되는 즉시 Discord에 등장합니다.
- `draft_update_interval_ms`는 partial 모드의 편집 스로틀을 제어합니다 (기본 1000ms).
- `multi_message_delay_ms`는 multi_message 모드에서 Discord 레이트 리밋을 피하기 위한 단락 송신 간 최소 지연입니다 (기본 800ms).
- 코드 펜스는 multi_message 모드에서 절대 메시지에 걸쳐 분할되지 않습니다.

### 4.3 Slack

```toml
[channels_config.slack]
bot_token = "xoxb-..."
app_token = "xapp-..."             # 선택
channel_id = "C1234567890"         # 선택: 단일 채널. 비우거나 "*"이면 접근 가능한 모든 채널
channel_ids = ["C1234567890"]      # 선택: 명시 채널 목록. channel_id보다 우선
allowed_users = ["*"]
```

Slack 리슨 동작:

- `channel_ids = ["C123...", "D456..."]`: 명시된 채널/DM만 리슨.
- `channel_id = "C123..."`: 그 채널만 리슨.
- `channel_id = "*"` 또는 생략: 접근 가능한 모든 채널을 자동 발견해 리슨.

### 4.4 Mattermost

```toml
[channels_config.mattermost]
url = "https://mm.example.com"
bot_token = "mattermost-token"
channel_id = "channel-id"          # 리슨에 필수
allowed_users = ["*"]
```

### 4.5 Matrix

```toml
[channels_config.matrix]
homeserver = "https://matrix.example.com"
access_token = "syt_..."
user_id = "@revka:matrix.example.com"   # 선택, E2EE에 권장
device_id = "DEVICEID123"                  # 선택, E2EE에 권장
room_id = "!room:matrix.example.com"       # 또는 방 별칭 (#ops:matrix.example.com)
allowed_users = ["*"]
stream_mode = "partial"                    # 선택: off | partial | multi_message (위저드 기본 partial)
draft_update_interval_ms = 1500            # 선택: partial 스트리밍의 편집 스로틀
multi_message_delay_ms = 800               # 선택: multi_message 모드의 단락 송신 간격
```

Matrix 스트리밍 메모:

- `stream_mode = "partial"`은 편집 가능한 초안 메시지를 보내고, LLM 스트리밍 중 Matrix `m.replace` 편집으로 토큰 단위로 갱신합니다.
- `stream_mode = "multi_message"`는 응답을 단락 경계(`\n\n`)에서 잘라 별도 메시지로 전달합니다. 코드 펜스는 메시지를 가로질러 분할되지 않습니다.
- `draft_update_interval_ms`는 partial 모드의 편집 스로틀을 제어합니다 (기본 1500ms — E2EE 재암호화 오버헤드와 페더레이션 지연을 고려해 Telegram보다 큼).
- `multi_message_delay_ms`는 multi_message 모드의 단락 송신 간 최소 지연입니다 (기본 800ms).
- 두 모드 모두 암호화·비암호화 방에서 동작합니다 — matrix-sdk가 E2EE를 투명하게 처리합니다.
- `stream_mode`가 없는 기존 설정은 `off`로 기본 처리됩니다 (동작 변경 없음).

암호화 방 트러블슈팅은 [Matrix E2EE 가이드](../../security/matrix-e2ee-guide.md) *(영문)* 를 참고하세요.

### 4.6 Signal

```toml
[channels_config.signal]
http_url = "http://127.0.0.1:8686"
account = "+1234567890"
group_id = "dm"                    # 선택: "dm" / 그룹 ID / 생략
allowed_from = ["*"]
ignore_attachments = false
ignore_stories = true
```

### 4.7 WhatsApp

Revka는 두 가지 WhatsApp 백엔드를 지원합니다.

- **Cloud API 모드** (`phone_number_id` + `access_token` + `verify_token`)
- **WhatsApp Web 모드** (`session_path`, 빌드 플래그 `--features whatsapp-web` 필요)

Cloud API 모드:

```toml
[channels_config.whatsapp]
access_token = "EAAB..."
phone_number_id = "123456789012345"
verify_token = "your-verify-token"
app_secret = "your-app-secret"     # 선택이지만 권장
allowed_numbers = ["*"]
```

WhatsApp Web 모드:

```toml
[channels_config.whatsapp]
session_path = "~/.revka/state/whatsapp-web/session.db"
pair_phone = "15551234567"         # 선택; 비우면 QR 흐름 사용
pair_code = ""                     # 선택, 사용자 지정 페어 코드
allowed_numbers = ["*"]
```

메모:

- 빌드는 `cargo build --features whatsapp-web` (또는 동등한 실행 명령)으로.
- 재시작 후 다시 페어링하지 않으려면 `session_path`를 영속 저장소에 두세요.
- 답신 라우팅은 발신 채팅의 JID를 사용하므로 1:1과 그룹 답신이 모두 정상 동작합니다.

### 4.8 Webhook 채널 설정 (Gateway)

`channels_config.webhook`은 웹훅 전용 게이트웨이 동작을 활성화합니다.

```toml
[channels_config.webhook]
port = 8080
secret = "optional-shared-secret"
```

게이트웨이/데몬과 함께 실행하고 `/health`를 확인하세요.

### 4.9 Email

```toml
[channels_config.email]
imap_host = "imap.example.com"
imap_port = 993
imap_folder = "INBOX"
smtp_host = "smtp.example.com"
smtp_port = 465
smtp_tls = true
username = "bot@example.com"
password = "email-password"
from_address = "bot@example.com"
poll_interval_secs = 60
allowed_senders = ["*"]
```

### 4.10 IRC

```toml
[channels_config.irc]
server = "irc.libera.chat"
port = 6697
nickname = "revka-bot"
username = "revka"              # 선택
channels = ["#revka"]
allowed_users = ["*"]
server_password = ""                # 선택
nickserv_password = ""              # 선택
sasl_password = ""                  # 선택
verify_tls = true
```

### 4.11 Lark

```toml
[channels_config.lark]
app_id = "cli_xxx"
app_secret = "xxx"
encrypt_key = ""                    # 선택
verification_token = ""             # 선택
allowed_users = ["*"]
mention_only = false              # 선택: 그룹에서 @멘션 요구 (DM은 항상 허용)
use_feishu = false
receive_mode = "websocket"          # 또는 "webhook"
port = 8081                          # 웹훅 모드에 필요
```

### 4.12 Feishu

```toml
[channels_config.feishu]
app_id = "cli_xxx"
app_secret = "xxx"
encrypt_key = ""                    # 선택
verification_token = ""             # 선택
allowed_users = ["*"]
receive_mode = "websocket"          # 또는 "webhook"
port = 8081                          # 웹훅 모드에 필요
```

마이그레이션 메모:

- 레거시 설정 `[channels_config.lark] use_feishu = true`는 하위 호환을 위해 여전히 지원됩니다.
- 새로 잡는 환경에서는 `[channels_config.feishu]`를 권장합니다.

### 4.13 Nostr

```toml
[channels_config.nostr]
private_key = "nsec1..."                   # hex 또는 nsec bech32 (저장 시 암호화)
# relays는 기본값으로 relay.damus.io, nos.lol, relay.primal.net, relay.snort.social
# relays = ["wss://relay.damus.io", "wss://nos.lol"]
allowed_pubkeys = ["hex-or-npub"]          # 빈 값 = 전부 거부, "*" = 전부 허용
```

Nostr는 NIP-04(레거시 암호화 DM)와 NIP-17(기프트랩 사설 메시지)을 모두 지원합니다. 답신은 발신자가 사용한 프로토콜을 자동으로 따릅니다. `secrets.encrypt = true`(기본)일 때 `SecretStore`로 개인키가 저장 시 암호화됩니다.

안내형 온보딩 지원:

```bash
revka onboard
```

위저드는 **Lark**와 **Feishu** 전용 단계를 포함하며 다음을 수행합니다.

- 공식 Open Platform 인증 엔드포인트 대상 자격 검증
- 수신 모드 선택 (`websocket` 또는 `webhook`)
- 콜백 진정성을 더 강하게 점검하기 위한 웹훅 verification 토큰 입력 (권장)

런타임 토큰 동작:

- `tenant_access_token`은 인증 응답의 `expire`/`expires_in`을 기반으로 갱신 데드라인과 함께 캐시됩니다.
- Feishu/Lark가 HTTP `401` 또는 비즈니스 에러 코드 `99991663`(`Invalid access token`)을 반환하면 송신 요청은 토큰 무효화 후 한 번 자동 재시도합니다.
- 재시도 후에도 토큰 무효 응답이면 트러블슈팅을 돕기 위해 상위 상태/바디와 함께 송신 호출이 실패합니다.

### 4.14 DingTalk

```toml
[channels_config.dingtalk]
client_id = "ding-app-key"
client_secret = "ding-app-secret"
allowed_users = ["*"]
```

### 4.15 QQ

```toml
[channels_config.qq]
app_id = "qq-app-id"
app_secret = "qq-app-secret"
allowed_users = ["*"]
```

### 4.16 Nextcloud Talk

```toml
[channels_config.nextcloud_talk]
base_url = "https://cloud.example.com"
app_token = "nextcloud-talk-app-token"
webhook_secret = "optional-webhook-secret"  # 선택이지만 권장
allowed_users = ["*"]
# bot_name = "revka"  # 봇 표시 이름; 봇 자신의 메시지를 걸러 피드백 루프 방지
```

메모:

- 인바운드 웹훅 엔드포인트: `POST /nextcloud-talk`.
- 서명 검증은 `X-Nextcloud-Talk-Random`과 `X-Nextcloud-Talk-Signature` 헤더를 사용합니다.
- `webhook_secret`이 설정되면 잘못된 서명은 `401`로 거부됩니다.
- `REVKA_NEXTCLOUD_TALK_WEBHOOK_SECRET`이 설정 시크릿을 오버라이드합니다.
- 전체 런북은 [nextcloud-talk-setup.md](../../setup-guides/nextcloud-talk-setup.md) *(영문)* 를 참고하세요.

### 4.16 Linq

```toml
[channels_config.linq]
api_token = "linq-partner-api-token"
from_phone = "+15551234567"
signing_secret = "optional-webhook-signing-secret"  # 선택이지만 권장
allowed_senders = ["*"]
```

메모:

- Linq는 iMessage·RCS·SMS용 Partner V3 API를 사용합니다.
- 인바운드 웹훅 엔드포인트: `POST /linq`.
- 서명 검증은 `X-Webhook-Signature` (HMAC-SHA256)와 `X-Webhook-Timestamp`를 사용합니다.
- `signing_secret`이 설정되면 잘못되거나 오래된(>300s) 서명은 거부됩니다.
- `REVKA_LINQ_SIGNING_SECRET`이 설정 시크릿을 오버라이드합니다.
- `allowed_senders`는 E.164 전화번호 형식을 사용합니다 (예: `+1234567890`).

### 4.17 iMessage

```toml
[channels_config.imessage]
allowed_contacts = ["*"]
```

---

## 5. 검증 워크플로

1. 초기 검증을 위해 채널 하나에 관대한 허용 목록(`"*"`)을 설정합니다.
2. 다음을 실행합니다.

```bash
revka onboard --channels-only
revka daemon
```

3. 예상되는 발신자에서 메시지를 보냅니다.
4. 답신이 도착하는지 확인합니다.
5. 허용 목록을 `"*"`에서 명시적 ID로 좁힙니다.

---

## 6. 트러블슈팅 체크리스트

연결돼 있는 것 같은데 채널이 응답하지 않는다면:

1. 발신자 아이덴티티가 올바른 허용 목록 필드에 있는지 확인.
2. 봇 계정이 대상 방/채널에 가입돼 있고 권한이 있는지 확인.
3. 토큰/시크릿이 유효하고 만료/취소되지 않았는지 확인.
4. 트랜스포트 모드 가정 확인:
   - 폴링/WebSocket 채널은 공용 인바운드 HTTP가 필요 없음.
   - 웹훅 채널은 도달 가능한 HTTPS 콜백이 필요함.
5. 설정 변경 후 `revka daemon`을 재시작.

Matrix 암호화 방에 대한 별도 도움은 [Matrix E2EE 가이드](../../security/matrix-e2ee-guide.md) *(영문)* 를 보세요.

---

## 7. 운영 부록: 로그 키워드 매트릭스

빠른 트리아지를 위한 부록입니다. 먼저 로그 키워드를 매칭하고, 위 트러블슈팅 단계를 따르세요.

### 7.1 권장 캡처 명령

```bash
RUST_LOG=info revka daemon 2>&1 | tee /tmp/revka.log
```

채널/게이트웨이 이벤트를 필터:

```bash
rg -n "Matrix|Telegram|Discord|Slack|Mattermost|Signal|WhatsApp|Email|IRC|Lark|DingTalk|QQ|iMessage|Nostr|Webhook|Channel" /tmp/revka.log
```

### 7.2 키워드 표

| 컴포넌트 | 시작 / 정상 신호 | 인증 / 정책 신호 | 트랜스포트 / 실패 신호 |
|---|---|---|---|
| Telegram | `Telegram channel listening for messages...` | `Telegram: ignoring message from unauthorized user:` | `Telegram poll error:` / `Telegram parse error:` / `Telegram polling conflict (409):` |
| Discord | `Discord: connected and identified` | `Discord: ignoring message from unauthorized user:` | `Discord: received Reconnect (op 7)` / `Discord: received Invalid Session (op 9)` |
| Slack | `Slack channel listening on #` / `Slack channel_id not set (or '*'); listening across all accessible channels.` | `Slack: ignoring message from unauthorized user:` | `Slack poll error:` / `Slack parse error:` / `Slack channel discovery failed:` |
| Mattermost | `Mattermost channel listening on` | `Mattermost: ignoring message from unauthorized user:` | `Mattermost poll error:` / `Mattermost parse error:` |
| Matrix | `Matrix channel listening on room` / `Matrix room ... is encrypted; E2EE decryption is enabled via matrix-sdk.` | `Matrix whoami failed; falling back to configured session hints for E2EE session restore:` / `Matrix whoami failed while resolving listener user_id; using configured user_id hint:` | `Matrix sync error: ... retrying...` |
| Signal | `Signal channel listening via SSE on` | (`allowed_from`이 허용 목록 검사 강제) | `Signal SSE returned ...` / `Signal SSE connect error:` |
| WhatsApp (channel) | `WhatsApp channel active (webhook mode).` / `WhatsApp Web connected successfully` | `WhatsApp: ignoring message from unauthorized number:` / `WhatsApp Web: message from ... not in allowed list` | `WhatsApp send failed:` / `WhatsApp Web stream error:` |
| Webhook / WhatsApp (gateway) | `WhatsApp webhook verified successfully` | `Webhook: rejected — not paired / invalid bearer token` / `Webhook: rejected request — invalid or missing X-Webhook-Secret` / `WhatsApp webhook verification failed — token mismatch` | `Webhook JSON parse error:` |
| Email | `Email polling every ...` / `Email sent to ...` | `Blocked email from ...` | `Email poll failed:` / `Email poll task panicked:` |
| IRC | `IRC channel connecting to ...` / `IRC registered as ...` | (`allowed_users`가 허용 목록 검사 강제) | `IRC SASL authentication failed (...)` / `IRC server does not support SASL...` / `IRC nickname ... is in use, trying ...` |
| Lark / Feishu | `Lark: WS connected` / `Lark event callback server listening on` | `Lark WS: ignoring ... (not in allowed_users)` / `Lark: ignoring message from unauthorized user:` | `Lark: ping failed, reconnecting` / `Lark: heartbeat timeout, reconnecting` / `Lark: WS read error:` |
| DingTalk | `DingTalk: connected and listening for messages...` | `DingTalk: ignoring message from unauthorized user:` | `DingTalk WebSocket error:` / `DingTalk: message channel closed` |
| QQ | `QQ: connected and identified` | `QQ: ignoring C2C message from unauthorized user:` / `QQ: ignoring group message from unauthorized user:` | `QQ: received Reconnect (op 7)` / `QQ: received Invalid Session (op 9)` / `QQ: message channel closed` |
| Nextcloud Talk (gateway) | `POST /nextcloud-talk — Nextcloud Talk bot webhook` | `Nextcloud Talk webhook signature verification failed` / `Nextcloud Talk: ignoring message from unauthorized actor:` | `Nextcloud Talk send failed:` / `LLM error for Nextcloud Talk message:` |
| iMessage | `iMessage channel listening (AppleScript bridge)...` | (`allowed_contacts`가 컨택트 허용 목록 강제) | `iMessage poll error:` |
| Nostr | `Nostr channel listening as npub1...` | `Nostr: ignoring NIP-04 message from unauthorized pubkey:` / `Nostr: ignoring NIP-17 message from unauthorized pubkey:` | `Failed to decrypt NIP-04 message:` / `Failed to unwrap NIP-17 gift wrap:` / `Nostr relay pool shut down` |

### 7.3 런타임 슈퍼바이저 키워드

특정 채널 작업이 죽거나 종료되면 `channels/mod.rs`의 채널 슈퍼바이저가 다음을 출력합니다.

- `Channel <name> exited unexpectedly; restarting`
- `Channel <name> error: ...; restarting`
- `Channel message worker crashed:`

이 메시지는 자동 재시작 동작이 동작 중임을 뜻하며, 근본 원인 파악을 위해 직전 로그를 살펴보세요.
