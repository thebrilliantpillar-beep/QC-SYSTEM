"""하우징 3종 자재 보정 마이그레이션 (2차분).

migrate_housing.py가 F항목(고저항값, CT SIDE/ROD SIDE 두 채널)을 judge_type='numeric_pair'
하나로 합쳐놨었는데, 사용자가 이걸 2개의 독립적인 spec 항목(CT SIDE 전용 / ROD SIDE 전용)
으로 분리하기로 결정했다(PROGRESS.md 2026-09-25 항목 참고).

- F 항목: numeric_pair -> numeric, spec_display에 "CT SIDE" 명시 (item_name은 F 그대로 유지)
- 새 항목 R(ROD SIDE): F 바로 뒤(item_order+1)에 삽입, 원본 규격(lower/upper/inspect_method)
  그대로 복제. 뒤 항목들의 item_order는 +1씩 밀림(migrate_housing.py가 VD쇼트 삽입할 때
  쓴 것과 같은 패턴).
"""
import sys, io, sqlite3
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

# db.DB_PATH = DATA_DIR(영구디스크)/iqc.db — 상대경로 'iqc.db'는 실행 위치(예: /app)의
# 빈 파일을 봐서 "no such table" 에러가 나는 실제 있었던 사고. 항상 이 경로를 써야 한다.
conn = sqlite3.connect(db.DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

for mn in HOUSING:
    print(f'\n=== {mn} ===')

    frow = cur.execute(
        "SELECT id, item_order, judge_type, spec_display, lower_limit, upper_limit, inspect_method, aql "
        "FROM specs WHERE material_no=? AND item_name='F'", (mn,)).fetchone()
    if not frow:
        print('  F 항목 없음 — 스킵')
        continue
    if frow['judge_type'] != 'numeric_pair':
        print(f"  F: judge_type이 numeric_pair가 아님({frow['judge_type']!r}) — 이미 분리됐거나 다른 상태, 스킵")
        continue

    exists_r = cur.execute("SELECT id FROM specs WHERE material_no=? AND item_name='R'", (mn,)).fetchone()
    if exists_r:
        print('  R (ROD SIDE) already exists — skip insert')
        continue

    f_order = frow['item_order']
    lo, hi, method, aql = frow['lower_limit'], frow['upper_limit'], frow['inspect_method'], frow['aql']

    # F 자리(뒤에 오는 항목들)를 +1씩 밀어서 R이 들어갈 자리를 만든다
    cur.execute('UPDATE specs SET item_order = item_order + 1 WHERE material_no=? AND item_order > ?',
                (mn, f_order))

    # F 항목을 CT SIDE 전용으로 전환
    new_f_display = f'고저항 CT SIDE ({lo}~{hi}Ω)'
    cur.execute("UPDATE specs SET judge_type='numeric', spec_display=? WHERE id=?",
                (new_f_display, frow['id']))
    print(f'  F: numeric_pair -> numeric, spec_display -> {new_f_display!r}')

    # R 항목(ROD SIDE) 신설, F 바로 뒤(item_order = f_order+1)
    new_r_display = f'고저항 ROD SIDE ({lo}~{hi}Ω)'
    cur.execute("""INSERT INTO specs
        (material_no, item_name, spec_display, judge_type, lower_limit, upper_limit,
         inspect_method, aql, item_order)
        VALUES (?, 'R', ?, 'numeric', ?, ?, ?, ?, ?)""",
        (mn, new_r_display, lo, hi, method, aql, f_order + 1))
    print(f'  + inserted R (ROD SIDE) at order={f_order + 1}, spec_display={new_r_display!r}')

conn.commit()

print('\n\n=== 최종 상태 ===')
for mn in HOUSING:
    print(f'\n[{mn}]')
    for r in conn.execute('SELECT item_order, item_name, judge_type, spec_display FROM specs WHERE material_no=? ORDER BY item_order', (mn,)):
        print(f'  {r["item_order"]:2} {r["item_name"]:3} {r["judge_type"]:14} {r["spec_display"]!r}')

conn.close()
print('\nDONE.')
