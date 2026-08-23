"""하우징 3종 자재 보정 마이그레이션.

- *A~*H 별표 접두어 제거 (item_label이 AQL 0.65에서 자동으로 붙임 — 저장은 순수 이름만)
- VD쇼트 항목 신설 (H 뒤, item_order 재배치)
- F 고저항 judge_type: numeric -> numeric_pair (CT SIDE / ROD SIDE 두 값)
- full_inspect_config 활성화
"""
import sys, io, sqlite3, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

conn = sqlite3.connect('iqc.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

for mn in HOUSING:
    print(f'\n=== {mn} ===')

    # 1) 별표 제거
    rows = cur.execute('SELECT id, item_name FROM specs WHERE material_no=?', (mn,)).fetchall()
    for r in rows:
        clean = r['item_name'].lstrip('*').strip()
        if clean != r['item_name']:
            cur.execute('UPDATE specs SET item_name=? WHERE id=?', (clean, r['id']))
            print(f'  strip *: {r["item_name"]!r} -> {clean!r}')

    # 2) VD쇼트 신설 (item_name='V', H 뒤에 삽입)
    exists_v = cur.execute("SELECT id FROM specs WHERE material_no=? AND item_name='V'", (mn,)).fetchone()
    if exists_v:
        print('  V (VD쇼트) already exists — skip insert')
    else:
        # I~N을 +1씩 밀기 (item_order 8 이상)
        cur.execute('UPDATE specs SET item_order = item_order + 1 WHERE material_no=? AND item_order >= 8', (mn,))
        cur.execute("""INSERT INTO specs
            (material_no, item_name, spec_display, judge_type, lower_limit, upper_limit,
             inspect_method, aql, item_order)
            VALUES (?, 'V', 'VD쇼트 (O/X)', 'ok_ng', NULL, NULL, 'VD 쇼트 시험기', '0.65', 8)""",
            (mn,))
        print('  + inserted V (VD쇼트) at order=8')

    # 3) F 항목 numeric_pair 로 전환
    frow = cur.execute("SELECT id, judge_type, spec_display FROM specs WHERE material_no=? AND item_name='F'", (mn,)).fetchone()
    if frow:
        if frow['judge_type'] != 'numeric_pair':
            cur.execute("UPDATE specs SET judge_type='numeric_pair' WHERE id=?", (frow['id'],))
            print(f'  F: numeric -> numeric_pair')
        else:
            print(f'  F: already numeric_pair')

    # 4) full_inspect_config 활성화
    cfg = {"enabled": True, "template": "housing",
           "note": "전수검사 기록지 — 컬럼: 제품번호(3칸) / 동작·외관 / VD쇼트 / CT극성 / 고저항(2ch) / CT저항값 / 주회로저항 / 충격파 내전압 / 부분방전 / 내전압"}
    cur.execute('UPDATE materials SET full_inspect_config=? WHERE material_no=?',
                (json.dumps(cfg, ensure_ascii=False), mn))
    print('  full_inspect_config: enabled (template=housing)')

conn.commit()

print('\n\n=== 최종 상태 ===')
for mn in HOUSING:
    print(f'\n[{mn}]')
    for r in conn.execute('SELECT item_order, item_name, judge_type, spec_display FROM specs WHERE material_no=? ORDER BY item_order', (mn,)):
        print(f'  {r["item_order"]:2} {r["item_name"]:3} {r["judge_type"]:14} {r["spec_display"]!r}')
    fi = conn.execute('SELECT full_inspect_config FROM materials WHERE material_no=?', (mn,)).fetchone()
    print(f'  fi_config: {fi["full_inspect_config"]}')

conn.close()
print('\nDONE.')
