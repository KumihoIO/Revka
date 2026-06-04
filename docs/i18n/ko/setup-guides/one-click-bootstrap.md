# 한 번에 부트스트랩

Revka를 가장 빠르게 설치하고 초기화하는 정식 경로입니다.

마지막 검증: **2026년 4월 21일**.

## 옵션 0: Homebrew (macOS / Linuxbrew)

```bash
brew install revka
```

## 옵션 A (권장): 클론 + 로컬 스크립트

```bash
git clone https://github.com/KumihoIO/Revka.git
cd revka
./install.sh
```

기본 동작:

1. `cargo build --release --locked`
2. `cargo install --path . --force --locked`

### 리소스 사전 점검과 사전 빌드 바이너리 흐름

소스 빌드는 보통 다음 정도가 필요합니다.

- **RAM 2 GB 이상 + 스왑**
- **빈 디스크 6 GB 이상**

리소스가 부족할 때는 부트스트랩이 사전 빌드된 바이너리를 먼저 시도합니다.

```bash
./install.sh --prefer-prebuilt
```

호환되는 릴리스 자산이 없으면 실패하도록 강제하려면:

```bash
./install.sh --prebuilt-only
```

사전 빌드 흐름을 건너뛰고 무조건 소스에서 컴파일하려면:

```bash
./install.sh --force-source-build
```

## 듀얼 모드 부트스트랩

기본 동작은 **앱만 설치**(Revka 빌드 + 인스톨)이며, Rust 툴체인이 이미 깔려 있다고 가정합니다.

새 머신에서는 환경 부트스트랩까지 명시적으로 켜세요.

```bash
./install.sh --install-system-deps --install-rust
```

플래그 정리:

- `--install-system-deps`: 컴파일러/빌드에 필요한 의존성을 설치합니다 (`sudo`가 필요할 수 있음).
- `--install-rust`: Rust가 없으면 `rustup`으로 설치합니다.
- `--prefer-prebuilt`: 릴리스 바이너리 다운로드를 먼저 시도하고, 실패하면 소스 빌드로 넘어갑니다.
- `--prebuilt-only`: 소스 빌드 폴백을 비활성화합니다.
- `--force-source-build`: 사전 빌드 흐름을 완전히 끕니다.

## 옵션 B: 원격 한 줄 설치

```bash
curl -fsSL https://raw.githubusercontent.com/KumihoIO/Revka/main/install.sh | bash
```

보안이 중요한 환경이라면, 스크립트를 먼저 점검할 수 있는 옵션 A를 권장합니다.

체크아웃이 아닌 곳에서 옵션 B를 실행하면 인스톨러가 임시 워크스페이스를 자동으로 클론해 빌드·설치한 뒤 깨끗이 정리합니다.

## 선택형 온보딩 모드

<!-- TODO screenshot: Docker 컨테이너에서 Revka를 실행해 브라우저에서 온보딩 UI를 보여주는 화면 -->
![브라우저에서 Revka 온보딩 UI를 표시하는 Docker 컨테이너](../../../assets/setup/one-click-bootstrap-02-docker-onboarding.png)

### 컨테이너 기반 온보딩 (Docker)

```bash
./install.sh --docker
```

로컬에서 Revka 이미지를 빌드한 뒤 컨테이너 안에서 온보딩을 실행합니다. 설정과 워크스페이스는 `./.revka-docker`에 영속됩니다.

컨테이너 CLI는 기본적으로 `docker`를 사용합니다. Docker CLI가 없고 `podman`이 있으면 인스톨러가 자동으로 `podman`으로 떨어집니다. `REVKA_CONTAINER_CLI`로 명시할 수도 있습니다 (예: `REVKA_CONTAINER_CLI=podman ./install.sh --docker`).

Podman의 경우 `--userns keep-id`와 `:Z` 볼륨 라벨로 실행되어, 워크스페이스/설정 마운트가 컨테이너 내부에서 쓰기 가능 상태로 유지됩니다.

`--skip-build`를 함께 주면 로컬 이미지 빌드를 건너뜁니다. 이때 인스톨러는 먼저 로컬 Docker 태그(`REVKA_DOCKER_IMAGE`, 기본값 `revka-bootstrap:local`)를 시도하고, 없으면 `ghcr.io/kumihoio/revka:latest`를 받아 로컬 태그로 붙인 뒤 실행합니다.

### Docker / Podman 컨테이너 정지·재시작

`./install.sh --docker`가 끝나면 컨테이너는 종료됩니다. 설정과 워크스페이스는 데이터 디렉토리(기본 `./.revka-docker`, `curl | bash` 경로에서는 `~/.revka-docker`)에 그대로 남습니다. 경로는 `REVKA_DOCKER_DATA_DIR`로 바꿀 수 있습니다.

**다시 실행할 때 `install.sh`를 또 돌리지 마세요** — 이미지를 재빌드하고 온보딩을 다시 돌립니다. 대신 기존 이미지에서 컨테이너를 새로 띄우고 보존된 데이터 디렉토리를 마운트합니다.

#### 리포지토리의 docker-compose.yml 사용하기

장기 운영용으로는 리포지토리 루트에 있는 `docker-compose.yml`이 가장 단순한 방법입니다. 명명 볼륨(`revka-data`)을 쓰고 `restart: unless-stopped`이 걸려 있어 재부팅 후에도 살아 있습니다.

```bash
# 백그라운드로 시작
docker compose up -d

# 정지
docker compose down

# 정지 후 재시작
docker compose up -d
```

Podman을 쓴다면 `docker`를 `podman`으로 바꿔 주세요.

#### 수동 실행 (install.sh 데이터 디렉토리 재활용)

`./install.sh --docker`로 설치한 뒤 compose 없이 `.revka-docker`를 그대로 다시 쓰고 싶다면:

```bash
# Docker
docker run -d --name revka \
  --restart unless-stopped \
  -v "$PWD/.revka-docker/.revka:/revka-data/.revka" \
  -v "$PWD/.revka-docker/workspace:/revka-data/workspace" \
  -e HOME=/revka-data \
  -e REVKA_WORKSPACE=/revka-data/workspace \
  -p 42617:42617 \
  revka-bootstrap:local \
  gateway

# Podman (--userns keep-id 와 :Z 볼륨 라벨 추가)
podman run -d --name revka \
  --restart unless-stopped \
  --userns keep-id \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/.revka-docker/.revka:/revka-data/.revka:Z" \
  -v "$PWD/.revka-docker/workspace:/revka-data/workspace:Z" \
  -e HOME=/revka-data \
  -e REVKA_WORKSPACE=/revka-data/workspace \
  -p 42617:42617 \
  revka-bootstrap:local \
  gateway
```

#### 자주 쓰는 라이프사이클 명령

```bash
# 컨테이너 정지 (데이터 보존)
docker stop revka

# 정지된 컨테이너 시작 (설정과 워크스페이스 그대로)
docker start revka

# 로그 확인
docker logs -f revka

# 컨테이너 제거 (볼륨/.revka-docker 데이터는 그대로)
docker rm revka

# 헬스 체크
docker exec revka revka status
```

#### 환경 변수

수동 실행 시 프로바이더 설정은 환경 변수로 넘기거나, 보존된 `config.toml`에 미리 저장해 두세요.

```bash
docker run -d --name revka \
  -e API_KEY="sk-..." \
  -e PROVIDER="openrouter" \
  -v "$PWD/.revka-docker/.revka:/revka-data/.revka" \
  -v "$PWD/.revka-docker/workspace:/revka-data/workspace" \
  -p 42617:42617 \
  revka-bootstrap:local \
  gateway
```

처음 설치 때 `onboard`까지 마쳤다면 API 키와 프로바이더가 `.revka-docker/.revka/config.toml`에 저장돼 있어 다시 넘겨줄 필요가 없습니다.

### 비대화형 온보딩

```bash
./install.sh --api-key "sk-..." --provider openrouter
```

또는 환경 변수로:

```bash
REVKA_API_KEY="sk-..." REVKA_PROVIDER="openrouter" ./install.sh
```

## 자주 쓰는 플래그

- `--install-system-deps`
- `--install-rust`
- `--skip-build` (`--docker` 모드: 로컬 이미지가 있으면 사용, 없으면 `ghcr.io/kumihoio/revka:latest`를 받아 옴)
- `--skip-install`
- `--provider <id>`

전체 옵션은:

```bash
./install.sh --help
```

<!-- TODO screenshot: 한 번에 부트스트랩 성공 직후 Revka 대시보드 초기 화면 -->
![한 번에 부트스트랩 성공 직후 Revka 대시보드 초기 화면](../../../assets/setup/one-click-bootstrap-01-dashboard-initial.png)

## 부트스트랩 이후

설치가 끝나면 가장 빠른 가동 경로는:

```bash
# 게이트웨이 시작 (임베디드 React 웹 대시보드 + REST API + WebSocket)
revka gateway

# 또는 풀 슈퍼바이즈드 런타임 (게이트웨이 + 채널 + 하트비트 + 크론)
revka daemon
```

웹 대시보드는 `http://127.0.0.1:42617/`에서 열립니다. 전체 기능 맵(Kumiho 그래프 메모리, Operator 워크플로, ClawHub, A2A, 트러스트 점수)은 루트 [README.md](../../../README.md)를, 프론트엔드 작업 흐름은 [dashboard-dev.md](dashboard-dev.md) *(영문)* 을 참고하세요.

Kumiho(FastAPI + Neo4j)와 Operator MCP는 런타임에서는 선택 사항이지만 `~/.revka/config.toml`의 `[kumiho]`/`[operator]` 섹션에서 기본적으로 켜져 있습니다. 사이드카를 띄우지 않을 거라면 거기서 끄세요.

`install.sh`(및 `setup.bat`)는 소스 체크아웃이 있고 런처가 비어 있을 때 **Kumiho**와 **Operator** Python MCP 사이드카를 `~/.revka/` 아래에 자동으로 설치합니다. 전체 절차·수동 단계·검증 명령은 [kumiho-operator-setup.md](kumiho-operator-setup.md) *(영문)* 에 있습니다. 끄려면 `--skip-sidecars`를 주세요.

## 관련 문서

- [README.md](../README.md)
- [commands-reference.md](../reference/cli/commands-reference.md) *(영문)*
- [providers-reference.md](../reference/api/providers-reference.md) *(영문)*
- [channels-reference.md](../reference/api/channels-reference.md) *(영문)*
- [dashboard-dev.md](dashboard-dev.md) *(영문)*
