# 시작하기 문서

처음 Revka를 설치하거나 빠르게 감을 잡고 싶을 때 보는 페이지입니다.

## 시작 전 준비: Kumiho 계정과 서비스 토큰

Revka는 영속적인 메모리·계보 그래프·감사 체인을 [Kumiho](https://kumiho.io)에 저장합니다. Kumiho 없이도 단일 에이전트 모드로는 동작하지만, 세션 간 메모리 회상이나 트러스트 점수 같은 핵심 기능을 쓰려면 계정과 토큰이 필요합니다.

1. **계정 만들기 — [kumiho.io](https://kumiho.io)에서 가입**
   - 이메일·비밀번호 또는 OAuth 한 가지로 가능합니다.
   - 무료 티어는 **5,000 노드**까지이며, 카드 등록 없이 바로 쓸 수 있습니다.
   - Revka가 첫 메모리를 쓰는 순간 **30일 Studio 트라이얼**(500,000 노드, 크로스 세션 회상, 감사 가시성)이 자동으로 활성화됩니다. 트라이얼 중 만든 데이터는 무료 티어로 돌아가도 그대로 남습니다.

2. **대시보드에서 서비스 토큰 발급**
   - 가입 후 [kumiho.io](https://kumiho.io) 대시보드에 로그인합니다.
   - 계정/설정 메뉴에서 **Service Token**(또는 **API Token**) 항목을 찾아 새로 발급합니다.
   - 발급된 토큰은 1년 유효한 JWT이며, 그 안에 테넌트 ID·리전·역할 같은 라우팅 정보가 모두 들어 있습니다. 다시 볼 수 없는 경우가 있으니 발급 직후 안전한 곳에 복사해 두세요.

3. **온보딩 시 토큰 붙여넣기**
   - `revka onboard`를 실행하면 *"Kumiho service token (KUMIHO_SERVICE_TOKEN)"* 입력 단계가 나옵니다. 거기에 위 토큰을 붙여넣으면 됩니다.
   - 워크스페이스의 `.env`에 `KUMIHO_SERVICE_TOKEN=...` 형태로 저장되며, 이후 `revka daemon`이 이 값을 읽어 게이트웨이와 MCP 사이드카에 전달합니다.

> 서비스 토큰만으로 충분합니다. `kumiho login`(파이어베이스 로그인)이 별도로 필요한 시나리오는 일반 사용자에겐 해당되지 않습니다.

자체 호스팅(Enterprise) 옵션이 필요하면 [kumiho.io/pricing](https://kumiho.io/pricing) 또는 <enterprise@kumiho.io>로 문의하세요.

---

## 추천 경로

1. 전체 개요와 빠른 시작: [../../../README.md](../../../README.md) *(영문)*
2. 한 번의 명령으로 설치 + 듀얼 부트스트랩 모드: [one-click-bootstrap.md](one-click-bootstrap.md) *(영문)*
3. Kumiho · Operator Python MCP 사이드카 설치: [kumiho-operator-setup.md](kumiho-operator-setup.md) *(영문)*
4. macOS 업데이트/제거: [macos-update-uninstall.md](macos-update-uninstall.md) *(영문)*
5. 대시보드 로컬 개발 및 빌드 흐름: [dashboard-dev.md](dashboard-dev.md) *(영문)*
6. 작업별 명령어 찾기: [../reference/cli/commands-reference.md](../reference/cli/commands-reference.md) *(영문)*

## 상황별 선택지

| 상황 | 명령 |
|---|---|
| API 키가 있고 가장 빠르게 시작하고 싶을 때 | `revka onboard --api-key sk-... --provider openrouter` |
| 안내 프롬프트를 따라가며 설정하고 싶을 때 | `revka onboard` |
| 기존 설정은 그대로 두고 채널만 다시 잡고 싶을 때 | `revka onboard --channels-only` |
| 기존 설정을 의도적으로 전부 덮어쓰고 싶을 때 | `revka onboard --force` |
| 구독 기반 인증을 쓰고 있을 때 | [구독 인증 문서](../../../README.md#subscription-auth-openai-codex--claude-code) *(영문)* 참고 |

## 온보딩과 검증

- 빠른 온보딩: `revka onboard --api-key "sk-..." --provider openrouter`
- 안내형 온보딩: `revka onboard`
- 기존 설정 보호: 재실행 시 명시적 확인이 필요하며, 비대화형 환경에서는 `--force`를 붙여야 진행됩니다.
- Ollama 클라우드 모델(`:cloud`)은 원격 `api_url`과 API 키가 필요합니다. 예: `api_url = "https://ollama.com"`.
- 환경 점검: `revka status`, `revka doctor`
- 대시보드 개발/빌드 루프: [dashboard-dev.md](dashboard-dev.md) *(영문)*

## 다음 단계

- 런타임 운영: [../ops/README.md](../ops/README.md)
- 참조 자료: [../reference/README.md](../reference/README.md)
- macOS 라이프사이클 작업: [macos-update-uninstall.md](macos-update-uninstall.md) *(영문)*
