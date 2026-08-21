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
    if lower is None and upper is None:
        return False  # 규격 자체가 없으면 안전하게 불합격
    if lower is not None and v < lower:
        return False
    if upper is not None and v > upper:
        return False
    return True
```

**앞으로 판정 로직을 건드릴 일이 있으면 반드시 하한 또는 상한이 `None`인 케이스를
테스트에 포함시킬 것.** `report_builder.py`의 측정값 빨간색 강조 로직도 동일한 패턴으로
되어 있다.

## 5. 권한 시스템

4개 고정 역할(admin/approver/manager/inspector)을 완전히 폐기하고, `users.permissions`에
콤마로 구분된 개별 권한 문자열을 저장하는 방식으로 전환했다:

```
intake / spec / inspect / inspect_all / approve / output / users / logs
```

- `intake` — 입고 리스트 등록
- `spec` — 규격 관리 전부(등록/수정/삭제/일괄업로드/개별등록/그룹관리)
- `inspect` — 검사 입력, **본인이 입력한 성적서만 수정 가능**
- `inspect_all` — 이 권한이 있으면 남이 입력한 성적서도 수정 가능
- `approve` — 승인/반려/특채
- `output` — 승인된 성적서 PDF/xlsx 출력
- `users` — 계정 관리. **이 권한 보유자만 10분 무동작 자동로그아웃 적용됨**
  (다른 계정은 24시간 세션 유지)
- `logs` — 활동 로그 열람

`app.py`의 `perm_required(*perms)` 데코레이터로 라우트별 권한을 체크한다.
`_can_edit_inspection()` 함수가 본인/타인 성적서 수정 가능 여부를 판단한다.

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
- `spec_review.html` — 확인 필요 자재 목록(자동파싱 실패 항목, "해결됨" 버튼)
- `groups.html` / `group_detail.html` — 조립품 그룹 관리
- `output_list.html` / `output_result.html` — 출력 대기 목록, 선택/전체 출력
- `users.html` — 계정 관리(체크박스 권한, **아이디/이름 인라인수정**, 비밀번호 재설정, 삭제)
- `logs.html` — 활동 로그(읽기 전용 — 수정·삭제 UI 절대 추가하지 말 것)
- `input.html` — **초기 프로토타입 잔재, 실제로 어떤 라우트에서도 참조 안 됨(확인 완료).
  삭제해도 안전함.**

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
