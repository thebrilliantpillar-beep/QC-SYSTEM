# IQC 입고검사 성적서 자동화 시스템 — 작업 인수인계 (CLAUDE.md)

이 문서는 claude.ai 채팅으로 이 프로젝트를 처음부터 함께 만들어온 이전 세션의 전체
맥락을 정리한 것입니다. Claude Code는 이 대화 기록에 접근할 수 없으므로, 코드만 보고는
알 수 없는 "왜 이렇게 만들었는지"와 "이미 겪었던 함정들"을 여기에 최대한 자세히 적어둡니다.
작업을 시작하기 전에 이 파일 전체를 읽어주세요.

## 0. 이 프로젝트가 뭔지 한 줄 요약

샤든그룹(자동차 부품 제조업체 추정)의 **입고 자재 검사 성적서**를 수기 작성에서
자동화로 전환하는 Flask+SQLite 웹앱. 안드로이드 태블릿으로 접속해서 측정값만 입력하면
판정→승인→PDF까지 자동으로 나온다. 검사자는 프로그래밍 경험이 거의 없는 품질관리 담당자
(파이썬·VBA 기초만 앎, JS·서버·DB는 미경험)이므로, **모든 걸 웹 UI로 클릭해서 할 수 있게**
만드는 게 원칙이다. 최종 배포 목표는 시놀로지 DS923+ NAS(Web Station).

## 1. 실행 방법

```bash
cd iqc-app
pip install flask openpyxl pillow --break-system-packages
python app.py
```

브라우저(또는 태블릿)로 `http://<이 PC의 IP>:5000` 접속. 기본 계정 `admin` / `admin1234`.
PDF 변환에는 **LibreOffice(soffice)가 설치돼 있어야 함** — `report_builder.py`가 PATH에서
자동 탐색하고, 없으면 흔한 Windows 설치 경로도 뒤진다(`_find_soffice()` 참고).

## 2. 파일 구조

```
iqc-app/
├── app.py                 # Flask 라우트 전부, 판정 로직, 권한 데코레이터
├── database.py             # SQLite 스키마+CRUD (DB 파일: iqc.db, 최초 실행 시 자동 생성)
├── report_builder.py       # xlsx/PDF 생성 엔진 — 성적서+기준서+로고 전부 여기서
├── spec_import.py          # 규격 일괄업로드 파서 (엑셀→규격표 자동 추출)
├── judge.py                # 초기 프로토타입 잔재, 지금은 안 씀(실제 판정은 app.py의 judge_numeric)
├── template_form.xlsx      # 성적서 원본 양식 (문서번호 XD-P-01901A) — 절대 이 파일 자체를
│                            # 수정하지 말 것, 매번 shutil.copy로 복사해서 채움
├── standard_template.xlsx  # 기준서(SAM 양식) 원본 — 마찬가지로 복사해서 씀
├── logo.png                 # 회사 로고(CHARDON GROUP), 성적서·기준서 양쪽에 삽입
├── static/signatures/       # 승인 서명 이미지 저장 위치
├── templates/                # Jinja2 템플릿 (아래 8절 목록 참고)
├── 성적서 발행/               # (자동 생성) 완성된 성적서 저장 위치 — 아래 6절 참고
├── backups/                  # (자동 생성) 매 변경마다 DB 스냅샷 백업
└── README.txt                # 사용자(비개발자)용 운영 안내서 — 기능 추가 시 이것도 갱신할 것
```

## 3. DB 스키마 요약 (database.py)

- `materials` — 자재 마스터. `material_no`(PK), `material_name`,
  `drawing_version`/`revision_date`/`edition`/`unit`(기준서용 메타정보).
  **항목이 하나도 없어도 자재만 먼저 존재할 수 있음**(규격 개별등록 대응).
- `specs` — 자재별 검사항목. `material_no`, `item_name`(A,B,C...), `spec_display`(표기 텍스트),
  `judge_type`('numeric'/'visual'/'ok_ng'), `lower_limit`/`upper_limit`(None 허용 — 단측 표기
  대응), `inspect_method`, `aql`(숫자 또는 "전수"/"퍼센트N" 문자열), `item_order`.
- `intake_list` — 입고 리스트. `status`('대기'/'검사완료').
- `inspections` — 성적서 헤더. `status`('pending'/'approved'/'rejected'/'superseded'),
  `approval_type`('normal'/'special'), `remark_inspector`/`remark_manager`/`remark_approver`
  (비고 3칸), `pdf_path`(승인 시점엔 비어있다가 출력 시점에 채워짐).
- `inspection_items` — 항목별 측정값. `part_material_no`(조립품 그룹 검사 시 어느 부품
  소속인지), `measured_value`(콤마구분 문자열), `result`, `max_value`, `min_value`,
  `gauge_expiry`.
- `material_groups` / `material_group_items` — 조립품(그룹) 관리.
- `users` — 계정. `permissions`(콤마구분 텍스트, 아래 5절 참고). 비밀번호 **평문 저장**
  (내부 전용 시스템이라 의도적 결정, 바꾸지 말 것 — 대신 admin 화면에서 관리자가 언제든
  확인/초기화 가능).
- `activity_log` — 감사 로그. **의도적으로 수정·삭제 기능을 안 만듦** — 감사 기록의
  신뢰성을 위해서다. 이후에도 이 테이블에는 edit/delete UI를 추가하지 말 것.
- `spec_review_flags` — 규격 일괄업로드 시 자동인식 실패한 항목을 영구 저장해서
  "확인 필요 자재" 화면에서 나중에 처리할 수 있게 함.

## 4. 판정 로직의 핵심 함정 (반드시 알아야 함)

`app.py`의 `judge_numeric()`과 `report_builder.py`의 빨간색 강조 로직에서 **한 번 실제
버그가 났던 부분**이다:

```python
# 틀렸던 버전 — 하한/상한 중 하나라도 None이면 무조건 불합격
ok = all(lower is not None and upper is not None and lower <= v <= upper for v in values)
```

`spec_import.py`가 "OO 이상"/"OO 이하" 같은 **단측 표기**(예: "백색 아연도금 5㎛ 이상" →
`lower=5, upper=None`)를 지원하게 되면서, 위 코드는 그런 항목을 항상 불합격 처리해버리는
치명적 버그였다. 지금은 고쳐져 있다:

```python
def _within(v):
    if lower is not None and v < lower:
        return False
    if upper is not None and v > upper:
        return False
    return True
```

**앞으로 판정 로직을 건드릴 일이 있으면 반드시 하한 또는 상한이 `None`인 케이스를
테스트에 포함시킬 것.** `report_builder.py`의 측정값 빨간색 강조 로직도 동일한 패턴으로
되어 있다.

### 4-1. 하한·상한이 "둘 다" 없을 때 (2026-08-23 변경)

예전엔 둘 다 None이면 `False`(=불합격)를 돌려줬다. 그런데 규격이 안 채워진 건
**제품 불량이 아니라 우리 데이터 누락**인데 불합격으로 처리하면 부적합 통보서가
협력사로 나가버린다. 실제로 381건이 이 상태였다(대부분 "도통검사 테스트" 같은
애초에 숫자가 아닌 항목).

지금은 `judge_numeric()`이 `NO_SPEC_RESULT`(= `"규격미입력"`)을 돌려준다:
- 합격이 아니므로 성적서 전체는 `검토필요`가 된다
- 불합격이 아니므로 NCR 대상 항목에서 제외된다 (`ncr_form` 필터 참고)
- **승인/특채 라우트가 이 항목이 있으면 승인 자체를 막는다** — 판정 근거 없는 성적서 발행 방지
- 검사 입력 화면에도 측정 전에 경고 배너가 뜬다 (`build_specs_with_sample()`의 `no_limit`)

숫자로 잴 수 없는 항목은 `judge_type='ok_ng'`(적합/부적합)로 등록하는 게 맞다.

### 4-2. 규격 표기 파서 (`spec_import._parse_tolerance`) — 실제로 났던 오판정

엑셀 표기에서 하한/상한을 뽑는 함수. 아래는 **전부 실측이 무조건 불합격되던 실제 버그**였다:

| 표기 | 예전 결과 | 지금 |
|---|---|---|
| `109 (-0.2~0.1)` | `[0.1, 0.2]` | `[108.8, 109.1]` |
| `Ø90 (+0.1~0.3)` | `[0.1, 0.3]` | `[90.1, 90.3]` |
| `37 +0.1~0.05` | `[0.05, 0.1]` | `[37.05, 37.1]` |
| `265 ±5%` | `[260, 270]` (%무시) | `[251.75, 278.25]` |
| `282 – 0.5` | 인식 실패 | `[281.5, 282]` (EN DASH) |
| `115 + 2` | 인식 실패 | `[115, 117]` (상한측 단측) |
| `4Ω ±10%` | 인식 실패 | `[3.6, 4.4]` (단위 낀 ±) |

주의할 점:
- **오프셋 범위 vs 절대 범위 구분**은 "부호가 하나라도 붙어 있는가"로 한다.
  `(17.1~20.9)`는 절대범위, `109 (-0.2~0.1)`은 기준값+공차.
- `normalize_spec_text()`가 EN DASH·전각물결·엑셀 `_x000D_` 잔재를 먼저 정리한다.
- 단위 문자는 나열하지 말 것 — Ω만 해도 U+03A9/U+2126 두 종류가 데이터에 섞여 있다.
  `_UNIT = r"(?:[^\d\s±~()%+\-]{1,3})?"` 처럼 "연산자가 아닌 아무 문자"로 잡는다.
- **파서를 고치면 반드시 기존 DB 값이 안 바뀌는지 먼저 확인(dry-run)하고 적용할 것.**

## 5. 권한 시스템 (2026-08-22 기능별 20개로 세분화됨)

`users.permissions`에 콤마구분 문자열로 저장. **예전 8개(intake/spec/inspect/inspect_all/
approve/output/users/logs)에서 기능별 20개로 세분화**했다. `app.py`의 `PERM_GROUPS`가 원본
정의(그룹→항목)이고 `PERM_LABELS`/`ALL_PERMS`는 여기서 파생된다.

```
입고:  intake
검사:  inspect_input / inspect_edit_all / inspect_history / history_delete /
       defect_history / ncr / ncr_confirm / return
승인:  approve / approve_revoke
출력:  output
자재:  material_view / material_edit / material_import
마스터: gauge / supplier
관리:  users / smtp / logs
```

- `inspect_input` — 검사 입력, **본인 성적서만 수정**. `inspect_edit_all`이 있으면 타인 것도 수정
  (`_can_edit_inspection()` 참고).
- `ncr` — 부적합 통보서 **작성**(검사자), `ncr_confirm` — **확인·발송**(관리자).
- `approve_revoke` — 승인/반려/불합격 **결정 회수**(연결된 NCR·반품 있으면 회수 차단).
- `history_delete` — 검사 이력 삭제(수정모드).
- `material_view/edit/import` — 자재(예전 '규격') 열람/등록·수정/일괄등록.
- `users` 보유자만 10분 무동작 자동로그아웃(나머지 24시간).

**마이그레이션**: `ensure_perm_migration()`이 `PERM_MIGRATION` 매핑으로 옛 8권한→새 20권한을
1회 변환(`settings.perm_schema_version`='2' 플래그로 멱등). 시작 시 `__main__`에서 호출.
새 라우트를 추가하면 `@perm_required(...)`에 위 세분화 권한명을 써야 한다.

**계정 권한 UI**: 계정 목록(`/users`, users.html)은 목록만 보여주고, 각 계정의
**상세페이지(`/users/<id>`, user_detail.html)**에서 그룹별 체크박스로 권한을 설정한다.
권한/프로필/비번/삭제 액션은 전부 `user_management` 라우트로 POST(`return_to=detail`로 상세
복귀). 본인 'users' 권한은 스스로 회수 못 함.

### 5-0. 조립품(MA 등) 자동 전개 — 2026-08-23 재구성

**입고 화면에서 파츠 자재번호 하나만 넣으면 그 조립품의 파츠 전체가 펼쳐진다.**
(조립품 대표번호가 아니라 **파츠 번호로 역검색**한다 — 사용자가 명시적으로 정한 방향)

- 기준 데이터: `assembly_masters` / `assembly_components` 테이블 (**DB에서 읽는다**)
- 진입점: `db.get_ma_by_component(component_no)` → `{"ma_master", "components"}` 또는 None
  - 한 번호가 **여러 조립품에 걸쳐 있으면 None**을 돌려 일반 자재로 처리한다.
    엉뚱한 조립품으로 8줄이 튀어나오는 것보다 안전하기 때문.
- 입고 폼의 `expand_assembly` 체크박스로 전개 여부를 고른다.
  **파츠 하나만 스페어로 받을 땐 꺼야 한다**(체크 끄면 입력한 번호만 등록).
- 관리 화면: `/assemblies` (조립품 목록) · `/assemblies/<id>` (파츠 편집, 줄바꿈/쉼표 붙여넣기)
  → MA가 아닌 조립품도 여기서 직접 등록한다.
- MA 엑셀 일괄 임포트: `/admin/import-assembly` → `db.import_assembly_from_excel()`

**MA 자동출력.xlsm의 DATABASE 시트 구조 (반드시 알아야 함)**:
```
A1 = MA명          B1~B8 = 그 MA의 파츠 자재번호
D1 = MA명          E1~E8 = 파츠
G1 / H,  J1 / K,  M1 / N,  P1 / Q,  S1 / T,  V1 / W,  Y1 / Z,  AB1 / AC   (3칸 간격)
```
- **파츠 번호는 오른쪽 열(B/E/H...)에 있다.** A/D/G 열에 있는 `7322`·`7311` 같은 값은
  사내 약칭이라 자재 마스터에 없다 — 예전 구현이 이걸 파츠번호로 읽어서 검사가 아예 안 됐다.
- 이 파츠 번호는 `자동출력/성적서/MA_성적서_최종.xlsx`의 **시트명과 1:1로 같고**,
  각 시트 `A4` 셀의 `"품명 및 규격：XXX"` 에서 XXX가 제품명이다.
  → **`품명 및 규격：` 접두어를 반드시 떼고 저장할 것.** 안 떼면 성적서 제목과
    **PDF 파일명까지** `..._품명 및 규격：MAGNETIC HOUSING...pdf` 로 나간다(실제로 났던 사고).

### 5-1. 제거된 기능 (2026-08-22)
- **자재 그룹(조립품)**: 관리 UI/라우트/템플릿(groups.html·group_detail.html) 완전 삭제.
  `_get_specs_for_material()`은 항상 `(specs, False, None)` 반환(그룹 미감지). `build_group_report`는
  report_builder.py에 남아있지만 호출 안 됨. DB 테이블(material_groups 등)은 빈 채로 잔존.
- **확인 필요 자재(spec_review)**: 라우트/템플릿(spec_review.html) 삭제, spec_import의 review_flag
  기록도 제거. spec_review_flags 테이블은 잔존하지만 참조 안 함.
- 검사입력 폼의 계측기 드롭다운은 선택 시 **계측기 이름**도 저장한다(inspection_items.gauge_name).
  성적서 상세의 '계측기 유효기간 임박'은 `gauge_name`(없으면 item_name) + 남은/경과 일수만 표시.
  계측기 이름을 남기려면 '계측기 관리'에 마스터를 등록하고 검사 시 드롭다운에서 선택해야 함.

## 6. 성적서 저장 경로·파일명 규칙 (최근 변경됨 — 중요)

**2026-08-21에 구조가 완전히 바뀌었다.** 예전에는 `output/업체명/입고일/발주번호/` 식으로
깊은 폴더 구조였는데, 지금은:

```
성적서 발행/
  2026-08-21/                                              ← 오늘 날짜(생성일) 폴더 하나만
    260821_ACE_600005P086_둥근머리 볼트(M416L,STS304).pdf   ← YYMMDD_업체명_자재번호_제품명
    260821_ACE_600005P086_둥근머리 볼트(M416L,STS304).xlsx
```

- `report_builder.report_output_dir()` — 인자 없이 오늘 날짜 폴더만 만듦
- `report_builder.build_report_filename(supplier, material_no, product_name)` — 파일명 생성.
  파일명에 못 쓰는 문자(`\ / : * ? " < > |` 및 제어문자)는 **치환하지 않고 그냥 제거만**
  한다(예: "M4*16L" → "M416L"). 이건 사용자가 명시적으로 지정한 규칙이니 바꾸지 말 것.
- `report_builder._dedupe_path(path)` — 같은 날 같은 자재를 여러 번 검사하면 파일명이
  겹칠 수 있어서, 있으면 `(2)`, `(3)`... 자동으로 붙임. **예전엔 발주번호별 폴더로 자연히
  구분됐는데, 폴더가 날짜 하나로 합쳐지면서 새로 필요해진 안전장치**다.

## 7. report_builder.py — xlsx/PDF 생성 엔진 상세

### 7-1. 성적서(build_report / build_group_report)

- `template_form.xlsx`를 매번 `shutil.copy`해서 시작. **원본 파일은 절대 직접 열어서 저장하지
  말 것**(공유 템플릿이 오염되면 그 이후 생성되는 모든 자재 성적서가 옛날 데모 값을 그대로
  물려받는 버그가 실제로 있었다 — B/C/D/P열이 항상 옛 자재 값이 나오던 사고).
- 항목표는 **9행부터 27행까지(A~S, 최대 19항목)**. 항목을 쓰기 전에 반드시 이 범위
  전체를 먼저 clear해야 함 — 안 그러면 항목 수가 적은 자재는 안 쓰는 행에 옛날 값이
  그대로 남는다.
- `build_group_report()`는 조립품(부품 여러 개로 분해 검사하는 자재) 전용 — 부품마다
  시트를 하나씩 만들어 통합 워크북 1개로 출력한다. **`wb.copy_worksheet()`는 인쇄영역
  (print_area)과 페이지설정(배율/방향/용지)을 복사하지 않으므로 매번 명시적으로
  재지정해야 한다** — 이것도 실제로 겪은 버그(우연히 LibreOffice가 사용범위 기반으로
  비슷하게 렌더링해서 겉보기엔 괜찮았지만 잠재적 위험이었음).

### 7-2. 기준서(_fill_standard_sheet / _append_standard_sheet)

성적서 승인 완료 후 PDF 출력 시, **성적서 시트 바로 뒤에 기준서 시트를 추가로 붙여서
같은 파일 안에서 다음 페이지로 이어지게** 만든다. `standard_template.xlsx`가 원본이고,
`_append_standard_sheet()`가 워크북에 새 시트로 복사(셀 값+서식+병합+열너비+행높이까지
전부)한 다음 `_fill_standard_sheet()`가 내용을 채운다.

- 항목표는 **10행부터 29행까지(A~T, 최대 20항목)** — 성적서보다 1개 더 많이 지원함.
- **도면번호는 저장하지 않고 매번 계산**한다: `compute_drawing_no(material_no)` —
  자재번호 앞에 "A"를 붙이고 자재번호 안의 "P"를 "-"로 치환. 예: `602106P246` →
  `A602106-246`. 이 규칙은 원본 기준서 파일 안의 실제 예시(`602506P005`→`A602506-005`)로
  검증된 것이니 절대 바꾸지 말 것.
- 도면버전/개정날짜/판수/단위는 `materials` 테이블에 자재별로 저장되고, 규격 상세
  화면에서 수정 가능. 새 자재 등록 시 개정날짜는 오늘 날짜로 자동 채워짐.

### 7-3. AQL 0.65 = 중요항목 자동 "*" 표시

**사람이 입력하는 게 아니라 시스템이 자동으로 붙이는 것**이다. `is_critical_aql(aql)` /
`item_label(item_name, aql)` 헬퍼가 AQL이 0.65인 항목이면 항목기호 앞에 "*"를 붙인다.
성적서·기준서 양쪽 다 이 헬퍼를 거쳐서 항목 라벨을 쓴다. (원본 기준서 예시 파일에서
"백색 아연도금 5㎛ 이상"(AQL 0.65) 항목에 "*J"라고 표기돼 있는 걸 보고 역산해낸 규칙 —
사용자가 명시적으로 확인해준 규칙이다.)

**`item_label()`은 반드시 멱등이어야 한다 (2026-08-23 수정)** — 일괄등록으로 들어온
`specs.item_name`에 이미 `*`가 붙은 게 393건 있었고, 거기에 `*`를 한 번 더 붙여서
성적서에 **`**B`로 두 번 찍히던 버그**가 있었다(364건). 지금은 앞의 `*`를 먼저 떼고
`AQL 0.65 이거나 원래 *가 붙어 있었으면` 하나만 붙인다.

### 7-3-1. AQL 표기는 `report_builder.format_aql()` 하나만 쓴다

DB에는 `퍼센트10`으로 저장하고 화면·성적서에는 `10%`로 보여준다. 예전엔 이 변환이
성적서·기준서·웹 세 군데에 복사돼 있었다. 지금은 `format_aql()` 하나이고,
`app.py`는 `format_aql_display = report_builder.format_aql` 로 그대로 받아
Jinja 필터 `|aql_display`로 등록한다. **새 화면에서 AQL을 찍을 땐 반드시 이 필터를 쓸 것.**

날짜도 마찬가지로 `|date_korean` 필터를 쓴다 → `2026-08-23 (일)`.
`2026/08/23`, `26-08-23`, `20260823` 등 어떤 형식으로 들어와도 통일되고,
해석 못 하는 값은 원문을 그대로 돌려준다(값 유실 방지).

### 7-4. 로고 삽입 (_insert_logo)

`logo.png`(회사 로고, CHARDON GROUP)를 좌상단에 삽입한다. **openpyxl 기본
`ws.add_image(img, "A1")` 방식은 셀 경계에만 딱 붙기 때문에, 서브셀 단위(0.5cm 오프셋
같은)로 정밀 배치하려면 `OneCellAnchor`를 직접 구성해야 한다**:

```python
from openpyxl.drawing.spreadsheet_drawing import OneCellAnchor, AnchorMarker
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.utils.units import cm_to_EMU

size = XDRPositiveSize2D(cm_to_EMU(width_cm), cm_to_EMU(height_cm))
marker = AnchorMarker(col=0, colOff=cm_to_EMU(offset_cm), row=0, rowOff=cm_to_EMU(offset_cm))
img.anchor = OneCellAnchor(_from=marker, ext=size)
ws.add_image(img)
```

- 로고 위치: A4 좌상단에서 아래로 0.5cm, 오른쪽으로 0.5cm (양쪽 문서 동일)
- 성적서: 폭 8.52cm 고정, 높이는 로고 원본 비율대로 자동 계산 (`_logo_aspect_ratio()`가
  매번 PIL로 실제 `logo.png` 파일을 읽어서 비율 계산 — 하드코딩 안 함, 나중에 로고
  파일이 또 바뀌어도 자동으로 대응됨)
- 기준서: 높이 1.1cm로 축소 (제목줄 "제품 구성품 검사 주요사항"과 겹치는 걸 막기 위해
  — 기준서 상단 행 높이가 성적서보다 좁아서 원본 크기 그대로 넣으면 겹쳤던 실제 사고가
  있었다. 폰 여유 공간을 계산해서 이 값으로 정함.)
- **원본 기준서 파일 안에 있던 EMF(Windows 벡터 이미지) 형식 로고는 openpyxl도
  LibreOffice도 렌더링을 못 한다** — 실제로 변환해보면 빈 칸으로 나온다. 그래서 사용자가
  PNG로 다시 저장해서 새로 준 게 지금의 `logo.png`다. **혹시 로고를 또 교체해야 하면
  PNG/JPG 같은 일반 래스터 포맷이어야 하고, EMF/WMF는 절대 안 됨.**

### 7-5. 페이지를 1페이지로 강제 (fit-to-page)

```python
ws.page_setup.fitToWidth = 1
ws.page_setup.fitToHeight = 1
ws.sheet_properties.pageSetUpPr.fitToPage = True
```

**주의**: `ws.page_setup.fitToPage = True`처럼 세터를 직접 쓰면 openpyxl 내부에서
`_parent` 연결이 끊긴 상태로 읽혀서 `AttributeError: 'NoneType' object has no attribute
'sheet_properties'`가 난다. 반드시 위 코드처럼 `sheet_properties.pageSetUpPr.fitToPage`를
직접 조작하는 우회 방식을 써야 한다.

### 7-6. 그 외 openpyxl/LibreOffice 함정 모음

- **wrap_text 필수**: 비고란처럼 여러 줄 텍스트가 들어가는 셀에 `Alignment(wrap_text=True)`를
  명시적으로 안 주면, xlsx 자체는 줄바꿈이 잘 저장되는데 **LibreOffice가 PDF로 변환할 때만
  명시적 줄바꿈(`\n`)을 무시하고 한 줄로 뭉개버린다.** (openpyxl로 xlsx만 열어볼 땐 멀쩡해
  보여서 놓치기 쉬운 버그였다.)
- **img.width/img.height는 재로딩 시 신뢰할 수 없다**: 파일을 저장했다가 다시 openpyxl로
  열어서 `image.width`를 확인하면 **실제 화면 표시 크기가 아니라 원본 이미지 파일의 픽셀
  크기**가 나온다. 진짜 배치 크기를 확인하려면 xlsx 안의 drawing XML에서 `<a:ext cx=".."
  cy=".."/>` 값을 직접 봐야 한다(EMU 단위, 1cm = 360000 EMU).
- **LibreOffice headless 연속 호출 주의**: 같은 프로필로 연달아 두 번 호출하면 프로필 잠금
  충돌로 두 번째 호출이 조용히 실패한다. 매 변환마다 `tempfile.mkdtemp()`로 독립된
  `-env:UserInstallation=` 프로필을 쓰고 있다.

## 8. templates/ 화면 목록과 역할

- `base.html` — 공통 레이아웃, 드롭다운 메뉴(검사/규격·자재/관리 3개 카테고리)
- `home.html` — 전체 성적서 목록, 승인상태 배지(한글+색상)
- `login.html`, `my_password.html` — 로그인, 본인 비밀번호 변경
- `intake.html` — 입고 리스트 등록(스프레드시트형 붙여넣기)
- `inspect_select.html` — 검사 대기 목록(**검색+선택삭제 있음**)
- `inspect_form.html` — 측정값 입력(타이머, D패드 방향이동, 계측기유효기간 D-15 강조)
- `inspection_detail.html` — 성적서 상세(비고3색 입력, 승인/반려/특채, 서명방식 선택)
- `spec.html` — 규격 관리 자재 목록(검색+선택삭제, **삭제버튼은 표 위쪽에 있음**)
- `spec_detail.html` — 자재 하나의 항목 상세(인라인 수정/삭제, 자재번호변경, 규격표기
  조합형빌더, 기준서정보 편집카드, 선택삭제 버튼도 표 위쪽)
- `spec_import.html` / `spec_import_result.html` — 규격 일괄업로드(그룹묶기 체크박스 포함)
- `spec_quick_add.html` — 규격 개별(빠른)등록 — 스프레드시트+측정조건 팝업+이전자재
  불러오기(드래그다중선택)+선택자재 일괄조건적용(배치모드, 충돌확인 2단계)
- `assembly_list.html` / `assembly_detail.html` — **조립품 관리(2026-08-23 신규)**.
  파츠 구성 등록/수정, 파츠별 "자재 미등록 / 규격 없음" 경고. 5-0절 참고.
- `import_assembly.html` — MA 자동출력.xlsm에서 조립품 일괄 임포트(`users` 권한)
- `data_health.html` — **데이터 점검(2026-08-23 신규, `users` 권한 전용)**.
  `db.data_health_report()`가 돌려주는 점검 항목들을 심각도(🔴시급/🟠확인/🔵참고)별로 보여줌.
  점검을 추가하려면 `data_health_report()`에 dict 하나 추가하면 화면은 자동으로 늘어난다.
- `output_list.html` / `output_result.html` — 출력 대기 목록, 선택/전체 출력
- `users.html` — 계정 관리(체크박스 권한, **아이디/이름 인라인수정**, 비밀번호 재설정, 삭제)
- `logs.html` — 활동 로그(읽기 전용 — 수정·삭제 UI 절대 추가하지 말 것)
- `input.html` — **초기 프로토타입 잔재, 실제로 어떤 라우트에서도 참조 안 됨(확인 완료).
  삭제해도 안전함.**

### 8-1. 중복을 만들지 말아야 할 공용 헬퍼 (2026-08-23 정리)

같은 계산이 여기저기 복사돼 있으면 한 곳만 고쳤을 때 나머지가 어긋난다. 아래는 이미
한 곳으로 모은 것들이니 **새로 복사하지 말 것**:

| 헬퍼 | 위치 | 예전 상태 |
|---|---|---|
| `build_specs_with_sample(specs, qty)` | app.py | 같은 리스트 컴프리헨션이 **8군데** 복붙 |
| `report_builder.format_aql(aql)` | report_builder.py | 성적서·기준서·웹 **3군데** |
| `item_label(item_name, aql)` | report_builder.py | 멱등이 아니라 `**B` 버그 |
| `spec_import.normalize_spec_text(t)` | spec_import.py | 정규화 없이 원문 파싱 |

`build_specs_with_sample()`은 `sample_qty`(AQL·입고수량으로 계산)와
`no_limit`(규격 미입력 여부)를 붙여준다. 검사 입력/상세/재검사/시간계산이 전부 이걸 쓴다.

### 8-2. 최종 결정 3종 = 서명 필수 + 최종결정권자만 (2026-08-23)

**최종 결정 3종**: `승인(approve)` · `특채(special)` · `불합격 확정(failed)`
→ 셋 다 **서명 없이는 통과 못 한다**. (`반려(reject)`만 서명 없이 가능 — 사내 재검사 요청이라서)

서명 처리는 세 액션이 공통 블록 하나를 쓴다. 새 결정 액션을 추가하면 그 블록의
`if action in ("approve", "special", "failed")` 에 같이 넣어야 한다.

**최종결정권자** (`users.is_final_approver`, 최대 `db.MAX_FINAL_APPROVERS`=2명):
- 계정 상세(`/users/<id>`)의 권한 폼에 체크박스로 지정/해제한다
  (별도 액션이 아니라 `update_permissions` 안에서 같이 처리 — 권한 저장 뒤에 플래그를 건드려야
   '승인 권한 보유' 검사가 맞는다)
- `승인` 권한이 없으면 지정 불가. 이미 지정된 사람의 `승인` 권한을 빼면 **자동으로 해제**된다
- 게이트: `_can_make_final_decision(user)` — `approve`, `approve_batch` 양쪽에서 호출

**중요 — 아무도 지정 안 됐을 때는 게이트를 통과시킨다.** 지정하기 전에 승인이 전부
잠겨버리면 안 되기 때문. **한 명이라도 지정되는 순간부터** 최종결정권자만 결정할 수 있다.
이 폴백을 없애려면 반드시 최소 1명이 지정돼 있는지 먼저 확인할 것.

**부적합 통보서(NCR) 확인도 같은 게이트를 쓴다** — 협력사로 발송되는 문서이기 때문.
`ncr_confirm` 라우트에서 `_can_make_final_decision()` + 서명을 요구하고,
서명 이미지는 `ncr.confirm_signature`에 경로로 저장돼 통보서 하단
"최종결정권자 승인" 칸에 표시된다. 서명 파일명은 `static/signatures/ncr{id}.png`
(성적서 서명 `{inspection_id}.png` 와 겹치지 않게 접두어를 붙임).

### 8-2-1. AQL 표기는 두 줄 (2026-08-23)

성적서 AQL 칸(C열)은 **두 줄**로 나간다:
```
4
샘플 5개/무결점
```
이 시스템은 샘플 중 **하나라도 벗어나면 불합격(Ac=0)** 이다. 그런데 KS Q ISO 2859-1의
AQL 4.0은 원래 합격판정개수 Ac=1(1개 불량까지 합격)이라 표기와 판정이 어긋난다.
본사 양식이라 AQL 표기를 뺄 수는 없어서, **판정 근거를 같이 적는 방식**으로 정리했다.

**`wrap_text=True`가 반드시 있어야 한다** — 없으면 LibreOffice가 PDF 변환할 때
줄바꿈을 무시하고 한 줄로 뭉갠다(7-6절 참고).

### 8-2-2. 검사 입력 자동저장은 서버에 (2026-08-23)

예전엔 `localStorage`에만 저장해서 **태블릿이 꺼지거나 기기를 바꾸면 입력이 날아갔다.**
지금은 서버 우선이다:
- 입력 0.8초 후 `POST /inspect/draft/<intake_id>` 로 JSON 전송 → `inspection_drafts` 테이블
- 페이지 이탈 시 `navigator.sendBeacon`으로 마지막 상태를 한 번 더 밀어넣음
- 복구 순서: **서버 임시저장 → 없으면 localStorage** (localStorage는 오프라인 백업으로 유지)
- 성적서 제출되면 `db.delete_inspection_draft(intake_id)`로 삭제
- 화면 우측에 `#draft-state`로 저장 상태 표시(저장 중 / ✓ 자동 저장됨 / ⚠ 서버 저장 실패)

### 8-2-3. 성적서 위변조 검증 (2026-08-23)

- `inspections.content_hash` — **승인 시점**에 판정 내용(헤더+항목)을 SHA-256으로 굳힘
- `inspections.pdf_hash` — **출력 시점**에 발행된 PDF 파일 자체의 해시
- `compute_content_hash()`는 항목을 이름순 정렬해서 담는다 → 조회 순서가 달라져도 해시가 안 흔들림
- 검증: `GET /inspection/<id>/verify` (JSON) → 성적서 상세에 배지로 표시
- **해시를 넣은 시점 이후 승인분만 보호된다(소급 불가).** 그 이전 건은 데이터 점검의
  "무결성 검증 기준값이 없는 승인 성적서"에 목록으로 뜬다.
- 재출력하면 `pdf_hash`만 갱신하고 `content_hash`는 건드리지 않는다(판정은 안 바뀌므로).

### 8-2-4. 4M 변경점 (2026-08-23)

`change_points` 테이블 — 협력사가 사람(Man)·설비(Machine)·자재(Material)·방법(Method)을
바꾼 시점 기록. 화면은 `/change-points`.
- `db.recent_change_points_for(supplier, material_no, within_days=90)` → 검사 화면 상단 경고
- `material_no`가 비어 있으면 **그 업체 전체**에 적용되는 변경으로 본다
- 나중에 대시보드에서 **변경 전후 불량률 비교**의 기준선으로 쓸 예정

### 8-2-5. 백업 자동정리 (2026-08-23)

`prune_backups()` — `record_change()` 끝에서 매번 호출:
- 최근 **30개**는 무조건 보관
- 그 이전은 **하루 1개**(그날 마지막 것)만 남김
- **90일** 초과분은 삭제
- **파일명이 `iqc_YYYYMMDD_HHMMSS_{id}.db` 형식이 아니면 건드리지 않는다**
  → 사람이 손으로 만든 `iqc_before_*.db` 같은 백업은 자동으로 보호됨

### 8-2-6. 품질 현황 대시보드 / 집계 (2026-08-23)

**집계는 `db.quality_report()` 하나로만 한다.** 대시보드·JSON/엑셀 내보내기·업체 성적표가
전부 이 함수를 쓴다. 새 화면을 만들 때 집계를 다시 짜지 말 것.

**불량률은 수량 기준(사용자 확정)**:
```
불량률 = 불합격 확정 수량 ÷ 판정 확정 수량 × 100
PPM   = 불량률 × 10,000
판정 확정 = 합격 + 특채 + 불합격   (승인 대기·반려는 분모에서 제외)
```
- 로트 상태는 `_lot_state(status, approval_type)`가 정한다
  → approved+failed=불합격 / approved+special=특채 / approved=합격 / 그 외=미결
- **특채는 불량률 분자에 안 들어간다.** 규격 이탈이긴 하므로 `규격이탈률`로 따로 보여준다
- **발주번호(po_number)를 로트 번호로 간주한다** (사용자 확정 — 별도 로트 필드 없음)
- 기간 묶음은 `_period_key()`: 일간/주간(ISO주차)/월간/분기/반기/연간

**내보내기**: JSON이 정본이다. 발표자료는 화면(HTML)을 긁는 게 아니라 JSON을 쓴다.
JSON에는 **기간·필터·불량률 기준을 같이 담는다** — 나중에 파일만 봐도 이 숫자가
건수 기준인지 수량 기준인지 알 수 있어야 하기 때문.

**공정능력 `db.process_capability(material_no)`**:
`Cpk = min((USL-μ)/3σ, (μ-LSL)/3σ)`, 단측이면 있는 쪽만. 판정은 1.67 우수 / 1.33 양호 /
1.0 주의 / 그 미만 부족. σ=0이면 "산포없음"(측정 분해능 부족 의심), 표본이 적으면 "표본부족".

### 8-2-7. 업체 월간 품질 성적표 (2026-08-23)

`supplier_reports` 테이블. **협력사로 나가는 문서라 NCR과 같은 게이트**를 탄다:
생성(누구나) → **최종결정권자 승인 + 서명** → 발송.
- 생성 시 `quality_report()` 결과를 **JSON 스냅샷으로 저장**한다 — 나중에 원본 데이터가
  바뀌어도 발송한 성적표 내용은 그대로 남아야 하기 때문
- `status`: draft → approved → sent. **초안일 때만** 재생성·삭제 가능
- 서명 파일명은 `static/signatures/sr{id}.png`

### 8-2-8. 최종 결정의 전제조건 — `_final_decision_block_reason()` (2026-08-23)

승인·특채·불합격 확정은 **단건이든 일괄이든 이 함수 하나를 통과해야** 한다.
품질 시퀀스상 앞뒤가 안 맞던 것들을 여기서 막는다:

| 막는 것 | 왜 |
|---|---|
| 규격 미입력 항목 존재 | 판정 근거가 없다. **불합격도 막는다** — 우리 데이터 누락이 협력사 NCR로 나가면 안 됨 |
| 미측정·입력오류 항목 존재 | 검사가 안 끝났는데 결정할 수 없음 |
| 전 항목 합격인데 **특채** | 특채는 "규격 이탈품을 예외적으로 쓴다"는 뜻. 합격이면 성립 안 함 |
| **불합격 확정에 사유 없음** | 반려는 사유 필수인데 더 중대한 불합격이 선택이던 건 앞뒤가 안 맞았음 |

### 8-2-9. 로트 중복 집계 방지 (2026-08-23)

**같은 입고 건에 성적서가 두 개 생기면 대시보드에서 같은 로트 수량이 두 번 잡힌다.**
실제로 등록 버튼 연타/새로고침으로 1초 간격 3건이 생겨 수량이 3배로 잡힌 사례가 있었다.

- `db.active_inspection_for_intake(intake_id)` — 살아있는 성적서가 있으면 `inspect_form`이
  새로 만들지 않고 기존 성적서로 보낸다. 고치려면 '수정'이나 '재검사'를 써야 한다.
- `_lot_state()`가 `superseded`를 **"대체됨"** 으로 분류하고 `quality_report()`가
  `status != 'superseded'` 로 걸러낸다 → 재검사한 옛 성적서는 집계에서 제외.

### 8-2-10. 승인 회수 시 지워야 하는 것 (2026-08-23)

`approve_revoke`는 상태만 되돌리는 게 아니라 **서명·content_hash·pdf_hash를 같이 지운다**:
- 승인이 취소됐는데 승인자 서명이 남아 있으면 안 됨
- 해시는 '승인 시점의 내용'을 굳힌 값이라, 회수 후 값을 고치면 **변조로 오인**된다
  (재승인 시 그 시점 기준으로 다시 굳혀짐)

### 8-2-11. 서명 패드는 `static/signature_pad.js` 하나 (2026-08-23)

승인 화면 / 부적합 통보서 / 업체 성적표가 **같은 파일**을 쓴다.
예전엔 캔버스 드로잉 코드가 세 템플릿에 복붙돼 있었다.
```js
var pad = SignaturePad.attach('sigPad');
pad.isEmpty() / pad.toDataURL() / pad.clear() / pad.loadDefault(cb)
pad.fillAndConfirm('hiddenInputId', '확인 문구')   // 폼 제출 직전 훅
```
새 서명 화면을 만들면 **반드시 이걸 쓸 것.**

### 8-2-12. 약어는 반드시 설명을 붙인다 — `templates/_glossary.html` (2026-08-23)

AQL·PPM·Cpk·Cp·NCR·4M·로트·특채 등 **약어나 품질 용어가 나오는 곳에는 설명 아이콘을 붙인다.**
문구는 `_glossary.html` 한 곳에만 두고 매크로로 꺼내 쓴다:
```jinja
{% from "_glossary.html" import term %}
{{ term('cpk') }}              {# 물음표 아이콘 + 엑셀 메모처럼 뜨는 팝오버 #}
{{ term('lot', '(로트)') }}     {# 앞에 글자를 같이 보여줄 때 #}
```
표시 장치(`.info-btn` / `.info-pop`)는 base.html에 이미 있고, 마우스오버로 뜨고 클릭하면 고정된다.

**설명을 쓸 때 지킬 것 (사용자가 명시한 원칙):**
- **비유는 오해가 생기지 않는 것만.** 업계 관행을 근거 없이 단정하지 말 것.
  예전에 Cpk를 "자동차 부품은 1.33 이상"이라고 썼는데, 맥락을 모르는 사람이
  "누가 그렇게 정했냐"고 딴지 걸 여지가 있었다. 지금은 "구간 표시는 1.33/1.67 기준이고
  실제 요구 기준은 거래처·품목마다 다르니 확인하라"로 바꿨다.
- 기준값은 **누가 정한 기준인지**를 같이 적는다.
- **이 시스템에서 실제로 어떻게 동작하는지**를 반드시 포함한다
  (예: AQL은 샘플 개수만 정하고 판정은 무결점).

### 8-2-13. 대시보드 필터 방식 (2026-08-23)

- **업체·발주번호는 다중 선택(칩)** — 검색해서 고르면 아래에 블럭으로 쌓이고 ✕로 뺀다.
  폼 제출 시 `supplier=A&supplier=B` 처럼 같은 이름이 여러 번 넘어간다.
  라우트는 `request.args.getlist()` + 콤마 분해 둘 다 받는다(`_dashboard_params`).
- **판정 상태 필터**(합격/특채/불합격/미결) — `db.LOT_STATES`.
  SQL이 아니라 `_lot_state()` 결과로 거른다(판정 규칙이 한 곳에만 있게).
- **표시 행수** `ROW_LIMIT_CHOICES` (0=전체). 집계는 전체로 하고 **표시만** 자른다.
- **각 구역은 `<details class="sec">`로 접힌다.** 열림/닫힘은 localStorage에 기억된다.

### 8-2-14. 날짜 파싱은 자릿수를 봐야 한다 (2026-08-23)

`_parse_any_date()`는 구분자 없는 숫자 형식을 **길이가 정확히 맞을 때만** 인정한다.
안 그러면 `260821`(=2026-08-21)을 `%Y%m%d`로 읽어서 **2608년 2월 1일**이 돼버린다
(실제로 검사 이력 그룹 헤더에 `2608-02-01`로 표시된 적 있음).
검사 이력은 원본 문자열이 아니라 **파싱한 날짜로 그룹을 묶는다** —
안 그러면 같은 날인데 `260821`과 `2026-08-21`이 별개 그룹으로 갈라진다.

### 8-2-15. 좁은 화면 드롭다운 (2026-08-23)

상단 메뉴가 두 줄로 접히면 열린 목록이 아랫줄 버튼 뒤로 깔려 글자가 겹쳐 보였다.
- `.nav-dropdown.open { z-index:1000 }`, `.menu { z-index:1001 }`
- `toggleNav(btn)` — **하나를 열면 나머지는 닫는다**
- 560px 이하에서 오른쪽 끝 메뉴는 `right:0` 기준으로 펴서 화면 밖으로 안 나가게

### 8-3. 날짜 표기는 필터로만 (2026-08-23)

화면에 날짜를 그대로 찍으면 안 된다. 반드시 Jinja 필터를 쓴다:
- `{{ x | date_korean }}` → `2026-08-23 (일)` — 검사일·입고일 등 날짜만 있는 값
- `{{ x | datetime_korean }}` → `2026-08-23 (일) 14:30` — 승인일시·생성일 등 시각이 있는 값

`2026/08/23`, `26.08.23`, `20260823` 등 어떤 형식으로 들어와도 통일되고,
해석 못 하는 값은 **원문 그대로** 돌려준다(값 유실 방지).
새 화면을 만들 때 날짜 컬럼을 필터 없이 쓰면 표기가 어긋나므로 주의.

## 9. 아직 구현 안 된 것 / 보류된 것

- **자유 양식 성적서 제작기** — 사용자가 요청했으나 "규모가 크다"고 안내 후 "나중에 하자"로
  보류됨. 확정된 설계: 웹 화면에서 드래그앤드롭으로 텍스트박스·데이터필드(자재번호,
  검사날짜, 항목표 등)를 캔버스에 배치 → 템플릿으로 저장 → 자재별로 "기본 양식 대신 이
  커스텀 템플릿 사용"하도록 지정 가능 → 출력 시 그 레이아웃 그대로 데이터 채워서 생성.
  다음에 이 요청이 다시 들어오면 이 설계안대로 진행하면 된다.
- `input.html`이 실제로 쓰이는지는 확인 완료(위 8절 참고) — 안 쓰인다.

## 10. 일하는 방식 관련 — 사용자가 이전 세션에 명시했던 선호

- 사용자는 코딩 비전문가다. 기술적인 설명은 하되, 항상 **실제로 뭐가 바뀌는지 쉬운 말로**
  설명해줄 것.
- "~해줘"는 편하게 하는 말버릇이고, **실제 코드 작성은 "코드 짜줘" 등 명시적 확인 후에만**
  진행하는 걸 선호했다(Claude Code에선 이 구분이 덜 중요할 수 있지만, 특히 범위가 크거나
  모호한 요청은 바로 구현하지 말고 먼저 계획을 설명하고 확인받는 걸 선호).
- 요청이 크거나 여러 갈래로 해석될 수 있으면, 코드를 짜기 전에 **선택지 형태로 명확히
  확인 질문**을 먼저 던지는 방식을 선호했다.
- 기능 구현 후에는 **실제로 end-to-end 흐름을 돌려서(가상 데이터로 입고→검사→승인→출력
  전체) 검증**하는 걸 항상 기대한다 — 컴파일만 통과했다고 끝난 게 아니라, 실제 생성된
  xlsx/PDF를 열어서 값·서식까지 확인하는 방식으로 여러 차례 진짜 버그를 잡아냈다
  (위 4절, 7-1절 등 참고). 이 습관을 유지할 것.
- 큰 기능은 구현 전에 프로토타입/목업을 먼저 보여주고 확인받은 뒤 진행하는 걸 좋아했다
  (로고 배치 위치를 정할 때 실제로 이렇게 했음).
- README.txt는 비개발자 사용자용 운영 안내서다 — 새 기능을 추가하면 이것도 같이 갱신할 것.
