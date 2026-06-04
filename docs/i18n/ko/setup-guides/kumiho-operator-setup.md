# Kumiho · Operator 사이드카 설치

Revka의 기본 설정은 두 개의 Python MCP(Model Context Protocol) 사이드카가 `~/.revka/` 아래에 설치돼 있다고 가정합니다.

| 사이드카 | 런처 경로 | 설정 키 |
|---|---|---|
| **Kumiho MCP** | `~/.revka/kumiho/run_kumiho_mcp.py` | `[kumiho].mcp_path` |
| **Operator MCP** | `~/.revka/operator_mcp/run_operator_mcp.py` | `[operator].mcp_path` |

이 문서는 두 사이드카의 정식 설치 경로, 각자 무엇을 해 주는지, 검증 방법과 트러블슈팅 절차를 다룹니다.

마지막 검증: **2026-04-21**.

## 빠른 설치 (자동)

`revka` 바이너리가 이미 PATH에 있다면 (예: `cargo install kumiho-revka` 또는 직전에 `./install.sh`를 돌렸음):

```bash
revka install --sidecars-only
```

크로스 플랫폼이며, OS에 맞는 번들 사이드카 인스톨러를 풀어 실행합니다.

Revka 소스 체크아웃에서 직접 돌리려면 (`revka` 바이너리 불필요):

```bash
# POSIX (macOS / Linux / WSL)
./scripts/install-sidecars.sh

# Windows
scripts\install-sidecars.bat
```

이 스크립트들은 **멱등(idempotent)** 합니다 — 다시 돌려도 안전합니다. `~/.revka/` 아래에 스캐폴드만 만들고, 기존의 `config.toml`·`.env`·사용자가 작성한 런처는 절대 덮어쓰지 않습니다.

체크아웃에 `operator-mcp/`가 있고 런처가 없을 때는 `./install.sh`와 `setup.bat`이 같은 로직을 자동 호출합니다. 끄고 싶으면 `./install.sh --skip-sidecars`.

---

## 1. Kumiho MCP 서버

### 무엇인가

Kumiho는 Revka의 그래프 네이티브 영속 메모리 백엔드입니다. **Kumiho MCP 서버**는 stdio 기반 MCP 프로세스로, 모든 비내부 에이전트에게 메모리 도구(`kumiho_memory_engage`, `kumiho_memory_reflect` 등)를 노출합니다.

이 MCP 서버는 **Kumiho 컨트롤 플레인**의 클라이언트입니다. 컨트롤 플레인은 FastAPI + Neo4j로 굴러가는 HTTP 서비스이고, 로컬에 설치하지 않습니다. 대신 `~/.revka/config.toml`의 `[kumiho].api_url`로 디스커버리합니다. 매니지드 Kumiho 엔드포인트(`https://api.kumiho.cloud`)나 자체 호스팅 URL을 가리키게 하세요. 여기서 설치하는 MCP 사이드카는 *클라이언트 스텁*이지 백엔드가 아닙니다.

### 사전 준비

- PATH에 Python **3.11 이상**.
- PyPI 접근 가능 (인스톨러가 `pip install 'kumiho[mcp]>=0.9.20'`을 실행합니다).
- 도달 가능한 Kumiho 컨트롤 플레인 URL과 서비스 토큰. `revka onboard`가 받아서 `~/.revka/.env`에 씁니다. 둘이 없어도 MCP 프로세스는 뜨지만 무상태(stateless)로 동작합니다.

### 자동 설치 단계

`./scripts/install-sidecars.sh`가 다음을 수행합니다.

1. `~/.revka/kumiho/venv/`를 만듭니다 (Python 3 virtualenv).
2. 그 venv 안에 `pip install 'kumiho[mcp]>=0.9.20'`을 실행합니다. `[mcp]` extra는 `kumiho.mcp_server`가 요구하는 `mcp>=1.0.0`과 `httpx>=0.27.0`을 함께 가져옵니다.
3. `~/.revka/kumiho/run_kumiho_mcp.py`를 작성합니다 — venv에서 `python -m kumiho.mcp_server`를 호출하는 얇은 셔임입니다 (pip이 venv `bin/`에 깔아 주는 `kumiho-mcp` 콘솔 스크립트와 동등).

이미 런처가 있으면 그대로 둡니다.

### 수동 설치 단계

```bash
mkdir -p ~/.revka/kumiho
python3 -m venv ~/.revka/kumiho/venv
~/.revka/kumiho/venv/bin/pip install --upgrade pip
~/.revka/kumiho/venv/bin/pip install "kumiho[mcp]>=0.9.20"

cat > ~/.revka/kumiho/run_kumiho_mcp.py <<'PY'
#!/usr/bin/env python3
import os, pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
VENV_PY = HERE / "venv" / "bin" / "python3"
if not VENV_PY.exists():
    VENV_PY = HERE / "venv" / "Scripts" / "python.exe"
os.execv(str(VENV_PY), [str(VENV_PY), "-m", "kumiho.mcp_server", *sys.argv[1:]])
PY
chmod +x ~/.revka/kumiho/run_kumiho_mcp.py
```

### 검증

```bash
# 패키지 임포트 스모크 테스트
~/.revka/kumiho/venv/bin/python3 -c 'import kumiho; print(kumiho.__version__)'

# 런처 스모크 테스트 (Ctrl-D로 종료; 프로토콜 배너만 찍고 대기 상태로 들어감)
~/.revka/kumiho/venv/bin/python3 -m kumiho.mcp_server --help 2>&1 | head -5

# 엔드투엔드 — Revka doctor가 배선을 점검
revka doctor
```

<!-- TODO screenshot: Kumiho 설정 전 비어 있는 MCP 도구 목록을 보여 주는 대시보드 도구 패널 -->
![Kumiho 설정 전 비어 있는 MCP 도구 목록을 보여 주는 대시보드 도구 패널](../../../assets/setup/kumiho-operator-01-mcp-tools-empty.png)

<!-- TODO screenshot: 정상 설정 후 Kumiho 메모리 도구가 로드된 대시보드 도구 패널 -->
![정상 설정 후 Kumiho 메모리 도구가 로드된 대시보드 도구 패널](../../../assets/setup/kumiho-operator-02-mcp-tools-loaded.png)

### 트러블슈팅

| 증상 | 조치 |
|---|---|
| `revka` 로그에 `Kumiho MCP script not found: ~/.revka/kumiho/run_kumiho_mcp.py` | `./scripts/install-sidecars.sh`를 실행하세요. |
| `ModuleNotFoundError: kumiho.mcp_server` 또는 `ModuleNotFoundError: mcp` | `~/.revka/kumiho/venv/bin/pip install -U 'kumiho[mcp]'`로 업그레이드하세요 (`[mcp]` extra가 필수). |
| 대시보드의 MCP 도구 목록이 비어 있음 | `~/.revka/logs/`에서 stderr 흔적을 확인하세요. `KUMIHO_AUTH_TOKEN`과 `KUMIHO_CONTROL_PLANE_URL`이 설정돼 있는지 점검하세요 (`revka onboard`가 `~/.revka/.env`에 씁니다). |
| Kumiho 컨트롤 플레인 도달 불가 | `~/.revka/config.toml`의 `[kumiho].api_url`이 도달 가능한 Kumiho 엔드포인트를 가리키는지 확인하세요. |

### 설정 배선

`~/.revka/config.toml` (`revka onboard`가 작성):

```toml
[kumiho]
enabled = true
mcp_path = "~/.revka/kumiho/run_kumiho_mcp.py"
api_url = "https://api.kumiho.cloud"   # 또는 자체 호스팅 URL
space_prefix = "Revka"
```

`kumiho.mcp_path` 결정 우선순위 (`src/agent/kumiho.rs` 참고):

1. 설정의 `kumiho.mcp_path`가 비어 있지 않으면 그 값.
2. `~/.revka/kumiho/run_kumiho_mcp.py` (기본 설치 위치).

---

## 2. Operator MCP 서버

### 무엇인가

**Operator**는 다중 에이전트 워크플로 오케스트레이션을 담당하는 Revka의 Python MCP 서버입니다. 약 89개의 MCP 도구를 노출합니다 — 에이전트 라이프사이클, 워크플로 실행, 팀 코디네이션, 패턴(리파인먼트, 맵-리듀스, 슈퍼바이저, 그룹 챗, 핸드오프). 소스는 리포지토리의 `operator-mcp/` 아래 있습니다.

### 사전 준비

- PATH에 Python **3.11 이상**.
- `make` (POSIX 환경에서 정식 설치 경로). Windows/.bat 쪽은 `make`를 건너뛰고 최소한의 rsync 대체로 처리합니다.
- (선택) Node.js 18 이상 — 라이브 실행 세션 매니저 사이드카가 필요할 때만. 없어도 Operator는 동작하지만 라이브 DAG 오버레이 이벤트가 중계되지 않습니다.

### 자동 설치 단계

`./scripts/install-sidecars.sh`가 POSIX에서 수행하는 일:

1. `make`가 있으면: `cd operator-mcp && make install` — 정식 install 타겟. `~/.revka/operator_mcp/venv/`를 만들고, `revka-operator[all]`을 `pip install`하고, 패키지 트리를 `~/.revka/operator_mcp/`로 rsync하고, Node.js 세션 매니저 사이드카를 빌드+설치하고, 오케스트레이션 스킬을 `~/.revka/skills/`에 복사하고, 부트스트랩 런처를 `~/.revka/operator_mcp/run_operator_mcp.py`에 둡니다.
2. `make`가 없으면: 최소 폴백 — `python3 -m venv`, `pip install operator-mcp/[all]`, `rsync operator_mcp/ ~/.revka/operator_mcp/`, `cp run_operator_mcp.py`.

Windows(`install-sidecars.bat`)는 항상 폴백 경로를 따르며, `rsync` 대신 `robocopy`를 씁니다.

### 수동 설치 단계

```bash
cd operator-mcp
make install
```

이게 전부입니다 — Makefile이 정식 배포 절차입니다. 컴포넌트 세부 사항은 [operator-mcp/README.md](../../../../operator-mcp/README.md) *(영문)* 를 보세요.

### 검증

```bash
# 패키지가 operator venv에 설치됐는지
~/.revka/operator_mcp/venv/bin/python3 -c 'import operator_mcp; print("ok")'

# 런처 스모크 테스트 — MCP 핸드셰이크 배너를 찍은 뒤 Ctrl-D 입력 시 종료
~/.revka/operator_mcp/venv/bin/python3 ~/.revka/operator_mcp/run_operator_mcp.py --help 2>&1 | head -5

# 부트스트랩 런처가 설치됐는지 확인
test -f ~/.revka/operator_mcp/run_operator_mcp.py && echo ok

# 엔드투엔드
revka doctor
```

### 트러블슈팅

| 증상 | 조치 |
|---|---|
| `run_operator_mcp.py`가 `ModuleNotFoundError: operator_mcp`로 죽음 | `operator-mcp/`에서 `make install`을 다시 돌리세요. 패키지 트리 복사가 실패했을 가능성이 높습니다. |
| `~/.revka/operator_mcp/requirements.txt`가 없음 | `cp operator-mcp/requirements.txt ~/.revka/operator_mcp/` 후 런처 재실행 — 공유 venv에 의존성이 다시 설치됩니다. |
| 워크플로 실행 중 라이브 DAG 오버레이가 비어 있음 | 세션 매니저 사이드카가 빠진 상태입니다. `cd operator-mcp && make build-ts && make install-ts`. |
| 대시보드 MCP 도구 목록에 워크플로 도구가 안 보임 | `~/.revka/logs/`에서 operator 프로세스 로그를 확인하고, `~/.revka/operator_mcp/venv/bin/python3 ~/.revka/operator_mcp/run_operator_mcp.py`를 직접 돌려 stderr를 관찰하세요. |

### 설정 배선

```toml
[operator]
enabled = true
mcp_path = "~/.revka/operator_mcp/run_operator_mcp.py"
```

결정 순서 (`src/agent/operator/mod.rs` 참고):

1. 설정의 `operator.mcp_path`가 비어 있지 않으면 그 값.
2. `~/.revka/operator_mcp/run_operator_mcp.py` (기본 설치 위치).

---

## 공유 venv 참고 사항

Operator 부트스트랩(`run_operator_mcp.py`)은 과거 `~/.cache/kumiho-claude/venv`라는 공유 venv를 써서 Kumiho gRPC 클라이언트 중복을 피했습니다. `operator-mcp/Makefile`은 *전용* venv를 `~/.revka/operator_mcp/venv`에 만듭니다. 두 레이아웃 다 동작합니다 — 부트스트랩은 공유 venv가 없으면 만들어 쓰는 폴백을 갖고 있습니다. 콜드 스타트마다 의존성이 다시 깔리는 것 같으면, 셸 전반에서 `XDG_CACHE_HOME`을 일관되게 설정하거나 `make install`로 전용 venv를 고정하세요.

## 관련 문서

- [one-click-bootstrap.md](one-click-bootstrap.md) — 풀 설치 진입점.
- [../../../operator-mcp/README.md](../../../../operator-mcp/README.md) — Operator 아키텍처와 배포 디테일 *(영문)*.
- [../reference/api/config-reference.md](../reference/api/config-reference.md) — 전체 `[kumiho]`/`[operator]` 설정 스키마 *(영문)*.
