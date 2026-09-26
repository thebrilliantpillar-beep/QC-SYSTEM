"""하우징 3종 자재 — 중복 등록된 '고저항 ROD SIDE' 단독 항목(R)을 제거.

사용자 확인(2026-09-27): 원본 성적서 엑셀은 "고저항값" 칸 하나(R11:S11 병합)에
CT SIDE/ROD SIDE 두 값을 나란히 적는 구조 — 이건 우리 시스템의 F 항목(judge_type=
'numeric_pair', "CT/ROD" 쌍을 한 칸에 같이 입력)과 정확히 일치한다. R 항목(judge_type=
'numeric', ROD SIDE만 별도)은 원본에 없는 항목으로, 이 프로젝트(공식 성적서 시스템)
이전에 비공식적으로 기록을 남기려고 CT/ROD를 억지로 나눠서 넣었던 흔적이라고
사용자가 직접 확인해줬다 — "원본을 따라가면 된다"는 지시에 따라 R을 제거한다.

**데이터 유실 방지**: R 항목 자체를 그냥 지우면 이미 R에 입력된 ROD 측정값이 통째로
사라진다. 그래서 삭제 전에 모든 하우징 성적서(full_inspection_units)를 훑어서, R에
값이 있는데 F의 ROD쪽이 비어있으면 F 안으로 병합(merge)한 다음에 R을 지운다.
F가 이미 ROD쪽 값을 갖고 있는데 R값과 다르면 자동으로 덮어쓰지 않고 충돌로 보고만
하고, 그 자재는 이번 실행에서 R 삭제를 건너뛴다(사람이 직접 확인 후 재실행).

pair 값 저장 포맷은 "ct/rod" 문자열이다(app.py judge_row_result()의 num_pair 분기,
STAGE 화면 JS의 c-pair data-side="ct"/"rod" 참고).

CLAUDE.md 26-2 원칙: db.DB_PATH(절대경로) 사용, 상대경로 'iqc.db' 금지.
"""
import sys, io, sqlite3, json
import database as db

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HOUSING = ['602013P015', '604013P000', '608013P011']

conn = sqlite3.connect(db.DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

for mn in HOUSING:
    print(f'\n=== {mn} ===')
    f_row = cur.execute(
        "SELECT id, item_name FROM specs WHERE material_no=? AND judge_type='numeric_pair' "
        "AND spec_display LIKE '%고저항%'", (mn,)).fetchone()
    r_row = cur.execute(
        "SELECT id, item_name FROM specs WHERE material_no=? AND judge_type='numeric' "
        "AND spec_display LIKE '%고저항%ROD%'", (mn,)).fetchone()
    if r_row is None:
        print('  중복 R 항목 없음 — 스킵(이미 정리됐거나 애초에 없음)')
        continue
    if f_row is None:
        print('  ! F(고저항 쌍) 항목을 못 찾음 — 안전하게 건드리지 않음')
        continue
    f_key, r_key = f_row['item_name'], r_row['item_name']
    print(f'  F={f_key!r}, R={r_key!r} (삭제 대상)')

    fi_ids = [row['id'] for row in cur.execute(
        "SELECT fi.id FROM full_inspections fi JOIN inspections i ON i.id = fi.inspection_id "
        "WHERE i.material_no = ?", (mn,)).fetchall()]

    conflict = False
    merged_count = 0
    for fid in fi_ids:
        units = cur.execute(
            "SELECT id, unit_no, values_json FROM full_inspection_units WHERE full_inspection_id=?",
            (fid,)).fetchall()
        for u in units:
            vals = json.loads(u['values_json'] or '{}')
            r_val = (vals.get(r_key) or '').strip()
            if not r_val:
                continue
            f_val = (vals.get(f_key) or '').strip()
            ct_part, _, rod_part = f_val.partition('/')
            ct_part, rod_part = ct_part.strip(), rod_part.strip()
            if rod_part and rod_part != r_val:
                print(f'  ! 충돌: fi={fid} unit={u["unit_no"]} F의 ROD쪽={rod_part!r} vs R={r_val!r} '
                      f'— 자동 병합 안 함, 이 자재는 이번엔 R을 안 지움')
                conflict = True
                continue
            new_f_val = f'{ct_part}/{r_val}'
            if new_f_val != f_val:
                vals[f_key] = new_f_val
                cur.execute("UPDATE full_inspection_units SET values_json=? WHERE id=?",
                            (json.dumps(vals, ensure_ascii=False), u['id']))
                merged_count += 1
                print(f'  병합: fi={fid} unit={u["unit_no"]} F {f_val!r} -> {new_f_val!r} (R값 흡수)')

    if conflict:
        print(f'  ! 충돌이 있어 {mn}의 R 항목({r_key})은 이번엔 삭제하지 않음 — 위 충돌 로그 확인 후 수동 처리')
        continue

    cur.execute("DELETE FROM specs WHERE id=?", (r_row['id'],))
    print(f'  R 항목({r_key}) 삭제 완료 (병합된 유닛 {merged_count}건)')

conn.commit()
conn.close()
print('\nDONE.')
