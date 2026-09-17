# 입고 "우선검사" 플래그 기능 스펙

**목표:** 입고 등록 화면에서 자재별로 "우선검사" 여부를 표시하고, 검사 대기 목록에서 그 항목이 눈에 띄고 상단 정렬되게 한다.

**범위 제한 (사용자 확정, developer는 이 두 화면 외에 건드리지 말 것):**
- `templates/intake.html` (`/intake` 라우트) — 등록 + 대기 목록 표시/토글
- `templates/inspect_select.html` (`/inspect/new` 라우트) — 배지 표시 + 상단 정렬
- 검사입력(`inspect_form.html`), 검사이력, 대시보드, 성적서, 엑셀 내보내기(`/inspect/new/export.xlsx`) 등 다른 화면·산출물은 이 플래그를 노출하거나 반영하지 않는다.

**DB 컬럼명:** `intake_list.is_priority` (INTEGER NOT NULL DEFAULT 0) — `database.py`의 `assembly_no` 컬럼(212~232행)과 같은 멱등 마이그레이션 패턴을 그대로 따른다.

---

## Task 1: DB 마이그레이션 — `is_priority` 컬럼 추가

**파일:** `C:\Users\Jaiden\Desktop\iqc-app\database.py`

`intake_list` 테이블 정의(213~223행) 바로 아래, 기존 컬럼 마이그레이션 블록(224~232행) 안에 이미 있는 패턴을 그대로 따라 추가한다:

```python
existing_intake_cols = [row[1] for row in cur.execute("PRAGMA table_info(intake_list)").fetchall()]
if "product_name" not in existing_intake_cols:
    cur.execute("ALTER TABLE intake_list ADD COLUMN product_name TEXT")
if "assembly_no" not in existing_intake_cols:
    cur.execute("ALTER TABLE intake_list ADD COLUMN assembly_no TEXT")
if "is_priority" not in existing_intake_cols:
    # 우선검사 플래그 — 입고 등록 화면에서 체크하면 검사 대기 목록 상단에 강조 표시된다.
    # 검사완료 후엔 의미 없음(대기 상태에서만 씀), 다른 화면엔 노출 안 함(스펙 확정 사항).
    cur.execute("ALTER TABLE intake_list ADD COLUMN is_priority INTEGER NOT NULL DEFAULT 0")
```

`SELECT *`로 조회하는 기존 함수(`list_intake`, `search_intake`, `get_intake`)는 코드 수정 없이 새 컬럼이 자동으로 딸려온다 — 건드릴 필요 없음.

**Task 1-1: 토글용 DB 함수 추가**

`set_intake_status()` 함수(1549~1553행) 바로 아래에 같은 스타일로 추가:

```python
def set_intake_priority(intake_id, is_priority):
    conn = get_conn()
    conn.execute("UPDATE intake_list SET is_priority = ? WHERE id = ?",
                (1 if is_priority else 0, intake_id))
    conn.commit()
    conn.close()
```

**검증:** `python -c "import database as db; db.init_db()"` 실행 후 에러 없이 끝나는지 확인. 기존 `iqc.db`가 있으면 `PRAGMA table_info(intake_list)`로 `is_priority` 컬럼이 추가됐는지 확인(sqlite3 CLI 또는 짧은 파이썬 스크립트로).

---

## Task 2: 입고 등록 POST 처리 — 우선검사 컬럼 파싱 + 저장

**파일:** `C:\Users\Jaiden\Desktop\iqc-app\app.py`

### 2-1. `_merge_duplicate_intake_rows()` (1409~1447행) — 같은 배치 내 합산 시 플래그 보존

기존 필드 보존 로직(assembly_no 처리, 1445~1446행) 바로 아래에 추가:

```python
if not existing.get("assembly_no") and r.get("assembly_no"):
    existing["assembly_no"] = r["assembly_no"]
if r.get("is_priority"):
    existing["is_priority"] = 1
```
(둘 중 하나라도 우선검사면 합쳐진 행도 우선검사로 — OR 규칙)

### 2-2. `intake()` POST 핸들러(1450행~) — 그리드 열 파싱

1466~1471행:

```python
# 스프레드시트 열 순서: 입고날짜, 납품업체, 발주번호, 제품명, 자재번호, 입고수량
for line_no, cols in enumerate(grid_rows, start=1):
    cols = [(c or "").strip() for c in cols]
    while len(cols) < 6:
        cols.append("")
    receive_date, supplier, po_number, product_name, material_no, quantity = cols[:6]
```

이걸 7열로 바꾼다(마지막에 우선검사 값 추가, `"1"`이면 체크된 것):

```python
# 스프레드시트 열 순서: 입고날짜, 납품업체, 발주번호, 제품명, 자재번호, 입고수량, 우선검사
for line_no, cols in enumerate(grid_rows, start=1):
    cols = [(c or "").strip() for c in cols]
    while len(cols) < 7:
        cols.append("")
    receive_date, supplier, po_number, product_name, material_no, quantity, priority_raw = cols[:7]
    is_priority = 1 if priority_raw == "1" else 0
```

`if not any([...])` 빈 행 판정(1472행)은 `priority_raw`를 넣지 않는다 — 체크박스만 켜져 있고 자재번호가 비어있으면 어차피 바로 아래서 "자재번호가 비어 있어 → 건너뜀" 처리되므로 그대로 둔다.

MA 자동전개 분기(1480~1495행)와 일반 자재 분기(1496~1505행) 둘 다 `rows.append({...})` 딕셔너리에 `"is_priority": is_priority`를 추가한다 — MA 전개 시 생성되는 모든 파츠 행이 부모 행과 동일한 우선검사 값을 상속받는다:

```python
if ma_info:
    for component_no in ma_info["components"]:
        component_material = db.get_material(component_no)
        component_name = component_material["material_name"] if component_material else product_name
        rows.append({
            "material_no": component_no,
            "quantity": quantity or None,
            "supplier": supplier,
            "receive_date": receive_date,
            "po_number": po_number,
            "product_name": component_name,
            "assembly_no": ma_info["ma_master"],
            "is_priority": is_priority,
        })
else:
    rows.append({
        "material_no": material_no,
        "quantity": quantity or None,
        "supplier": supplier,
        "receive_date": receive_date,
        "po_number": po_number,
        "product_name": product_name,
        "is_priority": is_priority,
    })
```

`intake_confirm_dups()`(2365행~)는 이 딕셔너리들을 세션 JSON으로 그대로 왕복시켜 `db.add_intake_bulk()`에 넘기는 구조라 — **수정 불필요**, `is_priority` 키가 딕셔너리에 있으면 자동으로 같이 실려간다.

### 2-3. `db.add_intake_bulk()` (database.py 1532~1546행) — INSERT에 컬럼 추가

```python
def add_intake_bulk(rows):
    """
    rows: list of dict (material_no, quantity, supplier, receive_date, po_number, product_name, assembly_no, is_priority)
    """
    conn = get_conn()
    cur = conn.cursor()
    for r in rows:
        cur.execute("""
            INSERT INTO intake_list (material_no, quantity, supplier, receive_date, po_number, product_name, assembly_no, is_priority)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (r["material_no"], r.get("quantity"), r.get("supplier"),
              r.get("receive_date"), r.get("po_number"), r.get("product_name"), r.get("assembly_no"),
              1 if r.get("is_priority") else 0))
    conn.commit()
    conn.close()
```

**검증:** 개발 서버를 띄우고 `/intake`에서 자재 하나를 체크박스 켠 채로 등록 → sqlite3로 `SELECT material_no, is_priority FROM intake_list ORDER BY id DESC LIMIT 1;` 결과가 `1`인지 확인. 체크박스 끈 채로 등록한 것도 하나 더 확인해서 `0`인지 확인. 조립품 파츠번호로 체크박스 켠 채 등록해서 전개된 파츠 행 전부 `is_priority=1`인지 확인.

---

## Task 3: `templates/intake.html` — 등록 그리드에 체크박스 열 추가

**파일:** `C:\Users\Jaiden\Desktop\iqc-app\templates\intake.html`

### 3-1. 헤더 행 (81~85행)

`입고수량` 열과 삭제 열(빈 `<th></th>`) 사이에 새 열 추가:

```html
<tr>
  <th></th>
  <th style="min-width:130px;">자재번호</th>
  <th style="min-width:190px;">제품명 (자동)</th>
  <th style="min-width:90px;">입고수량</th>
  <th style="min-width:70px;">우선검사</th>
  <th></th>
</tr>
```

### 3-2. JS `addRow()` (146~158행)

```javascript
function addRow() {
  rowCount++;
  const tr = document.createElement('tr');
  tr.innerHTML =
    `<td class="rownum">${rowCount}</td>` +
    `<td><input type="text" data-col="material_no"></td>` +
    `<td class="name-cell empty"></td>` +
    `<td><input type="text" data-col="quantity" inputmode="numeric"></td>` +
    `<td style="text-align:center;"><input type="checkbox" data-col="is_priority" style="width:17px; height:17px;"></td>` +
    `<td class="del-cell"><button type="button" class="del-row-btn" onclick="this.closest('tr').remove()">✕</button></td>`;
  grid.appendChild(tr);
  tr.querySelector('input[data-col="material_no"]').addEventListener('input', () => fillName(tr));
  return tr;
}
```

체크박스는 `ITEM_COLS`(116행, 드래그선택/붙여넣기 대상)에 넣지 않는다 — 드래그채우기·엑셀붙여넣기 대상은 지금처럼 `material_no`/`quantity` 텍스트 칸만으로 충분하고, 체크박스까지 넣으면 `cellInfo()`/`paintSel()` 등 기존 드래그로직이 checkbox와 text input을 섞어 다뤄야 해서 불필요하게 커진다(범위 밖).

### 3-3. 제출 핸들러 (278~300행)

```javascript
document.getElementById('intakeForm').addEventListener('submit', function (e) {
  const supplier = document.getElementById('supplierCommon').value.trim();
  const date = document.getElementById('dateCommon').value.trim();
  const lot = document.getElementById('lotCommon').value.trim();

  // 백엔드는 [입고날짜, 납품업체, 발주번호(=로트번호), 제품명, 자재번호, 입고수량, 우선검사] 7칸 순서를 기대함.
  const rows = [];
  itemRows().forEach(tr => {
    const no = tr.querySelector('input[data-col="material_no"]').value.trim();
    if (!no) return;
    const qty = tr.querySelector('input[data-col="quantity"]').value.trim();
    const priority = tr.querySelector('input[data-col="is_priority"]').checked ? '1' : '0';
    const name = (Object.prototype.hasOwnProperty.call(NAME_MAP, no) ? NAME_MAP[no] : '') || '';
    rows.push([date, supplier, lot, name, no, qty, priority]);
  });

  if (rows.length === 0) {
    e.preventDefault();
    alert('자재번호를 최소 한 줄 이상 입력해줘.');
    return;
  }
  document.getElementById('rowsJson').value = JSON.stringify(rows);
});
```

### 3-4. 대기 목록 표(351~377행) — 체크박스로 토글 가능하게

대기 탭 표 헤더(354행)와 행(356~370행)에 열 추가:

```html
<table>
  <tr><th>입고날짜</th><th>납품업체</th><th>로트번호</th><th>제품명</th><th>자재번호</th><th>입고수량</th><th>우선검사</th></tr>
  {% for r in p['items'] %}
  <tr>
    <td>{{ (r['receive_date'] | date_korean) or '-' }}</td>
    <td>{{ r['supplier'] or '-' }}</td>
    <td style="white-space:pre-line;">{{ r['po_number'] or '-' }}</td>
    <td>{{ name_map.get(r['material_no']) or r['product_name'] or '-' }}</td>
    <td>
      {{ r['material_no'] }}
      {% if r['material_no'] in group_nos %}
        <span class="badge pending" style="margin-left:4px;">조립품</span>
      {% elif r['material_no'] not in registered %}
        <span class="badge fail" style="margin-left:4px;">규격 미등록</span>
      {% endif %}
    </td>
    <td>{{ r['quantity'] or '-' }}</td>
    <td style="text-align:center;">
      <input type="checkbox" class="priority-toggle" data-intake-id="{{ r['id'] }}"
             {% if r['is_priority'] %}checked{% endif %}
             style="width:17px; height:17px;">
    </td>
  </tr>
  {% else %}
  <tr><td colspan="7" style="text-align:center; color:var(--muted);">
    ...
```

`colspan="6"` → `colspan="7"`로 변경(372행). **완료(done) 탭 표(378~398행)는 건드리지 않는다** — 검사완료 후엔 우선검사 표시가 의미 없다는 스펙 범위 결정.

체크박스 클릭 시 서버에 토글 요청하는 스크립트를 파일 하단 스크립트 블록(112~302행 안, `})();` 직전)에 추가:

```javascript
document.querySelectorAll('.priority-toggle').forEach(function (chk) {
  chk.addEventListener('change', function () {
    const id = this.dataset.intakeId;
    const prevChecked = !this.checked;  // 실패 시 되돌릴 값
    fetch('/intake/' + id + '/toggle-priority', { method: 'POST' })
      .then(r => r.json())
      .then(data => {
        if (!data.ok) {
          alert(data.error || '변경 실패');
          this.checked = prevChecked;
        } else {
          this.checked = !!data.is_priority;
        }
      })
      .catch(() => { alert('네트워크 오류로 변경 실패'); this.checked = prevChecked; });
  });
});
```
(이 스크립트는 IIFE `(function () { ... })();` 안, `for (let i = 0; i < 12; i++) addRow();`부터 시작하는 기존 블록 맨 끝에 그대로 이어 붙이면 됨 — `.priority-toggle`은 페이지 하단 대기목록 표에만 있고 등록 폼과는 무관하므로 같은 IIFE 안에 있어도 충돌 없음.)

**Task 3-1 라우트:** `app.py`에 새 라우트 추가 (`intake()` 함수 바로 아래, 2365행 `intake_confirm_dups` 이전 아무 곳이나):

```python
@app.route("/intake/<int:intake_id>/toggle-priority", methods=["POST"])
@perm_required("intake")
def intake_toggle_priority(intake_id):
    """입고 등록 화면(대기 탭)에서만 쓰는 우선검사 플래그 토글. 검사완료된 건은 바꿀 수 없다."""
    row = db.get_intake(intake_id)
    if row is None:
        return jsonify({"ok": False, "error": "해당 입고 건을 찾을 수 없어."}), 404
    if row["status"] != "대기":
        return jsonify({"ok": False, "error": "이미 검사완료된 건은 우선검사 표시를 바꿀 수 없어."}), 409
    new_val = 0 if row["is_priority"] else 1
    db.set_intake_priority(intake_id, new_val)
    return jsonify({"ok": True, "is_priority": new_val})
```

**검증:** `/intake` 대기 탭에서 체크박스를 켜고/끄고 새로고침해서 상태가 유지되는지 확인. 이미 검사완료된 건(완료 탭)엔 체크박스 자체가 안 보이는지 확인. 개발자도구 네트워크 탭에서 `POST /intake/<id>/toggle-priority` 응답이 `{"ok":true,"is_priority":0 또는 1}`인지 확인.

---

## Task 4: `inspect_select` 정렬 로직 — 우선검사 항목 최상단

**파일:** `C:\Users\Jaiden\Desktop\iqc-app\app.py`, `_inspect_select_rows()` 함수 (2444~2472행)

기존 날짜 정렬 마지막 줄(2471행) 바로 다음에 한 줄 추가:

```python
sort_dir = request.args.get("sort", "desc")
if sort_dir not in ("asc", "desc"):
    sort_dir = "desc"
dated = [(r, _parse_any_date(r["receive_date"])) for r in pending]
with_date = sorted((x for x in dated if x[1] is not None), key=lambda x: x[1], reverse=(sort_dir == "desc"))
without_date = [x for x in dated if x[1] is None]
pending = [r for r, _ in with_date] + [r for r, _ in without_date]
# 우선검사 항목을 맨 위로 — 안정 정렬(stable sort)이라 위에서 이미 정해진
# (날짜순 → 날짜없는것 맨뒤) 순서는 각 그룹(우선/일반) 안에서 그대로 유지된다.
pending = sorted(pending, key=lambda r: 0 if r["is_priority"] else 1)
return pending, f, query, sort_dir
```

이 함수는 `inspect_select()`(화면)와 `inspect_select_export()`(엑셀 내보내기) 둘 다 공유하므로 엑셀 내보내기도 자동으로 같은 순서로 나간다 — **엑셀 파일에 "우선검사" 열을 추가하는 건 이번 스코프 밖**(사용자가 명시한 범위: 입고 등록 화면 + 검사 대기 목록 화면 두 개뿐), 손대지 않는다.

**검증:** 대기 항목 3~4건 중 입고일이 가장 오래된 것 하나를 우선검사로 체크 → `/inspect/new`에서 정렬 방향(오름/내림차순 토글)과 무관하게 그 항목이 항상 목록 맨 위에 오는지 확인.

---

## Task 5: `inspect_select.html` — 배지 표시

**파일:** `C:\Users\Jaiden\Desktop\iqc-app\templates\inspect_select.html`

자재번호 셀(317~331행)의 기존 배지들(MA 파츠 / 조립품 / 규격 미등록) 앞에 우선검사 배지를 추가한다 — 자재번호 바로 옆이 이 화면에서 "여러 상태 배지를 붙이는" 기존 관례 위치다:

```html
<td class="clickable" onclick="openRow('{{ url_for('inspect_form', intake_id=r['id']) }}{{ inspect_qs }}')">
  {% if r['is_priority'] %}
    <span class="badge priority" style="margin-right:4px;">🚩 우선검사</span>
  {% endif %}
  {{ r['material_no'] }}
  {% if r['assembly_no'] %}
    <span class="badge special" style="margin-left:4px;" title="{{ r['assembly_no'] }} 조립품의 파츠로 자동 전개됨">MA 파츠</span>
  {% elif r['material_no'] in group_nos %}
    <span class="badge pending" style="margin-left:4px;">조립품</span>
  {% endif %}
  {% if r['material_no'] not in registered %}
    ...
```

새 배지 클래스 `.badge.priority`가 필요하다 — `templates/base.html`의 공용 배지 정의 블록(143~149행) 맨 아래에 추가:

```css
.badge.priority { color: #0e7490; background: #cffafe; }
```

**색상 선택 근거 (developer는 이 값을 임의로 다른 기존 의미색으로 바꾸지 말 것):** CLAUDE.md 16절에 따라 초록(합격)/빨강(불합격·규격이탈)/호박색(`--pending`, 승인대기)/주황(`#ea580c`, 특채)/보라(`#7c3aed`, 검토)는 전부 이미 다른 뜻으로 예약돼 있다. 위 청록색(`#0e7490`/`#cffafe`)은 이 프로젝트에서 아직 쓰이지 않는 색이라 새 의미(우선검사)에 충돌 없이 쓸 수 있다. 이 값은 developer가 기능 동작을 위해 넣는 최소 스타일이고, CLAUDE.md 13절 파이프라인대로 이후 designer 에이전트가 색상 톤을 다듬을 수 있다 — 다만 그때도 위에 나열한 예약색과는 겹치지 않게 유지할 것.

아이콘은 `⭐`가 아니라 `🚩`를 썼다 — `⭐`는 이미 `templates/users.html`(29행)에서 "최종결정권자" 배지로 쓰이고 있어 재사용하면 의미가 겹친다(관련 없는 전혀 다른 개념인데 같은 아이콘을 쓰면 사용자가 혼동함). `🚩`는 이 코드베이스 어디에도 쓰이지 않는 걸 확인했다.

**검증:** `/inspect/new`에서 우선검사 체크된 항목의 자재번호 셀에 "🚩 우선검사" 배지가 보이는지, MA파츠/조립품/규격미등록 배지와 같이 있을 때도 겹치거나 깨지지 않는지 확인. 우선검사 아닌 항목엔 배지가 안 보이는지 확인.

---

## Task 6: quality-watcher 검증

CLAUDE.md 11절에 따라, 위 구현(app.py/database.py/intake.html/inspect_select.html/base.html) 완료 후 코드를 사용자에게 보고하기 전에 **quality-watcher 서브에이전트를 호출해서 검증**받는다. 특히 다음을 중점 확인하도록 요청할 것:
- 우선검사 플래그가 스펙 범위(입고 등록/검사대기 목록) 밖으로 새지 않았는지(다른 템플릿/라우트에 노출 안 됐는지)
- `add_intake_bulk`/`_merge_duplicate_intake_rows`가 `is_priority` 키가 없는 기존 호출부(있다면)에서도 안전한지(`r.get("is_priority")` 방어 확인)
- `intake_toggle_priority` 라우트가 `@perm_required("intake")`로 제대로 게이트됐는지, 검사완료된 건에 대한 토글을 서버에서도 막는지(클라이언트 우회 방어)
- `.badge.priority` 색상이 기존 판정/특채/검토 의미색과 겹치지 않는지

---

## 관련 파일 경로 (developer가 열어야 할 것)

- `C:\Users\Jaiden\Desktop\iqc-app\database.py` (Task 1, 2-3)
- `C:\Users\Jaiden\Desktop\iqc-app\app.py` (Task 2-1, 2-2, 3-1 라우트, 4)
- `C:\Users\Jaiden\Desktop\iqc-app\templates\intake.html` (Task 3)
- `C:\Users\Jaiden\Desktop\iqc-app\templates\inspect_select.html` (Task 5)
- `C:\Users\Jaiden\Desktop\iqc-app\templates\base.html` (Task 5, `.badge.priority` 정의)
