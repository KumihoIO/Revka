# 운영 및 배포 문서

Revka를 상시 가동/운영 환경에서 굴리는 운영자를 위한 페이지입니다.

## 핵심 운영 자료

- 2일차 런북: [./operations-runbook.md](./operations-runbook.md) *(영문)*
- 릴리스 런북: [../contributing/release-process.md](../contributing/release-process.md) *(영문)*
- 트러블슈팅 매트릭스: [./troubleshooting.md](./troubleshooting.md) *(영문)*
- 안전한 네트워크/게이트웨이 배포: [./network-deployment.md](./network-deployment.md) *(영문)*
- Mattermost 채널 셋업: [../setup-guides/mattermost-setup.md](../setup-guides/mattermost-setup.md) *(영문)*

## 일반적인 흐름

1. 런타임 점검 (`status`, `doctor`, `channel doctor`)
2. 설정은 한 번에 한 가지씩만 변경
3. 서비스/데몬 재시작
4. 채널과 게이트웨이 상태 확인
5. 동작이 의도와 달라지면 즉시 롤백

## 관련 문서

- 구성 참조: [../reference/api/config-reference.md](../reference/api/config-reference.md) *(영문)*
- 보안 자료 모음: [../security/README.md](../security/README.md)
