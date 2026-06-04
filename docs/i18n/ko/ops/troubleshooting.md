# Revka 트러블슈팅

설치·런타임에서 자주 부딪히는 실패 사례와 빠른 해결 경로입니다.

마지막 검증: **2026년 2월 20일**.

## 설치 / 부트스트랩

### `cargo`를 찾을 수 없음

증상:

- 부트스트랩이 `cargo is not installed`로 종료됨

조치:

```bash
./install.sh --install-rust
```

또는 <https://rustup.rs/>에서 직접 설치하세요.

### 시스템 빌드 의존성 누락

증상:

- 컴파일러 또는 `pkg-config` 문제로 빌드 실패

조치:

```bash
./install.sh --install-system-deps
```

### 저사양 RAM/디스크 환경에서 빌드 실패

증상:

- `cargo build --release`가 강제 종료 (`signal: 9`, OOM 킬러, `cannot allocate memory`)
- 스왑을 추가했더니 디스크가 부족해 다시 죽음

원인:

- 런타임 메모리(일반 작업 시 5MB 미만)와 컴파일 시 메모리는 다릅니다.
- 풀 소스 빌드는 **RAM 2 GB + 스왑**, **빈 디스크 6 GB 이상**을 요구합니다.
- 작은 디스크에 스왑만 잡으면 RAM OOM은 피해도 디스크 고갈로 다시 실패합니다.

저사양 머신에 권장하는 경로:

```bash
./install.sh --prefer-prebuilt
```

바이너리 전용 모드 (소스 빌드 폴백 없음):

```bash
./install.sh --prebuilt-only
```

저사양에서도 꼭 소스로 빌드해야 한다면:

1. 스왑은 **빌드 산출물 + 스왑 모두를 담을 수 있는 디스크**가 있을 때만 추가하세요.
2. cargo 병렬 작업 수를 줄이세요:

   ```bash
   CARGO_BUILD_JOBS=1 cargo build --release --locked
   ```

3. Matrix가 필요 없으면 무거운 피처를 제외하세요:

   ```bash
   cargo build --release --locked --features hardware
   ```

4. 더 강한 머신에서 크로스 컴파일한 뒤 바이너리만 옮기는 방법도 있습니다.

### 빌드가 매우 느리거나 멈춰 보임

증상:

- `cargo check` / `cargo build`가 한참 동안 `Checking revka`에서 멈춰 있음
- `Blocking waiting for file lock on package cache` 또는 `build directory` 메시지가 반복됨

Revka에서 이런 일이 자주 일어나는 이유:

- Matrix E2EE 스택(`matrix-sdk`, `ruma`, `vodozemac`)은 크고 타입 체크 비용이 높습니다.
- TLS·암호화 네이티브 빌드 스크립트(`aws-lc-sys`, `ring`)가 컴파일 시간을 눈에 띄게 늘립니다.
- 번들 SQLite를 포함한 `rusqlite`는 C 코드를 로컬에서 컴파일합니다.
- 여러 cargo 작업이나 워크트리를 동시에 돌리면 락 경합이 발생합니다.

빠른 점검:

```bash
cargo check --timings
cargo tree -d
```

타이밍 리포트는 `target/cargo-timings/cargo-timing.html`에 저장됩니다.

로컬 반복 작업 속도를 올리려면 (Matrix 채널이 필요 없을 때):

```bash
cargo check
```

기본 피처만 사용하므로 컴파일 시간이 크게 줄어듭니다.

Matrix를 명시적으로 켜고 빌드:

```bash
cargo check --features channel-matrix
```

Matrix + Lark + 하드웨어 지원까지:

```bash
cargo check --features hardware,channel-matrix,channel-lark
```

락 경합 완화:

```bash
pgrep -af "cargo (check|build|test)|cargo check|cargo build|cargo test"
```

본인 빌드를 시작하기 전에 관련 없는 cargo 작업은 정리하세요.

### 설치 후에도 `revka` 명령을 찾을 수 없음

증상:

- 설치는 성공했는데 셸이 `revka`를 못 찾음

조치:

```bash
export PATH="$HOME/.cargo/bin:$PATH"
which revka
```

필요하면 셸 프로파일에도 추가해 두세요.

## 런타임 / 게이트웨이

### 게이트웨이 도달 불가

점검:

```bash
revka status
revka doctor
```

`~/.revka/config.toml` 확인 항목:

- `[gateway].host` (기본 `127.0.0.1`)
- `[gateway].port` (기본 `42617`)
- `allow_public_bind`는 LAN/공용 인터페이스에 의도적으로 노출할 때만 켜세요.

### 웹훅 페어링 / 인증 실패

점검:

1. 페어링 절차(`/pair` 흐름)가 끝났는지 확인
2. Bearer 토큰이 현재 유효한지 확인
3. 진단 재실행:

   ```bash
   revka doctor
   ```

## 채널 이슈

### Telegram 충돌: `terminated by other getUpdates request`

원인:

- 같은 봇 토큰으로 여러 폴러가 동시에 동작 중

조치:

- 해당 토큰에 대해 활성 런타임을 하나만 남기세요.
- 잉여 `revka daemon` / `revka channel start` 프로세스를 정리하세요.

### `channel doctor`에서 채널이 unhealthy로 보임

점검:

```bash
revka channel doctor
```

이어서 채널별 자격 증명과 설정의 허용 목록 항목을 검증하세요.

## 서비스 모드

### 서비스는 설치됐는데 실행 중이 아님

점검:

```bash
revka service status
```

복구:

```bash
revka service stop
revka service start
```

Linux 로그:

```bash
journalctl --user -u revka.service -f
```

## 인스톨러 URL

```bash
curl -fsSL https://raw.githubusercontent.com/KumihoIO/Revka/main/install.sh | bash
```

## 그래도 막혔다면

이슈를 등록할 때 다음 출력을 함께 첨부해 주세요.

```bash
revka --version
revka status
revka doctor
revka channel doctor
```

OS, 설치 방법, 비밀이 제거된 설정 스니펫도 같이 적어 주세요.

## 관련 문서

- [operations-runbook.md](operations-runbook.md) *(영문)*
- [one-click-bootstrap.md](../setup-guides/one-click-bootstrap.md)
- [channels-reference.md](../reference/api/channels-reference.md) *(영문)*
- [network-deployment.md](network-deployment.md) *(영문)*
