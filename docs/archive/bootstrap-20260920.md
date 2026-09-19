# Bootstrap Record — CLAUDE ↔ CHATGPT PROTOCOL v1.0.0

- RECORD_ID: bootstrap-20260920
- 생성일: 2026-09-20
- 생성자: Claude Code (이 세션)
- 목적: TBD-0001 확정 후 최초 프로토콜 상태 기록

---

## PROJECT

- 이름: IQC 입고검사 성적서 자동화 시스템
- 저장소: `C:\Users\Jaiden\Desktop\iqc-app`
- 업종: 배전용 전력기기 제조업체(샤든그룹, 리클로저 등) — 자동차 부품 아님
- 정본 컨텍스트 문서: `CLAUDE.md`, `docs/ai-sidekick-handoff.md`, `PROGRESS.md`

---

## AGENTS

| 에이전트 | 역할 | 기본 권한 |
|---|---|---|
| Claude Code | 메인 개발자, 주 변경 담당 | read + write + git_commit |
| ChatGPT/Codex | 보조 / 긴급 대체 | read_only (기본), 사용자 명시 승인 시 write |

---

## CONFIRMED DECISIONS

| ID | 결정 | 날짜 | 근거 |
|---|---|---|---|
| TBD-0001 | Archive 저장소: `docs/archive/`, 파일명 `handoff-YYYYMMDD-HHMMSS.md` | 2026-09-20 | git이 이력 보존, Claude·Codex 모두 접근 가능, 외부 서비스 불필요 |
| CONFLICT-0001 | `EMERGENCY_HANDOFF.md`는 최신 신호 파일로 유지, 덮어쓰기 전 archive 복사 | 2026-09-20 | TBD-0001 확정으로 해결 |

---

## RULES (현재 적용 중)

- P-1: Codex 기본 역할 = read_only, 긴급 대체 시 사용자 명시 승인 필요
- P-2: PERMISSION_PROFILE (filesystem, git, handoff signal)
- P-3: Codex 대체 commit의 3가지 필수 조건
- P-4: EMERGENCY_HANDOFF.md 형식 + 덮어쓰기 전 `docs/archive/` 복사 (2026-09-20 추가)
- P-5: 작업 시작 전 git log / git status / PROGRESS.md / Handoff 파일 확인
- P-6: 금지 명령 목록 (git reset --hard 등)

---

## STATE

- v1.0.0 ↔ P-1~P-6 대조표: 완료 (IMPLEMENTED 0 / PARTIAL 8 / MISSING 9 / TBD 2 / CONFLICT 0)
- CONFLICT-0001: 해결됨 (P-4 수정 + TBD-0001 확정)
- AGENTS.md 구버전 오류 4건: 수정 완료 (2026-09-20)
  1. 자동차 부품 → 배전용 전력기기
  2. NCR 최종결정권자 게이트 설명 최신화
  3. AQL 2줄 → 3줄 형식 안내
  4. P-4 archive 복사 규칙 추가

---

## NEXT

- AGENTS.md 최신화 commit
- v1.0.0의 나머지 MISSING 항목은 실제 필요 시점에 최소 단위로 추가
