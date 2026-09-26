"""대기중인 하우징 입고건들에 대해 검사(inspections row)를 '시작' 상태로 생성한다.

실제 측정값은 아무도 안 넣은 상태(모든 항목 result='미측정')로 성적서만 만들어서,
하우징 검사입력 목록(/housing/inspect)에 바로 뜨게 한다 — 실제 STAGE1/2/3 값 입력은
검사자가 화면에서 나중에 한다. 이 스크립트는 일반 입력화면에서 아무 값도 안 채우고
제출했을 때와 정확히 같은 결과(db.create_inspection 직접 호출)를 만든다.

대상: intake_list.status='대기'인 하우징 자재(602013P015/604013P000/608013P011) 건
전부. 이미 활성 성적서가 있는 건(active_inspection_for_intake)은 건너뛴다(멱등).

Render Shell에서: python start_housing_inspections.py
"""
import sys, io
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']


def main():
    conn = db.get_conn()
    admin_row = conn.execute("SELECT id, username, display_name FROM users WHERE username='admin'").fetchone()
    conn.close()
    if admin_row is None:
        print('admin 계정을 못 찾았어 - 중단')
        return
    admin_id = admin_row['id']
    admin_name = admin_row['display_name'] or admin_row['username']

    created = []
    skipped = []

    for mn in HOUSING:
        material = db.get_material(mn)
        material_name = material['material_name'] if material else ''
        specs = db.get_specs_by_material(mn)
        if not specs:
            print(f'[{mn}] 규격(specs)이 없어서 건너뜀')
            continue

        conn = db.get_conn()
        intakes = conn.execute(
            "SELECT * FROM intake_list WHERE material_no=? AND status='대기' ORDER BY id", (mn,)
        ).fetchall()
        conn.close()

        for intake_row in intakes:
            existing = db.active_inspection_for_intake(intake_row['id'])
            if existing is not None:
                skipped.append((mn, intake_row['id'], f"이미 활성 성적서 #{existing['id']}"))
                continue

            header = {
                "material_no": mn,
                "material_name": material_name,
                "supplier": intake_row["supplier"],
                "po_number": intake_row["po_number"],
                "receive_date": intake_row["receive_date"],
                "inspect_date": None,
                "inspector": admin_name,
                "quantity": intake_row["quantity"],
            }
            items_with_results = [{
                "item_name": sp["item_name"],
                "measured_value": "",
                "max_value": None,
                "min_value": None,
                "result": "미측정",
                "gauge_expiry": None,
                "gauge_name": None,
                "part_material_no": mn,
            } for sp in specs]

            try:
                inspection_id = db.create_inspection(
                    header, items_with_results, "검토필요",
                    intake_id=intake_row["id"], created_by_user_id=admin_id,
                )
            except ValueError as e:
                skipped.append((mn, intake_row['id'], str(e)))
                continue

            created.append((mn, intake_row['id'], inspection_id))
            print(f"[{mn}] intake#{intake_row['id']} -> inspection#{inspection_id} 생성 "
                  f"(수량={intake_row['quantity']}, 항목수={len(specs)}, 전부 미측정)")

    print(f"\n생성됨: {len(created)}건, 건너뜀: {len(skipped)}건")
    for mn, iid, reason in skipped:
        print(f"  건너뜀: [{mn}] intake#{iid} - {reason}")
    print('\nDONE.')


if __name__ == "__main__":
    main()
