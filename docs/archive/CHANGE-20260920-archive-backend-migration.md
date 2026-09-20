# CHANGE — QMS Archive Backend Migration

- RECORD_ID: `CHANGE-20260920-archive-backend-migration`
- 작성일: 2026-09-20
- 상태: `APPLIED` — 문서·저장 경로 이관 기준
- 결정자: 사용자
- 관계: `supersedes` → 초기 Archive Storage Backend 결정 (`bootstrap-20260920`, TBD-0001)

## FACTS

- 초기 결정은 `docs/archive/`에 bootstrap 및 `handoff-<HANDOFF_ID>.md`를 보존하는 방식이었다.
- 사용자는 QMS 협업 기록과 Emergency Handoff의 현재 저장소를 프로젝트 내부 `.collab/`으로 통합하도록 결정했다.
- 이 변경은 기존 `docs/archive/` 자료의 삭제·이동·내용 변경을 승인하지 않는다.

## DECISION

새 QMS 협업 기록은 `.collab/`에 생성한다.

- Runtime 원장: `.collab/runtime/audit.sqlite3`
- Git 추적 미러: `.collab/events/YYYY-MM.jsonl`
- Emergency Handoff 본문: `.collab/handoffs/handoff-<HANDOFF_ID>.md`
- 최신 Handoff 존재 신호: `docs/EMERGENCY_HANDOFF.md`

## SCOPE

- AGENTS.md P-4·P-5, CLAUDE.md의 협업 기록 참조, 프로토콜 §12·§17·§19의 Archive Backend 참조를 현재 저장소에 맞춘다.
- `docs/archive/`는 역사 자료만 읽는 위치가 된다. 이 폴더에 새 Handoff를 작성하지 않는다.
- 다른 TBD, Record lifecycle, Merge 알고리즘, 감사 보존 정책은 이 결정만으로 해결되지 않는다.

## VERIFICATION

- 문서 경로가 `.collab/` 현재 저장소와 일치하는지 확인 필요.
- `.collab/handoffs/` 생성과 Git post-commit hook 설치의 실행 검증은 별도 감사 이벤트로 기록한다.
