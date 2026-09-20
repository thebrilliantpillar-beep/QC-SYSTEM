# Claude 작업완료 보고서 (2026-09-21)

> 대상: Codex — 정상 인계(HANDOFF)가 아니라 단순 작업 보고다. TASK 소유권 이전이 필요한 상황이 아니므로 `.collab` HANDOFF Record는 만들지 않았다(claude actor는 UPDATE_STATE에 기본 READ 권한만 가져 정식 HANDOFF 생성이 애초에 막혀 있음 — P-1/P-3 경계, 의도된 설계).
> 기준 commit: `708ccdf` (main, origin과 일치)

## 1. 현재 상태

- `main` HEAD: `708ccdf`. `origin`은 최신 반영. `deploy`(Render)는 `19d4535`까지 반영 — 이후 두 커밋(`b45b20e`, `d31c2f2`, `708ccdf`)은 앱 동작에 영향 없는 순수 문서 변경이라 재배포하지 않았다.
- `.collab` 원장: `python .collab/qms_audit.py verify` 정상(SQLite/JSONL 미러 일치). 이벤트/Record 수는 계속 증가 중.
- 전체 테스트: `python -m unittest discover .collab/tests` → 33/33 통과.

## 2. 완료한 작업

### 2.1 출고 품질확인 필드별 판정값 검증 누락 수정 (커밋 `02da686`)

`outbound_item_check_update`가 `field`/`value`를 각각만 검증하고 조합은 확인하지 않아서, `check_cable` 외 필드에도 "해당없음"을, `check_cable`에도 "특채"를 API 직접 호출로 저장할 수 있는 구멍이 있었다(quality-watcher 재검증에서 발견). `database.py`에 `OUTBOUND_FIELD_ALLOWED_VALUES`(필드별 허용값)를 신설해 막았다.

### 2.2 출고 내역서 엑셀 서식 + 출고 스캔 화면 개선 (커밋 `4e77cd4`)

사용자가 준 실제 참고파일("출고 패킹리스트 변경본.xlsx")을 openpyxl과 raw XML로 직접 열어 대조:

- 헤더 텍스트 정정(F열 줄바꿈 제거), "검사 기준" 행에 A6 라벨 추가
- 행높이(23.6/60/26.15/80.05), 열너비(D~L 전부 18.71 — 처음엔 "미지정"으로 오판했다가 참고파일 원본 XML의 `<cols min="4" max="12">` 범위 지정을 직접 열어보고 정정)
- 폰트명(맑은 고딕) 명시, PASS=파랑/FAIL=빨강/SPECIAL=주황 색상 코딩(품질확인 9종 + 전체판정 양쪽)
- 케이블타이 안내문구를 데이터 마지막 행 다음 K열에 추가

이 작업 중 **CLAUDE.md 25절에 문서화된 버그(6자리 색상 문자열 → 알파 자동 "00"/투명 처리)가 `Font`뿐 아니라 `Border`(Side)·`PatternFill`에도 동일하게 재현되는 것을 새로 발견**해 같이 8자리로 수정했다.

출고 스캔 화면(`outbound_scan.html`)은 "품질확인 (9개 항목)" 새 항목 카드와 "저장된 항목" 세부 토글 **양쪽 다** 항목 전체이름 + 검사기준 문구를 표시하도록 변경(`database.py`에 `OUTBOUND_CHECK_CRITERIA` 신설, 화면·엑셀 공용). 부수적으로 JS 동적 렌더링(`resultCellHtml`)에 누락돼 있던 케이블타이 전용 "해당없음" 버튼 분기도 같이 고쳤다.

전부 LibreOffice PDF 변환 후 참고파일과 픽셀 단위 비교, 헤드리스 크롬 스크린샷으로 화면까지 실제 렌더링 확인했다(코드 리뷰만으로 끝내지 않음).

### 2.3 coverage_limits 개선 실사용 검증 (Claude workflow smoke test 2회)

Codex가 구현한 `coverage_limits` 필드(planner/reuse-scout의 정상적 검증 범위 한계가 `issues[]`로 잘못 분류돼 `workflow-finalize`를 부당하게 막던 문제의 해결책)를 실제 Claude workflow로 2차례 검증했다:

- TASK 5(coverage_limits 개선 전): planner/developer가 정직하게 남긴 한계가 `issues[]`에 섞여 ISSUE 3건 생성 → `workflow-finalize`가 정당하게 `PARTIAL`로 차단(버그 아니라 게이트가 설계대로 작동한 증거).
- TASK 6(coverage_limits 개선 후): 같은 종류의 한계를 `coverage_limits[]`로 분리하자 ISSUE 0건 생성 → `workflow-finalize` `COMPLETED`(exit 0) 정상 종료.

### 2.4 프로토콜 TBD-0002~0011 전수 해소 (커밋 `19d4535`, `b45b20e`, `d31c2f2`)

`docs/protocol-tbd-roadmap.md`를 작성해 `CLAUDE-CHATGPT-PROTOCOL-v1.0.0-draft.md` §19의 TBD 10건을 사용자와 함께 3라운드에 걸쳐 전수 검토·해소했다. 각 TBD의 근거·선택지·최종 결론은 그 로드맵 문서와 프로토콜 §19 Resolved entries에 상세히 남아 있다. 요약:

| TBD | 최종 결론 | 반영 위치 |
|---|---|---|
| 0002 | ID 포맷은 권장 관례로만, 강제 규격화 안 함 | §4.1.2 |
| 0003 | 타임스탬프·직렬화 형식(§4.1.1) + 전역표준화 여부(§4.1.2) 둘 다 결론 남 | §4.1.1/§4.1.2 |
| 0004 | `AI_INTERPRETATION` Record Type을 QMS 적용 사례로 확정 | §6.1 |
| 0005 | ISSUE는 append-only, 해소는 별도 STATE로 표현(코드 1줄 수정: `status="OPEN"`→`"ACTIVE"`) | §5.1.1 |
| 0006 | Operation Registry와 workflow 인가 레인을 통합하지 않고 분리 유지(문서화만) | §8.1 |
| 0007 | GIT_COMMIT 정책 문서화 + **DEPLOY 승인 즉시 강화**(아래 2.5) | §7.3.1 |
| 0008 | 자동 Merge 미구현, 항상 CONFLICT로 확정 | §12.2.1 |
| 0009 | 추가 Relation Type 없음, 필요 시 추가 | §4.3.1 |
| 0010 | 정렬(seq+해시체인) + 보존("삭제 안 함", 구체 수치 없음) 둘 다 결론 남 | §15.1 |
| 0011 | 게이트는 구현됨. **실제 변환 포맷 설계만 의도적으로 미해결**(아래 3절) | §17.2 |

### 2.5 DEPLOY 승인 강화 — `deploy-authorize` + `pre-push` 훅 신설 (TBD-0007, 커밋 `19d4535`)

기존 `git-commit-authorize`+`pre-commit`+`post-commit` 패턴을 그대로 본떠 `git push deploy main`(Render 배포 트리거)용으로 새로 구현:

- `qms_audit.py`: `deploy_authorization_usage` 테이블(append-only), `deploy_authorization()` 검증 함수, `cmd_deploy_authorize`(호출 시점 `git rev-parse HEAD`를 1회성 DECISION에 결속), `preflight()`의 `DEPLOY` 분기
- `.collab/hooks/pre-push`(신규) — remote 이름이 정확히 `deploy`일 때만 게이트, 그 외(`origin` 등)는 무조건 통과
- `install-post-commit-hook.ps1`에 `pre-push` 설치 로직 추가 — **이 저장소에 실제로 설치 완료**
- 신규 테스트 2건(명령경로 단위테스트 + 로컬 bare repo를 가짜 `deploy` remote로 쓰는 실제 `git push` e2e 테스트)
- **GIT_COMMIT과의 중요한 차이**: push는 네트워크 작업이라 성공을 로컬에서 확인할 `post-push` 훅이 없다. 그래서 GIT_COMMIT처럼 "성공 확인 후 소비"가 아니라 **`pre-push` 통과 시점에 바로 승인을 소비**한다 — push 자체가 실패해도 승인은 이미 소비되므로 재시도하려면 `deploy-authorize`를 다시 호출해야 한다. 이 한계는 `.collab/README.md`의 "Deploy boundary" 절에 명시했다(버그 아님, 의도적으로 받아들인 비대칭).
- 커밋 `19d4535` 자체의 배포로 이 기능의 첫 실사용 검증까지 성공적으로 마쳤다.

### 2.6 PROGRESS.md 미커밋 이력 커밋 (커밋 `708ccdf`)

여러 세션에 걸쳐 미커밋 상태로 쌓여있던 `PROGRESS.md`를 커밋해, 저장소가 재클론되거나 `git reset --hard`될 경우의 유실 리스크를 없앴다.

## 3. 미해결 사항 (의도적)

**TBD-0011의 실제 payload 변환 포맷 설계만 의도적으로 미해결로 남겼다.** 실제 MAJOR 버전 전환 사례가 하나도 없는 상태에서 추상적으로 변환 포맷을 설계하면, 프로토콜 자체가 못박은 원칙("미정 사항을 추측으로 채우지 않는다")과 정면으로 충돌한다. "게이트(`register-adapter`)는 이미 구현됨, 실제 변환 형식은 첫 MAJOR 버전 제안이 나올 때 그 구체적 필요에 맞춰 설계한다"가 그 자체로 완결된 최종 결론이다 — 빠뜨린 게 아니라 트리거 조건이 분명한 결정이다.

## 4. Codex가 알아둘 것

- **배포 전 반드시 `.collab/README.md`의 "Deploy boundary" 절을 먼저 읽을 것.** 이제 `git push deploy main`은 사전에 `deploy-authorize`를 실행하지 않으면 `pre-push`에서 차단된다. 모르고 시도하면 단순 push 실패로 오인하기 쉽다.
- TBD-0011 재오픈이 필요한 상황(실제 MAJOR 버전 제안 등)이 생기면 사용자에게 먼저 알릴 것.
- QMS 파일/DB 변경, commit, deploy는 여전히 각각 사용자 직접 지시가 필요하다는 기존 원칙은 전혀 바뀌지 않았다.

## 5. 검증 근거

- `python -m unittest discover .collab/tests` → 33/33 통과
- `python .collab/qms_audit.py verify` → 정상
- 출고 엑셀: LibreOffice PDF 변환 후 참고파일과 픽셀 단위 비교
- 출고 스캔 화면: 헤드리스 크롬(PowerShell 경유) 스크린샷으로 실제 렌더링 확인(새항목카드·저장된항목 세부토글 둘 다)
- 배포 후 `https://chardon-qms-byjh.onrender.com/login` 200 확인 2회
- quality-watcher를 이번 세션에서 여러 차례 호출했고, 발견된 결함(출고 필드검증 누락, JS 동적렌더 케이블타이 분기 누락, `preflight()` DEPLOY 분기 try/except 비대칭 등)은 모두 그 자리에서 수정 완료

## 6. 참고

- 상세 근거·선택지·과거 결정 이력: `docs/protocol-tbd-roadmap.md`
- 최신 확정 내용: `CLAUDE-CHATGPT-PROTOCOL-v1.0.0-draft.md` §19 Resolved entries
- 이 보고서 자체는 사용자가 대화에서 직접 요청함("코덱스 보고용으로 너가 일한 내용 정리해서 작업완료 보고서 써줘").
