# Construct 선언형 워크플로 — HOW-TO 가이드

> 원문: [`WORKFLOWS.md`](../../../WORKFLOWS.md). 이 문서는 한국어 번역본이며, 정식 명세는 영문이 기준입니다. 동작 변경이 영문에 먼저 반영될 수 있으니 의심스러우면 원문을 함께 확인하세요.

## 개요

Construct 워크플로는 다중 단계·다중 에이전트 파이프라인을 정의하는 YAML 파일입니다. Operator는 이를 결정적으로 실행합니다 — 데이터를 resolve하고, 에이전트를 띄우고, 조건에 따라 분기하고, 엔티티를 발행하고, 다운스트림 워크플로로 체이닝하는 전 과정을 수동 오케스트레이션 없이 처리합니다.

```
YAML 정의 → Operator 검증 → Executor가 단계 실행 → 엔티티 발행 → 다운스트림 트리거
```

---

## Quick Start

### 1. 워크플로가 사는 곳

| 우선순위 | 경로 | 용도 |
|----------|------|---------|
| 3 (가장 높음) | `.construct/workflows/` | 프로젝트 로컬 오버라이드 |
| 2 | `~/.construct/workflows/` | 사용자 전역 워크플로 |
| 1 (가장 낮음) | `operator/workflow/builtins/` | 기본 내장 워크플로 |

뒤쪽 소스가 앞쪽을 덮어씁니다. 디스크에서 워크플로를 찾지 못하면 Operator는 마지막 폴백으로 **Kumiho** (`Construct/Workflows` 스페이스)를 확인합니다.

### 2. 최소 워크플로

```yaml
name: hello-world
version: "1.0"
description: A simple two-step workflow.

steps:
  - id: greet
    type: agent
    agent:
      agent_type: claude
      role: researcher
      prompt: "Say hello in three languages."

  - id: summary
    type: output
    depends_on: [greet]
    output:
      format: text
      template: "Agent said: ${greet.output}"
```

### 3. 워크플로 실행하기

- **Operator CLI**: AI 어시스턴트에게 워크플로 실행을 요청 (예: "run quantum-soul-arc-room")
- **API**: `POST /api/workflows/run/{name}`, 선택적으로 `{"inputs": {...}, "cwd": "..."}` 본문 전달
- **Cron**: `triggers:` 블록 추가 — 저장 시 Construct가 스케줄을 자동 등록
- **이벤트 체인**: 이전 워크플로의 출력 엔티티가 이 워크플로를 자동으로 트리거

### 4. 대시보드 에디터와 실행 제어

대시보드는 워크플로 정의와 워크플로 실행 인스턴스를 분리해서 다룹니다:

- **정의 탭**(`/workflows`)은 워크플로 정의를 편집, 복제, 비활성화, 삭제, 시작합니다.
- **실행 탭**(`/workflows`, `/runs`)은 실행 인스턴스를 선택하고 실행 범위 제어만 제공합니다: 활성 실행 중지, 실패 실행 재시도, 선택한 실행 기록 삭제.
- YAML 드로어는 그래프 에디터 표면입니다. YAML 텍스트 변경은 저장 전에 그래프에 적용해야 하며, Save는 그래프를 다시 YAML로 직렬화한 뒤 게이트웨이 검증을 거쳐 Kumiho 리비전을 만듭니다.
- 실행 뷰어는 가능하면 실행이 사용한 워크플로 리비전에 고정되므로 이후 정의 편집이 표시 중인 실행 그래프를 바꾸지 않습니다.

---

## 워크플로의 구조

```yaml
name: my-workflow              # 고유 식별자 (slug이 됨)
version: "1.0"                 # 시맨틱 버전
description: What this does.
tags: [domain, category]

triggers:                      # 선택 — 자동 실행 조건
  - cron: "0 9 * * 1"         # 시간 기반 (cron 표현식)
  - on_kind: "report"         # 이벤트 기반 (엔티티 kind + tag)
    on_tag: "ready"
    input_map:
      report_kref: "${trigger.entity_kref}"

inputs:                        # 타입 있는 파라미터
  - name: topic
    type: string               # string | number | boolean | list
    required: true
    default: ""
    description: The topic to research.

outputs:                       # 호출자에게 노출할 명명된 출력
  - name: result
    source: "${final_step.output}"

steps:                         # 최소 한 개 단계 필수
  - id: step_1
    type: agent
    ...
```

---

## 단계 타입

### `agent` — LLM 에이전트 띄우기

```yaml
- id: research
  type: agent
  depends_on: []
  agent:
    agent_type: claude         # claude 또는 codex
    role: researcher           # coder, researcher, reviewer 등
    prompt: |
      Research ${inputs.topic} and summarize findings.
    output_fields: [summary, score] # 선택 — 필수 구조화 출력 계약
    model: null                # 선택 — 모델 오버라이드
    timeout: 300               # 초 (기본 300)
    template: my-template      # 선택 — 에이전트 풀 템플릿
  skills:
    - "kref://CognitiveMemory/Skills/some-skill.skilldef"
  retry: 1                    # 실패 시 1회 재시도
  retry_delay: 10             # 재시도 간 10초 대기
```

`action` 필드는 단축형입니다: `action: research` 한 줄로 `ACTION_DEFAULTS`에 따라 `type: agent`, `role: researcher`, `agent_type: claude`가 자동 설정됩니다.

#### 에이전트 구조화 출력

에이전트 단계는 `output_data`를 통해 구조화 필드를 노출할 수 있습니다.

`agent.output_fields`가 설정되어 있으면 Construct는 이를 필수 구조화 출력
계약으로 처리합니다:

1. Executor가 에이전트 프롬프트에 구조화 출력 지시를 붙입니다.
2. 에이전트는 선언된 모든 필드를 반환해야 합니다.
3. 누락된 필드가 있으면 단계가 `structured_output_missing`으로 실패합니다.

```yaml
- id: final-canon-auditor
  type: agent
  agent:
    role: reviewer
    prompt: "Review canon consistency."
    output_fields: [verdict, production_ready]
```

권장 최종 형식:

```yaml
FINAL_OUTPUT:
  verdict: NEEDS_CHANGES
  production_ready: false
```

지원되는 구조화 출력 형식:

- 전체 JSON 객체: `{"verdict":"APPROVED","production_ready":true}`
- 마지막 fenced `json` 블록
- 마지막 `FINAL_OUTPUT:` YAML 블록

파싱된 필드는 `output_data`에 병합되므로 다운스트림 단계는
`${final-canon-auditor.output_data.production_ready}` 또는
`final_canon_auditor.output_data.production_ready` 같은 표현식 alias를 사용할
수 있습니다. `output_fields`가 없는 에이전트 출력은 기존 호환성을 유지합니다.
유효한 JSON, fenced JSON, `FINAL_OUTPUT`은 있으면 파싱하지만, 필드 누락으로
단계를 실패시키지는 않습니다.

### `shell` — 셸 명령 실행

```yaml
- id: build
  type: shell
  shell:
    command: "cd ${inputs.project_dir} && npm run build"
    timeout: 60
    allow_failure: false       # true로 두면 비-0 종료여도 워크플로가 실패하지 않음
```

### `resolve` — Kumiho 엔티티 결정적 조회 (LLM 미사용)

```yaml
- id: resolve_cursor
  type: resolve
  resolve:
    kind: "qs-episode-final"   # 엔티티 kind (정확히 일치)
    tag: "published"           # 리비전 tag (정확히 일치)
    name_pattern: ""           # 선택 — 엔티티 이름에 대한 glob 필터
    space: ""                  # 스페이스 경로 필터 (기본: Construct/WorkflowOutputs)
    mode: latest               # latest = 최신 한 건 | all = 목록
    fields: [part, episode_number, arc_name]  # 추출할 메타데이터 필드 (비우면 전체)
    fail_if_missing: false     # false = 결과가 없어도 실패시키지 않음
```

**출력 데이터** (`${resolve_cursor.output_data.*}`로 접근):

| 필드 | 값 |
|-------|-------|
| `found` | `true` 또는 `false` |
| `item_kref` | Kumiho 아이템 kref |
| `revision_kref` | Kumiho 리비전 kref |
| `name` | 엔티티 이름 |
| `<field>` | `fields` 목록의 각 필드, 또는 `fields`가 비어 있으면 모든 메타데이터 |

### `conditional` — 표현식으로 분기

```yaml
- id: gate
  type: conditional
  depends_on: [review]
  conditional:
    branches:
      - condition: "${review.output} contains APPROVED"
        goto: publish
      - condition: "${review.status} == 'failed'"
        goto: fix
      - condition: default      # 폴백
        goto: fix
```

지원 연산자: `==`, `!=`, `contains`, `>`, `<`, `>=`, `<=`. goto 대상에 `"end"`를 넣으면 워크플로를 종료합니다.

### `parallel` — 단계를 동시 실행

```yaml
- id: fan_out
  type: parallel
  parallel:
    steps: [step_a, step_b, step_c]
    join: all                  # all | any | majority
    max_concurrency: 5         # 1-10
```

| Join 전략 | 동작 |
|---------------|----------|
| `all` | 모든 분기 대기, 하나라도 실패하면 실패 |
| `any` | 첫 성공이 승, 나머지는 취소 |
| `majority` | 과반 성공 필요 |

### `goto` — 가드 있는 루프

```yaml
- id: retry_loop
  type: goto
  depends_on: [check_quality]
  goto:
    target: improve            # 되돌아갈 단계 ID
    condition: "${check_quality.output} contains NEEDS_WORK"
    max_iterations: 3          # 안전 상한 (1-20)
```

### `output` — 결과 발행 및 (선택) 엔티티 게시

```yaml
- id: report
  type: output
  depends_on: [analyze]
  output:
    format: markdown           # text | json | markdown
    template: |
      # Analysis Report
      ${analyze.output}

    # 선택: Kumiho 엔티티로 발행 (다운스트림 워크플로 트리거)
    entity_name: "analysis-${inputs.topic}"
    entity_kind: "analysis-report"
    entity_tag: "ready"
    entity_space: "Construct/WorkflowOutputs"   # 기본 스페이스
    entity_metadata:
      topic: "${inputs.topic}"
      summary: "${analyze.output}"
```

`entity_name`과 `entity_kind`가 모두 설정되면 executor는 다음을 수행합니다:
1. `entity_space`에 Kumiho 아이템 생성
2. 렌더링된 템플릿을 본문으로 하는 리비전 생성
3. 리비전에 `entity_tag` 부여
4. `revision.tagged` 이벤트 발생 — 다운스트림 워크플로를 트리거할 수 있음

**Output data**에는 다운스트림에서 참조할 수 있는 `entity_kref`와 `entity_revision_kref`가 포함됩니다.

### `human_approval` — Y/N 승인 대기

```yaml
- id: approve
  type: human_approval
  human_approval:
    message: "Deploy to production?"
    timeout: 3600              # 1시간
```

### `human_input` — 자유 텍스트 입력 대기

```yaml
- id: ask_user
  type: human_input
  human_input:
    message: "What changes do you want?"
    channel: dashboard
    timeout: 3600
```

응답은 다운스트림 단계에서 `${ask_user.output}`로 사용 가능합니다.

### `a2a` — 외부 A2A 에이전트 호출

```yaml
- id: external
  type: a2a
  a2a:
    url: "https://agent.example.com/a2a"
    skill_id: "analyze-data"
    message: "Analyze: ${inputs.data}"
    timeout: 300
```

### 오케스트레이션 패턴

| 타입 | 용도 |
|------|---------|
| `map_reduce` | 분할 fan-out 후 reduce |
| `supervisor` | 동적 위임 루프 |
| `group_chat` | 모더레이트되는 다중 에이전트 토론 |
| `handoff` | 한 에이전트에서 다음 에이전트로 컨텍스트 전달 |

---

## 변수 보간

단계의 모든 문자열 필드는 `${...}` 보간을 지원합니다. 변수는 실행 시점에 현재 워크플로 상태에서 해석됩니다.

### 네임스페이스

```
${inputs.name}                    워크플로 입력 파라미터
${trigger.entity_kref}            트리거 엔티티 kref
${trigger.entity_name}            트리거 엔티티 이름
${trigger.entity_kind}            트리거 엔티티 kind
${trigger.tag}                    트리거 태그
${trigger.revision_kref}          트리거 리비전 kref
${trigger.metadata.key}           트리거 엔티티 메타데이터 필드

${step_id.output}                 단계 텍스트 출력
${step_id.status}                 completed | failed | running | skipped
${step_id.error}                  에러 메시지 (실패 시)
${step_id.output_data.key}        구조화 출력 필드
${step_id.files}                  변경된 파일 목록 (콤마 구분)
${step_id.agent_id}               에이전트 ID (agent 단계용)

${loop.iteration}                 현재 goto 루프 카운트
${env.VAR}                        환경 변수
${run_id}                         워크플로 실행 UUID
```

**해석되지 않은 변수**는 `${...}` 문자열 그대로 남습니다 (output_data 키가 없으면 빈 문자열).

---

## 트리거와 워크플로 체이닝

### Cron 트리거

```yaml
triggers:
  - cron: "0 9 * * 1"           # 매주 월요일 오전 9시
```

UI를 통해 cron 트리거가 있는 워크플로를 Kumiho에 저장하면 Construct가 스케줄 잡으로 자동 등록합니다. 스케줄 시각이 되면 cron 스케줄러가 직접 `POST /api/workflows/run/{name}`을 호출합니다.

> **참고:** Cron 단독 트리거에는 `on_kind`/`on_tag`가 필요 없습니다. 이 두 필드는 엔티티 기반 트리거에서만 사용합니다.

### 엔티티 트리거

```yaml
triggers:
  - on_kind: "qs-arc-plan"      # 이 엔티티 kind를 감시
    on_tag: "ready"             # 이 태그가 붙으면 발화
    on_name_pattern: "qs-*"     # 선택 — 엔티티 이름에 대한 glob
    input_map:                  # 트리거 데이터 → 워크플로 입력 매핑
      arc_kref: "${trigger.entity_kref}"
      arc_name: "${trigger.metadata.arc_name}"
```

이벤트 리스너는 `/Construct/WorkflowOutputs`에서 발생하는 `revision.tagged` 이벤트를 감시합니다. output 단계가 트리거 규칙과 일치하는 엔티티를 발행하면 다운스트림 워크플로가 자동 실행됩니다.

**자동 매핑**: 트리거 엔티티의 메타데이터 키가 다운스트림 워크플로의 필수 입력 이름과 일치하면 자동으로 매핑됩니다 — 명시적 `input_map`이 없어도 됩니다.

### 체이닝 예시

```
quantum-soul-arc-room
  └─ output 단계가 발행: kind=qs-arc-plan, tag=ready
       └─ 이벤트 리스너가 quantum-soul-episode-room의 트리거와 매칭
            └─ quantum-soul-episode-room이 arc 컨텍스트와 함께 실행
                 └─ output 단계가 발행: kind=qs-episode-final, tag=published
                      └─ 다음 arc-room 실행 시 이 항목이 cursor로 resolve됨
```

---

## 다중 실행 연속성 패턴

이 패턴은 이전 실행을 이어받는 워크플로의 핵심 구조입니다.

### 풀어야 할 문제

워크플로가 매주 실행됩니다. 매 실행은 이전 실행에서 무엇이 일어났는지(마지막에 쓴 에피소드, 마지막에 계획한 arc 등)를 상태를 하드코딩하지 않고 알아야 합니다.

### 해법: resolve + seed 입력 + 엔티티 발행

```yaml
inputs:
  - name: arc_name
    default: "awakening-arc-1"       # 첫 실행용 시드
    description: 이후 실행에서는 자동 resolve

steps:
  # 1. 이전 출력 찾기 시도 (첫 실행에서는 비어 있음)
  - id: resolve_prior
    type: resolve
    resolve:
      kind: "qs-arc-plan"
      tag: "ready"
      fail_if_missing: false         # 아직 아무것도 없어도 실패시키지 않음

  # 2. 에이전트가 resolve된 데이터 또는 시드 입력을 사용
  - id: plan
    type: agent
    depends_on: [resolve_prior]
    agent:
      prompt: |
        ## 이전 실행에서 자동 resolve (첫 실행에서는 비어 있음)
        Previous arc: ${resolve_prior.output_data.arc_name}
        Episode range: ${resolve_prior.output_data.episode_range}
        Continuity: ${resolve_prior.output_data.continuity_context}

        ## Seed 입력 (자동 resolve가 비어 있을 때 사용)
        Arc name: ${inputs.arc_name}

        Use auto-resolved values when available; fall back to seeds on first run.

  # 3. 다음 실행이 찾을 수 있도록 엔티티 발행
  - id: output
    type: output
    depends_on: [plan]
    output:
      template: "${plan.output}"
      entity_name: "qs-arc-${inputs.arc_name}"
      entity_kind: "qs-arc-plan"
      entity_tag: "ready"
      entity_metadata:
        arc_name: "${inputs.arc_name}"
        episode_range: "1-8"
        continuity_context: "${plan.output}"
```

**첫 실행**: `resolve_prior.output_data.found = false`, 모든 필드가 비어 있음. 에이전트가 시드 입력을 사용. output이 엔티티를 발행.

**두 번째 실행**: `resolve_prior`가 1번 실행이 만든 엔티티를 찾음. 에이전트가 resolve된 연속성을 사용. output이 새 엔티티를 발행 (다음 반복용).

### 핵심 규칙

1. 비어 있을 수 있는 resolve 단계에는 항상 `fail_if_missing: false`
2. 첫 실행을 위한 합리적인 기본값을 `inputs`에 두기
3. 프롬프트를 resolve 섹션과 시드 섹션 양쪽으로 구성
4. 다음 실행에서 필요한 모든 것을 `entity_metadata`에 저장
5. output 단계의 `entity_kind` + `entity_tag`가 resolve 단계의 검색 조건과 일치해야 함

---

## 저장과 아티팩트 영속

UI에서 워크플로를 저장하면:

1. **API가** `PUT /api/workflows/{kref}`로 YAML 정의를 받음
2. 정의를 메타데이터로 담은 **Kumiho 리비전** 생성
3. **YAML이 디스크에 기록됨** — `~/.construct/workflows/{slug}.r{N}.yaml`
4. 그 파일을 가리키는 **Kumiho 아티팩트** 등록 — `file:///.../{slug}.r{N}.yaml`
5. (아티팩트 첨부 후) **리비전을 `published`로 태깅**
6. cron 트리거가 있으면 **cron 잡 동기화**

`resolve_kref`가 동작하는 이유는 바로 이 아티팩트 때문입니다 — Kumiho에 등록된 워크플로를 Operator가 로드해야 할 때, kref를 디스크 상의 파일로 resolve합니다.

**리비전 파일** (`.r{N}.yaml`)은 디렉토리 스캔에서 잡히지 않습니다. 로더의 파일시스템 스캔은 베이스 파일(`workflow-name.yaml`)만 발견합니다. 리비전 파일은 오직 kref resolve를 통해서만 접근됩니다.

---

## 종합 예시: Quantum Soul Arc Room

5개 페이즈, 11개 단계로 구성된 실제 운영 워크플로입니다:

```
Phase 0: Resolve     ─ resolve_cursor + resolve_last_arc (parallel, no LLM)
Phase 1: Specialists ─ 6 agents in parallel (world, science, character, structure, persona, hooks)
Phase 2: Synthesis   ─ arc_editor synthesizes all 6 memos
Phase 3: Queue       ─ episode_queue builds operational writing queue
Phase 4: Output      ─ arc_packet publishes qs-arc-plan entity → triggers episode room
```

### Phase 0: 이전 상태 resolve

```yaml
steps:
  - id: resolve_cursor
    type: resolve
    resolve:
      kind: "qs-episode-final"
      tag: "published"
      fields: [part, episode_number, episode_goal, arc_name]
      fail_if_missing: false

  - id: resolve_last_arc
    type: resolve
    resolve:
      kind: "qs-arc-plan"
      tag: "ready"
      fields: [part, arc_name, episode_range, arc_goal, continuity_context]
      fail_if_missing: false
```

병렬 resolve 두 단계. LLM 호출 없음. 각각 일치하는 최신 Kumiho 엔티티의 메타데이터를 반환하거나, 아무것도 없으면 `found: false`를 반환합니다.

### Phase 1: 병렬 전문가 에이전트

6개 에이전트 모두 두 resolve 단계에 depends_on을 두고 병렬 실행됩니다. 각 프롬프트는 듀얼 소스 패턴을 따릅니다:

```yaml
  - id: arc_world
    type: agent
    depends_on: [resolve_cursor, resolve_last_arc]
    agent:
      agent_type: claude
      role: world-builder
      template: quantum-soul-world-builder
      prompt: |
        ## Series cursor (auto-resolved — empty on first run)
        Last episode number: ${resolve_cursor.output_data.episode_number}
        Part: ${resolve_cursor.output_data.part}

        ## Previous arc plan (auto-resolved — empty on first run)
        Episode range: ${resolve_last_arc.output_data.episode_range}
        Arc goal: ${resolve_last_arc.output_data.arc_goal}

        ## Seed inputs (use when auto-resolved values above are empty)
        Part: ${inputs.part}
        Arc name: ${inputs.arc_name}
        Episode range: ${inputs.episode_range}

        Use the auto-resolved values when available; fall back to seed inputs on first run.

        Output in markdown with exactly these sections:
        1. Setting / Institutional Pressure Across The Arc
        ...
```

### Phase 2-3: 합성 체인

```yaml
  - id: arc_editor
    depends_on: [arc_world, arc_science, arc_character, arc_structure, arc_persona, arc_hooks]
    agent:
      prompt: |
        World memo:    ${arc_world.output}
        Science memo:  ${arc_science.output}
        ...
        Synthesize into one canonical arc mandate.

  - id: episode_queue
    depends_on: [arc_editor]
    agent:
      prompt: |
        ${arc_editor.output}
        Convert into an operational writing queue.
```

### Phase 4: 엔티티 출력

```yaml
  - id: arc_packet
    type: output
    depends_on: [arc_editor, episode_queue]
    output:
      format: markdown
      template: |
        # Quantum Soul Arc Plan
        ${arc_editor.output}
        ## Episode Queue
        ${episode_queue.output}
      entity_name: "qs-arc-${inputs.arc_name}"
      entity_kind: "qs-arc-plan"
      entity_tag: "ready"
      entity_space: "Construct/WorkflowOutputs"
      entity_metadata:
        part: "${resolve_cursor.output_data.part}"
        arc_name: "${resolve_cursor.output_data.arc_name}"
        episode_range: "${inputs.episode_range}"
        last_episode_number: "${resolve_cursor.output_data.episode_number}"
        last_episode_kref: "${resolve_cursor.output_data.revision_kref}"
        last_arc_kref: "${resolve_last_arc.output_data.revision_kref}"
        continuity_context: "${arc_editor.output}"
        episode_queue: "${episode_queue.output}"
```

**중요한 디테일:**
- `entity_kind: "qs-arc-plan"`은 `resolve_last_arc`의 `kind` 필드와 일치해야 함
- `entity_tag: "ready"`는 `resolve_last_arc`의 `tag` 필드와 일치해야 함
- `entity_metadata`에는 다음 실행이 연속성을 이어받는 데 필요한 모든 것을 저장
- entity_name은 항상 값이 있는 `${inputs.arc_name}`을 사용 — resolve된 값(비어 있을 수 있음)을 쓰지 않음

---

## 검증

검증기는 실행 전에 6개 패스를 실행합니다:

1. **중복 단계 ID** — 두 단계가 같은 ID를 공유할 수 없음
2. **의존성 참조** — 모든 `depends_on`이 존재하는 단계를 가리켜야 함
3. **사이클 감지** — 토폴로지 정렬에서 사이클이 있으면 실패
4. **단계 설정** — 타입별 검사 (예: agent에 config 존재, shell에 command 존재)
5. **변수 참조** — `${step_id.*}`가 알 수 없는 단계를 참조하면 경고
6. **트리거 검증** — 트리거 필드 확인, 매핑되지 않은 필수 입력에 경고

실행 없이 검증만 하려면 Operator에게 워크플로를 dry-run으로 실행해 달라고 하세요. Operator의 `dry_run_workflow` 도구는 YAML을 파싱하고 6개 패스를 모두 돌린 뒤, 실행을 시작하지 않은 채 에러/경고를 보고합니다.

---

## 재시도와 체크포인트

### 재시도

```yaml
- id: flaky_step
  type: agent
  retry: 2              # 첫 시도 후 최대 2회 재시도
  retry_delay: 10       # 재시도 간 10초 대기
```

단계 실패에서만 재시도합니다. 완료(completion) 및 검증 에러는 재시도하지 않습니다.

### 체크포인트

```yaml
checkpoint: true         # 기본값: true (워크플로 레벨에서 설정)
```

활성화되면 executor는 각 단계 완료 시점과 워크플로 일시 정지(human approval) 시점에 상태를 `~/.construct/workflow_checkpoints/{run_id}.json`에 저장합니다. 체크포인트는 승인 후 재개와 실패 실행 재시도 같은 명시적 사용자 동작을 지원합니다. Operator 시작 시 중단된 워크플로 실행을 자동으로 재개하지 않습니다. 진행 중으로 남은 오래된 실행은 실패로 표시되며, 사용자가 Retry를 눌러야 재시도 경로가 시작됩니다.

### 실행 중지와 삭제

실행 중지는 게이트웨이에 `POST /api/workflows/runs/{run_id}/cancel`을 보내고, 게이트웨이는 Operator MCP 서버의 `cancel_workflow`로 전달합니다. executor는 다음 경계에서 멈추며 가능한 경우 소유한 shell/python 서브프로세스를 종료합니다. 실행 삭제는 `DELETE /api/workflows/runs/{run_id}`를 호출해 WorkflowRuns Kumiho 아이템과 로컬 체크포인트/아티팩트 파일을 best-effort로 제거합니다. 워크플로 정의를 삭제하거나 비활성화하지 않습니다.

정의에는 별도 엔드포인트를 사용합니다:

- `DELETE /api/workflows/{kref}`는 워크플로 정의를 삭제합니다.
- `POST /api/workflows/deprecate`는 정의 사용 가능 상태를 토글합니다.
- 실행 중지, 재시도, 삭제 엔드포인트는 실행 인스턴스에만 적용됩니다.

---

## Action 단축형

`action` 필드는 에디터 친화적인 이름을 단계 타입과 에이전트 기본값으로 매핑합니다:

| Action | Type | Role | Agent |
|--------|------|------|-------|
| `research` | agent | researcher | claude |
| `code` | agent | coder | codex |
| `review` | agent | reviewer | claude |
| `test` | agent | tester | codex |
| `build` | agent | builder | codex |
| `deploy` | agent | deployer | codex |
| `notify` | agent | notifier | claude |
| `summarize` | agent | summarizer | claude |
| `task` | agent | coder | claude |
| `approve` | human_approval | — | — |
| `gate` | conditional | — | — |
| `human_input` | human_input | — | — |
| `resolve` | resolve | — | — |

`agent_hints`로 오버라이드:

```yaml
- id: my_step
  action: research
  agent_hints: [codex]    # 오버라이드: claude 대신 codex 사용
```

---

## 자주 쓰는 패턴

### 패턴 1: 선형 파이프라인

```yaml
steps:
  - id: gather
    type: agent
    agent: { agent_type: claude, role: researcher, prompt: "..." }

  - id: process
    type: agent
    depends_on: [gather]
    agent: { agent_type: codex, role: coder, prompt: "Using: ${gather.output}" }

  - id: report
    type: output
    depends_on: [process]
    output: { format: text, template: "${process.output}" }
```

### 패턴 2: 병렬 fan-out + 합성

```yaml
steps:
  - id: analyst_a
    type: agent
    agent: { prompt: "Analyze from angle A..." }

  - id: analyst_b
    type: agent
    agent: { prompt: "Analyze from angle B..." }

  - id: synthesize
    type: agent
    depends_on: [analyst_a, analyst_b]
    agent:
      prompt: |
        Angle A: ${analyst_a.output}
        Angle B: ${analyst_b.output}
        Synthesize into one recommendation.
```

### 패턴 3: 조건부 리뷰 루프

```yaml
steps:
  - id: implement
    type: agent
    agent: { agent_type: codex, role: coder, prompt: "Implement ${inputs.feature}" }

  - id: review
    type: agent
    depends_on: [implement]
    agent: { role: reviewer, prompt: "Review: ${implement.output}" }

  - id: check
    type: conditional
    depends_on: [review]
    conditional:
      branches:
        - condition: "${review.output} contains APPROVED"
          goto: done
        - condition: default
          goto: implement    # 되돌아가서 다시 작업

  - id: done
    type: output
    depends_on: [review]
    output: { template: "${implement.output}" }
```

### 패턴 4: 엔티티 체인 (워크플로 A → 워크플로 B)

**워크플로 A** (생산자):
```yaml
steps:
  - id: result
    type: output
    output:
      entity_name: "my-result"
      entity_kind: "analysis"
      entity_tag: "ready"
      entity_metadata:
        summary: "${analyze.output}"
```

**워크플로 B** (소비자):
```yaml
triggers:
  - on_kind: "analysis"
    on_tag: "ready"
    input_map:
      analysis_kref: "${trigger.entity_kref}"

steps:
  - id: use_result
    type: agent
    agent:
      prompt: "The analysis kref is: ${inputs.analysis_kref}"
```

### 패턴 5: 다중 실행을 위한 resolve + 폴백

```yaml
inputs:
  - name: seed
    default: "initial value"

steps:
  - id: prior
    type: resolve
    resolve:
      kind: "my-output"
      tag: "ready"
      fail_if_missing: false

  - id: work
    type: agent
    depends_on: [prior]
    agent:
      prompt: |
        ## Resolved (empty on first run)
        Previous: ${prior.output_data.value}

        ## Seed (use when resolved is empty)
        Default: ${inputs.seed}

  - id: publish
    type: output
    depends_on: [work]
    output:
      entity_name: "my-output-latest"
      entity_kind: "my-output"
      entity_tag: "ready"
      entity_metadata:
        value: "${work.output}"
```

---

## 트러블슈팅

### 워크플로를 찾을 수 없음

```
workflow_loader: 'my-workflow' not found in Kumiho
```

확인할 것: YAML이 `~/.construct/workflows/`에 있거나, 아티팩트와 함께 Kumiho에 등록되어 있나요? Operator는 디스크를 먼저 확인한 뒤 `kref://Construct/Workflows/my-workflow.workflow`로 resolve합니다.

### 로드 시 검증 에러

```
workflow_loader: skipping 'my-workflow.r3' (...): N validation errors
```

`*.r{N}.yaml` 패턴의 파일은 리비전 아티팩트이지 독립 워크플로가 아닙니다. 디렉토리 스캔이 아니라 kref resolve로 접근됩니다. 이 경고는 무해합니다 — 로더가 알아서 걸러냅니다.

### resolve 단계가 엔티티를 못 찾음

다음을 확인하세요:
1. resolve 설정의 `kind`가 생산하는 output 단계의 `entity_kind`와 일치하는가
2. `tag`가 `entity_tag`와 일치하는가
3. 엔티티가 기대한 스페이스(기본: `Construct/WorkflowOutputs`)에 발행되었는가
4. 생산하는 워크플로가 실제로 성공적으로 끝났는가

### 아티팩트가 생성되지 않음 (403 에러)

```
Failed to create artifact: Revision not found or is published.
```

이는 아티팩트가 첨부되기 전에 리비전이 `published`로 태깅되면 발생합니다. Construct v2026.4.21+에서는 아티팩트를 첨부한 뒤에 publish하도록 수정되어 있습니다.

### 보간 결과가 빈 문자열

해석되지 않은 `${step.output_data.key}`는 다음 경우에 `""`을 반환합니다:
- 단계가 아직 실행되지 않음 (`depends_on` 확인)
- 단계의 `output_data`에 해당 키가 없음
- resolve 단계가 `found: false`를 반환

이는 첫 실행 패턴에서 의도된 동작입니다 — 빈 값을 처리할 수 있도록 프롬프트를 설계하세요.
