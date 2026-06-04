# CanonWorks 사용 설명서

CanonWorks는 Revka 안에 들어가는 장편 연재 소설 제작 도구다. 별도 앱이 아니라 Operator MCP tool과 built-in workflow 묶음이며, Kumiho의 Project, Space, Item.kind, Revision, Artifact, Bundle, Edge를 사용해 장편 연재의 캐논을 기록하고 다음 회차 생산에 다시 주입한다.

핵심 원칙은 간단하다.

```text
오퍼레이터가 작품 seed를 준다.
CanonWorks가 Kumiho canon graph를 만든다.
Episode Factory가 회차와 canon patch candidate를 만든다.
Canon State Sync가 캐릭터 상태, 관계, 타임라인, 스토리라인, 떡밥 상태를 정산한다.
다음 Episode Factory가 정산된 상태를 읽는다.
```

중요한 점은 `project_config_yaml`을 사용자가 먼저 손으로 만드는 구조가 아니라는 것이다. 기본 흐름에서는 `canonworks_start`가 프로젝트명을 확인하는 즉시 Kumiho Project와 기본 Space scaffold를 만들고, 대화형으로 seed를 모은다. `canonworks_commit`은 내부적으로 `canonworks_init`를 호출해 실제 canon items, revisions, artifacts, bundles, relationship edges와 config artifact를 생성한다. 이후 `canonworks_run_episode`와 `canonworks_sync_state`가 저장된 config path를 자동으로 사용한다.

## 사용자 친화적이어야 하는 부분

CanonWorks를 일반 사용자가 쓰기 좋게 만들려면 도구의 중심을 YAML 편집이 아니라 “작품 운영 흐름”에 둬야 한다.

필요한 디자인은 다음이다.

1. `canonworks_start` 중심의 시작 화면
   - 프로젝트명이 정해지면 Kumiho Project와 기본 Space scaffold를 먼저 만든다.
   - 제목, 장르, 로그라인, 캐릭터, 관계, 타임라인, 떡밥을 질문으로 수집한다.
   - 사용자는 Space, Bundle, kref를 먼저 이해하지 않아도 된다.

2. Canon Graph 미리보기
   - 생성될 Project, Spaces, Bundles, Items, Edges를 실행 전에 보여준다.
   - 관계 endpoint가 캐릭터 id와 맞지 않으면 warning을 먼저 보여준다.

3. 두 단계 운영 버튼
   - “다음 회차 만들기”는 `canonworks_run_episode`가 `canonworks-serial-episode-factory`를 실행한다.
   - “캐논 상태 정산하기”는 `canonworks_sync_state`가 `canonworks-serial-canon-state-sync`를 실행한다.
   - 사용자는 workflow 이름보다 작업 의도를 먼저 본다.

4. 현재 상태 패널
   - 최근 production-ready episode
   - 최신 canon patch candidate
   - 현재 character state
   - 현재 relationship state
   - 현재 timeline/storyline/foreshadow progress
   - blocked episode와 audit failure

5. 안전한 revision UX
   - main canon은 직접 덮어쓰지 않는다.
   - 회차 생산은 patch candidate를 만들고, state sync는 current snapshot을 만든다.
   - risky relationship/timeline delta는 approval-gated로 보이게 한다.

6. 예제 workflow lineage 표시
   - CanonWorks built-in이 원본 2단계 workflow에서 왔다는 사실을 UI와 문서에 보여준다.
   - “원본 step 구조 + project-config 일반화”라는 계약을 명확히 보여줘야 사용자가 신뢰할 수 있다.

## 원본 예제에서 일반화된 구조

사용자가 처음 준 2단계 workflow는 특정 작품 전용이었다.

```text
manghan-developer-episode-factory
manghan-developer-canon-state-sync
```

CanonWorks built-in은 이 구조를 다음 이름으로 일반화한다.

```text
canonworks-serial-episode-factory
canonworks-serial-canon-state-sync
```

일반화 방식은 “workflow를 새로 만든 것”이 아니라 “원본 top-level step 구조를 유지하고, 작품 전용 값을 `project-config` step으로 빼낸 것”이다.

Episode Factory는 원본 예제 step에 `project-config`만 앞에 추가한다.

```text
project-config
latest-production-episode
next-episode-info
episode-context
volume-canon-alignment
relationship-pressure-plan
opencrab-reference-builder
episode-intent-planner
episode-beat-planner
episode-draft-writer
episode-prose-reviser
draft-canon-auditor
episode-finalizer
final-canon-auditor
final-gate-router
production-route-gate
canon-patch-builder
emit-final-episode
production-emit-gate
emit-canon-patch-candidate
emit-context-pack
production-output-gate
update-output-bundles
emit-blocked-episode
blocked-output-gate
update-blocked-bundle
run-summary
```

Canon State Sync도 원본 예제 step에 `project-config`만 앞에 추가한다.

```text
project-config
latest-production-episode
sync-info
canon-patch-candidate
state-sync-context
state-delta-context-lite
state-delta-extractor
state-delta-review
current-snapshot-builder
emit-character-state-snapshot
emit-relationship-state-snapshot
emit-timeline-progress-snapshot
emit-storyline-progress-snapshot
emit-foreshadow-progress-snapshot
emit-post-episode-sync-report
update-state-sync-bundles
run-summary
```

작품 전용 값은 다음처럼 config로 이동한다.

| 원본 예제의 고정값 | CanonWorks 일반화 위치 |
| --- | --- |
| `ManghanDev/Episodes` | `canon_project.spaces.episodes` |
| `ManghanDev/Patches` | `canon_project.spaces.patches` |
| `manghan-main-canon` | `canon_project.bundles.main_canon` |
| `manghan-current-character-states` | `canon_project.bundles.current_character_states` |
| `mg-ep-001` 같은 회차 이름 | `canon_project.naming.episode_name_prefix` |
| 작품 고유 synopsis, 캐릭터, 관계 | `canonworks_init`가 만드는 Kumiho items/artifacts |
| relationship map artifact | `canon_project.krefs.relationship_map_artifact` |
| roadmap | `canon_project.krefs.roadmap` |

## 제품형 시작 흐름

오퍼레이터에게 “캐논웍스 시작하자”라고 말했을 때의 MCP 흐름은 다음이다.

```text
canonworks_start
→ next_questions를 사용자에게 질문
→ canonworks_start(session_id, answers) 반복
→ canonworks_preview
→ canonworks_commit
→ canonworks_run_episode
→ canonworks_sync_state
```

### 1. 대화형 setup 시작

```json
{
  "seed": {
    "title": "Glass City",
    "project": "GlassCity",
    "story_slug": "glass-city"
  }
}
```

반환값:

```text
session_id
draft
readiness.ready_to_commit
readiness.blocking
readiness.warnings
project_scaffold.status
next_questions
preview
```

`next_questions`를 사용자에게 물어보고 답을 다시 넣는다.

```json
{
  "session_id": "<session_id>",
  "answers": {
    "premise": "기억 아카이브 위에 세워진 도시에서 벌어지는 연쇄 조작 사건.",
    "characters": [
      {
        "id": "mira",
        "display_name": "미라",
        "role": "lead",
        "summary": "기억 delta를 읽을 수 있는 조사관."
      }
    ]
  }
}
```

### 2. Preview 확인

```json
{
  "session_id": "<session_id>"
}
```

`canonworks_preview`는 Kumiho를 변경하지 않고 다음을 보여준다.

```text
생성될 spaces
생성될 bundles
생성될 items/artifacts
생성될 relationship edges
relationship endpoint warning
readiness
```

### 3. Commit

```json
{
  "session_id": "<session_id>"
}
```

`canonworks_commit`은 readiness가 충분하면 내부적으로 `canonworks_init`를 호출한다. 성공하면 `project_config_artifact_path`를 session/project state에 저장한다. 이후 사용자는 이 path를 직접 다루지 않아도 된다.

### 4. 다음 회차 만들기

```json
{
  "session_id": "<session_id>",
  "episode_goal": "첫 번째 아카이브 범죄를 제시하고, 마지막에 구체적인 기억 불일치로 끝낸다.",
  "must_include": "미라와 준이 업무상 충돌하는 장면.",
  "avoid": "준의 사설 아카이브는 아직 공개하지 않는다.",
  "pacing_mode": "balanced"
}
```

`canonworks_run_episode`가 저장된 config path를 사용해 `canonworks-serial-episode-factory`를 실행한다.

### 5. 상태 정산하기

```json
{
  "session_id": "<session_id>",
  "apply_mode": "propose_only",
  "review_focus": "미라, 준, 첫 아카이브 범죄, 사설 아카이브 떡밥"
}
```

`canonworks_sync_state`가 저장된 config path를 사용해 `canonworks-serial-canon-state-sync`를 실행한다.

## 저수준 1단계: Canon Graph 직접 만들기

필요하면 대화형 product layer를 건너뛰고 Operator MCP tool `canonworks_init`를 직접 호출할 수 있다.

최소 입력:

```json
{
  "title": "Glass City",
  "project": "GlassCity",
  "story_slug": "glass-city",
  "premise": "기억 아카이브 위에 세워진 도시를 다루는 장편 연재물."
}
```

권장 입력:

```json
{
  "title": "Glass City",
  "project": "GlassCity",
  "story_slug": "glass-city",
  "premise": "기억 아카이브 위에 세워진 도시에서 벌어지는 연쇄 조작 사건.",
  "synopsis": "미라는 죽은 사람의 기억까지 수정되는 도시 아카이브에서 첫 번째 조작 살인을 추적한다.",
  "language": "ko-KR",
  "cadence": "web_serial",
  "target_length": "6000",
  "genre_modules": [
    "serialized-mystery",
    "relationship-drama",
    "long-arc-payoff"
  ],
  "themes": [
    "기억은 인프라인가",
    "운영 압력 속의 진실"
  ],
  "canon_guardrails": [
    "인물이 비밀을 알게 되려면 반드시 회차 artifact에 reveal이 기록되어야 한다.",
    "관계의 큰 변화는 canon patch candidate를 먼저 거쳐야 한다."
  ],
  "characters": [
    {
      "id": "mira",
      "display_name": "미라",
      "role": "lead",
      "summary": "기억 delta를 읽을 수 있는 조사관.",
      "traits": ["정밀함", "감춤", "집착"]
    },
    {
      "id": "jun",
      "display_name": "준",
      "role": "rival",
      "summary": "사설 아카이브를 숨긴 시스템 감사관.",
      "traits": ["침착함", "전략적", "불신"]
    }
  ],
  "relationships": [
    {
      "from": "mira",
      "to": "jun",
      "edge_type": "RIVAL_OF",
      "label": "업무상 라이벌",
      "summary": "서로가 필요하지만 둘 다 상대가 증거를 편집한다고 의심한다."
    }
  ],
  "timeline_events": [
    {
      "position": "prelude",
      "summary": "도시 아카이브가 최초의 인간 기억 백업을 받아들인다."
    }
  ],
  "storylines": [
    {
      "id": "archive-murder",
      "summary": "미라가 피해자의 기억된 하루를 수정해 벌어진 살인을 추적한다.",
      "goal": "아카이브의 write path를 폭로한다."
    }
  ],
  "foreshadow_threads": [
    {
      "id": "jun-private-archive",
      "summary": "준은 아카이브 내부 구조를 인정하는 것보다 더 많이 알고 있다.",
      "payoff_target": "volume-01-finale"
    }
  ],
  "style_guide": "근접 3인칭, 높은 연속성 압력, 절제된 기술 은유.",
  "external_reference_seed": "serialized mystery continuity, character state tracking, clue fairness"
}
```

`canonworks_init`가 만드는 것:

```text
Project spaces
core bundles
series bible
canon synopsis
character index
relationship map
timeline
long-arc roadmap
production style guide
character items
current state/progress snapshots
relationship revision edges
canonworks project config artifact
```

중요한 반환값:

```text
project_config_yaml
project_config_item_kref
project_config_revision_kref
project_config_artifact_path
created.spaces
created.bundles
created.items
created.revisions
created.artifacts
created.bundle_members
created.edges
created.warnings
next_workflows
```

다음 workflow에는 보통 `project_config_artifact_path`를 넘긴다.

## 저수준 2단계: 다음 회차 만들기

직접 workflow를 호출할 때는 `canonworks-serial-episode-factory`를 실행한다.

```json
{
  "workflow": "canonworks-serial-episode-factory",
  "cwd": "G:\\git\\KumihoIO\\Revka",
  "inputs": {
    "project_config_yaml": "<canonworks_init가 반환한 project_config_artifact_path>",
    "target_length": "6000자",
    "episode_goal": "첫 번째 아카이브 범죄를 제시하고, 마지막에 구체적인 기억 불일치로 끝낸다.",
    "must_include": "미라와 준이 업무상 충돌하는 장면.",
    "avoid": "준의 사설 아카이브는 아직 공개하지 않는다.",
    "continuity_context": "첫 production episode.",
    "pacing_mode": "balanced",
    "initial_episode_number": 1,
    "initial_volume": 1
  }
}
```

기대 산출물:

```text
production-ready episode revision
locked context pack artifact
canon patch candidate revision
production episode bundle update
volume bundle update
blocked draft if final audit blocks publication
```

이 workflow는 main canon을 직접 수정하지 않는다. 회차와 patch candidate를 만들고, 다음 단계가 상태 변화를 정산한다.

## 저수준 3단계: 캐논 상태 정산하기

직접 workflow를 호출할 때는 회차가 production-ready가 된 뒤 `canonworks-serial-canon-state-sync`를 실행한다.

```json
{
  "workflow": "canonworks-serial-canon-state-sync",
  "cwd": "G:\\git\\KumihoIO\\Revka",
  "inputs": {
    "project_config_yaml": "<canonworks_init가 반환한 project_config_artifact_path>",
    "apply_mode": "propose_only",
    "continuity_context": "안전한 operational state만 반영하고, 관계/타임라인의 큰 변화는 approval-gated로 남긴다.",
    "review_focus": "미라, 준, 첫 아카이브 범죄, 사설 아카이브 떡밥"
  }
}
```

특정 회차를 다시 정산하거나 backfill하려면 다음 입력을 추가한다.

```json
{
  "target_episode_number": "12",
  "target_episode_kref": "kref://GlassCity/Episodes/ep-012.webnovel-episode?r=3",
  "target_patch_kref": "kref://GlassCity/Patches/ep-012-canon-patch.canon-patch?r=1",
  "bootstrap_mode": "sequential_backfill"
}
```

기대 산출물:

```text
current character state snapshot
current relationship state snapshot
current timeline progress snapshot
current storyline progress snapshot
current foreshadow progress snapshot
canon state sync report
state sync bundle updates
```

## 운영 루프

```text
canonworks_start
→ canonworks_preview
→ canonworks_commit
→ canonworks-serial-episode-factory
→ production-ready episode + canon patch candidate
→ canonworks-serial-canon-state-sync
→ current state/progress snapshots
→ canonworks_run_episode
```

이 루프의 제품적 의미는 “작가의 머릿속 정산”을 Kumiho graph로 외부화하는 것이다.

```text
방금 회차로 캐릭터 상태가 바뀌었는가?
관계가 너무 빨리 진전되었는가?
타임라인에 irreversible event가 생겼는가?
떡밥은 심긴 상태인가, 회수 직전인가?
다음 회차가 반드시 이어받아야 할 압력은 무엇인가?
```

## 재실행 동작

`canonworks_init`는 destructive reset이 아니다.

```text
기존 space는 재사용한다.
기존 bundle은 재사용한다.
기존 item은 재사용한다.
seed docs/config는 새 revision과 artifact를 만든다.
bundle membership은 Kumiho가 이미 존재한다고 보고하면 중복 추가하지 않는다.
관계 edge는 이번 실행에서 만든 character revision 사이에 생성한다.
```

그래서 두 번째 `canonworks_init` 실행은 “새 bootstrap revision pass”다. 기존 canon data를 삭제하지 않고 main canon을 덮어쓰지도 않는다.

## 검증 명령

소스 검증:

```powershell
python -m pytest operator-mcp\tests\test_canonworks_tool.py operator-mcp\tests\test_builtin_workflows.py -q
python -m py_compile operator-mcp\operator_mcp\operator_mcp.py operator-mcp\operator_mcp\tool_handlers\canonworks.py
```

설치된 Operator tool catalog 확인:

```powershell
$py = "$env:USERPROFILE\.revka\operator_mcp\venv\Scripts\python.exe"
$env:PYTHONPATH = "$env:USERPROFILE\.revka"
@'
import asyncio
from operator_mcp.operator_mcp import list_tools

async def main():
    tools = await list_tools()
    print(any(t.name == "canonworks_init" for t in tools))

asyncio.run(main())
'@ | & $py -
```

설치된 workflow validation:

```powershell
$py = "$env:USERPROFILE\.revka\operator_mcp\venv\Scripts\python.exe"
$env:PYTHONPATH = "$env:USERPROFILE\.revka"
@'
import asyncio, json
from operator_mcp.operator_mcp import KUMIHO_SDK
from operator_mcp.tool_handlers.workflows import tool_validate_workflow

async def main():
    if hasattr(KUMIHO_SDK, "_lazy_init"):
        KUMIHO_SDK._lazy_init()
    for workflow in ["canonworks-serial-episode-factory", "canonworks-serial-canon-state-sync"]:
        result = await tool_validate_workflow({"workflow": workflow, "cwd": r"G:\git\KumihoIO\Revka"})
        print(json.dumps({"workflow": workflow, "valid": result.get("valid"), "errors": len(result.get("errors") or [])}, ensure_ascii=False))

asyncio.run(main())
'@ | & $py -
```

서비스 확인:

```powershell
$revka = "$env:USERPROFILE\.revka\bin\revka.exe"
& $revka service status
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:42617/health
```

## 다음 UX 작업

문서와 workflow만으로는 아직 충분히 친절하지 않다. 다음 순서로 만들면 된다.

1. Project Dashboard
   - project_config_artifact_path를 숨기고 “이 프로젝트로 실행” 버튼을 제공한다.
   - 최근 회차, 최신 patch candidate, 최신 current snapshot을 보여준다.

2. Runbook Buttons
   - “다음 회차 만들기”
   - “방금 회차 상태 정산하기”
   - “특정 회차 다시 정산하기”
   - “blocked episode 검토하기”

3. Canon Diff Review
   - state sync가 만든 delta를 사람이 승인/반려할 수 있게 한다.
   - relationship/timeline risky delta는 별도 queue로 보여준다.

4. Example Lineage Inspector
   - CanonWorks workflow가 원본 예제의 어떤 step을 일반화했는지 보여준다.
   - project-specific literal이 config의 어느 field로 이동했는지 표시한다.
