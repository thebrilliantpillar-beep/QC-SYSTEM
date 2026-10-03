"""605013P300(EPOXY HOUSING(CKMR3515))를 15KV 하우징(602013P015)과 동일한 설정으로 등록한다.

하는 일 (전부 멱등 — 재실행해도 안전, 이미 돼있으면 건너뜀):
  1. materials: 602013P015 행을 그대로 복사해서 605013P300으로 저장(material_no/material_name만
     바꿈) — full_inspect_config(전수검사=하우징 설정)·category·도면정보 등 전부 포함.
     605013P300이 이미 materials에 있으면 덮어쓰지 않고 건너뜀(수동으로 손댄 값 보호).
  2. specs: 602013P015의 검사항목을 605013P300으로 복사(item_order 등 전부 동일, id만 새로
     발급). 605013P300에 이미 규격이 있으면 건너뜀(중복 방지).
  3. intake_list에 605013P300으로 등록된 '대기' 건 중 아직 활성 성적서가 없는 것들을
     찾아서, 하우징 검사입력 목록(/housing/inspect)에 뜨도록 빈 성적서(항목 전부
     미측정)를 생성한다 — app.py의 _auto_start_full_inspections()와 동일한 방식
     (db.create_inspection 직접 호출, overall_result="").

Render Shell에서: python register_housing_605013P300.py
"""
import sys, io
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SRC = '602013P015'          # 15KV 하우징 기존 자재(복사 원본)
DST = '605013P300'          # 새로 등록할 자재
DST_NAME = 'EPOXY HOUSING(CKMR3515)'


def main():
    conn = db.get_conn()

    # ---- 1. materials 복사 ----
    src_mat = conn.execute("SELECT * FROM materials WHERE material_no=?", (SRC,)).fetchone()
    if src_mat is None:
        print(f'원본 자재 {SRC}가 materials에 없어 — 중단')
        conn.close()
        return
    dst_mat = conn.execute("SELECT * FROM materials WHERE material_no=?", (DST,)).fetchone()
    if dst_mat is not None:
        print(f'[materials] {DST} 이미 존재 — 건너뜀 (full_inspect_config={dst_mat["full_inspect_config"]!r})')
    else:
        # created_at은 원본 생성시각이 아니라 지금(= DEFAULT)이 맞다 — 신규 등록 자재가
        # 옛날 날짜로 보이면 입고/자재 목록 최신순 정렬이 어긋난다(quality-watcher 지적).
        cols = [c for c in src_mat.keys() if c != 'created_at']
        values = []
        for c in cols:
            if c == 'material_no':
                values.append(DST)
            elif c == 'material_name':
                values.append(DST_NAME)
            else:
                values.append(src_mat[c])
        placeholders = ','.join('?' * len(cols))
        conn.execute(f"INSERT INTO materials ({','.join(cols)}) VALUES ({placeholders})", values)
        conn.commit()
        print(f'[materials] {DST} 신규 등록 완료 ({SRC} 설정 그대로 복사)')

    # 전압분류 명시 설정 — 605013P300은 이름이 "EPOXY HOUSING..."이라 자재명 기반 자동판별
    # (NNKV 패턴)이 안 먹는다. /housing/voltage-groups 화면과 같은 저장 경로(db.set_housing_voltage)
    # 를 그대로 써서 한 번에 15KV로 지정해둔다 — 이후 화면에서 값을 바꾸면 그게 우선된다.
    db.set_housing_voltage(DST, '15KV')
    print(f'[voltage] {DST} 전압분류를 15KV로 지정 완료')

    # ---- 2. specs 복사 ----
    dst_specs_existing = conn.execute("SELECT COUNT(*) AS c FROM specs WHERE material_no=?", (DST,)).fetchone()
    if dst_specs_existing['c'] > 0:
        print(f'[specs] {DST} 에 이미 {dst_specs_existing["c"]}개 등록돼 있음 — 건너뜀')
    else:
        src_specs = conn.execute(
            "SELECT * FROM specs WHERE material_no=? ORDER BY item_order, id", (SRC,)
        ).fetchall()
        if not src_specs:
            print(f'[specs] 원본 {SRC}에 규격이 없음 — 복사할 게 없음')
        else:
            spec_cols = [c for c in src_specs[0].keys() if c != 'id']
            for sp in src_specs:
                values = [
                    DST if c == 'material_no' else
                    DST_NAME if c == 'material_name' else
                    sp[c]
                    for c in spec_cols
                ]
                placeholders = ','.join('?' * len(spec_cols))
                conn.execute(f"INSERT INTO specs ({','.join(spec_cols)}) VALUES ({placeholders})", values)
            conn.commit()
            print(f'[specs] {DST}에 {len(src_specs)}개 항목 복사 완료')

    conn.close()

    # ---- 3. 대기중인 605013P300 입고건 검사시작 ----
    material = db.get_material(DST)
    material_name = material['material_name'] if material else DST_NAME
    specs = db.get_specs_by_material(DST)
    if not specs:
        print(f'[inspections] {DST}에 규격이 없어서 검사시작 건너뜀')
        return

    conn = db.get_conn()
    admin_row = conn.execute(
        "SELECT id, username, display_name FROM users WHERE username='admin'"
    ).fetchone()
    intakes = conn.execute(
        "SELECT * FROM intake_list WHERE material_no=? AND status='대기' ORDER BY id", (DST,)
    ).fetchall()
    conn.close()

    if admin_row is None:
        print('admin 계정을 못 찾아서 검사시작 건너뜀')
        return
    admin_id = admin_row['id']
    admin_name = admin_row['display_name'] or admin_row['username']

    created, skipped = [], []
    for intake_row in intakes:
        existing = db.active_inspection_for_intake(intake_row['id'])
        if existing is not None:
            skipped.append((intake_row['id'], f"이미 활성 성적서 #{existing['id']}"))
            continue
        header = {
            "material_no": DST,
            "material_name": material_name,
            "supplier": intake_row["supplier"],
            "po_number": intake_row["po_number"],
            "receive_date": intake_row["receive_date"],
            "inspect_date": None,
            "inspector": admin_name,
            "quantity": intake_row["quantity"],
        }
        items_with_results = [{
            "item_name": sp["item_name"], "measured_value": "", "max_value": None, "min_value": None,
            "result": "미측정", "gauge_expiry": None, "gauge_name": None, "part_material_no": DST,
        } for sp in specs]
        try:
            inspection_id = db.create_inspection(
                header, items_with_results, "", intake_id=intake_row["id"], created_by_user_id=admin_id,
            )
        except ValueError as e:
            skipped.append((intake_row['id'], str(e)))
            continue
        created.append((intake_row['id'], inspection_id))
        print(f"[inspections] intake#{intake_row['id']} -> inspection#{inspection_id} 생성 "
              f"(수량={intake_row['quantity']}, 항목수={len(specs)}, 전부 미측정)")

    print(f"\n검사시작 생성됨: {len(created)}건, 건너뜀: {len(skipped)}건")
    for iid, reason in skipped:
        print(f"  건너뜀: intake#{iid} - {reason}")
    print('\nDONE. /housing/inspect 에서 확인해줘.')


if __name__ == "__main__":
    main()
