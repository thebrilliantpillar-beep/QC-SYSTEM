# CLAUDE ↔ CHATGPT PROTOCOL v1.0.0

> 상태: 초안 (기존 합의사항 통합본)  
> 규격 버전: `1.0.0`  
> 범위: Claude와 ChatGPT가 동일 프로젝트에서 기록, 작업, 검토, 인계 및 실행을 안전하게 협업하기 위한 공통 프로토콜

## 1. 목적과 불변 원칙

이 프로토콜은 두 AI가 같은 프로젝트의 기록과 작업을 공유할 때, 사실·해석·사용자 결정·실행 권한을 혼동하지 않고 기록 손실이나 무단 변경 없이 협업하도록 한다.

다음 원칙은 불변이다.

- **Last Write Wins를 사용하지 않는다.** 시간상 마지막 변경이라는 이유만으로 다른 변경을 정답으로 취급하거나 덮어쓰지 않는다.
- **Silent Overwrite를 금지한다.** 충돌하거나 오래된 기준 버전에서 만들어진 변경은 비교·병합 또는 충돌 기록 없이 반영하지 않는다.
- **AI의 판단을 사용자 결정으로 승격하지 않는다.** `INFERENCE`나 `HYPOTHESIS`는 사용자의 명시적 결정 없이는 `CONFIRMED_DECISION`이 될 수 없다.
- **`USER_INTENT`는 실행 승인이나 확정 결정이 아니다.** 방향을 이해하는 데만 사용하며, 적용 범위가 불명확하면 `USER_DECISION_REQUIRED`로 둔다.
- **Evidence와 Interpretation을 섞지 않는다.** 원본 사실은 `EVIDENCE`에, AI의 해석·판단은 별도 AI view/interpretation으로 기록하고 관계로 연결한다.
- **미검증 결과를 `VERIFIED`로 표시하지 않는다.** 미완료 작업을 `COMPLETED`로 표시하지 않는다.
- **과거 기록을 삭제하거나 역사적 사실을 덮어쓰지 않는다.** 교체는 새 Record, `CHANGE`, `RELATION`, `status: SUPERSEDED`로 추적한다.
- **권한이 실행 허가를 자동으로 뜻하지 않는다.** Operation, Agent Permission Profile, Scope, Risk, Approval, Current State를 모두 확인한다.
- **압축본은 원본 진실이 아니다.** `STATE`, Handoff, 검색 요약은 출발점이며 중요한 판단 전에는 필요한 Evidence/Archive를 재확인한다.
- **미정 사항을 추측으로 채우지 않는다.** `TBD`, `UNKNOWN`, `USER_DECISION_REQUIRED`, 또는 `CONFLICT`로 명시한다.

## 2. 용어와 책임

| 용어 | 의미 |
|---|---|
| Project Owner | 프로젝트 전체 책임 AI. |
| Task Owner | 특정 TASK의 단일 책임 AI. |
| Contributor | Owner의 요청으로 작업에 기여하는 AI. 결과를 Owner에게 전달한다. |
| Reviewer | 결과와 근거를 검토하는 AI. `FAIL`만으로 자동 되돌림·수정을 하지 않는다. |
| Record | 프로젝트의 지속적 데이터 단위. 공통 Envelope와 Record Type을 가진다. |
| Operation | 프로토콜 Registry가 정의한 표준 행위. 일반 Record가 아니다. |
| Evidence | 실제로 존재하는 원본 사실·원본 참조·실행 결과. |
| Interpretation / AI View | Evidence 등에 기초한 AI의 해석 또는 판단. Evidence가 아니다. |
| Current State | 프로젝트의 현재 이해를 압축한 `STATE` Record. |
| Archive | 동일 Record 체계 안에서 보존되는 역사적 기록. 별도 진실 원천이나 삭제 영역이 아니다. |

동일 TASK에는 한 시점에 Task Owner가 한 명만 존재한다. 다른 AI는 조회·검토할 수 있고 요청에 따라 Contributor가 될 수 있으나, Owner 승인 없이 핵심 상태나 방향을 임의 변경하지 않는다. Owner가 장기간 이용 불가이거나 명시적으로 넘기면 Owner Transfer를 수행한다.

## 3. Protocol Envelope

모든 AI↔AI 통신과 저장 요청은 다음 바깥 Envelope를 사용한다.

```text
PROTOCOL_ENVELOPE
- protocol_name: CLAUDE_CHATGPT_PROTOCOL
- protocol_version: 1.0.0
- message_id
- sender
- receiver
- timestamp
- payload
```

`protocol_version`은 규격 자체의 버전이다. 개별 Record의 변경 버전(`record_version`)과 혼동하지 않는다. `message_id`의 형식, timestamp 표현 형식 및 payload의 직렬화 형식은 본 버전에서 **TBD**다.

## 4. Data Contract

### 4.1 공통 Record Envelope

모든 핵심 Record는 다음 공통 필드를 갖는다.

```text
record_id          immutable, globally unique
record_type
project_id
created_at
updated_at
created_by
knowledge_state
status
risk_level
record_version
protocol_version
```

- `record_id`는 생성 후 변경하지 않는다. Archive 여부와 무관하게 같은 ID를 유지한다.
- `record_version`은 개별 Record의 변경 및 동시성 추적용이다.
- `protocol_version`은 해당 Record가 해석되는 프로토콜 규격 버전이다.
- 위험도가 적용되지 않는 Record의 `risk_level`은 `NONE`을 사용한다.
- Record의 구체적 직렬화 형식, ID 생성 규칙, 낙관적 잠금 구현 방식은 **TBD**다.

### 4.1.1 QMS 적용: Timestamp·직렬화 형식 (2026-09-21, TBD-0003 — 정렬 방식 한정)

IQC QMS(`.collab`)는 이미 구체적 구현을 갖고 있다: 타임스탬프는 UTC ISO8601, 초 단위(마이크로초 절삭), `Z` 접미사(`qms_audit.py`의 `now()`). 직렬화는 키 정렬(`sort_keys=True`) + 공백 없는 압축 JSON(`separators=(",",":")`, `canonical()`)이며, 이 canonical 형태를 저장·미러링뿐 아니라 해시체인 계산(무결성)에도 그대로 쓴다.

**주의**: 초 단위 절삭 때문에 같은 초에 여러 이벤트가 생기면 타임스탬프만으로는 순서를 구분할 수 없다 — 그래서 QMS는 순서 보장을 타임스탬프에 맡기지 않는다(§15.1 참고). 이 형식과 canonical 직렬화 방식은 QMS의 구체적 구현 사례로 문서화하는 것이며 프로토콜 전역 표준으로 확정하는 것은 아니다. `record_id`/`message_id` 생성 규칙(TBD-0002)과 낙관적 잠금 구현(`record_version` 증가 방식)은 이 절과 별개로 여전히 미결이다.

### 4.2 개념의 엄격한 분리

| 개념 | 역할 |
|---|---|
| `knowledge_state` | 정보의 성격과 AI가 이를 어떻게 취급해야 하는지 |
| `status` | Record의 생명주기·현재 단계 |
| `risk_level` | 변경·행동이 잘못되었을 때의 위험도 |
| `permission_level` | AI가 할 수 있는 행위의 수준 |
| `approval` | 실행에 필요한 승인 여부·상태 |
| `record_version` | 개별 데이터의 변경 및 동시성 추적 |
| `protocol_version` | 프로토콜 규격의 버전 |

특히 `CONFIRMED_DECISION ≠ 승인`이며, 확정 설계 결정은 DB 변경·배포 등의 무제한 실행 승인이 아니다.

### 4.3 RELATION

Record 간 연결은 ID 목록에 매립하지 않고 독립 `RELATION` Record로 관리한다.

```text
RELATION
- from_record_id
- to_record_id
- relation_type
```

합의된 관계 유형은 다음과 같다.

```text
based_on, supports, contradicts, supersedes, superseded_by,
caused_by, result_of, verified_by, handoff_to, affects, related_to
```

시간·조건 변화에 따른 사실 변화는 가능한 경우 관계와 시점·범위를 통해 표현하며, 단지 기록이 다르다는 이유만으로 충돌로 취급하지 않는다. 추가 관계명(`changed_after`, `valid_during` 등)의 표준화는 **TBD**다.

## 5. Record Types

아래는 합의된 Record Type과 핵심 전용 필드다. 공통 Envelope는 생략했다.

| Type | 목적 및 전용 필드 |
|---|---|
| `PROJECT` | 전체 연결 중심. `name`, `description`, `purpose`, `current_owner`, `current_state_ref`, `active_task_refs`, `project_rules_ref` |
| `RULE` | 독립된 프로젝트 규칙. `name`, `content`, `scope`, `source`, `effective_from`, `supersedes_ref`; scope: `PROJECT`, `MODULE`, `TASK`, `AGENT` |
| `AGENT` | 협업 주체 이력. `agent_name`, `role`, `model`, `model_version`, `instructions_ref`, `skills`, `tools`, `permission_profile_ref` |
| `PERMISSION_PROFILE` | Agent별 권한 이력. `agent_id`, `operation`, `permission_level`, `approval_required`, `scope`, `conditions` |
| `TASK` | 지속적인 작업 상태·역사. `parent_task_id`, `title`, `owner`, `requester`, `priority`, `why`, `context`, `expected_result`, `constraints`, `scope`, `work`, `dependencies`, `risks`, `current_state_ref`, `handoff_ref` |
| `DECISION` | 사용자 또는 명시된 결정 주체의 결정. `task_id`, `decision_type`, `decision`, `decided_by`, `decision_basis`, `scope`; type: `POLICY`, `DESIGN`, `IMPLEMENTATION`, `PRIORITY`, `SCOPE`, `OPERATION`, `OTHER` |
| `EVIDENCE` | 원본 증거. `source_type`, `source_ref`, `original_ref`, `captured_at`, `captured_by`, `content`, `content_type`, `content_ref`, `metadata`, `scope`, `verification_status` |
| `STATE` | Current State 자체. `scope_type`, `scope_ref`, `summary`, `based_on`, `last_verified_at`; scope: `PROJECT`, `TASK`, `MODULE`, `AGENT` |
| `HANDOFF` | Owner/작업 인계 압축본. `task_id`, `previous_owner`, `new_owner`, `reason`, `current_state`, `completed_work`, `unresolved`, `risks`, `important_evidence`, `decisions`, `next_action`, `verification`, `context_hint` |
| `REQUEST` | AI 간 질문·요청·검토·인계 요청. `request_type`, `from_agent`, `to_agent`, `priority`, `why`, `context`, `expected_result`, `constraints`, `scope`, `work`, `dependencies`, `reporting`, `response_ref` |
| `REVIEW` | 검토 기록. `review_type`, `reviewer`, `review_target`, `review_scope`, `expected_standard`, `known_risks`, `verification`, `facts`, `findings`, `ai_view`, `conflict`, `user_decision_required`, `next_action` |
| `CHANGE` | 상태·규칙·소유자·데이터·병합의 변경 이력. `task_id`, `change_type`, `target_record_id`, `previous_value`, `new_value`, `changed_by`, `base_version`, `reason`, `basis`, `verification` |
| `ISSUE` | 문제 조사와 해결 이력. `task_id`, `title`, `description`, `priority`, `discovered_by`, `facts`, `attempts`, `failed_hypotheses`, `current_hypothesis`, `next_action`, `risks`, `resolution`, `verification` |
| `RELATION` | Record 간의 방향성 있는 관계. `from_record_id`, `to_record_id`, `relation_type` |

`AUDIT_EVENT`는 일반 Record Type이 아닌 별도 append-only 감사 로그다(§16).

### 5.1 상태값

- `TASK.status`: `PLANNED`, `READY`, `IN_PROGRESS`, `BLOCKED`, `WAITING_USER`, `WAITING_OTHER_AGENT`, `COMPLETED`, `CANCELLED`
- `REQUEST.status`: `ACKNOWLEDGED → IN_PROGRESS → RESULT → VERIFIED → COMPLETED`
- `ISSUE.status`: `OPEN`, `INVESTIGATING`, `BLOCKED`, `RESOLVED`, `VERIFIED`, `CLOSED`, `WONT_FIX`
- `REVIEW` 결과: `PASS`, `PASS_WITH_NOTES`, `FAIL`, `INCONCLUSIVE`, `CONFLICT`
- 적용 중인 Rule 등은 `ACTIVE`, 교체된 과거 Record는 `SUPERSEDED`로 둘 수 있다. `SUPERSEDED`는 `knowledge_state` 값이 아니라 status와 `supersedes`/`superseded_by` 관계로 표현한다.

다른 Record Type에 대한 완전한 status enum과 공통 status lifecycle은 **TBD**다.

### 5.1.1 QMS 적용: ISSUE는 append-only (2026-09-21 사용자 결정, TBD-0005)

위 `ISSUE.status`(OPEN→...→RESOLVED/CLOSED) enum은 프로토콜이 제시하는 일반 옵션으로 계속 남겨둔다 — 다른 구현체가 이 방식을 쓰는 것을 막지 않는다. 다만 **IQC QMS(`.collab`)는 이 옵션을 쓰지 않기로 확정했다**: `ISSUE.status`는 생성 후 절대 바꾸지 않고 항상 `ACTIVE`로 남으며, "해소됐다"는 사실은 status 전이가 아니라 별도 `STATE` Record(TASK 범위, `issue_record_id`+`issue_resolution` 필드로 연결)로 표현한다. 구현은 `.collab/qms_audit.py`의 `workflow_issue_is_active()`/`cmd_workflow_issue_resolution()`, 근거는 `.collab/README.md`의 "ISSUE는 append-only다" 절 참고. 이건 QMS의 로컬 선택이지 이 프로토콜 전체의 공통 규격 승격이 아니다.

## 6. Knowledge State, Confidence, Evidence와 Interpretation

모든 핵심 Record의 `knowledge_state`는 다음 중 하나다.

| 값 | 취급 규칙 |
|---|---|
| `FACT` | 사실로 사용할 수 있다. 원본·출처·검증 상태는 별도로 추적한다. |
| `CONFIRMED_DECISION` | 명시적으로 확인된 사용자 결정. 반드시 따라야 하나 실행 승인은 아니다. |
| `USER_INTENT` | 사용자가 원하는 방향. 자동 실행·확정 결정으로 취급하지 않는다. |
| `INFERENCE` | 근거 기반 추론. 필요 시 검증한다. |
| `HYPOTHESIS` | 가설. 사실로 사용하지 않는다. |
| `UNKNOWN` | 조사·확인이 필요하다. |
| `CONFLICT` | 상충 상태. 근거 없이 임의 결정하지 않는다. |

`CONFIDENCE`는 `knowledge_state`와 별도의 판단 보조 필드이며 `HIGH`, `MEDIUM`, `LOW`를 사용한다. 예를 들어 `INFERENCE + HIGH`는 근거가 충분한 추론일 뿐 FACT는 아니다.

`EVIDENCE.verification_status`는 `UNVERIFIED`, `VERIFIED`, `CONFLICTED`를 사용한다. Evidence 내용은 원본을 보존하며 AI의 요약·해석으로 덮어쓰지 않는다. AI View/Interpretation의 독립 Record Type 또는 저장 위치는 **TBD**이나, Evidence와 분리되고 `RELATION`으로 근거를 연결해야 한다.

### 6.1 QMS 적용: `AI_INTERPRETATION` Record Type (2026-09-21, TBD-0004)

위 TBD는 "AI View/Interpretation을 Evidence와 분리해서 저장해야 한다"는 원칙만 정하고 구체적 Record Type 이름·필드는 열어뒀다. IQC QMS(`.collab`)는 이미 구체적 구현을 갖고 있다: `AI_INTERPRETATION`이라는 독립 Record Type을 두고 필수 필드로 `interpretation`(해석 내용), `derived_from`(근거가 된 `EVIDENCE` Record ID), `scope`를 요구하며, 항상 `based_on` Relation으로 그 `EVIDENCE`와 연결한다. `.collab/qms_audit.py`의 `cmd_workflow_result()`가 서브에이전트(planner/developer/quality-watcher 등) 결과를 기록할 때마다 EVIDENCE와 짝을 지어 자동 생성한다 — 2026-09-20~21의 두 차례 Claude workflow smoke test(TASK 5, TASK 6)에서 end-to-end로 실제 동작이 검증됐다.

**이 QMS 구현을 프로토콜 전역 표준으로 승격하는 것은 아니다** — 다른 구현체(ChatGPT 등)는 같은 원칙(Evidence와 분리 + Relation 연결)만 지키면 다른 Record Type 이름이나 필드 구조를 써도 무방하다. QMS 내부에서 Codex가 별도로 interpretation류 Record를 만드는 경로가 생기면 이 `AI_INTERPRETATION` 타입명·필드를 그대로 재사용할 것을 권장한다(형식 드리프트 방지).

## 7. Risk, Permission, Approval

### 7.1 Risk Level

`risk_level`은 `NONE`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`이다.

### 7.2 Permission Level

권한 단계는 누적적이다.

```text
READ → ANALYZE → VERIFY → PROPOSE → EXECUTE
```

| 권한 | 가능한 행위 |
|---|---|
| `READ` | 파일·기록·Evidence 조회, Archive 검색 |
| `ANALYZE` | 분석, 비교, 원인 추정, 재현 |
| `VERIFY` | 테스트·검증·Review |
| `PROPOSE` | 변경안·설계안·계획 제안 |
| `EXECUTE` | 실제 변경·적용·실행 |

각 Agent는 `PERMISSION_PROFILE`로 Operation별 권한, Scope, 조건, 승인 요건을 가진다. Profile은 일반 설정이 아니라 이력을 보존하는 Record다.

### 7.3 Approval Boundary

다음 영역은 기본적으로 실제 `EXECUTE` 전 사용자 승인이 필요하다.

- 프로젝트 규칙/정책 변경
- DB 스키마 또는 중요 데이터 변경
- Git commit 및 중요한 Git 작업
- Production 배포
- 비용 발생 또는 외부 서비스 연결
- 사용자 의도·요구사항의 변경
- AI 간 해결되지 않은 설계·정책 충돌

`CRITICAL` 작업은 자동 실행하지 않으며 사용자 승인 또는 별도 명시적 정책을 요구한다. 승인 전에도 분석·재현·영향 분석·수정안 제안까지는 진행할 수 있다.

### 7.3.1 QMS 적용: GIT_COMMIT·DEPLOY의 구체적 결속 방식 (2026-09-21, TBD-0007 GIT_COMMIT/DEPLOY 부분)

§8 표의 `GIT_COMMIT`="정책에 따름", `DEPLOY`="사용자 승인"은 일반 원칙일 뿐 구체적 메커니즘을 정하지 않는다. IQC QMS(`.collab`)는 다음과 같은 구체적 참조 구현을 갖고 있다 — 이걸 프로토콜 전역 표준으로 확정하는 것은 아니며, 다른 구현체가 다른 방식을 쓸 수 있다.

- **GIT_COMMIT**: `git-commit-authorize`가 현재 staged diff(경로·모드·blob ID 전부)를 SHA-256으로 해시해 1회성 `DECISION`에 결속한다. `pre-commit` 훅이 그 manifest를 다시 계산해 대조하고, `post-commit`이 실제로 만들어진 commit과 매칭될 때만 승인을 소비한다 — commit 생성은 로컬에서 결정적이라 "성공 이후에만 소비"가 안전하게 보장된다.
- **DEPLOY**: `deploy-authorize`가 배포 대상 commit hash(HEAD)를 캡처해 1회성 `DECISION`에 결속한다. `pre-push` 훅(remote 이름이 `deploy`일 때만 작동)이 이를 대조한다. **GIT_COMMIT과 달리 push는 네트워크 작업이라 "성공 이후에만 소비"를 보장할 로컬 훅이 없다** — 그래서 승인은 `pre-push` 통과 시점에 바로 소비되고, 그 뒤 push 자체가 실패해도 재사용할 수 없다(재시도하려면 `deploy-authorize`를 다시 호출). 이 비대칭은 의도적으로 받아들인 한계로 문서화한다.
- **UPDATE_STATE**: "조건부"의 실제 의미는 QMS에서는 별도의 UPDATE_STATE 전용 승인 게이트가 없다는 것이다 — `STATE` Record는 이미 인가된 workflow run(§9의 workflow 레인, task/run/token 검증 완료) 안에서 자동 생성될 때만 조건 충족으로 본다. workflow 밖에서 임의로 STATE를 쓰는 경로는 없다.

근거·구현 상세: `.collab/README.md`의 "Git commit boundary"/"Deploy boundary" 절.

## 8. Operation Registry

Operation은 프로젝트 데이터가 아닌 Protocol Registry다. 각 Operation은 다음을 갖는다.

```text
operation_type
permission_level
default_risk_level
approval_policy
allowed_agents
conditions
description
```

합의된 기본 항목은 다음과 같다.

| Operation | Permission | 기본 Risk | Approval |
|---|---:|---:|---|
| `READ` | READ | NONE | 불필요 |
| `SEARCH_ARCHIVE` | READ | NONE | 불필요 |
| `ANALYZE_ISSUE` | ANALYZE | LOW | 불필요 |
| `RUN_TEST` | VERIFY | LOW | 불필요 |
| `CREATE_PROPOSAL` | PROPOSE | LOW | 불필요 |
| `UPDATE_STATE` | EXECUTE | MEDIUM | 조건부 |
| `CHANGE_RULE` | EXECUTE | HIGH | 사용자 승인 |
| `DB_DATA_CHANGE` | EXECUTE | HIGH | 사용자 승인 |
| `GIT_COMMIT` | EXECUTE | MEDIUM | 정책에 따름 |
| `DEPLOY` | EXECUTE | HIGH 또는 CRITICAL | 사용자 승인 |

실행 가능 여부는 Registry 기본값만으로 결정되지 않는다.

```text
Operation + Agent Permission Profile + Scope + Current Risk + Approval Status + Current State
```

모두를 검사하고, 성공·차단·실패를 포함한 결과를 `AUDIT_EVENT`에 기록한다.

### 8.1 QMS 적용: Operation Registry와 workflow 레인의 의도된 분리 (2026-09-21 사용자 결정, TBD-0006)

IQC QMS(`.collab`)의 실제 구현에는 위 Operation Registry 경로 외에 **별도의 두 번째 인가 경로("workflow 레인")**가 있다: `workflow-authorize`/`workflow-start`/`workflow-dispatch`/`workflow-result`/`workflow-finalize`와 `.collab` 검증용 `run` 명령이다. 이 레인은 task/run/token으로 스코프가 좁혀지는 다단계 서브에이전트 파이프라인 전용 의미론을 가지며, 위 표의 `OP_REGISTRY`를 거치지 않고 Agent Permission Profile을 직접 조회한다. `GIT_COMMIT`(과 이후 `DEPLOY`)은 두 레인이 만나는 지점으로, Registry에 정식 등록돼 있으면서 QMS가 추가 보증(§7.3.1)을 얹었다.

**사용자가 이 두 레인을 통합하지 않고 의도적으로 분리 유지하기로 결정했다** — 이미 검증된 코드·테스트를 건드리는 리팩터링 비용 대비 이득이 불분명했기 때문. 이건 QMS의 구현 선택이며, `allowed_agents`/`conditions`를 포함한 Operation Registry 전체 목록의 공통 규격화(TBD-0006 원안)를 완결한 것은 아니다 — 다른 구현체가 Registry 하나로 통합하는 방식을 택해도 무방하다. 근거·상세: `.collab/README.md`의 "인가 경로는 두 갈래다" 절.

## 9. AI-to-AI 통신

통신 타입은 `QUESTION`, `REQUEST`, `REVIEW_REQUEST`, `HANDOFF_REQUEST`다. 어느 AI도 구조적으로 우선권을 갖지 않으며 같은 포맷·검사 규칙을 적용한다.

- `QUESTION`: 정보 또는 판단을 요청한다. 응답은 Answer, `knowledge_state`, Basis, `confidence`, Unknown, `user_decision_required`, Next Action을 포함한다.
- `REQUEST`: 실제 작업 수행을 요청한다.
- `REVIEW_REQUEST`: 결과·기록의 검증을 요청한다.
- `HANDOFF_REQUEST`: Owner 책임 이전을 요청한다.

요청받은 AI는 범위 이탈, 기존 Rule 충돌, 위험한 실행 필요, 근거 부족을 발견하면 무조건 수행하지 않고 `CONFLICT`, `UNKNOWN`, `USER_DECISION_REQUIRED`를 보고할 수 있다.

### 9.1 TASK, REQUEST, REVIEW

Task Packet은 “무엇을 해야 하는가”를, Handoff Packet은 “어디까지 왔고 무엇을 이어야 하는가”를, Archive는 “실제로 무엇이 있었는가”를, Current State는 “현재 어떻게 이해하는가”를 표현한다.

REQUEST에는 최소한 WHY, CONTEXT, EXPECTED_RESULT, CONSTRAINTS, SCOPE (`IN_SCOPE`/`OUT_OF_SCOPE`), WORK, DEPENDENCIES, REPORTING을 담는다.

`RESULT`와 `VERIFIED`는 절대 합치지 않는다. 결과가 제출되어도 독립된 검증을 거치기 전에는 완료가 아니다.

Review는 `SELF_REVIEW`, `PEER_REVIEW`, `QUALITY_REVIEW`로 구분한다. `FAIL`은 자동 롤백·자동 수정의 근거가 아니다. Evidence, 양측 AI의 판단, 차이를 다시 확인하고 해결 불가 시 `CONFLICT` 및 필요에 따라 `USER_DECISION_REQUIRED`로 올린다.

## 10. Handoff와 Owner Transfer

정상 Owner Transfer는 다음 순서를 따른다.

```text
TRANSFER_REQUESTED → STATE_VERIFIED → HANDOFF_CREATED →
NEW_OWNER_ACCEPTED → OWNER_CHANGED → WORK_RESUMED
```

Handoff에는 Current State, Active Task, Completed Work, Unresolved, Risks, Important Evidence, Decisions, Next Action, Verification, Context Hint를 포함한다. Handoff는 충분한 시작점이지만 원본 진실이 아니므로, 새 Owner는 중요한 판단 전에 Evidence/Archive를 확인한다.

Owner 변경에는 `previous_owner`, `new_owner`, `reason`, `timestamp`, `task_id`, `handoff_reference`, `verification_status`를 기록한다. Owner 변경과 인계 자체는 `HANDOFF`, `CHANGE`, `AUDIT_EVENT`로 추적한다.

## 11. Emergency Handoff와 통신 장애

### 11.1 Emergency Handoff

토큰 소진, 세션 종료, 도구 장애 등으로 정상 인계가 어려우면 다음 최소 절차를 수행한다.

```text
EMERGENCY_DETECTED → SAVE_CURRENT_STATE → RECORD_LAST_VERIFICATION →
RECORD_UNFINISHED_WORK → RECORD_RISKS → CREATE_EMERGENCY_HANDOFF →
TRANSFER_OWNER (가능한 경우) → STOP
```

필수 내용은 `EMERGENCY_REASON`, `LAST_KNOWN_STATE`, `LAST_VERIFICATION`, `UNFINISHED_WORK`, `RISKS`, `NEXT_ACTION`이다. Owner 변경을 끝낼 수 없으면 Handoff만 남기고 종료할 수 있다. 새 AI는 이를 출발점으로 검증 후 작업을 재개한다.

### 11.2 Communication Failure

```text
COMMUNICATION_FAILURE → LOCAL_STATE_PRESERVATION → RECORD_UNSYNCED_CHANGES →
CONTINUE_SAFE_WORK → CONNECTION_RESTORED → SYNC → CONFLICT_CHECK → VERIFY
```

통신 장애 중에는 확보된 Evidence와 Context만으로 가능한 안전한 분석·문서화·테스트는 계속할 수 있다. 최신 상태가 필요한 변경, 타 AI의 최신 판단이 필수인 작업, Owner 변경, DB 변경, Rule 변경, Deploy, 사용자 승인 필요 작업은 보류한다. 복구 후 동기화·버전/충돌 검사·필요한 검증 전에는 최신 상태라고 가정하지 않는다.

## 12. DB ↔ File Sync, Version, Merge, Conflict

### 12.1 저장 역할

- DB는 Runtime Primary다: 빠른 검색, Relation 탐색, 동시 작업, Version 관리, 상태·Archive 검색.
- File은 Persistent Representation이다: Git, 사람 검토, 백업, 개발 환경 연동, 원본 Evidence 보존.
- 같은 `record_id`의 DB와 File은 서로 독립된 진실 원천이 아니라 동일 Record의 서로 다른 저장 표현이다.

### 12.1.1 QMS 적용 저장소 (2026-09-20 사용자 결정)

IQC QMS의 현재 협업 원장은 프로젝트 루트의 `.collab/`이다.

- Runtime DB: `.collab/runtime/audit.sqlite3`
- Git 추적 파일 미러: `.collab/events/YYYY-MM.jsonl`
- Emergency Handoff 본문: `.collab/handoffs/handoff-<HANDOFF_ID>.md`
- 현재 Handoff 신호: `docs/EMERGENCY_HANDOFF.md` (존재 여부만 신호로 사용)
- 기록 진입점: `.collab/qms-audit.ps1`

`docs/archive/`의 기존 bootstrap·handoff 자료는 레거시 역사 자료로 보존한다. 이관은 삭제·이동이 아니며, 새 기록과 새 Handoff는 `.collab/`에만 생성한다. 이관 근거와 이전 저장소의 역사적 사실은 `docs/archive/CHANGE-20260920-archive-backend-migration.md`에 보존한다.

구체적인 DB와 파일 미러의 현재 구현은 위 경로로 확정한다. 충돌 병합 알고리즘·추가 Relation 표준화·감사 보존 정책은 §19의 해당 TBD를 유지한다.

### 12.2 동기화와 병합

```text
Change detected → BASE_VERSION 확인 → CURRENT_VERSION 확인 → 변경 비교
  ├─ 호환 가능: MERGE → VERIFY → CHANGE 기록 → AUDIT_EVENT
  └─ 충돌: CONFLICT 기록 → 자동 덮어쓰기 금지 → USER_DECISION_REQUIRED (필요 시)
```

일반 데이터는 서로 다른 필드 변경 또는 동일 필드의 동일 값 변경일 때 자동 Merge할 수 있다. 같은 필드에 다른 값을 쓴 경우는 `CONFLICT`다.

다음 중요 데이터는 자동 Merge하지 않는다.

```text
RULE, DECISION, PERMISSION_PROFILE, DB_DATA
```

중요 데이터의 변경은 비교 후 필요한 경우 `USER_DECISION_REQUIRED`로 올린다. 모든 Merge는 검증 전 완료로 취급하지 않는다.

### 12.2.1 QMS 적용: 자동 Merge를 구현하지 않는다 (2026-09-21, TBD-0008)

위 "일반 데이터는 자동 Merge할 수 있다"는 프로토콜의 일반 옵션으로 남겨둔다. IQC QMS(`.collab`)는 **이 옵션을 아예 쓰지 않기로 확정했다** — RULE/DECISION/PERMISSION_PROFILE/DB_DATA뿐 아니라 **모든 Record Type**에 대해 자동 Merge 코드가 어디에도 없다. 버전 충돌이 감지되면 예외 없이 `CONFLICT` knowledge_state의 `ISSUE` Record(`next_action: USER_DECISION_REQUIRED`)로 귀결된다(`.collab/qms_audit.py`의 `relate`/`conflict` 명령).

이 선택이 안전한 이유는 QMS의 모든 쓰기가 `qms_audit.py`의 파일 잠금(`with lock():`)으로 이미 직렬화돼 있어, 프로토콜이 상정하는 "동시에 다른 필드를 고친 두 변경" 같은 상황 자체가 현재 구조상 거의 발생하지 않기 때문이다. 즉 지금 QMS에는 병합 로직을 만들 실질적 필요가 없다(YAGNI) — 다른 구현체가 실제 동시-편집 필요가 있어 필드 단위 자동 Merge를 구현하는 것을 막지 않는다.

### 12.3 Evidence 충돌과 시간 변화

동일 대상의 기록 차이는 먼저 대상·시점·조건/범위·출처·원본 Evidence·검증 상태를 비교한다.

- 시점 또는 조건 변화로 설명되면 상태 변화로 보존하고 필요 시 Current State를 갱신한다.
- 같은 시점·같은 대상·같은 조건에서 양립 불가능하면 `CONFLICT`다.
- 정보가 부족하면 `UNKNOWN` 또는 `NEEDS_VERIFICATION`이다.

어느 경우에도 Evidence를 삭제하거나, 시간순서만으로 정답을 결정하지 않는다.

## 13. Current State, Archive, Progressive Context Loading

`STATE` Record 자체가 Current State다. 별도의 Current State 복제본을 만들지 않으며 Index는 빠른 검색용일 수 있다. State 변경은 `STATE vN → CHANGE → STATE vN+1`로 추적하고, `WHAT_CHANGED`, `WHY`, `BASIS`, `VERIFICATION`, source records를 남긴다.

Current State는 원본 Evidence가 아닌 압축된 현재 이해다. 새 Evidence 또는 검증 결과가 들어오면 Evidence와 기존 State를 비교하고, 객관적으로 갱신 가능한 경우에만 State를 갱신한다. 판단이 필요하면 임의 변경 대신 `CONFLICT` 또는 `USER_DECISION_REQUIRED`로 둔다.

Archive는 동일 Record·Relation 체계 안의 역사 보존 영역이다. 보관은 삭제가 아니며 검색·관계 추적이 가능해야 한다.

Progressive Context Loading 원칙은 **많이 저장하고, 적게 읽는다**이다.

- Depth 1: 압축 검색 결과
- Depth 2: 상세 Record와 연결 정보
- Depth 3: 원본 Evidence

검색 결과는 `status` (`FOUND`, `PARTIAL`, `NOT_FOUND`, `CONFLICT`, `NEEDS_VERIFICATION`), relevance, summary, matched_records, why_relevant, next_depth를 포함한다.

## 14. User Decision과 Conflict Handling

사용자가 명시적으로 결정하면 다음을 수행한다.

1. 원문과 시점·관련 범위를 보존하는 `DECISION`을 생성한다.
2. `knowledge_state: CONFIRMED_DECISION`, `decided_by: USER`로 기록한다.
3. 영향받는 Task/State에만 반영하고 관계를 연결한다.
4. Archive에 영구 보존한다.
5. 기존 결정이 변경되면 기존 기록을 덮어쓰지 않고 `supersedes` 관계를 기록한다.

AI는 사용자가 A를 결정했다는 이유만으로 유사한 B까지 허용되었다고 일반화하지 않는다. 적용 범위가 불명확하면 `USER_DECISION_REQUIRED`다.

새 사용자 결정이 기존 사용자 결정과 충돌할 때도 조용히 덮어쓰지 않는다. 기존/신규 결정을 비교해 충돌을 표시하고 사용자 확인을 받는다.

AI 간 의견 차이는 오류가 아니라 기록할 정보다. 처리 순서는 다음과 같다.

```text
차이 발견 → Evidence 재확인 → 각 AI의 판단·근거 정리 → 해결 시도 →
해결 시 기록 / 미해결 시 CONFLICT → USER_DECISION_REQUIRED (필요 시)
```

사용자 보고는 공통적으로 다음 순서를 사용한다.

```text
FACTS → AI_VIEW → UNKNOWN/CONFLICT → OPTIONS → RISK →
USER_DECISION_REQUIRED → NEXT_ACTION
```

## 15. AUDIT_EVENT

`AUDIT_EVENT`는 일반 Record와 분리된 불변 append-only 로그다. 생성 후 수정·삭제하지 않는다. Record의 변경 버전과 실제 행위의 이력은 다르므로, 둘을 별도로 유지한다.

```text
AUDIT_EVENT
- event_id
- project_id
- timestamp
- actor_id
- actor_type
- operation
- target_record_id
- request_id
- task_id
- permission_level
- risk_level
- approval_status
- result
- metadata
```

성공뿐 아니라 권한·승인·위험 검사로 차단된 시도도 기록한다. 로그는 “누가, 언제, 무엇을, 어떤 권한·승인 상태로 시도했고 어떤 결과가 났는가”를 재구성할 수 있어야 한다. `event_id` 형식, 시간 정렬·보존 구현은 **TBD**다.

### 15.1 QMS 적용: 정렬은 해결됨, 보존은 여전히 미결 (2026-09-21, TBD-0010 — 정렬 부분 한정)

**정렬**: IQC QMS(`.collab`)는 타임스탬프가 아니라 SQLite의 단조증가 `seq` 컬럼 + SHA-256 해시체인(각 이벤트가 직전 이벤트의 해시를 포함)으로 순서를 보장한다. `verify` 명령이 이 체인을 매번 검증해 재정렬·변조를 잡아낸다. §4.1.1에서 언급한 타임스탬프 초단위 절삭 문제는 이 방식 덕분에 실제 순서 보장에 영향을 주지 않는다.

**보존(retention)**: 여전히 미결이다. QMS는 현재 어떤 보존기간·삭제 정책도 구현하지 않았다 — 모든 이벤트를 SQLite와 Git 추적 JSONL 미러 양쪽에 영구 보관한다. 이건 QMS가 다른 감사성 기록(iqc-app의 `activity_log` 등)에 이미 적용해온 "감사 기록은 삭제하지 않는다"는 원칙과 일치하는 잠정 입장이며, 구체적 보존기간·콜드 아카이브 방식을 정한 것은 아니다 — 저장소 크기가 실제 문제가 될 때 재검토한다.

## 16. Resource / Token Management

AI는 Token, Time, Compute, Cost, Tool Limit을 지속적으로 고려한다. 자원 부족은 실패가 아니라 정상적인 Handoff 사유다.

```text
RESOURCE_MONITORING → LOW_RESOURCE_DETECTED → CURRENT_STATE_CHECK →
SAFE_STOP_POINT → VERIFY → RECORD → HANDOFF
```

고정된 비율 대신, AI는 현재 작업을 안전하게 검증하고 Handoff를 완료할 최소 자원을 확보해야 한다. 자원 부족으로 검증을 마치지 못했다면 `VERIFIED`나 `COMPLETED`로 표시하지 않는다.

## 17. Protocol Versioning

규격 버전은 Semantic Versioning을 사용한다.

```text
MAJOR.MINOR.PATCH
```

- MAJOR: 기존 해석 호환성을 깨는 변경
- MINOR: 호환성을 유지하는 기능·필드 추가
- PATCH: 오류 수정·표현 명확화·문서 수정

과거 Record는 새 프로토콜 버전이 생겨도 수정하지 않는다. 필요하면 호환 변환 규칙(adapter/migration)을 사용한다.

### 17.1 QMS Archive Backend Migration (2026-09-20)

사용자는 QMS 협업 기록과 Emergency Handoff의 현재 저장소를 `.collab/`으로 통합하도록 승인했다. 이는 기존 `docs/archive/` 기록을 삭제하거나 수정하지 않는 저장소 이관이며, 이전 결정·경로는 `CHANGE-20260920-archive-backend-migration`이 `supersedes` 관계로 보존한다. 적용 범위는 §12.1.1과 AGENTS.md P-4·P-5이며, 프로토콜의 미결 항목을 일괄 해결하는 변경은 아니다.

프로토콜 변경은 다음을 거친다.

```text
제안 → 영향 분석 → Claude/ChatGPT 검토 → 사용자 승인 → 새 Protocol Version → 적용 → 검증
```

MAJOR 변경은 반드시 사용자 결정이 필요하다. 구체적인 compatibility adapter 형식은 **TBD**다.

### 17.2 QMS 적용: `register-adapter`는 게이트이지 변환 엔진이 아니다 (2026-09-21, TBD-0011)

IQC QMS(`.collab`)에는 이미 `register-adapter` 명령과 append-only `protocol_adapters` 테이블이 있다. 다만 이건 **"버전이 다른 Envelope를 거부하지 않고 허용목록에 등록"하는 게이트일 뿐**이다 — `validate_envelope()`가 `protocol_version`이 현재 버전과 다르면 등록된 adapter가 없는 한 무조건 거부한다. **실제로 옛 구조의 payload를 새 구조로 변환하는 코드는 어디에도 없다.** 프로토콜이 아직 한 번도 1.0.0을 벗어난 적이 없어 이 메커니즘이 진짜 버전 전환에 쓰인 적도 없다.

TBD-0011이 묻는 "compatibility adapter 형식"이 이 게이트 자체를 뜻하는 거라면 그 부분은 QMS 구현으로 확인됐지만, "옛 Record를 새 구조로 실제 변환하는 형식"은 여전히 미결로 남겨둔다 — 실제 MAJOR 버전 전환 사례가 하나도 없는 상태에서 추상적으로 변환 형식을 설계하면 틀릴 위험이 크다고 판단했기 때문이다. 첫 MAJOR 버전이 실제로 제안될 때 이 부분을 다시 열어 그 구체적 전환 필요에 맞춰 결정한다.

## 18. Common Reporting Standard

작업 결과와 판단 보고는 사실과 해석을 분리해 최소한 다음을 포함한다.

```text
FACTS
AI_VIEW
UNKNOWN / CONFLICT
OPTIONS
RISK
USER_DECISION_REQUIRED
NEXT_ACTION
```

작업·검토·인계 보고에는 적용 범위, 근거 Record/Evidence, 실제 수행한 검증, 미완료 사항을 함께 명시한다. 결과 제출은 검증 완료를 뜻하지 않는다.

## 19. TBD / UNKNOWN Registry

기존 합의에서 의도적으로 확정하지 않은 항목과, 이후 확정된 항목의 이력을
함께 관리한다. Status가 `TBD`인 항목은 현재 미결이며, `RESOLVED`인 항목은 해결됐음을 나타낸다.
이 Registry의 항목은 구현자가 임의로 채울 수 있는 빈칸이 아니다.

| ID | Topic | Status | User decision required | 비고 |
|---|---|---|---|---|
| TBD-0001 | Archive Storage Backend | RESOLVED | YES — 확정일 2026-09-20 | → Resolved entries 참조 |
| TBD-0002 | Record와 Message ID 형식 | TBD | NO | 불변·전역 고유성 원칙만 확정 |
| TBD-0003 | Timestamp·직렬화 형식 | TBD | NO | 정렬 방식은 §4.1.1에서 QMS 적용 사례로 문서화(2026-09-21). 전역 표준 승격은 여전히 미결 |
| TBD-0004 | AI Interpretation의 독립 Record Type/저장 위치 | RESOLVED (QMS 적용 범위) | YES — 확정일 2026-09-21 | → Resolved entries 참조. 다른 구현체의 Record Type 이름·구조 자유는 유지 |
| TBD-0005 | 전체 Record별 status enum 및 공통 lifecycle | RESOLVED (QMS 적용 범위) | YES — 확정일 2026-09-21 | → Resolved entries 참조. 다른 Record Type의 일반 enum은 여전히 TBD |
| TBD-0006 | Operation Registry의 전체 목록·Agent별 allowed_agents/conditions | RESOLVED (QMS 적용 범위) | YES — 확정일 2026-09-21 | → Resolved entries 참조. Registry 자체의 전역 표준화는 여전히 TBD |
| TBD-0007 | `UPDATE_STATE`의 조건부 승인 및 `GIT_COMMIT` 정책 세부 | RESOLVED (QMS 적용 범위) | YES — 확정일 2026-09-21 | → Resolved entries 참조. DEPLOY도 같이 다뤘다(원 Topic 범위 확장) |
| TBD-0008 | Merge 구현 알고리즘·필드 비교 정밀도 | RESOLVED (QMS 적용 범위) | YES — 확정일 2026-09-21 | → Resolved entries 참조. 필드 단위 자동 Merge를 실제로 구현하는 것 자체는 다른 구현체에 여전히 열려 있음 |
| TBD-0009 | 추가 Relation Type 표준화 | TBD | NO | 현재 합의 목록 외 관계명 미정 |
| TBD-0010 | Audit event ordering·retention 구현 | TBD | NO | 정렬은 §15.1에서 QMS 적용 사례로 RESOLVED 문서화(2026-09-21, seq+해시체인). 보존기간·콜드 아카이브 방식은 여전히 미결 |
| TBD-0011 | Protocol compatibility adapter/migration 형식 | RESOLVED (QMS 적용 범위, 게이트 부분만) | YES — 확정일 2026-09-21 | → Resolved entries 참조. 실제 payload 변환 형식은 첫 MAJOR 버전 제안 시까지 여전히 미결 |

`Status`는 이 §19 Registry 내부에서만 사용하는 항목 상태값이다: `TBD`(미결) / `RESOLVED`(해결됨).
프로토콜 공통 Record status를 새로 정의하지 않는다.

### Resolved entries

TBD Registry에 등록됐다가 이후 확정된 항목이다.
원래 등록 행은 RESOLVED 상태로 이 Registry에 유지하고, 상세 이력은 이 표에 보존한다.

| ID | Topic | 사용자 결정 필요 | 확정일 | 결정 내용 | 근거 | 반영 위치 | Bootstrap |
|---|---|---|---|---|---|---|---|
| TBD-0001 | Archive Storage Backend | YES | 2026-09-20 | 현재 저장소: `.collab/` (Runtime DB·JSONL 미러·`handoffs/`); 초기 `docs/archive/` 결정은 이관으로 대체 | 사용자 2026-09-20 이관 결정. 초기 근거와 기록은 보존하며, 현재 근거·이관 범위는 `docs/archive/CHANGE-20260920-archive-backend-migration.md` | AGENTS.md P-4·P-5, 프로토콜 §12.1.1 | `docs/archive/bootstrap-20260920.md` (역사 자료) |
| TBD-0005 | 전체 Record별 status enum 및 공통 lifecycle (QMS 적용 범위) | YES | 2026-09-21 | QMS의 `ISSUE`는 append-only로 확정: status는 항상 `ACTIVE`, 해소는 별도 `STATE` Record로 표현(status enum으로 직접 전이하지 않음). `TASK`(IN_PROGRESS/PARTIAL/COMPLETED)·`REQUEST`(기존 제안과 일치 확인)는 이미 쓰던 대로 유지, 그 외 Record Type은 `ACTIVE`/`SUPERSEDED` 외 추가 lifecycle 없이 유지. 일반 프로토콜의 전체 status enum(§5.1)은 여전히 미결 | `docs/protocol-tbd-roadmap.md`의 TBD-0005 분석에서 발견한 코드 내 불일치(OPEN vs ACTIVE 두 관행 혼재)를 근거로 사용자가 직접 결정 | 프로토콜 §5.1.1, `.collab/README.md`("ISSUE는 append-only다"), `.collab/qms_audit.py`(merge-conflict ISSUE 생성부 `status="OPEN"`→`"ACTIVE"` 수정) | — |
| TBD-0006 | Operation Registry의 전체 목록·Agent별 allowed_agents/conditions (QMS 적용 범위) | YES | 2026-09-21 | QMS는 Operation Registry와 workflow 전용 인가 레인을 통합하지 않고 의도적으로 분리 유지하기로 확정 — 문서화만 하고 리팩터링은 하지 않음. Registry의 `allowed_agents`/`conditions` 전체 목록 자체는 여전히 미완성(TBD) | `docs/protocol-tbd-roadmap.md`의 TBD-0006 분석(두 레인이 문서화 없이 갈라져 있던 걸 발견 — CLAUDE.md 20절의 `_can_make_final_decision()` 사고와 같은 유형의 리스크로 판단)을 근거로 사용자가 직접 결정 | 프로토콜 §8.1, `.collab/README.md`("인가 경로는 두 갈래다") | — |
| TBD-0007 | `UPDATE_STATE`의 조건부 승인 및 `GIT_COMMIT` 정책 세부 (QMS 적용 범위, DEPLOY로 확장) | YES | 2026-09-21 | GIT_COMMIT의 staged-diff manifest 결속 방식을 문서화하고, 같은 조사에서 발견한 "DEPLOY 승인이 GIT_COMMIT보다 약함" 격차를 사용자가 즉시 강화하기로 결정 — `deploy-authorize`+`pre-push` 훅으로 배포 대상 commit hash에 1회성 승인을 결속(단, push는 네트워크 작업이라 GIT_COMMIT과 달리 승인을 사후-성공 확인 없이 pre-push 시점에 소비하는 한계가 있음, 문서에 명시). UPDATE_STATE는 "인가된 workflow run 안에서만 자동 허용"으로 현재 동작을 문서화만 함(추가 게이트 신설 안 함) | 사용자가 `docs/protocol-tbd-roadmap.md` 제시 후 "지금 바로 강화" 선택 | 프로토콜 §7.3.1, `.collab/README.md`("Deploy boundary"), `.collab/qms_audit.py`(`deploy-authorize`/`deploy_authorization()`/`preflight()` DEPLOY 분기), `.collab/hooks/pre-push`(신규), `.collab/hooks/install-post-commit-hook.ps1`(pre-push 설치 추가), `.collab/tests/test_qms_audit.py`(신규 테스트 2건) | — |
| TBD-0004 | AI Interpretation의 독립 Record Type/저장 위치 (QMS 적용 범위) | YES | 2026-09-21 | QMS는 `AI_INTERPRETATION` Record Type(필드: `interpretation`/`derived_from`/`scope`, `based_on` Relation으로 EVIDENCE와 연결)을 이미 구현·검증(TASK 5·6 smoke test)됐던 것으로 확정 — 다른 구현체는 같은 원칙만 지키면 다른 이름·구조를 써도 무방 | 사용자가 `docs/protocol-tbd-roadmap.md`의 "기술안 먼저 작성 가능" 항목 승인("응") | 프로토콜 §6.1, `.collab/qms_audit.py`(`cmd_workflow_result()`) | — |
| TBD-0008 | Merge 구현 알고리즘·필드 비교 정밀도 (QMS 적용 범위) | YES | 2026-09-21 | QMS는 자동 Merge를 어떤 Record Type에도 구현하지 않고, 모든 버전 충돌을 `CONFLICT`+`USER_DECISION_REQUIRED`로 처리하기로 확정 — file lock이 동시쓰기 자체를 직렬화하므로 안전하다고 판단 | 사용자가 `docs/protocol-tbd-roadmap.md`의 "기술안 먼저 작성 가능" 항목 승인("응") | 프로토콜 §12.2.1 | — |
| TBD-0011 | Protocol compatibility adapter/migration 형식 (QMS 적용 범위, 게이트 부분만) | YES | 2026-09-21 | QMS의 `register-adapter`는 버전 불일치 Envelope를 거부하지 않게 허용목록에 등록하는 "게이트"일 뿐, 실제 payload 변환 로직은 없다는 것을 확정 — 실제 변환 형식 설계는 첫 MAJOR 버전 제안이 나올 때까지 의도적으로 미룸(추상 설계는 틀릴 위험이 크다고 판단) | 사용자가 `docs/protocol-tbd-roadmap.md`의 "기술안 먼저 작성 가능" 항목 승인("응") | 프로토콜 §17.2 | — |

## 20. Agent Capability & Model Fit

### 20.1 목적

Agent를 TASK에 배정하거나 다른 Agent에게 작업을 인계할 때, 해당 Agent와 Model이 TASK에 필요한 능력 및 실행 조건을 충족하는지 확인하고 기록한다. 이 규칙은 기존 Agent의 역할 또는 Model 배정을 재설계하기 위한 것이 아니다.

### 20.2 Existing Agent Configuration Preservation

기존 Claude 또는 ChatGPT 환경에서 정의·배정된 다음 설정은 기본적으로 존중하고 유지한다.

```text
agent_name
role
model
model_version
instructions_ref
skills
tools
permission_profile_ref
```

프로토콜은 이 설정을 표준 `AGENT` Record로 표현하고 상호운용하기 위한 것이다. AI는 기존 Agent의 Model, Role, Instructions 또는 Permission을 임의로 변경하지 않는다. 변경이 필요하면 기존 변경 절차와 사용자 승인 규칙을 따른다.

### 20.3 Capability Check

기존 Agent를 그대로 사용하더라도 TASK 단위의 적합성 검증은 별도로 수행한다.

```text
TASK → Required Capability → Existing Agent Configuration → Model / Version →
Tools / Skills → Permission → Resource → MODEL FIT CHECK
```

`MODEL FIT CHECK` 결과는 다음 중 하나다.

| 결과 | 의미 |
|---|---|
| `PASS` | 현재 Agent/Model 조합이 TASK 요구사항을 충족한다고 확인됨 |
| `INSUFFICIENT` | 요구 능력 또는 실행 조건을 충족하지 못함 |
| `UNKNOWN` | 충분성을 판단할 근거가 부족함 |

Model 이름만으로 적합성을 판단하지 않으며, 판단 근거(`capability_basis`)를 남긴다.

### 20.4 Model Fit은 Model Replacement가 아니다

Model Fit Check는 현재 배정된 Model을 자동으로 교체하는 기능이 아니다.

```text
PASS
  → 기존 Agent로 작업
INSUFFICIENT
  → 적합한 기존 Agent 탐색
  → 필요 시 Handoff / Owner Transfer
UNKNOWN
  → 중요한 작업은 추가 검증
```

AI는 단지 더 강력하다고 판단했다는 이유만으로 기존 Agent의 Model을 임의 교체하지 않는다. `INSUFFICIENT` 상태에서 무단 실행하거나 `UNKNOWN` 상태에서 고위험 작업을 실행하지 않는다.

### 20.5 Claude ↔ ChatGPT 대칭성

동일한 보존·검증 원칙을 Claude와 ChatGPT 양쪽에 적용한다. 기존 Claude Agent 배정 정보를 변경하지 않으면서 ChatGPT Agent도 동일한 `AGENT` Record 구조로 표현할 수 있어야 한다.

### 20.6 Agent 변경

다음은 Capability Check와 별개의 변경 행위다.

```text
Model 변경
Role 변경
Instructions 변경
Skills 변경
Tools 변경
Permission 변경
Agent 배정 변경
```

이러한 변경은 기존 설정을 덮어쓰지 않고 변경 이력을 남긴다. 필요한 경우 `CHANGE`와 `AUDIT_EVENT`를 생성하며, 고위험 변경 또는 프로젝트 Rule/권한에 영향을 주는 변경은 기존 사용자 승인 규칙을 따른다.

### 20.7 Resource와 Fallback

Capability가 충분하더라도 다음 자원이 부족하면 TASK를 계속 수행할 수 있는 것으로 간주하지 않는다.

```text
TOKEN, TIME, COMPUTE, COST, TOOL_LIMIT
```

Token 부족이 예상되면 안전한 중단 지점을 확보하고 Handoff를 준비한다. `fallback_agent`는 기존 Agent를 대체할 후보이며, 후보 역시 동일한 Capability Check를 통과해야 한다.

```text
Current Agent → Capability / Resource 문제 → Fallback Agent 탐색 →
Capability Check → PASS → Handoff / Owner Transfer
```

### 20.8 Audit

Agent/Model 적합성 판단이 중요한 작업에 영향을 준 경우 `AUDIT_EVENT`는 다음 정보를 기록할 수 있어야 한다.

```text
assigned_agent
assigned_model
model_version
capability_check
capability_basis
resource_condition
result
```

### 20.9 핵심 원칙

```text
기존 Agent 배정 존중
        +
Task별 Capability 검증
        +
Model Fit 검증
        +
Permission / Risk / Approval 검증
        +
Resource 검증
        =
안전한 Agent 사용
```

## 21. v1.0.0 충돌 점검

현재 합의된 구조에서 즉시 해결해야 할 논리 충돌은 확인되지 않았다. 단, 다음 구분을 유지해야 한다.

- `CONFIRMED_DECISION`과 실행 승인
- `USER_INTENT`와 확정 결정·실행 권한
- `knowledge_state`와 `status`
- `risk_level`과 `permission_level`
- `record_version`과 `protocol_version`
- `record_version`과 append-only `AUDIT_EVENT`
- Current State/Handoff의 압축 정보와 원본 Evidence

새 정책을 추가하거나 이 구분을 흐리는 변경은 본 규격의 호환 변경으로 간주하지 않으며, 영향 분석과 필요한 사용자 결정을 거쳐야 한다.
