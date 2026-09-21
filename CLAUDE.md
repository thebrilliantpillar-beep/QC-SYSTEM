# IQC 입고검사 성적서 자동화 시스템 — 작업 인수인계 (CLAUDE.md)

이 문서는 claude.ai 채팅으로 이 프로젝트를 처음부터 함께 만들어온 이전 세션의 전체
맥락을 정리한 것입니다. Claude Code는 이 대화 기록에 접근할 수 없으므로, 코드만 보고는
알 수 없는 "왜 이렇게 만들었는지"와 "이미 겪었던 함정들"을 여기에 최대한 자세히 적어둡니다.
작업을 시작하기 전에 이 파일 전체와 `PROGRESS.md`(최근 작업 이력·다음 할 일)를 읽어주세요.

## 0. 이 프로젝트가 뭔지 한 줄 요약

샤든그룹(리클로저 등 배전용 전력기기 제조업체 — 자동차 부품 아님, 2026-09-17 사용자가 직접
정정. 출고 관리의 S/N 접두어 `CKMR`=리클로저 본체/`CKCB`=제어함이 이 업종을 가리키는
근거였는데 예전 세션이 "추정"으로 잘못 적어둔 걸 이후 세션들이 그대로 답습했었다 — 21절
참고)의 **입고 자재 검사 성적서**를 수기 작성에서
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

### 7-4-1. 열너비(문자단위) → EMU 변환은 워크북 기본 글꼴에 따라 계수가 다르다 (2026-09-15)

이미지를 셀 안에서 "정중앙"에 배치하려고 열너비(엑셀 열너비 설정 대화상자 값, 문자
단위)를 EMU로 바꿀 때, `문자단위 * 7 * 9525`처럼 **Calibri 11pt 기준 계수(MDW=7px)를
아무 워크북에나 재사용하면 안 된다.** 실제로 `build_outbound_qr_labels_excel()`(QR
라벨 엑셀)에서 이 계수를 그대로 썼다가 QR 이미지가 셀 왼쪽으로 눈에 띄게 치우쳐
보이는 버그가 났다 — 원인은 그 워크북의 실제 기본 글꼴이 Calibri가 아니라
**"맑은 고딕" 11pt**였고, 맑은 고딕의 MDW(최대 자릿수 폭)는 8px이기 때문이다(사용자가
실제 엑셀에서 이미지를 화살표로 여러 번 눌러 눈으로 정중앙을 맞춘 값을 역산해서
확인 — MDW=8로 계산한 값이 오차 0.1px 이내로 정확히 일치했다).

**올바른 변환은 ECMA-376 표준 공식 그대로 구현해야 한다** (`_excel_col_width_to_emu()`,
report_builder.py):
```python
pixels = floor(((256 * width_chars + floor(128 / MDW)) / 256) * MDW)
emu = pixels * 9525
```
**MDW는 폰트마다 다르므로, 새 워크북에서 이 변환을 쓰기 전에 그 워크북의 실제 기본
글꼴이 뭔지부터 확인할 것** — `openpyxl.Workbook()`으로 새로 만든 워크북이라도 코드
어딘가에서 기본 스타일 폰트를 "맑은 고딕"으로 지정해뒀으면 Calibri 계수는 안 맞는다.
**`_build_ncr_photo_sheet()`/`_append_ncr_photos()`(NCR 통보서 사진 배치)도 지금
Calibri 기준 계수(`CHAR_TO_EMU = 7 * 9525`)를 그대로 쓰고 있다** — 그 워크북 기본
글꼴도 맑은 고딕이면 똑같은 치우침 버그가 있을 수 있음(2026-09-15 quality-watcher가
발견, 아직 미수정·미확인 상태로 남겨둠 — 이미지 배치 관련 요청이 그쪽에서 또 들어오면
`_excel_col_width_to_emu(mdw=8)`로 통합하는 걸 먼저 검토할 것).

**세로 방향(행높이→EMU)은 이 문제가 없다** — 포인트(pt)는 폰트와 무관한 절대 단위라
`포인트 * 12700`(1pt = 1/72인치 = 12700 EMU)이 항상 정확하다. 가로(열너비, 문자단위)만
폰트 의존적이라는 점을 헷갈리지 말 것.

**2026-09-15 두 번째 재발 — `build_outbound_excel()`(출고 이력 엑셀)도 같은 함정에
걸렸었고, 이번엔 "선언된 폰트 이름만 보고 판단하면 안 된다"는 새 교훈까지 나왔다.**
이 함수는 `openpyxl.Workbook()`을 기본값 그대로 쓰고(코드 어디서도 폰트를 "맑은 고딕"
으로 바꾸지 않음), openpyxl 자체에 물어보면 기본 폰트가 **"Calibri"라고 나온다**
(`Workbook()._fonts[0].name == "Calibri"`, 직접 확인함) — 그래서 처음엔 "폰트가
Calibri니까 MDW=7이 맞다"고 판단해서 quality-watcher의 "폰트 미확인" 지적을
반증했었다. 그런데 **Python `pywin32`로 실제 Excel COM을 열어서 `ws.Columns(9).Width`
(pt)와 사진 `Shape.Width`(pt)를 직접 측정**해보니, 문자단위 16으로 설정한 열의 실제
렌더 폭이 96.0pt였는데 `7*9525` 계산값은 84.0pt(87.5%)로 정확히 8:7 비율만큼
모자랐다 — 즉 **이 컴퓨터의 실제 Excel은 "Calibri"라고 적힌 워크북도 MDW=8로
렌더링하고 있었다**(Windows/Office 로케일 등의 이유로 실제 렌더링 폰트가 선언된
폰트와 다르게 치환됐을 가능성 — 정확한 이유는 추적 안 함). `_excel_col_width_to_emu()`
(mdw=8)로 교체하고 재측정하니 여백 0.0pt로 완전히 일치했다.

**교훈: 워크북의 "선언된 폰트 이름"을 코드로 확인하는 것만으로는 부족하다 — 그
워크북을 실제로 만드는 이 환경(이 PC의 Windows/Excel)이 그 폰트를 실제로 어떻게
렌더링하는지까지 COM으로 직접 측정해야 한다.** 코드 리뷰(quality-watcher 등)가
"폰트를 확인 안 했다"고 지적했을 때 "코드상 폰트는 Calibri가 맞다"는 반박만으로
끝내지 말고, 애매하면 이번처럼 실측(COM)까지 가서 결론 낼 것 — 이론적으로 맞는
반박이 실측과 다를 수 있다는 걸 실제로 겪었다.

**2026-09-16 세 번째 재발 — `_place_improvement_photos()`(개선요청서 엑셀)도 같은
`CHAR_TO_EMU = 7*9525` 근사값을 그대로 복사해 썼다가 실측(Excel COM)으로 걸림.**
이번 템플릿(`improvement_request_template.xlsx`)은 openpyxl로 확인한 워크북 기본
글꼴이 실제로 "맑은 고딕"(출고 이력 때와 달리 이번엔 선언된 폰트명부터 Calibri가
아니었음)이었는데도 새로 짠 함수가 옛 상수를 그대로 복붙했다. COM으로 측정하니
사진 폭이 실제 열 폭의 87.6%(≈7/8, MDW 불일치와 정확히 일치)만 채워서 재확인,
기존 `_excel_col_width_to_emu()`(mdw=8)로 교체해서 여백 0에 가깝게(99.9~100%) 고침.
**`_insert_ncr_photos()`(1004행)와 QR 그리드 배치(1057행)는 여전히 옛 `CHAR_TO_EMU`
상수를 쓰고 있다** — 이 두 곳도 다음에 손댈 일이 생기면 그 워크북의 실제 기본
글꼴을 COM으로 먼저 실측하고 `_excel_col_width_to_emu()`로 통합할 것. **새로 엑셀에
사진/도형을 배치하는 함수를 짤 때 `7 * 9525`(또는 비슷한 하드코딩된 배수)를 그냥
복붙하지 말 것 — 매번 이 문제가 재발했다.** `_excel_col_width_to_emu()`를 기본으로
쓰고, 그 워크북의 실제 MDW가 8이 아닌 게 COM으로 확인되면 그때 다른 mdw 값을
넘기는 방향으로 짤 것.

### 7-4-2. **중요** — `OneCellAnchor`+`ext`로 넣은 이미지는 실제 엑셀에서 셀 전체를
꽉 채워버릴 수 있다. LibreOffice로는 이 버그를 절대 못 잡는다 (2026-09-15)

이 세션(과 아마 이전 세션들)이 반복해온 검증 방식 — "LibreOffice로 xlsx→PDF 변환해서
눈으로 확인하면 실제 엑셀과 같다고 본다" — 이 **이미지 삽입에 한해서는 틀렸다**는 게
실제로 드러난 사고다. QR 라벨 엑셀에서 이미지를 3cm 크기로 셀 한가운데 넣었는데,
사용자가 **진짜 윈도우 엑셀**(LibreOffice 아님, PDF 메타데이터의 producer가
`Microsoft: Print To PDF`)로 인쇄해보니 QR 이미지가 3cm가 아니라 **셀 전체(열너비×
행높이)를 꽉 채워서** 위아래 절취선(테두리)을 통째로 가려버렸다. 그런데 이 세션이
그동안 수없이 LibreOffice로 변환해서 확인했을 땐 매번 정상(의도한 3cm 크기, 여백
있음)으로 보였다 — **LibreOffice가 이 버그를 재현을 안 해줘서 여러 번의 "실측 검증"을
거치고도 못 잡은 것.**

**원인(추정, openpyxl 소스코드로 확인)**: `openpyxl.drawing.spreadsheet_drawing.SpreadsheetDrawing._picture_frame()`이
그림을 워크북에 쓸 때 `<xdr:pic><xdr:spPr>`에 `prstGeom`만 넣고 **`<a:xfrm>`(도형의
절대 위치/크기를 명시하는 하위 요소)은 절대 안 넣는다** — `OneCellAnchor`의 `_from`+
`ext`만으로 위치/크기가 이미 정의되니 spPr에 또 xfrm을 안 넣어도 스펙상 문제는 없어야
하는데, **실제 윈도우 엑셀(적어도 "인쇄" 경로)은 spPr에 xfrm이 없는 그림을 만나면
`ext` 크기를 무시하고 앵커된 셀 전체 크기로 늘려서 그리는 것으로 보인다.**
LibreOffice는 스펙대로 `ext`만 보고 정확히 그려서 이 차이가 안 드러났다.

**확인 방법 — 실제 엑셀로 직접 재현/검증**: 이 PC엔 (사용자가 실제로 쓰는) 진짜
Microsoft Excel이 깔려 있고, **PowerShell + COM 자동화로 headless하게 열어서 PDF로
내보낼 수 있다** — LibreOffice만으로는 이런 버그를 못 잡으므로, 이미지 배치가 걸린
엑셀 출력을 새로 만들거나 고칠 땐 이 방법도 같이 써서 검증할 것:
```powershell
$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$wb = $excel.Workbooks.Open("<입력 xlsx 절대경로>")
$wb.ExportAsFixedFormat(0, "<출력 pdf 절대경로>")  # 0 = xlTypePDF
$wb.Close($false)
$excel.Quit()
```
(Bash 도구에서 `powershell -ExecutionPolicy Bypass -File 스크립트.ps1 -InPath ... -OutPath ...`
형태로 호출. 변환된 PDF는 기존처럼 `pymupdf`/`fitz`로 이미지화해서 확인하면 된다 —
필요하면 4배율(`fitz.Matrix(4,4)`) 이상으로 확대해서 셀 경계와 이미지 경계가 겹치는지
픽셀 단위로 대조할 것, 일반 배율로는 1mm 미만의 여백 소실이 눈에 잘 안 띈다.)

**고친 방법**: `build_outbound_qr_labels_excel()`의 QR 이미지 배치를
`OneCellAnchor(_from=marker, ext=size)` 대신 **`TwoCellAnchor(editAs="oneCell",
_from=marker_start, to=marker_end)`**로 바꿨다 — `to` 마커를 `_from`과 같은 셀(같은
col/row) 안에서 `colOff+ext`/`rowOff+ext`로 계산해서, "이미지가 차지할 사각 영역"을
`ext`/`xfrm` 없이 **두 좌표만으로 직접 정의**한다. 이러면 xfrm 유무와 무관하게
렌더러가 정확한 크기를 알 수 있다 — TwoCellAnchor는 이 버그가 원천적으로 발생할
수 없는 구조다. `editAs="oneCell"`은 "칸이 이동하면 같이 이동하되 칸 크기가
바뀌어도 이미지 크기는 안 바뀐다"는 뜻으로, `OneCellAnchor`가 주던 것과 같은
동작을 준다.

**아직 안 고친, 잠재적 위험으로 남아있는 곳**: `_insert_logo()`(성적서/기준서 로고),
서명 스탬프(485~486줄 부근), `_place_photos_in_area()`(NCR 사진/출고 이력 사진) —
전부 여전히 `OneCellAnchor`+`ext` 방식을 쓴다. 지금까지 실사용자 신고가 없었던 건
①로고/서명은 이미 자기 셀 크기에 거의 맞춰 넣어서 "꽉 채워짐"의 차이가 육안으로
안 드러났거나, ②아무도 진짜 윈도우 엑셀로 인쇄해서 자세히 안 봤을 가능성이 있다.
**이 함수들 중 하나를 다시 고칠 일이 생기면, 이번처럼 실제 엑셀(COM 자동화)로도
검증할 것 — LibreOffice 결과만 믿고 "됐다"고 하지 말 것.** 여유가 되면 전부
TwoCellAnchor로 통일하는 것도 고려해볼 만하지만, 지금은 이번에 실제로 문제가
드러난 QR 라벨 함수만 고쳤다(과설계 방지 — 문제가 확인 안 된 곳까지 미리 다
바꾸지 않음).

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

**이 fit-to-page 설정을 아예 빠뜨리면 벌어지는 일 (2026-09-15 재발)**: 열이 여러 개인
표에서 fit-to-page/방향 지정을 빼먹으면, 넘치는 열이 다음 페이지로 넘어가는 게 아니라
**PDF에서 그 열 자체가 통째로 안 보이게 잘려버린다** — LibreOffice 변환으로 직접 열어보지
않으면 절대 못 알아챈다(xlsx 파일 자체엔 데이터가 멀쩡히 들어있어서 openpyxl로 다시
읽으면 값이 다 보임). 실제로 `build_outbound_excel()`(출고 이력 5열 표)에 이 설정을
빼먹어서 마지막 열("본체사진"과 "담당자" 정보)이 PDF에서 사라지는 사고가 있었다. **여러
열/넓은 표를 새로 만드는 함수는 무조건 이 3줄 세트(방향+fitToWidth+fitToPage)를 넣고,
반드시 실제 LibreOffice PDF 변환으로 모든 열이 다 보이는지 확인할 것** — "openpyxl로
읽었을 때 값이 있다"는 "PDF에 보인다"의 증명이 아니다.

**예외 — `build_outbound_qr_labels_excel()`(QR 라벨 엑셀)은 fit-to-page를 안 쓴다
(2026-09-15)**: 위 원칙과 달리 이 함수는 세로 A4 + 고정 인쇄배율 95%(`ws.page_setup.scale
= 95`)만 쓰고 `fitToWidth`/`fitToHeight`/`pageSetUpPr.fitToPage`는 아예 설정하지 않는다.
사용자가 실제 엑셀에서 인쇄까지 해보고 확정한 설정을 그대로 옮긴 것 — 한 줄에 2세트
(S/N+QR)를 나란히 두는 배치가 예전엔(가로 방향+폭맞춤 없이) 페이지가 갈라지는 버그가
있었지만, 이 고정배율 95% 설정으로는 실측(LibreOffice PDF, 4개/15개 항목 두 케이스)
결과 페이지가 안 갈라지는 게 확인됐다. **이 함수를 또 고칠 일이 있으면 "fit-to-page가
없다"고 버그로 오인해서 함부로 되돌리지 말 것** — 의도된 예외다.

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
  조합형빌더, 기준서정보 편집카드, 선택삭제 버튼도 표 위쪽). **2026-09-07부터 `is_new`
  플래그로 신규 자재 등록(`spec_new`, 메뉴 "자재 개별등록")도 겸함 — 15절 참고.**
- `spec_import.html` / `spec_import_result.html` — 규격 엑셀 자동파싱 업로드(그룹묶기
  체크박스 포함). **2026-09-07부터 메뉴에서 제거**(15절 참고) — 라우트는 살아있음.
- `spec_quick_add.html` — 메뉴 이름 "자재 일괄등록"(2026-09-07 개명, 15절 참고) —
  스프레드시트+측정조건 팝업+이전자재 불러오기(드래그다중선택)+선택자재 일괄조건적용
  (배치모드, 충돌확인 2단계)
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
| `report_builder._place_photos_in_area(...)` | report_builder.py | NCR 사진배치와 출고 엑셀 사진배치가 각자 구현될 뻔함(2026-09-15) |

`build_specs_with_sample()`은 `sample_qty`(AQL·입고수량으로 계산)와
`no_limit`(규격 미입력 여부)를 붙여준다. 검사 입력/상세/재검사/시간계산이 전부 이걸 쓴다.

`_place_photos_in_area(ws, photo_paths, col_widths_emu, row_heights_emu, col_s, row_s, PILImage)`는
"사진 N장을 지정 영역 안에 가로로 등분해서 비율 유지 최대크기로 배치"하는 계산을 한 곳에
모은 것(2026-09-15, 출고 이력 엑셀에 사진 임베드를 추가하면서 `_insert_ncr_photos()`에서
추출) — `_insert_ncr_photos()`(불량현상사진, NCR 통보서)와 `build_outbound_excel()`(출고
이력, 항목별 인디케이터/본체 사진 칸) 둘 다 이 함수 하나를 쓴다. **새로 "사진 여러 장을
칸에 나눠 넣는" 화면을 만들 때 이 함수부터 재사용을 검토할 것.**

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

**부적합 통보서(NCR) 확인 — 2026-09-10부터 최종결정권자 게이트를 안 쓴다(사용자 확정,
되돌리지 말 것)**. `ncr_confirm` 라우트는 `@perm_required("ncr_confirm")` 권한만
있으면 통과하고, `_can_make_final_decision()`은 더 이상 호출하지 않는다 — 서명
요구는 그대로 유지된다(승인/특채/불합격과 달리 NCR은 최종결정권자 지정 여부와
무관하게 `ncr_confirm` 권한자면 누구나 확인·발송 가능). 이전엔 승인 라우트와 같은
게이트를 썼는데, `_can_make_final_decision()`의 폴백 로직(최종결정권자 미지정 시
'승인' 권한만으로 통과)이 실제로는 그 권한 체크가 빠져 있어서(20절 참고)
`ncr_confirm` 권한만 있고 `approve` 권한은 없는 사람도 NCR을 확인·발송할 수 있는
구멍이 있었다 — 이걸 막느니 애초에 이 게이트 자체가 필요 없다고 사용자가 판단해서
아예 뗐다. 서명 이미지는 `ncr.confirm_signature`에 경로로 저장돼 통보서 하단
"최종결정권자 승인" 칸에 그대로 표시된다(칸 이름은 안 바꿨다 — UI 문구 변경은
별도 요청 시 처리). 서명 파일명은 `static/signatures/ncr{id}.png`
(성적서 서명 `{inspection_id}.png` 와 겹치지 않게 접두어를 붙임).

**2026-09-15 추가 — 서명 필수 요구를 없앴다(사용자 확정, 위 문단의 "서명 요구는
그대로 유지된다"를 뒤집음).** `ncr_confirm()`이 서명 누락을 더 이상 에러로 막지
않는다 — 캔버스 서명/업로드는 여전히 선택적으로 할 수 있지만, 아무것도 안
넣고 확인해도 통과한다(뭔가 입력했는데 저장 자체가 실패한 경우만 에러로 막음).

**같은 조사 중 위 문단의 설명 자체가 이미 현재 코드와 안 맞다는 것도 확인했다:**
`report_builder.build_ncr_excel()`은 `confirm_signature`를 전혀 참조하지 않고
"결 재 득" 칸도 라벨+테두리만 그리고 아무 값도 안 채운다. `templates/ncr_detail.html`엔
애초에 `ncr_confirm` 라우트로 보내는 버튼이나 서명 패드가 없다 — 실제 "발송" 버튼
("이메일 보내기"/"메일 파일 발행")은 `ncr_send_email`/`ncr_eml`을 직접 호출해서
draft→sent로 곧장 바꾼다. 즉 `/ncr/<id>/confirm`은 어떤 화면에서도 링크되지 않는
고아 라우트였다 — 이번 서명 필수 제거는 이 라우트 코드 자체에만 적용했다
(재연결하거나 라우트를 지우는 건 이번 스코프 밖).

**대시보드 "NCR포함" 불량률/건수율 지표(8-2-6절 참고)의 "발송됨" 판정 기준은
`status IN ('sent', 'confirmed')`다** — 실사용 흐름은 `mark_ncr_email_sent()`가
draft에서 곧장 `sent`로 바꾸는 것뿐이지만, 고아 라우트 `ncr_confirm()`이 실제론
`status='confirmed'`로 바꾼다는 걸 quality-watcher 검증 중 발견했다(스펙 문서엔
'sent'로 바뀐다고 잘못 적혀있었음). 지금은 이 라우트가 어디서도 안 불려서 실제로
`confirmed` 상태 NCR이 생길 일이 없지만, 나중에 이 라우트가 화면에 재연결되면
`sent`만 보는 집계 SQL에서 조용히 누락될 뻔한 걸 방어적으로 막아둔 것 —
`quality_report()`의 두 SQL(연결형 `ncr_sent_count` 서브쿼리, 수기입력 NCR 조회)
전부 이 두 상태를 같이 본다.

### 8-2-1. AQL 표기는 여러 줄 (2026-08-23, 2026-09-08 두 차례 표기 개선)

성적서 AQL 칸(C열)은 (Ac 있는 항목 기준) **3줄**로 나간다:
```
[4]
불량 1개
까지 합격
```
(무결점 판정 항목이면 `[0.65]` / `무결점` 처럼 둘째 줄이 "무결점"만 남는다 — 2줄)

`judge_numeric(raw_value, lower, upper, allowed_defects)`이 실제로 `allowed_defects`
(=Ac, `aql_ac_allowance()`가 KS Q ISO 2859-1 표준표로 계산)까지는 불량이어도 "합격"
처리한다 — 즉 표기와 실제 판정이 정확히 일치한다. 둘째·셋째 줄은 그 Ac값을 사람이
바로 알아볼 수 있게 풀어쓴 것뿐이다.

**"Ac1 이내 합격"이라고 그대로 썼다가 실사용자가 반대로 오해했다(2026-09-08)** —
"불량 3개면 합격이냐"고 되물음. "Ac"라는 전문용어를 모르면 "이내"가 어느 방향인지
헷갈릴 수 있다는 뜻이라, **"불량 N개까지 합격"**으로 완전히 풀어썼다. 이 문구를 다시
줄이거나 전문용어(Ac, Re 등)로 되돌리지 말 것 — 비전문가 가독성이 확인된 문구다.

**"불량 N개"/"까지 합격" 사이 줄바꿈은 반드시 코드에서 `\n`으로 직접 지정한다** —
`wrap_text`의 자동 줄바꿈에 맡기면 셀 폭에 따라 "불량 1"/"개까지"/"합격"처럼 단어
중간을 끊어버렸다(2026-09-08 재피드백). 이 지점을 다시 하나의 문자열로 합쳐서
자동 줄바꿈에 맡기지 말 것.

**Ac가 두 자리 이상(예: 12)이면 폰트를 9pt로 줄인다**(`_AQL_FONT_SIZE_SMALL`,
기본은 `_AQL_FONT_SIZE_NORMAL`=11pt) — "불량 12개"가 좁은 C열 폭에서 답답해 보이는 걸
막기 위함(2026-09-08 확정). 이때 줄바꿈 추정용 `chars_per_line`도
`_AQL_COL_CHARS_PER_LINE_SMALL`로 같이 바꿔야 행 높이 계산이 안 어긋난다.

첫 줄은 `[숫자]`처럼 대괄호로 감싸고, "샘플 N개"는 안 넣는다 — 샘플 수량은 바로 옆
D열("샘플 수량")에 이미 따로 나오는데 여기서 또 반복하면 중복이다.
이 텍스트를 만드는 자리는 `report_builder.py`의 `_fill_sheet()` 안, `r.get("aql")`
분기 하나뿐이다(기준서 쪽엔 이 표기가 없음).

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

**2026-09-15 추가 — 대시보드에 'NCR포함' 불량률(수량기준)/불량건수율(건수기준)
지표 2개를 신설했다.** 기존 불량률(성적서 판정 기준)은 그대로 두고 병행 표시.
발송·확인(status가 'sent' 또는 'confirmed')된 NCR이 딸려있지만 아직 성적서 자체는 불합격 확정이 안 된
로트(합격/특채/미결)를 추가로 반영한다. 이중집계 방지를 위해 이미 불합격
확정된 로트는 또 안 더한다. 자세한 알고리즘은 `_ncr_extra_stats()`(database.py)
참고. **업체 월간 성적표(8-2-7절)에는 아직 이 지표를 노출하지 않는다** — 대외
문서라 신중히 판단할 사안으로 남겨둠.

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
- **QMS 개선 백로그** — 2026-09-17 정밀진단(Artifact 리포트)에서 나온 개선항목 16개를
  `docs/qms-audit-backlog.md`에서 체크리스트로 추적 중(상7·중5·하4). 사용자가 "개선될
  때마다 정리하는 식으로 가자"고 명시적으로 요청한 살아있는 문서다 — 이 항목 중 하나를
  구현하면 반드시 그 파일의 체크박스를 채우고 완료일·커밋해시를 적을 것. 새로 발견되는
  개선사항도 여기 CLAUDE.md가 아니라 그 백로그 파일에 추가한다.

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

## 11. 품질 검증 규칙

템플릿(.html), 파이썬 코드, 엑셀 자동화 로직 등 iqc-app의 실행 가능한 산출물을 수정하거나 새로 만든 뒤에는,
작업 완료 보고를 사용자에게 하기 전에 반드시 quality-watcher 서브에이전트를 호출해서 검증받는다.

- 호출 예: `quality-watcher 에이전트로 방금 수정한 [파일명] 검증해줘`
- quality-watcher가 "결함 있음"으로 판단하면, 사용자에게 보고하기 전에 먼저 결함을 수정하거나 사용자에게 결함 내용과 함께 알린다.
- quality-watcher가 "확인 필요"로 판단한 항목은 결함으로 단정하지 말고, 사용자에게 그대로 전달해서 판단을 맡긴다.
- 단순 오타 수정, 문서(README 등) 편집처럼 실행에 영향 없는 변경은 quality-watcher 호출을 생략해도 된다.
- **CSS/시각적 변경은 quality-watcher 코드리뷰만으로 "완료"라고 하지 말 것** (2026-09-10,
  superpowers `verification-before-completion` 스킬 도입 계기). quality-watcher는 코드가
  스펙과 일치하는지는 보지만 "실제로 그렇게 보이는지"는 못 본다. Flask test client로
  `status_code == 200` 확인한 것도 "렌더는 된다"만 증명하지 "의도대로 보인다"는 증명이
  아니다 — 실제로 회전 애니메이션 방향이 반대였던 버그, CSS 마스킹이 실기기(Whale
  브라우저)에서 깨져서 혜성 모양 얼룩이 보이던 버그 둘 다 HTTP 200 확인만으로는 못
  잡았고 사용자의 화면 녹화로만 발견됐다. **렌더링 결과를 직접 보지 않고는 "됐습니다"라고
  단정하지 말고, 안 봤다는 것 자체를 사용자에게 알릴 것** (17절의 헤드리스 크롬 캡처
  방법을 CSS 레이아웃뿐 아니라 애니메이션류 확인에도 먼저 쓸 것 — 배포 후 사용자 피드백에
  의존하는 사이클을 기본값으로 삼지 말 것).
- **엑셀에 이미지를 삽입/배치하는 로직은 LibreOffice 검증만으로 "됐다"고 하지 말 것**
  (2026-09-15, 7-4-2절 참고). `OneCellAnchor`+`ext`로 넣은 이미지가 실제 윈도우 엑셀에서는
  앵커된 셀 전체를 꽉 채워버리는(지정한 크기 무시) 실제 버그가 있었는데, 이 세션이
  LibreOffice로 수차례 "실측 검증"했을 때도 매번 정상으로 보여서 못 잡았다 — 이미지
  배치·크기가 걸린 엑셀 출력은 **PowerShell+Excel COM 자동화로 진짜 엑셀에서도 인쇄
  결과를 확인할 것**(7-4-2절에 스크립트 예시 있음). 표/폰트/색상 등 이미지가 없는
  엑셀 로직은 LibreOffice 검증으로 충분하다 — 이 항목은 "이미지 삽입"에 한정된 예외.

## 12. 업체(suppliers) 담당자 역할별 다중 등록 (2026-09-07 신규)

업체 관리(`/suppliers`)에 검색 기능을 추가하고, 담당자를 **역할별(영업/품질/구매/기타)로
여러 명** 등록할 수 있게 확장했다. NCR·업체 성적표 발송 시 문서 종류에 맞는 담당자
이메일이 자동으로 기본값에 채워진다.

- `supplier_contacts` 테이블 신설(`database.py`) — `supplier_name`, `role`, `contact_name`,
  `phone`, `email`, `notes`, `sort_order`. 한 업체에 같은 역할로 여러 명 등록 가능.
- **기존 단일 필드(`suppliers.contact_name`/`contact`/`contact2`/`email`)는 삭제하지
  않고 폴백용으로 유지한다.** `get_default_contact()`가 담당자 미등록 업체에서는 이
  레거시 필드로 자동 폴백한다.
- **마이그레이션** `ensure_supplier_contacts_migration_20260907()` — 1회성, 멱등(설정
  플래그 `supplier_contacts_migrated_20260907`). 기존 데이터가 있는 업체마다
  `role="기타"` 담당자 1건만 생성한다. **"김준범 사장, 장진우 대리"처럼 한 칸에 여러
  명이 섞인 값은 자동으로 쪼개지 않고 원문 그대로 넣는다** — 잘못 분리해서 이름과
  전화번호가 엉뚱하게 매칭되는 사고를 막기 위한 의도적 결정이다. `contact2`(보조연락처)는
  새 담당자를 만들지 않고 `notes`에 "보조연락처: {값}"으로 병기한다.
- `get_default_contact(supplier_name, purpose)` — `purpose`는 `"ncr"` 또는 `"report"`.
  role 우선순위: NCR은 `[품질, 영업, 구매]`, 성적표는 `[영업, 품질, 구매]` 순으로 찾고,
  없으면 등록된 담당자 중 첫 번째, 그마저 없으면 레거시 필드로 폴백한다. `ncr_detail()`·
  NCR EML 라우트·`supplier_report_detail()` 세 곳 모두 이 함수 하나로 통일했다.
- `search_suppliers(query)` — 필드별 드롭다운 없이 **단일 자유텍스트**로 업체명·주소·
  취급품목·레거시 연락처·담당자(역할/이름/전화/이메일) 전체를 LIKE로 훑는다.
  `spec.html`의 필드 선택형 검색과 의도적으로 다른 방식이다(업체는 필드가 적고
  비개발자가 "아무 정보로나 찾기"가 더 직관적이라는 판단).
- 화면: `/suppliers/<name>`(신규, `supplier_detail.html`)에서 `spec_detail.html`과 같은
  인라인 AJAX 방식으로 담당자 추가/수정/삭제.
- **역할 배지에 빨간색을 쓰지 않는다** — 빨간색은 이 시스템 전체에서 "규격이탈/오류"
  의미로 예약돼 있다(7-6절, 상단 문서 참고). 디자인 작업 시 이 색상을 다른 용도로
  재사용하지 말 것.

## 13. QMS 서브에이전트 체계

11절의 quality-watcher 외에 역할별 서브에이전트 4개가 더 있다(`.claude/agents/*.md`에
정의, 자세한 내용은 각 파일 참고). 전체 설계 배경과 실전 검증 이력은 claude.ai의
별도 세션에 있고, 여기는 실행 시 알아야 할 것만 요약한다.

| 에이전트 | 역할 | 개입 방식 | 도구 |
|---|---|---|---|
| `planner` | 새 기능/화면 변경 요청을 구체적 스펙으로 정리 | 자동(기능 추가·변경 요청 시 최우선 호출) | 읽기전용, `writing-plans`·`codebase-design` 스킬 |
| `developer` | 스펙을 실제 코드로 구현 | planner 이후 | Edit/Write + 제한된 Bash(테스트·git 조회만, `pip install`·`git commit`·`git push`·`rm -rf` 등은 금지), `systematic-debugging`·`verification-before-completion`·`codebase-design`·`receiving-code-review` 스킬 |
| `designer` | 화면의 시각적 표현(타이포그래피·여백·색상·접근성) 개선 | developer 완료 이후, 항상 순차(병행 안 함) | Edit/Write, `frontend-design`·`frontend-design-audit` 스킬 |
| `quality-watcher` | 산출물 검증(11절 참고) | 코드 수정 완료 보고 전 자동 | 읽기전용 |
| `reuse-scout` | 여러 화면 간 기능 불일치(검색·삭제 등이 화면마다 있거나 없음) 탐지 | 수동 호출("페이지 통일성 확인해줘" 등) | 읽기전용 — **발견만 하고 직접 고치지 않는다** |

**대개편·구조 변경 시 planner 스폰 규칙 (2026-09-20)**: 신기능 대거 추가, 화면 전반
개편, DB 스키마 변경 등 구조적 변경이 포함된 요청에서 planner를 스폰할 때는 프롬프트에
.collab/README.md와 `.collab/events/`의 최신 Record/Evidence를 명시적으로 포함해 읽게 해야 한다 —
현재 협업 결정·검토·감사·인계 이력을 모르면 이미 확정된 설계를 다시 논쟁하거나 어긋난 방향으로
스펙을 짤 수 있다. 과거 결정의 원문이 필요할 때는 `docs/archive/`의 역사 자료를 추가로 지정한다.
developer 스폰 프롬프트에도 planner가 "협업 기록 참고"를 명시했으면 같이 포함한다.

**.collab 워크플로우 연결 (2026-09-20)**: 사용자가 터미널 또는 대화에서 Claude 또는 Codex 중 한 메인 에이전트에게 기능·화면·동작 변경을 직접 지시하면, 지시받은 actor만 기존 `planner → developer → (필요 시 designer) → quality-watcher` 순서를 바꾸지 않고 `.collab/qms-audit.ps1` 워크플로우로 이력을 묶는다. 지시받은 메인 actor는 해당 사용자 지시가 확인된 직후 `workflow-authorize --actor <claude|codex> --scope ... --user-request ...`를 자동 실행해 1회성 `WORKFLOW_AUTHORIZATION` DECISION을 남기고, 그 Record를 `workflow-start --actor ... --authorization-record ...`에 지정한다. 이 Record는 사용자 직접 지시의 운영상 근거이며 입력자를 기술적으로 증명하지 않는다. Claude·Codex·서브에이전트는 사용자 직접 지시 없이 자신을 위해 승인 Record를 만들거나 사용자 지시를 추정해서는 안 된다. actor·scope·사용자 지시가 정확히 일치하고 아직 사용되지 않은 v2 직접 지시 승인만 시작할 수 있다. v1 secret 기반 승인은 역사 보존만 하며 새 시작에는 사용할 수 없다. 이 과정은 다른 actor의 기록을 읽고 인계·검토하는 것을 허용하지만, 기록만으로 자동 작업을 시작하게 하지는 않는다.

시작 시 `TASK_ID`와 기준 범위·필수 역할·시각/E2E 검증 여부를 기록한다. 역할마다 `workflow-dispatch`가 출력한 `ARCHIVE_CONTEXT`를 위임 프롬프트에 그대로 포함한다. `RUN_ID`는 포함하되 capability token은 절대 포함하지 않는다. 서브에이전트는 CLI를 직접 쓰지 않고 `ARCHIVE_RESULT` 최종 보고만 내며, 지시받은 메인 actor가 `workflow-result`로 한 번만 수집한다. 역할별 dispatch와 각 REQUEST 결과도 task/run 안에서 한 번만 기록된다. 모든 필수 결과 뒤에만 `workflow-finalize`를 실행한다.

`workflow-finalize`는 planner/developer/quality-watcher 등 시작 시 정한 필수 역할 결과, quality-watcher PASS, 시각 변경의 실제 렌더링 근거, 필요 시 end-to-end 근거를 확인한다. 하나라도 없거나 FAIL 결과·활성 ISSUE·PARTIAL/UNKNOWN 근거가 있으면 `COMPLETED`를 만들지 않고 `PARTIAL` STATE와 차단 근거를 남긴다. ISSUE 해소는 원본 ISSUE를 수정하지 않고 현재 TASK 범위의 별도 STATE에 `issue_record_id`, `issue_resolution`, 해결 Evidence를 연결해 남긴다. 이후 세션 재개가 필요하면 기존 HANDOFF 절차로 인계한다.

이 연결부는 Claude와 Codex에 `.collab workflow orchestration only` 범위의 기록 종합 권한만 부여한다. 시작 시 UUID `RUN_ID`를 발급하고, 런타임 SQLite에는 capability token의 해시만 저장한다. 원문 token은 `workflow-start` 출력 뒤 해당 메인 세션의 비밀 문맥 또는 환경에만 보관한다. 후속 명령은 task·run·runner·token·활성 작업 상태를 검사한다. 이 token은 운영 문맥을 구분하는 최소 보호이며 OS 차원의 서브에이전트 프로세스 신원을 증명하지 않는다. 따라서 token을 어떤 서브에이전트 프롬프트·보고·파일에도 전달하지 않는다. QMS 파일·DB 변경은 사용자 직접 작업 지시 및 작업별 승인 범위로만 가능하며, commit·deploy는 항상 각각의 별도 사용자 지시와 승인 Record가 필요하다. 사용자 터미널의 일반 `run` 기록 동작은 그대로 유지한다.

**개발-디자인 경계**: developer는 기능이 동작하는 최소 마크업까지만 작성하고 시각적
스타일은 임의로 꾸미지 않는다. designer는 CSS·레이아웃·타이포그래피만 수정하고
HTML의 기능 구조(폼 필드, JS 로직, DOM id)는 건드리지 않는다 — 구조 변경이 필요해
보이면 직접 고치지 않고 사용자에게 보고한다.

**커밋 경계**: developer·designer 둘 다 `git commit`/`git push` 권한이 없다. 커밋은
항상 메인 세션(사람이 메시지를 확인하며)이 담당한다.

**기억할 만한 내용은 보고서에 표시할 것 (2026-09-10)**: 서브에이전트(planner/developer/
designer/quality-watcher/reuse-scout)는 메인 세션과 별개로 새로 시작되기 때문에, 메인
세션이 갖고 있는 사용자 개인 메모리(auto-memory, 프로젝트 폴더 밖에 있음)를 전혀 모른다
— 그래서 작업 중에 이 프로젝트 코드가 아니라 **"이 사용자와 어떻게 일해야 하는지"에
관한 교훈**(예: 특정 방식을 명시적으로 싫어함/좋아함, 검증 습관, 반복되는 요청 패턴 등)을
발견하면, 그냥 지나치지 말고 최종 보고 맨 앞에 `💾 기억할 만한 내용:` 줄로 따로 표시해서
올려라. 메인 세션이 그걸 보고 실제로 메모리에 저장할지 판단한다(서브에이전트가 직접
저장하지 않는다 — 대부분 읽기전용이고, 쓰기 권한이 있어도 이 프로젝트 폴더 밖에 있는
개인 메모리 디렉터리에 함부로 쓰면 안 된다). 이 프로젝트 자체의 코드/설계 관련 사실은
평소처럼 CLAUDE.md에 직접 적어두면 된다 — 이 규칙은 "사용자에 대한" 교훈에만 해당.

**디자인 감사 범위**: designer의 `frontend-design-audit`은 사용자가 "전체 감사"를
명시적으로 요청한 경우에만 프로젝트 전체를 대상으로 하고, 이때는 리포트만 생성한 뒤
우선순위를 제시하고 사용자 승인을 받은 화면부터 한 번에 하나씩 수정한다. 일반 호출
(개발 완료 후 자동 개입)은 이번 작업에서 변경된 파일만 대상으로 한다. **iqc-app
전체 화면 대상 최초 감사는 아직 실행 전이다** — PROGRESS.md 참고.

**서브에이전트 스킬 재배치 (2026-09-16)**: superpowers/mattpocock-skills/understand-anything
등 여러 플러그인이 전역 설치된 뒤, 5개 에이전트 각각에 어떤 스킬을 `skills:` frontmatter로
preload할지 전수 검토했다. **`skills:` 필드는 권한 부여가 아니라 preload(먼저 컨텍스트에
주입)일 뿐**이라, 그 필드가 없어도 이미 켜진 스킬은 여전히 Skill 도구로 호출 가능하다 —
이건 "쓸 수 있는 스킬을 좁히는" 작업이 아니라 "자동으로 먼저 챙기게 만드는" 작업이었다.

후보 전체(약 45개)를 역할별로 걸러 실제 SKILL.md 내용까지 읽어서 검증한 결과:
- **채택**: planner에 `codebase-design`(mattpocock, 얕은 모듈/깊은 모듈 용어집 —
  8-1절 "공용헬퍼로 중복 제거" 원칙과 같은 정신, 외부 의존성 없음). developer에
  `systematic-debugging`·`verification-before-completion`(둘 다 superpowers, 19절에서
  이미 이 프로젝트에 맞다고 검증됐던 것들을 preload로 못박음)·`codebase-design`·
  `receiving-code-review`(superpowers, quality-watcher 지적을 검증 후 반영하는 태도 —
  quality-watcher 자신이 아니라 그 지적을 **받는 쪽**인 developer에 붙이는 게 맞는 짝이었음,
  처음엔 quality-watcher 쪽으로 잘못 짚었다가 재검토로 바로잡음).
- **폐기(도구/인프라 불일치, 단순 취향 문제가 아님)**: `code-review`(mattpocock, Standards/Spec
  서브에이전트를 병렬 스폰하는 게 핵심인데 quality-watcher엔 Agent 도구가 없어 애초에
  실행 불가 + `docs/agents/issue-tracker.md` 필요한데 이 프로젝트엔 이슈트래커 자체가
  없음 + 출력포맷이 quality-watcher의 필수 포맷과 다름). `research`(mattpocock, "백그라운드
  에이전트를 스폰"이 핵심인데 planner도 Agent 도구 없음). `to-spec`(mattpocock, 이슈트래커
  필요 + **"파일 경로/코드 스니펫을 넣지 마라"고 명시**돼 있어서 planner.md 자체 규칙
  "developer가 다시 안 뒤지게 파일 경로를 빠짐없이 포함하라"와 정면 충돌). `domain-modeling`
  (mattpocock, CONTEXT.md/ADR 파일에 Write 필요 — planner는 Write 자체가 없고, developer가
  쓴다 쳐도 이미 CLAUDE.md 하나로 같은 역할을 하고 있어서 문서 체계가 둘로 쪼개짐).
  `diagnosing-bugs`(mattpocock, systematic-debugging과 목적 중복 + 회귀테스트 필수화가
  이 프로젝트의 "1회성 스크립트 검증" 관례보다 무거움).
- **보류(reuse-scout + understand-anything)**: understand-anything의 조회 스킬
  (`understand-chat`/`understand-diff`/`understand-explain`)은 전부 그래프 신선도 체크
  단계에서 `git rev-parse`/`git diff`(Bash)를 무조건 요구한다 — reuse-scout는 의도적으로
  읽기전용(Bash 없음)이라 이 스킬들을 못 쓴다. "그래프가 이미 존재한다"고 가정하고
  다시 계산해봐도(빌드비용 상쇄) 결론은 안 바뀌었다: Bash를 열면 생기는 도구표면 리스크
  (deny목록에 안 걸리는 파일변조 경로, repo 밖 읽기, "구조적으로 못 함"→"안 하길 바람"으로
  보장 약화, 문서-실제권한 불일치)는 그래프 유무와 무관하게 그대로고, 거기에 "그래프가
  낡으면 오탐/누락"이라는 새 리스크까지 얹힌다. 게다가 reuse-scout의 실제 일(화면별
  기능 유무 비교)은 grep으로 이미 충분히 잘 되는 유형이라 그래프의 강점(의존성 체인)을
  크게 못 살린다. **패턴 제한 allow-list**(예: `Bash(git rev-parse*)`만 허용)로 좁히는
  대안도 검토했으나, 호출 빈도가 낮은 에이전트에 영구적으로 남는 리스크 대비 이득이
  불분명해서 최종 보류 — reuse-scout 호출 빈도가 늘거나 구체적 불편이 확인되면 재검토.

### 13-1. 실제 사고 — `workflow-dispatch` 계열이 세션 내내 전면 누락됐었다 (2026-09-21)

사용자가 "아카이브에 다 기록되고 있는거 맞지?"라고 물어서 확인해보니, **아니었다.** 그 세션에서
planner→developer→quality-watcher를 여러 차례(출고 확인 정책, 검사자/등록자 구분, 스티커
참고 팝업 등) 돌려서 실제 기능 9개를 커밋·배포까지 했는데, `.collab status`로 확인하니
그 세션 관련 TASK가 단 하나도 없었다 — 가장 최근 TASK가 전날(9/20) "workflow smoke test"
(말 그대로 메커니즘 자체를 테스트한 것이지 실사용이 아님)에서 멈춰 있었다.

**원인(직접 확인)**: `GIT_COMMIT`/`DEPLOY`는 `pre-commit`/`pre-push` git hook이 **기술적으로
차단**한다 — `git-commit-authorize`/`deploy-authorize`를 안 거치면 커밋·푸시 자체가 그냥
실패해서 어쩔 수 없이 매번 지켰다. 반면 `workflow-authorize`/`workflow-start`/
`workflow-dispatch`/`workflow-result`/`workflow-finalize`는 **Agent 도구 호출을 막는
장치가 전혀 없다** — 순수하게 "매번 기억해서 스스로 호출해야 하는 관례"일 뿐이라, 여러
기능을 연달아 처리하는 동안 실제 산출물(코드 커밋)에 집중하느라 매번 빠뜨렸다. 12절의
"인가 경로는 두 갈래다(TBD-0006, 의도된 분리)"가 정확히 이 비대칭의 근거였는데, 그
비대칭이 실제로 이런 누락으로 이어진 첫 실증 사례다.

**조치**:
- 이미 끝난 그 세션의 커밋 9건(baseline `85ae32f` 이후)은 `.collab` TASK #7로 **사후
  요약**만 남겼다(`start`/`end`, `workflow-*` 아님 — 실시간 dispatch가 아니었다는 걸
  요약 안에 명시했음, 없었던 일을 있었던 것처럼 꾸미지 않음).
- **이제부터 Claude·Codex 양쪽 다, planner/developer/quality-watcher/designer/reuse-scout
  서브에이전트를 하나라도 부를 일이 있으면 반드시 그 호출 "직전"에 실시간으로**
  `workflow-start`(작업당 최초 1회) → 역할마다 `workflow-dispatch` → 결과 받으면 즉시
  `workflow-result` → 전체 끝나면 `workflow-finalize` 순서를 지킨다. "나중에 몰아서
  기록하면 되지"라고 미루지 말 것 — 이번 사고가 정확히 그렇게 났다. `git-commit-authorize`
  를 매번 잊지 않고 지킨 이유가 "안 지키면 커밋이 실패해서"였다는 걸 기억하고, 이
  workflow 절차도 기술적 강제가 없다는 이유로 똑같이 가볍게 여기지 말 것.
- **상대 actor(Claude↔Codex)의 작업 이력·자기 자신의 과거 작업 이력 둘 다 이미
  조회 가능하다** — 같은 SQLite 원장(`​.collab/runtime/audit.sqlite3`)에 두 actor가 같이
  쓰기 때문에 별도 공유 장치가 필요 없다. `python .collab/qms_audit.py status --limit N`
  으로 최근 TASK 전체(actor 무관, 상태 포함)를 훑고, `search --query "<키워드>"`로
  특정 작업을 찾고, `current-state --task <ID>`로 특정 TASK의 최신 확정 사실을 본다.
  **다른 actor가 같은 파일을 이미 건드리고 있는지, 또는 본인이 예전에 이미 비슷한 걸
  했는지를 새 작업 시작 전에 `status`/`search`로 먼저 확인하는 걸 습관으로 만들 것**
  (특히 큰 구조변경 전에는 13절의 "planner에게 .collab/README.md와 최신 Record 읽히기"
  지시와 같은 이유 — 과거 결정을 모르고 재작업/충돌하는 걸 막기 위함).

## 14. 성적서 관련 4개 삭제버튼 — 전부 admin 전용 (2026-09-08 최종 확정, 되돌리지 말 것)

`templates/_admin_delete.html`의 `admin_delete_bar` 매크로는 `show` 인자로 노출 조건을
바꿀 수 있다(기본값은 `is_admin`). 이 삭제 버튼을 쓰는 4개 화면 전부 지금은 동일하게
**`show=is_admin`(admin 계정만)** 이다:

- 승인목록(`approve_list.html`)·승인이력(`approval_history.html`)
- 출력대기(`output_list.html`)·출력이력(`output_history.html`)

**변경 이력(헷갈리지 않도록 순서대로 적어둠)**:
1. 2026-09-07: 한 세션이 "승인 쪽은 admin 전용, 출력 쪽은 `history_delete` 권한 기반"으로
   **의도적으로 다르게** confirm하고, 그걸 "되돌리지 말 것"이라고 이 문서에 박아뒀었다.
   (그 이전에 이미 두 세션이 이 둘을 서로 반대 방향으로 여러 번 되돌린 이력이 있었음)
2. 2026-09-08: **사용자가 직접** "출력 대기/이력 삭제 권한도 관리자에게만 있어"라고
   명시적으로 지시해서, 출력 쪽도 admin 전용으로 통일했다. 즉 1번의 "의도적 차등"
   설계 자체가 사용자 지시로 뒤집힌 것이지, 세션이 임의로 되돌린 게 아니다.

**따라서 지금부터의 규칙**: 4개 화면 모두 `show=is_admin`으로 통일된 게 현재 최종
상태다. 이걸 다시 권한 기반으로 되돌리려면 반드시 사용자에게 먼저 확인할 것 — 이
문서만 보고 "예전엔 차등이었네, 원래대로 되돌려야겠다"고 판단하지 말 것. 각 파일의
`admin_delete_bar(...)` 호출 바로 위 주석에 이 문서의 이 절을 참고하라고 적어뒀다.

**일반적 교훈(계속 유효함)**: 같은 로컬 저장소(`C:\Users\Jaiden\Desktop\iqc-app`)를 여러
Claude 세션이 시차를 두고 건드릴 수 있고, 사용자 본인도 시간이 지나면서 결정을 바꿀 수
있다. 코드를 "고치기" 전에 `git log -p`로 관련 커밋 메시지를 먼저 읽고, 이 문서에
"확정/되돌리지 말 것"이라고 적힌 결정이라도 사용자가 지금 다르게 지시하면 **그 지시가
우선**이다 — 다만 그럴 땐 이 문서도 같이 갱신해서 다음 세션이 또 헷갈리지 않게 할 것.

## 15. 자재 메뉴 재구성 (2026-09-07) — 관리/개별등록/일괄등록 3개 체계로 정리

자재 관련 메뉴 이름이 실제 기능과 반대로 붙어 있던 걸 바로잡고, 신규 자재 등록
전용 화면(`spec_new`)을 새로 만들었다. **이름이 여러 번 뒤바뀌며 혼선이 컸던
결정이니, 다시 고칠 일이 있으면 아래 표부터 확인할 것.**

| 화면(route) | 이전 메뉴 이름 | 지금 |
|---|---|---|
| `spec_list` (`/spec`) | 자재 관리 | **그대로 "자재 관리"** — 이름도 기능도 안 바꿈. 목록 검색 + 자재 클릭 시 수정 화면(`spec_detail`)으로 진입 |
| `spec_new` (`/spec/new`, **신규 라우트**) | (없었음) | **새로 만든 "자재 개별등록"** — `spec_detail.html`을 `is_new=True`로 재사용한 화면. 자재번호/자재명만 입력해 저장하면 그 즉시 자재가 만들어지고 `spec_detail`(그 자재의 실제 편집 화면)로 리다이렉트된다. `material_edit` 권한 필요 |
| `spec_detail` (`/spec/<material_no>`) | (메뉴 없음) | 그대로 — 기존 자재 하나의 분류/기준서정보/전수검사/항목을 수정하는 화면. `is_new=True`로 렌더링되면 이 화면 대부분(분류~항목표~JS)이 숨겨지고 "자재번호/자재명 등록" 카드 하나만 보인다 |
| `spec_quick_add` (`/spec/quick_add`) | 자재 개별 등록 | **"자재 일괄등록"으로 개명** (기능은 그대로 — 붙여넣기 그리드로 여러 자재 한 번에 등록) |
| `spec_import` (`/spec/import`, 엑셀 업로드 자동파싱) | 자재 일괄 등록 | **메뉴에서 제거.** 라우트·권한(`material_import`)은 그대로 살아있어 URL로 직접 접속하면 여전히 동작한다. 실사용 빈도가 낮아 내린 결정이지 버그가 아니다 |

**왜 이렇게 됐는지**: `spec_import`(엑셀 업로드)는 이름이 "일괄등록"이었지만 실제로는
자재 하나하나의 정밀한 규격을 파싱하는 도구였고, `spec_quick_add`(붙여넣기 그리드)는
이름이 "개별등록"이었지만 실제로는 여러 자재를 한 번에 넣는 도구였다 — 이름과 실제
기능이 서로 바뀌어 있었다. 사용자가 이 불일치를 발견하고 이름을 실제 기능에 맞게
정리해달라고 요청했고, "자재 개별등록"이라는 이름에 걸맞은 화면(자재 하나를 새로
등록하는 화면)이 실제로 없어서 `spec_new`를 새로 만들었다.

**`spec_new`가 `spec_detail.html`을 그대로 재사용하는 이유**: 새 자재의 분류·기준서
정보·항목표 입력 UI를 통째로 새로 만들면 `spec_detail.html`과 로직이 중복돼서(8-1절
"중복을 만들지 말아야 할 공용 헬퍼" 원칙 위반) 한쪽만 고치고 다른 쪽을 놓치는 사고가
나기 쉽다. 대신 `spec_detail.html` 하나에 `is_new` 플래그로 분기시켰다:
- `is_new=True`일 때: "검사 이력 추적"/"과거 입고 이력" 버튼, 자재분류/기준서정보/
  전수검사/도면파일/항목표/새항목추가 카드와 그 아래 JS(Sortable, AJAX 저장 등)를
  전부 숨긴다 — 아직 존재하지 않는 자재라 이 기능들이 동작할 대상이 없기 때문.
  "자재번호/자재명 등록" 카드 하나만 남는다.
- 이 카드의 저장 폼은 `spec_new`(POST)로 보낸다(기존 자재를 고칠 때 쓰는
  `spec_material_rename`이 아니다) — `spec_new`가 자재번호 중복을 먼저 확인하고
  `db.upsert_material()`로 만든 뒤 `spec_detail(material_no=새번호)`로 리다이렉트하면,
  그 뒤부터는 기존 편집 화면이 아무 수정 없이 그대로 동작한다.
- **앞으로 `spec_detail.html`의 자재분류/기준서정보/항목표 관련 UI를 고칠 일이 있으면
  `is_new=True`로 렌더링했을 때도 안 깨지는지 같이 확인할 것** (예: `material`이
  `None`일 때 `material['category']` 같은 접근은 이미 `if material else None` 패턴으로
  방어돼 있음 — 새 필드를 추가할 때도 이 패턴을 유지해야 한다).

## 16. 색상 팔레트 · 폰트 리브랜딩 — 완료 및 마감 확정 (2026-09-08)

전체 디자인 감사(13절 마지막에 언급된 7개 화면 순차 개선) 이후, 사용자가 "태블릿을
하루 종일 보니 눈 피로를 최우선으로 고려해달라"고 직접 요청해서 색상·폰트 방향을
다시 잡았다. Artifact로 여러 차례 시안(타이포그래피 3종 → 역동형 → 채도 낮춘 버전)을
보여주고 사용자가 최종 승인한 방향은 다음과 같다:

- **폰트**: Gothic A1(구글폰트, 굵기 400/500/700/900) — 획이 둥글고 굵어서 기존
  맑은 고딕과 확실히 구분됨. 사용자가 "B안"으로 명시적으로 골랐다.
- **색상**: 채도를 확 낮춘 톤. 진한 파랑(#2563eb) 대신 차분한 남색(#3D5A8A). 배경도
  순백색 대신 톤 낮춘 회색(#ECEEF2/#F9F9F8) — "쨍하거나 밝은 색은 눈이 피로할 것
  같다"는 사용자의 명시적 요청.
- **판정 의미 색상(합격/불합격/보류 = --pass/--fail/--pending)은 그대로 유지** —
  이건 시스템 규칙이라 톤 낮추기 대상에서 제외했다.
- **"놓치기 쉬운데 중요한 상태"에는 작고 은은한 반복 애니메이션(예: 상태 점 깜빡임)을
  의도적으로 허용** — 사용자가 "채도만 낮으면 애니메이션은 오히려 강조돼서 좋다"고
  명시적으로 확인해준 원칙이다. 무조건 정적으로만 만들 필요는 없다는 뜻.

**진행 상태(중요 — 아직 다 안 끝남)**:
- ✅ **1단계 완료**: `base.html`의 `:root` 색상 변수(`--primary`/`--primary-dark`/`--bg`/
  `--card`/`--border`/`--text`)와 `body` 폰트를 위 방향으로 교체. Google Fonts 링크 추가.
  `input:focus`의 하드코딩된 rgba도 새 팔레트에 맞춰 같이 고침.
- ✅ **2단계 1차 완료(2026-09-08)**: 템플릿 전체에서 가장 눈에 띄던 "쨍한 구 브랜드
  파랑" 5종(`#2563eb`/`#1d4ed8`/`#1e40af`/`#3b82f6`/`#1a6faf`, 그 rgba 변형 포함)을
  33개 파일·104곳에서 새 남색 계열(`#3D5A8A`/`#2F4670`/`#24344F`)로 **리터럴 문자열
  치환**했다. 개별 화면마다 designer를 부르지 않고 스크립트로 일괄 처리한 뒤
  quality-watcher로 검증(명암비 WCAG AA 이상, 옛 색상 잔재 0건, 색이 JS 로직 분기에
  안 쓰였는지 확인) — 이런 "리터럴 값 치환"류는 이 방식이 화면별 개별 작업보다
  훨씬 빠르다는 걸 확인함.
- ✅ **2단계 2차 완료(2026-09-08)**: 회색/보더/어두운텍스트 계열 9종(`#e5e7eb`/
  `#d1d5db`/`#f3f4f6`/`#f9fafb`/`#f8fafc`/`#f1f5f9`/`#374151`/`#1f2937`/`#111827`,
  rgba 변형 포함)을 44개 파일·358곳에서 새 팔레트(`#DDE0E6`/`#ECEEF2`/`#F9F9F8`/
  `#2B3140`/`#24344F`)로 치환. `base.html` 자체의 헤더 그라데이션·드롭다운 메뉴
  배경도 이때 같이 정리됨(검정 계열 → 남색 계열). quality-watcher 검증 완료
  (명암비 계산상 충분, 이메일 발송용 `ncr_email_body.html`과 공용 부분템플릿
  `_pager.html`/`_drawing_modal.html`도 별도 확인).
- **여기까지 두 차례로 원래 1,734곳 중 462곳(파랑 104 + 회색/텍스트 358) 정리 완료.**
  **남은 건 "정리 안 한 게 아니라 의도적으로 안 건드린 것"이 대부분이다:**
  - 판정 의미 색상(`#dc2626`/`#16a34a`/`#d97706` 계열) — 시스템 규칙, 절대 건드리면 안 됨
  - "info"/주의/특채/검토 등 의미가 있는 뱃지·안내 색(연한 파랑 `#eff6ff` 계열,
    주황 `#92400e`/`#f59e0b` 계열, 보라 `#7c3aed` 계열, 주황(특채) `#ea580c` 등) —
    각 상태의 의미를 나타내는 색이라 톤을 낮추는 대상이 아님
  - `#9ca3af`/`#6b7280`/`#4b5563` — 이미 채도가 충분히 낮은 회색이라 손 안 댐
  - `#fff`(202곳) — 버튼 글자색·입력칸 배경 등 문맥상 흰색이 맞는 경우가 대부분이라
    일괄 치환 대상이 아니었음
  - 그 외 한두 화면에만 쓰이는 일회성 강조색(예: `approval_history.html`/
    `dashboard.html`의 "필터 칩" 인디고 `#6366f1`/`#3730a3`, `defect_history.html`의
    "검토" 레인 보라 `#8b5cf6`/`#5b21b6` — 이건 `.badge.review`(`#7c3aed`)와 같은
    계열이라 사실상 의미색, `custom_template_edit.html`의 어두운 톤 8~9종 —
    이건 커스텀 성적서 편집기 자체의 독립적인 다크 테마 UI라 앱 나머지 색상 체계와
    처음부터 무관함)
  **"디자인이 반만 바뀐 것 같다"는 얘기가 나오면, 위 목록에 해당하는 색인지부터
  확인할 것 — 대부분은 버그가 아니라 의도된 보존이다.**

**2026-09-08, 사용자가 색상 작업을 여기서 마무리하기로 확정함.** 위 두 차례(파랑
104곳 + 회색/텍스트 358곳)로 눈에 띄는 "쨍한 색" 문제는 실질적으로 해결됐다고
판단, 나머지 일회성 강조색까지 화면별로 계속 정리하는 옵션 대신 "여기서 종료"를
선택했다. **앞으로 이 작업을 다시 이어가라는 명시적 요청이 없으면, 위에 나열된
남은 색상들은 건드리지 말 것** — 미완성이 아니라 사용자가 마감을 확정한 상태다.
- Artifact 시안 자체(구글폰트 Gothic A1/IBM Plex Mono 조합, 역동형 레이아웃 등)는
  iqc-app 코드가 아니라 별도 미리보기 페이지였다 — 실제 화면에 옮길 때는 태블릿 뷰포트,
  기존 기능(폼/JS/권한 로직)과의 충돌 여부를 화면마다 다시 확인해야 한다.

## 17. `position: sticky`가 표 셀에서 안 먹을 때 — base.html의 공용 `table` 규칙부터 의심할 것 (2026-09-08)

검사 입력 화면(`inspect_form.html`)에서 "검사항목/규격" 열을 가로 스크롤 시 고정(엑셀
틀고정)하는 기능을 넣었는데, 처음 구현이 **실제로는 전혀 동작하지 않았다** — 코드
리뷰만으로는 멀쩡해 보였지만 실기기에서 스크롤해도 그 열이 안 붙어 있었다.

**겪은 과정(다음에 비슷한 버그 만나면 이 순서로 의심할 것)**:
1. 처음엔 `.inspect-table`에 `border-collapse: collapse`와 `overflow: hidden`이
   있길래 이게 원인이라 보고, `border-collapse: separate; border-spacing: 0;`으로
   바꾸고 `overflow: hidden`을 **지웠다.** → 여전히 안 됐다.
2. 원인: **`base.html`에 있는 범용 `table { ... overflow: hidden; ... }` 규칙이
   모든 `<table>`에 걸려 있다.** `.inspect-table`에서 `overflow` 선언 자체를
   지워버리면 "아무것도 안 남는" 게 아니라 **범용 규칙이 그대로 이어받아 적용된다.**
   즉 지우는 것만으로는 안 되고, **`overflow: visible`을 명시적으로 다시 선언해서
   범용 규칙을 덮어써야 한다.**
3. `position: sticky`를 쓰는 요소의 조상 중 하나라도 `overflow`가 `visible`이
   아니면(`hidden`/`auto`/`scroll` 전부 포함), 그 조상이 "가장 가까운 스크롤
   조상"으로 잡혀서 sticky가 **그 조상 기준으로 계산된다.** 우리가 원한 건 바깥의
   `overflow-x:auto` div를 기준으로 붙는 것이었는데, 그보다 안쪽(더 가까운)의 `<table>`
   자체가 `overflow:hidden`이라 **table 기준으로 sticky가 계산되면서(table 자체가
   스크롤과 함께 통째로 움직이니까) 사실상 아무 효과가 없어 보이는 것**이었다.

**확인 방법(코드만 보고는 못 잡음, 실제로 이렇게 확인했다)**: 이 환경엔 브라우저가
없어서, 로컬 Chrome(`C:\Program Files\Google\Chrome\Application\chrome.exe`)을
`--headless=new --screenshot=파일경로 --window-size=W,H` 로 직접 띄워서 렌더링
스크린샷을 찍었다. 추가로 페이지에 진단용 `<script>`를 주입해서(`window.onload`에서
대상 요소의 `getComputedStyle()`과 조상 전부를 순회하며 `overflow`/`position`/
`transform` 등을 화면에 텍스트로 찍어줌) 어느 조상이 문제인지 정확히 짚어냈다.
**앞으로 CSS 레이아웃(sticky, 겹침, z-index 등)이 코드상으론 맞는데 실제로 안
먹는 것 같으면, 말로 추측하지 말고 이 방식(headless Chrome 스크린샷 + 진단
스크립트 주입)으로 직접 확인할 것** — PowerShell 도구로 `& $chrome --headless=new
--screenshot=... file:///경로` 형태로 호출하면 된다(Bash 도구로 직접 exe를 부르면
경로/프로세스 처리가 불안정해서 PowerShell 쪽이 더 안정적이었다).

**2026-09-14 추가 — Bash 도구로도 잘 되는 방법을 찾음(Claude Code 세션)**:
- **Bash 도구의 샌드박스가 Chrome 서브프로세스 실행 자체를 막는다.** 그냥
  `chrome.exe --headless=new --screenshot=...`를 Bash로 부르면 겉보기엔
  `exit 0`인데 스크린샷 파일이 아예 안 생긴다(에러 메시지도 없이 조용히 실패).
  **`dangerouslyDisableSandbox: true` 옵션을 켜야 실제로 실행된다** — PowerShell을
  거칠 필요 없이 Bash로도 안정적으로 됨, 이 옵션이 핵심이었다.
- **`file:///` URL 경로는 반드시 윈도우 드라이브 표기(`file:///C:/Users/...`)여야
  한다.** Git Bash의 `/c/Users/...` 형식 그대로 붙이면(`file:///c/Users/...`)
  Chrome이 못 찾고 `ERR_FILE_NOT_FOUND`를 낸다(창은 뜨고 스크린샷도 "찍히지만"
  내용이 크롬 자체 오류 페이지라 안 걸러내면 놓치기 쉬움 — 스크린샷 파일 크기가
  화면마다 똑같이 나오면 이 오류를 의심할 것).
- **로그인이 필요한 화면을 캡처할 땐 브라우저 쿠키를 흉내낼 필요 없이, Flask
  `app.test_client()`로 로그인해서 받은 렌더링된 HTML을 로컬 파일로 저장한 뒤
  그 파일을 Chrome으로 스크린샷하는 방식이 제일 간단하다** (이 앱은 정적 자산이
  전부 CDN 절대경로 아니면 인라인이라 `<base href>` 보정도 필요 없었음 — 화면에
  `/static/...`처럼 로컬 상대경로 자산이 있으면 그때는 `<base href="http://127.0.0.1:5000/">`
  삽입이나 실서버 병행 구동을 고려할 것).

**2026-09-17 추가 — 위 "Bash로도 안정적으로 됨"이 이번 세션에서는 재현 안 됨,
PowerShell로 즉시 전환할 것**: `dangerouslyDisableSandbox: true`를 켠 Bash로
`chrome.exe --headless=new --screenshot=...`를 불렀는데(`file:///C:/...` 드라이브
표기도 정확히 지킴), 트리비얼한 1줄짜리 HTML조차 `exit 0`·에러메시지 없음·
스크린샷 파일 생성 안 됨으로 조용히 실패했다(`--no-sandbox`, `--user-data-dir`
명시, 구버전 `--headless` 등 여러 조합 다 시도해도 동일). **같은 명령을
PowerShell로 감싸서 호출하니(`powershell -NoProfile -Command "& 'chrome.exe' ..."`)
바로 정상 동작**했다 — 2026-09-08 addendum이 원래 하던 방식으로 되돌아간 셈.
환경마다(혹은 세션마다) Bash 경유 안정성이 달라지는 것으로 보임 — **다음에 이
방식이 또 조용히 실패하면(exit 0인데 파일이 없음) 원인을 깊이 파기 전에 바로
PowerShell 경유로 전환해서 시간을 아낄 것.**

**일반적 교훈**: `base.html`처럼 사이트 전체에 적용되는 범용 선택자(`table`, `button`,
`input` 등)에 `overflow`/`position`/`transform`처럼 자식 요소의 레이아웃 계산에
영향을 주는 속성이 있으면, 특정 화면에서 그 속성을 "끄고 싶을 때" 단순히 그 화면의
스타일에서 선언을 **지우면 안 되고 반대값으로 명시적으로 덮어써야 한다.** 이런
범용 규칙이 뭐가 있는지 먼저 `base.html`을 확인하는 습관을 들일 것.

**2026-09-16 추가 — 이 환경의 헤드리스 크롬은 `--window-size`를 신뢰할 수 없다,
좁은 폭(태블릿/폰) 검증은 iframe으로 우회할 것**: 출고 스캔 화면을 좁은 폭(390px)
에서 검증하려고 `--window-size=390,1600`을 줬는데, 페이지 안에 진단 스크립트를
심어서 `window.innerWidth`를 실제로 찍어보니 **요청한 390이 아니라 500~518px
근처로 나왔다**(재현할 때마다 조금씩 다름 — 원인 미상, 아마 이 세션 환경의 가상
디스플레이/최소창크기 제약으로 추정). `--force-device-scale-factor=1`을 추가해도
안 고쳐졌다. 큰 값(`--window-size=1234,999`)을 줘봐도 요청한 값 그대로 안 나오고
1210x900 근처로 클램프됐다 — 즉 이 환경에서 `--window-size`는 어느 정도만 참고되고
실제 렌더링 폭을 결정하지 못한다. **해결책: 확인하고 싶은 페이지를 `<iframe
style="width:390px;...">`으로 감싼 별도 래퍼 HTML을 만들고, 그 래퍼 페이지를
평소처럼 넉넉한 창 크기(예: 1200x2300)로 스크린샷하면, iframe은 자기 CSS 폭을
그대로 독립된 뷰포트로 써서 내부 문서가 정확히 390px 기준으로 렌더링된다** —
바깥 창 크기가 이 환경에서 얼마로 잡히든 iframe 내부는 영향받지 않는다. 앞으로
태블릿/폰 폭 레이아웃을 헤드리스 크롬으로 검증할 땐 `--window-size`를 좁게
주려고 하지 말고 처음부터 이 iframe 래퍼 방식을 쓸 것 — 실측(`getBoundingClientRect`)
없이 스크린샷만 보고 "폭이 안 맞아 보인다"고 판단하면 이번처럼 애먼 CSS를
의심하며 시간을 버릴 수 있다(실제로 이번에 새로 만든 패널 자체는 처음부터
문제가 없었고, 테스트 도구의 창크기 처리가 원인이었다).

동적으로 생성되는 화면(버튼 클릭 후 상태가 바뀌는 것 등)을 정적 스크린샷으로
확인하려면, `window.addEventListener('load', ...)` 안에 `setTimeout`으로 클릭을
예약하면 `--screenshot`이 그 타이머보다 먼저 캡처해버려서 반영이 안 될 수 있다
(가상시간 예산 문제와는 다른, 같은 계열의 타이밍 함정). **`<body>` 맨 끝에 둔
동기 `<script>`에서 곧바로 `.click()`을 호출하면**(별도 이벤트 리스너나
타이머 없이) HTML 파싱이 끝난 시점에 이미 버튼이 DOM에 있으므로 동기적으로
바로 실행되고, 그 결과가 `load` 이벤트 시점 스크린샷에 그대로 반영된다 — 상태
변화를 스크린샷으로 확인하고 싶을 때 이 패턴을 재사용할 것.

**2026-09-17 추가 — HTML `hidden` 속성은 그 요소에 `display`를 지정하는 CSS 규칙이
하나라도 있으면 조용히 무시된다.** 검사대기 목록의 "우선검사" 배지를 체크 안 됐을 때
숨기려고 `<span class="badge priority" {% if not r['is_priority'] %}hidden{% endif %}>`
처럼 HTML5 `hidden` 속성을 썼는데, 실제 배포 후 사용자가 스크린샷으로 "체크 안 해도
배지가 계속 보인다"고 지적해서 발견했다 — quality-watcher도 처음엔 "조건이 맞게
걸려 있다"고 코드만 보고 통과시켰던 부분이다(로직은 맞았지만 실제로 안 먹혔다,
11절 원칙이 또 유효했던 사례). 원인: 브라우저 기본(UA) 스타일시트엔
`[hidden] { display: none }`이 있지만, 이 프로젝트의 `.badge { display: inline-block; }`
같은 **작성자(author) CSS는 특정도가 같아도 UA 스타일시트보다 항상 이긴다** —
그래서 `hidden` 속성이 켜져 있어도 `.badge`의 `display: inline-block`이 그대로
적용돼 계속 보였다. **고치는 법**: `hidden` 속성에 기대지 말고, 감추고 싶으면
인라인 `style="display:none"`을 직접 준다(인라인 스타일은 클래스 규칙보다 항상
우선한다) — JS에서 토글할 때도 `el.hidden = true/false`가 아니라
`el.style.display = 'none'/'block'`으로 직접 제어할 것. **앞으로 어떤 요소에
`display`를 지정하는 CSS 규칙(`.badge`, `.btn` 등 거의 모든 공용 클래스가 해당)이
이미 있다면, 그 요소를 감출 때 `hidden` 속성을 쓰지 말 것** — 코드 리뷰만으로는
이 함정을 못 잡는다(quality-watcher도 놓쳤다), 반드시 실제 렌더링(헤드리스
크롬 스크린샷 등)으로 눈으로 확인할 것.

## 18. 통합BOM 계층 정보 — "자재 찾기" (2026-09-09, `assembly_masters`와 완전히 별개)

28개 모델 ERP BOM(Lv1~Lv5 계층)에서 자재별 소속 정보(어느 모델, 어느 Lv, 어느 상위품목코드
아래 있는지)를 뽑아 조회/필터링만 하는 기능. **5-0절의 `assembly_masters`/
`assembly_components`(입고 시 파츠 자동전개)와는 목적·테이블·라우트 전부 무관하다 —
헷갈려서 서로 건드리지 말 것.** 이쪽은 순수 조회용이라 입고·검사 흐름에 아무 영향이 없다.

- 테이블: `material_bom_links` (`material_no`/`parent_material_no`/`model_name`/`level`/
  `kind`/`qty_per_parent`/`qty_per_model`/`unit`/`source_row_no`). `materials`에 FK 없음
  (느슨한 문자열 연결 — 기존 관례, 화면에서 "자재 미등록"만 표시).
- **같은 자재가 여러 모델/여러 상위품목코드 아래 다른 Lv로 나타나면 대표값으로 뭉개지 않고
  전부 별도 행으로 저장한다**(사용자 확정).
- 임포트(`database.import_bom_from_excel()`, `/admin/import-bom`, `material_import` 권한):
  **재임포트 시 전량 삭제 후 재삽입**(부분 갱신 아님) — 최신 통합BOM 파일 전체를 매번
  올려야 한다. 엑셀 시트명 `"통합BOM"` 고정, 헤더 2행/데이터 3행부터.
- **단위가 `Pc`/`SET`/`EA`(대소문자·공백 무시)인 행만 연동 대상**이다. 그 외 단위(ml, g, M
  등)와 단위 없는 행, `구분`이 `완제품`인 행은 스킵한다.
- 조회: `database.search_bom_materials()`(신규, `search_materials()`와 별개 함수 —
  8-1절 원칙에 따라 책임 분리) → `/materials/find` 화면("자재 찾기"). Lv·모델 다중선택
  필터는 `_multi_arg()` 재사용(대시보드 8-2-13절과 같은 방식).
- 자재 상세(`spec_detail.html`)에 "BOM 계층 정보" 카드로도 보여준다(읽기 전용,
  `is_new=True`일 때는 숨김 — 15절의 `is_new` 가드 패턴 그대로 따름).

## 19. superpowers 플러그인 스킬 — 이 프로젝트에 적용할 지점 (2026-09-10 조사)

사용자 요청으로 superpowers 플러그인의 스킬 13개를 전부 읽고 이 프로젝트의 실제 작업
방식과 대조했다. 대부분은 이미 있는 project 서브에이전트 체계(13절: planner/developer/
designer/quality-watcher/reuse-scout)와 겹치거나 이 프로젝트 규모(1인 유지보수, 매
작업이 파일 1~7개 규모로 단발성 끝남)엔 과하다. **아래 항목만 실제로 자동 적용 대상.**
나머지(writing-plans/subagent-driven-development/executing-plans/dispatching-parallel-agents/
brainstorming/test-driven-development/writing-skills)는 "안 맞음"으로 결론 — 진짜 큰
신규 기능(9절의 보류된 "자유 양식 성적서 제작기" 등)이 나올 때만 필요시 꺼내 쓸 것.

- **verification-before-completion** — 11절에 규칙으로 편입함(위 참고). 가장 확실한 적용
  대상이었다: HTTP 200 확인만으로 "완료" 선언했다가 실기기에서 시각적으로 깨진 걸 두 번
  놓쳤다.
- **systematic-debugging(1단계, 증거 먼저 수집)** — 17절에 이미 있던 원칙(헤드리스 크롬
  캡처로 먼저 확인)과 같은 정신인데 이번 세션에서 재사용을 놓쳤다. CSS 레이아웃뿐
  아니라 애니메이션/시각효과 버그에도 배포 전에 먼저 적용할 것.
- **using-git-worktrees** — "해봐야 결과를 알 수 있는" 실험적 CSS 기법(예: mask-composite
  같은 실기기별 지원이 갈리는 기법)을 시도할 때만 워크트리에서 먼저 만들고 확인 후
  main에 반영하는 걸 고려할 것. 일반적인 작업(자재 데이터 수정, 기능 추가 등)까지 매번
  워크트리를 쓰는 건 과함 — 이 프로젝트는 사용자가 빠른 반영을 기대하는 내부 도구라
  기본은 계속 main 직접 작업+즉시 배포.
- **receiving-code-review(피드백 수신 태도)** — quality-watcher는 "스펙과 일치하는지"만
  보고, 이 스킬은 "사용자 피드백을 성과적으로 바로 수긍하지 말고 먼저 검증 후 필요하면
  반박하라"는 태도 쪽이라 겹치지 않는다. 자동 규칙으로 만들 건 아니고, 사용자가 준
  피드백이 기술적으로 의아하면(예: 브라우저 버그가 아니라 실제 설계 의도인 경우 등)
  바로 순응하기 전에 먼저 검증하는 걸 계속 의식할 것.
- **finishing-a-development-branch** — 이 프로젝트엔 브랜치 전략 자체가 없다(항상 main
  직접 커밋 → `git push deploy main`). 정식 도입은 과하지만, 시각적 변경처럼 배포 후
  확인이 필요한 작업은 배포 직후 실제 렌더링을 한 번 더 확인하는 가벼운 "캐너리 체크"
  습관 정도는 유지할 것(이미 curl로 배포 반영 여부는 확인해왔으나, 시각적 정확성까지는
  아니었다 — 위 11절 참고).

## 20. 전체 워크플로우 로직 감사 (2026-09-10) — 발견·수정 8건

사용자 요청으로 "모든 프로세스의 워크플로우"를 4개 영역(판정·성적서 산출 / 권한·
최종결정 게이트 / 데이터 무결성·집계 / 자재·BOM·조립품 연동)으로 나눠 병렬 감사했다.
찾은 것 전부 그날 바로 고쳤고, quality-watcher 검증까지 통과했다. **아래 항목들은
이미 고쳐진 상태**이니, 혹시 이 문서의 다른 절(4절/5절/8-2절/18절)이 옛 동작을
설명하는 것처럼 보이면 이 절이 최신이다.

1. **(보안, 근본원인) `_can_make_final_decision()`의 폴백이 'approve' 권한을 안 봤음**
   (`app.py`) — docstring은 "최종결정권자 미지정 시 승인 권한만으로 통과"라는데
   실제 코드는 권한 체크 없이 무조건 통과였다. `approvers`가 비었을 때
   `"approve" in _user_perms(user)`를 확인하도록 고쳤다. 이 버그가 실제로 뚫리던
   유일한 경로가 `ncr_confirm`이었는데(다른 최종결정 라우트는 전부 이미
   `@perm_required("approve")`가 따로 걸려 있어 안 뚫림), 사용자가 그 김에
   **NCR 확인은 아예 이 게이트를 떼기로 결정**했다(8-2절 참고).
2. **BOM Lv 판별 로직이 설계와 반대로 짜여 있었음** (`database.py`,
   `import_bom_from_excel()`) — "Lv1~Lv5 열 중 값이 있는 열의 위치가 레벨"이 맞는
   설계인데, 코드는 그 칸의 값을 `int()`로 파싱해서 레벨 번호로 쓰려는 분기가
   주 경로였다. 지금까지는 그 칸 값이 자재번호처럼 문자 섞인 값이라 파싱이 항상
   실패해서 우연히 올바른 분기(열 위치 기반)로 빠졌을 뿐이다. `int()` 분기를
   없애고 항상 열 위치(`lv_idx + 1`)만 쓰게 고쳤다. **다음에 통합BOM 파싱을
   또 건드릴 일이 있으면 이 함수에 "값 파싱" 시도를 다시 넣지 말 것.**
3. **재검사 제출 시 임시저장(draft) 삭제 누락** (`app.py` `reinspect_submit()`) —
   최초 제출(`inspect_form`)은 제출 후 `db.clear_inspection_progress()`/
   `db.delete_inspection_draft()`를 호출하는데 재검사 경로엔 없었다. 추가함
   (8-2-2절의 "성적서 제출되면 삭제된다" 보장이 이제 재검사 경로에도 적용됨).
4. **도면번호 계산이 자재번호의 "P"를 전부 치환했음** (`report_builder.py`
   `compute_drawing_no()`) — `.replace("P", "-")` → `.replace("P", "-", 1)`(첫 P만).
   지금 DB엔 P가 2개 이상인 자재번호가 없어서 안 드러났을 뿐인 잠재 버그였다.
5. **판정 이탈(빨간 글씨) 로직 3중 중복을 하나로 합침** — `report_builder.py`
   (성적서 xlsx), `app.py`의 `_out_of_spec_flags()`(웹 성적서 상세), `_mark_vals()`
   (NCR 통보서) 세 곳에 각자 구현돼 있던 걸 `report_builder.is_out_of_range(v,
   lower, upper)` 하나로 통합했다. **앞으로 규격 이탈 판정 로직을 고칠 일이 있으면
   이 함수 하나만 고치면 셋 다 반영된다** — 8-1절의 공용 헬퍼 목록에 추가된 것으로
   취급할 것, 다시 복붙하지 말 것.
6. **일괄승인 대리 승인자 이름 미검증** (`app.py` `approve_batch()`) —
   최종결정권자가 아직 아무도 지정 안 됐을 때는 대리 승인자로 아무 문자열이나
   넣어도 검증 없이 감사로그에 기록됐다. 그 경우에도 `db.list_users()`로 실존
   계정인지 확인하도록 고쳤다.
7. **성적서 중복생성 방지의 경합(TOCTOU) 조건** (`database.py` `create_inspection()`)
   — 기존엔 "이미 있는지 확인" → "새로 만들기"가 서로 다른 커넥션의 별개 쿼리라
   동시 요청 시 이론적으로 둘 다 통과할 수 있었다(8-2-9절 참고, 현재 배포는
   gunicorn 워커 1개라 실제로는 거의 안 터짐). 스키마에 UNIQUE 제약을 추가하는
   대신(기존 프로덕션에 이미 중복 데이터가 있으면 마이그레이션이 깨질 위험이 있어서
   피함) `create_inspection()` 내부에서 `BEGIN IMMEDIATE`로 잠그고 INSERT 직전에
   활성 성적서를 한 번 더 확인해서 있으면 `ValueError`를 내게 했다. 호출부 3곳
   (`inspect_form` 정상 제출, 재검사, 테스트용 자동입력 루프) 전부 `try/except
   ValueError`로 감싸서 사용자에게 안내 메시지를 띄우고 리다이렉트한다.

이 감사는 병렬 서브에이전트(fork) 4개로 각 영역을 나눠 코드를 직접 Read/Grep해서
검증했다(문서를 믿지 않고 실제 코드로 확인) — 규모가 큰 정기 점검이 필요하면 이
방식(영역별 병렬 감사 → 발견사항 종합 → quality-watcher로 수정 검증)을 그대로
재사용하면 된다.

## 21. 출고 관리 — 사진 종류 분리(인디케이터/본체) + 관리자 토글 (2026-09-15)

출고 스캔에서 찍는 사진을 "인디케이터 사진"/"본체사진" 두 종류로 나눴다. 사용자가
실제 회사 출고 이력 서식(엑셀)을 줬는데 이 두 항목이 별도 열로 있었기 때문.

- `outbound_item_photos.kind` 컬럼('indicator'/'body', 기본값 'indicator') — 기존
  사진은 전부 'indicator'로 간주(마이그레이션 시 자동).
- **"본체사진" 기능 자체는 기본 꺼짐, admin 계정만 켤 수 있다** — 이 프로젝트에서
  "관리자 전용"은 세분화된 권한(perm)이 아니라 **`admin` 계정 하나**를 가리키는
  확립된 의미다(14절 참고, `_admin_only()` 재사용). `db.outbound_body_photo_enabled()`가
  `db.get_setting()` 기반 전역 토글이고, "출고 → 분류 규칙 관리" 화면 상단에 카드로
  노출된다. **끄고 켜는 걸 "outbound" 권한 세분화로 하지 말 것** — 이미 있는
  get_setting/set_setting 관례를 그대로 따른 것이니 다른 방식으로 바꾸지 말 것.
- 토글이 꺼져 있으면: 스캔 화면에 본체사진 입력 UI 자체가 안 뜨고, 서버(`outbound_item_add`/
  `outbound_item_photo_add`)도 `kind='body'` 사진을 거부한다(클라이언트 조작으로
  우회 못 하게 이중 방어). **끈다고 이미 저장된 본체사진이 지워지지도, 엑셀 출력에서
  빠지지도 않는다** — UI 노출 여부와 데이터 보존은 별개.
- `db.outbound_plan_progress(batch_id)` — 계획 대비 진행상황을 "S/N별 상태
  (confirmed/incomplete/pending)" 리스트로 계산하는 신규 집계 함수. **confirmed
  판정은 인디케이터 사진 필수 + (토글 켜져있을 때만) 본체사진도 필수** — 토글이
  꺼져 있는데 본체사진을 필수조건에 넣으면 애초에 입력받지 않으므로 영원히
  confirmed가 안 되는 모순이 생긴다. 새로 이 판정을 건드릴 일이 있으면 이 원칙을
  깨지 말 것.
- **"확인됨" 표시는 항목 추가 자체를 막지 않는다** — 사진·S/N이 부족해도 스캔
  항목은 그대로 저장되고, "확인됨" 배지만 안 뜬다. 이 서브시스템 전체 설계원칙
  (미등록/중복/계획외 전부 경고만 하고 진행은 막지 않음, 1차 설계 확정사항)과
  일관되게 유지한 것 — 여기서만 하드 블로킹을 넣지 말 것.
  **단, "계획외"만은 2026-09-16 사용자 지시로 이 원칙에서 빠졌다 — 24절 참고.**
  미등록/타배치사용/중복은 여전히 경고만 하고 막지 않는다, 바뀐 건 계획외 하나뿐.
- 저장된 항목에 사진을 나중에 더 추가(재업로드)하는 라우트
  `outbound_item_photo_add`(POST `/outbound/item/<id>/add-photo`)는 **기존 사진
  삭제가 아니라 추가**다. 삭제는 이미 있던 `outbound_photo_delete`가 계속 담당.
- 사진을 지정 영역에 여러 장 등분 배치하는 계산은 `report_builder._place_photos_in_area()`
  하나로 공용화했다(8-1절 참고, NCR 통보서와 출고 이력 엑셀이 공유) — 새로 복사하지 말 것.
- **`build_outbound_excel()`에 fit-to-page 설정을 처음에 빠뜨려서 5번째 열("본체사진"/
  "담당자")이 PDF에서 통째로 사라지는 실제 사고가 있었다** — 7-5절에 재발 사례로
  기록해뒀다. 이 함수를 또 고칠 일이 있으면 fit-to-page가 여전히 있는지 먼저 확인할 것.

## 22. 출고 확인 = 잠금, 회수는 신원 기반(세분화 권한 아님) (2026-09-15)

`database.py`의 `confirm_outbound_batch()`엔 원래 "확인은 순수 기록용, 잠금 아님 —
설계문서 확정사항"이라는 docstring이 있었다. **사용자가 이 확정사항을 명시적으로
뒤집어달라고 요청해서 지금은 반대로 동작한다** — 14절의 일반 교훈("확정"이라 적혀
있어도 사용자가 지금 다르게 지시하면 그 지시가 우선)이 여기도 그대로 적용된 사례다.
혹시 이 문서 다른 곳(README.txt 포함)에 "확인 후에도 계속 수정 가능"이라는 옛 설명이
남아있는 걸 보면 이 절이 최신이다.

**지금 동작**:
- `outbound_scan.html`에서 "✅ 출고 확인"을 누르면 그 즉시 이 배치의 항목 추가/수정/
  삭제·사진 추가/삭제·배치정보(거래처·차수·출고일·담당자) 수정이 전부 잠긴다.
  잠금은 화면(조건부 렌더링)과 서버(라우트 가드) 양쪽에서 걸려있다 — URL 직접 호출로
  우회 못 하게.
- 서버 가드는 공용 헬퍼 `app.py`의 `_outbound_batch_lock_response(batch,
  redirect_endpoint, **kwargs)` 하나로 통일했다(8-1절 원칙) — `outbound_batch_update`/
  `outbound_item_add`/`outbound_item_edit`/`outbound_item_delete`/`outbound_photo_delete`
  (item이 있을 때만)/`outbound_item_photo_add` 6개 라우트가 이걸 공유한다. AJAX 요청엔
  JSON 409, 일반 폼 제출엔 flash+redirect로 응답 형태를 나눠준다.
- **회수 권한은 새 세분화 permission이 아니라 "신원"으로 게이트한다** — `outbound`
  권한 보유자 중에서도 **그 배치를 확인한 사람 본인**이거나 **admin 계정**만 회수
  (`outbound_batch_confirm_revoke` 라우트)할 수 있다. 이게 이 프로젝트의 다른 회수
  기능(`approve_revoke` — 별도 세분화 권한으로 게이트)과 다른 방식이라는 걸 헷갈리지
  말 것 — 사용자가 이번엔 명시적으로 "확인자, 관리자만"이라고 신원 기준으로 요청했다.
- **신원 비교 계산식은 반드시 `confirm_outbound_batch`가 저장할 때 쓰는 것과 똑같이**
  `g.user["display_name"] or g.user["username"]`여야 한다 — 다르게 계산하면(예:
  username만 비교) 표시이름이 설정된 계정은 자기가 확인한 것도 회수를 못 하는 버그가
  난다. "관리자"는 여기서도(14절/21절과 동일) `username == "admin"` 계정 하나만 뜻한다.
- 서명·`content_hash` 같은 부수 상태 초기화는 **해당 없음** — 이 기능엔 애초에 그런
  개념이 없다(8-2-10절의 성적서 승인 회수와는 다른 케이스, 헷갈리지 말 것).
- 잠금 범위는 **"출고 스캔" 화면(`outbound_scan.html`)의 항목/사진/배치정보 수정만**이다.
  "차수 계획" 화면(`outbound_round_edit`, 계획 S/N 추가·삭제)은 별개 기능이라 이번
  잠금 대상이 아니다 — 나중에 "차수 계획도 잠가야 하나"는 질문이 나오면 이 구분을
  먼저 확인할 것.
- **확인 액션 진입점이 두 화면**(`outbound_scan.html`의 확인 버튼, `outbound_history.html`
  목록의 확인 버튼)**에 있다** — 둘 다 같은 `outbound_batch_confirm` 라우트를 쓰므로
  잠금/회수 로직은 라우트 레벨 한 곳만 구현하면 양쪽에 자동 반영된다. 이 사실을
  처음 조사할 때 "확인 액션은 한 곳뿐"이라고 잘못 파악했다가 실제 코드 대조로
  바로잡은 적이 있다 — **사전 조사 노트를 믿더라도 핵심 라우트/템플릿 원문은 직접
  한 번 더 확인하는 습관을 들일 것.**
- `outbound_scan_list()`("출고 스캔 — 차수 선택" 화면)는 목록을 `active_batches`
  (미확인)/`completed_batches`(확인됨) 두 그룹으로 나눠 넘긴다. 완료 그룹은
  `<details class="sec" data-sec="outbound-scan-done">`(기본 접힘, `open` 속성 없음)로
  분리 표시 — 21절에서 처음 도입한 `static/collapsible_sections.js` 패턴을 그대로
  재사용한 것(8-1절 원칙, 새로 구현 안 함).
- `outbound_scan.html`에서 "새 항목 스캔·입력" 카드는 확인된 배치에선 아예 렌더링
  안 된다(`{% if not batch.confirmed_at %}`) — 그 카드 안의 DOM(qrReaderRegion,
  addItemForm, curSerial 등)을 참조하는 JS 함수(`resetCurSerial`/`checkSerial`/
  `onScanned`/`addCurPhotos`)들은 전부 호출 시점에 해당 요소가 없을 수 있다는
  전제로 널 체크를 넣어뒀다(2026-09-15 quality-watcher가 지적해서 보강) — **이
  카드 안 요소를 참조하는 JS를 새로 추가할 때 이 가드 패턴을 빠뜨리지 말 것.**

## 23. 출고 관리 6개 화면 일괄선택삭제 + `outbound_delete` 권한 (2026-09-15)

출고(🚚) 메뉴의 모든 목록 화면(차수 입력/S/N 발급/분류 규칙 관리/출고 스캔/출고
이력/QR 출력 이력)에 체크박스형 일괄선택삭제를 추가했다. 기존에 개별삭제가 이미
있던 화면(S/N 발급, 분류 규칙)에도 **추가로** 넣었다 — 기존 것을 없애지 않았다.

- **새 세분화 권한 `outbound_delete`**("출고 기록 일괄삭제") — 기존 단일 `outbound`
  권한과 별개다. `PERM_GROUPS`에 튜플 하나 추가하는 것만으로 `PERM_LABELS`/
  `ALL_PERMS`와 계정 상세 화면의 체크박스 UI에 자동 반영됐다(`user_detail.html`이
  `PERM_GROUPS`를 그대로 순회하는 완전 동적 렌더링이라 템플릿 수정이 필요 없었다).
  **삭제 라우트 4개는 `@perm_required("outbound_delete")` 단독으로만 건다** —
  `@perm_required("outbound", "outbound_delete")`처럼 두 개를 같이 넘기면
  `perm_required`가 OR 조건이라(702행 근처) `outbound`만 있어도 삭제가 뚫리는
  보안 구멍이 생긴다. 새로 삭제 라우트를 추가할 때 이 실수를 반복하지 말 것.
- **재사용한 기존 컴포넌트**: `templates/_admin_delete.html`의 `admin_delete_bar`
  매크로(원래 성적서 관련 4개 화면이 `show=is_admin`으로 쓰던 것, 14절 참고) —
  이번엔 `show=('outbound_delete' in user_perms)`로 admin이 아니어도 이 권한만
  있으면 보이게 했다. **매크로 자체는 수정하지 않았다.**
- **매크로는 페이지당 인스턴스 1개만 지원한다**(내부 JS가 `document.querySelector`로
  첫 번째 `[data-admin-bar]`만 찾는 구조) — 표가 여러 개인 화면에서 이 제약을
  두 가지 방식으로 우회했다:
  - **분류 규칙 관리**(표 3개: 전압코드/접미사/P코드): 매크로는 1번만 쓰고, 각 행의
    `data-admin-id`에 `"{kind}:{code}"` **합성 키**(예: `"voltage:7"`)를 넣어서
    한 폼(`rule_keys`)으로 세 표를 동시에 처리한다. 서버(`outbound_rule_delete_selected`)가
    `split(":", 1)`로 kind/code를 분리해서 각 테이블에 맞게 삭제한다.
  - **출고 스캔**(표 2개: 활성 배치/완료된 배치): 매크로는 1번만 쓰고, 두 표의
    `<tr>` 전부에 `data-admin-id="{{ b.id }}"`를 붙인다 — 매크로의
    `querySelectorAll('[data-admin-id]')`가 문서 전체를 훑으므로 표가 여러 개여도
    자동으로 다 커버된다. **새로 "표가 여러 개인 화면"에 이 매크로를 쓸 일이
    있으면 이 두 가지 우회 패턴 중 하나를 재사용할 것 — 매크로를 여러 번 부르면
    안 된다.**
- **차수(배치) 삭제는 세 화면(차수 입력/출고 스캔/출고 이력)이 라우트 하나
  (`outbound_batch_delete_selected`)를 공유한다** — 셋 다 같은 `outbound_batches`
  테이블을 가리키는 목록이기 때문(8-1절 원칙). 이 라우트만 `_resolve_return_to()`/
  `_ADMIN_DELETE_RETURNS`(어느 화면에서 눌렀는지에 따라 그 화면으로 되돌아가는
  기존 헬퍼)를 쓴다 — 나머지 3개 라우트(S/N 발급/분류규칙/QR출력이력)는 각각
  호출하는 화면이 정확히 하나뿐이라 리다이렉트 대상을 고정값으로 하드코딩했다
  (2026-09-15 quality-watcher가 "왜 이 셋은 `_resolve_return_to`를 안 쓰냐"고
  확인 필요로 짚었는데, 호출자가 하나뿐이면 그 간접 계층이 불필요하다는
  의도된 설계다 — 나중에 또 이 질문이 나오면 이 문단을 참고할 것).
- **차수 삭제는 cascade가 필요하다** — `outbound_item_photos`(DB row + 실제 파일)
  → `outbound_items` → `outbound_planned_items` → `outbound_qr_exports` →
  `outbound_batches` 순서(자식 먼저, FK 제약 때문에 순서를 지켜야 함).
  `database.delete_outbound_batches()`가 이 순서를 담당하고, 지워진 사진의
  **실제 파일명 목록**을 반환해서 호출부(`app.py`)가 `OUTBOUND_PHOTO_DIR`에서
  디스크 파일까지 지운다(`delete_outbound_item()`과 같은 계약).
  **`finished_goods_serials`(S/N 발급이력)는 배치와 무관한 별개 테이블이라
  배치 삭제에 안 딸려온다** — 실수로 여기에 끼워넣지 말 것, 핵심 회귀 포인트다.
- **확인 완료(잠금)된 배치도 삭제 가능하다** — 22절의 "확인 시 수정 잠금"과
  "삭제"는 서로 다른 개념으로 취급한다(`_outbound_batch_lock_response()`를
  삭제 라우트에서 호출하지 않음). 잠금은 실수로 내용이 바뀌는 걸 막는 장치고,
  삭제는 권한을 가진 사람이 의도적으로 기록 자체를 없애는 행위라 별개다.

## 24. "계획외" 항목만 예외적으로 하드 블로킹 — 21절 원칙 일부 뒤집힘 (2026-09-16)

21절에 "미등록/중복/계획외 전부 경고만 하고 진행은 막지 않음(1차 설계 확정사항)"
이라고 명시적으로 적어뒀었는데, **사용자가 "계획외"에 한해서만 이 원칙을
명시적으로 뒤집었다**: "차수 리스트에 안맞는 QR이 인식될 경우 무조건 미입력
하게 해야해. 또한 출고 이력 페이지에서 확인 처리도 불가해야해." — 14절의 일반
교훈("확정"이라 적혀 있어도 사용자가 지금 다르게 지시하면 그 지시가 우선)이 여기도
그대로 적용된 사례다. **미등록 S/N/타배치사용/이 출고 건에 이미 추가된 S/N, 이
세 가지 경고는 그대로 경고만 하고 진행을 막지 않는다** — 바뀐 건 "계획외"(차수
계획에 없는 S/N) 하나뿐이니 혼동하지 말 것.

**지금 동작**:
- **항목 추가 차단**: `outbound_item_add()`(`app.py`, `/outbound/scan/<batch_id>/add-item`)가
  해당 배치에 계획(`outbound_planned_items`)이 하나라도 있는데 스캔된 S/N이 그
  계획에 없으면 항목 생성 자체를 400으로 거부한다(`db.planned_item_exists()`로
  확인). **계획 자체가 없는 배치(자유 등록)는 이 차단이 적용되지 않는다** — "계획에
  안 맞는다"는 개념 자체가 성립하지 않기 때문(8-2-9절 등 다른 곳의 "계획이 없으면
  통과시킨다" 폴백 패턴과 같은 원칙).
- 클라이언트(`outbound_scan.html`)도 `/outbound/serial-check` 응답의 `in_plan` 값을
  `curInPlan` 전역변수에 저장해뒀다가 "리스트에 추가" 클릭 시 서버 왕복 없이
  먼저 막는다 — 서버 차단이 진짜 방어선이고 클라이언트는 불필요한 요청을 줄이는
  용도일 뿐이니, 서버측 검증을 절대 빼고 클라이언트만 믿지 말 것.
- **출고 확인(`outbound_batch_confirm()`, `app.py`) 차단**: 배치에 계획이 있는데
  현재 스캔된 항목 중 계획에 없는 게 하나라도 남아있으면(과거에 이미 들어와
  있던 계획외 항목 포함) 확인 처리 자체를 거부한다. `db.outbound_batch_has_unplanned_items(batch_id)`
  하나로 판단 — **출고 스캔 화면과 출고 이력 화면 둘 다 이 라우트 하나를
  공유하므로(22절에서 이미 확인된 사실) 여기 한 곳만 게이트하면 양쪽 진입점에
  자동으로 다 적용된다.** 새로 라우트를 또 만들지 말 것.
- UI: 계획외 항목이 남아있으면 "✅ 출고 확인" 버튼 자체가 안 뜨고 대신 경고
  문구가 뜬다(`outbound_scan.html`), 출고 이력 목록(`outbound_history.html`)도
  "확인" 버튼 대신 "⚠ 계획외 항목 있음" 배지가 뜬다. 이 목록 화면의 배지는
  `db.list_outbound_batches()`가 SQL 서브쿼리로 배치마다 `planned_count`/
  `unplanned_count`를 같이 계산해서 내려준 걸 Jinja에서 `b.planned_count and
  b.unplanned_count`로 판단한다(N+1 쿼리 방지 — 목록 화면은 여러 배치를 한 번에
  보여주니 배치당 별도 쿼리를 안 날리게 SQL에서 한 번에 처리) — 반면 출고 스캔
  화면(단일 배치)은 이미 로드된 `items`/`planned_serials`로 같은 로직을 Python에서
  계산한다(`has_unplanned = bool(planned_serials) and any(...)`), 굳이 DB 헬퍼를
  또 안 부른다. **같은 "계획불일치 여부" 판단이 세 곳(단건 헬퍼 `outbound_batch_has_unplanned_items`,
  목록용 SQL 서브쿼리, 스캔화면용 Python 계산)에 존재하는데 이건 의도된 것이다** —
  각자 이미 갖고 있는 데이터 형태(단건 조회/목록 조회/이미 로드된 items)에 맞춰
  최소 쿼리로 같은 결론을 내도록 짠 것뿐, 로직 자체(계획 있고 + 계획외 항목 있음)는
  세 곳 다 동일하다. 이 판단 기준을 고칠 일이 있으면 세 곳 다 같이 고칠 것.

## 25. openpyxl `InlineFont`/`Font`의 `color`에 6자리 RGB만 주면 알파가 "00"(완전투명)이
된다 — 실제 Excel에서 "복구된 레코드" 경고로 이어짐 (2026-09-16)

사용자가 개선요청서 엑셀을 실제 Excel로 열었을 때 **"복구된 레코드: /xl/worksheets/
sheet1.xml 부분의 문자열 속성"** 경고를 봤다. 원인을 추적해보니 `CellRichText`/
`InlineFont`에 `color="000000"`처럼 **6자리(RGB만, 알파 없음)** 문자열을 주면,
openpyxl의 `Color`가 이걸 **앞에 "00"을 붙여 8자리로 채워서 `"00000000"`(알파=00,
완전투명)으로 저장**해버린다(직접 실측 확인:
`InlineFont(color="000000").color.rgb == "00000000"`). 사람이 의도한 건 당연히
불투명한 검정(`FF000000`)이었는데, 정반대인 완전투명이 저장된 것 — 이게 실제
Excel의 엄격한 스키마 검증기가 "복구가 필요한" 상태로 판단해서 저 경고를 띄운
것으로 보인다. **LibreOffice는 알파를 그냥 무시하고 RGB만 보고 렌더링해서 이
문제가 전혀 안 드러났다** — 이 세션이 LibreOffice PDF로 여러 번 "정상 확인"했던
바로 그 파일에서 실제 Excel만 경고를 띄운 것도 이 알파값 차이 때문.

**고친 곳**: `_build_defect_type_richtext()`(개선요청서 불량유형 강조, 2026-09-16
신설)와 `_build_remark_richtext()`(295행, 성적서 비고란 3색 표시 — **이 프로젝트
초기부터 있던 기존 기능**, 이번에 같이 발견돼서 같이 고침) 둘 다 `color="FF..."`
처럼 **반드시 8자리(AARRGGBB)로 알파까지 명시**하도록 수정했다.

```python
# 틀렸던 방식 — 알파가 "00"(투명)으로 채워짐
InlineFont(rFont="맑은 고딕", color="000000")

# 올바른 방식 — 알파 "FF"(불투명)를 직접 명시
InlineFont(rFont="맑은 고딕", color="FF000000")
```

**확인 필요, 아직 미수정으로 남겨둔 것**: 같은 파일 469행 부근
`Font(color=mark_color)`(성적서 "검사 결과" 합격/불합격/특채 마크, `mark_color`가
`"0000FF"`/`"FF0000"`/`"FF8C00"` 같은 6자리 문자열)도 직접 실측해보니 **일반
`Font`도 똑같이 알파가 "00"으로 채워지는 걸 확인했다**(`Font(color="FF0000").color.rgb
== "00FF0000"`) — 즉 이 버그는 `InlineFont`(리치텍스트)뿐 아니라 일반 셀 폰트
색상에도 구조적으로 존재한다. **다만 일반 `Font`(셀 스타일 색상)는 이번에 사용자가
지적한 리치텍스트(인라인 문자열)와는 XML상 다른 위치(`styles.xml`의 `<font>` vs
`sheet1.xml`의 `<is><r><rPr>`)라서, 이 프로젝트가 수년간 수많은 성적서를 발행해오며
저 마크 색상을 계속 6자리로 써왔는데도 이번처럼 "복구된 레코드" 경고가 보고된 적이
없었다** — 실제 Excel이 일반 셀 폰트 색상의 알파=00은 리치텍스트만큼 엄격하게
검증하지 않는 것으로 추정된다(확실히 검증된 사실은 아님, 추정). 그래서 이번엔
**실제로 사용자가 문제를 보고한 리치텍스트 두 곳만 고쳤고, 확인 안 된 일반 Font
색상 곳들은 건드리지 않았다**(과설계 방지 — 문제가 실증 안 된 곳까지 미리 다
바꾸지 않음, 이 프로젝트의 반복된 원칙). **다음에 성적서의 합격/불합격/특채 마크
색상이나, 그 외 이 파일에 있는 다른 `color="6자리"` 형태의 `Font`/`InlineFont`
호출에서 비슷한 경고나 색상 이상이 보고되면, 이 절부터 의심하고 8자리로 바꿀 것**
(`grep -n 'color="[0-9A-Fa-f]\{6\}"' report_builder.py`로 6자리짜리 잔여 호출을
전부 찾을 수 있다).
