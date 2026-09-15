# 출고 스캔 — 5개 품질확인항목(PASS/FAIL/SPECIAL) + 전체판정 구현 계획

> **For agentic workers:** developer 에이전트가 이 문서를 그대로 구현한다(코드는 planner가 작성하지 않음). 완료 후 quality-watcher 검증 필수(CLAUDE.md 11절). 이 프로젝트엔 pytest 스위트가 없으므로 "검증"은 Flask test client + `sqlite3` CLI + 실제 xlsx를 열어보는 수동 확인으로 대체한다(기존 `docs/superpowers/plans/2026-09-15-outbound-shipment-v2-plan.md` 3번째 줄과 동일한 관례).

**Goal:** 사용자가 제공한 실제 회사 "출고 내역서" 서식(순번/S·N/제품명·모델명/체결상태확인/QR번호부착상태확인/간지포장상태/RST단자나무판결착/부속품유무확인/인디케이터사진/본체사진/판정, 11열)을 출고 스캔 화면(`outbound_scan.html`)과 출력 엑셀(`build_outbound_excel`)에 반영한다. 5개 검사항목을 항목(S/N)마다 터치로 PASS/FAIL/SPECIAL 선택하고, 전체판정을 자동계산(수동 번복 가능)한다.

**Architecture:** 기존 출고 서브시스템(`outbound_items` 테이블, `outbound` 권한 하나, `_outbound_batch_lock_response()` 잠금)에 컬럼만 추가하고 얹는다. 새 테이블 없음. 판정 우선순위 계산은 `database.py`에 순수함수 하나(`compute_outbound_item_auto_result`)로 두고 자동판정 값 자체는 저장하지 않는다(5개 체크값에서 매번 계산 — 저장된 값과 어긋날 위험 자체를 없앰, CLAUDE.md 8-1절 원칙). 수동 오버라이드만 컬럼 하나로 저장한다(`inspection_items.override_result`/`effective_result()` 패턴과 동일 설계, `app.py:3051-3057` 참고). 사진 셀 "비율 무시 꽉 채우기"는 기존 공용 헬퍼 `_place_photos_in_area()`에 `stretch` 파라미터를 추가해서 처리하고(기본값 False로 NCR 등 기존 호출부는 전혀 안 바뀜), 실제 채우기는 `OneCellAnchor+ext`가 아니라 `TwoCellAnchor(editAs="oneCell")`로 구현한다 — 같은 파일에 이미 있는 QR 라벨 수정(커밋 `88013b9`)과 동일한, 실측으로 검증된 안전한 패턴이다.

**Tech Stack:** Flask(`app.py`), SQLite(`database.py`), openpyxl(`report_builder.py` — `TwoCellAnchor`/`AnchorMarker`는 이미 import돼 있음), Jinja2(`templates/outbound_scan.html`), 순수 JS(fetch, 기존 `outbound_scan.html` 스크립트 블록에 이미 있는 즉시저장 아키텍처 재사용).

## 실제 회사 서식 확정 정보 (실제 첨부 xlsx를 openpyxl로 직접 읽어서 확인함, 추측 아님)

시트명 "출고내역", 범위 A1:K9:
- 1행: "출고 내역서" (제목, 이미 구현됨)
- 2행: 거래처/차수/출고일/담당자 (이미 구현됨, `build_outbound_excel` 그대로 유지)
- 3행(헤더, 11열): `순번 | S/N | 제품명/모델명 | 체결 상태 확인\n(가대 다리, 탱크 다리,\n네마) | QR 번호\n부착 상태 확인 | 간지 포장 상태 | RST단자\n나무판 결착 | 부속품 유무 확인 | 인디케이터 사진 | 본체사진 | 판정`
- 예시 데이터로 확정된 판정 우선순위:
  - `PASS,PASS,PASS,FAIL,SPECIAL` → 판정 = **SPECIAL** (FAIL이 섞여 있어도 SPECIAL이 이김)
  - `PASS×5` → PASS
  - `FAIL×5` → FAIL
  - 즉 **우선순위: 하나라도 SPECIAL이면 SPECIAL → 그 외 하나라도 FAIL이면 FAIL → 전부 PASS면 PASS**

이 5개 항목은 **배치(차수) 단위가 아니라 항목(S/N, `outbound_items`의 한 행) 단위**다.

## Global Constraints

```
- 자동판정(result_auto)은 절대 DB 컬럼으로 저장하지 않는다. 5개 check_* 컬럼에서
  compute_outbound_item_auto_result()로 매번 계산한다(CLAUDE.md 8-1절 공용헬퍼 원칙,
  저장값과 계산값이 어긋나는 사고 원천 차단).
- 판정 우선순위는 반드시 "SPECIAL 하나라도 있으면 SPECIAL(FAIL 섞여도 이김) → 그 외
  FAIL 하나라도 있으면 FAIL → 전부 PASS면 PASS"다. FAIL을 SPECIAL보다 먼저 체크하는
  코드를 짜면 안 된다 — 실제 회사 서식 예시로 확정된 값이다.
- 5개 체크 필드 중 하나라도 미선택(NULL)이면 자동판정은 None("미검사")이다 — 부분
  입력 상태에서 억지로 판정을 내지 않는다.
- 수동 오버라이드(result_override)는 별도 컬럼 하나로만 저장한다. "실제로 적용되는
  값"은 override가 있으면 override, 없으면 auto — `inspection_items`의
  `override_result`/`effective_result()` 패턴(app.py:3051-3057)과 동일하게 갈 것.
- outbound_items 테이블 수정 라우트는 전부 기존 `_outbound_batch_lock_response()`를
  통과해야 한다(배치 확인 완료 시 잠금, CLAUDE.md 22절). 새 라우트 2개도 예외 없음.
- 새 권한을 만들지 않는다. 기존 `outbound` 권한 하나로 게이트한다(1차/2차 구현과
  동일 원칙).
- 새 필드명(`field`)이 SQL 컬럼명으로 f-string에 직접 들어가는 지점이 있다
  (`update_outbound_item_check`) — 반드시 화이트리스트(`OUTBOUND_CHECK_FIELDS`) 검증을
  라우트와 DB 함수 양쪽에서 한다(방어 2중화, SQL 인젝션 방지).
- `_place_photos_in_area()`에 새로 추가하는 `stretch` 파라미터의 기본값은 `False`다.
  `_insert_ncr_photos()`(불량현상사진)는 이 인자를 호출하지 않으므로 동작이 전혀
  바뀌면 안 된다 — 회귀 테스트 시 NCR 통보서 PDF도 같이 열어서 사진이 여전히
  비율유지로 나오는지 확인할 것.
- 이미지 삽입 로직을 건드리므로 CLAUDE.md 11절/7-4-2절에 따라 LibreOffice 검증만으로
  "됐다"고 하지 말 것 — PowerShell + Excel COM 자동화로 실제 엑셀 인쇄결과까지 확인할 것.
- 코드 수정 완료 후 quality-watcher 검증 필수(CLAUDE.md 11절).
```

---

## Task 1: DB 스키마 확장 + 판정 계산 헬퍼 (`database.py`)

**Files:**
- Modify: `C:\Users\Jaiden\Desktop\iqc-app\database.py`
  - 상수+헬퍼 함수: `init_db()` 정의 바로 앞, 즉 13번째 줄(`DB_PATH = ...`)과 15번째 줄(`def get_ma_by_component`) 사이에 삽입
  - 스키마 마이그레이션: `init_db()` 안, 674~683행(`outbound_items` CREATE TABLE) 바로 뒤, 685행(`outbound_item_photos` CREATE TABLE) 앞
  - `list_outbound_items()`: 3725~3740행, `row["photos"] = ...` 다음 줄에 2줄 추가
  - `outbound_plan_progress()`: 3951~3996행, `photos_ok` 계산 로직 확장
  - 신규 함수 2개(`update_outbound_item_check`, `set_outbound_item_result_override`): `add_outbound_item_photo()` 함수(3765~3773행) 바로 뒤에 추가

**Interfaces:**
- Produces: `db.OUTBOUND_CHECK_FIELDS`(리스트), `db.OUTBOUND_CHECK_LABELS`(dict), `db.OUTBOUND_RESULT_VALUES`(튜플), `db.compute_outbound_item_auto_result(item: dict) -> str|None`, `db.outbound_item_effective_result(item: dict) -> str|None`, `db.update_outbound_item_check(item_id: int, field: str, value: str) -> None`, `db.set_outbound_item_result_override(item_id: int, value: str|None) -> None` — Task 2(app.py 라우트)가 이 전부를 그대로 호출한다.

- [ ] **Step 1: 상수 + 순수함수 추가**

`database.py`의 13행과 15행 사이(`get_ma_by_component` 함수 정의 직전)에 삽입:

```python
# ---------- 2026-09-15 확장: 출고 항목 품질확인 5종 + 전체판정 ----------
# 사용자가 준 실제 회사 "출고 내역서" 서식 헤더 순서 그대로. 배치(차수) 단위가 아니라
# 항목(S/N, outbound_items 한 행) 단위다 — 실제 첨부 엑셀을 openpyxl로 직접 읽어서 확인함.
OUTBOUND_CHECK_FIELDS = ["check_tie", "check_qr", "check_wrap", "check_rst", "check_access"]
OUTBOUND_CHECK_LABELS = {
    "check_tie": "체결 상태 확인",
    "check_qr": "QR 번호 부착 상태 확인",
    "check_wrap": "간지 포장 상태",
    "check_rst": "RST단자 나무판 결착",
    "check_access": "부속품 유무 확인",
}
OUTBOUND_RESULT_VALUES = ("PASS", "FAIL", "SPECIAL")


def compute_outbound_item_auto_result(item):
    """item: dict(다섯 개 check_* 키 포함 — sqlite3.Row면 호출 전에 dict()로 바꿀 것).
    5개 전부 값이 있어야 계산하고, 하나라도 비어있으면(None, 아직 안 눌러봄) None을
    돌려준다("미검사").

    우선순위: SPECIAL이 하나라도 있으면 SPECIAL(FAIL이 섞여 있어도 SPECIAL이 이긴다) →
    그 다음 FAIL이 하나라도 있으면 FAIL → 전부 PASS면 PASS.
    (2026-09-15, 사용자가 준 실제 회사 서식의 예시 데이터로 확정된 우선순위:
    PASS,PASS,PASS,FAIL,SPECIAL → 판정=SPECIAL. 이 순서를 절대 바꾸지 말 것 —
    FAIL을 먼저 체크하면 실제 서식과 다른 결과가 나온다.)"""
    vals = [item.get(f) for f in OUTBOUND_CHECK_FIELDS]
    if any(v not in OUTBOUND_RESULT_VALUES for v in vals):
        return None
    if "SPECIAL" in vals:
        return "SPECIAL"
    if "FAIL" in vals:
        return "FAIL"
    return "PASS"


def outbound_item_effective_result(item):
    """수동 오버라이드가 있으면 그 값, 없으면 자동판정 그대로.
    inspection_items.override_result / app.py의 effective_result() 패턴과 동일 설계
    (CLAUDE.md 관례 — 원본 자동판정은 안 지우고 오버라이드만 별도 저장)."""
    return item.get("result_override") or compute_outbound_item_auto_result(item)
# ---------- 확장 끝 ----------
```

- [ ] **Step 2: 스키마 마이그레이션 추가**

`init_db()` 안, 683행(`outbound_items` CREATE TABLE의 닫는 `"""`) 바로 뒤, 685행(`outbound_item_photos` CREATE TABLE 시작) 앞에 삽입:

```python
    existing_oi_cols = [row[1] for row in cur.execute("PRAGMA table_info(outbound_items)").fetchall()]
    # 2026-09-15 확장: 출고 전 5개 품질확인항목(PASS/FAIL/SPECIAL) + 전체판정 수동 오버라이드.
    # 자동판정 값 자체는 저장하지 않는다 — compute_outbound_item_auto_result()가 5개
    # check_* 컬럼에서 매번 계산한다(CLAUDE.md 8-1절, 저장값-계산값 불일치 사고 방지).
    for _ob_col in OUTBOUND_CHECK_FIELDS + ["result_override"]:
        if _ob_col not in existing_oi_cols:
            cur.execute(f"ALTER TABLE outbound_items ADD COLUMN {_ob_col} TEXT")
```

- [ ] **Step 3: `list_outbound_items()`에 계산값 첨부**

`list_outbound_items()` 함수(3725~3740행) 안, `row["photos"] = [dict(p) for p in photos]` 줄 바로 다음에 2줄 추가:

```python
        row["photos"] = [dict(p) for p in photos]
        row["result_auto"] = compute_outbound_item_auto_result(row)
        row["result_effective"] = outbound_item_effective_result(row)
        result.append(row)
```

- [ ] **Step 4: 검사 결과 저장 함수 2개 추가**

`add_outbound_item_photo()` 함수(3765~3773행) 바로 뒤에 추가:

```python
def update_outbound_item_check(item_id, field, value):
    """field는 OUTBOUND_CHECK_FIELDS 안에 있어야 하고 value는 OUTBOUND_RESULT_VALUES
    안에 있어야 한다 — field가 SQL 컬럼명으로 f-string에 그대로 들어가므로, 호출부
    (app.py 라우트)가 반드시 먼저 화이트리스트 검증을 해야 한다. 여기서도 다시 한 번
    검증한다(방어 2중화, 라우트가 실수로 빠뜨려도 SQL 인젝션 경로가 안 되게)."""
    if field not in OUTBOUND_CHECK_FIELDS:
        raise ValueError(f"허용되지 않은 검사항목 필드: {field}")
    if value not in OUTBOUND_RESULT_VALUES:
        raise ValueError(f"허용되지 않은 판정값: {value}")
    conn = get_conn()
    conn.execute(f"UPDATE outbound_items SET {field}=? WHERE id=?", (value, item_id))
    conn.commit()
    conn.close()


def set_outbound_item_result_override(item_id, value):
    """value: 'PASS'/'FAIL'/'SPECIAL' 중 하나 또는 None(오버라이드 해제 → 자동판정 복귀)."""
    if value is not None and value not in OUTBOUND_RESULT_VALUES:
        raise ValueError(f"허용되지 않은 판정값: {value}")
    conn = get_conn()
    conn.execute("UPDATE outbound_items SET result_override=? WHERE id=?", (value, item_id))
    conn.commit()
    conn.close()
```

- [ ] **Step 5: `outbound_plan_progress()` "확인됨(confirmed)" 조건에 검사완료 추가**

**설계 판단(사용자 확인 불필요, 기존 원칙의 자연스러운 확장):** 이 함수의 기존 docstring은 "confirmed = 스캔됨 + 필요 사진 전부 있음"이다. 이번에 5개 검사항목이 새로 생겼으니, 사진뿐 아니라 **5개 검사항목도 전부 선택돼야 "확인됨"으로 본다** — 그래야 계획대비진행상황 화면에서 "확인됨" 배지가 실제로 출고 가능한 상태(사진+판정 모두 완료)를 뜻하게 된다.

3951~3996행 함수를 아래로 교체(docstring과 for 루프 안쪽만 수정, 함수 시그니처/반환 형식은 그대로):

```python
def outbound_plan_progress(batch_id):
    """차수 하나의 "계획 대비 스캔 진행상황"을 계산한다.

    반환: {"rows": [{"serial_no", "model_label", "status"}, ...],
           "summary": {"planned_total", "matched", "extra"}}
    status: 'confirmed'(스캔됨 + 필요 사진 전부 있음 + 5개 품질확인항목 전부 선택됨) /
    'incomplete'(스캔은 됐는데 사진 또는 품질확인이 부족함) / 'pending'(아직 안 스캔됨).

    본체사진이 "필요 사진"에 들어가는지는 이 함수를 호출하는 시점의
    outbound_body_photo_enabled() 값을 따른다(토글이 꺼져 있으면 인디케이터
    사진만 있으면 됨 — 애초에 입력받지 않는 항목을 조건에 넣으면 영원히 확인
    불가능해지기 때문). 품질확인 5종은 2026-09-15 신규 — 5개 항목이 전부 선택된
    항목이 하나라도 있으면 checks_ok로 본다(같은 S/N이 여러 번 스캔된 드문 경우,
    사진 병합과 같은 방식으로 처리)."""
    items = list_outbound_items(batch_id)
    planned = list_planned_items(batch_id)
    body_required = outbound_body_photo_enabled()
    rules = get_classify_rules()

    items_by_serial = {}
    for it in items:
        items_by_serial.setdefault(it["serial_no"], []).append(it)
    planned_serials = {p["serial_no"] for p in planned}

    rows = []
    matched = 0
    for p in planned:
        its = items_by_serial.get(p["serial_no"]) or []
        scanned = bool(its)
        photos_ok = False
        checks_ok = False
        if scanned:
            kinds = {ph.get("kind") or "indicator" for it in its for ph in (it.get("photos") or [])}
            photos_ok = ("indicator" in kinds) and (not body_required or "body" in kinds)
            checks_ok = any(compute_outbound_item_auto_result(it) is not None for it in its)
        if scanned and photos_ok and checks_ok:
            status = "confirmed"
            matched += 1
        elif scanned:
            status = "incomplete"
        else:
            status = "pending"
        rows.append({
            "serial_no": p["serial_no"],
            "model_label": classify_serial_no(p["serial_no"], rules=rules),
            "status": status,
        })

    extra = sum(1 for it in items if it["serial_no"] not in planned_serials)
    return {"rows": rows, "summary": {"planned_total": len(planned), "matched": matched, "extra": extra}}
```

- [ ] **Step 6: 검증 (수동, 자동화 테스트 스위트 없음)**

```bash
python -c "
import database as db
db.init_db()
conn = db.get_conn()
cols = [r[1] for r in conn.execute('PRAGMA table_info(outbound_items)').fetchall()]
assert set(db.OUTBOUND_CHECK_FIELDS + ['result_override']) <= set(cols), cols
print('OK: 컬럼 추가됨', cols)
conn.close()
"
```

```bash
python -c "
import database as db
assert db.compute_outbound_item_auto_result({'check_tie':'PASS','check_qr':'PASS','check_wrap':'PASS','check_rst':'FAIL','check_access':'SPECIAL'}) == 'SPECIAL'
assert db.compute_outbound_item_auto_result({'check_tie':'PASS','check_qr':'PASS','check_wrap':'PASS','check_rst':'PASS','check_access':'PASS'}) == 'PASS'
assert db.compute_outbound_item_auto_result({'check_tie':'FAIL','check_qr':'FAIL','check_wrap':'FAIL','check_rst':'FAIL','check_access':'FAIL'}) == 'FAIL'
assert db.compute_outbound_item_auto_result({'check_tie':'PASS','check_qr':None,'check_wrap':'PASS','check_rst':'PASS','check_access':'PASS'}) is None
print('OK: 판정 우선순위 4케이스 통과')
"
```

---

## Task 2: 저장 라우트 2개 추가 (`app.py`)

**Files:**
- Modify: `C:\Users\Jaiden\Desktop\iqc-app\app.py`
  - 신규 라우트 2개: `outbound_item_edit` 함수(7768~7788행) 끝(`return redirect(...)` 다음 줄)과 `outbound_item_delete` 라우트(7791행) 사이에 삽입
  - `outbound_scan_edit()` 뷰(7642~7661행)에 템플릿 컨텍스트 1개 추가

**Interfaces:**
- Consumes: `db.OUTBOUND_CHECK_FIELDS`, `db.OUTBOUND_CHECK_LABELS`, `db.OUTBOUND_RESULT_VALUES`, `db.compute_outbound_item_auto_result`, `db.update_outbound_item_check`, `db.set_outbound_item_result_override`, `db.get_outbound_item`, `db.get_outbound_batch`, 기존 헬퍼 `_outbound_batch_lock_response(batch, redirect_endpoint, **kwargs)`(489~499행), `record_change(action, target_type, target_id, detail)`(362행)
- Produces: 라우트 `POST /outbound/item/<int:item_id>/check` (엔드포인트명 `outbound_item_check_update`), `POST /outbound/item/<int:item_id>/result-override`(엔드포인트명 `outbound_item_result_override`) — Task 4(템플릿 JS)가 이 두 URL과 JSON 응답 스키마를 그대로 호출한다.

- [ ] **Step 1: 라우트 2개 추가**

`app.py`, `outbound_item_edit` 함수 끝(7788행 `return redirect(url_for("outbound_scan_edit", batch_id=item["batch_id"]))`) 바로 뒤, `outbound_item_delete` 라우트(7791행) 앞에 삽입:

```python
@app.route("/outbound/item/<int:item_id>/check", methods=["POST"])
@perm_required("outbound")
def outbound_item_check_update(item_id):
    """5개 품질확인항목(체결/QR/간지포장/RST/부속품) 중 하나를 PASS/FAIL/SPECIAL로
    저장한다. 터치 한 번 = 저장 한 번(기존 사진 업로드와 같은 즉시저장 아키텍처)."""
    item = db.get_outbound_item(item_id)
    if item is None:
        return jsonify({"ok": False, "error": "항목을 찾을 수 없어."}), 404
    batch = db.get_outbound_batch(item["batch_id"])
    locked = _outbound_batch_lock_response(batch, "outbound_scan_edit", batch_id=item["batch_id"])
    if locked:
        return locked

    field = request.form.get("field", "")
    value = request.form.get("value", "")
    if field not in db.OUTBOUND_CHECK_FIELDS:
        return jsonify({"ok": False, "error": "잘못된 검사항목이야."}), 400
    if value not in db.OUTBOUND_RESULT_VALUES:
        return jsonify({"ok": False, "error": "잘못된 판정값이야."}), 400

    db.update_outbound_item_check(item_id, field, value)
    updated = dict(db.get_outbound_item(item_id))
    result_auto = db.compute_outbound_item_auto_result(updated)
    record_change("출고 항목 검사결과 변경", "outbound_item", item_id,
                  f"{item['serial_no']} / {db.OUTBOUND_CHECK_LABELS[field]}={value}")
    return jsonify({"ok": True, "result_auto": result_auto,
                    "result_override": updated.get("result_override")})


@app.route("/outbound/item/<int:item_id>/result-override", methods=["POST"])
@perm_required("outbound")
def outbound_item_result_override(item_id):
    """전체판정 수동 오버라이드("판정 번복"). value가 빈 문자열이면 오버라이드를 지우고
    자동판정으로 되돌린다."""
    item = db.get_outbound_item(item_id)
    if item is None:
        return jsonify({"ok": False, "error": "항목을 찾을 수 없어."}), 404
    batch = db.get_outbound_batch(item["batch_id"])
    locked = _outbound_batch_lock_response(batch, "outbound_scan_edit", batch_id=item["batch_id"])
    if locked:
        return locked

    value = request.form.get("value", "")
    if value == "":
        value = None
    elif value not in db.OUTBOUND_RESULT_VALUES:
        return jsonify({"ok": False, "error": "잘못된 판정값이야."}), 400

    db.set_outbound_item_result_override(item_id, value)
    updated = dict(db.get_outbound_item(item_id))
    result_auto = db.compute_outbound_item_auto_result(updated)
    record_change("출고 항목 전체판정 수동변경", "outbound_item", item_id,
                  f"{item['serial_no']} / {value or '자동으로 복귀'}")
    return jsonify({"ok": True, "result_auto": result_auto, "result_override": value})
```

- [ ] **Step 2: `outbound_scan_edit()`에 체크필드 라벨 목록 전달**

`app.py` 7642~7661행의 `outbound_scan_edit(batch_id)` 함수에서, `return render_template(...)` 호출에 인자 하나 추가:

```python
    return render_template("outbound_scan.html", batch=batch, items=items,
                           planned_serials=planned_serials,
                           plan_rows=progress["rows"], plan_summary=progress["summary"],
                           body_photo_enabled=db.outbound_body_photo_enabled(),
                           can_revoke_confirm=can_revoke_confirm,
                           check_fields=[(f, db.OUTBOUND_CHECK_LABELS[f]) for f in db.OUTBOUND_CHECK_FIELDS])
```

- [ ] **Step 3: 검증 (Flask test client, 격리된 임시 DB 사용 — DATA_DIR 오버라이드로 프로덕션 iqc.db 보호)**

```bash
python -c "
import os
os.environ['DATA_DIR'] = r'C:\Users\Jaiden\AppData\Local\Temp\claude\verify_outbound_checks'
os.makedirs(os.environ['DATA_DIR'], exist_ok=True)
import app as flaskapp
import database as db
client = flaskapp.app.test_client()
client.post('/login', data={'username':'admin','password':'admin1234'})

bid = db.create_outbound_batch('테스트거래처', '2026-09-15', '테스터', 'admin')
iid = db.add_outbound_item(bid, 'TESTSN001', '테스트제품', None)

r = client.post(f'/outbound/item/{iid}/check', data={'field':'check_tie','value':'PASS'},
                headers={'X-Requested-With':'XMLHttpRequest'})
assert r.get_json()['ok']
assert r.get_json()['result_auto'] is None

for f, v in [('check_qr','PASS'), ('check_wrap','PASS'), ('check_rst','FAIL')]:
    client.post(f'/outbound/item/{iid}/check', data={'field':f,'value':v}, headers={'X-Requested-With':'XMLHttpRequest'})
r = client.post(f'/outbound/item/{iid}/check', data={'field':'check_access','value':'SPECIAL'}, headers={'X-Requested-With':'XMLHttpRequest'})
assert r.get_json()['result_auto'] == 'SPECIAL'

r = client.post(f'/outbound/item/{iid}/result-override', data={'value':'FAIL'}, headers={'X-Requested-With':'XMLHttpRequest'})
assert r.get_json()['result_override'] == 'FAIL'

r = client.post(f'/outbound/item/{iid}/check', data={'field':'check_tie','value':'INVALID'}, headers={'X-Requested-With':'XMLHttpRequest'})
assert r.status_code == 400

print('OK: 라우트 검증 통과')
"
```

---

## Task 3: 출력 엑셀에 11열 서식 반영 (`report_builder.py`)

**Files:**
- Modify: `C:\Users\Jaiden\Desktop\iqc-app\report_builder.py`
  - `_place_photos_in_area()`: 917~973행, `stretch` 파라미터 추가
  - `build_outbound_excel()`: 1401~1500행, 헤더/폭/데이터행/사진배치 전면 수정

**Interfaces:**
- Consumes: Task 1에서 `list_outbound_items()`가 각 item dict에 붙여주는 `result_auto`/`result_effective`, 그리고 5개 `check_*` 값
- Produces: `_place_photos_in_area(..., stretch=False)` — 기존 호출부(`_insert_ncr_photos`)는 인자 안 바뀜, `build_outbound_excel`만 `stretch=True`로 호출

- [ ] **Step 1: `_place_photos_in_area()`에 `stretch` 파라미터 추가**

917~973행 함수 전체를 아래로 교체:

```python
def _place_photos_in_area(ws, photo_paths, col_widths_emu, row_heights_emu, col_s, row_s,
                           PILImage=None, stretch=False):
    """photo_paths(이미 존재 확인이 끝난 절대경로 리스트)를 col_widths_emu × row_heights_emu로
    정의된 사각 영역 안에 가로로 N등분해서 배치한다. col_s/row_s: 이 영역의 좌상단이
    워크시트에서 몇 번째 열/행인지(0-based).

    stretch=False(기본값): N장을 총 너비 N등분 → 각 슬롯 안에서 비율 유지하며 최대
    크기로 맞춤 → 수직 중앙 정렬(기존 동작 그대로, `_insert_ncr_photos()`가 이 방식을 씀).

    stretch=True(2026-09-15 신규): 비율을 무시하고 슬롯 전체를 사진으로 꽉 채운다
    (출고 이력 엑셀 인디케이터/본체 사진 — 사용자가 "비율 상관없이 셀에 꽉 채워도
    된다"고 명시적으로 확인함). 이땐 슬롯의 좌상단/우하단 두 좌표를 `TwoCellAnchor`로
    직접 지정한다 — `OneCellAnchor(_from=..., ext=...)`는 실제 Microsoft Excel에서
    openpyxl이 spPr에 xfrm을 안 써주면 ext 크기를 무시하고 앵커 셀 전체로 늘어나는
    실측 버그가 있다(CLAUDE.md 7-4-2절, QR 라벨 커밋 88013b9로 실제 고친 사례).
    "정확히 슬롯 하나만큼 채우기"가 목적인 이 용도엔 애초에 TwoCellAnchor 쪽이
    LibreOffice·실제 Excel 양쪽에서 일관되게 그려지므로 더 안전하다.

    _insert_ncr_photos()(불량현상사진, stretch 인자 안 씀 → 기존 동작 그대로)와
    build_outbound_excel()(출고 이력, stretch=True)이 이 함수 하나를 공유한다
    (CLAUDE.md 8-1절 공용 헬퍼 원칙)."""
    if not photo_paths:
        return

    n = len(photo_paths)
    total_w_emu = sum(col_widths_emu)
    total_h_emu = sum(row_heights_emu)
    slot_w = total_w_emu // n

    def _resolve(abs_pos, offsets, base_idx):
        cum = 0
        for j, size in enumerate(offsets):
            if cum + size > abs_pos:
                return base_idx + j, abs_pos - cum
            cum += size
        return base_idx + len(offsets) - 1, abs_pos - (cum - offsets[-1])

    for i, path in enumerate(photo_paths):
        if stretch:
            iw, ih = slot_w, total_h_emu
        else:
            aspect = 4 / 3
            if PILImage:
                try:
                    with PILImage.open(path) as pil:
                        ow, oh = pil.size
                        if oh:
                            aspect = ow / oh
                except Exception:
                    pass
            if aspect >= slot_w / total_h_emu:
                iw, ih = slot_w, int(slot_w / aspect)
            else:
                iw, ih = int(total_h_emu * aspect), total_h_emu

        x_abs = i * slot_w
        y_abs = 0 if stretch else (total_h_emu - ih) // 2

        img_col, img_col_off = _resolve(x_abs, col_widths_emu, col_s)
        img_row, img_row_off = _resolve(y_abs, row_heights_emu, row_s)

        img = XLImage(path)
        if stretch:
            end_col, end_col_off = _resolve(x_abs + iw, col_widths_emu, col_s)
            end_row, end_row_off = _resolve(y_abs + ih, row_heights_emu, row_s)
            img.anchor = TwoCellAnchor(
                editAs="oneCell",
                _from=AnchorMarker(col=img_col, colOff=img_col_off, row=img_row, rowOff=img_row_off),
                to=AnchorMarker(col=end_col, colOff=end_col_off, row=end_row, rowOff=end_row_off),
            )
        else:
            img.anchor = OneCellAnchor(
                _from=AnchorMarker(col=img_col, colOff=img_col_off, row=img_row, rowOff=img_row_off),
                ext=XDRPositiveSize2D(iw, ih),
            )
        ws.add_image(img)
```

- [ ] **Step 2: `build_outbound_excel()` 헤더/폭/열 구조를 11열로 확장**

1401~1500행 함수 전체를 아래로 교체:

```python
def build_outbound_excel(batch, items, photo_dir):
    """출고 배치 1건을 실제 회사 서식(사용자 제공 참고파일 "출고 내역서" 기준,
    시트명 "출고내역")에 맞춰 xlsx로 만들어 BytesIO로 반환한다(디스크 저장 안 함).

    batch: {"customer", "ship_date", "handler", "round_no"} 등을 가진 dict.
    items: database.list_outbound_items()의 형태 — 각 item에 "photos" 리스트(각 photo는
           {"file_path", "kind"}, kind는 'indicator'/'body'), 그리고 2026-09-15부터
           5개 check_*(체결/QR/간지포장/RST/부속품, 값은 'PASS'/'FAIL'/'SPECIAL'/None)와
           "result_auto"/"result_effective"(자동판정/실제적용판정, database.py에서 계산됨)가
           같이 붙어 온다.
    photo_dir: 사진이 실제 저장된 디렉터리(app.py의 OUTBOUND_PHOTO_DIR) — DB에는 파일명만
    있으므로 절대경로로 바꾸는 데 필요하다."""
    import io as _io
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.utils import get_column_letter
    try:
        from PIL import Image as PILImage
    except ImportError:
        PILImage = None

    wb = Workbook()
    ws = wb.active
    ws.title = "출고내역"
    ws.sheet_view.showGridLines = False

    bold = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Side(style="thin", color="B7BEC9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="E7EAF0")

    ws["A1"] = "출고 내역서"
    ws["A1"].font = Font(bold=True, size=16)
    ws.merge_cells("A1:K1")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")

    ws["A3"] = f"거래처 {batch.get('customer') or ''}"
    ws["C3"] = f"차수 {batch.get('round_no') or ''}"
    ws["D3"] = f"출고일 {batch.get('ship_date') or ''}"
    ws["K3"] = f"담당자 {batch.get('handler') or ''}"
    for cell in ("A3", "C3", "D3", "K3"):
        ws[cell].font = bold

    # 2026-09-15 확장: 실제 회사 서식 예시 데이터로 확정한 11열 구조.
    # 순번/S·N/제품명·모델명(1~3) → 품질확인 5종(4~8) → 사진 2종(9~10) → 판정(11).
    HEADER_ROW = 5
    headers = [
        "순번", "S/N", "제품명/모델명",
        "체결 상태 확인\n(가대 다리, 탱크 다리,\n네마)",
        "QR 번호\n부착 상태 확인",
        "간지 포장 상태",
        "RST단자\n나무판 결착",
        "부속품 유무 확인",
        "인디케이터 사진", "본체사진", "판정",
    ]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=HEADER_ROW, column=i, value=h)
        c.font = bold
        c.fill = header_fill
        c.alignment = center
        c.border = border
    ws.row_dimensions[HEADER_ROW].height = 45  # 3줄 헤더가 잘리지 않게

    widths = [6, 16, 22, 12, 12, 11, 11, 12, 16, 16, 9]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # fit-to-page를 안 걸면 LibreOffice가 오른쪽 열을 인쇄영역 밖으로 통째로 잘라버린다
    # (2026-09-15, 7-5절 함정 재발 이력 있음). 반드시 이 3줄 세트로
    # sheet_properties.pageSetUpPr을 직접 건드려야 한다(ws.page_setup.fitToPage = True
    # 직접대입은 AttributeError 남, 7-5절 참고).
    ws.page_setup.orientation = ws.ORIENTATION_LANDSCAPE
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True

    CHAR_TO_EMU = 7 * 9525
    col_widths_emu = [int(w * CHAR_TO_EMU) for w in widths]
    ROW_HEIGHT_PT = 80
    row_height_emu = ROW_HEIGHT_PT * 12700

    # 0-based 열 인덱스: A=0...K=10. 인디케이터사진=I(8), 본체사진=J(9).
    PHOTO_INDICATOR_COL = 8
    PHOTO_BODY_COL = 9

    DATA_START_ROW = HEADER_ROW + 1
    for offset, it in enumerate(items):
        row_i = DATA_START_ROW + offset
        ws.row_dimensions[row_i].height = ROW_HEIGHT_PT
        values = [
            offset + 1, it["serial_no"], it.get("product_name") or "",
            it.get("check_tie") or "", it.get("check_qr") or "",
            it.get("check_wrap") or "", it.get("check_rst") or "",
            it.get("check_access") or "",
        ]
        for j, v in enumerate(values, start=1):
            c = ws.cell(row=row_i, column=j, value=v)
            c.border = border
            c.alignment = center

        # 9=인디케이터사진, 10=본체사진 (값은 안 씀, _place_photos_in_area가 이미지로 채움)
        ws.cell(row=row_i, column=9).border = border
        ws.cell(row=row_i, column=10).border = border

        result_cell = ws.cell(row=row_i, column=11, value=it.get("result_effective") or "")
        result_cell.border = border
        result_cell.alignment = center
        result_cell.font = bold

        photos = it.get("photos") or []
        indicator_paths = [
            os.path.join(photo_dir, p["file_path"]) for p in photos
            if (p.get("kind") or "indicator") == "indicator"
            and os.path.exists(os.path.join(photo_dir, p["file_path"]))
        ]
        body_paths = [
            os.path.join(photo_dir, p["file_path"]) for p in photos
            if p.get("kind") == "body"
            and os.path.exists(os.path.join(photo_dir, p["file_path"]))
        ]
        # stretch=True: 비율 무시하고 셀 꽉 채우기 (사용자 명시적 요청, 2026-09-15)
        _place_photos_in_area(ws, indicator_paths, [col_widths_emu[PHOTO_INDICATOR_COL]],
                               [row_height_emu], PHOTO_INDICATOR_COL, row_i - 1, PILImage, stretch=True)
        _place_photos_in_area(ws, body_paths, [col_widths_emu[PHOTO_BODY_COL]],
                               [row_height_emu], PHOTO_BODY_COL, row_i - 1, PILImage, stretch=True)

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
```

(주의: 담당자 셀을 기존 `E3`에서 `K3`로 옮겼다 — 열이 5개에서 11개로 늘면서 이전처럼 E3에 두면 새로 끼어든 체크항목 헤더와 겹쳐 보인다. 맨 오른쪽 K열로 옮겨서 겹침을 피했다. 이 위치가 실제 보기에 어색하면 designer/developer 판단으로 조정 가능 — 기능적 요구사항은 아님.)

- [ ] **Step 3: LibreOffice로 1차 확인**

`_test_outbound.xlsx`를 임시 DB로 만들어서 LibreOffice로 PDF 변환 후 확인할 것:
- 11개 열(순번~판정)이 전부 잘리지 않고 한 페이지에 보이는지
- 판정 열에 "SPECIAL"이 찍혀 있는지 (PASS,PASS,PASS,FAIL,SPECIAL 조합)
- 헤더 3줄 텍스트가 안 잘리는지

- [ ] **Step 4: 실제 Excel(COM)로 사진 stretch 검증 — 사진이 있는 케이스로 재확인**

CLAUDE.md 7-4-2절/11절 필수 사항, LibreOffice만으로 완료 선언 금지. 사진 1장을 실제로 넣어서(`db.add_outbound_item_photo`) PowerShell + Excel COM(`Excel.Application` → `ExportAsFixedFormat`)으로 PDF 내보낸 뒤 `pymupdf`(fitz)로 4배율 이미지화해서 인디케이터/본체 사진 칸이 실제로 슬롯 전체를 채우는지(비율 무시, 칸 경계까지 꽉 참) 육안 확인할 것.

- [ ] **Step 5: NCR 통보서 회귀 확인 (stretch 기본값 False 안 깨졌는지)**

기존 NCR 통보서 하나를 골라 PDF를 다시 만들어서 불량현상사진이 여전히 비율 유지로(찌그러지지 않고) 나오는지 확인.

---

## Task 4: 출고 스캔 화면 UI (`templates/outbound_scan.html`)

**Files:**
- Modify: `C:\Users\Jaiden\Desktop\iqc-app\templates\outbound_scan.html`

**Interfaces:**
- Consumes: Task 2에서 `outbound_scan_edit()`가 넘기는 `check_fields`(리스트 `[(field, label), ...]`), Task 1/2에서 만든 `POST /outbound/item/<id>/check`, `POST /outbound/item/<id>/result-override`

**UI 설계 방향(기능 요구사항 — 세부 CSS/여백/폰트는 developer가 동작하는 최소 마크업까지만, 이후 designer가 다듬음):**
- "저장된 항목" 표에 새 열 2개 추가: **품질확인(5개)**, **판정**. 위치는 "제품명/모델명"과 "인디케이터 사진" 사이(엑셀 열 순서와 맞춤).
- 품질확인 열: 5개 항목 각각 "라벨 + PASS/FAIL/SPECIAL 3버튼"을 세로로 쌓은 컴팩트한 목록. 버튼은 기존 `badge pass`/`badge fail`/`badge special` 클래스를 그대로 재사용해서(16절에서 이미 확정된 판정 의미 색상) 선택 상태를 표시한다 — 새 색상을 만들지 말 것. **"스크롤 부담 최소화" 요구사항 때문에 5행을 세로로 쌓되 한 행당 높이를 최소화**(작은 폰트, 얇은 패딩)할 것 — 참고 목표치: 한 행 ~22px × 5행 ≈ 110px, 기존 사진 그리드 셀 높이(~90~100px)와 비슷한 수준으로 맞출 것.
- 판정 열: 자동판정 배지(합격/불합격/특채/미검사) + 오버라이드 없으면 그대로, 있으면 "(수동)" 표시 + 되돌리기 버튼. 오버라이드용 버튼 3개(합격으로/불합격으로/특채로)는 항상 노출.
- 터치 즉시 저장(모달·확인창 없음) — 기존 사진 업로드와 동일한 즉시저장 아키텍처를 따른다.
- 배치가 확인 완료(`batch.confirmed_at`)면 모든 버튼에 `disabled` 부여(잠금 규칙 유지, CLAUDE.md 22절).

- [ ] **Step 1: 헤더 행 수정 (104~109행)**

```html
      <tr>
        <th>S/N</th><th>제품명/모델명</th><th>품질확인</th><th>판정</th><th>인디케이터 사진</th>
        {% if body_photo_enabled %}<th>본체 사진</th>{% endif %}
        <th></th>
      </tr>
```

- [ ] **Step 2: 각 항목 행에 품질확인/판정 셀 삽입 (117행 `</td>` 다음, 130행 `<td>` 인디케이터사진 셀 앞)**

```html
        <td>
          <div class="ob-check-list" data-item="{{ it.id }}">
            {% for field, label in check_fields %}
            <div class="ob-check-row" data-field="{{ field }}">
              <span class="ob-check-label">{{ label }}</span>
              {% for v, txt in [('PASS','합격'), ('FAIL','불합격'), ('SPECIAL','특채')] %}
              <button type="button"
                      class="ob-check-btn {{ 'badge pass' if v=='PASS' else ('badge fail' if v=='FAIL' else 'badge special') }}{{ ' active' if it[field] == v else '' }}"
                      data-value="{{ v }}" {{ 'disabled' if batch.confirmed_at else '' }}
                      onclick="setOutboundCheck({{ it.id }}, '{{ field }}', '{{ v }}', this)">{{ txt }}</button>
              {% endfor %}
            </div>
            {% endfor %}
          </div>
        </td>
        <td>
          <div class="ob-result" data-item="{{ it.id }}">
            {% set eff = it.result_effective %}
            {% if eff == 'PASS' %}<span class="badge pass" id="ob-result-badge-{{ it.id }}">합격</span>
            {% elif eff == 'FAIL' %}<span class="badge fail" id="ob-result-badge-{{ it.id }}">불합격</span>
            {% elif eff == 'SPECIAL' %}<span class="badge special" id="ob-result-badge-{{ it.id }}">특채</span>
            {% else %}<span class="muted" id="ob-result-badge-{{ it.id }}">미검사</span>{% endif %}
            {% if it.result_override %}<span class="muted" style="font-size:11px;"> (수동)</span>{% endif %}
            {% if not batch.confirmed_at %}
            <div style="margin-top:4px; display:flex; gap:4px; flex-wrap:wrap;">
              <button type="button" class="btn secondary" style="margin-top:0; padding:2px 8px; font-size:11px;" onclick="setOutboundResultOverride({{ it.id }}, 'PASS')">합격으로</button>
              <button type="button" class="btn secondary" style="margin-top:0; padding:2px 8px; font-size:11px;" onclick="setOutboundResultOverride({{ it.id }}, 'FAIL')">불합격으로</button>
              <button type="button" class="btn secondary" style="margin-top:0; padding:2px 8px; font-size:11px;" onclick="setOutboundResultOverride({{ it.id }}, 'SPECIAL')">특채로</button>
              {% if it.result_override %}
              <button type="button" class="btn secondary" style="margin-top:0; padding:2px 8px; font-size:11px;" onclick="setOutboundResultOverride({{ it.id }}, '')">↺ 자동으로</button>
              {% endif %}
            </div>
            {% endif %}
          </div>
        </td>
```

- [ ] **Step 3: 빈 상태 colspan 수정 (188행)**

```html
      <tr class="empty-row"><td colspan="{{ 7 if body_photo_enabled else 6 }}" style="text-align:center; color:var(--muted);">저장된 항목이 없어.</td></tr>
```

- [ ] **Step 4: JS — CHECK_FIELDS 배열 + 두 저장 함수 + 판정 배지 갱신 함수**

스크립트 블록(268행 `(function () {` 안, `var batchId = {{ batch.id }};` 다음 줄)에 추가:

```javascript
  var CHECK_FIELDS = [{% for f, label in check_fields %}["{{ f }}", "{{ label }}"],{% endfor %}];
  var RESULT_LABEL_MAP = { PASS: ['badge pass', '합격'], FAIL: ['badge fail', '불합격'], SPECIAL: ['badge special', '특채'] };

  function updateResultBadge(itemId, resultAuto, resultOverride) {
    var badge = document.getElementById('ob-result-badge-' + itemId);
    if (!badge) return;
    var eff = resultOverride || resultAuto;
    var m = RESULT_LABEL_MAP[eff] || ['muted', '미검사'];
    badge.className = m[0];
    badge.textContent = m[1];
  }

  window.setOutboundCheck = function (itemId, field, value, btnEl) {
    var fd = new FormData();
    fd.append('field', field);
    fd.append('value', value);
    fetch('/outbound/item/' + itemId + '/check', {
      method: 'POST', body: fd, headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (!data.ok) { alert(data.error || '저장 실패'); return; }
      var row = btnEl.closest('.ob-check-row');
      row.querySelectorAll('.ob-check-btn').forEach(function (b) { b.classList.remove('active'); });
      btnEl.classList.add('active');
      updateResultBadge(itemId, data.result_auto, data.result_override);
    }).catch(function () { alert('저장 실패(네트워크 확인)'); });
  };

  window.setOutboundResultOverride = function (itemId, value) {
    var fd = new FormData();
    fd.append('value', value);
    fetch('/outbound/item/' + itemId + '/result-override', {
      method: 'POST', body: fd, headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (!data.ok) { alert(data.error || '저장 실패'); return; }
      updateResultBadge(itemId, data.result_auto, data.result_override);
    }).catch(function () { alert('저장 실패(네트워크 확인)'); });
  };

  function checksCellHtml(itemId) {
    var html = '<td><div class="ob-check-list" data-item="' + itemId + '">';
    CHECK_FIELDS.forEach(function (pair) {
      var field = pair[0], label = pair[1];
      html += '<div class="ob-check-row" data-field="' + field + '"><span class="ob-check-label">' + label + '</span>';
      [['PASS', 'badge pass', '합격'], ['FAIL', 'badge fail', '불합격'], ['SPECIAL', 'badge special', '특채']].forEach(function (t) {
        html += '<button type="button" class="ob-check-btn ' + t[1] + '" data-value="' + t[0] + '" onclick="setOutboundCheck(' + itemId + ', \'' + field + '\', \'' + t[0] + '\', this)">' + t[2] + '</button>';
      });
      html += '</div>';
    });
    return html + '</div></td>';
  }

  function resultCellHtml(itemId) {
    return '<td><div class="ob-result" data-item="' + itemId + '">' +
      '<span class="muted" id="ob-result-badge-' + itemId + '">미검사</span>' +
      '<div style="margin-top:4px; display:flex; gap:4px; flex-wrap:wrap;">' +
      '<button type="button" class="btn secondary" style="margin-top:0;padding:2px 8px;font-size:11px;" onclick="setOutboundResultOverride(' + itemId + ', \'PASS\')">합격으로</button>' +
      '<button type="button" class="btn secondary" style="margin-top:0;padding:2px 8px;font-size:11px;" onclick="setOutboundResultOverride(' + itemId + ', \'FAIL\')">불합격으로</button>' +
      '<button type="button" class="btn secondary" style="margin-top:0;padding:2px 8px;font-size:11px;" onclick="setOutboundResultOverride(' + itemId + ', \'SPECIAL\')">특채로</button>' +
      '</div></div></td>';
  }
```

- [ ] **Step 5: `appendItemRow()`에 새 셀 끼워넣기 (458~494행)**

`appendItemRow` 함수 안, `photosCell('indicator', ...)`를 조합하는 `html` 변수 생성 부분을 수정 — 제품명 셀과 인디케이터사진 셀 사이에 `checksCellHtml`/`resultCellHtml` 호출을 끼워넣는다:

```javascript
    var html = '<td>' + item.serial_no + ' <span class="plan-badge ' + (inPlan ? 'ok">계획' : 'extra">계획외') + '</span></td>' +
      '<td><form method="POST" action="/outbound/item/' + item.id + '/edit" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">' +
      '<input type="text" name="product_name" value="' + (item.product_name || '').replace(/"/g, '&quot;') + '" placeholder="제품명/모델명" style="flex:1;min-width:160px;">' +
      '<button type="submit" class="btn secondary" style="margin-top:0;">저장</button></form></td>' +
      checksCellHtml(item.id) + resultCellHtml(item.id) +
      photosCell('indicator', pending.indicator);
```

(그 아래 `if (bodyPhotoEnabled) html += photosCell('body', ...)`와 삭제 `<td>`는 그대로 둔다.)

- [ ] **Step 6: 검증 — 실제 렌더링 확인 (CLAUDE.md 11절/17절, HTTP 200만으로 완료 선언 금지)**

Flask test client로 로그인 후 페이지를 로컬 HTML로 저장하고 headless Chrome으로 스크린샷(`dangerouslyDisableSandbox: true` 필요, `file:///C:/...` 윈도우 드라이브 표기 사용). 확인할 것:
- 412px 폭(안드로이드 태블릿/폰 기준 근사치)에서 품질확인 5행이 세로 스크롤을 크게 늘리지 않는지
- 5개 항목 버튼이 터치하기에 너무 작지 않은지(최소 터치영역 확보)
- badge pass/fail/special 색상이 16절 팔레트(초록/빨강/주황)와 일치하는지

확인 후 반드시 테스트 데이터 정리.

---

## Self-Review 결과

- **스펙 커버리지**: 5개 항목 터치선택(Task 4) / 컴팩트 UI(Task 4 설계방향) / 판정 우선순위 SPECIAL>FAIL>PASS(Task 1 Step 1) / 수동 오버라이드(Task 1/2/4) / 사진 비율무시 채우기(Task 3, NCR은 영향 없음 회귀확인 포함) — 전부 태스크에 반영됨.
- **타입/시그니처 일관성**: `compute_outbound_item_auto_result(item: dict)`이 Task 1(정의)·Task 2(라우트에서 `dict(db.get_outbound_item(...))` 변환 후 호출)·Task 3(`report_builder`는 이미 dict인 `list_outbound_items()` 결과를 그대로 받음)에서 전부 "dict 입력"으로 일관됨. `field`/`value` 파라미터명도 라우트(Task 2)·JS(Task 4)·DB 함수(Task 1) 전부 동일.

## 실행 안내

Task 1→2→3→4 순서로(각 태스크가 이전 태스크의 DB 컬럼/라우트를 전제하므로 반드시 순서대로) 구현하고, 완료 후 quality-watcher로 검증받을 것. Task 4(화면)는 이후 designer 에이전트가 CSS/여백을 다듬는 단계를 거치는 게 이 프로젝트의 표준 흐름(CLAUDE.md 13절 "개발-디자인 경계").
