"""하우징 3종 자재 — 중복 등록된 '고저항 ROD SIDE' 단독 항목(R)을 제거 (v2).

migrate_housing_remove_duplicate_rod.py(1차)는 로컬DB 기준(F.judge_type=
'numeric_pair')으로 짰는데, 실제 프로덕션은 F.judge_type='numeric'이라(CT SIDE만
재는 단일 숫자항목) F를 못 찾고 아무 것도 안 하고 끝났다(CLAUDE.md 26-3 — 로컬과
프로덕션 데이터가 다시 달랐던 사례). 실측(check_housing_resistance_specs.py 결과)
으로 확인한 프로덕션 실제 구조에 맞춰 다시 짠다:

  프로덕션 지금 상태 — F: judge_type='numeric', "고저항 CT SIDE (범위)"
                      R: judge_type='numeric', "고저항 ROD SIDE (범위)"
  원본 성적서 목표 — 고저항값 한 칸(R11:S11 병합)에 CT SIDE/ROD SIDE 두 값을 나란히
                     (report_builder의 numeric_pair 타입이 정확히 이 구조를 렌더링함)

그래서 이번엔 R을 지우기 전에 **F 자체를 numeric_pair로 전환**하고, 기존에 F/R에
각각 들어있던 측정값을 "ct/rod" 페어 문자열로 합친 다음 R을 지운다. F의 기존 값이
이미 "/"가 들어간 페어 형식이면(이미 한 번 전환된 경우) 그대로 두고 ROD쪽만 채운다.
값이 있는데 서로 다른 값이 이미 양쪽에 들어있어 충돌하면 자동으로 덮어쓰지 않고
보고만 하고 그 자재는 건드리지 않는다(1차 스크립트와 동일한 안전장치).

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
        "SELECT id, item_name, judge_type FROM specs WHERE material_no=? "
        "AND judge_type IN ('numeric','numeric_pair') AND spec_display LIKE '%고저항%CT%'",
        (mn,)).fetchone()
    r_row = cur.execute(
        "SELECT id, item_name FROM specs WHERE material_no=? AND judge_type='numeric' "
        "AND spec_display LIKE '%고저항%ROD%'", (mn,)).fetchone()
    if r_row is None:
        print('  중복 R 항목 없음 — 스킵(이미 정리됐거나 애초에 없음)')
        continue
    if f_row is None:
        print('  ! F(고저항 CT SIDE) 항목을 못 찾음 — 안전하게 건드리지 않음')
        continue
    f_key, r_key = f_row['item_name'], r_row['item_name']
    print(f'  F={f_key!r}(judge_type={f_row["judge_type"]!r}), R={r_key!r} (삭제 대상)')

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
            f_val = (vals.get(f_key) or '').strip()
            ct_part, sep, rod_part = f_val.partition('/')
            ct_part, rod_part = ct_part.strip(), rod_part.strip()
            if r_val and rod_part and rod_part != r_val:
                print(f'  ! 충돌: fi={fid} unit={u["unit_no"]} F의 ROD쪽={rod_part!r} vs R={r_val!r} '
                      f'— 자동 병합 안 함, 이 자재는 이번엔 R을 안 지움')
                conflict = True
                continue
            new_rod = rod_part or r_val
            new_f_val = f'{ct_part}/{new_rod}' if (ct_part or new_rod) else ''
            if new_f_val and new_f_val != f_val:
                vals[f_key] = new_f_val
                cur.execute("UPDATE full_inspection_units SET values_json=? WHERE id=?",
                            (json.dumps(vals, ensure_ascii=False), u['id']))
                merged_count += 1
                print(f'  병합: fi={fid} unit={u["unit_no"]} F {f_val!r} -> {new_f_val!r} (R값 흡수)')

    if conflict:
        print(f'  ! 충돌이 있어 {mn}의 R 항목({r_key})은 이번엔 삭제하지 않음 — 위 충돌 로그 확인 후 수동 처리')
        continue

    if f_row['judge_type'] != 'numeric_pair':
        cur.execute("UPDATE specs SET judge_type='numeric_pair' WHERE id=?", (f_row['id'],))
        print(f'  F({f_key}) judge_type을 numeric -> numeric_pair로 전환(CT/ROD 쌍 입력 항목화)')

    cur.execute("DELETE FROM specs WHERE id=?", (r_row['id'],))
    print(f'  R 항목({r_key}) 삭제 완료 (병합된 유닛 {merged_count}건)')

conn.commit()
conn.close()
print('\nDONE.')
