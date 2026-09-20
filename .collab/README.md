# QMS 협업 기록

`.collab`은 IQC QMS 프로젝트 안에서 수행된 협업·터미널 작업의 기록 원장과 인계 Archive다. Emergency Handoff도 `.collab/handoffs/`에 같은 Record 체계로 보존한다.

## 저장 구조

- `runtime/audit.sqlite3`: 실행용 SQLite 원장. Git에 포함하지 않는다.
- `events/YYYY-MM.jsonl`: 검토 가능한 append-only 미러. 각 행은 이전 행 해시를 포함한다.
- `records/YYYY-MM.jsonl`: Protocol Record의 Git 검토용 영구 표현. SQLite와 같은 Record ID를 사용한다.
- `handoffs/`: 정상·긴급 인계의 사람이 읽을 수 있는 JSON 표현. 원본은 `HANDOFF` Record다.
- `qms_audit.py`: 표준 라이브러리만 쓰는 기록 CLI.
- `qms-audit.ps1`: PowerShell 진입점.

기록은 작업 시작·터미널 실행·판단·완료 및 Git commit에 사용한다. 토큰·비밀번호·키·쿠키처럼 보이는 값은 저장 전에 `[REDACTED]`로 바뀐다. 명령 결과는 종료 코드와 짧은 요약만 기록한다. 명령 전문과 출력 전문은 기본적으로 기록하지 않는다.

## 빠른 사용

```powershell
# 1) 작업 시작
.\.collab\qms-audit.ps1 start --title "CSRF 점검" --actor user --scope "app.py" --summary "로그인 흐름 확인"

# 2) 명령 실행과 결과 자동 기록 (-- 뒤는 실제 명령)
.\.collab\qms-audit.ps1 run --task 1 --actor user --summary "단위 테스트" -- python -m unittest discover .collab/tests

# 3) 판단·종료
.\.collab\qms-audit.ps1 decision --task 1 --actor user --summary "배포 승인" --state CONFIRMED
.\.collab\qms-audit.ps1 end --task 1 --actor user --summary "완료" --status COMPLETED

# 4) 무결성 검사 / DB에서 JSONL 재생성
.\.collab\qms-audit.ps1 verify
.\.collab\qms-audit.ps1 repair-mirror
```

`start`, `note`, `decision`, `end`의 내용은 사실·근거·미확인을 분리해 작성한다. 사실을 모르면 `PARTIAL` 또는 `NEEDS_VERIFICATION`을 명시한다. `status`는 완료 여부를 추정하지 않고 마지막 `TASK_ENDED` 이벤트의 상태(`PARTIAL`, `NEEDS_VERIFICATION`, `COMPLETED` 등)를 표시한다.

## Protocol v1 기반 명령

```powershell
# 최초 1회: 기존 감사 원장은 보존한 채 Protocol Record로 표현
.\.collab\qms-audit.ps1 bootstrap-protocol
.\.collab\qms-audit.ps1 migrate-legacy

# 실행 전 권한·위험·승인 경계 확인 (차단도 AUDIT_EVENT에 기록)
.\.collab\qms-audit.ps1 preflight --actor user --operation DEPLOY --approval APPROVED

# 표준 Record 작성. STATE/RULE/DECISION/PERMISSION_PROFILE은 승인 상태를 명시한다.
.\.collab\qms-audit.ps1 record --record-type EVIDENCE --actor user --payload '{"source_type":"terminal","content":"테스트 통과","verification_status":"VERIFIED"}'

# 정상 인계: payload에는 reason/current_state/completed_work/unresolved/risks/next_action/verification/context_hint가 필요하다.
.\.collab\qms-audit.ps1 handoff --actor claude --new-owner codex --payload '{"reason":"정상 인계","current_state":"...","completed_work":"...","unresolved":"...","risks":"...","next_action":"...","verification":"...","context_hint":"..."}'

# 긴급 인계에는 위 필드 외 emergency_reason/last_known_state/last_verification/unfinished_work가 필요하다.
.\.collab\qms-audit.ps1 handoff --emergency --actor claude --new-owner codex --payload '{...}'
```

TBD-0002~0011은 이 CLI가 임의로 확정하지 않는다. 정의되지 않은 Relation 또는 Operation은 차단한다. 병합은 구현하지 않으며 충돌이 의심되면 새 `CONFLICT` Record와 사용자 결정을 요구해야 한다.

## Git hook

`.collab/hooks/install-post-commit-hook.ps1`는 검토 가능한 hook 원본을 `.git/hooks/post-commit`으로 설치한다. 기존 hook이 있으면 `post-commit.qms-original`으로 보존하고 먼저 실행한 뒤 감사 기록을 추가한다. 설치는 사용자 승인 후 수행한다.

## 제한

이 도구는 전용 wrapper를 통과한 터미널 명령과 hook이 기록한 커밋만 자동 기록한다. 기존 명령 기록을 추측해서 만들지 않는다. SQLite가 없어져도 JSONL은 Git 기록으로 남는다. 모든 CLI 호출은 시작 시 JSONL이 SQLite 원장의 정확한 앞부분인 경우에만 누락된 뒷부분을 자동 append 재시도한다. 내용이 다르면 자동으로 덮어쓰지 않으며 `verify`로 확인한 뒤 `repair-mirror`를 수동 실행한다. `repair-mirror`는 SQLite 원장으로 JSONL 미러를 다시 만들며, 원본 DB가 없을 때의 복원은 별도 절차가 필요하다.

## 전체 Protocol 적용 범위와 안전 경계

`.collab`의 정본은 SQLite 원장이고 JSONL은 canonical(정렬된 키·공백 없음) 미러다. `verify`는 이벤트·Record·Relation·Conflict 미러와 해시를 검사한다. `repair-mirror`는 무결성이 확인된 SQLite에서만 모든 JSONL 표현을 재생성하며, 의미 병합이나 덮어쓰기는 하지 않는다.

```powershell
# 지정된 수신자만 인계를 수락한다. 생성 때 이미 승인된 인계만 수락 가능하다.
.\.collab\qms-audit.ps1 accept-handoff --handoff rec-... --actor codex --basis "기록 확인" --verification "PARTIAL"
.\.collab\qms-audit.ps1 current-state --task 3

# 기존 Agent 설정은 바꾸지 않고, 해당 작업에 대한 적합성 근거만 남긴다.
.\.collab\qms-audit.ps1 capability-check --task 3 --actor user --agent claude --required "DB 검토" --result UNKNOWN --basis "현재 모델 세부정보 미확인"

# AI 간 파일 Envelope: outbox 생성 → inbox에 전달 → 수신/ACK. 수신 실패 원본은 보존되고 자동 병합은 하지 않는다.
.\.collab\qms-audit.ps1 envelope --sender claude --receiver codex --payload '{"message_type":"REQUEST","request_type":"REVIEW","scope":"app.py","request":"검토"}'
.\.collab\qms-audit.ps1 receive-envelope --file .\.collab\inbox\message.json --receiver codex
.\.collab\qms-audit.ps1 sync --receiver codex

# 현재 버전과 다른 Envelope는 adapter를 먼저 명시적으로 등록해야 한다.
.\.collab\qms-audit.ps1 register-adapter --actor user --approval APPROVED --source-version 0.9.0 --target-version 1.0.0 --scope "..." --evidence-ref rec-...
```

`EVIDENCE`는 관찰·원문·명령 결과처럼 재검토할 수 있는 근거만, `AI_INTERPRETATION`은 그 근거에서 나온 해석만 저장한다. 해석은 Evidence를 대체하지 않는다. 충돌·기준 버전 불일치는 `conflict`로 기록하고 사용자 결정 전 자동 의미 병합하지 않는다.

`record-unification-migration --actor user --approval APPROVED`는 사용자가 승인한 `.collab/` 통합과 hook 설치 요청을 `DECISION`·`CHANGE`·`AUDIT_EVENT`로 남긴다. 이관 전 `core.hooksPath`를 검사한다. 현재 `core.hooksPath`가 설정돼 있으면 설치 스크립트는 중단하며, 그 경로를 사용자 확인 없이 바꾸지 않는다.

### Git commit boundary

`post-commit` is **observation only**: a commit already exists by the time it runs, so it cannot satisfy the Protocol preflight requirement. The supplied `hooks/pre-commit` is the enforcement point. It requires `QMS_AUDIT_GIT_APPROVAL=APPROVED` and records an allowed `GIT_COMMIT` preflight before Git creates the commit. Do not treat a post-commit audit event as retroactive approval.

The current installer checks `core.hooksPath` and blocks if a custom hook directory is active. When the default `.git/hooks` path is used, install the reviewed pre-commit hook alongside the observation hook; preserve any existing hook before chaining it. This repository's hook state must be verified with `git config --get core.hooksPath` and `Test-Path .git/hooks/pre-commit`.

`post-commit` cannot approve a completed Git operation. It only observes it. If its normal audit write fails, it writes a durable JSON marker in `.collab/outbox/postcommit-failures/` and never changes the Git commit result. Run `ingest-postcommit-failures` to append a visible `GIT_COMMIT_AUDIT_FAILED` audit event while preserving the marker.

The pre-commit boundary also requires `QMS_AUDIT_GIT_APPROVAL_RECORD=<DECISION Record ID>`; `QMS_AUDIT_GIT_APPROVAL=APPROVED` alone is insufficient. A main actor records that Record only after a direct user commit instruction, using `git-commit-authorize`. The command hashes the current staged raw diff (paths, modes, and staged blob IDs) and binds the one-time approval to that exact manifest and actor. The hook independently recalculates the manifest and blocks a different actor, changed staged files, missing Record, or a reused Record. It does not consume the approval during pre-commit; the post-commit observation consumes it only after Git has created the matching commit tree.

```powershell
# The instructed Claude/Codex main actor runs this only after the user directly requests this commit,
# and only after the exact files for this commit have been staged.
.\.collab\qms-audit.ps1 git-commit-authorize --actor codex --scope ".collab archive files staged for this commit" --user-request "user's direct commit instruction"
# Save the returned GIT_COMMIT_APPROVAL_RECORD. The actor sets these only for this one git commit:
$env:QMS_AUDIT_ACTOR = "codex"
$env:QMS_AUDIT_GIT_APPROVAL = "APPROVED"
$env:QMS_AUDIT_GIT_APPROVAL_RECORD = "rec-..."
git commit -m "..."
```

This is operational evidence of the direct instruction, not cryptographic proof of who sent a chat message. It grants no file, DB, deployment, or arbitrary Git permission, and `--no-verify` remains prohibited by the project rules.


## Claude·Codex 서브에이전트 워크플로우

사용자가 Claude 또는 Codex 중 **한 메인 actor에게 직접** 기능·화면·동작 변경을 지시한 경우에만 이 워크플로우를 시작한다. 양쪽은 같은 Task의 상대방 기록·Evidence·Review·Handoff를 읽고 이어받을 수 있지만, 기록이 있다는 이유만으로 자동 시작하거나 다른 actor의 지시를 대신할 수는 없다.

사용자가 Claude 또는 Codex에 직접 지시하면, **지시받은 메인 actor가** 아래 `workflow-authorize`를 자동 실행해 정확한 actor·scope·요청을 묶은 1회성 승인 Record를 만든다. 사용자가 이 명령을 직접 실행하거나 비밀값을 입력하지 않는다. 이 Record는 사용자의 직접 지시를 **운영상 기록하는 근거**이며, 누가 명령을 입력했는지를 기술적으로 증명하지는 않는다. Claude·Codex·서브에이전트는 사용자 직접 지시 없이 자신을 위해 이 명령을 실행하거나 사용자 지시를 추정해서는 안 된다.

```powershell
# 지시받은 메인 actor가 사용자 직접 지시를 받은 뒤 자동 실행한다.
.\.collab\qms-audit.ps1 workflow-authorize --actor <claude|codex> --scope "대상 파일·기능" --user-request "사용자 지시 원문"
# WORKFLOW_AUTHORIZATION_RECORD=rec-... 를 보관한다.

# 같은 메인 actor가 authorization Record와 actor·scope·user-request를 그대로 사용해 작업을 시작한다.
.\.collab\qms-audit.ps1 workflow-start --actor <claude|codex> --authorization-record <WORKFLOW_AUTHORIZATION_RECORD> --title "작업 제목" --scope "대상 파일·기능" --user-request "사용자 지시 원문" --baseline "기준 commit"

# 출력된 RUN_ID와 token은 이 작업을 지시받은 메인 actor의 환경에만 보관한다.
$env:QMS_WORKFLOW_CAPABILITY_TOKEN = "<workflow-start 출력 token>"

# 메인 actor가 역할마다 한 번만 배정하고, 출력된 ARCHIVE_CONTEXT만 해당 서브에이전트 프롬프트에 포함한다.
.\.collab\qms-audit.ps1 workflow-dispatch --actor <claude|codex> --task <TASK_ID> --run <RUN_ID> --role planner --scope "범위" --why "계획 필요" --work "스펙 작성"

# 서브에이전트의 ARCHIVE_RESULT JSON을 같은 메인 actor가 한 번만 수집한다.
.\.collab\qms-audit.ps1 workflow-result --actor <claude|codex> --task <TASK_ID> --run <RUN_ID> --role planner --request <REQUEST_RECORD> --result '<ARCHIVE_RESULT JSON>'
.\.collab\qms-audit.ps1 workflow-finalize --actor <claude|codex> --task <TASK_ID> --run <RUN_ID>
```

처음 한 번, 사용자 승인에 따라 Claude·Codex 양쪽에 기록된 actor-neutral workflow 종합 profile이 필요하다.

```powershell
.\.collab\qms-audit.ps1 backfill-workflow-permission --approval APPROVED
```

이 profile은 `.collab workflow orchestration only`에 한정된다. 각 작업은 지시받은 메인 actor가 사용자 직접 지시를 자동 기록한 별도의 `WORKFLOW_AUTHORIZATION` DECISION, actor·scope·요청 일치 및 1회 사용 검사를 통과해야 한다. 과거 v1 authorizer-secret 해시와 관련 기록은 이력 보존을 위해 runtime SQLite에 남지만 운영상 사용하지 않으며, v1 승인 Record는 새 workflow 시작에 사용할 수 없다. `workflow-start`는 UUID `RUN_ID`를 발급하고, 런타임 SQLite에는 capability token의 해시만 저장한다. 원문 token은 해당 메인 actor의 비밀 문맥 또는 환경에만 보관하며 서브에이전트 프롬프트·보고·파일에는 전달하지 않는다.

`.collab` 자체의 검증 명령을 workflow TASK에 기록해야 할 때는, 사용자 승인에 따른 아래 1회성 profile도 먼저 기록한다. 이 profile은 Claude·Codex 메인 actor의 **활성 task/run/token 일치**가 있을 때만 적용되고, 일반 사용자 터미널 `run` 동작은 바꾸지 않는다.

```powershell
.\.collab\qms-audit.ps1 backfill-workflow-test-permission --approval APPROVED

# 지시받은 메인 actor만: 정확히 아래 세 명령만 실행·기록할 수 있다.
.\.collab\qms-audit.ps1 run --task <TASK_ID> --run <RUN_ID> --actor <claude|codex> --summary "원장 검증" -- python qms_audit.py verify
.\.collab\qms-audit.ps1 run --task <TASK_ID> --run <RUN_ID> --actor <claude|codex> --summary "CLI 문법 검사" -- python -m py_compile qms_audit.py
.\.collab\qms-audit.ps1 run --task <TASK_ID> --run <RUN_ID> --actor <claude|codex> --summary "원장 테스트" -- python -m unittest discover tests
```

이 범위 밖의 명령, 잘못된 actor·task·run·token은 실행 전에 차단·감사 기록된다. 이 profile은 임의 shell 명령, QMS 파일·DB 변경, Git, commit, deploy 권한을 주지 않는다. 명령 전문과 출력 전문은 원장에 저장하지 않는다.

서브에이전트는 TASK 범위의 상대방 기록도 읽되 CLI·SQLite·JSONL 원장을 직접 쓰지 않고 `ARCHIVE_RESULT`만 낸다. `workflow-finalize`는 필수 역할 누락, quality-watcher 실패·미수행, 요구된 렌더링 또는 E2E 근거 누락, 미해결 FAIL·ISSUE 또는 PARTIAL/UNKNOWN 근거가 있으면 `PARTIAL`과 차단 근거를 남기고 종료 코드 3을 반환한다.

QMS 파일 수정·테스트는 사용자에게 직접 지시받은 actor가 자동 기록한 승인된 작업 범위에서만 수행할 수 있다. 이 workflow profile이나 token은 commit·deploy 권한을 부여하지 않는다. commit·deploy는 언제나 각각 사용자 직접 지시와 별도 승인 Record가 필요하다. 일반 사용자가 터미널에서 직접 수행한 명령은 workflow가 자동 기록하지 않으므로, 필요할 때 기존 `run` 명령으로 목적·범위·결과를 해당 TASK에 남긴다.
