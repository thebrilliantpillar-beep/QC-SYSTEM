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

구체적인 DB, 파일 형식, 동기화 주기 및 adapter 구현은 **TBD**다.

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

과거 Record는 새 프로토콜 버전이 생겨도 수정하지 않는다. 필요하면 호환 변환 규칙(adapter/migration)을 사용한다. 프로토콜 변경은 다음을 거친다.

```text
제안 → 영향 분석 → Claude/ChatGPT 검토 → 사용자 승인 → 새 Protocol Version → 적용 → 검증
```

MAJOR 변경은 반드시 사용자 결정이 필요하다. 구체적인 compatibility adapter 형식은 **TBD**다.

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
| TBD-0003 | Timestamp·직렬화 형식 | TBD | NO | Envelope/Record의 표준 표현 미정 |
| TBD-0004 | AI Interpretation의 독립 Record Type/저장 위치 | TBD | YES | Evidence와 분리·Relation 연결 원칙만 확정 |
| TBD-0005 | 전체 Record별 status enum 및 공통 lifecycle | TBD | YES | TASK/REQUEST/ISSUE/REVIEW 및 ACTIVE/SUPERSEDED만 확정 |
| TBD-0006 | Operation Registry의 전체 목록·Agent별 allowed_agents/conditions | TBD | YES | 표의 기본 Operation만 확정 |
| TBD-0007 | `UPDATE_STATE`의 조건부 승인 및 `GIT_COMMIT` 정책 세부 | TBD | YES | 조건부/정책에 따름으로만 합의 |
| TBD-0008 | Merge 구현 알고리즘·필드 비교 정밀도 | TBD | YES | 허용/금지 원칙은 확정 |
| TBD-0009 | 추가 Relation Type 표준화 | TBD | NO | 현재 합의 목록 외 관계명 미정 |
| TBD-0010 | Audit event ordering·retention 구현 | TBD | NO | append-only·필수 의미만 확정 |
| TBD-0011 | Protocol compatibility adapter/migration 형식 | TBD | YES | 과거 기록 불변 원칙만 확정 |

`Status`는 이 §19 Registry 내부에서만 사용하는 항목 상태값이다: `TBD`(미결) / `RESOLVED`(해결됨).
프로토콜 공통 Record status를 새로 정의하지 않는다.

### Resolved entries

TBD Registry에 등록됐다가 이후 확정된 항목이다.
원래 등록 행은 RESOLVED 상태로 이 Registry에 유지하고, 상세 이력은 이 표에 보존한다.

| ID | Topic | 사용자 결정 필요 | 확정일 | 결정 내용 | 근거 | 반영 위치 | Bootstrap |
|---|---|---|---|---|---|---|---|
| TBD-0001 | Archive Storage Backend | YES | 2026-09-20 | 저장소: `docs/archive/` / 파일명: `handoff-YYYYMMDD-HHMMSS.md` | `docs/archive/bootstrap-20260920.md` CONFIRMED DECISIONS — git 이력 보존, Claude·Codex 양측 접근 가능, 외부 서비스 불필요 | AGENTS.md P-4 | `docs/archive/bootstrap-20260920.md` |

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
