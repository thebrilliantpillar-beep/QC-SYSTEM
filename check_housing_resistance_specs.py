"""하우징 3종 자재의 specs 전체를 그대로 출력 — '고저항' 관련 항목의 실제 judge_type/
spec_display를 프로덕션에서 직접 확인하기 위한 읽기전용 진단 스크립트.
CLAUDE.md 26-3 원칙: 로컬DB와 프로덕션 데이터가 다를 수 있으니 추측하지 말고 실측할 것.
"""
import sys, io, sqlite3
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

conn = sqlite3.connect(db.DB_PATH)
conn.row_factory = sqlite3.Row

for mn in HOUSING:
    print(f'\n=== {mn} ===')
    rows = conn.execute(
        "SELECT item_name, judge_type, item_order, spec_display FROM specs "
        "WHERE material_no=? ORDER BY item_order", (mn,)).fetchall()
    for r in rows:
        print(f'  {r["item_name"]:3} order={r["item_order"]:3} type={r["judge_type"]:14} {r["spec_display"]!r}')

conn.close()
