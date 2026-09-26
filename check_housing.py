"""하우징 3종 자재 기존 입고/검사 기록 조회 (읽기전용, DB 변경 없음).

Render Shell에서: python check_housing.py
"""
import sys, io, sqlite3
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

conn = sqlite3.connect(db.DB_PATH)
conn.row_factory = sqlite3.Row

print('=== intake_list (입고 등록) ===')
for mn in HOUSING:
    rows = conn.execute(
        "SELECT id, status, receive_date, quantity, supplier, po_number "
        "FROM intake_list WHERE material_no=? ORDER BY id", (mn,)
    ).fetchall()
    print(f'\n[{mn}] {len(rows)}건')
    for r in rows:
        print(f"  id={r['id']} status={r['status']} 입고일={r['receive_date']} "
              f"수량={r['quantity']} 업체={r['supplier']} 발주={r['po_number']}")

print('\n=== inspections (검사 성적서) ===')
for mn in HOUSING:
    rows = conn.execute(
        "SELECT id, status, receive_date, quantity, supplier, intake_id "
        "FROM inspections WHERE material_no=? ORDER BY id", (mn,)
    ).fetchall()
    print(f'\n[{mn}] {len(rows)}건')
    for r in rows:
        fi = conn.execute(
            "SELECT status FROM full_inspections WHERE inspection_id=?", (r['id'],)
        ).fetchone()
        fi_status = fi['status'] if fi else '(전수검사 기록 없음)'
        print(f"  id={r['id']} status={r['status']} intake_id={r['intake_id']} "
              f"입고일={r['receive_date']} 수량={r['quantity']} 업체={r['supplier']} "
              f"전수검사상태={fi_status}")

conn.close()
print('\nDONE.')
