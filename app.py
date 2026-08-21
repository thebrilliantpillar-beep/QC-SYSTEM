# -*- coding: utf-8 -*-
# Copyright (c) 2026 윤주호. All rights reserved.
# 무단 복제·배포·수정을 금합니다.
"""IQC 성적서 시스템 — Flask 골격 (2단계: DB + 판정 로직 + 기본 화면)"""

# ──────────────────────────────────────────────
# 라이선스 체크 — 절대 이 블록을 제거하거나 수정하지 말 것
# Copyright (c) 2026 윤주호. 무단 사용 금지.
# ──────────────────────────────────────────────
def _check_license():
    import urllib.request as _ur
    import datetime as _d
    import sys as _sys

    # ① 만료일 체크 (이 날짜 이후 자동 종료)
    expiry = _d.date(2027, 12, 31)
    if _d.date.today() > expiry:
        print("\n[오류] 소프트웨어 사용 기간이 만료되었습니다. 개발자(윤주호)에게 문의하세요.\n")
        _sys.exit(1)

    # ② 원격 활성화 체크 (개발자가 원격으로 즉시 비활성화 가능)
    _REMOTE_URL = "https://raw.githubusercontent.com/thebrilliantpillar-beep/QC-SYSTEM/main/status.txt"
    try:
        res = _ur.urlopen(_REMOTE_URL, timeout=5)
        status = res.read().strip()
        if status != b"OK":
            print("\n[오류] 라이선스가 비활성화되었습니다. 개발자(윤주호)에게 문의하세요.\n")
            _sys.exit(1)
    except Exception:
        # 인터넷 연결 없으면 원격 체크 건너뜀 (NAS 내부망 환경 고려)
        pass

_check_license()
# ──────────────────────────────────────────────

import os, base64, io, time, shutil
from datetime import datetime as _dt
from functools import wraps
from datetime import timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, g, send_file
import database as db
import report_builder
import spec_import as spec_import_module

app = Flask(__name__)
app.secret_key = "dev-secret-change-later"  # 배포 전 반드시 교체
app.permanent_session_lifetime = timedelta(hours=24)  # 하루 한 번 로그인하면 그 뒤로 계속 유지 (admin 제외)

SIGNATURE_DIR = os.path.join(os.path.dirname(__file__), "static", "signatures")
os.makedirs(SIGNATURE_DIR, exist_ok=True)

BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")
os.makedirs(BACKUP_DIR, exist_ok=True)
BACKUP_README = os.path.join(BACKUP_DIR, "README.txt")

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "admin1234"
ADMIN_IDLE_TIMEOUT_SEC = 600  # "users" 권한 보유 계정만 10분 무동작 시 자동 로그아웃

# 개별 권한 종류 — admin이 계정마다 하나하나 부여/회수
PERM_LABELS = {
    "intake": "입고 리스트",
    "spec": "규격 관리",
    "inspect": "검사 입력",
    "inspect_all": "타인 성적서도 수정",
    "approve": "승인·반려·특채",
    "output": "출력",
    "users": "계정 관리 (10분 자동로그아웃 대상)",
    "logs": "활동 로그 열람",
}
ALL_PERMS = list(PERM_LABELS.keys())


def record_change(action, target_type=None, target_id=None, detail=None):
    """
    데이터가 새로 생기거나 수정될 때마다 호출:
    1) activity_log 테이블에 기록 (users 권한 보유자만 /logs에서 조회 가능)
    2) iqc.db 전체를 backups/ 밑에 타임스탬프 붙여 복사
    3) backups/README.txt에 사람이 읽을 수 있는 상세 로그 한 줄 추가
    """
    user_id = g.user["id"] if g.user else None
    username = g.user["username"] if g.user else "system"
    perms_summary = (g.user["permissions"] if g.user else None) or ""

    log_id = db.log_activity(user_id, username, perms_summary, action, target_type, target_id, detail)

    now = _dt.now()
    ts = now.strftime("%Y%m%d_%H%M%S")
    backup_name = f"iqc_{ts}_{log_id}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    try:
        shutil.copy(db.DB_PATH, backup_path)
    except Exception:
        backup_name = "(백업 실패)"

    line = (f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] "
            f"사용자: {username} | "
            f"액션: {action} | 대상: {target_type or '-'} {target_id or ''} | "
            f"상세: {detail or '-'} | 백업파일: {backup_name}\n")
    try:
        with open(BACKUP_README, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


@app.context_processor
def inject_perm_labels():
    user_perms = _user_perms(g.user) if hasattr(g, "user") else set()
    return {"perm_labels": PERM_LABELS, "user_perms": user_perms,
            "status_display": status_display, "sample_size": sample_size}


def status_display(insp):
    """
    성적서 한 건의 (한글 상태 라벨, badge css 클래스) 반환.
    승인된 건은 합격/불합격/특채 여부까지 구분해서 보여준다.
    """
    status = insp["status"]
    if status == "pending":
        if insp["overall_result"] == "검토필요":
            return ("검토필요", "review")
        return ("대기중", "pending")
    if status == "rejected":
        return ("반려", "fail")
    if status == "superseded":
        return ("재검사 진행됨", "muted")
    if status == "approved":
        if insp["approval_type"] == "special":
            return ("특채 승인", "special")
        return ("합격 승인", "pass")
    return (status, "pending")


def ensure_default_admin():
    """계정이 하나도 없으면(최초 실행) 전체 권한을 가진 기본 계정을 하나 만들어둔다."""
    if db.count_users() == 0:
        db.create_user(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD, "관리자", ",".join(ALL_PERMS))


# ---------- 로그인/권한 ----------

def _user_perms(user_row):
    if user_row is None:
        return set()
    return set(p for p in (user_row["permissions"] or "").split(",") if p)


@app.before_request
def load_logged_in_user():
    user_id = session.get("user_id")
    g.user = db.get_user(user_id) if user_id else None

    if g.user is not None and "users" in _user_perms(g.user):
        # "users"(계정관리) 권한 보유 계정만 10분 무동작 시 자동 로그아웃
        last_seen = session.get("last_seen")
        now = time.time()
        if last_seen and (now - last_seen) > ADMIN_IDLE_TIMEOUT_SEC:
            session.clear()
            g.user = None
        else:
            session["last_seen"] = now


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def perm_required(*required_perms):
    """나열된 권한 중 하나라도 있으면 통과. 아무것도 안 넘기면(perm_required()) 'users' 권한 보유자만 통과."""
    checks = required_perms or ("users",)

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("login", next=request.path))
            user_perms = _user_perms(g.user)
            if not any(p in user_perms for p in checks):
                flash("이 화면에 접근할 권한이 없어.")
                return redirect(url_for("home"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


# 이전 role_required 이름을 쓰던 코드 호환용 별칭 (아래에서 전부 perm_required로 교체할 예정)
role_required = perm_required
admin_required = perm_required()  # "users" 권한 보유자만


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db.get_user_by_username(username)
        if user is None or user["password"] != password:
            flash("아이디 또는 비밀번호가 틀렸어.")
            return redirect(url_for("login"))

        session.permanent = True
        session["user_id"] = user["id"]
        session["last_seen"] = time.time()
        next_url = request.args.get("next") or url_for("home")
        return redirect(next_url)

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/my-password", methods=["GET", "POST"])
@login_required
def my_password():
    """본인 비밀번호 직접 변경 (임시 계정 발급 후 본인이 바꾸는 용도)."""
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "").strip()
        if g.user["password"] != current:
            flash("현재 비밀번호가 틀렸어.")
        elif not new_password:
            flash("새 비밀번호를 입력해줘.")
        else:
            db.update_user_password(g.user["id"], new_password)
            flash("비밀번호가 변경됐어.")
            record_change("본인 비밀번호 변경", "user", g.user["username"])
        return redirect(url_for("my_password"))

    return render_template("my_password.html")


# ---------- DB 백업 다운로드 ----------

@app.route("/download-db")
@perm_required("users")
def download_db():
    db_path = os.path.join(os.path.dirname(__file__), "iqc.db")
    today = _dt.now().strftime("%Y%m%d_%H%M%S")
    filename = f"iqc_backup_{today}.db"
    record_change("DB 백업 다운로드", "system", 0, f"다운로드: {filename}")
    return send_file(db_path, as_attachment=True, download_name=filename)


# ---------- 계정 관리 (관리자 전용) ----------

@app.route("/users", methods=["GET", "POST"])
@perm_required("users")
def user_management():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "").strip()
            display_name = request.form.get("display_name", "").strip()
            perms = [p for p in request.form.getlist("permissions") if p in ALL_PERMS]
            if not username or not password:
                flash("아이디와 비밀번호를 입력해줘.")
            elif db.get_user_by_username(username):
                flash("이미 있는 아이디야.")
            else:
                db.create_user(username, password, display_name, ",".join(perms))
                flash(f"계정 '{username}' 만들어졌어. 임시 비밀번호를 알려주고, 첫 로그인 후 '내 비밀번호 변경'에서 바꾸라고 안내해줘.")
                perm_names = ", ".join(PERM_LABELS.get(p, p) for p in perms) or "없음"
                record_change("계정 생성", "user", username, f"권한: {perm_names}")

        elif action == "delete":
            user_id = int(request.form.get("user_id"))
            if user_id == g.user["id"]:
                flash("본인 계정은 삭제할 수 없어.")
            else:
                target_user = db.get_user(user_id)
                db.delete_user(user_id)
                flash("계정 삭제됐어.")
                record_change("계정 삭제", "user", target_user["username"] if target_user else user_id)

        elif action == "reset_password":
            user_id = int(request.form.get("user_id"))
            new_password = request.form.get("new_password", "").strip()
            if not new_password:
                flash("새 비밀번호를 입력해줘.")
            else:
                db.update_user_password(user_id, new_password)
                flash("비밀번호가 변경됐어.")
                target_user = db.get_user(user_id)
                record_change("비밀번호 초기화(관리자)", "user", target_user["username"] if target_user else user_id)

        elif action == "update_profile":
            user_id = int(request.form.get("user_id"))
            username = request.form.get("username", "").strip()
            display_name = request.form.get("display_name", "").strip()
            if not username:
                flash("아이디는 비워둘 수 없어.")
            else:
                ok, error = db.update_user_profile(user_id, username, display_name)
                if not ok:
                    flash(error)
                else:
                    flash("계정 정보가 수정됐어.")
                    record_change("계정 정보 수정", "user", username, f"이름: {display_name}")

        elif action == "update_permissions":
            user_id = int(request.form.get("user_id"))
            perms = [p for p in request.form.getlist("permissions") if p in ALL_PERMS]
            target_user = db.get_user(user_id)
            if target_user is None:
                flash("존재하지 않는 계정이야.")
            elif user_id == g.user["id"] and "users" not in perms:
                flash("본인의 계정 관리 권한은 스스로 회수할 수 없어(잠길 수 있어서). 다른 관리자 계정으로 바꿔줘.")
            else:
                db.update_user_permissions(user_id, ",".join(perms))
                flash(f"'{target_user['username']}' 계정 권한이 변경됐어.")
                perm_names = ", ".join(PERM_LABELS.get(p, p) for p in perms) or "없음"
                record_change("계정 권한 변경", "user", target_user["username"], f"권한: {perm_names}")

        return redirect(url_for("user_management"))

    users = db.list_users()
    return render_template("users.html", users=users, current_user_id=g.user["id"],
                           perm_labels=PERM_LABELS, all_perms=ALL_PERMS)


@app.route("/logs")
@perm_required("logs")
def activity_logs():
    logs = db.list_activity_logs(limit=300)
    return render_template("logs.html", logs=logs)



# AQL별 샘플수량 구간표 — 성적서_양식.xlsx의 IFS 수식을 그대로 옮긴 것.
# (수량 상한, 샘플수) 튜플 리스트. 새 AQL값이 나오면 이 표에 추가해야 함(임의 계산 금지).
AQL_TABLES = {
    4: [(8, 2), (15, 2), (25, 3), (50, 5), (90, 5), (150, 8), (280, 13), (500, 20),
        (1200, 32), (3200, 50), (10000, 88), (35000, 125), (150000, 200), (500000, 315),
        (float("inf"), 500)],
    0.65: [(8, 3), (15, 5), (25, 8), (50, 13), (90, 20), (150, 32), (280, 50), (500, 88),
           (1200, 125), (3200, 200), (10000, 315), (35000, 500), (150000, 800), (500000, 1250),
           (float("inf"), 2000)],
    1.5: [(8, 2), (15, 3), (25, 5), (50, 8), (90, 13), (150, 20), (280, 32), (500, 50),
          (1200, 88), (3200, 125), (10000, 200), (35000, 315), (150000, 500), (500000, 800),
          (float("inf"), 1250)],
}


def sample_size(aql, quantity):
    """
    AQL과 입고수량으로 샘플수량 계산.
    - aql이 숫자(4/1.5/0.65 등)면 기존 구간표 사용
    - aql == "전수" 면 입고수량 전부
    - aql이 "퍼센트N" (예: "퍼센트10") 이면 입고수량의 N%를 반올림한 정수 (최소 1개)
    표에 없는 숫자 AQL이면 None(화면에서 확인 요청).
    """
    if quantity is None or aql is None:
        return None
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        return None

    aql_str = str(aql).strip()
    if aql_str == "전수":
        return quantity
    if aql_str.startswith("퍼센트"):
        try:
            percent = float(aql_str[3:])
        except ValueError:
            return None
        # "반올림해서 정수" — 사사오입(0.5 이상 올림) 방식으로 계산
        raw = quantity * percent / 100
        result = int(raw + 0.5) if raw >= 0 else int(raw - 0.5)
        return max(1, min(quantity, result))

    try:
        aql_num = float(aql)
        if aql_num == int(aql_num):
            aql_num = int(aql_num)
    except (TypeError, ValueError):
        return None
    table = AQL_TABLES.get(aql_num)
    if not table:
        return None
    for limit, size in table:
        if quantity <= limit:
            return min(quantity, size)
    return quantity


def format_duration(total_sec):
    """N시간 N분 N초 형식. 시간/분이 0이면 생략, 다 0이면 '0초'."""
    total_sec = int(total_sec or 0)
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}시간")
    if m:
        parts.append(f"{m}분")
    if s or not parts:
        parts.append(f"{s}초")
    return " ".join(parts)


BASE_AQL = 4          # 기준 AQL(항상 최소 샘플수) — 이 그룹 샘플은 스톱워치로 실측한 사이클 시간 사용
EXTRA_SAMPLE_SEC = 10  # 기준 AQL 그룹에서 이미 측정한 수량을 넘는 추가 샘플 1개당 걸리는 시간(고정값)


def _unique_aql_sample_qty(specs_with_sample):
    """AQL 그룹당 한 번만 세어 {AQL(숫자 또는 '전수'/'퍼센트N' 문자열): 샘플수량} 딕셔너리 반환."""
    seen_aql = {}
    for s in specs_with_sample:
        aql = s.get("aql")
        qty = s.get("sample_qty")
        if aql is None or qty is None:
            continue
        aql_str = str(aql).strip()
        if aql_str == "전수" or aql_str.startswith("퍼센트"):
            key = aql_str
        else:
            try:
                key = float(aql)
                if key == int(key):
                    key = int(key)
            except (TypeError, ValueError):
                continue
        if key not in seen_aql:
            seen_aql[key] = qty
    return seen_aql


def compute_total_time_sec(specs_with_sample, per_cycle_sec):
    """
    총 측정 시간 계산.
    - 기준(AQL4) 샘플수 × 개당(1사이클) 시간
    - 다른 AQL 그룹은 (그 그룹 샘플수 - 기준 샘플수)만큼만 추가로 측정하면 되므로
      그 차이 × EXTRA_SAMPLE_SEC(10초)만 더함 (기준 그룹에서 이미 측정한 수량은 차감)
    """
    seen_aql = _unique_aql_sample_qty(specs_with_sample)
    base_qty = seen_aql.get(BASE_AQL, 0)
    total = base_qty * per_cycle_sec
    for aql_num, qty in seen_aql.items():
        if aql_num == BASE_AQL:
            continue
        extra = max(0, qty - base_qty)
        total += extra * EXTRA_SAMPLE_SEC
    return total


@app.route("/")
@login_required
def home():
    inspections = db.list_inspections()
    pending_intake = db.list_intake(status="대기")
    return render_template("home.html", inspections=inspections, pending_count=len(pending_intake))


# ---------- 입고 리스트 (엑셀 붙여넣기 등록) ----------

@app.route("/intake", methods=["GET", "POST"])
@perm_required("intake")
def intake():
    if request.method == "POST":
        import json
        raw_json = request.form.get("rows_json", "")
        try:
            grid_rows = json.loads(raw_json) if raw_json else []
        except (ValueError, TypeError):
            grid_rows = []

        rows = []
        errors = []
        # 스프레드시트 열 순서: 입고날짜, 납품업체, 발주번호, 제품명, 자재번호, 입고수량
        for line_no, cols in enumerate(grid_rows, start=1):
            cols = [(c or "").strip() for c in cols]
            while len(cols) < 6:
                cols.append("")
            receive_date, supplier, po_number, product_name, material_no, quantity = cols[:6]
            if not any([receive_date, supplier, po_number, product_name, material_no, quantity]):
                continue  # 완전히 빈 행은 건너뜀
            if not material_no:
                errors.append(f"{line_no}번째 행: 자재번호가 비어 있어 → 건너뜀")
                continue
            rows.append({
                "material_no": material_no,
                "quantity": quantity or None,
                "supplier": supplier,
                "receive_date": receive_date,
                "po_number": po_number,
                "product_name": product_name,
            })

        if errors:
            for e in errors:
                flash(e)
        if rows:
            db.add_intake_bulk(rows)
            flash(f"{len(rows)}건 등록 완료")
            material_list = ", ".join(r["material_no"] for r in rows[:10])
            if len(rows) > 10:
                material_list += " 외"
            record_change("입고 리스트 등록", "intake", None, f"{len(rows)}건 ({material_list})")
        elif not errors:
            flash("등록할 내용이 없어.")
        return redirect(url_for("intake"))

    pending = db.list_intake(status="대기")
    done = db.list_intake(status="검사완료")
    registered = {m["material_no"] for m in db.get_materials()}
    group_nos = {gr["group_no"] for gr in db.list_material_groups()}
    return render_template("intake.html", pending=pending, done=done,
                           registered=registered, group_nos=group_nos)


# ---------- 규격 관리 ----------

@app.route("/spec")
@perm_required("spec")
def spec_list():
    query = request.args.get("q", "").strip()
    search_by = request.args.get("by", "all")
    materials = db.search_materials(query, search_by) if query else db.get_materials()
    return render_template("spec.html", materials=materials, query=query, search_by=search_by)


@app.route("/spec/quick_add", methods=["GET", "POST"])
@perm_required("spec")
def spec_quick_add():
    """
    자재번호+제품명을 스프레드시트형으로 여러 줄 한번에 등록.
    "측정조건 입력" 팝업에서 항목까지 같이 입력했으면 그 항목들도 함께 등록됨.
    """
    if request.method == "POST":
        import json
        raw_rows_json = request.form.get("rows_json", "")
        raw_items_json = request.form.get("items_json", "")
        try:
            grid_rows = json.loads(raw_rows_json) if raw_rows_json else []
        except (ValueError, TypeError):
            grid_rows = []
        try:
            items_per_row = json.loads(raw_items_json) if raw_items_json else []
        except (ValueError, TypeError):
            items_per_row = []

        rows = []
        rows_items = []  # rows와 같은 순서로 매칭되는 항목 리스트
        for idx, cols in enumerate(grid_rows):
            cols = [(c or "").strip() for c in cols]
            while len(cols) < 2:
                cols.append("")
            material_no, material_name = cols[:2]
            if not material_no:
                continue
            rows.append({"material_no": material_no, "material_name": material_name})
            rows_items.append(items_per_row[idx] if idx < len(items_per_row) else [])

        item_count_total = 0
        if rows:
            db.upsert_materials_bulk(rows)
            for r, raw_items in zip(rows, rows_items):
                parsed_items = []
                for order, raw_it in enumerate(raw_items, start=1):
                    if not isinstance(raw_it, dict):
                        continue
                    spec_display = (raw_it.get("spec_display") or "").strip()
                    if not spec_display:
                        continue
                    item_name = (raw_it.get("item_name") or "").strip() or f"항목{order}"
                    judge_type = raw_it.get("judge_type") or "numeric"
                    inspect_method = (raw_it.get("inspect_method") or "").strip()

                    if judge_type in ("visual", "ok_ng"):
                        lower_limit = upper_limit = None
                    else:
                        try:
                            lower_limit = float(raw_it.get("lower_limit")) if raw_it.get("lower_limit") not in (None, "") else None
                        except (TypeError, ValueError):
                            lower_limit = None
                        try:
                            upper_limit = float(raw_it.get("upper_limit")) if raw_it.get("upper_limit") not in (None, "") else None
                        except (TypeError, ValueError):
                            upper_limit = None

                    aql_select = raw_it.get("aql_select") or "4"
                    if aql_select == "전수":
                        aql = "전수"
                    elif aql_select == "퍼센트":
                        try:
                            percent_num = float(raw_it.get("aql_percent") or 0)
                            aql = f"퍼센트{percent_num:g}"
                        except ValueError:
                            aql = "퍼센트0"
                    else:
                        try:
                            aql_f = float(aql_select)
                            aql = int(aql_f) if aql_f == int(aql_f) else aql_f
                        except ValueError:
                            aql = aql_select

                    try:
                        item_order = int(raw_it.get("item_order") or order)
                    except (TypeError, ValueError):
                        item_order = order

                    parsed_items.append({
                        "item_name": item_name, "spec_display": spec_display, "judge_type": judge_type,
                        "lower_limit": lower_limit, "upper_limit": upper_limit,
                        "inspect_method": inspect_method, "aql": aql, "item_order": item_order,
                    })

                if parsed_items:
                    db.replace_specs_for_material(r["material_no"], r["material_name"], parsed_items)
                    item_count_total += len(parsed_items)

            flash(f"{len(rows)}개 자재 등록 완료" +
                  (f" (측정조건 총 {item_count_total}개 항목 함께 등록됨)" if item_count_total else
                   " — 이제 각 자재의 규격 상세 화면에서 항목을 추가해줘."))
            material_list = ", ".join(r["material_no"] for r in rows[:10])
            if len(rows) > 10:
                material_list += " 외"
            record_change("규격 개별 등록", "spec", None,
                          f"{len(rows)}건 ({material_list}), 항목 {item_count_total}개")
        else:
            flash("등록할 내용이 없어.")
        return redirect(url_for("spec_quick_add"))

    return render_template("spec_quick_add.html")


@app.route("/spec/review")
@perm_required("spec")
def spec_review():
    flags = db.list_unresolved_review_flags()
    return render_template("spec_review.html", flags=flags)


@app.route("/spec/review/<int:flag_id>/resolve", methods=["POST"])
@perm_required("spec")
def spec_review_resolve(flag_id):
    db.resolve_review_flag(flag_id)
    flash("해결됨으로 처리했어.")
    record_change("확인필요 자재 해결", "spec_review", flag_id)
    return redirect(url_for("spec_review"))


@app.route("/spec/delete_bulk", methods=["POST"])
@perm_required("spec")
def spec_delete_bulk():
    material_nos = request.form.getlist("material_nos")
    if not material_nos:
        flash("삭제할 자재를 선택해줘.")
        return redirect(url_for("spec_list"))
    db.delete_materials_bulk(material_nos)
    flash(f"{len(material_nos)}개 자재의 규격을 삭제했어.")
    record_change("규격 자재 일괄 삭제", "spec", None, ", ".join(material_nos[:10]))
    return redirect(url_for("spec_list"))


@app.route("/spec/<material_no>")
@perm_required("spec")
def spec_detail(material_no):
    specs = db.get_specs_by_material(material_no)
    material = db.get_material(material_no)
    material_name = specs[0]["material_name"] if specs else None
    if material_name is None:
        material_name = material["material_name"] if material else None
    drawing_no = report_builder.compute_drawing_no(material_no)
    return render_template("spec_detail.html", material_no=material_no, specs=specs,
                           material_name=material_name, material=material, drawing_no=drawing_no)


@app.route("/spec/<material_no>/standard_info", methods=["POST"])
@perm_required("spec")
def spec_standard_info_update(material_no):
    drawing_version = request.form.get("drawing_version", "1").strip() or "1"
    revision_date = request.form.get("revision_date", "").strip()
    edition_raw = request.form.get("edition", "1").strip()
    unit = request.form.get("unit", "mm").strip() or "mm"
    try:
        edition = int(edition_raw)
    except ValueError:
        edition = 1

    db.update_material_standard_info(material_no, drawing_version, revision_date, edition, unit)
    flash("기준서 정보가 저장됐어.")
    record_change("기준서 정보 수정", "material", material_no,
                  f"버전{drawing_version}/{revision_date}/제{edition}판/{unit}")
    return redirect(url_for("spec_detail", material_no=material_no))


@app.route("/spec/<material_no>/rename", methods=["POST"])
@perm_required("spec")
def spec_material_rename(material_no):
    new_material_no = request.form.get("new_material_no", "").strip()
    new_material_name = request.form.get("new_material_name", "").strip()
    if not new_material_no:
        flash("자재번호는 비워둘 수 없어.")
        return redirect(url_for("spec_detail", material_no=material_no))

    ok, error = db.rename_material(material_no, new_material_no, new_material_name)
    if not ok:
        flash(error)
        return redirect(url_for("spec_detail", material_no=material_no))

    flash(f"자재번호가 '{new_material_no}'(으)로 변경됐어. (과거 성적서는 옛 번호로 그대로 남아있어)")
    record_change("자재번호/자재명 변경", "material", new_material_no,
                  f"{material_no} → {new_material_no} ({new_material_name})")
    return redirect(url_for("spec_detail", material_no=new_material_no))


def _parse_spec_form(form):
    """규격 수정/추가 폼에서 값을 뽑아 db 저장용 튜플로 정리."""
    item_name = form.get("item_name", "").strip()
    spec_display = form.get("spec_display", "").strip()
    judge_type = form.get("judge_type", "numeric")
    inspect_method = form.get("inspect_method", "").strip()
    aql_raw = form.get("aql", "").strip()
    order_raw = form.get("item_order", "").strip()

    lower_raw = form.get("lower_limit", "").strip()
    upper_raw = form.get("upper_limit", "").strip()
    if judge_type in ("visual", "ok_ng"):
        lower_limit = upper_limit = None
    else:
        try:
            lower_limit = float(lower_raw) if lower_raw else None
        except ValueError:
            lower_limit = None
        try:
            upper_limit = float(upper_raw) if upper_raw else None
        except ValueError:
            upper_limit = None

    aql_select = form.get("aql_select", "").strip()
    percent_raw = form.get("aql_percent", "").strip()
    if aql_select == "전수":
        aql = "전수"
    elif aql_select == "퍼센트":
        try:
            percent_num = float(percent_raw)
            aql = f"퍼센트{percent_num:g}"
        except ValueError:
            aql = "퍼센트0"
    elif aql_select:
        try:
            aql = float(aql_select)
            if aql == int(aql):
                aql = int(aql)
        except ValueError:
            aql = aql_select
    else:
        # 구버전 폼 호환(aql_select가 없는 경우) — 기존처럼 aql 필드 그대로 파싱
        try:
            aql = float(aql_raw) if aql_raw else None
            if aql is not None and aql == int(aql):
                aql = int(aql)
        except ValueError:
            aql = aql_raw or None

    try:
        item_order = int(order_raw) if order_raw else 0
    except ValueError:
        item_order = 0

    return item_name, spec_display, judge_type, lower_limit, upper_limit, inspect_method, aql, item_order


@app.route("/spec/<material_no>/item/<int:spec_id>/update", methods=["POST"])
@perm_required("spec")
def spec_item_update(material_no, spec_id):
    item_name, spec_display, judge_type, lower_limit, upper_limit, inspect_method, aql, item_order = \
        _parse_spec_form(request.form)
    db.update_spec_item(spec_id, item_name, spec_display, judge_type,
                        lower_limit, upper_limit, inspect_method, aql, item_order)
    flash(f"항목 {item_name or spec_id} 수정됐어.")
    record_change("규격 항목 수정", "spec_item", spec_id, f"{material_no} / 항목 {item_name}: {spec_display}")
    return redirect(url_for("spec_detail", material_no=material_no))


@app.route("/spec/<material_no>/item/<int:spec_id>/delete", methods=["POST"])
@perm_required("spec")
def spec_item_delete(material_no, spec_id):
    item = db.get_spec_item(spec_id)
    db.delete_spec_item(spec_id)
    flash("항목 삭제됐어.")
    detail = f"{material_no} / 항목 {item['item_name']}: {item['spec_display']}" if item else material_no
    record_change("규격 항목 삭제", "spec_item", spec_id, detail)
    return redirect(url_for("spec_detail", material_no=material_no))


@app.route("/spec/<material_no>/items/delete_bulk", methods=["POST"])
@perm_required("spec")
def spec_item_delete_bulk(material_no):
    spec_ids = [int(v) for v in request.form.getlist("spec_ids")]
    if not spec_ids:
        flash("삭제할 항목을 선택해줘.")
        return redirect(url_for("spec_detail", material_no=material_no))
    db.delete_spec_items_bulk(spec_ids)
    flash(f"{len(spec_ids)}개 항목 삭제됐어.")
    record_change("규격 항목 일괄 삭제", "spec_item", None, f"{material_no} / {len(spec_ids)}개")
    return redirect(url_for("spec_detail", material_no=material_no))


@app.route("/spec/<material_no>/item/add", methods=["POST"])
@perm_required("spec")
def spec_item_add(material_no):
    item_name, spec_display, judge_type, lower_limit, upper_limit, inspect_method, aql, item_order = \
        _parse_spec_form(request.form)
    if not spec_display:
        flash("규격 표기를 입력해줘.")
        return redirect(url_for("spec_detail", material_no=material_no))

    existing = db.get_specs_by_material(material_no)
    material_name = existing[0]["material_name"] if existing else ""
    if not item_order:
        item_order = (max((s["item_order"] or 0) for s in existing) + 1) if existing else 1

    db.add_spec(material_no, material_name, item_name or f"항목{item_order}", spec_display,
                judge_type, lower_limit, upper_limit, inspect_method, aql, item_order)
    flash("새 항목이 추가됐어.")
    record_change("규격 항목 추가", "spec_item", None, f"{material_no} / 항목 {item_name or item_order}: {spec_display}")
    return redirect(url_for("spec_detail", material_no=material_no))


# ---------- 자재 그룹(조립품) ----------

@app.route("/groups")
@perm_required("spec")
def material_groups():
    groups = db.list_material_groups()
    groups_with_counts = []
    for g_row in groups:
        members = db.get_group_members(g_row["group_no"])
        groups_with_counts.append({**dict(g_row), "member_count": len(members)})
    return render_template("groups.html", groups=groups_with_counts)


@app.route("/groups/create", methods=["POST"])
@perm_required("spec")
def material_group_create():
    group_no = request.form.get("group_no", "").strip()
    group_name = request.form.get("group_name", "").strip()
    if not group_no:
        flash("조립품 자재번호를 입력해줘.")
        return redirect(url_for("material_groups"))
    if group_no in {m["material_no"] for m in db.get_materials()}:
        flash("이미 일반 자재로 규격이 등록된 번호야 — 그룹 번호는 겹치면 안 돼.")
        return redirect(url_for("material_groups"))
    db.create_material_group(group_no, group_name)
    flash(f"조립품 그룹 '{group_no}' 만들어졌어. 이제 부품을 추가해줘.")
    record_change("자재 그룹 생성", "material_group", group_no, group_name)
    return redirect(url_for("material_group_detail", group_no=group_no))


@app.route("/groups/<group_no>")
@perm_required("spec")
def material_group_detail(group_no):
    group = db.get_material_group(group_no)
    if group is None:
        flash("존재하지 않는 그룹이야.")
        return redirect(url_for("material_groups"))
    members = db.get_group_members(group_no)
    available_materials = [m for m in db.get_materials()
                           if m["material_no"] not in {mem["material_no"] for mem in members}]
    return render_template("group_detail.html", group=group, members=members,
                           available_materials=available_materials)


@app.route("/groups/<group_no>/delete", methods=["POST"])
@perm_required("spec")
def material_group_delete(group_no):
    db.delete_material_group(group_no)
    flash("그룹이 삭제됐어. (부품 개별 규격은 그대로 남아있어)")
    record_change("자재 그룹 삭제", "material_group", group_no)
    return redirect(url_for("material_groups"))


@app.route("/groups/<group_no>/members/add", methods=["POST"])
@perm_required("spec")
def material_group_add_member(group_no):
    material_no = request.form.get("material_no", "").strip()
    if not material_no:
        flash("추가할 부품 자재번호를 선택해줘.")
        return redirect(url_for("material_group_detail", group_no=group_no))
    existing = db.get_group_members(group_no)
    next_order = (max((m["item_order"] or 0) for m in existing) + 1) if existing else 1
    db.add_group_member(group_no, material_no, next_order)
    flash(f"부품 '{material_no}' 추가됐어.")
    record_change("자재 그룹 부품 추가", "material_group", group_no, material_no)
    return redirect(url_for("material_group_detail", group_no=group_no))


@app.route("/groups/<group_no>/members/<int:member_id>/remove", methods=["POST"])
@perm_required("spec")
def material_group_remove_member(group_no, member_id):
    db.remove_group_member(member_id)
    flash("부품이 그룹에서 제외됐어.")
    record_change("자재 그룹 부품 제외", "material_group", group_no, str(member_id))
    return redirect(url_for("material_group_detail", group_no=group_no))


@app.route("/spec/import", methods=["GET", "POST"])
@perm_required("spec")
def spec_import():
    """빈 성적서 양식(자재별 규격만 채워진 파일) 여러 개를 한 번에 업로드해서 규격표에 등록."""
    if request.method == "POST":
        files = request.files.getlist("spec_files")
        files = [f for f in files if f and f.filename]
        if not files:
            flash("업로드할 파일을 선택해줘.")
            return redirect(url_for("spec_import"))

        is_group = request.form.get("is_group") == "1"
        group_no = request.form.get("group_no", "").strip()
        group_name = request.form.get("group_name", "").strip()
        if is_group and not group_no:
            flash("그룹으로 등록하려면 그룹번호를 입력해줘.")
            return redirect(url_for("spec_import"))

        results = []   # 등록 성공: [{"material_no","material_name","item_count","filename"}]
        failures = []  # 등록 실패: [{"filename","reason"}]
        item_warnings = []  # 등록은 됐지만 항목별로 확인이 필요한 것들

        for f in files:
            if not f.filename.lower().endswith((".xlsx", ".xlsm")):
                failures.append({"filename": f.filename, "reason": ".xlsx/.xlsm 파일이 아니야."})
                db.add_review_flag(None, f.filename, ".xlsx/.xlsm 파일이 아니야.")
                continue
            try:
                sheet_results = spec_import_module.parse_spec_file(
                    io.BytesIO(f.read()), source_name=f.filename
                )
            except Exception as e:
                failures.append({"filename": f.filename, "reason": f"파일을 읽는 중 오류: {e}"})
                db.add_review_flag(None, f.filename, f"파일을 읽는 중 오류: {e}")
                continue

            for material_no, material_name, items, warnings, fail_reason in sheet_results:
                if fail_reason:
                    failures.append({"filename": f.filename, "reason": fail_reason})
                    db.add_review_flag(material_no or None, f.filename, fail_reason)
                    continue

                item_warnings.extend(warnings)
                for w in warnings:
                    db.add_review_flag(material_no, f.filename, w)
                db.replace_specs_for_material(material_no, material_name, items)
                results.append({
                    "material_no": material_no,
                    "material_name": material_name,
                    "item_count": len(items),
                    "filename": f.filename,
                })

        if results:
            material_list = ", ".join(r["material_no"] for r in results[:10])
            if len(results) > 10:
                material_list += " 외"
            record_change("규격 일괄 등록", "spec", None, f"{len(results)}개 자재 ({material_list})")

            if is_group:
                db.create_material_group(group_no, group_name)
                for order, r in enumerate(results, start=1):
                    db.add_group_member(group_no, r["material_no"], order)
                flash(f"'{group_no}' 그룹으로 {len(results)}개 부품이 묶여서 등록됐어.")
                record_change("자재 그룹 생성(일괄등록 연동)", "material_group", group_no,
                              f"{len(results)}개 부품")

        return render_template("spec_import_result.html",
                               results=results, failures=failures, warnings=item_warnings)

    return render_template("spec_import.html")


# ---------- 검사 입력 ----------

@app.route("/inspect/new")
@perm_required("inspect")
def inspect_select():
    """입고 리스트 중 미검사(대기) 건만 표로 보여줌"""
    query = request.args.get("q", "").strip()
    pending = db.search_intake(query, status="대기") if query else db.list_intake(status="대기")
    registered = {m["material_no"] for m in db.get_materials()}
    group_nos = {g["group_no"] for g in db.list_material_groups()}
    return render_template("inspect_select.html", pending=pending, query=query,
                           registered=registered, group_nos=group_nos)


@app.route("/inspect/delete_bulk", methods=["POST"])
@perm_required("inspect", "intake")
def inspect_delete_bulk():
    """검사 대기 목록에서 선택한 건들을 삭제(아직 검사 안 한 건만)."""
    intake_ids = [int(v) for v in request.form.getlist("intake_ids")]
    if not intake_ids:
        flash("삭제할 항목을 선택해줘.")
        return redirect(url_for("inspect_select"))
    deleted = db.delete_intake_bulk(intake_ids)
    flash(f"{deleted}건 삭제됐어." if deleted else "삭제된 항목이 없어(이미 검사된 건은 지울 수 없어).")
    record_change("검사 대기 목록 삭제", "intake", None, f"{deleted}건")
    return redirect(url_for("inspect_select"))


def _get_specs_for_material(material_no):
    """
    material_no가 조립품 그룹번호면 그룹에 속한 부품들의 규격을 등록 순서대로 이어붙여서 반환,
    일반 자재면 그 자재 규격 그대로 반환.
    반환: (specs_list, is_group, group_name)
    """
    group = db.get_material_group(material_no)
    if group is not None:
        members = db.get_group_members(material_no)
        specs = []
        for m in members:
            specs.extend(db.get_specs_by_material(m["material_no"]))
        return specs, True, group["group_name"]
    return db.get_specs_by_material(material_no), False, None


@app.route("/inspect/start/<int:intake_id>", methods=["GET", "POST"])
@perm_required("inspect")
def inspect_form(intake_id):
    intake_row = db.get_intake(intake_id)
    if intake_row is None:
        flash("존재하지 않는 입고 건이야.")
        return redirect(url_for("inspect_select"))

    material_no = intake_row["material_no"]
    specs, is_group, group_name = _get_specs_for_material(material_no)
    if not specs:
        if is_group:
            flash(f"조립품 그룹 '{material_no}'에 등록된 부품이 없거나, 부품 규격이 비어있어. 먼저 그룹 구성원/규격을 등록해줘.")
            return redirect(url_for("material_groups"))
        flash(f"{material_no}에 등록된 규격이 없어. 먼저 규격을 등록해줘.")
        return redirect(url_for("spec_list"))

    # 항목별 샘플수량 미리 계산 (입고수량 기준) — 조립품이면 부품마다 자기 AQL로 각자 계산
    specs_with_sample = []
    for s in specs:
        specs_with_sample.append({
            **dict(s),
            "sample_qty": sample_size(s["aql"], intake_row["quantity"]),
        })

    if request.method == "POST":
        header = {
            "material_no": material_no,
            "material_name": group_name if is_group else specs[0]["material_name"],
            "supplier": intake_row["supplier"],
            "po_number": intake_row["po_number"],
            "receive_date": intake_row["receive_date"],
            "inspect_date": request.form.get("inspect_date"),
            "inspector": g.user["display_name"] or g.user["username"],
            "quantity": intake_row["quantity"],
        }

        items_with_results = []
        overall_ok = True

        for spec in specs:
            if spec["judge_type"] == "numeric":
                # 측정값 1~6 개별 입력 수집
                vals = []
                for i in range(1, 7):
                    v = request.form.get(f"item_{spec['id']}_{i}", "").strip()
                    if v:
                        vals.append(v)
                raw_value = ",".join(vals)
                # JS에서 이미 계산한 결과를 hidden으로 받음 (서버에서도 재검증)
                result, max_v, min_v = judge_numeric(raw_value, spec["lower_limit"], spec["upper_limit"])
            else:
                vals = [request.form.get(f"item_{spec['id']}_{i}", "").strip() for i in range(1, 7)]
                raw_value = ",".join(v for v in vals if v)
                result, max_v, min_v = judge_visual(raw_value)

            if result != "합격":
                overall_ok = False

            gauge_expiry = request.form.get(f"item_{spec['id']}_gauge_expiry", "").strip() or None

            items_with_results.append({
                "item_name": spec["item_name"],
                "measured_value": raw_value,
                "max_value": max_v,
                "min_value": min_v,
                "result": result,
                "gauge_expiry": gauge_expiry,
                "part_material_no": spec["material_no"],
            })

        overall_result = "합격" if overall_ok else "검토필요"
        actual_time_sec = request.form.get("actual_time_sec", "").strip()
        actual_time_sec = int(actual_time_sec) if actual_time_sec.isdigit() else 0
        est_time_label = format_duration(actual_time_sec)
        inspection_id = db.create_inspection(header, items_with_results, overall_result,
                                              intake_id=intake_id, est_time_label=est_time_label,
                                              actual_time_sec=actual_time_sec,
                                              created_by_user_id=g.user["id"])
        record_change("성적서 등록", "inspection", inspection_id,
                      f"자재 {material_no}, 업체 {intake_row['supplier']}, 판정 {overall_result}")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    return render_template("inspect_form.html", intake=intake_row, specs=specs_with_sample,
                           is_group=is_group, group_name=group_name)


def judge_numeric(raw_value, lower, upper):
    """
    raw_value: 콤마로 구분된 여러 측정값 가능 (예: "12.1,12.3,12.0")
    lower/upper 둘 다 있으면 범위 판정, 하나만 있으면("OO 이상"/"OO 이하") 그쪽만 판정,
    둘 다 없으면(규격 미확정) 안전하게 불합격 처리.
    반환: (판정, 최대값, 최소값)
    """
    if not raw_value:
        return "미측정", None, None
    try:
        values = [float(v.strip()) for v in raw_value.split(",") if v.strip() != ""]
    except ValueError:
        return "입력오류", None, None

    if not values:
        return "미측정", None, None

    max_v, min_v = max(values), min(values)

    def _within(v):
        if lower is None and upper is None:
            return False
        if lower is not None and v < lower:
            return False
        if upper is not None and v > upper:
            return False
        return True

    ok = all(_within(v) for v in values)
    return ("합격" if ok else "불합격"), max_v, min_v


def judge_visual(raw_value):
    """육안 항목: O(합격)/X(불합격) 여러 칸 입력, 콤마로 구분. 하나라도 X면 불합격."""
    if not raw_value:
        return "미측정", None, None
    vals = [v.strip().upper() for v in raw_value.split(",") if v.strip()]
    if not vals:
        return "미측정", None, None
    ok = all(v == "O" for v in vals)
    return ("합격" if ok else "불합격"), None, None


# ---------- 성적서 상세 ----------

@app.route("/inspection/<int:inspection_id>")
@login_required
def inspection_detail(inspection_id):
    header, items = db.get_inspection(inspection_id)
    if header is None:
        flash("존재하지 않는 성적서야.")
        return redirect(url_for("home"))

    # 개당 측정시간 = 스톱워치로 잰 "샘플 1개(1사이클)" 시간 그대로
    per_cycle_sec = header["actual_time_sec"] or 0
    per_cycle_label = format_duration(per_cycle_sec)

    total_time_label = "-"
    specs, _, _ = _get_specs_for_material(header["material_no"])
    if specs and per_cycle_sec:
        specs_with_sample = [
            {**dict(s), "sample_qty": sample_size(s["aql"], header["quantity"])}
            for s in specs
        ]
        total_time_label = format_duration(compute_total_time_sec(specs_with_sample, per_cycle_sec))

    # 계측기 유효기간이 한 달(30일) 이내로 남았거나 이미 지난 항목들을 항목별로 나열
    gauge_alerts = []
    today = _dt.now().date()
    for it in items:
        expiry_str = it["gauge_expiry"]
        if not expiry_str:
            continue
        try:
            expiry_date = _dt.strptime(expiry_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_left = (expiry_date - today).days
        if days_left <= 30:
            gauge_alerts.append({
                "item_name": it["item_name"],
                "spec_display": it["spec_display"],
                "days_left": days_left,
            })

    return render_template("inspection_detail.html", header=header, items=items,
                           total_time_label=total_time_label, per_cycle_label=per_cycle_label,
                           gauge_alerts=gauge_alerts)


@app.route("/inspection/<int:inspection_id>/remark", methods=["POST"])
@perm_required("inspect", "inspect_all", "approve")
def inspection_remark(inspection_id):
    """비고란 저장 — 화면에서 어느 칸(검사자/중간관리자/최종결정권자)인지 넘겨받고, 해당 권한이 있는지 확인."""
    header, _ = db.get_inspection(inspection_id)
    if header is None:
        flash("존재하지 않는 성적서야.")
        return redirect(url_for("home"))

    # 검사자란=inspect 권한, 중간관리자란=inspect_all 권한, 최종결정권자란=approve 권한 보유자만 그 칸에 쓸 수 있음
    REMARK_PERM_MAP = {"inspector": "inspect", "manager": "inspect_all", "approver": "approve"}
    role_key = request.form.get("target_role", "")
    needed_perm = REMARK_PERM_MAP.get(role_key)
    user_perms = _user_perms(g.user)
    if role_key not in db.REMARK_FIELDS or needed_perm not in user_perms:
        flash("이 비고란에 쓸 권한이 없어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    text = request.form.get("remark_text", "").strip()
    name = g.user["display_name"] or g.user["username"]
    combined = f"{name}: {text}" if text else ""
    db.update_inspection_remark(inspection_id, role_key, combined)
    flash("비고가 저장됐어.")
    record_change("비고 작성", "inspection", inspection_id, f"{role_key} 비고: {text[:50]}")
    return redirect(url_for("inspection_detail", inspection_id=inspection_id))


# ---------- 측정값 수정 (pending 상태만) ----------

def _can_edit_inspection(header):
    """inspect_all 권한이 있으면 전체 수정 가능, 없으면(inspect만) 본인이 만든 성적서만."""
    user_perms = _user_perms(g.user)
    if "inspect_all" in user_perms:
        return True
    if "inspect" in user_perms:
        return header["created_by_user_id"] == g.user["id"]
    return False


@app.route("/inspection/<int:inspection_id>/edit")
@perm_required("inspect")
def edit_inspection(inspection_id):
    header, items = db.get_inspection(inspection_id)
    if header is None or header["status"] != "pending":
        flash("수정할 수 없는 상태야.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))
    if not _can_edit_inspection(header):
        flash("본인이 입력한 성적서만 수정할 수 있어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    intake_row = db.get_intake(header["intake_id"]) if header["intake_id"] else None
    material_no = header["material_no"]
    specs, is_group, group_name = _get_specs_for_material(material_no)
    specs_with_sample = [
        {**dict(s), "sample_qty": sample_size(s["aql"], header["quantity"])}
        for s in specs
    ]

    # 기존 측정값 prefill — 그룹 검사는 item_name만으로는 부품 간 중복될 수 있어 spec.id(전역 고유)로 맞춤
    prefill = {
        "inspect_date": header["inspect_date"] or "",
        "inspector": header["inspector"] or "",
    }
    items_by_key = {(it["part_material_no"], it["item_name"]): it for it in items}
    for s in specs:
        it = items_by_key.get((s["material_no"], s["item_name"]))
        if it is None:
            continue
        vals = (it["measured_value"] or "").split(",")
        for i, v in enumerate(vals, start=1):
            prefill[f"item_{s['id']}_{i}"] = v.strip()
        prefill[f"item_{s['id']}_gauge_expiry"] = it["gauge_expiry"] or ""

    # intake_row가 없으면 header 정보로 임시 구성
    if intake_row is None:
        class FakeIntake:
            pass
        fake = FakeIntake()
        fake.__class__.__getitem__ = lambda s, k: getattr(s, k, None)
        intake_row = {
            "id": header["intake_id"],
            "material_no": header["material_no"],
            "supplier": header["supplier"],
            "po_number": header["po_number"],
            "receive_date": header["receive_date"],
            "quantity": header["quantity"],
        }

    return render_template("inspect_form.html",
                           intake=intake_row, specs=specs_with_sample,
                           prefill=prefill, edit_mode=inspection_id,
                           is_group=is_group, group_name=group_name)


@app.route("/inspection/<int:inspection_id>/edit", methods=["POST"])
@perm_required("inspect")
def edit_inspection_submit(inspection_id):
    header, _ = db.get_inspection(inspection_id)
    if header is None or header["status"] != "pending":
        flash("수정할 수 없는 상태야.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))
    if not _can_edit_inspection(header):
        flash("본인이 입력한 성적서만 수정할 수 있어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    specs, _, _ = _get_specs_for_material(header["material_no"])
    items_with_results = []
    overall_ok = True

    for spec in specs:
        if spec["judge_type"] == "numeric":
            vals = [request.form.get(f"item_{spec['id']}_{i}", "").strip() for i in range(1, 7)]
            raw_value = ",".join(v for v in vals if v)
            result, max_v, min_v = judge_numeric(raw_value, spec["lower_limit"], spec["upper_limit"])
        else:
            vals = [request.form.get(f"item_{spec['id']}_{i}", "").strip() for i in range(1, 7)]
            raw_value = ",".join(v for v in vals if v)
            ok = bool(raw_value) and all(v == "O" for v in raw_value.split(","))
            result = "합격" if ok else ("불합격" if raw_value else "미측정")
            max_v = min_v = None

        if result != "합격":
            overall_ok = False
        gauge_expiry = request.form.get(f"item_{spec['id']}_gauge_expiry", "").strip() or None
        items_with_results.append({
            "item_name": spec["item_name"],
            "measured_value": raw_value,
            "max_value": max_v,
            "min_value": min_v,
            "result": result,
            "gauge_expiry": gauge_expiry,
            "part_material_no": spec["material_no"],
        })

    overall_result = "합격" if overall_ok else "검토필요"
    actual_time_sec = request.form.get("actual_time_sec", "").strip()
    actual_time_sec = int(actual_time_sec) if actual_time_sec.isdigit() else None
    est_time_label = format_duration(actual_time_sec) if actual_time_sec is not None else None
    db.update_inspection_items(
        inspection_id,
        request.form.get("inspect_date"),
        g.user["display_name"] or g.user["username"],
        items_with_results,
        overall_result,
        est_time_label=est_time_label,
        actual_time_sec=actual_time_sec,
    )
    flash("측정값이 수정됐어.")
    record_change("성적서 수정", "inspection", inspection_id,
                  f"자재 {header['material_no']}, 판정 {overall_result}")
    return redirect(url_for("inspection_detail", inspection_id=inspection_id))


# ---------- 승인 / 반려 ----------

def _save_signature(inspection_id, signature_data_url):
    """캔버스 서명(base64 dataURL)을 PNG 파일로 저장. 반환: (경로, 에러메시지)"""
    if not signature_data_url or "," not in signature_data_url:
        return None, "서명 데이터가 비어 있어."
    try:
        header_part, b64_part = signature_data_url.split(",", 1)
        png_bytes = base64.b64decode(b64_part)
    except Exception as e:
        return None, f"서명 데이터 디코딩 실패: {e}"

    try:
        os.makedirs(SIGNATURE_DIR, exist_ok=True)
        path = os.path.join(SIGNATURE_DIR, f"{inspection_id}.png")
        with open(path, "wb") as f:
            f.write(png_bytes)
    except Exception as e:
        return None, f"서명 파일 저장 실패({SIGNATURE_DIR}): {e}"

    return path, None


def _save_signature_upload(inspection_id, file_storage):
    """업로드된 서명 이미지 파일(PNG/JPG)을 저장. 반환: (경로, 에러메시지)"""
    if file_storage is None or not file_storage.filename:
        return None, "업로드할 서명 이미지 파일을 선택해줘."

    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        return None, "PNG 또는 JPG 이미지 파일만 업로드할 수 있어."

    try:
        os.makedirs(SIGNATURE_DIR, exist_ok=True)
        path = os.path.join(SIGNATURE_DIR, f"{inspection_id}{ext}")
        file_storage.save(path)
    except Exception as e:
        return None, f"서명 파일 저장 실패({SIGNATURE_DIR}): {e}"

    return path, None


def _generate_report_files(inspection_id, approver, signature_path):
    """DB에 저장된 성적서 데이터를 report_builder 입력 형식으로 변환해 xlsx/PDF 생성.
    조립품 그룹 검사면 부품별로 시트를 나눈 통합 워크북을 만든다.
    반환: (xlsx_path, pdf_path, error_message) — error_message는 성공 시 None"""
    header, items = db.get_inspection(inspection_id)
    if header is None:
        return None, None, "성적서 정보를 찾을 수 없어."

    rb_header = {
        "vendor": header["supplier"],
        "po_no": header["po_number"],
        "lot": header["receive_date"],
        "inspect_date": header["inspect_date"],
        "inspector": header["inspector"],
        "qty": header["quantity"],
    }

    group = db.get_material_group(header["material_no"])
    if group is not None:
        members = db.get_group_members(header["material_no"])
        combined_specs = []
        for m in members:
            combined_specs.extend(db.get_specs_by_material(m["material_no"]))
    else:
        combined_specs = db.get_specs_by_material(header["material_no"])

    # 개당 측정시간 = 스톱워치로 잰 "샘플 1개(1사이클)" 시간 그대로
    per_cycle_sec = header["actual_time_sec"] or 0
    per_cycle_label = format_duration(per_cycle_sec)

    # 총 측정시간 = 기준(AQL4) 샘플수×개당시간 + 다른 AQL 그룹의 초과분×10초
    specs_with_sample = [
        {**dict(s), "sample_qty": sample_size(s["aql"], header["quantity"])}
        for s in combined_specs
    ]
    total_time_label = format_duration(compute_total_time_sec(specs_with_sample, per_cycle_sec))

    def _build_result(it):
        raw = (it["measured_value"] or "")
        raw_tokens = [v.strip() for v in raw.split(",") if v.strip() != ""]
        values = []
        for v in raw_tokens:
            try:
                values.append(float(v))
            except ValueError:
                pass  # 숫자로 안 바뀌면(O/X 등) 아래에서 별도 처리
        # 육안/O,X 항목은 OK/NG 문자열로 변환해서 그대로 기입
        if not values and raw_tokens and all(t.upper() in ("O", "X") for t in raw_tokens):
            values = ["OK" if t.upper() == "O" else "NG" for t in raw_tokens]
        sample_qty = sample_size(it["aql"], header["quantity"]) if it["aql"] is not None else None
        return {
            "item_name": it["item_name"],
            "values": values,
            "max": it["max_value"],
            "min": it["min_value"],
            "verdict": it["result"] or "",
            "gauge_expiry": it["gauge_expiry"],
            "spec_display": it["spec_display"],
            "aql": it["aql"],
            "sample_qty": sample_qty,
            "inspect_method": it["inspect_method"],
            "lower_limit": it["lower_limit"],
            "upper_limit": it["upper_limit"],
        }

    remarks = {
        "inspector": header["remark_inspector"],
        "manager": header["remark_manager"],
        "approver": header["remark_approver"],
    }

    def _standard_info_for(material_no):
        m = db.get_material(material_no)
        if m is None:
            return None
        return {
            "drawing_version": m["drawing_version"],
            "revision_date": m["revision_date"],
            "edition": m["edition"],
            "unit": m["unit"],
        }

    try:
        if group is not None:
            # 부품별로 결과를 나눠서(등록 순서 유지) 시트별로 채울 부품 리스트 구성
            parts_map = {}
            for it in items:
                part_no = it["part_material_no"] or header["material_no"]
                if part_no not in parts_map:
                    parts_map[part_no] = {
                        "material_no": part_no,
                        "product_name": it["part_material_name"] or "",
                        "results": [],
                    }
                parts_map[part_no]["results"].append(_build_result(it))
            parts = list(parts_map.values())
            standard_info_map = {p["material_no"]: _standard_info_for(p["material_no"]) for p in parts}
            standard_info_map = {k: v for k, v in standard_info_map.items() if v is not None}

            xlsx_path, pdf_path, pdf_error, signature_error = report_builder.build_group_report(
                header["material_no"], header["material_name"] or (group["group_name"] or ""),
                rb_header, parts, header["overall_result"],
                approver=approver, signature_path=signature_path,
                per_cycle_label=per_cycle_label, total_time_label=total_time_label,
                remarks=remarks, approval_type=header["approval_type"],
                standard_info_map=standard_info_map,
            )
        else:
            results = [_build_result(it) for it in items]
            xlsx_path, pdf_path, pdf_error, signature_error = report_builder.build_report(
                header["material_no"], header["material_name"] or "",
                rb_header, results, header["overall_result"],
                approver=approver, signature_path=signature_path,
                per_cycle_label=per_cycle_label, total_time_label=total_time_label,
                remarks=remarks, approval_type=header["approval_type"],
                standard_info=_standard_info_for(header["material_no"]),
            )
    except Exception:
        import traceback
        return None, None, f"성적서 파일 생성 중 오류:\n{traceback.format_exc(limit=3)}"

    error_msg = None
    if signature_error and pdf_error:
        error_msg = f"{signature_error} / {pdf_error}"
    elif signature_error:
        error_msg = signature_error
    elif pdf_error:
        error_msg = pdf_error

    return xlsx_path, pdf_path, error_msg


@app.route("/inspection/<int:inspection_id>/approve", methods=["POST"])
@perm_required("approve")
def approve(inspection_id):
    action = request.form.get("action")          # 'approve' / 'special' / 'reject'
    approver = g.user["display_name"] or g.user["username"]
    reject_reason = request.form.get("reject_reason", "").strip()
    from_approve = request.form.get("from_approve") == "1"

    def _back(on_error=False):
        if from_approve and on_error:
            return redirect(url_for("approve_view", inspection_id=inspection_id))
        if from_approve:
            return redirect(url_for("approve_list"))
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    if not approver:
        flash("결정권자 이름을 입력해줘.")
        return _back(on_error=True)

    if action in ("approve", "special"):
        signature_method = request.form.get("signature_method", "draw")
        if signature_method == "upload":
            signature_file = request.files.get("signature_file")
            signature_path, sig_save_error = _save_signature_upload(inspection_id, signature_file)
        else:
            signature_data = request.form.get("signature_data", "").strip()
            if not signature_data:
                flash("서명을 입력해줘.")
                return _back(on_error=True)
            signature_path, sig_save_error = _save_signature(inspection_id, signature_data)

        if sig_save_error:
            flash(f"서명 저장 실패: {sig_save_error}")
            return _back(on_error=True)

        approval_type = "special" if action == "special" else "normal"
        db.update_inspection_status(inspection_id, "approved", approver=approver, approval_type=approval_type)
        # PDF는 여기서 바로 만들지 않음 — "출력 대기" 화면에서 선택/전체 출력할 때 생성됨
        db.set_report_files(inspection_id, signature_path=signature_path, pdf_path=None, xlsx_path=None)

        if action == "special":
            flash(f"특채 승인 완료 ({approver}) — 성적서 파일은 '출력 대기' 화면에서 선택 출력하면 돼.")
            record_change("성적서 특채 승인", "inspection", inspection_id, f"승인자 {approver}")
        else:
            flash(f"승인 완료 ({approver}) — 성적서 파일은 '출력 대기' 화면에서 선택 출력하면 돼.")
            record_change("성적서 승인", "inspection", inspection_id, f"승인자 {approver}")

    elif action == "reject":
        if not reject_reason:
            flash("반려 사유를 입력해줘.")
            return _back(on_error=True)
        db.update_inspection_status(inspection_id, "rejected",
                                    approver=approver, reject_reason=reject_reason)
        flash("반려 처리됐어. 검사자가 재입력할 수 있어.")
        record_change("성적서 반려", "inspection", inspection_id, f"반려자 {approver}, 사유: {reject_reason}")
    else:
        flash("알 수 없는 액션이야.")

    return _back()


# ---------- 승인 목록 / 승인 전용 화면 ----------

@app.route("/approve")
@perm_required("approve")
def approve_list():
    """승인 대기 중인 성적서 목록 (pending 상태만)."""
    pending = db.list_inspections(status="pending")
    return render_template("approve_list.html", pending=pending)


@app.route("/approve/<int:inspection_id>")
@perm_required("approve")
def approve_view(inspection_id):
    """항목·측정값·검사방법이 모두 보이는 승인 전용 화면."""
    header, items = db.get_inspection(inspection_id)
    if header is None:
        flash("존재하지 않는 성적서야.")
        return redirect(url_for("approve_list"))
    if header["status"] != "pending":
        flash("이미 처리된 성적서야.")
        return redirect(url_for("approve_list"))

    per_cycle_sec = header["actual_time_sec"] or 0
    per_cycle_label = format_duration(per_cycle_sec)

    today = _dt.now().date()
    gauge_alerts = []

    # 항목별 측정값 파싱 + 문제/합격 분류
    def _parse_item(it):
        raw = (it["measured_value"] or "").strip()
        tokens = [v.strip() for v in raw.split(",") if v.strip()]
        lo, hi = it["lower_limit"], it["upper_limit"]
        parsed_vals = []
        for t in tokens:
            try:
                v = float(t)
                out = False
                if lo is not None and v < lo:
                    out = True
                if hi is not None and v > hi:
                    out = True
                parsed_vals.append({"display": t, "out": out, "is_num": True})
            except ValueError:
                # O/X 육안 값
                parsed_vals.append({"display": t, "out": t.upper() == "X", "is_num": False})

        expiry_str = it["gauge_expiry"]
        days_left = None
        if expiry_str:
            try:
                expiry_date = _dt.strptime(expiry_str, "%Y-%m-%d").date()
                days_left = (expiry_date - today).days
                if days_left <= 30:
                    gauge_alerts.append({
                        "item_name": it["item_name"],
                        "spec_display": it["spec_display"],
                        "days_left": days_left,
                    })
            except ValueError:
                pass

        return dict(it) | {
            "parsed_vals": parsed_vals,
            "gauge_days_left": days_left,
        }

    all_items = [_parse_item(it) for it in items]
    problem_items = [it for it in all_items if it["result"] == "불합격"]
    pass_items    = [it for it in all_items if it["result"] != "불합격"]

    return render_template("approve_form.html",
                           header=header,
                           problem_items=problem_items,
                           pass_items=pass_items,
                           all_items=all_items,
                           per_cycle_label=per_cycle_label,
                           gauge_alerts=gauge_alerts)


# ---------- 검사 이력 ----------

@app.route("/history")
@login_required
def history():
    """전체 성적서를 입고일 기준으로 날짜별 그룹화해서 보여줌."""
    from collections import defaultdict, OrderedDict
    inspections = db.list_inspections()
    by_date = defaultdict(list)
    for insp in inspections:
        date_key = insp["receive_date"] or "날짜 없음"
        by_date[date_key].append(insp)
    sorted_dates = sorted(
        by_date.keys(),
        key=lambda d: d if d != "날짜 없음" else "0000",
        reverse=True,
    )
    return render_template("history.html", by_date=by_date, sorted_dates=sorted_dates)


# ---------- 성적서 출력 (승인된 것 중 선택/전체) ----------

@app.route("/output")
@perm_required("output")
def output_list():
    pending = db.list_pending_output_inspections()
    return render_template("output_list.html", pending=pending)


@app.route("/output/generate", methods=["POST"])
@perm_required("output")
def output_generate():
    if request.form.get("select_all") == "1":
        target_ids = [i["id"] for i in db.list_pending_output_inspections()]
    else:
        target_ids = [int(v) for v in request.form.getlist("inspection_ids")]

    if not target_ids:
        flash("출력할 성적서를 선택해줘.")
        return redirect(url_for("output_list"))

    results = []  # {"id","material_no","pdf_path","error"}
    for insp_id in target_ids:
        header, _ = db.get_inspection(insp_id)
        if header is None or header["status"] != "approved":
            results.append({"id": insp_id, "material_no": "-", "pdf_path": None,
                            "error": "승인된 성적서가 아니야(다시 확인해줘)."})
            continue

        xlsx_path, pdf_path, error_msg = _generate_report_files(
            insp_id, header["approver"], header["signature_path"]
        )
        db.set_report_files(insp_id, signature_path=header["signature_path"],
                            pdf_path=pdf_path, xlsx_path=xlsx_path)
        results.append({
            "id": insp_id, "material_no": header["material_no"],
            "pdf_path": pdf_path, "error": error_msg,
        })

    success_count = sum(1 for r in results if r["pdf_path"] and not r["error"])
    record_change("성적서 출력", "inspection", None,
                  f"{len(target_ids)}건 중 {success_count}건 성공")

    return render_template("output_result.html", results=results)


# ---------- 반려 후 재검사 입력 ----------

@app.route("/inspection/<int:inspection_id>/reinspect")
@perm_required("inspect")
def reinspect(inspection_id):
    """반려된 성적서를 기반으로 재검사 폼을 열어줌 — 이전 측정값 prefill"""
    header, items = db.get_inspection(inspection_id)
    if header is None or header["status"] != "rejected":
        flash("재검사 대상이 아니야.")
        return redirect(url_for("home"))
    if not _can_edit_inspection(header):
        flash("본인이 입력한 성적서만 재검사할 수 있어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    intake_row = db.get_intake(header["intake_id"]) if header["intake_id"] else None
    if intake_row is None:
        flash("입고 정보를 찾을 수 없어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    material_no = header["material_no"]
    specs, is_group, group_name = _get_specs_for_material(material_no)
    specs_with_sample = [
        {**dict(s), "sample_qty": sample_size(s["aql"], intake_row["quantity"])}
        for s in specs
    ]

    # 이전 측정값을 prefill dict에 담기 — 그룹 검사는 (부품자재, 항목명)으로 정확히 매칭
    prefill = {
        "inspect_date": header["inspect_date"] or "",
        "inspector": header["inspector"] or "",
        "reject_reason": header["reject_reason"] or "",
    }
    items_by_key = {(it["part_material_no"], it["item_name"]): it for it in items}
    for s in specs:
        it = items_by_key.get((s["material_no"], s["item_name"]))
        if it is None:
            continue
        vals = (it["measured_value"] or "").split(",")
        for i, v in enumerate(vals, start=1):
            prefill[f"item_{s['id']}_{i}"] = v.strip()
        prefill[f"item_{s['id']}_gauge_expiry"] = it["gauge_expiry"] or ""

    return render_template("inspect_form.html",
                           intake=intake_row, specs=specs_with_sample,
                           prefill=prefill, reinspect_from=inspection_id,
                           is_group=is_group, group_name=group_name)


@app.route("/inspection/<int:inspection_id>/reinspect", methods=["POST"])
@perm_required("inspect")
def reinspect_submit(inspection_id):
    """재검사 결과 저장 — 기존 반려 건은 superseded 처리"""
    old_header, _ = db.get_inspection(inspection_id)
    if old_header is None:
        flash("성적서를 찾을 수 없어.")
        return redirect(url_for("home"))
    if not _can_edit_inspection(old_header):
        flash("본인이 입력한 성적서만 재검사할 수 있어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    intake_id = old_header["intake_id"]
    intake_row = db.get_intake(intake_id) if intake_id else None
    if intake_row is None:
        flash("입고 정보를 찾을 수 없어.")
        return redirect(url_for("inspection_detail", inspection_id=inspection_id))

    material_no = old_header["material_no"]
    specs, is_group, group_name = _get_specs_for_material(material_no)

    header = {
        "material_no": material_no,
        "material_name": group_name if is_group else (specs[0]["material_name"] if specs else ""),
        "supplier": intake_row["supplier"],
        "po_number": intake_row["po_number"],
        "receive_date": intake_row["receive_date"],
        "inspect_date": request.form.get("inspect_date"),
        "inspector": g.user["display_name"] or g.user["username"],
        "quantity": intake_row["quantity"],
    }

    items_with_results = []
    overall_ok = True
    for spec in specs:
        if spec["judge_type"] == "numeric":
            vals = [request.form.get(f"item_{spec['id']}_{i}", "").strip()
                    for i in range(1, 7)]
            raw_value = ",".join(v for v in vals if v)
            result, max_v, min_v = judge_numeric(raw_value, spec["lower_limit"], spec["upper_limit"])
        else:
            vals = [request.form.get(f"item_{spec['id']}_{i}", "").strip()
                    for i in range(1, 7)]
            raw_value = ",".join(v for v in vals if v)
            ok = bool(raw_value) and all(v == "O" for v in raw_value.split(","))
            result = "합격" if ok else ("불합격" if raw_value else "미측정")
            max_v = min_v = None

        if result != "합격":
            overall_ok = False
        gauge_expiry = request.form.get(f"item_{spec['id']}_gauge_expiry", "").strip() or None
        items_with_results.append({
            "item_name": spec["item_name"],
            "measured_value": raw_value,
            "max_value": max_v,
            "min_value": min_v,
            "result": result,
            "gauge_expiry": gauge_expiry,
            "part_material_no": spec["material_no"],
        })

    overall_result = "합격" if overall_ok else "검토필요"
    actual_time_sec = request.form.get("actual_time_sec", "").strip()
    actual_time_sec = int(actual_time_sec) if actual_time_sec.isdigit() else 0
    est_time_label = format_duration(actual_time_sec)
    # 반려된 이전 건을 superseded로 표시하고 intake를 다시 대기로 돌림
    db.update_inspection_status(inspection_id, "superseded")
    db.set_intake_status(intake_id, "대기")
    new_id = db.create_inspection(header, items_with_results, overall_result,
                                   intake_id=intake_id, est_time_label=est_time_label,
                                   actual_time_sec=actual_time_sec,
                                   created_by_user_id=g.user["id"])
    flash("재검사 성적서가 생성됐어. 다시 승인을 요청해줘.")
    record_change("재검사 성적서 등록", "inspection", new_id,
                  f"자재 {material_no}, 이전 성적서 #{inspection_id}, 판정 {overall_result}")
    return redirect(url_for("inspection_detail", inspection_id=new_id))


if __name__ == "__main__":
    db.init_db()
    ensure_default_admin()
    app.run(host="0.0.0.0", port=5000, debug=True)
