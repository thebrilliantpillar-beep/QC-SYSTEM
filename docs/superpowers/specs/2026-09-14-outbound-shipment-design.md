# 출고(완제품 S/N·QR·사진) 관리 기능 — 설계 문서

## 배경 / 목적

완제품(finished goods) 출고 과정을 QR 기반으로 기록한다. 지금 iqc-app은 입고 자재
검사(IQC)만 다루고, 출고 관련 기능은 전혀 없다 — 이번이 처음 추가되는 완전히 새로운
하위시스템이다.

흐름 요약:
1. **생산 완료 직후** — 완제품에 부여할 S/N을 사람이 직접 입력하면, 시스템이 QR코드로
   변환해서 화면에 보여주고 인쇄한다. 그 QR을 인쇄해서 제품에 라벨로 붙인다.
2. **출고 시점** — 붙어있는 QR을 카메라로 스캔하면 S/N이 자동으로 입력란에 채워진다.
   제품명/모델명·수량을 입력하고 사진을 여러 장 찍어 붙인 뒤 "리스트에 추가"를 눌러
   화면에 쌓아둔다. 오늘 한 거래처에 나갈 항목을 이렇게 여러 개 쌓은 뒤 "저장"하면
   출고 배치(한 건) 하나로 확정된다. 확정 후에도 항목 추가·수정이 가능하다.
3. **출고 이력** — 저장된 출고 배치 목록을 보고, 각 배치를 엑셀로 출력할 수 있다.
   엑셀 양식은 사용자가 추후 별도로 제공할 예정 — 그 전까지는 기본 표 형태로 출력한다.

## 확정된 결정 사항 (브레인스토밍 세션에서 확인 완료)

| 항목 | 결정 |
|---|---|
| S/N 값 | 사람이 직접 입력(자동 채번 아님). 형식 규칙은 이미 있으나 이번 설계 범위 밖(추후 별도 논의) |
| S/N 발급 시점 | 생산 완료 직후, 미리 발급해서 라벨로 인쇄·부착 |
| S/N-제품 연결 | 발급 시점엔 제품 정보를 연결하지 않는다. S/N 자체만 있으면 됨 |
| S/N 발급 이력 | DB에 남긴다(발급자·발급일시) |
| S/N 중복 발급 | 이미 발급된 S/N은 재발급을 아예 막는다(UNIQUE 제약 + 에러 안내) |
| 출고 스캔 시 자동입력 항목 | S/N만(QR엔 S/N 문자열만 들어있음). 제품명/모델명·거래처/고객사·수량·출고일/담당자는 사람이 입력 |
| 거래처/고객사 | 출고 배치(한 건) 전체에 하나만 — 포장리스트처럼 배치 헤더에 귀속, 항목마다 다르지 않음 |
| 사진 | 항목(S/N)당 여러 장 가능(NCR 부적합 사진첨부와 같은 방식 재사용) |
| 리스트 단위 | 출고 건(배치) 단위로 구분 — 한 배치 = 한 거래처에 보내는 여러 항목 묶음 |
| 확정 후 수정 | 가능해야 함 — 성적서처럼 확정되면 잠기는 방식이 아니라, 나중에 항목 추가·수정 가능 |
| 이미 다른 배치에 쓰인 S/N을 또 스캔 | 막지 않고 경고만 표시("이미 다른 출고에 쓰인 S/N이야") |
| 발급 이력에 없는 S/N을 스캔(미등록) | 위와 같은 원칙 적용 — 경고만 하고 계속 진행 허용 (이번 설계에서 기본값으로 정함) |
| 엑셀 출력 | 사용자가 추후 실제 양식 파일 제공 예정. 그 전까지는 기본 표 형태 xlsx로 출력, 파일 받으면 report_builder.py의 기존 템플릿-복사 패턴(NCR/성적서와 동일)으로 재작업 |
| 메뉴 위치 | 최상단에 새 메뉴 "출고" 신설(입고/검사/승인과 같은 레벨) |
| 권한 | 새 권한 `outbound` 신설, 3개 화면(S/N 발급/출고 스캔/출고 이력) 전부 이 권한 하나로 게이트 |

## 아키텍처

### 새 DB 테이블 4개 (`database.py`의 `init_db()` 안에 직접 추가 — 최근 관례, `change_points` 참고)

```sql
CREATE TABLE IF NOT EXISTS finished_goods_serials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    serial_no   TEXT NOT NULL UNIQUE,
    issued_by   TEXT,
    issued_at   TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS outbound_batches (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    customer      TEXT,
    ship_date     TEXT,
    handler       TEXT,           -- 담당자(기본값: 로그인한 사람, 수정 가능)
    created_by    TEXT,
    created_at    TEXT DEFAULT (datetime('now','localtime')),
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS outbound_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id        INTEGER NOT NULL REFERENCES outbound_batches(id),
    serial_no       TEXT NOT NULL,
    product_name    TEXT,
    quantity        INTEGER,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS outbound_item_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id     INTEGER NOT NULL REFERENCES outbound_items(id),
    file_path   TEXT NOT NULL,
    uploaded_at TEXT DEFAULT (datetime('now','localtime'))
);
```

- `finished_goods_serials`와 `outbound_items.serial_no`는 **느슨하게 연결**한다(FK 없음,
  문자열 비교) — 미등록 S/N도 경고만 하고 출고 항목으로 받아들여야 하기 때문에
  FK로 강제하면 안 됨. `material_bom_links`가 `materials`에 FK 없이 느슨하게 연결하는
  기존 관례(18절)와 같은 방식.
- 사진은 `ncr_photos/`처럼 별도 디렉터리(`outbound_photos/`)에 저장하고 경로만 DB에 남긴다.

### 화면 3개

1. **S/N 발급** (`/outbound/serial/new`, GET+POST)
   - S/N 입력창 → "QR 생성" → 화면에 QR 이미지 표시(서버에서 `qrcode` 파이썬 라이브러리로
     생성하거나, 클라이언트 JS QR 생성 라이브러리 사용 — 구현 단계에서 결정) + 인쇄 버튼
     (도면 인쇄와 같은 패턴: 숨긴 iframe + `window.print()`)
   - 저장 시 `finished_goods_serials`에 INSERT, UNIQUE 위반이면 "이미 발급된 S/N이야"
     에러로 되돌림(발급 자체를 막음 — 확정된 규칙)
   - 발급 이력 조회(간단한 목록)도 이 화면 하단이나 별도 탭에 둘 수 있음(구현 시 결정)

2. **출고 스캔** (`/outbound/scan`, 신규 배치 시작 시 `/outbound/scan/new`, 기존 배치
   이어서 편집 시 `/outbound/scan/<batch_id>`)
   - 상단: 배치 헤더 폼(거래처, 출고일, 담당자 — 기존 배치 편집 시엔 기존 값으로 채워짐)
   - QR 스캔 영역: 카메라 스트림 + QR 디코딩(JS 라이브러리, 예: html5-qrcode — pdf.js를
     이미 CDN으로 쓰는 선례와 같은 방식). 카메라를 못 쓰는 상황 대비 **수동 S/N 입력
     폴백**도 같이 제공(텍스트 입력창).
   - 스캔/입력된 S/N을 `finished_goods_serials`·같은 배치 안 중복과 대조:
     - 미등록 S/N → 경고 배너만 표시, 진행은 허용
     - 다른 배치에 이미 쓰인 S/N → 경고 배너만 표시("이미 다른 출고에 쓰였어"), 진행 허용
   - 제품명/모델명·수량 입력 + 사진 여러 장 촬영/첨부(NCR의 "📸 카메라로 촬영"/
     "🖼️ 갤러리에서 선택" 패턴 재사용) → "리스트에 추가"
   - 화면에 누적되는 항목 목록(추가 취소도 가능) → "저장" 누르면 서버에 배치 헤더+
     항목+사진 한 번에 제출
   - 기존 배치를 다시 열면 이미 저장된 항목들이 목록에 나오고, 여기에 새 항목을 계속
     추가하거나 기존 항목을 수정/삭제할 수 있음(확정돼도 안 잠김 — 확정된 규칙)

3. **출고 이력** (`/outbound/history`)
   - 배치 목록(거래처/출고일/담당자/항목수), 클릭 시 상세(=출고 스캔 화면의 편집 모드로
     이동) 또는 읽기전용 상세보기
   - "엑셀 출력" 버튼 → 그 배치 하나를 xlsx로 생성(현재는 기본 표, 추후 사용자 제공
     템플릿으로 교체)

### 권한

`app.py`의 `PERM_GROUPS`에 새 그룹(또는 기존 그룹에 항목 추가) — 권한명 `outbound` 하나
신설. 3개 라우트 전부 `@perm_required("outbound")`.

### 메뉴

`base.html`의 상단 드롭다운 메뉴에 "출고" 신설(입고/검사/승인과 같은 레벨), 하위 항목:
S/N 발급 / 출고 스캔(새 배치 시작) / 출고 이력.

## 범위 밖 (이번엔 안 함, 필요하면 나중에 별도로)

- S/N 자동 채번(형식 규칙 설계) — 사용자가 "나중에 얘기하자"고 명시적으로 보류함
- 실제 엑셀 양식 매핑 — 파일 받은 후 별도 작업
- 출고 배치의 승인/서명 같은 결재 흐름 — 요청 없었음, IQC 쪽 승인 체계와는 무관한 별개
  기능이라 가정
- S/N 발급 화면에서 여러 개 한 번에 발급(배치 발급) — 이번엔 한 번에 하나씩만. 필요해지면
  나중에 추가(YAGNI)

## 사용자 확인이 아직 안 된 것 (구현 전 최종 확인 권장)

- 미등록 S/N을 스캔했을 때 "경고만 하고 허용"으로 이번 설계에서 기본값으로 정함 —
  이미 확정된 "이미 쓰인 S/N" 케이스와 원칙을 통일한 것. 다르게 하고 싶으면 알려줄 것.
- QR 생성 방식(서버 파이썬 `qrcode` 라이브러리 vs 클라이언트 JS 라이브러리)은 구현 단계
  세부사항이라 이 설계 문서에서 확정하지 않음 — developer가 구현 시 더 간단한 쪽으로 결정.
