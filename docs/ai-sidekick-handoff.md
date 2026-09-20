# IQC/QMS 프로젝트 — AI Sidekick(ChatGPT) 인수인계 브리핑

**작성**: Claude Code(Sonnet 5) 메인 세션, 2026-09-18
**작성 방식**: 코드 수정 없음(읽기 전용 조사). `CLAUDE.md`(1561줄) 전체, `PROGRESS.md`(588줄)
전체, `.claude/agents/*.md` 5개 전체, `.claude/settings.json`(프로젝트+전역), `README.txt`,
`render.yaml`/`Procfile`/`Dockerfile`, `requirements.txt`, git log/branch/remote, 그리고
`app.py`/`database.py`의 실제 소스 코드(스키마·라우트 정의부)를 직접 대조해서 작성했다.
**표기 규칙**: 확인된 사실은 그대로 서술한다. 확인 못 했거나 추정인 것은 `UNKNOWN` 또는
`NEEDS_CONFIRMATION`으로 명시한다. 문서와 실제 코드가 다르면 `DOCUMENT SAYS` /
`ACTUAL CODE` / `CONFLICT` 형식으로 병기한다. 이번 조사에서 "더 좋은 방법"은 제안하지
않았다 — 현재 규칙과 구조를 사실 그대로 옮기는 것이 목적이다.

---

## 1. 프로젝트 정체성

**1-1. QMS 시스템의 정확한 목적**
샤든그룹(리클로저 등 배전용 전력기기 제조업체)의 **입고 자재 검사 성적서**를 수기 작성에서
자동화로 전환하는 것. 검사자가 측정값만 입력하면 판정→승인→PDF 성적서 출력까지 자동으로
나온다. (CLAUDE.md 0절. 2026-09-17에 사용자가 "자동차 부품 제조업체"라는 예전 세션의 잘못된
추정을 리클로저 제조업체로 직접 정정했다 — 회사 정체성에 대한 가장 신뢰도 높은 출처는 이
정정 기록이다.)

**1-2. 실제 사용자**
품질관리팀 검사자(파이썬·VBA 기초만 알고 JS/서버/DB는 미경험인 비개발자), 승인권자(관리자급),
그리고 이 저장소를 관리하는 사용자 본인(개발자 역할도 겸함, `project_dev_roadmap.md` 개인
메모리에 "품질검사팀: 팀장+수입검사 파트+공정검사 파트" 구조가 있었으나 이건 auto-memory이지
프로젝트 파일이 아니므로 이 문서에서는 참고 수준으로만 언급 — CLAUDE.md/PROGRESS.md에는
조직 구조가 기술돼 있지 않음, **NEEDS_CONFIRMATION**: 현재도 이 조직 구조가 유효한지). 안드로이드
태블릿으로 접속하는 게 주 사용 방식(CLAUDE.md 0절).

**1-3. 해결하려는 핵심 업무 문제**
수기 성적서 작성의 비효율과 오류(판정 실수, 문서 형식 불일치, 이력 추적 어려움)를 없애고,
입고→검사→판정→승인→NCR/특채→출력까지 하나의 시스템 안에서 끊김 없이 처리하는 것.

**1-4. 현재 구현된 핵심 업무 흐름(끝에서 끝까지)**
1. **입고 등록**(`/intake`) — 업체·날짜 공통 입력 + 자재별 스프레드시트형 붙여넣기. 조립품(MA)
   파츠번호 입력 시 자동 전개 옵션. 중복 입고는 병합 또는 별도등록 선택.
2. **검사 대기 목록**(`/inspect/new`) — 대기 중인 입고 건 목록, 검색/정렬/우선검사 표시.
3. **검사 입력**(`/inspect/form`) — 항목별 측정값 입력, AQL 기반 샘플수 자동 계산, 계측기
   유효기간 자동 연동, 0.8초 디바운스 서버 자동저장.
4. **판정** — `judge_numeric()`(단측 규격/규격미입력 케이스 처리, AQL Ac 허용치까지 자동
   반영)이 항목별 자동 판정, 전체 판정(`overall_result`)까지 계산.
5. **성적서 상세/승인**(`/inspection/<id>`) — 비고 3칸, 승인/반려/특채/불합격 확정(전부
   `_final_decision_block_reason()` 게이트 통과 필요), 서명 패드, 승인 시점 `content_hash`
   생성.
6. **불합격/특채 시** — NCR(부적합통보서) 작성→(선택적으로) 확인·발송, 필요 시 반품 처리.
7. **출력**(`/output`) — 승인된 성적서를 xlsx→PDF(LibreOffice 변환)로 일괄/개별 출력,
   `pdf_hash` 생성, `성적서 발행/YYYY-MM-DD/` 폴더에 저장.
8. **품질 현황 대시보드** — `quality_report()` 단일 집계 함수로 불량률/PPM/Cpk 등 시각화.
9. (별도 하위시스템) **출고 관리** — 완제품 S/N 발급→QR스캔 출고 등록→엑셀 출력. **입고검사와
   DB 차원에서 완전히 분리**돼 있고 로트 역추적 연결이 없다(2026-09-17 QMS 정밀진단에서
   최우선 발견사항으로 지적됨, `docs/qms-audit-backlog.md` 참고).

**1-5. 절대로 잃어버리면 안 되는 핵심 가치/의도**
- **비전문가도 웹 UI 클릭만으로 전부 가능해야 한다**(CLAUDE.md 0절, 10절) — CLI/설정파일
  편집을 요구하는 기능은 이 프로젝트 철학과 맞지 않는다.
- **판정 근거 없는 결정을 막는다** — 규격미입력 항목이 있으면 승인도 불합격도 못 낸다
  (4-1절, 8-2-8절). 이건 "우리 데이터 누락을 협력사 책임으로 떠넘기지 않는다"는 원칙에서
  나온 것이라 되돌리면 안 된다.
- **판정/감사 관련 기능은 축소하지 않는다** — 사용자의 명시적 선호(개인 메모리
  `feedback_scope_on_integrity_features.md`, 프로젝트 문서 밖이라 참고 수준). UI 편의
  기능과 품질판정·승인·집계 로직은 취급이 다르다.
- **실제 렌더링/실행 확인 없이 "완료"라고 하지 않는다**(CLAUDE.md 11절, 19절) — 이
  프로젝트에서 여러 차례 실제 사고로 이어졌던 원칙.

---

## 2. 현재 프로젝트 상태

**2-1. 개발 단계**: 초기 커밋 2026-08-21, 현재(2026-09-18)까지 약 4주간 315개 커밋. 핵심
기능(입고~검사~승인~출력~NCR~대시보드~출고관리)은 CLAUDE.md 3~8절 기준 **이미 대부분 구현
완료**로 명시돼 있다("참고: 성적서 자동화의 주요 기능은 이미 대부분 구현되어 있음" 섹션,
PROGRESS.md 끝부분). 지금은 신규 대형기능 개발보다는 **기능 다듬기·버그 수정·사용자 피드백
반영** 단계에 가깝다(PROGRESS.md 최근 이력이 전부 이런 성격).

**2-2. 최근(2026-09-17~18) 작업 중이던 기능**: 입고 등록/검사대기 목록에 "우선검사" 플래그
추가 — 완료됨(커밋 `74a36e5`~`9da005b`). 완료 후 진행 중인 별도 트랙은 없다(**이 문서
작성 시점 기준 활성 작업 없음** — 이번 요청 자체가 활성 작업임).

**2-3. 최근 완료된 작업**: PROGRESS.md 최근 이력 순으로 — 우선검사 플래그(2026-09-17),
QMS 종합진단+라이벌 관점 재조사(2026-09-17, `docs/qms-audit-backlog.md` 신설), 업종
정정(자동차→리클로저), 화면간 불일치 정리(reuse-scout 파이프라인), NCR/개선요청서 수정
기능+삭제 권한 세분화, 리스트 화면 엑셀 출력 일괄 추가(20개 화면), 대시보드 NCR포함 지표,
개선요청서 신규 서브시스템, 출고 관리 대폭 확장(사진 분류/잠금/계획외 차단/일괄삭제 등).

**2-4. 미완료 작업**: CLAUDE.md 9절이 정본.
- **자유 양식 성적서 제작기**(드래그앤드롭 커스텀 템플릿) — 설계안만 있고 미구현, 보류 중.
- **QMS 개선 백로그**(`docs/qms-audit-backlog.md`) 16개 항목 — 전부 미착수(체크박스 전부
  비어있음, 2026-09-18 기준). 상위 7개, 중위 5개, 하위 4개 + 2026-09-17 라이벌 관점
  재조사에서 추가된 보안/SDLC 항목 7개(상3·중2). **이 문서를 읽는 시점에 이 파일을 다시
  열어서 상태를 확인할 것** — 이 브리핑 문서보다 그 파일이 더 최신일 수 있다.
- **designer의 iqc-app 전체 화면 최초 감사** — 아직 미실행(PROGRESS.md 끝부분).

**2-5. 알려진 버그**: 이 문서 작성 시점 기준 CLAUDE.md/PROGRESS.md에 "미해결"로 명시된
버그는 없다(발견된 버그는 발견 즉시 고치는 게 이 프로젝트 관례). 다만 **이번 조사에서
새로 발견한 미확인 위험**이 있다 — 13절/14절 참고(Render 배포 방식 Dockerfile vs
render.yaml 충돌).

**2-6. 기술부채/의도적으로 미뤄둔 것**:
- `material_groups`/`spec_review_flags` 테이블 — 기능은 삭제됐지만 테이블은 빈 채로 잔존
  (5-1절).
- 성적표 일부 레거시 데이터 인코딩 깨짐(`supplier_reports.supplier`) — 원인 미조사, 영향
  없어서 보류.
- f-string 기반 SQL 조립 패턴(39곳) — 현재는 안전(컬럼명 고정+파라미터 바인딩)하지만
  안전망(정적분석/테스트) 부재로 향후 재발 가능성 존재(`docs/qms-audit-backlog.md`).
- 자동화 테스트 스위트 0개, CI 파이프라인 0개 — 이 프로젝트는 "1회성 검증 스크립트 +
  실제 실행 확인" 관례로 대체하고 있다(developer.md 명시).

**2-7. "루프 엔지니어링"에서의 현재 위치**: 이 프로젝트는 정해진 스프린트/이터레이션
개념이 없다. 사용자가 요청 → planner(필요시) → developer → quality-watcher → 사용자
확인 → 커밋 → 배포, 이 사이클이 요청 단위로 반복되는 구조다(8절 참고). "다음 스프린트"
같은 개념 자체가 **NOT APPLICABLE**.

**2-8/2-9. 다음에 해야 할 작업과 우선순위**: 문서상 확정된 "다음 작업"은 없다 — 사용자가
다음 요청을 할 때까지 대기 상태. 다만 **문서에 근거해 우선순위를 매길 수 있는 후보**는
`docs/qms-audit-backlog.md`의 "상" 등급 항목들이다:
1. 출고 S/N ↔ 입고 로트 연결(로트 역추적) — 자동차/전력기기 리콜 대응의 최소 요건
2. NCR CAPA 폐루프(근본원인·조치·효과검증·종결)
3. `SECRET_KEY` 하드코딩 폴백 제거(app.py:113) — 세션 위조 리스크
4. CSRF 방어 부재
5. 로그인 무차별대입 방어 부재
이 다섯은 전부 **사용자가 아직 공식적으로 "이것부터 하자"고 확정하지 않았다** — Sidekick이나
Claude Code가 임의로 착수하면 안 되고, 반드시 사용자 확인 후 진행해야 한다(15절 "사용자
확인 필요 상황" 참고).

**PROGRESS.md와 실제 코드 상태 일치 여부**: 이번 조사에서 대조한 범위(권한 세분화 개수,
DB 스키마 주요 컬럼, git 커밋 해시, 최근 라우트/함수명)는 전부 실제 코드와 일치했다.
단 하나 확인된 불일치는 3절(CLAUDE.md 5절의 "20개 권한" 표기, 아래 14절 참고).

---

## 3. 프로젝트 규칙 / 헌법

**3-1. 반드시 지켜야 하는 규칙**
- 판정 로직에서 하한/상한이 `None`인 케이스(단측 규격)를 항상 테스트에 포함(4절).
- `template_form.xlsx`/`standard_template.xlsx` 원본 파일은 절대 직접 열어서 저장하지
  않는다 — 항상 `shutil.copy`로 복사해서 사용(7-1절).
- 코드 수정 완료 후 사용자 보고 전 **quality-watcher 서브에이전트 검증 필수**(11절, 13절).
- CSS/시각적 변경, 이미지가 걸린 엑셀 출력은 quality-watcher 코드리뷰만으로 "완료"
  선언 금지 — 실제 렌더링(헤드리스 크롬) 또는 실제 엑셀(Excel COM) 확인 필수(11절).
- 새 화면에서 AQL 표기는 `|aql_display` 필터, 날짜는 `|date_korean`/`|datetime_korean`
  필터만 사용(7-3-1절, 8-3절).
- 최종 결정 3종(승인·특채·불합격 확정)은 서명 필수 + `_final_decision_block_reason()`
  게이트 통과 필수(8-2절, 8-2-8절).

**3-2. 절대로 수정하면 안 되는 규칙(사용자가 "되돌리지 말 것"으로 명시)**
- 비밀번호 평문 저장(3절, 의도적 결정 — 단 이건 나중에 사용자가 바꿀 수도 있는 종류의
  결정이라는 점은 유의. **NEEDS_CONFIRMATION**: 2026-09-17 라이벌 관점 재조사에서 이
  결정에 대한 재검토가 백로그에 올라와 있음 — "절대 수정 금지"와 "재검토 대상"이 같은
  프로젝트 안에 공존하는 상태).
- `activity_log` 테이블에 edit/delete UI 추가 금지(3절, 감사 신뢰성).
- `report_builder.build_report_filename()`의 특수문자 "치환 아닌 제거만" 규칙(6절).
- 도면번호 계산 규칙(`compute_drawing_no`, 첫 P만 치환, 7-2절/20절).
- 성적서 관련 4개 삭제버튼은 전부 admin 전용(14절, 변경 이력까지 문서화된 최종 확정).
- NCR 확인(`ncr_confirm`)은 최종결정권자 게이트를 쓰지 않음(8-2절, 2026-09-10 확정).
- 출고 확인=잠금, 회수는 신원 기반(22절, 예전 설계문서를 사용자가 직접 뒤집은 것).
- "계획외" 항목만 하드 블로킹, 나머지(미등록/중복)는 경고만(24절, 21절 원칙의 예외).
- 색상 팔레트/폰트 리브랜딩은 2026-09-08에 종료 확정 — 추가 요청 없이 남은 색상들
  건드리지 말 것(16절).
- QR 라벨 엑셀은 fit-to-page 대신 고정배율 95%(7-5절 예외).

**3-3. 특정 파일/디렉터리를 함부로 수정하면 안 되는 이유**
- `template_form.xlsx`/`standard_template.xlsx` — 공유 템플릿 오염 시 이후 모든 성적서가
  옛 데모값을 물려받는 실제 사고가 있었다(7-1절).
- `iqc.db` — `.gitignore`에 등록돼 커밋 금지(실 데이터 보호). `backups/`, `성적서 발행/`,
  `자동출력/`, `static/signatures/`, `static/ncr_photos/`도 동일하게 커밋 제외.
- `judge.py` — 초기 프로토타입 잔재, 실제 판정 로직과 무관(2절). 참조하면 안 된다.

**3-4. 코드 작성/수정 시 반드시 따라야 하는 패턴**
- 공용 헬퍼 재사용 원칙(8-1절 표 참고) — `build_specs_with_sample`, `format_aql`,
  `item_label`, `normalize_spec_text`, `_place_photos_in_area`, `is_out_of_range`(20절)
  등은 절대 재구현하지 않고 재사용.
- 엑셀에 이미지/도형 배치 시 `_excel_col_width_to_emu()`를 기본으로 쓰고, `7*9525` 같은
  하드코딩 배수를 복붙하지 않는다(7-4-1절, 세 번 재발한 함정).
- `OneCellAnchor`+`ext`보다 `TwoCellAnchor(editAs="oneCell")`가 더 안전(7-4-2절, 아직
  일부 함수는 미전환 상태로 남음).
- fit-to-page 3줄 세트(방향+fitToWidth+fitToPage)는 여러 열 표 만들 때 필수(7-5절).
- HTML 요소를 감출 때 `hidden` 속성 대신 인라인 `style="display:none"` 사용(17절
  2026-09-17 추가, `.badge` 등 `display`를 이미 지정하는 클래스가 있으면 `hidden`이
  무시됨).
- `base.html`의 범용 선택자(`table`, `button` 등)에 이미 있는 속성(`overflow` 등)을
  특정 화면에서 끄려면 "지우기"가 아니라 "반대값으로 명시적으로 덮어쓰기"(17절).

**3-5. 테스트 관련 규칙**: 정식 테스트 스위트(pytest 등)가 없다. `systematic-debugging`
스킬의 "실패 테스트를 TDD로 작성하라"는 지침은 이 프로젝트에 명시적으로 **적용 안 함**
(developer.md) — 대신 Flask `test_client()`로 만든 1회성 검증 스크립트를 그 자리에서
실행하고 결과를 확인하는 방식.

**3-6. Git 관련 규칙**: 커밋은 항상 메인 세션(사람 확인 하에)만 한다 — developer/designer
서브에이전트는 `git commit`/`git push` 권한이 없다(13절, developer.md). 전역 `git-guardrail.py`
훅이 `git reset --hard`/`git push --force`/`git clean -f*`/`git checkout .`/`git branch -D`를
하드 차단한다(프로젝트 무관 전역 안전장치). 로컬 프로젝트 설정(`.claude/settings.json`)은
`git commit`/`git push`를 "ask"(승인 필요)로 지정.

**3-7. 배포 관련 규칙**: `git push deploy main` — **반드시 `deploy` 리모트여야 함**
(`origin`으로 푸시해도 Render에 반영 안 됨, `project_render_production.md` 개인 메모리
— 이 사실 자체는 CLAUDE.md/PROGRESS.md에는 명시적으로 안 적혀 있어서 **NEEDS_CONFIRMATION**:
이 규칙을 프로젝트 파일(CLAUDE.md)에도 옮겨 적어야 하지 않을지 사용자에게 확인 필요).
브랜치 전략 자체가 없다 — 항상 `main`에 직접 커밋(19절).

**3-8. 데이터베이스 변경 규칙**: 모든 스키마 변경은 `database.py`의 `init_db()` 안에
**멱등 마이그레이션**(`PRAGMA table_info`로 컬럼 존재 확인 후 `ALTER TABLE`)으로 추가한다.
UNIQUE 제약 추가처럼 기존 프로덕션 데이터와 충돌 가능한 변경은 회피하고 애플리케이션
레벨 잠금(`BEGIN IMMEDIATE`)으로 대체한 선례가 있다(20절 7번).

**3-9. 에이전트 사용 규칙**: 6절 참고. 요약 — planner는 새 기능/화면변경 요청에 자동
최우선 호출, developer는 그 다음, designer는 developer 완료 후 순차(병행 안 함),
quality-watcher는 코드 수정 완료 보고 전 자동, reuse-scout는 사용자가 명시적으로
요청할 때만 수동 호출.

**3-10. 플러그인 사용 규칙**: 7절 참고.

---

## 4. 기술 아키텍처

**4-1. 전체 기술 스택**
- 언어/프레임워크: Python 3(로컬은 버전 미고정, Dockerfile은 `python:3.11-slim-bookworm`),
  Flask(버전 미고정 — `requirements.txt`에 `flask`만, 숫자 없음).
- DB: SQLite 단일 파일(`iqc.db`).
- 템플릿: Jinja2(Flask 내장) 서버사이드 렌더링. SPA/프론트엔드 프레임워크 없음.
- 문서 생성: `openpyxl`(xlsx), `reportlab`/`pypdf`(PDF 보조 처리로 추정, **NEEDS_CONFIRMATION**:
  이번 조사에서 이 두 라이브러리의 정확한 사용처까지는 추적 안 함), LibreOffice(`soffice`,
  xlsx→PDF 변환 — 로컬은 PATH/일반 설치경로 탐색, 프로덕션은 13절 참고 **CONFLICT** 있음).
- 이미지: `Pillow`(로고 비율 계산 등), `qrcode`(QR 생성).
- 스케줄링: `APScheduler`가 `requirements.txt`에 있으나, 이번 조사 중 로컬 실행 시
  `No module named 'apscheduler'` 경고가 떴다 — **NEEDS_CONFIRMATION**: 실제로 이
  라이브러리를 쓰는 기능이 뭔지, 로컬 환경에 설치가 안 돼 있는 것뿐인지 아니면 코드
  자체가 이 의존성을 잘못 참조하고 있는지 추적 안 함.
- WSGI 서버: `gunicorn`(프로덕션).
- 서버 사이드 세션: Flask 기본 서명 쿠키 세션(`app.secret_key`).

**4-2. Frontend 구조**: 없음(별도 프론트엔드 빌드 체계가 아니라는 뜻) — 서버가 렌더링한
HTML(`templates/*.html`, 84개 파일)에 바닐라 JS(각 템플릿 내 `<script>` 블록, 일부는
`static/*.js`로 분리 — `collapsible_sections.js`, `kpi_countup.js`, `photo_resize.js`,
`signature_pad.js` 등 공용 컴포넌트). CSS도 대부분 `base.html`의 공용 `<style>`에 정의된
걸 상속받는 구조, Chart.js(CDN)를 대시보드에서 사용. 빌드 스텝(webpack/vite 등) 없음 —
정적 자산은 원본 그대로 서빙.

**4-3. Backend 구조**: 단일 `app.py`(9,300여 줄) 안에 Flask 라우트 218개가 전부 정의돼
있다(블루프린트 분리 없음, 모놀리식 단일 파일 구조). 권한 데코레이터(`@perm_required`),
판정 로직(`judge_numeric` 등), 세션/인증 로직도 이 파일 안에 있다. `database.py`(5,600여
줄)가 모든 DB 접근을 담당(별도 ORM 없음, `sqlite3` 표준 라이브러리 직접 사용).
`report_builder.py`(2,100여 줄)가 xlsx/PDF 생성 전담. `spec_import.py`가 규격 엑셀
파싱 전담.

**4-4. Database 구조**: 5절 참고. SQLite 파일 하나(`DATA_DIR/iqc.db`), FK 제약은 일부만
사용(예: `outbound_items.batch_id`), 다수 테이블은 **의도적으로 FK 없이 느슨한 문자열
연결**(`material_bom_links`↔`materials`, `finished_goods_serials`↔`outbound_items` 등 —
"미등록 데이터도 경고만 하고 통과시켜야 한다"는 현장 요구 때문, database.py 739행 주석에
명시).

**4-5. Authentication/Authorization 구조**: 세션 쿠키 기반 로그인(`/login`, 평문 비밀번호
비교). 권한은 `users.permissions` 콤마구분 문자열, `app.py`의 `PERM_GROUPS`가 원본 정의
(8개 그룹, 실제 28개 개별 권한 — 아래 14절 CONFLICT 참고). `@perm_required(...)`는 OR
조건(여러 권한 중 하나만 있어도 통과) — 23절에 "삭제 권한은 반드시 단독으로만 걸 것"이라는
명시적 함정 경고가 있다. `is_final_approver`(최대 2명)가 승인/특채/불합격 확정의 추가
게이트. CSRF 토큰 없음(2026-09-17 라이벌 관점 재조사에서 확인, 백로그 등재).

**4-6. API 구조**: 정식 REST API 설계는 없다. 대부분 라우트가 서버 렌더링 HTML을
반환하는 전통적 MVC 패턴이고, 일부만 AJAX용 JSON을 반환한다(예: `/intake/<id>/toggle-priority`,
`/dashboard/chart-data`, `/outbound/serial-check`). 외부 시스템이 소비할 수 있는
공식 API 계약(OpenAPI 스펙 등)은 **없음**.

**4-7. 주요 서비스/모듈 간 의존관계**: `app.py` → `database.py`(모든 DB 접근),
`app.py`/`report_builder.py` → `spec_import.py`(규격 파싱 시), `app.py` → `report_builder.py`
(출력 시). `database.py`는 다른 모듈에 의존하지 않는다(최하위 계층). 순환 의존 없음
(**NEEDS_CONFIRMATION**: 전수 확인은 안 했음, import 문 grep 기준 추정).

**4-8. 외부 서비스와의 연결**: SMTP(이메일 발송, NCR/개선요청서/성적표), CDN(Chart.js,
html5-qrcode) — 인증이 필요한 외부 API 연동(결제, 클라우드 스토리지 등)은 **없음**.
ERP(더존 아마란스) 연동은 개인 메모리(`project_dev_roadmap.md`, 프로젝트 파일 아님)에
"장기 로드맵" 항목으로만 언급돼 있고 실제 구현은 **없음(UNKNOWN 진행 여부)**.

**4-9. 핵심 디렉터리 구조**: 2절 CLAUDE.md 원문 그대로.
```
iqc-app/
├── app.py / database.py / report_builder.py / spec_import.py / judge.py(미사용)
├── template_form.xlsx / standard_template.xlsx / logo.png (원본, 직접 수정 금지)
├── templates/ (84개 html) / static/ (js, signatures/, ncr_photos/ 등)
├── docs/ (superpowers 스펙·계획 문서 + qms-audit-backlog.md + 이 문서)
├── .claude/ (agents/*.md 5개, settings.json, settings.local.json)
├── 성적서 발행/ backups/ (자동 생성, 커밋 제외)
├── requirements.txt / Procfile / render.yaml / Dockerfile
└── README.txt (비개발자용 운영 안내서)
```

**4-10. 데이터가 시스템을 통과하는 주요 흐름**: 1-4절 "핵심 업무 흐름" 참고. 간단히:
사용자 입력(폼) → Flask 라우트 → `database.py` 함수(파라미터 바인딩 SQL) → SQLite →
필요 시 `report_builder.py`(xlsx 생성 → LibreOffice 변환 → PDF) → 파일시스템(`DATA_DIR`
하위) 저장 → 다음 조회 시 다시 DB에서 읽음.

---

## 5. 데이터 모델

**5-1. 핵심 Entity**: `materials`(자재), `specs`(자재별 검사항목), `intake_list`(입고),
`inspections`(성적서 헤더), `inspection_items`(성적서 항목), `users`(계정), `ncr`(부적합
통보서), `improvement_requests`(개선요청서), `suppliers`/`supplier_contacts`(업체),
`gauge_master`(계측기), `change_points`(4M변경점), `supplier_reports`(업체 성적표),
`assembly_masters`/`assembly_components`(조립품, 입고 자동전개용), `material_bom_links`
(통합BOM, 조회 전용 — 위 조립품과 무관, 18절), `finished_goods_serials`/`outbound_batches`/
`outbound_items`/`outbound_item_photos`(출고 관리, 입고검사와 완전 별개 서브시스템).

**5-2. Entity 간 관계**: `inspections.material_no` → `materials.material_no`(FK 없이
문자열 연결), `inspection_items.inspection_id` → `inspections.id`(FK 있음, 3절 원문
기준 확인은 안 했으나 CLAUDE.md 서술상 그렇게 추정 — **NEEDS_CONFIRMATION**: 정확한
FK 제약 유무는 `database.py`의 `CREATE TABLE` 문 전체를 다시 대조해야 확정), `ncr.inspection_id`
→ `inspections.id`(nullable, 수기입력 NCR은 연결 없음), `outbound_items` ↔ `finished_goods_serials`
는 `serial_no` 문자열로만 느슨하게 연결(FK 없음), **`outbound_items`에서 `materials`/
`inspections`로 가는 연결이 전혀 없다**(2026-09-17 정밀진단 최우선 발견사항).

**5-3. 중요한 DB 제약조건**: `users.permissions`가 없으면 아무 권한도 없는 계정.
`intake_list.status`는 '대기'/'검사완료' 둘 중 하나(앱 레벨 제약, DB CHECK 제약
여부는 **UNKNOWN**). `inspections.status`는 'pending'/'approved'/'rejected'/'superseded'.

**5-4. 데이터 무결성을 위해 반드시 지켜야 하는 사항**: `create_inspection()`의
`BEGIN IMMEDIATE` 잠금(중복 성적서 생성 방지, 20절 7번) — 이 함수를 우회하는 새 INSERT
경로를 만들면 안 된다. `approve_revoke` 시 서명·해시를 반드시 같이 지워야 함(8-2-10절).

**5-5. 수정 시 특히 주의해야 하는 데이터 구조**: `specs.lower_limit`/`upper_limit`
(None 조합 3가지: 둘다있음/한쪽만/둘다없음 — 각기 다른 판정 경로, 4절/4-1절). `ncr`/
`outbound_items` 등 FK 없는 테이블들 — 수정 시 참조 무결성을 코드로 직접 보장해야 함
(DB가 안 막아줌).

**5-6. 기존 데이터와의 호환성 고려사항**: 신규 컬럼은 항상 `DEFAULT` 값과 함께 멱등
마이그레이션으로 추가(3-8절). 규격 파서(`_parse_tolerance`)를 고치면 기존 DB 값이
안 바뀌는지 dry-run으로 먼저 확인(4-2절). 권한 스키마 변경 시 `PERM_MIGRATION` 같은
매핑 테이블로 기존 계정 자동 이관(5절).

---

## 6. 에이전트 생태계

5개 서브에이전트 전부 `.claude/agents/*.md`에 정의(이번 조사에서 5개 파일 전체를
직접 읽었다). Claude Code(Anthropic 자체 CLI) 전용 메커니즘이라 **ChatGPT는 이
서브에이전트들을 직접 호출할 수 없다** — 이 사실 자체가 Sidekick 설계에서 가장 중요한
제약이다(15절 참고).

| 항목 | planner | developer | designer | quality-watcher | reuse-scout |
|---|---|---|---|---|---|
| 파일 | `.claude/agents/planner.md` | `.claude/agents/developer.md` | `.claude/agents/designer.md` | `.claude/agents/quality-watcher.md` | `.claude/agents/reuse-scout.md` |
| 역할 | 자연어 요청→구체적 스펙 문서 | 스펙→실제 코드 구현 | 시각적 표현(타이포·여백·색상·접근성) 개선 | 산출물 결함 검증 | 화면간 기능 불일치 탐지 |
| 도구 | Read, Glob, Grep(읽기전용) | Read, Edit, Write, Glob, Grep, Bash(제한적) | Read, Edit, Write, Glob, Grep | Read, Grep, Glob(읽기전용) | Read, Glob, Grep(읽기전용) |
| 모델 | sonnet | sonnet | sonnet | haiku | sonnet |
| preload 스킬 | writing-plans, codebase-design | systematic-debugging, verification-before-completion, codebase-design, receiving-code-review | frontend-design, frontend-design-audit | (없음) | (없음) |
| 호출 시점 | 새 기능/화면변경 요청 시 **자동 최우선** | planner 다음 단계 | developer 완료 **이후 순차만**(병행 안 함) | 코드 수정 완료 보고 **전 자동** | 사용자가 **명시적으로** 요청할 때만 |
| 호출하면 안 되는 상황 | 스펙이 이미 명확한 단순 반복작업(오타수정 등) | planner 스펙 없이 구현 착수(모호한 요청 그대로 던지기) | 개발과 병행(동시 진행 금지) | — | 사용자 요청 없이 자동 개입 |
| git commit/push | 불가(Edit/Write 자체 없음) | **불가**(권한 명시적 제한) | **불가**(권한 명시적 제한) | 불가(읽기전용) | 불가(읽기전용) |

**6-1. 어떤 에이전트가 어떤 업무를 담당하는가**: 위 표 참고.

**6-2. 업무가 겹치는 에이전트가 있는가**: 없음 — planner(기획)/developer(구현)/
designer(시각)/quality-watcher(검증)/reuse-scout(화면간 일관성)로 역할이 명확히
분리돼 있다. 단, quality-watcher와 reuse-scout는 둘 다 "문제를 찾는다"는 점에서
유사해 보일 수 있으나 quality-watcher는 **단일 산출물이 스펙을 충족하는지**,
reuse-scout는 **여러 화면 간 불일치**를 본다는 점에서 다르다.

**6-3. 호출 순서가 중요한 경우**: 있다 — designer는 반드시 developer **이후**에만
개입(병행 금지, designer.md 명시). quality-watcher는 반드시 코드 수정 **완료 후**
(11절, 13절).

**6-4. 결과를 이어받는 구조**: planner 스펙 → developer 입력. developer 산출물 →
quality-watcher 입력, 결함 있으면 developer가 다시 고침(receiving-code-review 스킬로
"무조건 순응 금지, 검증 후 판단"). developer 완료 → designer 입력(시각적 변경이 있는
경우).

**6-5. 사용자가 직접 호출해야 하는 에이전트**: reuse-scout(명시적 요청 전용).

**6-6. Claude Code가 자동으로 판단해서 호출하는 에이전트**: planner(기능/화면 변경
요청 감지 시), quality-watcher(코드 수정 완료 후), designer(developer 작업에 시각적
변경이 포함된 경우).

---

## 7. 플러그인 / 외부 도구

전역 `~/.claude/settings.json`의 `enabledPlugins`에서 확인된 5개:

| 플러그인 | 목적 | 사용 에이전트 | 사용 상황 | 실패 시 영향 | 대체 수단 |
|---|---|---|---|---|---|
| `superpowers` | 범용 개발 스킬 모음(디버깅/검증/plan 작성 등) | developer(4개 스킬 preload), 메인 세션(수동 호출) | 19절에서 13개 중 5개만 실제 적용 대상으로 선별됨 | 스킬 미적용 시 그냥 기본 판단으로 대체(치명적 아님) | 없음, 이미 최소 채택 |
| `mattpocock-skills` | `codebase-design` 등 설계 용어집 | planner, developer | 리팩터링/설계 판단 시 참고 | 없어도 기존 8-1절 원칙으로 대체 가능 | CLAUDE.md 8-1절 자체가 대체재 |
| `ponytail` | "가장 게으른(최소) 해결책" 강제 스타일 가이드 | 세션 전체(전역 모드) | 코딩 작업 전반 | 없음(스타일 지침일 뿐) | 없음 |
| `skill-creator` | 스킬 자체를 만들거나 최적화 | 필요시 수동 | 새 스킬 제작 요청 시 | 없음 | 없음 |
| `understand-anything` | 코드베이스 지식그래프 생성/조회 | **채택 안 됨** — reuse-scout에 붙이는 걸 검토했으나 Bash 의존성 문제로 보류(13절) | UNKNOWN(실사용 안 하는 걸로 보임) | N/A | N/A |

이 다섯은 **전부 Claude Code(Anthropic CLI) 전용 플러그인 시스템**이다 — MCP 서버
설정은 이번 조사에서 **발견되지 않았다**(`.claude/` 안에 `mcp.json` 등 관련 파일
없음, **UNKNOWN**: 확실히 없다고 단정하려면 더 넓은 범위 검색 필요). **ChatGPT는
이 플러그인 생태계에 접근할 수단이 전혀 없다** — 이 점이 Sidekick 설계에서 가장
근본적인 제약이다.

---

## 8. 개발 워크플로우

**8-1. 실제 사용 순서**: 사용자 요청 → (기능/화면 변경이면) planner 자동 호출 →
스펙 문서(`docs/superpowers/plans/*.md`에 저장되는 경우가 많음) → developer 구현 →
developer가 Flask test_client 등으로 자체 1차 검증 → quality-watcher 검증(결함 있으면
수정 반복) → (시각적 변경이면) designer → 메인 세션이 실제 렌더링/실행 재확인(특히
CSS/이미지 관련) → 사용자에게 보고 → 사용자 승인 시 메인 세션이 `git add`+`git commit`
→ `git push deploy main`(프로덕션 배포) → curl 등으로 배포 반영 확인.

**8-2. 작업 시작 전 반드시 확인하는 것**: `CLAUDE.md`와 `PROGRESS.md`(SessionStart
훅이 이걸 자동 상기시킨다), 관련 있으면 `docs/qms-audit-backlog.md`.

**8-3. 코드 수정 전 반드시 읽어야 하는 것**: 건드릴 파일의 관련 CLAUDE.md 절(예:
report_builder.py 이미지 배치면 7-4절/7-4-1절/7-4-2절), 8-1절의 공용 헬퍼 목록(중복
구현 방지).

**8-4. 작업 완료의 기준**: quality-watcher 결함 0건 + 실제 실행/렌더링 확인(11절) +
(있다면) 사용자가 요구한 end-to-end 시나리오 검증(10절 — 가상 데이터로 입고→검사→
승인→출력 전체를 실제로 돌려봄).

**8-5. 테스트 통과 외에 확인해야 하는 것**: CSS/애니메이션 — 실제 렌더링(헤드리스
크롬). 엑셀 이미지 배치 — 가능하면 Excel COM 실측(로컬 PC에 실제 Excel 설치돼 있음).
색상 — 16절 팔레트 규칙 준수 여부.

**8-6. `PROGRESS.md` 업데이트 시점**: 의미 있는 작업 단위가 끝날 때마다(전역 훅이
`git commit` 직후 "PROGRESS.md에 한 줄 추가할 것"을 리마인드함 — 하지만 강제는
아니고 알림뿐).

**8-7. commit 시점**: 사용자가 명시적으로 승인한 뒤(예: "커밋해줘", "커밋 및
배포해줘"). 프로젝트 설정상 `git commit`/`git push`는 "ask" — 자동 승인 모드에서도
매번 확인 절차를 거친다.

**8-8. 작업 중 문제 발생 시 처리**: `systematic-debugging` 스킬의 증거수집→패턴비교
→가설검증 단계(TDD 요구 부분은 제외, 3-5절), 안 되면 사용자에게 상황을 알리고 확인
질문.

---

## 9. 인수인계 프로토콜

**이미 존재하는 인수인계 정보가 기록되는 곳**: `PROGRESS.md`의 "최근 작업 이력"
항목 하나하나가 사실상 미니 인수인계 문서 역할을 한다 — 각 항목이 무엇을/왜/어떻게
검증했는지, 커밋 해시까지 포함해서 서술형으로 남긴다. `CLAUDE.md`는 "왜 이렇게
만들었는지"와 "겪었던 함정"을 절 단위로 누적하는 더 안정적인(자주 안 바뀌는) 지식
베이스다. `docs/qms-audit-backlog.md`는 미완료 개선항목 전용 체크리스트.
`docs/superpowers/plans/*.md`/`docs/superpowers/specs/*.md`는 개별 기능의 상세
설계·구현 스펙 아카이브.

**Claude Code 세션이 토큰 소진으로 중단됐다고 가정할 때, Sidekick이 반드시 확인해야
할 순서**(이 프로젝트에 맞게 설계):
1. `git log --oneline -10` — 마지막 커밋이 뭔지, 메시지에 작업 맥락이 있는지.
2. `git status` — 커밋 안 된 변경사항이 있는지(**있다면 매우 중요한 신호** — 작업이
   중간에 끊겼다는 뜻).
3. `PROGRESS.md` 최상단 항목 — 가장 최근에 뭘 했다고 기록돼 있는지, 그 항목이 git
   log의 최신 커밋과 시점이 맞는지.
4. `docs/qms-audit-backlog.md` — 체크되지 않은 "진행 중이었을 수도 있는" 항목이
   있는지(단, 체크 안 됨=미착수가 기본이므로 착수 여부는 PROGRESS.md로 교차 확인).
5. 커밋 안 된 변경사항이 있다면: 그 파일들이 어느 CLAUDE.md 절과 관련 있는지 찾아서
   맥락 파악, quality-watcher 상당의 자체 점검(Sidekick은 이 서브에이전트를 못 부르므로
   6절 체크리스트를 직접 수행) 후 사용자에게 "이어서 진행할지" 확인.

**형식별 인수인계 항목 설계**(현재 프로젝트에 맞춰):
- 현재 작업 / 작업 목적 → `PROGRESS.md` 최상단 항목의 첫 문장
- 현재 상태 → `git status` + `git log` 대조
- 완료된 것 / 변경된 파일 / 변경 이유 → `PROGRESS.md` 항목 본문(이 프로젝트는 각
  항목에 "무엇을 왜 어떻게 검증했는지"까지 서술형으로 적는 관례가 이미 있다)
- 아직 하지 않은 것 → `docs/qms-audit-backlog.md`의 미체크 항목, CLAUDE.md 9절
- 발견된 문제 / 확인된 사실 / 추정·가설 / 미확인 사항 → 이 문서(`docs/ai-sidekick-handoff.md`)의
  `NEEDS_CONFIRMATION`/`CONFLICT` 표기들, 그리고 각 PROGRESS.md 항목의 "교훈" 문단
- 의사결정 사항 → CLAUDE.md의 "사용자가 명시적으로 확정/되돌리지 말 것" 문구가 붙은
  부분(14절, 21절, 22절, 24절 등)
- 지켜야 할 제약 → 3절(이 문서) / CLAUDE.md 전체
- 다음 작업 / 이유 → CLAUDE.md 9절, `docs/qms-audit-backlog.md` "상" 등급
- 테스트 상태 → 정식 테스트 스위트 없음(2-6절), 마지막 작업의 실행 검증 방식은
  PROGRESS.md 해당 항목 참고
- 위험 요소 → 11절(이 문서)
- 롤백 방법 → 12절(이 문서)

---

## 10. Claude 자신의 판단과 의도

**10-1. 현재 구조를 이렇게 만든 중요한 이유**: 단일 파일(`app.py`) 모놀리식 구조는
"프로젝트 규모(1인 유지보수, 매 작업이 파일 1~7개 수준)에 블루프린트 분리 같은
아키텍처 오버헤드가 안 맞는다"는 반복된 판단(19절)에서 나온 것으로 읽힌다 — 명시적으로
"이렇게 하기로 했다"는 문장은 없지만, 19절의 스킬 채택/폐기 논리(도구 오버헤드가
실제 이득보다 크면 안 씀)가 코드 구조 전반에도 일관되게 적용된 것으로 보인다
(**추정, NEEDS_CONFIRMATION**).

**10-2. 일반적 패턴과 다르지만 의도적인 부분**: FK 없는 느슨한 DB 연결(5-4절 등
여러 곳) — "현장 데이터는 불완전할 수밖에 없고, 그걸 시스템이 거부하면 안 된다"는
철학. 정식 REST API 대신 서버렌더링+필요한 곳만 AJAX — 비개발자 사용자가 새로고침
기반 UI에 더 익숙하다는 가정(**추정**). 자동화 테스트 대신 매번 실제 실행 검증 —
빠른 반복이 중요한 내부 도구 특성상 테스트 스위트 유지비용보다 즉각 검증이 더
실용적이라는 판단으로 보임(**추정**).

**10-3. 앞으로 절대로 무심코 리팩터링하면 안 되는 부분**: 8-1절 공용 헬퍼 목록 전부.
`_final_decision_block_reason()`의 4가지 차단 조건(8-2-8절). `compute_content_hash()`의
정렬 방식(순서 안정성). 판정 색상 체계(16절 — 합격/불합격/보류는 시스템 규칙).
`_excel_col_width_to_emu()` 관련 함수들(세 번 재발한 함정, 7-4-1절).

**10-4. 과거 문제를 해결하기 위해 존재하는 구조**: `create_inspection()`의
`BEGIN IMMEDIATE`(중복 성적서 생성 사고, 8-2-9절/20절). `_place_photos_in_area()`
공용화(중복 구현 위험, 8-1절). `TwoCellAnchor` 전환(실제 엑셀에서 이미지가 셀을
꽉 채우던 사고, 7-4-2절). `hidden` 속성 대신 인라인 style(2026-09-17 실제 버그,
17절).

**10-5. 겉보기엔 이상하지만 건드리면 안 되는 것**: `@perm_required`가 **OR 조건**
이라는 것(23절에 명시적 경고, 삭제 라우트는 반드시 단독으로만 걸어야 함 — 직관적으로
"권한 여러 개 걸면 더 안전할 것 같은데 실제론 반대"). `intake_list`에 규격이 하나도
없어도 자재만 먼저 존재할 수 있다는 것(3절, "이상해 보이지만 규격 개별등록 워크플로를
위한 의도적 설계"). NCR 라우트(`/ncr/<id>/confirm`)가 어떤 화면에서도 안 불리는 고아
라우트로 남아있는 것(8-2절 — 삭제하지 않고 의도적으로 남겨둠, 재연결 가능성 열어둠).

---

## 11. 위험 영역

| 영역 | 왜 위험한가 |
|---|---|
| **DB(`iqc.db`)** | 단일 SQLite 파일. 동시쓰기 제약이 있고, 스키마 변경은 반드시 멱등 마이그레이션 패턴을 따라야 한다(안 그러면 기존 프로덕션 데이터가 깨질 수 있음). `.gitignore`돼 있어 로컬 파일을 실수로 커밋하면 실 데이터가 공개 저장소에 노출될 위험(아래 `origin`이 공개 저장소인지 여부 자체가 `UNKNOWN` — 13절 참고). |
| **인증/비밀번호** | 평문 저장이 "의도적 결정"으로 문서화돼 있지만(3절), 동시에 2026-09-17 재조사에서 로그인 무차별대입 방어 부재와 함께 재검토 대상으로 백로그에 올라가 있다(3-2절 CONFLICT 참고) — 함부로 "규칙이니 그대로 둔다"고 판단하면 안 되고, 사용자에게 현재 입장을 재확인해야 한다. |
| **`SECRET_KEY`(app.py:113)** | 환경변수 미설정 시 하드코딩된 문자열("dev-secret-change-later")로 조용히 폴백 — 세션 쿠키 위조로 관리자 권한 탈취 가능. Render 환경에 실제로 설정돼 있는지 **NEEDS_CONFIRMATION**(코드만으론 확인 불가, Render 대시보드 확인 필요). |
| **권한(permissions)** | `@perm_required`가 OR 조건이라, 삭제류 라우트에 다른 권한과 같이 걸면 의도치 않게 뚫린다(23절 명시적 경고). 새 라우트 추가 시 이 함정을 반복하기 쉽다. |
| **데이터 삭제** | `activity_log`는 절대 edit/delete UI 금지(3절). 출고 배치 삭제는 cascade 순서가 엄격하다(23절, 순서 틀리면 FK 오류 또는 고아 레코드). `finished_goods_serials`는 배치 삭제에 딸려오면 안 됨(23절, "핵심 회귀 포인트"로 명시). |
| **migration** | `init_db()` 안의 멱등 마이그레이션 패턴을 벗어나면(예: UNIQUE 제약 직접 추가) 기존 프로덕션 데이터와 충돌 가능(20절 7번에서 의도적으로 회피한 이력). |
| **production 설정(render.yaml vs Dockerfile)** | **13절/14절 CONFLICT 참고 — 이 문서에서 발견한 가장 중요한 미확인 위험.** 두 설정이 서로 다른 배포 방식(네이티브 Python vs Docker)을 전제하고 있고, 어느 쪽이 실제로 Render에서 쓰이는지 이 저장소 안의 파일만으로는 확정할 수 없다. 잘못 판단하고 배포 설정을 "정리"하면 LibreOffice가 사라져서 **PDF 출력(핵심 기능)이 프로덕션에서 통째로 깨질 수 있다.** |
| **Render / 환경변수** | `DATA_DIR`(영구디스크 마운트), `SECRET_KEY`(설정 여부 불명) 등 Render 대시보드에만 있고 이 저장소엔 없는 설정이 있다 — 대시보드를 직접 보지 않고 "코드에 없으니 없다"고 단정하면 안 된다. |
| **API 계약** | 정식 API 계약이 없다(4-6절) — JSON을 반환하는 라우트들(`/dashboard/chart-data` 등)은 사실상 그 화면의 프론트엔드 JS만을 위한 내부용이다. 외부에서 이걸 안정된 API로 오인해서 의존하면 위험. |
| **외부 서비스** | SMTP 설정(`smtp` 권한 화면) — 잘못 건드리면 NCR/개선요청서/성적표 발송이 조용히 실패할 수 있음. |
| **기존 에이전트 규칙** | developer/designer가 `git commit`/`git push`를 시도하지 못하게 막아둔 `.claude/settings.json`의 `permissions.deny`/`ask` 설정 — 이걸 느슨하게 바꾸면 서브에이전트가 사용자 확인 없이 배포할 수 있게 된다(13절의 "커밋 경계" 원칙 위반). |
| **환경변수** | `.env`는 `.gitignore`돼 있어 로컬에 실제로 존재하는지 이 조사로는 확인 못 함(`UNKNOWN`) — `SECRET_KEY` 외에 다른 민감정보(SMTP 비밀번호 등)도 환경변수로 관리되는 것으로 추정되나 전수 확인 안 함. |
| **결제/비용 관련 기능** | 이 프로젝트에는 없음(`N/A`). |

---

## 12. Git / 배포

**12-1. branch 구조**: `main`이 유일한 활성 브랜치(로컬+두 리모트 전부). **단, `session/2026-08-21`이라는
브랜치가 로컬과 `origin` 양쪽에 존재한다**(`deploy` 리모트에는 없음) — CLAUDE.md 19절은
"브랜치 전략 자체가 없다"고 명시하는데 실제로는 이 스트레이 브랜치가 하나 있다.
**CONFLICT**(14절에 정리) — **NEEDS_CONFIRMATION**: 이게 뭘 위해 만들어진 브랜치인지,
지워도 되는 건지 사용자에게 확인 필요. 함부로 지우지 말 것(git-guardrail 훅이 `git branch -D`
자체는 막아주지만, 판단은 사람이 해야 함).

**12-2. commit 규칙**: 정해진 컨벤션(Conventional Commits 등)이 강제되진 않지만,
실제 커밋 메시지는 `feat:`/`fix:`/`docs:` 접두어를 관례적으로 쓰고 있다(최근 커밋
로그 기준). 커밋 메시지 끝에 `Co-Authored-By: Claude ... <noreply@anthropic.com>`과
세션 링크를 붙이는 관례가 있다(Claude Code 세션 한정).

**12-3. 작업 단위**: 기능 하나(또는 사용자 피드백 하나) = 커밋 하나가 기본. 같은
작업이라도 파일이 여러 개면 `git apply --cached`로 hunk 단위 분리해서 논리적으로
나눈 사례도 있다(PROGRESS.md 2026-09-16 추가13).

**12-4. merge 방식**: 항상 `main` 직접 커밋(fast-forward만, 별도 PR/merge 워크플로
없음).

**12-5. 배포 방식**: `git push deploy main` → Render가 그 push를 감지해서 자동 재배포
(추정, Render의 일반적 동작 방식 — **NEEDS_CONFIRMATION**: 이 저장소 설정만으로 자동
배포 트리거 방식(웹훅 등)을 확정할 근거는 없음).

**12-6. Render와 로컬 환경의 차이**: 로컬은 `python app.py`(Flask 개발서버), 프로덕션은
`gunicorn`(워커 수는 아래 CONFLICT 참고). 로컬 `DATA_DIR`는 기본값(앱 폴더 자체),
프로덕션은 `/var/data`(영구 디스크 마운트, `render.yaml`). 로컬은 실 데이터 없음(테스트용
`iqc.db`), 프로덕션이 실 운영 데이터(`project_render_production.md` 개인 메모리 —
"실 데이터 이전 완료").

**12-7. production 반영 전 반드시 확인해야 하는 사항**: 이 프로젝트 관례상 —
quality-watcher 결함 0건, 가능하면 실제 렌더링/실행 확인, 사용자의 명시적 커밋·배포
승인. **이번 조사로 추가돼야 할 항목**: PDF 생성이 걸린 변경이라면 13절의 render.yaml/
Dockerfile 불확실성을 먼저 해소할 것.

**12-8. rollback 방법**: 문서화된 공식 절차는 **없음**(`NEEDS_CONFIRMATION`). 일반적
추정으로는 `git revert`(git-guardrail 훅이 `--hard` 리셋은 막지만 `revert`는 안 막음,
`--hard` 계열만 차단 대상) 후 `git push deploy main`으로 재배포하는 방식이 가장
안전할 것으로 보이나, **이 프로젝트에서 실제로 롤백을 해본 기록은 이번 조사 범위에서
발견되지 않았다.**

---

## 13. 현재 프로젝트의 "진실의 원천" — 그리고 이번에 발견한 최대 CONFLICT

**우선순위(이 프로젝트에서 확인 가능한 근거로 정리)**:
1. **실제 코드** — CLAUDE.md/PROGRESS.md 자체가 여러 차례 "문서보다 코드·실측이
   우선"이라고 명시한다(7-4-1절 "이론적으로 맞는 반박이 실측과 다를 수 있다",
   14절 "사용자가 지금 다르게 지시하면 그 지시가 우선", 20절 "문서를 믿지 않고
   실제 코드로 확인"). **가장 신뢰도 높은 원천은 실제 코드 + 실제 실행/렌더링
   결과다.**
2. **사용자의 최신 직접 지시** — CLAUDE.md의 여러 절이 "예전엔 이랬는데 사용자가
   지금 다르게 지시해서 뒤집었다"는 패턴을 반복적으로 기록한다(14절, 21절, 22절,
   24절). 즉 **CLAUDE.md 안에서도 최신 절이 옛 절을 이긴다**, 그리고 **사용자의
   지금 지시가 CLAUDE.md에 "확정"이라고 적힌 것보다도 우선**한다.
3. **`CLAUDE.md`** — 도메인 지식·함정·확정된 설계 결정의 정본. 단, 날짜가 오래된
   절(예: 1절의 "시놀로지 NAS 배포 목표")은 이후 절/PROGRESS.md에 의해 사실상
   대체됐을 수 있다 — **날짜가 더 최신인 절/기록이 우선**이라고 보는 게 이 문서
   자체의 관례와 일치한다.
4. **`PROGRESS.md`** — "무엇을 했는지"의 시간순 기록. CLAUDE.md와 내용이 겹치면
   PROGRESS.md가 더 최신 사실을 담고 있을 가능성이 높다(CLAUDE.md는 안정적 지식,
   PROGRESS.md는 매번 갱신됨 — 문서 자체의 1~6줄에 이 구분이 명시돼 있다).
5. **`README.txt`** — 비개발자 사용자용 운영 안내서. 기능 설명은 CLAUDE.md/실제
   코드와 일치해야 하지만, **"기능 추가 시 이것도 갱신할 것"이라고만 돼 있지 실제로
   매번 갱신됐는지는 이번 조사에서 확인하지 않았다**(`NEEDS_CONFIRMATION`).
6. **개인 auto-memory**(`project_dev_roadmap.md` 등) — 이건 **이 저장소 밖**,
   Claude Code 사용자 계정에 귀속된 정보다. ChatGPT는 물리적으로 접근 불가능하고,
   CLAUDE.md 13절도 "이 프로젝트 코드/설계 관련 사실은 CLAUDE.md에 적고, 개인
   메모리는 '사용자에 대한' 교훈에만 쓴다"고 역할을 분리해뒀다 — **즉 프로젝트
   진실의 원천 순위에 개인 메모리는 원래 안 들어간다.** 이 문서에서 조직 구조 등을
   언급할 때 "참고 수준"이라고 명시한 이유가 이것이다.
7. **과거 대화(claude.ai 채팅)** — CLAUDE.md 자체가 "이 대화 기록에 Claude Code는
   접근할 수 없다"고 명시한다(문서 맨 앞). 즉 이 프로젝트의 공식 진실 원천 목록에
   포함되지 않는다 — 과거 대화의 내용이 필요하면 전부 CLAUDE.md로 옮겨 적혀 있어야
   한다는 게 이 문서의 존재 이유 자체다.

**이번 조사에서 발견한 최대 CONFLICT — production 배포 방식**:

```
DOCUMENT SAYS (render.yaml):
  runtime: python
  buildCommand: pip install -r requirements.txt
  startCommand: gunicorn app:app --timeout 600 --workers 1
  (Docker 관련 언급 전혀 없음)

ACTUAL CODE (Dockerfile, 파일 맨 위 주석):
  "Render 네이티브(비-Docker) 파이썬 환경은 apt 시스템 패키지를 설치할 수 없어서
   LibreOffice(soffice)를 넣을 수 없다 — xlsx→PDF 변환에 LibreOffice가 반드시
   필요하므로 Docker로 전환해서 apt로 직접 설치한다."
  FROM python:3.11-slim-bookworm
  RUN apt-get install -y libreoffice-calc fonts-nanum
  CMD gunicorn -w 2 -t 180 -b 0.0.0.0:$PORT app:app

CONFLICT:
  두 파일이 같은 저장소에 공존하는데 서로 다른 배포 경로를 전제한다. render.yaml대로
  Render가 "네이티브 Python 런타임"으로 이 서비스를 인식하고 있다면, Dockerfile의
  존재 이유(apt로 LibreOffice 설치)가 애초에 작동할 수 없다 — 즉 프로덕션에
  LibreOffice가 없을 수 있고, 그러면 이 시스템의 핵심 기능인 "성적서 xlsx→PDF 출력"이
  프로덕션에서 실패하고 있을 가능성이 있다. 반대로 실제 Render 서비스가 대시보드에서
  "Docker" 런타임으로 설정돼 있고 render.yaml은 (Render가 서비스 생성 이후엔 일부
  설정을 무시하거나, 이 파일 자체가 쓰이지 않는 구성이라면) 참고용으로만 남아있는
  것일 수도 있다. worker 수(1 vs 2)와 timeout(600s vs 180s)도 두 파일이 다르게
  적어놨다.
  이 저장소 안의 파일만으로는 어느 쪽이 실제로 작동 중인지 확정할 수 없다 —
  **Render 대시보드에서 이 서비스의 "Runtime"이 Python인지 Docker인지 직접 확인이
  필요하다(NEEDS_CONFIRMATION, 최우선).**
```

이 CONFLICT는 CLAUDE.md/PROGRESS.md 어디에도 언급되지 않은, 이번 조사에서 처음
발견한 사항이다. **Sidekick이 이 프로젝트에 투입되면 가장 먼저 확인해야 할 사실
중 하나로 취급할 것을 권한다.**

**그 외 발견한 DOCUMENT SAYS / ACTUAL CODE 대조**:

```
DOCUMENT SAYS (CLAUDE.md 0절, 1절):
  "최종 배포 목표는 시놀로지 DS923+ NAS(Web Station)."

ACTUAL 상태 (개인 메모리 project_render_production.md + render.yaml/Procfile 실존):
  Render가 정식 운영 서버로 이미 실 데이터까지 이전 완료된 상태.

CONFLICT:
  CLAUDE.md 1절이 실제 배포 상태를 반영하지 못하고 있다(작성 시점 이후 배포 전략이
  Render로 바뀐 것으로 보이나 그 절 자체는 갱신 안 됨). 프로젝트 파일(render.yaml,
  Procfile, Dockerfile)이 실제 사실을 더 정확히 반영한다 — **문서(CLAUDE.md 1절)가
  낡은 케이스.**
```

```
DOCUMENT SAYS (CLAUDE.md 5절 제목/표):
  "권한 시스템 (2026-08-22 기능별 20개로 세분화됨)"
  20개 권한 표 나열

ACTUAL CODE (app.py, PERM_GROUPS 실제 정의):
  8개 그룹, 실제 항목 수를 세면 28개(입고1+검사13+승인2+출고2+출력2+자재3+마스터2+관리3)

CONFLICT:
  2026-09-15~17 사이 NCR/개선요청서/커스텀템플릿/출고삭제 등 8개 권한이 추가됐는데
  CLAUDE.md 5절 제목·표는 갱신 안 됨. 이미 `docs/qms-audit-backlog.md`에 "상" 등급
  개선항목으로 등재돼 있으나(2026-09-17 발견), 이번 조사 시점까지 미착수.
```

---

## 14. 현재 상태의 객관적 검증

13절에 두 개의 핵심 CONFLICT를 이미 정리했다. 그 외 이번 조사에서 실제 코드 대조로
확인한 것들:

```
DOCUMENT SAYS (PROGRESS.md, 여러 항목):
  "quality-watcher 검증 통과", "실제 렌더링 확인" 등 검증 완료 서술

ACTUAL CODE:
  이번 조사에서 최근 커밋(우선검사 플래그 관련) 코드를 직접 열어 대조한 결과,
  서술된 구현 내용(is_priority 컬럼, toggle 라우트, 권한 게이트, 배지 표시 로직)이
  실제 코드와 정확히 일치했다.

CONFLICT: 없음 — 이 범위는 문서와 코드가 일치한다.
```

```
DOCUMENT SAYS (구두 확인 안 됨, 그냥 requirements.txt에 있음):
  APScheduler가 의존성 목록에 있음

ACTUAL 실행 결과 (이번 조사 중 test_client 실행 시 관측):
  "WARNING in app: ... No module named 'apscheduler'" 경고가 매번 출력됨

CONFLICT(엄밀히는 문서 대 문서 충돌이 아니라 선언된 의존성 대 로컬 실행환경의 불일치):
  로컬 개발환경에 APScheduler가 실제로 설치돼 있지 않다. 이게 로컬만의 문제인지,
  이 라이브러리를 쓰는 기능이 뭔지, 프로덕션에도 같은 문제가 있는지는 이번 조사
  범위에서 추적하지 않았다 — NEEDS_CONFIRMATION.
```

```
DOCUMENT SAYS (docs/qms-audit-backlog.md, "확인 필요" 항목):
  "backups/ 폴더가 Render의 영구 디스크에 있는지 확인 안 됨"

ACTUAL CODE (database.py:11-13, app.py:125, render.yaml):
  DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
  BACKUP_DIR = os.path.join(db.DATA_DIR, "backups")
  render.yaml: DATA_DIR=/var/data, disk 마운트(1GB, name: iqc-data)

CONFLICT: 없음 — 오히려 이번 조사로 백로그의 "확인 필요" 항목 하나가 해소됐다.
  backups/·iqc.db·서명·사진·성적서 발행 폴더 전부 DATA_DIR 하위이므로, render.yaml의
  영구 디스크 설정이 실제로 적용되고 있다면(13절의 Docker/Python 런타임 불확실성과는
  별개로) 이 데이터들은 재배포에도 유실되지 않는다. **다만 이 결론도 13절의 CONFLICT가
  해소돼야("render.yaml이 실제로 쓰이는 설정이 맞다"가 확인돼야) 완전히 신뢰할 수 있다**
  — render.yaml 자체가 지금 활성 설정인지 자체가 불확실하기 때문.
  이 문서를 읽는 사람은 `docs/qms-audit-backlog.md`의 해당 "확인 필요" 항목을 이
  내용으로 갱신하는 걸 고려할 것(이번 작업 범위에서는 코드/문서를 수정하지 않기로
  했으므로 이 문서에만 기록해둔다).
```

---

## 15. Sidekick에게 필요한 최종 브리핑

### 이 프로젝트에서 내가(Sidekick) 반드시 알아야 할 것 20가지

1. 회사는 자동차 부품이 아니라 **리클로저(배전용 전력기기) 제조업체**다(2026-09-17
   정정, 예전 문서 추정이 틀렸었다).
2. 사용자는 코딩 비전문가다 — 기술 설명은 항상 쉬운 말로.
3. "~해줘"는 말버릇, 실제 코드 작업은 명시적 확인 후에만(10절).
4. 판정 로직에서 규격 하한/상한이 `None`인 케이스는 반드시 테스트에 포함(4절).
5. 규격이 아예 안 채워진 항목(`NO_SPEC_RESULT`)은 불합격이 아니라 "검토필요"다 —
   우리 데이터 누락을 협력사 책임으로 만들면 안 된다(4-1절).
6. `template_form.xlsx`/`standard_template.xlsx` 원본은 절대 직접 저장하지 않는다.
7. 공용 헬퍼(8-1절 표) 절대 재구현 금지.
8. 엑셀 이미지 배치는 `_excel_col_width_to_emu()` 기본, `7*9525` 복붙 금지(세 번
   재발한 함정).
9. CSS/애니메이션/엑셀이미지 변경은 코드리뷰만으로 "완료" 선언 금지 — 실제 렌더링/
   Excel COM 확인 필수(11절).
10. `hidden` HTML 속성은 `.badge` 같은 클래스가 `display`를 지정하면 무시된다 —
    인라인 `style="display:none"` 써야 한다(17절, 2026-09-17 실사고).
11. `@perm_required`는 **OR 조건**이다 — 삭제 라우트엔 절대 다른 권한과 같이 걸지
    말 것(23절).
12. 최종 결정 3종(승인/특채/불합격)은 서명 필수 + `_final_decision_block_reason()`
    게이트, 반려만 예외.
13. "관리자 전용" = `username=="admin"` 계정 하나를 뜻하지, 세분화 권한이 아니다
    (14절, 21절, 22절 반복 확인).
14. 색상 팔레트 리브랜딩은 2026-09-08에 마감 확정 — 추가 요청 없이 남은 색 건드리지
    말 것(16절). 새 UI 요소 색을 고를 땐 "예약색과 안 겹치는가"뿐 아니라 "채도가
    낮은가"도 같이 체크(2026-09-17 교훈).
15. `docs/qms-audit-backlog.md`는 살아있는 체크리스트다 — 항목 완료 시 체크+커밋
    해시 기록, 새 발견사항도 여기 누적.
16. 브랜치 전략이 없다 — 항상 `main` 직접 커밋, `git push deploy main`으로만 배포
    (`origin`은 배포와 무관).
17. developer/designer 서브에이전트는 commit/push 권한이 없다 — 항상 사람(메인
    세션)이 확인 후 커밋한다.
18. 정식 테스트 스위트가 없다 — 1회성 검증 스크립트 + 실제 실행 확인이 이 프로젝트의
    "테스트"다.
19. **render.yaml과 Dockerfile이 서로 다른 배포 경로를 전제한다** — 이 저장소 파일만
    으론 프로덕션에 LibreOffice가 실제로 있는지 확정 불가(13절 CONFLICT, 최우선
    확인 사항).
20. "확정/되돌리지 말 것"이라고 문서에 적혀 있어도, **사용자가 지금 다르게 말하면
    그 지시가 우선**이다 — 다만 그럴 땐 문서도 같이 갱신해야 다음 세션이 안 헷갈린다
    (14절의 일반 교훈, 이 프로젝트에서 가장 자주 반복되는 메타 원칙).

### Sidekick이 절대로 해서는 안 되는 행동
- `git push --force`, `git reset --hard`, `git branch -D` 등 파괴적 git 명령
  (Claude Code 세션에선 전역 훅이 하드 차단하지만, ChatGPT 환경엔 이 훅이 없을 수
  있으므로 **Sidekick 스스로 이 명령들을 시도하지 않아야 한다** — 훅에 의존하지 말 것).
- 사용자 확인 없이 `git commit`/`git push deploy main` 실행(13절 "커밋 경계" 원칙,
  ChatGPT에도 동일하게 적용돼야 함).
- `iqc.db`, `backups/`, `static/signatures/`, `static/ncr_photos/` 등 실데이터/개인정보
  포함 디렉터리를 커밋하거나 외부로 유출.
- 판정 로직(`judge_numeric` 등), 최종결정 게이트(`_final_decision_block_reason`),
  무결성 해시 로직을 "리팩터링"이라는 명목으로 단순화.
- `template_form.xlsx`/`standard_template.xlsx` 원본 파일을 열어서 직접 저장.
- CLAUDE.md에 "확정"이라고 적힌 걸 그 문서만 보고 임의로 되돌리기(사용자 재확인
  없이).
- render.yaml이나 Dockerfile 중 하나를 "정리 차원에서" 삭제(13절 CONFLICT가
  해소되기 전까지는 둘 다 그대로 둘 것 — 잘못 지우면 프로덕션 PDF 기능이
  깨질 수 있음).

### Sidekick이 Claude Code에게 작업을 넘겨야 하는 상황
- `.claude/agents/*.md` 서브에이전트(planner/developer/designer/quality-watcher/
  reuse-scout) 실행이 필요한 작업 — ChatGPT는 이걸 물리적으로 호출할 수 없다.
- 헤드리스 크롬 스크린샷, Excel COM 자동화 등 **이 PC의 로컬 환경**(Windows,
  설치된 Chrome/Excel/LibreOffice)에 의존하는 검증이 필요한 작업.
- CLAUDE.md/PROGRESS.md/`docs/qms-audit-backlog.md` 자체의 대규모 구조 개편
  (이 문서 체계 자체가 Claude Code 세션들의 누적 관례이므로, 구조를 바꾸려면
  그 맥락을 아는 쪽이 낫다).

### Claude Code가 Sidekick에게 인수인계해야 하는 상황
- (역방향 시나리오) Claude Code의 토큰이 소진돼 세션이 끊기고, 사용자가 급하게
  ChatGPT로 작업을 이어가야 할 때 — 이 문서(`docs/ai-sidekick-handoff.md`) +
  최신 `PROGRESS.md`/`git log`를 Sidekick에게 넘기면 된다.
- 서브에이전트 실행이 필요 없는 단순 조사/설명/문서 작업이라면 Sidekick이 맡아도
  이 프로젝트의 품질 기준(11절)에 지장이 없다.

### 사용자에게 반드시 확인받아야 하는 상황
- **render.yaml vs Dockerfile 중 어느 쪽이 실제로 Render에서 쓰이는지**(13절,
  이 문서에서 발견한 최우선 확인 필요 사항).
- `SECRET_KEY` 환경변수가 Render에 실제로 설정돼 있는지.
- 비밀번호 평문 저장 정책을 계속 유지할지(3-2절, "절대 수정 금지"와 "재검토 대상"이
  공존하는 상태).
- `session/2026-08-21` 브랜치를 정리해도 되는지.
- `docs/qms-audit-backlog.md`의 "상" 등급 항목 중 무엇부터 착수할지(2-8/2-9절).
- CLAUDE.md의 "확정/되돌리지 말 것" 문구와 다른 방향으로 진행하고 싶을 때는 항상.
- git commit/push 실행 직전(이 프로젝트의 확립된 관례).

---

## 16. Sidekick이 이해하지 못할 수 있는 부분 — 사용자에게 필요한 질문

**Q1. Render 대시보드에서 이 서비스(`chardon-qms`)의 Runtime이 실제로 "Python"인가,
아니면 "Docker"인가?**
왜 필요한가: 13절/14절의 최대 CONFLICT를 해소하는 유일한 방법이다. 이 저장소 안의
파일(`render.yaml` vs `Dockerfile`)만으로는 답이 안 나온다. 답에 따라 "프로덕션에
LibreOffice가 있는가"(=PDF 출력 핵심기능이 실제로 작동하는가)가 결정된다.

**Q2. `SECRET_KEY` 환경변수를 Render에 실제로 설정해두셨는가?**
왜 필요한가: 코드(app.py:113)는 안 하면 하드코딩된 값으로 조용히 폴백한다. 이건
세션 위조로 이어질 수 있는 보안 이슈인데, 실제 배포 환경 설정은 이 저장소로는
확인이 안 된다.

**Q3. 비밀번호 평문 저장 정책을 계속 유지하실 건가, 아니면 이번에 재검토하고
싶으신가?**
왜 필요한가: CLAUDE.md엔 "절대 수정 금지"로 적혀 있는데, 같은 프로젝트의 2026-09-17
백로그엔 재검토 후보로 올라가 있다 — 두 기록이 서로 다른 방향을 가리키고 있어서,
Sidekick이든 Claude Code든 이 결정을 다시 마음대로 내리면 안 된다.

**Q4. `session/2026-08-21`이라는 git 브랜치는 어떤 용도였는가? 지금도 필요한가?**
왜 필요한가: CLAUDE.md는 "브랜치 전략이 없다"고 단언하는데 실제로는 이 브랜치가
존재한다. 실수로 만들어진 것인지, 특정 시점 백업용인지에 따라 처리(유지/정리)가
달라진다.

**Q5. `origin`(`QC-SYSTEM` 저장소)과 `deploy`(`Chardon_QMS_BYjh` 저장소)는 각각
공개(public) 저장소인가, 비공개(private)인가?**
왜 필요한가: `SECRET_KEY` 하드코딩 폴백 값이나 `iqc.db`가 혹시라도 실수로 커밋된
이력이 있다면, 공개 저장소 여부에 따라 위험도가 완전히 달라진다(개인 메모리에
"저작권 보호 조치"라는 항목이 있는 걸로 봐서 이미 이 주제를 신경 쓰고 계신 것
같은데, 이 저장소 파일만으론 공개/비공개 상태 자체를 확인할 수 없었다).

**Q6. `docs/qms-audit-backlog.md`의 "상" 등급 항목(로트역추적/CAPA폐루프/보안 3건)
중 다음에 착수할 순서를 정해두셨는가, 아니면 그때그때 말씀하실 건가?**
왜 필요한가: Sidekick이 먼저 투입돼서 "뭐부터 할까요"라고 물어야 할지, 아니면
사용자가 먼저 지정해줄 때까지 대기해야 할지가 달라진다 — 이전 세션에서 이 항목들에
대해 "내가 나중에 먼저 말 걸게"라고 명시적으로 보류하신 적이 있다(대기가 기본값일
가능성이 높다는 뜻).

**Q7. ChatGPT(Sidekick)에게 이 저장소에 대한 어느 수준의 접근 권한을 줄 계획인가
(로컬 파일 읽기/쓰기, git 조작, 실제 배포 실행 등)?**
왜 필요한가: 이 문서의 "절대 금지 행동"/"사용자 확인 필요" 목록은 Claude Code의
현재 권한 구조(서브에이전트 커밋 금지, 전역 git-guardrail 훅 등)를 전제로 짠 것이다.
ChatGPT 쪽 실행 환경이 이런 안전장치 없이 더 넓은 권한(예: 직접 셸 접근, 자동 배포)을
갖는다면, 이 문서에 없는 새로운 위험 영역이 생길 수 있다 — 그 권한 범위를 먼저
정의해야 "Sidekick이 뭘 해도 되는지"를 정확히 설계할 수 있다.
