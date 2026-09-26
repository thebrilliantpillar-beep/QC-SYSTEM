"""하우징 3종 자재 — 2차 기능검사(stage_group=2) 항목 표시순서 재배열.

실사용 피드백(2026-09-27): 태블릿 입력화면(full_inspect_housing.html)의 2차 탭
열 순서가 "CT극성 → CT저항값 → 고저항CT SIDE → 고저항ROD SIDE → 주회로저항 →
동작검사 → VD쇼트 → 외관검사(N 또는 M)"였는데, 사용자가 "외관, CT극성, 동작검사,
VD쇼트, CT저항값, 고저항값(CT+ROD), 주회로저항" 순서로 바꿔달라고 요청했다.

_housing_columns_by_stage()(app.py)가 specs.item_order 순서를 그대로 stage_group
버킷에 담아 화면·PDF(report_builder.build_housing_report) 양쪽에 다 반영하므로,
item_order 자체를 여기서 바로잡는 게 근본 수정이다(화면단 재배열 X).

"외관검사" 항목은 CLAUDE.md 26-3에 기록된 대로 자재마다 item_name이 다를 수 있다
(로컬 테스트DB는 N, 프로덕션은 자재별로 M 또는 N) — 그래서 item_name으로 하드코딩
하지 않고 spec_display에 "외관"이 포함된 항목을 찾아서 식별한다(내용 기반 식별,
26-3의 교훈: 데이터 내용은 환경마다 다를 수 있다).

item_order는 stage1/stage3 항목과 뒤섞인 전역 시퀀스라, stage2 항목이 차지하고
있던 item_order "슬롯"(정수값)만 재사용해서 그 안에서 순서만 바꾼다 — stage1/3
항목의 item_order는 건드리지 않는다(migrate_housing_split_resistance.py와 같은
"슬롯 재사용" 패턴).

CLAUDE.md 26-2 원칙에 따라 db.DB_PATH(절대경로)를 그대로 쓴다 — 상대경로
'iqc.db' 금지.
"""
import sys, io, sqlite3
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

# 이 순서 그대로 앞쪽부터 배치한다 — 외관검사 항목은 아래서 spec_display로 찾아서 맨 앞에 끼운다.
CORE_ORDER = ['D', 'H', 'V', 'E', 'F', 'R', 'G']

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
        for r in visual_rows:
            print(f'      후보: {r["item_name"]} {r["spec_display"]!r}')
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
    ordered_rows += leftover  # 목록에 안 든 나머지(예: 로컬 테스트DB의 X-선검사 M)는 뒤에 그대로 붙임

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
