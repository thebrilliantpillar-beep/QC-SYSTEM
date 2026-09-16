# 리스트 화면 엑셀 출력 기능 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **테스트 방식에 대한 주석**: 이 프로젝트(iqc-app)에는 pytest 등 정식 테스트 스위트가 없다(1인 유지보수 내부 도구, CLAUDE.md 19절/11절). 이 계획의 "Step: 검증"은 pytest가 아니라 `python -c "import app"`과 Flask `app.test_client()`를 쓰는 1회성 스크립트, 그리고 실제로 열어보는 xlsx 파일 확인으로 대체한다 — 이게 이 프로젝트의 기존 검증 관례다.

**Goal:** 검색 기능이 있는 20개 리스트 화면(신규 18개 + 기존 2개 코드 통합)에 "현재 화면(필터 적용된) 그대로 엑셀로 내보내기" 버튼을 추가한다.

**Architecture:** `report_builder.py`에 신설하는 시트 작성 공용 헬퍼(`build_list_excel`/`_write_list_sheet`) 하나로 모든 화면의 xlsx 스타일링을 통일하고, 각 화면은 "이미 필터링된 rows 리스트"를 만드는 로직만 소유한다(대부분 기존 라우트 안에 있던 걸 헬퍼 함수로 뽑아내는 정도). `app.py`의 `_send_list_excel()`이 다운로드 응답 변환을 공용화한다. 자체 검색 로직 화면은 라우트별로 `_x_list_rows()` 헬퍼를 새로 뽑아 화면(페이지)과 export 라우트가 같은 필터 결과를 공유하게 한다.

**Tech Stack:** Flask, openpyxl, SQLite(sqlite3.Row) — 새 의존성 없음.

**Spec:** 2026-09-16 사용자 요청("모든 리스트 화면에 엑셀 출력물 생성기능" + "검색 결과 엑셀 출력") — 확정사항은 아래 "Global Constraints"와 각 Task에 반영.

## Global Constraints

- 대상은 아래 20개 화면뿐이다(18개 신규 + 기존 2개 코드 통합). `/users`, `/logs`는 절대 대상에 넣지 않는다.
- "선택한 항목만" 체크박스 export는 이번 스코프 밖이다 — "현재 화면(필터 적용된) 전체 내보내기" 버튼 하나만 추가한다. 기존에 `approval_history.html`에 있던 체크박스 export UI는 그대로 둔다(백엔드만 통합).
- 엑셀 파일에 현재 검색어/필터 조건을 요약 행으로 넣는다(CLAUDE.md 8-2-6절 관례).
- export는 페이지네이션 무시, 필터링된 전체를 내보낸다(정렬은 화면과 동일하게 유지).
- 각 export 라우트는 그 화면의 `@perm_required(...)`를 그대로 쓴다. 새 permission은 만들지 않는다.
- 스타일(폰트 "맑은 고딕", 헤더 채우기 `E7EAF0`, 테두리 `B7BEC9`)은 기존 `approval_history_export`/`output_history_export`에서 그대로 이관한다 — 새로 디자인하지 않는다.
- 코드 수정/신설 후 quality-watcher 검증 필수(CLAUDE.md 11절) — 특히 권한 데코레이터 누락, 자체 검색 로직 화면의 필터 조건 누락 여부.

---

## File Structure

- **Modify: `report_builder.py`** — `build_list_excel()`, `_write_list_sheet()`, `_cell_value()` 신설(맨 끝에 추가). 시트 스타일링 전담, DB/Flask 몰라도 됨.
- **Modify: `app.py`** — `_send_list_excel()`, `_common_filter_summary()` 신설(각각 `_paginate`/`_list_search_params` 근처). 20개 export 라우트 신설. 자체 검색 로직 화면(spec/gauges/suppliers/materials_find/assemblies/approve/inspect_select/outbound류)은 그 라우트 바로 위에 `_x_list_rows()` 헬퍼를 뽑아서 페이지 라우트·export 라우트가 공유하게 한다.
- **Modify: 20개 템플릿** — 각 화면에 "📊 엑셀로 내보내기" 버튼 1줄 삽입. HTML 구조 변경 없음(버튼 1개 추가뿐).

---

### Task 1: 공용 헬퍼 — `report_builder.build_list_excel()` / `_write_list_sheet()`

**Files:**
- Modify: `C:\Users\Jaiden\Desktop\iqc-app\report_builder.py` (파일 맨 끝에 추가)

**Interfaces:**
- Produces: `report_builder.build_list_excel(sheet_title, columns, rows, filter_summary=None, title=None) -> io.BytesIO`
  이후 모든 Task가 이 함수(또는 다중 시트가 필요한 Task 8에서는 `_write_list_sheet`)를 쓴다.
  `columns`: `[(header_text: str, key_or_callable, width_chars: int), ...]`.
  `key_or_callable`이 문자열이면 `row[key]`(dict/`sqlite3.Row` 둘 다 안전하게), 콜러블이면 `row`를 받아 값을 계산.
  `filter_summary`: `[("검색어", "볼트"), ...]` — 있으면 표 위에 "적용된 조건" 요약 행 추가, 없으면 표만.
  `title`: 있으면 표 맨 위에 큰 제목 행(기존 승인이력/출력기록 export의 "검색 결과 리스트" 스타일) — 없으면 생략.

- [ ] **Step 1: `report_builder.py` 맨 끝에 아래 코드를 그대로 추가한다**

```python
def _cell_value(row, key):
    """columns의 문자열 key를 dict/sqlite3.Row 양쪽에서 안전하게 꺼낸다.
    없는 키거나 None이면 빈 문자열(엑셀에 파이썬 None이 그대로 안 찍히게)."""
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return ""
    return v if v is not None else ""


def _write_list_sheet(wb, sheet_title, columns, rows, filter_summary=None, title=None):
    """워크북에 리스트 화면 하나를 새 시트로 채워 넣는다. 승인이력/출력기록 export가 쓰던
    스타일(맑은 고딕, 헤더 채우기 E7EAF0, 얇은 테두리 B7BEC9)을 그대로 재사용한다 —
    8-1절 공용헬퍼 원칙, 새로 디자인하지 않는다. 시트가 여러 개 필요한 화면(불량 이력의
    레인별 시트 등)은 이 함수를 여러 번 불러서 조립한다 — build_list_excel()을 새로
    복사하지 말 것.

    columns: [(header_text, key_or_callable, width_chars), ...]
    rows: 이미 필터링·정렬 끝난 dict-like(sqlite3.Row 허용) 리스트.
    filter_summary: [("검색어", "볼트"), ...] 이면 표 위에 "적용된 조건" 요약 행 추가.
    title: 있으면 표 맨 위에 열 전체를 병합한 큰 제목 행 추가(기존 승인이력 export 스타일).
    반환: 새로 만든 Worksheet.
    """
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side

    ws = wb.create_sheet(title=sheet_title[:31])
    thin = Side(style="thin", color="B7BEC9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="E7EAF0")
    n_cols = len(columns)

    row_cursor = 1
    if title:
        c = ws.cell(row=1, column=1, value=title)
        c.font = Font(name="맑은 고딕", size=16, bold=True)
        c.alignment = Alignment(horizontal="center", vertical="center")
        if n_cols > 1:
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=n_cols)
        ws.row_dimensions[1].height = 28
        row_cursor = 2

    if filter_summary:
        c = ws.cell(row=row_cursor, column=1, value="적용된 조건")
        c.font = Font(name="맑은 고딕", bold=True)
        row_cursor += 1
        for label, value in filter_summary:
            ws.cell(row=row_cursor, column=1, value=label).font = Font(name="맑은 고딕", bold=True)
            ws.cell(row=row_cursor, column=2, value=str(value)).font = Font(name="맑은 고딕")
            row_cursor += 1
        row_cursor += 1  # 요약과 표 사이 빈 줄

    header_row = row_cursor
    for i, (header_text, _key, _w) in enumerate(columns, start=1):
        c = ws.cell(row=header_row, column=i, value=header_text)
        c.font = Font(name="맑은 고딕", bold=True)
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.fill = header_fill
        c.border = border
    ws.row_dimensions[header_row].height = 22

    for r_i, row in enumerate(rows, start=header_row + 1):
        for c_i, (_h, key, _w) in enumerate(columns, start=1):
            value = key(row) if callable(key) else _cell_value(row, key)
            c = ws.cell(row=r_i, column=c_i, value=value)
            c.font = Font(name="맑은 고딕")
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = border

    for i, (_h, _k, w) in enumerate(columns, start=1):
        ws.column_dimensions[ws.cell(row=header_row, column=i).column_letter].width = w

    ws.freeze_panes = ws.cell(row=header_row + 1, column=1).coordinate
    return ws


def build_list_excel(sheet_title, columns, rows, filter_summary=None, title=None):
    """리스트 화면 하나짜리 엑셀(시트 1개)을 만든다. 시그니처는 _write_list_sheet()와 동일 —
    내부에서 새 Workbook을 만들고 그 함수를 한 번 호출할 뿐이다.
    반환: io.BytesIO (openpyxl로 저장된 xlsx, seek(0) 완료 상태)
    """
    import io as _io
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    _write_list_sheet(wb, sheet_title, columns, rows, filter_summary=filter_summary, title=title)
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
```

- [ ] **Step 2: import 확인** — `python -c "import report_builder"` 실행, 에러 없어야 함(Workbook/PatternFill/Border/Side는 함수 내부 지역 import라 이 파일의 기존 관례와 동일 — 모듈 최상단 import를 건드릴 필요 없음).

- [ ] **Step 3: 수동 smoke test** — 아래를 한 번 실행해서 실제로 파일이 열리는지 확인 (스크래치 스크립트, 커밋 대상 아님):

```python
import report_builder
rows = [{"a": "볼트", "b": 10}, {"a": "너트", "b": None}]
cols = [("이름", "a", 20), ("수량", "b", 10), ("두배", lambda r: (r["b"] or 0) * 2, 10)]
buf = report_builder.build_list_excel("테스트", cols, rows,
                                       filter_summary=[("검색어", "볼트")], title="테스트 제목")
open("C:/Users/Jaiden/AppData/Local/Temp/claude/_scratch_list_excel_test.xlsx", "wb").write(buf.read())
```
openpyxl로 다시 열어서 A1(제목, 병합), 그 아래 "적용된 조건"/"검색어"/"볼트" 행, 그 아래 헤더("이름"/"수량"/"두배"), 데이터 2행("볼트"/10/20, "너트"/""/0)이 정확히 들어갔는지 확인한다.

- [ ] **Step 4: Commit**

```bash
git add report_builder.py
git commit -m "feat: 리스트 화면 엑셀 출력 공용 헬퍼(build_list_excel) 신설

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EEjCqYn7mf9syZbg7aYed9"
```

---

### Task 2: 공용 헬퍼 — `app.py`의 `_send_list_excel()` / `_common_filter_summary()`

**Files:**
- Modify: `C:\Users\Jaiden\Desktop\iqc-app\app.py`

**Interfaces:**
- Consumes: `report_builder.build_list_excel` (Task 1).
- Produces: `_send_list_excel(buf, filename_base) -> Flask Response`, `_common_filter_summary(f) -> list[tuple]`.
  이후 20개 export 라우트가 전부 `_send_list_excel`을 쓰고, `_list_search_params()`를 쓰는 화면(3,7,8,11,12,13,5)은 `_common_filter_summary`도 쓴다.

- [ ] **Step 1: `_send_list_excel` 추가** — `app.py`의 `_paginate` 함수 바로 위(`_list_search_params` 정의 직전)에 삽입:

```python
def _send_list_excel(buf, filename_base):
    """xlsx BytesIO를 다운로드 응답으로 변환하는 공용 함수. 파일명 특수문자 제거는
    report_builder.build_report_filename()과 같은 규칙(6절) — 치환하지 않고 그냥 제거만."""
    from datetime import date as _date
    safe_base = re.sub(r'[\\/:*?"<>|]', '', filename_base)
    fname = f"{safe_base}_{_date.today():%Y%m%d}.xlsx"
    return send_file(buf, as_attachment=True, download_name=fname,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
```

- [ ] **Step 2: `_common_filter_summary` 추가** — `_list_search_params()` 함수 정의 바로 뒤에 삽입:

```python
def _common_filter_summary(f):
    """_list_search_params() 결과에서 실제로 값이 채워진 조건만 뽑아 엑셀 요약행으로 변환.
    여러 화면(과거입고이력/검사이력/불량이력/NCR/개선요청서/반품처리/검사대기)이 공유."""
    out = []
    if f.get("q_inspector"): out.append(("검사자", f["q_inspector"]))
    if f.get("q_supplier"):  out.append(("업체", f["q_supplier"]))
    if f.get("q_product"):   out.append(("제품명", f["q_product"]))
    if f.get("q_material"):  out.append(("자재번호", f["q_material"]))
    if f.get("q_lot"):       out.append(("로트/발주번호", f["q_lot"]))
    if f.get("f_result"):    out.append(("자동판정", f["f_result"]))
    if f.get("f_status"):    out.append(("승인상태", f["f_status"]))
    if f.get("insp_start") or f.get("insp_end"):
        out.append(("검사일 범위", f"{f.get('insp_start') or ''} ~ {f.get('insp_end') or ''}"))
    if f.get("recv_start") or f.get("recv_end"):
        out.append(("입고일 범위", f"{f.get('recv_start') or ''} ~ {f.get('recv_end') or ''}"))
    if f.get("include"): out.append(("포함 단어", ", ".join(f["include"])))
    if f.get("exclude"): out.append(("제외 단어", ", ".join(f["exclude"])))
    return out
```
**주의**: 위 딕셔너리 키 이름(`q_inspector`/`q_supplier`/... )은 planner의 추정이다. developer는 실제 `_list_search_params()` 함수 정의를 먼저 읽어서 실제로 반환하는 딕셔너리 키 이름과 정확히 일치시킬 것 — 다르면 실제 키 이름으로 고쳐 쓴다.

- [ ] **Step 3: 검증** — `python -c "import app"` 에러 없어야 함.

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: 엑셀 다운로드 응답/공용 필터요약 헬퍼 추가

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01EEjCqYn7mf9syZbg7aYed9"
```

---

## Part C — 화면별 Task (20개, 서로 독립 — Task 17만 예외적으로 18/19/21보다 먼저 필요)

| # | 화면 | 라우트 | 난이도 | 비고 |
|---|---|---|---|---|
| 3 | 과거입고이력 | `/intake-history` | 쉬움 | 공용 필터엔진 |
| 4 | 자재관리 | `/spec` | 보통 | 자체 검색(query/by/category/include/exclude) |
| 5 | 검사대기 | `/inspect/new` | 보통 | 공용엔진+정렬+검사원매핑 |
| 6 | 승인대기 | `/approve` | 보통 | tab 기반 자체 로직 |
| 7 | 검사이력 | `/history` | 쉬움 | 공용 필터엔진 |
| 8 | 불량이력 | `/defects` | 보통(다중시트) | 레인 4개 → 시트 4개 |
| 9 | 계측기관리 | `/gauges` | 쉬움 | 단순 q 검색 |
| 10 | 업체관리 | `/suppliers` | 쉬움 | 단순 q 검색 |
| 11 | NCR | `/ncr` | 쉬움 | 공용 필터엔진 |
| 12 | 개선요청서 | `/improvement` | 쉬움 | 공용 필터엔진 |
| 13 | 반품처리 | `/returns` | 쉬움 | 공용 필터엔진 |
| 14 | 자재찾기 | `/materials/find` | 보통 | 자체 다중필터(Lv/모델/상위품목/카테고리) |
| 15 | 조립품관리 | `/assemblies` | 쉬움 | 단순 q+by 검색 |
| 16 | S/N발급이력 | `/outbound/serial/new` | 쉬움 | 단순 q, limit=-1 트릭 필요 |
| 17 | 출고배치 공용 라우트 신설 | (route only) | 보통 | 18/19/21의 기반, 먼저 또는 같이 진행 |
| 18 | 출고스캔 목록 | `/outbound/scan` | 쉬움 | Task 17 라우트 재사용, 템플릿만 |
| 19 | 출고이력 | `/outbound/history` | 쉬움 | Task 17 라우트 재사용, 템플릿만 |
| 20 | QR출력이력 | `/outbound/qr-exports` | 쉬움 | 단순 q, limit=-1 트릭 |
| 21 | 출고차수계획 | `/outbound/round` | 쉬움 | Task 17 라우트 재사용, 템플릿만 |
| 22 | 승인이력 통합 | `/history/approved/export` | 쉬움 | 기존 함수 교체 |
| 23 | 출력이력 통합 | `/output/history/export.xlsx` | 쉬움 | 기존 함수 교체 |

(각 Task 3~23의 구체적 코드는 planner가 이미 상세 작성했으나 지면상 이 파일에는 표만 남긴다 — 원본 상세 스펙은 이 계획을 만든 세션의 대화 기록에 있다. **developer는 각 화면의 실제 라우트 함수를 직접 열어서 필터링 로직·컬럼 구성을 스스로 확인하고 구현할 것** — 화면명/URL/난이도/데이터 소스 힌트(위 표의 "비고")를 출발점으로 삼되, 정확한 변수명·필드명은 실제 코드에서 확인한다.)

### 공통 패턴 (모든 Task에 적용)

1. 그 화면 라우트 함수 안에서 "필터링된 rows를 계산하는 부분"을 `_x_list_rows()` 형태의 헬퍼로 뽑아낸다(이미 공용 필터엔진 `_list_search_params()`+`_row_passes_search()`를 쓰는 화면은 그 호출부만 그대로 옮기면 됨).
2. 원래 라우트 함수는 그 헬퍼를 호출하도록 교체(동작 변화 없음, 리팩터링).
3. `/<url>/export.xlsx` 형태의 새 라우트를 추가, 그 화면과 동일한 `@perm_required(...)` 사용, 같은 헬퍼로 rows를 얻은 뒤 `columns` 리스트를 그 화면 표의 실제 헤더에 맞게 구성.
4. `report_builder.build_list_excel(sheet_title, columns, rows, filter_summary=...)` 호출 → `_send_list_excel(buf, filename_base)`로 응답.
5. 해당 템플릿의 검색 폼 바로 아래, 표 시작 전에 버튼 삽입:
```html
<div style="margin:0 0 12px;">
  <a href="{{ url_for('<export_endpoint>') }}?{{ request.query_string.decode('utf-8') }}"
     class="btn secondary" style="margin-top:0;">📊 엑셀로 내보내기</a>
</div>
```
6. 페이지네이션이 있는 화면도 export는 페이지 무시, 필터링된 전체를 낸다.
7. `db.list_serials()`/`db.list_qr_exports()`처럼 화면이 `limit`으로 자르는 함수를 쓰면, export 호출 시 `limit=-1`을 넘겨서 SQLite의 "무제한" 동작을 이용한다(함수 자체는 수정하지 않음).

### Task 8 특이사항 — 불량이력(`/defects`)은 다중 시트

레인 4개(재검사대기/통보서작성필요/통보서불필요/완료)를 시트 4개로 낸다 — `build_list_excel()` 대신 `_write_list_sheet()`을 새 `Workbook()`에 4번 호출해서 조립할 것(Task 1에서 만든 두 함수를 그대로 재사용).

### Task 17 특이사항 — 출고배치 공용 export 라우트

출고스캔 목록(`/outbound/scan`)·출고이력(`/outbound/history`)·출고차수계획(`/outbound/round`) 3개 화면이 전부 `db.list_outbound_batches(query=q)`를 그대로 표에 뿌린다(자체 필터 없음) — export 라우트도 `/outbound/batches/export.xlsx` 하나만 만들어서 3개 화면 템플릿이 공유한다. **이 Task를 Task 18/19/21보다 먼저 끝낼 것.** `db.list_outbound_batches()`가 `planned_count`/`unplanned_count`/`confirmed_by`/`confirmed_at`/`item_count` 필드를 실제로 반환하는지 `database.py`를 직접 읽어서 확인 후 컬럼 구성.

### Task 16/20 특이사항 — 날짜시간 포맷 함수명 확인

`format_datetime_korean`이 app.py에 실제로 그 이름으로 존재하는지 먼저 grep으로 확인할 것(`|datetime_korean` Jinja 필터가 감싸는 원본 함수명이 다를 수 있음). 없으면 그 실제 함수명으로 바꿔 쓰거나, 못 찾으면 원본 문자열을 그대로 쓴다.

---

### Task 22: 승인이력(`/history/approved/export`) 기존 코드를 `build_list_excel`로 통합

**Files:** Modify: `app.py` (`approval_history_export()` 전체 교체)

기존 함수(약 78줄의 직접 openpyxl 스타일링 코드)를 아래로 교체:

```python
@app.route("/history/approved/export")
@perm_required("inspect_history")
def approval_history_export():
    """첨부 양식 그대로 — report_builder.build_list_excel() 공용 헬퍼로 통일(2026-09-16).
    이전에는 이 함수와 output_history_export()가 거의 동일한 openpyxl 스타일링 코드를
    각자 갖고 있었다(8-1절 원칙 위반) — 지금은 report_builder.build_list_excel() 하나만."""
    rows, _ = _collect_approval_history()

    selected_ids = _multi_arg("ids")
    if selected_ids:
        wanted = {int(x) for x in selected_ids if x.isdigit()}
        rows = [r for r in rows if r["id"] in wanted]

    columns = [
        ("입고일", "receive_date_label", 15),
        ("업체명", "supplier", 14),
        ("로트번호", "po_number", 14),
        ("자재명", "material_name", 34),
        ("자재번호", "material_no", 14),
        ("입고수량", lambda r: f"{r['quantity']}개" if r["quantity"] else "", 10),
        ("검사자", "inspector", 10),
        ("검사시간", "total_time_label", 12),
        ("판정여부", "state", 10),
        ("합격 수량", lambda r: f"{r['pass_count']}개" if r["max_sample"] else "", 11),
        ("불량 수량", lambda r: f"{r['bad_count']}개" if r["bad_count"] else ("0개" if r["max_sample"] else ""), 11),
        ("AQL 최대 샘플", lambda r: f"{r['max_sample']}개" if r["max_sample"] else "", 13),
    ]
    buf = report_builder.build_list_excel("승인이력", columns, rows, title="검사 결과 리스트")
    return _send_list_excel(buf, "승인이력")
```
`_collect_approval_history()`가 실제 함수명/반환 형태(rows, 무엇)가 맞는지 기존 함수 내부를 먼저 읽어서 확인할 것 — 다르면 실제 로직에 맞게 조정.

**시각적 차이(회귀 아님, 사용자에게 알릴 것)**: 기존엔 B열부터 시작(A열 여백)했는데 `build_list_excel`은 A열부터 시작. 나머지 스타일은 동일.

---

### Task 23: 출력이력(`/output/history/export.xlsx`) 기존 코드를 `build_list_excel`로 통합

**Files:** Modify: `app.py` (`output_history_export()` 전체 교체)

```python
@app.route("/output/history/export.xlsx")
@perm_required("output")
def output_history_export():
    """출력기록 엑셀 내보내기 — ids= 파라미터로 선택 항목만, 없으면 전체(현재 검색 조건).
    report_builder.build_list_excel() 공용 헬퍼로 통일(2026-09-16, approval_history_export와
    같은 이유)."""
    q = request.args.get("q", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    rows = [dict(r) for r in db.list_output_history(q, date_from, date_to)]

    selected_ids = _multi_arg("ids")
    if selected_ids:
        wanted = {int(x) for x in selected_ids if x.isdigit()}
        rows = [r for r in rows if r["id"] in wanted]

    state_label = {"normal": "합격", "special": "특채", "failed": "불합격"}
    columns = [
        ("번호", "id", 8),
        ("자재번호", "material_no", 18),
        ("자재명", "material_name", 30),
        ("업체", "supplier", 18),
        ("로트번호", "po_number", 18),
        ("결정", lambda r: state_label.get(r["approval_type"] or "", "-"), 8),
        ("승인자", "approver", 12),
        ("승인일시", "approved_at", 20),
    ]
    buf = report_builder.build_list_excel("출력기록", columns, rows, title="출력 기록")
    return _send_list_excel(buf, "출력기록")
```

---

## Part E — 통합 검증 (Task 1~23 전부 끝난 뒤 1회)

- [ ] `python -c "import app"` 최종 확인.
- [ ] 아래 스크립트를 스크래치 디렉터리에서 실행(레포에 커밋 안 함):

```python
import time
import app as app_module

EXPORT_URLS = [
    "/intake-history/export.xlsx", "/spec/export.xlsx", "/inspect/new/export.xlsx",
    "/approve/export.xlsx", "/history/export.xlsx", "/defects/export.xlsx",
    "/gauges/export.xlsx", "/suppliers/export.xlsx", "/ncr/export.xlsx",
    "/improvement/export.xlsx", "/returns/export.xlsx", "/materials/find/export.xlsx",
    "/assemblies/export.xlsx", "/outbound/serial/export.xlsx",
    "/outbound/batches/export.xlsx", "/outbound/qr-exports/export.xlsx",
    "/history/approved/export", "/output/history/export.xlsx",
]

client = app_module.app.test_client()
r = client.post("/login", data={"username": "admin", "password": "admin1234"})
assert r.status_code in (302, 200), f"로그인 실패: {r.status_code}"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
results = []
for url in EXPORT_URLS:
    t0 = time.time()
    resp = client.get(url)
    dt = time.time() - t0
    ok = resp.status_code == 200 and resp.headers.get("Content-Type", "").startswith(XLSX_MIME)
    results.append((url, resp.status_code, round(dt, 2), ok))
    print(f"{'OK ' if ok else 'FAIL'} {url}  status={resp.status_code}  {dt:.2f}s")

failed = [r for r in results if not r[3]]
slow = [r for r in results if r[2] >= 5]
print(f"\n총 {len(results)}개, 실패 {len(failed)}개, 5초 이상 {len(slow)}개")
if failed:
    raise SystemExit(f"실패한 export: {failed}")
```
- [ ] 5초 이상 걸린 화면이 있으면 그대로 최종 보고에 남긴다 — 임의로 상한을 추가하지 않는다(사용자 확정 스코프 밖).
- [ ] 검사이력 export 파일을 실제로 열어서 헤더/데이터/필터 요약행 확인.
- [ ] quality-watcher 서브에이전트 호출 — 20개 export 라우트의 권한 데코레이터 일치 여부, `_x_list_rows()` 헬퍼가 원래 로직과 1:1 동일한지(자체 구현 화면 8개 중점), `format_datetime_korean` 등 함수명 정확성, `outbound_batches_export`가 참조하는 필드 존재 여부.

## Self-Review

**미포함 확인(의도된 스코프 밖)**: `/output`(출력대기)·`/spec/templates`(성적서양식목록)·`/change-points`(4M변경점)·업체성적표·`/outbound/rules`(분류규칙관리) — 2단계 대상, 이번엔 Task 없음. `/users`, `/logs` — 민감화면, 영구 제외.
