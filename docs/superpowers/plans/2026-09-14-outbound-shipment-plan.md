# 출고(완제품 S/N·QR·사진) 관리 기능 — 구현 스펙 / 실행계획

> 이 문서는 `writing-plans` 스킬 형식을 따르되, 이 프로젝트에는 자동화 테스트 스위트(pytest 등)가 없고 CLAUDE.md 11절/19절이 명시한 대로 "실제 화면·xlsx를 직접 열어서 확인"하는 수동 검증이 표준이므로, 각 태스크의 "테스트" 단계는 **Flask test client + sqlite3 CLI + 실제 iqc.db(개발용 사본)를 이용한 스크립트 검증**으로 대체했다. 코드는 이 문서를 작성한 planner가 아니라 **developer 에이전트가 작성**해야 한다.

**Goal:** 완제품 S/N을 QR로 발급·인쇄하고, 출고 시점에 QR을 스캔해 S/N·제품명·수량·사진을 배치(출고 건) 단위로 기록하는 완전히 새로운 하위시스템을 추가한다.

**Architecture:** 기존 IQC(입고검사) 흐름과 데이터베이스·권한·화면을 전혀 공유하지 않는 독립 서브시스템으로 만든다. 새 테이블 4개(`finished_goods_serials`/`outbound_batches`/`outbound_items`/`outbound_item_photos`)는 `materials`/`inspections` 등 기존 테이블에 FK로 연결하지 않는다(`material_bom_links`가 `materials`에 FK 없이 연결하는 기존 관례와 동일 — 18절 참고). 사진 첨부는 NCR의 `_save_ncr_photo()`(범용, `dest_dir` 인자로 재사용 가능)를 그대로 재사용한다. 출고 스캔 화면은 "새 항목은 클라이언트에서만 누적하다가 '저장' 한 번에 서버로 통째 제출, 이미 저장된 항목은 개별 라우트로 즉시 수정/삭제"라는 두 갈래 흐름으로 설계한다(자세한 이유는 Task 9 참고).

**Tech Stack:** Flask, SQLite(`database.py`의 `get_conn()` 패턴), Jinja2, openpyxl(엑셀), 신규 의존성 `qrcode`(서버사이드 QR PNG 생성, Pillow는 이미 설치돼 있음), CDN JS 라이브러리 `html5-qrcode`(카메라 QR 스캔 — 이 프로젝트가 `pdf.js`를 cdnjs에서 로드하는 선례와 동일 패턴, 여기서는 jsDelivr 사용).

**Spec:** `docs/superpowers/specs/2026-09-14-outbound-shipment-design.md` (사용자 승인 완료된 확정 설계 — 이 계획 문서는 그 설계의 DB 스키마·화면 3개·권한·"확정된 결정 사항" 표를 바꾸지 않고 구현 스펙만 구체화한 것).

## Global Constraints

```
- 새 테이블 4개는 database.py의 init_db() 안에 change_points와 같은 패턴으로 직접 추가한다
  (마이그레이션 파일 분리 없음).
- finished_goods_serials ↔ outbound_items.serial_no는 FK 없이 문자열로만 느슨하게 연결한다
  — 미등록 S/N도 경고만 하고 출고 항목으로 받아들여야 하기 때문(설계문서 확정사항).
- S/N 중복 발급은 UNIQUE 제약 + 앱 레벨 사전 체크 둘 다로 막는다(발급 자체 차단, 예외 없음).
- "이미 다른 배치에 쓰인 S/N"과 "미등록 S/N"은 어디서도 절대 진행을 막지 않는다 — 항상 경고
  배너만 띄우고 계속 진행 허용(설계문서 확정사항, 두 케이스 원칙 통일).
- 사진은 outbound_photos/ 디렉터리(DATA_DIR 기준)에 저장하고 경로만 DB에 남긴다
  (ncr_photos/와 동일한 배치, 압축 저장 로직도 _save_ncr_photo() 그대로 재사용).
- 확정(=배치 저장)된 이후에도 항목 추가·수정·삭제가 항상 가능해야 한다 — 성적서처럼 잠기는
  개념이 없다.
- 엑셀 출력은 지금은 report_builder.py 안에 템플릿 없이 openpyxl로 직접 그리는 기본 표
  하나뿐이다. 사용자가 실제 양식 파일을 제공하면 그 함수 하나만 NCR/성적서와 같은
  "shutil.copy 템플릿" 방식으로 재작업한다 — 지금부터 그 자리(report_builder.py)에 만든다.
- 권한은 새 권한 `outbound` 문자열 하나만 신설하고, 3개 화면 + 그 화면들이 쓰는 보조 라우트
  (QR 이미지, S/N 상태체크, 항목/사진 수정삭제, 엑셀출력) 전부 `@perm_required("outbound")`
  하나로 게이트한다.
- 새 메뉴 "출고"는 입고/검사/승인과 같은 레벨(최상단)에 신설한다.
- 코드 수정 완료 후 quality-watcher 검증 필수(CLAUDE.md 11절). CSS/카메라 UI처럼 "실제로
  보이는지"는 코드리뷰만으로 완료 선언하지 않는다(CLAUDE.md 11절/17절, verification-before-
  completion).
```

---

## 실제 코드 확인 결과 (설계문서의 "구현 단계에서 결정" 항목들을 여기서 확정)

| 설계문서가 열어둔 것 | 조사 결과 / 결정 |
|---|---|
| QR 생성 방식(서버 파이썬 vs 클라이언트 JS) | **서버 파이썬 `qrcode` 패키지로 확정.** Pillow가 이미 설치돼 있어 `qrcode.make(data, box_size=10, border=2)`가 별도 이미지 라이브러리 없이 바로 PNG를 만든다. 저장 안 하고 요청마다 즉석 생성(`/outbound/qr`) — 파일 정리할 게 없어 가장 단순하다. |
| QR 인쇄 방식 | 도면 인쇄(`_drawing_modal.html`의 `drawingPrint()` — 숨긴 iframe + PDF)는 **재사용하지 않는다.** QR은 PDF가 아니라 이미지 한 장 + 텍스트뿐이라, `dashboard.html`/`ncr_detail.html`이 이미 쓰는 훨씬 단순한 패턴(`window.print()` + `@media print { header, footer, .no-print{display:none} }`)이 그대로 맞는다. |
| QR 스캔 라이브러리 | `html5-qrcode`(jsDelivr CDN, `https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js`)로 확정. 이 프로젝트는 이미 `pdf.js`를 cdnjs에서 로드해 Android 태블릿 Chrome에서 실사용 검증됐다(`_drawing_modal.html`) — 같은 성격의 공개 CDN이라 접근성 문제 없다고 판단. **developer는 구현 시점에 이 URL이 살아있는지 한 번 더 확인할 것**(패키지 버전 고정 관례는 `pdf.js/3.11.174`처럼 이미 있음). |
| 사진 첨부 UI 재사용 | `templates/ncr_form.html:109-162`의 "📸 카메라로 촬영/🖼️ 갤러리에서 선택" 두 버튼 + 미리보기 그리드(버퍼 배열 + `URL.createObjectURL`)를 그대로 재사용. `app.py:5739`의 `_save_ncr_photo(file_storage, dest_dir, base_name)`는 `dest_dir`이 매개변수라 **수정 없이 바로 재사용 가능**(NCR 전용 상수가 함수 안에 박혀있지 않음). |
| 엑셀 출력 위치 | `report_builder.py`에 `build_outbound_excel()` 신설(뒤에서 NCR/성적서와 같은 템플릿-복사 패턴으로 교체될 예정이므로 처음부터 여기 둠). `app.py:7028`(`dashboard_export_xlsx`)처럼 디스크 저장 없이 `BytesIO`로 즉시 다운로드시킨다 — 공식 IQC 문서(성적서/NCR)와 달리 "성적서 발행" 폴더 추적 대상이 아니므로 disk 저장 불필요. |
| 배치 항목 누적→일괄저장 데이터 포맷 | JSON 컨벤션은 `intake.html`/`spec_quick_add`가 이미 쓰는 `rows_json`/`items_json` 히든필드 패턴 재사용. 단 **사진 파일은 JSON에 못 담기므로**, 항목 배열 순서(0-based)에 맞춰 `photos_0`, `photos_1`, ... 이름의 `<input type=file multiple>`을 제출 직전에 JS `DataTransfer` API로 동적 생성해 채운다(브라우저 표준 API, 별도 라이브러리 불필요). |
| 기존 저장된 항목의 수정/삭제 | "새 항목 누적→일괄저장"과는 별도로, 이미 DB에 있는 항목은 `supplier_detail.html`이 쓰는 "폼 액션 URL + 일반 POST, AJAX는 선택사항" 패턴으로 즉시 반영한다(항목당 별도 라우트 3개: 수정/삭제/사진삭제). 스캔 화면 재진입 시 마다 반복 편집하는 흔치 않은 동작이라 AJAX까지는 과함(YAGNI) — 일반 form POST + redirect로 충분. |

**사용자에게 물어봐야 할 것: 없음.** 위 항목들은 설계문서가 "구현 단계에서 결정"이라고 명시적으로 위임한 것들이고, 설계의 확정 표(DB 스키마·화면 3개·권한 1개)와 모순되지 않는다.

---

## Task 1: DB 스키마 — 신규 테이블 4개

**Files:** `database.py:647` 부근(`full_inspection_units` 컬럼 마이그레이션 직후, `conn.commit()` 직전)

- [ ] **Step 1: 테이블 정의 추가**

`database.py`의 `init_db()` 안, 아래 블록(현재 644~649행) 바로 앞에 삽입:

```python
    existing_fi_cols = [row[1] for row in cur.execute("PRAGMA table_info(full_inspection_units)").fetchall()]
    if "gauge_name" not in existing_fi_cols:
        cur.execute("ALTER TABLE full_inspection_units ADD COLUMN gauge_name TEXT DEFAULT ''")

    # ---- 여기부터 신규 삽입 ----
    # 출고(완제품 S/N·QR·사진) 관리 — 입고검사(IQC)와 완전히 별개의 신규 하위시스템.
    # finished_goods_serials/outbound_items는 FK 없이 문자열(serial_no)로만 느슨하게 연결한다
    # — 미등록 S/N, 다른 배치에 이미 쓰인 S/N도 경고만 하고 출고 항목으로 받아들여야 하기
    # 때문에 FK로 강제하면 안 된다(material_bom_links가 materials에 FK 없는 것과 같은 이유).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS finished_goods_serials (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            serial_no   TEXT NOT NULL UNIQUE,
            issued_by   TEXT,
            issued_at   TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_batches (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            customer      TEXT,
            ship_date     TEXT,
            handler       TEXT,
            created_by    TEXT,
            created_at    TEXT DEFAULT (datetime('now','localtime')),
            updated_at    TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_items (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id        INTEGER NOT NULL REFERENCES outbound_batches(id),
            serial_no       TEXT NOT NULL,
            product_name    TEXT,
            quantity        INTEGER,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_item_photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     INTEGER NOT NULL REFERENCES outbound_items(id),
            file_path   TEXT NOT NULL,
            uploaded_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # ---- 신규 삽입 끝 ----

    conn.commit()
    conn.close()
```

- [ ] **Step 2: 검증**

```bash
python -c "import database as db; db.init_db()"
sqlite3 iqc.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'outbound%' OR name='finished_goods_serials';"
```
Expected: `finished_goods_serials`, `outbound_batches`, `outbound_items`, `outbound_item_photos` 4줄 출력. 다시 실행해도(`db.init_db()` 재호출) 에러 없이 그대로 유지되는지(멱등성) 한 번 더 실행해서 확인.

- [ ] **Step 3: Commit** — 메인 세션이 검토 후 직접 커밋(developer는 git commit 금지, CLAUDE.md 13절)

---

## Task 2: `database.py` — S/N 발급 관련 함수

**Files:** `database.py` — Task 1에서 추가한 테이블 정의 다음, 또는 `add_change_point()` 근처(3339행 부근)에 새 섹션으로 추가.

**Interfaces (Produces):**
- `create_serial(serial_no: str, issued_by: str) -> int` — 성공 시 새 row id, 중복이면 `ValueError` 발생
- `serial_exists(serial_no: str) -> bool`
- `list_serials(query: str|None = None, limit: int = 50) -> list[sqlite3.Row]`
- `find_outbound_item_batch(serial_no: str, exclude_batch_id: int|None = None) -> int|None`

- [ ] **Step 1: 함수 작성**

```python
# ---------- 출고 완제품 S/N ----------

def serial_exists(serial_no):
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM finished_goods_serials WHERE serial_no=?", (serial_no,)).fetchone()
    conn.close()
    return row is not None


def create_serial(serial_no, issued_by):
    """완제품 S/N을 발급 이력에 등록. 이미 발급된 S/N이면 재발급 자체를 막는다(ValueError)."""
    if serial_exists(serial_no):
        raise ValueError(f"'{serial_no}'는 이미 발급된 S/N이야.")
    conn = get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO finished_goods_serials (serial_no, issued_by) VALUES (?, ?)",
            (serial_no, issued_by))
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        # UNIQUE 제약이 최종 방어선(동시 발급 경합 시). 위의 사전체크가 거의 다 잡아내므로
        # 실제로 여기 걸릴 일은 드물지만, 걸리면 같은 메시지로 통일해서 처리한다.
        raise ValueError(f"'{serial_no}'는 이미 발급된 S/N이야.")
    finally:
        conn.close()


def list_serials(query=None, limit=50):
    """S/N 발급 이력 최신순. query가 있으면 부분일치로 거른다."""
    conn = get_conn()
    sql = "SELECT * FROM finished_goods_serials WHERE 1=1"
    params = []
    if query:
        sql += " AND serial_no LIKE ?"
        params.append(f"%{query}%")
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def find_outbound_item_batch(serial_no, exclude_batch_id=None):
    """이 S/N이 이미 다른 출고 배치에 쓰였으면 그 batch_id, 아니면 None.
    exclude_batch_id(현재 편집 중인 배치)는 '다른 배치'로 치지 않는다."""
    conn = get_conn()
    sql = "SELECT batch_id FROM outbound_items WHERE serial_no=?"
    params = [serial_no]
    if exclude_batch_id is not None:
        sql += " AND batch_id != ?"
        params.append(exclude_batch_id)
    row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", params).fetchone()
    conn.close()
    return row["batch_id"] if row else None
```

- [ ] **Step 2: 검증 스크립트**

```python
import database as db

TEST_SN = "TESTSN_PLAN_0001"
conn = db.get_conn(); conn.execute("DELETE FROM finished_goods_serials WHERE serial_no=?", (TEST_SN,)); conn.commit(); conn.close()

sid = db.create_serial(TEST_SN, "테스트")
assert isinstance(sid, int) and sid > 0
assert db.serial_exists(TEST_SN) is True

try:
    db.create_serial(TEST_SN, "테스트")
    raise SystemExit("FAIL: 중복 발급이 막히지 않았어")
except ValueError:
    print("OK: 중복 발급 차단됨")

rows = db.list_serials(query="TESTSN_PLAN")
assert any(r["serial_no"] == TEST_SN for r in rows)
print("OK: list_serials 조회됨,", len(rows), "건")

assert db.find_outbound_item_batch(TEST_SN) is None
print("OK: find_outbound_item_batch (미사용) None 반환")

conn = db.get_conn(); conn.execute("DELETE FROM finished_goods_serials WHERE serial_no=?", (TEST_SN,)); conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 3: Commit** — 메인 세션이 처리

---

## Task 3: `database.py` — 출고 배치/항목/사진 CRUD 함수

**Files:** `database.py` — Task 2 함수들 다음.

**Interfaces (Produces):**
- `create_outbound_batch(customer, ship_date, handler, created_by) -> int`
- `update_outbound_batch(batch_id, customer, ship_date, handler) -> None`
- `get_outbound_batch(batch_id) -> sqlite3.Row | None`
- `list_outbound_batches(query=None, limit=200) -> list[sqlite3.Row]` (각 row에 `item_count` 포함)
- `add_outbound_item(batch_id, serial_no, product_name, quantity) -> int`
- `get_outbound_item(item_id) -> sqlite3.Row | None`
- `list_outbound_items(batch_id) -> list[dict]` (각 dict에 `"photos": [dict, ...]` 포함)
- `update_outbound_item(item_id, product_name, quantity) -> None`
- `delete_outbound_item(item_id) -> list[str]` (삭제된 사진의 `file_path` 목록 반환 — 호출부가 실제 파일 삭제)
- `add_outbound_item_photo(item_id, file_path) -> int`
- `get_outbound_item_photo(photo_id) -> sqlite3.Row | None`
- `delete_outbound_item_photo(photo_id) -> str | None` (삭제된 `file_path` 반환)

- [ ] **Step 1: 함수 작성**

```python
# ---------- 출고 배치/항목/사진 ----------

def create_outbound_batch(customer, ship_date, handler, created_by):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO outbound_batches (customer, ship_date, handler, created_by)
        VALUES (?, ?, ?, ?)
    """, (customer, ship_date, handler, created_by))
    conn.commit()
    batch_id = cur.lastrowid
    conn.close()
    return batch_id


def update_outbound_batch(batch_id, customer, ship_date, handler):
    conn = get_conn()
    conn.execute("""
        UPDATE outbound_batches SET customer=?, ship_date=?, handler=?,
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (customer, ship_date, handler, batch_id))
    conn.commit()
    conn.close()


def get_outbound_batch(batch_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM outbound_batches WHERE id=?", (batch_id,)).fetchone()
    conn.close()
    return row


def list_outbound_batches(query=None, limit=200):
    conn = get_conn()
    sql = """
        SELECT b.*, COUNT(i.id) AS item_count
          FROM outbound_batches b
          LEFT JOIN outbound_items i ON i.batch_id = b.id
         WHERE 1=1
    """
    params = []
    if query:
        sql += " AND (b.customer LIKE ? OR b.handler LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " GROUP BY b.id ORDER BY b.id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def add_outbound_item(batch_id, serial_no, product_name, quantity):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO outbound_items (batch_id, serial_no, product_name, quantity)
        VALUES (?, ?, ?, ?)
    """, (batch_id, serial_no, product_name, quantity))
    conn.commit()
    item_id = cur.lastrowid
    conn.close()
    return item_id


def get_outbound_item(item_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM outbound_items WHERE id=?", (item_id,)).fetchone()
    conn.close()
    return row


def list_outbound_items(batch_id):
    """배치의 항목 + 각 항목의 사진 목록을 함께 반환.
    반환: [{...item 컬럼.., "photos": [{"id":.., "file_path":.., "uploaded_at":..}, ...]}, ...]"""
    conn = get_conn()
    items = conn.execute(
        "SELECT * FROM outbound_items WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
    result = []
    for it in items:
        photos = conn.execute(
            "SELECT * FROM outbound_item_photos WHERE item_id=? ORDER BY id",
            (it["id"],)).fetchall()
        row = dict(it)
        row["photos"] = [dict(p) for p in photos]
        result.append(row)
    conn.close()
    return result


def update_outbound_item(item_id, product_name, quantity):
    conn = get_conn()
    conn.execute(
        "UPDATE outbound_items SET product_name=?, quantity=? WHERE id=?",
        (product_name, quantity, item_id))
    conn.commit()
    conn.close()


def delete_outbound_item(item_id):
    """항목 삭제. 첨부 사진의 실제 파일명 목록을 반환하니 호출부가 파일도 지울 것
    (FK 제약이 있으므로 사진 행 먼저 지우고 항목을 지운다)."""
    conn = get_conn()
    photo_paths = [r["file_path"] for r in conn.execute(
        "SELECT file_path FROM outbound_item_photos WHERE item_id=?", (item_id,)).fetchall()]
    conn.execute("DELETE FROM outbound_item_photos WHERE item_id=?", (item_id,))
    conn.execute("DELETE FROM outbound_items WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return photo_paths


def add_outbound_item_photo(item_id, file_path):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO outbound_item_photos (item_id, file_path) VALUES (?, ?)",
        (item_id, file_path))
    conn.commit()
    photo_id = cur.lastrowid
    conn.close()
    return photo_id


def get_outbound_item_photo(photo_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM outbound_item_photos WHERE id=?", (photo_id,)).fetchone()
    conn.close()
    return row


def delete_outbound_item_photo(photo_id):
    """사진 1장 삭제. 실제 파일명을 반환(호출부가 파일 삭제)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT file_path FROM outbound_item_photos WHERE id=?", (photo_id,)).fetchone()
    conn.execute("DELETE FROM outbound_item_photos WHERE id=?", (photo_id,))
    conn.commit()
    conn.close()
    return row["file_path"] if row else None
```

- [ ] **Step 2: 검증 스크립트**

```python
import database as db

conn = db.get_conn(); conn.execute("DELETE FROM outbound_batches WHERE customer='테스트거래처_PLAN'"); conn.commit(); conn.close()

batch_id = db.create_outbound_batch("테스트거래처_PLAN", "2026-09-14", "홍길동", "테스트")
assert db.get_outbound_batch(batch_id)["customer"] == "테스트거래처_PLAN"

item_id = db.add_outbound_item(batch_id, "TESTSN_ITEM_A", "테스트제품", 5)
items = db.list_outbound_items(batch_id)
assert len(items) == 1 and items[0]["photos"] == []
print("OK: 배치/항목 생성")

photo_id = db.add_outbound_item_photo(item_id, "fake.jpg")
items = db.list_outbound_items(batch_id)
assert len(items[0]["photos"]) == 1
print("OK: 사진 첨부")

db.update_outbound_item(item_id, "수정된제품", 9)
assert db.get_outbound_item(item_id)["product_name"] == "수정된제품"
print("OK: 항목 수정")

assert db.find_outbound_item_batch("TESTSN_ITEM_A") == batch_id
assert db.find_outbound_item_batch("TESTSN_ITEM_A", exclude_batch_id=batch_id) is None
print("OK: find_outbound_item_batch (본인 배치 제외 동작)")

fname = db.delete_outbound_item_photo(photo_id)
assert fname == "fake.jpg"
assert db.list_outbound_items(batch_id)[0]["photos"] == []
print("OK: 사진 삭제")

paths = db.delete_outbound_item(item_id)
assert paths == []  # 이미 위에서 사진을 지웠으니 빈 리스트
assert db.get_outbound_item(item_id) is None
print("OK: 항목 삭제")

conn = db.get_conn(); conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,)); conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 3: Commit** — 메인 세션이 처리

---

## Task 4: `requirements.txt`(qrcode) + `report_builder.py` — 기본 표 엑셀 출력

**Files:**
- Modify: `requirements.txt`
- Modify: `report_builder.py` (파일 끝, 또는 `build_ncr_excel` 섹션 다음)

**Interfaces (Produces):** `report_builder.build_outbound_excel(batch: dict, items: list[dict]) -> io.BytesIO`

- [ ] **Step 1: `requirements.txt`에 한 줄 추가**

```
flask
openpyxl
reportlab
Pillow
pypdf
gunicorn
APScheduler
qrcode
```

**중요(개발자 에이전트가 직접 못 하는 부분):** developer 에이전트는 `pip install`이 금지돼 있다. 로컬 개발환경에서 이 태스크를 검증하려면 **사람이 먼저** `pip install qrcode --break-system-packages`를 실행해야 한다. Render 배포 서버는 `requirements.txt` 기반으로 자동 설치되므로 배포 시에는 별도 조치 불필요.

- [ ] **Step 2: `report_builder.py`에 함수 추가**

```python
def build_outbound_excel(batch, items):
    """출고 배치 1건을 기본 표 형태의 xlsx로 만들어 BytesIO로 반환한다(디스크 저장 안 함).
    사용자가 실제 양식 파일을 제공하면 NCR/성적서와 같은 shutil.copy 템플릿 방식으로
    이 함수 하나만 재작업하면 된다 — 그 전까지는 openpyxl로 직접 표를 그린다.
    batch: {"customer", "ship_date", "handler"} 등을 가진 dict.
    items: database.list_outbound_items()가 돌려주는 형태(각 item에 "photos" 리스트 포함)."""
    import io as _io
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "출고내역"

    bold = Font(bold=True)
    center = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin", color="B7BEC9")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor="E7EAF0")

    ws["A1"] = "출고 내역서"
    ws["A1"].font = Font(bold=True, size=16)
    ws.merge_cells("A1:E1")
    ws["A1"].alignment = center

    ws["A3"] = "거래처"; ws["B3"] = batch.get("customer") or ""
    ws["A4"] = "출고일"; ws["B4"] = batch.get("ship_date") or ""
    ws["A5"] = "담당자"; ws["B5"] = batch.get("handler") or ""
    for cell in ("A3", "A4", "A5"):
        ws[cell].font = bold

    headers = ["번호", "S/N", "제품명/모델명", "수량", "사진 매수"]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=7, column=i, value=h)
        c.font = bold
        c.fill = header_fill
        c.alignment = center
        c.border = border

    for row_i, it in enumerate(items, start=8):
        values = [row_i - 7, it["serial_no"], it.get("product_name") or "",
                  it.get("quantity") if it.get("quantity") is not None else "",
                  len(it.get("photos") or [])]
        for j, v in enumerate(values, start=1):
            c = ws.cell(row=row_i, column=j, value=v)
            c.border = border
            c.alignment = center

    widths = [8, 22, 32, 10, 10]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
```

- [ ] **Step 3: 검증**

```python
import report_builder

batch = {"customer": "테스트거래처", "ship_date": "2026-09-14", "handler": "홍길동"}
items = [
    {"serial_no": "SN001", "product_name": "테스트제품", "quantity": 10, "photos": [{"id": 1, "file_path": "x.jpg"}]},
    {"serial_no": "SN002", "product_name": "테스트제품", "quantity": 5, "photos": []},
]
buf = report_builder.build_outbound_excel(batch, items)

import openpyxl
wb = openpyxl.load_workbook(buf)
ws = wb.active
assert ws["B3"].value == "테스트거래처"
assert ws.cell(row=8, column=2).value == "SN001"
assert ws.cell(row=8, column=5).value == 1   # 사진 매수
assert ws.cell(row=9, column=2).value == "SN002"
print("OK: build_outbound_excel")
```

(로컬에 `qrcode`가 아직 설치 안 됐어도 이 태스크는 영향 없음 — `qrcode`는 Task 5에서 씀.)

- [ ] **Step 4: Commit** — 메인 세션이 처리

---

## Task 5: `app.py` — S/N 발급 라우트 + QR 이미지 라우트

**Files:** `app.py` — 파일 끝(`assembly_delete_bulk()` 함수 다음, `db.init_db()` 호출 전, 현재 7506~7509행 사이)에 새 섹션 시작.

**Interfaces (Consumes):** `db.create_serial`, `db.serial_exists`, `db.list_serials` (Task 2)
**Interfaces (Produces):** 라우트 `outbound_serial_new`, `outbound_qr_image`

- [ ] **Step 1: 라우트 작성**

`app.py`의 `assembly_delete_bulk()` 함수(현재 7496~7506행) 바로 다음, `db.init_db()`(현재 7509행) 바로 전에 새 섹션을 삽입:

```python
# =========================================================================
# 출고(완제품 S/N·QR·사진) 관리 — 입고검사(IQC)와 별개의 신규 하위시스템
# =========================================================================

@app.route("/outbound/qr")
@perm_required("outbound")
def outbound_qr_image():
    """S/N 문자열을 QR PNG로 즉석 생성해서 돌려준다 — 파일로 저장하지 않고
    필요할 때마다(화면 표시/인쇄) 매번 다시 만든다. 저장할 게 없어 정리도 필요 없다."""
    serial_no = (request.args.get("serial_no") or "").strip()
    if not serial_no:
        return "", 400
    import qrcode
    img = qrcode.make(serial_no, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/outbound/serial/new", methods=["GET", "POST"])
@perm_required("outbound")
def outbound_serial_new():
    """완제품 S/N 발급. 발급 시점엔 제품 정보를 연결하지 않는다(설계문서 확정사항) —
    S/N 문자열만 등록하고 QR을 보여준다. 이미 발급된 S/N은 재발급 자체를 막는다."""
    if request.method == "POST":
        serial_no = request.form.get("serial_no", "").strip()
        if not serial_no:
            flash("S/N을 입력해줘.")
            return redirect(url_for("outbound_serial_new"))
        try:
            db.create_serial(serial_no, g.user["display_name"] or g.user["username"])
        except ValueError as e:
            flash(str(e))
            return redirect(url_for("outbound_serial_new"))
        record_change("완제품 S/N 발급", "outbound_serial", None, serial_no)
        flash(f"S/N '{serial_no}' 발급 완료. 아래 QR을 인쇄해서 라벨로 붙여줘.")
        return redirect(url_for("outbound_serial_new", issued=serial_no))

    issued = request.args.get("issued", "").strip()
    q = request.args.get("q", "").strip()
    recent = db.list_serials(query=q or None, limit=50)
    return render_template("outbound_serial.html", issued=issued, recent=recent, q=q)
```

- [ ] **Step 2: 권한 임시 부여(검증용)** — Task 11에서 정식으로 `PERM_GROUPS`에 등록하기 전까지는, `perm_required("outbound")`가 있어도 **admin 계정은 `_user_perms()`가 `ALL_PERMS`를 통째로 돌려주므로 이미 통과된다.** 즉 이 태스크는 Task 11 이전에도 admin으로 검증 가능(이미 확인한 `app.py:638`의 `_user_perms` 로직 참고). 일반 계정으로 검증하려면 Task 11까지 마친 뒤에 할 것.

- [ ] **Step 3: 검증 스크립트**

```python
import app as appmodule
client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

r = client.post("/outbound/serial/new", data={"serial_no": "TESTSN_ROUTE_01"}, follow_redirects=True)
assert r.status_code == 200

r2 = client.get("/outbound/qr?serial_no=TESTSN_ROUTE_01")
assert r2.status_code == 200
assert r2.mimetype == "image/png"
assert len(r2.data) > 100
print("OK: S/N 발급 + QR 이미지 라우트")

# 중복 발급 차단 확인
r3 = client.post("/outbound/serial/new", data={"serial_no": "TESTSN_ROUTE_01"}, follow_redirects=True)
assert "이미 발급된" in r3.get_data(as_text=True)
print("OK: 중복 발급 차단 메시지 노출")

import database as db
conn = db.get_conn(); conn.execute("DELETE FROM finished_goods_serials WHERE serial_no=?", ("TESTSN_ROUTE_01",)); conn.commit(); conn.close()
print("정리 완료")
```

이 태스크는 **Task 4에서 설치한 `qrcode` 패키지가 로컬에 있어야 통과한다** — 없으면 `python -c "import qrcode"`로 먼저 확인.

- [ ] **Step 4: Commit** — 메인 세션이 처리

---

## Task 6: `app.py` — 출고 스캔 라우트 (신규/편집/항목수정/삭제/사진삭제/S-N체크)

**Files:** `app.py` — Task 5에서 만든 섹션 바로 다음.

**Interfaces (Consumes):** `db.create_outbound_batch/update_outbound_batch/get_outbound_batch/add_outbound_item/get_outbound_item/list_outbound_items/update_outbound_item/delete_outbound_item/add_outbound_item_photo/get_outbound_item_photo/delete_outbound_item_photo/find_outbound_item_batch/serial_exists` (Task 2·3), `_save_ncr_photo(file_storage, dest_dir, base_name)` (기존 `app.py:5739`, 수정 없이 재사용)

**Interfaces (Produces):** 라우트 `outbound_scan_new`, `outbound_scan_edit`, `outbound_item_edit`, `outbound_item_delete`, `outbound_photo_delete`, `outbound_serial_check`, `outbound_photo_file`. 전역 `OUTBOUND_PHOTO_DIR`.

- [ ] **Step 1: 라우트 작성**

```python
OUTBOUND_PHOTO_DIR = os.path.join(db.DATA_DIR, "outbound_photos")
os.makedirs(OUTBOUND_PHOTO_DIR, exist_ok=True)


@app.route("/static/outbound_photos/<path:filename>")
def outbound_photo_file(filename):
    return send_from_directory(OUTBOUND_PHOTO_DIR, filename)


@app.route("/outbound/serial-check")
@perm_required("outbound")
def outbound_serial_check():
    """스캔/입력된 S/N의 상태를 JSON으로 돌려준다 — 미등록/다른배치사용 여부.
    둘 다 진행을 막지 않고 경고만 표시하는 용도(설계문서 확정사항)."""
    serial_no = (request.args.get("serial_no") or "").strip()
    exclude_batch_id = request.args.get("exclude_batch_id", type=int)
    if not serial_no:
        return jsonify({"registered": False, "used_in_other_batch": None,
                        "used_in_other_batch_customer": None})
    registered = db.serial_exists(serial_no)
    other_batch_id = db.find_outbound_item_batch(serial_no, exclude_batch_id=exclude_batch_id)
    other_batch_customer = None
    if other_batch_id:
        b = db.get_outbound_batch(other_batch_id)
        other_batch_customer = b["customer"] if b else None
    return jsonify({
        "registered": registered,
        "used_in_other_batch": other_batch_id,
        "used_in_other_batch_customer": other_batch_customer,
    })


def _outbound_scan_submit(batch_id):
    """배치 헤더 저장(신규 생성 또는 갱신) + items_json/photos_N으로 넘어온 새 항목들을
    추가한다. 신규 배치든 기존 배치에 항목을 더 추가하는 것이든 이 함수 하나로 처리한다
    — '새 항목 추가' 로직이 완전히 같기 때문(중복 방지, 8-1절 원칙)."""
    import json as _json, uuid

    customer = request.form.get("customer", "").strip()
    ship_date = request.form.get("ship_date", "").strip()
    handler = request.form.get("handler", "").strip()
    if not customer:
        flash("거래처를 입력해줘.")
        return redirect(request.referrer or url_for("outbound_scan_new"))

    is_new_batch = batch_id is None
    if is_new_batch:
        batch_id = db.create_outbound_batch(customer, ship_date, handler,
                                             g.user["display_name"] or g.user["username"])
    else:
        db.update_outbound_batch(batch_id, customer, ship_date, handler)

    raw_items = request.form.get("items_json", "")
    try:
        new_items = _json.loads(raw_items) if raw_items else []
    except (ValueError, TypeError):
        new_items = []

    if is_new_batch and not new_items:
        flash("신규 출고 건은 최소 1개 항목을 추가한 뒤 저장해줘.")
        conn = db.get_conn(); conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,)); conn.commit(); conn.close()
        return redirect(url_for("outbound_scan_new"))

    added = 0
    for idx, it in enumerate(new_items):
        serial_no = str(it.get("serial_no", "")).strip()
        if not serial_no:
            continue
        product_name = str(it.get("product_name", "")).strip()
        quantity_raw = it.get("quantity")
        try:
            quantity = int(quantity_raw)
        except (ValueError, TypeError):
            quantity = None

        item_id = db.add_outbound_item(batch_id, serial_no, product_name, quantity)
        added += 1
        for file in request.files.getlist(f"photos_{idx}"):
            if not file or not file.filename:
                continue
            ext = os.path.splitext(file.filename)[1].lower() or ".jpg"
            if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
                continue
            base = f"ob{batch_id}_{item_id}_{uuid.uuid4().hex[:8]}"
            fname = _save_ncr_photo(file, OUTBOUND_PHOTO_DIR, base)
            db.add_outbound_item_photo(item_id, fname)

    action = "출고 배치 생성" if is_new_batch else "출고 배치 정보 수정"
    record_change(action, "outbound_batch", batch_id, f"{customer} (신규 항목 {added}건)")
    flash(f"출고 배치가 저장됐어. (신규 항목 {added}건 추가)" if added else "출고 배치 정보가 저장됐어.")
    return redirect(url_for("outbound_scan_edit", batch_id=batch_id))


@app.route("/outbound/scan/new", methods=["GET", "POST"])
@perm_required("outbound")
def outbound_scan_new():
    if request.method == "POST":
        return _outbound_scan_submit(batch_id=None)
    return render_template("outbound_scan.html", batch=None, items=[],
                           today=_dt.now().strftime("%Y-%m-%d"),
                           default_handler=g.user["display_name"] or g.user["username"])


@app.route("/outbound/scan/<int:batch_id>", methods=["GET", "POST"])
@perm_required("outbound")
def outbound_scan_edit(batch_id):
    batch = db.get_outbound_batch(batch_id)
    if batch is None:
        flash("존재하지 않는 출고 배치야.")
        return redirect(url_for("outbound_history"))
    if request.method == "POST":
        return _outbound_scan_submit(batch_id=batch_id)
    items = db.list_outbound_items(batch_id)
    return render_template("outbound_scan.html", batch=batch, items=items,
                           today=_dt.now().strftime("%Y-%m-%d"),
                           default_handler=batch["handler"] or "")


@app.route("/outbound/item/<int:item_id>/edit", methods=["POST"])
@perm_required("outbound")
def outbound_item_edit(item_id):
    item = db.get_outbound_item(item_id)
    if item is None:
        return jsonify({"ok": False, "error": "항목을 찾을 수 없어."}), 404
    product_name = request.form.get("product_name", "").strip()
    quantity_raw = request.form.get("quantity", "").strip()
    try:
        quantity = int(quantity_raw) if quantity_raw else None
    except ValueError:
        quantity = None
    db.update_outbound_item(item_id, product_name, quantity)
    record_change("출고 항목 수정", "outbound_item", item_id,
                  f"{item['serial_no']} / {product_name} / {quantity}")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    flash("항목이 수정됐어.")
    return redirect(url_for("outbound_scan_edit", batch_id=item["batch_id"]))


@app.route("/outbound/item/<int:item_id>/delete", methods=["POST"])
@perm_required("outbound")
def outbound_item_delete(item_id):
    item = db.get_outbound_item(item_id)
    if item is None:
        return jsonify({"ok": False, "error": "항목을 찾을 수 없어."}), 404
    batch_id = item["batch_id"]
    photo_names = db.delete_outbound_item(item_id)
    for fname in photo_names:
        try:
            os.remove(os.path.join(OUTBOUND_PHOTO_DIR, fname))
        except OSError:
            pass
    record_change("출고 항목 삭제", "outbound_item", item_id, item["serial_no"])
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    flash("항목이 삭제됐어.")
    return redirect(url_for("outbound_scan_edit", batch_id=batch_id))


@app.route("/outbound/photo/<int:photo_id>/delete", methods=["POST"])
@perm_required("outbound")
def outbound_photo_delete(photo_id):
    photo = db.get_outbound_item_photo(photo_id)
    if photo is None:
        return jsonify({"ok": False, "error": "사진을 찾을 수 없어."}), 404
    item = db.get_outbound_item(photo["item_id"])
    fname = db.delete_outbound_item_photo(photo_id)
    if fname:
        try:
            os.remove(os.path.join(OUTBOUND_PHOTO_DIR, fname))
        except OSError:
            pass
    record_change("출고 사진 삭제", "outbound_item", photo["item_id"], "")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True})
    flash("사진이 삭제됐어.")
    return redirect(url_for("outbound_scan_edit", batch_id=item["batch_id"] if item else 0))
```

`OUTBOUND_PHOTO_DIR`을 참조하므로 이 블록은 **Task 5 섹션 바로 다음, `db.init_db()` 호출보다 반드시 앞**에 위치해야 한다(모듈 로드 시점에 `os.makedirs` 실행).

- [ ] **Step 2: 검증 스크립트**

```python
import io, app as appmodule, database as db

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

data = {
    "customer": "테스트거래처_PLAN",
    "ship_date": "2026-09-14",
    "handler": "홍길동",
    "items_json": '[{"serial_no":"TESTSN_ITEM_01","product_name":"테스트품","quantity":"5"}]',
    "photos_0": (io.BytesIO(b"fakejpgbytes"), "a.jpg"),
}
r = client.post("/outbound/scan/new", data=data, content_type="multipart/form-data", follow_redirects=True)
assert r.status_code == 200

batches = db.list_outbound_batches(query="테스트거래처_PLAN")
assert batches, "배치가 안 만들어졌어"
batch_id = batches[0]["id"]
items = db.list_outbound_items(batch_id)
assert len(items) == 1 and items[0]["serial_no"] == "TESTSN_ITEM_01"
assert len(items[0]["photos"]) == 1
print("OK: 출고 스캔 신규 생성 (헤더+항목+사진)")

item_id = items[0]["id"]
r2 = client.post(f"/outbound/item/{item_id}/edit", data={"product_name": "수정됨", "quantity": "7"})
assert r2.status_code in (200, 302)
assert db.get_outbound_item(item_id)["product_name"] == "수정됨"
print("OK: 항목 수정")

r3 = client.get(f"/outbound/serial-check?serial_no=TESTSN_ITEM_01&exclude_batch_id={batch_id}")
j = r3.get_json()
assert j["used_in_other_batch"] is None   # 본인 배치는 '다른 배치'로 안 침
r4 = client.get("/outbound/serial-check?serial_no=TESTSN_ITEM_01")
j4 = r4.get_json()
assert j4["used_in_other_batch"] == batch_id   # exclude 없이 물으면 자기 배치라도 '이미 쓰임'으로 나옴
print("OK: serial-check exclude_batch_id 동작")

r5 = client.post(f"/outbound/item/{item_id}/delete")
assert db.get_outbound_item(item_id) is None
print("OK: 항목 삭제(사진 파일도 같이 지워짐 — OUTBOUND_PHOTO_DIR 확인)")

conn = db.get_conn(); conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,)); conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 3: Commit** — 메인 세션이 처리

---

## Task 7: `app.py` — 출고 이력 + 엑셀 출력 라우트

**Files:** `app.py` — Task 6 섹션 바로 다음.

**Interfaces (Consumes):** `db.list_outbound_batches`, `db.get_outbound_batch`, `db.list_outbound_items` (Task 3), `report_builder.build_outbound_excel` (Task 4)

- [ ] **Step 1: 라우트 작성**

```python
@app.route("/outbound/history")
@perm_required("outbound")
def outbound_history():
    q = request.args.get("q", "").strip()
    batches = db.list_outbound_batches(query=q or None)
    return render_template("outbound_history.html", batches=batches, q=q)


@app.route("/outbound/batch/<int:batch_id>/excel")
@perm_required("outbound")
def outbound_batch_excel(batch_id):
    batch = db.get_outbound_batch(batch_id)
    if batch is None:
        flash("존재하지 않는 출고 배치야.")
        return redirect(url_for("outbound_history"))
    items = db.list_outbound_items(batch_id)
    buf = report_builder.build_outbound_excel(dict(batch), items)
    raw_name = f"출고_{(batch['ship_date'] or '')[:10].replace('-', '')}_{batch['customer'] or ''}.xlsx"
    safe = re.sub(r'[\\/:*?"<>|]', '', raw_name)
    return send_file(buf, as_attachment=True, download_name=safe,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
```

- [ ] **Step 2: 검증 스크립트**

```python
import app as appmodule, database as db

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

batch_id = db.create_outbound_batch("테스트거래처_PLAN2", "2026-09-14", "홍길동", "테스트")
db.add_outbound_item(batch_id, "TESTSN_H1", "테스트품", 3)

r = client.get("/outbound/history")
assert r.status_code == 200
assert "테스트거래처_PLAN2" in r.get_data(as_text=True)
print("OK: 출고 이력 목록")

r2 = client.get(f"/outbound/batch/{batch_id}/excel")
assert r2.status_code == 200
assert r2.mimetype.startswith("application/vnd.openxmlformats")
print("OK: 배치 엑셀 다운로드")

conn = db.get_conn(); conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,)); conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 3: Commit** — 메인 세션이 처리

---

## Task 8: `templates/outbound_serial.html` — S/N 발급 화면

**Files:** Create `templates/outbound_serial.html`

**재사용할 기존 패턴:**
- `templates/change_points.html` — 등록 폼 + 목록 카드 레이아웃 전체 구조(`.card`, `<label>`, `<input>` 배치)
- `templates/ncr_detail.html:5-11`, `templates/dashboard.html:242-243` — 인쇄는 `window.print()` + `@media print { header, footer, .no-print{display:none!important} }` (도면의 iframe+PDF 방식은 재사용하지 않는다 — QR은 이미지 한 장이라 훨씬 단순한 이 패턴이 맞다)

**필수 마크업/동작:**
1. S/N 입력 폼(`method="POST"`, 단일 텍스트 필드 `name="serial_no"`, "QR 생성" 제출버튼)
2. `issued` 쿼리파라미터(POST 리다이렉트로 전달됨)가 있으면:
   - QR 이미지 `<img src="{{ url_for('outbound_qr_image', serial_no=issued) }}">`
   - S/N 문자열을 QR 아래에 텍스트로 같이 표시(인쇄물에 QR만 있으면 육안 식별이 안 됨)
   - "🖨️ 인쇄" 버튼 → `onclick="window.print()"`
   - 이 QR+텍스트 블록만 `@media print`에서 보이게, 나머지(폼/목록/네비)는 `.no-print`로 숨김
3. 최근 발급 이력: `<form method="GET">` 검색창(`name="q"`) + `recent` 리스트를 표(serial_no / issued_by / `issued_at | datetime_korean`)로 렌더링. 삭제 기능 없음(설계 범위 밖).

- [ ] **Step 1: 템플릿 작성** (위 마크업/동작 요구사항대로, `{% extends "base.html" %}`, `{% block title %}` / `{% block content %}` 사용)

- [ ] **Step 2: 검증**

```python
import app as appmodule
client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

r = client.get("/outbound/serial/new")
body = r.get_data(as_text=True)
assert r.status_code == 200
assert 'name="serial_no"' in body

r2 = client.get("/outbound/serial/new?issued=TESTSN_TPL_01")
body2 = r2.get_data(as_text=True)
assert "/outbound/qr?serial_no=TESTSN_TPL_01" in body2
assert "window.print()" in body2
print("OK: outbound_serial.html 렌더")
```

- [ ] **Step 3: quality-watcher 검증 요청** (템플릿 신규 작성이므로 CLAUDE.md 11절에 따라 필수)

- [ ] **Step 4: Commit** — 메인 세션이 처리

---

## Task 9: `templates/outbound_scan.html` — 출고 스캔 화면 (가장 복잡한 화면)

**Files:** Create `templates/outbound_scan.html`

**왜 "새 항목 누적 후 일괄저장" + "기존 항목 즉시수정"이 나뉘는가**: 새로 스캔하는 항목은 사진 File 객체를 아직 서버에 올리지 않은 상태로 여러 개를 빠르게 쌓아야 한다(카메라를 계속 켠 채로) — 설계문서가 "'저장' 누르면 서버에 배치 헤더+항목+사진 한 번에 제출"이라고 명시했다. 반면 이미 저장된 항목(재방문 편집)은 사진이 이미 서버 파일이라 브라우저 File 객체로 되돌릴 수 없으므로, 그 항목만 별도의 즉시반영 라우트(Task 6)로 고친다. 이 구분은 설계문서와 모순되지 않는 구현 세부사항이다.

**재사용할 기존 패턴:**
- `templates/ncr_form.html:109-162` — 사진 첨부 UI(카메라/갤러리 두 버튼 + 미리보기 그리드)를 그대로 복사해서, "현재 입력 중인 항목"의 사진 캡처에 쓴다. 단 `name="photos"` 속성은 빼고(전송 대상 아님, JS가 `.files`만 즉시 읽어서 `curFiles` 배열에 옮김), id만 부여
- `templates/intake.html:48,299` — `rows_json`/JSON.stringify 히든 필드 패턴 → 여기서는 `items_json`으로 이름만 맞춰 재사용(신규 항목 메타데이터만 담음, 사진 제외)
- `templates/change_points.html` — 헤더 폼/목록 카드 레이아웃

**화면 구성 (위에서 아래 순서):**

1. **배치 헤더 폼** (거래처 `name="customer"` required, 출고일 `name="ship_date"` type=date 기본값 `{{ batch.ship_date if batch else today }}`, 담당자 `name="handler"` 기본값 `{{ default_handler }}`) — 이 필드들은 아래 "저장" 버튼과 **같은 `<form id="outboundScanForm" method="POST" enctype="multipart/form-data">`** 안에 있어야 한다.

2. **기존 항목 목록** (`batch`가 `None`이 아닐 때만, 즉 편집 모드): `items`(Task 6에서 넘겨준 서버 데이터)를 `<table>`로 순회. 각 행:
   - S/N(읽기전용 텍스트)
   - 제품명/수량 — **별도의 작은 `<form method="POST" action="{{ url_for('outbound_item_edit', item_id=it.id) }}">`**(메인 폼과 무관, 중첩 `<form>` 금지이므로 반드시 분리)로 인라인 수정, "저장" 버튼
   - 사진 썸네일: `<img src="{{ url_for('outbound_photo_file', filename=p.file_path) }}">` 나열, 각 사진 옆에 "✕" → `<form method="POST" action="{{ url_for('outbound_photo_delete', photo_id=p.id) }}">`(`onsubmit="return confirm(...)"`, `change_points.html`의 삭제버튼 패턴 그대로)
   - 항목 전체 삭제: `<form method="POST" action="{{ url_for('outbound_item_delete', item_id=it.id) }}">` + confirm

3. **새 항목 스캔·입력 영역** (신규/편집 공통, 메인 `outboundScanForm` 안):
   - QR 카메라 뷰: `<div id="qrReaderRegion" style="width:300px;"></div>`
   - 수동 입력 폴백: `<input type="text" id="manualSerialInput" placeholder="카메라를 못 쓰면 S/N을 직접 입력">` + `<button type="button" id="manualSerialConfirm">확인</button>`
   - 경고 배너: `<div id="serialWarning" style="display:none;"></div>` (색상은 `--fail`이 아니라 `--pending`(#d97706, 이미 base.html에 정의된 amber) 계열을 쓸 것 — CLAUDE.md 16절: 빨간색은 "규격이탈/오류" 전용으로 예약돼 있고, 미등록/타배치사용 S/N은 오류가 아니라 경고이므로)
   - 현재 입력 중 필드: `<input id="curSerial" readonly>`, `<input id="curProductName" placeholder="제품명/모델명">`, `<input id="curQuantity" type="number" placeholder="수량">`
   - 사진 캡처(ncr_form.html 마크업 재사용, `name` 속성 없이): 카메라 버튼 `onchange="addCurPhotos(this)"`, 갤러리 버튼도 동일. 미리보기 `<div id="curPhotoPreview">`
   - `<button type="button" onclick="addPendingItem()">리스트에 추가</button>`
   - 누적된(아직 미저장) 항목 목록: `<div id="pendingItemsList">`, 각 줄에 "취소" 버튼(`onclick="removePendingItem(idx)"`)
   - 숨은 필드: `<input type="hidden" name="items_json" id="itemsJsonField">`, 빈 컨테이너 `<div id="pendingFileInputs" style="display:none;"></div>`(제출 직전 JS가 여기에 `photos_0`, `photos_1`... 파일 input을 채워넣음)
   - 최종 `<button type="submit">저장</button>`

- [ ] **Step 1: JS 작성** (`<script>` 블록, 템플릿 안 또는 `{% block scripts %}`가 있으면 그쪽에)

```javascript
<script src="https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
<script>
(function () {
  var pending = [];   // {serial_no, product_name, quantity, files: File[]}
  var curFiles = [];
  var existingSerials = new Set([
    {% for it in items %}"{{ it.serial_no }}",{% endfor %}
  ]);
  var excludeBatchId = {{ batch.id if batch else 'null' }};

  function renderCurPreview() {
    var box = document.getElementById('curPhotoPreview');
    box.innerHTML = curFiles.map(function (f) {
      return '<img src="' + URL.createObjectURL(f) + '" style="width:70px;height:70px;object-fit:cover;border-radius:6px;margin:2px;">';
    }).join('');
  }
  window.addCurPhotos = function (input) {
    for (var i = 0; i < input.files.length; i++) curFiles.push(input.files[i]);
    renderCurPreview();
  };

  function checkSerial(serial) {
    var url = '/outbound/serial-check?serial_no=' + encodeURIComponent(serial);
    if (excludeBatchId) url += '&exclude_batch_id=' + excludeBatchId;
    fetch(url).then(function (r) { return r.json(); }).then(function (data) {
      var box = document.getElementById('serialWarning');
      var msgs = [];
      if (!data.registered) msgs.push('⚠ 발급 이력에 없는 S/N이야. 그래도 계속 진행할 수 있어.');
      if (data.used_in_other_batch) msgs.push('⚠ 이미 다른 출고(' + (data.used_in_other_batch_customer || '') + ')에 쓰인 S/N이야.');
      if (existingSerials.has(serial) || pending.some(function (p) { return p.serial_no === serial; }))
        msgs.push('⚠ 이 출고 건에 이미 추가된 S/N이야.');
      if (msgs.length) { box.style.display = 'block'; box.innerHTML = msgs.join('<br>'); }
      else { box.style.display = 'none'; box.innerHTML = ''; }
    }).catch(function () {});
  }

  function onScanned(serial) {
    document.getElementById('curSerial').value = serial;
    checkSerial(serial);
  }

  document.getElementById('manualSerialConfirm').addEventListener('click', function () {
    var v = document.getElementById('manualSerialInput').value.trim();
    if (v) { onScanned(v); document.getElementById('manualSerialInput').value = ''; }
  });

  window.addPendingItem = function () {
    var serial = document.getElementById('curSerial').value.trim();
    var product = document.getElementById('curProductName').value.trim();
    var qty = document.getElementById('curQuantity').value.trim();
    if (!serial) { alert('S/N을 스캔하거나 입력해줘.'); return; }
    pending.push({ serial_no: serial, product_name: product, quantity: qty, files: curFiles.slice() });
    renderPendingList();
    document.getElementById('curSerial').value = '';
    document.getElementById('curProductName').value = '';
    document.getElementById('curQuantity').value = '';
    curFiles = [];
    renderCurPreview();
    document.getElementById('serialWarning').style.display = 'none';
  };

  window.removePendingItem = function (idx) {
    pending.splice(idx, 1);
    renderPendingList();
  };

  function renderPendingList() {
    var box = document.getElementById('pendingItemsList');
    box.innerHTML = pending.map(function (p, idx) {
      return '<div>' + (idx + 1) + '. ' + p.serial_no + ' / ' + (p.product_name || '-') + ' / '
        + (p.quantity || '-') + ' · 사진 ' + p.files.length + '장 '
        + '<button type="button" onclick="removePendingItem(' + idx + ')">취소</button></div>';
    }).join('') || '<span class="muted">추가된 항목이 없어.</span>';
  }
  renderPendingList();

  document.getElementById('outboundScanForm').addEventListener('submit', function () {
    if (document.getElementById('curSerial').value.trim()) {
      window.addPendingItem();   // 입력해놓고 '리스트에 추가'를 안 누른 채 저장을 누른 경우 구제
    }
    document.getElementById('itemsJsonField').value = JSON.stringify(
      pending.map(function (p) {
        return { serial_no: p.serial_no, product_name: p.product_name, quantity: p.quantity };
      })
    );
    var container = document.getElementById('pendingFileInputs');
    container.innerHTML = '';
    pending.forEach(function (p, idx) {
      var dt = new DataTransfer();
      p.files.forEach(function (f) { dt.items.add(f); });
      var input = document.createElement('input');
      input.type = 'file';
      input.name = 'photos_' + idx;
      input.multiple = true;
      input.style.display = 'none';
      input.files = dt.files;
      container.appendChild(input);
    });
  });

  // QR 카메라 스캐너
  document.addEventListener('DOMContentLoaded', function () {
    if (!window.Html5Qrcode) {
      document.getElementById('qrReaderRegion').innerHTML =
        '<p class="muted">QR 스캐너를 불러오지 못했어. 아래 입력창에 S/N을 직접 입력해줘.</p>';
      return;
    }
    var html5QrCode = new Html5Qrcode('qrReaderRegion');
    html5QrCode.start(
      { facingMode: 'environment' },
      { fps: 10, qrbox: 220 },
      function (decodedText) { onScanned(decodedText); },
      function () { /* 프레임 단위 인식 실패는 무시 — 다음 프레임에서 재시도 */ }
    ).catch(function () {
      document.getElementById('qrReaderRegion').innerHTML =
        '<p class="muted">카메라를 사용할 수 없어. 아래 입력창에 S/N을 직접 입력해줘.</p>';
    });
  });
})();
</script>
```

- [ ] **Step 2: 검증 스크립트 (렌더/마크업)**

```python
import app as appmodule
client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

r = client.get("/outbound/scan/new")
body = r.get_data(as_text=True)
assert r.status_code == 200
for needle in ["qrReaderRegion", "itemsJsonField", 'id="outboundScanForm"',
               "curSerial", "manualSerialInput", "html5-qrcode"]:
    assert needle in body, f"누락: {needle}"
print("OK: outbound_scan.html(신규 모드) 렌더")

import database as db
batch_id = db.create_outbound_batch("테스트거래처_PLAN3", "2026-09-14", "홍길동", "테스트")
db.add_outbound_item(batch_id, "TESTSN_S1", "테스트품", 4)
r2 = client.get(f"/outbound/scan/{batch_id}")
body2 = r2.get_data(as_text=True)
assert "TESTSN_S1" in body2
print("OK: outbound_scan.html(편집 모드) 기존 항목 표시")

conn = db.get_conn(); conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,)); conn.commit(); conn.close()
```

- [ ] **Step 3: 실기기/헤드리스 확인 (권장, CLAUDE.md 11절/17절)**

카메라 UI는 HTTP 200 확인만으로 "됐다"고 하면 안 된다(CLAUDE.md 11절). 로컬 Chrome을 fake camera device로 띄워서 `#qrReaderRegion` 안에 실제 `<video>` 엘리먼트가 생기는지 스크린샷으로 확인할 것(17절의 headless Chrome 진단 방식과 동일, `dangerouslyDisableSandbox`가 필요했던 것도 참고):

```bash
chrome.exe --headless=new --disable-gpu --no-sandbox \
  --use-fake-ui-for-media-stream --use-fake-device-for-media-stream \
  --screenshot=scan_check.png --window-size=500,900 \
  "http://127.0.0.1:5000/outbound/scan/new"
```

(로그인 세션이 필요하므로 Flask test client로 렌더링한 HTML을 저장해서 스크린샷하는 이전 방식과 달리, 이 화면은 실제 카메라 스트림(`getUserMedia`)이 있어야 하므로 정적 HTML 저장으로는 검증이 안 된다 — 반드시 개발 서버를 띄운 채로 실제 URL에 접속해서 캡처할 것.)

- [ ] **Step 4: quality-watcher 검증 요청**

- [ ] **Step 5: Commit** — 메인 세션이 처리

---

## Task 10: `templates/outbound_history.html` — 출고 이력 화면

**Files:** Create `templates/outbound_history.html`

**재사용할 기존 패턴:** `templates/change_points.html`의 검색폼+표 레이아웃. 상세보기는 별도 읽기전용 템플릿을 만들지 않는다 — 설계문서 자체가 "클릭 시 상세(=출고 스캔 화면의 편집모드로 이동)"를 명시했으므로, 각 행은 `outbound_scan_edit`으로 링크한다(YAGNI).

**화면 구성:**
1. 검색 폼(`method="GET"`, `name="q"`, 거래처/담당자 대상 LIKE 검색 — `db.list_outbound_batches`가 이미 처리)
2. 표: 거래처(클릭 시 `outbound_scan_edit`) / 출고일(`| date_korean`) / 담당자 / 항목수(`item_count`) / "📊 엑셀" 링크(`outbound_batch_excel`)

- [ ] **Step 1: 템플릿 작성**

- [ ] **Step 2: 검증 스크립트**

```python
import app as appmodule, database as db
client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

batch_id = db.create_outbound_batch("테스트거래처_PLAN4", "2026-09-14", "홍길동", "테스트")
db.add_outbound_item(batch_id, "TESTSN_HH1", "테스트품", 2)

r = client.get("/outbound/history")
body = r.get_data(as_text=True)
assert r.status_code == 200
assert "테스트거래처_PLAN4" in body
assert f"/outbound/scan/{batch_id}" in body
assert f"/outbound/batch/{batch_id}/excel" in body
print("OK: outbound_history.html 렌더")

r2 = client.get("/outbound/history?q=테스트거래처_PLAN4")
assert "테스트거래처_PLAN4" in r2.get_data(as_text=True)
print("OK: 검색 필터 동작")

conn = db.get_conn(); conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,)); conn.commit(); conn.close()
```

- [ ] **Step 3: quality-watcher 검증 요청**

- [ ] **Step 4: Commit** — 메인 세션이 처리

---

## Task 11: 권한(`outbound`) + 메뉴("출고") 등록

**Files:**
- Modify: `app.py:147-183` (`PERM_GROUPS`)
- Modify: `templates/base.html` (승인 메뉴 블록과 출력 메뉴 블록 사이, 현재 307~309행 부근)

- [ ] **Step 1: `app.py`의 `PERM_GROUPS`에 새 그룹 추가**

`PERM_GROUPS` 리스트에서 `("승인", [...])` 다음, `("출력", [...])` 앞에 삽입:

```python
    ("승인", [
        ("approve",        "승인·반려·특채·불합격 확정"),
        ("approve_revoke", "결정 회수"),
    ]),
    ("출고", [
        ("outbound", "출고(S/N 발급/스캔/이력) 관리"),
    ]),
    ("출력", [
```

(`PERM_LABELS`/`ALL_PERMS`는 `PERM_GROUPS`에서 파생되므로 자동으로 `outbound`를 포함하게 된다 — 별도 수정 불필요.)

- [ ] **Step 2: `templates/base.html`에 메뉴 추가**

`{# ✅ 승인 #}` 블록(현재 293~307행)이 끝난 직후, `{# 🖨️ 출력 #}` 블록(현재 309행) 시작 전에 삽입:

```html
  {# 🚚 출고 #}
  {% if 'outbound' in perms %}
  <div class="nav-dropdown">
    <button type="button" onclick="toggleNav(this)">🚚 출고 ▾</button>
    <div class="menu">
      <a href="{{ url_for('outbound_serial_new') }}">S/N 발급</a>
      <a href="{{ url_for('outbound_scan_new') }}">출고 스캔(새 배치 시작)</a>
      <a href="{{ url_for('outbound_history') }}">출고 이력</a>
    </div>
  </div>
  {% endif %}

```

- [ ] **Step 3: 검증 스크립트**

```python
import app as appmodule, database as db

assert "outbound" in appmodule.ALL_PERMS
assert appmodule.PERM_LABELS["outbound"] == "출고(S/N 발급/스캔/이력) 관리"
print("OK: PERM_GROUPS/ALL_PERMS/PERM_LABELS 반영")

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)
r = client.get("/")
body = r.get_data(as_text=True)
assert "🚚 출고" in body   # admin은 모든 권한 보유 → 메뉴가 보여야 함
print("OK: admin 계정에 출고 메뉴 노출")

# outbound 권한 없는 일반 계정으로도 확인 — database.py의 실제 create_user 시그니처를
# 먼저 확인하고 그에 맞게 호출할 것(아래는 예시).
uid = db.create_user("outbound_test_user", "pw1234", "테스트유저", "")  # 권한 없음
client2 = appmodule.app.test_client()
client2.post("/login", data={"username": "outbound_test_user", "password": "pw1234"}, follow_redirects=True)
r2 = client2.get("/")
assert "🚚 출고" not in r2.get_data(as_text=True)
print("OK: 권한 없는 계정에는 메뉴 안 보임")

r3 = client2.get("/outbound/history")
assert r3.status_code in (302, 200)  # perm_required가 홈으로 리다이렉트
assert "/outbound/history" not in r3.headers.get("Location", "")
print("OK: 권한 없는 계정은 라우트 접근도 차단됨")

# 정리
conn = db.get_conn(); conn.execute("DELETE FROM users WHERE username='outbound_test_user'"); conn.commit(); conn.close()
```

- [ ] **Step 4: Commit** — 메인 세션이 처리

---

## Task 12: README.txt 갱신 + 전체 end-to-end 검증 + quality-watcher

- [ ] **Step 1: README.txt에 비개발자용 안내 추가**

"출고 관리" 섹션을 새로 만들어 다음을 포함시킬 것:
- S/N은 생산 완료 직후 미리 발급해서 QR을 인쇄해 라벨로 붙인다.
- 출고할 때 그 QR을 스캔하면 S/N이 자동 입력된다. 카메라가 안 되면 직접 입력해도 된다.
- "미등록 S/N"이나 "이미 다른 출고에 쓰인 S/N" 경고가 떠도 실수가 아니라면 그냥 진행해도 된다.
- 저장한 뒤에도 항목을 계속 추가하거나 고칠 수 있다.
- 엑셀 양식은 아직 기본 표 형태다(회사 정식 양식 파일을 주면 그걸로 바꿔줄 수 있다).

- [ ] **Step 2: 전체 흐름 수동 검증 (CLAUDE.md 10절/11절)**

1. admin으로 로그인 → 상단 메뉴에 "🚚 출고"가 보이는지 확인
2. "S/N 발급"에서 임의의 S/N(예: `SN-DEMO-0001`) 발급 → QR 이미지가 뜨는지, 같은 S/N을 다시 발급 시도하면 막히는지 확인
3. "출고 스캔(새 배치 시작)" → 거래처/출고일/담당자 입력 → 카메라 권한을 거부해보고 수동 입력창으로 `SN-DEMO-0001`을 입력했을 때 "발급 이력에 없는 S/N" 경고가 뜨지 않는지(방금 발급했으므로) 확인, 아직 발급 안 한 임의의 S/N(예: `SN-UNKNOWN-9999`)을 입력했을 때는 "발급 이력에 없는 S/N" 경고가 뜨는지 확인(그래도 추가는 되는지)
4. 제품명/수량 입력 + 사진 2장(카메라 버튼 하나, 갤러리 버튼 하나로 각각) 첨부 → "리스트에 추가" → 같은 방식으로 항목 하나 더 추가 → "저장"
5. 저장 후 리다이렉트된 편집 화면에서 방금 저장한 항목 2개와 사진들이 보이는지 확인
6. 그중 하나의 제품명을 인라인으로 수정 → 저장되는지 확인
7. 사진 1장을 삭제 → 화면에서 사라지고 실제 파일(`outbound_photos/` 디렉터리)도 지워지는지 확인
8. 이 배치에 새 항목을 하나 더 추가(같은 편집 화면에서) → 기존 항목 안 지워지고 잘 추가되는지 확인
9. "출고 이력"에서 방금 만든 배치가 목록에 나오는지, "📊 엑셀" 눌러서 열어봤을 때 거래처/S/N/제품명/수량/사진매수가 제대로 채워져 있는지 실제로 xlsx를 열어서 확인
10. 방금 발급한 S/N을 **다른** 새 배치에서 또 스캔/입력해봐서 "이미 다른 출고에 쓰인 S/N" 경고가 뜨는지, 그래도 추가가 되는지 확인
11. `outbound` 권한이 없는 일반 계정으로 로그인해서 "출고" 메뉴 자체가 안 보이고, URL로 직접 들어가도 막히는지 확인

- [ ] **Step 3: quality-watcher 최종 검증 요청**

전체 태스크(1~12)에서 수정/신규 생성된 파일 전부(`database.py`, `app.py`, `report_builder.py`, `requirements.txt`, `templates/outbound_serial.html`, `templates/outbound_scan.html`, `templates/outbound_history.html`, `templates/base.html`)를 대상으로 quality-watcher에게 검증을 요청한다. "결함 있음"이면 보고 전에 고치고, "확인 필요"는 사용자에게 그대로 전달한다.

---

## 파일 경로 요약

- `database.py` — 신규 테이블 4개(`finished_goods_serials`/`outbound_batches`/`outbound_items`/`outbound_item_photos`) + S/N·배치·항목·사진 CRUD 함수 12개
- `report_builder.py` — `build_outbound_excel(batch, items)` 신규
- `requirements.txt` — `qrcode` 추가
- `app.py` — `OUTBOUND_PHOTO_DIR` 전역 + 라우트 10개(`outbound_qr_image`, `outbound_serial_new`, `outbound_photo_file`, `outbound_serial_check`, `outbound_scan_new`, `outbound_scan_edit`, `outbound_item_edit`, `outbound_item_delete`, `outbound_photo_delete`, `outbound_history`, `outbound_batch_excel`) + 내부 헬퍼 `_outbound_scan_submit()` + `PERM_GROUPS`에 `"출고"` 그룹 추가
- `templates/outbound_serial.html` — 신규(S/N 발급 화면)
- `templates/outbound_scan.html` — 신규(출고 스캔 화면, 가장 복잡)
- `templates/outbound_history.html` — 신규(출고 이력 화면)
- `templates/base.html` — "🚚 출고" 메뉴 신설
- `README.txt` — 사용자 안내 갱신

**Execution Handoff**: Task 1~12 순서대로 developer 에이전트가 구현. 각 태스크마다 검증 스크립트를 실제로 돌려 통과를 확인한 뒤, Task 8·9·10(신규 템플릿)마다, 그리고 최종적으로 Task 12에서 quality-watcher 검증을 받는다. developer는 git commit을 하지 않는다(메인 세션이 검토 후 커밋).
