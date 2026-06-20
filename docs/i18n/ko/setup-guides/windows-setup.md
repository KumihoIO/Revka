# Windows 설치 가이드

Windows에서 Revka를 빌드하고 설치하는 절차입니다.

## 빠른 시작

### 옵션 A: 한 번에 끝내는 셋업 스크립트

리포지토리 루트에서:

```cmd
setup.bat
```

스크립트가 환경을 자동 감지하고 설치 단계를 안내합니다. 대화형 메뉴를 건너뛰려면 플래그를 함께 줄 수 있습니다.

| 플래그 | 설명 |
|---|---|
| `--prebuilt` | 사전 컴파일된 바이너리 다운로드 (가장 빠름) |
| `--minimal` | 기본 피처만 켜고 빌드 |
| `--standard` | Matrix + Lark/Feishu + Postgres 포함 빌드 |
| `--full` | 모든 피처 포함 빌드 |

### 옵션 B: Scoop (패키지 매니저)

```powershell
scoop bucket add revka https://github.com/KumihoIO/scoop-revka
scoop install revka
```

### 옵션 C: 수동 빌드

```cmd
rustup target add x86_64-pc-windows-msvc
cargo build --release --locked --features channel-matrix,channel-lark --target x86_64-pc-windows-msvc
copy target\x86_64-pc-windows-msvc\release\revka.exe %USERPROFILE%\.revka\bin\
```

## 사전 준비

| 요구 사항 | 필수? | 메모 |
|---|---|---|
| Git | 예 | [git-scm.com/download/win](https://git-scm.com/download/win) |
| Rust 1.89 이상 | 예 | `setup.bat`이 없으면 자동 설치 |
| Visual Studio Build Tools | 예 (소스 빌드 시) | MSVC 링커를 위한 C++ 워크로드 필요 |
| Node.js | 아니오 | 웹 대시보드를 소스에서 빌드할 때만 |

<!-- TODO screenshot: C++ 빌드 도구 워크로드가 선택된 Visual Studio Build Tools 설치 다이얼로그 -->
![C++ 빌드 도구 워크로드가 선택된 Visual Studio Build Tools 설치 다이얼로그](../../../assets/setup/windows-setup-01-vs-build-tools-installer.png)

### Visual Studio Build Tools 설치

Visual Studio가 없다면 Build Tools를 설치하세요.

1. [visualstudio.microsoft.com/visual-cpp-build-tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)에서 다운로드
2. **"C++를 사용한 데스크톱 개발(Desktop development with C++)"** 워크로드 선택
3. 설치 후 터미널 재시작

Visual Studio 2019 이상에 C++ 워크로드를 이미 설치했다면 추가 작업은 필요 없습니다.

## 피처 플래그

Revka는 Cargo 피처 플래그로 어떤 통합을 컴파일할지 제어합니다.

| 피처 | 설명 | 기본 포함? |
|---|---|---|
| `channel-lark` | Lark/Feishu 메시징 | 예 |
| `channel-nostr` | Nostr 프로토콜 | 예 |
| `observability-prometheus` | Prometheus 메트릭 | 예 |
| `skill-creation` | 자동 스킬 생성 | 예 |
| `channel-matrix` | Matrix 프로토콜 | 아니오 |
| `browser-native` | 헤드리스 브라우저 | 아니오 |
| `hardware` | USB 장치 지원 | 아니오 |
| `rag-pdf` | RAG용 PDF 추출 | 아니오 |
| `observability-otel` | OpenTelemetry | 아니오 |

특정 피처로 빌드:

```cmd
cargo build --release --locked --features channel-matrix,channel-lark --target x86_64-pc-windows-msvc
```

<!-- TODO screenshot: Windows 환경에서 revka onboard가 성공적으로 끝난 PowerShell 터미널 -->
![Windows 환경에서 revka onboard가 성공적으로 끝난 PowerShell 터미널](../../../assets/setup/windows-setup-02-revka-onboard-terminal.png)

<!-- TODO screenshot: 온보딩 후 http://127.0.0.1:42617의 Revka 대시보드를 보여 주는 Edge 브라우저 -->
![온보딩 후 http://127.0.0.1:42617의 Revka 대시보드를 보여 주는 Edge 브라우저](../../../assets/setup/windows-setup-03-dashboard-browser.png)

## 설치 후 단계

1. **터미널을 다시 열어** PATH 변경 사항을 반영합니다.
2. **Revka 초기화**:
   ```cmd
   revka onboard
   ```
3. `%USERPROFILE%\.revka\config.toml`에 **API 키 설정**.
4. `http://127.0.0.1:42617`에서 **게이트웨이 + 대시보드 시작**:
   ```cmd
   revka gateway
   ```
   또는 풀 런타임(게이트웨이 + 채널 + 하트비트 + 크론 스케줄러):
   ```cmd
   revka daemon
   ```

## 트러블슈팅

### 빌드 시 링커 에러

Visual Studio Build Tools를 C++ 워크로드와 함께 설치하세요. MSVC 링커가 필요합니다.

### `cargo build`가 메모리 부족으로 실패

소스 빌드는 최소 2 GB의 빈 RAM이 필요합니다. 대신 `setup.bat --prebuilt`로 사전 컴파일된 바이너리를 받으세요.

### Feishu / Lark가 안 보임

Feishu와 Lark는 같은 플랫폼입니다. `channel-lark` 피처를 켜고 빌드하세요.

```cmd
cargo build --release --locked --features channel-lark --target x86_64-pc-windows-msvc
```

### 웹 대시보드가 빠져 있음

웹 대시보드는 빌드 시점에 Node.js와 npm이 필요합니다. Node.js를 설치하고 다시 빌드하거나, 대시보드가 포함된 사전 빌드 바이너리를 사용하세요.
