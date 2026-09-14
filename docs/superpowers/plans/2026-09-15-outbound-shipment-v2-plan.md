# 출고 관리 확장(모델명 자동분류·차수·QR라벨출력·발급이력관리·출고확인) 구현 계획

> 이 프로젝트에는 자동화 테스트 스위트(pytest 등)가 없고 CLAUDE.md 11절/19절이 명시한 대로 "실제 화면·xlsx를 직접 열어서 확인"하는 수동 검증이 표준이므로, 각 태스크의 "검증" 단계는 **Flask test client + sqlite3 CLI + 실제 iqc.db(개발용 사본)를 이용한 스크립트 검증**으로 대체했다. 코드는 이 문서를 작성한 planner가 아니라 **developer 에이전트가 작성**해야 한다. developer는 git commit을 하지 않는다(메인 세션이 검토 후 커밋).

**Goal:** 이미 운영 배포된 출고(완제품 S/N·QR·사진) 관리 1차 구현(커밋 `a07d7ec`)을 확장한다 — S/N 문자열 패턴 기반 모델명 자동분류 규칙 엔진, S/N 발급 붙여넣기 전환, 차수(=출고 배치) 계획 입력, 계획 대비 스캔 진행상황 대조, 즉시저장 아키텍처로의 스캔 화면 재작성, 종류별 QR 라벨 엑셀 출력, 출고 확인 기록까지 6가지를 추가한다.

**Architecture:** 기존 출고 서브시스템(`finished_goods_serials`/`outbound_batches`/`outbound_items`/`outbound_item_photos`, 권한 `outbound` 하나, IQC와 완전 분리)의 골격은 그대로 두고 그 위에 얹는다. 새 테이블 5개(`outbound_rule_voltage`/`outbound_rule_suffix`/`outbound_rule_pcode`/`outbound_planned_items`/`outbound_qr_exports`)와 `outbound_batches`에 컬럼 3개(`round_no`/`confirmed_by`/`confirmed_at`)를 추가한다. 분류 로직(`database.classify_serial_no()`)은 S/N 발급 미리보기·출고 스캔 자동입력·QR 라벨 카테고리 분류 세 곳이 공유하는 단일 함수로 만든다(CLAUDE.md 8-1절 원칙). QR PNG 생성도 `/outbound/qr` 라우트와 QR 라벨 엑셀 출력이 `report_builder.qr_png_bytes()` 하나를 공유한다. 1차 구현의 "새 항목을 브라우저에 누적했다가 한 번에 제출"하는 `_outbound_scan_submit()`/`items_json`/`DataTransfer` 메커니즘은 **전량 폐기**하고, "리스트에 추가"를 누르면 그 즉시 fetch(FormData)로 서버에 저장하고 응답으로 받은 항목을 DOM에 바로 붙이는 방식(카메라 스트림은 재시작 없이 계속 유지)으로 재작성한다. 배치 생성은 출고 스캔 화면이 아니라 신설되는 "차수 입력" 화면의 책임으로 옮긴다.

**Tech Stack:** Flask, SQLite(`database.py`의 `get_conn()` 패턴), Jinja2, openpyxl(`OneCellAnchor`/`AnchorMarker`/`XDRPositiveSize2D`/`cm_to_EMU` — `report_builder._insert_logo()`·`_build_ncr_photo_sheet()`와 같은 정밀배치 기법 재사용), `qrcode`(1차 구현에서 이미 `requirements.txt`에 추가·배포됨, 신규 의존성 없음), `html5-qrcode`(CDN, 1차 구현에서 이미 로드 중).

**Spec:** `docs/superpowers/specs/2026-09-14-outbound-shipment-design.md`의 `# 2026-09-14 확장` 섹션(사용자 승인 완료된 확정 설계). 그 위 본문은 1차 구현분(참고용).

**1차 구현 계획 문서(참고용, 실제 코드가 이 문서와 100% 일치함을 확인함):** `docs/superpowers/plans/2026-09-14-outbound-shipment-plan.md`

## Global Constraints

```
- 새 테이블/컬럼은 database.py의 init_db() 안에서 "# ---- 신규 삽입 끝 ----" 주석 줄을
  찾아 그 바로 앞에 삽입한다(마이그레이션 파일 분리 없음, 기존 관례). 이 주석은 각 태스크가
  자기 블록을 추가한 뒤에도 그대로 파일 끝에 남아있어야 한다(다음 태스크가 또 그 앞에 삽입).
- outbound_rule_voltage/outbound_rule_suffix/outbound_rule_pcode/outbound_planned_items/
  outbound_qr_exports는 전부 outbound_batches/outbound_items에 FK로 연결하되(배치 삭제
  기능이 없으므로 고아 행 위험 없음), classify_serial_no()가 사용하는 3개 규칙표는 서로
  독립적이라 FK가 없다.
- "이미 다른 배치에 쓰인 S/N", "미등록 S/N", "계획에 없는 항목이 스캔됨" 세 가지는 전부
  어디서도 진행을 막지 않는다 — 항상 경고 배너만 띄우고 계속 진행 허용(설계문서 확정사항,
  세 케이스 원칙 통일 — 세 번째는 이번에 새로 추가되는 것이므로 반드시 앞의 두 케이스와
  같은 취급을 받아야 한다).
- 배치(=차수)는 확정 이후에도 항목 추가·수정·삭제·계획 추가가 항상 가능해야 한다.
  "출고 확인"도 잠금이 아니라 순수 기록용이다(확인 후에도 전부 수정 가능).
- 수량(quantity) 입력은 UI에서 완전히 제거한다. outbound_items.quantity 컬럼 자체는
  하위호환을 위해 남겨두고 새 항목은 항상 NULL로 저장한다.
- 분류 규칙 엔진은 정규식 패턴 구조(CKMR.../CKCB...) 자체를 코드에 고정하고, 사용자가
  화면에서 바꿀 수 있는 건 세 매핑표(전압코드/접미사/P코드특수값)의 코드→라벨 값뿐이다
  (YAGNI — 패턴 구조를 바꿔야 하는 요구가 생기면 그때 다시 설계).
- 배치 생성은 "차수 입력" 화면(outbound_round_new)에서만 한다. 1차 구현의
  outbound_scan_new(POST로 새 배치 생성)는 폐기하고 outbound_round_new로의 리다이렉트
  별칭으로만 남긴다 — 옛 메뉴 링크/북마크가 깨지면 안 되기 때문.
- 엑셀 출력(기본표/QR라벨)은 전부 디스크에 저장하지 않고 BytesIO로 즉시 다운로드 응답한다
  (1차 구현의 build_outbound_excel과 동일 원칙). QR 라벨 "재다운로드"도 저장된 파일을
  다시 주는 게 아니라 그 시점의 계획+규칙으로 매번 새로 생성한다.
- 새 라우트는 전부 기존과 동일하게 @perm_required("outbound") 하나로 게이트한다(새 권한
  신설 없음 — 1차 구현의 "3개 화면 전부 이 권한 하나" 원칙을 확장 화면에도 그대로 적용).
- 코드 수정 완료 후 quality-watcher 검증 필수(CLAUDE.md 11절). 신규 템플릿·JS(특히 카메라
  화면 재작성)는 "실제로 보이는지"를 코드리뷰만으로 완료 선언하지 않는다(CLAUDE.md 11절/17절).
```

---

## 실제 코드 확인 결과 — 1차 구현이 실제로 어떻게 만들어져 있는지

| 확인 항목 | 실제 위치/내용 |
|---|---|
| 신규 테이블 4개 정의 | `database.py:648-693` (`init_db()` 안, `# ---- 여기부터 신규 삽입 ----` ~ `# ---- 신규 삽입 끝 ----` 사이) |
| S/N·배치·항목·사진 CRUD 함수 12개 | `database.py:3440-3631` (`serial_exists`/`create_serial`/`list_serials`/`find_outbound_item_batch`/`create_outbound_batch`/`update_outbound_batch`/`get_outbound_batch`/`list_outbound_batches`/`add_outbound_item`/`get_outbound_item`/`list_outbound_items`/`update_outbound_item`/`delete_outbound_item`/`add_outbound_item_photo`/`get_outbound_item_photo`/`delete_outbound_item_photo`) |
| `get_conn()` | `database.py:57-61` — `sqlite3.Row` factory, `PRAGMA foreign_keys = ON`(자식 행 먼저 지워야 함에 주의) |
| `PERM_GROUPS`의 "출고" 그룹 | `app.py:165-167`, `outbound` 권한 하나 |
| 출고 라우트 10개 + 헬퍼 | `app.py:7512-7757` (`outbound_qr_image`/`outbound_serial_new`/`outbound_photo_file`/`outbound_serial_check`/`_outbound_scan_submit`(헬퍼)/`outbound_scan_new`/`outbound_scan_edit`/`outbound_item_edit`/`outbound_item_delete`/`outbound_photo_delete`/`outbound_history`/`outbound_batch_excel`), `OUTBOUND_PHOTO_DIR = db.DATA_DIR/outbound_photos`(`app.py:7557`) |
| `_save_ncr_photo(file_storage, dest_dir, base_name)` | `app.py:5742` — `dest_dir` 매개변수라 수정 없이 그대로 재사용 가능(NCR 전용 상수 없음) |
| `build_outbound_excel(batch, items)` | `report_builder.py:1386-1444`(파일 맨 끝) |
| `_insert_logo`/이미지 정밀배치 패턴 | `report_builder.py:63-85`(`_insert_logo`, `OneCellAnchor`+`AnchorMarker`+`cm_to_EMU`), `report_builder.py:988-1105`(`_build_ncr_photo_sheet` — 촘촘한 좁은 열/행을 미리 깔아두고 `_resolve()`로 절대좌표→(칸,오프셋) 변환하는 기법, QR 라벨 그리드에 그대로 재사용) |
| `templates/outbound_serial.html` | 단일 S/N 입력 폼 + `issued` 쿼리파라미터로 QR 1장 표시 + `@media print` 패턴(`window.print()`) — 이번에 전면 재작성 대상 |
| `templates/outbound_scan.html` | `items_json`+`pending` 배열+제출 직전 `DataTransfer`로 `photos_N` 인풋 동적 생성하는 방식 — 이번에 전면 재작성(폐기) 대상 |
| `templates/outbound_history.html` | 배치 목록 표, 거래처 클릭→`outbound_scan_edit`, "📊 엑셀" 링크 — 이번엔 컬럼 추가만 |
| `templates/base.html`의 "🚚 출고" 메뉴 | `base.html:309-319`, 3개 링크(S/N 발급/출고 스캔(새 배치 시작)/출고 이력) |
| `templates/intake.html`의 붙여넣기 그리드 패턴 | `rows_json` 히든필드에 `JSON.stringify(rows)`를 제출 직전 채워넣는 방식(`intake.html:48,278-300`), `paste` 이벤트에서 `\t`/`\n` 감지해 여러 셀에 분산 기록(`intake.html:249-276`). 드래그-채우기(`fillBtn`)는 "같은 값을 여러 줄에 반복"용이라 S/N처럼 전부 고유한 값에는 불필요 — 이번 확장에선 이 부분만 빼고 재사용 |
| `templates/change_points.html`의 개별삭제 패턴 | `change_points.html:94-98` — 각 행마다 `<form method="POST" ... onsubmit="return confirm(...)">`+`✕` 버튼, 목록 위쪽엔 등록 폼 — S/N 발급이력 삭제·분류규칙 삭제·계획항목 삭제 전부 이 패턴 재사용 |
| `templates/supplier_detail.html`류 CRUD 라우트 패턴 | `app.py:5669-5731`(`supplier_detail`/`supplier_contact_add`/`update`/`delete`) — 일반 form POST+redirect, AJAX는 `X-Requested-With` 헤더로 선택적 지원. 분류 규칙 CRUD에 그대로 재사용(간단한 값만 다루므로 AJAX 없이 plain POST로 충분, YAGNI) |
| `ncr_form.html`의 사진 첨부 UI | `ncr_form.html:109-162` — `name="photos"` 파일 인풋 2개(카메라/갤러리) + 미리보기 그리드. 1차 구현이 이미 이 패턴을 `outbound_scan.html`에 옮겨놨음(단, `name` 없이 JS로 버퍼링하는 변형) — 이번 재작성에서는 **`name="photos"`를 그대로 살려서** 각 신규항목 전송을 위한 `FormData(form)`이 자동으로 파일을 담게 한다(1차 구현의 DataTransfer 수작업이 필요 없어지는 이유) |
| README.txt 기존 "출고 관리" 섹션 | `README.txt:433-451` — 이번에 전면 교체 대상 |

---

## 구현 단계에서 확정한 것들

| 항목 | 결정 및 근거 |
|---|---|
| S/N 발급·차수계획의 "붙여넣기" UI 형태 | `intake.html`과 완전히 같은 다중 컬럼 드래그-채우기 그리드가 아니라, **단일 컬럼(S/N만) 입력 그리드 + 붙여넣기 파싱**으로 간소화한다. 드래그-채우기는 "같은 값을 여러 줄에 반복"할 때 쓰는데 S/N은 전부 고유값이라 이 기능이 무의미하다(YAGNI). 두 화면(S/N 발급/차수 입력)이 완전히 같은 그리드를 쓰므로 `templates/_serial_paste_grid.html` 파셜 하나로 통합한다(8-1절 원칙). |
| 분류 미리보기 계산 위치 | 클라이언트 JS에 파싱 로직을 복제하지 않고, `POST /outbound/classify-bulk`(신규)로 서버의 `db.classify_serial_no()`를 그대로 호출한다 — 규칙이 나중에 바뀌어도 화면 두 곳(JS/서버)이 따로 놀 위험이 없다. |
| "리스트에 추가" 즉시저장을 reload로 할지 AJAX로 할지 | **AJAX(fetch+FormData)로 확정.** 스캔 화면은 연속으로 여러 개를 스캔하는 워크플로우라 매번 페이지를 새로고침하면 카메라 스트림(`Html5Qrcode`)이 매번 재시작돼 체감 속도가 크게 나빠진다. `outbound_item_add`(신규)가 `outbound_item_edit`/`outbound_item_delete`와 동일하게 `X-Requested-With` 헤더 유무로 JSON/redirect 두 가지를 다 지원하도록 만들어서, JS가 없거나 실패해도 plain POST로 안전하게 동작한다. |
| 1차 구현 `outbound_scan_new`(POST로 새 배치 즉시 생성) 처리 | **완전 삭제하지 않고 `outbound_round_new`로의 리다이렉트 별칭으로만 남긴다**(GET/POST 무관하게 리다이렉트). 배치 생성 책임은 전부 신설되는 "차수 입력" 화면으로 옮긴다. `_outbound_scan_submit()` 헬퍼 함수는 완전히 삭제한다(호출부가 없어짐). |
| QR 라벨 엑셀 "재다운로드" 시 원본 그대로 재현할지 | 원본 바이트를 저장하지 않고 **그 시점의 계획 S/N + 그 시점의 분류 규칙으로 매번 새로 생성**한다(디스크 미저장 원칙 유지, `outbound_qr_exports`는 메타데이터만 기록). |
| 엑셀 시트명 규칙 위반 문자 제거 범위 | `\ / ? * [ ] :` 7종 제거(엑셀 표준 금지문자) + 31자 컷 + 잘린 이름 충돌 시 `(2)`,`(3)`... 부여. `report_builder._sanitize_outbound_sheet_name()`을 새로 만든다. |
| 미분류(정규식 매칭 실패, 또는 매칭됐지만 규칙표에 없는 코드) S/N의 QR 라벨 시트 소속 | "미분류" 시트 하나로 몰아서 누락 없이 전부 출력한다(라벨 없이 통째로 빠뜨리면 실제 출고 물량 누락으로 이어질 수 있어 더 위험). |
| 출고 스캔 화면에서 배치 헤더(거래처 등) 수정 | 항목 추가와 별개의 작은 폼으로 분리(`outbound_batch_update`, 신규). |

**사용자에게 물어봐야 할 것: 없음.**

---

## Task A: `database.py` — 모델명 자동분류 규칙 엔진(3개 매핑표 + `classify_serial_no()`)

**Files:**
- Modify: `database.py` (`init_db()` 안 `# ---- 신규 삽입 끝 ----` 주석 직전에 테이블 3개 추가)
- Modify: `database.py` (`delete_outbound_item_photo()` 함수 바로 다음에 신규 함수 추가)

**Interfaces (Produces):**
- `db.list_classify_rules(kind: str) -> list[sqlite3.Row]` (`kind` ∈ `"voltage"`/`"suffix"`/`"pcode"`)
- `db.upsert_classify_rule(kind: str, code: str, label: str) -> None`
- `db.delete_classify_rule(kind: str, code: str) -> None`
- `db.get_classify_rules() -> dict` (`{"voltage": {code: label}, "suffix": [(code, label), ...] (길이 내림차순 정렬됨), "pcode": {code: label}}`)
- `db.classify_serial_no(serial_no: str, rules: dict|None = None) -> str|None`

- [ ] **Step 1: 신규 테이블 3개 추가**

`database.py`의 `init_db()` 안에서 `# ---- 신규 삽입 끝 ----`를 찾아 그 줄 **바로 앞**에 삽입:

```python
    # ---- 2026-09-15 확장: 모델명 자동분류 규칙 3개 매핑표 ----
    # 정규식 패턴 구조(CKMR.../CKCB...)는 코드에 고정하고, 여기 값(코드->라벨)만
    # 화면에서 CRUD 가능하게 한다(YAGNI — 패턴 구조 자체를 바꿀 요구가 생기면 재설계).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_rule_voltage (
            code  TEXT PRIMARY KEY,
            label TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_rule_suffix (
            code  TEXT PRIMARY KEY,
            label TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_rule_pcode (
            code  TEXT PRIMARY KEY,
            label TEXT NOT NULL
        )
    """)
    # ---- 2026-09-15 확장(모델명 분류 규칙) 끝 ----

    # ---- 신규 삽입 끝 ----
```

(마지막 줄 `# ---- 신규 삽입 끝 ----`은 원래 있던 주석을 그대로 남긴 것 — 다음 태스크(C, F)가 또 그 앞에 자기 블록을 추가한다.)

- [ ] **Step 2: `classify_serial_no()` + CRUD 함수 추가**

`database.py`의 `delete_outbound_item_photo()` 함수 바로 다음에 추가:

```python
# ---------- 2026-09-15 확장: 모델명 자동분류 규칙 ----------

_OUTBOUND_RULE_TABLES = {
    "voltage": "outbound_rule_voltage",
    "suffix":  "outbound_rule_suffix",
    "pcode":   "outbound_rule_pcode",
}

# 리클로저 본체: CKMR{전압코드}K####USA####{접미사}(({P}P))?
_CKMR_RE = re.compile(r"^CKMR(\d)K\d{4}USA\d{4}([A-Z0-9]*)(?:\((\d+)P\))?$")
# 제어함: CKCB####-{꼬리표}
_CKCB_RE = re.compile(r"^CKCB\d+-(.+)$")


def list_classify_rules(kind):
    """kind: 'voltage'/'suffix'/'pcode'. code 오름차순으로 반환."""
    table = _OUTBOUND_RULE_TABLES[kind]
    conn = get_conn()
    rows = conn.execute(f"SELECT code, label FROM {table} ORDER BY code").fetchall()
    conn.close()
    return rows


def upsert_classify_rule(kind, code, label):
    """code가 이미 있으면 라벨만 갱신, 없으면 새로 추가 — 화면의 추가/수정 폼 하나로 겸용."""
    table = _OUTBOUND_RULE_TABLES[kind]
    conn = get_conn()
    conn.execute(
        f"INSERT INTO {table} (code, label) VALUES (?, ?) "
        f"ON CONFLICT(code) DO UPDATE SET label=excluded.label",
        (code, label))
    conn.commit()
    conn.close()


def delete_classify_rule(kind, code):
    table = _OUTBOUND_RULE_TABLES[kind]
    conn = get_conn()
    conn.execute(f"DELETE FROM {table} WHERE code=?", (code,))
    conn.commit()
    conn.close()


def get_classify_rules():
    """세 매핑표를 한 번에 읽어 classify_serial_no()에 넘길 dict로 반환한다.
    여러 S/N을 한 번에 분류할 때(QR 라벨 엑셀 출력 등) 매번 DB를 다시 열지 않도록
    호출부가 이 함수를 한 번만 불러서 재사용할 수 있게 만든 캐시 겸용 함수다."""
    conn = get_conn()
    voltage = {r["code"]: r["label"] for r in
               conn.execute("SELECT code, label FROM outbound_rule_voltage").fetchall()}
    suffix_rows = conn.execute("SELECT code, label FROM outbound_rule_suffix").fetchall()
    pcode = {r["code"]: r["label"] for r in
             conn.execute("SELECT code, label FROM outbound_rule_pcode").fetchall()}
    conn.close()
    # 접미사는 긴 코드부터 검사해야 한다 — "HAT3"가 "HA"/"H"보다 먼저 매칭돼야
    # "CKMR8K0803USA6622HAT3"의 접미사가 "HA"로 잘못 분류되는 걸 막을 수 있다.
    suffix = sorted(((r["code"], r["label"]) for r in suffix_rows), key=lambda kv: -len(kv[0]))
    return {"voltage": voltage, "suffix": suffix, "pcode": pcode}


def classify_serial_no(serial_no, rules=None):
    """S/N 문자열로 제품 분류 라벨을 계산한다.

    - 리클로저 본체: CKMR{전압코드}K####USA####{접미사}(({P}P))?
      예: CKMR7K0902USA5771H(42P) -> "15kV 일반 수평 (42P)"
          CKMR8K0803USA6622HAT3(32P) -> "27kV 트리플 3핸들 앵글 (32P) 155V"
          (P코드가 특수값표에 있으면 "(NNP)" 뒤에 그 특수값도 덧붙인다)
          CKMR9K0912USA8775H -> "38kV 일반 수평"
    - 제어함: CKCB####-{꼬리표}  예: CKCB2758-RA -> "제어함 - RA"
    - 둘 다 매칭 안 되거나, 매칭은 됐는데 전압코드/접미사가 매핑표에 없으면 None(미분류).

    rules를 안 넘기면 이 함수가 직접 get_classify_rules()로 조회한다(단건 호출용).
    여러 건을 한 번에 분류할 때는 호출부가 get_classify_rules()를 한 번만 불러 넘길 것
    (매번 DB를 다시 여는 낭비를 막기 위함)."""
    serial_no = (serial_no or "").strip()
    if not serial_no:
        return None
    if rules is None:
        rules = get_classify_rules()

    m = _CKMR_RE.match(serial_no)
    if m:
        volt_code, suffix_raw, pcode = m.group(1), m.group(2), m.group(3)
        volt_label = rules["voltage"].get(volt_code)
        if volt_label is None:
            return None
        suffix_label = None
        for code, label in rules["suffix"]:
            if suffix_raw.startswith(code):
                suffix_label = label
                break
        if suffix_raw and suffix_label is None:
            return None  # 접미사가 있는데 매핑표에 없으면 분류 불가(미확정 접미사)
        parts = [volt_label]
        if suffix_label:
            parts.append(suffix_label)
        label = " ".join(parts)
        if pcode:
            label += f" ({pcode}P)"
            special = rules["pcode"].get(pcode)
            if special:
                label += f" {special}"
        return label

    m2 = _CKCB_RE.match(serial_no)
    if m2:
        return f"제어함 - {m2.group(1)}"

    return None
```

- [ ] **Step 3: `app.py`에 초기값 1회 시딩 함수 추가**

`app.py`에서 `ensure_supplier_contacts_migration_20260907()` 함수 정의 다음에 추가:

```python
def ensure_outbound_rules_seed_20260915():
    """모델명 자동분류 규칙 초기값을 1회만 심는다(설계문서 확정값). settings 플래그로
    멱등 처리 — 사용자가 화면에서 규칙을 지운 뒤 서버가 재시작돼도 되살아나면 안 되므로,
    "값이 없으면 INSERT OR IGNORE" 방식이 아니라 "이미 심었는지" 플래그로 딱 한 번만 심는다."""
    if db.get_setting("outbound_rules_seeded_20260915", "0") == "1":
        return
    for code, label in [("7", "15kV"), ("8", "27kV"), ("9", "38kV")]:
        db.upsert_classify_rule("voltage", code, label)
    for code, label in [
        ("HAT1", "트리플 1핸들 앵글"), ("HAT3", "트리플 3핸들 앵글"),
        ("HT1", "트리플 1핸들"), ("HT3", "트리플 3핸들"),
        ("HA", "일반 앵글"), ("H", "일반 수평"), ("S", "단상"),
    ]:
        db.upsert_classify_rule("suffix", code, label)
    db.upsert_classify_rule("pcode", "32", "155V")
    db.set_setting("outbound_rules_seeded_20260915", "1")
```

그리고 파일 맨 끝의 `db.init_db()` 이후 다른 `ensure_*()` 호출들이 나열된 블록에 한 줄 추가:

```python
db.init_db()
ensure_default_admin()
ensure_perm_migration()
ensure_inspect_method_fill_20260825()
ensure_supplier_contacts_migration_20260907()
ensure_outbound_rules_seed_20260915()   # ← 추가
```

- [ ] **Step 4: 검증 스크립트**

```python
import database as db

db.init_db()

db.upsert_classify_rule("voltage", "7", "15kV")
db.upsert_classify_rule("voltage", "8", "27kV")
db.upsert_classify_rule("voltage", "9", "38kV")
db.upsert_classify_rule("suffix", "HAT1", "트리플 1핸들 앵글")
db.upsert_classify_rule("suffix", "HAT3", "트리플 3핸들 앵글")
db.upsert_classify_rule("suffix", "HT1", "트리플 1핸들")
db.upsert_classify_rule("suffix", "HT3", "트리플 3핸들")
db.upsert_classify_rule("suffix", "HA", "일반 앵글")
db.upsert_classify_rule("suffix", "H", "일반 수평")
db.upsert_classify_rule("suffix", "S", "단상")
db.upsert_classify_rule("pcode", "32", "155V")

rules = db.get_classify_rules()
assert db.classify_serial_no("CKMR7K0902USA5771H(42P)", rules=rules) == "15kV 일반 수평 (42P)"
assert db.classify_serial_no("CKMR8K0803USA6622HAT3(32P)", rules=rules) == "27kV 트리플 3핸들 앵글 (32P) 155V"
assert db.classify_serial_no("CKMR9K0912USA8775H", rules=rules) == "38kV 일반 수평"
assert db.classify_serial_no("CKCB2758-RA", rules=rules) == "제어함 - RA"
assert db.classify_serial_no("CKCB8655-R2", rules=rules) == "제어함 - R2"
assert db.classify_serial_no("UNKNOWN-1234", rules=rules) is None
assert db.classify_serial_no("", rules=rules) is None
print("OK: classify_serial_no 정상 동작(6개 케이스)")

db.delete_classify_rule("pcode", "32")
rules2 = db.get_classify_rules()
assert "32" not in rules2["pcode"]
assert db.classify_serial_no("CKMR8K0803USA6622HAT3(32P)", rules=rules2) == "27kV 트리플 3핸들 앵글 (32P)"
print("OK: delete_classify_rule 반영됨(특수값 삭제해도 (32P) 표기는 유지)")
db.upsert_classify_rule("pcode", "32", "155V")  # 원복

import app as appmodule
appmodule.ensure_outbound_rules_seed_20260915()
appmodule.ensure_outbound_rules_seed_20260915()  # 두 번 호출해도 중복 삽입 안 되는지(멱등성)
rules3 = db.get_classify_rules()
assert len(rules3["voltage"]) == 3
print("OK: 시딩 멱등성 확인")
print("정리 필요 없음(seed 데이터는 실제로도 남아있어야 하는 기본값)")
```

- [ ] **Step 5: quality-watcher 검증 요청** (정규식 파싱 로직 신규 — CLAUDE.md 4-2절 "파서 함정" 사례처럼 실측 검증 필요)

- [ ] **Step 6: Commit** — 메인 세션이 처리

---

## Task B: 분류 규칙 관리 화면 (신규 라우트 + 템플릿)

**Files:**
- Modify: `app.py` (Task A의 `ensure_outbound_rules_seed_20260915()` 다음, 또는 기존 출고 라우트 섹션 끝 — `outbound_batch_excel` 함수 다음)
- Create: `templates/outbound_rules.html`

**Interfaces (Consumes):** `db.list_classify_rules`/`upsert_classify_rule`/`delete_classify_rule`/`get_classify_rules`/`classify_serial_no` (Task A)
**Interfaces (Produces):** 라우트 `outbound_rules`, `outbound_rule_add`, `outbound_rule_update`, `outbound_rule_delete`, `outbound_classify_bulk`

- [ ] **Step 1: 라우트 작성**

`app.py`의 `outbound_batch_excel()` 함수 바로 다음에 추가:

```python
_OUTBOUND_RULE_KIND_LABELS = {"voltage": "전압코드", "suffix": "접미사", "pcode": "P코드 특수값"}


@app.route("/outbound/rules")
@perm_required("outbound")
def outbound_rules():
    rules = {kind: db.list_classify_rules(kind) for kind in _OUTBOUND_RULE_KIND_LABELS}
    return render_template("outbound_rules.html", rules=rules,
                           kind_labels=_OUTBOUND_RULE_KIND_LABELS)


@app.route("/outbound/rules/<kind>/add", methods=["POST"])
@perm_required("outbound")
def outbound_rule_add(kind):
    if kind not in _OUTBOUND_RULE_KIND_LABELS:
        flash("존재하지 않는 규칙 종류야.")
        return redirect(url_for("outbound_rules"))
    code = request.form.get("code", "").strip()
    label = request.form.get("label", "").strip()
    if not code or not label:
        flash("코드와 라벨을 모두 입력해줘.")
        return redirect(url_for("outbound_rules"))
    db.upsert_classify_rule(kind, code, label)
    record_change("모델명 분류 규칙 등록/수정", "outbound_rule", None, f"{kind} / {code} -> {label}")
    flash("규칙이 저장됐어.")
    return redirect(url_for("outbound_rules"))


@app.route("/outbound/rules/<kind>/<code>/update", methods=["POST"])
@perm_required("outbound")
def outbound_rule_update(kind, code):
    if kind not in _OUTBOUND_RULE_KIND_LABELS:
        flash("존재하지 않는 규칙 종류야.")
        return redirect(url_for("outbound_rules"))
    label = request.form.get("label", "").strip()
    if not label:
        flash("라벨을 입력해줘.")
        return redirect(url_for("outbound_rules"))
    db.upsert_classify_rule(kind, code, label)
    record_change("모델명 분류 규칙 수정", "outbound_rule", None, f"{kind} / {code} -> {label}")
    flash("규칙이 수정됐어.")
    return redirect(url_for("outbound_rules"))


@app.route("/outbound/rules/<kind>/<code>/delete", methods=["POST"])
@perm_required("outbound")
def outbound_rule_delete(kind, code):
    if kind not in _OUTBOUND_RULE_KIND_LABELS:
        flash("존재하지 않는 규칙 종류야.")
        return redirect(url_for("outbound_rules"))
    db.delete_classify_rule(kind, code)
    record_change("모델명 분류 규칙 삭제", "outbound_rule", None, f"{kind} / {code}")
    flash("규칙이 삭제됐어.")
    return redirect(url_for("outbound_rules"))


@app.route("/outbound/classify-bulk", methods=["POST"])
@perm_required("outbound")
def outbound_classify_bulk():
    """S/N 목록을 한 번에 분류해서 라벨 배열로 돌려준다 — S/N 발급·차수 입력 그리드의
    실시간 미리보기용. 파싱 로직을 클라이언트 JS로 복제하지 않기 위한 단일 창구
    (8-1절 원칙 — 규칙이 바뀌어도 화면 두 곳이 따로 놀 일이 없다)."""
    import json as _json
    try:
        payload = _json.loads(request.get_data(as_text=True) or "{}")
    except (ValueError, TypeError):
        payload = {}
    serials = payload.get("serials") or []
    rules = db.get_classify_rules()
    labels = [db.classify_serial_no(s, rules=rules) for s in serials]
    return jsonify({"labels": labels})
```

- [ ] **Step 2: 템플릿 작성** — `templates/outbound_rules.html`

```html
{% extends "base.html" %}
{% block title %}모델명 분류 규칙 — Chardon QMS{% endblock %}
{% block content %}
<h2>🚚 모델명 분류 규칙 관리</h2>

<div class="card">
  <p class="muted" style="margin:0; font-size:13.5px;">
    S/N 문자열 패턴(예: CKMR7K0902USA5771H(42P))으로 제품명을 자동 판별할 때 쓰는 매핑표야.
    여기서 값을 추가·수정·삭제하면 S/N 발급·차수 입력·출고 스캔·QR 라벨 출력에 즉시 반영돼.
    패턴 구조 자체(CKMR.../CKCB...)는 바꿀 수 없고, 코드→라벨 값만 바꿀 수 있어.
  </p>
</div>

{% for kind, label in kind_labels.items() %}
<div class="card">
  <h3 style="margin-top:0; font-size:15px;">{{ label }}</h3>
  <table>
    <thead><tr><th style="width:120px;">코드</th><th>라벨</th><th style="width:160px;"></th></tr></thead>
    <tbody>
      {% for r in rules[kind] %}
      <tr>
        <td>{{ r['code'] }}</td>
        <td>
          <form method="POST" action="{{ url_for('outbound_rule_update', kind=kind, code=r['code']) }}"
                style="display:flex; gap:6px;">
            <input type="text" name="label" value="{{ r['label'] }}" style="flex:1;">
            <button type="submit" class="btn secondary" style="margin-top:0;">저장</button>
          </form>
        </td>
        <td>
          <form method="POST" action="{{ url_for('outbound_rule_delete', kind=kind, code=r['code']) }}"
                onsubmit="return confirm('이 규칙을 지울까?');">
            <button type="submit" class="btn secondary" style="margin-top:0; color:var(--fail);">삭제</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="3" style="text-align:center; color:var(--muted);">등록된 규칙이 없어.</td></tr>
      {% endfor %}
    </tbody>
  </table>
  <form method="POST" action="{{ url_for('outbound_rule_add', kind=kind) }}"
        style="display:flex; gap:8px; margin-top:10px; flex-wrap:wrap;">
    <input type="text" name="code" placeholder="코드 (예: 7, HAT1, 32)" required style="width:160px;">
    <input type="text" name="label" placeholder="라벨 (예: 15kV)" required style="flex:1; min-width:160px;">
    <button type="submit" style="margin-top:0;">추가</button>
  </form>
</div>
{% endfor %}
{% endblock %}
```

- [ ] **Step 3: 검증 스크립트**

```python
import app as appmodule, database as db

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

r = client.get("/outbound/rules")
assert r.status_code == 200
body = r.get_data(as_text=True)
assert "전압코드" in body and "접미사" in body and "P코드 특수값" in body
print("OK: outbound_rules.html 렌더")

r2 = client.post("/outbound/rules/voltage/add", data={"code": "TESTV", "label": "테스트전압"}, follow_redirects=True)
assert "TESTV" in r2.get_data(as_text=True)
print("OK: 규칙 추가")

r3 = client.post("/outbound/rules/voltage/TESTV/update", data={"label": "수정된전압"}, follow_redirects=True)
assert "수정된전압" in r3.get_data(as_text=True)
print("OK: 규칙 수정")

r4 = client.post("/outbound/classify-bulk", json={"serials": ["CKMR7K0902USA5771H(42P)", "GARBAGE"]})
data = r4.get_json()
assert data["labels"][0] == "15kV 일반 수평 (42P)"
assert data["labels"][1] is None
print("OK: classify-bulk 엔드포인트")

r5 = client.post("/outbound/rules/voltage/TESTV/delete", follow_redirects=True)
assert "TESTV" not in r5.get_data(as_text=True)
print("OK: 규칙 삭제")

r6 = client.post("/outbound/rules/badkind/add", data={"code": "x", "label": "y"}, follow_redirects=True)
assert r6.status_code == 200  # flash 메시지로 처리, 500 아님
print("OK: 잘못된 kind 안전 처리")
```

- [ ] **Step 4: quality-watcher 검증 요청**

- [ ] **Step 5: Commit** — 메인 세션이 처리

---

## Task C: `outbound_planned_items` 테이블 + 차수(=배치) 컬럼 확장 + 차수 입력 페이지 + 공용 붙여넣기 그리드 파셜

**Files:**
- Modify: `database.py` (`init_db()`에 테이블/컬럼 추가, `create_outbound_batch`/`update_outbound_batch` 시그니처 확장, 신규 CRUD 함수)
- Modify: `app.py` (Task B 라우트 다음에 신규 라우트 추가)
- Create: `templates/_serial_paste_grid.html` (S/N 발급·차수 입력이 공유하는 붙여넣기 그리드 파셜)
- Create: `templates/outbound_round.html`

**Interfaces (Consumes):** `db.classify_serial_no`/`get_classify_rules` (Task A), `POST /outbound/classify-bulk` (Task B)
**Interfaces (Produces):**
- `db.add_planned_items_bulk(batch_id, serial_nos: list[str]) -> int`(추가된 건수)
- `db.list_planned_items(batch_id) -> list[sqlite3.Row]`
- `db.planned_item_exists(batch_id, serial_no) -> bool`
- `db.delete_planned_item(planned_id) -> None`
- `db.create_outbound_batch(customer, ship_date, handler, created_by, round_no=None) -> int` (시그니처 확장)
- `db.update_outbound_batch(batch_id, customer, ship_date, handler, round_no=None) -> None` (시그니처 확장)
- 라우트 `outbound_round_new`, `outbound_round_edit`, `outbound_planned_item_delete`
- JS 전역 함수 `window.collectSerialGridValues()` (파셜이 제공, 부모 폼이 제출 직전 호출)

- [ ] **Step 1: 테이블/컬럼 추가**

`database.py`의 `init_db()`에서, Task A가 추가한 `# ---- 2026-09-15 확장(모델명 분류 규칙) 끝 ----` 바로 다음(`# ---- 신규 삽입 끝 ----` 앞)에 삽입:

```python
    # ---- 2026-09-15 확장: 차수(=배치) 계획 ----
    existing_ob_cols = [row[1] for row in cur.execute("PRAGMA table_info(outbound_batches)").fetchall()]
    for col in ("round_no", "confirmed_by", "confirmed_at"):
        if col not in existing_ob_cols:
            cur.execute(f"ALTER TABLE outbound_batches ADD COLUMN {col} TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_planned_items (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id   INTEGER NOT NULL REFERENCES outbound_batches(id),
            serial_no  TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    # ---- 2026-09-15 확장(차수 계획) 끝 ----
```

- [ ] **Step 2: `create_outbound_batch`/`update_outbound_batch` 시그니처 확장 + 신규 CRUD**

`database.py`에서 기존 `create_outbound_batch()`/`update_outbound_batch()` 함수를 아래로 **교체**:

```python
def create_outbound_batch(customer, ship_date, handler, created_by, round_no=None):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO outbound_batches (customer, ship_date, handler, created_by, round_no)
        VALUES (?, ?, ?, ?, ?)
    """, (customer, ship_date, handler, created_by, round_no))
    conn.commit()
    batch_id = cur.lastrowid
    conn.close()
    return batch_id


def update_outbound_batch(batch_id, customer, ship_date, handler, round_no=None):
    conn = get_conn()
    conn.execute("""
        UPDATE outbound_batches SET customer=?, ship_date=?, handler=?, round_no=?,
               updated_at=datetime('now','localtime')
        WHERE id=?
    """, (customer, ship_date, handler, round_no, batch_id))
    conn.commit()
    conn.close()
```

그리고 `database.py`의 Task A 함수들(`classify_serial_no()`) 바로 다음에 추가:

```python
# ---------- 2026-09-15 확장: 차수(=배치) 계획 항목 ----------

def add_planned_items_bulk(batch_id, serial_nos):
    """차수 계획 S/N을 한 번에 등록. 빈 값과, 이미 이 배치에 등록된 S/N은 건너뛴다.
    반환: 실제로 새로 추가된 건수."""
    conn = get_conn()
    seen = {r["serial_no"] for r in conn.execute(
        "SELECT serial_no FROM outbound_planned_items WHERE batch_id=?", (batch_id,)).fetchall()}
    added = 0
    for sn in serial_nos:
        sn = (sn or "").strip()
        if not sn or sn in seen:
            continue
        seen.add(sn)
        conn.execute(
            "INSERT INTO outbound_planned_items (batch_id, serial_no) VALUES (?, ?)",
            (batch_id, sn))
        added += 1
    conn.commit()
    conn.close()
    return added


def list_planned_items(batch_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM outbound_planned_items WHERE batch_id=? ORDER BY id", (batch_id,)).fetchall()
    conn.close()
    return rows


def planned_item_exists(batch_id, serial_no):
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM outbound_planned_items WHERE batch_id=? AND serial_no=?",
        (batch_id, serial_no)).fetchone()
    conn.close()
    return row is not None


def delete_planned_item(planned_id):
    conn = get_conn()
    conn.execute("DELETE FROM outbound_planned_items WHERE id=?", (planned_id,))
    conn.commit()
    conn.close()
```

- [ ] **Step 3: 공용 붙여넣기 그리드 파셜** — `templates/_serial_paste_grid.html`

```html
<div class="fill-toolbar" style="margin-bottom:8px; display:flex; align-items:center; gap:10px; flex-wrap:wrap;">
  <button type="button" id="snAddRowBtn" class="btn secondary" style="margin-top:0;">+ 행 추가</button>
  <span class="muted" style="font-size:12px;">
    S/N 칸에 엑셀에서 복사한 여러 줄을 붙여넣으면(Ctrl+V) 한 번에 채워지고, 오른쪽에 자동분류 미리보기가 떠.
  </span>
</div>
<div style="overflow-x:auto;">
  <table class="sn-grid" id="snGrid">
    <tr><th></th><th style="min-width:220px;">S/N</th><th style="min-width:220px;">분류 미리보기 (자동)</th><th></th></tr>
  </table>
</div>
<input type="hidden" name="serials_json" id="serialsJsonField">

<style>
  .sn-grid { width:100%; border-collapse:collapse; background:#fff; border-radius:10px;
             overflow:hidden; box-shadow:0 1px 2px rgba(0,0,0,.05); font-size:12.5px; }
  .sn-grid th { background:#ECEEF2; color:#4b5563; padding:8px 6px; font-size:11.5px; }
  .sn-grid td { border:1px solid #DDE0E6; padding:0; }
  .sn-grid input { width:100%; box-sizing:border-box; padding:8px 7px; font-size:13px;
                   border:none; margin:0; background:transparent; }
  .sn-grid td.rownum { background:#F9F9F8; text-align:center; color:#9ca3af; font-size:11px; width:26px; }
  .sn-grid td.del-cell { width:30px; text-align:center; }
  .sn-grid .label-cell { padding:8px 8px; color:#24344F; }
  .sn-grid .label-cell.unclassified { color:#9ca3af; }
  .sn-del-row-btn { background:none; border:none; color:#dc2626; cursor:pointer; font-size:14px; padding:4px; }
</style>

<script>
(function () {
  const grid = document.getElementById('snGrid');
  let rowCount = 0;
  let debounceTimer = null;

  function addRow() {
    rowCount++;
    const tr = document.createElement('tr');
    tr.innerHTML =
      `<td class="rownum">${rowCount}</td>` +
      `<td><input type="text" data-col="serial_no"></td>` +
      `<td class="label-cell unclassified">-</td>` +
      `<td class="del-cell"><button type="button" class="sn-del-row-btn" onclick="this.closest('tr').remove()">✕</button></td>`;
    grid.appendChild(tr);
    tr.querySelector('input').addEventListener('input', scheduleClassify);
    return tr;
  }
  for (let i = 0; i < 10; i++) addRow();
  document.getElementById('snAddRowBtn').addEventListener('click', () => addRow());

  function rows() {
    return Array.from(grid.querySelectorAll('tr')).filter(tr => tr.querySelector('input[data-col="serial_no"]'));
  }

  grid.addEventListener('paste', function (e) {
    const target = e.target;
    if (!target.matches('input[data-col="serial_no"]')) return;
    const text = (e.clipboardData || window.clipboardData).getData('text');
    if (!text.includes('\n')) return;  // 한 줄만 붙여넣으면 기본 동작 유지
    e.preventDefault();
    const lines = text.replace(/\r/g, '').split('\n').filter((l, i, arr) => !(i === arr.length - 1 && l === ''));
    let currentRow = target.closest('tr');
    lines.forEach((line, idx) => {
      if (idx > 0) {
        currentRow = currentRow.nextElementSibling;
        if (!currentRow) currentRow = addRow();
      }
      currentRow.querySelector('input[data-col="serial_no"]').value = line.trim();
    });
    scheduleClassify();
  });

  function scheduleClassify() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runClassify, 250);
  }

  function runClassify() {
    const rs = rows();
    const serials = rs.map(tr => tr.querySelector('input[data-col="serial_no"]').value.trim());
    const nonEmptyIdx = [];
    const nonEmptySerials = [];
    serials.forEach((s, i) => { if (s) { nonEmptyIdx.push(i); nonEmptySerials.push(s); } });
    if (!nonEmptySerials.length) return;
    fetch('/outbound/classify-bulk', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ serials: nonEmptySerials })
    }).then(r => r.json()).then(data => {
      nonEmptyIdx.forEach((rowIdx, i) => {
        const cell = rs[rowIdx].querySelector('.label-cell');
        const label = data.labels[i];
        cell.textContent = label || '미분류';
        cell.className = 'label-cell' + (label ? '' : ' unclassified');
      });
    }).catch(() => {});
  }

  // 부모 폼이 제출 직전 이 함수를 호출해서 hidden 필드를 채운다.
  window.collectSerialGridValues = function () {
    const values = rows().map(tr => tr.querySelector('input[data-col="serial_no"]').value.trim()).filter(Boolean);
    document.getElementById('serialsJsonField').value = JSON.stringify(values);
    return values;
  };
})();
</script>
```

- [ ] **Step 4: 라우트 작성** — `app.py`, Task B 라우트 다음에 추가

```python
@app.route("/outbound/round/new", methods=["GET", "POST"])
@perm_required("outbound")
def outbound_round_new():
    if request.method == "POST":
        customer = request.form.get("customer", "").strip()
        ship_date = request.form.get("ship_date", "").strip()
        handler = request.form.get("handler", "").strip()
        round_no = request.form.get("round_no", "").strip()
        if not customer:
            flash("거래처를 입력해줘.")
            return redirect(url_for("outbound_round_new"))
        import json as _json
        try:
            serials = _json.loads(request.form.get("serials_json", "") or "[]")
        except (ValueError, TypeError):
            serials = []
        batch_id = db.create_outbound_batch(customer, ship_date, handler,
                                             g.user["display_name"] or g.user["username"],
                                             round_no=round_no)
        added = db.add_planned_items_bulk(batch_id, serials)
        record_change("출고 차수 등록", "outbound_batch", batch_id,
                      f"{round_no} / {customer} (계획 {added}건)")
        flash(f"차수가 등록됐어. (계획 S/N {added}건)")
        return redirect(url_for("outbound_round_edit", batch_id=batch_id))

    return render_template("outbound_round.html", batch=None, planned=[],
                           today=_dt.now().strftime("%Y-%m-%d"),
                           default_handler=g.user["display_name"] or g.user["username"])


@app.route("/outbound/round/<int:batch_id>", methods=["GET", "POST"])
@perm_required("outbound")
def outbound_round_edit(batch_id):
    batch = db.get_outbound_batch(batch_id)
    if batch is None:
        flash("존재하지 않는 차수야.")
        return redirect(url_for("outbound_history"))
    if request.method == "POST":
        customer = request.form.get("customer", "").strip()
        ship_date = request.form.get("ship_date", "").strip()
        handler = request.form.get("handler", "").strip()
        round_no = request.form.get("round_no", "").strip()
        if not customer:
            flash("거래처를 입력해줘.")
            return redirect(url_for("outbound_round_edit", batch_id=batch_id))
        db.update_outbound_batch(batch_id, customer, ship_date, handler, round_no)
        import json as _json
        try:
            serials = _json.loads(request.form.get("serials_json", "") or "[]")
        except (ValueError, TypeError):
            serials = []
        added = db.add_planned_items_bulk(batch_id, serials)
        record_change("출고 차수 정보/계획 수정", "outbound_batch", batch_id,
                      f"{round_no} / {customer} (신규 계획 {added}건)")
        flash(f"차수 정보가 저장됐어. (신규 계획 S/N {added}건 추가)" if added else "차수 정보가 저장됐어.")
        return redirect(url_for("outbound_round_edit", batch_id=batch_id))

    planned = db.list_planned_items(batch_id)
    return render_template("outbound_round.html", batch=batch, planned=planned,
                           today=_dt.now().strftime("%Y-%m-%d"),
                           default_handler=batch["handler"] or "")


@app.route("/outbound/planned/<int:planned_id>/delete", methods=["POST"])
@perm_required("outbound")
def outbound_planned_item_delete(planned_id):
    db.delete_planned_item(planned_id)
    record_change("출고 계획 항목 삭제", "outbound_planned_item", planned_id, "")
    flash("계획 항목이 삭제됐어.")
    return redirect(request.referrer or url_for("outbound_history"))
```

- [ ] **Step 5: 템플릿 작성** — `templates/outbound_round.html`

```html
{% extends "base.html" %}
{% block title %}차수 입력 — Chardon QMS{% endblock %}
{% block content %}
<h2>🚚 차수 입력
  {% if batch %}<span class="muted" style="font-size:14px; font-weight:400;">— {{ batch.round_no or ('#' ~ batch.id) }} 수정</span>{% endif %}
</h2>

<div class="card">
  <p class="muted" style="margin:0; font-size:13.5px;">
    차수(=출고 배치) 하나를 등록하고, 이번 차수에 나갈 예정인 S/N 목록을 미리 붙여넣어 계획해둘 수 있어.
    나중에 출고 스캔 화면에서 이 계획과 실제 스캔된 항목을 대조해서 진행상황을 보여줘.
  </p>
</div>

<form method="POST" id="roundForm">
  <div class="card">
    <h3 style="margin-top:0; font-size:15px;">차수 정보</h3>
    <div style="display:flex; gap:12px; flex-wrap:wrap;">
      <div style="flex:1; min-width:120px;">
        <label>차수 <span style="color:var(--fail);">*</span></label>
        <input type="text" name="round_no" required placeholder="예: 3차" value="{{ batch.round_no if batch else '' }}">
      </div>
      <div style="flex:1; min-width:170px;">
        <label>거래처 <span style="color:var(--fail);">*</span></label>
        <input type="text" name="customer" required placeholder="예: ACE" value="{{ batch.customer if batch else '' }}">
      </div>
      <div style="flex:1; min-width:150px;">
        <label>출고예정일</label>
        <input type="date" name="ship_date" value="{{ batch.ship_date if batch else today }}">
      </div>
      <div style="flex:1; min-width:150px;">
        <label>담당자</label>
        <input type="text" name="handler" value="{{ default_handler }}">
      </div>
    </div>
  </div>

  {% if planned %}
  <div class="card">
    <h3 style="margin-top:0; font-size:15px;">이미 등록된 계획 S/N ({{ planned|length }}건)</h3>
    <table>
      <thead><tr><th>S/N</th><th></th></tr></thead>
      <tbody>
        {% for p in planned %}
        <tr>
          <td>{{ p.serial_no }}</td>
          <td>
            <form method="POST" action="{{ url_for('outbound_planned_item_delete', planned_id=p.id) }}"
                  onsubmit="return confirm('이 계획 항목을 지울까?');">
              <button type="submit" class="btn secondary" style="margin-top:0; color:var(--fail);">삭제</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}

  <div class="card">
    <h3 style="margin-top:0; font-size:15px;">{{ '계획 S/N 추가' if batch else '계획 S/N 목록' }}</h3>
    {% include "_serial_paste_grid.html" %}
  </div>

  {% if batch %}
  <div class="card" style="display:flex; gap:10px; flex-wrap:wrap;">
    <a href="{{ url_for('outbound_qr_labels', batch_id=batch.id) }}" class="btn secondary" style="margin-top:0;">📄 QR 라벨 엑셀 출력</a>
    <a href="{{ url_for('outbound_scan_edit', batch_id=batch.id) }}" class="btn secondary" style="margin-top:0;">🔍 출고 스캔 시작</a>
  </div>
  {% endif %}

  <button type="submit" style="width:100%; font-size:16px; padding:15px; margin-top:4px;">저장</button>
</form>

<script>
document.getElementById('roundForm').addEventListener('submit', function () {
  window.collectSerialGridValues();
});
</script>
{% endblock %}
```

**주의**: `outbound_qr_labels` 라우트는 Task F에서 생김 — 지금 이 템플릿을 렌더링해봤을 때 `url_for('outbound_qr_labels', ...)`가 `BuildError`를 낼 수 있으므로, **Task C의 검증은 `batch=None`(신규 등록) 경로만** 확인하고, `batch`가 있는 편집 화면 렌더 확인은 Task F 완료 이후로 미룬다.

- [ ] **Step 6: 검증 스크립트**

```python
import app as appmodule, database as db

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

r = client.get("/outbound/round/new")
assert r.status_code == 200
body = r.get_data(as_text=True)
assert 'name="round_no"' in body and "snGrid" in body
print("OK: outbound_round.html(신규) 렌더")

r2 = client.post("/outbound/round/new", data={
    "customer": "테스트거래처_PLANV2", "ship_date": "2026-09-20", "handler": "홍길동",
    "round_no": "테스트3차",
    "serials_json": '["TESTSN_ROUND_A", "TESTSN_ROUND_B", "TESTSN_ROUND_A"]',  # 중복 포함
}, follow_redirects=True)
assert r2.status_code == 200

batches = db.list_outbound_batches(query="테스트거래처_PLANV2")
assert batches, "차수가 안 만들어졌어"
batch_id = batches[0]["id"]
assert batches[0]["round_no"] == "테스트3차"
planned = db.list_planned_items(batch_id)
assert len(planned) == 2, f"중복 제거 안 됨: {len(planned)}건"
print("OK: 차수 생성 + 계획 S/N 중복 제거 확인")

added2 = db.add_planned_items_bulk(batch_id, ["TESTSN_ROUND_A", "TESTSN_ROUND_C"])
assert added2 == 1  # A는 이미 있으니 C만 추가됨
print("OK: add_planned_items_bulk 재호출 시 기존 항목 재중복 방지")

assert db.planned_item_exists(batch_id, "TESTSN_ROUND_B") is True
assert db.planned_item_exists(batch_id, "TESTSN_ROUND_ZZZ") is False
print("OK: planned_item_exists")

target_id = db.list_planned_items(batch_id)[0]["id"]
r3 = client.post(f"/outbound/planned/{target_id}/delete", follow_redirects=True)
assert len(db.list_planned_items(batch_id)) == 2
print("OK: 계획 항목 삭제")

conn = db.get_conn()
conn.execute("DELETE FROM outbound_planned_items WHERE batch_id=?", (batch_id,))
conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,))
conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 7: quality-watcher 검증 요청**

- [ ] **Step 8: Commit** — 메인 세션이 처리

---

## Task D: S/N 발급 페이지 붙여넣기 전환 + 발급이력 삭제

**Files:**
- Modify: `database.py` (`create_serial()` 다음에 `create_serials_bulk()`/`delete_serial()` 추가)
- Modify: `app.py` (`outbound_serial_new()` 함수 전체 교체 + `outbound_serial_delete` 라우트 추가)
- Modify: `templates/outbound_serial.html` (전면 재작성)

**Interfaces (Consumes):** `templates/_serial_paste_grid.html`(Task C), `db.classify_serial_no`(Task A, 간접적으로 미리보기에 쓰임)
**Interfaces (Produces):** `db.create_serials_bulk(serial_nos, issued_by) -> (added: list[str], skipped: list[str])`, `db.delete_serial(serial_id) -> None`, 라우트 `outbound_serial_delete`(신규), `outbound_serial_new`(기존 라우트, 동작 변경)

- [ ] **Step 1: `database.py`에 벌크 발급/삭제 함수 추가**

`database.py`의 `create_serial()` 함수 바로 다음에 추가:

```python
def create_serials_bulk(serial_nos, issued_by):
    """여러 S/N을 한 번에 발급 — 이미 발급된 것(제출 목록 내 중복 포함)은 조용히
    건너뛰고 나머지는 계속 진행한다(create_serial()과 달리 예외를 던지지 않는다 —
    붙여넣기 특성상 한 줄이 실패했다고 전체를 막으면 안 되기 때문).
    반환: (added: [str], skipped: [str])."""
    conn = get_conn()
    added, skipped, seen = [], [], set()
    for sn in serial_nos:
        sn = (sn or "").strip()
        if not sn:
            continue
        if sn in seen:
            skipped.append(sn)
            continue
        seen.add(sn)
        try:
            conn.execute(
                "INSERT INTO finished_goods_serials (serial_no, issued_by) VALUES (?, ?)",
                (sn, issued_by))
            added.append(sn)
        except sqlite3.IntegrityError:
            skipped.append(sn)
    conn.commit()
    conn.close()
    return added, skipped


def delete_serial(serial_id):
    conn = get_conn()
    conn.execute("DELETE FROM finished_goods_serials WHERE id=?", (serial_id,))
    conn.commit()
    conn.close()
```

- [ ] **Step 2: `app.py`의 `outbound_serial_new()` 교체 + 삭제 라우트 추가**

기존 `outbound_serial_new()` 함수를 아래로 **교체**:

```python
@app.route("/outbound/serial/new", methods=["GET", "POST"])
@perm_required("outbound")
def outbound_serial_new():
    """완제품 S/N 발급 — 여러 줄을 붙여넣어 한 번에 발급한다(2026-09-15 전환, 1차 구현의
    단일입력 폼을 대체). 발급 시점엔 제품 정보를 연결하지 않는다(설계문서 확정사항)."""
    if request.method == "POST":
        import json as _json
        try:
            serials = _json.loads(request.form.get("serials_json", "") or "[]")
        except (ValueError, TypeError):
            serials = []
        serials = [s.strip() for s in serials if (s or "").strip()]
        if not serials:
            flash("S/N을 최소 1개 이상 입력해줘.")
            return redirect(url_for("outbound_serial_new"))
        added, skipped = db.create_serials_bulk(serials, g.user["display_name"] or g.user["username"])
        if added:
            preview = ", ".join(added[:10]) + (" 외" if len(added) > 10 else "")
            record_change("완제품 S/N 발급", "outbound_serial", None, f"{len(added)}건 ({preview})")
        msg = f"{len(added)}건 발급 완료."
        if skipped:
            msg += f" {len(skipped)}건은 이미 발급됐거나 중복이라 건너뜀."
        flash(msg)
        recent = db.list_serials(limit=50)
        return render_template("outbound_serial.html", added=added, recent=recent, q="")

    q = request.args.get("q", "").strip()
    recent = db.list_serials(query=q or None, limit=50)
    return render_template("outbound_serial.html", added=[], recent=recent, q=q)


@app.route("/outbound/serial/<int:serial_id>/delete", methods=["POST"])
@perm_required("outbound")
def outbound_serial_delete(serial_id):
    db.delete_serial(serial_id)
    record_change("완제품 S/N 발급 이력 삭제", "outbound_serial", serial_id, "")
    flash("발급 이력이 삭제됐어.")
    return redirect(url_for("outbound_serial_new"))
```

- [ ] **Step 3: `templates/outbound_serial.html` 전면 재작성**

```html
{% extends "base.html" %}
{% block title %}S/N 발급 — Chardon QMS{% endblock %}
{% block content %}
<style>
@media print {
  header, footer, .no-print { display: none !important; }
  main { padding: 0 !important; max-width: 100% !important; }
  body { background: #fff !important; }
}
.qr-print-block { display: none; }
@media print {
  .qr-print-block { display: flex !important; flex-wrap: wrap; gap: 20px; }
  .qr-print-item { text-align: center; page-break-inside: avoid; }
}
</style>

<div class="no-print">
<h2>🚚 S/N 발급</h2>

<div class="card">
  <p class="muted" style="margin:0; font-size:13.5px;">
    생산 완료 직후 완제품에 부여할 S/N을 붙여넣으면(Ctrl+V) 여러 줄이 한 번에 채워지고,
    각 줄 옆에 <b>모델명이 자동으로 미리보기</b>돼(<a href="{{ url_for('outbound_rules') }}">분류 규칙 관리</a>에서 매핑표 수정 가능).
    이미 발급된 S/N은 다시 발급되지 않아(같은 목록에 중복으로 넣어도 한 번만 등록돼).
  </p>
</div>

<div class="card">
  <h3 style="margin-top:0; font-size:15px;">S/N 발급</h3>
  <form method="POST" id="serialIssueForm">
    {% include "_serial_paste_grid.html" %}
    <button type="submit" style="margin-top:12px;">발급</button>
  </form>
</div>

{% if added %}
<div class="card" style="border-left:4px solid var(--pass);">
  <h3 style="margin-top:0; font-size:15px;">방금 발급된 S/N ({{ added|length }}건)</h3>
  <div style="display:grid; grid-template-columns:repeat(auto-fill, minmax(160px, 1fr)); gap:14px;">
    {% for sn in added %}
    <div style="text-align:center;">
      <img src="{{ url_for('outbound_qr_image', serial_no=sn) }}" alt="QR" style="width:140px; height:140px;">
      <div style="font-size:12.5px; font-weight:700; margin-top:4px; word-break:break-all;">{{ sn }}</div>
    </div>
    {% endfor %}
  </div>
  <button type="button" class="btn secondary" style="margin-top:14px;" onclick="window.print()">🖨️ 전체 인쇄</button>
</div>
{% endif %}

<div class="card">
  <h3 style="margin-top:0; font-size:15px;">최근 발급 이력</h3>
  <form method="GET" style="display:flex; gap:8px; margin-bottom:10px;">
    <input type="text" name="q" value="{{ q }}" placeholder="S/N 일부 입력" style="flex:1;">
    <button type="submit" style="margin-top:0;">조회</button>
  </form>
  <table>
    <thead><tr><th>S/N</th><th>발급자</th><th>발급일시</th><th></th></tr></thead>
    <tbody>
      {% for r in recent %}
      <tr>
        <td>{{ r.serial_no }}</td>
        <td>{{ r.issued_by or '-' }}</td>
        <td>{{ r.issued_at | datetime_korean }}</td>
        <td>
          <form method="POST" action="{{ url_for('outbound_serial_delete', serial_id=r.id) }}"
                onsubmit="return confirm('이 발급 이력을 지울까? (다시 발급 가능해져)');">
            <button type="submit" class="btn secondary" style="margin-top:0; color:var(--fail);">삭제</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr><td colspan="4" style="text-align:center; color:var(--muted);">발급 이력이 없어.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
</div>

<div class="qr-print-block">
  {% for sn in added %}
  <div class="qr-print-item">
    <img src="{{ url_for('outbound_qr_image', serial_no=sn) }}" alt="QR" style="width:120px; height:120px;">
    <div style="font-size:13px; font-weight:700;">{{ sn }}</div>
  </div>
  {% endfor %}
</div>

<script>
document.getElementById('serialIssueForm').addEventListener('submit', function () {
  window.collectSerialGridValues();
});
</script>
{% endblock %}
```

- [ ] **Step 4: 검증 스크립트**

```python
import app as appmodule, database as db

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

r = client.get("/outbound/serial/new")
assert r.status_code == 200
body = r.get_data(as_text=True)
assert "snGrid" in body
print("OK: outbound_serial.html 렌더(그리드)")

r2 = client.post("/outbound/serial/new", data={
    "serials_json": '["TESTSN_BULK_1", "TESTSN_BULK_2", "TESTSN_BULK_1"]',  # 중복 포함
})
body2 = r2.get_data(as_text=True)
assert "2건 발급 완료" in body2
assert "1건은 이미 발급됐거나 중복" in body2
print("OK: 벌크 발급 + 중복 스킵 메시지")

serials = db.list_serials(query="TESTSN_BULK")
assert len(serials) == 2
print("OK: DB에 2건만 등록됨")

r3 = client.post("/outbound/serial/new", data={"serials_json": '["TESTSN_BULK_1"]'})
body3 = r3.get_data(as_text=True)
assert "0건 발급 완료" in body3
print("OK: 이미 발급된 S/N 재발급 시도 시 0건 처리")

sid = serials[0]["id"]
r4 = client.post(f"/outbound/serial/{sid}/delete", follow_redirects=True)
remaining = db.list_serials(query="TESTSN_BULK")
assert len(remaining) == 1
print("OK: 발급 이력 삭제")

conn = db.get_conn()
conn.execute("DELETE FROM finished_goods_serials WHERE serial_no LIKE 'TESTSN_BULK%'")
conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 5: quality-watcher 검증 요청**

- [ ] **Step 6: Commit** — 메인 세션이 처리

---

## Task E: 출고 스캔 페이지 재작성 (즉시저장 아키텍처, 수량칸 제거, 차수선택, 계획대조)

**Files:**
- Modify: `app.py` (`outbound_scan_new`를 리다이렉트 별칭으로 축소, `_outbound_scan_submit()` 삭제, `outbound_scan_list`/`outbound_batch_update`/`outbound_item_add` 신규, `outbound_scan_edit` 수정, `outbound_serial_check` 확장)
- Create: `templates/outbound_scan_list.html`
- Modify: `templates/outbound_scan.html` (전면 재작성)

**Interfaces (Consumes):** `db.classify_serial_no`(Task A), `db.list_planned_items`/`planned_item_exists`(Task C), `db.create_outbound_batch`/`update_outbound_batch`(Task C 확장판), `_save_ncr_photo`(1차 구현)
**Interfaces (Produces):** 라우트 `outbound_scan_list`, `outbound_batch_update`, `outbound_item_add`. `outbound_scan_new` → `outbound_round_new` 리다이렉트. `outbound_scan_edit` GET만 남김(POST 제거).

> **실행 순서 주의**: `outbound_scan.html`이 Task G의 `outbound_batch_confirm`을 참조하므로, **Task G를 Task E 바로 다음(Task F보다 먼저) 구현한다.** 최종 실행 순서: **A → B → C → D → E → G → F → H**.

- [ ] **Step 1: `app.py` — 라우트 교체/추가**

기존 `outbound_scan_new()`/`outbound_scan_edit()`/`_outbound_scan_submit()`을 아래로 **전부 교체**:

```python
@app.route("/outbound/scan")
@perm_required("outbound")
def outbound_scan_list():
    """스캔할 차수를 고르는 화면 — 배치 생성은 이제 outbound_round_new의 책임이라
    여기서는 목록만 보여준다."""
    q = request.args.get("q", "").strip()
    batches = db.list_outbound_batches(query=q or None)
    return render_template("outbound_scan_list.html", batches=batches, q=q)


@app.route("/outbound/scan/new", methods=["GET", "POST"])
@perm_required("outbound")
def outbound_scan_new():
    """1차 구현에서 이 라우트가 새 배치를 즉시 만들었는데, 이제 그 책임은
    outbound_round_new로 옮겨졌다. 옛 메뉴 링크/북마크가 깨지지 않도록 리다이렉트만 한다."""
    return redirect(url_for("outbound_round_new"))


@app.route("/outbound/scan/<int:batch_id>", methods=["GET"])
@perm_required("outbound")
def outbound_scan_edit(batch_id):
    batch = db.get_outbound_batch(batch_id)
    if batch is None:
        flash("존재하지 않는 출고 배치야.")
        return redirect(url_for("outbound_history"))
    items = db.list_outbound_items(batch_id)
    planned = db.list_planned_items(batch_id)
    planned_serials = {p["serial_no"] for p in planned}
    matched = sum(1 for it in items if it["serial_no"] in planned_serials)
    extra = len(items) - matched
    plan_summary = {"planned_total": len(planned), "matched": matched, "extra": extra}
    return render_template("outbound_scan.html", batch=batch, items=items,
                           planned_serials=planned_serials, plan_summary=plan_summary)


@app.route("/outbound/batch/<int:batch_id>/update", methods=["POST"])
@perm_required("outbound")
def outbound_batch_update(batch_id):
    batch = db.get_outbound_batch(batch_id)
    if batch is None:
        flash("존재하지 않는 출고 배치야.")
        return redirect(url_for("outbound_history"))
    customer = request.form.get("customer", "").strip()
    ship_date = request.form.get("ship_date", "").strip()
    handler = request.form.get("handler", "").strip()
    round_no = request.form.get("round_no", "").strip()
    if not customer:
        flash("거래처를 입력해줘.")
        return redirect(url_for("outbound_scan_edit", batch_id=batch_id))
    db.update_outbound_batch(batch_id, customer, ship_date, handler, round_no)
    record_change("출고 배치 정보 수정", "outbound_batch", batch_id, f"{round_no} / {customer}")
    flash("출고 정보가 저장됐어.")
    return redirect(url_for("outbound_scan_edit", batch_id=batch_id))


@app.route("/outbound/scan/<int:batch_id>/add-item", methods=["POST"])
@perm_required("outbound")
def outbound_item_add(batch_id):
    """새 항목을 즉시 서버에 저장한다(2026-09-15 아키텍처 변경 — 예전엔 브라우저에
    누적했다가 '저장' 한 번에 일괄 제출했는데, "매번 임시저장 필수" 요구사항 때문에
    '리스트에 추가'를 누르는 즉시 이 라우트가 호출되도록 바꿨다). quantity는 더 이상
    받지 않는다(항상 None)."""
    batch = db.get_outbound_batch(batch_id)
    if batch is None:
        return jsonify({"ok": False, "error": "존재하지 않는 출고 배치야."}), 404

    serial_no = request.form.get("serial_no", "").strip()
    if not serial_no:
        return jsonify({"ok": False, "error": "S/N이 비어있어."}), 400
    product_name = request.form.get("product_name", "").strip()

    item_id = db.add_outbound_item(batch_id, serial_no, product_name, None)
    photos = []
    for file in request.files.getlist("photos"):
        if not file or not file.filename:
            continue
        ext = os.path.splitext(file.filename)[1].lower() or ".jpg"
        if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
            continue
        base = f"ob{batch_id}_{item_id}_{uuid.uuid4().hex[:8]}"
        fname = _save_ncr_photo(file, OUTBOUND_PHOTO_DIR, base)
        photo_id = db.add_outbound_item_photo(item_id, fname)
        photos.append({"id": photo_id, "file_path": fname,
                        "url": url_for("outbound_photo_file", filename=fname)})

    record_change("출고 항목 추가", "outbound_item", item_id, f"{serial_no} / {product_name}")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "item": {
            "id": item_id, "serial_no": serial_no,
            "product_name": product_name, "photos": photos,
        }})
    flash("항목이 추가됐어.")
    return redirect(url_for("outbound_scan_edit", batch_id=batch_id))
```

(`uuid`는 파일 상단에 이미 import돼 있는지 확인하고, 없으면 `import uuid`를 파일 최상단 import 블록에 추가할 것 — 1차 구현의 `_outbound_scan_submit()`이 함수 내부에서 `import uuid`를 지역으로 했었는데, 이 함수가 삭제되므로 새 함수(`outbound_item_add`)에서 다시 필요하다.)

- [ ] **Step 2: `outbound_serial_check()` 확장** — 분류 라벨 + 계획 소속 여부 추가

기존 `outbound_serial_check()` 함수를 아래로 **교체**:

```python
@app.route("/outbound/serial-check")
@perm_required("outbound")
def outbound_serial_check():
    """스캔/입력된 S/N의 상태를 JSON으로 돌려준다 — 미등록/다른배치사용/계획외 여부 +
    자동분류 라벨. 셋 다 진행을 막지 않고 경고만 표시하는 용도(설계문서 확정사항)."""
    serial_no = (request.args.get("serial_no") or "").strip()
    exclude_batch_id = request.args.get("exclude_batch_id", type=int)
    if not serial_no:
        return jsonify({"registered": False, "used_in_other_batch": None,
                        "used_in_other_batch_customer": None,
                        "classified_label": None, "in_plan": None})
    registered = db.serial_exists(serial_no)
    other_batch_id = db.find_outbound_item_batch(serial_no, exclude_batch_id=exclude_batch_id)
    other_batch_customer = None
    if other_batch_id:
        b = db.get_outbound_batch(other_batch_id)
        other_batch_customer = b["customer"] if b else None
    classified_label = db.classify_serial_no(serial_no)
    in_plan = db.planned_item_exists(exclude_batch_id, serial_no) if exclude_batch_id else None
    return jsonify({
        "registered": registered,
        "used_in_other_batch": other_batch_id,
        "used_in_other_batch_customer": other_batch_customer,
        "classified_label": classified_label,
        "in_plan": in_plan,
    })
```

- [ ] **Step 3: `templates/outbound_scan_list.html` 신규 작성**

```html
{% extends "base.html" %}
{% block title %}출고 스캔 — 차수 선택 — Chardon QMS{% endblock %}
{% block content %}
<h2>🚚 출고 스캔 — 차수 선택</h2>

<div class="card">
  <p class="muted" style="margin:0; font-size:13.5px;">
    스캔할 차수를 골라줘. 새 차수를 만들려면 <a href="{{ url_for('outbound_round_new') }}">차수 입력</a>에서 먼저 등록해야 해.
  </p>
</div>

<div class="card">
  <form method="GET" style="display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap;">
    <div style="flex:1; min-width:170px;">
      <label>거래처/담당자로 찾기</label>
      <input type="text" name="q" value="{{ q }}">
    </div>
    <button type="submit" style="margin-top:0;">조회</button>
  </form>
</div>

<div class="card">
  <table>
    <thead><tr><th>차수</th><th>거래처</th><th>출고예정일</th><th>담당자</th><th>스캔된 항목</th><th></th></tr></thead>
    <tbody>
      {% for b in batches %}
      <tr>
        <td>{{ b.round_no or '-' }}</td>
        <td>{{ b.customer or '-' }}</td>
        <td>{{ b.ship_date | date_korean if b.ship_date else '-' }}</td>
        <td>{{ b.handler or '-' }}</td>
        <td>{{ b.item_count }}</td>
        <td><a href="{{ url_for('outbound_scan_edit', batch_id=b.id) }}" class="btn secondary" style="margin-top:0;">스캔 시작/이어하기</a></td>
      </tr>
      {% else %}
      <tr><td colspan="6" style="text-align:center; color:var(--muted);">등록된 차수가 없어.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 4: `templates/outbound_scan.html` 전면 재작성**

```html
{% extends "base.html" %}
{% block title %}출고 스캔 — Chardon QMS{% endblock %}
{% block content %}
<style>
  .ob-photo-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(90px, 1fr));
                   gap:8px; margin-top:8px; }
  .ob-photo-grid img { width:100%; aspect-ratio:1/1; object-fit:cover; border-radius:8px;
                        border:1px solid var(--border); }
  #serialWarning { background:#fef3c7; border:1px solid #fcd34d; color:#92400e;
                   border-radius:8px; padding:10px 14px; font-size:13px; margin-top:10px; }
  .plan-summary { display:flex; gap:16px; flex-wrap:wrap; font-size:13.5px; }
  .plan-summary b { font-size:16px; }
  .plan-badge { display:inline-block; padding:1px 7px; border-radius:6px; font-size:11px; font-weight:700; margin-left:6px; }
  .plan-badge.ok { background:#d1fae5; color:#065f46; }
  .plan-badge.extra { background:#fef3c7; color:#92400e; }
</style>

<h2>🚚 출고 스캔 <span class="muted" style="font-size:14px; font-weight:400;">— {{ batch.round_no or ('#' ~ batch.id) }}</span></h2>

<div class="card">
  <h3 style="margin-top:0; font-size:15px;">출고 정보</h3>
  <form method="POST" action="{{ url_for('outbound_batch_update', batch_id=batch.id) }}"
        style="display:flex; gap:12px; flex-wrap:wrap; align-items:flex-end;">
    <div style="flex:1; min-width:100px;">
      <label>차수</label>
      <input type="text" name="round_no" value="{{ batch.round_no or '' }}">
    </div>
    <div style="flex:1; min-width:170px;">
      <label>거래처 <span style="color:var(--fail);">*</span></label>
      <input type="text" name="customer" required value="{{ batch.customer or '' }}">
    </div>
    <div style="flex:1; min-width:150px;">
      <label>출고(예정)일</label>
      <input type="date" name="ship_date" value="{{ batch.ship_date or '' }}">
    </div>
    <div style="flex:1; min-width:150px;">
      <label>담당자</label>
      <input type="text" name="handler" value="{{ batch.handler or '' }}">
    </div>
    <button type="submit" class="btn secondary" style="margin-top:0;">저장</button>
  </form>
  {% if batch.confirmed_at %}
  <p style="margin:10px 0 0; color:var(--pass); font-size:13px;">✅ {{ batch.confirmed_by }}이(가) {{ batch.confirmed_at | datetime_korean }}에 출고 확인함</p>
  {% else %}
  <form method="POST" action="{{ url_for('outbound_batch_confirm', batch_id=batch.id) }}" style="margin-top:10px;">
    <button type="submit" class="btn secondary" style="margin-top:0;">✅ 출고 확인</button>
  </form>
  {% endif %}
</div>

<div class="card">
  <h3 style="margin-top:0; font-size:15px;">계획 대비 진행상황</h3>
  <div class="plan-summary">
    <span>계획 <b>{{ plan_summary.planned_total }}</b>개 중 <b id="planMatched">{{ plan_summary.matched }}</b>개 스캔됨</span>
    <span>계획외 <b id="planExtra">{{ plan_summary.extra }}</b>건</span>
  </div>
</div>

<div class="card">
  <h3 style="margin-top:0; font-size:15px;">저장된 항목 (<span id="itemCountLabel">{{ items|length }}</span>건)</h3>
  <table id="itemsTable">
    <thead><tr><th>S/N</th><th>제품명/모델명</th><th>사진</th><th></th></tr></thead>
    <tbody>
      {% for it in items %}
      <tr>
        <td>{{ it.serial_no }}
          {% if it.serial_no in planned_serials %}<span class="plan-badge ok">계획</span>
          {% else %}<span class="plan-badge extra">계획외</span>{% endif %}
        </td>
        <td>
          <form method="POST" action="{{ url_for('outbound_item_edit', item_id=it.id) }}"
                style="display:flex; gap:6px; flex-wrap:wrap; align-items:center;">
            <input type="text" name="product_name" value="{{ it.product_name or '' }}"
                   placeholder="제품명/모델명" style="flex:1; min-width:160px;">
            <button type="submit" class="btn secondary" style="margin-top:0;">저장</button>
          </form>
        </td>
        <td>
          <div class="ob-photo-grid" style="grid-template-columns:repeat(auto-fill, minmax(60px, 1fr)); max-width:220px;">
            {% for p in it.photos %}
            <div style="position:relative;">
              <img src="{{ url_for('outbound_photo_file', filename=p.file_path) }}" alt="사진">
              <form method="POST" action="{{ url_for('outbound_photo_delete', photo_id=p.id) }}"
                    style="position:absolute; top:-6px; right:-6px;"
                    onsubmit="return confirm('이 사진을 지울까?');">
                <button type="submit" style="background:#dc2626; color:#fff; border:none;
                        border-radius:999px; width:20px; height:20px; font-size:11px; line-height:1;
                        cursor:pointer; padding:0; margin:0;">✕</button>
              </form>
            </div>
            {% else %}
            <span class="muted" style="font-size:12px;">없음</span>
            {% endfor %}
          </div>
        </td>
        <td>
          <form method="POST" action="{{ url_for('outbound_item_delete', item_id=it.id) }}"
                onsubmit="return confirm('이 항목을 삭제할까?');">
            <button type="submit" class="btn secondary" style="margin-top:0; color:var(--fail);">삭제</button>
          </form>
        </td>
      </tr>
      {% else %}
      <tr class="empty-row"><td colspan="4" style="text-align:center; color:var(--muted);">저장된 항목이 없어.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>

<div class="card">
  <h3 style="margin-top:0; font-size:15px;">새 항목 스캔·입력</h3>

  <div id="qrReaderRegion" style="width:300px; max-width:100%;"></div>

  <div style="display:flex; gap:8px; align-items:center; margin-top:10px; flex-wrap:wrap;">
    <input type="text" id="manualSerialInput" placeholder="카메라를 못 쓰면 S/N을 직접 입력"
           style="flex:1; min-width:160px;">
    <button type="button" id="manualSerialConfirm" class="btn secondary" style="margin-top:0;">확인</button>
  </div>

  <div id="serialWarning" style="display:none;"></div>

  <form id="addItemForm" style="margin-top:12px;">
    <div style="display:flex; gap:12px; flex-wrap:wrap;">
      <div style="flex:1; min-width:150px;">
        <label>S/N</label>
        <input name="serial_no" id="curSerial" readonly placeholder="스캔 또는 직접입력 대기중">
      </div>
      <div style="flex:1; min-width:200px;">
        <label>제품명/모델명</label>
        <input name="product_name" id="curProductName" placeholder="제품명/모델명 (자동입력됨, 수정 가능)">
      </div>
    </div>

    <div style="margin-top:10px;">
      <div style="display:flex; gap:10px; flex-wrap:wrap;">
        <label class="btn secondary" style="margin-top:0; padding:10px 16px; font-size:13.5px; cursor:pointer;">
          📸 카메라로 촬영
          <input type="file" name="photos" accept="image/*" capture="environment" multiple
                 style="display:none;" onchange="addCurPhotos()">
        </label>
        <label class="btn secondary" style="margin-top:0; padding:10px 16px; font-size:13.5px; cursor:pointer;">
          🖼️ 갤러리에서 선택
          <input type="file" name="photos" accept="image/*" multiple
                 style="display:none;" onchange="addCurPhotos()">
        </label>
      </div>
      <div id="curPhotoPreview" class="ob-photo-grid"></div>
    </div>

    <button type="submit" style="margin-top:14px;">리스트에 추가</button>
    <span id="addItemMsg" class="muted" style="margin-left:10px; font-size:12.5px;"></span>
  </form>
</div>

<script src="https://cdn.jsdelivr.net/npm/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
<script>
(function () {
  var scannedSerials = new Set([{% for it in items %}"{{ it.serial_no }}",{% endfor %}]);
  var plannedSerials = new Set([{% for s in planned_serials %}"{{ s }}",{% endfor %}]);
  var batchId = {{ batch.id }};

  window.addCurPhotos = function () {
    var box = document.getElementById('curPhotoPreview');
    var all = [];
    document.querySelectorAll('#addItemForm input[type=file]').forEach(function (inp) {
      for (var i = 0; i < inp.files.length; i++) all.push(inp.files[i]);
    });
    box.innerHTML = all.map(function (f) { return '<img src="' + URL.createObjectURL(f) + '">'; }).join('');
  };

  function checkSerial(serial) {
    var url = '/outbound/serial-check?serial_no=' + encodeURIComponent(serial) + '&exclude_batch_id=' + batchId;
    fetch(url).then(function (r) { return r.json(); }).then(function (data) {
      var box = document.getElementById('serialWarning');
      var msgs = [];
      if (!data.registered) msgs.push('⚠ 발급 이력에 없는 S/N이야. 그래도 계속 진행할 수 있어.');
      if (data.used_in_other_batch) msgs.push('⚠ 이미 다른 출고(' + (data.used_in_other_batch_customer || '') + ')에 쓰인 S/N이야.');
      if (scannedSerials.has(serial)) msgs.push('⚠ 이 출고 건에 이미 추가된 S/N이야.');
      if (plannedSerials.size && !data.in_plan) msgs.push('⚠ 계획에 없는 항목이 스캔됐어.');
      if (msgs.length) { box.style.display = 'block'; box.innerHTML = msgs.join('<br>'); }
      else { box.style.display = 'none'; box.innerHTML = ''; }
      var nameField = document.getElementById('curProductName');
      if (data.classified_label && !nameField.value.trim()) nameField.value = data.classified_label;
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

  function appendItemRow(item, inPlan) {
    var tbody = document.querySelector('#itemsTable tbody');
    var empty = tbody.querySelector('.empty-row');
    if (empty) empty.remove();
    var tr = document.createElement('tr');
    var photosHtml = (item.photos || []).map(function (p) {
      return '<div style="position:relative;"><img src="' + p.url + '">' +
        '<form method="POST" action="/outbound/photo/' + p.id + '/delete" style="position:absolute; top:-6px; right:-6px;" onsubmit="return confirm(\'이 사진을 지울까?\');">' +
        '<button type="submit" style="background:#dc2626;color:#fff;border:none;border-radius:999px;width:20px;height:20px;font-size:11px;line-height:1;cursor:pointer;padding:0;margin:0;">✕</button></form></div>';
    }).join('') || '<span class="muted" style="font-size:12px;">없음</span>';
    tr.innerHTML =
      '<td>' + item.serial_no + ' <span class="plan-badge ' + (inPlan ? 'ok">계획' : 'extra">계획외') + '</span></td>' +
      '<td><form method="POST" action="/outbound/item/' + item.id + '/edit" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;">' +
      '<input type="text" name="product_name" value="' + (item.product_name || '').replace(/"/g, '&quot;') + '" placeholder="제품명/모델명" style="flex:1;min-width:160px;">' +
      '<button type="submit" class="btn secondary" style="margin-top:0;">저장</button></form></td>' +
      '<td><div class="ob-photo-grid" style="grid-template-columns:repeat(auto-fill, minmax(60px, 1fr));max-width:220px;">' + photosHtml + '</div></td>' +
      '<td><form method="POST" action="/outbound/item/' + item.id + '/delete" onsubmit="return confirm(\'이 항목을 삭제할까?\');">' +
      '<button type="submit" class="btn secondary" style="margin-top:0;color:var(--fail);">삭제</button></form></td>';
    tbody.appendChild(tr);
    document.getElementById('itemCountLabel').textContent = tbody.querySelectorAll('tr:not(.empty-row)').length;
  }

  function updatePlanSummary(inPlan) {
    var el = document.getElementById(inPlan ? 'planMatched' : 'planExtra');
    el.textContent = parseInt(el.textContent, 10) + 1;
  }

  document.getElementById('addItemForm').addEventListener('submit', function (e) {
    e.preventDefault();
    var serial = document.getElementById('curSerial').value.trim();
    if (!serial) { alert('S/N을 스캔하거나 입력해줘.'); return; }
    var msg = document.getElementById('addItemMsg');
    msg.textContent = '저장 중...';
    var fd = new FormData(e.target);
    fetch('/outbound/scan/' + batchId + '/add-item', {
      method: 'POST', body: fd, headers: { 'X-Requested-With': 'XMLHttpRequest' }
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (!data.ok) { msg.textContent = '⚠ ' + (data.error || '저장 실패'); return; }
      var inPlan = plannedSerials.has(serial);
      scannedSerials.add(serial);
      appendItemRow(data.item, inPlan);
      updatePlanSummary(inPlan);
      msg.textContent = '✓ 추가됨';
      document.getElementById('curSerial').value = '';
      document.getElementById('curProductName').value = '';
      document.querySelectorAll('#addItemForm input[type=file]').forEach(function (inp) { inp.value = ''; });
      document.getElementById('curPhotoPreview').innerHTML = '';
      document.getElementById('serialWarning').style.display = 'none';
    }).catch(function () { msg.textContent = '⚠ 저장 실패(네트워크 확인)'; });
  });

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
      function () {}
    ).catch(function () {
      document.getElementById('qrReaderRegion').innerHTML =
        '<p class="muted">카메라를 사용할 수 없어. 아래 입력창에 S/N을 직접 입력해줘.</p>';
    });
  });
})();
</script>
{% endblock %}
```

- [ ] **Step 5: 검증 스크립트** (Task G까지 마친 뒤 실행 — 실행 순서 A→B→C→D→E→G→F→H 참고)

```python
import io, app as appmodule, database as db

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

batch_id = db.create_outbound_batch("테스트거래처_SCANV2", "2026-09-20", "홍길동",
                                     "테스트", round_no="테스트4차")
db.add_planned_items_bulk(batch_id, ["TESTSN_PLANNED_1", "TESTSN_PLANNED_2"])

r = client.get("/outbound/scan")
assert r.status_code == 200 and "테스트거래처_SCANV2" in r.get_data(as_text=True)
print("OK: outbound_scan_list(차수 선택) 렌더")

r2 = client.get("/outbound/scan/new", follow_redirects=False)
assert r2.status_code == 302
assert "/outbound/round/new" in r2.headers["Location"]
print("OK: outbound_scan_new가 outbound_round_new로 리다이렉트")

r3 = client.get(f"/outbound/scan/{batch_id}")
body3 = r3.get_data(as_text=True)
assert 'id="planMatched">0<' in body3
print("OK: outbound_scan_edit 렌더 + 계획 대비 진행상황 표시")

data = {"serial_no": "TESTSN_PLANNED_1", "product_name": "테스트품"}
files = {"photos": (io.BytesIO(b"fakejpgbytes"), "a.jpg")}
r4 = client.post(f"/outbound/scan/{batch_id}/add-item", data={**data, **files},
                  content_type="multipart/form-data", headers={"X-Requested-With": "XMLHttpRequest"})
j4 = r4.get_json()
assert j4["ok"] is True
assert j4["item"]["serial_no"] == "TESTSN_PLANNED_1"
assert len(j4["item"]["photos"]) == 1
print("OK: outbound_item_add 즉시저장(AJAX) + 계획 항목 매칭")

items = db.list_outbound_items(batch_id)
assert len(items) == 1 and items[0]["quantity"] is None
print("OK: quantity는 항상 None으로 저장됨")

r5 = client.get(f"/outbound/serial-check?serial_no=TESTSN_PLANNED_2&exclude_batch_id={batch_id}")
j5 = r5.get_json()
assert j5["in_plan"] is True
r6 = client.get(f"/outbound/serial-check?serial_no=TESTSN_UNPLANNED&exclude_batch_id={batch_id}")
j6 = r6.get_json()
assert j6["in_plan"] is False
print("OK: serial-check의 in_plan 판정")

r7 = client.post(f"/outbound/batch/{batch_id}/update",
                  data={"customer": "수정거래처", "ship_date": "2026-09-21", "handler": "김철수", "round_no": "5차"},
                  follow_redirects=True)
assert db.get_outbound_batch(batch_id)["customer"] == "수정거래처"
assert db.get_outbound_batch(batch_id)["round_no"] == "5차"
print("OK: outbound_batch_update")

conn = db.get_conn()
conn.execute("DELETE FROM outbound_planned_items WHERE batch_id=?", (batch_id,))
conn.execute("DELETE FROM outbound_item_photos WHERE item_id IN (SELECT id FROM outbound_items WHERE batch_id=?)", (batch_id,))
conn.execute("DELETE FROM outbound_items WHERE batch_id=?", (batch_id,))
conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,))
conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 6: 실기기/헤드리스 확인 (필수, CLAUDE.md 11절/17절)**

카메라 UI + JS DOM 조작(항목 추가 후 reload 없이 표에 반영되는지) 재작성이므로 코드리뷰만으로 "됐다"고 하면 안 된다. 로컬 Chrome을 fake camera device로 띄워 확인:

```bash
chrome.exe --headless=new --disable-gpu --no-sandbox \
  --use-fake-ui-for-media-stream --use-fake-device-for-media-stream \
  --screenshot=scan_check.png --window-size=500,1400 \
  "http://127.0.0.1:5000/outbound/scan/<실제 batch_id>"
```

로그인 세션이 필요하므로, 개발 서버(`python app.py`)를 띄운 상태에서 먼저 로그인해 세션 쿠키를 얻거나, CLAUDE.md 17절의 진단 스크립트 주입 방식으로 `#qrReaderRegion` 안에 `<video>`가 생기는지, "리스트에 추가" 클릭 후 `#itemsTable`에 새 행이 실제로 추가되는지 확인할 것.

- [ ] **Step 7: quality-watcher 검증 요청**

- [ ] **Step 8: Commit** — 메인 세션이 처리

---

## Task G: 출고 이력 "출고 확인" 버튼

> **실행 순서 주의**: Task E의 `outbound_scan.html`이 `outbound_batch_confirm`을 참조하므로, 이 태스크는 **Task E 바로 다음(Task F보다 먼저)** 구현한다. 최종 실행 순서: **A → B → C → D → E → G → F → H**.

**Files:**
- Modify: `database.py` (`confirm_outbound_batch()` 추가)
- Modify: `app.py` (`outbound_batch_confirm` 라우트 추가)
- Modify: `templates/outbound_history.html` (확인 컬럼 + 차수 컬럼 추가)

**Interfaces (Consumes):** `outbound_batches.confirmed_by`/`confirmed_at` 컬럼(Task C)
**Interfaces (Produces):** `db.confirm_outbound_batch(batch_id, confirmed_by) -> None`, 라우트 `outbound_batch_confirm`

- [ ] **Step 1: `database.py`에 함수 추가**

`database.py`의 `update_outbound_batch()`(Task C에서 수정된 버전) 바로 다음에 추가:

```python
def confirm_outbound_batch(batch_id, confirmed_by):
    """확인은 잠금이 아니라 순수 기록용 — 확인 후에도 항목 추가·수정·삭제가 계속
    가능하다(설계문서 확정사항, 성적서 승인과는 다른 개념)."""
    conn = get_conn()
    conn.execute("""
        UPDATE outbound_batches SET confirmed_by=?, confirmed_at=datetime('now','localtime')
        WHERE id=?
    """, (confirmed_by, batch_id))
    conn.commit()
    conn.close()
```

- [ ] **Step 2: `app.py`에 라우트 추가**

`outbound_history()` 함수 근처(또는 Task E가 추가한 라우트들 다음)에 추가:

```python
@app.route("/outbound/batch/<int:batch_id>/confirm", methods=["POST"])
@perm_required("outbound")
def outbound_batch_confirm(batch_id):
    batch = db.get_outbound_batch(batch_id)
    if batch is None:
        flash("존재하지 않는 출고 배치야.")
        return redirect(url_for("outbound_history"))
    db.confirm_outbound_batch(batch_id, g.user["display_name"] or g.user["username"])
    record_change("출고 확인", "outbound_batch", batch_id, batch["customer"] or "")
    flash("출고 확인 처리됐어.")
    return redirect(request.referrer or url_for("outbound_history"))
```

- [ ] **Step 3: `templates/outbound_history.html`에 확인/차수 컬럼 추가**

기존 표를 아래로 **교체**:

```html
<div class="card">
  <table>
    <thead>
      <tr><th>차수</th><th>거래처</th><th>출고일</th><th>담당자</th><th>항목수</th><th>엑셀</th><th>확인</th></tr>
    </thead>
    <tbody>
      {% for b in batches %}
      <tr>
        <td>{{ b.round_no or '-' }}</td>
        <td><a href="{{ url_for('outbound_scan_edit', batch_id=b.id) }}">{{ b.customer or '-' }}</a></td>
        <td>{{ b.ship_date | date_korean if b.ship_date else '-' }}</td>
        <td>{{ b.handler or '-' }}</td>
        <td>{{ b.item_count }}</td>
        <td><a href="{{ url_for('outbound_batch_excel', batch_id=b.id) }}">📊 엑셀</a></td>
        <td>
          {% if b.confirmed_at %}
          <span style="color:var(--pass); font-size:12.5px;">✅ {{ b.confirmed_by }}<br>{{ b.confirmed_at | datetime_korean }}</span>
          {% else %}
          <form method="POST" action="{{ url_for('outbound_batch_confirm', batch_id=b.id) }}">
            <button type="submit" class="btn secondary" style="margin-top:0; font-size:12px; padding:4px 10px;">확인</button>
          </form>
          {% endif %}
        </td>
      </tr>
      {% else %}
      <tr><td colspan="7" style="text-align:center; color:var(--muted);">출고 이력이 없어.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
```

- [ ] **Step 4: 검증 스크립트**

```python
import app as appmodule, database as db

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

batch_id = db.create_outbound_batch("테스트거래처_CONFIRMV2", "2026-09-22", "홍길동", "테스트", round_no="확인차수")

r1 = client.get("/outbound/history")
body1 = r1.get_data(as_text=True)
assert "확인차수" in body1
assert f"/outbound/batch/{batch_id}/confirm" in body1
print("OK: outbound_history.html에 차수/확인 버튼 노출")

r2 = client.post(f"/outbound/batch/{batch_id}/confirm", follow_redirects=True)
b = db.get_outbound_batch(batch_id)
assert b["confirmed_by"] and b["confirmed_at"]
print("OK: 출고 확인 기록됨 —", b["confirmed_by"], b["confirmed_at"])

r3 = client.get(f"/outbound/scan/{batch_id}")
assert "출고 확인함" in r3.get_data(as_text=True)
print("OK: 출고 스캔 화면에도 확인 상태 표시됨")

db.add_outbound_item(batch_id, "TESTSN_AFTER_CONFIRM", "테스트품", None)
assert len(db.list_outbound_items(batch_id)) == 1
print("OK: 확인 후에도 항목 추가 가능(잠금 아님)")

conn = db.get_conn()
conn.execute("DELETE FROM outbound_items WHERE batch_id=?", (batch_id,))
conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,))
conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 5: quality-watcher 검증 요청**

- [ ] **Step 6: Commit** — 메인 세션이 처리

---

## Task F: 종류별 QR 라벨 엑셀 출력 + 출력 이력

**Files:**
- Modify: `database.py` (`outbound_qr_exports` 테이블 + CRUD)
- Modify: `report_builder.py` (`qr_png_bytes()`/`_sanitize_outbound_sheet_name()`/`build_outbound_qr_labels_excel()` 추가, 파일 끝)
- Modify: `app.py` (`outbound_qr_image()`를 `report_builder.qr_png_bytes()` 재사용하도록 변경, `outbound_qr_labels`/`outbound_qr_export_history` 라우트 추가)
- Create: `templates/outbound_qr_history.html`

**Interfaces (Consumes):** `db.list_planned_items`(Task C), `db.classify_serial_no`/`get_classify_rules`(Task A)
**Interfaces (Produces):** `report_builder.qr_png_bytes(data, box_size=8, border=2) -> bytes`, `report_builder.build_outbound_qr_labels_excel(round_no, ship_date, categorized_items: dict[str, list[str]]) -> io.BytesIO`, `db.record_qr_export(batch_id, generated_by, item_count) -> int`, `db.list_qr_exports(query=None, limit=200) -> list[sqlite3.Row]`, 라우트 `outbound_qr_labels`, `outbound_qr_export_history`

- [ ] **Step 1: `database.py` — 테이블 + CRUD**

`init_db()`에서 Task C가 추가한 `# ---- 2026-09-15 확장(차수 계획) 끝 ----` 바로 다음(`# ---- 신규 삽입 끝 ----` 앞)에 삽입:

```python
    # ---- 2026-09-15 확장: QR 라벨 출력 이력 ----
    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_qr_exports (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id      INTEGER NOT NULL REFERENCES outbound_batches(id),
            generated_by  TEXT,
            generated_at  TEXT DEFAULT (datetime('now','localtime')),
            item_count    INTEGER
        )
    """)
    # ---- 2026-09-15 확장(QR 라벨 출력 이력) 끝 ----
```

`database.py`의 `delete_planned_item()` 함수(Task C에서 추가) 바로 다음에 추가:

```python
# ---------- 2026-09-15 확장: QR 라벨 출력 이력 ----------

def record_qr_export(batch_id, generated_by, item_count):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO outbound_qr_exports (batch_id, generated_by, item_count)
        VALUES (?, ?, ?)
    """, (batch_id, generated_by, item_count))
    conn.commit()
    export_id = cur.lastrowid
    conn.close()
    return export_id


def list_qr_exports(query=None, limit=200):
    conn = get_conn()
    sql = """
        SELECT e.*, b.round_no, b.customer, b.ship_date
          FROM outbound_qr_exports e
          JOIN outbound_batches b ON b.id = e.batch_id
         WHERE 1=1
    """
    params = []
    if query:
        sql += " AND (b.customer LIKE ? OR b.round_no LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY e.id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows
```

- [ ] **Step 2: `report_builder.py` — QR PNG 공용 헬퍼 + 라벨 엑셀 빌더**

파일 맨 끝(`build_outbound_excel()` 함수 다음)에 추가:

```python
QR_LABEL_BOX_CM = 3.2       # QR 이미지 정사각형 한 변
QR_LABEL_TEXT_COL_CM = 4.5  # S/N 텍스트 열 너비
_QR_CHAR_TO_EMU = 7 * 9525  # _build_ncr_photo_sheet와 동일한 근사 변환(문자폭->EMU)
_QR_CM_PER_PT = 2.54 / 72


def qr_png_bytes(data, box_size=8, border=2):
    """QR PNG 바이트를 생성한다 — /outbound/qr 라우트와 QR 라벨 엑셀 출력 양쪽이
    이 함수 하나를 공유한다(8-1절 원칙, 1차 구현은 app.py 라우트 안에 인라인으로
    qrcode를 호출했는데 엑셀 출력에서도 똑같은 호출이 필요해져서 여기로 뽑았다)."""
    import io as _io, qrcode
    img = qrcode.make(data, box_size=box_size, border=border)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _sanitize_outbound_sheet_name(name, used_names):
    """엑셀 시트명 규칙(31자 제한, \\/?*[]: 금지) 적용 + 잘려서 겹치면 (2),(3)... 부여.
    파일명 금지문자 정규식(report_builder가 build_outbound_excel/성적서 등에서 쓰는
    r'[\\\\/:*?"<>|]')과는 별개다 — 이건 "엑셀 시트명" 규칙이라 대상 문자가 다르다."""
    import re as _re
    cleaned = _re.sub(r'[\\/\?\*\[\]:]', '', name or "").strip() or "미분류"
    base = cleaned[:31]
    candidate = base
    n = 2
    while candidate in used_names:
        suffix = f"({n})"
        candidate = base[:31 - len(suffix)] + suffix
        n += 1
    used_names.add(candidate)
    return candidate


def build_outbound_qr_labels_excel(round_no, ship_date, categorized_items):
    """차수의 계획 S/N을 분류 카테고리별로 시트를 나눠, 시트마다 "S/N 텍스트 | QR이미지 |
    S/N 텍스트 | QR이미지"를 한 줄에 2세트씩 반복 배치한 QR 라벨 엑셀을 만든다.
    실제 라벨을 인쇄해서 제품에 부착하는 용도(물류 체크리스트 아님).

    categorized_items: {카테고리라벨(예: "15kV 일반 수평 (42P)", "제어함 - RA",
                        "미분류"): [serial_no, ...], ...}
    반환: BytesIO (디스크 저장 안 함 — 호출부가 다운로드 응답으로 바로 스트리밍한다).

    이미지 배치는 _insert_logo()/_build_ncr_photo_sheet()와 같은 OneCellAnchor 정밀배치
    기법을 쓴다 — 단 여기는 그리드가 항상 규칙적(고정폭 열/행)이라 _build_ncr_photo_sheet
    처럼 복잡한 _resolve() 서브셀 계산 없이, 이미지 크기와 열너비/행높이를 똑같이 맞춰서
    셀 경계에 딱 맞게 앉히기만 하면 된다(더 단순한 경우)."""
    import io as _io

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used_names = set()

    text_col_w = cm_to_EMU(QR_LABEL_TEXT_COL_CM) / _QR_CHAR_TO_EMU
    qr_col_w = cm_to_EMU(QR_LABEL_BOX_CM) / _QR_CHAR_TO_EMU
    row_h_pt = QR_LABEL_BOX_CM / _QR_CM_PER_PT
    font = Font(name="맑은 고딕", size=18, bold=True)
    center = Alignment(horizontal="center", vertical="center")

    for category, serials in categorized_items.items():
        label = category or "미분류"
        ws = wb.create_sheet(title=_sanitize_outbound_sheet_name(label, used_names))
        ws.sheet_view.showGridLines = False
        ws.column_dimensions['A'].width = text_col_w
        ws.column_dimensions['B'].width = qr_col_w
        ws.column_dimensions['C'].width = text_col_w
        ws.column_dimensions['D'].width = qr_col_w

        for i, serial_no in enumerate(serials):
            row = i // 2 + 1
            text_col, qr_col = (1, 2) if i % 2 == 0 else (3, 4)
            ws.row_dimensions[row].height = row_h_pt

            c = ws.cell(row=row, column=text_col, value=serial_no)
            c.font = font
            c.alignment = center

            png_bytes = qr_png_bytes(serial_no, box_size=8, border=1)
            img = XLImage(_io.BytesIO(png_bytes))
            marker = AnchorMarker(col=qr_col - 1, colOff=0, row=row - 1, rowOff=0)
            size = XDRPositiveSize2D(cm_to_EMU(QR_LABEL_BOX_CM), cm_to_EMU(QR_LABEL_BOX_CM))
            img.anchor = OneCellAnchor(_from=marker, ext=size)
            ws.add_image(img)

    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
```

(`openpyxl`/`Font`/`Alignment`/`AnchorMarker`/`XDRPositiveSize2D`/`OneCellAnchor`/`cm_to_EMU`/`XLImage`가 파일 상단에 이미 import돼 있는지 확인하고, 없는 것만 추가할 것 — `_insert_logo()`가 이미 이 중 대부분을 쓰고 있다.)

- [ ] **Step 3: `app.py` — `outbound_qr_image` 중복 제거 + 신규 라우트 2개**

기존 `outbound_qr_image()` 함수를 아래로 **교체**(중복 로직 제거):

```python
@app.route("/outbound/qr")
@perm_required("outbound")
def outbound_qr_image():
    """S/N 문자열을 QR PNG로 즉석 생성해서 돌려준다 — 파일로 저장하지 않고
    필요할 때마다(화면 표시/인쇄) 매번 다시 만든다. QR 라벨 엑셀 출력과
    report_builder.qr_png_bytes() 하나를 공유한다(8-1절 원칙)."""
    serial_no = (request.args.get("serial_no") or "").strip()
    if not serial_no:
        return "", 400
    png_bytes = report_builder.qr_png_bytes(serial_no)
    return send_file(io.BytesIO(png_bytes), mimetype="image/png")
```

`outbound_batch_excel()` 함수 다음(또는 Task B/C/E가 이미 추가한 라우트들 다음)에 추가:

```python
@app.route("/outbound/batch/<int:batch_id>/qr-labels")
@perm_required("outbound")
def outbound_qr_labels(batch_id):
    batch = db.get_outbound_batch(batch_id)
    if batch is None:
        flash("존재하지 않는 차수야.")
        return redirect(url_for("outbound_history"))
    planned = db.list_planned_items(batch_id)
    if not planned:
        flash("이 차수에 등록된 계획 S/N이 없어. 먼저 차수 입력에서 계획을 등록해줘.")
        return redirect(url_for("outbound_round_edit", batch_id=batch_id))

    rules = db.get_classify_rules()
    categorized = {}
    for p in planned:
        label = db.classify_serial_no(p["serial_no"], rules=rules) or "미분류"
        categorized.setdefault(label, []).append(p["serial_no"])

    buf = report_builder.build_outbound_qr_labels_excel(batch["round_no"], batch["ship_date"], categorized)
    db.record_qr_export(batch_id, g.user["display_name"] or g.user["username"], len(planned))
    record_change("QR 라벨 엑셀 출력", "outbound_batch", batch_id, f"{batch['round_no']} ({len(planned)}건)")

    raw_name = f"{batch['round_no'] or batch_id}_{(batch['ship_date'] or '').replace('-', '')}.xlsx"
    safe = re.sub(r'[\\/:*?"<>|]', '', raw_name)
    return send_file(buf, as_attachment=True, download_name=safe,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/outbound/qr-exports")
@perm_required("outbound")
def outbound_qr_export_history():
    q = request.args.get("q", "").strip()
    exports = db.list_qr_exports(query=q or None)
    return render_template("outbound_qr_history.html", exports=exports, q=q)
```

- [ ] **Step 4: 템플릿** — `templates/outbound_qr_history.html`

```html
{% extends "base.html" %}
{% block title %}QR 라벨 출력 이력 — Chardon QMS{% endblock %}
{% block content %}
<h2>🚚 QR 라벨 출력 이력</h2>

<div class="card">
  <form method="GET" style="display:flex; gap:8px; align-items:flex-end; flex-wrap:wrap;">
    <div style="flex:1; min-width:170px;">
      <label>차수/거래처로 찾기</label>
      <input type="text" name="q" value="{{ q }}">
    </div>
    <button type="submit" style="margin-top:0;">조회</button>
  </form>
</div>

<div class="card">
  <table>
    <thead><tr><th>차수</th><th>거래처</th><th>출고예정일</th><th>생성일시</th><th>생성자</th><th>건수</th><th></th></tr></thead>
    <tbody>
      {% for e in exports %}
      <tr>
        <td>{{ e.round_no or '-' }}</td>
        <td>{{ e.customer or '-' }}</td>
        <td>{{ e.ship_date | date_korean if e.ship_date else '-' }}</td>
        <td>{{ e.generated_at | datetime_korean }}</td>
        <td>{{ e.generated_by or '-' }}</td>
        <td>{{ e.item_count }}</td>
        <td><a href="{{ url_for('outbound_qr_labels', batch_id=e.batch_id) }}">📄 다시 받기</a></td>
      </tr>
      {% else %}
      <tr><td colspan="7" style="text-align:center; color:var(--muted);">출력 이력이 없어.</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: 검증 스크립트**

```python
import app as appmodule, database as db, openpyxl, io as _io

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

r0 = client.get("/outbound/qr?serial_no=TESTQR_1")
assert r0.status_code == 200 and r0.mimetype == "image/png" and len(r0.data) > 100
print("OK: outbound_qr_image가 report_builder.qr_png_bytes 재사용 후에도 정상 동작")

batch_id = db.create_outbound_batch("테스트거래처_QRV2", "2026-09-22", "홍길동", "테스트", round_no="QR3차")
db.add_planned_items_bulk(batch_id, [
    "CKMR7K0902USA5771H(42P)", "CKMR8K0803USA6622HAT3(32P)", "CKCB2758-RA", "GARBAGE-NOMATCH",
])

r1 = client.get(f"/outbound/batch/{batch_id}/qr-labels")
assert r1.status_code == 200
assert r1.mimetype.startswith("application/vnd.openxmlformats")
wb = openpyxl.load_workbook(_io.BytesIO(r1.data))
assert "15kV 일반 수평 (42P)" in wb.sheetnames
assert "27kV 트리플 3핸들 앵글 (32P) 155V" in wb.sheetnames
assert "제어함 - RA" in wb.sheetnames
assert "미분류" in wb.sheetnames
ws = wb["제어함 - RA"]
assert ws["A1"].value == "CKCB2758-RA"
assert len(ws._images) == 1
print("OK: 카테고리별 시트 분리 + QR 이미지 삽입 확인")

exports = db.list_qr_exports(query="QR3차")
assert len(exports) == 1 and exports[0]["item_count"] == 4
print("OK: 출력 이력 기록됨")

r2 = client.get("/outbound/qr-exports")
assert "QR3차" in r2.get_data(as_text=True)
print("OK: outbound_qr_history.html 렌더")

label31 = "가" * 40 + "/이름[테스트]:확인"
sheet1 = __import__("report_builder")._sanitize_outbound_sheet_name(label31, set())
assert len(sheet1) <= 31
assert not any(ch in sheet1 for ch in "\\/?*[]:")
print("OK: 시트명 31자 컷 + 금지문자 제거")

conn = db.get_conn()
conn.execute("DELETE FROM outbound_qr_exports WHERE batch_id=?", (batch_id,))
conn.execute("DELETE FROM outbound_planned_items WHERE batch_id=?", (batch_id,))
conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,))
conn.commit(); conn.close()
print("정리 완료")
```

- [ ] **Step 6: 실제 xlsx 파일을 열어서 육안 확인 (CLAUDE.md 11절)**

위 검증 스크립트가 만든 xlsx를 실제로 디스크에 저장해서(`with open("qr_labels_check.xlsx","wb") as f: f.write(r1.data)`) 엑셀/LibreOffice로 직접 열어, 시트마다 S/N 텍스트와 QR 이미지가 열 경계에 딱 맞게 앉아 있는지, QR을 실제로 스캔했을 때 원래 S/N이 나오는지 확인할 것.

- [ ] **Step 7: quality-watcher 검증 요청**

- [ ] **Step 8: Commit** — 메인 세션이 처리

---

## Task H: 메뉴 등록 + README 갱신 + 전체 end-to-end 검증

**Files:**
- Modify: `templates/base.html` (출고 메뉴에 신규 링크 4개 추가)
- Modify: `README.txt` (출고 관리 섹션 전면 교체 + 메뉴/권한 요약 줄 갱신)

- [ ] **Step 1: `base.html` 메뉴 교체**

기존 "🚚 출고" 메뉴 블록을 아래로 **교체**:

```html
  {# 🚚 출고 #}
  {% if 'outbound' in perms %}
  <div class="nav-dropdown">
    <button type="button" onclick="toggleNav(this)">🚚 출고 ▾</button>
    <div class="menu">
      <a href="{{ url_for('outbound_serial_new') }}">S/N 발급</a>
      <a href="{{ url_for('outbound_rules') }}">분류 규칙 관리</a>
      <a href="{{ url_for('outbound_round_new') }}">차수 입력</a>
      <a href="{{ url_for('outbound_scan_list') }}">출고 스캔</a>
      <a href="{{ url_for('outbound_history') }}">출고 이력</a>
      <a href="{{ url_for('outbound_qr_export_history') }}">QR 출력 이력</a>
    </div>
  </div>
  {% endif %}
```

- [ ] **Step 2: `README.txt` 갱신**

`README.txt`의 기존 `[출고 관리]` 섹션을 아래로 **전면 교체**:

```
[출고 관리]
완제품에 QR라벨(S/N)을 붙여서 출고할 때 스캔으로 기록하는 기능입니다. 입고검사(IQC)와는
완전히 별개의 기능이라 "🚚 출고" 권한이 따로 있어야 메뉴가 보입니다.

  1) S/N 발급 — 생산이 끝난 완제품마다 S/N을 여러 줄 붙여넣으면(엑셀에서 복사해서
     Ctrl+V) 한 번에 발급됩니다. 각 줄 옆에 모델명이 자동으로 미리보기 됩니다(S/N 문자열
     패턴으로 자동 판별 — "분류 규칙 관리"에서 매핑표를 직접 고칠 수 있습니다). 발급 직후
     QR이 화면에 표시되고 "🖨️ 인쇄"로 인쇄해서 제품에 라벨로 붙일 수 있습니다. 같은 S/N은
     두 번 발급되지 않습니다. 발급 이력은 목록에서 삭제할 수 있습니다(S/N 값 자체는 못
     고칩니다 — 이미 다른 출고에 쓰였을 수 있어서 값이 바뀌면 연결이 끊어지기 때문입니다).

  2) 분류 규칙 관리 — S/N 문자열 패턴(전압코드/접미사/P코드 특수값)으로 모델명을 자동
     판별하는 매핑표를 화면에서 추가·수정·삭제할 수 있습니다. 여기서 값을 바꾸면 S/N
     발급·차수 입력·출고 스캔·QR 라벨 출력 전체에 즉시 반영됩니다.

  3) 차수 입력 — 새로운 출고 건(=차수)을 여기서 만듭니다. 차수번호·거래처·출고예정일·
     담당자를 입력하고, 이번 차수에 나갈 예정인 S/N 목록을 미리 붙여넣어 계획해둘 수
     있습니다. 등록 직후 "📄 QR 라벨 엑셀 출력"으로 종류별로 나뉜 QR 라벨 시트를 바로
     받을 수 있고, "🔍 출고 스캔 시작"으로 이어서 스캔을 진행할 수 있습니다.

  4) 출고 스캔 — 차수를 먼저 고른 뒤(새로 만들려면 "차수 입력"에서 먼저 등록), 붙어있는
     QR을 카메라로 비추면 S/N과 모델명이 자동으로 입력됩니다. 카메라가 안 되면 화면 아래
     입력창에 S/N을 직접 타이핑해도 됩니다. 사진을 찍은 뒤 "리스트에 추가"를 누르면 그
     즉시 서버에 저장됩니다(수량 입력칸은 없어졌습니다). 화면 위쪽에 "계획 N개 중 M개
     스캔됨 / 계획외 K건"으로 진행상황이 표시됩니다.
     - "미등록 S/N", "이미 다른 출고에 쓰인 S/N", "계획에 없는 항목" 경고가 떠도 실수가
       아니라면 그냥 진행해도 됩니다(막지 않고 알려주기만 하는 용도입니다).
     - 저장한 뒤에도 그 출고 건을 다시 열어서 항목을 계속 추가하거나, 제품명을 고치거나,
       사진을 지울 수 있습니다.

  5) 출고 이력 — 지금까지 저장된 출고 건(차수) 목록입니다. 거래처를 클릭하면 다시
     스캔/편집할 수 있고, "📊 엑셀"로 기본 표 형태 파일을 받을 수 있습니다(회사 정식
     양식 파일을 주시면 그 모양대로 바꿔드릴 수 있습니다). "확인" 버튼을 누르면 확인자와
     확인일시가 기록됩니다(확인 후에도 계속 수정할 수 있습니다 — 잠금이 아닙니다).

  6) QR 출력 이력 — 언제 누가 어떤 차수의 QR 라벨을 몇 건 출력했는지 기록된 목록입니다.
     "📄 다시 받기"로 언제든 같은 차수의 QR 라벨을 다시 받을 수 있습니다(그 시점의 최신
     분류 규칙 기준으로 다시 만들어집니다).
```

그리고 `README.txt` 상단 메뉴/권한 요약 두 줄도 갱신:

- `  [출고]  출고(S/N 발급/스캔/이력) 관리` → `  [출고]  출고(S/N 발급/분류규칙/차수입력/스캔/이력/QR출력이력) 관리`
- `  ✅ 승인 / 🚚 출고(S/N 발급·출고 스캔·출고 이력) / 🖨️ 출력...` → `  ✅ 승인 / 🚚 출고(S/N 발급·분류규칙관리·차수입력·출고 스캔·출고 이력·QR출력이력) / 🖨️ 출력...`

- [ ] **Step 3: 메뉴 검증 스크립트**

```python
import app as appmodule, database as db

client = appmodule.app.test_client()
client.post("/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)
r = client.get("/")
body = r.get_data(as_text=True)
for label in ["S/N 발급", "분류 규칙 관리", "차수 입력", "출고 스캔", "출고 이력", "QR 출력 이력"]:
    assert label in body, f"메뉴 누락: {label}"
print("OK: 출고 메뉴 6개 전부 노출")

uid = db.create_user("outbound_test_user_v2", "pw1234", "테스트유저", "")
client2 = appmodule.app.test_client()
client2.post("/login", data={"username": "outbound_test_user_v2", "password": "pw1234"}, follow_redirects=True)
r2 = client2.get("/")
assert "🚚 출고" not in r2.get_data(as_text=True)
for path in ["/outbound/rules", "/outbound/round/new", "/outbound/scan", "/outbound/qr-exports"]:
    rr = client2.get(path)
    assert rr.status_code in (302, 200)
    assert path not in rr.headers.get("Location", "")
print("OK: 권한 없는 계정은 신규 라우트 전부 차단됨")

conn = db.get_conn(); conn.execute("DELETE FROM users WHERE username='outbound_test_user_v2'"); conn.commit(); conn.close()
```

- [ ] **Step 4: 전체 흐름 수동 검증 (CLAUDE.md 10절/11절)**

1. admin으로 로그인 → "🚚 출고" 메뉴에 6개 항목이 모두 보이는지 확인
2. "분류 규칙 관리"에서 seed된 기본값(전압 7/8/9, 접미사 HAT1~S, P코드 32)이 보이는지 확인, 임의로 하나 추가/수정/삭제해보고 반영되는지 확인
3. "S/N 발급"에서 `CKMR7K0902USA5771H(42P)` 등 실제 패턴의 S/N 3~4개를 붙여넣기로 한 번에 발급 → 각 줄 옆 미리보기가 올바른 모델명을 보여주는지, 발급 후 QR 이미지들과 인쇄 화면이 뜨는지 확인
4. 발급 이력에서 방금 만든 항목 하나를 삭제해보고 목록에서 사라지는지 확인
5. "차수 입력"에서 새 차수(예: "1차")를 등록하면서 위에서 발급한 S/N 중 일부 + 아직 발급 안 한 S/N도 하나 섞어서 계획 목록에 붙여넣기 → 저장 후 "QR 라벨 엑셀 출력" 눌러서 실제로 다운로드되는 xlsx를 열어 카테고리별 시트와 QR+텍스트 배치가 올바른지 확인(QR을 실제 스캔해서 원래 S/N이 나오는지까지)
6. 같은 화면에서 "출고 스캔 시작" → 계획 대비 진행상황이 "계획 N개 중 0개"로 뜨는지 확인
7. 카메라 권한을 거부하고 수동 입력창으로 계획에 포함된 S/N 하나를 입력 → 경고 없이(또는 "발급 이력에 없는 S/N"만) 제품명이 자동으로 채워지는지 확인 → "리스트에 추가" → 페이지 새로고침 없이 표에 바로 반영되고 "계획 N개 중 1개 스캔됨"으로 즉시 바뀌는지 확인
8. 계획에 없는 임의의 S/N을 스캔/입력 → "계획에 없는 항목이 스캔됐어" 경고가 뜨는지, 그래도 추가되는지, "계획외 1건"으로 카운트가 올라가는지 확인
9. 저장된 항목 중 하나의 제품명을 인라인 수정 → 저장되는지, 사진 1장을 삭제 → 화면과 실제 파일(`outbound_photos/`)에서 사라지는지 확인
10. 배치 헤더의 거래처/차수를 수정 → 저장되는지 확인
11. "출고 확인" 버튼을 누르고 확인자/확인일시가 기록되는지, 그 이후에도 항목을 계속 추가/수정할 수 있는지(잠기지 않는지) 확인
12. "출고 이력"에서 방금 만든 차수가 차수번호/확인상태와 함께 보이는지, "📊 엑셀" 결과가 정상인지 확인
13. "QR 출력 이력"에서 방금 만든 차수의 출력 기록이 보이는지, "📄 다시 받기"가 정상 동작하는지 확인
14. `outbound` 권한이 없는 일반 계정으로 로그인해서 "출고" 메뉴 자체가 안 보이고, 새로 만든 URL(`/outbound/rules`, `/outbound/round/new`, `/outbound/scan`, `/outbound/qr-exports` 등)에 직접 접속해도 차단되는지 확인
15. (Android 태블릿 실기기가 있으면) 실기기 Chrome에서 카메라 스캔 → "리스트에 추가"를 연속으로 여러 번 눌러도 카메라 화면이 매번 재시작되지 않고 계속 유지되는지 확인 — 이번 재작성의 핵심 UX 목표

- [ ] **Step 5: quality-watcher 최종 검증 요청**

Task A~H에서 수정/신규 생성된 파일 전부(`database.py`, `app.py`, `report_builder.py`, `templates/outbound_serial.html`, `templates/outbound_scan.html`, `templates/outbound_scan_list.html`, `templates/outbound_round.html`, `templates/outbound_rules.html`, `templates/outbound_qr_history.html`, `templates/outbound_history.html`, `templates/_serial_paste_grid.html`, `templates/base.html`, `README.txt`)를 대상으로 quality-watcher에게 검증을 요청한다. "결함 있음"이면 보고 전에 고치고, "확인 필요"는 사용자에게 그대로 전달한다.

- [ ] **Step 6: Commit** — 메인 세션이 처리

---

## 파일 경로 요약

- `database.py` — 신규 테이블 5개(`outbound_rule_voltage`/`outbound_rule_suffix`/`outbound_rule_pcode`/`outbound_planned_items`/`outbound_qr_exports`) + `outbound_batches` 컬럼 3개(`round_no`/`confirmed_by`/`confirmed_at`) + 함수 신규/수정 20개
- `report_builder.py` — `qr_png_bytes()`/`_sanitize_outbound_sheet_name()`/`build_outbound_qr_labels_excel()` 신규
- `app.py` — `ensure_outbound_rules_seed_20260915()` 신규 + 라우트 신규 12개 + 기존 라우트 수정 5개 + `_outbound_scan_submit()` 삭제
- `templates/_serial_paste_grid.html` — 신규(S/N 발급·차수 입력 공용 붙여넣기 그리드 파셜)
- `templates/outbound_serial.html` — 전면 재작성(붙여넣기 그리드 + 벌크 QR 미리보기)
- `templates/outbound_rules.html` — 신규(분류 규칙 관리)
- `templates/outbound_round.html` — 신규(차수 입력/수정)
- `templates/outbound_scan_list.html` — 신규(차수 선택)
- `templates/outbound_scan.html` — 전면 재작성(즉시저장 아키텍처, 계획대조, 수량칸 제거)
- `templates/outbound_qr_history.html` — 신규(QR 출력 이력)
- `templates/outbound_history.html` — 컬럼 추가(차수/확인)
- `templates/base.html` — "🚚 출고" 메뉴 6개 항목으로 확장
- `README.txt` — "출고 관리" 섹션 전면 교체 + 메뉴/권한 요약 2줄 갱신

**최종 실행 순서**: **A → B → C → D → E → G → F → H** (Task E의 템플릿이 Task G의 라우트를 참조하므로 G를 F보다 앞당김). 각 태스크마다 검증 스크립트를 실제로 돌려 통과를 확인한 뒤, 신규 템플릿이 생기는 태스크마다, 그리고 최종적으로 Task H에서 quality-watcher 검증을 받는다. developer는 git commit을 하지 않는다(메인 세션이 검토 후 커밋).
