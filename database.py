# -*- coding: utf-8 -*-
# Copyright (c) 2026 윤주호. All rights reserved.
# 무단 복제·배포·수정을 금합니다.
"""DB 초기화 및 데이터 접근 — SQLite (파일 하나 = DB 전체)"""
import sqlite3, os
from datetime import datetime as _dt

DB_PATH = os.path.join(os.path.dirname(__file__), "iqc.db")


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

    conn.execute("UPDATE materials SET material_no = ?, material_name = ? WHERE material_no = ?",
                (new_no, new_name, old_no))
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
    query: 검색어. search_by: 'material_no' / 'material_name' / 'method' / 'all'
    'method'(검사방식)는 specs.inspect_method에서 매칭.
    """
    conn = get_conn()
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
    else:
        rows = conn.execute("""
            SELECT DISTINCT m.material_no, m.material_name FROM materials m
            LEFT JOIN specs s ON s.material_no = m.material_no
            WHERE m.material_no LIKE ? OR m.material_name LIKE ? OR s.inspect_method LIKE ?
            ORDER BY m.material_no
        """, (like, like, like)).fetchall()
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
    conn.execute("""
        INSERT INTO specs (material_no, material_name, item_name, spec_display,
                            judge_type, lower_limit, upper_limit, inspect_method, aql, item_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (material_no, material_name, item_name, spec_display,
          judge_type, lower_limit, upper_limit, inspect_method, aql, item_order))
    if not conn.execute("SELECT 1 FROM materials WHERE material_no = ?", (material_no,)).fetchone():
        conn.execute("INSERT INTO materials (material_no, material_name) VALUES (?, ?)",
                    (material_no, material_name))
    elif material_name:
        conn.execute("UPDATE materials SET material_name = ? WHERE material_no = ? AND (material_name IS NULL OR material_name = '')",
                    (material_name, material_no))
    conn.commit()
    conn.close()


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

def add_intake_bulk(rows):
    """
    rows: list of dict (material_no, quantity, supplier, receive_date, po_number, product_name)
    붙여넣기로 여러 건을 한 번에 등록
    """
    conn = get_conn()
    cur = conn.cursor()
    for r in rows:
        cur.execute("""
            INSERT INTO intake_list (material_no, quantity, supplier, receive_date, po_number, product_name)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (r["material_no"], r.get("quantity"), r.get("supplier"),
              r.get("receive_date"), r.get("po_number"), r.get("product_name")))
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
                       est_time_label=None, actual_time_sec=None, created_by_user_id=None):
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
                                  created_by_user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
    """, (intake_id, header["material_no"], header.get("material_name"), header.get("supplier"),
          header.get("po_number"), header.get("receive_date"), header.get("inspect_date"),
          header.get("inspector"), header.get("quantity"), overall_result,
          est_time_label, actual_time_sec, created_by_user_id))
    inspection_id = cur.lastrowid

    if intake_id:
        cur.execute("UPDATE intake_list SET status = '검사완료' WHERE id = ?", (intake_id,))

    for it in items_with_results:
        cur.execute("""
            INSERT INTO inspection_items (inspection_id, item_name, measured_value,
                                           max_value, min_value, result, gauge_expiry, part_material_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (inspection_id, it["item_name"], it.get("measured_value"),
              it.get("max_value"), it.get("min_value"), it.get("result"),
              it.get("gauge_expiry"), it.get("part_material_no") or header["material_no"]))

    conn.commit()
    conn.close()
    return inspection_id


def update_inspection_items(inspection_id, inspect_date, inspector, items_with_results, overall_result,
                             est_time_label=None, actual_time_sec=None):
    """pending 상태 성적서 측정값·판정 전체 갱신"""
    conn = get_conn()
    header_row = conn.execute("SELECT material_no FROM inspections WHERE id=?", (inspection_id,)).fetchone()
    default_material_no = header_row["material_no"] if header_row else None
    if est_time_label is not None or actual_time_sec is not None:
        conn.execute("""
            UPDATE inspections SET inspect_date=?, inspector=?, overall_result=?,
                                    est_time_label=?, actual_time_sec=? WHERE id=?
        """, (inspect_date, inspector, overall_result, est_time_label, actual_time_sec, inspection_id))
    else:
        conn.execute("""
            UPDATE inspections SET inspect_date=?, inspector=?, overall_result=? WHERE id=?
        """, (inspect_date, inspector, overall_result, inspection_id))
    conn.execute("DELETE FROM inspection_items WHERE inspection_id=?", (inspection_id,))
    for it in items_with_results:
        conn.execute("""
            INSERT INTO inspection_items (inspection_id, item_name, measured_value, max_value, min_value, result, gauge_expiry, part_material_no)
            VALUES (?,?,?,?,?,?,?,?)
        """, (inspection_id, it["item_name"], it.get("measured_value"),
              it.get("max_value"), it.get("min_value"), it.get("result"),
              it.get("gauge_expiry"), it.get("part_material_no") or default_material_no))
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
