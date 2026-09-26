"""하우징 3종 자재 — 2차 기능검사(stage_group=2) 항목 순서 재교정.

migrate_housing_reorder_stage2.py(2026-09-27 1차)가 사용자의 말로 전달된 순서
("외관,CT극성,동작검사,VD쇼트,CT저항값,고저항값,주회로저항")로 바꿨는데, 사용자가
원본 성적서 엑셀(15KV 하우징 참고파일)을 직접 첨부해서 재확인해보니 실제 원본
열 순서(N→O→P→Q→R:S→T→U)는 "외관,CT극성,CT저항값,VD쇼트,고저항값(CT+ROD),
동작검사,주회로저항"이었다 — 1차 재배열이 잘못된 순서를 반영했으므로 이 스크립트로
바로잡는다. CLAUDE.md 26-2/26-3 원칙(db.DB_PATH, spec_display로 외관검사 식별) 그대로 따름.
"""
import sys, io, sqlite3
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

# 원본 엑셀 실측 순서: 외관(맨앞 고정) -> CT극성 -> CT저항값 -> VD쇼트 -> 고저항(CT/ROD) -> 동작검사 -> 주회로저항
CORE_ORDER = ['D', 'E', 'V', 'F', 'R', 'H', 'G']

conn = sqlite3.connect(db.DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

for mn in HOUSING:
    print(f'\n=== {mn} ===')
    stage2 = cur.execute(
        "SELECT id, item_name, item_order, spec_display FROM specs "
        "WHERE material_no=? AND stage_group=2 ORDER BY item_order", (mn,)).fetchall()
    if not stage2:
        print('  stage_group=2 항목 없음 — 스킵')
        continue

    visual_rows = [r for r in stage2 if '외관' in (r['spec_display'] or '')]
    if len(visual_rows) != 1:
        print(f'  ! 외관검사 항목을 정확히 하나 못 찾음(매칭 {len(visual_rows)}건) — 이 자재는 건드리지 않음')
        continue
    visual_row = visual_rows[0]

    by_name = {r['item_name']: r for r in stage2}
    ordered_rows = [visual_row]
    for name in CORE_ORDER:
        r = by_name.get(name)
        if r is None:
            print(f'  ! item_name={name!r} 없음 — 스킵 대상에서 제외')
            continue
        if r['id'] == visual_row['id']:
            continue
        ordered_rows.append(r)
    used_ids = {r['id'] for r in ordered_rows}
    leftover = [r for r in stage2 if r['id'] not in used_ids]
    ordered_rows += leftover

    current_names = [r['item_name'] for r in stage2]
    target_names = [r['item_name'] for r in ordered_rows]
    if current_names == target_names:
        print(f'  이미 목표 순서와 동일({current_names}) — 스킵')
        continue

    slots = sorted(r['item_order'] for r in stage2)
    for slot, row in zip(slots, ordered_rows):
        if row['item_order'] != slot:
            cur.execute("UPDATE specs SET item_order=? WHERE id=?", (slot, row['id']))
    print(f'  {current_names} -> {target_names}')

conn.commit()

print('\n\n=== 최종 상태(전체 항목) ===')
for mn in HOUSING:
    print(f'\n[{mn}]')
    for r in conn.execute('SELECT item_order, item_name, stage_group, spec_display '
                           'FROM specs WHERE material_no=? ORDER BY item_order', (mn,)):
        print(f'  {r["item_order"]:2} {r["item_name"]:3} stage={r["stage_group"]} {r["spec_display"]!r}')

conn.close()
print('\nDONE.')
