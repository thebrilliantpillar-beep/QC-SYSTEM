# -*- coding: utf-8 -*-
# Copyright (c) 2026 윤주호. All rights reserved.
# 무단 복제·배포·수정을 금합니다.
"""DB 초기화 및 데이터 접근 — SQLite (파일 하나 = DB 전체)"""
import re, sqlite3, os
from datetime import datetime as _dt

# 검사자 비고의 표준 불량 문구 파싱 — app.py의 parse_defect_counts와 동일 패턴
_DEFECT_RE = re.compile(r"검사\s*수량\s*(\d+)\s*개\s*중\s*(\d+)\s*개\s*불량")

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(__file__))
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "iqc.db")

# ---------- 2026-09-20 확장: 출고 항목 품질확인 9종 + 전체판정 ----------
# 사용자가 준 실제 회사 "출고 내역서" 서식 헤더 순서 그대로. 배치(차수) 단위가 아니라
# 항목(S/N, outbound_items 한 행) 단위다 — 실제 첨부 엑셀을 openpyxl로 직접 읽어서 확인함.
# 2026-09-20: 5종 → 9종으로 확장 (스티커/도장/케이블타이/인디케이터 추가, 항목명 일부 변경)
OUTBOUND_CHECK_FIELDS = [
    "check_tie", "check_qr", "check_wrap", "check_rst",
    "check_sticker", "check_paint", "check_access",
    "check_cable", "check_indicator",
]
OUTBOUND_CHECK_LABELS = {
    "check_tie": "볼트/너트 체결 상태 (가대/탱크 다리)",
    "check_qr": "QR 번호 부착 리셉터클 캡 결합",
    "check_wrap": "하우징 커버 포장 상태",
    "check_rst": "RST단자 나무판 결착",
    "check_sticker": "스티커 부착 상태",
    "check_paint": "도장 상태",
    "check_access": "부속품 유무 확인",
    "check_cable": "케이블 타이 식별 (155V)",
    "check_indicator": "INDICATOR 상태 확인",
}
# 2026-09-20: check_cable은 "해당없음" 포함 4종, 나머지는 3종.
# "해당없음"은 FAIL/SPECIAL이 아니어서 자동판정에서 PASS와 동일하게 취급됨.
OUTBOUND_RESULT_VALUES = ("PASS", "FAIL", "SPECIAL", "해당없음")
# 필드별로 실제 허용되는 값(위 OUTBOUND_RESULT_VALUES는 "존재하는 값 전체"일 뿐,
# 필드마다 어떤 값이 유효한지는 이걸로 걸러야 한다 — check_cable만 "해당없음"을 받고
# SPECIAL은 못 받는다, 나머지는 반대. 2026-09-20 quality-watcher가 이 검증 누락을
# HIGH로 지적: 이게 없으면 API를 직접 호출해 check_tie="해당없음" 같은 값을 넣을 수 있었다.
OUTBOUND_FIELD_ALLOWED_VALUES = {
    f: ("PASS", "FAIL", "해당없음") if f == "check_cable" else ("PASS", "FAIL", "SPECIAL")
    for f in OUTBOUND_CHECK_FIELDS
}
# 2026-09-16: "새 항목 스캔·입력" 카드 안 좁은 폭에 넣을 축약 라벨. 정식 명칭은
# OUTBOUND_CHECK_LABELS(표 헤더·엑셀 출력용)를 계속 쓰고, 이건 카드 UI 전용 —
# 화면에선 이 짧은 텍스트를 쓰고 title 속성에 정식 명칭을 붙여 보완한다.
OUTBOUND_CHECK_SHORT_LABELS = {
    "check_tie": "체결상태",
    "check_qr": "QR 부착",
    "check_wrap": "커버포장",
    "check_rst": "RST단자",
    "check_sticker": "스티커",
    "check_paint": "도장상태",
    "check_access": "부속품",
    "check_cable": "케이블타이",
    "check_indicator": "인디케이터",
}
# 2026-09-20: 사용자가 준 실제 "출고 패킹리스트" 참고파일의 6행("검사 기준") 문구
# 그대로 — 출고 스캔 화면(새 항목 입력 카드)과 report_builder.build_outbound_excel()의
# 6행이 둘 다 이 딕셔너리 하나를 쓴다(8-1절 공용헬퍼 원칙, 문구를 두 곳에 복붙하지 않음).
OUTBOUND_CHECK_CRITERIA = {
    "check_tie": "각각 해당 위치에 체결상태 확인",
    "check_qr": "탱크에 S/N 및 QR 번호 부착 및 리셉터클 캡 장착 여부",
    "check_wrap": "커버에 하우징이 노출이 되는지 확인",
    "check_rst": "볼트가 단자에 잘 고정 되었는지",
    "check_sticker": "정격표시/ 배큠표시/오픈락/골든이글",
    "check_paint": "도장 벗겨짐/파임/오염",
    "check_access": "부속품 유무",
    "check_cable": "LIFT RING 에 노란색 케이블 타이",
    "check_indicator": "투입상태(빨강)확인",
}


def compute_outbound_item_auto_result(item):
    """item: dict(아홉 개 check_* 키 포함 — sqlite3.Row면 호출 전에 dict()로 바꿀 것).
    9개 전부 값이 있어야 계산하고, 하나라도 비어있으면(None, 아직 안 눌러봄) None을
    돌려준다("미검사").

    "해당없음"은 OUTBOUND_RESULT_VALUES에 포함돼 있어 미검사로 안 잡히고,
    FAIL/SPECIAL이 아니므로 자동판정에서 PASS와 동일하게 취급된다.

    우선순위: FAIL이 하나라도 있으면 무조건 FAIL(SPECIAL이 섞여 있어도 FAIL이 이긴다) →
    그 다음 SPECIAL이 하나라도 있으면 SPECIAL → 전부 PASS/해당없음이면 PASS.
    (2026-09-16, 사용자가 명시적으로 확정: "Special이 있어도 Fail이 들어가면 무조건
    최종 판결은 Fail" — 이 순서를 절대 바꾸지 말 것.)"""
    vals = [item.get(f) for f in OUTBOUND_CHECK_FIELDS]
    if any(v not in OUTBOUND_RESULT_VALUES for v in vals):
        return None
    if "FAIL" in vals:
        return "FAIL"
    if "SPECIAL" in vals:
        return "SPECIAL"
    return "PASS"


def outbound_item_effective_result(item):
    """수동 오버라이드가 있으면 그 값, 없으면 자동판정 그대로.
    inspection_items.override_result / app.py의 effective_result() 패턴과 동일 설계
    (CLAUDE.md 관례 — 원본 자동판정은 안 지우고 오버라이드만 별도 저장)."""
    return item.get("result_override") or compute_outbound_item_auto_result(item)
# ---------- 확장 끝 ----------

def get_ma_by_component(component_no):
    """파츠 자재번호 -> 그 파츠가 속한 MA와 그 MA의 파츠 전체를 반환.

    입고 화면에서 파츠 하나를 입력하면 같은 MA의 나머지 파츠까지 한 번에 펼치기 위한 함수.
    반환: {"ma_master": MA명, "components": [자재번호, ...]} 또는 None(= 일반 자재)

    (자재번호 하나가 여러 MA에 걸쳐 있으면 어느 MA로 펼칠지 알 수 없으므로 None을 돌려
     일반 자재로 처리한다 — 잘못된 MA로 8줄이 튀어나오는 것보다 안전하다.)
    """
    if not component_no:
        return None

    conn = get_conn()
    try:
        masters = conn.execute(
            "SELECT DISTINCT assembly_id FROM assembly_components WHERE component_no=?",
            (component_no,),
        ).fetchall()
        if len(masters) != 1:
            return None

        assembly_id = masters[0]["assembly_id"]
        master = conn.execute(
            "SELECT assembly_no FROM assembly_masters WHERE id=?", (assembly_id,)
        ).fetchone()
        if not master:
            return None

        components = [
            r["component_no"]
            for r in conn.execute(
                "SELECT component_no FROM assembly_components WHERE assembly_id=? ORDER BY component_order",
                (assembly_id,),
            ).fetchall()
        ]
        if not components:
            return None
        return {"ma_master": master["assembly_no"], "components": components}
    finally:
        conn.close()


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """테이블이 없으면 생성. 있으면 그대로 둠(재실행 안전)."""
    conn = get_conn()
    cur = conn.cursor()

    # 0. 자재 마스터 — 항목(규격)이 아직 하나도 없어도 자재 자체는 존재할 수 있게 별도 테이블로 관리
    #    (규격 개별 등록에서 자재번호+제품명만 먼저 등록하고 항목은 나중에 추가하는 경우 대응)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS materials (
            material_no TEXT PRIMARY KEY,
            material_name TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    existing_material_cols = [row[1] for row in cur.execute("PRAGMA table_info(materials)").fetchall()]
    # 기준서(SAM 양식) 정보 — 도면번호는 자재번호에서 자동 계산되므로 저장 안 함(A+P→- 규칙)
    if "drawing_version" not in existing_material_cols:
        cur.execute("ALTER TABLE materials ADD COLUMN drawing_version TEXT DEFAULT '1'")
    if "revision_date" not in existing_material_cols:
        cur.execute("ALTER TABLE materials ADD COLUMN revision_date TEXT")
    if "edition" not in existing_material_cols:
        cur.execute("ALTER TABLE materials ADD COLUMN edition INTEGER DEFAULT 1")
    if "unit" not in existing_material_cols:
        cur.execute("ALTER TABLE materials ADD COLUMN unit TEXT DEFAULT 'mm'")
    if "drawing_file" not in existing_material_cols:
        cur.execute("ALTER TABLE materials ADD COLUMN drawing_file TEXT")
    # 커스텀(자유양식) 성적서 — 이 자재에 지정된 템플릿 id. NULL이면 기본(xlsx) 양식 사용
    if "custom_template_id" not in existing_material_cols:
        cur.execute("ALTER TABLE materials ADD COLUMN custom_template_id INTEGER")
    # 전수검사 — NULL이면 전수검사 없음. 열 정의는 JSON {"note":"...", "columns":[...]}
    if "full_inspect_config" not in existing_material_cols:
        cur.execute("ALTER TABLE materials ADD COLUMN full_inspect_config TEXT DEFAULT NULL")
    # 자재 분류(어셈블리/하우징/PCB 등) — FK 없이 문자열 그대로 저장(intake_list.supplier와 동일 관례)
    if "category" not in existing_material_cols:
        cur.execute("ALTER TABLE materials ADD COLUMN category TEXT")

    # 0-0-1. 자재 분류 마스터 — suppliers/gauges와 같은 독립 마스터 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS material_categories (
            name TEXT PRIMARY KEY,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 0-0-2. 불량 유형 마스터 — material_categories와 동일한 관례(name PK, FK 없이
    # ncr.defect_type에 문자열로만 저장). 2026-09-08 신설.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS defect_types (
            name TEXT PRIMARY KEY,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 0-1. 커스텀 성적서 템플릿 — 드래그앤드롭 디자이너로 만든 자유 배치 양식
    #      layout_json = 요소 배열 [{kind,x,y,w,h,field?,text?,size,bold,align}, ...]
    #      좌표는 캔버스 기준 px(canvas_w × canvas_h). 출력 시 reportlab로 PDF 직접 그림.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS custom_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            page_size TEXT DEFAULT 'A4',
            orientation TEXT DEFAULT 'portrait',   -- portrait / landscape
            canvas_w INTEGER DEFAULT 495,
            canvas_h INTEGER DEFAULT 700,
            layout_json TEXT DEFAULT '[]',
            created_by TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 1. 규격표 — 자재별 검사항목 하한/상한 (또는 육안판정)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_no TEXT NOT NULL,
            material_name TEXT,
            item_name TEXT NOT NULL,
            spec_display TEXT,          -- 화면 표기용 (예: "108.5 ± 0.8")
            judge_type TEXT NOT NULL DEFAULT 'numeric',  -- 'numeric' or 'visual'
            lower_limit REAL,
            upper_limit REAL,
            inspect_method TEXT,
            aql TEXT,
            item_order INTEGER DEFAULT 0
        )
    """)

    # 기존 specs 테이블에만 있던 자재들을 materials 테이블로 1회 백필 (재실행 안전 — INSERT OR IGNORE)
    cur.execute("""
        INSERT OR IGNORE INTO materials (material_no, material_name)
        SELECT DISTINCT material_no, material_name FROM specs
    """)

    # 1-1. 입고 리스트 — 엑셀에서 붙여넣은 입고 건 (검사 전 대기 상태)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS intake_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_no TEXT NOT NULL,
            quantity INTEGER,
            supplier TEXT,
            receive_date TEXT,
            po_number TEXT,
            status TEXT NOT NULL DEFAULT '대기',   -- 대기 / 검사완료
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    existing_intake_cols = [row[1] for row in cur.execute("PRAGMA table_info(intake_list)").fetchall()]
    if "product_name" not in existing_intake_cols:
        # 제품명 — 입고리스트 표시 전용 필드. 규격표에 등록된 자재명과는 별개(자동 연동 안 함)
        cur.execute("ALTER TABLE intake_list ADD COLUMN product_name TEXT")
    if "assembly_no" not in existing_intake_cols:
        # 이 행이 조립품(MA) 파츠 자동전개로 생성됐으면 그 MA번호. 일반 입고는 NULL.
        # (예전엔 product_name에 " - "가 들어있는지로 MA 파츠 여부를 추측했는데, 우연히 제품명에
        #  " - "가 들어간 일반 자재까지 "MA 파츠"로 잘못 표시되는 오판정이 있었다 — 이제 이 컬럼으로 실제 출처를 기록한다.)
        cur.execute("ALTER TABLE intake_list ADD COLUMN assembly_no TEXT")
    if "is_priority" not in existing_intake_cols:
        # 우선검사 플래그 — 입고 등록 화면에서 체크하면 검사 대기 목록 상단에 강조 표시된다.
        # 검사완료 후엔 의미 없음(대기 상태에서만 씀), 다른 화면엔 노출 안 함(스펙 확정 사항).
        cur.execute("ALTER TABLE intake_list ADD COLUMN is_priority INTEGER NOT NULL DEFAULT 0")

    # 1-2. 과거 입고 이력 — 일일보고 엑셀 등에서 옮겨온 "이미 끝난" 입고 기록 조회 전용.
    # intake_list와 절대 혼동하지 말 것: 여기 등록해도 검사 대기 큐(상태='대기')에 안 뜨고
    # 품질현황(quality_report)에도 안 잡힌다 — 검사/승인 워크플로우와 완전히 무관한 순수 로그다.
    # FK를 일부러 안 건다(materials/intake_list/inspections 어디와도 조인 강제 없음).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS intake_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_no TEXT NOT NULL,
            product_name TEXT,
            supplier TEXT,
            receive_date TEXT,      -- 가능하면 'YYYY-MM-DD'로 정규화해서 저장(정렬/기간검색 위해)
            po_number TEXT,
            quantity INTEGER,
            source TEXT,            -- 이 행이 어느 등록(파일+시각)에서 왔는지 — 배치 단위 되돌리기 키로도 씀
            imported_by TEXT,       -- 등록한 계정의 username
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_intake_history_material ON intake_history(material_no)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_intake_history_supplier ON intake_history(supplier)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_intake_history_date ON intake_history(receive_date)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_intake_history_source ON intake_history(source)")

    # 2. 검사(성적서) 헤더 — 자재 입고 1건 = 성적서 1건
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            intake_id INTEGER,
            material_no TEXT NOT NULL,
            material_name TEXT,
            supplier TEXT,
            po_number TEXT,
            receive_date TEXT,
            inspect_date TEXT,
            inspector TEXT,
            quantity INTEGER,
            overall_result TEXT,        -- '합격' / '불합격'
            status TEXT NOT NULL DEFAULT 'pending',  -- pending / approved / rejected
            approver TEXT,
            approved_at TEXT,
            signature_path TEXT,
            pdf_path TEXT,
            reject_reason TEXT,
            est_time_label TEXT,        -- 실제 측정 시간 ("N시간 N분 N초") — 태블릿 스톱워치로 측정
            actual_time_sec INTEGER,    -- 실제 측정 시간(초) — 원본값. est_time_label은 이 값을 표시용으로 포맷한 것
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 3. 검사 결과 — 성적서 항목별 측정값·판정
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inspection_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspection_id INTEGER NOT NULL,
            item_name TEXT NOT NULL,
            measured_value TEXT,        -- 수치 또는 O/X, 콤마로 여러 샘플 가능
            max_value REAL,
            min_value REAL,
            result TEXT,                -- '합격' / '불합격'
            gauge_expiry TEXT,          -- 계측기 유효기간 (항목별, YYYY-MM-DD)
            FOREIGN KEY (inspection_id) REFERENCES inspections(id)
        )
    """)

    # 기존 DB에 gauge_expiry 컬럼이 없으면 추가 (재실행 안전 마이그레이션)
    existing_cols = [row[1] for row in cur.execute("PRAGMA table_info(inspection_items)").fetchall()]
    if "gauge_expiry" not in existing_cols:
        cur.execute("ALTER TABLE inspection_items ADD COLUMN gauge_expiry TEXT")
    if "gauge_name" not in existing_cols:
        cur.execute("ALTER TABLE inspection_items ADD COLUMN gauge_name TEXT")
    if "part_material_no" not in existing_cols:
        # 조립품 그룹 검사일 때, 이 측정값이 실제로 어느 부품(자재)에 속하는지 기록.
        # 일반(단일 자재) 검사는 항상 헤더의 material_no와 동일하게 채움.
        cur.execute("ALTER TABLE inspection_items ADD COLUMN part_material_no TEXT")

    existing_insp_cols = [row[1] for row in cur.execute("PRAGMA table_info(inspections)").fetchall()]
    if "est_time_label" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN est_time_label TEXT")
    if "actual_time_sec" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN actual_time_sec INTEGER")
    if "created_by_user_id" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN created_by_user_id INTEGER")
    # 비고란 — 검사자/중간관리자/최종결정권자 3명이 각자 따로 작성 (역할별 색상 구분해서 성적서에도 반영)
    if "remark_inspector" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN remark_inspector TEXT")
    if "remark_manager" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN remark_manager TEXT")
    if "remark_approver" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN remark_approver TEXT")
    # 특채 승인 여부 — 'normal'(일반 합/불 판정 그대로) / 'special'(규격 벗어나도 특채로 승인)
    if "approval_type" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN approval_type TEXT DEFAULT 'normal'")
    if "total_time_sec" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN total_time_sec INTEGER")
    # 위변조 검증 — 승인 시점의 성적서 내용을 해시로 굳혀둔다.
    #   content_hash: DB에 저장된 판정 내용(헤더+항목)의 해시. 나중에 값이 바뀌면 불일치로 잡힌다.
    #   pdf_hash    : 발행된 PDF 파일 자체의 해시. 파일이 덮어써지면 불일치로 잡힌다.
    #   ※ 해시를 넣은 시점 이후 승인분만 보호된다(소급 불가).
    if "content_hash" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN content_hash TEXT")
    if "pdf_hash" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN pdf_hash TEXT")
    if "ncr_waived" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN ncr_waived INTEGER DEFAULT 0")
    if "ncr_waived_reason" not in existing_insp_cols:
        cur.execute("ALTER TABLE inspections ADD COLUMN ncr_waived_reason TEXT")

    # 4. 사용자 계정 — 고정 역할 대신 개별 권한(콤마구분 텍스트)을 admin이 하나하나 부여/회수
    # 권한 종류: intake(입고리스트) / spec(규격관리) / inspect(검사입력,본인것만수정) /
    #           inspect_all(타인 성적서도 수정) / approve(승인·반려·특채) / output(출력) /
    #           users(계정관리, 10분 자동로그아웃 대상) / logs(활동로그 열람)
    # 비밀번호는 내부 시스템 특성상 평문 저장(관리자가 계정 발급·확인 용도)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            display_name TEXT,
            permissions TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    existing_user_cols = [row[1] for row in cur.execute("PRAGMA table_info(users)").fetchall()]
    if "permissions" not in existing_user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN permissions TEXT NOT NULL DEFAULT ''")
    if "is_final_approver" not in existing_user_cols:
        # 최종결정권자 — 승인/특채/불합격 확정에 서명할 수 있는 사람. 최대 2명(MAX_FINAL_APPROVERS)
        cur.execute("ALTER TABLE users ADD COLUMN is_final_approver INTEGER NOT NULL DEFAULT 0")
    if "role" in existing_user_cols:
        # 예전 role 컬럼이 남아있으면 권한 세트로 1회 변환(이미 permissions가 채워진 계정은 건드리지 않음)
        role_to_perms = {
            "admin": "intake,spec,inspect,inspect_all,approve,output,users,logs",
            "approver": "intake,spec,inspect,inspect_all,approve,output",
            "manager": "intake,spec,inspect,inspect_all,output",
            "inspector": "inspect",
        }
        for r in cur.execute("SELECT id, role, permissions FROM users").fetchall():
            uid, old_role, perms = r
            if not perms and old_role in role_to_perms:
                cur.execute("UPDATE users SET permissions = ? WHERE id = ?",
                           (role_to_perms[old_role], uid))

    # 4-1. 조립 제품(MA) 마스터 및 파츠 분해
    cur.execute("""
        CREATE TABLE IF NOT EXISTS assembly_masters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assembly_no TEXT NOT NULL UNIQUE,
            assembly_name TEXT,
            component_count INTEGER DEFAULT 8,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS assembly_components (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            assembly_id INTEGER NOT NULL,
            component_order INTEGER NOT NULL,
            component_no TEXT NOT NULL,
            component_name TEXT,
            FOREIGN KEY(assembly_id) REFERENCES assembly_masters(id),
            UNIQUE(assembly_id, component_order)
        )
    """)

    # 4-1-1. 통합BOM 계층 정보 (2026-09-09) — assembly_masters/components와는 완전히 별개.
    #        조회/필터링 전용("자재 찾기" 화면). 재임포트 시 전량 삭제 후 재삽입한다.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS material_bom_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_no        TEXT NOT NULL,
            parent_material_no TEXT,
            model_name          TEXT NOT NULL,
            level               INTEGER NOT NULL,
            kind                TEXT,
            qty_per_parent      REAL,
            qty_per_model       REAL,
            unit                TEXT,
            source_row_no       INTEGER,
            imported_at         TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bom_material_no ON material_bom_links(material_no)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bom_model_name ON material_bom_links(model_name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_bom_parent ON material_bom_links(parent_material_no)")
    existing_bom_cols = [row[1] for row in cur.execute("PRAGMA table_info(material_bom_links)").fetchall()]
    if "bom_name" not in existing_bom_cols:
        # BOM 원본의 "규격사양(SPEC)" 열 — materials.material_name은 절대 안 건드리고,
        # "자재 찾기"에서 미등록 자재를 등록할 때 이름을 자동으로 채워주는 참고용 값.
        cur.execute("ALTER TABLE material_bom_links ADD COLUMN bom_name TEXT")

    # 4-1. 검사 입력 임시저장 — 검사자가 입력하는 즉시 서버에 저장된다.
    #      예전엔 브라우저 localStorage에만 있어서 태블릿이 꺼지거나 기기를 바꾸면 날아갔다.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inspection_drafts (
            intake_id  INTEGER PRIMARY KEY,
            user_id    INTEGER,
            username   TEXT,
            payload    TEXT NOT NULL,          -- 화면 입력값 전체를 JSON으로
            updated_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 4-1-1. 항목 판정 수동 오버라이드 — 자동판정(AQL 허용범위 등)과 무관하게
    #        사후에 사람이 개별 항목 합격/불합격을 뒤집을 때 쓴다.
    #        inspection_items.result 원본은 절대 안 건드리고 별도 주석 레이어로만 존재한다.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inspection_item_overrides (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            inspection_id      INTEGER NOT NULL,
            part_material_no   TEXT NOT NULL,
            item_name          TEXT NOT NULL,
            override_result    TEXT NOT NULL,
            reason             TEXT NOT NULL,
            created_by_user_id INTEGER,
            created_by_name    TEXT,
            created_at         TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (inspection_id) REFERENCES inspections(id),
            UNIQUE(inspection_id, part_material_no, item_name)
        )
    """)

    # 4-2. 4M 변경점 — 협력사가 사람/설비/자재/방법을 바꾼 시점 기록.
    #      변경 전후 불량률을 비교하려면 "언제 바뀌었는지"가 남아 있어야 한다(IATF 변경점 관리).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS change_points (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier    TEXT NOT NULL,
            material_no TEXT,                  -- 비어 있으면 그 업체 전체에 해당
            change_type TEXT NOT NULL,         -- Man / Machine / Material / Method
            change_date TEXT NOT NULL,
            description TEXT,
            reported_by TEXT,
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 4-3. 업체 월간 품질 성적표 — 자동 생성하되 최종결정권자 승인 전에는 발송 못 한다.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier    TEXT NOT NULL,
            period      TEXT NOT NULL,          -- 'YYYY-MM'
            start_date  TEXT NOT NULL,
            end_date    TEXT NOT NULL,
            payload     TEXT NOT NULL,          -- 집계 결과 스냅샷(JSON)
            status      TEXT NOT NULL DEFAULT 'draft',   -- draft / approved / sent
            created_by  TEXT,
            created_at  TEXT DEFAULT (datetime('now', 'localtime')),
            approved_by TEXT,
            approved_at TEXT,
            approve_signature TEXT,
            sent_to     TEXT,
            sent_at     TEXT,
            UNIQUE(supplier, period)
        )
    """)

    # 5. 활동 로그 — 등록/수정/삭제 등 주요 액션 기록 (admin만 열람)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            role TEXT,
            action TEXT NOT NULL,        -- 예: '성적서 등록', '규격 항목 수정' 등
            target_type TEXT,            -- 예: 'inspection', 'spec_item', 'intake', 'user'
            target_id TEXT,
            detail TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 6. 자재 그룹(조립품) — 입고 시엔 조립품 자재번호 하나지만, 분해해서 부품별로 따로 검사하는 경우
    cur.execute("""
        CREATE TABLE IF NOT EXISTS material_groups (
            group_no TEXT PRIMARY KEY,   -- 조립품 자재번호(입고 리스트/검사입력에서 이 번호로 취급)
            group_name TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS material_group_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_no TEXT NOT NULL,
            material_no TEXT NOT NULL,   -- 분해된 부품의 실제 자재번호 (specs.material_no와 매칭)
            item_order INTEGER DEFAULT 0,
            FOREIGN KEY (group_no) REFERENCES material_groups(group_no)
        )
    """)

    # 7. 규격 일괄등록에서 문제(확인필요/실패) 있었던 자재 — 나중에 다시 보고 해결 처리할 수 있게 기록
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spec_review_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            material_no TEXT,
            source_filename TEXT,
            reason TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 8. 업체 정보 — 이름, 이메일, 연락처
    cur.execute("""
        CREATE TABLE IF NOT EXISTS suppliers (
            name TEXT PRIMARY KEY,
            email TEXT,
            contact TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    existing_supplier_cols = [row[1] for row in cur.execute("PRAGMA table_info(suppliers)").fetchall()]
    if "address" not in existing_supplier_cols:
        cur.execute("ALTER TABLE suppliers ADD COLUMN address TEXT")
    if "biz_no" not in existing_supplier_cols:
        cur.execute("ALTER TABLE suppliers ADD COLUMN biz_no TEXT")
    if "contact_name" not in existing_supplier_cols:
        cur.execute("ALTER TABLE suppliers ADD COLUMN contact_name TEXT")
    if "contact2" not in existing_supplier_cols:
        cur.execute("ALTER TABLE suppliers ADD COLUMN contact2 TEXT")
    if "items" not in existing_supplier_cols:
        cur.execute("ALTER TABLE suppliers ADD COLUMN items TEXT")

    # 8-1. 업체 담당자 — 역할(영업/품질/구매/기타)별로 여러 명 등록 가능(2026-09-07).
    #      suppliers의 contact_name/contact/contact2/email은 옛 단일 담당자 필드로,
    #      호환을 위해 그대로 남겨두고 폴백용으로 쓴다(get_default_contact 참고).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS supplier_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            supplier_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT '기타',
            contact_name TEXT,
            phone TEXT,
            email TEXT,
            notes TEXT,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_supplier_contacts_name ON supplier_contacts(supplier_name)")

    # 9. 부적합 통보서 (NCR)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ncr (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ncr_no TEXT UNIQUE,
            inspection_id INTEGER,
            material_no TEXT,
            material_name TEXT,
            supplier TEXT,
            defect_description TEXT,
            action_required TEXT,
            due_date TEXT,
            issued_by TEXT,
            issued_date TEXT,
            photos TEXT DEFAULT '[]',
            email_sent_at TEXT,
            status TEXT DEFAULT 'draft',
            confirmed_by TEXT,
            confirmed_at TEXT,
            sent_to TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    # 기존 DB에 컬럼 없으면 추가 (마이그레이션)
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(ncr)").fetchall()]
    for col, definition in [
        ("confirmed_by", "TEXT"),
        ("confirmed_at", "TEXT"),
        ("sent_to",      "TEXT"),
        # 최종결정권자 승인 서명 이미지 경로 — 협력사로 나가는 문서라 서명 근거가 남아야 함
        ("confirm_signature", "TEXT"),
        # 수기입력(성적서 미연결) 통보서용 — 연결된 성적서가 있으면 insp_ 조인값을 쓰고,
        # 없으면(inspection_id NULL) 이 값을 대신 보여준다
        ("lot_number", "TEXT"),
        ("receive_date", "TEXT"),
        # 새 양식 필드 (2026-09-04)
        ("cc_recipient",  "TEXT"),
        ("sample_qty",    "INTEGER"),
        ("defect_qty",    "INTEGER"),
        ("special_note",  "TEXT"),
        ("lot_qty",       "TEXT"),
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE ncr ADD COLUMN {col} {definition}")

    # 9-1. 개선요청서 — NCR보다 가벼운 사전조치 문서 (2026-09-15, 서명 없음)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS improvement_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_no TEXT UNIQUE,
            inspection_id INTEGER,
            material_no TEXT,
            material_name TEXT,
            supplier TEXT,
            lot_number TEXT,
            po_number TEXT,
            lot_qty TEXT,
            defect_categories TEXT,
            defect_category_etc TEXT,
            request_detail TEXT,
            confirmed_name TEXT,
            issued_by TEXT,
            issued_date TEXT,
            status TEXT DEFAULT 'draft',
            email_sent_at TEXT,
            sent_to TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS improvement_request_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            improvement_request_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'defect',
            photo_path TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_imp_photos_req ON improvement_request_photos(improvement_request_id)")

    # 10. 앱 설정 (SMTP 등)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # 11. 계측기 마스터
    cur.execute("""
        CREATE TABLE IF NOT EXISTS gauge_master (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gauge_no TEXT UNIQUE,
            name TEXT NOT NULL,
            model TEXT,
            location TEXT,
            last_calibrated TEXT,
            expiry_date TEXT,
            notes TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 12. 검사 진행 현황 (검사 입력폼 열고 있는 사람 추적)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inspection_progress (
            intake_id INTEGER PRIMARY KEY,
            inspectors TEXT DEFAULT '[]',
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 13. 반품 처리
    cur.execute("""
        CREATE TABLE IF NOT EXISTS return_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspection_id INTEGER NOT NULL,
            material_no TEXT,
            material_name TEXT,
            supplier TEXT,
            return_date TEXT,
            reason TEXT,
            quantity INTEGER,
            status TEXT DEFAULT '반품요청',
            resolved_inspection_id INTEGER,
            created_by TEXT,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 전수검사 헤더 — inspection_id 1:1 대응. 상태: open / complete
    cur.execute("""
        CREATE TABLE IF NOT EXISTS full_inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspection_id INTEGER NOT NULL UNIQUE,
            inspect_date TEXT,
            complete_date TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (inspection_id) REFERENCES inspections(id)
        )
    """)
    # 전수검사 유닛별 데이터 — 유닛 1개(시리얼번호) = 1행
    cur.execute("""
        CREATE TABLE IF NOT EXISTS full_inspection_units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            full_inspection_id INTEGER NOT NULL,
            unit_no INTEGER NOT NULL,
            serial_no TEXT DEFAULT '',
            values_json TEXT DEFAULT '{}',
            result TEXT DEFAULT '',
            remark TEXT DEFAULT '',
            gauge_name TEXT DEFAULT '',
            FOREIGN KEY (full_inspection_id) REFERENCES full_inspections(id)
        )
    """)
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

    existing_oi_cols = [row[1] for row in cur.execute("PRAGMA table_info(outbound_items)").fetchall()]
    # 2026-09-15 확장: 출고 전 품질확인항목(PASS/FAIL/SPECIAL/해당없음) + 전체판정 수동 오버라이드.
    # 2026-09-20: 5→9개로 확장(check_sticker/paint/cable/indicator 추가).
    # 자동판정 값 자체는 저장하지 않는다 — compute_outbound_item_auto_result()가 9개
    # check_* 컬럼에서 매번 계산한다(CLAUDE.md 8-1절, 저장값-계산값 불일치 사고 방지).
    for _ob_col in OUTBOUND_CHECK_FIELDS + ["result_override"]:
        if _ob_col not in existing_oi_cols:
            cur.execute(f"ALTER TABLE outbound_items ADD COLUMN {_ob_col} TEXT")

    if "inspected_by" not in existing_oi_cols:
        # 2026-09-21 확장: 항목(S/N)을 등록한 사용자 표시이름. app.py의 outbound_item_add()가
        # 생성 시점에 채운다. 저장된 항목의 수정/삭제 권한을 "이 항목의 검사자 본인" 또는
        # "이 차수의 등록자"(outbound_batches.created_by)로 제한하는 데 쓴다(사용자 확정,
        # admin 예외·outbound_delete 권한 예외 없음). 마이그레이션 이전에 생성된 기존 항목은
        # NULL로 남는다 — 그 항목은 차수 등록자만 수정/삭제 가능해진다(의도된 동작, 버그 아님).
        cur.execute("ALTER TABLE outbound_items ADD COLUMN inspected_by TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS outbound_item_photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id     INTEGER NOT NULL REFERENCES outbound_items(id),
            file_path   TEXT NOT NULL,
            uploaded_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    existing_obp_cols = [row[1] for row in cur.execute("PRAGMA table_info(outbound_item_photos)").fetchall()]
    if "kind" not in existing_obp_cols:
        # 'indicator'(인디케이터 사진) / 'body'(본체사진). 기존 사진은 전부 'indicator'로
        # 간주한다 — 지금까지는 사진 종류 구분이 없어 전부 한 종류였고, 사용자가 준
        # 실제 서식에서도 첫 번째 사진 칸이 인디케이터 사진이기 때문(합리적 기본값).
        cur.execute("ALTER TABLE outbound_item_photos ADD COLUMN kind TEXT NOT NULL DEFAULT 'indicator'")

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

    # ---- 2026-09-21 신규: 스티커 부착 기준 참고(출고 스캔 "기준 보기" 팝업용) ----
    # assembly_masters(5-0절)/material_bom_links(18절)와 완전히 별개 — CKMR 모델별
    # 스티커 4종 부착면/수량 표 + 참고이미지를 보여주기 위한 순수 조회용 참고 데이터.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sticker_reference_models (
            model_no      TEXT PRIMARY KEY,
            voltage_code  TEXT,
            suffix_code   TEXT,
            pcode         TEXT,
            image_path    TEXT,
            updated_at    TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS sticker_reference_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            model_no      TEXT NOT NULL REFERENCES sticker_reference_models(model_no),
            no            TEXT,
            label         TEXT NOT NULL,
            attach_face   TEXT,
            qty           TEXT,
            sort_order    INTEGER NOT NULL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sticker_ref_items_model "
                "ON sticker_reference_items(model_no)")
    # ---- 2026-09-21 신규 끝 ----

    # ---- 2026-09-15 확장: 차수(=배치) 계획 ----
    existing_ob_cols = [row[1] for row in cur.execute("PRAGMA table_info(outbound_batches)").fetchall()]
    for col in ("round_no", "confirmed_by", "confirmed_at", "confirm_signature"):
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

    # ---- 신규 삽입 끝 ----

    conn.commit()
    conn.close()


# ---------- 사용자 계정 ----------

def get_user_by_username(username):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()
    return row


def get_user(user_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def list_users():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
    conn.close()
    return rows


def count_users():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    conn.close()
    return n


def create_user(username, password, display_name, permissions=""):
    conn = get_conn()
    conn.execute("""
        INSERT INTO users (username, password, display_name, permissions)
        VALUES (?, ?, ?, ?)
    """, (username, password, display_name, permissions))
    conn.commit()
    conn.close()


def update_user_permissions(user_id, permissions):
    conn = get_conn()
    conn.execute("UPDATE users SET permissions = ? WHERE id = ?", (permissions, user_id))
    conn.commit()
    conn.close()


def user_has_permission(user_row, perm):
    if user_row is None:
        return False
    perms = (user_row["permissions"] or "").split(",")
    return perm in perms


# ---------- 최종결정권자 ----------

MAX_FINAL_APPROVERS = 2   # 최종결정권자는 최대 2명까지만 지정할 수 있다


def list_final_approvers():
    """최종결정권자로 지정된 계정 목록."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM users WHERE is_final_approver = 1 ORDER BY id"
    ).fetchall()
    conn.close()
    return rows


def set_final_approver(user_id, enabled):
    """최종결정권자 지정/해제. 최대 인원을 넘기면 (False, 에러메시지)."""
    conn = get_conn()
    try:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if user is None:
            return False, "존재하지 않는 계정이야."

        if enabled:
            if not user_has_permission(user, "approve"):
                return False, "최종결정권자로 지정하려면 먼저 '승인' 권한을 줘야 해."
            current = conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_final_approver = 1 AND id != ?", (user_id,)
            ).fetchone()[0]
            if current >= MAX_FINAL_APPROVERS:
                return False, (f"최종결정권자는 최대 {MAX_FINAL_APPROVERS}명까지야. "
                               f"다른 사람 체크를 먼저 풀어줘.")

        conn.execute("UPDATE users SET is_final_approver = ? WHERE id = ?",
                     (1 if enabled else 0, user_id))
        conn.commit()
        return True, None
    finally:
        conn.close()


def delete_user(user_id):
    conn = get_conn()
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def update_user_password(user_id, password):
    conn = get_conn()
    conn.execute("UPDATE users SET password = ? WHERE id = ?", (password, user_id))
    conn.commit()
    conn.close()


def update_user_profile(user_id, username, display_name):
    """계정 아이디/표시이름 수정. username 중복이면 (False, 에러메시지) 반환."""
    conn = get_conn()
    dup = conn.execute("SELECT id FROM users WHERE username = ? AND id != ?", (username, user_id)).fetchone()
    if dup:
        conn.close()
        return False, f"아이디 '{username}'는 이미 사용 중이야."
    conn.execute("UPDATE users SET username = ?, display_name = ? WHERE id = ?",
                (username, display_name, user_id))
    conn.commit()
    conn.close()
    return True, None


# ---------- 활동 로그 ----------

def log_activity(user_id, username, role, action, target_type=None, target_id=None, detail=None):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO activity_log (user_id, username, role, action, target_type, target_id, detail)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (user_id, username, role, action, target_type, str(target_id) if target_id is not None else None, detail))
    log_id = cur.lastrowid
    conn.commit()
    conn.close()
    return log_id


def list_activity_logs(limit=300):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows


# ---------- 규격표 조회/등록 ----------

def get_materials():
    """등록된 모든 자재번호 목록 (항목이 아직 없는 자재도 포함)"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM materials ORDER BY material_no"
    ).fetchall()
    conn.close()
    return rows


def get_material(material_no):
    conn = get_conn()
    row = conn.execute("SELECT * FROM materials WHERE material_no = ?", (material_no,)).fetchone()
    conn.close()
    return row


def upsert_material(material_no, material_name=None):
    conn = get_conn()
    existing = conn.execute("SELECT material_no FROM materials WHERE material_no = ?", (material_no,)).fetchone()
    if existing:
        if material_name:
            conn.execute("UPDATE materials SET material_name = ? WHERE material_no = ?", (material_name, material_no))
    else:
        today = _dt.now().strftime("%Y-%m-%d")
        conn.execute("""
            INSERT INTO materials (material_no, material_name, revision_date)
            VALUES (?, ?, ?)
        """, (material_no, material_name, today))
    conn.commit()
    conn.close()


def update_drawing_file(material_no, drawing_file):
    """수동 도면 파일 지정 — drawing_file은 도면 폴더 내 파일명(예: 'CKMR4610.pdf')"""
    conn = get_conn()
    conn.execute("UPDATE materials SET drawing_file = ? WHERE material_no = ?",
                 (drawing_file or None, material_no))
    conn.commit()
    conn.close()


def update_material_standard_info(material_no, drawing_version, revision_date, edition, unit):
    """기준서(SAM 양식)에 들어가는 자재별 정보 — 도면버전/개정일자/판수/단위."""
    conn = get_conn()
    conn.execute("""
        UPDATE materials SET drawing_version = ?, revision_date = ?, edition = ?, unit = ?
        WHERE material_no = ?
    """, (drawing_version, revision_date, edition, unit, material_no))
    conn.commit()
    conn.close()


def rename_material(old_no, new_no, new_name):
    """
    자재번호/자재명 변경 — 지금부터만 적용(과거 입고/검사/성적서 기록은 옛 번호 그대로 둠).
    materials·specs·material_group_items(부품으로 소속된 경우)만 새 번호로 갱신.
    반환: (성공여부, 에러메시지)
    """
    conn = get_conn()
    if old_no != new_no:
        dup = conn.execute("SELECT 1 FROM materials WHERE material_no = ?", (new_no,)).fetchone()
        if dup:
            conn.close()
            return False, f"자재번호 '{new_no}'는 이미 사용 중이야."
        dup_group = conn.execute("SELECT 1 FROM material_groups WHERE group_no = ?", (new_no,)).fetchone()
        if dup_group:
            conn.close()
            return False, f"'{new_no}'는 이미 조립품 그룹 번호로 쓰이고 있어."

    existing = conn.execute("SELECT 1 FROM materials WHERE material_no = ?", (old_no,)).fetchone()
    if existing:
        conn.execute("UPDATE materials SET material_no = ?, material_name = ? WHERE material_no = ?",
                    (new_no, new_name, old_no))
    else:
        # old_no가 아직 자재로 등록 안 된 상태(예: 검사대기 목록에서 "규격 미등록" 링크로 바로
        # 들어와서 자재명만 처음 입력하는 경우) — UPDATE는 매칭되는 행이 없어 조용히 아무 일도
        # 안 하므로, 이 경우엔 새로 INSERT해야 한다.
        today = _dt.now().strftime("%Y-%m-%d")
        conn.execute("INSERT INTO materials (material_no, material_name, revision_date) VALUES (?, ?, ?)",
                    (new_no, new_name, today))
    conn.execute("UPDATE specs SET material_no = ?, material_name = ? WHERE material_no = ?",
                (new_no, new_name, old_no))
    conn.execute("UPDATE material_group_items SET material_no = ? WHERE material_no = ?",
                (new_no, old_no))
    conn.commit()
    conn.close()
    return True, None


def upsert_materials_bulk(rows):
    """rows: [{"material_no":.., "material_name":..}, ...]"""
    conn = get_conn()
    today = _dt.now().strftime("%Y-%m-%d")
    for r in rows:
        existing = conn.execute("SELECT material_no FROM materials WHERE material_no = ?", (r["material_no"],)).fetchone()
        if existing:
            if r.get("material_name"):
                conn.execute("UPDATE materials SET material_name = ? WHERE material_no = ?",
                            (r["material_name"], r["material_no"]))
        else:
            conn.execute("""
                INSERT INTO materials (material_no, material_name, revision_date)
                VALUES (?, ?, ?)
            """, (r["material_no"], r.get("material_name"), today))
    conn.commit()
    conn.close()


def list_material_categories():
    """등록된 자재 분류명 전체 (가나다순)."""
    conn = get_conn()
    rows = conn.execute("SELECT name FROM material_categories ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def add_material_category(name):
    """분류명을 마스터에 등록. 이미 있으면(공백/대소문자 무시) 기존 정본 표기를 반환.
    name이 빈 값이면 None."""
    name = (name or "").strip()
    if not name:
        return None
    conn = get_conn()
    for row in conn.execute("SELECT name FROM material_categories").fetchall():
        if row["name"].strip().casefold() == name.casefold():
            conn.close()
            return row["name"]
    conn.execute("INSERT INTO material_categories (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    return name


def update_material_category(material_no, category):
    """자재 하나의 분류 저장. category가 비어있지 않으면 마스터에 자동 등록 후 정본
    표기로 저장. 빈 값이면 NULL로 해제. 반환값: 실제 저장된 분류명(또는 None)."""
    canonical = add_material_category(category) if (category or "").strip() else None
    conn = get_conn()
    conn.execute("UPDATE materials SET category = ? WHERE material_no = ?", (canonical, material_no))
    conn.commit()
    conn.close()
    return canonical


def _include_exclude_clause(columns, include_words=None, exclude_words=None):
    """포함/제외 다중 단어 검색 조건 생성 — 자재 관리(search_materials)/자재 찾기
    (search_bom_materials) 공용 헬퍼(8-1절 원칙, 복붙 금지). 포함 단어 여러 개 = OR
    (하나라도 매칭 컬럼에 있으면 통과), 제외 단어 여러 개 = OR(하나라도 있으면 제외 —
    드모르간 법칙으로 NOT (OR 그룹) 하나로 표현).

    columns: SQL 컬럼 표현식 리스트(예: ["m.material_no", "m.material_name"]).
    반환: (조건식 문자열 — 괄호 포함, 앞에 "AND" 없음, params 리스트). 조건이 없으면 ("", []).
    호출부에서 기존 SQL 조립 방식(리스트에 append 하거나 " AND {식}"으로 이어붙이는 등)에
    맞춰 붙이면 된다."""
    include_words = [w.strip() for w in (include_words or []) if w and w.strip()]
    exclude_words = [w.strip() for w in (exclude_words or []) if w and w.strip()]
    parts, params = [], []

    def _or_group(words):
        # COALESCE 필수 — search_bom_materials처럼 LEFT JOIN이라 컬럼이 NULL일 수 있으면
        # "NOT (... OR NULL ...)" 이 SQL 3치논리상 NULL이 되어 조건 없는 exclude에서도
        # 그 행이 통째로 빠져버린다(실측으로 확인한 버그, 2026-09-14).
        ors = []
        for w in words:
            like = f"%{w}%"
            for col in columns:
                ors.append(f"COALESCE({col}, '') LIKE ?")
                params.append(like)
        return "(" + " OR ".join(ors) + ")"

    if include_words:
        parts.append(_or_group(include_words))
    if exclude_words:
        parts.append("NOT " + _or_group(exclude_words))

    if not parts:
        return "", []
    return " AND ".join(parts), params


def search_materials(query=None, search_by="all", category=None, include=None, exclude=None):
    """
    query: 검색어. search_by: 'material_no' / 'material_name' / 'method' / 'spec' / 'all'
    'method'(검사방식)는 specs.inspect_method에서, 'spec'(규격 표기)는 specs.spec_display에서 매칭.
    'all'(전체)은 자재번호·자재명·규격 표기·검사방식을 모두 훑는다.
    category: 지정하면 그 분류(정확일치)로만 추가 필터링. search_by(어디서 찾을지)와는
    별개 축이라 AND 조건으로 얹는다.
    include/exclude: 포함/제외 다중 단어(자재번호·자재명만 대상, 규격표기/검사방식은
    제외 — 확정된 결정). _include_exclude_clause() 참고.
    """
    conn = get_conn()
    category = (category or "").strip() or None
    cat_sql = " AND m.category = ?" if category else ""
    cat_params = (category,) if category else ()
    ie_expr, ie_params = _include_exclude_clause(["m.material_no", "m.material_name"], include, exclude)
    ie_sql = f" AND {ie_expr}" if ie_expr else ""

    if search_by == "method_empty":
        # 검사방식(inspect_method)이 비어 있는 항목을 가진 자재. 검색어와 무관하게 동작한다.
        rows = conn.execute(f"""
            SELECT DISTINCT m.material_no, m.material_name, m.category FROM materials m
            JOIN specs s ON s.material_no = m.material_no
            WHERE (s.inspect_method IS NULL OR TRIM(s.inspect_method) = ''){cat_sql}{ie_sql}
            ORDER BY m.material_no
        """, (*cat_params, *ie_params)).fetchall()
        conn.close()
        return rows
    if not query:
        rows = conn.execute(f"""
            SELECT m.material_no, m.material_name, m.category FROM materials m
            WHERE 1=1{cat_sql}{ie_sql}
            ORDER BY m.material_no
        """, (*cat_params, *ie_params)).fetchall()
        conn.close()
        return rows

    like = f"%{query}%"
    if search_by == "material_no":
        rows = conn.execute(f"""
            SELECT m.material_no, m.material_name, m.category FROM materials m
            WHERE m.material_no LIKE ?{cat_sql}{ie_sql}
            ORDER BY m.material_no
        """, (like, *cat_params, *ie_params)).fetchall()
    elif search_by == "material_name":
        rows = conn.execute(f"""
            SELECT m.material_no, m.material_name, m.category FROM materials m
            WHERE m.material_name LIKE ?{cat_sql}{ie_sql}
            ORDER BY m.material_no
        """, (like, *cat_params, *ie_params)).fetchall()
    elif search_by == "method":
        rows = conn.execute(f"""
            SELECT DISTINCT m.material_no, m.material_name, m.category FROM materials m
            JOIN specs s ON s.material_no = m.material_no
            WHERE s.inspect_method LIKE ?{cat_sql}{ie_sql}
            ORDER BY m.material_no
        """, (like, *cat_params, *ie_params)).fetchall()
    elif search_by == "spec":
        rows = conn.execute(f"""
            SELECT DISTINCT m.material_no, m.material_name, m.category FROM materials m
            JOIN specs s ON s.material_no = m.material_no
            WHERE s.spec_display LIKE ?{cat_sql}{ie_sql}
            ORDER BY m.material_no
        """, (like, *cat_params, *ie_params)).fetchall()
    else:
        # 전체: 자재번호·자재명·규격 표기·검사방식 어디에 있든 잡는다.
        # (규격 표기 spec_display를 빠뜨려서 규격에만 있는 검색어가 안 걸리던 버그 수정)
        rows = conn.execute(f"""
            SELECT DISTINCT m.material_no, m.material_name, m.category FROM materials m
            LEFT JOIN specs s ON s.material_no = m.material_no
            WHERE (m.material_no LIKE ? OR m.material_name LIKE ?
               OR s.spec_display LIKE ? OR s.inspect_method LIKE ?){cat_sql}{ie_sql}
            ORDER BY m.material_no
        """, (like, like, like, like, *cat_params, *ie_params)).fetchall()
    conn.close()
    return rows


def get_specs_by_material(material_no):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM specs WHERE material_no = ? ORDER BY item_order, id",
        (material_no,)
    ).fetchall()
    conn.close()
    return rows


# ---------- 자재 그룹(조립품) ----------

def list_material_groups():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM material_groups ORDER BY group_no").fetchall()
    conn.close()
    return rows


def get_material_group(group_no):
    conn = get_conn()
    row = conn.execute("SELECT * FROM material_groups WHERE group_no = ?", (group_no,)).fetchone()
    conn.close()
    return row


def get_group_members(group_no):
    """그룹에 속한 부품 자재번호 목록 (등록 순서대로), 각 부품의 material_name도 같이 반환."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT gi.id, gi.group_no, gi.material_no, gi.item_order,
               (SELECT material_name FROM specs WHERE specs.material_no = gi.material_no LIMIT 1) AS material_name
        FROM material_group_items gi
        WHERE gi.group_no = ?
        ORDER BY gi.item_order, gi.id
    """, (group_no,)).fetchall()
    conn.close()
    return rows


def create_material_group(group_no, group_name):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO material_groups (group_no, group_name) VALUES (?, ?)",
                (group_no, group_name))
    conn.commit()
    conn.close()


def delete_material_group(group_no):
    conn = get_conn()
    conn.execute("DELETE FROM material_group_items WHERE group_no = ?", (group_no,))
    conn.execute("DELETE FROM material_groups WHERE group_no = ?", (group_no,))
    conn.commit()
    conn.close()


def add_group_member(group_no, material_no, item_order=0):
    conn = get_conn()
    conn.execute("""
        INSERT INTO material_group_items (group_no, material_no, item_order)
        VALUES (?, ?, ?)
    """, (group_no, material_no, item_order))
    conn.commit()
    conn.close()


def remove_group_member(member_id):
    conn = get_conn()
    conn.execute("DELETE FROM material_group_items WHERE id = ?", (member_id,))
    conn.commit()
    conn.close()


# ---------- 규격 일괄등록 확인필요 자재 ----------

def add_review_flag(material_no, source_filename, reason):
    conn = get_conn()
    conn.execute("""
        INSERT INTO spec_review_flags (material_no, source_filename, reason)
        VALUES (?, ?, ?)
    """, (material_no, source_filename, reason))
    conn.commit()
    conn.close()


def list_unresolved_review_flags():
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM spec_review_flags WHERE resolved = 0 ORDER BY id DESC
    """).fetchall()
    conn.close()
    return rows


def resolve_review_flag(flag_id):
    conn = get_conn()
    conn.execute("UPDATE spec_review_flags SET resolved = 1 WHERE id = ?", (flag_id,))
    conn.commit()
    conn.close()


def add_spec(material_no, material_name, item_name, spec_display,
             judge_type, lower_limit, upper_limit, inspect_method, aql, item_order=0):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO specs (material_no, material_name, item_name, spec_display,
                            judge_type, lower_limit, upper_limit, inspect_method, aql, item_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (material_no, material_name, item_name, spec_display,
          judge_type, lower_limit, upper_limit, inspect_method, aql, item_order))
    new_id = cur.lastrowid
    if not conn.execute("SELECT 1 FROM materials WHERE material_no = ?", (material_no,)).fetchone():
        conn.execute("INSERT INTO materials (material_no, material_name) VALUES (?, ?)",
                    (material_no, material_name))
    elif material_name:
        conn.execute("UPDATE materials SET material_name = ? WHERE material_no = ? AND (material_name IS NULL OR material_name = '')",
                    (material_name, material_no))
    conn.commit()
    conn.close()
    return new_id


def replace_specs_for_material(material_no, material_name, items):
    """
    한 자재의 규격표를 통째로 교체 등록 (파일 재업로드 시 중복 없이 덮어쓰기).
    items: [{"item_name","spec_display","judge_type","lower_limit","upper_limit",
             "inspect_method","aql","item_order"}]
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM specs WHERE material_no = ?", (material_no,))
    for it in items:
        cur.execute("""
            INSERT INTO specs (material_no, material_name, item_name, spec_display,
                                judge_type, lower_limit, upper_limit, inspect_method, aql, item_order)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (material_no, material_name, it["item_name"], it["spec_display"],
              it["judge_type"], it["lower_limit"], it["upper_limit"],
              it["inspect_method"], it["aql"], it["item_order"]))
    if not cur.execute("SELECT 1 FROM materials WHERE material_no = ?", (material_no,)).fetchone():
        cur.execute("INSERT INTO materials (material_no, material_name) VALUES (?, ?)",
                    (material_no, material_name))
    elif material_name:
        cur.execute("UPDATE materials SET material_name = ? WHERE material_no = ?", (material_name, material_no))
    conn.commit()
    conn.close()


def get_spec_item(spec_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM specs WHERE id = ?", (spec_id,)).fetchone()
    conn.close()
    return row


def update_spec_item(spec_id, item_name, spec_display, judge_type,
                      lower_limit, upper_limit, inspect_method, aql, item_order):
    conn = get_conn()
    conn.execute("""
        UPDATE specs SET item_name=?, spec_display=?, judge_type=?, lower_limit=?,
                          upper_limit=?, inspect_method=?, aql=?, item_order=?
        WHERE id=?
    """, (item_name, spec_display, judge_type, lower_limit, upper_limit,
          inspect_method, aql, item_order, spec_id))
    conn.commit()
    conn.close()


def delete_spec_item(spec_id):
    conn = get_conn()
    conn.execute("DELETE FROM specs WHERE id = ?", (spec_id,))
    conn.commit()
    conn.close()


def delete_spec_items_bulk(spec_ids):
    if not spec_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" for _ in spec_ids)
    conn.execute(f"DELETE FROM specs WHERE id IN ({placeholders})", spec_ids)
    conn.commit()
    conn.close()


def delete_materials_bulk(material_nos):
    """자재 통째로(규격+마스터) 삭제 — 규격 목록 화면에서 자재 단위 선택삭제용."""
    if not material_nos:
        return
    conn = get_conn()
    placeholders = ",".join("?" for _ in material_nos)
    conn.execute(f"DELETE FROM specs WHERE material_no IN ({placeholders})", material_nos)
    conn.execute(f"DELETE FROM materials WHERE material_no IN ({placeholders})", material_nos)
    conn.commit()
    conn.close()


# ---------- 입고 리스트 (붙여넣기 등록) ----------

def find_duplicate_intakes(rows):
    """이미 intake_list에 등록된 동일 항목 찾기. 둘 중 하나라도 걸리면 중복:
    (1) po_number 있으면 material_no+po_number 일치
    (2) material_no+receive_date+supplier 일치 (2026-09-16 사용자 요청 추가 —
        발주번호가 달라도 같은 업체·같은 날짜에 같은 자재번호면 중복으로 본다.
        예전엔 po_number가 있는 행에서 (1)만 보고 (2)는 아예 안 봤음).
    매칭된 기존 레코드의 id/status/quantity/po_number를 'existing' 키에 담아
    같이 돌려준다(2026-09-16 합산 기능 추가) — 합산할 때 어느 기존 행에 합칠지,
    이미 검사완료돼서 합산이 불가능한지(그럴 땐 별도 등록으로 폴백) 판단하는 데 쓴다."""
    if not rows:
        return []
    conn = get_conn()
    dups = []
    for r in rows:
        mn  = r["material_no"]
        po  = (r.get("po_number") or "").strip()
        rd  = (r.get("receive_date") or "").strip()
        sup = (r.get("supplier") or "").strip()
        hit = None
        if po:
            hit = conn.execute(
                "SELECT id, status, quantity, po_number FROM intake_list WHERE material_no=? AND po_number=?",
                (mn, po)).fetchone()
        if not hit:
            hit = conn.execute(
                "SELECT id, status, quantity, po_number FROM intake_list WHERE material_no=? AND receive_date=? AND supplier=?",
                (mn, rd, sup)).fetchone()
        if hit:
            d = dict(r)
            d["existing"] = {"id": hit["id"], "status": hit["status"],
                              "quantity": hit["quantity"], "po_number": hit["po_number"] or ""}
            dups.append(d)
    conn.close()
    return dups


def merge_intake_duplicate(existing_id, add_quantity, add_po_number):
    """중복 입고 합산 처리(2026-09-16 사용자 요청) — 기존 intake_list 행의 수량에
    더하고, 발주번호를 '/'로 이어붙인다(이미 포함된 발주번호면 또 안 붙임 — 사용자
    확정). 숫자로 못 바꾸는 수량이 섞여 있으면 값 유실 방지를 위해 원문을 이어붙인다
    (_merge_duplicate_intake_rows()의 같은 원칙 재사용, 8-1절)."""
    conn = get_conn()
    row = conn.execute("SELECT quantity, po_number FROM intake_list WHERE id=?", (existing_id,)).fetchone()
    if row is None:
        conn.close()
        return
    try:
        existing_qty = int(row["quantity"]) if row["quantity"] not in (None, "") else 0
    except (TypeError, ValueError):
        existing_qty = None
    try:
        new_qty = int(add_quantity) if add_quantity not in (None, "") else 0
    except (TypeError, ValueError):
        new_qty = None
    if existing_qty is None or new_qty is None:
        parts = [str(p) for p in (row["quantity"], add_quantity) if p not in (None, "")]
        merged_qty = ", ".join(parts) if parts else None
    else:
        merged_qty = existing_qty + new_qty

    po_parts = [p for p in (row["po_number"] or "").split("/") if p]
    new_po = (add_po_number or "").strip()
    if new_po and new_po not in po_parts:
        po_parts.append(new_po)
    merged_po = "/".join(po_parts)

    conn.execute("UPDATE intake_list SET quantity=?, po_number=? WHERE id=?",
                (merged_qty, merged_po, existing_id))
    conn.commit()
    conn.close()


def add_intake_bulk(rows):
    """
    rows: list of dict (material_no, quantity, supplier, receive_date, po_number, product_name, assembly_no, is_priority)
    붙여넣기로 여러 건을 한 번에 등록
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


def set_intake_status(intake_id, status):
    conn = get_conn()
    conn.execute("UPDATE intake_list SET status = ? WHERE id = ?", (status, intake_id))
    conn.commit()
    conn.close()


def set_intake_priority(intake_id, is_priority):
    conn = get_conn()
    conn.execute("UPDATE intake_list SET is_priority = ? WHERE id = ?",
                (1 if is_priority else 0, intake_id))
    conn.commit()
    conn.close()


def list_intake(status=None):
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM intake_list WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM intake_list ORDER BY id DESC").fetchall()
    conn.close()
    return rows


def search_intake(query, status=None, include=None, exclude=None):
    """자재번호/제품명/납품업체/발주번호 기준으로 입고 리스트 검색.
    include/exclude(포함/제외 다중 단어)도 같은 4개 필드를 대상으로 본다(_include_exclude_clause 재사용,
    8-1절 원칙, 검사대기목록 화면의 포함/제외 검색 — 2026-09-14)."""
    cols = ["material_no", "product_name", "supplier", "po_number"]
    where = ["1=1"]
    params = []
    if status:
        where.append("status = ?")
        params.append(status)
    if query:
        like = f"%{query}%"
        where.append("(material_no LIKE ? OR product_name LIKE ? OR supplier LIKE ? OR po_number LIKE ?)")
        params += [like, like, like, like]
    ie_sql, ie_params = _include_exclude_clause(cols, include, exclude)
    if ie_sql:
        where.append(ie_sql)
        params += ie_params

    conn = get_conn()
    rows = conn.execute(f"""
        SELECT * FROM intake_list
        WHERE {' AND '.join(where)}
        ORDER BY id DESC
    """, params).fetchall()
    conn.close()
    return rows


def get_intake(intake_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM intake_list WHERE id = ?", (intake_id,)).fetchone()
    conn.close()
    return row


def delete_intake_bulk(intake_ids):
    """입고 리스트(검사 대기 목록)에서 선택한 건들을 삭제. 이미 검사완료(상태='검사완료')된 건은
    성적서와 연결돼있을 수 있으니 여기서는 지우지 않고 건너뜀 — 대기 중인 건만 삭제."""
    if not intake_ids:
        return 0
    conn = get_conn()
    placeholders = ",".join("?" for _ in intake_ids)
    cur = conn.execute(
        f"DELETE FROM intake_list WHERE id IN ({placeholders}) AND status = '대기'",
        intake_ids
    )
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


# ---------- 과거 입고 이력 (검사/승인과 무관한 순수 조회용 — intake_list와 별개 테이블) ----------

def add_intake_history_bulk(rows, source, imported_by):
    """rows: list of dict(material_no, product_name, supplier, receive_date, po_number, quantity)
    검사/승인 큐에 절대 올라가지 않는다 — 그냥 intake_history 테이블에 그대로 적재만 한다."""
    conn = get_conn()
    cur = conn.cursor()
    for r in rows:
        cur.execute("""
            INSERT INTO intake_history
                (material_no, product_name, supplier, receive_date, po_number, quantity, source, imported_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (r["material_no"], r.get("product_name"), r.get("supplier"),
              r.get("receive_date"), r.get("po_number"), r.get("quantity"),
              source, imported_by))
    conn.commit()
    conn.close()


def find_duplicate_intake_history(rows):
    """material_no+supplier+receive_date+po_number+quantity가 전부 같은 행이 이미
    intake_history에 있으면 그 rows만 골라 돌려준다. 같은 엑셀 파일을 실수로 두 번
    올렸을 때 감지하기 위함. 값이 같은 게 실제로 반복 입고일 수도 있어서 자동으로
    막지는 않고(등록 여부는 호출부가 결정), 감지만 한다."""
    if not rows:
        return []
    conn = get_conn()
    dups = []
    for r in rows:
        hit = conn.execute("""
            SELECT id FROM intake_history
            WHERE material_no = ?
              AND IFNULL(supplier, '') = IFNULL(?, '')
              AND IFNULL(receive_date, '') = IFNULL(?, '')
              AND IFNULL(po_number, '') = IFNULL(?, '')
              AND IFNULL(quantity, -1) = IFNULL(?, -1)
        """, (r["material_no"], r.get("supplier"), r.get("receive_date"),
              r.get("po_number"), r.get("quantity"))).fetchone()
        if hit:
            dups.append(r)
    conn.close()
    return dups


def list_intake_history():
    """과거 입고 이력 전체 조회 — 검색/필터는 app.py의 공용 헬퍼
    (_list_search_params/_row_passes_search)가 화면 쪽에서 Python으로 거른다.
    검사이력(history.html) 등 4개 화면과 같은 방식으로 통일했다(2026-09-08 —
    예전엔 이 테이블만 SQL WHERE로 직접 검색해서 화면마다 검색 UI/방식이 달랐음)."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM intake_history ORDER BY receive_date DESC, id DESC").fetchall()
    conn.close()
    return rows


def delete_intake_history_bulk(ids):
    """목록 화면에서 선택 삭제. 이 테이블엔 상태 개념이 없으니 그냥 지운다."""
    if not ids:
        return 0
    conn = get_conn()
    placeholders = ",".join("?" for _ in ids)
    cur = conn.execute(f"DELETE FROM intake_history WHERE id IN ({placeholders})", ids)
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


def delete_intake_history_by_source(source):
    """대량 등록 직후 "방금 등록한 것 전체 되돌리기" 용 — source 문자열은 그 등록
    액션(파일명+시각)마다 고유하게 만들어지므로 이걸로 배치 단위 삭제가 된다."""
    if not source:
        return 0
    conn = get_conn()
    cur = conn.execute("DELETE FROM intake_history WHERE source = ?", (source,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    return deleted


# ---------- 검사(성적서) 생성/조회 ----------

def create_inspection(header, items_with_results, overall_result, intake_id=None,
                       est_time_label=None, actual_time_sec=None, created_by_user_id=None,
                       total_time_sec=None):
    """
    header: dict (material_no, material_name, supplier, po_number,
                   receive_date, inspect_date, inspector, quantity)
    items_with_results: list of dict (item_name, measured_value, max_value, min_value, result)

    intake_id가 있으면 BEGIN IMMEDIATE로 잠근 뒤 활성 성적서가 없는지 다시 한 번
    확인하고 INSERT한다 — 호출부(라우트)가 미리 active_inspection_for_intake()로
    걸러내지만, 그 확인과 이 INSERT 사이에 동시 요청이 끼어들 수 있는 경합
    (TOCTOU)이 이론적으로 있었다(2026-09-10 감사에서 발견). 이미 있으면
    ValueError를 낸다 — 호출부가 이를 "이미 등록됨"으로 처리해야 한다.
    """
    conn = get_conn()
    if intake_id:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT id FROM inspections WHERE intake_id = ? AND status != 'superseded'",
            (intake_id,),
        ).fetchone()
        if existing is not None:
            conn.rollback()
            conn.close()
            raise ValueError(f"intake_id={intake_id}에 이미 활성 성적서(#{existing['id']})가 있어.")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO inspections (intake_id, material_no, material_name, supplier, po_number,
                                  receive_date, inspect_date, inspector, quantity,
                                  overall_result, status, est_time_label, actual_time_sec,
                                  total_time_sec, created_by_user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
    """, (intake_id, header["material_no"], header.get("material_name"), header.get("supplier"),
          header.get("po_number"), header.get("receive_date"), header.get("inspect_date"),
          header.get("inspector"), header.get("quantity"), overall_result,
          est_time_label, actual_time_sec, total_time_sec, created_by_user_id))
    inspection_id = cur.lastrowid

    if intake_id:
        cur.execute("UPDATE intake_list SET status = '검사완료' WHERE id = ?", (intake_id,))

    for it in items_with_results:
        cur.execute("""
            INSERT INTO inspection_items (inspection_id, item_name, measured_value,
                                           max_value, min_value, result, gauge_expiry, gauge_name, part_material_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (inspection_id, it["item_name"], it.get("measured_value"),
              it.get("max_value"), it.get("min_value"), it.get("result"),
              it.get("gauge_expiry"), it.get("gauge_name"), it.get("part_material_no") or header["material_no"]))

    conn.commit()
    conn.close()
    return inspection_id


def rename_inspection_item(item_id, new_item_name):
    """inspection_items 한 행의 item_name만 고친다.

    자재 규격에 항목기호(item_name)가 중복 등록됐다가 하나를 다른 기호로 고친 뒤,
    이미 저장된 성적서 쪽의 item_name도 맞춰줘야 할 때 쓰는 정정용 함수
    (2026-08-30, 실제 데이터 사고 수습 — 정상 플로우에서는 쓰이지 않음)."""
    conn = get_conn()
    conn.execute("UPDATE inspection_items SET item_name=? WHERE id=?", (new_item_name, item_id))
    conn.commit()
    conn.close()


def set_item_override(inspection_id, part_material_no, item_name, override_result, reason,
                       user_id, user_name):
    """검사 항목의 자동판정 결과를 사람이 사후에 합격/불합격으로 덮어쓴다.
    inspection_items.result 원본은 건드리지 않는다."""
    conn = get_conn()
    existing = conn.execute(
        "SELECT id FROM inspection_item_overrides WHERE inspection_id=? AND part_material_no=? AND item_name=?",
        (inspection_id, part_material_no, item_name)).fetchone()
    if existing:
        conn.execute("""
            UPDATE inspection_item_overrides
               SET override_result=?, reason=?, created_by_user_id=?, created_by_name=?,
                   created_at=datetime('now','localtime')
             WHERE id=?
        """, (override_result, reason, user_id, user_name, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO inspection_item_overrides
                (inspection_id, part_material_no, item_name, override_result, reason,
                 created_by_user_id, created_by_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (inspection_id, part_material_no, item_name, override_result, reason, user_id, user_name))
    conn.commit()
    conn.close()


def clear_item_override(inspection_id, part_material_no, item_name):
    """오버라이드를 지워서 자동판정 결과로 되돌린다."""
    conn = get_conn()
    conn.execute(
        "DELETE FROM inspection_item_overrides WHERE inspection_id=? AND part_material_no=? AND item_name=?",
        (inspection_id, part_material_no, item_name))
    conn.commit()
    conn.close()


def update_inspection_overall_result(inspection_id, overall_result):
    """항목 오버라이드 반영 후 성적서 전체 판정(표시용)을 갱신한다.
    status/approval_type/서명/content_hash/pdf_hash는 절대 건드리지 않는다 —
    최종 결정 자체를 바꾸려면 여전히 승인회수 후 재결정이 필요하다."""
    conn = get_conn()
    conn.execute("UPDATE inspections SET overall_result=? WHERE id=?", (overall_result, inspection_id))
    conn.commit()
    conn.close()


def sync_material_names_from_master():
    """specs.material_name(등록 당시 복사본)이 materials.material_name(정본)과 어긋난
    자재들을 바로잡고, 그 여파로 material_name이 빈 채 저장된 성적서(inspections)도
    같이 채운다. 원인: 성적서 생성 코드가 예전엔 정본(materials) 대신 이 복사본을
    읽어서, 복사본이 비어있거나 오래된 자재는 검사이력·승인 화면에 자재명이
    안 보였다(2026-09-01 실사용자 리포트로 발견). 코드는 이미 정본을 보게 고쳤고,
    이건 기존에 어긋나 있던 데이터 자체를 정리하는 일회성 정리 함수.
    반환: {"specs_fixed": N, "inspections_fixed": N}"""
    conn = get_conn()
    materials_rows = conn.execute(
        "SELECT material_no, material_name FROM materials WHERE material_name IS NOT NULL AND material_name != ''"
    ).fetchall()
    specs_fixed = 0
    insp_fixed = 0
    for m in materials_rows:
        cur = conn.execute(
            "UPDATE specs SET material_name = ? WHERE material_no = ? AND (material_name IS NULL OR material_name != ?)",
            (m["material_name"], m["material_no"], m["material_name"]),
        )
        specs_fixed += cur.rowcount
        cur = conn.execute(
            "UPDATE inspections SET material_name = ? WHERE material_no = ? AND (material_name IS NULL OR material_name = '')",
            (m["material_name"], m["material_no"]),
        )
        insp_fixed += cur.rowcount
    conn.commit()
    conn.close()
    return {"specs_fixed": specs_fixed, "inspections_fixed": insp_fixed}


def update_inspection_items(inspection_id, inspect_date, inspector, items_with_results, overall_result,
                             est_time_label=None, actual_time_sec=None, total_time_sec=None):
    """pending 상태 성적서 측정값·판정 전체 갱신"""
    conn = get_conn()
    header_row = conn.execute("SELECT material_no FROM inspections WHERE id=?", (inspection_id,)).fetchone()
    default_material_no = header_row["material_no"] if header_row else None
    if est_time_label is not None or actual_time_sec is not None:
        conn.execute("""
            UPDATE inspections SET inspect_date=?, inspector=?, overall_result=?,
                                    est_time_label=?, actual_time_sec=?, total_time_sec=? WHERE id=?
        """, (inspect_date, inspector, overall_result, est_time_label, actual_time_sec, total_time_sec, inspection_id))
    else:
        conn.execute("""
            UPDATE inspections SET inspect_date=?, inspector=?, overall_result=? WHERE id=?
        """, (inspect_date, inspector, overall_result, inspection_id))
    conn.execute("DELETE FROM inspection_items WHERE inspection_id=?", (inspection_id,))
    for it in items_with_results:
        conn.execute("""
            INSERT INTO inspection_items (inspection_id, item_name, measured_value, max_value, min_value, result, gauge_expiry, gauge_name, part_material_no)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (inspection_id, it["item_name"], it.get("measured_value"),
              it.get("max_value"), it.get("min_value"), it.get("result"),
              it.get("gauge_expiry"), it.get("gauge_name"), it.get("part_material_no") or default_material_no))
    conn.commit()
    conn.close()


def get_inspection(inspection_id):
    conn = get_conn()
    header = conn.execute(
        "SELECT * FROM inspections WHERE id = ?", (inspection_id,)
    ).fetchone()
    # specs 테이블에서 spec_display·AQL·검사방법 조인 — part_material_no(조립품이면 부품별 실제 자재,
    # 일반 검사면 헤더 자재와 동일)와 항목명이 같으면 매핑
    items = conn.execute("""
        SELECT ii.*,
               COALESCE(s.spec_display, ii.item_name) AS spec_display,
               s.aql AS aql,
               s.inspect_method AS inspect_method,
               s.lower_limit AS lower_limit,
               s.upper_limit AS upper_limit,
               s.judge_type AS judge_type,
               s.material_name AS part_material_name,
               io.override_result AS override_result,
               io.reason          AS override_reason,
               io.created_by_name AS override_by,
               io.created_at      AS override_at
        FROM inspection_items ii
        LEFT JOIN specs s
               ON s.material_no = COALESCE(ii.part_material_no,
                                            (SELECT material_no FROM inspections WHERE id = ?))
              AND s.item_name   = ii.item_name
        LEFT JOIN inspection_item_overrides io
               ON io.inspection_id = ii.inspection_id
              AND io.part_material_no = COALESCE(ii.part_material_no,
                                            (SELECT material_no FROM inspections WHERE id = ?))
              AND io.item_name = ii.item_name
        WHERE ii.inspection_id = ?
        ORDER BY ii.id
    """, (inspection_id, inspection_id, inspection_id)).fetchall()
    conn.close()
    return header, items


def list_inspections(status=None):
    conn = get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM inspections WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM inspections ORDER BY id DESC"
        ).fetchall()
    conn.close()
    return rows


def list_output_history(q="", date_from="", date_to=""):
    """이미 PDF/xlsx가 생성된 성적서 목록 (출력 이력).
    q로 자재번호·자재명·업체·승인자 검색, date_from/date_to로 승인일 범위 필터."""
    conn = get_conn()
    sql = "SELECT * FROM inspections WHERE pdf_path IS NOT NULL"
    params = []
    if q:
        sql += " AND (material_no LIKE ? OR material_name LIKE ? OR supplier LIKE ? OR approver LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like, like]
    if date_from:
        sql += " AND DATE(approved_at) >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND DATE(approved_at) <= ?"
        params.append(date_to)
    sql += " ORDER BY approved_at DESC, id DESC"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def list_pending_output_inspections():
    """승인은 됐지만 아직 PDF/xlsx 출력을 안 한 성적서들."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM inspections
        WHERE status = 'approved' AND pdf_path IS NULL
        ORDER BY id DESC
    """).fetchall()
    conn.close()
    return rows


def get_today_stats():
    """금일 현황 — 입고/검사/불량/소요시간(인원별)."""
    from datetime import date
    today = date.today().isoformat()
    conn = get_conn()

    # 오늘 입고 건수/품목수
    intake_rows = conn.execute(
        "SELECT COUNT(*) as cnt, COUNT(DISTINCT material_no) as items FROM intake_list WHERE date(created_at) = ?",
        (today,)
    ).fetchone()

    # 오늘 검사 완료 건수 — 제출(created_at)이 아니라 실제 검사한 날짜(inspect_date) 기준.
    # 검사를 오늘 했어도 제출은 나중에(또는 그 반대) 할 수 있어서 created_at으로 집계하면 어긋난다.
    inspected = conn.execute(
        "SELECT COUNT(*) FROM inspections WHERE date(inspect_date) = ?", (today,)
    ).fetchone()[0]

    # 오늘 불량/검토필요 건수
    defects = conn.execute(
        "SELECT COUNT(*) FROM inspections WHERE date(inspect_date) = ? AND overall_result NOT IN ('합격', '')",
        (today,)
    ).fetchone()[0]

    # 인원별 검사 소요시간 (오늘) — total_time_sec(성적서 총 측정시간) 우선, 없으면 actual_time_sec fallback
    time_rows = conn.execute("""
        SELECT inspector,
               SUM(COALESCE(total_time_sec, actual_time_sec, 0)) as total_sec
        FROM inspections
        WHERE date(inspect_date) = ?
          AND (total_time_sec > 0 OR actual_time_sec > 0)
        GROUP BY inspector
    """, (today,)).fetchall()

    conn.close()

    return {
        "intake_count": intake_rows["cnt"] if intake_rows else 0,
        "intake_items": intake_rows["items"] if intake_rows else 0,
        "inspected": inspected,
        "defects": defects,
        "time_by_user": [{"inspector": r["inspector"], "total_sec": r["total_sec"]} for r in time_rows],
    }


def get_defect_history(start_date=None, end_date=None):
    """기간 내 불합격/검토필요 성적서 목록 + 업체별/자재별 통계."""
    conn = get_conn()

    # 기간 조건은 두 쿼리가 똑같이 쓰므로 한 번만 만든다
    period_sql = ""
    period_params = []
    if start_date:
        period_sql += " AND i.inspect_date >= ?"
        period_params.append(start_date)
    if end_date:
        period_sql += " AND i.inspect_date <= ?"
        period_params.append(end_date)

    # ncr_count — 이 성적서로 부적합 통보서가 이미 작성됐는지 (불량 이력 화면의 후속조치 안내용)
    rows = conn.execute(
        f"""SELECT i.*,
                   (SELECT COUNT(*) FROM ncr n WHERE n.inspection_id = i.id) AS ncr_count
              FROM inspections i
             WHERE i.overall_result IS NOT NULL
               AND i.overall_result NOT IN ('합격', '')
                   {period_sql}
             ORDER BY i.inspect_date DESC, i.id DESC""",
        period_params
    ).fetchall()

    # 전체 검사 건수 (같은 기간) — 불량률 계산용
    total_rows = conn.execute(
        f"""SELECT i.supplier, COUNT(*) AS cnt
              FROM inspections i
             WHERE 1=1 {period_sql}
             GROUP BY i.supplier""",
        period_params
    ).fetchall()
    total_by_supplier = {r["supplier"]: r["cnt"] for r in total_rows}

    conn.close()

    # 업체별 집계
    supplier_stats = {}
    for r in rows:
        s = r["supplier"] or "(미입력)"
        if s not in supplier_stats:
            supplier_stats[s] = {"supplier": s, "defects": 0,
                                  "total": total_by_supplier.get(r["supplier"], 0)}
        supplier_stats[s]["defects"] += 1

    by_supplier = sorted(supplier_stats.values(), key=lambda x: x["defects"], reverse=True)
    for s in by_supplier:
        s["rate"] = round(s["defects"] / s["total"] * 100, 1) if s["total"] else 0

    return {"rows": rows, "by_supplier": by_supplier}


def get_defect_followup(completed_start=None, completed_end=None):
    """후속조치 추적 전용 — 각 단계별로 분류된 목록을 반환."""
    conn = get_conn()

    def q(sql, params=()):
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # 1. 재검사 대기 — 반려됨, 아직 재검사 안 됨
    #    재검사됐으면 원본이 superseded로 바뀌므로 rejected만 보면 됨
    recheck = q("""
        SELECT i.id, i.material_no, m.material_name, i.supplier, i.inspector,
               i.inspect_date, i.receive_date, i.reject_reason, i.status
          FROM inspections i
          LEFT JOIN materials m ON m.material_no = i.material_no
         WHERE i.status = 'rejected'
         ORDER BY i.inspect_date DESC, i.id DESC
    """)

    # 2. 통보서 작성 필요 — 불합격 항목 하나라도 있는 성적서(상태 무관) + NCR 없음 + 대체 아님
    #    B안: 승인 여부와 무관하게 fail 항목이 있으면 통보서 대상(pending 포함)
    #    ncr_waived=1(통보서 불필요 처리)인 건은 제외 — 대신 아래 6번 버킷으로 따로 뺀다.
    ncr_write = q("""
        SELECT DISTINCT i.id, i.material_no, m.material_name, i.supplier, i.inspector,
               i.inspect_date, i.receive_date, i.remark_approver, i.status, i.overall_result
          FROM inspections i
          LEFT JOIN materials m ON m.material_no = i.material_no
         INNER JOIN inspection_items ii ON ii.inspection_id = i.id
         WHERE ii.result NOT IN ('합격', '미측정', '', '규격미입력')
           AND ii.result IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM ncr n WHERE n.inspection_id = i.id)
           AND i.status != 'superseded'
           AND (i.ncr_waived IS NULL OR i.ncr_waived = 0)
         ORDER BY i.inspect_date DESC, i.id DESC
    """)

    # 6. 통보서 불필요 처리됨 — 위 2번과 조건은 같지만 ncr_waived=1인 건만. 작성 대기열에서
    #    빠졌다고 사라진 게 아니라 여기로 옮겨져서 계속 추적 가능하게 한다.
    ncr_waived = q("""
        SELECT DISTINCT i.id, i.material_no, m.material_name, i.supplier, i.inspector,
               i.inspect_date, i.receive_date, i.ncr_waived_reason, i.status, i.overall_result
          FROM inspections i
          LEFT JOIN materials m ON m.material_no = i.material_no
         INNER JOIN inspection_items ii ON ii.inspection_id = i.id
         WHERE ii.result NOT IN ('합격', '미측정', '', '규격미입력')
           AND ii.result IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM ncr n WHERE n.inspection_id = i.id)
           AND i.status != 'superseded'
           AND i.ncr_waived = 1
         ORDER BY i.inspect_date DESC, i.id DESC
    """)

    # 3. 통보서 확인 필요 — NCR draft 상태
    ncr_review = q("""
        SELECT n.id AS ncr_id, n.ncr_no, n.material_no, m.material_name, n.supplier,
               n.issued_by, n.created_at, n.due_date, n.defect_description,
               i.inspect_date, i.receive_date, i.inspector, i.id AS inspection_id
          FROM ncr n
          LEFT JOIN materials m ON m.material_no = n.material_no
          LEFT JOIN inspections i ON i.id = n.inspection_id
         WHERE n.status = 'draft'
         ORDER BY n.created_at DESC, n.id DESC
    """)

    # 4. 통보서 발송 필요 — NCR confirmed 상태
    ncr_send = q("""
        SELECT n.id AS ncr_id, n.ncr_no, n.material_no, m.material_name, n.supplier,
               n.issued_by, n.confirmed_by, n.confirmed_at, n.due_date, n.defect_description,
               i.inspect_date, i.receive_date, i.inspector, i.id AS inspection_id
          FROM ncr n
          LEFT JOIN materials m ON m.material_no = n.material_no
          LEFT JOIN inspections i ON i.id = n.inspection_id
         WHERE n.status = 'confirmed'
         ORDER BY n.confirmed_at DESC, n.id DESC
    """)

    # 5. 완료 — NCR sent (기간 필터로 archive 조회)
    period_sql = ""
    period_params = []
    if completed_start:
        period_sql += " AND date(n.email_sent_at) >= ?"
        period_params.append(completed_start)
    if completed_end:
        period_sql += " AND date(n.email_sent_at) <= ?"
        period_params.append(completed_end)

    completed = q(f"""
        SELECT n.id AS ncr_id, n.ncr_no, n.material_no, m.material_name, n.supplier,
               n.sent_to, n.email_sent_at, n.issued_by, n.confirmed_by, n.defect_description,
               i.inspect_date, i.receive_date, i.inspector, i.id AS inspection_id
          FROM ncr n
          LEFT JOIN materials m ON m.material_no = n.material_no
          LEFT JOIN inspections i ON i.id = n.inspection_id
         WHERE n.status = 'sent' {period_sql}
         ORDER BY n.email_sent_at DESC, n.id DESC
    """, period_params)

    conn.close()
    return {
        "recheck": recheck,
        "ncr_write": ncr_write,
        "ncr_review": ncr_review,
        "ncr_send": ncr_send,
        "completed": completed,
        "ncr_waived": ncr_waived,
    }


def get_material_inspection_history(material_no):
    """자재번호로 전체 검사 이력 + 항목별 측정값을 반환."""
    conn = get_conn()
    inspections = conn.execute("""
        SELECT id, inspect_date, supplier, inspector, overall_result, status, approval_type
        FROM inspections WHERE material_no = ?
        ORDER BY inspect_date ASC, id ASC
    """, (material_no,)).fetchall()

    items_by_inspection = {}
    for insp in inspections:
        rows = conn.execute("""
            SELECT item_name, measured_value, result, max_value, min_value
            FROM inspection_items WHERE inspection_id = ?
            ORDER BY id
        """, (insp["id"],)).fetchall()
        items_by_inspection[insp["id"]] = rows

    conn.close()
    return inspections, items_by_inspection


def get_repeat_defects(min_count=3):
    """업체+자재 조합별 불량 건수, min_count 이상인 것만 반환."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT supplier, material_no, material_name, COUNT(*) as cnt,
               MAX(inspect_date) as last_date
        FROM inspections
        WHERE overall_result NOT IN ('합격', '') AND overall_result IS NOT NULL
          AND overall_result != ''
        GROUP BY supplier, material_no
        HAVING COUNT(*) >= ?
        ORDER BY cnt DESC, last_date DESC
    """, (min_count,)).fetchall()
    conn.close()
    return rows


def get_defect_count_for(supplier, material_no):
    """특정 업체+자재의 전체 불량 건수 반환."""
    if not supplier or not material_no:
        return 0
    conn = get_conn()
    count = conn.execute("""
        SELECT COUNT(*) FROM inspections
        WHERE supplier = ? AND material_no = ?
          AND overall_result NOT IN ('합격', '') AND overall_result IS NOT NULL
          AND overall_result != ''
    """, (supplier, material_no)).fetchone()[0]
    conn.close()
    return count


# ---------- 계측기 마스터 ----------

# 측정 방식(specs.inspect_method)에 적히지만 계측기가 아닌 값들.
# (육안/외관=시각검사, 전수=검사 범위) — 계측기 종류 자동등록·자동매칭 대상에서 뺀다.
NON_GAUGE_METHODS = {"육안", "외관", "전수"}

def distinct_inspect_methods():
    """규격에 실제로 쓰인 측정 방식들을 (이름, 사용건수)로 돌려준다. 빈칸 제외.
    계측기 종류 자동등록·드롭다운 옵션의 원본이 된다."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT TRIM(inspect_method) AS m, COUNT(*) AS c
          FROM specs
         WHERE inspect_method IS NOT NULL AND TRIM(inspect_method) <> ''
      GROUP BY TRIM(inspect_method)
      ORDER BY c DESC
    """).fetchall()
    conn.close()
    return [(r["m"], r["c"]) for r in rows]

def list_gauges():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM gauge_master"
    ).fetchall()
    conn.close()
    return rows

def search_gauges(query=None):
    """계측기 검색 — 관리번호/이름/모델/위치를 단일 자유텍스트로 훑는다
    (suppliers.html과 동일한 방식, 필드가 적어 드롭다운형 검색은 과함)."""
    conn = get_conn()
    if not query:
        rows = conn.execute("SELECT * FROM gauge_master").fetchall()
        conn.close()
        return rows
    like = f"%{query}%"
    rows = conn.execute("""
        SELECT * FROM gauge_master
         WHERE gauge_no LIKE ? OR name LIKE ? OR model LIKE ? OR location LIKE ?
    """, (like, like, like, like)).fetchall()
    conn.close()
    return rows

def get_gauge(gauge_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM gauge_master WHERE id = ?", (gauge_id,)).fetchone()
    conn.close()
    return row

def upsert_gauge(gauge_id, gauge_no, name, model, location, last_calibrated, expiry_date, notes):
    conn = get_conn()
    if gauge_id:
        conn.execute("""
            UPDATE gauge_master SET gauge_no=?, name=?, model=?, location=?,
              last_calibrated=?, expiry_date=?, notes=? WHERE id=?
        """, (gauge_no, name, model, location, last_calibrated, expiry_date, notes, gauge_id))
    else:
        conn.execute("""
            INSERT INTO gauge_master (gauge_no, name, model, location, last_calibrated, expiry_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (gauge_no, name, model, location, last_calibrated, expiry_date, notes))
    conn.commit()
    conn.close()

def delete_gauge(gauge_id):
    conn = get_conn()
    conn.execute("DELETE FROM gauge_master WHERE id = ?", (gauge_id,))
    conn.commit()
    conn.close()

def get_gauge_master_warnings(days=30):
    """만료일이 days일 이내(이미 만료 포함)인 계측기 목록."""
    from datetime import date, timedelta
    today = date.today().isoformat()
    limit = (date.today() + timedelta(days=days)).isoformat()
    conn = get_conn()
    rows = conn.execute("""
        SELECT *, julianday(expiry_date) - julianday('now') as days_left
        FROM gauge_master
        WHERE expiry_date IS NOT NULL AND expiry_date != ''
          AND expiry_date <= ?
        ORDER BY expiry_date ASC
    """, (limit,)).fetchall()
    conn.close()
    return rows


def get_gauge_expiry_warnings(days=15):
    """검교정 유효기간이 days일 이내인 항목 목록."""
    from datetime import date, timedelta
    today = date.today().isoformat()
    limit = (date.today() + timedelta(days=days)).isoformat()
    conn = get_conn()
    rows = conn.execute("""
        SELECT ii.gauge_expiry, ii.item_name, i.material_no, i.supplier
        FROM inspection_items ii
        JOIN inspections i ON ii.inspection_id = i.id
        WHERE ii.gauge_expiry IS NOT NULL
          AND ii.gauge_expiry != ''
          AND ii.gauge_expiry <= ?
          AND ii.gauge_expiry >= ?
        ORDER BY ii.gauge_expiry ASC
    """, (limit, today)).fetchall()
    conn.close()
    return rows


# ---------- 업체 정보 ----------

def list_suppliers():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
    conn.close()
    return rows

def get_supplier(name):
    conn = get_conn()
    row = conn.execute("SELECT * FROM suppliers WHERE name = ?", (name,)).fetchone()
    conn.close()
    return row

def upsert_supplier(name, email, contact, notes, address="", biz_no="", contact_name="", contact2="", items=""):
    conn = get_conn()
    conn.execute("""
        INSERT INTO suppliers (name, email, contact, notes, address, biz_no, contact_name, contact2, items)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET email=excluded.email,
            contact=excluded.contact, notes=excluded.notes,
            address=excluded.address, biz_no=excluded.biz_no,
            contact_name=excluded.contact_name, contact2=excluded.contact2,
            items=excluded.items
    """, (name, email, contact, notes, address, biz_no, contact_name, contact2, items))
    conn.commit()
    conn.close()

def delete_supplier(name):
    conn = get_conn()
    conn.execute("DELETE FROM supplier_contacts WHERE supplier_name = ?", (name,))
    conn.execute("DELETE FROM suppliers WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def search_suppliers(query=None):
    """업체 검색 — 단일 자유텍스트로 업체명·주소·취급품목·(레거시)연락처 필드·
    담당자(역할/이름/전화/이메일)까지 전부 훑는다. 필드 지정 없이 아무 정보로나
    찾는 게 비개발자 사용자에게 더 직관적이라 spec.html의 by= 드롭다운과는 다르게
    필드 선택 없이 만들었다."""
    conn = get_conn()
    if not query:
        rows = conn.execute("SELECT * FROM suppliers ORDER BY name").fetchall()
        conn.close()
        return rows
    like = f"%{query}%"
    rows = conn.execute("""
        SELECT DISTINCT s.* FROM suppliers s
        LEFT JOIN supplier_contacts c ON c.supplier_name = s.name
        WHERE s.name LIKE ? OR s.items LIKE ? OR s.address LIKE ?
           OR s.contact_name LIKE ? OR s.contact LIKE ? OR s.contact2 LIKE ? OR s.email LIKE ?
           OR c.role LIKE ? OR c.contact_name LIKE ? OR c.phone LIKE ? OR c.email LIKE ?
        ORDER BY s.name
    """, (like,) * 11).fetchall()
    conn.close()
    return rows


# ---------- 업체 담당자 (역할별 다중 등록) ----------

def list_supplier_contacts(supplier_name):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM supplier_contacts WHERE supplier_name = ? ORDER BY sort_order, id",
        (supplier_name,)
    ).fetchall()
    conn.close()
    return rows


def add_supplier_contact(supplier_name, role, contact_name, phone, email, notes):
    conn = get_conn()
    max_order = conn.execute(
        "SELECT MAX(sort_order) FROM supplier_contacts WHERE supplier_name = ?", (supplier_name,)
    ).fetchone()[0]
    sort_order = (max_order or 0) + 1
    cur = conn.execute("""
        INSERT INTO supplier_contacts (supplier_name, role, contact_name, phone, email, notes, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (supplier_name, role or "기타", contact_name, phone, email, notes, sort_order))
    conn.commit()
    contact_id = cur.lastrowid
    conn.close()
    return contact_id


def update_supplier_contact(contact_id, role, contact_name, phone, email, notes):
    conn = get_conn()
    conn.execute("""
        UPDATE supplier_contacts SET role=?, contact_name=?, phone=?, email=?, notes=?
        WHERE id=?
    """, (role or "기타", contact_name, phone, email, notes, contact_id))
    conn.commit()
    conn.close()


def delete_supplier_contact(contact_id):
    conn = get_conn()
    conn.execute("DELETE FROM supplier_contacts WHERE id = ?", (contact_id,))
    conn.commit()
    conn.close()


_CONTACT_ROLE_PRIORITY = {
    "ncr": ["품질", "영업", "구매"],
    "report": ["영업", "품질", "구매"],
    "improvement": ["품질", "영업", "구매"],
}


def get_default_contact(supplier_name, purpose):
    """문서 종류(purpose: 'ncr' 또는 'report')에 맞는 기본 발송 대상 담당자를 고른다.

    role 우선순위에 맞는 담당자 중 sort_order가 가장 빠른 사람 → 없으면 등록된
    담당자 중 첫 번째 → 그마저 없으면 suppliers의 옛 단일 필드(email/contact_name)로
    폴백한다. 반환 형식은 기존 supplier_info["email"] 사용부와 호환되는 dict.
    """
    contacts = list_supplier_contacts(supplier_name)
    if contacts:
        priority = _CONTACT_ROLE_PRIORITY.get(purpose, [])
        for role in priority:
            for c in contacts:
                if c["role"] == role and c["email"]:
                    return {"email": c["email"], "contact_name": c["contact_name"], "role": c["role"]}
        for c in contacts:
            if c["email"]:
                return {"email": c["email"], "contact_name": c["contact_name"], "role": c["role"]}
    supplier = get_supplier(supplier_name)
    if supplier:
        return {"email": supplier["email"] or "", "contact_name": supplier["contact_name"] or "", "role": None}
    return {"email": "", "contact_name": "", "role": None}


# ---------- 부적합 통보서 (NCR) ----------

def _next_ncr_no():
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    conn = get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM ncr WHERE ncr_no LIKE ?", (f"NCR-{today}-%",)
    ).fetchone()[0]
    conn.close()
    return f"NCR-{today}-{count + 1:03d}"

def create_ncr(inspection_id, material_no, material_name, supplier, defect_description,
               action_required, due_date, issued_by, issued_date, lot_number=None, receive_date=None,
               cc_recipient=None, sample_qty=None, defect_qty=None, special_note=None, lot_qty=None,
               occurrence_type='입고검사', defect_type=None):
    ncr_no = _next_ncr_no()
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO ncr (ncr_no, inspection_id, material_no, material_name, supplier,
            defect_description, action_required, due_date, issued_by, issued_date, status,
            lot_number, receive_date, cc_recipient, sample_qty, defect_qty, special_note, lot_qty,
            occurrence_type, defect_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ncr_no, inspection_id, material_no, material_name, supplier,
          defect_description, action_required, due_date, issued_by, issued_date,
          lot_number, receive_date, cc_recipient, sample_qty, defect_qty, special_note, lot_qty,
          occurrence_type, defect_type))
    conn.commit()
    ncr_id = cur.lastrowid
    conn.close()
    return ncr_id, ncr_no


def update_ncr(ncr_id, material_no, material_name, supplier, defect_description,
               due_date, issued_date, lot_number=None, receive_date=None,
               cc_recipient=None, sample_qty=None, defect_qty=None, special_note=None,
               lot_qty=None, occurrence_type='입고검사', defect_type=None):
    """부적합 통보서 내용 수정. 발송/확인 여부와 무관하게 항상 저장하되,
    내용이 바뀐 문서에 옛 확인/발송 흔적이 남지 않도록 status를 'draft'로
    되돌리고 confirmed_by/confirmed_at/confirm_signature/email_sent_at/sent_to를
    같이 지운다(8-2-10절의 '승인 회수 시 서명·해시 초기화'와 같은 원칙)."""
    conn = get_conn()
    conn.execute("""
        UPDATE ncr SET
            material_no = ?, material_name = ?, supplier = ?,
            defect_description = ?, due_date = ?, issued_date = ?,
            lot_number = ?, receive_date = ?, cc_recipient = ?,
            sample_qty = ?, defect_qty = ?, special_note = ?, lot_qty = ?,
            occurrence_type = ?, defect_type = ?,
            status = 'draft', confirmed_by = NULL, confirmed_at = NULL,
            confirm_signature = NULL, email_sent_at = NULL, sent_to = NULL
        WHERE id = ?
    """, (material_no, material_name, supplier, defect_description, due_date, issued_date,
          lot_number, receive_date, cc_recipient, sample_qty, defect_qty, special_note, lot_qty,
          occurrence_type, defect_type, ncr_id))
    conn.commit()
    conn.close()


def confirm_ncr(ncr_id, confirmed_by, signature_path=None):
    """부적합 통보서 확인 완료. 최종결정권자의 승인 서명 경로를 같이 저장한다."""
    conn = get_conn()
    conn.execute("""
        UPDATE ncr SET status = 'confirmed',
            confirmed_by = ?, confirmed_at = datetime('now','localtime'),
            confirm_signature = ?
        WHERE id = ?
    """, (confirmed_by, signature_path, ncr_id))
    conn.commit()
    conn.close()

def get_ncr(ncr_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM ncr WHERE id = ?", (ncr_id,)).fetchone()
    conn.close()
    return row

_NCR_LIST_SELECT = """
    SELECT n.*,
           i.inspector       AS insp_inspector,
           i.receive_date    AS insp_receive_date,
           i.inspect_date    AS insp_inspect_date,
           i.overall_result  AS insp_overall_result,
           i.status          AS insp_status,
           i.approval_type   AS insp_approval_type
    FROM ncr n
    LEFT JOIN inspections i ON i.id = n.inspection_id
"""


def list_ncr(inspection_id=None):
    """검사자/입고일/검사일/자동판정/승인상태로도 검색할 수 있게 원본 성적서(inspections)
    정보를 같이 조인해서 넘긴다(insp_ 접두어 — ncr.status와 헷갈리지 않게 구분)."""
    conn = get_conn()
    if inspection_id:
        rows = conn.execute(
            _NCR_LIST_SELECT + " WHERE n.inspection_id = ? ORDER BY n.id DESC", (inspection_id,)
        ).fetchall()
    else:
        rows = conn.execute(_NCR_LIST_SELECT + " ORDER BY n.id DESC").fetchall()
    conn.close()
    return rows

def add_ncr_photo(ncr_id, photo_path):
    import json
    conn = get_conn()
    row = conn.execute("SELECT photos FROM ncr WHERE id = ?", (ncr_id,)).fetchone()
    photos = json.loads(row["photos"] or "[]")
    photos.append(photo_path)
    conn.execute("UPDATE ncr SET photos = ? WHERE id = ?", (json.dumps(photos), ncr_id))
    conn.commit()
    conn.close()

def mark_ncr_email_sent(ncr_id, sent_to):
    conn = get_conn()
    conn.execute("""
        UPDATE ncr SET status = 'sent',
            email_sent_at = datetime('now','localtime'),
            sent_to = ?
        WHERE id = ?
    """, (sent_to, ncr_id))
    conn.commit()
    conn.close()


# ---------- 개선요청서 (부적합 통보서보다 가벼운 사전 조치 문서, 서명 없음) ----------

def _next_improvement_no():
    from datetime import date
    today = date.today().strftime("%Y%m%d")
    conn = get_conn()
    count = conn.execute(
        "SELECT COUNT(*) FROM improvement_requests WHERE request_no LIKE ?", (f"IMP-{today}-%",)
    ).fetchone()[0]
    conn.close()
    return f"IMP-{today}-{count + 1:03d}"


def create_improvement_request(inspection_id, material_no, material_name, supplier,
                                lot_number, po_number, lot_qty, defect_categories,
                                defect_category_etc, request_detail, confirmed_name,
                                issued_by, issued_date):
    request_no = _next_improvement_no()
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO improvement_requests (request_no, inspection_id, material_no, material_name,
            supplier, lot_number, po_number, lot_qty, defect_categories, defect_category_etc,
            request_detail, confirmed_name, issued_by, issued_date, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft')
    """, (request_no, inspection_id, material_no, material_name, supplier, lot_number,
          po_number, lot_qty, defect_categories, defect_category_etc, request_detail,
          confirmed_name, issued_by, issued_date))
    conn.commit()
    req_id = cur.lastrowid
    conn.close()
    return req_id, request_no


def update_improvement_request(req_id, material_no, material_name, supplier,
                                lot_number, po_number, lot_qty, defect_categories,
                                defect_category_etc, request_detail, confirmed_name,
                                issued_date):
    """개선요청서 내용 수정. NCR과 같은 원칙 — 발송 여부 무관하게 수정 허용하되
    저장 시 status를 'draft'로 되돌리고 발송 흔적을 지운다(서명 개념은 없음)."""
    conn = get_conn()
    conn.execute("""
        UPDATE improvement_requests SET
            material_no = ?, material_name = ?, supplier = ?, lot_number = ?, po_number = ?,
            lot_qty = ?, defect_categories = ?, defect_category_etc = ?, request_detail = ?,
            confirmed_name = ?, issued_date = ?,
            status = 'draft', email_sent_at = NULL, sent_to = NULL
        WHERE id = ?
    """, (material_no, material_name, supplier, lot_number, po_number, lot_qty,
          defect_categories, defect_category_etc, request_detail, confirmed_name,
          issued_date, req_id))
    conn.commit()
    conn.close()


def get_improvement_request(req_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM improvement_requests WHERE id = ?", (req_id,)).fetchone()
    conn.close()
    return row


_IMPROVEMENT_LIST_SELECT = """
    SELECT r.*,
           i.inspector       AS insp_inspector,
           i.receive_date    AS insp_receive_date,
           i.inspect_date    AS insp_inspect_date,
           i.overall_result  AS insp_overall_result,
           i.status          AS insp_status,
           i.approval_type   AS insp_approval_type
    FROM improvement_requests r
    LEFT JOIN inspections i ON i.id = r.inspection_id
"""


def list_improvement_requests(inspection_id=None):
    """성적서 정보를 LEFT JOIN해서 같이 넘긴다 — inspection_id가 NULL인 독립작성 건도
    정상 조회된다(insp_ 컬럼은 전부 NULL로 나올 뿐)."""
    conn = get_conn()
    if inspection_id:
        rows = conn.execute(
            _IMPROVEMENT_LIST_SELECT + " WHERE r.inspection_id = ? ORDER BY r.id DESC", (inspection_id,)
        ).fetchall()
    else:
        rows = conn.execute(_IMPROVEMENT_LIST_SELECT + " ORDER BY r.id DESC").fetchall()
    conn.close()
    return rows


def add_improvement_photo(req_id, kind, photo_path):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO improvement_request_photos (improvement_request_id, kind, photo_path) VALUES (?, ?, ?)",
        (req_id, kind, photo_path))
    conn.commit()
    photo_id = cur.lastrowid
    conn.close()
    return photo_id


def list_improvement_photos(req_id, kind=None):
    conn = get_conn()
    if kind:
        rows = conn.execute(
            "SELECT * FROM improvement_request_photos WHERE improvement_request_id = ? AND kind = ? ORDER BY id",
            (req_id, kind)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM improvement_request_photos WHERE improvement_request_id = ? ORDER BY id",
            (req_id,)).fetchall()
    conn.close()
    return rows


def delete_improvement_photo(photo_id):
    conn = get_conn()
    row = conn.execute("SELECT photo_path FROM improvement_request_photos WHERE id = ?", (photo_id,)).fetchone()
    conn.execute("DELETE FROM improvement_request_photos WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()
    return row["photo_path"] if row else None


def mark_improvement_sent(req_id, sent_to):
    conn = get_conn()
    conn.execute("""
        UPDATE improvement_requests SET status = 'sent',
            email_sent_at = datetime('now','localtime'),
            sent_to = ?
        WHERE id = ?
    """, (sent_to, req_id))
    conn.commit()
    conn.close()


def delete_improvement_requests(req_ids):
    """개선요청서 일괄 삭제. 첨부 사진의 실제 파일명 목록을 반환하니 호출부가 디스크에서도
    지울 것(delete_outbound_item()과 같은 계약)."""
    if not req_ids:
        return []
    conn = get_conn()
    placeholders = ",".join("?" * len(req_ids))
    photo_paths = [r["photo_path"] for r in conn.execute(
        f"SELECT photo_path FROM improvement_request_photos WHERE improvement_request_id IN ({placeholders})",
        req_ids).fetchall()]
    conn.execute(f"DELETE FROM improvement_request_photos WHERE improvement_request_id IN ({placeholders})", req_ids)
    conn.execute(f"DELETE FROM improvement_requests WHERE id IN ({placeholders})", req_ids)
    conn.commit()
    conn.close()
    return photo_paths


# ---------- 커스텀 성적서 템플릿 ----------

def list_custom_templates():
    """모든 커스텀 템플릿 + 각 템플릿에 지정된 자재 수."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT t.*,
               (SELECT COUNT(*) FROM materials m WHERE m.custom_template_id = t.id) AS material_count
          FROM custom_templates t
         ORDER BY t.updated_at DESC, t.id DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_custom_template(template_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM custom_templates WHERE id = ?", (template_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_custom_template(name, layout_json="[]", created_by=None,
                           canvas_w=495, canvas_h=700,
                           page_size="A4", orientation="portrait"):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO custom_templates
            (name, layout_json, created_by, canvas_w, canvas_h, page_size, orientation)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (name, layout_json, created_by, canvas_w, canvas_h, page_size, orientation))
    tid = cur.lastrowid
    conn.commit()
    conn.close()
    return tid


def update_custom_template(template_id, name=None, layout_json=None,
                           canvas_w=None, canvas_h=None,
                           page_size=None, orientation=None):
    fields, params = [], []
    for col, val in [("name", name), ("layout_json", layout_json),
                     ("canvas_w", canvas_w), ("canvas_h", canvas_h),
                     ("page_size", page_size), ("orientation", orientation)]:
        if val is not None:
            fields.append(f"{col} = ?")
            params.append(val)
    if not fields:
        return
    fields.append("updated_at = datetime('now', 'localtime')")
    params.append(template_id)
    conn = get_conn()
    conn.execute(f"UPDATE custom_templates SET {', '.join(fields)} WHERE id = ?", params)
    conn.commit()
    conn.close()


def delete_custom_template(template_id):
    """템플릿 삭제 — 이 템플릿을 쓰던 자재는 기본 양식으로 되돌린다."""
    conn = get_conn()
    conn.execute("UPDATE materials SET custom_template_id = NULL WHERE custom_template_id = ?",
                 (template_id,))
    conn.execute("DELETE FROM custom_templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()


def materials_for_template(template_id):
    """이 템플릿이 지정된 자재 목록."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT material_no, material_name FROM materials
         WHERE custom_template_id = ? ORDER BY material_no
    """, (template_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_material_template(material_no, template_id):
    """자재에 커스텀 템플릿 지정(None이면 기본 양식으로 되돌림)."""
    conn = get_conn()
    conn.execute("UPDATE materials SET custom_template_id = ? WHERE material_no = ?",
                 (template_id, material_no))
    conn.commit()
    conn.close()


def get_material_template_id(material_no):
    """자재에 지정된 커스텀 템플릿 id(없으면 None)."""
    conn = get_conn()
    row = conn.execute("SELECT custom_template_id FROM materials WHERE material_no = ?",
                       (material_no,)).fetchone()
    conn.close()
    return row["custom_template_id"] if row and row["custom_template_id"] else None


def latest_inspection_for_material(material_no):
    """이 자재의 가장 최근 승인 성적서(미리보기용). 없으면 None."""
    conn = get_conn()
    row = conn.execute("""
        SELECT * FROM inspections
         WHERE material_no = ? AND status = 'approved'
         ORDER BY id DESC LIMIT 1
    """, (material_no,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_prev_inspection_values(material_no):
    """이 자재의 가장 최근 성적서(superseded 제외)의 측정값을 반환. 없으면 None."""
    conn = get_conn()
    row = conn.execute("""
        SELECT id, inspector, inspect_date FROM inspections
         WHERE material_no=? AND status != 'superseded'
         ORDER BY id DESC LIMIT 1
    """, (material_no,)).fetchone()
    if not row:
        conn.close()
        return None
    items = conn.execute("""
        SELECT item_name, measured_value FROM inspection_items
         WHERE inspection_id=? ORDER BY id
    """, (row["id"],)).fetchall()
    conn.close()
    return {
        "inspection_id": row["id"],
        "inspector": row["inspector"] or "",
        "inspect_date": row["inspect_date"] or "",
        "values": {r["item_name"]: r["measured_value"] for r in items},
    }


# ---------- 전수검사 ----------

def get_full_inspect_config(material_no):
    """자재의 전수검사 설정 dict. 없으면 None."""
    import json as _j
    conn = get_conn()
    row = conn.execute("SELECT full_inspect_config FROM materials WHERE material_no = ?",
                       (material_no,)).fetchone()
    conn.close()
    if not row or not row["full_inspect_config"]:
        return None
    try:
        return _j.loads(row["full_inspect_config"])
    except Exception:
        return None


def set_full_inspect_config(material_no, config_dict):
    """전수검사 열 설정 저장. config_dict=None이면 해제."""
    import json as _j
    conn = get_conn()
    val = _j.dumps(config_dict, ensure_ascii=False) if config_dict is not None else None
    conn.execute("UPDATE materials SET full_inspect_config = ? WHERE material_no = ?",
                 (val, material_no))
    conn.commit()
    conn.close()


def get_or_create_full_inspection(inspection_id):
    """전수검사 헤더 조회 또는 신규 생성. dict 반환."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM full_inspections WHERE inspection_id = ?",
                       (inspection_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO full_inspections (inspection_id) VALUES (?)", (inspection_id,))
        conn.commit()
        row = conn.execute("SELECT * FROM full_inspections WHERE inspection_id = ?",
                           (inspection_id,)).fetchone()
    result = dict(row)
    conn.close()
    return result


def get_full_inspection(inspection_id):
    """전수검사 헤더 조회. 없으면 None."""
    conn = get_conn()
    row = conn.execute("SELECT * FROM full_inspections WHERE inspection_id = ?",
                       (inspection_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_full_inspection(inspection_id, inspect_date=None, complete_date=None, status=None):
    conn = get_conn()
    fi = conn.execute("SELECT id FROM full_inspections WHERE inspection_id = ?",
                      (inspection_id,)).fetchone()
    if fi is None:
        conn.close()
        return
    fields, params = [], []
    if inspect_date is not None:
        fields.append("inspect_date = ?"); params.append(inspect_date)
    if complete_date is not None:
        fields.append("complete_date = ?"); params.append(complete_date)
    if status is not None:
        fields.append("status = ?"); params.append(status)
    if fields:
        params.append(fi["id"])
        conn.execute(f"UPDATE full_inspections SET {', '.join(fields)} WHERE id = ?", params)
        conn.commit()
    conn.close()


def list_full_inspection_units(inspection_id):
    """unit_no 오름차순으로 유닛 목록 반환."""
    import json as _j
    conn = get_conn()
    fi = conn.execute("SELECT id FROM full_inspections WHERE inspection_id = ?",
                      (inspection_id,)).fetchone()
    if fi is None:
        conn.close()
        return []
    rows = conn.execute("""
        SELECT * FROM full_inspection_units
         WHERE full_inspection_id = ?
         ORDER BY unit_no
    """, (fi["id"],)).fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["values"] = _j.loads(d["values_json"] or "{}")
        except Exception:
            d["values"] = {}
        result.append(d)
    return result


def save_full_inspection_units(inspection_id, units):
    """units = [{"unit_no":1,"serial_no":"...","values":{...},"result":"OK","remark":""}]
    기존 전부 삭제 후 재삽입(upsert 대신 단순 교체)."""
    import json as _j
    conn = get_conn()
    fi = conn.execute("SELECT id FROM full_inspections WHERE inspection_id = ?",
                      (inspection_id,)).fetchone()
    if fi is None:
        conn.execute("INSERT INTO full_inspections (inspection_id) VALUES (?)", (inspection_id,))
        conn.commit()
        fi = conn.execute("SELECT id FROM full_inspections WHERE inspection_id = ?",
                          (inspection_id,)).fetchone()
    fid = fi["id"]
    conn.execute("DELETE FROM full_inspection_units WHERE full_inspection_id = ?", (fid,))
    for u in units:
        conn.execute("""
            INSERT INTO full_inspection_units
                (full_inspection_id, unit_no, serial_no, values_json, result, remark, gauge_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fid, u.get("unit_no", 0), u.get("serial_no", ""),
              _j.dumps(u.get("values", {}), ensure_ascii=False),
              u.get("result", ""), u.get("remark", ""), u.get("gauge_name", "")))
    conn.commit()
    conn.close()


def delete_full_inspection(inspection_id):
    conn = get_conn()
    fi = conn.execute("SELECT id FROM full_inspections WHERE inspection_id = ?",
                      (inspection_id,)).fetchone()
    if fi:
        conn.execute("DELETE FROM full_inspection_units WHERE full_inspection_id = ?", (fi["id"],))
        conn.execute("DELETE FROM full_inspections WHERE id = ?", (fi["id"],))
        conn.commit()
    conn.close()


# ---------- 앱 설정 ----------

def get_setting(key, default=None):
    conn = get_conn()
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value)
    )
    conn.commit()
    conn.close()


def update_inspection_status(inspection_id, status, approver=None, reject_reason=None,
                             approval_type=None, approved_at=None):
    """approved_at을 넘기면 그 값을 쓰고, 안 넘기면 현재시각(now, localtime)을 쓴다.
    (복구 등록 — 원래 승인됐던 날짜를 그대로 기록하고 싶을 때 씀.)
    """
    conn = get_conn()
    approved_at_sql = "?" if approved_at else "datetime('now', 'localtime')"
    params = [status, approver]
    if approved_at:
        params.append(approved_at)
    if approval_type is not None:
        conn.execute(f"""
            UPDATE inspections
            SET status = ?, approver = ?, approved_at = {approved_at_sql},
                reject_reason = ?, approval_type = ?
            WHERE id = ?
        """, (*params, reject_reason, approval_type, inspection_id))
    else:
        conn.execute(f"""
            UPDATE inspections
            SET status = ?, approver = ?, approved_at = {approved_at_sql},
                reject_reason = ?
            WHERE id = ?
        """, (*params, reject_reason, inspection_id))
    conn.commit()
    conn.close()


def set_report_files(inspection_id, signature_path=None, pdf_path=None, xlsx_path=None):
    conn = get_conn()
    conn.execute("""
        UPDATE inspections SET signature_path = ?, pdf_path = ? WHERE id = ?
    """, (signature_path, pdf_path, inspection_id))
    conn.commit()
    conn.close()


REMARK_FIELDS = {"inspector": "remark_inspector", "manager": "remark_manager", "approver": "remark_approver"}


def update_inspection_remark(inspection_id, role_key, text):
    """role_key: 'inspector' / 'manager' / 'approver' — 각자 자기 비고란만 갱신."""
    column = REMARK_FIELDS.get(role_key)
    if column is None:
        return
    conn = get_conn()
    conn.execute(f"UPDATE inspections SET {column} = ? WHERE id = ?", (text, inspection_id))
    conn.commit()
    conn.close()


# ---------- 검사 진행 현황 ----------

def _ip_get(conn, intake_id):
    import json
    row = conn.execute(
        "SELECT inspectors FROM inspection_progress WHERE intake_id = ?", (intake_id,)
    ).fetchone()
    return json.loads(row["inspectors"]) if row else []


def register_inspector(intake_id, name):
    """검사 입력폼 열 때 본인 이름을 진행 현황에 등록."""
    import json
    conn = get_conn()
    names = _ip_get(conn, intake_id)
    if name not in names:
        names.append(name)
    conn.execute("""
        INSERT INTO inspection_progress (intake_id, inspectors, updated_at)
        VALUES (?, ?, datetime('now','localtime'))
        ON CONFLICT(intake_id) DO UPDATE SET
            inspectors = excluded.inspectors,
            updated_at = excluded.updated_at
    """, (intake_id, json.dumps(names, ensure_ascii=False)))
    conn.commit()
    conn.close()


def withdraw_inspector(intake_id, name):
    """검사원 제외 — 본인 이름만 제거. 마지막 1명은 제외 불가(호출 전 확인 필요)."""
    import json
    conn = get_conn()
    names = _ip_get(conn, intake_id)
    names = [n for n in names if n != name]
    if names:
        conn.execute("""
            INSERT INTO inspection_progress (intake_id, inspectors, updated_at)
            VALUES (?, ?, datetime('now','localtime'))
            ON CONFLICT(intake_id) DO UPDATE SET
                inspectors = excluded.inspectors,
                updated_at = excluded.updated_at
        """, (intake_id, json.dumps(names, ensure_ascii=False)))
    else:
        conn.execute("DELETE FROM inspection_progress WHERE intake_id = ?", (intake_id,))
    conn.commit()
    conn.close()
    return names  # 남은 검사원 목록 반환


def clear_inspection_progress(intake_id):
    """검사 제출 완료 시 진행 현황 삭제."""
    conn = get_conn()
    conn.execute("DELETE FROM inspection_progress WHERE intake_id = ?", (intake_id,))
    conn.commit()
    conn.close()


def get_progress_by_intake_ids(intake_ids):
    """intake_id 목록에 대한 진행 현황 일괄 조회. 반환: {intake_id: [name, ...]}"""
    import json
    if not intake_ids:
        return {}
    conn = get_conn()
    placeholders = ",".join("?" for _ in intake_ids)
    rows = conn.execute(
        f"SELECT intake_id, inspectors FROM inspection_progress WHERE intake_id IN ({placeholders})",
        intake_ids
    ).fetchall()
    conn.close()
    return {r["intake_id"]: json.loads(r["inspectors"] or "[]") for r in rows}


# ---------- 반품 처리 ----------

def create_return_request(inspection_id, material_no, material_name, supplier,
                          return_date, reason, quantity, created_by):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO return_requests
            (inspection_id, material_no, material_name, supplier,
             return_date, reason, quantity, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (inspection_id, material_no, material_name, supplier,
          return_date, reason, quantity, created_by))
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid


def update_return_request(return_id, return_date, reason, quantity):
    """반품 처리 내용 수정(2026-09-16 신설). 반품 사유/날짜/수량이 바뀌면 이미 진행된
    처리(재납품대기/재검사완료 등)의 근거가 달라지므로, 상태를 '반품요청'으로 되돌리고
    resolved_inspection_id를 지운다(8-2-10절 승인회수 원칙과 같은 정신 — NCR/개선요청서
    수정 시 발송·확인 흔적을 지우는 것과 동일한 이유)."""
    conn = get_conn()
    conn.execute("""
        UPDATE return_requests SET
            return_date = ?, reason = ?, quantity = ?,
            status = '반품요청', resolved_inspection_id = NULL
        WHERE id = ?
    """, (return_date, reason, quantity, return_id))
    conn.commit()
    conn.close()


_RETURN_LIST_SELECT = """
    SELECT r.*,
           i.inspector       AS insp_inspector,
           i.receive_date    AS insp_receive_date,
           i.inspect_date    AS insp_inspect_date,
           i.overall_result  AS insp_overall_result,
           i.status          AS insp_status,
           i.approval_type   AS insp_approval_type
    FROM return_requests r
    LEFT JOIN inspections i ON i.id = r.inspection_id
"""


def list_return_requests(status=None):
    """검사자/입고일/검사일/자동판정/승인상태로도 검색할 수 있게 원본 성적서(inspections)
    정보를 같이 조인해서 넘긴다(insp_ 접두어 — return_requests.status와 헷갈리지 않게 구분)."""
    conn = get_conn()
    if status:
        rows = conn.execute(
            _RETURN_LIST_SELECT + " WHERE r.status = ? ORDER BY r.id DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(_RETURN_LIST_SELECT + " ORDER BY r.id DESC").fetchall()
    conn.close()
    return rows


def get_return_request(return_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM return_requests WHERE id = ?", (return_id,)).fetchone()
    conn.close()
    return row


def update_return_status(return_id, status, resolved_inspection_id=None):
    conn = get_conn()
    conn.execute("""
        UPDATE return_requests SET status = ?, resolved_inspection_id = ?
        WHERE id = ?
    """, (status, resolved_inspection_id, return_id))
    conn.commit()
    conn.close()


def get_return_requests_by_inspection(inspection_id):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM return_requests WHERE inspection_id = ? ORDER BY id DESC",
        (inspection_id,)
    ).fetchall()
    conn.close()
    return rows


def delete_return_requests(return_ids):
    """반품 요청 일괄 삭제 (admin 전용)."""
    if not return_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" * len(return_ids))
    conn.execute(f"DELETE FROM return_requests WHERE id IN ({placeholders})", return_ids)
    conn.commit()
    conn.close()


def delete_inspections(inspection_ids):
    """성적서 복수 삭제 (inspection_items 포함). pending/rejected 건은 연결 intake를 대기로 되돌림."""
    if not inspection_ids:
        return
    ids = list(inspection_ids)
    ph = ",".join("?" * len(ids))
    conn = get_conn()
    # pending/rejected 건의 intake_id 수집 → 나중에 대기로 복구
    rows = conn.execute(
        f"SELECT intake_id FROM inspections WHERE id IN ({ph}) AND status IN ('pending','rejected')",
        ids
    ).fetchall()
    intake_ids_to_reset = [r["intake_id"] for r in rows if r["intake_id"]]

    # 전수검사 자식 테이블 먼저 삭제 (FK 걸려 있음)
    fi_ids = [r["id"] for r in conn.execute(
        f"SELECT id FROM full_inspections WHERE inspection_id IN ({ph})", ids
    ).fetchall()]
    if fi_ids:
        fph = ",".join("?" * len(fi_ids))
        conn.execute(f"DELETE FROM full_inspection_units WHERE full_inspection_id IN ({fph})", fi_ids)
        conn.execute(f"DELETE FROM full_inspections WHERE id IN ({fph})", fi_ids)

    conn.execute(f"DELETE FROM inspection_items WHERE inspection_id IN ({ph})", ids)
    conn.execute(f"DELETE FROM inspections WHERE id IN ({ph})", ids)

    # 남은 inspection이 없는 intake만 대기로 되돌림
    for iid in intake_ids_to_reset:
        remaining = conn.execute(
            "SELECT COUNT(*) AS cnt FROM inspections WHERE intake_id=?", (iid,)
        ).fetchone()["cnt"]
        if remaining == 0:
            conn.execute("UPDATE intake_list SET status='대기' WHERE id=?", (iid,))

    conn.commit()
    conn.close()


def import_assembly_from_excel(excel_filepath):
    """MA 자동출력.xlsm의 DATABASE 시트에서 조립 제품 파츠 분해 정보를 읽어 DB에 임포트.

    DATABASE 시트 구조 (2026-08-23 확인):
      - MA명은 A1, D1, G1, J1, ... (3칸 간격) 에 있고
      - 그 MA의 파츠 자재번호는 바로 오른쪽 열(B, E, H, K, ...)의 1~8행에 있다.
      - 파츠 자재번호는 MA_성적서_최종.xlsx의 시트명과 1:1로 같다.
    (예전 구현은 A/D/G 열 자체를 파츠번호로 읽어서 7322·7311 같은 사내 약칭이 들어갔고,
     그 번호는 자재 마스터에 존재하지 않아 검사가 불가능했다 — 그래서 오른쪽 열로 바로잡음)

    반환: (성공 건수, 에러 메시지 또는 None)"""
    try:
        import openpyxl
    except ImportError:
        return 0, "openpyxl 미설치"

    wb = None
    try:
        wb = openpyxl.load_workbook(excel_filepath, read_only=True, data_only=True)
        ws = wb['DATABASE']
        # 1~8행 전체를 읽어둔다 (행1 = MA명 + 첫 파츠, 행2~8 = 나머지 파츠)
        data = [list(r) for r in ws.iter_rows(min_row=1, max_row=8, values_only=True)]
    except Exception as e:
        return 0, f"엑셀 파일 읽기 실패: {e}"
    finally:
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass

    if not data or not data[0]:
        return 0, "DATABASE 시트가 비어있어"

    def _cell(row_idx, col_idx):
        row = data[row_idx] if row_idx < len(data) else []
        v = row[col_idx] if col_idx < len(row) else None
        if v is None:
            return ""
        return str(v).strip()

    conn = get_conn()
    cur = conn.cursor()
    imported = 0

    try:
        # A(0)=MA명 / B(1)=파츠,  D(3)=MA명 / E(4)=파츠,  G(6)/H(7) ... 3칸 간격
        for ma_col in range(0, len(data[0]), 3):
            part_col = ma_col + 1
            assembly_no = _cell(0, ma_col)
            if not assembly_no:
                continue

            components = []
            for row_idx in range(0, 8):
                component_no = _cell(row_idx, part_col)
                if component_no:
                    components.append(component_no)
            if not components:
                continue

            # 재임포트 안전: 기존 MA는 파츠를 갈아끼운다(파일이 갱신되면 반영돼야 하므로)
            master = cur.execute(
                "SELECT id FROM assembly_masters WHERE assembly_no=?", (assembly_no,)
            ).fetchone()
            if master:
                assembly_id = master["id"]
                cur.execute("DELETE FROM assembly_components WHERE assembly_id=?", (assembly_id,))
                cur.execute(
                    "UPDATE assembly_masters SET assembly_name=?, component_count=? WHERE id=?",
                    (assembly_no, len(components), assembly_id),
                )
            else:
                cur.execute(
                    "INSERT INTO assembly_masters (assembly_no, assembly_name, component_count) VALUES (?, ?, ?)",
                    (assembly_no, assembly_no, len(components)),
                )
                assembly_id = cur.lastrowid

            for order, component_no in enumerate(components, start=1):
                # 파츠명은 자재 마스터(성적서에서 뽑은 품명)를 정본으로 쓴다 — 여기선 번호만 저장
                cur.execute(
                    "INSERT INTO assembly_components (assembly_id, component_order, component_no, component_name) "
                    "VALUES (?, ?, ?, ?)",
                    (assembly_id, order, component_no, ""),
                )

            imported += 1

        conn.commit()
    finally:
        conn.close()

    return imported, None


# ---------- 통합BOM 계층 정보 (assembly_masters와 무관, 조회/필터 전용) ----------

_BOM_ALLOWED_UNITS = {"pc", "set", "ea"}


def import_bom_from_excel(filepath):
    """통합BOM 엑셀("통합BOM" 시트)에서 자재별 계층(모델/Lv/상위품목코드) 정보를 읽어
    material_bom_links에 전량 재삽입한다(재임포트 시 기존 데이터는 전부 삭제 후 다시 채움).

    시트 구조(2026-09-09 확인, 헤더 2행/데이터 3행부터):
      1~5=Lv1~Lv5(그 중 하나만 값 있음)  6=모델명  7=Rev(안씀)  8=품목코드
      9=규격사양(SPEC, bom_name으로 참고 저장 — materials.material_name은 안 건드림)
      10=소요량  11=단위  12=1대당누적  13=상위품목코드  14=구분  15=적용모델수(안씀)  16~=모델별 매트릭스(안씀)

    구분이 '완제품'인 행은 제외. 단위가 Pc/SET/EA(대소문자·공백 무시)가 아니면 제외.
    반환: {"imported", "skipped_unit", "skipped_no_code", "skipped_finished_good", "skipped_no_level"}"""
    import openpyxl

    wb = openpyxl.load_workbook(filepath, data_only=True)
    try:
        ws = wb["통합BOM"]
        rows_raw = list(ws.iter_rows(min_row=3, values_only=True))
    finally:
        wb.close()

    summary = {"imported": 0, "skipped_unit": 0, "skipped_no_code": 0,
               "skipped_finished_good": 0, "skipped_no_level": 0}
    rows_to_insert = []

    for row_no, row in enumerate(rows_raw, start=3):
        if row is None or all(v is None for v in row):
            continue
        row = list(row) + [None] * max(0, 15 - len(row))  # 15열 미만 방어

        kind = str(row[13]).strip() if row[13] is not None else None
        if kind == "완제품":
            summary["skipped_finished_good"] += 1
            continue

        unit_raw = row[10]
        unit_norm = str(unit_raw).strip().lower() if unit_raw is not None else ""
        if unit_norm not in _BOM_ALLOWED_UNITS:
            summary["skipped_unit"] += 1
            continue

        material_no = str(row[7]).strip() if row[7] is not None else ""
        if not material_no:
            summary["skipped_no_code"] += 1
            continue

        level = None
        for lv_idx in range(0, 5):
            v = row[lv_idx]
            if v is not None and str(v).strip() != "":
                level = lv_idx + 1
                break
        if level is None:
            summary["skipped_no_level"] += 1
            continue

        model_name = str(row[5]).strip() if row[5] is not None else ""
        parent_no = str(row[12]).strip() if row[12] is not None else None
        bom_name = str(row[8]).strip() if row[8] is not None else None

        def _num(v):
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        rows_to_insert.append((
            material_no, parent_no or None, model_name, level, kind,
            _num(row[9]), _num(row[11]), str(row[10]).strip() if row[10] is not None else None,
            row_no, bom_name or None,
        ))
        summary["imported"] += 1

    conn = get_conn()
    try:
        conn.execute("DELETE FROM material_bom_links")
        conn.executemany("""
            INSERT INTO material_bom_links
                (material_no, parent_material_no, model_name, level, kind,
                 qty_per_parent, qty_per_model, unit, source_row_no, bom_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows_to_insert)
        conn.commit()
    finally:
        conn.close()

    return summary


def get_bom_links_for_material(material_no):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM material_bom_links WHERE material_no = ? ORDER BY model_name, level",
        (material_no,)
    ).fetchall()
    conn.close()
    return rows


def list_bom_model_names():
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT model_name FROM material_bom_links ORDER BY model_name"
    ).fetchall()
    conn.close()
    return [r["model_name"] for r in rows]


def search_bom_materials(query="", levels=None, models=None, parent_no="", category=None,
                          unregistered_only=False, include=None, exclude=None):
    """material_bom_links를 materials에 LEFT JOIN해서 조회("자재 찾기" 화면 전용).
    search_materials()와는 완전히 별개 함수(책임 분리).
    include/exclude: 포함/제외 다중 단어(자재번호·자재명만 대상 — 확정된 결정).
    _include_exclude_clause() 참고."""
    conn = get_conn()
    where = ["1=1"]
    params = []

    if unregistered_only:
        where.append("m.material_no IS NULL")

    query = (query or "").strip()
    if query:
        where.append("(b.material_no LIKE ? OR m.material_name LIKE ?)")
        like = f"%{query}%"
        params += [like, like]

    levels = [l for l in (levels or []) if str(l).strip()]
    if levels:
        placeholders = ",".join("?" * len(levels))
        where.append(f"b.level IN ({placeholders})")
        params += [int(l) for l in levels]

    models = [mo for mo in (models or []) if mo]
    if models:
        placeholders = ",".join("?" * len(models))
        where.append(f"b.model_name IN ({placeholders})")
        params += models

    parent_no = (parent_no or "").strip()
    if parent_no:
        where.append("b.parent_material_no LIKE ?")
        params.append(f"%{parent_no}%")

    category = (category or "").strip()
    if category:
        where.append("m.category = ?")
        params.append(category)

    ie_expr, ie_params = _include_exclude_clause(["b.material_no", "m.material_name"], include, exclude)
    if ie_expr:
        where.append(ie_expr)
        params += ie_params

    sql = f"""
        SELECT b.id, b.material_no, m.material_name, b.bom_name, b.level, b.model_name,
               b.parent_material_no, b.kind, b.qty_per_parent, b.qty_per_model, b.unit, m.category
        FROM material_bom_links b
        LEFT JOIN materials m ON m.material_no = b.material_no
        WHERE {" AND ".join(where)}
        ORDER BY b.material_no, b.model_name, b.level
    """
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def delete_bom_links_bulk(link_ids):
    """'자재 찾기'에서 선택한 BOM 연동 행만 삭제. 자재(materials)나 규격은 안 건드림 —
    material_bom_links는 조회/필터 전용 참고 데이터라 잘못 들어온 행만 지워도 안전하다."""
    if not link_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" * len(link_ids))
    conn.execute(f"DELETE FROM material_bom_links WHERE id IN ({placeholders})", link_ids)
    conn.commit()
    conn.close()


def count_unregistered_bom_materials():
    """material_bom_links에는 있는데 materials에는 없는 자재번호 수(자재 찾기 화면 상단 안내용)."""
    conn = get_conn()
    n = conn.execute("""
        SELECT COUNT(DISTINCT b.material_no) FROM material_bom_links b
        LEFT JOIN materials m ON m.material_no = b.material_no
        WHERE m.material_no IS NULL
    """).fetchone()[0]
    conn.close()
    return n


def get_assembly_by_no(assembly_no):
    """MA 조립 제품 정보 조회. 반환: (assembly_id, 파츠 리스트) 또는 (None, [])"""
    conn = get_conn()
    cur = conn.cursor()

    master = cur.execute(
        "SELECT id FROM assembly_masters WHERE assembly_no=?",
        (assembly_no,)
    ).fetchone()

    if not master:
        conn.close()
        return None, []

    assembly_id = master['id']
    components = cur.execute(
        "SELECT component_order, component_no, component_name FROM assembly_components WHERE assembly_id=? ORDER BY component_order",
        (assembly_id,)
    ).fetchall()

    conn.close()
    return assembly_id, [dict(c) for c in components]


def list_all_assemblies(query=None, search_by="all"):
    """조립품 목록 + 각 조립품의 파츠 수. 자재 마스터에 없는 파츠 수(missing)도 같이 센다.

    query: 검색어. search_by: 'assembly_no'(조립품번호) / 'component_no'(파츠번호) / 'all'(둘 다).
    """
    conn = get_conn()
    sql = """
        SELECT m.*,
               (SELECT COUNT(*) FROM assembly_components c WHERE c.assembly_id = m.id) AS part_count,
               (SELECT COUNT(*) FROM assembly_components c
                 WHERE c.assembly_id = m.id
                   AND NOT EXISTS (SELECT 1 FROM materials mt WHERE mt.material_no = c.component_no)
               ) AS missing_count
          FROM assembly_masters m
    """
    params = []
    if query:
        like = f"%{query}%"
        component_match = ("EXISTS (SELECT 1 FROM assembly_components c "
                            "WHERE c.assembly_id = m.id AND c.component_no LIKE ?)")
        if search_by == "component_no":
            sql += f" WHERE {component_match}"
            params.append(like)
        elif search_by == "assembly_no":
            sql += " WHERE m.assembly_no LIKE ?"
            params.append(like)
        else:
            sql += f" WHERE m.assembly_no LIKE ? OR {component_match}"
            params.extend([like, like])
    sql += " ORDER BY m.assembly_no"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_assembly(assembly_id):
    """조립품 1건 + 파츠 목록(자재명·규격수 포함). 반환: (master, parts) 또는 (None, [])"""
    conn = get_conn()
    master = conn.execute("SELECT * FROM assembly_masters WHERE id=?", (assembly_id,)).fetchone()
    if not master:
        conn.close()
        return None, []
    parts = conn.execute("""
        SELECT c.*,
               mt.material_name,
               (SELECT COUNT(*) FROM specs s WHERE s.material_no = c.component_no) AS spec_count
          FROM assembly_components c
          LEFT JOIN materials mt ON mt.material_no = c.component_no
         WHERE c.assembly_id = ?
         ORDER BY c.component_order
    """, (assembly_id,)).fetchall()
    conn.close()
    return dict(master), [dict(p) for p in parts]


def save_assembly(assembly_no, component_nos, assembly_id=None):
    """조립품 하나를 통째로 저장(신규 등록 또는 수정).
    component_nos: 파츠 자재번호 리스트(순서대로). 파츠는 항상 전체 교체된다.
    반환: (assembly_id, 에러메시지 또는 None)"""
    assembly_no = (assembly_no or "").strip()
    if not assembly_no:
        return None, "조립품 이름(또는 대표 자재번호)을 입력해줘."

    seen, parts = set(), []
    for no in component_nos:
        no = (no or "").strip()
        if no and no not in seen:      # 같은 파츠를 두 번 넣는 건 막는다
            seen.add(no)
            parts.append(no)
    if not parts:
        return None, "파츠 자재번호를 하나 이상 입력해줘."

    conn = get_conn()
    try:
        dup = conn.execute(
            "SELECT id FROM assembly_masters WHERE assembly_no=? AND id IS NOT ?",
            (assembly_no, assembly_id),
        ).fetchone()
        if dup:
            return None, f"'{assembly_no}' 이름의 조립품이 이미 있어."

        if assembly_id:
            conn.execute("UPDATE assembly_masters SET assembly_no=?, assembly_name=?, component_count=? WHERE id=?",
                         (assembly_no, assembly_no, len(parts), assembly_id))
            conn.execute("DELETE FROM assembly_components WHERE assembly_id=?", (assembly_id,))
        else:
            cur = conn.execute(
                "INSERT INTO assembly_masters (assembly_no, assembly_name, component_count) VALUES (?, ?, ?)",
                (assembly_no, assembly_no, len(parts)))
            assembly_id = cur.lastrowid

        for order, no in enumerate(parts, start=1):
            conn.execute("INSERT INTO assembly_components (assembly_id, component_order, component_no, component_name) "
                         "VALUES (?, ?, ?, ?)", (assembly_id, order, no, ""))
        conn.commit()
        return assembly_id, None
    finally:
        conn.close()


def delete_assembly(assembly_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM assembly_components WHERE assembly_id=?", (assembly_id,))
        conn.execute("DELETE FROM assembly_masters WHERE id=?", (assembly_id,))
        conn.commit()
    finally:
        conn.close()


def delete_assemblies_bulk(assembly_ids):
    """조립품 여러 개를 한번에 삭제 — 조립품 관리 화면 선택삭제용."""
    if not assembly_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" for _ in assembly_ids)
    conn.execute(f"DELETE FROM assembly_components WHERE assembly_id IN ({placeholders})", assembly_ids)
    conn.execute(f"DELETE FROM assembly_masters WHERE id IN ({placeholders})", assembly_ids)
    conn.commit()
    conn.close()


# ---------- 검사 입력 임시저장 (서버 보관) ----------

def active_inspection_for_intake(intake_id):
    """이 입고 건에 이미 살아있는 성적서가 있으면 돌려준다(재검사로 대체된 건 제외).

    같은 입고 건에 성적서가 여러 개 생기면 대시보드에서 같은 로트 수량이 중복 집계된다.
    (실제로 등록 버튼 연타/새로고침으로 1초 간격 3건이 생긴 사례가 있었다)
    """
    if not intake_id:
        return None
    conn = get_conn()
    row = conn.execute("""SELECT * FROM inspections
                           WHERE intake_id = ? AND status != 'superseded'
                           ORDER BY id DESC LIMIT 1""", (intake_id,)).fetchone()
    conn.close()
    return row


def save_inspection_draft(intake_id, payload_json, user_id=None, username=None):
    """검사 입력 중간값을 서버에 저장. 같은 입고 건은 항상 덮어쓴다."""
    conn = get_conn()
    try:
        conn.execute("""
            INSERT INTO inspection_drafts (intake_id, user_id, username, payload, updated_at)
                 VALUES (?, ?, ?, ?, datetime('now','localtime'))
            ON CONFLICT(intake_id) DO UPDATE SET
                 user_id=excluded.user_id, username=excluded.username,
                 payload=excluded.payload, updated_at=excluded.updated_at
        """, (intake_id, user_id, username, payload_json))
        conn.commit()
    finally:
        conn.close()


def get_inspection_draft(intake_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM inspection_drafts WHERE intake_id=?", (intake_id,)).fetchone()
    conn.close()
    return row


def delete_inspection_draft(intake_id):
    conn = get_conn()
    conn.execute("DELETE FROM inspection_drafts WHERE intake_id=?", (intake_id,))
    conn.commit()
    conn.close()


# ---------- 4M 변경점 ----------

CHANGE_TYPES = [
    ("Man",      "사람 (작업자·교대 변경)"),
    ("Machine",  "설비 (금형·장비 교체)"),
    ("Material", "자재 (원자재·공급처 변경)"),
    ("Method",   "방법 (공정·조건 변경)"),
]


def add_change_point(supplier, material_no, change_type, change_date, description, reported_by):
    conn = get_conn()
    try:
        cur = conn.execute("""
            INSERT INTO change_points (supplier, material_no, change_type, change_date, description, reported_by)
                 VALUES (?, ?, ?, ?, ?, ?)
        """, (supplier, material_no or None, change_type, change_date, description, reported_by))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_change_points(supplier=None, material_no=None, limit=200):
    conn = get_conn()
    sql = "SELECT * FROM change_points WHERE 1=1"
    params = []
    if supplier:
        sql += " AND supplier = ?"
        params.append(supplier)
    if material_no:
        sql += " AND (material_no = ? OR material_no IS NULL)"
        params.append(material_no)
    sql += " ORDER BY change_date DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def delete_change_point(cp_id):
    conn = get_conn()
    conn.execute("DELETE FROM change_points WHERE id=?", (cp_id,))
    conn.commit()
    conn.close()


def recent_change_points_for(supplier, material_no, within_days=90):
    """이 업체·자재에 최근 변경점이 있었는지 — 검사 화면 경고용."""
    if not supplier:
        return []
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM change_points
         WHERE supplier = ?
           AND (material_no IS NULL OR material_no = ?)
           AND date(change_date) >= date('now', 'localtime', ?)
         ORDER BY change_date DESC
    """, (supplier, material_no, f"-{int(within_days)} days")).fetchall()
    conn.close()
    return rows


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


def delete_serials_bulk(serial_ids):
    """S/N 발급 이력을 여러 건 한 번에 삭제."""
    conn = get_conn()
    conn.executemany("DELETE FROM finished_goods_serials WHERE id=?",
                      [(sid,) for sid in serial_ids])
    conn.commit()
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


# ---------- 출고 배치/항목/사진 ----------

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


def confirm_outbound_batch(batch_id, confirmed_by, signature_path=None):
    """확인 시 이 배치의 항목 추가/수정/삭제·배치정보 수정을 잠근다(2026-09-15 변경 —
    예전엔 '확인은 순수 기록용, 잠금 아님'이 설계문서 확정사항이었으나 사용자가 명시적으로
    잠금 방식으로 바꿔달라고 요청해 뒤집었다). 실제 잠금 강제는 app.py의
    _outbound_batch_lock_response()가 각 수정 라우트 앞단에서 한다 — 이 함수 자체는
    여전히 단순 UPDATE다. 2026-09-21: 최종결정권자 서명 경로(confirm_signature)도 같이
    저장한다(사용자 확정 — 출고 확인도 승인/NCR/업체성적표처럼 서명이 필수인 최종 결정으로
    바뀌었다)."""
    conn = get_conn()
    conn.execute("""
        UPDATE outbound_batches SET confirmed_by=?, confirmed_at=datetime('now','localtime'),
               confirm_signature=?
        WHERE id=?
    """, (confirmed_by, signature_path, batch_id))
    conn.commit()
    conn.close()


def revoke_outbound_batch_confirm(batch_id):
    """출고 확인을 취소하고 잠금을 풀어준다. 2026-09-21: 서명이 생기면서 8-2-10절과
    같은 원칙 적용 — 확인이 취소됐는데 확인자 서명이 남아 있으면 안 된다. 성적서
    승인 회수와 같은 관례로 DB 포인터만 NULL로 하고 실제 PNG 파일은 지우지 않는다."""
    conn = get_conn()
    conn.execute("""
        UPDATE outbound_batches SET confirmed_by=NULL, confirmed_at=NULL, confirm_signature=NULL
        WHERE id=?
    """, (batch_id,))
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
        SELECT b.*, COUNT(i.id) AS item_count,
               (SELECT COUNT(*) FROM outbound_planned_items p WHERE p.batch_id = b.id) AS planned_count,
               (SELECT COUNT(*) FROM outbound_items i2
                 WHERE i2.batch_id = b.id
                   AND NOT EXISTS (SELECT 1 FROM outbound_planned_items p2
                                    WHERE p2.batch_id = i2.batch_id AND p2.serial_no = i2.serial_no)
               ) AS unplanned_count,
               (SELECT COUNT(*) FROM outbound_planned_items p3
                 WHERE p3.batch_id = b.id
                   AND NOT EXISTS (SELECT 1 FROM outbound_items i3
                                    WHERE i3.batch_id = p3.batch_id AND i3.serial_no = p3.serial_no)
               ) AS missing_count,
               (SELECT GROUP_CONCAT(DISTINCT i4.inspected_by) FROM outbound_items i4
                 WHERE i4.batch_id = b.id AND i4.inspected_by IS NOT NULL AND i4.inspected_by != ''
               ) AS inspectors
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


def delete_outbound_batches(batch_ids):
    """차수(배치)를 통째로 삭제한다. 배치 안의 스캔 항목·사진(DB row)·계획 S/N·
    QR 라벨 출력 이력까지 전부 같이 지운다 — FK 제약(PRAGMA foreign_keys=ON) 때문에
    자식 테이블부터 지워야 한다. 발급된 S/N 자체(finished_goods_serials)는 배치와
    무관한 별개 이력이라 손대지 않는다. 확인 완료(잠금) 여부는 확인하지 않는다 —
    '삭제'는 '수정'과 다른 개념이라 _outbound_batch_lock_response()의 잠금 대상이 아니다.
    반환: 지워진 사진의 실제 파일명 목록(호출부가 디스크에서도 지울 것 —
    delete_outbound_item()과 같은 계약)."""
    conn = get_conn()
    photo_paths = []
    for batch_id in batch_ids:
        rows = conn.execute("""
            SELECT file_path FROM outbound_item_photos
             WHERE item_id IN (SELECT id FROM outbound_items WHERE batch_id=?)
        """, (batch_id,)).fetchall()
        photo_paths += [r["file_path"] for r in rows]
        conn.execute("""
            DELETE FROM outbound_item_photos
             WHERE item_id IN (SELECT id FROM outbound_items WHERE batch_id=?)
        """, (batch_id,))
        conn.execute("DELETE FROM outbound_items WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM outbound_planned_items WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM outbound_qr_exports WHERE batch_id=?", (batch_id,))
        conn.execute("DELETE FROM outbound_batches WHERE id=?", (batch_id,))
    conn.commit()
    conn.close()
    return photo_paths


def add_outbound_item(batch_id, serial_no, product_name, quantity, inspected_by=None):
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO outbound_items (batch_id, serial_no, product_name, quantity, inspected_by)
        VALUES (?, ?, ?, ?, ?)
    """, (batch_id, serial_no, product_name, quantity, inspected_by))
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
        row["result_auto"] = compute_outbound_item_auto_result(row)
        row["result_effective"] = outbound_item_effective_result(row)
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


def add_outbound_item_photo(item_id, file_path, kind="indicator"):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO outbound_item_photos (item_id, file_path, kind) VALUES (?, ?, ?)",
        (item_id, file_path, kind))
    conn.commit()
    photo_id = cur.lastrowid
    conn.close()
    return photo_id


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


def outbound_body_photo_enabled():
    """관리자가 켠 경우에만 True. 기본값은 꺼짐(새 기능이므로 admin이 명시적으로
    켜기 전까지는 스캔 화면에 본체사진 입력 UI를 노출하지 않는다)."""
    return get_setting("outbound_body_photo_enabled", "0") == "1"


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


def delete_classify_rules_bulk(kind, codes):
    """kind('voltage'/'suffix'/'pcode') 하나의 매핑표에서 여러 code를 한 번에 삭제."""
    table = _OUTBOUND_RULE_TABLES[kind]
    conn = get_conn()
    conn.executemany(f"DELETE FROM {table} WHERE code=?", [(c,) for c in codes])
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


def _match_ckmr(serial_no, rules):
    """CKMR 리클로저 본체 패턴 매칭 + 접미사 코드 결정. classify_serial_no()와
    resolve_sticker_reference()가 공유한다(8-1절 원칙, 2026-09-21 추출).
    매칭 실패(정규식 자체가 안 맞음) 시 None. 성공 시 dict:
      volt_code: 전압코드 1자리 문자열
      suffix_raw: S/N에 실제로 붙은 접미사 원문(빈 문자열 가능)
      suffix_code: 매핑표에서 매칭된 코드. suffix_raw가 없으면 "", 매핑표에
                   없는 접미사면 None(=인식 실패), 매칭되면 그 코드 문자열
      pcode: 괄호 안 숫자 문자열 또는 None"""
    m = _CKMR_RE.match((serial_no or "").strip())
    if not m:
        return None
    volt_code, suffix_raw, pcode = m.group(1), m.group(2), m.group(3)
    suffix_code = ""
    if suffix_raw:
        suffix_code = None
        for code, label in rules["suffix"]:
            if suffix_raw.startswith(code):
                suffix_code = code
                break
    return {"volt_code": volt_code, "suffix_raw": suffix_raw,
            "suffix_code": suffix_code, "pcode": pcode}


# 2026-09-21 확정값(사용자 확인 완료) — 스티커 부착 기준 참고자료의 21개 CKMR 모델과
# 기존 출고 분류 규칙(전압코드/접미사코드/P코드)의 매칭표. resolve_sticker_reference()가
# S/N에서 뽑아낸 (volt_code, suffix_code, pcode)와 이 표의 값을 비교해서 모델을 찾는다.
# 이 표를 벗어난 model_no는 import_sticker_reference_excel()이 새로 발견했을 때
# 코드 3개를 NULL로 남기고, 관리자가 /outbound/rules 화면에서 수동으로 채운다.
STICKER_MODEL_MATCH_KEYS = {
    "CKMR1070": ("7", "S", None),
    "CKMR1080": ("7", "S", "19"),
    "CKMR1200": ("8", "S", "19"),
    "CKMR1280": ("8", "S", None),
    "CKMR3000": ("7", "HT3", None),
    "CKMR3010": ("7", "HAT3", None),
    "CKMR3050": ("7", "HT1", None),
    "CKMR3060": ("7", "HAT1", None),
    "CKMR3080": ("7", "HT3", "32"),
    "CKMR3500": ("8", "HAT3", None),
    "CKMR3510": ("8", "HT3", None),
    "CKMR3580": ("8", "HT1", None),
    "CKMR3590": ("8", "HT3", "32"),
    "CKMR4000": ("7", "H", None),
    "CKMR4020": ("7", "H", "42"),
    "CKMR4050": ("7", "HA", None),
    "CKMR6050": ("7", "VA", None),
    "CKMR8800": ("8", "H", None),
    "CKMR8830": ("8", "H", "42"),
    "CKMR8880": ("8", "HA", None),
    "CKMR9880": ("9", "H", None),
}

STICKER_REF_NOT_CKMR = "not_ckmr"
STICKER_REF_UNRECOGNIZED_CODE = "unrecognized_code"
STICKER_REF_NO_MODEL = "no_registered_model"


def ensure_sticker_reference_match_seed():
    """STICKER_MODEL_MATCH_KEYS 21건을 sticker_reference_models에 심는다. items/image_path는
    비워두고(관리자 업로드로만 채움, Task 6) 코드 3개만 채워서 resolve_sticker_reference()가
    업로드 이전에도 모델 식별은 가능하게 한다. INSERT OR IGNORE라 이미 있는 행(업로드로
    새로 생겼거나 관리자가 수동으로 채운 행 포함)은 절대 덮어쓰지 않는다. 삭제 기능이
    있으므로(Task 6) 매 재시작마다 다시 실행돼도 무해하게 설계 — 별도 settings 플래그 불필요."""
    conn = get_conn()
    for model_no, (volt, suf, pc) in STICKER_MODEL_MATCH_KEYS.items():
        conn.execute(
            "INSERT OR IGNORE INTO sticker_reference_models "
            "(model_no, voltage_code, suffix_code, pcode) VALUES (?, ?, ?, ?)",
            (model_no, volt, suf, pc))
    conn.commit()
    conn.close()


def bom_model_label(model_no):
    """material_bom_links.model_name(예: "CKMR1070 본체 15kV 단상")에서 이 model_no로
    시작하는 한글 표시명을 찾는다(18절 — assembly_masters와 무관). 못 찾으면 model_no
    자체를 돌려준다(값 유실 방지, 8-3절과 같은 원칙). 표시명은 별도 컬럼에 저장하지
    않고 항상 이 함수로 조회한다 — material_bom_links가 갱신되면 자동으로 최신 반영됨."""
    conn = get_conn()
    row = conn.execute(
        "SELECT model_name FROM material_bom_links "
        "WHERE model_name = ? OR model_name LIKE ? ORDER BY model_name LIMIT 1",
        (model_no, model_no + " %")).fetchone()
    conn.close()
    return row["model_name"] if row else model_no


def _sticker_reference_items(model_no):
    conn = get_conn()
    rows = conn.execute(
        "SELECT no, label, attach_face, qty FROM sticker_reference_items "
        "WHERE model_no=? ORDER BY sort_order", (model_no,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _sticker_reference_model_dict(row):
    """sticker_reference_models 행(sqlite3.Row) -> API/화면 공용 dict.
    resolve/search/list 세 곳 모두 이 함수 하나로 통일한다(8-1절)."""
    model_no = row["model_no"]
    return {
        "model_no": model_no,
        "display_name": bom_model_label(model_no),
        "voltage_code": row["voltage_code"] or "",
        "suffix_code": row["suffix_code"] or "",
        "pcode": row["pcode"] or "",
        "image_url": f"/static/sticker_reference/{row['image_path']}" if row["image_path"] else None,
        "has_image": bool(row["image_path"]),
        "items": _sticker_reference_items(model_no),
        "item_count": len(_sticker_reference_items(model_no)),
    }


def resolve_sticker_reference(serial_no, rules=None):
    """S/N으로 스티커 부착 기준 모델을 자동 매칭. 반환:
      성공: {"ok": True, "model": {...}}  (_sticker_reference_model_dict 형태)
      실패: {"ok": False, "reason": STICKER_REF_*, "message": "..."}"""
    serial_no = (serial_no or "").strip()
    if rules is None:
        rules = get_classify_rules()

    match = _match_ckmr(serial_no, rules)
    if match is None:
        return {"ok": False, "reason": STICKER_REF_NOT_CKMR,
                "message": "이 항목은 리클로저 본체가 아니라서 자동 매칭 대상이 아니야."}

    volt_label = rules["voltage"].get(match["volt_code"])
    if volt_label is None or (match["suffix_raw"] and match["suffix_code"] is None):
        return {"ok": False, "reason": STICKER_REF_UNRECOGNIZED_CODE,
                "message": "S/N의 전압코드 또는 접미사를 인식하지 못했어."}

    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM sticker_reference_models WHERE voltage_code=? AND suffix_code=? AND pcode IS ?",
        (match["volt_code"], match["suffix_code"] or "", match["pcode"])).fetchone()
    conn.close()
    if row is None:
        label = classify_serial_no(serial_no, rules) or volt_label
        return {"ok": False, "reason": STICKER_REF_NO_MODEL,
                "message": f"'{label}' 조합은 아직 등록된 도면이 없어."}

    return {"ok": True, "model": _sticker_reference_model_dict(row)}


def search_sticker_reference_models(query):
    """model_no 또는 BOM 한글 표시명으로 검색(자동매칭 실패 시 폴백 UI용). 행이 최대
    수십 건뿐이라 전체 스캔 후 파이썬에서 필터링한다(과설계 방지)."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM sticker_reference_models ORDER BY model_no").fetchall()
    conn.close()
    q = (query or "").strip().lower()
    results = []
    for row in rows:
        d = _sticker_reference_model_dict(row)
        if not q or q in row["model_no"].lower() or q in d["display_name"].lower():
            results.append(d)
    return results


def list_sticker_reference_models():
    """관리 화면(/outbound/rules 카드)용 전체 목록. _sticker_reference_model_dict()를
    그대로 재사용 — search와 다른 shape을 새로 만들지 않는다(8-1절)."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM sticker_reference_models ORDER BY model_no").fetchall()
    conn.close()
    return [_sticker_reference_model_dict(r) for r in rows]


def set_sticker_reference_codes(model_no, voltage_code, suffix_code, pcode):
    """관리자가 수동으로 매칭 코드 3개를 채우거나 고칠 때(신규 발견 모델 대응).
    빈 문자열은 NULL로 저장(=아직 미분류)."""
    conn = get_conn()
    conn.execute(
        "UPDATE sticker_reference_models SET voltage_code=?, suffix_code=?, pcode=?, "
        "updated_at=datetime('now','localtime') WHERE model_no=?",
        (voltage_code or None, suffix_code or None, pcode or None, model_no))
    conn.commit()
    conn.close()


def create_sticker_reference_model(model_no, voltage_code="", suffix_code="", pcode=""):
    """개별 신규 모델 등록 — model_no만 갖고 빈 모델(항목 없음, 이미지 없음)을 하나 만든다.
    이미 있는 model_no면 아무것도 안 하고 False를 돌려준다(엑셀 재업로드/코드수정 폼으로
    유도 — upsert로 조용히 덮어쓰면 기존 항목표를 실수로 날릴 위험이 있어서 막음).
    표시명은 별도 저장 안 함 — bom_model_label()이 항상 조회해서 계산한다."""
    model_no = (model_no or "").strip()
    if not model_no:
        return False
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM sticker_reference_models WHERE model_no=?", (model_no,)).fetchone()
    if exists:
        conn.close()
        return False
    conn.execute(
        "INSERT INTO sticker_reference_models (model_no, voltage_code, suffix_code, pcode) "
        "VALUES (?, ?, ?, ?)",
        (model_no, (voltage_code or "").strip() or None,
         (suffix_code or "").strip() or None, (pcode or "").strip() or None))
    conn.commit()
    conn.close()
    return True


def delete_sticker_reference_model(model_no):
    """모델 삭제 — sticker_reference_items(자식)부터 지우고 sticker_reference_models(부모)를
    지운다(SQLite는 기본 FK cascade가 없어 수동으로 순서를 지켜야 함). 이미지 파일은
    여기서 안 지운다 — 파일 경로는 app.py(STICKER_REFERENCE_DIR)만 알고 있으므로 실제
    os.remove는 호출부(app.py) 책임. 반환: 실제로 지웠으면 True, 없던 model_no면 False."""
    conn = get_conn()
    exists = conn.execute(
        "SELECT 1 FROM sticker_reference_models WHERE model_no=?", (model_no,)).fetchone()
    if not exists:
        conn.close()
        return False
    conn.execute("DELETE FROM sticker_reference_items WHERE model_no=?", (model_no,))
    conn.execute("DELETE FROM sticker_reference_models WHERE model_no=?", (model_no,))
    conn.commit()
    conn.close()
    return True


def import_sticker_reference_from_excel(filepath, image_dir):
    """스티커 부착 기준 참고파일("모델별 도면" 시트)을 파싱해서 sticker_reference_models/
    sticker_reference_items를 갱신하고 참고이미지를 image_dir에 저장한다.

    블록 구조(34행 간격, 첫 블록은 엑셀 1행부터):
      오프셋0: A=모델번호(CKMRxxxx)
      오프셋2~5: A=No. B=스티커명 C=부착 면 D=수량 (4행, 라벨 비어있으면 스킵)
      오프셋7: 참고이미지 1장 앵커(있으면 저장, 없으면 기존 이미지 보존)

    voltage_code/suffix_code/pcode는 이 파일에서 읽지 않는다 — 기존 행이 이미 채워져
    있으면 절대 덮어쓰지 않고(관리자 수동편집 보존), 새 model_no면 STICKER_MODEL_MATCH_KEYS에
    있는 값을 쓰거나 없으면 NULL로 남긴다.

    반환: {"imported": 갱신된 모델 수, "no_image": 이미지를 못 찾은 모델 수,
           "new_unmatched": 21개표에 없는 새 모델 수(코드 미배정, 관리자가 채워야 함)}"""
    import openpyxl
    os.makedirs(image_dir, exist_ok=True)

    wb = openpyxl.load_workbook(filepath, data_only=True)
    try:
        ws = wb["모델별 도면"]
        images_by_row = {}
        for img in ws._images:
            images_by_row[img.anchor._from.row] = img

        summary = {"imported": 0, "no_image": 0, "new_unmatched": 0}
        conn = get_conn()
        cur = conn.cursor()

        block_i = 0
        while block_i < 200:
            start0 = block_i * 34
            model_no = ws.cell(row=start0 + 1, column=1).value
            if model_no is None or str(model_no).strip() == "":
                break
            model_no = str(model_no).strip()

            items = []
            for r_off in range(2, 6):
                row1 = start0 + r_off + 1
                no_val = ws.cell(row=row1, column=1).value
                label = ws.cell(row=row1, column=2).value
                face = ws.cell(row=row1, column=3).value
                qty = ws.cell(row=row1, column=4).value
                if label is None or str(label).strip() == "":
                    continue
                items.append((
                    str(no_val).strip() if no_val is not None else "",
                    str(label).strip(),
                    str(face).strip() if face is not None else "",
                    str(qty).strip() if qty is not None else ""))

            existing = cur.execute(
                "SELECT voltage_code, suffix_code, pcode, image_path FROM sticker_reference_models WHERE model_no=?",
                (model_no,)).fetchone()

            if existing and (existing["voltage_code"] or existing["suffix_code"] or existing["pcode"]):
                voltage_code, suffix_code, pcode = existing["voltage_code"], existing["suffix_code"], existing["pcode"]
            else:
                keys = STICKER_MODEL_MATCH_KEYS.get(model_no)
                if keys:
                    voltage_code, suffix_code, pcode = keys
                else:
                    voltage_code, suffix_code, pcode = None, None, None
                    summary["new_unmatched"] += 1

            image_path = existing["image_path"] if existing else None
            img = images_by_row.get(start0 + 7)
            if img is not None:
                try:
                    img_bytes = img._data()
                except Exception:
                    img_bytes = None
                if img_bytes:
                    # 경로 이탈 방지 — model_no는 엑셀 셀 값이라 슬래시/역슬래시/".." 등이
                    # 섞여 들어올 수 있으므로 파일명에 안전한 문자만 남긴다(quality-watcher 지적).
                    safe_model_no = re.sub(r"[^A-Za-z0-9_-]", "", model_no)
                    fname = f"{safe_model_no}.png"
                    with open(os.path.join(image_dir, fname), "wb") as f:
                        f.write(img_bytes)
                    image_path = fname
            if image_path is None:
                summary["no_image"] += 1

            cur.execute("""
                INSERT INTO sticker_reference_models
                    (model_no, voltage_code, suffix_code, pcode, image_path, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
                ON CONFLICT(model_no) DO UPDATE SET
                    voltage_code=excluded.voltage_code, suffix_code=excluded.suffix_code,
                    pcode=excluded.pcode, image_path=excluded.image_path, updated_at=excluded.updated_at
            """, (model_no, voltage_code, suffix_code, pcode, image_path))

            cur.execute("DELETE FROM sticker_reference_items WHERE model_no=?", (model_no,))
            for order, (no_val, label, face, qty) in enumerate(items, start=1):
                cur.execute(
                    "INSERT INTO sticker_reference_items (model_no, no, label, attach_face, qty, sort_order) "
                    "VALUES (?, ?, ?, ?, ?, ?)", (model_no, no_val, label, face, qty, order))

            summary["imported"] += 1
            block_i += 1

        conn.commit()
        conn.close()
    finally:
        wb.close()

    return summary


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

    match = _match_ckmr(serial_no, rules)
    if match:
        volt_label = rules["voltage"].get(match["volt_code"])
        if volt_label is None:
            return None
        if match["suffix_raw"] and match["suffix_code"] is None:
            return None  # 접미사가 있는데 매핑표에 없으면 분류 불가(미확정 접미사)
        suffix_label = None
        if match["suffix_code"]:
            for code, label in rules["suffix"]:
                if code == match["suffix_code"]:
                    suffix_label = label
                    break
        parts = [volt_label]
        if suffix_label:
            parts.append(suffix_label)
        label = " ".join(parts)
        pcode = match["pcode"]
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


def outbound_plan_progress(batch_id):
    """차수 하나의 "계획 대비 스캔 진행상황"을 계산한다.

    반환: {"rows": [{"serial_no", "model_label", "status"}, ...],
           "summary": {"planned_total", "matched", "extra"}}
    status: 'confirmed'(스캔됨 + 필요 사진 전부 있음 + 9개 품질확인항목 전부 선택됨) /
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


def planned_item_exists(batch_id, serial_no):
    conn = get_conn()
    row = conn.execute(
        "SELECT 1 FROM outbound_planned_items WHERE batch_id=? AND serial_no=?",
        (batch_id, serial_no)).fetchone()
    conn.close()
    return row is not None


def outbound_batch_has_unplanned_items(batch_id):
    """차수 계획이 있는데(planned_items 1건 이상) 그 계획에 없는 항목이 이 배치에
    하나라도 있으면 True. 계획 자체가 없는 배치(자유 등록)는 애초에 "계획에 안
    맞는다"는 개념이 성립하지 않으므로 False — outbound_item_add()의 추가 차단
    로직과 같은 전제를 쓴다(2026-09-16, 사용자 확정: 계획 불일치 항목은 추가 자체를
    막고, 이미 들어와 있는 계획외 항목이 있으면 출고 확인도 막는다). outbound_batch_confirm()
    라우트가 이 함수로 확인 처리를 게이트한다."""
    conn = get_conn()
    planned_count = conn.execute(
        "SELECT COUNT(*) FROM outbound_planned_items WHERE batch_id=?", (batch_id,)).fetchone()[0]
    if planned_count == 0:
        conn.close()
        return False
    unplanned_count = conn.execute("""
        SELECT COUNT(*) FROM outbound_items i
         WHERE i.batch_id=? AND NOT EXISTS (
             SELECT 1 FROM outbound_planned_items p WHERE p.batch_id=i.batch_id AND p.serial_no=i.serial_no)
    """, (batch_id,)).fetchone()[0]
    conn.close()
    return unplanned_count > 0


def outbound_batch_has_unscanned_planned_items(batch_id):
    """차수 계획(outbound_planned_items)에 등록된 S/N 중 아직 스캔(outbound_items)되지
    않은 게 하나라도 있으면 True. 계획 자체가 없으면(자유 등록 배치) False —
    outbound_batch_has_unplanned_items()와 같은 전제(2026-09-21 사용자 확정: '계획에는
    있는데 아직 안 채워진' 반대 방향도 출고 확인을 막는다). outbound_batch_confirm()
    라우트가 이 함수로 확인 처리를 게이트한다."""
    conn = get_conn()
    planned_count = conn.execute(
        "SELECT COUNT(*) FROM outbound_planned_items WHERE batch_id=?", (batch_id,)).fetchone()[0]
    if planned_count == 0:
        conn.close()
        return False
    missing_count = conn.execute("""
        SELECT COUNT(*) FROM outbound_planned_items p
         WHERE p.batch_id=? AND NOT EXISTS (
             SELECT 1 FROM outbound_items i WHERE i.batch_id=p.batch_id AND i.serial_no=p.serial_no)
    """, (batch_id,)).fetchone()[0]
    conn.close()
    return missing_count > 0


def delete_planned_item(planned_id):
    conn = get_conn()
    conn.execute("DELETE FROM outbound_planned_items WHERE id=?", (planned_id,))
    conn.commit()
    conn.close()


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


def delete_qr_exports(export_ids):
    """QR 라벨 출력 이력을 여러 건 한 번에 삭제. 실제 출력 파일은 디스크에 저장 안 하고
    매번 즉석 생성해서 다운로드만 시키는 방식이라 DB row만 지우면 된다."""
    conn = get_conn()
    conn.executemany("DELETE FROM outbound_qr_exports WHERE id=?",
                      [(eid,) for eid in export_ids])
    conn.commit()
    conn.close()


# ---------- 성적서 위변조 검증 ----------

def set_inspection_hashes(inspection_id, content_hash=None, pdf_hash=None):
    """None으로 준 값은 기존 값을 그대로 둔다(승인 시 content, 출력 시 pdf 를 따로 채우기 위함)."""
    conn = get_conn()
    if content_hash is not None:
        conn.execute("UPDATE inspections SET content_hash=? WHERE id=?", (content_hash, inspection_id))
    if pdf_hash is not None:
        conn.execute("UPDATE inspections SET pdf_hash=? WHERE id=?", (pdf_hash, inspection_id))
    conn.commit()
    conn.close()


# ---------- 품질 현황 집계 (대시보드 / 보고서) ----------

PERIOD_TYPES = [
    ("daily",     "일간"),
    ("weekly",    "주간"),
    ("monthly",   "월간"),
    ("quarterly", "분기"),
    ("half",      "반기"),
    ("yearly",    "연간"),
]


def _period_key(date_str, period_type):
    """검사일을 기간 유형에 맞는 묶음 키로 바꾼다. 못 읽는 날짜는 None."""
    from datetime import date as _date
    text = str(date_str or "")[:10]
    try:
        y, m, d = int(text[0:4]), int(text[5:7]), int(text[8:10])
        dt = _date(y, m, d)
    except (ValueError, IndexError):
        return None
    if period_type == "daily":
        return f"{dt:%Y-%m-%d}"
    if period_type == "weekly":
        iso_y, iso_w, _ = dt.isocalendar()
        return f"{iso_y}-W{iso_w:02d}"
    if period_type == "monthly":
        return f"{dt:%Y-%m}"
    if period_type == "quarterly":
        return f"{dt.year}-Q{(dt.month - 1) // 3 + 1}"
    if period_type == "half":
        return f"{dt.year}-H{1 if dt.month <= 6 else 2}"
    return str(dt.year)


def _lot_state(status, approval_type, overall_result=None):
    """로트 하나의 최종 상태. 불량률은 '판정이 확정된 것'만으로 계산한다.

    superseded(재검사로 대체된 옛 성적서)는 집계에서 아예 빼야 한다.
    같은 입고 건을 재검사하면 성적서가 하나 더 생기는데, 옛 건까지 세면
    같은 로트의 수량이 두 번 잡혀서 검사 수량이 부풀려진다.

    overall_result 인자(2026-09-14, 항목 판정 수동 오버라이드 기능과 함께 도입):
    - 넘기면(집계·통계용 호출) 항목 오버라이드가 반영된 로트 상태를 돌려준다 —
      '합격승인(normal)'인데 오버라이드로 overall_result가 '합격'이 아니게 됐으면 '불합격'으로 재분류.
    - 안 넘기면(기본값 None, 공식 출력물용 호출) 예전 그대로 status+approval_type만 본다.
      공식 출력물(커스텀 자유양식 성적서 등)을 만드는 호출부는 절대 이 인자를 넘기면 안 된다.
    - 특채/불합격확정 로트는 오버라이드가 나중에 뭐가 되든 재분류하지 않는다
      (이미 서명받은 최종 결정을 오버라이드로 되돌리지 않기 위함).
    """
    if status == "superseded":
        return "대체됨"
    if status == "approved":
        if approval_type == "failed":
            return "불합격"
        if approval_type == "special":
            return "특채"
        if overall_result is not None and overall_result != "합격":
            return "불합격"
        return "합격"
    return "미결"      # pending / rejected


def _ncr_extra_stats(rows, manual_ncr_lot_qtys):
    """②③(NCR포함 불량률/건수율, 2026-09-15 사용자 확정)에 얹을 'NCR만 있는 로트'의
    수량 합·건수를 계산한다. quality_report()에서만 쓰는 헬퍼.

    이미 baseline(①, 불합격 확정)에서 잡힌 로트는 여기서 또 세면 이중집계이므로
    제외한다(8-2-9절 '재검사한 옛 성적서 집계 제외'와 같은 정신). 합격/특채/미결
    로트인데 발송·확인(status가 'sent' 또는 'confirmed') NCR이 딸려있으면 '실질
    불량이 이미 보고된 로트'로 보고 분자·분모 양쪽에 더한다.

    rows: quality_report()가 이미 superseded 제외 필터를 거친 inspections dict 리스트.
          각 dict에 'ncr_sent_count'(이 로트에 연결된 sent/confirmed NCR 개수,
          0 이상 정수), 'status', 'approval_type', 'overall_result', 'quantity',
          'id' 키가 있어야 한다.
    manual_ncr_lot_qtys: 성적서 미연결(inspection_id IS NULL) sent/confirmed NCR의
          lot_qty 문자열 리스트(예: "12대", "200"). 숫자 추출 실패한 건은 이 함수
          안에서 스킵한다.

    반환: (추가수량, 추가건수)
    """
    qty = 0
    cnt = 0
    for r in rows:
        if not r["ncr_sent_count"]:
            continue
        state = _lot_state(r["status"], r["approval_type"], r["overall_result"])
        if state == "불합격":
            continue  # 이미 ①(baseline)에서 분자·분모 둘 다 잡혀있음 — 이중집계 방지
        qty += int(r["quantity"] or 0)
        cnt += 1
    for lot_qty in manual_ncr_lot_qtys:
        m = re.search(r"\d+", str(lot_qty or ""))
        if not m:
            continue  # 수량 파싱 실패 — 근거 없는 숫자를 만들지 않기 위해 건수도 같이 스킵
        qty += int(m.group())
        cnt += 1
    return qty, cnt


LOT_STATES = ["합격", "특채", "불합격", "미결"]   # 화면 상태 필터에 쓰는 값


def daily_status(day=None):
    """금일(또는 지정일) 현황 — 홈 요약보다 자세한 하루치 상황판.

    "아침에 출근해서 무엇부터 해야 하는가"에 답하는 화면용 데이터.
    입고→검사→승인→출력 흐름이 각 단계에서 얼마나 밀려 있는지, 오늘 누가 얼마나 했는지,
    그리고 지금 손대야 할 일(미조치)이 뭔지를 한 번에 모아준다.
    """
    from datetime import date as _date
    day = day or _date.today().isoformat()
    conn = get_conn()

    def one(sql, params=()):
        return conn.execute(sql, params).fetchone()[0] or 0

    def rows(sql, params=()):
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # ── 오늘 들어온 것 ──
    intake_today = rows("""SELECT * FROM intake_list WHERE date(created_at)=?
                            ORDER BY id DESC""", (day,))
    intake_qty = sum(int(r["quantity"] or 0) for r in intake_today)

    # 업체별로 몇 건·몇 개 들어왔는지 + 그 업체의 실제 입고 행 목록도 같이 담아둔다
    # (2026-09-02: 예전엔 요약표와 전체 목록 두 섹션이 따로였는데, 업체별 접기/펼치기
    # 하나로 합치는 게 스크롤도 덜하고 "어느 업체에서 뭐가 왔는지"가 한눈에 보인다.)
    by_supplier = {}
    for r in intake_today:
        sup = r["supplier"] or "(미입력)"
        b = by_supplier.setdefault(sup, {"업체": sup, "건수": 0, "수량": 0, "목록": []})
        b["건수"] += 1
        b["수량"] += int(r["quantity"] or 0)
        b["목록"].append(r)
    by_supplier_list = sorted(by_supplier.values(), key=lambda x: -x["건수"])

    # ── 오늘 검사한 것 ── 제출(created_at)이 아니라 실제 검사한 날짜(inspect_date) 기준으로 집계한다.
    # 검사를 그날 했어도 제출은 다른 날 할 수 있어서, created_at으로 걸러면 검사자별 실적이 어긋난다.
    inspected = rows("""SELECT * FROM inspections WHERE date(inspect_date)=? AND status!='superseded'
                         ORDER BY id DESC""", (day,))
    insp_qty = sum(int(r["quantity"] or 0) for r in inspected)
    defect_today = [r for r in inspected if (r["overall_result"] or "") not in ("합격", "")]

    # ── 단계별 밀린 일 (오늘 것만이 아니라 '지금 쌓여 있는 것') ──
    backlog = {
        "검사대기": one("SELECT COUNT(*) FROM intake_list WHERE status='대기'"),
        "승인대기": one("SELECT COUNT(*) FROM inspections WHERE status='pending'"),
        "출력대기": one("""SELECT COUNT(*) FROM inspections
                            WHERE status='approved' AND (pdf_path IS NULL OR pdf_path='')"""),
        "반려": one("SELECT COUNT(*) FROM inspections WHERE status='rejected'"),
    }

    # ── 조치가 남아 있는 것 (이게 진짜 '해야 할 일') ──
    todo = {
        # 반려됐으니 다시 검사해야 하는 건
        "재검사필요": rows("""SELECT id, material_no, material_name, supplier, reject_reason, inspect_date
                                FROM inspections WHERE status='rejected' ORDER BY id DESC"""),
        # 불합격 확정인데 아직 부적합 통보서를 안 쓴 건
        "통보서작성필요": rows("""SELECT i.id, i.material_no, i.material_name, i.supplier, i.inspect_date
                                    FROM inspections i
                                   WHERE i.status='approved' AND i.approval_type='failed'
                                     AND NOT EXISTS (SELECT 1 FROM ncr n WHERE n.inspection_id=i.id)
                                   ORDER BY i.id DESC"""),
        # 통보서는 썼는데 아직 확인(승인) 안 된 건
        "통보서확인필요": rows("""SELECT id, ncr_no, material_no, supplier, issued_by, created_at
                                    FROM ncr WHERE status='draft' ORDER BY id DESC"""),
        # 확인은 됐는데 아직 협력사로 발송 안 한 건
        "통보서발송필요": rows("""SELECT id, ncr_no, material_no, supplier, confirmed_by, confirmed_at
                                    FROM ncr WHERE status='confirmed' ORDER BY id DESC"""),
    }

    # ── 오늘 누가 얼마나 했나 ──
    by_person = {}
    for r in inspected:
        p = by_person.setdefault(r["inspector"] or "(미입력)",
                                 {"검사자": r["inspector"] or "(미입력)", "건수": 0,
                                  "수량": 0, "총초": 0, "불량": 0})
        p["건수"] += 1
        p["수량"] += int(r["quantity"] or 0)
        p["총초"] += int(r["total_time_sec"] or 0) or int(r["actual_time_sec"] or 0)
        if (r["overall_result"] or "") not in ("합격", ""):
            p["불량"] += 1
    for p in by_person.values():
        p["평균초"] = int(round(p["총초"] / p["건수"])) if p["건수"] else 0

    # ── 오늘 승인/불합격 결정 ──
    decided = rows("""SELECT * FROM inspections
                       WHERE date(approved_at)=? ORDER BY approved_at DESC""", (day,))

    conn.close()

    # 검사 진행률 = 오늘 입고분 중 몇 건이나 검사가 끝났나
    done_pos = len([r for r in intake_today if r["status"] == "검사완료"])
    progress = round(done_pos / len(intake_today) * 100) if intake_today else 0

    return {
        "날짜": day,
        "입고": {"건수": len(intake_today), "수량": intake_qty,
                 "검사완료": done_pos, "진행률": progress, "목록": intake_today,
                 "업체별": by_supplier_list},
        "검사": {"건수": len(inspected), "수량": insp_qty,
                 "불량건수": len(defect_today), "목록": inspected, "불량목록": defect_today},
        "결정": {"건수": len(decided),
                 "합격": len([r for r in decided if r["approval_type"] == "normal"]),
                 "특채": len([r for r in decided if r["approval_type"] == "special"]),
                 "불합격": len([r for r in decided if r["approval_type"] == "failed"])},
        "밀린일": backlog,
        "해야할일": todo,
        "인원별": sorted(by_person.values(), key=lambda x: -x["건수"]),
    }


def quality_report(start_date, end_date, period_type="monthly",
                   supplier=None, po_number=None, material=None, states=None, category=None):
    """품질 현황 집계 — 대시보드·보고서·내보내기가 전부 이 함수 하나를 쓴다.

    불량률은 **수량 기준**이다(사용자 확정):
        불량률 = 불합격 확정 수량 / 판정 확정 수량 × 100
        PPM    = 불량률 × 10,000
    '판정 확정'은 합격 + 특채 + 불합격이고, 승인 대기·반려 건은 분모에서 뺀다
    (아직 결과가 아니므로). 미결 수량은 따로 보여준다.

    material 인자는 자재번호와 제품명 양쪽에서 부분일치로 찾는다(화면 입력 하나로 둘 다 커버).

    supplier / po_number 는 문자열 하나 또는 **리스트**를 받는다(화면에서 여러 개 선택 가능).
    states 는 ['합격','불합격', ...] 처럼 로트 상태로 거를 때 쓴다(비우면 전체).
    """
    def _as_list(v):
        if v is None:
            return []
        if isinstance(v, (list, tuple, set)):
            return [str(x).strip() for x in v if str(x).strip()]
        return [v.strip()] if str(v).strip() else []

    suppliers = _as_list(supplier)
    po_numbers = _as_list(po_number)
    states = _as_list(states)

    conn = get_conn()

    sql = """SELECT i.*,
                    (SELECT COUNT(*) FROM ncr n WHERE n.inspection_id = i.id) AS ncr_count,
                    (SELECT n.id FROM ncr n WHERE n.inspection_id = i.id
                      ORDER BY n.id DESC LIMIT 1) AS ncr_id,
                    (SELECT COUNT(*) FROM ncr n WHERE n.inspection_id = i.id
                      AND n.status IN ('sent', 'confirmed')) AS ncr_sent_count
               FROM inspections i
               LEFT JOIN materials mt ON mt.material_no = i.material_no
              WHERE 1=1"""
    params = []
    if start_date:
        sql += " AND i.inspect_date >= ?"; params.append(start_date)
    if end_date:
        sql += " AND i.inspect_date <= ?"; params.append(end_date)
    if suppliers:
        sql += f" AND i.supplier IN ({','.join('?' * len(suppliers))})"; params += suppliers
    if po_numbers:
        sql += f" AND i.po_number IN ({','.join('?' * len(po_numbers))})"; params += po_numbers
    if material:
        sql += " AND (i.material_no LIKE ? OR i.material_name LIKE ?)"
        params += [f"%{material}%", f"%{material}%"]
    if category:
        sql += " AND mt.category = ?"; params.append(category)
    # 재검사로 대체된 옛 성적서는 집계에서 제외 (같은 로트가 두 번 잡히는 것 방지)
    sql += " AND i.status != 'superseded'"
    sql += " ORDER BY i.inspect_date DESC, i.id DESC"

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # 로트 상태(합격/특채/불합격/미결) 필터는 status+approval_type 조합이라 SQL이 지저분해진다.
    # 판정 규칙이 _lot_state() 한 곳에만 있도록 여기서 걸러낸다.
    if states:
        rows = [r for r in rows
                if _lot_state(r["status"], r["approval_type"], r["overall_result"]) in states]

    ids = [r["id"] for r in rows]

    # 불량 항목 — 어떤 검사항목에서 많이 터지는지
    defect_items = []
    if ids:
        ph = ",".join("?" * len(ids))
        defect_items = [dict(r) for r in conn.execute(f"""
            SELECT ii.item_name, ii.result, ii.measured_value,
                   i.material_no, i.material_name, i.supplier, i.id AS inspection_id,
                   COALESCE(s.spec_display, ii.item_name) AS spec_display
              FROM inspection_items ii
              JOIN inspections i ON i.id = ii.inspection_id
              LEFT JOIN specs s ON s.material_no = COALESCE(ii.part_material_no, i.material_no)
                               AND s.item_name = ii.item_name
             WHERE ii.inspection_id IN ({ph})
               AND ii.result NOT IN ('합격', '미측정', '')
               AND ii.result IS NOT NULL
        """, ids).fetchall()]

    # 기간 내 4M 변경점 — 불량률 변화의 원인 후보
    cp_sql = "SELECT * FROM change_points WHERE 1=1"
    cp_params = []
    if start_date:
        cp_sql += " AND change_date >= ?"; cp_params.append(start_date)
    if end_date:
        cp_sql += " AND change_date <= ?"; cp_params.append(end_date)
    if suppliers:
        cp_sql += f" AND supplier IN ({','.join('?' * len(suppliers))})"; cp_params += suppliers
    cp_sql += " ORDER BY change_date DESC"
    change_points = [dict(r) for r in conn.execute(cp_sql, cp_params).fetchall()]

    # 실제 측정 표본수 — inspection_id별 measured_value 콤마수 최댓값
    # (값이 3개면 콤마 2개 = 길이차 + 1 = 3)
    sample_count_map = {}
    if ids:
        ph2 = ",".join("?" * len(ids))
        for sc_row in conn.execute(f"""
            SELECT inspection_id,
                   MAX(LENGTH(measured_value) - LENGTH(REPLACE(measured_value, ',', '')) + 1) AS cnt
              FROM inspection_items
             WHERE inspection_id IN ({ph2})
               AND measured_value IS NOT NULL AND TRIM(measured_value) != ''
             GROUP BY inspection_id
        """, ids).fetchall():
            sample_count_map[sc_row[0]] = int(sc_row[1])

    # NCR 집계용 데이터 — issued_date 기준, supplier 필터 적용
    ncr_sql = """
        SELECT supplier, issued_date,
               COALESCE(occurrence_type, '입고검사') AS occurrence_type,
               defect_type
          FROM ncr
         WHERE issued_date IS NOT NULL
    """
    ncr_params = []
    if start_date:
        ncr_sql += " AND issued_date >= ?"; ncr_params.append(start_date)
    if end_date:
        ncr_sql += " AND issued_date <= ?"; ncr_params.append(end_date)
    if suppliers:
        ncr_sql += f" AND supplier IN ({','.join('?' * len(suppliers))})"; ncr_params += suppliers
    ncr_rows = [dict(r) for r in conn.execute(ncr_sql, ncr_params).fetchall()]

    # 개선요청서 집계용 데이터 — NCR과 병렬(issued_date 기준, supplier 필터 적용)
    imp_sql = "SELECT supplier, issued_date FROM improvement_requests WHERE issued_date IS NOT NULL"
    imp_params = []
    if start_date:
        imp_sql += " AND issued_date >= ?"; imp_params.append(start_date)
    if end_date:
        imp_sql += " AND issued_date <= ?"; imp_params.append(end_date)
    if suppliers:
        imp_sql += f" AND supplier IN ({','.join('?' * len(suppliers))})"; imp_params += suppliers
    imp_rows = [dict(r) for r in conn.execute(imp_sql, imp_params).fetchall()]

    # 수기입력(성적서 미연결) sent NCR — ②③(NCR포함 불량률/건수율)용 lot_qty만 뽑는다.
    manual_ncr_sql = "SELECT lot_qty FROM ncr WHERE inspection_id IS NULL AND status IN ('sent', 'confirmed')"
    manual_ncr_params = []
    if start_date:
        manual_ncr_sql += " AND issued_date >= ?"; manual_ncr_params.append(start_date)
    if end_date:
        manual_ncr_sql += " AND issued_date <= ?"; manual_ncr_params.append(end_date)
    if suppliers:
        manual_ncr_sql += f" AND supplier IN ({','.join('?' * len(suppliers))})"; manual_ncr_params += suppliers
    manual_ncr_lot_qtys = [r[0] for r in conn.execute(manual_ncr_sql, manual_ncr_params).fetchall()]

    conn.close()

    # ---- 집계 ----
    def blank():
        return {"로트": 0, "수량": 0, "합격수량": 0, "특채수량": 0,
                "불합격수량": 0, "미결수량": 0, "불합격로트": 0, "합격로트": 0,
                "검사표본수": 0, "표본불량수": 0, "특채건수": 0}

    def add(acc, r):
        qty = int(r["quantity"] or 0)
        state = _lot_state(r["status"], r["approval_type"], r["overall_result"])
        acc["로트"] += 1
        acc["수량"] += qty
        acc["검사표본수"] += sample_count_map.get(r["id"], 0)
        acc["표본불량수"] += sum(int(m[1]) for m in _DEFECT_RE.findall(r.get("remark_inspector") or ""))
        if state == "합격":
            acc["합격수량"] += qty
            acc["합격로트"] += 1
        elif state == "특채":
            acc["특채수량"] += qty
            acc["특채건수"] += 1
        elif state == "불합격":
            acc["불합격수량"] += qty
            acc["불합격로트"] += 1
        else:
            acc["미결수량"] += qty

    def finish(acc):
        confirmed = acc["합격수량"] + acc["특채수량"] + acc["불합격수량"]
        acc["확정수량"] = confirmed
        acc["판정확정로트"] = acc["합격로트"] + acc["특채건수"] + acc["불합격로트"]
        acc["불량률"] = round(acc["불합격수량"] / confirmed * 100, 3) if confirmed else 0.0
        acc["PPM"] = int(round(acc["불합격수량"] / confirmed * 1_000_000)) if confirmed else 0
        # 특채까지 포함한 '규격 이탈률' — 참고용
        out = acc["불합격수량"] + acc["특채수량"]
        acc["규격이탈률"] = round(out / confirmed * 100, 3) if confirmed else 0.0
        # 표본 기준 불량률 (None = 표본데이터 없음, 0%와 구분)
        samp = acc["검사표본수"]
        acc["표본불량률"] = round(acc["표본불량수"] / samp * 100, 3) if samp else None
        acc["표본PPM"] = int(round(acc["표본불량수"] / samp * 1_000_000)) if samp else None
        return acc

    summary = blank()
    by_period, by_supplier, by_material = {}, {}, {}
    for r in rows:
        add(summary, r)
        pk = _period_key(r["inspect_date"], period_type) or "(날짜없음)"
        add(by_period.setdefault(pk, blank()), r)
        add(by_supplier.setdefault(r["supplier"] or "(미입력)", blank()), r)
        mk = r["material_no"] or "(미입력)"
        acc_m = by_material.setdefault(mk, blank())
        acc_m["자재명"] = r["material_name"]
        add(acc_m, r)

    finish(summary)

    # ---- ②③ NCR포함 불량률/건수율 (2026-09-15 사용자 확정) ----
    ncr_extra_qty, ncr_extra_cnt = _ncr_extra_stats(rows, manual_ncr_lot_qtys)
    summary["NCR추가수량"] = ncr_extra_qty
    summary["NCR추가로트"] = ncr_extra_cnt

    confirmed_ncr = summary["확정수량"] + ncr_extra_qty
    defect_ncr = summary["불합격수량"] + ncr_extra_qty
    summary["불량률_NCR포함"] = round(defect_ncr / confirmed_ncr * 100, 3) if confirmed_ncr else 0.0
    summary["PPM_NCR포함"] = int(round(defect_ncr / confirmed_ncr * 1_000_000)) if confirmed_ncr else 0

    confirmed_cnt_ncr = summary["판정확정로트"] + ncr_extra_cnt
    defect_cnt_ncr = summary["불합격로트"] + ncr_extra_cnt
    summary["불량건수율_NCR포함"] = round(defect_cnt_ncr / confirmed_cnt_ncr * 100, 3) if confirmed_cnt_ncr else 0.0

    period_list = []
    for k in sorted(by_period.keys()):
        period_list.append({"구간": k, **finish(by_period[k])})

    supplier_list = []
    for k, v in by_supplier.items():
        supplier_list.append({"업체": k, **finish(v)})
    supplier_list.sort(key=lambda x: (-x["불합격수량"], -x["수량"]))

    material_list = []
    for k, v in by_material.items():
        name = v.pop("자재명", None)
        material_list.append({"자재번호": k, "자재명": name, **finish(v)})
    material_list.sort(key=lambda x: (-x["불합격수량"], -x["수량"]))

    # 불량 항목 순위
    item_rank = {}
    for d in defect_items:
        key = (d["material_no"], d["item_name"])
        e = item_rank.setdefault(key, {
            "자재번호": d["material_no"], "자재명": d["material_name"],
            "항목": d["item_name"], "규격": d["spec_display"], "발생건수": 0, "업체": set(),
        })
        e["발생건수"] += 1
        if d["supplier"]:
            e["업체"].add(d["supplier"])
    top_items = sorted(item_rank.values(), key=lambda x: -x["발생건수"])
    for e in top_items:
        e["업체"] = ", ".join(sorted(e["업체"]))

    # 검사 소요시간 — 인력 산정 근거
    time_rows = [r for r in rows if (r["total_time_sec"] or r["actual_time_sec"])]
    per_person = {}
    total_sec = 0
    for r in time_rows:
        sec = int(r["total_time_sec"] or 0) or int(r["actual_time_sec"] or 0)
        total_sec += sec
        p = per_person.setdefault(r["inspector"] or "(미입력)", {"검사자": r["inspector"] or "(미입력)",
                                                              "건수": 0, "총초": 0})
        p["건수"] += 1
        p["총초"] += sec
    for p in per_person.values():
        p["평균초"] = int(round(p["총초"] / p["건수"])) if p["건수"] else 0
    time_stats = {
        "측정건수": len(time_rows),
        "총초": total_sec,
        "평균초": int(round(total_sec / len(time_rows))) if time_rows else 0,
        "인원별": sorted(per_person.values(), key=lambda x: -x["건수"]),
    }

    # ---- NCR 집계 ----
    # 최상위 요약
    ncr_total = len(ncr_rows)
    ncr_후발 = sum(1 for n in ncr_rows if n["occurrence_type"] == "사후")
    ncr_불량유형별: dict = {}
    for n in ncr_rows:
        dt = n["defect_type"] or "(미분류)"
        ncr_불량유형별[dt] = ncr_불량유형별.get(dt, 0) + 1

    # 기간별 NCR 건수 — issued_date 기준으로 period_key 계산
    ncr_by_period: dict = {}
    for n in ncr_rows:
        pk = _period_key(n["issued_date"], period_type) or "(날짜없음)"
        entry = ncr_by_period.setdefault(pk, {"ncr_건수": 0, "사후불량_건수": 0})
        entry["ncr_건수"] += 1
        if n["occurrence_type"] == "사후":
            entry["사후불량_건수"] += 1

    # period_list에 NCR 건수 병합
    for item in period_list:
        pk = item["구간"]
        ncr_entry = ncr_by_period.get(pk, {"ncr_건수": 0, "사후불량_건수": 0})
        item["ncr_건수"] = ncr_entry["ncr_건수"]
        item["사후불량_건수"] = ncr_entry["사후불량_건수"]

    # 업체별 NCR 건수
    ncr_by_supplier: dict = {}
    for n in ncr_rows:
        sup = n["supplier"] or "(미입력)"
        entry = ncr_by_supplier.setdefault(sup, {"ncr_건수": 0, "사후불량_건수": 0})
        entry["ncr_건수"] += 1
        if n["occurrence_type"] == "사후":
            entry["사후불량_건수"] += 1

    # supplier_list에 NCR 건수 병합
    for item in supplier_list:
        sup = item["업체"]
        ncr_entry = ncr_by_supplier.get(sup, {"ncr_건수": 0, "사후불량_건수": 0})
        item["ncr_건수"] = ncr_entry["ncr_건수"]
        item["사후불량_건수"] = ncr_entry["사후불량_건수"]

    # 요약에 NCR 건수 추가
    summary["ncr_건수"] = ncr_total
    summary["사후불량_건수"] = ncr_후발
    summary["불량유형별"] = ncr_불량유형별

    # supplier_ncr_rank — NCR 건수 내림차순
    sup_ncr_map: dict = {}
    for n in ncr_rows:
        sup = n["supplier"] or "(미입력)"
        sup_ncr_map[sup] = sup_ncr_map.get(sup, 0) + 1
    # 불합격수량은 supplier_list에서 가져온다
    sup_불합격_map = {item["업체"]: item["불합격수량"] for item in supplier_list}
    supplier_ncr_rank = sorted(
        [{"name": s, "ncr_건수": cnt, "불합격수량": sup_불합격_map.get(s, 0)}
         for s, cnt in sup_ncr_map.items()],
        key=lambda x: -x["ncr_건수"],
    )

    # ---- 개선요청서 집계 (NCR과 병렬) ----
    summary["개선요청_건수"] = len(imp_rows)

    imp_by_period: dict = {}
    for n in imp_rows:
        pk = _period_key(n["issued_date"], period_type) or "(날짜없음)"
        imp_by_period[pk] = imp_by_period.get(pk, 0) + 1
    for item in period_list:
        item["개선요청_건수"] = imp_by_period.get(item["구간"], 0)

    imp_by_supplier: dict = {}
    for n in imp_rows:
        sup = n["supplier"] or "(미입력)"
        imp_by_supplier[sup] = imp_by_supplier.get(sup, 0) + 1
    for item in supplier_list:
        item["개선요청_건수"] = imp_by_supplier.get(item["업체"], 0)

    supplier_improvement_rank = sorted(
        [{"name": s, "개선요청_건수": cnt} for s, cnt in imp_by_supplier.items()],
        key=lambda x: -x["개선요청_건수"],
    )

    # material_deviation_rank — 특채건수 내림차순 (특채건수 > 0인 자재만)
    material_deviation_rank = sorted(
        [{"material_no": item["자재번호"], "material_name": item["자재명"],
          "특채건수": item["특채건수"]}
         for item in material_list if item.get("특채건수", 0) > 0],
        key=lambda x: -x["특채건수"],
    )

    # supplier_deviation_rank — 특채건수 내림차순 (특채건수 > 0인 업체만)
    supplier_deviation_rank = sorted(
        [{"name": item["업체"], "특채건수": item["특채건수"]}
         for item in supplier_list if item.get("특채건수", 0) > 0],
        key=lambda x: -x["특채건수"],
    )

    return {
        "기간": {"시작": start_date, "종료": end_date, "유형": period_type},
        "필터": {"업체": ", ".join(suppliers) if suppliers else "전체",
                 "로트번호": ", ".join(po_numbers) if po_numbers else "전체",
                 "자재/제품명": material or "전체",
                 "판정상태": ", ".join(states) if states else "전체"},
        "불량률기준": "수량기준 (불합격 확정수량 ÷ 판정 확정수량)",
        "불량률기준_NCR포함": ("수량기준 ((불합격확정수량 + NCR만있는로트 수량) ÷ "
                          "(판정확정수량 + NCR만있는로트 수량)). NCR만있는로트 = "
                          "불합격확정이 아닌 로트(합격/특채/미결) 중 발송·확인(status가 "
                          "sent 또는 confirmed)된 NCR이 연결된 것 + 수기입력 발송·확인 "
                          "NCR(lot_qty 숫자 인식 성공분)."),
        "불량건수율기준_NCR포함": "건수기준 ((불합격로트 + NCR만있는로트) ÷ (판정확정로트 + NCR만있는로트))",
        "요약": summary,
        "기간별": period_list,
        "업체별": supplier_list,
        "자재별": material_list,
        "불량항목순위": top_items,
        "소요시간": time_stats,
        "변경점": change_points,
        "성적서목록": rows,
        "supplier_ncr_rank": supplier_ncr_rank,
        "supplier_improvement_rank": supplier_improvement_rank,
        "material_deviation_rank": material_deviation_rank,
        "supplier_deviation_rank": supplier_deviation_rank,
    }


_HL_DEVIATION_MIN = 5.0     # 규격이탈률(%) 이 이상이어야 특이사항 후보로 본다
_HL_MIN_CONFIRMED = 10      # 확정수량이 이 미만이면 표본이 너무 작아 노이즈로 보고 제외한다
_HL_SUPPLIER_SLOTS = 3      # 특이사항 중 업체 몫(수량 영향이 큰 순)
_HL_MATERIAL_SLOTS = 2      # 특이사항 중 자재 몫


def quality_highlights(report):
    """quality_report() 결과를 임원도 바로 읽을 수 있는 문장으로 요약한다(규칙 기반 — AI 아님).

    대시보드 화면과 JSON/엑셀 내보내기가 이 함수 하나만 쓴다(집계는 한 곳에서만 하는
    quality_report()의 원칙을 요약 문장에도 그대로 적용) — 화면과 발표자료가 서로
    다른 이야기를 하지 않게 하기 위함이다.

    특이사항은 '규격이탈률이 높은 것'이 아니라 '실제 영향 수량(특채+불합격)이 큰 것'을
    우선 노출한다 — 작은 로트 하나가 100% 이탈인 것보다, 큰 로트에서 이탈이 반복되는
    업체가 경영진에게 더 중요한 신호이기 때문. 업체 몫과 자재 몫을 미리 나눠서(3+2),
    업체 단위 이슈가 개별 부품 이슈에 전부 밀려나지 않게 한다.
    """
    s = report["요약"]
    confirmed = s["확정수량"]
    if confirmed == 0:
        if s["로트"] == 0:
            headline = "이 조건에 해당하는 검사 기록이 없습니다."
        else:
            headline = f"이 기간 {s['로트']}개 로트 중 판정이 확정된 건이 아직 없습니다(전부 승인 대기·반려)."
        return {"headline": headline, "톤": "정보없음", "특이사항": [], "표본참고": None}

    def _candidates(rows, kind, name_key):
        out = []
        for r in rows:
            if r["확정수량"] < _HL_MIN_CONFIRMED or r["규격이탈률"] < _HL_DEVIATION_MIN:
                continue
            name = r[name_key]
            if kind == "자재" and r.get("자재명"):
                name = f"{name} ({r['자재명']})"
            impact = r["특채수량"] + r["불합격수량"]
            out.append({
                "구분": kind, "이름": name, "규격이탈률": r["규격이탈률"], "불량률": r["불량률"],
                "특채수량": r["특채수량"], "불합격수량": r["불합격수량"], "확정수량": r["확정수량"],
                "_impact": impact,
            })
        out.sort(key=lambda x: (-x["_impact"], -x["규격이탈률"]))
        return out

    sup_cands = _candidates(report["업체별"], "업체", "업체")
    mat_cands = _candidates(report["자재별"], "자재", "자재번호")

    top = sup_cands[:_HL_SUPPLIER_SLOTS] + mat_cands[:_HL_MATERIAL_SLOTS]
    total_slots = _HL_SUPPLIER_SLOTS + _HL_MATERIAL_SLOTS
    remaining = total_slots - len(top)
    if remaining > 0:
        used_sup = min(len(sup_cands), _HL_SUPPLIER_SLOTS)
        used_mat = min(len(mat_cands), _HL_MATERIAL_SLOTS)
        top += sup_cands[used_sup:used_sup + remaining]
        remaining = total_slots - len(top)
        if remaining > 0:
            top += mat_cands[used_mat:used_mat + remaining]
    top.sort(key=lambda x: (-x["_impact"], -x["규격이탈률"]))

    for c in top:
        parts = []
        if c["불합격수량"]:
            parts.append(f"불합격 {c['불합격수량']:,}개")
        if c["특채수량"]:
            parts.append(f"특채 {c['특채수량']:,}개")
        detail = ", ".join(parts) if parts else "규격 이탈"
        c["메시지"] = f"{c['이름']} — 규격이탈률 {c['규격이탈률']}%(확정 {c['확정수량']:,}개 중 {detail})"
        c.pop("_impact", None)

    pass_rate = round(s["합격수량"] / confirmed * 100, 1)
    headline = (f"이번 기간 판정 확정 {confirmed:,}개 중 합격 {s['합격수량']:,}개({pass_rate}%), "
                f"불합격 {s['불합격수량']:,}개, 특채 {s['특채수량']:,}개입니다.")
    if top:
        names = ", ".join(c["이름"] for c in top[:3])
        suffix = " 등" if len(top) > 3 else ""
        headline += f" 다만 {names}{suffix}에서 규격을 반복적으로 벗어나 특채·불합격 처리된 사례가 있어 확인이 필요합니다."
    else:
        headline += " 규격 이탈이 두드러지는 업체·자재는 없습니다."

    sample_note = None
    sample_defect_rate = s.get("표본불량률")  # 이 필드가 없던 시절(구버전) 성적표 스냅샷 대응
    if sample_defect_rate is not None and (sample_defect_rate - s["불량률"]) >= 2:
        sample_note = (f"참고: 개별 표본 기준으로는 불량 표본 비율이 {sample_defect_rate}%로 나타납니다. "
                        "이 수치는 특채로 넘어간 물량의 표본 불량까지 포함하며, "
                        "위 불량률·PPM에는 특채가 들어가지 않습니다.")

    if s["불량률"] >= 3 or any(c["규격이탈률"] >= 20 for c in top):
        tone = "경고"
    elif top or s["불량률"] >= 1:
        tone = "주의"
    else:
        tone = "양호"

    return {"headline": headline, "톤": tone, "특이사항": top, "표본참고": sample_note}


def upsert_supplier_report(supplier, period, start_date, end_date, payload_json, created_by):
    """업체 월간 성적표 생성/갱신. 이미 승인·발송된 건은 덮어쓰지 않는다."""
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT id, status FROM supplier_reports WHERE supplier=? AND period=?",
            (supplier, period)).fetchone()
        if existing and existing["status"] != "draft":
            return existing["id"], f"이미 {existing['status']} 상태라 다시 만들 수 없어."
        if existing:
            conn.execute("""UPDATE supplier_reports
                               SET start_date=?, end_date=?, payload=?, created_by=?,
                                   created_at=datetime('now','localtime')
                             WHERE id=?""",
                         (start_date, end_date, payload_json, created_by, existing["id"]))
            rid = existing["id"]
        else:
            cur = conn.execute("""INSERT INTO supplier_reports
                                   (supplier, period, start_date, end_date, payload, created_by)
                                   VALUES (?, ?, ?, ?, ?, ?)""",
                               (supplier, period, start_date, end_date, payload_json, created_by))
            rid = cur.lastrowid
        conn.commit()
        return rid, None
    finally:
        conn.close()


def get_supplier_report_trend(supplier, upto_period_exclusive, limit=5):
    """최근 성적표 목록 — 월별 추이 차트용. upto_period_exclusive 는 포함하지 않는다
    (같은 달 draft가 자기 자신을 중복으로 끌고 오는 걸 막기 위해 반드시 '<')."""
    import json
    conn = get_conn()
    rows = conn.execute(
        """SELECT period, payload FROM supplier_reports
            WHERE supplier = ? AND period < ?
            ORDER BY period DESC LIMIT ?""",
        (supplier, upto_period_exclusive, limit)).fetchall()
    conn.close()
    out = []
    for row in reversed(rows):
        try:
            data = json.loads(row["payload"])
            s = data["요약"]
            out.append({"기간": row["period"], "수량": s["수량"], "불합격수량": s["불합격수량"],
                        "특채수량": s["특채수량"], "합격수량": s["합격수량"],
                        "불량률": s["불량률"], "규격이탈률": s["규격이탈률"], "PPM": s["PPM"]})
        except (ValueError, KeyError, TypeError):
            continue
    return out


def list_supplier_reports(status=None):
    conn = get_conn()
    sql = "SELECT * FROM supplier_reports"
    params = []
    if status:
        sql += " WHERE status = ?"; params.append(status)
    sql += " ORDER BY period DESC, supplier"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def get_supplier_report(report_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM supplier_reports WHERE id=?", (report_id,)).fetchone()
    conn.close()
    return row


def approve_supplier_report(report_id, approved_by, signature_path):
    conn = get_conn()
    conn.execute("""UPDATE supplier_reports
                       SET status='approved', approved_by=?, approve_signature=?,
                           approved_at=datetime('now','localtime')
                     WHERE id=? AND status='draft'""",
                 (approved_by, signature_path, report_id))
    conn.commit()
    conn.close()


def mark_supplier_report_sent(report_id, sent_to):
    conn = get_conn()
    conn.execute("""UPDATE supplier_reports
                       SET status='sent', sent_to=?, sent_at=datetime('now','localtime')
                     WHERE id=? AND status='approved'""",
                 (sent_to, report_id))
    conn.commit()
    conn.close()


def delete_supplier_report(report_id):
    conn = get_conn()
    conn.execute("DELETE FROM supplier_reports WHERE id=? AND status='draft'", (report_id,))
    conn.commit()
    conn.close()


def delete_supplier_reports_admin(report_ids):
    """admin 전용: 상태와 무관하게 성적표 삭제."""
    if not report_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" * len(report_ids))
    conn.execute(f"DELETE FROM supplier_reports WHERE id IN ({placeholders})", report_ids)
    conn.commit()
    conn.close()


def delete_ncrs(ncr_ids):
    """부적합 통보서 일괄 삭제 (photos는 JSON으로 인라인 저장돼 있어서 부수 테이블 없음)."""
    if not ncr_ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" * len(ncr_ids))
    conn.execute(f"DELETE FROM ncr WHERE id IN ({placeholders})", ncr_ids)
    conn.commit()
    conn.close()


def process_capability(material_no, item_name=None, min_samples=5):
    """공정능력(Cp/Cpk) 계산 — 규격 하한·상한과 실제 측정값으로.

    Cpk = min( (USL-μ)/3σ , (μ-LSL)/3σ )  — 단측 규격이면 있는 쪽만 본다.
    구간 표시(우수/양호/주의/부족)는 1.33/1.67 기준이고, 실제 요구 기준은 거래처·품목마다
    다르니 확인해야 한다(CLAUDE.md 8-2-12절 원칙과 동일 — 업종 단정 금지).
    표본이 적으면 숫자가 튀므로 min_samples 미만은 계산하지 않고 표시만 한다.
    """
    import statistics
    conn = get_conn()
    specs = conn.execute("""
        SELECT item_name, spec_display, lower_limit, upper_limit
          FROM specs
         WHERE material_no = ? AND judge_type = 'numeric'
           AND (lower_limit IS NOT NULL OR upper_limit IS NOT NULL)
           AND (? IS NULL OR item_name = ?)
         ORDER BY item_order
    """, (material_no, item_name, item_name)).fetchall()

    out = []
    for sp in specs:
        raw = conn.execute("""
            SELECT ii.measured_value
              FROM inspection_items ii
              JOIN inspections i ON i.id = ii.inspection_id
             WHERE ii.item_name = ?
               AND COALESCE(ii.part_material_no, i.material_no) = ?
               AND ii.measured_value IS NOT NULL AND ii.measured_value != ''
        """, (sp["item_name"], material_no)).fetchall()

        values = []
        for r in raw:
            for tok in str(r["measured_value"]).split(","):
                tok = tok.strip()
                if not tok:
                    continue
                try:
                    values.append(float(tok))
                except ValueError:
                    pass

        entry = {
            "항목": sp["item_name"], "규격": sp["spec_display"],
            "하한": sp["lower_limit"], "상한": sp["upper_limit"],
            "표본수": len(values), "평균": None, "표준편차": None,
            "Cp": None, "Cpk": None, "판정": None,
        }
        if len(values) >= max(2, min_samples):
            mu = statistics.fmean(values)
            sigma = statistics.stdev(values)
            entry["평균"] = round(mu, 4)
            entry["표준편차"] = round(sigma, 4)
            if sigma > 0:
                lsl, usl = sp["lower_limit"], sp["upper_limit"]
                cpu = (usl - mu) / (3 * sigma) if usl is not None else None
                cpl = (mu - lsl) / (3 * sigma) if lsl is not None else None
                cands = [c for c in (cpu, cpl) if c is not None]
                if cands:
                    entry["Cpk"] = round(min(cands), 3)
                if lsl is not None and usl is not None:
                    entry["Cp"] = round((usl - lsl) / (6 * sigma), 3)
                if entry["Cpk"] is not None:
                    entry["판정"] = ("우수" if entry["Cpk"] >= 1.67 else
                                    "양호" if entry["Cpk"] >= 1.33 else
                                    "주의" if entry["Cpk"] >= 1.0 else "부족")
            else:
                entry["판정"] = "산포없음"     # 측정값이 전부 같음 (분해능 부족 의심)
        else:
            entry["판정"] = "표본부족"
        out.append(entry)

    conn.close()
    return out


# ---------- 마이그레이션 ----------

def ensure_ncr_columns_migration():
    """ncr 테이블에 occurrence_type / defect_type 컬럼 추가 (멱등).

    - occurrence_type: '입고검사' | '사후'. NULL은 '입고검사'로 간주.
    - defect_type: '치수불량'|'외관불량'|'기능불량'|'재질불량'|'수량불량'|'기타'. NULL 허용.
    """
    if get_setting("ncr_columns_migrated_20260908") == "1":
        return
    conn = get_conn()
    existing = [r[1] for r in conn.execute("PRAGMA table_info('ncr')").fetchall()]
    if "occurrence_type" not in existing:
        conn.execute("ALTER TABLE ncr ADD COLUMN occurrence_type TEXT DEFAULT '입고검사'")
    if "defect_type" not in existing:
        conn.execute("ALTER TABLE ncr ADD COLUMN defect_type TEXT")
    conn.commit()
    conn.close()
    set_setting("ncr_columns_migrated_20260908", "1")


def ensure_defect_types_seed_20260908():
    """불량 유형 마스터 최초 시드 — 폼에 하드코딩돼 있던 6종을 마스터 테이블로 옮긴다(멱등)."""
    if get_setting("defect_types_seeded_20260908") == "1":
        return
    for name in ["치수불량", "외관불량", "기능불량", "재질불량", "수량불량", "기타"]:
        add_defect_type(name)
    set_setting("defect_types_seeded_20260908", "1")


# ---------- 불량 유형 마스터 (NCR 폼 드롭다운, 생성/수정/삭제 팝업에서 씀) ----------

def list_defect_types():
    """등록된 불량 유형명 전체 (가나다순)."""
    conn = get_conn()
    rows = conn.execute("SELECT name FROM defect_types ORDER BY name").fetchall()
    conn.close()
    return [r["name"] for r in rows]


def list_improvement_defect_categories():
    """개선요청서 불량유형 체크박스 목록 — list_defect_types()(NCR과 공용 마스터)를
    그대로 쓰되 '기타'만 맨 끝으로 옮긴다(2026-09-16 사용자 확정: "개선요청서에
    있는 불량 유형들을 기존에 등록되어있는 불량유형의 내용으로 통일" — 예전엔
    이 앱 두 곳(app.py/report_builder.py)에 NCR 마스터와 이름도 항목 구성도 다른
    5종이 따로 하드코딩돼 있었다). '기타'는 옆에 항상 자유입력칸이 붙는 항목이라
    마스터의 가나다순 그대로 두면 중간에 섞여 나와 어색해서 여기서만 보정 —
    app.py(폼 렌더/검증)와 report_builder.py(엑셀 생성) 둘 다 이 함수 하나를 쓴다
    (CLAUDE.md 8-1절)."""
    cats = list_defect_types()
    if "기타" not in cats:
        return cats
    return [c for c in cats if c != "기타"] + ["기타"]


def ensure_improvement_defect_categories_migration_20260916():
    """개선요청서 불량유형을 NCR 마스터로 통일하기 전(2026-09-16 이전)에 만들어진
    기존 레코드는 `defect_categories`에 옛 5종("치수 불량"/"표면 결함"/
    "기능 부적합"/"포장 손상") 문자열이 그대로 저장돼 있다 — 새 마스터
    (치수불량/외관불량/기능불량/재질불량/수량불량/기타, 공백 없음)와 문자열이
    달라서 엑셀을 다시 발행해도 체크박스가 하나도 안 켜져 보이는 문제가 실제로
    있었다(사용자가 실제 발행 PDF로 발견). 저장된 문자열을 새 이름으로 1회
    치환한다(멱등). '포장 손상'은 새 마스터에 대응하는 항목이 없어서 그냥
    제거한다(사용자가 원하면 나중에 새 항목으로 등록하면 됨, 임의로 지어내지 않음)."""
    if get_setting("improvement_defect_categories_migrated_20260916") == "1":
        return
    mapping = {
        "치수 불량": "치수불량",
        "표면 결함": "외관불량",
        "기능 부적합": "기능불량",
        "포장 손상": None,  # 새 마스터에 대응 항목 없음 — 제거
    }
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, defect_categories FROM improvement_requests WHERE defect_categories IS NOT NULL"
    ).fetchall()
    for row in rows:
        old_cats = [c.strip() for c in (row["defect_categories"] or "").split(",") if c.strip()]
        new_cats = []
        changed = False
        for c in old_cats:
            if c in mapping:
                changed = True
                mapped = mapping[c]
                if mapped:
                    new_cats.append(mapped)
            else:
                new_cats.append(c)
        if changed:
            conn.execute("UPDATE improvement_requests SET defect_categories=? WHERE id=?",
                        (",".join(new_cats), row["id"]))
    conn.commit()
    conn.close()
    set_setting("improvement_defect_categories_migrated_20260916", "1")


def ensure_material_category_merge_20260916():
    """자재 분류 마스터에 '★완전포장제품'과 '★완포장 제품'이 표기만 다른 같은
    분류로 두 개 나뉘어 있던 걸(2026-09-10 BOM 자동분류 때 키워드 매칭이 두 형태를
    각각 새 분류로 등록해버린 것으로 추정) 사용자 요청으로 '★완포장 제품' 하나로
    합친다. materials.category가 옛 이름인 행을 새 이름으로 바꾸고, 마스터에서
    옛 이름 항목을 제거(새 이름 항목이 마스터에 없으면 새로 등록). 1회성, 멱등."""
    if get_setting("material_category_merge_20260916") == "1":
        return
    OLD_NAME = "★완전포장제품"
    NEW_NAME = "★완포장 제품"
    add_material_category(NEW_NAME)
    conn = get_conn()
    conn.execute("UPDATE materials SET category = ? WHERE category = ?", (NEW_NAME, OLD_NAME))
    conn.execute("DELETE FROM material_categories WHERE name = ?", (OLD_NAME,))
    conn.commit()
    conn.close()
    set_setting("material_category_merge_20260916", "1")


def add_defect_type(name):
    """불량 유형명을 마스터에 등록. 이미 있으면(공백/대소문자 무시) 기존 정본 표기를
    반환. name이 빈 값이면 None. (material_categories.add_material_category와 동일 관례)"""
    name = (name or "").strip()[:30]  # 프론트 maxlength=30과 맞춰 서버에서도 강제
    if not name:
        return None
    conn = get_conn()
    for row in conn.execute("SELECT name FROM defect_types").fetchall():
        if row["name"].strip().casefold() == name.casefold():
            conn.close()
            return row["name"]
    conn.execute("INSERT INTO defect_types (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()
    return name


def rename_defect_type(old_name, new_name):
    """불량 유형명 변경 — 이미 발행된 NCR에 쓰인 값도 새 이름으로 같이 갱신한다
    (과거 통계·목록에서 라벨이 끊기지 않게, rename_material()과 동일한 사고방식).
    반환: (성공여부, 에러메시지)."""
    old_name = (old_name or "").strip()
    new_name = (new_name or "").strip()[:30]  # 프론트 maxlength=30과 맞춰 서버에서도 강제
    if not new_name:
        return False, "이름을 입력해줘."
    conn = get_conn()
    if new_name.casefold() != old_name.casefold():
        dup = conn.execute("SELECT 1 FROM defect_types WHERE name = ?", (new_name,)).fetchone()
        if dup:
            conn.close()
            return False, f"'{new_name}'은 이미 있는 이름이야."
    existing = conn.execute("SELECT 1 FROM defect_types WHERE name = ?", (old_name,)).fetchone()
    if not existing:
        conn.close()
        return False, "존재하지 않는 항목이야."
    conn.execute("UPDATE defect_types SET name = ? WHERE name = ?", (new_name, old_name))
    conn.execute("UPDATE ncr SET defect_type = ? WHERE defect_type = ?", (new_name, old_name))
    conn.commit()
    conn.close()
    return True, None


def delete_defect_type(name):
    """불량 유형을 마스터에서만 제거한다 — FK가 없어서 이미 발행된 NCR의 defect_type
    값은 그대로 남는다(자재 분류 삭제와 동일한 방식). 목록/드롭다운에서만 안 보이게 된다."""
    conn = get_conn()
    conn.execute("DELETE FROM defect_types WHERE name = ?", ((name or "").strip(),))
    conn.commit()
    conn.close()


# ---------- 데이터 점검 (관리자 전용) ----------

def data_health_report():
    """자재·규격 데이터에서 검사·성적서를 망가뜨릴 수 있는 것들을 찾아서 모아준다.
    각 항목: {key, title, desc, severity('high'/'mid'/'low'), rows:[...], columns:[...]}"""
    conn = get_conn()

    def q(sql, params=()):
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    checks = []

    checks.append({
        "key": "no_limit",
        "severity": "high",
        "title": "규격(하한·상한)이 비어 있는 숫자 항목",
        "desc": "판정 기준이 없어서 측정값을 넣어도 합격/불합격을 낼 수 없어. 검사는 되지만 승인이 막혀.",
        "columns": ["자재번호", "항목", "규격 표기", "검사방법"],
        "rows": q("""SELECT material_no, item_name, spec_display, inspect_method
                       FROM specs
                      WHERE judge_type='numeric' AND lower_limit IS NULL AND upper_limit IS NULL
                      ORDER BY material_no, item_order"""),
    })

    checks.append({
        "key": "no_spec",
        "severity": "high",
        "title": "규격 항목이 하나도 없는 자재",
        "desc": "입고돼도 검사를 시작할 수 없어. 자재 관리에서 항목을 등록해줘.",
        "columns": ["자재번호", "자재명", "도면"],
        "rows": q("""SELECT m.material_no, m.material_name,
                            CASE WHEN m.drawing_file IS NOT NULL AND m.drawing_file!='' THEN '있음' ELSE '없음' END AS drawing
                       FROM materials m
                      WHERE NOT EXISTS (SELECT 1 FROM specs s WHERE s.material_no = m.material_no)
                      ORDER BY m.material_no"""),
    })

    checks.append({
        "key": "bad_material_no",
        "severity": "mid",
        "title": "자재번호가 이상한 자재",
        "desc": "자재번호 자리에 한글 품명이나 '-' 같은 값이 들어가 있어. "
                "도면번호 자동계산(A+번호)과 도면 파일 매칭이 안 돼.",
        "columns": ["자재번호", "자재명", "규격 수", "검사 이력"],
        "rows": q("""SELECT m.material_no, m.material_name,
                            (SELECT COUNT(*) FROM specs s WHERE s.material_no=m.material_no) AS spec_count,
                            (SELECT COUNT(*) FROM inspections i WHERE i.material_no=m.material_no) AS insp_count
                       FROM materials m
                      WHERE LENGTH(TRIM(m.material_no)) <= 2
                         OR m.material_no GLOB '*[가-힣]*'
                      ORDER BY m.material_no"""),
    })

    checks.append({
        "key": "assembly_missing_part",
        "severity": "high",
        "title": "조립품 파츠인데 자재로 등록 안 된 번호",
        "desc": "입고 때 조립품이 펼쳐지면 이 번호로 입고 줄이 생기는데, 자재가 없어서 검사를 못 해.",
        "columns": ["조립품", "순서", "파츠 자재번호"],
        "rows": q("""SELECT am.assembly_no, ac.component_order, ac.component_no
                       FROM assembly_components ac
                       JOIN assembly_masters am ON am.id = ac.assembly_id
                      WHERE NOT EXISTS (SELECT 1 FROM materials m WHERE m.material_no = ac.component_no)
                      ORDER BY am.assembly_no, ac.component_order"""),
    })

    gauge_count = conn.execute("SELECT COUNT(*) FROM gauge_master").fetchone()[0]
    checks.append({
        "key": "gauge_master_empty",
        "severity": "high" if gauge_count == 0 else "low",
        "title": "계측기 마스터 비어 있음",
        "desc": "등록된 계측기가 없으면 교정 유효기간 임박(D-15) 경고가 절대 뜨지 않는다. "
                "'경고 없음 = 문제 없음'으로 오해하기 쉬워서 위험하다.",
        "columns": ["상태"],
        "rows": ([{"상태": "계측기가 하나도 등록돼 있지 않아 — 설정 → 계측기 관리에서 등록해줘"}]
                 if gauge_count == 0 else []),
    })

    checks.append({
        "key": "no_integrity_hash",
        "severity": "low",
        "title": "무결성 검증 기준값이 없는 승인 성적서",
        "desc": "위변조 검증 기능이 생기기 전에 승인된 건이라 '승인 당시 그대로인지' 확인할 수 없다. "
                "새로 승인되는 건부터는 자동으로 기준값이 저장된다.",
        "columns": ["성적서 번호", "자재번호", "업체", "검사일"],
        "rows": q("""SELECT id, material_no, supplier, inspect_date
                       FROM inspections
                      WHERE status='approved' AND (content_hash IS NULL OR content_hash='')
                      ORDER BY id DESC"""),
    })

    checks.append({
        "key": "no_drawing",
        "severity": "low",
        "title": "도면이 연결 안 된 자재",
        "desc": "검사 화면에서 도면을 못 봐. 도면 파일명이 자재번호와 같으면 자동으로 연결돼.",
        "columns": ["자재번호", "자재명"],
        "rows": q("""SELECT material_no, material_name FROM materials
                      WHERE drawing_file IS NULL OR drawing_file=''
                      ORDER BY material_no"""),
    })

    checks.append({
        "key": "dup_spec",
        "severity": "low",
        "title": "같은 자재 안에 규격 표기가 똑같은 항목",
        "desc": "성적서에 같은 내용이 두 줄로 나와. 일부러 나눈 거면 그대로 둬도 돼.",
        "columns": ["자재번호", "항목들", "규격 표기"],
        "rows": q("""SELECT material_no,
                            GROUP_CONCAT(item_name, ', ') AS item_names,
                            spec_display
                       FROM specs
                      WHERE spec_display IS NOT NULL AND TRIM(spec_display) != ''
                      GROUP BY material_no, TRIM(spec_display)
                     HAVING COUNT(*) > 1
                      ORDER BY material_no"""),
    })

    checks.append({
        "key": "star_mismatch",
        "severity": "low",
        "title": "중요항목(*) 표시와 AQL이 안 맞는 항목",
        "desc": "원칙은 'AQL 0.65 = 중요항목(*)'인데, *는 붙어 있고 AQL은 0.65가 아니야. "
                "성적서에는 *가 그대로 나가니 AQL 쪽을 확인해줘.",
        "columns": ["자재번호", "항목", "AQL", "규격 표기"],
        "rows": q("""SELECT material_no, item_name, aql, spec_display
                       FROM specs
                      WHERE TRIM(item_name) LIKE '*%' AND (aql IS NULL OR CAST(aql AS TEXT) != '0.65')
                      ORDER BY material_no"""),
    })

    conn.close()
    return checks
