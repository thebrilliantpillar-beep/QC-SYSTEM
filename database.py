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
    ]:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE ncr ADD COLUMN {col} {definition}")

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


def search_materials(query=None, search_by="all"):
    """
    query: 검색어. search_by: 'material_no' / 'material_name' / 'method' / 'spec' / 'all'
    'method'(검사방식)는 specs.inspect_method에서, 'spec'(규격 표기)는 specs.spec_display에서 매칭.
    'all'(전체)은 자재번호·자재명·규격 표기·검사방식을 모두 훑는다.
    """
    conn = get_conn()
    if search_by == "method_empty":
        # 검사방식(inspect_method)이 비어 있는 항목을 가진 자재. 검색어와 무관하게 동작한다.
        rows = conn.execute("""
            SELECT DISTINCT m.material_no, m.material_name FROM materials m
            JOIN specs s ON s.material_no = m.material_no
            WHERE s.inspect_method IS NULL OR TRIM(s.inspect_method) = ''
            ORDER BY m.material_no
        """).fetchall()
        conn.close()
        return rows
    if not query:
        rows = conn.execute("SELECT material_no, material_name FROM materials ORDER BY material_no").fetchall()
        conn.close()
        return rows

    like = f"%{query}%"
    if search_by == "material_no":
        rows = conn.execute(
            "SELECT material_no, material_name FROM materials WHERE material_no LIKE ? ORDER BY material_no",
            (like,)
        ).fetchall()
    elif search_by == "material_name":
        rows = conn.execute(
            "SELECT material_no, material_name FROM materials WHERE material_name LIKE ? ORDER BY material_no",
            (like,)
        ).fetchall()
    elif search_by == "method":
        rows = conn.execute("""
            SELECT DISTINCT m.material_no, m.material_name FROM materials m
            JOIN specs s ON s.material_no = m.material_no
            WHERE s.inspect_method LIKE ?
            ORDER BY m.material_no
        """, (like,)).fetchall()
    elif search_by == "spec":
        rows = conn.execute("""
            SELECT DISTINCT m.material_no, m.material_name FROM materials m
            JOIN specs s ON s.material_no = m.material_no
            WHERE s.spec_display LIKE ?
            ORDER BY m.material_no
        """, (like,)).fetchall()
    else:
        # 전체: 자재번호·자재명·규격 표기·검사방식 어디에 있든 잡는다.
        # (규격 표기 spec_display를 빠뜨려서 규격에만 있는 검색어가 안 걸리던 버그 수정)
        rows = conn.execute("""
            SELECT DISTINCT m.material_no, m.material_name FROM materials m
            LEFT JOIN specs s ON s.material_no = m.material_no
            WHERE m.material_no LIKE ? OR m.material_name LIKE ?
               OR s.spec_display LIKE ? OR s.inspect_method LIKE ?
            ORDER BY m.material_no
        """, (like, like, like, like)).fetchall()
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
    """이미 intake_list에 등록된 동일 항목 찾기.
    po_number 있으면 material_no+po_number, 없으면 material_no+receive_date+supplier로 비교."""
    if not rows:
        return []
    conn = get_conn()
    dups = []
    for r in rows:
        mn  = r["material_no"]
        po  = (r.get("po_number") or "").strip()
        rd  = (r.get("receive_date") or "").strip()
        sup = (r.get("supplier") or "").strip()
        if po:
            hit = conn.execute(
                "SELECT id FROM intake_list WHERE material_no=? AND po_number=?",
                (mn, po)).fetchone()
        else:
            hit = conn.execute(
                "SELECT id FROM intake_list WHERE material_no=? AND receive_date=? AND supplier=?",
                (mn, rd, sup)).fetchone()
        if hit:
            dups.append(r)
    conn.close()
    return dups


def add_intake_bulk(rows):
    """
    rows: list of dict (material_no, quantity, supplier, receive_date, po_number, product_name, assembly_no)
    붙여넣기로 여러 건을 한 번에 등록
    """
    conn = get_conn()
    cur = conn.cursor()
    for r in rows:
        cur.execute("""
            INSERT INTO intake_list (material_no, quantity, supplier, receive_date, po_number, product_name, assembly_no)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (r["material_no"], r.get("quantity"), r.get("supplier"),
              r.get("receive_date"), r.get("po_number"), r.get("product_name"), r.get("assembly_no")))
    conn.commit()
    conn.close()


def set_intake_status(intake_id, status):
    conn = get_conn()
    conn.execute("UPDATE intake_list SET status = ? WHERE id = ?", (status, intake_id))
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


def search_intake(query, status=None):
    """자재번호/제품명/납품업체/발주번호 기준으로 입고 리스트 검색."""
    conn = get_conn()
    like = f"%{query}%"
    if status:
        rows = conn.execute("""
            SELECT * FROM intake_list
            WHERE status = ? AND (material_no LIKE ? OR product_name LIKE ?
                                  OR supplier LIKE ? OR po_number LIKE ?)
            ORDER BY id DESC
        """, (status, like, like, like, like)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM intake_list
            WHERE material_no LIKE ? OR product_name LIKE ?
                  OR supplier LIKE ? OR po_number LIKE ?
            ORDER BY id DESC
        """, (like, like, like, like)).fetchall()
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


# ---------- 검사(성적서) 생성/조회 ----------

def create_inspection(header, items_with_results, overall_result, intake_id=None,
                       est_time_label=None, actual_time_sec=None, created_by_user_id=None,
                       total_time_sec=None):
    """
    header: dict (material_no, material_name, supplier, po_number,
                   receive_date, inspect_date, inspector, quantity)
    items_with_results: list of dict (item_name, measured_value, max_value, min_value, result)
    """
    conn = get_conn()
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
               s.material_name AS part_material_name
        FROM inspection_items ii
        LEFT JOIN specs s
               ON s.material_no = COALESCE(ii.part_material_no,
                                            (SELECT material_no FROM inspections WHERE id = ?))
              AND s.item_name   = ii.item_name
        WHERE ii.inspection_id = ?
        ORDER BY ii.id
    """, (inspection_id, inspection_id)).fetchall()
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

    # 오늘 검사 완료 건수
    inspected = conn.execute(
        "SELECT COUNT(*) FROM inspections WHERE date(created_at) = ?", (today,)
    ).fetchone()[0]

    # 오늘 불량/검토필요 건수
    defects = conn.execute(
        "SELECT COUNT(*) FROM inspections WHERE date(created_at) = ? AND overall_result NOT IN ('합격', '')",
        (today,)
    ).fetchone()[0]

    # 인원별 검사 소요시간 (오늘) — total_time_sec(성적서 총 측정시간) 우선, 없으면 actual_time_sec fallback
    time_rows = conn.execute("""
        SELECT inspector,
               SUM(COALESCE(total_time_sec, actual_time_sec, 0)) as total_sec
        FROM inspections
        WHERE date(created_at) = ?
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

    # 2. 통보서 작성 필요 — 불합격 확정, NCR 없음
    ncr_write = q("""
        SELECT i.id, i.material_no, m.material_name, i.supplier, i.inspector,
               i.inspect_date, i.receive_date, i.remark_approver
          FROM inspections i
          LEFT JOIN materials m ON m.material_no = i.material_no
         WHERE i.status = 'approved' AND i.approval_type = 'failed'
           AND NOT EXISTS (SELECT 1 FROM ncr n WHERE n.inspection_id = i.id)
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
        "SELECT * FROM gauge_master ORDER BY expiry_date ASC, gauge_no ASC"
    ).fetchall()
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
    conn.execute("DELETE FROM suppliers WHERE name = ?", (name,))
    conn.commit()
    conn.close()


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
               action_required, due_date, issued_by, issued_date, lot_number=None, receive_date=None):
    ncr_no = _next_ncr_no()
    conn = get_conn()
    cur = conn.execute("""
        INSERT INTO ncr (ncr_no, inspection_id, material_no, material_name, supplier,
            defect_description, action_required, due_date, issued_by, issued_date, status,
            lot_number, receive_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
    """, (ncr_no, inspection_id, material_no, material_name, supplier,
          defect_description, action_required, due_date, issued_by, issued_date,
          lot_number, receive_date))
    conn.commit()
    ncr_id = cur.lastrowid
    conn.close()
    return ncr_id, ncr_no


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


def update_inspection_status(inspection_id, status, approver=None, reject_reason=None, approval_type=None):
    conn = get_conn()
    if approval_type is not None:
        conn.execute("""
            UPDATE inspections
            SET status = ?, approver = ?, approved_at = datetime('now', 'localtime'),
                reject_reason = ?, approval_type = ?
            WHERE id = ?
        """, (status, approver, reject_reason, approval_type, inspection_id))
    else:
        conn.execute("""
            UPDATE inspections
            SET status = ?, approver = ?, approved_at = datetime('now', 'localtime'),
                reject_reason = ?
            WHERE id = ?
        """, (status, approver, reject_reason, inspection_id))
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


def list_all_assemblies():
    """조립품 목록 + 각 조립품의 파츠 수. 자재 마스터에 없는 파츠 수(missing)도 같이 센다."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT m.*,
               (SELECT COUNT(*) FROM assembly_components c WHERE c.assembly_id = m.id) AS part_count,
               (SELECT COUNT(*) FROM assembly_components c
                 WHERE c.assembly_id = m.id
                   AND NOT EXISTS (SELECT 1 FROM materials mt WHERE mt.material_no = c.component_no)
               ) AS missing_count
          FROM assembly_masters m
         ORDER BY m.assembly_no
    """).fetchall()
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


def _lot_state(status, approval_type):
    """로트 하나의 최종 상태. 불량률은 '판정이 확정된 것'만으로 계산한다.

    superseded(재검사로 대체된 옛 성적서)는 집계에서 아예 빼야 한다.
    같은 입고 건을 재검사하면 성적서가 하나 더 생기는데, 옛 건까지 세면
    같은 로트의 수량이 두 번 잡혀서 검사 수량이 부풀려진다.
    """
    if status == "superseded":
        return "대체됨"
    if status == "approved":
        if approval_type == "failed":
            return "불합격"
        if approval_type == "special":
            return "특채"
        return "합격"
    return "미결"      # pending / rejected


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

    # ── 오늘 검사한 것 ──
    inspected = rows("""SELECT * FROM inspections WHERE date(created_at)=? AND status!='superseded'
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
                   supplier=None, po_number=None, material=None, states=None):
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
                      ORDER BY n.id DESC LIMIT 1) AS ncr_id
               FROM inspections i
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
    # 재검사로 대체된 옛 성적서는 집계에서 제외 (같은 로트가 두 번 잡히는 것 방지)
    sql += " AND i.status != 'superseded'"
    sql += " ORDER BY i.inspect_date DESC, i.id DESC"

    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    # 로트 상태(합격/특채/불합격/미결) 필터는 status+approval_type 조합이라 SQL이 지저분해진다.
    # 판정 규칙이 _lot_state() 한 곳에만 있도록 여기서 걸러낸다.
    if states:
        rows = [r for r in rows if _lot_state(r["status"], r["approval_type"]) in states]

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

    conn.close()

    # ---- 집계 ----
    def blank():
        return {"로트": 0, "수량": 0, "합격수량": 0, "특채수량": 0,
                "불합격수량": 0, "미결수량": 0, "불합격로트": 0,
                "검사표본수": 0, "표본불량수": 0}

    def add(acc, r):
        qty = int(r["quantity"] or 0)
        state = _lot_state(r["status"], r["approval_type"])
        acc["로트"] += 1
        acc["수량"] += qty
        acc["검사표본수"] += sample_count_map.get(r["id"], 0)
        acc["표본불량수"] += sum(int(m[1]) for m in _DEFECT_RE.findall(r.get("remark_inspector") or ""))
        if state == "합격":
            acc["합격수량"] += qty
        elif state == "특채":
            acc["특채수량"] += qty
        elif state == "불합격":
            acc["불합격수량"] += qty
            acc["불합격로트"] += 1
        else:
            acc["미결수량"] += qty

    def finish(acc):
        confirmed = acc["합격수량"] + acc["특채수량"] + acc["불합격수량"]
        acc["확정수량"] = confirmed
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

    return {
        "기간": {"시작": start_date, "종료": end_date, "유형": period_type},
        "필터": {"업체": ", ".join(suppliers) if suppliers else "전체",
                 "로트번호": ", ".join(po_numbers) if po_numbers else "전체",
                 "자재/제품명": material or "전체",
                 "판정상태": ", ".join(states) if states else "전체"},
        "불량률기준": "수량기준 (불합격 확정수량 ÷ 판정 확정수량)",
        "요약": summary,
        "기간별": period_list,
        "업체별": supplier_list,
        "자재별": material_list,
        "불량항목순위": top_items,
        "소요시간": time_stats,
        "변경점": change_points,
        "성적서목록": rows,
    }


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
    자동차 부품은 보통 Cpk ≥ 1.33 을 요구한다.
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
